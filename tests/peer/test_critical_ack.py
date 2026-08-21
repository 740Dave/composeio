"""[BPR-A-CRITICAL] / [BPR-CRITICAL-ACK] — THE correctness bar.

The peer is a TRANSPARENT forwarder for critical traffic: it forwards the
critical frame (with its cid) and the returning ack UNCHANGED, and NEVER
synthesizes an ``ok``. A ``pri: critical`` message resolves as a genuine
end-to-end ack OR an explicit ``_bus/fault`` degrade — never silent-loss.

Topology: origin on the EDGE, designated acker on the MASTER (a critical
command published from the isolated edge to an actuator behind the peer). The
fault-injection hook drops the ACK substream mid-flight ([SCEN-BPR-CRIT-DROP]).

The dropped-hop assertion of record: **silent-clear count = 0**.
"""

import time

import pytest

from composeio.peer import CriticalAckOrigin, CriticalAcker


def _wait(pred, timeout=3.0, interval=0.005):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _setup(seg, backoff_ms=(40, 80, 200)):
    seg.master.register_namespace("jumpdrive/arm", "json")
    seg.edge.register_namespace("jumpdrive/arm", "json")
    assert seg.link.wait_ready(3.0)
    acker = CriticalAcker(seg.master, "jumpdrive/arm", "relay-master",
                          handler=lambda ns, val: "ok")
    origin = CriticalAckOrigin(seg.edge, backoff_ms=backoff_ms)
    return origin, acker


def test_critical_resolves_as_genuine_end_to_end_ack(segment_factory):
    seg = segment_factory(["jumpdrive/*"])
    origin, acker = _setup(seg)

    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["relay-master"],
                                  source="edge-panel")
    assert _wait(lambda: origin.resolution(cid) == "acked")

    # Genuine end-to-end: a real acker did the processing; no fault raised.
    assert acker.processed >= 1
    assert origin.faults == []
    assert origin.silent_clear_count == 0
    assert origin.pending_cids() == []
    origin.close()
    acker.close()


def test_dropped_hop_yields_fault_and_zero_silent_clears(segment_factory):
    # Sever the ack hop for the whole retry budget -> the origin CANNOT get a
    # genuine ack, so it MUST degrade to an explicit _bus/fault. Assert
    # ack-or-fault held and NOTHING cleared silently.
    seg = segment_factory(["jumpdrive/*"])
    origin, acker = _setup(seg, backoff_ms=(30, 60, 120))

    faults_seen = []
    seg.edge.subscribe_events("_bus/fault",
                              lambda ns, evt, data: faults_seen.append(data))

    # Drop every returning ack for 2s (> the ~330ms budget) — the dropped hop.
    seg.hub.inject_drop({"ack"}, seconds=2.0)

    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["relay-master"],
                                  source="edge-panel")

    # Resolves — as a fault, not an ack, not silently.
    assert _wait(lambda: origin.resolution(cid) is not None, timeout=3.0)
    assert origin.resolution(cid) == "fault_timeout"

    # The ack-pending entry is GONE but only via the fault path.
    assert origin.pending_cids() == []
    assert origin.silent_clear_count == 0          # <-- the load-bearing proof

    # The fault is explicit, on _bus/fault, and names the unacked acker.
    assert _wait(lambda: len(faults_seen) >= 1)
    fault = faults_seen[-1]
    assert fault["fault"] == "ack_timeout"
    assert fault["target_ns"] == "jumpdrive/arm"
    assert fault["original_cid"] == cid
    assert "relay-master" in fault["unacked"]

    # The acker was reachable (crit crossed) and re-acked idempotently on each
    # retry, but every ack was lost on the hop — the peer synthesized nothing.
    assert acker.processed >= 1
    origin.close()
    acker.close()


def test_dropped_hop_then_recover_resolves_as_genuine_ack(segment_factory):
    # Drop the ack hop only briefly; on recovery a retry's ack gets through and
    # the entry resolves as a GENUINE ack — still zero silent clears, and the
    # origin never learned a false "delivered".
    seg = segment_factory(["jumpdrive/*"])
    origin, acker = _setup(seg, backoff_ms=(40, 80, 200))  # fault deadline ~520ms

    seg.hub.inject_drop({"ack"}, seconds=0.2)  # clears before the last retry

    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["relay-master"],
                                  source="edge-panel")
    assert _wait(lambda: origin.resolution(cid) is not None, timeout=3.0)
    assert origin.resolution(cid) == "acked"
    assert origin.faults == []
    assert origin.silent_clear_count == 0
    # Idempotent convergence: retries were processed once, re-acked from cache.
    assert acker.reacks >= 1
    origin.close()
    acker.close()


def test_reject_becomes_explicit_fault_not_silent_clear(segment_factory):
    # A designated acker that refuses (reject) resolves as an explicit fault,
    # never a silent clear and never a fabricated ok.
    seg = segment_factory(["jumpdrive/*"])
    seg.master.register_namespace("jumpdrive/arm", "json")
    seg.edge.register_namespace("jumpdrive/arm", "json")
    assert seg.link.wait_ready(3.0)
    acker = CriticalAcker(seg.master, "jumpdrive/arm", "relay-master",
                          handler=lambda ns, val: ("reject", "actuator_fault"))
    origin = CriticalAckOrigin(seg.edge, backoff_ms=(40, 80, 200))

    cid = origin.publish_critical("jumpdrive/arm", "on", ack_from=["relay-master"],
                                  source="edge-panel")
    assert _wait(lambda: origin.resolution(cid) == "reject", timeout=3.0)
    assert origin.silent_clear_count == 0
    assert any(f["fault"] == "ack_reject" for f in origin.faults)
    origin.close()
    acker.close()
