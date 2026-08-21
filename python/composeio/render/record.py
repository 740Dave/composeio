"""
RecordingCanvas — a Canvas-contract DISPLAY LIST ([CXC-R-DRAW-CONTRACT]).

Records every drawing call made against the Layer-1 contract into an op list
that can be replayed onto any real backend canvas later.  This is the engine's
lowered-cache primitive: an expensive lowering pass (walking an SVG document,
evaluating a diagram) runs ONCE against a recorder, and every subsequent frame
replays the captured contract calls — no re-parse, no re-walk.

First consumer: the native hookup widget's per-footprint cache
(`composeio.render.widgets.hookup`) — each Fritzing footprint SVG is lowered
once per (part, size) and replayed per redraw.

Design points:

  * **Contract-shaped, never backend-shaped** (R1): the ops are the abstract
    `Canvas` methods themselves, so a display list recorded on macOS replays
    identically through Skia, CanvasKit, or any future backend — the cache
    lives ABOVE the narrow waist.
  * **Queries delegate**: `measure_text` (and image decode) are questions only
    a real backend can answer.  The recorder forwards them to an optional
    *delegate* canvas — normally the live canvas of the frame that triggered
    the recording.  `set_font`/`set_font_scale` are BOTH recorded (replay
    needs them) and forwarded (so delegated measurement uses the right font).
    Without a delegate, a deterministic monospace-ish estimate keeps headless
    tests running.
  * **Replay is a pure fan-out**: `replay(target)` issues the recorded calls
    in order.  Recorded content that brackets itself in save/restore (e.g.
    everything `render_svg` emits) leaves the target's state untouched.

NOT recorded: `create_surface` / `present` / `present_partial` (surface
lifecycle belongs to the frame's owner, not to cached content).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from .canvas import Canvas, PathCommand, Size, TextMetrics


class RecordingCanvas(Canvas):
    """Canvas implementation that captures calls for later replay."""

    def __init__(self, delegate: Optional[Canvas] = None) -> None:
        self._delegate = delegate
        self._ops: List[Tuple[str, tuple]] = []
        # fallback text-metrics state (used only when no delegate is given)
        self._font_size = 12.0
        self._font_scale = 1.0

    # -- introspection / replay ---------------------------------------------

    @property
    def ops(self) -> List[Tuple[str, tuple]]:
        """The recorded (method, args) list — read-only by convention."""
        return self._ops

    def __len__(self) -> int:
        return len(self._ops)

    def replay(self, target: Canvas) -> None:
        """Issue every recorded call, in order, against *target*."""
        for name, args in self._ops:
            getattr(target, name)(*args)

    # -- recording core -------------------------------------------------------

    def _rec(self, name: str, *args: Any) -> None:
        self._ops.append((name, args))

    # -- Geometry -------------------------------------------------------------

    def fill_rect(self, x, y, w, h):
        self._rec("fill_rect", x, y, w, h)

    def stroke_rect(self, x, y, w, h):
        self._rec("stroke_rect", x, y, w, h)

    def fill_round_rect(self, x, y, w, h, radius):
        self._rec("fill_round_rect", x, y, w, h, radius)

    def stroke_round_rect(self, x, y, w, h, radius):
        self._rec("stroke_round_rect", x, y, w, h, radius)

    def fill_circle(self, cx, cy, r):
        self._rec("fill_circle", cx, cy, r)

    def stroke_circle(self, cx, cy, r):
        self._rec("stroke_circle", cx, cy, r)

    def fill_arc(self, cx, cy, r, start_angle, end_angle):
        self._rec("fill_arc", cx, cy, r, start_angle, end_angle)

    def stroke_arc(self, cx, cy, r, start_angle, end_angle):
        self._rec("stroke_arc", cx, cy, r, start_angle, end_angle)

    def draw_line(self, x1, y1, x2, y2):
        self._rec("draw_line", x1, y1, x2, y2)

    def draw_path(self, commands: List[PathCommand]):
        self._rec("draw_path", list(commands))

    def fill_path(self, commands: List[PathCommand]):
        self._rec("fill_path", list(commands))

    # -- Style State ------------------------------------------------------------

    def set_fill_color(self, r, g, b, a=255):
        self._rec("set_fill_color", r, g, b, a)

    def set_stroke_color(self, r, g, b, a=255):
        self._rec("set_stroke_color", r, g, b, a)

    def set_stroke_width(self, w):
        self._rec("set_stroke_width", w)

    def set_linear_gradient(self, x1, y1, x2, y2, stops):
        self._rec("set_linear_gradient", x1, y1, x2, y2, list(stops))

    def set_radial_gradient(self, cx, cy, r, stops):
        self._rec("set_radial_gradient", cx, cy, r, list(stops))

    def set_shadow(self, offset_x, offset_y, blur, color):
        self._rec("set_shadow", offset_x, offset_y, blur, color)

    def clear_shadow(self):
        self._rec("clear_shadow")

    def set_opacity(self, a):
        self._rec("set_opacity", a)

    # -- Transform & Clip ---------------------------------------------------

    def save(self):
        self._rec("save")

    def restore(self):
        self._rec("restore")

    def translate(self, dx, dy):
        self._rec("translate", dx, dy)

    def rotate(self, angle):
        self._rec("rotate", angle)

    def scale(self, sx, sy):
        self._rec("scale", sx, sy)

    def clip_rect(self, x, y, w, h):
        self._rec("clip_rect", x, y, w, h)

    def clip_round_rect(self, x, y, w, h, radius):
        self._rec("clip_round_rect", x, y, w, h, radius)

    # -- Text -----------------------------------------------------------------

    def set_font_scale(self, scale: float) -> None:
        self._font_scale = float(scale)
        self._rec("set_font_scale", scale)
        if self._delegate is not None:
            self._delegate.set_font_scale(scale)

    def set_font(self, family, size, weight="regular"):
        self._font_size = float(size)
        self._rec("set_font", family, size, weight)
        if self._delegate is not None:
            # forwarded so delegated measure_text answers with THIS font
            self._delegate.set_font(family, size, weight)

    def draw_text(self, text, x, y, align="left"):
        self._rec("draw_text", text, x, y, align)

    def measure_text(self, text: str) -> TextMetrics:
        if self._delegate is not None:
            return self._delegate.measure_text(text)
        # deterministic headless estimate (0.6em advance, 1.2em line)
        size = self._font_size * self._font_scale
        return TextMetrics(width=0.6 * size * len(text), height=1.2 * size,
                           ascent=0.8 * size, descent=0.2 * size)

    # -- Image ------------------------------------------------------------------

    def draw_image(self, image_handle, x, y, w, h):
        self._rec("draw_image", image_handle, x, y, w, h)

    def load_image(self, uri: str) -> Any:
        return self._delegate.load_image(uri) if self._delegate else None

    def decode_image(self, data: bytes) -> Any:
        return self._delegate.decode_image(data) if self._delegate else None

    # -- Surface (lifecycle is NOT cached content) ----------------------------

    def create_surface(self, width: int, height: int) -> None:
        pass

    def present(self) -> None:
        pass

    def get_surface_size(self) -> Size:
        if self._delegate is not None:
            return self._delegate.get_surface_size()
        return Size(0, 0)


__all__ = ["RecordingCanvas"]
