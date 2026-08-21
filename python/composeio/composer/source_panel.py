"""SourcePanel — the Composer's live YAML view ([CXC-BE-SOURCE]).

A synced PROJECTION of the CompositeDocument (DP-CXC-2: the document/
visual stays the source of truth):

    - shows the design's .compose.yaml exactly as export writes it
      (the byte-identical CompositeDocument serialization, DP-CXC-7);
    - engine-native multi-line editing (cursor, click-to-position,
      keyboard) — every edit is validated; a VALID parse commits to the
      document over the bus (composer/source/replace); an invalid edit
      shows an inline error and NEVER touches the visual state
      ([CXC-BE-C5]);
    - selection linkage both ways: canvas selection scrolls/highlights
      the widget's YAML block; clicking inside a block selects the
      widget on the canvas ([CXC-BE-C4]);
    - commits go through the core dispatcher, which re-parses the RAW
      spec dicts ([CXC-BE-RAWSPEC]) — the parsed widget tree is never
      re-serialized, so bindings/variables/behaviors survive intact.

An Intelligence editing the design over the document bus updates this
view identically to a human — watch the YAML change as either drives
the canvas ([CXC-CREDO]).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..render.canvas import Canvas, Size
from ..render.widget import Constraints, Rect, Widget

from . import chrome
from .chrome import FONT, MONO, _baseline
from .document import CompositeDocument

logger = logging.getLogger("composeio.composer.source")

_LINE_H = 16.0
_GUTTER = 40.0
_PAD = 8.0
_HEAD_H = 26.0
_STATUS_H = 22.0

_EDITOR_BG = (0x0C, 0x0E, 0x13)
_GUTTER_BG = (0x12, 0x15, 0x1C)
_CURLINE = (0xFF, 0xFF, 0xFF, 0x0A)
_ERRLINE = (0xE5, 0x6D, 0x61, 0x26)
_HILITE = (0x3F, 0xB6, 0xD4, 0x14)

# YAML-ish syntax tint: keys vs values vs comments
_KEY_RE = re.compile(r"^(\s*(?:- )?)([A-Za-z_][A-Za-z0-9_]*)(:)(.*)$")


class SourcePanel(Widget):
    """Multi-line YAML editor over the CompositeDocument."""

    def __init__(self, widget_id: str, core: Any, *,
                 on_popout: Optional[Any] = None, **kwargs: Any) -> None:
        super().__init__(widget_id, "source_panel", **kwargs)
        self.core = core
        self.on_popout = on_popout
        self.interactive = True
        self.accepts_focus = True

        self.lines: List[str] = [""]
        self.cur_row = 0
        self.cur_col = 0
        self.scroll = 0.0
        self.error: Optional[Tuple[Optional[int], str]] = None
        self.highlight_id = ""
        self._blocks: Dict[str, Tuple[int, int]] = {}   # wid -> (start, end)
        self._char_w = 6.6
        self._popout_zone: Optional[Rect] = None

    # -- text model -----------------------------------------------------------

    def text(self) -> str:
        return "\n".join(self.lines)

    def set_text(self, text: str) -> None:
        self.lines = text.split("\n")
        self.cur_row = min(self.cur_row, len(self.lines) - 1)
        self.cur_col = min(self.cur_col, len(self.lines[self.cur_row]))
        self._recompute_blocks()

    # -- doc sync ([CXC-BE-C4]) ---------------------------------------------

    def _doc_equivalent(self) -> bool:
        """Does the buffer parse to the SAME document the core holds?
        When yes the user's exact text is preserved (no normalization
        jump under the cursor)."""
        try:
            return (CompositeDocument.from_yaml(self.text()).to_dict()
                    == self.core.document.to_dict())
        except Exception:
            return False

    def refresh_from_doc(self) -> None:
        """The document changed (canvas edit, Intelligence, our own
        commit): re-project unless the buffer already means the same."""
        if self._doc_equivalent():
            self._recompute_blocks()
            self.request_redraw()
            return
        self.set_text(self.core.document.to_yaml())
        self.error = None
        self.request_redraw()

    # -- widget-block map (selection linkage) ----------------------------------

    def _recompute_blocks(self) -> None:
        self._blocks = {}
        ids = [l.widget_id for l in self.core.document.layers]
        for wid in ids:
            # block style ("  id: alert") OR flow style ("- {type: label,
            # id: alert, ...}") — safe_dump uses flow for small mappings.
            w = re.escape(wid)
            pat = re.compile(r"(?:^\s*(?:- )?id: " + w + r"\s*$)"
                             r"|(?:[{,]\s*id: " + w + r"\s*[,}])")
            for i, line in enumerate(self.lines):
                if not pat.search(line):
                    continue
                # block starts at the nearest preceding "- " item line
                start = i
                for j in range(i, -1, -1):
                    if re.match(r"^\s*- ", self.lines[j]):
                        start = j
                        break
                indent = len(self.lines[start]) - \
                    len(self.lines[start].lstrip())
                end = len(self.lines) - 1
                for j in range(start + 1, len(self.lines)):
                    s = self.lines[j]
                    if not s.strip():
                        continue
                    j_indent = len(s) - len(s.lstrip())
                    if j_indent <= indent:
                        end = j - 1
                        break
                self._blocks[wid] = (start, end)
                break

    def _widget_at_line(self, row: int) -> str:
        for wid, (a, b) in self._blocks.items():
            if a <= row <= b:
                return wid
        return ""

    def on_canvas_selection(self, widget_id: str) -> None:
        """Canvas -> Source: highlight + scroll the widget's block."""
        self.highlight_id = widget_id
        blk = self._blocks.get(widget_id)
        if blk is not None and self.arranged_rect is not None:
            top = self.arranged_rect.y + _HEAD_H
            view_h = self.arranged_rect.h - _HEAD_H - _STATUS_H
            y0 = blk[0] * _LINE_H - self.scroll
            y1 = (blk[1] + 1) * _LINE_H - self.scroll
            if y0 < 0 or y1 > view_h:
                self.scroll = max(0.0, blk[0] * _LINE_H - view_h / 4)
        self.request_redraw()

    # -- editing --------------------------------------------------------------

    def on_text(self, text: str) -> None:
        printable = "".join(ch for ch in text if ch.isprintable())
        if not printable:
            return
        line = self.lines[self.cur_row]
        self.lines[self.cur_row] = (line[:self.cur_col] + printable
                                    + line[self.cur_col:])
        self.cur_col += len(printable)
        self._edited()

    def on_key(self, key: str, modifiers: Any = None) -> None:
        row, col = self.cur_row, self.cur_col
        line = self.lines[row]
        if key in ("Return", "Enter", "Keypad Enter"):
            indent = re.match(r"\s*", line).group(0)
            self.lines[row] = line[:col]
            self.lines.insert(row + 1, indent + line[col:])
            self.cur_row, self.cur_col = row + 1, len(indent)
            self._edited()
        elif key == "Backspace":
            if col > 0:
                self.lines[row] = line[:col - 1] + line[col:]
                self.cur_col -= 1
            elif row > 0:
                prev = self.lines[row - 1]
                self.cur_col = len(prev)
                self.lines[row - 1] = prev + line
                self.lines.pop(row)
                self.cur_row -= 1
            self._edited()
        elif key == "Delete":
            if col < len(line):
                self.lines[row] = line[:col] + line[col + 1:]
            elif row + 1 < len(self.lines):
                self.lines[row] = line + self.lines.pop(row + 1)
            self._edited()
        elif key == "Left":
            if col > 0:
                self.cur_col -= 1
            elif row > 0:
                self.cur_row -= 1
                self.cur_col = len(self.lines[self.cur_row])
        elif key == "Right":
            if col < len(line):
                self.cur_col += 1
            elif row + 1 < len(self.lines):
                self.cur_row += 1
                self.cur_col = 0
        elif key == "Up" and row > 0:
            self.cur_row -= 1
            self.cur_col = min(col, len(self.lines[self.cur_row]))
        elif key == "Down" and row + 1 < len(self.lines):
            self.cur_row += 1
            self.cur_col = min(col, len(self.lines[self.cur_row]))
        elif key == "Home":
            self.cur_col = 0
        elif key == "End":
            self.cur_col = len(line)
        elif key in ("Page Up", "PageUp"):
            self.cur_row = max(0, row - 20)
        elif key in ("Page Down", "PageDown"):
            self.cur_row = min(len(self.lines) - 1, row + 20)
        self._keep_cursor_visible()
        self.request_redraw()

    def _keep_cursor_visible(self) -> None:
        r = self.arranged_rect
        if r is None:
            return
        view_h = r.h - _HEAD_H - _STATUS_H
        cy = self.cur_row * _LINE_H
        if cy - self.scroll < 0:
            self.scroll = cy
        elif cy - self.scroll > view_h - _LINE_H:
            self.scroll = cy - view_h + _LINE_H

    def _edited(self) -> None:
        """Validate every edit; COMMIT only on a valid parse ([CXC-BE-C5])."""
        text = self.text()
        try:
            CompositeDocument.from_yaml(text)
        except Exception as e:
            self.error = (_err_line(e), _err_msg(e))
            try:
                self.core.internal_bus.publish_state(
                    "composer/source/status",
                    {"ok": False, "error": self.error[1],
                     "line": self.error[0]},
                    source="source_panel")
            except Exception:
                logger.exception("source status publish failed")
            self.request_redraw()
            return                          # visual state untouched
        self.error = None
        self._recompute_blocks()
        self.core.internal_bus.publish_event(
            "composer/source/replace", "do", data={"yaml": text},
            source="source_panel")
        self.request_redraw()

    # -- pointer --------------------------------------------------------------

    def _pos_from_point(self, x: float, y: float) -> Tuple[int, int]:
        r = self.arranged_rect
        row = int((y - r.y - _HEAD_H + self.scroll) // _LINE_H)
        row = max(0, min(row, len(self.lines) - 1))
        col = int(round((x - r.x - _GUTTER - _PAD) / max(self._char_w, 1.0)))
        col = max(0, min(col, len(self.lines[row])))
        return row, col

    def on_pointer_down(self, x: float, y: float) -> None:
        r = self.arranged_rect
        if r is None or y < r.y + _HEAD_H:
            return
        self.cur_row, self.cur_col = self._pos_from_point(x, y)
        # Source -> canvas selection linkage ([CXC-BE-C4])
        wid = self._widget_at_line(self.cur_row)
        if wid and wid != self.highlight_id:
            self.highlight_id = wid
            ib = self.core.internal_bus
            ib.publish_state("composer/selection/widget_id", wid,
                             source="source_panel")
            ib.publish_state("composer/selection/type", "widget",
                             source="source_panel")
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        z = self._popout_zone
        if z is not None and self.on_popout is not None and \
                z.x <= x <= z.x + z.w and z.y <= y <= z.y + z.h:
            self.on_popout()

    def on_scroll(self, dx: float, dy: float) -> None:
        max_scroll = max(0.0, len(self.lines) * _LINE_H
                         - (self.arranged_rect.h - _HEAD_H - _STATUS_H
                            if self.arranged_rect else 300))
        self.scroll = max(0.0, min(max_scroll, self.scroll - dy * 32))
        self.request_redraw()

    # -- draw -----------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*_EDITOR_BG)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        # header: doc slug + sync state + pop-out
        canvas.set_fill_color(*chrome.PANEL)
        canvas.fill_rect(rect.x, rect.y, rect.w, _HEAD_H)
        canvas.set_stroke_color(*chrome.BORDER)
        canvas.set_stroke_width(1)
        canvas.draw_line(rect.x, rect.y + _HEAD_H, rect.x + rect.w,
                         rect.y + _HEAD_H)
        canvas.set_fill_color(*chrome.DIM)
        canvas.set_font(MONO, 10.5, "regular")
        canvas.draw_text(f"{self.core.document.slug}.compose.yaml",
                         rect.x + 8, _baseline(canvas, rect.y + _HEAD_H / 2),
                         "left")
        if self.on_popout is not None:
            label = "↗ pop out"
            bw = len(label) * 6.4 + 16
            bx = rect.x + rect.w - bw - 6
            by = rect.y + 3
            self._popout_zone = Rect(bx, by, bw, _HEAD_H - 6)
            canvas.set_fill_color(*chrome.PANEL2)
            canvas.fill_round_rect(bx, by, bw, _HEAD_H - 6, 5)
            canvas.set_stroke_color(*chrome.BORDER_STRONG)
            canvas.stroke_round_rect(bx, by, bw, _HEAD_H - 6, 5)
            canvas.set_fill_color(*chrome.ACCENT)
            canvas.set_font(FONT, 10.5, "medium")
            canvas.draw_text(label, bx + bw / 2,
                             _baseline(canvas, by + (_HEAD_H - 6) / 2),
                             "center")

        # metrics
        canvas.set_font(MONO, 11, "regular")
        self._char_w = max(1.0, canvas.measure_text("MMMMMMMMMM").width / 10)

        top = rect.y + _HEAD_H
        view_h = rect.h - _HEAD_H - _STATUS_H
        first = max(0, int(self.scroll // _LINE_H))
        last = min(len(self.lines) - 1,
                   int((self.scroll + view_h) // _LINE_H) + 1)

        # gutter
        canvas.set_fill_color(*_GUTTER_BG)
        canvas.fill_rect(rect.x, top, _GUTTER, view_h)

        blk = self._blocks.get(self.highlight_id)
        err_line = self.error[0] if self.error else None

        tx = rect.x + _GUTTER + _PAD
        for i in range(first, last + 1):
            ly = top + i * _LINE_H - self.scroll
            if ly + _LINE_H < top or ly > top + view_h:
                continue
            # selection-linked block highlight
            if blk is not None and blk[0] <= i <= blk[1]:
                canvas.set_fill_color(*_HILITE)
                canvas.fill_rect(rect.x + _GUTTER, ly, rect.w - _GUTTER,
                                 _LINE_H)
            if i == self.cur_row and self.focused:
                canvas.set_fill_color(*_CURLINE)
                canvas.fill_rect(rect.x + _GUTTER, ly, rect.w - _GUTTER,
                                 _LINE_H)
            if err_line is not None and i == err_line:
                canvas.set_fill_color(*_ERRLINE)
                canvas.fill_rect(rect.x + _GUTTER, ly, rect.w - _GUTTER,
                                 _LINE_H)
            # line number
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(MONO, 9.5, "regular")
            canvas.draw_text(str(i + 1), rect.x + _GUTTER - 6,
                             ly + _LINE_H - 4.5, "right")
            # text (key tint)
            line = self.lines[i]
            baseline = ly + _LINE_H - 4
            canvas.set_font(MONO, 11, "regular")
            m = _KEY_RE.match(line)
            if m and not line.lstrip().startswith("#"):
                lead, key, colon, rest = m.groups()
                canvas.set_fill_color(*chrome.DIM)
                canvas.draw_text(lead, tx, baseline, "left")
                kx = tx + len(lead) * self._char_w
                canvas.set_fill_color(*chrome.ACCENT)
                canvas.draw_text(key + colon, kx, baseline, "left")
                vx = kx + (len(key) + 1) * self._char_w
                canvas.set_fill_color(*chrome.TEXT)
                canvas.draw_text(rest, vx, baseline, "left")
            else:
                canvas.set_fill_color(
                    *(chrome.FAINT if line.lstrip().startswith("#")
                      else chrome.TEXT))
                canvas.draw_text(line, tx, baseline, "left")
            # cursor caret
            if i == self.cur_row and self.focused:
                cx = tx + self.cur_col * self._char_w
                canvas.set_stroke_color(*chrome.TEXT)
                canvas.set_stroke_width(1)
                canvas.draw_line(cx + 0.5, ly + 2, cx + 0.5,
                                 ly + _LINE_H - 2)

        # status bar
        sy = rect.y + rect.h - _STATUS_H
        canvas.set_fill_color(*chrome.PANEL)
        canvas.fill_rect(rect.x, sy, rect.w, _STATUS_H)
        canvas.set_stroke_color(*chrome.BORDER)
        canvas.draw_line(rect.x, sy, rect.x + rect.w, sy)
        canvas.set_font(MONO, 10, "regular")
        if self.error is not None:
            line_no, msg = self.error
            at = f"line {line_no + 1}: " if line_no is not None else ""
            canvas.set_fill_color(*chrome.CRIT)
            canvas.draw_text(f"✗ {at}{msg}"[:110], rect.x + 8,
                             _baseline(canvas, sy + _STATUS_H / 2), "left")
        else:
            canvas.set_fill_color(*chrome.GOOD)
            canvas.draw_text("✓ valid — synced to canvas", rect.x + 8,
                             _baseline(canvas, sy + _STATUS_H / 2), "left")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.draw_text(f"{self.cur_row + 1}:{self.cur_col + 1}",
                         rect.x + rect.w - 8,
                         _baseline(canvas, sy + _STATUS_H / 2), "right")
        canvas.restore()

        if self.focused:
            self.request_redraw()      # caret liveliness


def _err_msg(e: Exception) -> str:
    return str(e).strip().split("\n")[0][:140] or type(e).__name__


def _err_line(e: Exception) -> Optional[int]:
    mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
    line = getattr(mark, "line", None)
    return int(line) if line is not None else None
