"""
[SPEC:CXC-GESTURE] Single-touch 4-way swipe gestures — compliance tests.

Swipe is ADDITIVE to the M1 interaction engine (tap / long-press / pan): a
qualifying single-touch flick is *also* dispatched as a directional swipe;
sub-threshold sequences remain exactly the tap/long-press/pan they already
were.  These tests assert both halves:

  CXC-GEST-C1  down->move->up over threshold classifies up/down/left/right;
               sub-threshold stays pan (no swipe)
  CXC-GEST-C2  tap, long-press, and pan still fire unchanged alongside swipe
  CXC-GEST-C3  a recognized swipe publishes the direction to swipe_namespace
  CXC-GEST-C5  an injected (synthesized) swipe drives the surface identically
               to a physical flick — the whole test IS synthesized input

Run:  python3 -m pytest tests/render/test_gestures_swipe.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine import runtime as rt
from composeio.render.engine.runtime import RenderApp, _classify_swipe


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakePointer:
    """Mirrors the fake used by the M1/primitive suites."""

    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = float(x), float(y)
        self.pointer_id = 0
        self.dx = self.dy = 0.0


def _seq(app, x0, y0, x1, y1):
    """Synthesize a down -> move -> up flick from (x0,y0) to (x1,y1)."""
    app._dispatch_pointer(_FakePointer("down", x0, y0))
    app._dispatch_pointer(_FakePointer("move", x1, y1))
    app._dispatch_pointer(_FakePointer("up", x1, y1))


def _swipe_app(swipe_ns="demo/swipe", tap_ns="demo/tap"):
    """A full-surface interactive rect that binds BOTH a tap publish and a
    swipe publish, so one widget exercises tap-vs-swipe coexistence.  Wrapped
    in a pixel_stack so the rect's x/y/w/h are absolute px (a genuine 400x400
    hit surface)."""
    config = {
        "design": {"width": 400, "height": 400},
        "namespaces": [
            {"name": swipe_ns, "state_engine": "json"},
            # json (not binary): publish_value "toggle" emits numeric 0/1,
            # which the binary engine's ['off','on'] domain rejects — the
            # documented toggle<->binary gap ([SPEC:CXC-PRIM] SUMMARY), not
            # under test here; we only need a togglable tap sink.
            {"name": tap_ns, "state_engine": "json", "initial": 0},
        ],
        "widgets": [{
            "type": "pixel_stack", "id": "root_stack",
            "width": 400, "height": 400,
            "children": [{
                "type": "rect", "id": "surface",
                "x": 0, "y": 0, "w": 400, "h": 400,
                "fill_color": "#222233",
                "swipe_namespace": swipe_ns,
                "publish_namespace": tap_ns,     # tap toggles a binary
            }],
        }],
    }
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    app.render_frame()          # arrange so the rect has an arranged_rect
    return app


def _center(app):
    surface = next(w for w in app.comp.root.iter_widgets()
                   if w.widget_id == "surface")
    r = surface.arranged_rect
    return (r.x + r.w / 2, r.y + r.h / 2)


# ---------------------------------------------------------------------------
# _classify_swipe — the pure classifier ([CXC-GEST-SWIPE])
# ---------------------------------------------------------------------------

def _st(x, y):
    return {"x": float(x), "y": float(y)}


def test_classify_four_directions():
    """CXC-GEST-C1: each cardinal flick classifies to its direction (an
    instantaneous synthesized flick => elapsed 0 => velocity check passes)."""
    st = _st(200, 200)
    assert _classify_swipe(st, 300, 200, 0.0) == "right"
    assert _classify_swipe(st, 100, 200, 0.0) == "left"
    assert _classify_swipe(st, 200, 300, 0.0) == "down"
    assert _classify_swipe(st, 200, 100, 0.0) == "up"


def test_classify_short_travel_is_not_swipe():
    """Under _SWIPE_MIN_DISTANCE stays a pan, whatever the speed."""
    assert _classify_swipe(_st(200, 200), 215, 200, 0.0) is None


def test_classify_slow_drag_is_not_swipe():
    """Over _SWIPE_MAX_MS (a deliberate drag) is a pan, not a flick, even for
    a long, clearly-axial travel."""
    assert _classify_swipe(_st(200, 200), 320, 200,
                           rt._SWIPE_MAX_MS + 50.0) is None


def test_classify_low_velocity_is_not_swipe():
    """Below _SWIPE_MIN_VELOCITY (far enough, but too slow) is a pan."""
    # 60px over 550ms = 0.109 px/ms < 0.12 default; still within max_ms.
    assert _classify_swipe(_st(200, 200), 260, 200, 550.0) is None


def test_classify_diagonal_is_rejected():
    """A path that is not dominantly axial (45deg) names no direction."""
    assert _classify_swipe(_st(200, 200), 260, 260, 0.0) is None


# ---------------------------------------------------------------------------
# end-to-end through the runtime ([CXC-GEST-C3], [CXC-GEST-C5])
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dx,dy,expect", [
    (120, 0, "right"),
    (-120, 0, "left"),
    (0, 120, "down"),
    (0, -120, "up"),
])
def test_swipe_publishes_direction_to_namespace(dx, dy, expect):
    """CXC-GEST-C3/C5: a synthesized flick publishes its direction to the
    widget's swipe_namespace — the finger-identical, Intelligence-observable
    bus event."""
    app = _swipe_app()
    try:
        cx, cy = _center(app)
        _seq(app, cx, cy, cx + dx, cy + dy)
        assert app.bus.get_state("demo/swipe") == expect
        # and the interaction event carries the direction ([CXC-GEST-C3])
        last = app._interaction_log[-1]
        assert last["type"] == "swipe"
        assert last["direction"] == expect
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# additivity — tap / long-press / pan unaffected ([CXC-GEST-C2])
# ---------------------------------------------------------------------------

def test_tap_still_fires_and_is_not_a_swipe():
    """A down/up with no move remains a tap (toggles the binary) and never
    publishes a swipe."""
    app = _swipe_app()
    try:
        cx, cy = _center(app)
        app._dispatch_pointer(_FakePointer("down", cx, cy))
        app._dispatch_pointer(_FakePointer("up", cx, cy))
        assert app.bus.get_state("demo/tap") == 1            # toggled 0 -> 1
        assert app.bus.get_state("demo/swipe") is None       # no swipe fired
        assert app._interaction_log[-1]["type"] == "tap"
    finally:
        app.stop()


def test_pan_still_fires_and_short_drag_is_not_a_swipe():
    """A drag past the pan threshold but under the swipe distance still fires
    the full pan lifecycle and does NOT publish a swipe."""
    app = _swipe_app()
    try:
        surface = next(w for w in app.comp.root.iter_widgets()
                       if w.widget_id == "surface")
        calls = []
        surface.on_pan_start = lambda: calls.append("start")   # type: ignore
        surface.on_pan = lambda dx, dy: calls.append(("pan", dx, dy))  # type: ignore
        surface.on_pan_end = lambda: calls.append("end")       # type: ignore

        cx, cy = _center(app)
        # 15px move: > _TAP_MAX_MOVE (pan) but < _SWIPE_MIN_DISTANCE (no swipe)
        _seq(app, cx, cy, cx + 15, cy)

        assert "start" in calls and "end" in calls
        assert any(c[0] == "pan" for c in calls if isinstance(c, tuple))
        assert app.bus.get_state("demo/swipe") is None
    finally:
        app.stop()


def test_long_press_still_fires_and_is_not_a_swipe(monkeypatch):
    """A no-move press held past _LONG_PRESS_MS remains a long-press (swipe
    only classifies moved sequences, so it never interferes)."""
    app = _swipe_app()
    try:
        # controlled clock: down at t=0, up at t=0.6s (600ms held, no move)
        ticks = iter([0.0, 0.6])
        cx, cy = _center(app)
        monkeypatch.setattr(rt.time, "monotonic",
                            lambda: next(ticks))
        app._dispatch_pointer(_FakePointer("down", cx, cy))
        app._dispatch_pointer(_FakePointer("up", cx, cy))
        assert app._interaction_log[-1]["type"] == "long_press"
        assert app.bus.get_state("demo/swipe") is None
    finally:
        app.stop()
