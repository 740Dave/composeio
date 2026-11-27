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


def test_send_before_start_is_held_then_flushed_on_first_connect():
    """[CXC-SOB-PRE-START-HOLD] "not started" is held exactly like "not yet
    connected". This used to assert the OPPOSITE (dropped, on the premise that a
    send with no client is a configuration error); the injected-bus seam
    ([CXC-SOB-HANDLE]) made pre-start publishing the console's normal startup
    path, and 20 first values were lost on every console start."""
    t = MQTTTransport(broker="test.local", client_id="t")
    t.send(_msg("fleet/roster", [{"id": "architect"}]))
    t.send(_msg("self/daemons", {"n": 21}))
    assert len(t._pending) == 2                     # held, not dropped
    assert t.health()["started"] is False           # visible, not hidden
    assert t.health()["pending"] == 2

    fake = FakeClient()
    t._client = fake                                # as start() leaves it
    assert fake.published == []                     # still nothing before CONNACK
    t._on_connect(fake, None, {}, 0)
    assert [p[0] for p in fake.published] == [
        "composeio/global/fleet/roster", "composeio/global/self/daemons"]
    assert t._pending == []


def test_pre_start_hold_is_the_same_bounded_queue():
    """One queue, one cap ([DX-NARROW-WAIST]): a transport that is never
    started cannot grow without bound, and the overflow is COUNTED."""
    t = MQTTTransport(broker="test.local", client_id="t")
    for i in range(t.PENDING_MAX + 5):
        t.send(_msg("self/n", i))
    assert len(t._pending) == t.PENDING_MAX
    assert t._pending_dropped == 5
    assert json.loads(json.dumps(t._pending[0].value)) == 5   # oldest dropped


class _FakePahoModuleClient(FakeClient):
    """Just enough paho surface for MQTTTransport.start() to run offline."""

    def __init__(self, *a, **kw):
        super().__init__()

    def __getattr__(self, name):            # username_pw_set, will_set, ...
        return lambda *a, **kw: None


def test_bus_publish_before_start_reaches_the_wire(monkeypatch):
    """The CONSOLE ORDERING, end to end through the bus: a source publishes on
    the injected bus BEFORE bus.start() (runtime.main starts the in-process
    sources before app.run()), then the bus starts and the broker connects.
    The first value must be on the wire. The local bus holding it is not
    enough: peers and the broker's retained copy only see the wire."""
    import composeio.transport_mqtt as tm
    from composeio.bus import ComposeIOBus

    made: list[_FakePahoModuleClient] = []

    def _fake_new_client(**kw):
        c = _FakePahoModuleClient()
        made.append(c)
        return c

    monkeypatch.setattr(tm, "new_paho_client", _fake_new_client)
    monkeypatch.setattr(tm, "connect_with_backoff", lambda *a, **kw: None)

    t = MQTTTransport(broker="test.local", client_id="console")
    bus = ComposeIOBus(bus_id="console", global_transport=t)
    bus.publish_state("self/projects", {"ok": 3}, source="project-validator")
    assert bus.get_state("self/projects") == {"ok": 3}   # local copy: always fine

    bus.start(announce_time=False)
    assert len(made) == 1
    t._on_connect(made[0], None, {}, 0)                  # the CONNACK lands

    topics = [p[0] for p in made[0].published]
    assert "composeio/global/self/projects" in topics, topics


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
