"""[BPR-PROVENANCE] — relayed/mirrored traffic carries provenance and cannot
silently drive actuation. A peer-relayed control message must NOT actuate a
mock actuator without explicit opt-in; WITH opt-in it fires.
"""

import time


def _wait(pred, timeout=3.0, interval=0.005):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def test_relayed_control_does_not_actuate_without_opt_in(segment_factory):
    from composeio.peer import ActuationGuard

    seg = segment_factory(["jumpdrive/*"])
    seg.master.register_namespace("jumpdrive/relay", "json")
    seg.edge.register_namespace("jumpdrive/relay", "json")
    assert seg.link.wait_ready(3.0)

    fired = []
    guard = ActuationGuard(seg.edge)
    guard.protect("jumpdrive/relay", lambda ns, val: fired.append(val))

    # Relayed control crosses the peer onto the edge bus.
    seg.master.publish_state("jumpdrive/relay", "on", source="master-ctrl")

    # Without opt-in: refused, actuator stays cold.
    assert _wait(lambda: len(guard.refused) >= 1)
    assert fired == []

    # With explicit opt-in: the actuator fires.
    guard.opt_in("jumpdrive/relay")
    seg.master.publish_state("jumpdrive/relay", "off", source="master-ctrl")
    assert _wait(lambda: "off" in fired)
    guard.close()


def test_relayed_traffic_is_provenance_stamped(segment_factory):
    # State arriving over the peer is stamped relayed + peer-prefixed source, so
    # the far bus can tell peer traffic from live-local traffic.
    seg = segment_factory(["jumpdrive/*"])
    seg.master.register_namespace("jumpdrive/telemetry", "json")
    seg.edge.register_namespace("jumpdrive/telemetry", "json")
    assert seg.link.wait_ready(3.0)

    captured = []
    seg.edge.on_message(lambda msg: captured.append(msg)
                        if msg.namespace == "jumpdrive/telemetry" else None)
    seg.master.publish_state("jumpdrive/telemetry", "42", source="sensor-1")
    assert _wait(lambda: any(m.namespace == "jumpdrive/telemetry" for m in captured))
    relayed = [m for m in captured if m.namespace == "jumpdrive/telemetry"][-1]
    assert relayed.data.get("relayed") is True
    assert relayed.source.startswith("peer:")
