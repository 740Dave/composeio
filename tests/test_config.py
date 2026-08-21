"""Test YAML config parsing, validation, and binary compilation.

DP-CIO-7: Configuration is the product.
DP-CIO-14: Parameterized collections, not copy-paste.
"""

import pytest
from composeio.config import (
    load_config_string, validate_config, compile_binary,
    apply_config, namespace_hash, ConfigError,
)
from composeio.bus import ComposeIOBus


VALID_BUTTON_PANEL = """
module: panel-1
role: button-matrix
buttons:
  - namespace: ftl_arm
    input: toggle
    state_engine: binary
    led: follow_state

  - namespace: sublight_engines
    input: toggle
    state_engine: tri_level
    led: follow_state

  - namespace: alert_status
    input: toggle
    state_engine: stop_light
    led: follow_state

  - namespace: warp_factor
    input: toggle
    state_engine:
      type: enum
      values: [standby, warp_1, warp_5, warp_9, maximum]
      default: standby
    led: follow_state

  - namespace: launch
    input: momentary
    led: while_pressed
"""

VALID_RELAY = """
module: relay-1
role: relay-controller
channels:
  - namespace: ftl_arm
    channel: 1
    default: off
  - namespace: sublight_engines
    channel: 2
    default: off
"""


class TestValidation:
    def test_valid_button_panel(self):
        config = load_config_string(VALID_BUTTON_PANEL)
        errors = validate_config(config)
        assert errors == []

    def test_valid_relay(self):
        config = load_config_string(VALID_RELAY)
        errors = validate_config(config)
        assert errors == []

    def test_missing_module(self):
        config = load_config_string("role: button-matrix")
        errors = validate_config(config)
        assert any("module" in e for e in errors)

    def test_missing_role(self):
        config = load_config_string("module: test")
        errors = validate_config(config)
        assert any("role" in e for e in errors)

    def test_unknown_role(self):
        config = load_config_string("module: test\nrole: flying-car")
        errors = validate_config(config)
        assert any("Unknown role" in e for e in errors)

    def test_unknown_input_type(self):
        config = load_config_string("""
module: test
role: button-matrix
buttons:
  - namespace: test
    input: telekinesis
""")
        errors = validate_config(config)
        assert any("input type" in e for e in errors)

    def test_enum_missing_values(self):
        config = load_config_string("""
module: test
role: button-matrix
buttons:
  - namespace: test
    state_engine:
      type: enum
""")
        errors = validate_config(config)
        assert any("values" in e for e in errors)


class TestApplyConfig:
    def test_registers_namespaces(self):
        bus = ComposeIOBus()
        bus.start()
        config = load_config_string(VALID_BUTTON_PANEL)
        apply_config(bus, config)
        assert bus.get_namespace("ftl_arm") is not None
        assert bus.get_namespace("sublight_engines") is not None
        assert bus.get_namespace("warp_factor") is not None
        bus.stop()

    def test_warp_factor_is_enum(self):
        bus = ComposeIOBus()
        bus.start()
        config = load_config_string(VALID_BUTTON_PANEL)
        apply_config(bus, config)
        ns = bus.get_namespace("warp_factor")
        assert ns.state_engine.engine_type == "enum"
        assert ns.value == "standby"
        bus.stop()

    def test_invalid_config_raises(self):
        bus = ComposeIOBus()
        bus.start()
        config = load_config_string("role: button-matrix")
        with pytest.raises(ConfigError):
            apply_config(bus, config)
        bus.stop()


class TestBinaryCompilation:
    def test_button_panel_compiles(self):
        config = load_config_string(VALID_BUTTON_PANEL)
        binary = compile_binary(config)
        assert len(binary) > 10  # At least header
        assert binary[:2] == b"\xC1\x00"  # Magic bytes
        assert binary[3] == 0x01  # Role ID for button-matrix

    def test_relay_compiles(self):
        config = load_config_string(VALID_RELAY)
        binary = compile_binary(config)
        assert binary[:2] == b"\xC1\x00"
        assert binary[3] == 0x02  # Role ID for relay-controller

    def test_5_buttons_fits_128_bytes(self):
        """5 buttons should fit in CH32V003's 128-byte config region."""
        config = load_config_string(VALID_BUTTON_PANEL)
        binary = compile_binary(config)
        assert len(binary) <= 128

    def test_namespace_hash_deterministic(self):
        h1 = namespace_hash("ftl_arm")
        h2 = namespace_hash("ftl_arm")
        assert h1 == h2

    def test_namespace_hash_different(self):
        h1 = namespace_hash("ftl_arm")
        h2 = namespace_hash("sublight_engines")
        assert h1 != h2

    def test_invalid_config_fails_compilation(self):
        config = load_config_string("role: button-matrix")
        with pytest.raises(ConfigError):
            compile_binary(config)


class TestEndToEnd:
    """DP-CIO-2: Button panel and relay connected by namespace only."""

    def test_button_toggles_relay(self):
        """Complete scenario: button panel + relay, connected by namespace."""
        bus = ComposeIOBus()
        bus.start()

        # Apply both configs
        panel_config = load_config_string(VALID_BUTTON_PANEL)
        relay_config = load_config_string(VALID_RELAY)
        apply_config(bus, panel_config)
        apply_config(bus, relay_config)

        # Subscribe relay-side to ftl_arm
        relay_state = []
        bus.subscribe("ftl_arm", lambda ns, new, old: relay_state.append(new))

        # Simulate button toggle
        bus.publish_state("ftl_arm", "on", source="panel-1")
        assert relay_state == ["on"]
        assert bus.get_state("ftl_arm") == "on"

        bus.publish_state("ftl_arm", "off", source="panel-1")
        assert relay_state == ["on", "off"]

        bus.stop()
