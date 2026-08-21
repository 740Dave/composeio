"""COUNTDOWN — a derived, semantic "time remaining".

David: "There should also be a countdown time signal that can be tied to a
specific semantic message, e.g. 'cylons arrive in X minutes' ... a useful
convenience/cover method so that local devices, some of which might be
capability limited, need not do that math themselves."

⛔ THE WHY, WHICH MUST NOT BE LOST.  A CH32V003 or a TM1637-driven panel should
not need a clock, a subtraction and a re-publish loop to display "T-minus 4:32".
The computation belongs at the CHEAPEST NODE THAT CAN FULLY OWN IT — and that is
NOT the leaf, because the leaf lacks the clock.  This is the [P1] "complexity
flows down to the leaf" rule producing the OPPOSITE answer from usual, and it is
worth saying so out loud: complexity flows down to the leaf EXCEPT where the leaf
structurally cannot hold it.  A leaf that must be told the time in order to
subtract from it has not been given a cheaper job by being made to subtract.

Every open question the brief listed is settled by a test below, each carrying
its reasoning.  See `test_both_at_and_in_are_published` (what is on the wire),
`test_the_wire_carries_a_number_not_a_format` (units), `test_it_crosses_zero`
(what happens at zero), `test_cancel_*` (cancellation), and
`test_a_countdown_from_an_undatable_input_publishes_none` (the mothercard rule).
"""
from __future__ import annotations

import pytest

from composeio.bus import ComposeIOBus
from composeio.bus_clock import BusClock
from composeio.countdown import CountdownService
from composeio.namespace import Cadence
from composeio.transport import MessageType


@pytest.fixture
def rig():
    bus = ComposeIOBus(bus_id="countdown-probe")
    bus.start(announce_time=False)
    clock = BusClock(autostart=False)
    svc = CountdownService(bus, clock=clock)
    yield bus, clock, svc
    svc.stop()
    bus.stop()


def _vals(bus, ns):
    return [m.value for m in bus.message_log if m.namespace == ns]


# --------------------------------------------------------------------------- #
# 1. WHAT IS ON THE WIRE — and the answer is BOTH
# --------------------------------------------------------------------------- #

def test_both_at_and_in_are_published(rig):
    """SIBLING NAMESPACES, and declining either one strands a real consumer.

    `<subject>/at` — the TARGET absolute Unix timestamp.  Stable, idempotent,
      retained, published once per schedule.  It is the authoritative fact and
      the only thing that survives a service restart correctly.  A capable node
      can also render smooth sub-second animation from it.
    `<subject>/in` — REMAINING SECONDS.  Derived, republished every tick.  Stale
      the instant it lands — which is exactly why it is republished, and exactly
      why it carries a declared cadence so a receiver can tell when it stopped.

    WHY NOT ONLY `/at`: it demands the clock the capability-limited leaf does not
      have.  That is the whole request, refused.
    WHY NOT ONLY `/in`: the schedule becomes unrecoverable and unauditable — a
      service restart could not resume it, and nothing on the bus records WHEN
      the thing actually happens, only how long until it does according to a
      publisher nobody can check.
    The pair costs one extra retained integer per countdown.  That is not a
    budget worth defending against.
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1000 + 272, now=1000.0)

    assert _vals(bus, "cylons/arrival/at") == [1272]
    assert _vals(bus, "cylons/arrival/in") == [272]


def test_at_is_the_stable_fact_and_is_not_republished_every_tick(rig):
    """`/at` moves only when the SCHEDULE moves.  Republishing an unchanged
    absolute timestamp at 1 Hz would be pure noise on a bus whose most-watched
    namespace is quiet by construction."""
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    for t in (1001.0, 1002.0, 1003.0):
        clock.tick_once(now=t)
    assert _vals(bus, "cylons/arrival/at") == [1272]
    assert _vals(bus, "cylons/arrival/in") == [272, 271, 270, 269]


def test_the_wire_carries_a_number_not_a_format(rig):
    """UNITS: whole SIGNED SECONDS.  FORMATTING IS A BACKEND CONCERN.

    Per [CXC-VISION-SEMANTIC-REALIZATION] the wire carries the value and the
    tube/panel formats it.  "4 minutes", "04:32" and "272" are three renderings
    of ONE fact; putting any of them on the wire picks a locale, a precision and
    a display width on behalf of every consumer that has not been written yet —
    and a TM1637 doing `divmod(n, 60)` is two integer ops, not a hardship.

    SECONDS rather than millis for the same reason `_bus/time` is whole seconds:
    the consumers are 8-bit, the display granularity is a second, and a 1 Hz
    republish cannot carry sub-second truth.
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    v = _vals(bus, "cylons/arrival/in")[0]
    assert isinstance(v, int) and not isinstance(v, bool)
    assert v == 272


def test_the_namespaces_are_named_for_their_subject(rig):
    """NAMING, per the corpus's own lesson: name the SUBJECT, not the verdict
    and not the mechanism.

    `cylons/arrival/in`  — subject: the arrival of the cylons.  GOOD.
    `countdown/cylons`   — names the MECHANISM; renaming the mechanism renames
                           every consumer, and a second mechanism cannot reuse it.
    `cylons/danger`      — names a VERDICT the signal did not compute.
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    assert bus.get_namespace("cylons/arrival/at") is not None
    assert bus.get_namespace("cylons/arrival/in") is not None


# --------------------------------------------------------------------------- #
# 2. IT DECLARES ITS OWN CADENCE — D4, on a DERIVED namespace
# --------------------------------------------------------------------------- #

def test_in_declares_a_periodic_cadence_and_at_declares_none(rig):
    """`/in` is PERIODIC by construction — silence means the deriver died, and
    that is exactly the fact a receiver needs.  `/at` is EVENT_DRIVEN: it changes
    only when the schedule changes, so its silence is meaningful and normal, and
    a per-namespace timeout on it would be a false alarm generator."""
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)

    nin = bus.get_namespace("cylons/arrival/in")
    assert nin.cadence is Cadence.PERIODIC
    assert nin.refresh_interval == 1.0
    assert nin.staleness_timeout == 3.0

    nat = bus.get_namespace("cylons/arrival/at")
    assert nat.cadence is Cadence.EVENT_DRIVEN
    assert nat.staleness_timeout is None


# --------------------------------------------------------------------------- #
# 3. WHAT HAPPENS AT ZERO, AND AFTER
# --------------------------------------------------------------------------- #

def test_it_crosses_zero_and_goes_negative(rig):
    """IT DOES NOT CLAMP AND IT DOES NOT STOP.  Three rejected alternatives:

    CLAMP AT 0 — destroys information.  "Arrived 3 minutes ago" is exactly as
      useful as "arrives in 3 minutes", and a panel stuck on 0 is
      indistinguishable from a dead publisher, which is the one confusion the
      whole freshness contract exists to remove.
    STOP PUBLISHING — worse.  The namespace goes silent, the freshness contract
      CORRECTLY declares it stale, and the panel greys out at the dramatic
      moment.  A signal that dies on the event it was tracking is a signal that
      fails exactly when it is watched.
    NEGATIVE FOREVER — unbounded.  Hence the linger below.

    So: negative means ELAPSED-SINCE, and the sign IS the semantics.
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1003, now=1000.0)
    for t in (1001.0, 1002.0, 1003.0, 1004.0, 1005.0):
        clock.tick_once(now=t)
    assert _vals(bus, "cylons/arrival/in") == [3, 2, 1, 0, -1, -2]


def test_the_crossing_emits_exactly_one_event(rig):
    """The sign change alone is a poor trigger: a subscriber that joins after
    the crossing sees only a negative number and cannot tell whether it missed
    the moment.  One EVENT, NOT retained, fires behaviour."""
    bus, clock, svc = rig
    events = []
    bus.subscribe_events("cylons/arrival", lambda ns, et, d: events.append((et, d)))
    svc.schedule("cylons/arrival", at_unix=1002, now=1000.0)
    for t in (1001.0, 1002.0, 1003.0, 1004.0):
        clock.tick_once(now=t)
    assert [e[0] for e in events] == ["reached"]
    assert events[0][1]["at"] == 1002


def test_it_retires_after_the_declared_linger(rig):
    """Elapsed-since is useful for a while and then it is litter.

    `linger_s` is DECLARED per schedule rather than global: "cylons arrived 4 s
    ago" matters for a scene beat; "the jump completed 6 hours ago" is noise.
    At retirement the deriver stops and says so, rather than simply going quiet
    — a retired countdown and a crashed one must not look the same.
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1002, now=1000.0, linger_s=3)
    for t in (1001.0, 1002.0, 1003.0, 1004.0, 1005.0, 1006.0, 1007.0):
        clock.tick_once(now=t)
    assert _vals(bus, "cylons/arrival/in") == [2, 1, 0, -1, -2, -3, None]
    assert "cylons/arrival" not in svc.active


# --------------------------------------------------------------------------- #
# 4. CANCELLATION AND RESCHEDULING — "a countdown that cannot be cancelled is a
#    bug in a game."
# --------------------------------------------------------------------------- #

def test_rescheduling_is_last_writer_wins_and_moves_both_namespaces(rig):
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    svc.schedule("cylons/arrival", at_unix=1600, now=1001.0)
    assert _vals(bus, "cylons/arrival/at") == [1272, 1600]
    assert _vals(bus, "cylons/arrival/in") == [272, 599]


def test_cancel_stops_the_deriver(rig):
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    svc.cancel("cylons/arrival", now=1001.0)
    before = len(_vals(bus, "cylons/arrival/in"))
    for t in (1002.0, 1003.0):
        clock.tick_once(now=t)
    assert len(_vals(bus, "cylons/arrival/in")) == before
    assert "cylons/arrival" not in svc.active


def test_cancel_overwrites_the_RETAINED_at_rather_than_leaving_it(rig):
    """THE BUG THIS TEST EXISTS TO PREVENT.  `/at` is RETAINED.  A cancel that
    only stopped the deriver would leave a live future timestamp sitting in the
    broker, and the next subscriber to appear — or the next service restart —
    would read it and RESURRECT the cancelled countdown.

    So cancel PUBLISHES over it.  Note that it publishes `None` rather than
    issuing a true MQTT retain-clear (a zero-length payload): every subscriber on
    this stack runs `BusMessage.from_json` on receipt and a zero-length payload
    would raise there, turning a cancel into an exception on every listener.
    A retained `None` says "cancelled" in the vocabulary the stack already reads.
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    svc.cancel("cylons/arrival", now=1001.0)
    assert _vals(bus, "cylons/arrival/at") == [1272, None]
    assert _vals(bus, "cylons/arrival/in") == [272, None]


def test_cancelling_an_unknown_subject_is_not_an_error(rig):
    """Cancel is idempotent: a scene that cancels defensively on teardown must
    not have to know whether it ever scheduled."""
    bus, clock, svc = rig
    assert svc.cancel("never/scheduled", now=1.0) is False


# --------------------------------------------------------------------------- #
# 5. ⚠ THE MOTHERCARD RULE — a derived value's age is the MAX of its inputs'
#    ages, and UNKNOWN if ANY input is unknown.
# --------------------------------------------------------------------------- #

def test_a_countdown_from_an_undatable_input_publishes_none(rig):
    """A COUNTDOWN DERIVED FROM A DEAD CLOCK MUST NOT READ AS AUTHORITATIVE.

    The service derives `/in` from two inputs: the target `/at` and the tick.
    If the target's age is UNKNOWN — the case that actually happens, a retained
    `/at` received from a 3.1.1 publisher that could not stamp a Message Expiry
    Interval — then the remaining time computed from it is unknown too.

    PUBLISHING A NUMBER THERE WOULD BE THE EXACT LIE.  A panel showing "T-minus
    4:32" derived from an undatable schedule is more dangerous than a blank
    panel, because it is confidently wrong.  So the deriver publishes None,
    which every renderer already has to handle for "no value yet".
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0, at_age_s=None)
    assert _vals(bus, "cylons/arrival/in") == [None]


def test_a_dated_but_stale_input_is_corrected_not_rejected(rig):
    """UNKNOWN and OLD are different.  A retained `/at` that arrived with a
    KNOWN age of 30 s is perfectly usable — an absolute timestamp does not decay
    — so it is used, and the age is carried forward as the derived value's age.
    """
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0, at_age_s=30.0)
    assert _vals(bus, "cylons/arrival/in") == [272]
    assert svc.active["cylons/arrival"].input_age_s == 30.0


def test_remaining_now_corrects_a_retained_in_by_its_own_age():
    """THE RECEIVER-SIDE HALF, and it is where the rule really bites.

    A late subscriber gets the RETAINED `/in` — a number that was true when it
    was published.  With MQTT 5's broker-decremented Message Expiry Interval
    that message arrives carrying its own age, so the receiver can correct it:
    `remaining = value - age`.  Without an age (a 3.1.1 publisher) the value
    cannot be corrected and the honest answer is None, NOT the uncorrected
    number, which would be wrong by exactly the amount nobody can measure.
    """
    from composeio.countdown import remaining_now
    from composeio.transport import BusMessage

    live = BusMessage(namespace="cylons/arrival/in", type=MessageType.STATE,
                      value=272, retained=False, age_s=0.0)
    assert remaining_now(live) == 272

    dated = BusMessage(namespace="cylons/arrival/in", type=MessageType.STATE,
                       value=272, retained=True, age_s=30.0)
    assert remaining_now(dated) == 242

    undatable = BusMessage(namespace="cylons/arrival/in", type=MessageType.STATE,
                           value=272, retained=True, age_s=None)
    assert remaining_now(undatable) is None

    cancelled = BusMessage(namespace="cylons/arrival/in", type=MessageType.STATE,
                           value=None, retained=True, age_s=5.0)
    assert remaining_now(cancelled) is None


# --------------------------------------------------------------------------- #
# 6. WHO PUBLISHES — settled, with the two rejected owners named
# --------------------------------------------------------------------------- #

def test_the_declaring_app_declares_once_and_is_then_done(rig):
    """WHO PUBLISHES: a SERVICE co-located with the clock.  Not the bus core,
    not the leaf, and not the declaring app.

    NOT THE BUS CORE — a semantic scheduler in `bus.py` would make every leaf
      bus in the fleet a scheduler, including the ones created by `source_bus`
      that are explicitly NOT masters.  The bus routes; it does not know what
      cylons are.
    NOT THE DECLARING APP — if the app must republish at 1 Hz forever then every
      app needs a clock and a loop, which is the burden being removed, merely
      moved up one node instead of removed.
    NOT THE LEAF — the leaf has no clock.  That is the entire request.
    THE SERVICE — it IS a tick consumer on the bus clock, so `_bus/time` and
      every `/in` are computed from the SAME instant.  That makes the derived-age
      rule exact rather than approximately exact, for free.

    The app's whole obligation is one publish of `<subject>/at`.
    """
    bus, clock, svc = rig
    bus.publish_state("cylons/arrival/at", 1272, source="bsg-scene")
    clock.tick_once(now=1000.0)
    clock.tick_once(now=1001.0)
    assert _vals(bus, "cylons/arrival/in")[-2:] == [272, 271]


def test_a_second_service_refuses_to_double_derive_one_subject(rig):
    """Two derivers on one subject is the two-masters clock argument again —
    the same fault `announce_time=False` exists to prevent one layer down, and
    it fails LOUDLY here rather than producing a `/in` that jitters between two
    publishers' arithmetic."""
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    other = CountdownService(bus, clock=clock)
    try:
        with pytest.raises(ValueError, match="already derived"):
            other.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    finally:
        other.stop()


def test_the_claim_is_released_on_cancel_so_a_restart_can_take_it_back(rig):
    bus, clock, svc = rig
    svc.schedule("cylons/arrival", at_unix=1272, now=1000.0)
    svc.cancel("cylons/arrival", now=1001.0)
    other = CountdownService(bus, clock=clock)
    try:
        other.schedule("cylons/arrival", at_unix=1600, now=1002.0)
        assert "cylons/arrival" in other.active
    finally:
        other.stop()
