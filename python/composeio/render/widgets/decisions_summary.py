"""
ComposeUX DecisionsSummaryWidget — the COMPACT "Decisions" rail card
([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D4, the P4 pattern: compact view on the
rail, full view on the tab, SAME bus state).

The full decision record is area-hungry — the Decisions Explorer tab (#54/#56)
and the Decisions Graph DAG both need a large canvas and are steered OFF the
332px rail by their ``region_affinity`` (D1).  "Decisions on the rail" is
therefore a NEW compact card, not a shrunk DAG: it reads the SAME
``self/decisions`` projection the tab reads and surfaces the three things that
fit a rail card —

  * a 3-cell gauge — OPEN (active decisions, accent) · SUPERSEDED (dim) ·
    TOTAL (ink);
  * the LATEST call — the anchor decision's title (bold ink), with its
    lifecycle chip (superseded dims + strikes, like the graph);
  * FRESHNESS — the newest decision session stamp.

DATA-DRIVEN off ONE namespace (``self/decisions``, the
``decisions_publisher.py`` MAGMA projection) — state on the bus, behavior in the
widget; it never re-queries MAGMA (the perceive/present split).  Payload:

    {"nodes": [{"id", "title", "lifecycle"/"lifecycle-position",
                "superseded", "is_anchor", "session"}, ...],
     "edges": [...], "anchor": "<id>", "corpus": "..."}

The header JUMP CHIP ("Explore ▸") publishes ``tab_namespace`` (default
``_app/console/tab``) = ``jump_tab`` (default ``"decisions"``); the console-level
ConsoleTabWidget subscribes it and switches to the full Decisions tab — the
exact seam the Artifacts ▸ chip uses (reuse by reference, not a new mechanism).

Damage discipline ([CXC-R-DAMAGE]): every bus update / tap calls
``self.request_redraw()`` ONLY — damage stays this card's rect.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce, _open_tri

DEFAULT_NAMESPACE = "self/decisions"
DEFAULT_TAB_NAMESPACE = "_app/console/tab"
DEFAULT_JUMP_TAB = "decisions"

# previs-parity geometry (px), shared by draw() AND on_tap() so hit regions are
# the drawn ones (the deliverable_card idiom).
HEADER_H = 42.0
STATS_H = 65.0
LATEST_TOP = HEADER_H + STATS_H + 4.0
LATEST_H = 34.0
FRESH_H = 20.0
PAD_X = 14.0


def _norm_lifecycle(v: Any) -> str:
    """lifecycle-position token, stripped of any trailing inline `# ...` comment
    the parser kept (`superseded   # applies to ...` -> `superseded`)."""
    return str(v or "").split("#", 1)[0].strip().lower()


class DecisionsSummaryWidget(Widget):
    """The compact decisions rail card (P4 compact view; the tab holds detail)."""

    def __init__(self, widget_id: str, *,
                 label: str = "Decisions",
                 namespace: str = DEFAULT_NAMESPACE,
                 tab_namespace: str = DEFAULT_TAB_NAMESPACE,
                 jump_tab: str = DEFAULT_JUMP_TAB,
                 chip_label: str = "Explore",
                 header_dot: bool = True,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "decisions_summary", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.tab_namespace = str(tab_namespace or DEFAULT_TAB_NAMESPACE)
        self.jump_tab = str(jump_tab or DEFAULT_JUMP_TAB)
        self.chip_label = str(chip_label or "Explore")
        self.header_dot = bool(header_dot)
        self.interactive = True          # the Explore ▸ jump chip
        self._nodes: List[Dict[str, Any]] = []
        self._anchor: str = ""
        self._corpus: str = ""
        self._chip_rect: Optional[Tuple[float, float, float, float]] = None

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if not isinstance(payload, dict):
            return
        nodes = self._coerce_nodes(payload.get("nodes"))
        anchor = str(payload.get("anchor") or self._anchor)
        corpus = str(payload.get("corpus") or self._corpus)
        # [CXC-R-DAMAGE-ONCHANGE] damage ONLY on a real content change.  This
        # projection card renders an idempotent SNAPSHOT, so a redelivery of an
        # unchanged projection — the retained-value replay every subscription
        # gets at mount / on a W2 hot-add re-bind ([CXC-ICP-REBIND]), a family/
        # pattern fan-out, or the decisions_publisher re-emitting the same graph
        # on its interval — must NOT request a redraw.  An always-visible rail
        # card that repainted on every such redelivery is a continuous-damage
        # source: the render loop never idles ([CXC-R-DAMAGE]).  The stored
        # state is byte-identical either way, so the gate is pixel-neutral.
        if nodes == self._nodes and anchor == self._anchor \
                and corpus == self._corpus:
            return
        self._nodes = nodes
        self._anchor = anchor
        self._corpus = corpus
        # [CXC-R-DAMAGE] scoped damage only (this card's own rect).
        self.request_redraw()

    @staticmethod
    def _coerce_nodes(value: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(value, list):
            return out
        for it in value:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("id") or "").strip()
            if not nid:
                continue
            lifecycle = _norm_lifecycle(it.get("lifecycle")
                                        or it.get("lifecycle-position")
                                        or it.get("lifecycle_position"))
            superseded = bool(it.get("superseded")) or lifecycle == "superseded"
            out.append({
                "id": nid,
                "title": str(it.get("title") or ""),
                "lifecycle": lifecycle,
                "superseded": superseded,
                "is_anchor": bool(it.get("is_anchor")),
                "session": str(it.get("session") or ""),
            })
        return out

    # -- derivation ------------------------------------------------------------

    def _counts(self) -> Tuple[int, int, int]:
        """(open, superseded, total): open = active (not superseded)."""
        total = len(self._nodes)
        superseded = sum(1 for n in self._nodes if n["superseded"])
        return total - superseded, superseded, total

    def _latest(self) -> Optional[Dict[str, Any]]:
        """The decision the card foregrounds: the projection ANCHOR (the query
        frame's centre) if present, else the first node (stable order)."""
        if not self._nodes:
            return None
        for n in self._nodes:
            if n["is_anchor"] or n["id"] == self._anchor:
                return n
        return self._nodes[0]

    def _freshness(self) -> str:
        """The newest decision session stamp across the neighborhood (the
        sessions are ISO-ish `YYYY-...` keys, so max() reads chronological)."""
        sessions = [n["session"] for n in self._nodes if n["session"]]
        return max(sessions) if sessions else ""

    def _stat_cells(self):
        t = ct.CONSOLE.active
        opn, sup, total = self._counts()
        return [
            (str(opn) if total else "—", "OPEN", t.accent),
            (str(sup) if total else "—", "SUPERSEDED", t.dim),
            (str(total) if total else "—", "TOTAL", t.ink),
        ]

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        r = self.arranged_rect
        if r is None:
            return
        chip = self._chip_rect
        if chip is None and y <= r.y + HEADER_H:
            chip = (r.x + r.w - 110, r.y, 110, HEADER_H)
        if chip and chip[0] <= x <= chip[0] + chip[2] \
                and chip[1] <= y <= chip[1] + chip[3]:
            # console-level tab seam — LIVE: ConsoleTabWidget subscribes this and
            # switches to the full Decisions tab (the Artifacts ▸ chip idiom).
            self.publish(self.tab_namespace, self.jump_tab)

    # -- lifecycle / layout ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        need = LATEST_TOP + LATEST_H + FRESH_H + 12.0
        return Size(min(332.0, constraints.max_width),
                    min(constraints.max_height, need))

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD, t)

        # ── header: dot · Decisions · [Explore ▸] ──
        head_cy = rect.y + HEADER_H / 2.0 + 1.0
        title_x = rect.x + PAD_X + (17 if self.header_dot else 0)
        if self.header_dot:
            ct.draw_status_dot(canvas, rect.x + PAD_X + 4, head_cy, 4.0,
                               t.accent, halo=t.accent_dim, halo_w=4.0)
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        base = head_cy + (canvas.measure_text("Ag").ascent
                          - canvas.measure_text("Ag").descent) / 2.0
        ct.draw_tracked(canvas, self.label.upper(), title_x, base,
                        ct.TRACK_LABEL, t.ink)
        # the Explore ▸ jump chip (bordered azure; ▸ is a drawn path — Plex has
        # no U+25B8, see _open_tri).
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        tw = canvas.measure_text(self.chip_label).width
        tri_w, tri_gap = 4.5, 5.0
        ch_w, ch_h = tw + tri_gap + tri_w + 16.0, 20.0
        ch_x = rect.x + rect.w - PAD_X - ch_w
        ch_y = head_cy - ch_h / 2.0
        canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 71)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(ch_x, ch_y, ch_w, ch_h, 6.0)
        canvas.set_fill_color(*t.accent)
        m = canvas.measure_text(self.chip_label)
        canvas.draw_text(self.chip_label, ch_x + 8,
                         head_cy + (m.ascent - m.descent) / 2.0, "left")
        _open_tri(canvas, ch_x + 8 + tw + tri_gap, head_cy, tri_w, 7.0, t.accent)
        self._chip_rect = (ch_x - 4, ch_y - 4, ch_w + 8, ch_h + 8)

        # ── the 3-cell decisions gauge (OPEN · SUPERSEDED · TOTAL) ──
        cells = self._stat_cells()
        gap = 8.0
        cell_w = (rect.w - 2 * PAD_X - 2 * gap) / 3.0
        cell_y = rect.y + HEADER_H + 2.0
        cell_h = 50.0
        for i, (val, lab, vcol) in enumerate(cells):
            cx = rect.x + PAD_X + i * (cell_w + gap)
            canvas.set_fill_color(*t.panel2)
            canvas.fill_round_rect(cx, cell_y, cell_w, cell_h, 9.0)
            canvas.set_stroke_color(*t.line)
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

        # ── the LATEST call — anchor title (bold; superseded dims + strikes) ──
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD)
        latest = self._latest()
        ly = rect.y + LATEST_TOP
        if latest is not None:
            lcy = ly + LATEST_H / 2.0
            sup = latest["superseded"]
            canvas.set_font(ct.FONT_MONO, 8.5, "regular")
            lead = "SUPERSEDED" if sup else "LATEST"
            ct.draw_tracked(canvas, lead, rect.x + PAD_X,
                            ly + 2 + canvas.measure_text("Ag").ascent, 0.6,
                            t.faint if not sup else t.warn)
            title = latest["title"] or latest["id"]
            canvas.set_font(ct.FONT_UI, 13.0, "regular" if sup else "bold")
            title = ct.ellipsize(canvas, title, rect.w - 2 * PAD_X)
            tm = canvas.measure_text(title)
            tbase = lcy + 6.0 + (tm.ascent - tm.descent) / 2.0
            canvas.set_fill_color(*(t.dim if sup else t.ink))
            canvas.draw_text(title, rect.x + PAD_X, tbase, "left")
            if sup:
                # strike-through (the graph's "this was replaced" read).
                canvas.set_stroke_color(*t.dim)
                canvas.set_stroke_width(1.0)
                sy = lcy + 6.0
                canvas.draw_line(rect.x + PAD_X, sy,
                                 rect.x + PAD_X + tm.width, sy)
        else:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.draw_text("awaiting self/decisions…",
                             rect.x + rect.w / 2, ly + LATEST_H / 2, "center")

        # ── freshness ──
        fresh = self._freshness()
        if fresh:
            fy = ly + LATEST_H + FRESH_H / 2.0
            canvas.set_font(ct.FONT_MONO, 9.0, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(f"updated {fresh}", rect.x + PAD_X,
                             fy + canvas.measure_text("Ag").ascent / 2.0,
                             "left")
        canvas.restore()
        canvas.restore()
