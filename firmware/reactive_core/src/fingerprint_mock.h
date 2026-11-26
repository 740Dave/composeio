/*
 * fingerprint_mock.h — MockFingerprintHAL, THE REFERENCE. [CXC-FPHAL-MOCK-ROLE]
 * C++ twin of python/composeio/peripherals/fingerprint/mock.py (main @ 0ba4e8b).
 *
 * Not a stub — the canonical realization.  Two properties earn it that status
 * and both are needed by the parity suite as the NEGATIVE CONTROL:
 *   1. FAULT INJECTION — it can produce each of the six failure axes on demand.
 *   2. CALL-ORDER RECORDING — every HAL call lands, in order, in call_log, so
 *      delete-before-write and stamp-strictly-last are assertable.
 *
 * The mock allocates a NON-CONTIGUOUS slot set deliberately ((7*k+3) % N): a
 * contiguous {0,1,2,3} decodes identically under several wrong install orders
 * and could not falsify an ordering bug ([CXC-FPHAL-PARITY-TEST]).
 */
#ifndef FINGERPRINT_MOCK_H
#define FINGERPRINT_MOCK_H

#include <map>
#include <utility>
#include <vector>

#include "fingerprint_hal.h"

struct CallRecord {
    std::string op;
    std::string detail;
};

/* =========================================================================
 * ⛔ USE-ENVELOPE — RATIFIED, LOAD-BEARING.  READ BEFORE BINDING THIS CLASS.
 * [CXC-FPHAL-MOCK-ROLE] [DEC-R503-MOCK-STRUCTURAL-GUARD] (D9, David 2026-08-24)
 * [CXC-FPHAL-MATCH-USES-ENROLL-SENSOR] DEFECT-ARMED-ROSTER-LOADED-R503-DEVICE-78C389
 * =========================================================================
 * MockFingerprintHAL is a FUNCTIONAL HAL: it stores templates, allocates
 * slots, and — critically — can RETURN A GRANTED MATCH (script_match()).
 * That power is exactly why it is dangerous in the wrong build.  It is kept
 * ON PURPOSE (you cannot automate a fingertip, so it is the parity-test
 * oracle) but its binding as the DEVICE HAL is now structurally gated.
 *
 *   X  HOST / NATIVE TEST ORACLE — UNRESTRICTED.  The expected value the
 *      single parity suite runs the real backend against; the only reachable
 *      way to exercise the six failure axes and the enrollment round-trip on
 *      demand ([CXC-FPHAL-PARITY-TEST]).  This is host_test_main.cpp's use.
 *   Y  BENCH / DEV ON HARDWARE WITH NO SENSOR — ONLY behind an EXPLICIT
 *      -DFP_ALLOW_MOCK build flag.  A deliberate, opt-in developer choice,
 *      never a default.
 *   Z  SCRIPTED UX / FLOW DEMO — the offline demo path — ONLY behind the same
 *      EXPLICIT -DFP_ALLOW_MOCK opt-in.
 *   ⛔ NEVER  A SILENT PRODUCTION RUNTIME FALLBACK.  A real device whose
 *      sensor is absent or answers late is INOPERABLE, never mock-backed:
 *      it binds NullFingerprintHAL (fingerprint_null_hal.h) and announces
 *      fingerprint_inoperable until the real R503 answers and the HAL is
 *      live-rebound.  In the production env (fingerprint_tm1637_s3, no
 *      FP_ALLOW_MOCK) this class is NOT COMPILED into the device-binding path
 *      at all — see the #ifdef FP_ALLOW_MOCK guard on g_hal in
 *      fingerprint_bringup_main.cpp.  That is D9: make the just-fixed defect
 *      (a device silently impersonating an armed sensor on the mock)
 *      un-reintroducible, not merely discouraged.
 * ========================================================================= */
class MockFingerprintHAL final : public FingerprintHAL {
public:
    /* R503-shaped defaults (C70: a 200-template on-chip store).  These are the
     * mock's OWN announced values; parity checks type + stability, so a backend
     * announces its own. */
    static Capabilities default_capabilities() {
        Capabilities c;
        c.slot_count = 200;
        c.supports_readback = true;
        c.feedback_channels = static_cast<uint32_t>(FeedbackChannel::AURA_LED);
        c.captures_per_template = 2;
        c.template_size_bytes = 1536;
        c.feedback_profile = "canonical";   /* the mock IS the reference table */
        return c;
    }

    MockFingerprintHAL() : MockFingerprintHAL(default_capabilities()) {}
    explicit MockFingerprintHAL(const Capabilities& caps)
        : caps_(caps), readback_supported_(caps.supports_readback) {}

    /* ---- fault injection (the NEGATIVE CONTROL) ---- */
    /* Make the nth (1-based) store_template of the current install sequence
     * fail on `axis`.  The install-order counter resets on every empty(). */
    void inject_store_failure(int nth, FailureAxis axis = FailureAxis::CAPACITY) {
        store_fault_[nth] = axis;
    }
    /* Make empty() fail on `axis`.  nth selects which empty() (1-based); 0/None
     * fails every empty().  nth==2 fails only the CLEANUP delete after a failed
     * load, leaving the pre-install wipe to succeed. */
    void inject_delete_failure(FailureAxis axis, int nth = 0) {
        delete_fault_ = axis;
        delete_fault_nth_ = nth;  /* 0 == every */
        delete_fault_set_ = true;
    }
    void clear_faults() {
        store_fault_.clear();
        delete_fault_set_ = false;
        delete_fault_nth_ = 0;
    }
    /* Queue a MatchResult the next match() returns.  Empty queue -> NO_FINGER. */
    void script_match(const MatchResult& r) { scripted_matches_.push_back(r); }

    /* ---- [CXC-FPHAL-OPERATIONS] the ten operations ---- */
    Capabilities capabilities() override;
    MatchResult match() override;
    EnrollmentSession begin_enroll(Ordinal ordinal) override;
    bool store_template(const TemplateBytes& t, Slot& out_slot,
                        FingerprintHALFailure& err) override;
    bool read_template(Slot slot, TemplateBytes& out,
                       FingerprintHALFailure& err) override;
    bool empty(FingerprintHALFailure& err) override;
    std::set<Slot> read_occupancy() override;
    void write_roster_stamp(const RosterStamp& stamp) override;
    std::optional<RosterStamp> read_roster_stamp() override;
    bool command_feedback(FeedbackState state, const FpProgress& progress,
                          FingerprintHALFailure& err) override;

    /* ---- test-facing introspection ---- */
    size_t occupancy_count() const { return store_.size(); }
    std::vector<std::string> op_sequence() const;
    void reset_log() { call_log_.clear(); feedback_log_.clear(); }
    const std::vector<CallRecord>& call_log() const { return call_log_; }
    const std::vector<std::pair<FeedbackState, FpProgress>>& feedback_log() const {
        return feedback_log_;
    }

private:
    Slot next_slot();
    void record(const std::string& op, const std::string& detail = "") {
        call_log_.push_back({ op, detail });
    }

    Capabilities caps_;
    std::map<Slot, TemplateBytes> store_;
    std::optional<RosterStamp> stamp_;
    int alloc_index_ = 0;      /* advances per successful install, across empties */

    std::vector<CallRecord> call_log_;
    std::vector<std::pair<FeedbackState, FpProgress>> feedback_log_;

    std::map<int, FailureAxis> store_fault_;   /* nth (1-based) -> axis */
    int store_call_n_ = 0;
    bool delete_fault_set_ = false;
    FailureAxis delete_fault_ = FailureAxis::CAPACITY;
    int delete_fault_nth_ = 0;                  /* 0 == every empty() */
    int empty_call_n_ = 0;
    bool readback_supported_ = true;

    std::vector<MatchResult> scripted_matches_;
};

#endif /* FINGERPRINT_MOCK_H */
