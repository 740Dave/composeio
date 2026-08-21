"""GaugeWidget — animated arc/needle gauge.

Subscribes to a namespace with continuous_0_255 or similar state engine.
Renders an animated gauge that sweeps to the current value.
Validates DP-CIO-14 (parameterized collections) — 3 gauges from one config.
"""

from __future__ import annotations
import math
from typing import Any
import customtkinter as ctk

from composeio.bus import ComposeIOBus


class GaugeWidget(ctk.CTkFrame):
    """Single animated gauge."""

    def __init__(
        self,
        master,
        namespace: str,
        bus: ComposeIOBus,
        label: str | None = None,
        min_val: float = 0,
        max_val: float = 100,
        size: int = 150,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.namespace = namespace
        self.bus = bus
        self.min_val = min_val
        self.max_val = max_val
        self.size = size
        self._current = 0.0
        self._target = 0.0
        self._animating = False

        self.configure(fg_color="transparent")

        # Canvas for gauge arc + needle
        self.canvas = ctk.CTkCanvas(
            self,
            width=size,
            height=size,
            bg="#1a1a1a",
            highlightthickness=0,
        )
        self.canvas.pack(pady=(4, 2))

        # Label
        display = label or namespace.replace("_", " ").title()
        self.label = ctk.CTkLabel(self, text=display, font=("Helvetica", 11, "bold"))
        self.label.pack(pady=(2, 0))

        # Value text
        self.value_label = ctk.CTkLabel(
            self, text="0", font=("Courier", 12), text_color="#888888"
        )
        self.value_label.pack(pady=(0, 4))

        # Draw static arc
        self._draw_arc()

        # Subscribe
        bus.ensure_namespace(namespace, "continuous_0_255")
        ns = bus.get_namespace(namespace)
        if ns:
            ns.subscribe(self._on_state_change)

    def _draw_arc(self):
        """Draw the background arc (225° sweep from 225° to -45°)."""
        pad = 15
        self.canvas.delete("arc_bg")
        self.canvas.create_arc(
            pad, pad, self.size - pad, self.size - pad,
            start=-45, extent=270,
            style="arc", outline="#444444", width=8,
            tags="arc_bg",
        )
        self._draw_needle(0)

    def _draw_needle(self, fraction: float):
        """Draw the needle at a given fraction (0-1) of the arc."""
        self.canvas.delete("needle")
        cx = self.size / 2
        cy = self.size / 2
        r = self.size / 2 - 20

        # Angle: 225° (min) to -45° (max), clockwise
        angle_deg = 225 - fraction * 270
        angle_rad = math.radians(angle_deg)

        nx = cx + r * math.cos(angle_rad)
        ny = cy - r * math.sin(angle_rad)

        # Needle line
        self.canvas.create_line(
            cx, cy, nx, ny,
            fill="#00FF88", width=3, tags="needle",
        )
        # Center dot
        self.canvas.create_oval(
            cx - 4, cy - 4, cx + 4, cy + 4,
            fill="#00FF88", outline="", tags="needle",
        )

        # Colored arc showing value
        self.canvas.delete("arc_val")
        if fraction > 0.01:
            extent = fraction * 270
            self.canvas.create_arc(
                15, 15, self.size - 15, self.size - 15,
                start=225, extent=-extent,
                style="arc", outline="#00FF88", width=8,
                tags="arc_val",
            )

    def _on_state_change(self, ns_name: str, new_value: Any, old_value: Any):
        """Animate needle to new value."""
        try:
            val = float(new_value)
        except (TypeError, ValueError):
            return
        self._target = val
        self.value_label.configure(text=f"{val:.0f}")
        if not self._animating:
            self._animating = True
            self._animate()

    def _animate(self):
        """Smooth animation toward target value."""
        diff = self._target - self._current
        if abs(diff) < 0.5:
            self._current = self._target
            self._animating = False
        else:
            self._current += diff * 0.15  # Ease factor
            self.after(16, self._animate)  # ~60fps

        fraction = (self._current - self.min_val) / (self.max_val - self.min_val)
        fraction = max(0, min(1, fraction))
        self._draw_needle(fraction)


class GaugePanelWidget(ctk.CTkFrame):
    """Row of gauges — parameterized from config (DP-CIO-14)."""

    def __init__(self, master, bus: ComposeIOBus, gauges_config: list[dict], **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="transparent")
        self.gauges: list[GaugeWidget] = []

        title = ctk.CTkLabel(self, text="GAUGES", font=("Helvetica", 14, "bold"))
        title.pack(pady=(8, 4))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=8)

        for g_cfg in gauges_config:
            ns = g_cfg["namespace"]
            bus.ensure_namespace(ns, g_cfg.get("state_engine", "continuous_0_255"))
            gauge = GaugeWidget(
                row,
                namespace=ns,
                bus=bus,
                label=g_cfg.get("label"),
                min_val=g_cfg.get("min", 0),
                max_val=g_cfg.get("max", 100),
            )
            gauge.pack(side="left", padx=8, pady=8, expand=True)
            self.gauges.append(gauge)
