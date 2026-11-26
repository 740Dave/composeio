# Brief for the "c" pass — from → to

**Written** 2026-08-17, from a working session in which David rewrote one node by hand and the result
specified strictly more behaviour in less text. That node is the pattern. This brief generalises it.

⛔ **START FROM THE ORIGINALS — `70`, `71`, `72`, `73`. NOT from the `b` files.**

The `b` pass fixed structure and left content untouched, because it was told to relocate narrative rather
than delete it. It cut once-used keys from 41% to 12% and made sequences render as flows — real repairs —
but it revisited no design decision, because it was not asked to. Starting from `b` inherits every content
decision unexamined. Keep `b` as a reference for the vocabulary it converged on (67 reused keys) and for the
flow decomposition; take the text from the originals.

---

## 1. The governing judgment — the specs were over-rigorous for the use case

This is the single most important instruction in this brief, and every other item follows from it.

**What is actually being built:** a consumer fingerprint module driving props in a game. `assurance:
experiential` — spoofable, no liveness, no secure element, and the corpus is explicit that it is theatre
rather than access control. The population is about twenty templates. There are three sensors on a desk.

**What was written:** four specifications for a peripheral that has never been powered on, producing zero
lines of implementation.

⚠ **A note on size, because it is a smell and not a measure.** These files are large relative to their
siblings, and that was the signal that something was wrong. It is not the thing to fix, and optimising for
it would be a mistake: the best artifact produced in this session — the properly-formed rewrite of the push
sequence — is **five times larger than the hand-written source it came from, and better**, because it made
every requirement individually addressable and attached an acceptance test to each. **Judge the work by the
criteria in §8. Treat size as a prompt to look, never as a target.**

**The consequence of a defect here is that the wrong player opens a prop box.** It is a visible gameplay
bug, noticed immediately, fixed by re-pushing a roster. It is not a safety event, not a compliance event,
and not a data-loss event.

⇒ **The rigour was calibrated for a system that does not exist. Recalibrate it.**

### The explicit licence

**Opportunities to simplify the DESIGN may be taken, not just opportunities to simplify the prose — provided
they do not reduce operational fingerprint functionality on ComposeBus.**

That is the test, and it is a functional test rather than a stylistic one. Before removing anything, ask:
*after this change, can the fleet still enroll a finger, distribute a roster, match a person, report who was
detected, and tell an operator when a device needs attention?* If yes, the simplification is available.

⚠ **This licence is about proportion, not about carelessness.** Rulings stay. Hazards that can damage
hardware stay. Evidence slots stay. What goes is machinery built to defend against edge cases that, in this
system, are cheap to absorb.

---

## 2. What the worked example simplified — in the DESIGN

David's rewrite of `[CXC-FPBUS-PUSH-SEQUENCE]` was not a compression of the original. It replaced the
design with a simpler one that does more. These six moves are the pattern to look for elsewhere, and
**§2.6 is the largest of them.**

### 2.1 A UUID on the set replaced a three-phase ordering discipline

**From:** every push unconditionally emptied the device and rewrote all templates, then wrote a roster stamp
*last* — with a prohibition on writing the stamp inside the loop, a rule that empty must clear the stamp in
the same operation, and an analysis of the window in which the device "testifies to a roster it does not
hold."

**To:** the device compares the incoming `template_set_uuid` to its stored one. Same → no action, acknowledge.
Different → delete all, load all, and store the UUID **only if every template saved successfully**.

**Why it is simpler:** the ordering guarantee that needed two constraints and a prohibition becomes one
conditional clause — *only on full success is the UUID stored*. And the common case, a device that is already
correct, costs nothing at all instead of a full destroy-and-rebuild.

### 2.2 Explicit acknowledgement replaced inference from state

**From:** the service pushed into silence. Convergence was inferred by comparing a stamp the device published.
**To:** four named device-emitted messages with payloads — `template_set_loaded`, `template_set_failed`
(with error code and human-readable string), `fingerprint_inoperable`, `fingerprint_push_needed`.

**Why it is simpler:** a positive signal replaces an inference. The original bus contract — a document whose
entire job was the wire — specified no acknowledgement for a push at all.

### 2.3 A 15-second heartbeat replaced retention semantics

**From:** reasoning about retained last-value-wins topics, what a stamp means when absent, and whether a
consumer would notice a device that had gone quiet.
**To:** a device with no template set emits `fingerprint_push_needed` every 15 seconds. A device that is
inoperative emits `fingerprint_inoperable` every 15 seconds.

**Why it is simpler:** it works on a bus with no retention, needs no one to track state, self-heals if a
message is lost, and removes an entire class of "what does absence mean" reasoning.

### 2.4 Fail-closed replaced a correctness argument

**From:** a multi-paragraph analysis of why a device interrupted mid-push is "confidently wrong rather than
merely stale."
**To:** on any failure, delete every template, leave the UUID unmodified, and report. If deletion itself
fails, emit `fingerprint_push_needed` or `fingerprint_inoperable`.

**Why it is simpler:** the device holds nothing and authenticates nobody. The dangerous intermediate state
the analysis was about no longer exists, so the analysis is unnecessary.

### 2.5 No hashing anywhere

**From:** `roster_sha256` on the desired-set message, `template_sha256` per template, and a tier-2 identity
audit that reads every template back and re-hashes it.

**To:** `template_set_uuid` for set identity, `entry_count` for completeness. No hashes.

**Why, and this is the decisive argument:**

- **A constrained MCU cannot SHA-256 up to twenty templates in reasonable time**, and the corpus has never
  measured how long it would take. The cost is real; the threat is hypothetical.
- **The transports already cover corruption.** CAN 2.0 carries a CRC-15 per frame with automatic
  retransmission and error confinement, and the ratified seven-byte CANopen-SDO-style chunk framing adds
  sequence numbers, so reordering and dropped chunks are detectable. Bare UART carries none of this — but
  nothing moves templates over bare UART; the bus transport beneath has its own framing.
- **The one gap a hash would close is a set that is wrong or short at the source and frames perfectly** —
  and `entry_count` closes it more cheaply, with no compute cost, and fails loudly.
- **The tier-2 audit was already broken and the corpus says so.** Under the 2026-08-16 ruling that the
  device owns the HAL, both the readback and the hashing happen ON the device, so what crosses the bus is a
  hash the device computed about itself. `[CXC-FPID-IDENTITY-AUDIT]` carries this as a `finding`. It is
  testimony, not evidence, and it rests on byte-stable readback, which is an unverified bench assumption.

**What is lost, stated plainly:** nothing proves that slot N holds person P's template except the device's
word. For an `experiential` sensor gating a prop, a mis-slotted template means the wrong player opens the
box — visible, immediate, and fixed by re-pushing.

**Record once, so nobody later believes a check exists that does not:** the 2026-08-16 binding ruling
retired `[CXC-FPID-EV-14]`, the de-vendoring falsifier, and weakened the identity audit; dropping the audit
means that ruling has now cost two verification mechanisms.

### 2.6 One semantic message replaced eleven RPC commands — the largest simplification available

**This is the one to apply first, because everything else in spec 73 hangs off it.**

**From:** spec 73 exposes **eleven** `[CXC-FPBUS-MSG-*]` bus messages that mirror the **twelve**
`[CXC-FPHAL-OP-*]` HAL operations almost one-to-one — `store/empty`, `slot/delete`, `template/write`,
`roster/stamp`, `occupancy`, `template/audit`. The service reaches across ComposeBus and drives the device
one operation at a time.

⚠ Confirmed by reading the addresses: `<dev>/<CMD>/store/empty {}` and `<dev>/<CMD>/slot/delete {slot}` are
**ComposeBus** commands to a device, not internal-bus or HAL-level calls. Spec 73 never states which bus it
covers, which is itself a defect to fix.

**To:** ONE semantic message carrying the desired template set and its UUID. The device decides which
operations that implies.

**Why it is simpler, and why it is also more correct:**

⛔ **The RPC surface inverts the platform's own model.** `[PRINCIPLE-STATE-ON-BUS-BEHAVIOR-IN-WIDGET]` and
`DX-LEAF-OWNS-BEHAVIOR` say publish semantic STATE and let the cheapest node that can fully own the
behaviour own it. Eleven commands publish MECHANISM. Each one is also a place the service can leave a device
in a state the device did not choose — which is the origin of most of the failure analysis in that document.

**Seven of the eleven messages disappear:**

| message | disposition |
|---|---|
| `MSG-EMPTY` | gone — emptying is an internal step of processing a push |
| `MSG-DELETE-SLOT` | gone — under desired-state you never delete one slot, you push a set without that person |
| `MSG-TEMPLATE-WRITE` | gone — templates arrive inside the set, not as N commands |
| `MSG-ROSTER-STAMP` | gone — replaced by device-owned `current_template_set_uuid`, reported in the ack |
| `MSG-TEMPLATE-AUDIT` | gone with the hash decision (§2.5) |
| `MSG-OCCUPANCY` | probably gone — the acknowledgement already says which set is loaded |
| `MSG-ROSTER-SET` | becomes the push message |

**Surviving:** `MSG-ENROLL` and `MSG-ENROLL-CANCEL` (enrollment is genuinely a command — a human is putting
a finger down), `MSG-DEVICE-ANNOUNCE`, `MSG-MATCH-HAS-NO-COMMAND` (still true), plus the four device-emitted
messages from the rewrite.

⭐ **And the reasoning attached to those seven goes with them.** The entire "empty must clear the stamp in
the same operation or the device lies" analysis exists *only* because `empty` was a separate bus command
with a gap after it. Make it one atomic device-side operation and the failure mode cannot occur. **Deleting
a mechanism deletes the paragraphs defending it — that is where the real reduction lives.**

⚠ **One question to settle before cutting `slot/delete`, and it is a ruling, not a judgement call.**
`[CXC-FPID-EDITOR-BOUNDARY]` requires that EVERY editor operation exist as a bus operation, and its list of
nine includes *delete*. Under desired-state, "delete a person's template" becomes "push a set without them"
— which preserves the simplification. **Confirm that reading before removing the message.** If the editor
genuinely needs a direct per-slot delete, `MSG-DELETE-SLOT` survives and nothing else in this section changes.

---

## 3. What it did to specification density — the useful sense of the word

| | original node | David's rewrite |
|---|---|---|
| specifies | 3 phases, no actor, no acknowledgement, no idempotence, no error payload | 4 named messages with payloads, UUID idempotence, error codes, 2 push-time validations, 2 heartbeats, a complete failure path |
| unspecified behaviour a builder must invent | who performs each phase; what the device replies; what happens on partial failure | none of those |

⭐ **The rewrite specifies roughly three times as much behaviour in less text.** That is the claim worth
generalising: **the goal is behaviour-per-word, not fewer words.** A shorter document that leaves a builder
guessing has not improved; a longer one that removes every guess has.

---

## 4. From → to, as concrete patterns

| from | to |
|---|---|
| A sequence as a semicolon list inside `claim:` | A step per node with `actor`, `action`, `on-failure`, joined by `next-step` (mints `BEFORE`), so `lts_flow` renders it with swimlanes |
| Ownership left to inference across a multi-actor process | `actor:` on every step. If two components alternate, the document says which does what |
| `the-window-this-opens-and-its-honest-cost:` | `constraint:` — a rule stated as a rule |
| `a-failure-at-any-phase-aborts-the-push-for-that-device-only:` | `on-failure:` on each step |
| A prohibition plus an ordering rule plus a `tell` | One conditional clause: *only on full success is the UUID stored* |
| An analysis of a dangerous intermediate state | A design in which the state does not occur |
| Inference from published state | An explicit acknowledgement message with a payload |
| Retention semantics and absence-reasoning | A heartbeat with a stated interval |
| A hash to defend an edge case | A counter, or nothing |
| A narrative explaining a relationship between two nodes | An edge |
| A citation re-explained in the body | The citation alone |
| Emphasis glyphs | Words, or a named field — `constraint`, `hazard`, `tell` |
| A literal bound (`20 templates`) | The device's announced `slot_count` from `capabilities()`, per `[CXC-FPID-DO-NOT-HARD-CODE-THE-BOUND]` |

---

## 5. Destructive testing is forbidden, and two slots must go

⛔ **No test on this project may risk damaging hardware. This is a standing rule.**

Two evidence slots require deliberately destroying a module and must be **deleted**, not merely deferred:

- **`[CXC-R503-EV-08]`** — *"on a SACRIFICIAL module, drive RXD from a 5 V logic level with VCC at 3.3 V and
  observe whether input current rises through a clamp diode."*
- **`[CXC-R503-EV-17]`** — *"on a module you are willing to LOSE, probe each N in 1..12 via SetSysPara."* Its
  own constraint reads **"NEVER RUN IT ON A MODULE YOU CARE ABOUT."**

**Both are unnecessary for the same reason: they characterise a hazard already decided against.**

- EV-08 asks whether RXD tolerates 5 V. **The host is a 3.3 V part and the rule is already "drive RXD from a
  3.3 V pin only."** The test would license something that will never be done.
- EV-17 asks which baud values the part accepts. **`inv-8` already says do not build the baud path at all.**
  The answer is unusable.

⇒ **Delete both slots. Keep both rules.** A rule you obey needs no experiment.

⭐ **The general form, and it is the proportion error in miniature: the specs repeatedly set out to
CHARACTERISE a danger instead of AVOIDING it.** Characterising costs hardware and bench time. Avoiding costs
one sentence. Look for other instances.

**Provenance, recorded so it is not repeated:** David said he had a spare module. That was read as
authorisation for deliberate destructive testing. It was not.

### Safe acceptance tests only

- **In the mock** — arm `write_template` to fail at index 12 of 20; assert the UUID is unmodified and no
  templates remain. No hardware is involved, so no hardware can be harmed. This is the only place a failure
  path should be exercised.
- **On hardware, using legal operations only** — push the same set twice and assert zero write operations on
  the second; push a different valid set and assert delete-then-reload; disconnect the sensor's wires and
  assert `fingerprint_inoperable`; power-cycle a device with no templates and assert `fingerprint_push_needed`
  every 15 seconds.
- ⛔ **Never** a mid-write power cut, and ⛔ **never** deliberately malformed data to the device — feeding bad
  bytes to an undocumented parser is itself a brick vector.

---

## 6. The rest of the c-pass rules

These carry forward from the `b` pass, which got them right, plus what the session added.

**Vocabulary.** Converge on a small reused key set. The `b` pass landed on 67 keys and dropped once-used keys
from 41% to 12%; `platform-specs/48-compose-launch-bar.lts` runs 20%. **A key NAMES A FIELD; the value carries
the content.** No sentence-shaped keys.

**Requirements must be individually addressable.** A developer must be able to cite one requirement from a
code comment or a test name, and `factlattice-gap-analyzer` must be able to bind it. Use whatever form the
normative spec prescribes — and ⛔ do not invent `requirement-1:` / `requirement-2:` keys, which regenerate
the once-used-key defect at scale.

**Glyphs: zero.** Not reduced. Where one carried meaning, it becomes a word or a named field. A leading glyph
before a colon also makes the whole line parse as prose — 137 keys were nearly lost that way in one document.

**No self-reference.** The phrase "the architect" appears 56 times across the four originals. A specification
does not discuss its author. **Process commentary is the largest deletable class of content** — the longest
properties in every file are records of how a previous draft was wrong, not statements of what to build.
**The RULING and its attribution survive in one or two sentences. The essay about how it was reached does not** — that belongs in
the handover and the DSD, both of which are retained.

**Ignore `profile.low_density`.** The check is being removed. Its arithmetic put `unique_property_keys` in the
numerator, so a key used once scored exactly as much as a key reused twenty times — it rewarded the very
behaviour that caused this. A worse density score after the c-pass is expected and correct.

**Keep the invariant-plus-`tell` pairing.** Each `requirement` or `constraint` that a builder could violate
carries a `tell:` — what the violation looks like in code, as a grep predicate. This is the strongest thing in
these documents and the only part a gap-analyser can act on. `b` made `tell` a reused key with 78 instances;
keep that.

**Keep `artifact:` paths.** They are what lets spec bind to code.

**Verify against the engine, not against belief.** Confirm every edge verb against `REL_PROPERTY_EDGES` /
`FLOW_PROPERTY_EDGES` rather than assuming — a hand-written example in this session asserted `enforces:` was
an edge and it mints nothing. Run `lts_flow` on every converted sequence and confirm it renders more than one
box.

---

## 7. What must survive

- **Every ruling, its attribution and its `dsd-source:`** — including the decision ruled and then reversed the
  same day, and the two that overruled the architect's recommendation. Compressed, never dropped.
- **Every `status:` token**, bare and queryable.
- **Every open `[DD-*]`** with its alternatives and honest costs.
- **Every evidence slot and PRE question**, sentinels intact — minus the two destructive slots, whose deletion
  is recorded with its reason.
- **Every hardware hazard**: tie 3.3VT to 3.3 V; never issue a baud change; the ring's colour set is
  unverified; wire colour never indicates pin order.
- **Typed signatures, the capability schema, the failure enum, both behaviour tables, the opcode set.**

---

## 8. Acceptance

1. **Every design simplification in §2 was considered and its disposition recorded** — taken, or declined
   with a reason. ⛔ This is the criterion the `b` pass failed: it revisited no design decision at all. A c
   file that is merely re-organised is a c file that did not happen.
2. `key_loss.lost_bullets` = **0** per file.
3. Once-used keys at or below 20%.
4. **Zero glyphs. Zero occurrences of "the architect."**
5. `lts_flow` renders every converted sequence as more than one box, grouped by `actor`.
6. **No destructive test appears anywhere.**
7. A loud list of everything dropped, with anything uncertain flagged rather than silently removed.
