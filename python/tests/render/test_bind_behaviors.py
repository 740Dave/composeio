"""
[SPEC:CXC-BIND] Declarative Bindings & Behaviors — compliance tests.

Covers the full compliance table:

  C1  attribute binding: a namespace change updates the bound attribute
      within one frame (opacity/rotation/scale/color/value attrs)
  C2  Red Alert: Image `visible` bound + blink behavior, golden-pinned
      at both phases
  C3  Dradis sweep: rotate behavior on `rotation`, declared deg/s
  C4  variables: $name parse-time substitution; namespace-bound variable
      stays reactive
  C5  revert on release: trigger goes false -> target reverts to base
  C6  cross-platform determinism: fixed logical time -> byte-identical
      frames (render_frame_at); goldens pin macOS<->RPi identity
  C7  no code path: inline-code fields are stripped, never executed
  C8  value map: literal lookup works; expression/arithmetic maps refused

Goldens live in reference_images/ and regenerate via:
    CXC_REGEN_GOLDENS=1 python3 -m pytest tests/render/test_bind_behaviors.py
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.engine.runtime import RenderApp

from .conftest import REFERENCE_DIR, images_match, load_reference, surface_to_array


def _golden(canvas, name):
    actual = surface_to_array(canvas)
    path = REFERENCE_DIR / f"{name}.png"
    if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
        canvas.save_png(str(path))
        pytest.skip(f"golden (re)generated: {path}")
    expected = load_reference(name)
    match, ratio = images_match(actual, expected, tolerance=2,
                                max_diff_ratio=0.005)
    assert match, f"{name}: {ratio * 100:.2f}% of pixels exceed tolerance"


def _app(config: dict) -> RenderApp:
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def _widget(app: RenderApp, wid: str):
    return next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == wid)


# ---------------------------------------------------------------------------
# C1 — attribute binding, one-frame update
# ---------------------------------------------------------------------------

def test_c1_transform_bindings_bare_namespace():
    """opacity/rotation/scale bind via the bare-namespace string form."""
    app = _app({
        "design": {"width": 200, "height": 120},
        "widgets": [
            {"type": "rect", "id": "r", "width": 60, "height": 30,
             "opacity": "fx/dim", "rotation": "nav/heading_deg",
             "scale": "fx/zoom"},
        ],
    })
    try:
        r = _widget(app, "r")
        assert (r.opacity, r.rotation, r.scale) == (1.0, 0.0, 1.0)
        app.publish("fx/dim", 0.5)
        app.publish("nav/heading_deg", 30)
        app.publish("fx/zoom", 1.5)
        app.render_frame()                      # ONE frame ([CXC-BIND-C1])
        assert r.opacity == 0.5
        assert r.rotation == 30.0
        assert r.scale == 1.5
    finally:
        app.stop()


def test_c1_object_form_value_and_text():
    """The object form binds a widget's value attributes."""
    app = _app({
        "design": {"width": 240, "height": 120},
        "widgets": [
            {"type": "gauge", "id": "g", "min": 0, "max": 120,
             "value": {"namespace": "engine/temp", "default": 20}},
            {"type": "label", "id": "lbl", "text": {"namespace": "sys/msg",
                                                    "default": "idle"}},
        ],
    })
    try:
        g, lbl = _widget(app, "g"), _widget(app, "lbl")
        assert g.value == 20.0                  # declared default
        assert lbl.text == "idle"
        app.publish("engine/temp", 87)
        app.publish("sys/msg", "coolant nominal")
        app.render_frame()
        assert g.value == 87.0
        assert lbl.text == "coolant nominal"
    finally:
        app.stop()


def test_c1_binding_defaults_hold_until_namespace_speaks():
    app = _app({
        "design": {"width": 200, "height": 100},
        "widgets": [
            {"type": "rect", "id": "r", "width": 40, "height": 20,
             "opacity": {"namespace": "fx/dim", "default": 0.25}},
        ],
    })
    try:
        assert _widget(app, "r").opacity == 0.25
    finally:
        app.stop()


def test_c1_wire_time_current_value_applies():
    """A namespace with existing state applies at wire time (bus-first)."""
    from composeio.bus import ComposeIOBus
    bus = ComposeIOBus(bus_id="pretest")
    bus.start()
    bus.ensure_namespace("nav/heading_deg", engine_config="json")
    bus.publish_state("nav/heading_deg", 45, source="test")
    app = RenderApp.from_dict({
        "design": {"width": 100, "height": 100},
        "widgets": [{"type": "rect", "id": "r", "width": 30, "height": 20,
                     "rotation": "nav/heading_deg"}],
    }, windowed=False, bus=bus)
    app.start()
    try:
        assert _widget(app, "r").rotation == 45.0
    finally:
        app.stop()
        bus.stop()


# ---------------------------------------------------------------------------
# C8 — value map (literal lookups only)
# ---------------------------------------------------------------------------

def test_c8_color_value_map():
    app = _app({
        "design": {"width": 200, "height": 100},
        "widgets": [
            {"type": "label", "id": "lbl", "text": "ALERT",
             "color": {"namespace": "sys/alert_level",
                       "map": {"ok": "#26A69A", "warn": "#FFB300",
                               "crit": "#E03B3B"}}},
        ],
    })
    try:
        lbl = _widget(app, "lbl")
        app.publish("sys/alert_level", "crit")
        app.render_frame()
        assert lbl.color == (0xE0, 0x3B, 0x3B)
        app.publish("sys/alert_level", "ok")
        app.render_frame()
        assert lbl.color == (0x26, 0xA6, 0x9A)
        # unmapped state with no default: attribute holds its last value
        app.publish("sys/alert_level", "wat")
        app.render_frame()
        assert lbl.color == (0x26, 0xA6, 0x9A)
    finally:
        app.stop()


def test_c8_arithmetic_map_refused(caplog):
    """A templated/arithmetic map is DSL territory: the binding is refused
    (attribute keeps its literal), never evaluated."""
    with caplog.at_level(logging.WARNING):
        app = _app({
            "design": {"width": 200, "height": 100},
            "widgets": [
                {"type": "rect", "id": "r", "width": 40, "height": 20,
                 "opacity": {"namespace": "fx/dim",
                             "map": {"half": "{{ value * 0.5 }}"}}},
            ],
        })
    try:
        r = _widget(app, "r")
        app.publish("fx/dim", "half")
        app.render_frame()
        assert r.opacity == 1.0                 # binding refused, untouched
        assert not app.comp.attr_bindings       # nothing wired
        assert any("refused" in rec.message for rec in caplog.records)
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C4 — inline variables
# ---------------------------------------------------------------------------

def test_c4_variable_substitutes_behavior_param():
    app = _app({
        "design": {"width": 200, "height": 100},
        "variables": {"alert_period": 2000},
        "widgets": [
            {"type": "rect", "id": "r", "width": 40, "height": 20,
             "behaviors": [
                 {"when": {"namespace": "ship/red_alert", "is": "on"},
                  "do": {"behavior": "blink", "target": "visible",
                         "period_ms": "$alert_period"}},
             ]},
        ],
    })
    try:
        decl = app.comp.behaviors[0]
        assert decl.params["period_ms"] == 2000     # resolved at load
    finally:
        app.stop()


def test_c4_namespace_bound_variable_stays_reactive():
    """A variable that IS a binding propagates reactivity to its uses."""
    app = _app({
        "design": {"width": 200, "height": 100},
        "variables": {"heading": {"namespace": "nav/heading_deg"}},
        "widgets": [
            {"type": "rect", "id": "r", "width": 40, "height": 20,
             "rotation": "$heading"},
        ],
    })
    try:
        r = _widget(app, "r")
        app.publish("nav/heading_deg", 270)
        app.render_frame()
        assert r.rotation == 270.0
    finally:
        app.stop()


def test_c4_widget_scoped_variables_and_embedded_strings():
    app = _app({
        "design": {"width": 240, "height": 100},
        "variables": {"unit": "degC"},
        "widgets": [
            {"type": "label", "id": "lbl",
             "variables": {"sensor": "coolant"},
             "text": "$sensor temp ($unit)"},
        ],
    })
    try:
        assert _widget(app, "lbl").text == "coolant temp (degC)"
    finally:
        app.stop()


def test_c4_unknown_variable_left_as_literal():
    app = _app({
        "design": {"width": 200, "height": 100},
        "widgets": [{"type": "label", "id": "lbl", "text": "$100 budget"}],
    })
    try:
        assert _widget(app, "lbl").text == "$100 budget"
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C2 — Red Alert (blink), golden-pinned at both phases
# ---------------------------------------------------------------------------

def _red_alert_config(png_path: str) -> dict:
    return {
        "design": {"width": 320, "height": 240, "title": "red-alert"},
        "variables": {"alert_period": 1000},
        "namespaces": [{"name": "ship/red_alert", "state_engine": "binary"}],
        "widgets": [{
            "type": "pixel_stack", "id": "scene",
            "width": 320, "height": 240, "background": "#101318",
            "children": [
                {"type": "rect", "id": "hull", "x": 20, "y": 20,
                 "w": 280, "h": 200, "fill_color": "#28283C",
                 "corner_radius": 10},
                {"type": "image", "id": "alert_img",
                 "src": "file://" + png_path,
                 "x": 100, "y": 90, "w": 120, "h": 60,
                 "visible": "ship/red_alert",
                 "behaviors": [
                     {"when": {"namespace": "ship/red_alert", "is": "on"},
                      "do": {"behavior": "blink", "target": "visible",
                             "period_ms": "$alert_period"}},
                 ]},
            ],
        }],
    }


def _alert_png(tmp_path) -> str:
    src = SkiaCanvas()
    src.create_surface(120, 60)
    src.set_fill_color(0xE0, 0x3B, 0x3B)
    src.fill_round_rect(0, 0, 120, 60, 8)
    src.set_fill_color(0xFF, 0xFF, 0xFF)
    src.set_font("sans-serif", 16, "bold")
    src.draw_text("RED ALERT", 60, 38, "center")
    png = os.path.join(str(tmp_path), "red_alert.png")
    src.save_png(png)
    return png


def test_c2_blink_toggles_at_declared_period(tmp_path):
    app = _app(_red_alert_config(_alert_png(tmp_path)))
    try:
        img = _widget(app, "alert_img")
        assert img.visible is False             # hidden until the alarm

        app.publish("ship/red_alert", "on")     # ONE bit on the bus
        app.render_frame_at(0.0)                # activation: on-phase
        assert img.visible is True
        app.render_frame_at(0.25)               # 25% of 1000ms: still on
        assert img.visible is True
        app.render_frame_at(0.75)               # off-phase
        assert img.visible is False
        app.render_frame_at(1.25)               # next cycle: on again
        assert img.visible is True
    finally:
        app.stop()


@pytest.mark.parametrize("t,phase", [(0.25, "on"), (0.75, "off")])
def test_c2_red_alert_golden_both_phases(tmp_path, t, phase):
    app = _app(_red_alert_config(_alert_png(tmp_path)))
    try:
        app.publish("ship/red_alert", "on")
        app.render_frame_at(0.0)                # deterministic activation
        app.render_frame_at(t)
        _golden(app.canvas, f"bind_red_alert_{phase}")
    finally:
        app.stop()


def test_c2_intelligence_parity_any_publisher_triggers_identically(tmp_path):
    """Bus-native: an Intelligence publishing red_alert=on is
    indistinguishable from any other publisher — same namespace, same
    state engine, same blink."""
    app = _app(_red_alert_config(_alert_png(tmp_path)))
    try:
        img = _widget(app, "alert_img")
        # publish exactly as an external peer would: straight to the bus
        # with a foreign source id, not through the app helper.
        app.bus.publish_state("ship/red_alert", "on",
                              source="intelligence-claude")
        app.render_frame_at(0.0)
        assert img.visible is True
        app.render_frame_at(0.75)
        assert img.visible is False
        app.bus.publish_state("ship/red_alert", "off",
                              source="intelligence-claude")
        app.render_frame_at(1.0)
        assert img.visible is False             # reverted ([CXC-BIND-C5])
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C3 — Dradis sweep (rotate at deg/s)
# ---------------------------------------------------------------------------

def _dradis_config() -> dict:
    return {
        "design": {"width": 320, "height": 240, "title": "dradis"},
        "variables": {"sweep_rate": 90},
        "namespaces": [{"name": "dradis/scanning", "state_engine": "binary"}],
        "widgets": [{
            "type": "pixel_stack", "id": "dish",
            "width": 320, "height": 240, "background": "#0A0F0A",
            "children": [
                {"type": "circle", "id": "ring", "radius": 90,
                 "fill_color": "#0E1A10", "stroke_color": "#1F5A2E",
                 "stroke_width": 2, "x": 70, "y": 30},
                {"type": "rect", "id": "needle", "x": 158, "y": 32,
                 "w": 4, "h": 176, "fill_color": "#3BE06B",
                 "behaviors": [
                     {"when": {"namespace": "dradis/scanning", "is": "on"},
                      "do": {"behavior": "rotate", "target": "rotation",
                             "deg_per_s": "$sweep_rate"}},
                 ]},
            ],
        }],
    }


def test_c3_rotate_sweeps_at_declared_rate():
    app = _app(_dradis_config())
    try:
        needle = _widget(app, "needle")
        assert needle.rotation == 0.0
        app.publish("dradis/scanning", "on")
        app.render_frame_at(0.0)
        assert needle.rotation == 0.0
        app.render_frame_at(0.5)
        assert needle.rotation == pytest.approx(45.0)   # 90 deg/s * 0.5s
        app.render_frame_at(1.0)
        assert needle.rotation == pytest.approx(90.0)
        app.render_frame_at(5.0)
        assert needle.rotation == pytest.approx(90.0)   # 450 % 360
    finally:
        app.stop()


def test_c3_dradis_golden_mid_sweep():
    app = _app(_dradis_config())
    try:
        app.publish("dradis/scanning", "on")
        app.render_frame_at(0.0)
        app.render_frame_at(0.5)                # needle at 45 degrees
        _golden(app.canvas, "bind_dradis_mid_sweep")
    finally:
        app.stop()


def test_c3_dradis_golden_idle():
    app = _app(_dradis_config())
    try:
        app.render_frame_at(0.0)                # not scanning: needle at base
        _golden(app.canvas, "bind_dradis_idle")
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C5 — revert on release
# ---------------------------------------------------------------------------

def test_c5_rotate_reverts_to_base_on_release():
    app = _app(_dradis_config())
    try:
        needle = _widget(app, "needle")
        app.publish("dradis/scanning", "on")
        app.render_frame_at(0.0)
        app.render_frame_at(0.7)
        assert needle.rotation != 0.0
        app.publish("dradis/scanning", "off")
        app.render_frame_at(0.8)
        assert needle.rotation == 0.0           # base value restored
    finally:
        app.stop()


def test_c5_revert_lands_on_bound_base(tmp_path):
    """When the same attribute is BOTH bound and behavior-held, release
    reverts to the binding's latest value, not a stale snapshot."""
    app = _app(_red_alert_config(_alert_png(tmp_path)))
    try:
        img = _widget(app, "alert_img")
        app.publish("ship/red_alert", "on")     # binding shows + blink starts
        app.render_frame_at(0.0)
        app.render_frame_at(0.75)
        assert img.visible is False             # blink off-phase
        app.publish("ship/red_alert", "off")    # trigger releases + binding hides
        app.render_frame_at(1.0)
        assert img.visible is False
        app.publish("ship/red_alert", "on")     # alarm again
        app.render_frame_at(1.1)
        assert img.visible is True
    finally:
        app.stop()


def test_c5_one_shot_ramp_runs_once_then_reverts():
    app = _app({
        "design": {"width": 200, "height": 100},
        "widgets": [
            {"type": "rect", "id": "r", "width": 40, "height": 20,
             "opacity": 1.0,
             "behaviors": [
                 {"when": {"namespace": "fx/fade", "is": "out"},
                  "do": {"behavior": "ramp", "target": "opacity",
                         "to": 0.2, "duration_ms": 1000,
                         "easing": "linear"}},
             ]},
        ],
    })
    try:
        r = _widget(app, "r")
        app.publish("fx/fade", "out")
        app.render_frame_at(0.0)
        assert r.opacity == pytest.approx(1.0)
        app.render_frame_at(0.5)
        assert r.opacity == pytest.approx(0.6)   # halfway, linear
        app.render_frame_at(1.5)
        assert r.opacity == pytest.approx(0.2)   # completed, holds
        app.render_frame_at(2.0)
        assert r.opacity == pytest.approx(0.2)
        app.publish("fx/fade", "in")             # trigger releases
        app.render_frame_at(2.1)
        assert r.opacity == pytest.approx(1.0)   # reverted to base
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# Behavior semantics: triggers, pulse, composition
# ---------------------------------------------------------------------------

def test_trigger_kinds_truthy_in_always():
    app = _app({
        "design": {"width": 240, "height": 120},
        "widgets": [
            {"type": "rect", "id": "a", "width": 30, "height": 20,
             "behaviors": [{"when": {"namespace": "sys/busy"},
                            "do": {"behavior": "blink", "target": "visible",
                                   "period_ms": 1000}}]},
            {"type": "rect", "id": "b", "width": 30, "height": 20,
             "behaviors": [{"when": {"namespace": "engine/temp",
                                     "in": [90, 120]},
                            "do": {"behavior": "blink", "target": "visible",
                                   "period_ms": 1000}}]},
            {"type": "rect", "id": "c", "width": 30, "height": 20,
             "behaviors": [{"when": "always",
                            "do": {"behavior": "rotate", "target": "rotation",
                                   "deg_per_s": 10}}]},
        ],
    })
    try:
        insts = app.behaviors.instances
        by_wid = {i.decl.widget.widget_id: i for i in insts}
        assert by_wid["c"].active                      # always: immediately
        app.publish("sys/busy", "yes")                 # truthy
        app.publish("engine/temp", 95)                 # in range
        app.render_frame_at(0.0)
        assert by_wid["a"].active and by_wid["b"].active
        app.publish("sys/busy", "off")                 # falsy
        app.publish("engine/temp", 40)                 # out of range
        app.render_frame_at(0.1)
        assert not by_wid["a"].active and not by_wid["b"].active
    finally:
        app.stop()


def test_pulse_eases_between_from_and_to():
    app = _app({
        "design": {"width": 200, "height": 100},
        "widgets": [
            {"type": "rect", "id": "r", "width": 40, "height": 20,
             "behaviors": [
                 {"when": {"namespace": "fx/breathe", "is": "on"},
                  "do": {"behavior": "pulse", "target": "opacity",
                         "from": 0.2, "to": 1.0, "period_ms": 2000,
                         "easing": "linear"}},
             ]},
        ],
    })
    try:
        r = _widget(app, "r")
        app.publish("fx/breathe", "on")
        app.render_frame_at(0.0)
        assert r.opacity == pytest.approx(0.2)          # phase 0 -> from
        app.render_frame_at(0.5)                        # quarter: halfway up
        assert r.opacity == pytest.approx(0.6)
        app.render_frame_at(1.0)                        # half: peak
        assert r.opacity == pytest.approx(1.0)
        app.render_frame_at(1.5)                        # three-quarter: down
        assert r.opacity == pytest.approx(0.6)
    finally:
        app.stop()


def test_composition_last_declared_wins():
    app = _app({
        "design": {"width": 200, "height": 100},
        "widgets": [
            {"type": "rect", "id": "r", "width": 40, "height": 20,
             "behaviors": [
                 {"when": "always",
                  "do": {"behavior": "rotate", "target": "rotation",
                         "deg_per_s": 10}},
                 {"when": "always",
                  "do": {"behavior": "rotate", "target": "rotation",
                         "deg_per_s": 50}},
             ]},
        ],
    })
    try:
        r = _widget(app, "r")
        app.render_frame_at(0.0)
        app.render_frame_at(1.0)
        assert r.rotation == pytest.approx(50.0)        # last declared wins
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C6 — determinism (fixed-timestep sampling)
# ---------------------------------------------------------------------------

def test_c6_same_logical_time_is_byte_identical():
    app1 = _app(_dradis_config())
    app2 = _app(_dradis_config())
    try:
        for app in (app1, app2):
            app.publish("dradis/scanning", "on")
            app.render_frame_at(0.0)
            app.render_frame_at(0.6180339887)
        a = surface_to_array(app1.canvas)
        b = surface_to_array(app2.canvas)
        assert np.array_equal(a, b)             # exact, not tolerance-based
    finally:
        app1.stop()
        app2.stop()


def test_c6_resampling_same_time_is_stable():
    app = _app(_dradis_config())
    try:
        app.publish("dradis/scanning", "on")
        app.render_frame_at(0.0)
        app.render_frame_at(0.5)
        first = surface_to_array(app.canvas).copy()
        app.render_frame_at(0.5)                # same logical instant again
        assert np.array_equal(first, surface_to_array(app.canvas))
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C7 — no code path
# ---------------------------------------------------------------------------

def test_c7_inline_code_fields_stripped_never_executed(caplog):
    """A config carrying code-shaped fields parses fine, the fields are
    ignored with a warning, and nothing is ever evaluated."""
    canary = os.path.join(os.environ.get("TMPDIR", "/tmp"), "cxc_bind_canary")
    if os.path.exists(canary):
        os.remove(canary)
    payload = f"__import__('pathlib').Path({canary!r}).touch()"
    with caplog.at_level(logging.WARNING):
        app = _app({
            "design": {"width": 200, "height": 100},
            "widgets": [
                {"type": "rect", "id": "r", "width": 40, "height": 20,
                 "script": payload, "code": payload, "eval": payload,
                 "behaviors": [
                     {"when": "always",
                      "do": {"behavior": "rotate", "target": "rotation",
                             "deg_per_s": 10, "expr": payload}},
                     {"when": "always",
                      "do": {"python": payload}},   # code-only do: dropped
                 ]},
            ],
        })
    try:
        r = _widget(app, "r")
        app.render_frame_at(0.0)
        app.render_frame_at(1.0)
        assert not os.path.exists(canary)              # NEVER executed
        assert not hasattr(r, "script")
        assert len(app.comp.behaviors) == 1            # code-only do: gone
        assert "expr" not in app.comp.behaviors[0].params
        assert r.rotation == pytest.approx(10.0)       # clean part still runs
        warnings = [rec.message for rec in caplog.records]
        assert any("CXC-BIND-BOUNDARY" in m for m in warnings)
    finally:
        app.stop()


def test_c7_unknown_behavior_name_ignored(caplog):
    with caplog.at_level(logging.WARNING):
        app = _app({
            "design": {"width": 200, "height": 100},
            "widgets": [
                {"type": "rect", "id": "r", "width": 40, "height": 20,
                 "behaviors": [
                     {"when": "always",
                      "do": {"behavior": "run_python", "target": "rotation"}},
                 ]},
            ],
        })
    try:
        assert not app.comp.behaviors
        assert any("unknown behavior" in rec.message
                   for rec in caplog.records)
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# Transform rendering (the draw-side of [CXC-BIND-ATTR])
# ---------------------------------------------------------------------------

def test_transform_attrs_change_rendered_pixels():
    """rotation/scale/opacity actually alter the raster output."""
    def render(**attrs):
        app = _app({
            "design": {"width": 120, "height": 120},
            "widgets": [{
                "type": "pixel_stack", "id": "s", "width": 120,
                "height": 120, "background": "#000000",
                "children": [{"type": "rect", "id": "r", "x": 40, "y": 50,
                              "w": 40, "h": 20, "fill_color": "#26A69A",
                              **attrs}],
            }],
        })
        try:
            app.render_frame_at(0.0)
            return surface_to_array(app.canvas).copy()
        finally:
            app.stop()

    base = render()
    assert not np.array_equal(base, render(rotation=45))
    assert not np.array_equal(base, render(scale=1.5))
    assert not np.array_equal(base, render(opacity=0.3))
    # and literal transforms are golden-stable
    app = _app({
        "design": {"width": 120, "height": 120},
        "widgets": [{
            "type": "pixel_stack", "id": "s", "width": 120, "height": 120,
            "background": "#000000",
            "children": [{"type": "rect", "id": "r", "x": 40, "y": 50,
                          "w": 40, "h": 20, "fill_color": "#26A69A",
                          "rotation": 30, "scale": 1.25, "opacity": 0.7}],
        }],
    })
    try:
        app.render_frame_at(0.0)
        _golden(app.canvas, "bind_transform_literals")
    finally:
        app.stop()


def test_nested_opacity_composes():
    """A translucent container's translucent child multiplies, and the
    canvas opacity returns to 1.0 afterwards (no leakage)."""
    app = _app({
        "design": {"width": 160, "height": 120},
        "widgets": [{
            "type": "pixel_stack", "id": "s", "width": 160, "height": 120,
            "background": "#000000",
            "children": [
                {"type": "stack", "id": "grp", "opacity": 0.5,
                 "x": 10, "y": 10, "w": 60, "h": 40,
                 "children": [{"type": "rect", "id": "inner", "width": 60,
                               "height": 40, "fill_color": "#FFFFFF",
                               "opacity": 0.5}]},
                {"type": "rect", "id": "ref", "x": 90, "y": 10,
                 "w": 60, "h": 40, "fill_color": "#FFFFFF"},
            ],
        }],
    })
    try:
        app.render_frame_at(0.0)
        px = surface_to_array(app.canvas)
        inner = px[30, 40]                      # inside nested rect
        ref = px[30, 120]                       # inside full-opacity rect
        assert tuple(ref[:3]) == (255, 255, 255)   # opacity state restored
        assert 48 <= inner[0] <= 80             # ~0.25 alpha over black
    finally:
        app.stop()
