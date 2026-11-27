"""MQTT transport binding.

DP-CIO-6: Same bus semantics over MQTT as over local/I2C/CAN.

Topic mapping: composeio/{scope}/{namespace}
  - composeio/global/ftl_arm
  - composeio/local/office_lights  (local scope still published for bus monitors)
  - composeio/_bus/time

QoS: 1 for state messages (at-least-once), 0 for events (fire-and-forget)
"""

from __future__ import annotations
import json
import logging
import math
import time
from collections import defaultdict
from typing import Callable

from composeio.transport import Transport, BusMessage, MessageType
# Reuse by reference, never fork ([DX-NARROW-WAIST]): `bus_client` already owns
# the one tested MQTT topic-filter matcher, and [CXC-SOB-ENDSTATE] has that
# module folding INTO this one — so depending on it here is the direction of
# travel, not a new coupling. A second hand-rolled `+`/`#` matcher is precisely
# the fork that drops messages with no error at all.
from composeio.bus_client import topic_matches as _topic_matches
# paho 1.x / 2.x construction lives in a pure helper module so code that must
# not import a transport (the launcher) can use it too; re-exported here.
from composeio.paho_compat import new_paho_client  # noqa: E402,F401

logger = logging.getLogger("composeio.transport.mqtt")

# QoS mapping by message type
_QOS_MAP = {
    MessageType.STATE: 1,
    MessageType.EVENT: 0,
    MessageType.CONFIG_PUSH: 1,
    MessageType.CONFIG_REQUEST: 1,
    MessageType.HEARTBEAT: 0,
    MessageType.DISCOVERY: 1,
    MessageType.TIME_SYNC: 0,
    MessageType.BINDING: 1,
}

# ---------------------------------------------------------------------------
# RETAIN, per spec/01 §6.2.2 [BP-MQTT-QOS] — THE OTHER COLUMN OF THE SAME TABLE
# ---------------------------------------------------------------------------
# `_QOS_MAP` above transcribed the QoS column and the Retain column was simply
# never written down, so `publish()` got no `retain` argument at all and paho's
# default `retain=False` applied to every message this stack has ever sent.
# Every "Retained: Yes" in the spec was false, including §2.1's core semantic of
# a state message — "new subscribers receive the last published state".
#
# The symptom reached the glass and was normalised rather than reported: "when a
# new thrust widget pops up, I have to wiggle the thrust levers for them to see
# any data." Wiggling the levers is a HUMAN-SUPPLIED RETAIN. Any symptom with a
# comfortable manual workaround is a defect that will never be filed.
#
# EVERY MessageType APPEARS HERE, including the two the spec table pre-dates, so
# that adding a type to the enum without deciding its retain semantics is a test
# failure rather than a silent inheritance of `False`. This is the same
# one-table discipline `MESSAGE_TYPE_WIRE_CODES` exists to enforce, adopted after
# four forked copies of that table silently relabelled types on the wire.
RETAIN_BY_TYPE: dict[MessageType, bool] = {
    MessageType.STATE: True,          # new subscribers get the last value
    MessageType.EVENT: False,         # transient; a retained press replays forever
    MessageType.CONFIG_PUSH: False,   # addressed to one module, not a standing fact
    MessageType.CONFIG_REQUEST: False,
    MessageType.HEARTBEAT: True,      # last status should be queryable
    MessageType.DISCOVERY: True,      # new participants need the device registry
    MessageType.TIME_SYNC: True,      # latest time beats reliability
    MessageType.BINDING: True,        # active bindings should be queryable
    # --- absent from the spec table (both post-date it) --------------------
    # NOT retained, and the asymmetry is deliberate: a wrongly-retained value
    # is a lie that PERSISTS in the broker and is served to every future
    # subscriber, while a wrongly-unretained one costs a single publish. An ACK
    # is a correlation response to one request; a LOG_ENTRY is one item of an
    # append-only stream, where retaining an arbitrary single entry is
    # meaningless.
    MessageType.ACK: False,
    MessageType.LOG_ENTRY: False,
}

#: Payload key carrying the Message Expiry Interval AS PUBLISHED, so a receiver
#: can compute `age = published - remaining`. It travels in the envelope rather
#: than being assumed from a shared constant because publisher and subscriber
#: are separate deployments that do not upgrade together.
#:
#: MEASURED 2026-08-15: it also crosses the VERSION boundary. A 3.1.1 subscriber
#: received a v5 client's retained publish with the payload byte-identical and
#: no `properties` attribute at all. The envelope survives the downgrade; the
#: property does not. That is the whole reason this is not solely a v5 property.
EXPIRY_KEY = "exp"


# ===========================================================================
# MQTT 5 — WHAT THE UPGRADE BUYS, AND WHAT WE DECIDED ABOUT EACH FEATURE
# ===========================================================================
# David: "Seems like we should upgrade to MQTT5."
#
# THIS TABLE IS THE ANSWER, and it lives in the code for the same reason
# `MESSAGE_TYPE_WIRE_CODES` and `RETAIN_BY_TYPE` do: a feature with no entry is a
# feature nobody decided about, and a decision that lives only in a commit
# message gets re-litigated from scratch by the next reader. A test asserts that
# every entry carries a status and a reason.
#
# ⛔ THE HEADLINE RISK, STATED FIRST. A SILENTLY DOWNGRADED CONNECTION IS WORSE
# THAN A REFUSED ONE — and this design gets the refusal for free rather than by
# effort: paho performs NO automatic fallback from v5 to 3.1.1. A broker too old
# for v5 answers the CONNECT with 0x84 (Unsupported Protocol Version) and the
# transport logs a failure. It never connects happily while dropping every
# property on the floor. `_on_connect` names the reason in words, because "rc=134"
# sends the reader to a spec table to learn the one thing they needed.
MQTT5_ADOPTION: dict[str, dict] = {
    "message_expiry_interval": {
        "status": "adopted",
        "why": "THE reason for the upgrade. The broker DECREMENTS it, so a "
               "retained value arrives carrying its own remaining staleness and "
               "`age = published - remaining` needs no clock agreement between "
               "publisher and subscriber — the property [D4] chose receiver-side "
               "ages for, since this fleet has no shared time base. Unavailable "
               "at 3.1.1, where PUBLISH properties do not exist at all.",
    },
    "will_delay_interval": {
        "status": "adopted",
        "why": "A Last Will is the only liveness signal that does not require "
               "the dead node to be alive enough to send it: the BROKER "
               "publishes it. Will Delay Interval is v5-only and is what stops a "
               "3-second Wi-Fi reconnect being announced as a death. Off by "
               "default — a will is a claim about a node's identity, and "
               "inventing one for every transport would plant a retained "
               "'offline' marker on a namespace the caller never asked to own.",
    },
    "reason_codes": {
        "status": "adopted",
        "why": "3.1.1 has five CONNACK codes and one DISCONNECT (none). v5 names "
               "the failure — unsupported protocol, not authorized, quota "
               "exceeded, server busy — which is the difference between a log "
               "line an operator can act on and a number they must look up.",
    },
    "session_expiry_interval": {
        "status": "declined",
        "why": "Set EXPLICITLY to 0 with clean_start=True rather than inherited "
               "from paho's MQTT_CLEAN_START_FIRST_ONLY default. A ComposeIO "
               "subscriber wants CURRENT STATE, which retain now delivers, not a "
               "replay of everything it missed. On a flaky RF link a persistent "
               "session turns a reconnect into a flood of superseded values, "
               "every one of which arrives looking live.",
    },
    "no_local": {
        "status": "deferred",
        "why": "MEASURED WORKING against mqtt-broker.local 2026-08-15: "
               "noLocal=True echoed a client's own publish back 0 times, without "
               "it 1. It matters more now than yesterday — this transport "
               "subscribes `composeio/+/_bus/#` and `_bus/time` has just become a "
               "1 Hz publisher, so every master bus receives its own tick back "
               "once a second forever and re-dispatches it as though a peer sent "
               "it. Implemented behind `no_local=`, OFF by default: loopback is "
               "observable behaviour this repo has never measured a dependency "
               "on either way, and flipping it globally in the same change that "
               "creates the echo would confound the two. One-line default flip "
               "behind a live-fleet observation.",
    },
    "retain_handling": {
        "status": "declined",
        "why": "Option 0 (send retained on every subscribe) is correct for us and "
               "is the default, so it is left alone. We reconnect with "
               "clean_start, so the subscription genuinely does not exist and a "
               "reconnecting console SHOULD be handed the current world — that is "
               "the wiggle-the-levers symptom staying fixed across a reconnect.",
    },
    "retain_as_published": {
        "status": "declined",
        "why": "RAP matters to BRIDGES that must preserve a message's original "
               "retain flag while forwarding. Our CAN-MQTT bridge re-derives "
               "retain from the message TYPE via `RETAIN_BY_TYPE`, which is the "
               "one table, so preserving an upstream flag would let a peer's "
               "opinion override our own table.",
    },
    "user_properties": {
        "status": "declined",
        "why": "Tempting home for `exp` and for freshness metadata, and rejected "
               "on measurement: a 3.1.1 subscriber receives NO properties at all "
               "(verified 2026-08-15), so anything carried there is invisible to "
               "the PubSubClient half of the fleet. The JSON envelope crosses the "
               "version boundary; properties do not.",
    },
    "topic_alias": {
        "status": "declined",
        "why": "The broker offers TopicAliasMaximum: 10. It trades a per-message "
               "byte saving for connection-scoped state that must be rebuilt on "
               "every reconnect and that makes a packet capture unreadable "
               "without the alias map. Our topics are ~40 bytes against payloads "
               "measured up to 110 KB; the saving is noise and the debuggability "
               "cost is not.",
    },
    "shared_subscriptions": {
        "status": "declined",
        "why": "$share load-balances a subscription across a consumer group. "
               "ComposeIO's model is BROADCAST — every module that cares sees "
               "every message — so a shared subscription would deliver each "
               "message to exactly one of N interested widgets, which is the "
               "opposite of the bus semantic. Also mutually exclusive with "
               "noLocal at the protocol level.",
    },
    "subscription_identifiers": {
        "status": "declined",
        "why": "Lets a client learn WHICH of its filters matched. We already "
               "route by parsing the namespace out of the topic and by "
               "`_topic_matches`, one tested matcher shared by reference. A "
               "second, protocol-level routing key is a fork of that decision.",
    },
    "request_response": {
        "status": "deferred",
        "why": "Response Topic + Correlation Data map exactly onto ComposeBus's "
               "request/announcement split, and onto the ACK type "
               "([BP-TYPE-ACK]) that already has a wire code. Genuinely "
               "attractive — and deliberately NOT taken in this change, because "
               "that idiom is implemented identically on CAN and UART, where no "
               "such property exists. Adopting it on MQTT alone would make ONE "
               "substrate's request/announcement structurally different from the "
               "others, which is the transport-agnosticism DP-CIO-6 exists for.",
    },
    "flow_control": {
        "status": "adopted",
        "why": "Not a switch — a constraint to respect. The broker declares "
               "ReceiveMaximum: 20, so at most 20 QoS>0 publishes are in flight "
               "and paho blocks beyond that. Worth naming because our largest "
               "payloads are ~110 KB at QoS 1: a burst of console sources can "
               "queue in the CLIENT, and that is a latency symptom with no log "
               "line.",
    },
    "maximum_packet_size": {
        "status": "adopted",
        "why": "MEASURED 2026-08-15: this broker declares NO MaximumPacketSize, "
               "so the v5 default (268,435,455 B) applies and our ~110 KB "
               "console payloads are safe. Named here because had it declared a "
               "small one, the broker would DISCARD oversized publishes — and a "
               "v5 publisher learns that only from a DISCONNECT reason code, "
               "which is exactly the silent-loss shape this repo keeps finding.",
    },
}

#: What the live broker ACTUALLY declared, with the date. Recorded so the next
#: reader does not re-measure, and — more usefully — so a CHANGE at the broker
#: shows up as a difference from a number rather than as a vibe.
MQTT5_BROKER_MEASURED = {
    "host": "mqtt-broker.local:1883",
    "measured": "2026-08-15",
    "connack_reason": "Success",
    "connack": {
        "ReceiveMaximum": 20,
        "TopicAliasMaximum": 10,
        # ABSENT, and each absence is load-bearing:
        #   MaximumPacketSize   -> v5 default 268435455 B; 110 KB payloads OK
        #   RetainAvailable     -> retain permitted (a 0 would forbid it)
        #   MaximumQoS          -> QoS 2 available
        #   SessionExpiryInterval -> our requested 0 was accepted as sent
    },
    "interop": {
        "v5_publisher_to_v311_subscriber":
            "DELIVERED, payload byte-identical, NO properties attribute",
        "v311_publisher_to_v5_subscriber":
            "DELIVERED retained, MessageExpiryInterval=None -> UNDATABLE",
        "no_local_true": "own publish echoed 0 times",
        "no_local_false": "own publish echoed 1 time",
    },
}

#: ⛔ WHO IN THIS FLEET CANNOT SPEAK v5, measured from the source tree.
#:
#: Arduino **PubSubClient** implements MQTT 3.1.1 ONLY and has no v5 path at all.
#: It is used by `firmware/reactive_core/src/controller_main.cpp` and
#: `firmware/esp32_mqtt_client` — and `reactive_core` is THE THROTTLE, the
#: fleet's most-watched publisher. ESP-IDF's `esp-mqtt` (`cyd/src/bus_mqtt.cpp`)
#: CAN speak v5 but only via the `esp_mqtt5_*` API with CONFIG_MQTT_PROTOCOL_5;
#: today it is built at 3.1 and publishes retain=0.
#:
#: THE CONSEQUENCE, AND IT IS NOT COSMETIC: a retained value from any of these
#: carries no Message Expiry Interval, so its age is UNKNOWN — permanently, not
#: pending an upgrade. `_annotate_age` returns None there rather than 0.0, which
#: is the correct and the only honest answer: the subscriber getting smarter must
#: not make an undatable value look fresh.
#:
#: NOTHING BREAKS ON THE WIRE. The broker translates between versions in both
#: directions (measured above), so a v5 publisher and a 3.1.1 subscriber
#: interoperate and vice versa. What is lost is the AGE, not the message.
MQTT5_NON_V5_PEERS = (
    "firmware/reactive_core (Arduino PubSubClient — 3.1.1 only, no v5 path)",
    "firmware/esp32_mqtt_client (Arduino PubSubClient — 3.1.1 only)",
    "cyd/src/bus_mqtt.cpp (ESP-IDF esp-mqtt, built at 3.1; v5 available via "
    "esp_mqtt5_* + CONFIG_MQTT_PROTOCOL_5)",
)

#: v5 CONNACK reason codes worth naming in a log line rather than printing raw.
#: An operator reading "rc=134" learns nothing; "unsupported protocol version"
#: tells them the broker is too old for what this build now requires.
MQTT5_CONNACK_REASONS = {
    0x00: "success",
    0x81: "malformed packet",
    0x82: "protocol error",
    0x83: "implementation specific error",
    0x84: "unsupported protocol version — this broker cannot speak MQTT 5, and "
          "paho does NOT downgrade. Either upgrade the broker or this client "
          "loses the age contract entirely",
    0x85: "client identifier not valid",
    0x86: "bad user name or password",
    0x87: "not authorized",
    0x88: "server unavailable",
    0x89: "server busy",
    0x8A: "banned",
    0x8C: "bad authentication method",
    0x90: "topic name invalid",
    0x95: "packet too large",
    0x97: "quota exceeded",
    0x99: "payload format invalid",
    0x9A: "retain not supported — RETAIN_BY_TYPE cannot be honoured here and "
          "every late subscriber is back to wiggling the levers",
    0x9B: "QoS not supported",
    0x9C: "use another server",
    0x9D: "server moved",
    0x9F: "connection rate exceeded",
}


def _publish_properties(expiry_s: int):
    """An MQTT 5 PUBLISH property bag carrying `MessageExpiryInterval`.

    paho is imported LAZILY here, matching the rest of this module and
    `bus_client`: paho is an optional runtime dependency and importing it at
    module scope would make every consumer of `BusMessage` require it.
    Returns None if paho is unavailable — the publish still happens, it simply
    carries no age, and the receive side already treats a missing expiry as
    UNKNOWN rather than fresh.
    """
    try:
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
    except ImportError:                                     # pragma: no cover
        return None
    props = Properties(PacketTypes.PUBLISH)
    props.MessageExpiryInterval = expiry_s
    return props


def _connect_properties():
    """An MQTT 5 CONNECT property bag declaring a NON-persistent session.

    Returns None if paho is unavailable, matching `_publish_properties`: the
    connect still happens and simply takes the broker's defaults.
    """
    try:
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
    except ImportError:                                      # pragma: no cover
        return None
    props = Properties(PacketTypes.CONNECT)
    props.SessionExpiryInterval = 0
    return props


def _will_properties(delay_s: int):
    """A WILLMESSAGE property bag carrying `WillDelayInterval` (v5 only)."""
    try:
        from paho.mqtt.properties import Properties
        from paho.mqtt.packettypes import PacketTypes
    except ImportError:                                      # pragma: no cover
        return None
    props = Properties(PacketTypes.WILLMESSAGE)
    props.WillDelayInterval = int(delay_s)
    return props


#: the clock the connect-failure log throttle reads; a module attribute so a
#: test can drive it without sleeping.
_monotonic = time.monotonic


def _broker_host_error(host) -> "str | None":
    """Why `host` can never resolve, or None if it might.

    No I/O. `socket.getaddrinfo` encodes a str host with the idna codec, so a
    host that idna rejects (an empty label as in `bad..host`, a label over 63
    characters) raises UnicodeError INSIDE paho's loop thread. paho 1.6.1
    catches only OSError there, so the thread dies with nothing counted and
    nothing in health(). Checking the same codec here turns that into a loud,
    recorded config error.
    """
    if not isinstance(host, str) or not host.strip():
        return f"invalid broker host {host!r}: empty"
    try:
        host.encode("idna")
    except UnicodeError as exc:
        return f"invalid broker host {host!r}: {exc}"
    return None


def _connack_reason(rc) -> str:
    """Render a v5 CONNACK reason code as words an operator can act on.

    `rc=134` sends a reader to a spec table to learn the one thing they needed;
    "unsupported protocol version" tells them the broker is too old for what this
    build requires. Accepts paho's `ReasonCodes` or a bare int, because 3.1.1
    delivers the latter and this must not raise while reporting a failure.
    """
    try:
        code = int(rc)
    except (TypeError, ValueError):
        code = getattr(rc, "value", None)
    text = MQTT5_CONNACK_REASONS.get(code)
    if text is None:
        return f"rc={rc!r}"
    return f"0x{code:02X} ({code}) {text}"


def connect_with_backoff(client, host, port, *, keepalive=60, min_delay=None,
                         max_delay=None, **connect_kwargs) -> None:
    """Start ``client`` connecting to ``host:port`` and keep it trying.

    The sequence ``MQTTTransport.start()`` has used since PR #16, factored out
    so ``bus_client`` runs the same one instead of a copy: bounded backoff via
    ``reconnect_delay_set`` (defaults: ``MQTTTransport.RECONNECT_MIN_DELAY_S`` /
    ``RECONNECT_MAX_DELAY_S``, read at call time), then ``connect_async`` (which
    records the target and does no I/O) and ``loop_start``. paho's loop thread
    makes the first attempt and every later one, so "broker down at start" and
    "broker dropped mid-run" are the same case and neither raises to the
    caller. The caller re-subscribes in ``on_connect``: a ``subscribe()`` made
    before the CONNACK is not sent (paho returns MQTT_ERR_NO_CONN with no socket).

    Raises only for a malformed target or an unusable client, never for an
    unreachable broker. ``connect_kwargs`` (for example ``clean_start``,
    ``properties``) pass through to ``connect_async`` untouched.
    """
    if min_delay is None:
        min_delay = MQTTTransport.RECONNECT_MIN_DELAY_S
    if max_delay is None:
        max_delay = MQTTTransport.RECONNECT_MAX_DELAY_S
    client.reconnect_delay_set(min_delay=min_delay, max_delay=max_delay)
    client.connect_async(host, port, keepalive=keepalive, **connect_kwargs)
    client.loop_start()


class LinkHealth:
    """The connection state of one MQTT link, and its failure log, in ONE place.

    Used by `MQTTTransport` and by `bus_client`, so an outage reads the same
    through either: `connected`, `connect_failures` (consecutive failed
    attempts since the last success), `last_error`, and `last_success`
    (wall-clock epoch seconds of the last successful CONNACK, None if never).
    `snapshot()` is the health() structure both expose.

    LOGGING. The first failure of each KIND is logged at once, at the level the
    caller gives (ERROR for a failed connect), and names `target`, the broker
    host:port as configured. Repeats are thinned: one summary line, with the
    counts, when the consecutive-failure count reaches a number in
    `repeat_at_attempts`, or when `summary_every_s` has passed since that kind
    was last reported. `repeat_level` sets the summary's level (None: the
    first line's level). The counts are never thinned. A success clears the
    throttle, so a new outage always logs its first line, and `record_success`
    returns how many attempts had failed so the caller can log the recovery.

    The clock is this module's `_monotonic`, read at each call, so tests can
    drive it.
    """

    def __init__(self, target: str, *, log, summary_every_s: float,
                 repeat_level=None, repeat_at_attempts=()):
        self.target = target
        self.connected = False
        self.failures = 0
        self.last_error: "str | None" = None
        self.last_success: "float | None" = None
        self.log = log
        self.summary_every_s = summary_every_s
        self.repeat_level = repeat_level
        self.repeat_at_attempts = frozenset(repeat_at_attempts)
        #: kind -> [monotonic time of the last line, repeats since it]
        self.fail_log: dict[str, list] = {}

    def throttled_log(self, level: int, kind: str, first_line: str,
                      summary: str, extra=None) -> None:
        now = _monotonic()
        state = self.fail_log.get(kind)
        if state is None:
            self.log.log(level, first_line)
            self.fail_log[kind] = [now, 0]
            return
        state[1] += 1
        if (self.failures in self.repeat_at_attempts
                or now - state[0] >= self.summary_every_s):
            self.log.log(
                self.repeat_level if self.repeat_level is not None else level,
                f"{summary}: {state[1]} more since the last report, "
                f"{self.failures} failed attempts in total"
                f"{extra() if extra else ''}")
            state[0], state[1] = now, 0

    def record_failure(self, kind: str, first_line: str, summary: str,
                       level: int = logging.ERROR, extra=None) -> None:
        """One failed attempt: count it, keep the reason, log it thinned."""
        self.connected = False
        self.failures += 1
        self.last_error = kind
        self.throttled_log(level, kind, first_line, summary, extra)

    def record_success(self) -> int:
        """A successful CONNACK. -> the number of attempts that had failed."""
        before = self.failures
        self.connected = True
        self.failures = 0
        self.last_error = None
        self.last_success = time.time()
        self.fail_log.clear()
        return before

    def snapshot(self) -> dict:
        return {
            "connected": self.connected,
            "broker": self.target,
            "connect_failures": self.failures,
            "last_error": self.last_error,
            "last_success": self.last_success,
        }


class MQTTTransport(Transport):
    """MQTT transport binding using paho-mqtt."""

    def __init__(
        self,
        broker: str = "localhost",
        port: int = 1883,
        client_id: str = "composeio",
        username: str | None = None,
        password: str | None = None,
        topic_prefix: str = "composeio",
        refresh_intervals: dict[str, float] | None = None,
        no_local: bool = False,
        will_namespace: str | None = None,
        will_value=None,
        will_delay_s: int = 0,
    ):
        #: v5 NoLocal on every subscription this transport makes. See
        #: MQTT5_ADOPTION["no_local"] — measured working, deliberately not the
        #: default in the same change that created the echo it fixes.
        self._no_local = no_local
        #: v5 Last Will. `will_delay_s` is the Will Delay Interval, which is
        #: v5-only and is what separates "this node died" from "this node's
        #: Wi-Fi blinked".
        self._will_namespace = will_namespace
        self._will_value = will_value
        self._will_delay_s = will_delay_s
        #: namespace -> DECLARED refresh interval in seconds, used to derive a
        #: retained message's expiry. Injected rather than read from the bus so
        #: this transport keeps knowing nothing about `Namespace`.
        #:
        #: EMPTY TODAY, AND HONESTLY SO. [D4] ratified a YAML-declarable
        #: freshness contract but nothing declares one yet — `Namespace` carries
        #: no refresh/max-age/timeout field at all. This is the seam that
        #: contract plugs into when it lands; until then every namespace takes
        #: the horizon below.
        self._refresh_intervals: dict[str, float] = dict(refresh_intervals or {})
        self._broker = broker
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._topic_prefix = topic_prefix
        self._client = None
        #: connected flag, consecutive failed connect attempts since the last
        #: CONNACK Success (counted from paho's on_connect_fail, so an outage
        #: is a NUMBER a health check can read, not a log line nobody does),
        #: last error, last success, and the failure-log throttle. Shared with
        #: `bus_client` through `LinkHealth`; the `_connected`,
        #: `_connect_failures`, `_last_connect_error` and `_fail_log`
        #: properties below read and write it.
        self._link = LinkHealth(f"{broker}:{port}", log=logger,
                                summary_every_s=self.FAILURE_LOG_SUMMARY_S)
        self._subscribers: dict[str, list[Callable[[BusMessage], None]]] = defaultdict(list)
        self._all_subscribers: list[Callable[[BusMessage], None]] = []
        # [CXC-FL-PATTERN] wildcard topic filters (deduped), re-subscribed
        # on every (re)connect like the exact-namespace set.
        self._pattern_topics: set[str] = set()
        # [DEC-CONSOLE-MQTT-STARTUP-RACE] a scene publishes its first _bus/time
        # tick and its namespace echoes within ~300ms of start(), BEFORE paho's
        # connect callback lands — those sends were logged as "not connected,
        # dropping" and genuinely lost.  Hold them briefly instead and flush on
        # connect: the outbound path becomes order-preserving across the
        # connect boundary rather than lossy at exactly the moment a peer
        # announces itself.  Bounded, so an unreachable broker can't grow it.
        self._pending: list[BusMessage] = []
        self._pending_dropped = 0
        # FOREIGN channel filter -> callbacks. Re-subscribed on every
        # (re)connect exactly like `_subscribers` and `_pattern_topics`; a
        # foreign subscription that silently failed to survive a reconnect
        # would take the human's channel down with no error.
        self._foreign_subs: dict[str, list] = {}
        # [CXC-SOB-LOG-NOISE-CALIBRATION] namespaces already reported as
        # oversized. Bounded by the number of DISTINCT namespaces that actually
        # cross LARGE_PAYLOAD_WARN_BYTES — a handful, not a function of the
        # publish rate — so it is deliberately uncapped: a cap would silently
        # re-arm the storm for whichever namespace got evicted.
        self._large_payload_warned: set[str] = set()

    #: how many pre-connect messages to hold before dropping the OLDEST.
    #: A scene's startup burst is a handful; this is slack, not a buffer.
    PENDING_MAX = 64

    #: paho's reconnect backoff, in seconds: the first retry after RECONNECT_
    #: MIN_DELAY_S, doubling to a cap of RECONNECT_MAX_DELAY_S, reset on a
    #: successful CONNACK. paho's default cap is 120 s; 30 s is chosen because
    #: the cap IS the worst-case time from "broker back" to "bus back", and
    #: 30 s keeps that inside one Docker HEALTHCHECK interval, while one TCP
    #: attempt per 30 s per client is no load on a broker that is coming back
    #: up. Retained state means a late reconnect still receives the current
    #: world, so nothing is gained by hammering faster than 1 s.
    RECONNECT_MIN_DELAY_S = 1
    RECONNECT_MAX_DELAY_S = 30

    #: repeated IDENTICAL connect failures are logged once, then summarised
    #: at most once per this many seconds. A new kind of failure is logged at
    #: once. The count in health() is never thinned.
    FAILURE_LOG_SUMMARY_S = 600

    #: Message Expiry Interval for a retained message whose namespace declares
    #: NO refresh interval. 24 hours.
    #:
    #: WHY LONG, WHEN THE POINT IS TO LIMIT STALENESS. The instinct is to make
    #: this short so stale values self-delete. That instinct is wrong here, and
    #: the throttle is the case that shows why: `controller_main.cpp` gates
    #: emission on BOTH a sample clock and a deadband against the last EMITTED
    #: value — "quiet-bus by construction". So the fleet's most-watched
    #: namespace is event-driven and ABSOLUTELY SILENT at rest, by design. A
    #: short expiry would delete its retained value during perfectly normal
    #: silence and reproduce the exact symptom retain is being added to fix.
    #:
    #: SO HONESTY IS SUPPLIED BY AGE, NOT BY DELETION. A long horizon WITH an
    #: exact age is truthful — the subscriber gets the last known value and is
    #: told precisely how old it is, then applies its own policy. A short
    #: horizon WITHOUT an age is the lie. Deleting data is not what makes
    #: retain safe; DATING it is.
    RETAIN_EXPIRY_DEFAULT_S = 86400

    def declare_refresh_intervals(self, intervals: dict[str, float]) -> None:
        """Accept [D4]'s declared cadences from the bus. See the base class.

        MERGED, not replaced: a caller may have injected intervals at
        construction (a deployment overriding a namespace's default cadence),
        and a bus start-up that wiped those would silently revert the override.
        """
        self._refresh_intervals.update(intervals)

    def _expiry_for(self, message: BusMessage) -> int:
        """Message Expiry Interval, in whole seconds, for a retained publish.

        Where a namespace DECLARES a refresh interval, the expiry is [D4]'s
        staleness timeout: 3x the interval, floored at interval + 100 ms (3x is
        a J1939 CONVENTION, not a requirement). Setting the expiry TO the
        staleness timeout composes the two mechanisms exactly — the broker
        drops the retained copy at the same instant the freshness contract
        would declare it stale, so a value that would arrive already-expired is
        never delivered at all.

        Where nothing is declared, the horizon applies and the age carries the
        truth instead. See `RETAIN_EXPIRY_DEFAULT_S`.
        """
        interval = self._refresh_intervals.get(message.namespace)
        if interval is None:
            return self.RETAIN_EXPIRY_DEFAULT_S
        timeout = max(3.0 * interval, interval + 0.1)
        return max(1, math.ceil(timeout))

    #: DP-CIO-18's 256-byte guideline is a DESIGN guideline for constrained
    #: substrates (CAN frames, a UART line), not a runtime alarm for MQTT.
    #: [SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-LOG-NOISE]: routing the Intelligence
    #: Console's 20 data sources through the bus puts KILOBYTE payloads on this
    #: path routinely — transcript narration, the deliverable ledger, the
    #: agents/skills arrays, the defect roll-up, the projects report. At the old
    #: 256-byte WARNING threshold that is hundreds of warnings a minute, which
    #: buries the LOUD failures this arc depends on being visible (a declared-
    #: scope conflict, a transport degrading to local-only). A warning that
    #: fires constantly is a warning nobody reads.
    #:
    #: So the guideline is still CHECKED and still LOGGED, and WARNING is
    #: reserved for a genuine runaway.
    #:
    #: A module CONSTANT, deliberately not a per-call argument: a per-call knob
    #: would let one noisy call site opt out and would have to be threaded
    #: through every publisher — the fork-divergence shape [DX-NARROW-WAIST]
    #: exists to prevent.
    GUIDELINE_PAYLOAD_BYTES = 256

    #: [CXC-SOB-LOG-NOISE-CALIBRATION], 2026-08-13. Was 64 KiB, calibrated as ~4x
    #: `self/projects` (16,465 B measured). It SHIPPED ALREADY-VIOLATED: on the
    #: authoring commit the deliverable ledger — the payload the spec's own hazard
    #: list names FIRST — was 70,333 bytes on the wire. Five days later that was
    #: 27,009 runaway warnings in 58,589 console lines, 46% of the log: exactly
    #: the storm [CXC-SOB-LOG-NOISE] exists to prevent, produced by the change
    #: that was preventing it.
    #:
    #: 256 KiB clears every enumerated console payload measured (the largest,
    #: `self/deliverables`, is ~110 KB at 178 nodes). But the number is NOT what
    #: makes this safe, and treating it as such is what failed the first time:
    #: `all` is an unbounded projection of a corpus that grew 104 -> 178 nodes in
    #: five days, so any absolute value is a clock, not a fix.
    #:
    #: `_large_payload_warned` is the structural half. One WARNING per namespace
    #: per process makes the absolute value NON-CRITICAL — a re-crossing costs one
    #: line instead of a storm, and that line is a durable audit record: one entry
    #: per oversized namespace, which is precisely the substrate-portability audit
    #: the DEBUG tier was claimed to provide and never did (`grep -c DEBUG` over
    #: 58,589 lines / ~30h of console.log = 0; the tier is not enabled and never
    #: has been).
    #:
    #: A growth-RATE detector was considered and REJECTED. It needs a rate and a
    #: window, both calibrated against payloads its author has in hand — the
    #: identical disease one level up. Prefer the change that removes the failure
    #: mode over the change that makes the detector cleverer.
    LARGE_PAYLOAD_WARN_BYTES = 256 * 1024

    def _topic_for(self, message: BusMessage) -> str:
        """Map a bus message to an MQTT topic."""
        scope = message.scope if message.scope != "auto" else "global"
        return f"{self._topic_prefix}/{scope}/{message.namespace}"

    def _namespace_from_topic(self, topic: str) -> tuple[str, str]:
        """Extract namespace and scope from MQTT topic."""
        parts = topic.split("/", 2)
        if len(parts) == 3:
            return parts[2], parts[1]  # namespace, scope
        return topic, "global"

    def send(self, message: BusMessage):
        """Publish a bus message to MQTT.

        Before the connect callback lands, messages are QUEUED (not dropped)
        and flushed in order on connect — see [DEC-CONSOLE-MQTT-STARTUP-RACE].
        """
        if not self._connected or self._client is None:
            if self._client is None:
                logger.warning(
                    f"MQTT transport not started, dropping message to "
                    f"'{message.namespace}'")
                return
            self._pending.append(message)
            if len(self._pending) > self.PENDING_MAX:
                self._pending.pop(0)
                self._pending_dropped += 1
            return

        self._send_now(message)

    def _send_now(self, message: BusMessage):
        topic = self._topic_for(message)
        qos = _QOS_MAP.get(message.type, 0)

        # RETAIN + AGE ([BP-MQTT-QOS], [DEC-IMPLEMENT-MQTT-RETAIN-WITH-AN-AGE-CONTRACT]).
        # The expiry is attached ONLY to retained messages: on a non-retained
        # publish it would govern nothing a subscriber can observe, and an
        # `exp` in that envelope would invite a receiver to compute an age from
        # a number that was never counting down.
        retain = RETAIN_BY_TYPE.get(message.type, False)
        body = message.to_json()
        props = None
        if retain:
            expiry = self._expiry_for(message)
            body[EXPIRY_KEY] = expiry
            props = _publish_properties(expiry)
        payload = json.dumps(body)

        # DP-CIO-18: verify message size. Two tiers ([CXC-SOB-LOG-NOISE]) — see
        # GUIDELINE_PAYLOAD_BYTES / LARGE_PAYLOAD_WARN_BYTES above.
        size = len(payload)
        if size > self.LARGE_PAYLOAD_WARN_BYTES:
            # ONCE PER NAMESPACE per process ([CXC-SOB-LOG-NOISE-CALIBRATION]).
            # A namespace that is oversized is oversized on EVERY send — it is a
            # standing fact, not an event, and re-reporting a standing fact at
            # the publish rate is how one warning became 46% of the log. The
            # first line carries the fact; the storm carried nothing.
            if message.namespace not in self._large_payload_warned:
                self._large_payload_warned.add(message.namespace)
                logger.warning(
                    f"Message to '{message.namespace}' is {size} bytes "
                    f"(over the {self.LARGE_PAYLOAD_WARN_BYTES}-byte runaway "
                    f"threshold). Reported ONCE per namespace per process; "
                    f"further oversized sends to this namespace are silent."
                )
        elif size > self.GUIDELINE_PAYLOAD_BYTES:
            logger.debug(
                f"Message to '{message.namespace}' is {size} bytes "
                f"(exceeds the {self.GUIDELINE_PAYLOAD_BYTES}-byte DP-CIO-18 "
                f"guideline; fine on MQTT, would need fragmenting on CAN/UART)"
            )

        self._client.publish(topic, payload, qos=qos, retain=retain,
                             properties=props)

    def subscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Subscribe to a namespace via MQTT."""
        self._subscribers[namespace].append(callback)
        if self._connected and self._client:
            # Subscribe to both global and local topics for this namespace
            self._client.subscribe(f"{self._topic_prefix}/+/{namespace}", qos=1)

    def subscribe_all(self, callback: Callable[[BusMessage], None]):
        """Subscribe to ALL incoming messages (for bus-level dispatch)."""
        self._all_subscribers.append(callback)

    def _pattern_topic(self, pattern: str) -> str:
        """Map a ComposeIO trailing-glob pattern to an MQTT topic filter.

        'jumpdrive/*' -> '{prefix}/+/jumpdrive/#'  (MQTT '#' includes the
        parent level, so this also matches '{prefix}/global/jumpdrive');
        '*'           -> '{prefix}/#'  (every scope, every namespace).
        """
        if pattern == "*":
            return f"{self._topic_prefix}/#"
        prefix = pattern[:-2] if pattern.endswith("/*") else pattern
        return f"{self._topic_prefix}/+/{prefix}/#"

    def subscribe_pattern(self, pattern: str):
        """[CXC-FL-PATTERN]: widen reception via MQTT's native wildcard —
        matching traffic arrives even for namespaces never registered."""
        topic = self._pattern_topic(pattern)
        if topic in self._pattern_topics:
            return
        self._pattern_topics.add(topic)
        if self._connected and self._client:
            self._client.subscribe(topic, qos=1)

    # ------------------------------------------------------------------ #
    # FOREIGN CHANNELS — see the block comment on `Transport.send_foreign`.
    # ------------------------------------------------------------------ #
    # On MQTT a foreign channel IS a bare topic (no `composeio/` prefix, no
    # scope segment, no BusMessage envelope) — which is exactly what
    # `compose_projects/intelligence/<id>/{in,out}` and a WLED `<deviceTopic>/api` already are.
    # So this override is thin BY DESIGN: the value it adds is not translation,
    # it is that the CALLER no longer imports paho, no longer builds a client,
    # and no longer runs a second network loop. The framing decision moved from
    # nine modules into this one.
    def send_foreign(self, channel: str, payload, *, qos: int = 1) -> bool:
        self._reject_composeio_channel(channel)
        if not self._connected or self._client is None:
            # Deliberately NOT queued into `_pending`: that queue holds
            # BusMessages and is flushed through `_send_now`, which frames an
            # envelope and derives a `composeio/` topic. A foreign frame has
            # neither. Refusing loudly beats inventing a second queue whose
            # ordering relative to the first is undefined.
            logger.warning(
                "MQTT not connected — foreign frame to %r DROPPED", channel)
            return False
        if isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
        self._client.publish(channel, body, qos=qos)
        return True

    def subscribe_foreign(self, channel: str, callback) -> bool:
        """Subscribe a bare topic (or topic FILTER — `+`/`#` pass through).

        Dispatch is kept OFF `_on_message`'s bus path on purpose: a foreign
        payload is not a BusMessage and `BusMessage.from_json` would raise on
        every single one, so routing it through that handler would fill the log
        with "Failed to process MQTT message" for traffic that is perfectly
        well-formed for its own protocol.
        """
        self._reject_composeio_channel(channel)
        self._foreign_subs.setdefault(channel, []).append(callback)
        if self._connected and self._client:
            self._client.subscribe(channel, qos=1)
            self._client.message_callback_add(channel, self._on_foreign_message)
            return True
        # Not connected YET is not a refusal: `_on_connect` re-subscribes the
        # whole set, the same contract the namespace and pattern sets have.
        return self._client is not None

    def _on_foreign_message(self, client, userdata, mqtt_msg):
        for filt, callbacks in self._foreign_subs.items():
            if not _topic_matches(filt, mqtt_msg.topic):
                continue
            for cb in callbacks:
                try:
                    cb(mqtt_msg.topic, mqtt_msg.payload)
                except Exception as e:                       # noqa: BLE001
                    logger.error(f"Foreign subscriber callback error: {e}")

    def unsubscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        if namespace in self._subscribers:
            try:
                self._subscribers[namespace].remove(callback)
            except ValueError:
                pass

    def start(self):
        """Connect to MQTT broker."""
        if self._client is not None:
            return  # Already started (guard against double-start from bus + app)

        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return

        # MQTT 5, EXPLICITLY. paho's default is MQTTv311 (protocol 4) and this
        # call passed no `protocol=`, so the stack negotiated 3.1.1 — where
        # PUBLISH properties do not exist, `MessageExpiryInterval` is silently
        # ignored, and a retained message therefore carries NO AGE. The whole
        # age contract above is unavailable at 3.1.1, so the version is not an
        # optimisation here, it is the mechanism.
        #
        # VERIFIED against the live broker (mqtt-broker.local:1883,
        # 2026-08-14) rather than assumed: an MQTT 5 CONNECT returns Success
        # with CONNACK properties [ReceiveMaximum: 20, TopicAliasMaximum: 10],
        # and the broker decrements Message Expiry Interval on retained
        # delivery (sent 120, a subscriber 9 s later received 111).
        #
        # Built through `new_paho_client` so paho 2.x constructs too (it
        # requires a CallbackAPIVersion; see the helper).
        self._client = new_paho_client(mqtt=mqtt, client_id=self._client_id,
                                       protocol=mqtt.MQTTv5)
        if self._username:
            self._client.username_pw_set(self._username, self._password)

        self._set_will()

        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

        # A host that can never resolve: one loud, recorded error, no loop
        # thread. It cannot become valid without a config change, so retrying
        # would only spin; health() carries the reason meanwhile.
        bad_host = _broker_host_error(self._broker)
        if bad_host is not None:
            self._record_failure(
                bad_host,
                f"MQTT not connecting: {bad_host}. Fix the broker setting and "
                f"restart; this transport stays DOWN until then.")
            return

        try:
            # SESSION SEMANTICS ARE DECLARED, NOT DEFAULTED. paho's v5 default is
            # MQTT_CLEAN_START_FIRST_ONLY, which is a third behaviour ("clean on
            # the first connect, resume afterwards") that nobody here chose. We
            # want neither surprise: clean_start=True with SessionExpiryInterval
            # 0 says a reconnecting subscriber gets the CURRENT world (which
            # retain now delivers) and not a replay of everything it missed. On a
            # flaky RF link the replay is a flood of superseded values, each
            # arriving looking live. See MQTT5_ADOPTION["session_expiry_interval"].
            #
            # ASYNC, SO A FAILED FIRST CONNECT IS RETRIED. This used to be the
            # blocking `connect()` followed by `loop_start()`: when the broker
            # was unreachable at start, `connect()` raised, `loop_start()` never
            # ran, and nothing ever retried. The process stayed up and looked
            # healthy while queueing into a connection that would never come
            # (measured: a ComposeServer container with no MQTT socket for 2+
            # days). `connect_async()` only records the target; paho's loop
            # thread makes the first attempt and every later one with the
            # backoff above, so "down at start" and "dropped mid-run" are now
            # the same case, and `on_connect` re-subscribes + flushes on each.
            # The sequence (backoff, connect_async, loop_start) is
            # `connect_with_backoff`, shared with `bus_client`.
            connect_with_backoff(self._client, self._broker, self._port,
                                 keepalive=60,
                                 min_delay=self.RECONNECT_MIN_DELAY_S,
                                 max_delay=self.RECONNECT_MAX_DELAY_S,
                                 clean_start=True,
                                 properties=_connect_properties())
        except Exception as e:
            # Only a malformed target reaches here (connect_async does no I/O);
            # an unreachable broker is retried by the loop thread instead.
            self._last_connect_error = str(e)
            logger.error(f"MQTT connection setup failed: {e}")

    def _set_will(self) -> None:
        """Register the v5 Last Will, if one was declared.

        WHY THIS IS THE STRONGEST THING v5 BUYS AFTER MESSAGE EXPIRY. [D4] made
        the per-node heartbeat load-bearing — for a quiet-by-construction
        namespace like `throttle/*` it is the ONLY liveness signal there is — and
        a heartbeat has one structural weakness: a node that dies cannot send the
        beat that says so, so its death is inferred from silence after a timeout.
        A WILL IS PUBLISHED BY THE BROKER. It converts an inference into an
        announcement.

        Will Delay Interval is v5-only, and it is the difference between "this
        node died" and "this node's Wi-Fi blinked": the broker holds the will for
        the delay, and cancels it if the session resumes first.
        """
        if self._will_namespace is None or self._client is None:
            return
        msg = BusMessage(namespace=self._will_namespace,
                         type=MessageType.HEARTBEAT,
                         value=self._will_value,
                         source=self._client_id,
                         scope="global")
        props = _will_properties(self._will_delay_s)
        self._client.will_set(
            self._topic_for(msg), json.dumps(msg.to_json()),
            qos=_QOS_MAP.get(MessageType.HEARTBEAT, 0),
            retain=RETAIN_BY_TYPE.get(MessageType.HEARTBEAT, False),
            properties=props)

    def _subscribe_options(self):
        """v5 SubscribeOptions for this transport, or None to take the default.

        None rather than a default-valued SubscribeOptions on purpose: passing
        an options object changes paho's code path, and a transport that has
        opted into nothing should take the identical path it took yesterday.
        """
        if not self._no_local:
            return None
        try:
            from paho.mqtt.subscribeoptions import SubscribeOptions
        except ImportError:                                  # pragma: no cover
            return None
        return SubscribeOptions(qos=1, noLocal=True)

    def stop(self):
        """Disconnect from MQTT broker."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # The link state lives in `self._link` (a `LinkHealth`, shared with
    # bus_client); these keep the names the rest of this class uses.
    @property
    def _connected(self) -> bool:
        return self._link.connected

    @_connected.setter
    def _connected(self, value: bool) -> None:
        self._link.connected = value

    @property
    def _connect_failures(self) -> int:
        return self._link.failures

    @_connect_failures.setter
    def _connect_failures(self, value: int) -> None:
        self._link.failures = value

    @property
    def _last_connect_error(self):
        return self._link.last_error

    @_last_connect_error.setter
    def _last_connect_error(self, value) -> None:
        self._link.last_error = value

    @property
    def _fail_log(self) -> dict:
        return self._link.fail_log

    def health(self) -> dict:
        """The link state behind `is_connected`, for a health check to read.

        `connected` IS `is_connected` (one signal, not a parallel one); the rest
        says why it is false: never started, or how many attempts have failed
        since the last success and with what error.
        """
        return {
            **self._link.snapshot(),
            "started": self._client is not None,
            "pending": len(self._pending),
        }

    def _on_connect_fail(self, client, userdata):
        """paho could not open the connection; its loop thread will retry."""
        self._connected = False
        self._record_failure(
            "connection attempt failed",
            f"MQTT connect to {self._broker}:{self._port} failed; retrying "
            f"with backoff {self.RECONNECT_MIN_DELAY_S}-"
            f"{self.RECONNECT_MAX_DELAY_S}s, {len(self._pending)} message(s) "
            f"held")

    def _record_failure(self, kind: str, first_line: str) -> None:
        """Count one failed attempt, and log it through the throttle."""
        self._link.record_failure(
            kind, first_line,
            f"MQTT connect to {self._broker}:{self._port} still failing ({kind})",
            extra=lambda: f", {len(self._pending)} message(s) held")

    def _throttled_log(self, level: int, kind: str, first_line: str,
                       summary: str) -> None:
        """Log a repeating failure without flooding.

        The first line of each KIND is logged at once. Repeats of that kind
        produce at most one summary line, with the count, per
        FAILURE_LOG_SUMMARY_S. Kinds are tracked separately, so a refused
        login (a CONNACK refusal, then paho's disconnect for the dropped
        socket) is two kinds thinned independently rather than two kinds
        that keep resetting each other.
        """
        self._link.throttled_log(
            level, kind, first_line, summary,
            extra=lambda: f", {len(self._pending)} message(s) held")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        # `properties` DEFAULTS so both callback arities work: MQTT 5 passes a
        # 5th argument, 3.1.1 does not. A TypeError raised here would happen
        # inside paho's network thread, where it would be swallowed and the
        # transport would simply never mark itself connected — a dead bridge
        # with no error, which is the failure class this whole change is about.
        #
        # `rc == 0` is correct under BOTH protocols: v5 delivers a
        # `ReasonCodes` whose __eq__ compares against an int by value, so
        # Success (0) still tests equal. Verified against paho 1.6.1.
        if rc == 0:
            failed = self._link.record_success()
            if failed:
                logger.info(f"MQTT connected after {failed} failed attempt(s)")
            logger.info(f"MQTT connected to {self._broker}:{self._port} "
                        f"(MQTT 5)")
            opts = self._subscribe_options()
            # Re-subscribe to all namespaces
            for ns in self._subscribers:
                self._sub(client, f"{self._topic_prefix}/+/{ns}", opts)
            # Re-subscribe wildcard pattern filters ([CXC-FL-PATTERN])
            for topic in self._pattern_topics:
                self._sub(client, topic, opts)
            # Subscribe to infrastructure topics
            self._sub(client, f"{self._topic_prefix}/+/_bus/#", opts)
            # Re-subscribe FOREIGN channels (peer protocols on this substrate).
            # NoLocal is NOT applied here even when enabled: a foreign channel is
            # another protocol's topic, and suppressing our own echo on it would
            # silently change a peer protocol's semantics from inside our
            # transport. Our loopback problem is on `composeio/` topics.
            for filt in self._foreign_subs:
                client.subscribe(filt, qos=1)
                client.message_callback_add(filt, self._on_foreign_message)
            self._flush_pending()
        else:
            # NAME THE REASON. A refused v5 CONNECT is the GOOD failure mode —
            # paho does not downgrade, so this is a loud stop rather than a
            # connection that quietly drops every property — but it is only good
            # if the log says what to do about it. Thinned like any repeated
            # failure: an auth error retried every 30 s must not log 2,900
            # identical lines a day.
            self._record_failure(
                f"CONNACK refused: {_connack_reason(rc)}",
                f"MQTT connection to {self._broker}:{self._port} REFUSED: "
                f"{_connack_reason(rc)}. This client requires MQTT 5 "
                f"(paho does NOT fall back to 3.1.1).")

    @staticmethod
    def _sub(client, topic: str, options) -> None:
        """Subscribe, passing v5 SubscribeOptions only when we have some."""
        if options is None:
            client.subscribe(topic, qos=1)
        else:
            client.subscribe(topic, options=options)

    def _flush_pending(self) -> int:
        """Publish everything held across the connect boundary, in order."""
        if not self._pending:
            return 0
        held, self._pending = self._pending, []
        for msg in held:
            self._send_now(msg)
        if self._pending_dropped:
            logger.warning(
                f"MQTT flushed {len(held)} pre-connect message(s); "
                f"{self._pending_dropped} older one(s) were dropped "
                f"(queue cap {self.PENDING_MAX})")
            self._pending_dropped = 0
        else:
            logger.info(f"MQTT flushed {len(held)} pre-connect message(s)")
        return len(held)

    def _on_disconnect(self, client, userdata, rc, properties=None):
        self._connected = False
        if rc != 0:
            # Thinned by rc: against a broker that refuses the login, paho calls
            # this after EVERY refused CONNACK, which unthinned was one WARNING
            # per attempt (about 2,880 a day at the 30 s cap). A drop after a
            # good connection still logs at once, because a successful CONNACK
            # clears the throttle.
            self._throttled_log(
                logging.WARNING, f"disconnect rc={rc}",
                f"MQTT unexpected disconnect (rc={rc}), will auto-reconnect",
                f"MQTT disconnects (rc={rc}) repeating")

    @staticmethod
    def _annotate_age(msg: BusMessage, payload: dict, mqtt_msg) -> None:
        """Stamp how this message ARRIVED: retained or live, and how old.

        THE PROBLEM THIS SOLVES. A retained message carries no age. The broker
        hands a late subscriber the last value with no indication whether it is
        one second or three days old, and a receiver's age counter starts at
        RECEIPT — so a stale retained value looks perfectly fresh at exactly
        the moment a subscriber is least able to detect it. That is the precise
        lie [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML] was ratified
        to prevent, and retain implemented without this would re-create it.

        THE MECHANISM. MQTT 5's Message Expiry Interval is decremented BY THE
        BROKER, so `age = published - remaining` is measured in broker-relative
        elapsed time and requires NO CLOCK AGREEMENT between publisher and
        subscriber. That matters because D4 rejected per-message timestamps on
        exactly that ground: `_bus/time` is one-way at one-second resolution
        and CH32V003 parts run on internal oscillators at +/-1% (~36 s/hour),
        so the clock-offset error would exceed the staleness being measured.
        Verified live against mqtt-broker.local: published 120, delivered
        111 to a subscriber 9 s later.

        UNKNOWN IS NOT ZERO. If either number is missing — an older publisher,
        a 3.1.1 peer, a foreign tool's retained value — the age is genuinely
        unknowable and is reported as None. Defaulting to 0.0 there would
        present an undatable value as brand new, which is the whole failure
        being engineered out. A live (non-retained) message is age 0.0 because
        it was just published, and that IS known.
        """
        msg.retained = bool(getattr(mqtt_msg, "retain", False))
        if not msg.retained:
            msg.age_s = 0.0
            return
        published = payload.get(EXPIRY_KEY)
        props = getattr(mqtt_msg, "properties", None)
        remaining = getattr(props, "MessageExpiryInterval", None)
        if published is None or remaining is None:
            msg.age_s = None            # UNKNOWN — deliberately not 0.0
            return
        # `max(0.0, ...)`: a broker returning more than was sent (clock skew, a
        # republished copy) must not yield a NEGATIVE age, which would read as
        # "from the future" and defeat any threshold comparison downstream.
        msg.age_s = max(0.0, float(published) - float(remaining))

    def _on_message(self, client, userdata, mqtt_msg):
        """Handle incoming MQTT message — deserialize and dispatch to subscribers."""
        try:
            payload = json.loads(mqtt_msg.payload.decode())
            msg = BusMessage.from_json(payload)
            self._annotate_age(msg, payload, mqtt_msg)

            # Dispatch to namespace-specific subscribers
            for cb in self._subscribers.get(msg.namespace, []):
                try:
                    cb(msg)
                except Exception as e:
                    logger.error(f"Subscriber callback error: {e}")

            # Dispatch to wildcard subscribers (bus-level dispatch)
            for cb in self._all_subscribers:
                try:
                    cb(msg)
                except Exception as e:
                    logger.error(f"All-subscriber callback error: {e}")
        except Exception as e:
            logger.error(f"Failed to process MQTT message: {e}")
