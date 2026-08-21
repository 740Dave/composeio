"""
Prompt-lag RECURRENCE ([CXC-R-ENGINE-TEXT-MEASURE], David 2026-07-23) — the
console composer was "almost unusably slow to type in" AGAIN, after 4bf091b had
already made the composer field's per-keystroke measurement linear.

Root cause (measured): the composer field itself was fine in isolation
(~0.3 ms/keystroke, flat).  The lag came from the READ SURFACE — the chat
thread re-WRAPPED and re-MEASURED its ENTIRE scrollback on EVERY draw, and it
draws on every inbound bus turn (narration/reply/heartbeat).  On the single
render thread that also renders keystrokes, that re-wrap (O(total turns),
growing unbounded as a session accumulates history) saturated the loop, so
keystrokes queued behind it.  4bf091b fixed the composer's `_prefix_widths`
cache but FORKED the read surface, which measured-per-draw with NO cache.

This file is the cross-implementation guard the recurrence proves is needed:
the "measure once; a redraw of UNCHANGED content re-measures nothing" invariant
is GENERAL — it binds BOTH measurement paths (the composer's single-line prefix
advances AND the thread's multi-line word-wrap).  Each test below FAILS against
the pre-fix chat_thread (which re-wraps every turn every draw) and passes after.

Whether it FEELS smooth is glass — David's eyes.  This is the machine tier:
per-frame measurement cost stays flat as the buffer / scrollback grows.

Run:  python3 -m pytest tests/render/test_prompt_lag_recurrence.py -q
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import ChatThreadWidget, TextInputWidget
from composeio.render.widgets import chat_thread as ct_mod
from tests.render.console_scene_ids import (  # noqa: E402
    composer as _composer,
    composer_field as _cf,
    composer_send as _cs,
    send_trigger_ns as _send_ns,
    thread as _th)

# [CXC-FC-UNIT] the console's collaboration surface is now N `intelligence_pane`
# instantiations, so every per-instance widget id is rooted at
# `<id_prefix>_<instance_id>_`. These ids are DERIVED from the scene (see
# `console_scene_ids`) rather than re-typed, so adding or repointing an instance
# moves the scene and these tests together.


class _CountingCanvas(SkiaCanvas):
    """A real SkiaCanvas that tallies characters passed to measure_text — the
    honest cost signal (a per-draw full re-wrap hides in the LENGTH of the
    strings it measures, not the call count alone)."""

    def __init__(self):
        super().__init__()
        self.measured_chars = 0

    def measure_text(self, text: str):
        self.measured_chars += len(text)
        return super().measure_text(text)


class _Ctx:
    def request_redraw(self, rect=None): pass
    def publish(self, *a, **k): pass
    def schedule_update(self, fn, *, target=None): pass
    def register_clocked(self, w): pass
    def unregister_clocked(self, w): pass


_THREAD_RECT = Rect(0, 0, 456, 356)


def _thread(n_turns: int) -> ChatThreadWidget:
    w = ChatThreadWidget("thread")
    try:
        w.on_mount(_Ctx())
    except Exception:
        pass
    for i in range(n_turns):
        body = ("narration turn %d: the decomposed bus-peer model is why "
                "composeio exists and this turn wraps across several lines of "
                "the read column so its layout is non-trivial to measure " % i)
        w.on_bus_state("thread/narration",
                       {"from": "self", "text": body, "turn": i})
    return w


# ---------------------------------------------------------------------------
# 1. THE RECURRENCE: a redraw of an UNCHANGED thread must re-wrap NOTHING.
#    Pre-fix, chat_thread._build_body_blocks ran for EVERY turn on EVERY draw
#    (both the heights pass and the paint pass) — this asserts zero on an
#    unchanged redraw, which the pre-fix source cannot satisfy.
# ---------------------------------------------------------------------------

def test_unchanged_thread_redraw_rewraps_nothing():
    c = _CountingCanvas()
    c.create_surface(int(_THREAD_RECT.w), int(_THREAD_RECT.h))
    w = _thread(80)
    w.draw(c, _THREAD_RECT)            # warm: wrap every turn once

    calls = {"n": 0}
    orig = ChatThreadWidget._build_body_blocks

    def _counted(self, *a, **k):
        calls["n"] += 1
        return orig(self, *a, **k)

    ChatThreadWidget._build_body_blocks = _counted
    try:
        w.draw(c, _THREAD_RECT)        # identical redraw
    finally:
        ChatThreadWidget._build_body_blocks = orig

    assert calls["n"] == 0, (
        f"a redraw of an unchanged {len(w._messages)}-turn thread re-wrapped "
        f"{calls['n']} turns — the read surface re-measures per draw, the "
        "forked prompt-lag defect ([CXC-R-ENGINE-TEXT-MEASURE])")


# ---------------------------------------------------------------------------
# 2. FLAT IN SCROLLBACK DEPTH: a redraw's measurement cost is O(visible +
#    changed turns), independent of how deep the history is.  The extra
#    off-screen turns of a deep thread add ~0 measured characters.
# ---------------------------------------------------------------------------

def _measure_one_redraw(n_turns: int) -> int:
    c = _CountingCanvas()
    c.create_surface(int(_THREAD_RECT.w), int(_THREAD_RECT.h))
    w = _thread(n_turns)
    w.draw(c, _THREAD_RECT)            # warm
    c.measured_chars = 0
    w.draw(c, _THREAD_RECT)            # the measured redraw
    return c.measured_chars


def test_thread_redraw_cost_is_flat_in_scrollback_depth():
    shallow = _measure_one_redraw(30)
    deep = _measure_one_redraw(400)
    # The 370 extra off-screen turns must not inflate a redraw's measurement.
    # Pre-fix this grows ~linearly with depth (measured ~4.7x here); the memo
    # holds it flat.  A generous 1.5x bound is unmistakably on the flat side of
    # that gap and robust to viewport/rounding differences.
    assert deep <= shallow * 1.5 + 2000, (
        f"a 400-turn thread redraw measured {deep} chars vs {shallow} for a "
        f"30-turn thread — cost scales with scrollback DEPTH, not viewport "
        "([CXC-R-ENGINE-TEXT-MEASURE] flat-in-depth)")


# ---------------------------------------------------------------------------
# 3. CROSS-IMPLEMENTATION: the SAME invariant on the composer field — the
#    other measurement path.  A redraw with unchanged text re-measures the
#    body zero times (the _prefix_widths cache short-circuits), and typing N
#    chars measures O(N), not O(N^2).  This is the 4bf091b property, pinned
#    here beside the thread's so a future drop of EITHER cache fails loudly.
# ---------------------------------------------------------------------------

def _composer() -> TextInputWidget:
    w = TextInputWidget(_cf(), chrome="console")
    w.on_mount(_Ctx())
    return w


def test_composer_unchanged_redraw_remeasures_no_body():
    c = _CountingCanvas()
    c.create_surface(360, 120)
    w = _composer()
    rect = Rect(0, 0, 360, 68)
    for ch in "the quick brown fox jumps over the lazy dog":
        w.on_text(ch)
    w.draw(c, rect)                   # warm the advance table
    before = c.measured_chars
    w.draw(c, rect)                   # identical redraw
    delta = c.measured_chars - before
    # An unchanged redraw may measure a tiny constant (baseline "Ag" et al.)
    # but MUST NOT re-measure the field body (len == 43 here).
    assert delta < len(w.text), (
        f"an unchanged composer redraw measured {delta} chars for a "
        f"{len(w.text)}-char field — the prefix cache is bypassed "
        "([CXC-R-ENGINE-TEXT-MEASURE])")


@pytest.mark.parametrize("n", [50, 400])
def test_composer_typing_is_linear(n):
    c = _CountingCanvas()
    c.create_surface(360, 120)
    w = _composer()
    rect = Rect(0, 0, 360, 68)
    w.draw(c, rect)
    c.measured_chars = 0
    for _ in range(n):
        w.on_text("x")
        w.draw(c, rect)
    assert c.measured_chars < 40 * n, (
        f"typing {n} chars measured {c.measured_chars} — quadratic re-measure, "
        "not the linear glyph-advance build ([CXC-R-ENGINE-TEXT-MEASURE])")
