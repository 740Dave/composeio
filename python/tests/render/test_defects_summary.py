"""
Tests for the Dashboard Defects card ([SPEC:CIO-DEFECT-REGISTRY]) — the read-only
summary of the Defect Registry roll-up (self/architect/defects).

Acceptance: given the defect_metrics_publisher roll-up, the card derives the
total, the 3-cell root-cause gauge (under-spec/fork/recurrence), the recurrence
rate, and the fix-resistant leaders; a bus update requests a scoped redraw only,
and an idempotent redelivery damages nothing ([CXC-R-DAMAGE-ONCHANGE]).
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import DefectsSummaryWidget
from composeio.render.widgets import console_theme as ct

from .conftest import surface_to_array

PAYLOAD = {
    "ts": "2026-07-26T00:00:00Z",
    "total": 5,
    "by_root_cause": {"under-spec": 3, "fork-divergence": 1, "oversight": 1},
    "structural": {"under_spec": 3, "fork": 1, "recurrence": 2},
    "recurrence_rate": 0.4,
    "fix_resistant": [
        {"symptom": "heartbeat get_layout returns a merged snapshot", "attempts": 3,
         "status": "recurred"},
        {"symptom": "done-chips don't disappear after subagent done", "attempts": 2,
         "status": "verified"},
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


def teardown_function(_fn):
    ct.set_theme("dark")


def _mounted():
    w = DefectsSummaryWidget("df")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = Rect(4, 4, 360, 240)
    return w, ctx


# --- payload -> derived state -----------------------------------------------

def test_payload_coercion_dict_and_json_string():
    for value in (PAYLOAD, json.dumps(PAYLOAD)):
        w = DefectsSummaryWidget("df")
        w.on_bus_state("self/architect/defects", value)
        assert w._total == 5
        assert w._structural == {"under_spec": 3, "fork": 1, "recurrence": 2}
        assert w._recurrence_rate == 0.4
        assert w._rate_str() == "40% recur"
        assert len(w._fix_resistant) == 2
        assert w._fix_resistant[0]["attempts"] == 3


def test_stat_cells_reflect_structural_tallies():
    w = DefectsSummaryWidget("df")
    w.on_bus_state("self/architect/defects", PAYLOAD)
    vals = [c[0] for c in w._stat_cells()]
    labels = [c[1] for c in w._stat_cells()]
    assert vals == ["3", "1", "2"]
    assert labels == ["UNDER-SPEC", "FORK", "RECURRENCE"]


def test_empty_store_renders_dashes():
    w = DefectsSummaryWidget("df")
    w.on_bus_state("self/architect/defects", {"total": 0})
    assert [c[0] for c in w._stat_cells()] == ["—", "—", "—"]
    assert w._rate_str() == "—"


def test_garbage_payload_never_clobbers_state():
    w = DefectsSummaryWidget("df")
    w.on_bus_state("self/architect/defects", PAYLOAD)
    w.on_bus_state("self/architect/defects", "not-json")
    w.on_bus_state("self/architect/defects", 42)
    assert w._total == 5                          # kept


# --- damage discipline -------------------------------------------------------

def test_bus_update_requests_scoped_redraw_only():
    w, ctx = _mounted()
    w.on_bus_state("self/architect/defects", PAYLOAD)
    assert ctx.redraws == 1


def test_redelivered_unchanged_rollup_damages_nothing():
    w, ctx = _mounted()
    w.on_bus_state("self/architect/defects", PAYLOAD)
    assert ctx.redraws == 1
    for _ in range(30):
        w.on_bus_state("self/architect/defects", PAYLOAD)
    assert ctx.redraws == 1


def test_a_real_change_still_redraws():
    w, ctx = _mounted()
    w.on_bus_state("self/architect/defects", PAYLOAD)
    assert ctx.redraws == 1
    moved = json.loads(json.dumps(PAYLOAD))
    moved["structural"]["recurrence"] = 3
    w.on_bus_state("self/architect/defects", moved)
    assert ctx.redraws == 2


# --- render ------------------------------------------------------------------

def test_renders_non_blank_with_data():
    w, _ = _mounted()
    w.on_bus_state("self/architect/defects", PAYLOAD)
    c = SkiaCanvas(); c.create_surface(390, 260)
    w.draw(c, Rect(4, 4, 360, 240))
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_empty_state_renders_awaiting():
    w, _ = _mounted()
    c = SkiaCanvas(); c.create_surface(390, 260)
    w.draw(c, Rect(4, 4, 360, 240))              # no data yet — must not raise
    assert surface_to_array(c)[..., :3].std() > 1.0


# --- registry / placement ----------------------------------------------------

def test_registered_dashboard_eligible_with_dashboard_affinity():
    from composeio.render.engine.widget_registry import get_descriptor
    d = get_descriptor("defects_summary")
    assert d is not None and d.cls is DefectsSummaryWidget
    assert "dashboard-eligible" in d.categories
    assert d.region_affinity == frozenset({"dashboard"})
    # steered off the 332px rail by min-width
    assert d.min_size[0] > 332
