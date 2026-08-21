"""
ComposeUX Lottie Widget ([SPEC:CXC-LOTTIE])

Plays a Lottie (Bodymovin) vector animation through the Canvas contract
(render/lottie/ — see its docstring for the skottie renderer seam).
Non-interactive motion only ([CXC-LOT-INTENT]): dradis sweeps, pulsing
alert glyphs, spinners, transition flourishes.

Playback is a PURE FUNCTION of the engine's logical clock
([CXC-LOT-DETERMINISM]): the runtime ticks the widget via
on_clock_tick(now) — wall clock in run(), the logical timestep in
render_frame_at(t) — and every transport mutation (speed change,
goToAndStop, playSegments) rebases the playhead against the last ticked
clock value, never against time.monotonic().  Sampling the same clock
sequence therefore reproduces frames byte-identically macOS<->RPi.

Bus-bindable transport ([CXC-LOT-PLAYBACK], all via [SPEC:CXC-BIND]):
    autoplay        bool     play while true (pause holds the frame)
    loop            bool|int true = forever, N = N passes, false = once
    speed           float    rate multiplier (negative plays backward)
    go_to_and_stop  frame (>1 or int) or 0..1 float progress: jump+hold
    play_segments   [a, b] frames, marker name(s), or "a,b" string
Aliases setSpeed/goToAndStop/playSegments are normalized by the parser.

The animation JSON is a bus asset ([CXC-LOT-ASSET]): `src:` takes
doc://, bus://assets/, or file:// URIs through the engine AssetManager.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ..canvas import Canvas, Size
from ..lottie import LottieAnimation, LottieError, render_lottie
from ..widget import Constraints, Rect, Widget

_DIM = (0x66, 0x66, 0x78)


def _truthy(value: Any) -> bool:
    return str(value).lower() not in ("", "0", "off", "false", "none", "no")


class LottieWidget(Widget):
    """Vector animation from a Lottie JSON asset, bus-drivable transport."""

    def __init__(self, widget_id: str, *,
                 src: str = "",
                 width: float = 200,
                 height: float = 200,
                 fit: str = "contain",
                 autoplay: Any = True,
                 loop: Any = True,
                 speed: float = 1.0,
                 go_to_and_stop: Any = None,
                 play_segments: Any = None,
                 trigger: Any = None,
                 **kwargs: Any) -> None:
        # playback state exists BEFORE super().__init__ so property
        # setters invoked via base-class kwargs find their fields.
        self._anim: Optional[LottieAnimation] = None
        self._load_tried = False
        self._src = ""
        self._speed = 1.0
        self._autoplay = True
        self._loop: Any = True
        self._goto: Any = None
        self._segments_raw: Any = None
        self._segment: Optional[Tuple[float, float]] = None
        # deterministic transport: playhead = origin_frame +
        # (now - origin_now) * fr * speed, origin stamped on first tick
        # after a transport change (the behaviors.py convention).
        self._origin_now: Optional[float] = None
        self._origin_frame: Optional[float] = None
        self._last_now: Optional[float] = None
        self._frame: Optional[float] = None          # None until anim loads
        self._finished = False

        super().__init__(widget_id, "lottie", **kwargs)
        self.img_width = float(width)
        self.img_height = float(height)
        self.fit = str(fit)
        self.trigger = trigger                       # spec-12 namespace gate
        self._src = str(src)
        self._speed = self._coerce_speed(speed)
        self._autoplay = bool(autoplay) if isinstance(autoplay, bool) \
            else _truthy(autoplay)
        self._loop = self._coerce_loop(loop)
        self._goto = go_to_and_stop
        self._segments_raw = play_segments

    # ------------------------------------------------------------------
    # Bindable transport properties ([CXC-LOT-PLAYBACK]).  Every setter
    # rebases deterministically against the last ticked clock.
    # ------------------------------------------------------------------

    @property
    def src(self) -> str:
        return self._src

    @src.setter
    def src(self, value: Any) -> None:
        value = str(value or "")
        if value == self._src:
            return
        self._src = value
        self._anim = None
        self._load_tried = False
        self._segment = None
        self._frame = None
        self._origin_now = None
        self._origin_frame = None
        self._finished = False
        self.request_redraw()

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: Any) -> None:
        new = self._coerce_speed(value)
        if new == self._speed:
            return
        # rebase: current playhead becomes the new origin so the rate
        # change is continuous and takes effect next frame ([CXC-LOT-C2])
        if self._origin_now is not None and self._last_now is not None:
            self._origin_frame = self._playhead(self._last_now)
            self._origin_now = self._last_now
        self._speed = new
        self.request_redraw()

    @property
    def autoplay(self) -> Any:
        return self._autoplay

    @autoplay.setter
    def autoplay(self, value: Any) -> None:
        new = bool(value) if isinstance(value, bool) else _truthy(value)
        if new == self._autoplay:
            return
        self._autoplay = new
        if new:
            # resume from the current frame (pause/play, not restart)
            self._origin_now = None
            self._origin_frame = self._frame
            self._finished = False
        self.request_redraw()

    @property
    def loop(self) -> Any:
        return self._loop

    @loop.setter
    def loop(self, value: Any) -> None:
        self._loop = self._coerce_loop(value)
        self._finished = False
        self.request_redraw()

    @property
    def go_to_and_stop(self) -> Any:
        return self._goto

    @go_to_and_stop.setter
    def go_to_and_stop(self, value: Any) -> None:
        self._goto = value
        if value is None:
            # release the hold: resume from the held frame
            self._origin_now = None
            self._origin_frame = self._frame
            self._finished = False
        self.request_redraw()

    @property
    def play_segments(self) -> Any:
        return self._segments_raw

    @play_segments.setter
    def play_segments(self, value: Any) -> None:
        self._segments_raw = value
        self._segment = None                         # re-resolve lazily
        # a new segment restarts playback at its start ([CXC-LOT-C4])
        self._origin_now = None
        self._origin_frame = None
        self._finished = False
        self.request_redraw()

    # -- binding coercion hook (engine/bindings.coerce_bound_value) --------

    def coerce_bound_attr(self, attr: str, raw: Any) -> Any:
        """Widget-directed coercion for attrs whose types the generic
        rules cannot infer.  Return NotImplemented to use the defaults."""
        if attr == "play_segments":
            return raw                               # setter parses all forms
        if attr == "go_to_and_stop":
            if raw is None or raw == "":
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return raw                           # marker names allowed
        if attr == "loop":
            return self._coerce_loop(raw)
        return NotImplemented

    # -- coercers ------------------------------------------------------------

    @staticmethod
    def _coerce_speed(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _coerce_loop(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            n = int(value)
            return n if n > 0 else False
        s = str(value).strip().lower()
        if s.isdigit():
            n = int(s)
            return n if n > 0 else False
        return _truthy(value)

    # ------------------------------------------------------------------
    # Lifecycle + loading ([CXC-LOT-ASSET])
    # ------------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        reg = getattr(ctx, "register_clocked", None)
        if reg is not None:
            reg(self)

    def on_unmount(self) -> None:
        unreg = getattr(self._ctx, "unregister_clocked", None) \
            if self._ctx is not None else None
        if unreg is not None:
            unreg(self)                              # stale-instance hygiene
        super().on_unmount()

    def set_animation(self, anim: LottieAnimation) -> None:
        """Directly install a parsed animation (tests / embedders)."""
        self._anim = anim
        self._load_tried = True
        self._segment = None
        if self._frame is None:
            self._frame = anim.ip

    def _ensure_loaded(self) -> Optional[LottieAnimation]:
        if self._anim is not None or self._load_tried or not self._src:
            return self._anim
        self._load_tried = True
        data: Optional[bytes] = None
        assets = getattr(self._ctx, "assets", None) if self._ctx else None
        if assets is not None:
            data = assets.get_bytes(self._src)
        else:                                        # unmounted fallback (dev)
            try:
                from composeio import resolve as _resolve
                data = _resolve.resolve_bytes(self._src)
            except Exception:
                data = None
        if data is not None:
            try:
                self._anim = LottieAnimation.from_bytes(data)
            except LottieError:
                self._anim = None
        if self._anim is not None and self._frame is None:
            self._frame = self._anim.ip
        return self._anim

    # -- spec-12 compatibility: namespace + trigger gate playback ----------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if self.trigger is not None:
            # canonical match, same vocabulary as behavior triggers
            from ..engine.bindings import canon
            self.autoplay = canon(value) == canon(self.trigger)
        else:
            self.autoplay = _truthy(value)

    # ------------------------------------------------------------------
    # The deterministic transport ([CXC-LOT-DETERMINISM])
    # ------------------------------------------------------------------

    def _active_segment(self, anim: LottieAnimation) -> Tuple[float, float]:
        if self._segments_raw is not None and self._segment is None:
            self._segment = anim.resolve_segment(self._segments_raw)
        return self._segment if self._segment is not None \
            else (anim.ip, anim.op)

    def _hold_frame(self, anim: LottieAnimation) -> Optional[float]:
        """Resolve go_to_and_stop to an absolute frame, or None."""
        v = self._goto
        if v is None:
            return None
        a, b = self._active_segment(anim)
        if isinstance(v, str):
            seg = anim.resolve_segment(v)            # marker name -> start
            if seg is not None:
                return seg[0]
            try:
                v = float(v)
            except ValueError:
                return None
        if isinstance(v, float) and 0.0 <= v <= 1.0 and not v.is_integer():
            return a + (b - a) * v                   # progress form
        if v == 1.0 and isinstance(v, float):
            return b                                 # 1.0 float = end
        f = float(v)
        return max(a, min(b, f))

    def _playhead(self, now: float) -> float:
        """Linear playhead (pre-loop) at clock *now* — pure arithmetic."""
        anim = self._anim
        assert anim is not None
        a, _b = self._active_segment(anim)
        f0 = self._origin_frame if self._origin_frame is not None else a
        t0 = self._origin_now if self._origin_now is not None else now
        return f0 + (now - t0) * anim.fr * self._speed

    def _apply_loop(self, f_lin: float, a: float, b: float) -> float:
        span = b - a
        if span <= 0:
            return a
        loop = self._loop
        if loop is True:
            return a + ((f_lin - a) % span)
        passes = float(loop) \
            if isinstance(loop, int) and not isinstance(loop, bool) else 1.0
        limit = a + span * passes
        if (self._speed >= 0 and f_lin >= limit) or \
                (self._speed < 0 and f_lin <= a - span * (passes - 1.0)):
            self._finished = True
            return b if self._speed >= 0 else a
        return a + ((f_lin - a) % span)

    def on_clock_tick(self, now: float) -> bool:
        """Advance the playhead at clock *now*.  Returns True while the
        animation still needs frames (keeps the composition dirty)."""
        self._last_now = now
        anim = self._ensure_loaded()
        if anim is None:
            return False
        a, b = self._active_segment(anim)

        hold = self._hold_frame(anim)
        if hold is not None:                         # goToAndStop wins
            changed = self._frame != hold
            self._frame = hold
            self._origin_now = None
            self._origin_frame = hold
            return changed

        if not self._autoplay or self._finished:
            if self._frame is None:
                self._frame = a
            return False

        if self._origin_now is None:                 # stamp on first tick
            self._origin_now = now
            if self._origin_frame is None:
                self._origin_frame = a
        self._frame = self._apply_loop(self._playhead(now), a, b)
        return not self._finished

    # ------------------------------------------------------------------
    # Measure / draw
    # ------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(self.img_width, constraints.max_width),
                    min(self.img_height, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        anim = self._ensure_loaded()
        if anim is not None:
            frame = self._frame if self._frame is not None else anim.ip
            render_lottie(canvas, anim, frame, rect, self.fit)
            return
        # fallback placeholder — the screen is never broken
        canvas.save()
        canvas.set_fill_color(0x28, 0x28, 0x3C)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*_DIM)
        canvas.set_stroke_width(1)
        canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*_DIM)
        canvas.set_font("sans-serif", 10, "regular")
        canvas.draw_text(self._src or "no animation",
                         rect.x + rect.w / 2, rect.y + rect.h / 2, "center")
        canvas.restore()
