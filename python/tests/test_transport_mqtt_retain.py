"""[BP-MQTT-QOS] retain, and the AGE CONTRACT that has to travel with it.

TWO THINGS ARE UNDER TEST AND THEY ARE NOT SEPARABLE.

1. RETAIN HAPPENS AT ALL. spec/01 §6.2.2's QoS table marks `state`,
   `heartbeat`, `discovery`, `time_sync` and `binding` as Retain=Yes, and
   §2.1 states the core semantic of a state message as "new subscribers
   receive the last published state". None of it happened: `MQTTTransport`
   never passed `retain` to `publish()`, so paho's default `retain=False`
   applied universally. The symptom reached the glass as "when a new thrust
   widget pops up, I have to wiggle the thrust levers for them to see any
   data" — wiggling the levers is a HUMAN-SUPPLIED RETAIN.

2. A RETAINED MESSAGE MUST NOT LOOK FRESH. A retained message carries no age
   of its own, and a receiver's age counter starts at RECEIPT — so a
   three-day-old retained value looks brand new at exactly the moment a
   subscriber is least able to detect it. Implemented naively, retain
   re-creates the precise lie [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML]
   was ratified to prevent. MQTT 5's Message Expiry Interval is the mechanism:
   the BROKER decrements it, so age is measured in broker-relative elapsed
   time and NO SHARED CLOCK IS NEEDED — which is the same property D4 chose
   receiver-side ages for in the first place.

   VERIFIED LIVE against mqtt-broker.local:1883 before this was written:
   an MQTT 5 CONNECT succeeds, and a retained publish sent with
   MessageExpiryInterval=120 was delivered to a late subscriber 9s later
   carrying 111. The broker decrements. See `test_age_is_published_minus_remaining`.

Ref: [DEC-IMPLEMENT-MQTT-RETAIN-WITH-AN-AGE-CONTRACT]
"""

from __future__ import annotations

import json

import pytest

from composeio.transport import BusMessage, MessageType
from composeio.transport_mqtt import MQTTTransport, RETAIN_BY_TYPE


class FakeClient:
    """Records publishes the way paho would receive them — INCLUDING `retain`
    and `properties`, which the old fake silently discarded."""

    def __init__(self):
        self.published: list[dict] = []
        self.subscribed: list[str] = []

    def publish(self, topic, payload, qos=0, retain=False, properties=None):
        self.published.append({
            "topic": topic, "payload": payload, "qos": qos,
            "retain": retain, "properties": properties,
        })

    def subscribe(self, topic, qos=1):
        self.subscribed.append(topic)

    def message_callback_add(self, *a):
        pass


def _wired(**kw) -> tuple[MQTTTransport, FakeClient]:
    t = MQTTTransport(broker="test.local", client_id="t", **kw)
    fake = FakeClient()
    t._client = fake
    t._connected = True
    return t, fake


def _msg(ns: str, value, mtype=MessageType.STATE) -> BusMessage:
    return BusMessage(namespace=ns, value=value, type=mtype, scope="global")


# --------------------------------------------------------------------------- #
# 1. RETAIN, PER THE SPEC TABLE — and only per the spec table.
# --------------------------------------------------------------------------- #

def test_state_is_published_retained():
    """THE BUG: this was `retain=False` for every message ever sent."""
    t, fake = _wired()
    t.send(_msg("throttle/left", 42))
    assert fake.published[0]["retain"] is True, (
        "state was not retained — a late subscriber gets nothing until the "
        "next publish, which for a throttle means until a human wiggles it")


def test_event_is_NOT_retained():
    """Events are transient. A retained event would be re-fired at every new
    subscriber — a button press replayed forever."""
    t, fake = _wired()
    t.send(_msg("ftl_arm", "pressed", MessageType.EVENT))
    assert fake.published[0]["retain"] is False


@pytest.mark.parametrize("mtype,expected", [
    (MessageType.STATE, True),
    (MessageType.EVENT, False),
    (MessageType.CONFIG_PUSH, False),
    (MessageType.CONFIG_REQUEST, False),
    (MessageType.HEARTBEAT, True),
    (MessageType.DISCOVERY, True),
    (MessageType.TIME_SYNC, True),
    (MessageType.BINDING, True),
])
def test_retain_matches_the_spec_table(mtype, expected):
    """spec/01 §6.2.2 [BP-MQTT-QOS], transcribed row for row. Retain is NOT
    blanket — the table is the contract."""
    t, fake = _wired()
    t.send(_msg("ns", "v", mtype))
    assert fake.published[0]["retain"] is expected


@pytest.mark.parametrize("mtype", [MessageType.ACK, MessageType.LOG_ENTRY])
def test_types_absent_from_the_table_are_not_retained(mtype):
    """`ack` and `log_entry` post-date the table. Defaulting an unlisted type
    to NOT retained is the safe direction: a wrongly-retained value is a lie
    that persists in the broker, a wrongly-unretained one costs one publish.
    An `ack` is a correlation response and a `log_entry` is one item of an
    append-only stream — retaining either is meaningless."""
    t, fake = _wired()
    t.send(_msg("ns", "v", mtype))
    assert fake.published[0]["retain"] is False


def test_retain_table_covers_every_message_type():
    """A type added to the enum without a retain decision would silently
    inherit `False`. Force the decision to be explicit — this is the same
    drift that made MESSAGE_TYPE_WIRE_CODES one table instead of four."""
    assert set(RETAIN_BY_TYPE) == set(MessageType), (
        "a MessageType has no explicit retain decision: "
        f"{set(MessageType) - set(RETAIN_BY_TYPE)}")


# --------------------------------------------------------------------------- #
# 2. THE AGE CONTRACT.
# --------------------------------------------------------------------------- #

def test_retained_publish_carries_a_message_expiry_interval():
    """Without an expiry the broker keeps the value forever with no age, and
    a late subscriber cannot tell one second from three days."""
    t, fake = _wired()
    t.send(_msg("throttle/left", 42))
    props = fake.published[0]["properties"]
    assert props is not None, "retained publish carried no MQTT 5 properties"
    assert props.MessageExpiryInterval > 0


def test_non_retained_publish_carries_no_expiry():
    """An expiry on a non-retained message is meaningless — it only governs
    how long the broker holds an undelivered/retained copy."""
    t, fake = _wired()
    t.send(_msg("ftl_arm", "pressed", MessageType.EVENT))
    assert fake.published[0]["properties"] is None


def test_expiry_is_derived_from_a_declared_refresh_interval():
    """[D4] timeout convention: 3x the declared interval, floored at
    interval + 100ms. Setting the expiry TO the staleness timeout composes
    the two mechanisms exactly — the broker deletes the retained value at
    the same instant the freshness contract would declare it stale, so a
    value that would be stale on arrival is never delivered at all."""
    t, fake = _wired(refresh_intervals={"_bus/heartbeat": 10.0})
    t.send(_msg("_bus/heartbeat", "alive", MessageType.HEARTBEAT))
    assert fake.published[0]["properties"].MessageExpiryInterval == 30


def test_expiry_falls_back_to_the_horizon_when_no_interval_is_declared():
    """THE THROTTLE CASE, and it is the important one. The throttle firmware
    is rate-limited AND change-gated ("quiet-bus by construction"), so the
    fleet's most-watched namespace is event-driven with NO declared cadence —
    D4's "3x the interval" has nothing to multiply.

    A short expiry here would delete the retained value during normal silence
    and bring back the exact symptom retain is being added to fix. So the
    fallback is a long HORIZON, and honesty is supplied by the age instead:
    a long horizon WITH an exact age is truthful; a short horizon WITHOUT one
    is the lie. Age, not deletion, is what makes retain safe."""
    t, fake = _wired()
    t.send(_msg("throttle/left", 42))
    exp = fake.published[0]["properties"].MessageExpiryInterval
    assert exp == MQTTTransport.RETAIN_EXPIRY_DEFAULT_S
    assert exp >= 3600, "the horizon is short enough to delete a quiet throttle"


def test_published_expiry_is_carried_in_the_envelope():
    """The receiver computes age as (published expiry - remaining expiry), so
    it must know what was originally sent. Carried in the payload rather than
    inferred from a constant, because publisher and subscriber are separate
    deployments that will not upgrade together."""
    t, fake = _wired()
    t.send(_msg("throttle/left", 42))
    body = json.loads(fake.published[0]["payload"])
    assert body["exp"] == MQTTTransport.RETAIN_EXPIRY_DEFAULT_S


# --------------------------------------------------------------------------- #
# 3. RECEIVE SIDE — a retained value must arrive ADMITTING its age.
# --------------------------------------------------------------------------- #

class FakeIncoming:
    def __init__(self, topic, body, retain=False, remaining=None):
        self.topic = topic
        self.payload = json.dumps(body).encode()
        self.retain = retain
        self.properties = _Props(remaining) if remaining is not None else None


class _Props:
    def __init__(self, remaining):
        self.MessageExpiryInterval = remaining


def _deliver(t, mqtt_msg) -> BusMessage:
    got: list[BusMessage] = []
    t.subscribe_all(got.append)
    t._on_message(None, None, mqtt_msg)
    assert got, "message was not dispatched"
    return got[0]


def test_age_is_published_minus_remaining():
    """The live probe measured exactly this: sent 120, received 111 after 9s.
    Age is broker-relative, so it needs no shared clock between publisher and
    subscriber — the property that makes this acceptable under D4."""
    t, _ = _wired()
    msg = _deliver(t, FakeIncoming(
        "composeio/global/throttle/left",
        {"ns": "throttle/left", "type": "state", "val": 42, "src": "t",
         "ts": 0, "scope": "global", "seq": 1, "exp": 120},
        retain=True, remaining=111))
    assert msg.retained is True
    assert msg.age_s == 9


def test_a_live_message_has_age_zero_and_is_not_marked_retained():
    t, _ = _wired()
    msg = _deliver(t, FakeIncoming(
        "composeio/global/throttle/left",
        {"ns": "throttle/left", "type": "state", "val": 42, "src": "t",
         "ts": 0, "scope": "global", "seq": 1, "exp": 120},
        retain=False, remaining=120))
    assert msg.retained is False
    assert msg.age_s == 0.0


def test_retained_with_no_expiry_information_reports_age_UNKNOWN_not_zero():
    """THE WHOLE POINT. A retained message from a publisher that sent no
    expiry (an old deployment, or a 3.1.1 peer) has an age nobody can
    compute. It must arrive as None — UNKNOWN — and NOT as 0.0.

    Reporting 0.0 would be the lie stated precisely: a value of unknown age
    presented as brand new. `None` forces a consumer to decide what to do
    about a value it cannot date; `0.0` silently decides for it, wrongly."""
    t, _ = _wired()
    msg = _deliver(t, FakeIncoming(
        "composeio/global/throttle/left",
        {"ns": "throttle/left", "type": "state", "val": 42, "src": "t",
         "ts": 0, "scope": "global", "seq": 1},
        retain=True, remaining=None))
    assert msg.retained is True
    assert msg.age_s is None, "an undatable retained value was reported FRESH"


def test_age_never_goes_negative():
    """A broker that hands back more than was sent (clock skew, a
    republished value) must not produce a negative age."""
    t, _ = _wired()
    msg = _deliver(t, FakeIncoming(
        "composeio/global/x",
        {"ns": "x", "type": "state", "val": 1, "src": "t", "ts": 0,
         "scope": "global", "seq": 1, "exp": 100},
        retain=True, remaining=140))
    assert msg.age_s == 0.0


# --------------------------------------------------------------------------- #
# 4. THE PROTOCOL VERSION THE WHOLE MECHANISM DEPENDS ON.
# --------------------------------------------------------------------------- #

def test_client_is_constructed_as_mqtt_5(monkeypatch):
    """Message Expiry Interval EXISTS ONLY IN MQTT 5. paho's default protocol
    is MQTTv311 (4) and the transport passed no `protocol=`, so every claim
    above would silently degrade to 3.1.1 — where the properties argument is
    ignored and retain carries no age at all."""
    import paho.mqtt.client as paho

    seen = {}

    class _C:
        def __init__(self, client_id=None, protocol=None, **kw):
            seen["protocol"] = protocol

        def username_pw_set(self, *a): pass
        def connect(self, *a, **kw): pass
        def connect_async(self, *a, **kw): pass
        def reconnect_delay_set(self, *a, **kw): pass
        def loop_start(self): pass

    monkeypatch.setattr(paho, "Client", _C)
    t = MQTTTransport(broker="test.local", client_id="t")
    t.start()

    assert seen["protocol"] == paho.MQTTv5


def test_v5_connect_callback_signature_is_accepted():
    """MQTT 5 calls `on_connect` with a 5th `properties` argument and a
    `ReasonCodes` rc instead of an int. If the callback raised on the extra
    argument the transport would never mark itself connected — and would fail
    SILENTLY, inside a paho thread."""
    t, fake = _wired()
    t._connected = False
    from paho.mqtt.reasoncodes import ReasonCodes
    from paho.mqtt.packettypes import PacketTypes

    t._on_connect(fake, None, {}, ReasonCodes(PacketTypes.CONNACK,
                                              identifier=0), None)
    assert t.is_connected is True


def test_v5_disconnect_callback_signature_is_accepted():
    t, fake = _wired()
    t._on_disconnect(fake, None, 0, None)
    assert t.is_connected is False
