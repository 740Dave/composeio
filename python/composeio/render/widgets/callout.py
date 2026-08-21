"""
ComposeUX CalloutWidget — data-bound annotation overlay (the M31
flagship's CalloutOverlayWidget).

A callout is a labeled chip with a leader line pointing at a target
coordinate — designed to sit in a pixel_stack composite ABOVE an
ImageWidget, annotating a point of interest on the image.  There was no
CTk implementation (the M31 gap); this is the first realization,
authored directly against the Canvas contract.

Bus binding: the bound namespace drives the callout live.
    string value        -> replaces the text
    dict value          -> {text, x, y, visible, color} — any subset;
                           x/y are 0-1 fractions (or px) of the widget rect

Config:
    text        chip text
    target      [x, y] point the leader line indicates (fraction 0-1 or px
                of this widget's rect)
    offset      [dx, dy] chip position relative to the target (px; default
                up-left).  The chip clamps into the widget rect.
    color       accent (chip border, leader, target dot); default teal
    font_size   chip text size (default 12)
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TEAL = (0x26, 0xA6, 0x9A)
_CHIP_BG = (0x21, 0x21, 0x21)
_PAD_X = 8.0
_CHIP_H = 22.0
_DOT_R = 3.5


def _resolve(v: float, extent: float) -> float:
    """Fraction 0-1 -> px; anything else is already px."""
    return v * extent if isinstance(v, float) and 0.0 <= v <= 1.0 else float(v)


class CalloutWidget(Widget):
    """Annotation chip + leader line + target dot."""

    def __init__(self, widget_id: str, *,
                 text: str = "",
                 target: Any = (0.5, 0.5),
                 offset: Any = (-70, -46),
                 color: Optional[Tuple[int, int, int]] = None,
                 font_size: float = 12.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "callout", **kwargs)
        self.text = text
        t = list(target) if isinstance(target, (list, tuple)) else [0.5, 0.5]
        self.target_x, self.target_y = (t + [0.5, 0.5])[:2]
        o = list(offset) if isinstance(offset, (list, tuple)) else [-70, -46]
        self.offset_x, self.offset_y = (o + [-70, -46])[:2]
        self.color = tuple(color) if color else _TEAL
        self.font_size = font_size

    # -- bus ----------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if isinstance(value, str):
            self.text = value
        elif isinstance(value, dict):
            if "text" in value:
                self.text = str(value["text"])
            if "x" in value:
                self.target_x = value["x"]
            if "y" in value:
                self.target_y = value["y"]
            if "visible" in value:
                self.visible = bool(value["visible"])
            if "color" in value and isinstance(value["color"], (list, tuple)):
                self.color = tuple(value["color"])[:3]

    # -- measure / draw --------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        w = len(self.text) * self.font_size * 0.62 + 2 * _PAD_X + 20
        return Size(min(max(w, 60.0), constraints.max_width),
                    min(64.0, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if not self.visible:
            return
        canvas.save()

        tx = rect.x + _resolve(self.target_x, rect.w)
        ty = rect.y + _resolve(self.target_y, rect.h)

        # chip anchored near the target (offset up-left by default),
        # clamped into the widget rect
        canvas.set_font(style.FONT_UI, self.font_size, "medium")
        tm = canvas.measure_text(self.text or " ")
        chip_w = tm.width + 2 * _PAD_X
        cx = max(rect.x, min(tx + self.offset_x, rect.x + rect.w - chip_w))
        cy = max(rect.y, min(ty + self.offset_y, rect.y + rect.h - _CHIP_H))
        # leader departs the chip edge nearest the target
        ax = cx + (chip_w if tx > cx + chip_w / 2 else 0.0)
        ay = cy + (_CHIP_H if ty > cy + _CHIP_H / 2 else _CHIP_H / 2)

        # leader line chip -> target, then the target dot
        canvas.set_stroke_color(*self.color)
        canvas.set_stroke_width(1.5)
        canvas.draw_line(ax, ay, tx, ty)
        canvas.set_fill_color(*self.color)
        canvas.fill_circle(tx, ty, _DOT_R)
        canvas.set_stroke_color(*self.color, 90)
        canvas.set_stroke_width(1.5)
        canvas.stroke_circle(tx, ty, _DOT_R + 3)

        # chip (drawn after the line so it sits on top)
        canvas.set_fill_color(*_CHIP_BG, 235)
        canvas.fill_round_rect(cx, cy, chip_w, _CHIP_H, 5)
        canvas.set_stroke_color(*self.color)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(cx, cy, chip_w, _CHIP_H, 5)
        canvas.set_fill_color(*style.TEXT)
        canvas.draw_text(self.text, cx + _PAD_X,
                         style.baseline_for(canvas, cy + _CHIP_H / 2), "left")
        canvas.restore()
