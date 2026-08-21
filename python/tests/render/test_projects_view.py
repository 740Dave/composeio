"""
Tests for the Projects-tab surface ([DEC-PROJECT-STRUCTURE]; the previs
``docs/intelligence-console/previs/config-and-projects-full.html`` #prj page):
the roster GROUPED BY FAMILY with the 3-state git indicator + the
missing-decision / structure-gap flags, and the select->swap structure detail.

Slice acceptance: the widget is DATA-DRIVEN off the ``self/projects``
projection (never re-computes structure); the git indicator renders three
visually-distinct states (own-repo / monorepo / no-git); the missing
decision-LTS is a FIRST-CLASS flag (loud when absent, an Open ▸ seam when
present); selecting a project swaps the detail; Open ▸ publishes the
doc-browser open seam.
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
from composeio.render.widgets import ProjectsViewWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.projects_view import (
    DEFAULT_NAMESPACE, DEFAULT_OPEN_NAMESPACE, FAMILY_ORDER)

from .conftest import surface_to_array

RECT = Rect(0, 0, 1256, 1524)


# a fixture covering ALL THREE git states + the two flag conditions:
#   * own-repo, HAS decision, compliant       -> the "green" case
#   * monorepo, NO decision (loud warn flag)  -> missing-decision nag
#   * no-git, non-compliant (structure gap)   -> the third git state
FIXTURE = {
    "ts": "2026-07-22T00:00:00Z",
    "projects": [
        {
            "slug": "intelligence-console", "name": "Intelligence Console",
            "family": "ComposeApps", "type": "ComposeApp",
            "root": "/repo/composeio", "status": "active", "parent": None,
            "git": {"state": "own-repo",
                    "remote": "git@github.com:740Dave/composeio.git"},
            "has_git": True, "has_decision_file": True,
            "decision_lts": "/repo/composeio/decisions/dg.lts",
            "missing": [], "compliant": True, "root_exists": True,
        },
        {
            "slug": "visual-composer", "name": "Visual Composer",
            "family": "ComposeApps", "type": "ComposeApp",
            "root": "/repo/composeio/python/composeio/composer",
            "status": "active", "parent": None,
            "git": {"state": "monorepo",
                    "remote": "git@github.com:740Dave/composeio.git",
                    "monorepo_root": "/repo/composeio"},
            "has_git": True, "has_decision_file": False,
            "decision_lts": "/repo/composeio/decisions/dg-vc.lts",
            "missing": ["README.md",
                        "decision-LTS: decisions/dg-vc.lts (no decision-LTS yet)",
                        "specs/ dir (ux_composer/specs)"],
            "compliant": False, "root_exists": True,
        },
        {
            "slug": "bus-storage", "name": "bus_storage",
            "family": "ComposeBus Services", "type": "ComposeBus Service",
            "root": "/repo/composeio/services/bus_storage",
            "status": "active", "parent": None,
            "git": {"state": "monorepo",
                    "remote": "git@github.com:740Dave/composeio.git"},
            "has_git": True, "has_decision_file": False,
            "decision_lts": "/repo/composeio/decisions/dg-bs.lts",
            "missing": ["README.md", "decision-LTS: (no decision-LTS yet)"],
            "compliant": False, "root_exists": True,
        },
        {
            "slug": "dradis-widget", "name": "BSG DRADIS Widget",
            "family": "Reactive Widgets", "type": "Reactive Widget",
            "root": "/tmp/standalone-widget", "status": "parked",
            "parent": None,
            "git": {"state": "no-git", "remote": None},
            "has_git": False, "has_decision_file": False,
            "decision_lts": "/tmp/standalone-widget/decisions/dg-dw.lts",
            "missing": ["root path not found: /tmp/standalone-widget",
                        "Reactive Widget: no *.compose.yaml widget def found"],
            "compliant": False, "root_exists": True,
        },
    ],
}


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


def _mounted(seed=True):
    w = ProjectsViewWidget("projects")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    if seed:
        w.on_bus_state(DEFAULT_NAMESPACE, FIXTURE)
    return w, ctx


def _drawn(w):
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)
    return c


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# data binding + grouping
# =============================================================================

def test_binds_self_projects_and_defaults_selection():
    w, ctx = _mounted()
    assert len(w._projects) == 4
    assert w._selected == "intelligence-console"   # first project
    assert ctx.redraws >= 1


def test_grouping_follows_family_order():
    w, _ = _mounted()
    groups = w.grouped()
    fams = [g[0] for g in groups if g[2]]           # non-empty groups
    assert fams == [f for f, _ in FAMILY_ORDER]     # ComposeApps, Services, Widgets
    composeapps = next(g for g in groups if g[0] == "ComposeApps")
    assert [p["slug"] for p in composeapps[2]] == \
        ["intelligence-console", "visual-composer"]


def test_ignores_non_dict_and_garbage_payloads():
    w, _ = _mounted(seed=False)
    w.on_bus_state(DEFAULT_NAMESPACE, "not-a-dict")
    assert w._projects == []
    w.on_bus_state(DEFAULT_NAMESPACE, {"projects": "nope"})
    assert w._projects == []


# =============================================================================
# render + the 3-state git indicator + flags
# =============================================================================

def test_draw_paints_content():
    w, _ = _mounted()
    c = _drawn(w)
    assert surface_to_array(c)[..., :3].std() > 3.0
    assert w._row_zones                              # roster rows recorded


def test_draws_family_headers_and_git_states():
    w, _ = _mounted()
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    # family headers (tracked uppercase, drawn per-glyph -> chars present)
    assert "PROJECTS" in joined.upper()
    # all three git states are labelled on the roster
    for label in ("own-repo", "monorepo", "no git"):
        assert label in joined
    # the missing-decision flag copy in the detail (default selection is the
    # compliant one; select a flagged one to see it)
    w2, _ = _mounted()
    w2._selected = "visual-composer"
    c2 = SkiaCanvas(); c2.create_surface(1256, 1524)
    drawn2 = []
    real2 = c2.draw_text
    c2.draw_text = lambda s, *a, **k: (drawn2.append(s), real2(s, *a, **k))[1]
    w2.draw(c2, RECT)
    joined2 = "".join(drawn2)
    assert "no decision-LTS yet" in joined2
    assert "structure gap" in joined2               # the gap-list header


def test_git_style_three_distinct_states():
    w, _ = _mounted()
    t = ct.CONSOLE.active
    own = w._git_style("own-repo", t)
    mono = w._git_style("monorepo", t)
    nogit = w._git_style("no-git", t)
    # distinct labels
    assert {own[0], mono[0], nogit[0]} == {"own-repo", "monorepo", "no git"}
    # distinct colours (own azure, mono violet)
    assert own[1] != mono[1]
    # no-git is the ONLY hollow (shape-distinct even in greyscale)
    assert own[2] is True and mono[2] is True and nogit[2] is False


# =============================================================================
# decision-LTS = first-class: Open ▸ present-only, publishes the doc seam
# =============================================================================

def test_open_decision_lts_present_publishes_doc_seam():
    w, ctx = _mounted()          # default selection HAS a decision file
    _drawn(w)
    assert w._open_rect is not None
    assert w._open_path == "/repo/composeio/decisions/dg.lts"
    r = w._open_rect
    w.on_tap(r.x + r.w / 2.0, r.y + r.h / 2.0)
    assert ctx.published == [(DEFAULT_OPEN_NAMESPACE,
                             "/repo/composeio/decisions/dg.lts")]


def test_missing_decision_has_no_open_seam():
    w, ctx = _mounted()
    w._selected = "visual-composer"      # no decision file
    _drawn(w)
    assert w._open_rect is None          # flagged, not a dead link
    # a tap where the chip would be publishes nothing
    w.on_tap(RECT.w - 40, 200)
    assert all(ns != DEFAULT_OPEN_NAMESPACE for ns, _ in ctx.published)


# =============================================================================
# select -> swap detail
# =============================================================================

def test_selecting_a_row_swaps_the_detail():
    w, ctx = _mounted()
    _drawn(w)
    # find the bus_storage row zone and tap it
    target = next(r for r, slug in w._row_zones if slug == "bus-storage")
    before = ctx.redraws
    w.on_tap(target.x + 20, target.y + target.h / 2.0)
    assert w._selected == "bus-storage"
    assert ctx.redraws > before
    assert w.selected_project()["name"] == "bus_storage"


def test_selecting_the_same_row_is_a_noop():
    w, ctx = _mounted()
    _drawn(w)
    target = next(r for r, slug in w._row_zones
                  if slug == "intelligence-console")     # already selected
    before = ctx.redraws
    w.on_tap(target.x + 20, target.y + target.h / 2.0)
    assert ctx.redraws == before


# =============================================================================
# parser wiring — ONE subscribed namespace (self/projects)
# =============================================================================

def test_parser_wires_one_subscription():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "projects_view",
                                           "id": "pv"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed == [DEFAULT_NAMESPACE]
    w = comp.subscriptions[0][1]
    assert isinstance(w, ProjectsViewWidget)
    assert w.interactive is True


# =============================================================================
# glass-checkable PNG proofs (roster + a project selected)
# =============================================================================

def test_projects_view_render_png():
    """Golden capture: the full roster + the (compliant, own-repo) default
    selection showing the Open ▸ decision seam."""
    w, _ = _mounted()
    c = _drawn(w)
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    out = os.path.join(cap_dir, "projects_view.png")
    c.save_png(out)
    assert os.path.exists(out) and os.path.getsize(out) > 3000


def test_projects_view_flagged_selection_png():
    """A flagged project selected (monorepo, NO decision-LTS) — the
    missing-decision nag + the structure-gap list."""
    w, _ = _mounted()
    w._selected = "visual-composer"
    c = _drawn(w)
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    out = os.path.join(cap_dir, "projects_view_flagged.png")
    c.save_png(out)
    assert os.path.exists(out) and os.path.getsize(out) > 3000
