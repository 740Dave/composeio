"""Test compound namespace bindings — multi-source condition evaluation.

Architecture extension from Architect spec: FTL Jump System.
Extends DP-CIO-10 for cases where an output depends on multiple inputs.
"""

import pytest
from composebus.bus import ComposeIOBus
from composebus.namespace import Scope


class TestCompoundBindings:
    """FTL Jump LED depends on ftl_arm AND ftl_cap_charge AND ftl_coord_status."""

    def setup_method(self):
        self.bus = ComposeIOBus()
        self.bus.start()
        self.bus.register_namespace("ftl_arm", "binary")
        self.bus.register_namespace("ftl_cap_charge", "continuous_0_255")
        self.bus.register_namespace("ftl_coord_status", {
            "type": "enum",
            "values": ["dark", "empty_flashing", "partial", "confirm_flashing", "committed"],
            "default": "dark",
        })
        self.bus.register_namespace("ftl_jump_led", {
            "type": "enum",
            "values": ["off", "blink_fast", "blink_slow", "solid"],
            "default": "off",
        })
        self.led_states = []
        self.bus.subscribe("ftl_jump_led", lambda ns, new, old: self.led_states.append(new))

        # Add the compound binding from the Architect's spec
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

    def teardown_method(self):
        self.bus.stop()

    def test_default_when_requires_not_met(self):
        """When ftl_arm is off, LED should be off regardless of other states."""
        self.bus.publish_state("ftl_cap_charge", 80)
        self.bus.publish_state("ftl_coord_status", "committed")
        # ftl_arm is still "off" — LED should remain off
        assert self.bus.get_state("ftl_jump_led") == "off"

    def test_blink_fast_when_charge_low(self):
        """ftl_arm on + charge < 50 → blink_fast."""
        self.bus.publish_state("ftl_arm", "on")
        self.bus.publish_state("ftl_cap_charge", 30)
        assert self.bus.get_state("ftl_jump_led") == "blink_fast"

    def test_blink_slow_when_charge_high_no_coords(self):
        """ftl_arm on + charge >= 50 + coords not committed → blink_slow."""
        self.bus.publish_state("ftl_arm", "on")
        self.bus.publish_state("ftl_cap_charge", 60)
        self.bus.publish_state("ftl_coord_status", "partial")
        assert self.bus.get_state("ftl_jump_led") == "blink_slow"

    def test_solid_when_ready(self):
        """ftl_arm on + charge >= 50 + coords committed → solid."""
        self.bus.publish_state("ftl_arm", "on")
        self.bus.publish_state("ftl_cap_charge", 75)
        self.bus.publish_state("ftl_coord_status", "committed")
        assert self.bus.get_state("ftl_jump_led") == "solid"

    def test_full_sequence(self):
        """Walk through the entire FTL jump sequence."""
        # 1. Arm
        self.bus.publish_state("ftl_arm", "on")
        # Charge starts at 0, so < 50 → blink_fast
        self.bus.publish_state("ftl_cap_charge", 10)
        assert self.bus.get_state("ftl_jump_led") == "blink_fast"

        # 2. Charge rises, still no coords
        self.bus.publish_state("ftl_cap_charge", 55)
        assert self.bus.get_state("ftl_jump_led") == "blink_slow"

        # 3. Enter coords
        self.bus.publish_state("ftl_coord_status", "partial")
        assert self.bus.get_state("ftl_jump_led") == "blink_slow"  # Still not committed

        # 4. Commit coords
        self.bus.publish_state("ftl_coord_status", "committed")
        assert self.bus.get_state("ftl_jump_led") == "solid"

        # 5. Disarm — LED goes off
        self.bus.publish_state("ftl_arm", "off")
        assert self.bus.get_state("ftl_jump_led") == "off"

    def test_requires_transition(self):
        """LED resets to default when requires condition becomes false."""
        self.bus.publish_state("ftl_arm", "on")
        self.bus.publish_state("ftl_cap_charge", 80)
        self.bus.publish_state("ftl_coord_status", "committed")
        assert self.bus.get_state("ftl_jump_led") == "solid"

        # Disarm
        self.bus.publish_state("ftl_arm", "off")
        assert self.bus.get_state("ftl_jump_led") == "off"

    def test_condition_priority(self):
        """First matching condition wins (top-to-bottom)."""
        self.bus.publish_state("ftl_arm", "on")
        # Charge exactly at boundary
        self.bus.publish_state("ftl_cap_charge", 49)
        assert self.bus.get_state("ftl_jump_led") == "blink_fast"  # < 50

        self.bus.publish_state("ftl_cap_charge", 50)
        # >= 50 and coord_status is "dark" (!= committed) → blink_slow
        assert self.bus.get_state("ftl_jump_led") == "blink_slow"


class TestEventBindings:
    """Event-triggered bindings with side effects (jump sequence)."""

    def setup_method(self):
        self.bus = ComposeIOBus()
        self.bus.start()
        self.bus.register_namespace("ftl_arm", "binary")
        self.bus.register_namespace("ftl_cap_charge", "continuous_0_255")
        self.bus.register_namespace("ftl_coord_status", {
            "type": "enum",
            "values": ["dark", "empty_flashing", "partial", "confirm_flashing", "committed"],
            "default": "dark",
        })
        self.bus.register_namespace("ftl_jump", "binary")
        self.bus.register_namespace("ftl_jump_led", {
            "type": "enum",
            "values": ["off", "blink_fast", "blink_slow", "solid"],
            "default": "off",
        })

    def teardown_method(self):
        self.bus.stop()

    def test_event_side_effects(self):
        """Jump event resets all FTL state."""
        # Set up armed state
        self.bus.publish_state("ftl_arm", "on")
        self.bus.publish_state("ftl_cap_charge", 80)
        self.bus.publish_state("ftl_coord_status", "committed")

        # Add event binding
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

        # Fire jump event
        self.bus.publish_event("ftl_jump", "jump")

        # All state should reset
        assert self.bus.get_state("ftl_arm") == "off"
        assert self.bus.get_state("ftl_cap_charge") == 0
        assert self.bus.get_state("ftl_coord_status") == "dark"
        assert self.bus.get_state("ftl_jump_led") == "off"


class TestNamespaceLabels:
    """DP-CIO-2 extension: namespace label property."""

    def test_label_set_on_registration(self):
        bus = ComposeIOBus()
        bus.start()
        ns = bus.register_namespace("sublight_engines", "tri_level", label="Sublight Engines")
        assert ns.label == "Sublight Engines"
        assert ns.display_name == "Sublight Engines"
        bus.stop()

    def test_display_name_fallback(self):
        bus = ComposeIOBus()
        bus.start()
        ns = bus.register_namespace("ftl_arm", "binary")
        assert ns.label is None
        assert ns.display_name == "Ftl Arm"
        bus.stop()

    def test_label_in_discovery(self):
        bus = ComposeIOBus()
        bus.start()
        ns = bus.register_namespace("test_ns", "binary", label="Test Namespace")
        d = ns.to_dict()
        assert d["label"] == "Test Namespace"
        bus.stop()

    def test_label_not_in_dict_when_none(self):
        bus = ComposeIOBus()
        bus.start()
        ns = bus.register_namespace("test_ns", "binary")
        d = ns.to_dict()
        assert "label" not in d
        bus.stop()

    def test_ensure_namespace_propagates_label(self):
        bus = ComposeIOBus()
        bus.start()
        ns = bus.ensure_namespace("my_ns", "binary", label="My Label")
        assert ns.label == "My Label"
        # Calling again without label shouldn't overwrite
        ns2 = bus.ensure_namespace("my_ns", "binary")
        assert ns2.label == "My Label"
        bus.stop()
