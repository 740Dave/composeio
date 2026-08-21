"""
ComposeUX DashboardWidget — Surface 5, a NEW Dashboard tab that HOSTS
maker-chosen widgets on a placement grid ([SPEC:CXC-CONSOLE-PROD]
[CXC-ICP-S5-DASHBOARD]).

The Dashboard is a PLACEMENT surface: a grid that mounts widgets the maker picks
from Foundation B.  Its contents are DRIVEN BY a persisted PLACEMENT model —
Foundation A's ``meta.console.dashboard.placements`` (``[{widget:<type>, row,
col, w, h}...]``).  At launch the parser reads that list and builds one child
per placement THROUGH the widget registry (``resolve_class``), binding each
child's descriptor ``required_namespaces`` (the READ side wired at parse time,
[CXC-ICP-RESTART]); THIS widget lays them out on the grid and draws them.

A widget is added from a PALETTE that renders
``list_widgets(category="dashboard-eligible")`` (Foundation B) — NEVER a
hardcoded palette (S6).  Placing / removing a widget publishes Foundation A's
``.../config/console/dashboard``; ``config_persister`` persists it and the NEXT
launch mounts the new set (modulo restart — a live add is a best-effort glass
bonus; the durable contract is restart).

A placement referencing an UNREGISTERED type is SKIPPED with a warning (a
removed/renamed widget must not break the Dashboard).  DEFAULT = empty
placements -> an empty-but-usable grid with the palette as the "add" affordance.

Damage discipline ([CXC-R-DAMAGE]): bus updates + taps call ONLY
``self.request_redraw()``.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from ..engine.layout import Container, draw_child
from . import console_theme as ct
from ..engine.widget_registry import list_widgets, resolve_class, get_descriptor
from .deliverable_card import _coerce

DEFAULT_DASHBOARD_NS = "intelligence/architect/config/console/dashboard"
DEFAULT_PALETTE_CATEGORY = "dashboard-eligible"

GRID_COLS = 4          # placement columns
CELL_ROW_H = 200.0     # px per grid row unit
CELL_GAP = 14.0
PALETTE_H = 96.0       # the top palette strip height
_CHIP_H = 30.0
_CHIP_PAD = 12.0
_CHIP_GAP = 8.0


def _base(canvas: Canvas, cy: float) -> float:
    m = canvas.measure_text("Ag")
    return cy + (m.ascent - m.descent) / 2.0


def normalize_placements(value: Any) -> List[Dict[str, Any]]:
    """Coerce a raw dashboard payload to a clean placement list.  Accepts the
    bare list or the ``{placements:[...]}`` wrapper; drops malformed entries.
    Never raises."""
    if isinstance(value, dict):
        value = value.get("placements", [])
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for p in value:
        if not isinstance(p, dict):
            continue
        typ = p.get("widget") or p.get("type")
        if not isinstance(typ, str) or not typ.strip():
            continue
        out.append({
            "widget": typ.strip(),
            "row": int(p.get("row", 0) or 0),
            "col": int(p.get("col", 0) or 0),
            "w": max(1, int(p.get("w", 1) or 1)),
            "h": max(1, int(p.get("h", 1) or 1)),
        })
    return out


class DashboardWidget(Container):
    """A grid placement surface: a registry-driven palette + persisted-placement
    children mounted through the widget registry."""

    def __init__(self, widget_id: str, *,
                 dashboard_ns: str = DEFAULT_DASHBOARD_NS,
                 palette_category: str = DEFAULT_PALETTE_CATEGORY,
                 placements: Optional[List[Dict[str, Any]]] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "dashboard", **kwargs)
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.dashboard_ns = str(dashboard_ns or DEFAULT_DASHBOARD_NS)
        self.palette_category = str(palette_category or DEFAULT_PALETTE_CATEGORY)
        self.interactive = True
        self._ctx: Any = None
        self._placements: List[Dict[str, Any]] = normalize_placements(
            placements or [])
        # (namespace, child) pairs the parser registers as subscriptions
        # (the parse-time READ side for placed widgets).
        self.child_subs: List[Tuple[str, Widget]] = []
        self.children = []
        self._build_children()
        # hit geometry
        self._palette_zones: List[Tuple[float, float, float, float, str]] = []
        self._remove_zones: List[Tuple[float, float, float, float, int]] = []

    # -- children built from placements THROUGH the registry -------------------

    def _build_children(self) -> None:
        """(Re)build one child widget per placement via the registry.  A
        placement whose type is unregistered is SKIPPED with a warning."""
        self.children = []
        self.child_subs = []
        kept: List[Dict[str, Any]] = []
        for i, p in enumerate(self._placements):
            cls = resolve_class(p["widget"])
            desc = get_descriptor(p["widget"])
            if cls is None or desc is None:
                sys.stderr.write(
                    f"[dashboard] skip unregistered placement "
                    f"{p['widget']!r}\n")
                continue
            try:
                child = cls(f"{self.widget_id}_p{i}_{p['widget']}")
            except Exception as exc:                     # noqa: BLE001
                sys.stderr.write(
                    f"[dashboard] cannot build {p['widget']!r}: {exc!r}\n")
                continue
            child.interactive = getattr(child, "interactive", False)
            self.children.append(child)
            for ns in desc.required_namespaces:
                self.child_subs.append((ns, child))
            kept.append(p)
        self._placements = kept

    def mount_placements(self, placements: List[Dict[str, Any]]) -> None:
        """Replace the placement set + rebuild children (used by tests + the
        best-effort live add).  Mounts newly built children if already mounted."""
        self._placements = normalize_placements(placements)
        self._build_children()
        if self._ctx is not None:
            for ch in self.children:
                try:
                    ch.on_mount(self._ctx)
                except Exception:                        # noqa: BLE001
                    pass
        self.request_redraw()

    def placements_payload(self) -> Dict[str, Any]:
        return {"placements": [dict(p) for p in self._placements]}

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace != self.dashboard_ns:
            return
        placements = normalize_placements(_coerce(value))
        # only rebuild if the set actually changed (avoid churn/self-echo)
        if placements != self._placements:
            self.mount_placements(placements)

    # -- lifecycle / layout ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        self._ctx = ctx
        super().on_mount(ctx)          # mounts the placement children
        ct.theme_from_ctx(ctx)

    def _grid_rect(self, r: Rect) -> Rect:
        return Rect(r.x, r.y + PALETTE_H + CELL_GAP, r.w,
                    max(0.0, r.h - PALETTE_H - CELL_GAP))

    def _cell_rect(self, grid: Rect, p: Dict[str, Any]) -> Rect:
        cell_w = (grid.w - CELL_GAP * (GRID_COLS - 1)) / GRID_COLS
        col = max(0, min(GRID_COLS - 1, int(p["col"])))
        span = max(1, min(GRID_COLS - col, int(p["w"])))
        x = grid.x + col * (cell_w + CELL_GAP)
        w = span * cell_w + (span - 1) * CELL_GAP
        y = grid.y + int(p["row"]) * (CELL_ROW_H + CELL_GAP)
        h = int(p["h"]) * CELL_ROW_H + (int(p["h"]) - 1) * CELL_GAP
        return Rect(x, y, w, h)

    def _arrange_children(self, inner: Rect) -> None:
        grid = self._grid_rect(inner)
        for child, p in zip(self.children, self._placements):
            cr = self._cell_rect(grid, p)
            child.arranged_rect = cr
            if isinstance(child, Container):
                child.arrange(cr)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._palette_zones = []
        self._remove_zones = []
        canvas.save()
        self._draw_palette(canvas, rect, t)
        grid = self._grid_rect(rect)
        if not self.children:
            self._draw_empty(canvas, grid, t)
        else:
            for i, (child, p) in enumerate(zip(self.children,
                                               self._placements)):
                cr = self._cell_rect(grid, p)
                child.arranged_rect = cr
                self._draw_cell_frame(canvas, cr, p, i, t)
                draw_child(canvas, child, cr)
        canvas.restore()

    def _draw_palette(self, canvas: Canvas, rect: Rect, t) -> None:
        ct.draw_card(canvas, rect.x, rect.y, rect.w, PALETTE_H, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, rect.x, rect.y, rect.w,
                                 "Add widget", ns_tag="console/dashboard",
                                 count_text=f"{len(self._placements)} placed",
                                 count_color=t.accent, t=t)
        cy = by + 8.0 + _CHIP_H / 2.0
        cx = rect.x + ct.PAD_CARD_BODY
        for d in list_widgets(category=self.palette_category):
            label = "+ " + d.display_name
            canvas.set_font(ct.FONT_MONO, 10.5, "regular")
            tw = canvas.measure_text(label).width
            cw = tw + 2 * _CHIP_PAD
            if cx + cw > rect.x + rect.w - ct.PAD_CARD_BODY:
                break                          # one row of the palette
            y = cy - _CHIP_H / 2.0
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(cx, y, cw, _CHIP_H, 8.0)
            canvas.set_stroke_color(*t.accent_line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(cx + 0.5, y + 0.5, cw - 1, _CHIP_H - 1, 8.0)
            canvas.set_fill_color(*t.accent)
            canvas.draw_text(label, cx + _CHIP_PAD, _base(canvas, cy), "left")
            self._palette_zones.append((cx, y, cx + cw, y + _CHIP_H, d.type))
            cx += cw + _CHIP_GAP

    def _draw_cell_frame(self, canvas: Canvas, cr: Rect, p: Dict[str, Any],
                         idx: int, t) -> None:
        # a subtle host frame + a remove [x] chip top-right of the cell.
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(cr.x + 0.5, cr.y + 0.5, cr.w - 1, cr.h - 1,
                                 ct.RADIUS_CARD)
        bs = 22.0
        bx, byy = cr.x + cr.w - bs - 6.0, cr.y + 6.0
        canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(bx, byy, bs, bs, 6.0)
        canvas.set_stroke_color(*t.crit_line)
        canvas.stroke_round_rect(bx + 0.5, byy + 0.5, bs - 1, bs - 1, 6.0)
        canvas.set_font(ct.FONT_UI, 12.0, "regular")
        m = canvas.measure_text("×")
        canvas.set_fill_color(*t.crit)
        canvas.draw_text("×", bx + bs / 2 - m.width / 2,
                         _base(canvas, byy + bs / 2), "left")
        self._remove_zones.append((bx, byy, bx + bs, byy + bs, idx))

    def _draw_empty(self, canvas: Canvas, grid: Rect, t) -> None:
        ct.draw_card(canvas, grid.x, grid.y, grid.w, min(grid.h, 260.0),
                     ct.RADIUS_CARD, t)
        canvas.set_font(ct.FONT_UI, 15.0, "regular")
        msg = "Empty Dashboard — pick a widget from the palette above to place it."
        m = canvas.measure_text(msg)
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(msg, grid.x + grid.w / 2 - m.width / 2,
                         grid.y + 70.0, "left")

    # -- interaction -----------------------------------------------------------

    def hit_test_tree(self, x: float, y: float):
        r = self.arranged_rect
        if r is None or not (r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h):
            return None
        # palette + remove chips + empty cells belong to the dashboard itself.
        for (x0, y0, x1, y1, _t) in self._palette_zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return self
        for (x0, y0, x1, y1, _i) in self._remove_zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return self
        # otherwise delegate to a hosted child.
        for child in self.children:
            if isinstance(child, Container):
                hit = child.hit_test_tree(x, y)
                if hit is not None:
                    return hit
            elif child.arranged_rect is not None and child.hit_test(x, y):
                return child
        return self

    def on_tap(self, x: float, y: float) -> None:
        for (x0, y0, x1, y1, idx) in self._remove_zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._remove_placement(idx)
                return
        for (x0, y0, x1, y1, typ) in self._palette_zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._add_placement(typ)
                return

    def _next_cell(self) -> Tuple[int, int]:
        """Next free (row, col) in row-major order for a new 1x1 placement."""
        occupied = {(p["row"], p["col"]) for p in self._placements}
        row = 0
        while True:
            for col in range(GRID_COLS):
                if (row, col) not in occupied:
                    return row, col
            row += 1

    def _add_placement(self, typ: str) -> None:
        row, col = self._next_cell()
        self._placements.append({"widget": typ, "row": row, "col": col,
                                 "w": 1, "h": 1})
        self._build_children()
        if self._ctx is not None:
            for ch in self.children:
                try:
                    ch.on_mount(self._ctx)
                except Exception:                        # noqa: BLE001
                    pass
        self.publish(self.dashboard_ns, self.placements_payload())
        self.request_redraw()

    def _remove_placement(self, idx: int) -> None:
        if 0 <= idx < len(self._placements):
            del self._placements[idx]
            self._build_children()
            self.publish(self.dashboard_ns, self.placements_payload())
            self.request_redraw()
