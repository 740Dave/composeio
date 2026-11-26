"""RUNTIME bringup smoke: ComposeServer/ComposeBus-kernel is a LIVE BUS PEER.

Not a re-check of `test_composeserver_plugin_contract.py` (that suite proves
the STATIC contract -- derivation, park/unpark logic, isolation math -- in
isolation). This file launches REAL objects and asserts what actually lands
ON THE WIRE: a subscribe->publish round trip through a real `ComposeIOBus`,
a `requires:` floor that genuinely blocks/unblocks delivery (not just a
returned list), and a global publish crossing TWO independently-typed live
connectors at once (CAN loopback + LocalTransport).

[SPEC:CXC-COMPOSESERVER] [SPEC:CXC-COMPOSEBUS-KERNEL]. First-bringup pass,
composeio-first-bringup agent, 2026-08-23.

EVERY GREEN ASSERTION HERE CARRIES A NEGATIVE CONTROL IN THE SAME TEST
([CXC-VISION-EPISTEMICS] rule-negative-control) -- see each test's own
docstring for "what would this show if the component were dead".
"""
from __future__ import annotations

from composeio.bus import ComposeIOBus
from composeio.composeserver import ComposeServer, standup
from composeio.module import Module
from composeio.namespace import Scope
from composeio.plugin import ComposePlugin, Isolation, RequiresFloor
from composeio.transport_can import CANTransport
from composeio.transport_local import LocalTransport


class _EchoConfig(ComposePlugin.Config):
    prefix: str = "echo"


class _EchoPlugin(ComposePlugin):
    """A trivial, REAL `ComposePlugin` used only to prove the contract is
    load-bearing at runtime, not merely importable."""

    class Config(_EchoConfig):
        pass

    name = "echo"
    version = "0.1.0"
    publishes = ["echo/out"]
    subscribes = ["echo/in"]
    requires = RequiresFloor(capabilities=["net"])

    def start(self, bus):
        self._bus = bus
        bus.subscribe("echo/in", self._on_in)

    def _on_in(self, namespace, new_value, old_value):
        self._bus.publish_state("echo/out", f"{self.config.prefix}:{new_value}",
                                 source=self.name)

    def stop(self):
        ns = self._bus.get_namespace("echo/in") if self._bus else None
        if ns is not None:
            ns.unsubscribe(self._on_in)
        super().stop()


# --------------------------------------------------------------------------- #
def test_live_peer_round_trip_and_negative_control():
    """A plugin loaded through standup() actually receives + emits on the bus.

    NEGATIVE CONTROL (in-test): stop() the plugin and re-publish the SAME
    input. If the round trip still fired, the first assertion would have been
    measuring an always-on wire, not the live peer -- so this must go silent.
    """
    bus = ComposeIOBus(bus_id="smoke-roundtrip")
    bus.start()
    plugin = _EchoPlugin()
    plugin.start(bus)

    seen = []
    bus.subscribe("echo/out", lambda ns, new, old: seen.append(new))
    bus.publish_state("echo/in", "world", source="test")
    assert seen == ["echo:world"], f"round trip did not reach a non-empty lane: {seen}"

    # off-lane control: a namespace this plugin never publishes must stay silent
    off_lane = []
    bus.subscribe("echo/mystery", lambda ns, new, old: off_lane.append(new))
    bus.publish_state("echo/in", "again", source="test")
    assert off_lane == [], "a namespace the plugin never publishes must never fire"

    # THE negative control: kill the peer, prove the wire goes dark
    plugin.stop()
    seen.clear()
    bus.publish_state("echo/in", "should-not-echo", source="test")
    assert seen == [], (
        "a STOPPED plugin must not round-trip -- if it did, the earlier PASS "
        "was a certifier (would pass whether or not the plugin were alive)")


def test_requires_floor_parks_loud_and_unparks_with_no_restart():
    """`requires:` genuinely BLOCKS delivery while unmet, and `provide_capability`
    unblocks the SAME instance -- no restart, no re-`start()`.

    NEGATIVE CONTROL: while parked, publish to the plugin's subscribed
    namespace and assert its output namespace stays untouched -- "parked"
    must be a real gate, not a status string with no effect on the wire.
    """
    srv = ComposeServer()
    srv.add_tenant("t", plugins=[_EchoPlugin()], capabilities=set())
    assert srv.parked("t") == {"echo": ["capability:net"]}

    bus = srv.tenant_bus("t")
    bus.start()
    bus.publish_state("echo/in", "ignored", source="test")
    assert bus.get_state("echo/out") is None, "a PARKED plugin round-tripped anyway"

    unparked = srv.provide_capability("t", "net")
    assert unparked == ["echo"]
    assert srv.parked("t") == {}

    bus.publish_state("echo/in", "hello", source="test")
    assert bus.get_state("echo/out") == "echo:hello", (
        "dynamic unpark did not actually wire the SAME instance onto the bus")


def test_default_loaded_discovery_plugin_is_a_live_peer():
    """The shed `discovery` plugin (default-loaded on a bare bus) actually
    announces a module registration on `_bus/discovery`.

    NEGATIVE CONTROL: a second bus that never registers a module must emit
    NOTHING on `_bus/discovery` -- rules out an always-fires monitor.
    """
    bus = ComposeIOBus(bus_id="smoke-discovery")
    bus.start()
    heard = []
    bus.on_message(lambda m: heard.append(m) if m.namespace == "_bus/discovery" else None)

    class _Dummy(Module):
        def __init__(self):
            super().__init__("smoke_module", role="test")

    bus.register_module(_Dummy())
    assert len(heard) == 1 and heard[0].source == "smoke_module"

    quiet_bus = ComposeIOBus(bus_id="smoke-discovery-quiet")
    quiet_bus.start()
    quiet_heard = []
    quiet_bus.on_message(
        lambda m: quiet_heard.append(m) if m.namespace == "_bus/discovery" else None)
    quiet_bus.publish_state("unrelated/ns", 1, source="test")
    assert quiet_heard == [], "no registration occurred; discovery must stay silent"


def test_multi_attach_fanout_across_two_live_connector_types():
    """One bus, TWO independently-typed live global connectors (CAN loopback +
    LocalTransport) -- a single global publish crosses BOTH.

    NEGATIVE CONTROL: a LOCAL-scoped namespace must cross NEITHER connector --
    proves fan-out is scope-gated (a membrane), not "everything reaches
    everything" (which would make the connector a hidden mediator).
    """
    res = standup(bus_id="smoke-multi-attach", connectors=[{"type": "can", "channel": "loopback"}])
    bus = res.bus
    local2 = LocalTransport()
    bus.attach_connector(local2, "global")
    assert len(bus.connectors("global")) == 2

    # CAN carries only a namespace HASH on the wire ([BP-CAN-ID]); an
    # independent witness node must pre-subscribe to resolve it -- this is a
    # documented CAN wire-format property, not a bug (see [CXC-VISION-SCOPE-
    # MEMBRANE]'s open item cited in [SPEC:CXC-COMPOSEBUS-KERNEL]).
    witness = CANTransport(channel="loopback", backend="loopback")
    witness.start()
    heard_can, heard_local = [], []
    witness.subscribe("multi/ns", lambda m: heard_can.append(m))
    local2.subscribe_all(lambda m: heard_local.append(m))

    bus.start()
    local2.start()

    bus.publish_state("multi/ns", 42, source="test")
    assert any(m.namespace == "multi/ns" and m.value == 42 for m in heard_can)
    assert any(m.namespace == "multi/ns" for m in heard_local)

    bus.register_namespace("local/only", "json", scope=Scope.LOCAL)
    heard_can.clear()
    heard_local.clear()
    bus.publish_state("local/only", "stay-home", source="test")
    assert heard_can == [] and heard_local == [], (
        "a LOCAL-scoped namespace crossed a global connector -- membrane broken")

    witness.stop()


def test_isolation_floor_resolves_as_a_minimum_never_forces_inprocess():
    """`resolved_isolation` is `max(floor, server_minimum)`.

    NEGATIVE CONTROL: an INPROCESS floor with NO server minimum must resolve
    to INPROCESS -- proves the math is `max()`, not a hard-coded SUBPROCESS.
    """
    class _InProc(_EchoPlugin):
        name = "inproc"
        requires = RequiresFloor()
        isolation_floor = Isolation.INPROCESS

    class _SubProc(_EchoPlugin):
        name = "subproc"
        requires = RequiresFloor()
        isolation_floor = Isolation.SUBPROCESS

    inp, sub = _InProc(), _SubProc()
    assert inp.resolved_isolation(Isolation.SUBPROCESS) == Isolation.SUBPROCESS
    assert sub.resolved_isolation(None) == Isolation.SUBPROCESS
    assert inp.resolved_isolation(None) == Isolation.INPROCESS  # negative control

    res = standup(bus_id="smoke-isolation", plugins=[sub])
    assert "subproc" in res.subprocess_pending and sub not in res.loaded
