#!/usr/bin/env python3
"""bus_client — ONE shared paho connection per broker, per process.

Phase 2a step 2a of the bus-connection collapse (2026-08-08).

THE MEASUREMENT THAT MOTIVATED THIS. The live console process held **20 TCP
connections to the broker and 45 threads**, because 17 of its 18 in-process data
sources each constructed their own `mqtt.Client()` — and every paho
`loop_start()` spawns its own network thread. All of that traffic is intra-app:
the widgets subscribing it live in the SAME process.

WHY IT LOOKED LIKE THAT, AND WHY THE REASON EXPIRED. Per
[DEC-BUS-ENVELOPE-RAW-PAHO] (2026-07-19), a per-source client with a unique
client-id was a deliberate fix for an rc=7 client-id collision — back when these
were separate OS PROCESSES. [DEC-INPROCESS-SOURCES] (2026-07-22) made them daemon
THREADS inside one console process. The reason expired at that moment; the
pattern did not, and reached 17 sites. That is the definition of a vestige.

Real cost, beyond tidiness: a broker blip triggers 20 independent reconnect
storms from a single process.

PUBLISH-ONLY, DELIBERATELY — read this before reusing it
--------------------------------------------------------
This client is shared, so it has exactly ONE `on_message`. A subscriber that
sets its own callback on a shared client silently steals delivery from every
other subscriber — last writer wins, and the losers go quiet with no error. That
is the precise silent-failure shape this whole phase exists to remove, so rather
than leave it as a documented footgun, `acquire()` REFUSES to hand back a shared
client to a caller that declares it will subscribe.

The console's 9 publish-only sources (self, transcript, meta, memory,
agents_skills, deliverable_publisher, deliverable_watcher, defect_metrics,
lts_engine_status) collapse onto one connection here with no coordination at all.

The 10 SUBSCRIBING sources (channel_bridge, config_persister, deliverable_monitor,
deliverable_opener, app_launcher, away_watch, decisions_curator, and the three
viewer launchers) still need a topic-dispatch ROUTER — one `on_message` fanning
out to registered handlers by topic filter. That is step 2b, deliberately NOT
smuggled in here: it changes delivery semantics for every consumer and deserves
its own commit and its own rollback point.

CONNECTING — THE CALLER-VISIBLE CONTRACT (w-26, 2026-11-27)
----------------------------------------------------------
`acquire()` and `subscribe()` used to call paho's BLOCKING `connect()`: a broker
that was down at start raised `ConnectionRefusedError` (or another `OSError`) to
the caller, and nothing ever retried. They now start the client through
`transport_mqtt.connect_with_backoff`, the same backoff + `connect_async` +
`loop_start` sequence `MQTTTransport.start()` uses since PR #16. So:

  * they return a client whose connection is IN PROGRESS, not established, and
    they do not raise for an unreachable broker; paho's loop thread retries with
    `MQTTTransport`'s bounded backoff, at start and after any drop;
  * a filter registered with `subscribe()` is (re)issued on EVERY CONNACK by the
    router, so a subscription made while the broker is down, or lost in a drop,
    becomes live when the broker is reachable;
  * a `publish()` before the connection is up follows paho: QoS 0 is DROPPED
    (rc MQTT_ERR_NO_CONN), QoS 1/2 is held by paho and sent on connect.

AN OUTAGE IS LOUD, NOT A QUIET RETRY. Retrying is not hiding: a broker that is
unreachable or refuses us is a misconfiguration somebody must see (the old
broker was taken offline ON PURPOSE so that anything not yet repointed fails
loudly, DEC-HOP-OLD-BROKER-OFFLINE-ON-PURPOSE-2026-11-27). So, per broker:

  * the FIRST failed connect logs at ERROR on the `composeio.bus_client`
    logger and names the broker host:port exactly as configured;
  * repeats log at WARNING with the attempt count, thinned: on the 2nd and
    4th consecutive failure, then at most once per 600 s, the SAME summary
    interval MQTTTransport uses (one throttle: `LinkHealth.SUMMARY_EVERY_S`);
  * a refused CONNACK logs at ERROR with its reason, thinned the same way, and
    the disconnect paho reports after each refusal adds NO line of its own,
    so each report is one line, not two;
  * the first successful connect after failures logs the recovery once, at
    INFO, with the number of failed attempts; a new outage logs its ERROR again;
  * `health(mqtt_url)` returns exactly `MQTTTransport.health()`'s keys, built
    by the same `transport_mqtt.LinkHealth.health()`: `connected`, `started`,
    `broker`, `connect_failures` (consecutive, since the last success),
    `last_error`, `last_success` (epoch seconds, None if never) and
    `pending` and `pending_dropped`, both None here: bus_client keeps no
    queue of its own, and the QoS 1/2 messages paho holds are not counted by
    paho's API.

Clients are built with `paho_compat.new_paho_client` (re-exported by
`transport_mqtt`), so paho 1.x and 2.x both construct (2.x gets
CallbackAPIVersion.VERSION1).
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Optional, Tuple

logger = logging.getLogger("composeio.bus_client")

#: extra early WARNINGs for repeated connect failures; after them the
#: summary interval is LinkHealth.SUMMARY_EVERY_S (see the module docstring).
REPEAT_AT_ATTEMPTS = (2, 4)

# (host, port) -> the one live client for this process.
_SHARED: dict[Tuple[str, int], object] = {}
_LOCK = threading.RLock()
_REFCOUNT: dict[Tuple[str, int], int] = {}
# (host, port) -> the LinkHealth of that shared connection.
_HEALTH: dict[Tuple[str, int], object] = {}


def parse_mqtt_url(url: str) -> Tuple[str, int]:
    """'mqtt://host:port' -> (host, port). Bare 'host' -> (host, 1883).

    Single-sourced here because every data source had its own copy of this
    four-line parser — a smaller instance of the same fork this module closes.
    """
    s = url or ""
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


def acquire(mqtt_url: str, *, label: str, will_subscribe: bool = False,
            keepalive: int = 60, client_factory=None):
    """The shared PUBLISH-ONLY client for `mqtt_url`, looping and connecting.

    Does not raise when the broker is unreachable: the connection is made, and
    re-made after a drop, by paho's loop thread with backoff (see the module
    docstring, "CONNECTING"). The outage is not silent: the first failure logs
    at ERROR naming host:port, and `health(mqtt_url)` counts it. A QoS-0
    publish before the connection is up is dropped.

    `label` identifies the caller in logs and in the shared client-id — it is
    NOT a separate connection; the first caller wins the id and the rest join it.

    `will_subscribe=True` is a REFUSAL, not a mode: it raises, because a shared
    client has one `on_message` and a second subscriber would silently steal
    delivery from the first. Subscribers keep their own client until step 2b's
    router exists. Refusing loudly beats a footgun that goes quiet.

    `client_factory` is injectable for tests (default: real paho).
    """
    if will_subscribe:
        raise ValueError(
            f"bus_client.acquire({label!r}, will_subscribe=True): the shared client is "
            "PUBLISH-ONLY. It has a single on_message, so a second subscriber would "
            "silently steal delivery from the first. Keep your own client until the "
            "step-2b topic-dispatch router lands.")

    host, port = parse_mqtt_url(mqtt_url)
    key = (host, port)
    with _LOCK:
        existing = _SHARED.get(key)
        if existing is not None:
            _REFCOUNT[key] = _REFCOUNT.get(key, 0) + 1
            sys.stderr.write(
                f"[bus_client] {label} joined the shared connection to "
                f"{host}:{port} (now {_REFCOUNT[key]} publishers, 1 socket)\n")
            return existing

        if client_factory is not None:
            client = client_factory()
        else:
            # A STABLE shared client-id: one connection means one id, so the
            # rc=7 collision [DEC-BUS-ENVELOPE-RAW-PAHO] worked around is now
            # structurally impossible rather than merely avoided.
            client = _new_client(f"composeio-shared-pub-{host}")
        _start(client, host, port, keepalive)
        _SHARED[key] = client
        _REFCOUNT[key] = 1
        sys.stderr.write(
            f"[bus_client] {label} opened THE shared publish connection to "
            f"{host}:{port} (1 socket for all publish-only sources)\n")
        return client


def _new_client(client_id: str):
    """A real v5 paho client, built at the paho seam (paho 1.x and 2.x)."""
    import paho.mqtt.client as mqtt
    from composeio.transport_mqtt import new_paho_client
    return new_paho_client(mqtt=mqtt, client_id=client_id, protocol=mqtt.MQTTv5)


def _start(client, host: str, port: int, keepalive: int) -> None:
    """Wire the health callbacks, then connect with the transport's backoff.

    Never raises for a down broker; the outage is logged and counted instead.
    Imported lazily: `transport_mqtt` imports `topic_matches` from this module
    at import time, so a top-level import here would be circular.
    """
    from composeio.transport_mqtt import connect_with_backoff
    _wire_health(client, host, port)
    connect_with_backoff(client, host, port, keepalive=keepalive)


def _wire_health(client, host: str, port: int) -> None:
    """Install on_connect / on_connect_fail / on_disconnect for (host, port).

    `on_connect` records the success (or the refusal) and then has the
    router, if one exists for this broker by then, re-issue every filter.
    """
    from composeio.transport_mqtt import MQTTTransport, _connack_reason

    key = (host, port)
    target = f"{host}:{port}"
    link = _new_link(target)
    _HEALTH[key] = link
    backoff = (f"{MQTTTransport.RECONNECT_MIN_DELAY_S}-"
               f"{MQTTTransport.RECONNECT_MAX_DELAY_S}s")

    def on_connect_fail(client, userdata):
        link.record_failure(
            "connection attempt failed",
            f"MQTT connect to {target} FAILED (bus_client shared connection); "
            f"is this the broker this process should use? Retrying with "
            f"backoff {backoff}",
            f"MQTT connect to {target} still failing (bus_client)")

    def on_connect(client, userdata, flags, rc, properties=None):
        code = getattr(rc, "value", rc)
        if code != 0:
            reason = _connack_reason(rc)
            link.record_failure(
                f"CONNACK refused: {reason}",
                f"MQTT connection to {target} REFUSED (bus_client): {reason}",
                f"MQTT connection to {target} still refused (bus_client)")
            return
        failed = link.record_success()
        if failed:
            logger.info(f"MQTT connection to {target} RECOVERED after "
                        f"{failed} failed attempt(s) (bus_client)")
        router = _ROUTERS.get(key)
        if router is not None:
            router.resubscribe(client)

    def on_disconnect(client, userdata, rc, properties=None):
        link.connected = False
        # After a refused CONNACK paho drops the socket and reports it here.
        # The refusal is already logged (and thinned) by on_connect, so a line
        # here would double every report (candidate review note 2).
        if (link.last_error or "").startswith("CONNACK refused"):
            return
        if rc != 0:
            link.throttled_log(
                logging.WARNING, f"disconnect rc={rc}",
                f"MQTT unexpected disconnect from {target} (rc={rc}, "
                f"bus_client), will auto-reconnect",
                f"MQTT disconnects from {target} (rc={rc}) repeating")

    client.on_connect = on_connect
    client.on_connect_fail = on_connect_fail
    client.on_disconnect = on_disconnect


def _new_link(target: str):
    from composeio.transport_mqtt import LinkHealth
    return LinkHealth(target, log=logger, repeat_level=logging.WARNING,
                      repeat_at_attempts=REPEAT_AT_ATTEMPTS)


def health(mqtt_url: str) -> dict:
    """The shared connection's link state, with exactly `MQTTTransport.health()`'s keys.

    Built by `LinkHealth.health()`, the one builder both use. A broker this
    process never started a connection to reads from a fresh LinkHealth:
    `started: False, connected: False`. `pending` and `pending_dropped` are
    None (see the module docstring).
    """
    host, port = parse_mqtt_url(mqtt_url)
    with _LOCK:
        link = _HEALTH.get((host, port))
        started = (host, port) in _SHARED
    if link is None:
        link = _new_link(f"{host}:{port}")
    return link.health(started=started, pending=None, pending_dropped=None)


# --------------------------------------------------------------------------- #
# Step 2b — the subscriber ROUTER
# --------------------------------------------------------------------------- #
# A shared client has exactly ONE `on_message`. The router IS that one callback:
# subscribers register (topic_filter, handler) instead of assigning the callback,
# and the router fans each inbound message out to every filter that matches.
#
# Delivery-loss is the risk this must not introduce, so two deliberate choices:
#   * a handler that RAISES is isolated — logged, and the remaining handlers for
#     that message still run. One bad consumer cannot silence its siblings.
#   * matching is by MQTT filter semantics, not string equality: `+` matches
#     exactly one level, `#` matches the rest and must be last. Getting this
#     subtly wrong would drop messages SILENTLY, which is the exact failure class
#     this whole phase exists to remove — hence the matcher is tested directly.

def topic_matches(filt: str, topic: str) -> bool:
    """MQTT topic-filter match. `+` = exactly one level, `#` = the rest (last only).

    Implemented and tested explicitly rather than trusting string comparison: a
    near-miss here drops messages with no error at all.
    """
    if filt == topic:
        return True
    f, t = filt.split("/"), topic.split("/")
    for i, seg in enumerate(f):
        if seg == "#":
            # `#` matches the remainder, including zero levels — but only as the
            # final segment. It also must not match a leading $-topic.
            return i == len(f) - 1
        if i >= len(t):
            return False
        if seg != "+" and seg != t[i]:
            return False
    return len(f) == len(t)


class _Router:
    """The single `on_message` for a shared client; fans out by topic filter."""

    def __init__(self, label: str):
        self.label = label
        self._routes: list[tuple[str, object]] = []
        #: filter -> the highest QoS any subscriber asked for; what is
        #: (re)issued to the broker on every CONNACK.
        self._filters: dict[str, int] = {}
        self._lock = threading.RLock()

    def add(self, topic_filter: str, handler, qos: int = 0) -> None:
        with self._lock:
            self._routes.append((topic_filter, handler))
            self._filters[topic_filter] = max(qos, self._filters.get(topic_filter, 0))

    def resubscribe(self, client) -> None:
        """Issue every registered filter; called on each successful CONNACK.

        The connection is made by paho's loop thread (connect_async), so a
        `subscribe()` that ran before it found no socket and sent nothing, and
        a reconnect starts a fresh session (no session expiry is requested).
        Either way the broker holds none of our filters until this runs.
        """
        with self._lock:
            filters = dict(self._filters)
        for filt, qos in filters.items():
            client.subscribe(filt, qos)

    def route_count(self) -> int:
        with self._lock:
            return len(self._routes)

    def dispatch(self, client, userdata, msg) -> int:
        """Deliver `msg` to every matching handler. Returns the delivery count.

        An exception in one handler NEVER stops the others — a subscriber that
        throws must not silence its siblings on a shared connection.
        """
        with self._lock:
            routes = list(self._routes)
        delivered = 0
        for filt, handler in routes:
            if not topic_matches(filt, msg.topic):
                continue
            try:
                handler(client, userdata, msg)
                delivered += 1
            except Exception as exc:              # noqa: BLE001 — isolation boundary
                sys.stderr.write(
                    f"[bus_client] handler for {filt!r} raised on {msg.topic!r} "
                    f"(siblings unaffected): {exc!r}\n")
        return delivered


_ROUTERS: dict[Tuple[str, int], _Router] = {}


def subscribe(mqtt_url: str, topic_filter: str, handler, *, label: str,
              qos: int = 0, keepalive: int = 60, client_factory=None):
    """Register `handler` for `topic_filter` on the shared SUBSCRIBER client.

    Returns the shared client, looping and connecting (it does not raise for a
    down broker), so the caller can also publish on it. The filter is
    re-issued on every CONNACK, so it survives a broker that is down at start
    and any later drop. bus_client owns `on_connect`, `on_connect_fail`,
    `on_disconnect` and `on_message`; do not reassign any of them.

    IMPORTANT — the caller must NOT call `loop_forever()` on the returned client.
    The shared client already runs `loop_start()`, and a second loop would
    conflict. A source whose `run()` used `loop_forever()` to stay alive must
    park on its own stop-Event instead: under `DataSourceSupervisor`, a clean
    RETURN is read as "yielded my singleton" and triggers a reclaim RETRY loop,
    so returning early is not harmless.
    """
    host, port = parse_mqtt_url(mqtt_url)
    key = (host, port)
    with _LOCK:
        router = _ROUTERS.get(key)
        first = router is None
        if first:
            router = _Router(label)
            _ROUTERS[key] = router
        router.add(topic_filter, handler, qos)

        client = _SHARED.get(key)
        if client is None:
            if client_factory is not None:
                client = client_factory()
            else:
                client = _new_client(f"composeio-shared-{host}")
            client.on_message = router.dispatch
            _start(client, host, port, keepalive)
            _SHARED[key] = client
            _REFCOUNT[key] = 0
        elif first or getattr(client, "on_message", None) is not router.dispatch:
            # An existing publish-only client gains the router the moment the
            # first subscriber arrives, so publishers and subscribers share ONE
            # socket rather than splitting into two.
            # Its on_connect (wired by `_start`) already re-issues this
            # router's filters, looked up by broker at each CONNACK.
            client.on_message = router.dispatch
        _REFCOUNT[key] = _REFCOUNT.get(key, 0) + 1

    # Sent now if the connection is already up; otherwise paho returns
    # MQTT_ERR_NO_CONN and `router.resubscribe` issues it on the CONNACK.
    client.subscribe(topic_filter, qos)
    sys.stderr.write(
        f"[bus_client] {label} subscribed {topic_filter!r} on the shared "
        f"connection to {host}:{port} ({router.route_count()} routes, 1 socket)\n")
    return client


def route_count(mqtt_url: str = "mqtt://localhost:1883") -> int:
    host, port = parse_mqtt_url(mqtt_url)
    with _LOCK:
        r = _ROUTERS.get((host, port))
        return r.route_count() if r else 0


def connection_count() -> int:
    """Live shared connections in this process. The acceptance metric for 2a."""
    with _LOCK:
        return len(_SHARED)


def publisher_count() -> int:
    """How many sources are sharing (the collapse ratio)."""
    with _LOCK:
        return sum(_REFCOUNT.values())


def _reset_for_tests() -> None:
    """Drop shared state WITHOUT touching the sockets (tests inject fakes)."""
    with _LOCK:
        _SHARED.clear()
        _REFCOUNT.clear()
        _ROUTERS.clear()
        _HEALTH.clear()


def _selftest() -> int:
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    class _Fake:
        def __init__(self): self.connected = None; self.looping = False; self.sent = []
        def connect(self, h, p, keepalive=60): self.connected = (h, p)
        def connect_async(self, h, p, keepalive=60): self.connected = (h, p)
        def reconnect_delay_set(self, min_delay=1, max_delay=120): pass
        def loop_start(self): self.looping = True
        def publish(self, t, p, qos=0, retain=False): self.sent.append((t, p, qos))

    _reset_for_tests()
    # (1) url parsing, incl. the bare-host and scheme cases every source re-implemented
    check(parse_mqtt_url("mqtt://mqtt-broker.local:1883") == ("mqtt-broker.local", 1883), "(1) full url")
    check(parse_mqtt_url("host") == ("host", 1883), "(2) bare host -> default port")
    check(parse_mqtt_url("tcp://h:1") == ("h", 1), "(3) tcp scheme")

    # (4) N callers, ONE socket — the whole point.
    made = []
    def factory():
        c = _Fake(); made.append(c); return c
    a = acquire("mqtt://h:1883", label="self", client_factory=factory)
    b = acquire("mqtt://h:1883", label="meta", client_factory=factory)
    c = acquire("mqtt://h:1883", label="transcript", client_factory=factory)
    check(a is b is c, "(4) all callers got the SAME client object")
    check(len(made) == 1, f"(5) only ONE client constructed, got {len(made)}")
    check(connection_count() == 1, f"(6) one connection, got {connection_count()}")
    check(publisher_count() == 3, f"(7) refcount tracks 3 publishers, got {publisher_count()}")
    check(a.connected == ("h", 1883) and a.looping, "(8) connected + loop started once")

    # (9) a DIFFERENT broker is a different connection (not over-shared).
    d = acquire("mqtt://other:1883", label="x", client_factory=factory)
    check(d is not a, "(9) different broker -> different client")
    check(connection_count() == 2, f"(10) two brokers -> two connections, got {connection_count()}")

    # (11) a subscriber is REFUSED, loudly — the footgun guard.
    try:
        acquire("mqtt://h:1883", label="channel_bridge", will_subscribe=True,
                client_factory=factory)
        fails.append("(11) will_subscribe=True was allowed onto the shared client")
    except ValueError as exc:
        check("PUBLISH-ONLY" in str(exc), f"(11b) refusal message unclear: {exc}")

    # ── step 2b: the topic-filter matcher ────────────────────────────────────
    # Tested exhaustively because a near-miss here drops messages with NO error.
    MATCH = [
        # (filter, topic, expected)
        ("composeio/global/self/x", "composeio/global/self/x", True),
        ("composeio/global/self/x", "composeio/global/self/y", False),
        ("composeio/+/self/x",      "composeio/global/self/x", True),
        ("composeio/+/self/x",      "composeio/local/self/x",  True),
        # `+` is exactly ONE level — it must NOT span a slash.
        ("composeio/+/x",           "composeio/a/b/x",         False),
        ("composeio/#",             "composeio/global/self/x", True),
        # `#` INCLUDES THE PARENT LEVEL — MQTT 3.1.1 §4.7.1.2: "sport/# also
        # matches the singular sport". My first draft of this case asserted
        # False; the implementation was right and the TEST was wrong. Pinned
        # with the citation so nobody "fixes" the matcher to match the intuition.
        ("composeio/#",             "composeio",               True),
        ("#",                       "anything/at/all",         True),
        # a longer filter than topic never matches
        ("composeio/global/a/b",    "composeio/global/a",      False),
        # a longer topic than a slash-terminated filter never matches
        ("composeio/global",        "composeio/global/a",      False),
        # the real console filters
        ("composeio/+/_app/doc/open", "composeio/global/_app/doc/open", True),
        ("composeio/+/_app/doc/open", "composeio/global/_app/dsd/open", False),
    ]
    for filt, topic, want in MATCH:
        got = topic_matches(filt, topic)
        check(got == want, f"(12) topic_matches({filt!r},{topic!r}) = {got}, want {want}")

    # (13) fan-out: ONE inbound message reaches every matching handler, and only those.
    _reset_for_tests()
    seen = {"a": 0, "b": 0, "c": 0}

    class _Msg:
        def __init__(self, topic): self.topic = topic; self.payload = b"{}"

    class _FakeSub(_Fake):
        def __init__(self): super().__init__(); self.on_message = None; self.subs = []
        def subscribe(self, t, qos=0): self.subs.append((t, qos))

    subf = []
    def sfactory():
        c = _FakeSub(); subf.append(c); return c

    subscribe("mqtt://h:1883", "composeio/+/self/a",
              lambda c, u, m: seen.__setitem__("a", seen["a"] + 1),
              label="A", client_factory=sfactory)
    subscribe("mqtt://h:1883", "composeio/#",
              lambda c, u, m: seen.__setitem__("b", seen["b"] + 1),
              label="B", client_factory=sfactory)
    subscribe("mqtt://h:1883", "composeio/+/self/zzz",
              lambda c, u, m: seen.__setitem__("c", seen["c"] + 1),
              label="C", client_factory=sfactory)
    check(len(subf) == 1, f"(13) 3 subscribers -> ONE client, got {len(subf)}")
    check(connection_count() == 1, f"(14) one connection, got {connection_count()}")
    check(route_count("mqtt://h:1883") == 3, f"(15) 3 routes registered")
    cl = subf[0]
    cl.on_message(cl, None, _Msg("composeio/global/self/a"))
    check(seen == {"a": 1, "b": 1, "c": 0},
          f"(16) fan-out hit exactly the matching handlers: {seen}")
    check(("composeio/+/self/a", 0) in cl.subs,
          f"(17) broker-side subscribe was issued: {cl.subs}")

    # (18) a RAISING handler must not silence its siblings — the delivery-loss guard.
    _reset_for_tests()
    hits = {"good": 0}
    boomf = []
    def bfactory():
        c = _FakeSub(); boomf.append(c); return c
    def _boom(c, u, m): raise RuntimeError("handler exploded")
    subscribe("mqtt://h:1883", "x/#", _boom, label="bad", client_factory=bfactory)
    subscribe("mqtt://h:1883", "x/#",
              lambda c, u, m: hits.__setitem__("good", hits["good"] + 1),
              label="good", client_factory=bfactory)
    b0 = boomf[0]
    b0.on_message(b0, None, _Msg("x/y"))
    check(hits["good"] == 1,
          "(18) a raising handler silenced its sibling — delivery loss")

    # (19) publishers and subscribers share ONE socket: a publisher-first client
    #      gains the router when the first subscriber arrives.
    _reset_for_tests()
    mixed = []
    def mfactory():
        c = _FakeSub(); mixed.append(c); return c
    pub = acquire("mqtt://h:1883", label="pub", client_factory=mfactory)
    got = {"n": 0}
    sub = subscribe("mqtt://h:1883", "q/#",
                    lambda c, u, m: got.__setitem__("n", got["n"] + 1),
                    label="sub", client_factory=mfactory)
    check(pub is sub, "(19) publisher and subscriber share the SAME client")
    check(len(mixed) == 1, f"(20) still ONE socket, got {len(mixed)}")
    pub.on_message(pub, None, _Msg("q/z"))
    check(got["n"] == 1, "(21) the router was attached to the pre-existing client")

    _reset_for_tests()
    if fails:
        for m in fails:
            sys.stderr.write(f"FAIL {m}\n")
        return 1
    print("PASS: bus_client selftest (all checks, incl. 12 topic-filter cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
