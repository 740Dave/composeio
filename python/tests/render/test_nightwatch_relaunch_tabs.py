"""Night Watch relaunch — the CLICKED entry reaches EVERY tab, not just the
header ([SPEC:CXC-RETRO §5d], David 2026-07-23).

Regression guard for the tab-recurrence of the away-watch wrong-entry defect:
commit 0acecba seeded only the SCALAR HEADER, so the Artifacts + Decisions tabs
kept rendering the scene's ONE baked-in entry (a prior watch's artifacts, a
stale decision neighborhood) and artifact rows were inert. These tests FAIL on
the pre-§5d launcher/scene (artifacts/decisions_graph absent from the seed; the
window has no outbound transport) and PASS after.

The LOAD-BEARING one is test_seed_covers_every_data_namespace_the_scene_binds:
it derives the required namespaces FROM the deployed scene, so ANY future tab or
field added to the window that the launcher forgets to seed fails loudly — the
map and the scene's data namespaces are kept in lockstep ([CXC-RETRO-RELAUNCH-COMPLETE]).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS = str(REPO_ROOT / "compose_projects" / "intelligence" / "tools")
for p in (PY_ROOT, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.nightwatch_artifacts import (
    NightWatchArtifactsWidget, DEFAULT_OPEN_NAMESPACE)

import away_watch_publisher as awp

SCENE = REPO_ROOT / "compose_projects" / "intelligence" / "console" / "nightwatch.compose.yaml"

# The window's warm-close CHROME + tab-control namespaces — NOT per-watch data,
# so they are deliberately outside the seed-completeness lockstep (documented in
# §5d: the seed covers the header scalars + the tabs, not the fixed sign-off).
CHROME_AND_CONTROL = {
    "fun/nightwatch/tab",
    "fun/nightwatch/_nobadge",
    "fun/nightwatch/greeting",
    "fun/nightwatch/message",
    "fun/nightwatch/subtitle",
}

# a distinct CLICKED watch (an in-progress afternoon entry) whose data must NOT
# be confusable with the scene's baked overnight default.
CLICKED = {
    "label": "Overnight 2026-07-23",
    "session_id": "59f1b46e-afternoon",
    "title": "Away Watch: Thu, Jul 23 @ 13:10",
    "started_fmt": "Thu, Jul 23 @ 13:10",
    "ended_fmt": "",                       # in progress
    "decisions": 3, "tokens": 1_200_000, "elapsed": None,
    "git_state": "clean",
    "tasks": [{"label": "nightwatch tabs defect", "status": "partial"}],
    "artifacts": [
        {"title": "Overnight build log",
         "path": "docs/intelligence-console/BUILD-LOG-2026-07-23-overnight.md"},
        {"title": "tab-scoping analysis", "path": "docs/nightwatch-tabs.md"},
    ],
}

_STUB_PROJECTION = {
    "corpus": "intelligence-console", "anchor": "DEC-FRESH",
    "nodes": [{"id": "DEC-FRESH", "title": "fresh", "is_anchor": True}],
    "edges": [],
}


def teardown_function(_fn):
    ct.set_theme("dark")


# --- walk the deployed scene for the namespaces its widgets BIND ------------

def _bound_namespaces(node, out):
    if isinstance(node, dict):
        for key in ("namespace",):
            v = node.get(key)
            if isinstance(v, str):
                out.add(v)
        txt = node.get("text")
        if isinstance(txt, dict) and isinstance(txt.get("namespace"), str):
            out.add(txt["namespace"])
        for v in node.values():
            _bound_namespaces(v, out)
    elif isinstance(node, list):
        for it in node:
            _bound_namespaces(it, out)


def _scene_data_namespaces():
    doc = yaml.safe_load(SCENE.read_text())
    found = set()
    _bound_namespaces(doc.get("widgets"), found)
    # per-watch DATA the seed must cover: fun/nightwatch/* + the decisions graph,
    # minus the fixed chrome/control namespaces.
    data = {ns for ns in found
            if ns.startswith("fun/nightwatch/")} - CHROME_AND_CONTROL
    return data


# =============================================================================
# THE recurrence guard — seed covers every data namespace the scene binds
# =============================================================================

def test_seed_covers_every_data_namespace_the_scene_binds():
    """[CXC-RETRO-RELAUNCH-COMPLETE] every per-watch namespace the deployed
    window binds is present in the launcher's initials map. FAILS pre-fix
    (fun/nightwatch/artifacts was bound by the tab but never seeded).

    [SPEC:CXC-GRAPH-VIEWER]: the Decisions tab (and its decisions_graph
    namespace) is REMOVED; the run SCOPE namespace takes its place in the seed."""
    required = _scene_data_namespaces()
    seed = awp.nightwatch_initials(CLICKED)
    missing = required - set(seed)
    assert not missing, f"tabs bound but NOT seeded (silent scene default): {missing}"
    # the remaining tab namespace is specifically covered (the 0acecba gap)
    assert awp.ARTIFACTS_NS in seed
    # the scope that replaced the removed decisions feed ([CXC-GV-SCOPE])
    assert awp.SESSIONS_NS in seed


# =============================================================================
# Artifacts tab — the CLICKED entry's artifacts, and they are click-to-open
# =============================================================================

def test_seed_artifacts_are_the_clicked_entrys_not_the_default():
    seed = awp.nightwatch_initials(CLICKED)
    assert seed[awp.ARTIFACTS_NS] == CLICKED["artifacts"]
    # NOT the scene's baked overnight default (Build log / DRADIS gap analysis)
    titles = [a["title"] for a in seed[awp.ARTIFACTS_NS]]
    assert "DRADIS gap analysis" not in titles
    assert titles == ["Overnight build log", "tab-scoping analysis"]


def test_artifact_row_tap_publishes_doc_open_intent():
    """A tapped artifact row publishes _app/doc/open = the path (the shared
    doc_viewer_launcher seam) — the click-to-open half of the defect."""
    published = []

    class _Ctx:
        theme = "dark"; reduced_motion = False
        def request_redraw(self, rect=None): pass
        def publish(self, ns, value, source=None): published.append((ns, value))

    w = NightWatchArtifactsWidget("arts")
    w.on_mount(_Ctx())
    rect = Rect(0, 0, 500, 284)
    w.on_bus_state(w.namespace, CLICKED["artifacts"])
    c = SkiaCanvas(); c.create_surface(500, 284)
    w.draw(c, rect)                        # populates _row_zones
    r0, path0 = w._row_zones[0]
    w.on_tap(r0.x + 20, r0.y + r0.h / 2.0)
    assert published == [(DEFAULT_OPEN_NAMESPACE,
                          "docs/intelligence-console/BUILD-LOG-2026-07-23-overnight.md")]


def test_window_carries_an_outbound_transport_so_the_intent_escapes():
    """[CXC-RETRO-ARTIFACTS-OPENABLE] the separate-process window declares an
    outbound MQTT transport, without which _app/doc/open reaches no launcher
    (the old transports:[] left every artifact row inert)."""
    doc = yaml.safe_load(SCENE.read_text())
    transports = doc.get("app", {}).get("transports") or []
    assert any(isinstance(t, dict) and t.get("type") == "mqtt"
               for t in transports), "window has no global leg for _app/doc/open"


# =============================================================================
# Decisions tab — per-window namespace, seeded fresh (not the shared global)
# =============================================================================

def test_embedded_decisions_tab_is_removed_no_decision_feed_remains():
    """[SPEC:CXC-GRAPH-VIEWER] the embedded Decisions tab is GONE (David
    2026-08-06): the window binds NEITHER the per-window decisions_graph feed
    NOR the shared global self/decisions.  Decisions are explored in the
    standalone Graph Viewer, reached by the "View Decisions" intent button — one
    decision surface, not two."""
    doc = yaml.safe_load(SCENE.read_text())
    found = set()
    _bound_namespaces(doc.get("widgets"), found)
    assert "fun/nightwatch/decisions_graph" not in found
    assert "self/decisions" not in found
    # the namespace declaration went with it (no orphan declaration)
    declared = {n.get("name") for n in (doc.get("namespaces") or [])
                if isinstance(n, dict)}
    assert "fun/nightwatch/decisions_graph" not in declared
    # ...and no decisions_graph WIDGET survives anywhere in the scene (a
    # structural check — the retirement is explained in prose comments, which a
    # raw substring scan would false-positive on).
    def _types(node, out):
        if isinstance(node, dict):
            if node.get("type"):
                out.add(str(node["type"]))
            for v in node.values():
                _types(v, out)
        elif isinstance(node, list):
            for v in node:
                _types(v, out)
    widget_types: set = set()
    _types(doc.get("widgets"), widget_types)
    assert "decisions_graph" not in widget_types
    assert "decisions_tab" not in widget_types


def test_run_scope_replaces_the_decision_seed():
    """[CXC-GV-SCOPE] the launcher seeds the run's SESSION IDS (the scope the
    "View Decisions" button carries), and NEVER a decision projection — a legacy
    decisions_projection argument is accepted-and-ignored, not written."""
    seed = awp.nightwatch_initials(CLICKED)
    assert isinstance(seed[awp.SESSIONS_NS], list)
    assert seed[awp.SESSIONS_NS] == awp.watch_sessions(CLICKED)
    # the removed feed is never seeded, even if an old caller passes one
    stale = awp.nightwatch_initials(CLICKED, decisions_projection=_STUB_PROJECTION)
    assert "fun/nightwatch/decisions_graph" not in stale
    # the builder for that feed is gone with the tab it fed
    assert not hasattr(awp, "build_decisions_seed")


def test_watch_sessions_window_opened_with_tokens_returns_the_tokens():
    """[CXC-GV-SCOPE] case 1 — a run that OBSERVED sessions scopes to them."""
    assert awp.watch_sessions({"decision_baseline_ts": 1785000000.0,
                               "sessions": ["s-a", "s-b"],
                               "session_id": "shared-uuid"}) == ["s-a", "s-b"]


def test_watch_sessions_window_opened_but_empty_is_HONESTLY_EMPTY():
    """[CXC-GV-SCOPE] case 2 — the load-bearing one.  A run that opened an
    observation window and observed NOTHING must return [] (the viewer then
    shows its loud UNSCOPED banner), NEVER the shared session_id — that fallback
    would hand back the merged-four-runs scope this feature exists to eliminate."""
    row = {"decision_baseline_ts": 1785000000.0, "sessions": [],
           "session_id": "59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3"}
    assert awp.watch_sessions(row) == []
    assert awp.has_observation_window(row) is True
    # absent `sessions` key entirely, window still open -> same honest answer
    assert awp.watch_sessions({"decision_baseline_ts": 1785000000.0,
                               "session_id": "shared-uuid"}) == []


def test_watch_sessions_legacy_row_without_a_window_keeps_the_fallback():
    """[CXC-GV-SCOPE] case 3 — a row predating observation (all 5 current rows)
    has no window, so session_id remains the best available signal.

    DEFENSIVE: the column may be ABSENT from this deployment entirely, or
    PRESENT-but-NULL once the watch-sessions build lands and backfills legacy
    rows.  Both must read as 'no window'."""
    # column absent entirely (today's schema)
    row_absent = {"session_id": "sid-legacy"}
    assert awp.has_observation_window(row_absent) is False
    assert awp.watch_sessions(row_absent) == ["sid-legacy"]
    # column present but NULL (legacy row after the other build lands)
    row_null = {"decision_baseline_ts": None, "session_id": "sid-legacy"}
    assert awp.has_observation_window(row_null) is False
    assert awp.watch_sessions(row_null) == ["sid-legacy"]
    # and a legacy row with no session id at all is still simply empty
    assert awp.watch_sessions({"decision_baseline_ts": None}) == []


def test_watch_sessions_never_invents_a_session():
    """[CXC-GV-SCOPE] HONEST SCOPE: watch_sessions returns only the id the run
    genuinely carries.  A run with no session id yields [] (the viewer then
    degrades loudly) — it is NEVER derived from the run's date/label, because a
    derived match would be a plausible-but-WRONG scope."""
    assert awp.watch_sessions({"session_id": "sid-1"}) == ["sid-1"]
    # a run spanning several sessions preselects ALL of them
    assert awp.watch_sessions({"sessions": ["a", "b"]}) == ["a", "b"]
    # no id -> empty, NOT a date guess from started_ts/label
    assert awp.watch_sessions({"label": "Overnight 2026-07-22",
                               "started_ts": 1784703000.0}) == []
    assert awp.watch_sessions({"session_id": ""}) == []


# =============================================================================
# In-progress watch — explicit "in progress" end, never the prior baked end
# =============================================================================

def test_in_progress_watch_seeds_explicit_end_not_a_prior_entrys():
    seed = awp.nightwatch_initials(CLICKED)          # ended_fmt = ""
    assert seed["fun/nightwatch/ended"] == "In progress"
    # a completed watch still reads its real end
    done = dict(CLICKED, ended_fmt="Thu, Jul 23 @ 21:02")
    assert awp.nightwatch_initials(done)["fun/nightwatch/ended"] == \
        "Ended  Thu, Jul 23 @ 21:02"
