/*
 * mock_r503_hal.cpp — MockFingerprintHAL implementation + the driven
 * enrollment ceremony.  C++ twin of mock.py (main @ 0ba4e8b).
 * [CXC-FPHAL-MOCK-ROLE] [CXC-FPHAL-OPERATIONS]
 *
 * PURE C++ — links in env:native_test and in the S3 bring-up firmware alike.
 */
#include "fingerprint_mock.h"

/* --- the driven ceremony (EnrollmentSession, declared in fingerprint_hal.h)
 * commands NO feedback of its own — feedback is the device firmware's job.
 * On the final capture it asks the HAL to allocate+write, like a template
 * install. */
int EnrollmentSession::capture() {
    if (complete_) return done;      /* already complete: no-op */
    done += 1;
    if (done >= required) {
        /* Deterministic opaque bytes; the parity test only checks that a slot
         * was allocated and occupancy == {slot}. */
        TemplateBytes t(1536, 'e');
        Slot s;
        FingerprintHALFailure err;
        if (hal && hal->store_template(t, s, err))
            slot_ = s;
        complete_ = true;
    }
    return done;
}

/* --- helpers ------------------------------------------------------------- */

/* Allocate a NON-CONTIGUOUS slot deterministically.  (7*k+3) % N is a
 * bijection for k < N when gcd(7, N)==1, so successive installs land in
 * {3,10,17,24,...} — distinct and gappy, never {0,1,2,3}. */
Slot MockFingerprintHAL::next_slot() {
    int n = caps_.slot_count;
    Slot slot = (7 * alloc_index_ + 3) % n;
    alloc_index_ += 1;
    return slot;
}

std::vector<std::string> MockFingerprintHAL::op_sequence() const {
    std::vector<std::string> ops;
    ops.reserve(call_log_.size());
    for (const auto& r : call_log_) ops.push_back(r.op);
    return ops;
}

/* --- [CXC-FPHAL-OPERATIONS] the ten operations --------------------------- */

Capabilities MockFingerprintHAL::capabilities() {
    record("capabilities");
    return caps_;
}

MatchResult MockFingerprintHAL::match() {
    record("match");
    if (!scripted_matches_.empty()) {
        MatchResult r = scripted_matches_.front();
        scripted_matches_.erase(scripted_matches_.begin());
        return r;
    }
    return MatchResult::no_finger();
}

EnrollmentSession MockFingerprintHAL::begin_enroll(Ordinal ordinal) {
    record("begin_enroll", "ordinal=" + std::to_string(ordinal));
    EnrollmentSession s;
    s.hal = this;
    s.ordinal = ordinal;
    s.required = caps_.captures_per_template;
    return s;
}

bool MockFingerprintHAL::store_template(const TemplateBytes& t, Slot& out_slot,
                                        FingerprintHALFailure& err) {
    /* [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] LAST LINE OF DEFENSE: a wrong-sized
     * write may BRICK a real sensor, so the device rejects it here even though
     * the service already checked before publishing. Checked before the fault
     * injector / install counter — a malformed template is invalid, not a
     * candidate for a scripted failure. */
    if (static_cast<int>(t.size()) != caps_.template_size_bytes) {
        record("store_template",
               "REJECTED wrong-size len=" + std::to_string(t.size()) +
               " expected=" + std::to_string(caps_.template_size_bytes));
        err = fp_make_failure(
            FailureAxis::CAPACITY,
            "template is " + std::to_string(t.size()) + " bytes, device requires " +
            std::to_string(caps_.template_size_bytes));
        return false;
    }
    store_call_n_ += 1;
    int n = store_call_n_;
    auto it = store_fault_.find(n);
    if (it != store_fault_.end()) {
        FailureAxis axis = it->second;
        record("store_template",
               "INJECTED-FAIL n=" + std::to_string(n) + " axis=" + to_string(axis));
        err = fp_make_failure(axis,
                              "injected store failure on install #" + std::to_string(n));
        return false;
    }
    Slot slot = next_slot();
    store_[slot] = t;
    record("store_template",
           "n=" + std::to_string(n) + " -> slot=" + std::to_string(slot));
    out_slot = slot;
    return true;
}

bool MockFingerprintHAL::read_template(Slot slot, TemplateBytes& out,
                                       FingerprintHALFailure& err) {
    record("read_template", "slot=" + std::to_string(slot));
    if (!readback_supported_) {
        err = fp_make_failure(FailureAxis::UNSUPPORTED, "readback not supported");
        return false;
    }
    auto it = store_.find(slot);
    if (it == store_.end()) {
        err = fp_make_failure(FailureAxis::CAPACITY,
                              "slot " + std::to_string(slot) + " out of range/empty");
        return false;
    }
    out = it->second;
    return true;
}

bool MockFingerprintHAL::empty(FingerprintHALFailure& err) {
    empty_call_n_ += 1;
    int n = empty_call_n_;
    bool fail = delete_fault_set_ &&
                (delete_fault_nth_ == 0 || delete_fault_nth_ == n);
    if (fail) {
        FailureAxis axis = delete_fault_;
        record("empty",
               "INJECTED-FAIL n=" + std::to_string(n) + " axis=" + to_string(axis));
        err = fp_make_failure(axis,
                              "injected delete failure on empty #" + std::to_string(n));
        return false;
    }
    store_.clear();
    /* A fresh install sequence begins after every wipe. */
    store_call_n_ = 0;
    record("empty", "n=" + std::to_string(n));
    return true;
}

std::set<Slot> MockFingerprintHAL::read_occupancy() {
    record("read_occupancy");
    std::set<Slot> occ;
    for (const auto& kv : store_) occ.insert(kv.first);
    return occ;
}

void MockFingerprintHAL::write_roster_stamp(const RosterStamp& stamp) {
    stamp_ = stamp;
    record("write_roster_stamp", "len=" + std::to_string(stamp.size()));
}

std::optional<RosterStamp> MockFingerprintHAL::read_roster_stamp() {
    record("read_roster_stamp");
    return stamp_;
}

bool MockFingerprintHAL::command_feedback(FeedbackState state,
                                          const FpProgress& progress,
                                          FingerprintHALFailure& err) {
    if (caps_.feedback_channels_empty()) {
        err = fp_make_failure(FailureAxis::UNSUPPORTED, "no feedback channels");
        return false;
    }
    feedback_log_.push_back({ state, progress });
    /* record the state as detail for legibility */
    record("command_feedback", std::to_string(static_cast<int>(state)));
    return true;
}
