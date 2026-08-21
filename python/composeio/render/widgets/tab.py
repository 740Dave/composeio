"""
ComposeUX TabWidget — tabbed container hosting child widgets.

M2 port of ux_composer TabRenderer (CTkTabview): a segmented tab bar
above a content area showing the active tab's children.  Visual: the
CTk dark-blue segmented button — rounded strip, selected segment in the
accent color.

Routing semantics: the engine wires ALL tabs' namespace subscriptions
centrally at startup ("all" routing).  Hidden tabs still track bus
state but are neither drawn nor hit-tested, so the visible effect
matches the CTk widget; the `routing: active_only` dynamic
unsubscribe optimization is deliberately not carried over (a state
update to a hidden widget costs nanoseconds on the engine).
"""

from __future__ import annotations

from typing import Any, List

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from ..engine.layout import Container
from . import style

_BAR_H = 32.0
_SEG_H = 26.0


class TabWidget(Container):
    """Tabbed container: segmented bar + active tab's child container."""

    def __init__(self, widget_id: str, *,
                 labels: List[str] | None = None,
                 initial_tab: int = 0,
                 routing: str = "all",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, kwargs.pop("widget_type", "tab"),
                         **kwargs)
        self.labels = list(labels or [])
        self.routing = routing
        self.active_index = int(initial_tab)
        self.interactive = True
        # Bar height seam: subclasses with different bar chrome (e.g. the
        # console-level tab bar) override this ONE number; measure/arrange/
        # hit-testing all read it, so the routing semantics stay shared.
        self.bar_h: float = _BAR_H
        self._seg_bounds: List[tuple] = []   # (x0, x1) per segment

    # children are one Container per tab, appended via add() by the parser

    def _clamp_active(self) -> None:
        if self.children:
            self.active_index = max(0, min(self.active_index,
                                           len(self.children) - 1))

    def set_active(self, index: int) -> None:
        self._clamp_active()
        if index != self.active_index and 0 <= index < len(self.children):
            self.active_index = index
            for i, ch in enumerate(self.children):
                ch.visible = (i == index)
            self.request_redraw()

    # -- layout ------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        inner = Constraints(constraints.max_width,
                            max(0.0, constraints.max_height - self.bar_h))
        w, h = 120.0, 0.0
        for ch in self.children:
            s = ch.measure(inner)
            w = max(w, s.width)
            h = max(h, s.height)
        return Size(min(w, constraints.max_width),
                    min(h + self.bar_h, constraints.max_height))

    def _arrange_children(self, inner: Rect) -> None:
        self._clamp_active()
        content = Rect(inner.x, inner.y + self.bar_h,
                       inner.w, max(0.0, inner.h - self.bar_h))
        for i, ch in enumerate(self.children):
            ch.visible = (i == self.active_index)
            ch.arranged_rect = content
            if isinstance(ch, Container):
                ch.arrange(content)

    # -- draw ----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self._clamp_active()
        canvas.save()

        # segmented bar
        canvas.set_font(style.FONT_UI, 13, "regular")
        widths = [max(70.0, canvas.measure_text(lb).width + 28)
                  for lb in self.labels] or [70.0]
        bar_w = sum(widths)
        bx = rect.x + (rect.w - bar_w) / 2
        by = rect.y + (self.bar_h - _SEG_H) / 2
        canvas.set_fill_color(*style.SEG_BG)
        canvas.fill_round_rect(bx, by, bar_w, _SEG_H, 6)

        self._seg_bounds = []
        sx = bx
        for i, lb in enumerate(self.labels):
            w = widths[i] if i < len(widths) else 70.0
            if i == self.active_index:
                canvas.set_fill_color(*style.SEG_SELECTED)
                canvas.fill_round_rect(sx + 2, by + 2, w - 4, _SEG_H - 4, 5)
            canvas.set_fill_color(*style.SEG_TEXT)
            canvas.draw_text(lb, sx + w / 2,
                             style.baseline_for(canvas, by + _SEG_H / 2),
                             "center")
            self._seg_bounds.append((sx, sx + w))
            sx += w

        # active tab content only
        if self.children:
            ch = self.children[self.active_index]
            if ch.arranged_rect is not None:
                ch.draw(canvas, ch.arranged_rect)
        canvas.restore()

    # -- interaction ------------------------------------------------------------

    def hit_test_tree(self, x: float, y: float):
        r = self.arranged_rect
        if r is None or not (r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h):
            return None
        if y <= r.y + self.bar_h:
            return self            # the bar: taps switch tabs
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
        for i, (x0, x1) in enumerate(self._seg_bounds):
            if x0 <= x <= x1:
                self.set_active(i)
                return
