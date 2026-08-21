"""
Compliance tests for the console launcher rail ([SPEC:CXC-DVL] Deliverable B):
the lts-engine-status source ([CXC-DVL-STATUS-SOURCE]), the rail widget
([CXC-DVL-RAIL]), the in-process decisions launcher ([CXC-DVL-LAUNCHER]), and
their registration ([CXC-DVL-REGISTER]).  Verify gates
[CXC-DVL-VERIFY-RAIL] / [CXC-DVL-VERIFY-LAUNCH] (machine tier).
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import LtsEngineRailWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.lts_engine_rail import (
    DEFAULT_NAMESPACE, DEFAULT_OPEN_NAMESPACE)

RAIL_RECT = Rect(0, 0, 340, 200)


def _load_tool(name):
    path = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


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


def _mounted():
    w = LtsEngineRailWidget("rail")
    ctx = _Ctx()
    w.on_mount(ctx)
    return w, ctx


def _draw_text(w):
    c = SkiaCanvas()
    c.create_surface(int(RAIL_RECT.w), int(RAIL_RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RAIL_RECT)
    return "".join(drawn)


# =============================================================================
# lts_engine_status source — payload shape + fail-closed ([CXC-DVL-STATUS-SOURCE])
# =============================================================================

STATUS_KEYS = {"ts", "reachable", "fresh", "corpus_count", "total_nodes",
               "total_edges", "last_refresh", "corpora"}


def test_status_source_selftest_passes():
    mod = _load_tool("lts_engine_status")
    assert mod._selftest() == 0


def test_status_up_payload_shape_and_aggregation():
    import json
    mod = _load_tool("lts_engine_status")
    up = mod.build_status([
        {"corpus": "a", "fresh": True, "nodes": 312, "edges": 540,
         "last_refresh": "2026-07-24T10:00:00"},
        {"corpus": "b", "fresh": True, "nodes": 700, "edges": 1400,
         "last_refresh": "2026-07-25T09:00:00"},
    ])
    assert set(up) == STATUS_KEYS
    assert up["reachable"] is True and up["fresh"] is True
    assert up["corpus_count"] == 2
    assert up["total_nodes"] == 312 + 700         # summed over corpora
    assert up["total_edges"] == 540 + 1400
    assert up["last_refresh"] == "2026-07-25T09:00:00"
    assert json.loads(json.dumps(up)) == up       # JSON-serializable


def test_status_fresh_is_and_over_corpora():
    mod = _load_tool("lts_engine_status")
    mixed = mod.build_status([{"corpus": "a", "fresh": True, "nodes": 1,
                               "edges": 1},
                              {"corpus": "b", "fresh": False, "nodes": 1,
                               "edges": 1}])
    assert mixed["reachable"] is True and mixed["fresh"] is False


def test_status_down_payload_is_fail_closed():
    mod = _load_tool("lts_engine_status")
    # a crashing probe never raises -> the reachable:false payload
    down = mod.sample(lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert set(down) == STATUS_KEYS
    assert down["reachable"] is False and down["fresh"] is False
    assert down["corpus_count"] == 0 and down["total_nodes"] == 0
    assert down["total_edges"] == 0 and down["corpora"] == []
    assert isinstance(down["ts"], str)


# =============================================================================
# rail widget — subscribe + three states + Open publish ([CXC-DVL-RAIL])
# =============================================================================

def test_rail_defaults_and_measure():
    w, _ = _mounted()
    assert w.namespace == DEFAULT_NAMESPACE == "self/lts_engine/status"
    # [SPEC:CXC-GRAPH-VIEWER] [DD-GV-INTENT-NAMESPACE] the Open intent generalized
    # to `_app/graph/open` (rail publish + launcher INTENT_NS moved together).
    assert w.open_namespace == DEFAULT_OPEN_NAMESPACE == "_app/graph/open"
    s = w.measure(Constraints(340, 600))
    assert s.width <= 360 and s.height > 0


def test_rail_parser_subscribes_status_namespace():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "lts_engine_rail", "id": "rail"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "self/lts_engine/status" in subbed
    w = next(x for ns, x in comp.subscriptions
             if ns == "self/lts_engine/status")
    assert isinstance(w, LtsEngineRailWidget)


def test_rail_awaiting_before_first_status():
    w, _ = _mounted()
    assert w._visual_state() == "AWAITING"
    txt = _draw_text(w)
    assert "awaiting lts-engine status" in txt


def test_rail_bus_update_redraws_and_coerces_state():
    w, ctx = _mounted()
    ctx.redraws = 0
    w.on_bus_state(DEFAULT_NAMESPACE, {
        "reachable": True, "fresh": True, "corpus_count": 3,
        "total_nodes": 1284, "total_edges": 2531,
        "last_refresh": "2026-07-25T09:00:00", "corpora": []})
    assert ctx.redraws == 1                        # request_redraw ONLY
    assert w._visual_state() == "UP_FRESH"


def test_rail_three_visual_states_render_distinctly():
    w, _ = _mounted()
    # UP + FRESH
    w.on_bus_state(DEFAULT_NAMESPACE, {"reachable": True, "fresh": True,
                                       "corpus_count": 3, "total_nodes": 1284,
                                       "total_edges": 2531,
                                       "last_refresh": None, "corpora": []})
    up = _draw_text(w)
    assert "UP" in up and "FRESH" in up and "STALE" not in up
    assert "1,284" in up and "2,531" in up         # metrics rendered
    # UP + STALE
    w.on_bus_state(DEFAULT_NAMESPACE, {"reachable": True, "fresh": False,
                                       "corpus_count": 3, "total_nodes": 1,
                                       "total_edges": 2, "last_refresh": None,
                                       "corpora": []})
    stale = _draw_text(w)
    assert "STALE" in stale and "FRESH" not in stale
    # DOWN -> never a false fresh/stale
    w.on_bus_state(DEFAULT_NAMESPACE, {"reachable": False, "fresh": False,
                                       "corpus_count": 0, "total_nodes": 0,
                                       "total_edges": 0, "last_refresh": None,
                                       "corpora": []})
    down = _draw_text(w)
    assert "DOWN" in down and "OFFLINE" in down
    assert "FRESH" not in down and "STALE" not in down


def test_rail_open_button_publishes_decisions_open_intent():
    w, ctx = _mounted()
    w.on_bus_state(DEFAULT_NAMESPACE, {"reachable": True, "fresh": True,
                                       "corpus_count": 1, "total_nodes": 1,
                                       "total_edges": 1, "last_refresh": None,
                                       "corpora": []})
    _draw_text(w)                                  # records the Open hit zone
    assert w._open_zone is not None
    z = w._open_zone
    w.on_tap(z.x + z.w / 2.0, z.y + z.h / 2.0)
    assert ctx.published == [("_app/graph/open", "open")]


def test_rail_bus_envelope_val_unwrapped():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_NAMESPACE, {"val": {"reachable": True,
                                               "fresh": False,
                                               "corpus_count": 2,
                                               "total_nodes": 5,
                                               "total_edges": 6,
                                               "last_refresh": None,
                                               "corpora": []}})
    assert w._visual_state() == "UP_STALE"


# =============================================================================
# decisions_viewer_launcher — intent -> spawn -> dedupe ([CXC-DVL-LAUNCHER])
# =============================================================================

def test_launcher_selftest_passes():
    mod = _load_tool("decisions_viewer_launcher")
    assert mod._selftest() == 0


def test_launcher_open_spawns_once_and_dedupes():
    mod = _load_tool("decisions_viewer_launcher")

    class FakeProc:
        def __init__(self):
            self.pid = 999
            self._alive = True

        def poll(self):
            return None if self._alive else 0

    spawned = []
    lau = mod.DecisionsViewerLauncher(spawn=lambda s: spawned.append(s)
                                      or FakeProc())
    r1 = lau.open()
    assert r1.startswith("opened") and len(spawned) == 1
    # [SPEC:CXC-GRAPH-VIEWER] [CXC-GV-RENAME] the spawn targets the generalized
    # Graph Viewer scene UNDER the repo root.
    assert spawned[0] == mod.resolve_scene(str(mod.DECISIONS_SCENE))
    assert spawned[0].endswith("graph_viewer.compose.yaml")
    # a SECOND open while alive is SKIPPED (still exactly one window)
    r2 = lau.open()
    assert "already open" in r2 and len(spawned) == 1
    # after the process dies, a later open spawns FRESH
    lau._windows.live[spawned[0]]._alive = False
    r3 = lau.open()
    assert r3.startswith("opened") and len(spawned) == 2


def test_launcher_falsy_intent_spawns_nothing():
    mod = _load_tool("decisions_viewer_launcher")
    assert mod.parse_intent("") is False
    assert mod.parse_intent("close") is False
    assert mod.parse_intent(0) is False


# =============================================================================
# registration ([CXC-DVL-REGISTER])
# =============================================================================

def test_both_sources_registered_session_independent():
    ds = _load_tool_from("compose_projects/intelligence/console", "data_sources")
    # no session pinned -> transcript sources omitted, but the two CXC-DVL
    # sources are session-independent and MUST still register
    names = [s.name for s in ds.build_console_sources(
        session_jsonl=None, intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-24")]
    assert "decisions_viewer_launcher" in names
    assert "lts_engine_status" in names
    assert "self_publisher" not in names           # (no session)


def test_data_sources_selftest_counts_updated():
    ds = _load_tool_from("compose_projects/intelligence/console", "data_sources")
    assert ds._selftest() == 0


def _load_tool_from(reldir, name):
    path = os.path.join(REPO_ROOT, reldir, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
