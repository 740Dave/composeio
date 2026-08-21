"""
ComposeUX Swipe-Carousel interaction seam ([SPEC:CXC-PRIM] [CXC-PRIM-CAROUSEL]
[CXC-PRIM-CAROUSEL-TOUCH]).

The carousel's *visual* is a pure function of a single continuous float
``position`` (the cover-flow geometry, [CXC-PRIM-CAROUSEL-MODEL]) — that math
lives in ``widgets/carousel.py``.  What is genuinely NEW is the INPUT: a
drag/tap/snap/elastic state machine that maps a horizontal finger pan onto
``position`` and, on release, animates ``position`` to the settled integer
tile.  That is this module — the carousel analogue of ``DragValueController``
([CXC-PRIM-DRAGVAL], engine/drag_value.py).

Design (state on the bus, behavior in the widget):

  * The controller OWNS ``position`` and only ever WRITES it (plus the settled
    index).  So an Intelligence that publishes a target index to ``goto_ns``
    moves the carousel byte-identically to a finger that flicks to the same
    tile ([CXC-PRIM-CAROUSEL-BIND], control-is-a-peer).

  * NO momentum / inertia (load-bearing, [CXC-PRIM-CAROUSEL-TOUCH]): a flick
    advances AT MOST ONE tile — velocity picks the DIRECTION, never the
    DISTANCE.  There is no multi-tile fling.

  * Mutually-exclusive state machine: exactly one of ``idle`` / ``dragging`` /
    ``snapping``.  A new ``begin`` (pointer-down) CANCELS an in-flight snap and
    the drag takes over from the LIVE position (CAR-V7).

  * Deterministic snap ([CXC-LOT-DETERMINISM] parity): the snap animation's
    start time is stamped on the FIRST clock tick, so ``position`` is a pure
    function of the logical clock — sampling frames under
    ``render_frame_at(t)`` reproduces the ease byte-identically on every
    backend, exactly as behaviors/Lottie do.

Time is injected: ``begin``/``update``/``finish`` take an explicit ``t``
(seconds) and ``tick`` takes the clock ``now`` — so the seam is trivially
unit-testable with a synthetic clock, and the widget simply feeds it
``time.monotonic()`` / the engine clock.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple


def ease_out_cubic(t: float) -> float:
    """1 − (1−t)³ ([CXC-PRIM-CAROUSEL-PARAMS] ``snap_easing`` default)."""
    it = 1.0 - t
    return 1.0 - it * it * it


def ease_linear(t: float) -> float:
    return t


_EASINGS = {
    "ease_out_cubic": ease_out_cubic,
    "ease_out": ease_out_cubic,
    "linear": ease_linear,
}


def _sign(v: float) -> float:
    return -1.0 if v < 0 else (1.0 if v > 0 else 0.0)


# Only samples within this many seconds of release feed the flick-velocity
# estimate — a slow drag that pauses then releases has ~0 release velocity.
_VELOCITY_WINDOW_S = 0.09


class SwipeCarouselController:
    """Maps a horizontal pan onto a continuous ``position`` with tap/drag
    discrimination, single-tile snap, and edge rubber-band.

    Attach to a ``CarouselWidget``; the widget forwards its pointer hooks
    (``begin``/``update``/``finish``) and per-frame clock (``tick``) here, and
    supplies two callbacks:

      * ``on_position(position)`` — fired whenever ``position`` changes (the
        widget redraws + publishes the live position for observability).
      * ``on_settle(index)``     — fired when a snap completes on an integer
        tile (the widget publishes the settled index to ``index_ns``).
    """

    def __init__(self, *, n: int, spacing: float,
                 snap_threshold: float = 0.15,
                 flick_velocity: float = 400.0,
                 snap_duration_ms: float = 300.0,
                 snap_easing: str = "ease_out_cubic",
                 elastic_factor: float = 0.30,
                 tap_max_drift_px: float = 15.0,
                 tap_max_duration_ms: float = 300.0,
                 wrap: bool = False,
                 initial_index: int = 0,
                 on_position: Optional[Callable[[float], None]] = None,
                 on_settle: Optional[Callable[[int], None]] = None) -> None:
        self.n = int(n)
        self.spacing = float(spacing) or 1.0
        self.snap_threshold = float(snap_threshold)
        self.flick_velocity = float(flick_velocity)
        self.snap_duration_ms = float(snap_duration_ms)
        self.easing = _EASINGS.get(str(snap_easing), ease_out_cubic)
        self.easing_name = str(snap_easing)
        self.elastic_factor = float(elastic_factor)
        self.tap_max_drift_px = float(tap_max_drift_px)
        self.tap_max_duration_ms = float(tap_max_duration_ms)
        self.wrap = bool(wrap)
        self.on_position = on_position
        self.on_settle = on_settle

        self.state = "idle"
        self.committed = self._clamp_int(initial_index)
        self.position = float(self.committed)

        # drag anchors (captured ONCE at begin so cumulative dx never drifts)
        self._anchor_pos = self.position          # live position at grab
        self._base = self.committed               # origin index for this gesture
        self._down_x = 0.0
        self._down_t = 0.0
        self._samples: List[Tuple[float, float]] = []   # (t, cumulative dx)

        # snap animation
        self._snap_from = 0.0
        self._snap_to = 0
        self._snap_start: Optional[float] = None   # stamped on first tick

    # -- geometry helpers ----------------------------------------------------

    def _clamp_int(self, i) -> int:
        if self.n <= 0:
            return 0
        i = int(round(i))
        if self.wrap:
            return i % self.n
        return max(0, min(self.n - 1, i))

    def current_index(self) -> int:
        """The centered tile: ``round(position)`` ([CXC-PRIM-CAROUSEL-MODEL])."""
        return self._clamp_int(round(self.position))

    def _clamp_position(self, p: float) -> float:
        """Clamp a raw position, applying edge rubber-band past the ends
        ([CXC-PRIM-CAROUSEL-TOUCH] elastic).  With ``wrap`` there are no ends."""
        if self.wrap or self.n <= 0:
            return p
        lo, hi = 0.0, float(self.n - 1)
        if p < lo:
            return lo + (p - lo) * self.elastic_factor
        if p > hi:
            return hi + (p - hi) * self.elastic_factor
        return p

    # -- driving position externally (goto / initial) ------------------------

    def set_position(self, p: float) -> None:
        """Hard-set position (no animation) — initial layout / test setup."""
        self.position = float(p)
        self.committed = self._clamp_int(round(p))
        self.state = "idle"
        self._emit_position()

    def _emit_position(self) -> None:
        if self.on_position is not None:
            self.on_position(self.position)

    # -- pointer gesture ([CXC-PRIM-CAROUSEL-TOUCH]) -------------------------

    def begin(self, x: float, y: float, t: float) -> None:
        """Pointer-down: CANCEL any in-flight snap and take over from the LIVE
        position (CAR-V7).  Anchors the drag on the current position so a
        cumulative-dx pan never accumulates error."""
        self._snap_start = None
        self.state = "dragging"
        self._anchor_pos = self.position
        self._base = self._clamp_int(round(self.position))
        self._down_x = float(x)
        self._down_t = float(t)
        self._samples = [(float(t), 0.0)]

    def update(self, dx: float, dy: float, t: float) -> None:
        """Pan: ``position = anchor + (−dx / S)`` — one full ``S`` of finger
        travel moves exactly one pane ([CXC-PRIM-CAROUSEL-TOUCH] 1:1 drag),
        with edge rubber-band past the ends."""
        if self.state != "dragging":
            return
        raw = self._anchor_pos + (-float(dx) / self.spacing)
        self.position = self._clamp_position(raw)
        self._samples.append((float(t), float(dx)))
        # keep the sample window bounded
        cutoff = float(t) - (_VELOCITY_WINDOW_S * 3.0)
        if len(self._samples) > 8:
            self._samples = [s for s in self._samples if s[0] >= cutoff] \
                or self._samples[-2:]
        self._emit_position()

    def finish(self, x: float, y: float, t: float) -> str:
        """Pointer-up: decide TAP vs SWIPE, then snap.

        Returns ``"tap"`` (the widget fires the centered tile's activate
        intent), ``"advance"`` (snapped to an adjacent tile), or ``"snap_back"``
        (sub-threshold; eased back to the origin tile)."""
        if self.state != "dragging":
            return "none"
        drift = abs(float(x) - self._down_x)
        dur_ms = (float(t) - self._down_t) * 1000.0
        total_dx = float(x) - self._down_x
        velocity = self._velocity(float(t), total_dx)

        # TAP: small drift AND short duration ([CXC-PRIM-CAROUSEL-TOUCH]).
        if drift < self.tap_max_drift_px and dur_ms < self.tap_max_duration_ms:
            self.state = "idle"
            self.position = float(self.committed)
            self._emit_position()
            return "tap"

        # SWIPE: advance ONE tile iff past the drift OR the flick threshold;
        # velocity picks direction, never distance (no momentum).
        advance = (abs(total_dx) > self.snap_threshold * self.spacing
                   or abs(velocity) > self.flick_velocity)
        if advance:
            basis = total_dx if abs(total_dx) > 1e-9 else velocity
            # finger LEFT (dx < 0) advances to the NEXT (higher) index.
            direction = 1 if basis < 0 else -1
            target = self._clamp_int(self._base + direction)
            result = "advance" if target != self._base else "snap_back"
        else:
            target = self._base
            result = "snap_back"

        self._start_snap(target)
        return result

    def _velocity(self, t_end: float, dx_end: float) -> float:
        """Release velocity in px/s from the samples within the recent window
        (px/s of FINGER travel; negative = leftward)."""
        ref: Optional[Tuple[float, float]] = None
        for s in self._samples:
            if s[0] >= t_end - _VELOCITY_WINDOW_S:
                ref = s
                break
        if ref is None and self._samples:
            ref = self._samples[0]
        if ref is None:
            return 0.0
        dt = t_end - ref[0]
        if dt <= 1e-6:
            return 0.0
        return (dx_end - ref[1]) / dt

    # -- snap animation ------------------------------------------------------

    def _start_snap(self, target: int) -> None:
        target = self._clamp_int(target)
        self._snap_from = self.position
        self._snap_to = target
        self._snap_start = None            # stamped on first tick (determinism)
        self.committed = target            # committed = the target integer
        self.state = "snapping"
        if abs(self._snap_from - float(target)) < 1e-9:
            # already there — settle immediately (still one clean event)
            self.position = float(target)
            self.state = "idle"
            self._emit_position()
            if self.on_settle is not None:
                self.on_settle(self.committed)

    def goto(self, index, t: Optional[float] = None) -> None:
        """Command a snap to ``index`` ([CXC-PRIM-CAROUSEL-BIND] ``goto_ns``) —
        identical to a swipe outcome.  Cancels any drag/snap first."""
        self.state = "snapping"
        self._start_snap(self._clamp_int(index))

    def tick(self, now: float) -> bool:
        """Advance the snap at clock ``now``.  Returns True while still
        animating (the widget keeps requesting frames).  The start time is
        stamped on the FIRST tick so the ease is a pure function of the
        logical clock ([CXC-LOT-DETERMINISM])."""
        if self.state != "snapping":
            return False
        if self._snap_start is None:
            self._snap_start = float(now)
        dur_s = self.snap_duration_ms / 1000.0
        if dur_s <= 0:
            frac = 1.0
        else:
            frac = (float(now) - self._snap_start) / dur_s
        if frac >= 1.0:
            self.position = float(self._snap_to)
            self.state = "idle"
            self._emit_position()
            if self.on_settle is not None:
                self.on_settle(self.committed)
            return False
        if frac < 0.0:
            frac = 0.0
        eased = self.easing(frac)
        self.position = self._snap_from + (float(self._snap_to) - self._snap_from) * eased
        self._emit_position()
        return True
