"""
Tests for the MasterDetailWidget ([CXC-IC-CONFIG-MASTERDETAIL]) — the Configuration
tab's two-panel master/detail: a LEFT nav-list + a RIGHT detail pane, selection
riding _app/config/section, built as TabWidget by subclass (reuse by reference).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import MasterDetailWidget, AgentsSkillsWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.master_detail import DEFAULT_NAV_NAMESPACE
from composeio.render.engine.parser import parse_widget_tree

from .conftest import surface_to_array

RECT = Rect(0, 0, 1256, 1000)


class _Ctx:
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []
        self.redraws = 0

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def _scene(nsel="_app/config/section"):
    return {"widgets": [{
        "type": "master_detail", "id": "md", "nav_namespace": nsel,
        "sections": [
            {"label": "Bootstrap Manifest",
             "children": [{"type": "config_settings", "id": "s0",
                           "sections": ["retention"]}]},
            {"label": "Escalation Filter",
             "children": [{"type": "config_settings", "id": "s1",
                           "sections": ["escalation"]}]},
            {"label": "Agents",
             "children": [{"type": "agents_skills", "id": "ag",
                           "mode": "agents",
                           "namespace": "self/architect/agents"}]},
            {"label": "Skills",
             "children": [{"type": "agents_skills", "id": "sk",
                           "mode": "skills",
                           "namespace": "self/architect/skills"}]},
        ]}]}


def _md(comp):
    return next(w for _, w in comp.subscriptions
               if isinstance(w, MasterDetailWidget))


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# parser wiring
# =============================================================================

def test_parser_builds_master_detail_and_wires_nav_seam():
    comp = parse_widget_tree(_scene())
    md = _md(comp)
    assert md.labels == ["Bootstrap Manifest", "Escalation Filter",
                         "Agents", "Skills"]
    assert len(md.children) == 4
    assert md.nav_namespace == "_app/config/section"
    # the nav-selection namespace is subscribed
    assert ("_app/config/section", md) in comp.subscriptions


def test_all_sections_subscriptions_wired_even_when_hidden():
    """[CXC-IC-CONFIG-MASTERDETAIL] hidden sections still track bus state
    (TabWidget 'all' routing) — every section's namespaces are subscribed
    regardless of which is active."""
    comp = parse_widget_tree(_scene())
    subbed = [ns for ns, _ in comp.subscriptions]
    # both agents + skills panels subscribe though only one is ever visible
    assert "self/architect/agents" in subbed
    assert "self/architect/skills" in subbed
    # the config_settings sections' namespaces too
    assert subbed.count("intelligence/architect/config/retention") == 2


# =============================================================================
# selection — the bus-driven seam (Intelligence drives identically)
# =============================================================================

def test_bus_selection_switches_the_active_section():
    comp = parse_widget_tree(_scene())
    md = _md(comp)
    md.on_mount(_Ctx())
    assert md.active_index == 0
    md.on_bus_state("_app/config/section", "agents")
    assert md.active_index == 2
    md.on_bus_state("_app/config/section", "skills")
    assert md.active_index == 3
    # a name that is not a section is ignored (no crash, no change)
    md.on_bus_state("_app/config/section", "nonesuch")
    assert md.active_index == 3
    # index form also works
    md.on_bus_state("_app/config/section", 1)
    assert md.active_index == 1


def test_nav_value_and_index_roundtrip():
    comp = parse_widget_tree(_scene())
    md = _md(comp)
    for i, lb in enumerate(md.labels):
        assert md.nav_value(i) == lb.lower()
        assert md._index_for(lb.lower()) == i


def test_nav_tap_publishes_and_switches():
    comp = parse_widget_tree(_scene())
    md = _md(comp)
    ctx = _Ctx()
    md.on_mount(ctx)
    md.arranged_rect = RECT
    md.arrange(RECT)
    c = SkiaCanvas(); c.create_surface(1256, 1000)
    md.draw(c, RECT)                       # records nav row bounds
    assert len(md._nav_bounds) == 4
    # tap the 3rd row (Agents)
    y0, y1 = md._nav_bounds[2]
    md.on_tap(20.0, (y0 + y1) / 2.0)
    assert md.active_index == 2
    assert ctx.published[-1] == ("_app/config/section", "agents")


# =============================================================================
# hit routing — nav column vs detail pane
# =============================================================================

def test_hit_routing_splits_nav_from_detail():
    comp = parse_widget_tree(_scene())
    md = _md(comp)
    md.on_mount(_Ctx())
    md.arrange(RECT)
    c = SkiaCanvas(); c.create_surface(1256, 1000)
    md.draw(c, RECT)
    # a point in the nav column returns the container itself (taps switch)
    assert md.hit_test_tree(20.0, 30.0) is md
    # a point in the detail pane is NOT the nav (delegates past the column)
    assert md.hit_test_tree(700.0, 30.0) is not md


# =============================================================================
# draw — non-trivial, selected entry emphasised
# =============================================================================

def test_draw_paints_nav_and_active_detail():
    comp = parse_widget_tree(_scene())
    md = _md(comp)
    md.on_mount(_Ctx())
    md.on_bus_state("_app/config/section", "agents")
    # seed the agents panel so the detail pane has content
    for _, w in comp.subscriptions:
        if isinstance(w, AgentsSkillsWidget) and w.mode == "agents":
            w.on_bus_state("self/architect/agents",
                           [{"name": "composeio-expert",
                             "description": "stack expert", "loaded": True}])
    md.arrange(RECT)
    c = SkiaCanvas(); c.create_surface(1256, 1000)
    md.draw(c, RECT)
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_draws_all_section_labels():
    comp = parse_widget_tree(_scene())
    md = _md(comp)
    md.on_mount(_Ctx())
    md.arrange(RECT)
    c = SkiaCanvas(); c.create_surface(1256, 1000)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    md.draw(c, RECT)
    joined = "".join(drawn)
    for token in ("Bootstrap Manifest", "Escalation Filter", "Agents",
                  "Skills"):
        assert token in joined
