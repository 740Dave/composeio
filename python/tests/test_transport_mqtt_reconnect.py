"""A failed FIRST connect must be retried, and the outage must be VISIBLE.

THE DEFECT. `MQTTTransport.start()` called paho's blocking `connect()` once and
only then `loop_start()`. When the broker was unreachable at start, `connect()`
raised, the transport logged "MQTT connection failed" and never started paho's
network thread, so nothing ever retried. The process stayed up (HTTP faces
answered, widgets rendered), messages queued against a connection that would
never come, and a container restart policy never fired. Measured in the field:
a ComposeServer container ran 2+ days with no MQTT socket at all after its
broker host went away.

THE FIX. `connect_async()` + `loop_start()`: paho's own loop thread makes the
first attempt AND every later one, with the bounded backoff set by
`reconnect_delay_set`. A first connect that fails and a drop mid-run are now the
same case.

HOW THIS IS TESTED. The repo's transport tests fake paho at the boundary (no
broker fixture exists), so most tests here do too. The fake models ONE paho
fact: once `loop_start()` runs after `connect_async()`, the loop thread keeps
attempting the connection until it succeeds, and reconnects after a drop. That
fact is about paho, not about our code, so it is NOT taken on trust:
`test_real_paho_retries_a_failed_first_connect` drives the REAL paho client
against a closed local port (no broker) and counts the retries, with the old
blocking `connect()` as its negative control.
"""
from __future__ import annotations

import json
import socket
import time

import pytest

paho = pytest.importorskip("paho.mqtt.client")

from composeio.transport import BusMessage, MessageType      # noqa: E402
from composeio.transport_mqtt import MQTTTransport           # noqa: E402


# --------------------------------------------------------------------------- #
# The boundary fake: a broker that can be up or down, and a paho-shaped client
# --------------------------------------------------------------------------- #

class FakeBroker:
    def __init__(self, up: bool):
        self.up = up
        self.received: list[tuple[str, dict]] = []   # (topic, decoded payload)


class FakePahoClient:
    """paho's client surface, against a FakeBroker.

    `pump()` stands in for one turn of paho's loop thread. It does something
    only if `loop_start()` was called and a connection was requested; that is
    exactly why the old code stays dead (it raised before `loop_start()`).
    """

    broker: FakeBroker = None            # set per test by the fixture
    instances: list["FakePahoClient"] = []

    def __init__(self, client_id="", protocol=None, **kw):
        self.on_connect = None
        self.on_connect_fail = None
        self.on_message = None
        self.on_disconnect = None
        self.loop_running = False
        self.wants_connection = False
        self.tcp_up = False
        self.connect_kwargs = None
        self.reconnect_delay = None
        self.sessions: list[list[str]] = []   # topics subscribed, per connection
        FakePahoClient.instances.append(self)

    # -- configuration ---------------------------------------------------- #
    def username_pw_set(self, u, p=None):
        pass

    def will_set(self, *a, **kw):
        pass

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delay = (min_delay, max_delay)

    # -- connection ------------------------------------------------------- #
    def connect(self, host, port=1883, keepalive=60, **kw):
        """paho's BLOCKING connect: a refused TCP connect raises here."""
        if not self.broker.up:
            raise ConnectionRefusedError(61, "Connection refused")
        self.connect_kwargs = dict(host=host, port=port, keepalive=keepalive, **kw)
        self.wants_connection = True

    def connect_async(self, host, port=1883, keepalive=60, **kw):
        """Stores the target only; the loop thread does the connecting."""
        self.connect_kwargs = dict(host=host, port=port, keepalive=keepalive, **kw)
        self.wants_connection = True

    def loop_start(self):
        self.loop_running = True

    def loop_stop(self):
        self.loop_running = False

    def disconnect(self):
        self.wants_connection = False
        self.tcp_up = False

    def pump(self):
        """One turn of paho's network loop."""
        if not (self.loop_running and self.wants_connection) or self.tcp_up:
            return
        if self.broker.up:
            self.tcp_up = True
            self.sessions.append([])
            self.on_connect(self, None, {}, 0, None)
        elif self.on_connect_fail is not None:
            self.on_connect_fail(self, None)

    def drop(self):
        """The broker goes away under an established connection."""
        self.tcp_up = False
        self.on_disconnect(self, None, 7, None)

    # -- traffic ---------------------------------------------------------- #
    def subscribe(self, topic, qos=0, options=None, properties=None):
        self.sessions[-1].append(topic)

    def message_callback_add(self, sub, cb):
        pass

    def publish(self, topic, payload=None, qos=0, retain=False, properties=None):
        if self.tcp_up:
            self.broker.received.append((topic, json.loads(payload)))


@pytest.fixture
def broker_down(monkeypatch):
    FakePahoClient.instances = []
    FakePahoClient.broker = FakeBroker(up=False)
    monkeypatch.setattr(paho, "Client", FakePahoClient)
    return FakePahoClient.broker


def _msg(ns, value):
    return BusMessage(namespace=ns, value=value, type=MessageType.STATE,
                      scope="global")


def _pump(n=3):
    for _ in range(n):
        FakePahoClient.instances[-1].pump()


# --------------------------------------------------------------------------- #
# 1. Broker DOWN at start(), UP later: connects with no restart
# --------------------------------------------------------------------------- #

def test_broker_down_at_start_then_up_connects_without_a_restart(broker_down):
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    t.subscribe("throttle/a", lambda m: None)
    t.send(_msg("self/status", "sent-while-down"))

    _pump()                               # broker still down: retries fail
    assert t.is_connected is False
    assert broker_down.received == []

    broker_down.up = True
    _pump()                               # the SAME process, no restart
    assert t.is_connected is True

    # The design QUEUES pre-connect sends ([DEC-CONSOLE-MQTT-STARTUP-RACE],
    # bounded at PENDING_MAX): the message sent while down reaches the broker.
    assert [(tp, p["val"]) for tp, p in broker_down.received] == [
        ("composeio/global/self/status", "sent-while-down")]
    # and the subscription made while down is live on the first session
    assert "composeio/+/throttle/a" in FakePahoClient.instances[-1].sessions[-1]


def test_start_uses_connect_async_with_a_bounded_backoff(broker_down):
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    c = FakePahoClient.instances[-1]
    assert c.loop_running, "loop_start() must run even when the broker is down"
    lo, hi = c.reconnect_delay
    assert (lo, hi) == (MQTTTransport.RECONNECT_MIN_DELAY_S,
                        MQTTTransport.RECONNECT_MAX_DELAY_S)
    assert 0 < lo <= hi <= 60
    # the session semantics survive the switch to connect_async
    assert c.connect_kwargs["clean_start"] is True
    assert c.connect_kwargs["properties"].SessionExpiryInterval == 0


# --------------------------------------------------------------------------- #
# 2. Broker drops mid-run and comes back: reconnects AND re-subscribes
# --------------------------------------------------------------------------- #

def test_mid_run_drop_reconnects_and_resubscribes(broker_down):
    broker_down.up = True
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.subscribe("throttle/a", lambda m: None)
    t.subscribe_pattern("_device/+/health")
    t.start()
    _pump()
    c = FakePahoClient.instances[-1]
    assert t.is_connected and len(c.sessions) == 1

    broker_down.up = False
    c.drop()
    t.send(_msg("self/status", "sent-during-outage"))
    _pump()
    assert t.is_connected is False

    broker_down.up = True
    _pump()
    assert t.is_connected is True
    assert len(c.sessions) == 2, "a second connection must have been made"
    resub = c.sessions[-1]
    assert "composeio/+/throttle/a" in resub
    assert t._pattern_topic("_device/+/health") in resub
    assert "composeio/+/_bus/#" in resub
    assert [p["val"] for _, p in broker_down.received] == ["sent-during-outage"]


# --------------------------------------------------------------------------- #
# 3. The health signal: false while down, true after connect, false on drop
# --------------------------------------------------------------------------- #

def test_health_signal_tracks_the_link(broker_down):
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    _pump()
    h = t.health()
    assert h["connected"] is False and t.is_connected is False
    assert h["connect_failures"] >= 1          # the outage is COUNTED, not silent
    assert h["broker"] == "broker.invalid:1883"

    broker_down.up = True
    _pump()
    h = t.health()
    assert h["connected"] is True and t.is_connected is True
    assert h["connect_failures"] == 0          # reset by a successful CONNACK

    FakePahoClient.instances[-1].drop()
    assert t.health()["connected"] is False


def test_health_before_start_says_not_started(broker_down):
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    h = t.health()
    assert h["connected"] is False and h["started"] is False


# --------------------------------------------------------------------------- #
# 4. The paho fact the fake models, checked against REAL paho (no broker)
# --------------------------------------------------------------------------- #

def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_real_paho_retries_a_failed_first_connect(monkeypatch):
    """Real paho, real sockets, a closed loopback port: the transport must keep
    attempting. Old code: one refused connect, `connect_failures` never moves."""
    monkeypatch.setattr(MQTTTransport, "RECONNECT_MIN_DELAY_S", 0.05,
                        raising=False)
    monkeypatch.setattr(MQTTTransport, "RECONNECT_MAX_DELAY_S", 0.1,
                        raising=False)
    t = MQTTTransport(broker="127.0.0.1", port=_closed_port(), client_id="probe")

    def failures() -> int:
        # getattr so the OLD code fails on the BEHAVIOUR (no retries), not on
        # a missing method
        h = getattr(t, "health", None)
        return h()["connect_failures"] if h else 0

    t.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and failures() < 3:
            time.sleep(0.05)
        assert failures() >= 3, \
            "paho's loop thread did not retry the failed first connect"
        assert t.is_connected is False
    finally:
        t.stop()


def test_negative_control_blocking_connect_does_not_retry():
    """The control for the test above: paho's BLOCKING connect() against the
    same kind of closed port raises once and nothing retries. If this ever
    stopped raising, the retry test above would be measuring nothing."""
    from composeio.transport_mqtt import new_paho_client
    c = new_paho_client(mqtt=paho, client_id="ctl", protocol=paho.MQTTv5)
    with pytest.raises(OSError):
        c.connect("127.0.0.1", _closed_port(), keepalive=60)


# --------------------------------------------------------------------------- #
# N2: a malformed host must not silently kill retrying; health must say why
# --------------------------------------------------------------------------- #

def test_a_malformed_host_is_reported_not_silently_fatal(broker_down, caplog):
    """`bad..host` raises UnicodeError inside paho's loop thread (idna), which
    paho 1.6.1 does not catch: the thread dies, nothing is counted, nothing is
    said. It cannot become valid without a config change, so ONE loud error
    and an honest health() is the right outcome, not a retry loop."""
    import logging
    caplog.set_level(logging.ERROR, logger="composeio.transport.mqtt")
    t = MQTTTransport(broker="bad..host", client_id="probe")
    t.start()
    c = FakePahoClient.instances[-1]
    assert not c.loop_running, "no loop thread for a host that cannot resolve"
    h = t.health()
    assert h["connected"] is False and t.is_connected is False
    assert h["connect_failures"] == 1
    assert "invalid broker host" in (h["last_error"] or "")
    assert sum("invalid broker host" in r.getMessage()
               for r in caplog.records) == 1


def test_a_malformed_host_with_real_paho_shows_in_health():
    t = MQTTTransport(broker="bad..host", client_id="probe")
    t.start()
    try:
        time.sleep(0.3)
        h = t.health()
        assert h["connected"] is False
        assert h["connect_failures"] >= 1 and h["last_error"], h
    finally:
        t.stop()


# --------------------------------------------------------------------------- #
# N5: repeated identical connect errors are thinned to a bounded log
# --------------------------------------------------------------------------- #

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _fail_lines(caplog):
    return [r for r in caplog.records
            if r.levelno >= 30 and "MQTT" in r.getMessage()
            and ("fail" in r.getMessage() or "REFUSED" in r.getMessage())]


@pytest.mark.parametrize("kind", ["connect_fail", "connack_refused"])
def test_1000_identical_failures_inside_10_minutes_log_once(broker_down, caplog,
                                                            monkeypatch, kind):
    import logging
    import composeio.transport_mqtt as tm
    clock = _Clock()
    monkeypatch.setattr(tm, "_monotonic", clock, raising=False)
    caplog.set_level(logging.INFO, logger="composeio.transport.mqtt")
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    c = FakePahoClient.instances[-1]
    for _ in range(1000):
        clock.now += 0.5                      # 500 s total: under 10 minutes
        if kind == "connect_fail":
            t._on_connect_fail(c, None)
        else:
            t._on_connect(c, None, {}, 135, None)   # v5 "Not authorized"
    assert len(_fail_lines(caplog)) == 1
    assert t.health()["connect_failures"] == 1000   # the COUNT is never thinned


@pytest.mark.parametrize("kind", ["connect_fail", "connack_refused"])
def test_1000_failures_over_8_hours_log_a_bounded_summary(broker_down, caplog,
                                                          monkeypatch, kind):
    import logging
    import composeio.transport_mqtt as tm
    clock = _Clock()
    monkeypatch.setattr(tm, "_monotonic", clock, raising=False)
    caplog.set_level(logging.INFO, logger="composeio.transport.mqtt")
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    c = FakePahoClient.instances[-1]
    for _ in range(1000):
        clock.now += 30                       # the 30 s cap: 30000 s = 500 min
        if kind == "connect_fail":
            t._on_connect_fail(c, None)
        else:
            t._on_connect(c, None, {}, 135, None)
    lines = _fail_lines(caplog)
    # first line + one summary per 10 minutes: 1 + 50
    assert len(lines) <= 1 + 30000 // 600
    assert len(lines) >= 30000 // 600         # and it is not silent
    assert any("1000" in r.getMessage() or "attempts" in r.getMessage()
               for r in lines[1:])


def test_a_different_error_is_logged_at_once_and_success_resets(broker_down,
                                                                caplog,
                                                                monkeypatch):
    import logging
    import composeio.transport_mqtt as tm
    clock = _Clock()
    monkeypatch.setattr(tm, "_monotonic", clock, raising=False)
    caplog.set_level(logging.INFO, logger="composeio.transport.mqtt")
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    c = FakePahoClient.instances[-1]
    for _ in range(5):
        t._on_connect_fail(c, None)
    t._on_connect(c, None, {}, 135, None)     # a NEW kind of failure: logged
    assert len(_fail_lines(caplog)) == 2
    c.sessions.append([])                     # the fake's per-connection log
    t._on_connect(c, None, {}, 0, None)       # success resets the throttle
    t._on_disconnect(c, None, 7, None)
    t._on_connect_fail(c, None)               # first failure of a new outage
    assert len(_fail_lines(caplog)) == 3


# --------------------------------------------------------------------------- #
# N5 (round 2): a refused login is a CONNACK refusal FOLLOWED BY a disconnect
# --------------------------------------------------------------------------- #

def _refused_login_storm(t, c, clock, n, step):
    """What paho 1.6.1 does against a broker that refuses the login: the
    refused CONNACK reaches on_connect, then on_disconnect fires for the
    dropped socket. Measured against an auth-required mosquitto."""
    for _ in range(n):
        clock.now += step
        t._on_connect(c, None, {}, 135, None)     # v5 "Not authorized"
        t._on_disconnect(c, None, 2, None)


def _mqtt_lines(caplog):
    return [r for r in caplog.records
            if r.levelno >= 30 and r.name == "composeio.transport.mqtt"]


def test_a_refused_login_storm_inside_10_minutes_logs_one_line_per_kind(
        broker_down, caplog, monkeypatch):
    import logging
    import composeio.transport_mqtt as tm
    clock = _Clock()
    monkeypatch.setattr(tm, "_monotonic", clock)
    caplog.set_level(logging.INFO, logger="composeio.transport.mqtt")
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    _refused_login_storm(t, FakePahoClient.instances[-1], clock, 1000, 0.5)
    lines = _mqtt_lines(caplog)
    # ONE refusal line and ONE disconnect line, not 1000 + 1000
    assert len(lines) == 2, [r.getMessage() for r in lines]
    assert any("REFUSED" in r.getMessage() for r in lines)
    assert any("disconnect" in r.getMessage() for r in lines)
    assert t.health()["connect_failures"] == 1000   # counted once per attempt


def test_a_refused_login_storm_over_a_day_is_bounded(broker_down, caplog,
                                                    monkeypatch):
    import logging
    import composeio.transport_mqtt as tm
    clock = _Clock()
    monkeypatch.setattr(tm, "_monotonic", clock)
    caplog.set_level(logging.INFO, logger="composeio.transport.mqtt")
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    # 2880 attempts at the 30 s cap = 24 h
    _refused_login_storm(t, FakePahoClient.instances[-1], clock, 2880, 30)
    lines = _mqtt_lines(caplog)
    per_kind_max = 1 + (2880 * 30) // 600            # first + one per 10 min
    assert len(lines) <= 2 * per_kind_max            # 290, not 5760
    assert len(lines) >= 2 * (per_kind_max - 1)      # and not silent


def test_a_normal_drop_after_a_good_connection_is_still_logged_at_once(
        broker_down, caplog, monkeypatch):
    import logging
    import composeio.transport_mqtt as tm
    clock = _Clock()
    monkeypatch.setattr(tm, "_monotonic", clock)
    caplog.set_level(logging.INFO, logger="composeio.transport.mqtt")
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    c = FakePahoClient.instances[-1]
    for _ in range(3):                    # connect, drop; connect, drop; ...
        c.sessions.append([])
        t._on_connect(c, None, {}, 0, None)
        clock.now += 1
        t._on_disconnect(c, None, 7, None)
    drops = [r for r in _mqtt_lines(caplog) if "disconnect" in r.getMessage()]
    assert len(drops) == 3                # a success re-arms the first line
