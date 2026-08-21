# Away-Watch Progress Report — 2026-07-26 (maker-project away watch)

> David handed me an away watch: lock the maker-project design, slice it, and drive every dispatch to
> completion — plus validate + build the heartbeat bus-peer design if it held up. This is the write-up.
> **TL;DR:** the whole me.next queue landed — **S1 method graph**, the **BSG world-app helm patch**, and the
> **complete heartbeat feature** (validated → built → the visible delta indicator). **6 commits landed clean**
> (5 on composeio local main, 1 on a bsg feature branch), every one verified green with the base-ancestry
> cherry-pick discipline. Nothing pushed (your call). One subagent (the delta-indicator widget), reviewed and
> landed. Two live-console surfaces flagged for your glass; three calls waiting on you.

## Landed (6 commits — LOCAL, not pushed)

| Commit | Repo | What | Verified |
|---|---|---|---|
| `e3ea6dd` | composeio | dual-throttle MCU → **ESP32-C3 SuperMini** (catalog part + swap + regen) | 49 maker tests |
| `7bc1629` | composeio | **S1 maker method graph** + bootstrap wiring + projects maker fields `[FLOW:MAKER-PROJECT]` | 2027 render suite; parser 16/16 |
| `ee63804` | ~/maker/bsg | **throttle→heading helm seam** (world-app patch) | dry-run harness (6 checks) |
| `2b6d95b` | composeio | heartbeat **publish-current** — the bus-peer half `[CXC-IC-HEARTBEAT-TWO-SOURCES]` | monitor 11/11; launcher a–h |
| `10b893f` | composeio | honest cadence **relabel** landed + spec + **defect registry A3** | 2028 render suite |
| `a4ca640` | composeio | Config-tab **SET-vs-CURRENT delta indicator** | 22 config + 2033 render suite |

composeio `main` is now **24 ahead of origin**, LOCAL. bsg `feat/throttle-helm-control` holds `ee63804` (not pushed; no remote yet).

## The deliverables

### 1. The maker-project flow — landed end-to-end (S1 was the last slice)
S1 (`7bc1629`) made the maker method **first-class in the bootstrap**: the `[FLOW:MAKER-PROJECT]` corpus
(`maker/maker-project-flow.lts`, the a–f process + the pin→slot→binding seam + the one-compose.yaml-N-consumers
model), the `method_flow` wake document read alongside the principles, and the additive maker fields on the
project registry (`mcu`/`baseline_stack_rev`/`widget`/`slots`/`current_step`). **The WIP-reconcile was the
interesting part:** your uncommitted `projects.lts` rewrite (colon-form migration + ~8 added projects) and S1's
maker layer were BOTH kept — I folded S1's additions in and landed the tolerant `[PROJECT[:-]…]` parser, which
accepts both id forms and closes the empty-Projects-tab bug for good. The Galactica controller is registered
(`mcu: esp32-c3-supermini`). With S1 in, S2–S5 + the SuperMini were already down, so the **whole flow is proven**
— one `compose.yaml` drives the widget, the hookup guide, and the MCU config, and the fritzing-style guide is on
the glass.

### 2. The BSG world-app patch — the throttle actually steers the Galactica
`ee63804` (in `~/maker/bsg`, feature branch): the dual-throttle controller publishes `throttle/left|right` as
standing engine thrust; the **world** integrates the Galactica's heading from the engine DIFFERENTIAL and
republishes `game/own_heading` — which the **Qualia dradis already binds, so the dradis reflects the new heading
with ZERO display edits** (state-on-bus; the CYD dradis was NOT touched, per your steer). I vendored the ~40-line
heading math into `world/helm.py` so bsg takes no composeio dependency. Verified on a dry-run harness: seed at the
planet bearing, hard-over-left 1s → **+45°** (== the max-rate feel knob), balanced/deadband hold, `game/own_heading`
republished on change, and the stick position carries across an FTL jump. The FEEL knobs (rate/deadband/sign) are
flagged for your hands on the real pots.

### 3. The heartbeat feature — validated, then the missing half built
Your design was right, and the validation found something clean: **the subscribe+apply half was already built**
(the monitor re-reads the persisted cadence live via `cadence_provider`, IC-C13c — a Config-tab edit already
applies to the running monitor with no restart). The gap was **observability** — an applied edit gave no readback,
so it was indistinguishable from a broken one, which is exactly why it kept getting re-reported. So I built the
**bus-peer publish-current half** (`2b6d95b`): the harness is now a co-equal ComposeBus peer that PUBLISHES the
cadence it is *actually* running to `config/heartbeat/current`, and the Config card renders **SET-vs-CURRENT**
(`a4ca640`): "✓ applied · current N min" / "⚠ not yet applied" / "⚠ awaiting harness readback". The honest relabel
you'd greenlit landed too (`10b893f`), the spec `[CXC-IC-HEARTBEAT-TWO-SOURCES]` was extended with the readback
bullet, and attempt **A3** was recorded to the defect registry (the inaugural case's fourth-plus attempt — now
with the readback that should let it finally close). This is P6 turned on a recurring defect: the fix was correct
but *invisible*; making the harness publish its own state is the independent light the recurrence lacked.

## Verification discipline held throughout
Every landing gated on real tests (monitor 11/11, launcher a–h, the BSG dry-run harness, the 2033-test render
suite on each render-touching commit). Base-ancestry cherry-picks, never raw merges; worktrees only ever saw
committed state. The one subagent (the delta-indicator widget, render-engineer, Opus tier for composition) was
**reviewed before trusting** — I verified mechanically that the readback branch is truly read-only (never
publishes) and the write path + other cards were untouched — then landed it. `defects/defect-graph.lts` is now
committed, so worktree-dispatched investigators inherit the history.

## What needs YOUR glass / your call
- **Heartbeat label pick** — the card currently says "Quiescence-nudge cadence"; you wanted to choose among
  Quiescence-nudge / Stall-nudge / Heartbeat+subtitle. Your call.
- **Live glass on the two console surfaces** (machine-green ≠ glass-green, P6b): the **Defects Dashboard widget**
  and the new **SET-vs-CURRENT delta indicator** — specifically the ✓→⚠→✓ transition on a real console bounce
  (does an actual harness publish `heartbeat/current`, does the readback track as you edit/apply). That's the
  untested tier.
- **Heartbeat resolution-accept** — once the delta shows green on the glass, accept the defect's resolution (it's
  yours to close).
- **Push** — `main` is 24 ahead, held LOCAL. Your explicit go.

## Threads left open (not blockers)
- The SuperMini hookup guide uses a *generated* footprint (no core Fritzing part); sourcing the real community
  "ESP32-C3 SuperMini" .fzpz graphic is a follow-on (in progress on your return).
- The two landed branches (`build/maker-s1-method-graph`, `defect/heartbeat-cadence`) + their worktrees can be
  pruned now that their content is on main.
- The S4 sensor-schema ratify (`[CXC-HAL-OPEN]`), the registration-gate proposal, and the write-side rail-add
  persistence (#72) remain deferred.
