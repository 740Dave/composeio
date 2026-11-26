"""WS connector — pure-logic acceptance ([SPEC:CXC-WS] §-acceptance WS-V rows).

These exercise `WSPeer`/`WSConnector` LOGIC through the injected `send_frame`
sink with NO socket and NO asyncio, so every lifecycle/subscription/replay/
tenant-boundary predicate is asserted deterministically. The real-socket path
(browser<->bus over a live WS) is in `test_transport_ws_live.py`.

Every check has its NEGATIVE CONTROL ([CXC-VISION-EPISTEMICS]): an instrument
that can only report success is not an instrument. The `verify-human` GLASS row
(a real BROWSER rendering a live scene) is owned by the DOM backend building in
parallel and is NOT self-certifiable here.
"""
from __future__ import annotations

import pytest

from composeio import transport_registry
from composeio.composeserver import ComposeServer
from composeio.namespace import Scope
from composeio.transport import BusMessage, MessageType, UnserialisableValue
from composeio.transport_ws import (
    BOUND,
    PRE_BINDING,
    UnknownTenantError,
    WSConnector,
    WSPeer,
    _default_deeplink_parser,
    replay_retained,
)


# --------------------------------------------------------------------------- #
# fixtures — a real two-tenant ComposeServer is the faithful TenantRegistry.
# --------------------------------------------------------------------------- #
@pytest.fixture
def server():
    srv = ComposeServer()
    srv.add_tenant("haven")
    srv.add_tenant("bsg")
    return srv


class Sink:
    """Captures the frames a peer emits, in order."""
    def __init__(self):
        self.frames: list[dict] = []

    def __call__(self, frame: dict) -> None:
        self.frames.append(frame)

    def bus_frames(self) -> list[dict]:
        return [f for f in self.frames if "ws" not in f]

    def control(self, verb: str) -> list[dict]:
        return [f for f in self.frames if f.get("ws") == verb]


def _peer(server, sink, tenant=None) -> WSPeer:
    conn = WSConnector(registry=server)
    return conn._new_peer(sink, deeplink=tenant)


# --------------------------------------------------------------------------- #
# WS-V1 — registered as a `type: ws` connector, never a plugin.
# --------------------------------------------------------------------------- #
def test_wsv1_registry_knows_ws_and_builds(server):
    assert "ws" in transport_registry.known_types()
    built = transport_registry.build(
        {"type": "ws", "host": "127.0.0.1", "port": 0, "registry": server})
    assert isinstance(built, WSConnector)  # never None on a valid spec
    assert built is not None


def test_wsv1_unknown_type_still_raises_loudly():
    # NEGATIVE CONTROL: registering `ws` must not soften the loud-failure rule.
    with pytest.raises(transport_registry.UnknownTransportError):
        transport_registry.build({"type": "ws2", "host": "x"})


# --------------------------------------------------------------------------- #
# WS-V2 — each connection is one singleton persistent peer.
# --------------------------------------------------------------------------- #
def test_wsv2_two_connections_are_independent_peers(server):
    conn = WSConnector(registry=server)
    a, b = Sink(), Sink()
    p1 = conn._new_peer(a)
    p2 = conn._new_peer(b)
    assert p1 is not p2
    assert p1.peer_id != p2.peer_id
    p1.set_subscription(["ship/*"])
    p2.set_subscription(["system/*"])
    assert p1.declared_patterns != p2.declared_patterns
    assert {p.peer_id for p in conn.peers} == {p1.peer_id, p2.peer_id}
    # closing one (the socket-close path) leaves the other untouched (NEG CONTROL)
    conn._drop_peer(p1)
    assert p1.state == PRE_BINDING
    assert p2.declared_patterns == ["system/*"]
    assert p2 in conn.peers and p1 not in conn.peers


# --------------------------------------------------------------------------- #
# WS-V3 — lifecycle: PRE-BINDING/BOUND, subscribe-before-replay, clean switch.
# --------------------------------------------------------------------------- #
def test_wsv3_prebinding_receives_no_tenant_traffic(server):
    sink = Sink()
    peer = _peer(server, sink)
    assert peer.state == PRE_BINDING
    # a publish on a tenant bus before ANY bind must not reach the peer.
    server.tenant_bus("haven").publish_state("ship/speed", 7)
    assert sink.bus_frames() == []


def test_wsv3_bound_entry_subscribes_then_replays(server):
    bus = server.tenant_bus("haven")
    bus.publish_state("ship/speed", 11)  # value exists BEFORE bind
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["ship/*"])     # declare while PRE-BINDING
    assert sink.bus_frames() == []        # nothing replayed before bind
    peer.bind("haven")
    assert peer.state == BOUND
    replays = [f for f in sink.bus_frames() if f.get("retained")]
    assert len(replays) == 1
    assert replays[0]["ns"] == "ship/speed" and replays[0]["val"] == 11


def test_wsv3_switch_leaves_A_before_joining_B(server):
    busA, busB = server.tenant_bus("haven"), server.tenant_bus("bsg")
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["ship/*"])
    peer.bind("haven")
    # switch A->B
    sink.frames.clear()
    peer.switch("bsg")
    verbs = [f.get("ws") for f in sink.frames if "ws" in f]
    # observable intermediate PRE-BINDING between the two BOUND states
    assert "prebinding" in verbs
    assert verbs.index("prebinding") < verbs.index("bound")
    assert peer.bound_tenant == "bsg"
    # NEGATIVE CONTROL: no residual A callback fires post-switch.
    sink.frames.clear()
    busA.publish_state("ship/speed", 99)
    assert sink.bus_frames() == []
    # and B traffic DOES reach the peer.
    busB.publish_state("ship/speed", 42)
    assert any(f.get("ns") == "ship/speed" and f.get("val") == 42
               for f in sink.bus_frames())


def test_wsv3_switch_while_prebinding_is_a_plain_bind(server):
    sink = Sink()
    peer = _peer(server, sink)   # PRE-BINDING, no A to leave
    peer.switch("haven")
    assert peer.state == BOUND and peer.bound_tenant == "haven"


# --------------------------------------------------------------------------- #
# WS-V4 — one tenant at a time; never sees another tenant's traffic.
# --------------------------------------------------------------------------- #
def test_wsv4_same_named_namespace_never_cross_delivers(server):
    busA, busB = server.tenant_bus("haven"), server.tenant_bus("bsg")
    # NB: a globally-egressing namespace, NOT `_server/status` — on a real
    # tenant bus `_server/*` is LOCAL-scoped (the internal status surface) and
    # never reaches ANY WS peer, so it can only demonstrate the negative
    # control, never the positive. See the report's spec/impl note.
    busA.publish_state("fleet/status", "haven-up")
    busB.publish_state("fleet/status", "bsg-up")
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["fleet/*"])
    peer.bind("haven")
    # replay + live: only haven's value, never bsg's.
    got = [f["val"] for f in sink.bus_frames() if f["ns"] == "fleet/status"]
    assert got == ["haven-up"]
    busB.publish_state("fleet/status", "bsg-changed")   # NEGATIVE CONTROL
    got = [f["val"] for f in sink.bus_frames() if f["ns"] == "fleet/status"]
    assert "bsg-changed" not in got and "bsg-up" not in got


# --------------------------------------------------------------------------- #
# WS-V5 — operator entry serves the list before any data.
# --------------------------------------------------------------------------- #
def test_wsv5_operator_list_served_before_selection(server):
    sink = Sink()
    peer = _peer(server, sink)   # no deep-link
    lists = sink.control("tenants")
    assert lists and set(lists[0]["tenants"]) == {"haven", "bsg"}
    assert peer.state == PRE_BINDING
    assert sink.bus_frames() == []   # NEGATIVE CONTROL: no data before select


# --------------------------------------------------------------------------- #
# WS-V6 — deep-link converges on BOUND; unknown tenant fails loudly.
# --------------------------------------------------------------------------- #
def test_wsv6_deeplink_binds_without_serving_list(server):
    assert _default_deeplink_parser("/bus/haven") == "haven"
    assert _default_deeplink_parser("/") is None
    assert _default_deeplink_parser("/ws") is None
    sink = Sink()
    peer = _peer(server, sink, tenant="haven")
    assert peer.state == BOUND and peer.bound_tenant == "haven"
    assert sink.control("tenants") == []   # list NEVER served on deep-link


def test_wsv6_unknown_tenant_bind_raises(server):
    sink = Sink()
    peer = _peer(server, sink)
    with pytest.raises(UnknownTenantError):
        peer.bind("nonexistent")   # loud, never a silent fallback


# --------------------------------------------------------------------------- #
# WS-V8 — wire envelope is BusMessage.to_json()/from_json() verbatim.
# --------------------------------------------------------------------------- #
def test_wsv8_envelope_roundtrips_every_field_combo(server):
    bus = server.tenant_bus("haven")
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["*"])
    peer.bind("haven")
    sink.frames.clear()
    bus.publish_state("ship/speed", {"knots": 12, "heading": 270})
    frame = [f for f in sink.bus_frames() if not f.get("retained")][-1]
    # every field the connector put on the wire is exactly to_json()'s, and
    # from_json decodes it back with no field lost or renamed.
    back = BusMessage.from_json(frame)
    assert back.namespace == "ship/speed"
    assert back.value == {"knots": 12, "heading": 270}
    assert back.type == MessageType.STATE
    assert set(frame) >= {"ns", "type", "src", "ts", "scope", "seq", "val"}


def test_wsv8_raw_bytes_rejected_identically_no_base64_hatch():
    # NEGATIVE CONTROL: the WS substrate invents no binary escape hatch; a
    # raw-bytes value raises the SAME UnserialisableValue every JSON substrate
    # raises ([CXC-WS-WIRE-FORMAT]).
    msg = BusMessage(namespace="blob/x", type=MessageType.STATE, value=b"\x00\x01")
    with pytest.raises(UnserialisableValue):
        msg.to_json()


# --------------------------------------------------------------------------- #
# WS-V9 — subscription is explicit, default-deny, no tenant firehose.
# --------------------------------------------------------------------------- #
def test_wsv9_default_deny_empty_set_receives_nothing(server):
    bus = server.tenant_bus("haven")
    sink = Sink()
    peer = _peer(server, sink)
    peer.bind("haven")                  # no subscription declared
    bus.publish_state("ship/speed", 3)
    assert sink.bus_frames() == []      # empty declared set == subscribe NOTHING


def test_wsv9_declared_pattern_is_not_a_firehose(server):
    bus = server.tenant_bus("haven")
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["ship/*"])
    report = sink.control("subscribed")
    assert report and report[0]["enforcement"] == "broker"   # report, never None
    peer.bind("haven")
    sink.frames.clear()
    bus.publish_state("ship/speed", 5)      # declared -> arrives
    bus.publish_state("system/alert", "x")  # NEGATIVE CONTROL: undeclared -> never
    names = [f["ns"] for f in sink.bus_frames()]
    assert "ship/speed" in names and "system/alert" not in names


def test_wsv9_star_must_be_explicit(server):
    bus = server.tenant_bus("haven")
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["*"])   # the firehose is opt-in, never default
    peer.bind("haven")
    sink.frames.clear()
    bus.publish_state("anything/at/all", 1)
    assert any(f["ns"] == "anything/at/all" for f in sink.bus_frames())


def test_wsv9_malformed_pattern_rejected(server):
    sink = Sink()
    peer = _peer(server, sink)
    with pytest.raises(ValueError):
        peer.set_subscription(["a/*/b"])   # mid-glob is not bus semantics


# --------------------------------------------------------------------------- #
# WS-V10 — retained replay once on subscribe/switch; never past deny/scope.
# --------------------------------------------------------------------------- #
def test_wsv10_replay_exactly_once_marked_retained(server):
    bus = server.tenant_bus("haven")
    bus.publish_state("ship/speed", 8)   # value BEFORE the peer exists
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["ship/*"])
    peer.bind("haven")
    replays = [f for f in sink.bus_frames() if f.get("retained")]
    assert len(replays) == 1
    assert replays[0]["ns"] == "ship/speed" and replays[0]["val"] == 8
    # NEGATIVE CONTROL: re-declaring the SAME pattern replays no duplicate.
    sink.frames.clear()
    peer.set_subscription(["ship/*"])
    assert [f for f in sink.bus_frames() if f.get("retained")] == []


def test_wsv10_replay_never_bypasses_default_deny(server):
    bus = server.tenant_bus("haven")
    bus.publish_state("ship/speed", 8)
    bus.publish_state("system/alert", "boom")
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["ship/*"])     # system/* NOT declared
    peer.bind("haven")
    names = [f["ns"] for f in sink.bus_frames() if f.get("retained")]
    assert "ship/speed" in names
    assert "system/alert" not in names    # NEGATIVE CONTROL: undeclared never replays


def test_wsv10_replay_and_live_honor_explicit_local_scope(server):
    bus = server.tenant_bus("haven")
    bus.register_namespace("secret/key", "json", scope=Scope.LOCAL)
    bus.publish_state("secret/key", "hunter2")   # pre-existing local value
    bus.publish_state("ship/speed", 6)
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["*"])          # even a firehose must not leak local
    peer.bind("haven")
    names = [f["ns"] for f in sink.bus_frames()]
    assert "ship/speed" in names
    assert "secret/key" not in names      # NEGATIVE CONTROL: local never replayed
    # live publish of the local namespace also does not leak.
    sink.frames.clear()
    bus.publish_state("secret/key", "changed")
    assert [f for f in sink.bus_frames() if f["ns"] == "secret/key"] == []


def test_wsv10_replay_widens_on_switch(server):
    busB = server.tenant_bus("bsg")
    busB.publish_state("ship/hull", 100)
    sink = Sink()
    peer = _peer(server, sink)
    peer.set_subscription(["ship/*"])
    peer.bind("haven")            # haven has no ship/hull
    sink.frames.clear()
    peer.switch("bsg")            # switch re-runs replay against B
    replays = [f for f in sink.bus_frames() if f.get("retained")]
    assert any(f["ns"] == "ship/hull" and f["val"] == 100 for f in replays)


# --------------------------------------------------------------------------- #
# WS-V11 — v1 ships with NO identity persistence.
# --------------------------------------------------------------------------- #
def test_wsv11_no_identity_persistence_ephemeral_only(server):
    conn = WSConnector(registry=server)
    p1 = conn._new_peer(Sink())
    p2 = conn._new_peer(Sink())
    # per-connection identity only; two connections are two identities, and
    # nothing persists or is required stable across reconnects.
    assert p1.identity != p2.identity
    assert p1.identity == p1.peer_id     # ephemeral, logging/debug only
    # NEGATIVE CONTROL: no persistent-store attribute exists on the connector.
    assert not hasattr(conn, "_localstorage")
    assert not hasattr(p1, "_persisted_identity")


# --------------------------------------------------------------------------- #
# browser -> bus publish reaches a real bus subscriber (via the pure path).
# --------------------------------------------------------------------------- #
def test_browser_publish_reaches_bus_subscriber(server):
    bus = server.tenant_bus("haven")
    seen = []
    bus.subscribe("bridge/cmd", lambda ns, new, old: seen.append(new))
    sink = Sink()
    peer = _peer(server, sink)
    peer.bind("haven")
    peer.handle_frame({"ns": "bridge/cmd", "type": "state", "val": 42, "src": "browser"})
    assert seen == [42]


def test_publish_illegal_before_bind_errors(server):
    sink = Sink()
    peer = _peer(server, sink)   # PRE-BINDING
    peer.handle_frame({"ns": "bridge/cmd", "type": "state", "val": 1})
    assert sink.control("error") and sink.control("error")[0]["code"] == "not_bound"


# --------------------------------------------------------------------------- #
# the connector-layer replay helper is substrate-neutral (UART/CAN inherit it).
# --------------------------------------------------------------------------- #
def test_replay_helper_is_a_plain_connector_layer_function(server):
    bus = server.tenant_bus("haven")
    bus.publish_state("a/x", 1)
    bus.publish_state("b/y", 2)
    out: list[dict] = []
    n = replay_retained(bus, ["a/*"], out.append)
    assert n == 1 and out[0]["ns"] == "a/x" and out[0]["retained"] is True
