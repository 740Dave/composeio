"""
ComposeUX DeliverableCardWidget — the LOCKED compact "Deliverables" rail
card ([DEC-DELIVERABLE-HISTORY-WIDGET]).

The design is FROZEN to the previs
``docs/intelligence-console/previs/deliverable-compact.html`` (David:
"Lock it!"): a 332px rail card carrying

  * a header row — azure presence dot, "Deliverables" (sans 600), and the
    small bordered "Artifacts ▸" affordance (publishes the tab namespace;
    the console-level ConsoleTabWidget subscribes it and switches to the
    Artifacts view),
  * the 3-cell deliverables GAUGE — NEEDS ENRICH (warn) · NEW·SESSION
    (azure) · TRACKED (muted),
  * the last-5 rows — a 6px kind-colored dot, the filename (BOLD ink when
    ``unseen`` — not opened since last modified — plain dim once seen),
    and a right-aligned ▸ open affordance.  Row padding kept (previs:
    "breathing room between filenames").

DATA-DRIVEN off ONE bus namespace (``self/deliverables``, published by
``compose_projects/intelligence/publisher/deliverable_publisher.py``) — state on the bus,
behavior in the widget; the widget never reads the ledger.  Payload:

    {"stats": {"needs_enrich": N, "new_session": N, "tracked": N},
     "recent": [{"name", "path", "kind", "modified", "unseen"}, ...],
     "session": "..."}

Tapping a row publishes ``_app/deliverable/open`` = the row's ``path``
(the declarative tap seam).  The actual open actuation + the
opened-state stamp (which flips ``unseen`` on the next publish) are the
documented FOLLOW-ON — this widget only emits the tap.

Colors come from the console token tables (``console_theme``, theme-
aware) by ROLE; the four kind hues are CATEGORY colors (like the chat
voice colors), not state — previs ``--violet`` carried verbatim.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct

DEFAULT_NAMESPACE = "self/deliverables"
DEFAULT_OPEN_NAMESPACE = "_app/deliverable/open"
DEFAULT_TAB_NAMESPACE = "_app/console/tab"

# previs geometry (px), shared by draw() AND on_tap() so the hit regions
# are exactly the drawn ones.
HEADER_H = 42.0            # 13 pad-top + 18 title line + 11 pad-bottom
STATS_H = 65.0             # 2 top + 50 cell + 13 bottom
ROWS_TOP = HEADER_H + STATS_H + 4.0   # rows container pad-top 4
ROW_H = 34.0               # 9 + ~16 line + 9 (padding kept per the lock)
ROWS_PAD_BOTTOM = 12.0
PAD_X = 14.0               # header/stats horizontal padding
ROW_PAD_X = 8.0            # rows container horizontal padding

# previs --violet — a KIND hue with no console-theme role (category, not
# state; the semantic rule keeps ok/warn/crit for state only).
VIOLET = (0x9A, 0x86, 0xFF, 255)


def _open_tri(canvas: Canvas, x: float, cy: float, w: float, h: float,
              color) -> None:
    """The ▸ open affordance as a FILLED PATH — the bundled Plex faces do
    not cover U+25B8, and a system-font fallback would fork pixels per
    platform ([CXC-R-DP-1]: decompose to the contract, never a font)."""
    canvas.set_fill_color(*color)
    canvas.fill_path([MoveTo(x, cy - h / 2.0),
                      LineTo(x + w, cy),
                      LineTo(x, cy + h / 2.0),
                      Close()])


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        s = value.strip()
        if s and s[0] in "{[":
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return value
    return value


class DeliverableCardWidget(Widget):
    """The compact deliverables rail card (LOCKED previs)."""

    def __init__(self, widget_id: str, *,
                 label: str = "Deliverables",
                 namespace: str = DEFAULT_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 tab_namespace: str = DEFAULT_TAB_NAMESPACE,
                 chip_label: str = "Artifacts",
                 header_dot: bool = True,
                 max_rows: int = 5,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "deliverable_card", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        # the jump-chip caption ([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION]): the
        # Artifacts rail card reads "All ▸" (the card IS titled Artifacts, so
        # the chip names the destination breadth, not the tab); default keeps
        # the historical "Artifacts ▸".
        self.chip_label = str(chip_label or "Artifacts")
        # the leading presence dot ([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION]):
        # the consolidated Artifacts card header carries NO leading dot (the
        # other two consolidated cards don't either); default keeps it.
        self.header_dot = bool(header_dot)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.tab_namespace = str(tab_namespace or DEFAULT_TAB_NAMESPACE)
        self.max_rows = int(max_rows)
        self.interactive = True          # row taps + the Artifacts ▸ chip
        self._stats: Dict[str, Any] = {}
        self._rows: List[Dict[str, Any]] = []
        self._session: str = ""
        # the chip rect is measure_text-dependent; draw() records it so
        # on_tap hits exactly the drawn affordance.
        self._chip_rect: Optional[Tuple[float, float, float, float]] = None

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if not isinstance(payload, dict):
            return
        stats = payload.get("stats")
        if isinstance(stats, dict):
            self._stats = stats
        recent = payload.get("recent")
        if isinstance(recent, list):
            self._rows = [r for r in recent if isinstance(r, dict)]
            self._rows = self._rows[:self.max_rows]
        self._session = str(payload.get("session", self._session))
        # [CXC-R-DAMAGE] scoped damage only — never mark_dirty/add_full.
        self.request_redraw()

    # -- derivation --------------------------------------------------------------

    def _stat_cells(self):
        """[(value text, LABEL, value color, border color)] for the 3-cell
        gauge — warn / accent / muted per the previs; missing stats -> em-dash
        (graceful, and the warn border only when the count is real)."""
        t = ct.CONSOLE.active

        def v(key):
            n = self._stats.get(key)
            return str(n) if isinstance(n, (int, float)) else "—"

        needs = v("needs_enrich")
        warn_border = (t.warn[0], t.warn[1], t.warn[2], 71) \
            if needs not in ("—", "0") else t.line
        return [
            (needs, "NEEDS ENRICH", t.warn, warn_border),
            (v("new_session"), "NEW · SESSION", t.accent, t.line),
            (v("tracked"), "TRACKED", t.dim, t.line),
        ]

    @staticmethod
    def _kind_color(kind: str, t) -> Tuple[int, int, int, int]:
        k = str(kind or "").strip().lower()
        if k == "previs":
            return VIOLET
        if k == "brief":
            return t.ok
        if k == "primer":
            return t.accent
        return t.faint       # report / un-enriched "—" / unknown

    # -- geometry (shared by draw + hit) -----------------------------------------

    def _row_rect(self, rect: Rect, i: int) -> Rect:
        return Rect(rect.x + ROW_PAD_X, rect.y + ROWS_TOP + i * ROW_H,
                    rect.w - 2 * ROW_PAD_X, ROW_H)

    def _row_at(self, x: float, y: float) -> Optional[int]:
        r = self.arranged_rect
        if r is None:
            return None
        for i in range(len(self._rows)):
            rr = self._row_rect(r, i)
            if rr.y + rr.h > r.y + r.h - 2:      # clipped row is not tappable
                return None
            if rr.x <= x <= rr.x + rr.w and rr.y <= y <= rr.y + rr.h:
                return i
        return None

    # -- interaction ---------------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        r = self.arranged_rect
        if r is None:
            return
        chip = self._chip_rect
        if chip is None and y <= r.y + HEADER_H:
            # pre-first-draw fallback: the header's right end IS the chip zone
            chip = (r.x + r.w - 110, r.y, 110, HEADER_H)
        if chip and chip[0] <= x <= chip[0] + chip[2] \
                and chip[1] <= y <= chip[1] + chip[3]:
            # console-level tab seam — LIVE: ConsoleTabWidget subscribes
            # this and switches to the Artifacts view.
            self.publish(self.tab_namespace, "artifacts")
            return
        idx = self._row_at(x, y)
        if idx is not None:
            path = str(self._rows[idx].get("path", ""))
            if path:
                # FOLLOW-ON: a handler subscribes this, runs the open, and
                # stamps the opened-state file the publisher reads — which
                # flips `unseen` (bold -> plain) on its next publish.  This
                # slice emits the tap only; seen-state stays on the bus.
                self.publish(self.open_namespace, path)

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        n = len(self._rows) or self.max_rows
        need = ROWS_TOP + n * ROW_H + ROWS_PAD_BOTTOM
        return Size(min(332.0, constraints.max_width),
                    min(constraints.max_height, need))

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h,
                     ct.RADIUS_CARD, t)

        # ── header: dot · Deliverables · [Artifacts ▸] ──
        head_cy = rect.y + HEADER_H / 2.0 + 1.0
        title_x = rect.x + PAD_X + (17 if self.header_dot else 0)
        if self.header_dot:
            ct.draw_status_dot(canvas, rect.x + PAD_X + 4, head_cy, 4.0,
                               t.accent, halo=t.accent_dim, halo_w=4.0)
        # Title in the SAME face/size/treatment as every other rail card header
        # (tracked UPPERCASE mono in ink) rather than 14px bold sans — David's
        # glass call 2026-07-21: this card was the odd one out. The shared
        # `ct.card_header` helper isn't used directly because this header also
        # carries the presence dot and the Artifacts ▸ chip; matching the type
        # treatment is what consistency requires here.
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        base = head_cy + (canvas.measure_text("Ag").ascent
                          - canvas.measure_text("Ag").descent) / 2.0
        ct.draw_tracked(canvas, self.label.upper(), title_x, base,
                        ct.TRACK_LABEL, t.ink)
        # the Artifacts ▸ chip (bordered, azure — previs .all); the ▸ is a
        # drawn path (Plex has no U+25B8 — see _open_tri).
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        chip_label = self.chip_label
        tw = canvas.measure_text(chip_label).width
        tri_w, tri_gap = 4.5, 5.0
        ch_w, ch_h = tw + tri_gap + tri_w + 16.0, 20.0
        ch_x = rect.x + rect.w - PAD_X - ch_w
        ch_y = head_cy - ch_h / 2.0
        canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 71)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(ch_x, ch_y, ch_w, ch_h, 6.0)
        canvas.set_fill_color(*t.accent)
        m = canvas.measure_text(chip_label)
        canvas.draw_text(chip_label, ch_x + 8,
                         head_cy + (m.ascent - m.descent) / 2.0, "left")
        _open_tri(canvas, ch_x + 8 + tw + tri_gap, head_cy, tri_w, 7.0,
                  t.accent)
        self._chip_rect = (ch_x - 4, ch_y - 4, ch_w + 8, ch_h + 8)

        # ── the 3-cell deliverables gauge ──
        cells = self._stat_cells()
        gap = 8.0
        cell_w = (rect.w - 2 * PAD_X - 2 * gap) / 3.0
        cell_y = rect.y + HEADER_H + 2.0
        cell_h = 50.0
        for i, (val, lab, vcol, bcol) in enumerate(cells):
            cx = rect.x + PAD_X + i * (cell_w + gap)
            canvas.set_fill_color(*t.panel2)
            canvas.fill_round_rect(cx, cell_y, cell_w, cell_h, 9.0)
            canvas.set_stroke_color(*bcol)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(cx + 0.5, cell_y + 0.5,
                                     cell_w - 1, cell_h - 1, 9.0)
            canvas.set_font(ct.FONT_MONO, 19.0, "bold")
            vm = canvas.measure_text(val)
            canvas.set_fill_color(*vcol)
            canvas.draw_text(val, cx + 10, cell_y + 8 + vm.ascent, "left")
            canvas.set_font(ct.FONT_MONO, 9.0, "regular")
            lm = canvas.measure_text("Ag")
            ct.draw_tracked(canvas, lab, cx + 10,
                            cell_y + 8 + vm.ascent + vm.descent + 4
                            + lm.ascent, 0.6, t.faint)

        # ── last-5 rows: kind dot · filename (BOLD when unseen) · ▸ ──
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h,
                               ct.RADIUS_CARD)
        for i, row in enumerate(self._rows):
            rr = self._row_rect(rect, i)
            if rr.y + rr.h > rect.y + rect.h - 2:
                break
            cy = rr.y + rr.h / 2.0
            canvas.set_fill_color(*self._kind_color(row.get("kind"), t))
            canvas.fill_circle(rr.x + ROW_PAD_X + 3, cy, 3.0)
            # open affordance, right-aligned (previs .op) — drawn path
            op_w = 5.0
            op_x = rr.x + rr.w - ROW_PAD_X - op_w
            _open_tri(canvas, op_x, cy, op_w, 8.0, t.faint)
            # filename — BOLD ink when unseen, plain dim once seen
            unseen = bool(row.get("unseen"))
            canvas.set_font(ct.FONT_UI, 13.0,
                            "bold" if unseen else "regular")
            nm_x = rr.x + ROW_PAD_X + 3 + 3 + 10
            name = ct.ellipsize(canvas, str(row.get("name", "?")),
                                op_x - 6 - nm_x)
            nm = canvas.measure_text(name)
            canvas.set_fill_color(*(t.ink if unseen else t.dim))
            canvas.draw_text(name, nm_x,
                             cy + (nm.ascent - nm.descent) / 2.0, "left")
        canvas.restore()

        if not self._rows:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.draw_text("awaiting deliverable sweep…",
                             rect.x + rect.w / 2,
                             rect.y + ROWS_TOP
                             + (rect.h - ROWS_TOP) / 2, "center")
        canvas.restore()
