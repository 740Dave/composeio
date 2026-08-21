"""
M4 Composer tests ([CXC-RM-M4] acceptance):

  - CompositeDocument round-trip (save -> load -> save byte-identical,
    [DP-CXC-7])
  - bus-driven document mutation: the SAME composer/* protocol the
    intelligence_mcp bridge publishes (add widget / move / resize /
    set_property / namespace / z-order / visibility) — [CXC-CREDO]
  - Intelligence responders: request_state / request_layout / validate
  - WC tab switching over the bus
  - THE GATE: build the M31 composite via bus commands, export
    .compose.yaml, run it standalone on desktop-Skia, and pixel-compare
    the Composer's live preview region against the standalone render —
    the preview IS the deployed artifact.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.composer.app import ComposerApp
from composeio.composer.core import ComposerCore
from composeio.composer.document import CompositeDocument, LayerDef, NamespaceDef

from .conftest import REFERENCE_DIR, images_match, load_reference, surface_to_array


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _core() -> ComposerCore:
    core = ComposerCore(CompositeDocument("Test Widget", 400, 300))
    core.start(connect_mqtt=False)
    return core


def _cmd(core: ComposerCore, target: str, do: dict) -> None:
    """Publish a command exactly the way the MCP bridge does
    (intelligence_mcp/tools.py::_publish_command)."""
    core.internal_bus.publish_event(
        "composer/command/execute", "execute",
        data={"command_id": "t1", "type": target, "do": do, "undo": {},
              "description": "", "source": "test-intelligence"},
        source="test-intelligence")


# ---------------------------------------------------------------------------
# document model
# ---------------------------------------------------------------------------

def test_document_roundtrip_byte_identical(tmp_path):
    doc = CompositeDocument("M31 Annotated Viewer", 560, 420)
    doc.purpose = "A star-field viewer."
    doc.add_namespace(NamespaceDef("astro/annotations", state_engine="json"))
    doc.add_layer(LayerDef("image_m31", "image", x=20, y=20, w=520, h=340,
                           properties={"src": "bus://assets/m31/board.png",
                                       "fit": "contain"}))
    doc.add_layer(LayerDef("btn_start", "button", x=430, y=370, w=110, h=36,
                           properties={"label": "Start Tour",
                                       "publish_namespace": "viewer/tour"}))
    y1 = doc.to_yaml()
    doc2 = CompositeDocument.from_yaml(y1)
    y2 = doc2.to_yaml()
    assert y1 == y2, "save -> load -> save must be byte-identical [DP-CXC-7]"
    assert doc2.get_layer("btn_start").properties["label"] == "Start Tour"
    assert doc2.width == 560 and doc2.height == 420


def test_document_z_order_and_visibility():
    doc = CompositeDocument()
    for wid in ("a", "b", "c"):
        doc.add_layer(LayerDef(wid, "rect"))
    doc.reorder_layer("a", 2)
    assert [l.widget_id for l in doc.layers] == ["b", "c", "a"]
    doc.layers[0].visible = False
    spec = doc.to_dict()["widgets"][0]["children"][0]
    assert spec["visible"] is False


# ---------------------------------------------------------------------------
# bus-driven mutation (the Intelligence path)
# ---------------------------------------------------------------------------

def test_bus_commands_mutate_document():
    core = _core()
    try:
        doc = core.document
        _cmd(core, "composer/canvas/widget_added",
             {"widget_id": "btn_1", "widget_type": "button",
              "module_id": "composite", "namespace": "",
              "properties": {"label": "Fire"}, "x": 40, "y": 50,
              "w": 150, "h": 36})
        layer = doc.get_layer("btn_1")
        assert layer is not None and layer.properties["label"] == "Fire"
        assert (layer.x, layer.y, layer.w, layer.h) == (40, 50, 150, 36)

        # MCP move_widget publishes row/column — composite mode reads px
        _cmd(core, "composer/canvas/widget_moved",
             {"widget_id": "btn_1", "row": 90, "column": 120})
        assert (layer.x, layer.y) == (120, 90)

        _cmd(core, "composer/canvas/widget_resized",
             {"widget_id": "btn_1", "columnspan": 200, "rowspan": 44})
        assert (layer.w, layer.h) == (200, 44)

        _cmd(core, "composer/config/widget_changed",
             {"widget_id": "btn_1", "property_name": "label",
              "value": "Engage"})
        assert layer.properties["label"] == "Engage"

        _cmd(core, "composer/config/namespace_changed",
             {"action": "created", "name": "ship/fire",
              "state_engine": "binary"})
        assert doc.get_namespace("ship/fire") is not None
        assert core.design_bus.get_namespace("ship/fire") is not None

        # layer ops via set_property (visible / z_index)
        _cmd(core, "composer/canvas/widget_added",
             {"widget_id": "img_1", "widget_type": "image",
              "module_id": "composite", "namespace": "", "properties": {},
              "x": 0, "y": 0})
        _cmd(core, "composer/config/widget_changed",
             {"widget_id": "img_1", "property_name": "z_index", "value": 0})
        assert [l.widget_id for l in doc.layers] == ["img_1", "btn_1"]
        _cmd(core, "composer/config/widget_changed",
             {"widget_id": "img_1", "property_name": "visible",
              "value": False})
        assert doc.get_layer("img_1").visible is False

        _cmd(core, "composer/canvas/widget_removed", {"widget_id": "img_1"})
        assert doc.get_layer("img_1") is None
    finally:
        core.stop()


def test_direct_canvas_events_also_mutate():
    """The human UI path publishes 'do' events straight on the canvas
    namespaces — same result as the command path (peers)."""
    core = _core()
    try:
        core.internal_bus.publish_event(
            "composer/canvas/widget_added", "do",
            data={"widget_type": "callout", "module_id": "composite",
                  "properties": {"text": "core"}, "x": 10, "y": 10},
            source="ui")
        assert len(core.document.layers) == 1
        wid = core.document.layers[0].widget_id
        assert wid.startswith("callout")
    finally:
        core.stop()


def test_intelligence_snapshots():
    core = _core()
    try:
        _cmd(core, "composer/canvas/widget_added",
             {"widget_id": "g1", "widget_type": "gauge",
              "module_id": "composite", "namespace": "eng/temp",
              "properties": {}, "x": 5, "y": 6, "w": 140, "h": 120})
        got: dict = {}
        core.internal_bus.subscribe_events(
            "composer/intelligence/state_snapshot",
            lambda ns, et, d: got.update(state=d))
        core.internal_bus.subscribe_events(
            "composer/intelligence/layout_snapshot",
            lambda ns, et, d: got.update(layout=d))
        core.internal_bus.subscribe_events(
            "composer/intelligence/validation_result",
            lambda ns, et, d: got.update(valid=d))

        core.internal_bus.publish_event(
            "composer/intelligence/request_state", "request",
            data={"agent_id": "tester", "correlation_id": "c1"},
            source="tester")
        core.internal_bus.publish_event(
            "composer/intelligence/request_layout", "request",
            data={"agent_id": "tester", "correlation_id": "c2"},
            source="tester")
        core.internal_bus.publish_event(
            "composer/intelligence/validate", "request",
            data={"agent_id": "tester", "correlation_id": "c3"},
            source="tester")

        assert got["state"]["correlation_id"] == "c1"
        mod = got["state"]["design"]["modules"][0]
        assert mod["id"] == "composite"
        assert mod["widgets"][0]["id"] == "g1"

        lay = got["layout"]["modules"][0]
        assert lay["widgets"][0]["bounds"] == {"x": 5, "y": 6,
                                               "w": 140, "h": 120}
        # gauge subscribes eng/temp which is undeclared -> warning
        assert got["valid"]["valid"] is True
        warnings = [w["message"] for w in got["valid"]["warnings"]]
        assert any("eng/temp" in w for w in warnings)
    finally:
        core.stop()


# ---------------------------------------------------------------------------
# full Composer app (headless window) — chrome + tab switching + golden
# ---------------------------------------------------------------------------

def _composer_app(tmp_path, name="wc") -> ComposerApp:
    app = ComposerApp(windowed=False, connect_mqtt=False)
    app.start()
    app.render.render_frame()
    return app


def test_wc_tab_switch_over_bus(tmp_path):
    app = _composer_app(tmp_path)
    try:
        assert app.ui.tabs.active_index == 0
        app.core.internal_bus.publish_state("composer/wc/active_tab",
                                            "layers", source="tester")
        app.render.render_frame()
        assert app.ui.tabs.active_index == 1
        app.core.internal_bus.publish_state("composer/wc/active_tab",
                                            "widgets", source="tester")
        app.render.render_frame()
        assert app.ui.tabs.active_index == 2
    finally:
        app.stop()


def test_selection_follows_added_widget(tmp_path):
    app = _composer_app(tmp_path)
    try:
        _cmd(app.core, "composer/canvas/widget_added",
             {"widget_id": "r1", "widget_type": "rect",
              "module_id": "composite", "namespace": "",
              "properties": {}, "x": 10, "y": 10, "w": 80, "h": 40})
        app.render.render_frame()
        assert app.ui.workshop.selected_id == "r1"
        assert app.core.internal_bus.get_state(
            "composer/selection/widget_id") == "r1"
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# THE GATE — M31 built over the bus; preview == deployed render
# ---------------------------------------------------------------------------

M31_BUILD = [
    ("composer/config/widget_changed",
     {"property": "name", "value": "M31 Annotated Viewer"}),
    ("composer/config/widget_changed", {"property": "width", "value": 560}),
    ("composer/config/widget_changed", {"property": "height", "value": 420}),
    ("composer/config/namespace_changed",
     {"action": "created", "name": "astro/annotations",
      "state_engine": "json"}),
    ("composer/config/namespace_changed",
     {"action": "created", "name": "viewer/click", "state_engine": "json"}),
    ("composer/config/namespace_changed",
     {"action": "created", "name": "viewer/tour", "state_engine": "binary"}),
    ("composer/canvas/widget_added",
     {"widget_id": "image_m31", "widget_type": "image",
      "module_id": "composite", "namespace": "",
      "properties": {"src": "bus://assets/m31/board.png", "fit": "contain",
                     "publish_namespace": "viewer/click"},
      "x": 20, "y": 16, "w": 520, "h": 330}),
    ("composer/canvas/widget_added",
     {"widget_id": "callout_core", "widget_type": "callout",
      "module_id": "composite", "namespace": "astro/annotations",
      "properties": {"text": "M31 · core", "target": [0.47, 0.50],
                     "offset": [40, -30]},
      "x": 20, "y": 16, "w": 520, "h": 330}),
    ("composer/canvas/widget_added",
     {"widget_id": "callout_m110", "widget_type": "callout",
      "module_id": "composite", "namespace": "",
      "properties": {"text": "M110", "target": [0.26, 0.70],
                     "offset": [-40, 26], "color": "#FFA726"},
      "x": 20, "y": 16, "w": 520, "h": 330}),
    ("composer/canvas/widget_added",
     {"widget_id": "btn_start", "widget_type": "button",
      "module_id": "composite", "namespace": "",
      "properties": {"label": "Start Tour",
                     "publish_namespace": "viewer/tour"},
      "x": 404, "y": 368, "w": 140, "h": 36}),
]


def _build_m31(core: ComposerCore) -> None:
    for target, do in M31_BUILD:
        _cmd(core, target, dict(do))


def test_m31_gate_preview_equals_deployed(tmp_path):
    """Build M31 over the bus, export, run standalone, and require the
    Composer preview region to pixel-match the deployed render."""
    from composeio.render.engine.runtime import RenderApp

    app = _composer_app(tmp_path)
    try:
        _build_m31(app.core)
        app.render.render_frame()      # apply build (auto-selects last add)
        # deselect so no selection chrome overlays the composite
        app.core.internal_bus.publish_state("composer/selection/widget_id",
                                            "", source="tester")
        app.render.render_frame()

        # export via the bus (the same event the Export button fires)
        out_yaml = os.path.join(str(tmp_path), "m31_workshop.compose.yaml")
        app.core.internal_bus.publish_event(
            "composer/file/export", "do", data={"path": out_yaml},
            source="tester")
        assert os.path.isfile(out_yaml)
        assert app.core.internal_bus.get_state("composer/file/path") == out_yaml

        app.render.render_frame()
        composer_px = surface_to_array(app.render.canvas)
        ox, oy = app.ui.workshop._origin
        doc = app.core.document
        preview = composer_px[int(oy):int(oy) + doc.height,
                              int(ox):int(ox) + doc.width]
    finally:
        app.stop()

    # run the exported artifact standalone (deployment path)
    dep = RenderApp.from_file(out_yaml, windowed=False)
    dep.start()
    try:
        dep.render_frame()
        deployed_px = surface_to_array(dep.canvas)
        # nested-dialect root pads 12px -> composite at (12, 12)
        deployed = deployed_px[12:12 + doc.height, 12:12 + doc.width]
    finally:
        dep.stop()

    assert preview.shape == deployed.shape
    diff = np.abs(preview.astype(np.int16) - deployed.astype(np.int16))
    bad = np.any(diff > 2, axis=-1).sum()
    ratio = bad / (preview.shape[0] * preview.shape[1])
    assert ratio <= 0.005, (
        f"Composer preview diverges from deployed render: "
        f"{ratio * 100:.2f}% pixels differ")


def test_m31_composer_chrome_golden(tmp_path):
    """Golden-pin the full Composer window with the M31 build loaded
    (btn_start selected, Layers tab active)."""
    app = _composer_app(tmp_path)
    try:
        _build_m31(app.core)
        app.core.internal_bus.publish_state("composer/wc/active_tab",
                                            "layers", source="tester")
        app.render.render_frame()
        canvas = app.render.canvas
        actual = surface_to_array(canvas)
        path = REFERENCE_DIR / "m4_composer_m31.png"
        if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
            canvas.save_png(str(path))
            pytest.skip(f"golden (re)generated: {path}")
        expected = load_reference("m4_composer_m31")
        match, ratio = images_match(actual, expected, tolerance=2,
                                    max_diff_ratio=0.005)
        assert match, f"composer chrome: {ratio * 100:.2f}% differ"
    finally:
        app.stop()
