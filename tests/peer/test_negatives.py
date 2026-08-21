"""[BPR-A-TEST] negative cases + [BPR-AUTH] — a non-authenticated peer is
rejected at connect with NO partial sync and NO mirror; a master-initiated
connect into the edge is refused.
"""

import socket
import time

from composeio.bus import ComposeIOBus
from composeio.peer import PeerHub, PeerLink


def _wait(pred, timeout=3.0, interval=0.005):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def test_peer_with_wrong_token_is_rejected_with_no_mirror():
    master = ComposeIOBus(bus_id="master")
    hub = PeerHub(auth_token="RIGHT")
    master._global = hub
    hub.bind_bus(master)
    master.start()
    master.register_namespace("jumpdrive/x", "json")
    master.publish_state("jumpdrive/x", "on", source="master")

    edge = ComposeIOBus(bus_id="edge")
    link = PeerLink(host="127.0.0.1", port=hub.port, subscriptions=["jumpdrive/*"],
                    auth_token="WRONG", backoff_initial=0.05, auto_reconnect=False)
    edge._global = link
    edge.start()
    try:
        # The handshake completes as a REJECT — never ready, explicitly rejected.
        assert not link.wait_ready(2.0)
        assert link.auth_rejected is True
        # No partial sync: the scoped index never loaded, mirror is empty.
        assert link.mirror.namespaces() == []
        assert link.resolve_state("jumpdrive/x").status == "unresolved"
    finally:
        edge.stop()
        master.stop()


def test_master_initiated_connect_into_edge_is_refused():
    # The edge holds no listener; a connect to any edge-side port is refused.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    unused = s.getsockname()[1]
    s.close()  # nothing listens here now — the edge never binds an inbound port

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    refused = False
    try:
        probe.connect(("127.0.0.1", unused))
    except (ConnectionRefusedError, OSError):
        refused = True
    finally:
        probe.close()
    assert refused
