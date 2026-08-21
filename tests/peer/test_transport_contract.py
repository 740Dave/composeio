"""[BPR-TRANSPORT-CONTRACT] — the ONE unified Transport-emitter contract.

Proves the three W1 acceptance claims:

  (a) BOTH poles satisfy the ``TransportContract`` interface — the thin egress
      adaptor (``HttpRelayTransport``) and the fat bridge peer
      (``PeerLink`` / ``PeerHub`` / ``CanBridge``) are the SAME contract with
      different knob settings.
  (b) On the BRIDGE pole a ``pri: critical`` message resolves as a GENUINE
      end-to-end ack OR an explicit ``_bus/fault`` — never a synthesized ``ok``;
      the silent-clear count stays 0 ([BPR-CRITICAL-ACK]).
  (c) The EGRESS pole is fire-and-forget and NEVER invokes the handshake / ack
      machinery — the thin adaptor is honestly thin.
"""

import threading
import time

import pytest

from composeio.transport import BusMessage, MessageType
from composeio.transport_contract import (
    Directionality,
    FarSide,
    QoS,
    TransportContract,
    TransportProfile,
)
from composeio.transport_http_relay import HttpRelayTransport
from composeio.peer import CanBridge, CriticalAckOrigin, CriticalAcker, PeerHub, PeerLink
import composeio.transport_http_relay as http_relay_mod
import composeio.peer.critical as critical_mod


def _wait(pred, timeout=3.0, interval=0.005):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


# --- (a) ONE contract spans the thin-to-fat spectrum ----------------------

def test_egress_pole_satisfies_contract_and_declares_thin_profile():
    relay = HttpRelayTransport(endpoint="http://localhost:8422/messages")
    assert isinstance(relay, TransportContract)
    assert relay.profile.qos is QoS.FIRE_AND_FORGET
    assert relay.profile.is_egress
    assert relay.profile.far_side is FarSide.DUMB_SINK
    assert relay.profile.preserves_critical_ack is False


def test_bridge_pole_full_peer_satisfies_contract_and_declares_fat_profile():
    link = PeerLink(host="127.0.0.1", port=1, subscriptions=["x/*"])
    hub = PeerHub()
    for peer in (link, hub):
        assert isinstance(peer, TransportContract)
        assert peer.profile.qos is QoS.CRITICAL_ACK
        assert peer.profile.is_bridge
        assert peer.profile.far_side is FarSide.FULL_PEER
        assert peer.profile.preserves_critical_ack is True


def test_bridge_pole_mapped_device_bus_declares_can_profile():
    from composeio.peer import CanIdRegistry, MockCanBus

    registry = CanIdRegistry()
    registry.register_prefix("jumpdrive", block_bits=4)
    registry.register("jumpdrive/arm")
    bridge = CanBridge(registry=registry, can_bus=MockCanBus(),
                       subscriptions=["jumpdrive/*"])
    assert isinstance(bridge, TransportContract)
    assert bridge.profile.qos is QoS.CRITICAL_ACK
    assert bridge.profile.is_bridge
    assert bridge.profile.far_side is FarSide.MAPPED_DEVICE_BUS


def test_the_two_poles_differ_only_in_knob_settings():
    """The unification is the INTERFACE; the poles are the SAME contract read at
    opposite ends of the spectrum."""
    egress = HttpRelayTransport().profile
    bridge = PeerHub().profile
    assert isinstance(egress, TransportProfile)
    assert isinstance(bridge, TransportProfile)
    # Same three-knob shape, opposite settings.
    assert (egress.qos, egress.directionality) == (
        QoS.FIRE_AND_FORGET, Directionality.EGRESS)
    assert (bridge.qos, bridge.directionality) == (
        QoS.CRITICAL_ACK, Directionality.BIDIRECTIONAL)


# --- the honest-thinness invariant (profile self-validates) ---------------

def test_critical_ack_egress_is_a_construction_error():
    """A one-way egress cannot honor critical-ack — declaring it is a hard error,
    not a latent runtime fault ([BPR-TRANSPORT-CONTRACT] knob coupling)."""
    with pytest.raises(ValueError):
        TransportProfile(qos=QoS.CRITICAL_ACK,
                         directionality=Directionality.EGRESS,
                         far_side=FarSide.FULL_PEER)


def test_critical_ack_dumb_sink_is_a_construction_error():
    with pytest.raises(ValueError):
        TransportProfile(qos=QoS.CRITICAL_ACK,
                         directionality=Directionality.BIDIRECTIONAL,
                         far_side=FarSide.DUMB_SINK)


# --- (b) BRIDGE pole: propagate-or-explicit-degrade, silent-clear = 0 ------

def _setup_bridge_critical(seg, backoff_ms):
    seg.master.register_namespace("jumpdrive/arm", "json")
    seg.edge.register_namespace("jumpdrive/arm", "json")
    assert seg.link.wait_ready(3.0)
    # The peer legs carry the critical-ack knob.
    assert seg.link.profile.preserves_critical_ack
    assert seg.hub.profile.preserves_critical_ack
    acker = CriticalAcker(seg.master, "jumpdrive/arm", "relay-master",
                          handler=lambda ns, val: "ok")
    origin = CriticalAckOrigin(seg.edge, backoff_ms=backoff_ms)
    return origin, acker


def test_bridge_pole_critical_resolves_as_genuine_ack(segment_factory):
    seg = segment_factory(["jumpdrive/*"])
    origin, acker = _setup_bridge_critical(seg, backoff_ms=(40, 80, 200))
    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["relay-master"],
                                  source="edge-panel")
    assert _wait(lambda: origin.resolution(cid) == "acked")
    assert acker.processed >= 1          # a REAL acker did the work
    assert origin.faults == []
    assert origin.silent_clear_count == 0
    origin.close()
    acker.close()


def test_bridge_pole_dropped_hop_is_fault_not_synthesized_ok(segment_factory):
    """THE correctness bar: sever the ack hop for the whole retry budget. The
    origin CANNOT get a genuine ack, so it MUST degrade to an explicit
    ``_bus/fault`` — never a fabricated ``ok`` — and NOTHING clears silently."""
    seg = segment_factory(["jumpdrive/*"])
    origin, acker = _setup_bridge_critical(seg, backoff_ms=(30, 60, 120))

    faults_seen = []
    seg.edge.subscribe_events("_bus/fault",
                              lambda ns, evt, data: faults_seen.append(data))
    seg.hub.inject_drop({"ack"}, seconds=2.0)   # > the ~330ms budget

    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["relay-master"],
                                  source="edge-panel")
    assert _wait(lambda: origin.resolution(cid) is not None, timeout=3.0)

    # ack-or-fault held: it resolved as a FAULT, never a synthesized ok.
    assert origin.resolution(cid) == "fault_timeout"
    assert origin.silent_clear_count == 0        # <-- the load-bearing proof
    assert origin.pending_cids() == []
    assert _wait(lambda: len(faults_seen) >= 1)
    assert faults_seen[-1]["fault"] == "ack_timeout"
    assert "relay-master" in faults_seen[-1]["unacked"]
    # The crit crossed (acker processed) but every ack was lost — the peer
    # synthesized nothing on the master's behalf.
    assert acker.processed >= 1
    origin.close()
    acker.close()


# --- (c) EGRESS pole: fire-and-forget, no handshake / ack path -------------

class _CapturingUrlopen:
    """Stand-in for urllib urlopen: records each POST and can block to prove the
    caller does not await delivery (fire-and-forget)."""

    def __init__(self, block_event=None):
        self.calls = []
        self._lock = threading.Lock()
        self._block = block_event
        self.posted = threading.Event()

    def __call__(self, req, timeout=None):
        with self._lock:
            self.calls.append(req.data)
        self.posted.set()
        if self._block is not None:
            self._block.wait(2.0)   # hold the "network" open
        return None


def test_egress_send_is_fire_and_forget_non_blocking(monkeypatch):
    """``send`` returns immediately — it does not await the POST (which is the
    whole point of the thin pole). Proven by holding the POST open and asserting
    ``send`` still returns before it completes."""
    release = threading.Event()
    cap = _CapturingUrlopen(block_event=release)
    monkeypatch.setattr(http_relay_mod, "urlopen", cap)

    relay = HttpRelayTransport(endpoint="http://localhost:8422/messages")
    relay.start()
    t0 = time.monotonic()
    relay.send(BusMessage(namespace="telemetry/cpu", type=MessageType.STATE,
                          value=42, source="probe"))
    assert (time.monotonic() - t0) < 0.5      # did not block on the POST
    assert cap.posted.wait(2.0)               # the POST did happen, out-of-band
    release.set()
    relay.stop()
    assert len(cap.calls) == 1


def test_egress_never_invokes_ack_machinery_even_for_critical(monkeypatch):
    """A ``pri: critical`` message routed through the EGRESS pole is treated
    EXACTLY like any other — it is POSTed fire-and-forget and NEVER opens an
    ack-pending entry or a designated acker. Spy the peer critical machinery and
    assert zero constructions."""
    constructions = {"origin": 0, "acker": 0}
    orig_origin_init = critical_mod.CriticalAckOrigin.__init__
    orig_acker_init = critical_mod.CriticalAcker.__init__

    def spy_origin(self, *a, **k):
        constructions["origin"] += 1
        return orig_origin_init(self, *a, **k)

    def spy_acker(self, *a, **k):
        constructions["acker"] += 1
        return orig_acker_init(self, *a, **k)

    monkeypatch.setattr(critical_mod.CriticalAckOrigin, "__init__", spy_origin)
    monkeypatch.setattr(critical_mod.CriticalAcker, "__init__", spy_acker)

    cap = _CapturingUrlopen()
    monkeypatch.setattr(http_relay_mod, "urlopen", cap)

    relay = HttpRelayTransport(endpoint="http://localhost:8422/messages")
    relay.start()

    # A message carrying the critical priority + a cid — the very shape the
    # BRIDGE pole would ack. The egress pole must ignore that weight.
    crit = BusMessage(namespace="jumpdrive/arm", type=MessageType.STATE,
                      value="on", source="edge-panel",
                      data={"_priority": "critical", "_cid": 7})
    relay.send(crit)
    assert cap.posted.wait(2.0)
    relay.stop()

    # It went out as one plain fire-and-forget POST...
    assert len(cap.calls) == 1
    # ...and NO ack machinery was ever touched.
    assert constructions == {"origin": 0, "acker": 0}
    # The adaptor exposes NONE of the bridge's ack/handshake surface.
    assert not hasattr(relay, "publish_ack")
    assert not hasattr(relay, "inject_drop")
    assert not hasattr(relay, "wait_ready")
    assert relay.profile.preserves_critical_ack is False


def test_egress_and_bridge_share_the_method_waist():
    """Both poles satisfy the SAME minimal method waist — the unification."""
    relay = HttpRelayTransport()
    hub = PeerHub()
    for emitter in (relay, hub):
        assert callable(emitter.send)
        assert callable(emitter.subscribe_all)
        assert callable(emitter.start)
        assert callable(emitter.stop)
        assert isinstance(emitter.is_connected, bool)
        assert isinstance(emitter.profile, TransportProfile)
