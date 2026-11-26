"""time — the `_bus/time` 1 Hz publisher, SHED from the kernel.

The kernel used to own the periodic `_bus/time` tick itself: `_publish_bus_time`
(the send) and `_tick_bus_time` (the clock handler) lived in `bus.py`, attached
to the process `BusClock` inside `Bus.start()`
([DEC-PHASE0B-LIFECYCLE-HOOK-SEAM-AND-DEFAULT-SHEDS] extraction 2). Publishing a
wall-clock announcement on a cadence is a plugin concern, not the kernel's — so
it moves here and binds through the kernel's generic LIFECYCLE-HOOK seam
(`register_start_hook`/`register_stop_hook`), fired at the exact site the bus
starts/stops its `BusClock` ticks.

BEHAVIOR IS PRESERVED. A bare `ComposeIOBus()` installs this default engine at
construction (via `install_default`), so `_bus/time` still emits an immediate
prompt tick on `start()` and then ticks periodically at the cadence the
`_bus/time` NAMESPACE DECLARES — byte-identical to the baked-in version.
`TimePlugin` is the same engine wearing the plugin contract for the
standup/registry path.

RESOLUTION: whole UNIX seconds (a uint32), unchanged — the named consumers are
8-bit (a CH32V003, a TM1637 panel) and spec/01 specifies uint32 seconds; a 1 Hz
signal over a retained MQTT hop cannot carry sub-second truth anyway
([DEC-BUS-TIME-IS-A-REAL-1HZ-SIGNAL]).
"""
from __future__ import annotations

import logging
import time

from composebus.bus_clock import shared_clock
from composebus.namespace import Cadence
from composebus.plugin import ComposePlugin
from composebus.transport import BusMessage, MessageType

logger = logging.getLogger("composeio.plugins.time")


class TimeEngine:
    """The `_bus/time` periodic publisher — a plugin peer, not the kernel.

    Binds to the bus through the LIFECYCLE-HOOK seam: the start-hook attaches a
    periodic publisher to the process clock and emits the prompt tick; the
    stop-hook detaches it so NO orphan clock callback survives a `bus.stop()`.
    """

    def __init__(self, bus):
        self._bus = bus
        self._interval: float | None = None

    @property
    def clock_interval(self) -> float | None:
        """The cadence this engine's `_bus/time` tick is actually running at,
        or None when not attached. Backs `bus.clock_interval`."""
        return self._interval

    def install(self) -> None:
        """Wire onto the bus's start/stop lifecycle seam."""
        self._bus.register_start_hook(self.on_bus_start)
        self._bus.register_stop_hook(self.on_bus_stop)

    def uninstall(self) -> None:
        self._bus.unregister_start_hook(self.on_bus_start)
        self._bus.unregister_stop_hook(self.on_bus_stop)
        shared_clock().detach(self._tick)
        self._interval = None

    # --- lifecycle hooks ------------------------------------------------- #
    def on_bus_start(self, bus) -> None:
        """Fired at the site the bus starts its BusClock ticks.

        THE IMMEDIATE TICK SURVIVES THE UPGRADE TO A PERIODIC SIGNAL: without it
        a subscriber present at start would wait a whole interval for the first
        value. Periodic AND prompt, exactly as the baked-in `start()` did.

        ⛔ THE CADENCE IS READ FROM THE NAMESPACE, never a literal — `_bus/time`
        is [D4]'s first periodic-by-construction namespace. An operator who
        re-declares it as event-driven has said "no clock here", and that is
        honoured rather than overridden.
        """
        self.publish_bus_time()
        ns = bus.get_namespace("_bus/time")
        if ns is None or ns.cadence is not Cadence.PERIODIC:
            return
        shared_clock().attach(self._tick, interval=ns.refresh_interval)
        self._interval = ns.refresh_interval

    def on_bus_stop(self, bus) -> None:
        """Fired at the site the bus stops its BusClock ticks — detach with no
        orphan tick left publishing into a stopped bus."""
        shared_clock().detach(self._tick)
        self._interval = None

    def _tick(self, now: float) -> None:
        self.publish_bus_time(now=now)

    # --- the send (former bus._publish_bus_time) ------------------------- #
    def publish_bus_time(self, now: float | None = None) -> None:
        """Publish current time to `_bus/time` — WHOLE UNIX SECONDS (uint32)."""
        bus = self._bus
        bus._bus_time = time.time() if now is None else now
        bus.send_message(BusMessage(
            namespace="_bus/time",
            type=MessageType.TIME_SYNC,
            value=int(bus._bus_time),
            source=bus.bus_id,
            scope="global",
            sequence=bus._next_seq(),
        ))


class TimePlugin(ComposePlugin):
    """The `_bus/time` publisher wearing the plugin contract ([CXC-CS-PLUGIN-
    CONTRACT]). A default-loaded plugin ([CXC-CS-DEFAULT-PLUGINS])."""

    class Config(ComposePlugin.Config):
        pass

    name = "time"
    version = "1.0.0"
    default_loaded = True
    publishes = ["_bus/time"]

    def __init__(self, config=None):
        super().__init__(config)
        self._engine: "TimeEngine | None" = None

    @property
    def engine(self) -> "TimeEngine | None":
        return self._engine

    def start(self, bus) -> None:
        self._bus = bus
        self._engine = install_default(bus)

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.uninstall()
        self._engine = None
        super().stop()


def install_default(bus) -> TimeEngine:
    """Create + wire the default `_bus/time` engine on a bus, idempotently.

    Called by `ComposeIOBus.__init__` (so a bare bus keeps ticking) and by
    `TimePlugin.start` (the standup path). Returns the bus's single engine,
    creating it on first call."""
    engine = getattr(bus, "_time_engine", None)
    if engine is None:
        engine = TimeEngine(bus)
        bus._time_engine = engine
        engine.install()
    return engine
