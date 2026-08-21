"""
ComposeUX Touch Visualizer Widget  ([SPEC:CXC-R-WIDGET-CONTRACT], [CXC-R-ENGINE-HIT])

A full-surface multitouch visualizer: every active finger paints a soft
radial halo + a solid core block, colored by its pointer_id, tracked
independently.  This is the first-class ComposeApp form of the raw-SDL2
`touch_viz.py` kiosk that proved the Turbot touch path (mary-ann) — now it
renders through the skia Canvas contract (cross-backend) and its fingers are
observable on the bus via the engine's interaction events ([CXC-R-CONTROL]).

Why a dedicated widget instead of the synthesized on_pan/on_tap hooks: those
route through the runtime's SINGLE _pointer_capture and carry no pointer_id,
so they COLLAPSE simultaneous fingers.  This widget opts into the raw
per-finger feed (``wants_raw_pointers = True``) and receives every
PointerEvent with its pointer_id intact — genuine N-finger tracking.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from ..canvas import Canvas, GradientStop, Size
from ..widget import Constraints, Rect, Widget

# Vivid, well-separated hues cycled by pointer_id.  A finger's colour is
# stable for its whole lifetime (keyed by the SDL fingerId).
_PALETTE: Tuple[Tuple[int, int, int], ...] = (
    (0xFF, 0x3B, 0x30),   # red
    (0xFF, 0x9F, 0x0A),   # orange
    (0xFF, 0xD6, 0x0A),   # yellow
    (0x34, 0xC7, 0x59),   # green
    (0x00, 0xC7, 0xBE),   # teal
    (0x0A, 0x84, 0xFF),   # blue
    (0x5E, 0x5C, 0xE6),   # indigo
    (0xBF, 0x5A, 0xF2),   # purple
    (0xFF, 0x2D, 0x55),   # pink
    (0x8E, 0x8E, 0x93),   # grey
)


class TouchVizWidget(Widget):
    """Fills its rect; draws a halo + core block under each active finger."""

    def __init__(self, widget_id: str, *,
                 core_size: float = 64.0,
                 halo_radius: float = 150.0,
                 background: Tuple[int, int, int, int] = (0x08, 0x0A, 0x10, 255),
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "touch_viz", **kwargs)
        self.core_size = float(core_size)
        self.halo_radius = float(halo_radius)
        self.background = background
        # Opt into the raw per-finger pointer feed (see runtime _dispatch_pointer).
        self.wants_raw_pointers = True
        # A viz surface is a hit target so fingers land on it.
        self.interactive = True
        # pointer_id -> (x, y) in surface coordinates.
        self._pointers: Dict[int, Tuple[float, float]] = {}

    # -- Layout -----------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # Greedy: fill whatever the layout offers (a fullscreen pixel-stack child).
        return Size(constraints.max_width, constraints.max_height)

    # -- Raw multitouch feed ----------------------------------------------------

    def on_raw_pointer(self, ev: Any) -> None:
        pid = ev.pointer_id
        if ev.type == "up":
            self._pointers.pop(pid, None)
        else:  # down | move
            self._pointers[pid] = (ev.x, ev.y)
        self.request_redraw()

    # -- Draw -------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()

        # Opaque backdrop so lifted fingers leave no trail.
        br, bg, bb, ba = self.background
        canvas.set_fill_color(br, bg, bb, ba)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        half = self.core_size / 2.0
        for pid, (x, y) in self._pointers.items():
            r, g, b = _PALETTE[pid % len(_PALETTE)]

            # Soft halo (bright centre -> transparent edge).
            canvas.set_radial_gradient(x, y, self.halo_radius, [
                GradientStop(0.0, r, g, b, 150),
                GradientStop(0.5, r, g, b, 55),
                GradientStop(1.0, r, g, b, 0),
            ])
            canvas.fill_circle(x, y, self.halo_radius)

            # Solid core block, centred on the finger.
            canvas.set_fill_color(r, g, b, 255)
            canvas.fill_round_rect(x - half, y - half,
                                   self.core_size, self.core_size, 8.0)

        canvas.restore()
