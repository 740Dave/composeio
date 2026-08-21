"""
ComposeUX CheckboxWidget — labeled checkbox bound to a binary namespace.

M2 port of ux_composer CheckboxRenderer: publishes on_value/off_value on
toggle; reflects the namespace when something else sets it (preset-able).
Visual: the CTk dark-blue checkbox — 24px rounded box, 3px border when
unchecked, accent fill + checkmark when checked, label to the right.
"""

from __future__ import annotations

from typing import Any

from ..canvas import Canvas, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TRUTHY = {"on", "true", "1", "yes", "enabled"}

_BOX = 24.0          # CTkCheckBox checkbox_width/height
_BOX_RADIUS = 6.0


class CheckboxWidget(Widget):
    """Labeled checkbox that publishes on/off values on toggle."""

    def __init__(self, widget_id: str, *,
                 label: str = "",
                 on_value: str = "on",
                 off_value: str = "off",
                 namespace: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "checkbox", **kwargs)
        self.label = label
        self.on_value = on_value
        self.off_value = off_value
        self.bound_namespace = namespace
        self.interactive = True
        self.checked = False

    def on_bus_state(self, namespace: str, value: Any) -> None:
        self.checked = str(value).lower() in _TRUTHY

    def on_tap(self, x: float, y: float) -> None:
        self.checked = not self.checked
        if self.bound_namespace:
            self.publish(self.bound_namespace,
                         self.on_value if self.checked else self.off_value)

    # -- measure / draw -------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        w = _BOX + 10 + len(self.label) * 7.2 + 16
        return Size(min(w, constraints.max_width),
                    min(_BOX + 6, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        bx = rect.x + 8
        by = rect.y + (rect.h - _BOX) / 2

        if self.checked:
            canvas.set_fill_color(*style.CHECK_FILL)
            canvas.fill_round_rect(bx, by, _BOX, _BOX, _BOX_RADIUS)
            # checkmark
            canvas.set_stroke_color(*style.CHECK_MARK)
            canvas.set_stroke_width(2.5)
            canvas.draw_path([
                MoveTo(bx + _BOX * 0.24, by + _BOX * 0.52),
                LineTo(bx + _BOX * 0.44, by + _BOX * 0.72),
                LineTo(bx + _BOX * 0.78, by + _BOX * 0.30),
            ])
        else:
            canvas.set_stroke_color(*style.CHECK_BORDER)
            canvas.set_stroke_width(3)
            canvas.stroke_round_rect(bx + 1.5, by + 1.5, _BOX - 3, _BOX - 3,
                                     _BOX_RADIUS - 1.5)

        if self.label:
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 13, "regular")
            baseline = style.baseline_for(canvas, rect.y + rect.h / 2)
            canvas.draw_text(self.label, bx + _BOX + 10, baseline, "left")
        canvas.restore()
