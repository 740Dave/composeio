"""
Tests for the Night Watch task checklist (David 2026-07-22: "a list of the
session's tasks each tagged done / partial / deferred (distinct visual states)").

Acceptance: data-driven off one namespace (default fun/nightwatch/tasks) with a
static `tasks:` render default; each of the three statuses draws a DISTINCT
marker AND a state-coloured status tag; an unknown status is normalized to
`deferred` (never dropped, never a fake done); the parser wires the widget.
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
from composeio.render.widgets import NightWatchChecklistWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.nightwatch_checklist import (
    DEFAULT_NAMESPACE, ROW_H)

from .conftest import surface_to_array

RECT = Rect(0, 0, 480, 216)

TASKS = [
    {"label": "Config tab", "status": "done"},
    {"label": "Projects tab", "status": "done"},
    {"label": "Drop files onto the prompt", "status": "deferred"},
    {"label": "Agent-registry reload", "status": "partial"},
]


class _Ctx:
    theme = "dark"
    reduced_motion = False

    def __init__(self):
        self.redraws = 0

    def request_redraw(self, rect=None):
        self.redraws += 1


def _mounted(tasks=None):
    w = NightWatchChecklistWidget("tasks", tasks=tasks)
    ctx = _Ctx()
    w.on_mount(ctx)
    return w, ctx


def _drawn(w):
    c = SkiaCanvas(); c.create_surface(int(RECT.w), int(RECT.h))
    w.draw(c, RECT)
    return c


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# coercion / normalization
# =============================================================================

def test_static_tasks_default_from_constructor():
    w, _ = _mounted(TASKS)
    assert [s for _, s in w._tasks] == ["done", "done", "deferred", "partial"]


def test_bus_payload_list_and_dict_forms():
    w, ctx = _mounted()
    w.on_bus_state(DEFAULT_NAMESPACE, TASKS)                 # bare list
    assert len(w._tasks) == 4 and ctx.redraws == 1
    w.on_bus_state(DEFAULT_NAMESPACE, {"tasks": TASKS[:2]})   # {tasks:[...]}
    assert len(w._tasks) == 2
    import json
    w.on_bus_state(DEFAULT_NAMESPACE, json.dumps(TASKS))     # JSON string
    assert len(w._tasks) == 4


def test_unknown_status_normalizes_to_deferred_not_dropped():
    w, _ = _mounted([{"label": "mystery", "status": "wat"},
                     {"label": "bare-string-form"}])
    assert w._tasks == [("mystery", "deferred"), ("bare-string-form", "deferred")]


def test_row_without_label_dropped_and_garbage_ignored():
    w, _ = _mounted([{"status": "done"}, 42, {"label": "keep", "status": "done"}])
    assert w._tasks == [("keep", "done")]
    # a non-list/garbage payload leaves state untouched
    before = list(w._tasks)
    w.on_bus_state(DEFAULT_NAMESPACE, "not-json")
    assert w._tasks == before


# =============================================================================
# render — three DISTINCT visual states
# =============================================================================

def test_all_three_states_draw_distinct_markers():
    """done = filled disc + check (fill_circle + lines); partial = half arc +
    ring; deferred = hollow ring only.  Assert the per-state primitive mix."""
    calls = {"fill_circle": 0, "stroke_circle": 0, "fill_arc": 0, "draw_line": 0}

    class _Rec(SkiaCanvas):
        def fill_circle(self, *a, **k):
            calls["fill_circle"] += 1; return super().fill_circle(*a, **k)

        def stroke_circle(self, *a, **k):
            calls["stroke_circle"] += 1; return super().stroke_circle(*a, **k)

        def fill_arc(self, *a, **k):
            calls["fill_arc"] += 1; return super().fill_arc(*a, **k)

        def draw_line(self, *a, **k):
            calls["draw_line"] += 1; return super().draw_line(*a, **k)

    w, _ = _mounted([{"label": "d", "status": "done"},
                     {"label": "p", "status": "partial"},
                     {"label": "x", "status": "deferred"}])
    c = _Rec(); c.create_surface(int(RECT.w), int(RECT.h))
    w.draw(c, RECT)
    assert calls["fill_circle"] == 1        # done disc
    assert calls["draw_line"] == 2          # done check (two strokes)
    assert calls["fill_arc"] == 1           # partial half-fill
    assert calls["stroke_circle"] == 2      # partial ring + deferred hollow ring


def test_status_tags_drawn_per_state():
    w, _ = _mounted(TASKS)
    c = SkiaCanvas(); c.create_surface(int(RECT.w), int(RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    assert "DONE" in joined and "PARTIAL" in joined and "DEFERRED" in joined
    assert "Config tab" in joined and "Agent-registry reload" in joined


def test_draw_paints_content():
    w, _ = _mounted(TASKS)
    c = _drawn(w)
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_empty_state_message():
    w, _ = _mounted([])
    c = SkiaCanvas(); c.create_surface(int(RECT.w), int(RECT.h))
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    assert "no tasks" in "".join(drawn)


# =============================================================================
# content-sized (NOT growable) + scroll a variable-length list in a fixed box
# =============================================================================

def test_measure_is_content_sized_not_growable():
    """measure() reports the TRUE row-stack height (one ROW_H per task) and
    never inflates to fill max_height — the leaf does not grow vertically."""
    w, _ = _mounted(TASKS)                       # 4 tasks
    big = Constraints(max_width=480, max_height=10_000)
    size = w.measure(big)
    assert size.height == len(TASKS) * ROW_H     # == content, not max_height
    assert size.height < big.max_height          # did NOT fill the box


def test_short_list_does_not_scroll():
    w, _ = _mounted(TASKS)                        # 4 rows = 108px < 216 box
    _drawn(w)                                     # draw computes _max_scroll
    assert w._max_scroll == 0.0
    w.on_scroll(0.0, -5.0)                        # wheel is a no-op
    assert w._scroll == 0.0


def test_overflowing_list_scrolls_within_fixed_box():
    tasks = [{"label": f"task {i}", "status": "done"} for i in range(16)]
    w, ctx = _mounted(tasks)
    _drawn(w)                                     # 16*ROW_H content vs 216 box
    expected_max = 16 * ROW_H - RECT.h
    assert w._max_scroll == expected_max > 0
    # wheeling down moves the view and requests a scoped redraw
    r0 = ctx.redraws
    w.on_scroll(0.0, -3.0)
    assert 0.0 < w._scroll <= w._max_scroll and ctx.redraws == r0 + 1
    # cannot scroll past the end (clamped)
    w.on_scroll(0.0, -1000.0)
    assert w._scroll == w._max_scroll


def test_scroll_resets_to_top_on_new_list():
    tasks = [{"label": f"t{i}", "status": "done"} for i in range(16)]
    w, _ = _mounted(tasks)
    _drawn(w)
    w.on_scroll(0.0, -3.0)
    assert w._scroll > 0.0
    w.on_bus_state(DEFAULT_NAMESPACE, {"tasks": tasks})   # a fresh push
    assert w._scroll == 0.0


# =============================================================================
# parser wiring
# =============================================================================

def test_parser_wires_checklist_with_static_tasks():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "nightwatch_checklist", "id": "tk",
         "namespace": "fun/nightwatch/tasks", "tasks": TASKS}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed == ["fun/nightwatch/tasks"]
    w = comp.subscriptions[0][1]
    assert isinstance(w, NightWatchChecklistWidget)
    assert len(w._tasks) == 4
