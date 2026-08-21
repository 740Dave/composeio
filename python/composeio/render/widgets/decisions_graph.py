"""
ComposeUX DecisionsGraphWidget — the Night Watch window's DECISIONS tab
(first-cut viz for [SPEC:CXC-DECISIONS-TAB] / [SPEC:CXC-DT-*], David 2026-07-22).

The Decisions tab renders a QUERY NEIGHBORHOOD of the project's decision graph —
NEVER the whole 70+-node hairball ([CXC-DT-QUERY-DRIVEN]).  The data source
(``compose_projects/intelligence/tools/decisions_publisher.py``) MAGMA-`traverse`s the neighborhood
of an anchor decision over the SEMANTIC edge alphabet and publishes a widget-ready
projection to ``self/decisions``; this leaf renders that projection verbatim (the
perceive/present split — it never re-queries MAGMA itself).

EDGE SEMANTICS made legible ([CXC-DT-EDGE-SEMANTICS]) — the value is legibility,
not drawing every line:

  * **SUPERSEDES** — the superseded node DIMS + STRIKES THROUGH, with a single
    arrow to its heir ("this was replaced" reads at a glance; David's
    live-supersession point — a decision made overnight then superseded displays
    AS superseded because the projection carries the SUPERSEDES edge).
  * **DEPENDS_ON** — a directed LAYERED edge; nodes are laid out by dependency
    depth (a DAG reads far better than force-directed), the dependency BELOW its
    dependent, arrow pointing down to the foundation.
  * **RESOLVES** — a faint secondary directed edge (the small remainder of the
    alphabet); rendered like DEPENDS_ON but muted.

DATA-DRIVEN off ONE namespace (default ``self/decisions``); a static
``nodes:``/``edges:`` projection in the scene spec is the RENDER DEFAULT (like a
``label``'s ``text`` default), so the window draws a real neighborhood standalone
and a live publisher overrides it on the bus.

Tapping a node publishes ``anchor_namespace`` (default ``_app/decisions/anchor``)
with that node's id — the query-driven RE-ANCHOR seam: a publisher can re-traverse
around the tapped node so the neighborhood is explorable (the query is the frame).

VIZ METAPHOR IS AN OPEN QUESTION ([CXC-DT-OPEN]) — this is the Architect's
documented lean (query-driven + edge-semantic, supersedes-first-class), NOT the
final answer; flagged for David's glass.

Damage discipline ([CXC-R-DAMAGE]): every bus update / interaction calls
``self.request_redraw()`` ONLY — damage stays this widget's own rect.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import layered_dag as dag

DEFAULT_NAMESPACE = "self/decisions"
DEFAULT_ANCHOR_NAMESPACE = "_app/decisions/anchor"

# The meaningful edge alphabet this first-cut renders (CONTAINS is deliberately
# excluded — in this corpus every decision is CONTAINS-ed by one section hub, so
# drawing it reproduces the hairball [CXC-DT-QUERY-DRIVEN] warns against; the
# publisher likewise traverses SEMANTIC edges only).  Kept as a set so an
# unknown edge type degrades to the faint secondary treatment, never a crash.
_SUPERSEDES = "SUPERSEDES"
_DEPENDS_ON = "DEPENDS_ON"

# geometry (px)
PAD = 14.0
HEADER_H = 26.0
LEGEND_H = 18.0
NODE_H = 46.0
NODE_MIN_W = 116.0
NODE_MAX_W = 210.0
COL_GAP = 18.0
ROW_GAP = 30.0
ARROW = 7.0


def _norm_lifecycle(v: Any) -> str:
    """The lifecycle-position token, stripped of any trailing inline `# ...`
    comment the parser kept (`superseded   # applies to ...` -> `superseded`)."""
    return str(v or "").split("#", 1)[0].strip().lower()


class DecisionsGraphWidget(Widget):
    """The query-neighborhood decision-graph viz (Night Watch Decisions tab)."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 anchor_namespace: str = DEFAULT_ANCHOR_NAMESPACE,
                 nodes: Optional[List[Any]] = None,
                 edges: Optional[List[Any]] = None,
                 anchor: str = "",
                 corpus: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "decisions_graph", **kwargs)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.anchor_namespace = str(anchor_namespace or DEFAULT_ANCHOR_NAMESPACE)
        self.interactive = True
        self._nodes: List[Dict[str, Any]] = self._coerce_nodes(nodes)
        self._edges: List[Dict[str, str]] = self._coerce_edges(edges)
        self._anchor = str(anchor or "")
        self._corpus = str(corpus or "")
        # hit geometry recorded at draw: (rect, node_id)
        self._node_zones: List[Tuple[Rect, str]] = []

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = value
        if isinstance(payload, str):
            s = payload.strip()
            if not s or s[0] not in "{[":
                return
            try:
                payload = json.loads(s)
            except (ValueError, TypeError):
                return
        if not isinstance(payload, dict):
            return
        self._nodes = self._coerce_nodes(payload.get("nodes"))
        self._edges = self._coerce_edges(payload.get("edges"))
        self._anchor = str(payload.get("anchor") or self._anchor)
        self._corpus = str(payload.get("corpus") or self._corpus)
        self.request_redraw()                       # [CXC-R-DAMAGE] scoped

    @staticmethod
    def _coerce_nodes(value: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(value, list):
            return out
        for it in value:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("id") or "").strip()
            if not nid:
                continue
            lifecycle = _norm_lifecycle(it.get("lifecycle")
                                        or it.get("lifecycle_position"))
            superseded = bool(it.get("superseded")) or lifecycle == "superseded"
            out.append({
                "id": nid,
                "title": str(it.get("title") or ""),
                "lifecycle": lifecycle,
                "superseded": superseded,
                "is_anchor": bool(it.get("is_anchor")),
            })
        return out

    @staticmethod
    def _coerce_edges(value: Any) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen = set()
        if not isinstance(value, list):
            return out
        for it in value:
            if not isinstance(it, dict):
                continue
            src = str(it.get("source") or "").strip()
            tgt = str(it.get("target") or "").strip()
            etype = str(it.get("type") or "").strip().upper()
            if not src or not tgt:
                continue
            key = (etype, src, tgt)
            if key in seen:                         # traverse emits dupes
                continue
            seen.add(key)
            out.append({"type": etype, "source": src, "target": tgt})
        return out

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        for r, nid in self._node_zones:
            if r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h:
                # query-driven re-anchor: a publisher re-traverses around nid.
                self.publish(self.anchor_namespace, {"anchor": nid,
                                                     "corpus": self._corpus})
                return

    # -- layout ----------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- layered layout (DAG by dependency depth) ------------------------------

    def _levels(self) -> Dict[str, int]:
        """Row assignment: DEPENDS_ON pushes a dependency BELOW its dependent
        (longest-path over DEPENDS_ON); a SUPERSEDES pair shares a row so the
        heir↔superseded arrow reads horizontally.  Bounded iteration — a cycle
        (never expected in a decision DAG) degrades to a stable partial order,
        never a hang."""
        ids = {n["id"] for n in self._nodes}
        deps = [(e["source"], e["target"]) for e in self._edges
                if e["type"] == _DEPENDS_ON]
        # longest-path layering is the SHARED layered-DAG core (one impl,
        # reused by the mermaid flowchart renderer) — dependency below dependent.
        level = dag.assign_levels(ids, deps)
        # align each SUPERSEDES pair onto the heir's row (side-by-side)
        for e in self._edges:
            if e["type"] == _SUPERSEDES and e["source"] in ids \
                    and e["target"] in ids:
                level[e["target"]] = level[e["source"]]
        return level

    def _rows(self, level: Dict[str, int]) -> List[List[Dict[str, Any]]]:
        by_lvl: Dict[int, List[Dict[str, Any]]] = {}
        for n in self._nodes:
            by_lvl.setdefault(level.get(n["id"], 0), []).append(n)
        # heirs immediately left of the node they supersede (pair reads L->R)
        heir_of = {e["target"]: e["source"] for e in self._edges
                   if e["type"] == _SUPERSEDES}
        rows: List[List[Dict[str, Any]]] = []
        for lvl in sorted(by_lvl):
            row = by_lvl[lvl]

            def sort_key(n: Dict[str, Any]) -> Tuple[int, str]:
                # anchor first, then a superseded node just after its heir
                if n["is_anchor"]:
                    return (0, n["id"])
                heir = heir_of.get(n["id"])
                if heir is not None:
                    return (1, heir + "~z")     # sort right after the heir
                return (1, n["id"])
            rows.append(sorted(row, key=sort_key))
        return rows

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._node_zones = []
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)

        # header: the neighborhood anchor + a compact edge legend
        self._draw_header(canvas, rect, t)

        if not self._nodes:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            canvas.draw_text("awaiting self/decisions…",
                             rect.x + rect.w / 2,
                             rect.y + rect.h / 2, "center")
            canvas.restore()
            return

        level = self._levels()
        rows = self._rows(level)

        # position every node in a centered grid of rows/columns — the SHARED
        # layered-DAG placement (TD, fixed node height, clamped width).
        top = rect.y + PAD + HEADER_H + LEGEND_H
        area = Rect(rect.x + PAD, top, rect.w - 2 * PAD,
                    rect.y + rect.h - PAD - top)
        pos: Dict[str, Rect] = dag.layout_grid(
            rows, area, orientation="TD",
            cell_main=NODE_H, gap_main=ROW_GAP,
            cross_min=NODE_MIN_W, cross_max=NODE_MAX_W, gap_cross=COL_GAP,
            id_of=lambda n: n["id"])

        # edges BEHIND nodes
        for e in self._edges:
            a = pos.get(e["source"])
            b = pos.get(e["target"])
            if a is None or b is None:
                continue
            self._draw_edge(canvas, e["type"], a, b, t)

        # nodes
        for n in self._nodes:
            r = pos.get(n["id"])
            if r is None:
                continue
            self._draw_node(canvas, n, r, t)
            self._node_zones.append((r, n["id"]))

        canvas.restore()

    # -- header + legend -------------------------------------------------------

    def _draw_header(self, canvas: Canvas, rect: Rect, t) -> None:
        hx = rect.x + PAD
        hy = rect.y + PAD
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        m = canvas.measure_text("Ag")
        base = hy + m.ascent
        title = "DECISION NEIGHBORHOOD"
        tw = ct.draw_tracked(canvas, title, hx, base, ct.TRACK_LABEL, t.ink)
        if self._anchor:
            canvas.set_font(ct.FONT_MONO, ct.NS_TAG, "regular")
            canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2],
                                  ct.NS_TAG_ALPHA)
            canvas.draw_text(ct.ellipsize(canvas, self._anchor,
                                          rect.w - 2 * PAD - tw - 10),
                             rect.x + rect.w - PAD, base, "right")
        # legend row: supersedes (strike + heir arrow) · depends_on (down arrow)
        ly = hy + HEADER_H
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        lm = canvas.measure_text("Ag")
        lb = ly + lm.ascent
        lx = hx
        # supersedes swatch
        canvas.set_stroke_color(*t.warn)
        canvas.set_stroke_width(2.0)
        canvas.draw_line(lx, lb - 3, lx + 16, lb - 3)
        canvas.set_fill_color(*t.warn)
        canvas.draw_text("supersedes", lx + 22, lb, "left")
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        lx += 22 + canvas.measure_text("supersedes").width + 18
        # depends_on swatch
        canvas.set_stroke_color(*t.dim)
        canvas.set_stroke_width(1.4)
        canvas.draw_line(lx, lb - 3, lx + 16, lb - 3)
        canvas.set_fill_color(*t.dim)
        canvas.draw_text("depends on", lx + 22, lb, "left")

    # -- one node --------------------------------------------------------------

    def _draw_node(self, canvas: Canvas, n: Dict[str, Any], r: Rect, t) -> None:
        superseded = n["superseded"]
        anchor = n["is_anchor"]
        # box fill + border
        canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, 8.0)
        if anchor:
            border, bw = t.accent, 1.6
        elif superseded:
            border, bw = t.line2, 1.0
        else:
            border, bw = t.line, 1.0
        canvas.set_stroke_color(*border)
        canvas.set_stroke_width(bw)
        canvas.stroke_round_rect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, 8.0)

        ink = t.faint if superseded else t.ink
        idc = t.faint if superseded else t.accent

        # line 1: the DEC id (mono, accent unless superseded)
        canvas.set_font(ct.FONT_MONO, 9.0, "bold")
        m1 = canvas.measure_text("Ag")
        idr = ct.ellipsize(canvas, n["id"], r.w - 16)
        canvas.set_fill_color(*idc)
        canvas.draw_text(idr, r.x + 8, r.y + 6 + m1.ascent, "left")

        # line 2: a short title (UI, ellipsized)
        canvas.set_font(ct.FONT_UI, 11.0, "regular")
        m2 = canvas.measure_text("Ag")
        ttl = ct.ellipsize(canvas, n["title"], r.w - 16)
        canvas.set_fill_color(*ink)
        canvas.draw_text(ttl, r.x + 8, r.y + 24 + m2.ascent, "left")

        if superseded:
            # STRIKETHROUGH across the box + a SUPERSEDED tag ([CXC-DT-EDGE-SEMANTICS])
            cy = r.y + r.h / 2.0
            canvas.set_stroke_color(*t.faint)
            canvas.set_stroke_width(1.2)
            canvas.draw_line(r.x + 6, cy, r.x + r.w - 6, cy)
            canvas.set_font(ct.FONT_MONO, 7.5, "bold")
            tag = "SUPERSEDED"
            tw = ct.measure_tracked(canvas, tag, 0.6)
            ct.draw_tracked(canvas, tag, r.x + r.w - 6 - tw,
                            r.y + r.h - 5, 0.6, t.warn)

    # -- one edge --------------------------------------------------------------

    def _draw_edge(self, canvas: Canvas, etype: str, a: Rect, b: Rect, t) -> None:
        if etype == _SUPERSEDES:
            # heir (a=source) -> superseded (b=target); the ARROW points to the
            # HEIR ([CXC-DT-EDGE-SEMANTICS] "a single arrow to its heir").
            self._edge_between(canvas, b, a, t.warn, 2.0)
        elif etype == _DEPENDS_ON:
            # dependent (a=source) -> dependency (b=target); arrow to the dep.
            self._edge_between(canvas, a, b, t.dim, 1.4)
        else:
            # RESOLVES / re-derive-via / relates-to: faint secondary.
            self._edge_between(canvas, a, b, t.faint, 1.0)

    def _edge_between(self, canvas: Canvas, frm: Rect, to: Rect,
                      color, width: float) -> None:
        """Draw an arrow from the edge of box *frm* to the edge of box *to* —
        the SHARED layered-DAG arrow (border-clipped shaft + arrowhead)."""
        dag.draw_arrow_edge(canvas, frm, to, color, width, arrow=ARROW)
