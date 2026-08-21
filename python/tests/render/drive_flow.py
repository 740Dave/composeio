"""Scripted [SPEC:CXC-FLOW] acceptance walkthrough — all over the bus.

Builds the Jump Drive Console (the pre-vis design), feeds live external
bus traffic, toggles to Flow, filters the rail, wires a discovered
namespace, and screenshots every step.  Runs identically on macOS and
RPi5 — the shot set is the cross-platform pixel evidence.

    python tests/render/drive_flow.py [outdir]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, "captures", "flow")
os.makedirs(OUT, exist_ok=True)

os.environ.pop("COMPOSEUX_MQTT_BROKER", None)

from composeio.composer.app import ComposerApp  # noqa: E402

app = ComposerApp("", windowed=False, connect_mqtt=False)
app.start()
core, ui = app.core, app.ui
ib = core.internal_bus


def shot(name):
    app.render.drain_updates()
    app.render.render_frame()
    app.render.canvas.save_png(os.path.join(OUT, name))
    print("shot:", name)


# -- 1. the Jump Drive Console design (pre-vis), over the bus ---------------
ib.publish_event("composer/config/widget_changed", "do",
                 data={"property": "name", "value": "Jump Drive Console"},
                 source="drive")
ib.publish_event("composer/config/namespace_changed", "do",
                 data={"action": "created", "name": "ship/scanning",
                       "state_engine": "binary"}, source="drive")
ib.publish_event("composer/canvas/widget_added", "do",
                 data={"widget_type": "gauge", "widget_id": "jump_readiness",
                       "x": 60, "y": 60, "w": 180, "h": 150,
                       "properties": {"label": "READINESS", "min_val": 0,
                                      "max_val": 1}}, source="drive")
ib.publish_event("composer/canvas/widget_added", "do",
                 data={"widget_type": "image", "widget_id": "klaxon",
                       "x": 300, "y": 60, "w": 160, "h": 120,
                       "properties": {"src": ""}}, source="drive")
ib.publish_event("composer/canvas/widget_added", "do",
                 data={"widget_type": "rect", "widget_id": "dradis",
                       "x": 300, "y": 220, "w": 160, "h": 120,
                       "properties": {"fill_color": "#10202C",
                                      "corner_radius": 8}}, source="drive")
# in-design wiring: dradis rotates while ship/scanning
ib.publish_event("composer/config/widget_changed", "do",
                 data={"widget_id": "dradis", "property": "behaviors",
                       "value": [{"when": {"namespace": "ship/scanning",
                                           "is": "on"},
                                  "do": {"behavior": "rotate",
                                         "target": "rotation",
                                         "deg_per_s": 90}}]},
                 source="drive")

# -- 2. external traffic arrives on the ComposeBus (the outside world) ------
EXTERNAL = [
    ("jumpdrive/readiness", "charging", "esp32-jumpdrive"),
    ("jumpdrive/charge", 142, "esp32-jumpdrive"),
    ("jumpdrive/ftl_lock", "off", "esp32-jumpdrive"),
    ("dradis/contact", {"count": 2}, "dradis-sim"),
    ("ship/hull_temp", 208, "hull-sensors"),
]
for ns, v, src in EXTERNAL:
    core.design_bus.ensure_namespace(ns, engine_config="json")
    core.design_bus.publish_state(ns, v, source=src)
# ...plus a firehose, so the filter has something real to narrow (C5)
for i in range(56):
    ns = f"ship/sensor/{i:02d}"
    core.design_bus.ensure_namespace(ns, engine_config="json")
    core.design_bus.publish_state(ns, i, source="hull-sensors")
core.design_bus.publish_state("ship/scanning", "on", source="drive")

shot("flow_00_layout.png")

# -- 3. toggle to Flow ([CXC-FL-C3]) -----------------------------------------
ib.publish_state("composer/ww/mode", "flow", source="drive")
shot("flow_01_graph.png")

# -- 4. filter the firehose ([CXC-FL-C5]) -------------------------------------
ui.flow.search.set_text("jumpdrive/*")
ui.flow._set_filter_text("jumpdrive/*")
shot("flow_02_filtered.png")
n, m = ui.flow._count_line
print(f"filter: showing {n} of {m}")

# -- 5. discover-and-wire ([CXC-FL-C6]): the drag's commit, over the bus ------
ui.flow.author_wire("jumpdrive/readiness", "jump_readiness", "value")
shot("flow_03_wired.png")

# select the new connection -> inspector
ui.flow.select_wire("jumpdrive/readiness", "jump_readiness", "value",
                    "subscribe")
shot("flow_04_inspector.png")

# -- 6. declare-expected ([CXC-FL-C7]): subscribe before it's live ------------
ui.flow._set_filter_text("jumpdrive/spool_temp")
ui.flow.search.set_text("jumpdrive/spool_temp")
shot("flow_05_declare_expected.png")
ui.flow.declare_expected("jumpdrive/spool_temp")
ui.flow.author_wire("jumpdrive/spool_temp", "klaxon", "visible")
ui.flow._set_filter_text("")
ui.flow.search.set_text("")
shot("flow_06_expected_wired.png")
# ...the rig powers on: first publish activates it
core.design_bus.ensure_namespace("jumpdrive/spool_temp",
                                 engine_config="json")
core.design_bus.publish_state("jumpdrive/spool_temp", "on",
                              source="esp32-jumpdrive")
shot("flow_07_expected_live.png")

# -- 7. pattern subscription ([CXC-FL-C8]): a bus_monitor of jumpdrive/* ------
ib.publish_event("composer/canvas/widget_added", "do",
                 data={"widget_type": "bus_monitor", "widget_id": "jd_mon",
                       "x": 60, "y": 260, "w": 220, "h": 120,
                       "namespace": "jumpdrive/*",
                       "properties": {"label": "Jumpdrive family"}},
                 source="drive")
shot("flow_08_pattern.png")

# -- 8. the synced projections ([CXC-FL-C2]): Bindings + Source see it all ----
ib.publish_state("composer/ww/mode", "layout", source="drive")
ib.publish_state("composer/selection/widget_id", "jump_readiness",
                 source="drive")
ib.publish_state("composer/wc/active_tab", "bindings", source="drive")
shot("flow_09_bindings_sync.png")
ib.publish_state("composer/wc/active_tab", "source", source="drive")
shot("flow_10_source_sync.png")

out = os.path.join(OUT, "jump_drive_console.compose.yaml")
core.save(out)
print("exported:", out)
print(open(out).read())
app.stop()
