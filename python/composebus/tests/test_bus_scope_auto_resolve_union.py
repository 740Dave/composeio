"""[BP-NS-AUTO-RESOLVE] / [BP-C3]: auto-resolve is a UNION, not a first match.

spec/01 §3.2 states the rule and then states it again as a worked example:

    "Auto-resolve delivers to ALL applicable scopes, not just the first match.
     A message to `office_lights` with a local subscriber AND a global
     transport is delivered both locally AND globally."

`_resolve_scope` returned on the FIRST match: any local subscriber made it
return the literal "local", and `_send_message` then tested
`scope in ("global", "auto")` before routing to the global transport — false.
So a namespace with BOTH a local subscriber AND a connected global transport
was delivered locally ONLY.

The failure shape is why these tests assert on the TRANSPORT and not on the
return value of `_resolve_scope`: nothing raised, nothing counted a drop, and
global delivery disappeared as an invisible side effect of something entirely
unrelated — a local subscriber appearing. A test that asserts the resolver's
string would have to agree with the resolver about what the string MEANS, and
that agreement is exactly what was broken.
"""

from __future__ import annotations

from composebus.bus import ComposeIOBus
from composebus.namespace import Scope
from composebus.transport import Transport, BusMessage, MessageType


class RecordingTransport(Transport):
    """A global transport that records what the bus handed it."""

    def __init__(self, connected: bool = True):
        self.sent: list[BusMessage] = []
        self._connected = connected

    def start(self):
        self._connected = True

    def stop(self):
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def send(self, message: BusMessage):
        self.sent.append(message)

    def subscribe(self, namespace: str, callback):
        pass

    def subscribe_all(self, callback):
        pass

    def unsubscribe(self, namespace: str, callback):
        pass

    @property
    def namespaces_sent(self) -> list[str]:
        return [m.namespace for m in self.sent]


def _bus_with_global() -> tuple[ComposeIOBus, RecordingTransport]:
    glob = RecordingTransport()
    bus = ComposeIOBus(bus_id="test-bus", global_transport=glob)
    return bus, glob


# --------------------------------------------------------------------------- #
# THE REGRESSION — the spec's own worked example, verbatim.
# --------------------------------------------------------------------------- #

def test_auto_namespace_with_local_subscriber_still_reaches_global():
    """spec/01 §3.2's worked example. THIS IS THE BUG: before the fix the
    local subscriber suppressed global delivery entirely."""
    bus, glob = _bus_with_global()
    bus.register_namespace("office_lights", "binary", scope=Scope.AUTO)

    seen: list = []
    bus.subscribe("office_lights", lambda ns, new, old: seen.append(new))

    bus.publish_state("office_lights", "on", source="panel-1")

    # delivered LOCALLY ...
    assert seen == ["on"], "local subscriber did not receive the state"
    # ... AND GLOBALLY. This is the assertion that failed before the fix.
    assert glob.namespaces_sent == ["office_lights"], (
        "auto-resolve dropped global delivery because a local subscriber "
        "existed — first-match instead of union ([BP-C3])")


def test_auto_namespace_without_local_subscriber_reaches_global():
    """The path that always worked — kept so the fix cannot regress it."""
    bus, glob = _bus_with_global()
    bus.register_namespace("ftl_arm", "binary", scope=Scope.AUTO)

    bus.publish_state("ftl_arm", "on", source="panel-1")

    assert glob.namespaces_sent == ["ftl_arm"]


def test_local_subscriber_count_does_not_change_global_reachability():
    """The heart of [BP-C3]: global reachability is a property of the
    TRANSPORT topology, never of how many local listeners happen to exist.
    Subscribers appearing must not silently unplug the other machine."""
    bus, glob = _bus_with_global()
    bus.register_namespace("thruster", "continuous_0_255", scope=Scope.AUTO)

    bus.publish_state("thruster", 10, source="t")
    assert len(glob.sent) == 1

    bus.subscribe("thruster", lambda ns, new, old: None)
    bus.publish_state("thruster", 20, source="t")
    assert len(glob.sent) == 2, "a local subscriber suppressed global delivery"

    bus.subscribe("thruster", lambda ns, new, old: None)
    bus.subscribe("thruster", lambda ns, new, old: None)
    bus.publish_state("thruster", 30, source="t")
    assert len(glob.sent) == 3, "more local subscribers suppressed global"


# --------------------------------------------------------------------------- #
# WHAT MUST NOT CHANGE — an EXPLICIT declaration still wins.
# --------------------------------------------------------------------------- #

def test_explicit_local_scope_is_still_never_globalised():
    """`Scope.LOCAL` is a DELIBERATE declaration of confinement. The union
    applies to AUTO only; widening an explicit local would be a privacy
    regression dressed as a bug fix."""
    bus, glob = _bus_with_global()
    bus.register_namespace("debug_chatter", "json", scope=Scope.LOCAL)

    seen: list = []
    bus.subscribe("debug_chatter", lambda ns, new, old: seen.append(new))
    bus.publish_state("debug_chatter", {"noisy": True}, source="dbg")

    assert seen == [{"noisy": True}]
    assert glob.sent == [], "an EXPLICIT local scope leaked to the global bus"


def test_explicit_global_scope_still_delivered_both_ways():
    bus, glob = _bus_with_global()
    bus.register_namespace("ftl_arm", "binary", scope=Scope.GLOBAL)

    seen: list = []
    bus.subscribe("ftl_arm", lambda ns, new, old: seen.append(new))
    bus.publish_state("ftl_arm", "on", source="panel-1")

    assert seen == ["on"]
    assert glob.namespaces_sent == ["ftl_arm"]


def test_remote_is_never_auto_resolved():
    """spec/01 §3.2 step 3: remote requires explicit declaration, so that
    auto-resolution can never produce accidental cross-network traffic."""
    glob = RecordingTransport()
    remote = RecordingTransport()
    bus = ComposeIOBus(bus_id="t", global_transport=glob,
                       remote_transport=remote)
    bus.register_namespace("office_lights", "binary", scope=Scope.AUTO)
    bus.subscribe("office_lights", lambda ns, new, old: None)

    bus.publish_state("office_lights", "on", source="panel-1")

    assert glob.namespaces_sent == ["office_lights"]
    assert remote.sent == [], "auto-resolve produced REMOTE traffic"


def test_no_global_transport_resolves_local():
    """With no global transport there is no applicable global scope, so the
    resolved scope must say `local` — the envelope should not claim a
    delivery that no transport could make."""
    bus = ComposeIOBus(bus_id="t")           # no global transport at all
    ns = bus.register_namespace("office_lights", "binary", scope=Scope.AUTO)

    assert bus._resolve_scope(ns) == "local"


# --------------------------------------------------------------------------- #
# THE ENVELOPE / TOPIC CONSEQUENCE.
# --------------------------------------------------------------------------- #

def test_globalised_auto_message_carries_scope_global_on_the_wire():
    """spec/01 §6.2.1: 'The scope in the topic is the resolved scope (never
    `auto`)'. MQTT's `_topic_for` builds `composeio/{scope}/{ns}`, so a
    message that crosses to the global transport must carry `global` — if it
    carried `local` it would land on `composeio/local/...` where no global
    peer is listening, which is the same invisible non-delivery one layer
    down."""
    bus, glob = _bus_with_global()
    bus.register_namespace("office_lights", "binary", scope=Scope.AUTO)
    bus.subscribe("office_lights", lambda ns, new, old: None)

    bus.publish_state("office_lights", "on", source="panel-1")

    assert glob.sent[0].scope == "global"


def test_events_also_union_resolve():
    """`publish_event` resolves scope through the same function, so the
    defect and its fix both apply to events."""
    bus, glob = _bus_with_global()
    bus.register_namespace("ftl_arm", "binary", scope=Scope.AUTO)
    bus.subscribe_events("ftl_arm", lambda ns, et, data: None)
    bus.ensure_namespace("ftl_arm").subscribe(lambda *a: None)

    bus.publish_event("ftl_arm", "pressed", source="panel-1")

    assert [m.type for m in glob.sent] == [MessageType.EVENT]
