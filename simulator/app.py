"""ComposeIO Simulator — FTL Jump System Demo.

Full integration: button panel + relay indicators + FTL capacitor gauge +
coordinate entry widget + bus monitor + physical CH32V003 jump button.

Demonstrates:
  - 5 state engine types (binary, tri_level, stop_light, continuous, enum)
  - Compound namespace bindings (FTL jump LED depends on arm + charge + coords)
  - Event-triggered bindings with side effects (jump resets all state)
  - Sensor-to-actuator bindings (sublight_engines → power gauge)
  - Physical ↔ simulated integration via UART transport
  - Namespace labels (DP-CIO-2 extension)

Run: python3 -m simulator.app
     (from ~/Dropbox/Maker/composeio/)
"""

from __future__ import annotations
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import customtkinter as ctk

from composeio.bus import ComposeIOBus
from composeio.config import load_config_string, apply_config
from simulator.button_panel import ButtonPanelWidget
from simulator.relay_widget import RelayWidget
from simulator.gauge_widget import GaugePanelWidget, GaugeWidget
from simulator.bus_monitor import BusMonitorWidget
from simulator.coordinate_entry import CoordinateEntryWidget


# --- FTL System configs ---

FTL_PANEL_CONFIG = """
module: ftl-panel
role: button-matrix
buttons:
  - namespace: ftl_arm
    label: "FTL Arm"
    input: toggle
    state_engine: binary
    led: follow_state

  - namespace: sublight_engines
    label: "Sublight Engines"
    input: toggle
    state_engine: tri_level
    led: follow_state

  - namespace: alert_status
    label: "Alert Status"
    input: toggle
    state_engine: stop_light
    led: follow_state

  - namespace: warp_factor
    label: "Warp Factor"
    input: toggle
    state_engine:
      type: enum
      values: [standby, warp_1, warp_5, warp_9, maximum]
      default: standby
    led: follow_state

  - namespace: shields
    label: "Shields"
    input: toggle
    state_engine: binary
    led: follow_state
"""

FTL_RELAY_CONFIG = """
module: ftl-relay
role: relay-controller
channels:
  - namespace: ftl_arm
    channel: 1
  - namespace: sublight_engines
    channel: 2
  - namespace: shields
    channel: 3
"""

FTL_GAUGE_CONFIG = [
    {"namespace": "ftl_cap_charge", "label": "FTL Capacitor", "min": 0, "max": 100},
    {"namespace": "gauge_power", "label": "Sublight Power", "min": 0, "max": 100},
]

# UART transport for physical CH32V003
UART_PORT = "/dev/cu.usbserial-3110"
UART_BAUD = 115200


class SimulatorApp(ctk.CTkFrame):
    """ComposeIO FTL Jump System Simulator."""

    name = "ComposeIO Simulator"
    icon_path = None

    def __init__(self, master, enable_uart: bool = True, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="#1a1a1a")
        self._charging = False
        self._charge_value = 0.0

        # Create bus
        self.bus = ComposeIOBus(bus_id="ftl-simulator")

        # Try to connect UART transport for physical CH32V003
        self._uart = None
        if enable_uart:
            try:
                from composeio.transport_uart import UARTTransport, register_namespace_hash
                # Pre-register namespace hashes for reverse lookup
                for ns in ["ftl_jump", "ftl_jump_led", "ftl_arm", "ftl_cap_charge", "ftl_coord_status"]:
                    register_namespace_hash(ns)
                self._uart = UARTTransport(port=UART_PORT, baud=UART_BAUD)
                self.bus = ComposeIOBus(bus_id="ftl-simulator", global_transport=self._uart)
            except Exception as e:
                print(f"UART not available ({e}), running simulator-only mode")

        self.bus.start()

        # Apply configs
        panel_cfg = load_config_string(FTL_PANEL_CONFIG)
        relay_cfg = load_config_string(FTL_RELAY_CONFIG)
        apply_config(self.bus, panel_cfg)
        apply_config(self.bus, relay_cfg)

        # Register FTL namespaces
        self.bus.register_namespace("ftl_cap_charge", "continuous_0_255", label="FTL Capacitor")
        self.bus.register_namespace("ftl_jump", "binary", label="FTL Jump")
        self.bus.register_namespace("ftl_jump_led", {
            "type": "enum",
            "values": ["off", "blink_fast", "blink_slow", "solid"],
            "default": "off",
        }, label="Jump LED")
        self.bus.register_namespace("gauge_power", "continuous_0_255", label="Sublight Power")

        # Binding: sublight_engines → gauge_power
        self.bus.add_binding("sublight_engines", "gauge_power", transform_config=[
            {"map": {"off": 0, "half": 50, "full": 100}},
        ])

        # Compound binding: FTL jump LED depends on arm + charge + coord_status
        self.bus.add_compound_binding({
            "output_namespace": "ftl_jump_led",
            "requires": 'ftl_arm == "on"',
            "default": "off",
            "conditions": [
                {"when": {"ftl_cap_charge": "< 50"}, "value": "blink_fast"},
                {"when": {"ftl_cap_charge": ">= 50", "ftl_coord_status": "!= committed"}, "value": "blink_slow"},
                {"when": {"ftl_cap_charge": ">= 50", "ftl_coord_status": "== committed"}, "value": "solid"},
            ],
        })

        # Event binding: jump event resets everything
        self.bus.add_event_binding({
            "trigger": 'ftl_jump.event == "jump"',
            "output_namespace": "ftl_jump_led",
            "sequence": [{"set": "off"}],
            "side_effects": [
                {"set": "ftl_arm", "value": "off"},
                {"set": "ftl_cap_charge", "value": 0},
                {"set": "ftl_coord_status", "value": "dark"},
            ],
        })

        # Subscribe to ftl_arm for charge timer control
        self.bus.subscribe("ftl_arm", self._on_ftl_arm_change)

        # --- Layout ---
        title = ctk.CTkLabel(
            self, text="BSG FTL JUMP SYSTEM", font=("Helvetica", 20, "bold"),
        )
        title.pack(pady=(8, 4))

        subtitle = ctk.CTkLabel(
            self,
            text="ComposeIO Multi-Device Integration" +
                 (" — UART Connected" if self._uart and self._uart.is_connected else " — Simulator Only"),
            font=("Helvetica", 11),
            text_color="#666666" if not (self._uart and self._uart.is_connected) else "#00FF88",
        )
        subtitle.pack(pady=(0, 8))

        # Main content — 3 columns
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=12)
        content.columnconfigure(0, weight=2)
        content.columnconfigure(1, weight=2)
        content.columnconfigure(2, weight=3)
        content.rowconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        # Col 0: Button panel + relays
        left_frame = ctk.CTkFrame(content, fg_color="transparent")
        left_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 6))

        panel_frame = ctk.CTkFrame(left_frame, fg_color="#1e1e1e", corner_radius=8)
        panel_frame.pack(fill="both", expand=True, pady=(0, 6))
        ctk.CTkLabel(panel_frame, text="CONTROL PANEL", font=("Helvetica", 13, "bold")).pack(pady=(6, 2))
        self.button_panel = ButtonPanelWidget(panel_frame, self.bus, panel_cfg["buttons"], columns=3)
        self.button_panel.pack(fill="both", expand=True, padx=6, pady=6)

        relay_frame = ctk.CTkFrame(left_frame, fg_color="#1e1e1e", corner_radius=8)
        relay_frame.pack(fill="x", pady=(0, 0))
        self.relay_widget = RelayWidget(relay_frame, self.bus, relay_cfg["channels"])
        self.relay_widget.pack(fill="x", padx=6, pady=6)

        # Col 1: Coordinate entry + gauges
        mid_frame = ctk.CTkFrame(content, fg_color="transparent")
        mid_frame.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=6)

        # Coordinate entry
        self.coord_entry = CoordinateEntryWidget(mid_frame, self.bus)
        self.coord_entry.pack(fill="x", pady=(0, 6))

        # Gauges
        gauge_frame = ctk.CTkFrame(mid_frame, fg_color="#1e1e1e", corner_radius=8)
        gauge_frame.pack(fill="both", expand=True)
        self.gauge_panel = GaugePanelWidget(gauge_frame, self.bus, FTL_GAUGE_CONFIG)
        self.gauge_panel.pack(fill="both", expand=True, padx=6, pady=6)

        # FTL Jump LED indicator (simulates the physical LED)
        jump_frame = ctk.CTkFrame(mid_frame, fg_color="#1e1e1e", corner_radius=8)
        jump_frame.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(jump_frame, text="FTL JUMP", font=("Helvetica", 12, "bold")).pack(pady=(6, 2))
        self.jump_led = ctk.CTkLabel(
            jump_frame, text="", width=40, height=40, corner_radius=20, fg_color="#333333"
        )
        self.jump_led.pack(pady=(2, 2))
        self.jump_led_label = ctk.CTkLabel(
            jump_frame, text="OFF", font=("Courier", 10), text_color="#666666"
        )
        self.jump_led_label.pack(pady=(0, 6))

        # Simulated jump button (mirrors the physical one)
        self.jump_btn = ctk.CTkButton(
            jump_frame, text="JUMP", width=100, height=35,
            fg_color="#661111", hover_color="#882222",
            font=("Helvetica", 14, "bold"),
            command=self._on_sim_jump,
        )
        self.jump_btn.pack(pady=(0, 8))

        # Col 2: Bus monitor
        self.bus_monitor = BusMonitorWidget(content, self.bus, max_lines=150)
        self.bus_monitor.grid(row=0, column=2, rowspan=2, sticky="nsew", padx=(6, 0))

        # Subscribe to jump LED for visual indicator
        self.bus.subscribe("ftl_jump_led", self._on_jump_led_change)

        # Start charge timer and LED blink ticks
        self._charge_tick()
        self._jump_led_blink_tick()

    def _on_ftl_arm_change(self, ns: str, new, old):
        """Start/stop FTL capacitor charging."""
        if new == "on":
            self._charging = True
            self._charge_value = 0.0
        else:
            self._charging = False
            self._charge_value = 0.0
            self.bus.publish_state("ftl_cap_charge", 0, source="charge_timer")

    def _charge_tick(self):
        """Capacitor charge timer — 2% per second when armed."""
        if self._charging:
            self._charge_value = min(100, self._charge_value + 0.4)  # 0.4 per 200ms = 2/sec
            self.bus.publish_state("ftl_cap_charge", int(self._charge_value), source="charge_timer")
        self.after(200, self._charge_tick)

    def _on_sim_jump(self):
        """Simulated jump button press (mirrors physical CH32V003 button)."""
        self.bus.publish_event("ftl_jump", "jump", source="simulator")

    def _on_jump_led_change(self, ns: str, new, old):
        """Update the visual jump LED indicator."""
        self._jump_led_state = str(new)
        if new == "solid":
            self.jump_led.configure(fg_color="#00FF88")
            self.jump_led_label.configure(text="READY", text_color="#00FF88")
        elif new == "off":
            self.jump_led.configure(fg_color="#333333")
            self.jump_led_label.configure(text="OFF", text_color="#666666")
        # blink states handled by _jump_led_blink_tick

    def _jump_led_blink_tick(self):
        """Blink the jump LED indicator for blink_fast/blink_slow states."""
        led_state = self.bus.get_state("ftl_jump_led")
        if led_state == "blink_fast":
            current = self.jump_led.cget("fg_color")
            self.jump_led.configure(fg_color="#FF4444" if current == "#333333" else "#333333")
            self.jump_led_label.configure(text="CHARGING", text_color="#FF4444")
            self.after(150, self._jump_led_blink_tick)
        elif led_state == "blink_slow":
            current = self.jump_led.cget("fg_color")
            self.jump_led.configure(fg_color="#FFAA00" if current == "#333333" else "#333333")
            self.jump_led_label.configure(text="AWAITING COORDS", text_color="#FFAA00")
            self.after(500, self._jump_led_blink_tick)
        else:
            self.after(100, self._jump_led_blink_tick)

    # Launcher lifecycle
    def on_activate(self, hw=None):
        pass

    def on_deactivate(self):
        pass

    def cleanup(self):
        self.bus.stop()


def main():
    """Standalone launcher."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    root.title("ComposeIO — BSG FTL Jump System")
    root.geometry("1300x800")

    # Check for --no-uart flag
    enable_uart = "--no-uart" not in sys.argv

    app = SimulatorApp(root, enable_uart=enable_uart)
    app.pack(fill="both", expand=True)

    root.protocol("WM_DELETE_WINDOW", lambda: (app.cleanup(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
