"""
Per-widget focus-ring opt-out ([CXC-R-ENGINE-FOCUS]).

The engine draws a blue focus ring around the focused widget as an
accessibility affordance.  A widget whose own chrome already signals focus
(the console composer's blinking accent caret) opts OUT by setting
``focus_ring = False`` — focus itself (Tab navigation, keyboard routing) is
unchanged; only the RING DRAW is suppressed.

These tests are the real gate: a static ``--headless`` render never focuses a
widget, so it can't exercise the ring either way.  They drive the ring draw
directly against a recording canvas (canvas-contract only, no skia) and assert
the stroke is emitted for a normal focused widget but SKIPPED for one that
opted out — and that the console composer opts out after scene parsing while a
plain text_input keeps the ring.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.focus import FocusManager
from composeio.render.engine.parser import parse_widget_tree
from composeio.render.widget import Rect, Widget


class _RecordingCanvas:
    """Minimal canvas-contract recorder: logs the drawing calls the focus
    ring emits.  No skia — just enough surface for draw_focus_ring."""

    def __init__(self) -> None:
        self.calls: list = []

    def save(self) -> None:
        self.calls.append(("save",))

    def restore(self) -> None:
        self.calls.append(("restore",))

    def set_stroke_color(self, *rgba) -> None:
        self.calls.append(("set_stroke_color", rgba))

    def set_stroke_width(self, w) -> None:
        self.calls.append(("set_stroke_width", w))

    def stroke_round_rect(self, x, y, w, h, r) -> None:
        self.calls.append(("stroke_round_rect", x, y, w, h, r))

    def _stroked(self) -> bool:
        return any(c[0] == "stroke_round_rect" for c in self.calls)


def _focused(**attrs) -> tuple:
    """A focus manager holding a single focused widget with an arranged rect."""
    w = Widget("w1", "rectangle")
    w.accepts_focus = True
    w.arranged_rect = Rect(20, 20, 100, 40)
    for k, v in attrs.items():
        setattr(w, k, v)
    fm = FocusManager()
    fm.focus(w)
    return fm, w


def test_ring_drawn_for_normal_focused_widget():
    fm, w = _focused()
    assert w.focus_ring is True          # default affordance intact
    c = _RecordingCanvas()
    fm.draw_focus_ring(c)
    assert c._stroked(), "normal focused widget must get the ring"


def test_ring_skipped_when_widget_opts_out():
    fm, w = _focused(focus_ring=False)
    c = _RecordingCanvas()
    fm.draw_focus_ring(c)
    assert not c._stroked(), "opted-out widget must NOT be stroked"
    assert c.calls == [], "no ring drawing calls at all for the opt-out"


def test_ring_getattr_guarded_for_widget_without_attr():
    """A widget/mock lacking focus_ring entirely keeps the default ring."""
    fm, w = _focused()
    del w.focus_ring
    c = _RecordingCanvas()
    fm.draw_focus_ring(c)
    assert c._stroked()


def _find(comp, wid):
    for w in comp.root.iter_widgets():
        if getattr(w, "widget_id", None) == wid:
            return w
    return None


def test_console_text_input_opts_out_after_parse():
    """chrome: console text_input defaults focus_ring == False (the caret is
    the focus signal), while a plain text_input keeps the ring."""
    comp = parse_widget_tree({"widgets": [
        {"type": "text_input", "id": "composer", "chrome": "console"},
        {"type": "text_input", "id": "plain"},
    ]})
    console = _find(comp, "composer")
    plain = _find(comp, "plain")
    assert console is not None and plain is not None
    assert console.focus_ring is False, "console composer must opt out"
    assert plain.focus_ring is True, "plain text_input keeps the ring"

    # and the manager actually skips only the opted-out one
    console.arranged_rect = Rect(0, 0, 100, 30)
    plain.arranged_rect = Rect(0, 0, 100, 30)

    fm = FocusManager()
    fm.focus(console)
    cc = _RecordingCanvas()
    fm.draw_focus_ring(cc)
    assert not cc._stroked()

    fm.focus(plain)
    pc = _RecordingCanvas()
    fm.draw_focus_ring(pc)
    assert pc._stroked()


def test_explicit_scene_key_overrides_chrome_default():
    """An explicit `focus_ring:` scene key wins over the chrome default in
    both directions (re-enable on console; disable on a plain widget)."""
    comp = parse_widget_tree({"widgets": [
        {"type": "text_input", "id": "c_on",
         "chrome": "console", "focus_ring": True},
        {"type": "text_input", "id": "p_off", "focus_ring": False},
    ]})
    assert _find(comp, "c_on").focus_ring is True   # explicit re-enable wins
    assert _find(comp, "p_off").focus_ring is False  # explicit opt-out on plain


def test_focus_navigation_untouched_by_optout():
    """Opting out of the RING must not affect focus itself: an opted-out
    widget still receives focus and Tab still cycles onto it."""
    a = Widget("a", "text_input")
    a.accepts_focus = True
    a.focus_ring = False
    b = Widget("b", "rectangle")
    b.accepts_focus = True

    fm = FocusManager()
    fm._focusables = [a, b]
    fm.handle_key("Tab")
    assert fm.focused is a               # opted-out widget still focusable
    assert a.focused is True
    fm.handle_key("Tab")
    assert fm.focused is b
