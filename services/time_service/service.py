"""time_service — the platform base clock, minus its transport.

The service is an ordinary ComposeBus client that originates ONE semantic fact,
``time/base`` ([CXC-TIME-SERVICE-INTENT]), and drives the idle time BODIES that
realize it ([CXC-TIME-BODY]). It never calls a method on another module and holds
no notion of planets, locales, offsets, or rotation beyond the idle body table it
was handed ([CXC-TIME-BASE] boundary).

THE BUS IS INJECTED (``attach``), never constructed here — DP-CIO-6: transport is
an implementation detail, not an architectural decision. The connect is
config-driven and lives in :mod:`.__main__` (the deploy path), which builds a
transport from a spec via ``composeio.transport_registry.build``. This module
names no substrate and imports no concrete Transport class, exactly like
``fingerprint_storage/service.py``.

WHAT THIS BUILD COVERS ([SPEC:CXC-TIME-SERVICE] platform core):
  * ``time/base`` at a CONFIGURABLE tick, DEFAULT 1 Hz ([CXC-TIME-BASE]),
    carrying RAW MONOTONIC time — UTC epoch when idle, game-elapsed-seconds when
    a game profile sets ``base_mode: game``.
  * a RETAINED last-value snapshot ([DD-TIME-SCHEDULE-RETAINED] ruled = YES):
    published as a STATE message, which ComposeBus retains
    (``transport_mqtt.RETAIN_BY_TYPE[STATE] is True``), so a leaf joining
    mid-stream reads the current time at once instead of waiting up to a tick.
  * epoch (int) on the wire ([DD-TIME-WIRE-FORMAT] ruled = epoch(int)).
  * the idle time BODIES ([CXC-TIME-IDLE-LOCALES]) driven from ``time/base``.
    Under the UNIFIED ruling ([DD-TIME-BODY-UNIFICATION]) a body is a time-OBJECT
    with its own source; the idle locale is the DEGENERATE ``offset`` source, and
    bodies are OBJECT-NAMED (``time/<object>``). Idle offset-bodies realize ONLY
    in ``base_mode == "idle"`` (QA note 2); their local-epoch wire value is an
    INTERIM idle-only format (see :mod:`.bodies`).

OUT OF SCOPE (game-world layer, plugged in at the SAME body-source seam later):
the game-world object sources (an independent ship base, planetary rotation),
proximity gating, the "Local Time" view alias ([CXC-TIME-PLANET-REALIZATION],
[CXC-TIME-VIEW-SELECTOR]). Named as a seam, NOT built here.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import topics
from .bodies import TimeBody
from .profile import TimeProfile


class TimeService:
    """The platform base clock. Give it a bus with ``publish_state`` +
    ``subscribe`` (an in-process ``ComposeIOBus`` or a bridged one) and it
    publishes ``time/base`` and its idle bodies."""

    def __init__(
        self,
        profile: TimeProfile | None = None,
        *,
        clock: Callable[[], float] = time.time,
        source: str = "time_service",
    ) -> None:
        self.profile = profile or TimeProfile.idle_default()
        self.clock = clock
        self.source = source
        self.bodies: list[TimeBody] = [
            TimeBody(name, tz) for name, tz in self.profile.bodies.items()
        ]
        self._bus: Any = None
        # Game mode measures elapsed seconds from this reference, set at start().
        self._game_start: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- transport ------------------------------------------------------

    def attach(self, bus: Any) -> None:
        """Inject the bus, declare the namespaces (json engine — an epoch int is
        a free-form value, never a `binary`/range engine, per the engine-mismatch
        hazard), and wire each object body that REALIZES in the current base mode
        to ``time/base``.

        ⛔ A body is wired ONLY when ``body.realizes_in(base_mode)`` (QA note 2).
        The idle ``offset`` source realizes only in ``idle``; in ``game`` mode the
        idle offset table is REPLACED by the game-world layer's object sources, so
        NO idle body is subscribed and none publishes a (nonsensical) offset onto
        a game-elapsed count."""
        self._bus = bus
        # Declare with the json engine so an epoch int (and a body's shifted
        # epoch) validates cleanly; a strict/range engine would reject it.
        bus.ensure_namespace(topics.BASE, "json")
        for b in self.bodies:
            if not b.realizes_in(self.profile.base_mode):
                continue
            bus.ensure_namespace(topics.body(b.name), "json")
            bus.subscribe(topics.BASE, self._body_publisher(b))

    def _body_publisher(self, body: TimeBody) -> Callable[[str, Any, Any], None]:
        """A subscriber that republishes ``time/base`` as this object's realized
        value. A body realizes off the SAME fact it hears — it never reads a clock
        of its own ([CXC-TIME-BODY]). Object-named: it publishes ``time/<object>``
        ([DD-TIME-BODY-UNIFICATION] = UNIFIED)."""

        def _on_base(_ns: str, new_value: Any, _old: Any) -> None:
            if new_value is None:
                return
            # Defensive re-check: only realize where the source is valid for the
            # current base mode — the subscription is already gated in attach(),
            # this makes the invariant local to the publish too.
            if not body.realizes_in(self.profile.base_mode):
                return
            self._bus.publish_state(
                topics.body(body.name), body.realize(int(new_value)), source=self.source
            )

        return _on_base

    # ---- the base value -------------------------------------------------

    def base_value(self) -> int:
        """The raw-monotonic ``time/base`` value ([CXC-TIME-BASE]).

        idle -> UTC epoch seconds; game -> game-elapsed-seconds-from-start.
        SETTLED as a bare int either way ([DD-TIME-WIRE-FORMAT] = epoch int).
        """
        if self.profile.base_mode == "game":
            start = self._game_start if self._game_start is not None else self.clock()
            return int(self.clock() - start)
        return int(self.clock())

    # ---- ticking --------------------------------------------------------

    @property
    def tick_interval(self) -> float:
        """Seconds between publishes — 1/tick_hz ([CXC-TIME-BASE] `tick-schedule`)."""
        return 1.0 / self.profile.tick_hz

    def tick(self) -> int:
        """Publish ONE ``time/base`` value (retained STATE) and return it. The
        idle bodies re-publish synchronously via their subscriptions."""
        if self._bus is None:
            raise RuntimeError("attach(bus) before tick()")
        value = self.base_value()
        self._bus.publish_state(topics.BASE, value, source=self.source)
        return value

    def start(self) -> None:
        """Latch the game start (if any), publish an IMMEDIATE snapshot so the
        retained value is present at once, then run the periodic tick in a
        daemon thread."""
        if self._bus is None:
            raise RuntimeError("attach(bus) before start()")
        if self.profile.base_mode == "game" and self._game_start is None:
            self._game_start = self.clock()
        # Immediate snapshot: a subscriber present at start must not wait a whole
        # interval for the first value ([DD-TIME-SCHEDULE-RETAINED]).
        self.tick()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="time_service-tick", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        # `Event.wait(interval)` returns True only when stop() is signalled, so
        # the loop ticks every interval and exits promptly on stop — no busy-wait,
        # no drift-accumulating sleep-then-check race.
        while not self._stop.wait(self.tick_interval):
            self.tick()

    def stop(self) -> None:
        """Signal the tick loop to stop and join it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
