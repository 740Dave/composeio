"""
ComposeUX Media Surface Widget ([CXC-RM-MEDIA]).

ONE frame-source widget that composites a stream of externally-decoded
frames through the EXISTING Canvas contract.  Video file and webcam and
network stream are three PLUGGABLE SOURCES of the SAME widget (see
sources.py), not three special cases.

State on the bus, behavior in the (capable) widget
---------------------------------------------------
Two bus-driven layers, both "state on the bus":

  transport controls ([CXC-RM-MEDIA] bus-bindable) — play/pause (autoplay),
    seek, speed, loop, src — bound as widget attributes via [SPEC:CXC-BIND]
    property setters, exactly like LottieWidget's transport.

  the TRIGGER / invocation model (David 2026-07-18) — the INVOCATION is
    state-on-bus too.  This capable device (decoder + display) SUBSCRIBES a
    semantic namespace and, in response, plays a stream.  The trigger MAY be
    a pure semantic value mapped to a local source (`trigger_map`), NOT
    necessarily a media URL: the device maps the semantic to its own asset.
    So the bus carries INTENT (a source ref OR a higher-level semantic); the
    capable device owns PLAYBACK.  A device WITHOUT a decoder ignores it or
    shows the placeholder (graceful degradation, no special-case).  NO
    frames ever cross the bus.

Portability / tiers
-------------------
PyAV runs on macOS + Linux (workhorse: macOS, Debian x86 MinnowBoard, RPi).
CYD/ESP32 is NOT supported by this widget — an ESP32 cannot decode H.264;
that tier shows a poster frame or omits the surface (per [CXC-RM-MEDIA]
`CYD: not supported`).  The module imports cleanly when PyAV is absent
(`av_available()` False) and every instance degrades to its placeholder.

Compositing (v1) — decoded RGB -> in-memory PNG -> Canvas.decode_image ->
Canvas.draw_image, using ONLY existing contract methods.  A raw-frame
Canvas primitive that takes a decoded RGB buffer without the PNG round-trip
is the FOLLOW-UP optimization (architect owns canvas.py; see
pyav_support.py).

Pixel-gate: the widget CHROME/layout is pixel-verifiable; decoded-video /
live-camera CONTENT is NOT golden-able (non-deterministic decode / a live
feed is non-reproducible) — the [CXC-RM-MEDIA] pixel-gate exception.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...canvas import Canvas, Size
from ...widget import Constraints, Rect, Widget
from .pyav_support import Frame, av_available, rgb_to_png
from .sources import FrameSource, _scheme_of, make_source

_DIM = (0x66, 0x66, 0x78)


def _truthy(value: Any) -> bool:
    return str(value).lower() not in ("", "0", "off", "false", "none", "no")


class MediaWidget(Widget):
    """Frame-source media surface (video file / webcam / network stream)."""

    def __init__(self, widget_id: str, *,
                 src: str = "",
                 width: float = 320,
                 height: float = 240,
                 fit: str = "contain",
                 autoplay: Any = True,
                 loop: Any = True,
                 speed: float = 1.0,
                 trigger: Any = None,
                 trigger_map: Optional[Dict[str, str]] = None,
                 hwaccel: str = "none",
                 **kwargs: Any) -> None:
        # state exists BEFORE super().__init__ so base-class kwargs that hit
        # property setters find their backing fields (the LottieWidget idiom).
        self._src = ""
        self._source: Optional[FrameSource] = None
        self._autoplay = True
        self._speed = 1.0
        self._loop: Any = True
        self._finished = False

        # hardware-decode OPT-IN ([CXC-RM-MEDIA]; docs/provisioning/
        # minnowboard-vaapi.md §7).  "none" (default) == software decode ==
        # current behavior.  "auto"/"vaapi"/"videotoolbox"/... thread down
        # to the PyAV decoder via make_source; an unavailable device falls
        # back to software (logged once), never crashing.  This is a build-
        # time backend choice, NOT a live transport property — it is read
        # once when the source is opened, not rebound mid-stream.
        self._hwaccel = "none"

        # deterministic-ish frame pacing against the engine clock: pull the
        # next frame when the logical clock passes the next due time.
        self._next_due: Optional[float] = None
        self._last_now: Optional[float] = None
        self._frame_count = 0

        # current decoded frame + its lazily-built Canvas handle (draw-time
        # decode, cached by frame index — the ImageWidget idiom).
        self._frame: Optional[Frame] = None
        self._handle: Any = None
        self._handle_index: int = -1

        super().__init__(widget_id, "media", **kwargs)
        self.img_width = float(width)
        self.img_height = float(height)
        self.fit = str(fit)                      # stretch | contain | cover
        self.trigger = trigger
        self.trigger_map = dict(trigger_map) if trigger_map else None
        self._speed = self._coerce_speed(speed)
        self._autoplay = bool(autoplay) if isinstance(autoplay, bool) \
            else _truthy(autoplay)
        self._loop = self._coerce_loop(loop)
        self._hwaccel = str(hwaccel or "none")
        self._src = str(src or "")

    # ------------------------------------------------------------------
    # Bindable transport properties ([CXC-RM-MEDIA] / [SPEC:CXC-BIND])
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
        self._reset_source()
        self.request_redraw()

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: Any) -> None:
        new = self._coerce_speed(value)
        if new == self._speed:
            return
        self._speed = new
        # re-pace from now so the rate change takes effect next frame.
        self._next_due = self._last_now
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
        if new:                                   # resume (pause/play, not restart)
            self._next_due = self._last_now
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
    def hwaccel(self) -> str:
        """The requested hardware-decode backend ("none" == software).

        Read-only: hwaccel is a decode-backend opt-in fixed at construction,
        not a bus-rebindable transport property (unlike src/speed/loop). The
        device actually in effect (after any software fallback) is on the
        opened decoder as `hwaccel_used`.
        """
        return self._hwaccel

    # transport verbs (imperative API; bindings drive the properties above)
    def play(self) -> None:
        self.autoplay = True

    def pause(self) -> None:
        self.autoplay = False

    def seek(self, seconds: float) -> None:
        if self._source is not None and self._source.seekable:
            try:
                self._source.seek(float(seconds))
                self._finished = False
                self._next_due = self._last_now
            except Exception:
                pass  # unseekable / failed seek: keep playing (never break)

    # -- binding coercion hook (engine/bindings.coerce_bound_value) --------
    def coerce_bound_attr(self, attr: str, raw: Any) -> Any:
        if attr == "loop":
            return self._coerce_loop(raw)
        if attr == "speed":
            return self._coerce_speed(raw)
        return NotImplemented

    @staticmethod
    def _coerce_speed(value: Any) -> float:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return 1.0
        return f if f > 0 else 1.0

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
    # The trigger / invocation model (David 2026-07-18)
    # ------------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """Semantic invocation over the bus.

        Precedence:
          1. trigger_map present  -> semantic value maps to a LOCAL source
             (the pure-semantic case: `briefing="cylon_attack"` -> file://…).
          2. value is itself a source ref (file://, camera://, rtsp://, …)
             -> play it directly (the source-ref case).
          3. otherwise treat value as a play/pause gate on the current src
             (matched against `trigger` when set, else truthiness).
        A device with no decoder still receives this; it just can't open a
        source and shows the placeholder — graceful degradation.
        """
        from ...engine.bindings import canon

        if self.trigger_map:
            for key, source_uri in self.trigger_map.items():
                if canon(value) == canon(key):
                    self.src = str(source_uri)
                    self.play()
                    return
            self.pause()                          # unmapped semantic: stand down
            return

        sval = str(value)
        if _scheme_of(sval) is not None:
            self.src = sval
            self.play()
            return

        if self.trigger is not None:
            self.autoplay = canon(value) == canon(self.trigger)
        else:
            self.autoplay = _truthy(value)

    # ------------------------------------------------------------------
    # Lifecycle
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
            unreg(self)
        if self._source is not None:
            self._source.close()
        super().on_unmount()

    def set_source_object(self, source: FrameSource) -> None:
        """Install a pre-built FrameSource (tests / embedders)."""
        self._reset_source()
        self._source = source
        self._src = source.uri

    def _reset_source(self) -> None:
        if self._source is not None:
            self._source.close()
        self._source = None
        self._frame = None
        self._handle = None
        self._handle_index = -1
        self._next_due = None
        self._frame_count = 0
        self._finished = False

    def _ensure_open(self) -> Optional[FrameSource]:
        if self._source is None:
            if not self._src:
                return None
            self._source = make_source(self._src, hwaccel=self._hwaccel)
            if self._source is None:
                return None
        if not self._source.is_open:
            try:
                self._source.open()
            except Exception:
                self._source = None            # decode unavailable -> placeholder
                return None
        return self._source

    def _restart(self) -> None:
        """Loop back to the stream start (seek if possible, else reopen)."""
        src = self._source
        if src is None:
            return
        if src.seekable:
            try:
                src.seek(0.0)
                return
            except Exception:
                pass
        try:
            src.close()
            src.open()
        except Exception:
            self._source = None

    def _loops_forever(self) -> bool:
        return self._loop is True or (
            isinstance(self._loop, int) and not isinstance(self._loop, bool)
            and self._loop > 1)

    # ------------------------------------------------------------------
    # Clock tick — advance frames ([CXC-RM-MEDIA] decode on tick)
    # ------------------------------------------------------------------

    def on_clock_tick(self, now: float) -> bool:
        """Pull the next frame(s) due at logical clock *now*.

        Returns True while the surface still needs frames (keeps the
        composition dirty), matching the LottieWidget contract.
        """
        self._last_now = now
        src = self._ensure_open()
        if src is None:
            return False
        if not self._autoplay or self._finished:
            return False
        if self._next_due is None:
            self._next_due = now
        interval = 1.0 / max(src.fps, 1.0) / max(self._speed, 1e-3)

        advanced = False
        guard = 0
        while now >= self._next_due and guard < 8:  # bounded catch-up
            guard += 1
            frame = src.read()
            if frame is None:                       # end of stream
                if self._loops_forever():
                    self._restart()
                    frame = src.read()
                if frame is None:
                    self._finished = True
                    break
            self._frame = frame
            self._frame_count += 1
            self._handle_index = -1                 # invalidate cached handle
            self._next_due += interval
            advanced = True

        return advanced and not self._finished

    # ------------------------------------------------------------------
    # Measure / draw
    # ------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(self.img_width, constraints.max_width),
                    min(self.img_height, constraints.max_height))

    def _fit_rect(self, rect: Rect, sw: float, sh: float) -> Rect:
        if self.fit not in ("contain", "cover") or sw <= 0 or sh <= 0:
            return rect
        s = (min(rect.w / sw, rect.h / sh) if self.fit == "contain"
             else max(rect.w / sw, rect.h / sh))
        w, h = sw * s, sh * s
        return Rect(rect.x + (rect.w - w) / 2, rect.y + (rect.h - h) / 2, w, h)

    def _composite_handle(self, canvas: Canvas) -> Any:
        """v1 route: decoded RGB -> PNG -> decode_image, cached per frame.

        FOLLOW-UP (architect owns canvas.py): a raw-frame Canvas primitive
        (push a decoded RGB buffer without re-encoding to PNG) removes this
        per-frame encode+decode round-trip — DO NOT add it here.
        """
        frame = self._frame
        if frame is None:
            return None
        if self._handle is not None and self._handle_index == frame.index:
            return self._handle
        try:
            png = rgb_to_png(frame.width, frame.height, frame.rgb)
            self._handle = canvas.decode_image(png)
        except Exception:
            self._handle = None
        self._handle_index = frame.index
        return self._handle

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        handle = self._composite_handle(canvas)
        canvas.save()
        if handle is not None and self._frame is not None:
            dest = self._fit_rect(rect, self._frame.width, self._frame.height)
            if self.fit == "cover":
                canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
            canvas.draw_image(handle, dest.x, dest.y, dest.w, dest.h)
        else:
            self._draw_placeholder(canvas, rect)
        canvas.restore()

    def _draw_placeholder(self, canvas: Canvas, rect: Rect) -> None:
        """Poster/placeholder chrome — the screen is never broken.

        Also the CYD/ESP32 fallback surface (no H.264 decode) and the
        no-decoder graceful-degradation state.
        """
        canvas.set_fill_color(0x28, 0x28, 0x3C)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*_DIM)
        canvas.set_stroke_width(1)
        canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)
        # a small "play" triangle glyph so the surface reads as media
        cx, cy = rect.x + rect.w / 2, rect.y + rect.h / 2
        r = min(rect.w, rect.h) * 0.12
        from ...canvas import MoveTo, LineTo, Close
        canvas.set_fill_color(*_DIM)
        canvas.fill_path([MoveTo(cx - r * 0.6, cy - r),
                          LineTo(cx + r, cy),
                          LineTo(cx - r * 0.6, cy + r),
                          Close()])
        label = self._src or ("no media" if av_available()
                              else "media: PyAV not installed")
        canvas.set_font("sans-serif", 10, "regular")
        canvas.draw_text(label, cx, rect.y + rect.h - 8, "center")
