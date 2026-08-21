"""
TextInputWidget multi-line console composer ([CXC-R-ENGINE-TEXT] soft wrap).

The console composer grows from 1 to max_lines (default 3) visible lines as
its text soft-wraps against the field width, then STOPS growing and scrolls
vertically to keep the caret line visible.  Chat semantics are preserved:
Enter still COMMITS, there is no newline key, and the field never contains
hard newlines.  These pin:

  * the wrap itself — computed on the SAME cached glyph advances that place
    the caret and hit-test the mouse (one measurement path, [CXC-R-DRAW-FONT]);
  * measure(): height follows the wrapped line count, capped at max_lines;
  * end-to-end growth through the real console scene (RenderApp) with the
    damage SCOPED — a growth keystroke damages the field's old-UNION-new
    rect, never the whole surface ([CXC-R-DAMAGE], commit 1e3f946);
  * the caret sliver (_caret_rect) staying scoped AND line-aware — its y
    follows the caret's current line, honoring vertical scroll;
  * commit-time emoji substitution (chat composers only).

Whether the wrapped field LOOKS right and typing FEELS smooth is glass-only —
David's eyes decide that; these tests are the machine tier.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# [DEC-CONSOLE-CHAT-ID-PARAM] the chat topics DERIVE from the scene's one
# chat_id line — see console_scene_ids for why hardcoding them is wrong.
from composeio.render.engine.damage import AA_MARGIN, DamageRegion
from composeio.render.engine.runtime import RenderApp
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import TextInputWidget
from composeio.render.widgets.text_input import (_CARET_H, _KMOD_SHIFT,

                                                 _LINE_PITCH, _V_PAD, emojify)
from tests.render.console_scene_ids import (  # noqa: E402
    chat_ns,
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

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")

# comfortably wraps a ~300px-wide field to 2-3 lines, a ~800px one too when
# repeated — plain words, no emoji codes
WORDS = ("the decomposed bus-peer model is why composeio exists and the "
         "console composer should wrap a real message across lines ")


def _canvas(w=360, h=120):
    c = SkiaCanvas()
    c.create_surface(w, h)
    return c


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


def _field(**kwargs) -> TextInputWidget:
    w = TextInputWidget(_cf(), chrome="console", **kwargs)
    w.on_mount(_Ctx())
    return w


def _find(root, wid):
    for w in root.iter_widgets():
        if w.widget_id == wid:
            return w
    raise AssertionError(f"widget {wid} not in tree")


# ---------------------------------------------------------------------------
# wrap: same advances as the caret, contiguous cover, width-bounded
# ---------------------------------------------------------------------------

def test_wrap_covers_text_contiguously_and_fits_width():
    w = _field()
    w.text = WORDS
    w.cursor = len(w.text)
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))

    lines = w._lines
    xs = w._glyph_x
    assert len(lines) >= 2, "long text must soft-wrap to multiple lines"
    # contiguous cover of the whole string
    assert lines[0][0] == 0 and lines[-1][1] == len(w.text)
    for (a, b), (c2, _d) in zip(lines, lines[1:]):
        assert b == c2
    # every line fits the avail width ON THE SAME ADVANCES the caret uses
    avail = w._wrap_avail
    for s, e in lines:
        assert xs[e] - xs[s] <= avail + 0.01, f"line ({s},{e}) overflows"


def test_wrap_breaks_at_word_boundaries_when_possible():
    w = _field()
    w.text = WORDS
    w.cursor = 0
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))
    # every non-final line of a spaced text ends right after a space
    for s, e in w._lines[:-1]:
        assert w.text[e - 1] == " ", f"line ({s},{e}) broke mid-word"


def test_single_line_field_measures_32px_and_grows_to_cap():
    from composeio.render.widget import Constraints
    w = _field()
    c = _canvas()

    w.draw(c, Rect(0, 0, 360, 32))         # establishes width + advances
    assert w.measure(Constraints(360, 500)).height == 2 * _V_PAD + _LINE_PITCH

    w.text = WORDS * 3                     # far beyond 3 lines at 360px
    w.cursor = len(w.text)
    w._recompute_wrap()
    assert w._line_count > 3
    # measured height CAPS at max_lines (grow-then-scroll)
    assert w.measure(Constraints(360, 500)).height == \
        2 * _V_PAD + 3 * _LINE_PITCH


def test_height_change_schedules_targeted_growth_update():
    """An edit that changes the wrapped height enqueues the no-op update
    TARGETED at the widget — the runtime's old-UNION-new growth path — and
    a same-height edit does not."""
    w = _field()
    ctx = w._ctx
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 32))

    w.on_text("hi")                        # stays 1 line
    assert ctx.scheduled == []
    w.on_text(WORDS)                       # wraps -> height change
    assert ctx.scheduled == [w], "growth must ride schedule_update(target=w)"


# ---------------------------------------------------------------------------
# caret: line-aware placement + scoped sliver + scroll keeps it visible
# ---------------------------------------------------------------------------

def test_caret_rect_follows_the_caret_line():
    w = _field()
    w.text = WORDS
    c = _canvas()
    rect = Rect(0, 0, 360, 68)

    w.cursor = 0                           # first wrapped line
    w.draw(c, rect)
    y_first = w._caret_rect.y
    assert abs(y_first - (_V_PAD + (_LINE_PITCH - _CARET_H) / 2)) < 0.01

    w.cursor = len(w.text)                 # last wrapped line
    w.draw(c, rect)
    y_last = w._caret_rect.y
    line_of_last = w._line_of(w.cursor) - w._scroll_top
    assert y_last == y_first + line_of_last * _LINE_PITCH
    assert y_last > y_first

    # the sliver stays a SLIVER (scoped blink damage, [CXC-R-DAMAGE])
    assert w.clock_damage_rect() is w._caret_rect
    assert w._caret_rect.w <= 4 and w._caret_rect.h <= _CARET_H
    # and it lies INSIDE the field box even on a lower line
    assert w._caret_rect.y + w._caret_rect.h <= rect.y + rect.h


def test_beyond_cap_scrolls_to_keep_caret_visible_and_clips():
    w = _field()
    w.text = WORDS * 3
    w.cursor = len(w.text)
    c = _canvas()
    rect = Rect(0, 0, 360, 68)             # 3-line viewport
    w.draw(c, rect)

    assert w._line_count > 3
    assert w._scroll_top == w._line_count - 3, \
        "caret at end -> viewport pinned to the last 3 lines"
    # only the visible window carries draw records
    assert len(w._line_meta) == 3
    # every drawn line lies inside the field box (the clip viewport)
    for _s, _e, _ox, ly in w._line_meta:
        assert rect.y <= ly and ly + _LINE_PITCH <= rect.y + rect.h + 0.01
    # caret on the BOTTOM visible line
    assert abs(w._caret_rect.y
               - (rect.y + _V_PAD + 2 * _LINE_PITCH
                  + (_LINE_PITCH - _CARET_H) / 2)) < 0.01

    # caret back at the top of the text -> viewport follows up to line 0
    w.cursor = 0
    w.draw(c, rect)
    assert w._scroll_top == 0
    assert abs(w._caret_rect.y - (rect.y + _V_PAD
                                  + (_LINE_PITCH - _CARET_H) / 2)) < 0.01


def test_click_places_caret_on_the_clicked_line():
    w = _field()
    w.text = WORDS
    w.cursor = 0
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))
    assert len(w._line_meta) >= 2

    s1, _e1, ox1, ly1 = w._line_meta[1]    # second visible line
    w.on_pointer_down(ox1, ly1 + _LINE_PITCH / 2)   # click at ITS start
    assert w.cursor == s1, "click on line 2 must land on line 2's glyphs"

    # drag down-right extends the selection across lines
    _s0, e0, ox0, ly0 = w._line_meta[0]
    w.on_pointer_down(ox0, ly0 + _LINE_PITCH / 2)
    assert w.cursor == 0
    w.on_pan(ox1 + 40 - ox0, ly1 - ly0)
    lo, hi = w._sel_range()
    assert lo == 0 and hi > s1, "drag across lines must select across them"


# ---------------------------------------------------------------------------
# end-to-end through the REAL console scene (growth + scoped damage)
# ---------------------------------------------------------------------------

def test_console_scene_field_rests_at_3_rows_then_scrolls():
    """The composer now RESTS at 3 rows (min_lines: 3 fills the fixed panel)
    and SCROLLS past 3 wrapped lines — no growth phase, height steady at 68."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        field = _find(app.comp.root, _cf())
        composer = _find(app.comp.root, _composer())
        thread = _find(app.comp.root, _th())
        assert field.max_lines == 3        # scene prop reached the widget
        assert field.min_lines == 3        # rests at 3 rows

        app.render_frame()                 # steady state
        rest_h = 2 * _V_PAD + 3 * _LINE_PITCH
        assert field.arranged_rect.h == rest_h     # resting: THREE rows (68px)
        bottom0 = field.arranged_rect.y + field.arranged_rect.h
        thread_rect0 = thread.arranged_rect

        app.focus.focus(field)
        long_msg = WORDS * 4               # >3 lines even at ~850px width
        app.focus.handle_text(long_msg)
        app.render_frame()

        r = field.arranged_rect
        assert r.h == rest_h, "field holds at 3 visible lines and scrolls"
        assert field._line_count > 3
        assert field._scroll_top == field._line_count - 3
        # steady rect inside the fixed composer panel: bottom edge pinned,
        # siblings (thread, composer) never move
        assert abs((r.y + r.h) - bottom0) < 0.01
        assert composer.arranged_rect.h == 84
        assert thread.arranged_rect == thread_rect0
        # scrollbar geometry engages once content overflows the 3 rows
        assert field._line_count > 3
        # caret sliver: line-aware, inside the field, still a sliver
        cr = field._caret_rect
        assert r.x <= cr.x and cr.x + cr.w <= r.x + r.w
        assert r.y <= cr.y and cr.y + cr.h <= r.y + r.h
        assert cr.w <= 4

        # Enter still SENDS ([CXC-ICV-CHROME] frozen wiring): publishes the
        # full soft-wrapped text (no newlines) and snaps back to the rest 3 rows
        field.on_key("Return", 0)
        app.drain_updates()
        sent = app.bus.get_state(chat_ns("compose"))
        assert sent == long_msg and "\n" not in sent
        assert field.text == ""
        app.render_frame()
        assert field.arranged_rect.h == rest_h
    finally:
        app.stop()


def test_growth_keystroke_damage_is_scoped_to_the_field():
    """The growth frame ([CXC-R-DAMAGE]): a keystroke that wraps the field
    to more lines damages the field's old-UNION-new region — contained in
    the (fixed) composer panel — and NEVER escalates to a full redraw."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        field = _find(app.comp.root, _cf())
        composer = _find(app.comp.root, _composer())
        # The scoped-growth path is a min<max property (the console composer
        # pins min==max==3 so its rect is steady).  Force a real growth phase
        # by resting this field at one line, exactly the mechanism min<max
        # fields still use.
        field.min_lines = 1
        app.render_frame()                 # steady state (1 line), damage cleared
        app.focus.focus(field)
        app._damage = DamageRegion()
        app._dirty = False

        old = Rect(field.arranged_rect.x, field.arranged_rect.y,
                   field.arranged_rect.w, field.arranged_rect.h)
        # the engine's text path damages the focused field, then the edit
        # itself schedules the targeted growth update
        app.windowed = True
        class _TextEvent:
            type = "text"
            text = WORDS * 2               # wraps even the ~820px-wide field
        app._handle_key(_TextEvent())
        app.windowed = False

        app.drain_updates()
        app._layout()
        app._apply_drained_damage()

        new = field.arranged_rect
        assert new.h > old.h, "keystroke should have grown the field"
        assert not app._damage.is_full, \
            "growth keystroke must not dirty the whole scene"
        b = app._damage.bounds()
        # damage covers the union (here: the grown rect contains the old one)
        assert b.x <= new.x + 0.01 and b.y <= new.y + 0.01
        assert b.x + b.w >= new.x + new.w - 0.01
        assert b.y + b.h >= new.y + new.h - 0.01
        # ... and stays inside the fixed composer panel (+AA margin)
        cp = composer.arranged_rect
        assert b.x >= cp.x - AA_MARGIN - 0.01
        assert b.y >= cp.y - AA_MARGIN - 0.01
        assert b.x + b.w <= cp.x + cp.w + AA_MARGIN + 0.01
        assert b.y + b.h <= cp.y + cp.h + AA_MARGIN + 0.01
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# commit-time emoji substitution (chat composers only)
# ---------------------------------------------------------------------------

def test_emojify_maps_the_five_codes():
    assert emojify(":) :D <3 :thumbsup: :fire:") == \
        "\U0001F642 \U0001F604 ❤️ \U0001F44D \U0001F525"
    assert emojify("no codes here.") == "no codes here."


def test_chat_commit_applies_emoji_form_commit_does_not():
    chat = TextInputWidget("c", namespace="intelligence/architect/compose",
                           chrome="console", clear_on_commit=True)
    ctx = _Ctx()
    chat.on_mount(ctx)
    chat.text = "ship it :) :fire:"
    chat.cursor = len(chat.text)
    chat.on_key("Return", 0)
    assert ctx.published == [("intelligence/architect/compose",
                              "ship it \U0001F642 \U0001F525")]
    # echo suppression armed with the SUBSTITUTED string (what was published)
    assert chat._suppress_echo == "ship it \U0001F642 \U0001F525"
    assert chat.text == ""

    form = TextInputWidget("f", namespace="settings/name")
    fctx = _Ctx()
    form.on_mount(fctx)
    form.text = "host:)name"
    form.cursor = len(form.text)
    form.commit()
    assert fctx.published == [("settings/name", "host:)name")], \
        "form fields must never emojify"


# ---------------------------------------------------------------------------
# Up/Down vertical caret navigation (same-measurement-path landing)
# ---------------------------------------------------------------------------

def test_up_down_caret_moves_between_wrapped_lines_keeping_x():
    w = _field()
    w.text = WORDS
    w.cursor = len(w.text)
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))
    lines = w._lines
    xs = w._glyph_x
    assert len(lines) >= 3, "WORDS wraps to several lines at 360px"

    # caret mid-way along line 1
    s1, e1 = lines[1]
    w.cursor = (s1 + e1) // 2
    x_off = xs[w.cursor] - xs[s1]

    # Up -> line 0, at the NEAREST boundary to the preserved x (argmin on the
    # same advances) — never further from x than a neighboring boundary
    w.on_key("Up", 0)
    assert w._line_of(w.cursor) == 0
    s0 = lines[0][0]
    up_xoff = xs[w.cursor] - xs[s0]
    i = w.cursor
    for j in range(lines[0][0], lines[0][1] + 1):
        assert abs(up_xoff - x_off) <= abs((xs[j] - xs[s0]) - x_off) + 1e-9

    # Down -> back onto line 1
    w.on_key("Down", 0)
    assert w._line_of(w.cursor) == 1


def test_up_at_top_and_down_at_bottom_go_to_line_edges():
    w = _field()
    w.text = WORDS
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))

    w.cursor = 3                            # somewhere on the first line
    w.on_key("Up", 0)
    assert w.cursor == 0, "Up on the top line jumps to line start (Home)"

    w.cursor = len(w.text) - 3             # on the last line
    w.on_key("Down", 0)
    assert w.cursor == len(w.text), "Down on the last line jumps to End"


def test_shift_up_down_extends_selection():
    w = _field()
    w.text = WORDS
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))
    assert len(w._lines) >= 2

    s1 = w._lines[1][0]
    w.cursor = s1 + 2
    w.select_anchor = None
    w.on_key("Up", _KMOD_SHIFT)
    assert w.select_anchor == s1 + 2, "Shift+Up anchors at the start point"
    assert w._sel_range() is not None, "Shift+Up builds a selection"
    # plain Down collapses it
    w.on_key("Down", 0)
    assert w.select_anchor is None


# ---------------------------------------------------------------------------
# 3-row resting height (min_lines) + generic default unchanged
# ---------------------------------------------------------------------------

def test_min_lines_three_rests_at_three_rows():
    from composeio.render.widget import Constraints
    w = _field(min_lines=3)
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))         # empty field
    assert w._visible_lines() == 3
    rest = 2 * _V_PAD + 3 * _LINE_PITCH
    assert w.measure(Constraints(360, 500)).height == rest

    # one short (single-wrapped) line still rests at 3 rows
    w.text = "hi there"
    w.cursor = len(w.text)
    w._recompute_wrap()
    assert w._line_count == 1
    assert w.measure(Constraints(360, 500)).height == rest


def test_default_min_lines_one_still_rests_at_one_line():
    """The generic auto-grow default (min_lines: 1) is unchanged — every
    existing scene/golden that never sets min_lines rests at one line."""
    from composeio.render.widget import Constraints
    w = _field()                           # no min_lines -> default 1
    assert w.min_lines == 1
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 32))
    assert w.measure(Constraints(360, 500)).height == 2 * _V_PAD + _LINE_PITCH


def test_min_lines_clamped_to_max():
    w = _field(min_lines=9, max_lines=3)
    assert w.min_lines == 3


# ---------------------------------------------------------------------------
# scrollbar (drawn only when wrapped content exceeds the visible rows)
# ---------------------------------------------------------------------------

class _RecCanvas:
    def __init__(self):
        self.rects = []

    def set_fill_color(self, *a):
        pass

    def fill_round_rect(self, x, y, w, h, r):
        self.rects.append((x, y, w, h, r))


def test_scrollbar_drawn_only_past_the_visible_rows():
    w = _field()
    rect = Rect(0, 0, 360, 68)
    track, thumb = (0x1B, 0x24, 0x32, 255), (0x55, 0x61, 0x6F, 255)

    # content fits the 3 visible rows -> no scrollbar
    w._line_count, w._scroll_top = 3, 0
    rc = _RecCanvas()
    w._draw_wrap_scrollbar(rc, rect, 3, _V_PAD, track, thumb)
    assert rc.rects == []

    # content overflows -> track + thumb on the right inner edge
    w._line_count, w._scroll_top = 6, 3
    rc = _RecCanvas()
    w._draw_wrap_scrollbar(rc, rect, 3, _V_PAD, track, thumb)
    assert len(rc.rects) == 2, "track + thumb"
    tx = rect.w - 6.0
    assert all(abs(r[0] - tx) < 0.01 for r in rc.rects), "on the right edge"
    thumb_rect = rc.rects[1]
    # thumb shorter than the track, and its travel reflects scroll position
    assert thumb_rect[3] < rc.rects[0][3]
    assert thumb_rect[1] > rc.rects[0][1], "scrolled down -> thumb off the top"


def test_scrollbar_engages_through_a_real_wrapped_draw():
    w = _field(min_lines=3)
    w.text = WORDS * 3                     # far beyond 3 lines at 360px
    w.cursor = len(w.text)
    c = _canvas()
    w.draw(c, Rect(0, 0, 360, 68))
    n_fit = 3
    assert w._line_count > n_fit, "content must overflow the 3 visible rows"
    # the viewport is pinned to the caret (last lines) -> scrolled
    assert w._scroll_top > 0


# ---------------------------------------------------------------------------
# #4 reusable-widget seam: the mixin is composed BY REFERENCE, not forked
# ---------------------------------------------------------------------------

def test_multiline_mixin_is_the_reusable_seam():
    from composeio.render.widgets import MultiLineEditMixin as Exported
    from composeio.render.widgets.multiline_text import MultiLineEditMixin

    assert Exported is MultiLineEditMixin, "exported from the widgets package"
    assert issubclass(TextInputWidget, MultiLineEditMixin)
    # the wrap math is the SAME function object the field uses (not a copy)
    assert TextInputWidget._wrap_lines is MultiLineEditMixin._wrap_lines

    # a fresh widget composing the mixin — NO console chrome, no fonts — reuses
    # the identical wrap / visible-line / vertical-nav code against the same
    # _glyph_x contract.
    class _Mini(MultiLineEditMixin):
        def __init__(self):
            self.text = ""
            self.cursor = 0
            self.select_anchor = None
            self._lines = [(0, 0)]
            self._glyph_x = [0.0]
            self._line_count = 1
            self._scroll_top = 0
            self.min_lines = 1
            self.max_lines = 4

    m = _Mini()
    assert m._visible_lines() == 1
    m.min_lines = 3
    assert m._visible_lines() == 3         # rests at min_lines
    m._line_count = 6
    assert m._visible_lines() == 4         # capped at max_lines

    # vertical caret nav on host-supplied advances, no draw/chrome involved
    m.text = "abcd"
    m._glyph_x = [0.0, 10.0, 20.0, 30.0, 40.0]
    m._lines = [(0, 2), (2, 4)]
    m._line_count = 2
    m.cursor = 3                           # line 1, x within line = 10px
    m._caret_vertical(-1, False)           # up to line 0 nearest 10px -> idx 1
    assert m._line_of(m.cursor) == 0 and m.cursor == 1
