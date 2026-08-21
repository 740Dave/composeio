"""ComposeUX Link-Health indicator — the header live-dot promoted into the
console's CONNECTION-HEALTH signal ([SPEC:CXC-CONSOLE-VISUAL] link health).

Makes a SILENT disconnection VISIBLE.  David reported typing messages that
"looked as though they went through when they didn't."  A sent message echoes
to the thread via channel_bridge (raw ``compose_projects/intelligence/<id>/in`` -> composeio
``/in``), which is DECOUPLED from whether channel.mjs actually delivered it to
the Intelligence's context.  So when the channel MCP is down but the MQTT bus
is up, the message appears sent but never reaches the mind — and nothing
signalled the break.

Two health signals drive this one dot:

  1. CHANNEL liveness — channel.mjs publishes a ~5s HEARTBEAT on the raw topic
     ``compose_projects/intelligence/<id>/channel/alive``; channel_bridge relays it onto the
     composeio scheme, and this widget subscribes it (its ``namespace:``).  A
     heartbeat that stops arriving within ``fresh_threshold_s`` means the
     channel is down — the EXACT failure David hit (channel down, bus up).
  2. BUS link — the console's own ``MQTTTransport.is_connected`` (reached via
     the bus's ``global_transport``).  When the whole MQTT bus is down the
     heartbeat ALSO stops, so freshness alone would catch it; is_connected
     lets us NAME it BUS OFFLINE rather than CHANNEL OFFLINE.

State — published to ``state_namespace`` (default ``console/link/state``), so
the header label + the composer SEND button react declaratively (attr maps):

  engaged         heartbeat fresh AND bus connected   -> ok (green),  BEATING
  channel_offline bus connected but heartbeat stale    -> crit (red),  STILL
  bus_offline     bus transport not connected          -> warn (amber),STILL

Damage ([CXC-R-DAMAGE]): the dot is CLOCKED; a health FLIP or a beat frame
returns True from ``on_clock_tick``, so the engine damages the dot's own rect
and it repaints (the scoped fast path the old ``pulse`` behavior used).  The
state publish additionally marks the scene dirty (schedule_update), which
repaints the bound header label + SEND button.  A visual state change that
skipped both would NOT show — this widget takes both paths deliberately.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from . import console_theme as ct
from .circle import CircleWidget

# ~2 missed 5s heartbeats before we call the channel stale.  One dropped beat
# (jitter, a GC pause) must NOT flip the dot red.
FRESH_THRESHOLD_S = 12.0

ENGAGED = "engaged"
CHANNEL_OFFLINE = "channel_offline"
BUS_OFFLINE = "bus_offline"


class LinkHealthWidget(CircleWidget):
    """The header live-dot, driven by connection health.  A CircleWidget (same
    measure/draw, so a healthy static render is pixel-identical to the old
    green dot) that computes health each frame and self-beats when engaged."""

    def __init__(self, widget_id: str, *,
                 radius: float = 4,
                 namespace: str = "intelligence/architect/channel/alive",
                 state_namespace: str = "console/link/state",
                 fresh_threshold_s: float = FRESH_THRESHOLD_S,
                 fill_color: Optional[tuple] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, radius=radius,
                         fill_color=fill_color or ct.DARK.ok, **kwargs)
        self.namespace = namespace
        self.state_namespace = state_namespace
        self.fresh_threshold_s = float(fresh_threshold_s)
        # [CXC-PRIM-STALE] this widget RENDERS ITS OWN staleness — a generic
        # hatch + badge over a 4-px dot would be absurd, and the dot's colour
        # IS the liveness report.  Declining explicitly (rather than being
        # forgotten) is exactly what "opt out" is for.
        self.staleness_treatment = "self"
        # ARRIVAL time of the last heartbeat (monotonic) — arrival-based, so it
        # is immune to clock skew between the channel host and the console.
        self._last_alive_mono: Optional[float] = None
        self._mount_mono: float = 0.0
        self._published_state: Optional[str] = None
        self._state: str = ENGAGED

    # -- lifecycle ----------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        self._mount_mono = time.monotonic()
        if ctx is not None and hasattr(ctx, "register_clocked"):
            ctx.register_clocked(self)

    def on_unmount(self) -> None:
        if self._ctx is not None and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)

    # -- bus: heartbeat arrival stamps freshness ----------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.namespace:
            self._last_alive_mono = time.monotonic()

    # -- health computation (pure, unit-testable) ---------------------------

    def _shared_verdict(self):
        """The SHARED receiver's verdict on the heartbeat namespace, or None.

        [CXC-PRIM-STALE] absorbing a private freshness notion.  This widget
        predates [SPEC:CXC-FRESHNESS] and hand-rolled its own receiver: a
        monotonic arrival stamp plus a 12-second threshold, which is the
        freshness receiver rebuilt inside one widget.  When the namespace
        DECLARES a cadence, the engine's receiver already computes the same
        judgement on the same monotonic clock — so we read it instead of
        keeping a second copy, and the second copy is the thing that eventually
        disagrees.

        None when nothing was declared, in which case the local threshold below
        still applies: an absorbed notion must not become a REGRESSION on the
        deployments that never declared a cadence, and [CXC-FRESH-VERDICT] is
        explicit that UNDECLARED authorises no verdict at all.
        """
        bus = getattr(self._ctx, "bus", None)
        if bus is None or not hasattr(bus, "get_namespace"):
            return None
        ns = bus.get_namespace(self.namespace)
        if ns is None:
            return None
        from composeio.freshness import Verdict
        v = ns.verdict
        return None if v is Verdict.UNDECLARED else v

    def compute_state(self, now_mono: float, connected: Optional[bool]) -> str:
        """(now_mono, connected) -> health state string.

        ``connected`` is None when no external bus transport is configured
        (headless / a local-only demo window) — link monitoring is then not
        applicable, so we stay ENGAGED and preserve today's demo look rather
        than false-alarm.  With a transport: no connection -> BUS_OFFLINE; a
        connection but a stale (or never-seen) heartbeat -> CHANNEL_OFFLINE.

        The staleness half now prefers the SHARED receiver's verdict when the
        heartbeat namespace declared a cadence (see `_shared_verdict`), and
        falls back to this widget's own threshold when it did not.
        """
        if connected is None:
            return ENGAGED
        if not connected:
            return BUS_OFFLINE
        from composeio.freshness import verdict_is_believable
        shared = self._shared_verdict()
        if shared is not None:
            self.freshness.verdict = shared
            return ENGAGED if verdict_is_believable(shared) else CHANNEL_OFFLINE
        ref = (self._last_alive_mono if self._last_alive_mono is not None
               else self._mount_mono)
        fresh = (now_mono - ref) <= self.fresh_threshold_s
        return ENGAGED if fresh else CHANNEL_OFFLINE

    def _connected(self) -> Optional[bool]:
        bus = getattr(self._ctx, "bus", None)
        transport = getattr(bus, "global_transport", None) if bus else None
        if transport is None:
            return None
        return bool(getattr(transport, "is_connected", False))

    # -- clock --------------------------------------------------------------

    def on_clock_tick(self, now: float) -> bool:
        """Evaluate health, publish it on change, and drive the dot's own
        colour + beat.  Returns True when the dot needs a repaint this frame
        (an engaged beat frame, or the frame health flipped)."""
        state = self.compute_state(time.monotonic(), self._connected())

        if state != self._published_state:
            self._published_state = state
            if self._ctx is not None:
                self._ctx.publish(self.state_namespace, state,
                                  source=self.widget_id)

        changed = state != self._state
        self._state = state
        t = ct.CONSOLE.active

        if state == ENGAGED:
            self.fill_color = t.ok
            if ct.reduced_motion(self._ctx):
                self.opacity = 1.0
                return changed                 # rest frame; repaint only on flip
            self.opacity = ct.pulse(now, ct.BEAT_PERIOD, ct.BEAT_LOW)
            return True                        # beating -> repaint every frame

        # Offline: NOT beating, held at full opacity, warm/crit colour.
        self.fill_color = t.crit if state == CHANNEL_OFFLINE else t.warn
        self.opacity = 1.0
        return changed
