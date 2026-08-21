"""
[SPEC:CXC-BIND] Declarative States + Transitions + Gesture producers
([CXC-BIND-STATES] / [CXC-BIND-TRANSITION] / [CXC-RM-GESTURE]) — compliance.

Phase A — animated-property core + easing
  A1  cubic-bezier(a,b,c,d) resolves to the SHARED Lottie solver, bit-for-bit
  A2  CSS-name aliases + named-entry precedence
  A3  StateEngine tween: deferred start-stamp -> render_frame_at byte-repro
  A4  damage confined to the animating widget (last_touched)
  A5  delay_ms / iteration_count honored

Phase B — declarative states / state / transitions
  B1  a bus-state flip drives a widget through a transition (value trajectory)
  B2  an unmatched attr SNAPS; a matched attr TWEENS
  B3  selector map + default; `state:` is a selector ONLY when `states:` present
  B4  reduced_motion snaps (no tween)
  B5  [CXC-BIND-BOUNDARY] inline code / arithmetic refused in a state config

Phase C — gesture-driven transitions
  C1  a synthesized gesture publishes semantic bus state (long_press/pan/pan_end)
  C2  the state machine consumes it -> the reticle transitions
  C3  Intelligence parity: publishing the SAME namespace produces the identical
      widget-attr trajectory as the gesture path

Run:  python3 -m pytest tests/render/test_states_transitions.py -q
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.engine.states import (resolve_easing, parse_state_machine,
                                            parse_gestures, StateEngine)
from composeio.render.lottie.model import bezier_ease

from .conftest import surface_to_array

EXAMPLE = os.path.join(
    PY_ROOT, "composeio", "render", "examples",
    "target_designation.compose.yaml")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _app(config: dict) -> RenderApp:
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def _w(app: RenderApp, wid: str):
    return next(x for x in app.comp.root.iter_widgets() if x.widget_id == wid)


def _frame_hash(app: RenderApp) -> str:
    return hashlib.sha256(surface_to_array(app.canvas).tobytes()).hexdigest()


class _P:
    """Synthetic PointerEvent (mirrors the gesture suite's fake)."""
    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = float(x), float(y)
        self.pointer_id = 0
        self.dx = self.dy = 0.0


_RETICLE = {
    "states": {
        "idle": {"opacity": 0.0, "scale": 1.6, "fill_color": "#3FB0FF"},
        "acquiring": {"opacity": 0.9, "scale": 1.2, "fill_color": "#FFCC00"},
        "locked": {"opacity": 1.0, "scale": 1.0, "fill_color": "#FF3B30"},
    },
    "state": {"namespace": "fc/lock",
              "map": {"none": "idle", "acquiring": "acquiring",
                      "tracking": "locked"},
              "default": "idle"},
    "transitions": [
        {"animate": ["opacity", "fill_color"], "duration_ms": 180,
         "easing": "ease_out"},
        {"animate": ["scale"], "to": "locked", "duration_ms": 130,
         "easing": "cubic-bezier(0.2, 0.9, 0.1, 1.0)"},
    ],
}


def _reticle_scene() -> dict:
    return {
        "design": {"width": 160, "height": 160},
        "namespaces": [{"name": "fc/lock", "state_engine": "json"}],
        "widgets": [{"type": "pixel_stack", "id": "st", "width": 160,
                     "height": 160, "children": [
            dict(type="circle", id="reticle", x=44, y=44, w=72, h=72,
                 radius=36, stroke_width=4, **_RETICLE)]}],
    }


# ===========================================================================
# Phase A
# ===========================================================================

def test_a1_cubic_bezier_matches_lottie_solver():
    """cubic-bezier(a,b,c,d) is evaluated by the SHARED Lottie solver — one
    solver, three consumers ([R3] reuse-by-reference)."""
    f = resolve_easing("cubic-bezier(0.2, 0.9, 0.1, 1.0)")
    for u in (0.0, 0.07, 0.19, 0.5, 0.63, 0.88, 1.0):
        assert f(u) == bezier_ease(u, 0.2, 0.9, 0.1, 1.0)


def test_a2_easing_aliases_and_precedence():
    """named entries win first; hyphenated CSS names resolve to beziers."""
    from composeio.render.engine.animation import EASING
    assert resolve_easing("ease_out") is EASING["ease_out"]   # our own curve
    assert resolve_easing("linear")(0.42) == 0.42
    # CSS ease-in-out is symmetric about 0.5
    ez = resolve_easing("ease-in-out")
    assert abs(ez(0.5) - 0.5) < 1e-6
    assert ez(0.0) == 0.0 and ez(1.0) == 1.0
    # unknown -> ease_out fallback (does not raise)
    assert callable(resolve_easing("nonsense"))


def test_a3_transition_is_render_frame_at_reproducible():
    """Deferred start-stamp: two independent apps driven with the IDENTICAL
    (publish, render_frame_at) sequence produce byte-identical frames — the
    determinism gate ([CXC-BIND-C6]) that a call-time stamp would break."""
    seq = [0.0, 0.05, 0.11, 0.18, 0.4]

    def run() -> list:
        app = _app(_reticle_scene())
        app.bus.publish_state("fc/lock", "tracking", source="t")
        hashes = []
        for t in seq:
            app.render_frame_at(t)
            hashes.append(_frame_hash(app))
        return hashes

    a, b = run(), run()
    assert a == b, "render_frame_at transition frames are not reproducible"
    # and the frames actually CHANGE through the transition (not a static scene)
    assert len(set(a)) > 1


def test_a4_damage_confined_to_animating_widget():
    """A tween records ONLY the widget it wrote on last_touched, so the
    runtime damages that sliver, not the scene ([CXC-R-DAMAGE])."""
    app = _app(_reticle_scene())
    app.bus.publish_state("fc/lock", "acquiring", source="t")
    app.drain_updates()
    app.behaviors.tick(0.0)
    app.states.tick(0.0)
    app.states.tick(0.05)                      # mid-transition write
    touched = {id(w) for w in app.states.last_touched}
    assert touched == {id(_w(app, "reticle"))}


def test_a5_delay_and_iteration_count():
    """delay_ms holds the from-value; iteration_count repeats the tween."""
    eng = StateEngine()

    class Dummy:
        def __init__(self):
            self.opacity = 0.0
            self._ctx = None
    d = Dummy()
    sm = parse_state_machine(
        {"states": {"a": {"opacity": 0.0}, "b": {"opacity": 1.0}},
         "state": {"namespace": "x", "default": "a"},
         "transitions": [{"animate": ["opacity"], "duration_ms": 100,
                          "easing": "linear", "delay_ms": 50,
                          "iteration_count": 2}]}, "d")
    sm.widget = d
    eng.add(sm)
    eng.set_state(sm, "a", animate=False)      # rest at a
    eng.set_state(sm, "b")                      # -> b, delayed 2x tween
    eng.tick(0.0)                               # stamp start=0
    eng.tick(0.02)                              # still inside the 50ms delay
    assert d.opacity == 0.0
    eng.tick(0.10)                              # 50ms in => halfway iter 1
    assert 0.4 < d.opacity < 0.6
    eng.tick(0.26)                              # past 2x100ms+delay => settled
    assert d.opacity == 1.0


# ===========================================================================
# Phase B
# ===========================================================================

def test_b1_bus_flip_drives_transition():
    app = _app(_reticle_scene())
    r = _w(app, "reticle")
    assert app.comp.state_machines[0].current == "idle"
    assert r.opacity == 0.0
    app.bus.publish_state("fc/lock", "acquiring", source="t")
    app.render_frame_at(0.0)                    # activation
    app.render_frame_at(0.30)                   # settle (>180ms)
    assert app.comp.state_machines[0].current == "acquiring"
    assert abs(r.opacity - 0.9) < 1e-6
    assert r.fill_color == (255, 204, 0, 255)


def test_b2_matched_tweens_unmatched_snaps():
    """opacity has a rule (tween); scale into `acquiring` has NO rule -> snaps
    immediately to the target."""
    app = _app(_reticle_scene())
    r = _w(app, "reticle")
    app.bus.publish_state("fc/lock", "acquiring", source="t")
    app.render_frame_at(0.0)                    # first transition frame
    assert r.scale == 1.2                       # snapped (no rule for this edge)
    assert r.opacity == 0.0                     # tween still at from at t=0
    app.render_frame_at(0.05)
    assert 0.0 < r.opacity < 0.9                # mid-tween


def test_b3_selector_and_state_is_selector_only_with_states():
    sm = parse_state_machine(
        {"states": {"a": {"opacity": 1.0}}, "state": "ns/sel",
         "transitions": None}, "w")
    assert sm.selector.namespace == "ns/sel"
    assert sm.selector.default == "a"           # first state as fallback
    # a lone `state:` (no states:) stays an ordinary attr binding (LED value)
    app = _app({
        "design": {"width": 80, "height": 40},
        "namespaces": [{"name": "led/on", "state_engine": "json"}],
        "widgets": [{"type": "led", "id": "l",
                     "state": {"namespace": "led/on"}}]})
    assert app.comp.state_machines == []
    assert any(b.namespace == "led/on" and b.attr == "state"
               for b in app.comp.attr_bindings)


def test_b4_reduced_motion_snaps():
    cfg = _reticle_scene()
    cfg["design"]["reduced_motion"] = True
    app = _app(cfg)
    r = _w(app, "reticle")
    app.bus.publish_state("fc/lock", "tracking", source="t")
    app.render_frame_at(0.0)                    # no tween window under RM
    assert app.comp.state_machines[0].active == []
    assert r.opacity == 1.0 and r.scale == 1.0
    assert r.fill_color == (255, 59, 48, 255)


def test_b5_boundary_refuses_inline_code(caplog):
    """A state config with an inline-code / arithmetic value is refused at the
    boundary ([CXC-BIND-BOUNDARY]); the clean states still parse."""
    with caplog.at_level(logging.WARNING):
        sm = parse_state_machine({
            "states": {
                "ok": {"opacity": 1.0},
                "bad": {"opacity": "{{ 1 + 1 }}"},   # templated -> whole map refused
            },
            "state": {"namespace": "x", "default": "ok"},
        }, "w")
    assert "ok" in sm.states
    assert "bad" not in sm.states                # refused, not executed


# ===========================================================================
# Phase C — gesture producers
# ===========================================================================

def _long_press(app, x, y):
    app._dispatch_pointer(_P("down", x, y))
    app._pointer_state[0]["t"] -= 1.0           # backdate to exceed long-press
    app._dispatch_pointer(_P("up", x, y))


def test_c1_gesture_publishes_semantic_state():
    app = RenderApp.from_file(EXAMPLE, windowed=False)
    app.start()
    _long_press(app, 240, 240)
    assert app.bus.get_state("fire_control/lock") == "acquiring"
    # a drag steers (@pointer coords) then commits the lock on release
    app._dispatch_pointer(_P("down", 240, 240))
    app._dispatch_pointer(_P("move", 300, 180))
    assert app.bus.get_state("reticle/x") == 300.0
    assert app.bus.get_state("reticle/y") == 180.0
    app._dispatch_pointer(_P("up", 300, 180))
    assert app.bus.get_state("fire_control/lock") == "tracking"


def test_c2_state_machine_consumes_the_gesture():
    app = RenderApp.from_file(EXAMPLE, windowed=False)
    app.start()
    _long_press(app, 240, 240)
    app.render_frame_at(0.0)
    sm = app.comp.state_machines[0]
    assert sm.current == "acquiring"


def test_c3_intelligence_parity_identical_trajectory():
    """A finger and an Intelligence reach the state machine through the SAME
    namespace, so the reticle's attr trajectory is identical ([CXC-RM-GESTURE]
    'swipe == finger' / control-is-a-peer)."""
    seq = [0.0, 0.06, 0.13, 0.30]

    def by_gesture():
        app = RenderApp.from_file(EXAMPLE, windowed=False); app.start()
        _long_press(app, 240, 240)              # finger -> lock=acquiring
        return _trajectory(app, seq)

    def by_intelligence():
        app = RenderApp.from_file(EXAMPLE, windowed=False); app.start()
        app.bus.publish_state("fire_control/lock", "acquiring", source="ai")
        return _trajectory(app, seq)

    assert by_gesture() == by_intelligence()


def _trajectory(app, seq):
    r = _w(app, "reticle")
    out = []
    for t in seq:
        app.render_frame_at(t)
        out.append((round(r.opacity, 6), round(r.scale, 6), r.fill_color
                    if hasattr(r, "fill_color") else r.stroke_color))
    return out
