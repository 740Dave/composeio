"""
ComposeUX Layout Engine (M1)

Spec: [CXC-R-ENGINE-LAYOUT] — two-pass layout: measure (bottom-up,
preferred sizes) then arrange (top-down, containers assign child rects).

Three container policies:
  FlexContainer        row/column main axis, gap, padding, align, justify
  GridContainer        children placed by (row, column) cell coordinates
  PixelStackContainer  the Composer pixel-stack composite: a fixed-size
                       pixel canvas with absolutely positioned children,
                       z-order = list order (bottom -> top)

Containers are Widgets, so trees nest arbitrarily.  Every widget's
arranged rect (in surface coordinates) is stored on the widget after
arrange — that is the hit-test spatial index.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget


# ---------------------------------------------------------------------------
# Transform-aware child drawing ([SPEC:CXC-BIND] [CXC-BIND-ATTR])
# ---------------------------------------------------------------------------

# Cumulative opacity per canvas: set_opacity is a backend paint multiplier,
# NOT part of the save/restore stack, so nested opacities compose here.
_OPACITY_STACK: Dict[int, List[float]] = {}

# [CXC-PRIM-STALE] depth of the staleness treatment currently being drawn, per
# canvas.  A stale CONTAINER already hatches its whole subtree; hatching a
# stale child inside it again would stack two textures into an illegible mess
# and would say "two things died" when one did.  The outermost treated
# ancestor wins.
_STALE_DEPTH: Dict[int, int] = {}


def _staleness_of(ch: Widget):
    """The treatment *ch* should be given by the ENGINE, or None.

    None when the widget is believable, when it declared no cadence
    (UNDECLARED is never judged — treating it would mark every widget in
    every existing scene), or when it opted out with `staleness = "none"` /
    renders its own with `staleness = "self"`.
    """
    mode = getattr(ch, "staleness_treatment", "auto")
    if mode != "auto":
        return None
    f = getattr(ch, "freshness", None)
    return None if f is None else f.treatment()


def draw_child(canvas: Canvas, ch: Widget, rect: Rect) -> None:
    """Draw *ch* into *rect*, applying its bindable render transforms:
    rotation (degrees) and scale about the rect center, opacity as a
    multiplicative paint alpha.  Widgets with default values take the
    zero-cost path (a plain draw call).

    Dirty-region pruning ([CXC-R-DAMAGE]): this is the single choke point
    through which every child (and thus every subtree) is drawn, so it is
    where partial-redraw pruning lives — using the REAL draw path, never a
    reimplementation.  When the engine has stamped a damage region on the
    canvas (``canvas._damage``), a child whose PAINT BOUNDS miss the damage
    is skipped entirely (its subtree never draws), which is the win that
    matters most for the raster-constrained embedded backends.  With no
    damage stamped (a full redraw, every golden/deterministic path) the
    check is a single attribute read and every child draws as before."""
    dmg = getattr(canvas, "_damage", None)
    if dmg is not None and not dmg.intersects(ch.paint_bounds()):
        return
    rot = float(getattr(ch, "rotation", 0.0) or 0.0)
    scl = float(getattr(ch, "scale", 1.0))
    op = float(getattr(ch, "opacity", 1.0))

    # [CXC-PRIM-STALE] the staleness treatment rides the SAME opacity
    # composition the bindable transforms use: the dim is a multiplier folded
    # into `op`, so the widget's own draw() needs no knowledge of it and there
    # is exactly ONE path that dims ([PRINCIPLE-ONE-MEASUREMENT-PATH]).  The
    # hatch and badge are drawn AFTER, at full opacity, because a dimmed
    # warning is the failure it is warning about.
    treat = None
    if _STALE_DEPTH.get(id(canvas), 0) == 0:
        treat = _staleness_of(ch)
    if treat is not None:
        op *= treat.dim

    if rot == 0.0 and scl == 1.0 and op >= 1.0:
        ch.draw(canvas, rect)
        return

    stack = _OPACITY_STACK.setdefault(id(canvas), [1.0])
    eff = max(0.0, min(1.0, stack[-1] * op))
    canvas.save()
    if rot != 0.0 or scl != 1.0:
        cx, cy = rect.x + rect.w / 2.0, rect.y + rect.h / 2.0
        canvas.translate(cx, cy)
        if rot != 0.0:
            canvas.rotate(math.radians(rot))
        if scl != 1.0:
            canvas.scale(scl, scl)
        canvas.translate(-cx, -cy)
    stack.append(eff)
    canvas.set_opacity(eff)
    if treat is not None:
        _STALE_DEPTH[id(canvas)] = _STALE_DEPTH.get(id(canvas), 0) + 1
    try:
        ch.draw(canvas, rect)
    finally:
        if treat is not None:
            _STALE_DEPTH[id(canvas)] -= 1
        stack.pop()
        canvas.set_opacity(stack[-1])
        canvas.restore()
    if treat is not None:
        from ..freshness_view import draw_treatment
        draw_treatment(canvas, rect, treat, getattr(ch.freshness, "age_s", None))


def _resolve_dim(value: Any, available: float, fallback: float) -> float:
    """Resolve a width/height spec: number, "NN%", or "auto"."""
    if value is None or value == "auto":
        return fallback
    if isinstance(value, str):
        if value.endswith("%"):
            return available * float(value[:-1]) / 100.0
        return float(value)
    return float(value)


def _resolve_coord(value: Any, extent: float) -> float:
    """Resolve a pixel-stack coordinate: px number, fraction 0-1, or "NN%"."""
    if isinstance(value, str) and value.endswith("%"):
        return extent * float(value[:-1]) / 100.0
    v = float(value)
    if isinstance(value, float) and 0.0 <= v <= 1.0:
        return extent * v
    return v


def _resolve_coord_px(value: Any, extent: float) -> float:
    """Resolve a pixel-stack coordinate as ABSOLUTE PIXELS ([CXC-PRIM-DRAGVAL]).

    Used for BOUND geometry (a fill.width / thumb.x expression on a live
    value): "NN%" still resolves against the extent, but a bare number is
    always taken as px — the 0..1-float-means-fraction shorthand of
    ``_resolve_coord`` is deliberately skipped so a computed sub-pixel value
    is not silently reinterpreted as a fraction of the track."""
    if isinstance(value, str) and value.endswith("%"):
        return extent * float(value[:-1]) / 100.0
    return float(value)


class Container(Widget):
    """Base container: children, padding, background, clip, hit-test."""

    def __init__(self, widget_id: str, widget_type: str = "container",
                 **kwargs: Any) -> None:
        self.children: List[Widget] = []
        self.padding = (0.0, 0.0, 0.0, 0.0)      # top, right, bottom, left
        self.gap = 0.0
        self.background: Optional[tuple] = None   # (r, g, b[, a])
        self.corner_radius = 0.0
        self.border_color: Optional[tuple] = None
        self.clip_children = False
        super().__init__(widget_id, widget_type, **kwargs)

    def add(self, child: Widget) -> "Container":
        self.children.append(child)
        return self

    # -- layout child ordering hook ------------------------------------------

    def _flow_children(self) -> List[Widget]:
        """The children to LAY OUT, in layout order ([CXC-R-ENGINE-LAYOUT]).

        Defaults to the declared child list — every container that lays out
        by list order (flex/grid) iterates THIS, not ``self.children``
        directly, so a subclass can reorder or exclude children from layout
        WITHOUT mutating the tree (the reversible order/visibility primitive,
        [SPEC:CXC-ORDER]).  Draw + hit-test still walk ``self.children`` in
        declared order and honor each child's ``visible`` flag, so a subclass
        that reorders layout keeps z-order stable and reversible."""
        return self.children

    # -- lifecycle propagates ------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        self._ctx = ctx          # containers publish/redraw too (M4 fix)
        for ch in self.children:
            ch.on_mount(ctx)

    def on_unmount(self) -> None:
        for ch in self.children:
            ch.on_unmount()

    # -- arrange contract ------------------------------------------------------

    def arrange(self, rect: Rect) -> None:
        """Assign this container's rect and lay out children.
        Subclasses implement _arrange_children."""
        self.arranged_rect = rect
        t, r, b, l = self.padding
        inner = Rect(rect.x + l, rect.y + t,
                     max(0.0, rect.w - l - r), max(0.0, rect.h - t - b))
        self._arrange_children(inner)

    def _arrange_children(self, inner: Rect) -> None:
        raise NotImplementedError

    # -- draw ---------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        if self.background is not None:
            bg = self.background
            canvas.set_fill_color(*bg[:3], bg[3] if len(bg) > 3 else 255)
            if self.corner_radius > 0:
                canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h,
                                       self.corner_radius)
            else:
                canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        if self.border_color is not None:
            bc = self.border_color
            canvas.set_stroke_color(*bc[:3], bc[3] if len(bc) > 3 else 255)
            canvas.set_stroke_width(1)
            if self.corner_radius > 0:
                canvas.stroke_round_rect(rect.x, rect.y, rect.w, rect.h,
                                         self.corner_radius)
            else:
                canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)
        if self.clip_children:
            if self.corner_radius > 0:
                canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h,
                                       self.corner_radius)
            else:
                canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        for ch in self.children:
            if getattr(ch, "visible", True) and ch.arranged_rect is not None:
                draw_child(canvas, ch, ch.arranged_rect)
        canvas.restore()

    # -- hit test (topmost first, per [CXC-R-ENGINE-HIT]) ----------------------

    def hit_test_tree(self, x: float, y: float) -> Optional[Widget]:
        """Return the topmost interactive widget at (x, y), or None.
        Children are tested in reverse order (last drawn = topmost)."""
        r = self.arranged_rect
        if r is None or not _contains(r, x, y):
            return None
        for ch in reversed(self.children):
            if not getattr(ch, "visible", True):
                continue
            if isinstance(ch, Container):
                hit = ch.hit_test_tree(x, y)
                if hit is not None:
                    return hit
            elif ch.arranged_rect is not None and _contains(ch.arranged_rect, x, y):
                if ch.hit_test(x, y):
                    return ch
        # container itself is a hit target only if flagged interactive
        return self if getattr(self, "interactive", False) else None

    def iter_widgets(self):
        """Depth-first iteration over the subtree (self included)."""
        yield self
        for ch in self.children:
            if isinstance(ch, Container):
                yield from ch.iter_widgets()
            else:
                yield ch


def _contains(r: Rect, x: float, y: float) -> bool:
    return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h


# ---------------------------------------------------------------------------
# Flex
# ---------------------------------------------------------------------------

class FlexContainer(Container):
    """Row/column flex layout: gap, align (cross axis), justify (main)."""

    def __init__(self, widget_id: str, *, direction: str = "row",
                 align: str = "start", justify: str = "start",
                 **kwargs: Any) -> None:
        self.direction = direction
        self.align = align
        self.justify = justify
        super().__init__(widget_id, "stack", **kwargs)

    def measure(self, constraints: Constraints) -> Size:
        t, r, b, l = self.padding
        avail_w = constraints.max_width - l - r
        avail_h = constraints.max_height - t - b
        main, cross = 0.0, 0.0
        horizontal = self.direction == "row"
        kids = self._flow_children()
        for ch in kids:
            s = _measure_child(ch, avail_w, avail_h)
            if horizontal:
                main += s.width
                cross = max(cross, s.height)
            else:
                main += s.height
                cross = max(cross, s.width)
        if kids:
            main += self.gap * (len(kids) - 1)
        w = main + l + r if horizontal else cross + l + r
        h = cross + t + b if horizontal else main + t + b
        w = _resolve_dim(self.layout.width, constraints.max_width, w)
        h = _resolve_dim(self.layout.height, constraints.max_height, h)
        return Size(min(w, constraints.max_width),
                    min(h, constraints.max_height))

    def _arrange_children(self, inner: Rect) -> None:
        horizontal = self.direction == "row"
        kids = self._flow_children()
        sizes = [_measure_child(ch, inner.w, inner.h) for ch in kids]
        mains = [(s.width if horizontal else s.height) for s in sizes]
        total_main = sum(mains) + self.gap * max(0, len(kids) - 1)
        avail_main = inner.w if horizontal else inner.h

        # flex grow (M4): CSS `flex: N` semantics — a growing child's main
        # basis is 0 and the whole free main axis is shared proportional to
        # grow factors.  Grow consumes the free space, so the justify
        # offsets below see free == 0.
        grows = [float(getattr(ch.layout, "grow", 0.0)) for ch in kids]
        total_grow = sum(g for g in grows if g > 0)
        if total_grow > 0:
            fixed_main = sum(m for m, g in zip(mains, grows) if g <= 0)
            fixed_main += self.gap * max(0, len(kids) - 1)
            pool = max(0.0, avail_main - fixed_main)
            for i, g in enumerate(grows):
                if g > 0:
                    share = pool * g / total_grow
                    if horizontal:
                        sizes[i] = Size(share, sizes[i].height)
                    else:
                        sizes[i] = Size(sizes[i].width, share)
                    mains[i] = share
            total_main = sum(mains) + self.gap * max(0, len(kids) - 1)

        # justify -> starting offset + extra gap
        offset, extra_gap = 0.0, 0.0
        free = max(0.0, avail_main - total_main)
        if self.justify == "center":
            offset = free / 2
        elif self.justify == "end":
            offset = free
        elif self.justify == "space-between" and len(kids) > 1:
            extra_gap = free / (len(kids) - 1)
        elif self.justify == "space-around" and kids:
            extra_gap = free / len(kids)
            offset = extra_gap / 2

        pos = (inner.x if horizontal else inner.y) + offset
        for ch, s in zip(kids, sizes):
            cw, chh = s.width, s.height
            if horizontal:
                if self.align == "stretch":
                    chh = inner.h
                cross_extent = inner.h - chh
                cy = inner.y + (cross_extent / 2 if self.align == "center"
                                else cross_extent if self.align == "end" else 0)
                rect = Rect(pos, cy, cw, chh)
                pos += cw + self.gap + extra_gap
            else:
                if self.align == "stretch":
                    cw = inner.w
                cross_extent = inner.w - cw
                cx = inner.x + (cross_extent / 2 if self.align == "center"
                                else cross_extent if self.align == "end" else 0)
                rect = Rect(cx, pos, cw, chh)
                pos += chh + self.gap + extra_gap
            _assign(ch, rect)


# ---------------------------------------------------------------------------
# Ordered / visibility-bound flow ([SPEC:CXC-ORDER])
# ---------------------------------------------------------------------------

class OrderedFlowContainer(FlexContainer):
    """A flex flow container whose child ORDER and SHOWN-SET are BOUND to a
    bus state namespace ([SPEC:CXC-ORDER]): publish an order/visibility state
    and the children reorder / hide / show and the container REFLOWS.

    This is the reusable primitive under any *customizable-layout* surface —
    a rearrangeable dashboard, an Intelligence-drivable collaboration region,
    a user-hidable card rail.  It is a strict, additive superset of
    ``stack``/flex: with NO state published it lays out byte-for-byte like the
    FlexContainer it subclasses (``_flow_children`` returns the declared list
    unchanged), so converting an existing ``stack`` to ``ordered_stack`` is a
    zero-diff change until a state actually drives it — UNCONDITIONALLY, with
    no key-by-key caveat.  That is why every default here is the ``stack``
    default, ``direction="row"`` included: a conversion that relied on the
    implicit default must not silently flip the layout AXIS.  Do not "improve"
    this default to ``column`` because rails are the common case — the
    superset property is load-bearing ([SPEC:CXC-ORDER] [CXC-ORD-SUPERSET],
    and [SPEC:CXC-IC-LAUNCH]'s pane swap converts in place on the strength of
    it); pinned by ``test_ordered_container.py::
    test_no_direction_given_matches_stack_axis``.

    Semantics (all reversible — the declared tree is never mutated):

      * ``state_namespace`` carries EITHER
          - a LIST of ``widget_id`` s  -> pure REORDER: those ids first in the
            given order, any unlisted child kept in declared order after them;
            visibility untouched; OR
          - a DICT ``{order: [...], hidden: [...], visible: [...]}`` -> full
            control.  ``order`` reorders (as above).  ``hidden`` hides those
            ids (they leave the flow and stop drawing).  ``visible`` is the
            inverse allow-list: ONLY those ids are shown.  ``hidden`` and
            ``visible`` are mutually exclusive, and when a payload carries
            BOTH, ``hidden`` WINS — deterministically, regardless of key
            insertion order.  Precedence is deliberately NOT "last key wins":
            a payload crosses a JSON/YAML/MQTT boundary where mapping key
            order is not a guaranteed carrier, so hanging visibility on it
            would make the result depend on serializer fidelity.  A
            subtractive rule that cannot be defeated by key ordering is the
            safe rule for a visibility system.
      * ``visibility_namespace`` (optional, a SEPARATE source) carries either a
        list of hidden ids or a ``{id: bool}`` shown-map.

    Order affects LAYOUT position only; z-order (draw order) stays the declared
    ``self.children`` order, so overlapping children never flicker on reorder.
    A hidden child's ``visible`` flag is set False, so the base Container draw +
    hit-test skip it (no space, no pixels, not tappable) — and clearing the
    state restores it."""

    def __init__(self, widget_id: str, *, direction: str = "row",
                 align: str = "start", justify: str = "start",
                 state_namespace: str = "",
                 visibility_namespace: str = "", **kwargs: Any) -> None:
        super().__init__(widget_id, direction=direction, align=align,
                         justify=justify, **kwargs)
        self.widget_type = "ordered_stack"
        self.state_namespace = state_namespace
        self.visibility_namespace = visibility_namespace
        # None => declared order; a list => explicit id order (unlisted after).
        self._order: Optional[List[str]] = None
        # hidden ids (subtractive) XOR a visible-only allow-list.
        self._hidden: set = set()
        self._visible_only: Optional[set] = None

    # -- layout order + visibility ------------------------------------------

    def _shown(self, wid: str) -> bool:
        if self._visible_only is not None:
            return wid in self._visible_only
        return wid not in self._hidden

    def _flow_children(self) -> List[Widget]:
        kids = self.children
        if self._order is None and not self._hidden and self._visible_only is None:
            return kids                              # zero-cost identity path
        by_id: Dict[str, Widget] = {}
        for c in kids:
            by_id.setdefault(c.widget_id, c)
        if self._order is None:
            return [c for c in kids if self._shown(c.widget_id)]
        ordered: List[Widget] = []
        seen: set = set()
        for wid in self._order:
            c = by_id.get(wid)
            if c is not None and wid not in seen and self._shown(wid):
                ordered.append(c)
                seen.add(wid)
        for c in kids:                               # unlisted keep declared order
            if c.widget_id not in seen and self._shown(c.widget_id):
                ordered.append(c)
        return ordered

    def _sync_visibility(self) -> None:
        """Reflect the shown-set onto each child's ``visible`` flag so the
        base Container draw + hit-test skip hidden children.  Only invoked
        when a visibility source is active (or was, to restore) — a
        REORDER-only state never touches child visibility."""
        for c in self.children:
            c.visible = self._shown(c.widget_id)

    # -- bus binding --------------------------------------------------------

    def apply_order_state(self, value: Any) -> bool:
        """Apply an order/visibility payload from ``state_namespace``.
        Returns True when the effective order or shown-set changed."""
        new_order = self._order
        new_hidden = self._hidden
        new_visible: Optional[set] = self._visible_only
        touches_vis = False
        if isinstance(value, dict):
            if "order" in value:
                o = value.get("order")
                new_order = ([str(x) for x in o]
                             if isinstance(o, (list, tuple)) else None)
            if "visible" in value and value.get("visible") is not None:
                v = value.get("visible")
                new_visible = ({str(x) for x in v}
                               if isinstance(v, (list, tuple)) else None)
                new_hidden = set()
                touches_vis = True
            # `hidden` is applied AFTER `visible` ON PURPOSE, and that order is
            # the spec: when a payload carries both, `hidden` WINS regardless of
            # key insertion order ([SPEC:CXC-ORDER] [CXC-ORD-ORDER-STATE]).  Do
            # not reorder these two clauses, and do not "restore" last-key-wins
            # — key order does not survive a JSON/YAML/MQTT round trip reliably.
            # Pinned by test_hidden_beats_visible_regardless_of_key_order.
            if "hidden" in value:
                h = value.get("hidden")
                new_hidden = ({str(x) for x in h}
                              if isinstance(h, (list, tuple)) else set())
                new_visible = None
                touches_vis = True
        elif isinstance(value, (list, tuple)):
            new_order = [str(x) for x in value]      # pure reorder shorthand
        elif value is None:
            new_order = None
        return self._commit(new_order, new_hidden, new_visible, touches_vis)

    def apply_visibility_state(self, value: Any) -> bool:
        """Apply a shown-set from the SEPARATE ``visibility_namespace``: a
        list of hidden ids, or a ``{id: bool}`` shown-map."""
        new_hidden = self._hidden
        new_visible: Optional[set] = None
        if isinstance(value, dict):
            new_hidden = {str(k) for k, v in value.items() if not _truthy(v)}
        elif isinstance(value, (list, tuple)):
            new_hidden = {str(x) for x in value}
        elif value is None:
            new_hidden = set()
        return self._commit(self._order, new_hidden, new_visible, True)

    def _commit(self, new_order, new_hidden, new_visible,
                touches_vis: bool) -> bool:
        had_vis = bool(self._hidden) or self._visible_only is not None
        changed = (new_order != self._order or new_hidden != self._hidden
                   or new_visible != self._visible_only)
        self._order = new_order
        self._hidden = new_hidden
        self._visible_only = new_visible
        # Own child visibility whenever a visibility source is (or just was)
        # active; a reorder-only container never stomps child.visible.
        if touches_vis or had_vis or self._hidden or self._visible_only is not None:
            self._sync_visibility()
        return changed

    def register_added_child(self, child: Widget) -> None:
        """Hook for a RUNTIME-added child ([CXC-ICP-REBIND] hot-add): keep it
        shown under an active allow-list and append it to an active order so a
        dropped-in widget appears immediately rather than being hidden by
        omission."""
        if self._visible_only is not None:
            self._visible_only.add(child.widget_id)
        if self._order is not None and child.widget_id not in self._order:
            self._order.append(child.widget_id)
        child.visible = self._shown(child.widget_id)

    def on_bus_state(self, namespace: str, value: Any) -> None:
        changed = False
        if namespace and namespace == self.state_namespace:
            changed = self.apply_order_state(value)
        elif namespace and namespace == self.visibility_namespace:
            changed = self.apply_visibility_state(value)
        if changed:
            # [CXC-R-DAMAGE] the rail's own region reflows in place; damage is
            # scoped to this container (the runtime's schedule_update target
            # already unions old+new rect for a shrink).
            self.request_redraw()


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "shown", "show")
    return bool(v)


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------

class GridContainer(Container):
    """Cell grid: children carry .grid_row / .grid_col.  Column widths and
    row heights derive from the largest child in each track; leftover space
    distributes evenly (equal weight per track).

    M4 track weights ([CXC-RM-M4] engine-addition #1, closes CTk-retirement
    divergence #3): ``row_weights`` / ``col_weights`` control how leftover
    space distributes across tracks.  Accepted forms:
        None       -> legacy behavior (equal bonus when stretch=True)
        "equal"    -> every track weight 1 (explicit equal-weight spread)
        dict       -> {track_index: weight}; unlisted tracks weight 0
        list       -> [w0, w1, ...] by track order
    Tracks with weight 0 keep their measured size (top-pack behavior for
    the weighted axis when all weights are 0)."""

    def __init__(self, widget_id: str, *, stretch: bool = True,
                 row_weights: Any = None, col_weights: Any = None,
                 **kwargs: Any) -> None:
        self.stretch = stretch
        self.row_weights = row_weights
        self.col_weights = col_weights
        super().__init__(widget_id, "grid", **kwargs)

    @staticmethod
    def _weight_for(weights: Any, track: int, order: int) -> float:
        """Resolve the weight of *track* (its index) at list position *order*."""
        if weights == "equal":
            return 1.0
        if isinstance(weights, dict):
            return float(weights.get(track, weights.get(str(track), 0.0)))
        if isinstance(weights, (list, tuple)):
            return float(weights[order]) if order < len(weights) else 0.0
        return 0.0

    def _distribute(self, tracks: dict, ordered: list, weights: Any,
                    avail: float) -> None:
        """Distribute leftover *avail* space across *tracks* per *weights*."""
        used = sum(tracks.values()) + self.gap * (len(ordered) - 1)
        leftover = avail - used
        if leftover <= 0:
            return
        if weights is None:
            if self.stretch:                      # legacy equal bonus
                bonus = leftover / len(ordered)
                for t in ordered:
                    tracks[t] += bonus
            return
        wts = [self._weight_for(weights, t, i) for i, t in enumerate(ordered)]
        total = sum(wts)
        if total <= 0:
            return                                # all-zero weights: top-pack
        for t, wt in zip(ordered, wts):
            tracks[t] += leftover * wt / total

    @staticmethod
    def _cell(ch: Widget) -> tuple:
        return (int(getattr(ch, "grid_row", 0)), int(getattr(ch, "grid_col", 0)))

    def measure(self, constraints: Constraints) -> Size:
        t, r, b, l = self.padding
        col_w, row_h = self._track_sizes(constraints.max_width - l - r,
                                         constraints.max_height - t - b)
        w = sum(col_w.values()) + self.gap * max(0, len(col_w) - 1) + l + r
        h = sum(row_h.values()) + self.gap * max(0, len(row_h) - 1) + t + b
        w = _resolve_dim(self.layout.width, constraints.max_width, w)
        h = _resolve_dim(self.layout.height, constraints.max_height, h)
        return Size(min(w, constraints.max_width),
                    min(h, constraints.max_height))

    def _track_sizes(self, avail_w: float, avail_h: float):
        col_w: dict = {}
        row_h: dict = {}
        for ch in self.children:
            row, col = self._cell(ch)
            s = _measure_child(ch, avail_w, avail_h)
            col_w[col] = max(col_w.get(col, 0.0), s.width)
            row_h[row] = max(row_h.get(row, 0.0), s.height)
        return col_w, row_h

    def _arrange_children(self, inner: Rect) -> None:
        col_w, row_h = self._track_sizes(inner.w, inner.h)
        if not col_w:
            return
        cols = sorted(col_w)
        rows = sorted(row_h)
        self._distribute(col_w, cols, self.col_weights, inner.w)
        self._distribute(row_h, rows, self.row_weights, inner.h)
        x_off = {}
        x = inner.x
        for c in cols:
            x_off[c] = x
            x += col_w[c] + self.gap
        y_off = {}
        y = inner.y
        for r in rows:
            y_off[r] = y
            y += row_h[r] + self.gap
        for ch in self.children:
            row, col = self._cell(ch)
            _assign(ch, Rect(x_off[col], y_off[row], col_w[col], row_h[row]))


# ---------------------------------------------------------------------------
# Pixel stack (the Composer composite container)
# ---------------------------------------------------------------------------

class PixelStackContainer(Container):
    """Fixed-size pixel canvas with absolutely positioned, z-ordered children.

    This is the composite model from the Composer Workshop pre-vis: the
    widget being built is a stack of real widgets bottom->top over a fixed
    (design.width x design.height) pixel canvas.  Child position comes from
    the child's .stack_x/.stack_y (px, fraction, or "NN%"); size from
    .stack_w/.stack_h (same units, defaults to the child's measured size).
    Z-order is list order; hit-testing walks top->bottom (Container base).
    """

    def __init__(self, widget_id: str, *, canvas_width: Any = 320,
                 canvas_height: Any = 240, **kwargs: Any) -> None:
        # Canvas dims may be a fixed px number OR a relative spec ("NN%" /
        # "auto") — the latter lets an overlay-host pixel_stack REFLOW to fill
        # its parent ([CXC-ICP-S1-RESIZE]); a bare number keeps the shipped
        # fixed-canvas behavior byte-for-byte.
        self.canvas_width: Any = canvas_width
        self.canvas_height: Any = canvas_height
        kwargs.setdefault("clip_children", True)
        super().__init__(widget_id, "pixel_stack", **kwargs)

    def measure(self, constraints: Constraints) -> Size:
        w = _resolve_dim(self.canvas_width, constraints.max_width,
                         constraints.max_width)
        h = _resolve_dim(self.canvas_height, constraints.max_height,
                         constraints.max_height)
        return Size(min(w, constraints.max_width),
                    min(h, constraints.max_height))

    def _arrange_children(self, inner: Rect) -> None:
        for ch in self.children:
            # [CXC-PRIM-DRAGVAL] bound geometry attrs resolve as absolute px.
            px_attrs = getattr(ch, "_stack_px_attrs", ())
            rx = _resolve_coord_px if "stack_w" in px_attrs else _resolve_coord
            ry = _resolve_coord_px if "stack_h" in px_attrs else _resolve_coord
            sw = getattr(ch, "stack_w", None)
            sh = getattr(ch, "stack_h", None)
            if sw is not None:
                w = rx(sw, inner.w)
            else:
                w = None
            if sh is not None:
                h = ry(sh, inner.h)
            else:
                h = None
            if w is None or h is None:
                s = _measure_child(ch, w if w is not None else inner.w,
                                   h if h is not None else inner.h)
                w = w if w is not None else s.width
                h = h if h is not None else s.height
            cx = _resolve_coord_px if "stack_x" in px_attrs else _resolve_coord
            cy = _resolve_coord_px if "stack_y" in px_attrs else _resolve_coord
            x = inner.x + cx(getattr(ch, "stack_x", 0), inner.w)
            y = inner.y + cy(getattr(ch, "stack_y", 0), inner.h)
            _assign(ch, Rect(x, y, w, h))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _measure_child(ch: Widget, avail_w: float, avail_h: float) -> Size:
    """Measure a child, honoring explicit layout width/height overrides."""
    s = ch.measure(Constraints(avail_w, avail_h))
    w = _resolve_dim(getattr(ch.layout, "width", "auto"), avail_w, s.width)
    h = _resolve_dim(getattr(ch.layout, "height", "auto"), avail_h, s.height)
    return Size(w, h)


def _assign(ch: Widget, rect: Rect) -> None:
    ch.arranged_rect = rect
    if isinstance(ch, Container):
        ch.arrange(rect)
