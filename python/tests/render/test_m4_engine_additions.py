"""
M4 engine-additions tests ([CXC-RM-M4]):

  1. Grid row/column weights (+ flex grow) — closes CTk-retirement
     divergence #3: default stays top-pack / legacy stretch; a config can
     request weighted spreading.
  2. Declarative ``visible:`` binding — any widget's visible attribute
     bound to a namespace from .compose.yaml (Red-Alert Image toggle).
  3. DialogWidget — engine-rendered modal: scrim + centered panel +
     z-top in a pixel stack + modal hit-capture.

Goldens live in reference_images/ and regenerate via:
    CXC_REGEN_GOLDENS=1 python -m pytest tests/render/test_m4_engine_additions.py
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import DialogWidget, LabelWidget, RectangleWidget
from composeio.render.engine.layout import (FlexContainer, GridContainer,
                                            PixelStackContainer)
from composeio.render.engine.runtime import RenderApp

from .conftest import REFERENCE_DIR, images_match, load_reference, surface_to_array


def _rect_child(wid, w=60, h=20, color=(0x26, 0xA6, 0x9A)):
    r = RectangleWidget(wid, width=w, height=h, fill_color=(*color, 255))
    return r


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


# ---------------------------------------------------------------------------
# 1. Grid weights + flex grow
# ---------------------------------------------------------------------------

def _arrange_grid(grid, w=300, h=300):
    grid.measure(Constraints(w, h))
    grid.arrange(Rect(0, 0, w, h))
    return grid


def test_grid_default_unweighted_is_legacy_equal_bonus():
    """No weights -> the pre-M4 behavior is untouched (stretch equal bonus)."""
    g = GridContainer("g", stretch=True)
    for i in range(3):
        ch = _rect_child(f"r{i}", h=20)
        ch.grid_row, ch.grid_col = i, 0
        g.add(ch)
    _arrange_grid(g, 100, 300)
    heights = [ch.arranged_rect.h for ch in g.children]
    assert heights == pytest.approx([100, 100, 100])


def test_grid_row_weights_equal_spreads_rows():
    g = GridContainer("g", stretch=False, row_weights="equal")
    for i in range(3):
        ch = _rect_child(f"r{i}", h=20)
        ch.grid_row, ch.grid_col = i, 0
        g.add(ch)
    _arrange_grid(g, 100, 300)
    heights = [ch.arranged_rect.h for ch in g.children]
    assert heights == pytest.approx([100, 100, 100])


def test_grid_row_weights_proportional_and_zero_tracks():
    """dict weights: leftover splits 1:3; weight-0 track keeps measured size."""
    g = GridContainer("g", stretch=False, row_weights={0: 1, 1: 3, 2: 0})
    for i in range(3):
        ch = _rect_child(f"r{i}", h=20)
        ch.grid_row, ch.grid_col = i, 0
        g.add(ch)
    g.gap = 0
    _arrange_grid(g, 100, 300)
    heights = [ch.arranged_rect.h for ch in g.children]
    # measured 20 each; leftover 240 split 60/180/0
    assert heights == pytest.approx([80, 200, 20])


def test_grid_all_zero_weights_top_packs():
    g = GridContainer("g", stretch=True, row_weights={0: 0, 1: 0})
    for i in range(2):
        ch = _rect_child(f"r{i}", h=20)
        ch.grid_row, ch.grid_col = i, 0
        g.add(ch)
    _arrange_grid(g, 100, 300)
    heights = [ch.arranged_rect.h for ch in g.children]
    assert heights == pytest.approx([20, 20])       # no spread at all


def test_grid_column_weights_list():
    g = GridContainer("g", stretch=False, col_weights=[1, 2])
    for i in range(2):
        ch = _rect_child(f"c{i}", w=40)
        ch.grid_row, ch.grid_col = 0, i
        g.add(ch)
    g.gap = 0
    _arrange_grid(g, 380, 100)
    widths = [ch.arranged_rect.w for ch in g.children]
    # measured 40 each; leftover 300 split 100/200
    assert widths == pytest.approx([140, 240])


def test_flex_grow_distributes_free_space():
    """CSS `flex: N` semantics: grow children have basis 0 and share the
    free main axis proportionally; fixed children keep their size."""
    f = FlexContainer("f", direction="row")
    a, b = _rect_child("a", w=50), _rect_child("b", w=50)
    fixed = _rect_child("fx", w=100)
    a.layout.grow = 2.0
    b.layout.grow = 1.0
    f.add(a)
    f.add(b)
    f.add(fixed)
    f.measure(Constraints(400, 100))
    f.arrange(Rect(0, 0, 400, 100))
    # pool = 400 - 100 fixed = 300, split 200/100
    assert a.arranged_rect.w == pytest.approx(200)
    assert b.arranged_rect.w == pytest.approx(100)
    assert b.arranged_rect.x == pytest.approx(200)
    assert fixed.arranged_rect.w == pytest.approx(100)


def test_grid_weights_via_parser_nested_dialect():
    """row_weights parses from the nested .compose.yaml dialect."""
    from composeio.render.engine.parser import parse_widget_tree
    config = {
        "design": {"width": 200, "height": 240},
        "widgets": [{
            "type": "grid", "id": "wg", "stretch": False,
            "row_weights": "equal",
            "children": [
                {"type": "rect", "id": "r0", "width": 60, "height": 20, "row": 0},
                {"type": "rect", "id": "r1", "width": 60, "height": 20, "row": 1},
            ],
        }],
    }
    comp = parse_widget_tree(config)
    grid = next(w for w in comp.root.iter_widgets()
                if w.widget_id == "wg")
    assert grid.row_weights == "equal"


def test_grid_weights_golden():
    """Pixel-pinned: unweighted top-pack column vs equal-weight spread."""
    c = SkiaCanvas()
    c.create_surface(320, 240)
    c.set_fill_color(0x1A, 0x1A, 0x1A)
    c.fill_rect(0, 0, 320, 240)

    left = GridContainer("gl", stretch=True, row_weights={0: 0, 1: 0, 2: 0})
    right = GridContainer("gr", stretch=False, row_weights="equal")
    for g, x0 in ((left, 10), (right, 170)):
        g.gap = 6
        for i in range(3):
            ch = _rect_child(f"{g.widget_id}{i}", w=130, h=24,
                             color=(0x1F, 0x53, 0x8D) if g is left
                             else (0x26, 0xA6, 0x9A))
            ch.grid_row = i
            g.add(ch)
        g.measure(Constraints(140, 220))
        g.arrange(Rect(x0, 10, 140, 220))
        g.draw(c, g.arranged_rect)
    _golden(c, "m4_grid_weights")


# ---------------------------------------------------------------------------
# 2. Declarative visible: binding (Red-Alert toggle)
# ---------------------------------------------------------------------------

def _red_alert_app(tmp_path):
    """A pixel-stack with a base rect and a red-alert Image bound to
    red_alert (binary): visible only while the alarm is on."""
    # deterministic alert PNG generated by the reference backend
    src = SkiaCanvas()
    src.create_surface(120, 60)
    src.set_fill_color(0xE0, 0x3B, 0x3B)
    src.fill_round_rect(0, 0, 120, 60, 8)
    src.set_fill_color(0xFF, 0xFF, 0xFF)
    src.set_font("sans-serif", 16, "bold")
    src.draw_text("RED ALERT", 60, 38, "center")
    png = os.path.join(str(tmp_path), "red_alert.png")
    src.save_png(png)

    config = {
        "design": {"width": 320, "height": 240, "title": "red-alert"},
        "namespaces": [{"name": "ship/red_alert", "state_engine": "binary"}],
        "widgets": [{
            "type": "pixel_stack", "id": "scene",
            "width": 320, "height": 240, "background": "#101318",
            "children": [
                {"type": "rect", "id": "hull", "x": 20, "y": 20,
                 "w": 280, "h": 200, "fill_color": "#28283C",
                 "corner_radius": 10},
                {"type": "image", "id": "alert_img",
                 "src": "file://" + png,
                 "x": 100, "y": 90, "w": 120, "h": 60,
                 "visible": "ship/red_alert"},
            ],
        }],
    }
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def test_visible_binding_red_alert_toggle(tmp_path):
    app = _red_alert_app(tmp_path)
    try:
        img = next(w for w in app.comp.root.iter_widgets()
                   if w.widget_id == "alert_img")
        assert img.visible is False               # hidden until alarm

        app.publish("ship/red_alert", "on")
        app.render_frame()
        assert img.visible is True

        app.publish("ship/red_alert", "off")
        app.render_frame()
        assert img.visible is False
    finally:
        app.stop()


@pytest.mark.parametrize("state", ["off", "on"])
def test_visible_binding_golden(tmp_path, state):
    app = _red_alert_app(tmp_path)
    try:
        app.publish("ship/red_alert", state)
        app.render_frame()
        _golden(app.canvas, f"m4_red_alert_{state}")
    finally:
        app.stop()


def test_visible_binding_when_and_invert():
    from composeio.render.engine.parser import parse_widget_tree
    config = {
        "design": {"width": 200, "height": 100},
        "widgets": [
            {"type": "rect", "id": "warn", "width": 40, "height": 20,
             "visible": {"namespace": "sys/status", "when": "red"}},
            {"type": "rect", "id": "ok", "width": 40, "height": 20,
             "visible": {"namespace": "sys/status", "when": "red",
                         "invert": True, "initial": True}},
        ],
    }
    app = RenderApp(parse_widget_tree(config), windowed=False)
    app.start()
    try:
        warn = next(w for w in app.comp.root.iter_widgets()
                    if w.widget_id == "warn")
        ok = next(w for w in app.comp.root.iter_widgets()
                  if w.widget_id == "ok")
        app.publish("sys/status", "green")
        app.render_frame()
        assert warn.visible is False and ok.visible is True
        app.publish("sys/status", "red")
        app.render_frame()
        assert warn.visible is True and ok.visible is False
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# 3. DialogWidget
# ---------------------------------------------------------------------------

def _dialog_app():
    config = {
        "design": {"width": 360, "height": 260, "title": "dialog"},
        "namespaces": [{"name": "ui/confirm_open", "state_engine": "binary"}],
        "widgets": [{
            "type": "pixel_stack", "id": "scene",
            "width": 360, "height": 260, "background": "#1A1A1A",
            "children": [
                {"type": "button", "id": "btn_arm", "label": "Arm",
                 "x": 20, "y": 20, "w": 150, "h": 36,
                 "publish_namespace": "ship/arm"},
                {"type": "rect", "id": "deck", "x": 20, "y": 70,
                 "w": 320, "h": 170, "fill_color": "#28283C",
                 "corner_radius": 8},
                {"type": "dialog", "id": "dlg", "title": "Confirm",
                 "namespace": "ui/confirm_open", "panel_width": 240,
                 "children": [
                     {"type": "label", "text": "Really fire the torpedo?",
                      "font_size": 13},
                     {"type": "button", "id": "dlg_yes", "label": "Fire",
                      "publish_namespace": "ship/fire"},
                 ]},
            ],
        }],
    }
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def test_dialog_visibility_and_hit_capture():
    app = _dialog_app()
    try:
        root = app.comp.root
        dlg = next(w for w in root.iter_widgets() if w.widget_id == "dlg")
        btn = next(w for w in root.iter_widgets() if w.widget_id == "btn_arm")

        # closed: the button behind is hittable
        assert dlg.visible is False
        hit = root.hit_test_tree(60, 40)
        assert hit is btn

        # open via the bus (visible-bound modal)
        app.publish("ui/confirm_open", "on")
        app.render_frame()
        assert dlg.visible is True

        # modal capture: the same point now resolves to the dialog (scrim)
        hit = root.hit_test_tree(60, 40)
        assert hit is dlg

        # panel child is reachable
        yes = next(w for w in root.iter_widgets() if w.widget_id == "dlg_yes")
        yr = yes.arranged_rect
        hit = root.hit_test_tree(yr.x + yr.w / 2, yr.y + yr.h / 2)
        assert hit is yes

        # scrim tap dismisses and publishes off
        dlg.on_tap(60, 40)
        app.render_frame()
        assert dlg.visible is False
        assert str(app.bus.get_state("ui/confirm_open")) in ("off", "0", "False")
    finally:
        app.stop()


@pytest.mark.parametrize("state", ["closed", "open"])
def test_dialog_golden(state):
    app = _dialog_app()
    try:
        if state == "open":
            app.publish("ui/confirm_open", "on")
        app.render_frame()
        _golden(app.canvas, f"m4_dialog_{state}")
    finally:
        app.stop()
