/*
 * freshness.cpp — see freshness.h.  Ported from
 * python/composeio/freshness.py + python/composeio/namespace.py.
 *
 * Pure C++: no ESP-IDF, no logging, no allocation.  The app shell logs.
 *
 * ⛔ THE ADVERSARIAL MUTATION HARNESS.  A freshness receiver CAN ONLY REPORT
 * SUCCESS: if it never marks anything stale, every test passes.  So the six
 * load-bearing rules below are each guarded by a FRESHNESS_MUTANT switch that
 * breaks exactly one of them, and the selftest is run against each mutant to
 * prove it can go RED.  Nothing defines FRESHNESS_MUTANT in a shipping build
 * (grep platformio.ini — no env sets it); it is set on the command line by
 * tools/freshness_mutants.sh.  This is [CXC-VISION-EPISTEMICS] made
 * executable: an instrument that can only report SUCCESS is not an
 * instrument, and the negative control is the difference.
 *
 *   1  EventDriven gets a timeout too   -> quiet-by-design is condemned
 *   2  NeverReceived reported as Fresh  -> "no evidence of death"
 *   3  age stamped on CHANGE not RECEIPT-> a steady publisher expires
 *   4  a node never heard from is alive -> every unbooted board certified
 *   5  emit every tick, not on edge     -> the diagnostic floods the bus
 *   6  WireUnknown loses to the age rule-> an explicit 0xFD is relabelled
 *   7  the sampler term is dropped      -> a LIVE fast publisher flaps
 *   8  a class may name an undeclared ns-> the class fails OPEN
 *   9  impaired == (health != Ok)       -> every UART leaf stops judging
 *  10  impairment demotes Fresh too     -> deafness MANUFACTURES a fault
 *  11  class age is the MIN of members  -> the freshest launders the rest
 *  12  the beat parser knows JSON only  -> NODE_SILENT dies on framed wires
 */

#include "freshness.h"

#include <cstring>
#include <cstdio>
#include <cstdlib>

#ifndef FRESHNESS_MUTANT
#define FRESHNESS_MUTANT 0
#endif

const char kFreshSuffix[] = "/_fresh";
const char kVerdictSuffix[] = "/_verdict";

/* ------------------------------------------------------------------ */
/* Verdict vocabulary                                                  */
/* ------------------------------------------------------------------ */

const char* freshness_verdict_name(FreshVerdict v) {
    switch (v) {
        case FreshVerdict::Fresh:         return "fresh";
        case FreshVerdict::Stale:         return "stale";
        case FreshVerdict::WireUnknown:   return "wire_unknown";
        case FreshVerdict::NeverReceived: return "never_received";
        case FreshVerdict::NodeSilent:    return "node_silent";
        case FreshVerdict::Undeclared:    return "undeclared";
        case FreshVerdict::ReceiverImpaired: return "receiver_impaired";
    }
    return "undeclared";
}

bool freshness_believable(FreshVerdict v) {
    /* Mirrors freshness._UNBELIEVABLE exactly.  ⚠ Undeclared is BELIEVABLE
     * here and that is not an oversight: no judgement was authorised, so the
     * receiver has no grounds to disbelieve a value.  It is reported as its
     * own case precisely so "nothing is wrong" can never be read off a
     * display that means "nothing was checked". */
    switch (v) {
        case FreshVerdict::Stale:
        case FreshVerdict::WireUnknown:
        case FreshVerdict::NeverReceived:
        case FreshVerdict::NodeSilent:
            return false;
        case FreshVerdict::ReceiverImpaired:
            /* NOT believable.  The value really is old — what impairment
             * changes is WHO is at fault, never whether the number can be
             * trusted.  Reporting it believable would be the stale-value lie
             * with a politer name. */
            return false;
        default:
            return true;
    }
}

uint32_t freshness_timeout_for(uint32_t interval_ms, uint32_t eval_period_ms) {
    /* max(3 * interval, interval + max(100 ms, eval_period)).
     * [CXC-FRESH-VERDICT] timeout-formula, with the LEAF'S SAMPLER folded in.
     * The 3x is a J1939 CONVENTION; the sampler term is what stops a cadence
     * faster than the tick flapping on tick phase alone (see freshness.h). */
#if FRESHNESS_MUTANT == 7
    /* MUTANT: the Python-tier floor ALONE.  This is the shape a leaf receiver
     * acquires for free by porting the formula verbatim — the sampler is not
     * mentioned in the spec's formula because the Python reader has no
     * sampler. */
    (void)eval_period_ms;
    uint32_t floor_term = kFreshnessFloorMs;
#else
    uint32_t floor_term = eval_period_ms > kFreshnessFloorMs ? eval_period_ms
                                                             : kFreshnessFloorMs;
#endif
    uint32_t triple = interval_ms * kFreshnessMultiplier;
    uint32_t floored = interval_ms + floor_term;
    return triple > floored ? triple : floored;
}

bool freshness_link_impaired(uint8_t h) {
    /* kAnnLink*: 0 Unknown, 1 Ok, 2 Warning, 3 Degraded, 4 Down, 5 Stopped,
     * 6 Recovering.  Spelled as literals for the same reason
     * BusTransport::link_health() spells its default as one — the freshness
     * SEAM must not take a dependency on the ANNOUNCEMENT encoding, and the
     * layering runs one way.  freshness_bus.cpp static_asserts the agreement.
     *
     * ⛔ Unknown (0) IS NOT IMPAIRED.  bus_uart.cpp returns it unconditionally
     * and correctly; treating it as impaired silences every UART leaf forever.
     * ⛔ Warning (2) and Degraded (3) ARE NOT IMPAIRED either: error counters
     * climbing is a link still passing traffic, and suppressing verdicts on it
     * would hide a genuine publisher death behind a transient burst. */
#if FRESHNESS_MUTANT == 9
    /* MUTANT: the OBVIOUS predicate, `health != Ok`.  It reads correctly and
     * it silences the entire UART fleet, because kAnnLinkUnknown is 0 and
     * bus_uart.cpp returns it unconditionally and CORRECTLY. */
    return h != 1 /*Ok*/;
#else
    return h == 4 /*Down*/ || h == 5 /*Stopped*/ || h == 6 /*Recovering*/;
#endif
}

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

static void copy_bounded(char* dst, size_t cap, const char* src) {
    if (!cap) return;
    if (!src) { dst[0] = '\0'; return; }
    size_t n = strlen(src);
    if (n > cap - 1) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static bool ends_with(const char* s, const char* suffix) {
    size_t n = strlen(s), m = strlen(suffix);
    return n >= m && memcmp(s + n - m, suffix, m) == 0;
}

/* Monotonic ms difference that survives the 32-bit wrap at 49.7 days.  A
 * plain `now - last` on unsigned already wraps correctly; this exists to say
 * so out loud, because the naive SIGNED version does not and an uptime of 49
 * days is not hypothetical on a rack board. */
static inline uint32_t elapsed_ms(uint32_t now, uint32_t then) {
    return (uint32_t)(now - then);
}

/* ------------------------------------------------------------------ */
/* FreshnessTable                                                      */
/* ------------------------------------------------------------------ */

int FreshnessTable::_find(const char* ns) const {
    if (!ns) return -1;
    for (int i = 0; i < _count; i++)
        if (strcmp(_e[i].ns, ns) == 0) return i;
    return -1;
}

int FreshnessTable::_find_node(const char* node) const {
    if (!node || !node[0]) return -1;
    for (int i = 0; i < _node_count; i++)
        if (strcmp(_n[i].id, node) == 0) return i;
    return -1;
}

int FreshnessTable::_intern_node(const char* node) {
    int i = _find_node(node);
    if (i >= 0) return i;
    if (_node_count >= kMaxFreshnessNodes) return -1;
    Node& n = _n[_node_count];
    memset(&n, 0, sizeof(n));
    copy_bounded(n.id, sizeof(n.id), node);
    snprintf(n.ns, sizeof(n.ns), "_device/%s/heartbeat", n.id);
    n.seen = false;
    return _node_count++;
}

bool FreshnessTable::declare(const char* ns, FreshCadence cadence,
                             uint32_t interval_ms, uint32_t timeout_ms,
                             const char* node) {
    if (!ns || !ns[0]) { _refused++; return false; }

    /* ⛔ A VERDICT SIBLING MAY NEVER BE DECLARED.  Watching `<x>/_fresh` would
     * make the receiver judge its own output; the verdict would then be a
     * received value with its own age, and the regress has no bottom.  This
     * is the LOCAL form of the same rule that forbids uplinking a verdict. */
    if (ends_with(ns, kFreshSuffix) || ends_with(ns, kVerdictSuffix)) {
        _refused++;
        return false;
    }

    if (strlen(ns) >= kFreshnessNsCap) { _refused++; return false; }

    /* A Periodic declaration with no interval is not a contract, it is a
     * typo.  Refused rather than defaulted: an invented interval expires live
     * values on the strength of a declaration nobody made. */
    if (cadence == FreshCadence::Periodic && interval_ms == 0) {
        _refused++;
        return false;
    }

    /* ⛔ AN EXPLICIT TIMEOUT THE SAMPLER CANNOT HONOUR IS REFUSED, NOT WIDENED.
     *
     * The DERIVED timeout already folds in the evaluator's period, so it can
     * never be shorter than the interval at which anyone looks.  An EXPLICIT
     * `freshness: {timeout: 0.1}` bypasses that formula entirely, and with a
     * 250 ms sampler it would flap fresh/stale/fresh on tick phase alone with
     * a perfectly healthy publisher.
     *
     * REFUSED rather than silently floored, because an explicit timeout is an
     * author's deliberate claim about how long a value stays believable.
     * Quietly turning their 100 ms into 250 ms would make the board disagree
     * with its own configuration, and the author would never find out.  A
     * refused namespace is UNDECLARED, so it publishes no verdict, so the DSL
     * reads its absent guard as 0.0 — DISBELIEVED, and visible on the glass
     * immediately.  That is the direction a refusal must fail in. */
    if (timeout_ms && timeout_ms < kFreshnessEvalIntervalMs) {
        _refused++;
        return false;
    }

    int i = _find(ns);
    if (i < 0) {
        if (_count >= kMaxFreshnessEntries) {
            /* N+1: REFUSE LOUDLY (the counter is the loudness; the app shell
             * turns it into an error line naming the namespace).  ⛔ NEVER a
             * silent overwrite of entry 0, and never a silent drop that leaves
             * the caller believing it declared something. */
            _refused++;
            return false;
        }
        i = _count++;
        memset(&_e[i], 0, sizeof(_e[i]));
        copy_bounded(_e[i].ns, sizeof(_e[i].ns), ns);
    }

    Entry& e = _e[i];
    e.cadence = cadence;
    e.interval_ms = interval_ms;
    e.timeout_ms = timeout_ms ? timeout_ms : freshness_timeout_for(interval_ms);
    copy_bounded(e.node, sizeof(e.node), node);
    if (e.node[0]) _intern_node(e.node);

    /* A cadence FASTER than the sampler keeps its contract but loses
     * SENSITIVITY: the floor means ~ceil(eval/interval) consecutive misses are
     * needed instead of 3.  Counted so the loss is visible; not refused,
     * because refusing would leave a fast namespace watched by nothing, and
     * losing sensitivity cannot invent a false alarm while flapping can. */
    if (cadence == FreshCadence::Periodic &&
        interval_ms * kFreshnessMultiplier < kFreshnessEvalIntervalMs)
        _sampling_limited++;

    return true;
}

void FreshnessTable::on_arrival(const char* ns, uint32_t now_ms, double value) {
    int i = _find(ns);
    if (i < 0) return;             /* undeclared — nothing to stamp */
#if FRESHNESS_MUTANT == 3
    /* MUTANT: stamp on CHANGE, not on RECEIPT.  This is the shape a receiver
     * acquires for free by hanging the age counter off a change-notification
     * callback — Namespace.publish_state notifies subscribers only when
     * `new != old`.  It expires a publisher that is working perfectly, and it
     * does so ONLY for the steadiest signals in the fleet: exactly the ones an
     * operator most expects to trust. */
    if (_e[i].have_receipt && _e[i].last_value == value) return;
    _e[i].last_value = value;
#else
    (void)value;
#endif
    _e[i].last_receipt_ms = now_ms;
    _e[i].have_receipt = true;
    /* A real value clears a latched wire-unknown.  Self-healing, with no
     * reset step for anyone to forget ([CXC-FRESH-LAZY] self-healing). */
    _e[i].wire_unknown = false;
}

void FreshnessTable::on_wire_unknown(const char* ns, uint32_t now_ms) {
    int i = _find(ns);
    if (i < 0) return;
    /* The peer SPOKE, so the age resets — it is alive and knowingly without a
     * value.  Wire-unknown and expiry-unknown are DIFFERENT MECHANISMS
     * CONVERGING ON ONE RECEIVER STATE ([CXC-FRESH-UNKNOWN-TYPE]). */
    _e[i].last_receipt_ms = now_ms;
    _e[i].have_receipt = true;
    _e[i].wire_unknown = true;
}

void FreshnessTable::note_heartbeat(const char* node, uint32_t now_ms,
                                    uint32_t interval_ms) {
    if (!node || !node[0]) return;
    int i = _intern_node(node);
    if (i < 0) return;             /* node table full — refuse, never evict */
    _n[i].last_beat_ms = now_ms;
    if (interval_ms) _n[i].interval_ms = interval_ms;
    _n[i].seen = true;
}

bool FreshnessTable::node_seen(const char* node) const {
    int i = _find_node(node);
    return i >= 0 && _n[i].seen;
}

bool FreshnessTable::node_alive(const char* node, uint32_t now_ms) const {
    int i = _find_node(node);
#if FRESHNESS_MUTANT == 4
    /* MUTANT: "no evidence of death".  Certifies every node in the fleet
     * alive before a single beat arrives, including ones that never boot. */
    if (i < 0 || !_n[i].seen) return true;
#else
    if (i < 0 || !_n[i].seen) return false;
#endif
    uint32_t iv = _n[i].interval_ms;
    if (!iv) return true;          /* beat carried no cadence: cannot judge */
    return elapsed_ms(now_ms, _n[i].last_beat_ms) < iv * kFreshnessMultiplier;
}

void FreshnessTable::set_impaired_probe(ImpairedFn fn, void* user) {
    _impaired_fn = fn;
    _impaired_user = user;
}

bool FreshnessTable::receiver_impaired() const {
    /* No probe wired => NOT impaired.  ⛔ The default must be "I have standing"
     * and not "I am impaired": the latter would make a receiver with no link
     * instrument — every host test, every MQTT board — refuse to judge
     * anything, which is an instrument that never reports, which reads as all
     * clear.  Same asymmetry, same reason, as link_health()'s own default. */
    return _impaired_fn ? _impaired_fn(_impaired_user) : false;
}

FreshVerdict FreshnessTable::verdict(const char* ns, uint32_t now_ms) const {
    int i = _find(ns);
    if (i < 0) return FreshVerdict::Undeclared;
    FreshVerdict v = _raw_verdict(_e[i], now_ms);

    /* ⛔ IMPAIRMENT SUPPRESSES A NEGATIVE CLAIM.  IT NEVER MANUFACTURES ONE.
     *
     * Stale / NeverReceived / NodeSilent are assertions ABOUT OTHER NODES, and
     * a receiver that cannot hear has no grounds for any of them — a bus-off
     * leaf would otherwise report the ENTIRE FLEET dead, consistently and
     * confidently, and the uniformity is what makes it invisible.
     *
     * Fresh survives untouched because it rests on something we POSITIVELY
     * HEARD inside the window.  WireUnknown survives for the same reason: it
     * is a statement a publisher made and we received.  Undeclared survives
     * because it is a fact about our own configuration, not about the link. */
#if FRESHNESS_MUTANT == 10
    /* MUTANT: impairment demotes EVERYTHING, including a namespace we
     * positively heard inside its window.  Deafness then MANUFACTURES a fault
     * instead of merely withdrawing a claim — the same over-condemnation
     * defect as mutant 1, arriving through a different door. */
    if (receiver_impaired()) return FreshVerdict::ReceiverImpaired;
#endif
    if (!freshness_believable(v) && v != FreshVerdict::WireUnknown &&
        receiver_impaired())
        return FreshVerdict::ReceiverImpaired;
    return v;
}

FreshVerdict FreshnessTable::_raw_verdict(const Entry& e,
                                          uint32_t now_ms) const {

    /* ⛔ THE ORDERING IS LOAD-BEARING ([CXC-FRESH-VERDICT]).  It is not a
     * style choice and it is not commutative — each of these four steps is
     * here to stop a LATER rule from overwriting an EARLIER, stronger fact. */

    /* 1. WireUnknown BEFORE every age rule.  An explicit 0xFD is a POSITIVE
     *    statement — "I am alive and I know that I do not know" — and no age
     *    rule may relabel it. */
#if FRESHNESS_MUTANT != 6
    if (e.wire_unknown) return FreshVerdict::WireUnknown;
#endif

    /* 2. Undeclared BEFORE anything time-based.  No verdict is invented for a
     *    contract nobody made: Fresh would claim confidence nobody earned,
     *    Stale would expire live values on a declaration that was never made. */
    if (e.cadence == FreshCadence::Undeclared) return FreshVerdict::Undeclared;

    /* 3. NeverReceived BEFORE Stale.  There is no last value being
     *    disbelieved; there was never a first one, and the two call for
     *    different operator responses (early in boot vs a publisher that
     *    died). */
    if (!e.have_receipt) {
#if FRESHNESS_MUTANT == 2
        return FreshVerdict::Fresh;    /* MUTANT: absence of evidence */
#else
        return FreshVerdict::NeverReceived;
#endif
    }

    /* 4. Quiet-by-construction routes to the NODE, never to a timeout
     *    ([CXC-FRESH-QUIET]).  controller_main.cpp gates throttle emission on
     *    a sample clock AND a deadband against the last EMITTED value — its
     *    own comment reads "quiet-bus by construction" — so a stationary
     *    lever publishes NOTHING, indefinitely, while being perfectly
     *    healthy.  "No message in N seconds implies stale" marks the fleet's
     *    most-watched namespace DEAD seconds after David stops touching it.
     *
     *    ⛔ AND NOTE WHAT CONDEMNS IT: only a node POSITIVELY IDENTIFIED and
     *    then POSITIVELY LOST.  An unattributed namespace (no `node:`) and a
     *    node that has never beaten both fall through to Fresh, because
     *    condemning them would mark every locally-published namespace dead
     *    the moment this ships. */
    if (e.cadence == FreshCadence::EventDriven) {
#if FRESHNESS_MUTANT == 1
        /* MUTANT: the naive rule — silence implies stale, for everything. */
        if (elapsed_ms(now_ms, e.last_receipt_ms) >=
            freshness_timeout_for(e.interval_ms ? e.interval_ms : 1000,
                                  kFreshnessEvalIntervalMs))
            return FreshVerdict::Stale;
        return FreshVerdict::Fresh;
#else
        if (e.node[0] && node_seen(e.node) && !node_alive(e.node, now_ms))
            return FreshVerdict::NodeSilent;
        return FreshVerdict::Fresh;
#endif
    }

    /* 5. Periodic: the declared timeout, and only now. */
    if (!e.timeout_ms) return FreshVerdict::Fresh;
    return elapsed_ms(now_ms, e.last_receipt_ms) >= e.timeout_ms
               ? FreshVerdict::Stale
               : FreshVerdict::Fresh;
}

uint32_t FreshnessTable::age_ms(const char* ns, uint32_t now_ms) const {
    int i = _find(ns);
    if (i < 0 || !_e[i].have_receipt) return UINT32_MAX;
    return elapsed_ms(now_ms, _e[i].last_receipt_ms);
}

/* ------------------------------------------------------------------ */
/* Freshness CLASSES  [CXC-FRESH-CLASS]                                */
/* ------------------------------------------------------------------ */

int FreshnessTable::_find_class(const char* name) const {
    if (!name || !name[0]) return -1;
    for (int i = 0; i < _class_count; i++)
        if (strcmp(_c[i].name, name) == 0) return i;
    return -1;
}

bool FreshnessTable::declare_class(const char* name,
                                   const char* const* members,
                                   size_t member_count) {
    if (!name || !name[0] || strlen(name) >= kFreshnessClassNameCap) {
        _refused++;
        return false;
    }

    /* ⛔ AN EMPTY CLASS IS REFUSED.  It would report FRESH forever — a green
     * light wired to nothing, and the most dangerous possible output of a
     * module whose entire job is to withhold confidence. */
    if (!members || member_count == 0) { _refused++; return false; }

    if (member_count > (size_t)kMaxFreshnessClassMembers) {
        _refused++;
        return false;
    }
    if (_find_class(name) >= 0 || _class_count >= kMaxFreshnessClasses) {
        _refused++;
        return false;
    }

    /* ⛔ A CLASS NAMING AN UNDECLARED NAMESPACE IS REFUSED, and this is the
     * subtle one.  Such a member has no contract, so its own verdict is
     * Undeclared, which is BELIEVABLE — so the conjunction would report the
     * class FRESH on the strength of the one member nobody is watching.  It
     * fails OPEN, and it fails open precisely for the namespace most likely to
     * die unnoticed.  Validated BEFORE anything is written, so a refusal
     * leaves no half-built class behind. */
    for (size_t m = 0; m < member_count; m++) {
#if FRESHNESS_MUTANT == 8
        /* MUTANT: accept a member nobody declared.  Its verdict is Undeclared,
         * which is BELIEVABLE, so the class certifies FRESH on the strength of
         * the one namespace nobody is watching — it fails OPEN. */
        if (!members[m] || !members[m][0]) { _refused++; return false; }
#else
        if (!members[m] || !members[m][0] || _find(members[m]) < 0) {
            _refused++;
            return false;
        }
#endif
    }

    Class& c = _c[_class_count];
    memset(&c, 0, sizeof(c));
    copy_bounded(c.name, sizeof(c.name), name);
    for (size_t m = 0; m < member_count; m++)
        copy_bounded(c.members[m], kFreshnessNsCap, members[m]);
    c.member_count = (int)member_count;
    _class_count++;
    return true;
}

FreshVerdict FreshnessTable::class_verdict(const char* name,
                                           uint32_t now_ms) const {
    int ci = _find_class(name);
    if (ci < 0) return FreshVerdict::Undeclared;
    const Class& c = _c[ci];
    /* The FIRST unbelievable member's OWN verdict, not a generic "bad": an
     * operator needs to know WHICH sensor died, not merely that the solution
     * is unusable. */
    for (int m = 0; m < c.member_count; m++) {
        FreshVerdict v = verdict(c.members[m], now_ms);
        if (!freshness_believable(v)) return v;
    }
    return FreshVerdict::Fresh;
}

const char* FreshnessTable::class_culprit(const char* name,
                                          uint32_t now_ms) const {
    int ci = _find_class(name);
    if (ci < 0) return "";
    const Class& c = _c[ci];
    for (int m = 0; m < c.member_count; m++)
        if (!freshness_believable(verdict(c.members[m], now_ms)))
            return c.members[m];
    return "";
}

uint32_t FreshnessTable::class_age_ms(const char* name,
                                      uint32_t now_ms) const {
    int ci = _find_class(name);
    if (ci < 0) return UINT32_MAX;
    const Class& c = _c[ci];
    uint32_t worst = 0;
    for (int m = 0; m < c.member_count; m++) {
        uint32_t a = age_ms(c.members[m], now_ms);
        /* ⛔ A MEMBER WITH NO AGE AT ALL DOMINATES.  An input never received
         * makes the derivation UNKNOWN rather than merely old, and returning
         * the max of the others would let the members that DID arrive launder
         * the absence of the one that did not. */
        if (a == UINT32_MAX) return UINT32_MAX;
#if FRESHNESS_MUTANT == 11
        /* MUTANT: the MIN.  "The solution is fresh because its newest input
         * is" — the freshest member laundering the staleness of the others. */
        if (m == 0 || a < worst) worst = a;
#else
        if (a > worst) worst = a;
#endif
    }
    /* MAX, not min and not mean: a navigation solution is as old as its OLDEST
     * input.  Taking anything else lets the freshest member launder the
     * staleness of the rest. */
    return worst;
}

const char* FreshnessTable::class_name_at(int i) const {
    return (i >= 0 && i < _class_count) ? _c[i].name : "";
}

/* ------------------------------------------------------------------ */

int FreshnessTable::emit_transitions(uint32_t now_ms, EmitFn fn, void* user) {
    if (!fn) return 0;
    int n = 0;
    for (int i = 0; i < _count; i++) {
        Entry& e = _e[i];
        FreshVerdict v = verdict(e.ns, now_ms);
#if FRESHNESS_MUTANT == 5
        /* MUTANT: emit every tick.  The diagnostic that causes the failure it
         * reports — 24 namespaces at 4 Hz is 96 publishes/second onto a queue
         * 16 deep, and the resulting drops get reported as staleness. */
        const bool changed = true;
        const bool fresh_changed = true;
#else
        const bool changed = !e.have_published || v != e.published;
        const bool fresh_changed =
            !e.have_published ||
            freshness_believable(v) != freshness_believable(e.published);
#endif
        if (!changed) continue;
        e.published = v;
        e.have_published = true;
        fn(user, e.ns, freshness_believable(v), fresh_changed,
           freshness_verdict_name(v), "");
        n++;
    }

    /* CLASSES, on the SAME edge rule.  A class is exactly as noisy as its
     * noisiest member and no noisier, because its verdict only moves when a
     * member's believability does. */
    for (int i = 0; i < _class_count; i++) {
        Class& c = _c[i];
        FreshVerdict v = class_verdict(c.name, now_ms);
#if FRESHNESS_MUTANT == 5
        const bool changed = true;
        const bool fresh_changed = true;
#else
        const bool changed = !c.have_published || v != c.published;
        const bool fresh_changed =
            !c.have_published ||
            freshness_believable(v) != freshness_believable(c.published);
#endif
        if (!changed) continue;
        c.published = v;
        c.have_published = true;
        fn(user, c.name, freshness_believable(v), fresh_changed,
           freshness_verdict_name(v), class_culprit(c.name, now_ms));
        n++;
    }
    return n;
}

const char* FreshnessTable::ns_at(int i) const {
    return (i >= 0 && i < _count) ? _e[i].ns : "";
}
const char* FreshnessTable::node_at(int i) const {
    return (i >= 0 && i < _count) ? _e[i].node : "";
}
FreshCadence FreshnessTable::cadence_at(int i) const {
    return (i >= 0 && i < _count) ? _e[i].cadence : FreshCadence::Undeclared;
}
uint32_t FreshnessTable::timeout_ms_at(int i) const {
    return (i >= 0 && i < _count) ? _e[i].timeout_ms : 0;
}

size_t FreshnessTable::heartbeat_namespaces(const char** out,
                                            size_t cap) const {
    size_t n = 0;
    for (int i = 0; i < _node_count && n < cap; i++)
        out[n++] = _n[i].ns;
    return n;
}

void FreshnessTable::reset() {
    _count = 0;
    _node_count = 0;
    _class_count = 0;
    _refused = 0;
    _sampling_limited = 0;
    /* ⚠ The impairment probe SURVIVES a reset.  It is a property of the
     * TRANSPORT, not of the scene, and a scene swap that silently restored the
     * receiver's standing to condemn a fleet it cannot hear would be the
     * defect back with a new trigger. */
}

/* ------------------------------------------------------------------ */
/* The heartbeat BODY — two quote styles, because the wire has two      */
/* ------------------------------------------------------------------ */

/* Find `key` written as "key" OR 'key' and return the first byte after the
 * colon.  Both forms occur on real wires (see freshness.h): MQTT serialises
 * the beat as JSON, a framed substrate carries the Python dict REPR. */
static const char* beat_field(const char* body, const char* key) {
    if (!body || !key) return nullptr;
    char pat[40];
    size_t n = strlen(key);
    if (n + 3 > sizeof(pat)) return nullptr;
#if FRESHNESS_MUTANT == 12
    /* MUTANT: JSON quotes only.  Correct on MQTT, and it finds no node on
     * every FRAMED substrate — where the beat arrives as the Python dict
     * repr — so NODE_SILENT becomes unreachable on exactly the wire the
     * heartbeat work was for, silently and with no error anywhere. */
    const int kQuoteStyles = 1;
#else
    const int kQuoteStyles = 2;
#endif
    for (int q = 0; q < kQuoteStyles; q++) {
        const char quote = q ? '\'' : '"';
        pat[0] = quote;
        memcpy(pat + 1, key, n);
        pat[n + 1] = quote;
        pat[n + 2] = '\0';
        const char* p = strstr(body, pat);
        if (!p) continue;
        p += n + 2;
        while (*p == ' ' || *p == ':') p++;
        return p;
    }
    return nullptr;
}

/* Copy the scalar at *p, stripping either quote style. */
static size_t beat_scalar(const char* p, char* out, size_t cap) {
    if (!p || !cap) return 0;
    char quote = 0;
    if (*p == '"' || *p == '\'') { quote = *p; p++; }
    size_t i = 0;
    while (*p && i + 1 < cap) {
        if (quote ? (*p == quote)
                  : (*p == ',' || *p == '}' || *p == ' ')) break;
        out[i++] = *p++;
    }
    out[i] = '\0';
    return i;
}

/* A minimal non-negative decimal parse.  atof/strtod would pull in locale
 * machinery for a value that is always a plain number on this wire, and this
 * file is deliberately dependency-free so it host-compiles and mutates. */
static double beat_number(const char* s) {
    if (!s) return 0.0;
    double whole = 0.0;
    while (*s >= '0' && *s <= '9') { whole = whole * 10 + (*s - '0'); s++; }
    if (*s == '.') {
        s++;
        double scale = 0.1;
        while (*s >= '0' && *s <= '9') {
            whole += (*s - '0') * scale;
            scale *= 0.1;
            s++;
        }
    }
    return whole;
}

bool freshness_parse_beat(const char* body, char* node, size_t node_cap,
                          uint32_t* interval_ms) {
    if (interval_ms) *interval_ms = 0;
    if (node && node_cap) node[0] = '\0';
    if (!body || !node || !node_cap) return false;

    if (beat_scalar(beat_field(body, "node"), node, node_cap) == 0)
        return false;                 /* a pulse with no patient */

    char iv[24];
    if (beat_scalar(beat_field(body, "interval_s"), iv, sizeof(iv))) {
        double s = beat_number(iv);
        if (s > 0.0 && interval_ms) *interval_ms = (uint32_t)(s * 1000.0 + 0.5);
    }
    return true;
}

/* ------------------------------------------------------------------ */
/* The one receiver for this boot                                      */
/* ------------------------------------------------------------------ */

FreshnessTable& freshness() {
    static FreshnessTable t;
    return t;
}
