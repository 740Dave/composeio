"""ButtonPanelWidget — clickable buttons with LED indicators.

Each button is configured from YAML: namespace, input type, state engine, LED behavior.
Clicking a button advances its state engine (toggle) or activates it (momentary).
The LED indicator reflects the current state per the declared LED behavior.

This is the strongest validation of DP-CIO-12 (declarative LED behavior) and
DP-CIO-4 (input/state separation). The 790-line tos-gumdrops ESPHome YAML
becomes ~40 lines of ComposeIO config rendered as interactive widgets.
"""

from __future__ import annotations
import time
from typing import Any
import customtkinter as ctk

from composeio.bus import ComposeIOBus
from composeio.namespace import Namespace

# LED behavior color mapping
_STATE_COLORS = {
    # Binary
    "off": "#333333",
    "on": "#00FF88",
    # Tri-level
    "half": "#FFAA00",
    "full": "#00FF88",
    # Stop-light
    "green": "#00CC44",
    "yellow": "#FFCC00",
    "red": "#FF3333",
}

_DEFAULT_OFF = "#333333"
_DEFAULT_ON = "#00FF88"
_BLINK_COLOR = "#00FF88"


class ButtonWidget(ctk.CTkFrame):
    """A single button with LED indicator."""

    def __init__(
        self,
        master,
        namespace: str,
        bus: ComposeIOBus,
        input_type: str = "toggle",
        led_behavior: str = "follow_state",
        label: str | None = None,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.namespace = namespace
        self.bus = bus
        self.input_type = input_type
        self.led_behavior = led_behavior
        self._blink_active = False
        self._blink_rate_ms = 500
        self._pressed = False

        # Layout
        self.configure(fg_color="transparent")

        # LED indicator (circular)
        self.led = ctk.CTkLabel(
            self,
            text="",
            width=24,
            height=24,
            corner_radius=12,
            fg_color=_DEFAULT_OFF,
        )
        self.led.pack(pady=(4, 2))

        # Button
        display_label = label or namespace.replace("_", " ").title()
        self.button = ctk.CTkButton(
            self,
            text=display_label,
            width=120,
            height=40,
            corner_radius=6,
            fg_color="#444444",
            hover_color="#555555",
            command=self._on_click,
        )
        self.button.pack(pady=(2, 4))

        # State label
        self.state_label = ctk.CTkLabel(
            self,
            text="—",
            font=("Courier", 10),
            text_color="#888888",
        )
        self.state_label.pack()

        # Subscribe to namespace state changes
        ns = bus.get_namespace(namespace)
        if ns:
            ns.subscribe(self._on_state_change)
            self._update_led(ns.value)
            self._update_state_label(ns.value)

        # Momentary: bind press/release
        if input_type == "momentary":
            self.button.bind("<ButtonPress-1>", self._on_press)
            self.button.bind("<ButtonRelease-1>", self._on_release)

    def _on_click(self):
        """Handle button click — advance state engine for toggle inputs."""
        if self.input_type == "toggle":
            ns = self.bus.get_namespace(self.namespace)
            if ns:
                ns.advance(source="simulator")
                # Publish the new state
                self.bus.publish_state(self.namespace, ns.value, source="simulator")

    def _on_press(self, event=None):
        """Momentary press — activate."""
        self._pressed = True
        self.bus.publish_state(self.namespace, "on", source="simulator")

    def _on_release(self, event=None):
        """Momentary release — deactivate."""
        self._pressed = False
        self.bus.publish_state(self.namespace, "off", source="simulator")

    def _on_state_change(self, ns_name: str, new_value: Any, old_value: Any):
        """Called when namespace state changes (from any source)."""
        self._update_led(new_value)
        self._update_state_label(new_value)

    def _update_led(self, value: Any):
        """Update LED indicator based on value and LED behavior."""
        if self.led_behavior == "follow_state":
            color = _STATE_COLORS.get(str(value), _DEFAULT_ON if value else _DEFAULT_OFF)
            self.led.configure(fg_color=color)
            self._stop_blink()
        elif self.led_behavior == "while_pressed":
            color = _DEFAULT_ON if self._pressed else _DEFAULT_OFF
            self.led.configure(fg_color=color)
        elif self.led_behavior == "off":
            self.led.configure(fg_color=_DEFAULT_OFF)
        elif self.led_behavior == "solid":
            self.led.configure(fg_color=_DEFAULT_ON)
        elif self.led_behavior.startswith("blink"):
            # Parse blink rate: "blink_500ms" → 500
            if "_" in self.led_behavior:
                rate_str = self.led_behavior.split("_", 1)[1].replace("ms", "")
                try:
                    self._blink_rate_ms = int(rate_str)
                except ValueError:
                    self._blink_rate_ms = 500
            self._start_blink()
        else:
            # Default: follow state
            color = _STATE_COLORS.get(str(value), _DEFAULT_OFF)
            self.led.configure(fg_color=color)

    def _update_state_label(self, value: Any):
        """Update the state text label."""
        self.state_label.configure(text=str(value))

    def _start_blink(self):
        """Start LED blinking."""
        if not self._blink_active:
            self._blink_active = True
            self._blink_tick()

    def _stop_blink(self):
        """Stop LED blinking."""
        self._blink_active = False

    def _blink_tick(self):
        """Blink animation tick."""
        if not self._blink_active:
            return
        current = self.led.cget("fg_color")
        next_color = _DEFAULT_OFF if current == _BLINK_COLOR else _BLINK_COLOR
        self.led.configure(fg_color=next_color)
        self.after(self._blink_rate_ms, self._blink_tick)


class ButtonPanelWidget(ctk.CTkFrame):
    """A panel of buttons configured from ComposeIO YAML.

    Usage:
        bus = ComposeIOBus()
        bus.start()
        # ... register namespaces from config ...
        panel = ButtonPanelWidget(root, bus, config["buttons"])
    """

    def __init__(
        self,
        master,
        bus: ComposeIOBus,
        buttons_config: list[dict],
        columns: int = 4,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.bus = bus
        self.configure(fg_color="transparent")
        self.buttons: list[ButtonWidget] = []

        for i, btn_cfg in enumerate(buttons_config):
            ns = btn_cfg["namespace"]
            input_type = btn_cfg.get("input", "toggle")
            led = btn_cfg.get("led", "follow_state")
            label = btn_cfg.get("label", None)

            # Ensure namespace exists on bus
            engine_cfg = btn_cfg.get("state_engine", "binary")
            bus.ensure_namespace(ns, engine_cfg)

            widget = ButtonWidget(
                self,
                namespace=ns,
                bus=bus,
                input_type=input_type,
                led_behavior=led,
                label=label,
            )
            row = i // columns
            col = i % columns
            widget.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")
            self.buttons.append(widget)
