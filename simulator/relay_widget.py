"""RelayWidget — labeled on/off indicators.

Subscribes to a namespace and shows relay state as a visual indicator.
Represents the actuator side of the namespace connection.
"""

from __future__ import annotations
from typing import Any
import customtkinter as ctk

from composeio.bus import ComposeIOBus


_STATE_COLORS = {
    "off": "#333333",
    "on": "#00FF88",
    "half": "#FFAA00",
    "full": "#00FF88",
    "green": "#00CC44",
    "yellow": "#FFCC00",
    "red": "#FF3333",
}


class RelayIndicator(ctk.CTkFrame):
    """Single relay channel indicator."""

    def __init__(self, master, namespace: str, bus: ComposeIOBus, channel: int = 0, **kwargs):
        super().__init__(master, **kwargs)
        self.namespace = namespace
        self.bus = bus
        self.channel = channel
        self.configure(fg_color="#222222", corner_radius=8)

        # Status light
        self.light = ctk.CTkLabel(
            self,
            text="",
            width=40,
            height=40,
            corner_radius=20,
            fg_color="#333333",
        )
        self.light.pack(pady=(8, 4))

        # Channel label
        label_text = namespace.replace("_", " ").title()
        self.label = ctk.CTkLabel(
            self,
            text=label_text,
            font=("Helvetica", 12, "bold"),
        )
        self.label.pack(pady=(2, 2))

        # State text
        self.state_text = ctk.CTkLabel(
            self,
            text="OFF",
            font=("Courier", 10),
            text_color="#666666",
        )
        self.state_text.pack(pady=(0, 8))

        # Subscribe
        ns = bus.get_namespace(namespace)
        if ns:
            ns.subscribe(self._on_state_change)
            self._update(ns.value)

    def _on_state_change(self, ns_name: str, new_value: Any, old_value: Any):
        self._update(new_value)

    def _update(self, value: Any):
        color = _STATE_COLORS.get(str(value), "#333333")
        self.light.configure(fg_color=color)
        self.state_text.configure(
            text=str(value).upper(),
            text_color=color if color != "#333333" else "#666666",
        )


class RelayWidget(ctk.CTkFrame):
    """Panel of relay indicators."""

    def __init__(self, master, bus: ComposeIOBus, channels_config: list[dict], **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="transparent")

        # Title
        title = ctk.CTkLabel(self, text="RELAYS", font=("Helvetica", 14, "bold"))
        title.pack(pady=(8, 4))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=8)

        for i, ch_cfg in enumerate(channels_config):
            ns = ch_cfg["namespace"]
            channel = ch_cfg.get("channel", i)
            bus.ensure_namespace(ns, ch_cfg.get("state_engine", "binary"))

            indicator = RelayIndicator(row, ns, bus, channel=channel)
            indicator.pack(side="left", padx=8, pady=8, expand=True, fill="both")
