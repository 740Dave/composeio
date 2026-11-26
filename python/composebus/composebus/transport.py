"""Transport abstraction layer.

DP-CIO-6: Transport is an implementation detail, not an architectural decision.
The same message format works identically over I2C, CAN, MQTT, HTTP, or in-process.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
import time


class MessageType(Enum):
    STATE = "state"
    EVENT = "event"
    CONFIG_PUSH = "config_push"
    CONFIG_REQUEST = "config_request"
    HEARTBEAT = "heartbeat"
    DISCOVERY = "discovery"
    TIME_SYNC = "time_sync"
    BINDING = "binding"
    ACK = "ack"              # Code 8 — critical acknowledgement
    LOG_ENTRY = "log_entry"  # Code 9 — BP-EXT-LOG append-only entry


# ---------------------------------------------------------------------------
# Message type <-> binary wire code — THE ONE TABLE  [SPEC:CIO-BUS-PROTOCOL]
# ---------------------------------------------------------------------------
# Every framed substrate (UART, CAN, I2C) carries the type as a 4-bit code, and
# [BP-TYPE-CODES] allocates those codes exactly once. This dict IS that table in
# Python; transports import it, they do not restate it.
#
# WHY IT LIVES HERE AND NOT IN EACH TRANSPORT (2026-08-10). It used to live in
# each transport, and the copies drifted: transport_uart had 8 of 10 entries,
# transport_can had 2, services/can_mqtt_bridge had 5 — and all three ended in
# `.get(msg.type, 0)`, so an unlisted type was silently re-labelled STATE on the
# wire. A `type=ACK` message came back from a UART round-trip as a spurious
# STATE, and on CAN so did HEARTBEAT, DISCOVERY, CONFIG_PUSH and four others.
# `ack`=8 and `log_entry`=9 had been spec'd for months ([BP-TYPE-ACK],
# [BP-EXT-LOG]) and landed in the enum above; no fork ever got them. One table
# reused by reference is the only shape where adding a type is one edit.
MESSAGE_TYPE_WIRE_CODES: dict[MessageType, int] = {
    MessageType.STATE: 0,
    MessageType.EVENT: 1,
    MessageType.CONFIG_PUSH: 2,
    MessageType.CONFIG_REQUEST: 3,
    MessageType.HEARTBEAT: 4,
    MessageType.DISCOVERY: 5,
    MessageType.TIME_SYNC: 6,
    MessageType.BINDING: 7,
    MessageType.ACK: 8,
    MessageType.LOG_ENTRY: 9,
}
WIRE_CODE_MESSAGE_TYPES: dict[int, MessageType] = {
    v: k for k, v in MESSAGE_TYPE_WIRE_CODES.items()
}

#: Width of the type field on every binary transport. 4 bits — codes 0..15, of
#: which 0..9 are allocated and 10..15 are reserved ([BP-TYPE-CODES]).
WIRE_TYPE_CODE_BITS = 4
WIRE_TYPE_CODE_MASK = (1 << WIRE_TYPE_CODE_BITS) - 1


class UnmappedMessageType(ValueError):
    """A MessageType with no allocated wire code was handed to a binary encoder.

    Never silently downgraded to STATE: an ack or a heartbeat arriving as a
    state change is indistinguishable from a real one, which is precisely the
    defect this exception replaced ([BP-TYPE-CODES], no-silent-fallback).
    """


def wire_code_for_type(mtype: MessageType) -> int:
    """MessageType -> 4-bit wire code. RAISES rather than defaulting.

    Every member of `MessageType` is in the table (a test enforces exactly
    that), so this raise fires only when someone adds an enum member without
    allocating its code in the spec — which is the moment to fail, loudly, at
    the keyboard rather than a year later on a bus.
    """
    try:
        return MESSAGE_TYPE_WIRE_CODES[mtype]
    except KeyError:
        raise UnmappedMessageType(
            f"{mtype!r} has no wire code. Allocate one in [BP-TYPE-CODES] "
            f"(spec/01-composeio-bus-protocol.smd) and add it to "
            f"MESSAGE_TYPE_WIRE_CODES. Allocated: "
            f"{sorted((t.value, c) for t, c in MESSAGE_TYPE_WIRE_CODES.items())}"
        ) from None


class UnknownMessageTypeName(ValueError):
    """A wire `type` STRING that [BP-TYPE-CODES] never allocated.

    A `ValueError` subclass ON PURPOSE. `services/can_mqtt_bridge/bridge.py`
    and several transports already wrap `BusMessage.from_json` in
    `except (ValueError, KeyError, ...)` and COUNT the failure; sharpening the
    message must not change the class out from under them and turn a counted
    drop into a crash.
    """


#: WORDS THAT ARE REAL ComposeBus VOCABULARY BUT ARE NOT WIRE TYPES.
#:
#: Measured incident, 2026-08-15: six devices were sent a restart carrying
#: ``"type": "command"``. Every receiver refused it — `from_json` here, and
#: `bus_message_type_from_name()` in `cyd/src/bus_mqtt.cpp`, which walks the
#: same ten-entry table and returns early. The restart was a NO-OP on every
#: device on every substrate, and the only signal was six WARNING lines in one
#: host's journal.
#:
#: ⛔ THE WORD IS NOT A TYPO, WHICH IS EXACTLY WHY IT NEEDS A NAMED ENTRY HERE.
#: `state` vs `command` is genuine ComposeBus PROSE — the distinction between a
#: retained current value and an imperative that drives undo — used throughout
#: the specs. It was simply never allocated a wire code. The publisher reached
#: for the CONCEPT and typed it where the PROTOCOL VALUE goes.
#:
#: A bare "not a valid MessageType" invites the obvious wrong fix: adding
#: COMMAND to the enum. That would demand a [BP-TYPE-CODES] wire-code
#: allocation across Python, firmware and three substrates to express what
#: `state` already expresses. So the refusal names the substitute instead.
NON_WIRE_TYPE_WORDS: dict[str, str] = {
    # word -> the wire type that actually carries it
    "command": "state",
    "cmd": "state",
    "request": "state",
    "announcement": "state",
    "announce": "state",
    "value": "state",
    "telemetry": "state",
}


def message_type_for_name(name: "str | MessageType") -> MessageType:
    """Wire `type` string -> MessageType. RAISES, with a remedy, never guesses.

    Pass-through for a `MessageType`, so a caller that accepts ``str |
    MessageType`` needs no branch of its own.

    The whole value of this function over `MessageType(name)` is the MESSAGE.
    The bare enum raises ``'command' is not a valid MessageType``, which is
    true, unactionable, and points a reader straight at the one fix that must
    not be made.
    """
    if isinstance(name, MessageType):
        return name
    try:
        return MessageType(name)
    except ValueError:
        pass
    allocated = ", ".join(sorted(t.value for t in MessageType))
    substitute = NON_WIRE_TYPE_WORDS.get(str(name).strip().lower())
    if substitute:
        raise UnknownMessageTypeName(
            f"{name!r} is ComposeBus PROSE, not a wire type. The state/command "
            f"distinction is real vocabulary, but [BP-TYPE-CODES] "
            f"(spec/01-composeio-bus-protocol.smd) allocates exactly ten wire "
            f"values and {name!r} is not one of them — so EVERY receiver "
            f"refuses it, on every substrate, silently from the publisher's "
            f"point of view. Use {substitute!r}. "
            f"⛔ Do NOT add it to MessageType: a new type costs a wire-code "
            f"allocation in Python, in cyd/src/bus_wire_codes.h and in three "
            f"substrates, to express what {substitute!r} already expresses. "
            f"Allocated: {allocated}."
        ) from None
    raise UnknownMessageTypeName(
        f"{name!r} is not a ComposeBus wire type. [BP-TYPE-CODES] "
        f"(spec/01-composeio-bus-protocol.smd) allocates exactly these ten and "
        f"nothing may be added without a wire-code allocation: {allocated}."
    ) from None


def message_type_for_wire_code(code: int) -> MessageType | None:
    """4-bit wire code -> MessageType, or None if the code is unallocated.

    None, not a default. An unallocated code (10..15, or one a newer peer was
    taught) means this build cannot know what the frame is; the caller must log
    it and DROP the frame. Guessing STATE invents traffic that never happened.
    """
    return WIRE_CODE_MESSAGE_TYPES.get(code & WIRE_TYPE_CODE_MASK)


# ---------------------------------------------------------------------------
# Arbitration PRIORITY — [BP-WIRE-PRIORITY], ratified by David 2026-08-13
# ---------------------------------------------------------------------------
# Three bits, eight tiers, 0 most urgent. It rides in the top three bits of the
# CAN arbitration id ([BP-CAN-ID] v2) where a numerically LOWER id wins the bus.
# It is SENDER-SIDE ONLY: nothing decodes it, nothing routes on it, and a
# receiver that read it would be reading the sender's opinion of itself.
#
# WHY IT EXISTS. Until this table, `ns_hash` occupied the top 16 bits of the id,
# so arbitration priority was a pseudo-random function of the namespace NAME.
# `throttle/left` (0x03F6) won everything by luck; `_device/wsc6-6f4ce4/config`
# (0x8921) outranked `_device/hosyond-d1afe8/config` (0xC6B6) — two transfers of
# identical role and urgency, ranked by a MAC suffix; `shields` (0xCC50) was the
# lowest-priority thing on the bus. Renaming a signal silently re-ranked it.
#
# TIERS ARE NAMED BY URGENCY CLASS, NOT BY EXAMPLE. David's correction to an
# earlier draft that mixed namespaces and types into one flat list: "a tier named
# by example goes stale the moment someone adds a namespace; a tier named by its
# class tells you where the new thing goes."
#
#   0  alarm                  — a human reacts; late is *wrong*
#   1  live control           — closed loop, lag is visible
#   2  transaction control    — terminates something; must not be starved by
#                               the thing it terminates
#   3  time sync              — small, and its value IS its timeliness
#   4  liveness & coordination
#   5  live telemetry         — late is stale, not wrong        (THE DEFAULT)
#   6  display state
#   7  bulk                   — nothing functional may live here
#
# THE ASSIGNMENT RULE: control-plane messages take their tier from their TYPE;
# application data (nearly all of which is STATE/EVENT) takes it from its
# NAMESPACE. A new namespace is placed by asking *which class is this*, never
# *what is it like*.
#
# Test for any future placement: if this message were 30 ms late, would a player
# notice — and notice it as a bug, or as the game?
PRIORITY_BITS = 3
PRIORITY_MASK = (1 << PRIORITY_BITS) - 1

PRIORITY_ALARM = 0
PRIORITY_LIVE_CONTROL = 1
PRIORITY_TRANSACTION = 2
PRIORITY_TIME_SYNC = 3
PRIORITY_LIVENESS = 4
PRIORITY_TELEMETRY = 5
PRIORITY_DISPLAY = 6
PRIORITY_BULK = 7

#: The tier an application namespace gets when NO rule names it.
#:
#: A DEFAULT, NOT A FAILURE, and deliberately not the most urgent one. An
#: unlisted namespace is placed at "live telemetry" — below every control-plane
#: tier (2, 3, 4), so a namespace nobody has classified can never starve an ACK,
#: a TIME_SYNC or a HEARTBEAT, and above the bulk floor so it is not silently
#: buried either. `dradis/*` is the ratified example of this class and is
#: therefore deliberately ABSENT from the rules below: it lands here by rule. A
#: rule that restates the default is the kind of list that goes stale.
DEFAULT_PRIORITY = PRIORITY_TELEMETRY

#: TYPE -> tier, for the control plane only. A type mapped here takes its tier
#: from this table REGARDLESS of namespace. STATE and EVENT are absent on
#: purpose — they are application data and are placed by namespace.
MESSAGE_TYPE_PRIORITY: dict[MessageType, int] = {
    MessageType.ACK: PRIORITY_TRANSACTION,
    MessageType.CONFIG_REQUEST: PRIORITY_TRANSACTION,
    MessageType.TIME_SYNC: PRIORITY_TIME_SYNC,
    MessageType.HEARTBEAT: PRIORITY_LIVENESS,
    MessageType.DISCOVERY: PRIORITY_LIVENESS,
    MessageType.BINDING: PRIORITY_LIVENESS,
    MessageType.CONFIG_PUSH: PRIORITY_BULK,
    MessageType.LOG_ENTRY: PRIORITY_BULK,
}

#: NAMESPACE -> tier, as an ORDERED RULE LIST, not a lookup of every namespace.
#:
#: A pattern ending in `/` matches by PREFIX (the whole family); any other
#: pattern is an exact namespace. First match wins, so order is meaningful.
#: The firmware evaluates the same table with the same rule (bus_wire_codes.h,
#: generated and parity-tested), which is why the matcher is prefix/exact rather
#: than fnmatch — an MCU must be able to run it byte-for-byte identically.
#:
#: KEEP TIER 0 NEARLY EMPTY. If everything is urgent, nothing is.
NAMESPACE_PRIORITY_RULES: tuple[tuple[str, int], ...] = (
    ("alert_status", PRIORITY_ALARM),
    ("shields", PRIORITY_ALARM),
    ("alarm/", PRIORITY_ALARM),
    ("emergency/", PRIORITY_ALARM),
    ("throttle/", PRIORITY_LIVE_CONTROL),
    ("helm/", PRIORITY_LIVE_CONTROL),
    ("control/", PRIORITY_LIVE_CONTROL),
    # `_device/` IS THE CONTROL PLANE WEARING A NAMESPACE.
    #
    # MESSAGE_TYPE_PRIORITY already lifts a typed HEARTBEAT to the liveness
    # tier, but this fleet's C++ boards publish liveness as a STATE on
    # `_device/<id>/heartbeat` (announce.cpp), and STATE is deliberately absent
    # from the type table because it is application data. So the one message
    # class that says "this board still exists" was arbitrating in the
    # TELEMETRY pool, behind every gauge reading on the segment — and on a
    # loaded CAN bus that is precisely the traffic that loses, which would make
    # a healthy board look dead exactly when the bus is busy. The same applies
    # to `_device/<id>/restart`: a command you send to a wedged board should
    # not queue behind its telemetry.
    ("_device/", PRIORITY_LIVENESS),
    ("_app/", PRIORITY_DISPLAY),
)

#: Tiers a device DECLARED for its own namespaces, which beat the rule list.
#:
#: This is the seam workstream 0 plugs into: because a tier is a property of a
#: semantic class rather than of a name, a device announcing a namespace can
#: announce its tier too — so the assignment travels WITH the thing instead of
#: living in a table someone has to remember to maintain. Empty by default; the
#: rules above are the fallback, not the registry.
_declared_priority: dict[str, int] = {}


def register_namespace_priority(namespace: str, tier: int) -> None:
    """Declare the arbitration tier for a namespace (announcement path).

    Raises on an out-of-range tier rather than masking it: a tier of 9 masked to
    1 would silently promote bulk traffic above the control plane.
    """
    if not isinstance(tier, int) or not (0 <= tier <= PRIORITY_MASK):
        raise ValueError(
            f"priority tier {tier!r} for {namespace!r} is outside 0..7 "
            f"([BP-WIRE-PRIORITY]); 0 is most urgent, 7 is bulk")
    _declared_priority[namespace] = tier


def clear_declared_priorities() -> None:
    """Drop every declared tier. For tests and for a bus re-announce cycle."""
    _declared_priority.clear()


def priority_for_namespace(namespace: str) -> int:
    """Namespace -> tier, by rule, with a default. NEVER raises.

    An unlisted namespace gets `DEFAULT_PRIORITY`. That is the whole point of
    making this a rule instead of a list: a namespace nobody anticipated must
    get a sane tier, not an exception on a wire.
    """
    declared = _declared_priority.get(namespace)
    if declared is not None:
        return declared
    for pattern, tier in NAMESPACE_PRIORITY_RULES:
        if pattern.endswith("/"):
            if namespace.startswith(pattern):
                return tier
        elif namespace == pattern:
            return tier
    return DEFAULT_PRIORITY


def priority_for(namespace: str, mtype: MessageType) -> int:
    """(namespace, type) -> the 3-bit arbitration tier. NEVER raises.

    THE RULE, in one line: the control plane is placed by its TYPE, application
    data by its NAMESPACE. A CONFIG_PUSH of `alert_status` is still bulk — the
    urgency belongs to the transfer, not to the name it happens to carry.
    """
    tier = MESSAGE_TYPE_PRIORITY.get(mtype)
    if tier is not None:
        return tier
    return priority_for_namespace(namespace)


class UnserialisableValue(TypeError):
    """A message field JSON cannot represent, named at the point of framing.

    A `TypeError` subclass on purpose, so every existing `except TypeError`
    around a publish still catches it — this makes the failure LEGIBLE, it does
    not move it.

    THE DEFECT IT REPLACES (2026-08-12, blocker F2 of the fleet plan).
    `to_json()` put whatever was in `value` straight into `val`, so a
    CONFIG_PUSH — whose value is a chunk of OPAQUE BYTES with its own internal
    framing — reached `json.dumps` inside `transport_mqtt.publish` and raised a
    bare `TypeError: Object of type bytes is not JSON serializable`, attributed
    to whichever call site happened to serialise it, naming neither the
    namespace, nor the field, nor the reason. The transfer simply did not
    traverse the bridge, in either direction, and nothing said which message
    died.

    WHAT THIS IS *NOT*: a licence to base64 the value. Doing that would give the
    head and the leaf two different ideas of what a config frame is, for one
    substrate's convenience — a fork of the wire format, which is the class of
    mistake [BP-TYPE-CODES] and [BP-CAN-ID-DIVERGENCE] each cost this repo a
    week. Opaque bytes belong on a FRAMED substrate (UART, CAN) where the value
    goes on the wire verbatim; over a JSON substrate they belong behind a
    `bus://` reference (DP-CIO-18).
    """


#: Types `json.dumps` can represent with no encoder. `bytes` is deliberately
#: absent — that absence IS the check.
_JSON_SCALARS = (str, int, float, bool)


def _assert_json_serialisable(value, field: str, namespace: str,
                              mtype: "MessageType", _path: str = "") -> None:
    """Raise `UnserialisableValue` NAMING the field, or return.

    Walks containers, because a chunk hidden in `data["frame"]` fails exactly as
    badly as one in `val` and "somewhere in this message" is not a diagnosis.
    The walk is O(size) and only over containers; bus values are overwhelmingly
    scalars, which exit on the first isinstance.
    """
    if value is None or isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _assert_json_serialisable(v, field, namespace, mtype,
                                      f"{_path}[{k!r}]")
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _assert_json_serialisable(v, field, namespace, mtype,
                                      f"{_path}[{i}]")
        return

    where = f"{field}{_path}"
    if isinstance(value, (bytes, bytearray, memoryview)):
        n = len(bytes(value))
        raise UnserialisableValue(
            f"{namespace}: field {where!r} holds {n} RAW BYTES and this "
            f"transport serialises to JSON, which has no byte type. "
            f"({mtype.value} message.) Opaque binary — a "
            f"[SPEC:CXC-CONFIG-BUS] chunk is the case this was written for — "
            f"must ride a FRAMED substrate (UARTTransport, CANTransport), "
            f"where the value goes on the wire verbatim; over a JSON substrate "
            f"reference it by `bus://` URL instead (DP-CIO-18). It is NOT "
            f"base64'd here: that would make two substrates disagree about "
            f"what a frame is.")
    raise UnserialisableValue(
        f"{namespace}: field {where!r} holds a {type(value).__name__}, which "
        f"JSON cannot represent. ({mtype.value} message.) Convert it at the "
        f"publisher, where the type is known — `json.dumps` would raise the "
        f"same refusal without naming the message.")


@dataclass
class BusMessage:
    """A message on the ComposeIO bus.

    DP-CIO-18: Must be <256 bytes when serialized.
    Large payloads referenced by URL, never inline.
    """
    namespace: str
    type: MessageType
    value: Any = None
    source: str = ""
    timestamp: float = field(default_factory=time.time)
    scope: str = "auto"
    sequence: int = 0

    # Optional fields for specific message types
    event_type: str = ""  # For EVENT messages
    data: dict = field(default_factory=dict)  # For EVENT/DISCOVERY messages

    # --- RECEIVE-SIDE ANNOTATIONS. Not serialised; see `to_json`. -----------
    # These describe how a message ARRIVED, not what a publisher said, so they
    # are set by the receiving transport and never travel outward. That split
    # is deliberate: [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML]
    # ratified freshness as a RECEIVER-side property precisely because the
    # fleet has no shared time base.
    #
    #: True when the broker delivered this as a RETAINED message — i.e. it was
    #: published before this subscriber existed. A retained value is a
    #: perfectly good value; what it is not is EVIDENCE OF A LIVE PUBLISHER.
    retained: bool = False
    #: Age in seconds at the moment of receipt, or None for UNKNOWN.
    #:
    #: `None` IS NOT `0.0` AND THE DIFFERENCE IS THE WHOLE POINT. A retained
    #: message carries no age of its own and a receiver's counter starts at
    #: RECEIPT, so a three-day-old value looks brand new exactly when a
    #: subscriber is least able to notice. MQTT 5's Message Expiry Interval is
    #: decremented BY THE BROKER, so `age = published_expiry - remaining`
    #: needs no clock agreement between publisher and subscriber. When that
    #: information is absent (a 3.1.1 peer, an older publisher), the age is
    #: genuinely unknown and must say so — reporting 0.0 would present an
    #: undatable value as fresh, which is the precise lie retain must not tell.
    age_s: float | None = 0.0

    def to_json(self) -> dict:
        """Serialize to JSON-compatible dict (MQTT/local/simulator transport).

        Raises `UnserialisableValue` — a named `TypeError` — for a value JSON
        cannot carry, HERE, naming the namespace and the field, rather than
        letting `json.dumps` raise a bare `TypeError` at whichever call site
        happens to serialise the result.
        """
        _assert_json_serialisable(self.value, "val", self.namespace, self.type)
        if self.data:
            _assert_json_serialisable(self.data, "data", self.namespace,
                                      self.type)
        msg = {
            "ns": self.namespace,
            "type": self.type.value,
            "src": self.source,
            "ts": self.timestamp,
            "scope": self.scope,
            "seq": self.sequence,
        }
        if self.value is not None:
            msg["val"] = self.value
        if self.event_type:
            msg["evt"] = self.event_type
        if self.data:
            msg["data"] = self.data
        return msg

    @staticmethod
    def from_json(d: dict) -> BusMessage:
        """Deserialize from JSON dict.

        An unallocated `type` raises `UnknownMessageTypeName` (a `ValueError`),
        NAMING THE NAMESPACE. The 2026-08-15 restart incident produced six log
        lines that each named a topic and an enum error, and correlating them
        into "one publisher, one wrong word" was hand work.
        """
        try:
            mtype = message_type_for_name(d["type"])
        except UnknownMessageTypeName as e:
            raise UnknownMessageTypeName(
                f"{d.get('ns', '<no ns>')}: {e}") from None
        return BusMessage(
            namespace=d["ns"],
            type=mtype,
            value=d.get("val"),
            source=d.get("src", ""),
            timestamp=d.get("ts", 0),
            scope=d.get("scope", "auto"),
            sequence=d.get("seq", 0),
            event_type=d.get("evt", ""),
            data=d.get("data", {}),
        )

    def size_estimate(self) -> int:
        """Rough estimate of serialized size in bytes."""
        import json
        return len(json.dumps(self.to_json()))


class Transport(ABC):
    """Abstract transport binding."""

    @abstractmethod
    def send(self, message: BusMessage):
        """Send a message to the bus."""

    @abstractmethod
    def subscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Subscribe to messages on a namespace."""

    @abstractmethod
    def subscribe_all(self, callback: Callable[[BusMessage], None]):
        """Subscribe to ALL messages on this transport (wildcard).

        Used by bus._wire_transport_incoming() to dispatch incoming
        messages to namespace subscribers. Every concrete transport
        must implement this.
        """

    @abstractmethod
    def unsubscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Unsubscribe from a namespace."""

    def subscribe_pattern(self, pattern: str):
        """Ask the transport to RECEIVE traffic for every namespace matching
        a trailing-glob pattern ('prefix/*' or '*') — [CXC-FL-PATTERN].

        Delivery still flows through subscribe_all() dispatch; this call
        only widens what arrives.  Default: no-op — in-process transports
        (Local) already carry everything, and transports without a native
        wildcard simply cannot widen (documented degradation: pattern
        subscribers then see only namespaces this bus already exchanges).
        MQTT overrides with its native '#' wildcard.
        """
        return None

    def declare_refresh_intervals(self, intervals: dict[str, float]) -> None:
        """Tell the transport which namespaces DECLARED a periodic cadence.

        [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML] ratified a declared
        refresh interval per namespace, and MQTT's retain path was built to
        consume one — `MQTTTransport(refresh_intervals=...)` existed from the
        day retain landed and was PERMANENTLY EMPTY, because nothing in the stack
        could declare a cadence yet. This is the call that fills it.

        WHY IT IS PUSHED BY THE BUS RATHER THAN PULLED BY THE TRANSPORT. A
        transport that reached into the bus for `Namespace` objects would know
        about the layer above it, and the CAN/UART/HTTP transports would each
        acquire that coupling to use a fact only MQTT can act on. A push keeps
        the transport knowing nothing but a dict of names to seconds.

        Default: no-op. A substrate with no expiry concept has nothing to do
        with this, and silently ignoring it is correct rather than lossy — the
        freshness contract is enforced RECEIVER-side by design, so a transport
        that cannot help is not a transport that breaks it.
        """
        return None

    # ----------------------------------------------------------------- #
    # FOREIGN CHANNELS — a PEER protocol carried by the same substrate.
    # ----------------------------------------------------------------- #
    # [SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], extended 2026-08-11.
    #
    # THE PROBLEM THIS SOLVES, STATED PRECISELY. Three modules could not be
    # converted to the waist at all, and the reason was not laziness: they
    # relay `compose_projects/intelligence/<id>/{in,out}`, which is the composeio-channel MCP's
    # OWN wire (`channel.mjs`), NOT a ComposeBus namespace. It carries a bare
    # `{from, text}` payload on a bare topic — no envelope, no scope segment,
    # no `composeio/` prefix. `publish_state` cannot express it: it would
    # frame it as `composeio/global/intelligence/<id>/in` with a BusMessage
    # envelope, which is a different topic AND a different payload, i.e. a
    # wire change [CXC-SOB-CONTRACT-FROZEN] forbids.
    #
    # So the choice was: leave three modules on raw paho forever, or give the
    # waist a way to name a FOREIGN ADDRESS without naming a substrate. This
    # is the second. A foreign channel is an address in a peer protocol that
    # happens to be co-hosted on whatever substrate this transport speaks —
    # an MQTT topic here, a CAN arbitration-id range or a UART logical channel
    # elsewhere. The MODULE still never says "MQTT", never builds a client,
    # and never calls `loop_*`.
    #
    # WHY THIS IS A WAIST AND NOT A HOLE IN ONE. Three properties, each
    # load-bearing:
    #   1. It REFUSES a `composeio/` address (see `_reject_composeio_channel`).
    #      A module cannot use this to smuggle ordinary namespace traffic past
    #      `publish_state` — that is the abuse it would otherwise invite, and
    #      it fails loudly at the call, not in review.
    #   2. The DEFAULT is a LOUD refusal returning False, never a silent
    #      no-op. Today `channel_bridge` on a CAN-only device is DARK with no
    #      error — the exact defect [CXC-SOB-THESIS] exists to remove. After
    #      this, that same deployment logs "this transport cannot carry
    #      foreign channels" and the caller can see the refusal in its return
    #      value. Substrate-independence does not mean every substrate can do
    #      everything; it means the app learns when one cannot.
    #   3. It is a TRANSPORT capability, so adding CAN/UART carriage later is
    #      one override in one file, not an edit to every relay.
    FOREIGN_CHANNEL_FORBIDDEN_PREFIX = "composeio/"

    @staticmethod
    def _reject_composeio_channel(channel: str) -> None:
        """Refuse a foreign address that is really a ComposeBus topic.

        The one guard that keeps this seam from becoming the bypass it was
        added to retire. `publish_foreign("composeio/global/self/context", ...)`
        is a module hand-framing an envelope again, one layer down.
        """
        if str(channel).startswith(Transport.FOREIGN_CHANNEL_FORBIDDEN_PREFIX):
            raise ValueError(
                f"{channel!r} is a ComposeBus topic, not a foreign channel. "
                "The foreign-channel seam carries PEER protocols (the "
                "Intelligence channel's `compose_projects/intelligence/<id>/in`, a WLED device "
                "topic); ComposeBus traffic goes through "
                "`bus.publish_state(namespace, value)` so the transport owns "
                "the framing. Using this to hand-frame a `composeio/...` topic "
                "rebuilds the exact bypass [CXC-SOB-THESIS] removed.")

    def send_foreign(self, channel: str, payload, *, qos: int = 1) -> bool:
        """Carry *payload* to a FOREIGN *channel* on this substrate.

        Returns True iff the transport actually carried it. The default is a
        LOUD refusal — see the block comment above for why a silent no-op is
        the thing being fixed, not an acceptable fallback.
        """
        self._reject_composeio_channel(channel)
        import logging as _logging
        _logging.getLogger("composeio.transport").error(
            "%s cannot carry FOREIGN channel %r — this deployment's peer "
            "protocol traffic is NOT being delivered. Implement "
            "send_foreign() on this transport, or run the peer protocol over "
            "a substrate that has it ([CXC-SOB-THESIS]).",
            type(self).__name__, channel)
        return False

    def subscribe_foreign(self, channel: str, callback) -> bool:
        """Receive FOREIGN traffic on *channel*; `callback(channel, payload_bytes)`.

        The payload is delivered UNDECODED (bytes) — a foreign protocol owns
        its own encoding and the waist must not guess at it. Returns True iff
        the subscription was actually established.
        """
        self._reject_composeio_channel(channel)
        import logging as _logging
        _logging.getLogger("composeio.transport").error(
            "%s cannot subscribe FOREIGN channel %r — nothing will ever "
            "arrive on it, silently, which is the failure mode "
            "[CXC-SOB-THESIS] exists to remove.",
            type(self).__name__, channel)
        return False

    @abstractmethod
    def start(self):
        """Start the transport (connect, begin listening)."""

    @abstractmethod
    def stop(self):
        """Stop the transport (disconnect, cleanup)."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is connected and operational.

        MUST be a PROPERTY, and must be CHEAP. Every caller reads it as an
        attribute — `bus.status()`, `app.py`, `link_health.py`,
        `project_validator`, `memory_monitor` — 12 sites, none with parens, plus
        `link_health` doing `bool(getattr(transport, "is_connected", False))`.

        If a transport needs to PROBE (do I/O) to answer, that belongs in a
        separate `check_connection()`; a property that performs I/O is a trap for
        every one of those attribute reads.
        """

    def __init_subclass__(cls, **kwargs):
        """Refuse, at CLASS-DEFINITION time, a plain-method `is_connected`.

        WHY THIS GUARD EXISTS (2026-08-08). `@property @abstractmethod` states
        the intent but does NOT enforce it: Python's ABC machinery only checks
        that the NAME is defined, so a subclass writing

            def is_connected(self) -> bool: ...

        satisfies `__abstractmethods__` and instantiates cleanly — then hands
        every caller a BOUND METHOD, which is always truthy. A dead link reports
        CONNECTED forever, silently. That is not hypothetical: `CANTransport` was
        written exactly that way hours before this guard, and only a conformance
        test caught it.

        A test only covers transports living in THIS repo. This covers any
        subclass anywhere, and turns a silent runtime lie into an import-time
        TypeError — the earliest possible moment.
        """
        super().__init_subclass__(**kwargs)
        attr = cls.__dict__.get("is_connected")
        if attr is not None and not isinstance(attr, property):
            raise TypeError(
                f"{cls.__name__}.is_connected must be a @property, not "
                f"{type(attr).__name__}. Every caller reads it as an attribute "
                "(bus.status(), link_health, app.py, ...), so a plain method "
                "hands them an always-truthy bound object and a DEAD link "
                "reports CONNECTED. If answering requires I/O, add a separate "
                "check_connection() method instead.")
