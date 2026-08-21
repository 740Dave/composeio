"""
TextInputWidget selection + clipboard pass ([CXC-R-ENGINE-TEXT]).

Covers the editing additions layered on Phase 1 single-line editing:

1. Selection state: Shift+Left/Right/Home/End extends, a plain arrow
   collapses, Cmd/Ctrl+A selects all, typing/Backspace/Delete replace
   or remove the selection first.
2. Clipboard: Cmd/Ctrl+V pastes at the cursor (replacing the selection,
   newlines collapsed to spaces), Cmd/Ctrl+C copies the selection,
   Cmd/Ctrl+X cuts.  BOTH the macOS Cmd (KMOD_GUI) and Ctrl (KMOD_CTRL)
   chords work.  Secret fields never copy/cut.
3. Mouse selection: draw caches glyph metrics; pointer down places the
   caret, a captured pan extends the selection.
4. The FROZEN console composer send path is untouched: commit with a
   selection present still publishes the full value, clears, and arms
   the one-shot echo suppression.

All headless: the SDL clipboard is reached only through the module-level
_clipboard_get/_clipboard_set seams (lazy sdl2 import), monkeypatched
here so no test touches SDL.
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
from composeio.render.widgets import TextInputWidget
from composeio.render.widgets import text_input as ti_mod

# SDL keymod literals (mirror text_input's — the point is no sdl2 import)
SHIFT = 0x0001          # KMOD_LSHIFT
CTRL = 0x0040           # KMOD_LCTRL
GUI = 0x0400            # KMOD_LGUI (macOS Cmd)


def _canvas(w=456, h=120):
    c = SkiaCanvas()
    c.create_surface(w, h)
    return c


class _Ctx:
    def __init__(self):
        self.published = []
        self.theme = "dark"
        self.reduced_motion = False

    def request_redraw(self, rect=None):
        pass

    def publish(self, ns, value, source=""):
        self.published.append((ns, value))


def _entry(text="hello world", cursor=None, **kwargs) -> TextInputWidget:
    w = TextInputWidget("f", **kwargs)
    w.text = text
    w.cursor = len(text) if cursor is None else cursor
    return w


# ── selection state ──────────────────────────────────────────────────────────

def test_shift_left_extends_and_plain_arrow_collapses():
    w = _entry("abcdef")                       # cursor at 6
    w.on_key("Left", SHIFT)
    w.on_key("Left", SHIFT)
    assert w._sel_range() == (4, 6)
    assert w.cursor == 4
    w.on_key("Left", 0)                        # collapse to selection start
    assert w._sel_range() is None
    assert w.cursor == 4


def test_shift_right_and_collapse_right_lands_at_selection_end():
    w = _entry("abcdef", cursor=1)
    w.on_key("Right", SHIFT)
    w.on_key("Right", SHIFT)
    assert w._sel_range() == (1, 3)
    w.on_key("Right", 0)
    assert w._sel_range() is None
    assert w.cursor == 3                       # collapse to hi, not hi+1


def test_shift_home_end_select_to_boundaries():
    w = _entry("abcdef", cursor=3)
    w.on_key("Home", SHIFT)
    assert w._sel_range() == (0, 3)
    w.on_key("End", SHIFT)                     # anchor kept at 3
    assert w._sel_range() == (3, 6)
    w.on_key("Home", 0)                        # plain Home clears
    assert w._sel_range() is None and w.cursor == 0


def test_select_all_cmd_and_ctrl():
    for mod in (GUI, CTRL):
        w = _entry("hello world", cursor=3)
        w.on_key("A", mod)
        assert w._sel_range() == (0, 11)
        assert w.cursor == 11


def test_typing_replaces_selection():
    w = _entry("hello world", cursor=0)
    w.on_key("A", GUI)                         # select all
    w.on_text("x")
    assert w.text == "x"
    assert w.cursor == 1
    assert w._sel_range() is None


def test_backspace_and_delete_remove_selection():
    for key in ("Backspace", "Delete"):
        w = _entry("hello world", cursor=6)
        for _ in range(5):
            w.on_key("Right", SHIFT)           # select "world"
        w.on_key(key, 0)
        assert w.text == "hello "
        assert w.cursor == 6
        assert w._sel_range() is None


def test_escape_clears_selection_too():
    w = _entry("abc")
    w.on_key("A", CTRL)
    w.on_key("Escape", 0)
    assert w.text == "" and w.cursor == 0 and w.select_anchor is None


# ── paste ────────────────────────────────────────────────────────────────────

def test_paste_inserts_clipboard_at_cursor(monkeypatch):
    monkeypatch.setattr(ti_mod, "_clipboard_get", lambda: "PASTED")
    for mod in (GUI, CTRL):                    # Cmd+V and Ctrl+V
        w = _entry("ab", cursor=1)
        w.on_key("V", mod)
        assert w.text == "aPASTEDb"
        assert w.cursor == 7


def test_paste_replaces_selection(monkeypatch):
    monkeypatch.setattr(ti_mod, "_clipboard_get", lambda: "new")
    w = _entry("hello world", cursor=6)
    w.on_key("End", SHIFT)                     # select "world"
    w.on_key("V", GUI)
    assert w.text == "hello new"
    assert w.cursor == 9
    assert w._sel_range() is None


def test_multiline_paste_collapses_newlines_to_spaces(monkeypatch):
    monkeypatch.setattr(ti_mod, "_clipboard_get",
                        lambda: "line one\nline two\r\nline three\rend")
    w = _entry("", cursor=0)
    w.on_key("V", GUI)
    assert w.text == "line one line two line three end"
    assert w.cursor == len(w.text)


def test_empty_clipboard_paste_is_noop(monkeypatch):
    monkeypatch.setattr(ti_mod, "_clipboard_get", lambda: "")
    w = _entry("keep", cursor=2)
    w.on_key("V", GUI)
    assert w.text == "keep" and w.cursor == 2


# ── copy / cut ───────────────────────────────────────────────────────────────

def test_copy_sets_clipboard_from_selection(monkeypatch):
    got = []
    monkeypatch.setattr(ti_mod, "_clipboard_set", got.append)
    w = _entry("hello world", cursor=0)
    for _ in range(5):
        w.on_key("Right", SHIFT)               # select "hello"
    w.on_key("C", GUI)
    assert got == ["hello"]
    assert w.text == "hello world"             # copy never mutates
    assert w._sel_range() == (0, 5)            # selection survives copy


def test_copy_without_selection_is_noop(monkeypatch):
    got = []
    monkeypatch.setattr(ti_mod, "_clipboard_set", got.append)
    w = _entry("hello")
    w.on_key("C", CTRL)
    assert got == []


def test_cut_copies_then_deletes(monkeypatch):
    got = []
    monkeypatch.setattr(ti_mod, "_clipboard_set", got.append)
    w = _entry("hello world", cursor=5)
    w.on_key("End", SHIFT)                     # select " world"
    w.on_key("X", CTRL)
    assert got == [" world"]
    assert w.text == "hello"
    assert w.cursor == 5
    assert w._sel_range() is None


def test_secret_field_never_copies_or_cuts(monkeypatch):
    got = []
    monkeypatch.setattr(ti_mod, "_clipboard_set", got.append)
    w = _entry("hunter2", secret=True)
    w.on_key("A", GUI)
    w.on_key("C", GUI)
    w.on_key("X", GUI)
    assert got == []
    assert w.text == "hunter2"                 # cut refused: text intact


def test_clipboard_get_headless_degrades_to_empty(monkeypatch):
    """The real seam (not a mock): with sdl2 unimportable it returns ''."""
    import builtins
    real_import = builtins.__import__

    def no_sdl(name, *a, **k):
        if name == "sdl2":
            raise ImportError("headless")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_sdl)
    monkeypatch.delitem(sys.modules, "sdl2", raising=False)
    assert ti_mod._clipboard_get() == ""
    ti_mod._clipboard_set("x")                 # must not raise


# ── selection rendering + mouse selection ────────────────────────────────────

def test_draw_caches_glyph_metrics_and_renders_selection():
    for chrome in ("", "console"):
        w = _entry("hello", chrome=chrome)
        w.on_key("A", GUI)
        c = _canvas()
        w.draw(c, Rect(8, 8, 300, 36))         # must not raise with selection
        assert len(w._glyph_x) == 6            # len+1 prefix widths
        assert w._glyph_x[0] == 0.0
        assert w._glyph_x[-1] > 0.0


def test_pointer_down_places_caret_and_pan_extends_selection():
    w = _entry("hello world", chrome="console")
    c = _canvas()
    w.draw(c, Rect(8, 8, 300, 36))             # cache metrics
    origin = w._text_origin
    end_x = origin + w._glyph_x[-1]

    w.on_pointer_down(origin, 20)              # click at the very start
    assert w.cursor == 0
    assert w._sel_range() is None
    w.on_pan(end_x - origin, 0)                # drag to the very end
    assert w._sel_range() == (0, 11)
    assert w.cursor == 11


def test_pointer_before_any_draw_is_a_safe_noop():
    w = _entry("hello", cursor=2)
    w.on_pointer_down(50, 10)                  # no metrics yet
    w.on_pan(30, 0)
    assert w.cursor == 2
    assert w._sel_range() is None


# ── FROZEN send path: commit unaffected by selection ─────────────────────────

def test_commit_with_selection_publishes_full_value_and_clears():
    w = TextInputWidget("composer",
                        namespace="intelligence/architect/compose",
                        chrome="console", clear_on_commit=True)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.text = "ship it"
    w.cursor = 7
    w.on_key("A", GUI)                         # selection present at commit
    w.on_key("Return", 0)
    assert ctx.published == [("intelligence/architect/compose", "ship it")]
    assert w.text == "" and w.cursor == 0 and w.select_anchor is None
    assert w._suppress_echo == "ship it"       # one-shot echo suppression armed
    w.on_bus_state("intelligence/architect/compose", "ship it")
    assert w.text == ""                        # echo swallowed exactly once
