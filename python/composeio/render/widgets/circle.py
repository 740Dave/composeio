"""
ComposeUX Circle Widget

Basic filled and/or stroked circle.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget


class CircleWidget(Widget):
    """Filled/stroked circle primitive."""

    def __init__(self, widget_id: str, *,
                 radius: float = 30,
                 fill_color: Optional[Tuple[int, int, int, int]] = None,
                 stroke_color: Optional[Tuple[int, int, int, int]] = None,
                 stroke_width: float = 1.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "circle", **kwargs)
        self.radius = radius
        self.fill_color = fill_color or (0x26, 0xA6, 0x9A, 255)
        self.stroke_color = stroke_color
        self.stroke_width = stroke_width

    def measure(self, constraints: Constraints) -> Size:
        d = self.radius * 2
        return Size(min(d, constraints.max_width),
                    min(d, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        cx = rect.x + rect.w / 2
        cy = rect.y + rect.h / 2
        r = min(rect.w, rect.h) / 2

        canvas.save()

        # Fill
        fr, fg, fb, fa = self.fill_color
        canvas.set_fill_color(fr, fg, fb, fa)
        canvas.fill_circle(cx, cy, r)

        # Stroke
        if self.stroke_color:
            sr, sg, sb, sa = self.stroke_color
            canvas.set_stroke_color(sr, sg, sb, sa)
            canvas.set_stroke_width(self.stroke_width)
            canvas.stroke_circle(cx, cy, r)

        canvas.restore()
