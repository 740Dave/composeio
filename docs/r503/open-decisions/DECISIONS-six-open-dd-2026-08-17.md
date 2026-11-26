# Decision-support — the SIX open `[DD-*]` on the R503 project

> Consolidates **every** decision still `status: open` across the three R503 specs
> (`corpus/70-r503-peripheral.lts`, `71-fingerprint-identity.lts`, `72-fingerprint-hal-contract.lts`).
> ⛔ **This is the complete open set, not a selection** — verified by
> `lts_find(corpus="r503", prefix="DD", status="open")` against the live index on 2026-08-17,
> immediately after five other `[DD-FPHAL-*]` were re-stated from a stale `open` to `decided`/`deferred`.
> **Answer by filling the `Your call:` slot in place, or on the channel — whichever is faster for you.**
>
> ⚠ **Delivered as a plain file rather than through the DSD viewer**, because
> `send_dsd_for_human_review` cannot reach a path outside the composeio checkout. That is a
> **confirmed** defect, written up as ask A in `docs/COMPOSEIO-REQUEST-dsd-roots-2026-08-17.md`.

## How to use this

The recommendation on each is **my honest engineering lean, not a nudge** — overrule freely and I encode
your call, not mine. ⛔ **Only D1 and D2 block work**, and they block different work; the other four can be
answered in any order, or deferred without stalling anything. Two of the six (**D3**, **D6**) are flagged
⚠ **discussion, not a slot** — the honest answer there may be a third thing neither option names, and
forcing a binary would cost us the better design.

⭐ **Three of the six are genuinely cheap for you**: D4 and D5 are one-word answers, and D2's real content
is a single sentence about who owns a fact.

---

## THEME 1 — Blocks the build now

### D1 · Does the roster reach a device by PUSH or by PULL?

**What this is.** The "roster" is the set of fingerprint templates a device must hold — the cast list, as
bytes. `fingerprint_storage` is the service that owns the library; a device holds a working subset in its
own flash. This decision is *which side initiates the transfer*.

**Why it matters.** ⛔ **It is the single largest gap in the corpus and it blocks three of the six
implementation targets** (`fingerprint_storage`, the enrollment station, the editor). The spec node named
*roster message shape* currently says the device *"fetches or is sent"* each template — one clause, both
directions, neither chosen. Push and pull are **different services**: they differ on who owns retry, on
what a device needs to know about its own inventory, and on what happens when power is cut mid-transfer.

| Option | Tradeoffs |
|---|---|
| **(a) PUSH — the service sends, the device accepts** | ⭐ **Matches every neighbouring ruling**: the service already owns slot allocation and the desired-state sync, so authority is already on that side. The service knows when it is done, which makes "the set is complete" expressible. ⚠ Cost: the service must track per-device progress and own retry, and a device that was offline needs a catch-up path |
| (b) PULL — the device fetches what it lacks | The device owns its own retry and recovers from a power cut by simply asking again, which is the more robust failure story. ⚠ Cost: the device must *know what it lacks*, which means it needs a manifest first — so pull is really "push a manifest, then pull the bytes," i.e. two mechanisms instead of one |
| (c) Hybrid — push the manifest, pull the bytes | Honest about (b)'s hidden first step and gives the device retry ownership. ⚠ Cost: two message families, two failure modes, and the most machinery of the three for a fleet of ~20 templates |

**My recommendation: (a) PUSH.** The authority already sits on the service side in every adjacent ruling,
and push is the only option under which *"the desired state is complete"* is a thing the protocol can say
rather than a thing a device infers. ⚠ **Its honest cost:** a device that was powered off during a push
needs a reconciliation path, and that path is real work I have not specified.

**Your call: A**

---

### D2 · How does a device announce itself — and does it announce its own capabilities?

**What this is.** The device registry answers *"what fingerprint sensors exist, where are they, and what
does each one gate?"* It is ruled **ANNOUNCE-DERIVED** — devices tell the system about themselves rather
than a human maintaining a list. This decision is what that announcement actually contains.

**Why it matters.** ⛔ **A stated INVARIANT currently has no way to work.** The HAL announces its own
limits — slot count, template size, whether it can read templates back — expressly so *"the layer above
degrades HONESTLY."* Every consumer of that announcement is service-side, and under the 2026-08-16 ruling
the service **cannot call a HAL method**. So the invariant that exists to prevent silent degradation is
unobservable by the layer it protects. The current draft of the announcement carries `device_id`, the
gated capability, and `location` — ⛔ **sensor capabilities are not among them.**

| Option | Tradeoffs |
|---|---|
| (a) Fold it into the existing `peripherals:` declaration | No new mechanism, reuses the path a device already has. ⚠ Cost: loads a hardware-binding schema with fleet-identity concepts, and a declaration is authored, not observed — it can lie about the part actually fitted |
| **(b) A dedicated `device/announce` at boot and on change, carrying capabilities** | ⭐ Self-describing and independently queryable, and it is the only option where the capability figures are **measured from the part** rather than declared by a human. Fixes the invariant. ⚠ Cost: a new message class, plus a retention question |
| (c) A service-side registry the editor writes through, devices announcing only liveness | Closest to the Sensors tab as you described it. ⛔ It is the hand-maintained form wearing a UI — the furthest thing from announce-derived, and it re-opens the antipattern the two-message design was built to avoid |

**My recommendation: (b)**, with the announcement carrying `device_id`, the gated capability, optional
`location`, **and the `capabilities()` payload.** ⚠ **The sub-question that decides how big this is:** does
a device know what it gates, or is that assigned centrally per deployment? If it is assigned centrally,
the announcement carries identity only, the capability binding belongs wholly to the service, and this
decision shrinks a lot.

**Your call: B, but to your sub-question, are you referring to the RBAC settings?**

---

## THEME 2 — Physical, and cheap to answer

### D3 · Fail-open or fail-locked, for a latch this sensor drives?

**What this is.** The R503 will unlock physical things for gameplay — hatches, boxes, reveals. The sensor
is `experiential` class and inherits no fail-safe default; the **latch** is `control` class and does get
one on boot and link-loss. So the fail-safe obligation lands on the actuator, and nothing currently says
what its safe state is.

**Why it matters.** ⛔ **The two failures are not symmetric, and one of them is a physical-safety
consequence rather than a gameplay one.** Fails-LOCKED preserves game state across a power blip — the
reveal is not spent — but a box that latches shut on power loss **can trap what is inside it**.
Fails-OPEN is safe and spends the reveal, turning a blip into a spoiled scene.

| Option | Tradeoffs |
|---|---|
| (a) Global fail-open | Safest and cheapest to reason about. ⚠ Spends every in-flight reveal on any blip |
| (b) Global fail-locked | Preserves drama. ⛔ Unacceptable for any enclosure a person or animal can be inside |
| **(c) PER-HATCH declaration — safe state is a required property of each latch** | ⭐ Matches the physical reality that a reveal box and a crawl-in hatch want opposite answers. ⚠ Cost: a schema field, and a required one, so nobody can forget it |
| (d) Fail-locked with a mechanical release | Moves the answer out of software entirely — the most robust for the trap case. ⚠ Cost: a physical part per enclosure |

**My recommendation: (c)**, and I'd make the field **required with no default**, so an integrator must
state it rather than inherit it.

⚠ **This one wants a discussion, not a slot.** The real answer may be **(c) + (d) together** — per-hatch
declaration for the software, *plus* a mechanical release on anything with interior volume — and a binary
cannot reach that. ⛔ **Its invalidating assumption is yours to confirm, not mine:** if every latch drives
a drawer or a lid with no interior volume, the asymmetry vanishes and a global fail-locked default is fine.

**Your call: C, a mechanical release need not be characterized at a sw level.**

---

### D4 · Which connector — SH1.0 (1.00 mm) or PicoBlade (1.25 mm)?

**What this is.** The six-pin connector on the R503's flying lead, and therefore the mating footprint on
any board we design for it.

**Why it matters.** A wrong pitch is a board respin. ⛔ **The manual changed this figure silently:** V1.1,
V1.2.1 and V1.4 all say `MX1.0--6P`; V1.4.1 says `SH1.0--6P` consistently, and the changelog does not
mention it. ⚠ Worse, our two project sources **disagree about what "MX1.0" even denotes** — one reads it
as JST-SH 1.0 mm, the other as Molex PicoBlade 1.25 mm. There is no standard Molex part called "MX1.0",
and PicoBlade is 1.25 mm. Both readings are defensible from the string alone.

| Option | Tradeoffs |
|---|---|
| (a) Design for SH1.0, 1.00 mm | Follows the newest revision. ⚠ Wrong on three older revisions, and a respin if the part in hand is older stock |
| (b) Design for PicoBlade, 1.25 mm | Follows the majority of revisions. ⚠ Contradicts the newest |
| **(c) Fly a pigtail to a 0.1" header; defer the footprint until measured** | ⭐ **The document cannot answer this and a caliper can.** Costs nothing on a bring-up board and removes the respin risk entirely |

**My recommendation: (c) — and this is really a request rather than a decision.** ⛔ **Please put calipers
on one of the three modules.** One measurement retires a contradiction four document revisions could not,
and it also bears on `[CXC-R503-PART-IDENTITY-UNCONFIRMED]`: if the part is an R503-5V the answer is
`MX1.25--6P` **and the rail is 5 V**, which is a different and more dangerous question.

**Your call: Intend to cut off sh/jst connector and solder direct to a prototyping board.**

---

## THEME 3 — Vocabulary and calibration; neither blocks anything

### D5 · Keep the `experiential` assurance class name, or rename it?

**What this is.** Every ComposePlatform peripheral declares an `assurance` class so the HAL never
over-promises. The closed set is `experiential` / `measurement` / `control`. A consumer fingerprint reader
is `experiential` — spoofable, no liveness, no secure element: theatrical, not access control.

**Why it matters.** ⚠ `experiential` describes a **vibe**, so in practice it needs a trailing *"NOT access
control"* clause every single time it is used — in our specs and in the platform's own assurance node. A
name that carried its own content would not need the clause. ⛔ But it is one of a **closed set of three**,
cited by name in a compliance row and in two other platform specs, so renaming is a fleet-wide vocabulary
change rather than a word swap.

| Option | Tradeoffs |
|---|---|
| (a) Rename to something stating the negative (`not-approved-for-real-world` or a sibling) | Carries its own meaning; the clause disappears. ⛔ Fleet-wide vocabulary change across at least three specs and a compliance row |
| **(b) Keep `experiential`; make the disclaimer a REQUIRED PROPERTY of the class, not prose** | ⭐ Cheap, keeps the closed set stable, and — the real win — **makes the negative queryable** instead of re-typed. A missing disclaimer becomes a lint finding rather than a thing a reviewer must notice |
| (c) Keep it, change nothing | Zero cost. ⚠ The clause gets re-typed by hand forever, and eventually somebody omits it |

**My recommendation: (b).** It converts a naming complaint into a mechanism, which is the move this project
keeps finding is the right one. ⚠ **Its invalidating assumption:** that a human reads the class name and
needs it to carry content. If the class is only ever consumed by a validator that emits its own warning
text, the name is arbitrary and the whole question dissolves.

**Your call: not-approved-for-real-world**

---

### D6 · Is our match threshold ADDITIVE to the part's own security level, or does it REPLACE reliance on it?

**What this is.** You asked for a settable match-confidence floor, device-internal, YAML-configurable,
`null` meaning unset. ⛔ **The R503 also has its own internal security level** — the part decides a match
against that setting first, and only then returns a score our floor can judge. So there are **two
confidence gates in series** on the same decision, and neither knows about the other.

**Why it matters.** ⛔ **The part's documented default security level CHANGED from 5 to 3 between manual
revisions, unannounced.** So the first gate's setting is not a constant, is not announced on the wire, and
**may differ between two modules sitting on the same desk.** Under the additive reading we would be tuning
our floor on top of a number nobody has measured — which is the same silent-permissive failure the
score-is-opaque ruling was made to prevent, arriving through the other gate.

| Option | Tradeoffs |
|---|---|
| (a) ADDITIVE — leave the part at whatever it ships with, apply our floor on top | Cheapest. ⛔ The effective threshold becomes the composition of a known number and an unknown one, and a module swap silently re-calibrates the fleet |
| **(b) REPLACES — PIN the part's level explicitly to a known permissive value, do all gating in our floor** | ⭐ One threshold, ours, measurable, identical across modules. ⚠ Cost: writing a vendor parameter, which the containment invariants are otherwise reluctant to do |
| (c) PIN and leave our floor `null` — the part is the only gate | Simplest of all. ⚠ Throws away the capability you asked for before it has ever been used |

**My recommendation: (b).** Two gates in series where one drifts silently is not a safety margin, it is an
unmeasurable threshold.

⚠ **This one wants a discussion too, and it is genuinely bench-dependent.** Its invalidating assumption is
that the part's security level is **readable, settable, and monotonic in its effect on the score**. If
reading it back is unreliable — or if changing it changes the *scale* of the returned score rather than
only the accept boundary — then (a) is unanalysable and the question collapses onto (b) anyway.
⛔ **NOT BLOCKING:** a device can ship with the floor `null` and behave exactly as it does today, so this
can stay open without stalling the build.

**Your call: Additive only, and likely not to be used unless we see issues with the R503 throwing false positives at high confidence levels.**

---

## Summary at a glance

| # | Decision | My lean | Blocks? |
|---|---|---|---|
| **D1** | Roster PUSH or PULL | **(a) PUSH** | ⛔ **Yes — 3 of 6 build targets** |
| **D2** | Device announcement shape, and does it carry capabilities | **(b) dedicated announce, capabilities included** | ⛔ **Yes — a stated invariant cannot work without it** |
| D3 | Latch fail-open or fail-locked | **(c) per-hatch, required field** ⚠ *discussion* | No |
| D4 | Connector SH1.0 vs PicoBlade | **(c) defer — please measure one** | No |
| D5 | `experiential` name | **(b) keep, make the disclaimer a required property** | No |
| D6 | Threshold additive or replaces | **(b) REPLACES — pin the part** ⚠ *discussion* | No |

⭐ **"Recommendations all accepted" is a complete answer** — but if you only have a minute, **D1 and D2 are
the two that unblock work**, and **D4 is a caliper rather than a judgement.**
