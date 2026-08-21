"""[BPR-LINK-DOWN] / [SCEN-BPR-LINKDOWN-CONVERGE] — tolerate the boundary being
down; sever/restore converges with zero lost critical-acks.

Capture-replay applied to the link ([AMEND:BP-CAPTURE-REPLAY] reuse): outbound
writes buffered during the outage replay in capture order on reconnect; the
buffered critical retains its cid so it resolves as ack-or-fault, never dropped.
"""

import time

from composeio.peer import CriticalAckOrigin, CriticalAcker


def _wait(pred, timeout=5.0, interval=0.005):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def test_sever_and_restore_converges_without_lost_critical_acks(segment_factory):
    seg = segment_factory(["jumpdrive/*"], backoff_initial=0.05)
    for ns in ("jumpdrive/arm", "jumpdrive/s1", "jumpdrive/s2", "jumpdrive/s3"):
        seg.master.register_namespace(ns, "json")
        seg.edge.register_namespace(ns, "json")
    assert seg.link.wait_ready(3.0)

    acker = CriticalAcker(seg.master, "jumpdrive/arm", "relay-master",
                          handler=lambda ns, val: "ok")
    # Long critical budget so it comfortably survives the outage window.
    origin = CriticalAckOrigin(seg.edge, backoff_ms=(50, 200, 1000))

    got = {}
    for ns in ("jumpdrive/s1", "jumpdrive/s2", "jumpdrive/s3"):
        seg.master.subscribe(ns, lambda n, new, old: got.__setitem__(n, new))

    # --- SEVER the boundary ---
    seg.hub.sever_all()
    assert _wait(lambda: not seg.link.is_connected)

    # Edge apps stay functional on local state throughout the outage.
    seg.edge.publish_state("jumpdrive/s1", "a", source="edge")
    seg.edge.publish_state("jumpdrive/s2", "b", source="edge")
    seg.edge.publish_state("jumpdrive/s3", "c", source="edge")
    assert seg.edge.get_state("jumpdrive/s1") == "a"          # local last-known-good
    # One critical write during the outage — buffered, must survive.
    cid = origin.publish_critical("jumpdrive/arm", "on",
                                  ack_from=["relay-master"], source="edge-panel")
    # Nothing has crossed yet (the link is down).
    assert got == {}

    # --- RESTORE (the link auto-reconnects with backoff) ---
    assert _wait(lambda: seg.link.is_connected, timeout=5.0)

    # All buffered state converges upstream ...
    assert _wait(lambda: got.get("jumpdrive/s1") == "a"
                 and got.get("jumpdrive/s2") == "b"
                 and got.get("jumpdrive/s3") == "c")
    # ... and the buffered critical resolves as a GENUINE ack — none lost.
    assert _wait(lambda: origin.resolution(cid) == "acked")
    assert origin.silent_clear_count == 0
    assert origin.faults == []
    origin.close()
    acker.close()
