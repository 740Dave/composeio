"""
Composer caret blink under dirty-region rendering ([CXC-R-DAMAGE] caret
animation).

Regression: under damage rendering nothing repainted the caret between
keystrokes, so the blinking accent caret froze ON.  The fix registers a
FOCUSED text_input as a clocked widget whose on_clock_tick toggles the caret
phase at ~1 Hz and damages ONLY the caret's small sliver — never the whole
field, never the scene.  It honors reduced_motion (solid caret, no clock, no
per-frame damage) and only clocks while focused (an unfocused/blurred field
neither ticks nor damages).

These pin the mechanism headlessly; the live blink itself is David's glass
gate (an on-screen visual property no unit test observes).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.widget import Rect
from composeio.render.widgets import TextInputWidget
from composeio.render.widgets.text_input import _CARET_PERIOD

# [CXC-FC-UNIT] the console's collaboration surface is now N `intelligence_pane`
# instantiations, so every per-instance widget id is rooted at
# `<id_prefix>_<instance_id>_`. These ids are DERIVED from the scene (see
# `console_scene_ids`) rather than re-typed, so adding or repointing an instance
# moves the scene and these tests together.
from tests.render.console_scene_ids import (composer_field as _cf,  # noqa: E402
                                              composer_send as _cs,
                                              send_trigger_ns as _send_ns,
                                              thread as _th)


class _Ctx:
    """Minimal engine context: records clocked registrations."""

    def __init__(self, reduced_motion: bool = False) -> None:
        self.reduced_motion = reduced_motion
        self.theme = "dark"
        self.clocked: list = []
        self.published: list = []

    def register_clocked(self, w) -> None:
        if w not in self.clocked:
            self.clocked.append(w)

    def unregister_clocked(self, w) -> None:
        self.clocked = [x for x in self.clocked if x is not w]

    def request_redraw(self, rect=None) -> None:
        pass

    def publish(self, ns, value, source="") -> None:
        self.published.append((ns, value))


def _field(ctx: _Ctx, **kwargs) -> TextInputWidget:
    w = TextInputWidget(_cf(), chrome="console", **kwargs)
    w.on_mount(ctx)
    w.arranged_rect = Rect(20, 1236, 826, 32)
    # simulate a draw having located the caret sliver
    w._caret_rect = Rect(120, 1242, 3, 20)
    return w


# ---------------------------------------------------------------------------
# Focused: clocks + blinks + damages only the caret sliver
# ---------------------------------------------------------------------------

def test_focus_registers_clocked_widget():
    ctx = _Ctx()
    w = _field(ctx)
    assert w not in ctx.clocked
    w.focused = True
    w.on_focus()
    assert w in ctx.clocked, "focused field must clock its caret blink"


def test_clock_tick_toggles_phase_at_1hz():
    ctx = _Ctx()
    w = _field(ctx)
    w.focused = True
    w.on_focus()
    # sample across two full periods; a ~1 Hz square wave flips ~4 times
    flips = 0
    steps = 40
    for i in range(steps):
        t = i * (2.0 * _CARET_PERIOD / steps)
        if w.on_clock_tick(t):
            flips += 1
    assert 3 <= flips <= 5, f"expected ~4 caret flips over 2 periods, got {flips}"


def test_clock_tick_returns_false_between_flips():
    """A tick that does NOT change the phase reports no repaint — the caret
    damages the sliver ~twice a second, not every frame."""
    ctx = _Ctx()
    w = _field(ctx)
    w.focused = True
    w.on_focus()
    # force a known phase, then tick again very close in time (same half)
    w._caret_on = w._caret_phase(0.10)
    assert w.on_clock_tick(0.11) is False


def test_blink_damages_only_the_caret_sliver_not_the_field():
    ctx = _Ctx()
    w = _field(ctx)
    w.focused = True
    w.on_focus()
    dmg = w.clock_damage_rect()
    # the caret sliver, NOT the whole field
    assert dmg is w._caret_rect
    assert dmg.w <= 4 and dmg.w < w.arranged_rect.w
    assert dmg.h <= w.arranged_rect.h


# ---------------------------------------------------------------------------
# Blurred: no clock, no tick, no damage
# ---------------------------------------------------------------------------

def test_blur_unregisters_and_stops_ticking():
    ctx = _Ctx()
    w = _field(ctx)
    w.focused = True
    w.on_focus()
    assert w in ctx.clocked

    w.focused = False
    w.on_blur()
    assert w not in ctx.clocked, "blurred field must stop clocking"
    # even if a stale tick races in, a blurred field reports no repaint
    assert w.on_clock_tick(0.1) is False


def test_unfocused_field_never_clocks():
    ctx = _Ctx()
    w = _field(ctx)          # never focused
    assert w not in ctx.clocked
    assert w.on_clock_tick(0.1) is False


# ---------------------------------------------------------------------------
# reduced_motion: solid caret, no clock, no per-frame damage
# ---------------------------------------------------------------------------

def test_reduced_motion_caret_is_solid_and_unclocked():
    ctx = _Ctx(reduced_motion=True)
    w = _field(ctx)
    w.focused = True
    w.on_focus()
    assert w not in ctx.clocked, "reduced_motion must not clock the caret"
    assert w._caret_on is True, "reduced_motion caret rests solid ON"
    # and it never reports a per-frame repaint
    assert w.on_clock_tick(0.10) is False
    assert w.on_clock_tick(0.60) is False
