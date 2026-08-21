"""
[FLOW:MAKER-PROJECT] S5 — the dual-throttle -> Galactica-heading contract.

This is the **composeio side** of the Galactica dual-throttle controller
(maker-project-flow §7).  The physical controller (2x pot-10k -> ESP32-C3, see
``render/examples/dual_throttle.compose.yaml``) is HEADLESS: the MCU reads the
two ADCs and **publishes** two engine-thrust slots and nothing else::

    throttle/left   (continuous_0_100)   left engine thrust  0..100 %
    throttle/right  (continuous_0_100)   right engine thrust 0..100 %

control-is-a-peer (Problem 4b): the throttle controller is *just another
publisher*.  Nothing on the game side reaches back to it — the world app
SUBSCRIBES the two slots the same way it subscribes any other bus state.

The integration model (David's 2026-07-26 correction)
-----------------------------------------------------
It is NOT "a dradis director reads throttle -> heading".  It is state-on-bus::

    throttle/left + throttle/right          (this controller publishes)
        -> the WORLD app consumes them as L/R engine thrust
        -> differential thrust (L-R) integrates the Galactica's HEADING
        -> the world publishes game/own_heading   (it already does)
        -> the Qualia dradis console REFLECTS it   (rotation binding; NO change)

The Qualia dradis (``bsg/game_hardware/dradis_console/dradis.compose.yaml``,
device ``qualia-6c9d0c``) already binds its ``own_heading`` input to
``game/own_heading`` — so the display end needs ZERO edits.  The ONE integration
point is the world app's **thrust -> position/heading model**.

This module owns the mapping (``differential_thrust`` / ``heading_rate``) and a
**reference world model** (:class:`ThrustHeadingModel`) that is the executable
statement of the world-app hook: it consumes the throttle slots exactly as the
world should and integrates heading (and a 2D position) per physics tick.  The
S5 proof drives this model; the world-app patch (documented below) mirrors it.

WHERE THE WORLD APP LIVES (cross-repo boundary — FLAGGED)
--------------------------------------------------------
The world app + the Qualia dradis are in a SEPARATE repo, ``~/maker/bsg`` (not
this worktree).  Per the S5 brief we do the composeio side definitively here and
DOCUMENT the world-app hook precisely rather than half-editing across repos.  The
exact bsg patch is ``WORLD_APP_HOOK`` below — it is a literal drop-in for
``bsg/world/sim.py`` + ``bsg/world/publish.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

# ---------------------------------------------------------------------------
# The namespaces — the S5 contract (single source of truth for both sides).
# ---------------------------------------------------------------------------

#: The two engine-thrust slots the headless controller publishes (0..255).
NS_THROTTLE_LEFT = "throttle/left"
NS_THROTTLE_RIGHT = "throttle/right"

#: What the world app publishes and the Qualia dradis reflects (degrees, 0..360).
#: The world already owns this namespace (bsg/world/publish.py NS_OWN_HEADING).
NS_OWN_HEADING = "game/own_heading"

#: Full-scale of the continuous_0_100 thrust engine — a PERCENTAGE.
#:
#: ⛔ CHANGED 255.0 -> 100.0 on 2026-08-15 (David: "the C3 should only be
#: publishing throttle percentages, not 0_255").  Everything downstream of this
#: constant normalizes to [0,1] before it does any physics, so the helm's FEEL
#: is unchanged by the migration — that is exactly why a percentage can replace
#: an 8-bit code here without retuning max_rate_dps or deadband.
#:
#: ⛔ THIS CONSTANT IS THE ENTIRE COUPLING TO THE WIRE FORMAT, and it is
#: DUPLICATED in compose_projects/bsg/world/helm.py, a deliberate vendored copy
#: that keeps the bsg repo free of a composeio dependency.  A vendored copy is
#: only safe while somebody updates BOTH; if this value and helm.py's ever
#: disagree the ship simply steers at the wrong rate, with no error anywhere.
#: If you change it here, change it there.
THRUST_FULL_SCALE = 100.0

# ---------------------------------------------------------------------------
# Tuning — FLAGGED for the human's glass (the "feel" of the stick).
# ---------------------------------------------------------------------------

#: Heading rate at FULL differential thrust (|L-R| = full scale), degrees/second.
#: 45 deg/s -> ~8 s for a half-turn at hard-over.  A FEEL knob: too high and the
#: ship is twitchy, too low and it feels dead.  Needs David's hands on the pots.
DEFAULT_MAX_RATE_DPS = 45.0

#: Centre deadband as a fraction of full differential.  Real pots jitter a few
#: LSB around centre; below this the ship holds heading (no creep).  FEEL knob.
DEFAULT_DEADBAND = 0.05

#: Forward speed at FULL mean thrust ((L+R)/2 = full scale), world-units/second.
#: Only used by the 2D position half of the reference model (the heading half is
#: what the Qualia dradis reflects).  FEEL knob — world-unit dependent.
DEFAULT_MAX_SPEED = 1.0

#: Heading-sign convention: with SIGN_CONVENTION = +1, MORE-LEFT-than-right
#: thrust (L>R) turns the heading in the +degrees direction (clockwise on the
#: dradis, i.e. to starboard — the outer engine pushes the bow across).  Flip to
#: -1 if the stick feels backwards on the real console.  FEEL knob — flag.
SIGN_CONVENTION = 1.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def normalized_thrust(value: Any, *, full_scale: float = THRUST_FULL_SCALE) -> float:
    """A thrust reading (0..full_scale, i.e. a percentage) -> normalized [0, 1].  Non-numeric or
    out-of-range inputs clamp rather than raise (a display/model tolerates a stale
    or bad frame; the bus engine is what HARD-validates at publish time)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return _clamp(f / full_scale, 0.0, 1.0)


def differential_thrust(left: Any, right: Any, *,
                        full_scale: float = THRUST_FULL_SCALE) -> float:
    """Differential thrust (L - R) as a normalized signed fraction in [-1, 1].

    +1 = hard-over left (left engine full, right engine idle); -1 = hard-over
    right.  This is the steering signal: a two-engine craft yaws on the DIFFERENCE
    of its engine thrusts (the mean is forward drive)."""
    return normalized_thrust(left, full_scale=full_scale) - \
        normalized_thrust(right, full_scale=full_scale)


def mean_thrust(left: Any, right: Any, *,
                full_scale: float = THRUST_FULL_SCALE) -> float:
    """Mean thrust ((L + R) / 2) as a normalized fraction in [0, 1] — the forward
    drive of the differential-drive model (steer on the difference, drive on the
    mean)."""
    return 0.5 * (normalized_thrust(left, full_scale=full_scale) +
                  normalized_thrust(right, full_scale=full_scale))


def heading_rate(left: Any, right: Any, *,
                 max_rate_dps: float = DEFAULT_MAX_RATE_DPS,
                 deadband: float = DEFAULT_DEADBAND,
                 full_scale: float = THRUST_FULL_SCALE,
                 sign: float = SIGN_CONVENTION) -> float:
    """Map differential engine thrust -> heading rate (degrees/second).

    Balanced thrust (L == R, including both-full or both-idle) -> 0 (drive
    straight).  A centre deadband suppresses pot jitter; beyond it the response
    is linear and continuous (rescaled so it starts at 0 at the deadband edge and
    reaches ``max_rate_dps`` at hard-over).  ``sign`` flips the turn direction."""
    d = differential_thrust(left, right, full_scale=full_scale)
    if abs(d) <= deadband:
        return 0.0
    span = max(1e-9, 1.0 - deadband)
    mag = (abs(d) - deadband) / span            # 0 at edge -> 1 at hard-over
    return sign * math.copysign(mag, d) * max_rate_dps


# ---------------------------------------------------------------------------
# The reference world model — the EXECUTABLE statement of the world-app hook.
# ---------------------------------------------------------------------------

@dataclass
class ThrustHeadingModel:
    """Reference thrust -> heading/position model: exactly what the bsg WORLD app
    must add to make the Galactica's heading throttle-controllable.

    The world subscribes ``throttle/left`` + ``throttle/right`` as L/R engine
    thrust (``set_thrust`` / ``on_throttle``); each physics tick (``advance``)
    integrates the heading from the differential and the position from the mean.
    ``heading`` is what the world publishes on ``game/own_heading`` and the Qualia
    dradis reflects — so this model IS the loop, minus the transport.

    This is a differential-drive kinematic model (two-tread robot / twin-screw
    ship): steer on (L-R), drive on (L+R)/2.  The position half is included so a
    world that tracks galaxy coordinates gets a full thrust->position model; a
    world where own-ship sits at scope centre (today's bsg) uses only ``heading``.
    """

    heading: float = 0.0                     # degrees, 0..360; published state
    x: float = 0.0                           # world-units (optional position)
    y: float = 0.0
    max_rate_dps: float = DEFAULT_MAX_RATE_DPS
    deadband: float = DEFAULT_DEADBAND
    max_speed: float = DEFAULT_MAX_SPEED
    sign: float = SIGN_CONVENTION
    full_scale: float = THRUST_FULL_SCALE
    _left: float = field(default=0.0, repr=False)
    _right: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.heading %= 360.0

    # -- thrust ingress (the world subscribes the two slots) --
    def set_thrust(self, left: Optional[Any] = None,
                   right: Optional[Any] = None) -> None:
        if left is not None:
            self._left = float(left)
        if right is not None:
            self._right = float(right)

    def on_throttle(self, namespace: str, value: Any) -> bool:
        """Bus-callback shape: feed a ``throttle/left|right`` state message.
        Returns True if the namespace was a throttle slot (consumed)."""
        if namespace == NS_THROTTLE_LEFT:
            self._left = float(value)
            return True
        if namespace == NS_THROTTLE_RIGHT:
            self._right = float(value)
            return True
        return False

    # -- readouts --
    @property
    def heading_rate_dps(self) -> float:
        return heading_rate(self._left, self._right,
                            max_rate_dps=self.max_rate_dps,
                            deadband=self.deadband,
                            full_scale=self.full_scale, sign=self.sign)

    @property
    def speed(self) -> float:
        return mean_thrust(self._left, self._right,
                           full_scale=self.full_scale) * self.max_speed

    # -- one physics tick (the world's _advance) --
    def advance(self, dt: float) -> float:
        """Integrate one tick.  Updates ``heading`` from the differential and
        ``x``/``y`` from the mean-thrust forward drive.  Returns the new heading
        (what the world publishes on ``game/own_heading``)."""
        self.heading = (self.heading + self.heading_rate_dps * dt) % 360.0
        v = self.speed
        rad = math.radians(self.heading)
        self.x += v * math.cos(rad) * dt
        self.y += v * math.sin(rad) * dt
        return self.heading

    @property
    def position(self) -> Tuple[float, float]:
        return (self.x, self.y)


# ---------------------------------------------------------------------------
# The publisher-side adapter — control-is-a-peer, as a live bus module.
# ---------------------------------------------------------------------------

class ThrottleThrustBridge:
    """OPTIONAL live-bus wiring of the reference model for a ComposeApp-style bus.

    Subscribes the two throttle slots and republishes ``game/own_heading`` on a
    clock, so the whole loop runs in-process for a demo/soak without the bsg world
    app.  In production the WORLD app owns this integration (see ``WORLD_APP_HOOK``)
    — this bridge is a composeio-side stand-in / soak harness, not the shipping
    path.  ``publish`` is any ``(namespace, value) -> None`` (e.g. a bus.publish).
    """

    def __init__(self, publish: Callable[[str, Any], None], *,
                 model: Optional[ThrustHeadingModel] = None) -> None:
        self._publish = publish
        self.model = model or ThrustHeadingModel()

    def on_message(self, namespace: str, value: Any) -> None:
        self.model.on_throttle(namespace, value)

    def tick(self, dt: float) -> float:
        heading = self.model.advance(dt)
        self._publish(NS_OWN_HEADING, round(heading, 1))
        return heading


# ---------------------------------------------------------------------------
# WORLD_APP_HOOK — the exact bsg-side patch (documented, NOT applied here).
# ---------------------------------------------------------------------------
#: Literal drop-in for the world app (`~/maker/bsg`).  Cross-repo: NOT applied
#: from this worktree.  Mirrors ThrustHeadingModel field-for-field.
WORLD_APP_HOOK = r'''
# === bsg/world/sim.py ======================================================
# 1. Import the mapping (or vendor these ~15 lines — no composeio dep needed):
from composeio.maker.throttle_thrust import (
    NS_THROTTLE_LEFT, NS_THROTTLE_RIGHT, heading_rate,
)

# 2. Own-ship heading becomes an INTEGRATED helm state (seed at planet_bearing so
#    a fresh system still points at the planet until the helm is touched):
#      In World.__init__ / dataclass:  own_heading: float = 0.0
#                                      thrust_left: float = 0.0
#                                      thrust_right: float = 0.0
#    On arrival (_arrive):  self.world.own_heading = self.world.system.planet_bearing

# 3. Integrate in _advance(dt), before _publish():
#      w = self.world
#      rate = heading_rate(w.thrust_left, w.thrust_right)   # deg/s
#      w.own_heading = (w.own_heading + rate * dt) % 360.0

# 4. _own_heading() returns the controllable state instead of planet_bearing:
#      def _own_heading(self) -> float:
#          return self.world.own_heading % 360.0
#    (_publish already sends NS_OWN_HEADING on change -> the Qualia dradis
#     reflects it via its own_heading rotation binding. NO dradis change.)

# === bsg/world/publish.py ==================================================
# 5. Ingress the two thrust slots exactly like game/cmd/* (control-is-a-peer):
#      In Publisher's MQTT on_message / subscribe list, subscribe:
#          throttle/left, throttle/right
#      and route each into the world:
#          if ns == NS_THROTTLE_LEFT:  world.thrust_left  = float(value)
#          if ns == NS_THROTTLE_RIGHT: world.thrust_right = float(value)
#      (These are STATE slots, not game/cmd/* commands — they set standing thrust,
#       they are not one-shot orders. Retained-value semantics: a late-joining
#       world picks up the current stick position immediately.)
'''


__all__ = [
    "NS_THROTTLE_LEFT", "NS_THROTTLE_RIGHT", "NS_OWN_HEADING",
    "THRUST_FULL_SCALE",
    "DEFAULT_MAX_RATE_DPS", "DEFAULT_DEADBAND", "DEFAULT_MAX_SPEED",
    "SIGN_CONVENTION",
    "normalized_thrust", "differential_thrust", "mean_thrust", "heading_rate",
    "ThrustHeadingModel", "ThrottleThrustBridge", "WORLD_APP_HOOK",
]
