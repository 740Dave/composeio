"""
Reusable soft-wrap / grow-then-scroll editing substrate ([CXC-R-ENGINE-TEXT]).

`MultiLineEditMixin` factors the console composer's multi-line behavior OUT of
`text_input` so any widget can compose it — the auto-grow field is no longer
console-chrome behavior locked inside one widget.  It owns the parts that are
generic to *any* soft-wrapping editable text region:

  * greedy word-wrap of the shown string (``_wrap_lines``);
  * the resting/min ↔ grow ↔ cap-then-scroll visible-line count
    (``_visible_lines`` — ``min_lines``..``max_lines``);
  * which wrapped line a caret index sits on (``_line_of``);
  * Up/Down caret navigation between wrapped lines and the within-line
    nearest-boundary pick (``_caret_vertical`` / ``_index_on_line``);
  * a thin track+thumb scrollbar drawn when content exceeds the viewport
    (``_draw_wrap_scrollbar``, the ``chat_thread._draw_scrollbar`` idiom).

The unifying discipline ([CXC-R-DRAW-FONT]): EVERY method here reads the host's
``_glyph_x`` — the cumulative prefix advances the host cached at draw time from
its ONE ``measure_text`` path (``_prefix_widths``) — so wrap, caret placement,
click hit-testing, and up/down nav can never disagree.  There is no second
measurement path.  The mixin computes nothing about glyph widths itself; it is
pure index/geometry math over advances the host supplies.

Reuse-by-reference, not fork (R3): a second multi-line widget mixes this in and
provides the same ``_glyph_x`` contract — it does NOT copy the wrap math.

Host contract — the composing widget must provide these attributes:
  text (str), cursor (int), select_anchor (int|None),
  _lines (list[(start, end)]), _glyph_x (list[float], len == len(shown)+1),
  _line_count (int), _scroll_top (int), min_lines (int), max_lines (int).
The mixin never touches chrome, fonts, secrets, or commit semantics.
"""

from __future__ import annotations

from typing import Any


class MultiLineEditMixin:
    """Soft-wrap + grow-then-scroll + vertical caret nav + scrollbar.

    A behavior mixin (no ``__init__``): the host owns the state attributes
    listed in the module docstring and calls these helpers from its own
    ``draw`` / ``on_key`` / ``measure``.
    """

    # -- wrap segmentation ----------------------------------------------------

    @staticmethod
    def _wrap_lines(xs: list, shown: str, avail: float) -> list:
        """Greedy word-wrap of *shown* to *avail* px, computed on the SAME
        cumulative glyph advances (*xs*) that position the caret and hit-test
        the mouse — ONE measurement path, so wrapping and caret placement can
        never disagree ([CXC-R-DRAW-FONT] identity).

        Returns [(start, end), ...] index pairs (contiguous, covering the
        whole string; always at least one line).  Breaks after the last
        space that fits; a word longer than the line hard-breaks per glyph.
        """
        n = len(shown)
        if n == 0 or avail <= 0:
            return [(0, n)]
        lines = []
        start = 0
        while start < n:
            end = start + 1                      # a line always takes >=1 char
            while end < n and xs[end + 1] - xs[start] <= avail:
                end += 1
            if end < n:                          # breaking mid-text
                for i in range(end, start, -1):  # prefer the last space
                    if shown[i - 1] == " ":
                        end = i
                        break
            lines.append((start, end))
            start = end
        return lines

    # -- visible-line count (resting min ↔ grow ↔ cap) ------------------------

    def _visible_lines(self) -> int:
        """Lines the field's measured height shows: min_lines..max_lines.

        Resting (empty / one wrapped line) the field still shows ``min_lines``
        rows — the console composer's "start at 3 rows, fill the space".  As
        the wrap grows it follows the line count, capped at ``max_lines``;
        beyond that the viewport scrolls (height holds at max_lines)."""
        lo = min(self.min_lines, self.max_lines)
        return max(lo, min(self._line_count, self.max_lines))

    def _line_of(self, idx: int) -> int:
        """The wrapped line holding caret index *idx* (a caret sitting ON a
        wrap boundary belongs to the NEXT line — where typing continues)."""
        last = len(self._lines) - 1
        for i, (_s, e) in enumerate(self._lines):
            if idx < e or (idx == e and i == last):
                return i
        return last

    # -- vertical caret navigation ([CXC-R-ENGINE-TEXT] Up/Down) --------------

    def _index_on_line(self, xs: list, line_idx: int, x_off: float) -> int:
        """Nearest glyph boundary on wrapped line *line_idx* to the within-line
        x offset *x_off*, on the SAME advances the caret/click use — so an
        Up/Down landing agrees with wrap and hit-testing."""
        s, e = self._lines[line_idx]
        return min(range(s, e + 1),
                   key=lambda i: abs((xs[i] - xs[s]) - x_off))

    def _caret_vertical(self, delta: int, shift: bool) -> None:
        """Move the caret one wrapped line up (delta=-1) or down (+1),
        preserving its x on the adjacent line via the cached advances.  Off
        the top → line start; off the bottom → line end (Home/End at the
        edges).  ``shift`` extends the selection (anchor stays put); a plain
        move collapses it.  A no-op measurement-wise before the first draw
        (no advances yet) degrades to a start/end jump."""
        xs = self._glyph_x
        lines = self._lines
        if not xs or not lines or self.cursor >= len(xs):
            target = 0 if delta < 0 else len(self.text)
        else:
            cur = self._line_of(self.cursor)
            cs = lines[cur][0]
            x_off = xs[self.cursor] - xs[cs]     # caret x within its line
            tgt = cur + delta
            if tgt < 0:
                target = 0
            elif tgt >= len(lines):
                target = len(self.text)
            else:
                target = self._index_on_line(xs, tgt, x_off)
        if shift:
            if self.select_anchor is None:
                self.select_anchor = self.cursor
        else:
            self.select_anchor = None
        self.cursor = target

    # -- scrollbar ([CXC-ICV-TOKENS]; chat_thread._draw_scrollbar idiom) ------

    def _draw_wrap_scrollbar(self, canvas: Any, rect: Any, n_fit: int,
                             pad_y: float, track_color, thumb_color) -> None:
        """Thin track + thumb on the field's right inner edge, shown ONLY when
        the wrapped content exceeds the visible rows (``_line_count`` >
        *n_fit*).  Geometry is pure (visible / total, scroll_top) — the SAME
        ~15-line idiom as ``chat_thread._draw_scrollbar``, mirrored (not
        imported) because the two widgets carry different scroll models and
        the shared surface is these few lines of geometry, not a base class."""
        total = self._line_count
        if total <= n_fit or n_fit <= 0:
            return
        track_w = 3.0
        tx = rect.x + rect.w - 6.0
        ty = rect.y + pad_y
        th = max(1.0, rect.h - 2 * pad_y)
        canvas.set_fill_color(*track_color)
        canvas.fill_round_rect(tx, ty, track_w, th, track_w / 2)
        thumb_h = max(18.0, th * (n_fit / total))
        travel = max(0.0, th - thumb_h)
        frac = 0.0 if total <= n_fit else self._scroll_top / (total - n_fit)
        canvas.set_fill_color(*thumb_color)
        canvas.fill_round_rect(tx, ty + travel * frac, track_w, thumb_h,
                               track_w / 2)
