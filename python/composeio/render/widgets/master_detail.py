"""
ComposeUX MasterDetailWidget — a two-panel master/detail container: a LEFT
nav-list of one-line clickable section entries + a RIGHT detail pane that
renders the selected section's content ([CXC-IC-CONFIG-MASTERDETAIL]).

REUSES TabWidget BY SUBCLASS ([R3] reuse by reference — the same move
`console_tabs` makes): measure / arrange / routing / "hidden sections still
track bus state" / set_active all come from the base.  This class swaps only
the CHROME — a top tab-bar becomes a LEFT nav column — and adds ONE bus seam:

  * ``nav_namespace`` (default ``_app/config/section``) — the selection-STATE
    namespace.  A nav tap publishes the section's name; the container
    subscribes it and swaps the right pane; an Intelligence drives the SAME
    seam by publishing the same namespace (no privileged path — the credo:
    Intelligence drives identically to a human).

Damage discipline ([CXC-R-DAMAGE]): bus updates + selection call ONLY
``self.request_redraw()`` (``set_active`` already does).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect
from ..engine.layout import Container, draw_child
from . import console_theme as ct
from .deliverable_card import _coerce
from .tab import TabWidget

DEFAULT_NAV_NAMESPACE = "_app/config/section"

NAV_W = 264.0          # the left nav column width
NAV_GAP = 18.0         # gap between nav column and the detail pane
_ROW_H = 42.0          # one-line entry height
_ROW_GAP = 4.0
_NAV_PAD_TOP = 6.0
_NAV_PAD_X = 12.0


class MasterDetailWidget(TabWidget):
    """LEFT nav-list + RIGHT detail pane, on TabWidget's routing machinery."""

    def __init__(self, widget_id: str, *,
                 labels: Optional[List[str]] = None,
                 initial_tab: int = 0,
                 nav_namespace: str = DEFAULT_NAV_NAMESPACE,
                 nav_width: float = NAV_W,
                 **kwargs: Any) -> None:
        super().__init__(widget_id,
                         labels=list(labels or []),
                         initial_tab=initial_tab,
                         widget_type="master_detail", **kwargs)
        self.nav_namespace = str(nav_namespace or DEFAULT_NAV_NAMESPACE)
        self.nav_width = float(nav_width or NAV_W)
        # the cards in the detail pane cast ambient shadows past their rects.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.interactive = True
        # (y0, y1) per nav row, recorded by draw()
        self._nav_bounds: List[Tuple[float, float]] = []

    # -- bus wiring ------------------------------------------------------------

    def nav_value(self, index: int) -> str:
        """The bus value for a section = its lowercased label."""
        if 0 <= index < len(self.labels):
            return str(self.labels[index]).lower()
        return str(index)

    def _index_for(self, value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            i = int(value)
            return i if 0 <= i < len(self.labels) else None
        name = str(value or "").strip().lower()
        for i, lb in enumerate(self.labels):
            if str(lb).lower() == name:
                return i
        return None

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.nav_namespace:
            idx = self._index_for(_coerce(value))
            if idx is not None:
                self.set_active(idx)        # no-op (no redraw) if unchanged

    # -- lifecycle -------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- layout ----------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # fill the space the parent gives us; the detail pane measures within
        # the reduced (right-of-nav) width.
        inner = Constraints(
            max(0.0, constraints.max_width - self.nav_width - NAV_GAP),
            constraints.max_height)
        h = 0.0
        for ch in self.children:
            h = max(h, ch.measure(inner).height)
        return Size(constraints.max_width,
                    min(max(h, 0.0), constraints.max_height)
                    if constraints.max_height else h)

    def _arrange_children(self, inner: Rect) -> None:
        self._clamp_active()
        content = Rect(inner.x + self.nav_width + NAV_GAP, inner.y,
                       max(0.0, inner.w - self.nav_width - NAV_GAP), inner.h)
        for i, ch in enumerate(self.children):
            ch.visible = (i == self.active_index)
            ch.arranged_rect = content
            if isinstance(ch, Container):
                ch.arrange(content)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self._clamp_active()
        t = ct.CONSOLE.active
        canvas.save()

        # LEFT nav column background panel + its right rule.
        nav_x, nav_w = rect.x, self.nav_width
        ct.draw_card(canvas, nav_x, rect.y, nav_w, rect.h, ct.RADIUS_CARD, t)

        self._nav_bounds = []
        canvas.set_font(ct.FONT_UI, 13.5, "regular")
        ry = rect.y + _NAV_PAD_TOP
        for i, lb in enumerate(self.labels):
            selected = (i == self.active_index)
            row_y0, row_y1 = ry, ry + _ROW_H
            if selected:
                # the selected entry: accent-dim fill + a 2px accent left bar.
                canvas.set_fill_color(*t.accent_dim)
                canvas.fill_round_rect(nav_x + 5.0, row_y0 + 2.0,
                                       nav_w - 10.0, _ROW_H - 4.0, 8.0)
                canvas.set_fill_color(*t.accent)
                canvas.fill_round_rect(nav_x + 5.0, row_y0 + 8.0,
                                       2.5, _ROW_H - 16.0, 1.25)
            cy = row_y0 + _ROW_H / 2.0
            m = canvas.measure_text("Ag")
            base = cy + (m.ascent - m.descent) / 2.0
            canvas.set_font(ct.FONT_UI, 13.5, "regular")
            canvas.set_fill_color(*(t.accent if selected else t.dim))
            canvas.draw_text(ct.ellipsize(canvas, str(lb), nav_w - 2 * _NAV_PAD_X),
                             nav_x + _NAV_PAD_X + 6.0, base, "left")
            self._nav_bounds.append((row_y0, row_y1))
            ry = row_y1 + _ROW_GAP

        # RIGHT detail pane: the active section only (the base routing
        # contract), through the real draw path (damage + transforms).
        if self.children:
            ch = self.children[self.active_index]
            if ch.arranged_rect is not None:
                draw_child(canvas, ch, ch.arranged_rect)
        canvas.restore()

    # -- interaction -----------------------------------------------------------

    def hit_test_tree(self, x: float, y: float):
        r = self.arranged_rect
        if r is None or not (r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h):
            return None
        # the nav column: taps switch sections.
        if x <= r.x + self.nav_width:
            return self
        # otherwise delegate to the active detail child.
        if self.children:
            ch = self.children[self.active_index]
            if isinstance(ch, Container):
                hit = ch.hit_test_tree(x, y)
                if hit is not None:
                    return hit
            elif (ch.arranged_rect is not None and ch.hit_test(x, y)):
                return ch
        return None

    def on_tap(self, x: float, y: float) -> None:
        for i, (y0, y1) in enumerate(self._nav_bounds):
            if y0 <= y <= y1:
                self.set_active(i)
                # selection lives ON the bus (the declared seam) — an
                # Intelligence publishes the identical namespace.
                self.publish(self.nav_namespace, self.nav_value(i))
                return
