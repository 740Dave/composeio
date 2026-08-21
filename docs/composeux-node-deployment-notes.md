---
scope: "Field notes: what it actually takes to deploy the ComposeUX render engine onto a fresh Linux SBC, from the first hardware validation (MinnowBoard Turbot / Atom E3845 / Bay Trail Gen7, 2026-07-21)."
audience: "Anyone deploying ComposeUX to a real display node, and the render-engine maintainers."
relates-to: ["render/conformance.py", "render/fonts.py", "render/window.py", "740Dave/provisionIO (ANTIPATTERNS.md, recipes/minnowboard-turbot-debian13.yaml)"]
status: "Validated on hardware — mary-ann (Turbot) runs a full touch kiosk."
---

# Deploying ComposeUX to a fresh Linux SBC — field notes

First end-to-end hardware validation of the ComposeUX render+decode stack on a from-scratch node
(**MinnowBoard Turbot Dual-E, Atom E3845, Bay Trail Gen7, 2 GB, Debian 13**). Result: **14/14 headless
render conformance byte-match** vs the macOS goldens, **VA-API H.264 HW decode live**, and a **rotated
5-finger multitouch kiosk**. What the render docs don't yet say, learned the hard way:

## Runtime requirements beyond `pip install composeio`
1. **Ship the fonts.** The bundled IBM Plex TTFs live at **repo-level `fonts/`, OUTSIDE the `python/`
   subtree.** A deploy that ships only `python/` (e.g. `git archive python/`) omits them, and
   `resolve_font_path()` falls back to a wrong face → **`text_native` conformance drops to ~90%** (silent).
   Fix: ship the TTFs and set **`COMPOSEIO_FONT_DIR`**, or populate the `bus://assets/fonts/` fallback.
   *(Consider vendoring the TTFs under the package, or making the bus-fetch the documented default.)*
2. **The live window needs `pysdl2` + `pysdl2-dll`.** `render.window.SkiaWindow` imports `sdl2`; headless
   conformance doesn't, so a headless-only dep set fails at `import sdl2` the moment you run a live/kiosk
   surface. Add them to the display-node dep set (SDL2 auto-selects the Wayland backend under a compositor).
3. **PyAV must be `av<15` on Debian ≤ trixie**, built `--no-binary av` to link the system vaapi ffmpeg.
   av≥15 targets ffmpeg 8's swscale API and won't compile against ffmpeg 7.1; the pip wheel bundles a
   vaapi-less ffmpeg (→ no HW decode). Pinned `av 14.4.0` gives `vaapi_in_hwdevices=True`, `h264_decoder=True`.

## Bay Trail (Gen7) display specifics
- **The GPU has no working modern-Mesa GL** — a wlroots compositor's default **GLES2/EGL renderer FAILS**
  ("DRI2: failed to load driver"). Run the compositor with **`WLR_RENDERER=pixman`** (software compositing).
  This is fine *because ComposeUX CPU-rasters via skia* — the render path never needed the GPU. The GPU is
  used **only for VA-API H.264 decode** (via i965), which works. i.e. the "software raster + software
  compositing, HW video decode" split is the right model for old Intel silicon, and it's now proven.
- **Whole-screen rotation is a compositor concern**, not the engine's per-widget `rotation`. Under a wlroots
  compositor, `output transform 90/180/270` rotates the display **and** the touch coordinates together
  (validated: taps land under fingertips on a physically-rotated panel). SDL2 receives already-transformed
  coordinates — no engine change needed.
- **A ComposeApp must render frames to receive input.** (General Wayland truth, but worth stating for kiosk
  authors: a surface with no committed buffer is never mapped → invisible and gets no touch/pointer events.)

## Touch
- Multitouch flows **Wayland → SDL2 `TouchFingerEvent` → engine PointerEvents**; SDL2 also synthesizes
  mouse-clicks from single taps, so ordinary button widgets work. 5 simultaneous fingers tracked cleanly
  (pinch/zoom/rotate hardware path is there); gesture *recognition* is a runtime concern.
- Hardware caveat (Bay Trail, not ComposeUX): the touch USB **must be on a USB-2 port** — the xHCI/USB-3
  wouldn't enumerate the panel at all.

## Interactivity gap worth noting
`render_demo.compose.yaml` is a passive **render showcase** — its only interactive element is one button
whose tap does a bus round-trip (needs the bus/binding to show effect). For a **touch-interactive kiosk
demo**, the example set could use a self-contained app with visible on-tap widget responses (no external
bus required). Touch delivery to the app is proven; the demos just don't exercise it visibly.

---
Full provisioning recipe + the negative knowledge (every dead-end) live in **`740Dave/provisionIO`**
(`recipes/minnowboard-turbot-debian13.yaml`, `ANTIPATTERNS.md`). The Turbot is now a validated first-class
ComposeUX touch-display tier.

---

## Widget interactivity findings (live tap-testing on mary-ann, 2026-07-21)

Building a tap-interactive kiosk surfaced several things that headless render tests never exercise:

1. **`led` widget has NO `on_tap`** — it's a passive display widget. `render_demo`'s "tap me (bus
   round-trip)" LED literally cannot respond to a tap. **Use `button` for tappable elements** (it has
   `on_tap` → advances its state engine → publishes).
2. **`publish_namespace` DOUBLE-FIRES on a widget that already has `on_tap`.** The runtime's `_fire_tap`
   calls `w.on_tap()` AND (if `publish_namespace` is set) a **declarative numeric `0`/`1` toggle publish**.
   On a `button` (which already publishes via its engine), this second publish sends `0`/`1` — which
   **crashes any non-binary state engine** (`ValueError: Invalid value '0' for tri_level engine`).
   `publish_namespace` is for *passive* widgets (the LED, which needs it since it has no `on_tap`).
   **Rule: don't put `publish_namespace` on a `button`.**
3. **A widget's `state_engine` must match its bound namespace's `state_engine`.** The button defaults to
   `binary` (`off`/`on`); publishing `on` into a `tri_level` namespace crashes. Keep them aligned.
4. **A `button` does NOT echo its own publish back to its own LED.** `button.on_tap` advances the engine +
   publishes, but the LED renders from `self._value` which only updates in `on_bus_state` — and the bus
   doesn't echo a widget's own publish back to it. So `led_behavior: follow_state` shows no persistent
   change on self-tap. Options: `led_behavior: while_pressed` (immediate touch-driven glow, no bus
   dependency — what we shipped), a **separate subscriber widget** (a big `led` bound to the namespace, fed
   by the button's publish — a *different* widget DOES receive it), or a widget-declared variable toggled +
   bound to the LED. *(Arguably the engine should either echo-to-self or not hard-crash on a rejected
   published value — `namespace.publish_state` → `state_engine.set` raises uncaught, killing the app.)*
5. **`ValueError` on a rejected published value crashes the whole app** (`on_bus_state` catches it, but the
   publish path doesn't). Consider clamping/rejecting invalid values gracefully rather than crashing.

## Turbot performance (for sizing render nodes)
E3845 @1.91GHz, 4 cores, 2GB DDR3L: **7-zip 6867 total MIPS** (≈ stock-to-mild-OC RPi4). Runs the ComposeUX
render stack + software (pixman) compositor + SDL2 window comfortably for a kiosk. RPi4-class; wins on
multithread + AES-NI + x86, RPi4 wins single-thread + memory bandwidth + RAM ceiling.

---

## DONE (2026-07-21): multitouch visualizer wrapped as a ComposeUX custom widget — `TouchVizWidget`
The permanent kiosk on `mary-ann` now runs a **first-class ComposeApp** (`~/apps/touch_viz.compose.yaml` via
`run_compose --fullscreen`), replacing the raw-SDL `~/apps/touch_viz.py` (kept in place as instant rollback;
old launcher saved as `~/kiosk-app.sh.rawviz.bak`). Same visual (halo + colored core block per finger), now
rendered through skia and bus-observable. Files (all in `render/`):
- **`widgets/touch_viz.py`** — `TouchVizWidget`: `measure()` fills the surface; tracks `pointer_id → (x,y)` from
  the raw feed (down/move set, up removes); `draw()` paints a radial-gradient halo + `fill_round_rect` core per
  active pointer, colour = `_PALETTE[pointer_id % len]`. Registered in `engine/parser.py build_leaf`
  (`touch_viz`) + exported from `widgets/__init__.py`.
- **`examples/touch_viz.compose.yaml`** — one `pixel_stack` root (portrait 1080×1920, matches the rotated Dell)
  holding a single fullscreen `touch_viz` leaf.

### ⚠️ Engine finding — the runtime COLLAPSES multitouch; the fix is an opt-in raw feed
The queued note said "the engine already has true multitouch." That is true only at the **event** layer
(`window.py` emits `PointerEvent(pointer_id=fingerId)` per finger). At the **dispatch** layer it is NOT: the
runtime's `_dispatch_pointer` routes through a **single `_pointer_capture`** and the synthesized hooks
(`on_pan(dx,dy)`/`on_tap`) **carry no pointer_id** — so N fingers collapse to one and a second finger's stream
is lost when the first lifts. Genuine per-finger tracking was unreachable through the stock hooks.
**Fix (a real, minimal, general contract extension — [CXC-R-ENGINE-HIT]):**
- `widget.py` — `Widget.wants_raw_pointers: bool = False` + `on_raw_pointer(ev)` hook (ev carries `.type`,
  `.x`, `.y`, `.pointer_id`).
- `engine/runtime.py` `_dispatch_pointer` — a widget that opts in receives EVERY PointerEvent (down/move/up),
  pointer_id intact, routed to the widget the finger LANDED on (looked up per-pointer_id in `_pointer_state`),
  ALONGSIDE the unchanged gesture synthesis.
This is the reusable path any true-multitouch widget (pinch/rotate surfaces, drum pads, N-slider boards) needs.
**Render-engine maintainers: consider promoting this to the widget contract spec.**

### Fullscreen-kiosk knob
The nested-`widgets:` root `FlexContainer` hardcoded a 12px gutter, which inset a fullscreen leaf and clipped
edge touches. Added `design: { padding: <n|[t,r,b,l]>, gap: <n> }` override in `engine/parser.py` (default 12,
unchanged) so a kiosk scene declares `padding: 0, gap: 0` for edge-to-edge bleed. Non-regressive: render
conformance stays **14/14 byte-match**.

### Deploy note (serial-only, no git on target)
mary-ann's `~/composeio-python` is an **editable install but NOT a git checkout** (`git` isn't even installed),
reachable only over the `ginger` serial console (she's on fw1-LAN, no inbound from the Mac). Deploy path that
worked: bundle new files + an **atomic, idempotent str-replace patcher** → `tar|gzip|base64` → stream in
≤1200-char chunks over serial → **sha256-verify the tarball** before extract. Two gotchas bit and are worth
keeping: (1) **serial flips characters silently** — the byte COUNT matched but 3 chunks were corrupted; per-chunk
sha compare located them, re-send fixed them (always verify content hash, never just length). (2) mary-ann's
tree is an **older composeio generation** (`__all__` had no `MultiLineEditMixin`) — the atomic validate-then-write
patcher caught the anchor mismatch and wrote nothing rather than half-patching. Editable install means the
patched files are live immediately; `pkill -x sway` cycles the kiosk hands-free via the getty-autologin path.
