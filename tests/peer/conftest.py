"""Slice A test substrate — two ComposeBus brokers + one relay process, no hardware.

[BPR-A-TEST]: the harness stands up a master ComposeBus and an edge ComposeBus
joined by a single PeerLink->PeerHub session over loopback TCP. This is the
portable, no-privilege realization of the "two brokers in two network
namespaces" substrate: netns would harden the isolation posture further (the
network-architect's glass gate), but loopback already makes the load-bearing
posture MECHANICALLY assertable — the edge opens no listener and a connect into
the edge is refused.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from composeio.bus import ComposeIOBus
from composeio.peer import PeerHub, PeerLink


class Segment:
    """A master+edge pair joined by one pull-initiated peer session."""

    def __init__(self, master, hub, edge, link):
        self.master = master
        self.hub = hub
        self.edge = edge
        self.link = link

    def close(self):
        try:
            self.edge.stop()
        except Exception:
            pass
        try:
            self.master.stop()
        except Exception:
            pass


def make_segment(subscriptions, auth_token="TESTTOKEN", resolver=None,
                 backoff_initial=0.05, connect_timeout=2.0, auto_reconnect=True):
    """Build master(hub) + edge(link), start both, wait for the session."""
    master = ComposeIOBus(bus_id="master")
    hub = PeerHub(auth_token=auth_token, resolver=resolver)
    master._global = hub
    hub.bind_bus(master)
    master.start()  # bus.start() wires + starts the hub transport

    edge = ComposeIOBus(bus_id="edge")
    link = PeerLink(host="127.0.0.1", port=hub.port, subscriptions=subscriptions,
                    auth_token=auth_token, backoff_initial=backoff_initial,
                    connect_timeout=connect_timeout, auto_reconnect=auto_reconnect)
    edge._global = link
    edge.start()  # bus.start() wires + starts the link (opens outbound session)
    return Segment(master, hub, edge, link)


@pytest.fixture
def segment_factory():
    made = []

    def _factory(subscriptions, **kw):
        seg = make_segment(subscriptions, **kw)
        made.append(seg)
        return seg

    yield _factory
    for seg in made:
        seg.close()
