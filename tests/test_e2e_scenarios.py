"""End-to-end scenario tests mapping to design principles.

These test complete workflows from the BOOTSTRAP.md deliverable 7 spec:
- Button panel → namespace → relay (in-process transport)
- Sensor → threshold transform → actuator
- Dynamic namespace selector (camera joystick)
- Composite device concepts
"""

import pytest
from composeio.bus import ComposeIOBus
from composeio.config import load_config_string, apply_config
from composeio.module import Module
from composeio.namespace import Scope, Authority


class TestButtonPanelToRelay:
    """DP-CIO-1, DP-CIO-2: Button + relay connected by namespace only."""

    PANEL_CONFIG = """
module: panel-1
role: button-matrix
buttons:
  - namespace: office_lights
    input: toggle
    state_engine: binary
    led: follow_state
  - namespace: sublight_engines
    input: toggle
    state_engine: tri_level
    led: follow_state
"""

    RELAY_CONFIG = """
module: relay-1
role: relay-controller
channels:
  - namespace: office_lights
    channel: 1
    default: off
  - namespace: sublight_engines
    channel: 2
    default: off
"""

    def test_toggle_propagates(self):
        bus = ComposeIOBus()
        bus.start()
        apply_config(bus, load_config_string(self.PANEL_CONFIG))
        apply_config(bus, load_config_string(self.RELAY_CONFIG))

        relay_state = {}

        def on_lights(ns, new, old):
            relay_state["lights"] = new

        def on_power(ns, new, old):
            relay_state["power"] = new

        bus.subscribe("office_lights", on_lights)
        bus.subscribe("sublight_engines", on_power)

        # Toggle office lights
        bus.publish_state("office_lights", "on", source="panel-1")
        assert relay_state["lights"] == "on"

        # Toggle engine power through tri-level
        ns = bus.get_namespace("sublight_engines")
        ns.advance()  # off -> half
        assert relay_state["power"] == "half"
        ns.advance()  # half -> full
        assert relay_state["power"] == "full"
        ns.advance()  # full -> off
        assert relay_state["power"] == "off"

        bus.stop()

    def test_swap_module_no_config_change(self):
        """DP-CIO-2: Swapping a module requires zero config changes elsewhere."""
        bus = ComposeIOBus()
        bus.start()
        apply_config(bus, load_config_string(self.PANEL_CONFIG))

        # Original relay
        apply_config(bus, load_config_string(self.RELAY_CONFIG))
        received = []
        bus.subscribe("office_lights", lambda ns, new, old: received.append(("relay-1", new)))

        bus.publish_state("office_lights", "on")
        assert received[-1] == ("relay-1", "on")

        # "Swap" relay — same namespace, different module
        RELAY_2 = """
module: relay-2
role: relay-controller
channels:
  - namespace: office_lights
    channel: 1
    default: off
"""
        apply_config(bus, load_config_string(RELAY_2))
        bus.subscribe("office_lights", lambda ns, new, old: received.append(("relay-2", new)))

        bus.publish_state("office_lights", "off")
        # Both relays see the change — multiple consumers on same namespace
        assert any(r[0] == "relay-2" for r in received)

        bus.stop()


class TestSensorActuatorBinding:
    """DP-CIO-10: Sensor-to-actuator bindings without intermediary servers."""

    def test_laser_fan_binding(self):
        """Real-world pattern: laser power draw drives fan speed tiers."""
        bus = ComposeIOBus()
        bus.start()

        bus.register_namespace("laser_power", "continuous_0_255")
        bus.register_namespace("fan_speed", {
            "type": "enum",
            "values": ["off", "low", "medium", "high", "max"],
            "default": "off",
        })

        fan_states = []
        bus.subscribe("fan_speed", lambda ns, new, old: fan_states.append(new))

        # Binding with threshold transform (replaces HA automation + Node-RED)
        bus.add_binding("laser_power", "fan_speed", transform_config=[
            {"threshold": [
                {"above": 200, "emit": "max"},
                {"above": 100, "emit": "high"},
                {"above": 50, "emit": "medium"},
                {"above": 10, "emit": "low"},
            ]},
        ])

        bus.publish_state("laser_power", 5)  # Below all thresholds
        assert len(fan_states) == 0  # Suppressed

        bus.publish_state("laser_power", 30)
        assert fan_states[-1] == "low"

        bus.publish_state("laser_power", 75)
        assert fan_states[-1] == "medium"

        bus.publish_state("laser_power", 150)
        assert fan_states[-1] == "high"

        bus.publish_state("laser_power", 250)
        assert fan_states[-1] == "max"

        bus.stop()


class TestDynamicNamespaceSelector:
    """DP-CIO-21: Camera joystick pattern — selector changes target namespace."""

    def test_camera_joystick_pattern(self):
        bus = ComposeIOBus()
        bus.start()

        # Register camera selection namespace
        bus.register_namespace("camera_select", {
            "type": "enum",
            "values": ["cam1", "cam2", "cam3"],
            "default": "cam1",
        })

        # Register PTZ namespaces for each camera
        for i in range(1, 4):
            bus.register_namespace(f"cam{i}_ptz", "continuous_0_255")

        # Track which cameras receive PTZ commands
        ptz_received = {f"cam{i}": [] for i in range(1, 4)}
        for i in range(1, 4):
            cam = f"cam{i}"
            bus.subscribe(f"{cam}_ptz", lambda ns, new, old, c=cam: ptz_received[c].append(new))

        # Select camera 1, send PTZ
        bus.set_selector("camera_select", "cam1")
        bus.publish_state("${camera_select}_ptz", 100)
        assert ptz_received["cam1"] == [100]
        assert ptz_received["cam2"] == []
        assert ptz_received["cam3"] == []

        # Switch to camera 2, send PTZ
        bus.set_selector("camera_select", "cam2")
        bus.publish_state("${camera_select}_ptz", 75)
        assert ptz_received["cam2"] == [75]

        # Camera 3 has no PTZ hardware — messages arrive but no subscriber
        # (DP-CIO-21: ignore-by-design, no error)
        bus.set_selector("camera_select", "cam3")
        bus.publish_state("${camera_select}_ptz", 50)
        assert ptz_received["cam3"] == [50]  # Subscriber exists in test, but in real system it wouldn't

        bus.stop()


class TestGracefulDegradation:
    """DP-CIO-11: System degrades gracefully, not catastrophically."""

    def test_local_bindings_survive_without_global(self):
        """Local namespace bindings work without global transport."""
        bus = ComposeIOBus(global_transport=None)  # No global transport
        bus.start()

        bus.register_namespace("local_button", "binary", scope=Scope.LOCAL)
        bus.register_namespace("local_relay", "binary", scope=Scope.LOCAL)

        relay_state = []
        bus.subscribe("local_relay", lambda ns, new, old: relay_state.append(new))
        bus.add_binding("local_button", "local_relay")

        # Toggle works without any global transport
        bus.publish_state("local_button", "on")
        assert relay_state == ["on"]

        bus.stop()


class TestStateAuthority:
    """DP-CIO-19: State authority — local vs. external."""

    def test_local_authority(self):
        """Local authority: state change is immediate."""
        bus = ComposeIOBus()
        bus.start()
        bus.register_namespace("local_relay", "binary", authority=Authority.LOCAL)

        states = []
        bus.subscribe("local_relay", lambda ns, new, old: states.append(new))
        bus.publish_state("local_relay", "on")
        assert states == ["on"]
        bus.stop()

    def test_external_authority_declared(self):
        """External authority: namespace tracks that authority is external."""
        bus = ComposeIOBus()
        bus.start()
        ns = bus.register_namespace("sonos_zone", "binary", authority=Authority.EXTERNAL)
        assert ns.authority == Authority.EXTERNAL
        bus.stop()


class TestStopLightPattern:
    """DP-CIO-4: The actual tiki bar use case — 3 buttons cycling alert levels."""

    def test_tiki_bar_alert(self):
        bus = ComposeIOBus()
        bus.start()
        bus.register_namespace("alert_level", "stop_light")

        alerts = []
        bus.subscribe("alert_level", lambda ns, new, old: alerts.append(new))

        ns = bus.get_namespace("alert_level")
        assert ns.value == "green"

        ns.advance()
        assert alerts[-1] == "yellow"

        ns.advance()
        assert alerts[-1] == "red"

        ns.advance()
        assert alerts[-1] == "green"

        bus.stop()


class TestWarpFactorEnum:
    """DP-CIO-4: Custom enum state engine from design principles."""

    def test_warp_factor_cycle(self):
        bus = ComposeIOBus()
        bus.start()
        ns = bus.register_namespace("warp_factor", {
            "type": "enum",
            "values": ["standby", "warp_1", "warp_5", "warp_9", "maximum"],
            "default": "standby",
        })

        states = []
        bus.subscribe("warp_factor", lambda name, new, old: states.append(new))

        ns.advance()
        assert states == ["warp_1"]
        ns.advance()
        assert states[-1] == "warp_5"
        ns.advance()
        assert states[-1] == "warp_9"
        ns.advance()
        assert states[-1] == "maximum"
        ns.advance()
        assert states[-1] == "standby"  # Wraps around

        bus.stop()
