/*
 * fingerprint_device.h — the device-logic ABOVE the HAL, C++ twin — C71/C73.
 * Mirror of python/composeio/peripherals/fingerprint/device.py (main @ 0ba4e8b).
 *
 * Transport-agnostic, HAL-agnostic logic the firmware is parity-tested
 * against.  It calls the FingerprintHAL interface and publishes to a bus
 * sink, knowing NOTHING about mock-vs-real or in-process-vs-wire.
 *
 * What the device owns:
 *   * the push RECEIPT ([CXC-FPBUS-PUSH-SEQUENCE], [CXC-FPBUS-REQ-UUID-COMPARE]):
 *     read the current UUID first; MATCH -> no-op + one ack; MISMATCH ->
 *     empty -> install-all-in-order -> write-stamp-LAST;
 *   * the slot -> {person, character, role} BRIDGE, built during install;
 *   * the role -> capability table + gate eval, run on-device;
 *   * the four-state heartbeat machine;
 *   * count_authentication_attempts, persisted (C71: NVS), reset ONLY on a
 *     genuine UUID change;
 *   * the failure obligations (load-failure + delete-failure escalation);
 *   * the three-valued auth_event + the RING that hides denial.
 *
 * PURE C++ — no Arduino, no ESP-IDF.  Host-testable in env:native_test.
 */
#ifndef FINGERPRINT_DEVICE_H
#define FINGERPRINT_DEVICE_H

#include <functional>
#include <map>
#include <string>
#include <vector>

#include "fingerprint_hal.h"

/* --- a small heterogeneous bus value ({str} | {num}) --------------------- */
struct FpValue {
    enum class K { Str, Num } kind = K::Str;
    std::string s;
    double n = 0.0;
    static FpValue str(std::string v) { return FpValue{ K::Str, std::move(v), 0.0 }; }
    static FpValue num(double v)      { return FpValue{ K::Num, std::string(), v }; }
    bool operator==(const FpValue& o) const {
        return kind == o.kind && (kind == K::Str ? s == o.s : n == o.n);
    }
};
using FpData = std::map<std::string, FpValue>;

/* --- the bus seam (mirrors ComposeIOBus.publish_event) ------------------- */
struct FingerprintBusSink {
    virtual ~FingerprintBusSink() = default;
    virtual void publish_event(const std::string& ns, const std::string& event_type,
                               const FpData& data, const std::string& source) = 0;
};

/* --- the three-valued outcome — [CXC-FPID-AUTH-OUTCOME], never a boolean -- */
enum class AuthOutcome { GRANTED, DENIED, UNRECOGNIZED };
inline const char* to_string(AuthOutcome o) {
    switch (o) {
        case AuthOutcome::GRANTED:      return "granted";
        case AuthOutcome::DENIED:       return "denied";
        case AuthOutcome::UNRECOGNIZED: return "unrecognized";
    }
    return "?";
}

/* --- the heartbeat state (silence is the 4th state, an ABSENCE) ---------- */
enum class HeartbeatState { READY, PUSH_NEEDED, INOPERABLE };
double heartbeat_interval_for(HeartbeatState s);   /* READY 60s, others 15s */

/* --- the bridge + persistence -------------------------------------------- */
struct BridgeEntry {
    std::string person;
    int ordinal = 0;
    std::string character;
    std::string role;
};

/* Device-private MCU NVS (C71).  A reboot is modelled as a NEW
 * FingerprintDevice built with the SAME persistence and the SAME
 * (sensor-persistent) HAL.  The bridge is persisted beside the count because
 * the ready-heartbeat authenticates on reboot from stamp+hardware alone
 * (C71 — the reference builds this; do not diverge). */
struct DevicePersistence {
    int count_authentication_attempts = 0;
    std::map<Slot, BridgeEntry> bridge;
    std::map<std::string, std::vector<std::string>> role_capability_table;
};

/* --- a push (service-validated before it reaches the device) ------------- */
struct PushEntry {
    std::string person;
    int ordinal = 0;
    std::string character;
    std::string rbac;          /* role */
    TemplateBytes tmpl;
};
struct TemplatePush {
    std::string template_set_uuid;
    std::vector<PushEntry> entries;
    std::map<std::string, std::vector<std::string>> role_capability_table;
};

/* --- [interop] THE INBOUND PUSH WIRE TOPIC -------------------------------
 * The fingerprint_storage service publishes a template-set push on
 * `<dev>/template_set` (event_type `template_set`).  C73 pins the push
 * PAYLOAD but does NOT name this topic (a real spec gap); the service is
 * authoritative for it.  When the live-bus RECEIVE path is wired (a later,
 * deliberate dispatch — this build is offline by mandate), it MUST subscribe /
 * consume the push on EXACTLY this string, decode it into a TemplatePush, and
 * hand it to FingerprintDevice::receive_push().
 *
 * ⛔ The device-EMITTED names — the heartbeats, auth_event, template_set_loaded,
 * template_set_failed — come from the reference device.py and are AUTHORITATIVE
 * and UNCHANGED; this helper is ONLY the inbound topic the firmware receives.
 *
 * [DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]. `RosterPublisher.
 * push_to_devices()` (the Python service) ALWAYS replaces each entry's raw
 * template bytes with a `fingerprint://<opaque-id>` STRING reference before
 * publishing — the JSON bus substrate cannot carry raw bytes (DP-CIO-18),
 * unconditionally across every transport (DP-CIO-6: no JSON-only carve-out).
 *
 * ⛔ CORRECTED (an earlier version of this note said resolution belongs in
 * THIS wire-decode layer, with `receive_push` left "untouched" — that is
 * WRONG and contradicted the authoritative Python reference twin even at the
 * time it was written). Resolution happens INSIDE `FingerprintDevice::
 * receive_push`, per-entry, immediately before `hal->store_template`, via
 * the injected `template_resolver_` seam below — mirroring `device.py`'s
 * `_resolve_template` + `receive_push` exactly ([CXC-FPBUS-REQ-TEMPLATE-
 * SIZE-GUARD] then runs on the FETCHED bytes via `store_template`'s own size
 * check, same as the Python twin). `PushEntry::tmpl` on the wire may already
 * be a `fingerprint://` reference by the time it reaches `receive_push` (the
 * decode layer just carries the string through unchanged); `receive_push`
 * resolves it before ever handing bytes to the HAL.
 *
 * A resolve failure is reported as a TRANSPORT axis failure and driven
 * through the SAME `handle_load_failure()` path a HAL store failure uses —
 * [CXC-FPBUS-REQ-LOAD-FAILURE]: report `template_set_failed`, hold the
 * stamp, empty the device. There is no separate failure path to add. */
inline std::string fingerprint_inbound_push_topic(const std::string& device_id) {
    return device_id + "/template_set";
}

/* --- service push-time validation — [CXC-FPBUS-REQ-SET-*] ---------------- */
/* Reference for roster_push.py: a push is publishable only if it carries a
 * UUID; when slot_count >= 0, it holds no more than slot_count templates
 * ([CXC-FPBUS-REQ-SET-SIZE-LIMIT] — the TARGET DEVICE's announced capacity,
 * NEVER a hardcoded constant); when template_size_bytes >= 0, every
 * template's byte length equals it exactly ([CXC-FPBUS-REQ-TEMPLATE-SIZE-
 * GUARD]).  A negative value (the default) means "unknown for this call" and
 * SKIPS that check rather than defaulting it.  reason is empty when ok. */
bool validate_publishable(const TemplatePush& push, std::string& reason,
                          int slot_count = -1, int template_size_bytes = -1);

/* --- the template-reference resolution seam ------------------------------
 * Mirrors device.py's injected `template_resolver` constructor param +
 * `_resolve_template`. A push entry's `template` field may arrive as a
 * `fingerprint://<opaque-id>` STRING reference (RosterPublisher always
 * converts once validation has passed — DP-CIO-6, no transport carve-out;
 * JSON cannot carry raw bytes, DP-CIO-18). C++'s `TemplateBytes` IS
 * `std::string` (no bytes/str type duality the way Python has), so the
 * discriminator is the `fingerprint://` prefix on the value itself — see
 * receive_push()'s entry loop.
 *
 * Returns true + fills `out` with the resolved bytes on success; false +
 * fills `err` (out-param error idiom, not an exception — mirrors every
 * other fallible HAL-adjacent op here) on failure. A resolver is injected by
 * the caller (real ESP32 HTTPClient GET on-chip, a fake in host tests); a
 * null resolver with a reference present is itself a TRANSPORT failure. */
using TemplateResolver = std::function<bool(const std::string& ref, TemplateBytes& out,
                                            FingerprintHALFailure& err)>;

/* --- the device firmware twin -------------------------------------------- */
class FingerprintDevice {
public:
    FingerprintDevice(FingerprintHAL* hal, FingerprintBusSink* bus,
                      std::string device_id, std::string gated_capability,
                      DevicePersistence* persistence,
                      FingerprintBusSink* uplink = nullptr,
                      std::optional<int> match_threshold = std::nullopt,
                      std::function<double()> clock = nullptr,
                      std::function<std::string()> event_id_factory = nullptr,
                      TemplateResolver template_resolver = nullptr);

    /* accessors used by tests */
    const std::string& device_id() const { return device_id_; }
    int count_authentication_attempts() const { return nvs_->count_authentication_attempts; }
    const std::map<Slot, BridgeEntry>& bridge() const { return nvs_->bridge; }
    void set_hardware_present(bool present) { hardware_present_ = present; }

    /* [CXC-FPBUS-PUSH-SEQUENCE] receive a template-set push; returns a status. */
    std::string receive_push(const TemplatePush& push);

    /* [CXC-FPBUS-MSG-MATCH-HAS-NO-COMMAND] poll the sensor once and resolve. */
    std::optional<AuthOutcome> poll_match();
    std::optional<AuthOutcome> resolve_match(const MatchResult& result);

    /* [CXC-FPBUS-REQ-*-HEARTBEAT] */
    HeartbeatState current_heartbeat_state();
    HeartbeatState emit_heartbeat();
    double heartbeat_interval();

private:
    std::optional<std::string> read_current_uuid();
    std::string handle_load_failure(const std::string& pushed_uuid,
                                    const FingerprintHALFailure& exc);
    void escalate_delete_failure(const FingerprintHALFailure& exc);
    void publish_auth_event(AuthOutcome outcome,
                            const std::optional<std::string>& player,
                            const std::optional<std::string>& character);
    void drive_ring(AuthOutcome outcome);
    void publish(const std::string& message, const FpData& data);

    FingerprintHAL* hal_;
    FingerprintBusSink* bus_;
    FingerprintBusSink* uplink_;
    std::string device_id_;
    std::string gated_capability_;
    std::optional<int> match_threshold_;
    DevicePersistence* nvs_;
    std::function<double()> clock_;
    std::function<std::string()> event_id_factory_;
    TemplateResolver template_resolver_;
    bool hardware_present_ = true;
    std::optional<HeartbeatState> escalation_state_;
    uint64_t event_seq_ = 0;   /* backs the default event_id_factory */
};

/* Virtual-time heartbeat driver (no real sleeping) — returns [(t, state)]. */
std::vector<std::pair<double, HeartbeatState>>
run_heartbeat_window(FingerprintDevice& device, double duration_s, double start = 0.0);

#endif /* FINGERPRINT_DEVICE_H */
