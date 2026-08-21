"""
ComposeUX ConsoleTabWidget — the console-level [Collaboration | Artifacts]
tab bar (LOCKED previs ``docs/intelligence-console/previs/
deliverable-compact.html`` ``.tabs``).

REUSES TabWidget's container/routing semantics BY SUBCLASS ([R3] reuse by
reference): measure/arrange/hit-testing/set_active and the "hidden tabs
still track bus state" routing all come from the base — this class swaps
only the bar CHROME (previs: left-aligned mono labels over a 1px line,
the active tab in azure with a 2px azure underline) and adds two bus
seams:

  * ``_app/console/tab`` — the tab-STATE namespace ([CXC-ICV] declared
    seam; the compact card's "Artifacts ▸" chip already publishes
    "artifacts" here).  A publish with a tab's name (or index) switches
    tabs; a user tap publishes the switched-to name, so tab state lives
    ON THE BUS and an Intelligence can drive it.
  * ``self/deliverables`` — the Artifacts tab's BADGE: the
    needs-enrichment backlog count (previs .badge, warn pill), visible
    from the Collaboration page.

Damage discipline ([CXC-R-DAMAGE]): bus updates call
``self.request_redraw()`` only (set_active already does).
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..canvas import Canvas
from ..widget import Rect
from . import console_theme as ct
from .deliverable_card import _coerce
from .tab import TabWidget

DEFAULT_TAB_NAMESPACE = "_app/console/tab"
DEFAULT_BADGE_NAMESPACE = "self/deliverables"

BAR_H = 44.0        # previs: 9px pad + 12px label + 11px pad + underline
_PAD_X = 16.0       # per-tab horizontal padding (previs 9px 16px 11px)


class ConsoleTabWidget(TabWidget):
    """[Collaboration | Artifacts] — previs tab chrome on TabWidget."""

    def __init__(self, widget_id: str, *,
                 labels: Optional[List[str]] = None,
                 initial_tab: int = 0,
                 tab_namespace: str = DEFAULT_TAB_NAMESPACE,
                 badge_namespace: str = DEFAULT_BADGE_NAMESPACE,
                 badge_tab: str = "artifacts",
                 **kwargs: Any) -> None:
        super().__init__(widget_id,
                         labels=list(labels or ["Collaboration",
                                                "Artifacts"]),
                         initial_tab=initial_tab,
                         widget_type="console_tabs", **kwargs)
        self.bar_h = BAR_H
        self.tab_namespace = str(tab_namespace or DEFAULT_TAB_NAMESPACE)
        self.badge_namespace = str(badge_namespace
                                   or DEFAULT_BADGE_NAMESPACE)
        self.badge_tab = str(badge_tab).lower()
        self._badge_count: Optional[int] = None

    # -- bus wiring ------------------------------------------------------------

    def tab_value(self, index: int) -> str:
        """The bus value for a tab = its lowercased label."""
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
        if namespace == self.tab_namespace:
            idx = self._index_for(_coerce(value))
            if idx is not None:
                self.set_active(idx)        # no-op (no redraw) if unchanged
            return
        if namespace == self.badge_namespace:
            payload = _coerce(value)
            if isinstance(payload, dict):
                stats = payload.get("stats")
                n = stats.get("needs_enrich") if isinstance(stats, dict) \
                    else None
                n = int(n) if isinstance(n, (int, float)) \
                    and not isinstance(n, bool) else None
                if n != self._badge_count:
                    self._badge_count = n
                    # [CXC-R-DAMAGE] scoped damage only.
                    self.request_redraw()

    # -- lifecycle ---------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- interaction -------------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        for i, (x0, x1) in enumerate(self._seg_bounds):
            if x0 <= x <= x1:
                self.set_active(i)
                # tab state lives ON the bus (the declared seam) — the
                # compact card's chip and an Intelligence publish the same.
                self.publish(self.tab_namespace, self.tab_value(i))
                return

    # -- draw (previs .tabs chrome; content delegation = base tail) --------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self._clamp_active()
        t = ct.CONSOLE.active
        canvas.save()

        # the bar's 1px bottom rule
        rule_y = rect.y + self.bar_h - 1.0
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(rect.x, rule_y, rect.x + rect.w, rule_y)

        canvas.set_font(ct.FONT_MONO, 12.0, "regular")
        m = canvas.measure_text("Ag")
        base = rect.y + (self.bar_h - 2.0) / 2.0 \
            + (m.ascent - m.descent) / 2.0

        self._seg_bounds = []
        sx = rect.x
        for i, lb in enumerate(self.labels):
            label = str(lb)
            selected = (i == self.active_index)
            canvas.set_font(ct.FONT_MONO, 12.0, "regular")
            lw = canvas.measure_text(label).width
            badge_w = 0.0
            badge_text = ""
            if self.tab_value(i) == self.badge_tab \
                    and self._badge_count is not None \
                    and self._badge_count > 0:
                badge_text = str(self._badge_count)
                canvas.set_font(ct.FONT_MONO, 10.0, "bold")
                badge_w = canvas.measure_text(badge_text).width + 12.0 + 7.0
            seg_w = _PAD_X * 2 + lw + badge_w

            canvas.set_font(ct.FONT_MONO, 12.0, "regular")
            canvas.set_fill_color(*(t.accent if selected else t.faint))
            canvas.draw_text(label, sx + _PAD_X, base, "left")
            if badge_text:
                # the needs-enrichment backlog pill (previs .badge: warn
                # bg, ground ink)
                bx = sx + _PAD_X + lw + 7.0
                canvas.set_font(ct.FONT_MONO, 10.0, "bold")
                bm = canvas.measure_text(badge_text)
                bh = 15.0
                by = rect.y + (self.bar_h - 2.0) / 2.0 - bh / 2.0 - 1.0
                canvas.set_fill_color(*t.warn)
                canvas.fill_round_rect(bx, by, bm.width + 12.0, bh, 7.5)
                canvas.set_fill_color(*t.send_ink)
                canvas.draw_text(badge_text, bx + 6.0,
                                 by + bh / 2.0
                                 + (bm.ascent - bm.descent) / 2.0, "left")
            if selected:
                canvas.set_stroke_color(*t.accent)
                canvas.set_stroke_width(2.0)
                canvas.draw_line(sx + 2, rule_y, sx + seg_w - 2, rule_y)

            self._seg_bounds.append((sx, sx + seg_w))
            sx += seg_w + 2.0

        # active tab content only (the base routing contract)
        if self.children:
            ch = self.children[self.active_index]
            if ch.arranged_rect is not None:
                ch.draw(canvas, ch.arranged_rect)
        canvas.restore()
