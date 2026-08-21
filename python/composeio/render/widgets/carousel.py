"""
ComposeUX Carousel Widget ([SPEC:CXC-PRIM] [CXC-PRIM-CAROUSEL])

A horizontal SCALE-CAROUSEL (cover-flow): a strip of tiles where a CENTER tile
renders full-size and lifted above its neighbors, tiles to either side shrink
toward ``side_scale`` with distance, and a horizontal touch-swipe scrolls the
strip with snap-to-tile + edge rubber-band + a dot indicator row.  It is the
ComposePlatform equivalent of the CTk launcher's ``CarouselView``, re-expressed
in the "state on the bus, behavior in the widget" model.

Two seams, one reused, one new:

  * the DRAWING is a pure function of a single continuous ``position``
    ([CXC-PRIM-CAROUSEL-MODEL]).  Every tile — a static IMAGE or a LIVE child
    widget — is scaled UNIFORMLY through the render engine's canvas SCALE
    TRANSFORM at draw time ([CXC-PRIM-CAROUSEL-SCALE]): the SAME
    ``engine/layout.draw_child`` transform path the dradis rotation binding
    uses.  No pre-render-11-steps image hack; a live ``{widget: …}`` tile
    shrinks at the sides EXACTLY like a static image tile.

  * the INPUT is the ``SwipeCarouselController`` seam
    (engine/swipe_carousel.py, [CXC-PRIM-CAROUSEL-TOUCH]) — a drag/tap/snap/
    elastic state machine that WRITES ``position``.  Because it only writes the
    value, an Intelligence publishing a target index to ``goto_ns`` moves the
    carousel identically to a finger ([CXC-PRIM-CAROUSEL-BIND]).

Tier: console/desktop (Skia).  The embedded ESP32 variant is an explicitly
scoped, distinct reduced capability ([CXC-PRIM-CAROUSEL-TIER]) — NOT built
here.

The carousel is a ``Container`` (its tiles are real, mounted child widgets, so
a live tile keeps updating on the bus), but it OWNS all pointer input: its
``hit_test_tree`` never descends to a tile — a tap is resolved by the carousel,
not the tile under the finger.

This is a GENERAL-PURPOSE, project-neutral component (no project content lives
here): any scene instantiates it with its own tiles/content.

Config API (``.compose.yaml`` — ``type: carousel``)
---------------------------------------------------
Layout / geometry:
  * ``spacing``       — px between adjacent tile centers (one full ``spacing``
                        of finger travel == one tile).  Default 425.
  * ``tile_width`` / ``tile_height`` — the UNSCALED tile box in px (500/500).
  * ``center_scale`` / ``side_scale`` — cover-flow falloff: the centered tile
                        renders at ``center_scale`` (1.0), a fully-off-center
                        tile at ``side_scale`` (0.5), linearly between.
  * ``initial_index`` — resting tile at mount (0).
  * ``wrap``          — endless (no edge/elastic) when true (false).
  * ``show_dots`` / ``dot_active_color`` / ``dot_inactive_color`` — the dot
                        indicator row below the strip (on / accent / dim).
Gesture tuning (the swipe state machine):
  * ``snap_threshold``      — fraction of ``spacing`` a drag must pass to
                              advance a tile (0.15).
  * ``flick_velocity``      — px/s finger speed that advances a tile regardless
                              of drift (400); velocity picks DIRECTION only —
                              a flick never advances more than ONE tile.
  * ``snap_duration_ms`` / ``snap_easing`` — settle animation (300 /
                              ``ease_out_cubic``; also ``ease_out``/``linear``).
  * ``elastic_factor``      — edge rubber-band past the ends (0.30).
  * ``tap_max_drift_px`` / ``tap_max_duration_ms`` — tap-vs-drag discrimination
                              (15 px / 300 ms).
Bus surface ([CXC-PRIM-CAROUSEL-BIND] — every input is a peer):
  * ``index_ns``    (out) — settled centered index, + ``<index_ns>/target`` when
                            that tile declares a ``target`` (``carousel/index``).
  * ``position_ns`` (out) — the live continuous position, for observability
                            (``carousel/position``).
  * ``goto_ns``     (in)  — a published index snaps the carousel exactly like a
                            finger flick (``carousel/goto``).
  * ``activate_ns`` (out) — a center-tap on a tile with NO ``target`` publishes
                            the centered index here (``carousel/activate``); a
                            tile WITH a ``target`` instead fires
                            ``_app/<target>/open`` (the launch-bar convention).
Tiles (``tiles:`` list — each entry):
  * ``target`` — optional launch id (drives ``_app/<target>/open`` on tap).
  * ``label``  — optional caption / fallback swatch text.
  * ``image: <uri>``   — a static image tile, OR
  * ``widget: {...}``  — a LIVE child widget tile, built through the normal node
                         path so its own subscriptions/bindings bind by
                         reference and keep updating while it rides the strip.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from ..canvas import Canvas, Size
from ..engine.layout import Container, draw_child
from ..engine.swipe_carousel import SwipeCarouselController
from ..widget import Constraints, Rect, Widget

logger = logging.getLogger("composeio.render.carousel")

# [CXC-PRIM-TOKENS] dot indicator defaults — active = accent, inactive = dim.
_DOT_ACTIVE = (0x34, 0xC3, 0xD0, 255)     # dark-theme accent
_DOT_INACTIVE = (0x66, 0x66, 0x66, 255)   # CTk DOT_INACTIVE_COLOR
_DOT_AREA = 26.0                          # px reserved below the strip for dots
_DOT_RADIUS = 4.0
_DOT_GAP = 16.0


@dataclass
class TileGeom:
    """The render geometry of one tile at a given ``position`` — a pure
    function of ``position`` ([CXC-PRIM-CAROUSEL-MODEL])."""
    index: int
    x: float          # widget-relative center x: W/2 + (i − position)*S
    scale: float      # clamped cover-flow falloff
    culled: bool      # scaled box entirely off-screen


class CarouselWidget(Container):
    """Touch-driven horizontal scale-carousel ([CXC-PRIM-CAROUSEL])."""

    def __init__(self, widget_id: str, *,
                 spacing: float = 425.0,
                 tile_width: float = 500.0,
                 tile_height: float = 500.0,
                 center_scale: float = 1.0,
                 side_scale: float = 0.5,
                 snap_threshold: float = 0.15,
                 flick_velocity: float = 400.0,
                 snap_duration_ms: float = 300.0,
                 snap_easing: str = "ease_out_cubic",
                 elastic_factor: float = 0.30,
                 tap_max_drift_px: float = 15.0,
                 tap_max_duration_ms: float = 300.0,
                 wrap: bool = False,
                 initial_index: int = 0,
                 show_dots: bool = True,
                 dot_active_color: Any = None,
                 dot_inactive_color: Any = None,
                 index_ns: str = "carousel/index",
                 goto_ns: str = "carousel/goto",
                 activate_ns: str = "carousel/activate",
                 position_ns: str = "carousel/position",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "carousel", **kwargs)
        self.spacing = float(spacing)
        self.tile_width = float(tile_width)
        self.tile_height = float(tile_height)
        self.center_scale = float(center_scale)
        self.side_scale = float(side_scale)
        self.snap_threshold = float(snap_threshold)
        self.flick_velocity = float(flick_velocity)
        self.snap_duration_ms = float(snap_duration_ms)
        self.snap_easing = str(snap_easing)
        self.elastic_factor = float(elastic_factor)
        self.tap_max_drift_px = float(tap_max_drift_px)
        self.tap_max_duration_ms = float(tap_max_duration_ms)
        self.wrap = bool(wrap)
        self.initial_index = int(initial_index)
        self.show_dots = bool(show_dots)
        self.dot_active_color = _rgba(dot_active_color, _DOT_ACTIVE)
        self.dot_inactive_color = _rgba(dot_inactive_color, _DOT_INACTIVE)
        self.index_ns = str(index_ns)
        self.goto_ns = str(goto_ns)
        self.activate_ns = str(activate_ns)
        self.position_ns = str(position_ns)

        # tile metadata parallels self.children (the tile widgets)
        self._targets: List[str] = []
        self._labels: List[str] = []

        self.interactive = True
        self._controller: Optional[SwipeCarouselController] = None
        self._clock = time.monotonic

    # -- construction (called by the parser build hook) ----------------------

    def add_tile(self, child: Widget, *, target: str = "",
                 label: str = "") -> None:
        """Append a tile (an ImageWidget or a live child widget) + its
        activate metadata ([CXC-PRIM-CAROUSEL-TILES])."""
        self.add(child)
        self._targets.append(str(target or ""))
        self._labels.append(str(label or ""))

    @property
    def n(self) -> int:
        return len(self.children)

    # -- lifecycle -----------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)              # mounts tile children with ctx
        self._controller = SwipeCarouselController(
            n=self.n, spacing=self.spacing,
            snap_threshold=self.snap_threshold,
            flick_velocity=self.flick_velocity,
            snap_duration_ms=self.snap_duration_ms,
            snap_easing=self.snap_easing,
            elastic_factor=self.elastic_factor,
            tap_max_drift_px=self.tap_max_drift_px,
            tap_max_duration_ms=self.tap_max_duration_ms,
            wrap=self.wrap,
            initial_index=self.initial_index,
            on_position=self._on_position,
            on_settle=self._on_settle)
        # snap animation ticks on the engine clock (deterministic under
        # render_frame_at, [CXC-LOT-DETERMINISM]).
        if ctx is not None:
            ctx.register_clocked(self)
            # publish the resting index so peers know the initial state.
            self._on_settle(self._controller.current_index())

    def on_unmount(self) -> None:
        if self._ctx is not None:
            unreg = getattr(self._ctx, "unregister_clocked", None)
            if unreg is not None:
                unreg(self)
        super().on_unmount()

    # -- controller callbacks ------------------------------------------------

    def _on_position(self, position: float) -> None:
        """position changed (drag or snap): publish it for observability and
        repaint ([CXC-PRIM-CAROUSEL-BIND] position is a bus value)."""
        if self.position_ns:
            self.publish(self.position_ns, round(float(position), 4))
        self.request_redraw()

    def _on_settle(self, index: int) -> None:
        """A snap settled on an integer tile: publish the centered index (and
        its target, when set) to ``index_ns`` ([CXC-PRIM-CAROUSEL-BIND])."""
        idx = int(index)
        target = self._targets[idx] if 0 <= idx < len(self._targets) else ""
        if self.index_ns:
            self.publish(self.index_ns, idx)
        if target and self.index_ns:
            self.publish(f"{self.index_ns}/target", target)

    # -- pointer input (all decisions in the controller) ---------------------

    def hit_test(self, x: float, y: float) -> bool:
        r = self.arranged_rect
        if r is None:
            return False
        return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h

    def hit_test_tree(self, x: float, y: float) -> Optional[Widget]:
        """The carousel OWNS its whole rect — never descend to a tile
        ([CXC-PRIM-CAROUSEL-TOUCH]: the controller resolves tap-vs-drag, so a
        tile must not independently capture the pointer)."""
        return self if self.hit_test(x, y) else None

    def on_pointer_down(self, x: float, y: float) -> None:
        if self._controller is not None:
            self._controller.begin(x, y, self._clock())

    def on_pan_start(self) -> None:
        pass

    def on_pan(self, dx: float, dy: float) -> None:
        if self._controller is not None:
            self._controller.update(dx, dy, self._clock())

    def on_pan_end(self) -> None:
        pass          # release handled in on_pointer_up (fires first)

    def on_pointer_up(self, x: float, y: float) -> None:
        if self._controller is None:
            return
        action = self._controller.finish(x, y, self._clock())
        if action == "tap":
            self._activate_center()
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        pass          # tap intent fired via the controller's tap verdict

    def _activate_center(self) -> None:
        """Fire the centered tile's activate intent ([CXC-PRIM-CAROUSEL-BIND]):
        a ``target`` publishes ``_app/<target>/open`` (the launch-bar
        convention, [CXC-LB-INTENT-BINDING]); otherwise the generic
        ``activate_ns`` carries the centered index."""
        if self._controller is None:
            return
        idx = self._controller.current_index()
        target = self._targets[idx] if 0 <= idx < len(self._targets) else ""
        if target:
            self.publish(f"_app/{target}/open", "open")
        elif self.activate_ns:
            self.publish(self.activate_ns, idx)

    # -- bus (goto command) --------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.goto_ns and self._controller is not None:
            try:
                idx = int(round(float(value)))
            except (TypeError, ValueError):
                logger.warning("[CXC-PRIM-CAROUSEL] goto_ns %s got non-index "
                               "%r (ignored)", self.goto_ns, value)
                return
            self._controller.goto(idx)
            self.request_redraw()

    # -- clock (snap animation) ----------------------------------------------

    def on_clock_tick(self, now: float) -> bool:
        if self._controller is None:
            return False
        return self._controller.tick(now)

    # -- geometry (pure function of position, [CXC-PRIM-CAROUSEL-MODEL]) ------

    @property
    def position(self) -> float:
        return self._controller.position if self._controller else \
            float(self.initial_index)

    def tile_geometry(self, position: Optional[float] = None,
                      width: Optional[float] = None) -> List[TileGeom]:
        """Every tile's (x, scale, culled) at ``position`` — the pure
        cover-flow function verified by CAR-V1.  ``x`` is widget-relative
        (``W/2 + (i − position)*S``); add the arranged rect's x for surface
        coordinates."""
        if position is None:
            position = self.position
        if width is None:
            r = self.arranged_rect
            width = r.w if r is not None else (self.n * self.spacing)
        out: List[TileGeom] = []
        for i in range(self.n):
            x = width / 2.0 + (i - position) * self.spacing
            dist = min(1.0, abs(i - position))
            scale = max(self.side_scale,
                        self.center_scale
                        - (self.center_scale - self.side_scale) * dist)
            half = self.tile_width * scale / 2.0
            culled = (x + half < 0.0) or (x - half > width)
            out.append(TileGeom(i, x, scale, culled))
        return out

    # -- layout --------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        dots = _DOT_AREA if self.show_dots and self.n > 0 else 0.0
        pref_h = self.tile_height * self.center_scale + dots
        return Size(constraints.max_width,
                    min(pref_h, constraints.max_height))

    def arrange(self, rect: Rect) -> None:
        """Set the carousel rect and reflow tiles from the live position.
        Tiles are absolutely placed (their scale rides the draw transform), so
        the base flex/grid arrange is bypassed."""
        self.arranged_rect = rect
        self._reflow(rect)

    def _arrange_children(self, inner: Rect) -> None:
        pass          # never reached (arrange is overridden)

    def _strip_center_y(self, rect: Rect) -> float:
        dots = _DOT_AREA if self.show_dots and self.n > 0 else 0.0
        return rect.y + (rect.h - dots) / 2.0

    def _reflow(self, rect: Rect) -> None:
        """Assign each tile an UNSCALED box centered on its cover-flow point;
        the per-tile ``scale`` rides the ``draw_child`` transform
        ([CXC-PRIM-CAROUSEL-SCALE])."""
        cy = self._strip_center_y(rect)
        geoms = self.tile_geometry(self.position, rect.w)
        for g in geoms:
            tile = self.children[g.index]
            cx = rect.x + g.x
            tile.arranged_rect = Rect(cx - self.tile_width / 2.0,
                                      cy - self.tile_height / 2.0,
                                      self.tile_width, self.tile_height)
            tile.scale = g.scale
            tile.visible = not g.culled
            # a nested container tile lays out its own subtree at this box.
            if isinstance(tile, Container):
                tile.arrange(tile.arranged_rect)

    # -- draw ----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self.arranged_rect = rect
        self._reflow(rect)
        if self.background is not None:
            bg = self.background
            canvas.set_fill_color(*bg[:3], bg[3] if len(bg) > 3 else 255)
            if self.corner_radius > 0:
                canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h,
                                       self.corner_radius)
            else:
                canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        pos = self.position
        geoms = self.tile_geometry(pos, rect.w)
        # z-order: draw farthest-from-center FIRST so the centered tile is
        # LIFTED above its neighbors ([CXC-PRIM-CAROUSEL-MODEL] z-order).
        order = sorted((g for g in geoms if not g.culled),
                       key=lambda g: abs(g.index - pos), reverse=True)
        for g in order:
            tile = self.children[g.index]
            r = tile.arranged_rect
            if r is not None:
                # draw_child pushes the scale transform about the box center —
                # the ONE uniform scale path for image AND live-widget tiles.
                draw_child(canvas, tile, r)

        if self.show_dots and self.n > 0:
            self._draw_dots(canvas, rect, pos)

    def _draw_dots(self, canvas: Canvas, rect: Rect, position: float) -> None:
        active = self._clamp_index(round(position))
        total = (self.n - 1) * _DOT_GAP
        x0 = rect.x + rect.w / 2.0 - total / 2.0
        dy = rect.y + rect.h - _DOT_AREA / 2.0
        for i in range(self.n):
            color = self.dot_active_color if i == active \
                else self.dot_inactive_color
            canvas.set_fill_color(*color)
            canvas.fill_circle(x0 + i * _DOT_GAP, dy, _DOT_RADIUS)

    def _clamp_index(self, i) -> int:
        i = int(i)
        if self.wrap and self.n > 0:
            return i % self.n
        return max(0, min(self.n - 1, i))


def _rgba(value: Any, default) -> tuple:
    """Coerce a color spec to an RGBA 4-tuple, defaulting when absent."""
    if value is None:
        return default
    from ..engine.bindings import rgba as _b_rgba
    return _b_rgba(value, default)
