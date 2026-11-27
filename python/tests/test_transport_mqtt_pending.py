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
import logging
import os
import random
import sys
import threading
import time

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


# ── [CXC-SOB-PRE-START-HOLD] thread safety of the ONE queue ─────────────────
#
# `_pending` is appended from every source thread and flushed on paho's
# network thread. The hazard is a check-then-act race: a send reads "not
# connected", loses the CPU, the CONNACK marks the link up and swaps the
# queue out for a fresh list, and the send then appends to the FRESH list.
# That message is stranded while the link is up, and the sender's next
# message (which sees "connected") goes out AHEAD of it.


class _WindowLink:
    """Wraps the transport's LinkHealth so the FIRST read of `connected` on
    one chosen thread opens a window: it signals `in_window`, then waits (up
    to `hold_s`) for `flush_done`. That forces the race's interleaving
    deterministically instead of hoping the scheduler finds it."""

    def __init__(self, inner, *, hold_s: float = 0.5):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "victim", None)
        object.__setattr__(self, "in_window", threading.Event())
        object.__setattr__(self, "flush_done", threading.Event())
        object.__setattr__(self, "hold_s", hold_s)
        object.__setattr__(self, "_armed", True)

    def __getattr__(self, name):
        if name == "connected":
            value = self._inner.connected
            if (self._armed and threading.current_thread() is self.victim
                    and not value):
                object.__setattr__(self, "_armed", False)
                self.in_window.set()
                self.flush_done.wait(self.hold_s)
            return value
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


def test_a_send_racing_the_connect_is_never_stranded_or_reordered():
    """Deterministic. The sender reads "not connected" and is held in that
    window while the CONNACK runs on another thread. With the pending lock the
    CONNACK cannot mark the link up and flush until the send has queued its
    message, so the message is flushed, and the sender's NEXT message goes
    out after it. Without the lock the flush runs inside the window, the
    message lands in the fresh queue and is stranded while the link is up."""
    t, fake = _wired()
    link = _WindowLink(t._link)
    t._link = link

    def sender():
        t.send(_msg("race/a", 1))
        t.send(_msg("race/a", 2))

    def connacker():
        assert link.in_window.wait(5)
        t._on_connect(fake, None, {}, 0)
        link.flush_done.set()

    s = threading.Thread(target=sender)
    c = threading.Thread(target=connacker)
    object.__setattr__(link, "victim", s)
    c.start()
    s.start()
    s.join(10)
    c.join(10)
    assert not s.is_alive() and not c.is_alive()

    values = [BusMessage.from_json(json.loads(p[1])).value
              for p in fake.published if p[0] == "composeio/global/race/a"]
    assert t._pending == [], "a message was stranded in the queue while connected"
    assert values == [1, 2], values


@pytest.mark.parametrize("seed", [20261127, 7, 1234])
def test_concurrent_senders_and_flushes_lose_nothing_and_keep_per_thread_order(seed):
    """Stress, with a fixed seed. N sender threads publish while another
    thread repeatedly connects and drops the link (each connect flushes the
    queue on that thread, as paho would). At the end one last connect flushes
    whatever is held. Every message must be on the wire EXACTLY once, and each
    thread's messages in the order it sent them. A tiny switch interval makes
    preemption inside the check-then-act window likely; this test is the
    probabilistic companion of the deterministic one above."""
    rng = random.Random(seed)
    n_threads, per_thread = 8, 300
    t, fake = _wired()
    t.PENDING_MAX = n_threads * per_thread * 2      # no cap drops in this test
    stop = threading.Event()
    go = threading.Barrier(n_threads + 1)
    pauses = [[rng.random() < 0.05 for _ in range(per_thread)]
              for _ in range(n_threads)]
    flips = [rng.random() * 0.0005 for _ in range(10_000)]

    def sender(k):
        go.wait()
        for i in range(per_thread):
            t.send(_msg(f"stress/t{k}", i))
            if pauses[k][i]:
                time.sleep(0)

    def flapper():
        go.wait()
        j = 0
        while not stop.is_set():
            t._on_connect(fake, None, {}, 0)
            time.sleep(flips[j % len(flips)])
            t._on_disconnect(fake, None, 1)
            time.sleep(flips[(j + 1) % len(flips)])
            j += 2

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        senders = [threading.Thread(target=sender, args=(k,))
                   for k in range(n_threads)]
        f = threading.Thread(target=flapper)
        for th in senders:
            th.start()
        f.start()
        go_wait_ok = True
        for th in senders:
            th.join(60)
            go_wait_ok = go_wait_ok and not th.is_alive()
        stop.set()
        f.join(60)
    finally:
        sys.setswitchinterval(old)
    assert go_wait_ok and not f.is_alive()

    # Order check FIRST, on the wire as it stood with the link last up and
    # then down: anything still held is flushed by one final connect.
    t._on_connect(fake, None, {}, 0)
    assert t._pending == []

    per: dict[str, list] = {}
    for topic, payload, _qos in fake.published:
        if topic.startswith("composeio/global/stress/"):
            per.setdefault(topic, []).append(
                BusMessage.from_json(json.loads(payload)).value)
    for k in range(n_threads):
        got = per.get(f"composeio/global/stress/t{k}", [])
        assert sorted(got) == list(range(per_thread)), (
            f"thread {k}: lost or duplicated "
            f"{sorted(set(range(per_thread)) ^ set(got))[:10]} "
            f"(n={len(got)})")
        assert got == list(range(per_thread)), (
            f"thread {k}: order broken at index "
            f"{next(i for i, v in enumerate(got) if v != i)}")


# ── overflow is VISIBLE on a transport that never connects ──────────────────

def _overflow_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.WARNING
            and "pre-connect queue" in r.getMessage()]


def test_overflow_before_start_is_counted_in_health_and_warned_once(caplog):
    """A transport that is never started (or never connects) must not drop
    past the cap silently. health() carries the count, and the FIRST drop is
    one WARNING, not one per message."""
    caplog.set_level(logging.INFO, logger="composeio.transport_mqtt")
    t = MQTTTransport(broker="test.local", client_id="t")
    for i in range(t.PENDING_MAX):
        t.send(_msg("self/n", i))
    assert _overflow_warnings(caplog) == []          # at the cap, nothing dropped
    assert t.health()["pending_dropped"] == 0

    t.send(_msg("self/n", t.PENDING_MAX))            # the first drop
    assert len(_overflow_warnings(caplog)) == 1

    for i in range(t.PENDING_MAX + 1, t.PENDING_MAX + 50):
        t.send(_msg("self/n", i))                    # 114 sends in total
    h = t.health()
    assert h["started"] is False
    assert h["pending"] == t.PENDING_MAX
    assert h["pending_dropped"] == 50
    assert len(_overflow_warnings(caplog)) == 1      # thinned, not per message


def test_overflow_repeats_are_summarised_on_the_link_throttle(monkeypatch, caplog):
    """Repeats go through LinkHealth's throttle: one summary line once
    SUMMARY_EVERY_S has passed, carrying the count."""
    import composeio.transport_mqtt as tm
    now = [1000.0]
    monkeypatch.setattr(tm, "_monotonic", lambda: now[0])
    caplog.set_level(logging.INFO, logger="composeio.transport_mqtt")
    t = MQTTTransport(broker="test.local", client_id="t")
    for i in range(t.PENDING_MAX + 10):
        t.send(_msg("self/n", i))
    assert len(_overflow_warnings(caplog)) == 1
    now[0] += tm.LinkHealth.SUMMARY_EVERY_S + 1
    t.send(_msg("self/n", -1))
    w = _overflow_warnings(caplog)
    assert len(w) == 2
    assert "11 dropped since the last flush" in w[1].getMessage()


def test_dropped_count_resets_on_flush_and_bus_client_carries_the_same_key():
    from composeio import bus_client as bc
    t, fake = _wired()
    for i in range(t.PENDING_MAX + 3):
        t.send(_msg("self/n", i))
    assert t.health()["pending_dropped"] == 3
    t._on_connect(fake, None, {}, 0)
    assert t.health()["pending_dropped"] == 0 and t.health()["pending"] == 0
    other = bc.health("mqtt://never-started.invalid:1883")
    assert set(other) == set(t.health())
    assert other["pending_dropped"] is None


# ── fleet_roster: its "last published" becomes true with the hold ───────────

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_fleet_roster_first_publish_reaches_the_wire_after_connect(monkeypatch):
    """`fleet_roster` publishes on CONTENT CHANGE only and records `last` as
    published even when the transport could not send it. Before the hold its
    forced first publish (made before the console starts the bus) was dropped,
    and the roster stayed off the wire until the fleet changed. With the hold
    `last` is true: the roster reaches the wire on the first connect, with NO
    content change in between."""
    import composeio.transport_mqtt as tm
    from composeio.bus import ComposeIOBus

    pub = os.path.join(_REPO, "compose_projects", "intelligence", "publisher")
    if pub not in sys.path:
        monkeypatch.syspath_prepend(pub)
    import fleet_roster                                        # type: ignore

    made: list[_FakePahoModuleClient] = []

    def _fake_new_client(**kw):
        c = _FakePahoModuleClient()
        made.append(c)
        return c

    monkeypatch.setattr(tm, "new_paho_client", _fake_new_client)
    monkeypatch.setattr(tm, "connect_with_backoff", lambda *a, **kw: None)

    t = MQTTTransport(broker="test.local", client_id="console")
    bus = ComposeIOBus(bus_id="console", global_transport=t)
    stop = threading.Event()
    th = threading.Thread(
        target=fleet_roster.run, args=("",),
        kwargs={"bus": bus, "stop": stop, "publish_s": 60.0,
                "hosted_ids": ["architect"]},
        daemon=True)
    th.start()                         # the console starts sources first ...
    deadline = time.time() + 5
    while bus.get_state(fleet_roster.ROSTER_NS) is None and time.time() < deadline:
        time.sleep(0.01)
    try:
        assert bus.get_state(fleet_roster.ROSTER_NS) == []    # published locally
        assert t.health()["started"] is False
        assert t.health()["pending"] >= 1                     # held, not dropped

        bus.start(announce_time=False)  # ... then app.run() starts the bus
        assert len(made) == 1
        t._on_connect(made[0], None, {}, 0)                   # the CONNACK

        roster = [BusMessage.from_json(json.loads(p[1])).value
                  for p in made[0].published
                  if p[0] == "composeio/global/fleet/roster"]
        assert roster == [[]], roster   # on the wire once, unchanged content
    finally:
        stop.set()
        th.join(5)
    assert not th.is_alive()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
