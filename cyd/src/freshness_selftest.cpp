/*
 * freshness_selftest.cpp — host harness for the C++ leaf freshness receiver.
 * [SPEC:CXC-FRESHNESS] [CXC-FRESH-CPP-FOLLOWON] [CXC-VISION-EPISTEMICS]
 *
 *   pio run -e native_freshness && .pio/build/native_freshness/program
 * Must print "ALL PASS" and exit 0.
 *
 * ⛔ WHY THIS FILE IS SHAPED THE WAY IT IS.  A freshness receiver CAN ONLY
 * REPORT SUCCESS.  If it never marks anything stale, every test that asks
 * "did it go unknown?" passes.  And the INVERSE defect passes exactly the
 * same test: a receiver that condemns EVERYTHING — including the
 * quiet-by-design namespaces whose silence is the design — is equally useless
 * and equally green.  So every rule here is asserted in BOTH DIRECTIONS, and
 * every one of them is additionally checked against a MUTANT build that
 * breaks it on purpose (tools/freshness_mutants.sh).  An instrument that can
 * only report SUCCESS is not an instrument.
 *
 * ⛔ THE CLOCK IS INJECTED, and that is load-bearing rather than tidy.  The
 * decisive negative control is "STILL FRESH AT 2.999 s" — a claim a real
 * sleep cannot make honestly, because the sleep's own jitter is the same
 * order as the margin being asserted.  Every `now_ms` below is a literal.
 *
 * WHAT THIS CANNOT PROVE, said plainly so a green line does not imply it:
 * nothing here touches a wire, a transport, a scene or a needle.  It covers
 * the VERDICT LOGIC and the codec's injectivity.  Whether a real x27.168
 * needle actually stops lying when its publisher dies is a GLASS question and
 * this file is not evidence about it.
 */

#ifdef FRESHNESS_CORPUS_TEST

#include "freshness.h"
#include "bus_frame.h"
#include "yaml_min.h"

#include <cstdio>
#include <cstring>

static int s_pass = 0, s_fail = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) { s_pass++; }                                           \
        else { s_fail++; printf("FAIL: "); printf(__VA_ARGS__);           \
               printf("\n"); }                                            \
    } while (0)

static const char* V(FreshVerdict v) { return freshness_verdict_name(v); }

/* ------------------------------------------------------------------ */
/* Sink capture — what actually got PUBLISHED                          */
/* ------------------------------------------------------------------ */

struct Emitted {
    char ns[128];
    bool fresh;
    bool fresh_changed;
    char verdict[24];
    char culprit[128];
};
static Emitted s_emit[64];
static int s_emit_n = 0;

static void capture(void* /*user*/, const char* ns, bool fresh,
                    bool fresh_changed, const char* verdict,
                    const char* culprit) {
    if (s_emit_n >= 64) return;
    snprintf(s_emit[s_emit_n].ns, sizeof(s_emit[s_emit_n].ns), "%s", ns);
    snprintf(s_emit[s_emit_n].verdict, sizeof(s_emit[s_emit_n].verdict),
             "%s", verdict);
    snprintf(s_emit[s_emit_n].culprit, sizeof(s_emit[s_emit_n].culprit),
             "%s", culprit ? culprit : "");
    s_emit[s_emit_n].fresh = fresh;
    s_emit[s_emit_n].fresh_changed = fresh_changed;
    s_emit_n++;
}
static void emit_reset() { s_emit_n = 0; }
static const Emitted* emitted_for(const char* ns) {
    for (int i = 0; i < s_emit_n; i++)
        if (strcmp(s_emit[i].ns, ns) == 0) return &s_emit[i];
    return nullptr;
}

/* ------------------------------------------------------------------ */

int main(void) {
    printf("freshness receiver: cyd/src/freshness.cpp "
           "(semantics ported from python/composeio/freshness.py)\n");
#if FRESHNESS_MUTANT
    printf("*** MUTANT BUILD %d — this build is DELIBERATELY WRONG and the "
           "suite MUST fail ***\n", FRESHNESS_MUTANT);
#endif

    /* ============================================================== *
     * 1. THE TIMEOUT FORMULA — max(3*interval, interval + 0.1 s)
     *    The 3x is a J1939 CONVENTION.  The floor is what stops a fast
     *    cadence producing a timeout inside its own jitter.
     * ============================================================== */
    CHECK(freshness_timeout_for(1000) == 3000,
          "timeout(1000 ms) = %u, want 3000 (3x dominates)",
          freshness_timeout_for(1000));
    /* With the Python-tier floor ALONE these would be 120 / 150 / 153.  The
     * sampler term is what makes them 270 / 300 / 301. */
    CHECK(freshness_timeout_for(20, 250) == 270,
          "timeout(20 ms, eval 250) = %u, want 270 (interval + sampler)",
          freshness_timeout_for(20, 250));
    CHECK(freshness_timeout_for(50, 250) == 300,
          "timeout(50 ms, eval 250) = %u, want 300",
          freshness_timeout_for(50, 250));
    /* With an instantaneous reader the Python numbers come back exactly — the
     * sampler term ADDS a constraint, it does not replace the old one. */
    CHECK(freshness_timeout_for(20, 0) == 120,
          "timeout(20 ms, eval 0) = %u, want 120 (the Python-tier floor)",
          freshness_timeout_for(20, 0));
    CHECK(freshness_timeout_for(51, 0) == 153,
          "timeout(51 ms, eval 0) = %u, want 153 (3x takes over above 50)",
          freshness_timeout_for(51, 0));
    CHECK(freshness_timeout_for(1000, 250) == 3000,
          "timeout(1000 ms, eval 250) = %u — a SLOW cadence must be "
          "unaffected by the sampler term", freshness_timeout_for(1000, 250));

    /* ⛔ THE TIMEOUT CAN NEVER BE SHORTER THAN THE SAMPLER'S PERIOD.
     * If it is, the verdict is decided by where the tick happens to land. */
    for (uint32_t iv = 1; iv <= 400; iv++)
        if (freshness_timeout_for(iv, kFreshnessEvalIntervalMs) <=
            kFreshnessEvalIntervalMs) {
            CHECK(false, "interval %u ms derives a timeout of %u ms, which is "
                  "<= the %u ms sampler — it can only flap", iv,
                  freshness_timeout_for(iv, kFreshnessEvalIntervalMs),
                  kFreshnessEvalIntervalMs);
            break;
        }
    s_pass++;   /* the loop above is one assertion over 400 intervals */

    /* ============================================================== *
     * 1b. A FAST PUBLISHER, ALIVE, SAMPLED AT THE TICK -> NEVER FLAPS
     *     The teardown's case: a 30 ms cadence under a 250 ms sampler.  With
     *     the Python-tier floor alone the timeout is 130 ms and the verdict
     *     depends on tick PHASE, so a perfectly healthy publisher produces
     *     fresh/stale/fresh — a false alarm manufactured entirely by the
     *     instrument, on the fastest and most important signals.
     * ============================================================== */
    {
        FreshnessTable t;
        CHECK(t.declare("throttle/fast", FreshCadence::Periodic, 30, 0,
                        nullptr),
              "declare(30 ms cadence) refused");
        CHECK(t.sampling_limited() == 1,
              "a cadence faster than the sampler was not COUNTED as "
              "sensitivity-limited (%u)", t.sampling_limited());

        /* Publisher alive at exactly 30 ms, ticks at 250 ms, swept across
         * every phase offset the tick can have relative to the arrivals. */
        int flaps = 0;
        for (uint32_t phase = 0; phase < 250; phase += 7) {
            FreshnessTable u;
            u.declare("throttle/fast", FreshCadence::Periodic, 30, 0, nullptr);
            uint32_t next_tick = 1000 + phase;
            for (uint32_t ms = 1000; ms < 1000 + 5000; ms++) {
                if ((ms - 1000) % 30 == 0) u.on_arrival("throttle/fast", ms, 1.0);
                if (ms >= next_tick) {
                    if (u.verdict("throttle/fast", ms) != FreshVerdict::Fresh)
                        flaps++;
                    next_tick += 250;
                }
            }
        }
        CHECK(flaps == 0,
              "⛔ %d tick(s) saw a LIVE 30 ms publisher as not-fresh — the "
              "timeout is shorter than the interval at which anyone looks, so "
              "the verdict is decided by tick PHASE and a healthy publisher "
              "flaps", flaps);
    }

    /* ============================================================== *
     * 1c. AN EXPLICIT TIMEOUT THE SAMPLER CANNOT HONOUR IS REFUSED
     *     The derived timeout folds the sampler in; an explicit one bypasses
     *     the formula entirely and would flap.  Refused rather than silently
     *     widened — quietly turning an author's 100 ms into 250 ms makes the
     *     board disagree with its own configuration and never say so.
     * ============================================================== */
    {
        FreshnessTable t;
        CHECK(!t.declare("too/fast", FreshCadence::Periodic, 1000, 100,
                         nullptr),
              "an explicit 100 ms timeout was ACCEPTED under a 250 ms sampler "
              "— it can only flap");
        CHECK(t.refused() == 1, "the unhonourable timeout was not counted");
        CHECK(t.verdict("too/fast", 999999) == FreshVerdict::Undeclared,
              "a refused namespace reports %s — it must be undeclared so its "
              "absent guard reads 0.0 (disbelieved) on the glass",
              V(t.verdict("too/fast", 999999)));
        /* An explicit timeout AT the sampler's period is honourable. */
        CHECK(t.declare("ok/timeout", FreshCadence::Periodic, 1000,
                        kFreshnessEvalIntervalMs, nullptr),
              "an explicit timeout equal to the sampler period was refused");
    }

    /* ============================================================== *
     * 2. A PUBLISHER THAT STOPS -> STALE AT 3x, AND NOT ONE MS BEFORE
     *    Both halves are required.  "It went stale eventually" is what a
     *    receiver that condemns everything also reports.
     * ============================================================== */
    {
        FreshnessTable t;
        CHECK(t.declare("dradis/contacts", FreshCadence::Periodic, 1000, 0,
                        "world-app"),
              "declare(dradis/contacts) refused");

        CHECK(t.verdict("dradis/contacts", 0) == FreshVerdict::NeverReceived,
              "before any arrival: %s, want never_received",
              V(t.verdict("dradis/contacts", 0)));

        t.on_arrival("dradis/contacts", 10000, 3.0);

        CHECK(t.verdict("dradis/contacts", 10000) == FreshVerdict::Fresh,
              "at t+0: %s, want fresh", V(t.verdict("dradis/contacts", 10000)));
        /* ⛔ THE NEGATIVE CONTROL.  One millisecond under the timeout it is
         * STILL FRESH.  A real sleep cannot assert this honestly. */
        CHECK(t.verdict("dradis/contacts", 12999) == FreshVerdict::Fresh,
              "at t+2999 ms (1 ms under 3x): %s, want fresh — a receiver that "
              "condemns early passes the 'did it go stale?' test too",
              V(t.verdict("dradis/contacts", 12999)));
        CHECK(t.verdict("dradis/contacts", 13000) == FreshVerdict::Stale,
              "at t+3000 ms (exactly 3x): %s, want stale",
              V(t.verdict("dradis/contacts", 13000)));
        CHECK(t.verdict("dradis/contacts", 99000) == FreshVerdict::Stale,
              "long after: %s, want stale",
              V(t.verdict("dradis/contacts", 99000)));

        /* SELF-HEALING: the publisher returns and nothing has to be reset. */
        t.on_arrival("dradis/contacts", 99000, 3.0);
        CHECK(t.verdict("dradis/contacts", 99001) == FreshVerdict::Fresh,
              "after the publisher returns: %s, want fresh (nothing latches)",
              V(t.verdict("dradis/contacts", 99001)));
    }

    /* ============================================================== *
     * 3. THE INVERSE DEFECT — QUIET BY DESIGN IS NOT DEAD
     *    [CXC-FRESH-QUIET].  `throttle/<n>` is gated on a sample clock AND a
     *    deadband against the last EMITTED value ("quiet-bus by
     *    construction"), so a stationary lever publishes NOTHING,
     *    indefinitely, while being perfectly healthy.  A rule that condemns
     *    it passes exactly the same "did it go stale?" test as a correct one.
     * ============================================================== */
    {
        FreshnessTable t;
        CHECK(t.declare("throttle/left", FreshCadence::EventDriven, 0, 0,
                        "ctrl-01"),
              "declare(throttle/left) refused");
        t.note_heartbeat("ctrl-01", 1000, 5000);
        t.on_arrival("throttle/left", 1000, 0.0);

        /* ONE HUNDRED THOUSAND SECONDS of silence — 27 hours — with the node
         * still beating.  STAYS FRESH. */
        for (uint32_t s = 0; s <= 100000; s += 4000)
            t.note_heartbeat("ctrl-01", 1000 + s * 1000, 5000);
        CHECK(t.verdict("throttle/left", 1000 + 100000u * 1000u) ==
                  FreshVerdict::Fresh,
              "quiet-by-design after 100000 s of silence: %s, want fresh — "
              "its own silence condemns NOTHING",
              V(t.verdict("throttle/left", 1000 + 100000u * 1000u)));

        /* And it is never STALE at any point along the way. */
        bool ever_stale = false;
        for (uint32_t s = 0; s <= 100000; s += 997)
            if (t.verdict("throttle/left", 1000 + s * 1000) ==
                FreshVerdict::Stale)
                ever_stale = true;
        CHECK(!ever_stale,
              "quiet-by-design went STALE at some point — silence is the "
              "DESIGN for this class and can never condemn it");
    }

    /* ============================================================== *
     * 4. WHAT DOES CONDEMN IT: THE NODE, AND ONLY THE NODE
     *    NODE_SILENT must be DISTINCT from STALE — a whole board gone is a
     *    different operator response from one publisher that died.
     * ============================================================== */
    {
        FreshnessTable t;
        t.declare("throttle/left", FreshCadence::EventDriven, 0, 0, "ctrl-01");
        t.on_arrival("throttle/left", 1000, 0.0);
        t.note_heartbeat("ctrl-01", 1000, 5000);

        CHECK(t.verdict("throttle/left", 1000) == FreshVerdict::Fresh,
              "node beating: %s, want fresh",
              V(t.verdict("throttle/left", 1000)));
        CHECK(t.verdict("throttle/left", 15999) == FreshVerdict::Fresh,
              "1 ms under 3x the DECLARED beat interval: %s, want fresh",
              V(t.verdict("throttle/left", 15999)));
        CHECK(t.verdict("throttle/left", 16000) == FreshVerdict::NodeSilent,
              "at 3x the declared beat interval: %s, want node_silent",
              V(t.verdict("throttle/left", 16000)));
        CHECK(t.verdict("throttle/left", 16000) != FreshVerdict::Stale,
              "node_silent collapsed into stale — the two call for different "
              "operator responses and the glass needs both words");

        /* THE CADENCE RIDES ON THE BEAT.  A node that declares 1 s is judged
         * at 3 s, not at the 5 s some other node declared — the receiver
         * never holds a configured second copy of somebody else's interval. */
        t.note_heartbeat("ctrl-02", 1000, 1000);
        t.declare("nav/heading", FreshCadence::EventDriven, 0, 0, "ctrl-02");
        t.on_arrival("nav/heading", 1000, 0.0);
        CHECK(t.verdict("nav/heading", 3999) == FreshVerdict::Fresh,
              "ctrl-02 declared 1 s: at 2.999 s %s, want fresh",
              V(t.verdict("nav/heading", 3999)));
        CHECK(t.verdict("nav/heading", 4000) == FreshVerdict::NodeSilent,
              "ctrl-02 declared 1 s: at 3 s %s, want node_silent",
              V(t.verdict("nav/heading", 4000)));
    }

    /* ============================================================== *
     * 5. ⛔ NO ATTRIBUTED PUBLISHER MEANS NO VERDICT
     *    A locally-published namespace has no node to condemn.  Condemning
     *    it anyway marks every in-process namespace dead the moment this
     *    ships — the defect that is invisible because it is uniform.
     * ============================================================== */
    {
        FreshnessTable t;
        /* No `node`. */
        t.declare("local/ui_mode", FreshCadence::EventDriven, 0, 0, nullptr);
        t.on_arrival("local/ui_mode", 1000, 0.0);
        CHECK(t.verdict("local/ui_mode", 9999999) == FreshVerdict::Fresh,
              "UNATTRIBUTED quiet namespace after ~3 hours: %s, want fresh — "
              "there is no node to condemn",
              V(t.verdict("local/ui_mode", 9999999)));

        /* Attributed to a node that has NEVER beaten.  The heartbeat may only
         * REMOVE confidence from a node positively identified and then
         * positively LOST — never from one never heard from at all. */
        t.declare("remote/thing", FreshCadence::EventDriven, 0, 0, "ghost-01");
        t.on_arrival("remote/thing", 1000, 0.0);
        CHECK(!t.node_seen("ghost-01"),
              "ghost-01 reported as SEEN before any beat");
        CHECK(!t.node_alive("ghost-01", 1000),
              "⛔ a node NEVER heard from was reported ALIVE — 'no evidence of "
              "death' certifies every node in the fleet including ones that "
              "never boot");
        CHECK(t.verdict("remote/thing", 9999999) == FreshVerdict::Fresh,
              "quiet ns attributed to a NEVER-SEEN node: %s, want fresh — a "
              "node must be positively identified and then positively lost",
              V(t.verdict("remote/thing", 9999999)));

        /* ...and once it HAS beaten and then stopped, it is condemned. */
        t.note_heartbeat("ghost-01", 2000, 5000);
        CHECK(t.node_alive("ghost-01", 2000), "beat not recorded");
        CHECK(t.verdict("remote/thing", 2000 + 15000) ==
                  FreshVerdict::NodeSilent,
              "after ghost-01 beat and then stopped: %s, want node_silent",
              V(t.verdict("remote/thing", 2000 + 15000)));
    }

    /* ============================================================== *
     * 6. A REPUBLISH OF THE *SAME* VALUE REFRESHES THE AGE
     *    [CXC-FRESH-RECEIPT].  A receiver hung off a change callback never
     *    sees a publisher faithfully restating a steady value, so it expires
     *    a transmitter that is working perfectly — and only the STEADIEST
     *    signals, which are the ones an operator most expects to trust.
     * ============================================================== */
    {
        FreshnessTable t;
        t.declare("sys/mode", FreshCadence::Periodic, 1000, 0, nullptr);
        t.on_arrival("sys/mode", 1000, 7.0);
        t.on_arrival("sys/mode", 3500, 7.0);   /* IDENTICAL value */
        CHECK(t.verdict("sys/mode", 4000) == FreshVerdict::Fresh,
              "after a republish of the SAME value: %s, want fresh",
              V(t.verdict("sys/mode", 4000)));
        CHECK(t.age_ms("sys/mode", 4000) == 500,
              "age after identical republish = %u ms, want 500 — the age "
              "resets on RECEIPT, not on CHANGE",
              t.age_ms("sys/mode", 4000));
    }

    /* ============================================================== *
     * 7. WIRE-UNKNOWN BEATS EVERY AGE RULE
     *    An explicit 0xFD is a POSITIVE statement ("alive, and I know that I
     *    do not know") and must not be relabelled by a timeout.
     * ============================================================== */
    {
        FreshnessTable t;
        t.declare("gps/fix", FreshCadence::Periodic, 1000, 0, nullptr);
        t.on_arrival("gps/fix", 1000, 42.0);
        t.on_wire_unknown("gps/fix", 2000);
        CHECK(t.verdict("gps/fix", 2000) == FreshVerdict::WireUnknown,
              "explicit 0xFD: %s, want wire_unknown",
              V(t.verdict("gps/fix", 2000)));
        CHECK(t.verdict("gps/fix", 999999) == FreshVerdict::WireUnknown,
              "0xFD long after the timeout: %s, want wire_unknown — the age "
              "rule must not relabel a positive statement",
              V(t.verdict("gps/fix", 999999)));
        CHECK(!freshness_believable(FreshVerdict::WireUnknown),
              "wire_unknown reported as believable");
        /* A real value clears it, with no reset step for anyone to forget. */
        t.on_arrival("gps/fix", 1000000, 43.0);
        CHECK(t.verdict("gps/fix", 1000000) == FreshVerdict::Fresh,
              "after a real value returns: %s, want fresh",
              V(t.verdict("gps/fix", 1000000)));
    }

    /* ============================================================== *
     * 8. UNDECLARED IS ITS OWN CASE AND IS *NOT* FRESH
     *    Reporting it as fresh is how a system claims confidence it never
     *    earned; reporting it stale expires live values on a declaration
     *    nobody made.  It is reported as "the question was never asked".
     * ============================================================== */
    {
        FreshnessTable t;
        CHECK(t.verdict("nobody/declared/this", 0) == FreshVerdict::Undeclared,
              "an unknown namespace: %s, want undeclared",
              V(t.verdict("nobody/declared/this", 0)));
        CHECK(t.verdict("nobody/declared/this", 999999999) ==
                  FreshVerdict::Undeclared,
              "an unknown namespace, much later: %s, want undeclared (never "
              "invented into stale)",
              V(t.verdict("nobody/declared/this", 999999999)));
        CHECK(strcmp(V(FreshVerdict::Undeclared), "undeclared") == 0,
              "undeclared does not report itself by name");
        /* NEVER_RECEIVED is DISTINCT from STALE and from UNDECLARED. */
        t.declare("early/boot", FreshCadence::Periodic, 1000, 0, nullptr);
        CHECK(t.verdict("early/boot", 999999) == FreshVerdict::NeverReceived,
              "declared but never received: %s, want never_received (not "
              "stale — there is no first value being disbelieved)",
              V(t.verdict("early/boot", 999999)));
        CHECK(!freshness_believable(FreshVerdict::NeverReceived),
              "never_received reported as believable");
    }

    /* ============================================================== *
     * 9. THE SIX VERDICT NAMES ARE THE PYTHON TOKENS
     *    An operator reading a Python monitor and a firmware board must not
     *    have to translate.
     * ============================================================== */
    CHECK(strcmp(V(FreshVerdict::Fresh), "fresh") == 0, "fresh name");
    CHECK(strcmp(V(FreshVerdict::Stale), "stale") == 0, "stale name");
    CHECK(strcmp(V(FreshVerdict::WireUnknown), "wire_unknown") == 0,
          "wire_unknown name");
    CHECK(strcmp(V(FreshVerdict::NeverReceived), "never_received") == 0,
          "never_received name");
    CHECK(strcmp(V(FreshVerdict::NodeSilent), "node_silent") == 0,
          "node_silent name");
    CHECK(freshness_believable(FreshVerdict::Fresh), "fresh believable");
    CHECK(!freshness_believable(FreshVerdict::Stale), "stale believable");
    CHECK(!freshness_believable(FreshVerdict::NodeSilent),
          "node_silent believable");

    /* ============================================================== *
     * 10. THE EMITTER IS EDGE-TRIGGERED
     *     "A diagnostic that causes the failure it reports" — 25 ESP_LOGW in
     *     60 ms delayed the very ring-draining being reported.  Anything that
     *     can fire per-namespace-per-tick MUST be edge-triggered.
     * ============================================================== */
    {
        FreshnessTable t;
        t.declare("a/one", FreshCadence::Periodic, 1000, 0, nullptr);
        t.declare("a/two", FreshCadence::Periodic, 1000, 0, nullptr);

        emit_reset();
        int n = t.emit_transitions(0, capture, nullptr);
        CHECK(n == 2, "first tick emitted %d, want 2 (both never_received)", n);
        CHECK(emitted_for("a/one") && !emitted_for("a/one")->fresh,
              "a/one first emission was not `not fresh`");

        emit_reset();
        int total = 0;
        for (uint32_t ms = 0; ms < 60000; ms += 250)
            total += t.emit_transitions(ms, capture, nullptr);
        CHECK(total == 0,
              "%d redundant emissions across 240 ticks with NOTHING changing "
              "— an emitter that fires per tick floods the very bus drain it "
              "then reports as stale", total);

        /* A real transition emits exactly once. */
        t.on_arrival("a/one", 60000, 1.0);
        emit_reset();
        int m = 0;
        for (uint32_t ms = 60000; ms < 61000; ms += 250)
            m += t.emit_transitions(ms, capture, nullptr);
        CHECK(m == 1, "a real transition emitted %d times, want exactly 1", m);
        CHECK(emitted_for("a/one") && emitted_for("a/one")->fresh,
              "a/one did not become fresh after its first arrival");
        CHECK(emitted_for("a/one") &&
                  strcmp(emitted_for("a/one")->verdict, "fresh") == 0,
              "a/one verdict string wrong");

        /* Stale -> the boolean flips; the six-case string carries the WHY. */
        emit_reset();
        int k = t.emit_transitions(63000, capture, nullptr);
        CHECK(k == 1, "expiry emitted %d, want 1", k);
        CHECK(emitted_for("a/one") && !emitted_for("a/one")->fresh &&
                  strcmp(emitted_for("a/one")->verdict, "stale") == 0,
              "expiry did not publish fresh=0 verdict=stale");
    }

    /* ============================================================== *
     * 11. CAPACITY — WHAT HAPPENS AT N+1
     *     kMaxPersist was 4, exactly the control plane's size, and broke the
     *     moment a fifth arrived with one WARN line.  N+1 here is a REFUSAL
     *     that writes nothing, and the refused namespace's verdict is
     *     UNDECLARED — so nothing it is not watching is ever certified.
     * ============================================================== */
    {
        FreshnessTable t;
        char ns[32];
        for (int i = 0; i < kMaxFreshnessEntries; i++) {
            snprintf(ns, sizeof(ns), "fill/%d", i);
            CHECK(t.declare(ns, FreshCadence::Periodic, 1000, 0, nullptr),
                  "declare %s refused below capacity", ns);
        }
        CHECK(t.count() == kMaxFreshnessEntries, "count = %d, want %d",
              t.count(), kMaxFreshnessEntries);
        CHECK(t.refused() == 0, "refused = %u before overflow, want 0",
              t.refused());

        /* N+1 */
        CHECK(!t.declare("overflow/one", FreshCadence::Periodic, 1000, 0,
                         nullptr),
              "⛔ the N+1 declaration was ACCEPTED — a bounded table that "
              "silently overwrites is worse than one that refuses");
        CHECK(t.refused() == 1, "refused = %u after overflow, want 1",
              t.refused());
        CHECK(t.count() == kMaxFreshnessEntries,
              "count grew past capacity to %d", t.count());
        /* Nothing was displaced. */
        CHECK(strcmp(t.ns_at(0), "fill/0") == 0,
              "entry 0 was OVERWRITTEN by the overflow (now '%s')",
              t.ns_at(0));
        /* And the refused namespace is UNDECLARED, never fresh. */
        CHECK(t.verdict("overflow/one", 999999) == FreshVerdict::Undeclared,
              "a REFUSED namespace reports %s — it must be undeclared, so a "
              "board that ran out of table never certifies what it is not "
              "watching", V(t.verdict("overflow/one", 999999)));

        /* A re-declaration of an EXISTING namespace is an update, not an
         * overflow. */
        CHECK(t.declare("fill/0", FreshCadence::Periodic, 2000, 0, nullptr),
              "re-declaring an existing namespace was refused");
        CHECK(t.timeout_ms_at(0) == 6000, "re-declare did not update timeout");
    }

    /* ============================================================== *
     * 12. THE INFINITE REGRESS IS REFUSED LOCALLY TOO
     *     A verdict that is itself watched becomes a received value with its
     *     own age.  Declaring `<x>/_fresh` is refused, writing nothing.
     * ============================================================== */
    {
        FreshnessTable t;
        CHECK(!t.declare("gps/fix/_fresh", FreshCadence::Periodic, 1000, 0,
                         nullptr),
              "declaring a `_fresh` sibling was ACCEPTED — a verdict with its "
              "own age is a regress with no bottom");
        CHECK(!t.declare("gps/fix/_verdict", FreshCadence::Periodic, 1000, 0,
                         nullptr),
              "declaring a `_verdict` sibling was ACCEPTED");
        CHECK(t.count() == 0, "a refused declaration still wrote an entry");
        CHECK(t.refused() == 2, "refusals = %u, want 2", t.refused());

        /* A Periodic declaration with no interval is a typo, not a contract. */
        CHECK(!t.declare("bad/decl", FreshCadence::Periodic, 0, 0, nullptr),
              "a Periodic declaration with interval 0 was ACCEPTED — an "
              "invented interval expires live values");
    }

    /* ============================================================== *
     * 13. ATTRIBUTION DRIVES THE SUBSCRIPTION SET
     *     On a FRAMED substrate an inbound frame is matched by ns HASH, and a
     *     CRC-16 does not invert — so a heartbeat from a node this board
     *     never declared literally cannot be NAMED on arrival.  Declaring the
     *     node is what makes the beat receivable.
     * ============================================================== */
    {
        FreshnessTable t;
        t.declare("a/x", FreshCadence::EventDriven, 0, 0, "ctrl-01");
        t.declare("a/y", FreshCadence::EventDriven, 0, 0, "ctrl-01");
        t.declare("a/z", FreshCadence::EventDriven, 0, 0, "world-app");
        const char* hb[8];
        size_t n = t.heartbeat_namespaces(hb, 8);
        CHECK(n == 2, "distinct heartbeat namespaces = %u, want 2 (ctrl-01 "
              "named twice must not be subscribed twice)", (unsigned)n);
        bool saw_ctrl = false, saw_world = false;
        for (size_t i = 0; i < n; i++) {
            if (strcmp(hb[i], "_device/ctrl-01/heartbeat") == 0) saw_ctrl = true;
            if (strcmp(hb[i], "_device/world-app/heartbeat") == 0)
                saw_world = true;
        }
        CHECK(saw_ctrl, "_device/ctrl-01/heartbeat not in the subscription set");
        CHECK(saw_world,
              "_device/world-app/heartbeat not in the subscription set");
    }

    /* ============================================================== *
     * 14. THE 32-BIT MILLISECOND CLOCK WRAPS AT 49.7 DAYS
     *     A rack board runs for months.  A SIGNED difference here turns a
     *     healthy publisher stale forever at the wrap, once, silently.
     * ============================================================== */
    {
        FreshnessTable t;
        t.declare("long/uptime", FreshCadence::Periodic, 1000, 0, nullptr);
        const uint32_t before_wrap = 0xFFFFFF00u;   /* 256 ms before wrap */
        t.on_arrival("long/uptime", before_wrap, 1.0);
        CHECK(t.verdict("long/uptime", 100u) == FreshVerdict::Fresh,
              "across the 32-bit ms wrap (age 356 ms): %s, want fresh",
              V(t.verdict("long/uptime", 100u)));
        CHECK(t.age_ms("long/uptime", 100u) == 356,
              "age across the wrap = %u ms, want 356",
              t.age_ms("long/uptime", 100u));
        CHECK(t.verdict("long/uptime", 3000u) == FreshVerdict::Stale,
              "3.25 s after the wrap: %s, want stale",
              V(t.verdict("long/uptime", 3000u)));
    }

    /* ============================================================== *
     * 15. THE CODEC IS INJECTIVE ACROSS EVERY SINGLE-BYTE INTEGER
     *     ⛔ THE 253 CONTROL.  `encode(253)` used to emit a bare 0xFD —
     *     byte-identical to unknown — and decode read a 1-byte 0xFD as the
     *     number 253.  The failure is not that unknown mis-decodes; it is
     *     that THE CODEC STOPPED BEING INJECTIVE, and no care at the decoder
     *     can recover information the encoder threw away.
     * ============================================================== */
    {
        uint8_t enc[256][8];
        size_t len[256];
        for (int v = 0; v <= 255; v++)
            len[v] = bus_value_encode_u8((uint8_t)v, enc[v]);

        uint8_t unk[8];
        size_t unk_len = bus_value_encode_unknown(unk);
        CHECK(unk_len == 1 && unk[0] == 0xFD,
              "unknown encoded to %u bytes led by 0x%02X, want 1 byte 0xFD",
              (unsigned)unk_len, unk[0]);

        int collisions = 0;
        for (int v = 0; v <= 255; v++) {
            if (len[v] == unk_len && memcmp(enc[v], unk, unk_len) == 0) {
                collisions++;
                printf("      integer %d encodes IDENTICALLY to `unknown`\n", v);
            }
        }
        CHECK(collisions == 0,
              "⛔ %d single-byte integer(s) share an encoding with `unknown` — "
              "the codec is NOT INJECTIVE", collisions);

        /* Pairwise injectivity across the whole domain. */
        int dupes = 0;
        for (int a = 0; a <= 255; a++)
            for (int b = a + 1; b <= 255; b++)
                if (len[a] == len[b] && memcmp(enc[a], enc[b], len[a]) == 0)
                    dupes++;
        CHECK(dupes == 0, "%d pair(s) of distinct integers share an encoding",
              dupes);

        /* And every integer round-trips to ITSELF through the decoder. */
        for (int v = 0; v <= 255; v++) {
            double num = -1;
            char str[8] = "";
            BusValueKind k = bus_value_decode(enc[v], len[v], &num, str,
                                              sizeof(str));
            CHECK(k == BusValueKind::Number && (int)num == v,
                  "decode(encode(%d)) -> kind %d value %g", v, (int)k, num);
        }

        /* A 1-byte 0xFD is UNKNOWN and can be nothing else. */
        double num = -1;
        char str[8] = "";
        CHECK(bus_value_decode(unk, 1, &num, str, sizeof(str)) ==
                  BusValueKind::Unknown,
              "a bare 0xFD did not decode as Unknown");

        /* ⛔ AND THE CARVE-OUT IS EXACTLY ONE INTEGER WIDE.  Widening it to
         * 254/255 would be cargo-culting a fix onto values that never
         * collided: 0xFE/0xFF are discriminated by LENGTH (only read as tags
         * at five bytes), which is the property 0xFD lacks. */
        CHECK(len[253] == 5 && enc[253][0] == 0xFE,
              "253 was not carved out to a tagged int32 (len %u, lead 0x%02X)",
              (unsigned)len[253], enc[253][0]);
        CHECK(len[254] == 1 && enc[254][0] == 254,
              "254 was carved out — it never collided (len %u)",
              (unsigned)len[254]);
        CHECK(len[255] == 1 && enc[255][0] == 255,
              "255 was carved out — it never collided (len %u)",
              (unsigned)len[255]);
        CHECK(len[252] == 1 && len[0] == 1,
              "an uncollided small int lost its single-byte fast path");
    }

    /* ============================================================== *
     * 16. AN UNKNOWN IS DELIVERED, NOT DROPPED
     *     bus_uart.cpp returned early on BusValueKind::None ("a zero-length
     *     payload means drop it").  An unknown is ALSO one byte with no
     *     payload after its tag, and a receiver that folds it into None
     *     silently discards the one message that says "do not believe me".
     * ============================================================== */
    {
        const uint8_t empty[1] = {0};
        double num = 0;
        char str[8] = "";
        CHECK(bus_value_decode(empty, 0, &num, str, sizeof(str)) ==
                  BusValueKind::None,
              "a genuinely empty value did not decode as None");
        const uint8_t fd[1] = {0xFD};
        CHECK(bus_value_decode(fd, 1, &num, str, sizeof(str)) !=
                  BusValueKind::None,
              "⛔ an `unknown` decoded as None — it would be DROPPED at "
              "bus_uart.cpp / bus_can.cpp and the receiver would never learn "
              "the publisher said 'I do not know'");
    }

    /* ============================================================== *
     * 17. THE DECLARATION IS READ FROM THE SAME YAML PYTHON READS
     *     A contract authored once must mean the same thing on both tiers.
     *     ⚠ This section exists because freshness_yaml.cpp was COMPILED into
     *     this env before anything exercised it — a file that builds and is
     *     never called is the cheapest possible way to ship a defect behind a
     *     green suite.
     * ============================================================== */
    {
        static const char kYaml[] =
            "app:\n"
            "  id: cyd-dradis\n"
            "namespaces:\n"
            "  - name: dradis/contacts\n"
            "    freshness: {refresh_interval: 1.0, node: world-app}\n"
            "  - name: throttle/left\n"
            "    freshness: {quiet_by_construction: true, node: ctrl-01}\n"
            "  - name: slow/thing\n"
            "    freshness: {refresh_interval: 5.0, timeout: 60.0}\n"
            "  - name: no/contract\n"
            "    state_engine: binary\n"
            "  - name: contradictory/one\n"
            "    freshness: {quiet_by_construction: true, refresh_interval: 2.0}\n";

        YamlDoc doc;
        const YamlNode* root = doc.parse(kYaml, sizeof(kYaml) - 1);
        CHECK(root != nullptr, "declaration YAML did not parse: %s",
              doc.error());

        FreshnessTable t;
        int contradictions = -1;
        int n = freshness_declare_from_yaml(t, root, &contradictions);
        CHECK(n == 3, "declared %d namespace(s), want 3", n);

        /* ⛔ A CONTRADICTION IS COUNTED, NOT SILENTLY RESOLVED.  "silence is
         * the design" and "it speaks every 2 s" cannot both be true, and
         * picking one would make the wrong half authoritative with the author
         * never learning which. */
        CHECK(contradictions == 1,
              "contradictory declarations counted %d, want 1 — a "
              "contradiction that is merely skipped is a declaration the "
              "author believes they made", contradictions);

        /* A namespace with NO `freshness:` block gets NO contract — it is
         * UNDECLARED, which is its own case and is not FRESH. */
        CHECK(t.verdict("no/contract", 999999) == FreshVerdict::Undeclared,
              "a namespace with no freshness block: %s, want undeclared",
              V(t.verdict("no/contract", 999999)));

        /* refresh_interval 1.0 s -> timeout 3000 ms (3x dominates). */
        t.on_arrival("dradis/contacts", 1000, 3.0);
        CHECK(t.verdict("dradis/contacts", 3999) == FreshVerdict::Fresh,
              "YAML-declared 1.0 s at 2.999 s: %s, want fresh",
              V(t.verdict("dradis/contacts", 3999)));
        CHECK(t.verdict("dradis/contacts", 4000) == FreshVerdict::Stale,
              "YAML-declared 1.0 s at 3.000 s: %s, want stale",
              V(t.verdict("dradis/contacts", 4000)));

        /* An EXPLICIT timeout: overrides the formula, and 60 s is NOT 15 s. */
        t.on_arrival("slow/thing", 1000, 1.0);
        CHECK(t.verdict("slow/thing", 1000 + 59999) == FreshVerdict::Fresh,
              "explicit timeout 60 s at 59.999 s: %s, want fresh",
              V(t.verdict("slow/thing", 1000 + 59999)));
        CHECK(t.verdict("slow/thing", 1000 + 60000) == FreshVerdict::Stale,
              "explicit timeout 60 s at 60.000 s: %s, want stale — an "
              "explicit timeout must OVERRIDE the 3x formula, not lose to it",
              V(t.verdict("slow/thing", 1000 + 60000)));

        /* quiet_by_construction routes to the node, and only the node. */
        t.on_arrival("throttle/left", 1000, 0.0);
        CHECK(t.verdict("throttle/left", 1000 + 3600000u) ==
                  FreshVerdict::Fresh,
              "YAML-declared quiet ns after an hour of silence: %s, want "
              "fresh", V(t.verdict("throttle/left", 1000 + 3600000u)));
        t.note_heartbeat("ctrl-01", 1000, 5000);
        CHECK(t.verdict("throttle/left", 1000 + 16000) ==
                  FreshVerdict::NodeSilent,
              "YAML-declared quiet ns after its node stopped beating: %s, "
              "want node_silent", V(t.verdict("throttle/left", 1000 + 16000)));

        /* The `node:` attribution drove the heartbeat subscription set. */
        const char* hb[8];
        size_t hbn = t.heartbeat_namespaces(hb, 8);
        CHECK(hbn == 2, "YAML declared 2 nodes, table reports %u",
              (unsigned)hbn);

        /* An EMPTY document declares nothing and refuses nothing. */
        FreshnessTable t2;
        CHECK(freshness_declare_from_yaml(t2, nullptr, nullptr) == 0,
              "a null root declared something");
        CHECK(t2.refused() == 0, "a null root refused something");
    }

    /* ============================================================== *
     * 18. THE FRESHNESS CLASS — the CONJUNCTION the leaf cannot express
     *     [CXC-FRESH-CLASS].  On the Python tier unknown PROPAGATES through
     *     arithmetic, so a derived value is automatically unbelievable if any
     *     input is.  DslValue has no unknown member, so that propagation is
     *     structurally ABSENT here and `expr: ns('a') + ns('b')` is silently
     *     unguarded.  A class collapses N guard terms into ONE — which is a
     *     term a reviewer can see is missing.
     * ============================================================== */
    {
        FreshnessTable t;
        t.declare("gps/fix", FreshCadence::Periodic, 1000, 0, nullptr);
        t.declare("imu/heading", FreshCadence::Periodic, 1000, 0, nullptr);
        t.declare("log/speed", FreshCadence::Periodic, 1000, 0, nullptr);
        const char* members[] = {"gps/fix", "imu/heading", "log/speed"};
        CHECK(t.declare_class("nav_solution", members, 3),
              "declare_class(nav_solution) refused");

        /* A member NEVER received makes the class unbelievable and the AGE
         * unknown — not "the max of the two that did arrive". */
        CHECK(t.class_verdict("nav_solution", 1000) ==
                  FreshVerdict::NeverReceived,
              "class before any member arrived: %s, want never_received",
              V(t.class_verdict("nav_solution", 1000)));
        CHECK(t.class_age_ms("nav_solution", 1000) == UINT32_MAX,
              "class age with a never-received member = %u, want UNKNOWN — a "
              "missing input must DOMINATE, not be skipped",
              t.class_age_ms("nav_solution", 1000));

        t.on_arrival("gps/fix", 1000, 1.0);
        t.on_arrival("imu/heading", 1500, 2.0);
        t.on_arrival("log/speed", 2000, 3.0);
        CHECK(t.class_verdict("nav_solution", 2000) == FreshVerdict::Fresh,
              "all three members fresh: class = %s, want fresh",
              V(t.class_verdict("nav_solution", 2000)));

        /* ⛔ AGE IS THE MAX.  A solution is as old as its OLDEST input; taking
         * anything else lets the freshest member launder the rest. */
        CHECK(t.class_age_ms("nav_solution", 2000) == 1000,
              "class age = %u ms, want 1000 (the OLDEST member's age, not the "
              "newest's 0 and not the mean)",
              t.class_age_ms("nav_solution", 2000));

        /* ONE member goes stale -> the whole class is unbelievable, and it
         * NAMES the culprit.  An operator needs to know WHICH sensor died. */
        CHECK(t.class_verdict("nav_solution", 4000) == FreshVerdict::Stale,
              "gps/fix expired at 4000: class = %s, want stale",
              V(t.class_verdict("nav_solution", 4000)));
        CHECK(strcmp(t.class_culprit("nav_solution", 4000), "gps/fix") == 0,
              "class culprit = '%s', want 'gps/fix' — 'the solution is bad' "
              "and 'the GPS died' are different sentences and only the second "
              "tells an operator what to touch",
              t.class_culprit("nav_solution", 4000));
        CHECK(t.class_culprit("nav_solution", 2000)[0] == '\0',
              "a healthy class named a culprit");

        /* A class is ONE term where a hand-written conjunction is three. */
        CHECK(!freshness_believable(t.class_verdict("nav_solution", 4000)),
              "a class with a stale member reported believable");

        /* ---- the two REFUSALS, both because the failure is OPEN ---- */
        CHECK(!t.declare_class("empty_one", nullptr, 0),
              "⛔ an EMPTY class was ACCEPTED — it reports FRESH forever, a "
              "green light wired to nothing");
        const char* ghost[] = {"gps/fix", "nobody/declared/this"};
        CHECK(!t.declare_class("open_one", ghost, 2),
              "⛔ a class naming an UNDECLARED namespace was ACCEPTED — that "
              "member's verdict is Undeclared, which is BELIEVABLE, so the "
              "class would certify FRESH on the strength of the one namespace "
              "nobody is watching");
        CHECK(t.class_count() == 1,
              "a refused class still wrote an entry (%d classes)",
              t.class_count());
        CHECK(t.refused() == 2, "class refusals = %u, want 2", t.refused());
        CHECK(t.class_verdict("open_one", 999999) == FreshVerdict::Undeclared,
              "a REFUSED class reports %s — it must be undeclared so its "
              "absent guard reads 0.0", V(t.class_verdict("open_one", 999999)));

        /* The class emits on the SAME edge rule, carrying its culprit. */
        emit_reset();
        t.emit_transitions(2000, capture, nullptr);
        const Emitted* e = emitted_for("nav_solution");
        CHECK(e && e->fresh, "class did not emit fresh at 2000");
        emit_reset();
        int n = t.emit_transitions(4000, capture, nullptr);
        e = emitted_for("nav_solution");
        CHECK(e && !e->fresh && strcmp(e->verdict, "stale") == 0 &&
                  strcmp(e->culprit, "gps/fix") == 0,
              "class transition did not carry fresh=0/stale/gps-fix");
        emit_reset();
        CHECK(t.emit_transitions(4001, capture, nullptr) == 0,
              "the class re-emitted with nothing changed");
        (void)n;
    }

    /* ============================================================== *
     * 19. A RECEIVER THAT CANNOT HEAR LOSES THE STANDING TO CONDEMN
     *     `stale` / `node_silent` are claims about OTHER nodes.  A bus-off
     *     leaf receives nothing, so every namespace times out at once and it
     *     renders "the entire fleet is dead" — confidently, uniformly, and
     *     wrongly.  ⛔ Lazy evaluation does NOT close this: the loop still
     *     ticks.  The receiver IS reading; it just is not hearing.
     * ============================================================== */
    {
        /* The kAnnLink* mapping, in BOTH directions.  The Unknown case is the
         * one that matters: bus_uart.cpp returns kAnnLinkUnknown ALWAYS and
         * correctly, so treating it as impaired silences every UART leaf in
         * the fleet — an instrument that never reports, which reads as all
         * clear. */
        CHECK(!freshness_link_impaired(0),
              "⛔ kAnnLinkUnknown treated as IMPAIRED — every UART leaf would "
              "stop judging anything, forever");
        CHECK(!freshness_link_impaired(1), "kAnnLinkOk treated as impaired");
        CHECK(!freshness_link_impaired(2),
              "kAnnLinkWarning treated as impaired — counters climbing is a "
              "link still passing traffic, and suppressing verdicts on it "
              "hides a real publisher death behind a transient burst");
        CHECK(!freshness_link_impaired(3),
              "kAnnLinkDegraded treated as impaired");
        CHECK(freshness_link_impaired(4), "kAnnLinkDown NOT treated as impaired");
        CHECK(freshness_link_impaired(5),
              "kAnnLinkStopped NOT treated as impaired");
        CHECK(freshness_link_impaired(6),
              "kAnnLinkRecovering NOT treated as impaired");

        static bool s_impaired = false;
        auto probe = [](void*) -> bool { return s_impaired; };

        FreshnessTable t;
        t.declare("dradis/contacts", FreshCadence::Periodic, 1000, 0,
                  "world-app");
        t.declare("throttle/left", FreshCadence::EventDriven, 0, 0, "ctrl-01");
        t.declare("never/came", FreshCadence::Periodic, 1000, 0, nullptr);
        t.on_arrival("dradis/contacts", 1000, 3.0);
        t.on_arrival("throttle/left", 1000, 0.0);
        t.note_heartbeat("ctrl-01", 1000, 5000);

        CHECK(!t.receiver_impaired(),
              "⛔ a table with NO probe wired reported itself IMPAIRED — the "
              "default must be 'I have standing', or every host test and every "
              "MQTT board silently stops judging");
        t.set_impaired_probe(probe, nullptr);

        /* ---- link HEALTHY: the verdicts are the receiver's to make ---- */
        s_impaired = false;
        CHECK(t.verdict("dradis/contacts", 5000) == FreshVerdict::Stale,
              "healthy link, expired publisher: %s, want stale",
              V(t.verdict("dradis/contacts", 5000)));
        CHECK(t.verdict("throttle/left", 20000) == FreshVerdict::NodeSilent,
              "healthy link, dead node: %s, want node_silent",
              V(t.verdict("throttle/left", 20000)));
        CHECK(t.verdict("never/came", 5000) == FreshVerdict::NeverReceived,
              "healthy link, nothing ever arrived: %s, want never_received",
              V(t.verdict("never/came", 5000)));

        /* ---- link DEAF: all three negative claims are withdrawn ---- */
        s_impaired = true;
        CHECK(t.verdict("dradis/contacts", 5000) ==
                  FreshVerdict::ReceiverImpaired,
              "deaf receiver still reported %s about ANOTHER node",
              V(t.verdict("dradis/contacts", 5000)));
        CHECK(t.verdict("throttle/left", 20000) ==
                  FreshVerdict::ReceiverImpaired,
              "deaf receiver still condemned a NODE: %s",
              V(t.verdict("throttle/left", 20000)));
        CHECK(t.verdict("never/came", 5000) == FreshVerdict::ReceiverImpaired,
              "deaf receiver still claimed never_received: %s",
              V(t.verdict("never/came", 5000)));
        CHECK(!freshness_believable(FreshVerdict::ReceiverImpaired),
              "receiver_impaired reported BELIEVABLE — the value really is "
              "old; what changes is who is at fault, not whether to trust it");
        CHECK(strcmp(V(FreshVerdict::ReceiverImpaired), "receiver_impaired")
                  == 0, "receiver_impaired does not report itself by name");

        /* ⛔ IMPAIRMENT SUPPRESSES A NEGATIVE CLAIM. IT NEVER MANUFACTURES ONE.
         * A namespace we positively heard inside its window stays FRESH. */
        CHECK(t.verdict("dradis/contacts", 1500) == FreshVerdict::Fresh,
              "a deaf receiver DEMOTED a namespace it had positively heard "
              "inside its window: %s — impairment must only remove standing "
              "to condemn, never invent a fault",
              V(t.verdict("dradis/contacts", 1500)));

        /* WireUnknown is a statement a publisher made and we RECEIVED, so it
         * also survives. */
        t.on_wire_unknown("dradis/contacts", 1000);
        CHECK(t.verdict("dradis/contacts", 5000) == FreshVerdict::WireUnknown,
              "a deaf receiver relabelled an explicit 0xFD it had already "
              "received: %s", V(t.verdict("dradis/contacts", 5000)));

        /* And a reset must NOT restore standing — impairment is a property of
         * the TRANSPORT, not of the scene. */
        t.reset();
        CHECK(t.receiver_impaired(),
              "a scene swap silently restored the receiver's standing to "
              "condemn a fleet it still cannot hear");
    }

    /* ============================================================== *
     * 20. THE HEARTBEAT BODY ARRIVES IN TWO QUOTE STYLES
     *     ⛔ MEASURED, NOT ASSUMED.  `publish_heartbeat` puts a DICT in
     *     `value`.  MQTT serialises it as JSON (double quotes);
     *     transport_uart.encode_value has NO dict branch, so on a framed
     *     substrate it falls through to `str(value)` and the Python REPR goes
     *     on the wire with SINGLE quotes.  The literal below is the exact
     *     string `encode_value` produced when run against the real payload.
     *
     *     A JSON-only parser finds no node there — and NO NODE MEANS NO
     *     VERDICT, so NODE_SILENT would have been unreachable on precisely the
     *     substrate the heartbeat work was for.
     * ============================================================== */
    {
        char node[kFreshnessNodeCap];
        uint32_t iv = 0;

        /* The MQTT form. */
        CHECK(freshness_parse_beat(
                  "{\"node\": \"world-app\", \"uptime_s\": 12345, "
                  "\"interval_s\": 5.0}", node, sizeof(node), &iv),
              "the JSON heartbeat form did not parse");
        CHECK(strcmp(node, "world-app") == 0, "JSON form node = '%s'", node);
        CHECK(iv == 5000, "JSON form interval = %u ms, want 5000", iv);

        /* The FRAMED form — verbatim from transport_uart.encode_value. */
        iv = 0;
        CHECK(freshness_parse_beat(
                  "{'node': 'world-app', 'uptime_s': 12345, "
                  "'interval_s': 5.0}", node, sizeof(node), &iv),
              "⛔ the PYTHON-REPR heartbeat form did not parse — this is what "
              "actually rides a UART/CAN wire, so NODE_SILENT would be "
              "unreachable on every framed substrate");
        CHECK(strcmp(node, "world-app") == 0, "repr form node = '%s'", node);
        CHECK(iv == 5000, "repr form interval = %u ms, want 5000", iv);

        /* THE CADENCE RIDES ON THE BEAT — a node declaring 1 s must be judged
         * at 3 s, not at somebody else's 5. */
        iv = 0;
        freshness_parse_beat("{'node': 'ctrl-02', 'interval_s': 1.0}", node,
                             sizeof(node), &iv);
        CHECK(iv == 1000, "a beat declaring 1.0 s parsed as %u ms", iv);

        /* ⛔ A BEAT WITH NO NODE IS REFUSED — a pulse with no patient. */
        CHECK(!freshness_parse_beat("{'uptime_s': 5, 'interval_s': 5.0}", node,
                                    sizeof(node), &iv),
              "a heartbeat naming NO node was accepted — it cannot mark "
              "anything dead and must not pretend to");
        CHECK(!freshness_parse_beat("", node, sizeof(node), &iv),
              "an empty heartbeat body was accepted");
        CHECK(!freshness_parse_beat(nullptr, node, sizeof(node), &iv),
              "a null heartbeat body was accepted");

        /* A beat that declares NO cadence yields 0, so the caller knows to
         * fall back rather than silently judging at zero. */
        iv = 99;
        CHECK(freshness_parse_beat("{'node': 'x'}", node, sizeof(node), &iv) &&
                  iv == 0,
              "a beat with no interval_s did not report 0 (got %u)", iv);
    }

    /* ============================================================== */
    printf("%d/%d checks pass\n", s_pass, s_pass + s_fail);
    if (s_fail) {
        printf("%d FAILED\n", s_fail);
        return 1;
    }
    printf("ALL PASS\n");
    return 0;
}

#endif /* FRESHNESS_CORPUS_TEST */
