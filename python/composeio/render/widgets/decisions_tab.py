"""
ComposeUX Decisions Tab — the Intelligence Console's query-driven decision-graph
explorer ([SPEC:CXC-DECISIONS-TAB]; the redesign of the Night-Watch first cut).

Desktop-only ([DEC] David 2026-07-24: a ~300-node interactive explorer is a bad
fit for the band renderer).  A *promotion + redesign* of the shipped
``DecisionsGraphWidget`` + ``layered_dag`` + ``decisions_publisher``:

  * ONE interactive ``Widget`` (the FlowPane idiom — a pane owns its node-set,
    layout, edges and hit-zones; a node is a DATACLASS drawn by a pure
    ``NodePresenter``, never a mounted child widget — [CXC-DT] feasibility §2).
    That single-arranged-rect / internal-zone-picking shape is what the damage +
    hit-test model rewards ([DEC-BANNER-OVERLAY-NOT-SLOT]: a node is clickable
    only because its zone derives from the SAME layout pass that drew it).
  * LEFT  — a Query pane (search + filter chips + lifecycle radios + session
    chips w/ a Custom overlay + importance + dusk-gift linkage + match/layout
    toggles + a synced results list).
  * RIGHT — a Graph pane (layered-DAG default; timeline mode; optional force —
    all three swap the LAYOUT only, never the NodePresenter — the seam test).
  * A Detail DRAWER slides over the graph's right edge on node-click — a
    CURATION surface: ▲ prioritize-up + append-note write back through the
    decision-ops MCP ``decision_update`` (the tab is a WRITE surface).

DETERMINISM ([CXC-R-C1]): the default layout is a pure function of the data
(``layered_dag`` longest-path + order-stable sorts), no per-frame random force
sim — so ``render_frame_at(t)`` is SHA-256-identical macOS↔RPi5 (Phase-0 gate,
proven).  Any animation (an optional force settle) MUST terminate.

Damage discipline ([CXC-R-DAMAGE]): every bus update / filter / selection / pan /
zoom calls ``self.request_redraw()`` ONLY — damage stays this widget's rect.

Bus-native + Intelligence-drivable: every act is a bus message
(``_app/decisions/anchor`` re-anchor · ``_app/decisions/selected`` selection ·
``_app/decisions/curate`` curation intent) — an Intelligence drives the identical
seams.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import layered_dag as dag

# ---------------------------------------------------------------------------
# Recenter control glyph ([CXC-DVL-RECENTER] / [CXC-DVL-ICON-ASSET]) — the
# Google Material "center_focus_strong" icon, COPIED INTO the repo (not
# hotlinked) and loaded from disk via the render suite's AssetManager, tinted
# to the header ink.  Parsed once (immutable) and cached.
# ---------------------------------------------------------------------------

RECENTER_ICON_PATH = os.path.join(os.path.dirname(__file__), "assets",
                                  "center_focus_strong.svg")
RECENTER_ICON_URI = "file://" + RECENTER_ICON_PATH
_icon_doc_cache: Dict[str, Any] = {}


def _load_recenter_icon(assets: Any = None) -> Any:
    """The parsed SvgDocument for the recenter glyph, or None.  Loads the
    repo-committed asset via the AssetManager when one is available
    ([CXC-DVL-ICON-ASSET] MUST load via AssetManager), else reads the repo
    file directly (offline, no network).  Cached module-wide."""
    if RECENTER_ICON_URI in _icon_doc_cache:
        return _icon_doc_cache[RECENTER_ICON_URI]
    data: Optional[bytes] = None
    getter = getattr(assets, "get_bytes", None)
    if callable(getter):
        try:
            data = getter(RECENTER_ICON_URI)
        except Exception:
            data = None
    if data is None:
        try:
            with open(RECENTER_ICON_PATH, "rb") as fh:
                data = fh.read()
        except OSError:
            data = None
    doc = None
    if data:
        try:
            from ..svg import SvgDocument
            doc = SvgDocument.from_bytes(data)
        except Exception:
            doc = None
    if doc is not None:
        _icon_doc_cache[RECENTER_ICON_URI] = doc
    return doc

DEFAULT_NAMESPACE = "self/decisions"
DEFAULT_ANCHOR_NAMESPACE = "_app/decisions/anchor"
DEFAULT_SELECTED_NAMESPACE = "_app/decisions/selected"
DEFAULT_CURATE_NAMESPACE = "_app/decisions/curate"
DEFAULT_OPEN_NAMESPACE = "_app/doc/open"

# ---------------------------------------------------------------------------
# Vocabulary ([SPEC:CXC-DECISIONS-TAB])
# ---------------------------------------------------------------------------

TYPES = ("DEC", "INSIGHT", "PRINCIPLE", "FIXTURE")
IMPORTANCES = ("high", "medium", "low")
# the granular lifecycle chips revealed under the Custom radio.  The SPEC's 7
# DEC positions + "noticed" (INSIGHT nodes carry it) so an insight is never
# silently hidden under Custom-all.
LIFECYCLES = ("raised", "decided", "spec-authored", "committed", "built",
              "settled", "superseded", "noticed")
# radio presets ([SPEC] §3): Incomplete / Complete
LIFE_INCOMPLETE = frozenset({"raised", "decided", "spec-authored", "noticed"})
LIFE_COMPLETE = frozenset({"committed", "built", "settled"})

# edge kinds ([SPEC] Graph pane): the full palette the previs shows
E_SUPERSEDES = "SUPERSEDES"
E_DEPENDS = "DEPENDS_ON"
E_RELATES = "RELATES_TO"
E_DERIVE = "RE_DERIVE_VIA"       # dusk-gift linkage carrier
E_FOLLOWS = "FOLLOWS"
_DIRECTED = {E_SUPERSEDES, E_DEPENDS, E_FOLLOWS}
# normalise the many spellings a projection/corpus may use
_EDGE_ALIASES = {
    "SUPERSEDES": E_SUPERSEDES, "SUPERSEDED_BY": E_SUPERSEDES,
    "DEPENDS_ON": E_DEPENDS, "DEPENDS-ON": E_DEPENDS,
    "RELATES_TO": E_RELATES, "RELATES-TO": E_RELATES, "RELATES": E_RELATES,
    "RE_DERIVE_VIA": E_DERIVE, "RE-DERIVE-VIA": E_DERIVE,
    "REDERIVE": E_DERIVE, "DERIVE": E_DERIVE, "RESOLVES": E_DERIVE,
    "FOLLOWS": E_FOLLOWS, "FOLLOW": E_FOLLOWS,
}


def _norm_lifecycle(v: Any) -> str:
    """lifecycle token, stripped of any trailing inline ``# ...`` comment."""
    return str(v or "").split("#", 1)[0].strip().lower()


def _type_of(node_id: str, explicit: Any = None) -> str:
    if explicit:
        u = str(explicit).strip().upper()
        if u in TYPES:
            return u
    up = str(node_id or "").upper()
    if up.startswith("TODO"):
        return "TODO"
    for t in TYPES:
        if up.startswith(t):
            return t
    return "DEC"


# ---------------------------------------------------------------------------
# Data model — topology + semantics as DATA (never geometry)
# ---------------------------------------------------------------------------

@dataclass
class DNode:
    id: str
    title: str = ""
    type: str = "DEC"
    lifecycle: str = ""
    importance: str = ""
    session: str = ""          # decided-in-session (date or session id)
    rationale: str = ""
    source: str = ""           # .lts:line
    dsd: str = ""              # linked decision-support doc / evidence
    note: str = ""             # curation note (append-note write-back target)
    badge: str = ""            # the corpus profile's badge_field value ([CXC-GV-STYLE-PROFILE])
    superseded: bool = False
    is_anchor: bool = False
    ghost: bool = False        # forward-dangle target (uncreated) — hollow
    dusk_linked: bool = False  # carries a re-derive-via / dusk-gift reference


@dataclass
class DEdge:
    type: str
    source: str
    target: str


# ---------------------------------------------------------------------------
# NodePresenter — "the node draws itself", relocated to a PURE fn + style table.
# The whole point of the pane-owns-layout seam: draw_node is byte-identical
# across dag/timeline/force — only the *rect* changes ([SPEC] the seam test).
# ---------------------------------------------------------------------------

class NodePresenter:
    """Pure ``draw_node(canvas, node, rect, theme, *, selected, dimmed)`` +
    a style table keyed by type / lifecycle / importance / ghost.  Carries NO
    geometry and NO layout knowledge; a new node kind is a local table change."""

    @staticmethod
    def accent_for(node: DNode, t) -> Tuple[int, int, int, int]:
        """The type's identity ink (from the console token table, by ROLE)."""
        return {
            "DEC": t.warn,          # amber — a decision
            "INSIGHT": t.accent,    # azure — an insight/wart  (accent role)
            "PRINCIPLE": t.ok,      # green — a principle
            "FIXTURE": t.dim,       # muted — a cold fixture
            "TODO": t.faint,
        }.get(node.type, t.accent)

    @staticmethod
    def draw_node(canvas: Canvas, n: DNode, r: Rect, t, *,
                  selected: bool = False, dimmed: bool = False,
                  tab_ink: Optional[Tuple[int, int, int, int]] = None) -> None:
        accent = NodePresenter.accent_for(n, t)
        # [CXC-GV-STYLE-PROFILE] a per-corpus profile MAY override the node accent
        # ink from its own semantic badge field; the SUPERSEDED + GHOST paths keep
        # their exact pre-existing rendering (so the decisions corpus, whose profile
        # maps every lifecycle to the same accent, is byte-identical).
        if tab_ink is not None and not n.superseded and not n.ghost:
            accent = tab_ink
        if dimmed:
            canvas.set_opacity(0.22)

        if n.ghost:
            # forward-dangle target: hollow, dashed border, muted purple ink.
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(r.x, r.y, r.w, r.h, 8.0)
            _dashed_rect(canvas, r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1,
                         t.dim, 1.2, dash=3.0, gap=3.0)
        else:
            canvas.set_fill_color(*t.panel2)
            canvas.fill_round_rect(r.x, r.y, r.w, r.h, 8.0)
            if selected:
                border, bw = t.accent, 2.0
            elif n.is_anchor:
                border, bw = accent, 1.8
            elif n.superseded:
                border, bw = t.line2, 1.0
            else:
                border, bw = t.line, 1.0
            canvas.set_stroke_color(*border)
            canvas.set_stroke_width(bw)
            canvas.stroke_round_rect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, 8.0)
            # a 3px type-accent tab on the left edge (the legend key made local)
            canvas.set_fill_color(*accent)
            canvas.fill_round_rect(r.x + 2.0, r.y + 6.0, 2.5, r.h - 12.0, 1.25)

        ink = t.faint if n.superseded else t.ink
        idc = t.faint if (n.superseded or n.ghost) else accent

        # line 1: the id (mono, type-accent unless superseded)
        canvas.set_font(ct.FONT_MONO, 9.0, "bold")
        m1 = canvas.measure_text("Ag")
        canvas.set_fill_color(*idc)
        canvas.draw_text(ct.ellipsize(canvas, n.id, r.w - 20),
                         r.x + 10, r.y + 6 + m1.ascent, "left")

        # line 2: a short title (UI, ellipsized)
        canvas.set_font(ct.FONT_UI, 11.0, "regular")
        m2 = canvas.measure_text("Ag")
        canvas.set_fill_color(*(t.faint if n.ghost else ink))
        canvas.draw_text(ct.ellipsize(canvas, n.title, r.w - 20),
                         r.x + 10, r.y + 24 + m2.ascent, "left")

        if n.superseded:
            cy = r.y + r.h / 2.0
            canvas.set_stroke_color(*t.faint)
            canvas.set_stroke_width(1.2)
            canvas.draw_line(r.x + 6, cy, r.x + r.w - 6, cy)
            canvas.set_font(ct.FONT_MONO, 7.5, "bold")
            tag = "SUPERSEDED"
            tw = ct.measure_tracked(canvas, tag, 0.6)
            ct.draw_tracked(canvas, tag, r.x + r.w - 6 - tw,
                            r.y + r.h - 5, 0.6, t.warn)
        elif n.dusk_linked and not n.ghost:
            # a small re-derivable-wisdom marker (diamond) in the derive ink
            canvas.set_font(ct.FONT_MONO, 8.0, "bold")
            canvas.set_fill_color(*t.ok)
            canvas.draw_text("◈", r.x + r.w - 14, r.y + r.h - 6, "left")

        if dimmed:
            canvas.set_opacity(1.0)


# ---------------------------------------------------------------------------
# small draw helpers (dashed straight-line geometry — SHA-stable primitives)
# ---------------------------------------------------------------------------

def _dashed_line(canvas: Canvas, x1: float, y1: float, x2: float, y2: float,
                 dash: float = 4.0, gap: float = 4.0) -> None:
    dx, dy = x2 - x1, y2 - y1
    dist = math.hypot(dx, dy)
    if dist <= 0:
        return
    ux, uy = dx / dist, dy / dist
    step = dash + gap
    n = int(dist // step) + 1
    for i in range(n):
        s = i * step
        e = min(s + dash, dist)
        if s >= dist:
            break
        canvas.draw_line(x1 + ux * s, y1 + uy * s,
                         x1 + ux * e, y1 + uy * e)


def _dashed_rect(canvas: Canvas, x: float, y: float, w: float, h: float,
                 color, width: float, dash: float = 3.0, gap: float = 3.0) -> None:
    canvas.set_stroke_color(*color)
    canvas.set_stroke_width(width)
    _dashed_line(canvas, x, y, x + w, y, dash, gap)
    _dashed_line(canvas, x + w, y, x + w, y + h, dash, gap)
    _dashed_line(canvas, x + w, y + h, x, y + h, dash, gap)
    _dashed_line(canvas, x, y + h, x, y, dash, gap)


# ---------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------

QPANE_W = 300.0          # left query-pane width
DRAWER_W = 380.0         # detail drawer width
PAD = 14.0
NODE_H = 46.0
NODE_MIN_W = 128.0
NODE_MAX_W = 210.0
COL_GAP = 20.0
ROW_GAP = 34.0
ARROW = 7.0
ROW_H = 40.0             # a results-list row
RECENTER_SIZE = 13.0    # [CXC-DVL-RECENTER] header glyph size (~the label cap)
RECENTER_GAP = 8.0      # gap between the recenter glyph and the label


class DecisionsTabWidget(Widget):
    """The whole Decisions tab as ONE interactive widget (the FlowPane idiom)."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 anchor_namespace: str = DEFAULT_ANCHOR_NAMESPACE,
                 selected_namespace: str = DEFAULT_SELECTED_NAMESPACE,
                 curate_namespace: str = DEFAULT_CURATE_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 nodes: Optional[List[Any]] = None,
                 edges: Optional[List[Any]] = None,
                 anchor: str = "",
                 corpus: str = "",
                 layout_mode: str = "dag",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "decisions_tab", **kwargs)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.anchor_namespace = str(anchor_namespace or DEFAULT_ANCHOR_NAMESPACE)
        self.selected_namespace = str(selected_namespace
                                      or DEFAULT_SELECTED_NAMESPACE)
        self.curate_namespace = str(curate_namespace or DEFAULT_CURATE_NAMESPACE)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.interactive = True
        self.accepts_focus = True

        self._nodes: List[DNode] = []
        self._edges: List[DEdge] = []
        self._by_id: Dict[str, DNode] = {}
        self._anchor = str(anchor or "")
        self._corpus = str(corpus or "")
        self.set_data(nodes, edges)

        # -- filter state ------------------------------------------------------
        self._q = ""
        self._search_focus = False
        self._note_focus = False
        self._note_draft = ""
        self._types = set(TYPES)
        self._life_mode = "custom"           # incomplete | complete | custom
        self._lifes = set(LIFECYCLES)
        self._imps = set(IMPORTANCES)
        self._sessions_sel: set = set()      # empty = all
        self._dusk = "all"                   # all | linked | notlinked (default)
        self._match = "highlight"            # highlight | filter
        self._layout_mode = layout_mode if layout_mode in (
            "dag", "timeline", "force") else "dag"

        # -- view / selection --------------------------------------------------
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._zoom = 1.0
        self._selected_id = ""
        self._drawer_open = False
        self._results_scroll = 0.0
        self._session_overlay_open = False
        self._overlay_scroll = 0.0
        self._last_pointer: Optional[Tuple[float, float]] = None
        self._timeline_keys: List[str] = []
        self._timeline_rows: List[List[DNode]] = []

        # -- hit zones (recorded at draw, from the same layout that drew) ------
        self._node_zones: List[Tuple[Rect, str]] = []
        self._edge_zones: List[Tuple[float, float, float, float, str, str]] = []
        self._chip_zones: List[Tuple[Rect, str, str]] = []   # (rect, group, val)
        self._result_zones: List[Tuple[Rect, str]] = []
        self._drawer_zones: List[Tuple[Rect, str, str]] = []  # (rect, kind, val)
        self._overlay_zones: List[Tuple[Rect, str, str]] = []
        self._search_zone: Optional[Rect] = None
        self._graph_area: Optional[Rect] = None
        # [CXC-DVL-RECENTER] the recenter control's hit zone, recorded from the
        # SAME layout pass that draws it (pane-owns-layout zone-picking).
        self._recenter_zone: Optional[Rect] = None
        self._icon_doc: Any = None

    # -- data --------------------------------------------------------------

    def set_data(self, nodes: Optional[List[Any]],
                 edges: Optional[List[Any]]) -> None:
        self._edges = self._coerce_edges(edges)
        self._nodes = self._coerce_nodes(nodes, self._edges)
        self._by_id = {n.id: n for n in self._nodes}

    @staticmethod
    def _coerce_edges(value: Any) -> List[DEdge]:
        out: List[DEdge] = []
        seen = set()
        if not isinstance(value, list):
            return out
        for it in value:
            if isinstance(it, (list, tuple)) and len(it) >= 3:
                src, tgt, et = str(it[0]), str(it[1]), str(it[2])
            elif isinstance(it, dict):
                src = str(it.get("source") or "").strip()
                tgt = str(it.get("target") or "").strip()
                et = str(it.get("type") or "").strip()
            else:
                continue
            src, tgt = src.strip(), tgt.strip()
            if not src or not tgt:
                continue
            etype = _EDGE_ALIASES.get(et.upper().replace(" ", "_"), et.upper())
            key = (etype, src, tgt)
            if key in seen:
                continue
            seen.add(key)
            out.append(DEdge(etype, src, tgt))
        return out

    def _coerce_nodes(self, value: Any, edges: List[DEdge]) -> List[DNode]:
        out: List[DNode] = []
        if not isinstance(value, list):
            value = []
        # which ids carry a dusk-gift re-derive edge?
        dusk_ids = set()
        for e in edges:
            if e.type == E_DERIVE:
                dusk_ids.add(e.source)
                dusk_ids.add(e.target)
        seen = set()
        for it in value:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("id") or "").strip()
            if not nid or nid in seen:
                continue
            seen.add(nid)
            lifecycle = _norm_lifecycle(it.get("lifecycle")
                                        or it.get("lifecycle_position")
                                        or it.get("lifecycle-position"))
            superseded = bool(it.get("superseded")) or lifecycle == "superseded"
            rat = str(it.get("rationale") or "")
            dusk = (bool(it.get("dusk_linked"))
                    or nid in dusk_ids
                    or "dusk-gift" in rat.lower())  # substring also matches "…/dusk-gifts/…"
            out.append(DNode(
                id=nid,
                title=str(it.get("title") or ""),
                type=_type_of(nid, it.get("type")),
                lifecycle=lifecycle,
                importance=str(it.get("importance") or "").strip().lower(),
                session=str(it.get("session")
                            or it.get("decided_in_session")
                            or it.get("decided-in-session") or "").strip(),
                rationale=rat,
                source=str(it.get("source") or it.get("src") or ""),
                dsd=str(it.get("dsd") or ""),
                note=str(it.get("note") or ""),
                badge=str(it.get("badge") or ""),
                superseded=superseded,
                is_anchor=bool(it.get("is_anchor")),
                ghost=bool(it.get("ghost")),
                dusk_linked=dusk,
            ))
        # synthesize GHOST nodes for forward-dangles: a depends-on/relates/
        # follows edge whose TARGET is not in the node set ([SPEC] Phase 1:
        # "add the ghost / forward-dangle field the shipped payload lacks").
        known = {n.id for n in out}
        for e in edges:
            if e.target and e.target not in known:
                out.append(DNode(id=e.target,
                                 title="(uncreated — forward dangle)",
                                 type=_type_of(e.target),
                                 ghost=True))
                known.add(e.target)
        return out

    # -- bus wiring --------------------------------------------------------

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
        self._anchor = str(payload.get("anchor") or self._anchor)
        self._corpus = str(payload.get("corpus") or self._corpus)
        self.set_data(payload.get("nodes"), payload.get("edges"))
        self.request_redraw()

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        # [CXC-DVL-ICON-ASSET] load the recenter glyph from the repo asset via
        # the engine AssetManager (offline; never a network fetch at render).
        self._icon_doc = _load_recenter_icon(getattr(ctx, "assets", None))

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    # -- filter logic ------------------------------------------------------

    def _eff_lifes(self) -> frozenset:
        if self._life_mode == "incomplete":
            return LIFE_INCOMPLETE
        if self._life_mode == "complete":
            return LIFE_COMPLETE
        return frozenset(self._lifes)

    def _sessions(self) -> List[str]:
        return sorted({n.session for n in self._nodes
                       if n.session and not n.ghost}, reverse=True)

    def _session_label(self, s: str, rank: int, total: int) -> str:
        tail = s[5:] if len(s) >= 10 and s[4] == "-" else s
        return f"S{total - rank} · {tail}" if len(s) >= 10 else s

    def _matches(self, n: DNode) -> bool:
        """True if the node satisfies the active query ([SPEC] §3)."""
        if n.ghost:
            return False
        if n.type not in self._types:
            return False
        if n.type in ("DEC", "INSIGHT"):
            if n.lifecycle and n.lifecycle not in self._eff_lifes():
                return False
            if n.importance and n.importance not in self._imps:
                return False
        if self._sessions_sel and n.session and n.session not in self._sessions_sel:
            return False
        if self._dusk == "linked" and not n.dusk_linked:
            return False
        if self._dusk == "notlinked" and n.dusk_linked:
            return False
        if self._q:
            hay = (n.id + " " + n.title + " " + n.rationale).lower()
            if self._q not in hay:
                return False
        return True

    def _any_filter_active(self) -> bool:
        return bool(self._q) or self._types != set(TYPES) \
            or self._life_mode != "custom" or set(self._lifes) != set(LIFECYCLES) \
            or self._imps != set(IMPORTANCES) or bool(self._sessions_sel) \
            or self._dusk != "all"

    def _results(self) -> List[DNode]:
        lst = [n for n in self._nodes if self._matches(n)]
        lst.sort(key=lambda n: (n.session, n.id), reverse=True)
        return lst

    # =====================================================================
    #  LAYOUT — the seam.  Each mode returns {id: Rect}; NodePresenter is
    #  byte-identical across all three (only the rect source changes).
    # =====================================================================

    def _layout(self, area: Rect) -> Dict[str, Rect]:
        if self._layout_mode == "timeline":
            return self._layout_timeline(area)
        if self._layout_mode == "force":
            return self._layout_force(area)
        return self._layout_dag(area)

    def _layout_dag(self, area: Rect) -> Dict[str, Rect]:
        ids = {n.id for n in self._nodes}
        deps = [(e.source, e.target) for e in self._edges if e.type == E_DEPENDS]
        level = dag.assign_levels(ids, deps)
        for e in self._edges:
            if e.type == E_SUPERSEDES and e.source in ids and e.target in ids:
                level[e.target] = level[e.source]
        by_lvl: Dict[int, List[DNode]] = {}
        for n in self._nodes:
            by_lvl.setdefault(level.get(n.id, 0), []).append(n)
        heir_of = {e.target: e.source for e in self._edges
                   if e.type == E_SUPERSEDES}
        rows: List[List[DNode]] = []
        for lvl in sorted(by_lvl):
            row = by_lvl[lvl]

            def sort_key(n: DNode) -> Tuple[int, str]:
                if n.is_anchor:
                    return (0, n.id)
                heir = heir_of.get(n.id)
                if heir is not None:
                    return (1, heir + "~z")
                return (1, n.id)
            rows.append(sorted(row, key=sort_key))
        return dag.layout_grid(rows, area, orientation="TD",
                               cell_main=NODE_H, gap_main=ROW_GAP,
                               cross_min=NODE_MIN_W, cross_max=NODE_MAX_W,
                               gap_cross=COL_GAP, id_of=lambda n: n.id)

    def _layout_timeline(self, area: Rect) -> Dict[str, Rect]:
        """Older TOP / newer BOTTOM ([SPEC] §Timeline).  Bucket by session,
        sort ascending, place bucket k at y = top + k·pitch; spread on x.
        O(V), order-stable, animation-free — the honest whole-graph mode."""
        buckets: Dict[str, List[DNode]] = {}
        for n in self._nodes:
            buckets.setdefault(n.session or "~", []).append(n)
        keys = sorted(buckets)                      # ascending = older first
        rows = [sorted(buckets[k], key=lambda n: n.id) for k in keys]
        # reserve a left gutter for the time axis
        inner = Rect(area.x + 62.0, area.y, max(1.0, area.w - 62.0), area.h)
        pos = dag.layout_grid(rows, inner, orientation="TD",
                              cell_main=NODE_H, gap_main=ROW_GAP,
                              cross_min=NODE_MIN_W, cross_max=NODE_MAX_W,
                              gap_cross=COL_GAP, id_of=lambda n: n.id,
                              center_main=False)
        self._timeline_keys = keys                  # TimeAxis reads these
        self._timeline_rows = rows
        return pos

    def _layout_force(self, area: Rect) -> Dict[str, Rect]:
        """OPTIONAL bounded, deterministically-SEEDED settle to a REST state —
        NOT a perpetual sim ([SPEC] §5; feasibility §3).  Grid-seeded (no RNG),
        a hard-capped iteration count, then it STOPS: a pure function of the
        data, so it is golden-stable and reproducible macOS↔RPi5.  Reuses the
        dag placement as the seed so a mode-swap is visually continuous."""
        seed = self._layout_dag(area)
        ids = [n.id for n in self._nodes]
        px = {i: seed[i].x + seed[i].w / 2.0 for i in ids if i in seed}
        py = {i: seed[i].y + seed[i].h / 2.0 for i in ids if i in seed}
        adj = [(e.source, e.target) for e in self._edges
               if e.source in px and e.target in px]
        cx, cy = area.x + area.w / 2.0, area.y + area.h / 2.0
        for _ in range(60):                         # hard cap → terminates
            fx = {i: 0.0 for i in px}
            fy = {i: 0.0 for i in px}
            keys = list(px)
            for a in keys:
                for b in keys:
                    if a >= b:
                        continue
                    dx, dy = px[a] - px[b], py[a] - py[b]
                    d2 = dx * dx + dy * dy or 1.0
                    f = 5200.0 / d2
                    d = math.sqrt(d2)
                    ux, uy = dx / d, dy / d
                    fx[a] += ux * f; fy[a] += uy * f
                    fx[b] -= ux * f; fy[b] -= uy * f
            for a, b in adj:
                dx, dy = px[b] - px[a], py[b] - py[a]
                d = math.hypot(dx, dy) or 1.0
                f = (d - 150.0) * 0.06
                ux, uy = dx / d, dy / d
                fx[a] += ux * f; fy[a] += uy * f
                fx[b] -= ux * f; fy[b] -= uy * f
            for i in keys:
                fx[i] += (cx - px[i]) * 0.02
                fy[i] += (cy - py[i]) * 0.02
                px[i] = min(area.x + area.w - NODE_MIN_W / 2,
                            max(area.x + NODE_MIN_W / 2, px[i] + fx[i] * 0.5))
                py[i] = min(area.y + area.h - NODE_H / 2,
                            max(area.y + NODE_H / 2, py[i] + fy[i] * 0.5))
        out: Dict[str, Rect] = {}
        for i in ids:
            if i not in px:
                continue
            w = seed[i].w if i in seed else NODE_MIN_W
            out[i] = Rect(px[i] - w / 2.0, py[i] - NODE_H / 2.0, w, NODE_H)
        return out

    # =====================================================================
    #  DRAW
    # =====================================================================

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._node_zones = []
        self._edge_zones = []
        self._chip_zones = []
        self._result_zones = []
        self._drawer_zones = []
        self._overlay_zones = []
        self._recenter_zone = None
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*t.bg)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        qrect = Rect(rect.x, rect.y, QPANE_W, rect.h)
        grect = Rect(rect.x + QPANE_W, rect.y,
                     max(1.0, rect.w - QPANE_W), rect.h)
        self._graph_area = grect
        self._draw_graph(canvas, grect, t)
        self._draw_query(canvas, qrect, t)

        if self._drawer_open and self._selected_id in self._by_id:
            self._draw_drawer(canvas, grect, t)
        if self._session_overlay_open:
            self._draw_session_overlay(canvas, qrect, t)
        # [CXC-GV-CORPUS-SELECTOR] the corpus dropdown's option list draws LAST so
        # it overlays the graph (a no-op in the base decisions tab).
        self._draw_corpus_overlay(canvas, grect, t)
        canvas.restore()

    # -- generalization hooks ([SPEC:CXC-GRAPH-VIEWER]) --------------------
    # These are no-ops / decisions-defaults in the base tab so the decisions_tab
    # render is BYTE-IDENTICAL; GraphViewerWidget overrides them to add the corpus
    # selector, the per-corpus badge ink, and the direct-query states.

    def _graph_header_label(self) -> str:
        return "DECISION NEIGHBORHOOD"

    def _empty_state_text(self) -> str:
        return "awaiting self/decisions…"

    def _draw_corpus_selector(self, canvas: Canvas, x: float, y_top: float,
                              m: Any, t) -> float:
        """Draw the corpus dropdown pill; return the horizontal advance the header
        label shifts right by.  0.0 (no pill) in the base tab."""
        return 0.0

    def _draw_corpus_overlay(self, canvas: Canvas, grect: Rect, t) -> None:
        return None

    def _tab_ink(self, n: "DNode", t) -> Optional[Tuple[int, int, int, int]]:
        return None

    def _draw_scope_banner(self, canvas: Canvas, x: float, base: float,
                           t) -> None:
        """Announce an active run-scope ([CXC-GV-SCOPE]).  No-op in the base tab
        (it has no scope); the viewer overrides it."""
        return None

    def _on_extra_tap(self, x: float, y: float) -> bool:
        """A first-look tap hook (corpus selector / its overlay).  False in the
        base tab; True in the viewer when the tap was consumed."""
        return False

    def _on_node_anchor(self, nid: str) -> None:
        """Called after a node tap publishes the re-anchor intent — the viewer
        re-queries a bounded neighborhood here ([CXC-GV-PROJECTION-MODES])."""
        return None

    # -- graph pane --------------------------------------------------------

    def _draw_graph(self, canvas: Canvas, rect: Rect, t) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        # header
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        m = canvas.measure_text("Ag")
        base = rect.y + PAD + m.ascent
        # [CXC-DVL-RECENTER] the recenter control sits IMMEDIATELY BEFORE the
        # "DECISION NEIGHBORHOOD" label; the label + its baseline shift right by
        # the control width + a small gap so the two never overlap.
        label_x = rect.x + PAD + self._draw_recenter(canvas, rect.x + PAD,
                                                      rect.y + PAD, m, t)
        label_x += self._draw_corpus_selector(canvas, label_x, rect.y + PAD, m, t)
        hdr = self._graph_header_label()
        ct.draw_tracked(canvas, hdr, label_x, base, ct.TRACK_LABEL, t.ink)
        # [CXC-GV-SCOPE] the run-scope announcement sits immediately after the
        # header label (a no-op in the base tab) — a scoped view MUST say so.
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "bold")
        self._draw_scope_banner(
            canvas, label_x + canvas.measure_text(hdr).width
            + ct.TRACK_LABEL * max(0, len(hdr)) + 18.0, base, t)
        canvas.set_font(ct.FONT_MONO, ct.NS_TAG, "regular")
        mode_tag = f"{self._layout_mode.upper()}  ·  z{self._zoom:.1f}"
        canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2],
                              ct.NS_TAG_ALPHA)
        canvas.draw_text(mode_tag, rect.x + rect.w - PAD, base, "right")

        area = Rect(rect.x + PAD, rect.y + PAD + 26.0,
                    rect.w - 2 * PAD, rect.h - 2 * PAD - 26.0)
        if not self._nodes:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            canvas.draw_text(self._empty_state_text(),
                             rect.x + rect.w / 2, rect.y + rect.h / 2, "center")
            canvas.restore()
            return

        pos = self._layout(area)
        acx, acy = area.x + area.w / 2.0, area.y + area.h / 2.0

        def to_screen(r: Rect) -> Rect:
            z = self._zoom
            return Rect(acx + self._pan_x + (r.x - acx) * z,
                        acy + self._pan_y + (r.y - acy) * z,
                        r.w * z, r.h * z)

        canvas.save()
        canvas.translate(acx + self._pan_x, acy + self._pan_y)
        canvas.scale(self._zoom, self._zoom)
        canvas.translate(-acx, -acy)

        if self._layout_mode == "timeline":
            self._draw_time_axis(canvas, area, pos, t)

        # edges behind nodes
        for e in self._edges:
            a = pos.get(e.source)
            b = pos.get(e.target)
            if a is None or b is None:
                continue
            lit = True
            if self._any_filter_active() and self._match == "highlight":
                lit = (self._matches_id(e.source) or self._matches_id(e.target))
            self._draw_edge(canvas, e, a, b, t, lit)

        # nodes
        for n in self._nodes:
            r = pos.get(n.id)
            if r is None:
                continue
            m_ok = self._matches(n)
            if self._match == "filter" and self._any_filter_active() \
                    and not m_ok and not n.ghost:
                continue
            dimmed = (self._match == "highlight" and self._any_filter_active()
                      and not m_ok and not n.ghost)
            NodePresenter.draw_node(canvas, n, r, t,
                                    selected=(n.id == self._selected_id),
                                    dimmed=dimmed, tab_ink=self._tab_ink(n, t))
            if not dimmed or n.id == self._selected_id:
                self._node_zones.append((to_screen(r), n.id))
        canvas.restore()

        # record edge zones (screen space) for edge-click navigation
        for e in self._edges:
            a = pos.get(e.source)
            b = pos.get(e.target)
            if a is None or b is None:
                continue
            sa, sb = to_screen(a), to_screen(b)
            self._edge_zones.append((sa.x + sa.w / 2, sa.y + sa.h / 2,
                                     sb.x + sb.w / 2, sb.y + sb.h / 2,
                                     e.source, e.target))
        canvas.restore()

    def _matches_id(self, nid: str) -> bool:
        n = self._by_id.get(nid)
        return bool(n and self._matches(n))

    # -- recenter control ([CXC-DVL-RECENTER] / [CXC-DVL-ICON-ASSET]) ------

    def _draw_recenter(self, canvas: Canvas, x: float, y_top: float,
                       m: Any, t) -> float:
        """Draw the recenter glyph at the header's left edge and record its
        hit zone from THIS draw pass.  Returns the horizontal advance (glyph
        width + gap) the "DECISION NEIGHBORHOOD" label shifts right by so the
        two never overlap ([CXC-DVL-RECENTER] PLACEMENT)."""
        line_h = m.ascent + m.descent
        size = RECENTER_SIZE
        iy = y_top + max(0.0, (line_h - size) / 2.0)
        icon_rect = Rect(x, iy, size, size)
        doc = self._icon_doc
        ink = t.ink
        if doc is not None:
            from ..svg import render_svg
            base_fill = "#%02x%02x%02x" % (ink[0], ink[1], ink[2])
            render_svg(canvas, doc, icon_rect, fit="contain",
                       base_fill=base_fill)
        else:
            # Fallback crosshair (asset missing) — never a blank control.
            canvas.set_stroke_color(*ink)
            canvas.set_stroke_width(1.2)
            cx, cy = x + size / 2.0, iy + size / 2.0
            canvas.stroke_round_rect(x + 1, iy + 1, size - 2, size - 2, 2.0)
            canvas.draw_line(cx, iy + 2, cx, iy + size - 2)
            canvas.draw_line(x + 2, cy, x + size - 2, cy)
        # a slightly-padded hit zone for easy clicking (still left of label).
        self._recenter_zone = Rect(x - 3, iy - 3, size + 6, size + 6)
        return size + RECENTER_GAP

    @staticmethod
    def _fit_zoom(area: Rect, pos: Dict[str, Rect],
                  zmin: float = 0.4, zmax: float = 3.0,
                  pad: float = 0.94) -> float:
        """The largest zoom at which, with pan=0 (scale about the graph-area
        center), every laid-out node's bounds fit inside *area*
        ([CXC-DVL-RECENTER] fit-to-viewport).  Pure function of the layout, so
        the re-fit is deterministic.  Clamped to the pinch limits so a fit
        never exceeds the interactive zoom range."""
        if not pos:
            return 1.0
        xs0 = min(r.x for r in pos.values())
        ys0 = min(r.y for r in pos.values())
        xs1 = max(r.x + r.w for r in pos.values())
        ys1 = max(r.y + r.h for r in pos.values())
        acx = area.x + area.w / 2.0
        acy = area.y + area.h / 2.0
        half_w = area.w / 2.0
        half_h = area.h / 2.0
        cands: List[float] = []
        for dist, half in ((acx - xs0, half_w), (xs1 - acx, half_w),
                           (acy - ys0, half_h), (ys1 - acy, half_h)):
            if dist > 1e-6:
                cands.append(half / dist)
        z = min(cands) if cands else 1.0
        return max(zmin, min(zmax, z * pad))

    def _recenter(self) -> None:
        """Reset pan to (0,0) and zoom to the fit-to-viewport zoom for the
        active layout, so the whole current neighborhood is visible inside the
        graph pane ([CXC-DVL-RECENTER] ACTION).  Damage stays this widget's
        rect (request_redraw only)."""
        self._pan_x = 0.0
        self._pan_y = 0.0
        grect = self._graph_area
        if grect is not None and self._nodes:
            area = Rect(grect.x + PAD, grect.y + PAD + 26.0,
                        grect.w - 2 * PAD, grect.h - 2 * PAD - 26.0)
            self._zoom = self._fit_zoom(area, self._layout(area))
        else:
            self._zoom = 1.0
        self.request_redraw()

    def _draw_edge(self, canvas: Canvas, e: DEdge, a: Rect, b: Rect, t,
                   lit: bool) -> None:
        color = {
            E_SUPERSEDES: t.warn,
            E_DEPENDS: t.accent,
            E_RELATES: t.faint,
            E_DERIVE: t.ok,
            E_FOLLOWS: t.dim,
        }.get(e.type, t.faint)
        if not lit:
            color = (color[0], color[1], color[2], 60)
        width = 2.0 if e.type == E_SUPERSEDES else 1.4
        tgt = self._by_id.get(e.target)
        if tgt is not None and tgt.ghost:
            # forward-dangle: a distinct DASHED purple edge (no pulse — a
            # perpetual pulse is damage-forever; the dash carries the meaning).
            p1 = dag.edge_point(a, b.x + b.w / 2, b.y + b.h / 2)
            p2 = dag.edge_point(b, a.x + a.w / 2, a.y + a.h / 2)
            canvas.set_stroke_color(0xB4, 0x8E, 0xF2, 220)
            canvas.set_stroke_width(2.0)
            _dashed_line(canvas, p1[0], p1[1], p2[0], p2[1], 3.0, 4.0)
            return
        if e.type == E_SUPERSEDES:
            # arrow points to the HEIR ([CXC-DT-EDGE-SEMANTICS])
            dag.draw_arrow_edge(canvas, b, a, color, width, arrow=ARROW)
        elif e.type in _DIRECTED:
            dag.draw_arrow_edge(canvas, a, b, color, width, arrow=ARROW)
        else:
            dag.draw_arrow_edge(canvas, a, b, color, width, arrowhead=False)

    def _draw_time_axis(self, canvas: Canvas, area: Rect,
                        pos: Dict[str, Rect], t) -> None:
        keys = self._timeline_keys
        rows = self._timeline_rows
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(area.x + 52.0, area.y, area.x + 52.0,
                         area.y + area.h)
        canvas.set_font(ct.FONT_MONO, 8.5, "regular")
        for k, row in zip(keys, rows):
            ys = [pos[n.id].y for n in row if n.id in pos]
            if not ys:
                continue
            yc = sum(ys) / len(ys) + NODE_H / 2.0
            canvas.set_fill_color(*t.accent)
            canvas.fill_circle(area.x + 52.0, yc, 2.5)
            canvas.set_fill_color(*t.dim)
            lbl = k if k != "~" else "—"
            lbl = lbl[5:] if len(lbl) >= 10 and lbl[4] == "-" else lbl
            canvas.draw_text(lbl, area.x, yc + 3.0, "left")

    # -- query pane --------------------------------------------------------

    def _draw_query(self, canvas: Canvas, rect: Rect, t) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*t.panel)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(rect.x + rect.w, rect.y, rect.x + rect.w,
                         rect.y + rect.h)

        x = rect.x + PAD
        w = rect.w - 2 * PAD
        y = rect.y + PAD

        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        mm = canvas.measure_text("Ag")
        ct.draw_tracked(canvas, "QUERY THE DECISION GRAPH", x, y + mm.ascent,
                        1.0, t.faint)
        y += 22.0

        # search box
        sb = Rect(x, y, w, 30.0)
        self._search_zone = sb
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(sb.x, sb.y, sb.w, sb.h, 7.0)
        canvas.set_stroke_color(*(t.accent if self._search_focus else t.line2))
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(sb.x + 0.5, sb.y + 0.5, sb.w - 1, sb.h - 1, 7.0)
        canvas.set_font(ct.FONT_UI, 12.0, "regular")
        ms = canvas.measure_text("Ag")
        show_ph = not self._q and not self._search_focus
        txt = self._q if self._q else ("free-text: title + rationale…"
                                       if show_ph else "")
        cursor = "|" if self._search_focus else ""
        canvas.set_fill_color(*(t.ink if self._q else t.faint))
        canvas.draw_text(ct.ellipsize(canvas, txt + cursor, w - 18),
                         sb.x + 9,
                         sb.y + sb.h / 2 + (ms.ascent - ms.descent) / 2, "left")
        y += 42.0

        y = self._draw_chip_group(canvas, x, y, w, "TYPE · PREFIX",
                                   [(tp, tp) for tp in TYPES], "type",
                                   self._types, t)

        # lifecycle radios
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("LIFECYCLE", x, y + 8, "left")
        y += 16.0
        y = self._draw_seg(canvas, x, y, w,
                           [("Show Incomplete", "incomplete"),
                            ("Show Complete", "complete"),
                            ("Custom", "custom")],
                           "lifemode", self._life_mode, t)
        if self._life_mode == "custom":
            y = self._draw_chip_group(canvas, x, y + 4, w, "",
                                       [(lc, lc) for lc in LIFECYCLES],
                                       "life", self._lifes, t)
        else:
            y += 4.0

        # sessions + Custom overlay button
        sess = self._sessions()
        total = len(sess)
        # The first 6 are the visible chips — PLUS any SELECTED session outside
        # that window.  A selected-but-unshown session would be an INVISIBLE
        # filter (the user could not see, or click off, what is scoping their
        # view); surfacing it keeps every active filter legible + widenable
        # ([CXC-GV-SCOPE]).
        shown = list(sess[:6])
        for s in sess[6:]:
            if s in self._sessions_sel:
                shown.append(s)
        chips = [(s, self._session_label(s, sess.index(s), total))
                 for s in shown]
        y = self._draw_session_group(canvas, x, y, w, chips, t)

        y = self._draw_chip_group(canvas, x, y, w, "IMPORTANCE",
                                   [(im, im) for im in IMPORTANCES], "imp",
                                   self._imps, t)

        # dusk-gift linkage
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("DUSK-GIFT LINKAGE", x, y + 8, "left")
        y += 16.0
        y = self._draw_seg(canvas, x, y, w,
                           [("All", "all"), ("Linked", "linked"),
                            ("Not-linked", "notlinked")],
                           "dusk", self._dusk, t)
        y += 6.0

        # match + layout toggles
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("MATCH", x, y + 8, "left")
        canvas.draw_text("LAYOUT", x + w / 2 + 4, y + 8, "left")
        y += 16.0
        half = w / 2 - 4
        self._draw_seg(canvas, x, y, half,
                       [("Highlight", "highlight"), ("Filter", "filter")],
                       "match", self._match, t)
        y2 = self._draw_seg(canvas, x + w / 2 + 4, y, half,
                            [("DAG", "dag"), ("Time", "timeline"),
                             ("Force", "force")],
                            "layout", self._layout_mode, t)
        y = y2 + 8.0

        self._draw_results(canvas, rect, x, y, w, t)
        canvas.restore()

    def _chip_row(self, canvas, x, y, w, items, group, active_set, t,
                  accent=False):
        cx, cy = x, y
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        for val, txt in items:
            tw = canvas.measure_text(txt).width
            cw = tw + 16.0
            if cx + cw > x + w:
                cx = x
                cy += 26.0
            on = (val in active_set) if active_set is not None else False
            r = Rect(cx, cy, cw, 20.0)
            if accent:
                fill, stroke, ink = t.accent_dim, t.accent_line, t.accent
            else:
                fill = t.panel2 if on else t.panel3
                stroke = t.line2 if on else t.line
                ink = t.ink if on else t.faint
            canvas.set_fill_color(*fill)
            canvas.fill_round_rect(r.x, r.y, r.w, r.h, 10.0)
            canvas.set_stroke_color(*stroke)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, 10.0)
            canvas.set_fill_color(*ink)
            mtx = canvas.measure_text("Ag")
            canvas.draw_text(txt, cx + 8,
                             cy + 10 + (mtx.ascent - mtx.descent) / 2, "left")
            self._chip_zones.append((r, group, val))
            cx += cw + 5.0
        return cy + 28.0

    def _draw_chip_group(self, canvas, x, y, w, label, items, group,
                         active_set, t):
        if label:
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(label, x, y + 8, "left")
            y += 16.0
        return self._chip_row(canvas, x, y, w, items, group, active_set, t)

    def _draw_session_group(self, canvas, x, y, w, chips, t):
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("SESSION", x, y + 8, "left")
        y += 16.0
        y = self._chip_row(canvas, x, y, w, chips, "session",
                           self._sessions_sel, t)
        # Custom overlay button (its own accent chip)
        y = self._chip_row(canvas, x, y - 28.0 + 26.0, w,
                           [("", "Custom ▸")], "session_custom", None, t,
                           accent=True)
        return y

    def _draw_seg(self, canvas, x, y, w, options, group, active_val, t):
        n = len(options)
        seg_w = w / n
        r = Rect(x, y, w, 24.0)
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, 6.0)
        canvas.set_stroke_color(*t.line2)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1, 6.0)
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        for i, (txt, val) in enumerate(options):
            sx = x + i * seg_w
            sr = Rect(sx, y, seg_w, 24.0)
            if val == active_val:
                canvas.set_fill_color(*t.accent_dim)
                canvas.fill_round_rect(sx + 1, y + 1, seg_w - 2, 22.0, 5.0)
            canvas.set_fill_color(*(t.ink if val == active_val else t.dim))
            mtx = canvas.measure_text("Ag")
            canvas.draw_text(ct.ellipsize(canvas, txt, seg_w - 6),
                             sx + seg_w / 2,
                             y + 12 + (mtx.ascent - mtx.descent) / 2, "center")
            self._chip_zones.append((sr, group, val))
        return y + 30.0

    def _draw_results(self, canvas, prect, x, y, w, t):
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(prect.x, y, prect.x + prect.w, y)
        results = self._results()
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("RESULTS", x, y + 14, "left")
        canvas.set_fill_color(*t.accent)
        canvas.draw_text(str(len(results)), x + 66, y + 14, "left")
        y += 24.0
        list_top = y
        list_bot = prect.y + prect.h - 4.0
        canvas.save()
        canvas.clip_rect(prect.x, list_top, prect.w, max(0.0, list_bot - list_top))
        ry = list_top - self._results_scroll
        for n in results:
            if ry + ROW_H >= list_top and ry <= list_bot:
                sel = (n.id == self._selected_id)
                r = Rect(prect.x, ry, prect.w, ROW_H)
                if sel:
                    canvas.set_fill_color(*t.accent_dim)
                    canvas.fill_rect(r.x, r.y, r.w, r.h)
                    canvas.set_fill_color(*t.accent)
                    canvas.fill_rect(r.x, r.y, 3.0, r.h)
                canvas.set_fill_color(*NodePresenter.accent_for(n, t))
                canvas.fill_circle(x + 4, ry + 13, 4.0)
                canvas.set_font(ct.FONT_MONO, 8.5, "regular")
                canvas.set_fill_color(*t.faint)
                canvas.draw_text(ct.ellipsize(canvas, n.id, w - 60),
                                 x + 14, ry + 13, "left")
                canvas.set_font(ct.FONT_UI, 11.0, "regular")
                canvas.set_fill_color(*t.ink)
                canvas.draw_text(ct.ellipsize(canvas, n.title, w - 20),
                                 x + 14, ry + 30, "left")
                if n.lifecycle:
                    canvas.set_font(ct.FONT_MONO, 7.5, "regular")
                    canvas.set_fill_color(*t.dim)
                    canvas.draw_text(n.lifecycle.upper(),
                                     prect.x + prect.w - PAD, ry + 13, "right")
                self._result_zones.append((r, n.id))
            ry += ROW_H
        canvas.restore()

    # -- detail drawer -----------------------------------------------------

    def _draw_drawer(self, canvas: Canvas, grect: Rect, t) -> None:
        n = self._by_id[self._selected_id]
        dx = grect.x + grect.w - DRAWER_W
        dr = Rect(dx, grect.y, DRAWER_W, grect.h)
        canvas.set_shadow(-16, 0, 40, t.shadow)
        canvas.set_fill_color(*t.panel2)
        canvas.fill_rect(dr.x, dr.y, dr.w, dr.h)
        canvas.clear_shadow()
        canvas.set_stroke_color(*t.line2)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(dr.x, dr.y, dr.x, dr.y + dr.h)

        px = dr.x + 16.0
        pw = dr.w - 32.0
        y = dr.y + 14.0
        cr = Rect(dr.x + dr.w - 26.0, dr.y + 10.0, 18.0, 18.0)
        canvas.set_font(ct.FONT_MONO, 13.0, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("✕", cr.x + 4, cr.y + 13, "left")
        self._drawer_zones.append((cr, "close", ""))

        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(ct.ellipsize(canvas, n.id, pw - 24), px, y + 9, "left")
        y += 20.0
        y = self._wrap_text(canvas, n.title, px, y, pw, ct.FONT_UI, 14.0,
                            "bold", t.ink, 19.0)
        y += 6.0
        chips = [n.type]
        if n.lifecycle:
            chips.append(n.lifecycle)
        if n.importance and n.type in ("DEC", "INSIGHT"):
            chips.append("imp: " + n.importance)
        if n.dusk_linked:
            chips.append("◈ dusk-linked")
        cxp = px
        canvas.set_font(ct.FONT_MONO, 8.0, "regular")
        for c in chips:
            cw = canvas.measure_text(c).width + 14.0
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(cxp, y, cw, 18.0, 9.0)
            canvas.set_stroke_color(*t.line2)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(cxp + 0.5, y + 0.5, cw - 1, 17.0, 9.0)
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(c, cxp + 7, y + 12, "left")
            cxp += cw + 5.0
        y += 28.0
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(dr.x, y, dr.x + dr.w, y)
        y += 12.0

        canvas.save()
        canvas.clip_rect(dr.x, y, dr.w, dr.y + dr.h - y)

        def meta(k, v):
            nonlocal y
            canvas.set_font(ct.FONT_MONO, 8.5, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(k.upper(), px, y + 9, "left")
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(ct.ellipsize(canvas, v, pw - 90), px + 84, y + 9,
                             "left")
            y += 16.0

        meta("decided", n.session or "—")
        meta("source", n.source or "—")
        if n.ghost:
            meta("state", "forward-dangle · uncreated")
        y += 8.0

        y = self._sec(canvas, "DOCUMENTARY EVIDENCE", px, y, t)
        if n.dsd:
            canvas.set_fill_color(*t.warn_dim)
            canvas.fill_round_rect(px, y, pw, 34.0, 8.0)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.set_fill_color(*t.warn)
            canvas.draw_text(ct.ellipsize(canvas, n.dsd, pw - 60), px + 10,
                             y + 21, "left")
            obr = Rect(px + pw - 46, y + 7, 40, 20)
            canvas.set_stroke_color(*t.line2)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(obr.x + 0.5, obr.y + 0.5, 39, 19, 6.0)
            canvas.set_fill_color(*t.dim)
            canvas.draw_text("open", obr.x + 7, obr.y + 14, "left")
            self._drawer_zones.append((obr, "open_doc", n.dsd))
            y += 44.0
        else:
            canvas.set_font(ct.FONT_UI, 11.0, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text("No linked evidence yet.", px, y + 12, "left")
            y += 24.0

        y = self._sec(canvas, "RATIONALE", px, y, t)
        y = self._wrap_text(canvas, n.rationale or "—", px, y, pw,
                            ct.FONT_UI, 11.5, "regular", t.dim, 17.0,
                            max_lines=8)
        y += 8.0

        my = [(e, "→", e.target) if e.source == n.id
              else (e, "←", e.source)
              for e in self._edges if n.id in (e.source, e.target)]
        if my:
            y = self._sec(canvas, f"EDGES · {len(my)}", px, y, t)
            canvas.set_font(ct.FONT_MONO, 9.0, "regular")
            for e, arrow, other in my:
                er = Rect(px, y, pw, 24.0)
                canvas.set_fill_color(*t.panel3)
                canvas.fill_round_rect(er.x, er.y, er.w, er.h, 6.0)
                col = {E_SUPERSEDES: t.warn, E_DEPENDS: t.accent,
                       E_RELATES: t.faint, E_DERIVE: t.ok,
                       E_FOLLOWS: t.dim}.get(e.type, t.faint)
                canvas.set_fill_color(*col)
                kind = e.type.lower().replace("_", "-")
                canvas.draw_text(f"{arrow} {kind}", px + 8, y + 15, "left")
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(ct.ellipsize(canvas, other, pw - 130),
                                 px + pw - 8, y + 15, "right")
                self._drawer_zones.append((er, "edge", other))
                y += 28.0
            y += 4.0

        if n.type in ("DEC", "INSIGHT") and not n.ghost:
            y = self._sec(canvas, "CURATE", px, y, t)
            pr = Rect(px, y, 150.0, 28.0)
            canvas.set_fill_color(*t.warn_dim)
            canvas.fill_round_rect(pr.x, pr.y, pr.w, pr.h, 7.0)
            canvas.set_stroke_color(*t.warn_line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(pr.x + 0.5, pr.y + 0.5, pr.w - 1, 27.0, 7.0)
            canvas.set_font(ct.FONT_MONO, 10.5, "regular")
            canvas.set_fill_color(*t.warn)
            canvas.draw_text("▲ Prioritize up", pr.x + 12, pr.y + 18, "left")
            self._drawer_zones.append((pr, "prioritize", n.id))
            canvas.set_fill_color(*t.faint)
            canvas.draw_text("importance: " + (n.importance or "—"),
                             pr.x + pr.w + 10, pr.y + 18, "left")
            y += 36.0
            if n.note:
                canvas.set_fill_color(*t.panel3)
                canvas.fill_round_rect(px, y, pw, 30.0, 6.0)
                canvas.set_fill_color(*t.accent)
                canvas.fill_rect(px, y, 2.0, 30.0)
                canvas.set_font(ct.FONT_UI, 10.5, "regular")
                canvas.set_fill_color(*t.dim)
                canvas.draw_text("\U0001f4dd " + ct.ellipsize(canvas, n.note,
                                 pw - 30), px + 10, y + 19, "left")
                y += 38.0
            nb = Rect(px, y, pw, 30.0)
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(nb.x, nb.y, nb.w, nb.h, 7.0)
            canvas.set_stroke_color(*(t.accent if self._note_focus else t.line2))
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(nb.x + 0.5, nb.y + 0.5, nb.w - 1, 29.0, 7.0)
            canvas.set_font(ct.FONT_UI, 10.5, "regular")
            canvas.set_fill_color(*(t.ink if self._note_draft else t.faint))
            nt = self._note_draft or "Append a note…"
            cur = "|" if self._note_focus else ""
            canvas.draw_text(ct.ellipsize(canvas, nt + cur, pw - 60),
                             nb.x + 9, nb.y + 19, "left")
            ab = Rect(nb.x + nb.w - 44, nb.y + 4, 40, 22)
            # add-note (smaller, on top) MUST be picked before the field zone
            # it overlaps — first-match wins in on_tap.
            self._drawer_zones.append((ab, "add_note", n.id))
            self._drawer_zones.append((nb, "note_focus", ""))
            canvas.set_stroke_color(*t.accent_line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(ab.x + 0.5, ab.y + 0.5, 39, 21, 6.0)
            canvas.set_fill_color(*t.accent)
            canvas.set_font(ct.FONT_MONO, 9.0, "regular")
            canvas.draw_text("Add", ab.x + 9, ab.y + 15, "left")
            y += 40.0
        canvas.restore()

    def _sec(self, canvas, label, x, y, t):
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(label, x, y + 10, "left")
        return y + 20.0

    def _wrap_text(self, canvas, text, x, y, w, font, size, weight, color,
                   line_h, max_lines=6):
        canvas.set_font(font, size, weight)
        canvas.set_fill_color(*color)
        words = str(text).split()
        line = ""
        lines: List[str] = []
        for word in words:
            cand = (line + " " + word).strip()
            if canvas.measure_text(cand).width > w and line:
                lines.append(line)
                line = word
            else:
                line = cand
        if line:
            lines.append(line)
        m = canvas.measure_text("Ag")
        for i, ln in enumerate(lines[:max_lines]):
            if i == max_lines - 1 and len(lines) > max_lines:
                ln = ct.ellipsize(canvas, ln + " …", w)
            canvas.draw_text(ln, x, y + m.ascent, "left")
            y += line_h
        return y

    # -- session Custom overlay -------------------------------------------

    def _draw_session_overlay(self, canvas: Canvas, qrect: Rect, t) -> None:
        ov = Rect(qrect.x + 20, qrect.y + 80, qrect.w - 40, qrect.h - 160)
        canvas.set_shadow(0, 12, 34, t.shadow)
        canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(ov.x, ov.y, ov.w, ov.h, 10.0)
        canvas.clear_shadow()
        canvas.set_stroke_color(*t.line2)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(ov.x + 0.5, ov.y + 0.5, ov.w - 1, ov.h - 1, 10.0)
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("SELECT SESSIONS", ov.x + 14, ov.y + 20, "left")
        cr = Rect(ov.x + ov.w - 28, ov.y + 8, 18, 18)
        canvas.set_font(ct.FONT_MONO, 12.0, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("✕", cr.x + 4, ov.y + 21, "left")
        self._overlay_zones.append((cr, "overlay_close", ""))
        top = ov.y + 34.0
        canvas.save()
        canvas.clip_rect(ov.x, top, ov.w, ov.y + ov.h - top - 8)
        ry = top - self._overlay_scroll
        for s in self._sessions():
            r = Rect(ov.x + 8, ry, ov.w - 16, 26.0)
            if r.y + 26 >= top and r.y <= ov.y + ov.h:
                on = s in self._sessions_sel
                canvas.set_fill_color(*(t.accent_dim if on else t.panel3))
                canvas.fill_round_rect(r.x, r.y, r.w, r.h, 6.0)
                canvas.set_stroke_color(*(t.accent if on else t.line2))
                canvas.set_stroke_width(1.2)
                canvas.stroke_round_rect(r.x + 6, r.y + 6, 14, 14, 3.0)
                if on:
                    canvas.set_fill_color(*t.accent)
                    canvas.fill_round_rect(r.x + 9, r.y + 9, 8, 8, 2.0)
                canvas.set_font(ct.FONT_MONO, 10.0, "regular")
                canvas.set_fill_color(*(t.ink if on else t.dim))
                canvas.draw_text(s, r.x + 28, r.y + 17, "left")
                self._overlay_zones.append((r, "overlay_session", s))
            ry += 30.0
        canvas.restore()

    # =====================================================================
    #  INTERACTION — pick internally against zones from the SAME draw pass
    # =====================================================================

    def on_tap(self, x: float, y: float) -> None:
        self._last_pointer = (x, y)
        # [CXC-GV-CORPUS-SELECTOR] the corpus selector / its overlay get first look
        # (a no-op in the base tab).
        if self._on_extra_tap(x, y):
            return
        if self._session_overlay_open:
            for r, kind, val in self._overlay_zones:
                if self._in(r, x, y):
                    if kind == "overlay_close":
                        self._session_overlay_open = False
                    elif kind == "overlay_session":
                        self._toggle(self._sessions_sel, val)
                    self.request_redraw()
                    return
            return
        if self._drawer_open:
            for r, kind, val in self._drawer_zones:
                if self._in(r, x, y):
                    self._on_drawer(kind, val)
                    return
        # [CXC-DVL-RECENTER] the recenter control re-fits the graph (pure view
        # reset — publishes nothing, mutates no graph state).  Available in
        # every layout mode; the fit is computed for whichever is active.
        if self._recenter_zone and self._in(self._recenter_zone, x, y):
            self._recenter()
            return
        if self._search_zone and self._in(self._search_zone, x, y):
            self._search_focus = True
            self._note_focus = False
            self.request_redraw()
            return
        self._search_focus = False
        for r, group, val in self._chip_zones:
            if self._in(r, x, y):
                self._on_chip(group, val)
                return
        for r, nid in self._result_zones:
            if self._in(r, x, y):
                self._select(nid, open_drawer=True)
                return
        for r, nid in self._node_zones:
            if self._in(r, x, y):
                n = self._by_id.get(nid)
                if n and not n.ghost:
                    self._select(nid, open_drawer=True)
                    self.publish(self.anchor_namespace,
                                 {"anchor": nid, "corpus": self._corpus})
                    self._on_node_anchor(nid)
                return
        if self._drawer_open and self._graph_area \
                and self._in(self._graph_area, x, y):
            self._drawer_open = False
            self.request_redraw()

    def _on_chip(self, group: str, val: str) -> None:
        if group == "type":
            self._toggle(self._types, val)
        elif group == "life":
            self._toggle(self._lifes, val)
        elif group == "lifemode":
            self._life_mode = val
        elif group == "imp":
            self._toggle(self._imps, val)
        elif group == "session":
            self._toggle(self._sessions_sel, val)
        elif group == "session_custom":
            self._session_overlay_open = True
        elif group == "dusk":
            self._dusk = val
        elif group == "match":
            self._match = val
        elif group == "layout":
            self._layout_mode = val
            self._pan_x = self._pan_y = 0.0
            self._zoom = 1.0
        self.request_redraw()

    def _on_drawer(self, kind: str, val: str) -> None:
        if kind == "close":
            self._drawer_open = False
            self._note_focus = False
        elif kind == "edge":
            if val in self._by_id and not self._by_id[val].ghost:
                self._select(val, open_drawer=True)
                return
        elif kind == "open_doc":
            self.publish(self.open_namespace, val)
        elif kind == "prioritize":
            self._curate(val, "prioritize", self._next_importance(val))
        elif kind == "note_focus":
            self._note_focus = True
            self._search_focus = False
        elif kind == "add_note":
            if self._note_draft.strip():
                self._curate(val, "note", self._note_draft.strip())
                self._note_draft = ""
                self._note_focus = False
        self.request_redraw()

    def _next_importance(self, nid: str) -> str:
        n = self._by_id.get(nid)
        cur = (n.importance if n else "") or "low"
        return {"low": "medium", "medium": "high",
                "high": "high"}.get(cur, "medium")

    def _curate(self, nid: str, op: str, value: str) -> None:
        """Publish the curation intent ([SPEC] the write surface).  A curator
        peer (``decisions_curator``) consumes it, calls the decision-ops MCP
        ``decision_update`` (writes the .lts + refreshes the engine), and
        re-publishes ``self/decisions`` so the pane reflects it on re-query.
        Also apply an OPTIMISTIC local echo so the drawer updates immediately;
        the authoritative re-query overwrites it."""
        n = self._by_id.get(nid)
        if n is not None:
            if op == "prioritize":
                n.importance = value
            elif op == "note":
                n.note = value
        self.publish(self.curate_namespace,
                     {"id": nid, "op": op, "value": value,
                      "corpus": self._corpus})

    def _select(self, nid: str, open_drawer: bool = False) -> None:
        self._selected_id = nid
        if open_drawer:
            self._drawer_open = True
        self._note_focus = False
        self.publish(self.selected_namespace, {"id": nid})
        self.request_redraw()

    # -- keyboard (search + note fields) ----------------------------------

    def on_text(self, text: str) -> None:
        if self._search_focus:
            self._q = (self._q + text).lower()
            self.request_redraw()
        elif self._note_focus:
            self._note_draft += text
            self.request_redraw()

    def on_key(self, key: str, modifiers: Any = None) -> None:
        k = str(key)
        if self._search_focus:
            if k in ("Backspace", "\x08"):
                self._q = self._q[:-1]
            elif k in ("Escape", "Return", "Enter"):
                self._search_focus = False
            else:
                return
            self.request_redraw()
        elif self._note_focus:
            if k in ("Backspace", "\x08"):
                self._note_draft = self._note_draft[:-1]
            elif k in ("Escape",):
                self._note_focus = False
            elif k in ("Return", "Enter"):
                if self._note_draft.strip():
                    self._curate(self._selected_id, "note",
                                 self._note_draft.strip())
                    self._note_draft = ""
                    self._note_focus = False
            else:
                return
            self.request_redraw()

    # -- pan / zoom / scroll ----------------------------------------------

    def on_pointer_down(self, x: float, y: float) -> None:
        self._last_pointer = (x, y)

    def on_pan(self, dx: float, dy: float) -> None:
        if self._graph_area and self._last_pointer \
                and self._in(self._graph_area, *self._last_pointer):
            self._pan_x += dx
            self._pan_y += dy
            self.request_redraw()

    def on_scroll(self, dx: float, dy: float) -> None:
        px, _py = self._last_pointer or (0.0, 0.0)
        gx = self._graph_area.x if self._graph_area else QPANE_W
        if self._session_overlay_open:
            self._overlay_scroll = max(0.0, self._overlay_scroll - dy)
        elif px < gx:
            self._results_scroll = max(0.0, self._results_scroll - dy)
        self.request_redraw()

    def on_pinch(self, scale: float, cx: float, cy: float) -> None:
        self._zoom = max(0.4, min(3.0, self._zoom * scale))
        self.request_redraw()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _in(r: Rect, x: float, y: float) -> bool:
        return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h

    @staticmethod
    def _toggle(s: set, v: Any) -> None:
        if v in s:
            s.discard(v)
        else:
            s.add(v)


# ===========================================================================
# GraphViewerWidget — the corpus-agnostic Graph Viewer ([SPEC:CXC-GRAPH-VIEWER]).
# GENERALIZES the Decisions tab: decisions are ONE corpus among many.  Three
# changes carry it (all here): (1) DIRECT lts-engine QUERY replaces the retired
# `self/decisions` pushed feed ([CXC-GV-DATA-PATH]); (2) a dynamic CORPUS SELECTOR
# ([CXC-GV-CORPUS-SELECTOR]); (3) a per-corpus STYLE PROFILE driving the node badge
# ([CXC-GV-STYLE-PROFILE]).  The window / recenter / rail / launcher MECHANICS and
# the graph pane's layout / hit-test / edge inks are REUSED UNCHANGED.
# ===========================================================================

# [CXC-GV-INTENTS] / [DD-GV-INTENT-NAMESPACE] the intent namespace GENERALIZES to
# `_app/graph/*` (control stays on the bus; only the DATA became a query).
GV_ANCHOR_NAMESPACE = "_app/graph/anchor"
GV_REFRESH_NAMESPACE = "_app/graph/refresh"
# [CXC-GV-SCOPE] the OPEN intent is also consumed IN-VIEWER: it may carry a
# {corpus, sessions, scope_label} SCOPE (the Night Watch "View Decisions" button
# scoping the viewer to one watch run).  A bare open (the console rail) carries no
# scope and leaves the viewer unscoped.
GV_OPEN_NAMESPACE = "_app/graph/open"
GV_DEFAULT_CORPUS = "intelligence-console"
_STYLE_ROLES = ("ok", "warn", "accent", "dim", "faint", "ink")
_DEFAULT_STYLES_PATH = os.path.join(os.path.dirname(__file__), "..", "examples",
                                    "graph_viewer.styles.yaml")


class GraphViewerWidget(DecisionsTabWidget):
    """The whole Graph Viewer as ONE interactive widget — the Decisions tab,
    corpus-parameterized + direct-query."""

    # header-pill geometry
    _PILL_H = 18.0
    _PILL_PAD = 8.0
    _PILL_GAP = 10.0

    def __init__(self, widget_id: str, *,
                 corpus: str = "",
                 default_corpus: str = GV_DEFAULT_CORPUS,
                 corpora: Optional[List[Any]] = None,
                 style_profiles_path: str = "",
                 anchor_namespace: str = GV_ANCHOR_NAMESPACE,
                 refresh_namespace: str = GV_REFRESH_NAMESPACE,
                 open_scope_namespace: str = GV_OPEN_NAMESPACE,
                 direct_query: bool = True,
                 projector: Any = None,
                 nodes: Optional[List[Any]] = None,
                 edges: Optional[List[Any]] = None,
                 **kwargs: Any) -> None:
        sel = str(corpus or default_corpus or GV_DEFAULT_CORPUS)
        super().__init__(widget_id, anchor_namespace=anchor_namespace,
                         nodes=nodes, edges=edges, corpus=sel, **kwargs)
        # NOTE: the base sets type "decisions_tab"; re-stamp as the general viewer.
        self.type = "graph_viewer"
        self.refresh_namespace = str(refresh_namespace or GV_REFRESH_NAMESPACE)
        self.open_scope_namespace = str(open_scope_namespace
                                        or GV_OPEN_NAMESPACE)
        self._default_corpus = sel
        # [CXC-GV-SCOPE] the run-scope this viewer was opened with.  `_scope_label`
        # names the scope on the glass; `_scope_note` is the LOUD degradation
        # message when a scope was requested but could not be honoured (never a
        # silent unscoped view pretending to be scoped).
        self._scope_label = ""
        self._scope_note = ""
        self._scope_sessions: List[str] = []
        self._direct_query = bool(direct_query)
        self._styles_path = str(style_profiles_path or _DEFAULT_STYLES_PATH)
        self._projector = projector          # injectable (tests) — else lazy
        self._profiles: Dict[str, Any] = {}
        self._default_profile: Dict[str, Any] = {}
        self._profile: Dict[str, Any] = {}
        self._corpora: List[str] = [str(c) for c in (corpora or []) if c]
        self._corpus_dropdown_open = False
        self._data_error = ""
        self._mode = "full"                  # full | neighborhood
        self._queried = False
        # hit zones for the selector (recorded from the SAME draw pass)
        self._corpus_pill_zone: Optional[Rect] = None
        self._corpus_option_zones: List[Tuple[Rect, str]] = []

    # -- lifecycle ---------------------------------------------------------
    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        self._load_profiles()
        self._profile = self._profiles.get(self._corpus) or self._default_profile
        if self._direct_query and not self._queried:
            self._enumerate_corpora()
            self._query_full(self._corpus)

    def _load_profiles(self) -> None:
        try:
            from composeio.decision_ops.graph_projection import load_style_profiles
            self._profiles, self._default_profile = load_style_profiles(
                self._styles_path)
        except Exception:
            self._profiles, self._default_profile = {}, {}

    def _ensure_projector(self) -> Any:
        if self._projector is None:
            from composeio.decision_ops.graph_projection import GraphProjector
            self._projector = GraphProjector(
                style_profiles=self._profiles,
                default_profile=self._default_profile)
        return self._projector

    # -- corpus enumeration + selection ([CXC-GV-CORPUS-SELECTOR]) --------
    def _enumerate_corpora(self) -> None:
        try:
            self._corpora = list(self._ensure_projector().list_corpora())
        except Exception as exc:  # noqa: BLE001
            self._corpora = list(self._corpora)
            self._data_error = f"{type(exc).__name__}: {exc}"
        if self._corpus not in self._corpora and self._corpus:
            self._corpora = [self._corpus] + self._corpora

    def _select_corpus(self, corpus: str, keep_scope: bool = False) -> None:
        """A selector change triggers a FRESH full-graph query for the new
        corpus ([CXC-GV-QUERY-CALLS]).

        A MANUAL corpus change (the dropdown) drops any run scope: the scope
        named one run's sessions in one corpus, so carrying it into a corpus the
        user just chose by hand would silently filter their new view.  The scoped
        OPEN path passes ``keep_scope=True`` ([CXC-GV-SCOPE])."""
        if not keep_scope:
            self._scope_sessions = []
            self._scope_label = ""
            self._scope_note = ""
            self._sessions_sel = set()
        if not corpus or corpus == self._corpus and self._mode == "full":
            self._corpus_dropdown_open = False
            self.request_redraw()
            return
        self._corpus = corpus
        self._profile = self._profiles.get(corpus) or self._default_profile
        self._corpus_dropdown_open = False
        self._anchor = ""
        self._pan_x = self._pan_y = 0.0
        self._selected_id = ""
        self._drawer_open = False
        self._query_full(corpus)

    # -- the two query modes -----------------------------------------------
    def _apply_projection(self, proj: Dict[str, Any]) -> None:
        self._queried = True
        self._data_error = str(proj.get("error") or "")
        self._anchor = str(proj.get("anchor") or self._anchor)
        self.set_data(proj.get("nodes"), proj.get("edges"))
        # [CXC-GV-SCOPE] a scope seeded BEFORE mount resolves here, once the
        # graph it must match against is actually loaded.
        self._resolve_scope()
        self.request_redraw()

    def _query_full(self, corpus: str) -> None:
        self._mode = "full"
        try:
            proj = self._ensure_projector().full_projection(corpus)
        except Exception as exc:  # noqa: BLE001 — a query failure is a render state
            self.set_data([], [])
            self._data_error = f"lts-engine error: {type(exc).__name__}: {exc}"
            self.request_redraw()
            return
        self._apply_projection(proj)

    def _query_neighborhood(self, corpus: str, anchor: str) -> None:
        self._mode = "neighborhood"
        try:
            proj = self._ensure_projector().neighborhood_projection(corpus, anchor)
        except Exception as exc:  # noqa: BLE001
            self._data_error = f"lts-engine error: {type(exc).__name__}: {exc}"
            self.request_redraw()
            return
        self._apply_projection(proj)

    def refresh(self) -> None:
        """Re-pull the current corpus + anchor ([CXC-GV-FRESHNESS] user/intent
        refresh — NEVER a timer)."""
        proj = self._ensure_projector()
        try:
            proj.refresh()
        except Exception:  # noqa: BLE001
            pass
        self._enumerate_corpora()
        if self._mode == "neighborhood" and self._anchor:
            self._query_neighborhood(self._corpus, self._anchor)
        else:
            self._query_full(self._corpus)

    # -- bus CONTROL only ([CXC-GV-INTENTS] — data is a QUERY, not a feed) --
    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = value
        if isinstance(payload, str):
            s = payload.strip()
            if s and s[0] in "{[":
                try:
                    payload = json.loads(s)
                except (ValueError, TypeError):
                    payload = {}
        d = payload.get("val", payload) if isinstance(payload, dict) else payload
        if namespace == self.refresh_namespace:
            self.refresh()
            return
        if namespace == self.anchor_namespace:
            anchor = None
            corpus = None
            if isinstance(d, dict):
                anchor = d.get("anchor")
                corpus = d.get("corpus")
            elif isinstance(d, str):
                anchor = d
            if corpus and corpus != self._corpus:
                self._select_corpus(str(corpus))
            if anchor:
                self._query_neighborhood(self._corpus, str(anchor))
            return
        if namespace == self.open_scope_namespace:
            self.apply_scope(d)
            return
        # NO self/decisions data subscription — the viewer QUERIES ([CXC-GV-DATA-PATH]).

    # -- run scope ([CXC-GV-SCOPE]) ---------------------------------------
    def apply_scope(self, scope: Any) -> None:
        """Honour an OPEN intent's optional ``{corpus, sessions, scope_label}``
        scope — the Night Watch "View Decisions" path.

        The isolation is a PRESELECT over the EXISTING session filter
        (``_sessions_sel``), not new filtering logic: the same set a user toggles
        by clicking a SESSION chip.  So the scope is always visible and always
        WIDENABLE — a scoped view announces its scope and can be opened up by
        hand; it is never a hidden filter.

        HONEST DEGRADATION: if a scope was requested but no requested session
        matches ANY loaded node's ``decided-in-session``, the viewer opens
        UNSCOPED and says so (``_scope_note``) rather than silently showing the
        whole corpus as if it were this run.  A bare open (no scope) is a no-op."""
        if isinstance(scope, str):
            s = scope.strip()
            if s and s[0] in "{[":
                try:
                    scope = json.loads(s)
                except (ValueError, TypeError):
                    return
            else:
                return
        if not isinstance(scope, dict):
            return
        raw = scope.get("sessions", scope.get("session"))
        sessions: List[str] = []
        if isinstance(raw, (list, tuple, set)):
            sessions = [str(s).strip() for s in raw if str(s or "").strip()]
        elif raw not in (None, ""):
            sessions = [str(raw).strip()]
        corpus = str(scope.get("corpus") or "").strip()
        label = str(scope.get("scope_label") or "").strip()
        if not sessions and not corpus:
            return                      # a bare open — stays unscoped
        if corpus and corpus != self._corpus:
            self._select_corpus(corpus, keep_scope=True)
        self._scope_label = label
        self._scope_sessions = sessions
        self._scope_note = ""
        # ORDERING: the launcher seeds this scope as scene-initial state, which
        # the runtime publishes BEFORE the tree mounts — so the graph is not
        # loaded yet and matching here would falsely report "no matching
        # session".  Resolution is therefore DEFERRED to _resolve_scope(), which
        # runs again after every query lands ([CXC-GV-SCOPE]).
        self._resolve_scope()

    def _resolve_scope(self) -> None:
        """Match the requested scope against the LOADED graph and preselect.
        Idempotent — re-run after each query; a no-op with no scope requested."""
        sessions = list(self._scope_sessions)
        if not sessions:
            self.request_redraw()
            return
        if not self._nodes:
            # data not in yet; stay silent (no false degradation claim) and
            # re-resolve when the projection lands.
            return
        known = {n.session for n in self._nodes if n.session}
        matched = [s for s in sessions if s in known]
        if matched:
            self._sessions_sel = set(matched)
            missing = [s for s in sessions if s not in known]
            self._scope_note = (
                f"{len(missing)} of {len(sessions)} session(s) not in this corpus"
                if missing else "")
        else:
            # LOUD degradation: requested but unmatchable -> unscoped + a note
            self._sessions_sel = set()
            self._scope_note = ("could not scope to this run — "
                                "no matching session in this corpus")
        self.request_redraw()

    def _scope_banner_text(self) -> str:
        """The one-line scope announcement drawn in the graph header, or ""."""
        if self._scope_note:
            base = "UNSCOPED"
            if self._sessions_sel:
                base = f"SCOPED {len(self._sessions_sel)}"
            return f"{base} · {self._scope_note}"
        if self._sessions_sel:
            what = self._scope_label or f"{len(self._sessions_sel)} session(s)"
            return f"SCOPED: {what}"
        return ""

    # -- generalization hooks (override the base no-ops) ------------------
    def _graph_header_label(self) -> str:
        mode = "NEIGHBORHOOD" if self._mode == "neighborhood" else "GRAPH"
        return f"{self._corpus}  {mode}".upper()

    def _empty_state_text(self) -> str:
        if self._data_error:
            return self._data_error
        return "querying lts-engine…"

    def _tab_ink(self, n: "DNode", t):
        try:
            from composeio.decision_ops.graph_projection import resolve_badge_role
        except Exception:
            return None
        role = resolve_badge_role(n.badge, self._profile.get("badge_map") or {})
        if not role:
            return None
        return {"ok": t.ok, "warn": t.warn, "accent": t.accent,
                "dim": t.dim, "faint": t.faint, "ink": t.ink}.get(role)

    def _draw_scope_banner(self, canvas: Canvas, x: float, base: float,
                           t) -> None:
        """Draw the run-scope announcement ([CXC-GV-SCOPE]): an ACCENT pill when
        the view is genuinely scoped, a WARN pill when a scope was requested but
        could not be honoured (the loud degradation — the user must never read an
        unscoped graph as if it were one run's)."""
        text = self._scope_banner_text()
        if not text:
            return
        warn = bool(self._scope_note)
        ink = t.warn if warn else t.accent
        tw = canvas.measure_text(text).width
        h = 15.0
        py = base - 11.0
        canvas.set_fill_color(ink[0], ink[1], ink[2], 34)
        canvas.fill_round_rect(x, py, tw + 14.0, h, 4.0)
        canvas.set_stroke_color(ink[0], ink[1], ink[2], 150)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, py + 0.5, tw + 13.0, h - 1.0, 4.0)
        canvas.set_fill_color(*ink)
        canvas.draw_text(text, x + 7.0, base - 1.0, "left")

    def _draw_corpus_selector(self, canvas: Canvas, x: float, y_top: float,
                              m: Any, t) -> float:
        """A compact corpus dropdown pill in the graph header
        ([CXC-GV-CORPUS-SELECTOR]).  Records its hit zone from THIS draw pass and
        returns the horizontal advance the header label shifts right by."""
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "bold")
        label = f"◤ {self._corpus} ▾"
        tw = canvas.measure_text(label).width
        pill_w = tw + 2 * self._PILL_PAD
        line_h = m.ascent + m.descent
        py = y_top + (line_h - self._PILL_H) / 2.0
        canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(x, py, pill_w, self._PILL_H, 5.0)
        canvas.set_stroke_color(*(t.accent if self._corpus_dropdown_open
                                  else t.line))
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, py + 0.5, pill_w - 1, self._PILL_H - 1,
                                 5.0)
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(label, x + self._PILL_PAD,
                         py + (self._PILL_H - line_h) / 2.0 + m.ascent, "left")
        self._corpus_pill_zone = Rect(x, py, pill_w, self._PILL_H)
        return pill_w + self._PILL_GAP

    def _draw_corpus_overlay(self, canvas: Canvas, grect: Rect, t) -> None:
        self._corpus_option_zones = []
        if not self._corpus_dropdown_open or not self._corpus_pill_zone:
            return
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        pz = self._corpus_pill_zone
        row_h = 22.0
        opts = self._corpora or [self._corpus]
        widest = max((canvas.measure_text(c).width for c in opts), default=80.0)
        ow = max(pz.w, widest + 2 * self._PILL_PAD, 160.0)
        oh = row_h * len(opts) + 8.0
        ox, oy = pz.x, pz.y + pz.h + 4.0
        # a thin drop-shadow panel
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(ox, oy, ow, oh, 6.0)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(ox + 0.5, oy + 0.5, ow - 1, oh - 1, 6.0)
        m = canvas.measure_text("Ag")
        y = oy + 4.0
        for c in opts:
            r = Rect(ox + 3.0, y, ow - 6.0, row_h)
            if c == self._corpus:
                canvas.set_fill_color(*t.panel2)
                canvas.fill_round_rect(r.x, r.y, r.w, r.h, 4.0)
            has_profile = c in self._profiles
            canvas.set_fill_color(*(t.ink if has_profile else t.faint))
            canvas.draw_text(ct.ellipsize(canvas, c, r.w - 16),
                             r.x + 8.0, r.y + (row_h - (m.ascent + m.descent)) / 2
                             + m.ascent, "left")
            self._corpus_option_zones.append((r, c))
            y += row_h
        # engine-unavailable note ([CXC-GV-CORPUS-SELECTOR] ERROR/EMPTY)
        proj = self._projector
        if proj is not None and not getattr(proj, "engine_available", True):
            canvas.set_font(ct.FONT_MONO, ct.NS_TAG, "regular")
            canvas.set_fill_color(*t.warn)
            canvas.draw_text("engine unavailable — repo-local only",
                             ox + 8.0, oy + oh + 12.0, "left")

    def _on_extra_tap(self, x: float, y: float) -> bool:
        if self._corpus_dropdown_open:
            for r, c in self._corpus_option_zones:
                if self._in(r, x, y):
                    self._select_corpus(c)
                    return True
            # a tap off the option list closes it
            self._corpus_dropdown_open = False
            self.request_redraw()
            # fall through only if the pill itself was re-tapped
            if self._corpus_pill_zone and self._in(self._corpus_pill_zone, x, y):
                return True
            return True
        if self._corpus_pill_zone and self._in(self._corpus_pill_zone, x, y):
            self._corpus_dropdown_open = True
            self.request_redraw()
            return True
        return False

    def _on_node_anchor(self, nid: str) -> None:
        """A node tap re-anchors via a DIRECT neighborhood query
        ([CXC-GV-PROJECTION-MODES] re-anchor zoom) — no bus data."""
        if self._direct_query:
            self._query_neighborhood(self._corpus, nid)
