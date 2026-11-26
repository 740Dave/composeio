/*
 * production_binding_tests.cpp — the D9 STRUCTURAL GUARD, proven on the host.
 * [SPEC:CXC-FPHAL] [DEC-R503-MOCK-STRUCTURAL-GUARD] (David 2026-08-24)
 * [CXC-FPHAL-MATCH-USES-ENROLL-SENSOR]  closes
 * DEFECT-ARMED-ROSTER-LOADED-R503-DEVICE-78C389.
 *
 * Compiled ONLY in env:native_prod_binding, which DELIBERATELY does NOT define
 * FP_ALLOW_MOCK and does NOT link mock_r503_hal.cpp — the exact production
 * binding config.  This is the falsifier for the guard: it must be possible to
 * build a device whose no-sensor state is INOPERABLE and whose ONLY compiled
 * default HAL cannot grant, WITHOUT the functional mock being reachable.
 *
 * Two proofs, one compile-time and one runtime:
 *   1. static_assert(!fp_build_allows_mock_hal()) — the SAME macro that guards
 *      g_hal in fingerprint_bringup_main.cpp is false here, so a regression
 *      that re-enabled the mock in a production build fails to COMPILE.  A
 *      NEGATIVE CONTROL, not a success-only check: were the mock reachable,
 *      this file would not build.
 *   2. A FingerprintDevice bound to NullFingerprintHAL is INOPERABLE and can
 *      NEVER produce a granted match — not in the production not-confirmed-live
 *      state, and not even if a careless caller wrongly marks it confirmed.
 *
 * PASS looks like: every CHECK silent, "ALL PASS (N checks)", exit 0.
 */
#include "fingerprint_null_hal.h"
#include "fingerprint_device.h"

#include <cstdio>
#include <string>
#include <vector>

/* ⛔ COMPILE-TIME PROOF: this translation unit is a production build, so the
 * functional mock is not a candidate device HAL.  If FP_ALLOW_MOCK ever leaked
 * into this env, THIS LINE fails to compile — the guard cannot silently rot. */
static_assert(!fp_build_allows_mock_hal(),
              "native_prod_binding MUST NOT define FP_ALLOW_MOCK: it is the "
              "production-binding config that proves the mock is unreachable "
              "[DEC-R503-MOCK-STRUCTURAL-GUARD]");

static int checks = 0;
static int failures = 0;
#define CHECK(cond, msg) do { \
    checks++; \
    if (!(cond)) { failures++; printf("FAIL %s (line %d)\n", msg, __LINE__); } \
} while (0)

/* Minimal recording bus (mirror of host_test_main.cpp's FpRecordingBus). */
struct RecEvent { std::string ns, event_type; FpData data; std::string source; };
class RecordingBus final : public FingerprintBusSink {
public:
    std::vector<RecEvent> events;
    void publish_event(const std::string& ns, const std::string& event_type,
                       const FpData& data, const std::string& source) override {
        events.push_back({ ns, event_type, data, source });
    }
    int count(const std::string& t) const {
        int n = 0; for (const auto& e : events) if (e.event_type == t) n++; return n;
    }
    /* did ANY auth_event carry outcome == "granted"? */
    bool any_granted() const {
        for (const auto& e : events) {
            if (e.event_type != "auth_event") continue;
            auto it = e.data.find("outcome");
            if (it != e.data.end() && it->second.kind == FpValue::K::Str &&
                it->second.s == "granted")
                return true;
        }
        return false;
    }
};

/* A push that WOULD grant on a working sensor: a crew finger the gate opens
 * for.  Bound to the null HAL it must never load, and never match. */
static TemplatePush granting_push() {
    TemplatePush p;
    p.template_set_uuid = "uuid-PROD-001";
    p.role_capability_table = { { "crew", { "open_hatch" } } };
    p.entries = { { "alice", 1, "Captain", "crew", std::string(1536, 'e') } };
    return p;
}

int main() {
    /* ================================================================
     * (1) PRODUCTION DEFAULT: null HAL, NOT confirmed-live (no sensor).
     * The device must announce inoperable, load nothing, never grant.
     * ================================================================ */
    {
        NullFingerprintHAL null_hal; DevicePersistence nvs; RecordingBus bus;
        FingerprintDevice dev(&null_hal, &bus, "dev/s3-fp1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.set_hal_confirmed_live(false);   /* production no-sensor default */

        std::string st = dev.receive_push(granting_push());
        HeartbeatState h = dev.emit_heartbeat();
        auto outcome = dev.poll_match();

        CHECK(st == "inoperable", "prod default: push is refused inoperable");
        CHECK(bus.count("template_set_loaded") == 0,
              "prod default: never publishes template_set_loaded");
        CHECK(bus.count("fingerprint_ready") == 0,
              "prod default: never publishes fingerprint_ready (armed)");
        CHECK(bus.count("fingerprint_inoperable") >= 1,
              "prod default: DOES announce fingerprint_inoperable");
        CHECK(h == HeartbeatState::INOPERABLE,
              "prod default: heartbeat is INOPERABLE");
        CHECK(!outcome.has_value(),
              "prod default: poll_match yields no outcome (no finger, ever)");
        CHECK(!bus.any_granted() && dev.count_authentication_attempts() == 0,
              "prod default: NEVER a granted auth_event; zero attempts");
    }

    /* ================================================================
     * (2) ADVERSARIAL: even if a careless caller marks the null HAL
     * confirmed-live, it STILL can never grant — match() is NO_FINGER
     * and the store cannot load, so no armed/ready/granted path exists.
     * This is the structural guarantee independent of the confirmed gate.
     * ================================================================ */
    {
        NullFingerprintHAL null_hal; DevicePersistence nvs; RecordingBus bus;
        FingerprintDevice dev(&null_hal, &bus, "dev/s3-fp1", "open_hatch", &nvs,
                              nullptr, std::nullopt, []() { return 1000.0; });
        dev.set_hal_confirmed_live(true);    /* WRONG on purpose — prove it can't grant */

        std::string st = dev.receive_push(granting_push());
        /* Poll many times; a working sensor would eventually grant a crew
         * finger — the null HAL sees NO_FINGER every time. */
        for (int i = 0; i < 8; i++) (void)dev.poll_match();
        HeartbeatState h = dev.emit_heartbeat();

        CHECK(st != "loaded",
              "adversarial: the null HAL cannot load a roster (store refuses)");
        CHECK(bus.any_granted() == false,
              "adversarial: NULL HAL can NEVER produce a granted match");
        CHECK(dev.count_authentication_attempts() == 0,
              "adversarial: NO_FINGER is not an attempt — count stays 0");
        CHECK(h != HeartbeatState::READY,
              "adversarial: empty store means never READY/armed");
        CHECK(dev.bridge().empty(),
              "adversarial: no slot->person bridge was ever built");
    }

    /* ================================================================
     * (3) The null HAL's own contract — the negative control on the HAL.
     * ================================================================ */
    {
        NullFingerprintHAL null_hal;
        Capabilities c = null_hal.capabilities();
        CHECK(c.slot_count == 0 && c.template_size_bytes == 0 &&
              c.feedback_channels == 0 && c.feedback_profile == "inoperable",
              "null HAL capabilities announce inoperable (zero everything)");
        CHECK(null_hal.match().outcome == MatchOutcome::NO_FINGER,
              "null HAL match() is hard-wired NO_FINGER");
        CHECK(null_hal.read_occupancy().empty() &&
              !null_hal.read_roster_stamp().has_value(),
              "null HAL has no occupancy and no roster stamp");
        FingerprintHALFailure err; Slot s = -1;
        bool stored = null_hal.store_template(std::string(1536, 'e'), s, err);
        CHECK(!stored && err.axis == FailureAxis::UNSUPPORTED,
              "null HAL store_template refuses (UNSUPPORTED)");
    }

    if (failures == 0)
        printf("ALL PASS (%d checks)\n", checks);
    else
        printf("%d/%d checks FAILED\n", failures, checks);
    return failures ? 1 : 0;
}
