"""
Tests for the app-level console surfaces END TO END ([SPEC:CXC-CONSOLE-PROD]
S4/S5/S6) — the LAUNCH-READ appliers in console/personalization.py that consume
Foundation A's persisted selection, and the S6 closed-loop proof that a single
new registry descriptor surfaces in BOTH selectors (Collaboration list +
Dashboard palette) with no selector edit.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
CONSOLE_DIR = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console")
for p in (PY_ROOT, CONSOLE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import personalization as P                                    # noqa: E402
from composeio.render.engine.layout import FlexContainer      # noqa: E402
from composeio.render.engine.parser import parse_widget_tree  # noqa: E402
from composeio.render.widgets import (ChatThreadWidget,       # noqa: E402
                                      ActivityWidget, SelfLedgerWidget)
from composeio.render.widgets.dashboard import DashboardWidget  # noqa: E402
from composeio.render.widgets.widget_select import (          # noqa: E402
    WidgetSelectWidget)
from composeio.render.engine.widget_registry import (         # noqa: E402
    list_widgets, register, WidgetDescriptor, WIDGET_REGISTRY, canonical_key)
from composeio.render.widgets.gauge import GaugeWidget        # noqa: E402


# --- S4 launch-read applier --------------------------------------------------

def _collab_container():
    cont = FlexContainer("collab", direction="column")
    for W in (ChatThreadWidget, ActivityWidget, SelfLedgerWidget):
        cont.add(W("x_" + W.__name__))
    return cont


class _Comp:
    def __init__(self, root):
        self.root = root
        self.subscriptions = []


def test_s4_apply_hides_and_reorders_collaboration():
    cont = _collab_container()
    comp = _Comp(cont)
    changed = P.apply_collaboration_selection(comp, {"tabs": {"collaboration": {
        "widgets": ["selfledger", "chatthread"], "hidden": ["activity"]}}})
    assert changed >= 1
    order = [c.widget_type for c in cont.children]
    assert order == ["self_ledger", "chat_thread", "activity"]   # persisted order
    vis = {c.widget_type: getattr(c, "visible", True) for c in cont.children}
    assert vis["activity"] is False and vis["self_ledger"] is True


def test_s4_mounts_explicit_added_rail_fitting_type():
    """[CXC-ICP-S4-DEFAULT-RAIL] launch half: an EXPLICIT ``added`` type not yet
    placed AND rail-fitting is INSTANTIATED into the rail container + its
    subscriptions registered — the config genuinely ADDs.  The show/hide roster
    (``widgets``) is CURATE-ONLY and does NOT mount."""
    from composeio.render.engine.layout import OrderedFlowContainer
    rail = OrderedFlowContainer("rail_col", direction="column",
                                state_namespace="_app/console/collab_order")
    rail.add(ActivityWidget("x_act"))
    comp = _Comp(rail)
    n0 = len(comp.subscriptions)
    changed = P.apply_collaboration_selection(comp, {"tabs": {"collaboration": {
        "added": ["decisionssummary"]}}})
    assert changed >= 1
    types = [c.widget_type for c in rail.children]
    assert "decisions_summary" in types              # mounted from the catalog
    # its required namespace is wired for the launch bind
    new_subs = [ns for ns, _ in comp.subscriptions[n0:]]
    assert "self/decisions" in new_subs


def test_s4_roster_visible_does_not_mount():
    """[CXC-ICP-S4-DEFAULT-RAIL] the regression guard the build lacked: a type
    merely SHOWN in the category roster (``widgets``) is NOT mounted into the
    rail — "shown in the catalog" is not "add to the rail".  Only ``added``
    mounts."""
    from composeio.render.engine.layout import OrderedFlowContainer
    rail = OrderedFlowContainer("rail_col", direction="column",
                                state_namespace="_app/console/collab_order")
    rail.add(ActivityWidget("x_act"))
    comp = _Comp(rail)
    P.apply_collaboration_selection(comp, {"tabs": {"collaboration": {
        "widgets": ["activity", "decisionssummary", "selfledger"],
        "hidden": []}}})
    types = [c.widget_type for c in rail.children]
    assert types == ["activity"]                     # nothing mounted from roster


def test_s4_does_not_mount_a_rail_misfit():
    """A explicitly-added type that does NOT fit the rail is NOT mounted (the
    backstop behind the widget_select fit filter)."""
    from composeio.render.engine.layout import OrderedFlowContainer
    rail = OrderedFlowContainer("rail_col", direction="column")
    rail.add(ActivityWidget("x_act"))
    comp = _Comp(rail)
    P.apply_collaboration_selection(comp, {"tabs": {"collaboration": {
        "added": ["decisionsgraph"]}}})
    assert "decisions_graph" not in [c.widget_type for c in rail.children]


def test_s4_hot_add_mounts_into_running_rail_and_binds():
    """[DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D3 (live half): hot_add_collaboration_
    widget mounts a rail-fitting catalog widget into the RUNNING rail via
    add_widget_live, and it lights up with the RETAINED bus value immediately
    (binds + renders, not blank)."""
    from composeio.render.engine.runtime import RenderApp
    scene = {"design": {"width": 420, "height": 700, "padding": 0, "gap": 0},
             "widgets": [{"type": "ordered_stack", "id": "rail_col",
                          "direction": "column", "align": "stretch", "gap": 8,
                          "state_namespace": "_app/console/collab_order",
                          "children": [{"type": "account_card", "id": "acct",
                                        "namespace": "self/rate_limit"}]}]}
    app = RenderApp.from_dict(scene, windowed=False)
    app.start()
    app.publish("self/decisions", {  # producer live BEFORE the add
        "nodes": [{"id": "DEC-95", "title": "Handoff line",
                   "lifecycle-position": "active", "is_anchor": True,
                   "session": "2026-07-22"}],
        "edges": [], "anchor": "DEC-95"})
    w = P.hot_add_collaboration_widget(app, "decisions_summary")
    assert w is not None and w.widget_type == "decisions_summary"
    rail = next(x for x in app.comp.root.iter_widgets()
                if x.widget_id == "rail_col")
    assert w in rail.children
    assert len(w._nodes) == 1                        # retained value bound NOW
    assert w._latest()["title"] == "Handoff line"
    # a rail-misfit is refused; a repeat add is idempotent
    assert P.hot_add_collaboration_widget(app, "decisionsgraph") is None
    assert P.hot_add_collaboration_widget(app, "decisions_summary") is None


def test_s4_empty_selection_is_a_noop():
    cont = _collab_container()
    before = [c.widget_type for c in cont.children]
    changed = P.apply_collaboration_selection(_Comp(cont), {"tabs": {}})
    assert changed == 0
    assert [c.widget_type for c in cont.children] == before      # untouched


def test_s4_unregistered_persisted_type_does_not_crash():
    cont = _collab_container()
    P.apply_collaboration_selection(_Comp(cont), {"tabs": {"collaboration": {
        "widgets": ["ghost", "chatthread"], "hidden": []}}})
    assert any(c.widget_type == "chat_thread" for c in cont.children)


# --- S4  END-TO-END on the REAL console scene ([CXC-ICP-S4-DEFAULT-RAIL]) -----
# The guards the widget-placement build lacked: a real launch through
# console.compose.yaml, not a hand-built short selection the balloon never hit.

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")
_SCENE_RAIL_CARDS = ["intelligence_card", "account_card", "deliverable_card",
                     "away_watch_view"]


def _rail_children_types(app):
    rail = next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == "rail_col")
    return [c.widget_type for c in rail.children]


def test_s4_real_console_default_roster_is_the_four_scene_cards():
    """[CXC-ICP-S4-DEFAULT-RAIL] BALLOON GUARD (fails pre-fix): the DEFAULT
    'all shown, registry order' roster the collab_select emits — every fitting
    collaboration type visible, none hidden — mounts NOTHING and reorders
    nothing.  The rail is EXACTLY the four scene-declared cards, in scene
    order.  Pre-fix this ballooned to ~11 cards and sank the scene cards."""
    from composeio.render.engine.runtime import RenderApp
    from composeio.render.widgets.widget_select import WidgetSelectWidget
    default_roster = WidgetSelectWidget("collab_select").selection_payload()
    app = RenderApp.from_file(SCENE, windowed=False)
    assert _rail_children_types(app) == _SCENE_RAIL_CARDS      # baseline
    changed = P.apply_collaboration_selection(app.comp,
                                              {"tabs": default_roster})
    assert changed == 0
    assert _rail_children_types(app) == _SCENE_RAIL_CARDS      # unchanged


def test_s4_real_console_explicit_add_mounts_binds_and_keeps_scene_cards():
    """[CXC-ICP-S4-DEFAULT-RAIL] + [CXC-ICP-LAUNCH-BIND] (fails pre-fix on BOTH
    the push-off and the blank-card): an explicit ``added`` rail-fitting type
    mounts AFTER the four scene cards (never displacing them), and — wired at
    start() — BINDS the retained bus value on the first frame, not blank."""
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(SCENE, windowed=False)
    changed = P.apply_collaboration_selection(app.comp, {"tabs": {
        "collaboration": {"added": ["decisionssummary"]}}})
    assert changed >= 1
    types = _rail_children_types(app)
    assert types[:4] == _SCENE_RAIL_CARDS            # scene cards NOT pushed off
    assert "decisions_summary" in types              # explicit add mounted
    # a retained value published BEFORE start binds at start ([CXC-ICP-LAUNCH-
    # BIND] launch-time replay), so the card lights up — not blank-until-restart.
    app.publish("self/decisions", {
        "nodes": [{"id": "DEC-9", "title": "Lit at launch",
                   "lifecycle-position": "active", "is_anchor": True,
                   "session": "2026-07-24"}],
        "edges": [], "anchor": "DEC-9"})
    app.start()
    card = next(w for w in app.comp.root.iter_widgets()
                if w.widget_type == "decisions_summary")
    assert len(card._nodes) == 1                     # bound, not blank
    assert card._latest()["title"] == "Lit at launch"
    app.stop()


# --- S5 launch-read applier --------------------------------------------------

def test_s5_apply_mounts_placements_and_wires_subs():
    comp = parse_widget_tree({"widgets": [{"type": "dashboard", "id": "d"}]})
    n0 = len(comp.subscriptions)
    mounted = P.apply_dashboard(comp, {"dashboard": {"placements": [
        {"widget": "gauge", "row": 0, "col": 0, "w": 2, "h": 1},
        {"widget": "intelligence_card", "row": 0, "col": 2, "w": 1, "h": 1}]}})
    assert mounted == 2
    new_subs = [ns for ns, _ in comp.subscriptions[n0:]]
    assert "self/context_pct" in new_subs        # the placed card's data bind


def test_s5_no_dashboard_in_scene_is_a_noop():
    comp = parse_widget_tree({"widgets": [{"type": "label", "id": "l",
                                           "text": "x"}]})
    assert P.apply_dashboard(comp, {"dashboard": {"placements": [
        {"widget": "gauge", "row": 0, "col": 0, "w": 1, "h": 1}]}}) == 0


# --- S6: ONE descriptor, BOTH selectors --------------------------------------

def test_s6_new_descriptor_surfaces_in_both_selectors():
    """[CXC-ICP-S6-PLUGGABLE]: a single descriptor tagged BOTH collaboration and
    dashboard-eligible appears in the Collaboration selector AND the Dashboard
    palette after a (simulated) restart — no selector/Dashboard code edited."""
    key = canonical_key("plugwidget")
    added = key not in WIDGET_REGISTRY
    try:
        register(WidgetDescriptor(
            type="plugwidget", cls=GaugeWidget, display_name="Plug Widget",
            required_namespaces=["plug/data"], category="collaboration",
            categories=("collaboration", "dashboard-eligible")))
        # a FRESH Collaboration selector (next launch) lists it
        ws = WidgetSelectWidget("ws")
        assert "plugwidget" in [r["type"] for r in ws._rows]
        # a FRESH Dashboard palette lists it
        assert "plugwidget" in [d.type
                                for d in list_widgets("dashboard-eligible")]
        # and it MOUNTS + binds when placed
        db = DashboardWidget("db", placements=[
            {"widget": "plugwidget", "row": 0, "col": 0, "w": 1, "h": 1}])
        assert len(db.children) == 1
        assert ("plug/data", db.children[0]) in db.child_subs
    finally:
        if added:
            WIDGET_REGISTRY.pop(key, None)


# --- the grep-provable single-source contract ([CXC-ICP-S6-PLUGGABLE]) -------

def test_selectors_have_no_hardcoded_widget_list():
    """S4/S5 obtain options ONLY via list_widgets(...): the selector modules
    import list_widgets and contain no literal widget-type roster."""
    for mod in ("widget_select", "dashboard"):
        path = os.path.join(PY_ROOT, "composeio", "render", "widgets",
                            f"{mod}.py")
        src = open(path, encoding="utf-8").read()
        assert "list_widgets" in src
