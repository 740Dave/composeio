/*
 * fingerprint_device_canonical.cpp — the device-logic (C71/C73) C++ twin.
 * Mirror of device.py (main @ 0ba4e8b).  PURE C++, host-testable.
 *
 * ⛔ BEHAVIOURAL PARITY, NOT LITERAL: the oracle wraps HAL calls in
 * try/except FingerprintHALError; here each fallible HAL op returns a bool +
 * fills a FingerprintHALFailure out-param (Arduino-ESP32 disables C++
 * exceptions).  The control flow — which branch, in what order, and the
 * observable publishes/stamp/counter effects — is identical.
 */
#include "fingerprint_device.h"

#include <cstdint>

/* [DD-FPBUS-DELETE-FAILURE-CLASS-MAP] (proposed): split by whether a fresh
 * push can clear the store.  Recoverable -> push_needed; unrecoverable ->
 * inoperable.  The task pins unknown_code -> push_needed.  UNSUPPORTED and
 * SENSOR_REFUSAL are unrecoverable (a push cannot add a capability the sensor
 * lacks, nor argue a refusing part into cooperating). */
static bool is_recoverable_delete_axis(FailureAxis axis) {
    switch (axis) {
        case FailureAxis::TRANSPORT:
        case FailureAxis::CAPACITY:
        case FailureAxis::PROTOCOL_DESYNC:
        case FailureAxis::UNKNOWN_CODE:
            return true;
        default:
            return false;
    }
}

double heartbeat_interval_for(HeartbeatState s) {
    switch (s) {
        case HeartbeatState::READY:       return 60.0;
        case HeartbeatState::PUSH_NEEDED: return 15.0;
        case HeartbeatState::INOPERABLE:  return 15.0;
    }
    return 15.0;
}

bool validate_publishable(const TemplatePush& push, std::string& reason,
                          int slot_count, int template_size_bytes) {
    if (push.template_set_uuid.empty()) {   /* None or "" */
        reason = "set has no UUID";
        return false;
    }
    if (slot_count >= 0 && static_cast<int>(push.entries.size()) > slot_count) {
        /* [CXC-FPBUS-REQ-SET-SIZE-LIMIT] the TARGET DEVICE's announced
         * slot_count, NEVER a hardcoded constant. */
        reason = "set has " + std::to_string(push.entries.size()) +
                 " templates (> " + std::to_string(slot_count) + ")";
        return false;
    }
    if (template_size_bytes >= 0) {
        /* [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] */
        for (const auto& entry : push.entries) {
            if (static_cast<int>(entry.tmpl.size()) != template_size_bytes) {
                reason = "template for " + entry.person + "#" +
                         std::to_string(entry.ordinal) + " is " +
                         std::to_string(entry.tmpl.size()) +
                         " bytes (device requires " +
                         std::to_string(template_size_bytes) + ")";
                return false;
            }
        }
    }
    reason.clear();
    return true;
}

/* ------------------------------------------------------------------------ */

FingerprintDevice::FingerprintDevice(
    FingerprintHAL* hal, FingerprintBusSink* bus, std::string device_id,
    std::string gated_capability, DevicePersistence* persistence,
    FingerprintBusSink* uplink, std::optional<int> match_threshold,
    std::function<double()> clock, std::function<std::string()> event_id_factory,
    TemplateResolver template_resolver)
    : hal_(hal), bus_(bus), uplink_(uplink),
      device_id_(std::move(device_id)),
      gated_capability_(std::move(gated_capability)),
      match_threshold_(match_threshold),
      nvs_(persistence),
      clock_(std::move(clock)),
      event_id_factory_(std::move(event_id_factory)),
      template_resolver_(std::move(template_resolver)) {
    /* Defaults assigned in the body (not the init list) with this-capturing
     * lambdas — the plainest form for every toolchain, incl. the esp32 gcc
     * 8.4 that rejects a shared_ptr-capturing lambda -> std::function here. */
    if (!clock_) {
        clock_ = [this]() { (void)this; return 0.0; };
    }
    if (!event_id_factory_) {
        event_id_factory_ = [this]() {
            return std::string("evt-") + std::to_string(++event_seq_);
        };
    }
}

std::optional<std::string> FingerprintDevice::read_current_uuid() {
    auto stamp = hal_->read_roster_stamp();
    if (!stamp.has_value()) return std::nullopt;
    return *stamp;   /* bytes == utf-8 identity here */
}

/* ===================================================================
 * [CXC-FPBUS-PUSH-SEQUENCE] The push receipt
 * =================================================================== */

std::string FingerprintDevice::receive_push(const TemplatePush& push) {
    /* CHANGE-1(a) FAIL-LOUD INVARIANT: never load a roster (and so never look
     * armed/loaded) while bound to the mock with a real sensor unconfirmed. A
     * push landing on the mock would DownChar into a store the match path never
     * reads — the exact silent-impersonation defect. Announce inoperable, load
     * nothing. */
    if (!hal_confirmed_live_) {
        publish("fingerprint_inoperable",
                { { "inop_code", FpValue::str("sensor_unconfirmed") },
                  { "human_readable_inop_string",
                    FpValue::str("real sensor present but HAL not confirmed live; "
                                 "push refused (would load onto the mock)") } });
        return "inoperable";
    }

    const std::string& pushed_uuid = push.template_set_uuid;

    /* [CXC-FPBUS-REQ-UUID-COMPARE] Receipt BEGINS with a UUID read, before any
     * delete or write. */
    std::optional<std::string> current = read_current_uuid();

    if (current.has_value() && *current == pushed_uuid) {
        /* [CXC-FPBUS-REQ-UUID-MATCH-BRANCH] no work, one ack.  Count NOT reset. */
        publish("template_set_loaded",
                { { "template_set_uuid", FpValue::str(pushed_uuid) } });
        return "loaded";
    }

    /* [CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH] delete all, load all, stamp last. */
    FingerprintHALFailure err;
    if (!hal_->empty(err)) {
        /* A delete failure even as the opening wipe escalates: the store cannot
         * be cleared, which is exactly the escalation's concern. */
        escalate_delete_failure(err);
        return "delete_failed";
    }

    std::map<Slot, BridgeEntry> new_bridge;
    auto role_table = push.role_capability_table;
    static const std::string kRefPrefix = "fingerprint://";
    for (const auto& entry : push.entries) {
        /* Resolve a `fingerprint://` reference to bytes BEFORE the store —
         * [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] then runs on the FETCHED bytes
         * via store_template's own size check, mirroring device.py's
         * resolve-then-guard-then-store call order exactly. A real 1536-byte
         * R503 template beginning with these exact 14 ASCII bytes is
         * astronomically unlikely — acceptable discriminator for this
         * toy-experiential system (no bytes/str type duality in C++ the way
         * Python has, so there is no other way to tell a reference from raw
         * bytes short of a wire-level flag nobody has proposed). */
        TemplateBytes bytes = entry.tmpl;
        if (entry.tmpl.rfind(kRefPrefix, 0) == 0) {
            if (template_resolver_) {
                if (!template_resolver_(entry.tmpl, bytes, err))
                    return handle_load_failure(pushed_uuid, err);
            } else {
                err.axis = FailureAxis::TRANSPORT;
                err.sensor_code = std::nullopt;
                err.detail = "no template resolver configured for reference " + entry.tmpl;
                return handle_load_failure(pushed_uuid, err);
            }
        }
        Slot slot;
        if (!hal_->store_template(bytes, slot, err))
            return handle_load_failure(pushed_uuid, err);
        new_bridge[slot] = BridgeEntry{ entry.person, entry.ordinal,
                                        entry.character, entry.rbac };
    }

    /* All templates saved successfully -> commit. */
    nvs_->bridge = new_bridge;
    nvs_->role_capability_table = role_table;
    /* [CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH] stamp STRICTLY LAST. */
    hal_->write_roster_stamp(pushed_uuid);
    /* counter-semantics: a GENUINE uuid change zeroes the count. */
    nvs_->count_authentication_attempts = 0;
    escalation_state_ = std::nullopt;
    publish("template_set_loaded",
            { { "template_set_uuid", FpValue::str(pushed_uuid) } });
    return "loaded";
}

std::string FingerprintDevice::handle_load_failure(const std::string& pushed_uuid,
                                                   const FingerprintHALFailure& exc) {
    /* [CXC-FPBUS-REQ-LOAD-FAILURE] report, HOLD the stamp, empty the device.
     * [CXC-FPBUS-FAILURE-ON-THE-WIRE] the closed six-axis taxonomy crosses the
     * wire verbatim: {axis, sensor_code, detail}. sensor_code is ABSENT (not
     * null) when the axis carries none, same idiom as auth_event's
     * player/character. */
    FpData failed_payload{
        { "template_set_uuid", FpValue::str(pushed_uuid) },
        { "axis", FpValue::str(to_string(exc.axis)) },
        { "detail", FpValue::str(exc.detail) },
    };
    if (exc.sensor_code.has_value()) {
        failed_payload["sensor_code"] = FpValue::num(*exc.sensor_code);
    }
    publish("template_set_failed", failed_payload);
    FingerprintHALFailure del_err;
    if (!hal_->empty(del_err)) {
        /* [CXC-FPBUS-REQ-DELETE-FAILURE-ESCALATION] */
        escalate_delete_failure(del_err);
        return "load_failed_delete_failed";
    }
    /* Cleanup succeeded: templates gone, stamp held -> unconfigured. */
    escalation_state_ = std::nullopt;
    return "load_failed";
}

void FingerprintDevice::escalate_delete_failure(const FingerprintHALFailure& exc) {
    if (is_recoverable_delete_axis(exc.axis)) {
        escalation_state_ = HeartbeatState::PUSH_NEEDED;
        publish("fingerprint_push_needed", {});
    } else {
        escalation_state_ = HeartbeatState::INOPERABLE;
        publish("fingerprint_inoperable",
                { { "inop_code", FpValue::str(to_string(exc.axis)) },
                  { "human_readable_inop_string", FpValue::str(exc.detail) } });
    }
}

/* ===================================================================
 * [CXC-FPBUS-MSG-MATCH-HAS-NO-COMMAND] The commandless match
 * =================================================================== */

std::optional<AuthOutcome> FingerprintDevice::poll_match() {
    return resolve_match(hal_->match());
}

std::optional<AuthOutcome> FingerprintDevice::resolve_match(const MatchResult& result) {
    if (result.outcome == MatchOutcome::NO_FINGER) {
        return std::nullopt;   /* an empty doorway is not an attempt */
    }

    if (result.outcome == MatchOutcome::MATCHED && match_threshold_.has_value()) {
        if (result.score.has_value() && *result.score < *match_threshold_) {
            return std::nullopt;   /* below the device-internal floor */
        }
    }

    std::optional<std::string> player;
    std::optional<std::string> character;
    AuthOutcome outcome;
    if (result.outcome == MatchOutcome::NO_MATCH) {
        outcome = AuthOutcome::UNRECOGNIZED;
    } else {  /* MATCHED */
        auto it = result.slot.has_value() ? nvs_->bridge.find(*result.slot)
                                          : nvs_->bridge.end();
        if (it == nvs_->bridge.end()) {
            outcome = AuthOutcome::UNRECOGNIZED;   /* matched slot, no binding */
        } else {
            const BridgeEntry& entry = it->second;
            auto caps_it = nvs_->role_capability_table.find(entry.role);
            bool granted = false;
            if (caps_it != nvs_->role_capability_table.end()) {
                for (const auto& c : caps_it->second)
                    if (c == gated_capability_) { granted = true; break; }
            }
            outcome = granted ? AuthOutcome::GRANTED : AuthOutcome::DENIED;
            player = entry.person;
            character = entry.character;
        }
    }

    /* This IS an authentication attempt against the loaded roster. */
    nvs_->count_authentication_attempts += 1;

    publish_auth_event(outcome, player, character);
    drive_ring(outcome);
    return outcome;
}

void FingerprintDevice::publish_auth_event(AuthOutcome outcome,
                                           const std::optional<std::string>& player,
                                           const std::optional<std::string>& character) {
    /* [CXC-FPBUS-MSG-AUTH-EVENT] one event, three-valued, all published.
     * player/character ABSENT (not null) for unrecognized.  No score.
     * gated_capability is DELIBERATELY ABSENT (wire-economy, 2026-08-18): a
     * device gates exactly ONE capability, a per-device constant already
     * retained on the device-announce, joinable by device_id. */
    FpData payload;
    payload["event_id"] = FpValue::str(event_id_factory_());
    payload["device_id"] = FpValue::str(device_id_);
    payload["outcome"] = FpValue::str(to_string(outcome));
    payload["timestamp"] = FpValue::num(clock_());
    if (outcome == AuthOutcome::GRANTED || outcome == AuthOutcome::DENIED) {
        payload["player"] = FpValue::str(player.value_or(""));
        payload["character"] = FpValue::str(character.value_or(""));
    }
    const std::string ns = device_id_ + "/auth_event";
    /* LOCAL bus. */
    bus_->publish_event(ns, "auth_event", payload, device_id_);
    /* Uplinked to ComposeBus — identical shape ([CXC-FPBUS-MSG-AUTH-EVENT]
     * scope-membrane).  A COPY of the same payload (same event_id, same
     * timestamp) goes to the second sink so a test can assert both scopes see
     * the byte-identical event. */
    if (uplink_ != nullptr) {
        uplink_->publish_event(ns, "auth_event", FpData(payload), device_id_);
    }
}

void FingerprintDevice::drive_ring(AuthOutcome outcome) {
    /* [CXC-FPID-RING-HIDES-DENIAL] granted -> green (ACCEPT); denied AND
     * unrecognized -> the SAME red, indistinguishable. */
    FeedbackState state = (outcome == AuthOutcome::GRANTED)
                              ? FeedbackState::ACCEPT
                              : FeedbackState::REJECT_SILENT;
    FingerprintHALFailure err;
    /* A sensor with no feedback channel cannot show the ring; not fatal. */
    (void)hal_->command_feedback(state, FpProgress{}, err);
}

/* ===================================================================
 * [CXC-FPBUS-REQ-*-HEARTBEAT] The four-state heartbeat machine
 * =================================================================== */

HeartbeatState FingerprintDevice::current_heartbeat_state() {
    if (!hardware_present_) return HeartbeatState::INOPERABLE;
    /* CHANGE-1(a) FAIL-LOUD: mock bound while a real sensor is present but
     * unconfirmed -> INOPERABLE, never READY. The mock's stamp+occupancy would
     * otherwise report READY and impersonate an armed device on the wire. */
    if (!hal_confirmed_live_) return HeartbeatState::INOPERABLE;
    if (escalation_state_.has_value()) return *escalation_state_;
    auto stamp = hal_->read_roster_stamp();
    auto occupancy = hal_->read_occupancy();
    if (stamp.has_value() && !occupancy.empty()) return HeartbeatState::READY;
    /* No stamp, or stamp held over an emptied store: unconfigured. */
    return HeartbeatState::PUSH_NEEDED;
}

HeartbeatState FingerprintDevice::emit_heartbeat() {
    HeartbeatState state = current_heartbeat_state();
    if (state == HeartbeatState::READY) {
        auto uuid = read_current_uuid();
        publish("fingerprint_ready",
                { { "template_set_uuid", FpValue::str(uuid.value_or("")) },
                  { "count_authentication_attempts",
                    FpValue::num(nvs_->count_authentication_attempts) } });
    } else if (state == HeartbeatState::PUSH_NEEDED) {
        publish("fingerprint_push_needed", {});
    } else {  /* INOPERABLE */
        /* CHANGE-1(a): distinguish "mock bound, real unconfirmed" from a truly
         * absent sensor so the wire says WHY it is inoperable. */
        const bool unconfirmed = hardware_present_ && !hal_confirmed_live_;
        publish("fingerprint_inoperable",
                { { "inop_code",
                    FpValue::str(unconfirmed ? "sensor_unconfirmed"
                                             : "sensor_unavailable") },
                  { "human_readable_inop_string",
                    FpValue::str(unconfirmed
                        ? "real sensor present but HAL not confirmed live"
                        : "fingerprint hardware not detected") } });
    }
    return state;
}

/* CHANGE-1(c) — mock->real live-rebind roster invalidation. */
void FingerprintDevice::invalidate_roster_request_push() {
    nvs_->bridge.clear();
    nvs_->role_capability_table.clear();
    nvs_->count_authentication_attempts = 0;
    escalation_state_ = HeartbeatState::PUSH_NEEDED;
    publish("fingerprint_push_needed", {});
}

double FingerprintDevice::heartbeat_interval() {
    return heartbeat_interval_for(current_heartbeat_state());
}

void FingerprintDevice::publish(const std::string& message, const FpData& data) {
    bus_->publish_event(device_id_ + "/" + message, message, data, device_id_);
}

/* --- virtual-time heartbeat driver -------------------------------------- */
std::vector<std::pair<double, HeartbeatState>>
run_heartbeat_window(FingerprintDevice& device, double duration_s, double start) {
    std::vector<std::pair<double, HeartbeatState>> emissions;
    double t = start;
    double end = start + duration_s;
    const double epsilon = 1e-9;
    while (t < end - epsilon) {
        HeartbeatState state = device.emit_heartbeat();
        emissions.push_back({ t, state });
        t += heartbeat_interval_for(state);
    }
    return emissions;
}
