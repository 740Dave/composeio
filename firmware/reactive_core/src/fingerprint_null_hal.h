/*
 * fingerprint_null_hal.h — NullFingerprintHAL, the INOPERABLE HAL.
 * [SPEC:CXC-FPHAL] [CXC-FPHAL-MATCH-USES-ENROLL-SENSOR]
 * [DEC-R503-MOCK-STRUCTURAL-GUARD] (D9, ratified by David 2026-08-24)
 * Closes DEFECT-ARMED-ROSTER-LOADED-R503-DEVICE-78C389.
 *
 * WHAT IT IS: the HAL a PRODUCTION build binds by default and whenever no
 * real R503 has been confirmed live.  It is the structural answer to the
 * question "what should a real device do while its sensor is absent or has
 * not yet answered?" — and the answer is NOTHING it could be mistaken for
 * arming.  Every operation reports inoperable; match() is hard-wired to
 * NO_FINGER and CAN NEVER return MATCHED, so a device bound to this HAL can
 * NEVER produce a granted/denied authentication.  read_occupancy() is empty
 * and read_roster_stamp() is nullopt, so it can never look READY/loaded even
 * if some caller wrongly marked it confirmed-live.
 *
 * WHY IT EXISTS — the mechanism, not the manners:
 * DEFECT-ARMED-ROSTER-LOADED-R503-DEVICE-78C389 was a real device SILENTLY
 * FORKED ONTO THE FUNCTIONAL MockFingerprintHAL, impersonating an armed,
 * loaded, ready sensor while the physical R503 was absent.  D8 (fail-loud +
 * live-rebind, shipped in 42e389f) catches that fork AT RUNTIME.  D9 (this
 * file) makes it UN-COMPILABLE: in a production build the functional mock is
 * not even a candidate for the device HAL (see the FP_ALLOW_MOCK guard in
 * fingerprint_bringup_main.cpp and fingerprint_mock.h's use-envelope), and
 * this inoperable NULL HAL takes the default/no-sensor slot instead.  The
 * intended composition is: production default = NullFingerprintHAL, marked
 * NOT confirmed-live -> device announces fingerprint_inoperable -> the real
 * R503 answers 'r503 ping' -> live-rebind to RealR503HAL -> armed.
 *
 * PURE C++ — no Arduino, no ESP-IDF; header-only inline so it links into the
 * host production-binding test (env:native_prod_binding) with NOTHING from
 * the mock translation unit.  That is the point: the test that proves the
 * mock is unreachable must not itself drag the mock in.
 */
#ifndef FINGERPRINT_NULL_HAL_H
#define FINGERPRINT_NULL_HAL_H

#include "fingerprint_hal.h"

/* -------------------------------------------------------------------------
 * fp_build_allows_mock_hal() — the compile-time truth of THIS build.
 * It mirrors, in a value a static_assert can read, the SAME FP_ALLOW_MOCK
 * macro that guards the functional mock's construction and binding.  A
 * production build (fingerprint_tm1637_s3, no FP_ALLOW_MOCK) evaluates this
 * to false; the host production-binding test static_asserts on it so a
 * regression that quietly re-enabled the mock would fail to COMPILE, not slip
 * through as a passing run.  [DEC-R503-MOCK-STRUCTURAL-GUARD]
 * ------------------------------------------------------------------------- */
inline constexpr bool fp_build_allows_mock_hal() {
#ifdef FP_ALLOW_MOCK
    return true;
#else
    return false;
#endif
}

/* --- [CXC-FPHAL-OPERATIONS] the ten operations, all inoperable ----------- */
class NullFingerprintHAL final : public FingerprintHAL {
public:
    /* Zeroed capabilities: no slots, no readback, no feedback channel, and a
     * feedback_profile that NAMES itself inoperable so a consumer of the
     * announce can tell this apart from a real backend that speaks a vocabulary.
     * slot_count == 0 means it structurally cannot store a template. */
    Capabilities capabilities() override {
        Capabilities c;
        c.slot_count = 0;
        c.supports_readback = false;
        c.feedback_channels = 0;
        c.captures_per_template = 0;
        c.template_size_bytes = 0;
        c.feedback_profile = "inoperable";
        return c;
    }

    /* THE load-bearing guarantee: an inoperable HAL sees no finger, EVER.
     * NO_FINGER routes through resolve_match() to std::nullopt — never an
     * attempt, never GRANTED/DENIED/UNRECOGNIZED. */
    MatchResult match() override { return MatchResult::no_finger(); }

    /* An enrollment on a null HAL completes immediately with NO slot: there is
     * nowhere to capture to.  store_template below refuses, so even a driven
     * ceremony allocates nothing. */
    EnrollmentSession begin_enroll(Ordinal ordinal) override {
        EnrollmentSession s;
        s.hal = this;
        s.ordinal = ordinal;
        s.required = 0;
        s.done = 0;
        s.complete_ = true;
        s.slot_ = std::nullopt;
        return s;
    }

    bool store_template(const TemplateBytes&, Slot&,
                        FingerprintHALFailure& err) override {
        err = fp_make_failure(FailureAxis::UNSUPPORTED,
                              "no sensor bound: device is inoperable (NULL HAL)");
        return false;
    }

    bool read_template(Slot, TemplateBytes&, FingerprintHALFailure& err) override {
        err = fp_make_failure(FailureAxis::UNSUPPORTED,
                              "no sensor bound: device is inoperable (NULL HAL)");
        return false;
    }

    bool empty(FingerprintHALFailure& err) override {
        err = fp_make_failure(FailureAxis::UNSUPPORTED,
                              "no sensor bound: device is inoperable (NULL HAL)");
        return false;
    }

    /* Empty store, no stamp: the heartbeat machine reads PUSH_NEEDED at most,
     * never READY, even absent the confirmed-live gate. */
    std::set<Slot> read_occupancy() override { return {}; }
    void write_roster_stamp(const RosterStamp&) override { /* no store to stamp */ }
    std::optional<RosterStamp> read_roster_stamp() override { return std::nullopt; }

    bool command_feedback(FeedbackState, const FpProgress&,
                          FingerprintHALFailure& err) override {
        /* No feedback channel (mirrors the interface's empty-channel contract). */
        err = fp_make_failure(FailureAxis::UNSUPPORTED,
                              "no feedback channel: device is inoperable (NULL HAL)");
        return false;
    }
};

#endif /* FINGERPRINT_NULL_HAL_H */
