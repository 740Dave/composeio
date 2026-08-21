"""
ComposeUX NightWatchChecklistWidget — the Away-Watch session TASK CHECKLIST
(David 2026-07-22: the Night Watch window should carry "a list of the session's
tasks each tagged done / partial / deferred (distinct visual states)").

A leaf that renders a data-driven task list, one compact row each:

    <marker>  <label>                                   <STATUS>

Three DISTINCT visual states — the marker AND the right-aligned status tag both
carry the state colour, so the state is legible two ways (no reliance on one cue):

  * **done**     — a FILLED ok-green disc with a drawn check + ``DONE`` in ok.
  * **partial**  — a HALF-filled warn-amber ring + ``PARTIAL`` in warn.
  * **deferred** — a faint HOLLOW ring + ``DEFERRED`` in faint (the calm rule,
    [CXC-ICV-TOKENS]: a deferred task is "not yet", never an alarm — never crit).

DATA-DRIVEN.  ONE namespace (default ``fun/nightwatch/tasks``); payload is the
retrospective ``away_watch.tasks`` projection in any of these graceful shapes::

    [{"label": "...", "status": "done|partial|deferred"}, ...]
    {"tasks": [ ... as above ... ]}

A static ``tasks:`` list in the scene spec is the DEFAULT (exactly like a
``label``'s ``text`` default): the window renders it standalone, and a live
publisher can override it on the bus.  All drawing is Canvas-contract only;
``measure_text`` drives truncation.  Damage discipline ([CXC-R-DAMAGE]): every
bus update calls ``self.request_redraw()`` only — damage stays this widget's rect.
"""

from __future__ import annotations

import json
import math
from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct

DEFAULT_NAMESPACE = "fun/nightwatch/tasks"

_STATUSES = ("done", "partial", "deferred")

ROW_H = 27.0          # marker line + breathing room
MARKER_R = 6.0        # marker radius (px)
PAD_X = 4.0
_SCROLL_STEP = 40.0   # px per wheel notch (the away_watch_view idiom)


def _norm_status(s: Any) -> str:
    v = str(s or "").strip().lower()
    return v if v in _STATUSES else "deferred"


class NightWatchChecklistWidget(Widget):
    """The Away-Watch session task checklist (done / partial / deferred)."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 tasks: Optional[List[Any]] = None,
                 font_size: float = 12.5,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "nightwatch_checklist", **kwargs)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.font_size = float(font_size)
        # (label, status) pairs in arrival/declaration order
        self._tasks: List[Tuple[str, str]] = self._coerce(tasks) or []
        # The task count is VARIABLE per watch (a live publisher pushes a
        # different watch's list on the bus) while the WINDOW is a fixed size —
        # so this region SCROLLS its rows within its fixed rect rather than
        # growing.  It never expands the box; overflow is reachable by wheel.
        self.interactive = True                     # so on_scroll routes here
        self._scroll = 0.0
        self._max_scroll = 0.0

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        tasks = self._coerce(value)
        if tasks is None:
            return
        self._tasks = tasks
        self._scroll = 0.0                          # new list -> top of view
        self.request_redraw()                       # [CXC-R-DAMAGE] scoped

    # -- interaction -----------------------------------------------------------

    def hover_cursor(self) -> str:
        # [CXC-R-ENGINE-HOVER] interactive ONLY to catch wheel/scroll — the rows
        # are not tap targets and hold no selectable text, so the click-hand
        # over the whole list would mislead.  Show the arrow.
        return "default"

    def on_scroll(self, dx: float, dy: float) -> None:
        # Wheel the overflowing task list within the fixed rect (no window
        # growth).  Clamp inside draw() knows the real max; guard here.
        if self._max_scroll <= 0:
            return
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll - dy * _SCROLL_STEP))
        if self._scroll != before:
            self.request_redraw()                   # [CXC-R-DAMAGE] scoped

    @staticmethod
    def _coerce(value: Any) -> Optional[List[Tuple[str, str]]]:
        if value is None:
            return None
        if isinstance(value, str):
            s = value.strip()
            if not s or s[0] not in "{[":
                return None
            try:
                value = json.loads(s)
            except (ValueError, TypeError):
                return None
        if isinstance(value, dict):
            value = value.get("tasks")
        if not isinstance(value, list):
            return None
        out: List[Tuple[str, str]] = []
        for it in value:
            if isinstance(it, dict):
                label = str(it.get("label") or it.get("name") or "").strip()
                if label:
                    out.append((label, _norm_status(it.get("status"))))
            elif isinstance(it, str) and it.strip():
                out.append((it.strip(), "deferred"))
        return out

    # -- layout ----------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # Content-sized, NOT growable: height is the true row-stack height
        # (one ROW_H per task); it never inflates to fill max_height.  Width is
        # the assigned column width (a checklist row spans the width it is given
        # — it does not stretch a parent).  In the Night Watch scene the leaf is
        # placed with an explicit fixed w/h, so overflow past that box SCROLLS
        # (on_scroll) rather than resizing anything.
        n = max(1, len(self._tasks))
        return Size(constraints.max_width, n * ROW_H)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- draw ------------------------------------------------------------------

    @staticmethod
    def _state_color(status: str, t):
        if status == "done":
            return t.ok
        if status == "partial":
            return t.warn
        return t.faint                              # deferred — calm, not crit

    def _draw_marker(self, canvas: Canvas, cx: float, cy: float,
                     status: str, t) -> None:
        color = self._state_color(status, t)
        if status == "done":
            canvas.set_fill_color(*color)
            canvas.fill_circle(cx, cy, MARKER_R)
            # a drawn check (Plex ships no ✓; a font fallback forks pixels)
            canvas.set_stroke_color(*t.bg)
            canvas.set_stroke_width(1.6)
            canvas.draw_line(cx - 2.8, cy + 0.2, cx - 0.6, cy + 2.4)
            canvas.draw_line(cx - 0.6, cy + 2.4, cx + 3.0, cy - 2.4)
        elif status == "partial":
            # half-filled amber ring: filled left semicircle + full ring
            canvas.set_fill_color(*color)
            canvas.fill_arc(cx, cy, MARKER_R, math.pi / 2.0, 3.0 * math.pi / 2.0)
            canvas.set_stroke_color(*color)
            canvas.set_stroke_width(1.6)
            canvas.stroke_circle(cx, cy, MARKER_R)
        else:
            # deferred — faint hollow ring
            canvas.set_stroke_color(*color)
            canvas.set_stroke_width(1.4)
            canvas.stroke_circle(cx, cy, MARKER_R)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)

        if not self._tasks:
            self._max_scroll = 0.0
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            canvas.draw_text("— no tasks recorded —",
                             rect.x + PAD_X, rect.y + 14.0, "left")
            canvas.restore()
            return

        # variable rows in a FIXED box -> scroll, never grow ([CXC-R-DAMAGE]).
        content_h = len(self._tasks) * ROW_H
        self._max_scroll = max(0.0, content_h - rect.h)
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))

        # reserve a right gutter for the scrollbar only when it is shown, so a
        # status tag never sits under the thumb.
        sb_gutter = 8.0 if self._max_scroll > 0 else 0.0
        mx = rect.x + PAD_X + MARKER_R + 2.0
        tx = mx + MARKER_R + 12.0
        for i, (label, status) in enumerate(self._tasks):
            ry = rect.y - self._scroll + i * ROW_H
            if ry + ROW_H < rect.y - 1.0:           # scrolled above the view
                continue
            if ry > rect.y + rect.h + 1.0:          # below the view -> done
                break
            cy = ry + ROW_H / 2.0
            self._draw_marker(canvas, mx, cy, status, t)

            color = self._state_color(status, t)
            # right-aligned status tag (tracked UPPERCASE mono, state colour)
            tag = status.upper()
            canvas.set_font(ct.FONT_MONO, 8.5, "bold")
            tag_w = ct.measure_tracked(canvas, tag, 0.8)
            tag_x = rect.x + rect.w - PAD_X - sb_gutter - tag_w
            base = cy + (canvas.measure_text("Ag").ascent
                         - canvas.measure_text("Ag").descent) / 2.0
            ct.draw_tracked(canvas, tag, tag_x, base, 0.8, color)

            # label — ink when done, dim when partial, faint when deferred
            ink = t.ink if status == "done" else (
                t.dim if status == "partial" else t.faint)
            canvas.set_font(ct.FONT_UI, self.font_size,
                            "regular" if status != "done" else "medium")
            lm = canvas.measure_text("Ag")
            lbl = ct.ellipsize(canvas, label, tag_x - tx - 10.0)
            canvas.set_fill_color(*ink)
            canvas.draw_text(lbl, tx, cy + (lm.ascent - lm.descent) / 2.0, "left")

        # scrollbar — only when the list overflows its fixed box (the
        # away_watch_view / deliverable idiom): a thin thumb, never a control
        # that resizes the region.
        if self._max_scroll > 0:
            track_w = 4.0
            sx = rect.x + rect.w - track_w - 1.0
            sy = rect.y + 2.0
            sh = rect.h - 4.0
            canvas.set_fill_color(*t.line)
            canvas.fill_round_rect(sx, sy, track_w, sh, 2.0)
            thumb_h = max(20.0, sh * (rect.h / content_h))
            travel = max(0.0, sh - thumb_h)
            frac = self._scroll / self._max_scroll
            canvas.set_fill_color(*t.faint)
            canvas.fill_round_rect(sx, sy + travel * frac, track_w, thumb_h, 2.0)

        canvas.restore()
