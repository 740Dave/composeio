"""
Tests for the ActivityWidget + SubagentLedgerWidget sub-widgets
([SPEC:CXC-IC]) — the Intelligence Console panels that light up on semantic
events.

Covers the load-bearing behaviors static reading can't prove: payload parsing
(dict AND json-string), the in-place update-by-id (a start/dispatched followed
by its done updates the SAME row, never a second one), duration + token
formatting, the ring cap, that draw() paints without error, and end-to-end
parser registration + single-namespace subscription wiring.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import ActivityWidget, SubagentLedgerWidget
from composeio.render.widgets.activity import (
    DEFAULT_NAMESPACE as ACT_NS, format_dur)
from composeio.render.widgets.subagent_ledger import (
    DEFAULT_NAMESPACE as SUB_NS, format_tokens)

from .conftest import surface_to_array


def _canvas(w=420, h=240):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x16)
    c.fill_rect(0, 0, w, h)
    return c


# =============================================================================
# ActivityWidget
# =============================================================================

def test_activity_start_is_active_accent():
    w = ActivityWidget("a")
    w.on_bus_state("self/activity",
                   {"tool": "Read", "target": "spec 34", "phase": "start",
                    "id": "act-1"})
    row = w._rows["act-1"]
    assert row.active is True
    assert row.tool == "Read" and row.target == "spec 34"
    text, color = w._line_text(row)
    assert color == w.accent
    assert "Read spec 34" in text
    assert text.startswith("›")


def test_activity_done_updates_same_row_in_place():
    w = ActivityWidget("a")
    w.on_bus_state("self/activity",
                   {"tool": "Read", "target": "spec 34", "phase": "start",
                    "id": "act-1"})
    w.on_bus_state("self/activity",
                   {"phase": "done", "id": "act-1", "dur_s": 0.3})
    # SAME row, not two
    assert len(w._rows) == 1
    row = w._rows["act-1"]
    assert row.active is False
    assert row.dur == "0.3s"
    text, color = w._line_text(row)
    assert color == w.dim
    assert "Read spec 34 · 0.3s" in text


def test_activity_done_without_start_creates_completed_row():
    w = ActivityWidget("a")
    w.on_bus_state("self/activity",
                   {"tool": "Grep", "target": "canvas", "phase": "done",
                    "id": "orphan", "dur_s": 1.2})
    assert len(w._rows) == 1
    row = w._rows["orphan"]
    assert row.active is False and row.dur == "1.2s"


def test_activity_accepts_json_string_payload():
    w = ActivityWidget("a")
    w.on_bus_state("self/activity",
                   '{"tool":"Edit","target":"widget.py","phase":"start","id":"j1"}')
    assert w._rows["j1"].tool == "Edit"


def test_activity_missing_fields_graceful():
    w = ActivityWidget("a")
    # no tool/target/id — still tracked, renders a fallback label
    w.on_bus_state("self/activity", {"phase": "start"})
    assert len(w._rows) == 1
    row = list(w._rows.values())[0]
    text, _ = w._line_text(row)
    assert "(activity)" in text


def test_activity_cap_drops_oldest():
    w = ActivityWidget("a", max_lines=3)
    for i in range(10):
        w.on_bus_state("self/activity",
                       {"tool": "Read", "target": f"f{i}", "phase": "start",
                        "id": f"id{i}"})
    assert len(w._rows) == 3
    assert "id0" not in w._rows
    assert "id9" in w._rows


def test_activity_default_namespace():
    w = ActivityWidget("a")
    assert w.namespace == ACT_NS == "self/activity"


def test_activity_bad_payload_ignored():
    w = ActivityWidget("a")
    w.on_bus_state("self/activity", 12345)        # not dict/str
    w.on_bus_state("self/activity", "not json")   # bare string, no {
    assert w._rows == {} or len(w._rows) == 0


def test_activity_draw_does_not_raise():
    w = ActivityWidget("panel")
    w.on_bus_state("self/activity",
                   {"tool": "Read", "target": "spec 34", "phase": "start",
                    "id": "1"})
    w.on_bus_state("self/activity",
                   {"tool": "Grep", "target": "very long target name that must "
                    "be ellipsized to fit the panel column width", "phase": "done",
                    "id": "2", "dur_s": 288})
    c = _canvas()
    w.draw(c, Rect(0, 0, 420, 160))
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 3.0


def test_activity_draw_empty_shows_idle():
    w = ActivityWidget("panel")
    c = _canvas()
    w.draw(c, Rect(0, 0, 420, 160))   # must not raise


# =============================================================================
# SubagentLedgerWidget
# =============================================================================

def test_subagent_dispatched_is_running_accent():
    w = SubagentLedgerWidget("s")
    w.on_bus_state("self/subagents",
                   {"type": "general-purpose", "model": "opus",
                    "desc": "Pre-vis Instrument", "status": "dispatched",
                    "id": "sa-1"})
    row = w._rows["sa-1"]
    assert row.running is True
    text, color = w._line_text(row)
    assert color == w.accent
    assert text.startswith("›")
    assert "general-purpose [opus]" in text
    assert "Pre-vis Instrument" in text
    assert text.endswith("running")


def test_subagent_done_updates_same_row_not_two():
    w = SubagentLedgerWidget("s")
    w.on_bus_state("self/subagents",
                   {"type": "general-purpose", "model": "opus",
                    "desc": "Pre-vis Instrument", "status": "dispatched",
                    "id": "sa-1"})
    w.on_bus_state("self/subagents",
                   {"status": "done", "id": "sa-1", "dur_s": 288, "tokens": 72000})
    # THE critical assertion: one row, updated in place
    assert len(w._rows) == 1
    row = w._rows["sa-1"]
    assert row.running is False
    assert row.dur == "4m48s"
    assert row.tokens == "72k"
    text, color = w._line_text(row)
    assert color == w.ok
    assert text.startswith("✓")
    assert "general-purpose [opus] · Pre-vis Instrument · 4m48s · 72k" in text


def test_subagent_done_without_dispatch_creates_row():
    w = SubagentLedgerWidget("s")
    w.on_bus_state("self/subagents",
                   {"type": "Explore", "status": "done", "id": "x",
                    "dur_s": 45, "tokens": 8000})
    assert len(w._rows) == 1
    row = w._rows["x"]
    assert row.running is False and row.dur == "45s" and row.tokens == "8k"


def test_subagent_json_string_payload():
    w = SubagentLedgerWidget("s")
    w.on_bus_state("self/subagents",
                   '{"type":"Plan","model":"sonnet","desc":"layout","status":"dispatched","id":"j"}')
    assert w._rows["j"].type == "Plan"
    assert w._rows["j"].model == "sonnet"


def test_subagent_missing_model_graceful():
    w = SubagentLedgerWidget("s")
    w.on_bus_state("self/subagents",
                   {"type": "worker", "desc": "task", "status": "dispatched",
                    "id": "m"})
    text, _ = w._line_text(w._rows["m"])
    assert "[" not in text   # no model bracket when model absent
    assert text.startswith("› worker")


def test_subagent_long_desc_truncated():
    w = SubagentLedgerWidget("s")
    long = "A very long subagent description that goes well beyond the row width"
    w.on_bus_state("self/subagents",
                   {"type": "t", "desc": long, "status": "dispatched", "id": "d"})
    text, _ = w._line_text(w._rows["d"])
    assert "…" in text


def test_subagent_cap_drops_oldest():
    w = SubagentLedgerWidget("s", max_rows=4)
    for i in range(12):
        w.on_bus_state("self/subagents",
                       {"type": "t", "status": "dispatched", "id": f"id{i}"})
    assert len(w._rows) == 4
    assert "id0" not in w._rows
    assert "id11" in w._rows


def test_subagent_default_namespace():
    w = SubagentLedgerWidget("s")
    assert w.namespace == SUB_NS == "self/subagents"


def test_subagent_draw_does_not_raise():
    w = SubagentLedgerWidget("panel")
    w.on_bus_state("self/subagents",
                   {"type": "general-purpose", "model": "opus",
                    "desc": "Pre-vis Instrument", "status": "dispatched",
                    "id": "1"})
    w.on_bus_state("self/subagents",
                   {"type": "Explore", "model": "haiku", "desc": "sweep",
                    "status": "done", "id": "2", "dur_s": 125, "tokens": 34000})
    c = _canvas()
    w.draw(c, Rect(0, 0, 420, 220))
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 3.0


# =============================================================================
# [CXC-ICV-SUBAGENTS] linger-then-remove: done rows must disappear
# =============================================================================

def test_subagent_done_row_pruned_after_linger():
    """A done row lingers (visibly resolved) for linger_s, then is REMOVED so
    the live ledger never grows an unbounded pile of ✓ rows."""
    w = SubagentLedgerWidget("s")
    w.linger_s = 5.0
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "dispatched", "id": "a"})
    w.on_bus_state("self/subagents",
                   {"status": "done", "id": "a", "dur_s": 12, "tokens": 8000})
    row = w._rows["a"]
    assert row.running is False and row.done_at is not None
    # still lingering just after done: kept, no repaint demanded by the tick
    assert w.on_clock_tick(row.done_at + 1.0) is False
    assert "a" in w._rows
    # linger elapsed: pruned AND the tick returns True so the frame repaints
    assert w.on_clock_tick(row.done_at + 5.01) is True
    assert "a" not in w._rows


def test_subagent_running_row_never_pruned():
    """Running rows are kept forever — only done rows linger-then-remove."""
    w = SubagentLedgerWidget("s")
    w.linger_s = 0.5
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "dispatched", "id": "run"})
    import time as _t
    w.on_clock_tick(_t.monotonic() + 1000.0)   # far past any linger
    assert "run" in w._rows and w._rows["run"].running is True


def test_subagent_done_pile_capped_to_done_keep():
    """A burst of completions never stacks: at most done_keep (2) done rows
    survive, the most recent, even inside the linger window."""
    w = SubagentLedgerWidget("s")
    w.linger_s = 1000.0            # time bound irrelevant here
    w.done_keep = 2
    for i in range(6):
        w.on_bus_state("self/subagents",
                       {"type": "t", "status": "done", "id": f"d{i}",
                        "dur_s": 3})
    done_ids = [rid for rid, r in w._rows.items() if not r.running]
    assert len(done_ids) == 2
    # the two MOST-RECENT completions survive
    assert set(done_ids) == {"d4", "d5"}


def test_subagent_redispatch_cancels_linger():
    """A row that goes done then is re-dispatched clears its linger and is a
    running row again (never pruned)."""
    w = SubagentLedgerWidget("s")
    w.linger_s = 0.1
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "done", "id": "z", "dur_s": 2})
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "dispatched", "id": "z"})
    row = w._rows["z"]
    assert row.running is True and row.done_at is None
    import time as _t
    w.on_clock_tick(_t.monotonic() + 100.0)
    assert "z" in w._rows


# =============================================================================
# duration + token formatting
# =============================================================================

def test_format_dur_minutes():
    assert format_dur(288) == "4m48s"
    assert format_dur(60) == "1m00s"
    assert format_dur(245) == "4m05s"    # zero-padded seconds


def test_format_dur_seconds():
    assert format_dur(0.3) == "0.3s"
    assert format_dur(12) == "12s"
    assert format_dur(9.9) == "9.9s"


def test_format_dur_bad_input():
    assert format_dur(None) == ""
    assert format_dur("nan-ish") == ""
    assert format_dur(-5) == ""


def test_format_dur_rounding_rollover():
    # 59.7s rounds to 60 -> must roll to 1m00s, never "0m60s"
    assert format_dur(59.7) == "1m00s"


def test_format_tokens():
    assert format_tokens(72000) == "72k"
    assert format_tokens(1500) == "1.5k"
    assert format_tokens(2000) == "2k"
    assert format_tokens(800) == "800"
    assert format_tokens(None) == ""


# =============================================================================
# end-to-end: parser registration + single-namespace wiring
# =============================================================================

def test_scene_parses_and_wires_both_widgets():
    from composeio.render.engine.parser import parse_compose_yaml
    scene = os.path.join(PY_ROOT, "composeio", "render", "examples",
                         "subwidgets_demo.compose.yaml")
    comp = parse_compose_yaml(scene)
    subbed = [ns for ns, _w in comp.subscriptions]
    assert "self/activity" in subbed
    assert "self/subagents" in subbed


def test_types_parse_to_correct_widgets():
    from composeio.render.engine.parser import build_leaf
    from composeio.render.engine.parser import Composition
    comp = Composition(root=None)
    a = build_leaf({"type": "activity", "id": "a1"}, comp)
    s = build_leaf({"type": "subagent_ledger", "id": "s1"}, comp)
    assert isinstance(a, ActivityWidget)
    assert isinstance(s, SubagentLedgerWidget)


def test_headless_scene_render(tmp_path):
    from composeio.render.engine.runtime import RenderApp
    scene = os.path.join(PY_ROOT, "composeio", "render", "examples",
                         "subwidgets_demo.compose.yaml")
    out = str(tmp_path / "subwidgets.png")
    app = RenderApp.from_file(scene, windowed=False)
    app.start()
    app.render_headless(out)
    app.stop()
    assert os.path.exists(out)
    assert os.path.getsize(out) > 2000


# =============================================================================
# [CXC-IC-SUBAGENT-LIFECYCLE] cross-widget prune invariant
# =============================================================================
# The bound recurred as a bug because it was FORKED (subagent_ledger._prune +
# intelligence_card._prune_chips, divergent copies) and never a cross-widget
# test.  This parametrizes the invariant over EVERY widget rendering
# self/subagents, so a future third such widget is added to the guard by one
# line.  It fails loudly if any of them lacks the shared prune.

def _mk_subagent_ledger():
    from composeio.render.widgets import SubagentLedgerWidget
    w = SubagentLedgerWidget("s")
    return w, w._rows, w.linger_s, w.done_keep, w.stale_s


def _mk_intelligence_card():
    from composeio.render.widgets import IntelligenceCardWidget
    from composeio.render.widgets.intelligence_card import (
        _CHIP_LINGER_S, _CHIP_DONE_KEEP, _CHIP_STALE_S)
    w = IntelligenceCardWidget("ic")
    return w, w._chips, _CHIP_LINGER_S, _CHIP_DONE_KEEP, _CHIP_STALE_S


@pytest.mark.parametrize("factory",
                         [_mk_subagent_ledger, _mk_intelligence_card],
                         ids=["subagent_ledger", "intelligence_card"])
def test_subagent_chip_ledger_is_bounded_shared(factory):
    """Every widget rendering self/subagents MUST bound its chip ledger via the
    ONE shared prune (subagent_ledger.prune_subagent_rows): a burst of done
    chips caps at done_keep, a stale running chip is swept, and a done chip
    DISAPPEARS after its linger.  [CXC-IC-SUBAGENT-LIFECYCLE]."""
    w, ledger, linger_s, done_keep, stale_s = factory()

    # a running subagent that will go stale (dropped 'done' / phantom seed)
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "dispatched", "id": "phantom"})
    # a burst of 12 completions
    for i in range(12):
        w.on_bus_state("self/subagents",
                       {"type": "t", "status": "done", "id": f"d{i}",
                        "dur_s": 3, "tokens": 1000})

    # the ledger NEVER grows unboundedly: count bound held on ingest, at most
    # done_keep done chips survive alongside the running one.
    done_ids = [k for k, it in ledger.items() if not it.running]
    assert len(done_ids) <= done_keep
    assert len(ledger) <= done_keep + 1
    assert "phantom" in ledger and ledger["phantom"].running is True

    # a clock tick past the staleness backstop sweeps the phantom running chip
    seen = ledger["phantom"].seen
    assert w.on_clock_tick(seen + stale_s + 1.0) is True
    assert "phantom" not in ledger

    # a done chip DISAPPEARS after its linger: ingest one, tick before (kept)
    # then after (gone).
    w.on_bus_state("self/subagents",
                   {"type": "t", "status": "done", "id": "solo", "dur_s": 2})
    solo = ledger["solo"]
    assert solo.running is False and solo.done_at is not None
    assert w.on_clock_tick(solo.done_at + linger_s * 0.5) is False
    assert "solo" in ledger
    assert w.on_clock_tick(solo.done_at + linger_s + 0.5) is True
    assert "solo" not in ledger


def test_subagent_prune_is_single_implementation():
    """De-fork guard: both widgets' prune routes to the SAME callable object —
    the shared prune_subagent_rows — so no divergent copy can be reintroduced
    without this test noticing.  [CXC-IC-SUBAGENT-LIFECYCLE]/[DX-NARROW-WAIST]."""
    from composeio.render.widgets import subagent_ledger, intelligence_card
    assert intelligence_card.prune_subagent_rows \
        is subagent_ledger.prune_subagent_rows
