"""
ComposeUX Rectangle Widget

Basic filled and/or stroked rectangle with optional corner radius.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget


class RectangleWidget(Widget):
    """Filled/stroked rectangle primitive."""

    def __init__(self, widget_id: str, *,
                 width: float = 100,
                 height: float = 60,
                 fill_color: Optional[Tuple[int, int, int, int]] = None,
                 stroke_color: Optional[Tuple[int, int, int, int]] = None,
                 stroke_width: float = 1.0,
                 corner_radius: float = 0.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "rectangle", **kwargs)
        self.rect_width = width
        self.rect_height = height
        self.fill_color = fill_color or (0x26, 0xA6, 0x9A, 255)
        self.stroke_color = stroke_color
        self.stroke_width = stroke_width
        self.corner_radius = corner_radius

    def measure(self, constraints: Constraints) -> Size:
        return Size(
            min(self.rect_width, constraints.max_width),
            min(self.rect_height, constraints.max_height),
        )

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()

        # Fill
        r, g, b, a = self.fill_color
        canvas.set_fill_color(r, g, b, a)
        if self.corner_radius > 0:
            canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h,
                                   self.corner_radius)
        else:
            canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        # Stroke
        if self.stroke_color:
            sr, sg, sb, sa = self.stroke_color
            canvas.set_stroke_color(sr, sg, sb, sa)
            canvas.set_stroke_width(self.stroke_width)
            if self.corner_radius > 0:
                canvas.stroke_round_rect(rect.x, rect.y, rect.w, rect.h,
                                         self.corner_radius)
            else:
                canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)

        canvas.restore()
