"""
M0+M1 acceptance tests for the ComposeUX render engine.

Maps to roadmap acceptance IDs:
  CXC-R-C2   backend passes the conformance suite (test_conformance_*)
  CXC-R-C3   .compose.yaml -> correct layout + data binding (test_parse_*)
  CXC-R-C4   bus value change updates widget within one frame
  CXC-R-C6   click dispatches to the correct (topmost) widget
  CXC-R-C7   layout engine produces correct positions
  CXC-R-C10  config change re-renders
  CXC-R-C11  animation frame budget (headless render loop >= 60 fps)

Windowed (SDL) behavior is covered by the separate interactive smoke
script (smoke_live_window.py) because CI may be headless; everything
here runs without a display.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.conformance import (CAP_CYD, CAP_FULL, SCENES,
                                          verify_backend)
from composeio.render.engine.layout import (FlexContainer, GridContainer,
                                            PixelStackContainer)
from composeio.render.engine.runtime import INTERACTION_NS, RenderApp
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import GaugeWidget, LEDWidget

DEMO = os.path.join(PY_ROOT, "composeio", "render", "examples",
                    "render_demo.compose.yaml")


# ---------------------------------------------------------------------------
# CXC-R-C2 — conformance
# ---------------------------------------------------------------------------

def test_conformance_skia_reference(tmp_path):
    results = verify_backend(SkiaCanvas, CAP_FULL, out_dir=str(tmp_path))
    failed = {k: v for k, v in results.items() if v["status"] == "FAIL"}
    assert not failed, f"non-conformant scenes: {failed}"
    assert all(v["status"] == "PASS" for v in results.values())


def test_conformance_cyd_subset_skips_unimplemented(tmp_path):
    """The CYD capability set must SKIP (not FAIL) fill_arc/paths/transforms."""
    results = verify_backend(SkiaCanvas, CAP_CYD, out_dir=str(tmp_path))
    assert results["fill_arcs"]["status"] == "SKIP"
    assert results["paths"]["status"] == "SKIP"
    assert results["transform_clip"]["status"] == "SKIP"
    assert results["rects"]["status"] == "PASS"


# ---------------------------------------------------------------------------
# CXC-R-C7 — layout positions
# ---------------------------------------------------------------------------

def _rect_of(app, wid):
    for w in app.comp.root.iter_widgets():
        if w.widget_id == wid:
            return w.arranged_rect
    raise AssertionError(f"widget {wid} not found")


def test_flex_row_gap_positions():
    """Container with 3 children, gap=8, direction=row -> correct x offsets."""
    from composeio.render.widgets import RectangleWidget
    row = FlexContainer("row", direction="row")
    row.gap = 8
    for i in range(3):
        row.add(RectangleWidget(f"r{i}", width=50, height=40))
    row.measure(Constraints(400, 100))
    row.arrange(Rect(0, 0, 400, 100))
    xs = [c.arranged_rect.x for c in row.children]
    assert xs == [0, 58, 116]
    assert all(c.arranged_rect.w == 50 for c in row.children)


def test_grid_cell_positions():
    from composeio.render.widgets import RectangleWidget
    grid = GridContainer("g", stretch=False)
    grid.gap = 10
    cells = {}
    for r in range(2):
        for c in range(2):
            w = RectangleWidget(f"g{r}{c}", width=60, height=30)
            w.grid_row, w.grid_col = r, c
            grid.add(w)
            cells[(r, c)] = w
    grid.measure(Constraints(500, 500))
    grid.arrange(Rect(0, 0, 500, 500))
    assert (cells[(0, 0)].arranged_rect.x, cells[(0, 0)].arranged_rect.y) == (0, 0)
    assert cells[(0, 1)].arranged_rect.x == 70          # 60 + gap
    assert cells[(1, 0)].arranged_rect.y == 40          # 30 + gap


def test_pixel_stack_absolute_and_fractional():
    from composeio.render.widgets import RectangleWidget
    ps = PixelStackContainer("ps", canvas_width=400, canvas_height=200)
    a = RectangleWidget("a", width=50, height=50)
    a.stack_x, a.stack_y = 10, 20
    b = RectangleWidget("b", width=50, height=50)
    b.stack_x, b.stack_y = "50%", "50%"
    b.stack_w, b.stack_h = 80, 40
    ps.add(a).add(b)
    ps.arrange(Rect(0, 0, 400, 200))
    assert (a.arranged_rect.x, a.arranged_rect.y) == (10, 20)
    assert (b.arranged_rect.x, b.arranged_rect.y) == (200, 100)
    assert (b.arranged_rect.w, b.arranged_rect.h) == (80, 40)


# ---------------------------------------------------------------------------
# CXC-R-C3 — parse -> layout + binding
# ---------------------------------------------------------------------------

def test_parse_demo_composition():
    app = RenderApp.from_file(DEMO, windowed=False)
    ids = {w.widget_id for w in app.comp.root.iter_widgets()}
    for expected in ("composite", "z3_rpm", "z4_led", "temp_gauge",
                     "status_led", "shape_grid"):
        assert expected in ids
    bound = {ns for ns, _ in app.comp.subscriptions}
    assert {"engine_temp", "engine_rpm", "system_status",
            "status_text"} <= bound
    app.stop() if app.canvas else None


def test_parse_production_dialect():
    """The existing production modules: dialect loads and lays out."""
    root_yaml = os.path.join(PY_ROOT, os.pardir,
                             "test_dashboard.compose.yaml")
    if not os.path.isfile(root_yaml):
        pytest.skip("production sample not present")
    app = RenderApp.from_file(root_yaml, windowed=False)
    app.start()
    types = {w.widget_type for w in app.comp.root.iter_widgets()}
    assert "gauge" in types            # GaugeWidget mapped for real
    # M2 parity: the production widget set renders for real — no more
    # PlaceholderWidgets for ported types ([CXC-RM-M2] step 2).
    assert "button" in types
    assert "relay" in types
    assert "placeholder" not in types
    app.stop()


# ---------------------------------------------------------------------------
# CXC-R-C4 — bus change -> visual change within one frame
# ---------------------------------------------------------------------------

def test_bus_value_updates_widget_within_one_frame(tmp_path):
    app = RenderApp.from_file(DEMO, windowed=False)
    app.start()
    app.render_frame()
    gauge = next(w for w in app.comp.root.iter_widgets()
                 if w.widget_id == "temp_gauge")
    assert gauge.value == 20

    app.publish("engine_temp", 88.5, source="test")
    # M2: subscriptions marshal through the update queue (bus callbacks
    # may arrive on transport threads); the queue drains at the top of
    # the very next frame — still "within one frame" per CXC-R-C4.
    assert app._dirty, "bus change must mark the composition dirty"
    app.drain_updates()
    assert gauge.value == 88.5

    before = os.path.join(str(tmp_path), "before.png")
    app.canvas.save_png(before)
    app.render_frame()                  # exactly one frame
    after = os.path.join(str(tmp_path), "after.png")
    app.canvas.save_png(after)

    sys.path.insert(0, os.path.join(PY_ROOT, "scripts"))
    from pixel_comparison import pixel_equivalency
    stats = pixel_equivalency(before, after, output_dir=str(tmp_path))
    assert stats["identical_pct"] < 100.0, \
        "frame after bus change must differ from frame before"
    app.stop()


# ---------------------------------------------------------------------------
# CXC-R-C6 — hit-test dispatches to the topmost widget
# ---------------------------------------------------------------------------

class _FakePointer:
    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = x, y
        self.pointer_id = 0
        self.dx = self.dy = 0.0


def _click(app, x, y):
    app._dispatch_pointer(_FakePointer("down", x, y))
    app._dispatch_pointer(_FakePointer("up", x, y))


def test_click_dispatches_to_topmost_widget():
    app = RenderApp.from_file(DEMO, windowed=False)
    app.start()
    app.render_frame()

    led = next(w for w in app.comp.root.iter_widgets()
               if w.widget_id == "z4_led")
    r = led.arranged_rect
    cx, cy = r.x + r.w / 2, r.y + r.h / 2

    # the LED overlaps the gauge (z3) — the click must hit the LED (z4)
    target = app.comp.root.hit_test_tree(cx, cy)
    assert target is led, f"expected z4_led, got {target and target.widget_id}"

    # full dispatch: tap -> publish toggle -> bus -> widget state within a frame
    assert led.state == "off"
    _click(app, cx, cy)
    assert app.bus.get_state("system_status") == 1
    app.drain_updates()                # bus -> widget marshals via the queue
    assert led.state == "on"           # round-trip through the bus

    # interaction event published for Intelligence observers ([CXC-R-CONTROL])
    evt = app.bus.get_state(INTERACTION_NS)
    assert evt["type"] == "tap" and evt["widget_id"] == "z4_led"
    app.stop()


def test_click_on_gauge_beside_led_hits_gauge_region():
    """A click inside the gauge but outside the LED must NOT hit the LED."""
    app = RenderApp.from_file(DEMO, windowed=False)
    app.start()
    app.render_frame()
    gauge = next(w for w in app.comp.root.iter_widgets()
                 if w.widget_id == "z3_rpm")
    r = gauge.arranged_rect
    # bottom-left corner of the gauge, far from the LED at its top-right
    target = app.comp.root.hit_test_tree(r.x + 5, r.y + r.h - 5)
    assert target is not next(w for w in app.comp.root.iter_widgets()
                              if w.widget_id == "z4_led")
    app.stop()


# ---------------------------------------------------------------------------
# CXC-R-C10 — config change re-renders
# ---------------------------------------------------------------------------

def test_config_change_rerenders(tmp_path):
    import yaml
    with open(DEMO, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    app = RenderApp.from_dict(cfg, windowed=False)
    app.start()
    app.render_frame()
    p1 = os.path.join(str(tmp_path), "cfg1.png")
    app.canvas.save_png(p1)
    app.stop()

    # mutate the config: recolor the z2 panel
    for w in cfg["widgets"][1]["children"][1]["children"]:
        if w.get("id") == "z2_panel":
            w["fill_color"] = [0xEF, 0x53, 0x50, 200]
    app2 = RenderApp.from_dict(cfg, windowed=False)
    app2.start()
    app2.render_frame()
    p2 = os.path.join(str(tmp_path), "cfg2.png")
    app2.canvas.save_png(p2)
    app2.stop()

    sys.path.insert(0, os.path.join(PY_ROOT, "scripts"))
    from pixel_comparison import pixel_equivalency
    stats = pixel_equivalency(p1, p2, output_dir=str(tmp_path))
    assert stats["identical_pct"] < 100.0


# ---------------------------------------------------------------------------
# CXC-R-C11 — animation at frame budget
# ---------------------------------------------------------------------------

def test_animation_engine_interpolates_and_paces():
    app = RenderApp.from_file(DEMO, windowed=False)
    app.start()
    gauge = next(w for w in app.comp.root.iter_widgets()
                 if w.widget_id == "z3_rpm")
    gauge.animate("value", 0, 6000, duration=300, easing="ease_out")
    t0 = time.monotonic()
    frames = 0
    while app.animator.active and time.monotonic() - t0 < 2.0:
        app.animator.tick()
        app.render_frame()
        frames += 1
    elapsed = time.monotonic() - t0
    assert gauge.value == 6000
    fps = frames / elapsed if elapsed > 0 else 0
    # 60fps budget: the render loop must sustain >= 60 full frames/sec
    assert fps >= 60, f"render loop too slow for 60fps: {fps:.1f} fps"
    app.stop()


def test_easing_functions_monotone_endpoints():
    from composeio.render.engine.animation import EASING
    for name, fn in EASING.items():
        assert abs(fn(0.0)) < 1e-6, name
        if name != "spring":
            assert abs(fn(1.0) - 1.0) < 1e-6, name


# ---------------------------------------------------------------------------
# Focus scaffolding
# ---------------------------------------------------------------------------

def test_focus_tab_cycles():
    from composeio.render.engine.focus import FocusManager
    app = RenderApp.from_file(DEMO, windowed=False)
    app.start()
    widgets = [w for w in app.comp.root.iter_widgets()]
    for w in widgets[:3]:
        w.accepts_focus = True
    fm = FocusManager()
    fm.collect(app.comp.root)
    fm.handle_key("Tab")
    first = fm.focused
    fm.handle_key("Tab")
    second = fm.focused
    assert first is not None and second is not None and first is not second
    app.stop()
