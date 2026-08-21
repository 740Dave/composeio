"""
RadioButtonGroupWidget compliance (the generic radio group promoted to engine
core; formerly the BSG ``colonial_radio_buttons`` widget).

The radio group renders a CONFIGURABLE-N set of stadium button-shapes with
pixel-font captions, exactly ONE selected in the HIGHLIGHT color; the rest use
the default (black) shape fill.  Orientation (h/v), padding, fixed 106x30 button
geometry (with per-button overrides), the subscribe-side selection derive, AND the
tap->publish seam (control-is-a-peer: a tap publishes the index,
touch/rotary/Intelligence identical) are all exercised here.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
# [CXC-PROJECT-WIDGETS] the colonial widgets are now project-local to BSG; the
# repo root must be importable to reach compose_projects.bsg.widgets.
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets.radio_button_group import (
    RadioButtonGroupWidget, HORIZONTAL, VERTICAL)

_HI = (0xCD, 0x67, 0x1D, 0xFF)          # cic theme highlight
_BLACK = (0x00, 0x00, 0x00, 0xFF)       # default button-shape fill


class _FillSpy:
    """Wrap a real canvas, recording the fill color active at each
    ``fill_round_rect`` so a test can read which shape got the highlight."""

    def __init__(self, cv):
        self._cv = cv
        self._cur = None
        self.round_rects = []            # (color, (x, y, w, h, r))

    def set_fill_color(self, r, g, b, a=255):
        self._cur = (r, g, b, a)
        self._cv.set_fill_color(r, g, b, a)

    def fill_round_rect(self, x, y, w, h, radius):
        self.round_rects.append((self._cur, (x, y, w, h, radius)))
        self._cv.fill_round_rect(x, y, w, h, radius)

    def __getattr__(self, name):
        return getattr(self._cv, name)


def _canvas(w=560, h=200):
    cv = SkiaCanvas()
    cv.create_surface(w, h)
    return cv


# ---------------------------------------------------------------------------
# geometry: fixed 106x30 stadiums, 10px padding, natural size = N-driven
# ---------------------------------------------------------------------------

def test_natural_size_and_rects_for_n_buttons():
    r = RadioButtonGroupWidget("r", labels=["DRADIS", "STD", "EXT", "CHRONO"])
    assert r.natural_size() == (4 * 106.0 + 3 * 10.0, 30.0)
    cv = _canvas()
    r.draw(cv, Rect(0, 0, *r.natural_size()))
    rects = r._button_rects
    assert len(rects) == 4
    for i, br in enumerate(rects):
        assert (br.w, br.h) == (106.0, 30.0)
        assert br.x == i * (106.0 + 10.0)          # fixed size + 10px padding


def test_count_is_configurable_from_a_bound_list():
    r = RadioButtonGroupWidget("r", labels=["A", "B", "C"])
    assert r.natural_size() == (3 * 106.0 + 2 * 10.0, 30.0)
    r.set_labels(["A", "B", "C", "D", "E"])          # the bound list drives N
    assert r.natural_size() == (5 * 106.0 + 4 * 10.0, 30.0)
    cv = _canvas(900, 80)
    r.draw(cv, Rect(0, 0, *r.natural_size()))
    assert len(r._button_rects) == 5


def test_per_button_width_override():
    r = RadioButtonGroupWidget("r", labels=["A", "B"],
                                   button_overrides={1: {"width": 200.0}})
    assert r.natural_size() == (106.0 + 200.0 + 10.0, 30.0)


# ---------------------------------------------------------------------------
# the SELECTED shape uses the theme HIGHLIGHT; the rest use the default fill
# ---------------------------------------------------------------------------

def test_selected_button_highlighted_rest_default():
    r = RadioButtonGroupWidget("r", labels=["DRADIS", "STD", "EXT", "CHRONO"],
                                   selected_index=0, highlight_color=_HI)
    cv = _canvas()
    spy = _FillSpy(cv)
    r.draw(spy, Rect(0, 0, *r.natural_size()))
    # exactly one round-rect painted in the highlight color (the selected one)
    hi_fills = [rr for (col, rr) in spy.round_rects if col == _HI]
    black_fills = [rr for (col, rr) in spy.round_rects if col == _BLACK]
    assert len(hi_fills) == 1
    assert len(black_fills) == 3
    # the highlighted shape is the FIRST button (DRADIS, selected_index 0)
    assert hi_fills[0][0] == 0.0                      # x of button 0
    # stadium: radius == height / 2 (David's ry)
    assert hi_fills[0][4] == 30.0 / 2.0


def test_selection_moves_the_highlight():
    r = RadioButtonGroupWidget("r", labels=["DRADIS", "STD", "EXT", "CHRONO"],
                                   selected_index=0, highlight_color=_HI)
    r.set_selected(2)                                 # EXT
    assert r.selected_index == 2
    cv = _canvas()
    spy = _FillSpy(cv)
    r.draw(spy, Rect(0, 0, *r.natural_size()))
    hi_fills = [rr for (col, rr) in spy.round_rects if col == _HI]
    assert len(hi_fills) == 1
    assert hi_fills[0][0] == 2 * (106.0 + 10.0)       # x of button 2 (EXT)


def test_out_of_range_selection_clamps():
    r = RadioButtonGroupWidget("r", labels=["A", "B", "C"], selected_index=9)
    assert r.selected_index == 2
    r.set_selected(-4)
    assert r.selected_index == 0


# ---------------------------------------------------------------------------
# orientation binding (h/v)
# ---------------------------------------------------------------------------

def test_vertical_orientation_stacks():
    r = RadioButtonGroupWidget("r", labels=["ONE", "TWO", "THREE"],
                                   orientation="vertical")
    assert r.orientation == VERTICAL
    assert r.natural_size() == (106.0, 3 * 30.0 + 2 * 10.0)
    cv = _canvas(160, 140)
    r.draw(cv, Rect(0, 0, *r.natural_size()))
    ys = [br.y for br in r._button_rects]
    assert ys == [0.0, 40.0, 80.0]                    # stacked, 10px padding


def test_orientation_defaults_horizontal():
    r = RadioButtonGroupWidget("r", labels=["A", "B"])
    assert r.orientation == HORIZONTAL


# ---------------------------------------------------------------------------
# control-is-a-peer: a tap publishes the station-scoped index, and the selection
# derives from the published index (touch / rotary / Intelligence identical)
# ---------------------------------------------------------------------------

def test_on_bus_state_derives_selection_from_published_index():
    r = RadioButtonGroupWidget("r", labels=["DRADIS", "STD", "EXT", "CHRONO"],
                                   index_namespace="cic/1/radio")
    r.on_bus_state("cic/1/radio", {"index": 3})
    assert r.selected_index == 3
    r.on_bus_state("cic/1/radio", "std")              # bare key resolves too
    assert r.selected_index == 1
    r.on_bus_state("other/ns", {"index": 0})          # ignores foreign ns
    assert r.selected_index == 1


def test_tap_is_wired_and_interactive():
    r = RadioButtonGroupWidget("r", labels=["A", "B"])
    assert r.interactive is True


def test_tap_publishes_station_scoped_index_and_moves_selection():
    """A tap on a radio cell publishes the station-scoped {index, key} and moves
    the selection — the SAME index a rotary / Intelligence publishes
    ([CXC-CIC-CREDO])."""
    r = RadioButtonGroupWidget(
        "r", labels=["DRADIS", "STD", "EXT", "CHRONO"], selected_index=0,
        index_namespace="cic/1/radio")
    got = []
    r.publish = lambda ns, v: got.append((ns, v))     # capture publishes
    # lay the group out (unscaled) so the cell rects exist
    w, h = r.natural_size()
    r.arranged_rect = Rect(0, 0, w, h)
    r.draw(_canvas(int(w) + 4, int(h) + 4), r.arranged_rect)
    # tap the CENTER of cell 2 (EXT)
    ext = r._button_rects[2]
    r.on_tap(ext.x + ext.w / 2.0, ext.y + ext.h / 2.0)
    assert r.selected_index == 2
    assert got == [("cic/1/radio", {"index": 2, "key": "EXT"})]
    # a direct publish (rotary / Intelligence) moves it IDENTICALLY
    r.on_bus_state("cic/1/radio", {"index": 0, "key": "DRADIS"})
    assert r.selected_index == 0


def test_tap_maps_through_uniform_scale():
    """When the group is uniform-scaled about its rect center by the container
    draw pass, the tap inverse-maps into the widget's own space so cell routing
    stays exact (the DRADIS drop-in scale idiom)."""
    r = RadioButtonGroupWidget(
        "r", labels=["DRADIS", "STD", "EXT", "CHRONO"],
        index_namespace="cic/1/radio")
    got = []
    r.publish = lambda ns, v: got.append((ns, v))
    w, h = r.natural_size()
    r.arranged_rect = Rect(0, 0, w, h)
    r.scale = 0.5                                     # shrunk about the center
    r.draw(_canvas(int(w) + 4, int(h) + 4), r.arranged_rect)
    cell = r._button_rects[3]                          # CHRONO, unscaled cell
    cx = r.arranged_rect.x + r.arranged_rect.w / 2.0
    cy = r.arranged_rect.y + r.arranged_rect.h / 2.0
    # device point = the scaled position of the cell center
    lx = cell.x + cell.w / 2.0
    ly = cell.y + cell.h / 2.0
    dx = cx + (lx - cx) * r.scale
    dy = cy + (ly - cy) * r.scale
    r.on_tap(dx, dy)
    assert r.selected_index == 3
    assert got == [("cic/1/radio", {"index": 3, "key": "CHRONO"})]


def test_tap_without_namespace_moves_selection_but_does_not_publish():
    r = RadioButtonGroupWidget("r", labels=["A", "B", "C"])
    got = []
    r.publish = lambda ns, v: got.append((ns, v))
    w, h = r.natural_size()
    r.arranged_rect = Rect(0, 0, w, h)
    r.draw(_canvas(int(w) + 4, int(h) + 4), r.arranged_rect)
    b = r._button_rects[1]
    r.on_tap(b.x + b.w / 2.0, b.y + b.h / 2.0)
    assert r.selected_index == 1
    assert got == []                                  # no ns -> selection only


# ---------------------------------------------------------------------------
# engine-core registration + backward-compat aliases (promoted out of BSG):
# canonical + legacy tokens both resolve to the promoted classes, with NO
# compose_projects import — so existing scenes render unchanged.
# ---------------------------------------------------------------------------

def _build_leaf(spec):
    """Build a single widget through the REAL engine parser (aliases + build
    hooks + subscription wiring), returning the constructed widget."""
    from composeio.render.engine.parser import parse_widget_tree
    scene = {"app": {"id": "t"},
             "design": {"title": "T", "width": 600, "height": 100,
                        "padding": 0, "gap": 0},
             "widgets": [dict(spec)]}
    comp = parse_widget_tree(scene)
    return comp.root.children[0]


def test_radio_group_engine_core_canonical_and_alias_tokens_render():
    from composeio.render.widgets.segmented_tab_row import SegmentedTabRowWidget
    cv = _canvas()
    for token in ("radio_button_group", "colonial_radio_buttons"):
        w = _build_leaf({"type": token, "id": "r",
                         "labels": ["DRADIS", "STD", "EXT"],
                         "index_namespace": "cic/1/radio"})
        assert type(w) is RadioButtonGroupWidget, token
        assert w.widget_type == "radio_button_group"   # canonical, not the alias
        w.draw(cv, Rect(0, 0, *w.natural_size()))       # the alias RENDERS
        assert len(w._button_rects) == 3
    for token in ("segmented_tab_row", "cic_tab_row", "cictabrow"):
        w = _build_leaf({"type": token, "id": "t", "labels": ["A", "B", "C"],
                         "selected_indicator": "pill-overlay",
                         "index_namespace": "cic/1/console"})
        assert type(w) is SegmentedTabRowWidget, token
        assert w.widget_type == "segmented_tab_row"
        w.draw(_canvas(600, 60), Rect(0, 0, 600, 48))   # the alias RENDERS


def test_promoted_widgets_have_no_compose_projects_dependency():
    # importing the two engine-core widget modules must not drag in BSG.
    import importlib
    import sys
    for m in list(sys.modules):
        if m.startswith("compose_projects"):
            del sys.modules[m]
    importlib.import_module("composeio.render.widgets.radio_button_group")
    importlib.import_module("composeio.render.widgets.segmented_tab_row")
    assert not any(m.startswith("compose_projects") for m in sys.modules)
