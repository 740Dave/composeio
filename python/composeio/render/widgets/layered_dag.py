"""
Shared LAYERED-DAG core ([CXC-R-MERMAID] / [CXC-DT-*]; reuse-by-reference, R3).

The ONE implementation of "lay a directed graph out by dependency depth and
draw its edges as arrows" — the geometry the Decisions-tab neighbourhood viz
(``decisions_graph``) and the markdown-viewer's mermaid-flowchart renderer
(``mermaid_flowchart``) BOTH consume.  A mermaid flowchart is the same shape as
a decision neighbourhood (a directed graph laid out by layer); a swimlane is
that same core with a lane partition on the cross axis.  A second layered-DAG
implementation would be a second graph engine and would let the two surfaces
drift — so the level assignment, the grid placement, and the arrow geometry
live HERE and nowhere else (grep-provable: ``assign_levels`` / ``layout_grid``
/ ``edge_point`` / ``draw_arrow_edge`` have exactly one def each).

Everything here is theme-free and canvas-contract-only: level assignment and
placement are pure geometry (no Canvas), and the two draw helpers issue only
``draw_line`` primitives.  Ink/width/shape are the CALLER's policy — the core
never picks a colour.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, Sequence, Tuple

from ..widget import Rect

# The orientations the grid understands.  Reversed axes (RL/BT) normalise to
# their forward twin — direction reversal is a caller nicety, not core geometry.
_TD = ("TD", "TB")
_LR = ("LR", "RL")


def normalize_direction(direction: str) -> str:
    """Fold a flow direction token to the canonical ``"TD"`` or ``"LR"``.

    ``TB`` -> ``TD``, ``RL`` -> ``LR``; anything unrecognised -> ``TD`` (the
    layered default).  Reversal (``RL``/``BT``) is treated as its forward twin
    in this first cut — the layered order is the same, only the reading sense
    would differ."""
    d = str(direction or "").strip().upper()
    if d in _LR:
        return "LR"
    return "TD"


def assign_levels(ids: Iterable[str],
                  layering_edges: Sequence[Tuple[str, str]]) -> Dict[str, int]:
    """Longest-path layering over directed *layering_edges* (``(src, tgt)`` =>
    ``tgt`` sits one level DEEPER than ``src``).

    This is the DAG core shared by the decision graph (``DEPENDS_ON`` pushes a
    dependency below its dependent) and the mermaid flowchart (``A --> B``
    pushes ``B`` below ``A``): identical semantics, one implementation.
    Iteration is bounded to ``len(ids) + 1`` relaxations, so a cycle (never
    expected in either corpus) degrades to a stable partial order rather than
    looping forever.  Edges whose endpoints are unknown are ignored."""
    known = set(ids)
    level: Dict[str, int] = {i: 0 for i in known}
    edges = [(s, t) for (s, t) in layering_edges if s in known and t in known]
    for _ in range(len(known) + 1):
        changed = False
        for s, t in edges:
            if level[t] < level[s] + 1:
                level[t] = level[s] + 1
                changed = True
        if not changed:
            break
    return level


def _clamp(lo: float, hi: float, v: float) -> float:
    return max(lo, min(hi, v))


def layout_grid(rows: Sequence[Sequence[Any]], area: Rect, *,
                orientation: str = "TD",
                cell_main: float,
                gap_main: float,
                cross_min: float,
                cross_max: float,
                gap_cross: float,
                center_main: bool = True,
                id_of: Callable[[Any], str] = lambda n: str(n)
                ) -> Dict[str, Rect]:
    """Place layered *rows* into ``Rect``s inside *area*.

    *rows* is level-ordered (row 0 = topmost/leftmost layer); each row is a
    sequence of nodes.  ``id_of(node)`` yields the key each node is stored
    under in the result.

    The MAIN axis advances layer-by-layer (down for ``TD``, right for ``LR``),
    each layer a fixed ``cell_main`` extent separated by ``gap_main``; the block
    is centred on the main axis when *center_main* and it is shorter than the
    area.  The CROSS axis spreads a layer's nodes evenly across the area, each
    node's cross extent = ``clamp(cross_min, cross_max, cross_pitch -
    gap_cross)``.

    ``TD`` gives nodes a fixed HEIGHT (``cell_main``) and a clamped WIDTH;
    ``LR`` gives them a fixed WIDTH (``cell_main``) and a clamped HEIGHT.  This
    is the exact grid ``decisions_graph`` used before extraction (TD, node
    height ``NODE_H``, width clamped ``NODE_MIN_W..NODE_MAX_W``)."""
    orient = normalize_direction(orientation)
    n = max(1, len(rows))
    pitch_main = cell_main + gap_main
    block = n * cell_main + (n - 1) * gap_main
    if orient == "TD":
        main_span, main_origin = area.h, area.y
        cross_origin, cross_span = area.x, area.w
    else:
        main_span, main_origin = area.w, area.x
        cross_origin, cross_span = area.y, area.h
    main0 = main_origin + (max(0.0, (main_span - block) / 2.0)
                           if center_main else 0.0)

    pos: Dict[str, Rect] = {}
    for ri, row in enumerate(rows):
        cols = max(1, len(row))
        cross_pitch = cross_span / cols
        node_cross = _clamp(cross_min, cross_max, cross_pitch - gap_cross)
        main_coord = main0 + ri * pitch_main
        for ci, node in enumerate(row):
            c = cross_origin + cross_pitch * (ci + 0.5)
            cc = c - node_cross / 2.0
            if orient == "TD":
                pos[id_of(node)] = Rect(cc, main_coord, node_cross, cell_main)
            else:
                pos[id_of(node)] = Rect(main_coord, cc, cell_main, node_cross)
    return pos


def edge_point(box: Rect, tx: float, ty: float) -> Tuple[float, float]:
    """The point on *box*'s border along the ray from its centre toward
    ``(tx, ty)`` — so an edge line starts/ends at the box edge, not its centre.
    (Extracted verbatim from ``decisions_graph`` — the single implementation.)
    """
    cx, cy = box.x + box.w / 2.0, box.y + box.h / 2.0
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return (cx, cy)
    hw, hh = box.w / 2.0, box.h / 2.0
    sx = hw / abs(dx) if dx != 0 else float("inf")
    sy = hh / abs(dy) if dy != 0 else float("inf")
    s = min(sx, sy)
    return (cx + dx * s, cy + dy * s)


def draw_arrow_edge(canvas: Any, frm: Rect, to: Rect, color, width: float,
                    *, arrow: float = 7.0, arrowhead: bool = True
                    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Draw a straight edge from the border of *frm* to the border of *to*,
    capped (when *arrowhead*) with a two-stroke arrowhead at *to*.  Returns the
    ``(start, end)`` border points so a caller can label the midpoint.

    Colour + width are the caller's policy; the geometry (border clipping +
    arrowhead) is shared so every layered-DAG surface draws the same arrow.
    Emits the shaft as one ``draw_line`` and each arrowhead barb as one more —
    the exact call shape ``decisions_graph`` relied on."""
    x1, y1 = frm.x + frm.w / 2.0, frm.y + frm.h / 2.0
    x2, y2 = to.x + to.w / 2.0, to.y + to.h / 2.0
    p1 = edge_point(frm, x2, y2)
    p2 = edge_point(to, x1, y1)
    canvas.set_stroke_color(*color)
    canvas.set_stroke_width(width)
    canvas.draw_line(p1[0], p1[1], p2[0], p2[1])
    if arrowhead:
        ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
        canvas.draw_line(p2[0], p2[1],
                         p2[0] - arrow * math.cos(ang - 0.5),
                         p2[1] - arrow * math.sin(ang - 0.5))
        canvas.draw_line(p2[0], p2[1],
                         p2[0] - arrow * math.cos(ang + 0.5),
                         p2[1] - arrow * math.sin(ang + 0.5))
    return p1, p2
