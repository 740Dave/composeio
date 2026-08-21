"""The ACK that never comes — silence as a REPORTED outcome.

[SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-CHUNKS], workstream D of
docs/PLAN-fleet-baseline-and-bus-native.md.

THE HALF-INSTRUMENT THIS CLOSES. The previous pass (D1/D2) made the config ACK
physically sendable on classic CAN — 7 binary bytes instead of a 13-byte string
against an 8-byte frame — and made the head CHECK it instead of trusting the
letters "ok". What it left behind, in its own words: *"No ACK timeout on the
head. The ACK can now arrive; nothing yet notices when it doesn't."*

A head that can hear success and cannot hear silence cannot tell a completed
transfer from a leaf that died mid-reassembly. And silence is the LIKELY failure
on CAN, where the proven 5,856 B scene is 977 frames rather than UART's 25: 977
chances to lose one, and a leaf that never completes never ACKs.

THE TWO TESTS THAT MATTER, and they pull in opposite directions:

  * the alarm FIRES — an unanswered transfer becomes a counted, logged,
    reportable event (`test_a_transfer_that_is_never_acked_...`);
  * the alarm STAYS QUIET — a healthy 977-frame CAN transfer does NOT trip it
    (`test_a_healthy_977_frame_can_transfer_...`). This is the regression that
    matters more, because an over-eager timeout teaches operators to ignore the
    alarm, and a counter nobody believes is worse than a counter that cannot
    increment.

`test_a_fixed_one_second_deadline_WOULD_have_tripped_that_same_transfer` is what
stops the second one being vacuous: it shows the obvious implementation failing
the case the shipping one passes.

WHAT THESE PROVE AND WHAT THEY DO NOT. They run the real chunker, the real
deadline arithmetic and (for two of them) the real CANTransport frame path over
a loopback backend. They prove nothing about a wire: no transceiver, no
arbitration, no bit timing. D5 — CAN host -> relay -> UART leaf -> rendered
scene — remains BLOCKED on working CAN hardware.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "python", _REPO / "services"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from composeio.config_chunk import (          # noqa: E402
    ACK_HASH_MISMATCH, ACK_INCOMPLETE, ACK_NO_MEMORY, CAN_CONTENT_BYTES,
    UART_CONTENT_BYTES, Reassembler, ack_ok, chunk_count, content_hash,
    encode_ack,
)
from composeio.transport import BusMessage, MessageType   # noqa: E402
from composeio.transport_can import (         # noqa: E402
    CANTransport, _reset_loopbacks_for_tests,
)
from uart_mqtt_bridge.config_server import (  # noqa: E402
    ACK_CHECK_SEC, ACK_TIMEOUT_BASE_S, ACK_TIMEOUT_WIRE_MARGIN, ConfigServer,
    PendingTransfer, ack_timeout_seconds, config_namespace,
)

#: The transfer the whole workstream is about: 5,856 bytes — 977 frames on CAN
#: (976 data + 1 header), 25 on UART.
SCENE_LEN = 5856
CAN_FRAMES = 977
UART_FRAMES = 25
PACE_S = 0.002

#: A CAN-sized scene. Content is irrelevant; the FRAME COUNT is the subject.
SCENE = bytes((i * 37 + 11) & 0xFF for i in range(SCENE_LEN))


class FakeClock:
    """Time the test drives. The deadline is 5.9 s for a CAN transfer, and a
    suite that sleeps that long is a suite that gets deleted."""

    def __init__(self, t: float = 1_000.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


def _server(scene: bytes = b"title: engine thrust\n", *, clock=None,
            devices=("leaf-1",), **kw) -> ConfigServer:
    """A server whose wire is /dev/null. Silence is the default here, which is
    exactly the condition under test."""
    kw.setdefault("pace_ms", 0)
    return ConfigServer(kw.pop("send", lambda m: None), lambda d: scene,
                        list(devices), clock=(clock or FakeClock()), **kw)


def _can_server(clock, *, sent=None, **kw) -> ConfigServer:
    """A server chunking for classic CAN at the SHIPPING pace, with the pacing
    sleep stubbed — so the deadline under test is the real one (977 frames x
    2 ms) without 1.95 s of real sleeping."""
    return ConfigServer(send=(sent.append if sent is not None else
                              (lambda m: None)),
                        fetch=lambda d: SCENE, devices=["leaf-1"],
                        chunk_payload=CAN_CONTENT_BYTES, pace_ms=2.0,
                        clock=clock, sleep=lambda s: None, **kw)


def _ack(srv: ConfigServer, value, device_id: str = "leaf-1") -> None:
    srv.on_message(BusMessage(namespace=config_namespace(device_id),
                              type=MessageType.ACK, value=value))


@pytest.fixture(autouse=True)
def _clean_loopbacks():
    _reset_loopbacks_for_tests()
    yield
    _reset_loopbacks_for_tests()


# --------------------------------------------------------------------------- #
# The derivation — why the deadline is not a constant                          #
# --------------------------------------------------------------------------- #

def test_the_frame_counts_this_deadline_is_sized_against():
    """The premise, asserted so it cannot drift under the arithmetic."""
    assert chunk_count(SCENE_LEN, CAN_CONTENT_BYTES) + 1 == CAN_FRAMES
    assert chunk_count(SCENE_LEN, UART_CONTENT_BYTES) + 1 == UART_FRAMES
    assert CAN_FRAMES == 39 * UART_FRAMES + 2       # ~39x the frames, same scene


def test_a_fixed_one_second_deadline_would_fire_on_a_HEALTHY_can_transfer():
    """The reason the timeout scales, stated as an assertion.

    977 frames at 2 ms is nearly two seconds of wire time before the leaf can
    even finish reassembling. A one-second constant — the obvious choice — is
    already expired before a perfectly healthy transfer has left the head.
    """
    can_wire_s = CAN_FRAMES * PACE_S
    assert can_wire_s == pytest.approx(1.954)
    assert can_wire_s > 1.0, "a fixed 1 s deadline expires mid-transfer"
    assert ack_timeout_seconds(CAN_FRAMES, PACE_S) > can_wire_s


def test_the_deadline_scales_with_the_substrate_not_with_a_constant():
    uart = ack_timeout_seconds(UART_FRAMES, PACE_S)
    can = ack_timeout_seconds(CAN_FRAMES, PACE_S)
    assert uart == pytest.approx(2.10, abs=0.01)
    assert can == pytest.approx(5.91, abs=0.01)
    # The SAME scene on the two substrates gets materially different budgets —
    # which is the entire point; one number cannot serve both.
    assert can > 2.5 * uart


def test_the_deadline_always_exceeds_the_wire_time_it_budgets_for():
    """Across every frame count this protocol can express."""
    for frames in (1, 2, 25, 100, 977, 5000, 10_923):
        wire = frames * PACE_S
        assert ack_timeout_seconds(frames, PACE_S) > wire, frames


def test_the_margin_is_a_FULL_second_pass_over_the_wire_time():
    """x2 on wire time buys the leaf as long again to drain as the head took
    to send. Stated numerically so a change to the constant fails here."""
    assert ACK_TIMEOUT_WIRE_MARGIN == 2.0
    assert ACK_TIMEOUT_BASE_S == 2.0
    frames, wire = CAN_FRAMES, CAN_FRAMES * PACE_S
    assert (ack_timeout_seconds(frames, PACE_S)
            == pytest.approx(ACK_TIMEOUT_BASE_S
                             + ACK_TIMEOUT_WIRE_MARGIN * wire))


def test_the_measured_send_time_is_used_when_pacing_understates_it():
    """A transport slower than the pacing must not shorten the deadline.

    `pace_ms=0` on a real substrate would otherwise leave only the base, which
    is the fixed-deadline bug wearing a different hat.
    """
    assert ack_timeout_seconds(977, 0.0, sent_s=8.0) == pytest.approx(18.0)
    # ...and the pacing floor wins when the measurement is the understatement
    # (a transport that QUEUES the write and returns immediately).
    assert (ack_timeout_seconds(977, PACE_S, sent_s=0.0)
            == pytest.approx(ack_timeout_seconds(977, PACE_S, sent_s=1.0)))


def test_the_deadline_is_not_capped_at_the_protocol_ceiling():
    """65,535 B over CAN is 10,923 frames. A cap would make the deadline a
    constant again for exactly the transfers where silence is most likely."""
    assert ack_timeout_seconds(10_923, PACE_S) == pytest.approx(45.7, abs=0.1)


def test_deadlines_are_checked_faster_than_scenes_are_polled():
    assert ACK_CHECK_SEC < 15.0, "a 2 s deadline on a 15 s check is not an alarm"


# --------------------------------------------------------------------------- #
# THE ALARM FIRES                                                              #
# --------------------------------------------------------------------------- #

def test_a_transfer_that_is_never_acked_is_counted_as_a_timeout(caplog):
    """THE HEADLINE. Before this, an unanswered transfer left no trace at all.

    The leaf here does exactly what a leaf that died mid-reassembly does:
    nothing.
    """
    clock = FakeClock()
    srv = _server(clock=clock)
    assert srv.serve("leaf-1")
    assert srv.acks_timeout == 0, "fired before anything could have gone wrong"
    assert srv.pending_transfers == 1, "a sent transfer must be IN FLIGHT"

    # Still inside the window: silence so far is not yet a fault.
    clock.advance(ACK_TIMEOUT_BASE_S - 0.01)
    assert srv.check_timeouts() == 0
    assert srv.acks_timeout == 0

    clock.advance(0.02)
    with caplog.at_level(logging.ERROR, logger="uart_mqtt_bridge.config"):
        assert srv.check_timeouts() == 1
    assert srv.acks_timeout == 1, "THE COUNTER THAT DID NOT EXIST"
    assert srv.pending_transfers == 0, "a resolved transfer is not still pending"
    assert "UNANSWERED" in caplog.text
    assert "NOT re-pushing" in caplog.text


def test_the_same_timeout_never_fires_twice():
    clock = FakeClock()
    srv = _server(clock=clock)
    srv.serve("leaf-1")
    clock.advance(60.0)
    assert srv.check_timeouts() == 1
    assert srv.check_timeouts() == 0
    assert srv.acks_timeout == 1


def test_the_timeout_is_reported_through_the_on_timeout_seam():
    """Counted AND handed to whoever asked to be told — this module refuses to
    invent a namespace to complain on, and refuses to make the operator grep."""
    seen: list[PendingTransfer] = []
    clock = FakeClock()
    srv = _server(SCENE, clock=clock, on_timeout=seen.append)
    srv.serve("leaf-1")
    clock.advance(60.0)
    srv.check_timeouts()

    assert len(seen) == 1
    assert seen[0].device_id == "leaf-1"
    assert seen[0].hash == content_hash(SCENE)
    assert seen[0].total_len == SCENE_LEN
    assert seen[0].frames == UART_FRAMES
    assert seen[0].waited(clock.t) == pytest.approx(60.0)


def test_a_broken_on_timeout_handler_cannot_swallow_the_timeout(caplog):
    def explode(pending):
        raise RuntimeError("the reporter is down")

    clock = FakeClock()
    srv = _server(clock=clock, on_timeout=explode)
    srv.serve("leaf-1")
    clock.advance(60.0)
    with caplog.at_level(logging.ERROR, logger="uart_mqtt_bridge.config"):
        assert srv.check_timeouts() == 1
    assert srv.acks_timeout == 1, "a broken reporter lost the event"
    assert "still stands" in caplog.text


def test_the_timeout_is_counted_BY_DEVICE_not_only_for_the_fleet():
    """One dead board on a healthy segment must be distinguishable from a dead
    segment. A single global counter cannot tell those apart."""
    clock = FakeClock()
    srv = ConfigServer(lambda m: None, lambda d: b"scene for " + d.encode(),
                       ["leaf-1", "leaf-2"], pace_ms=0, clock=clock)
    srv.serve("leaf-1")
    srv.serve("leaf-2")
    _ack(srv, ack_ok(content_hash(b"scene for leaf-2"), 16, 1), "leaf-2")

    clock.advance(60.0)
    assert srv.check_timeouts() == 1

    assert srv.census("leaf-1").timeout == 1
    assert srv.census("leaf-1").confirmed == 0
    assert srv.census("leaf-2").timeout == 0, "a healthy board was blamed"
    assert srv.census("leaf-2").confirmed == 1
    assert srv.acks_timeout == 1 and srv.acks_ok == 1
    assert [d for d, _ in srv.census_rows()] == ["leaf-1", "leaf-2"]


def test_a_timeout_does_NOT_trigger_a_re_push():
    """A TIMEOUT IS A DIAGNOSIS. An immediate re-push from both ends is how you
    get two interleaved transfers, which is the one thing the content hash
    exists to catch — and the leaf is already re-requesting with backoff."""
    sent: list[BusMessage] = []
    clock = FakeClock()
    srv = ConfigServer(sent.append, lambda d: SCENE, ["leaf-1"], pace_ms=0,
                       clock=clock)
    srv.serve("leaf-1")
    after_serve = len(sent)
    assert after_serve == UART_FRAMES

    clock.advance(60.0)
    srv.check_timeouts()
    assert len(sent) == after_serve, "the head RE-PUSHED on timeout"
    assert srv.transfers == 1


def test_the_counters_dict_carries_the_timeout_for_a_status_line():
    clock = FakeClock()
    srv = _server(clock=clock)
    srv.serve("leaf-1")
    clock.advance(60.0)
    srv.check_timeouts()
    c = srv.counters()
    assert c["acks_timeout"] == 1
    assert c["acks_ok"] == 0 and c["acks_refused"] == 0
    assert c["pending"] == 0 and c["transfers"] == 1


# --------------------------------------------------------------------------- #
# THE ALARM STAYS QUIET — the regression that matters more                     #
# --------------------------------------------------------------------------- #

def test_a_healthy_977_frame_can_transfer_does_not_trip_the_timeout():
    """THE REGRESSION THAT MATTERS. An over-eager timeout is worse than none:
    it teaches operators to ignore the alarm.

    The real chunker, the real 5,856 B scene, the real CAN chunk size, the real
    2 ms pacing. The leaf takes nearly the whole budget and then ACKs — which is
    what a slow-but-healthy leaf draining a 977-frame backlog looks like.
    """
    clock = FakeClock()
    srv = _can_server(clock)
    assert srv.serve("leaf-1")

    pending = srv.pending("leaf-1")
    assert pending.frames == CAN_FRAMES, "not the transfer this test is about"
    budget = pending.deadline - pending.sent_at
    assert budget == pytest.approx(5.91, abs=0.01)

    # The leaf spends 95% of its budget reassembling 977 frames, then answers.
    clock.advance(budget * 0.95)
    assert srv.check_timeouts() == 0, "fired on a HEALTHY transfer"
    _ack(srv, ack_ok(content_hash(SCENE), SCENE_LEN, CAN_FRAMES - 1))

    assert srv.acks_ok == 1
    assert srv.acks_timeout == 0, "the alarm cried on the happy path"
    assert srv.acks_bad == 0 and srv.acks_unmatched == 0
    assert srv.pending_transfers == 0


def test_a_fixed_one_second_deadline_WOULD_have_tripped_that_same_transfer():
    """What stops the test above being vacuous.

    Same transfer, same leaf, same timing — with the naive constant deadline
    instead of the derived one. It fires, and it is wrong to.
    """
    clock = FakeClock()
    srv = _can_server(clock, ack_timeout_base_s=1.0, ack_timeout_margin=0.0)
    srv.serve("leaf-1")
    clock.advance(CAN_FRAMES * PACE_S * 0.95)       # still mid-reassembly
    assert srv.check_timeouts() == 1, "the naive deadline was expected to fire"
    assert srv.acks_timeout == 1

    # ...and the leaf's perfectly good ACK then arrives to a head that has
    # already declared it dead. That is the false alarm, in one assertion.
    _ack(srv, ack_ok(content_hash(SCENE), SCENE_LEN, CAN_FRAMES - 1))
    assert srv.acks_ok == 0
    assert srv.acks_stale == 1


def test_the_leaf_may_take_as_long_again_as_the_head_took_to_send():
    """What the x2 margin buys, spent exactly."""
    clock = FakeClock()
    srv = _can_server(clock)
    srv.serve("leaf-1")
    clock.advance(CAN_FRAMES * PACE_S)              # a leaf at HALF send rate
    assert srv.check_timeouts() == 0
    _ack(srv, ack_ok(content_hash(SCENE), SCENE_LEN, CAN_FRAMES - 1))
    assert (srv.acks_ok, srv.acks_timeout) == (1, 0)


def test_a_healthy_uart_transfer_stays_quiet_on_the_REAL_clock():
    """No injected clock anywhere: the shipping deadline against wall time."""
    srv = ConfigServer(lambda m: None, lambda d: SCENE, ["leaf-1"], pace_ms=0)
    assert srv.serve("leaf-1")
    assert srv.check_timeouts() == 0
    _ack(srv, ack_ok(content_hash(SCENE), SCENE_LEN, UART_FRAMES - 1))
    assert srv.check_timeouts() == 0
    assert (srv.acks_ok, srv.acks_timeout) == (1, 0)


def test_a_confirmed_transfer_can_never_time_out_afterwards():
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    _ack(srv, ack_ok(content_hash(SCENE), SCENE_LEN, UART_FRAMES - 1))
    clock.advance(3600.0)
    assert srv.check_timeouts() == 0
    assert (srv.acks_ok, srv.acks_timeout) == (1, 0)


def test_a_device_that_was_never_served_never_times_out():
    clock = FakeClock()
    srv = _server(clock=clock)
    clock.advance(3600.0)
    assert srv.check_timeouts() == 0
    assert srv.acks_timeout == 0


# --------------------------------------------------------------------------- #
# THE TAXONOMY — silent, wrong and abandoned are not one bucket                #
# --------------------------------------------------------------------------- #

def test_silent_and_wrong_hash_and_abandoned_are_THREE_counters():
    """Acceptance (3). Different faults, different causes, different repairs:

      silent      the leaf or the link died — nothing came back at all
      wrong-hash  the bytes arrived corrupt; every frame was present
      abandoned   the reassembly was interrupted; a frame never came

    Merging them would leave the operator with one number and no next step.
    """
    clock = FakeClock()
    scenes = {"silent": b"a" * 40, "corrupt": b"b" * 41, "dropped": b"c" * 42}
    srv = ConfigServer(lambda m: None, lambda d: scenes[d], list(scenes),
                       pace_ms=0, clock=clock)
    for device_id in scenes:
        srv.serve(device_id)

    _ack(srv, encode_ack(ACK_HASH_MISMATCH, content_hash(scenes["corrupt"]),
                         41, 0), "corrupt")
    _ack(srv, encode_ack(ACK_INCOMPLETE, content_hash(scenes["dropped"]),
                         42, 7), "dropped")
    clock.advance(60.0)
    assert srv.check_timeouts() == 1                # only the silent one

    silent = srv.census("silent")
    corrupt = srv.census("corrupt")
    dropped = srv.census("dropped")

    assert (silent.timeout, silent.refused_total) == (1, 0)
    assert (corrupt.timeout, corrupt.refused) == (0, {ACK_HASH_MISMATCH: 1})
    assert (dropped.timeout, dropped.refused) == (0, {ACK_INCOMPLETE: 1})
    # ...and the two failure REASONS do not collapse into each other either.
    assert corrupt.refused_summary() == "hash-mismatch=1"
    assert dropped.refused_summary() == "incomplete=1"
    assert srv.acks_timeout == 1 and srv.acks_refused == 2


def test_a_timeout_is_not_counted_as_a_BAD_ack():
    """Silence must not pollute the bucket that means 'the leaf reported a
    failure'. `acks_bad` answers "did the leaf reject it?"; only `acks_timeout`
    answers "did the leaf say ANYTHING?"."""
    clock = FakeClock()
    srv = _server(clock=clock)
    srv.serve("leaf-1")
    clock.advance(60.0)
    srv.check_timeouts()
    assert srv.acks_timeout == 1
    assert srv.acks_bad == 0
    assert srv.acks_unmatched == 0
    assert srv.acks_unreadable == 0


@pytest.mark.parametrize("status", [ACK_HASH_MISMATCH, ACK_INCOMPLETE,
                                    ACK_NO_MEMORY])
def test_a_leaf_that_reports_a_failure_is_not_ALSO_reported_as_silent(status):
    """A leaf that spoke is not a leaf that went quiet, whatever it said."""
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    _ack(srv, encode_ack(status, content_hash(SCENE), SCENE_LEN, 3))
    clock.advance(3600.0)
    assert srv.check_timeouts() == 0
    assert srv.acks_timeout == 0
    assert srv.acks_bad == 1
    assert srv.census("leaf-1").refused == {status: 1}


def test_an_unreadable_ack_does_NOT_clear_the_deadline():
    """Both counters move, and both are true.

    An unreadable value cannot be ATTRIBUTED to a transfer — it may be a
    corrupt frame from any of them — so it is counted as a message and the
    transfer it was probably about still goes unconfirmed. Absorbing it would
    let one corrupt byte silence the alarm.
    """
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    _ack(srv, b"\x00\x01\x02")
    assert srv.pending_transfers == 1, "an unreadable value cleared the flight"
    clock.advance(60.0)
    assert srv.check_timeouts() == 1
    assert srv.acks_unreadable == 1 and srv.acks_timeout == 1
    assert srv.acks_ok == 0


def test_an_ack_naming_ANOTHER_transfer_does_not_clear_the_pending_one():
    """The check that keeps a second head on the segment from covering for a
    dead leaf."""
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    _ack(srv, ack_ok(content_hash(SCENE) ^ 0xFFFF, SCENE_LEN, 1))
    assert srv.acks_unmatched == 1
    assert srv.pending_transfers == 1
    clock.advance(60.0)
    assert srv.check_timeouts() == 1
    assert srv.acks_ok == 0 and srv.acks_timeout == 1


def test_an_ack_that_arrives_AFTER_its_deadline_is_stale_not_a_success():
    """One transfer, one verdict. Reporting both 'silent' and 'confirmed' for
    the same transfer would make both numbers untrustworthy."""
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    clock.advance(60.0)
    assert srv.check_timeouts() == 1
    _ack(srv, ack_ok(content_hash(SCENE), SCENE_LEN, UART_FRAMES - 1))
    assert srv.acks_ok == 0, "a post-mortem ACK was promoted to a success"
    assert srv.acks_stale == 1
    assert srv.acks_timeout == 1


def test_a_duplicate_ack_is_stale_and_does_not_double_count_success():
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    good = ack_ok(content_hash(SCENE), SCENE_LEN, UART_FRAMES - 1)
    _ack(srv, good)
    _ack(srv, good)
    assert srv.acks_ok == 1 and srv.acks_stale == 1


def test_a_late_ack_is_reported_late_even_without_a_watch_thread():
    """A deployment with no loop still notices: `_on_ack` checks deadlines
    before it reads the value, so the verdict does not depend on who got there
    first."""
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    clock.advance(60.0)
    _ack(srv, ack_ok(content_hash(SCENE), SCENE_LEN, UART_FRAMES - 1))
    assert srv.acks_timeout == 1, "nothing called check_timeouts and it showed"
    assert srv.acks_stale == 1 and srv.acks_ok == 0


def test_every_transfer_reaches_exactly_one_verdict():
    """The accounting identity: transfers == resolved + pending.

    Message counters (unreadable / unmatched / stale) are deliberately NOT in
    it — they count values that arrived, not transfers that were sent, and
    adding them would make the sum stop meaning anything.
    """
    clock = FakeClock()
    scenes = {"a": b"a" * 10, "b": b"b" * 11, "c": b"c" * 12, "d": b"d" * 13}
    srv = ConfigServer(lambda m: None, lambda d: scenes[d], list(scenes),
                       pace_ms=0, clock=clock)
    for device_id in scenes:
        srv.serve(device_id)
    _ack(srv, ack_ok(content_hash(scenes["a"]), 10, 1), "a")
    _ack(srv, encode_ack(ACK_NO_MEMORY, content_hash(scenes["b"]), 11, 0), "b")
    _ack(srv, b"junk", "c")                    # unreadable: resolves nothing
    clock.advance(60.0)
    srv.check_timeouts()                       # c and d go silent

    resolved = sum(row.resolved for _, row in srv.census_rows())
    assert resolved + srv.pending_transfers == srv.transfers == 4
    assert (srv.acks_ok, srv.acks_refused, srv.acks_timeout) == (1, 1, 2)
    assert srv.acks_unreadable == 1


# --------------------------------------------------------------------------- #
# SUPERSEDED — the head's own doing, and not charged to the wire               #
# --------------------------------------------------------------------------- #

def test_a_transfer_superseded_IN_FLIGHT_is_not_a_timeout():
    """DECIDED: superseded is its own verdict, never `timeout`.

    When a second header reaches the leaf mid-transfer its reassembler ABANDONS
    the first (`Reassembler.RESTARTED`), so the first transfer can no longer be
    confirmed BY CONSTRUCTION. Charging that to the silence counter would make
    an ordinary poll-driven scene change look like a dead link — and an alarm
    that fires on normal operation is the failure this deadline exists to avoid.
    """
    clock = FakeClock()
    scenes = ["one" * 10, "two" * 10]
    srv = ConfigServer(lambda m: None, lambda d: scenes[-1].encode(),
                       ["leaf-1"], pace_ms=0, clock=clock)
    srv.serve("leaf-1")
    clock.advance(0.5)                          # well inside the deadline
    scenes.append("three" * 10)
    srv.serve("leaf-1", reason="changed")

    assert srv.acks_superseded == 1
    assert srv.acks_timeout == 0, "the head blamed the wire for its own push"
    assert srv.pending_transfers == 1, "the NEW transfer is in flight"
    assert srv.pending("leaf-1").hash == content_hash(b"three" * 10)


def test_a_transfer_superseded_AFTER_its_deadline_is_a_TIMEOUT():
    """The other half of that ruling, and the reason ordering matters.

    A pending transfer whose deadline has already passed was silent for its
    entire window; the later push had nothing to do with that. Calling it
    'superseded' would let a periodic re-push erase every silence in the log.
    """
    clock = FakeClock()
    srv = _server(SCENE, clock=clock)
    srv.serve("leaf-1")
    clock.advance(60.0)
    srv.serve("leaf-1", reason="changed")

    assert srv.acks_timeout == 1, "a silence was erased by the next push"
    assert srv.acks_superseded == 0


def test_the_superseded_transfers_own_incomplete_ack_is_not_unmatched():
    """The firmware ACKs the ABANDONED transfer as `incomplete` (config_bus.cpp
    Restarted branch). That names a hash the head has already resolved, so it is
    STALE — not `unmatched`, which means 'a transfer this head never served' and
    would point the operator at a second head that does not exist."""
    clock = FakeClock()
    scenes = [b"first" * 8]
    srv = ConfigServer(lambda m: None, lambda d: scenes[-1], ["leaf-1"],
                       pace_ms=0, clock=clock)
    srv.serve("leaf-1")
    first_hash, first_len = content_hash(scenes[0]), len(scenes[0])
    scenes.append(b"second" * 8)
    srv.serve("leaf-1", reason="changed")

    _ack(srv, encode_ack(ACK_INCOMPLETE, first_hash, first_len, 3))
    assert srv.acks_stale == 1
    assert srv.acks_unmatched == 0
    # ...and the NEW transfer is still owed an ACK.
    assert srv.pending_transfers == 1
    clock.advance(60.0)
    assert srv.check_timeouts() == 1


# --------------------------------------------------------------------------- #
# Over a REAL CANTransport frame path                                          #
# --------------------------------------------------------------------------- #

def test_the_head_reports_SILENCE_over_a_real_can_loopback(caplog):
    """A leaf that reassembles the scene and then dies before ACKing.

    Nothing is stubbed but the scene fetch and the clock: the transfer crosses a
    real CANTransport pair as 8-byte frames on v2 arbitration ids. This is the
    condition that produced NOTHING AT ALL before this change — the leaf had the
    scene, the head had no idea.
    """
    scene = b"widgets:\n  - type: gauge\n"
    clock = FakeClock()
    head = CANTransport(channel="ack-silent", backend="loopback", node_id=1)
    leaf = CANTransport(channel="ack-silent", backend="loopback", node_id=2)
    head.start()
    leaf.start()
    srv = ConfigServer.for_transport(head, lambda d: scene, ["leaf-1"],
                                     pace_ms=0, clock=clock)
    asm = Reassembler()

    def on_leaf(msg: BusMessage) -> None:
        if msg.type == MessageType.CONFIG_PUSH:
            asm.on_frame(msg.value)             # ...and then says NOTHING

    leaf.subscribe(config_namespace("leaf-1"), on_leaf)
    assert srv.serve("leaf-1")
    assert asm.payload == scene, "the transfer itself did not arrive"

    assert srv.check_timeouts() == 0
    clock.advance(60.0)
    with caplog.at_level(logging.ERROR, logger="uart_mqtt_bridge.config"):
        assert srv.check_timeouts() == 1
    assert srv.acks_timeout == 1
    assert srv.acks_ok == 0 and srv.acks_bad == 0
    assert "went UNANSWERED" in caplog.text


def test_a_leaf_that_acks_INSIDE_the_send_loop_is_not_called_unmatched():
    """A defect this suite found: the transfer must be in flight from the FIRST
    frame, not the last.

    Registering it after the send loop is the natural way to write it and it is
    wrong. On a synchronous substrate the ACK for the final frame arrives inside
    `send()` — before the head has finished writing itself the note it checks
    the ACK against — and the head then reports `unmatched`, i.e. "a second head
    is answering on this segment", about its own perfectly good transfer.
    """
    scene = b"x" * 700                          # three UART frames
    srv_box: list[ConfigServer] = []

    def send(msg: BusMessage) -> None:
        # The leaf answers the moment the last frame lands.
        if msg.value[:2] == b"\x00\x03":
            _ack(srv_box[0], ack_ok(content_hash(scene), len(scene), 3))

    srv = ConfigServer(send, lambda d: scene, ["leaf-1"], pace_ms=0,
                       clock=FakeClock())
    srv_box.append(srv)
    assert srv.serve("leaf-1")

    assert srv.acks_ok == 1
    assert srv.acks_unmatched == 0, "the head reported a phantom second head"
    assert srv.pending_transfers == 0


def test_the_same_can_loopback_stays_quiet_when_the_leaf_DOES_ack():
    """The paired proof: same wire, same head, a leaf that answers. Zero."""
    scene = b"widgets:\n  - type: gauge\n"
    clock = FakeClock()
    head = CANTransport(channel="ack-quiet", backend="loopback", node_id=1)
    leaf = CANTransport(channel="ack-quiet", backend="loopback", node_id=2)
    head.start()
    leaf.start()
    srv = ConfigServer.for_transport(head, lambda d: scene, ["leaf-1"],
                                     pace_ms=0, clock=clock)
    ns = config_namespace("leaf-1")
    asm = Reassembler()

    def on_leaf(msg: BusMessage) -> None:
        if msg.type != MessageType.CONFIG_PUSH:
            return
        if asm.on_frame(msg.value) == Reassembler.COMPLETE:
            leaf.send(BusMessage(namespace=ns, type=MessageType.ACK,
                                 scope="global",
                                 value=ack_ok(asm.expect_hash, asm.total_len,
                                              asm.ack_seq())))

    leaf.subscribe(ns, on_leaf)
    assert srv.serve("leaf-1")

    assert srv.acks_ok == 1, "the leaf ACKed and the head did not hear it"
    clock.advance(3600.0)
    assert srv.check_timeouts() == 0
    assert srv.acks_timeout == 0


# --------------------------------------------------------------------------- #
# The watch loop                                                               #
# --------------------------------------------------------------------------- #

def test_run_watch_fires_the_timeout_without_anyone_calling_it():
    """The deployment path: bridge.py runs `run_watch` in a thread, and that is
    the only thing that will ever call `check_timeouts` in production."""
    clock = FakeClock()
    srv = _server(clock=clock, ack_check_sec=0.05, poll_sec=3600.0)
    srv.serve("leaf-1")
    clock.advance(60.0)

    stop = threading.Event()
    t = threading.Thread(target=srv.run_watch, args=(stop,), daemon=True)
    t.start()
    for _ in range(200):                        # <= 2 s, normally one tick
        if srv.acks_timeout:
            break
        stop.wait(0.01)
    stop.set()
    t.join(timeout=2.0)

    assert srv.acks_timeout == 1, "the watch loop never checked the deadline"
