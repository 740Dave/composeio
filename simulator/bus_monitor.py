"""BusMonitorWidget — live event stream display.

Shows all bus messages in real-time. The debugging window into the bus.
Validates DP-CIO-17 (infrastructure namespaces visible) and DP-CIO-18 (message format).
"""

from __future__ import annotations
import time
import customtkinter as ctk

from composeio.bus import ComposeIOBus
from composeio.transport import BusMessage, MessageType


# Color coding by message type
_TYPE_COLORS = {
    MessageType.STATE: "#00FF88",
    MessageType.EVENT: "#FFAA00",
    MessageType.HEARTBEAT: "#555555",
    MessageType.DISCOVERY: "#66AAFF",
    MessageType.TIME_SYNC: "#555555",
    MessageType.CONFIG_PUSH: "#FF66AA",
    MessageType.CONFIG_REQUEST: "#FF66AA",
    MessageType.BINDING: "#AA66FF",
}


class BusMonitorWidget(ctk.CTkFrame):
    """Live bus message stream with filtering."""

    def __init__(
        self,
        master,
        bus: ComposeIOBus,
        max_lines: int = 200,
        show_heartbeats: bool = False,
        **kwargs,
    ):
        super().__init__(master, **kwargs)
        self.bus = bus
        self.max_lines = max_lines
        self.show_heartbeats = show_heartbeats
        self._lines: list[str] = []

        self.configure(fg_color="#111111", corner_radius=8)

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(8, 4))

        ctk.CTkLabel(
            header, text="BUS MONITOR", font=("Courier", 12, "bold")
        ).pack(side="left")

        self.msg_count_label = ctk.CTkLabel(
            header, text="0 msgs", font=("Courier", 10), text_color="#666666"
        )
        self.msg_count_label.pack(side="right")

        # Filter toggle
        self.heartbeat_var = ctk.BooleanVar(value=show_heartbeats)
        ctk.CTkCheckBox(
            header,
            text="HB",
            variable=self.heartbeat_var,
            width=40,
            font=("Courier", 9),
            command=self._toggle_heartbeats,
        ).pack(side="right", padx=8)

        # Text display
        self.textbox = ctk.CTkTextbox(
            self,
            font=("Courier", 10),
            fg_color="#0a0a0a",
            text_color="#888888",
            state="disabled",
            wrap="none",
            height=300,
        )
        self.textbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Subscribe to all bus messages
        bus.on_message(self._on_message)

    def _toggle_heartbeats(self):
        self.show_heartbeats = self.heartbeat_var.get()

    def _on_message(self, msg: BusMessage):
        """Handle incoming bus message — format and display."""
        # Filter heartbeats if disabled
        if msg.type in (MessageType.HEARTBEAT, MessageType.TIME_SYNC) and not self.show_heartbeats:
            return

        # Format the line
        ts = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
        type_str = msg.type.value.upper()[:5].ljust(5)

        if msg.type == MessageType.STATE:
            detail = f"{msg.namespace} = {msg.value}"
        elif msg.type == MessageType.EVENT:
            detail = f"{msg.namespace} ! {msg.event_type}"
            if msg.data:
                detail += f" {msg.data}"
        elif msg.type == MessageType.DISCOVERY:
            detail = f"{msg.source} joined ({msg.data.get('role', '?')})"
        else:
            detail = f"{msg.namespace}"

        src = msg.source[:12].ljust(12) if msg.source else "".ljust(12)
        line = f"{ts} {type_str} {src} {detail}"

        # Add to display
        self._lines.append(line)
        if len(self._lines) > self.max_lines:
            self._lines = self._lines[-self.max_lines:]

        # Update textbox (must be done in main thread for CTk)
        try:
            self.textbox.configure(state="normal")
            self.textbox.insert("end", line + "\n")
            self.textbox.see("end")
            self.textbox.configure(state="disabled")
            self.msg_count_label.configure(text=f"{len(self._lines)} msgs")
        except Exception:
            pass  # Widget may be destroyed
