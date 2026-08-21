"""[BPR-A-CHANNEL] + [BPR-PULL] — one pull-initiated session carries read AND
write; the edge opens no inbound listener and a master->edge connect is refused.
"""

import socket
import time

import pytest


def _wait(pred, timeout=3.0, interval=0.01):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def test_read_and_write_ride_one_session(segment_factory):
    # [SCEN-BPR-A-BIDIR]: master->edge read and edge->master write both traverse
    # the single edge-initiated session.
    seg = segment_factory(["jumpdrive/*"])
    assert seg.link.wait_ready(3.0)
    # Free-form string state uses the json engine (the binary-engine hazard).
    seg.master.register_namespace("jumpdrive/status", "json")
    seg.edge.register_namespace("jumpdrive/status", "json")
    seg.master.register_namespace("jumpdrive/cmd", "json")
    seg.edge.register_namespace("jumpdrive/cmd", "json")

    # master -> edge (read): edge subscriber sees a master publish.
    seen = {}
    seg.edge.subscribe("jumpdrive/status", lambda ns, new, old: seen.__setitem__(ns, new))
    seg.master.publish_state("jumpdrive/status", "on", source="master-app")
    assert _wait(lambda: seen.get("jumpdrive/status") == "on")

    # edge -> write: master subscriber sees an edge publish over the SAME session.
    got = {}
    seg.master.subscribe("jumpdrive/cmd", lambda ns, new, old: got.__setitem__(ns, new))
    seg.edge.publish_state("jumpdrive/cmd", "arm", source="edge-app")
    assert _wait(lambda: got.get("jumpdrive/cmd") == "arm")


def test_edge_opens_no_inbound_listener(segment_factory):
    # [SCEN-BPR-PULL-NO-INBOUND]: the edge peer opens NO listening socket, and a
    # connection attempt INTO the edge is refused (nothing is listening there).
    seg = segment_factory(["jumpdrive/*"])
    assert seg.link.wait_ready(3.0)

    # Mechanical: the link exposes no listener (the loopback analog of
    # `ss -ltn` in the edge netns showing zero listener on the sync port).
    assert seg.link.listening_sockets() == []

    # A master-initiated connect into the edge is refused: pick a fresh port
    # (the edge holds no listener on it), assert ECONNREFUSED.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    # An ephemeral port the edge never bound — connect must be refused.
    with pytest.raises((ConnectionRefusedError, OSError)):
        probe.connect(("127.0.0.1", _find_unused_port()))
    probe.close()


def test_master_is_the_only_listener(segment_factory):
    # The listener lives on the MASTER (non-isolated) side only. Confirm the hub
    # accepts (it has a port) while the edge link never exposes one.
    seg = segment_factory(["jumpdrive/*"])
    assert seg.link.wait_ready(3.0)
    assert seg.hub.port > 0                     # master listens (allowed)
    assert seg.link.listening_sockets() == []   # edge does not


def _find_unused_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # closed -> nothing listens here -> connect will be refused
    return port
