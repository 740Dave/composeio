"""The bus clock — ONE periodic driver, cadences supplied by DECLARATION.

WHY THIS EXISTS.  `_bus/time` was a single stale integer: `_publish_bus_time`
had exactly one caller repo-wide (`Bus.start()`), so it fired once at startup and
never again.  `Bus.publish_heartbeat()` was worse — it had NO caller at all, only
its own definition, while spec/01 described heartbeat-timeout-driven degraded
mode as working behaviour.  Both are periodic publishers, and neither was
periodic.

WHY IT IS A SEPARATE MODULE AND NOT A `threading.Timer` IN `bus.py`.  Three
reasons, in order of how much they cost when ignored:

1. THE CADENCE MUST COME FROM THE DECLARATION.
   [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML] ratified that each
   namespace declares its own refresh interval.  `_bus/time` is the fleet's
   FIRST namespace that is periodic by construction, which makes it that
   decision's first real consumer.  A literal `1.0` in a timer call would make
   the declaration decorative on the exact case that is meant to prove it.  So
   the clock takes an interval from its caller, who reads it off the namespace.

2. ONE THREAD PER PROCESS, NOT ONE PER BUS.  There is one wall clock.  A
   process that builds forty buses (the test suite builds thousands) must not
   build forty threads, and a second periodic namespace landing next month must
   not add a third.  Subscribers each carry their own interval and fire on their
   own schedule; the thread's period is the finest of them.

3. IT MUST BE DRIVABLE WITHOUT SLEEPING.  `tick_once(now=...)` advances the
   clock deterministically, which is what lets the countdown service be tested
   as arithmetic instead of as a race.

⛔ SUBSCRIBERS ARE HELD WEAKLY, and that is load-bearing rather than tidy.  A
caller that builds a bus and drops it without calling `stop()` would otherwise
leave a callback publishing into a dead bus for the life of the process.  Asking
3,539 tests and every future script to remember `stop()` is the kind of rule that
holds for a month.  A weak reference makes the collection structural.
"""
from __future__ import annotations

import logging
import threading
import time
import weakref
from typing import Callable

logger = logging.getLogger("composeio.bus_clock")

#: Floor on the driver thread's period.  A subscriber may declare any cadence it
#: likes; this bounds how hard the ONE thread is allowed to spin, so a
#: mis-declared 1 ms interval degrades to jitter rather than to a busy loop that
#: starves the process it was supposed to serve.
MIN_PERIOD_S = 0.01

#: Fallback period when nothing is attached.  The thread parks at this cadence
#: rather than exiting, so an attach during a quiet moment does not have to
#: restart it.
IDLE_PERIOD_S = 1.0


class _Subscriber:
    """One periodic callback and the interval IT declared.

    Bound methods are held as (weak object, function) rather than as a
    `weakref.WeakMethod` of convenience: the point is that the OWNER's lifetime
    governs, and a bound method is a fresh object on every attribute access, so
    a naive weakref to it would be dead before the first tick.
    """

    __slots__ = ("_ref", "_func", "interval", "_last", "__weakref__")

    def __init__(self, callback: Callable[[float], None], interval: float):
        if interval <= 0:
            raise ValueError(
                f"clock interval must be > 0, got {interval!r}. Zero is not "
                f"'every tick', it is an undeclared cadence.")
        self.interval = float(interval)
        self._last: float | None = None
        owner = getattr(callback, "__self__", None)
        if owner is not None:
            self._ref = weakref.ref(owner)
            self._func = callback.__func__
        else:
            # A plain function or closure: nothing owns it but the caller, so
            # hold it strongly. A closure attached by a caller that then drops
            # every reference is indistinguishable from one it means to keep.
            self._ref = None
            self._func = callback

    def alive(self) -> bool:
        return self._ref is None or self._ref() is not None

    def due(self, now: float) -> bool:
        if self._last is None:
            return True
        return (now - self._last) >= self.interval

    def fire(self, now: float) -> None:
        self._last = now
        if self._ref is None:
            self._func(now)
            return
        owner = self._ref()
        if owner is not None:
            self._func(owner, now)


class BusClock:
    """A single daemon thread driving every declared periodic publisher."""

    def __init__(self, *, autostart: bool = True):
        self._subs: list[_Subscriber] = []
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._autostart = autostart

    # --- registration --------------------------------------------------- #

    def attach(self, callback: Callable[[float], None], *,
               interval: float) -> None:
        """Register *callback* to be called every *interval* seconds.

        The interval is the CALLER's, read from whatever declared it.  This
        object deliberately knows nothing about namespaces: a clock that looked
        namespaces up would couple the driver to the thing being driven, and the
        countdown service — which is not a namespace at all — could not use it.
        """
        with self._lock:
            self._subs.append(_Subscriber(callback, interval))
            if self._autostart:
                self._ensure_thread()

    def detach(self, callback: Callable[[float], None]) -> bool:
        """Remove a previously attached callback.  Idempotent."""
        target_owner = getattr(callback, "__self__", None)
        target_func = getattr(callback, "__func__", callback)
        with self._lock:
            for i, sub in enumerate(self._subs):
                owner = sub._ref() if sub._ref is not None else None
                if sub._func is target_func and owner is target_owner:
                    del self._subs[i]
                    return True
        return False

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            self._reap()
            return len(self._subs)

    @property
    def period(self) -> float:
        """The driver thread's period: the FINEST declared interval."""
        with self._lock:
            live = [s.interval for s in self._subs if s.alive()]
        if not live:
            return IDLE_PERIOD_S
        return max(MIN_PERIOD_S, min(live))

    @property
    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # --- driving --------------------------------------------------------- #

    def tick_once(self, now: float | None = None) -> int:
        """Fire every subscriber whose interval has elapsed.  Returns the count.

        A callback that raises is LOGGED AND SURVIVED.  One misbehaving periodic
        publisher must not stop the clock — that failure would take `_bus/time`,
        every heartbeat and every countdown down together, and the symptom
        (everything silently frozen) points at none of them.
        """
        if now is None:
            now = time.time()
        with self._lock:
            self._reap()
            due = [s for s in self._subs if s.due(now)]
        fired = 0
        for sub in due:
            try:
                sub.fire(now)
                fired += 1
            except Exception as exc:                        # noqa: BLE001
                logger.error("bus clock subscriber raised: %s", exc)
        return fired

    def start(self) -> None:
        with self._lock:
            self._ensure_thread()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._thread = None

    # --- internals -------------------------------------------------------- #

    def _reap(self) -> None:
        self._subs = [s for s in self._subs if s.alive()]

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="composeio-bus-clock", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            # The period is re-read EVERY loop rather than captured once, so an
            # attach or detach retunes the thread without restarting it — which
            # is what makes a second periodic namespace a one-line addition.
            self._stop.wait(self.period)
            if self._stop.is_set():
                return
            self.tick_once()


# --------------------------------------------------------------------------- #
# THE PROCESS-WIDE CLOCK
# --------------------------------------------------------------------------- #
_shared: BusClock | None = None
_shared_lock = threading.Lock()


def shared_clock() -> BusClock:
    """The one clock this process runs.

    Lazily built: importing `composeio.bus` must not start a thread, because
    plenty of importers (the CLI's `--help`, a config validator, a test that
    only inspects a namespace) never run a bus at all.
    """
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = BusClock()
        return _shared


def _reset_shared_clock_for_tests() -> None:
    """Tear the process clock down.  Named for its only legitimate caller."""
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.stop()
        _shared = None
