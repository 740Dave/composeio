"""MQTT 5 is the NEGOTIATED protocol on every paho path in this repo.

David: "Seems like we should upgrade to MQTT5."

The groundwork landed with retain ([DEC-IMPLEMENT-MQTT-RETAIN-WITH-AN-AGE-CONTRACT]):
`MQTTTransport` now builds `mqtt.Client(protocol=mqtt.MQTTv5)` because the age
contract is UNAVAILABLE at 3.1.1 — PUBLISH properties do not exist there, so
`MessageExpiryInterval` is silently ignored and a retained message carries no age.

But `MQTTTransport` was never the only paho client in the tree.  `bus_client`
(the SHARED client every source and adaptor now funnels through), the CAN-MQTT
bridge, and the Intelligence launcher each built their own, all at paho's default
3.1.1.  "Make v5 the negotiated protocol" is only true when the client a message
actually leaves through speaks it, so these tests pin every construction site.

⛔ A SILENTLY DOWNGRADED CONNECTION IS WORSE THAN A REFUSED ONE, and that is a
property of the design here rather than a hope: paho does NOT fall back from v5
to 3.1.1.  A broker that cannot speak v5 REFUSES the CONNECT
(0x84 Unsupported Protocol Version) and the transport logs a failure, rather than
connecting happily and dropping every property on the floor.  See
`test_the_v5_reason_code_for_an_unsupported_protocol_is_reported_not_swallowed`.
"""
from __future__ import annotations

import sys
import types

import pytest

paho = pytest.importorskip("paho.mqtt.client")

from composeio.transport import BusMessage, MessageType   # noqa: E402
from composeio.transport_mqtt import (                    # noqa: E402
    MQTT5_ADOPTION,
    MQTTTransport,
)


class FakePahoClient:
    """Records what the code ASKED paho for.  No network."""

    instances: list["FakePahoClient"] = []

    def __init__(self, client_id="", protocol=None, **kw):
        self.client_id = client_id
        self.protocol = protocol
        self.kwargs = kw
        self.connect_calls = []
        self.subscribe_calls = []
        self.published = []
        self.will = None
        self.on_connect = None
        self.on_message = None
        self.on_disconnect = None
        FakePahoClient.instances.append(self)

    def username_pw_set(self, u, p=None):
        pass

    def will_set(self, topic, payload=None, qos=0, retain=False,
                 properties=None):
        self.will = dict(topic=topic, payload=payload, qos=qos, retain=retain,
                         properties=properties)

    def connect(self, host, port, keepalive=60, **kw):
        self.connect_calls.append(dict(host=host, port=port,
                                       keepalive=keepalive, **kw))

    # The transport starts with connect_async() so paho's loop thread retries a
    # failed first connect (test_transport_mqtt_reconnect.py). Recorded into the
    # same list: these tests assert WHAT is asked for, not which call asks.
    def connect_async(self, host, port, keepalive=60, **kw):
        self.connect(host, port, keepalive=keepalive, **kw)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def subscribe(self, topic, qos=0, options=None, properties=None):
        self.subscribe_calls.append(dict(topic=topic, qos=qos, options=options,
                                         properties=properties))

    def message_callback_add(self, sub, cb):
        pass

    def publish(self, topic, payload=None, qos=0, retain=False,
                properties=None):
        self.published.append(dict(topic=topic, payload=payload, qos=qos,
                                   retain=retain, properties=properties))


@pytest.fixture
def fake_paho(monkeypatch):
    FakePahoClient.instances = []
    monkeypatch.setattr(paho, "Client", FakePahoClient)
    return FakePahoClient


# --------------------------------------------------------------------------- #
# 1. EVERY paho CONSTRUCTION SITE NEGOTIATES v5
# --------------------------------------------------------------------------- #

def test_the_transport_negotiates_v5(fake_paho):
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    assert fake_paho.instances[-1].protocol == paho.MQTTv5


def test_bus_client_shared_publisher_negotiates_v5(fake_paho):
    """`bus_client` is the SHARED client [DX-NARROW-WAIST] funnels every source
    and adaptor through, so a 3.1.1 default here silently un-does the upgrade
    for most of the fleet's actual traffic."""
    import composeio.bus_client as bc

    bc._reset_for_tests()
    try:
        bc.acquire("mqtt://broker.invalid:1883", label="v5-probe")
        assert fake_paho.instances, "no client was constructed"
        assert all(i.protocol == paho.MQTTv5 for i in fake_paho.instances)
    finally:
        bc._reset_for_tests()


def test_no_paho_client_in_the_repo_is_left_at_the_3_1_1_DEFAULT():
    """A GREP TEST, and it is the one that keeps this true a year from now.

    Every `mqtt.Client(` in the tree must pass an explicit `protocol=`.  Nothing
    else catches the NEXT client somebody adds — and the defect this replaces was
    exactly that: `MQTTTransport` passed no `protocol=` for the whole life of the
    project and nobody noticed, because the default is invisible at the call site.
    """
    import io
    import pathlib
    import re
    import tokenize

    def strip_prose(src: str) -> str:
        """Blank out COMMENTS and STRINGS, preserving line numbers.

        `bus_client`'s own module docstring says "sources each constructed their
        own `mqtt.Client()`" — a sentence ABOUT the defect, which a raw regex
        reports as the defect. Tokenizing means the rule reads code and only
        code, so documenting a hazard never trips the guard against it.
        """
        try:
            toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return src
        lines = src.splitlines(keepends=True)
        out = list(lines)
        for tok in toks:
            if tok.type not in (tokenize.COMMENT, tokenize.STRING):
                continue
            (r1, c1), (r2, c2) = tok.start, tok.end
            for r in range(r1, r2 + 1):
                if r - 1 >= len(out):
                    break
                line = out[r - 1]
                a = c1 if r == r1 else 0
                b = c2 if r == r2 else len(line.rstrip("\n"))
                out[r - 1] = (line[:a] + " " * max(0, b - a)
                              + line[b:])
        return "".join(out)

    root = pathlib.Path(__file__).resolve().parents[2]
    pattern = re.compile(r"\bmqtt\.Client\s*\(")
    offenders = []
    scanned = 0
    for path in root.rglob("*.py"):
        # ⛔ RELATIVE parts, never absolute. Filtering on `path.parts` matched
        # the worktree's OWN prefix — this repo is checked out under
        # /private/tmp/... — so "tmp" excluded every file in the tree and this
        # test passed while scanning NOTHING. A guard that silently guards zero
        # files is worse than no guard, so `scanned` is asserted below.
        rel_parts = set(path.relative_to(root).parts)
        if rel_parts & {"site", ".git", "node_modules", "backups", "tmp"}:
            continue
        if path.name.startswith("test_") or "tests" in rel_parts:
            continue
        scanned += 1
        text = strip_prose(path.read_text(errors="ignore"))
        for m in pattern.finditer(text):
            # Read the call's argument list up to the balanced close paren.
            i, depth, buf = m.end() - 1, 0, []
            while i < len(text):
                ch = text[i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                buf.append(ch)
                i += 1
            if "protocol=" not in "".join(buf):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(root)}:{line}")
    assert scanned > 100, (
        f"the scan covered only {scanned} files — the exclusion filter is "
        f"eating the tree again and this assertion is guarding nothing")
    assert not offenders, (
        "these mqtt.Client() calls take paho's 3.1.1 DEFAULT — pass "
        "protocol=mqtt.MQTTv5 explicitly:\n  " + "\n  ".join(offenders))


# --------------------------------------------------------------------------- #
# 2. THE v5 CONNECT IS DECLARED, NOT DEFAULTED
# --------------------------------------------------------------------------- #

def test_connect_declares_a_clean_start_and_a_zero_session_expiry(fake_paho):
    """DECLINED DELIBERATELY: persistent sessions.

    Under v5, `clean_start` and Session Expiry Interval decide whether the broker
    queues missed QoS>0 messages for a disconnected subscriber.  We decline them
    and SAY SO in code rather than inheriting paho's `MQTT_CLEAN_START_FIRST_ONLY`
    default, because a ComposeIO subscriber wants CURRENT STATE, which retain now
    delivers, not a replay of every message it missed.  On a flaky RF link a
    persistent session turns a reconnect into a flood of superseded values —
    every one of which would arrive looking live.
    """
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    call = fake_paho.instances[-1].connect_calls[-1]
    assert call["clean_start"] is True
    props = call["properties"]
    assert props is not None
    assert props.SessionExpiryInterval == 0


def test_the_v5_reason_code_for_an_unsupported_protocol_is_reported_not_swallowed(
        fake_paho, caplog):
    """A BROKER THAT CANNOT SPEAK v5 MUST FAIL LOUDLY.

    paho performs NO automatic downgrade, so the failure mode is a refused
    CONNECT rather than a connection that silently drops every property.  What
    this test pins is that the refusal reaches a human with its REASON NAMED:
    `rc=134` alone sends the reader to a spec table, and the one thing an
    operator needs to know — "this broker is too old for the protocol we now
    require" — is precisely what the number hides.
    """
    import logging

    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    with caplog.at_level(logging.ERROR):
        t._on_connect(fake_paho.instances[-1], None, {}, 0x84, None)
    assert not t.is_connected
    text = caplog.text
    assert "0x84" in text or "134" in text
    assert "unsupported protocol version" in text.lower()
    assert "MQTT 5" in text


# --------------------------------------------------------------------------- #
# 3. THE LAST WILL — v5's Will Delay Interval, and D4's missing liveness half
# --------------------------------------------------------------------------- #

def test_a_will_is_registered_with_a_v5_will_delay(fake_paho):
    """ADOPTED: Last Will + Will Delay Interval.

    This is the strongest thing v5 buys this fleet after message expiry, and it
    is the only half of D4's per-node liveness that does NOT depend on the dead
    node being alive enough to report: the BROKER publishes the will when the
    connection drops.  Will Delay Interval is v5-only and is what stops a 3-second
    Wi-Fi reconnect from being announced as a death.
    """
    t = MQTTTransport(broker="broker.invalid", client_id="probe",
                      will_namespace="_bus/heartbeat",
                      will_value={"node": "probe", "online": False},
                      will_delay_s=10)
    t.start()
    will = fake_paho.instances[-1].will
    assert will is not None
    assert will["topic"] == "composeio/global/_bus/heartbeat"
    assert will["retain"] is True
    assert will["properties"].WillDelayInterval == 10
    import json
    assert json.loads(will["payload"])["val"]["online"] is False


def test_no_will_is_registered_when_none_is_declared(fake_paho):
    """OFF BY DEFAULT.  A will is a claim about a node's identity and liveness;
    inventing one for every transport would put a retained "offline" marker on a
    namespace the caller never asked to own."""
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    assert fake_paho.instances[-1].will is None


# --------------------------------------------------------------------------- #
# 4. NO LOCAL — measured to work, deliberately NOT default
# --------------------------------------------------------------------------- #

def test_no_local_is_available_and_off_by_default(fake_paho):
    """v5's NoLocal subscription option stops the broker echoing a client's own
    publishes back to it.  MEASURED against mqtt-broker.local 2026-08-15:
    with `noLocal=True` a client's own publish came back 0 times; without it, 1.

    THIS MATTERS MORE NOW THAN IT DID YESTERDAY.  `MQTTTransport` subscribes
    `composeio/+/_bus/#`, and `_bus/time` has just become a 1 Hz publisher — so
    every master bus now receives its OWN tick back, once a second, forever, and
    re-dispatches it to every local subscriber as though a peer had sent it.

    IT IS STILL OFF BY DEFAULT, and the reason is honest rather than timid:
    loopback is currently observable behaviour that this repo has never measured
    a dependency on either way, and flipping it globally in the same change that
    creates the echo would confound the two.  The flag is here, tested, with the
    measurement recorded; enabling it fleet-wide is a one-line default change
    behind a live-fleet observation.
    """
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    t._on_connect(fake_paho.instances[-1], None, {}, 0, None)
    for call in fake_paho.instances[-1].subscribe_calls:
        assert call["options"] is None

    t2 = MQTTTransport(broker="broker.invalid", client_id="probe2",
                       no_local=True)
    t2.start()
    inst = fake_paho.instances[-1]
    t2._on_connect(inst, None, {}, 0, None)
    assert inst.subscribe_calls, "nothing was subscribed"
    for call in inst.subscribe_calls:
        assert call["options"] is not None
        assert call["options"].noLocal is True


# --------------------------------------------------------------------------- #
# 5. THE ADOPTION LEDGER IS IN THE CODE, NOT IN A DOC NOBODY OPENS
# --------------------------------------------------------------------------- #

def test_every_v5_feature_is_explicitly_adopted_or_declined():
    """A feature with no entry is a feature nobody decided about.

    Same one-table discipline as `MESSAGE_TYPE_WIRE_CODES` and `RETAIN_BY_TYPE`:
    the upgrade's value is in the DECISIONS, and a decision that lives only in a
    commit message is a decision the next reader will re-litigate from scratch.
    """
    expected = {
        "message_expiry_interval",
        "will_delay_interval",
        "no_local",
        "session_expiry_interval",
        "retain_handling",
        "retain_as_published",
        "reason_codes",
        "user_properties",
        "topic_alias",
        "shared_subscriptions",
        "subscription_identifiers",
        "request_response",
        "flow_control",
        "maximum_packet_size",
    }
    assert set(MQTT5_ADOPTION) == expected
    for name, entry in MQTT5_ADOPTION.items():
        assert entry["status"] in {"adopted", "declined", "deferred"}, name
        assert entry["why"].strip(), f"{name} has no reasoning"


def test_the_measured_broker_capabilities_are_recorded_with_their_date():
    """What the broker actually declared, so the next reader does not re-measure
    — and, more importantly, so a CHANGE at the broker is detectable as a
    difference from a recorded number rather than a vibe."""
    from composeio.transport_mqtt import MQTT5_BROKER_MEASURED

    assert MQTT5_BROKER_MEASURED["host"] == "mqtt-broker.local:1883"
    assert MQTT5_BROKER_MEASURED["connack"]["ReceiveMaximum"] == 20
    assert MQTT5_BROKER_MEASURED["connack"]["TopicAliasMaximum"] == 10
    # NOT declared by this broker => the v5 defaults apply, and both matter:
    # no MaximumPacketSize means our ~110 KB console payloads are safe, and no
    # RetainAvailable=0 means retain is permitted at all.
    assert MQTT5_BROKER_MEASURED["connack"].get("MaximumPacketSize") is None
    assert MQTT5_BROKER_MEASURED["connack"].get("RetainAvailable") is None


# --------------------------------------------------------------------------- #
# 6. WHAT BREAKS — the 3.1.1 peers, pinned as behaviour rather than a warning
# --------------------------------------------------------------------------- #

def test_a_retained_value_from_a_3_1_1_publisher_is_UNDATABLE(fake_paho):
    """MEASURED against the live broker 2026-08-15: a v5 subscriber receiving a
    retained message published by a 3.1.1 client gets `MessageExpiryInterval =
    None`.

    ⛔ THE FLEET CONSEQUENCE.  `firmware/reactive_core` and
    `firmware/esp32_mqtt_client` use Arduino **PubSubClient**, which implements
    3.1.1 ONLY and has no v5 path at all — and that firmware is the THROTTLE, the
    fleet's most-watched publisher.  Its retained values can never carry an age.
    The correct behaviour is therefore `age_s = None` (UNKNOWN), never 0.0, and
    that is what this pins: the upgrade must not make an undatable value look
    fresh just because the SUBSCRIBER got smarter.
    """
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    msg = BusMessage(namespace="throttle/left", type=MessageType.STATE, value=42)
    mqtt_msg = types.SimpleNamespace(retain=1, properties=None)
    t._annotate_age(msg, {"ns": "throttle/left", "val": 42}, mqtt_msg)
    assert msg.retained is True
    assert msg.age_s is None


def test_a_v5_publisher_still_reaches_a_3_1_1_subscriber(fake_paho):
    """MEASURED 2026-08-15: the broker translates.  A 3.1.1 subscriber received
    a v5 client's retained publish intact, payload byte-identical — it simply had
    no `properties` attribute at all.

    Which is exactly why the published expiry rides in the JSON ENVELOPE as
    `exp` and not solely in a v5 property: the envelope crosses the version
    boundary and the property does not.
    """
    t = MQTTTransport(broker="broker.invalid", client_id="probe")
    t.start()
    inst = fake_paho.instances[-1]
    t._on_connect(inst, None, {}, 0, None)
    t.send(BusMessage(namespace="throttle/left", type=MessageType.STATE,
                      value=42, scope="global"))
    import json
    body = json.loads(inst.published[-1]["payload"])
    assert body["exp"] == MQTTTransport.RETAIN_EXPIRY_DEFAULT_S
    assert inst.published[-1]["retain"] is True


# --------------------------------------------------------------------------- #
# 7. THE [D4] SEAM — declared cadences reaching the expiry path
# --------------------------------------------------------------------------- #
def test_bus_start_HANDS_the_declarations_to_its_transports():
    """The seam is CONNECTED, not merely connectable.

    `MQTTTransport(refresh_intervals=...)` shipped with retain and was
    permanently empty, because nothing in the stack could declare a cadence.
    Wiring it at `bus.start()` is what turns `_bus/time`'s retained copy from a
    24-hour horizon into D4's 3-second staleness timeout — the broker dropping
    it at the same instant the freshness contract would call it stale.
    """
    from composeio.bus import ComposeIOBus
    from composeio.transport_mqtt import MQTTTransport

    t = MQTTTransport(broker="broker.invalid", client_id="seam")
    assert t._refresh_intervals == {}, "precondition: the seam starts empty"

    bus = ComposeIOBus(bus_id="seam-app", global_transport=t)
    bus.register_namespace("beacon/tick", "json", refresh_interval=2.0)
    bus.start(announce_time=False)
    try:
        assert t._refresh_intervals["_bus/time"] == 1.0
        assert t._refresh_intervals["beacon/tick"] == 2.0
        # And the expiry actually derives from it: 3x1.0 s, not 86400.
        from composeio.transport import BusMessage, MessageType
        tick = BusMessage(namespace="_bus/time", type=MessageType.TIME_SYNC,
                          value=1)
        assert t._expiry_for(tick) == 3
    finally:
        bus.stop()


def test_a_transport_without_the_method_does_not_break_bus_start():
    """A third-party or test-double transport predating this method must
    degrade to the 24 h horizon, not fail the bus."""
    from composeio.bus import ComposeIOBus

    class OldTransport:
        is_connected = True

        def start(self): pass

        def stop(self): pass

        def send(self, m): pass

        def subscribe(self, ns, cb): pass

        def subscribe_all(self, cb): pass

    bus = ComposeIOBus(bus_id="old", global_transport=OldTransport())
    bus.start(announce_time=False)
    bus.stop()
