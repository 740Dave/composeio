"""Test DP-CIO-13 (modal buttons) and DP-CIO-16 (control priority).

DP-CIO-13: A button's behavior changes based on context (time, state, game phase).
DP-CIO-16: disabled > manual > remote > auto priority stack at the edge.

Both emerged from the tos-gumdrops ESPHome analysis:
- Button 3 normally toggles monitors, but during 12:30-12:59 AM it toggles shutdown suppression
- Laser fan has disabled > manual > auto priority for fan speed control
"""

import pytest
from composebus.bus import ComposeIOBus
from composebus.namespace import Scope


class TestModalButtons:
    """DP-CIO-13: Context changes button behavior."""

    def test_compound_binding_as_modal(self):
        """Modal behavior via compound binding: different output based on a mode namespace."""
        bus = ComposeIOBus()
        bus.start()

        # Mode namespace (like game phase or time window)
        bus.register_namespace("game_phase", {
            "type": "enum",
            "values": ["setup", "combat", "exploration"],
            "default": "setup",
        })

        # The button always publishes to "console_action"
        bus.register_namespace("console_action", "binary")

        # But the EFFECT depends on game phase
        bus.register_namespace("weapons_arm", "binary")
        bus.register_namespace("shields_raise", "binary")
        bus.register_namespace("scanner_activate", "binary")

        # Compound binding: console_action routes to different targets based on game_phase
        bus.add_compound_binding({
            "output_namespace": "weapons_arm",
            "requires": 'game_phase == "combat"',
            "default": "off",
            "conditions": [
                {"when": {"console_action": "== on"}, "value": "on"},
            ],
        })
        bus.add_compound_binding({
            "output_namespace": "shields_raise",
            "requires": 'game_phase == "combat"',
            "default": "off",
            "conditions": [
                {"when": {"console_action": "== on"}, "value": "on"},
            ],
        })
        bus.add_compound_binding({
            "output_namespace": "scanner_activate",
            "requires": 'game_phase == "exploration"',
            "default": "off",
            "conditions": [
                {"when": {"console_action": "== on"}, "value": "on"},
            ],
        })

        # In setup phase: button does nothing (no requires match)
        bus.publish_state("console_action", "on")
        assert bus.get_state("weapons_arm") == "off"
        assert bus.get_state("scanner_activate") == "off"

        # Switch to combat: button arms weapons
        bus.publish_state("game_phase", "combat")
        bus.publish_state("console_action", "on")
        assert bus.get_state("weapons_arm") == "on"
        assert bus.get_state("scanner_activate") == "off"

        # Switch to exploration: button activates scanner
        bus.publish_state("game_phase", "exploration")
        # Weapons should deactivate (requires no longer met)
        bus.publish_state("console_action", "on")
        assert bus.get_state("scanner_activate") == "on"

        bus.stop()

    def test_mode_switch_resets_output(self):
        """When mode changes, compound binding default takes effect."""
        bus = ComposeIOBus()
        bus.start()

        bus.register_namespace("mode", "binary")
        bus.register_namespace("output", "binary")

        bus.add_compound_binding({
            "output_namespace": "output",
            "requires": 'mode == "on"',
            "default": "off",
            "conditions": [
                {"when": {"mode": "== on"}, "value": "on"},
            ],
        })

        bus.publish_state("mode", "on")
        assert bus.get_state("output") == "on"

        bus.publish_state("mode", "off")
        assert bus.get_state("output") == "off"

        bus.stop()


class TestControlPriority:
    """DP-CIO-16: disabled > manual > remote > auto priority stack.

    Implemented as compound bindings where the highest-priority active source wins.
    """

    def test_priority_stack(self):
        """Disabled override beats all other sources via compound binding."""
        bus = ComposeIOBus()
        bus.start()

        # Priority: disabled flag overrides sensor-driven auto value
        bus.register_namespace("fan_disabled", "binary")
        bus.register_namespace("fan_sensor", "continuous_0_255")
        bus.register_namespace("fan_speed", "continuous_0_255")

        # Compound binding: if not disabled, follow sensor. If disabled, force 0.
        bus.add_compound_binding({
            "output_namespace": "fan_speed",
            "requires": 'fan_disabled == "off"',
            "default": 0,
            "conditions": [
                {"when": {"fan_sensor": ">= 0"}, "value": 128},
            ],
        })

        # 1. Normal: sensor drives fan
        bus.publish_state("fan_disabled", "off")
        bus.publish_state("fan_sensor", 128)
        assert bus.get_state("fan_speed") == 128

        # 2. Disabled: output forced to 0
        bus.publish_state("fan_disabled", "on")
        assert bus.get_state("fan_speed") == 0

        # 3. Re-enable: output restores
        bus.publish_state("fan_disabled", "off")
        # Need to re-trigger sensor to re-evaluate
        bus.publish_state("fan_sensor", 100)
        assert bus.get_state("fan_speed") == 128  # Compound always emits 128 when condition met

        bus.stop()

    def test_disabled_overrides_everything(self):
        """Safety lockout (disabled) always wins."""
        bus = ComposeIOBus()
        bus.start()

        bus.register_namespace("safety_lockout", "binary")
        bus.register_namespace("actuator", "binary")

        bus.add_compound_binding({
            "output_namespace": "actuator",
            "requires": 'safety_lockout == "off"',
            "default": "off",
            "conditions": [
                {"when": {"safety_lockout": "== off"}, "value": "on"},
            ],
        })

        # Normal operation
        bus.publish_state("safety_lockout", "off")
        assert bus.get_state("actuator") == "on"

        # Lockout
        bus.publish_state("safety_lockout", "on")
        assert bus.get_state("actuator") == "off"

        bus.stop()


class TestTimeWindowModal:
    """DP-CIO-13: Button 3 time-window behavior from tos-gumdrops.

    The actual tos-gumdrops pattern: during 12:30-12:59 AM, button 3 toggles
    suppress_shutdown instead of monitors. We model this with a mode namespace
    that's set by the bus time infrastructure.
    """

    def test_time_window_via_mode_namespace(self):
        """Time-based modal: mode namespace controls which behavior is active."""
        bus = ComposeIOBus()
        bus.start()

        # Time window mode (would be driven by _bus/time in production)
        bus.register_namespace("shutdown_warning_window", "binary")

        # Button press always goes to same namespace
        bus.register_namespace("button3_press", "binary")

        # Two possible effects
        bus.register_namespace("monitor_power", "binary")
        bus.register_namespace("suppress_shutdown", "binary")

        # Normal mode: button toggles monitors
        bus.add_compound_binding({
            "output_namespace": "monitor_power",
            "requires": 'shutdown_warning_window == "off"',
            "default": "off",
            "conditions": [
                {"when": {"button3_press": "== on"}, "value": "on"},
                {"when": {"button3_press": "== off"}, "value": "off"},
            ],
        })

        # Warning window mode: button toggles suppression
        bus.add_compound_binding({
            "output_namespace": "suppress_shutdown",
            "requires": 'shutdown_warning_window == "on"',
            "default": "off",
            "conditions": [
                {"when": {"button3_press": "== on"}, "value": "on"},
                {"when": {"button3_press": "== off"}, "value": "off"},
            ],
        })

        # Normal mode — button controls monitors
        bus.publish_state("shutdown_warning_window", "off")
        bus.publish_state("button3_press", "on")
        assert bus.get_state("monitor_power") == "on"
        assert bus.get_state("suppress_shutdown") == "off"

        # Enter warning window — same button now controls suppression
        bus.publish_state("shutdown_warning_window", "on")
        bus.publish_state("button3_press", "on")
        assert bus.get_state("suppress_shutdown") == "on"

        bus.stop()
