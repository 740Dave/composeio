"""
ComposeUX PowerToggleWidget — one stateful button that reads a plug and
toggles it.

M2 port of ux_composer PowerToggleRenderer: follows a plug-# namespace;
when a plug number arrives it reads the outlet's true state via an
mcp_action (status_tool) and labels itself with the ACTION it would
take ("Power On" when off, "Power Off" when on).  A tap invokes
on_tool/off_tool, then re-reads the true state (state authority is the
outlet, DP-CIO-19).  Powering OFF is confirm-gated via the engine's
arm-confirm pattern (see CommandWidget — no OS dialogs on the engine).
"""

from __future__ import annotations

import time
from typing import Any, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_ON_DOT = style.GREEN
_IDLE = (0x88, 0x88, 0x88)
_BUSY = style.YELLOW

_CONFIRM_WINDOW_S = 3.0

_UNSET = object()          # "no trigger message seen yet" sentinel (edge-detect)
_FALSY = {"", "0", "off", "false", "no", "none", "null"}


def _truthy(value: Any) -> bool:
    """Does *value* count as a firing trigger when no explicit trigger_value
    is configured?  Strings are matched case-insensitively against a small
    falsy set ('off', '0', ...); everything else falls back to bool() — so an
    empty list/`[]` (no DRADIS contacts) is falsy but `[{...}]` fires."""
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY
    return bool(value)


class PowerToggleWidget(Widget):
    """A single Power button that reflects and toggles a plug's state.

    Two independent ways to fire, either or both configurable:
      * MANUAL TAP  — follows ``plug_namespace``, reads the outlet via
        ``status_tool`` and taps ``on_tool`` / ``off_tool`` (state authority
        is the outlet; power-off is arm-confirm gated).  UNCHANGED default.
      * SEMANTIC TRIGGER — subscribes ``trigger_namespace`` (e.g. a DRADIS
        contact event); on a matching message it invokes ``toggle_tool`` for
        ``ha_entity`` (default tool ``homeassistant.toggle``).  Point the
        ``*_tool`` names at ``homeassistant.*`` and a TAP drives HA too.
    """

    def __init__(self, widget_id: str, *,
                 label: str = "Power",
                 plug_namespace: str = "bringup/plug",
                 on_tool: str = "fleetpower.power_on",
                 off_tool: str = "fleetpower.power_off",
                 status_tool: str = "fleetpower.power_status",
                 confirm_off: bool = True,
                 trigger_namespace: str = "",
                 trigger_value: Any = None,
                 ha_entity: str = "",
                 toggle_tool: str = "homeassistant.toggle",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "power_toggle", **kwargs)
        self.label = label
        self.plug_namespace = plug_namespace
        self.on_tool = on_tool
        self.off_tool = off_tool
        self.status_tool = status_tool
        self.confirm_off = confirm_off
        # semantic-trigger config (all optional; default => classic behavior)
        self.trigger_namespace = trigger_namespace
        self.trigger_value = trigger_value
        self.ha_entity = ha_entity
        self.toggle_tool = toggle_tool
        self.interactive = True

        self._plug: Optional[str] = None
        self._state: Optional[str] = None    # "on" | "off" | None(unknown)
        self._busy = False
        self._dot = _IDLE
        self._armed_at: Optional[float] = None
        self._now = time.monotonic
        self._last_trigger: Any = _UNSET     # edge-detect for the trigger ns

    @property
    def _has_runner(self) -> bool:
        return self._ctx is not None and callable(getattr(self._ctx, "invoke", None))

    # -- plug namespace (wired by the parser as this widget's subscription) ---

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if self.trigger_namespace and namespace == self.trigger_namespace:
            self._handle_trigger(value)
            return
        if namespace != self.plug_namespace:
            return
        plug = str(value).strip() if value not in (None, "") else None
        if plug == self._plug:
            return
        self._plug = plug
        if plug and self._has_runner:
            self._refresh()                   # read the outlet's current state
        else:
            self._state = None
            self._dot = _IDLE

    def _refresh(self) -> None:
        if not (self._plug and self._has_runner):
            return
        self._dot = _BUSY
        self.request_redraw()
        self._ctx.invoke(self.status_tool, {"target": self._plug},
                         on_event=self._on_status)

    def _on_status(self, ev: dict) -> None:
        self._ctx.schedule_update(lambda e=dict(ev): self._apply_status(e))

    def _apply_status(self, ev: dict) -> None:
        if ev.get("kind") != "result":
            return
        self._render_state(ev.get("state") if ev.get("ok") else None)

    def _render_state(self, state: Optional[str]) -> None:
        self._state = state
        self._dot = _ON_DOT if state == "on" else _IDLE
        self.request_redraw()

    # -- semantic trigger (bus event -> HA toggle) ---------------------------------

    def _handle_trigger(self, value: Any) -> None:
        """A message on ``trigger_namespace``.  Fire on the RISING edge into a
        matching value: if ``trigger_value`` is set, match equals that value;
        else fire on any truthy value.  De-dupes an unchanged value so a
        retained-message redelivery does not double-toggle."""
        if self.trigger_value is not None:
            matches = str(value) == str(self.trigger_value)
        else:
            matches = _truthy(value)
        fire = matches and value != self._last_trigger
        self._last_trigger = value
        if fire:
            self._fire_trigger()

    def _fire_trigger(self) -> None:
        if not (self.ha_entity and self._has_runner):
            return
        self._busy = True
        self._dot = _BUSY
        self.request_redraw()
        self._ctx.invoke(self.toggle_tool, {"entity": self.ha_entity},
                         on_event=self._on_trigger_action)

    def _on_trigger_action(self, ev: dict) -> None:
        self._ctx.schedule_update(lambda e=dict(ev): self._after_trigger(e))

    def _after_trigger(self, ev: dict) -> None:
        if ev.get("kind") != "result":
            return
        self._busy = False
        if self._plug and self._has_runner:
            self._refresh()               # re-read the plug reflection if any
        else:
            self._dot = _IDLE             # stateless toggle: no state to show
            self.request_redraw()

    # -- interaction ---------------------------------------------------------------

    def _button_props(self) -> tuple:
        """(label, enabled, warm) for the current state."""
        if self._armed_at is not None:
            return (f"Confirm OFF plug {self._plug}?", True, True)
        if self._state == "on":
            return ("Power Off", not self._busy, True)
        if self._state == "off":
            return ("Power On", not self._busy, False)
        if self._plug:
            return ("Power  (tap to read)", not self._busy, False)
        return ("Power  (enter plug #)", False, False)

    def on_tap(self, x: float, y: float) -> None:
        if self._busy:
            return
        if self._state is None:               # unknown -> (re)read
            self._refresh()
            return
        turning_off = self._state == "on"
        if turning_off and self.confirm_off:
            now = self._now()
            if self._armed_at is None or now - self._armed_at > _CONFIRM_WINDOW_S:
                self._armed_at = now          # arm: tap again to confirm
                self.request_redraw()
                return
            self._armed_at = None
        tool = self.off_tool if turning_off else self.on_tool
        self._busy = True
        self._dot = _BUSY
        self.request_redraw()
        self._ctx.invoke(tool, {"target": self._plug}, on_event=self._on_action)

    def _on_action(self, ev: dict) -> None:
        self._ctx.schedule_update(lambda e=dict(ev): self._after_action(e))

    def _after_action(self, ev: dict) -> None:
        if ev.get("kind") != "result":
            return
        self._busy = False
        self._refresh()                       # re-read the true state

    # -- measure / draw ---------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(220.0, constraints.max_width),
                    min(60.0, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self._armed_at is not None and self._now() - self._armed_at > _CONFIRM_WINDOW_S:
            self._armed_at = None

        canvas.save()
        x = rect.x + 8
        y = rect.y + 4

        if self.label:
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 13, "bold")
            m = canvas.measure_text(self.label)
            canvas.draw_text(self.label, x, y + m.ascent, "left")
            y += 20

        cy = y + 14
        style.draw_dot(canvas, x + 6, cy, 5, self._dot)

        text, enabled, warm = self._button_props()
        style.draw_button(canvas, x + 18, cy - 14, max(160.0, rect.w - 34), 28,
                          text,
                          fill=style.BTN_WARM if warm else style.BTN_FILL,
                          disabled=not enabled)
        canvas.restore()

        if self._armed_at is not None:
            self.request_redraw()
