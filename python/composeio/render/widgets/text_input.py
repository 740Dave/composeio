"""
ComposeUX TextInputWidget — labeled text entry that publishes on commit.

M2 port of ux_composer TextInputRenderer, plus the engine-native editing
that CTk got for free: focus, cursor, keyboard input ([CXC-R-C8]).
Editing per [CXC-R-ENGINE-TEXT]: the default (form) chrome stays the
Phase-1 single-line entry, byte-identical to its goldens; the console
composer chrome ([SPEC:CXC-CONSOLE-VISUAL]) is a SOFT-WRAPPING
multi-line field — see "console composer mode" below.  IME remains
deferred.  Selection + clipboard pass:
Shift+arrows / Cmd-Ctrl+A select, Cmd-Ctrl+C/X/V copy/cut/paste via the
SDL clipboard (lazily imported — headless tests never touch sdl2), and
mouse click/drag places the caret / extends the selection using glyph
metrics cached at draw time.  Secret fields never copy/cut (the
plaintext must not reach the clipboard); paste collapses newlines to
spaces so a multi-line clipboard lands as one clean paragraph (the
field NEVER contains hard newlines — multi-line display is soft wrap
only, and Enter still commits).

An INPUT widget: a source of bus state, not a follower.  On commit
(Return / focus-out) it publishes the field value to its namespace.

secret=True — the credential case: input renders masked (•), and commit
publishes the SHA-512 crypt hash ($6$…), never the plaintext.  A
restored $6$ hash arriving on the namespace NEVER flows into the entry
(committing would re-hash it); it surfaces as a status badge instead.

Console composer mode ([SPEC:CXC-CONSOLE-VISUAL], interactivity pass) —
all OPT-IN so every existing scene/golden renders byte-identically:
  chrome="console"     draw() wears the pre-vis .composer .field look
                       (console_theme tokens: panel3 field, dim
                       placeholder, neutral line border + blinking
                       accent cursor, ink text — no focus ring).
  clear_on_commit=True chat-composer semantics: commit clears the entry
                       (the bus echo of the just-sent value is
                       suppressed so it never flows back in), and blur
                       does NOT commit — a chat message sends only on
                       an explicit Enter / SEND, never by clicking away.
  commit_namespace     a commit TRIGGER topic: any publish on it commits
                       exactly like Enter.  The scene's SEND button is a
                       declarative publish_namespace tap binding onto
                       this topic — cross-widget send with zero new
                       engine plumbing, and an Intelligence can trigger
                       it identically ([CXC-R-CONTROL] symmetry).
  max_lines            console chrome only (default 3): the field SOFT-
                       WRAPS its text against its width and GROWS from
                       min_lines to at most max_lines visible lines
                       (measure() reports the wrapped height); beyond
                       that it stops growing and scrolls vertically to
                       keep the caret line visible, drawing a thin
                       scrollbar.  Wrapping runs on the SAME cached
                       glyph advances that place the caret and hit-test
                       the mouse (_prefix_widths) — one measurement
                       path, so wrap/caret/click can never disagree, and
                       the engine-rasterized FreeType pixel identity
                       ([CXC-R-DRAW-FONT]) is untouched.  Plain Up/Down
                       move the caret vertically between wrapped lines
                       (Shift+Up/Down extend the selection), computed at
                       the caret's x on the adjacent line from the SAME
                       advances.  Enter still COMMITS — there is no
                       newline key; the text never contains "\\n".
  min_lines            console chrome only (default 1): the RESTING /
                       minimum visible-line count.  The generic widget
                       rests at 1 line (grow-from-1, every existing
                       scene byte-identical); the console composer sets
                       min_lines: 3 in the scene so the field STARTS at
                       three rows and fills the composer's fixed panel,
                       still growing-then-scrolling past max_lines.
                       Clamped to <= max_lines.

The grow/wrap/caret-nav/scrollbar substrate is the reusable
MultiLineEditMixin ([CXC-R-ENGINE-TEXT], widgets/multiline_text.py) —
this widget composes it; a second multi-line widget mixes in the SAME
math against the same _glyph_x contract rather than forking it.

Damage discipline ([CXC-R-DAMAGE]): every content/height/scroll change
requests only this widget's own region (request_redraw, no args).  A
change that alters the WRAPPED HEIGHT additionally enqueues a no-op
update TARGETED at this widget (ctx.schedule_update(..., target=self))
so the runtime damages old-rect UNION new-rect post-layout — the exact
growth path runtime.drain_updates/_apply_drained_damage provides.  The
caret blink stays scoped to the caret's sliver (_caret_rect), which
draw() places on the caret's CURRENT line.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style
from .multiline_text import MultiLineEditMixin

_ENTRY_H = 28.0
_LABEL_GAP = 4.0
_STATUS_H = 16.0

# Console composer multi-line metrics ([SPEC:CXC-CONSOLE-VISUAL]): 13px Plex
# on an 18px line pitch, 7px vertical inset — one line measures 2*7 + 18 = 32,
# the resting composer field height the scene has always had.  The caret
# sliver keeps its historical 20px height (the 32px single-line field's
# y+6..y+26) on whichever line it currently occupies.
_LINE_PITCH = 18.0
_V_PAD = 7.0
_CARET_H = 20.0

# Caret blink ([CXC-R-DAMAGE] caret animation): ~1 Hz, 50% duty — ON for the
# first half of each period, OFF for the second (the pre-vis composer caret).
_CARET_PERIOD = 1.0
_CARET_DUTY = 0.5

# SDL keymod masks (sdl2.KMOD_*) — literals so this module never needs sdl2.
_KMOD_SHIFT = 0x0003     # LSHIFT | RSHIFT
_KMOD_CTRL = 0x00C0      # LCTRL | RCTRL
_KMOD_GUI = 0x0C00       # LGUI | RGUI (macOS Cmd)


def _clipboard_get() -> str:
    """Read the OS clipboard.

    Delegates to `render.sdl_strings` — the ONE place the engine takes ownership
    of an SDL-malloc'd string.  This function used to carry its own copy of the
    read, and that second copy leaked SDL's buffer on every paste; the copy in
    `window.py` got the free WRONG and aborted the process (see `sdl_strings`).
    `sdl_strings` is skia-free and lazy about sdl2, so the headless promise below
    still holds.

    Lazy import: headless tests and non-SDL environments never touch
    sdl2 — tests monkeypatch this seam instead.  Any failure (no SDL,
    no clipboard, undecodable bytes) degrades to "" (paste is a no-op).
    """
    try:
        from ..sdl_strings import clipboard_text
        return clipboard_text()
    except Exception:
        return ""


def _clipboard_set(text: str) -> None:
    """Write the OS clipboard (same single seam; failures ignored)."""
    try:
        from ..sdl_strings import set_clipboard_text
        set_clipboard_text(text)
    except Exception:
        pass


# Commit-time ASCII -> emoji substitution (chat-composer nicety).  Applied
# ONLY to the committed string of a clear_on_commit (chat) field — the entry
# text, form fields, and secret fields are never touched.  Longer codes
# first so ":thumbsup:" is never half-eaten by a shorter pattern.
_EMOJI_MAP = (
    (":thumbsup:", "\U0001F44D"),   # 👍
    (":fire:", "\U0001F525"),       # 🔥
    (":D", "\U0001F604"),           # 😄
    (":)", "\U0001F642"),           # 🙂
    ("<3", "❤️"),         # ❤️
)


def emojify(text: str) -> str:
    """Map a few ASCII emoticon codes to emoji (plain substring replace —
    deliberately tiny; a code inside a longer word is also replaced)."""
    for code, emoji in _EMOJI_MAP:
        if code in text:
            text = text.replace(code, emoji)
    return text


def hash_password(plaintext: str) -> str:
    """SHA-512 crypt (`$6$…`) with a fresh random salt, plaintext via stdin.

    Port of composeio_renderers.secrets.hash_password: openssl is present
    on macOS (LibreSSL) and Linux; the plaintext never enters argv, is
    never logged, and never leaves this function except as the hash.
    """
    if not plaintext:
        raise ValueError("empty password")
    proc = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=plaintext, capture_output=True, text=True, timeout=10,
    )
    digest = (proc.stdout or "").strip()
    if proc.returncode != 0 or not digest.startswith("$6$"):
        raise RuntimeError(f"password hash failed (rc={proc.returncode})")
    return digest


class TextInputWidget(MultiLineEditMixin, Widget):
    """Labeled text/secret entry with engine-native single-line editing.

    Composes MultiLineEditMixin for the console composer's soft-wrap /
    grow-then-scroll / vertical-caret-nav / scrollbar behavior."""

    def __init__(self, widget_id: str, *,
                 label: str = "",
                 placeholder: str = "",
                 secret: bool = False,
                 namespace: str = "",
                 chrome: str = "",
                 clear_on_commit: bool = False,
                 commit_namespace: str = "",
                 max_lines: int = 3,
                 min_lines: int = 1,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "text_input", **kwargs)
        self.label = label
        self.placeholder = placeholder
        self.secret = secret
        self.bound_namespace = namespace
        self.chrome = str(chrome)                  # "" | "console"
        self.clear_on_commit = bool(clear_on_commit)
        self.commit_namespace = str(commit_namespace)
        # console chrome: cap of VISIBLE soft-wrapped lines (grow-then-scroll)
        self.max_lines = max(1, int(max_lines))
        # RESTING / minimum visible-line count (grow-from here up to max_lines).
        # 1 = the historical grow-from-1 (every non-console scene unchanged);
        # the console composer sets 3 so the field fills its fixed panel.
        self.min_lines = max(1, min(int(min_lines), self.max_lines))
        self.interactive = True
        self.accepts_focus = True
        # [CXC-R-ENGINE-HOVER] editable text -> I-beam, not the click-hand.
        # This IS the prompt/message input; MultiLineEditMixin gives it a real
        # cursor + drag-select (select_anchor), so the I-beam is honored today.
        self.text_selectable = True
        # File-drop target (5b, [DEC-THREAD-MARKDOWN-HEADERS] companion): a drop
        # onto this field inserts the file path at the cursor (David: "drop into
        # the message prompt text area as well").  The runtime routes a drop to
        # the widget under the pointer that opts in here; a drop elsewhere keeps
        # the historical whole-window -> thread behavior.
        self.wants_file_drop = True
        if self.chrome == "console":
            # The console composer signals focus with its blinking accent
            # caret, so it opts OUT of the engine focus ring ([CXC-ICV-CHROME]).
            # An explicit scene `focus_ring:` key (applied in _wire_common,
            # after construction) still wins over this default.
            self.focus_ring = False

        self.text = ""
        self.cursor = 0
        # selection: anchor..cursor is the selected range; None = no selection
        self.select_anchor: Optional[int] = None
        # draw-time glyph metrics (mouse hit-testing seam): cumulative prefix
        # widths of the SHOWN string + the last text origin x.  Only valid
        # after a draw; pointer mapping degrades to a no-op before one.
        self._glyph_x: list = []
        self._metrics_key: Optional[str] = None
        self._text_origin = 0.0
        self._press_x = 0.0
        self._press_y = 0.0
        # Multi-line soft wrap (console chrome, [CXC-R-ENGINE-TEXT] Phase 2):
        # _lines is the wrap segmentation of the SHOWN string as (start, end)
        # index pairs; _line_meta the per-VISIBLE-line draw record
        # (start, end, origin_x, top_y) that mouse hit-testing reads;
        # _scroll_top the first visible line once content exceeds max_lines.
        # _wrap_avail + _last_canvas are the draw-time seams that let the
        # edit path re-wrap on the SAME glyph advances between frames.
        self._lines: list = [(0, 0)]
        self._line_count = 1
        self._scroll_top = 0
        # wheel scroll ([CXC-R-ENGINE-TEXT] wheel-scroll invariant): _n_fit is
        # the visible-row count recorded at draw; _user_scrolled latches once
        # the wheel moves the viewport OFF the caret line so the next draw does
        # not yank it back (caret-follow re-engages the moment the caret moves,
        # tracked via _last_cursor).
        self._n_fit = 1
        self._last_cursor = -1
        self._user_scrolled = False
        self._line_meta: list = []
        self._wrap_avail = 0.0
        self._last_canvas: Optional[Canvas] = None
        self._status_text = ""
        self._status_color = style.TEXT_DIM
        self._now = time.monotonic      # test seam (cursor blink)
        # the just-committed value, ignored ONCE when the bus echoes it
        # back on bound_namespace (clear_on_commit only)
        self._suppress_echo: Optional[str] = None
        # Caret blink ([CXC-R-DAMAGE]): while FOCUSED the field registers as a
        # clocked widget; on_clock_tick toggles this phase at ~1 Hz and damages
        # ONLY the caret sliver.  _caret_rect is the caret's small surface rect
        # (set at draw time) that clock_damage_rect() hands the engine.  Steady
        # ON when not clocking (unfocused, or reduced_motion — no blink).
        self._caret_on: bool = True
        self._caret_rect: Optional[Rect] = None
        self._clocking: bool = False

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        if self.chrome == "console":
            ct.theme_from_ctx(ctx)      # adopt design.theme ([CXC-ICV-THEME])

    # -- caret blink ([CXC-R-DAMAGE] caret animation) -------------------------

    def _caret_phase(self, now: float) -> bool:
        """Caret ON/OFF at clock *now* — first half of each period is ON."""
        if _CARET_PERIOD <= 0:
            return True
        return (now % _CARET_PERIOD) < (_CARET_PERIOD * _CARET_DUTY)

    def on_focus(self) -> None:
        """Start blinking the caret ([CXC-R-DAMAGE]): register as a clocked
        widget so on_clock_tick drives the ~1 Hz phase and damages only the
        caret sliver.  Under reduced_motion ([CXC-ICV-MOTION]) the caret is
        solid — no clock, no per-frame damage — exactly like the other ambient
        animations.  An unfocused field never ticks."""
        self._caret_on = True
        if ct.reduced_motion(self._ctx):
            return
        ctx = self._ctx
        if ctx is not None and hasattr(ctx, "register_clocked") \
                and not self._clocking:
            ctx.register_clocked(self)
            self._clocking = True

    def on_blur(self) -> None:
        # Stop the caret clock BEFORE the (possible) commit: a blurred field
        # must not tick or damage ([CXC-R-DAMAGE]).
        if self._clocking and self._ctx is not None \
                and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)
        self._clocking = False
        self._caret_on = True
        if not self.clear_on_commit:
            # form-field semantics (unchanged default): focus-out commits.
            # A chat composer (clear_on_commit) sends ONLY on an explicit
            # Enter / SEND — clicking away never fires a half-typed turn.
            self.commit()

    def on_clock_tick(self, now: float) -> bool:
        """Toggle the caret phase at ~1 Hz; return True (repaint) only on the
        frames the phase actually flips — so the engine damages the caret
        sliver ~twice a second, never every frame.  A blurred field (racing an
        in-flight unregister) never animates."""
        if not self.focused or ct.reduced_motion(self._ctx):
            # reduced_motion: solid caret, no blink (the field is also never
            # registered as clocked, so this is a belt-and-suspenders rest).
            return False
        phase = self._caret_phase(now)
        if phase == self._caret_on:
            return False
        self._caret_on = phase
        return True

    def clock_damage_rect(self) -> Optional[Rect]:
        """The caret's small sliver ([CXC-R-DAMAGE]) — the ONLY region a blink
        repaints.  Falls back to the arranged rect before the first draw has
        located the caret."""
        return self._caret_rect if self._caret_rect is not None \
            else self.arranged_rect

    @staticmethod
    def _caret_bounds(caret_x: float, y0: float, y1: float) -> Rect:
        """The caret's small surface rect (a ~2px sliver around the 1px line),
        recorded at draw time so a blink damages just this — not the field."""
        return Rect(caret_x - 1.0, y0, 3.0, max(1.0, y1 - y0))

    def on_unmount(self) -> None:
        if self._clocking and self._ctx is not None \
                and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)
        self._clocking = False

    # -- bus reflection (runs on the render thread via the update queue) -----

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if self.commit_namespace and namespace == self.commit_namespace:
            # commit TRIGGER topic (the SEND button's declarative tap
            # publish lands here): commit exactly like Enter.  The value
            # is a nudge (toggle), never reflected into the entry.
            self.commit()
            self.request_redraw()
            return
        if self.secret:
            self._reflect_secret(value)
        else:
            self._reflect(value)

    def _reflect(self, value: Any) -> None:
        if self._suppress_echo is not None and value is not None \
                and str(value) == self._suppress_echo:
            # the bus echo of the value we just committed-and-cleared —
            # consume the marker, keep the entry empty.
            self._suppress_echo = None
            return
        if value is not None and str(value) != self.text:
            self.text = str(value)
            self.cursor = len(self.text)
            self.select_anchor = None
            # re-wrap so the SAME drained frame's layout sees the restored
            # height (this update is already runtime-targeted at this widget,
            # so its old-UNION-new damage needs no extra scheduling here)
            self._recompute_wrap()
            self._set_status("↺ restored", style.LED_ACTIVE)

    def _reflect_secret(self, value: Any) -> None:
        if self.text:            # value came from a restore, not typing here
            return
        if value and str(value).startswith("$6$"):
            self._set_status("✓ saved password loaded (from document store)",
                             style.GREEN)
        else:
            self._set_status("⚠ no password set", (0xE6, 0xB4, 0x50))

    def _set_status(self, text: str, color) -> None:
        self._status_text = text
        self._status_color = color

    # -- soft wrap ([CXC-R-ENGINE-TEXT] multi-line, console chrome) -----------
    # _wrap_lines / _visible_lines / _line_of live on MultiLineEditMixin (the
    # reusable substrate) — this widget composes them.

    def _shown(self) -> str:
        return ("•" * len(self.text)) if self.secret else self.text

    def _recompute_wrap(self) -> bool:
        """Re-wrap after a text edit, on the same cached advances draw uses.
        Returns True when the VISIBLE line count — and so measure()'s height
        — changed.  A no-op before the first draw (no canvas/width yet: the
        first draw computes the wrap) and for non-console chrome."""
        canvas = self._last_canvas
        if self.chrome != "console" or canvas is None or self._wrap_avail <= 0:
            return False
        old_vis = self._visible_lines()
        try:
            canvas.set_font(ct.FONT_UI, 13, "regular")
            xs = self._prefix_widths(canvas, self._shown())
        except Exception:
            return False                 # measurement unavailable: next draw wraps
        self._lines = self._wrap_lines(xs, self._shown(), self._wrap_avail)
        self._line_count = len(self._lines)
        return self._visible_lines() != old_vis

    def _after_edit(self) -> None:
        """Post-edit hook ([CXC-R-DAMAGE]): re-wrap, then request ONLY this
        widget's repaint.  When the wrapped height changed, additionally
        enqueue a no-op update TARGETED at this widget so the runtime damages
        old-rect UNION new-rect post-layout (drain_updates captures the
        pre-layout rect; _apply_drained_damage adds the post-layout one) —
        the sanctioned scoped-growth path, never a full-surface mark."""
        height_changed = self._recompute_wrap()
        self.request_redraw()
        if height_changed and self._ctx is not None \
                and hasattr(self._ctx, "schedule_update"):
            self._ctx.schedule_update(lambda: None, target=self)

    # -- editing ([CXC-R-ENGINE-TEXT] Phase 1 + selection/clipboard) ----------

    def _sel_range(self) -> Optional[tuple]:
        """(lo, hi) of the selection, or None when nothing is selected."""
        a = self.select_anchor
        if a is None or a == self.cursor:
            return None
        return (min(a, self.cursor), max(a, self.cursor))

    def _delete_selection(self) -> bool:
        """Remove the selected range (cursor lands at its start).
        Returns True when a selection was actually deleted."""
        r = self._sel_range()
        self.select_anchor = None
        if r is None:
            return False
        lo, hi = r
        self.text = self.text[:lo] + self.text[hi:]
        self.cursor = lo
        return True

    def on_text(self, text: str) -> None:
        printable = "".join(ch for ch in text if ch.isprintable())
        if not printable:
            return
        self._delete_selection()          # typing replaces the selection
        self.text = self.text[:self.cursor] + printable + self.text[self.cursor:]
        self.cursor += len(printable)
        self._after_edit()

    def on_key(self, key: str, modifiers: Any = None) -> None:
        mod = int(modifiers or 0)
        shift = bool(mod & _KMOD_SHIFT)
        # Cmd (macOS KMOD_GUI) OR Ctrl — same shortcut works cross-platform
        primary = bool(mod & (_KMOD_CTRL | _KMOD_GUI))

        if primary and len(key) == 1:
            k = key.upper()
            if k == "V":
                self._paste()
            elif k == "C":
                self._copy()
            elif k == "X":
                self._cut()
            elif k == "A":                # select all
                self.select_anchor = 0
                self.cursor = len(self.text)
            return

        if key in ("Return", "Enter", "Keypad Enter"):
            # chat semantics preserved: Enter SENDS ([CXC-ICV-CHROME]) — the
            # multi-line room comes purely from soft wrap, never a newline key.
            self.commit()
        elif key == "Backspace":
            if not self._delete_selection() and self.cursor > 0:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
            self._after_edit()
        elif key == "Delete":
            if not self._delete_selection():
                self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
            self._after_edit()
        elif key == "Left":
            if shift:
                if self.select_anchor is None:
                    self.select_anchor = self.cursor
                self.cursor = max(0, self.cursor - 1)
            else:
                r = self._sel_range()
                self.select_anchor = None
                self.cursor = r[0] if r else max(0, self.cursor - 1)
        elif key == "Right":
            if shift:
                if self.select_anchor is None:
                    self.select_anchor = self.cursor
                self.cursor = min(len(self.text), self.cursor + 1)
            else:
                r = self._sel_range()
                self.select_anchor = None
                self.cursor = r[1] if r else min(len(self.text), self.cursor + 1)
        elif key == "Home":
            if shift and self.select_anchor is None:
                self.select_anchor = self.cursor
            elif not shift:
                self.select_anchor = None
            self.cursor = 0
        elif key == "End":
            if shift and self.select_anchor is None:
                self.select_anchor = self.cursor
            elif not shift:
                self.select_anchor = None
            self.cursor = len(self.text)
        elif key == "Up":
            # vertical caret nav across wrapped lines ([CXC-R-ENGINE-TEXT]):
            # keeps the caret's x, computed from the SAME advances as wrap/
            # click, on the line above (off the top -> line start).  No text
            # change -> no re-wrap; the engine damages the focused field.
            self._caret_vertical(-1, shift)
        elif key == "Down":
            self._caret_vertical(+1, shift)
        elif key == "Escape":
            self.text = ""
            self.cursor = 0
            self.select_anchor = None
            self._after_edit()

    # -- clipboard ([CXC-R-ENGINE-TEXT] selection/clipboard pass) -------------

    def _paste(self) -> None:
        """Insert the OS clipboard at the cursor (replacing any selection).
        The field never holds hard newlines ([CXC-R-ENGINE-TEXT]): newlines
        of any flavor collapse to single spaces, so a multi-line paste lands
        as one clean paragraph that then SOFT-wraps in the console chrome."""
        clip = _clipboard_get()
        if not clip:
            return
        one_line = " ".join(
            clip.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
        printable = "".join(ch for ch in one_line if ch.isprintable())
        if not printable:
            return
        self._delete_selection()
        self.text = (self.text[:self.cursor] + printable
                     + self.text[self.cursor:])
        self.cursor += len(printable)
        self._after_edit()

    def on_file_drop(self, path: str) -> bool:
        """A file was dropped ONTO this field (5b): insert its path at the
        cursor so it rides with the typed message, exactly like the OS-paste
        path (replace any selection, collapse newlines, keep the field
        single-logical-line).  A trailing space separates it from a following
        caption.  Returns True when the drop was consumed here — so the runtime
        does NOT also fire the whole-window thread publish; False (secret field
        / empty path) lets the runtime fall back to the historical behavior.

        The runtime focuses this field after a True (so its caret shows where
        the path landed and an immediate Enter sends it)."""
        if self.secret or not path:
            return False
        insert = " ".join(str(path).split("\n")) + " "
        printable = "".join(ch for ch in insert if ch.isprintable())
        if not printable:
            return False
        self._delete_selection()
        self.text = self.text[:self.cursor] + printable + self.text[self.cursor:]
        self.cursor += len(printable)
        self._after_edit()
        return True

    def _copy(self) -> None:
        """Copy the selection to the OS clipboard.  No-op without a
        selection — and ALWAYS a no-op for secret fields (the display is
        masked; the plaintext must never reach the clipboard)."""
        if self.secret:
            return
        r = self._sel_range()
        if r is not None:
            _clipboard_set(self.text[r[0]:r[1]])

    def _cut(self) -> None:
        """Copy the selection, then delete it (secret fields: no-op)."""
        if self.secret:
            return
        r = self._sel_range()
        if r is None:
            return
        _clipboard_set(self.text[r[0]:r[1]])
        self._delete_selection()
        self._after_edit()

    # -- mouse selection (pointer capture routes the drag here) ---------------

    def _index_at(self, x: float, y: Optional[float] = None) -> Optional[int]:
        """Map a pointer position (surface coords) to the nearest glyph
        boundary, using the metrics cached by the last draw.  Line-aware:
        *y* picks the wrapped line from the per-line draw records
        (_line_meta), then *x* picks the boundary within it on the same
        glyph advances the caret uses.  None before any draw."""
        xs = self._glyph_x
        if not xs:
            return None
        meta = self._line_meta
        if not meta or y is None:
            # pre-multi-line fallback (a draw that recorded no lines):
            # single-line mapping against the last text origin
            rel = x - self._text_origin
            return min(range(len(xs)), key=lambda i: abs(xs[i] - rel))
        row = meta[-1]                       # below the last line -> last line
        for m in meta:
            if y < m[3] + _LINE_PITCH:       # first line whose band holds y
                row = m
                break
        s, e, origin, _ly = row
        rel = x - origin
        return min(range(s, e + 1), key=lambda i: abs((xs[i] - xs[s]) - rel))

    def on_pointer_down(self, x: float, y: float) -> None:
        self._press_x, self._press_y = x, y
        idx = self._index_at(x, y)
        if idx is not None:
            self.select_anchor = None
            self.cursor = idx

    def on_pan(self, dx: float, dy: float) -> None:
        # cumulative deltas from the press point ([CXC-R-ENGINE-HIT])
        idx = self._index_at(self._press_x + dx, self._press_y + dy)
        if idx is None:
            return
        if self.select_anchor is None:
            self.select_anchor = self.cursor
        self.cursor = idx
        self.request_redraw()

    def on_scroll(self, dx: float, dy: float) -> None:
        """Mouse-wheel / two-finger scroll of the console composer's viewport
        ([CXC-R-ENGINE-TEXT] wheel-scroll invariant).  The runtime routes the
        wheel to the widget under the pointer; a multi-line field whose content
        overflows its rows moves _scroll_top by whole wrapped lines (dy > 0 =
        wheel-up = reveal EARLIER lines, the chat_thread.on_scroll sign).  It
        latches _user_scrolled so the draw-time caret-follow leaves this
        position alone until the caret next moves.  A no-op for single-line
        chrome, when nothing overflows, or a zero delta — so every non-console
        field keeps its historical (inert) wheel behavior."""
        if self.chrome != "console" or not dy:
            return
        max_top = max(0, self._line_count - self._n_fit)
        if max_top <= 0:
            return
        step = int(dy) if abs(dy) >= 1 else (1 if dy > 0 else -1)
        new_top = max(0, min(self._scroll_top - step, max_top))
        if new_top != self._scroll_top:
            self._scroll_top = new_top
            self._user_scrolled = True
            self.request_redraw()

    # -- word selection (double-click, [CXC-R-ENGINE-TEXT] word-select) --------

    @staticmethod
    def _char_class(ch: str) -> int:
        """Coarse word/space/punct class for word-boundary expansion — a
        double-click grows over one CLASS run (word chars, or a whitespace
        run, or a punctuation run), the platform double-click idiom."""
        if ch.isalnum() or ch == "_":
            return 0                        # word
        if ch.isspace():
            return 1                        # whitespace
        return 2                            # punctuation

    def _word_bounds(self, idx: int) -> tuple:
        """(lo, hi) of the class-run of characters around logical index *idx*
        — the word (or space/punct run) a double-click selects.  A caret at
        the very end grows the LAST run.  Empty text -> (0, 0)."""
        t = self.text
        n = len(t)
        if n == 0:
            return (0, 0)
        i = idx if idx < n else n - 1
        i = max(0, i)
        cls = self._char_class(t[i])
        lo = i
        while lo > 0 and self._char_class(t[lo - 1]) == cls:
            lo -= 1
        hi = i + 1
        while hi < n and self._char_class(t[hi]) == cls:
            hi += 1
        return (lo, hi)

    def on_double_click(self, x: float, y: float) -> None:
        """Select the word under the pointer ([CXC-R-ENGINE-TEXT]
        word-select): map the click to a glyph index on the SAME cached
        advances the caret/click use, then grow to the surrounding class-run.
        The caret lands at the run END (selection anchor at its start), so a
        following Shift+arrow extends from the word — the platform idiom."""
        idx = self._index_at(x, y)
        if idx is None:
            return
        lo, hi = self._word_bounds(idx)
        if lo == hi:
            return
        self.select_anchor = lo
        self.cursor = hi
        self.request_redraw()

    def commit(self) -> None:
        """Publish the field value (hashing first when secret)."""
        raw = self.text
        if not raw:
            return
        if self.secret:
            try:
                value = hash_password(raw)   # plaintext discarded on return
            except Exception:
                self._set_status("✗", style.LED_ALARM)
                return
        elif self.clear_on_commit:
            # chat composer: commit-time emoji substitution — applied to the
            # COMMITTED string only (the entry text is never rewritten, form
            # fields and secrets never touched; the publish leg is unchanged)
            value = emojify(raw)
        else:
            value = raw
        self._set_status("✓ hashed locally" if self.secret else "✓ set",
                         style.GREEN)
        if self.clear_on_commit:
            # chat-composer semantics: the entry clears on send; arm the
            # one-shot echo suppression BEFORE publishing (the bus may
            # call back synchronously).
            self._suppress_echo = str(value)
            self.text = ""
            self.cursor = 0
            self.select_anchor = None
            self._after_edit()      # multi-line field snaps back to one line
        if self.bound_namespace:
            self.publish(self.bound_namespace, value)

    # -- measure / draw ---------------------------------------------------------

    def _prefix_widths(self, canvas: Canvas, shown: str) -> list:
        """Cumulative prefix widths of the shown string (len+1 entries),
        cached until the shown text changes.  Draw-time only — the canvas
        font must already be set.  Doubles as the mouse hit-test table.

        Built in ONE linear pass of per-GLYPH advances, reusing the
        unchanged head of the previously measured string ([CXC-R-ENGINE-TEXT]
        linear-measurement invariant).  The engine's text metric is ADDITIVE
        — a string's width is the sum of its glyphs' integer advances, with
        no kerning or shaping (that is deferred to Phase 3, and normalize_text
        is a neighbour-independent per-char substitution) — so
        ``xs[i] == sum(advance(shown[:i]))`` is identical whether measured as
        one growing prefix per index (the old O(n^2) loop, which re-summed the
        whole prefix n times and re-ran EVERY keystroke) or accumulated glyph
        by glyph here.  Appending at the caret (the "laggy near the end of a
        long message" case) shares the whole existing head, so a keystroke
        measures ONE new glyph, not the entire buffer: O(1) amortised vs the
        old O(L^2)-per-keystroke re-measure.  Pixel identity ([CXC-R-DRAW-FONT])
        is preserved exactly."""
        if shown == self._metrics_key:
            return self._glyph_x
        prev = self._metrics_key
        prev_xs = self._glyph_x
        # longest common prefix with the previously measured string: those
        # cumulative widths are unchanged and get reused verbatim.
        lo = 0
        if prev is not None and prev_xs:
            limit = min(len(shown), len(prev), len(prev_xs) - 1)
            while lo < limit and shown[lo] == prev[lo]:
                lo += 1
        xs = list(prev_xs[:lo + 1]) if (prev_xs and lo + 1 <= len(prev_xs)) \
            else [0.0]
        acc = xs[-1]
        for i in range(lo, len(shown)):
            acc += canvas.measure_text(shown[i]).width
            xs.append(acc)
        self._glyph_x = xs
        self._metrics_key = shown
        return self._glyph_x

    def _draw_selection(self, canvas: Canvas, origin: float,
                        y0: float, y1: float, color) -> None:
        """Filled rect behind the selected glyph range (drawn UNDER text)."""
        r = self._sel_range()
        if r is None:
            return
        xs = self._glyph_x
        sx0, sx1 = origin + xs[r[0]], origin + xs[r[1]]
        canvas.set_fill_color(*color)
        canvas.fill_rect(sx0, y0, sx1 - sx0, y1 - y0)

    def measure(self, constraints: Constraints) -> Size:
        if self.chrome == "console":
            # Multi-line composer: height follows the wrapped line count,
            # capped at max_lines (grow 1..max_lines, then scroll).  The
            # wrap itself is computed at draw/edit time from the arranged
            # width — measure() just reports it (1 line before any draw).
            h = 2 * _V_PAD + self._visible_lines() * _LINE_PITCH
            return Size(min(200.0, constraints.max_width),
                        min(h, constraints.max_height))
        h = _ENTRY_H + _STATUS_H + 8
        if self.label:
            h += 16 + _LABEL_GAP
        return Size(min(200.0, constraints.max_width),
                    min(h, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self.chrome == "console":
            self._draw_console(canvas, rect)
            return
        canvas.save()
        x = rect.x + 8
        y = rect.y + 2
        w = max(60.0, rect.w - 16)

        if self.label:
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 12, "regular")
            m = canvas.measure_text(self.label)
            canvas.draw_text(self.label, x, y + m.ascent, "left")
            y += 16 + _LABEL_GAP

        # entry body + border (2px, accent when focused)
        canvas.set_fill_color(*style.ENTRY_BG)
        canvas.fill_round_rect(x, y, w, _ENTRY_H, 6)
        canvas.set_stroke_color(*(style.ENTRY_BORDER_FOCUS if self.focused
                                  else style.ENTRY_BORDER))
        canvas.set_stroke_width(2)
        canvas.stroke_round_rect(x + 1, y + 1, w - 2, _ENTRY_H - 2, 5)

        # text / placeholder, clipped to the entry
        canvas.save()
        canvas.clip_round_rect(x + 2, y + 2, w - 4, _ENTRY_H - 4, 4)
        canvas.set_font(style.FONT_UI, 13, "regular")
        pad = 8.0
        baseline = style.baseline_for(canvas, y + _ENTRY_H / 2)
        shown = ("•" * len(self.text)) if self.secret else self.text
        if shown:
            # keep the cursor visible: scroll left when the text overflows
            xs = self._prefix_widths(canvas, shown)
            cur_w = xs[self.cursor]
            avail = w - 2 * pad
            shift = max(0.0, cur_w - avail)
            origin = x + pad - shift
            self._text_origin = origin
            self._draw_selection(canvas, origin, y + 5, y + _ENTRY_H - 5,
                                 style.ENTRY_BORDER_FOCUS)
            canvas.set_fill_color(*style.ENTRY_TEXT)
            canvas.draw_text(shown, origin, baseline, "left")
            cursor_x = origin + cur_w
        else:
            self._prefix_widths(canvas, "")
            self._text_origin = x + pad
            origin = x + pad
            if self.placeholder and not self.focused:
                canvas.set_fill_color(*style.ENTRY_PLACEHOLDER)
                canvas.draw_text(self.placeholder, x + pad, baseline, "left")
            cursor_x = x + pad
        # single-line hit-test record (the console chrome's per-line analog)
        self._line_meta = [(0, len(shown), origin, y + 5)]

        # cursor (blinks at 1Hz while focused — phase driven by on_clock_tick)
        self._caret_rect = self._caret_bounds(cursor_x, y + 6, y + _ENTRY_H - 6)
        if self.focused and self._caret_on:
            canvas.set_stroke_color(*style.ENTRY_TEXT)
            canvas.set_stroke_width(1)
            canvas.draw_line(cursor_x + 0.5, y + 6, cursor_x + 0.5, y + _ENTRY_H - 6)
        canvas.restore()

        # status line below
        if self._status_text:
            canvas.set_fill_color(*self._status_color)
            canvas.set_font(style.FONT_UI, 11, "regular")
            sm = canvas.measure_text(self._status_text)
            canvas.draw_text(self._status_text, x + 2,
                             y + _ENTRY_H + 2 + sm.ascent, "left")
        canvas.restore()

    # -- console composer chrome ([SPEC:CXC-CONSOLE-VISUAL]) -------------------

    def _draw_console(self, canvas: Canvas, rect: Rect) -> None:
        """The pre-vis ``.composer .field`` wearing console_theme tokens
        ([CXC-ICV-TOKENS]): panel3 fill on a RADIUS_COMPOSER round-rect,
        1px neutral ``line`` border in both states (focus reads through
        the caret alone, no accent ring), placeholder in ``dim`` (drawn
        only when non-empty), text in ``ink``, blinking accent caret.
        No label/status chrome — the composer stack provides the panel.

        MULTI-LINE ([CXC-R-ENGINE-TEXT] soft wrap): the text word-wraps to
        the field width on the same glyph advances that place the caret;
        the field's measured height follows the line count up to max_lines,
        then the viewport SCROLLS vertically to keep the caret line
        visible.  Everything is clipped to the field box, so scrolled-out
        lines never paint outside it.  The caret sliver (_caret_rect) is
        recorded on the caret's CURRENT visible line, keeping the blink
        damage scoped ([CXC-R-DAMAGE])."""
        t = ct.CONSOLE.active
        x, y, w, h = rect.x, rect.y, rect.w, rect.h
        canvas.save()

        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(x, y, w, h, ct.RADIUS_COMPOSER)
        # neutral ``line`` border in BOTH states — no accent focus ring
        # (the blinking accent caret alone signals focus)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, w - 1, h - 1,
                                 ct.RADIUS_COMPOSER)

        canvas.save()
        canvas.clip_round_rect(x + 1, y + 1, w - 2, h - 2,
                               max(1.0, ct.RADIUS_COMPOSER - 1))
        canvas.set_font(ct.FONT_UI, 13, "regular")
        pad = 12.0
        avail = max(8.0, w - 2 * pad)
        shown = self._shown()
        # draw-time seams for the edit-path re-wrap (_recompute_wrap)
        self._last_canvas = canvas
        self._wrap_avail = avail
        self._text_origin = x + pad          # wrap replaces horizontal shift

        xs = self._prefix_widths(canvas, shown)
        self._lines = self._wrap_lines(xs, shown, avail)
        self._line_count = len(self._lines)

        # viewport: how many wrapped lines this rect shows, and which is on
        # top.  Caret-follow ([CXC-R-ENGINE-TEXT]): when the caret MOVED since
        # the last draw (typing / Up/Down / click) the window is nudged so the
        # caret line is inside it — and that cancels any prior wheel scroll.
        # When the caret is still (a wheel scroll, an ambient repaint) the
        # user's _scroll_top is respected, merely re-bounded to valid range, so
        # a mouse-wheel scroll STICKS instead of snapping back to the caret.
        n_fit = max(1, int((h - 2 * _V_PAD + 0.5) // _LINE_PITCH))
        self._n_fit = n_fit
        max_top = max(0, self._line_count - n_fit)
        cline = self._line_of(self.cursor)
        if self.cursor != self._last_cursor:
            self._user_scrolled = False        # caret moved -> re-follow it
        if self._user_scrolled:
            top = min(max(0, self._scroll_top), max_top)
        else:
            top = self._scroll_top
            top = min(top, cline)
            top = max(top, cline - n_fit + 1)
            top = max(0, min(top, max_top))
        self._scroll_top = top
        self._last_cursor = self.cursor

        sel = self._sel_range()
        self._line_meta = []
        if shown:
            for vi in range(top, min(self._line_count, top + n_fit)):
                s, e = self._lines[vi]
                ly = y + _V_PAD + (vi - top) * _LINE_PITCH
                baseline = style.baseline_for(canvas, ly + _LINE_PITCH / 2)
                if sel is not None:          # selection band, per line
                    lo, hi = max(sel[0], s), min(sel[1], e)
                    if lo < hi:
                        canvas.set_fill_color(*t.accent_dim)
                        canvas.fill_rect(x + pad + (xs[lo] - xs[s]), ly + 1,
                                         xs[hi] - xs[lo], _LINE_PITCH - 2)
                canvas.set_fill_color(*t.ink)
                canvas.draw_text(shown[s:e], x + pad, baseline, "left")
                self._line_meta.append((s, e, x + pad, ly))
            cs, _ce = self._lines[cline]
            caret_x = x + pad + (xs[self.cursor] - xs[cs])
            caret_ly = y + _V_PAD + (cline - top) * _LINE_PITCH
        else:
            self._prefix_widths(canvas, "")
            if self.placeholder:
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(self.placeholder, x + pad,
                                 style.baseline_for(
                                     canvas, y + _V_PAD + _LINE_PITCH / 2),
                                 "left")
            self._line_meta = [(0, 0, x + pad, y + _V_PAD)]
            caret_x = x + pad
            caret_ly = y + _V_PAD

        # caret on its CURRENT line (blinks at 1Hz while focused, accent ink
        # — phase driven by on_clock_tick, which damages ONLY this sliver):
        # the 20px sliver of the 32px single-line field, centered on the
        # caret's 18px line band.
        cy0 = caret_ly + (_LINE_PITCH - _CARET_H) / 2
        cy1 = cy0 + _CARET_H
        self._caret_rect = self._caret_bounds(caret_x, cy0, cy1)
        if self.focused and self._caret_on:
            canvas.set_stroke_color(*t.accent)
            canvas.set_stroke_width(1.5)
            canvas.draw_line(caret_x + 0.5, cy0, caret_x + 0.5, cy1)

        # scrollbar ([CXC-ICV-TOKENS]): drawn only when the wrapped content
        # exceeds the visible rows (_line_count > n_fit).  The right-edge inset
        # (6px) sits in the field's 12px right pad, clear of the text.
        self._draw_wrap_scrollbar(canvas, rect, n_fit, _V_PAD, t.line, t.faint)
        canvas.restore()
        canvas.restore()
