"""The FOREIGN-CHANNEL seam — the waist's answer to "a peer protocol shares my substrate".

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], added 2026-08-11.

WHY THIS SEAM EXISTS, AND WHY IT NEEDED ITS OWN TESTS
-----------------------------------------------------
Three modules could not be converted to the bus AT ALL, and the reason was
structural rather than effortful. `channel_bridge`, `dsd_viewer_launcher` and
`compose_projects/intelligence/console/chat.py` all address `compose_projects/intelligence/<id>/{in,out}` — the
composeio-channel MCP's own wire (`channel.mjs`), a BARE topic carrying a BARE
`{from, text}` payload. `bus.publish_state` cannot express that: it would emit
`composeio/global/intelligence/<id>/in` wrapped in a BusMessage envelope, which
is a different topic AND a different payload, i.e. exactly the wire change
[CXC-SOB-CONTRACT-FROZEN] forbids. So they sat on the allow-list through six
slices with an honest rationale.

The fix is `Transport.send_foreign` / `subscribe_foreign`, surfaced on the bus as
`publish_foreign` / `subscribe_foreign`. A module names a peer-protocol ADDRESS;
the transport still owns the substrate.

THE DANGER, NAMED: this is the shape of a bypass. "A way to publish arbitrary
bytes to an arbitrary address" is precisely what the last two years of this arc
have been removing. Four properties keep it a waist rather than a hole, and each
one below is a test:

  1. A `composeio/` address RAISES. The seam cannot be used to hand-frame
     ordinary namespace traffic — which is the abuse it would otherwise invite,
     and which would sail past the paho seam-lint because it imports no paho.
  2. An unsupported substrate REFUSES LOUDLY and returns False; never a silent
     no-op. Before this seam, `channel_bridge` on a CAN-only device was simply
     DARK — no error, no gauge, the human's messages just stopped. Silence is
     the failure mode being removed, so the fallback must not reintroduce it.
  3. A local-only bus (no global transport) is the same loud False.
  4. A transport DOUBLE that predates the seam degrades to False rather than
     raising AttributeError inside a message callback. Half this repo's tests
     hand the bus a duck-typed recorder; a new bus method that explodes on them
     converts a missing capability into a crash in unrelated code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.bus import ComposeIOBus                        # noqa: E402
from composeio.transport import Transport                     # noqa: E402
from composeio.transport_mqtt import MQTTTransport            # noqa: E402


class _NoForeignTransport(Transport):
    """A substrate with NO foreign carriage — a CAN or UART segment, in effect.

    It deliberately does NOT override `send_foreign`/`subscribe_foreign`, so it
    inherits the ABC's refusing default. That inheritance IS the contract under
    test: every transport in the repo, present and future, gets the loud
    behaviour for free and has to opt IN to carrying a peer protocol.
    """

    def __init__(self):
        self.sent = []

    def send(self, message): self.sent.append(message)
    def subscribe(self, namespace, callback): pass
    def subscribe_all(self, callback): pass
    def unsubscribe(self, namespace, callback): pass
    def start(self): pass
    def stop(self): pass

    @property
    def is_connected(self) -> bool: return True


class _DuckTypedRecorder:
    """A transport-SHAPED double with no `Transport` base — the pattern used all
    over this suite, written before the foreign seam existed."""

    def __init__(self): self.sent = []
    def send(self, message): self.sent.append(message)
    def subscribe(self, namespace, callback): pass
    def subscribe_all(self, callback): pass
    def subscribe_pattern(self, pattern): pass
    def start(self): pass
    def stop(self): pass

    @property
    def is_connected(self): return True


# --------------------------------------------------------------------------- #
# property 1 — the seam REFUSES a ComposeBus address
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [
    "composeio/global/self/context",
    "composeio/local/_app/doc/open",
    "composeio/global/intelligence/architect/in",   # the tempting one
])
def test_a_composeio_topic_is_refused_on_every_leg(bad):
    """The guard that stops this becoming the next bypass.

    The third case is the one that matters: a relay could "helpfully" publish the
    composeio-scheme copy of a channel message through here instead of through
    `publish_state`, hand-framing its own envelope again. That is the defect
    [CXC-SOB-THESIS] removed, rebuilt one layer down, and it would pass the paho
    seam-lint because no paho is imported. It must fail at the CALL.
    """
    bus = ComposeIOBus(bus_id="t", global_transport=MQTTTransport(broker="x"))
    with pytest.raises(ValueError, match="not a foreign channel"):
        bus.publish_foreign(bad, {"x": 1})
    with pytest.raises(ValueError, match="not a foreign channel"):
        bus.subscribe_foreign(bad, lambda c, p: None)


def test_the_refusal_explains_what_to_do_instead():
    """A refusal that does not name the alternative gets worked around."""
    with pytest.raises(ValueError) as exc:
        Transport._reject_composeio_channel("composeio/global/x")
    msg = str(exc.value)
    assert "publish_state" in msg, msg
    assert "peer protocol" in msg.lower() or "PEER" in msg, msg


@pytest.mark.parametrize("ok", [
    "intelligence/architect/in",
    "intelligence/architect/channel/alive",
    "wled/livingroom/api",
    "thread/architect/narration",     # a namespace-SHAPED name is still not `composeio/`
])
def test_a_genuine_foreign_address_is_accepted(ok):
    """No over-broad refusal: the real callers' channels must pass the guard."""
    Transport._reject_composeio_channel(ok)          # must not raise


# --------------------------------------------------------------------------- #
# property 2 — an unsupported substrate refuses LOUDLY, never silently
# --------------------------------------------------------------------------- #
def test_a_transport_without_foreign_carriage_returns_false_and_logs(caplog):
    """The CAN/UART deployment case — the whole reason this is a waist.

    Today `channel_bridge` on a CAN-only device would be DARK: it publishes into
    the void with no error. After this seam it returns False and says so, so the
    caller can surface it (both `channel_bridge` and `chat.py` do).
    """
    bus = ComposeIOBus(bus_id="t", global_transport=_NoForeignTransport())
    with caplog.at_level("ERROR"):
        assert bus.publish_foreign("intelligence/a/in", {"x": 1}) is False
        assert bus.subscribe_foreign("intelligence/a/in", lambda c, p: None) is False
    text = caplog.text
    assert "cannot carry" in text or "cannot subscribe" in text, text
    assert "intelligence/a/in" in text, "the refusal must NAME the dark channel"


def test_a_refusal_is_never_a_silent_no_op():
    """Pinning the RETURN VALUE, not just the log.

    A caller that cannot distinguish "delivered" from "dropped" will report
    success either way — which is how the original defect stayed invisible. The
    boolean is what `channel_bridge` prints "!! REFUSED by transport" from.
    """
    t = _NoForeignTransport()
    assert t.send_foreign("intelligence/a/in", b"x") is False
    assert t.subscribe_foreign("intelligence/a/in", lambda c, p: None) is False
    assert t.sent == [], "a refused foreign frame must not leak onto the state path"


# --------------------------------------------------------------------------- #
# property 3 — a local-only bus is loud too
# --------------------------------------------------------------------------- #
def test_a_local_only_bus_refuses_loudly(caplog):
    bus = ComposeIOBus(bus_id="t")               # no global transport at all
    with caplog.at_level("ERROR"):
        assert bus.publish_foreign("intelligence/a/in", {"x": 1}) is False
        assert bus.subscribe_foreign("intelligence/a/in", lambda c, p: None) is False
    assert "local-only" in caplog.text, caplog.text


# --------------------------------------------------------------------------- #
# property 4 — a pre-existing duck-typed double degrades, never explodes
# --------------------------------------------------------------------------- #
def test_a_transport_double_without_the_method_degrades_to_false(caplog):
    """A new bus method must not turn "unsupported" into a crash in old tests.

    These calls happen inside message callbacks; an AttributeError there is
    swallowed-and-logged by some dispatchers and fatal in others, which is the
    worst of both. Explicit `getattr` + a loud False keeps it one behaviour.
    """
    bus = ComposeIOBus(bus_id="t", global_transport=_DuckTypedRecorder())
    with caplog.at_level("ERROR"):
        assert bus.publish_foreign("intelligence/a/in", {"x": 1}) is False
        assert bus.subscribe_foreign("intelligence/a/in", lambda c, p: None) is False
    assert "send_foreign" in caplog.text or "subscribe_foreign" in caplog.text


# --------------------------------------------------------------------------- #
# the MQTT implementation's own contract
# --------------------------------------------------------------------------- #
def test_mqtt_send_foreign_publishes_the_bare_topic_with_no_envelope():
    """The bytes are the point: a foreign frame is NOT wrapped.

    This is what makes the three conversions wire-identical to their paho
    predecessors — `channel.mjs` reads `{from,text}` off the payload directly and
    would not recognise a BusMessage.
    """
    published = []

    class _FakeClient:
        def publish(self, topic, payload, qos=0):
            published.append((topic, payload, qos))

    t = MQTTTransport(broker="x")
    t._client = _FakeClient()
    t._connected = True
    assert t.send_foreign("intelligence/architect/in",
                          '{"from":"david","text":"hi"}') is True
    topic, payload, qos = published[0]
    assert topic == "intelligence/architect/in", topic
    assert payload == b'{"from":"david","text":"hi"}', payload
    assert qos == 1
    assert b'"ns"' not in payload and b'"scope"' not in payload, (
        "a foreign frame must carry NO BusMessage envelope")


def test_mqtt_foreign_dispatch_matches_wildcards_and_survives_a_bad_callback():
    """Delivery is filter-matched, and one broken handler cannot mute the rest.

    Dispatch is deliberately kept OFF `_on_message`'s bus path: a foreign payload
    is not a BusMessage, so routing it there would raise on EVERY message and
    fill the log with failures for traffic that is perfectly well-formed for its
    own protocol.
    """
    seen = []
    t = MQTTTransport(broker="x")
    t._foreign_subs = {
        "compose_projects/intelligence/+/in": [lambda c, p: (_ for _ in ()).throw(RuntimeError("boom"))],
        "intelligence/architect/#": [lambda c, p: seen.append((c, p))],
    }

    class _Msg:
        topic = "intelligence/architect/in"
        payload = b"raw bytes, not json"

    t._on_foreign_message(None, None, _Msg())
    assert seen == [("intelligence/architect/in", b"raw bytes, not json")], seen


def test_mqtt_drops_a_foreign_frame_loudly_when_not_connected(caplog):
    """NOT queued into `_pending`, and that is deliberate.

    `_pending` holds BusMessages and is flushed through `_send_now`, which frames
    an envelope and derives a `composeio/` topic — a foreign frame has neither. A
    second queue with undefined ordering relative to the first would be worse
    than a loud drop.
    """
    t = MQTTTransport(broker="x")
    with caplog.at_level("WARNING"):
        assert t.send_foreign("intelligence/a/in", b"x") is False
    assert "DROPPED" in caplog.text, caplog.text
    assert t._pending == [], "a foreign frame must never enter the BusMessage queue"


def test_every_transport_in_the_repo_inherits_the_refusing_default():
    """CLASS guard: a transport added later gets the loud behaviour for free.

    The alternative — each transport remembering to implement or refuse — is the
    shape that let `MESSAGE_TYPE_WIRE_CODES` drift four ways. One default,
    inherited; opting IN is the explicit act.
    """
    for mod, cls in (("transport_local", "LocalTransport"),
                     ("transport_synthetic", "SyntheticTransport")):
        m = __import__(f"composeio.{mod}", fromlist=[cls])
        klass = getattr(m, cls)
        assert klass.send_foreign is Transport.send_foreign, (
            f"{cls} overrides send_foreign — if that is intended, add it to this "
            f"list with a reason; if not, it has silently claimed a capability")
