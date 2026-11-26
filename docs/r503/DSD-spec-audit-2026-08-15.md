<!-- dsd-status: answered 2026-08-16T05:48:10Z -->
# DSD — Independent audit of `70-r503-peripheral.lts` + `71-fingerprint-identity.lts` against what David actually said

> **What this consolidates.** An adversarial, no-conversational-context audit of the two R503 specs
> (`corpus/70-r503-peripheral.lts`, `corpus/71-fingerprint-identity.lts`) and the design narrative
> (`docs/fleet-identity-design-2026-08-15.md`) against David's verbatim turns in session transcript
> `80559914-0876-48e6-9bac-0db1cf8b66db.jsonl`. Four directions: **MISSING · ALTERED · INVENTED · AMBIGUOUS**.
>
> **How to answer.** Fill the `Your call:` slots in-place, or reply on the channel. Every quote below is
> verbatim with its transcript line; every spec claim carries a node id and `file:line`.
>
> **Auditor independence, stated.** I was not in the conversation. I re-derived David's statements myself by
> raw-grepping the transcript rather than trusting `tools/extract_design_inputs.py` — **and that check found a
> defect in the tool (see §3).** I deliberately did **not** read `fleet-identity-design-2026-08-15.md` §18
> (the architect's pre-registered predictions about this audit), because reading it would have anchored me to
> what the architect already suspects.

---

## How to use this

The recommendations are my honest engineering lean, not a nudge — overrule freely.

**The document is split in two on purpose.**

- **§1 — QUESTIONS (5).** AMBIGUOUS findings: David said something that admits more than one valid reading and
  the spec silently picked one. **Only David can answer these.** Each states both readings and what changes
  under each. If you answer nothing else, answer these five.
- **§2 — CORRECTIONS (9 + 2 minor).** MISSING / ALTERED / INVENTED findings. These are **defects the architect
  fixes and you merely ratify.** They carry a one-line `Ratify?` slot, not an options table. "All ratified" is
  a complete answer to §2.

⛔ **blocks** marks work that cannot proceed correctly until the item is settled.

⭐ **The single most consequential finding:** the identity spec asserts that **every device carries every
enrolled person** — which contradicts David's explicit sentence that a device holds **10 players** while the
service holds an **unbounded** population, and it is the stated precondition the entire uniform-index design
rests on. That is **Q1 + C3**, and they are the same defect seen from two sides.

---

# §1 — QUESTIONS (AMBIGUOUS — only David can answer)

### D1 · ⛔ Is a device's working set the WHOLE enrolled population, or THIS MODULE'S CAST?

**What David said** (transcript line 461, verbatim):

> "Want to be clear that what gets loaded into a designed-into-the-game R503 is 10 players, but the
> fingerprint_storage service on the compose bus needs to be able to store an unbounded number of game players."

**What the spec says — three different answers, in three nodes:**

| Node | `corpus/71-fingerprint-identity.lts` | What it says the working set is |
|---|---|---|
| `[CXC-FPID-ENROLL-EVERYWHERE-DIFFERENTIATE-IN-RBAC]` | :579–587 | "every device carries **EVERY player**" … "every device stores every player's templates, so a stolen device carries **the whole population's** biometrics" |
| `[CXC-FPID-PUSH-TRIGGER]` | :630–639 | "**the cast for this module IS the working set**" |
| `[CXC-FPID-ROSTER-PUSH-SCOPE]` | :423–431 | "a push projects an unbounded library onto a bounded device … **the service decides WHO is on a device**" |

**Why it matters.** "Every player" is the *stated precondition* of the uniform index
(`[CXC-FPID-UNIFORM-INDEX]` :486): if devices hold different subsets, slot 4 is a different person on different
devices and the whole divergence-is-a-fault guarantee collapses. But "every player" against an **unbounded**
library is unbuildable — `[CXC-FPID-POPULATION-HEADROOM]` :591 puts the R503 ceiling at ~100 people, and the
spec then asks you `[CXC-FPID-PRE-POPULATION-CEILING]` :1127 whether 10 is a ceiling. Under the *cast* reading
there is no ceiling problem at all and that PRE question mostly dissolves.

| Option | Tradeoffs |
|---|---|
| **A — The working set is THIS MODULE'S CAST** (what your sentence says) | Matches "10 players loaded into a device" exactly; the library stays genuinely unbounded; ceiling is per-session, never reached. Cost: the uniform index must be **per-roster**, not fleet-eternal — slot K means "the same person on every device *for this roster*", and a new game start reassigns. That is a real strengthening of the verifier's job. |
| B — The working set is EVERY enrolled person | Slot K is stable forever, which is the cleanest possible index. Cost: contradicts your sentence, caps the fleet at ~100 people on an R503, and puts the entire biometric population on every prop you leave in a room. |
| C — Every person *ever cast in any module*, cast-union | Middle ground; slot stability survives re-casting across modules. Cost: grows monotonically and hits the same ceiling later, silently. |

**My recommendation: A.** It is what you said, it is the only one compatible with "unbounded", and the honest
cost (a roster-scoped index) is a verifier change, not a redesign. B is the reading the spec is written
against and it is the one that will bite.

**Your call:** Neither
**Comments:** I thought we had deferred the cast concept, but a cast (the people playing roles in a game) would be universal across all fingerprint sensors.  1 game = 1 cast list of up to 10 live players = up to 20 fingerprint scans.  All cast templates get loaded in all fingerprint modules, no sub-segmentation by module.

---

### D2 · "Haven't authenticated in six months" — does a DENIED attempt reset the clock?

**What David said** (transcript line 519 — ⚠ a queued message the extractor never saw):

> "And the system should auto-delete a user & fingers if they haven't **authenticated** in six months - even
> though its a toy, no need to keep that data laying around."

**What the spec says.** `[CXC-FPID-SERVICE-RETENTION]` :1034–1044 makes `user_detected` the retention signal,
and `[CXC-FPID-MSG-USER-DETECTED]` :828 states it "fires even when the permission check **DENIES** — she was
detected. That is what makes it the `last_seen` signal." `[CXC-FPID-EV-21]` :1526 tests exactly that.

**Why it matters.** The spec picked "detected" for your word "authenticated" without recording that it chose.
A player whose only interactions are denials — which, given the deception design, is a normal and even
*intended* experience — never ages out under the spec's reading and ages out promptly under the other.

| Option | Tradeoffs |
|---|---|
| **A — `user_detected` (any match, denied or granted) resets the clock** | Nobody who is physically playing ever ages out. Retention is about abandoned enrollments, which is what "no need to keep that data laying around" reads like. |
| B — Only `access_granted: true` resets the clock | Closer to the literal word "authenticated". Cost: a player whose role legitimately fails every device they touch is deleted mid-campaign, and their next session needs a re-enrollment ceremony. |

**My recommendation: A.** You wrote "no need to keep that data laying around" — the target is *dormant data*,
not *unprivileged players*. But the spec should say it chose, and say why.

**Your call:** A — user_detected (any match, denied or granted) resets the clock

---

### D3 · `location` — you wrote it as a message field; the spec made it a namespace

**What David said** (transcript line 351, verbatim):

> "I'd want the device controlling an R503 to have the vocabulary to emit `user_deected: <user_id>` at
> `location: <location_id>` and `access_granted: true/false` semantic messages."

**What the spec says.** `[CXC-FPID-MSG-LOCATION]` :855–863 — "`location` is NEITHER message's field — it goes
in the **NAMESPACE**", published as `<location>/user_detected`. The node states the cost honestly ("relocating
a device becomes a CONFIG CHANGE") but the trade was never put to you.

**Why it matters.** It decides what a consumer must do to ask "who has been seen anywhere in the last hour":
under the namespace form that is N subscriptions, one per location, and moving a prop between rooms is a
config edit and a redeploy.

| Option | Tradeoffs |
|---|---|
| **A — namespace (as specced)** | One subscription for "who is at the port hatch"; works with CAN acceptance filters and selective uplink; zero payload bytes. Cost: relocation is a config change; fleet-wide queries fan out. |
| B — payload field | Relocation is free; one subscription answers fleet-wide questions. Cost: a location field in every message is budget an 8-byte CAN frame does not have, and it can disagree with the device's declared location. |
| C — both: namespace for routing, and the device's declared location resolvable from the Sensors registry | No duplication on the wire, fleet-wide queries answerable by joining the registry. Cost: depends on the registry that does not yet exist (**C1**). |

**My recommendation: C**, with A as the wire form. Your sentence reads as "the message should tell me where",
not "the field must be in the payload" — and C delivers that without paying the CAN cost. This one may want a
short discussion rather than a slot.

**Your call:** Neither
**Comments:** location was probably not the best term, was I was intending to indicate was that the device emitting the semantic event was identifying itself clearly - e.g. "I'm the device that controls access to nuclear permission to fire, and I rejected boomer's attempt to authorize the use of nukes"  Hopefully that english helps explain intent; device might be the better term than location, because the device itself could be portable (think a star-trek tricorder with an attached R503)

---

### D4 · "Optionally define RBAC" — may a person exist with NO role?

**What David said** (transcript line 351, verbatim):

> "we want to be able to use one R503 to register new players/users, **optionally** define RBAC (*game* role),
> and then push these into all the connected R503s"

**What the spec says.** Nothing. The match chain `[CXC-FPID-MATCH-CHAIN]` :309–320 runs
`person → character → role → capabilities` with no branch for a person who has no character or no role, and no
node states what hop 4 returns when the chain is short.

**Why it matters.** Enrollment and casting are separated by design (that is the whole payoff at
`[CXC-FPID-RECASTING-MOVES-NO-TEMPLATE]`), so an enrolled-but-uncast person is not an edge case — it is the
*normal* state between an enrollment ceremony and a game start. Every device will meet one.

| Option | Tradeoffs |
|---|---|
| **A — an uncast/roleless person resolves to the empty capability set** | Uniform: they are detected, named upstream, and denied everything. No special case anywhere; `user_detected` still fires, so retention still sees them. |
| B — an uncast person is a fault the verifier reports | Catches a forgotten casting before a session. Cost: noisy between sessions, when it is the expected state. |
| C — a default role ("crew") applied on enrollment | Convenient. Cost: a default permission bundle is exactly the thing nobody re-reads before it grants something. |

**My recommendation: A**, plus one line in `[CXC-FPID-MATCH-CHAIN]` saying a short chain is legal and yields
the empty set.

**Your call:** A — an uncast/roleless person resolves to the empty capability set

---

### D5 · Index finger "unless stated otherwise" — the spec closed a door you left ajar

**What David said** (transcript line 427 — ⚠ queued, the extractor never saw it):

> "I'm fine with (person_id, finger), but recall this is a toy, not likely to be anything other than index
> finger, so that should be the assumed default **UNLESS STATED OTHERWISE**. Actually, for simplicity, let's
> have the game design be that both index fingers are scanned per person…"

…then later (transcript line 461):

> "as we have no need to track left-vs-right, that's fine, it's simply user x, finger 1, finger 2."

**What the spec says.** `[CXC-FPID-ORDINAL-SEMANTICS]` :448–457 — "⛔ nothing tracks left versus right",
"no interface labels them anatomically", "⛔ no logic branches on the ordinal. A branch on ordinal 1 is a
**defect**." That is a flat prohibition; your first statement reserved an escape hatch.

**Why it matters.** Small, but cheap to settle now and annoying later: if a player enrolls a thumb because of
a bandage, there is nowhere to record it, and no schema field to add without touching a rule marked ⛔.

| Option | Tradeoffs |
|---|---|
| **A — keep the flat prohibition** | Simplest; nothing can branch on a field that carries no fact. Cost: a non-index enrollment is invisible and unrecordable. |
| B — allow an optional, non-normative `note` on an enrollment | Preserves "stated otherwise" without letting anything branch on it. Cost: one nullable field, and the discipline to never read it in logic. |

**My recommendation: B.** Your later sentence justifies A for *logic*; B keeps the escape hatch you asked for
without reopening handedness.

**Your call:** A — keep the flat prohibition
**Comments:** Good ambiguity catch, glad I was asked to clarify.

---

# §2 — CORRECTIONS (MISSING / ALTERED / INVENTED — ratify, don't decide)

### C1 · ⛔ MISSING — the device / Sensors registry has no home in either spec

- **David said** (line 519, queued): *"we'll need to have a way to identify all the R503s in use, and state
  what they're for, and their relationship to the RBAC's"* — and (line 573, queued): *"Sensors (map R503s to
  capability they connect to, e.g. nuclear permission to fire vs jump the ship)"*.
- **The specs say:** the registry is **load-bearing and undefined**. `[CXC-FPID-UPSTREAM-MESSAGE-SET]`
  (71:784–794) rejects a fatter payload *because* "`capability` is DERIVABLE from the device via the Sensors
  registry"; `[CXC-FPID-EDITOR-BOUNDARY]` (71:912) lists "register a device" as a required bus operation. But
  `device` is **not one of the six entities** (71 §3, :215–304), there is no `[DD-FPID-*]` for it, and it is
  not one of the eight PRE questions. The design doc's own open item — §10.5 #4, *"How a device declares its
  purpose and its gated permission … the announcement's shape is unspecified"* — **did not transfer to the
  spec.**
- **Why it matters:** the upstream message design is justified by an object that is specified nowhere. Anyone
  building `user_detected` + `access_granted` today has to invent the registry, and the "derive it, don't
  carry it" ruling silently becomes "hand-maintain it" — the exact antipattern the node cites.
- **Recommendation:** add a `[CXC-FPID-ENTITY-DEVICE]` node and a `[DD-FPID-DEVICE-ANNOUNCEMENT-SHAPE]` open
  decision carrying the design doc's announce-derived-not-hand-maintained position. This is the largest
  buildability hole I found.

**Ratify? _______________**

---

### C2 · INVENTED — the six-month retention "UN-PARKING" is recorded as David's ruling; David parked it and never un-parked it

- **David said** (line 519, queued): *"the system should auto-delete a user & fingers if they haven't
  authenticated in six months"* — the requirement is genuinely his. Then (line 544): *"I didn't mean that the
  R503s should delete, only that the fingerprint_registry should auto-delete, **but let's park this for riht
  now**."* Then, much later (line 790): *"my comment that 'fingerprint_storage' should delete fingerprints
  after 6 months of disuse was tied specifically to the design I proposed … But it was a minor point and it
  was, frankly, **easier to drop the concept** … than to try to explain why my design was valid!"*
- **The spec says:** `[CXC-FPID-SERVICE-RETENTION]` (71:1034–1044) — *"⛔ **UN-PARKED** … **ruling: David,
  2026-08-15, un-parking his own original proposal**"*, with a hard `MUST` (injectable clock, :1041) and two
  evidence slots (`[CXC-FPID-EV-20]`, `[CXC-FPID-EV-21]`, 71:1510–1537) grounded on it. The version footer
  (71:1699) lists "un-parked-retention" among David's rulings.
- **Why it matters:** the last thing you actually said about this was *"let's park this"*, followed by an
  explanation of why you'd dropped it. The un-parking is the architect's call — reasonable, probably right,
  and it should be recorded as the architect's, not yours. A spec that records an architect's inference as a
  human ruling is the failure mode this whole audit exists to catch.
- **Secondary:** the same node states *"untestability was David's own stated reason for dropping it"*
  (:1041). Your stated reason was that explaining the design cost more than the feature was worth; testing
  appears as a parenthetical inside what you dropped, not as the reason. The design doc §17.2 words this
  correctly ("One real design constraint from David's own words"); the spec overstates it.
- **Recommendation:** re-attribute to `architect's call, on David's requirement of 2026-08-15, open to his
  dissent` — the pattern already used correctly at `[DD-R503-MATCH-LOCALITY]`. Then **D2** decides the signal.

**Ratify? _______________**

---

### C3 · ⛔ ALTERED — "every device carries EVERY player" is not what David said

- **David said** (line 461): *"what gets loaded into a designed-into-the-game R503 is **10 players**, but the
  fingerprint_storage service on the compose bus needs to be able to store an **unbounded** number of game
  players."*
- **The spec says:** `[CXC-FPID-ENROLL-EVERYWHERE-DIFFERENTIATE-IN-RBAC]` (71:581) — *"every device carries
  **EVERY player**"* — and (71:585) *"every device stores every player's templates, so a stolen device carries
  **the whole population's** biometrics."*
- **Why it matters:** this is the substance of **D1** as a defect rather than a question. It converts a
  bounded device + unbounded library into an unbounded device, then has to introduce a ~100-person ceiling
  (`[CXC-FPID-POPULATION-HEADROOM]` 71:591) and ask you a PRE question your own sentence already answered.
- **Recommendation:** fix once **D1** is answered; under my recommended reading the rule becomes *"every
  device carries every player **in the current roster**"* and the honest-cost line loses "the whole
  population".

**Ratify? _______________**

---

### C4 · ALTERED — corpus placement is recorded as permanently CLOSED; David made it conditional

- **David said** (line 196, verbatim): *"all LTS files and source code generated should happen in this
  directory **until such time as we're comfortable that the work is solid and can be committed to the main
  Compose\* repo**."*
- **The specs say:** `[CXC-R503-PLACEMENT]` (70:1042) — *"⛔ **RULED** by David … the placement question here
  is **CLOSED**"* — and `[CXC-FPID-PLACEMENT-INHERITED]` (71:68–76) inherits it, accepting every unresolvable
  cross-corpus dangle as *"a standing property of the placement"*.
- **Why it matters:** your sentence describes a **staging area with an exit condition**; the spec records a
  permanent siting. That changes real decisions: whether the `[DD-R503-*]` id renames in **C7** are worth
  doing now (they are cheap only before the move), whether the dangles are a defect backlog or a permanent
  cost, and whether spec 32's frontmatter amendment (**C5**) waits for the merge.
- **Recommendation:** amend both nodes to `RULED — staging placement, with a stated exit condition: migration
  to composeio when the work is judged solid`, and add the migration as a tracked item.

**Ratify? _______________**

---

### C5 · ALTERED (stale) — `[CXC-R503-TEMPLATES-NEVER-CROSS-THE-BUS]` survived a ruling that reversed it

- **David said** (line 654, verbatim): *"I honestly don't have a problem with them flowing over ComposeBus, as
  it's the same risk as having them flow over any other protocol (and in saying that, I don't want to have a
  different protocol to support anyway)."*
- **The specs say:** spec 71 records the reversal correctly (`[CXC-FPID-TEMPLATES-MAY-RIDE-THE-BUS]`
  71:656–665). **Spec 70 does not.** `[CXC-R503-TEMPLATES-NEVER-CROSS-THE-BUS]` (70:433) still reads as a live
  obligation — *"central matching REQUIRES templates to leave the sensor, and this rule says they may not
  leave on the bus"* — and `[DD-R503-MATCH-LOCALITY]` (70:561) still cites it as one of three supporting
  arguments: *"it keeps templates off the wire, which `[CXC-R503-TEMPLATES-NEVER-CROSS-THE-BUS]` requires
  anyway."* The relationship line 71:1601 merely labels the pair `CONTRAST_WITH`.
- **Also missing:** the rule originates in **platform spec 32's frontmatter** (`raw framebuffers / images /
  waveforms / templates NEVER cross the bus`). Your ruling overrides a *platform-level* statement, and
  **nothing in either spec records that spec 32 now needs amending** — the same shape as the unresolved
  spec-27-vs-32 conflict at `[DD-R503-NAMESPACE-SPELLING]`.
- **Why it matters:** `[DD-R503-MATCH-LOCALITY]` stands fine on its other grounds (the 20-template collapse),
  so nothing is wrong *today* — but a successor reading spec 70 alone gets a withdrawn constraint presented as
  current, and the platform corpus keeps contradicting the fleet.
- **Recommendation:** amend the 70 node with a dated supersession pointing at 71, strike the third argument
  from `[DD-R503-MATCH-LOCALITY]`, and open an item for the spec 32 frontmatter amendment.

**Ratify? _______________**

---

### C6 · INVENTED (status promotion) — `[DD-FPID-VERIFICATION-IS-TWO-TIERED]` is `status: decided`; the design doc calls it RECOMMENDED and David never ruled

- **David said** (line 629, verbatim): *"Re: identity-verification method and template_ref - **I lack context
  to make calls on these, can you provide more detail?**"* Then, after the detail (line 654): *"Your
  suggestion for fp://7/1 is excellent, as well as the sha256 of the template."* — an explicit ratification of
  **`template_ref` only**. He said nothing about the two-tier verification method.
- **The artifacts say:** design doc §11.3 is headed *"**RECOMMENDED** — verification is notepad continuously,
  UpChar-and-hash periodically"*. Spec 71 records `[DD-FPID-VERIFICATION-IS-TWO-TIERED]` (71:965) with
  `status: decided` (:968), and `[DD-FPID-TEMPLATE-REF…]` (:942) claims ratifying the hash closed *"the
  verification-method question with it"*.
- **Why it matters:** you asked for detail on this one specifically, and it moved from RECOMMENDED to DECIDED
  without a turn from you in between. It also carries real cost — `[CXC-FPID-EV-13]` is now "LOAD-BEARING",
  and a periodic full-template-readback audit costs the same as a full push (71:571).
- **Recommendation:** either downgrade to `status: proposed — awaiting ratify`, or keep `decided` and mark it
  `architect's call, open to dissent`. Then ratify it here if you're happy with it.

**Ratify? _______________**

---

### C7 · ALTERED — the de-vendoring ruling was applied to spec 71 but not to four decisions it depends on

- **David said** (line 693, verbatim): *"I'd prefer that we take R503 out of the taxonomy **everywhere except
  the R503 side of the HAL**; this would let us swap out a different FP sensor when/if we choose."*
- **The specs say:** `[CXC-FPID-SEAM]` (71:89) defines its own tell — *"any id, property key, namespace
  literal or schema field **above this seam that names a vendor**"*. Four ids fail it, and 71 cites all four
  rather than owning them (`[CXC-FPID-OPEN-ITEMS-OWNED-ELSEWHERE]` 71:1086):
  - `[DD-R503-MATCH-LOCALITY]` (70:561) — holds the **identity layer's** `10 × 2 = 20` design bound, cited
    from `[CXC-FPID-TIER-WORKING-SET]` (71:404).
  - `[DD-R503-LATCH-FAILSAFE]` (70:530) — the spec itself says this belongs to *the integrating device*, which
    is not the sensor side of anything.
  - `[DD-R503-ASSURANCE-NAME]` (70:516) — spec 71 calls it *"Fleet-wide vocabulary"* in the same breath.
  - `[DD-R503-NAMESPACE-SPELLING]` (70:579) — the `fingerprint/*` semantic surface, which 71 declares is
    *above* the seam (`[CXC-FPID-ABOVE-THE-SEAM]` 71:98).
- **Why it matters:** on a sensor swap these four decisions travel with a vendor's name on them. 71:60 makes
  the argument itself — *"renaming an id that carries a verdict into every citation is cheap exactly once"* —
  and then does not apply it to the four. Cheapest **before** the composeio merge (**C4**).
- **Recommendation:** re-home all four above the seam as `[DD-FPID-*]` / a device-integration spec, leaving
  pointer stubs. Or, if that is more churn than it's worth, state in `[CXC-FPID-OPEN-ITEMS-OWNED-ELSEWHERE]`
  that the tell is knowingly violated for these four and why — an acknowledged exception beats a silent one.

**Ratify? _______________**

---

### C8 · MISSING — David's `ready` = blue/purple LED intent has no normative home

- **David said** (line 757, verbatim): *"One semantic state for the sensor's LED should be the quivalent of
  'ready for authentication' - **suggest a blue or purple color for this** vs the obvs green / red for
  matched/failed."*
- **The specs say:** `[CXC-FPID-COLOURS-BELONG-TO-THE-SENSOR-SPEC]` (71:749) records it and explicitly
  disclaims it — *"⚠ Recorded here as INTENT **to be authored THERE**, not as a rule of this layer"* —
  pointing at `[SPEC:CXC-R503]` / `[CXC-R503-AURA-LED]`. **Spec 70 never received it.**
  `[CXC-R503-AURA-LED]` (70:248–258) is pure datasheet: six modes, seven colours, no semantic-state mapping of
  any kind. I grepped spec 70 for `reject_silent`, "ready", "semantic state", "blue", "purple" — the only hits
  are the datasheet colour table and a driver-drift note.
- **Why it matters:** your only concrete UX preference in the whole session is currently normative **nowhere**,
  and it is the *one* preference a builder would otherwise default wrong (green-for-ready is the obvious
  choice). It also drags `[CXC-FPID-REJECT-SILENT-RENDERING]`'s must-render-identically constraint with it —
  that constraint binds on a mapping table that does not exist.
- **Recommendation:** author the semantic-state → colour/mode/duration realization table in spec 70 now, with
  `ready` = blue or purple, `accept` = green, and `reject_silent` **as a literal duplicate row** of the
  genuine non-match row.

**Ratify? _______________**

---

### C9 · ALTERED — you named two push modes; the spec forbids one of them on the wire

- **David said** (line 351, verbatim): *"push these into all the connected R503s, **either as an \*add\* to the
  existing set or** a 'delete all, add players 1, 3 and 15 and their RBAC profile'"*
- **The spec says:** `[CXC-FPID-ROSTER-SYNC-MODE]` (71:605–614) — *"the declarative desired-state set is the
  PRIMITIVE sync mode and **the only one** … an 'add this player' request is expanded BY THE SERVICE … **It is
  never a wire primitive**"* — and `[CXC-FPID-DELTA-DRIFTS-SILENTLY]` (71:623) *"⛔ **FULL RESYNC IS THE ONLY
  SYNC MODE**."*
- **Why it matters:** low, and the argument is good — you separately ratified the rhythm at line 613 (*"re-
  pushing roster at game start will be the normal rythm, and def not an edge case"*). Flagged because the
  capability you asked for by name is preserved only as a service-side convenience, and nothing records that
  the narrowing was a choice. Note also that the estimated per-push cost on classic CAN is **~4,400 frames,
  roughly a second of bus time** (`[CXC-FPID-FULL-RESYNC-COST-DERIVED]` 71:643) — every time, by design.
- **Recommendation:** ratify as-is, and add one line to `[CXC-FPID-ROSTER-SYNC-MODE]` recording that David
  named an incremental `add` and it was deliberately demoted to service-side sugar.

**Ratify? _______________**

---

### Minor corrections (batch — no separate slot needed)

- **M1 · ALTERED (arithmetic).** `[CXC-R503-MATCH-TIME-AMBIGUOUS]` (70:340) says *"20 × 10 ms = 200 ms, which
  is **noise** against the ~0.7 s capture-dominated transaction."* The 0.7 s figure
  (`[CXC-R503-CAPTURE-DOMINATES-INFERRED]` 70:354) is `GetImg <0.2 s + GenChar <0.5 s + Search` — the search
  term is **additive**, so the pessimistic total is **0.9 s, a 29 % increase**, not noise. The conclusion
  (sensor-side wins under both readings) is unaffected; only the word "noise" is wrong.
- **M2 · ALTERED (citation rule violated).** Spec 71 states its own rule that *"every device fact here is a
  citation to a `[CXC-R503-*]` node"* (71:50, :113). `[CXC-FPID-OCCUPANCY-READBACK-LIMIT]` (71:544) breaks it —
  *"the R503's `ReadIndexTable` returns a **32-byte bitmap per page, per manual V1.4.1 p.14**"* — an
  uncited-to-node raw manual fact, and there is **no** `[CXC-R503-*]` node asserting it (spec 70 mentions
  `0x1F ReadIndexTable` only in the opcode list at 70:221). `[CXC-FPID-EV-02]`'s bit-order test depends on it.

---

# §3 — ⛔ A FINDING ABOUT THE INSTRUMENT: `tools/extract_design_inputs.py` dropped 25 % of David's design statements

This is a finding in its own right, and it undermines any audit built on the tool alone.

**Measured.** The tool, run exactly as briefed
(`--from-match "R503 fingerprint sensor"`), returns **21** statements. Independent grep of the raw transcript
finds **28** distinct human design statements in the same range. **Seven were never seen.**

**Root cause.** `_is_the_human()` requires `rec.get("type") == "user"`. A message David types **while the
assistant is working** is queued by the harness and is persisted as a `type: "attachment"` record with
`attachment.type == "queued_command"` and `origin.kind == "human"` — never as a `type: "user"` text record.
The tool's docstring anticipates the *mid-turn delivery wrapper* trap; it does not anticipate the
**queued-command** class at all, and there is no instrument watching for it.

**What was lost — the seven, by transcript line:**

| Line | David's words (opening) | Load-bearing? |
|---|---|---|
| 377 | *"Practically speaking, we'll never have more than 10 fingerprints per R503."* | ⛔ **Yes** — the premise of `[DD-R503-MATCH-LOCALITY]`, quoted in the spec but invisible to the auditor asked to check it |
| 427 | *"I'm fine with (person_id, finger)… index finger… assumed default UNLESS stated otherwise. Actually… both index fingers are scanned per person…"* | ⛔ **Yes** — the whole 10 × 2 = 20 arithmetic, and **D5** |
| 519 | *"…an actual visual editor that runs on a desktop-class machine… identify all the R503s in use… the system should auto-delete a user & fingers if they haven't authenticated in six months"* | ⛔ **Yes** — the editor requirement, **C1**, **C2** and **D2** all originate here |
| 573 | *"tabs for the things that are discreet: RBAC creation/definition, character def'n/RBAC linkage, Sensors… etc."* | ⛔ **Yes** — the tab IA that exposed `capability` and `module` |
| 613 | *"…the XO may always have the ability to authorize the use of nuclear weapons… but if a game module has him planetside…"* | ⛔ **Yes** — the entire `[CXC-FPID-RBAC-SCOPE-MODEL]` ruling |
| 856 | *"…cross check anywhere you had to interpret my own guidance due to me being insufficienlty precise…"* | The AMBIGUOUS direction itself |
| 887 | *"Suggest you have it hand you a DSD that you can feed to me."* | The instruction that produced this document |

**Why this is severe.** The seven are not a random sample — they are disproportionately the **highest-value
statements in the session**, because the ones David types while the architect is mid-task are the ones he
types urgently. An auditor trusting the tool would have found **no source** for the retention requirement, the
editor requirement, the tab IA, or the RBAC-scope ruling, and would most likely have reported them as
**INVENTED** — four confident false positives — while missing **C1** and **D2** entirely.

**What is NOT wrong with the tool:** everything it *did* return is byte-identical to the raw record (verified
programmatically), the machinery filters correctly reject all 13 non-human `type: "user"` records in range,
and the prefix-dedup at 708/712 correctly kept the longer copy. The defect is a **blind spot, not a
distortion**.

**Recommendation (architect, not David):**
1. Collect `type: "attachment"` records where `attachment.type == "queued_command"` and
   `attachment.origin.kind == "human"`, keyed by `attachment.timestamp`, and merge into the ordered statement
   list; dedup against `type: "user"` records by exact text.
2. Emit a **counted reconciliation** — "N statements from `user` records, M from queued commands" — so a
   future silent class-loss is visible in the output rather than invisible by construction.
3. Widen the dedup beyond `out[-1]`: it currently only compares against the immediately preceding kept
   statement, which worked here only because 708/712 were adjacent.

---

# §4 — Directions that came back CLEAN (a clean direction is a real result)

- **INVENTED, broadly: clean.** I checked every node in both specs that attributes a position to David — 24 of
  them. Twenty-two attribute accurately, several with impressive precision (`[CXC-FPID-RBAC-SCOPE-MODEL]`
  reproduces the planetside argument almost word for word; `[CXC-FPID-UNIFORM-INDEX]` correctly credits the
  proposal to David *over* the architect's own). **Two fail: C2 and C6.** The architect's known invention
  failure modes from earlier in the session — the over-specified attempt event, per-site service replication,
  the device-side retention requirement — are all **correctly recorded as withdrawn** at 71:788, 71:1074 and
  71:1048.
- **Arithmetic: clean except M1.** Verified independently:
  `10 × 2 = 20` ✓ · `20 × 1536 = 30,720 bytes` ✓ · `30,720 / 7 ≈ 4,389 → "roughly 4,400 frames"` ✓ ·
  `4,400 frames × ~110 bits / 500 kbit ≈ 0.97 s → "about a second"` ✓ · `200 slots / 2 = 100 people`, ~10× the
  10-player bound ✓ · `6 captures × 2 templates = 12 presentations; × 10 players = 120` ✓ ·
  `20 × 10 ms = 200 ms` ✓ (only its characterization as "noise" is wrong — **M1**).
  I also confirmed no stale ten-template / 100 ms arithmetic survives anywhere in spec 70 from the pre-
  correction draft.
- **The design doc's claim to record every ruling: substantially holds.** Every one of David's statements I
  extracted is traceable into `fleet-identity-design-2026-08-15.md`, including the four the *tool* missed —
  the architect had them in-conversation even though the extractor could not see them. **One ruling reached
  neither the design doc nor a spec: none.** One design-doc **open item** failed to reach the spec (§10.5 #4,
  the device registry — that is **C1**), and on two items the design doc is **more honest than the spec**
  (§11.3 says RECOMMENDED where 71 says DECIDED — **C6**; §17.2 words the testability point carefully where
  71 overstates it — **C2**).
- **MISSING, broadly: sparse.** Only C1 and C8 are genuine losses. The editor-side rulings David gave that are
  out of scope for these two specs (maximal widget decomposition + reuse of existing primitives, line 554;
  FocusManager default-off, line 629; the virtual-keyboard overwrite defect, line 629; the later colonial-fleet
  restyle, line 554) are all carried in the design doc §§10.3, 10.4, 11.6, 11.7 and correctly declared out of
  scope at `[CXC-FPID-EDITOR-BOUNDARY]` (71:916).

---

# Summary at a glance

**Counts — MISSING 2 · ALTERED 5 (+2 minor) · INVENTED 2 · AMBIGUOUS 5 · instrument defect 1.**

**Questions (§1) — only you can answer:**

| # | Question | My recommendation |
|---|---|---|
| **D1** ⛔ | Working set = whole population, or this module's cast? | **A — this module's cast** (what you said; the only reading compatible with "unbounded") |
| D2 | Does a *denied* match reset the six-month clock? | **A — yes, `user_detected` resets it** |
| D3 | `location` — namespace, payload, or both? | **C — namespace on the wire, location resolvable from the registry** (may want a discussion) |
| D4 | May a person exist with no role? | **A — yes; resolves to the empty capability set** |
| D5 | Index-finger default "unless stated otherwise" | **B — keep the escape hatch as a non-branchable note** |

**Corrections (§2) — ratify, or say "all ratified":**

| # | Correction | Direction |
|---|---|---|
| **C1** ⛔ | Device / Sensors registry has no entity, decision or PRE question — and the upstream message design depends on it | MISSING |
| **C2** | Six-month retention "UN-PARKED" recorded as *David's ruling*; he parked it and never un-parked it | INVENTED |
| **C3** ⛔ | "Every device carries EVERY player" ≠ "10 players per device, unbounded library" | ALTERED |
| **C4** | Placement recorded CLOSED/permanent; you said "until… committed to the main Compose\* repo" | ALTERED |
| **C5** | Spec 70's templates-never-cross-the-bus survived your reversal; spec 32's frontmatter amendment unrecorded | ALTERED |
| **C6** | Two-tier verification promoted RECOMMENDED → `status: decided` with no ratification | INVENTED |
| **C7** | De-vendoring not applied to four `[DD-R503-*]` decisions that are not sensor-side | ALTERED |
| **C8** | Your blue/purple `ready` LED intent is normative nowhere — 71 disclaims it, 70 never got it | MISSING |
| **C9** | You named two push modes; `add` is forbidden on the wire | ALTERED |
| M1 / M2 | "200 ms is noise against 0.7 s" (it's 29 %); one uncited raw-manual device fact in spec 71 | ALTERED |

**Instrument (§3):** `extract_design_inputs.py` misses every message typed while the assistant is working
(`queued_command` attachments) — **7 of 28**, including the four most load-bearing. Architect to fix; no call
needed from you.
