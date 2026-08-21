"""[SPEC:CXC-GRAPH-VIEWER] Graph Viewer compliance — the machine tier of the
four acceptance nodes ([CXC-GV-VERIFY-DATA-PATH] / -SELECTOR / -STYLE /
-RETIREMENT).

All engine-touching tests use an INJECTED store / engine / projector (no real
lts-engine), so the DATA PATH is proven with the calls it makes, not a live
deployment ([CXC-GV-VERIFY-DATA-PATH] verify.machine).
"""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                         os.pardir, os.pardir, os.pardir))
sys.path.insert(0, os.path.join(REPO_ROOT, "python"))
sys.path.insert(0, os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools"))

from composeio.render.skia_backend import SkiaCanvas          # noqa: E402
from composeio.render.widget import Rect                      # noqa: E402
from composeio.render.widgets import console_theme as ct      # noqa: E402
from composeio.render.widgets.decisions_tab import (          # noqa: E402
    GraphViewerWidget, GV_ANCHOR_NAMESPACE, GV_REFRESH_NAMESPACE,
    GV_OPEN_NAMESPACE)
from composeio.decision_ops import graph_projection as gp     # noqa: E402
from composeio.decision_ops.graph_projection import (         # noqa: E402
    GraphProjector, build_full_projection, build_projection,
    resolve_badge_role, load_style_profiles)

SCENE = os.path.join(REPO_ROOT, "python", "composeio", "render",
                     "examples", "graph_viewer.compose.yaml")
STYLES = os.path.join(REPO_ROOT, "python", "composeio", "render",
                      "examples", "graph_viewer.styles.yaml")
GRECT = Rect(0, 0, 1200, 800)


# --------------------------------------------------------------------------- #
# fakes — an injected store / engine / partition (no real lts-engine)
# --------------------------------------------------------------------------- #
def _node(nid, lifecycle="", title="t", props=None, **extra):
    p = dict(props or {})
    if lifecycle:
        p.setdefault("lifecycle-position", lifecycle)
    d = {"id": nid, "title": title, "properties": p,
         "source_file": "x.lts", "line_range": [1, 2]}
    d.update(extra)
    return d


class FakePartition:
    def __init__(self, nodes, edges, corpus_id="c"):
        self.nodes = {n["id"]: n for n in nodes}
        self._edges = edges
        self.corpus_id = corpus_id

    def iter_edges(self):
        return list(self._edges)


class FakeStore:
    def __init__(self, parts, corpus_ids_raises=False):
        self.parts = parts
        self.get_partition_calls = []
        self._raise = corpus_ids_raises

    def corpus_ids(self):
        if self._raise:
            raise RuntimeError("enumeration blew up")
        return list(self.parts.keys())

    def get_partition(self, cid):
        self.get_partition_calls.append(cid)
        return self.parts.get(cid)


class FakeEngine:
    def __init__(self, result):
        self.result = result
        self.traverse_calls = []

    def traverse(self, **kw):
        self.traverse_calls.append(kw)
        return self.result


class _Ctx:
    theme = "dark"
    reduced_motion = True

    def __init__(self):
        self.redraws = 0
        self.published = []

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# [CXC-GV-VERIFY-DATA-PATH] direct query — calls + modes + fail-closed + title
# =============================================================================
def test_full_graph_mode_calls_get_partition_and_shapes_topology_only():
    # (a) full-graph reads the partition DIRECTLY and shapes via the reused shaper
    part = FakePartition(
        [_node("DEC-1", "committed"), _node("DEC-2", "superseded"),
         _node("CXC-9", "")],
        [{"type": "SUPERSEDES", "source": "DEC-1", "target": "DEC-2"},
         {"type": "DEPENDS_ON", "source": "DEC-1", "target": "CXC-9"},
         {"type": "CONTAINS", "source": "HUB", "target": "DEC-1"}],
        corpus_id="intelligence-console")
    store = FakeStore({"intelligence-console": part})
    proj_gp = GraphProjector(store=store,
                             style_profiles={"intelligence-console":
                                             {"node_kinds": ["DEC-"]}})
    proj = proj_gp.full_projection("intelligence-console")
    assert store.get_partition_calls == ["intelligence-console"]
    ids = {n["id"] for n in proj["nodes"]}
    # (e) admits ONLY the profile's node kinds; the CONTAINS hub + cross-kind
    # DEPENDS_ON (to the unadmitted CXC-9) are excluded.
    assert ids == {"DEC-1", "DEC-2"}
    kinds = {e["type"] for e in proj["edges"]}
    assert kinds == {"SUPERSEDES"}
    # topology-only: NO geometry keys ([CXC-DPE-TOPOLOGY-ONLY])
    dp = importlib.import_module("decisions_publisher")
    assert dp._geometry_key_violations(proj) == []


def test_neighborhood_mode_calls_traverse():
    # (a) re-anchor mode calls engine.traverse and shapes via build_projection
    res = {"success": True,
           "nodes": [_node("DEC-1", "committed"), _node("DEC-2", "superseded")],
           "edges": [{"type": "SUPERSEDES", "source": "DEC-1",
                      "target": "DEC-2"}]}
    engine = FakeEngine(res)
    proj_gp = GraphProjector(store=FakeStore({}), engine=engine,
                             style_profiles={"c": {"default_depth": 3}})
    proj = proj_gp.neighborhood_projection("c", "DEC-1")
    assert len(engine.traverse_calls) == 1
    call = engine.traverse_calls[0]
    assert call["corpus"] == "c" and call["start_node"] == "DEC-1"
    assert call["max_depth"] == 3
    assert proj["mode"] == "neighborhood"
    assert {n["id"] for n in proj["nodes"]} == {"DEC-1", "DEC-2"}


def test_store_built_once_and_reused(monkeypatch):
    # (b) the store is built ONCE and reused across selector-change + re-anchor
    import factlattice_engine.config as cfg
    import factlattice_engine.tools as tls
    builds = {"n": 0}
    part = FakePartition([_node("DEC-1", "committed")], [], corpus_id="a")
    store = FakeStore({"a": part, "b": part})

    def fake_build_store(*a, **k):
        builds["n"] += 1
        return store

    monkeypatch.setattr(cfg, "build_store", fake_build_store)
    monkeypatch.setattr(tls, "LtsEngine",
                        lambda s, *a, **k: FakeEngine({"success": True,
                                                       "nodes": [], "edges": []}))
    proj_gp = GraphProjector(style_profiles={})
    proj_gp.full_projection("a")            # first build
    proj_gp.full_projection("b")            # selector change — reuse
    proj_gp.neighborhood_projection("a", "DEC-1")  # re-anchor — reuse
    assert builds["n"] == 1, "store must be built exactly once per session"


def test_unreachable_engine_renders_explicit_state():
    # (c) an unreachable engine (and no repo-local fallback for a served-only
    # corpus) yields the "lts-engine unavailable" state — not a crash, not blank
    proj_gp = GraphProjector(style_profiles={}, repo_root="/nonexistent/nope")
    proj_gp._store = None
    proj_gp._store_built = True
    proj_gp.engine_available = False
    proj = proj_gp.full_projection("served-only-corpus")
    assert proj["error"] == "lts-engine unavailable"
    assert proj["nodes"] == []


def test_stale_or_refuse_verdict_is_a_loud_state_not_a_partial_graph():
    # (d) a traverse success=False yields an explicit error state, not a graph
    engine = FakeEngine({"success": False, "error": "stale index"})
    proj_gp = GraphProjector(store=FakeStore({}), engine=engine)
    proj = proj_gp.neighborhood_projection("c", "DEC-1")
    assert proj["nodes"] == [] and proj["error"] == "stale index"


def test_window_title_is_graph_viewer():
    # (f) the standalone window TITLE resolves to exactly "Graph Viewer"
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    assert comp.title == "Graph Viewer"
    assert comp.title != "Decision Viewer"


def test_widget_does_not_subscribe_self_decisions():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    subs = [ns for ns, _ in comp.subscriptions]
    assert "self/decisions" not in subs
    # only the lightweight CONTROL intents stay ([CXC-GV-INTENTS])
    assert GV_ANCHOR_NAMESPACE in subs and GV_REFRESH_NAMESPACE in subs


def test_widget_ignores_self_decisions_data():
    w = GraphViewerWidget("w", direct_query=False)
    w.on_mount(_Ctx())
    w.on_bus_state("self/decisions", {"nodes": [{"id": "X"}], "edges": []})
    assert w._nodes == []            # data arrives from the QUERY, not the feed


# =============================================================================
# [CXC-GV-VERIFY-SELECTOR] dynamic union + dedupe + fallback
# =============================================================================
def test_selector_is_deduped_union_with_defect_graph_present():
    # served set OMITS defect-graph; the repo-local union restores it
    store = FakeStore({"intelligence-console": None, "insights-and-warts": None})
    proj_gp = GraphProjector(store=store, repo_root=REPO_ROOT)
    corpora = proj_gp.list_corpora()
    assert "defect-graph" in corpora
    assert "intelligence-console" in corpora
    assert len(corpora) == len(set(corpora))          # deduped
    # served ids come first, repo-local appended
    assert corpora[0] == "intelligence-console"


def test_enumeration_failure_falls_back_to_repo_local():
    store = FakeStore({}, corpus_ids_raises=True)
    proj_gp = GraphProjector(store=store, repo_root=REPO_ROOT)
    corpora = proj_gp.list_corpora()
    assert corpora, "never an empty dropdown"
    assert "defect-graph" in corpora
    assert proj_gp.last_error                          # "engine unavailable" note


def test_unprofiled_corpus_selects_and_renders_with_default_profile():
    part = FakePartition([_node("FOO-1", "committed")], [], corpus_id="mystery")
    store = FakeStore({"mystery": part})
    profs, dflt = load_style_profiles(STYLES)
    proj_gp = GraphProjector(store=store, style_profiles=profs,
                             default_profile=dflt)
    assert proj_gp.profile_for("mystery") == dflt      # neutral default
    proj = proj_gp.full_projection("mystery")
    assert {n["id"] for n in proj["nodes"]} == {"FOO-1"}   # renders (not blank)


# =============================================================================
# [CXC-GV-VERIFY-STYLE] per-corpus profile drives badges (data, not code)
# =============================================================================
def test_style_profiles_are_a_data_file_keyed_per_corpus():
    profs, dflt = load_style_profiles(STYLES)
    for cid in ("intelligence-console", "defect-graph", "insights-and-warts"):
        assert cid in profs
        assert "badge_field" in profs[cid] and "badge_map" in profs[cid]
    assert dflt.get("node_kinds") == []                # neutral default


def test_badge_role_comes_from_the_corpus_own_field():
    profs, _ = load_style_profiles(STYLES)
    dec = profs["intelligence-console"]
    ins = profs["insights-and-warts"]
    dfx = profs["defect-graph"]
    assert dec["badge_field"] == "lifecycle-position"
    assert resolve_badge_role("superseded", dec["badge_map"]) == "dim"
    assert ins["badge_field"] == "kind"
    assert resolve_badge_role("wart", ins["badge_map"]) == "warn"
    assert resolve_badge_role("elegance", ins["badge_map"]) == "ok"
    # a freeform status keys on its first token (defects "live (empty) — <date>")
    assert resolve_badge_role("live (empty) — 2026-07-26", dfx["badge_map"]) == "ok"
    # an unmapped value is neutral (no color)
    assert resolve_badge_role("whatever", dec["badge_map"]) == ""


def test_widget_tab_ink_reads_the_profile_no_code_branch():
    from composeio.render.widgets.decisions_tab import DNode
    w = GraphViewerWidget("w", direct_query=False)
    w.on_mount(_Ctx())
    w._profile = {"badge_field": "kind",
                  "badge_map": {"wart": "warn", "elegance": "ok"}}
    t = ct.CONSOLE.active
    assert w._tab_ink(DNode(id="I-1", badge="wart"), t) == t.warn
    assert w._tab_ink(DNode(id="I-2", badge="elegance"), t) == t.ok
    assert w._tab_ink(DNode(id="I-3", badge="none"), t) is None


def test_decision_profile_reproduces_five_edge_inks_and_supersession():
    # the decision profile is the REFERENCE instance ([CXC-GV-STYLE-PROFILE])
    part = FakePartition(
        [_node("DEC-1", "committed"), _node("DEC-2", "superseded"),
         _node("DEC-3", "committed"), _node("DEC-4", "committed"),
         _node("DEC-5", "committed"), _node("DEC-6", "committed")],
        [{"type": "SUPERSEDES", "source": "DEC-1", "target": "DEC-2"},
         {"type": "DEPENDS_ON", "source": "DEC-1", "target": "DEC-3"},
         {"type": "RELATED_TO", "source": "DEC-3", "target": "DEC-4"},
         {"type": "RESOLVES", "source": "DEC-4", "target": "DEC-5"},
         {"type": "FOLLOWS", "source": "DEC-5", "target": "DEC-6"}],
        corpus_id="intelligence-console")
    profs, _ = load_style_profiles(STYLES)
    proj = build_full_projection(part, profs["intelligence-console"],
                                 corpus="intelligence-console")
    kinds = {e["type"] for e in proj["edges"]}
    # the five canonical inks, incl. RELATED_TO->RELATES_TO + RESOLVES->RE_DERIVE_VIA
    assert kinds == {"SUPERSEDES", "DEPENDS_ON", "RELATES_TO",
                     "RE_DERIVE_VIA", "FOLLOWS"}
    n2 = next(n for n in proj["nodes"] if n["id"] == "DEC-2")
    assert n2["superseded"] is True                    # dim/strike carrier


# =============================================================================
# [CXC-GV-VERIFY-RETIREMENT] emitter retired, shaper survives
# =============================================================================
def test_emitter_transport_is_gone():
    dp = importlib.import_module("decisions_publisher")
    # the daemon/transport half is DELETED ([CXC-GV-EMITTER-RETIRED])
    assert not hasattr(dp, "run"), "the MQTT publish loop must be gone"
    src = open(dp.__file__, "r", encoding="utf-8").read()
    assert "loop_start" not in src and "client.publish" not in src
    assert "acquire_singleton" not in src


def test_extracted_shaper_still_exposed():
    # the PURE shaper survives in the shared module ([CXC-GV-VERIFY-RETIREMENT] c)
    assert callable(build_full_projection) and callable(build_projection)
    dp = importlib.import_module("decisions_publisher")
    assert callable(dp.build_projection)               # back-compat shim


def test_decisions_publisher_not_a_console_data_source():
    sys.path.insert(0, os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console"))
    import data_sources
    names = [s.name for s in data_sources.build_console_sources(
        session_jsonl=None, intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22")]
    assert "decisions_publisher" not in names


def test_no_process_publishes_self_decisions_from_the_shim():
    # the retirement is COMPLETE: no MQTT transport publishes the projection
    dp = importlib.import_module("decisions_publisher")
    src = open(dp.__file__, "r", encoding="utf-8").read()
    assert "import paho" not in src
    assert "def run(" not in src
    # no publish envelope construction for the retired feed
    assert 'composeio/global/' not in src


# =============================================================================
# render smoke — the widget draws its queried graph (glass proof is manual)
# =============================================================================
class _FakeProjector:
    def __init__(self):
        self.engine_available = True
        self.last_error = ""

    def list_corpora(self):
        return ["intelligence-console", "defect-graph", "insights-and-warts"]

    def profile_for(self, c):
        return {}

    def full_projection(self, corpus):
        return {"corpus": corpus, "anchor": "", "mode": "full",
                "nodes": [{"id": "DEC-1", "title": "a decision",
                           "lifecycle": "committed", "badge": "committed"},
                          {"id": "DEC-2", "title": "an heir",
                           "lifecycle": "superseded", "superseded": True,
                           "badge": "superseded"}],
                "edges": [{"type": "SUPERSEDES", "source": "DEC-1",
                           "target": "DEC-2"}]}

    def neighborhood_projection(self, corpus, anchor, depth=None):
        return self.full_projection(corpus)

    def refresh(self):
        pass


def test_render_smoke_queried_graph_draws():
    w = GraphViewerWidget("w", projector=_FakeProjector(), direct_query=True)
    w.on_mount(_Ctx())
    assert {n.id for n in w._nodes} == {"DEC-1", "DEC-2"}
    c = SkiaCanvas(); c.create_surface(int(GRECT.w), int(GRECT.h))
    w.draw(c, GRECT)                                    # no exception
    assert w._node_zones, "node hit zones recorded from the draw pass"
    assert "GRAPH" in w._graph_header_label()


def test_corpus_selector_toggles_and_selects():
    fp = _FakeProjector()
    w = GraphViewerWidget("w", projector=fp, direct_query=True)
    w.on_mount(_Ctx())
    assert w._corpora == ["intelligence-console", "defect-graph",
                          "insights-and-warts"]
    c = SkiaCanvas(); c.create_surface(int(GRECT.w), int(GRECT.h))
    w.draw(c, GRECT)
    assert w._corpus_pill_zone is not None
    # tap the pill -> dropdown opens
    pz = w._corpus_pill_zone
    w.on_tap(pz.x + pz.w / 2, pz.y + pz.h / 2)
    assert w._corpus_dropdown_open is True
    w.draw(c, GRECT)                                    # records option zones
    opt = next(r for r, cval in w._corpus_option_zones if cval == "defect-graph")
    w.on_tap(opt.x + opt.w / 2, opt.y + opt.h / 2)
    assert w._corpus == "defect-graph" and w._corpus_dropdown_open is False


# =============================================================================
# [CXC-GV-SCOPE] run scope — the intent payload -> session PRESELECT path
# (the Night Watch "View Decisions" button), incl. HONEST degradation.
# =============================================================================
class _SessionProjector(_FakeProjector):
    """A projector whose graph carries real ``decided-in-session`` values."""

    def full_projection(self, corpus):
        return {"corpus": corpus, "anchor": "", "mode": "full",
                "nodes": [{"id": "DEC-1", "title": "a", "session": "sid-A"},
                          {"id": "DEC-2", "title": "b", "session": "sid-B"},
                          {"id": "DEC-3", "title": "c", "session": "sid-C"}],
                "edges": []}


def _scoped(scope, projector=None):
    w = GraphViewerWidget("w", projector=projector or _SessionProjector(),
                          direct_query=True)
    w.on_mount(_Ctx())
    w.on_bus_state(GV_OPEN_NAMESPACE, scope)
    return w


def test_open_intent_scope_preselects_the_runs_sessions():
    w = _scoped({"open": True, "sessions": ["sid-B"],
                 "scope_label": "Away Watch: Tue"})
    # the isolation is a PRESELECT over the EXISTING session filter
    assert w._sessions_sel == {"sid-B"}
    assert w._scope_note == ""                       # fully honoured
    assert "SCOPED" in w._scope_banner_text()
    assert "Away Watch: Tue" in w._scope_banner_text()
    # ...and it really filters the results
    assert {n.id for n in w._results()} == {"DEC-2"}


def test_scope_preselects_ALL_sessions_when_a_run_spans_several():
    w = _scoped({"open": True, "sessions": ["sid-A", "sid-C"]})
    assert w._sessions_sel == {"sid-A", "sid-C"}
    assert {n.id for n in w._results()} == {"DEC-1", "DEC-3"}


def test_unmatchable_scope_degrades_LOUDLY_unscoped_not_silently():
    """A scope that matches nothing must open UNSCOPED *and say so* — never a
    silent whole-corpus view the human reads as this run's."""
    w = _scoped({"open": True, "sessions": ["sid-NOPE"]})
    assert w._sessions_sel == set()                  # unscoped, not empty-result
    assert {n.id for n in w._results()} == {"DEC-1", "DEC-2", "DEC-3"}
    assert "could not scope" in w._scope_note
    assert "could not scope" in w._scope_banner_text()


def test_partially_matched_scope_says_which_part_was_lost():
    w = _scoped({"open": True, "sessions": ["sid-A", "sid-GONE"]})
    assert w._sessions_sel == {"sid-A"}
    assert "1 of 2" in w._scope_note


def test_bare_open_from_the_rail_stays_UNSCOPED():
    """The console rail publishes a bare open; it must not scope anything."""
    for bare in ("open", True, {"open": True}, {"val": "open"}):
        w = _scoped(bare)
        assert w._sessions_sel == set()
        assert w._scope_note == "" and w._scope_banner_text() == ""


def test_scope_seeded_BEFORE_the_graph_loads_still_resolves():
    """ORDERING GUARD: the launcher seeds the scope as scene-initial state, which
    the runtime publishes BEFORE the tree mounts.  Matching at arrival would see
    an empty graph and falsely claim 'could not scope', so resolution defers
    until the projection lands."""
    w = GraphViewerWidget("w", projector=_SessionProjector(), direct_query=True)
    w.on_bus_state(GV_OPEN_NAMESPACE, {"open": True, "sessions": ["sid-B"]})
    assert w._scope_note == "", "must not claim failure before data exists"
    assert w._sessions_sel == set()
    w.on_mount(_Ctx())                               # the query lands here
    assert w._sessions_sel == {"sid-B"}              # resolved after load
    assert w._scope_note == ""


def test_scope_is_VISIBLE_and_widenable_never_a_hidden_filter():
    """A scoped session outside the 6 visible chips is still drawn as a chip, so
    the human can SEE what is filtering and click it off."""
    class _Many(_FakeProjector):
        def full_projection(self, corpus):
            return {"corpus": corpus, "anchor": "", "mode": "full", "edges": [],
                    "nodes": [{"id": f"DEC-{i}", "title": "t",
                               "session": f"s{i:02d}"} for i in range(10)]}
    w = _scoped({"open": True, "sessions": ["s00"]}, projector=_Many())
    assert w._sessions_sel == {"s00"}
    drawn = []
    c = SkiaCanvas(); c.create_surface(int(GRECT.w), int(GRECT.h))
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(str(s)), real(s, *a, **k))[1]
    w.draw(c, GRECT)
    # s00 sorts LAST of 10 (reverse order) — outside the first 6 visible chips —
    # yet its chip IS drawn, because it is selected.
    labels = "".join(drawn)
    assert "s00" in labels, "a selected session must always be a visible chip"
    # the unselected out-of-window sessions stay hidden (the 6-chip window holds)
    assert "s01" not in labels and "s02" not in labels
    assert "SCOPED" in labels                        # and announced on the glass


def test_manual_corpus_change_drops_a_stale_scope():
    w = _scoped({"open": True, "sessions": ["sid-B"]})
    assert w._sessions_sel == {"sid-B"}
    w._select_corpus("defect-graph")                 # the dropdown path
    assert w._sessions_sel == set() and w._scope_sessions == []
    assert w._scope_banner_text() == ""


# =============================================================================
# [CXC-GV-SCOPE] the launcher leg — intent scope parsed + handed to the spawn
# =============================================================================
def test_launcher_parses_scope_and_leaves_a_bare_open_unscoped():
    mod = importlib.import_module("decisions_viewer_launcher")
    assert mod.parse_scope({"open": True, "sessions": ["a", "b"],
                            "corpus": "c"}) == {"sessions": ["a", "b"],
                                                "corpus": "c"}
    # bus envelope + a single scalar session
    assert mod.parse_scope({"val": {"session": "solo"}})["sessions"] == ["solo"]
    # a BARE open (the rail) carries no scope
    assert mod.parse_scope("open") is None
    assert mod.parse_scope({"open": True}) is None
    # ...and a scoped intent is still a truthy OPEN
    assert mod.parse_intent({"open": True, "sessions": ["a"]}) is True


def test_launcher_hands_the_scope_to_the_spawned_viewer():
    """The scope reaches the new window through the EXISTING race-free
    COMPOSEIO_SCENE_INITIAL seam — not a new mechanism."""
    mod = importlib.import_module("decisions_viewer_launcher")
    captured = {}

    class _P:
        pid = 4321

    def fake_popen(cmd, **kw):
        captured.update(kw.get("env") or {})
        return _P()

    lau = mod.DecisionsViewerLauncher()
    orig = mod.subprocess.Popen
    mod.subprocess.Popen = fake_popen
    try:
        lau.open({"sessions": ["sid-B"], "corpus": "intelligence-console",
                  "scope_label": "Away Watch: Tue"})
    finally:
        mod.subprocess.Popen = orig
    seeded = json.loads(captured[mod.SCENE_INITIAL_ENV])
    assert seeded[mod.GRAPH_OPEN_NS]["sessions"] == ["sid-B"]
    assert seeded[mod.GRAPH_OPEN_NS]["corpus"] == "intelligence-console"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
