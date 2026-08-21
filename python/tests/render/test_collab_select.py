"""
Tests for Surface 4 — the Collaboration widget-selection editor
([SPEC:CXC-CONSOLE-PROD] [CXC-ICP-S4-WIDGETSEL]).

Acceptance: the list is DRIVEN BY Foundation B (list_widgets("collaboration"),
never a hardcoded list); a show/hide toggle + a reorder publish Foundation A's
console/tabs with the ordered visible set + hidden set; a persisted selection
referencing an unregistered type is SKIPPED, never a crash.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import console_theme as ct
from composeio.render.engine.widget_registry import list_widgets
from composeio.render.widgets.widget_select import (
    WidgetSelectWidget, DEFAULT_TABS_NS, collaboration_selection)

from .conftest import surface_to_array

RECT = Rect(0, 0, 974, 1200)


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


def _mounted():
    w = WidgetSelectWidget("ws")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    return w, ctx


def _drawn(w):
    c = SkiaCanvas(); c.create_surface(974, 1200)
    w.draw(c, RECT)
    return c


def _zone(w, kind, idx):
    for (x0, y0, x1, y1, k, i) in w._zones:
        if k == kind and i == idx:
            return (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return None


# --- registry-driven (Foundation B) ------------------------------------------

def test_rows_are_the_collaboration_registry_not_a_literal():
    w, _ = _mounted()
    reg = [d.type for d in list_widgets(category="collaboration")]
    assert [r["type"] for r in w._rows] == reg
    assert w._rows                       # non-empty
    # [DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2 fit-aware default: every
    # RAIL-FITTING row is shown; a rail-misfit row is present but NOT visible
    # (disabled + redirected, not silently dropped).
    assert all(r["visible"] for r in w._rows if r["fits"])
    assert any(not r["fits"] for r in w._rows)          # the catalog has misfits
    assert all(not r["visible"] for r in w._rows if not r["fits"])


# --- toggle / reorder publish console/tabs -----------------------------------

def test_toggle_hides_and_publishes():
    w, ctx = _mounted()
    _drawn(w)
    first = w._rows[0]["type"]
    w.on_tap(*_zone(w, "toggle", 0))
    assert w._rows[0]["visible"] is False
    ns, payload = ctx.published[-1]
    assert ns == DEFAULT_TABS_NS
    assert first in payload["collaboration"]["hidden"]
    assert first not in payload["collaboration"]["widgets"]


def test_reorder_down_moves_and_publishes_order():
    w, ctx = _mounted()
    _drawn(w)
    before = [r["type"] for r in w._rows]
    w.on_tap(*_zone(w, "down", 0))
    after = [r["type"] for r in w._rows]
    assert after[0] == before[1] and after[1] == before[0]
    _ns, payload = ctx.published[-1]
    assert payload["collaboration"]["widgets"][:2] == after[:2]


def test_up_at_top_is_a_noop():
    w, ctx = _mounted()
    _drawn(w)
    z = _zone(w, "up", 0)
    w.on_tap(*z)
    assert ctx.published == []            # disabled — nothing published


# --- persisted selection reconciliation (skip unregistered) ------------------

def test_apply_selection_skips_unregistered_type():
    w, _ = _mounted()
    reg = [d.type for d in list_widgets(category="collaboration")]
    # a persisted order that hides one real type + references a ghost
    w._apply_selection(visible=[reg[1], reg[0], "ghost_widget_gone"],
                       hidden=[reg[2]])
    types = [r["type"] for r in w._rows]
    assert "ghost_widget_gone" not in types      # skipped, no crash
    assert types[0] == reg[1] and types[1] == reg[0]   # persisted order honored
    hidden = [r["type"] for r in w._rows if not r["visible"]]
    assert reg[2] in hidden


def test_bus_state_applies_selection():
    w, _ = _mounted()
    reg = [d.type for d in list_widgets(category="collaboration")]
    w.on_bus_state(DEFAULT_TABS_NS,
                   {"collaboration": {"widgets": [reg[0]],
                                      "hidden": [reg[1]]}})
    by_type = {r["type"]: r["visible"] for r in w._rows}
    assert by_type[reg[0]] is True and by_type[reg[1]] is False


def test_collaboration_selection_pure_reader():
    vis, hid = collaboration_selection(
        {"collaboration": {"widgets": ["a", "b"], "hidden": ["c"]}})
    assert vis == ["a", "b"] and hid == ["c"]
    assert collaboration_selection("garbage") == ([], [])
    assert collaboration_selection({"other": {}}) == ([], [])


# --- parser wiring + render --------------------------------------------------

def test_parser_wires_tabs_subscription():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "collab_select", "id": "c"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert DEFAULT_TABS_NS in subbed
    assert isinstance(comp.subscriptions[0][1], WidgetSelectWidget)


# --- D2 fit-aware: disable the rail-misfits + redirect ----------------------
# ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2)

def _row_index(w, wtype):
    return next(i for i, r in enumerate(w._rows) if r["type"] == wtype)


def test_rail_misfit_is_disabled_with_redirect_and_no_toggle():
    """A rail-misfit catalog widget (the Decisions Graph DAG) is present but
    DISABLED — not visible, no toggle/reorder zone — and carries a redirect to
    the Dashboard, killing the silent no-op."""
    w, _ = _mounted()
    _drawn(w)
    i = _row_index(w, "decisionsgraph")
    r = w._rows[i]
    assert r["fits"] is False and r["visible"] is False
    assert r["redirect"] == "dashboard"
    # no toggle/up/down zone for a disabled row; a redirect zone instead
    assert _zone(w, "toggle", i) is None
    assert _zone(w, "redirect", i) is not None
    # it never enters the rail's selection payload
    assert "decisionsgraph" not in w.selection_payload()["collaboration"]["widgets"]
    assert "decisionsgraph" not in w.selection_payload()["collaboration"]["hidden"]


def test_redirect_tap_publishes_the_home_region_switch():
    """Tapping a misfit's '→ add on Dashboard' chip publishes the console
    tab-switch seam to the redirect home (actionable, not a dead label)."""
    w, ctx = _mounted()
    _drawn(w)
    i = _row_index(w, "mermaid")
    w.on_tap(*_zone(w, "redirect", i))
    ns, val = ctx.published[-1]
    assert ns == "_app/console/tab" and val == "dashboard"


def test_rail_fitting_row_still_toggles():
    """A rail-FITTING collaboration widget keeps its full show/hide control
    (the fit gate only disables misfits)."""
    w, ctx = _mounted()
    _drawn(w)
    i = _row_index(w, "decisionssummary")
    assert w._rows[i]["fits"] is True
    z = _zone(w, "toggle", i)
    assert z is not None
    w.on_tap(*z)
    assert w._rows[i]["visible"] is False           # toggled off
    assert ctx.published                            # published the selection


def test_render_paints_rows():
    w, _ = _mounted()
    c = _drawn(w)
    assert surface_to_array(c)[..., :3].std() > 3.0
    # a FITTING row records up/down/toggle zones; a MISFIT row records a
    # redirect zone instead ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2).
    kinds = {k for (_a, _b, _c, _d, k, _i) in w._zones}
    assert {"up", "down", "toggle"} <= kinds
    assert "redirect" in kinds
