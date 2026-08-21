"""[DEC-CONSOLE-MQTT-STARTUP-RACE] pre-connect sends are QUEUED, not dropped.

A scene publishes its first `_bus/time` tick and its namespace echoes within
~300ms of `start()` — before paho's connect callback lands.  Those sends used
to be logged as "not connected, dropping" and genuinely lost.

These assert what lands on the WIRE (the fake client's publish log), not what
the code intended to do — the message that never reaches the broker is exactly
the failure a code-shaped test misses.
"""

from __future__ import annotations

import json

import pytest

from composeio.transport import BusMessage, MessageType
from composeio.transport_mqtt import MQTTTransport


class FakeClient:
    """Records publishes/subscribes the way paho would receive them."""

    def __init__(self):
        self.published: list[tuple[str, str, int]] = []
        self.subscribed: list[str] = []

    # `retain`/`properties` accepted because the real paho client accepts them
    # and the transport now passes them ([BP-MQTT-QOS] retain + the MQTT 5
    # Message Expiry Interval age contract). A double that lags the interface
    # it doubles fails on the ARGUMENTS rather than on the behaviour under
    # test, which is noise. These assertions are about QUEUEING, so the two
    # extra fields are recorded and ignored here — retain itself is asserted
    # in test_transport_mqtt_retain.py.
    def publish(self, topic, payload, qos=0, retain=False, properties=None):
        self.published.append((topic, payload, qos))

    def subscribe(self, topic, qos=1):
        self.subscribed.append(topic)


def _msg(ns: str, value) -> BusMessage:
    return BusMessage(namespace=ns, value=value, type=MessageType.STATE,
                      scope="global")


def _wired() -> tuple[MQTTTransport, FakeClient]:
    t = MQTTTransport(broker="test.local", client_id="t")
    fake = FakeClient()
    t._client = fake                      # as start() would leave it
    return t, fake


def test_pre_connect_sends_are_held_then_flushed_in_order():
    t, fake = _wired()

    t.send(_msg("_bus/time", 1))
    t.send(_msg("self/deliverables", {"tracked": 41}))
    assert fake.published == []           # nothing on the wire yet
    assert len(t._pending) == 2

    t._on_connect(fake, None, {}, 0)

    topics = [p[0] for p in fake.published]
    assert topics == ["composeio/global/_bus/time",
                      "composeio/global/self/deliverables"]   # order preserved
    assert t._pending == []


def test_after_connect_sends_go_straight_out():
    t, fake = _wired()
    t._on_connect(fake, None, {}, 0)
    t.send(_msg("self/context_pct", 45.0))
    assert [p[0] for p in fake.published] == ["composeio/global/self/context_pct"]
    assert t._pending == []


def test_queue_is_bounded_and_keeps_the_newest():
    t, fake = _wired()
    for i in range(t.PENDING_MAX + 10):
        t.send(_msg("burst", i))
    assert len(t._pending) == t.PENDING_MAX
    assert t._pending_dropped == 10

    t._on_connect(fake, None, {}, 0)
    assert len(fake.published) == t.PENDING_MAX
    # the OLDEST were dropped — the last message sent must survive.  Read the
    # value back off the WIRE payload rather than trusting a remembered field
    # name (it is `val`, not `value` — the first cut of this test guessed).
    last = BusMessage.from_json(json.loads(fake.published[-1][1]))
    assert last.value == t.PENDING_MAX + 9


def test_a_failed_connect_does_not_flush():
    t, fake = _wired()
    t.send(_msg("_bus/time", 1))
    t._on_connect(fake, None, {}, 5)      # rc != 0 -> refused
    assert fake.published == []
    assert len(t._pending) == 1

    t._on_connect(fake, None, {}, 0)      # a later successful connect flushes
    assert len(fake.published) == 1


def test_send_before_start_is_dropped_not_queued():
    """No client at all means the transport was never started — queueing there
    would hide a configuration error behind a growing list."""
    t = MQTTTransport(broker="test.local", client_id="t")
    t.send(_msg("_bus/time", 1))
    assert t._pending == []


def test_disconnect_then_reconnect_holds_the_gap():
    t, fake = _wired()
    t._on_connect(fake, None, {}, 0)
    t._on_disconnect(fake, None, 1)       # unexpected drop
    t.send(_msg("self/activity", {"tool": "Edit"}))
    assert fake.published == []           # held, not lost
    t._on_connect(fake, None, {}, 0)
    assert [p[0] for p in fake.published] == ["composeio/global/self/activity"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
