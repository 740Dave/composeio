"""
ComposeUX ButtonWidget — interactive toggle button with LED indicator.

M2 port of ux_composer ButtonRenderer (the visual/behavioral spec):
tap advances the state engine and publishes the new value to the bound
namespace; the LED dot follows the configured led_behavior.

LED behaviors:
    follow_state  — lit when not default, off when default
    blink_500ms   — 500 ms on/off while active
    while_pressed — lit only during pointer-down
    solid         — always lit
    off           — never lit

Uses ONLY the abstract Canvas contract.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Tuple

from composeio.state_engine import StateEngine

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

# Values that map to each LED color category (verbatim from ButtonRenderer)
_ACTIVE_VALUES = {"on", "full", "255"}
_INTERMEDIATE_VALUES = {"half", "yellow", "128"}
_ALARM_VALUES = {"red"}


def led_color_for_value(value: Any, default_value: Any) -> Tuple[int, int, int]:
    """Port of ButtonRenderer._led_color_for_value."""
    sv = str(value)
    if sv in _ALARM_VALUES:
        return style.LED_ALARM
    if sv in _ACTIVE_VALUES:
        return style.LED_ACTIVE
    if sv in _INTERMEDIATE_VALUES:
        return style.LED_INTERMEDIATE
    try:
        fv = float(value)
        if fv > 0:
            return style.LED_ACTIVE if fv > 200 else style.LED_INTERMEDIATE
    except (TypeError, ValueError):
        pass
    if value == default_value:
        return style.LED_OFF
    return style.LED_ACTIVE


class ButtonWidget(Widget):
    """Toggle button + LED indicator, bound to a namespace."""

    def __init__(self, widget_id: str, *,
                 label: str = "Button",
                 state_engine: Any = "binary",
                 led_behavior: str = "follow_state",
                 namespace: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "button", **kwargs)
        self.label = label
        self.led_behavior = led_behavior
        self.bound_namespace = namespace
        self.interactive = True
        self._engine = StateEngine.from_config(state_engine)
        self._default_value = self._engine.default
        self._value: Any = self._default_value
        self._is_active = False
        self._pressed = False
        self._now = time.monotonic     # test seam for blink determinism

    # -- bus ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        try:
            self._engine.set(value)
        except (ValueError, KeyError):
            pass
        self._value = value
        self._is_active = (value != self._default_value)

    # -- interaction -------------------------------------------------------

    def on_pointer_down(self, x: float, y: float) -> None:
        self._pressed = True

    def on_pointer_up(self, x: float, y: float) -> None:
        self._pressed = False

    def on_tap(self, x: float, y: float) -> None:
        """Advance the state engine and publish the new value."""
        new_val = self._engine.advance()
        if self.bound_namespace:
            self.publish(self.bound_namespace, new_val)

    # -- LED state ---------------------------------------------------------

    def _blinking(self) -> bool:
        return self.led_behavior == "blink_500ms" and self._is_active

    def _led_color(self) -> Tuple[int, int, int]:
        b = self.led_behavior
        if b == "off":
            return style.LED_OFF
        if b == "solid":
            return style.LED_ACTIVE
        if b == "while_pressed":
            return style.LED_ACTIVE if self._pressed else style.LED_OFF
        if b == "blink_500ms":
            if not self._is_active:
                return style.LED_OFF
            on_phase = (self._now() % 1.0) < 0.5
            return (led_color_for_value(self._value, self._default_value)
                    if on_phase else style.LED_OFF)
        return led_color_for_value(self._value, self._default_value)

    # -- measure / draw -----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # LED gutter (28) + button (>=100) with 4px frame padding all round
        w = min(max(28 + 108, 20 + 8 + len(self.label) * 8 + 40),
                constraints.max_width)
        return Size(w, min(36.0, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        cy = rect.y + rect.h / 2

        # LED indicator (20px gutter, 12px dot, dark outline)
        style.draw_dot(canvas, rect.x + 14, cy, 6, self._led_color(),
                       outline=style.LED_OUTLINE)

        # Button body fills the remaining width (CTk: fill=x, expand)
        bx = rect.x + 28
        bw = max(40.0, rect.w - 28 - 4)
        bh = min(28.0, rect.h - 4)
        style.draw_button(canvas, bx, cy - bh / 2, bw, bh, self.label,
                          pressed=self._pressed)
        canvas.restore()

        if self._blinking():
            self.request_redraw()
