"""COUNTDOWN — a derived, semantic "time remaining" on the bus.

David: "There should also be a countdown time signal that can be tied to a
specific semantic message, e.g. 'cylons arrive in X minutes' (which is really
nothing more than a future time subtracted from the current time, but it's a
useful convenience/cover method so that local devices, some of which might be
capability limited, need not do that math themselves)."

⛔ THE WHY, AND IT IS THE PART THAT MUST NOT BE LOST.  A CH32V003, or a panel
whose entire output is a TM1637, should not need a clock, a subtraction and a
re-publish loop in order to display "T-minus 4:32".  The computation belongs at
the CHEAPEST NODE THAT CAN FULLY OWN IT — and that node is not the leaf, because
the leaf lacks the clock.

This is the [P1] "push complexity down to the leaf" rule producing the OPPOSITE
answer from usual, and the exception deserves stating rather than hiding:
COMPLEXITY FLOWS DOWN TO THE LEAF EXCEPT WHERE THE LEAF STRUCTURALLY CANNOT HOLD
IT.  A leaf that must first be told the time in order to subtract from it has not
been given a cheaper job by being made to subtract; it has been given the same
job plus a clock it cannot keep.


THE WIRE  (both, on sibling namespaces)
---------------------------------------
    <subject>/at   target absolute Unix timestamp, uint32 seconds.  RETAINED,
                   published once per schedule, not per tick.  The authoritative,
                   durable fact.
    <subject>/in   remaining SIGNED whole seconds.  Derived, republished every
                   tick, declares a PERIODIC cadence so a receiver can tell when
                   the deriver stopped.

Declining either one strands a real consumer.  `/at` alone demands the clock the
capability-limited leaf does not have — the request, refused.  `/in` alone makes
the schedule unrecoverable and unauditable: nothing on the bus would record WHEN
the thing happens, only how long until it does according to a publisher no one
can check, and a service restart could not resume.  The pair costs one extra
retained integer per countdown.


UNITS
-----
Whole signed seconds, always.  "4 minutes", "04:32" and "272" are three
RENDERINGS of one fact, and per [CXC-VISION-SEMANTIC-REALIZATION] formatting is
a backend concern: putting any rendering on the wire picks a locale, a precision
and a display width on behalf of every consumer not yet written.  A TM1637 doing
`divmod(n, 60)` is two integer ops.


AT ZERO
-------
It crosses and goes NEGATIVE — negative means elapsed-since, and the sign is the
semantics — plus exactly one `reached` EVENT at the crossing, then retirement
after a declared linger.  Clamping at 0 destroys information and makes a live
panel indistinguishable from a dead one; stopping makes the signal die on the
event it was tracking, at the exact moment it is being watched.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from composeio.bus_clock import BusClock, shared_clock
from composeio.namespace import Scope
from composeio.transport import BusMessage

logger = logging.getLogger("composeio.countdown")

#: Cadence `<subject>/in` DECLARES, in seconds.  It matches `_bus/time` because
#: it is the same fact re-expressed: a countdown that ticked slower than the
#: clock would show a number the clock could already contradict.
COUNTDOWN_INTERVAL_S = 1.0

#: How long elapsed-since keeps being published after the crossing.  Per
#: schedule, not global: "cylons arrived 4 s ago" is a scene beat, "the jump
#: completed 6 hours ago" is litter.
DEFAULT_LINGER_S = 3600

#: Suffixes.  `at`/`in` read as English at the call site ("cylons/arrival/at",
#: "cylons/arrival/in") and neither one names a mechanism, so a second deriver
#: could serve the same namespaces without a rename.
AT_SUFFIX = "at"
IN_SUFFIX = "in"


class Countdown:
    """One scheduled subject and the state its deriver needs."""

    __slots__ = ("subject", "at_unix", "linger_s", "input_age_s", "_reached")

    def __init__(self, subject: str, at_unix: int, linger_s: float,
                 input_age_s: float | None):
        self.subject = subject
        self.at_unix = int(at_unix)
        self.linger_s = linger_s
        #: Age of the `/at` input at the moment it was accepted, or None for
        #: UNKNOWN.  Carried, not discarded: it is the derived value's age under
        #: the mothercard rule.
        self.input_age_s = input_age_s
        self._reached = False

    @property
    def at_namespace(self) -> str:
        return f"{self.subject}/{AT_SUFFIX}"

    @property
    def in_namespace(self) -> str:
        return f"{self.subject}/{IN_SUFFIX}"

    def remaining(self, now: float) -> int | None:
        """Seconds until (positive) or since (negative) the target.

        ⚠ THE MOTHERCARD RULE.  A derived value's age is the MAX of its inputs'
        ages, and it is UNKNOWN if ANY input is unknown.  This deriver has two
        inputs: the target and the tick.  When the target's age is UNKNOWN — the
        case that actually happens, a retained `/at` from a 3.1.1 publisher that
        could not stamp a Message Expiry Interval — the remaining time computed
        from it is unknown too, and the honest output is None.

        PUBLISHING A NUMBER THERE WOULD BE THE EXACT LIE.  A panel reading
        "T-minus 4:32" derived from an undatable schedule is worse than a blank
        panel, because it is confidently wrong at the moment it is most trusted.

        Note that a KNOWN-BUT-OLD input is fine and is NOT rejected: an absolute
        timestamp does not decay, so a `/at` that arrived 30 s stale is still
        exactly as true.  UNKNOWN and OLD are different, and conflating them
        would throw away most of the fleet's usable schedules.
        """
        if self.input_age_s is None:
            return None
        return int(self.at_unix - int(now))

    def expired(self, now: float) -> bool:
        rem = self.remaining(now)
        return rem is not None and rem < -self.linger_s


class CountdownService:
    """Derives `<subject>/in` from `<subject>/at` and the bus clock.

    WHO PUBLISHES — settled, with the rejected owners named, because the
    alternatives are each superficially reasonable:

    NOT THE BUS CORE.  A semantic scheduler in `bus.py` makes every bus in the
      fleet a scheduler, including the leaf buses `source_bus` creates that are
      explicitly NOT masters.  The bus routes messages; it does not know what a
      cylon is, and the moment it does, every embedded port inherits that.

    NOT THE DECLARING APP.  If the app that knows the schedule must also
      republish at 1 Hz forever, then every app needs a clock and a loop — which
      is the burden being removed, merely relocated one node up rather than
      removed.  The app's whole obligation is ONE publish of `<subject>/at`.

    NOT THE LEAF.  The leaf has no clock.  That is the entire request.

    A SERVICE CO-LOCATED WITH THE CLOCK.  It is a tick consumer on the same
      `BusClock` that drives `_bus/time`, so the clock announcement and every
      `/in` are computed from the SAME instant — which makes the derived-age
      accounting exact rather than approximately exact, for free.
    """

    def __init__(self, bus, clock: BusClock | None = None):
        self._bus = bus
        self._clock = clock if clock is not None else shared_clock()
        self.active: dict[str, Countdown] = {}
        self._clock.attach(self._tick, interval=COUNTDOWN_INTERVAL_S)
        # Adopt schedules published by ANYONE, so an app that simply publishes
        # `<subject>/at` gets a countdown without importing this module. That is
        # the credo applied to a service: a capability reachable only through a
        # Python import is not reachable over the bus.
        bus.on_message(self._on_bus_message)

    # --- lifecycle -------------------------------------------------------- #

    def stop(self) -> None:
        self._clock.detach(self._tick)
        self._bus.off_message(self._on_bus_message)
        for subject in list(self.active):
            self._release(subject)

    # --- the API an app calls once ---------------------------------------- #

    def schedule(self, subject: str, at_unix: int, *, now: float | None = None,
                 linger_s: float = DEFAULT_LINGER_S,
                 at_age_s: float | None = 0.0) -> Countdown:
        """Declare that *subject* happens at *at_unix*, and start deriving.

        `at_age_s` is the AGE OF THE INPUT — 0.0 for a schedule declared right
        here, or the `age_s` of a retained `/at` message a restarting service is
        resuming from.  None means undatable; see `Countdown.remaining`.
        """
        now = time.time() if now is None else now
        self._claim(subject)

        cd = Countdown(subject, at_unix, linger_s, at_age_s)
        self.active[subject] = cd
        self._declare_namespaces(cd)

        # `/at` FIRST, then `/in`. A subscriber that sees the derived value
        # before the fact it derives from has a moment where it cannot check the
        # arithmetic, and on a retained bus that moment survives a restart.
        self._bus.publish_state(cd.at_namespace, int(at_unix),
                                source="countdown")
        self._publish_remaining(cd, now)
        return cd

    def cancel(self, subject: str, *, now: float | None = None) -> bool:
        """Cancel *subject*.  Returns False if it was not scheduled.

        ⛔ IT OVERWRITES THE RETAINED `/at` RATHER THAN JUST STOPPING.  `/at` is
        a RETAINED namespace, so a cancel that only stopped the deriver would
        leave a live future timestamp sitting in the broker — and the next
        subscriber to appear, or the next restart of this service, would read it
        and RESURRECT the cancelled countdown.  In a game that is a Cylon attack
        nobody ordered.

        It publishes `None` rather than issuing a true MQTT retain-clear (a
        zero-length payload): every subscriber on this stack runs
        `BusMessage.from_json` on receipt, and a zero-length payload raises
        there — turning one cancel into an exception on every listener.  A
        retained `None` says "cancelled" in vocabulary the stack already reads.
        """
        if subject not in self.active:
            # Idempotent: a scene tearing down defensively must not have to know
            # whether it ever scheduled.
            return False
        self._bus.publish_state(f"{subject}/{AT_SUFFIX}", None,
                                source="countdown")
        self._bus.publish_state(f"{subject}/{IN_SUFFIX}", None,
                                source="countdown")
        del self.active[subject]
        self._release(subject)
        return True

    # --- the deriver ------------------------------------------------------ #

    def _tick(self, now: float) -> None:
        for subject in list(self.active):
            cd = self.active.get(subject)
            if cd is None:
                continue
            # EXPIRY IS CHECKED BEFORE PUBLISHING, not after. Publishing the
            # elapsed value and then retracting it with a None IN THE SAME TICK
            # puts a number on the bus that no subscriber can act on and that
            # the next message immediately contradicts — and on a retained
            # namespace a subscriber arriving between the two would latch it.
            if cd.expired(now):
                self._retire(cd)
                continue
            self._publish_remaining(cd, now)

    def _publish_remaining(self, cd: Countdown, now: float) -> None:
        remaining = cd.remaining(now)
        self._bus.publish_state(cd.in_namespace, remaining, source="countdown")
        if remaining is not None and remaining <= 0 and not cd._reached:
            cd._reached = True
            # ONE event, NOT retained. The sign change alone is a poor trigger:
            # a subscriber joining after the crossing sees only a negative
            # number and cannot tell whether it missed the moment.
            self._bus.publish_event(cd.subject, "reached",
                                    {"at": cd.at_unix}, source="countdown")

    def _retire(self, cd: Countdown) -> None:
        """Stop deriving, and SAY SO.

        A retired countdown and a crashed one must not look the same on the bus.
        Simply going quiet would let the freshness contract report the deriver as
        dead — a false alarm produced by orderly completion.
        """
        self._bus.publish_state(cd.in_namespace, None, source="countdown")
        self.active.pop(cd.subject, None)
        self._release(cd.subject)

    # --- adoption from the bus -------------------------------------------- #

    def _on_bus_message(self, msg: BusMessage) -> None:
        """Adopt a schedule published by anyone, over the bus.

        DELIBERATELY DOES NOT PUBLISH IMMEDIATELY, unlike `schedule()`. Two
        reasons: `/at` is already on the bus (the publisher just put it there),
        so echoing it would be this service arguing with its own input; and the
        only "now" this service trusts is the clock's, so it waits for the next
        tick rather than inventing one.
        """
        if not msg.namespace.endswith("/" + AT_SUFFIX):
            return
        if msg.source == "countdown":
            return                      # our own publish — the loop guard
        subject = msg.namespace[: -(len(AT_SUFFIX) + 1)]
        if msg.value is None:
            if subject in self.active:
                self.active.pop(subject, None)
                self._release(subject)
            return
        try:
            at_unix = int(msg.value)
        except (TypeError, ValueError):
            logger.warning("countdown: %s carried a non-timestamp %r",
                           msg.namespace, msg.value)
            return
        existing = self.active.get(subject)
        if existing is not None:
            existing.at_unix = at_unix
            existing.input_age_s = msg.age_s
            existing._reached = False
            return
        self._claim(subject)
        cd = Countdown(subject, at_unix, DEFAULT_LINGER_S, msg.age_s)
        self.active[subject] = cd
        self._declare_namespaces(cd)

    # --- declarations and claims ------------------------------------------ #

    def _declare_namespaces(self, cd: Countdown) -> None:
        """Register both siblings WITH their [D4] freshness contracts.

        `/in` is PERIODIC: silence means the deriver died, which is exactly the
        fact a receiver needs.  `/at` is EVENT_DRIVEN: it changes only when the
        schedule does, so its silence is normal and a timeout on it would be a
        false-alarm generator.
        """
        self._bus.register_namespace(
            cd.at_namespace, "json", scope=Scope.GLOBAL,
            quiet_by_construction=True)
        self._bus.register_namespace(
            cd.in_namespace, "json", scope=Scope.GLOBAL,
            refresh_interval=COUNTDOWN_INTERVAL_S)

    @staticmethod
    def _claims_for(bus) -> dict:
        """The per-bus subject->owner claim table, OWNED BY COUNTDOWN.

        Created lazily on first claim rather than in the bus constructor: the
        claim table is a countdown concern that had LEAKED into the kernel's
        __init__ (a micro-kernel violation). It lives on the bus object (one
        table per bus, which is the invariant the two-masters check needs) but
        the kernel no longer knows it exists ([DEC-...-BUS-STANDUP-2026-08-23]).
        """
        claims = getattr(bus, "_countdown_claims", None)
        if claims is None:
            claims = {}
            bus._countdown_claims = claims
        return claims

    def _claim(self, subject: str) -> None:
        """One deriver per subject.  Two is the two-masters clock argument.

        It fails LOUDLY rather than letting `/in` jitter between two publishers'
        arithmetic — a symptom that looks like network jitter and is not.
        """
        claims = self._claims_for(self._bus)
        owner = claims.get(subject)
        if owner is not None and owner is not self:
            raise ValueError(
                f"{subject!r} is already derived by another CountdownService on "
                f"bus {self._bus.bus_id!r}. Two derivers on one subject produce "
                f"a `/in` that alternates between their arithmetic.")
        claims[subject] = self

    def _release(self, subject: str) -> None:
        claims = self._claims_for(self._bus)
        if claims.get(subject) is self:
            del claims[subject]


def remaining_now(msg: BusMessage) -> int | None:
    """Correct a received `<subject>/in` by its OWN age.  THE RECEIVER-SIDE HALF.

    This is where the mothercard rule really bites.  A late subscriber gets the
    RETAINED `/in` — a number that was true when it was published and is wrong by
    however long it sat in the broker.  With MQTT 5's broker-decremented Message
    Expiry Interval that message arrives carrying its own age, so the correction
    is exact and needs NO clock agreement between publisher and subscriber:

        remaining = value - age

    Without an age (a 3.1.1 publisher, an older peer) the value CANNOT be
    corrected, and the honest answer is None rather than the uncorrected number
    — which is wrong by precisely the amount nobody can measure.
    """
    if msg.value is None:
        return None
    if msg.age_s is None:
        return None
    return int(msg.value) - int(msg.age_s)
