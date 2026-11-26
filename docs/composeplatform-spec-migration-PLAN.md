# ComposePlatform Spec Migration — PLAN (understand + organize)

Status: **PLAN only.** No spec content moved, ported, or rewritten. Maps the existing
corpus into the decided taxonomy (`[DEC-COMPOSEPLATFORM-REFRAME]` /
`ComposePlatform/platform/00-composeplatform-foundation.lts`). Codebase/role/prefix
renames are explicitly OUT of scope (a separate migration David sequences).

## The decided taxonomy (target nodes)
```
ComposePlatform (TLD)
├─ Platform            → ComposePlatform/platform/
├─ LAYERS
│   ├─ ComposeIO       → ComposePlatform/layers/composeio/specs/
│   └─ ComposeUX       → ComposePlatform/layers/composeux/specs/
└─ PROJECT FAMILIES
    ├─ ComposeApps     → ComposePlatform/families/apps/{visual-composer,intelligence-console,headless-monitor,…}/specs/
    ├─ ComposeBus Svcs → ComposePlatform/families/services/specs/
    └─ Reactive Widgets→ ComposePlatform/families/widgets/specs/   ← currently EMPTY
```

## Corpus census
- `ux_composer/specs/*.lts` — **47** flat LTS (00–45, two "00" files).
- `spec/*.smd` — **22** SMD (original ComposeIO / Haven-bridge / service specs), not yet LTS.
- `ComposePlatform/**` — **21** app/service/layer specs + 7 platform docs already migrated,
  plus 2 CP-native visual-composer specs with no flat origin (`ux-01-composer-binding-editor`,
  `ux-02-composer-flow-mode` — leave as-is, no action).

## Headline
- **44 / 47** flat LTS are already migrated to ComposePlatform. Of those, **40 are
  byte-identical** duplicates (delete the flat leftover) and **4 have DIVERGED** (flat edited
  after the 2026-07-22 snapshot AND the CP copies were independently touched — reconcile, do
  not blind-overwrite).
- **3** flat LTS are new and unmigrated (43, 44, 45) → move.
- **22** SMD → port to LTS + place.
- **2 genuine placement forks** need David's ruling (Haven corpus; intelligence-mcp-server).
  Everything else is a dispositive call, made below.

---

## 1. MAPPING TABLE — flat `ux_composer/specs/*.lts` (47)

For the 44 already-migrated files the "target" is where ComposePlatform ALREADY put them
(the decided placement — confirmed correct on review). Port-status column: `MIG`=already
migrated (identical dup), `DIV`=duplicate-diverged, `NEW`=flat-only, move.

| file | target group | why | status |
|---|---|---|---|
| 00-composer-overview | apps/visual-composer | Composer app overview | MIG |
| 00-composer-design-principles | apps/visual-composer (`00a`) | Composer design principles; renamed 00a on migrate | MIG |
| 01-composer-architecture | apps/visual-composer | Composer app architecture | MIG |
| 02-composer-internal-bus | layers/composeio | in-process bus mechanics = ComposeIO layer | MIG |
| 03-composer-modules | apps/visual-composer | Composer module layout | MIG |
| 04-composer-serialization | apps/visual-composer | `.compose.yaml` save/load in Composer | MIG |
| 05-composer-preview-system | apps/visual-composer | Composer preview | MIG |
| 06-composer-intelligence-api | apps/visual-composer | Composer MCP driving surface | MIG |
| 07-composer-agent-build-plan | apps/visual-composer | Composer build plan | MIG |
| 08-composer-tab-container | apps/visual-composer | Tab widget in Composer | MIG |
| 09-composer-reactive-window | apps/visual-composer | Composer reactive window | MIG |
| 10-composer-runtime-dashboard | apps/visual-composer | Composer runtime dashboard | MIG |
| 11-composer-widget-renderers | apps/visual-composer | Composer renderer set | MIG |
| 12-composeux-render-engine | layers/composeux | renderer-agnostic render engine = ComposeUX layer | **DIV** |
| 13-mcp-action-binding | layers/composeio | bus action binding = ComposeIO layer | MIG |
| 14-composer-components | apps/visual-composer | Composer component model | MIG |
| 15-composer-device-monitor | apps/headless-monitor | device monitor app | MIG |
| 16-composeux-bindings-behaviors | layers/composeux | binding/behavior contract | MIG |
| 17-composeux-lottie | layers/composeux | Lottie rendering | MIG |
| 18-composeux-svg | layers/composeux | SVG rendering | MIG |
| 19-composeux-gestures | layers/composeux | gesture contract | MIG |
| 20-composeux-headless-widgets | layers/composeux | headless widget contract | MIG |
| 21-composebus-pattern-subscription | layers/composeio | pattern subscription = bus mechanic | MIG |
| 22-composeux-expression-dsl | layers/composeux | expression DSL | MIG |
| 23-composeux-widget-primitives | layers/composeux | widget primitive set | MIG |
| 24-composer-flow-headless-authoring | apps/visual-composer | flow authoring in Composer | MIG |
| 25-composeux-audio | layers/composeux | audio contract | MIG |
| 26-composeux-nfc | layers/composeux | NFC contract | MIG |
| 27-composeux-peripheral-hal | layers/composeux | peripheral HAL | MIG |
| 28-composeux-headless-monitor | apps/headless-monitor | headless monitor app | MIG |
| 29-composeux-embedded-reactive | layers/composeux | embedded reactive rendering | MIG |
| 30-composeux-carthing-surface | layers/composeux | Car-Thing surface (see note A) | MIG |
| 31-composeux-board-bringup | layers/composeux | board bring-up contract | MIG |
| 32-composeux-hal-declaration | layers/composeux | HAL declaration | MIG |
| 33-composeux-ha-bridge | layers/composeux | Home-Assistant bridge contract | MIG |
| 34-composeux-intelligence-console | apps/intelligence-console | the Console app | **DIV** |
| 35-intelligence-console-decisions | apps/intelligence-console | Console decision log | MIG |
| 36-console-visual-fidelity | layers/composeux | pixel-fidelity contract (see note A) | MIG |
| 37-deliverable-sweep | families/services | deliverable daemon | MIG |
| 38-agent-team-host | apps/intelligence-console | Console agent-team host | MIG |
| 39-session-retrospective | apps/intelligence-console | Console retrospective | **DIV** |
| 40-decisions-tab | apps/intelligence-console | Console Decisions tab | MIG |
| 41-intelligence-host-spawner | apps/intelligence-console | Console host spawner | **DIV** |
| 42-idle-tracking | apps/intelligence-console | Console idle tracking | MIG |
| 43-defect-tracking-store | families/services | defect-investigation store (SQLite+doc); Console is the consumer | **NEW** |
| 44-lts-engine-consolidation | families/services | lts-engine = FactLattice ComposeBus Service | **NEW** |
| 45-session-bootstrap-read | apps/intelligence-console | session-start strategic read = Console | **NEW** |

**Note A (low-stakes, glance-worthy, NOT a fork ask):** CP already filed
`30-composeux-carthing-surface` and `36-console-visual-fidelity` in `layers/composeux`.
Both are defensibly a *layer* (Car-Thing = a reusable embedded surface; visual-fidelity = a
platform-wide rendering contract) rather than `families/widgets` / `apps/intelligence-console`
respectively. The layer placement holds; noting only so it gets a glance during the move.

## 2. MAPPING TABLE — `spec/*.smd` (22) → LTS port + placement

| file | target group | why |
|---|---|---|
| 00-composeio-design-principles | layers/composeio | ComposeIO-layer principles (reconcile overlap w/ `platform/design-excellence`) |
| 01-composeio-bus-protocol | layers/composeio | THE bus protocol — ComposeIO core |
| 01-amendment-bus-extensions | layers/composeio | amends bus protocol |
| 01-amendment-capture-replay | layers/composeio | bus capture/replay (`capture.py`, `transport_replay.py`) |
| 01-amendment-critical-ack | layers/composeio | app-level ack on the bus |
| 02-composeio-config-schema | layers/composeio | ComposeApp/bus config schema (`.compose.yaml` authoring facet → note ComposeUX) |
| 02-amendment-app-config | layers/composeio | amends config schema |
| 03-composeio-module-roles | layers/composeio | module role vocabulary |
| 04-haven-bridge | **FORK 1** | Haven↔bus bridge (see forks) |
| 05-haven-scheduler | **FORK 1** | Haven scheduler (see forks) |
| 06-news-service | **FORK 1** | news service (Haven-adjacent; see forks) |
| 07-notification-bridge | **FORK 1** | notification bridge (Haven-adjacent; see forks) |
| 08-composeio-app-framework | layers/composeio | `ComposeApp` dual-bus framework — ComposeIO core |
| 09-composeio-intelligence-mcp-server | **FORK 2** | MCP↔bus server (see forks) |
| 10-composeio-node-red-integration | families/services | Node-RED integration daemon |
| 11-smd-mkdocs | families/services | SMD→MkDocs doc-site generator (FactLattice predecessor; likely superseded) |
| 12-haven-terminus-composeapp | families/apps (Haven Terminus) | explicitly a ComposeApp (see FORK 1 for the family's home) |
| 13-service-hub-v2 | families/services | Service Hub daemon (`services/service_hub/hub.py`) |
| 14-board-manager | families/apps | fleet/board management app (provisionIO-adjacent); distinct from the `31` bring-up contract |
| 15-composeux-embedded-display | layers/composeux | embedded display contract (peer of `29`) |
| 20-oob-management-plane | families/services | out-of-band management plane = infra service |
| 99-composeio-conventions | platform | corpus conventions — **superseded by** `platform/99-composeplatform-conventions.lts`; port as historical/merge |

## 3. PORT-STATUS SUMMARY

- **already-migrated (identical dup — delete flat leftover):** 40 flat LTS (all `MIG` rows).
- **duplicate-diverged (reconcile — flat is likely authoritative but CP was independently
  edited; MERGE, do not overwrite):** `12-composeux-render-engine`,
  `34-composeux-intelligence-console`, `39-session-retrospective`,
  `41-intelligence-host-spawner`. Evidence below.
- **flat-LTS-needs-move:** `43-defect-tracking-store`, `44-lts-engine-consolidation`,
  `45-session-bootstrap-read`.
- **SMD-needs-port+move:** all 22 `spec/*.smd`.

### Divergence evidence (flat vs CP copy)
| spec | flat mtime / lines | CP mtime / lines | diff-lines | authoritative |
|---|---|---|---|---|
| 12-composeux-render-engine | 07-23 14:03 / 1005 | 07-22 16:22 / 964 | 41 | flat (newer, +41 lines) |
| 34-composeux-intelligence-console | 07-23 13:58 / 244 | 07-22 22:56 / 214 | 30 | flat (newer, +30) — CP also touched 22:56 |
| 39-session-retrospective | 07-23 12:51 / 304 | 07-22 22:53 / 248 | 56 | flat (newer, +56) — CP also touched 22:53 |
| 41-intelligence-host-spawner | 07-23 12:40 / 139 | 07-22 16:22 / 80 | 59 | flat (newer, +59 lines) |

Recommended rule: for these 4, treat **flat as base** (newest, most content), diff the CP
copy for any CP-only edits (34/39 CP mtimes post-date the bulk migration), fold those in, then
replace the CP copy and delete the flat file.

## 4. GENUINE PLACEMENT FORKS (need David's ruling — both branches defensible)

### FORK 1 — The Haven corpus (SMD 04, 05, 06, 07, 12): does it live under ComposePlatform, and if so App vs Service?
Haven is David's adjacent product. Two defensible readings:
- **(a) ComposeApps** — Haven Terminus (`12`) is explicitly a ComposeApp; treat the bridge/
  scheduler/news/notification specs as internals of that app family (`families/apps/haven-*`).
- **(b) ComposeBus Services** — the bridge/scheduler/news/notification are reusable bus
  daemons (they bridge external systems to the bus), so they belong in `families/services`,
  with only Terminus as an app.
- **(c) not-in-corpus** — Haven is a downstream *product* that merely consumes ComposePlatform;
  its specs live in Haven's own corpus, not here at all.
The reframe lists provisionIO under ComposeApps but says nothing about Haven. Ruling needed:
(a) one Haven app-family here, (b) split app/services here, or (c) exclude from this corpus.
My lean: **(b)** — Terminus → `families/apps`, the four bridges → `families/services`.

### FORK 2 — `09-composeio-intelligence-mcp-server`: ComposeIO layer vs ComposeBus Service
- **layers/composeio** — Intelligence-as-peer is a *defining property of the ComposeIO layer*
  ([PLT-INTELLIGENCE-PEER]); the MCP↔bus surface is how that property is realized, so it is
  substrate, not an app.
- **families/services** — it ships as a deployable daemon (`services/intelligence_mcp/`,
  Dockerfile, docker-compose), which is exactly the shape of a ComposeBus Service.
Both on-aesthetic. My lean: **layers/composeio** (the *contract* is substrate; the *daemon
packaging* is an artifact of that layer, not a separate family), but this is a real call.

---

## 5. GAP INVENTORY — code ahead of spec (prioritized, `file:symbol`)

Highest-value first: load-bearing subsystems with no (or thin) spec.

1. **Reactive Widgets family — ZERO specs.** `ComposePlatform/families/widgets/specs/` is
   empty; the whole family (DRADIS/Qualia, `rack_triple_gauge`, `button_matrix`, WLED) is
   unspecified. Code/evidence: `cyd/`, `firmware/`, `ux_composer` widget renderers,
   `docs/composer-dradis-gap-analysis.md` (analysis exists, no spec). *Biggest structural gap.*
2. **MAGMA aggregator.** `python/composeio/magma_agg/` — powers the decision-graph queries,
   the Decisions tab (`40`), and the retrospective. Named in the taxonomy as a ComposeBus
   Service; **no spec**. `python/composeio/magma_agg/` (module).
3. **ComposeBus channel bridge.** `compose_projects/intelligence/channel/channel.mjs` — the live MCP↔MQTT↔GUI
   Intelligence-as-peer transport (the credo made real). **No spec**; it is the loop every
   session depends on.
4. **decision_ops write-path.** `python/composeio/decision_ops/mcp_server.py`,
   `decision_ops/lts_refresh.py`, `decision_ops/core.py` — the decision-WRITING MCP + MAGMA
   refresh-on-write. **No dedicated spec** (`40` is the UX, not the write service).
5. **bus_storage service.** `services/bus_storage/server.py` (`bus://assets`, `:8420`).
   Referenced platform-wide; `document_store` has its own spec dir, bus_storage does **not**.
6. **coordination_relay service.** `services/coordination_relay/server.py` — multi-console /
   multi-agent coordination. **No spec**; directly implicated in the open instance-lease /
   `[INSIGHT-SELF-IS-NOT-YET-A-PEER]` wart.
7. **Console sensors.** `python/composeio/self_sensor/sensor.py`,
   `python/composeio/session_sweep/sensor.py`, `python/composeio/git_sensor/sensor.py` — feed
   `self/*`, session state, and git state into the Console. **No specs**; `self_sensor` is the
   code under the self-is-not-a-peer wart.
8. **Render-engine internals.** `python/composeio/render/engine/{damage,focus,layout,runtime,
   parser}.py` — spec `12` is overview/roadmap and `16`/`22`/`23` cover bindings/DSL/
   primitives, but damage-tracking, focus, the layout engine, and runtime lifecycle are
   unspecified.
9. **decision_extractor.** `python/composeio/decision_extractor/extractor.py` — mines
   decisions from transcripts (feeds MAGMA + the Decisions tab). **No spec.**
10. **Intelligence meta-object schema.** `compose_projects/intelligence/meta/architect.meta.json` +
    `compose_projects/intelligence/publisher/config_persister.py` — the `self` meta-object (identity,
    `compact_threshold`, config). **No schema spec**; central to the self-as-peer completion.

Lower tier (note, not top-10): `apps/{fleet-hud,composeux-shell,minnow-bringup}` as ComposeApps
(minnow-bringup partly covered by `31`/provisionIO); `services/wake_word/detector.py`;
`services/can_mqtt_bridge/bridge.py`; `python/composeio/lts_lint/` (largely covered by `44`).

---

## 6. Duplicates / divergences needing a ruling (compact)
- **4 diverged duplicates** (§3) — flat vs CP both edited; merge with flat as base.
- **40 identical duplicates** — safe to delete the flat leftover once CP is confirmed home.
- **`99-composeio-conventions.smd` superseded** by `platform/99-composeplatform-conventions.lts`
  — port as historical or fold, don't create a live rival.
- **`00-composeio-design-principles.smd`** overlaps `platform/design-excellence.lts` — a
  content-dedup task on port, not a placement fork.
- **Notes A** (§1): `30-carthing`, `36-console-visual-fidelity` — CP layer-placement holds;
  glance only.

## 7. Next phase (NOT done here)
Author lint-clean LTS bridges, execute the 4 merges, move the 3 new flats, port the 22 SMD,
delete the 40 identical flat leftovers, seed `families/widgets/specs/`, and close the top-10
gaps in priority order. All reviewed before execution.
