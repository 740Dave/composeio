"""
Console PROMPT-ENTRY defects (David 2026-07-22) — the three regressions in the
composer ``text_input`` field, each pinned by a test that FAILS before its fix.

  1. PERF ([CXC-R-ENGINE-TEXT-MEASURE]) — typing near the end of a long message
     went very laggy.  The glyph-advance table was rebuilt every keystroke by
     re-measuring a GROWING prefix per character (``measure_text(shown[:i])``),
     an O(L^2)-per-keystroke / O(n^3)-per-message re-measure.  The fix builds it
     in ONE linear pass of per-glyph advances, reusing the unchanged head — and,
     because the engine text metric is ADDITIVE (no kerning/shaping), the linear
     table is PIXEL-IDENTICAL to the old per-prefix one.  Both are asserted.

  2. DOUBLE-CLICK word-select ([CXC-R-ENGINE-HIT] double-click,
     [CXC-R-ENGINE-TEXT] word-select) — double-clicking a word did nothing.
     The runtime never synthesized a double-click, and the field had no word
     selection.  Tested end-to-end through the runtime's pointer dispatch.

  3. WHEEL scroll ([CXC-R-ENGINE-TEXT-SCROLL]) — with >3 wrapped lines and focus
     in the field, the mouse wheel was dead (the field had no ``on_scroll``; an
     unconditional caret-follow would also have yanked any scroll back).  The
     wheel now moves the viewport and STICKS until the caret next moves.

Whether it FEELS smooth / LOOKS right is glass — David's eyes.  These are the
machine tier.

Run:  python3 -m pytest tests/render/test_text_input_prompt_defects.py -q
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import TextInputWidget
from composeio.render.widgets.text_input import _LINE_PITCH, _V_PAD

# [CXC-FC-UNIT] the console's collaboration surface is now N `intelligence_pane`
# instantiations, so every per-instance widget id is rooted at
# `<id_prefix>_<instance_id>_`. These ids are DERIVED from the scene (see
# `console_scene_ids`) rather than re-typed, so adding or repointing an instance
# moves the scene and these tests together.
from tests.render.console_scene_ids import (composer_field as _cf,  # noqa: E402
                                              composer_send as _cs,
                                              send_trigger_ns as _send_ns,
                                              thread as _th)

WORDS = ("the decomposed bus-peer model is why composeio exists and the "
         "console composer should wrap a real message across many lines ")


class _Ctx:
    def __init__(self):
        self.published = []
        self.scheduled = []
        self.theme = "dark"
        self.reduced_motion = False

    def request_redraw(self, rect=None):
        pass

    def publish(self, ns, value, source=""):
        self.published.append((ns, value))

    def schedule_update(self, fn, *, target=None):
        self.scheduled.append(target)

    def register_clocked(self, w):
        pass

    def unregister_clocked(self, w):
        pass


class _CountingCanvas(SkiaCanvas):
    """A real SkiaCanvas that tallies the total number of CHARACTERS passed to
    measure_text — the honest cost signal for the O(n^2)-vs-O(n) prefix build
    (the call COUNT alone hides it; the quadratic lives in the length of each
    measured string)."""

    def __init__(self):
        super().__init__()
        self.measured_chars = 0

    def measure_text(self, text: str):
        self.measured_chars += len(text)
        return super().measure_text(text)


def _field(**kwargs) -> TextInputWidget:
    w = TextInputWidget(_cf(), chrome="console", **kwargs)
    w.on_mount(_Ctx())
    return w


# ---------------------------------------------------------------------------
# 1. PERF — linear (not quadratic) per-keystroke re-measure + pixel identity
# ---------------------------------------------------------------------------

def test_typing_a_long_message_measures_linearly_not_quadratically():
    """Typing N chars one-by-one (draw after each, as the loop does) measures a
    total ~O(N) characters, NOT ~O(N^3).  The old per-prefix rebuild measured
    sum(1..L) chars EVERY keystroke — this bound (< 40*N) fails by orders of
    magnitude under that, and holds comfortably for the linear build."""
    c = _CountingCanvas()
    c.create_surface(360, 120)
    w = _field()
    rect = Rect(0, 0, 360, 68)
    w.draw(c, rect)                       # prime width + advances

    n = 120
    c.measured_chars = 0                  # count ONLY the typing session
    for i in range(n):
        w.on_text("x")
        w.draw(c, rect)

    assert len(w.text) == n
    assert c.measured_chars < 40 * n, (
        f"typing {n} chars measured {c.measured_chars} chars — "
        "quadratic/cubic re-measure, not the linear glyph-advance build")


def test_prefix_widths_stay_pixel_identical_to_a_full_remeasure():
    """The incremental per-glyph build is EXACT: the cumulative advance table
    equals a from-scratch ``measure_text(text[:i])`` per index (the engine
    metric is additive, so linearising it changes no pixel)."""
    c = SkiaCanvas()
    c.create_surface(360, 120)
    w = _field()
    rect = Rect(0, 0, 360, 68)
    w.draw(c, rect)
    # type in the middle as well as the end (exercise the common-prefix reuse
    # AND a divergence that is not at the tail)
    for ch in "hello world":
        w.on_text(ch)
        w.draw(c, rect)
    w.cursor = 5                          # caret after "hello"
    w.on_text("XY")                       # mid-string insert -> "helloXY world"
    w.draw(c, rect)

    shown = w.text
    from composeio.render.widgets import console_theme as ct
    c.set_font(ct.FONT_UI, 13, "regular")   # the exact face/size draw measured on
    ref = [0.0]
    for i in range(1, len(shown) + 1):
        ref.append(c.measure_text(shown[:i]).width)
    assert w._glyph_x == ref, "linear advance table diverged from full measure"


# ---------------------------------------------------------------------------
# 2. DOUBLE-CLICK — end-to-end word selection through the runtime
# ---------------------------------------------------------------------------

class _FakePointer:
    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = float(x), float(y)
        self.pointer_id = 0
        self.dx = self.dy = 0.0


def _double_click_app():
    config = {
        "design": {"width": 400, "height": 200},
        "namespaces": [
            {"name": "demo/msg", "state_engine": "json", "initial": ""},
        ],
        "widgets": [{
            "type": "pixel_stack", "id": "root_stack",
            "width": 400, "height": 200,
            "children": [{
                "type": "text_input", "id": _cf(),
                "x": 0, "y": 0, "w": 360, "h": 68,
                "chrome": "console",
                "namespace": "demo/msg",
            }],
        }],
    }
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    app.render_frame()
    field = next(w for w in app.comp.root.iter_widgets()
                 if w.widget_id == _cf())
    return app, field


def _word_center_xy(field, word):
    """Surface (x, y) at the middle glyph of *word* on the field's first drawn
    line, from the same advances the caret/hit-test use."""
    idx = field.text.index(word) + len(word) // 2
    s, e, origin, ly = field._line_meta[0]
    xs = field._glyph_x
    x = origin + (xs[idx] + xs[idx + 1]) / 2
    y = ly + _LINE_PITCH / 2
    return x, y


def test_double_click_selects_the_word_end_to_end():
    """Two rapid taps on a word select exactly that word (the runtime
    synthesizes on_double_click; the field word-selects)."""
    app, field = _double_click_app()
    field.text = "hello world foo"
    field.cursor = 0
    app.render_frame()                    # draw -> advances + line_meta

    x, y = _word_center_xy(field, "world")
    for _ in range(2):                    # tap, tap (fast) => double-click
        app._dispatch_pointer(_FakePointer("down", x, y))
        app._dispatch_pointer(_FakePointer("up", x, y))

    r = field._sel_range()
    assert r is not None, "double-click made no selection"
    assert field.text[r[0]:r[1]] == "world"


def test_single_tap_does_not_select_a_word():
    """A lone tap only places the caret — no word selection (guards against a
    tap being misread as a double)."""
    app, field = _double_click_app()
    field.text = "hello world foo"
    field.cursor = 0
    app.render_frame()

    x, y = _word_center_xy(field, "world")
    app._dispatch_pointer(_FakePointer("down", x, y))
    app._dispatch_pointer(_FakePointer("up", x, y))
    assert field._sel_range() is None


def test_double_click_word_bounds_on_punctuation_run():
    """A double-click grows over the CLASS run: on a word it takes the word, not
    across neighbouring spaces/punctuation."""
    w = _field()
    w.text = "foo, bar-baz qux"
    lo, hi = w._word_bounds(w.text.index("bar"))
    assert w.text[lo:hi] == "bar"         # stops at '-' (punct) and ' '
    lo, hi = w._word_bounds(w.text.index(","))
    assert w.text[lo:hi] == ","           # the punctuation run alone


# ---------------------------------------------------------------------------
# 3. WHEEL — the focused overflowing field scrolls, and the scroll STICKS
# ---------------------------------------------------------------------------

def _overflowing_field():
    """A 3-row console field whose text wraps to well beyond 3 lines."""
    w = _field(min_lines=3, max_lines=3)
    w.text = WORDS * 3
    w.cursor = len(w.text)
    c = SkiaCanvas()
    c.create_surface(360, 120)
    rect = Rect(0, 0, 360, 2 * _V_PAD + 3 * _LINE_PITCH)
    w.draw(c, rect)                        # caret at end -> pinned to bottom
    return w, c, rect


def test_wheel_up_scrolls_toward_earlier_lines_and_sticks():
    w, c, rect = _overflowing_field()
    assert w._line_count > 3
    bottom_top = w._scroll_top
    assert bottom_top == w._line_count - 3   # caret-follow pinned it to the end

    w.on_scroll(0, 1)                        # one wheel-up notch -> earlier
    assert w._scroll_top == bottom_top - 1
    assert w._user_scrolled is True

    # redraw with the caret UNMOVED: the wheel position must NOT snap back
    w.draw(c, rect)
    assert w._scroll_top == bottom_top - 1, "wheel scroll snapped back to caret"


def test_wheel_clamps_at_both_ends():
    w, c, rect = _overflowing_field()
    w.on_scroll(0, 10_000)                   # a huge wheel-up flick
    assert w._scroll_top == 0
    w.on_scroll(0, -10_000)                  # and back down
    assert w._scroll_top == w._line_count - w._n_fit


def test_moving_the_caret_re_engages_caret_follow():
    """A wheel scroll sticks only until the caret moves — typing (caret moves)
    pulls the viewport back to keep the caret visible ([CXC-R-ENGINE-TEXT])."""
    w, c, rect = _overflowing_field()
    w.on_scroll(0, 3)                        # scroll up, away from the caret
    assert w._scroll_top < w._line_count - 3
    w.on_text("Z")                           # caret moves (append at end)
    w.draw(c, rect)
    assert w._user_scrolled is False
    assert w._scroll_top == w._line_count - 3  # re-pinned to the caret line


def test_wheel_is_a_noop_when_content_fits():
    """A single-line field (nothing overflows) ignores the wheel — every
    non-scrolling field keeps its historical inert behavior."""
    w = _field(min_lines=3, max_lines=3)
    w.text = "short"
    c = SkiaCanvas()
    c.create_surface(360, 120)
    rect = Rect(0, 0, 360, 2 * _V_PAD + 3 * _LINE_PITCH)
    w.draw(c, rect)
    w.on_scroll(0, 5)
    assert w._scroll_top == 0
    assert w._user_scrolled is False
