"""
Interactive widget-primitive acceptance tests ([SPEC:CXC-PRIM]) — the two
composites that need a new interaction seam:

  slider   [CXC-PRIM-DRAGVAL] drag-to-value input (engine/drag_value.py) +
           the pure-composite visual (fill.w / thumb.x bound expressions on
           the published value).  Owner: Opus.
  dropdown [CXC-PRIM-POPUP]  popup item list reusing DialogWidget in
           list-select mode (widgets/dialog.py).

The four pure composites (progress_bar/switch/numpad/keyboard) are a
separate agent's suite (test_primitives_composites.py) — not touched here.

All tests run offscreen (windowed=False), no display required.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.drag_value import DragValueController
from composeio.render.engine.runtime import RenderApp
from composeio.render.widget import Rect

COMPONENTS = os.path.join(PY_ROOT, "composeio", "render", "examples",
                          "components")
SLIDER_YAML = os.path.join(COMPONENTS, "slider.compose.yaml")
DROPDOWN_YAML = os.path.join(COMPONENTS, "dropdown.compose.yaml")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakePointer:
    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = float(x), float(y)
        self.pointer_id = 0
        self.dx = self.dy = 0.0


def _drag(app, x0, y0, x1, y1):
    """Synthesize the down/move/up sequence the runtime turns into pan."""
    app._dispatch_pointer(_FakePointer("down", x0, y0))
    app._dispatch_pointer(_FakePointer("move", x1, y1))
    app._dispatch_pointer(_FakePointer("up", x1, y1))


def _tap(app, x, y):
    app._dispatch_pointer(_FakePointer("down", x, y))
    app._dispatch_pointer(_FakePointer("up", x, y))
    app.drain_updates()
    app.render_frame()


def _by_id(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}


def _drag_surface(app):
    """The slider's drag surface = the widget carrying the drag-value seam."""
    return next(w for w in app.comp.root.iter_widgets()
               if getattr(w, "_drag_value", None) is not None)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _slider_app(**overrides):
    """A slider composite with the {use:} params overridable per-test."""
    cfg = _load(SLIDER_YAML)
    use = cfg["widgets"][0]["children"][-1]      # the {use: slider} entry
    use.update(overrides)
    app = RenderApp.from_dict(cfg, windowed=False)
    app.start()
    app.drain_updates()
    app.render_frame()
    return app


# ---------------------------------------------------------------------------
# DragValueController — the mapping unit ([CXC-PRIM-DRAGVAL])
# ---------------------------------------------------------------------------

class _Surface:
    """Minimal drag surface: an arranged_rect + a capturing publish()."""
    def __init__(self, rect):
        self.arranged_rect = rect
        self.published = []

    def publish(self, ns, value):
        self.published.append((ns, value))


def test_dragvalue_maps_position_to_normalized_value():
    ctl = DragValueController("v/x", vmin=0.0, vmax=1.0, inset=0.0)
    surf = _Surface(Rect(0, 0, 200, 20))
    assert ctl.value_at(surf, 0, 10) == pytest.approx(0.0)
    assert ctl.value_at(surf, 100, 10) == pytest.approx(0.5)
    assert ctl.value_at(surf, 200, 10) == pytest.approx(1.0)
    # clamps beyond the ends
    assert ctl.value_at(surf, -50, 10) == pytest.approx(0.0)
    assert ctl.value_at(surf, 400, 10) == pytest.approx(1.0)


def test_dragvalue_respects_step_and_range():
    ctl = DragValueController("v/x", vmin=0.0, vmax=10.0, step=2.0)
    surf = _Surface(Rect(0, 0, 100, 20))
    # 37% of [0,10] = 3.7 -> nearest multiple of 2 = 4
    assert ctl.value_at(surf, 37, 10) == pytest.approx(4.0)
    # 96% = 9.6 -> 10 (clamped to max, a step multiple)
    assert ctl.value_at(surf, 96, 10) == pytest.approx(10.0)


def test_dragvalue_vertical_bottom_is_min():
    ctl = DragValueController("v/x", vmin=0.0, vmax=1.0, orient="vertical")
    surf = _Surface(Rect(0, 0, 20, 100))
    assert ctl.value_at(surf, 10, 100) == pytest.approx(0.0)   # bottom = min
    assert ctl.value_at(surf, 10, 0) == pytest.approx(1.0)     # top = max


# ---------------------------------------------------------------------------
# slider — live mode ([CXC-PRIM-C6])
# ---------------------------------------------------------------------------

def test_slider_live_drag_publishes_normalized_value():
    app = _slider_app(min=0, max=1, step=0.0, live_or_on_release="live")
    surface = _drag_surface(app)        # the slider track surface
    r = surface.arranged_rect
    # drag to the mid-point (accounting for the inset=9 registration)
    mid_x = r.x + r.w / 2
    _drag(app, r.x + 2, r.y + r.h / 2, mid_x, r.y + r.h / 2)
    assert app.bus.get_state("demo/volume") == pytest.approx(0.5, abs=0.02)
    app.stop()


def test_slider_live_publishes_during_pan_not_only_at_end():
    app = _slider_app(min=0, max=1, step=0.0, live_or_on_release="live")
    surface = _drag_surface(app)
    r = surface.arranged_rect
    seen = []
    orig = app.publish

    def spy(ns, value, **kw):
        if ns == "demo/volume":
            seen.append(value)
        return orig(ns, value, **kw)
    app.publish = spy
    # down + one move (no up): live mode must already have published twice
    app._dispatch_pointer(_FakePointer("down", r.x + 2, r.y + r.h / 2))
    app._dispatch_pointer(_FakePointer("move", r.x + r.w - 2, r.y + r.h / 2))
    assert len(seen) >= 2, f"live mode should publish on down+pan, got {seen}"
    app.stop()


def test_slider_step_quantization_end_to_end():
    app = _slider_app(min=0, max=10, step=1.0, live_or_on_release="live")
    surface = _drag_surface(app)
    r = surface.arranged_rect
    # aim for ~37% -> 3.7 -> snaps to 4
    target = r.x + 9 + 0.37 * (r.w - 18)
    _drag(app, r.x + 2, r.y + r.h / 2, target, r.y + r.h / 2)
    v = app.bus.get_state("demo/volume")
    assert v == pytest.approx(4.0), f"step=1 should snap, got {v}"
    app.stop()


# ---------------------------------------------------------------------------
# slider — on_release mode ([CXC-PRIM-DRAGVAL] two modes)
# ---------------------------------------------------------------------------

def test_slider_on_release_publishes_only_at_end():
    app = _slider_app(min=0, max=1, step=0.0, live_or_on_release="on_release")
    surface = _drag_surface(app)
    r = surface.arranged_rect
    start_val = app.bus.get_state("demo/volume")
    # down + move, but NOT up yet: value must be unchanged in on_release mode
    app._dispatch_pointer(_FakePointer("down", r.x + 2, r.y + r.h / 2))
    app._dispatch_pointer(_FakePointer("move", r.x + r.w - 2, r.y + r.h / 2))
    assert app.bus.get_state("demo/volume") == start_val, \
        "on_release must not publish mid-drag"
    # up commits the final value
    app._dispatch_pointer(_FakePointer("up", r.x + r.w - 2, r.y + r.h / 2))
    assert app.bus.get_state("demo/volume") == pytest.approx(1.0)
    app.stop()


# ---------------------------------------------------------------------------
# slider — the Intelligence-publishes-identically property ([CXC-PRIM-C6])
# ---------------------------------------------------------------------------

def test_slider_thumb_tracks_directly_published_value():
    """A finger drag and a direct bus publish that land on the SAME value put
    the thumb in the SAME place — the thumb is bound to the published value,
    so an Intelligence 'drags' by publishing ([CXC-PRIM-C6])."""
    app = _slider_app(min=0, max=1, step=0.0, live_or_on_release="live")
    ids = _by_id(app)
    thumb, surface = ids["thumb"], _drag_surface(app)
    r = surface.arranged_rect

    # (a) finger drag to ~0.7
    target = r.x + 9 + 0.70 * (r.w - 18)
    _drag(app, r.x + 2, r.y + r.h / 2, target, r.y + r.h / 2)
    app.drain_updates()
    app.render_frame()
    drag_value = app.bus.get_state("demo/volume")
    drag_thumb_x = thumb.arranged_rect.x
    assert drag_value == pytest.approx(0.70, abs=0.02)

    # (b) an Intelligence publishes the SAME value directly
    app.publish("demo/volume", drag_value)
    app.drain_updates()
    app.render_frame()
    assert thumb.arranged_rect.x == pytest.approx(drag_thumb_x, abs=0.001), \
        "a directly-published value must move the thumb identically to a drag"
    app.stop()


def test_slider_visual_is_pure_composite_no_new_widget_type():
    """[CXC-PRIM-C1] the slider is a components: expansion of stock leaves —
    no bespoke slider Widget subclass exists in the tree."""
    app = _slider_app()
    types = {w.widget_type for w in app.comp.root.iter_widgets()}
    assert "slider" not in types
    assert {"pixel_stack", "rectangle", "circle"} <= types
    app.stop()


# ---------------------------------------------------------------------------
# dropdown — open / select / dismiss ([CXC-PRIM-C7], [CXC-PRIM-POPUP])
# ---------------------------------------------------------------------------

def _dropdown_app():
    cfg = _load(DROPDOWN_YAML)
    app = RenderApp.from_dict(cfg, windowed=False)
    app.start()
    app.drain_updates()
    app.render_frame()
    return app


def test_dropdown_open_select_publishes_and_dismisses():
    app = _dropdown_app()
    ids = _by_id(app)
    popup, field_bg = ids["popup"], ids["field_bg"]
    assert popup.visible is False

    # field tap opens the popup (publishes the open bit)
    fr = field_bg.arranged_rect
    _tap(app, fr.x + fr.w / 2, fr.y + fr.h / 2)
    assert app.bus.get_state("ship/menu_open") == "on"
    assert popup.visible is True

    # tap an item row -> publishes the value + dismisses
    rows = popup._row_rects()
    assert [o for o, _ in rows] == ["Colonial One", "Galactica",
                                    "Pegasus", "Raptor 312"]
    opt, rr = rows[1]                              # Galactica
    _tap(app, rr.x + rr.w / 2, rr.y + rr.h / 2)
    assert app.bus.get_state("ship/selected") == "Galactica"
    assert popup.visible is False                  # dismissed on select
    app.stop()


def test_dropdown_outside_tap_dismisses_without_change():
    app = _dropdown_app()
    ids = _by_id(app)
    popup, field_bg = ids["popup"], ids["field_bg"]

    # seed a selection, then open
    app.publish("ship/selected", "Pegasus")
    app.drain_updates()
    fr = field_bg.arranged_rect
    _tap(app, fr.x + fr.w / 2, fr.y + fr.h / 2)
    assert popup.visible is True
    assert app.bus.get_state("ship/selected") == "Pegasus"

    # tap inside the scrim but OUTSIDE the panel -> dismiss, value unchanged
    d = popup.arranged_rect
    _tap(app, d.x + 3, d.y + 3)                    # top-left corner, above panel
    assert popup.visible is False
    assert app.bus.get_state("ship/selected") == "Pegasus", \
        "outside-tap dismiss must not change the selection"
    app.stop()


def test_dropdown_reuses_dialogwidget():
    """[CXC-PRIM-POPUP] the popup is a DialogWidget in list-select mode, not a
    second overlay system."""
    from composeio.render.widgets.dialog import DialogWidget
    app = _dropdown_app()
    popup = _by_id(app)["popup"]
    assert isinstance(popup, DialogWidget)
    assert popup.is_list is True
    app.stop()


def test_dropdown_intelligence_publish_updates_field_label():
    """The field label is bound to selected_ns, so a direct publish (an
    Intelligence choosing) updates the closed field identically to a tap."""
    app = _dropdown_app()
    label = _by_id(app)["field_value"]
    app.publish("ship/selected", "Raptor 312")
    app.drain_updates()
    app.render_frame()
    assert label.text == "Raptor 312"
    app.stop()
