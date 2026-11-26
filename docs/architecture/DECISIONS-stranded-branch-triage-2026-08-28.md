> ## ✅ RESOLVED 2026-08-28 — 9 evidence-decisive branches CLEARED (David-authorized)
>
> Abandoned (deleted local + origin) after git-expert **independently re-verified** each is
> subsumed/superseded — content preserved on `origin/main` or a KEPT branch, nothing uniquely
> wanted lost. The **15 NEEDS-DAVID** rows below remain open.
>
> **Cleared:** `feat/substrate-choice-guard` (⊂ door4) · `feat/can-mqtt-bridge-rpi5` (⊂ can-bridge-adaptive) ·
> `docs/fleet-plan-2026-08-13` · `docs/session-2-record` · `defect/bus-storage-containment` ·
> `feat/wellknown-announce-root` (main fuller — the inverted claim) · `docs/improvement-suggestions-03-06` (inverted) ·
> `fix/toolbox-reconnect-rule` (⊂ bus-exactly-once-delivery) · `audit/worktrunk-2026-08-17`.
>
> ⚠ **Ledger correction:** `audit/worktrunk-2026-08-17` was mislabeled here as a "small audit branch" — it is
> actually a **41k-line divergent accumulation snapshot** (756 files vs main). All 58 of its authored files were
> verified accounted-for on main before deletion. A snapshot-then-drift accumulation branch is a fork-divergence smell.
>
> **Recovery SHAs** (reflog/origin, briefly): `961bb41` · `f834dc2` · `e74513b` · `e34af47` · `27d54f6` ·
> `3b5b32a` · `9cd8d57` · `48e6283` · `f04de0b`.

## 📍 STATUS — updated 2026-08-31

> **Update 2026-09-02 — 11 more decided (in flight; see each section's Your-call).** Still OPEN (4): `feat/esp32c3-can-node` (DISCUSS) · `feat/device-announcement` (lean ABANDON) · `perf/hosyond-dirty-rows` (lean ABANDON) · `spec/75-worktrunk-cutover` (lean ABANDON, salvage spec first?).


**9 of 24 CLEARED** (abandoned, David-authorized) · **15 awaiting your review.** The 15 below are the only thing gating the freeze lift. (Category A — the 6 land-ready branches — was resolved separately: 5 landed, r503 abandoned.)

**✅ CLEARED (9):** `feat/substrate-choice-guard` · `feat/can-mqtt-bridge-rpi5` · `docs/fleet-plan-2026-08-13` · `docs/session-2-record` · `defect/bus-storage-containment` · `feat/wellknown-announce-root` · `docs/improvement-suggestions-03-06` · `fix/toolbox-reconnect-rule` · `audit/worktrunk-2026-08-17`

**◻ OPEN — needs your review (15), grouped by my lean:**

| Branch | My lean |
|---|---|
| `feat/firmware-transport-seam` | **LAND** — "firmware speaks ComposeBus" seam, not on main |
| `feat/door4-and-fleet-transport` | **LAND** — substrate-agnostic poll |
| `docs/pcb-corpus` | **LAND / re-derive** — genuinely absent on main |
| `defect/bus-exactly-once-delivery` | **LAND (small)** — only spec + toolbox note remain |
| `fix/hookup-legibility-gate` | **LAND** — genuine tested fix not on main |
| `feat/esp32c3-can-node` | DISCUSS — land only if the C3 CAN node is still a target |
| `feat/can-bridge-adaptive-registry` | DISCUSS — main's bridge.py moved ahead |
| `docs/quality-events-union` | DISCUSS / re-derive — corpus being rewritten now |
| `defect/bus-storage-path-containment` | LAND spec-only **or** ABANDON (**dup of `rescue/console-surface-singleton`**) |
| `spec/73-bus-storage` | RE-DERIVE / ABANDON — live corpus exists |
| `tm1637-diag` | VERIFY / ABANDON |
| `spec/75-worktrunk-cutover` | ABANDON (salvage spec first?) |
| `feat/device-announcement` | ABANDON — core already on main |
| `lift2-cyd-line-renderer` | ABANDON (verify) |
| `perf/hosyond-dirty-rows` | ABANDON |

Full per-branch evidence below. Fill the **Your call** slot on each of the 15, or just reply with your decisions.

---


# Stranded-branch triage — land / abandon / which-variant-wins

> **What this consolidates.** The repo is FROZEN mid-cutover to the worktrunk land model
> (`[DEC-WORKTRUNK-LAND-MODEL-2026-08-26]`). A parallel sweep is landing the 6 "bucket A"
> ready branches and the mechanical `wt-agent-*` branches (bucket F) are being pruned by
> git-expert. **This doc is the residue you actually have to judge**: buckets **B** (older
> feature work), **C** (docs/defect), **D** (fork-divergence), **E** (obsolete cutover) —
> 24 branch decisions. It replaces the one-line-per-branch worklist
> (`RESIDUAL-TRIAGE-2026-08-26.md`) with per-branch EVIDENCE so each call is fast and correct.
>
> **How to answer.** Each `### <branch>` carries **What this is**, **Why it matters**, an
> **Evidence** block, an **Options** table, **My recommendation** (my honest lean), and a
> **Your call: ____** slot. Fill the slots in place, or reply "recommendations accepted" (or
> per-branch) on the channel. I encode your calls, not mine.

## How to use this

The recommendations are my honest engineering lean from the git + decision-graph evidence,
not a nudge — overrule freely. **Two structural facts frame every call below, established
by measurement:**

1. **Everything here is ~200+ commits behind main across a partial `compose_projects/` →
   `apps/` reorg.** Main has advanced **233 commits since Aug 14**; these branches were cut
   Aug 4–17. So "LAND" for any of them means *git-expert rebases the branch's own commits
   onto today's main and re-QAs* — never a raw merge. The two-dot `git diff main..branch`
   numbers are swamped by that reorg (hundreds of thousands of "deletions" that are just
   main's newer files) and are **not** a measure of the branch's content — I used
   `git cherry` patch-equivalence and per-file compares instead.
2. **`git cherry` `-` = that commit's patch is already on main.** Several "defect/fix"
   branches turned out to have their FIX + TEST already landed; only an *unratified spec
   `.lts`* remains stranded. Those are marked decisive.

Each branch is tagged **[EVIDENCE-DECISIVE]** (the evidence settles it — I can act without
you) or **[NEEDS-DAVID]** (a genuine judgment call). **You can skip every EVIDENCE-DECISIVE
one** unless you want to spot-check my read. Nothing here blocks the cutover; decide in any
order.

---

## Summary at a glance

| # | Branch | Bucket | My recommendation | Decisive / Needs-David |
|---|---|---|---|---|
| 1 | feat/device-announcement | B | **ABANDON** — core feature already on main; branch is an older fork | NEEDS-DAVID |
| 2 | feat/esp32c3-can-node | B | **DISCUSS** — overlaps main's C3+ST7796; land only if the C3 CAN node is still a target | NEEDS-DAVID |
| 3 | feat/firmware-transport-seam | B | **LAND (rebase)** — core "firmware speaks ComposeBus" seam, not on main | NEEDS-DAVID |
| 4 | feat/door4-and-fleet-transport | B | **LAND (rebase)** — substrate-agnostic poll; supersedes #5 | NEEDS-DAVID |
| 5 | feat/substrate-choice-guard | B | **ABANDON as standalone** — its 2 commits ⊂ #4 | ✅ CLEARED |
| 6 | feat/can-bridge-adaptive-registry | B | **DISCUSS** — main's bridge.py has moved ahead; supersedes #7 | NEEDS-DAVID |
| 7 | feat/can-mqtt-bridge-rpi5 | B | **ABANDON as standalone** — its 1 commit ⊂ #6 | ✅ CLEARED |
| 8 | lift2-cyd-line-renderer | B | **ABANDON** (verify) — oldest branch (Aug 4); renderer has evolved | NEEDS-DAVID |
| 9 | spec/73-bus-storage | B | **RE-DERIVE / ABANDON** — pass-1 `.lts`, placement unratified; live corpus already exists | NEEDS-DAVID |
| 10 | docs/fleet-plan-2026-08-13 | C | **ABANDON** — all three files already on main | ✅ CLEARED |
| 11 | docs/pcb-corpus | C | **LAND / re-derive** — genuinely absent on main; makes PCB landscape queryable | NEEDS-DAVID |
| 12 | docs/session-2-record | C | **ABANDON** — stale landing plan + session_handover ruled not-in-git | ✅ CLEARED |
| 13 | defect/bus-storage-containment | C | **ABANDON** — fix+test already landed (origin/land/…); only unratified spec left | ✅ CLEARED |
| 14 | defect/bus-storage-path-containment | C | **LAND spec-only, or ABANDON** — console fix landed; spec+defect-note stranded. **Dup of `rescue/console-surface-singleton-2026-08-15`** | NEEDS-DAVID |
| 15 | feat/wellknown-announce-root | D | **ABANDON** — fully subsumed; **main is MORE complete** (see headline) | ✅ CLEARED |
| 16 | docs/improvement-suggestions-03-06 | D | **ABANDON** — files on main; main's `04` .lts is fuller | ✅ CLEARED |
| 17 | docs/quality-events-union | D | **DISCUSS / re-derive** — 161 recovered fields are real, but target specs are absent on main and that corpus is being rewritten right now | NEEDS-DAVID |
| 18 | defect/bus-exactly-once-delivery | D | **LAND (small)** — fix+test already on main; only the spec + toolbox note remain | NEEDS-DAVID |
| 19 | fix/hookup-legibility-gate | D | **LAND (rebase)** — `8ba563c` is a genuine tested fix not on main | NEEDS-DAVID |
| 20 | fix/toolbox-reconnect-rule | D | **DECIDE WITH #18** — its 2 commits ARE #18's unlanded pair | ✅ CLEARED |
| 21 | tm1637-diag | D | **VERIFY / ABANDON** — reactive_core tm1637 is on main; diag modes may be redundant | NEEDS-DAVID |
| 22 | perf/hosyond-dirty-rows | D | **ABANDON** — perf work already landed; residue is not-in-git handover records + superseded agent-defs | NEEDS-DAVID |
| 23 | audit/worktrunk-2026-08-17 | E | **ABANDON** — audits an approach the Aug-26 model retired | ✅ CLEARED |
| 24 | spec/75-worktrunk-cutover | E | **ABANDON (salvage first)** — superseded changelist; may hold queryable spec value | NEEDS-DAVID |

**Headline (bucket D fork-divergence):** the two "big" divergence claims from the worklist
are **inverted by measurement**. `feat/wellknown-announce-root`'s reported "~1900 lines
dropped on main" is a two-dot-diff artifact — its core feature files (`device_announce.py`,
`ns_announce.py`, `announce.cpp`, `bridge.py`, …) are **byte-identical or MORE complete on
main** (main has +360/+263/+216 lines in the build/config files the branch would *regress*).
`docs/improvement-suggestions-03-06`'s "2 .lts (334 lines) never landed" is likewise stale:
both `.lts` are on main and main's `04-…` is 35 lines longer. **Neither is a live fork — both
are subsumed.** The real, still-stranded content is small and specific: an exactly-once
*spec* note (#18/#20), one hookup *fix* (#19), and the PCB-landscape corpus (#11).

**Counts:** 9 EVIDENCE-DECISIVE · 15 NEEDS-DAVID. Bucket B: 9 · C: 5 (one a byte-dup) · D: 8 · E: 2.

---

# Bucket B — older feature work (likely live, needs rebase + your nod)

### feat/device-announcement
**[NEEDS-DAVID]**

**What this is.** The feature that lets fleet devices ANNOUNCE themselves on the bus so it can
be polled (`python/composeio/device_announce.py`, generated C headers, parity tests). Tip
`1b0199a`, Aug 13; the branch also carries a `ade7009` "SNAPSHOT of other agents' uncommitted
work — NOT MINE" baseline that inflates its size to 120 files / 28k lines.

**Why it matters.** Device-announce is a foundational bus capability; landing a stale copy on
top of the live one would *regress* it, while abandoning loses nothing if the current version
is already in place.

**Evidence.**
- Age: 15 days (Aug 13). Nominal size 120f/+28k, but that is mostly the "NOT MINE" snapshot.
- Of the 27 files in the real feature commit `1b0199a`: **10 are byte-identical on main**
  (including `device_announce.py`, `gen_device_announce_header.py`, `test_device_announce.py`,
  `announce_pack.h`), **17 differ**, **0 are absent**. The feature is on main and has evolved
  past this branch — the same pattern proven on `feat/wellknown-announce-root` (#15).
- `git cherry` marks both commits `+` only because the whole-commit patch differs (the
  differing 17 files + the NOT-MINE snapshot), not because the feature is missing.

| Option | Tradeoffs |
|---|---|
| **ABANDON** | Zero loss if the 17 differing files are just main's newer evolution (highly likely). Fastest. |
| Diff the 17 files, salvage any unique delta | Costs a focused review; only worth it if you recall shipping something here that main lacks. |
| LAND (rebase) | Rejected — would reintroduce an older fork over a live, evolved feature. |

**My recommendation.** **ABANDON.** The core is demonstrably on main; the size is a mirage
from the "NOT MINE" snapshot. If anything in the 17 differing files nags you, I'll diff them
before dropping — but my lean is drop.

**Your call: David 2026-09-02: **ABANDON** — core already on main.**

### feat/esp32c3-can-node
**[NEEDS-DAVID]**

**What this is.** Bring-up of the ESP32-C3 as the fleet's first CAN node, plus a 480×320
ST7796 glass panel on it and a dirty-region render optimization (6 commits, 32f/+10k, Aug 13).

**Why it matters.** This is hardware bring-up: its value is entirely whether the C3-as-CAN-node
is still a board you intend to field. If it is, this is a head start; if the fleet moved to a
different CAN MCU, it is dead weight.

**Evidence.**
- Age: 15 days. All 6 commits `+` (unlanded).
- **Overlap risk:** main already carries "ESP32-C3 + ST7796 board profile bundle" work (it
  landed via `perf/hosyond-dirty-rows`'s now-merged commits and the reactive_core tree). So the
  branch and main likely diverge on the same board.
- No decision-graph node ratifies or retires the C3 CAN node — this is a hardware-roadmap
  judgment, not a settled call.

| Option | Tradeoffs |
|---|---|
| **DISCUSS** (hardware-architect check) | Confirms whether the branch adds anything past main's C3+ST7796 bundle before spending rebase effort. |
| LAND (rebase) | Good if the C3 CAN node is live; risks conflicting with main's board bundle. |
| ABANDON | Right if the CAN-node role moved to another MCU; loses the dirty-region perf idea. |

**My recommendation.** **DISCUSS** — this wants a hardware-architect read against main's current
C3 board files, not a blind land. Lean: land the CAN-node parts if the board is still real,
drop the rest.

**Your call: David 2026-09-02: **NOT abandon** — diff vs main; **ESP32-C3 CANBUS support is a FORWARD REQUIREMENT**, so preserve/cherry-pick the unique CAN-node work — hardware-architect investigating.**

### feat/firmware-transport-seam
**[NEEDS-DAVID]**

**What this is.** The seam that lets device firmware speak *ComposeBus* instead of raw MQTT —
one transport table + a UART path (single commit `7f224e3`, 21f/+2.8k, Aug 12).

**Why it matters.** This is the architectural boundary that makes the fleet substrate-agnostic
(MQTT / UART / CAN behind one contract). Abandoning it strands a core piece of the bus-native
firmware story; it's the conceptual parent of #4.

**Evidence.**
- Age: 16 days. Single commit, all `+` (unlanded).
- Directly on the principle line David has been driving ("firmware speaks the bus, not the
  wire"). Related work (#4 door4, #2 c3-node) builds on the same seam idea.
- Not superseded by any landed commit or ratified decision I can find.

| Option | Tradeoffs |
|---|---|
| **LAND (rebase)** | Preserves the core seam; moderate rebase across the reorg. |
| Fold into #4 and land once | #4 (door4) already builds on the seam; landing #4 may subsume this — check overlap first. |
| ABANDON | Loses a foundational abstraction; only right if the seam was re-implemented on main. |

**My recommendation.** **LAND (rebase)** — but check whether #4 already contains it, so you land
the seam once, not twice. This is the kind of principle-aligned work worth the rebase cost.

**Your call: David 2026-09-02: **LAND** (rebase) — landing via the gated model.**

### feat/door4-and-fleet-transport
**[NEEDS-DAVID]**

**What this is.** Makes the fleet poll "work identically on MQTT, UART and CAN" — a spec-dict
literal names the substrate ("DOOR 4"), and the substrate-choice guard asks *who chooses* the
substrate (5 commits, 13f/+4.6k, Aug 13). It contains #5's two commits.

**Why it matters.** This is the payoff of the transport-seam work — one poll, any wire. It also
subsumes the standalone `substrate-choice-guard` branch, so landing it clears two branches.

**Evidence.**
- Age: 15 days. All 5 commits `+`.
- **Superset:** commits `d6e20fd` + `961bb41` are shared with `feat/substrate-choice-guard`
  (#5) — door4 is the fuller branch.
- Also imports device-announce "verbatim (NOT MY WORK)" in `377670c`, so part of its diff is
  the #1 feature that's already on main.

| Option | Tradeoffs |
|---|---|
| **LAND (rebase); drop #5** | One rebase clears door4 + substrate-guard; strip the imported device-announce noise. |
| LAND #5 (guard) only, defer door4 | Smaller, but leaves the substrate-agnostic poll stranded. |
| ABANDON both | Loses the multi-substrate poll; only if the design changed. |

**My recommendation.** **LAND (rebase)**, and treat #5 as decided-by-this. Confirm it composes
with the transport-seam (#3) so the three transport branches land as one coherent slice.

**Your call: David 2026-09-02: **LAND** (rebase) — landing.**

### feat/substrate-choice-guard
**[✅ CLEARED 2026-08-31]**

**What this is.** A guard that asks who *chooses* the transport substrate (rather than who
reaches paho), fixing a case where a licensing scope was doing architectural duty (2 commits,
5f/+1.7k, Aug 13).

**Why it matters.** It is not an independent decision — its entire content is already inside #4,
so keeping it as a separate branch only creates a duplicate-landing hazard.

**Evidence.**
- Both commits (`d6e20fd`, `961bb41`) are a **strict subset** of `feat/door4-and-fleet-transport`
  (#4) — verified in the `git cherry` output (identical hashes).
- Standalone size 5f/+1.7k, all `+`.

| Option | Tradeoffs |
|---|---|
| **ABANDON as standalone; land via #4** | Correct — avoids landing the same two commits twice. |
| Land this instead of #4 | Loses the door4 poll work that sits on top. |

**My recommendation.** **ABANDON as a standalone branch** — its content lands (or not) with #4.
No separate judgment needed.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### feat/can-bridge-adaptive-registry
**[NEEDS-DAVID]**

**What this is.** The CAN↔MQTT bridge that filters in-process, learns a registry, and drops
frames that won't fit — plus its rpi5 deploy (2 commits, 8f/+3k, Aug 12). Contains #7's commit.

**Why it matters.** The bridge is live infrastructure (it runs on rpi5), and main's `bridge.py`
has already moved ahead of this branch — so landing the branch as-is could regress the deployed
bridge.

**Evidence.**
- Age: 16 days. Both commits `+`, but **`services/can_mqtt_bridge/bridge.py` on main is ahead**
  of the branch version (measured on #15: main `+5/-84` vs this lineage's bridge.py).
- **Superset:** commit `f834dc2` is shared with `feat/can-mqtt-bridge-rpi5` (#7).

| Option | Tradeoffs |
|---|---|
| **DISCUSS** — diff branch bridge.py vs main first | Main's bridge evolved; land only the adaptive-registry delta that main lacks. |
| LAND (rebase) whole branch | Risks reverting main's newer bridge changes. |
| ABANDON | Right if the adaptive-registry idea already landed in main's bridge. |

**My recommendation.** **DISCUSS** — this needs a targeted `bridge.py` diff against main before
any land, because the deployed bridge is downstream. Lean: cherry the adaptive-registry logic if
main lacks it, else abandon. #7 is decided-by-this.

**Your call: David 2026-09-02: diff bridge.py for TRULY-UNIQUE adaptive-registry logic; **cherry-pick if unique, else abandon** — composeio-expert investigating.**

### feat/can-mqtt-bridge-rpi5
**[✅ CLEARED 2026-08-31]**

**What this is.** The single commit that deploys the CAN-MQTT bridge to rpi5 (and kills two
defects first) — 6f/+1.2k, Aug 12.

**Why it matters.** It is a strict subset of #6, so it carries no independent decision; keeping
it separate risks a double-land of the same commit.

**Evidence.**
- Its only commit `f834dc2` is **contained in** `feat/can-bridge-adaptive-registry` (#6).

| Option | Tradeoffs |
|---|---|
| **ABANDON as standalone; decide via #6** | Correct — no unique content. |

**My recommendation.** **ABANDON as a standalone branch.** Folded into #6.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### lift2-cyd-line-renderer
**[NEEDS-DAVID]**

**What this is.** A line-by-line CYD display renderer with DMA overlap and dirty-band redraw,
tagged `[SPEC:CXC-LINE]` ("Lift #2") — 15f/+1.6k. **The oldest branch in scope (Aug 4).**

**Why it matters.** The render engine is one of the most heavily-evolved parts of the stack
since Aug 4; a 24-day-old renderer branch is the most likely of any here to be quietly
superseded — landing it could fight the current engine.

**Evidence.**
- Age: **24 days** (oldest). Single commit `+`.
- The renderer has had major work since (COMMP/font-unify/scene-geometry commits visible on the
  worktrunk branches, all Aug 15–17), so the CXC-LINE approach may already be re-realized.
- I did not find a landed `[SPEC:CXC-LINE]` on main, but the renderer's shape has changed enough
  that a rebase is high-conflict.

| Option | Tradeoffs |
|---|---|
| **ABANDON (verify against current engine)** | Likely superseded; a render-engineer glance confirms cheaply. |
| LAND (rebase) | High conflict risk against 3+ weeks of renderer evolution. |
| Salvage the DMA-overlap idea as a spec note | Keeps the technique without the stale code. |

**My recommendation.** **ABANDON after a render-engineer verify** that DMA-overlap / dirty-band
is already (or better) handled. If the technique is genuinely absent, salvage it as a spec note
rather than rebasing 24-day-old renderer code.

**Your call: David 2026-09-02: **render-engineer to assess value** — dispatched; land/abandon on its verdict.**

### spec/73-bus-storage
**[NEEDS-DAVID]**

**What this is.** Pass 1 of 2 converting the `bus_storage` service description into a FactLattice
`.lts` spec — explicitly committed "NOT reconciled `[PLACEMENT UNRATIFIED]`" (`2303db4`, 4f/+1.9k,
Aug 15). (Its other commit `db8207f`, the R503 part + hookup, is already on main.)

**Why it matters.** A queryable bus_storage spec is valuable — but a live `bus-storage` corpus
(218 nodes) already exists in the engine, so landing an unreconciled repo-side pass-1 could
create a second, conflicting source of truth.

**Evidence.**
- The `bus-storage` corpus is loaded and fresh in lts-engine (218 nodes) from
  `~/.claude/factlattice_corpora/…`, **not** this repo path.
- The commit self-labels `[PLACEMENT UNRATIFIED]` and "pass 1 of 2, NOT reconciled".
- `2303db4` is the shared base of several defect branches (#13, #14) — it is *not* on main.

| Option | Tradeoffs |
|---|---|
| **RE-DERIVE into the live corpus** | Avoids a duplicate spec source; captures any content the live corpus lacks. |
| ABANDON | Right if the live corpus already covers it. |
| LAND as-is | Rejected — lands an explicitly-unratified, unreconciled placement. |

**My recommendation.** **RE-DERIVE / ABANDON** — do not land an unratified pass-1. If it holds
content the live `bus-storage` corpus lacks, fold that in through the corpus; otherwise drop.

**Your call: David 2026-09-02: **ABANDON** — abandoning (a live bus-storage corpus already exists).**

---

# Bucket C — docs / defect (check if superseded)

### docs/fleet-plan-2026-08-13
**[✅ CLEARED 2026-08-31]**

**What this is.** The fleet baseline plan, a CAN-binding teardown brief, and a value-codec defect
test pinned as docs (`e74513b`, 3f/+1.3k, Aug 13).

**Why it matters.** A superseded plan doc on a branch is pure carrying cost; if its files are
already on main there is nothing to decide.

**Evidence.**
- **All three files are present on main**: `docs/PLAN-fleet-baseline-and-bus-native.md`,
  `docs/can-binding-teardown-brief.md`, `python/tests/test_value_codec_type_ambiguity.py`.
- `PLAN-fleet-baseline-and-bus-native.md` was measured **byte-identical** between main and the
  wellknown branch (#15), i.e. this content flowed to main by another path.

| Option | Tradeoffs |
|---|---|
| **ABANDON** | The files are on main; nothing stranded. |
| Diff for a stale delta | Only if you recall editing the plan after it landed. |

**My recommendation.** **ABANDON** — content is on main.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### docs/pcb-corpus
**[NEEDS-DAVID]**

**What this is.** Folds the PCB board landscape into the queryable corpus "as a derivation" —
adds `corpora/platform-specs/64-pcb-board-landscape.lts` and a composeio profile `.lts`
(`581ff5b`, 3f/+289, Aug 13).

**Why it matters.** This makes the PCB landscape *queryable* knowledge rather than prose — genuine
durable value. But it lands into `corpora/platform-specs/`, the same staging area that is being
actively rewritten right now (see working-tree churn), so placement is unsettled.

**Evidence.**
- Its `.lts` files are **absent on main** — genuinely unlanded content, not a stale dup.
- Target dir `corpora/platform-specs/` is under active, `[PLACEMENT UNRATIFIED]` churn (the
  recent `dd29144` "NEW events/ stream under platform-specs [PLACEMENT UNRATIFIED]" commit and
  the modified `56-terminal-widget.lts` in the working tree).

| Option | Tradeoffs |
|---|---|
| **LAND / re-derive into the ratified corpus home** | Keeps the queryable PCB landscape; needs a placement decision first. |
| ABANDON | Loses queryable PCB knowledge; only if it's redundant with the PCB corpus elsewhere. |

**My recommendation.** **LAND the content, but into whatever the ratified `platform-specs`
placement resolves to** — don't drop real corpus content, but don't cement it into an unratified
path either. ⚠ This one wants the placement question answered first.

**Your call: David 2026-09-02: **LAND into a NEW corpora dir `corpora/pcb-specs`** (NOT platform-specs) — factlattice-editor placing.**

### docs/session-2-record
**[✅ CLEARED 2026-08-31]**

**What this is.** The session-2 record (night queue, handover, four-way summary experiment) and a
landing plan that "supersedes the 03:37 audit's" (`e34af47`, 6f/+2.1k, Aug 13), living under
`session_handover/`.

**Why it matters.** It's a superseded, dated landing plan plus session records — and you've
already ruled that `session_handover/` per-machine records don't belong in git.

**Evidence.**
- `session_handover/composeio_hardware_architect/session_number_2/LANDING-PLAN.md` is **absent on
  main** — deliberately, per the not-in-git rule.
- It self-describes as *"the CURRENT landing plan superseding the 03:37 audit's"* — an Aug-13
  plan now superseded twice over by `[DEC-WORKTRUNK-LAND-MODEL-2026-08-26]`.
- The same not-in-git pattern is on record: `perf/hosyond-dirty-rows`'s `153b050`/`ebbdb5b`
  untracked exactly these session artifacts by your ruling.

| Option | Tradeoffs |
|---|---|
| **ABANDON** | Consistent with the not-in-git ruling; the plan is stale. |
| Keep the "four-way summary" experiment note | Only if that experiment is a lesson you want in the corpus — re-file it, don't land the branch. |

**My recommendation.** **ABANDON.** Stale plan + not-in-git records.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### defect/bus-storage-containment
**[✅ CLEARED 2026-08-31]**

**What this is.** A path-containment fix for `bus_storage` (a request always answers; one
containment primitive at the seam) with 41 RED-first tests (`27d54f6`, Aug 15).

**Why it matters.** The fix is safety-relevant — but if it already landed, the only thing left on
the branch is an unratified spec, and re-landing the fix is a no-op at best.

**Evidence.**
- **The fix `f4c16f5` and test `27d54f6` are already on main** (`git cherry` `-`), landed via
  `origin/land/bus-storage-containment` (`56ca155`, Aug 16).
- What remains `+` (stranded) is only the spec `.lts` (`6cf0db0` + the pass-1 `2303db4` from #9),
  both `[PLACEMENT UNRATIFIED]`.

| Option | Tradeoffs |
|---|---|
| **ABANDON** | Fix+test are on main; the residue is unratified spec handled under #9. |
| Re-derive the spec into the live corpus | Only if the containment spec adds to the corpus; fold via #9. |

**My recommendation.** **ABANDON.** The defect is fixed and landed; the leftover spec rides with
the #9 re-derive decision.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### defect/bus-storage-path-containment
**[NEEDS-DAVID]** · ⚠ **byte-identical duplicate of `rescue/console-surface-singleton-2026-08-15`** (same tip `538b659`)

**What this is.** The console-surface-singleton work: the surface takes the flock BEFORE it opens
a window, a reproduced-then-closed duplicate-console test, the `[CXC-IC-SURFACE-SINGLETON]` spec,
and a defect-graph registration of the bug + its broken instrument (tip `538b659`, Aug 15).

**Why it matters.** The *fix* prevents duplicate console windows (a real bug you hit); the
stranded remainder is the spec + the defect-registry entry — durable records worth having if they
aren't already in the live graphs.

**Evidence.**
- **Fix `37ee92e` and test `4834ae1` are already on main** (`git cherry` `-`).
- Stranded `+`: spec `fddbe69` (`[CXC-IC-SURFACE-SINGLETON]`, IC-C28), defect-note `538b659`, and
  the pass-1 bus-storage `.lts` (`2303db4`, shared with #9).
- **Duplicate:** `rescue/console-surface-singleton-2026-08-15` has the identical tip `538b659` —
  reviewing this one covers both; drop the rescue branch.
- `defects/defect-graph.lts` is modified in the working tree, so the registration may already be
  captured live.

| Option | Tradeoffs |
|---|---|
| **LAND the spec + defect-note only** | Keeps the durable records; tiny, low-risk. Verify they're not already in the live graphs first. |
| ABANDON | Right if `[CXC-IC-SURFACE-SINGLETON]` and the defect entry already exist live. |
| LAND whole branch | Reintroduces the already-landed fix + unratified bus-storage `.lts`. |

**My recommendation.** **LAND the spec + defect-note only** (after checking they aren't already in
the live console spec / defect-graph), and **drop the `rescue/…` duplicate**. If they're already
live, ABANDON both.

**Your call: David 2026-09-02: **concur** — land the unique spec-only remainder or abandon per the evidence (console fix already on main); **drop the byte-identical `rescue/console-surface-singleton` dup** — git-expert executing.**

---

# Bucket D — fork-divergence (a partial version already landed; which wins?)

### feat/wellknown-announce-root
**[✅ CLEARED 2026-08-31]**

**What this is.** A CAN "well-known announce root": one hardcoded name that has never touched
MQTT, everything else learned (single commit `3b5b32a`, nominally 23f/+8.9k, Aug 13). The
worklist flagged this as the big fork — *"main got a REDUCED version; ~1900 lines dropped here."*

**Why it matters.** If the branch really held 1900 lines main dropped, abandoning it would lose
real work. **The measurement says the opposite** — landing the branch would *regress* main.

**Evidence (this is the load-bearing bucket-D finding).**
- Of the 23 files: **the core feature files are byte-identical on main** —
  `python/composeio/device_announce.py`, `ns_announce.py`, `transport_can.py`,
  `cyd/src/announce.cpp`, `announce_pack.h`, `ns_announce.h`, the generated headers,
  `test_can_announce_root.py`, `docs/PLAN-fleet-baseline-and-bus-native.md`.
- Where they differ, **main has MORE**: `cyd/build_fingerprints.json` main **+360**,
  `cyd/platformio.ini` **+263**, `cyd/src/CMakeLists.txt` **+216**, `services/…/bridge.py`
  main is **+79** net, `composeio-can-bridge.env` main **+36** net. The branch would *remove*
  those lines.
- The "~1900 dropped" number is a `git diff main..branch` artifact — that two-dot diff is
  dominated by main's post-reorg files, not by this feature.

| Option | Tradeoffs |
|---|---|
| **ABANDON** | Correct — the feature is on main and main is the fuller version. |
| LAND (rebase) | Actively harmful — reverts 800+ lines of build/config main already has. |

**My recommendation.** **ABANDON.** This is not a live fork; main won it and then some.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### docs/improvement-suggestions-03-06
**[✅ CLEARED 2026-08-31]**

**What this is.** Rescues improvement-suggestion docs 03–06 "from the discard path" — the
authoring-path-enforcement `.lts`, the recall-side-failures `.lts`, and two field-report `.md`s
(`9cd8d57`, 4f/+594, Aug 13). Worklist flagged: *"2 .lts (334 lines) never landed."*

**Why it matters.** These are process-lesson docs; losing genuinely-unlanded ones would be a real
gap. But the measurement shows they landed.

**Evidence.**
- **All four files are on main.** `03-authoring-path-enforcement.lts` is **identical** (105 lines
  both). `04-recall-side-failures…lts` — **main has 264 lines vs the branch's 229** (main
  `+0/-35`), i.e. main's copy is *fuller* and the branch adds nothing main lacks.
- The `improvement-suggestions` corpus is live and fresh (38 nodes).

| Option | Tradeoffs |
|---|---|
| **ABANDON** | Files are on main; main's `04` is the fuller copy. |
| Spot-diff the two `.md`s | Only if you suspect a lost paragraph; the `.lts` (the queryable part) are covered. |

**My recommendation.** **ABANDON.** Subsumed; the "334 lines never landed" note is stale.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### docs/quality-events-union
**[NEEDS-DAVID]**

**What this is.** Recovers 161 silently-dropped fields (bringing 61 in line with the code) across
three platform specs, and unions `quality.jsonl` lint records "that existed only on disk"
(`46bfa3d`+`9186d4f`, 4f/+410/−178, Aug 13).

**Why it matters.** 161 recovered spec fields is real signal-loss repair — but the target files
don't exist on main at these paths and that corpus area is being rewritten *right now*, so landing
the old branch would collide head-on with in-flight work.

**Evidence.**
- All four targets — `55-console-launches-the-intelligence.lts`, `56-terminal-widget.lts`,
  `61-config-over-bus.lts`, `events/quality.jsonl` — are **absent on main** at
  `corpora/platform-specs/`.
- That exact area is in active churn: `56-terminal-widget.lts` is modified in the working tree
  and `dd29144` just added `events/` under `platform-specs` `[PLACEMENT UNRATIFIED]`.
- So this is a fork against a moving target, not a clean land.

| Option | Tradeoffs |
|---|---|
| **DISCUSS / re-derive the 161 fields into the current specs** | Preserves the recovered content without clobbering in-flight edits. |
| LAND (rebase) | High collision risk with the specs being rewritten now. |
| ABANDON | Loses 161 recovered fields — only if they were re-recovered by the current work. |

**My recommendation.** **DISCUSS** — the field-recovery is worth keeping, but as a re-derive into
the live specs, coordinated with whoever is rewriting `platform-specs` now. ⚠ Not a clean A/B.

**Your call: David 2026-09-02: **re-derive the recovered fields into the current specs** (architect controls timing/placement) — factlattice-editor.**

### defect/bus-exactly-once-delivery
**[NEEDS-DAVID]**

**What this is.** Exactly-once bus delivery: de-double `on_message`, drop self-echo, with a
counted `== 1` test across transport topologies; plus the *spec* statement of delivery
multiplicity and a toolbox MCP-reconnect note (4 commits, Aug 11).

**Why it matters.** The behavior fix already shipped; what's stranded is the *spec* that documents
it and the toolbox rule — small, low-risk, and valuable because undocumented shipped behavior is
how the next agent re-derives a bug.

**Evidence.**
- **Fix `bc37ad1` and test `c545b22` are already on main** (`git cherry` `-`).
- Stranded `+`: `bb5f832` (spec — adds to `21-composebus-pattern-subscription.lts` +
  `34-composeux-intelligence-console.lts`, +36 lines) and `48e6283` (bootstrap/toolbox.md
  reconnect rule, +26/−12).
- ⚠ The toolbox commit `48e6283` **overlaps** `perf/hosyond-dirty-rows`'s `65fe15e`, which also
  edits the same reconnect rule in `toolbox.md` — reconcile so you land one coherent version.

| Option | Tradeoffs |
|---|---|
| **LAND the spec + toolbox note (rebase)** | Small, documents already-shipped behavior; reconcile the toolbox.md overlap with #22. |
| ABANDON | Leaves exactly-once behavior undocumented in the spec. |
| Spec only, defer toolbox | Splits a 2-commit change for little gain. |

**My recommendation.** **LAND** the spec + toolbox note (it's ~60 lines), reconciling the
`toolbox.md` edit against `hosyond`'s version. Decides #20 too.

**Your call: David 2026-09-02: **LAND (small)** — landing the stranded spec/note.**

### fix/hookup-legibility-gate
**[NEEDS-DAVID]**

**What this is.** The hookup-diagram legibility gate: presence ≠ readability, and specifically
"a conductor under a part body cannot be hopped and cannot be followed" — spec `70-hookup-review.lts`
+ `hookup_svg.py`/`hookup_layout.py` changes + a 262-line test (relevant commit `8ba563c`, Aug 16).

**Why it matters.** This gate is what keeps a wiring diagram from being shipped with an unreadable
crossing — a correctness gate on a human-facing artifact. The earlier gate commits already landed;
this is the follow-on that closes a real hole.

**Evidence.**
- The earlier legibility-gate commits (`1c7b4d2`, `d0a88ae`, `ca815a4`) are **already on main**
  (`git cherry` `-`).
- **`8ba563c` is `+` (not on main)** and is substantive: 619 insertions incl. a 262-line test
  `test_hookup_legibility_gate.py` and spec additions to `70-hookup-review.lts`.
- Sits on the shared console/bus-storage base stack, but the fix itself is isolable.

| Option | Tradeoffs |
|---|---|
| **LAND `8ba563c` (rebase)** | Closes a real legibility hole with its test; cherry just this commit onto main. |
| ABANDON | Leaves the "conductor under a part body" case unguarded. |
| Re-derive the rule into the current hookup code | Costs a rebuild; only if the hookup renderer changed shape on main. |

**My recommendation.** **LAND `8ba563c`** (cherry-pick the single fix commit onto rebased main;
it carries its own test). A tested correctness gate is worth the small rebase.

**Your call: David 2026-09-02: **LAND** (rebase) — landing.**

### fix/toolbox-reconnect-rule
**[✅ CLEARED 2026-08-31]**

**What this is.** Re-corrects the MCP reconnect rule in `bootstrap/toolbox.md` ("it WAS true and
silently stopped being"), plus the delivery-multiplicity spec — 2 commits, 3f/+62/−15, Aug 11.

**Why it matters.** It carries no content independent of #18 — its two commits *are* #18's two
stranded commits — so it's a decide-once, not a separate call.

**Evidence.**
- Its commits `bb5f832` + `48e6283` are **exactly** the two unlanded commits of
  `defect/bus-exactly-once-delivery` (#18) — identical hashes in `git cherry`.

| Option | Tradeoffs |
|---|---|
| **DECIDE WITH #18** | Correct — same two commits. Landing #18's spec+toolbox lands this. |

**My recommendation.** **DECIDE WITH #18** (my lean there is LAND). No separate judgment.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### tm1637-diag
**[NEEDS-DAVID]**

**What this is.** Combined R503 + 4× TM1637 firmware on one ESP32-S3, plus three bring-up
diagnostics (`tm1637 selftest`, single-tube `selftest N`, `wiggle` push-pull multimeter aid) —
4 stranded commits, Aug 24.

**Why it matters.** These are hardware bring-up aids; their value is whether the R503+TM1637 panel
is still being brought up. Main already carries a substantial reactive_core TM1637 stack, so some
of this may be redundant.

**Evidence.**
- The branch's *final throttle fixes* (`be4bfff`, `da13d5b`, `f99bf3f`) are **already on main**
  (`git cherry` `-`).
- Stranded `+`: `d5aa404` (combined firmware), `c8e4915`/`731e1e5`/`007500b` (the three diag
  modes, touching `firmware/reactive_core/src/tm1637/tm1637_panel.cpp`).
- **Main already has** `firmware/reactive_core/src/tm1637/tm1637_panel.{cpp,h}`,
  `hosttest/tm1637_panel_tests.cpp`, and `fingerprint_bringup_main.cpp` — so the panel is on main;
  whether the *selftest/wiggle* entry points specifically are is the open question.
- Newest branch in scope (Aug 24) and active R503/reactive_core work continues.

| Option | Tradeoffs |
|---|---|
| **VERIFY vs main's reactive_core, then land the diag modes if absent** | Keeps genuinely-useful multimeter aids without duplicating the panel. |
| ABANDON | Right if selftest/wiggle already exist on main; loses nothing. |
| LAND whole branch | Risks duplicating the panel code main already has. |

**My recommendation.** **VERIFY** whether `selftest`/`wiggle` are already on main's reactive_core;
land only the diag modes it lacks (they're cheap, LDO-safe bring-up aids), else ABANDON. A
hardware-architect can settle this in one pass.

**Your call: David 2026-09-02: **VERIFY** tm1637 fully supported + easy multi-hardware/widget reuse + diag modes — hardware-architect verifying; branch land/abandon follows.**

### perf/hosyond-dirty-rows
**[NEEDS-DAVID]**

**What this is.** Labeled a perf branch (85f/+19.6k) but the *perf* content already landed — the
stranded commits are data-scientist session-handover records and a couple of docs (wake-sequence
agent-defs, a toolbox reconnect note), Aug 15.

**Why it matters.** Its size makes it look important, but the stranded residue is precisely the
category you've ruled out of git (session handover) plus agent-def docs that main has since
rewritten — so landing it would re-add not-in-git records and stale agent-defs.

**Evidence.**
- The perf commits are **already on main** (`git cherry` `-`).
- The 5 stranded `+` commits: `c14bcf9`/`bd61773` *add* `session_handover/data-scientist/…`
  records, and `153b050` *untracks the six data-scientist session records* — the add and the
  untrack **self-cancel**. `65fe15e` is a `toolbox.md` reconnect note (overlaps #18/#20).
  `d86fabf` rewrites agent-defs ("WAKE SEQUENCE, tiered by role").
- Agent-defs on main have since changed heavily (measured on #15: e.g. `git-expert.md` ±401,
  `composeux-architect.md` ±192), so `d86fabf`'s versions are superseded.

| Option | Tradeoffs |
|---|---|
| **ABANDON** | Perf is on main; residue is not-in-git records + superseded agent-defs. |
| Salvage the "wake-sequence tiering" idea into current agent-defs | Only if that role-tiered wake concept isn't already reflected — re-file, don't land. |

**My recommendation.** **ABANDON.** The valuable part (perf) already landed; what's left is
either not-in-git by your ruling or superseded. If the role-tiered wake sequence in `d86fabf` is a
concept you still want, I'll re-file it against today's agent-defs rather than land this branch.

**Your call: David 2026-09-02: **ABANDON**.**

---

# Bucket E — likely obsolete (the old worktrunk cutover attempt)

### audit/worktrunk-2026-08-17
**[✅ CLEARED 2026-08-31]**

**What this is.** The Aug-17 worktrunk audit — "the tool measured, not summarised" — plus the
git-model DSD (delivered then WITHDRAWN), the push ruling, and runtime-written ledger/events
snapshots (31 stranded commits, Aug 17).

**Why it matters.** It audits and half-ratifies a cutover *approach* that a later, verified
ruling explicitly retired — so its analysis is now about a superseded model, and it carries
churny runtime-written files you don't want re-landed.

**Evidence.**
- `[DEC-WORKTRUNK-LAND-MODEL-2026-08-26]` (high-importance, verified against wt 0.74.0)
  **supersedes cherry-pick landing and the equivalence heuristics** — the exact subject this
  audit measured. The branch even contains `1ec71e4` "WITHDRAW the git-model DSD — it ratified an
  incomplete model."
- Includes runtime-written `deliverables`/`events` ledger commits (`00ca9dd`, `f04de0b`) that are
  regenerated live and shouldn't be hand-landed.
- Long shared stack with #14/#19 whose fixes are already on main.

| Option | Tradeoffs |
|---|---|
| **ABANDON** | The model it audits is retired; the durable rulings (push-is-free) already landed as decision nodes. |
| Salvage a specific measurement | Only if a number in the audit is still cited; it's in the DELIV ledger already. |

**My recommendation.** **ABANDON.** Superseded by the Aug-26 land model; its durable rulings are
already decision nodes.

**Your call: ✅ ABANDONED — cleared 2026-08-31 (David-authorized; branch deleted, content verified preserved).**

### spec/75-worktrunk-cutover
**[NEEDS-DAVID]**

**What this is.** The worktrunk cutover expressed as a queryable changelist spec, `[PLACEMENT
UNRATIFIED]` (tip `43db36a`, 25 stranded commits, Aug 17).

**Why it matters.** The cutover *approach* is superseded — but a well-structured queryable
changelist can hold spec value (the mechanics, the antipatterns) worth salvaging before the branch
is dropped, which is exactly the exception you flagged.

**Evidence.**
- Same supersession as #23: `[DEC-WORKTRUNK-LAND-MODEL-2026-08-26]` reversed the earlier
  "cut over later" and retired the cherry-pick model this spec describes.
- Self-labeled `[PLACEMENT UNRATIFIED]`; sits on the same shared stack whose fixes are on main.
- The worktrunk-expert has since re-verified the model against wt 0.74.0, so the *authoritative*
  mechanics live in the Aug-26 decision, not here.

| Option | Tradeoffs |
|---|---|
| **ABANDON after a salvage pass** | Drop the branch, but first lift any still-true mechanics/antipatterns into the live worktrunk spec/decision. |
| ABANDON outright | Fastest; risks losing a queryable detail the Aug-26 node didn't capture. |
| LAND as-is | Rejected — lands an unratified spec for a retired approach. |

**My recommendation.** **ABANDON after a salvage pass** — have the worktrunk-expert skim it for
any mechanic/antipattern not already in `[DEC-WORKTRUNK-LAND-MODEL-2026-08-26]` or the live
worktrunk spec, fold that in, then drop the branch.

**Your call: David 2026-09-02: **ABANDON** — superseded by today's worktrunk land model.**

---

*Provenance: git evidence (`git cherry`, per-file `git diff --numstat`, `git show`) gathered
read-only against a frozen repo on 2026-08-28, origin/main = `144ef44`; decision-graph queries via
lts-engine / decision-ops. `feat/mcp-command-direction @ 5f42cb7` was not touched. This DSD is
untracked during the freeze — not committed. Once you answer, each call becomes a `[DEC-*]` node
with `dsd-source:` pointing here.*
