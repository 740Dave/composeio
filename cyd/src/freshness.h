/*
 * freshness.h — the C++ LEAF FRESHNESS RECEIVER.
 * [SPEC:CXC-FRESHNESS] [CXC-FRESH-CPP-FOLLOWON] — the named follow-on that
 * 72-freshness-receiver.lts scoped OUT of the Python change and argued for
 * separately.  The SEMANTICS are ported from python/composeio/freshness.py
 * and python/composeio/namespace.py; the STRUCTURE is not (there is no
 * Namespace object on this tier, and no heap to build one on).
 *
 * WHAT IT IS.  A value that stopped arriving looks exactly like a value that
 * has not changed.  On the display tier that is a frozen number; on the
 * ACTUATOR tier it is worse — the x27.168 stepper needle is dead reckoning
 * with no feedback and LATCHES its position forever (x27168.h:76-81), so a
 * dead publisher leaves a physical needle pointing at a number that stopped
 * being true hours ago, with every machine signal on the board agreeing.
 *
 * ⛔ THE GOVERNING CONSTRAINT — ZERO CONSUMER CODE.  David: "we shouldn't
 * have to program for that, it should just happen because a new state has
 * been emitted."  The verdict is therefore published as ORDINARY STATE on a
 * sibling namespace and every consumer reacts through the bindings it
 * already has.  There is no `if (stale)` in any widget, any draw path, any
 * gauge dispatch or any binding applier, and the acceptance test for this
 * module is that you cannot point at one.
 *
 *     <ns>/_fresh      BOOLEAN — believable / not believable.
 *     <ns>/_verdict    the six-case lowercase string, for an operator.
 *
 * WHY THE BOOLEAN IS THE LOAD-BEARING ONE.  The DSL reads an UNSET namespace
 * as 0.0 (dsl.h, `ns()` — "an unset namespace reads 0.0"), and 0.0 is falsy.
 * So a binding written `expr: ns("x") * ns("x/_fresh")` — or any guard shaped
 * like it — FAILS SAFE BY CONSTRUCTION on a board where this receiver never
 * ran, never declared that namespace, or crashed before its first tick.  An
 * absent verdict is a disbelieved value, which is the only direction of
 * failure that cannot lie.  A `_stale` flag would have the opposite polarity
 * and would silently certify everything.
 *
 * ⛔ INTERNAL SCOPE ONLY.  A verdict is NEVER uplinked to the shared bus.
 * Two receivers may legitimately disagree — different declared intervals,
 * different clocks, different link quality — so publishing one node's verdict
 * globally would present one receiver's OPINION as a fact about the
 * namespace.  Worse, a verdict that crosses the wire becomes a RECEIVED VALUE
 * WITH ITS OWN AGE, and the regress has no bottom.  [CXC-FRESH-ASYMMETRY]:
 * freshness is a RECEIVER's judgement and never a publisher's.
 *
 * ⛔ LAZY, AND NOTHING SWEEPS ([CXC-FRESH-LAZY]).  There is no timer here and
 * no FreeRTOS task.  `verdict()` is computed at call time from a monotonic
 * millisecond stamp, and the only thing that walks the table is
 * `emit_transitions()`, called from a loop the firmware ALREADY runs (the
 * render loop's tick, beside announce_tick / config_bus_tick).  D4's reason:
 * "a timer-driven sweep means a stalled receiver demotes the whole world and
 * blames the world".  A wedged render task must not manufacture a fleet-wide
 * outage out of its own stall — and under lazy evaluation it structurally
 * cannot, because a receiver that is not reading is not judging.
 *
 * Pure C++ (no ESP-IDF, no Arduino, no logging) so it host-compiles for
 * env:native_freshness — same idiom as dsl.h / restart.h / bus_frame.h.
 * Everything that needs to LOG lives in the app shell that drives it.
 */

#ifndef FRESHNESS_H
#define FRESHNESS_H

#include <cstddef>
#include <cstdint>

/* ------------------------------------------------------------------ */
/* The verdict — six cases, because a boolean cannot say WHY           */
/* ------------------------------------------------------------------ */

/* [CXC-FRESH-VERDICT].  The four failure cases call for four different
 * operator responses: a NeverReceived namespace may simply be early in boot,
 * a Stale one has a publisher that died, a NodeSilent one has a whole board
 * gone, and an Undeclared one was never judged at all.  Collapsing them hands
 * the glass one word where the operator needs four.
 *
 * ⛔ Undeclared IS NOT A SYNONYM FOR Fresh.  Reporting it as Fresh is how a
 * system claims confidence it never earned; it means the question was never
 * asked.  It is its own case and is counted separately. */
enum class FreshVerdict : uint8_t {
    Fresh = 0,        /* received within its declared window                */
    Stale,            /* declared a cadence; nothing arrived within 3x it   */
    WireUnknown,      /* the publisher sent an explicit 0xFD — alive, and
                       * knowingly without a value                          */
    NeverReceived,    /* declared a cadence; nothing has EVER arrived       */
    NodeSilent,       /* quiet-by-construction, and its NODE stopped beating */
    Undeclared,       /* no contract, so no verdict was ever authorised     */

    /* ⛔ THE SEVENTH CASE, AND IT IS ABOUT US, NOT THEM.
     *
     * `stale` and `node_silent` are CLAIMS ABOUT OTHER NODES.  A node in
     * bus-off receives nothing at all, so every namespace times out at once
     * and the leaf renders "the entire fleet is dead" — a confident, uniform,
     * completely wrong report, and uniformity is exactly what makes it
     * invisible.  [CXC-FRESH-LAZY]'s rationale does NOT close this door: the
     * argument there is that a receiver which is not READING is not judging,
     * and a bus-off receiver IS reading.  The render loop still ticks.  It
     * just is not hearing anything.
     *
     * So a receiver that cannot HEAR loses the STANDING TO CONDEMN.  Note the
     * polarity carefully — impairment SUPPRESSES a negative claim, it never
     * manufactures one.  A namespace that is genuinely Fresh (we heard it
     * inside its window, which is a positive fact) stays Fresh on an impaired
     * link; only Stale / NeverReceived / NodeSilent are demoted to this.
     *
     * ⚠ The VALUE is still not believable — it really is old.  What changes is
     * WHO the operator blames, and that is the difference between replacing a
     * publisher and replacing a transceiver. */
    ReceiverImpaired,
};

/* The lowercase six-case string published on `<ns>/_verdict`.  These are the
 * SAME tokens as python/composeio/freshness.py's `Verdict` values — an
 * operator reading a Python monitor and a firmware board must not have to
 * translate. */
const char* freshness_verdict_name(FreshVerdict v);

/* Whether a verdict permits believing the stored value.  Mirrors
 * freshness.verdict_is_believable: Fresh and Undeclared are believable-ish
 * (Undeclared because no judgement was authorised, NOT because it is good),
 * the other four are not.  This is what `<ns>/_fresh` carries. */
bool freshness_believable(FreshVerdict v);

/* ------------------------------------------------------------------ */
/* The declaration — an interval is DECLARED, never inferred           */
/* ------------------------------------------------------------------ */

/* Which of D4's three cases a namespace declared itself to be.  Mirrors
 * python/composeio/namespace.py `Cadence`. */
enum class FreshCadence : uint8_t {
    Undeclared = 0,   /* no freshness: block at all                        */
    Periodic,         /* refresh_interval declared                          */
    EventDriven,      /* quiet_by_construction — silence is the DESIGN     */
};

/* ⛔ THE 3x IS A J1939 CONVENTION, not a requirement.  It is the miss-count
 * that heavy-vehicle networks settled on for a periodic PGN, and it is used
 * here because a convention with fleet history beats a number somebody
 * invented in a design meeting.  Saying "convention" in the spec and in this
 * comment is deliberate: a reader who believes it is derived will not think
 * to change it when a link with different loss characteristics arrives. */
static constexpr uint32_t kFreshnessMultiplier = 3;

/* The Python tier's floor term in `max(3*interval, interval + 0.1s)`.  It
 * stops a FAST cadence producing a timeout that lands inside its own jitter.
 * ⛔ It is `interval + 100 ms`, not a flat 100 ms: a flat floor would be BELOW
 * 3x for every interval over 50 ms and would do nothing where it was needed. */
static constexpr uint32_t kFreshnessFloorMs = 100;

/* How often the leaf's evaluator is allowed to WALK the table.  Declared here
 * rather than beside the tick BECAUSE IT IS AN INPUT TO THE TIMEOUT, not an
 * unrelated constant — see freshness_timeout_for(). */
static constexpr uint32_t kFreshnessEvalIntervalMs = 250;

/* `max(3*interval, interval + max(100 ms, eval_period))`.
 *
 * ⛔ THE SAMPLER'S PERIOD IS PART OF THE FORMULA, AND OMITTING IT FLAPS.
 * `interval + 0.1 s` is a PYTHON-TIER number: it assumes a reader that
 * evaluates on demand, sub-millisecond, whenever anyone asks.  On a leaf the
 * reader IS the tick.  With a 100 ms floor and a 250 ms tick, a namespace
 * declared at 30 ms gets a 130 ms timeout that is only ever SAMPLED every
 * 250 ms — so whether it reads Fresh or Stale depends on where the tick
 * happens to land relative to the last arrival, and a perfectly healthy
 * publisher produces fresh/stale/fresh forever.  That is a false alarm
 * generated entirely by the instrument, on the fastest and therefore most
 * important signals, which is the same class of defect as condemning a
 * quiet-by-design namespace — and it passes every "did it go stale?" test.
 *
 * Making the sampler's period a TERM means the timeout can never be shorter
 * than the interval at which anyone looks, by construction rather than by a
 * matching pair of constants somebody has to remember to keep in step.
 *
 * ⚠ WHAT THIS DOES *NOT* BUY: sub-tick RESOLUTION.  A 30 ms namespace now has
 * a 280 ms timeout, so ~9 consecutive misses are needed before it is
 * condemned.  That is a real loss of sensitivity and it is the honest price of
 * a 250 ms sampler; it is reported (see `sampling_limited()`), not hidden. */
uint32_t freshness_timeout_for(uint32_t interval_ms,
                               uint32_t eval_period_ms = kFreshnessEvalIntervalMs);

/* ------------------------------------------------------------------ */
/* Capacity — and what happens at N+1                                  */
/* ------------------------------------------------------------------ */

/* ⛔ THIS NUMBER IS NOT SIZED FOR TODAY, ON PURPOSE.  `kMaxPersist` was 4 —
 * exactly the size of the control plane the day it was written — and it broke
 * the moment a fifth namespace arrived, announcing the fact in ONE WARN line
 * that nobody was reading.  A scene can hold kMaxSceneBindings = 24 bindings,
 * so 24 is today's ceiling on DISTINCT bound namespaces; this is 40, and the
 * headroom is for the heartbeat namespaces and the `freshness:` declarations
 * that name namespaces no widget binds (a class member, a liveness-only
 * signal).
 *
 * WHAT HAPPENS AT N+1, asked explicitly because the failure mode of a bounded
 * table is normally a silent overwrite: `declare()` REFUSES.  It writes
 * nothing, returns false, and increments `refused()` — and the app shell
 * turns that into a LOUD boot-time error naming the namespace.  The refused
 * namespace is then simply not in the table, so its verdict is Undeclared and
 * `<ns>/_fresh` is never published — which the DSL reads as 0.0, DISBELIEVED.
 * An over-capacity receiver therefore fails toward "do not trust me", never
 * toward a green light for a namespace it is not watching. */
static constexpr int kMaxFreshnessEntries = 40;

/* Nodes whose heartbeat this receiver tracks.  A leaf depends on a handful of
 * publishers; 12 is far past any scene in the tree and the same refusal rule
 * applies. */
static constexpr int kMaxFreshnessNodes = 12;

/* Freshness CLASSES and their members ([CXC-FRESH-CLASS]).  Same refusal rule
 * at N+1; a class that will not fit is not declared, so it is Undeclared and
 * publishes nothing, and the DSL reads its absent guard as 0.0 — disbelieved. */
static constexpr int kMaxFreshnessClasses = 8;
static constexpr int kMaxFreshnessClassMembers = 8;
static constexpr size_t kFreshnessClassNameCap = 48;

/* Sized to BusEvent::ns (bus_transport.h) EXACTLY.  A shorter buffer here
 * would truncate a long namespace and then key the table on the truncation —
 * two namespaces sharing one row, which is the silent-substitution failure
 * that is worse than a loss because it looks well-formed. */
static constexpr size_t kFreshnessNsCap = 96;
static constexpr size_t kFreshnessNodeCap = 40;

/* The sibling suffixes.  `_` is the established reserved prefix on this bus
 * (`_bus/time`, `_device/<id>/restart`), so a widget namespace can never
 * collide with a verdict namespace by accident. */
extern const char kFreshSuffix[];        /* "/_fresh"   */
extern const char kVerdictSuffix[];      /* "/_verdict" */

/* ------------------------------------------------------------------ */
/* The table                                                           */
/* ------------------------------------------------------------------ */

class FreshnessTable {
public:
    /* DECLARE a namespace's freshness contract.  Returns false — writing
     * NOTHING — on a full table, an unusable namespace, or a namespace that
     * is itself a verdict sibling (which would make the receiver judge its
     * own output; see the infinite-regress note in the header comment).
     *
     * `interval_ms` is meaningful only for Periodic.  `timeout_ms` of 0 means
     * "derive it" via freshness_timeout_for(); a non-zero value is an
     * explicit `freshness: {timeout: ...}` override.
     *
     * ⛔ `node` is the ATTRIBUTION and it may be null or empty.  NO
     * ATTRIBUTED PUBLISHER MEANS NO VERDICT FROM A HEARTBEAT
     * ([CXC-FRESH-HEARTBEAT-RX]): a namespace with no identified source has
     * no node to condemn, and condemning it anyway would mark every
     * locally-published namespace dead the moment this ships. */
    bool declare(const char* ns, FreshCadence cadence, uint32_t interval_ms,
                 uint32_t timeout_ms, const char* node);

    /* Stamp a receipt.  ⛔ ON RECEIPT, NOT ON CHANGE ([CXC-FRESH-RECEIPT]).
     * A republish of an IDENTICAL value must refresh the age.  An age counter
     * driven by a change callback never sees a publisher faithfully restating
     * a steady value, so it would expire a transmitter that is working
     * perfectly — and it would do so ONLY for the steadiest signals in the
     * fleet, which are exactly the ones an operator most expects to trust.
     *
     * ⛔ THE VALUE IS ACCEPTED AND DELIBERATELY IGNORED.  It is a parameter
     * only so the mutation harness can BUILD the wrong receiver (stamp on
     * change) on the REAL call path and watch the suite go red; a signature
     * that could not see the value would make the defect unbuildable here and
     * therefore untestable, while leaving it perfectly buildable in the next
     * port.  In a shipping build the argument is unused and the receipt rule
     * holds STRUCTURALLY, not by discipline.
     *
     * Undeclared namespaces are ignored (cheap: one strcmp miss). */
    void on_arrival(const char* ns, uint32_t now_ms, double value = 0.0);

    /* An explicit wire `unknown` (tag 0xFD) arrived: the publisher is ALIVE
     * and knowingly without a value.  Stamps a receipt too — the peer spoke —
     * and latches WireUnknown until a real value arrives. */
    void on_wire_unknown(const char* ns, uint32_t now_ms);

    /* Record a beat from `node` at the cadence THE BEAT ITSELF DECLARED.
     * `interval_ms` of 0 falls back to the caller's default; the fallback is
     * the "second copy that eventually disagrees" the spec warns about, so
     * the app shell names its source when it uses one. */
    void note_heartbeat(const char* node, uint32_t now_ms, uint32_t interval_ms);

    /* Has this node EVER beaten?  Distinct from alive: the heartbeat may only
     * ever REMOVE confidence from a node POSITIVELY IDENTIFIED AND THEN
     * POSITIVELY LOST. */
    bool node_seen(const char* node) const;

    /* ⛔ A NODE NEVER HEARD FROM IS NOT ALIVE.  The tempting default is "no
     * evidence of death", and it certifies every node in the fleet — including
     * ones that never boot.  Absence of evidence is not evidence of liveness. */
    bool node_alive(const char* node, uint32_t now_ms) const;

    /* THE VERDICT, computed at call time.  Nothing sweeps, nothing latches
     * except WireUnknown (which a real arrival clears), and when a publisher
     * returns the next receipt makes the next read Fresh with no reset step
     * for anyone to forget. */
    FreshVerdict verdict(const char* ns, uint32_t now_ms) const;

    /* Milliseconds since the last receipt; UINT32_MAX for never / undeclared. */
    uint32_t age_ms(const char* ns, uint32_t now_ms) const;

    /* ---- freshness CLASSES  [CXC-FRESH-CLASS] -------------------------- */

    /* A NAMED GROUP that must ALL be believable together.  David, ratifying
     * D4, verbatim: "or a simple grouping of semantic messages that must all
     * have a specific value within the bounding window defined as a class."
     *
     * ⛔ WHY THIS IS NOT A NICETY ON *THIS* TIER.  On the Python tier
     * [CXC-FRESH-UNKNOWN-ARITH] makes `unknown` PROPAGATE through arithmetic,
     * so a derived value over three inputs is automatically unbelievable if
     * any one of them is — D4b's rule holding for free wherever a transform
     * does maths.  THAT PROPAGATION IS STRUCTURALLY ABSENT HERE: DslValue is
     * Number | Bool | String and has no unknown member, so
     * `expr: ns('a') + ns('b')` is silently UNGUARDED.
     *
     * Without a class the author must hand-write
     * `ns('a/_fresh') and ns('b/_fresh') ? ... : ...` for EVERY input, and
     * nothing anywhere checks the omission.  That is not zero-code, it is
     * zero-REVIEWABLE-code: the missing conjunct is invisible in review and
     * surfaces as a confidently-wrong gauge.  A class collapses N guard terms
     * into ONE — `ns('nav_solution/_fresh')` — which is a term a reviewer can
     * see is missing.
     *
     * THE TWO RATIFIED RULES: age is the MAX of the members' ages, and the
     * class is unbelievable if ANY member is.  A navigation solution is not
     * fresh because its NEWEST input is fresh; it is as old as its OLDEST
     * input, and it is not a solution at all if one input is missing.  Taking
     * the max is what stops the freshest member laundering the staleness of
     * the others.
     *
     * TWO REFUSALS, BOTH BECAUSE THE FAILURE DIRECTION IS *OPEN*:
     *   - an EMPTY class would report FRESH forever — a green light wired to
     *     nothing;
     *   - a class naming an UNDECLARED namespace would report FRESH for the
     *     member nobody is watching, so the one namespace most likely to die
     *     unnoticed is the one it certifies.
     * Both write NOTHING and count in refused(). */
    bool declare_class(const char* name, const char* const* members,
                       size_t member_count);

    /* Fresh only if EVERY member is believable; otherwise the FIRST
     * unbelievable member's OWN verdict, so the class reports WHY rather than
     * merely THAT. */
    FreshVerdict class_verdict(const char* name, uint32_t now_ms) const;

    /* The MAX of the members' ages.  UINT32_MAX if ANY member has no age at
     * all — an input never received makes the derivation unknown rather than
     * merely old, so it DOMINATES instead of being skipped. */
    uint32_t class_age_ms(const char* name, uint32_t now_ms) const;

    /* WHICH member broke the class.  "" when none did.  An operator needs to
     * know which sensor died, not merely that the solution is bad. */
    const char* class_culprit(const char* name, uint32_t now_ms) const;

    int class_count() const { return _class_count; }
    const char* class_name_at(int i) const;

    /* ---- the receiver's OWN standing to judge ------------------------- */

    /* ⛔ TRUE MEANS "THIS RECEIVER CANNOT HEAR", NOT "SOMETHING IS ODD".
     *
     * The instrument already exists and reads real hardware:
     * CanTransport::link_health() reads TEC/REC out of the TWAI controller.
     * But `link_health() != kAnnLinkOk` IS THE WRONG PREDICATE, and getting it
     * wrong is fatal in the other direction: UartTransport::link_health()
     * returns kAnnLinkUnknown ALWAYS, and correctly — a UART port being open
     * says nothing about the far end, so "I have no error model" is a true and
     * useful answer.  Treating Unknown as impaired would make every UART leaf
     * refuse to judge anything, forever: a receiver that never speaks, which
     * is precisely the do-nothing instrument this whole module exists to
     * prevent.
     *
     * So: UNKNOWN MEANS "NO OPINION, PROCEED".  Only a POSITIVELY BAD link
     * (down / stopped / recovering) removes standing.  See
     * freshness_link_impaired() for the mapping, which is the one place that
     * knows the kAnnLink* vocabulary. */
    typedef bool (*ImpairedFn)(void* user);
    void set_impaired_probe(ImpairedFn fn, void* user);
    bool receiver_impaired() const;

    /* ---- the EDGE-TRIGGERED emitter ----------------------------------- */

    /* Called once per CHANGED namespace.  `fresh` is the boolean for
     * `<ns>/_fresh`; `verdict_name` is the six-case string for
     * `<ns>/_verdict`; `verdict_changed` is false when only the boolean is
     * new (it never is — a boolean change implies a verdict change — but the
     * converse happens constantly, e.g. Stale -> NodeSilent, and the sink
     * uses it to skip a redundant boolean publish). */
    typedef void (*EmitFn)(void* user, const char* ns, bool fresh,
                           bool fresh_changed, const char* verdict_name,
                           const char* culprit);

    /* Walk the table, compare each verdict to the last one PUBLISHED, and
     * call `fn` for the ones that moved.  Returns how many transitioned.
     *
     * ⛔ EDGE-TRIGGERED, NEVER PER-TICK.  This codebase has already paid for a
     * "diagnostic that causes the failure it reports": 25 ESP_LOGW calls in
     * 60 ms delayed the very ring-draining the log was reporting on.  A
     * receiver that republished 24 verdicts every render pass would flood its
     * own bus drain and then report the resulting staleness as a fault in the
     * publishers.  A verdict transition is rare BY CONSTRUCTION — once Stale,
     * a namespace stays Stale until a receipt arrives, and a receipt buys a
     * full timeout of Fresh — so the emission rate is bounded by the
     * publisher's own cadence and cannot exceed it.
     *
     * The caller additionally rate-limits how often this WALK happens (see
     * kFreshnessEvalIntervalMs); the walk is O(entries) string-free integer
     * comparisons, but "cheap" is not "free" at 60 Hz. */
    int emit_transitions(uint32_t now_ms, EmitFn fn, void* user);

    /* ---- introspection, for the boot banner and the status line -------- */

    int count() const { return _count; }
    const char* ns_at(int i) const;
    const char* node_at(int i) const;
    FreshCadence cadence_at(int i) const;
    uint32_t timeout_ms_at(int i) const;

    /* How many declarations were REFUSED (table full / malformed / a verdict
     * sibling / an unhonourable explicit timeout / an empty or open class).
     * Non-zero is a boot-time error, not a debug counter: it means some
     * namespace on this board is being watched by nothing. */
    uint32_t refused() const { return _refused; }

    /* Declarations whose cadence is FASTER THAN THE SAMPLER, so their timeout
     * was floored to the sampler's period and they can no longer detect a
     * SINGLE missed message — only ~ceil(eval_period/interval) of them.
     *
     * ⚠ NOT a refusal, and the distinction is deliberate.  Refusing would
     * leave a legitimately-fast namespace watched by nothing; flooring keeps
     * it watched and loses only SENSITIVITY, which is the direction that
     * cannot invent a false alarm.  It is counted so the loss is visible
     * rather than being the silent difference between two constants. */
    uint32_t sampling_limited() const { return _sampling_limited; }

    /* Fill `out` with `_device/<node>/heartbeat` for every DISTINCT node any
     * declaration attributed, and return the count.  The app shell appends
     * these to its subscription set — on a FRAMED substrate an inbound frame
     * is matched by ns HASH against the registered set, so a heartbeat from a
     * node this board never declared literally cannot be named on arrival
     * (a CRC-16 does not invert).  Declaring the node is what makes the beat
     * receivable, which is the same rule stated from the other end: no
     * attribution, no verdict.  Strings are owned by the table. */
    size_t heartbeat_namespaces(const char** out, size_t cap) const;

    /* Reset everything (scene swap / test). */
    void reset();

private:
    struct Entry {
        char ns[kFreshnessNsCap];
        char node[kFreshnessNodeCap];
        uint32_t interval_ms;
        uint32_t timeout_ms;
        uint32_t last_receipt_ms;
        FreshCadence cadence;
        bool have_receipt;
        bool wire_unknown;
        double last_value;            /* MUTATION HARNESS ONLY — see .cpp  */
        bool have_published;          /* has a verdict ever gone out?      */
        FreshVerdict published;       /* the last verdict PUBLISHED        */
    };
    struct Node {
        char id[kFreshnessNodeCap];
        char ns[kFreshnessNsCap];     /* "_device/<id>/heartbeat"          */
        uint32_t last_beat_ms;
        uint32_t interval_ms;
        bool seen;
    };

    struct Class {
        char name[kFreshnessClassNameCap];
        char members[kMaxFreshnessClassMembers][kFreshnessNsCap];
        int member_count;
        bool have_published;
        FreshVerdict published;
    };

    Entry _e[kMaxFreshnessEntries];
    Node _n[kMaxFreshnessNodes];
    Class _c[kMaxFreshnessClasses];
    int _count = 0;
    int _node_count = 0;
    int _class_count = 0;
    uint32_t _refused = 0;
    uint32_t _sampling_limited = 0;
    ImpairedFn _impaired_fn = nullptr;
    void* _impaired_user = nullptr;

    int _find(const char* ns) const;
    int _find_node(const char* node) const;
    int _find_class(const char* name) const;
    int _intern_node(const char* node);
    FreshVerdict _raw_verdict(const Entry& e, uint32_t now_ms) const;
};

/* kAnnLink* -> "this receiver has lost the standing to condemn other nodes".
 *
 * ⛔ THE ONLY TWO VALUES THAT MUST *NOT* MAP TO TRUE ARE Ok AND UNKNOWN, and
 * Unknown is the one that matters.  `link_health()`'s default is Unknown
 * BECAUSE a substrate may genuinely have no error model (bus_uart.cpp returns
 * it unconditionally and is right to).  Mapping Unknown to impaired would
 * silence the receiver on every UART leaf in the fleet — shipping an
 * instrument that never reports anything, which reads as "all clear".
 *
 * Warning is also NOT impaired: error counters climbing is a link that is
 * still passing traffic, and demoting real verdicts on it would hide a genuine
 * publisher death behind a transient burst.  Only down / stopped / recovering
 * — states in which frames demonstrably are NOT arriving — remove standing.
 *
 * Lives in the header (and takes a plain uint8_t) so freshness.cpp stays free
 * of announce_pack.h and still host-compiles. */
bool freshness_link_impaired(uint8_t ann_link_health);

/* ------------------------------------------------------------------ */
/* The app-shell facade — ONE receiver per boot                        */
/* ------------------------------------------------------------------ */

/* The receiver for this boot, resolved exactly like bus_transport(): one
 * instance, no ownership question, reachable from the drain and the tick
 * without either of them having to hold a pointer. */
FreshnessTable& freshness();

/* (kFreshnessEvalIntervalMs is declared beside freshness_timeout_for(),
 * because it is a TERM IN THE TIMEOUT and not merely the tick's own business.
 * 4 Hz: fast enough that a verdict reaches the glass within a quarter second
 * of becoming true, slow enough that a 60 Hz render loop pays for it 4 times a
 * second instead of 60.  It bounds the LATENCY of a verdict, not its
 * CORRECTNESS — verdict() is exact at any instant it is asked.) */

/* Declare from a parsed scene YAML.  Reads the SAME schema
 * python/composeio/config.py reads, so a namespace's contract is written once
 * and means the same thing on both tiers:
 *
 *   namespaces:
 *     - name: dradis/contacts
 *       freshness: {refresh_interval: 1.0, node: world-app}
 *     - name: throttle/left
 *       freshness: {quiet_by_construction: true, node: ctrl-01}
 *     - name: slow/thing
 *       freshness: {refresh_interval: 5.0, timeout: 60.0}
 *   freshness_classes:
 *     - name: nav_solution
 *       members: [gps/fix, imu/heading, log/speed]
 *
 * (`node:` is this tier's addition and is the ATTRIBUTION — the Python tier
 * takes it from the envelope's `src`, which a framed substrate does not
 * carry.  Without it a quiet-by-construction namespace has no node to
 * condemn and is simply always Fresh, which is the correct failure direction
 * and is asserted as such in the selftest.)
 *
 * Returns the number DECLARED; `freshness().refused()` reports the rest, and
 * `contradictions` counts declarations that said BOTH quiet_by_construction
 * AND refresh_interval — a contradiction that is merely skipped is a
 * declaration the author believes they made.  Implemented in
 * freshness_yaml.cpp so freshness.cpp depends on nothing at all. */
struct YamlNode;
int freshness_declare_from_yaml(FreshnessTable& t, const YamlNode* root,
                                int* contradictions = nullptr);

/* PARSE A HEARTBEAT BODY — the `node` and the cadence THE BEAT DECLARED.
 * False when the body names no node, because "a beat that does not say whose
 * it is cannot mark a node dead; it is a pulse with no patient".
 * `*interval_ms` is 0 when the beat declared no cadence.
 *
 * ⛔ IT MUST ACCEPT TWO QUOTE STYLES, AND THAT IS A MEASURED FACT, NOT
 * DEFENSIVENESS.  `ComposeIOBus.publish_heartbeat` puts a Python DICT in
 * `value`.  On MQTT that is serialised as real JSON — `{"node": "world-app"}`,
 * DOUBLE-quoted.  On a FRAMED substrate `transport_uart.encode_value` has no
 * dict branch, so the dict falls through to `str(value)` and goes on the wire
 * as the Python REPR — `{'node': 'world-app'}`, SINGLE-quoted.  A parser that
 * only knows JSON finds no node on UART, and no node means no verdict, so
 * NODE_SILENT would be unreachable on exactly the substrate the heartbeat work
 * was for.  Found by ENCODING the real payload, not by reading the publisher.
 *
 * Lives here (pure C++) rather than in freshness_bus.cpp so both wire forms
 * are HOST-TESTABLE; the ESP-IDF file only routes. */
bool freshness_parse_beat(const char* body, char* node, size_t node_cap,
                          uint32_t* interval_ms);

/* Wire the receiver's standing-to-judge to the SELECTED transport's real link
 * instrument.  Call ONCE after bus_transport().start(); it survives scene
 * swaps, because impairment is a property of the transport and not of the
 * composition. */
void freshness_attach_link_probe(void);

/* ---- the two seams the app shell calls ---- */

/* Stamp EVERY inbound arrival.  ⛔ CALLED BEFORE THE CONTROL-PLANE SIEVE
 * ([CXC-FRESH-HEARTBEAT-RX] "ordering"): the freshness receiver sees every
 * inbound message FIRST, because a peer whose value a later stage rejects is
 * still a peer that is ALIVE, and losing its attribution because its data was
 * malformed would condemn it for the wrong reason.  Consumes nothing and
 * returns nothing — it is not part of the sieve. */
void freshness_note_arrival(const char* ns, uint32_t now_ms);

/* A wire `unknown` (0xFD) arrived on `ns`.  Consumed here — see
 * BusEvent::unknown in bus_transport.h for why it is a flag on the envelope
 * and NOT a fourth BusEvent::Kind. */
void freshness_note_wire_unknown(const char* ns, uint32_t now_ms);

/* THE HEARTBEAT SIEVE ENTRY.  True iff this arrival was a heartbeat and was
 * CONSUMED here — the caller must then NOT route it to the scene, exactly as
 * it already does for bus_transport / announce / config_source commands.
 * That is the answer to "how do heartbeats reach the receiver without
 * flooding the scene binding path": they reach the queue, and the sieve eats
 * them one stage before the scene ever sees them.
 *
 * Two forms are understood, and only two:
 *   `_bus/heartbeat`            String, a JSON object — the Python form
 *                               (bus.py publish_heartbeat).  The cadence
 *                               RIDES ON THE BEAT as `interval_s`, which is
 *                               the whole point: the receiver never needs a
 *                               configured second copy.
 *   `_device/<id>/heartbeat`    Number, uptime seconds — the C++ fleet form
 *                               (announce.cpp:368).  ⚠ THIS FORM DOES NOT
 *                               DECLARE ITS CADENCE, so the receiver falls
 *                               back to kAnnHeartbeatIntervalMs.  That IS the
 *                               second copy, and it is named here rather than
 *                               hidden; a static_assert in freshness_bus.cpp
 *                               keeps the two in step WITHIN this repo, and a
 *                               peer that beats at some other rate is a
 *                               residual risk this tier cannot see.
 * `kind` mirrors BusEvent::Kind (0=Number, 1=Bool, 2=String). */
bool freshness_consume_heartbeat(const char* ns, int kind, double num,
                                 const char* str, uint32_t now_ms);

/* THE TICK.  Call from a loop the firmware ALREADY runs — beside
 * announce_tick() / config_bus_tick().  ⛔ NO NEW TIMER AND NO NEW FreeRTOS
 * TASK: expiry is lazy, and this is the read that makes it observable.
 *
 * `sink` is called once per TRANSITION and publishes the verdict through the
 * IDENTICAL entry point a real arrival uses — scene_on_bus_value() on the
 * display tier, GaugeBindings::dispatch() on the actuator tier.  There is no
 * parallel delivery path, which is what makes "it just happens because a new
 * state has been emitted" literally true rather than approximately true. */
typedef void (*FreshnessSink)(void* user, const char* ns, int kind,
                              double num, bool b, const char* str);
int freshness_tick(uint32_t now_ms, FreshnessSink sink, void* user);

#endif /* FRESHNESS_H */
