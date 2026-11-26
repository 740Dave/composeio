"""
ChatThreadWidget text selection + copy ([CXC-ICV-SELECT]).

The thread is a READ surface: David can select rendered message text and
copy it (Cmd/Ctrl+C) — no caret, no editing.  These tests pin the behaviors
static reading can't prove:

1. Pixel -> char mapping lands on the expected character, hit-testing against
   the SAME per-line glyph advances the paint path recorded during draw
   (_sel_lines) — one measurement path, like text_input's _prefix_widths.
2. A selection range yields the expected rendered-plain substring, within a
   line, across soft-wrapped lines (rejoined with a space), and across message
   bubbles (rejoined with a newline).
3. Cmd/Ctrl+C sets that substring on a fake clipboard; a bare thread copies
   nothing.
4. Selection resets sensibly: a new turn (epoch change) drops it at draw; a
   wheel scroll drops it.
5. The selection highlight band paints (accent_line fill) behind the glyphs.

All headless.  The clipboard is reached ONLY through the module-level
_clipboard_set seam (imported by-reference from text_input); it is patched on
the chat_thread module — the name the copy path actually calls — so no test
touches SDL.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import ChatThreadWidget
from composeio.render.widgets import chat_thread as chat_mod
from composeio.render.widgets import console_theme as ct

# SDL keymod literals (mirror the widget's — the point is no sdl2 import)
SHIFT = 0x0001          # KMOD_LSHIFT
CTRL = 0x0040           # KMOD_LCTRL
GUI = 0x0400            # KMOD_LGUI (macOS Cmd)


def _canvas(w=456, h=356):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x16)
    c.fill_rect(0, 0, w, h)
    return c


def _reply(w, text):
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect", "text": text})


def _david(w, text):
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": text})


def _line_with(w, text):
    """The recorded _SelLine whose drawn text equals *text*."""
    for sl in w._sel_lines:
        if sl.text == text:
            return sl
    raise AssertionError(f"no drawn line == {text!r}; got "
                         f"{[s.text for s in w._sel_lines]}")


def _mid_y(sl):
    return (sl.y_top + sl.y_bot) * 0.5


# ── pixel -> char mapping ─────────────────────────────────────────────────────

def test_index_at_lands_on_the_expected_character():
    w = ChatThreadWidget("t")
    _reply(w, "hello world")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    sl = _line_with(w, "hello world")
    # aim exactly at the char-6 boundary -> boundary 6 ("w" of world)
    pos = w._index_at(sl.bounds[6], _mid_y(sl))
    assert pos == (sl.msg_idx, sl.line_idx, 6)
    # a click just left of the start snaps to boundary 0
    assert w._index_at(sl.bounds[0] - 50, _mid_y(sl))[2] == 0
    # a click past the end snaps to the last boundary
    assert w._index_at(sl.bounds[-1] + 50, _mid_y(sl))[2] == len(sl.text)


def test_index_at_picks_the_line_by_y():
    w = ChatThreadWidget("t")
    _reply(w, "alpha")
    _david(w, "omega")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    la, lo = _line_with(w, "alpha"), _line_with(w, "omega")
    assert w._index_at(la.bounds[0], _mid_y(la))[:2] == (la.msg_idx, la.line_idx)
    assert w._index_at(lo.bounds[0], _mid_y(lo))[:2] == (lo.msg_idx, lo.line_idx)


def test_pointer_before_any_draw_is_a_safe_noop():
    w = ChatThreadWidget("t")
    _reply(w, "never drawn")
    w.on_pointer_down(50, 50)          # no _sel_lines yet
    w.on_pan(30, 0)
    assert w._sel_anchor is None
    assert w._selected_text() == ""


# ── selection range -> substring ──────────────────────────────────────────────

def test_within_line_selection_yields_substring():
    w = ChatThreadWidget("t")
    _reply(w, "hello world")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    sl = _line_with(w, "hello world")
    w._sel_anchor = (sl.msg_idx, sl.line_idx, 0)
    w._sel_focus = (sl.msg_idx, sl.line_idx, 5)
    w._sel_epoch = w._epoch()
    assert w._selected_text() == "hello"


def test_drag_selects_across_the_line():
    w = ChatThreadWidget("t")
    _reply(w, "hello world")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    sl = _line_with(w, "hello world")
    y = _mid_y(sl)
    w.on_pointer_down(sl.bounds[0], y)         # anchor at start
    assert w._sel_ordered() is None            # zero-width caret
    w.on_pan(sl.bounds[5] - sl.bounds[0], 0)   # drag to boundary 5
    assert w._selected_text() == "hello"
    # extend to the end
    w.on_pan(sl.bounds[-1] - sl.bounds[0], 0)
    assert w._selected_text() == "hello world"


def test_soft_wrapped_lines_rejoin_with_a_space():
    w = ChatThreadWidget("t")
    _reply(w, "the quick brown fox jumps over the lazy dog again and again")
    c = _canvas()
    w.draw(c, Rect(0, 0, 180, 356))            # narrow -> forces a wrap
    lines = [s for s in w._sel_lines if s.msg_idx == 0 and s.text]
    assert len(lines) >= 2, "expected the reply to wrap"
    l0, l1 = lines[0], lines[1]
    assert l0.para_start and not l1.para_start  # a soft-wrap continuation
    w._sel_anchor = (0, l0.line_idx, len(l0.text) - 3)
    w._sel_focus = (0, l1.line_idx, 2)
    w._sel_epoch = w._epoch()
    assert w._selected_text() == l0.text[-3:] + " " + l1.text[:2]


def test_cross_bubble_selection_rejoins_with_a_newline():
    w = ChatThreadWidget("t")
    _reply(w, "alpha")
    _david(w, "beta")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    la, lb = _line_with(w, "alpha"), _line_with(w, "beta")
    w._sel_anchor = (la.msg_idx, la.line_idx, 0)
    w._sel_focus = (lb.msg_idx, lb.line_idx, len("beta"))
    w._sel_epoch = w._epoch()
    assert la.msg_idx != lb.msg_idx            # genuinely different bubbles
    assert w._selected_text() == "alpha\nbeta"


def test_paragraph_break_rejoins_with_a_newline():
    w = ChatThreadWidget("t")
    _reply(w, "line one\nline two")            # explicit hard newline
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    l0, l1 = _line_with(w, "line one"), _line_with(w, "line two")
    w._sel_anchor = (0, l0.line_idx, 0)
    w._sel_focus = (0, l1.line_idx, len("line two"))
    w._sel_epoch = w._epoch()
    assert l1.para_start                       # second paragraph
    assert w._selected_text() == "line one\nline two"


def test_markdown_markers_are_absent_from_the_copy():
    # the tokenizer strips ** / ` before draw, so copy is the PLAIN rendered
    # text — never the source markers
    w = ChatThreadWidget("t")
    _reply(w, "cycle **fleetnode2** then `power_status`")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    w._select_all()
    got = w._selected_text()
    assert "**" not in got and "`" not in got
    assert "fleetnode2" in got and "power_status" in got


# ── clipboard (Cmd/Ctrl+C) ────────────────────────────────────────────────────

def test_copy_sets_clipboard_from_selection(monkeypatch):
    for mod in (GUI, CTRL):
        got = []
        monkeypatch.setattr(chat_mod, "_clipboard_set", got.append)
        w = ChatThreadWidget("t")
        _reply(w, "copy me please")
        c = _canvas()
        w.draw(c, Rect(0, 0, 456, 356))
        sl = _line_with(w, "copy me please")
        w._sel_anchor = (sl.msg_idx, sl.line_idx, 0)
        w._sel_focus = (sl.msg_idx, sl.line_idx, 7)
        w._sel_epoch = w._epoch()
        w.on_key("C", mod)
        assert got == ["copy me"]
        # copy never mutates the selection
        assert w._selected_text() == "copy me"


def test_copy_without_selection_is_noop(monkeypatch):
    got = []
    monkeypatch.setattr(chat_mod, "_clipboard_set", got.append)
    w = ChatThreadWidget("t")
    _reply(w, "nothing selected")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    w.on_key("C", GUI)
    assert got == []


def test_select_all_then_copy_spans_the_drawn_thread(monkeypatch):
    got = []
    monkeypatch.setattr(chat_mod, "_clipboard_set", got.append)
    w = ChatThreadWidget("t")
    _reply(w, "alpha")
    _david(w, "beta")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    w.on_key("A", GUI)                         # select all
    w.on_key("C", GUI)
    assert got == ["alpha\nbeta"]


def test_escape_clears_the_selection():
    w = ChatThreadWidget("t")
    _reply(w, "hello world")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    sl = _line_with(w, "hello world")
    w._sel_anchor = (sl.msg_idx, sl.line_idx, 0)
    w._sel_focus = (sl.msg_idx, sl.line_idx, 5)
    w._sel_epoch = w._epoch()
    w.on_key("Escape", 0)
    assert w._sel_anchor is None
    assert w._selected_text() == ""


# ── reset semantics ───────────────────────────────────────────────────────────

def test_new_turn_drops_the_selection_at_draw():
    w = ChatThreadWidget("t")
    _reply(w, "first")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    sl = _line_with(w, "first")
    w._sel_anchor = (sl.msg_idx, sl.line_idx, 0)
    w._sel_focus = (sl.msg_idx, sl.line_idx, 3)
    w._sel_epoch = w._epoch()
    _david(w, "a brand new turn arrives")      # epoch changes
    w.draw(c, Rect(0, 0, 456, 356))            # reconciliation runs here
    assert w._sel_anchor is None


def test_scroll_drops_the_selection():
    w = ChatThreadWidget("t", max_messages=60)
    for i in range(40):
        _reply(w, f"message number {i} carrying enough text to overflow it")
    c = _canvas()
    w.draw(c, Rect(0, 0, 300, 200))            # small -> content overflows
    assert w._max_scroll > 0.0, "expected the thread to overflow"
    w._sel_anchor = (0, 0, 0)
    w._sel_focus = (0, 0, 3)
    w._sel_epoch = w._epoch()
    w.on_scroll(0, 1)                          # wheel up -> view moves
    assert w._sel_anchor is None


# ── highlight render ──────────────────────────────────────────────────────────

def test_selection_highlight_band_paints_accent_line():
    w = ChatThreadWidget("t")
    _reply(w, "highlight this text")
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))            # first draw records _sel_lines
    sl = _line_with(w, "highlight this text")
    w._sel_anchor = (sl.msg_idx, sl.line_idx, 0)
    w._sel_focus = (sl.msg_idx, sl.line_idx, 9)   # "highlight"
    w._sel_epoch = w._epoch()

    t = ct.CONSOLE.active
    bands = []
    cur = {"fill": None}
    real_sf, real_fr = c.set_fill_color, c.fill_rect

    def set_fill(*a):
        cur["fill"] = tuple(a)
        return real_sf(*a)

    def fill_rect(x, y, ww, hh):
        if cur["fill"] == tuple(t.accent_line):
            bands.append((x, ww))
        return real_fr(x, y, ww, hh)

    c.set_fill_color, c.fill_rect = set_fill, fill_rect
    w.draw(c, Rect(0, 0, 456, 356))            # redraw with the selection live
    assert bands, "the selection band (accent_line fill_rect) must paint"
    # its width matches the selected glyph span from the recorded advances
    sl2 = _line_with(w, "highlight this text")
    expect_w = sl2.bounds[9] - sl2.bounds[0]
    assert any(abs(bw - expect_w) < 0.5 for _x, bw in bands)


def test_thread_is_focusable_read_surface():
    # focusable so Cmd/Ctrl+C routes here (focus-on-tap), but no focus ring —
    # the selection band is the affordance
    w = ChatThreadWidget("t")
    assert w.accepts_focus is True
    assert w.focus_ring is False
    assert w.interactive is True
