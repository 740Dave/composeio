"""liveness — the `_bus/heartbeat` publisher + receiver, SHED from the kernel.

Node-liveness is a PLUGIN (publisher + receiver), the kernel consulting it via a
sanctioned oracle hook ([CXC-CS-LIVENESS-FRESHNESS-SPLIT];
[DEC-PHASE0B-LIFECYCLE-HOOK-SEAM-AND-DEFAULT-SHEDS] extraction 3). This engine
owns both halves:

  * THE PUBLISHER — `publish_heartbeat`, formerly `bus.py`'s, driven by the
    kernel's LIFECYCLE-HOOK seam: the start-hook emits a prompt beat and attaches
    the periodic beat to the process clock at the cadence `_bus/heartbeat`
    DECLARES; the stop-hook detaches it with no orphan tick.
  * THE RECEIVER — the `HeartbeatReceiver` that counts beats and answers "is
    that node still there?". The bus references it via a delegating
    `_heartbeat_rx` property, so `note_heartbeat`, `_dispatch_freshness` and the
    single-namespace freshness verdict (which legitimately stays kernel) read the
    plugin's receiver unchanged.

THE ORACLE SEAM IS UNTOUCHED. `bus.set_liveness_oracle` / the oracle consultation
in `bus.node_is_alive` are preserved exactly: the oracle is how a RICHER liveness
plugin overrides this default one, and `node_is_alive` falls back to this
engine's receiver when no oracle is set. A bare `ComposeIOBus()` is byte-identical
before/after — the receiver it consults and the beat it emits are the same, only
their code now lives here.
"""
from __future__ import annotations

import logging
import time

from composebus.bus_clock import shared_clock
from composebus.freshness import HeartbeatReceiver
from composebus.namespace import Cadence
from composebus.plugin import ComposePlugin
from composebus.transport import BusMessage, MessageType

logger = logging.getLogger("composeio.plugins.liveness")


class LivenessEngine:
    """The `_bus/heartbeat` publisher + node-liveness receiver — a plugin peer.

    The publisher binds through the lifecycle-hook seam; the receiver is owned
    here and exposed to the kernel's read-path via `bus._heartbeat_rx`.
    """

    def __init__(self, bus):
        self._bus = bus
        # The receiver's default interval matches the kernel's former literal so
        # a beat that omits `interval_s` times out identically. Imported lazily
        # to keep this module free of a bus import at load time.
        from composebus.bus import BUS_HEARTBEAT_INTERVAL_S
        self.receiver = HeartbeatReceiver(
            bus._mono, default_interval_s=BUS_HEARTBEAT_INTERVAL_S)
        # D2 running-state: the kernel's `_self_is_alive` reads this to answer
        # `node_is_alive(self.bus_id)`. Flipped True by `install`, False by
        # `uninstall`, so a node whose liveness plugin was torn down stops
        # reporting ITSELF alive ([DEC-PHASE1-DSD-CALLS-SETTLED]).
        self._installed = False

    def install(self) -> None:
        """Wire onto the bus's start/stop lifecycle seam."""
        self._bus.register_start_hook(self.on_bus_start)
        self._bus.register_stop_hook(self.on_bus_stop)
        self._installed = True

    def uninstall(self) -> None:
        self._bus.unregister_start_hook(self.on_bus_start)
        self._bus.unregister_stop_hook(self.on_bus_stop)
        shared_clock().detach(self._tick)
        self._installed = False

    @property
    def is_installed(self) -> bool:
        """Whether this engine's lifecycle hooks are attached — the
        'liveness plugin is live' half of D2's self-liveness verdict."""
        return self._installed

    # --- lifecycle hooks ------------------------------------------------- #
    def on_bus_start(self, bus) -> None:
        """Fired at the site the bus starts its BusClock ticks: a prompt beat,
        then the periodic beat attached at the DECLARED cadence."""
        self.publish_heartbeat()
        ns = bus.get_namespace("_bus/heartbeat")
        if ns is None or ns.cadence is not Cadence.PERIODIC:
            return
        shared_clock().attach(self._tick, interval=ns.refresh_interval)

    def on_bus_stop(self, bus) -> None:
        """Fired at the site the bus stops its BusClock ticks — detach so no
        orphan beat survives a stopped bus."""
        shared_clock().detach(self._tick)

    def _tick(self, now: float) -> None:
        self.publish_heartbeat()

    def heartbeat_interval(self) -> float | None:
        ns = self._bus.get_namespace("_bus/heartbeat")
        return None if ns is None else ns.refresh_interval

    # --- the send (former bus.publish_heartbeat) ------------------------- #
    def publish_heartbeat(self) -> None:
        """Publish this node's liveness beat.

        THE PAYLOAD NAMES ITS NODE. A beat that does not say whose it is cannot
        mark a node dead; `source` carries it on the envelope and `val.node` in
        the body too, because some downstream projections drop `src`. The
        DECLARED cadence rides along so a receiver derives the timeout from what
        the publisher SAID rather than a configured copy of it.
        """
        bus = self._bus
        bus.send_message(BusMessage(
            namespace="_bus/heartbeat",
            type=MessageType.HEARTBEAT,
            value={
                "node": bus.bus_id,
                "uptime_s": int(time.time() - bus._started_at),
                "interval_s": self.heartbeat_interval(),
            },
            source=bus.bus_id,
            scope="global",
            sequence=bus._next_seq(),
        ))


class LivenessPlugin(ComposePlugin):
    """Node-liveness wearing the plugin contract ([CXC-CS-PLUGIN-CONTRACT]).
    A default-loaded plugin ([CXC-CS-DEFAULT-PLUGINS]).

    ⛔ THE DEFAULT REMAINS THE FALLBACK, NOT THE ORACLE (D1 /
    [DEC-PHASE1-DSD-CALLS-SETTLED]). `start` DELIBERATELY does NOT call
    `bus.set_liveness_oracle`: the oracle seat stays EMPTY so the kernel falls
    back to this plugin's own heartbeat receiver, and a RICHER liveness plugin
    can cleanly claim the single seat later to supersede this one. Occupying the
    seat here would collapse the fallback/oracle split
    ([CXC-CS-LIVENESS-FRESHNESS-SPLIT]) and force a future plugin to CONTEND for
    the slot. A guard test asserts the seat is empty for the default."""

    class Config(ComposePlugin.Config):
        pass

    name = "liveness"
    version = "1.0.0"
    default_loaded = True
    publishes = ["_bus/heartbeat"]
    subscribes = ["_bus/heartbeat"]

    def __init__(self, config=None):
        super().__init__(config)
        self._engine: "LivenessEngine | None" = None

    @property
    def engine(self) -> "LivenessEngine | None":
        return self._engine

    def start(self, bus) -> None:
        self._bus = bus
        self._engine = install_default(bus)

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.uninstall()
        self._engine = None
        super().stop()


def install_default(bus) -> LivenessEngine:
    """Create + wire the default liveness engine on a bus, idempotently.

    Called by `ComposeIOBus.__init__` (so a bare bus keeps beating AND keeps a
    heartbeat receiver backing `node_is_alive`) and by `LivenessPlugin.start`
    (the standup path). Returns the bus's single engine, creating it on first
    call."""
    engine = getattr(bus, "_liveness_engine", None)
    if engine is None:
        engine = LivenessEngine(bus)
        bus._liveness_engine = engine
        engine.install()
    return engine
