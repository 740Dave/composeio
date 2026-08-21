<!-- dsd-status: answered 2026-08-14T08:59:47Z -->
# Decisions — CAN wire format, message freshness, and the ST7796 landing

> Consolidates every open call accumulated 2026-08-13/14 across: the CAN specialist's adversarial teardown,
> the FactLattice gap analysis against `[SPEC:CXC-SUBFILTER]` / `[CXC-NSID-ALLOCATE]` / `[CXC-VISION-*]`,
> tonight's git-tree audit, and `docs/PLAN-fleet-baseline-and-bus-native.md` (workstreams E, F, G, J).
> Each decision carries **What this is** (where the surface is deep or seldom-touched) → **Why it matters** →
> **Options** → **My recommendation** → a fillable **Your call** slot.
> Answer by filling the slots in place, or reply on the channel. "Recommendations all accepted" is a valid answer.
>
> **Checked against the committed decision log before writing** — none of these are already settled.

## How to use this

The recommendations are my honest engineering lean, not a nudge — overrule freely and I'll encode your call, not
mine. **⛔ marks a decision that blocks downstream work**; those are worth doing first. Everything else can be
decided in any order, or not at all tonight.

Two of these (**D4**, **D6**) reverse or complicate something already said out loud, so they get extra context
rather than a bare fork. **D4 in particular contradicts a call you already made** — that's flagged, not buried.

---

## Theme A — The arbitration ID. Freeze this before anything else.

> **What an arbitration ID is, in one line:** every CAN message's 29-bit "address." It does three jobs at once —
> it says *what the message is*, it decides *who wins when two nodes talk at once* (lower number wins), and it is
> **the only thing a receiver's hardware filter can look at**. Change its layout and you must reflash the whole
> fleet, which is why it goes first.

### D1 ⛔ · Do we stop hashing namespace names into the wire ID and start *allocating* them?

**What this is.** Today a namespace name like `throttle/left` is run through a checksum (CRC-16) to produce its
16-bit wire ID. A checksum deliberately scrambles — two names that look alike land in unrelated places.

**Why it matters.** Three separate consequences, and the first is a correctness bug, not a performance one:
- **Collisions.** Exact birthday math on the live bus: **2.32% at today's 56 namespaces, 7.28% at 100, 39.27% at 256.**
  There is no checksum on the ID, so a collision is **a receiver confidently decoding the wrong namespace** — a
  corrupted control surface, not a missing message.
- **A ratified spec is currently unimplementable.** `[CXC-NSID-LOWERING]`'s prefix→filter table requires that
  `bsg/dradis/contact` and `bsg/dradis/bearing` share high bits. A CRC-16 is *designed* to destroy that. So the
  spec you already ratified cannot be built against a hash.
- **Hardware filtering dies.** No mask can select a scattered set, so every CAN node today accepts every frame.

| Option | Tradeoffs |
|---|---|
| **Allocate hierarchically at build time** (`[4b root][4b group][8b leaf]`, already ratified in `[CXC-NSID-ALLOCATE]`) | **Collisions impossible by construction; prefix filtering becomes buildable; no registrar needed — same as J1939's PGNs. Cost: a namespace gets an ID in a table, and adding one is a build step.** |
| Keep hashing, add collision *detection* | No allocation discipline, but 39% at 256 is not a curve you detect your way out of, and it buys **zero** filtering headroom |
| Runtime registrar assigns IDs per segment (`[CXC-VISION-COMPACT-WIRE]`'s stated endpoint) | Collision-free too, **but** it has no obligation to preserve prefix→bit-prefix correlation, so it would silently break the filtering the allocation table exists to enable. It also makes a segment stateful |

**My recommendation:** **allocate, hierarchically, at build time** — the option you already ratified. Also worth
recording explicitly that this *supersedes* `[CXC-VISION-COMPACT-WIRE]`'s runtime-registrar endpoint, because
right now the corpus holds both and they are incompatible.

**Your call:** Allocate hierarchically at build time ([4b root][4b group][8b leaf], already ratified in [CXC-NSID-ALLOCATE])

---

### D2 · `[BP-CAN-ID]` v2 broke a filtering property. Accept the loss, or move the field back?

**What this is.** The ESP32's CAN chip has a "dual filter" mode that can accept **two** exact patterns — but it
only ever compares **bits 28–13** of the ID. Nothing else. That's a hardware fact, measured on your own C3.

**Why it matters.** v2 (ratified 2026-08-13, for good reasons — `shields` was losing arbitration to console
bounding boxes because priority was a pseudo-random function of the *name*) moved `ns_id` from bits 28–13 down to
**21–6**. So `[CXC-NSID-CAN-WINDOW]`'s claim — *"the two windows coincide exactly… TWO exact accept patterns at
ANY granularity"* — **is now false.** Dual mode reaches `root + group + one bit of leaf`. Nobody redid this
arithmetic when v2 landed; two independent agents found it tonight.

| Option | Tradeoffs |
|---|---|
| **Accept the loss; correct the spec** | Priority-at-top is worth more than dual-mode exact matching, and single-filter mode still gives one exact 29-bit match. **Cost: a leaf needing two exact namespaces in silicon can't have it on an ESP32 — it filters the second in software** |
| Move `ns_id` back up to 28–13 | Restores dual-mode exact matching — but displaces `prio`, undoing the arbitration fix that was the whole point of v2 |

**My recommendation:** **accept the loss and correct `[CXC-NSID-CAN-WINDOW]`.** The spec node is the
justification for the entire allocation scheme, so it must not assert a hardware property the layout no longer
has. This is cheap now and expensive after someone builds on it.

**Your call:** Accept the loss; correct the spec

---

### D3 · `src = 0` (unassigned) — make allocation mandatory, or refuse to transmit?

**What this is.** `src` is a 4-bit "which node sent this" field. Every implementation currently defaults it to
**0 = unassigned**, with a comment reasoning correctly about *identity* — two unassigned nodes should be visibly
indistinguishable rather than accidentally distinct.

**Why it matters.** The reasoning is right about identity and silent about **arbitration**. Two unassigned nodes
publishing the same namespace emit **byte-identical 29-bit IDs with different data**. Both win arbitration, both
keep transmitting, and the first differing data bit is a bus error → error frames → automatic retransmit → a
self-sustaining storm **that looks exactly like a wiring fault.** It cannot happen today only because the C3's
transceiver is dead. **It becomes live the day you fix the transceiver — which is also the day you flash new
firmware, so you'd have two suspects and one symptom.**

| Option | Tradeoffs |
|---|---|
| **`src == 0` implies listen-only** | Fails safe and needs no coordination; a node with no identity can hear but not speak. Cost: one more reason a node is silent, so it must log loudly |
| Hard precondition — refuse to start without an allocated `src` | Strictest, catches it at boot. Cost: a node that loses its NVS becomes a brick until re-provisioned |
| Leave it | Free today, and hands you a bus-wide error storm on transceiver-swap day |

**My recommendation:** **`src == 0` implies listen-only**, with an ERROR-level log line naming the reason. It's
the version that cannot brick a board, and listen-only is already a state the fleet understands.

**Your call:** src == 0 implies listen-only

---

## Theme B — Freshness and `unknown`. Blocks the mothercard.

### D4 ⛔ · How does a receiver know a value is still true? *(This reverses a call you already made.)*

**⚠ Flagged honestly:** you said *"timestamps on messages, agree 100%"* and I agreed. **The CAN teardown says we
were both wrong**, on three independent grounds. Presenting the reversal rather than quietly implementing it.

**What this is.** Right now, if a publisher dies, its last value sits in every receiver forever, looking current.
Nothing anywhere can tell "still true" from "publisher died 40 minutes ago."

**Why it matters.** It's the precondition for the mothercard: a derived value like `jump/permitted` built from a
stale conjunction is a confident lie, produced by the one node everything else trusts without checking.

**Why per-message timestamps fail:**
- **They don't fit.** A tagged value is 5 bytes (`[0xFE][int32]`); a meaningful millisecond timestamp is 4.
  **9 > 8.** Squeezing to 3 bytes wraps every 4.66 hours, so the receiver needs a local clock anyway — the
  timestamp buys nothing. Dropping the type tag to make room reopens a closed defect.
- **There is no shared time base.** `_bus/time` is `int(time.time())` — one-second resolution, one-way, no delay
  compensation — **and it has no consumer anywhere in the tree.** Against CH32V003s on internal oscillators
  (±1%, ~36 s/hour drift), the clock-offset error *exceeds the staleness being measured*. It would report fresh
  values as expired **consistently**, which is what lets a bad instrument survive testing.
- **The prior art is unanimous.** CANopen, J1939 and NMEA 2000 all solve this with a declared rate at the
  publisher plus a receiver-side timeout on its own clock. None of them timestamps.

| Option | Tradeoffs |
|---|---|
| **Receiver-side age + declared refresh interval + 3× timeout + per-node heartbeat** | Zero wire bytes; no shared clock needed; matches CANopen/J1939 exactly. **Cost: two mechanisms, not one — a per-namespace timeout can't tell "publisher dead" from "event-driven with nothing to say," so the heartbeat is not optional** |
| Per-message timestamps | Doesn't fit in 8 bytes, and is actively misleading without clock sync |
| Do nothing (status quo) | Every stale value asserts itself forever; the mothercard cannot be built honestly |

**My recommendation:** **receiver-side age against a monotonic clock; each namespace declares an expected refresh
interval; timeout = 3 × interval, floored at interval + 100 ms; plus a per-node heartbeat.** Three details worth
ratifying with it: expire **lazily on read** (a timer-driven sweep means a stalled receiver demotes the whole
world and blames the world); the 3× is a **J1939 convention**, so say *convention* in the spec, not *required*;
and **mark `_bus/time` NOT A TIME SOURCE today**, before something depends on it.

**Your call:** Receiver-side age + declared refresh interval + 3× timeout + per-node heartbeat
**Comments:** This should be configurable in YAML, ideally specifying the refresh interval, max age, timeout, etc, or if thats too difficult then a simple grouping of semantic messages that must all have a specific value wihtin the bounding window defined as a class.

---

### D5 · How is `unknown` represented on the wire?

**What this is.** A value that is *unknown* — never received, or expired — must be distinguishable from `false`
or `0`. The current codec has no way to say it.

**Why it matters.** Pairs with D4. And it carries the rule I'd protect above everything else in this document:
**a derived value's age is the MAX of its inputs' ages, and it is `unknown` if ANY input is `unknown`.**

| Option | Tradeoffs |
|---|---|
| **A new type tag `0xFD`, zero-length payload** | Unambiguous for every value type at once; 52 code points are free; costs one byte only when the value *is* unknown |
| A reserved sentinel value (the J1939 approach) | J1939 reserves the top ~2% of every parameter's range — **but that is only safe inside its per-SPN range contract**, which our dynamically-typed values cannot have. A reserved float sentinel *is* NaN, which derived values legitimately produce |

**My recommendation:** **tag `0xFD`**, shipped in the same flag day as the `[0xFE]`/`[0xFF]` type tags — the
coordinated reflash is already priced in, so this rides for free. Add it to the **UART codec in the same change**;
two mechanisms on two substrates is a fork this codebase has already paid for twice.

**Your call:** A new type tag 0xFD, zero-length payload

---

## Theme C — Topology and traffic

### D6 · Is locality enforced by a `scope` bit in the ID, or by two physically separate CAN segments?

**⚠ This one wants a discussion more than a slot** — the honest answer may be "both, at different layers," and
the ruling you already gave (a mothercard is *not* a bus-relay) reshapes it.

**What this is.** The mothercard sits on the shared ComposeBus *and* runs an internal trunk to its daughtercards.
The question is what keeps internal chatter off the external bus.

**Why it matters.** A `scope` bit only saves the *receiver's CPU*. A separate segment saves **bandwidth, error
confinement, and bitrate** — a scope-filtered frame still occupies the external wire for its full 320 µs.

| Option | Tradeoffs |
|---|---|
| **Two segments, mothercard as gateway; keep the 2 scope bits but demote them to policy/diagnostics** | Internal traffic is *absent* from the external bus, not merely ignored; the internal trunk can run faster; **a stuck-dominant daughtercard takes down the trunk, not the vehicle**; and each segment gets its own bit timing. Cost: the mothercard is now two controllers and must publish its own upstream link state |
| Scope-in-the-ID as the locality mechanism | Already shipped into three implementations — **but on an ESP32 in dual-filter mode, scope sits at bits 1–0, outside the compared window, and is not filterable at all.** It's filterable exactly where you have filter budget to spare |

**My recommendation:** **two segments with the mothercard as gateway.** Keep the scope bits (removing them is
another flag day and they're already spoken for) but stop anything load-bearing depending on scope being
filterable in silicon. One correctness note to write down either way: with `src` above `scope` in the ID, **node 1
systematically beats node 15** for the same namespace — fine, and exactly what J1939 does, but state it or it
becomes a mystery in three years.

**Your call:** Two segments, mothercard as gateway; keep the 2 scope bits but demote them to policy/diagnostics

---

### D7 · The 977-frame config transfer — add flow control, or move bulk off CAN?

**What this is.** Pushing a 5,856-byte scene over CAN takes 977 frames, because each frame carries 6 usable bytes.
It has never actually run.

**Why it matters.** 30% wire efficiency, ~313 ms of solid bus time worst case, whole-transfer retry with no flow
control (`Reassembler.missing()` exists and **nothing calls it**). At 1% per-frame loss, ~0.005% of transfers
complete — so it retries forever and presents to you as *"the network is flaky."*

| Option | Tradeoffs |
|---|---|
| **Keep bulk off CAN — send scenes over UART, where it already works in 25 frames** | Zero new mechanism; CAN stays a control bus. Cost: a daughtercard with only CAN can't be configured over it |
| Add ISO-TP-style flow control (`BS`/`STmin`) | Caps bulk at ~25% duty cycle by construction; a loss costs 16 frames instead of 977. Cost: real protocol work, and the transfer gets ~4× slower (313 ms → ~1.3 s) |
| Leave it | Congestion collapse the first time it runs on a loaded bus |

**My recommendation:** **keep bulk off CAN for now** — it crosses UART today in 25 frames and works. Revisit
flow control when a CAN-only daughtercard actually needs a scene. (J1939 agrees by precedent: its bulk transport
tops out at 1,785 bytes, and your scene is 3× that entire limit.)

**Your call:** Neither
**Comments:** I can't answer what the right technical implementation method is, but any device on ComposeBus must be able to get their config details via the ComposeBus, even if the substrate is CANBUS.  There were extensive discusssions on this point previously, as there were design issues around loss of a frame, recovery of a single frame vs repeat the entire file, etc.  Suggest you dispatch a subagent to look at your transcript and recover this discussion from a prior session.

---

## Theme D — The Pi, and one number that needs a deploy

### D8 · `restart-ms` on the Pi's CAN interfaces — 100 (auto) or 0 (manual)?

**What this is.** When a CAN controller sees too many errors it shuts itself off ("bus-off"). `restart-ms 100`
means "wait 100 ms and switch back on, automatically."

**Why it matters.** On a *real* fault it becomes: bus-off → 100 ms → reset → bus-off, **~10 Hz forever**, while
the interface reads healthy almost every time you sample it. It hides the fault by making it intermittent — the
exact instrument failure this fleet keeps producing. **It has never fired.**

| Option | Tradeoffs |
|---|---|
| **`restart-ms 0` + counted, logged manual recovery** | A fault stays visible and attributable. Cost: a transient glitch now needs an operator or a supervisor |
| Keep 100, but export the bus-off **count** and alarm on its **rate** | Keeps auto-recovery; makes the hiding visible. Cost: you must build the counter, or you have kept only the hiding |

**My recommendation:** **`restart-ms 0` on the gateway**, auto-restart elsewhere if you want it. Either way,
**rehearse it before you ship** — one node alone on a terminated segment with no peer to ACK will drive its error
counter up on demand, and that costs nothing to try.

**Your call:** restart-ms 0 + counted, logged manual recovery

---

### D9 · The Pi's SJW — leave at the driver minimum, or widen it?

**What this is.** SJW is how much a node may nudge its own clock each bit to stay in step with the bus. Yours is
at **1 of 16 quanta = 6.25%, the driver's minimum**; the ESP32s sit at 12.5–15% with a different sample point.
A 7.5–12.5 point spread, and **the Pi is the weak node.**

**Why it matters.** It's fine on a short bench cable and degrades exactly where you can't reproduce it — long
runs, temperature, a noisy device.

| Option | Tradeoffs |
|---|---|
| **Widen the Pi's SJW (~4× resync window) via one argument in `can0.service`** | Costs one line and a service restart; buys margin at the weakest node. Cost: it's a **deploy** change to `pi.example.local`, which also runs the carousel kiosk |
| Leave it | Free; keeps the fleet's least tolerant node least tolerant |

**My recommendation:** **widen it**, bundled with the next planned `rpi5` touch rather than as its own
interruption — the kiosk means no casual reboots.

**Your call:** Widen the Pi's SJW (~4× resync window) via one argument in can0.service

---

## Theme E — Landing and corpus

### D10 · Land the ESP32-C3 + ST7796 board profile bundle?

**Why it matters.** **The panel running on your desk right now exists in no commit and on no branch.** Six files
live only on this laptop: the board profile (`cyd/src/boards/esp32c3_st7796.cpp`), the panel constants header
(the pin map you paid for with a soldering iron — SCK=GPIO4, MOSI=7, CS=10, DC=3, RST=1, LED=0, VCC=5V), the
partition table, and three sdkconfigs. Sixteen modified `cyd/` files go with it (the render side plus four
transport files).

Everything else I flagged earlier is **already safe** — the announcement firmware, build-stamp tooling and
`can_bringup_main.cpp` are all on main already.

| Option | Tradeoffs |
|---|---|
| **Land as one bundle, gated on a build of the affected envs** | It's a `BoardProfile` + build env, the architecturally additive shape — nothing above the seam changes. **Cost: someone must actually run the build first, because firmware is exactly where "compiles clean" has fooled us twice tonight** |
| Land it unbuilt | Fast; and a broken env on main is worse than an unlanded one |
| Leave it uncommitted | Free until the laptop isn't |

**My recommendation:** **land the bundle, build-first**, via git-expert on a branch off main in a worktree. If
the build fails, that's information you want before it's on main.

**Your call:** Land as one bundle, gated on a build of the affected envs

---

### D11 ⛔ · Migrate `spec/01-composeio-bus-protocol.smd` to `.lts` before authoring against it?

**What this is.** The bus protocol — the wire format, the message fields, the value codec — lives in an
un-migrated `.smd` file. It is prose the graph *points at and cannot reach*.

**Why it matters.** **15 identifiers, referenced 58 times across the graph, zero resolvable.** Every decision in
Themes A and B needs to amend `[BP-MSG-FIELDS]` or `[BP-CAN-ID]`, and **those are not nodes.** Authoring the
freshness spec without this means writing the 16th unresolvable reference.

| Option | Tradeoffs |
|---|---|
| **Migrate `spec/01` first** (`tools/smd_to_lts.py` exists) | Closes 58 dangling references in one move — the highest-leverage item in the remaining 45 files. Cost: it's a real migration, and which corpus root it lands in is itself unratified (`spec/` is a root of nothing today) |
| Author the new specs anyway, referencing dangling IDs | Faster tonight; deepens the exact debt |

**My recommendation:** **migrate first.** It's the difference between specs that link and specs that gesture.

**Your call:** Migrate spec/01 first (tools/smd_to_lts.py exists)
**Comments:** Migrate it if it passes smd's spec-gap-analyzer (static code analysis).  We don't want to migrate a stale smd spec to LTS and treat it like it's authoritative/current/accurate.

---

## Summary at a glance

| # | Decision | My recommendation |
|---|---|---|
| **D1** ⛔ | Hash or allocate namespace IDs | **Allocate hierarchically at build time** (supersede the runtime-registrar endpoint) |
| **D2** | v2 broke dual-filter exact matching | **Accept the loss; correct `[CXC-NSID-CAN-WINDOW]`** |
| **D3** | `src = 0` hazard | **`src == 0` implies listen-only**, logged loudly |
| **D4** ⛔ | Message freshness *(reverses your earlier call)* | **Receiver-side age + declared interval + 3× timeout + heartbeat**; kill per-message timestamps |
| **D5** | Representing `unknown` | **Type tag `0xFD`**, same flag day, UART too |
| **D6** | Locality: scope bit or two segments | **Two segments + gateway**; keep scope bits as policy *(wants a discussion)* |
| **D7** | 977-frame bulk transfer | **Keep bulk off CAN**; revisit flow control when a CAN-only leaf needs it |
| **D8** | Pi `restart-ms` | **0 (manual)** on the gateway; rehearse bus-off before shipping |
| **D9** | Pi SJW | **Widen**, bundled with the next planned `rpi5` touch |
| **D10** | ST7796 board profile bundle | **Land it, build-first** |
| **D11** ⛔ | Migrate `spec/01` to `.lts` | **Migrate first** — it unblocks D1–D5 authoring |

---

## Not asking — doing these unless you object

- **`SO_PRIORITY` + a `prio` qdisc on the Pi.** Software-only, no fork: a tier-0 alarm currently waits **3.2–4.16 ms**
  behind queued bulk versus **~580 µs** of actual wire time, because SocketCAN doesn't derive packet priority from
  the CAN ID. The `prio` field cannot see this. Largest latency win available, and it costs nothing architectural.
- **Stop swallowing ENOBUFS as a generic write error** — 828 in a 2,000-frame burst logging as "CAN write error"
  is a counter that can't distinguish a full queue from a dead wire.
- **`.gitignore` `cyd/build_fingerprints.json`** — a generated artifact currently tracked; it will show dirty forever.

## Needs 30 seconds of your eyes, not a decision

- **`corpora/platform-specs/60-board-hardware-inventory.lts.bak`** holds a section heading `## 5. What Generalizes`
  that appears in **neither main nor the live file**. Either it was renamed and the heading is noise, or content
  was dropped. I kept the `.bak` rather than guess.
- **`stash@{0}`** is the *pre-change* golden render captures for two PNGs currently modified in the tree — worth
  keeping until the render work settles. **`stash@{1}`** is 37 files against `ux_composer/`, a tree deleted
  2026-08-08. Both exported to patch files, so dropping either is now reversible.
