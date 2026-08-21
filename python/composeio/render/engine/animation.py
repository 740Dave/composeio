"""
ComposeUX Animation Engine (M1)

Spec: [CXC-R-ENGINE-ANIM] — property interpolation with easing, ticked
once per frame by the runtime.  Numeric, tuple (color/point), and
via-callback properties are supported.  Color tuples interpolate
per-channel; widgets wanting HSL-space blends can override
on_animation_frame and interpolate themselves.

Easing: linear, ease_in, ease_out, ease_in_out, spring.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


def _linear(t: float) -> float:
    return t


def _ease_in(t: float) -> float:
    return t * t


def _ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) * (1.0 - t)


def _ease_in_out(t: float) -> float:
    return 3 * t * t - 2 * t * t * t  # smoothstep


def _spring(t: float) -> float:
    """Underdamped spring settling to 1.0 (overshoot ~10%)."""
    return 1.0 - math.exp(-6.0 * t) * math.cos(8.0 * t)


EASING: Dict[str, Callable[[float], float]] = {
    "linear": _linear,
    "ease_in": _ease_in,
    "ease_out": _ease_out,
    "ease_in_out": _ease_in_out,
    "spring": _spring,
}


def _interpolate(a: Any, b: Any, t: float) -> Any:
    if isinstance(a, (tuple, list)):
        return tuple(_interpolate(x, y, t) for x, y in zip(a, b))
    if isinstance(a, int) and isinstance(b, int):
        return int(round(a + (b - a) * t))
    return a + (b - a) * t


@dataclass
class _Animation:
    target: Any                 # widget (or any object)
    prop: str
    from_val: Any
    to_val: Any
    duration: float             # seconds
    easing: Callable[[float], float]
    start_time: float
    on_complete: Optional[Callable[[], None]] = None
    done: bool = False


class Animator:
    """Frame-ticked property animator.

    animate() registers an animation; tick(now) advances all of them and
    writes interpolated values onto the target (setattr) plus calls the
    target's on_animation_frame hook.  Returns True while anything is
    animating so the runtime knows the composition is dirty.
    """

    def __init__(self) -> None:
        self._animations: List[_Animation] = []
        # [CXC-R-DAMAGE] widgets written by the most recent tick(), for the
        # engine to scope the redraw region.  Populated each tick().
        self.last_touched: List[Any] = []

    def animate(self, target: Any, prop: str, from_val: Any, to_val: Any,
                duration: float = 500.0, easing: str = "ease_out",
                on_complete: Optional[Callable[[], None]] = None) -> None:
        """duration is in milliseconds, matching the widget contract."""
        # Replace any in-flight animation of the same target+prop
        self._animations = [a for a in self._animations
                            if not (a.target is target and a.prop == prop)]
        self._animations.append(_Animation(
            target=target, prop=prop,
            from_val=from_val, to_val=to_val,
            duration=max(duration, 1.0) / 1000.0,
            easing=EASING.get(easing, _ease_out),
            start_time=time.monotonic(),
            on_complete=on_complete))

    def tick(self, now: Optional[float] = None) -> bool:
        """Advance all animations.  Returns True if any are active.

        Records the set of widgets it wrote this tick on ``last_touched``
        ([CXC-R-DAMAGE]) so the engine can scope the redraw to just the
        animating widgets' regions instead of the whole surface."""
        self.last_touched = []
        if not self._animations:
            return False
        now = time.monotonic() if now is None else now
        still: List[_Animation] = []
        for a in self._animations:
            t = (now - a.start_time) / a.duration
            if t >= 1.0:
                value = a.to_val
                a.done = True
            else:
                value = _interpolate(a.from_val, a.to_val, a.easing(t))
            try:
                setattr(a.target, a.prop, value)
            except AttributeError:
                pass
            self.last_touched.append(a.target)
            hook = getattr(a.target, "on_animation_frame", None)
            if hook:
                hook(a.prop, value)
            if a.done:
                if a.on_complete:
                    a.on_complete()
            else:
                still.append(a)
        self._animations = still
        return True

    @property
    def active(self) -> bool:
        return bool(self._animations)

    def cancel(self, target: Any, prop: Optional[str] = None) -> None:
        self._animations = [
            a for a in self._animations
            if not (a.target is target and (prop is None or a.prop == prop))]
