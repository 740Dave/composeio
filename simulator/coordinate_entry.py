"""CoordinateEntryWidget — TM1637 7-segment display + 12-key keypad emulation.

Simulates a ComposeIO module with dual TM1637 displays (8 digits total)
and a 12-key matrix keypad (0-9, *, #).

Namespaces:
  ftl_coordinates: string, current 8-digit coordinate value
  ftl_coord_status: enum [dark, empty_flashing, partial, confirm_flashing, committed]

Behavior:
  - Default (ftl_arm off): display dark, keypad ignored
  - ftl_arm on: display "00000000" flashing, accept input
  - Keypad fills from right: press "1" → "00000001"
  - 8 digits entered: all flash together for confirmation
  - *: clear entry
  - #: commit coordinates
"""

from __future__ import annotations
from typing import Any
import customtkinter as ctk

from composeio.bus import ComposeIOBus

# 7-segment display visual representation
_SEGMENT_FONT = ("Courier", 28, "bold")
_SEGMENT_DIM = "#1a2a1a"      # Dim green (off segments)
_SEGMENT_ON = "#00FF88"        # Bright green (on segments)
_SEGMENT_FLASH = "#005533"     # Mid green (flash off-phase)
_KEYPAD_KEYS = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"],
]


class SevenSegmentDisplay(ctk.CTkFrame):
    """Emulated dual TM1637 — 8-digit 7-segment display."""

    def __init__(self, master, digits: int = 8, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="#0a0a0a", corner_radius=8)
        self.digit_count = digits
        self.digit_labels: list[ctk.CTkLabel] = []
        self._flash_on = True
        self._flashing = False
        self._flash_digits: set[int] = set()

        # Create digit display
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=12, pady=12)

        for i in range(digits):
            # Add separator between the two "TM1637" units
            if i == 4:
                sep = ctk.CTkLabel(row, text=":", font=("Courier", 24), text_color="#333333")
                sep.pack(side="left", padx=4)

            label = ctk.CTkLabel(
                row,
                text="8",
                font=_SEGMENT_FONT,
                text_color=_SEGMENT_DIM,
                width=30,
            )
            label.pack(side="left", padx=2)
            self.digit_labels.append(label)

    def set_dark(self):
        """All segments off."""
        self._flashing = False
        self._flash_digits.clear()
        for lbl in self.digit_labels:
            lbl.configure(text="8", text_color=_SEGMENT_DIM)

    def set_digits(self, value: str, flash_indices: set[int] | None = None):
        """Set display to a value, optionally flashing some digits."""
        padded = value.ljust(self.digit_count, " ")[:self.digit_count]
        self._flash_digits = flash_indices or set()
        self._flashing = len(self._flash_digits) > 0

        for i, lbl in enumerate(self.digit_labels):
            ch = padded[i] if i < len(padded) else " "
            if ch == " ":
                lbl.configure(text="8", text_color=_SEGMENT_DIM)
            elif i in self._flash_digits:
                lbl.configure(text=ch, text_color=_SEGMENT_ON if self._flash_on else _SEGMENT_FLASH)
            else:
                lbl.configure(text=ch, text_color=_SEGMENT_ON)

        if self._flashing:
            self._start_flash()

    def _start_flash(self):
        """Start flash animation."""
        if not self._flashing:
            return
        self._flash_on = not self._flash_on
        for i in self._flash_digits:
            if i < len(self.digit_labels):
                lbl = self.digit_labels[i]
                current_text = lbl.cget("text")
                if current_text != "8" or lbl.cget("text_color") != _SEGMENT_DIM:
                    lbl.configure(text_color=_SEGMENT_ON if self._flash_on else _SEGMENT_FLASH)
        self.after(400, self._start_flash)


class KeypadWidget(ctk.CTkFrame):
    """12-key matrix keypad emulation."""

    def __init__(self, master, on_key_press=None, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color="transparent")
        self.on_key_press = on_key_press
        self._enabled = False

        for r, row_keys in enumerate(_KEYPAD_KEYS):
            for c, key in enumerate(row_keys):
                # Special colors for * and #
                if key == "*":
                    fg = "#663333"
                    hover = "#884444"
                elif key == "#":
                    fg = "#336633"
                    hover = "#448844"
                else:
                    fg = "#444444"
                    hover = "#555555"

                btn = ctk.CTkButton(
                    self,
                    text=key,
                    width=50,
                    height=50,
                    corner_radius=6,
                    fg_color=fg,
                    hover_color=hover,
                    font=("Helvetica", 16, "bold"),
                    command=lambda k=key: self._press(k),
                )
                btn.grid(row=r, column=c, padx=3, pady=3)

    def _press(self, key: str):
        if self._enabled and self.on_key_press:
            self.on_key_press(key)

    def set_enabled(self, enabled: bool):
        self._enabled = enabled


class CoordinateEntryWidget(ctk.CTkFrame):
    """Complete FTL coordinate entry module — display + keypad + bus integration."""

    def __init__(self, master, bus: ComposeIOBus, **kwargs):
        super().__init__(master, **kwargs)
        self.bus = bus
        self.configure(fg_color="#1e1e1e", corner_radius=8)

        self._digits_entered: list[str] = []
        self._max_digits = 8

        # Register namespaces
        bus.ensure_namespace("ftl_coordinates", {
            "type": "enum",
            "values": [""],  # Will hold arbitrary strings
        })
        bus.register_namespace("ftl_coord_status", {
            "type": "enum",
            "values": ["dark", "empty_flashing", "partial", "confirm_flashing", "committed"],
            "default": "dark",
        })

        # Title
        ctk.CTkLabel(
            self, text="FTL COORDINATES", font=("Helvetica", 14, "bold")
        ).pack(pady=(8, 4))

        # Status indicator
        self.status_label = ctk.CTkLabel(
            self, text="OFFLINE", font=("Courier", 10), text_color="#666666"
        )
        self.status_label.pack(pady=(0, 4))

        # 7-segment display
        self.display = SevenSegmentDisplay(self, digits=8)
        self.display.pack(padx=12, pady=4)

        # Keypad
        self.keypad = KeypadWidget(self, on_key_press=self._on_key)
        self.keypad.pack(padx=12, pady=(4, 12))

        # Subscribe to ftl_arm for activation
        bus.ensure_namespace("ftl_arm", "binary")
        bus.subscribe("ftl_arm", self._on_ftl_arm_change)

        # Subscribe to ftl_coord_status for external changes (e.g., jump reset)
        bus.subscribe("ftl_coord_status", self._on_status_change)

    def _on_ftl_arm_change(self, ns: str, new: Any, old: Any):
        """Activate/deactivate based on FTL arm state."""
        if new == "on":
            self._activate()
        else:
            self._deactivate()

    def _on_status_change(self, ns: str, new: Any, old: Any):
        """Handle external status changes (e.g., jump event resets to dark)."""
        if new == "dark":
            self._deactivate()

    def _activate(self):
        """FTL armed — show empty display, enable keypad."""
        self._digits_entered = []
        self.keypad.set_enabled(True)
        self._update_display()
        self.bus.publish_state("ftl_coord_status", "empty_flashing", source="ftl-coords")
        self.status_label.configure(text="AWAITING COORDINATES", text_color="#FFAA00")

    def _deactivate(self):
        """FTL disarmed — dark display, disable keypad."""
        self._digits_entered = []
        self.keypad.set_enabled(False)
        self.display.set_dark()
        self.bus.publish_state("ftl_coord_status", "dark", source="ftl-coords")
        self.bus.publish_state("ftl_coordinates", "", source="ftl-coords")
        self.status_label.configure(text="OFFLINE", text_color="#666666")

    def _on_key(self, key: str):
        """Handle keypad press."""
        status = self.bus.get_state("ftl_coord_status")

        if key == "*":
            # Clear entry
            self._digits_entered = []
            self._update_display()
            self.bus.publish_state("ftl_coord_status", "empty_flashing", source="ftl-coords")
            self.status_label.configure(text="AWAITING COORDINATES", text_color="#FFAA00")
            return

        if key == "#":
            # Commit — only if all 8 digits entered
            if len(self._digits_entered) == self._max_digits:
                self.bus.publish_state("ftl_coord_status", "committed", source="ftl-coords")
                coord_str = "".join(self._digits_entered)
                self.bus.publish_state("ftl_coordinates", coord_str, source="ftl-coords")
                self.status_label.configure(text="COORDINATES LOCKED", text_color="#00FF88")
                # Show all solid
                self.display.set_digits(coord_str, flash_indices=set())
            return

        # Digit entry (0-9)
        if key.isdigit() and status not in ("committed", "dark"):
            if len(self._digits_entered) < self._max_digits:
                self._digits_entered.append(key)
                self._update_display()

                if len(self._digits_entered) == self._max_digits:
                    self.bus.publish_state("ftl_coord_status", "confirm_flashing", source="ftl-coords")
                    self.status_label.configure(text="PRESS # TO CONFIRM", text_color="#FFCC00")
                else:
                    self.bus.publish_state("ftl_coord_status", "partial", source="ftl-coords")
                    self.status_label.configure(text=f"ENTERING ({len(self._digits_entered)}/8)", text_color="#FFAA00")

    def _update_display(self):
        """Update 7-segment display based on entered digits."""
        # Right-fill: entered digits at the right, zeros at the left
        entered = "".join(self._digits_entered)
        padded = entered.rjust(self._max_digits, "0")

        # Flash the zero-padded positions (not yet entered)
        entered_count = len(self._digits_entered)
        flash_set = set(range(0, self._max_digits - entered_count))

        if entered_count == self._max_digits:
            # All entered — flash all for confirmation
            flash_set = set(range(self._max_digits))

        self.display.set_digits(padded, flash_indices=flash_set)
