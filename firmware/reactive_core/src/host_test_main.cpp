/*
 * host_test_main.cpp — host unit test of the R1 reactive core + the
 * R2 output-actuator HAL.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-C1] (reactive re-eval, headless)
 * + [CXC-ER-C2] (expr: through the REAL canonical DSL)
 * + [CXC-ER-C4] (actuator mapping: binary canon -> GPIO state, numeric
 *   scale -> PWM duty, value -> stepper target + full-step sequencer).
 *
 * No hardware, no Arduino: proves publish -> re-evaluate -> derived
 * output on the host, exactly like the DSL's native_dsl harness.
 *
 *   pio run -e native_test && .pio/build/native_test/program
 *
 * PASS looks like: every CHECK line silent, final "ALL PASS (N checks)",
 * exit 0.
 */

#include "actuator_hal.h"
#include "reactive_core.h"
#include "reactive_apply.h"    /* R3 apply logic (cyd/src via -I) */
#include "widgets/checklist.h"     /* RAttr::ChecklistItems live apply parity */
#include "widgets/master_detail.h" /* RAttr::NavSelection  live apply parity  */
#include "xform2d.h"           /* R5 transform/round-clip geometry */
#include "audio_cover.h"       /* dual-throttle S6 DY-SV5F cover */
#include "controller_config.h" /* dual-throttle config-fetch device model */
#include "signal_conditioning.h" /* THE one value-conditioning pipeline */
#include "relay_actuator.h"    /* office relay-fan break-before-make actuator */
#include "restart.h"           /* remote config-reload handler */
#include "fingerprint_hal.h"       /* R503 fingerprint HAL contract (C72) */
#include "fingerprint_mock.h"      /* MockFingerprintHAL — the reference   */
#include "fingerprint_device.h"    /* device-logic twin (C71/C73)          */
#include "bus_transport_select.h"  /* offline-default transport selection  */

#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

/* The R503 wire-protocol + driver suite lives in its own translation unit
 * (src/hosttest/r503_protocol_tests.cpp).  It keeps its own counters and
 * returns the failure count; main() folds both into the totals below. */
int r503_protocol_tests(int* out_checks);

static int checks = 0;
static int failures = 0;

#define CHECK(cond, msg) do { \
    checks++; \
    if (!(cond)) { failures++; printf("FAIL %s (line %d)\n", msg, __LINE__); } \
} while (0)

/* Sink capture */
struct Event {
    std::string ns;
    DslValue value;
};
static std::vector<Event> events;

static void sink(void*, const char* ns, const DslValue& v) {
    events.push_back({ns, v});
}

static bool ev_is_number(const Event& e, const char* ns, double num) {
    return e.ns == ns && e.value.type == DslValue::Type::Number &&
           e.value.num == num;
}

static bool ev_is_bool(const Event& e, const char* ns, bool b) {
    return e.ns == ns && e.value.type == DslValue::Type::Bool &&
           e.value.b == b;
}

/* Actuator sink capture (R2) */
struct ActEvent {
    std::string ns;
    ActuatorCommand cmd;
};
static std::vector<ActEvent> acts;

static void act_sink(void*, const ActuatorSpec& spec,
                     const ActuatorCommand& cmd) {
    acts.push_back({spec.ns, cmd});
}

static bool act_is_binary(const ActEvent& e, const char* ns, bool on,
                          bool level) {
    return e.ns == ns && e.cmd.kind == ActuatorCommand::Kind::Binary &&
           e.cmd.on == on && e.cmd.level == level;
}

static bool act_is_duty(const ActEvent& e, const char* ns, int duty) {
    return e.ns == ns && e.cmd.kind == ActuatorCommand::Kind::PwmDuty &&
           e.cmd.duty == duty;
}

static bool act_is_step(const ActEvent& e, const char* ns, int position,
                        int direction, uint8_t coils) {
    return e.ns == ns &&
           e.cmd.kind == ActuatorCommand::Kind::StepperStep &&
           e.cmd.position == position && e.cmd.direction == direction &&
           e.cmd.coils == coils;
}

/* Relay drive capture (office fan): each drive is a (pin, level) pair. */
struct RelayDrive { int pin; bool level; };
static std::vector<RelayDrive> relays;
static void relay_sink(void*, int pin, bool level) {
    relays.push_back({pin, level});
}
/* count HIGH drives to a pin in the capture (energized, active-high) */
static int relay_highs(int pin) {
    int n = 0;
    for (const auto& d : relays) if (d.pin == pin && d.level) n++;
    return n;
}
/* the last level driven to a pin (-1 if never) */
static int relay_last(int pin) {
    int last = -1;
    for (const auto& d : relays) if (d.pin == pin) last = d.level ? 1 : 0;
    return last;
}

/* Restart seam capture. */
static int reboot_calls = 0;
static void test_reboot(void) { reboot_calls++; }
static std::vector<std::pair<std::string, std::string>> restart_pubs;
static void test_restart_pub(const char* ns, const char* v) {
    restart_pubs.push_back({ns ? ns : "", v ? v : ""});
}

/* ===============================================================
 * FINGERPRINT PARITY — the CXC-FPBUS-SCEN-* + CXC-FPHAL-PARITY-TEST
 * scenarios from python/composeio/peripherals/fingerprint/selftest.py
 * (main @ 0ba4e8b), asserted against the C++ mock HAL + device twin.
 * Green here == parity proven ([CXC-FPHAL-PARITY-TEST]).
 * =============================================================== */

struct FpBusEvent {
    std::string ns;
    std::string event_type;
    FpData data;
    std::string source;
};

class FpRecordingBus final : public FingerprintBusSink {
public:
    std::vector<FpBusEvent> events;
    void publish_event(const std::string& ns, const std::string& event_type,
                       const FpData& data, const std::string& source) override {
        events.push_back({ ns, event_type, data, source });
    }
    std::vector<const FpBusEvent*> of_type(const std::string& t) const {
        std::vector<const FpBusEvent*> out;
        for (const auto& e : events) if (e.event_type == t) out.push_back(&e);
        return out;
    }
    int count(const std::string& t) const { return (int)of_type(t).size(); }
    const FpBusEvent* last(const std::string& t) const {
        const FpBusEvent* r = nullptr;
        for (const auto& e : events) if (e.event_type == t) r = &e;
        return r;
    }
};

/* Correctly-sized opaque bytes for the mock's default template_size_bytes
 * (1536), so HAL-level tests exercise the OPERATION under test, not the
 * [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] wrong-size rejection. Content still
 * starts with `seed` so tests that inspect the payload stay distinguishable. */
static TemplateBytes fp_synth(const std::string& seed) {
    const size_t n = static_cast<size_t>(
        MockFingerprintHAL::default_capabilities().template_size_bytes);
    std::string out;
    out.reserve(n);
    while (out.size() < n) out += seed;
    out.resize(n);
    return out;
}

static PushEntry fp_entry(const char* person, int ordinal, const char* character,
                          const char* role, const char* tmpl = nullptr) {
    PushEntry e;
    e.person = person; e.ordinal = ordinal; e.character = character; e.rbac = role;
    e.tmpl = tmpl ? fp_synth(tmpl)
                  : fp_synth(std::string("tmpl-") + person + "-" +
                             std::to_string(ordinal));
    return e;
}

static std::map<std::string, std::vector<std::string>> fp_default_roles() {
    return { { "crew", { "open_hatch" } }, { "guest", {} } };
}

static TemplatePush fp_push(const char* uuid, std::vector<PushEntry> entries,
                            std::map<std::string, std::vector<std::string>> roles
                                = fp_default_roles()) {
    TemplatePush p;
    p.template_set_uuid = uuid ? uuid : "";
    p.entries = std::move(entries);
    p.role_capability_table = std::move(roles);
    return p;
}

static std::vector<PushEntry> fp_cast() {
    return { fp_entry("alice", 1, "Captain", "crew"),
             fp_entry("alice", 2, "Captain", "crew"),
             fp_entry("bob", 1, "Passenger", "guest") };
}

static Slot fp_crew_slot(const FingerprintDevice& dev) {
    for (const auto& kv : dev.bridge()) if (kv.second.role == "crew") return kv.first;
    return -1;
}
static Slot fp_guest_slot(const FingerprintDevice& dev) {
    for (const auto& kv : dev.bridge()) if (kv.second.role == "guest") return kv.first;
    return -1;
}
static bool fp_data_has(const FpData& d, const char* k) { return d.count(k) != 0; }
static std::string fp_data_str(const FpData& d, const char* k) {
    auto it = d.find(k); return it != d.end() ? it->second.s : std::string();
}
static double fp_data_num(const FpData& d, const char* k) {
    auto it = d.find(k); return it != d.end() ? it->second.n : 0.0;
}

static void fingerprint_parity_tests() {
    /* ---- HAL PARITY — [CXC-FPHAL-PARITY-TEST] ---------------------- */

    /* test_parity_capabilities_five_fields_typed_and_stable */
    {
        MockFingerprintHAL hal;
        Capabilities caps = hal.capabilities();
        CHECK(caps.slot_count == 200 &&
              caps.supports_readback && caps.captures_per_template == 2 &&
              caps.template_size_bytes == 1536 &&
              caps.has_feedback_channel(FeedbackChannel::AURA_LED),
              "SCEN parity_capabilities: five fields present + correct");
        CHECK(hal.capabilities() == caps,
              "SCEN parity_capabilities: stable for the life of the object");
    }

    /* test_parity_enroll_drives_to_stored_slot */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; hal.empty(err);
        EnrollmentSession s = hal.begin_enroll(1);
        CHECK(s.captures_required() == hal.capabilities().captures_per_template,
              "SCEN parity_enroll: captures_required == captures_per_template");
        while (!s.complete()) s.capture();
        CHECK(s.slot().has_value(), "SCEN parity_enroll: a slot was allocated");
        CHECK(hal.read_occupancy() == std::set<Slot>{ *s.slot() },
              "SCEN parity_enroll: occupancy == {slot}");
    }

    /* test_parity_sequential_install_returns_distinct_noncontiguous_slots */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; hal.empty(err);
        std::vector<Slot> slots;
        for (int i = 0; i < 4; i++) {
            Slot s; hal.store_template(fp_synth("t" + std::to_string(i)), s, err);
            slots.push_back(s);
        }
        std::set<Slot> uniq(slots.begin(), slots.end());
        CHECK(uniq.size() == 4, "SCEN parity_sequential: 4 distinct slots");
        CHECK(hal.read_occupancy() == uniq,
              "SCEN parity_sequential: occupancy == returned set");
        CHECK(uniq != (std::set<Slot>{ 0, 1, 2, 3 }),
              "SCEN parity_sequential: slot set is NON-CONTIGUOUS");
    }

    /* test_parity_read_template_round_trips */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; hal.empty(err);
        TemplateBytes payload = fp_synth("opaque-vendor-blob");
        Slot s; hal.store_template(payload, s, err);
        TemplateBytes back;
        CHECK(hal.read_template(s, back, err) && back == payload,
              "SCEN parity_readback: read_template round-trips byte-identical");
    }

    /* test_parity_readback_unsupported_fails_typed */
    {
        Capabilities caps = MockFingerprintHAL::default_capabilities();
        caps.supports_readback = false;
        MockFingerprintHAL hal(caps);
        FingerprintHALFailure err; Slot s; hal.store_template(fp_synth("x"), s, err);
        TemplateBytes back;
        CHECK(!hal.read_template(s, back, err) && err.axis == FailureAxis::UNSUPPORTED,
              "SCEN parity_readback_unsupported: fails UNSUPPORTED, never silently");
    }

    /* test_parity_empty_leaves_occupancy_empty */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; Slot s;
        hal.store_template(fp_synth("a"), s, err);
        hal.empty(err);
        CHECK(hal.read_occupancy().empty(),
              "SCEN parity_empty: empty() leaves occupancy empty");
    }

    /* test_parity_roster_stamp_round_trips_and_none_when_unstamped */
    {
        MockFingerprintHAL hal;
        CHECK(!hal.read_roster_stamp().has_value(),
              "SCEN parity_stamp: never-stamped reads None (!= empty bytes)");
        hal.write_roster_stamp("uuid-1234");
        auto got = hal.read_roster_stamp();
        CHECK(got.has_value() && *got == "uuid-1234",
              "SCEN parity_stamp: write then read is byte-identical");
    }

    /* test_parity_empty_store_never_matches */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; hal.empty(err);
        CHECK(hal.match().outcome != MatchOutcome::MATCHED,
              "SCEN parity_empty_match: an empty store never MATCHES");
    }

    /* test_parity_store_past_capacity_fails_capacity_axis (NEGATIVE CONTROL) */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; Slot s;
        hal.empty(err);
        hal.store_template(fp_synth("a"), s, err);
        hal.store_template(fp_synth("b"), s, err);
        std::set<Slot> before = hal.read_occupancy();
        hal.inject_store_failure(3, FailureAxis::CAPACITY);
        CHECK(!hal.store_template(fp_synth("c"), s, err) && err.axis == FailureAxis::CAPACITY,
              "SCEN parity_overflow: over-capacity store fails on CAPACITY axis");
        CHECK(hal.read_occupancy() == before,
              "SCEN parity_overflow: occupancy unchanged on a failed store");
    }

    /* test_parity_unknown_code_is_nonfatal */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; Slot s;
        hal.empty(err);
        hal.inject_store_failure(1, FailureAxis::UNKNOWN_CODE);
        CHECK(!hal.store_template(fp_synth("x"), s, err) &&
              err.axis == FailureAxis::UNKNOWN_CODE && err.sensor_code.has_value(),
              "SCEN parity_unknown_code: raises, quotes the raw byte");
        /* object remains usable */
        CHECK(hal.capabilities().slot_count == 200 && hal.read_occupancy().empty(),
              "SCEN parity_unknown_code: non-fatal — object still usable");
    }

    /* test_parity_store_wrong_size_template_rejected (NEGATIVE CONTROL) —
     * [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] device-side: a template whose byte
     * length differs from the announced template_size_bytes is REJECTED at
     * the HAL with a failure axis — never a silent accept. Last line of
     * defense against bricking the sensor with a malformed write. */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; hal.empty(err);
        TemplateBytes wrong(hal.capabilities().template_size_bytes - 1, 'x');
        Slot s;
        CHECK(!hal.store_template(wrong, s, err) && err.axis == FailureAxis::CAPACITY,
              "SCEN parity_wrong_size: a mis-sized template is rejected, never accepted");
        CHECK(hal.read_occupancy().empty(),
              "SCEN parity_wrong_size: a rejected store allocates no slot");
    }

    /* test_parity_store_correct_size_template_accepted (positive control) */
    {
        MockFingerprintHAL hal;
        FingerprintHALFailure err; hal.empty(err);
        TemplateBytes ok_template = fp_synth("good");
        Slot s;
        CHECK(hal.store_template(ok_template, s, err),
              "SCEN parity_wrong_size positive control: correctly-sized template is accepted");
        CHECK(hal.read_occupancy() == std::set<Slot>{ s },
              "SCEN parity_wrong_size positive control: occupancy == {slot}");
    }

    /* test_parity_hal_originates_no_feedback */
    {
        MockFingerprintHAL hal;
        hal.match();
        EnrollmentSession s = hal.begin_enroll(1);
        while (!s.complete()) s.capture();
        CHECK(hal.feedback_log().empty(),
              "SCEN parity_no_feedback: HAL originates no feedback of its own");
    }

    /* test_parity_empty_feedback_channels_fails_unsupported */
    {
        Capabilities caps = MockFingerprintHAL::default_capabilities();
        caps.feedback_channels = 0;
        MockFingerprintHAL hal(caps);
        FingerprintHALFailure err;
        CHECK(!hal.command_feedback(FeedbackState::ACCEPT, FpProgress{}, err) &&
              err.axis == FailureAxis::UNSUPPORTED,
              "SCEN parity_no_channels: empty feedback set fails UNSUPPORTED");
    }

    /* test_failure_taxonomy_disjointness_enforced */
    {
        CHECK(!fp_failure_valid(FailureAxis::TRANSPORT, std::optional<int>(0x01)),
              "SCEN taxonomy: transport quotes NO sensor_code");
        CHECK(!fp_failure_valid(FailureAxis::SENSOR_REFUSAL, std::nullopt),
              "SCEN taxonomy: sensor_refusal MUST carry a sensor_code");
    }

    /* test_match_result_slot_and_score_together_or_not_at_all */
    {
        CHECK(fp_match_result_valid(MatchOutcome::MATCHED, true, true),
              "SCEN match_result: MATCHED with slot+score is valid");
        CHECK(fp_match_result_valid(MatchOutcome::NO_MATCH, false, false),
              "SCEN match_result: NO_MATCH with neither is valid");
        CHECK(!fp_match_result_valid(MatchOutcome::MATCHED, true, false),
              "SCEN match_result: MATCHED without a score is invalid");
        CHECK(!fp_match_result_valid(MatchOutcome::NO_MATCH, true, true),
              "SCEN match_result: NO_MATCH carrying slot/score is invalid");
    }

    /* ---- PUSH RECEIPT — the CXC-FPBUS-SCEN-* scenarios ------------- */

    /* test_uuid_compare_reads_before_any_delete_or_write */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        hal.reset_log();
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        auto ops = hal.op_sequence();
        auto idx = [&](const char* o) {
            for (size_t i = 0; i < ops.size(); i++) if (ops[i] == o) return (int)i;
            return -1;
        };
        int rd = idx("read_roster_stamp"), em = idx("empty"), st = idx("store_template");
        CHECK(rd >= 0 && rd < em && rd < st,
              "SCEN-UUID-COMPARE: UUID read precedes any delete or write");
    }

    /* test_scen_same_set_pushed_twice */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        hal.reset_log();
        std::string status = dev.receive_push(fp_push("uuid-A", fp_cast()));
        auto ops = hal.op_sequence();
        bool has_empty = false, has_store = false;
        for (auto& o : ops) { if (o == "empty") has_empty = true; if (o == "store_template") has_store = true; }
        CHECK(status == "loaded" && !has_empty && !has_store,
              "SCEN-SAME-SET-PUSHED-TWICE: matching UUID = zero delete/write");
        CHECK(bus.count("template_set_loaded") >= 1 &&
              fp_data_str(bus.last("template_set_loaded")->data, "template_set_uuid") == "uuid-A",
              "SCEN-SAME-SET-PUSHED-TWICE: template_set_loaded(uuid-A)");
    }

    /* test_scen_all_templates_save */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        hal.reset_log();
        std::string status = dev.receive_push(fp_push("uuid-A", fp_cast()));
        auto ops = hal.op_sequence();
        int empty_idx = -1, stamp_idx = -1;
        std::vector<int> store_idxs;
        for (size_t i = 0; i < ops.size(); i++) {
            if (ops[i] == "empty" && empty_idx < 0) empty_idx = (int)i;
            if (ops[i] == "store_template") store_idxs.push_back((int)i);
            if (ops[i] == "write_roster_stamp") stamp_idx = (int)i;
        }
        bool del_first = true, stamp_last = true;
        for (int i : store_idxs) { if (empty_idx >= i) del_first = false;
                                   if (stamp_idx <= i) stamp_last = false; }
        auto stamp = hal.read_roster_stamp();
        CHECK(status == "loaded" && del_first && stamp_last &&
              hal.occupancy_count() == 3 && stamp.has_value() && *stamp == "uuid-A" &&
              bus.count("template_set_loaded") == 1 &&
              bus.events.back().event_type == "template_set_loaded",
              "SCEN-ALL-TEMPLATES-SAVE: delete<writes<stamp, ack last");
    }

    /* test_scen_partial_load_holds_stamp (NEGATIVE CONTROL) */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        int loaded_before = bus.count("template_set_loaded");
        hal.reset_log();
        hal.inject_store_failure(2, FailureAxis::CAPACITY);
        std::string status = dev.receive_push(fp_push("uuid-B", fp_cast()));
        auto stamp = hal.read_roster_stamp();
        CHECK(status.rfind("load_failed", 0) == 0 &&
              (!stamp.has_value() || *stamp != "uuid-B") &&
              bus.count("template_set_loaded") == loaded_before,
              "SCEN-PARTIAL-LOAD-HOLDS-STAMP: stamp NOT advanced, no new loaded");
    }

    /* test_scen_load_failure */
    {
        std::vector<PushEntry> five;
        for (int i = 0; i < 5; i++)
            five.push_back(fp_entry(("p" + std::to_string(i)).c_str(), 1,
                                    ("C" + std::to_string(i)).c_str(), "crew"));
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        hal.reset_log();
        hal.inject_store_failure(3, FailureAxis::CAPACITY);
        std::string status = dev.receive_push(fp_push("uuid-A", five));
        const FpBusEvent* failed = bus.last("template_set_failed");
        /* [CXC-FPBUS-FAILURE-ON-THE-WIRE] the closed six-axis taxonomy
         * crosses the wire verbatim -- {axis, sensor_code, detail}, never a
         * relabeled error_code/human_readable_error_string. capacity carries
         * no sensor_code (ABSENT, not null). */
        CHECK(status == "load_failed" && bus.count("template_set_failed") == 1 &&
              failed && fp_data_str(failed->data, "template_set_uuid") == "uuid-A" &&
              fp_data_str(failed->data, "axis") == "capacity" &&
              !fp_data_has(failed->data, "sensor_code") &&
              fp_data_has(failed->data, "detail") &&
              !hal.read_roster_stamp().has_value() && hal.occupancy_count() == 0,
              "SCEN-LOAD-FAILURE: template_set_failed, stamp unmodified, emptied");
    }

    /* test_scen_load_failure_carries_sensor_code_on_sensor_refusal_axis */
    {
        std::vector<PushEntry> five;
        for (int i = 0; i < 5; i++)
            five.push_back(fp_entry(("p" + std::to_string(i)).c_str(), 1,
                                    ("C" + std::to_string(i)).c_str(), "crew"));
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        hal.reset_log();
        hal.inject_store_failure(3, FailureAxis::SENSOR_REFUSAL);
        std::string status = dev.receive_push(fp_push("uuid-A", five));
        const FpBusEvent* failed = bus.last("template_set_failed");
        CHECK(status == "load_failed" && failed &&
              fp_data_str(failed->data, "axis") == "sensor_refusal" &&
              fp_data_has(failed->data, "sensor_code"),
              "SCEN-LOAD-FAILURE sensor_refusal axis: sensor_code present on the wire");
    }

    /* test_load_failure_positive_control */
    {
        std::vector<PushEntry> five;
        for (int i = 0; i < 5; i++)
            five.push_back(fp_entry(("p" + std::to_string(i)).c_str(), 1,
                                    ("C" + std::to_string(i)).c_str(), "crew"));
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        std::string status = dev.receive_push(fp_push("uuid-A", five));
        CHECK(status == "loaded" && bus.count("template_set_failed") == 0 &&
              bus.count("template_set_loaded") == 1,
              "SCEN-LOAD-FAILURE positive control: no fault -> loaded");
    }

    /* test_scen_delete_failure_escalation_recoverable (task-pins unknown_code) */
    {
        FailureAxis axes[] = { FailureAxis::TRANSPORT, FailureAxis::CAPACITY,
                               FailureAxis::PROTOCOL_DESYNC, FailureAxis::UNKNOWN_CODE };
        for (FailureAxis axis : axes) {
            MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
            FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                                  nullptr, std::nullopt, []() { return 1000.0; });
            hal.reset_log();
            hal.inject_store_failure(1, FailureAxis::CAPACITY);
            hal.inject_delete_failure(axis, 2);
            std::string status = dev.receive_push(fp_push("uuid-A", fp_cast()));
            CHECK(status == "load_failed_delete_failed" &&
                  bus.count("fingerprint_push_needed") == 1 &&
                  bus.count("fingerprint_inoperable") == 0 &&
                  dev.current_heartbeat_state() == HeartbeatState::PUSH_NEEDED,
                  "SCEN-DELETE-FAILURE-ESCALATION recoverable -> push_needed");
        }
    }

    /* test_scen_delete_failure_escalation_unrecoverable */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        hal.reset_log();
        hal.inject_store_failure(1, FailureAxis::CAPACITY);
        hal.inject_delete_failure(FailureAxis::SENSOR_REFUSAL, 2);
        std::string status = dev.receive_push(fp_push("uuid-A", fp_cast()));
        CHECK(status == "load_failed_delete_failed" &&
              bus.count("fingerprint_inoperable") == 1 &&
              bus.count("fingerprint_push_needed") == 0 &&
              dev.current_heartbeat_state() == HeartbeatState::INOPERABLE,
              "SCEN-DELETE-FAILURE-ESCALATION unrecoverable -> inoperable");
    }

    /* ---- TEMPLATE REFERENCE RESOLUTION SEAM -------------------------
     * [DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18] firmware-fetch
     * slice: a push entry's `template` may arrive as a `fingerprint://
     * <opaque-id>` STRING reference (RosterPublisher always converts once
     * validation passes — DP-CIO-6, no transport carve-out; JSON cannot
     * carry raw bytes, DP-CIO-18). receive_push() resolves it via the
     * injected `template_resolver_` seam, per-entry, immediately before
     * hal->store_template — mirroring device.py's `_resolve_template` +
     * `receive_push` exactly. */

    /* test_template_resolver_fetch_success */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        std::map<std::string, TemplateBytes> store = {
            { "fingerprint://alice-1", fp_synth("resolved-alice-1") },
            { "fingerprint://alice-2", fp_synth("resolved-alice-2") },
            { "fingerprint://bob-1",   fp_synth("resolved-bob-1")   },
        };
        TemplateResolver fake_resolver = [&](const std::string& ref, TemplateBytes& out,
                                             FingerprintHALFailure&) {
            auto it = store.find(ref);
            if (it == store.end()) return false;
            out = it->second;
            return true;
        };
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; },
                              nullptr, fake_resolver);
        std::vector<PushEntry> entries = {
            { "alice", 1, "Captain",   "crew",  "fingerprint://alice-1" },
            { "alice", 2, "Captain",   "crew",  "fingerprint://alice-2" },
            { "bob",   1, "Passenger", "guest", "fingerprint://bob-1"   },
        };
        std::string status = dev.receive_push(fp_push("uuid-A", entries));
        /* fetched bytes actually reached the HAL — read every occupied slot
         * back and confirm it matches the resolver's bytes, not the ref
         * string that was pushed. */
        bool bytes_reached_hal = true;
        for (const auto& kv : dev.bridge()) {
            TemplateBytes readback;
            FingerprintHALFailure rerr;
            if (!hal.read_template(kv.first, readback, rerr)) { bytes_reached_hal = false; break; }
            bool matched_one = false;
            for (const auto& sv : store) if (readback == sv.second) matched_one = true;
            if (!matched_one) bytes_reached_hal = false;
        }
        CHECK(status == "loaded" && dev.bridge().size() == 3 && bytes_reached_hal &&
              hal.occupancy_count() == 3 && bus.count("template_set_loaded") == 1 &&
              bus.count("template_set_failed") == 0,
              "TEMPLATE-RESOLVER: fingerprint:// refs resolve, bridge populated, "
              "fetched bytes (not the ref string) reach the HAL");
    }

    /* test_template_resolver_fetch_failure */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        TemplateResolver failing_resolver = [](const std::string&, TemplateBytes&,
                                                FingerprintHALFailure& err) {
            err.axis = FailureAxis::TRANSPORT;
            err.sensor_code = std::nullopt;
            err.detail = "simulated fetch failure";
            return false;
        };
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; },
                              nullptr, failing_resolver);
        std::vector<PushEntry> entries = {
            { "alice", 1, "Captain", "crew", "fingerprint://alice-1" },
        };
        std::string status = dev.receive_push(fp_push("uuid-A", entries));
        const FpBusEvent* failed = bus.last("template_set_failed");
        CHECK(status == "load_failed" && bus.count("template_set_failed") == 1 &&
              failed && fp_data_str(failed->data, "axis") == "transport" &&
              !fp_data_has(failed->data, "sensor_code") &&
              fp_data_has(failed->data, "detail") &&
              !hal.read_roster_stamp().has_value() && hal.occupancy_count() == 0 &&
              dev.bridge().empty(),
              "TEMPLATE-RESOLVER: a resolve failure drives the SAME load-failure "
              "path a HAL store failure would — transport axis, no sensor_code, "
              "device emptied, stamp held");
    }

    /* test_template_resolver_null_with_reference */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        /* no resolver injected — the default nullptr */
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        std::vector<PushEntry> entries = {
            { "alice", 1, "Captain", "crew", "fingerprint://alice-1" },
        };
        std::string status = dev.receive_push(fp_push("uuid-A", entries));
        const FpBusEvent* failed = bus.last("template_set_failed");
        CHECK(status == "load_failed" && failed &&
              fp_data_str(failed->data, "axis") == "transport" &&
              !fp_data_has(failed->data, "sensor_code") &&
              hal.occupancy_count() == 0,
              "TEMPLATE-RESOLVER: a reference with no resolver configured -> "
              "TRANSPORT load-failure, no crash");
    }

    /* ---- SERVICE PUSH-TIME VALIDATIONS — [CXC-FPBUS-REQ-SET-*] ----- */

    /* test_scen_set_size_limit — cap is the TARGET DEVICE's announced
     * slot_count (here 20), NEVER a hardcoded constant. */
    {
        std::vector<PushEntry> twenty_one;
        for (int i = 0; i < 21; i++)
            twenty_one.push_back(fp_entry(("p" + std::to_string(i)).c_str(), 1,
                                          ("C" + std::to_string(i)).c_str(), "crew"));
        std::string reason;
        bool ok = validate_publishable(fp_push("uuid-A", twenty_one), reason, 20);
        CHECK(!ok && reason.find("21") != std::string::npos,
              "SCEN-SET-SIZE-LIMIT: a 21-template set is not publishable");
    }

    /* test_scen_set_uuid_present */
    {
        std::string reason;
        bool ok = validate_publishable(fp_push(nullptr, fp_cast()), reason);
        std::string reason2;
        bool ok2 = validate_publishable(fp_push("", fp_cast()), reason2);
        CHECK(!ok && !ok2,
              "SCEN-SET-UUID-PRESENT: a set with no UUID is not publishable");
    }

    /* test_publishable_positive_control */
    {
        std::vector<PushEntry> twenty;
        for (int i = 0; i < 20; i++)
            twenty.push_back(fp_entry(("p" + std::to_string(i)).c_str(), 1,
                                      ("C" + std::to_string(i)).c_str(), "crew"));
        std::string reason;
        bool ok = validate_publishable(fp_push("uuid-A", twenty), reason, 20);
        CHECK(ok && reason.empty(),
              "SCEN-publishable positive control: 20 + UUID IS publishable");
    }

    /* test_scen_template_size_guard — [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] a
     * template one byte off the announced template_size_bytes is refused; no
     * message reaches the bus (asserted at the validate_publishable seam).
     * Built WITHOUT fp_entry's tmpl default so the wrong size is not re-padded
     * back to correct by fp_synth. */
    {
        int tsz = MockFingerprintHAL::default_capabilities().template_size_bytes;
        PushEntry wrong;
        wrong.person = "p1"; wrong.ordinal = 1; wrong.character = "C1";
        wrong.rbac = "crew"; wrong.tmpl = std::string(tsz - 1, 'x');
        std::vector<PushEntry> one = { wrong };
        std::string reason;
        bool ok = validate_publishable(fp_push("uuid-A", one), reason, -1, tsz);
        CHECK(!ok && reason.find(std::to_string(tsz - 1)) != std::string::npos &&
              reason.find(std::to_string(tsz)) != std::string::npos,
              "SCEN-TEMPLATE-SIZE-GUARD: a mis-sized template is not publishable");
    }

    /* test_template_size_guard_positive_control */
    {
        int tsz = MockFingerprintHAL::default_capabilities().template_size_bytes;
        std::vector<PushEntry> one = { fp_entry("p1", 1, "C1", "crew") };
        std::string reason;
        bool ok = validate_publishable(fp_push("uuid-A", one), reason, -1, tsz);
        CHECK(ok && reason.empty(),
              "SCEN-TEMPLATE-SIZE-GUARD positive control: correctly-sized set IS publishable");
    }

    /* ---- HEARTBEATS — [CXC-FPBUS-REQ-*-HEARTBEAT] ----------------- */

    /* test_scen_push_needed_heartbeat */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        auto em = run_heartbeat_window(dev, 60.0);
        bool all_pn = em.size() == 4;
        for (auto& e : em) if (e.second != HeartbeatState::PUSH_NEEDED) all_pn = false;
        bool times_ok = em.size() == 4 && em[0].first == 0.0 && em[1].first == 15.0 &&
                        em[2].first == 30.0 && em[3].first == 45.0;
        CHECK(all_pn && times_ok && bus.count("fingerprint_push_needed") == 4 &&
              bus.count("fingerprint_ready") == 0,
              "SCEN-PUSH-NEEDED-HEARTBEAT: 4 emissions, 15s apart");
    }

    /* test_scen_inoperable_heartbeat */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        dev.set_hardware_present(false);
        auto em = run_heartbeat_window(dev, 60.0);
        bool all_inop = em.size() == 4;
        for (auto& e : em) if (e.second != HeartbeatState::INOPERABLE) all_inop = false;
        CHECK(all_inop && bus.count("fingerprint_inoperable") == 4 &&
              bus.count("fingerprint_ready") == 0,
              "SCEN-INOPERABLE-HEARTBEAT: 4 inoperable emissions, 15s apart");
    }

    /* test_ready_heartbeat_carries_uuid_and_count */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        auto em = run_heartbeat_window(dev, 150.0);
        bool all_ready = em.size() == 3;
        for (auto& e : em) if (e.second != HeartbeatState::READY) all_ready = false;
        bool times_ok = em.size() == 3 && em[0].first == 0.0 && em[1].first == 60.0 &&
                        em[2].first == 120.0;
        const FpBusEvent* ready = bus.last("fingerprint_ready");
        CHECK(all_ready && times_ok && ready &&
              fp_data_str(ready->data, "template_set_uuid") == "uuid-A" &&
              fp_data_num(ready->data, "count_authentication_attempts") == 0,
              "SCEN-READY-HEARTBEAT: 3 @60s, carries uuid + count 0");
    }

    /* test_heartbeats_are_mutually_exclusive */
    {
        MockFingerprintHAL hal; FpRecordingBus bus; DevicePersistence nvs;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.emit_heartbeat();                            /* unconfigured -> push_needed */
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        dev.emit_heartbeat();                            /* configured -> ready */
        dev.set_hardware_present(false);
        dev.emit_heartbeat();                            /* inoperable */
        CHECK(bus.count("fingerprint_push_needed") == 1 &&
              bus.count("fingerprint_ready") == 1 &&
              bus.count("fingerprint_inoperable") == 1,
              "SCEN-heartbeats mutually exclusive, jointly exhaustive");
    }

    /* ---- COUNTER — [CXC-FPBUS-MSG-FINGERPRINT-READY] counter-semantics ---- */

    /* test_counter_persists_across_reboot_same_uuid */
    {
        MockFingerprintHAL hal; DevicePersistence nvs;
        FpRecordingBus bus1;
        FingerprintDevice dev(&hal, &bus1, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        MatchResult granted = MatchResult::matched(fp_crew_slot(dev), 200);
        for (int i = 0; i < 3; i++) dev.resolve_match(granted);
        CHECK(dev.count_authentication_attempts() == 3,
              "SCEN-counter: three attempts counted");
        /* reboot: SAME hal (stamp+templates persist) + SAME nvs (count persists) */
        FpRecordingBus bus2;
        FingerprintDevice dev2(&hal, &bus2, "dev/hatch1", "open_hatch", &nvs,
                               nullptr, std::nullopt, []() { return 1000.0; });
        CHECK(dev2.count_authentication_attempts() == 3,
              "SCEN-counter: count RESUMES across reboot (same uuid)");
        dev2.receive_push(fp_push("uuid-A", fp_cast()));
        CHECK(dev2.count_authentication_attempts() == 3,
              "SCEN-counter: idempotent re-push does NOT reset the count");
    }

    /* test_counter_zeroes_on_new_uuid */
    {
        MockFingerprintHAL hal; DevicePersistence nvs; FpRecordingBus bus;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        MatchResult granted = MatchResult::matched(fp_crew_slot(dev), 200);
        dev.resolve_match(granted);
        dev.resolve_match(granted);
        CHECK(dev.count_authentication_attempts() == 2, "SCEN-counter: two before change");
        dev.receive_push(fp_push("uuid-B", fp_cast()));  /* genuine uuid change */
        CHECK(dev.count_authentication_attempts() == 0,
              "SCEN-counter: new uuid ZEROES the count");
    }

    /* ---- AUTH EVENT — [CXC-FPBUS-MSG-AUTH-EVENT] + RING ----------- */

    /* test_auth_event_all_three_outcomes_publish */
    {
        MockFingerprintHAL hal; DevicePersistence nvs; FpRecordingBus bus;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        dev.resolve_match(MatchResult::matched(fp_crew_slot(dev), 200));
        dev.resolve_match(MatchResult::matched(fp_guest_slot(dev), 200));
        dev.resolve_match(MatchResult::no_match());
        auto evs = bus.of_type("auth_event");
        bool ok = evs.size() == 3 &&
                  fp_data_str(evs[0]->data, "outcome") == "granted" &&
                  fp_data_str(evs[1]->data, "outcome") == "denied" &&
                  fp_data_str(evs[2]->data, "outcome") == "unrecognized";
        CHECK(ok, "SCEN-AUTH-EVENT: all three outcomes published, none suppressed");
    }

    /* test_auth_event_payload_shape */
    {
        MockFingerprintHAL hal; DevicePersistence nvs; FpRecordingBus bus;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        dev.resolve_match(MatchResult::matched(fp_crew_slot(dev), 200));
        const FpData& g = bus.last("auth_event")->data;
        bool keys = fp_data_has(g, "event_id") && fp_data_has(g, "device_id") &&
                    fp_data_has(g, "outcome") && fp_data_has(g, "timestamp");
        CHECK(keys && fp_data_str(g, "player") == "alice" &&
              fp_data_str(g, "character") == "Captain",
              "SCEN-AUTH-EVENT shape: granted carries player/character");
        CHECK(!fp_data_has(g, "gated_capability"),
              "SCEN-AUTH-EVENT shape: gated_capability is deliberately ABSENT (wire-economy)");
        dev.resolve_match(MatchResult::no_match());
        const FpData& u = bus.last("auth_event")->data;
        CHECK(!fp_data_has(u, "player") && !fp_data_has(u, "character"),
              "SCEN-AUTH-EVENT shape: unrecognized hides identity (no leak)");
    }

    /* test_auth_event_never_carries_score */
    {
        MockFingerprintHAL hal; DevicePersistence nvs; FpRecordingBus bus;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        dev.resolve_match(MatchResult::matched(fp_crew_slot(dev), 200));
        bool any_score = false;
        for (auto& e : bus.events) if (fp_data_has(e.data, "score")) any_score = true;
        CHECK(!any_score, "SCEN-AUTH-EVENT: the score NEVER crosses the bus");
    }

    /* test_ring_hides_denial_denied_and_unrecognized_identical */
    {
        MockFingerprintHAL hal; DevicePersistence nvs; FpRecordingBus bus;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        hal.reset_log();
        dev.resolve_match(MatchResult::matched(fp_guest_slot(dev), 200));  /* denied */
        FeedbackState denied_ring = hal.feedback_log().back().first;
        dev.resolve_match(MatchResult::no_match());                        /* unrecognized */
        FeedbackState unrec_ring = hal.feedback_log().back().first;
        dev.resolve_match(MatchResult::matched(fp_crew_slot(dev), 200));   /* granted */
        FeedbackState granted_ring = hal.feedback_log().back().first;
        auto outs = bus.of_type("auth_event");
        bool has_denied = false, has_unrec = false;
        for (auto* e : outs) {
            if (fp_data_str(e->data, "outcome") == "denied") has_denied = true;
            if (fp_data_str(e->data, "outcome") == "unrecognized") has_unrec = true;
        }
        CHECK(denied_ring == unrec_ring && granted_ring != denied_ring &&
              granted_ring == FeedbackState::ACCEPT && has_denied && has_unrec,
              "SCEN-RING-HIDES-DENIAL: ring collapses denied+unrec; event distinguishes");
    }

    /* test_auth_event_two_scopes_identical_shape */
    {
        MockFingerprintHAL hal; DevicePersistence nvs;
        FpRecordingBus local, uplink;
        FingerprintDevice dev(&hal, &local, "dev/hatch1", "open_hatch", &nvs,
                              &uplink, std::nullopt, []() { return 1000.0; },
                              []() { return std::string("evt-fixed"); });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        dev.resolve_match(MatchResult::matched(fp_crew_slot(dev), 200));
        CHECK(local.last("auth_event")->data == uplink.last("auth_event")->data,
              "SCEN-AUTH-EVENT scope-membrane: local == uplink, byte-identical");
    }

    /* test_no_finger_is_not_an_attempt */
    {
        MockFingerprintHAL hal; DevicePersistence nvs; FpRecordingBus bus;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        auto r = dev.resolve_match(MatchResult::no_finger());
        CHECK(!r.has_value() && bus.count("auth_event") == 0 &&
              dev.count_authentication_attempts() == 0,
              "SCEN-no-finger: an empty doorway is not an attempt");
    }

    /* test_below_threshold_match_produces_no_event */
    {
        MockFingerprintHAL hal; DevicePersistence nvs; FpRecordingBus bus;
        FingerprintDevice dev(&hal, &bus, "dev/hatch1", "open_hatch", &nvs,
                              nullptr, std::optional<int>(100), []() { return 1000.0; });
        dev.receive_push(fp_push("uuid-A", fp_cast()));
        auto low = dev.resolve_match(MatchResult::matched(fp_crew_slot(dev), 50));
        CHECK(!low.has_value() && bus.count("auth_event") == 0,
              "SCEN-threshold: below-floor score produces no identity event");
        auto high = dev.resolve_match(MatchResult::matched(fp_crew_slot(dev), 150));
        CHECK(high.has_value() && *high == AuthOutcome::GRANTED &&
              bus.count("auth_event") == 1,
              "SCEN-threshold: above the floor -> event");
    }

    /* ---- transport selection is OFFLINE by default (this dispatch) - */
    {
        TransportSelection sel = transport_select_resolve(nullptr);
        CHECK(sel.name == "offline" && sel.source == "default" && sel.known &&
              !sel.needs_network,
              "SCEN-transport: unset -> offline, known, no network");
        TransportSelection u = transport_select_resolve("uart");
        CHECK(u.name == "uart" && u.source == "nvs" && u.known && !u.needs_network,
              "SCEN-transport: uart is known + needs no IP stack");
        TransportSelection bad = transport_select_resolve("mqqt");
        CHECK(!bad.known && bad.name == "mqqt",
              "SCEN-transport: a typo does NOT fall back — known=false, loud");
    }

    /* ---- [interop] inbound push topic must be <dev>/template_set ---------- */
    {
        CHECK(fingerprint_inbound_push_topic("dev/hatch1") == "dev/hatch1/template_set",
              "INTEROP: inbound push consumed on <dev>/template_set (service contract)");
    }

    printf("  fingerprint parity: 42 selftest scenarios asserted against the "
           "C++ mock HAL + device twin\n");
}

int main() {
    /* ---------------------------------------------------------------
     * 1. Binding declaration + subscription introspection
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        CHECK(core.add_binding("out/led", "expr: ns('demo/level') > 50"),
              "expr binding parses via the real DSL");
        CHECK(core.add_binding("out/pct",
                               "expr: clamp(ns('demo/level') * 2, 0, 100)"),
              "clamp expr parses");
        CHECK(core.add_binding("out/echo", "demo/level"),
              "bare-namespace binding");
        CHECK(core.add_binding("out/and",
                               "expr: ns('alpha') > 0 and ns('beta') > 0"),
              "two-namespace expr");
        /* dedup: demo/level appears once despite three readers */
        CHECK(core.subscription_count() == 3, "subscriptions deduped");
        CHECK(strcmp(core.subscription_at(0), "demo/level") == 0,
              "first-appearance order [0]");
        CHECK(strcmp(core.subscription_at(1), "alpha") == 0, "order [1]");
        CHECK(strcmp(core.subscription_at(2), "beta") == 0, "order [2]");

        /* refusal: outside the closed grammar => binding NOT added */
        CHECK(!core.add_binding("out/bad", "expr: sqrt(2)"),
              "unknown function refused (parse error)");
        CHECK(!core.add_binding("out/bad2", "expr: 1 +"),
              "malformed expr refused");
        CHECK(core.binding_count() == 4, "refused bindings not added");
    }

    /* ---------------------------------------------------------------
     * 2. The R1 loop: publish -> re-evaluate -> derived output
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        core.add_binding("demo/led", "expr: ns('demo/level') > 50");
        core.add_binding("demo/level_pct",
                         "expr: clamp(ns('demo/level') * 2, 0, 100)");
        core.add_binding("demo/echo", "demo/level");

        events.clear();
        int fired = core.on_bus_value("demo/level",
                                      CoreValue::number(42.0), 1.0);
        CHECK(fired == 3, "first arrival fires all three (initial state)");
        CHECK(events.size() == 3, "three sink events");
        CHECK(ev_is_bool(events[0], "demo/led", false), "42 > 50 is false");
        CHECK(ev_is_number(events[1], "demo/level_pct", 84.0),
              "clamp(42*2,0,100) == 84");
        CHECK(ev_is_number(events[2], "demo/echo", 42.0),
              "bare echo passes the number through");

        events.clear();
        fired = core.on_bus_value("demo/level", CoreValue::number(80.0), 2.0);
        CHECK(fired == 3, "80 changes all three");
        CHECK(ev_is_bool(events[0], "demo/led", true), "80 > 50 is true");
        CHECK(ev_is_number(events[1], "demo/level_pct", 100.0),
              "clamp caps at 100");

        /* publish-on-change: led stays true, pct stays 100 -> only echo */
        events.clear();
        fired = core.on_bus_value("demo/level", CoreValue::number(90.0), 3.0);
        CHECK(fired == 1, "unchanged results suppressed");
        CHECK(ev_is_number(events[0], "demo/echo", 90.0),
              "only the echo changed");

        /* unrelated namespace: no reader, no eval, no output */
        size_t evals = core.eval_count();
        events.clear();
        fired = core.on_bus_value("other/thing", CoreValue::number(1.0), 4.0);
        CHECK(fired == 0, "unrelated namespace fires nothing");
        CHECK(core.eval_count() == evals, "and evaluates nothing");
    }

    /* ---------------------------------------------------------------
     * 3. Multi-namespace expr re-evaluates on EITHER input
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        core.add_binding("ready", "expr: ns('alpha') >= 40 and ns('beta') >= 60");

        events.clear();
        core.on_bus_value("alpha", CoreValue::number(50.0), 1.0);
        CHECK(events.size() == 1, "alpha arrival evaluates");
        CHECK(ev_is_bool(events[0], "ready", false),
              "beta unset reads 0.0 -> not ready");

        events.clear();
        core.on_bus_value("beta", CoreValue::number(75.0), 2.0);
        CHECK(events.size() == 1, "beta arrival re-evaluates");
        CHECK(ev_is_bool(events[0], "ready", true), "both above -> ready");

        events.clear();
        core.on_bus_value("beta", CoreValue::number(80.0), 3.0);
        CHECK(events.empty(), "still ready -> suppressed");
    }

    /* ---------------------------------------------------------------
     * 4. Input coercion parity (dsl_coerce_input on the bare path)
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        core.add_binding("out", "mode");

        events.clear();
        core.on_bus_value("mode", CoreValue::string("standby"), 1.0);
        CHECK(events.size() == 1 && events[0].value.type ==
              DslValue::Type::String && events[0].value.str == "standby",
              "plain string passes through");

        events.clear();
        core.on_bus_value("mode", CoreValue::boolean(true), 2.0);
        CHECK(events.size() == 1 && events[0].value.type ==
              DslValue::Type::String && events[0].value.str == "on",
              "bool coerces to the canon 'on' (ns()-read parity)");

        events.clear();
        core.on_bus_value("mode", CoreValue::string("42"), 3.0);
        CHECK(events.size() == 1 &&
              ev_is_number(events[0], "out", 42.0),
              "numeric string coerces to float64");
    }

    /* ---------------------------------------------------------------
     * 5. Eval error withholds the publish (never crashes)
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        CHECK(core.add_binding("out", "expr: $missing + ns('x')"),
              "unknown $var is an EVAL error, parses fine");
        events.clear();
        int fired = core.on_bus_value("x", CoreValue::number(1.0), 1.0);
        CHECK(fired == 0, "eval error -> publish withheld");
        CHECK(events.empty(), "no sink call");
        CHECK(*core.last_eval_error() != '\0', "error is reported");
    }

    /* ---------------------------------------------------------------
     * 6. dt/t tick context ([CXC-DSL-STATE] parity with runtime.tick)
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        core.add_binding("integrated", "expr: ns('flow') * dt");

        events.clear();
        core.on_bus_value("flow", CoreValue::number(4.0), 1.0);
        CHECK(ev_is_number(events[0], "integrated", 0.0),
              "first tick dt == 0.0");

        events.clear();
        core.on_bus_value("flow", CoreValue::number(4.0), 3.5);
        CHECK(ev_is_number(events[0], "integrated", 10.0),
              "second tick dt == 2.5 -> 4 * 2.5");
    }

    /* ---------------------------------------------------------------
     * 7. Local cascade: a derived output feeds a downstream binding
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        core.add_binding("d1", "expr: ns('x') * 2");
        core.add_binding("d2", "expr: ns('d1') + 1");

        events.clear();
        int fired = core.on_bus_value("x", CoreValue::number(3.0), 1.0);
        CHECK(fired == 2, "x fires d1 and cascades to d2");
        CHECK(ev_is_number(events[0], "d1", 6.0), "d1 == 6");
        CHECK(ev_is_number(events[1], "d2", 7.0), "d2 sees d1's publish");
    }

    /* ---------------------------------------------------------------
     * 8. Non-converging cycle: depth-capped, terminates
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        core.add_binding("c", "expr: ns('c') + 1");
        events.clear();
        int fired = core.on_bus_value("c", CoreValue::number(1.0), 1.0);
        CHECK(fired >= 1 && fired <= ReactiveCore::kMaxCascade,
              "cycle terminates at the cascade cap");
        CHECK(strstr(core.last_eval_error(), "cascade") != nullptr,
              "cycle is reported");
        /* a converging follow-up still works */
        events.clear();
        core.on_bus_value("other", CoreValue::number(0.0), 2.0);
        CHECK(events.empty(), "core still healthy after the cap");
    }

    /* ---------------------------------------------------------------
     * 9. Corpus-pinned spot checks through the core (real DSL semantics)
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        core.add_binding("r", "expr: round(ns('v'))");
        events.clear();
        core.on_bus_value("v", CoreValue::number(2.5), 1.0);
        CHECK(ev_is_number(events[0], "r", 2.0),
              "round() is half-to-even (banker's), [CXC-DSL-C12]");

        core.add_binding("g", "expr: ns('num') / ns('zero')");
        events.clear();
        core.on_bus_value("num", CoreValue::number(7.0), 2.0);
        CHECK(ev_is_number(events[0], "g", 0.0),
              "guarded x/0 -> +0.0, [CXC-DSL-C4]");

        core.add_binding("eq", "expr: ns('sw') == 'on'");
        events.clear();
        core.on_bus_value("sw", CoreValue::boolean(true), 3.0);
        CHECK(ev_is_bool(events[0], "eq", true),
              "canon-typed equality: true == 'on', [CXC-DSL-C6]");
    }

    /* ===============================================================
     * R2 — the output-actuator HAL ([CXC-ER-C4], [CXC-HAL-ACTUATE])
     * =============================================================== */

    /* ---------------------------------------------------------------
     * 10. Binary actuator: the [CXC-BIND] binary canon -> GPIO state
     * --------------------------------------------------------------- */
    {
        static const ActuatorSpec specs[] = {
            { "light/5", ActuatorMode::Binary, 4 },
            { "light/6", ActuatorMode::Binary, 7, /*active_high*/ false },
        };
        ActuatorHal hal;
        hal.set_sink(act_sink, nullptr);
        hal.configure(specs, 2);
        CHECK(hal.namespace_count() == 2, "two actuator namespaces");

        acts.clear();
        int n = hal.on_value("light/5", DslValue::string(std::string("on")));
        CHECK(n == 1 && acts.size() == 1, "first value always fires");
        CHECK(act_is_binary(acts[0], "light/5", true, true),
              "'on' -> ON, level HIGH (active-high)");

        acts.clear();
        hal.on_value("light/5", DslValue::string(std::string("ON")));
        CHECK(acts.empty(), "'ON' canons to on -> unchanged, suppressed");

        acts.clear();
        hal.on_value("light/5", DslValue::string(std::string("OFF")));
        CHECK(acts.size() == 1 &&
              act_is_binary(acts[0], "light/5", false, false),
              "'OFF' canons off -> OFF, level LOW");

        acts.clear();
        hal.on_value("light/5", DslValue::number(1.0));
        CHECK(acts.size() == 1 &&
              act_is_binary(acts[0], "light/5", true, true),
              "number 1 is truthy -> ON");

        acts.clear();
        hal.on_value("light/5", DslValue::number(0.0));
        CHECK(acts.size() == 1 &&
              act_is_binary(acts[0], "light/5", false, false),
              "number 0 -> OFF");

        acts.clear();
        hal.on_value("light/5", DslValue::boolean(true));
        CHECK(acts.size() == 1 &&
              act_is_binary(acts[0], "light/5", true, true),
              "bool true -> ON");

        /* polarity: active-low inverts the PHYSICAL level only */
        acts.clear();
        hal.on_value("light/6", DslValue::string(std::string("yes")));
        CHECK(acts.size() == 1 &&
              act_is_binary(acts[0], "light/6", true, false),
              "'yes' -> logical ON, physical LOW (active-low)");

        acts.clear();
        CHECK(hal.on_value("light/99", DslValue::number(1.0)) == 0,
              "unknown namespace drives nothing");
        CHECK(acts.empty(), "no sink call for unknown namespace");
    }

    /* ---------------------------------------------------------------
     * 11. PWM actuator: numeric scale -> duty, clamped; non-numeric
     *     withholds the drive
     * --------------------------------------------------------------- */
    {
        static const ActuatorSpec specs[] = {
            { "lamp", ActuatorMode::Pwm, 5 },   /* 0..100 -> 0..255 */
        };
        ActuatorHal hal;
        hal.set_sink(act_sink, nullptr);
        hal.configure(specs, 1);

        acts.clear();
        hal.on_value("lamp", DslValue::number(50.0));
        CHECK(acts.size() == 1 && act_is_duty(acts[0], "lamp", 128),
              "50% -> duty 128 (llround 127.5)");

        acts.clear();
        hal.on_value("lamp", DslValue::number(100.0));
        CHECK(acts.size() == 1 && act_is_duty(acts[0], "lamp", 255),
              "100% -> full duty");

        acts.clear();
        hal.on_value("lamp", DslValue::number(150.0));
        CHECK(acts.empty(), "over-range clamps to 255 -> unchanged");

        acts.clear();
        hal.on_value("lamp", DslValue::number(-10.0));
        CHECK(acts.size() == 1 && act_is_duty(acts[0], "lamp", 0),
              "under-range clamps to 0");

        acts.clear();
        hal.on_value("lamp", DslValue::string(std::string("42")));
        CHECK(acts.size() == 1 && act_is_duty(acts[0], "lamp", 107),
              "numeric string coerces (42% -> 107)");

        acts.clear();
        int n = hal.on_value("lamp", DslValue::string(std::string("banana")));
        CHECK(n == 0 && acts.empty(), "non-numeric drive is WITHHELD");
        CHECK(*hal.last_error() != '\0', "withheld drive is reported");

        acts.clear();
        n = hal.on_value("lamp", DslValue::number(std::nan("")));
        CHECK(n == 0 && acts.empty(), "NaN drive is WITHHELD");
    }

    /* ---------------------------------------------------------------
     * 12. Stepper: value -> target-position math, direction, and the
     *     full-step sequencer (patterns + pacing + arrival)
     * --------------------------------------------------------------- */
    {
        static const ActuatorSpec specs[] = {
            /* 0..100 -> 0..8 steps, 3 ms/step, no homing */
            { "gauge/2", ActuatorMode::Stepper, 1, true, 0.0, 100.0, 8,
              3, false },
        };
        ActuatorHal hal;
        hal.set_sink(act_sink, nullptr);
        hal.configure(specs, 1);
        CHECK(!hal.stepping(), "no homing -> starts at rest");

        acts.clear();
        hal.on_value("gauge/2", DslValue::number(50.0));
        CHECK(acts.size() == 1 &&
              acts[0].cmd.kind == ActuatorCommand::Kind::StepperTarget &&
              acts[0].cmd.target == 4 && acts[0].cmd.position == 0,
              "50% of 8 -> target 4 from 0");
        CHECK(hal.stepper_target("gauge/2") == 4, "target introspection");
        CHECK(hal.stepping(), "steps pending");

        /* the walk: 4 steps up, full-step patterns follow position&3 */
        acts.clear();
        hal.tick(0);
        CHECK(acts.size() == 1 &&
              act_is_step(acts[0], "gauge/2", 1, +1,
                          ActuatorHal::kFullStep[1]),
              "step 1: pos 1, dir +1, coils 0110");
        hal.tick(0);
        hal.tick(2);
        CHECK(acts.size() == 1, "pacing: no step before interval_ms");
        hal.tick(3);
        CHECK(acts.size() == 2 &&
              act_is_step(acts[1], "gauge/2", 2, +1,
                          ActuatorHal::kFullStep[2]),
              "step 2 at t=3ms, coils 1010");
        hal.tick(6);
        hal.tick(9);
        CHECK(acts.size() == 5, "4 steps + arrival");
        CHECK(act_is_step(acts[2], "gauge/2", 3, +1,
                          ActuatorHal::kFullStep[3]), "step 3 coils 1001");
        CHECK(act_is_step(acts[3], "gauge/2", 4, +1,
                          ActuatorHal::kFullStep[0]), "step 4 coils 0101");
        CHECK(acts[4].cmd.kind == ActuatorCommand::Kind::StepperArrived &&
              acts[4].cmd.position == 4, "arrival reported at target");
        CHECK(hal.stepper_position("gauge/2") == 4, "position == 4");
        CHECK(!hal.stepping(), "at rest after arrival");

        /* retarget DOWN: direction flips, patterns walk backward */
        acts.clear();
        hal.on_value("gauge/2", DslValue::number(25.0));
        CHECK(acts.size() == 1 && acts[0].cmd.target == 2 &&
              acts[0].cmd.position == 4, "25% -> target 2 from 4");
        acts.clear();
        hal.tick(20);
        CHECK(acts.size() == 1 &&
              act_is_step(acts[0], "gauge/2", 3, -1,
                          ActuatorHal::kFullStep[3]),
              "downward step: pos 3, dir -1");
        hal.tick(23);
        CHECK(acts.size() == 3 &&
              act_is_step(acts[1], "gauge/2", 2, -1,
                          ActuatorHal::kFullStep[2]) &&
              acts[2].cmd.kind == ActuatorCommand::Kind::StepperArrived,
              "arrives at 2");

        /* clamp + dedupe + withhold */
        acts.clear();
        hal.on_value("gauge/2", DslValue::number(200.0));
        CHECK(acts.size() == 1 && acts[0].cmd.target == 8,
              "over-range clamps to full scale");
        acts.clear();
        hal.on_value("gauge/2", DslValue::number(200.0));
        CHECK(acts.empty(), "same value -> no new target");
        acts.clear();
        CHECK(hal.on_value("gauge/2",
                           DslValue::string(std::string("mid"))) == 0,
              "non-numeric stepper value withheld");
    }

    /* ---------------------------------------------------------------
     * 13. Homing: worst-case start walks the full scale to zero
     * --------------------------------------------------------------- */
    {
        static const ActuatorSpec specs[] = {
            { "gauge/1", ActuatorMode::Stepper, 0, true, 0.0, 100.0, 6,
              3, /*home_on_boot*/ true },
        };
        ActuatorHal hal;
        hal.set_sink(act_sink, nullptr);
        acts.clear();
        hal.configure(specs, 1);
        CHECK(acts.size() == 1 &&
              acts[0].cmd.kind == ActuatorCommand::Kind::StepperTarget &&
              acts[0].cmd.target == 0 && acts[0].cmd.position == 6,
              "homing intent reported: full scale -> 0");
        CHECK(hal.stepping(), "homing pending");

        acts.clear();
        for (uint32_t t = 0; t <= 30; t += 3) hal.tick(t);
        CHECK(acts.size() == 7, "6 homing steps + arrival");
        CHECK(act_is_step(acts[0], "gauge/1", 5, -1,
                          ActuatorHal::kFullStep[1]),
              "homing walks downward");
        CHECK(acts[6].cmd.kind == ActuatorCommand::Kind::StepperArrived &&
              acts[6].cmd.position == 0, "homed to zero");

        /* a first value equal to the homing target is QUIET */
        acts.clear();
        hal.on_value("gauge/1", DslValue::number(0.0));
        CHECK(acts.empty(), "value 0 == current target -> nothing new");
        hal.on_value("gauge/1", DslValue::number(100.0));
        CHECK(acts.size() == 1 && acts[0].cmd.target == 6,
              "then full scale retargets");
    }

    /* ---------------------------------------------------------------
     * 14. Define once, actuate many: ONE namespace, two actuators
     * --------------------------------------------------------------- */
    {
        static const ActuatorSpec specs[] = {
            { "beacon", ActuatorMode::Binary, 2 },
            { "beacon", ActuatorMode::Pwm, 5 },
        };
        ActuatorHal hal;
        hal.set_sink(act_sink, nullptr);
        hal.configure(specs, 2);
        CHECK(hal.namespace_count() == 1, "one subscription, two drives");

        acts.clear();
        int n = hal.on_value("beacon", DslValue::number(100.0));
        CHECK(n == 2 && acts.size() == 2, "both actuators fire");
        CHECK(act_is_binary(acts[0], "beacon", true, true) &&
              act_is_duty(acts[1], "beacon", 255),
              "binary ON + full duty from the same value");
    }

    /* ---------------------------------------------------------------
     * 15. R3 ([CXC-ER-RENDER]) — binding result -> widget attribute
     * application logic (cyd/src/reactive_apply.h, the pure half of
     * "a bus value moves a needle").
     * --------------------------------------------------------------- */
    {
        /* Bindable-attr table mirrors bindings.py BINDABLE_ATTRS_BY_TYPE
         * (embedded subset) + universal visible. */
        CHECK(reactive_attr_lookup("gauge", "value") == RAttr::Value,
              "gauge.value bindable");
        CHECK(reactive_attr_lookup("led", "state") == RAttr::State,
              "led.state bindable");
        CHECK(reactive_attr_lookup("label", "text") == RAttr::Text,
              "label.text bindable");
        CHECK(reactive_attr_lookup("rectangle", "fill_color") ==
                  RAttr::FillColor,
              "rect.fill_color bindable");
        CHECK(reactive_attr_lookup("button", "visible") == RAttr::Visible,
              "visible is universal");
        CHECK(reactive_attr_lookup("button", "value") == RAttr::None,
              "button.value not bindable on this tier");
        CHECK(strcmp(reactive_primary_attr("gauge"), "value") == 0 &&
                  strcmp(reactive_primary_attr("led"), "state") == 0 &&
                  strcmp(reactive_primary_attr("label"), "text") == 0 &&
                  reactive_primary_attr("rectangle") == nullptr,
              "widget-level namespace: -> primary attr table");
        /* checklist / status_list: the namespace-subscription items list
         * (on_bus_state payload) — a JSON string attr, dradis_contacts
         * precedent (NOT a bindings.py BINDABLE_ATTRS_BY_TYPE row). */
        CHECK(reactive_attr_lookup("checklist", "items") ==
                  RAttr::ChecklistItems &&
                  reactive_attr_lookup("status_list", "items") ==
                  RAttr::ChecklistItems &&
                  reactive_attr_lookup("nightwatch_checklist", "tasks") ==
                  RAttr::ChecklistItems,
              "checklist items list bindable via namespace subscription");
        CHECK(strcmp(reactive_primary_attr("checklist"), "items") == 0 &&
                  strcmp(reactive_primary_attr("status_list"), "items") == 0 &&
                  strcmp(reactive_primary_attr("nightwatch_checklist"),
                         "tasks") == 0,
              "checklist widget-level namespace: -> items/tasks");
        /* master_detail: the LEFT nav selection (section-name string) is a
         * namespace subscription (nav_namespace) — the subclass index seam
         * tab.h referenced, NOT a bindings.py binding-editor row.  Its
         * on-device apply is deferred (no NavSelection case in
         * apply_binding_value), like ChecklistItems. */
        CHECK(reactive_attr_lookup("master_detail", "selection") ==
                  RAttr::NavSelection,
              "master_detail selection bindable via nav_namespace subscription");
        CHECK(strcmp(reactive_primary_attr("master_detail"), "selection") == 0,
              "master_detail widget-level namespace: -> selection");

        /* Numeric coercion (gauge.value): the one DSL numeric rule. */
        double d = 0;
        CHECK(reactive_coerce_number(DslValue::number(72.0), d) && d == 72.0,
              "number -> gauge value");
        CHECK(reactive_coerce_number(DslValue::string("72.5", 4), d) &&
                  d == 72.5,
              "numeric string -> gauge value");
        CHECK(reactive_coerce_number(DslValue::boolean(true), d) && d == 1.0,
              "bool -> 1/0");
        CHECK(!reactive_coerce_number(DslValue::string("warp", 4), d),
              "non-numeric string WITHHELD for a numeric attr");

        /* Text formatting (label.text). */
        char buf[64];
        CHECK(reactive_format_text(DslValue::number(72.0), buf, sizeof(buf))
                  && strcmp(buf, "72") == 0,
              "number -> %g text");
        CHECK(reactive_format_text(DslValue::boolean(false), buf,
                                   sizeof(buf)) && strcmp(buf, "off") == 0,
              "bool -> canon on/off text");

        /* led.state selection (LEDState order On/Off/Warning/Danger). */
        CHECK(reactive_led_state(DslValue::string("on", 2)) == 0 &&
                  reactive_led_state(DslValue::string("off", 3)) == 1 &&
                  reactive_led_state(DslValue::string("warning", 7)) == 2 &&
                  reactive_led_state(DslValue::string("DANGER", 6)) == 3,
              "canon state strings select LED states");
        CHECK(reactive_led_state(DslValue::boolean(true)) == 0 &&
                  reactive_led_state(DslValue::number(0.0)) == 1,
              "expr bool/number results follow truthiness");

        /* Colors: bus-portable #hex only. */
        uint8_t c[4];
        CHECK(reactive_parse_color(DslValue::string("#B71C1C", 7), c) &&
                  c[0] == 0xB7 && c[1] == 0x1C && c[2] == 0x1C && c[3] == 255,
              "#RRGGBB parses (alpha 255)");
        CHECK(reactive_parse_color(DslValue::string("#00FF0080", 9), c) &&
                  c[3] == 0x80,
              "#RRGGBBAA parses alpha");
        CHECK(!reactive_parse_color(DslValue::number(255), c) &&
                  !reactive_parse_color(DslValue::string("red", 3), c),
              "non-hex color WITHHELD");

        /* Visibility: DSL truthiness (binary canon). */
        CHECK(reactive_visible(DslValue::string("on", 2)) &&
                  !reactive_visible(DslValue::string("off", 3)) &&
                  !reactive_visible(DslValue::number(0.0)) &&
                  reactive_visible(DslValue::number(2.0)),
              "visible follows dsl_truthy");

        /* Dirty bands (16-scanline bands on a 320-high panel). */
        CHECK(reactive_band_mask(40, 150, 16, 320) ==
                  (((1u << 12) - 1) & ~((1u << 2) - 1)),
              "y40 h150 -> bands 2..11");
        CHECK(reactive_band_mask(0, 16, 16, 320) == 0x1u,
              "exactly band 0");
        CHECK(reactive_band_mask(16, 16, 16, 320) == 0x2u,
              "exactly band 1 (no bleed)");
        CHECK(reactive_band_mask(-20, 30, 16, 320) == 0x1u,
              "clips negative y");
        CHECK(reactive_band_mask(316, 100, 16, 320) == (1u << 19),
              "clips past panel bottom");
        CHECK(reactive_band_mask(400, 20, 16, 320) == 0,
              "fully off-panel -> no repaint");
        CHECK(reactive_first_band(0x0C) == 2 && reactive_last_band(0x0C) == 3,
              "band range helpers");
    }

    /* ---------------------------------------------------------------
     * 16. R3 end-to-end (pure): core arrival -> sink -> coercion —
     * the exact pipeline compose_scene.cpp runs per widget attr.
     * --------------------------------------------------------------- */
    {
        ReactiveCore core;
        core.set_sink(sink, nullptr);
        CHECK(core.add_binding("b0", "demo/gauge"),
              "gauge.value object-form binding (bare ns)");
        CHECK(core.add_binding("b1", "expr: ns('demo/gauge') > 75"),
              "led.state expr binding");
        CHECK(core.subscription_count() == 1,
              "both bindings dedupe to one subscription");

        events.clear();
        core.on_bus_value("demo/gauge", CoreValue::number(72.0), 1.0);
        CHECK(events.size() == 2, "one publish fires both bindings");
        double gv = 0;
        CHECK(events[0].ns == "b0" &&
                  reactive_coerce_number(events[0].value, gv) && gv == 72.0,
              "gauge.value receives 72");
        CHECK(events[1].ns == "b1" &&
                  reactive_led_state(events[1].value) == 1,
              "HIGH led stays Off at 72");

        events.clear();
        core.on_bus_value("demo/gauge", CoreValue::number(90.0), 2.0);
        CHECK(events.size() == 2 &&
                  reactive_led_state(events[1].value) == 0,
              "90 crosses the threshold -> led On");

        events.clear();
        core.on_bus_value("demo/gauge", CoreValue::number(90.0), 3.0);
        CHECK(events.empty(),
              "unchanged value -> no re-apply, no repaint");
    }

    /* ---------------------------------------------------------------
     * 17. R5 pure geometry (cyd/src/xform2d.h) + transform attrs —
     * the framebuffer tier's live-transform math ([CXC-ER-C6]) and
     * round-panel clip, host-proven before hardware.
     * --------------------------------------------------------------- */
    {
        /* affine round trip: rotate 90 deg about (10, 10) */
        Mat2D m;
        m.translate(10, 10);
        m.rotate((float)M_PI / 2.0f);       /* screen space: +90 = CW */
        m.translate(-10, -10);
        float ox, oy;
        m.apply(20, 10, ox, oy);            /* right of pivot -> below */
        CHECK(std::fabs(ox - 10) < 1e-4 && std::fabs(oy - 20) < 1e-4,
              "rotate 90 about pivot maps (20,10)->(10,20)");
        Mat2D inv;
        CHECK(m.invert(inv), "similarity matrix inverts");
        float bx, by;
        inv.apply(ox, oy, bx, by);
        CHECK(std::fabs(bx - 20) < 1e-3 && std::fabs(by - 10) < 1e-3,
              "inverse round-trips the point");
        CHECK(std::fabs(m.scale_avg() - 1.0f) < 1e-4,
              "pure rotation has unit scale");
        m.scale(2.0f, 2.0f);
        CHECK(std::fabs(m.scale_avg() - 2.0f) < 1e-4,
              "scale(2) doubles the device scale factor");

        /* rect signed distance: inside positive, edge zero, outside neg */
        CHECK(xf_rect_sdist(50, 50, 0, 0, 100, 100) == 50,
              "rect sdist center");
        CHECK(xf_rect_sdist(0, 50, 0, 0, 100, 100) == 0,
              "rect sdist on edge");
        CHECK(xf_rect_sdist(-3, 50, 0, 0, 100, 100) == -3,
              "rect sdist outside edge");
        CHECK(std::fabs(xf_rect_sdist(-3, -4, 0, 0, 100, 100) + 5) < 1e-4,
              "rect sdist outside corner (3-4-5)");

        /* circle + round-rect fields */
        CHECK(xf_circle_sdist(10, 10, 10, 10, 5) == 5, "circle sdist center");
        CHECK(std::fabs(xf_circle_sdist(14, 10, 10, 10, 5) - 1) < 1e-4,
              "circle sdist near rim");
        CHECK(std::fabs(xf_rrect_sdist(50, 50, 0, 0, 100, 100, 10) - 50) < 1e-4,
              "rrect sdist center");
        CHECK(xf_rrect_sdist(0.5f, 0.5f, 0, 0, 100, 100, 10) < 0,
              "rrect corner pixel is OUTSIDE the rounded corner");

        /* round-panel clip spans (720x720, r=360 inscribed circle) */
        int x0, x1;
        CHECK(xf_round_span(360, 360, 360, 360, 720, x0, x1) &&
                  x0 == 0 && x1 == 719,
              "equator row spans the full panel");
        CHECK(!xf_round_span(-1, 360, 360, 360, 720, x0, x1),
              "off-panel row is empty");
        xf_round_span(0, 360, 360, 360, 720, x0, x1);
        CHECK(x0 > 300 && x1 < 420 && x0 + x1 == 719,
              "top row span is narrow and symmetric");
        xf_round_span(719, 360, 360, 360, 720, x0, x1);
        CHECK(x0 > 300 && x1 < 420 && x0 + x1 == 719,
              "bottom row span mirrors the top");

        /* transformed dirty span: bounding circle covers every angle */
        float ty, th;
        xf_transformed_vspan(100, 100, 200, 100, 1.0f, ty, th);
        float half_diag = 0.5f * std::sqrt(200.0f * 200.0f + 100.0f * 100.0f);
        CHECK(ty <= 150 - half_diag && ty + th >= 150 + half_diag,
              "vspan covers the rect's bounding circle");
        xf_transformed_vspan(100, 100, 200, 100, 2.0f, ty, th);
        CHECK(th > 2 * half_diag * 1.9f, "vspan scales with widget scale");

        /* dirty quantum on the 720-row panel: band_h=32 keeps the
         * bottom row inside the 32-bit mask (16 would overflow) */
        CHECK(reactive_band_mask(700, 20, 32, 720) == (1u << 21) | (1u << 22),
              "720-row panel bottom rows land in bands 21-22");
        CHECK(reactive_band_mask(719, 1, 32, 720) == (1u << 22),
              "last scanline is representable in the 32-bit mask");

#ifdef REACTIVE_TRANSFORM_ATTRS
        /* transform binding rows exist ONLY on transform tiers */
        CHECK(reactive_attr_lookup("gauge", "rotation") == RAttr::Rotation,
              "rotation is a universal bindable attr on this tier");
        CHECK(reactive_attr_lookup("label", "scale") == RAttr::Scale,
              "scale is a universal bindable attr on this tier");
        CHECK(reactive_attr_lookup("sweep", "speed") == RAttr::Value,
              "sweep.speed binds as a numeric value");
        CHECK(strcmp(reactive_primary_attr("sweep"), "speed") == 0,
              "sweep primary attr is speed");
#endif
    }

    /* ---------------------------------------------------------------
     * DUAL-THROTTLE S6 — the DY-SV5F audio cover (audio_cover.h ==
     * composeio.maker.audio_cover) [CXC-ER-BOARDS]
     * --------------------------------------------------------------- */
    {
        /* UART frames match the Python + the S4 mcu-config uart_frame. */
        uint8_t f[audio_cover::DYSV5F_FRAME_LEN];
        CHECK(audio_cover::dysv5f_play_track(8, f) == 0xBB, "track 8 checksum BB");
        CHECK(f[0] == 0xAA && f[1] == 0x07 && f[2] == 0x02 && f[3] == 0x00 &&
              f[4] == 0x08 && f[5] == 0xBB, "track 8 frame AA 07 02 00 08 BB");
        CHECK(audio_cover::dysv5f_play_track(9, f) == 0xBC, "track 9 checksum BC");
        CHECK(f[4] == 0x09 && f[5] == 0xBC, "track 9 frame ...09 BC");
        CHECK(audio_cover::dysv5f_play_track(0, f) == 0, "track 0 out of range");

        /* Contact identification edge: baseline-guarded unknown->hostile. */
        audio_cover::ContactIdentificationDetector cid;
        std::vector<audio_cover::Contact> frame;
        frame.push_back({"c1", "neutral"});
        CHECK(cid.update(frame) == 0, "first sight neutral: no cue (baseline)");
        frame.clear();
        frame.push_back({"c1", "hostile"});
        CHECK(cid.update(frame) == 1, "c1 neutral->hostile fires once");
        CHECK(cid.update(frame) == 0, "c1 hostile->hostile does not re-fire");
        /* a contact already hostile on FIRST sight records no cue */
        std::vector<audio_cover::Contact> f2;
        f2.push_back({"c2", "hostile"});
        CHECK(cid.update(f2) == 0, "c2 hostile on first sight: no observed reveal");

        /* Jump edge: transition INTO "jumping". */
        audio_cover::JumpDetector jd;
        CHECK(jd.update("idle") == false, "idle: no jump");
        CHECK(jd.update("spooling") == false, "spooling: no jump");
        CHECK(jd.update("jumping") == true, "->jumping fires");
        CHECK(jd.update("jumping") == false, "jumping held: no re-fire");
        CHECK(jd.update("idle") == false, "back to idle");
        CHECK(jd.update("jumping") == true, "re-jump fires again");
    }

    /* ---------------------------------------------------------------
     * DUAL-THROTTLE — controller_config lowers the REAL compose.yaml
     * (hookup: + audio_cover:) into the device model [CXC-ER-DEVICE]
     * --------------------------------------------------------------- */
    {
        CHECK(controller_parse_gpio("mcu.GPIO0") == 0, "gpio mcu.GPIO0 -> 0");
        CHECK(controller_parse_gpio("mcu.GPIO1") == 1, "gpio mcu.GPIO1 -> 1");
        CHECK(controller_parse_gpio("GPIO5") == 5, "gpio GPIO5 -> 5");
        CHECK(controller_parse_gpio("6") == 6, "gpio bare 6 -> 6");
        CHECK(controller_parse_gpio("mcu.3V3") == -1, "rail 3V3 -> not a gpio");
        CHECK(controller_parse_gpio("mcu.GND") == -1, "rail GND -> not a gpio");

        static const char* kYaml =
            "namespaces:\n"
            "  - name: throttle/left\n"
            "    state_engine: continuous_0_100\n"
            "  - name: throttle/right\n"
            "    state_engine: continuous_0_100\n"
            "hookup:\n"
            "  board: perfboard\n"
            "  parts:\n"
            "    - {id: mcu,  part: esp32-c3-supermini, at: [8, 2]}\n"
            "    - {id: potL, part: pot-10k,  at: [1, 12]}\n"
            "    - {id: potR, part: pot-10k,  at: [15, 12]}\n"
            "    - {id: dysv5f, part: dy-sv5f, at: [30, 2]}\n"
            "  wires:\n"
            "    - {from: potL.wiper, to: mcu.GPIO0, publishes: throttle/left, deadband: 3, in_min: 96, in_max: 3999}\n"
            "    - {from: potR.wiper, to: mcu.GPIO1, publishes: throttle/right, invert: true, ema: 0.25}\n"
            "    - {from: potL.vcc, to: mcu.3V3}\n"
            "    - {from: potL.gnd, to: mcu.GND}\n"
            "    - {from: mcu.GPIO5, to: \"dysv5f.RXD/IO1\"}\n"
            "    - {from: \"dysv5f.BUSY\", to: mcu.GPIO6}\n"
            "audio_cover:\n"
            "  module: dysv5f\n"
            "  uart:\n"
            "    tx_pin: GPIO5\n"
            "    busy_pin: GPIO6\n"
            "    baud: 9600\n"
            "  subscribes:\n"
            "    - dradis/contacts\n"
            "    - game/jump\n"
            "  cues:\n"
            "    - cue: condition_one\n"
            "      source: dradis/contacts\n"
            "      edge: contact_identified\n"
            "      track: 8\n"
            "    - cue: jump\n"
            "      source: game/jump\n"
            "      edge: \"value == jumping\"\n"
            "      track: 9\n";

        ControllerConfig cfg = controller_config_parse(kYaml, strlen(kYaml));
        CHECK(cfg.ok(), "real dual_throttle compose.yaml lowers cleanly");
        CHECK(cfg.sensors.size() == 2, "two ADC sensors from hookup wires");
        if (cfg.sensors.size() == 2) {
            CHECK(cfg.sensors[0].ns == "throttle/left", "sensor0 ns");
            CHECK(cfg.sensors[0].pin == 0, "sensor0 on GPIO0");
            CHECK(cfg.sensors[0].out_max == 100, "continuous_0_100 -> out_max 100");
            CHECK(cfg.sensors[0].adc_max == 4095, "12-bit adc full-scale");
            CHECK(cfg.sensors[1].ns == "throttle/right", "sensor1 ns");
            CHECK(cfg.sensors[1].pin == 1, "sensor1 on GPIO1");
            /* leaf-owned conditioning parsed off the wire (P1b): */
            CHECK(cfg.sensors[0].invert == false, "sensor0 invert defaults false");
            CHECK(cfg.sensors[0].deadband == 3, "sensor0 deadband from wire");
            CHECK(cfg.sensors[0].ema_alpha == 0.0f, "sensor0 ema defaults off");
            CHECK(cfg.sensors[1].invert == true, "sensor1 inverted (right pot)");
            CHECK(cfg.sensors[1].deadband == 2, "sensor1 deadband default 2");
            CHECK(cfg.sensors[1].ema_alpha > 0.24f && cfg.sensors[1].ema_alpha < 0.26f,
                  "sensor1 ema 0.25 from wire");
            /* Safety bands off the wire — the fixture bands sensor0 and
             * leaves sensor1 unbanded ON PURPOSE, so one parse exercises
             * BOTH the declared path and the additive default.
             * [CXC-HAL-CONDITION-BANDS] */
            CHECK(cfg.sensors[0].in_min == 96,   "sensor0 in_min from wire");
            CHECK(cfg.sensors[0].in_max == 3999, "sensor0 in_max from wire");
            CHECK(cfg.sensors[1].in_min == 0,    "sensor1 in_min defaults 0 (no band)");
            CHECK(cfg.sensors[1].in_max == 0,    "sensor1 in_max defaults 0 (no band)");
        }
        CHECK(cfg.audio.present, "audio cover present");
        CHECK(cfg.audio.tx_pin == 5, "DY-SV5F UART TX GPIO5");
        CHECK(cfg.audio.busy_pin == 6, "DY-SV5F BUSY GPIO6");
        CHECK(cfg.audio.baud == 9600, "DY-SV5F 9600 baud");
        CHECK(cfg.audio.subscribes.size() == 2, "two subscribed namespaces");
        CHECK(cfg.audio.contacts_ns == "dradis/contacts", "condition_one source");
        CHECK(cfg.audio.condition_one_track == 8, "condition_one -> track 8");
        CHECK(cfg.audio.jump_ns == "game/jump", "jump source");
        CHECK(cfg.audio.jump_track == 9, "jump -> track 9");
    }

    /* ===============================================================
     * OFFICE RELAY FAN — the break-before-make relay-bank actuator
     * ([CXC-HAL-ACTUATE] Output Tier 1, relay_actuator.{h,cpp})
     * =============================================================== */
    {
        /* office_fan speed-tap map: off=all open, low=33, medium=25, high=26;
         * RY1=32 is the owned SPARE (break must still open it).  active-HIGH,
         * 80ms dead-time. */
        RelayActuatorSpec spec;
        spec.present = true;
        spec.ns = "office_fan/speed";
        spec.pins = {32, 33, 25, 26};
        spec.active_high = true;
        spec.dead_time_ms = 80;
        spec.default_value = "off";
        spec.states = {
            {"off", -1}, {"low", 33}, {"medium", 25}, {"high", 26},
        };

        RelayActuator ra;
        ra.set_sink(relay_sink, nullptr);

        /* configure() = SAFE BOOT BASELINE: every owned pin de-energized. */
        relays.clear();
        ra.configure(&spec);
        CHECK(relays.size() == 4, "configure drives all 4 owned pins");
        CHECK(relay_last(32) == 0 && relay_last(33) == 0 &&
              relay_last(25) == 0 && relay_last(26) == 0,
              "boot baseline: every relay OPEN (LOW, active-high)");
        CHECK(strcmp(ra.current(), "") == 0, "no value applied yet");

        /* off -> low: BREAK all 4 now, MAKE GPIO33 only AFTER the dead-time */
        relays.clear();
        int fired = ra.on_value("low", 1000);
        CHECK(fired == 4, "low: break de-energizes all 4 pins first");
        CHECK(relay_highs(33) == 0, "MAKE is withheld during the dead-time");
        CHECK(ra.sequencing(), "make armed (dead-time running)");
        CHECK(ra.tick(1000 + 79) == 0, "no make before dead_time_ms");
        CHECK(!relays.empty() ? relay_highs(33) == 0 : true,
              "still no GPIO33 energize at t+79ms");
        int made = ra.tick(1000 + 80);
        CHECK(made == 1 && relay_last(33) == 1,
              "make fires at t+80ms: GPIO33 energized");
        CHECK(!ra.sequencing(), "sequence complete");
        /* the invariant: during the whole low transition NO OTHER pin ever
         * went HIGH — only 33 is energized, 32/25/26 stayed open. */
        CHECK(relay_highs(32) == 0 && relay_highs(25) == 0 &&
              relay_highs(26) == 0, "no two relays ever closed (break-first)");

        /* low -> high: break all (incl. the currently-closed 33), make 26 */
        relays.clear();
        ra.on_value("high", 2000);
        CHECK(relay_last(33) == 0, "high: previously-closed GPIO33 OPENED first");
        CHECK(relay_highs(26) == 0, "GPIO26 make withheld during dead-time");
        ra.tick(2000 + 80);
        CHECK(relay_last(26) == 1, "high make: GPIO26 energized after dead-time");
        CHECK(relay_last(33) == 0, "GPIO33 stays open (break-before-make held)");

        /* on-change: re-commanding the SAME state is a no-op (no thrash). */
        relays.clear();
        CHECK(ra.on_value("high", 3000) == 0, "same state suppressed");
        CHECK(relays.empty(), "no relay drive on an unchanged command");

        /* high -> off: break all, NO make (off is pin-less). */
        relays.clear();
        ra.on_value("off", 4000);
        CHECK(relay_last(26) == 0, "off: GPIO26 opened");
        CHECK(!ra.sequencing(), "off arms no make");
        CHECK(ra.tick(4000 + 80) == 0, "off: nothing to energize");

        /* unknown enum value: WITHHELD, relays untouched (motor-safe). */
        ra.on_value("low", 5000); ra.tick(5000 + 80);
        relays.clear();
        int bad = ra.on_value("turbo", 6000);
        CHECK(bad == 0 && relays.empty(),
              "unknown speed 'turbo' withheld — relays untouched");
        CHECK(*ra.last_error() != '\0', "withheld unknown value is reported");
        CHECK(strcmp(ra.current(), "low") == 0,
              "state unchanged after a bad command");

        /* rapid re-command mid-dead-time re-breaks + re-arms (still safe). */
        relays.clear();
        ra.on_value("off", 7000);
        ra.on_value("medium", 7010);           /* before any make */
        ra.on_value("high", 7020);             /* again, still safe */
        CHECK(relay_highs(25) == 0 && relay_highs(26) == 0,
              "no make while re-commanding inside the dead-time");
        ra.tick(7020 + 80);
        CHECK(relay_last(26) == 1 && relay_last(25) == 0,
              "only the FINAL target (high/GPIO26) makes");
    }

    /* ---------------------------------------------------------------
     * OFFICE RELAY FAN — controller_config lowers the real relay
     * .compose.yaml (relay_actuator: block) [CXC-ER-DEVICE]
     * --------------------------------------------------------------- */
    {
        static const char* kYaml =
            "app:\n"
            "  id: office_fan\n"
            "namespaces:\n"
            "  - name: office_fan/speed\n"
            "    state_engine: enum\n"
            "relay_actuator:\n"
            "  subscribes: office_fan/speed\n"
            "  active_high: true\n"
            "  dead_time_ms: 80\n"
            "  default: off\n"
            "  pins: [mcu.GPIO32, mcu.GPIO33, mcu.GPIO25, mcu.GPIO26]\n"
            "  states:\n"
            "    - {value: off,    pin: none}\n"
            "    - {value: low,    pin: mcu.GPIO33}\n"
            "    - {value: medium, pin: mcu.GPIO25}\n"
            "    - {value: high,   pin: mcu.GPIO26}\n";

        ControllerConfig cfg = controller_config_parse(kYaml, strlen(kYaml));
        CHECK(cfg.ok(), "office_fan relay compose.yaml lowers cleanly");
        CHECK(cfg.relay.present, "relay actuator present");
        CHECK(cfg.relay.ns == "office_fan/speed", "subscribes the speed enum");
        CHECK(cfg.relay.active_high, "active-high energize");
        CHECK(cfg.relay.dead_time_ms == 80, "80ms break-before-make dead-time");
        CHECK(cfg.relay.default_value == "off", "boots OFF");
        CHECK(cfg.relay.pins.size() == 4, "owns 4 relay pins (incl. spare RY1)");
        CHECK(cfg.relay.pins[0] == 32 && cfg.relay.pins[1] == 33 &&
              cfg.relay.pins[2] == 25 && cfg.relay.pins[3] == 26,
              "pins RY1=32 RY2=33 RY3=25 RY4=26");
        CHECK(cfg.relay.states.size() == 4, "four state rows");
        CHECK(cfg.relay.states[0].value == "off" &&
              cfg.relay.states[0].pin == -1, "off -> all open (pin -1)");
        CHECK(cfg.relay.states[1].pin == 33, "low -> GPIO33");
        CHECK(cfg.relay.states[2].pin == 25, "medium -> GPIO25");
        CHECK(cfg.relay.states[3].pin == 26, "high -> GPIO26");

        /* a state energizing a pin NOT in pins: is a hard config error */
        static const char* kBad =
            "relay_actuator:\n"
            "  subscribes: x/speed\n"
            "  pins: [GPIO33]\n"
            "  states:\n"
            "    - {value: low, pin: GPIO33}\n"
            "    - {value: high, pin: GPIO26}\n";   /* 26 not owned */
        ControllerConfig bad = controller_config_parse(kBad, strlen(kBad));
        CHECK(!bad.ok(), "state pin outside pins: is rejected");
    }

    /* ---------------------------------------------------------------
     * REMOTE CONFIG-RELOAD — the `_device/<id>/restart` handler wired
     * into the controller shell ([CXC-ER-REMOTE-RESTART], restart.{h,cpp})
     * --------------------------------------------------------------- */
    {
        restart_reset();
        reboot_calls = 0;
        restart_pubs.clear();
        restart_init("office_fan", test_restart_pub, test_reboot,
                     /*boot_window_ms*/ 3000);

        const char* rns[4];
        size_t rn = restart_namespaces(rns, 4);
        CHECK(rn == 2, "two exact reload namespaces");
        CHECK(strcmp(rns[0], "_device/office_fan/restart") == 0,
              "per-device reload namespace");
        CHECK(strcmp(rns[1], "_device/restart") == 0, "broadcast namespace");
        CHECK(restart_is_restart_ns("_device/office_fan/restart"),
              "recognizes its per-device reload ns");
        CHECK(!restart_is_restart_ns("office_fan/speed"),
              "the speed enum is NOT a control ns");

        /* boot-window guard: a truthy command inside 3s is DROPPED (no loop) */
        bool consumed = restart_on_bus_value("_device/office_fan/restart",
                                             1, 0, true, "", /*now*/ 1000);
        CHECK(consumed && reboot_calls == 0,
              "truthy reload inside boot window is a no-op (loop guard)");

        /* falsy command past the window: consumed but no reboot */
        consumed = restart_on_bus_value("_device/office_fan/restart",
                                        1, 0, false, "", 5000);
        CHECK(consumed && reboot_calls == 0, "falsy reload does not reboot");

        /* truthy command past the window: publishes ack + reboots ONCE */
        consumed = restart_on_bus_value("_device/office_fan/restart",
                                        1, 0, true, "", 5000);
        CHECK(consumed && reboot_calls == 1, "truthy reload past window reboots");
        CHECK(restart_pubs.size() == 1 &&
              restart_pubs[0].first == "_device/office_fan/status" &&
              restart_pubs[0].second == "restarting",
              "pre-reload ack published");

        /* latched: a second command does not double-reboot this boot */
        restart_on_bus_value("_device/restart", 1, 0, true, "", 6000);
        CHECK(reboot_calls == 1, "restart latched — no double reboot");
        restart_reset();
    }

    /* ---------------------------------------------------------------
     * List-payload reactive apply ([CXC-RM-M5], [CXC-ER-RENDER]):
     * a retained MQTT list payload updates the widget's item vector.
     * These exercise the SAME setters compose_scene.cpp's
     * apply_binding_value calls for RAttr::ChecklistItems /
     * RAttr::NavSelection, asserting parity with the python apply
     * (nightwatch_checklist.py _coerce / master_detail.py _index_for).
     * --------------------------------------------------------------- */
    {
        using CS = ChecklistWidget::Status;

        /* --- checklist: JSON array of {label|name, status} ---------- */
        ChecklistWidget cl("cl");
        CHECK(cl.set_items_json(
                  "[{\"label\":\"Fuel check\",\"status\":\"done\"},"
                  "{\"label\":\"Nav align\",\"status\":\"partial\"},"
                  "{\"name\":\"Comms sweep\",\"status\":\"deferred\"}]"),
              "checklist accepts a JSON array payload");
        CHECK(cl.row_count() == 3, "checklist: 3 rows populated from payload");
        CHECK(strcmp(cl.row_label(0), "Fuel check") == 0, "checklist row0 label");
        CHECK(cl.row_status(0) == CS::Done, "checklist row0 done");
        CHECK(cl.row_status(1) == CS::Partial, "checklist row1 partial");
        CHECK(strcmp(cl.row_label(2), "Comms sweep") == 0,
              "checklist name-fallback label (python: label or name)");
        CHECK(cl.row_status(2) == CS::Deferred, "checklist row2 deferred");

        /* {"tasks":[...]} wrapper form (python: dict -> value['tasks']). */
        CHECK(cl.set_items_json(
                  "{\"tasks\":[{\"label\":\"only one\",\"status\":\"done\"}]}"),
              "checklist accepts {tasks:[...]} wrapper");
        CHECK(cl.row_count() == 1 && cl.row_status(0) == CS::Done,
              "checklist tasks-wrapper replaced the list");

        /* bare-string elements -> deferred (python _coerce parity). */
        CHECK(cl.set_items_json("[\"alpha\", \"bravo\"]"),
              "checklist accepts bare-string elements");
        CHECK(cl.row_count() == 2 &&
                  strcmp(cl.row_label(0), "alpha") == 0 &&
                  cl.row_status(0) == CS::Deferred,
              "checklist bare string -> (label, deferred)");

        /* unknown status -> deferred; a label-less object is dropped. */
        CHECK(cl.set_items_json(
                  "[{\"label\":\"x\",\"status\":\"weird\"},"
                  "{\"status\":\"done\"}]"),
              "checklist unknown-status / label-less payload");
        CHECK(cl.row_count() == 1 && cl.row_status(0) == CS::Deferred,
              "checklist unknown status -> deferred; no-label row dropped");

        /* whitespace in fields is stripped (python .strip()). */
        CHECK(cl.set_items_json("[{\"label\":\"  spaced  \",\"status\":\" done \"}]"),
              "checklist accepts padded fields");
        CHECK(cl.row_count() == 1 && strcmp(cl.row_label(0), "spaced") == 0 &&
                  cl.row_status(0) == CS::Done,
              "checklist trims label + status (python .strip())");

        /* empty array is a valid list -> clears to empty. */
        CHECK(cl.set_items_json("[]"), "checklist empty array is valid");
        CHECK(cl.row_count() == 0, "checklist empty array clears the list");

        /* a non-list payload keeps the last good list (python _coerce->None). */
        cl.set_items_json("[{\"label\":\"keep me\",\"status\":\"done\"}]");
        CHECK(!cl.set_items_json("not json at all"),
              "checklist refuses a non-JSON payload (no replace)");
        CHECK(cl.row_count() == 1 && strcmp(cl.row_label(0), "keep me") == 0,
              "checklist keeps prior list on a bad payload");

        /* memory bound: a payload longer than kMaxRows drops the overflow
         * (band-tier cap, no dynamic allocation). */
        std::string big = "[";
        for (int i = 0; i < ChecklistWidget::kMaxRows + 20; i++) {
            if (i) big += ",";
            big += "{\"label\":\"t\",\"status\":\"done\"}";
        }
        big += "]";
        CHECK(cl.set_items_json(big.c_str()), "checklist accepts oversized payload");
        CHECK(cl.row_count() == ChecklistWidget::kMaxRows,
              "checklist caps rows at kMaxRows (overflow dropped)");

        /* --- master_detail: name / index selection ------------------ */
        MasterDetailWidget md("md");
        md.add_section("Overview", "o");
        md.add_section("Engineering", "e");
        md.add_section("Comms", "c");
        CHECK(md.index_for("engineering") == 1,
              "md: name match (python lowercases the label)");
        CHECK(md.index_for("ENGINEERING") == 1,
              "md: name match is case-insensitive");
        CHECK(md.index_for("  comms  ") == 2, "md: name match trims the value");
        CHECK(md.index_for("missing") == -1, "md: unmatched name -> -1 (withhold)");
        CHECK(md.index_for("comm") == -1, "md: partial name is not a match");
        /* the apply drives set_active from the resolved index. */
        md.set_active(md.index_for("comms"));
        CHECK(md.active() == 2, "md: selection applied by section name");
        md.set_active(md.index_for("overview"));
        CHECK(md.active() == 0, "md: re-selection by name");
    }

    /* ================================================================
     * SIGNAL CONDITIONING — [CXC-HAL-CONDITION-RAILS]
     * "A value-conditioning pipeline must be able to emit BOTH RAILS,
     *  for every combination of invert / deadband, and a quiet-bus
     *  optimization must never make a rail unreachable."
     *
     * ⛔ This is a SHARED-PATH test on purpose.  The defect that forced
     * it (right throttle latched at 254 while left reached 255) was
     * invisible to every per-channel check, because a per-channel test
     * asks "is THIS channel right?" and the fault was in arithmetic
     * BOTH channels share.  The tests below parametrize over the
     * conditioning inputs themselves, not over a device.
     * ================================================================ */
    {
        printf("--- [CXC-HAL-CONDITION-RAILS] signal conditioning ---\n");

        auto mk = [](bool invert, int deadband, float ema) {
            SensorSpec s;
            s.ns = "throttle/test";
            s.pin = 0;
            s.adc_max = 4095;
            s.out_max = 255;
            s.invert = invert;
            s.deadband = deadband;
            s.ema_alpha = ema;
            return s;
        };

        /* ---- 1. THE CLASS DEFECT, stated exactly ------------------
         * Inversion is a MIRROR.  If the scale is a faithful mirror then
         * f(x) + f(full - x) == out_max for EVERY input code -- that is
         * precisely the statement "the arithmetic introduces no invert
         * asymmetry".  Truncation violates it; rounding satisfies it.
         * Exhaustive over all 4096 codes: no sampling, no luck. */
        {
            SensorSpec up = mk(false, 1, 0.0f);
            int asym = 0, first_bad = -1;
            for (int x = 0; x <= 4095; x++) {
                ConditionState a, b;
                int lo = condition_sample(up, x, a).value;
                int hi = condition_sample(up, 4095 - x, b).value;
                if (lo + hi != 255) {
                    asym++;
                    if (first_bad < 0) first_bad = x;
                }
            }
            if (asym)
                printf("   mirror asymmetry: %d/4096 codes (first at raw=%d)\n",
                       asym, first_bad);
            CHECK(asym == 0,
                  "scale is an exact mirror: f(x)+f(full-x)==out_max for all x");
        }

        /* ---- 2. RAIL REACHABILITY over (invert x deadband x rail) --
         * A plausible ADC trajectory: the real ESP32-C3 ADC hard-SATURATES
         * at full scale but has a nonzero FLOOR near 0 (it does not reach
         * code 0 at 0 V).  That asymmetry is the hardware half of the
         * defect -- an uninverted channel reaches its top rail via
         * saturation, an inverted one must reach it through the floor. */
        auto adc_at = [](double p, int floor_offset) {
            int a = (int)(p * 4095.0 + 0.5) + floor_offset;
            if (a > 4095) a = 4095;      /* hard saturation at the top */
            if (a < 0) a = 0;
            return a;
        };

        /* Sweep the lever end-to-end and back; collect what was EMITTED. */
        auto rails_emitted = [&](const SensorSpec& s, int floor_offset,
                                 bool* saw_lo, bool* saw_hi) {
            ConditionState st;
            *saw_lo = *saw_hi = false;
            for (int pass = 0; pass < 2; pass++) {
                for (int k = 0; k <= 400; k++) {
                    double p = pass ? 1.0 - k / 400.0 : k / 400.0;
                    ConditionResult r = condition_sample(s, adc_at(p, floor_offset), st);
                    if (!r.emit) continue;
                    if (r.value == 0) *saw_lo = true;
                    if (r.value == s.out_max) *saw_hi = true;
                }
            }
        };

        const int kFloor = 6;   /* codes of ADC zero-offset, ~0.15% FS */
        for (int inv = 0; inv <= 1; inv++) {
            for (int db = 1; db <= 4; db++) {
                SensorSpec s = mk(inv != 0, db, 0.0f);
                bool lo = false, hi = false;
                rails_emitted(s, kFloor, &lo, &hi);
                char m[128];
                snprintf(m, sizeof(m),
                         "invert=%d deadband=%d: emits BOTTOM rail (0)", inv, db);
                CHECK(lo, m);
                snprintf(m, sizeof(m),
                         "invert=%d deadband=%d: emits TOP rail (out_max)", inv, db);
                CHECK(hi, m);
            }
        }

        /* Same, with the EMA denoiser engaged -- smoothing must not
         * strand a rail either (it approaches asymptotically, so the
         * sweep dwells at each end). */
        for (int inv = 0; inv <= 1; inv++) {
            SensorSpec s = mk(inv != 0, 2, 0.30f);
            ConditionState st;
            bool lo = false, hi = false;
            for (int pass = 0; pass < 2; pass++) {
                for (int k = 0; k <= 400; k++) {
                    double p = pass ? 1.0 - k / 400.0 : k / 400.0;
                    ConditionResult r = condition_sample(s, adc_at(p, kFloor), st);
                    if (r.emit && r.value == 0) lo = true;
                    if (r.emit && r.value == s.out_max) hi = true;
                }
                /* dwell at the end of travel, as a held lever does */
                for (int k = 0; k < 60; k++) {
                    ConditionResult r = condition_sample(s, adc_at(pass ? 0.0 : 1.0, kFloor), st);
                    if (r.emit && r.value == 0) lo = true;
                    if (r.emit && r.value == s.out_max) hi = true;
                }
            }
            char m[128];
            snprintf(m, sizeof(m), "invert=%d ema=0.30: emits BOTTOM rail", inv);
            CHECK(lo, m);
            snprintf(m, sizeof(m), "invert=%d ema=0.30: emits TOP rail", inv);
            CHECK(hi, m);
        }

        /* ---- 3. THE DEADBAND MUST NOT STRAND A RAIL ----------------
         * The observed absorbing state: latched at 254 with deadband=2,
         * a computed 255 is delta 1 and was suppressed FOREVER. */
        {
            SensorSpec s = mk(false, 2, 0.0f);
            ConditionState st;
            st.have_last = true;
            st.last_val = 254;
            ConditionResult r = condition_sample(s, 4095, st);
            CHECK(r.value == 255, "raw==adc_max computes the top rail");
            CHECK(r.emit, "deadband does NOT strand the top rail at 254 (rail escape)");

            /* ...and the mirror case at the bottom. */
            ConditionState st2;
            st2.have_last = true;
            st2.last_val = 1;
            ConditionResult r2 = condition_sample(s, 0, st2);
            CHECK(r2.value == 0, "raw==0 computes the bottom rail");
            CHECK(r2.emit, "deadband does NOT strand the bottom rail at 1");
        }

        /* ---- 3b. LEAVING A RAIL ESCAPES THE DEADBAND TOO -----------
         * [CXC-HAL-CONDITION] rule-rail-exit-escapes-too.  The escape is
         * BIDIRECTIONAL.  Section 3 above tests only the ARRIVING edge, and
         * an `at_rail` predicate over the NEW value alone passes all of it
         * while leaving the exact MIRROR defect on the LEAVING edge: latched
         * AT 255 with deadband=2, a computed 254 is delta 1 and is suppressed
         * FOREVER, so a lever that has left the stop keeps reporting 255.
         * That is the more dangerous half -- the original defect under-
         * reported a control at maximum; this one OVER-reports a control that
         * is no longer there.  Symptom on the glass: the top segment of a
         * 10-segment led_bar (which lights ONLY at frac >= 1.0, i.e. exactly
         * out_max) stays lit for ~1.6 s after release, and forever if the
         * lever parks one code down. */
        {
            /* raw code that, AFTER the pipeline's own invert, conditions to
             * `want`.  condition_sample() does `raw = full - raw` when
             * inverted, so we pre-mirror here. */
            auto raw_for = [](bool invert, int post_invert_raw) {
                return invert ? 4095 - post_invert_raw : post_invert_raw;
            };
            for (int inv = 0; inv <= 1; inv++) {
                for (int db = 2; db <= 5; db++) {
                    SensorSpec s = mk(inv != 0, db, 0.0f);
                    char m[144];

                    /* leaving the TOP rail: latched at 255, lever eases to ~254 */
                    ConditionState st;
                    st.have_last = true;
                    st.last_val = 255;
                    ConditionResult r = condition_sample(s, raw_for(inv != 0, 4080), st);
                    snprintf(m, sizeof(m),
                             "invert=%d deadband=%d: leaving the TOP rail is EMITTED "
                             "(not stranded at out_max)", inv, db);
                    CHECK(r.value < 255 && r.emit, m);

                    /* leaving the BOTTOM rail: latched at 0, lever eases to ~1 */
                    ConditionState st2;
                    st2.have_last = true;
                    st2.last_val = 0;
                    ConditionResult r2 = condition_sample(s, raw_for(inv != 0, 16), st2);
                    snprintf(m, sizeof(m),
                             "invert=%d deadband=%d: leaving the BOTTOM rail is EMITTED "
                             "(not stranded at 0)", inv, db);
                    CHECK(r2.value > 0 && r2.emit, m);
                }
            }
        }

        /* ---- 3c. THE ABSORBING STATE, STATED AS THE OPERATOR SEES IT ----
         * Park the lever ONE CODE below the stop after touching it.  Under
         * the one-directional escape the emitted value never leaves 255. */
        {
            SensorSpec s = mk(false, 2, 0.0f);
            ConditionState st;
            for (int k = 0; k < 20; k++) condition_sample(s, 4095, st);  /* at the stop */
            int last_emitted = st.last_val;
            for (int k = 0; k < 400; k++) condition_sample(s, 4080, st); /* -> val 254 */
            CHECK(last_emitted == 255, "precondition: the stop was reported as 255");
            CHECK(st.last_val == 254,
                  "parked one code below the stop for 20 s: the emitted value "
                  "FOLLOWS (no absorbing state at the top rail)");
        }

        /* ---- 4. THE RAIL ESCAPE MUST NOT REINTRODUCE CHATTER -------
         * Holding AT a rail must publish once, not every sample. */
        {
            SensorSpec s = mk(false, 2, 0.0f);
            ConditionState st;
            int emits = 0;
            for (int k = 0; k < 50; k++)
                if (condition_sample(s, 4095, st).emit) emits++;
            CHECK(emits == 1, "holding at a rail emits ONCE, not per-sample");
        }

        /* ---- 5. BOUNDARY REPORT (an instrument that reports a limit,
         * not merely success): how much ADC zero-offset can each channel
         * absorb and still reach its top rail?  This is the residual the
         * arithmetic CANNOT fix -- past this, only calibration can. */
        for (int inv = 0; inv <= 1; inv++) {
            SensorSpec s = mk(inv != 0, 2, 0.0f);
            int max_ok = -1;
            for (int off = 0; off <= 64; off++) {
                bool lo = false, hi = false;
                rails_emitted(s, off, &lo, &hi);
                if (lo && hi) max_ok = off; else break;
            }
            printf("   invert=%d: top rail survives ADC zero-offset up to %d codes\n",
                   inv, max_ok);
        }

        /* ================================================================
         * 6. SAFETY BANDS — [CXC-HAL-CONDITION-BANDS]
         *
         * Section 5 above MEASURES the residual: ~8 codes of ADC zero-
         * offset and the far rail becomes unreachable.  That is not a
         * rounding bug -- it is the preimage of the rail being a code the
         * ADC never emits.  No arithmetic can fix it, so the fix is
         * STRUCTURAL: give each rail a whole BAND of raw codes.
         *
         * The vocabulary is the OUTPUT path's, unchanged: in_min/in_max,
         * the same clamped linear map actuator_hal.cpp `_map_scaled` runs
         * (and the same map mcu_config.py already CLAIMED both paths ran).
         *
         * David's ruling, 2026-08-15: "0% thrust should not just be 0 on
         * the 255 pot readout scale, but a small safety band, say 0-5, and
         * the same on the high end, 250-255 should be 100%".  6 codes of
         * 256 == 2.34% of full scale; on the 12-bit ADC that is 96 codes,
         * which is TWELVE TIMES the measured 8-code residual.
         * ================================================================ */
        printf("--- [CXC-HAL-CONDITION-BANDS] safety bands ---\n");

        /* The percent engine (continuous_0_100) with David's bands, stated
         * in the ONLY units a band can be invariant in: RAW ADC CODES.
         * (Expressed on the OUTPUT scale a band would silently change width
         * when out_max changes -- which is exactly the migration we are
         * doing, so it would have changed under us.) */
        const int kBandLo = 96;      /*  5.5/255 of full scale, rounded */
        const int kBandHi = 4095 - 96;

        auto mkband = [&](bool invert, int deadband, int out_max,
                          int in_min, int in_max) {
            SensorSpec s;
            s.ns = "throttle/test";
            s.pin = 0;
            s.adc_max = 4095;
            s.out_max = out_max;
            s.invert = invert;
            s.deadband = deadband;
            s.ema_alpha = 0.0f;
            s.in_min = in_min;
            s.in_max = in_max;
            return s;
        };

        /* ---- 6a. THE BAND SATURATES, EXHAUSTIVELY ------------------
         * Every code at or below in_min is 0; every code at or above
         * in_max is out_max.  Not "usually" -- every one of them. */
        {
            SensorSpec s = mkband(false, 1, 100, kBandLo, kBandHi);
            int bad_lo = 0, bad_hi = 0;
            for (int x = 0; x <= kBandLo; x++) {
                ConditionState st;
                if (condition_sample(s, x, st).value != 0) bad_lo++;
            }
            for (int x = kBandHi; x <= 4095; x++) {
                ConditionState st;
                if (condition_sample(s, x, st).value != 100) bad_hi++;
            }
            if (bad_lo || bad_hi)
                printf("   band saturation: %d/%d low codes and %d/%d high "
                       "codes did NOT pin to a rail\n",
                       bad_lo, kBandLo + 1, bad_hi, 4096 - kBandHi);
            CHECK(bad_lo == 0, "every raw code <= in_min maps to 0");
            CHECK(bad_hi == 0, "every raw code >= in_max maps to out_max");
        }

        /* ---- 6b. THE RESIDUAL IS CLOSED ---------------------------
         * THE TEST THAT MATTERS.  Sweep the lever with an ADC zero-
         * offset FAR past the 8-code cliff section 5 measured, on BOTH
         * polarities and every deadband, and demand both rails.  This is
         * the failure David is actually fixing: a physically-full lever
         * that the bus never reports as full. */
        for (int inv = 0; inv <= 1; inv++) {
            for (int db = 1; db <= 4; db++) {
                SensorSpec s = mkband(inv != 0, db, 100, kBandLo, kBandHi);
                bool lo = false, hi = false;
                rails_emitted(s, 40, &lo, &hi);   /* 5x the residual */
                char m[160];
                snprintf(m, sizeof(m),
                         "banded invert=%d db=%d: BOTTOM rail survives a "
                         "40-code ADC offset", inv, db);
                CHECK(lo, m);
                snprintf(m, sizeof(m),
                         "banded invert=%d db=%d: TOP rail survives a "
                         "40-code ADC offset", inv, db);
                CHECK(hi, m);
            }
        }

        /* ---- 6c. SYMMETRIC BANDS PRESERVE THE MIRROR ---------------
         * Section 1's invariant must survive the new arithmetic: with
         * bands placed symmetrically the scale is still an exact mirror,
         * so inversion introduces no asymmetry.  Exhaustive again. */
        {
            SensorSpec s = mkband(false, 1, 100, kBandLo, kBandHi);
            int asym = 0, first_bad = -1;
            for (int x = 0; x <= 4095; x++) {
                ConditionState a, b;
                int lo = condition_sample(s, x, a).value;
                int hi = condition_sample(s, 4095 - x, b).value;
                if (lo + hi != 100) {
                    asym++;
                    if (first_bad < 0) first_bad = x;
                }
            }
            if (asym)
                printf("   banded mirror asymmetry: %d/4096 codes "
                       "(first at raw=%d)\n", asym, first_bad);
            CHECK(asym == 0,
                  "banded scale is still an exact mirror: f(x)+f(full-x)==out_max");
        }

        /* ---- 6d. UNSET BANDS ARE BYTE-IDENTICAL TO TODAY -----------
         * in_min=0 / in_max=0 must mean "no band", not "a degenerate
         * band".  Every already-deployed wire declares neither, so this
         * is the check that the change is additive on the wire.  The
         * negative control lives here too: a band-less spec must NOT
         * survive the 40-code offset, or 6b proves nothing. */
        {
            int diffs = 0;
            for (int x = 0; x <= 4095; x++) {
                SensorSpec bare = mk(false, 1, 0.0f);          /* pre-change */
                SensorSpec unset = mkband(false, 1, 255, 0, 0);
                ConditionState a, b;
                if (condition_sample(bare, x, a).value !=
                    condition_sample(unset, x, b).value) diffs++;
            }
            CHECK(diffs == 0, "in_min=0,in_max=0 is EXACTLY the pre-band map");

            SensorSpec s = mkband(true, 2, 100, 0, 0);
            bool lo = false, hi = false;
            rails_emitted(s, 40, &lo, &hi);
            CHECK(!hi, "NEGATIVE CONTROL: WITHOUT a band, a 40-code offset "
                       "DOES strand the top rail");
        }

        /* ---- 6e. BOUNDARY REPORT, BANDED --------------------------
         * The same instrument as section 5, now with the band in place.
         * An instrument that can only report success is not an
         * instrument: this one still reports where it gives up. */
        for (int inv = 0; inv <= 1; inv++) {
            SensorSpec s = mkband(inv != 0, 2, 100, kBandLo, kBandHi);
            int max_ok = -1;
            for (int off = 0; off <= 256; off++) {
                bool lo = false, hi = false;
                rails_emitted(s, off, &lo, &hi);
                if (lo && hi) max_ok = off; else break;
            }
            printf("   invert=%d BANDED(%d..%d): rails survive ADC zero-offset "
                   "up to %d codes\n", inv, kBandLo, kBandHi, max_ok);
        }
    }

    /* ===============================================================
     * FINGERPRINT PARITY — R503 device half, mock sensor (C71/C72/C73)
     * =============================================================== */
    fingerprint_parity_tests();

    /* ===============================================================
     * R503 WIRE PROTOCOL + RealR503HAL — the REAL backend under the
     * same [SPEC:CXC-FPHAL] contract the mock satisfies.  Defined in
     * src/hosttest/r503_protocol_tests.cpp (its own counters, folded in
     * here) so the multi-packet DownChar/UpChar suite does not swell
     * this file.  It carries its own negative controls.
     * =============================================================== */
    {
        int r503_checks = 0;
        const int r503_failures = r503_protocol_tests(&r503_checks);
        checks += r503_checks;
        failures += r503_failures;
    }

    if (failures == 0)
        printf("ALL PASS (%d checks)\n", checks);
    else
        printf("%d/%d checks FAILED\n", failures, checks);
    return failures ? 1 : 0;
}
