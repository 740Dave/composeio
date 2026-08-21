"""
test_widget_registry — [SPEC:CXC-CONSOLE-PROD] [CXC-ICP-FOUND-B].

Foundation B: the implicit ``type:``->widget-class map inside
``parser.py :: build_leaf`` is made an EXPLICIT, enumerable ``WIDGET_REGISTRY``
of descriptors (display_name, required_namespaces, category, class), so the
console personalization surfaces (S4 selector, S5 Dashboard palette, S6) can
LIST and SELECT widgets without a hardcoded list.

Covers the spec's verify.machine gates:
  (a) every widget type reachable via a scene ``type:`` today is present in
      WIDGET_REGISTRY with a non-empty display_name and a class;
  (b) ``list_widgets()`` enumerates them (and filters by category);
  (c) ``build_leaf`` for every registered type returns an instance of the
      descriptor's ``cls``;
  (d) an unknown ``type:`` still yields ``PlaceholderWidget``;
  (e) there is exactly ONE definition of the type->class map (the WIDGET_REGISTRY
      dict — grep-provable single site) + the S6 pluggable ``build`` hook.
"""
from __future__ import annotations

import pytest

from composeio.render.engine import widget_registry as wr
from composeio.render.engine.parser import Composition, build_leaf
from composeio.render.engine.layout import FlexContainer
from composeio.render.widgets import PlaceholderWidget


def _comp() -> Composition:
    return Composition(root=FlexContainer("root"))


# The canonical scene ``type:`` tokens (primary + alias) reachable through
# ``build_leaf`` today.  If a branch is added to build_leaf without a registry
# row, (a)/(c) here fail — the coverage tripwire.
SCENE_TYPES = [
    "gauge", "led", "relay", "button", "command", "textinput", "text_input",
    "checkbox", "powertoggle", "power_toggle", "docpicker", "doc_picker",
    "busmonitor", "bus_monitor", "chatthread", "chat_thread", "activity",
    "subagentledger", "subagent_ledger", "registeredchecklist",
    "registered_checklist", "nightwatchchecklist", "nightwatch_checklist",
    "task_checklist", "taskchecklist", "nightwatchartifacts",
    "nightwatch_artifacts", "decisionsgraph", "decisions_graph", "decisionstab",
    "decisions_tab", "decisionssummary", "decisions_summary",
    "ltsenginerail", "lts_engine_rail", "selfledger",
    "self_ledger", "deliverablecard", "deliverable_card", "deliverablealert",
    "deliverable_alert", "deliverablehistory", "deliverable_history",
    "bootstrapmanifest", "bootstrap_manifest", "configsettings",
    "config_settings", "docbrowser", "doc_browser", "projectsview",
    "projects_view", "awaywatchview", "away_watch_view", "consoletabs",
    "console_tabs", "masterdetail", "master_detail", "agentsskills",
    "agents_skills", "intelligencecard", "intelligence_card", "accountcard",
    "account_card", "modelpicker", "model_picker", "avatar", "themetoggle",
    "theme_toggle", "coordinate", "coordinateentry", "coordinate_entry",
    "callout", "tab", "tabcontainer", "tab_container", "label", "rectangle",
    "rect", "circle", "markdownviewer", "markdown_viewer", "touchviz",
    "touch_viz", "linkhealth", "link_health", "image", "lottie", "svg",
    "media", "mermaid", "logic",
]


# ── (a) coverage: every scene type is registered, with a name + class ───────
@pytest.mark.parametrize("wtype", SCENE_TYPES)
def test_every_scene_type_is_registered(wtype):
    desc = wr.get_descriptor(wtype)
    assert desc is not None, f"{wtype!r} missing from WIDGET_REGISTRY"
    assert desc.cls is not None, f"{wtype!r} descriptor has no class"
    assert desc.display_name and desc.display_name.strip(), \
        f"{wtype!r} has an empty display_name"


def test_registry_lints_clean():
    assert wr.lint_registry() == []


# ── (b) list_widgets enumerates + filters ───────────────────────────────────
def test_list_widgets_enumerates_all():
    all_descs = wr.list_widgets()
    assert len(all_descs) >= 40
    # unique by identity — aliases do not double-count.
    assert len({id(d) for d in all_descs}) == len(all_descs)
    names = {d.display_name for d in all_descs}
    assert "Message Thread" in names and "Gauge" in names


def test_list_widgets_filters_by_category():
    collab = wr.list_widgets(category="collaboration")
    assert collab, "no collaboration widgets"
    assert all("collaboration" in d.categories for d in collab)
    assert any(d.type == "chatthread" for d in collab)

    dash = wr.list_widgets(category="dashboard-eligible")
    assert dash, "no dashboard-eligible widgets"
    assert all("dashboard-eligible" in d.categories for d in dash)


def test_descriptor_carries_required_namespaces():
    act = wr.get_descriptor("activity")
    assert act.required_namespaces == ["self/activity"]


# ── (c) build_leaf returns an instance of the descriptor's class ────────────
@pytest.mark.parametrize("wtype", SCENE_TYPES)
def test_build_leaf_matches_descriptor_class(wtype):
    desc = wr.get_descriptor(wtype)
    w = build_leaf({"type": wtype}, _comp())
    assert isinstance(w, desc.cls), \
        f"{wtype!r}: build_leaf returned {type(w).__name__}, " \
        f"registry says {desc.cls.__name__}"


# ── (d) unknown type still yields PlaceholderWidget ─────────────────────────
def test_unknown_type_yields_placeholder():
    w = build_leaf({"type": "not_a_widget"}, _comp())
    assert isinstance(w, PlaceholderWidget)
    assert wr.get_descriptor("not_a_widget") is None
    assert wr.resolve_class("not_a_widget") is None


# ── (e) single-site map + the S6 pluggable build hook ───────────────────────
def test_single_definition_site():
    """The ENGINE-CORE type->class map is defined in exactly ONE place: the
    WIDGET_REGISTRY dict, populated from parser's ``_REGISTRY_ROWS``.  No other
    module defines a parallel engine map.

    [CXC-PROJECT-WIDGETS]: a project may ADD descriptors on top by importing its
    own widget module (``widget_modules:``), so ``list_widgets()`` is a SUPERSET
    of the engine rows — but every engine-core row remains a single authoring
    surface (one row per unique engine descriptor), and each is registered."""
    import composeio.render.engine.parser as parser_mod
    assert isinstance(wr.WIDGET_REGISTRY, dict)
    assert hasattr(parser_mod, "_REGISTRY_ROWS")
    # one row per unique engine-core descriptor (the single authoring surface):
    # the rows de-dup to exactly themselves (no duplicate engine authoring site).
    row_types = [r[0] for r in parser_mod._REGISTRY_ROWS]
    assert len(row_types) == len(set(row_types))
    # every engine-core row is registered with a class.
    for rtype in row_types:
        assert wr.get_descriptor(rtype) is not None
    # the global registry is a superset (project widgets may have registered).
    assert len(wr.list_widgets()) >= len(parser_mod._REGISTRY_ROWS)


# ── D1 placement schema: min_size + optional region_affinity ────────────────
# ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1)

def test_descriptor_placement_defaults_fit_anywhere():
    """A descriptor that declares no placement floor keeps ``min_size=(0,0)``
    (fits any area) and ``region_affinity=None`` (no curation) — the honest
    shipped default."""
    d = wr.WidgetDescriptor(type="p", cls=PlaceholderWidget, display_name="P")
    assert d.min_size == (0, 0)
    assert d.region_affinity is None
    assert d.fits(10, 10) and d.fits(None, None)
    assert d.fits_region("rail", 10, 10)          # absent affinity defers to fit


def test_descriptor_min_size_and_affinity_round_trip():
    """min_size is coerced to an int pair; region_affinity to a frozenset —
    both readable back exactly ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1)."""
    d = wr.WidgetDescriptor(
        type="q", cls=PlaceholderWidget, display_name="Q",
        min_size=(480.0, 340), region_affinity={"hero", "dashboard"})
    assert d.min_size == (480, 340)
    assert d.region_affinity == frozenset({"hero", "dashboard"})


def test_fits_region_is_min_size_AND_affinity():
    """fits_region is the curation (affinity) AND the dimension (min_size)
    gate: a declared affinity excluding the region is a miss regardless of
    dimensions; then min_size must clear the area."""
    big = wr.WidgetDescriptor(
        type="big", cls=PlaceholderWidget, display_name="Big",
        min_size=(480, 340), region_affinity={"hero", "dashboard", "tab"})
    assert not big.fits_region("rail", 332, None)     # affinity excludes rail
    assert not big.fits_region("hero", 300, None)     # affinity ok, too wide
    assert big.fits_region("hero", 900, 900)          # affinity ok + fits
    assert big.redirect_region("rail") == "dashboard"

    railcard = wr.WidgetDescriptor(
        type="rc", cls=PlaceholderWidget, display_name="RC",
        min_size=(300, 150), region_affinity={"rail"})
    assert railcard.fits_region("rail", 332, None)    # width-bounded column
    assert not railcard.fits_region("rail", 200, None)  # narrower than min


def test_shipped_placement_populated_for_catalog():
    """The shipped collaboration + dashboard descriptors carry a realistic
    min_size, and the area-hungry ones a curating region_affinity that steers
    them off the 332px rail ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1)."""
    # a compact rail-fitting collaboration card fits the rail
    summ = wr.get_descriptor("decisions_summary")
    assert summ.min_size[0] <= 332 and summ.region_affinity == frozenset({"rail"})
    assert summ.fits_region("rail", 332, None)
    # the area-hungry decisions widgets are steered off the rail -> dashboard
    for t in ("decisionsgraph", "decisionstab", "mermaid", "deliverablehistory"):
        d = wr.get_descriptor(t)
        assert d.min_size[0] > 332
        assert not d.fits_region("rail", 332, None)
        assert d.redirect_region("rail") == "dashboard"
    # a dashboard-eligible control carries a real min_size floor
    assert wr.get_descriptor("gauge").min_size == (200, 160)


def test_pluggable_build_hook_constructs_without_editing_build_leaf():
    """A NEW widget registered with a ``build`` hook is constructed THROUGH the
    registry by build_leaf — the S6 closed loop (no build_leaf edit)."""
    class _SyntheticWidget(PlaceholderWidget):
        pass

    def _build(spec, wid, comp):
        return _SyntheticWidget(wid, original_type="synthetic",
                                label=str(spec.get("label", "")))

    key = wr.canonical_key("synthetic_probe")
    wr.register(wr.WidgetDescriptor(
        type="synthetic_probe", cls=_SyntheticWidget,
        display_name="Synthetic Probe", category="dashboard-eligible",
        build=_build))
    try:
        assert any(d.type == "synthetic_probe"
                   for d in wr.list_widgets(category="dashboard-eligible"))
        w = build_leaf({"type": "synthetic_probe", "label": "x"}, _comp())
        assert isinstance(w, _SyntheticWidget)
    finally:
        wr.WIDGET_REGISTRY.pop(key, None)
