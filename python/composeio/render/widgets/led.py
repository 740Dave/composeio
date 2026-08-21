"""
ComposeUX LED Widget

Circle with radial-gradient glow effect.  Color driven by state.
Uses ONLY the abstract Canvas contract.
"""

from __future__ import annotations

from typing import Any, Tuple

from ..canvas import Canvas, GradientStop, Size
from ..widget import Constraints, Rect, Widget

# Default palette
_TEAL = (0x26, 0xA6, 0x9A)
_AMBER = (0xFF, 0xA7, 0x26)
_RED = (0xEF, 0x53, 0x50)
_OFF = (0x44, 0x44, 0x55)
_BG = (0x1E, 0x1E, 0x2E)

_STATE_COLORS = {
    "on": _TEAL,
    "off": _OFF,
    "warning": _AMBER,
    "danger": _RED,
}


class LEDWidget(Widget):
    """Status LED with glow effect."""

    def __init__(self, widget_id: str, *,
                 state: str = "off",
                 color: Tuple[int, int, int] | None = None,
                 label: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "led", **kwargs)
        self.state = state
        self._custom_color = color
        self.label = label

    @property
    def active_color(self) -> Tuple[int, int, int]:
        if self._custom_color and self.state != "off":
            return self._custom_color
        return _STATE_COLORS.get(self.state, _OFF)

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if isinstance(value, str):
            self.state = value
        elif isinstance(value, bool):
            self.state = "on" if value else "off"
        elif isinstance(value, (int, float)):
            # Numeric state-engine levels: 0 off, 1 on, 2 warning, 3 danger
            self.state = {0: "off", 1: "on", 2: "warning", 3: "danger"}.get(
                int(value), "on")

    # -- measure / draw -----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        side = min(constraints.max_width, constraints.max_height, 60)
        return Size(side, side + (18 if self.label else 0))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        r, g, b = self.active_color
        led_r = min(rect.w, rect.h - (18 if self.label else 0)) / 2 - 4
        cx = rect.x + rect.w / 2
        cy = rect.y + led_r + 4

        canvas.save()

        # --- glow (radial gradient from bright center to transparent) ---
        glow_r = led_r * 2.0
        is_on = self.state != "off"

        if is_on:
            canvas.set_radial_gradient(cx, cy, glow_r, [
                GradientStop(0.0, r, g, b, 100),
                GradientStop(0.5, r, g, b, 40),
                GradientStop(1.0, r, g, b, 0),
            ])
            canvas.fill_circle(cx, cy, glow_r)

        # --- LED body (filled circle) ---
        canvas.set_radial_gradient(cx, cy, led_r, [
            GradientStop(0.0, min(r + 60, 255), min(g + 60, 255), min(b + 60, 255), 255),
            GradientStop(0.6, r, g, b, 255),
            GradientStop(1.0, max(r - 40, 0), max(g - 40, 0), max(b - 40, 0), 255),
        ])
        canvas.fill_circle(cx, cy, led_r)

        # --- subtle highlight (specular dot near top-left) ---
        canvas.set_radial_gradient(
            cx - led_r * 0.25, cy - led_r * 0.25, led_r * 0.4, [
                GradientStop(0.0, 255, 255, 255, 90 if is_on else 30),
                GradientStop(1.0, 255, 255, 255, 0),
            ])
        canvas.fill_circle(cx - led_r * 0.25, cy - led_r * 0.25, led_r * 0.4)

        # --- ring ---
        canvas.set_stroke_color(0x55, 0x55, 0x66)
        canvas.set_stroke_width(1.5)
        canvas.stroke_circle(cx, cy, led_r)

        # --- label ---
        if self.label:
            canvas.set_fill_color(0xBB, 0xBB, 0xCC)
            canvas.set_font("sans-serif", 12, "regular")
            canvas.draw_text(self.label, cx, rect.y + rect.h - 2, "center")

        canvas.restore()
