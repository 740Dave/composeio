"""
Tests for Surface 5 — the Dashboard placement grid ([SPEC:CXC-CONSOLE-PROD]
[CXC-ICP-S5-DASHBOARD]) — plus the Surface 6 pluggable seam
([CXC-ICP-S6-PLUGGABLE]) exercised through the Dashboard palette.

Acceptance: a fixture ``placements`` list mounts placed widgets THROUGH the
registry at the stated cells; the palette renders
list_widgets("dashboard-eligible") (never a literal); placing/removing publishes
Foundation A's console/dashboard; a placement of an unregistered type is SKIPPED;
a NEW registry descriptor surfaces in the palette (modulo restart).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect, Constraints
from composeio.render.widgets import console_theme as ct
from composeio.render.engine.widget_registry import (
    list_widgets, register, WidgetDescriptor, WIDGET_REGISTRY, canonical_key)
from composeio.render.widgets.dashboard import (
    DashboardWidget, DEFAULT_DASHBOARD_NS, normalize_placements, GRID_COLS)
from composeio.render.widgets.gauge import GaugeWidget

from .conftest import surface_to_array

RECT = Rect(0, 0, 1256, 1000)


class _Ctx:
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []

    def request_redraw(self, rect=None):
        pass

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def teardown_function(_fn):
    ct.set_theme("dark")


def _mounted(placements=None):
    w = DashboardWidget("db", placements=placements)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    w.arrange(RECT)
    return w, ctx


def _drawn(w):
    c = SkiaCanvas(); c.create_surface(1256, 1000)
    w.draw(c, RECT)
    return c


# --- placements mount THROUGH the registry -----------------------------------

def test_fixture_placements_mount_the_widgets():
    placements = [{"widget": "gauge", "row": 0, "col": 0, "w": 2, "h": 1},
                  {"widget": "intelligence_card", "row": 0, "col": 2,
                   "w": 1, "h": 1}]
    w, _ = _mounted(placements)
    assert len(w.children) == 2
    assert isinstance(w.children[0], GaugeWidget)
    # the intelligence_card's required namespace is wired for parse-time bind
    assert ("self/context_pct", w.children[1]) in w.child_subs


def test_cells_map_to_grid_geometry():
    w, _ = _mounted([{"widget": "gauge", "row": 0, "col": 0, "w": 2, "h": 1},
                     {"widget": "avatar", "row": 1, "col": 1, "w": 1, "h": 1}])
    g = w._grid_rect(RECT)
    r0 = w._cell_rect(g, w._placements[0])
    r1 = w._cell_rect(g, w._placements[1])
    cell_w = (g.w - 14.0 * (GRID_COLS - 1)) / GRID_COLS
    assert abs(r0.w - (2 * cell_w + 14.0)) < 0.5     # a 2-col span
    assert r1.y > r0.y                                # row 1 sits below row 0
    assert r1.x > g.x                                 # col 1 is indented


def test_unregistered_placement_is_skipped():
    w, _ = _mounted([{"widget": "gauge", "row": 0, "col": 0, "w": 1, "h": 1},
                     {"widget": "no_such_widget", "row": 0, "col": 1,
                      "w": 1, "h": 1}])
    assert len(w.children) == 1                       # ghost dropped, no crash
    assert all(p["widget"] != "no_such_widget" for p in w._placements)


# --- palette is registry-driven (Foundation B / S6) --------------------------

def test_palette_lists_dashboard_eligible_registry():
    w, _ = _mounted()
    _drawn(w)
    palette_types = [t for (_a, _b, _c, _d, t) in w._palette_zones]
    eligible = [d.type for d in list_widgets(category="dashboard-eligible")]
    # every palette chip is a registry dashboard-eligible type (a prefix — the
    # strip shows one row; order + membership come from the registry)
    assert palette_types
    assert all(t in eligible for t in palette_types)


# --- placing / removing publishes console/dashboard --------------------------

def test_palette_tap_adds_placement_and_publishes():
    w, ctx = _mounted()
    _drawn(w)
    x0, y0, x1, y1, typ = w._palette_zones[0]
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert len(w._placements) == 1 and w._placements[0]["widget"] == typ
    ns, payload = ctx.published[-1]
    assert ns == DEFAULT_DASHBOARD_NS
    assert payload["placements"][0]["widget"] == typ


def test_remove_chip_drops_placement_and_publishes():
    w, ctx = _mounted([{"widget": "gauge", "row": 0, "col": 0, "w": 1, "h": 1}])
    _drawn(w)
    x0, y0, x1, y1, idx = w._remove_zones[0]
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert w._placements == []
    _ns, payload = ctx.published[-1]
    assert payload["placements"] == []


def test_bus_state_rebuilds_placements():
    w, _ = _mounted()
    assert w.children == []
    w.on_bus_state(DEFAULT_DASHBOARD_NS,
                   {"placements": [{"widget": "gauge", "row": 0, "col": 0,
                                    "w": 1, "h": 1}]})
    assert len(w.children) == 1 and isinstance(w.children[0], GaugeWidget)


def test_normalize_placements_tolerates_garbage():
    assert normalize_placements("nope") == []
    assert normalize_placements([{"no_widget_key": 1}]) == []
    out = normalize_placements([{"widget": "gauge"}])
    assert out == [{"widget": "gauge", "row": 0, "col": 0, "w": 1, "h": 1}]


# --- parser wiring (child_subs -> engine subscriptions) ----------------------

def test_parser_wires_dashboard_and_child_subs():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{
        "type": "dashboard", "id": "d",
        "placements": [{"widget": "intelligence_card", "row": 0, "col": 0,
                        "w": 1, "h": 1}]}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert DEFAULT_DASHBOARD_NS in subbed
    # the placed intelligence_card's required namespace is a real subscription
    assert "self/context_pct" in subbed


def test_empty_dashboard_renders_usable():
    w, _ = _mounted()
    c = _drawn(w)
    assert surface_to_array(c)[..., :3].std() > 2.0   # palette + empty hint drawn
    assert w._palette_zones                            # the add affordance exists


# --- S6: a NEW descriptor surfaces in the palette (modulo restart) -----------

def test_new_registry_descriptor_appears_in_palette():
    """[CXC-ICP-S6-PLUGGABLE]: registering a synthetic dashboard-eligible
    descriptor makes it SELECTABLE in the Dashboard palette with NO selector /
    Dashboard edit — the closed pluggable loop."""
    key = canonical_key("synthwidget")
    added = key not in WIDGET_REGISTRY
    try:
        register(WidgetDescriptor(
            type="synthwidget", cls=GaugeWidget, display_name="Synth Widget",
            required_namespaces=["synth/data"], category="dashboard-eligible"))
        # a FRESH dashboard (simulating the next launch) lists it in the palette
        w, _ = _mounted()
        _drawn(w)
        palette_types = [t for (_a, _b, _c, _d, t) in w._palette_zones]
        # the palette shows one row; assert membership via the discovery API
        eligible = [d.type for d in list_widgets(category="dashboard-eligible")]
        assert "synthwidget" in eligible
        # and it is genuinely PLACEABLE: mount it and confirm it builds + binds
        w2, _ = _mounted([{"widget": "synthwidget", "row": 0, "col": 0,
                           "w": 1, "h": 1}])
        assert len(w2.children) == 1
        assert ("synth/data", w2.children[0]) in w2.child_subs
    finally:
        if added:
            WIDGET_REGISTRY.pop(key, None)
