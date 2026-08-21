"""
SevenSegmentDisplay tests ([CXC-SEVEN-SEG] compliance).

  C1  value -> segments truth table: each character lights exactly the
      standard seven-segment set (digits 0-9, hex A-F, blank, '-')
  C2  SVG source of truth: the seven named segments (a..g) are FOUND in
      the project SVG by label and their union design box is derived —
      no hardcoded geometry
  C3  lit/dim rendering: a drawn glyph fills every found segment, lit ones
      in the lit color and the rest in the dim color (recording canvas)
  C4  color binding: publishing the color namespace recolors the lit
      segments (and re-derives dim); an explicit dim is preserved
  C5  clock substrate: on_clock_tick fills the value from the (injectable)
      time source, redraws only when the displayed second changes
  C6  colon art: a ':' renders from the colon SVG, recolored to the lit
      color (SVG source of truth, same treatment as the segments)
  C7  multi-digit layout: value is a string; fixed `digits` right-justify
      with blank ghost padding / rightmost truncation
  C8  parser: a scene builds the real widget (not a placeholder) and wires
      the color/value namespace subscriptions
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.canvas import Canvas, Size, TextMetrics
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.svg import SvgDocument
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets.seven_segment import (
    SEGMENT_LABELS, SevenSegmentDisplay, _shape_design_bbox, colon_bbox,
    find_colon_shapes, find_segments, lit_segments, segment_top_y,
    segments_bbox)

ART_DIR = os.path.join(
    PY_ROOT, "composeio", "render", "examples", "assets")
SEG_SVG = os.path.join(ART_DIR, "seven_segment.svg")
COLON_SVG = os.path.join(ART_DIR, "seven_segment_colon.svg")


@pytest.fixture(scope="module")
def seg_doc() -> SvgDocument:
    with open(SEG_SVG, "rb") as f:
        return SvgDocument.from_bytes(f.read())


@pytest.fixture(scope="module")
def colon_doc() -> SvgDocument:
    with open(COLON_SVG, "rb") as f:
        return SvgDocument.from_bytes(f.read())


def _widget(seg_doc, colon_doc=None, **kw) -> SevenSegmentDisplay:
    w = SevenSegmentDisplay("t", **kw)
    w.set_document(seg_doc)
    if colon_doc is not None:
        w.set_colon_document(colon_doc)
    w.arranged_rect = Rect(0, 0, 400, 120)
    return w


# ---------------------------------------------------------------------------
# A recording canvas: captures each fill's color + the primitive drawn, so a
# test can prove WHICH segments were lit vs dim without a golden image.
# ---------------------------------------------------------------------------

class RecordingCanvas(Canvas):
    pass


def _make_recording() -> RecordingCanvas:
    fills = []          # (r,g,b,a) at each shape fill
    circles = []        # (cx, cy, r) at each fill_circle
    state = {"color": (0, 0, 0, 255)}

    def _mk(name):
        def f(self, *a, **k):
            if name == "set_fill_color":
                r, g, b = a[0], a[1], a[2]
                al = a[3] if len(a) > 3 else 255
                state["color"] = (r, g, b, al)
            elif name in ("fill_path", "fill_circle", "fill_rect",
                          "fill_round_rect"):
                fills.append(state["color"])
                if name == "fill_circle":
                    circles.append((a[0], a[1], a[2]))
            if name == "measure_text":
                return TextMetrics(0, 0, 0, 0)
            if name == "get_surface_size":
                return Size(400, 120)
            return None
        return f

    contract = {m for m in dir(Canvas) if not m.startswith("_")}
    for m in contract:
        setattr(RecordingCanvas, m, _mk(m))
    RecordingCanvas.__abstractmethods__ = frozenset()
    c = RecordingCanvas()
    c.create_surface(400, 120)
    c._fills = fills                                # type: ignore
    c._circles = circles                            # type: ignore
    return c


# ===========================================================================
# C1 — truth table
# ===========================================================================

_EXPECTED = {
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg",
    "a": "abcefg", "b": "cdefg", "c": "adef", "d": "bcdeg", "e": "adefg",
    "f": "aefg", "-": "g", " ": "",
}


def test_c1_truth_table():
    for char, segs in _EXPECTED.items():
        assert lit_segments(char) == frozenset(segs), char


def test_c1_hex_is_case_insensitive():
    assert lit_segments("A") == lit_segments("a")
    assert lit_segments("F") == lit_segments("f")


def test_c1_unknown_char_is_blank():
    assert lit_segments("Z") == frozenset()
    assert lit_segments("?") == frozenset()


def test_c1_each_lit_set_is_subset_of_af_g():
    seven = frozenset("abcdefg")
    for char in "0123456789abcdef -":
        assert lit_segments(char) <= seven


# ===========================================================================
# C2 — SVG source of truth
# ===========================================================================

def test_c2_all_seven_segments_found(seg_doc):
    segs = find_segments(seg_doc)
    assert set(segs.keys()) == set(SEGMENT_LABELS.keys())   # a..g all present


def test_c2_design_bbox_is_derived_not_hardcoded(seg_doc):
    segs = find_segments(seg_doc)
    box = segments_bbox(segs)
    assert box is not None
    x, y, w, h = box
    # a real, portrait-ish glyph box read from the art (not a placeholder)
    assert w > 0 and h > 0
    assert h > w                                    # digits are taller than wide


# ===========================================================================
# C3 — lit/dim rendering
# ===========================================================================

def test_c3_glyph_fills_every_segment_lit_or_dim(seg_doc):
    lit = (255, 154, 48, 255)
    dim = (40, 20, 5, 255)
    w = _widget(seg_doc, value="8", lit_color=list(lit), dim_color=list(dim))
    c = _make_recording()
    w.draw(c, Rect(0, 0, 100, 120))
    fills = c._fills                                # type: ignore
    # '8' lights all 7 segments -> all fills should be the lit color
    assert fills
    assert all(f == lit for f in fills)
    assert len(fills) == 7


def test_c3_off_segments_use_dim_color(seg_doc):
    lit = (255, 154, 48, 255)
    dim = (40, 20, 5, 255)
    w = _widget(seg_doc, value="1", lit_color=list(lit), dim_color=list(dim))
    c = _make_recording()
    w.draw(c, Rect(0, 0, 100, 120))
    fills = c._fills                                # type: ignore
    # '1' lights b,c (2 segments) and ghosts the other 5
    assert fills.count(lit) == 2
    assert fills.count(dim) == 5


# ===========================================================================
# C4 — color binding
# ===========================================================================

def test_c4_color_namespace_recolors_lit(seg_doc):
    w = _widget(seg_doc, value="8", lit_color=[255, 154, 48],
                color_namespace="seg/color")
    w.on_bus_state("seg/color", "#39d0e0")
    assert w.lit_color == (0x39, 0xd0, 0xe0, 255)


def test_c4_dim_is_derived_from_lit_when_not_explicit(seg_doc):
    w = _widget(seg_doc, value="8", lit_color=[200, 100, 50])
    d0 = w.dim_color
    w.lit_color = [0, 255, 0]
    # dim re-derives (a dark same-hue ghost) when it was never set explicitly
    assert w.dim_color != d0
    assert w.dim_color[1] > w.dim_color[0]          # tracks the green hue


def test_c4_explicit_dim_is_preserved_on_recolor(seg_doc):
    w = _widget(seg_doc, value="8", lit_color=[200, 100, 50],
                dim_color=[10, 10, 10])
    w.lit_color = [0, 255, 0]
    assert w.dim_color == (10, 10, 10, 255)         # explicit dim untouched


def test_c4_color_accepts_list_and_dict(seg_doc):
    w = _widget(seg_doc, lit_color=[1, 2, 3], color_namespace="c")
    w.on_bus_state("c", [10, 20, 30])
    assert w.lit_color == (10, 20, 30, 255)
    w.on_bus_state("c", {"r": 40, "g": 50, "b": 60, "a": 128})
    assert w.lit_color == (40, 50, 60, 128)


def test_c4_bad_color_payload_is_ignored(seg_doc):
    w = _widget(seg_doc, lit_color=[9, 9, 9], color_namespace="c")
    w.on_bus_state("c", "not-a-color")
    assert w.lit_color == (9, 9, 9, 255)


# ===========================================================================
# C5 — clock substrate
# ===========================================================================

def test_c5_clock_tick_fills_value_from_time_source(seg_doc):
    import time
    # a fixed instant -> a stable formatted string
    fixed = [time.mktime((2026, 8, 4, 13, 5, 9, 0, 0, -1))]
    w = _widget(seg_doc, clock=True, clock_format="%H:%M:%S",
                time_source=lambda: fixed[0])
    assert w.value == ""                            # blank until first tick
    changed = w.on_clock_tick(0.0)
    assert changed is True
    assert w.value == "13:05:09"


def test_c5_no_redraw_within_the_same_second(seg_doc):
    import time
    fixed = [time.mktime((2026, 8, 4, 13, 5, 9, 0, 0, -1))]
    w = _widget(seg_doc, clock=True, time_source=lambda: fixed[0])
    assert w.on_clock_tick(0.0) is True
    # same integer second -> no change, no redraw
    assert w.on_clock_tick(0.016) is False
    fixed[0] += 1                                   # cross the second boundary
    assert w.on_clock_tick(0.032) is True
    assert w.value.endswith(":10")


def test_c5_clock_mode_ignores_value_namespace(seg_doc):
    w = _widget(seg_doc, clock=True, value_namespace="v",
                time_source=lambda: 1000.0)
    w.on_clock_tick(0.0)
    v = w.value
    w.on_bus_state("v", "9999")                     # ignored in clock mode
    assert w.value == v


def test_c5_registers_with_clocked_substrate(seg_doc):
    class Ctx:
        def __init__(self):
            self.registered = []
        def register_clocked(self, w):
            self.registered.append(w)
        def request_redraw(self, rect=None):
            pass
    w = _widget(seg_doc, clock=True, time_source=lambda: 0.0)
    ctx = Ctx()
    w.on_mount(ctx)
    assert w in ctx.registered


# ===========================================================================
# C6 — colon art
# ===========================================================================

def test_c6_colon_shapes_found(colon_doc):
    shapes = find_colon_shapes(colon_doc)
    assert len(shapes) == 2                          # two dots
    assert colon_bbox(colon_doc, shapes) is not None


def test_c6_colon_renders_in_lit_color(seg_doc, colon_doc):
    lit = (255, 154, 48, 255)
    w = _widget(seg_doc, colon_doc, value=":", lit_color=list(lit))
    c = _make_recording()
    w.draw(c, Rect(0, 0, 60, 120))
    fills = c._fills                                # type: ignore
    assert fills                                    # the colon drew
    assert all(f == lit for f in fills)             # recolored to the lit color


def test_c6_colon_falls_back_to_dots_without_art(seg_doc):
    lit = (255, 154, 48, 255)
    w = _widget(seg_doc, value=":", lit_color=list(lit))  # no colon art
    c = _make_recording()
    w.draw(c, Rect(0, 0, 60, 120))
    fills = c._fills                                # type: ignore
    assert fills.count(lit) == 2                     # two programmatic dots


# --- colon alignment to the digit segment tops (David's glass note) --------

def test_c6_segment_top_y_reads_g_and_d(seg_doc):
    segs = find_segments(seg_doc)
    g = segment_top_y(segs, "g")
    d = segment_top_y(segs, "d")
    assert g is not None and d is not None
    assert d > g                                     # bottom segment sits below center


def test_c6_aligned_rect_lands_dot_bottoms_on_segment_tops(seg_doc, colon_doc):
    """The aligned colon rect maps the two dot BOTTOMS onto the center('g')-top
    and bottom('d')-top cell y-lines (the SVG-source-of-truth alignment)."""
    w = _widget(seg_doc, colon_doc)
    cell = Rect(0, 0, 60, 200)
    g_top = w._segment_top_cell_y(cell, "g")
    d_top = w._segment_top_cell_y(cell, "d")
    rect = w._aligned_colon_rect(cell)
    assert rect is not None
    cx0, cy0, cw, ch = w._colon_design
    scale = rect.h / ch                              # design-y -> cell-y
    boxes = sorted((_shape_design_bbox(colon_doc, s) for s in w._colon_shapes),
                   key=lambda b: b[1] + b[3] / 2.0)
    upper_bottom = boxes[0][1] + boxes[0][3]
    lower_bottom = boxes[-1][1] + boxes[-1][3]
    map_y = lambda Y: rect.y + (Y - cy0) * scale     # the contain map
    assert map_y(upper_bottom) == pytest.approx(g_top, abs=1e-6)
    assert map_y(lower_bottom) == pytest.approx(d_top, abs=1e-6)
    # round dots preserved: rect aspect == colon design aspect
    assert rect.w / rect.h == pytest.approx(cw / ch, abs=1e-9)


def test_c6_fallback_dots_bottoms_on_segment_tops(seg_doc):
    """The programmatic (no-art) dots also place their BOTTOM edges on the
    center/bottom segment-top y-lines."""
    w = _widget(seg_doc, value=":", lit_color=[255, 154, 48])  # no colon art
    cell = Rect(0, 0, 60, 200)
    g_top = w._segment_top_cell_y(cell, "g")
    d_top = w._segment_top_cell_y(cell, "d")
    c = _make_recording()
    # draw a single ':' cell directly so the recorded circles are just the dots
    w._draw_punct(c, ":", cell)
    circles = c._circles                            # type: ignore
    assert len(circles) == 2
    bottoms = sorted(cy + r for _cx, cy, r in circles)
    assert bottoms[0] == pytest.approx(g_top, abs=1e-6)
    assert bottoms[1] == pytest.approx(d_top, abs=1e-6)


# ===========================================================================
# C7 — multi-digit string layout
# ===========================================================================

def test_c7_digits_right_justify_with_blank_pad(seg_doc):
    w = _widget(seg_doc, value="42", digits=4)
    assert w._display_value() == "  42"             # two leading blanks


def test_c7_digits_truncate_to_rightmost(seg_doc):
    w = _widget(seg_doc, value="123456", digits=4)
    assert w._display_value() == "3456"


def test_c7_free_width_uses_full_value(seg_doc):
    w = _widget(seg_doc, value="12:34:56")
    assert w._display_value() == "12:34:56"


def test_c7_value_setter_redraws_only_on_change(seg_doc):
    calls = []
    w = _widget(seg_doc, value="1")
    w.request_redraw = lambda: calls.append(1)      # type: ignore
    w.value = "1"                                   # unchanged
    assert not calls
    w.value = "2"
    assert calls


def test_c7_layout_places_one_cell_per_char(seg_doc):
    w = _widget(seg_doc, value="12:34")
    aspect = w._design[2] / w._design[3]
    cells = w._layout_cells(Rect(0, 0, 400, 120), aspect)
    assert [c for c, _ in cells] == list("12:34")
    # cells are left-to-right, non-overlapping
    xs = [r.x for _, r in cells]
    assert xs == sorted(xs)


# ===========================================================================
# C8 — parser integration
# ===========================================================================

SCENE = """
design:
  width: 900
  height: 300
widgets:
  - type: seven_segment
    id: clk
    src: file://assets/seven_segment.svg
    colon_src: file://assets/seven_segment_colon.svg
    lit_color: "#ff9a30"
    clock: true
    color_namespace: clock/color
  - type: seven_segment
    id: ro
    src: file://assets/seven_segment.svg
    value: "42"
    digits: 4
    value_namespace: panel/readout
"""


def test_c8_scene_builds_real_widget_not_placeholder():
    import yaml
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree(yaml.safe_load(SCENE))
    ids = {}

    def walk(w):
        ids[getattr(w, "widget_id", "")] = w
        for c in getattr(w, "children", []) or []:
            walk(c)

    walk(comp.root)
    assert isinstance(ids["clk"], SevenSegmentDisplay)
    assert isinstance(ids["ro"], SevenSegmentDisplay)
    assert ids["clk"].clock is True
    assert ids["ro"].digits == 4


def test_c8_scene_wires_namespace_subscriptions():
    import yaml
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree(yaml.safe_load(SCENE))
    subs = {(ns, w.widget_id) for ns, w in comp.subscriptions}
    assert ("clock/color", "clk") in subs
    assert ("panel/readout", "ro") in subs


def test_c8_scene_registers_svg_assets():
    import yaml
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree(yaml.safe_load(SCENE))
    assert "file://assets/seven_segment.svg" in comp.asset_uris
    assert "file://assets/seven_segment_colon.svg" in comp.asset_uris


# ===========================================================================
# Render smoke through the real backend (no crash; both platforms)
# ===========================================================================

def test_render_full_string_through_skia(seg_doc, colon_doc):
    w = _widget(seg_doc, colon_doc, value="12:34:56", lit_color=[255, 154, 48])
    c = SkiaCanvas()
    c.create_surface(600, 160)
    w.draw(c, Rect(10, 10, 580, 140))               # must not raise
    c.present()


def test_measure_returns_positive_size(seg_doc):
    w = _widget(seg_doc, value="8", height=120)
    size = w.measure(Constraints(1000, 1000))
    assert size.width > 0 and size.height > 0


# ===========================================================================
# Scene-relative asset resolution ([CXC-R-ENGINE-ASSETS]) — the demo's bundled
# art loads from ANY launch cwd, not just examples/.
# ===========================================================================

DEMO_SCENE = os.path.join(
    PY_ROOT, "composeio", "render", "examples", "colonial_clock.compose.yaml")


def test_parse_sets_scene_dir():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(DEMO_SCENE)
    assert comp.scene_dir == os.path.dirname(os.path.abspath(DEMO_SCENE))


def test_asset_manager_resolves_scene_relative_from_any_cwd(tmp_path, monkeypatch):
    from composeio.render.engine.assets import AssetManager
    base = os.path.join(PY_ROOT, "composeio", "render", "examples")
    am = AssetManager(base_dir=base)
    # a foreign cwd where 'assets/...' does NOT exist relative to it
    monkeypatch.chdir(tmp_path)
    data = am.get_bytes("file://assets/seven_segment.svg")
    assert data is not None and data.lstrip().startswith(b"<?xml")


def test_asset_manager_without_base_dir_is_cwd_relative(tmp_path, monkeypatch):
    from composeio.render.engine.assets import AssetManager
    am = AssetManager()                              # no base -> legacy behavior
    monkeypatch.chdir(tmp_path)
    assert am.get_bytes("file://assets/seven_segment.svg") is None


def test_demo_scene_renders_widgets_from_repo_root(tmp_path, monkeypatch):
    """Launched from a foreign cwd, the demo still builds real widgets (the
    art resolved scene-relative) — not the fallback placeholders."""
    from composeio.render.engine.runtime import RenderApp
    monkeypatch.chdir(tmp_path)
    app = RenderApp.from_file(DEMO_SCENE, windowed=False)
    app.start()
    try:
        assert app.assets.base_dir.endswith(os.path.join("examples"))
        segs = [w for w in _iter_tree(app.comp.root)
                if isinstance(w, SevenSegmentDisplay)]
        assert len(segs) == 1
        # the segment SVG resolves through the app's AssetManager
        for w in segs:
            assert w._ensure_loaded() is not None
    finally:
        app.stop()


def _iter_tree(w):
    yield w
    for c in getattr(w, "children", []) or []:
        yield from _iter_tree(c)
