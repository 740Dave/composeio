<!-- dsd-status: answered 2026-08-17T20:05:42Z -->
# DSD — closing the fingerprint HAL contract

> **What this consolidates.** Every open call in `corpus/72-fingerprint-hal-contract.lts` (the HAL interface
> contract, authored 2026-08-16), plus one contingent call from `corpus/70-r503-peripheral.lts`. Each item
> below is already declared open **in the spec itself**, with alternatives and an invalidating assumption
> recorded — nothing here needed discovering. This exists so you answer six questions instead of reading
> 1,100 lines.
>
> **How to answer.** Fill the `Your call:` slot in place, or reply on the channel. **"Recommendations all
> accepted"** is a complete answer — see the summary at the end.
>
> ⛔ **This does not replace reading the spec.** The last DSD was an *audit* — an independent agent checking
> whether I'd represented you correctly. This is a *decision queue*. Different instrument; the audit's job is
> the one no queue substitutes for.

## How to use this

The recommendations are my honest engineering lean, **not a nudge** — overrule freely, and I encode your call,
not mine. ⛔ marks decisions that **block downstream work**; those are worth doing first. The rest can go in
any order. **D6 is not answerable yet** and is included only so it isn't a surprise later.

⚠ **D1 is flagged as wanting a discussion rather than a slot.** You can answer it as a fork, but I think the
design space may be wider than the three options I can name.

---

## Theme A — The shape of the contract ⛔

### D1 · Should enrollment be a stepped SESSION OBJECT, or something flatter?

**What this is.** Enrolling a finger isn't one call — the sensor needs the same finger pressed several times
in a row, over several seconds, with a person standing there. So the HAL needs *some* way to express "this
operation is in progress, here's where it's up to." The authoring agent chose a **session object**: you call
`begin_enroll(slot, ordinal)`, get back a handle, and call `step()` repeatedly until it reports done or
failed. The alternatives are a plain polling function, or a callback the device fires as it goes.

**Why it matters.** ⛔ **CORRECTED 2026-08-17 — this line was STALE WHEN WRITTEN and it is why D1 looked
like a fork.** It originally read: *"it appears in every enrollment call site across the enrollment station,
the storage service and the editor, and changing it later is a rewrite of all three."* **That is false under
[DD-FPHAL-HOST-OR-VM-BINDING], which David closed and the architect recorded hours BEFORE authoring this
document:** those three are ordinary bus clients and **none of them ever calls the HAL.** The HAL has exactly
ONE caller — the device's own firmware. ⚠ The inflated blast radius is what made this look like a decision
worth David's time; the honest stake is one firmware module's internal structure. See the follow-up below.

| Option | Tradeoffs |
|---|---|
| **(a) Stepped session object** — `begin_enroll()` → `step()` / `cancel()` | ⭐ One extra type to learn. **But: the session has no feedback method and no way to reach one**, so "the HAL never drives the ring" becomes a compile-time impossibility instead of a review rule. That guarantee is a property of *this* shape. |
| (b) Poll-handle — `enroll_poll(handle)` | Flatter and more familiar. The handle can be passed anywhere, so the no-ring guarantee weakens back to convention — which is what your ring ruling was meant to end. |
| (c) Callback the device fires per capture | Inverts control into a tick loop that is forbidden to block. Awkward on-device, and the callback becomes a place ring-driving code can hide. |

⚠ **This one wants a discussion, not a slot.** All three satisfy your rulings equally; they differ in what
they make *impossible*. If the strongest answer is "neither — here's a reframe," that's the tell it was a
discussion. I'd rather talk it through than have you pick from my three.

**My recommendation: (a).** Not because it's cleanest, but because it's the only one where the ring guarantee
is enforced by the type system rather than by everyone remembering.

**Your call:** Neither

---

#### ⛔ FOLLOW-UP to your "Neither" — the architect's reframe, 2026-08-17. Please respond below.

You flagged this as not-a-fork, which is what I'd guessed when I marked it "wants a discussion." Here is
where I think the third answer is — offered as a proposal, **not taken as a decision**.

**Under your own runtime ruling, the HAL has exactly ONE caller — the device's own firmware.**
`fingerprint_storage`, the editor and the enrollment station are ordinary bus clients; nothing above the bus
ever calls `begin_enroll`. So all three options I put in front of you describe **an internal firmware API with
a single consumer**, and I presented them as though they were the interface three components program against.

⛔ **They aren't. The interface is the bus messages** — `fingerprint/cmd/enroll` in,
`fingerprint/enroll/status {step, prompt}` out.

**So the reframe: enrollment progress is an EVENT-SEQUENCE problem on the bus, not an object-shape problem in
a language.** What the spec should pin down is the message sequence — what `enroll/status` carries, in what
order, what a consumer sees if it subscribes mid-ceremony, and what happens when a ceremony is abandoned. The
session object then becomes one firmware module's private business and genuinely does not need your ruling.

⚠ **This also weakens my own recommendation, which you should know.** I argued for the session shape because
it makes "the HAL has no voice" a compile-time impossibility. That is true — but if the HAL is internal to
firmware with one caller, the guarantee that actually matters is that **nothing on the bus can command the
ring**, and that is enforced by the message vocabulary, not by the object.

| If the reframe is right | Then |
|---|---|
| **(d) Specify the enrollment MESSAGE SEQUENCE; leave the object shape to the firmware** | D1 disappears as a decision. Costs a small addition to spec 72. ⭐ My revised lean. |
| The reframe is not what you meant | Tell me where you were pointing and I will follow — I would rather be corrected than build on my guess at your "Neither". |

**Your response: (D) - specify the enrollment message sequence**

---
**Comments:** I think we need to discuss this item.  There's no reason why the HAL riding over the FP firmware can't emit a semantic message upstream  to advertise the general interrupt case and finger capture 2 of N - as long as these are messages that stay on the internal bus.  In fact, the HAL should be able to answer questions like "How many touches does this fingerprint sensor require for registration".  My hunch is that we need to walk the design of the HAL a bit more carefully to make sure that it fits into the Compose design model (and before we do this re-analysis, please step back and reconsider the design in light of these two design nudges, you might be able to disprove these as valid approaches, or you might be able to extend this into a more cohesive design).Be

---

## Theme B — What the contract ANNOUNCES about itself

### D2 · `supports_challenge_match` is a declared capability. What operation does it actually gate?

**What this is.** Every HAL implementation must publish a `capabilities()` record — a small set of facts about
what the attached sensor can do (how many slots, whether templates can be read back, and so on) so the layer
above binds to what's really there. One of those declared facts is `supports_challenge_match`. A *challenge
match* would mean: "here is one specific slot — is the finger on the sensor right now that person?" — a 1:1
check against a named person, as opposed to the normal 1:N search across everyone.

⛔ **The field is in the ruled list. The operation it announces is defined in no node of this corpus.** Its
only description anywhere is inside a *rejected* alternative in `[DD-FPID-VERIFICATION-IS-TWO-TIERED]`.

**Why it matters.** A mandatory announcement whose meaning is undefined is the exact defect this whole
document was written to remove, reproduced one layer down — and it's cheapest to settle *before* two
implementations each guess at it.

| Option | Tradeoffs |
|---|---|
| (a) Keep it — define `verify_slot(slot) -> Score \| None` | Matches the plain reading, and the R503's `Match 0x03` does exactly this. Costs a method nothing currently calls. |
| **(b) Drop the field entirely** | ⭐ Nothing in the corpus consumes it, and the verification method it came from was *rejected*. Removing it costs nothing today. Re-adding it later is one field. |

**My recommendation: (b), drop it.** An unused capability is one that two implementations will interpret
differently before anyone notices — which is the failure `capabilities()` exists to prevent.

**Your call:** (b) Drop the field entirely

---

### D3 · What does a match `score` MEAN — and may anyone act on it?

**What this is.** When the sensor matches a finger it returns a confidence number alongside the slot. That
number is **already published fleet-wide** — `[CXC-R503-HAL-BINDING]` declares the result surface as
`fingerprint/match {id, score}`. Nothing anywhere says what the number means, what its range is, or whether
it's comparable between two different sensors.

**Why it matters.** If anyone ever thresholds on it — "only accept above 80" — that threshold is tuned to one
vendor's scale. After a sensor swap it's wrong **silently, and in the permissive direction**: it lets people
in it shouldn't.

| Option | Tradeoffs |
|---|---|
| **(a) OPAQUE — diagnostic only; no consumer may threshold on it** | ⭐ Cheapest and honest. Makes the published number nearly useless, which does invite someone to use it anyway. |
| (b) NORMALIZED to a stated range by each implementation | Gives the number real meaning. Costs a documented mapping per sensor, and we can't verify it without a second sensor. |
| (c) Announce the scale as a `capabilities()` field | Most honest of the three. One more mandatory field on every implementation, forever. |

⚠ **Nothing is blocked either way** — this only bites when a consumer of `score` appears, and none exists.
Worth noting the sensor's own internal security level, not our threshold, is what actually decides a match.

**My recommendation: (a), opaque.** The honest position is that we can't give the number meaning we haven't
measured, and pretending otherwise is worse than leaving it diagnostic.

**Your call:** (a) OPAQUE — diagnostic only; no consumer may threshold on it
**Comments:** This value should not be published outside the device.  I would like the capability to set a match threshold  confidence level that must be met, but that's internal only and not emitted on the ComposeBus.  This confidence threshold must be settable in the YAML, with null being interpreted as unset.

---

## Theme C — Things added beyond what was ruled ⛔

### D4 · Four ADDITIONS the authoring agent made on its own judgement — accept or reject as a set

**What this is.** `[CXC-FPID-HAL-CONTRACT]` listed ten operations the HAL must have, and
`[CXC-FPID-HAL-CAPABILITY-ANNOUNCEMENT]` listed candidate fields. While writing actual function signatures the
agent found four things those lists don't cover but the rest of the design requires. It added them and named
them together so you can reject them as one set rather than one at a time.

**Why it matters.** One of them isn't optional: **without `write_template` the roster push — the mechanism the
entire identity layer is built on — has no function to call.** That gap was invisible until someone tried to
write the signatures down.

| Option | Tradeoffs |
|---|---|
| **(a) Accept all four** | ⭐ `write_template` (roster push has no surface without it) · `template_size_bytes` (see below) · `MatchOutcome.NO_FINGER` (distinguishes "nobody touched it" from "no match") · `FailureAxis.UNSUPPORTED` (lets a sensor say "I can't do that" distinctly from "I refused") |
| (b) Accept `write_template` only; defer the rest | Unblocks the roster, leaves three smaller gaps that will each resurface during the build. |
| (c) Reject all four; re-derive from the ruled lists | Keeps the ruled lists sovereign. The roster stays unbuildable until the lists are amended. |

⭐ **`template_size_bytes` stopped being tidiness yesterday.** You said your sensors may be V1.1-era firmware —
where a fingerprint template is **768 bytes, not 1536**. That halves the roster-sync arithmetic, and this field
is the mechanism that makes the difference *announced* rather than a wrong constant compiled in.

⚠ **`MatchOutcome.NO_FINGER` is the one I'd scrutinise.** You ruled the sensor's outcome vocabulary at exactly
three values; this adds a fourth. The agent argues it never crosses the seam so it adds no token to your
vocabulary. That argument is rejectable.

**My recommendation: (a), accept all four** — with `NO_FINGER` flagged as the one worth a second look.

**Your call:** (a) Accept all four

---

## Theme D — Behaviour at the edges

### D5 · Deleting a slot that's already empty — success, or error?

**What this is.** The sensor stores fingerprints in numbered slots. Clearing a device before a roster push
means deleting slots — including ones that may already be empty. The manual doesn't say what the part does in
that case, and `[CXC-R503-CONFIRMATION-CODES]` doesn't cover it. The spec currently declares it **idempotent**
(deleting an empty slot succeeds) and labels that a *transcription judgement, not a ruling*.

**Why it matters.** Every roster push hits this on every slot it clears. If it's an error, the push logic
needs a "was it occupied?" check before each delete, and the desired-state model gets a special case it
currently doesn't have.

| Option | Tradeoffs |
|---|---|
| **(a) Idempotent — ratify now** | ⭐ Matches desired-state semantics, where you declare the end state and don't care about the path. Risks contradicting what the part actually does. |
| (b) Leave open; let `[CXC-FPHAL-EV-04]` settle it on the bench | Five minutes with hardware gives the real answer. One more thing pending a bench session that's imminent anyway. |

**My recommendation: (a), ratify idempotent** — and let EV-04 confirm it. If the part disagrees, the HAL
absorbs the difference, which is what a HAL is for.

**Your call:** (b) Leave open; let [CXC-FPHAL-EV-04] settle it on the bench

---

## Theme E — Pending the bench ⚠ do not answer yet

### D6 · If the ring has no GREEN, what does `accept` show?

**What this is.** The R503 has a coloured LED ring around the sensor. The 2023 manual documents seven colours;
the **2019 manual documents three** — Red, Blue, Purple. You said your sensors are years old and may be
V1.1-era firmware. The realization table currently maps `accept` → green.

**Why it matters.** Your one stated UX preference was `ready` in blue or purple *"vs the obvs green / red for
matched/failed."* If green doesn't exist, `ready` survives and **the thing it was chosen to contrast against
does not.**

| Option | Tradeoffs |
|---|---|
| `accept` in blue | Collides with `ready` — the two states a user most needs to tell apart become the same colour. |
| `accept` in purple | Collides with `ready`'s other option; same problem, different corner. |
| Distinguish by MODE, not colour (steady vs breathing) | Runs into the R503's **five-second minimum animation cycle** — a per-press acknowledgement can't be expressed as a breathing mode. |

⛔ **Do not answer this now.** `[CXC-R503-EV-25]` settles whether green exists in about ten seconds once the
sensor is wired, and the probe that runs it is built and verified. Listed here only so it can be answered in
one pass with the bench result.

**Your call (after EV-25): _______________**

---

## §2 — RATIFY (4) · calls I already took; a one-word confirm is enough

**R1 · The eight containment invariants** (`[CXC-FPHAL-CONTAINMENT-INVARIANTS]`) — my reading of what your
containment ruling requires, each tied to a documented defect and each with a `TELL` a reviewer can grep for.
Worth a glance: **inv-7** — where the manual contradicts itself, take the reading that fails *later* (wait the
15 s flowchart value, not the 10 s prose value; aborting a live operation is the worse failure). **inv-8** —
never issue a baud change at all.
**Ratify? confirm**

**R2 · The mock is the CANONICAL REFERENCE** (`[CXC-FPHAL-MOCK-ARBITRATION-ROLE]`) — spec 71 says mock and
`R503HAL` are peers and "neither is privileged," which left the parity test with no answer to "which is right
when they disagree." Recorded as an amendment *citing* spec 71 rather than editing it.
**Ratify? confirm, note we may discover when working with the R503 that the HAL surface itself requires changes.  Not unusual to happen.**

**R3 · The mock's FAULT MODEL survives; the mock-as-development-enabler does not.** Your ruling killed mocking
the ESP32 for buildout. It did not kill the fault injector — you can't make a real R503 emit an undocumented
error code on demand, and that's what proves the containment boundary holds. ⛔ I recorded the opposite first
and corrected it; both commits stand in order.
**Ratify? I'm not 100% sure what you mean here, but a mock implementation of a fingerprint sensor via the HAL is critical for for upstream capability to be tested, such as the widgets for enrollment.  To put a fine point on it: QA testing of ComposeBus widgets requires postitive and negative case testing that you want to do without a human in the loop putting their finger on the sensor, even if you have a fully integrated and fully landed R503 implementation.**

**R4 · Separate 3.3 V regulator for the sensor** — architect's lean, not ruled. Don't share the SuperMini's
LDO: the R503's input is unregulated and feeds its imaging chip directly, no inrush/peak/LED-current figure
exists in any manual revision, and the low-power mode hard-switches VCC repeatedly. Costs one part.
**Ratify? this is overly conservative, i've slaved the R503 to ESP32 3.3.v LDOs before with no problem.  If we need VCC to be switchable on/off for the R503, that's different and wouldn't that require that we put a transistor like the 2N2222 connected to a 3.3v source and one of the ESP32's GPIOs?**

---

## Summary at a glance

| | Decision | My recommendation |
|---|---|---|
| **D1** ⛔ | Enrollment shape | **Stepped session** — the only option where "the HAL never drives the ring" is compile-time ⚠ *wants a discussion* |
| **D2** | `supports_challenge_match` | **Drop the field** |
| **D3** | What `score` means | **Opaque — diagnostic only** |
| **D4** ⛔ | The four additions | **Accept all four** (⚠ `NO_FINGER` worth a second look) |
| **D5** | Delete an empty slot | **Idempotent — succeeds** |
| **D6** | Ring `accept` colour | ⚠ **not answerable — wait for EV-25** |
| **R1–R4** | Ratifies | Confirm as recorded |

⭐ **"Recommendations all accepted" closes D2–D5 and R1–R4** — leaving D1 for a conversation and D6 for the
bench.

## What this does NOT close

- `[DD-R503-NAMESPACE-SPELLING]` — specs 27 and 32 disagree on this device's namespace literals. **Not your
  judgement: it's write access to a corpus this project doesn't own.** Lands with the migration out of staging.
- `[DD-R503-ASSURANCE-NAME]`, `[DD-R503-CONNECTOR]`, `[DD-R503-LATCH-FAILSAFE]` — spec 70, none blocking.
- 25 evidence slots, all EMPTY. Three need no hardware; the rest wait on the bench.
