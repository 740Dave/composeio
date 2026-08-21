# Slint vs. ComposeUX / ComposePlatform — Comparative Architecture Analysis

**Author:** composeio-expert (senior control-surface / distributed-systems)
**Date:** 2026-08-04
**Status:** Decision-support. Opinionated. Grounded in the actual codebase (`python/composeio/render/`, `cyd/`) and in primary Slint sources (cited).
**Audience:** David (architect).

---

## Executive Summary

**Recommendation: keep the heart, lift specific ideas, do NOT switch to Slint.**

Slint is an excellent, production-grade *compile-and-run* declarative GUI toolkit — arguably the best embedded-Rust UI on the market. It is also a fundamentally different animal from ComposeUX. Our differentiator is a **runtime decompose/compose model** (`.compose.yaml` parsed to a live widget tree with no build step, mutated one bus command at a time, hot-swappable) over a **bus-native reactive core** and a **narrow-waist `Canvas` contract** that reaches from macOS down to a 7.6 KB-buffer ESP32. Slint's model is **ahead-of-time-compiled `.slint`** with a Slint-expression reactive graph baked into each component. Even Slint's runtime *interpreter* — the one seam that gestures at runtime composition — still runs the full parse + type-check + LLR build on every composition change. That is a compile step wearing a costume. Adopting it means giving back exactly what makes ComposeUX ComposeUX.

- **Direction 1 (layer our uniqueness onto Slint): limited — reject as a foundation.** The interpreter path re-compiles per composition, uses Slint's reactive graph rather than the bus, replaces our `Canvas` contract with Slint's element set, and would force a full rewrite of every Python `Widget`. It does not reach our CYD tier without a GPL/commercial embedded license *and* that rewrite. Coherent only as **interop-at-the-edges** (a Slint window as one bus-fed tactical surface), and only if a concrete need appears.
- **Direction 2 (lift from Slint to accelerate us): high value.** Slint has solved, in production, two things squarely on our roadmap.

**Top 3 lifts (ranked by value):**

1. **The declarative states + transitions + easing model** (`states { ... in/out { animate } }`, `cubic-bezier`, delay/iteration-count). This *is* the tactical-UX animation substrate we have queued in `[CXC-RM-BEHAVIORS]`/`[CXC-RM-GESTURE]`, and it maps almost 1:1 onto our already-deterministic `engine/behaviors.py` + `engine/animation.py`. Highest value, medium effort, no new runtime concept.
2. **The line-by-line software renderer technique for the CYD/ESP32 tier** (two one-line buffers + DMA overlap: render line N+1 while line N is DMA'd out; dirty-line tracking). This directly attacks our single worst measured bottleneck — the CYD band-renderer perf floor (empty scene 56 ms / 17.9 fps; animation currently needs dirty-band rendering we have not built). Lift the *algorithm*, not the GPL code.
3. **The type-erased "property-by-string `Value`" protocol + `cubic-bezier` easing.** Slint's `ComponentInstance::set_property(name, Value)` is a clean, uniform runtime property model that mirrors what our attr-binding dispatch and Composer property editor already reach for. Small, high-leverage formalization; `cubic-bezier` drops straight into our `EASING` table.

**Layering verdict, in one line:** Slint's interpreter is real and impressive, but "runtime `.compose.yaml` → `.slint` → interpreter" reintroduces a per-composition compile, splits us into two reactive systems, and abandons the Canvas narrow-waist that owns our cross-tier reach. The juice is not worth the heart.

---

## 1. The Two Models, Side by Side

| Dimension | **ComposeUX / ComposePlatform** | **Slint** |
|---|---|---|
| **Authoring artifact** | `.compose.yaml` (declarative, data) | `.slint` (declarative, HTML/CSS-like typed markup) |
| **Build model** | **Runtime parse to a live widget tree** (`parser.parse_widget_tree(config)`), *no build step* | **Ahead-of-time compile** to native code per language (Rust, C++); interpreter path for Python/JS |
| **Composition** | **Runtime decompose/compose** — the tree is mutated one bus command at a time (`add_widget`, `move_widget`, `set_property`); scenes hot-swap by re-parsing a new config | **Static component instantiation** — a component's structure is fixed at compile/build; the interpreter can build a *new* component from source but not incrementally mutate a live tree |
| **Reactive model** | **Bus-native pub/sub.** Widgets subscribe namespaces at parse time; `on_bus_state(ns, value)` → `request_redraw()`; "state on the bus, behavior in the widget" | **Compiled property/expression bindings** inside the component; a change to one property re-evaluates dependent expressions. Reactive graph is Slint's, not a message bus |
| **Data flow** | **Data-by-reference** over the bus (`bus://`, `doc://` via `resolve.py`); no frames on the wire | In-process property values + models; data comes from the host language via the binding |
| **Drawing contract** | **Narrow-waist `Canvas` ABC (~30 primitives)** — geometry/style/transform/text/image/surface — that every backend implements; widgets draw *only* against it | Built-in element set + renderer; app code does not draw primitives, it composes elements |
| **Backends** | Skia raster + SDL2 window (macOS/RPi, the workhorse); C++ `ESP32Canvas`/`Qualia`; browser planned (Decision-1 open) | Skia, femtovg (GLES2), **software (CPU)**, Qt; DMA2D/GPU/line-by-line on MCU |
| **Cross-tier reach** | One widget definition renders pixel-identically macOS ↔ RPi (proven, hard pixel gate) and degrades to CYD via the C++ Canvas port | One `.slint` runs Rust-native on desktop *and* bare-metal MCU (`no_std`, <300 KiB RAM) — but as **compiled Rust**, not fetched config |
| **Fleet deployment** | **Fungible firmware**: one binary fetches `bus://assets/displays/<device_id>.compose.yaml` and renders it; a new screen is a new *config*, not a new build | New UI = new compiled firmware image (or an interpreter host that loads `.slint`, which is still a per-file compile) |
| **Actor of change** | **Human and Intelligence are peers** — every builder action is a bus message (MCP → MQTT → GUI) | Developer edits `.slint` + host code, rebuilds; live-preview hot-reload exists but is a dev-loop, not a production actuation model |
| **Inline logic** | **No inline code** in config (`strip_code_fields`) — RCE-safe on a fetch-from-bus fleet, portable to C++/JS; escape hatch is a bus workload | Expressions live in `.slint`; real logic in the host language (Rust/C++/Py/JS) |
| **License** | Ours | GPLv3 / royalty-free (proprietary desktop-mobile-web only) / paid commercial (proprietary **embedded**) |
| **Maturity** | Pre-production render engine (M0–M5 first passes operational; browser + full CYD parity outstanding) | Stable 1.x API; Rust/C++ production; Python/JS **beta** |

The two systems agree on a surprising amount of *philosophy* — declarative UI, a platform-independent rendering seam, "state drives presentation," pixel portability down to the MCU. They disagree on the **one axis that defines our product**: *when* the UI structure is determined. Slint says "at build." We say "at runtime, continuously, over the bus."

Sources: [Slint architecture / backends / licensing](https://slint.dev/), [Slint interpreter & dynamic loading (DeepWiki)](https://deepwiki.com/slint-ui/slint/3.4-interpreter-and-dynamic-loading), [Slint MCU platform API](https://slint.dev/blog/slint-0.3-released-with-platform-api-for-mcu), [Porting Slint to microcontrollers](https://slint.dev/blog/porting-slint-to-microcontrollers), [slint-interpreter crate](https://docs.rs/slint-interpreter).

---

## 2. ComposeUX's Protected Heart — What It Is, and Why Compile-and-Run Cannot Replicate It

The hard constraint governing this analysis: **we do not lose the decompose/compose model or the design flexibility it buys us.** Characterized precisely from the code, the heart is four interlocking pieces:

### 2.1 Runtime decompose/compose — no build step
`parser.parse_widget_tree(config)` (`python/composeio/render/engine/parser.py`) turns an already-loaded `.compose.yaml` dict into a live `Composition` (widget tree + subscriptions + bindings + behaviors) *at runtime*. There is no codegen, no compiler, no artifact between the YAML and the running tree. Composer edits that tree **incrementally**: `add_widget`, `move_widget`, `set_property`, `remove_widget` each arrive as a *peer bus message* (a mouse drag and an MCP publish are indistinguishable). A scene hot-swaps by parsing a different config. **This is the flexibility David is protecting.**

### 2.2 The enumerable registry + project-local widgets (`import is the contract`)
`engine/widget_registry.py` makes the `type:` → widget-class map a single enumerable data structure of `WidgetDescriptor`s (`WIDGET_REGISTRY`, `list_widgets(category)`, an optional `build` construction hook). Crucially, `[CXC-PROJECT-WIDGETS]` lets a scene name project-local widget modules via `widget_modules:`; the parser imports them *before* resolving any `type:`, and each module **self-registers** its widgets at import time (`register(WidgetDescriptor(...))`). "Import IS the contract; the engine never reaches into a project." A project owns its widgets with **zero engine edits**, and an unknown/broken type degrades to a `PlaceholderWidget` (never fatal). This is decompose/compose extended to the *widget catalog itself*.

### 2.3 Bus-native reactive core
Widgets subscribe namespaces at parse time (`comp.subscriptions`); a bus state change calls `on_bus_state(ns, value)`, the widget mutates its own state and calls `request_redraw()`, and the engine damages just that widget's rect and re-composites (`present_partial`). Declarative `[CXC-BIND]` bindings and temporal `behaviors:` (blink/pulse/rotate/ramp/animate) implement "**state on the bus, behavior in the widget**": the bus carries a *semantic* bit (`red_alert = on`), the widget owns the *temporal* presentation. No frames on the wire. An Intelligence drives a Red-Alert screen by flipping one bit. Data is by-reference (`bus://`/`doc://`, `resolve.py`).

### 2.4 The narrow-waist `Canvas` contract
Every backend implements one ~30-primitive `Canvas` ABC (`python/composeio/render/canvas.py`), ported verbatim to C++ (`cyd/include/canvas.h`). Widgets draw *only* against it. This narrow waist is what lets *the same widget code* render pixel-identically on macOS+RPi (proven, hard pixel gate) and degrade gracefully onto a 7.6 KB-band ESP32. **We own the primitive contract**, which is why we can hit a fungible-firmware fleet and a strict cross-platform pixel gate.

### Why compile-and-run cannot replicate this
Slint's model determines UI *structure* at build time. Its reactive graph is compiled *into* each component as resolved expression bindings. To get runtime composition on Slint you must go through its **interpreter** — and here is the exact mismatch:

> The Slint interpreter still runs the **same front-end as codegen**: parse → type-check → Low-Level Representation (LLR) build, producing a new `ComponentDefinition` and `ComponentInstance`. That is a **compile step** — it is precisely how Slint's live-preview hot-reload works (edit `.slint` → recompile the file → swap the instance).

So "runtime composition on Slint" means: on every structural change, **regenerate `.slint` source and rebuild the component**. That is coarse-grained (whole-component rebuild + instance swap), not our fine-grained, incremental, one-bus-command-per-mutation model. And the resulting component's internal reactivity is **Slint expressions, not the bus** — you would bolt bus reactivity onto the property edges and run *two* reactive systems. The narrow-waist Canvas is gone (Slint owns rendering). The Python `Widget.draw()` bodies have no home (Slint widgets are `.slint` elements). Every one of the four heart-pieces is either lost or duplicated. **The heart does not survive the transplant.**

---

## 3. Direction 1 — Layering ComposeUX's Uniqueness onto Slint: Feasibility

**Verdict: limited. Reject Slint as the ComposeUX foundation. Viable only as interop-at-the-edges.**

Three real seams exist. I take each honestly.

### Seam A — The Slint interpreter (`slint-interpreter`, and the Python/JS bindings that ride it)
This is the strongest seam and deserves credit: Slint genuinely loads `.slint` at runtime. `ComponentCompiler::build_from_source` → `ComponentDefinition` → `ComponentInstance`; properties/callbacks are read and set **by string name** through a type-erased `Value` enum. The **Python binding is built entirely on this** (`slint.load_file("app.slint")`, PyO3 bridge) — no codegen. That means our engine's language (Python) *could* host Slint.

Why it still fails as a foundation:
- **It re-compiles per composition.** Every structural edit = regenerate source + `build_from_source` (parse + type-check + LLR). Composer's value proposition is *incremental* mutation as peer bus messages; the interpreter offers *whole-component rebuild*. We would trade a live, addressable widget tree for a "recompile the file" loop. That is the compile step returning, exactly as the hard constraint warns.
- **Two reactive systems.** The component's bindings are Slint expressions. Bus reactivity would be an outer layer poking `set_property(name, Value)` on MQTT arrival. Our clean "subscribe → `on_bus_state` → `request_redraw`" collapses into a bridge that fights Slint's own dependency graph.
- **We lose the Canvas narrow-waist and the widget model.** Slint renders through its own element set and renderer. Our ~30-primitive contract, the pixel gate it enforces, and every Python `Widget` subclass (their `measure()`/`draw()` against `Canvas`) would be discarded and re-authored as `.slint` elements. The `WIDGET_REGISTRY` + `widget_modules:` "import is the contract" plugin model has no analog.
- **It does not reach our CYD tier.** The interpreter is a desktop/host construct; MCU Slint is *compiled Rust* firmware. Fungible "fetch a config and render" does not exist on Slint's embedded path. And proprietary embedded Slint is GPLv3 (viral) or a paid commercial license.

### Seam B — Transpile `.compose.yaml` → `.slint` at runtime
Superficially attractive ("keep our authoring format, emit Slint"). Incoherent in practice:
- It **is** a compile step, per composition. Every hot edit re-emits `.slint` and rebuilds — the thing we must not do.
- It is lossy in both directions. Our `type:` tokens resolve through a runtime registry incl. project-local self-registering modules; `.slint` has a fixed, statically-typed element vocabulary. Bus subscriptions, `bus://`/`doc://` by-reference data, and the "no inline code" boundary have no clean `.slint` expression.
- It forfeits the narrow-waist: the transpile targets Slint elements, so we no longer control the primitive contract that lets us pixel-match the CYD band renderer.

A *one-way, offline* `.compose.yaml → .slint` exporter (a build-time "bake this scene into a native Slint app") is coherent as a niche deliverable, but it is emphatically **not** the runtime model and buys us little.

### Seam C — Slint's property system as our binding target
The `Value`-by-string property protocol (Seam A's mechanism) is genuinely nice and is the one piece worth *studying* (see Direction 2, lift #3). But adopting it wholesale means adopting Slint's component runtime, which drags in everything above.

**Honest bottom line for Direction 1:** the only Slint integration that does not maim the heart is **a Slint window as one bus-fed tactical surface** — e.g. a high-end Rust-embedded panel that subscribes MQTT and renders a Slint UI, appearing to the rest of ComposeIO as just another bus peer. That is *interop*, not *foundation*, and I would only reach for it if a concrete need arises that our own tier genuinely cannot meet (see §5).

---

## 4. Direction 2 — Lift FROM Slint to Accelerate Us (the high-value output)

Ranked by value. Each: *what to lift · how it maps into our runtime model · effort.* The governing rule: **lift the model/technique, never adopt AOT, never take GPL code into a proprietary tier.**

### Lift #1 — The declarative states + transitions + easing model (HIGHEST VALUE)
**What to lift.** Slint's animation grammar: `animate x, y { duration: 100ms; easing: ease-in-out; }`; the easing vocabulary `linear | ease | ease-in | ease-out | ease-in-out | cubic-bezier(a,b,c,d)`; `delay` / `duration` / `iteration-count`; and especially the **`states` block with `in { animate … }` / `out { animate … }` transitions** bound to state changes.

**How it maps.** This is almost exactly the tactical-UX animation substrate we have queued (`[CXC-RM-BEHAVIORS]`, `[CXC-RM-GESTURE]`). Our `engine/behaviors.py` already does *trigger → engine-owned temporal behavior* and our `engine/animation.py` already does property interpolation with easings and — critically — is **deterministic** (`tick(now)` / `render_frame_at` produce byte-identical frames cross-platform, which is a *stronger* guarantee than Slint offers). The lift is a **schema + easing-math** addition, not a new runtime concept:
- Add a declarative `states:` block per widget to `.compose.yaml`, with `in`/`out` transition animations, driven by a bus namespace (state on the bus, transition in the widget). This generalizes today's `behaviors:` `when → do` into named states with entry/exit choreography.
- Extend our `EASING` dict with `cubic-bezier(a,b,c,d)` and add `delay` / `iteration_count` to the behavior/animation params.
- Keep our determinism guarantee (Slint's isn't frame-reproducible cross-platform the way our pixel gate demands).

**Effort:** Medium. No new engine subsystem — it lands in `behaviors.py`/`animation.py` + the parser + the Composer binding editor. This is the single best accelerator because it turns a roadmap item into a well-trodden design we can copy the *shape* of.

### Lift #2 — The line-by-line software renderer technique for the CYD/ESP32 tier (HIGH VALUE, CYD)
**What to lift.** Slint's MCU software renderer: allocate **two one-line buffers**, render line N+1 on the CPU while line N is streamed to the display over **DMA**, overlapping compute and transfer; support both full-framebuffer and strict line-by-line modes; track dirty lines to skip unchanged rows. Proven on RP2040/Pico 2 and STM32 in `no_std` with the embassy executor, runtime <300 KiB RAM.

**How it maps.** This attacks our **single worst measured bottleneck**. The `[CXC-RM-M5]` perf floor: empty-scene band render **56 ms / 17.9 fps**, a 10-widget gauge dashboard **2.7 fps**, and the explicit note that *animation on the CYD needs dirty-band rendering we have not built*. Our `Canvas` contract already has `present_partial(rects)` as the seam. Lift the algorithm into `ESP32Canvas`:
- Double-buffered line/band + DMA overlap (render-ahead while the prior band DMAs over SPI) — directly raises the empty-scene floor and the per-frame budget.
- Dirty-line/dirty-band tracking wired to our existing damage model (`request_redraw` → `present_partial`) — the missing piece for on-device animation.
- Slint's `no_std` + board-abstraction structuring is a strong reference for our `board_profile.h` firmware and the Qualia sub-tier.

**Effort:** Medium-high (C++ on the CYD tier), but it de-risks the biggest characterized weakness in the whole stack. **Critical caveat:** lift the *technique* from the blogs/docs; do **not** vendor Slint's GPL renderer code into our (non-GPL) firmware.

### Lift #3 — The `Value`-by-string dynamic property protocol + `cubic-bezier` easing (MEDIUM VALUE)
**What to lift.** Two things. (a) `cubic-bezier(a,b,c,d)` easing (trivial). (b) Slint's type-erased `Value` + `set_property(name, Value)` / `get_property(name)` — a *uniform runtime property protocol* addressed by string, backed by per-type `ErasedPropertyInfo`.

**How it maps.** We already have attr-bindings and, from `[CXC-RM-BINDING-EDITOR]`, engine **introspection tables** for widget attributes. Formalize a per-widget **get/set-attr-by-name** table mirroring Slint's model: one uniform protocol that the attr-binding dispatch, the Composer property editor, and the Intelligence bridge all speak. It sharpens what we have rather than adding a subsystem, and it's the *right* half of Seam C to steal without taking the runtime it lives in. `cubic-bezier` drops straight into `EASING`.

**Effort:** Low–medium.

### Lift #4 — Layout constraint model cross-check (MEDIUM-LOW VALUE)
**What to lift.** Slint's mature layout: row/column/grid with `spacing`, `alignment`, `min`/`max`/`preferred` sizes, and stretch factors — a clean, battle-tested constraint solver.

**How it maps.** We have `FlexContainer` + `GridContainer` + `PixelStackContainer` with `grow` and row/column weights. This is mostly **validation**: cross-check our constraint semantics (preferred/min/max + stretch) against Slint's, and confirm we expose spacing/alignment enums as cleanly. A few small primitives may be worth adding; no architectural change.

**Effort:** Low.

### Lift #5 — Portable expression-DSL evaluation contract (MEDIUM VALUE, FUTURE)
**What to lift.** Slint evaluates its expression language *identically* through the interpreter (`eval_expression`) and codegen via the shared LLR — a pure, host-escape-free evaluation contract.

**How it maps.** `[CXC-RM-BEHAVIORS]` deliberately **deferred a sandboxed expression DSL** (the 6-use-case gate is now met but it's un-scheduled). When we build it, model the *evaluation contract* on Slint's LLR/`eval_expression` approach: a pure function of declared inputs, interpretable identically on Python (desktop/RPi) **and** C++ (CYD), no host escape — which is exactly our "no inline code, must run define-once on every backend" constraint. Reference now, build later.

**Effort:** N/A now; a design reference for the future DSL deliverable.

### Explicitly NOT worth lifting
- **Skia backend structuring** — we already have a working Skia raster + SDL2 window and a backend abstraction (the `Canvas` ABC) that mirrors Slint's renderer-trait selector. Nothing to gain.
- **The `.slint` element vocabulary as our widget set** — coupling to Slint's elements forfeits the narrow-waist and the registry/plugin model. No.
- **Slint's reactive graph** — it is not the bus. Adopting it means two reactive systems.

---

## 5. Recommendation

1. **Keep the heart, unconditionally.** The runtime decompose/compose model (`.compose.yaml` parsed to a live, incrementally-mutated, hot-swappable tree), the bus-native reactive core, the enumerable registry + `widget_modules:` plugin model, and the narrow-waist `Canvas` are non-negotiable. They are the product.

2. **Reject "switch to Slint."** Slint is compile-and-run. Its interpreter — the only seam that even approaches runtime composition — re-compiles per composition, runs a rival reactive graph, replaces our Canvas contract and Python widget model, and cannot reach our fungible-firmware CYD tier without a GPL/commercial embedded license *and* a full widget rewrite. Every piece of the heart is lost or duplicated. The mismatch is structural, not a matter of effort.

3. **Lift these, in this order:**
   - **Now:** the **declarative states/transitions/easing model** (Lift #1) into `behaviors.py`/`animation.py`/`.compose.yaml` — it converts a roadmap item into a copyable design and is the highest-leverage move.
   - **Now (CYD track):** the **line-by-line + DMA-overlap + dirty-line software-renderer technique** (Lift #2) into `ESP32Canvas`/`present_partial` — it targets our worst measured bottleneck and unlocks on-device animation. Technique only; no GPL code.
   - **Soon:** `cubic-bezier` easing + the **`Value`-by-string property protocol** (Lift #3) to unify attr-binding/property-editor/Intelligence dispatch.
   - **Later / reference:** layout constraint cross-check (Lift #4); the portable expression-DSL evaluation contract (Lift #5) when the deferred DSL is scheduled.

4. **Interop-at-the-edges only, and only on demand.** If a concrete need appears for a high-end Rust-embedded tactical panel that our Python/Skia + CYD tiers genuinely cannot serve, the sanctioned pattern is **a Slint window as one bus-fed surface**: it subscribes MQTT, renders a Slint UI, and appears to ComposeIO as just another bus peer (state on the bus, behavior in that surface). That is interop, never foundation — and it keeps the heart untouched.

**One-sentence disposition:** Slint has already built, in production, the two things on our roadmap we most want to copy (a states/transitions animation model and a line-by-line MCU renderer) — so lift those ideas hard, and leave its compile-and-run model exactly where it is.

---

### Sources
- Slint architecture, rendering backends, licensing — https://slint.dev/
- Slint interpreter & dynamic loading (ComponentCompiler / ComponentInstance / Value) — https://deepwiki.com/slint-ui/slint/3.4-interpreter-and-dynamic-loading
- `slint-interpreter` crate (runtime compilation of `.slint`) — https://docs.rs/slint-interpreter
- Slint Python binding (beta, interpreter-based, `load_file`) — https://docs.slint.dev/latest/docs/python/
- Slint 0.3 MCU platform API — https://slint.dev/blog/slint-0.3-released-with-platform-api-for-mcu
- Porting Slint to microcontrollers (264K RAM; line-by-line renderer) — https://slint.dev/blog/porting-slint-to-microcontrollers
- Slint embedded / `no_std` / software renderer docs — https://docs.slint.dev/latest/docs/slint/guide/platforms/embedded/
- Slint animations & states/transitions reference — https://docs.slint.dev/latest/docs/slint/guide/language/coding/animation/ and https://docs.slint.dev/latest/docs/slint/guide/language/coding/states/

### ComposeUX grounding (this repo)
- `python/composeio/render/engine/parser.py` — `.compose.yaml` runtime parser, `parse_widget_tree`, `widget_modules:` project-local mechanism
- `python/composeio/render/engine/widget_registry.py` — `WIDGET_REGISTRY`, `WidgetDescriptor`, `list_widgets`, `build` hook
- `python/composeio/render/canvas.py` and `cyd/include/canvas.h` — the narrow-waist `Canvas` contract (Python ABC + C++ port)
- `python/composeio/render/widget.py` — `Widget` base: `on_bus_state`, `request_redraw`, damage/paint-bounds, animation hooks
- `python/composeio/render/engine/behaviors.py`, `engine/animation.py` — deterministic temporal behaviors + easing
- `python/composeio/resolve.py` — `bus://` / `doc://` data-by-reference
- `corpora/platform-specs/roadmap/00-render-engine-roadmap.lts` — `[PROJECT:CXC-ROADMAP]` (M0–M5, CYD perf floor, Decision-1, behaviors/gesture/DSL roadmap)
</content>
</invoke>
