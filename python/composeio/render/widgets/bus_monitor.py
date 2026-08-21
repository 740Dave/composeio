"""
ComposeUX BusMonitorWidget — scrolling bus traffic log.

M2 port of ux_composer BusMonitorRenderer: subscribes to ALL bus
messages via bus.on_message() and renders them in a monospace log,
color-coded by message type.  Message callbacks arrive on transport
threads and marshal through the engine update queue.

Colors (verbatim):
    state -> white, event -> cyan, heartbeat/time_sync -> gray,
    discovery -> yellow, config_push -> magenta, other -> #AAAAAA
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TYPE_COLORS = {
    "state": (0xFF, 0xFF, 0xFF),
    "event": (0x00, 0xBC, 0xD4),
    "heartbeat": (0x88, 0x88, 0x88),
    "time_sync": (0x88, 0x88, 0x88),
    "discovery": (0xFF, 0xD7, 0x00),
    "config_push": (0xE0, 0x40, 0xFB),
}
_OTHER = (0xAA, 0xAA, 0xAA)

_LINE_H = 15.0
_FONT_SIZE = 11.0


class BusMonitorWidget(Widget):
    """Scrolling, color-coded bus traffic log."""

    def __init__(self, widget_id: str, *,
                 label: str = "Bus Monitor",
                 max_lines: int = 150,
                 show_heartbeats: bool = False,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "bus_monitor", **kwargs)
        self.label = label
        self.max_lines = int(max_lines)
        self.show_heartbeats = bool(show_heartbeats)
        # (text, color) ring of formatted lines
        self._lines: List[Tuple[str, tuple]] = []

    # -- bus wiring (all messages, not a namespace) ----------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        bus = getattr(ctx, "bus", None) if ctx else None
        if bus is not None and hasattr(bus, "on_message"):
            bus.on_message(self._on_bus_message)

    def on_unmount(self) -> None:
        bus = getattr(self._ctx, "bus", None) if self._ctx else None
        if bus is not None and hasattr(bus, "off_message"):
            try:
                bus.off_message(self._on_bus_message)
            except Exception:
                pass
        super().on_unmount()

    def _on_bus_message(self, message: Any) -> None:
        """Transport-thread callback — filter, format, marshal."""
        type_str = getattr(getattr(message, "type", None), "value",
                           str(getattr(message, "type", "?")))
        if not self.show_heartbeats and type_str in ("heartbeat", "time_sync"):
            return
        ts = datetime.fromtimestamp(getattr(message, "timestamp", 0)).strftime("%H:%M:%S")
        value = getattr(message, "value", None)
        value_str = str(value) if value is not None else ""
        source = getattr(message, "source", "") or ""
        line = f"[{ts}] {message.namespace}: {type_str} {value_str}"
        if source:
            line += f" ({source})"
        color = _TYPE_COLORS.get(type_str, _OTHER)
        if self._ctx is not None and hasattr(self._ctx, "schedule_update"):
            self._ctx.schedule_update(lambda: self._append(line, color))
        else:
            self._append(line, color)

    def _append(self, line: str, color: tuple) -> None:
        self._lines.append((line, color))
        if len(self._lines) > self.max_lines:
            del self._lines[:len(self._lines) - self.max_lines]
        self.request_redraw()

    # -- measure / draw --------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(416.0, constraints.max_width),
                    min(240.0, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        x = rect.x + 8
        y = rect.y + 4
        w = max(120.0, rect.w - 16)

        if self.label:
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 13, "bold")
            m = canvas.measure_text(self.label)
            canvas.draw_text(self.label, x, y + m.ascent, "left")
            y += 20

        # textbox body
        box_h = max(40.0, rect.y + rect.h - y - 8)
        canvas.set_fill_color(*style.TEXTBOX_BG)
        canvas.fill_round_rect(x, y, w, box_h, 6)

        # last N lines that fit, bottom-anchored (auto-scroll to end)
        canvas.save()
        canvas.clip_round_rect(x, y, w, box_h, 6)
        canvas.set_font(style.FONT_MONO, _FONT_SIZE, "regular")
        fit = max(1, int((box_h - 8) // _LINE_H))
        visible = self._lines[-fit:]
        ly = y + 4
        for text, color in visible:
            canvas.set_fill_color(*color)
            lm = canvas.measure_text("Ag")
            canvas.draw_text(text, x + 6, ly + lm.ascent, "left")
            ly += _LINE_H
        canvas.restore()
        canvas.restore()
