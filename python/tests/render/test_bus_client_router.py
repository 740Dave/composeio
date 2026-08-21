"""Shared bus client + subscriber router — Phase 2a step 2 (2026-08-08).

The console held 20 TCP connections and 45 threads from ONE process because 17 of
its 18 data sources each built their own `mqtt.Client()`. Step 2a collapsed the 9
publish-only sources onto one socket; this pins the step-2b ROUTER that lets
SUBSCRIBERS share it too.

A shared client has exactly ONE `on_message`, so a subscriber that assigns the
callback directly silently steals delivery from every other subscriber — no
error, just silence. The router is that single callback, fanning out by topic
filter. The two properties that keep it honest are tested hardest here:

  * MQTT filter semantics (`+` one level, `#` the rest) — a near-miss drops
    messages with NO error, which is precisely the failure class this phase
    exists to remove;
  * handler isolation — one raising consumer must not silence its siblings.

`python/composeio/bus_client.py` carries the same checks as a runnable selftest;
this file exists so the PRE-COMMIT GATE runs them (the gate maps `python/**.py`
to the render suite).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from composeio import bus_client as bc  # noqa: E402


class _Msg:
    def __init__(self, topic):
        self.topic = topic
        self.payload = b"{}"


class _FakeClient:
    def __init__(self):
        self.on_message = None
        self.connected = None
        self.looping = False
        self.subs = []
        self.sent = []

    def connect(self, h, p, keepalive=60):
        self.connected = (h, p)

    def loop_start(self):
        self.looping = True

    def subscribe(self, t, qos=0):
        self.subs.append((t, qos))

    def publish(self, t, p, qos=0, retain=False):
        self.sent.append((t, p, qos))


@pytest.fixture(autouse=True)
def _clean():
    bc._reset_for_tests()
    yield
    bc._reset_for_tests()


# --------------------------------------------------------------------------- #
# topic filter matching — where silent message loss would hide
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("filt,topic,want", [
    ("composeio/global/self/x", "composeio/global/self/x", True),
    ("composeio/global/self/x", "composeio/global/self/y", False),
    ("composeio/+/self/x",      "composeio/global/self/x", True),
    ("composeio/+/self/x",      "composeio/local/self/x",  True),
    # `+` is EXACTLY one level and must not span a slash
    ("composeio/+/x",           "composeio/a/b/x",         False),
    ("composeio/#",             "composeio/global/self/x", True),
    # MQTT 3.1.1 §4.7.1.2: "sport/# also matches the singular sport" — `#`
    # includes the PARENT level. Counter-intuitive, so pinned with the citation.
    ("composeio/#",             "composeio",               True),
    ("#",                       "anything/at/all",         True),
    ("composeio/global/a/b",    "composeio/global/a",      False),
    ("composeio/global",        "composeio/global/a",      False),
    # the real console intent filters
    ("composeio/+/_app/doc/open", "composeio/global/_app/doc/open", True),
    ("composeio/+/_app/doc/open", "composeio/global/_app/dsd/open", False),
])
def test_topic_matches(filt, topic, want):
    assert bc.topic_matches(filt, topic) is want


# --------------------------------------------------------------------------- #
# fan-out
# --------------------------------------------------------------------------- #
def test_many_subscribers_share_one_socket_and_get_correct_fanout():
    made, seen = [], {"a": 0, "b": 0, "c": 0}

    def factory():
        c = _FakeClient(); made.append(c); return c

    bc.subscribe("mqtt://h:1883", "composeio/+/self/a",
                 lambda c, u, m: seen.__setitem__("a", seen["a"] + 1),
                 label="A", client_factory=factory)
    bc.subscribe("mqtt://h:1883", "composeio/#",
                 lambda c, u, m: seen.__setitem__("b", seen["b"] + 1),
                 label="B", client_factory=factory)
    bc.subscribe("mqtt://h:1883", "composeio/+/self/zzz",
                 lambda c, u, m: seen.__setitem__("c", seen["c"] + 1),
                 label="C", client_factory=factory)

    assert len(made) == 1, "3 subscribers must share ONE client"
    assert bc.connection_count() == 1
    assert bc.route_count("mqtt://h:1883") == 3

    cl = made[0]
    cl.on_message(cl, None, _Msg("composeio/global/self/a"))
    assert seen == {"a": 1, "b": 1, "c": 0}, "fan-out hit the wrong handler set"
    assert ("composeio/+/self/a", 0) in cl.subs, "broker-side subscribe not issued"


def test_raising_handler_does_not_silence_siblings():
    """The delivery-loss guard: one bad consumer must not mute the others."""
    made, hits = [], {"good": 0}

    def factory():
        c = _FakeClient(); made.append(c); return c

    def boom(c, u, m):
        raise RuntimeError("handler exploded")

    bc.subscribe("mqtt://h:1883", "x/#", boom, label="bad", client_factory=factory)
    bc.subscribe("mqtt://h:1883", "x/#",
                 lambda c, u, m: hits.__setitem__("good", hits["good"] + 1),
                 label="good", client_factory=factory)

    cl = made[0]
    cl.on_message(cl, None, _Msg("x/y"))
    assert hits["good"] == 1, "a raising handler silenced its sibling"


def test_publishers_and_subscribers_share_the_same_socket():
    """A publish-only client gains the router when the first subscriber arrives.

    Without this, publishers and subscribers would split into TWO sockets and
    step 2 would only be half done.
    """
    made, got = [], {"n": 0}

    def factory():
        c = _FakeClient(); made.append(c); return c

    pub = bc.acquire("mqtt://h:1883", label="pub", client_factory=factory)
    sub = bc.subscribe("mqtt://h:1883", "q/#",
                       lambda c, u, m: got.__setitem__("n", got["n"] + 1),
                       label="sub", client_factory=factory)
    assert pub is sub
    assert len(made) == 1, "publisher + subscriber must share ONE socket"
    pub.on_message(pub, None, _Msg("q/z"))
    assert got["n"] == 1, "router was not attached to the pre-existing client"


# --------------------------------------------------------------------------- #
# the publish-only guard (step 2a)
# --------------------------------------------------------------------------- #
def test_acquire_refuses_a_declared_subscriber():
    """`acquire(will_subscribe=True)` REFUSES rather than documenting a footgun."""
    with pytest.raises(ValueError, match="PUBLISH-ONLY"):
        bc.acquire("mqtt://h:1883", label="channel_bridge", will_subscribe=True,
                   client_factory=_FakeClient)


def test_n_publishers_one_connection():
    made = []

    def factory():
        c = _FakeClient(); made.append(c); return c

    for label in ("self", "transcript", "meta", "memory", "agents_skills",
                  "deliverable_publisher", "deliverable_watcher",
                  "defect_metrics", "lts_engine_status"):
        bc.acquire("mqtt://h:1883", label=label, client_factory=factory)

    assert len(made) == 1, "the 9 publish-only sources must share ONE client"
    assert bc.connection_count() == 1
    assert bc.publisher_count() == 9


def test_different_brokers_are_not_over_shared():
    made = []

    def factory():
        c = _FakeClient(); made.append(c); return c

    a = bc.acquire("mqtt://h:1883", label="x", client_factory=factory)
    b = bc.acquire("mqtt://other:1883", label="y", client_factory=factory)
    assert a is not b
    assert bc.connection_count() == 2


@pytest.mark.parametrize("url,want", [
    ("mqtt://mqtt-broker.local:1883", ("mqtt-broker.local", 1883)),
    ("tcp://h:1", ("h", 1)),
    ("host", ("host", 1883)),
    ("", ("localhost", 1883)),
])
def test_parse_mqtt_url(url, want):
    """Single-sourced here; every data source used to carry its own copy."""
    assert bc.parse_mqtt_url(url) == want
