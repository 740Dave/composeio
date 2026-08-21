"""
ComposeUX Placeholder Widget (M1)

Honest stand-in for widget types that are not yet ported to the Canvas
contract (the M2 parity work: Button, Relay, Command, TextInput, ...).
Renders a labeled dashed outline so a production .compose.yaml loads and
lays out correctly today without faking visual parity.
"""

from __future__ import annotations

from typing import Any

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget

_DIM = (0x66, 0x66, 0x78)
_BG = (0x28, 0x28, 0x3C)


class PlaceholderWidget(Widget):
    """Dashed outline + type name; occupies real layout space."""

    def __init__(self, widget_id: str, *,
                 original_type: str = "unknown",
                 label: str = "",
                 width: float = 140,
                 height: float = 80,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "placeholder", **kwargs)
        self.original_type = original_type
        self.label = label
        self.ph_width = width
        self.ph_height = height

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(self.ph_width, constraints.max_width),
                    min(self.ph_height, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.set_fill_color(*_BG, 120)
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 6)

        # dashed border via line segments (no dash primitive in contract)
        canvas.set_stroke_color(*_DIM, 200)
        canvas.set_stroke_width(1)
        dash, gap = 6.0, 4.0
        x = rect.x
        while x < rect.x + rect.w:
            x2 = min(x + dash, rect.x + rect.w)
            canvas.draw_line(x, rect.y, x2, rect.y)
            canvas.draw_line(x, rect.y + rect.h, x2, rect.y + rect.h)
            x += dash + gap
        y = rect.y
        while y < rect.y + rect.h:
            y2 = min(y + dash, rect.y + rect.h)
            canvas.draw_line(rect.x, y, rect.x, y2)
            canvas.draw_line(rect.x + rect.w, y, rect.x + rect.w, y2)
            y += dash + gap

        cx = rect.x + rect.w / 2
        cy = rect.y + rect.h / 2
        canvas.set_fill_color(*_DIM)
        canvas.set_font("sans-serif", 11, "medium")
        canvas.draw_text(self.original_type, cx, cy - 2, "center")
        if self.label:
            canvas.set_font("sans-serif", 10, "regular")
            canvas.draw_text(self.label, cx, cy + 12, "center")
        canvas.restore()
