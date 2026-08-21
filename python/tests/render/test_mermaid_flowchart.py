"""
[CXC-R-MERMAID] Mermaid-subset Canvas renderer — compliance tests.

Three tiers, all headless:

  * the PARSER (mermaid subset -> graph model): shapes, edge styles, labels,
    chained edges, direction, swimlanes, cycle-safety, and the degrade
    classification (unsupported type -> ok=False);
  * the LAYOUT (the SHARED layered-DAG core): levels, grid placement, node
    fit within the block, swimlane lane bands — plus the grep-provable
    single-implementation invariant ([CXC-R-MERMAID-SHARED]);
  * the RENDER inside the markdown viewer: a ```mermaid fence becomes a drawn
    diagram block, an out-of-subset type degrades to a styled source panel
    ([CXC-R-MERMAID-DEGRADE]), and a mixed markdown+mermaid doc paints — a
    seeded full render is written to a PNG for a human glass-check.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import MarkdownViewerWidget
from composeio.render.widgets import mermaid_flowchart as mf
from composeio.render.widgets import markdown_viewer as mdv
from composeio.render.widgets import layered_dag as dag
from composeio.render.widgets import console_theme as ct

from .conftest import surface_to_array


FLOW = """graph TD
  A[Start] --> B{Ready?}
  B -->|yes| C[Go]
  B -->|no| D(Wait)
  C --> E((Done))
  D --> B
"""

SWIM = """flowchart LR
  subgraph client [Client]
    A[Browser] --> B[Cache]
  end
  subgraph server [Server]
    C[API] --> D(Store)
  end
  B --> C
"""


def _canvas(w=560, h=520):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x07, 0x0A, 0x0F)
    c.fill_rect(0, 0, w, h)
    return c


# ── parser: nodes, shapes, labels ───────────────────────────────────────────

def test_parse_flowchart_header_and_direction():
    assert mf.parse_mermaid("graph TD\n A-->B").direction == "TD"
    assert mf.parse_mermaid("flowchart LR\n A-->B").direction == "LR"
    # bare `graph` (no direction) defaults to TD
    assert mf.parse_mermaid("graph\n A-->B").direction == "TD"


def test_parse_node_shapes():
    g = mf.parse_mermaid(
        "graph TD\n A[rect]\n B(round)\n C([stad])\n D{dia}\n E((cir))")
    shapes = {n.id: n.shape for n in g.nodes}
    assert shapes == {"A": "rect", "B": "round", "C": "stadium",
                      "D": "diamond", "E": "circle"}


def test_parse_labels_default_to_id_and_strip_quotes():
    g = mf.parse_mermaid('graph TD\n A["Quoted <br> Label"] --> B')
    a = g.node("A")
    b = g.node("B")
    assert a.label == "Quoted Label"      # <br> -> space, quotes stripped
    assert b.label == "B"                 # bare ref -> id is the label


def test_parse_glued_edge_id_not_swallowing_dash():
    # `B-->C` (no spaces): the id must be `B`, not `B--` — the '-' can't be
    # part of an id or it eats the connector's leading dash.
    g = mf.parse_mermaid("graph TD\n A-->B\n B-->|x|C")
    assert {n.id for n in g.nodes} == {"A", "B", "C"}


# ── parser: edges (styles, arrows, labels, chains) ──────────────────────────

def test_parse_edge_styles_and_arrowheads():
    g = mf.parse_mermaid("graph LR\n A-->B\n A---C\n A-.->D\n A==>E\n A===F")
    by = {(e.source, e.target): e for e in g.edges}
    assert by[("A", "B")].style == "solid" and by[("A", "B")].arrow
    assert by[("A", "C")].style == "solid" and not by[("A", "C")].arrow
    assert by[("A", "D")].style == "dotted" and by[("A", "D")].arrow
    assert by[("A", "E")].style == "thick" and by[("A", "E")].arrow
    assert by[("A", "F")].style == "thick" and not by[("A", "F")].arrow


def test_parse_pipe_and_inline_labels():
    g = mf.parse_mermaid("graph TD\n A -->|pipe| B\n A -- inline --> C")
    labels = {(e.source, e.target): e.label for e in g.edges}
    assert labels[("A", "B")] == "pipe"
    assert labels[("A", "C")] == "inline"


def test_parse_chained_edges():
    g = mf.parse_mermaid("graph LR\n A --> B --> C --> D")
    assert [(e.source, e.target) for e in g.edges] == \
        [("A", "B"), ("B", "C"), ("C", "D")]


def test_parse_strips_comments_and_ignores_style_lines():
    g = mf.parse_mermaid(
        "graph TD  %% a comment\n A-->B %% trailing\n style A fill:#f00\n"
        " classDef big font-size:20px")
    assert g.ok
    assert [(e.source, e.target) for e in g.edges] == [("A", "B")]


# ── parser: degrade classification ([CXC-R-MERMAID-DEGRADE]) ────────────────

def test_unsupported_diagram_types_degrade():
    for src, pretty in [
        ("sequenceDiagram\n A->>B: hi", "sequence diagram"),
        ("classDiagram\n A <|-- B", "class diagram"),
        ("stateDiagram-v2\n [*] --> S", "state diagram"),
        ("gantt\n title x", "gantt chart"),
        ("pie\n \"a\" : 10", "pie chart"),
    ]:
        g = mf.parse_mermaid(src)
        assert not g.ok, src
        assert g.diagram_type == pretty, (src, g.diagram_type)


def test_empty_and_nodeless_degrade():
    assert not mf.parse_mermaid("").ok
    assert not mf.parse_mermaid("   \n  ").ok
    # a flowchart header with no parseable nodes degrades too
    assert not mf.parse_mermaid("graph TD\n").ok


# ── parser: swimlanes + cycle safety ────────────────────────────────────────

def test_parse_swimlane_lanes_and_membership():
    g = mf.parse_mermaid(SWIM)
    assert g.ok
    assert [(l.id, l.title) for l in g.lanes] == \
        [("client", "Client"), ("server", "Server")]
    lane = {n.id: n.lane for n in g.nodes}
    assert lane == {"A": "client", "B": "client",
                    "C": "server", "D": "server"}


def test_cycle_back_edge_does_not_inflate_levels():
    # A->B->C->A is a cycle; layering depth must stay bounded (<= n nodes),
    # and the loop edge is still present to be drawn.
    g = mf.parse_mermaid("graph TD\n A-->B\n B-->C\n C-->A")
    level = mf._levels(g)
    assert max(level.values()) < len(g.nodes)
    assert ("C", "A") in {(e.source, e.target) for e in g.edges}


# ── the SHARED layered-DAG core ─────────────────────────────────────────────

def test_assign_levels_longest_path():
    # A->B->D and A->C->D: D sits at the LONGEST path depth (2), below B/C (1).
    lvl = dag.assign_levels(
        ["A", "B", "C", "D"],
        [("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")])
    assert lvl["A"] == 0
    assert lvl["B"] == 1 and lvl["C"] == 1
    assert lvl["D"] == 2


def test_layout_grid_td_fixed_height_clamped_width():
    rows = [["A"], ["B", "C"]]
    area = Rect(0, 0, 400, 200)
    pos = dag.layout_grid(rows, area, orientation="TD",
                          cell_main=40, gap_main=30,
                          cross_min=80, cross_max=180, gap_cross=20)
    assert pos["A"].h == 40 and pos["B"].h == 40      # fixed height
    assert 80 <= pos["B"].w <= 180                    # clamped width
    assert pos["B"].y == pos["C"].y                   # same row
    assert pos["A"].y < pos["B"].y                    # A is a layer above


def test_layout_grid_lr_fixed_width_clamped_height():
    rows = [["A"], ["B"]]
    area = Rect(0, 0, 400, 200)
    pos = dag.layout_grid(rows, area, orientation="LR",
                          cell_main=120, gap_main=20,
                          cross_min=30, cross_max=48, gap_cross=14)
    assert pos["A"].w == 120 and pos["B"].w == 120    # fixed width (main)
    assert pos["A"].x < pos["B"].x                    # levels advance in x


def test_edge_point_lands_on_border():
    box = Rect(0, 0, 100, 40)
    # ray toward a point far to the right exits the right edge (x == 100)
    px, py = dag.edge_point(box, 1000, 20)
    assert abs(px - 100.0) < 1e-6
    assert 0 <= py <= 40


# ── measure: block sizing + node fit ────────────────────────────────────────

def test_measure_flowchart_positions_all_nodes_within_block():
    g = mf.parse_mermaid(FLOW)
    c = _canvas()
    lay = mf.measure_mermaid(c, g, 520.0)
    assert lay.height > 0
    assert set(lay.positions) == {n.id for n in g.nodes}
    for r in lay.positions.values():
        assert 0 <= r.x and r.x + r.w <= lay.width + 1.0
        assert 0 <= r.y and r.y + r.h <= lay.height + 1.0


def test_measure_swimlane_has_lane_bands_and_nodes_fit_width():
    g = mf.parse_mermaid(SWIM)
    c = _canvas()
    lay = mf.measure_mermaid(c, g, 520.0)
    assert [lb.id for lb in lay.lane_boxes] == ["client", "server"]
    # LR nodes must not overflow the content width (the fit-to-column rule)
    for r in lay.positions.values():
        assert r.x + r.w <= lay.width + 1.0
    # each node sits inside its own lane band on the cross (y) axis
    band = {lb.id: lb.rect for lb in lay.lane_boxes}
    for n in g.nodes:
        r = lay.positions[n.id]
        b = band[n.lane]
        cy = r.y + r.h / 2.0
        assert b.y - 1 <= cy <= b.y + b.h + 1, n.id


# ── the single-implementation invariant ([CXC-R-MERMAID-SHARED]) ────────────

def test_layered_dag_core_is_the_single_implementation():
    import inspect
    dg = inspect.getsource(
        __import__("composeio.render.widgets.decisions_graph",
                   fromlist=["x"]))
    mfsrc = inspect.getsource(mf)
    # both consumers import the shared core...
    assert "import layered_dag" in dg
    assert "import layered_dag" in mfsrc
    # ...and neither re-defines the shared geometry.
    for src in (dg, mfsrc):
        assert "def assign_levels" not in src
        assert "def layout_grid" not in src
        assert "def edge_point" not in src
        assert "def draw_arrow_edge" not in src
    # the core defines each exactly once.
    core = inspect.getsource(dag)
    for name in ("def assign_levels", "def layout_grid",
                 "def edge_point", "def draw_arrow_edge"):
        assert core.count(name) == 1, name


# ── render inside the markdown viewer ───────────────────────────────────────

def test_markdown_fence_becomes_mermaid_block():
    blocks = mdv.parse_markdown_blocks(
        "text\n\n```mermaid\ngraph TD\n A-->B\n```\n\n```python\nx=1\n```")
    kinds = [b.kind for b in blocks]
    assert "mermaid" in kinds
    assert "code" in kinds                     # non-mermaid fences stay code
    merm = next(b for b in blocks if b.kind == "mermaid")
    assert merm.lines[0] == "graph TD"


def _viewer(doc, w=560, h=640):
    v = MarkdownViewerWidget("md", content=doc, font_size=14.0)
    v.on_mount(None)
    c = _canvas(w, h)
    v.measure(Constraints(w, h))
    v.draw(c, Rect(8, 8, w - 16, h - 16))
    return v, c


def test_flowchart_renders_and_paints_edges():
    lines = {"n": 0}

    class _Rec(SkiaCanvas):
        def draw_line(self, *a, **k):
            lines["n"] += 1
            return super().draw_line(*a, **k)

    v = MarkdownViewerWidget("md",
                             content="```mermaid\n" + FLOW + "```",
                             font_size=14.0)
    v.on_mount(None)
    c = _Rec()
    c.create_surface(560, 480)
    v.measure(Constraints(560, 480))
    v.draw(c, Rect(8, 8, 544, 464))
    # edges drawn as shaft + 2 arrowhead barbs => many draw_line calls
    assert lines["n"] >= 3 * len(mf.parse_mermaid(FLOW).edges) - 3
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_unsupported_degrades_to_source_panel_text():
    doc = "```mermaid\nsequenceDiagram\n  A->>B: hi\n  B-->>A: yo\n```"
    drawn = []

    class _Rec(SkiaCanvas):
        def draw_text(self, s, *a, **k):
            drawn.append(s)
            return super().draw_text(s, *a, **k)

    v = MarkdownViewerWidget("md", content=doc, font_size=14.0)
    v.on_mount(None)
    c = _Rec()
    c.create_surface(520, 260)
    v.measure(Constraints(520, 260))
    v.draw(c, Rect(8, 8, 504, 244))
    joined = " ".join(drawn)
    assert "mermaid" in joined                 # the panel caption
    assert "sequence diagram" in joined        # the classified type
    assert "sequenceDiagram" in joined         # the verbatim source line


def test_mixed_doc_lays_out_taller_than_prose_only():
    prose = "# Title\n\nSome prose only.\n"
    mixed = prose + "\n```mermaid\n" + FLOW + "```\n"
    a, _ = _viewer(prose)
    b, _ = _viewer(mixed)
    assert b.content_height > a.content_height


# ── seeded PNG for a human glass-check ──────────────────────────────────────

def test_seeded_mermaid_render_png(tmp_path):
    doc = ("# Deployment Flow\n\nThe boot sequence:\n\n```mermaid\n" + FLOW
           + "```\n\nAcross client and server:\n\n```mermaid\n" + SWIM
           + "```\n\nAn unsupported type:\n\n```mermaid\nsequenceDiagram\n"
             "  David->>Architect: emit\n  Architect-->>Widget: render\n```\n"
             "\nClosing prose.\n")
    v, c = _viewer(doc, w=600, h=940)
    assert surface_to_array(c)[..., :3].std() > 3.0
    out = tmp_path / "mermaid_markdown.png"
    c.save_png(str(out))
    assert out.exists()
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    c.save_png(os.path.join(cap_dir, "mermaid_markdown.png"))
