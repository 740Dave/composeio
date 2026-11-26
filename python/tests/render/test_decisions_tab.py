"""
Compliance tests for the Night Watch 3-tab detail pane (David 2026-07-22):
Task Status / Artifacts / DECISIONS ([SPEC:CXC-DECISIONS-TAB]).

Covers the two NEW leaves (DecisionsGraphWidget + NightWatchArtifactsWidget), the
scene wiring (3 tabs, bus tab-switch), and the MAGMA data source
(decisions_publisher.build_projection) — including the load-bearing acceptance:
the Decisions tab renders a real SUPERSEDES relationship from the actual corpus as
dim + strike + arrow-to-heir (live-supersession).
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import (DecisionsGraphWidget,
                                      NightWatchArtifactsWidget)
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.decisions_graph import (
    DEFAULT_NAMESPACE as DG_NS, DEFAULT_ANCHOR_NAMESPACE, _norm_lifecycle)
from composeio.render.widgets.nightwatch_artifacts import (
    DEFAULT_NAMESPACE as ART_NS, DEFAULT_OPEN_NAMESPACE)

from .conftest import surface_to_array

DG_RECT = Rect(0, 0, 512, 284)
ART_RECT = Rect(0, 0, 512, 284)

# the real DEC-95-HANDOFF-LINE neighborhood the decisions_publisher projects
NODES = [
    {"id": "DEC-95-HANDOFF-LINE", "title": "95% is the handoff line",
     "lifecycle": "committed", "is_anchor": True},
    {"id": "DEC-90-COMPACT-PREP", "title": "90% was the old threshold",
     "lifecycle": "superseded"},
    {"id": "DEC-CROSSING-IS-CHEAP", "title": "a crossing is cheap",
     "lifecycle": "committed"},
]
EDGES = [
    {"type": "SUPERSEDES", "source": "DEC-95-HANDOFF-LINE",
     "target": "DEC-90-COMPACT-PREP"},
    {"type": "DEPENDS_ON", "source": "DEC-CROSSING-IS-CHEAP",
     "target": "DEC-95-HANDOFF-LINE"},
]

ARTS = [
    {"title": "Build log", "path": "docs/x/BUILD.md"},
    {"title": "DRADIS gap", "path": "docs/y/gap.md"},
]


class _Ctx:
    theme = "dark"
    reduced_motion = False

    def __init__(self):
        self.redraws = 0
        self.published = []

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def teardown_function(_fn):
    ct.set_theme("dark")


def _mounted(cls, **kw):
    w = cls("w", **kw)
    ctx = _Ctx()
    w.on_mount(ctx)
    return w, ctx


def _draw(w, rect):
    c = SkiaCanvas(); c.create_surface(int(rect.w), int(rect.h))
    w.draw(c, rect)
    return c


# =============================================================================
# DecisionsGraphWidget — coercion + lifecycle + supersession
# =============================================================================

def test_lifecycle_comment_stripped():
    assert _norm_lifecycle("superseded   # applies to the OLD composer") \
        == "superseded"
    assert _norm_lifecycle(None) == ""


def test_static_nodes_edges_from_constructor():
    w, _ = _mounted(DecisionsGraphWidget, nodes=NODES, edges=EDGES)
    assert {n["id"] for n in w._nodes} == {
        "DEC-95-HANDOFF-LINE", "DEC-90-COMPACT-PREP", "DEC-CROSSING-IS-CHEAP"}
    assert len(w._edges) == 2


def test_superseded_flag_from_lifecycle_and_from_edge():
    # DEC-90 is superseded by lifecycle; also mark via edge-target logic.
    w, _ = _mounted(DecisionsGraphWidget, nodes=NODES, edges=EDGES)
    by_id = {n["id"]: n for n in w._nodes}
    assert by_id["DEC-90-COMPACT-PREP"]["superseded"] is True
    assert by_id["DEC-95-HANDOFF-LINE"]["superseded"] is False
    assert by_id["DEC-95-HANDOFF-LINE"]["is_anchor"] is True


def test_bus_payload_dict_and_json_string():
    w, ctx = _mounted(DecisionsGraphWidget)
    payload = {"anchor": "DEC-95-HANDOFF-LINE", "corpus": "intelligence-console",
               "nodes": NODES, "edges": EDGES}
    w.on_bus_state(DG_NS, payload)
    assert len(w._nodes) == 3 and len(w._edges) == 2 and ctx.redraws == 1
    assert w._anchor == "DEC-95-HANDOFF-LINE"
    w.on_bus_state(DG_NS, json.dumps(payload))       # JSON string form
    assert len(w._nodes) == 3
    # garbage leaves state untouched
    before = list(w._nodes)
    w.on_bus_state(DG_NS, "not-json")
    assert w._nodes == before


def test_edges_deduped():
    dup = EDGES + EDGES               # traverse emits each edge twice
    w, _ = _mounted(DecisionsGraphWidget, nodes=NODES, edges=dup)
    assert len(w._edges) == 2


def test_layered_levels_place_dependency_below_dependent():
    # DEC-CROSSING DEPENDS_ON DEC-95 -> DEC-95 sits a row BELOW DEC-CROSSING.
    w, _ = _mounted(DecisionsGraphWidget, nodes=NODES, edges=EDGES)
    lvl = w._levels()
    assert lvl["DEC-95-HANDOFF-LINE"] > lvl["DEC-CROSSING-IS-CHEAP"]
    # a SUPERSEDES pair shares a row (heir & superseded side by side)
    assert lvl["DEC-90-COMPACT-PREP"] == lvl["DEC-95-HANDOFF-LINE"]


# =============================================================================
# DecisionsGraphWidget — render + live-supersession (the acceptance)
# =============================================================================

def test_superseded_node_draws_strikethrough_and_tag():
    w, _ = _mounted(DecisionsGraphWidget, nodes=NODES, edges=EDGES,
                    anchor="DEC-95-HANDOFF-LINE")
    c = SkiaCanvas(); c.create_surface(int(DG_RECT.w), int(DG_RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, DG_RECT)
    joined = "".join(drawn)
    # every node id is rendered + the superseded tag is present (dim+strike+tag)
    assert "DEC-95-HANDOFF-LINE" in joined
    assert "DEC-90-COMPACT-PREP" in joined
    assert "SUPERSEDED" in joined
    # the edge legend makes the alphabet legible
    assert "supersedes" in joined and "depends on" in joined


def test_supersedes_arrow_is_drawn():
    """The SUPERSEDES edge draws a warn-coloured arrow (3 line segments: the
    shaft + two arrowhead strokes)."""
    lines = {"n": 0}

    class _Rec(SkiaCanvas):
        def draw_line(self, *a, **k):
            lines["n"] += 1
            return super().draw_line(*a, **k)

    w, _ = _mounted(DecisionsGraphWidget,
                    nodes=NODES[:2], edges=[EDGES[0]])   # just the supersedes
    c = _Rec(); c.create_surface(int(DG_RECT.w), int(DG_RECT.h))
    w.draw(c, DG_RECT)
    # >= shaft + 2 arrowhead segments + the superseded strikethrough
    assert lines["n"] >= 4


def test_draw_paints_content():
    w, _ = _mounted(DecisionsGraphWidget, nodes=NODES, edges=EDGES)
    c = _draw(w, DG_RECT)
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_empty_state_message():
    w, _ = _mounted(DecisionsGraphWidget, nodes=[], edges=[])
    c = SkiaCanvas(); c.create_surface(int(DG_RECT.w), int(DG_RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, DG_RECT)
    assert "self/decisions" in "".join(drawn)


def test_node_tap_publishes_reanchor_intent():
    w, ctx = _mounted(DecisionsGraphWidget, nodes=NODES, edges=EDGES,
                      corpus="intelligence-console")
    _draw(w, DG_RECT)                       # draw records node hit zones
    assert w._node_zones
    r, nid = w._node_zones[0]
    w.on_tap(r.x + r.w / 2.0, r.y + r.h / 2.0)
    assert len(ctx.published) == 1
    ns, val = ctx.published[0]
    assert ns == DEFAULT_ANCHOR_NAMESPACE
    assert val["anchor"] == nid and val["corpus"] == "intelligence-console"


def test_measure_fills_given_box():
    w, _ = _mounted(DecisionsGraphWidget, nodes=NODES, edges=EDGES)
    s = w.measure(Constraints(512, 284))
    assert (s.width, s.height) == (512, 284)


# =============================================================================
# NightWatchArtifactsWidget
# =============================================================================

def test_artifacts_coerce_list_and_dict():
    w, ctx = _mounted(NightWatchArtifactsWidget, artifacts=ARTS)
    assert len(w._arts) == 2
    w.on_bus_state(ART_NS, {"artifacts": ARTS[:1]})
    assert len(w._arts) == 1 and ctx.redraws == 1
    # entries without a path are dropped
    w.on_bus_state(ART_NS, [{"title": "no path"}, {"path": "docs/z.md"}])
    assert [a["path"] for a in w._arts] == ["docs/z.md"]


def test_artifact_tap_publishes_doc_open():
    w, ctx = _mounted(NightWatchArtifactsWidget, artifacts=ARTS)
    _draw(w, ART_RECT)
    r, path = w._row_zones[0]
    w.on_tap(r.x + 20, r.y + r.h / 2.0)
    assert ctx.published == [(DEFAULT_OPEN_NAMESPACE, "docs/x/BUILD.md")]


def test_artifacts_render_titles_and_paths():
    w, _ = _mounted(NightWatchArtifactsWidget, artifacts=ARTS)
    c = SkiaCanvas(); c.create_surface(int(ART_RECT.w), int(ART_RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, ART_RECT)
    joined = "".join(drawn)
    assert "Build log" in joined and "docs/x/BUILD.md" in joined


def test_artifacts_empty_state():
    w, _ = _mounted(NightWatchArtifactsWidget, artifacts=[])
    c = SkiaCanvas(); c.create_surface(int(ART_RECT.w), int(ART_RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, ART_RECT)
    assert "no artifacts" in "".join(drawn)


# =============================================================================
# parser wiring
# =============================================================================

def test_parser_wires_decisions_graph():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "decisions_graph", "id": "dg", "namespace": "self/decisions",
         "anchor": "DEC-95-HANDOFF-LINE", "nodes": NODES, "edges": EDGES}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "self/decisions" in subbed
    w = next(x for ns, x in comp.subscriptions if ns == "self/decisions")
    assert isinstance(w, DecisionsGraphWidget)
    assert len(w._nodes) == 3


def test_parser_wires_artifacts():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "nightwatch_artifacts", "id": "a",
         "namespace": "fun/nightwatch/artifacts", "artifacts": ARTS}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "fun/nightwatch/artifacts" in subbed
    w = next(x for ns, x in comp.subscriptions
             if ns == "fun/nightwatch/artifacts")
    assert isinstance(w, NightWatchArtifactsWidget) and len(w._arts) == 2


# =============================================================================
# the deployed scene — 3 tabs, bus tab-switch, each tab renders
# =============================================================================

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "nightwatch.compose.yaml")


def _scene_app():
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    return app


def test_scene_has_two_tabs_and_the_view_decisions_button():
    """[SPEC:CXC-GRAPH-VIEWER] the embedded Decisions tab is REMOVED (David
    2026-08-06) — 3 tabs became 2 — and the decisions METRIC stays, now paired
    with the "View Decisions" intent button."""
    app = _scene_app()
    try:
        types = {w.widget_type for w in app.comp.root.iter_widgets()}
        assert "console_tabs" in types
        assert "nightwatch_checklist" in types     # Task Status tab (reused)
        assert "nightwatch_artifacts" in types     # Artifacts tab
        assert "decisions_graph" not in types      # the removed Decisions tab
        tabw = next(w for w in app.comp.root.iter_widgets()
                    if w.widget_type == "console_tabs")
        assert [str(l).lower() for l in tabw.labels] == \
            ["task status", "artifacts"]
        # the metric card SURVIVES (a count is a different surface from a graph)
        ids = {w.widget_id for w in app.comp.root.iter_widgets()}
        assert {"m_decisions", "n_decisions", "l_decisions"} <= ids
        # ...paired with the button that opens the standalone Graph Viewer
        btn = next(w for w in app.comp.root.iter_widgets()
                   if w.widget_id == "b_view_decisions")
        assert btn.widget_type == "emit_button"
        assert btn.interactive and btn.hover_cursor() == "pointer"
    finally:
        app.stop()


def test_scene_tab_switch_over_the_bus():
    app = _scene_app()
    try:
        tabw = next(w for w in app.comp.root.iter_widgets()
                    if w.widget_type == "console_tabs")
        app.render_frame_at(0.0)
        assert tabw.active_index == 0                     # Task Status default
        # bus updates drain on the render tick (the thread-safe update queue),
        # so render after publishing to observe the switch.
        app.publish("fun/nightwatch/tab", "artifacts")
        app.render_frame_at(0.0)
        assert tabw.active_index == 1
        app.publish("fun/nightwatch/tab", "task status")
        app.render_frame_at(0.0)
        assert tabw.active_index == 0
    finally:
        app.stop()


def test_scene_view_decisions_button_publishes_the_scoped_open_intent(tmp_path):
    """[SPEC:CXC-GRAPH-VIEWER] [CXC-GV-INTENTS] the load-bearing acceptance for
    the replacement surface: tapping "View Decisions" publishes a PURE
    `_app/graph/open` intent (no spawn, no new mechanism) carrying THIS run's
    scope, with the session ids resolved LIVE from the seeded namespace."""
    app = _scene_app()
    try:
        app.publish("fun/nightwatch/sessions", ["sid-run-1", "sid-run-2"])
        app.publish("fun/nightwatch/title", "Away Watch: Tue, Jul 21 @ 23:50")
        app.render_frame_at(0.0)          # drain the queue into the widget
        btn = next(w for w in app.comp.root.iter_widgets()
                   if w.widget_id == "b_view_decisions")
        sent = []
        real = app.publish
        app.publish = lambda ns, v, *a, **k: (sent.append((ns, v)),
                                              real(ns, v, *a, **k))[1]
        btn.on_tap(0.0, 0.0)
        assert len(sent) == 1, "a pure single intent publish"
        ns, payload = sent[0]
        assert ns == "_app/graph/open"
        assert payload["open"] is True
        assert payload["corpus"] == "intelligence-console"
        # the LIVE seeded sessions, not the authored default
        assert payload["sessions"] == ["sid-run-1", "sid-run-2"]
        assert payload["scope_label"] == "Away Watch: Tue, Jul 21 @ 23:50"
        out = os.path.join(str(tmp_path), "nightwatch_pane.png")
        app.canvas.save_png(out)
        assert os.path.exists(out) and os.path.getsize(out) > 4000
    finally:
        app.stop()


# =============================================================================
# decisions_publisher — the MAGMA projection (reads the real corpus, read-only)
# =============================================================================

FACTLATTICE = os.environ.get("FACTLATTICE_ROOT",
                             os.path.expanduser("~/factlattice"))


@pytest.mark.skipif(not os.path.isdir(FACTLATTICE),
                    reason="factlattice engine repo not present")
def test_publisher_projects_real_supersedes_from_corpus():
    sys.path.insert(0, os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools"))
    import importlib
    dp = importlib.import_module("decisions_publisher")
    pl = dp.build_projection("DEC-95-HANDOFF-LINE", "intelligence-console", 2,
                             FACTLATTICE)
    ids = {n["id"] for n in pl["nodes"]}
    assert "DEC-95-HANDOFF-LINE" in ids and "DEC-90-COMPACT-PREP" in ids
    sup = [e for e in pl["edges"] if e["type"] == "SUPERSEDES"]
    assert any(e["source"] == "DEC-95-HANDOFF-LINE"
               and e["target"] == "DEC-90-COMPACT-PREP" for e in sup)
    old = next(n for n in pl["nodes"] if n["id"] == "DEC-90-COMPACT-PREP")
    assert old["superseded"] is True
    # a bounded NEIGHBORHOOD, never the whole-graph hairball
    assert len(pl["nodes"]) <= 12
    assert not any(e["type"] == "CONTAINS" for e in pl["edges"])
    assert json.dumps(pl)                          # JSON-serializable on the wire
