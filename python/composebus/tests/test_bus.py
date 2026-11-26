"""Test the ComposeIO bus — core pub/sub, scope resolution, bindings.

Tests map directly to design principles:
- DP-CIO-1: Add module via config, no code changes
- DP-CIO-2: Namespace-only connections
- DP-CIO-5: Local/global/remote scope resolution
- DP-CIO-10: Sensor-to-actuator bindings
- DP-CIO-11: Graceful degradation
- DP-CIO-17: Bus infrastructure namespaces
- DP-CIO-21: Dynamic namespace selectors
"""

import pytest
from composebus.bus import ComposeIOBus
from composebus.namespace import Scope, Authority
from composebus.module import Module
from composebus.transport import BusMessage, MessageType


class TestNamespaceRegistration:
    """DP-CIO-1: Adding hardware is configuration."""

    def test_register_namespace(self, bus):
        ns = bus.register_namespace("ftl_arm", "binary")
        assert ns.name == "ftl_arm"
        assert ns.value == "off"

    def test_register_enum_namespace(self, bus):
        ns = bus.register_namespace("warp_factor", {
            "type": "enum",
            "values": ["standby", "warp_1", "warp_5"],
            "default": "standby",
        })
        assert ns.value == "standby"

    def test_duplicate_registration_returns_existing(self, bus):
        ns1 = bus.register_namespace("test", "binary")
        ns2 = bus.register_namespace("test", "binary")
        assert ns1 is ns2


class TestPubSub:
    """DP-CIO-2: Pub/sub by namespace, not hardware address."""

    def test_state_change_notification(self, bus):
        bus.register_namespace("office_lights", "binary")
        received = []
        bus.subscribe("office_lights", lambda ns, new, old: received.append((ns, new, old)))
        bus.publish_state("office_lights", "on", source="panel-1")
        assert received == [("office_lights", "on", "off")]

    def test_multiple_subscribers(self, bus):
        """Two consumers on the same namespace both receive."""
        bus.register_namespace("red_alert", "binary")
        r1, r2 = [], []
        bus.subscribe("red_alert", lambda ns, new, old: r1.append(new))
        bus.subscribe("red_alert", lambda ns, new, old: r2.append(new))
        bus.publish_state("red_alert", "on")
        assert r1 == ["on"]
        assert r2 == ["on"]

    def test_no_notification_on_same_value(self, bus):
        """Publishing the same value doesn't trigger subscribers."""
        bus.register_namespace("test", "binary")
        received = []
        bus.subscribe("test", lambda ns, new, old: received.append(new))
        bus.publish_state("test", "off")  # Already "off"
        assert received == []

    def test_events(self, bus):
        """One-shot events are separate from state changes."""
        bus.register_namespace("scanner", "binary")
        events = []
        bus.subscribe_events("scanner", lambda ns, evt, data: events.append((evt, data)))
        bus.publish_event("scanner", "scan_complete", {"player": 2})
        assert events == [("scan_complete", {"player": 2})]

    def test_namespace_isolation(self, bus):
        """Messages on one namespace don't leak to another."""
        bus.register_namespace("ns_a", "binary")
        bus.register_namespace("ns_b", "binary")
        received = []
        bus.subscribe("ns_a", lambda ns, new, old: received.append(ns))
        bus.publish_state("ns_b", "on")
        assert received == []


class TestScopeResolution:
    """DP-CIO-5: Local/global/remote scopes with auto-resolution."""

    def test_auto_resolves_to_local_with_subscriber(self, bus):
        bus.register_namespace("local_test", "binary", scope=Scope.AUTO)
        bus.subscribe("local_test", lambda ns, new, old: None)
        # Publish and check message log
        bus.publish_state("local_test", "on")
        # The last message should have scope resolved
        last_msg = bus.message_log[-1]
        # With local subscribers, auto should resolve to local
        assert last_msg.scope == "local"

    def test_explicit_global_scope(self, bus):
        bus.register_namespace("global_test", "binary", scope=Scope.GLOBAL)
        bus.publish_state("global_test", "on")
        last_msg = bus.message_log[-1]
        assert last_msg.scope == "global"

    def test_explicit_local_scope(self, bus):
        bus.register_namespace("local_only", "binary", scope=Scope.LOCAL)
        bus.publish_state("local_only", "on")
        last_msg = bus.message_log[-1]
        assert last_msg.scope == "local"


class TestBindings:
    """DP-CIO-10: Sensor-to-actuator bindings without intermediary servers."""

    def test_simple_binding(self, bus):
        """Source namespace change propagates to target."""
        bus.register_namespace("sensor", "continuous_0_255")
        bus.register_namespace("actuator", "continuous_0_255")
        received = []
        bus.subscribe("actuator", lambda ns, new, old: received.append(new))
        bus.add_binding("sensor", "actuator")
        bus.publish_state("sensor", 128)
        assert received == [128]

    def test_binding_with_threshold_transform(self, bus):
        """DP-CIO-22: Threshold transform in binding."""
        bus.register_namespace("laser_power", "continuous_0_255")
        bus.register_namespace("fan_speed", {
            "type": "enum",
            "values": ["off", "tier_1", "tier_2", "tier_3"],
        })
        received = []
        bus.subscribe("fan_speed", lambda ns, new, old: received.append(new))
        bus.add_binding("laser_power", "fan_speed", transform_config=[
            {"threshold": [
                {"above": 200, "emit": "tier_3"},
                {"above": 100, "emit": "tier_2"},
                {"above": 10, "emit": "tier_1"},
            ]},
        ])
        bus.publish_state("laser_power", 50)
        assert received == ["tier_1"]
        bus.publish_state("laser_power", 150)
        assert received[-1] == "tier_2"
        bus.publish_state("laser_power", 250)
        assert received[-1] == "tier_3"

    def test_binding_with_scale(self, bus):
        """Scale transform: ADC range to normalized."""
        bus.register_namespace("adc_raw", "continuous_0_255")
        bus.register_namespace("normalized", "continuous_0_255")
        received = []
        bus.subscribe("normalized", lambda ns, new, old: received.append(new))
        bus.add_binding("adc_raw", "normalized", transform_config=[
            {"scale": {"in": [0, 255], "out": [0, 100]}},
        ])
        bus.publish_state("adc_raw", 128)
        # ~50.2
        assert 49 < received[-1] < 52


class TestDynamicSelectors:
    """DP-CIO-21: Dynamic namespace selectors."""

    def test_selector_resolves_namespace(self, bus):
        bus.register_namespace("camera_select", {
            "type": "enum",
            "values": ["camera_1", "camera_2", "camera_3"],
        })
        bus.register_namespace("camera_2_ptz", "continuous_0_255")
        received = []
        bus.subscribe("camera_2_ptz", lambda ns, new, old: received.append(new))

        bus.set_selector("camera_select", "camera_2")
        bus.publish_state("${camera_select}_ptz", 100)
        assert received == [100]

    def test_selector_changes_target(self, bus):
        """Changing selector redirects output."""
        bus.register_namespace("camera_select", {
            "type": "enum",
            "values": ["cam1", "cam2"],
        })
        bus.register_namespace("cam1_ptz", "continuous_0_255")
        bus.register_namespace("cam2_ptz", "continuous_0_255")
        r1, r2 = [], []
        bus.subscribe("cam1_ptz", lambda ns, new, old: r1.append(new))
        bus.subscribe("cam2_ptz", lambda ns, new, old: r2.append(new))

        bus.set_selector("camera_select", "cam1")
        bus.publish_state("${camera_select}_ptz", 50)
        assert r1 == [50]
        assert r2 == []

        bus.set_selector("camera_select", "cam2")
        bus.publish_state("${camera_select}_ptz", 75)
        assert r2 == [75]


class TestModules:
    def test_module_registration(self, bus):
        m = Module("panel-1", "button-matrix")
        bus.register_module(m)
        assert "panel-1" in bus.status()["modules"]

    def test_module_publish(self, bus):
        m = Module("panel-1", "button-matrix")
        bus.register_module(m)
        bus.register_namespace("test_ns", "binary")
        received = []
        bus.subscribe("test_ns", lambda ns, new, old: received.append(new))
        m.publish_state("test_ns", "on")
        assert received == ["on"]


class TestBusInfrastructure:
    """DP-CIO-17: Bus provides shared infrastructure services."""

    def test_bus_time_published_on_start(self, bus):
        """Bus publishes _bus/time on start."""
        time_msgs = [m for m in bus.message_log if m.namespace == "_bus/time"]
        assert len(time_msgs) >= 1

    def test_heartbeat(self, bus):
        bus.publish_heartbeat()
        hb_msgs = [m for m in bus.message_log if m.namespace == "_bus/heartbeat"]
        assert len(hb_msgs) >= 1

    def test_discovery_on_module_register(self, bus):
        m = Module("test-mod", "button-matrix")
        bus.register_module(m)
        disc_msgs = [m for m in bus.message_log if m.namespace == "_bus/discovery"]
        assert len(disc_msgs) >= 1


class TestMessageFormat:
    """DP-CIO-18: Messages <256 bytes."""

    def test_json_serialization(self):
        msg = BusMessage(
            namespace="ftl_arm",
            type=MessageType.STATE,
            value="on",
            source="panel-1",
            scope="auto",
        )
        j = msg.to_json()
        assert j["ns"] == "ftl_arm"
        assert j["val"] == "on"
        assert j["type"] == "state"

    def test_json_round_trip(self):
        msg = BusMessage(
            namespace="sublight_engines",
            type=MessageType.STATE,
            value="half",
            source="panel-1",
            scope="global",
            sequence=42,
        )
        j = msg.to_json()
        msg2 = BusMessage.from_json(j)
        assert msg2.namespace == "sublight_engines"
        assert msg2.value == "half"
        assert msg2.sequence == 42

    def test_message_size_under_256(self):
        msg = BusMessage(
            namespace="a_reasonably_long_namespace_name",
            type=MessageType.STATE,
            value="some_state_value",
            source="module_with_a_long_name",
            scope="global",
        )
        assert msg.size_estimate() < 256


class TestStatus:
    def test_status_overview(self, bus):
        bus.register_namespace("ns1", "binary")
        bus.register_namespace("ns2", "tri_level")
        s = bus.status()
        assert "ns1" in s["namespaces"]
        assert "ns2" in s["namespaces"]
        assert s["transports"]["local"] is True
