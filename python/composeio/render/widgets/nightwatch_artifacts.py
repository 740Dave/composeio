"""
ComposeUX NightWatchArtifactsWidget — the Night Watch window's ARTIFACTS tab.

A leaf that lists a watch's ``artifacts:[{title,path}]`` — one tappable row each;
tapping a row opens that doc via the SAME doc-viewer intent the rest of the app
uses (``_app/doc/open`` = the artifact's ``path``, [DEC-DOCVIEWER-WINDOW], the seam
``away_watch_view`` already publishes).  The leaf owns presentation + publishes the
open INTENT; a launcher (``doc_viewer_launcher``) owns the window spawn — the
deliverable_card row-tap → opener split.

DATA-DRIVEN off ONE namespace (default ``fun/nightwatch/artifacts``); a static
``artifacts:`` list in the scene spec is the RENDER DEFAULT (like a ``label``'s
``text`` default), so the window draws its night's artifacts standalone and a live
publisher (the away_watch projection's per-watch ``artifacts``) can override on the
bus.  Payload is accepted in either graceful shape::

    [{"title": "...", "path": "..."}, ...]
    {"artifacts": [ ... as above ... ]}

Overflow SCROLLS within the fixed rect (the nightwatch_checklist / away_watch_view
idiom) — never grows the box.  Damage discipline ([CXC-R-DAMAGE]): every bus update
/ interaction calls ``self.request_redraw()`` ONLY.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _open_tri

DEFAULT_NAMESPACE = "fun/nightwatch/artifacts"
DEFAULT_OPEN_NAMESPACE = "_app/doc/open"

ROW_H = 40.0
PAD_X = 4.0
_SCROLL_STEP = 40.0


class NightWatchArtifactsWidget(Widget):
    """The Night Watch artifacts list (tap a row -> open the doc)."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 artifacts: Optional[List[Any]] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "nightwatch_artifacts", **kwargs)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.interactive = True
        self._arts: List[Dict[str, str]] = self._coerce(artifacts) or []
        self._scroll = 0.0
        self._max_scroll = 0.0
        self._row_zones: List[Tuple[Rect, str]] = []

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        arts = self._coerce(value)
        if arts is None:
            return
        self._arts = arts
        self._scroll = 0.0
        self.request_redraw()                       # [CXC-R-DAMAGE] scoped

    @staticmethod
    def _coerce(value: Any) -> Optional[List[Dict[str, str]]]:
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
            value = value.get("artifacts")
        if not isinstance(value, list):
            return None
        out: List[Dict[str, str]] = []
        for it in value:
            if isinstance(it, dict) and it.get("path"):
                out.append({"title": str(it.get("title") or it["path"]),
                            "path": str(it["path"])})
        return out

    # -- interaction -----------------------------------------------------------

    def on_scroll(self, dx: float, dy: float) -> None:
        if self._max_scroll <= 0:
            return
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll - dy * _SCROLL_STEP))
        if self._scroll != before:
            self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        for r, path in self._row_zones:
            if r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h:
                if path:
                    self.publish(self.open_namespace, path)
                return

    # -- layout ----------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._row_zones = []
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)

        if not self._arts:
            self._max_scroll = 0.0
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            canvas.draw_text("— no artifacts recorded —",
                             rect.x + rect.w / 2, rect.y + rect.h / 2, "center")
            canvas.restore()
            return

        content_h = len(self._arts) * ROW_H
        self._max_scroll = max(0.0, content_h - rect.h)
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))

        sb_gutter = 8.0 if self._max_scroll > 0 else 0.0
        for i, art in enumerate(self._arts):
            ry = rect.y - self._scroll + i * ROW_H
            self._row_zones.append((Rect(rect.x, ry, rect.w, ROW_H),
                                    art["path"]))
            if ry + ROW_H < rect.y - 1.0 or ry > rect.y + rect.h + 1.0:
                continue
            self._draw_row(canvas, art, rect.x, ry, rect.w - sb_gutter, t)

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

    def _draw_row(self, canvas: Canvas, art: Dict[str, str], x: float,
                  y: float, w: float, t) -> None:
        cy = y + ROW_H / 2.0
        # leading ▸ open affordance (accent), vertically centered on the row
        _open_tri(canvas, x + PAD_X + 5, cy, 6.0, 9.0, t.accent)
        # title (bold ink)
        tx = x + PAD_X + 20
        canvas.set_font(ct.FONT_UI, 12.5, "bold")
        nm = canvas.measure_text("Ag")
        title = ct.ellipsize(canvas, art["title"], w - (tx - x) - PAD_X)
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(title, tx, cy - 3 + (nm.ascent - nm.descent) / 2.0,
                         "left")
        # path (mono dim, one line)
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        pm = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(ct.ellipsize(canvas, art["path"], w - (tx - x) - PAD_X),
                         tx, cy + 11 + (pm.ascent - pm.descent) / 2.0, "left")
        # row separator
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(x + PAD_X, y + ROW_H - 0.5, x + w - PAD_X,
                         y + ROW_H - 0.5)
