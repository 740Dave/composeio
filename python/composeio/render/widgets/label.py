"""
ComposeUX Label Widget

Simple text display.  Uses set_font + draw_text from the Canvas contract.
"""

from __future__ import annotations

from typing import Any, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget

_TEXT = (0xE0, 0xE0, 0xE0)


class LabelWidget(Widget):
    """Static or data-bound text label."""

    def __init__(self, widget_id: str, *,
                 text: str = "",
                 font_size: float = 16,
                 font_weight: str = "regular",
                 font_family: str = "sans-serif",
                 color: Tuple[int, int, int] = _TEXT,
                 align: str = "left",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "label", **kwargs)
        self.text = text
        self.font_size = font_size
        self.font_weight = font_weight
        self.font_family = font_family
        self.color = color
        self.align = align

    def on_bus_state(self, namespace: str, value: Any) -> None:
        self.text = str(value)

    # -- measure / draw -----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # Rough estimate (actual measurement needs a canvas instance).
        char_width = self.font_size * 0.6
        w = min(char_width * len(self.text), constraints.max_width)
        h = self.font_size * 1.4
        return Size(w, h)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        r, g, b = self.color
        canvas.set_fill_color(r, g, b)
        canvas.set_font(self.font_family, self.font_size, self.font_weight)

        if self.align == "center":
            x = rect.x + rect.w / 2
        elif self.align == "right":
            x = rect.x + rect.w
        else:
            x = rect.x

        # Vertically center the text in the rect
        y = rect.y + rect.h / 2 + self.font_size * 0.35
        canvas.draw_text(self.text, x, y, self.align)
        canvas.restore()
