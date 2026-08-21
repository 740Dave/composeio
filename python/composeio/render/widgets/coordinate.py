"""
ComposeUX CoordinateWidget — multi-digit entry with status indicator and
arm control.

M2 port of ux_composer CoordinateEntryRenderer.  Multi-namespace binding:
    namespace_coordinates — publishes the entered coordinate string
    namespace_status      — publishes entry status transitions
    namespace_arm         — subscribes to the arm signal (binary on/off)

Status states and indicator:
    dark              — off (#555555): not armed
    empty_flashing    — blue blink:  armed, awaiting input
    partial           — blue solid:  digits entered, not complete
    confirm_flashing  — green blink: all digits entered, awaiting Return
    committed         — green solid: coordinate committed to bus

Keyboard (focused): digits append, Backspace deletes, Return commits,
Escape clears.
"""

from __future__ import annotations

import time
from typing import Any

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_OFF = (0x55, 0x55, 0x55)
_BLUE = (0x3B, 0x8E, 0xD0)
_GREEN = (0x2E, 0xCC, 0x71)

_DISPLAY_BG = (0x1A, 0x1A, 0x2E)
_DISPLAY_TEXT = (0xCC, 0xCC, 0xCC)


class CoordinateWidget(Widget):
    """Multi-digit coordinate entry with arm control and status indicator."""

    def __init__(self, widget_id: str, *,
                 label: str = "Coordinates",
                 max_digits: int = 8,
                 namespace: str = "",
                 namespace_coordinates: str = "",
                 namespace_status: str = "",
                 namespace_arm: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "coordinate", **kwargs)
        self.label = label
        self.max_digits = int(max_digits)
        self.ns_coordinates = namespace_coordinates or namespace
        self.ns_status = namespace_status
        self.ns_arm = namespace_arm
        self.interactive = True
        self.accepts_focus = True

        self.digits = ""
        self.armed = False
        self.status = "dark"
        self._now = time.monotonic

    # -- bus (parser wires BOTH coordinates and arm namespaces here) -----------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.ns_arm:
            self._handle_arm(value)
            return
        # coordinates published externally -> reflect
        if value is not None and str(value).strip():
            sv = str(value)
            if sv != self.digits:
                self.digits = sv[:self.max_digits]

    def _handle_arm(self, value: Any) -> None:
        self.armed = (str(value) == "on")
        if self.armed:
            if not self.digits:
                self._set_status("empty_flashing")
        else:
            self.digits = ""
            self._set_status("dark")

    # -- keyboard ---------------------------------------------------------------

    def on_key(self, key: str, modifiers: Any = None) -> None:
        if not self.armed:
            return
        if key in ("Return", "Enter", "Keypad Enter"):
            self._commit()
        elif key == "Escape":
            self._clear()
        elif key == "Backspace" and self.digits:
            self.digits = self.digits[:-1]
            self._set_status("partial" if self.digits else "empty_flashing")
        elif len(key) == 1 and key.isdigit() and len(self.digits) < self.max_digits:
            self.digits += key
            self._set_status("confirm_flashing"
                             if len(self.digits) >= self.max_digits else "partial")

    def on_text(self, text: str) -> None:
        for ch in text:
            if ch.isdigit():
                self.on_key(ch)

    def _commit(self) -> None:
        if not self.digits:
            return
        if self.ns_coordinates:
            self.publish(self.ns_coordinates, self.digits)
        self._set_status("committed")

    def _clear(self) -> None:
        self.digits = ""
        self._set_status("empty_flashing" if self.armed else "dark")

    def _set_status(self, status: str) -> None:
        old = self.status
        self.status = status
        if self.ns_status and status != old:
            self.publish(self.ns_status, status)
        self.request_redraw()

    # -- indicator --------------------------------------------------------------

    def _led_color(self) -> tuple:
        blink_on = (self._now() % 1.0) < 0.5
        if self.status == "dark":
            return _OFF
        if self.status == "empty_flashing":
            return _BLUE if blink_on else _OFF
        if self.status == "partial":
            return _BLUE
        if self.status == "confirm_flashing":
            return _GREEN if blink_on else _OFF
        if self.status == "committed":
            return _GREEN
        return _OFF

    def _format_digits(self) -> str:
        return " ".join(self.digits.ljust(self.max_digits, "_"))

    # -- measure / draw ------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        w = self.max_digits * 16 + 90
        h = 36.0 + (24.0 if self.label else 0.0)
        return Size(min(w, constraints.max_width), min(h, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        x = rect.x + 8
        y = rect.y + 4
        w = max(80.0, rect.w - 16)

        if self.label:
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 13, "bold")
            m = canvas.measure_text(self.label)
            canvas.draw_text(self.label, rect.x + rect.w / 2, y + m.ascent, "center")
            y += 24

        # digit display (entry-like, monospace bold)
        disp_w = w - 36
        canvas.set_fill_color(*_DISPLAY_BG)
        canvas.fill_round_rect(x, y, disp_w, 28, 4)
        canvas.set_fill_color(*_DISPLAY_TEXT)
        canvas.set_font(style.FONT_MONO, 16, "bold")
        canvas.draw_text(self._format_digits(), x + 8,
                         style.baseline_for(canvas, y + 14), "left")

        # status indicator (24px canvas, r=8 oval, dark outline)
        style.draw_dot(canvas, x + disp_w + 20, y + 14, 8, self._led_color(),
                       outline=style.LED_OUTLINE)
        canvas.restore()

        if self.status in ("empty_flashing", "confirm_flashing"):
            self.request_redraw()     # keep the blink ticking
