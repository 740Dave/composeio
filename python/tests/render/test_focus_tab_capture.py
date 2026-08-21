"""Per-widget TAB CAPTURE ([CXC-TERM-INPUT] · [DD-TERM-TAB-CAPTURE]) — acceptance TERM-C10.

`FocusManager.handle_key` intercepts Tab and cycles focus, so the focused widget never
sees the key.  A hosted terminal cannot live with that: Claude Code's TUI uses Tab.

The opt-out follows the engine's EXISTING idiom (`focus_ring = False`, consulted with
`getattr`) rather than inventing one: a `captures_tab` attribute, default False, checked
before the Tab branch.  These tests pin both halves of TERM-C10 — Tab reaches a capturing
widget AND focus still cycles for every other widget — plus the getattr guard, because a
widget or mock that never heard of the attribute must keep the shipped behavior.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.focus import FocusManager
from composeio.render.widget import Widget


class _KeyRecorder(Widget):
    def __init__(self, wid: str, **kw) -> None:
        super().__init__(wid, "rectangle", **kw)
        self.accepts_focus = True
        self.keys: list = []

    def on_key(self, key, modifiers=None) -> None:
        self.keys.append((key, modifiers))


def test_tab_reaches_a_capturing_widget_and_focus_does_not_move():
    term = _KeyRecorder("term")
    term.captures_tab = True
    other = _KeyRecorder("other")
    fm = FocusManager()
    fm._focusables = [term, other]
    fm.focus(term)

    assert fm.handle_key("Tab") is True
    assert fm.focused is term, "focus MUST NOT move out of a capturing widget"
    assert term.keys == [("Tab", 0)]
    assert other.keys == []


def test_shift_tab_also_reaches_the_capturing_widget():
    """Shift-Tab is the backwards-cycle key; a capturing widget needs it too (the TUI
    reads it as CSI Z)."""
    term = _KeyRecorder("term")
    term.captures_tab = True
    other = _KeyRecorder("other")
    fm = FocusManager()
    fm._focusables = [term, other]
    fm.focus(term)

    assert fm.handle_key("Tab", 0x0001) is True     # KMOD_LSHIFT
    assert fm.focused is term
    assert term.keys == [("Tab", 0x0001)]


def test_every_other_widget_still_cycles():
    """The other half of TERM-C10: nothing about ordinary Tab navigation changes."""
    a = _KeyRecorder("a")
    b = _KeyRecorder("b")
    fm = FocusManager()
    fm._focusables = [a, b]

    fm.handle_key("Tab")
    assert fm.focused is a
    fm.handle_key("Tab")
    assert fm.focused is b
    fm.handle_key("Tab", 0x0001)                    # shift -> backwards
    assert fm.focused is a
    assert a.keys == [] and b.keys == [], "a non-capturing widget must NOT receive Tab"


def test_default_is_false_engine_wide():
    """`captures_tab` is opt-IN.  A plain widget does not carry it at all, and the
    getattr guard means it keeps cycling."""
    w = Widget("plain", "rectangle")
    assert getattr(w, "captures_tab", False) is False
    a = _KeyRecorder("a")
    fm = FocusManager()
    fm._focusables = [a]
    fm.focus(a)
    fm.handle_key("Tab")
    assert a.keys == []


def test_capture_off_falls_back_to_cycling():
    """Turning capture OFF at runtime restores cycling — the release path a modal input
    grab would need, without a modal state machine."""
    term = _KeyRecorder("term")
    term.captures_tab = True
    other = _KeyRecorder("other")
    fm = FocusManager()
    fm._focusables = [term, other]
    fm.focus(term)

    term.captures_tab = False
    fm.handle_key("Tab")
    assert fm.focused is other
    assert term.keys == []


def test_non_tab_keys_route_unchanged_to_the_capturing_widget():
    term = _KeyRecorder("term")
    term.captures_tab = True
    fm = FocusManager()
    fm._focusables = [term]
    fm.focus(term)
    fm.handle_key("Escape", 0)
    assert term.keys == [("Escape", 0)]
