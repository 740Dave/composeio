# ComposePlatform — LTS conversion + reorg (additive, zero blast radius)

**Instance:** ComposeIO-Architect #16 · **2026-07-22 away watch** · **[DEC-SPEC-LTS-CONVERSION]**
**Approach approved by David:** build in a **brand-new `ComposePlatform/` directory, copy+convert from the
current project dir. Nothing in the live tree moves — zero blast radius.** The live `ux_composer/specs/*`,
`decisions/*`, and the running console are untouched; this new tree is the reviewable target-state, trivially
deletable if wrong. Reference template: Haven's `proposals/lts-cutover-steps.md` (adapted — because this is
additive, we SKIP Haven's hard-cutover Phases D/E and the rollback drama; the new tree *is* the safe artifact).

## Where it lives (a choice flagged for David)
`ComposePlatform/` is a **subdir inside the composeio repo** — so it's version-controlled + reviewable via
`git diff` in the repo you already review, and deletable in one commit. When you kick off the real migration,
promote it (git mv / extract to a top-level `${MAKER_HOME}/ComposePlatform/` repo per the TLD reframe). I
chose subdir-in-repo over a sibling repo to avoid cross-repo complexity during an unsupervised build. Your call
to relocate.

## The taxonomy (from [DEC-COMPOSEPLATFORM-REFRAME], registered in `profile/composeplatform-profile.yaml`)
```
ComposePlatform (TLD)
├─ LAYERS   → layers/composeio (io.*)   ·  layers/composeux (ux.*)
└─ FAMILIES → families/apps (app.console.* / app.composer.* / app.monitor.*)
              families/services (svc.*) ·  families/widgets (wid.*)
platform/ (plt.*) = cross-cutting: foundation, design-excellence, principles, insights, glossary, conventions
```
Each corpus dir holds `specs/ decisions/ artifacts/` ([DEC-SPEC-LTS-CONVERSION]: per-project separation, not
one flat pool). MAGMA reads each as a multi-root corpus, filtered by domain suffix (multi-corpus partition
model, Haven D5 — my MAGMA-lite `store.py` already supports a List of roots + `corpus_id=project`).

## Phases (adapted for additive)
- **A — Layout + profile** (DONE this watch): the dir skeleton + `composeplatform-profile.yaml` + this plan.
- **B — Copy + convert** (dispatched this watch, first slice): copy each source spec to its destination per the
  MAPPING below; convert the one `.smd` → `.lts`; author the platform-wide cross-cutting `.lts` from the
  existing docs; write `platform/99-composeplatform-conventions.lts` (readable, points at the profile yaml).
  **Copy, never move** — the source stays live.
- **C — Prove MAGMA multi-corpus** (after B): build a `graph_index.json` per corpus; prove a query filters to
  one corpus by domain; prove a cross-corpus reference resolves via the routing layer. Exercises the partition
  model with ≥2 real corpora (Haven GATE C, adapted — but nothing retires, so it's a proof, not a gate).
- **D — Cutover** (DEFERRED to David): repoint the console/tools/linter at `ComposePlatform/` as canonical +
  archive the old pool. A deliberate, glass-reviewed step — NOT done unsupervised.

## MAPPING — source → destination (the judgment; the builder executes it mechanically)
All sources are under `ux_composer/specs/` unless noted. All destinations under `ComposePlatform/`.
Ambiguous calls are marked `⚠confirm` — my lean stated; David adjusts at review.

### platform/ (plt.*) — cross-cutting
| source | dest |
|---|---|
| `99-composeio-conventions.smd` | `platform/99-composeplatform-conventions.lts` (convert .smd→.lts, readable, point at profile yaml) |
| `99-composeio-profile.yaml` | fold into `profile/composeplatform-profile.yaml` (already superseded by it — note, don't duplicate) |
| `docs/design-excellence-composeplatform.lts` | `platform/design-excellence.lts` (copy) |
| `docs/collaboration-and-design-principles.md` | `platform/collaboration-principles.lts` (convert md→lts structure) |
| `decisions/insights_and_warts.lts` | `platform/insights-and-warts.lts` (copy) |
| (new) | `platform/00-composeplatform-foundation.lts` — author the decompositional thesis + the reframe cascade as the foundation node |
| (new) | `platform/10-glossary.lts` — a starter glossary of the platform's load-bearing terms |

### layers/composeio (io.*)
`02-composer-internal-bus`, `13-mcp-action-binding`, `21-composebus-pattern-subscription` → `layers/composeio/specs/`

### layers/composeux (ux.*)
`12-composeux-render-engine`, `16-composeux-bindings-behaviors`, `17-composeux-lottie`, `18-composeux-svg`,
`19-composeux-gestures`, `20-composeux-headless-widgets`, `22-composeux-expression-dsl`,
`23-composeux-widget-primitives`, `25-composeux-audio`, `26-composeux-nfc`, `27-composeux-peripheral-hal`,
`29-composeux-embedded-reactive`, `30-composeux-carthing-surface`, `31-composeux-board-bringup`,
`32-composeux-hal-declaration`, `33-composeux-ha-bridge`, `36-console-visual-fidelity` ⚠confirm(ux vs app.console)
→ `layers/composeux/specs/`

### families/apps/intelligence-console (app.console.*)
`34-composeux-intelligence-console`, `35-intelligence-console-decisions`, `39-session-retrospective`,
`40-decisions-tab`, `41-intelligence-host-spawner`, `38-agent-team-host` ⚠confirm(console vs a platform host)
→ `families/apps/intelligence-console/specs/`

### families/apps/visual-composer (app.composer.*)
`00-composer-design-principles`, `00-composer-overview`, `01-composer-architecture`, `03-composer-modules`,
`04-composer-serialization`, `05-composer-preview-system`, `06-composer-intelligence-api`,
`07-composer-agent-build-plan`, `08-composer-tab-container`, `09-composer-reactive-window`,
`10-composer-runtime-dashboard`, `11-composer-widget-renderers`, `14-composer-components`,
`24-composer-flow-headless-authoring` → `families/apps/visual-composer/specs/`
(the two `00-` files collide on number — renumber the design-principles to `00a`/`01`-adjacent at convert time, Haven D6-style)

### families/apps/headless-monitor (app.monitor.*)
`15-composer-device-monitor` ⚠confirm(monitor vs composer), `28-composeux-headless-monitor` → `families/apps/headless-monitor/specs/`

### families/services (svc.*)
`37-deliverable-sweep` → `families/services/specs/`
(FactLattice / bus_storage / document_store / MAGMA specs are not yet in this pool — leave placeholders)

### decisions (per-project split — DEFERRED, needs care)
The monolithic `decisions/decision-graph-composeux-architect.lts` (972 lines, ~130 nodes spanning every
project) is the hard part of the per-project split — each node must be routed to its project's `decisions/`
by domain. This is a SEPARATE dispatch (a domain-routing pass), NOT in the first slice. For now, COPY the whole
graph to `platform/decision-graph-composeux-architect.lts` as a reference snapshot; the split comes later.

### NOT specs (leave in place, do not migrate)
`roadmap/`, `ux/`, `DESIGN-command-dispatcher.md`, `PRIMER-smd-navigator.md` — app assets/docs, not corpus.

## Acceptance for the first slice
Every mapped file present under `ComposePlatform/` (copied, sources still live + unchanged); the `.smd`→`.lts`
+ `.md`→`.lts` conversions are valid LTS (lint-clean); `platform/00-foundation` + `10-glossary` +
`99-conventions` authored; a MAGMA build over ≥2 of the new corpora succeeds and a domain-filtered query
returns only that corpus's nodes. Sources under `ux_composer/specs/` byte-unchanged (prove with a hash check).
