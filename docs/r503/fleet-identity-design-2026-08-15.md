# R503 fleet identity — the design, before it becomes a spec

**Status:** design narrative, authored 2026-08-15 by the architect from David's rulings the same day.
Not a spec. The spec is authored FROM this. Where this and David disagree, David wins; where this and the
corpus disagree, the corpus wins and David gets told.

**Why this file exists:** the design was settled in conversation and would otherwise live only in a transcript
a summarizer will flatten. This is the reviewable artifact — redline the *reasoning* here, not the spec's output.

---

## 1. The entity model (David, 2026-08-15)

His words: *"Someone named 'Dave' is playing the XO, the XO's name is 'Col. Saul Tigh', has player ID X, this
is the XO's RBAC, and here's Dave's fingerprint template."*

Four distinct things, and collapsing any two of them is the design error:

| Entity | Owns | Lifetime |
|---|---|---|
| **Person** (Dave) | the biometric templates | stable across campaigns |
| **Character** (the XO, "Col. Saul Tigh") | the RBAC / game role | stable across castings |
| **Casting** (Dave *is playing* the XO) | the person↔character binding | **session-scoped, mutable** |
| **Template** | the finger itself | belongs to the PERSON |

**The payoff:** re-casting a game requires **no re-enrollment and no template push**. Only the casting table
changes. Dave gets promoted to Admiral; his finger does not change.

**Therefore the device index is keyed by PERSON**, never by character and never by casting. This is the whole
reason the model is worth having.

### The match chain — four hops, and the sensor answers only the first

```
R503 match  ->  slot        (the sensor's ONLY output; a device-local slot number)
middleware  ->  person      (slot -> person, via the assignment table)
middleware  ->  character   (person -> character, via the CASTING table)
middleware  ->  RBAC        (character -> permissions)
device      ->  decision    (access_granted, or whatever this device does)
```

⛔ **The R503 knows nothing past hop 1.** It has no concept of person, character, role, or permission — and
must not acquire one.

---

## 2. Two storage tiers — library and working set

David, 2026-08-15: *"what gets loaded into a designed-into-the-game R503 is 10 players, but the
fingerprint_storage service on the compose bus needs to be able to store an unbounded number of game players."*

| Tier | Holds | Bound |
|---|---|---|
| `fingerprint_storage` service (ComposeBus) | **every** person ever enrolled | unbounded |
| An R503's on-module store | a **selected working set** | 200 hardware; 10 players by game design |

**This is not a new pattern.** It is ComposeIO's already-settled *"bus storage is source of truth, local flash
is cache"*, applied to biometric templates. Recognising that is what keeps this from being a bespoke subsystem.

**Consequence:** a roster push is a **SELECTION**, not a mirror. "Delete all, add players 1, 3 and 15" is a
working-set projection of an unbounded library onto a bounded device — which is what David described from the
start.

---

## 3. Templates per person: ONE or TWO, and the ordinal is meaningless

David, 2026-08-15: both index fingers are scanned per person, either resolves to the same identity — **but**
*"some users may choose not to enroll both fingers, and as we have no need to track left-vs-right, that's fine,
it's simply user x, finger 1, finger 2."*

- Per person: **1 or 2 templates.** Two is the game-design default; one is legitimate and must not be an error.
- **`finger_1` / `finger_2` are ORDINALS, not anatomy.** Nothing tracks left vs right. No UI labels them. No
  logic branches on them.
- Both ordinals resolve to the **same person and the same character**. Downstream they are indistinguishable.

⛔ **The schema must NOT hard-code two** (David concurred). Express as `person -> [slot, ...]`; the *game
design* fixes the count at two, the *structure* permits N. A bandaged finger mid-campaign then costs a slot,
not a schema change.

**The bound must fail LOUD at N+1.** The R503 has confirmation code `0x1F` (library full); a roster push that
overflows must surface it, never silently drop a template. This is the platform's own lesson about a limit
sized exactly for today (`kMaxPersist = 4`, one short the moment a config namespace joined).

---

## 4. The fleet index is UNIFORM — and what that costs

David, 2026-08-15: *"we could take the approach that the exact order of each finger in the R503's internal
index will be consistent across all R503s — that's a valid model, and one that would probably eliminate a
category of silent failures."*

**Adopted, and it is stronger than the per-device maps the architect first proposed.**

- Per-device maps: N maps, each drifting independently, and a difference between two devices is
  **indistinguishable from a legitimate difference**. You cannot diff your way to a fault.
- One uniform index: there is exactly ONE correct assignment, so divergence is **mechanically detectable** —
  ⛔ but ONLY by the two-tier verifier in cost #3 below. This bullet originally named `ReadIndexTable 0x1F`
  alone as the check; that was the same false claim, stated twice in one document. It is corrected in ONE
  place, below, deliberately — a correction that leaves the original assertion standing elsewhere in the same
  file is not a correction.

That converts invisible per-device state into a declared fleet-wide fact that can be checked. A whole failure
class is deleted rather than mitigated.

### What it costs

1. **Slot allocation belongs to the `fingerprint_storage` service.** Only it sees the whole population. The
   enrollment station is TOLD where to write; it does not choose.
2. ⛔ **`AutoEnroll`'s auto-assign mode (ModelID `0xC8`–`0xFF`) is FORBIDDEN in fleet mode.** It picks the
   device's next free slot, which is precisely the divergence being eliminated. Always specify the slot.
   This is an invariant, not a guideline.
3. **It needs a verifier or it is only a convention** — and a convention nothing checks will drift.
   ⛔ **CORRECTED 2026-08-15, SAME DAY.** This document originally said: *"the verifier is cheap:
   `ReadIndexTable` vs the declared roster, per device, on every convergence check."* **THAT WAS WRONG, and it
   was wrong in the most dangerous possible way.** `ReadIndexTable 0x1F` returns *"the index table of the
   fingerprint template up to 256 at a time (32 bytes)"* (V1.4.1 p.14) — 256 slots in 32 bytes is **ONE BIT
   PER SLOT**. It reports OCCUPANCY, not IDENTITY. Two devices holding DIFFERENT PEOPLE in the same slots
   produce IDENTICAL BITMAPS, so the proposed verifier would have returned "converged ✓" on exactly the
   divergence the uniform index exists to catch. An instrument that agrees with the wrong answer — the failure
   class this whole platform is organised around, authored into the design by the architect who had just
   finished writing about it. Caught by the spec-writer reading the manual instead of the design.

   **The corrected verification is TWO TIERS, and the difference between them is the difference between
   testimony and evidence:**

   - **CHEAP, CONTINUOUS — testimony.** `ReadIndexTable` (occupancy: is the right NUMBER of slots filled, and
     the right ones?) **plus the module's NOTEPAD.** The R503 provides *"a 512-bytes memory (16 pages * 32
     bytes) for user's notepad, where data requiring power-off protection can be stored"* (V1.4.1 p.5), via
     `WriteNotepad 0x18` / `ReadNotepad 0x19`. The pushing service writes the roster id + hash there in the
     same operation that writes the templates. A device then REPORTS which roster it believes it holds, and
     that survives power cycles. ⚠ This is still the DEVICE'S CLAIM about its own state — it detects a missed
     push, a partial push, and a stale device. It does NOT prove slot N holds person X.
     ⛔ Trap: *"when write on one page of the pad, the entire 32 bytes will be written in wholly covering the
     original contents"* — pages are write-whole, not byte-addressable.
   - **EXPENSIVE, PERIODIC — evidence.** `UpChar 0x08` a slot and hash it against the library's copy. This
     PROVES slot N holds template X. It costs 1536 bytes per slot — a full audit is the same ~30 KB as a full
     push, so it is an out-of-band operation, not a per-match check.

   **Do not let the cheap tier be mistaken for the expensive one.** A receipt settles a structural question;
   identity here is not structural, and dressing testimony as evidence is the same error one level up.

### The precondition it depends on

**Enroll everyone everywhere; differentiate in RBAC, never in enrollment.**

If different devices carry different subsets, slot 4 is a different person on different devices and the
uniformity guarantee is gone. Using template distribution as an access-control mechanism also duplicates the
RBAC and makes re-casting require a template push.

So: every device knows every player; the **character's role** decides what they may do.

**This holds until `cast x 2 > 200`, i.e. roughly 100 people** — not 10. The 10-player design bound has an
order of magnitude of headroom.

---

## 5. The roster is DESIRED STATE, not a delta

Two push modes were described: *add*, and *delete-all-then-add {1,3,15}*.

**The declarative form is PRIMITIVE; "add" is sugar the service expands into a new desired set.**

- A delta-only protocol **drifts silently**. A missed `add` is invisible forever — nothing re-checks it.
- A desired-state roster **self-heals on every publication**: each device converges to the declared set
  regardless of what it missed.

This is the platform's own argument that announcement beats configuration — *every hand-maintained list in this
fleet produced a silent failure once it went stale.* It also yields the announce half for free: each node
publishes its current roster version/hash, the service sees who has converged, nothing is hand-maintained.

**At this scale the delta path is not even a performance win.** 20 templates x 1536 B = 30 KB. Trivial over
UART; ~4,400 frames over classic CAN at 7 bytes a chunk, about a second of pure bus time at 500 kbit and
realistically several seconds at sane utilisation. Affordable **between rounds**; not something to fire
mid-firefight. A delta saves seconds and costs silent drift. **Full resync is the only sync mode.**

⚠ Devices whose only transport is CAN pay this cost every push. A WiFi/MQTT or UART-to-host path makes roster
distribution cheap. That is a topology consideration, not a protocol one.

### The roster message carries NO template bytes

The corpus is explicit that templates never cross the bus. The roster carries
`{person_id, [slot, ...], character_ref, template_ref}` — small. Templates come from the service **by
reference**, and the driver `DownChar`s them into the assigned slots.

⛔ **What "by reference" MEANS for a template is not settled** — a BusStorage artifact id, something else? The
peripheral spec already records this gap at `[CXC-R503-TEMPLATES-NEVER-CROSS-THE-BUS]`. It must be closed
before this is buildable.

---

## 6. The semantic messages an integrating device emits

David, 2026-08-15: he wants `user_detected: <user_id>` at `location: <location_id>` and
`access_granted: true/false`, *"and the configurability to emit or not emit these messages."*

These are **three different layers**, and the separation is what keeps the sensor reusable:

| Message | Whose | Why |
|---|---|---|
| `user_detected: <person_id>` | **the sensor node's** | the semantic output of a match. Runtime face. |
| `access_granted: true\|false` | **the integrating device's** | a DECISION, downstream of the match, the casting, the RBAC and this device's policy. The R503 knows nothing about roles. |
| `location: <location_id>` | **neither** — device placement | a property, not an event field |

Keeping `access_granted` out of the sensor is what lets one R503 node serve a hatch, a scoreboard and a
"who's aboard" display without any of them growing a special case.

### `location` goes in the NAMESPACE, not the payload

`<location>/user_detected` rather than a `location` field in every message.

- A consumer wanting "who is at the port hatch" subscribes ONE namespace instead of filtering payloads — which
  is what makes selective uplink and CAN acceptance filters work.
- On an 8-byte CAN frame, a location field in every message is budget we do not have.
- **The trade, stated honestly:** relocating a device becomes a config change. That is correct — relocation
  *is* a configuration event and should be visible as one.

### Emit-or-not needs NO new mechanism

The `peripherals:` schema already declares namespaces as a map keyed by role-position. **A message you do not
want emitted is a namespace you do not declare.** Reuse by reference — and it is the same default-deny-outward
shape as the escalation filter.

---

## 7. Enrollment is a ceremony with a real UX cost

`AutoEnroll` takes **six captures per template**. At two templates per person that is **twelve presses** to
enroll one player.

This is what makes the ring LED load-bearing rather than decorative — walking a person through twelve presses
needs unambiguous feedback. It is also why the driver survey's LED finding matters: **four of six reference
drivers cannot name the colours at all, and one would have told us there are three when there are seven.**

⚠ `AutoEnroll` with the status-stream enabled emits **15 intermediate acknowledgement packets**, and **no
reference driver of the six implements it**. The enrollment station's driver is the piece with no prior art.

---

## 8. What is NOT settled here

1. **What "by reference" means for a template** (§5). Blocks buildability.
2. **The `assurance` class rename** — David proposed `not-approved-for-real-world` over `experiential`. A
   fleet-wide vocabulary change; his call. Recorded at `[DD-R503-ASSURANCE-NAME]`.
3. **Per-device latch fail-safe** — belongs to each integrating device's spec, not here and not to the R503.
4. **The spec 27 vs 32 namespace-literal conflict** — recorded at `[DD-R503-NAMESPACE-SPELLING]`; the fix is
   an amendment to spec 27, which neither this design nor the peripheral spec owns.
5. **UN-PARKED 2026-08-15 — see §17. The parking was based on a WRONG objection.** Device-side retention: Six-month auto-delete of an unused person is
   `fingerprint_storage`'s concern **ONLY**. David: *"I didn't mean that the R503s should delete, only that the
   fingerprint_registry should auto-delete, but let's park this for right now."* The architect had widened it
   into a device requirement ("deletion must trigger a convergence cycle") — that was over-reach and is
   withdrawn. What remains genuinely open, and is PARKED rather than answered: whether a device's flash may go
   on holding a template for a person the library has deleted. Recorded so it is findable, NOT so it is
   re-raised; do not reopen without David.
6. **Whether the `fingerprint_storage` service is a new ComposeBus service or an extension of bus_storage.**
   Not yet examined. It is a `[DX-ENGINE-VS-POLICY]` question and should be asked before it is answered.

---

## 9. Nothing here has run

No R503 has been connected to anything. No driver exists. Every timing figure is from a datasheet that
contradicts itself in at least six places, and the one figure this design leans on — `1:N<10ms/Fingerprint` —
is genuinely ambiguous and is recorded as such. See `docs/R503-characterization-2026-08-15.md` §7 and the
peripheral spec's 24 EMPTY evidence slots.

---

## 10. The editor (David, 2026-08-15) — a desktop-class visual editor

**Requirement, his words:** *"there needs to be an actual visual editor that runs on a desktop-class machine
(RPI, MacOS, etc) to create users, enroll their fingers, define RBACs, define characters, and players"*, plus
*"a way to identify all the R503s in use, and state what they're for, and their relationship to the RBACs"*,
plus — *"obviously"* — a **manual delete** option.

### 10.1 ⛔ RATIFIED — the editor is a CLIENT of `fingerprint_storage`, never its owner

**David proactively confirmed this 2026-08-15.** It was the architect's proposal; it is now his ruling, and the
distinction matters — this is the boundary the rest of the editor hangs off.

**Every operation the editor performs MUST exist as a bus operation the editor merely calls**: create person,
enroll, define character, define RBAC, cast a player, register a device, push a roster, delete. If any of them
lives only in the GUI, then:

- a bulk import cannot be scripted,
- a restore-from-backup cannot run headlessly,
- the editor cannot be replaced, and
- a second surface (a phone, a console pane, a CLI) has to reimplement it — the fork generator.

This is `[DX-ENGINE-VS-POLICY]` at the UI seam: the service is the engine and is UI-blind; the editor is ONE
policy surface over it. The test is whether the generic core ever imports anything about the editor. It must
not.

**Promotion candidate:** this rule is not R503-specific — it is the general editor/service boundary. When the
R503 work lands in composeio, consider promoting it to `design-excellence` as a `[DX-*]` node. Recorded here
because the work is deliberately held outside the repo for now.

### 10.2 Manual delete

A manual delete is required alongside (and independent of) the parked auto-retention. It is a
`fingerprint_storage` OPERATION with an editor affordance — never an editor-only capability, per 10.1.

Needs a confirmation step: deleting a person destroys their enrolled templates, and there is no undo unless
the service versions them. ⚠ **Whether delete is soft or hard is NOT settled here.**

### 10.3 ⛔ DECOMPOSE MAXIMALLY, REUSE EXISTING PRIMITIVES (David, 2026-08-15)

*"we should have decomposed individual widgets to the greatest extent possible, and we should reuse existing
primitives where we can."*

This is `[DX-NARROW-WAIST]` applied to the editor: compose from the Canvas / `.compose.yaml` contract, reuse by
reference, **never fork a widget to this app**. Before ANY widget is authored, the existing palette is
surveyed and the honest question is asked — *is this "build X", or is it "extend the existing X"?*

### 10.4 The consequence of a LATER restyle — build for it, or the restyle is a rewrite

*"Once we get a functional UX together, I will redesign it to fit the design aesthetic of the colonial fleet
UX."*

That sequencing is only cheap if the FIRST build keeps **style separable from structure**:

- **Theme constants are canonical.** Widgets read colour/type/spacing from one source and NEVER hardcode a hex
  value. One hardcoded colour is one site the restyle has to hunt.
- **Composites in `.compose.yaml`, not code widgets.** Layout expressed declaratively restyles as data.
- **Structure carries no aesthetic commitment** — no widget assumes a palette, a corner radius, or a chrome
  idiom.

Get this right and the Colonial Fleet pass is a theme edit plus YAML. Get it wrong and it is a rewrite, and the
"functional first, pretty later" plan silently becomes "build it twice".

This is `[PRINCIPLE-PREVIS-IS-A-STYLE-GUIDE]` pointed FORWARD: there, a previs fixes the visual language and
the implementer improves within it. Here the visual language does not exist yet, so the build must leave a
seam where it will later be inserted.

### 10.5 What is NOT settled about the editor

1. **Whether a text-entry primitive exists** in the Canvas contract. An editor requires typing names. If there
   is no caret/selection/focus model, this is a hard gap that changes the whole approach — under survey.
2. **Soft delete or hard delete** (10.2).
3. **Whether the editor and the enrollment station are one process or two.** They are co-located physically
   (enrollment needs the sensor) but should be separable logically, or "enroll a player" means "carry the
   laptop to the sensor". Lean: separable, editor drives a possibly-remote station over the bus. NOT DECIDED.
4. **How a device declares its purpose and its gated permission** — the registry is announce-derived, never
   hand-maintained (`CAN_BRIDGE_REGISTER` is the counter-example already in the corpus), but the announcement's
   shape is unspecified.

### 10.6 The initial IA — tabs, and each tab is an ENTITY (David, 2026-08-15)

*"For the initial build, let's have tabs for the things that are discreet: RBAC creation/definition, character
def'n/RBAC linkage, Sensors (map R503s to capability they connect to, e.g. nuclear permission to fire vs jump
the ship), etc."*

⛔ **His example surfaced a MISSING ENTITY.** "Nuclear permission to fire" and "jump the ship" are
**CAPABILITIES**, and they are the JOIN between two tabs: the RBAC tab maps *role -> capabilities*, the Sensors
tab maps *device -> the capability it gates*. §1's model had RBAC as an attribute of character; it is its own
layer. **The tab list is what exposed the gap** — the IA was load-bearing on the entity model, not cosmetic.

**The match chain is therefore FIVE hops, superseding §1's four:**

```
R503 match  ->  slot        (the sensor's ONLY output)
middleware  ->  person      (slot -> person)
middleware  ->  character   (person -> character, via CASTING)
middleware  ->  role        (character -> role)
middleware  ->  capabilities(role -> the set)
device      ->  decision    device declares "I gate ftl_jump"; is it in the set? -> access_granted
```

**Each tab is an entity** — which is why the set is derivable rather than a matter of taste:

| Tab | Entity | Lifetime | Source |
|---|---|---|---|
| People | person + templates | durable | derived |
| Characters | character -> role linkage | durable | **David** |
| RBAC | role -> capabilities | durable | **David** |
| Sensors | device -> gated capability | durable | **David** |
| Casting | person <-> character | **session-scoped** | derived |
| Rosters | working-set selection, push, convergence | operational | derived |

**⚠ CASTING IS THE ODD ONE OUT and needs a deliberate call.** It is the only session-mutable entity in the
list; every other tab is set-up-once. It may not want to be a peer tab at all — it may be a "start a game"
screen, since re-casting is the operation performed EVERY session while the rest are configured rarely.
Architect's lean: separate it. **NOT DECIDED — David's.**

**The tab set must itself be DECLARATIVE** — a scene listing tabs, not a hardcoded shell, with each tab an
independently-composable view. This serves 10.3 (decompose maximally) and 10.4 (the later Colonial Fleet pass
re-chromes the shell without touching any tab's content). A hardcoded tab shell is a site the restyle has to
rewrite.

### 10.7 GAME MODULE — the second entity the IA exposed (David, 2026-08-15)

*"casting *is* a concept, but yes, I think it's better to have a 'start a game' screen, because different game
modules would have different player character options (not all playable characters are present in a given game
module)."*

**RULING: casting stays in the model; it is NOT a peer tab. It lives on a "start a game" screen.**

⛔ **And the reason introduces a NEW ENTITY: the GAME MODULE.** A module names which characters are playable in
it. So casting is not `(person, character)` — it is **`(module, person, character)`**.

**This is the second time the IA has corrected the domain model** (the first was `capability`, §10.6). That is
worth naming as a method rather than a coincidence: asking "what are the screens" surfaced gaps that staring at
the entity diagram did not. The screens are a different vantage on the same model, and a different vantage is
what finds what a self-consistent model cannot see about itself.

**The split mirrors characters/casting exactly:**

| Surface | Holds | Lifetime |
|---|---|---|
| **Modules** tab | module -> its playable characters | durable, set-up-once |
| **Start a game** screen | module + casting for THIS session | session-scoped |

### 10.8 "Start a game" is the ROSTER PUSH TRIGGER — the working set finally has a moment

§2 and §5 defined the working set and the desired-state push, but named no natural moment for it to happen —
it read as an admin action someone must remember. **It has one: the cast for this module IS the working set.**
Pick the module, cast the players, and the push falls out as a consequence rather than a chore.

**This changes the cost analysis in §5's favour, not against it.** That section assumed rosters were fairly
static and treated a full resync (~30 KB, ~4,400 CAN frames, seconds) as affordable-but-occasional. If the cast
changes per module then **re-pushing at every game start is the NORMAL rhythm**. Consequences:

- The desired-state model gets STRONGER: every session converges by construction, and a device that missed a
  push self-heals at the next game start instead of staying silently wrong indefinitely.
- The measured cost is the MAIN path, not an edge case — and it is still comfortably between-rounds work, not
  mid-firefight work.
- A delta protocol would have been actively worse here: more pushes means more chances to miss one.

### 10.9 CLOSED — RBAC is NOT module-scoped. THE DEVICE IS THE SCOPE. (David, 2026-08-15)

If the XO may jump the ship in one module and not another, roles live at `(module, character)` and it is a
MATRIX. If a character's role is stable and the module only controls WHICH CHARACTERS ARE PRESENT, it stays a
simple link and module scoping comes for free.

**Architect's lean: the second** — it is simpler, and David's framing ("not all playable characters are present
in a given game module") points at character AVAILABILITY rather than at role variation. **NOT DECIDED.**
Cheap to settle now, expensive to retrofit: the matrix form touches the RBAC tab, the Characters tab and the
match chain's hop 5.


### 10.10 The ruling that closed 10.9, and the principle inside it

**David, 2026-08-15:** *"The superset of RBACs may not be present in some game modules, but there wouldn't be
alternate definitions of the XO's RBAC tied to a specific game module. To illustrate, the XO may always have
the ability to authorize the use of nuclear weapons on the Galactica, but if a game module has him planetside,
then they wouldn't be near the device that would allow them to issue that authorization."*

**RULING: roles are STABLE and GLOBAL. There is no module dimension on RBAC.** The architect's lean was right;
the REASONING is better than the lean's was, and it is the part worth keeping:

⛔ **THE DEVICE IS THE SCOPE.** Module-scoped permissions are unnecessary because **physical reachability
already does that work.** The XO always holds nuclear authorization; a module that places him planetside simply
does not place him near the device that exercises it. So the model stays flat and gains a third axis that costs
nothing to implement:

| Axis | Determined by | Where it lives |
|---|---|---|
| WHAT a character may do | role -> capabilities | RBAC tab, stable and global |
| WHO is playing | module -> characters, casting | Modules tab + start-a-game |
| WHERE it can be exercised | **physical device placement** | Sensors tab (device -> gated capability) |

**This vindicates `[10.4 / §4] enroll everyone everywhere, differentiate in RBAC`** — with physical placement
doing the scoping, there is no remaining case for differentiating the ROSTER per device, and the uniform index
survives intact.

**⚠ THE ASSUMPTION THIS RESTS ON, named so it is not an accident:** that sets are PHYSICALLY SEPARATED. If all
players sit in one room, "planetside" is fiction the hardware cannot enforce — the person playing a planetside
XO is still standing three feet from the Galactica device, and nothing in this design stops them. Architect's
lean: **the fiction carries it and we build nothing** (it is a toy, and the ceremony is the point). The only
alternative is a module suppressing capabilities, which is exactly the module-scoped RBAC just ruled out.
NOT RAISED AS A PROBLEM — recorded so a successor does not "discover" it and re-open a settled call.

---

## 11. Rulings of 2026-08-15 (second batch)

### 11.1 CLOSED — `fingerprint_storage` is a NEW service. The reason is a KILL SWITCH.

**David:** *"fingerprint_storage should be new because it is frankly semi-risky to use the R503 even as a toy —
and even more so to sling fingerprint templates across a network — so I want a single point to kill network
enablement of R503."*

Closes `[DD-R503FI-FINGERPRINT-STORAGE-NEW-OR-EXTENSION]`. ⛔ **The rationale is BLAST-RADIUS CONTAINMENT, not
engine-vs-policy tidiness**, and that is the part a successor must keep: an extension of `bus_storage` cannot be
killed without killing generic storage with it. A separate service is **one process to stop**, and stopping it
stops every path a template can travel.

**Consequence for `template_ref` (11.2): the reference form must be resolvable ONLY by this service.** A generic
`doc://` id would let any storage client fetch a template and would silently defeat the kill switch.

### 11.2 RECOMMENDED — `template_ref` is a service-private locator PLUS a content hash

`[DD-R503FI-BY-REFERENCE-MEANS-WHAT]` — David asked for detail rather than a verdict; this is the architect's
recommendation, NOT yet his ruling.

Two small fields in the roster, no template bytes:

- `template_ref: fp://<person_id>/<ordinal>` — a **service-private locator**. Nothing else in the platform can
  turn it into bytes, so 11.1's kill switch is STRUCTURAL rather than remembered.
- `template_sha256` — the **verifier**, and the reason this decision also settles 11.3.

Rejected: a generic `doc://` artifact id (reuses existing machinery, but defeats 11.1).

### 11.3 RECOMMENDED — verification is notepad continuously, UpChar-and-hash periodically

`[DD-R503FI-IDENTITY-VERIFICATION-METHOD]`. **These two questions are ONE**, which is why both were stuck.

`ReadIndexTable` returns one bit per slot: OCCUPIED, never BY WHOM (see the §4 erratum).

| Method | Proves | Cost | Class |
|---|---|---|---|
| Trust the write | nothing | free | — |
| **Notepad roster stamp** | the device BELIEVES it holds roster vN; catches missed / partial / stale pushes | ~32 B | testimony |
| **UpChar + hash** | slot N genuinely holds template X | 1536 B/slot | **evidence** |
| Challenge-match | slot N matches a known finger | comparable | evidence |

⛔ **If `template_sha256` rides in the roster (11.2), the expensive audit becomes nearly free to IMPLEMENT** —
read the slot back, hash it, compare against the number already pushed. **The reference and the verifier are the
same object.** Settle 11.2 and 11.3 answers itself.

**Two honest costs, not to be hidden:** (1) `UpChar` returns a MULTI-PACKET data burst, and no surveyed
reference driver handles data packets safely — Adafruit cannot read templates at all. The audit path needs
driver work the match path does not. (2) Whether `UpChar` returns BYTE-STABLE output for a stored template is a
BENCH question, not a documented one. It is an evidence slot, never an assumption.

### 11.4 Physical boundaries are REAL — 10.10's assumption is CONFIRMED, not merely tolerated

**David:** *"in reality there are different rooms/compartments that make up the planned physical layout, so
there are physical boundaries."*

§10.10 recorded "sets are physically separated" as an assumption the device-is-the-scope model rests on, with
the architect leaning that fiction would have to carry it. **It does not have to.** The boundaries are real, so
physical reachability genuinely enforces capability scoping and the flat RBAC model is sound rather than merely
convenient.

### 11.5 NEW — ComposeBus across TWO SITES over VPN, and where templates must live

**David:** *"I am contemplating having the composebus for two physical sites being connected by VPN to allow
for remote player…"*

⛔ **This makes 11.1's concern the DEFAULT behaviour of a naive roster push.** A single `fingerprint_storage`
serving both sites means every template crosses the WAN — exactly what the kill switch exists to prevent.

**Architect's recommendation: REPLICATE `fingerprint_storage` PER SITE. Never stream templates across the VPN.**
The VPN carries roster INTENTIONS — `{person, slot, ref, hash}`, small and semantic — and each site's LOCAL
service serves the bytes locally. Templates become `scope: local` BY CONSTRUCTION rather than by policy, and the
kill switch becomes per-site for free.

⚠ **This lands on the one scope value the platform has never specified.** `[CXC-VISION-SCOPE-MEMBRANE]` records
`local`/`global`/`auto` as defined and says of the third: *"still-unspecified: `remote`. Whether a relay may
ORIGINATE or forward it is unwritten."* A two-site VPN bus is the first real use case for `remote` — and
specifying it is a PLATFORM decision, not an R503 one. **Do not settle it inside this project.**

### 11.6 Focus/Tab must be a SCENE-LEVEL capability, default OFF

**David:** *"I like the idea of FocusManager, but be aware that often I will want to have it off for my own uses
(e.g. the BSG/colonial UX is not driven by tabs)."*

Adding `accepts_focus = True` to buttons/checkboxes/radios must therefore NOT make tab navigation globally
active. It is declared **per scene** — ON for the editor, OFF for the CIC and every game surface. The engine
already has a per-widget `captures_tab` opt-out (the terminal); this is the same idea one level up.

⛔ **Default OFF.** Game surfaces vastly outnumber data-entry surfaces here, and a focus ring appearing on a
Colonial console is a visual regression on shipped art.

### 11.7 CONFIRMED ON GLASS — text entry works; the VIRTUAL keyboard does not

**David, having run it:** *"I can type into the widget just fine from my physical keyboard, but each keypress
from the virtual keyboard overwrites the text in the field."*

**Both halves confirm the survey.** `TextInputWidget` works on real glass with a physical keyboard — the gate
question is now a GLASS result, not a code read. And the on-screen `keyboard` composite failing exactly as
predicted: a key publish REPLACES `target_ns` (last-key-wins) because the DSL has no string concatenation and
there is no read-modify-write publish site. It is documented as a KNOWN LIMITATION in the composite's own header.

**Irrelevant to a desktop editor** (real keyboard). ⚠ **It would block a touch-only RPi kiosk**, so it is
recorded rather than left to be rediscovered by whoever tries that.

### 11.8 Also ratified

- **`record_table`** — the one generic schema-parameterized collection widget. Agreed. Not per-entity widgets.
- **Colonial rework in Inkscape** — confirmed by David as *"massive time saver"*.
- **Introspection-driven property panels** (the Visual Composer's `bindpanel.py` pattern) — concurred.

---

## 12. Corrections and ratifications, 2026-08-15 (third batch)

### 12.1 ⛔ CORRECTION — the architect over-engineered TWICE; David reversed both

**David:** *"I do want to be very clear that we're not trying to make slinging fingerprint templates over the
network secure. And I honestly don't have a problem with them flowing over ComposeBus, as it's the same risk as
having them flow over any other protocol (and in saying that, I don't want to have a different protocol to
support anyway). And having two deployments of fingerprint_storage per vpn segments is design overkill for now
— something to reconsider in the future after we have support for multi-site gameplay (which we *expect* but
don't have now)."*

**Both §11.5 recommendations are WITHDRAWN:**

1. **Templates MAY flow over ComposeBus.** The architect implied an out-of-band path. David's counter is a cost
   the architect did not weigh: **an out-of-band path is a SECOND PROTOCOL TO MAINTAIN, for no security gain.**
   The security posture is explicit and is not a gap — *we are not trying to make this secure.* The assurance
   class already says so; this states it for the transport too.
2. **Per-site `fingerprint_storage` replication is PARKED.** Overkill until multi-site gameplay exists, which
   is EXPECTED but not present. Revisit then; do not build it now.

**⛔ THE ARCHITECT'S "SURVIVING NUANCE" WAS ALSO WRONG — corrected 2026-08-15 when David asked the obvious
question the architect had not.** The claim was: *"a template is 1536 bytes and a bus message is capped at 256
([DP-CIO-18]), so templates ride ComposeBus CHUNKED."* David asked: **"Are bus messages constrained to 256
bytes even when running on MQTT?"** They are not, and the code says so in as many words
(`python/composeio/transport_mqtt.py:512-513`):

> *"DP-CIO-18's 256-byte guideline is a DESIGN guideline for constrained substrates (CAN frames, a UART line),
> NOT a runtime alarm for MQTT."*

Two separate constants live on that transport: `GUIDELINE_PAYLOAD_BYTES = 256` (checked, logged at INFO) and
`LARGE_PAYLOAD_WARN_BYTES = 256 * 1024` — the real warning threshold. **Kilobyte payloads are ROUTINE on that
path today**; the Intelligence Console's 20 data sources put them there constantly, and `self/deliverables`
measures ~110 KB.

**So the corrected position:**

| Substrate | A 1536-byte template | Ceiling |
|---|---|---|
| **MQTT** | **ONE ordinary message.** No chunking, no indirection needed for transport reasons | warns at 256 KiB |
| **CAN / UART** | chunked, via `config_chunk` | `MAX_PAYLOAD_BYTES = 0xFFFF` — a HARD 65,535 B protocol ceiling, the length field being a uint16 |

**The semantic contract is `fp://<person>/<ordinal>` + `template_sha256` EITHER WAY, and how the bytes actually
move is a TRANSPORT DETAIL** — which is [DP-CIO-6]'s position exactly. The design must not force one transfer
shape onto both substrates.

**Why this correction matters beyond the fact:** the architect stated a 256-byte cap FLATLY, twice, without
reading the transport. It was a remembered design principle applied as a runtime constraint — the same shape as
the `ReadIndexTable` erratum in §4, and the same shape as asserting the 3.3VT trap as fact when it was an
inference. **Three instances in one session of asserting a property without reading the bytes that decide it.**

⚠ **A live hazard the same code comment records, and it applies to US:** `LARGE_PAYLOAD_WARN_BYTES` SHIPPED
ALREADY-VIOLATED — 70,333 bytes on its authoring commit, which became **27,009 runaway warnings in five days,
46% of the console log.** Its own conclusion: *"any absolute value is a clock, not a fix."* A roster push of 20
templates is ~30 KB **if framed as one payload.** Frame it per-template, not per-roster.

**The kill switch (§11.1) is UNAFFECTED**: `fingerprint_storage` remains the only thing that serves those bytes,
whatever wire they travel on.

### 12.2 RATIFIED — `fp://<person>/<ordinal>` + `template_sha256`

**David:** *"Your suggestion for fp://7/1 is excellent, as well as the sha256 of the template."*

Closes `[DD-R503FI-BY-REFERENCE-MEANS-WHAT]` and, with it, `[DD-R503FI-IDENTITY-VERIFICATION-METHOD]` — because
the hash IS the verifier (§11.3). The reference and the verifier are one object, as designed.

⚠ **This PROMOTES a bench question to load-bearing.** Whether `UpChar` returns BYTE-STABLE output for a stored
template was a curiosity while verification was undecided. Now that the sha256 is the verifier, **the entire
audit path depends on it.** It must be answered on hardware before the audit is built, not after.

---

## 13. Implementation readiness — the honest verdict

**The DESIGN is ready. The BUILD is not, for two separate reasons.**

### RATIFIED and not waiting on anyone

service is NEW · `fp://person/ordinal` + `template_sha256` · sensor-side matching · uniform fleet index ·
desired-state roster with full resync at game start · the five-hop match chain · flat RBAC, device-is-the-scope ·
the tab IA · editor-as-client-never-owner · `record_table` · Inkscape restyle · introspection-driven property
panels · scene-level focus defaulting OFF.

### BLOCKER 1 — one decision still gates the software contract

`[DD-R503-NAMESPACE-SPELLING]`. Specs 27 and 32 give DIFFERENT literals for this device (`fingerprint/matched`
vs `fingerprint/match`; 27 has no status surface at all). **A driver cannot publish until it is settled.** The
fix is an amendment to spec 27 — composeio work, owned by neither R503 document.

### BLOCKER 2 — nothing has touched hardware

**36 evidence slots across the two specs, ALL EMPTY.** Building a driver from a datasheet that self-contradicts
in six places, against 21 unanswered bench questions, yields a driver that works on paper.

**One bench hour collapses most of it.** Five questions, in priority order:
1. **Which part is actually in hand** — `ReadProdInfo 0x3C`. Everything in §1 of the characterization is
   conditional on this.
2. **Connector pitch** — calipers. Blocks any harness or footprint.
3. **The `0x55` timing** — and whether it re-fires after `SoftRst`.
4. **Is the AutoIdentify ack stream unbounded** — sizes the RX ring buffer.
5. ⛔ **Is `UpChar` byte-stable** — now load-bearing, see 12.2.

### NOT YET WRITTEN

The driver spec · the service spec · the editor spec (held deliberately while requirements were still moving).

### ⭐ WHAT CAN START NOW — five pieces, zero dependencies

No hardware, no open decision, no R503. All composeio-repo work, all mutually independent, all needed by the
editor AND useful to the platform regardless:

1. **`record_table`** — the generic collection widget (§2 of the survey; THE blocker for every editor list).
2. **Theme unification** — make `style.py` a projection of `console_theme`'s role tokens; hoist `dialog.py`'s
   three literals. ~126 lines. **This is what makes the Inkscape restyle an edit rather than a rewrite**, and it
   is cheapest before an editor's widgets pick a side.
3. **Scene-level focus, default OFF** — `accepts_focus` on buttons/checkboxes/radios plus a Space/Enter handler,
   gated by a per-scene flag (§11.6).
4. **Confirm-dialog composite** — ~25 lines of YAML, once, in a shared library. Delete needs it.
5. **Bindable `options` on `DialogWidget`** — fixes the shipped `dropdown` composite's stated limitation for
   free, and unblocks "cast this person as \<character\>".

**Recommended sequence:** bench hour → namespace amendment → driver → service → editor, with those five running
in parallel throughout.

---

## 14. HAL, mock, de-vendoring, and the LED (David, 2026-08-15 — fourth batch)

### 14.1 RULING — a small R503 HAL + a MOCK; hardware bring-up runs in PARALLEL

**David:** *"I would specifically write a small HAL for the R503 with the command set we need and build with a
mock R503, then we can do R503 hardware bring up in a parallel path."*

**This DISSOLVES §13's Blocker 2.** "Nothing has touched hardware" stops gating the service and the editor: they
build against the mock while bring-up proceeds independently. It is also the maker corpus's existing
`[PAT-HW-ABSTRACTION]` (interface + simulate mode), applied at the right layer.

### 14.2 ⛔ THE MOCK'S SPEC IS THE 21 BENCH QUESTIONS — not "behave like an R503"

**A mock built from the same datasheet reading as the driver AGREES WITH THE DRIVER'S MISUNDERSTANDING.** That
is a test inheriting its author's blind spot and then CERTIFYING it — `[PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-
GREEN]` one layer up, and the single most likely way this build produces a driver that works only on paper.

**So the mock models the TRAPS, and is SWITCHABLE wherever the datasheet is ambiguous:**

| Behaviour | Why |
|---|---|
| `0x55` at power-on **AND after `SoftRst 0x3D`** | the case that desyncs a driver handling it only at boot |
| The AutoIdentify ack stream, **including the UNBOUNDED variant** at `Config2=0` | sizes the RX ring; nothing has ever tested it |
| `0x1F` library-full on overflow | the loud-failure requirement of §3 |
| **Switchable** `1:N` timing — whole-sweep vs per-template | the ambiguity `[CXC-R503-MATCH-TIME-AMBIGUOUS]` records |
| **Switchable** `ReadProdInfo` length — 49 vs 53 bytes | V1.2.1 vs V1.4/V1.4.1 self-contradiction |
| Byte-stable `UpChar` — **and a mode where it is NOT** | ⛔ the verifier depends on this (§12.2) |
| The stray `0x00` from the RX-pin rising edge | field-earned, in esphome's driver, in no manual |

**A mock that only does the happy path certifies a driver that only handles the happy path.** Built this way it
is a FALSIFIER HARNESS, and hardware bring-up becomes a CONFIRMATION rather than a discovery.

### 14.3 RULING — de-vendor the taxonomy; R503 survives ONLY on the sensor side of the HAL

**David:** *"I'd prefer that we take R503 out of the taxonomy everywhere except the R503 side of the HAL; this
would let us swap out a different FP sensor when/if we choose."*

`[DX-NARROW-WAIST]` and `[DP-CIO-6]` applied. **⛔ THE SEAM IS THE TEMPLATE** — an opaque, vendor-specific blob,
produced by a vendor-specific ceremony, held in a vendor-specific store.

| ABOVE the seam — sensor-neutral | BELOW — stays R503, correctly |
|---|---|
| person · character · casting · capability · module | 200 slots · 1-byte ModelID · the index model |
| `fingerprint/cmd/*`, `/status`, `/result` | packet protocol · opcodes · confirmation codes |
| `fp://<person>/<ordinal>` | the 6-captures-per-template ceremony |
| roster intent · desired-state sync · the five-hop chain | `driver: r503` in the `peripherals:` block |

⚠ **`template_sha256` is neutral as a MECHANISM; the BYTES are not portable.** Swapping sensor vendors means
**re-enrolling everyone.** Inherent to biometric templates, not a design flaw — but it must be a STATED property
rather than a discovery.

⛔ **RENAME NOW OR NEVER.** `71-r503-fleet-identity.lts` and its `CXC-R503FI-*` ids are misnamed under this
ruling — that layer is sensor-neutral. Renaming ids breaks citations, but **nothing outside these two files
cites them yet**, so it is FREE TODAY and expensive next week. Identical argument to the claim-shaped-id rename
the spec-writer made and was right about.

### 14.4 The LED ring — define SEMANTIC STATES, never colours

Never discussed until David raised it. The right frame follows directly from 14.3 and from
`[CXC-VISION-SEMANTIC-REALIZATION]`: **one semantic value, many physical realizations.** A different sensor may
have three colours, or a buzzer, or nothing. **The R503 driver maps semantics onto its 7 colours and 6 modes;
nobody upstream knows a colour exists.**

Proposed state set: `idle` · `awaiting_finger` · `capture_accepted` · `capture_retry` ·
`enroll_progress(n of N)` · `match_ok` · `match_unknown` · `busy` · `fault`.

**⚠ The ring is an ACTUATOR WITH TWO WRITERS.** Enrollment feedback comes from the DRIVER mid-transaction
("capture 3 of 6"); outcome feedback comes from the DEVICE ("access denied" is the device's decision, never the
sensor's — §10/§6). Proposed arbitration: **the driver owns the ring during a transaction, the device owns it
otherwise.** NOT DECIDED.

### 14.5 RECOMMENDED — build the HAL on the GENERAL instruction set, NOT `AutoEnroll`

The LED question forces this one, and three independent arguments converge:

1. **`AutoEnroll`'s flowchart MANDATES its own LED behaviour** — yellow 500 ms, green flash 200 ms, red 500 ms,
   6×. The module drives the ring itself, so **we do not get to define the semantics of 14.4.**
2. **No surveyed driver implements `0x31`/`0x32`** (`[CXC-R503FI-ENROLL-STATION-HAS-NO-PRIOR-ART]`), and its
   15-packet intermediate-ack stream is the single most untested behaviour on the part.
3. **The general path gives us the capture COUNT** — `GetImg` → `GenChar` into `CharBuffer[1..6]` → `RegModel` →
   `Store` lets us choose how many captures; `AutoEnroll` fixes it at six.

**Architect's recommendation: the general instruction set.** Prior art in every reference driver, instrumentable,
and the ring means what we say it means.

⛔ **DAVID HAS THE DOMAIN BACKGROUND HERE and the architect does not.** If there is a CAPTURE-QUALITY reason to
prefer `AutoEnroll`'s on-module sequencing — image selection across the six captures, or on-module quality
gating we cannot replicate — that outweighs all three arguments above. **His call, not the architect's.**

### 14.6 ⛔ CORRECTION — the mock is a HAL IMPLEMENTATION, not a fake R503

**David:** *"my intent was that the HAL would define vendor-neutral command sets and then the HAL
implementation for a mock fingerprint sensor would respond via the HAL; the actual R503 HAL implementation
would be against real HW… It's actually *extending* the ComposeBus concepts down to the interface layer above
the FP sensor HW."*

**The architect had described the WRONG THING in 14.1/14.2**, and the difference is architectural, not
terminological:

| | Shape | What it tests |
|---|---|---|
| **Architect's (wrong)** | a fake R503 emitting `0xEF01` frames, checksums, a stray `0x55`; the driver speaks the R503 wire protocol to a pretend R503 | the R503 DRIVER |
| **David's (right)** | the HAL is the vendor-neutral CONTRACT; `MockFingerprintHAL` and `R503HAL` are two PEER IMPLEMENTATIONS of it. The mock never speaks R503 at all | everything ABOVE the HAL, vendor-neutrally |

**David's is the correct swap seam**, and it is what makes 14.3's de-vendoring structurally real rather than
aspirational: the layer above has no R503 concepts to unlearn.

**His framing is the platform's own pattern and should be cited as such:**
`[CXC-VISION-SEMANTIC-REALIZATION]` — one semantic contract, many physical realizations — **pushed one level
BELOW the bus.** `[CXC-VISION-ANNOUNCE-BIND]`'s design check applies directly: *a mechanism built for level N
must be reusable at N+1.* The HAL is the bus's contract shape at the driver layer, which is exactly the
recurrence that makes the spine a spine.

**Consequence: the mock becomes TRIVIAL** — an in-memory dict of slots, ~100 lines, no protocol emulation. The
service, the roster logic and the entire editor build and test against it.

### 14.7 The traps RELOCATE — two fixtures at two layers, and the second is David's call

14.2's trap catalogue does not disappear under 14.6; it moves. `0x55`-after-`SoftRst`, the unbounded ack
stream, both `1:N` readings, non-byte-stable `UpChar` — **all live BELOW the HAL.** Under 14.6 they are
exercised only when real hardware is attached.

| Fixture | Tests | Cost | Status |
|---|---|---|---|
| **`MockFingerprintHAL`** | everything above the HAL | ~100 lines | **BUILD REGARDLESS** |
| **A serial-level fixture under `R503HAL`** | the R503 implementation's protocol handling | larger, R503-only | **OPTIONAL — David's call** |

The trade on the second: **meet the `0x55` desync in a test, or on the bench.** With bring-up running in
parallel anyway (14.1), "on the bench" may simply be cheaper. NOT DECIDED.

### 14.8 ⛔ The HAL MUST expose `capabilities()` — or the swap degrades SILENTLY

Required by 14.6 and NOT by the architect's version, so it would have been missed.

A different sensor may not support template readback — and `[DD-R503FI-IDENTITY-VERIFICATION-METHOD]`'s audit
path **depends on `UpChar`** (§12.2). Without capability announcement the swap degrades silently: the audit
simply stops proving anything and **nothing says so** — an instrument that can only report success, arriving by
the back door of a vendor swap.

**With it, the layer above degrades HONESTLY**: *"this sensor cannot support identity audit"* becomes a
queryable fact rather than a surprise.

Announce-then-bind at the HAL: the implementation ANNOUNCES what it can do; the consumer BINDS to what is
actually there. Candidate fields: slot count · max templates per person · supports-readback · supports-
challenge-match · feedback channels available (colours / none / buzzer) · capture count per template.

---

## 15. Two vocabularies, deliberately divergent (David, 2026-08-15 — the Boomer case)

### 15.1 RATIFIED — the HAL is built on the GENERAL instruction set

David took §14.5's recommendation. `GetImg` → `GenChar` → `RegModel` → `Store`, not `AutoEnroll`. Closes it.

### 15.2 ⛔ The sensor's OUTCOME vocabulary and the ring's PRESENTATION vocabulary MUST NOT SHARE TOKENS

**David:** *"the semantic states issued for the LED ring to pick up may be different than the FP match decision
from the R503; if Boomer from bsg puts her finger on the R503/nuclear permission to fire, her finger might
match, but it'll fail RBAC, and so 'match_unknown' would be emitted. And the semantic message emitted upstream
on the ComposeBus might be the equivalent of 'Boomer tried to hack the nuclear permission to fire system'."*

| Layer | Vocabulary | Answers |
|---|---|---|
| **SENSOR outcome** | `matched(person)` · `no_match` · `error` | what ACTUALLY happened |
| **PRESENTATION** (the ring) | `accept` · `reject_silent` · `reject_known` · `retry` · `busy` | what the user is TOLD |

⛔ **The architect had proposed `match_unknown` for BOTH, and that was a latent defect.** In Boomer's case the
sensor reports `matched(Boomer)` while the ring shows unrecognised — **the same token, opposite meanings, one
layer apart.** A shared token invites an implementer to wire the sensor result straight to the ring because the
names line up, **and the deception then breaks SILENTLY.** Distinct vocabularies make the shortcut
unavailable rather than merely discouraged.

- `reject_silent` — deny while appearing not to recognise. Boomer's case.
- `reject_known` — deny while acknowledging identity. *"Access denied, Lieutenant"* is good drama too.
- **The game chooses the flavour per device.**

### 15.3 ⛔ The HAL MUST NOT indicate outcome autonomously

If an implementation "helpfully" lights green on a successful match, **the deception leaks.** Feedback is always
COMMANDED, never inferred from a match result.

A HAL invariant — and independently another reason `AutoEnroll` is rejected: it MANDATES its own LED sequence,
so the module would decide what the user sees.

### 15.4 ⛔ This is DELIBERATE DECEPTION and is recorded as INTENT

A future reader seeing *"the ring shows unrecognised when the match succeeded"* would file it as a defect and
"fix" it. **It is designed.** The drama is the product — consistent with `assurance: experiential`: this was
never authentication, and a system that cannot be trusted to tell the truth to a user's face is free to be
theatrical about it.

### 15.5 The upstream event is an ATTEMPT RECORD, not a boolean

*"Boomer tried to hack the nuclear permission to fire system"* is `{person, capability, device, outcome}` — the
GM's feed — not `access_granted: false`. §10's three-layer split needs this fourth thing.

⭐ **It also carries person + timestamp, so it doubles as the `last_seen` signal retention needs.** One event,
two jobs — and it dissolves the emit-or-not vs retention conflict raised earlier: the attempt record is
internal plumbing and always emitted; the *public* `user_detected` message stays optional.

---

## 16. Simplification, the `ready` state, and the bring-up probe (2026-08-15)

### 16.1 ⛔ CORRECTION — the "attempt event" over-specified; David's ORIGINAL two messages win

The architect proposed an ATTEMPT EVENT `{person, capability, device, outcome}`. **Withdrawn.** David pointed
back to his earliest design input this session; retrieved VERBATIM from the transcript rather than paraphrased:

> *"I'd want the device controlling an R503 to have the vocabulary to emit `user_detected: <user_id>` at
> `location: <location_id>` and `access_granted: true/false` semantic messages. And the configurability to emit
> or not emit these messages."*

**The corrected model — TWO messages, scoped by location namespace:**

```
<location>/user_detected:  <person_id>
<location>/access_granted: true | false
```

**Why simpler is BETTER here, not merely smaller:**

- `capability` is **DERIVABLE from the device** via the Sensors registry (§10.6). Carrying it in the payload
  DUPLICATES what the registry knows — **and a duplicate is a thing that can DISAGREE with its source.** That is
  the hand-maintained-register antipattern in miniature, the same shape as `CAN_BRIDGE_REGISTER`.
- `location` is **already the namespace scope** (§10.6 put it there deliberately).
- So `nuke_console/user_detected: boomer` + `nuke_console/access_granted: false` **already IS** *"Boomer tried to
  hack the nuclear permission to fire system."* The GM's feed derives the narrative from the REGISTRY, not from
  a fatter wire payload.

**It still does both jobs**: `user_detected` fires even when RBAC denies — she WAS detected — so it remains the
`last_seen` signal retention needs.

**And it resolves the emit-or-not conflict BETTER than the invention did.** The architect had justified the
attempt record partly as always-emitted internal plumbing, dodging the conflict where an optional
`user_detected` lets an active player silently age out. ⭐ **The correct resolution is the SCOPE MEMBRANE, not a
new message class**: the device ALWAYS publishes locally; what is configurable is the **UPLINK**
(`[DX-INTERNAL-BUS-UPLINK]`). Retention reads the local path and is unaffected by an uplink being off.

**Method note:** the architect had this from David seven hours earlier and re-derived a more complex version
anyway. It was recovered by GREPPING THE TRANSCRIPT at David's suggestion — *"can you grep your transaction log
for 'rbac'"*. A design input that is not in a queryable home gets re-derived, worse, by the same instance that
received it.

### 16.2 ADDED — a `ready` presentation state (David)

*"One semantic state for the sensor's LED should be the equivalent of 'ready for authentication' — suggest a
blue or purple color for this vs the obvs green / red for matched/failed."*

Presentation vocabulary becomes: `idle` · **`ready`** · `accept` · `reject_silent` · `reject_known` · `retry` ·
`busy`. `ready` = armed and expecting a finger, distinct from `idle` = nothing in progress.

⛔ **Two constraints, and both are easy to violate by accident:**

1. **The COLOURS are a REALIZATION and belong in the R503 HAL spec, not the identity spec** (§14.3). David's
   blue-or-purple / green / red is the INTENDED DEFAULT MAPPING, recorded on the sensor side. A different sensor
   may have three colours, a buzzer, or nothing.
2. ⛔ **`reject_silent` MUST be visually INDISTINGUISHABLE from a genuine `no_match`** — same colour, same mode,
   same duration. If a silent rejection renders even slightly differently from a real non-match, **an observant
   player learns the tell and §15's deception is worthless.** Falls directly out of §15.2 and does not survive
   being left implicit.

### 16.3 ARCHITECT'S CALL — build a BRING-UP PROBE, not a fake serial R503

**David:** *"only implement anything useful for when we actually bring up an R503 on the bench, and I could see
some implementation work here that will simplify that process, but your call."*

**Call: no serial-level mock. Build bring-up INSTRUMENTATION** — a probe that runs against REAL hardware and
answers the evidence slots:

- `ReadProdInfo 0x3C` → confirm WHICH PART is in hand (everything else is conditional on it)
- Capture `0x55` timing from power-on **and after `SoftRst 0x3D`**
- Dump `ReadIndexTable 0x1F` occupancy
- Time a 1:N `Search` at ONE template and at TWENTY → settles `[CXC-R503-MATCH-TIME-AMBIGUOUS]`
- `UpChar` a slot TWICE and compare hashes → settles byte-stability, on which the §12.2 verifier depends
- `AutoIdentify` with `Config1=1, Config2=0` and a never-matching finger → counts the ack stream

**Rationale:** a fake serial device would have tested the driver against **the architect's GUESSES about
ambiguous datasheet text** — the §14.2 blind-spot trap, one layer down. The probe tests the driver against THE
PART. It converts the bench hour from "poke and take notes" into a **repeatable script whose output FILLS the 36
empty evidence slots.**

**House precedent:** `docs/board-bringup/*.descriptor.yaml` (machine-readable bring-up) and the beep-out sheet
(DERIVED from a netlist, never hand-authored). Bring-up as a runnable, self-proving artifact is already how this
project works.


---

## 17. ⛔ UN-PARKED — six-month retention. The objection that killed it was wrong.

**David, 2026-08-15:** *"my comment that 'fingerprint_storage' should delete fingerprints after 6 months of
disuse was tied specifically to the design I proposed, that we get regular emission of identified users… I knew
it was a closed loop system and would work. But it was a minor point and it was, frankly, easier to drop the
concept (and the testing to verify that it would actually drop at 6 months of no user verification) than to try
to explain why my design was valid!"*

### 17.1 What actually happened

1. David proposed six-month auto-delete on `fingerprint_storage`.
2. **The architect raised a "conflict"**: if `user_detected` is emit-or-not configurable per device, a person who
   only uses a non-emitting device ages out while actively playing.
3. David scoped it to the service and said park it.
4. **The architect later solved the same problem independently** at §16.1 — the device ALWAYS publishes locally;
   only the **UPLINK** is configurable (`[DX-INTERNAL-BUS-UPLINK]`). **That restores exactly the closed loop
   David had described.**

**His design was valid. The objection was wrong. And the architect reinvented the answer that was already
implicit in the original proposal.**

### 17.2 RULING: retention is UN-PARKED and stands as originally designed

Six-month auto-delete of an unused person, on `fingerprint_storage` ONLY. It is a closed loop because
`user_detected` is published on every match — including matches that FAIL RBAC (§15/§16.1), so an active player
cannot silently age out.

⚠ **One real design constraint from David's own words** — *"the testing to verify that it would actually drop at
6 months"*. A wall-clock timer is untestable in practice. **The service must take its clock as an INJECTABLE
DEPENDENCY, never read wall time directly**, so a six-month expiry is a unit test rather than a six-month wait.
Cheap if designed in; a rewrite if not.

Device-side retention (whether a device's flash may keep a template for a person the library deleted) remains
separately PARKED — that half was never the disputed part.

### 17.3 ⛔ THE PATTERN, which matters more than the instance — a COLLABORATION WART

**An objection that is cheap for the architect to raise and expensive for David to refute does not function as
push-back. It functions as a TAX on his ideas** — and it is paid silently, by good designs being dropped rather
than defended. David said so explicitly: *"it was easier to drop the concept than to try to explain why my
design was valid."*

**This inverts `[PRINCIPLE-PUSH-BACK]`.** That principle exists because *"deference where a push-back was
warranted reads as a MISCALIBRATION"* — a disagreement backed by a concrete reason is signal David cannot get
any other way. **The failure here is the mirror image: push-back where it was NOT warranted, which produces the
same loss in the opposite direction and is HARDER TO SEE, because a dropped idea leaves no trace.** A
suppressed objection is at least visible to the one suppressing it; an idea abandoned under objection-cost is
invisible to both parties.

**The tell**: David concedes a point quickly and moves on, on something he had proposed with confidence. That is
not agreement — it is the cost of rebuttal exceeding the value of the item.

**The remedy is not "object less."** It is: before raising a conflict, **check whether the human's design
already resolves it.** In this instance the closed loop was implicit in David's own proposal and the architect
did not look for it before objecting. §16.1's method note is the same failure in a different costume — a design
input received seven hours earlier, re-derived worse, recoverable only by grepping the transcript.

⭐ **PROMOTION CANDIDATE — `insights-and-warts`, kind: wart.** This is not R503-specific. It is a wart on the
ARCHITECT, and `[SBR-SELF-AUDIT]` says the observer cannot audit itself — *"the Architect cannot see the wart on
its own face… that one needs the light from outside too, and David is it."* This is that light, and it should
land in the ledger when the R503 work merges to composeio.

---

## 18. PRE-REGISTERED EXPECTATIONS for the spec audit (written BEFORE the result)

Recorded before the auditing subagent returns, so its output cannot be retro-fitted into confirming the method.
This is the negative-control discipline applied to our own process instrument.

### 18.1 The two independent predictions

- **The architect** withheld its three KNOWN failures from the auditor's brief, deliberately, as a blind set:
  the 10-vs-20 template misread, the over-specified attempt event, and per-site service replication.
- **David**, unprompted and before seeing anything: *"it **should** return you a DSD with good clarification
  questions about your specific known dave-being-vague examples."*

### 18.2 ⛔ THE VALIDATION SET IS WEAKER THAN THE ARCHITECT FIRST CLAIMED

Stated before the result, because stating it after would be worthless.

**All three "known failures" were CORRECTED BY DAVID IN THE TRANSCRIPT.** An auditor reading the whole
conversation sees the error AND the correction. **So finding them proves only that it can READ — not that it
can catch what nobody caught.** The architect framed this as a blind validation set one message earlier; that
framing was too generous to the instrument.

### 18.3 What would actually VALIDATE vs FALSIFY

| Outcome | Reading |
|---|---|
| Finds the three known-and-corrected items | **WEAK confirmation.** It read the transcript. Expected floor, not evidence. |
| Correctly reports resolved items as RESOLVED rather than re-raising them | **Real signal.** Re-asking a settled call is a named failure mode ([SBR-SELF-AUDIT], [STRATEGY-DONT-LEAP-BEFORE-LOOKING]); an auditor that does it generates noise. |
| ⭐ Surfaces a LIVE ambiguity NEITHER party has named | **STRONG validation.** This is the only outcome that proves the mechanism adds something a careful human plus a careful architect did not already have. |
| ⭐ Finds an INVENTED item the architect still does not know about | **STRONG validation.** Three surfaced today only because David caught them; the base rate suggests more exist. |
| A DSD full of things David already said, presented as discoveries | **FALSIFICATION.** An instrument that restates its input is not measuring. |

### 18.4 The known-live one, named now so it cannot be claimed as a discovery

`location` in the NAMESPACE vs the PAYLOAD (§10.6). David never said which; the architect chose, and the
trade-off — relocating a device becomes a config change — was never put to him. **If the DSD raises this, it is
correct but it is not new.** The interesting findings are the ones not on this list.

### 18.5 SECOND pre-registration — David bets on the STRONG criterion

**David, before the result:** *"re: 'Does it surface ambiguities that are still LIVE' — I bet it will."*

That is a bet on §18.3's **strong validation** row, not the weak one. Recorded because a prediction made after
the fact is not a prediction.

**The architect takes the same side, and states its REASONING as its own prediction:** it caught itself
interpreting three times on 2026-08-15, and **all three were caught by David, none by itself.** A base rate of
zero self-catches does not suggest the remaining interpretive gaps were all surfaced.

**Where the architect predicts the live ambiguities are** — named NOW so the auditor's findings are checkable
against the architect's own guesses, and so anything found OUTSIDE this list counts for more:

| # | Suspected live ambiguity | What David actually said |
|---|---|---|
| 1 | The **"etc." tabs** — People, Casting, Rosters were DERIVED and written into the IA as if specified | he named RBAC, Characters, Sensors, "etc." |
| 2 | **Hop 5 of the match chain** — `role` inserted as a layer between character and capabilities | *"this is the XO's RBAC"* reads equally as character→capabilities, no separate role entity |
| 3 | **The LED state set** — eight of nine states invented | he specified exactly one: "ready for authentication" |
| 4 | **`capabilities()` field list** — entirely the architect's | he ratified the CONCEPT, never the contents |
| 5 | **Is the working set exactly the cast**, or the cast plus spares? | never addressed |
| 6 | **Are `fp://person/ordinal` ordinals STABLE across re-enrollment?** | never discussed — and it silently decides whether re-enrolling one finger invalidates a stored `template_sha256` |

⭐ **A finding NOT on this list is the strongest possible outcome** — it means the instrument sees past BOTH
parties, which is the only thing that would justify making it standing practice rather than a one-off.

### 18.6 ⛔ The grading rubric was the wrong frame — David corrects the posture

**David:** *"even if it only catches 50% of real issues, that's 50% more than we would have caught otherwise."*

Correct, and it supersedes §18.3's strong/weak framing as the OPERATING question. §18.3 was asking *"is this
instrument good enough to keep?"* — a one-shot verdict. The right question is *"is it cheap enough to run every
time?"*, and it is: two scripts and one subagent.

⭐ **A 50% instrument run FIVE TIMES beats a 90% instrument run once.** So the posture is **make it ROUTINE, not
PRECISE** — a standing step after every spec authoring, not a thing to tune. Effort spent perfecting the
auditor is effort not spent running it again.

**⛔ THE ONE GUARD, and it is the only way a partial instrument becomes HARMFUL:**

**Its output must never read as COVERAGE.** Four findings means four findings — it says NOTHING about the other
108 nodes. If a clean-looking DSD is read as *"the spec was audited and is fine"*, we have built the exact
failure this corpus names repeatedly: the report that LOOKS complete, so the reader acts blind while believing
they are oriented ([SBR-TRUST]: *"a boot read that silently returns partial strategic state is the worst
outcome"*; [LEC-CORPUS-BUILDS-NONEMPTY]: an empty result must be a loud fault, never a silent clean bill).

**Requirement on the artifact, cheap to satisfy:** every audit DSD states what it EXAMINED and what it DID NOT,
and never implies the unflagged remainder was verified. With that guard, 50% is pure gain. Without it, 50%
coverage read as 100% is worse than no audit at all.

### 19.1 D3 sharpened — `location` is NULLABLE, and that is what settles the address question

**David, 2026-08-15:** *"location may be null - it's not intended to be a set of GPS coordinates. It could be
'CIC' or 'Raptor_Two', but it may simply be unset, and that should be legal."*

⛔ **A NULLABLE FIELD CANNOT BE AN ADDRESS.** There is no `<location>/user_detected` topic for a device that has
no location. So the portable tricorder is not an awkward edge case in the namespace design — **it is the
counterexample that makes DEVICE IDENTITY the only viable address.**

| Field | Role | Required? |
|---|---|---|
| `device_id` | the ADDRESS — `<device_id>/user_detected`, `<device_id>/access_granted` | **yes**, always |
| `location` | INFORMATIONAL only — `"CIC"`, `"Raptor_Two"`, or nothing. Not coordinates. | **no** — absent is LEGAL |

**Display consequence:** a consumer rendering *"Boomer rejected at CIC"* must degrade to *"Boomer rejected at
`nuke_console`"* when location is unset. **The device id is always the fallback**, which is a second independent
reason it is the primary key rather than a co-equal one.

**⚠ AN INTERPRETATION, RECORDED AS ONE** per [DX-INTERPRETATION-IS-AN-INFERENCE]: the architect is treating
"unset" as **simply ABSENT — no null-vs-absent distinction.** The alternative reading is that `location: null`
("this device deliberately has no fixed location" — a tricorder) is a different fact from an absent key
("nobody labelled it yet"), which a registry view could care about. **David did not ask for that distinction**,
and inventing it is the over-specification pattern already corrected twice today (the attempt event, per-site
replication). Default: absent means absent. **Reopen only if David says so.**

### 19.2 D3, final — the event carries DEVICE; location is an ATTRIBUTE OF THE DEVICE, never of the event

**David, 2026-08-15:** *"'Boomer rejected at nuke_console' should be the primary emitted message, with
'nuke_console is in CIC' as an optional attribute to nuke_console. Location should be secondary, not primary."*

⛔ **This is stronger than §19.1.** There the architect had location as an informational property *adjacent to*
the message. David's precision: **`location` is not in the EVENT'S VOCABULARY AT ALL.**

| Layer | Carries | Required |
|---|---|---|
| **The EVENT** | `<device_id>/user_detected: <person>` · `<device_id>/access_granted: bool` | yes |
| **The DEVICE REGISTRY** | `nuke_console.location = "CIC"` \| `"Raptor_Two"` \| absent | **no** |

A consumer that wants *"at CIC"* **JOINS** the event to the registry. Nothing on the wire ever carries location,
so a portable device (a tricorder) changes ONE registry attribute and no message shape, no namespace, and no
subscription is affected.

**Three earlier findings collapse into this one:**

1. §19.1 — a NULLABLE field cannot be an address.
2. **The auditor's C1** — the device/Sensors registry is a missing entity the whole message design already
   depended on. David's answer makes the registry the *home* of location, so C1 is no longer a gap to fill
   later; **it is a prerequisite.**
3. §16.1 — `capability` was removed from the payload because it is DERIVABLE from the device via the registry.
   **`location` is the identical move on the identical join.** The message carries the KEY; the registry carries
   the ATTRIBUTES. One rule, two fields, and the second was found by David rather than derived from the first.

⛔ **The generalisable rule, and it is the reason this was worth three rounds:** an EVENT carries the minimum
key needed to identify its source. Everything ELSE about that source is an attribute of the source, resolved by
join. Putting a source's attributes in its events duplicates the registry onto the wire, and a duplicate is a
thing that can disagree with its source.
