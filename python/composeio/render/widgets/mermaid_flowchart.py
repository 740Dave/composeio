"""
Mermaid-SUBSET flowchart renderer ([CXC-R-MERMAID]) — Canvas-primitive, inline
in the markdown viewer's document flow.

The markdown viewer detects a fenced ```mermaid block and, instead of showing
its source, DRAWS it: a mermaid *flowchart* (``graph``/``flowchart`` +
``TD``/``LR``) is a directed graph laid out by layer — the SAME shape as the
Decisions-tab neighbourhood viz — so this renderer parses the subset into a
graph model and hands it to the SHARED layered-DAG core (``layered_dag``: level
assignment, grid placement, arrow geometry) that ``decisions_graph`` also uses.
A *swimlane* (mermaid ``subgraph``…``end``) is that same core with a lane
partition on the cross axis (one ``layout_grid`` call per lane band).

SUBSET, deliberately (own-every-pixel with Canvas primitives, no diagram
engine, NO NEW primitive):

  * diagrams: ``graph``/``flowchart`` with direction ``TD``/``TB``/``LR``/``RL``
    and ``subgraph``…``end`` swimlanes.  ANY OTHER diagram type
    (``sequenceDiagram``, ``classDiagram``, ``stateDiagram``, ``gantt``, …)
    parses to ``ok=False`` and the viewer DEGRADES to a styled source panel —
    never a broken screen.
  * node shapes: ``[rect]``, ``(round)``, ``([stadium])``, ``{diamond}``,
    ``((circle))``; unknown shapes fall back to a rect.
  * edges: ``-->`` / ``---`` (solid), ``-.->`` / ``-.-`` (dotted), ``==>`` /
    ``===`` (thick); labels via ``-->|text|`` or the inline ``-- text -->``
    form; chained ``A --> B --> C``.

Everything is drawn with Canvas contract primitives (round-rects, circles,
paths for the diamond, lines for edges via the shared arrow helper).  The
module is theme-free where it can be: the parser is pure (canvas-free, unit
testable) and the renderer takes a theme token table from the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo
from ..widget import Rect
from . import layered_dag as dag

# --------------------------------------------------------------------------- #
# Geometry constants (px)
# --------------------------------------------------------------------------- #

MPAD = 14.0            # inner padding around the diagram within its block
NODE_H = 34.0          # fixed node height in TD (the main-axis slot)
NODE_W_MIN = 74.0
NODE_W_MAX = 210.0
ROW_GAP = 34.0         # between layers (room for an edge label)
COL_GAP = 22.0         # between siblings in a layer
# LR: the main axis is horizontal, so a node's WIDTH is the fixed slot and its
# HEIGHT is the clamped cross extent.
NODE_W_LR = 132.0
NODE_H_MIN = 30.0
NODE_H_MAX = 48.0
LANE_TITLE_H = 20.0    # swimlane title band
LANE_GAP = 10.0        # gutter between lane bands
LABEL_SIZE = 9.5       # edge-label / node-label font
ARROW = 7.0

FONT_UI = "sans-serif"
FONT_MONO = "monospace"

_MAX_LINES = 400       # hard bound: never chew an adversarial block forever


# --------------------------------------------------------------------------- #
# Graph model (canvas-free — testable without a surface)
# --------------------------------------------------------------------------- #

@dataclass
class MermaidNode:
    id: str
    label: str
    shape: str = "rect"     # rect | round | stadium | diamond | circle
    lane: str = ""


@dataclass
class MermaidEdge:
    source: str
    target: str
    label: str = ""
    arrow: bool = True
    style: str = "solid"    # solid | dotted | thick


@dataclass
class MermaidLane:
    id: str
    title: str


@dataclass
class MermaidGraph:
    direction: str = "TD"
    nodes: List[MermaidNode] = field(default_factory=list)
    edges: List[MermaidEdge] = field(default_factory=list)
    lanes: List[MermaidLane] = field(default_factory=list)
    ok: bool = True
    diagram_type: str = "flowchart"
    reason: str = ""

    def node(self, nid: str) -> Optional[MermaidNode]:
        for n in self.nodes:
            if n.id == nid:
                return n
        return None


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

# Header: `graph TD` / `flowchart LR` (direction optional -> TD).
_HEADER_RE = re.compile(r"^(graph|flowchart)(?:\s+(TD|TB|BT|LR|RL))?\b",
                        re.IGNORECASE)

# Ordered shape delimiters — longest openers first so `((` beats `(`.
_SHAPES: List[Tuple[str, str, str]] = [
    ("((", "))", "circle"),
    ("([", "])", "stadium"),
    ("[[", "]]", "rect"),
    ("[(", ")]", "rect"),
    ("{{", "}}", "diamond"),
    ("[", "]", "rect"),
    ("(", ")", "round"),
    ("{", "}", "diamond"),
    (">", "]", "rect"),
]

# A node id: alnum + _ (mermaid ids; '-' is excluded so it can't swallow the
# leading dash of a `-->` connector).
_ID_RE = re.compile(r"[A-Za-z0-9_]+")

# A link operator, anchored at the current scan position.  The INLINE-label
# branch (open run · label · close run) is tried first so `-- text -->` is a
# labelled link, not `--` followed by a node called "text".
_LINK_AT = re.compile(
    r"""\s*
        (?:
            (?P<iopen>-{2,}|={2,}|-\.-)
            \s*(?P<ilabel>[^-=>|<][^>|]*?)\s*
            (?P<iclose>-\.->|-{2,}>|={2,}>|-\.-|-{2,}|={2,})
          |
            (?P<body>-\.->|-\.-|={2,}>|={2,}|-{2,}>|-{2,})
            (?:\|(?P<plabel>[^|]*)\|)?
        )
        \s*""",
    re.VERBOSE)

# Diagram-type tokens we explicitly recognise as OUT of subset (for a helpful
# degrade caption).  Anything non-graph/flowchart degrades regardless.
_KNOWN_TYPES = {
    "sequencediagram": "sequence diagram", "classdiagram": "class diagram",
    "statediagram": "state diagram", "statediagram-v2": "state diagram",
    "erdiagram": "ER diagram", "gantt": "gantt chart", "pie": "pie chart",
    "journey": "user journey", "mindmap": "mindmap", "gitgraph": "git graph",
    "quadrantchart": "quadrant chart", "timeline": "timeline",
    "requirementdiagram": "requirement diagram", "c4context": "C4 diagram",
    "sankey-beta": "sankey diagram", "xychart-beta": "xy chart",
    "block-beta": "block diagram",
}


def _clean_label(raw: str) -> str:
    """Strip surrounding quotes and normalise ``<br>``/whitespace in a label."""
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = s.replace("&nbsp;", " ").replace("&quot;", '"').replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()


def parse_mermaid(src: str) -> MermaidGraph:
    """Parse mermaid *src* into a :class:`MermaidGraph`.

    Returns ``ok=True`` for a supported flowchart/swimlane with >=1 node, else
    ``ok=False`` with ``diagram_type`` + ``reason`` set so the caller can
    degrade to a source panel.  Never raises — a malformed line is skipped."""
    g = MermaidGraph()
    lines = str(src).replace("\r", "").split("\n")
    # -- find the header (first non-blank, non-comment line) -----------------
    idx = 0
    header = None
    for i, ln in enumerate(lines[:_MAX_LINES]):
        s = _strip_comment(ln).strip()
        if not s:
            continue
        header = s
        idx = i + 1
        break
    if header is None:
        return MermaidGraph(ok=False, diagram_type="empty",
                            reason="empty diagram")
    m = _HEADER_RE.match(header)
    if not m:
        first = re.split(r"[\s{]", header, maxsplit=1)[0].lower()
        pretty = _KNOWN_TYPES.get(first, first or "diagram")
        return MermaidGraph(ok=False, diagram_type=pretty,
                            reason="not a flowchart")
    g.direction = (m.group(2) or "TD").upper()
    # the header line may carry a first statement after the direction token
    tail = header[m.end():].strip()
    body: List[str] = ([tail] if tail else []) + lines[idx:_MAX_LINES]

    lane_stack: List[str] = []
    lane_seen: set = set()
    order: Dict[str, int] = {}          # first-seen order for stable sort

    def touch(nid: str, shape: Optional[str], label: Optional[str]) -> None:
        node = g.node(nid)
        lane = lane_stack[-1] if lane_stack else ""
        if node is None:
            node = MermaidNode(id=nid, label=label if label is not None else nid,
                               shape=shape or "rect", lane=lane)
            g.nodes.append(node)
            order[nid] = len(order)
        else:
            if shape:
                node.shape = shape
            if label is not None:
                node.label = label
            if lane and not node.lane:
                node.lane = lane

    for raw in body:
        s = _strip_comment(raw).strip()
        if not s:
            continue
        low = s.lower()
        if low == "end":
            if lane_stack:
                lane_stack.pop()
            continue
        if low.startswith("subgraph"):
            lane_id, lane_title = _parse_subgraph(s)
            if lane_id not in lane_seen:
                lane_seen.add(lane_id)
                g.lanes.append(MermaidLane(id=lane_id, title=lane_title))
            lane_stack.append(lane_id)
            continue
        # skip statements the subset ignores (styling / classes / clicks)
        if re.match(r"(?:style|classDef|class|click|linkStyle|direction)\b",
                    s, re.IGNORECASE):
            continue
        _parse_statement(s, touch, g)

    if not g.nodes:
        return MermaidGraph(ok=False, diagram_type="flowchart",
                            reason="no nodes")
    # stable order for layout determinism
    g.nodes.sort(key=lambda n: order.get(n.id, 1 << 30))
    return g


def _strip_comment(line: str) -> str:
    """Drop a ``%%`` mermaid comment to end-of-line."""
    i = line.find("%%")
    return line if i < 0 else line[:i]


def _parse_subgraph(s: str) -> Tuple[str, str]:
    """``subgraph id [Title]`` or ``subgraph Title`` -> (lane_id, title)."""
    rest = s[len("subgraph"):].strip()
    m = re.match(r"([A-Za-z0-9_.\-]+)\s*\[(.*)\]\s*$", rest)
    if m:
        return m.group(1), _clean_label(m.group(2))
    if not rest:
        return "sg", "sg"
    title = _clean_label(rest)
    return title, title


def _read_noderef(line: str, pos: int,
                  touch) -> Optional[Tuple[str, int]]:
    """Read a node reference (id + optional shape/label) at *pos*.  Registers
    the node via *touch*.  Returns ``(id, new_pos)`` or None."""
    n = len(line)
    while pos < n and line[pos] == " ":
        pos += 1
    m = _ID_RE.match(line, pos)
    if not m:
        return None
    nid = m.group(0)
    pos = m.end()
    shape = None
    label = None
    for open_tok, close_tok, sh in _SHAPES:
        if line.startswith(open_tok, pos):
            close_at = line.find(close_tok, pos + len(open_tok))
            if close_at < 0:
                continue
            label = _clean_label(line[pos + len(open_tok):close_at])
            shape = sh
            pos = close_at + len(close_tok)
            break
    touch(nid, shape, label)
    return nid, pos


def _read_link(line: str, pos: int) -> Optional[Tuple[dict, int]]:
    """Match a link operator at *pos*; returns ``(link, new_pos)`` or None."""
    m = _LINK_AT.match(line, pos)
    if not m or m.end() == pos:
        return None
    if m.group("body") is not None:
        op = m.group("body")
        label = m.group("plabel") or ""
        arrow = op.endswith(">")
    else:
        op = m.group("iopen") + m.group("iclose")
        label = m.group("ilabel") or ""
        arrow = m.group("iclose").endswith(">")
    style = "dotted" if "." in op else ("thick" if "=" in op else "solid")
    return {"label": _clean_label(label), "arrow": arrow, "style": style}, m.end()


def _parse_statement(s: str, touch, g: MermaidGraph) -> None:
    """Parse one flowchart statement: a node definition or a (chained) edge."""
    pos = 0
    first = _read_noderef(s, pos, touch)
    if first is None:
        return
    prev_id, pos = first
    while True:
        link = _read_link(s, pos)
        if link is None:
            break
        ldict, pos = link
        nxt = _read_noderef(s, pos, touch)
        if nxt is None:
            break
        nid, pos = nxt
        g.edges.append(MermaidEdge(
            source=prev_id, target=nid, label=ldict["label"],
            arrow=ldict["arrow"], style=ldict["style"]))
        prev_id = nid


# --------------------------------------------------------------------------- #
# Layout (uses the shared layered-DAG core)
# --------------------------------------------------------------------------- #

@dataclass
class LaneBox:
    id: str
    title: str
    rect: Rect


@dataclass
class MermaidLayout:
    graph: MermaidGraph
    positions: Dict[str, Rect]
    lane_boxes: List[LaneBox]
    width: float
    height: float
    direction: str          # normalized TD | LR


def _acyclic_edges(g: MermaidGraph) -> List[Tuple[str, str]]:
    """The layering edge set with cycle-closing BACK-edges removed (a flowchart
    loop ``D --> B`` should draw as an arrow but must NOT push depth to
    infinity).  A DFS over first-seen node order classifies edges; only edges
    to a node currently on the DFS stack (an ancestor) are dropped.  The loops
    are still DRAWN — this only keeps the shared longest-path layering finite
    and shallow."""
    adj: Dict[str, List[str]] = {}
    for e in g.edges:
        adj.setdefault(e.source, []).append(e.target)
    known = {n.id for n in g.nodes}
    color: Dict[str, int] = {n.id: 0 for n in g.nodes}    # 0 white 1 gray 2 black
    back: set = set()
    for root in [n.id for n in g.nodes]:
        if color[root] != 0:
            continue
        stack = [(root, iter(adj.get(root, [])))]
        color[root] = 1
        while stack:
            node, it = stack[-1]
            pushed = False
            for tgt in it:
                if tgt not in known:
                    continue
                if color[tgt] == 0:
                    color[tgt] = 1
                    stack.append((tgt, iter(adj.get(tgt, []))))
                    pushed = True
                    break
                if color[tgt] == 1:                        # ancestor -> back edge
                    back.add((node, tgt))
            if not pushed:
                color[node] = 2
                stack.pop()
    return [(e.source, e.target) for e in g.edges
            if (e.source, e.target) not in back]


def _levels(g: MermaidGraph) -> Dict[str, int]:
    return dag.assign_levels([n.id for n in g.nodes], _acyclic_edges(g))


def _row_buckets(g: MermaidGraph, level: Dict[str, int]
                 ) -> List[List[MermaidNode]]:
    n_levels = (max(level.values()) + 1) if level else 1
    rows: List[List[MermaidNode]] = [[] for _ in range(n_levels)]
    for n in g.nodes:                       # nodes already in first-seen order
        rows[level.get(n.id, 0)].append(n)
    return rows


def measure_mermaid(canvas: Any, graph: MermaidGraph,
                    content_w: float) -> MermaidLayout:
    """Lay the flowchart out within *content_w*; returns positions + the block
    height the markdown viewer reserves.  A swimlane runs the shared
    ``layout_grid`` once PER lane band (nodes aligned on the shared main axis).

    The MAIN axis (levels) always fits the box: in ``TD`` the fixed node height
    stacks levels down; in ``LR`` the node WIDTH shrinks so all levels fit the
    column width.  The CROSS axis (siblings, and lane bands for a swimlane) is
    what sets the block's other dimension."""
    orient = dag.normalize_direction(graph.direction)
    rows = _row_buckets(graph, _levels(graph))
    n_levels = len(rows)
    lanes = _effective_lanes(graph)
    width = content_w
    inner_w = width - 2 * MPAD

    if orient == "TD":
        cell_main, gap_main = NODE_H, ROW_GAP
        cross_min, cross_max, gap_cross = NODE_W_MIN, NODE_W_MAX, COL_GAP
    else:
        # fit every level across the column: shrink node width if needed.
        cell_main = min(NODE_W_LR,
                        max(NODE_W_MIN,
                            (inner_w - (n_levels - 1) * COL_GAP) / n_levels))
        gap_main = COL_GAP
        cross_min, cross_max, gap_cross = NODE_H_MIN, NODE_H_MAX, 14.0

    block_main = n_levels * cell_main + (n_levels - 1) * gap_main
    lane_boxes: List[LaneBox] = []
    positions: Dict[str, Rect] = {}

    # -- plain flowchart (no subgraphs) -------------------------------------
    if not lanes:
        if orient == "TD":
            height = block_main + 2 * MPAD
            area = Rect(MPAD, MPAD, inner_w, block_main)
        else:
            max_in_level = max((len(r) for r in rows), default=1)
            cross_block = (max_in_level * cross_max
                           + (max_in_level - 1) * gap_cross)
            height = cross_block + 2 * MPAD
            area = Rect(MPAD, MPAD, block_main, cross_block)
        positions = dag.layout_grid(
            rows, area, orientation=orient,
            cell_main=cell_main, gap_main=gap_main,
            cross_min=cross_min, cross_max=cross_max, gap_cross=gap_cross,
            id_of=lambda n: n.id)
        return MermaidLayout(graph, positions, lane_boxes, width, height, orient)

    # -- swimlanes: split the CROSS axis into lane bands --------------------
    lanes_list = lanes
    nlanes = len(lanes_list)
    by_lane: Dict[str, Dict[int, List[MermaidNode]]] = {
        lid: {} for lid, _ in lanes_list}
    for lvl, row in enumerate(rows):
        for node in row:
            lid = node.lane if node.lane in by_lane else lanes_list[0][0]
            by_lane[lid].setdefault(lvl, []).append(node)

    def lane_cross_count(lid: str) -> int:
        return max((len(v) for v in by_lane[lid].values()), default=1)

    if orient == "TD":
        # lanes are vertical bands (split X); title strip on top.
        height = block_main + 2 * MPAD + LANE_TITLE_H
        band_w = (inner_w - (nlanes - 1) * LANE_GAP) / nlanes
        for i, (lid, title) in enumerate(lanes_list):
            bx = MPAD + i * (band_w + LANE_GAP)
            band = Rect(bx, MPAD + LANE_TITLE_H, band_w, block_main)
            lane_rows = [by_lane[lid].get(k, []) for k in range(n_levels)]
            positions.update(dag.layout_grid(
                lane_rows, band, orientation="TD",
                cell_main=cell_main, gap_main=gap_main,
                cross_min=min(cross_min, band_w - gap_cross),
                cross_max=min(cross_max, band_w - gap_cross),
                gap_cross=gap_cross, center_main=False, id_of=lambda n: n.id))
            lane_boxes.append(LaneBox(lid, title,
                                      Rect(bx, MPAD, band_w,
                                           block_main + LANE_TITLE_H)))
    else:
        # lanes are horizontal bands (split Y); each holds a full LR layout.
        band_hs = [lane_cross_count(lid) * cross_max
                   + (lane_cross_count(lid) - 1) * gap_cross + LANE_TITLE_H
                   for lid, _ in lanes_list]
        height = sum(band_hs) + (nlanes - 1) * LANE_GAP + 2 * MPAD
        by = MPAD
        for i, (lid, title) in enumerate(lanes_list):
            bh = band_hs[i]
            band = Rect(MPAD, by + LANE_TITLE_H, block_main, bh - LANE_TITLE_H)
            lane_rows = [by_lane[lid].get(k, []) for k in range(n_levels)]
            positions.update(dag.layout_grid(
                lane_rows, band, orientation="LR",
                cell_main=cell_main, gap_main=gap_main,
                cross_min=cross_min,
                cross_max=min(cross_max, band.h - gap_cross),
                gap_cross=gap_cross, center_main=True, id_of=lambda n: n.id))
            lane_boxes.append(LaneBox(lid, title, Rect(MPAD, by, block_main, bh)))
            by += bh + LANE_GAP

    return MermaidLayout(graph, positions, lane_boxes, width, height, orient)


def _effective_lanes(graph: MermaidGraph) -> List[Tuple[str, str]]:
    """The lane bands to render: the declared subgraphs, plus an implicit
    ``(other)`` band if any node sits outside every subgraph.  Returns [] when
    there are no subgraphs at all (the plain-flowchart path)."""
    if not graph.lanes:
        return []
    lanes = [(lz.id, lz.title) for lz in graph.lanes]
    lane_ids = {lz.id for lz in graph.lanes}
    if any((n.lane or "") not in lane_ids for n in graph.nodes):
        lanes.append(("", "other"))
    return lanes


# --------------------------------------------------------------------------- #
# Draw
# --------------------------------------------------------------------------- #

def _edge_color(t, style: str):
    if style == "thick":
        return t.accent
    if style == "dotted":
        return t.faint
    return t.dim


def _edge_width(style: str) -> float:
    return 2.2 if style == "thick" else 1.3


def draw_mermaid(canvas: Canvas, layout: MermaidLayout, ox: float, oy: float,
                 t) -> None:
    """Draw *layout* at document offset ``(ox, oy)`` with theme tokens *t*
    (a ``console_theme`` table)."""
    pos = {nid: Rect(r.x + ox, r.y + oy, r.w, r.h)
           for nid, r in layout.positions.items()}

    # lane bands first (behind everything)
    for lb in layout.lane_boxes:
        r = Rect(lb.rect.x + ox, lb.rect.y + oy, lb.rect.w, lb.rect.h)
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, 8.0)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, 8.0)
        canvas.set_font(FONT_MONO, 8.6, "bold")
        m = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(_ellipsize(canvas, lb.title.upper(), r.w - 12),
                         r.x + 7, r.y + 6 + m.ascent, "left")

    # edges behind nodes
    for e in layout.graph.edges:
        a = pos.get(e.source)
        b = pos.get(e.target)
        if a is None or b is None:
            continue
        color = _edge_color(t, e.style)
        p1, p2 = dag.draw_arrow_edge(canvas, a, b, color, _edge_width(e.style),
                                     arrow=ARROW, arrowhead=e.arrow)
        if e.label:
            mx, my = (p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0
            _draw_edge_label(canvas, e.label, mx, my, t)

    # nodes
    for n in layout.graph.nodes:
        r = pos.get(n.id)
        if r is not None:
            _draw_node(canvas, n, r, t)


def _draw_node(canvas: Canvas, n: MermaidNode, r: Rect, t) -> None:
    canvas.set_fill_color(*t.panel2)
    canvas.set_stroke_color(*t.accent_line)
    canvas.set_stroke_width(1.3)
    shape = n.shape
    if shape == "circle":
        cx, cy = r.x + r.w / 2.0, r.y + r.h / 2.0
        rad = min(r.w, r.h) / 2.0
        canvas.fill_circle(cx, cy, rad)
        canvas.stroke_circle(cx, cy, rad)
    elif shape == "diamond":
        cx, cy = r.x + r.w / 2.0, r.y + r.h / 2.0
        cmds = [MoveTo(cx, r.y), LineTo(r.x + r.w, cy),
                LineTo(cx, r.y + r.h), LineTo(r.x, cy), Close()]
        canvas.fill_path(cmds)
        canvas.draw_path(cmds)
    elif shape in ("round", "stadium"):
        rad = r.h / 2.0 if shape == "stadium" else 10.0
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, rad)
        canvas.stroke_round_rect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, rad)
    else:  # rect
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, 4.0)
        canvas.stroke_round_rect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, 4.0)

    # centered label
    canvas.set_font(FONT_UI, LABEL_SIZE, "regular")
    m = canvas.measure_text("Ag")
    canvas.set_fill_color(*t.ink)
    label = _ellipsize(canvas, n.label, r.w - 12)
    canvas.draw_text(label, r.x + r.w / 2.0,
                     r.y + (r.h - (m.ascent + m.descent)) / 2.0 + m.ascent,
                     "center")


def _draw_edge_label(canvas: Canvas, text: str, cx: float, cy: float,
                     t) -> None:
    canvas.set_font(FONT_MONO, 8.4, "regular")
    m = canvas.measure_text(text)
    pad = 3.0
    bw = m.width + 2 * pad
    bh = m.ascent + m.descent + 2 * pad
    canvas.set_fill_color(*t.panel)
    canvas.fill_round_rect(cx - bw / 2.0, cy - bh / 2.0, bw, bh, 3.0)
    canvas.set_fill_color(*t.dim)
    canvas.draw_text(text, cx, cy - bh / 2.0 + pad + m.ascent, "center")


def _ellipsize(canvas: Canvas, text: str, max_w: float) -> str:
    """Trim *text* to *max_w* with a trailing '…' (measured in the current
    font).  Local copy of the console idiom so this module has no console dep."""
    if max_w <= 0:
        return ""
    if canvas.measure_text(text).width <= max_w:
        return text
    ell = "…"
    ew = canvas.measure_text(ell).width
    budget = max_w - ew
    if budget <= 0:
        return ell
    cur = ""
    for ch in text:
        if canvas.measure_text(cur + ch).width > budget:
            break
        cur += ch
    return cur + ell
