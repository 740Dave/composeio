"""
Tests for the Away-Watch condensed roster rail card (David 2026-07-22 redesign:
"Match the Artifacts widget's visual language ... ONE representation, not two.
The card is now just a condensed roster ... Clicking a row opens the Night Watch
window — the detail IS the window now").

Slice acceptance: the card is DATA-DRIVEN off ``self/away_watches`` (never
re-derives it); it lists entries most-recent-first as ONE compact row each
(label + date + a Decisions·Tokens·Elapsed stat line); tapping a row publishes
the Night Watch relaunch INTENT with the scene_path.  There is NO in-card detail
pane and NO artifact chips (those moved to the window) — the card emits only the
launch intent.
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
from composeio.render.widgets import AwayWatchViewWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.away_watch_view import (
    DEFAULT_NAMESPACE, DEFAULT_OPEN_NAMESPACE, DEFAULT_LAUNCH_NAMESPACE,
    _fmt_tokens, _fmt_elapsed, _stat_line)

from .conftest import surface_to_array

# the rail card geometry it is deployed at (width 332, height 372)
RECT = Rect(0, 0, 332, 372)

FIXTURE = {
    "ts": "2026-07-22T08:00:00Z",
    "watches": [
        {
            "id": 3, "session_id": "overnight-2026-07-22",
            "label": "Overnight 2026-07-22",
            "started_ts": 1784703000.0, "ended_ts": 1784731320.0,
            "decisions": 12, "tokens": 23_700_000, "elapsed": 28320.0,
            "scene_path": "compose_projects/intelligence/console/nightwatch.compose.yaml",
        },
        {
            "id": 2, "session_id": "prev", "label": "Overnight 2026-07-15",
            "started_ts": 1752537600.0, "ended_ts": None,
            "decisions": 7, "tokens": 9_100_000,
            "scene_path": "compose_projects/intelligence/console/nightwatch.compose.yaml",
        },
        {
            "id": 1, "session_id": "old", "label": "Overnight 2026-07-09",
            "started_ts": 1751932800.0,
            "decisions": 3, "tokens": 420_000,
            "scene_path": "",
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
    w = AwayWatchViewWidget("away")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    if seed:
        w.on_bus_state(DEFAULT_NAMESPACE, FIXTURE)
    return w, ctx


def _drawn(w):
    c = SkiaCanvas(); c.create_surface(int(RECT.w), int(RECT.h))
    w.draw(c, RECT)
    return c


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# formatting helpers
# =============================================================================

def test_token_and_elapsed_formatters():
    assert _fmt_tokens(23_700_000) == "23.7M"
    assert _fmt_tokens(9_100_000) == "9.1M"
    assert _fmt_tokens(420_000) == "420k"
    assert _fmt_tokens(12) == "12"
    # elapsed from the projected seconds, else derived from start/end
    assert _fmt_elapsed({"elapsed": 28320.0}) == "7h 52m"
    assert _fmt_elapsed({"started_ts": 0.0, "ended_ts": 3600.0}) == "1h 0m"
    assert _fmt_elapsed({"started_ts": 0.0, "ended_ts": 300.0}) == "5m"
    assert _fmt_elapsed({}) == ""


def test_stat_line_is_decisions_tokens_elapsed():
    line = _stat_line(FIXTURE["watches"][0])
    assert "12 decisions" in line and "23.7M tok" in line and "7h 52m" in line
    # the OLD commits/agents/tests vocabulary is gone
    assert "commits" not in line and "agents" not in line


# =============================================================================
# data binding
# =============================================================================

def test_binds_self_away_watches_most_recent_first():
    w, ctx = _mounted()
    assert len(w._watches) == 3
    assert w._watches[0]["label"] == "Overnight 2026-07-22"
    assert ctx.redraws >= 1


def test_ignores_non_dict_and_garbage_payloads():
    w, _ = _mounted(seed=False)
    w.on_bus_state(DEFAULT_NAMESPACE, "not-a-dict")
    assert w._watches == []
    w.on_bus_state(DEFAULT_NAMESPACE, {"watches": "nope"})
    assert w._watches == []


# =============================================================================
# render — a condensed roster (one row per watch), Artifacts-sibling chrome
# =============================================================================

def test_draw_paints_content_and_records_one_zone_per_watch():
    w, _ = _mounted()
    c = _drawn(w)
    assert surface_to_array(c)[..., :3].std() > 3.0
    assert len(w._row_zones) == 3               # one hit zone per watch


def test_lists_labels_with_new_stat_line_and_sibling_header():
    w, _ = _mounted()
    c = SkiaCanvas(); c.create_surface(int(RECT.w), int(RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    # the header is the tracked UPPERCASE mono title (draw_tracked draws per-glyph)
    assert "AWAY WATCH" in joined.upper()
    assert "Overnight 2026-07-22" in joined     # the roster label
    # the new Decisions·Tokens·Elapsed stat line, NOT the old vocabulary
    assert "decisions" in joined and "tok" in joined
    assert "commits" not in joined and "agents" not in joined
    # no in-card detail pane / no artifact chips: those strings never appear
    assert "Open watch" not in joined
    assert "Build log" not in joined and "ARTIFACTS" not in joined


def test_no_detail_pane_or_artifact_zones_exist():
    """The redesign removed the select→swap detail + artifact chips entirely."""
    w, _ = _mounted()
    _drawn(w)
    assert not hasattr(w, "_artifact_zones")
    assert not hasattr(w, "_launch_rect")
    assert not hasattr(w, "_selected")


# =============================================================================
# ONE interaction: tapping a row opens the Night Watch window (launch intent)
# =============================================================================

def test_row_tap_publishes_launch_intent():
    w, ctx = _mounted()
    _drawn(w)
    row_rect, wch = w._row_zones[0]             # newest
    w.on_tap(row_rect.x + 20, row_rect.y + row_rect.h / 2.0)
    assert len(ctx.published) == 1
    ns, val = ctx.published[0]
    assert ns == DEFAULT_LAUNCH_NAMESPACE
    assert val["scene_path"] == "compose_projects/intelligence/console/nightwatch.compose.yaml"
    assert val["label"] == "Overnight 2026-07-22"


def test_row_without_scene_path_is_a_noop():
    w, ctx = _mounted()
    _drawn(w)
    row_rect, wch = next((r, x) for r, x in w._row_zones if not x.get("scene_path"))
    w.on_tap(row_rect.x + 20, row_rect.y + row_rect.h / 2.0)
    assert ctx.published == []                   # no scene -> nothing published


def test_tap_never_publishes_the_doc_open_seam():
    """A SCENE-typed row (`.compose.yaml`) opens via the launch seam, never the
    doc-open seam. (The fixture rows are scenes/empty — a DOC-typed target routes
    differently, asserted in test_doc_typed_row_tap_* below.)"""
    w, ctx = _mounted()
    _drawn(w)
    for row_rect, _ in w._row_zones:
        w.on_tap(row_rect.x + 20, row_rect.y + row_rect.h / 2.0)
    assert all(ns == DEFAULT_LAUNCH_NAMESPACE for ns, _ in ctx.published)
    assert DEFAULT_OPEN_NAMESPACE not in [ns for ns, _ in ctx.published]


# =============================================================================
# [CXC-RETRO-AWAYWATCH-OPEN-TYPED] a row's open target is TYPED: a scene relaunches
# the Night Watch window; a DOC (a report .md, e.g. the maker away-watch) opens in
# the doc-viewer via the doc-open seam — never the Night Watch runtime (which
# cannot parse a .md and dies silently: the 2026-07-26 not-clickable defect).
# =============================================================================

_DOC_FIXTURE = {
    "ts": "2026-07-26T08:00:00Z",
    "watches": [{
        "id": 9, "session_id": "59f1b46e", "label": "Away 2026-07-26 (maker)",
        "started_ts": 1785500000.0, "decisions": 4, "tokens": 0,
        # a REPORT away-watch: its open target is a doc, not a .compose.yaml scene
        "scene_path": "docs/watch_reports/away-watch-2026-07-26-report.md",
    }],
}


def test_doc_typed_row_tap_opens_the_doc_viewer_not_the_scene_launcher():
    """The maker defect: tapping a row whose scene_path is a `.md` must publish the
    doc-open intent (path only) so doc_viewer_launcher opens it — NOT the Night
    Watch launch intent (which would spawn `runtime <report.md>` and die silently).
    FAILS before the fix (the row published DEFAULT_LAUNCH_NAMESPACE with the .md)."""
    w = AwayWatchViewWidget("away")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    w.on_bus_state(DEFAULT_NAMESPACE, _DOC_FIXTURE)
    _drawn(w)
    row_rect, _ = w._row_zones[0]
    w.on_tap(row_rect.x + 20, row_rect.y + row_rect.h / 2.0)
    assert len(ctx.published) == 1
    ns, val = ctx.published[0]
    assert ns == DEFAULT_OPEN_NAMESPACE          # the doc-open seam, not launch
    assert val == "docs/watch_reports/away-watch-2026-07-26-report.md"


def test_scene_predicate_is_the_shared_compose_yaml_invariant():
    """The widget router's scene-vs-doc predicate — the SAME invariant the launcher
    guard (away_watch_publisher._scene_ok) enforces: only `.compose.yaml` is a
    Night Watch scene."""
    from composeio.render.widgets.away_watch_view import _is_scene, SCENE_SUFFIX
    assert SCENE_SUFFIX == ".compose.yaml"
    assert _is_scene("compose_projects/intelligence/console/nightwatch.compose.yaml") is True
    assert _is_scene("docs/report.md") is False
    assert _is_scene("") is False
    assert _is_scene(None) is False


# =============================================================================
# parser wiring — ONE subscribed namespace (self/away_watches)
# =============================================================================

def test_parser_wires_one_subscription():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "away_watch_view",
                                           "id": "aw"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed == [DEFAULT_NAMESPACE]
    w = comp.subscriptions[0][1]
    assert isinstance(w, AwayWatchViewWidget)
    assert w.interactive is True


# =============================================================================
# glass-checkable PNG proof (the condensed, Artifacts-styled roster)
# =============================================================================

def test_away_watch_view_render_png():
    """Golden capture: the condensed Away-Watch roster — card chrome, tracked
    header, one row per watch with the Decisions·Tokens·Elapsed stat line."""
    w, _ = _mounted()
    c = _drawn(w)
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    out = os.path.join(cap_dir, "away_watch_view.png")
    c.save_png(out)
    assert os.path.exists(out) and os.path.getsize(out) > 2000
