"""
ComposeUX DefectsSummaryWidget — the Dashboard "Defects" card
([SPEC:CIO-DEFECT-REGISTRY]): a read-only summary of the Defect Registry roll-up,
so the process-improvement signal (which discipline to tighten) and the
fix-resistant leaders (which defects keep re-breaking) are visible at a glance.

DATA-DRIVEN off ONE namespace (``self/architect/defects``, the
``defect_metrics_publisher`` projection) — state on the bus, behavior in the
widget; it never reads the SQLite/graph directly (the perceive/present split).
Payload:

    {"total": <int>,                       # total defect investigations
     "by_root_cause": {"under-spec": n, "fork-divergence": n, ...},
     "structural": {"under_spec": n, "fork": n, "recurrence": n},
     "recurrence_rate": <0..1>,
     "fix_resistant": [{"symptom", "attempts", "status"}, ...]}

Rendered: TOTAL defects · a 3-cell root-cause gauge (UNDER-SPEC · FORK ·
RECURRENCE) · the recurrence rate · the fix-resistant leaders (symptom + attempt
count + status) — the "5 attempts all touched X" pattern a blind re-dig misses.

A dashboard-category widget: ``region_affinity {dashboard}`` + a dashboard-sized
``min_size`` steer it onto the composable Dashboard grid (the fit-aware placement,
[DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1).

Damage discipline ([CXC-R-DAMAGE]): every bus update calls
``self.request_redraw()`` ONLY, and only on a REAL content change — an always-
visible dashboard card must not turn a retained-value redelivery into a
continuous-damage source.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce

DEFAULT_NAMESPACE = "self/architect/defects"

HEADER_H = 42.0
GAUGE_TOP = HEADER_H + 2.0
GAUGE_H = 50.0
RATE_H = 22.0
LEADERS_TOP = GAUGE_TOP + GAUGE_H + RATE_H + 6.0
LEADER_ROW_H = 22.0
PAD_X = 14.0
MAX_LEADERS = 4


class DefectsSummaryWidget(Widget):
    """The Dashboard Defects card — the Defect Registry roll-up at a glance."""

    def __init__(self, widget_id: str, *,
                 label: str = "Defects",
                 namespace: str = DEFAULT_NAMESPACE,
                 header_dot: bool = True,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "defects_summary", **kwargs)
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.header_dot = bool(header_dot)
        self._total = 0
        self._by_root_cause: Dict[str, int] = {}
        self._structural: Dict[str, int] = {}
        self._recurrence_rate = 0.0
        self._fix_resistant: List[Dict[str, Any]] = []

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if not isinstance(payload, dict):
            return
        total = int(payload.get("total") or 0)
        brc = payload.get("by_root_cause")
        brc = {str(k): int(v) for k, v in brc.items()} if isinstance(brc, dict) else {}
        st = payload.get("structural")
        st = {str(k): int(v) for k, v in st.items()} if isinstance(st, dict) else {}
        try:
            rate = float(payload.get("recurrence_rate") or 0.0)
        except (TypeError, ValueError):
            rate = 0.0
        leaders = self._coerce_leaders(payload.get("fix_resistant"))
        # [CXC-R-DAMAGE-ONCHANGE] damage ONLY on a real content change (an
        # idempotent redelivery — retained-value replay, publisher interval —
        # must not repaint an always-visible card).
        if (total == self._total and brc == self._by_root_cause
                and st == self._structural and rate == self._recurrence_rate
                and leaders == self._fix_resistant):
            return
        self._total = total
        self._by_root_cause = brc
        self._structural = st
        self._recurrence_rate = rate
        self._fix_resistant = leaders
        self.request_redraw()

    @staticmethod
    def _coerce_leaders(value: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(value, list):
            return out
        for it in value:
            if not isinstance(it, dict):
                continue
            out.append({
                "symptom": str(it.get("symptom") or ""),
                "attempts": int(it.get("attempts") or 0),
                "status": str(it.get("status") or ""),
            })
        return out

    # -- derivation ------------------------------------------------------------

    def _stat_cells(self):
        t = ct.CONSOLE.active
        us = self._structural.get("under_spec", 0)
        fk = self._structural.get("fork", 0)
        rc = self._structural.get("recurrence", 0)
        has = self._total > 0
        return [
            (str(us) if has else "—", "UNDER-SPEC", t.warn),
            (str(fk) if has else "—", "FORK", t.accent),
            (str(rc) if has else "—", "RECURRENCE", t.ink),
        ]

    def _rate_str(self) -> str:
        return f"{round(self._recurrence_rate * 100)}% recur" if self._total else "—"

    # -- lifecycle / layout ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        need = LEADERS_TOP + LEADER_ROW_H * max(1, min(len(self._fix_resistant),
                                                       MAX_LEADERS)) + 14.0
        return Size(min(420.0, constraints.max_width),
                    min(constraints.max_height, max(220.0, need)))

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD, t)

        # ── header: dot · DEFECTS · [NN% recur] · total ──
        head_cy = rect.y + HEADER_H / 2.0 + 1.0
        title_x = rect.x + PAD_X + (17 if self.header_dot else 0)
        if self.header_dot:
            ct.draw_status_dot(canvas, rect.x + PAD_X + 4, head_cy, 4.0,
                               t.warn, halo=t.warn, halo_w=4.0)
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        m = canvas.measure_text("Ag")
        base = head_cy + (m.ascent - m.descent) / 2.0
        ct.draw_tracked(canvas, self.label.upper(), title_x, base, ct.TRACK_LABEL, t.ink)
        # total-defects readout, right-aligned in the header
        total_str = f"{self._total} INVESTIGATED"
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        tm = canvas.measure_text(total_str)
        ct.draw_tracked(canvas, total_str,
                        rect.x + rect.w - PAD_X - tm.width,
                        head_cy + (m.ascent - m.descent) / 2.0, 0.6, t.faint)

        # ── the 3-cell root-cause gauge (UNDER-SPEC · FORK · RECURRENCE) ──
        cells = self._stat_cells()
        gap = 8.0
        cell_w = (rect.w - 2 * PAD_X - 2 * gap) / 3.0
        cell_y = rect.y + GAUGE_TOP
        for i, (val, lab, vcol) in enumerate(cells):
            cx = rect.x + PAD_X + i * (cell_w + gap)
            canvas.set_fill_color(*t.panel2)
            canvas.fill_round_rect(cx, cell_y, cell_w, GAUGE_H, 9.0)
            canvas.set_stroke_color(*t.line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(cx + 0.5, cell_y + 0.5, cell_w - 1, GAUGE_H - 1, 9.0)
            canvas.set_font(ct.FONT_MONO, 19.0, "bold")
            vm = canvas.measure_text(val)
            canvas.set_fill_color(*vcol)
            canvas.draw_text(val, cx + 10, cell_y + 8 + vm.ascent, "left")
            canvas.set_font(ct.FONT_MONO, 8.0, "regular")
            lm = canvas.measure_text("Ag")
            ct.draw_tracked(canvas, lab, cx + 10,
                            cell_y + 8 + vm.ascent + vm.descent + 4 + lm.ascent,
                            0.5, t.faint)

        # ── recurrence rate line ──
        rate_y = cell_y + GAUGE_H + RATE_H / 2.0
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        rm = canvas.measure_text("Ag")
        ct.draw_tracked(canvas, "RECURRENCE RATE", rect.x + PAD_X,
                        rate_y + rm.ascent / 2.0, 0.6, t.faint)
        rate_str = self._rate_str()
        canvas.set_font(ct.FONT_MONO, 11.0, "bold")
        rrm = canvas.measure_text(rate_str)
        col = t.warn if self._recurrence_rate >= 0.34 else t.ink
        canvas.set_fill_color(*col)
        canvas.draw_text(rate_str, rect.x + rect.w - PAD_X - rrm.width,
                         rate_y + rrm.ascent / 2.0, "left")

        # ── fix-resistant leaders ──
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD)
        ly = rect.y + LEADERS_TOP
        canvas.set_font(ct.FONT_MONO, 8.5, "regular")
        hm = canvas.measure_text("Ag")
        ct.draw_tracked(canvas, "FIX-RESISTANT (≥2 ATTEMPTS)", rect.x + PAD_X,
                        ly + hm.ascent, 0.6, t.faint)
        ly += 16.0
        if self._fix_resistant:
            for row in self._fix_resistant[:MAX_LEADERS]:
                cy = ly + LEADER_ROW_H / 2.0
                badge = f"×{row['attempts']}"
                status = (row["status"] or "").upper()
                # attempt-count badge (warn), symptom (ink, ellipsized), status (dim)
                canvas.set_font(ct.FONT_MONO, 10.0, "bold")
                bm = canvas.measure_text(badge)
                canvas.set_fill_color(*t.warn)
                canvas.draw_text(badge, rect.x + PAD_X, cy + bm.ascent / 2.0, "left")
                status_w = 0.0
                if status:
                    canvas.set_font(ct.FONT_MONO, 8.5, "regular")
                    sm = canvas.measure_text(status)
                    status_w = sm.width + 8.0
                    scol = t.warn if status == "RECURRED" else t.dim
                    canvas.set_fill_color(*scol)
                    canvas.draw_text(status, rect.x + rect.w - PAD_X - sm.width,
                                     cy + sm.ascent / 2.0, "left")
                sym_x = rect.x + PAD_X + bm.width + 8.0
                avail = rect.w - PAD_X - sym_x - status_w
                canvas.set_font(ct.FONT_UI, 12.0, "regular")
                sym = ct.ellipsize(canvas, row["symptom"] or "(unnamed)", avail)
                ym = canvas.measure_text(sym)
                canvas.set_fill_color(*t.ink)
                canvas.draw_text(sym, sym_x, cy + ym.ascent / 2.0, "left")
                ly += LEADER_ROW_H
        else:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            msg = ("no fix-resistant defects" if self._total
                   else "awaiting self/…/defects…")
            canvas.draw_text(msg, rect.x + rect.w / 2, ly + LEADER_ROW_H / 2, "center")
        canvas.restore()
        canvas.restore()
