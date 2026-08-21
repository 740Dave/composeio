"""[BPR-B-SYNTH] / [SCEN-BPR-B-DEGRADE] — the CAN bridge on a mock CAN interface.

The CAN bridge is a Slice B CONSUMER of the Slice A core: it plugs into the
local ComposeBus as the ``_global`` Transport and speaks CAN on the far side.
These tests run entirely against the pure-Python mock CAN interface
([DD-BPR-MOCK-CAN] alternative b) — NO hardware, NO threads for the wire (mock
delivery is synchronous), only the origin's own real retry timers (short).

THE load-bearing proof ([BPR-B-SYNTH], [BPR-CRITICAL-ACK] extended to CAN):
a CAN-side ``pri: critical`` yields a CONFIRMED upstream ack ONLY from an ACTUAL
[BP-ACK-CAN] type-8 frame, OR an explicit ``_bus/fault``. A bare electrical
hardware ack ("seen on the wire") is NEVER fabricated into an ``ok``, and the
origin's ack-pending entry NEVER clears silently — ``silent_clear_count == 0``.
"""

import time

import pytest

from composeio.bus import ComposeIOBus
from composeio.peer import (
    CanBridge, CanIdRegistry, MockCanBus, MockCanNode, MockCanEcu,
    CriticalAckOrigin, ActuationGuard, fragment,
)
from composeio.peer.canmap import ACK_FLAG


def _wait(pred, timeout=3.0, interval=0.005):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


class Harness:
    def __init__(self, subscriptions=("jumpdrive/*",), emit_app_ack=True,
                 handler=None, acker_id="can-ecu"):
        self.bus = ComposeIOBus(bus_id="can-edge")
        self.registry = CanIdRegistry()
        self.registry.register_prefix("jumpdrive", block_bits=4)
        for ns in ("jumpdrive/arm", "jumpdrive/throttle", "jumpdrive/status"):
            self.bus.register_namespace(ns, "json")
            self.registry.register(ns)
        self.can = MockCanBus()
        self.bridge = CanBridge(self.registry, self.can,
                                subscriptions=list(subscriptions),
                                acker_id=acker_id)
        self.bus._global = self.bridge
        self.bus.start()   # wires transport-incoming dispatch + attaches the bridge node
        self.ecu = MockCanEcu(self.registry, name=acker_id,
                              emit_app_ack=emit_app_ack, handler=handler)
        self.ecu.attach_to(self.can)

    def close(self):
        try:
            self.bus.stop()
        except Exception:
            pass


@pytest.fixture
def harness_factory():
    made = []

    def _make(**kw):
        h = Harness(**kw)
        made.append(h)
        return h

    yield _make
    for h in made:
        h.close()


# --- [BPR-B-CANMAP] egress mapping-miss --------------------------------------

def test_unmapped_namespace_on_egress_faults_no_fabricated_id(harness_factory):
    h = harness_factory()
    h.bus.register_namespace("weapons/fire", "json")   # on the bus, NOT in the CAN registry
    before = h.can.tx_count
    h.bus.publish_state("weapons/fire", "boom", source="panel")
    # No CAN frame was fabricated, and an explicit mapping-miss fault was raised.
    assert h.can.tx_count == before
    assert any(f["fault"] == "mapping_miss" and f["target_ns"] == "weapons/fire"
               for f in h.bridge.faults)


# --- [BPR-B-FRAG] large state egress reassembles at the ECU ------------------

def test_large_state_payload_reassembles_at_the_ecu(harness_factory):
    h = harness_factory()
    big = {"vec": list(range(60))}   # JSON >> one 8-byte CAN frame
    h.bus.publish_state("jumpdrive/status", big, source="panel")
    assert h.bridge.egress_frames > 1          # actually fragmented
    assert ("jumpdrive/status", big) in h.ecu.received


# --- [BPR-B-SYNTH] critical -> CONFIRMED ack from a type-8 frame --------------

def test_critical_confirmed_only_via_type8_application_ack(harness_factory):
    h = harness_factory(emit_app_ack=True, handler=lambda ns, val: "ok")
    origin = CriticalAckOrigin(h.bus, backoff_ms=(40, 80, 200))

    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["can-ecu"],
                                  source="edge-panel")

    assert _wait(lambda: origin.resolution(cid) == "acked")
    # The upstream ack came from an ACTUAL type-8 frame, not fabrication.
    assert h.ecu.app_acks_sent >= 1
    assert ("jumpdrive/arm", cid, "ok") in h.bridge.app_acks_in
    assert origin.faults == []
    assert origin.silent_clear_count == 0
    assert origin.pending_cids() == []
    origin.close()


# --- [SCEN-BPR-B-DEGRADE] withheld type-8 -> fault, hw-ack never becomes ok ---

def test_bare_hardware_ack_does_not_become_ok_it_degrades_to_fault(harness_factory):
    # The ECU RECEIVES the critical electrically (the wire reports a hardware
    # ack) but its firmware emits NO type-8 application ack. The bridge MUST NOT
    # fabricate an ok; the origin must degrade to an explicit fault.
    h = harness_factory(emit_app_ack=False, handler=lambda ns, val: "ok")

    faults_seen = []
    h.bus.subscribe_events("_bus/fault",
                           lambda ns, evt, data: faults_seen.append(data))

    origin = CriticalAckOrigin(h.bus, backoff_ms=(30, 60, 120))
    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["can-ecu"],
                                  source="edge-panel")

    # Resolves — as a fault, NOT an ack, NOT silently.
    assert _wait(lambda: origin.resolution(cid) is not None, timeout=3.0)
    assert origin.resolution(cid) == "fault_timeout"
    assert origin.pending_cids() == []
    assert origin.silent_clear_count == 0             # <-- the load-bearing proof

    # A hardware/electrical ack WAS observed on every send...
    assert h.bridge.hw_acks, "expected the mock wire to report an electrical ack"
    assert all(hw is True for (_c, hw) in h.bridge.hw_acks)
    # ...yet it was NEVER turned into an upstream application ack.
    assert h.bridge.app_acks_in == []
    assert h.ecu.app_acks_sent == 0
    # The ECU DID receive+process the frame (it was reachable), proving the fault
    # is "no confirmed processing", not "unreachable".
    assert any(ns == "jumpdrive/arm" for ns, _v in h.ecu.received)

    # The degrade is an explicit _bus/fault naming the unacked acker.
    assert _wait(lambda: len(faults_seen) >= 1)
    assert faults_seen[-1]["fault"] == "ack_timeout"
    assert "can-ecu" in faults_seen[-1]["unacked"]
    origin.close()


def test_can_reject_becomes_explicit_fault_not_silent_clear(harness_factory):
    # A type-8 frame carrying a reject status resolves as an explicit fault,
    # never a silent clear and never a fabricated ok.
    h = harness_factory(emit_app_ack=True, handler=lambda ns, val: "reject")
    origin = CriticalAckOrigin(h.bus, backoff_ms=(40, 80, 200))

    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["can-ecu"],
                                  source="edge-panel")
    assert _wait(lambda: origin.resolution(cid) == "reject", timeout=3.0)
    assert origin.silent_clear_count == 0
    assert any(f["fault"] == "ack_reject" for f in origin.faults)
    origin.close()


# --- [BPR-B-SYNTH] synthesized retain ----------------------------------------

def test_synthesized_retain_rebroadcasts_last_known_state_to_late_joiner(harness_factory):
    h = harness_factory()
    h.bus.publish_state("jumpdrive/throttle", 42, source="panel")

    # A CAN node that joins AFTER the state was published (a late joiner) sees
    # nothing until the bridge synthesizes retain by re-broadcasting.
    seen = []
    from composeio.peer.frag import Reassembler
    reasm = Reassembler(frame_size=8)
    import json as _json

    def _on(cid, data):
        payload = reasm.feed(cid, data, 0.0)
        if payload is not None:
            seen.append(_json.loads(payload.decode()))

    late = MockCanNode("late-joiner", on_receive=_on)
    late.set_filters([h.registry.prefix_to_mask("jumpdrive/*")])
    h.can.attach(late)

    assert seen == []                       # missed the original broadcast
    sent = h.bridge.rebroadcast_retained()  # synthesized retain
    assert sent >= 1
    assert 42 in seen                        # late joiner now has last-known state


# --- [BPR-PROVENANCE] CAN ingress is relayed -> actuation quarantined ---------

def test_can_ingress_is_provenance_stamped_and_does_not_actuate_without_optin(harness_factory):
    h = harness_factory()
    guard = ActuationGuard(h.bus)
    fired = []
    guard.protect("jumpdrive/arm", lambda ns, val: fired.append(val))

    # Inject a state frame "from CAN" via a separate node so the bridge receives it.
    injector = MockCanNode("injector", on_receive=lambda c, d: None)
    h.can.attach(injector)
    import json as _json
    can_id = h.registry.namespace_to_id("jumpdrive/arm")
    for fr in fragment(_json.dumps("on").encode(), 8):
        h.can.send(can_id, fr, sender=injector)

    # The CAN-sourced state reached the bus but is quarantined (relayed, no opt-in).
    assert _wait(lambda: len(guard.refused) >= 1)
    assert fired == []
    assert any(ns == "jumpdrive/arm" for ns, _src in guard.refused)
    guard.close()


# --- bridge acceptance filters: ONE mask per prefix + one ack filter ----------

def test_bridge_installs_one_mask_per_subscription_plus_ack_filter(harness_factory):
    h = harness_factory(subscriptions=("jumpdrive/*",))
    filters = h.bridge.acceptance_filters()
    # exactly: one data mask for the prefix + one ack filter.
    assert len(filters) == 2
    assert (ACK_FLAG, ACK_FLAG) in filters
    data_filter = [f for f in filters if f != (ACK_FLAG, ACK_FLAG)][0]
    assert data_filter == h.registry.prefix_to_mask("jumpdrive/*")
