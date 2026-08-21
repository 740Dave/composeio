# Composer / DRADIS gap analysis

**Question:** can a human build the BSG DRADIS widget using the current build of the
M4 Composer (`python3 -m composeio.composer`)? Read-only investigation; no code touched.

**Verdict up front:** **No** — not the real DRADIS. A human can build a crude *toy*
version (a rotating needle over two static rings, the same shape as the repo's own
`dradis.compose.yaml` demo) almost entirely by hand-editing the Source tab, with two of
its four widgets absent from the drag-and-drop palette. The thing David actually means by
"DRADIS" — the flagship Qualia scope with logarithmic-range contacts, per-contact icons,
derived motion-heading arrows, own-ship heading, alert-blink, and a proper sweep beam — is
a **hand-written, compiled C++ widget** (`DradisContactsWidget`, ~610 lines +
`SweepWidget`) that has **no Python-engine implementation at all**. It cannot be built,
previewed, or even represented in the Composer today; the gap is not "clunky UI," it is
"the capability doesn't exist in the tool's target backend."

---

## Step 1 — what DRADIS actually is

Four real artifacts exist under this name, at increasing fidelity. All were read in full.

### 1a. The render-engine toy demo — `python/composeio/render/examples/dradis.compose.yaml`
A `pixel_stack` with two `circle` rings (outer/inner, static) and one `rect` "needle"
carrying a `behaviors: [{when: {namespace: dradis/scanning, is: on}, do: {behavior:
rotate, target: rotation, deg_per_s: $sweep_rate}}]`. This is the *entire* mechanism: a
generic `rotate` behavior gated by a binary namespace. No contacts, no icons, no HUD.

### 1b. The Lottie demo — `python/composeio/render/examples/dradis_lottie.compose.yaml`
A `lottie` widget whose `autoplay` / `speed` / `play_segments` / `go_to_and_stop`
attributes are each bound to a namespace (`dradis/scanning`, `dradis/speed`,
`dradis/segment`, `dradis/hold`), plus a `led` (`scan_led`) and several `label`s
mirroring namespace values. Demonstrates the bindable-Lottie-transport pattern, not the
semantic contacts model.

### 1c. The CYD baked-sprite config — `cyd/configs/cyd_dradis.compose.yaml`
A `pixel_stack` of `label` + `sprite` (a pre-baked 8-frame CI16 animation played via
`draw_image`, `bus://assets/animations/dradis-cyd/frame_{i}.ci16`) + three `led`s. This is
the ESP32-C3/low-tier fallback: the "same" Lottie asset baked to a sprite sheet offline
(`scripts/bake_lottie_sprite.py`) because that tier can't evaluate Lottie live.

### 1d. The flagship — `DradisContactsWidget` (`cyd/include/widgets/dradis_contacts.h`,
`cyd/src/dradis_contacts.cpp`, ~610 lines), instantiated as `type: dradis_contacts` in the
firmware's own scene YAML dialect (`compose_scene.cpp:750-773`), Qualia/framebuffer tier
only (`#ifdef RENDER_TIER_TRANSFORMS`). This is "DRADIS" in the sense David means it. Its
requirements, all read directly from the code:

- **Data model**: `dradis/contacts` — a bus namespace carrying a **JSON array** of
  `{id, type, bearing, range, allegiance, size?, label?}`; `dradis/bodies` — a second JSON
  array of stellar terrain `{type, bearing, range_ls, size?}`; `demo/alert` (binary);
  `own_heading` (float, degrees). Contract doc `docs/dradis/contacts-contract.md` is
  *referenced repeatedly in the code but does not exist in this repo* — it was either
  never committed or lives elsewhere; the code comments are the only surviving normative
  source.
- **Logarithmic polar projection**: `radius_px = clamp(2R/3 + (R/3)·log10(range_ls)/log10(60), 0, R)`,
  then `x = cx + r·sin(θ), y = cy - r·cos(θ)` (`dradis_contacts.cpp:196-203, 295-298`).
  Three range rings at 1/60 LS, 1 LS, 1 LM, log-anchored.
- **Per-type icon fetch/cache**: one HTTP fetch (`bus://assets/icons/dradis/<type>.bmp` or
  `.../stellar/<type>.bmp`) per distinct `type` string seen, decoded once via the Canvas
  contract, cached by type, freed on scene teardown (`icon_warm`, `free_icons`).
- **Derived (not published) motion heading**: the widget tracks each contact by stable
  `id` across updates, diffs its own previously-*projected* screen position, and derives
  `heading = atan2(dy, dx)` locally, smoothed by an EMA — explicitly a design decision
  (`docs/dusk-gifts/registration-protocol.md` Problem 1b: "heading is DERIVED at the leaf;
  identity is PUBLISHED from the engine"). Renders a rotated directional-indicator sprite
  behind the contact icon, only while in motion above an epsilon.
- **Own-ship heading arrow**: same indicator asset, drawn at scope center rotated by a
  bus-published `own_heading` (can't be derived — own-ship never moves on screen).
- **Alert blink**: hostile contacts blink at ~2 Hz, gated by `demo/alert` truthy — state on
  the bus, temporal behavior owned entirely by the widget (Problem 4 in the registration
  doc).
- **HUD tally**: "CONTACTS n THREATS h" derived from the array, never a separate namespace.
- **Missing-icon fallback**: an allegiance-tinted dot (hostile red / friendly green /
  neutral amber) when an icon fails to decode.
- **Multi-line label captions** per contact, optional, `\n`-split.
- **A separate `sweep` widget** for the rotating beam, coordinated through a **3-buffer
  compositing regime** (`SweepRenderPhase::GridOnly` / `BeamOnly` / `Full`) so the static
  rings bake into a background snapshot once and only the beam + contacts redraw
  per-frame — a real-time performance discipline, not just "a rotating shape."
- Driven end-to-end by `python/scripts/dradis_scenario.py`, which publishes the contract's
  exact JSON shape over MQTT (idle blips → closing raider/basestar attack → alert trip).

---

## Step 2 — what the Composer can actually do

Read: `python/composeio/composer/{app,core,catalog,chrome,bindpanel,document,discovery,
deploy,flowpanel}.py`, and `python/composeio/render/engine/{parser,bindings,behaviors,
dsl,layout}.py` (the M4 Composer literally renders itself through this same engine —
`chrome.py`'s docstring calls this "the dogfooding gate of [CXC-RM-M4]").

**Add-a-widget (point-and-click), `catalog.py`:** the *only* types offered in the
Widgets-tab palette are `button, image, lottie, svg, media, mermaid, callout, gauge,
relay, label, rect, checkbox, text_input, coordinate, bus_monitor, dialog, logic`
(`catalog.py:32-90`). This list is what `CatalogPanel` in `chrome.py` renders.

**What the underlying render engine (`parser.py: build_leaf`) can actually parse and
draw**, a materially larger set including two DRADIS needs that never made it into the
palette: `circle` (`parser.py:561-566`) and `led` (`parser.py:189-192`) both have full
Python widget implementations (`CircleWidget`, `LEDWidget`) but **no catalog entry** —
they're only reachable by hand-typing YAML in the Source tab, not by dragging from the
Widgets tab.

**What the render engine cannot parse at all:** `sprite`, `sweep`, `dradis_contacts` do
not exist anywhere in `python/composeio/render/widgets/__init__.py` or `parser.py`. Any
unrecognized `type:` becomes a `PlaceholderWidget` (`parser.py:670,962`) that draws a gray
box with the literal type-name string as its only content (`placeholder.py:66`).

**Bindings (Bindings tab, `bindpanel.py`):** a real, well-built point-and-click surface.
It supports:
- attribute bindings on `BINDABLE_ATTRS_BY_TYPE` (`bindings.py:239-291`) — e.g. `image.src`
  IS bindable to a namespace, `label.text`/`color` are bindable, universal
  `visible/opacity/rotation/scale` on everything.
- **behaviors**, engine-owned and temporal: `blink, pulse, ramp, ramp_color, rotate,
  animate, transition` (`behaviors.py:50-51`), each with a trigger (`is / is_exact /
  truthy / in / always`) and a parameter form rendered from `BEHAVIOR_PARAM_SCHEMA`. This
  is exactly the mechanism the toy `dradis.compose.yaml` needle-sweep uses, and it *is*
  reachable through the GUI (`bindpanel.py:1056-1130`).
- namespace declaration with an arbitrary `state_engine` string, including `"json"`
  (`document.py:41`, default) — so declaring `dradis/contacts` as a JSON-array namespace
  is fully supported.

**What the Bindings tab explicitly refuses:** any binding value or behavior parameter
that "looks like an expression" is rejected inline (`bindpanel.py:662-666, 703-706,
737-740`) and instead **logged as a DSL-candidate "need"** to
`~/.composeio/dsl_candidates.jsonl` (`core.py: log_dsl_candidate`, `[CXC-BE-C8]`) — i.e.
the tool *knows* it can't do this and records the ask rather than doing it.

**The expression DSL that binding values would need (`engine/dsl.py`):** exists as a
sandboxed reference interpreter, but its **entire closed function whitelist** is `min,
max, abs, clamp, round, floor, ceil` (`dsl.py:29-31`) — **no `sin`, `cos`, `atan2`, or
`log10`**. Even if the Bindings-tab rejection above were lifted, the DSL as specified
cannot express DRADIS's `x = cx + r·sin(θ)` polar projection.

**No repeater / list-binding primitive anywhere.** Widgets are individually placed layers
with fixed `(x, y, w, h)`; nothing in `parser.py`/`layout.py` iterates a JSON-array
namespace value into N child widgets. Binding `image.src` to a namespace works for
*one* image; there is no construct for "one entry per element of `dradis/contacts`."

**Preview parity:** the Composer's own canvas (`WorkshopWidget` in `chrome.py`) renders
through the identical `parser.build_node`/`build_leaf` path used everywhere else. This
means: (a) anything the palette is missing but the engine supports (`circle`, `led`) *can*
be hand-typed in Source and will preview correctly; (b) anything the engine doesn't
implement at all (`sprite`, `sweep`, `dradis_contacts`) will preview as a placeholder box
in the Composer even though the exact same YAML, deployed to real Qualia firmware,
renders the real widget — the tool is *silently wrong* about what the final artifact
looks like, not merely limited.

**MCP surface:** `composeio_add_widget`'s `widget_type` and `composeio_add_namespace`'s
`state_engine` are untyped strings, not enums (confirmed against the tool schemas) — so
nothing in the MCP contract itself blocks asking for `dradis_contacts`; the block is
entirely in the renderer, same as the GUI.

---

## Step 3 — the gap table

| # | DRADIS requirement | Status | Detail |
|---|---|---|---|
| 1 | Declare `dradis/contacts`/`dradis/bodies` as JSON-array namespaces | **Supported** | Namespace tab, `state_engine: json` (default), point-and-click. |
| 2 | Static range rings (`circle`) | **Gap — workaround** | `CircleWidget` fully implemented but absent from `catalog.py`'s palette; must hand-type `type: circle` in the Source tab. |
| 3 | Contact/alert LED indicators (`led`) | **Gap — workaround** | Same story: `LEDWidget` works, no catalog entry, Source-tab-only. |
| 4 | Simple rotating "sweep" needle (crude approximation) | **Supported** | Generic `rotate` behavior on any widget, GUI-authorable via Bindings tab exactly as the repo's own `dradis.compose.yaml` demo does it. |
| 5 | Real radar **beam** with 3-buffer compositing (`sweep` widget, `SweepRenderPhase`) | **Gap — blocked** | No Python/render-engine equivalent exists; this is C++-only performance machinery, not representable as engine widgets/behaviors at all. |
| 6 | Per-contact icon fetch/cache keyed by `type` from `bus://assets/icons/dradis/<type>.bmp` | **Gap — blocked** | `image.src` binding exists for exactly *one* static image; nothing dynamically resolves a per-array-item icon URI, still less caches N of them. |
| 7 | Render N contacts from **one** JSON-array namespace, each independently positioned | **Gap — blocked** | No repeater / list-binding primitive anywhere in `parser.py`/`layout.py`. The engine's whole model is individually placed, fixed-count layers. |
| 8 | Logarithmic bearing/range → screen (x, y) projection | **Gap — blocked** | Binding-tab expressions are explicitly rejected and logged as an unmet need (`bindpanel.py`); the DSL that *would* carry this math has no `sin/cos/log10/atan2` in its whitelist (`dsl.py:29-31`) even in principle. |
| 9 | Motion heading **derived** from tracked per-contact position deltas across updates | **Gap — blocked** | No stateful, per-item, cross-update derivation exists in `engine/behaviors.py` (blink/pulse/ramp/ramp_color/rotate/animate/transition are all single-widget, not identity-tracked-across-array-updates). This is bespoke widget code by design (Problem 1b). |
| 10 | Own-ship heading arrow bound to `own_heading` | **Gap — workaround-ish, moot** | The *binding* mechanics (rotate an image by a namespace value) are supported in isolation, but only meaningful once contacts/positions exist, which don't (#6, #7, #9). |
| 11 | Hostile 2 Hz alert blink gated by `demo/alert` | **Supported (per-widget)** | `blink` behavior + `is truthy` trigger is exactly this pattern and is GUI-authorable — but only for a single pre-placed widget, not "every hostile contact in the array" (#7 blocks the array part). |
| 12 | HUD tally text derived from contact count/threat count | **Gap — blocked** | `label.text` can bind to a namespace's raw value, not to a derived aggregate of another namespace's array contents — no derivation layer between namespaces. |
| 13 | Missing-icon colored-dot fallback per allegiance | **Gap — blocked** | Conditional-render-by-data-value inside one visual slot; no such conditional composition primitive exists. |
| 14 | See what you built, before flashing hardware | **Gap — blocked (silent)** | Composer's own preview uses the same engine that lacks #5/#6/#7/#9 — a hand-typed `dradis_contacts` scene previews as a gray placeholder box, not a warning that the tool can't represent it. |
| 15 | CYD baked-sprite tier (`sprite`, `draw_image` frame sequence) | **Gap — blocked** | No `SpriteWidget` in the Python engine at all; this tier is authored and validated purely by hand + on-device, outside the Composer entirely. |

**Tally:** 3 supported outright, 2 workaround (hand-edit YAML for widgets the engine
already has), **10 blocked** — and the blocked set is exactly the part that makes DRADIS
*DRADIS* rather than a rotating needle: the array-of-contacts data model, the log-polar
math, per-item icon resolution, and derived motion state.

---

## Verdict

**A human cannot build DRADIS — the thing the name refers to — in the Composer today.**
They can build the toy needle-and-rings demo that already ships as an example, and they
can do maybe 70% of *that* through point-and-click (the last 30% — the two ring/LED
primitives — needs a Source-tab hand-edit, which the tool at least previews correctly).
Past that toy, every requirement that actually constitutes "DRADIS" — a variable-count
JSON-array data model rendered as N independently-positioned, independently-animated,
identity-tracked, icon-resolved contacts, on a logarithmic polar scope, with real-time
sweep compositing — has **zero representation** in the render engine the Composer builds
on, and the tool doesn't even fail loudly about it: it silently previews a labeled gray
box. Nobody flashing real Qualia firmware from a Composer-authored file would find out
their radar scope is broken until they looked at the physical glass.

This is architecturally honest, not a bug: `dradis_contacts` is explicitly a compiled,
framebuffer-tier-only C++ widget (`#ifdef RENDER_TIER_TRANSFORMS`), the kind of "cheapest
node that can fully own the behavior" the project's own doctrine (registration-protocol.md,
Problem 2b) says belongs at the backend, not forked per-config. The gap isn't that the
Composer does DRADIS badly — it's that DRADIS's four hardest primitives (repeater/array
rendering, derived cross-update state, polar/trig math, dynamic per-item asset
resolution) don't exist as *general* engine capabilities yet, so there was never anything
generic for the Composer to expose. The C++ widget proves the backend CAN do it; the front
end (engine + Composer) never grew the vocabulary to describe it.

---

## Highest-leverage Composer/engine improvements this implies

Ranked by how much of the blocked list each one would clear, and consistent with the
project's own "generalize the backend, don't fork the widget" doctrine (Problem 2b):

1. **A repeater/list-binding primitive**: bind a container to a JSON-array namespace and a
   per-item template (position expression + icon-uri expression + text). This alone would
   unblock gaps #6, #7, #12, #13 and turn "one bespoke C++ widget" into "one general
   render-engine capability that also benefits any other future array-shaped bus data"
   (fleet dashboards, sensor grids, etc.) — the highest-leverage single item on this list.
2. **Trig/log functions in the expression DSL** (`sin, cos, atan2, log10` at minimum) and
   **actually wiring binding-value expressions into the Bindings-tab UI** instead of
   detecting-and-rejecting them. The DSL infrastructure and the candidate-logging
   discipline (`[CXC-BE-C8]`) are already in place — this is closing a loop that's already
   instrumented, not building from zero.
3. **Cross-update derived state as a first-class behavior** (something like a `track`
   behavior: "derive `heading` from this item's position delta since last update, keyed by
   `id`") — generalizes the exact mechanism `DradisContactsWidget` hand-rolled, and is
   reusable for anything else that needs motion-implied heading from tracked position.
4. **Close the palette/engine gap for free wins**: add `circle` and `led` to `catalog.py`
   — zero new engine work, purely a missed catalog entry, and it removes two of the
   Source-tab-only workarounds immediately.
5. **A loud "this widget type has no preview implementation" banner** in the Composer's
   canvas (instead of a silent generic placeholder box) whenever `PlaceholderWidget` is
   used — cheap, and turns "you won't find out until the physical glass" into an honest,
   in-tool signal, consistent with the project's own "machine-green ≠ glass-green"
   principle (registration-protocol.md, Problem 5).

---

## Files read (grounding)

- `python/composeio/render/examples/dradis.compose.yaml`, `dradis_lottie.compose.yaml`
- `cyd/configs/cyd_dradis.compose.yaml`
- `cyd/include/widgets/dradis_contacts.h`, `cyd/src/dradis_contacts.cpp`
- `cyd/src/compose_scene.cpp` (lines ~280-335, ~740-780 — `dradis_contacts` wiring)
- `cyd/src/reactive_apply.h` (lines ~55-133 — `RAttr::ContactsSource/…/OwnHeading`)
- `python/scripts/dradis_scenario.py`
- `docs/dusk-gifts/registration-protocol.md` (Problem 1b, 2b, 4)
- `reviews/bsg_first_build_review.md` (adjacent BSG game-engine gap report, context only)
- `python/composeio/composer/{catalog,core,chrome,bindpanel,document,discovery}.py`
- `python/composeio/render/engine/{parser,bindings,behaviors,dsl,layout}.py`
- `python/composeio/render/widgets/__init__.py`, `placeholder.py`
- `mcp__composeio__composeio_add_widget` / `composeio_create_binding` /
  `composeio_set_property` / `composeio_add_namespace` tool schemas (MCP composition
  surface)

**Not found in the repo** (referenced but absent — noted for the record, not assumed):
`docs/dradis/contacts-contract.md` (the normative contract the C++ code cites
repeatedly); a top-level `bsg/` game-engine directory (referenced in
`registration-protocol.md` and `reviews/bsg_first_build_review.md`).
