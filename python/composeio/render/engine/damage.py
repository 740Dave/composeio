"""
ComposeUX Damage / Dirty-Region tracking ([CXC-R-DAMAGE]).

The engine's input-latency lever: instead of re-rastering the WHOLE surface
every time anything changes, the engine tracks the set of screen rectangles
that actually changed (a keystroke damages only the composer field, the
avatar breathe damages only the presence card) and redraws + presents ONLY
their union.  A full-scene raster of the 1280x1300 Intelligence Console is
~52 ms/frame; a keystroke that damages only the composer field is sub-frame.

This is a BACKEND-AGNOSTIC engine concept, not a Skia hack ([CXC-R-ARCH]):
the damage rectangles flow through the engine generically, the Canvas
``clip_rect`` primitive (already on the contract) bounds the raster, and the
optional ``Canvas.present_partial`` blits only the damaged sub-region.  The
raster-constrained embedded backends (Qualia/ESP32, the Dradis display)
benefit MOST — a partial-redraw path is the difference between a usable and
an unusable refresh there.

A ``DamageRegion`` is either:
  * FULL   — the whole surface is dirty (a bus-state change, a resize, an
             expose, or any caller that can't scope its change).  This is the
             conservative default: every pre-damage code path still marks the
             whole scene and renders byte-identically to before.
  * a set of RECTS — the accumulated union of scoped damage.  The engine
             clips to their bounding box, prunes the widget tree to the
             widgets whose paint bounds intersect the damage, and presents
             the region.

Correctness invariant ([CXC-R-DAMAGE-C1]): a partial redraw composites to
the SAME pixels as a full redraw.  This holds because (a) the clip never
changes *what* a primitive draws, only which pixels it is allowed to touch,
and (b) tree pruning only skips widgets whose PAINT BOUNDS
(``Widget.paint_bounds`` = arranged rect + ``paint_overflow``) miss the
damage, so every widget with pixels in the region is still drawn, in order.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..widget import Rect

# Anti-alias safety margin (logical px) added around every damage rect: a
# widget's antialiased edge (a stroked circle, text) bleeds ~1px past its
# geometric rect, and a hard clip exactly on that rect would shear the AA
# coverage.  Two pixels of slack keeps the partial redraw byte-identical to
# the full redraw at region boundaries.
AA_MARGIN = 2.0


def _inflate(r: Rect, m: float) -> Rect:
    return Rect(r.x - m, r.y - m, r.w + 2 * m, r.h + 2 * m)


def _clamp(r: Rect, w: float, h: float) -> Optional[Tuple[float, float, float, float]]:
    """Clamp *r* to the surface, integer-aligned outward; None when empty."""
    import math
    x0 = max(0.0, math.floor(r.x))
    y0 = max(0.0, math.floor(r.y))
    x1 = min(w, math.ceil(r.x + r.w))
    y1 = min(h, math.ceil(r.y + r.h))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def rects_intersect(a: Rect, b: Rect) -> bool:
    """True when two axis-aligned rects overlap (touching edges do not)."""
    return not (a.x + a.w <= b.x or b.x + b.w <= a.x or
                a.y + a.h <= b.y or b.y + b.h <= a.y)


def rect_contains(outer: Rect, inner: Rect) -> bool:
    """True when *inner* lies entirely within *outer* (edges inclusive)."""
    return (inner.x >= outer.x and inner.y >= outer.y and
            inner.x + inner.w <= outer.x + outer.w and
            inner.y + inner.h <= outer.y + outer.h)


# Above this many rects the O(n^2) containment prune is skipped: a frame with
# that much disjoint damage is already going to be dominated by the redraws, and
# the prune is an optimization, never a correctness requirement.
_PRUNE_LIMIT = 64


def prune_contained(rects: List[Rect]) -> List[Rect]:
    """Drop every rect that lies wholly inside another one, preserving order.

    WHY ([CXC-R-DAMAGE] frame budget; measured 2026-08-11).  ``_draw_partial``
    redraws the pruned widget tree ONCE PER RECT.  Damage accumulates from several
    independent sources in a frame — the widget's own ``request_redraw``, once per
    drained update, plus the pre/post-layout rects of the drained target — and they
    are frequently the SAME rect.  A Terminal Widget mid-burst produced four copies
    of one 96%-of-scene rect, so the whole tree was rastered four times to paint the
    same pixels: 20.4 ms of draw for 5.1 ms of content.

    Dropping a CONTAINED rect cannot change a pixel: the containing rect's pass
    clears and repaints that area with the same background, the same background
    layers, the same widgets in the same z-order, and the same focus ring — a clip
    restricts which pixels are touched, never what a primitive draws.  Disjoint
    rects are NEVER merged (that is the bounding-box mistake ``clamped_rects``
    exists to refuse); only redundant work is removed."""
    n = len(rects)
    if n < 2 or n > _PRUNE_LIMIT:
        return rects
    keep: List[Rect] = []
    for i, r in enumerate(rects):
        redundant = False
        for j, other in enumerate(rects):
            if i == j:
                continue
            if rect_contains(other, r):
                # Identical rects contain each other — keep the FIRST only.
                if rect_contains(r, other) and j > i:
                    continue
                redundant = True
                break
        if not redundant:
            keep.append(r)
    return keep


class DamageRegion:
    """Accumulates the dirty rectangles for one frame.

    Cheap and allocation-light: the common case is one or two rects.  The
    engine unions them into a single bounding box for the clip + present
    (a bbox is the simplest region every backend can express; a backend
    with real multi-rect scissor support can consume ``rects`` directly).
    """

    __slots__ = ("_full", "_rects")

    def __init__(self) -> None:
        self._full: bool = False
        self._rects: List[Rect] = []

    # -- accumulation --------------------------------------------------------

    def add_full(self) -> None:
        """Mark the entire surface dirty (the conservative default)."""
        self._full = True
        self._rects = []

    def add_rect(self, r: Optional[Rect]) -> None:
        """Add a damaged rect (already in surface coords).  A None rect —
        a widget with no arranged rect yet — escalates to FULL, since we
        cannot scope what we cannot locate."""
        if self._full:
            return
        if r is None:
            self.add_full()
            return
        self._rects.append(_inflate(r, AA_MARGIN))

    @classmethod
    def _of_rect(cls, r: Rect) -> "DamageRegion":
        """A single-rect region carrying *r* VERBATIM (no AA re-inflation):
        the per-rect pruning stamp used while a partial redraw walks the
        damage rects one at a time.  Not for accumulation — construction
        only."""
        d = cls()
        d._rects = [r]
        return d

    # -- queries -------------------------------------------------------------

    @property
    def is_full(self) -> bool:
        return self._full

    @property
    def is_empty(self) -> bool:
        return not self._full and not self._rects

    @property
    def rects(self) -> List[Rect]:
        return list(self._rects)

    def bounds(self) -> Optional[Rect]:
        """The bounding box of all damage rects, or None when empty/full."""
        if self._full or not self._rects:
            return None
        x0 = min(r.x for r in self._rects)
        y0 = min(r.y for r in self._rects)
        x1 = max(r.x + r.w for r in self._rects)
        y1 = max(r.y + r.h for r in self._rects)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def intersects(self, r: Optional[Rect]) -> bool:
        """True when *r* (a widget's paint bounds) overlaps any damage rect.
        A FULL region intersects everything; None paint bounds never do."""
        if self._full:
            return True
        if r is None:
            return False
        return any(rects_intersect(r, d) for d in self._rects)

    def clamped_bounds(self, w: float, h: float) -> Optional[Tuple[float, float, float, float]]:
        """Damage bbox clamped to the surface, as an integer-aligned
        (x, y, w, h) tuple ready for clip_rect / present_partial — or None
        when there is nothing partial to present."""
        b = self.bounds()
        if b is None:
            return None
        return _clamp(b, w, h)

    def clamped_rects(self, w: float, h: float) -> List[Rect]:
        """EACH damage rect individually clamped to the surface (empties
        dropped), as integer-aligned Rects — the per-rect redraw/present list.

        This is deliberately NOT the bounding box ([CXC-R-DAMAGE-C1]).  A live
        frame accumulates DISJOINT damage — a keystroke's composer field at the
        bottom, the header live-dot beat at the top, the gauge hotpulse in the
        rail — and their bounding box spans nearly the whole scene.  Clearing +
        presenting that bbox would erase every un-damaged card that happens to
        lie between the rects (its paint bounds intersect no actual damage rect,
        so pruning correctly skips its redraw) — a clear-then-restore FLASH.
        Walking the rects individually keeps a partial redraw touching ONLY the
        pixels that changed, so the cards between the rects are never disturbed.

        Rects wholly CONTAINED in another are dropped (see `prune_contained`):
        that is pure duplicate work, not a merge, and it is what stops a widget
        whose damage arrives from several sources in one frame from rastering the
        tree once per source.
        """
        if self._full or not self._rects:
            return []
        out: List[Rect] = []
        for r in self._rects:
            c = _clamp(r, w, h)
            if c is not None:
                out.append(Rect(*c))
        return prune_contained(out)
