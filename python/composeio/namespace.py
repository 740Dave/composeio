"""Namespace — the identity of a function on the bus.

DP-CIO-2: Modules are connected by shared namespace, not hardware address.
A namespace carries a state engine, current value, scope, and subscriber list.
"""

from __future__ import annotations
import collections
import time
from enum import Enum
from typing import Any, Callable
from composeio.freshness import (
    UNKNOWN, Verdict, is_unknown, verdict_is_believable,
)
from composeio.state_engine import StateEngine


class Scope(Enum):
    LOCAL = "local"
    GLOBAL = "global"
    REMOTE = "remote"
    AUTO = "auto"


class Authority(Enum):
    LOCAL = "local"
    EXTERNAL = "external"


class Cadence(Enum):
    """How OFTEN a namespace speaks — the shape of its freshness contract.

    [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML] ratified a declared
    refresh interval plus a receiver-side age counter.  Its 2026-08-14 amendment
    then found, BY MEASUREMENT, that the ratified mechanism has a third case it
    does not cover, so all three are named here rather than one being the
    default the other two are exceptions to.
    """

    #: A declared interval.  Silence past the timeout means the publisher is
    #: gone.  `_bus/time` is the fleet's first member.
    PERIODIC = "periodic"

    #: QUIET BY CONSTRUCTION.  `controller_main.cpp:449-468` gates the throttle
    #: on a sample clock AND a deadband against the last EMITTED value — its own
    #: comment says "quiet-bus by construction".  So it is ~1 Hz while moving and
    #: ABSOLUTELY SILENT at rest, by design.  A per-namespace timeout here is
    #: MEANINGLESS: silence is the normal state and can never indicate a dead
    #: publisher.  Liveness for this class comes only from the per-node
    #: heartbeat, which is what makes that heartbeat load-bearing.
    EVENT_DRIVEN = "event_driven"

    #: Nothing was declared.  NOT a synonym for either of the above, and
    #: emphatically not a synonym for "fresh".
    UNDECLARED = "undeclared"


#: [D4]'s staleness multiplier.  THREE TIMES the declared interval — and the
#: spec must say CONVENTION rather than requirement, because that is what it is:
#: J1939 uses 3x, CANopen and NMEA 2000 use comparable receiver-side timeouts,
#: and none of them derives the number from anything but experience.
STALENESS_MULTIPLIER = 3.0

#: Floor, so a fast cadence cannot produce a timeout inside its own jitter.
STALENESS_FLOOR_MARGIN_S = 0.1


class Namespace:
    """A named channel on the ComposeIO bus.

    Producers publish state changes to a namespace.
    Consumers subscribe and receive callbacks.
    The state engine defines the value domain.
    """

    def __init__(
        self,
        name: str,
        state_engine: StateEngine,
        scope: Scope = Scope.AUTO,
        authority: Authority = Authority.LOCAL,
        label: str | None = None,
        mode: str = "state",
        retention: int = 100,
        access_publish: list[str] | None = None,
        access_subscribe: list[str] | None = None,
        refresh_interval: float | None = None,
        timeout: float | None = None,
        quiet_by_construction: bool = False,
        clock: Callable[[], float] | None = None,
    ):
        # THE RECEIVER'S CLOCK IS MONOTONIC AND INJECTABLE  [D4].
        #
        # `time.monotonic`, never `time.time`: D4 rejected wall-clock reasoning
        # outright because the fleet HAS NO SHARED TIME BASE — `_bus/time` is
        # one-way, one-second-resolution, with no delay compensation, and
        # CH32V003 parts on internal oscillators drift about 36 s/hour, so the
        # clock-offset error would EXCEED the staleness being measured. A
        # monotonic clock measures an interval on ONE machine, which is the only
        # quantity any of this needs and the only one available. It also cannot
        # be dragged backwards by NTP mid-flight, which would otherwise make a
        # value spontaneously un-expire.
        #
        # Injectable so a test can drive it by hand. A freshness test that used
        # real sleeps would be slow, flaky, and — worse — unable to assert the
        # NEGATIVE control precisely, because "not yet stale at 2.999 s" is not
        # a claim a wall-clock sleep can make honestly.
        self._clock = clock if clock is not None else time.monotonic
        #: Monotonic instant of the last RECEIPT (not the last CHANGE).
        self._last_receipt: float | None = None
        #: Set when a publisher sent an explicit wire-unknown (0xFD).
        self._wire_unknown: bool = False
        #: Installed by the bus: "is the node that publishes me still beating?"
        self._node_liveness: Callable[[], bool] | None = None
        # --- THE FRESHNESS CONTRACT, DECLARED  [D4] ------------------------
        # David, ratifying: "This should be configurable in YAML, ideally
        # specifying the refresh interval, max age, timeout, etc."  Two fields
        # carry all of that, and the omission of a third is deliberate:
        #
        #   refresh_interval — how often this namespace SPEAKS.
        #   timeout          — the age past which its value is STALE.
        #
        # `max_age` is NOT a third field.  "Max age" and "timeout" name the same
        # quantity — the age past which a value stops being believed — and
        # shipping both invites a config that declares them differently, at
        # which point the code has to pick a winner and the YAML has stopped
        # being the contract.  One quantity, one field.
        #
        # THE THIRD CASE gets a boolean rather than a magic interval value,
        # because "quiet by construction" is a POSITIVE DECLARATION about a
        # publisher's design ("I emit only on change, and that is a feature")
        # and not the absence of a number.
        if quiet_by_construction and refresh_interval is not None:
            raise ValueError(
                f"namespace {name!r} declares BOTH refresh_interval="
                f"{refresh_interval} and quiet_by_construction=True. Those are "
                f"contradictory claims about the same publisher: a cadence says "
                f"'silence means I am dead', quiet-by-construction says "
                f"'silence is normal'. Declare one.")
        if refresh_interval is not None and refresh_interval <= 0:
            raise ValueError(
                f"namespace {name!r}: refresh_interval must be > 0 "
                f"(got {refresh_interval!r}). Zero or negative is not 'as fast "
                f"as possible', it is an undeclared cadence wearing a number.")
        if timeout is not None and timeout <= 0:
            raise ValueError(
                f"namespace {name!r}: timeout must be > 0 (got {timeout!r})")
        self.refresh_interval = refresh_interval
        self._timeout = timeout
        self.quiet_by_construction = quiet_by_construction

        self.name = name
        self.label = label  # DP-CIO-2: human-readable display name (machine contract vs human contract)
        self.state_engine = state_engine
        self.scope = scope
        self.authority = authority
        self.mode = mode  # BP-EXT-LOG: "state", "event_only", or "log"
        self.retention = retention  # BP-EXT-LOG: max log entries (log mode only)
        self.access_publish = access_publish  # BP-EXT-ACCESS: publisher whitelist (None = any)
        self.access_subscribe = access_subscribe  # BP-EXT-ACCESS: subscriber whitelist (None = any)
        self._subscribers: list[Callable] = []
        self._event_subscribers: list[Callable] = []
        self._log_entries: collections.deque = collections.deque(maxlen=retention if mode == "log" else None)

    @property
    def value(self) -> Any:
        """The current value — or `UNKNOWN` if it is no longer believable.

        ⛔ THIS IS THE WHOLE DEFECT'S FIX, AND IT IS ONE PROPERTY.  It used to be
        `return self.state_engine.current`, unconditionally, which is why
        David's Dradis read `contacts: 3` for hours after the publisher died:
        nothing anywhere expired a value, so a frozen last-known reading was
        rendered as though current.

        EXPIRY IS LAZY — computed HERE, at read, never by a sweep.  D4: "a
        timer-driven sweep means a stalled receiver demotes the whole world and
        blames the world."  A blocked GUI thread must not manufacture a fleet-
        wide outage out of its own stall, and with lazy expiry it cannot: the
        verdict is only ever computed by someone actually asking.

        IT IS ALSO SELF-HEALING.  Nothing latches; when the publisher comes
        back, the next receipt makes the next read FRESH again with no reset
        step to forget.

        ONE MEASUREMENT PATH: every reader of a namespace's value comes through
        here — `bus.get_state`, the transports, the monitors, every widget —
        so a stale value cannot leak out through a second accessor that forgot
        to check. `raw_value` is the deliberate, named exception.
        """
        if verdict_is_believable(self.verdict):
            return self.state_engine.current
        return UNKNOWN

    @property
    def raw_value(self) -> Any:
        """The stored value with NO freshness judgement applied.

        For diagnostics that need the last thing actually received — a bus
        monitor showing "last seen: 3 (17 s ago)" is more useful than one
        showing "unknown", because the operator wants to know WHAT went stale.
        ⛔ Never render this on a control surface: it is precisely the frozen
        number the freshness receiver exists to stop presenting as current.
        """
        return self.state_engine.current

    # --- THE RECEIVER  [D4] ------------------------------------------------ #

    def mark_received(self, *, wire_unknown: bool = False) -> None:
        """Stamp a receipt on the monotonic clock.

        ⛔ RECEIPT, NOT CHANGE.  `publish_state` notifies subscribers only when
        `new != old`, so a publisher faithfully restating a steady value fires
        no callback at all. An age counter driven by the CALLBACK would expire a
        publisher that is transmitting perfectly — a false alarm produced by the
        very mechanism built to prevent false confidence, and one that would
        appear only for the steadiest signals in the fleet.
        """
        self._last_receipt = self._clock()
        self._wire_unknown = wire_unknown

    def attach_node_liveness(self, probe: Callable[[], bool] | None) -> None:
        """Install "is my publishing node still beating?".

        The bus installs this once it knows which node publishes this namespace
        (from the message envelope's `src`). It is the ONLY thing that can
        condemn a quiet-by-construction namespace, whose own silence proves
        nothing.
        """
        self._node_liveness = probe

    @property
    def age(self) -> float | None:
        """Seconds since the last receipt, or None if nothing ever arrived."""
        if self._last_receipt is None:
            return None
        return self._clock() - self._last_receipt

    @property
    def verdict(self) -> Verdict:
        """WHY this value is or is not believable — the receiver's full answer.

        The ordering of these branches is load-bearing:

        1. WIRE_UNKNOWN first. An explicit 0xFD from a live publisher is a
           POSITIVE statement ("I am alive and I know that I do not know") and
           must not be relabelled by any age rule.
        2. UNDECLARED before anything time-based. A namespace that declared no
           contract gets no verdict invented for it — reporting it FRESH would
           claim confidence nobody earned, and reporting it STALE would expire
           live values on the strength of a declaration that was never made.
        3. NEVER_RECEIVED before STALE, and only for a namespace that DID
           declare. There is no last value being disbelieved; there was never a
           first one, and the two call for different operator responses.
        4. Quiet-by-construction routes to the NODE, never to a timeout.
        """
        if self._wire_unknown:
            return Verdict.WIRE_UNKNOWN

        if self.cadence is Cadence.UNDECLARED:
            return Verdict.UNDECLARED

        if self._last_receipt is None:
            return Verdict.NEVER_RECEIVED

        if self.cadence is Cadence.EVENT_DRIVEN:
            # ⛔ SILENCE PROVES NOTHING HERE — it is the declared design.
            # `controller_main.cpp` gates the throttle on a sample clock AND a
            # deadband against the last EMITTED value ("quiet-bus by
            # construction"), so a stationary lever publishes NOTHING,
            # indefinitely, while being perfectly healthy. The only evidence
            # that can condemn this class is the ABSENCE OF ITS NODE.
            if self._node_liveness is not None and not self._node_liveness():
                return Verdict.NODE_SILENT
            return Verdict.FRESH

        timeout = self.staleness_timeout
        if timeout is None:
            return Verdict.FRESH
        age = self._clock() - self._last_receipt
        return Verdict.STALE if age >= timeout else Verdict.FRESH

    @property
    def is_stale(self) -> bool:
        """Convenience: whether a read would hand back UNKNOWN."""
        return not verdict_is_believable(self.verdict)

    def freshness_status(self) -> dict:
        """The receiver's state, for a monitor or a status endpoint."""
        return {
            "cadence": self.cadence.value,
            "verdict": self.verdict.value,
            "age_s": self.age,
            "staleness_timeout_s": self.staleness_timeout,
            "refresh_interval_s": self.refresh_interval,
            "last_value": self.state_engine.current,
        }

    # --- THE DECLARED FRESHNESS CONTRACT  [D4] ----------------------------- #

    @property
    def cadence(self) -> Cadence:
        """Which of D4's three cases this namespace declared itself to be."""
        if self.quiet_by_construction:
            return Cadence.EVENT_DRIVEN
        if self.refresh_interval is not None:
            return Cadence.PERIODIC
        return Cadence.UNDECLARED

    @property
    def staleness_timeout(self) -> float | None:
        """Age past which this namespace's value is STALE — or None for "no
        such thing", which is a real answer and not a missing one.

        None for EVENT_DRIVEN: a timeout on a namespace whose silence is normal
        would declare `throttle/left` expired seconds after David stops touching
        it — a false alarm on the fleet's most visible signal, produced by a
        mechanism built to prevent false confidence.

        None for UNDECLARED: UNKNOWN IS NOT FRESH, and it is not stale either.
        Inventing an interval would expire live values; assuming "never stale"
        would bless dead ones.  Both are verdicts the declaration never made, so
        the caller is told it does not know and decides what to do about that.
        """
        if self.quiet_by_construction or self.refresh_interval is None:
            return None
        if self._timeout is not None:
            return self._timeout
        return max(STALENESS_MULTIPLIER * self.refresh_interval,
                   self.refresh_interval + STALENESS_FLOOR_MARGIN_S)

    def declare_freshness(self, *, refresh_interval: float | None = None,
                          timeout: float | None = None,
                          quiet_by_construction: bool = False) -> None:
        """(Re)declare this namespace's freshness contract, IN PLACE.

        Validation is shared with `__init__` by construction — a throwaway
        Namespace is built purely to run the checks — so a contradictory
        re-declaration is refused by the SAME rule that refuses it at
        registration, rather than by a second copy of that rule which would
        eventually disagree with the first.
        """
        probe = Namespace(self.name, self.state_engine,
                          refresh_interval=refresh_interval, timeout=timeout,
                          quiet_by_construction=quiet_by_construction)
        self.refresh_interval = probe.refresh_interval
        self._timeout = probe._timeout
        self.quiet_by_construction = probe.quiet_by_construction

    def freshness_to_dict(self) -> dict | None:
        """The declaration, or None when nothing was declared.

        None rather than a block full of nulls: a namespace that declared
        nothing must not appear on the wire wearing the shape of one that did.
        """
        if self.cadence is Cadence.UNDECLARED:
            return None
        d: dict[str, Any] = {"cadence": self.cadence.value}
        if self.refresh_interval is not None:
            d["refresh_interval"] = self.refresh_interval
        d["staleness_timeout"] = self.staleness_timeout
        return d

    def subscribe(self, callback: Callable):
        """Subscribe to state changes. Callback receives (namespace_name, new_value, old_value)."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable):
        """Remove a state change subscriber."""
        self._subscribers.remove(callback)

    def subscribe_events(self, callback: Callable):
        """Subscribe to one-shot events. Callback receives (namespace_name, event_type, data)."""
        self._event_subscribers.append(callback)

    def publish_state(self, value: Any, source: str = "") -> Any:
        """Publish a new state value. Notifies all subscribers. Returns new value.

        EVERY publish stamps a receipt, INCLUDING one that changes nothing —
        see `mark_received` for why the callback is the wrong trigger.

        ⛔ UNKNOWN BYPASSES THE STATE ENGINE ENTIRELY.  `unknown` is a TYPE, not
        a member of any engine's value domain, so validating it against one is a
        category error — and a dangerous one here: a `binary` engine handed an
        out-of-domain value raises inside a Tk callback, which on macOS becomes
        an NSException and aborts the whole process with SIGABRT. A publisher
        legitimately saying "I do not know" must never be able to kill the
        surface rendering it. The engine keeps its last value untouched so a
        monitor can still show what was last actually received.
        """
        if is_unknown(value):
            self.mark_received(wire_unknown=True)
            old = self.state_engine.current
            for cb in self._subscribers:
                cb(self.name, UNKNOWN, old)
            return UNKNOWN

        old = self.state_engine.current
        self.state_engine.set(value)
        new = self.state_engine.current
        self.mark_received()
        if new != old:
            for cb in self._subscribers:
                cb(self.name, new, old)
        return new

    def advance(self, source: str = "") -> Any:
        """Advance to next state (toggle input). Returns new value."""
        old = self.state_engine.current
        new = self.state_engine.advance()
        self.mark_received()
        if new != old:
            for cb in self._subscribers:
                cb(self.name, new, old)
        return new

    def publish_event(self, event_type: str, data: dict | None = None, source: str = ""):
        """Publish a one-shot event (not a state change)."""
        for cb in self._event_subscribers:
            cb(self.name, event_type, data or {})

    def publish_log(self, value: Any, source: str = ""):
        """Append a log entry (BP-EXT-LOG). Notifies event subscribers."""
        entry = {
            "value": value,
            "source": source,
            "timestamp": time.time(),
        }
        self._log_entries.append(entry)
        # Notify event subscribers with the log entry data
        for cb in self._event_subscribers:
            cb(self.name, "log_entry", value)

    def history(self, since: float = 0, limit: int = 100) -> list[dict]:
        """Query log entries (BP-EXT-LOG).

        Args:
            since: Only return entries with timestamp >= since. 0 = all.
            limit: Max entries to return.
        """
        entries = list(self._log_entries)
        if since > 0:
            entries = [e for e in entries if e["timestamp"] >= since]
        return entries[:limit]

    def check_publish_access(self, source: str) -> bool:
        """Validate source against publish whitelist (BP-EXT-ACCESS).

        Returns True if publishing is allowed.
        None whitelist = any source allowed.
        """
        if not self.access_publish:
            return True
        if source in self.access_publish:
            return True
        # Check @capability: patterns — source may match a capability class
        for entry in self.access_publish:
            if entry.startswith("@capability:") and source.startswith(entry):
                return True
        return False

    def check_subscribe_access(self, client_id: str) -> bool:
        """Validate client against subscribe whitelist (BP-EXT-ACCESS).

        Returns True if subscribing is allowed.
        None whitelist = any client allowed.
        """
        if not self.access_subscribe:
            return True
        if client_id in self.access_subscribe:
            return True
        for entry in self.access_subscribe:
            if entry.startswith("@capability:") and client_id.startswith(entry):
                return True
        return False

    def has_local_subscribers(self) -> bool:
        """Check if any local subscribers exist (for auto-scope resolution)."""
        return len(self._subscribers) > 0 or len(self._event_subscribers) > 0

    @property
    def display_name(self) -> str:
        """Human-readable name: label if set, else title-cased namespace."""
        if self.label:
            return self.label
        return self.name.replace("_", " ").title()

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "value": self.value,
            "engine": self.state_engine.to_dict(),
            "scope": self.scope.value,
            "authority": self.authority.value,
            "subscriber_count": len(self._subscribers),
            "mode": self.mode,
        }
        if self.label:
            d["label"] = self.label
        if self.mode == "log":
            d["retention"] = self.retention
            d["log_entry_count"] = len(self._log_entries)
        if self.access_publish:
            d["access_publish"] = self.access_publish
        if self.access_subscribe:
            d["access_subscribe"] = self.access_subscribe
        freshness = self.freshness_to_dict()
        if freshness is not None:
            d["freshness"] = freshness
        return d
