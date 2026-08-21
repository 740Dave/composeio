"""
Composer Binding Editor + Source Tab tests ([SPEC:CXC-BINDEDIT]
compliance [CXC-BE-C1..C8]):

  C1  visual attribute binding — the picker's Save command produces a
      [SPEC:CXC-BIND] binding in the exported .compose.yaml
  C2  visual behavior config — a blink configured via the UI command
      makes the WW-previewed widget blink on the design bus trigger
  C3  introspection-driven UI — attrs/behaviors/params come from engine
      tables; the tables cover the whole engine model
  C4  Source sync + selection linkage, both directions
  C5  edit safety — an invalid Source edit reports an error and never
      touches the document
  C6  pop-out — full-width Source dialog stays synced
  C7  raw-spec fidelity — bindings/variables/behaviors round-trip with
      no lossy re-serialization (byte-identical)
  C8  DSL-candidate capture — arithmetic in a map is refused and logged

Plus: is_exact trigger, undo/redo (incl. raw-property restore), and the
rotated-selection AABB hint's data source.
"""

from __future__ import annotations

import json
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.composer.app import ComposerApp
from composeio.composer.document import CompositeDocument
from composeio.render.engine.behaviors import (BEHAVIOR_DEFAULT_TARGET,
                                               BEHAVIOR_NAMES,
                                               BEHAVIOR_PARAM_SCHEMA,
                                               TRIGGER_KINDS, Trigger,
                                               parse_trigger)
from composeio.render.engine.bindings import (bindable_attrs_for,
                                              looks_like_expression)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    os.environ.pop("COMPOSEUX_MQTT_BROKER", None)
    a = ComposerApp("", windowed=False, connect_mqtt=False)
    a.core.__class__.DSL_CANDIDATES_PATH = str(tmp_path / "dsl.jsonl")
    a.start()
    yield a
    a.stop()


def _build_alert(core) -> None:
    ib = core.internal_bus
    ib.publish_event("composer/config/namespace_changed", "do",
                     data={"action": "created", "name": "ship/red_alert",
                           "state_engine": "binary"}, source="t")
    ib.publish_event("composer/canvas/widget_added", "do",
                     data={"widget_type": "label", "widget_id": "alert",
                           "x": 40, "y": 40, "w": 200, "h": 30,
                           "properties": {"text": "RED ALERT"}}, source="t")


def _bind_visible(core) -> None:
    """The exact command the BindingsPanel Save publishes ([CXC-BE-C1])."""
    core.internal_bus.publish_event(
        "composer/config/widget_changed", "do",
        data={"widget_id": "alert", "property": "visible",
              "value": {"namespace": "ship/red_alert", "when": "on",
                        "default": True}},
        source="bindings_panel")


def _add_blink(core) -> None:
    core.internal_bus.publish_event(
        "composer/config/widget_changed", "do",
        data={"widget_id": "alert", "property": "behaviors",
              "value": [{"when": {"namespace": "ship/red_alert", "is": "on"},
                         "do": {"behavior": "blink", "target": "visible",
                                "period_ms": 1000}}]},
        source="bindings_panel")


# ---------------------------------------------------------------------------
# C3 — introspection tables ARE the model
# ---------------------------------------------------------------------------

def test_c3_behavior_schema_covers_engine():
    assert set(BEHAVIOR_PARAM_SCHEMA) == set(BEHAVIOR_NAMES)
    assert set(BEHAVIOR_DEFAULT_TARGET) == set(BEHAVIOR_NAMES)
    for name, schema in BEHAVIOR_PARAM_SCHEMA.items():
        for pname, meta in schema.items():
            assert meta.get("type") in ("number", "value", "color", "enum"), \
                f"{name}.{pname}"
            if meta["type"] == "enum":
                assert meta.get("values"), f"{name}.{pname} needs values"
    assert "is_exact" in TRIGGER_KINDS


def test_c3_bindable_attrs_per_type():
    for base in ("visible", "opacity", "rotation", "scale"):
        assert base in bindable_attrs_for("gauge")
    assert "value" in bindable_attrs_for("gauge")
    assert "text" in bindable_attrs_for("label")
    assert "fill_color" in bindable_attrs_for("rect")
    assert "src" in bindable_attrs_for("image")
    # unknown types still expose the universal transform set
    assert "opacity" in bindable_attrs_for("someday_widget")


def test_c3_new_behavior_appears_without_editor_change(app):
    """Editor fields are read from the schema at form-build time: a row
    added to the engine table shows up with no editor code change."""
    from composeio.render.engine import behaviors as beh
    beh.BEHAVIOR_PARAM_SCHEMA["wobble"] = {
        "amp": {"type": "number", "default": 3}}
    beh.BEHAVIOR_DEFAULT_TARGET["wobble"] = "rotation"
    try:
        _build_alert(app.core)
        app.render.drain_updates()
        app.ui.bindings._open_behavior_form()
        app.ui.bindings.b_behavior = "wobble"
        app.ui.bindings._rebuild()
        assert "param_amp" in app.ui.bindings._inputs
    finally:
        beh.BEHAVIOR_PARAM_SCHEMA.pop("wobble")
        beh.BEHAVIOR_DEFAULT_TARGET.pop("wobble")


def test_is_exact_trigger():
    t = parse_trigger({"namespace": "n", "is_exact": "on"}, "t")
    assert t.kind == "is_exact"
    assert t.matches("on") and not t.matches("true") and not t.matches(True)
    # canonical `is:` treats them as the same bit
    t2 = parse_trigger({"namespace": "n", "is": "on"}, "t")
    assert t2.matches("true") and t2.matches(1) and t2.matches(True)


# ---------------------------------------------------------------------------
# C1 / C2 — bind + behave, zero YAML typed
# ---------------------------------------------------------------------------

def test_c1_binding_lands_in_export(app, tmp_path):
    _build_alert(app.core)
    _bind_visible(app.core)
    out = tmp_path / "a.compose.yaml"
    app.core.save(str(out))
    spec = yaml.safe_load(out.read_text())
    lbl = spec["widgets"][0]["children"][0]
    assert lbl["visible"] == {"namespace": "ship/red_alert", "when": "on",
                              "default": True}


def test_c2_blink_previews_in_workshop(app):
    _build_alert(app.core)
    _bind_visible(app.core)
    _add_blink(app.core)
    app.render.drain_updates()
    app.render.render_frame()
    core, ww = app.core, app.ui.workshop
    core.design_bus.ensure_namespace("ship/red_alert", engine_config="json")
    core.design_bus.publish_state("ship/red_alert", "on", source="intel")
    app.render.drain_updates()
    states = []
    for t in (0.25, 0.75, 1.25, 1.75):
        app.render.behaviors.tick(t)
        w = [c for c in ww._preview.children if c.widget_id == "alert"][0]
        states.append(w.visible)
    assert states == [True, False, True, False]
    # release: revert to the bound base ([CXC-BIND-C5] holds in preview)
    core.design_bus.publish_state("ship/red_alert", "off", source="intel")
    app.render.drain_updates()
    app.render.behaviors.tick(2.0)
    w = [c for c in ww._preview.children if c.widget_id == "alert"][0]
    assert w.visible is False          # when: on unmatched -> hidden


def test_c2_rebuild_clears_stale_behavior_instances(app):
    _build_alert(app.core)
    _add_blink(app.core)
    app.render.drain_updates()
    n1 = len(app.render.behaviors.instances)
    app.ui.workshop.rebuild()
    app.ui.workshop.rebuild()
    assert len(app.render.behaviors.instances) == n1


# ---------------------------------------------------------------------------
# C4 / C5 / C6 — Source tab
# ---------------------------------------------------------------------------

def test_c4_selection_linkage_both_ways(app):
    _build_alert(app.core)
    app.render.drain_updates()
    app.render.render_frame()
    src = app.ui.source
    # canvas -> source
    app.core.internal_bus.publish_state("composer/selection/widget_id",
                                        "alert", source="t")
    app.render.drain_updates()
    assert src.highlight_id == "alert"
    blk = src._blocks["alert"]
    assert any("id: alert" in src.lines[i] for i in range(blk[0], blk[1] + 1))
    # source -> canvas: click inside the block
    app.core.internal_bus.publish_state("composer/selection/widget_id",
                                        "", source="t")
    app.render.drain_updates()
    r = src.arranged_rect
    y = r.y + 26 + (blk[0] + 1) * 16 - src.scroll + 4
    src.on_pointer_down(r.x + 60, y)
    assert app.core.internal_bus.get_state(
        "composer/selection/widget_id") == "alert"


def test_c4_source_edit_updates_canvas(app):
    _build_alert(app.core)
    app.render.drain_updates()
    app.render.render_frame()
    src = app.ui.source
    src.refresh_from_doc()
    i = next(i for i, l in enumerate(src.lines) if "text: RED ALERT" in l)
    src.lines[i] = src.lines[i].replace("RED ALERT", "CONDITION RED")
    src._edited()
    app.render.drain_updates()
    assert app.core.document.get_layer("alert").properties["text"] == \
        "CONDITION RED"
    assert src.error is None


def test_c5_invalid_edit_cannot_corrupt(app):
    _build_alert(app.core)
    app.render.drain_updates()
    src = app.ui.source
    src.refresh_from_doc()
    before = app.core.document.to_yaml()
    src.lines[0] = "]]]: [broken"
    src._edited()
    app.render.drain_updates()
    assert src.error is not None
    assert app.core.document.to_yaml() == before
    status = app.core.internal_bus.get_state("composer/source/status")
    assert status and status.get("ok") is False


def test_c6_popout_full_width_and_synced(app):
    _build_alert(app.core)
    app.render.drain_updates()
    app.render.render_frame()
    app.ui._open_source_popout()
    app.render.render_frame()
    dlg = app.ui.source_dialog
    assert dlg.visible
    assert dlg._panel_rect.w >= 1300           # full-width (window 1440)
    assert "id: alert" in app.ui.source_big.text()
    # an edit in the pop-out commits and syncs the docked view
    big = app.ui.source_big
    i = next(i for i, l in enumerate(big.lines) if "text: RED ALERT" in l)
    big.lines[i] = big.lines[i].replace("RED ALERT", "STAND DOWN")
    big._edited()
    app.render.drain_updates()
    assert "STAND DOWN" in app.ui.source.text()
    dlg.dismiss()


# ---------------------------------------------------------------------------
# C7 — raw-spec fidelity
# ---------------------------------------------------------------------------

_REACTIVE_DOC = """\
app: {id: reactive, transports: []}
design: {title: Reactive, target: desktop, width: 424, height: 424, theme: dark}
namespaces:
- {name: ship/red_alert, state_engine: binary}
variables:
  alert_period: 750
widgets:
- type: pixel_stack
  id: composite
  width: 400
  height: 400
  background: '#0B0D12'
  border: '#3A3A50'
  corner_radius: 4
  clip: true
  children:
  - type: rect
    id: panel
    fill_color:
      namespace: ship/alert_level
      map: {ok: '#153018', crit: '#5A1218'}
      default: '#20242E'
    behaviors:
    - when: {namespace: ship/red_alert, is: on}
      do: {behavior: blink, target: visible, period_ms: $alert_period}
    - when: {namespace: ship/red_alert, is_exact: 'ON'}
      do: {behavior: pulse, target: opacity, to: 0.2}
    x: 20
    y: 20
    w: 360
    h: 200
  - type: label
    id: caption
    text: status
    visible: ship/red_alert
    rotation: {namespace: nav/heading}
    x: 40
    y: 240
    w: 200
    h: 30
"""


def test_c7_reactive_design_roundtrips_byte_identical(app, tmp_path):
    p = tmp_path / "reactive.compose.yaml"
    p.write_text(_REACTIVE_DOC)
    app.core.load(str(p))
    app.render.drain_updates()
    out = tmp_path / "out.compose.yaml"
    app.core.save(str(out))
    doc2 = CompositeDocument.from_yaml(out.read_text())
    assert doc2.to_yaml() == out.read_text(), "not byte-identical"
    spec = yaml.safe_load(out.read_text())
    # variables survived (document-level extras passthrough)
    assert spec["variables"] == {"alert_period": 750}
    panel = spec["widgets"][0]["children"][0]
    # bindings + behaviors survived RAW — including $var params and the
    # is_exact escape hatch
    assert panel["fill_color"]["map"] == {"ok": "#153018", "crit": "#5A1218"}
    assert panel["behaviors"][0]["do"]["period_ms"] == "$alert_period"
    assert "is_exact" in panel["behaviors"][1]["when"]
    caption = spec["widgets"][0]["children"][1]
    assert caption["visible"] == "ship/red_alert"          # bare-string form
    assert caption["rotation"]["namespace"] == "nav/heading"


def test_c7_visible_binding_survives_editor_edits(app, tmp_path):
    """Editing OTHER properties must not disturb a visible binding, and
    the Layers-tab eye must not clobber it on export."""
    p = tmp_path / "reactive.compose.yaml"
    p.write_text(_REACTIVE_DOC)
    app.core.load(str(p))
    ib = app.core.internal_bus
    ib.publish_event("composer/canvas/widget_moved", "do",
                     data={"widget_id": "caption", "x": 50, "y": 250},
                     source="t")
    ib.publish_event("composer/config/widget_changed", "do",
                     data={"widget_id": "caption", "property": "text",
                           "value": "hail"}, source="t")
    spec = yaml.safe_load(app.core.document.to_yaml())
    caption = spec["widgets"][0]["children"][1]
    assert caption["visible"] == "ship/red_alert"
    assert caption["text"] == "hail" and caption["x"] == 50


# ---------------------------------------------------------------------------
# C8 — DSL-candidate capture
# ---------------------------------------------------------------------------

def test_c8_arithmetic_map_refused_and_logged(app):
    _build_alert(app.core)
    app.core.internal_bus.publish_state("composer/selection/widget_id",
                                        "alert", source="t")
    app.render.drain_updates()
    bp = app.ui.bindings
    bp._open_bind_form("rotation")
    bp.f_attr = "rotation"
    bp.f_ns = "ship/red_alert"
    bp._add_map_row()
    bp._inputs["map_k0"].set_text("on")
    bp._inputs["map_v0"].set_text("heading * 2")
    bp._save_binding()
    assert bp.form_error, "arithmetic map must be refused"
    assert "rotation" not in app.core.document.get_layer("alert").properties
    log = app.core.DSL_CANDIDATES_PATH
    assert os.path.exists(log)
    entry = json.loads(open(log).read().strip().split("\n")[-1])
    assert "heading * 2" in str(entry)


def test_c8_expression_heuristic():
    assert looks_like_expression("value * 2")
    assert looks_like_expression("{{ states.x }}")
    assert looks_like_expression("${x}")
    assert not looks_like_expression("bus://assets/green.png")
    assert not looks_like_expression("#FF8800")
    assert not looks_like_expression("n/a")


# ---------------------------------------------------------------------------
# polish — undo/redo
# ---------------------------------------------------------------------------

def test_undo_redo_restores_raw_properties(app):
    _build_alert(app.core)
    _bind_visible(app.core)
    core = app.core
    layer = core.document.get_layer("alert")
    assert isinstance(layer.properties["visible"], dict)
    core.internal_bus.publish_event(
        "composer/canvas/widget_removed", "do",
        data={"widget_id": "alert"}, source="t")
    assert core.document.get_layer("alert") is None
    assert core.undo()
    layer = core.document.get_layer("alert")
    assert layer is not None
    assert layer.properties["visible"]["namespace"] == "ship/red_alert"
    assert core.redo()
    assert core.document.get_layer("alert") is None
    assert core.undo()


def test_undo_property_change(app):
    _build_alert(app.core)
    core = app.core
    core.internal_bus.publish_event(
        "composer/config/widget_changed", "do",
        data={"widget_id": "alert", "property": "text", "value": "X"},
        source="t")
    assert core.document.get_layer("alert").properties["text"] == "X"
    core.undo()
    assert core.document.get_layer("alert").properties["text"] == "RED ALERT"


def test_undo_source_replace(app):
    _build_alert(app.core)
    core = app.core
    before = core.document.to_yaml()
    text = before.replace("RED ALERT", "GREEN BOARD")
    core.internal_bus.publish_event("composer/source/replace", "do",
                                    data={"yaml": text}, source="t")
    assert core.document.get_layer("alert").properties["text"] == \
        "GREEN BOARD"
    core.undo()
    assert core.document.to_yaml() == before


# ---------------------------------------------------------------------------
# edges — rotated AABB hint data
# ---------------------------------------------------------------------------

def test_rotated_selection_reads_live_preview_rotation(app):
    _build_alert(app.core)
    app.core.internal_bus.publish_event(
        "composer/config/widget_changed", "do",
        data={"widget_id": "alert", "property": "rotation", "value": 30},
        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    ww = app.ui.workshop
    ww.selected_id = "alert"
    assert ww._selected_rotation() == pytest.approx(30.0)
