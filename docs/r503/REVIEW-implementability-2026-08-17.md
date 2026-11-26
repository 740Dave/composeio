# Implementability review — specs 70 / 71 / 72

**Reviewer**: independent spec-vs-build verifier (ComposePlatform QA), reading the specs cold.
**Date**: 2026-08-17
**Artifact under review**: the SPECS' fitness to be built from. There is no build.

---

## 1. VERDICT

# IMPLEMENTABLE WITH NAMED GAPS

**Lead with this, because it is the single thing most likely to cause a wrong build:**
`[DD-FPHAL-HOST-OR-VM-BINDING]` ruled on 2026-08-16 that **the device owns the driver AND the HAL**, and
that `fingerprint_storage`, the editor and the enrollment station are ordinary bus clients that
**"NEVER call a HAL method"** (`72:3`, `72:936`, `72:948`). That ruling relocated the entire consumer-facing
interface from eleven typed method signatures onto **bus messages** — and the corpus specifies bus messages
for **exactly one of the eleven operations** (`[CXC-FPHAL-ENROLLMENT-MESSAGE-SEQUENCE]`, `72:281-323`, enroll
only). The roster push, the occupancy read, the roster stamp read/write, the template readback that is the
*only* thing that can prove identity, and the `capabilities()` announcement that a stated INVARIANT depends
on, all have named off-device consumers and **no wire**. All three specs then put the roster wire protocol
explicitly OUT of scope (`71:4`, `72:4`), so no document in the corpus owns it.

The consequence is not "some detail is missing." It is that a builder opening `fingerprint_storage.py`
tomorrow must **invent the whole service⇄device protocol**, and every choice it makes crosses the seam the
three specs were written to protect. Worse, the invention is *invisible* as an invention: the specs read as
complete because they specify the HAL exhaustively, and the HAL is the thing nobody above the bus can reach.

**Why the verdict is still IMPLEMENTABLE WITH NAMED GAPS and not NOT-YET-IMPLEMENTABLE.** This is one
missing artifact, not thirty holes. Every input that artifact needs is already ruled: desired-state-only
sync, explicit slots, service-owned allocation, per-template framing, stamp-after-last-write ordering,
device-addressed events, retained last-value-wins status, the six-axis failure taxonomy, opaque templates
plus `template_sha256`. A spec 73 — *the fingerprint bus contract* — is a transcription job of the same kind
spec 72 was, and spec 72 proves the authors can do it well. Two of the six targets (`R503HAL`, the device
firmware) are buildable **today** and are among the best-specified peripheral work I have read: eight
containment invariants each tied to a *cited* defect with a *tell*, and an opcode-per-operation table where
every cell carries an answer. Three of the six targets are blocked on one document.

**Per target, in one line each:**

| Target | Can an agent open an empty file and write it? |
|---|---|
| 1. `MockFingerprintHAL` | **Mostly yes** — 11 signatures + a per-operation behaviour table + 8 faults. But its *restored* third job (test double for bus clients) is unbuildable: a Protocol implementation cannot serve a consumer that speaks messages. |
| 2. `R503HAL` | **Yes.** Best-specified target in the set. Named gaps are all labelled as gaps by the spec itself. |
| 3. `fingerprint_storage` | **No.** Zero API, zero wire protocol, zero message shapes for eight of its operations. |
| 4. enrollment station | **No.** One command shape (`fingerprint/cmd/enroll {slot, ordinal}`), un-addressed to any device, and no way to obtain the slot. |
| 5. editor | **No.** Nine required bus operations named (`71:983`), zero shapes for any of them. |
| 6. device firmware (driver + HAL) | **Yes** — with one real ambiguity (enroll-status → feedback-state mapping) and one silent hazard (slot index base). |

---

## 2. COVERAGE

**Read in full, every line:**

- `${DATASHEETS_HOME}/R503/corpus/70-r503-peripheral.lts` — 1,261 lines. **All read.**
- `${DATASHEETS_HOME}/R503/corpus/71-fingerprint-identity.lts` — 1,855 lines. **All read.**
- `${DATASHEETS_HOME}/R503/corpus/72-fingerprint-hal-contract.lts` — 1,295 lines. **All read.**

Total 4,411 lines, including frontmatter, all code blocks, all tables, both Relationships blocks per file,
and the nodes appended after each file's Version/Status footer (`[CXC-R503-EV-25]`,
`[CXC-FPHAL-BOUNDARY-RATIONALE]`, `[CXC-FPHAL-CONTAINMENT-INVARIANTS]`,
`[CXC-FPHAL-POWER-OPTIMIZATION-LATER-PASS]`).

**Also read (context for judging, not under review):**
`~/.claude/agents/factlattice-gap-analyzer.md` (in full — required by the brief for Q2);
`[CXC-VISION-EPISTEMICS]` and `PRINCIPLE-*` in `intelligence-console`; dusk-gift problem P5 and its answer.

**Tools run:**
`lts_find(corpus="r503", prefix="DD")` and `…status="open"` (digest);
`lts_find(corpus="r503", text="write_template" | "MockFingerprintHAL")`;
`lts_find_in_prose(corpus="r503", text="R503HAL" | "MockFingerprintHAL")`;
`lts_node(corpus="r503", node_id="CXC-FPHAL-CONTAINMENT-INVARIANTS" | "CXC-R503-EV-25")`;
plus `grep` over the three files for namespace literals, EV/PRE node counts, status tokens, slot-base
statements, character-buffer statements, code-block symbol declarations and file paths.

**NOT read — say so plainly:**

- **The cross-corpus targets.** `[plt.cux:SPEC:CXC-HAL-DECLARE]` (spec 32), `[plt.cux:SPEC:CXC-HAL]` (spec 27),
  `[plt.cux:CXC-HALD-*]`, `[plt.cux:CXC-PERIPH-EVIDENCE-SLOTS]`, `[plt.cio:SPEC:DP-CIO-6]`,
  `[plt.cux:DX-*]`. Per the brief I did not chase these and do not report them as dangles. See §4 for the
  one place where the dependence is heavy enough to matter to a builder.
- **The R503 manual PDFs.** I did not verify a single datasheet citation against the source. Every
  `[CXC-R503-*]` DATASHEET FACT is taken on the spec's word; the spec's hash pinning
  (`[CXC-R503-SOURCE-PINNED]`) means someone else can.
- **`docs/hal-contract/DECISIONS-hal-contract-close.md`**, cited by eleven `dsd-source:` keys in spec 72. I
  did not open it, so every "David ruled X on 2026-08-17" is taken as transcribed.
- **`docs/fleet-identity-design-2026-08-15.md`** and `docs/DSD-spec-audit-2026-08-15.md`. Spec 71's
  frontmatter (`71:12`) says *"Where this file and docs/fleet-identity-design-2026-08-15.md §§15-19
  disagree, the design doc is NEWER and WINS."* **I could not check that**, and it is a live risk to any
  builder who reads only the `.lts` — see §6.
- **The six vendored driver trees** surveyed at `[CXC-R503-DRIVER-SURVEY]`. Every claim about FPM,
  R503Lib, pyfingerprint, esphome and the two Adafruit libraries is taken on the spec's word.

---

## 3. Q1 — CAN AN AGENT BUILD FROM THIS WITHOUT GUESSING?

Worked target by target. **AMBIGUOUS** = the spec says something admitting two actionable readings.
**ABSENT** = the spec says nothing and the builder must invent (marked *safe* = local and reversible, or
*load-bearing* = crosses a seam, hard to undo). **DELIBERATELY OPEN** = recorded as an open decision; not a
defect, listed only so the builder knows to stop rather than invent.

---

### Target 1 — `MockFingerprintHAL`

**Could an agent open an empty file and write it? Substantially yes, and this is the strongest "write it
now" target after `R503HAL`.** `[CXC-FPHAL-PROTOCOL-SURFACE]` gives eleven full signatures;
`[CXC-FPHAL-MOCK-BEHAVIOUR-TABLE]` (`72:807-820`) gives a behaviour cell for every one of them;
`[CXC-FPHAL-MOCK-FAULT-MODEL]` gives eight named faults each tied to a specific test that needs it;
`[CXC-FPHAL-MOCK-COSTS-MORE-THAN-100-LINES]` even gives a component-by-component line budget. An agent would
produce something close to right on the first pass.

#### AMBIGUOUS

1. **The mock's third job is unbuildable as the corpus stands.** `[CXC-FPHAL-MOCK-ARBITRATION-ROLE]`
   `corrected-2026-08-17-the-architect-collapsed-two-different-mocks` (`72:792`) restores, on David's words,
   job **(3) THE TEST DOUBLE FOR EVERY CONSUMER ABOVE THE BUS — the widgets, the enrollment station, the
   service**. But `[DD-FPHAL-HOST-OR-VM-BINDING]` (`72:936`) rules that those three consumers **never call a
   HAL method** — they speak `fingerprint/cmd/*` and consume `fingerprint/enroll/status`. A
   `FingerprintHAL` Protocol implementation is therefore **not consumable by any of the three named
   clients**. Two readings, both actionable and incompatible: (a) `MockFingerprintHAL` is a bare Protocol
   impl and something *else* — an unnamed mock *device* that binds it to the bus — is what the widgets test
   against; (b) the mock is itself a bus peer and its "Protocol implementation" framing is stale. The
   corpus contains no third artifact and no wrapper. **This is the difference between ~300 lines and a
   whole simulated device.** Severity: high — it decides the shape of every above-bus test in the project.
2. **`CAPACITY` has three stated triggers and the mock table implements two.** `[CXC-FPHAL-FAILURE-AXES]`
   `axis-capacity` (`72:503`) defines CAPACITY as *store full, **or** slot index out of range, **or** a
   payload exceeds what the device can hold*. The mock's `write_template` row (`72:812`) covers only
   out-of-range and dict-full. Does the mock validate `len(template)` against
   `capabilities().template_size_bytes`? Both readings are defensible and only one exercises the third
   trigger. Given `template_size_bytes` exists precisely because V1.1-era parts may use 768 rather than 1536
   bytes (`72:94`), a mock that never length-checks leaves that path untested by construction.

#### ABSENT

3. **The fault-script object has no shape.** *Load-bearing.* `[CXC-FPHAL-MOCK-FAULT-MODEL]`
   `how-it-is-armed` (`72:855`) rules faults are armed *"DECLARATIVELY, on the constructor or on a script
   object handed to it"* and rules out three alternatives (subclassing, monkey-patching, a method on the
   contract). It never says what the script object **is** — a list? a per-operation dict? a queue with
   ordering? `[CXC-FPHAL-MOCK-BEHAVIOUR-TABLE]` adds a "scripted presentation queue" that `match()`
   *"pops"* (`72:810`), and a "fixture flag" for persistence (`72:802`), and a
   *"fixture-configured maximum"* for stamp size (`72:817`) — three fixture surfaces, no shape for any.
   Load-bearing because **the parity suite is parameterized over both implementations** and
   `[CXC-FPHAL-EV-01]` requires arming each of eight faults; the arming API is therefore shared test
   infrastructure, and two builders will not invent the same one.
4. **No language, no module path, no repo location.** *Load-bearing.* The Protocol blocks are Python and
   the parity test is `tests/hal/test_fingerprint_hal_parity.py` (`72:889`) — the **only** file path named
   anywhere in 4,411 lines. Nothing says where `MockFingerprintHAL` lives, what package it is in, or
   whether Python is normative or illustrative. Under the ruled binding the HAL is *device firmware*, so
   Python is probably wrong for `R503HAL` and right for the mock — meaning the two "peers" are in different
   languages, which nothing says and everything depends on.
5. **What `match()` returns when the script names a slot that is not resident.** *Safe.* The table says a
   presentation *"naming a resident slot yields MATCHED"*; the non-resident case is presumably `NO_MATCH`,
   but it is not written. Local, reversible.
6. **Whether the mock enforces `Ordinal ∈ {1,2}`.** *Safe.* `[CXC-FPID-DO-NOT-HARD-CODE-THE-BOUND]`
   forbids encoding "two" in a type, so presumably no validation — but `[CXC-FPHAL-PRIMITIVE-TYPES]` says
   *"`1` or `2` today"*. A builder could reasonably add a validator and thereby violate a ⛔ rule.

#### DELIBERATELY OPEN
- `[DD-FPHAL-FAILURE-RAISED-OR-RETURNED]` — carrier for failures. **Demoted, not open**; see finding F1 in §5,
  because the document asserts in four places that it *is* open.

---

### Target 2 — `R503HAL`

**Could an agent open an empty file and write it? Yes.** This is the strongest work in the three documents.
`[CXC-FPHAL-R503-BEHAVIOUR-TABLE]` maps every one of the eleven operations to its opcode sequence
(`match()` = `GetImg 0x01` → `GenChar 0x02` → `Search 0x04`, `0x02` from GetImg is `NO_FINGER`, `0x09` is
`NO_MATCH`), and names the hazard at the cell that meets it. `[CXC-FPHAL-CONTAINMENT-INVARIANTS]` gives
eight MUSTs, each tied to a **specific cited defect in a specific surveyed driver** and each carrying a
**tell** a reviewer can grep for. `[CXC-R503-FRAMING]` + `[CXC-R503-CHECKSUM-VERIFIED]` give a framing and
checksum spec that has been *recomputed against 21 worked examples*. `[CXC-R503-UNSOLICITED-0X55]` gives the
structural remedy (drain RX before every transmit **and** a sliding two-byte header window) with the reason
either alone is insufficient. This is exactly what David asked for: engineering shape with enough colour to
build from.

#### AMBIGUOUS

7. **⛔ The slot index base is unstated and the error is self-concealing.** `[CXC-FPHAL-PRIMITIVE-TYPES]`
   declares `Slot` **0-based**, `0 <= slot < slot_count` (`72:331`, `72:343`). The part's own documentation
   drifts: `[CXC-R503-REVISION-DRIFT]` `drift-behaviour` records *"AutoIdentify End position `0-199`
   (V1.2.1) → `1-200` (V1.4)"* (`70:429`). **No node in any of the three specs states the mapping from the
   HAL's `Slot` to the module's PageID**, and no evidence slot tests it. This is the
   `[CXC-VISION-EPISTEMICS]` `rule-self-verification` class arriving in full: `[CXC-FPID-EV-08]` (enroll into
   slot 10, expect occupancy `{10}`) and `[CXC-FPID-EV-02]` (write `{0,3,7,19}`, read back the same) **both
   read back through the same off-by-one that wrote**, so a uniform ±1 shift passes every specified test
   while shifting the uniform index fleet-wide. The one thing that would catch it — a slot read by a
   *second, independently-written* reader, or an assertion against slot 199/200 at the boundary — is in no
   slot. Severity: high. Cheap fix: one sentence in `[CXC-FPHAL-R503-BEHAVIOUR-TABLE]` and one boundary
   case in `[CXC-FPID-EV-08]`.
8. **`captures_per_template` may be architecturally impossible above 2 on the chosen path.**
   `[CXC-FPHAL-R503-BEHAVIOUR-TABLE]` specifies enroll as *"`GetImg`/`GenChar` into character buffers 1..N,
   then `RegModel 0x05`"* (`72:839`), and `[CXC-FPHAL-PRE-CAPTURES-PER-TEMPLATE]` correctly records that
   the part's character-buffer count *is not stated in `[SPEC:CXC-R503]`* (`72:828`, `72:1039`). The
   ambiguity is that `[CXC-FPID-HAL-BUILT-ON-THE-GENERAL-INSTRUCTION-SET]` `reason-instrumentation`
   (`71:198`) argues the general path was chosen **because** *"we CHOOSE it rather than inherit it"* — an
   argument that is void if N is fixed at 2 by the silicon. A builder reading `1..N` will write an N-buffer
   loop; a builder who knows this family will write `CharBuffer1`/`CharBuffer2` and stop. **The spec names
   the missing bound but not its consequence for the argument that rests on it.** This is a NOTE against the
   reasoning, not against the PRE question, which is correctly filed.
9. **`fingerprint/match` vs `<device_id>/user_detected` — what does a device actually publish on a match?**
   `[CXC-R503-HAL-BINDING]` declares `result: fingerprint/match` `{id, score}` (`70:467`);
   `[DD-FPHAL-HOST-OR-VM-BINDING]` `ruled-2026-08-16` says out-messages are *"`fingerprint/enroll/status` +
   `fingerprint/match`"* (`72:948`); spec 71's frontmatter says *"`<device_id>/user_detected` and
   `<device_id>/access_granted` are **the whole upstream vocabulary**"* (`71:39-41`). Three statements, and
   `[CXC-FPHAL-SCORE-DEVICE-INTERNAL]` (`72:391`) removes `score` from one of them without reconciling the
   other. Is `fingerprint/match {id}` the same message as `<device_id>/user_detected {person_id}`, or two?
   Is `id` a **slot** or a **person**? Both readings are buildable and they differ on **who runs hop 2**.
   Severity: high — it decides whether the roster's slot→person table lives on the device.

#### ABSENT

10. **Whether the device holds the slot→person map, and how it gets it.** *Load-bearing.*
    `[CXC-FPID-MSG-USER-DETECTED]` says the payload is *"a `person_id`, resolved from the slot by hop 2"*
    and that the **fingerprint node** owns the message (`71:896-898`). `[CXC-FPID-SENSOR-KNOWS-ONLY-HOP-1]`
    forbids pushing *"roster semantics… into the sensor node's driver"* (`71:370`). The specs draw a real
    distinction between the *driver/HAL* (slots only) and the *device logic* (may resolve), but they never
    say the device holds the map, never say how it arrives, and never say what the device publishes for a
    matched slot with **no roster entry**. A builder must invent all three.
11. **Which end of the `Slot`↔PageID and `Ordinal`↔slot arithmetic owns the uniform index.** *Load-bearing;*
    see finding 7.
12. **What `R503HAL` does when `ReadProdInfo 0x3C` returns an unrecognised model string.**
    `[CXC-FPHAL-R503-BEHAVIOUR-TABLE]` says the implementation *"SHOULD refuse to construct"* (`72:827`) —
    the only SHOULD-shaped statement in the contract, with no failure axis assigned (construction is not an
    operation, so no `FailureAxis` applies) and no acceptance criterion. *Safe-ish*, but it is the one guard
    against `[CXC-R503-PART-IDENTITY-UNCONFIRMED]` and it is the weakest sentence protecting it.

#### DELIBERATELY OPEN — correctly filed, do not treat as gaps
- `[DD-R503-CONNECTOR]` (calipers question), `[DD-R503-ASSURANCE-NAME]`, `[DD-R503-LATCH-FAILSAFE]`
  (re-homed to the integrating device), `[DD-R503-NAMESPACE-SPELLING]` (blocked on write access to another
  corpus), `[DD-FPHAL-THRESHOLD-IN-SERIES-WITH-PART-SECURITY-LEVEL]`.
- `[CXC-FPHAL-OP-DELETE-SLOT]` idempotence — **explicitly** not ratified (`72:690`), David chose to measure
  it at `[CXC-FPHAL-EV-04]`. Correctly labelled PROVISIONAL, and the builder is told not to encode it as an
  invariant. This is the spec doing its job.
- `[CXC-FPHAL-ENROLLMENT-MESSAGE-SEQUENCE]` `the-timeout-terminates-as-cancelled` (`72:294`) — labelled a
  TRANSCRIPTION JUDGEMENT with the cost of being wrong stated ("one enum value on one message"). Exemplary.
- `[CXC-R503-EV-25]` — whether green `0x04` exists on the parts in hand, which decides whether
  `[CXC-R503-FEEDBACK-REALIZATION]`'s `accept` row is implementable at all. Correctly a risk, not a finding.

---

### Target 3 — `fingerprint_storage`

**Could an agent open an empty file and write it? No.** Not a single message shape, endpoint, schema or
signature exists for this service. The specs are explicit that this is deliberate — `71:4` and `72:4` both
put *"the `fingerprint_storage` wire protocol"* and *"the `fingerprint_storage` API"* OUT of scope — but
**no document in the corpus is IN scope for it**, and `[DD-FPID-STORAGE-IS-A-NEW-SERVICE]` (David's ruling)
makes it a new service rather than an extension of an exemplar whose shape could be copied. The stated
exemplar `bus_storage` lives in a corpus this project cannot see.

#### AMBIGUOUS

13. **⛔ "the device **fetches or is sent** each template."** `[CXC-FPID-ROSTER-MESSAGE-SHAPE]`
    `how-the-bytes-arrive` (`71:747`) states both directions in one clause and picks neither. **Push and
    pull are different services.** Push: the service iterates devices, needs a device inventory, needs
    delivery confirmation per template, owns retry. Pull: the device iterates its own roster, needs a
    request/response verb, owns retry, and the service becomes stateless. These have different failure
    modes, different convergence semantics and different answers to `[CXC-FPID-EV-04]` (power cut
    mid-push). A builder acts on one and the other half of the system is built against the other. **This is
    the most dangerous single sentence in the three documents**, because it is short, reads as
    completeness, and sits in the node named "roster message shape."

#### ABSENT — every one of these is load-bearing

14. **The roster message has no topic, no verb, no envelope and no ack.**
    `[CXC-FPID-ROSTER-MESSAGE-SHAPE]` gives per-person fields
    `{person_id, [slot,...], character_ref, template_ref, template_sha256}` and
    `[CXC-FPID-FRAME-PER-TEMPLATE-NOT-PER-ROSTER]` gives a framing rule — and that is the whole wire spec.
    Missing: the address (device-scoped? fleet broadcast?), the verb, whether the desired-state set arrives
    as one message or N, how a device knows the set is **complete** (critical: a desired-state protocol
    whose set can be truncated is a delta protocol wearing a costume), and what acknowledges it.
15. **`capabilities()` is unreachable across the ruled seam — and it is an INVARIANT.**
    `[CXC-FPID-HAL-CAPABILITY-ANNOUNCEMENT]` (`71:178-190`) is stated as a ⛔ INVARIANT whose entire purpose
    is that *"the layer above degrades HONESTLY"*. `[CXC-FPID-TIER-WORKING-SET]` `hardware-bound`,
    `[CXC-FPID-POPULATION-HEADROOM]`, `[CXC-FPID-OVERFLOW-FAILS-LOUD]` and `[CXC-FPID-EV-15]` are all
    **service-side** consumers of it. Under the ruled binding the service cannot call `capabilities()`, and
    **no node specifies a bus announcement carrying it.** The one open decision that could have carried it,
    `[DD-FPID-DEVICE-ANNOUNCEMENT-SHAPE]` `what-must-be-in-it-whatever-the-shape` (`71:1132`), lists exactly
    three things — `device_id`, the gated capability, optional `location` — and **sensor capabilities are
    not among them.** So the invariant that exists to prevent silent degradation is, as specified,
    structurally unobservable by the layer it protects. Severity: highest non-headline finding.
16. **No bus surface for occupancy, roster stamp, template readback, delete or empty.** The two-tier
    verification (`[DD-FPID-VERIFICATION-IS-TWO-TIERED]`), the identity audit
    (`[CXC-FPID-IDENTITY-AUDIT]`), the "announce half" of convergence (*"each node publishes its roster
    stamp"*, `71:672`) and the delete-all-then-add push all require these across the seam. Zero messages.
17. **Slot allocation algorithm.** `[CXC-FPID-SLOT-ALLOCATION-OWNER]` rules the service allocates and the
    station is told, and `[CXC-FPID-POPULATION-HEADROOM]` `ceiling-two` says the service must **recycle
    slot numbers between casts** past ~100 people. No algorithm, no persistence model, no statement of
    whether allocation is dense-from-zero (which `[CXC-FPID-EV-02]`'s non-contiguous-set test implies it is
    not) or arbitrary.
18. **Storage model.** No statement of where the library lives, how templates are persisted, whether the
    service versions them (`[CXC-FPID-MANUAL-DELETE]` `why-a-confirmation` says *"no undo unless the service
    versions them"* — and never says whether it does), or what `[DD-FPID-DELETE-IS-TOTAL]`'s "actually
    removed" means against a backing store.
19. **`fp://<person>/<ordinal>` resolution.** Ratified as the reference form and as **service-private**
    (`[DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE-PLUS-HASH]`), which makes the kill switch *structural*. But
    no resolution operation is specified, so the mechanism that makes the kill switch structural has no
    surface — and a builder will reach for the generic artifact store, which is precisely the REJECTED
    alternative (a).

#### DELIBERATELY OPEN
- `[CXC-FPID-PRE-SERVICE-HOME]`, `[CXC-FPID-PRE-CONVERGENCE-CADENCE]`, `[CXC-FPID-PRE-DEVICE-REGISTRY-HOME]`,
  `[DD-FPID-DEVICE-ANNOUNCEMENT-SHAPE]`, `[DD-FPID-DEVICE-SIDE-RETENTION]` (parked), and the two open
  sub-questions inside `[DD-FPID-DELETE-IS-TOTAL]`. All correctly filed.
- The **injectable clock** requirement (`[CXC-FPID-SERVICE-RETENTION]` `the-testability-constraint`) is one
  of the best things in spec 71: a specific, cheap-if-designed-in, rewrite-if-not constraint with its own
  falsifier pair (`EV-20`/`EV-21`). A builder can act on it directly.

---

### Target 4 — the enrollment station

**Could an agent open an empty file and write it? No.** It has exactly one specified interaction and that
one is un-addressed.

#### AMBIGUOUS

20. **⛔ `fingerprint/cmd/enroll` names no device.** `[CXC-FPHAL-ENROLLMENT-MESSAGE-SEQUENCE]` specifies the
    in-message as `fingerprint/cmd/enroll {slot, ordinal}` (`72:285`, `72:308`) — **no `device_id`, no
    address prefix.** Meanwhile `[CXC-FPID-MSG-ADDRESS]` rules that *events* are addressed
    `<device_id>/user_detected` and that `device_id` is *"the only source identity on the wire"*
    (`71:925`). Commands are not covered by that rule anywhere. In a fleet where
    `[CXC-FPID-ENROLL-EVERYWHERE-DIFFERENTIATE-IN-RBAC]` guarantees **every device holds the whole cast**, an
    un-addressed enroll command is either (a) a broadcast that starts a ceremony on every fingerprint device
    simultaneously — physically absurd, since one finger touches one sensor — or (b) implicitly
    device-scoped by a prefix nobody wrote. Both are buildable. Severity: high, and it is a one-line fix:
    say `<device_id>/fingerprint/cmd/enroll` or say why not.
21. **`fingerprint/enroll/status` has the same problem, and it is worse because it is RETAINED.** A single
    retained last-value-wins topic shared by N devices means device B's ceremony overwrites device A's
    terminal state. `[CXC-FPHAL-ENROLLMENT-MESSAGE-SEQUENCE]` `what-status-holds-afterwards` (`72:293`)
    depends on the terminal value persisting *"until the next `fingerprint/cmd/enroll`"* — which, unscoped,
    means any device's next enroll clears any other device's record.

#### ABSENT

22. **How the station obtains the slot.** *Load-bearing.* `[CXC-FPID-SLOT-ALLOCATION-OWNER]` rules the
    station *"is TOLD and never picks"* — and no message tells it. The station cannot issue the one command
    it has without a value it has no way to obtain.
23. **How the station reaches the editor.** *Load-bearing.* `[DD-FPID-EDITOR-AND-STATION-ARE-TWO]` rules
    two processes and that *"EVERY operation crossing that seam MUST be a bus operation"* (`71:1117`) — the
    ruling's stated reason being that an Intelligence must be able to drive one end. **Zero operations on
    that seam are specified.** The ruling's own justification is therefore not yet buildable.
24. **The person↔ordinal↔ceremony sequencing.** *Safe.* Twelve presses per person = two `begin_enroll`
    ceremonies. Nothing says whether the station issues two `cmd/enroll` commands or one; the message
    carries `ordinal`, which implies two, but the enrollment ceremony node (`[CXC-FPID-ENROLLMENT-CEREMONY]`)
    describes one ceremony per *person*.

#### DELIBERATELY OPEN
- `[CXC-FPID-PRE-ENROLLMENT-STATION]` — one dedicated station or a sensor at every device. Correctly filed,
  and it does not block the *interface* work.

---

### Target 5 — the editor

**Could an agent open an empty file and write it? No, and the specs say so.** `71:4` puts *"the editor's
widget/theme craft"* out of scope and `[CXC-FPID-EDITOR-BOUNDARY]` `out-of-scope-here` repeats it. What the
editor **does** have is a ruling that binds it hard: *"⛔ EVERY operation the editor performs MUST exist as a
bus operation the editor merely calls — create person, enroll, define character, define role, define
capability, register a device, cast, push a roster, delete"* (`71:983`).

#### ABSENT

25. **Nine required bus operations, zero shapes.** *Load-bearing.* That sentence is the editor's entire
    contract and it is a list of nine verbs with no payloads, no topics, no responses and no errors. The
    ruling is excellent — it is exactly the right constraint — and it is currently unfalsifiable, because
    nothing states what any of the nine looks like on the wire. A builder cannot even write the *client*.
26. **The device registry's read surface.** *Load-bearing.* `[CXC-FPID-ENTITY-DEVICE]` rules the registry
    is *"ANNOUNCE-DERIVED, NEVER HAND-MAINTAINED"* and `[CXC-FPID-MSG-ADDRESS]` rules consumers **JOIN** to
    it — but "register a device" appears in the editor's required-operations list (`71:983`), which is a
    *write*. Announce-derived and editor-registered are two different registries. `[CXC-FPID-ENTITY-DEVICE]`
    `registry-is-announce-derived` and `[CXC-FPID-EDITOR-BOUNDARY]`'s list disagree, and
    `[DD-FPID-DEVICE-ANNOUNCEMENT-SHAPE]` alternative (c) names exactly this tension as *"the
    hand-maintained form wearing a UI"* without resolving which the editor does. Classify as **AMBIGUOUS**,
    severity medium: it decides whether the editor writes to the registry or only reads it.
27. **Nothing states the editor's language, framework or relation to ComposeUX.** *Safe* only because the
    editor is furthest from the hardware.

---

### Target 6 — the device firmware (owns the driver AND the HAL)

**Could an agent open an empty file and write it? Yes for the driver and the HAL; no for the device logic
that sits above them.** The split matters: `[DD-FPHAL-HOST-OR-VM-BINDING]` puts three distinct layers in one
binary — the r503 wire driver, the `FingerprintHAL` implementation, and the *device logic* that owns the
ring, applies the match threshold, resolves hop 2 and publishes upstream. The first two are specified in
detail. The third is where the holes are.

#### AMBIGUOUS

28. **⛔ The `enroll/status` phase → `FeedbackState` mapping is unspecified, and both readings type-check.**
    The out-message carries `phase ∈ {awaiting_finger, capture_accepted, capture_retry, merging, stored,
    cancelled, failed}` plus `{ordinal, capture_n, capture_of}` (`72:287`). `FeedbackState` carries twelve
    members including `AWAITING_FINGER`, `CAPTURE_ACCEPTED`, `CAPTURE_RETRY` **and** `ENROLL_PROGRESS` —
    the last being *"the ONE state carrying a payload"* (`72:484`). So on receiving
    `phase=capture_accepted, capture_n=3, capture_of=6`, does the device command
    `FeedbackState.CAPTURE_ACCEPTED` (progress **MUST be None**, `72:470`) or
    `FeedbackState.ENROLL_PROGRESS` with `EnrollProgress(ordinal, 3, 6)`? **Both are legal calls.** Only the
    second can render "press 3 of 6 on the second finger" — which
    `[CXC-FPID-FEEDBACK-STATE-SET]` `the-progress-report-carries-the-ordinal` (`71:888`) says is the whole
    reason the ordinal exists. A builder taking the obvious same-name mapping silently destroys the
    capability the ordinal was added for. Severity: high, and it is exactly the failure the *human*
    acceptance gate at `72:300` would catch and no machine signal would.
29. **`reject_silent` vs `no_match`: who chooses, and can it cross the bus?**
    `[CXC-FPID-PRESENTATION-VOCABULARY]` `who-chooses-the-flavour` says *"⛔ THE GAME"* (`71:787`) and
    `who-emits` says *"the integrating device, as a COMMAND to the HAL"* (`71:788`).
    `[CXC-FPHAL-ENROLLMENT-MESSAGE-SEQUENCE]` states flatly *"⛔ NOTHING ON THE BUS MAY COMMAND THE RING"*
    (`72:295`, unscoped in its wording). If the game is off-device and nothing may cross the bus, the
    flavour must be device **configuration** — which is `[CXC-FPID-PRE-DENIAL-FLAVOUR]`, unanswered, and
    whose own text contemplates *"a runtime choice the game makes per scene"* (`71:1335`). One reading needs
    a YAML key; the other needs a message class that the ring rule appears to forbid. **A builder must
    invent a whole message class or decline to.**
30. **The `[CXC-FPID-VOCABULARY-SEPARATION]` tell fires a false positive on the enrollment path.** That
    invariant's tell is *"any mapping table whose left and right columns contain the same string"*
    (`71:827`). `EnrollPhase.capture_accepted` (a report) and `FeedbackState.CAPTURE_ACCEPTED` (a command)
    are the same string, as are `awaiting_finger` and `capture_retry`. A lint built from the stated tell —
    which is precisely how `[CXC-FPHAL-PARITY-TEST]` assert-12 says mechanical rules should be enforced —
    would flag correct enrollment code. The invariant is about the *sensor-outcome vs presentation* pair and
    is not violated; its **tell** is over-broad. Severity: low, but it will burn the first builder who
    tries to mechanise it.

#### ABSENT

31. **Where the match threshold is read from and what "the YAML" is.** *Load-bearing-ish.*
    `[CXC-FPHAL-MATCH-THRESHOLD]` rules the floor *"settable in the YAML, with `null` being interpreted as
    unset"* — the semantics are crisp and excellent — but no key name, no file, no relation to the
    `peripherals:` block of `[plt.cux:SPEC:CXC-HAL-DECLARE]`. A builder must invent the config surface.
32. **What the device publishes when a match falls **below** the floor.** `[CXC-FPHAL-MATCH-THRESHOLD]`
    says *"does NOT produce `user_detected`"* — and does not say whether it produces `no_match` upstream,
    nothing at all, or a distinct suppressed-match signal. Under `[CXC-FPID-SERVICE-RETENTION]`, publishing
    nothing means a below-threshold match **does not reset the six-month clock**, which is a data-deletion
    consequence of a confidence setting. Nothing states it. *Load-bearing.*
33. **The device's tick model, boot sequence, and what it does before its first roster.** *Safe to
    invent locally*, but nothing describes device startup, whether `read_roster_stamp() is None` triggers
    anything, or what `match()` polling cadence the runtime face uses.

#### DELIBERATELY OPEN
- `[DD-FPHAL-CONCURRENCY-MODEL]` and `[DD-FPHAL-FAILURE-RAISED-OR-RETURNED]` — **demoted** to device-firmware
  internal; the engineering content (0.9 s `match()`, 0.27 s of UART for a 1536-byte burst, a tick loop that
  may not stall) survives and is directly useful. Correctly disposed. See F1 for the count that still says
  otherwise.
- `[CXC-FPHAL-CONTAINMENT-INVARIANTS]` carries `status: proposed` (`72:1276`) — eight MUSTs a firmware
  author must honour that are the architect's enumeration of David's ruling, open to his dissent. Not a
  defect; the builder should know they are building to an unratified enumeration of a ratified rule.

---

## 4. Q2 — CAN `factlattice-gap-analyzer` TRAVERSE BUILT CODE AND COMPARE IT TO THESE SPECS?

Judged against what that agent actually does: Step 0 resolve a profile; Step 0.5 name the tree; Phase 1
extract entities by ID pattern; Phase 2 glob the implementation and build a map of modules, classes,
function signatures, endpoints and test files; Phase 3 cross-reference each spec entity to an
implementation and categorise; Phase 4 verify cross-domain refs; Phase 5 weight by confidence.

### What works, and works unusually well

**Phase 1 is clean.** 243 nodes parse; `lts_find(corpus="r503", prefix="DD")` returns 20 with correct
statuses; the four nodes appended after the Version/Status footers (`[CXC-R503-EV-25]`,
`[CXC-FPHAL-BOUNDARY-RATIONALE]`, `[CXC-FPHAL-CONTAINMENT-INVARIANTS]`,
`[CXC-FPHAL-POWER-OPTIMIZATION-LATER-PASS]`) are indexed correctly despite using the
`## Title [ID]` form rather than `### [ID] Title` — I checked both with `lts_node(tier="digest")`.
Edges resolve, `part-of` chains resolve, evidence slots carry `grounds-claim` back to the claim they
falsify.

**⭐ Symbol names are in STRUCTURE, not only prose — this is the strongest traversability property in the
three files.** I measured it: `lts_find(text="write_template")` returns **7 nodes** and
`lts_find(text="MockFingerprintHAL")` returns **9**, from `id`, `title` and **string property values** — no
prose scan needed. `lts_find_in_prose(text="R503HAL")` returns 13 hits and **every single one is
`also-in-structure`, zero body-only.** That is the opposite of the measured `verified-by` case (48 body-only
vs 9 also-in-structure): these specs put their symbols where the graph index can see them. Nodes titled
`` `write_template(slot, template)` — the roster push's only surface `` and carrying
`signature: def write_template(self, slot: Slot, template: TemplateBytes) -> None: ...` are directly
bindable. A traversal can return **CONFIRMED** or **REFUTED** for statements like *"the implementation
exposes `read_occupancy` returning a set"* without reading a line of prose.

**Sampled bindings I actually checked (12 nodes across all three files):**

| Node | Bindable to | Verdict |
|---|---|---|
| `[CXC-FPHAL-OP-WRITE-TEMPLATE]` | a function signature, verbatim in `signature:` | **CONFIRMED/REFUTED possible** |
| `[CXC-FPHAL-OP-READ-OCCUPANCY]` | signature + return type `frozenset[Slot]` | **possible** |
| `[CXC-FPHAL-CAPABILITIES-SCHEMA]` | a dataclass with 6 named, typed fields | **possible — field-by-field** |
| `[CXC-FPHAL-FAILURE-AXES]` | an Enum with 6 named members | **possible — member-by-member** |
| `[CXC-FPHAL-FEEDBACK-COMMAND-SURFACE]` | an Enum with 12 named members | **possible** |
| `[CXC-FPHAL-PARITY-TEST]` | `tests/hal/test_fingerprint_hal_parity.py`, 13 named assertions | **possible — assertion-by-assertion** |
| `[CXC-FPHAL-MOCK-BEHAVIOUR-TABLE]` | a class + 11 method behaviours | **possible** |
| `[CXC-FPHAL-R503-BEHAVIOUR-TABLE]` | opcode literals `0x01,0x02,0x04,0x05,0x06,0x07,0x08,0x09,0x0C,0x0D,0x18,0x19,0x1F,0x35` | **possible — grep for opcode constants** |
| `[CXC-FPHAL-CONTAINMENT-INVARIANTS]` | 8 invariants, **each with a `TELL:` clause** | **⭐ possible, and mechanically** |
| `[CXC-FPID-SEAM]` | a greppable tell: any vendor name/opcode/slot-count/byte-length above the seam | **⭐ possible, and mechanically** |
| `[CXC-FPID-EXPLICIT-SLOT-REQUIRED]` | "no overload omits `slot`" — checkable by signature inspection | **possible** |
| `[CXC-FPID-MATCH-CHAIN]` | five hops, no symbol, no module, no function | **NOT bindable** |

**⭐ The `TELL:` convention is the single most analyzer-friendly thing here.** Eight of eight containment
invariants and both seam rules state *what a violation looks like in code* — "any `while` over a read with no
bound", "a read sized from a parsed length without a prior comparison", "a `0x55` special-case at init and
nowhere else", "the presence of a `SetSysPara` baud path at all", "a `score` key in ANY payload published on
ComposeBus", "a driver constant naming a game role". These are **grep predicates**, not prose. An analyzer
can return REFUTED with a file and a line. Nothing else in the ComposePlatform corpora I have seen does
this systematically.

### What does not work

**G1 — Phase 2/3 has almost nothing to glob against. One file path exists in 4,411 lines.** I grepped: the
only implementation path named anywhere is `tests/hal/test_fingerprint_hal_parity.py`. There is **no**
module path, package name, repo, directory or language declaration for `MockFingerprintHAL`, `R503HAL`,
`fingerprint_storage`, the station, the editor, or the device firmware. The analyzer's cross-reference step
would have to *guess* which file implements which node, and a guess that lands wrong produces
`SPEC_NOT_IMPLEMENTED` for code that exists — the exact false-alarm class the agent's Step 0.5 exists to
prevent. **Fix: a property, not a restructure.** Add `artifact:` (or `impl-file:`) to the ~20 nodes that
name a buildable thing — `[CXC-FPHAL-MOCK-BEHAVIOUR-TABLE]`, `[CXC-FPHAL-R503-BEHAVIOUR-TABLE]`, each
`[CXC-FPHAL-OP-*]`, `[CXC-FPID-TIER-LIBRARY]`, `[CXC-FPID-EDITOR-BOUNDARY]`. Cheap now, and it is the single
highest-leverage change for Q2.

**G2 — There is no `verified-by` / `verified-at-commit` convention anywhere in this corpus.** I checked:
zero occurrences. The agent's `⭐ Stamp what you judged BETWEEN` step and its P0 laundering check both key on
`verified-at-commit`; without the key the analyzer cannot record what it judged, and the next run cannot
tell whether either side has moved. **Fix: a property**, added to the evidence slots and to any node that
will ever be reconciled against code. Note this composes naturally with the existing
`evidence-state: EMPTY → RECORDED` mechanism, which is already the right shape.

**G3 — Roughly a third of the corpus is unbindable by construction, and that is fine, but it should be
declared.** `[CXC-FPID-MATCH-CHAIN]`, `[CXC-FPID-ENTITY-*]`, `[CXC-FPID-DECEPTION-INTENT]`,
`[CXC-FPID-PHYSICAL-BOUNDARIES]`, `[CXC-R503-REVISION-DRIFT]`, `[CXC-R503-PATTERN-INHERITED]` and every
`[CXC-*-PRE-*]` node describe intent, entities, document facts or unanswered questions. An analyzer that
tries to bind them returns "unclear" for a third of the corpus and the report's signal is diluted. **Fix: a
property** — the corpus already has `evidence-class:` (DATASHEET FACT / DERIVED FACT / INFERENCE / CORPUS
FACT / DESIGN CLAIM / DAVID RULING), which is *nearly* the right axis. Adding `traversable: false` — or
better, teaching the analyzer that `evidence-class: DATASHEET FACT` and `DAVID RULING` are never
code-bindable — converts a third of the report from noise into a declared out-of-scope set.

**G4 — The evidence slots are `OPEN_AWAITING_RULING`-shaped and the analyzer will handle them correctly,
but the slot counts are wrong and it will report the wrong denominator.** `[CXC-R503-EV-INDEX]` says
`slots-total: 25` (`70:773`) in a node titled *"0 of **24** slots filled"* (`70:771`), inside a file whose
`not-this` frontmatter says *"**twenty-four** evidence slots are EMPTY"* (`70:5`) while its `boundary` says
*"**TWENTY-FIVE**"* (`70:4`). The true count is 25 (I counted the headings). `[CXC-R503-EV-25]` is also
absent from the index's `provenance-of-the-slots` and `pairing-is-load-bearing` lists (`70:776`, `70:780`)
and from the Relationships `Entity` block, which stops at EV-24 (`70:1215-1238`). An analyzer that trusts
`slots-total` gets 25; one that trusts the title gets 24; one that counts headings gets 25 and disagrees
with two frontmatter keys. **Fix: mechanical, one pass.**

**G5 — Cross-corpus refs will be reported as `CROSS_DOMAIN_BROKEN` and the corpus already anticipates it.**
`[CXC-R503-PLACEMENT]` `the-dangles-are-real-not-mistaken` names the count (30 forward dangles measured
2026-08-16) and warns against "fixing" them by copying platform nodes in. Excellent — but the *analyzer*
cannot read that warning as an instruction, and its Phase 4 will emit 30 CROSS_DOMAIN_BROKEN rows. **Fix:
a property** on the SPEC root nodes — something like `expected-unresolvable-prefixes: [plt.cux, plt.cio]` —
so a mechanical check can suppress them rather than a human filtering them each run.

### Does a builder need the other corpus?

**For `R503HAL` and the device firmware: no.** Spec 72 is remarkably self-contained; it cites in-corpus nodes
"wherever the in-corpus node exists, which is nearly everywhere" (`72:62`), and that is true — I checked. The
one place it isn't: `[CXC-R503-HAL-BINDING]`'s `peripherals:` declaration is quoted verbatim from spec 32
and its schema is not restated, so a builder writing the board descriptor needs spec 32. That is one YAML
block.

**For `fingerprint_storage`: yes, heavily, and it will not be enough.** `[DD-FPID-STORAGE-IS-A-NEW-SERVICE]`
names `bus_storage` as the exemplar; `[CXC-FPID-EMIT-OR-NOT-NEEDS-NO-MECHANISM]` resolves emit-or-not by
deferring entirely to `[plt.cux:CXC-HALD-DECLARE]`'s namespace map; `[CXC-FPID-EMIT-SCOPE-MEMBRANE]` defers
to `[plt.cux:DX-INTERNAL-BUS-UPLINK]` and `[plt.cux:DEC-INTERNAL-BUS-SELECTIVE-UPLINK]`;
`[CXC-FPID-TEMPLATES-MAY-RIDE-THE-BUS]` defers chunking to "the existing chunked config path". Four external
mechanisms, all load-bearing, none restated. **And even with all four read, the roster protocol still does
not exist** — see §3 target 3.

---

## 5. FINDINGS THAT ARE LEFTOVERS FROM THE 2026-08-17 RE-STATUS PASS

The brief said: *"Five other `[DD-FPHAL-*]` were re-stated to `decided`/`deferred` earlier today; if you see
any inconsistency left from that pass, THAT is a real finding."* I found two, and the first is ironic
enough to be worth fixing today.

**F1 — Spec 72 asserts "THREE still OPEN" in four places; the queryable answer is ONE.**
`lts_find(corpus="r503", prefix="DD", status="open")` returns **six** decisions corpus-wide and exactly
**one** of them is in spec 72: `[DD-FPHAL-THRESHOLD-IN-SERIES-WITH-PART-SECURITY-LEVEL]` (`72:1018`). The
other two named as open — `[DD-FPHAL-CONCURRENCY-MODEL]` and `[DD-FPHAL-FAILURE-RAISED-OR-RETURNED]` —
carry `status: deferred` (`72:917`, `72:958`) and their own `decision:` keys say *"DEMOTED… NOT open and NOT
closed… ⛔ IT BLOCKS NOTHING ABOVE THE BUS and must not be reported as a build blocker."* Yet:

- `72:2` (frontmatter `scope`) — *"THREE still OPEN and deliberately NOT made"*
- `72:23` (the opening blockquote) — *"THREE remain `status: open`"* ← **literally false, and it names the
  key**
- `72:911` (section 8 heading) — *"THREE still OPEN and deliberately NOT made"*
- `72:1244` (Status footer) — *"THREE REMAIN OPEN and deliberately NOT made"*
- `72:1052` — *"SIX decisions are now recorded and THREE remain open"*

**The irony is the finding.** `[DD-FPHAL-HOST-OR-VM-BINDING]` `status-note` (`72:950`) records, at length,
the measured incident in which a stale status token caused *"the pre-dispatch gate… returned NINE decisions
when only SIX were open — three phantom blockers"* and the architect reported two demoted nodes to David as
live build blockers. That pass fixed the **status tokens**. It did not fix the **four prose surfaces that
assert the count**, so the document still asserts three different answers from three different surfaces —
which is the exact failure its own `status-note` names as the transferable lesson. Severity: medium, cost to
fix: five edits.

**F2 — `[CXC-FPHAL-PRE-HAL-RUNTIME-HOME]` carries `question-state: OPEN` in a node that says it is
ANSWERED.** Line `72:1050` reads `- question-state: OPEN`; line `72:1056` in the **same node** reads
*"`question-state` is therefore ANSWERED, not OPEN."* The machine-readable key holds the value the prose
explicitly corrects. Spec 72's own Status footer (`72:1244`) says *"ONE pre-integration question is
ANSWERED"* — so three surfaces, two answers. Same class as F1, cost to fix: one word.

**F3 — Spec 71's frontmatter lists a withdrawn concept as in-scope.** `71:4` `boundary` includes *"the
attempt event"* under **IN**. `[CXC-FPID-UPSTREAM-MESSAGE-SET]` `what-was-rejected` (`71:851`) records:
*"⛔ an architect-invented 'attempt event' carrying `{person, capability, device, outcome}`. Over-specified,
and withdrawn."* A builder reading the frontmatter for scope believes an attempt event is part of the
design. Severity: low, cost: delete three words.

**F4 — A third namespace spelling exists and `[DD-R503-NAMESPACE-SPELLING]` does not know about it.** That
decision node is careful and correct about the two contested sets: spec 27's `fingerprint/enroll` /
`fingerprint/enrolled` / `fingerprint/matched`, and spec 32's `fingerprint/cmd/{...}` /
`fingerprint/enroll/status` / `fingerprint/match` (`70:668-669`). But `[CXC-FPID-ABOVE-THE-SEAM]` (`71:112`)
declares the surface as **`fingerprint/cmd/*`, `fingerprint/status`, `fingerprint/result`** — a third set
that matches neither, authored in this corpus, and cited by nothing that flags it. Severity: low-medium,
because it is the node a builder reads first when asking "what is the semantic surface."

---

## 6. THE STRONGEST THING THE SPECS DO — and what should propagate

Not flattery; three specific patterns, argued.

### ⭐ 1. The invariant-plus-TELL pair. **Propagate this fleet-wide, immediately.**

`[CXC-FPHAL-CONTAINMENT-INVARIANTS]` states eight MUSTs, and each one carries three things: the
**guarantee**, the **specific cited defect it exists because of**, and a **TELL — what a violation looks
like in code**. *"inv-1: EVERY operation carries a DEADLINE… TIED TO: pyfingerprint's read loop, which
returns empty on timeout and continues with no counter and no deadline, so a silent module HANGS THE HOST
FOREVER. TELL: any `while` over a read with no bound, anywhere in an implementation."*

Why this should propagate: a rule with no tell is enforced by the reviewer remembering it, and
`[CXC-FPHAL-CONTAINMENT-INVARIANTS]` says so out loud — *"a quarantine that depends on the implementer
remembering is not a quarantine."* A rule with a tell is a **grep predicate**, which means it survives
`/compact`, survives a builder who never read the spec, and — critically for this project — gives
`factlattice-gap-analyzer` something it can return REFUTED on. This is
`[PRINCIPLE-DETERMINISTIC-CODE-MODELS-FOR-JUDGMENT]` operationalised at the sentence level, and the specs
even name that connection themselves (`72:791`). **Every ⛔ MUST in every ComposePlatform spec should carry
a tell or admit it cannot be checked.**

### ⭐ 2. Negative controls declared as first-class nodes with edges, not as sentences inside the positive test.

`[CXC-R503-EV-INDEX]` `rule-negative-control-is-a-slot`, and nine controls across the two ledgers
(`EV-22/23/24` in spec 70; `EV-09/10/11/12/17/21` in spec 71), each with `requires-control:` /
`ordering:` edges and a `PRECONDITION_FOR` relationship. And the reasoning is stated where it bites:
`[CXC-FPID-EV-INDEX]` `why-controls-here-especially` — *"five of these slots are satisfied by a NULL RESULT
— no template traffic, no divergence, no ring change, no expiry — and a null result is exactly what a
harness that is not running also produces."* `[CXC-FPID-EV-11]` is the exemplar: EV-06 proves zero template
bytes moved; EV-11 proves the capture rig would have seen them. Without EV-11, EV-06 cannot distinguish "the
design works" from "nothing was recording."

Why this should propagate: it makes `[CXC-VISION-EPISTEMICS]` `rule-negative-control` **structural** rather
than remembered. A control that lives as a sentence inside a positive test's prose gets dropped when someone
is in a hurry; a control that is a node with an edge shows up in a query for what is unfilled. **Every
evidence ledger in the fleet should adopt `requires-control:` as an edge type.**

### 3. Ruling attribution as a first-class, correctable property.

Three separate nodes correct their own attribution *in place*, keeping the key and fixing the value:
`[CXC-FPID-SERVICE-RETENTION]` `the-un-parking-is-the-architects` — *"A spec that records an architect's
inference as a human ruling is the failure the audit exists to catch"*; `[DD-FPID-VERIFICATION-IS-TWO-TIERED]`
`status-note` — *"the promotion from RECOMMENDED to DECIDED happened with no turn from him in between"*;
`[DD-R503-MATCH-LOCALITY]` `whose-call-this-is` — *"ARCHITECT'S CALL, NOT DAVID'S RULING — recorded so he can
dissent cheaply."* And `[CXC-FPHAL-BOTH-NUDGES-ALREADY-RULED]` `correction-one-third-of-the-nudge-was-a-genuine-gap`
(`72:277`) goes further: it **refuses to let the document take credit** for a rule it did not contain,
because *"An instrument that mis-sorts a MISSING rule into the ALREADY-SAID pile is worse than one that
reports neither."*

Why this matters: the distinction between "David ruled it" and "the architect decided it, open to dissent"
is the difference between a constraint you must honour and one you may push back on. A builder cannot tell
those apart from `status: decided` alone, and these specs make it queryable. **The `status-note:` +
`ruled-YYYY-MM-DD:` convention should be the fleet standard.** One caveat, which is F1: the convention only
works if the *prose surfaces* are re-stated in the same pass as the status token.

---

## 7. WHAT I COULD NOT DETERMINE

1. **Whether `docs/fleet-identity-design-2026-08-15.md` §§15-19 contradicts spec 71.** Spec 71's own
   frontmatter (`71:12`) declares that where they disagree **the design doc wins**. I did not read it. This
   means spec 71 is, by its own declaration, **not authoritative on its own** for at least five sections —
   and a builder or an analyzer reading only the `.lts` cannot know which claims are overridden. That is a
   structural risk independent of anything I found: a spec that names an external document as its own
   superior should say *which* nodes are affected, or the precedence rule should be retired once the
   amendment pass is done.
2. **Whether the eleven `dsd-source:` citations to `docs/hal-contract/DECISIONS-hal-contract-close.md`
   accurately transcribe David's words.** Every 2026-08-17 ruling in spec 72 rests on them. I took them on
   faith. The verbatim quotes are extensive and internally consistent, which is weak positive evidence.
3. **Whether the R503's general enrollment path (`GetImg`/`GenChar`/`RegModel`) supports more than two
   character buffers.** This decides whether `captures_per_template` is a real parameter or a fixed 2 —
   and one of the four arguments for choosing the general instruction set depends on it being a real
   parameter. Answerable from the manual, which I did not open; `[CXC-FPHAL-PRE-CAPTURES-PER-TEMPLATE]`
   already names it as a document question for the next sweep.
4. **Whether spec 32's `peripherals:` schema can express the device-internal match threshold**
   (`[CXC-FPHAL-MATCH-THRESHOLD]`, "settable in the YAML"). Cross-corpus; I could not check whether the
   schema has a place to put a nullable per-device float, or whether adding one is a schema change to a
   document this project does not own — which would make it a fourth item on the spec-32 amendment list that
   `[CXC-FPHAL-SCORE-DEVICE-INTERNAL]` says is now three lines long.
5. **Whether any of the ~30 cross-corpus targets actually exist.** Per the brief I did not chase them, so I
   cannot distinguish "authored, real, unresolvable locally" (which `[CXC-R503-PLACEMENT]` asserts) from a
   genuine typo in a `[plt.cux:*]` id. If one of those thirty is misspelled, nothing in this corpus can tell.
6. **Whether the mock, at ~250-300 lines, is really cheaper than the alternative.**
   `[CXC-FPHAL-MOCK-COSTS-MORE-THAN-100-LINES]` labels the line counts as INFERENCE, not measurement, and it
   is right to. If finding 1 in §3 resolves toward "the mock must also be a bus peer", the number is wrong
   again and by more than threefold.

---

## Appendix — the shortest list of changes that would move the verdict to IMPLEMENTABLE

1. **Author spec 73: the fingerprint bus contract.** Ten message pairs to match the ten unspecified
   operations, plus the roster push. Every input is already ruled. This unblocks targets 3, 4 and 5 in one
   document.
2. Resolve **"fetches or is sent"** (`71:747`) — one word.
3. Say whether `fingerprint/cmd/enroll` and `fingerprint/enroll/status` are **device-addressed** (`72:285`) —
   one prefix.
4. Add the **sensor `capabilities()` announcement** to `[DD-FPID-DEVICE-ANNOUNCEMENT-SHAPE]`'s
   `what-must-be-in-it-whatever-the-shape` (`71:1132`), or say where else it crosses the bus.
5. State the **`Slot` → PageID base mapping** in `[CXC-FPHAL-R503-BEHAVIOUR-TABLE]`, and add a boundary slot
   (0 and `slot_count - 1`) to `[CXC-FPID-EV-08]`.
6. Specify the **`enroll/status` phase → `FeedbackState` mapping** (`72:287` vs `72:478`).
7. Fix the **counts**: F1 (five edits), F2 (one word), G4 (24 vs 25), F3, F4.
8. Add **`artifact:`** to the ~20 buildable-thing nodes, and **`verified-at-commit:`** to the evidence slots.
