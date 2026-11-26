# ComposeUX — Quickstart: launching the app + monitoring

A short field guide for building and watching ComposeUX widgets. Every command below is
copy-paste and verified from the repo root (`${COMPOSEIO_HOME}`). `composeio` is installed
(editable), so `python3 -m composeio...` works from the repo root with scene paths relative to
wherever you run it.

> **Two ways to build a widget.** (A) The **visual Composer GUI** (§0) — drag/drop, property +
> binding editors, live preview. (B) The **file/CLI path** (§1–§5) — edit a `.compose.yaml` and run
> the render engine; lower-level, good for quick edits and understanding the format. Both produce the
> same `.compose.yaml`.

---

## 0. The visual Composer + Headless Monitor (the GUI tools)

> These are the **M4** tools (`python/composeio/composer/`), built on the render engine. They
> supersede the older CustomTkinter composer under `ux_composer/src/composer` — use the M4 ones below.

**The Composer** — the visual widget builder, and itself a ComposeUX app: **the same Skia engine
that runs deployed widgets runs the tool that builds them** (dogfooding, `[CXC-RM-M4]`). Runs from
the repo root (main package, installed):

```bash
python3 -m composeio.composer [design.compose.yaml] [--fullscreen]
python3 -m composeio.composer --headless /tmp/composer.png    # one frame to PNG (verified)
```

**Open an existing widget** — until an in-GUI Open button lands, load it via the launch arg. E.g.
open the **DRADIS** radar display (the game widget that deploys to the Qualia ESP32) to inspect it,
edit its bindings, or preview it on the design bus:

```bash
python3 -m composeio.composer python/composeio/render/examples/dradis.compose.yaml
#   also: dradis_lottie.compose.yaml (Lottie sweep) · m31_flagship / m31_workshop · git_graph
```

It opens a ruler'd **canvas** on the left and a tabbed panel on the right — **Overview**
(name / width·height / namespaces / bindings / purpose), **Layers**, **Widgets** (the palette),
**Bindings**, **Source** (the live `.compose.yaml`), **Flow** — with a Layout/Flow toggle,
Undo / Redo, **Save**, **Export**, and a bottom status bar showing live **Bus / MQTT / UART** state.
It reads and writes the same `.compose.yaml` the render engine (§1) runs — build here, run there.

**Co-editing with the Intelligence (me):** the Composer is bus-connected (the status bar's `Bus ●
live`), and the `composeio_*` composer tools are my composition surface — so the design intent is
that I can observe what you've built and edit/suggest *alongside* you on the same bus, not through a
separate API. (Ask me to look and I'll read the composite off the bus.)

**The Headless Monitor** — the diagnosis viewer for *headless* (invisible, logic-only) widgets
(`[SPEC:CXC-HMON]`, spec 28 — **it IS built**, contrary to an earlier note): one define-once
ComposeUX surface over `_bus/headless/*` with a **fleet rail** (runtimes + heartbeats), an
**annotated hero** (the output expression with this tick's live values substituted, the taken branch
lit / dead branch dimmed, + a published/suppressed pill), and a **tick timeline**:

```bash
python3 -m composeio.composer.monitor [--mqtt mqtt-broker.local] [--widget <id>] [--runtime <id>]
```

**Running a finished design live** is just the render engine with `--transports` (§1) — that runs a
`.compose.yaml` as a live, bus-connected surface. (The Composer's `Export` + its `deploy` path push
headless logic nodes to a HeadlessRuntime; see `[CXC-FLAUTH-DEPLOY]`.)

---

## 1. Launch a scene (the app)

A "scene" is a `.compose.yaml`. Run it live in an SDL window:

```bash
# live window (no bus — uses the scene's `initial:` seed values)
python3 -m composeio.render.engine.runtime <scene>.compose.yaml

# live window + connect the ComposeBus (192.0.2.75:1883) so bindings show REAL data
python3 -m composeio.render.engine.runtime <scene>.compose.yaml --transports

# borderless fullscreen (RPi/kiosk)
python3 -m composeio.render.engine.runtime <scene>.compose.yaml --fullscreen
```

Try one now — the shipped examples are the best reference:

```bash
python3 -m composeio.render.engine.runtime python/composeio/render/examples/render_demo.compose.yaml
```

**Good starting scenes** (`python/composeio/render/examples/`):
`render_demo` (general widget tour) · `subwidgets_demo` (composite widgets) ·
`chat_thread_demo` · `git_graph` · `red_alert` (state→behavior) · `mermaid_diagram` ·
`dradis` (the game display) · `components/` (dropdown, keyboard, numpad, progress_bar,
segment_bar, slider, switch — forkable composites).

The live Intelligence Console is `compose_projects/intelligence/console/console.compose.yaml` (run it with
`--transports` to see it on the bus).

---

## 2. Headless render (fast iterate loop)

Render **one frame to a PNG and exit** — no window, instant, ideal for tightening a layout:

```bash
python3 -m composeio.render.engine.runtime <scene>.compose.yaml --headless /tmp/out.png
open /tmp/out.png
```

The iterate loop: edit the `.compose.yaml` → `--headless /tmp/out.png` → look → repeat. Once the
layout's right, run it live (§1) to see motion + live bindings.

---

## 3. Monitor the bus (see what your bindings are actually doing)

Widgets bind to **namespaces** on the bus; when a binding looks wrong, watch the traffic.

**A. Bus watch (one-liner)** — print everything on the bus (Ctrl-C to stop):

```bash
python3 - <<'PY'
import paho.mqtt.client as mqtt, json
def on_c(c,u,f,rc): c.subscribe('composeio/#', 0); print('watching composeio/#')
def on_m(c,u,m):
    try: v = json.loads(m.payload.decode()); v = v.get('val', v)
    except Exception: v = m.payload.decode(errors='replace')
    print(f'{m.topic:48} {v}')
cl = mqtt.Client(); cl.on_connect=on_c; cl.on_message=on_m
cl.connect('192.0.2.75', 1883, 30); cl.loop_forever()
PY
```

Topic scheme is `composeio/<scope>/<namespace>` (e.g. `composeio/global/self/context_pct`).
Narrow it by changing the filter, e.g. `composeio/global/self/#`.

**B. On-screen bus monitor** — drop a `bus_monitor` widget into any scene to see live traffic
*inside* the app:

```yaml
- type: bus_monitor
  width: 400
  height: 300
```

**C. Publish a test value** — to exercise a binding without waiting for a real producer:

```bash
python3 - <<'PY'
import paho.mqtt.client as mqtt, json, time
c = mqtt.Client(); c.connect('192.0.2.75',1883,10); c.loop_start()
env = {"ns":"demo/value","type":"state","val":73,"src":"you","scope":"global","seq":1,"ts":time.time()}
c.publish('composeio/global/demo/value', json.dumps(env), qos=1); time.sleep(0.5)
print('published demo/value = 73')
PY
```

> **Deeper introspection (design-tier):** spec 28 `[SPEC:CXC-HMON]` defines a fuller *Headless
> Monitor* — a per-widget tick trace (inputs read, which DSL branch ran, published-or-suppressed)
> on `_bus/headless/<id>/*`. That's the "why did this tick produce that?" tool; the bus watch above
> is the "what's on the wire right now?" tool that covers day-to-day widget building.

---

## 4. Scene anatomy (authoring from scratch)

Copy an example and edit, or start from this shape:

```yaml
app:
  id: my-widget-lab
design:
  target: desktop          # desktop | cyd | qualia | ...
  theme: dark
  width: 480
  height: 320
namespaces:                # the bus state your widgets read
  - {name: demo/value, state_engine: json, initial: 42}
widgets:
  - type: label
    text: "Hello ComposeUX"
    font_size: 20
  - type: gauge            # binds to a namespace -> live value
    namespace: demo/value
    min_val: 0
    max_val: 100
```

Rules of thumb:
- **State on the bus, behavior in the widget** — publish a semantic value once (`demo/value = 73`);
  the widget owns how it presents it (a gauge sweep, a blink). Don't stream frames.
- Bind a widget to live data by giving it a `namespace:`; seed it with `initial:` for the headless
  render.
- Lay out with `stack` (`direction: row|column`, `gap`, `padding`, `grow`).

**Widget catalog** (`python/composeio/render/widgets/`):
`label` · `rectangle` · `circle` · `led` · `gauge` · `button` · `checkbox` · `switch`(power_toggle) ·
`slider`(drag) · `text_input` · `tab` · `dialog` · `callout` · `image` · `svg` · `lottie` ·
`mermaid` · `chat_thread` · `activity` · `subagent_ledger` · `registered_checklist` · `self_ledger` ·
`gauge` · `avatar` · `bus_monitor` · `doc_picker` · `command` · `relay` · `coordinate`.

---

## 5. The one gotcha

Run from the **repo root** with a scene path relative to it. `--transports` needs the ComposeBus
reachable (`192.0.2.75:1883`); without it the scene renders its `initial:` seeds only. If a live
scene looks laggy while iterating, that's expected on heavy scenes — the engine now does
dirty-region rendering, so input is sub-frame; a full first paint of a large scene is the cost.

---

*Questions / rough edges → note them as you build; that feedback is exactly what sharpens the tool.*
