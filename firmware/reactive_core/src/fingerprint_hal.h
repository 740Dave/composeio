/*
 * fingerprint_hal.h — the agnostic fingerprint HAL contract, C++ twin.
 * [SPEC:CXC-FPHAL]  (mirror of python/composeio/peripherals/fingerprint/hal.py
 * on main @ 0ba4e8b — THE ORACLE.)
 *
 * The pure, vendor-free device-side seam: the primitives, the ten
 * operations, the six-field capabilities record, the closed six-axis
 * failure taxonomy, and the twelve feedback states.  It names no vendor,
 * no person, character or role — the HAL knows only hop 1
 * ([CXC-FPID-SENSOR-KNOWS-ONLY-HOP-1]).
 *
 * Two realizations satisfy this contract: MockFingerprintHAL (the canonical
 * reference — see fingerprint_mock.h) and, later, an R503 backend.  A single
 * parity suite runs unchanged against both; the mock is the expected value
 * ([CXC-FPHAL-MOCK-ROLE], [CXC-FPHAL-PARITY-TEST]).
 *
 * PURE C++ — no Arduino, no ESP-IDF, host-testable in env:native_test
 * exactly like actuator_hal.h.
 *
 * ⛔ MECHANISM NOTE (behavioural parity, not literal parity): the oracle
 * RAISES FingerprintHALError on a failed op; Arduino-ESP32 ships with C++
 * exceptions DISABLED, so a throw on-chip aborts.  The fallible ops here
 * therefore return a bool and fill a FingerprintHALFailure out-param — the
 * device logic's try/except becomes an if-check, and the OBSERVABLE
 * behaviour (which axis, when, non-fatal continuation) is identical.
 */
#ifndef FINGERPRINT_HAL_H
#define FINGERPRINT_HAL_H

#include <cstdint>
#include <optional>
#include <set>
#include <string>

/* --- [CXC-FPHAL-PRIMITIVE-TYPES] five primitives, and no identity type --- */
using Slot = int;            /* device-local 0-based index, 0 <= s < slot_count */
using Score = int;           /* vendor-scaled confidence, device-internal only  */
using Ordinal = int;         /* which of a person's templates (1 or 2 today)    */
using TemplateBytes = std::string;   /* opaque; the HAL never parses it         */
using RosterStamp = std::string;     /* opaque to the HAL; stored/returned whole */

/* --- [CXC-FPHAL-MATCH-RESULT] the outcome enum --------------------------- */
enum class MatchOutcome { MATCHED, NO_MATCH, NO_FINGER };

/* --- [CXC-FPHAL-FAILURE-AXES] six closed, disjoint axes ------------------- */
enum class FailureAxis {
    TRANSPORT,        /* the link failed: framing, checksum, timeout, silence */
    SENSOR_REFUSAL,   /* the part answered, and it was a documented refusal   */
    CAPACITY,         /* store full, slot out of range, or payload too large  */
    PROTOCOL_DESYNC,  /* the stream is phase-shifted                          */
    UNSUPPORTED,      /* capabilities says this implementation cannot do this */
    UNKNOWN_CODE      /* the part answered with a code no revision documents  */
};

inline const char* to_string(FailureAxis a) {
    switch (a) {
        case FailureAxis::TRANSPORT:       return "transport";
        case FailureAxis::SENSOR_REFUSAL:  return "sensor_refusal";
        case FailureAxis::CAPACITY:        return "capacity";
        case FailureAxis::PROTOCOL_DESYNC: return "protocol_desync";
        case FailureAxis::UNSUPPORTED:     return "unsupported";
        case FailureAxis::UNKNOWN_CODE:    return "unknown_code";
    }
    return "?";
}

/* --- [CXC-FPHAL-FEEDBACK-STATES] the closed set of twelve ---------------- */
enum class FeedbackState {
    IDLE, READY, AWAITING_FINGER, CAPTURE_ACCEPTED, CAPTURE_RETRY,
    ENROLL_PROGRESS, BUSY, FAULT, ACCEPT, REJECT_SILENT, REJECT_KNOWN, RETRY
};

/* A feedback output the sensor physically owns.  The empty channel set means
 * command_feedback fails with UNSUPPORTED. */
enum class FeedbackChannel : uint32_t { AURA_LED = 1u << 0 };

/* --- [CXC-FPHAL-CAPABILITIES-SCHEMA] six required fields, no defaults ---
 * max_templates_per_person was REMOVED (2026-08-18): the sensor has no
 * person concept, so a per-person limit is service-side game policy
 * ([CXC-FPID-ONE-OR-TWO-TEMPLATES]), never a device capability.
 * feedback_profile was ADDED (2026-08-22): a stable string NAMING the
 * command_feedback realization a backend implements — the mock's canonical
 * three-colour table is "canonical", the R503's two-channel Aura mapping is
 * "r503-2ch".  It lets a consumer of the announce distinguish which physical
 * feedback vocabulary a device speaks WITHOUT reading the vendor out of the
 * HAL, which knows only hop 1.  It is the SIXTH required field, and it is the
 * one enrichment this contract took in this pass. */
struct Capabilities {
    int slot_count = 0;
    bool supports_readback = false;
    uint32_t feedback_channels = 0;   /* bitmask of FeedbackChannel; 0 == none */
    int captures_per_template = 0;
    int template_size_bytes = 0;
    std::string feedback_profile;     /* NAMES the command_feedback realization */

    bool has_feedback_channel(FeedbackChannel c) const {
        return (feedback_channels & static_cast<uint32_t>(c)) != 0;
    }
    bool feedback_channels_empty() const { return feedback_channels == 0; }

    bool operator==(const Capabilities& o) const {
        return slot_count == o.slot_count &&
               supports_readback == o.supports_readback &&
               feedback_channels == o.feedback_channels &&
               captures_per_template == o.captures_per_template &&
               template_size_bytes == o.template_size_bytes &&
               feedback_profile == o.feedback_profile;
    }
};

/* --- [CXC-FPHAL-MATCH-RESULT] slot+score together or not at all ---------- */
struct MatchResult {
    MatchOutcome outcome = MatchOutcome::NO_FINGER;
    std::optional<Slot> slot;
    std::optional<Score> score;

    static MatchResult matched(Slot s, Score sc) {
        return { MatchOutcome::MATCHED, s, sc };
    }
    static MatchResult no_match()  { return { MatchOutcome::NO_MATCH,  std::nullopt, std::nullopt }; }
    static MatchResult no_finger() { return { MatchOutcome::NO_FINGER, std::nullopt, std::nullopt }; }
};

/* The oracle enforces this in __post_init__ (raise); here it is a predicate
 * the parity test asserts, since we do not throw ([CXC-FPHAL-MATCH-RESULT]). */
inline bool fp_match_result_valid(MatchOutcome o, bool has_slot, bool has_score) {
    if (o == MatchOutcome::MATCHED) return has_slot && has_score;
    return !has_slot && !has_score;
}

/* --- [CXC-FPHAL-FAILURE-AXES] the failure payload ------------------------ */
struct FingerprintHALFailure {
    FailureAxis axis = FailureAxis::UNKNOWN_CODE;
    std::optional<int> sensor_code;   /* None on transport; present on
                                       * sensor_refusal + unknown_code       */
    std::string detail;
};

/* [CXC-FPHAL-FAILURE-AXES] taxonomy validity: transport quotes no code,
 * sensor_refusal/unknown_code MUST carry one.  Predicate, not a raise. */
inline bool fp_failure_valid(FailureAxis axis, const std::optional<int>& code) {
    if (axis == FailureAxis::TRANSPORT) return !code.has_value();
    if (axis == FailureAxis::SENSOR_REFUSAL || axis == FailureAxis::UNKNOWN_CODE)
        return code.has_value();
    return true;
}

/* Build a taxonomy-valid failure — the mock's `_failure` helper.  A
 * sensor_refusal/unknown_code with no code gets the R503's known-undocumented
 * 0x28 as a stand-in; a transport failure is forced code-less. */
inline FingerprintHALFailure fp_make_failure(
    FailureAxis axis, const std::string& detail,
    std::optional<int> sensor_code = std::nullopt) {
    if ((axis == FailureAxis::SENSOR_REFUSAL || axis == FailureAxis::UNKNOWN_CODE)
        && !sensor_code.has_value())
        sensor_code = 0x28;
    if (axis == FailureAxis::TRANSPORT)
        sensor_code = std::nullopt;
    return FingerprintHALFailure{ axis, sensor_code, detail };
}

/* Progress payload for ENROLL_PROGRESS (capture_n, capture_of). */
struct FpProgress {
    bool has_value = false;
    int capture_n = 0;
    int capture_of = 0;
};

class FingerprintHAL;   /* fwd — EnrollmentSession drives store_template on it */

/* --- [CXC-FPHAL-ENROLLMENT-SEQUENCE] the driven ceremony ----------------- */
struct EnrollmentSession {
    FingerprintHAL* hal = nullptr;
    Ordinal ordinal = 0;
    int required = 0;
    int done = 0;
    bool complete_ = false;
    std::optional<Slot> slot_;

    int captures_required() const { return required; }
    int captures_done() const { return done; }
    bool complete() const { return complete_; }
    std::optional<Slot> slot() const { return slot_; }

    int capture();       /* defined in mock_r503_hal.cpp (needs the HAL) */
    void abandon() { complete_ = true; }
};

/* --- [CXC-FPHAL-OPERATIONS] the ten operations --------------------------- */
class FingerprintHAL {
public:
    virtual ~FingerprintHAL() = default;

    /* -- the runtime face -- */
    virtual MatchResult match() = 0;

    /* -- the management face -- */
    virtual Capabilities capabilities() = 0;
    virtual EnrollmentSession begin_enroll(Ordinal ordinal) = 0;
    virtual bool store_template(const TemplateBytes& t, Slot& out_slot,
                                FingerprintHALFailure& err) = 0;
    virtual bool read_template(Slot slot, TemplateBytes& out,
                               FingerprintHALFailure& err) = 0;
    virtual bool empty(FingerprintHALFailure& err) = 0;
    virtual std::set<Slot> read_occupancy() = 0;
    virtual void write_roster_stamp(const RosterStamp& stamp) = 0;
    virtual std::optional<RosterStamp> read_roster_stamp() = 0;
    virtual bool command_feedback(FeedbackState state, const FpProgress& progress,
                                  FingerprintHALFailure& err) = 0;
};

#endif /* FINGERPRINT_HAL_H */
