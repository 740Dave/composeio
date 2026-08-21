"""ONE publish is EXACTLY ONE delivery per registration — counted, not assumed.

[SPEC:CXC-PATSUB] [CXC-PS-EXACTLY-ONCE] [CXC-PS-NO-SELF-ECHO], compliance
CXC-PS-C14 / CXC-PS-C15.  Amends [CXC-IC-CHANNEL] IC-C15.

THE DEFECT THIS ENCODES.  On 2026-08-11 David typed one message into the
Intelligence Console and it reached the Architect twice, verbatim, in the same
turn.  It had been reported before and "fixed" on 2026-07-23 by making the
channel relay a flock singleton — a correct fix for a real mechanism (two
relays), which the spec then mistook for the ONLY mechanism.  This time the
wire showed exactly ONE relay and the message STILL doubled, because the
doubling was never in the relay's instance count.  It was underneath, in the
bus, in two independent places:

  1. `on_message` registered its callback TWICE — into `_on_message_callbacks`
     AND into `_local.subscribe_all` — while `_send_message` drove both paths.
  2. A bus that publishes a namespace it ALSO pattern-subscribes is subscribed
     to ITSELF: MQTT 3.1.1 has no `noLocal`, so the broker hands every publish
     back to the publishing client, and the bus dispatched it a second time.

WHY THE ASSERTIONS ARE `== 1` AND NEVER `>= 1`.  This is the whole point.  The
old contract was written entirely as set membership — "receives every `p/...`
publish and nothing else" — and a bus that delivers everything twice satisfies
every word of it.  A `>=` assertion is structurally blind to this class and
would have passed against every version of the code that shipped the bug.
`test_the_counter_can_actually_fail` is the positive control proving the
harness can see a wrong count at all.

WHY IT IS PARAMETRIZED OVER TRANSPORT TOPOLOGY.  The delivery count must be a
property of the SUBSCRIPTION, not of what is bolted underneath it.  Bug (1)
needs no transport; bug (2) needs a broadcast one.  A test fixed to one
topology sees at most half the defect — and the console runs the topology that
sees the other half.

`EchoingTransport` is the faithful model of the measured behaviour, not a
convenience: the fingerprints below were taken against the LIVE broker
(mqtt-broker.local:1883) with the real `MQTTTransport` — one `publish_state` on a
global namespace fired the pattern subscriber at +0.03 ms (local) and +0.74 ms
(the broker's echo), and end-to-end through the real `channel_bridge` one
compose commit put TWO byte-identical frames on `compose_projects/intelligence/<id>/in`, 1.2 ms
apart, matching the 1 ms gap in the live channel log for David's message.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from composeio.bus import ComposeIOBus
from composeio.namespace import Scope
from composeio.transport import BusMessage, Transport

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Transport doubles
# --------------------------------------------------------------------------- #
class SilentTransport(Transport):
    """A global transport that swallows everything — models a broker the bus
    is NOT subscribed to (publish-only participant)."""

    def __init__(self):
        self.sent: list[BusMessage] = []
        self._all: list = []
        self._running = False

    def send(self, message: BusMessage):
        self.sent.append(message)

    def subscribe(self, namespace, callback):
        pass

    def unsubscribe(self, namespace, callback):
        pass

    def subscribe_all(self, callback):
        self._all.append(callback)

    def subscribe_pattern(self, pattern):
        pass

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    @property
    def is_connected(self) -> bool:
        return self._running


class EchoingTransport(SilentTransport):
    """MQTT 3.1.1 with a self-matching subscription, modelled honestly.

    The broker delivers a published message to EVERY matching subscription,
    including the publishing client's own — there is no `noLocal` before MQTT
    5.  So `send()` hands the message straight back to the bus's ingress.  The
    round trip through `to_json`/`from_json` is deliberate and load-bearing:
    the echo the bus must recognise is bytes off the wire, not the very object
    it emitted, so an identity-based (`is`) suppressor would pass this test and
    still fail against a real broker.
    """

    def send(self, message: BusMessage):
        super().send(message)
        wire = BusMessage.from_json(message.to_json())
        for cb in list(self._all):
            cb(wire)


TOPOLOGIES = {
    "local-only": lambda: None,
    "global-silent": SilentTransport,
    "global-echoing": EchoingTransport,
}


def _bus(topology: str, *, bus_id: str = "exactly-once") -> ComposeIOBus:
    factory = TOPOLOGIES[topology]
    transport = factory() if factory else None
    bus = ComposeIOBus(bus_id=bus_id, global_transport=transport)
    bus.start(announce_time=False)
    return bus


# --------------------------------------------------------------------------- #
# [CXC-PS-EXACTLY-ONCE] — one publish, one delivery, per registration kind
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("topology", list(TOPOLOGIES))
def test_on_message_monitor_fires_exactly_once(topology):
    """RED before the fix: 2 on local-only/global-silent (double registration),
    3 on global-echoing (double registration + self-echo)."""
    bus = _bus(topology)
    seen: list[BusMessage] = []
    bus.on_message(seen.append)

    bus.ensure_namespace("mon/probe", scope=Scope.GLOBAL)
    bus.publish_state("mon/probe", "one", source="test")

    assert len(seen) == 1, (
        f"[{topology}] one publish reached the on_message monitor "
        f"{len(seen)} times; CXC-PS-C14 requires exactly 1")


@pytest.mark.parametrize("topology", list(TOPOLOGIES))
def test_pattern_subscriber_fires_exactly_once(topology):
    """RED before the fix: 2 on global-echoing (this is David's path — the
    channel relay watches `compose_projects/intelligence/<id>/*` on a bus that publishes into
    it, so the broker's echo re-fired the relay and it re-sent his message)."""
    bus = _bus(topology)
    seen: list[tuple] = []
    bus.subscribe_pattern("fam/*", lambda ns, new, old: seen.append((ns, new)))

    bus.ensure_namespace("fam/member", scope=Scope.GLOBAL)
    bus.publish_state("fam/member", "one", source="test")

    assert len(seen) == 1, (
        f"[{topology}] one publish reached the pattern subscriber "
        f"{len(seen)} times; CXC-PS-C14 requires exactly 1")


@pytest.mark.parametrize("topology", list(TOPOLOGIES))
def test_exact_namespace_subscriber_fires_exactly_once(topology):
    """Guards the third registration kind.  Recorded honestly: this one was
    already GREEN pre-fix on every topology, because an exact subscriber fires
    only on `new != old` and an echo carries the value the namespace already
    holds.  It is here so the invariant is stated for ALL THREE registration
    kinds — the next change to the ingress path must keep it that way, and
    "the bug happened not to reach this one" is not a reason to leave it
    unasserted."""
    bus = _bus(topology)
    seen: list = []
    bus.ensure_namespace("exact/probe", scope=Scope.GLOBAL)
    bus.subscribe("exact/probe", lambda ns, new, old: seen.append(new))

    bus.publish_state("exact/probe", "one", source="test")

    assert len(seen) == 1, (
        f"[{topology}] one publish reached the exact subscriber "
        f"{len(seen)} times; CXC-PS-C14 requires exactly 1")


@pytest.mark.parametrize("topology", list(TOPOLOGIES))
def test_delivery_count_is_independent_of_transport_topology(topology):
    """The count is a property of the SUBSCRIPTION, not of what is bolted
    underneath it — the invariant that makes a per-transport test insufficient.
    """
    bus = _bus(topology)
    counts: dict[str, int] = {"monitor": 0, "pattern": 0}

    def bump(key):
        def _cb(*_a, **_k):
            counts[key] += 1
        return _cb

    bus.on_message(bump("monitor"))
    bus.subscribe_pattern("topo/*", bump("pattern"))
    bus.ensure_namespace("topo/probe", scope=Scope.GLOBAL)
    bus.publish_state("topo/probe", "one", source="test")

    assert counts == {"monitor": 1, "pattern": 1}, (
        f"[{topology}] delivery counts changed with the transport topology: "
        f"{counts}")


@pytest.mark.parametrize("topology", list(TOPOLOGIES))
def test_off_message_actually_stops_delivery(topology):
    """The double registration also made `off_message` a lie: it removed the
    `_on_message_callbacks` entry but left the `_local.subscribe_all` one, so
    an 'unsubscribed' monitor kept receiving."""
    bus = _bus(topology)
    seen: list = []
    bus.on_message(seen.append)
    bus.off_message(seen.append)

    bus.ensure_namespace("off/probe", scope=Scope.GLOBAL)
    bus.publish_state("off/probe", "one", source="test")

    assert len(seen) == 0, (
        f"[{topology}] an unsubscribed monitor still received {len(seen)} "
        f"message(s); off_message must actually stop delivery")


# --------------------------------------------------------------------------- #
# [CXC-PS-NO-SELF-ECHO] — suppression is envelope identity, NOT content dedup
# --------------------------------------------------------------------------- #
def test_a_genuine_repeat_of_the_same_value_still_delivers():
    """The guard-rail on the fix, and the reason [CXC-IC-NARRATION]'s
    'repeats are never deduped' ruling survives it: David legitimately types
    the same word twice and a heartbeat legitimately re-emits identical text.
    Each publish carries its own `ts` and `seq`, so no genuine repeat can ever
    match an emitted fingerprint."""
    bus = _bus("global-echoing")
    seen: list = []
    bus.subscribe_pattern("rep/*", lambda ns, new, old: seen.append(new))
    bus.ensure_namespace("rep/probe", scope=Scope.GLOBAL)

    bus.publish_state("rep/probe", "same text", source="david")
    bus.publish_state("rep/probe", "same text", source="david")
    bus.publish_state("rep/probe", "same text", source="david")

    assert len(seen) == 3, (
        f"three genuine publishes of the SAME value delivered {len(seen)} "
        f"times; self-echo suppression must be envelope identity (ts+seq), "
        f"never content dedup")


def test_a_foreign_message_on_a_published_namespace_is_not_suppressed():
    """A peer's message must arrive even on a namespace this bus also
    publishes — suppression keys on the exact envelope, not the namespace."""
    bus = _bus("global-echoing")
    transport = bus.global_transport
    seen: list = []
    bus.subscribe_pattern("shared/*", lambda ns, new, old: seen.append(new))
    bus.ensure_namespace("shared/probe", scope=Scope.GLOBAL)

    bus.publish_state("shared/probe", "mine", source="me")
    for cb in list(transport._all):
        cb(BusMessage.from_json({
            "ns": "shared/probe", "type": "state", "src": "a-peer",
            "ts": 1.0, "scope": "global", "seq": 7, "val": "theirs"}))

    assert seen == ["mine", "theirs"], (
        f"expected exactly one delivery of each, got {seen}")


def test_a_second_copy_from_a_duplicate_publisher_is_NOT_absorbed():
    """Deliberate non-goal, stated as a test so a later reader does not
    "improve" the suppressor into hiding it: two live publishers is a
    DIFFERENT defect with a different fix ([CXC-IC-SINGLETON] flock), and
    silently absorbing its second copy here would erase the evidence that
    diagnoses it.  One emission suppresses at most one echo."""
    bus = _bus("global-echoing")
    transport = bus.global_transport
    seen: list = []
    bus.subscribe_pattern("dup/*", lambda ns, new, old: seen.append(new))
    bus.ensure_namespace("dup/probe", scope=Scope.GLOBAL)

    bus.publish_state("dup/probe", "one", source="me")
    echoed = transport.sent[-1]
    for cb in list(transport._all):          # a SECOND copy on the wire
        cb(BusMessage.from_json(echoed.to_json()))

    assert len(seen) == 2, (
        f"expected 1 (own publish) + 1 (a duplicate publisher's copy, which "
        f"must stay visible), got {len(seen)}")


def test_self_echo_memory_is_bounded():
    """The fingerprint set must not grow without limit on a busy bus."""
    bus = _bus("global-silent")
    bus.ensure_namespace("bound/probe", scope=Scope.GLOBAL)
    for i in range(ComposeIOBus.SELF_ECHO_MEMORY * 2 + 50):
        bus.publish_state("bound/probe", i, source="test")

    assert len(bus._emitted_fingerprints) <= ComposeIOBus.SELF_ECHO_MEMORY


# --------------------------------------------------------------------------- #
# End-to-end: the reported symptom, through the REAL relay
# --------------------------------------------------------------------------- #
def test_one_compose_commit_sends_exactly_one_frame_to_the_intelligence():
    """David's exact path — [CXC-IC-CHANNEL] IC-C15 exactly-once, measured
    where it was actually breaking.

    The console's composer commits to `compose_projects/intelligence/<id>/compose`; the relay's
    pattern subscriber unwraps it onto the raw `compose_projects/intelligence/<id>/in` topic the
    channel MCP server subscribes.  RED before the fix: TWO frames, which is
    the two `INBOUND` lines 1 ms apart in `/tmp/composeio-channel-<id>.log`
    and the two identical `<channel>` messages in one Architect turn.

    A relay-level test alone could not catch this (the relay is correct and was
    already a proven singleton) and a bus-level test alone would not have shown
    that the bus's delivery count IS the Intelligence's message count.  Hence
    both, in one file.
    """
    pub = str(_REPO_ROOT / "compose_projects" / "intelligence" / "publisher")
    if pub not in sys.path:
        sys.path.insert(0, pub)
    import channel_bridge

    idd = "exactly-once-probe"
    bus = _bus("global-echoing", bus_id="console")
    frames: list = []
    bus.publish_foreign = lambda channel, payload, **kw: (
        frames.append((channel, payload)) or True)

    stop = type("Stop", (), {"wait": lambda self, *a: True,
                             "is_set": lambda self: True})()
    channel_bridge.run(idd, "mqtt://unused:1883", bus=bus, stop=stop)

    bus.publish_state(f"intelligence/{idd}/compose",
                      {"text": "ONE MESSAGE TYPED ONCE"}, source="composer")

    sent = [p for ch, p in frames if ch == f"intelligence/{idd}/in"]
    assert len(sent) == 1, (
        f"ONE compose commit put {len(sent)} frames on "
        f"intelligence/{idd}/in — the Intelligence receives David's message "
        f"{len(sent)} times (IC-C15 exactly-once)")


# --------------------------------------------------------------------------- #
# Positive control
# --------------------------------------------------------------------------- #
def test_the_counter_can_actually_fail():
    """Proves the harness can SEE a wrong count — an exact-count assertion is
    only evidence if it is capable of failing.  Two publishes must count as
    two; a test that could not tell 1 from 2 would pass on a doubling bus."""
    bus = _bus("global-echoing")
    seen: list = []
    bus.on_message(seen.append)
    bus.ensure_namespace("ctl/probe", scope=Scope.GLOBAL)

    bus.publish_state("ctl/probe", "a", source="test")
    bus.publish_state("ctl/probe", "b", source="test")

    assert len(seen) == 2
