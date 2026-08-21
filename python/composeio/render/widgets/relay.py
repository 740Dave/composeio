"""
ComposeUX RelayWidget — read-only status indicator.

M2 port of ux_composer RelayRenderer (true Relay visuals — replaces the
M1 LED approximation): a large flat colored circle whose color is
determined by the state-engine value, with an optional label below.

Color mapping (verbatim from RelayRenderer):
    binary:     off -> gray, on -> green
    tri_level:  off -> gray, half -> yellow, full -> green
    stop_light: green/yellow/red
    enum:       first -> gray, last -> green, middle interpolated
    continuous: 0 -> gray … 255 -> green (interpolated)
"""

from __future__ import annotations

from typing import Any, Tuple

from composeio.state_engine import StateEngine

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_COLOR_MAP = {
    "binary": {"off": style.GRAY, "on": style.GREEN},
    "tri_level": {"off": style.GRAY, "half": style.YELLOW, "full": style.GREEN},
    "stop_light": {"green": style.GREEN, "yellow": style.YELLOW, "red": style.RED},
}


class RelayWidget(Widget):
    """Read-only status indicator circle."""

    def __init__(self, widget_id: str, *,
                 label: str = "",
                 state_engine: Any = "binary",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "relay", **kwargs)
        self.label = label
        self._engine = StateEngine.from_config(state_engine)
        self._value: Any = self._engine.default

    def on_bus_state(self, namespace: str, value: Any) -> None:
        self._value = value

    # -- color resolution (port of RelayRenderer._resolve_color) ------------

    def _resolve_color(self, value: Any) -> Tuple[int, int, int]:
        engine_type = self._engine.engine_type
        emap = _COLOR_MAP.get(engine_type)
        if emap:
            return emap.get(str(value), style.GRAY)

        if engine_type == "enum":
            values = self._engine.values
            if not values:
                return style.GRAY
            sv = str(value)
            if sv == str(values[0]):
                return style.GRAY
            if sv == str(values[-1]):
                return style.GREEN
            try:
                idx = values.index(sv)
            except ValueError:
                return style.GRAY
            frac = idx / max(len(values) - 1, 1)
            return style.interpolate_color(style.GRAY, style.GREEN, frac)

        try:
            fv = float(value)
            frac = max(0.0, min(1.0, fv / 255.0))
            return style.interpolate_color(style.GRAY, style.GREEN, frac)
        except (TypeError, ValueError):
            return style.GRAY

    # -- measure / draw ------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        h = 56.0 + (22.0 if self.label else 8.0)
        return Size(min(72.0, constraints.max_width),
                    min(h, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        cx = rect.x + rect.w / 2
        cy = rect.y + 8 + 20          # CTk: 56px canvas, oval 8..48 -> r=20

        canvas.set_fill_color(*self._resolve_color(self._value))
        canvas.fill_circle(cx, cy, 20)
        canvas.set_stroke_color(*style.LED_OUTLINE)
        canvas.set_stroke_width(1)
        canvas.stroke_circle(cx, cy, 20)

        if self.label:
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 12, "regular")
            m = canvas.measure_text(self.label)
            canvas.draw_text(self.label, cx, cy + 20 + 4 + m.ascent, "center")
        canvas.restore()
