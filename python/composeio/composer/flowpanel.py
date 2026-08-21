"""Flow mode — the Composer's bus-wiring lens ([SPEC:CXC-FLOW]).

Visual target: specs/ux/composer-flow-previs.v2.html.

    FlowPane            the WW-area Flow lens: Bus·Live discovery rail
                        (filter + rows w/ live pulse) + the node graph
                        (namespace hub nodes <-> widget attribute ports,
                        bezier wires).  ONE interactive widget, so
                        drag-to-wire never needs a cross-panel drag
                        primitive (the [CXC-BE-BINDING] escalation
                        boundary stays un-crossed).
    FlowInspectorPanel  the WC "Flow" tab: the selected connection's
                        "what am I listening for" detail ([CXC-FL-SCALE]).
    build_graph(core)   the PURE graph projection of the document +
                        discovery — nodes/wires derive ONLY from the raw
                        spec, so an Intelligence authoring over the bus
                        yields the identical graph ([CXC-FL-C1]/[C9]).

Sync discipline ([CXC-FL-MODEL] / [CXC-BE-RAWSPEC]): every authoring act
here is a bus command into the RAW spec (composer/config/widget_changed,
composer/config/namespace_changed) — the graph never re-serializes from
the parsed tree, and Layout/Bindings/Source stay in lockstep for free.

Node-model forward hook ([CXC-FL-HEADLESS]): FlowNode.kind is
"namespace" | "widget" | "logic" — a widget node is built from id/type/
ports with NO geometry semantics, so a non-visual (headless) widget slots
in as kind="logic" without special-casing.

[SPEC:CXC-FLAUTH] Pass 3 — headless authoring: a layer carrying a
widget-level ``headless:`` block IS a logic node.  build_graph renders it
from the [CXC-FLAUTH-INTROSPECT] table (engine/headless.introspect_ports
— inputs left, outputs right, schedule badge); dragging a hub/rail signal
onto it subscribes ([CXC-FLAUTH-CREATE]); dragging OUT of an output port
onto a hub writes the widget's ``outputs:`` entry ([CXC-FLAUTH-OUTWIRE],
publish wires first-class beyond Button); the ＋ Logic chip creates one;
the Flow inspector authors its exprs (dsl.try_parse live validation),
schedule, and roster deploy — every act a bus command into the RAW spec.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from composeio.bus import is_pattern, pattern_matches

from ..render.canvas import Canvas, Size
from ..render.widget import Constraints, Rect, Widget
from ..render.engine.layout import Container, _contains
from ..render.engine.bindings import (NAMESPACE_STRING_ATTRS,
                                      bindable_attrs_for)

from . import chrome
from .chrome import FONT, MONO, LiveInput, _baseline
from .document import LayerDef

logger = logging.getLogger("composeio.composer.flowpanel")

# palette accents beyond the shared chrome set (previs --live)
LIVE = (0x4B, 0xD6, 0xA0)
LIVE_SOFT = (0x4B, 0xD6, 0xA0, 0x1C)
WIRE = chrome.ACCENT
GRID_DOT = (0xFF, 0xFF, 0xFF, 0x09)
# [CXC-FLAUTH-NODE] logic-node accent (previs --logic)
LOGIC = (0xB9, 0x8C, 0xFF)
LOGIC_SOFT = (0xB9, 0x8C, 0xFF, 0x1C)

RAIL_W = 232.0
_NODE_W = 190.0
_WNODE_W = 200.0
_PORT_R = 4.5
_ROW_H = 40.0


def schedule_summary(sched: dict) -> str:
    """The schedule badge line ([CXC-FLAUTH-NODE]), from the
    [CXC-FLAUTH-INTROSPECT] table's schedule dict.  ASCII-safe (the
    previs ∞/⏱/⚡ glyphs are outside the baked font atlas)."""
    mode = str(sched.get("mode", "continuous"))
    if mode == "periodic":
        iv = float(sched.get("interval_s", 60.0))
        iv_s = f"{int(iv)}" if iv == int(iv) else f"{iv:g}"
        return f"periodic · every {iv_s}s"
    if mode == "bus_triggered":
        trigs = sched.get("triggers") or []
        return ("on " + ", ".join(trigs))[:44] if trigs \
            else "bus-trig · no trigger!"
    return "continuous · 10 Hz"


# ---------------------------------------------------------------------------
# Graph model ([CXC-FL-MODEL], headless-ready)
# ---------------------------------------------------------------------------

@dataclass
class FlowNode:
    node_id: str
    kind: str                        # "namespace" | "widget" | "logic"
    title: str
    subtitle: str = ""
    ports: List[str] = field(default_factory=list)   # widget attr ports
    live: bool = False
    external: bool = False
    pattern: bool = False            # namespace node is a family glob
    value: Any = None
    engine: str = ""
    first_seen: float = 0.0
    rect: Optional[Rect] = None      # assigned by layout (graph coords)
    # [CXC-FLAUTH-NODE] logic nodes: ports are NAMESPACES, from the
    # [CXC-FLAUTH-INTROSPECT] table — inputs left, outputs right — plus
    # the parsed schedule and per-port liveness (the Confirmation tier:
    # a running node's ports are a live readout, [CXC-FLAUTH-CONFIRM]).
    in_ports: List[str] = field(default_factory=list)
    out_ports: List[str] = field(default_factory=list)
    schedule: Optional[dict] = None
    port_values: Dict[str, tuple] = field(default_factory=dict)  # ns -> (live, value)


@dataclass
class FlowWire:
    namespace: str                   # source hub (may be a pattern)
    widget_id: str
    attr: str                        # target port ("value" for subscribe)
    kind: str                        # subscribe | attr | behavior | publish
    detail: Any = None               # raw binding dict / behavior summary
    live: bool = False

    @property
    def key(self) -> tuple:
        return (self.namespace, self.widget_id, self.attr, self.kind)


def build_graph(core: Any) -> Tuple[List[FlowNode], List[FlowWire]]:
    """The document's bus topology as nodes + wires — a pure projection
    of the RAW spec ([CXC-FL-C1]); discovery only decorates liveness.
    Logic nodes ([CXC-FLAUTH-NODE]) derive from the static-introspection
    table ([CXC-FLAUTH-INTROSPECT]) — no hardcoding here."""
    from composeio.render.engine.headless import introspect_ports
    doc = core.document
    disc = getattr(core, "discovery", None)

    def _live(ns_name: str) -> Tuple[bool, Any, str, float]:
        if disc is None:
            return False, None, "", 0.0
        for e in disc.snapshot():
            if e.name == ns_name or (is_pattern(ns_name)
                                     and pattern_matches(ns_name, e.name)):
                return e.live, e.value, e.engine, e.first_seen
        return False, None, "", 0.0

    # [CXC-FLAUTH-INTROSPECT]: the ports/schedule table per logic layer
    logic_info: Dict[str, dict] = {}
    for layer in doc.layers:
        if isinstance(layer.properties.get("headless"), dict):
            logic_info[layer.widget_id] = introspect_ports(
                layer.to_spec(), layer.widget_id)

    wires: List[FlowWire] = []
    seen_keys: set = set()

    def _push(w: FlowWire) -> None:
        if w.key not in seen_keys:
            seen_keys.add(w.key)
            wires.append(w)

    for row in doc.bindings():
        ns = str(row.get("namespace", ""))
        if not ns or ns == "always":
            continue
        kind = row["kind"]
        wid = str(row["widget"])
        if wid in logic_info:
            # logic-node ports ARE namespaces; every read collapses to a
            # subscribe wire into the matching input port, every output
            # to a publish wire out of the matching output port.
            if kind == "publish":
                _push(FlowWire(namespace=ns, widget_id=wid, attr=ns,
                               kind="publish",
                               detail={"attr": row.get("attr"),
                                       "expr": row.get("expr")}))
            else:
                _push(FlowWire(namespace=ns, widget_id=wid, attr=ns,
                               kind="subscribe", detail={"via": kind}))
            continue
        if kind == "behavior":
            attr = f"» {row.get('behavior', '?')}"
            detail: Any = {"behavior": row.get("behavior"),
                           "target": row.get("target")}
        elif kind == "subscribe":
            attr, detail = "value", None
        elif kind == "publish":
            attr, detail = "publish", None
        elif kind == "trigger":
            continue                       # schedules live on logic nodes
        else:
            # attr bindings AND expr reads project onto the bound attr
            attr = str(row.get("attr", "?"))
            layer = doc.get_layer(row["widget"])
            detail = layer.properties.get(attr) if layer else None
        _push(FlowWire(namespace=ns, widget_id=wid,
                       attr=attr, kind=kind, detail=detail))

    # namespace hub nodes: everything wired + everything declared
    ns_names: List[str] = []
    for w in wires:
        if w.namespace not in ns_names:
            ns_names.append(w.namespace)
    for ns in doc.namespaces:
        if ns.name not in ns_names:
            ns_names.append(ns.name)

    nodes: List[FlowNode] = []
    for name in ns_names:
        decl = doc.get_namespace(name)
        live, value, engine, first = _live(name)
        eng = engine
        if decl is not None and not eng:
            eng = decl.state_engine if isinstance(decl.state_engine, str) \
                else decl.state_engine.get("type", "enum")
        sub = f"namespace · {eng}" if eng else "namespace"
        if is_pattern(name):
            sub = f"family · {eng or 'glob'}"
        if decl is not None and decl.external:
            sub += " · external"
        elif decl is None:
            sub += " · undeclared"
        nodes.append(FlowNode(
            node_id=f"ns:{name}", kind="namespace", title=name,
            subtitle=sub, live=live,
            external=bool(decl.external) if decl is not None else True,
            pattern=is_pattern(name), value=value, engine=str(eng),
            first_seen=first))

    for layer in doc.layers:
        info = logic_info.get(layer.widget_id)
        if info is not None:
            # [CXC-FLAUTH-NODE]: inputs left / outputs right / schedule
            # badge — all straight from the introspection table.
            in_ports = [i["namespace"] for i in info["inputs"]]
            out_ports = [o["namespace"] for o in info["outputs"]]
            port_values: Dict[str, tuple] = {}
            for ns in in_ports + out_ports:
                is_live, val, _eng, _fs = _live(ns)
                port_values[ns] = (is_live, val)
            nodes.append(FlowNode(
                node_id=f"w:{layer.widget_id}", kind="logic",
                title=layer.widget_id,
                subtitle="logic · " + schedule_summary(info["schedule"]),
                ports=list(in_ports),
                in_ports=in_ports, out_ports=out_ports,
                schedule=info["schedule"], port_values=port_values,
                live=any(port_values[ns][0] for ns in out_ports)))
            continue
        bound: List[str] = []
        for w in wires:
            if w.widget_id == layer.widget_id \
                    and w.kind in ("attr", "expr") and w.attr not in bound:
                bound.append(w.attr)
        if any(w.widget_id == layer.widget_id and w.kind == "subscribe"
               for w in wires):
            bound.insert(0, "value")
        for w in wires:
            if w.widget_id == layer.widget_id and w.kind == "behavior":
                bound.append(w.attr)
        live = any(w.live for w in wires if w.widget_id == layer.widget_id)
        nodes.append(FlowNode(
            node_id=f"w:{layer.widget_id}", kind="widget",
            title=layer.widget_id,
            subtitle=f"{layer.widget_type} widget",
            ports=bound))

    for w in wires:
        for n in nodes:
            if n.node_id == f"ns:{w.namespace}":
                w.live = n.live
    return nodes, wires


# ---------------------------------------------------------------------------
# FlowPane — rail + graph, one widget
# ---------------------------------------------------------------------------

class FlowPane(Container):
    """The WW Flow lens: Bus·Live rail (left) + node graph (right)."""

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, "flow_pane", **kwargs)
        self.core = core
        self.interactive = True
        # filter state ([CXC-FL-FILTER])
        self.filter_text = ""
        self.filter_category = "all"
        self.search = LiveInput("flow_search", label="",
                                placeholder="filter — name or jumpdrive/*",
                                on_change=self._set_filter_text)
        self.add(self.search)
        # graph state
        self._nodes: List[FlowNode] = []
        self._wires: List[FlowWire] = []
        self._node_pos: Dict[str, Tuple[float, float]] = {}   # drag overrides
        self._rail_rows: List[Tuple[Rect, Any]] = []          # (rect, entry)
        self._chip_zones: List[Tuple[Rect, str]] = []
        self._listen_zone: Optional[Tuple[Rect, str]] = None
        self._logic_btn_zone: Optional[Rect] = None   # ＋ Logic node chip
        self._rail_scroll = 0.0
        self._graph_scroll = 0.0
        self._rail_rect: Optional[Rect] = None
        self._graph_rect: Optional[Rect] = None
        self._count_line = (0, 0)
        # interaction state
        self._drag: Optional[dict] = None       # rail-drag / port-drag / node-drag
        self._hover_expand: str = ""             # widget node expanded ports
        self._selected_wire: Optional[tuple] = None
        self._pulse_t = 0.0

    # -- context ---------------------------------------------------------------

    @property
    def doc(self):
        return self.core.document

    @property
    def discovery(self):
        return getattr(self.core, "discovery", None)

    # -- lifecycle: live pulse via the engine clock -----------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        reg = getattr(ctx, "register_clocked", None)
        if reg is not None:
            reg(self)
        if self.discovery is not None:
            self.discovery.on_update(self._on_discovery_update)
        self.refresh()

    def on_unmount(self) -> None:
        unreg = getattr(self._ctx, "unregister_clocked", None) \
            if self._ctx else None
        if unreg is not None:
            unreg(self)
        super().on_unmount()

    def on_clock_tick(self, now: float) -> bool:
        """Animate the live pulse + flowing wires only while visible."""
        self._pulse_t = now
        return bool(self.visible)

    def _on_discovery_update(self) -> None:
        """Transport-thread callback — marshal a repaint."""
        if self._ctx is not None and self.visible:
            self._ctx.schedule_update(self.request_redraw)

    # -- refresh ---------------------------------------------------------------

    def refresh(self) -> None:
        """Document changed — rebuild the graph projection."""
        self._nodes, self._wires = build_graph(self.core)
        self._layout_graph()
        self.request_redraw()

    def _set_filter_text(self, text: str) -> None:
        self.filter_text = str(text or "").strip()
        self._rail_scroll = 0.0
        self.request_redraw()

    # -- graph auto-layout ([CXC-FL-SCALE] first pass) ---------------------------

    def _node_h(self, n: FlowNode, expanded: bool = False) -> float:
        if n.kind == "namespace":
            return 56.0
        if n.kind == "logic":
            return 40.0 + max(1, len(self._logic_rows(n))) * 17.0
        ports = self._ports_for(n, expanded)
        return 40.0 + max(1, len(ports)) * 17.0

    def _logic_rows(self, n: FlowNode) -> range:
        """Row indices of a logic node's port lanes: inputs left, outputs
        right (+ one phantom '+' out lane when there are no outputs yet,
        so [CXC-FLAUTH-OUTWIRE] has a drag source)."""
        return range(max(len(n.in_ports), len(n.out_ports) or 1, 1))

    def _ports_for(self, n: FlowNode, expanded: bool) -> List[str]:
        if n.kind == "logic":
            return list(n.in_ports)
        if n.kind != "widget" or not expanded:
            return n.ports or (["(unwired)"] if n.kind == "widget" else [])
        layer = self.doc.get_layer(n.title)
        attrs = list(bindable_attrs_for(layer.widget_type)) if layer else []
        if "value" not in attrs:
            attrs.insert(0, "value")
        merged = list(n.ports)
        for a in attrs:
            if a not in merged:
                merged.append(a)
        return merged

    def _layout_graph(self) -> None:
        y_ns, y_w = 24.0, 24.0
        for n in self._nodes:
            expanded = (self._hover_expand == n.node_id)
            h = self._node_h(n, expanded)
            if n.node_id in self._node_pos:
                x, y = self._node_pos[n.node_id]
                n.rect = Rect(x, y, _NODE_W if n.kind == "namespace"
                              else _WNODE_W, h)
                continue
            if n.kind == "namespace":
                n.rect = Rect(24.0, y_ns, _NODE_W, h)
                y_ns += h + 18
            else:
                n.rect = Rect(24.0 + _NODE_W + 120.0, y_w, _WNODE_W, h)
                y_w += h + 18

    def _content_h(self) -> float:
        bottom = 200.0
        for n in self._nodes:
            if n.rect is not None:
                bottom = max(bottom, n.rect.y + n.rect.h + 40)
        return bottom

    # -- port geometry -----------------------------------------------------------

    def _gx(self, x: float) -> float:
        """graph coord -> surface x"""
        return (self._graph_rect.x if self._graph_rect else 0) + x

    def _gy(self, y: float) -> float:
        return (self._graph_rect.y if self._graph_rect else 0) + y \
            - self._graph_scroll

    def _ns_port(self, n: FlowNode) -> Tuple[float, float]:
        return (self._gx(n.rect.x + n.rect.w), self._gy(n.rect.y + 24))

    def _ns_in_port(self, n: FlowNode) -> Tuple[float, float]:
        """A hub's LEFT edge — where publish wires land ([CXC-FLAUTH-
        OUTWIRE])."""
        return (self._gx(n.rect.x), self._gy(n.rect.y + 24))

    def _logic_port(self, n: FlowNode, name: str,
                    side: str) -> Tuple[float, float]:
        """A logic node's port position ([CXC-FLAUTH-NODE]): inputs on
        the left edge, outputs on the right, one lane per port row."""
        ports = n.in_ports if side == "in" else n.out_ports
        try:
            i = ports.index(name)
        except ValueError:
            i = len(ports)          # the phantom '+' out lane
        x = n.rect.x if side == "in" else n.rect.x + n.rect.w
        return (self._gx(x), self._gy(n.rect.y + 40 + i * 17.0 + 8))

    def _widget_port(self, n: FlowNode, attr: str,
                     expanded: bool = False) -> Tuple[float, float]:
        ports = self._ports_for(n, expanded)
        try:
            i = ports.index(attr)
        except ValueError:
            i = 0
        return (self._gx(n.rect.x), self._gy(n.rect.y + 40 + i * 17.0 + 8))

    def _node_at(self, x: float, y: float) -> Optional[FlowNode]:
        for n in reversed(self._nodes):
            if n.rect is None:
                continue
            r = Rect(self._gx(n.rect.x), self._gy(n.rect.y),
                     n.rect.w, n.rect.h)
            if _contains(r, x, y):
                return n
        return None

    def _port_at(self, x: float, y: float) -> Optional[Tuple[FlowNode, str]]:
        """Widget attr port under the pointer (expanded nodes included).
        Logic nodes are handled whole-node (their inputs are namespaces,
        not attrs) — see author_logic_input / _out_port_at."""
        n = self._node_at(x, y)
        if n is None or n.kind != "widget":
            return None
        expanded = (self._hover_expand == n.node_id)
        for attr in self._ports_for(n, expanded):
            px, py = self._widget_port(n, attr, expanded)
            if abs(y - py) <= 8.5 and x <= px + n.rect.w:
                if attr != "(unwired)":
                    return (n, attr)
        return None

    def _out_port_at(self, x: float,
                     y: float) -> Optional[Tuple[FlowNode, str]]:
        """A logic node's OUTPUT port (right edge) under the pointer —
        the [CXC-FLAUTH-OUTWIRE] drag source.  Returns (node, namespace)
        or (node, '+') for the phantom new-output lane."""
        n = self._node_at(x, y)
        if n is None or n.kind != "logic":
            return None
        lanes = list(n.out_ports) or ["+"]
        for name in lanes:
            px, py = self._logic_port(n, name, "out")
            if abs(y - py) <= 8.5 and x >= px - 14:
                return (n, name)
        return None

    def _wire_at(self, x: float, y: float) -> Optional[FlowWire]:
        """Hit-test wires by sampling their beziers ([CXC-FL-SCALE])."""
        best, best_d = None, 8.0
        for w in self._wires:
            pts = self._wire_points(w)
            if pts is None:
                continue
            for t in range(0, 21):
                bx, by = _bezier(pts, t / 20.0)
                d = math.hypot(x - bx, y - by)
                if d < best_d:
                    best, best_d = w, d
        return best

    def _wire_points(self, w: FlowWire):
        ns_node = next((n for n in self._nodes
                        if n.node_id == f"ns:{w.namespace}"), None)
        wd_node = next((n for n in self._nodes
                        if n.node_id == f"w:{w.widget_id}"), None)
        if ns_node is None or wd_node is None or ns_node.rect is None \
                or wd_node.rect is None:
            return None
        if wd_node.kind == "logic":
            if w.kind == "publish":
                # logic output port -> the hub's left edge
                x0, y0 = self._logic_port(wd_node, w.attr, "out")
                x1, y1 = self._ns_in_port(ns_node)
                return (x0, y0, x1, y1)
            x0, y0 = self._ns_port(ns_node)
            x1, y1 = self._logic_port(wd_node, w.attr, "in")
            return (x0, y0, x1, y1)
        x0, y0 = self._ns_port(ns_node)
        expanded = (self._hover_expand == wd_node.node_id)
        x1, y1 = self._widget_port(wd_node, w.attr, expanded)
        return (x0, y0, x1, y1)

    # -- rail data ([CXC-FL-FILTER]) -----------------------------------------------

    def _rail_entries(self):
        disc = self.discovery
        if disc is None:
            return [], 0
        all_entries = disc.snapshot()
        filtered = disc.filter(all_entries, text=self.filter_text,
                               category=self.filter_category)
        return filtered, len(all_entries)

    def _chips(self) -> List[str]:
        disc = self.discovery
        chips = ["All"]
        if disc is not None:
            chips += disc.prefix_chips(3)
        chips += ["external", "active", "in-design"]
        return chips

    def _chip_active(self, chip: str) -> bool:
        if chip == "All":
            return self.filter_category == "all" and not (
                self.filter_text and "*" in self.filter_text)
        if chip.endswith("/*"):
            return self.filter_text == chip
        return self.filter_category == {"in-design": "internal"}.get(chip, chip)

    def _pick_chip(self, chip: str) -> None:
        if chip == "All":
            self.filter_category = "all"
            self.filter_text = ""
            self.search.set_text("")
        elif chip.endswith("/*"):
            self.filter_text = "" if self.filter_text == chip else chip
            self.search.set_text(self.filter_text)
        else:
            cat = {"in-design": "internal"}.get(chip, chip)
            self.filter_category = "all" if self.filter_category == cat \
                else cat
        self._rail_scroll = 0.0
        self.request_redraw()

    # -- authoring acts (bus commands into the RAW spec) --------------------------

    def _ensure_declared(self, ns: str, *, external: bool) -> None:
        if not ns or self.doc.get_namespace(ns) is not None:
            return
        engine: Any = "json"
        disc = self.discovery
        if disc is not None and not is_pattern(ns):
            for e in disc.snapshot():
                if e.name == ns and e.engine and not \
                        e.engine.startswith(("~", "?")):
                    engine = e.engine
                    break
        self.core.internal_bus.publish_event(
            "composer/config/namespace_changed", "do",
            data={"action": "created", "name": ns, "state_engine": engine,
                  "external": external},
            source="flow_pane")

    def declare_expected(self, ns: str) -> None:
        """[CXC-FL-DISCOVERY] declare-expected: subscribe BEFORE the signal
        is live; it activates the instant the first publish arrives."""
        self._ensure_declared(ns, external=True)

    def author_wire(self, ns: str, widget_id: str, attr: str) -> None:
        """Drag-to-wire ([CXC-FL-C6]): the wire IS the binding — emitted in
        the simplest [SPEC:CXC-BIND] form, through the same command the
        Bindings tab publishes."""
        layer = self.doc.get_layer(widget_id)
        if layer is None or not attr:
            return
        kind = self.discovery.kind_of(ns) if self.discovery else "live-available"
        self._ensure_declared(ns, external=(kind == "live-available"))
        if attr == "value" and not is_pattern(ns) and not layer.namespace:
            prop, value = "namespace", ns
        elif attr in NAMESPACE_STRING_ATTRS:
            prop, value = attr, ns
        else:
            prop, value = attr, {"namespace": ns}
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": widget_id, "property": prop, "value": value},
            source="flow_pane")
        self.select_wire(ns, widget_id, attr,
                         "subscribe" if prop == "namespace" else "attr")

    # -- [SPEC:CXC-FLAUTH] logic-node authoring ------------------------------

    def add_logic_node(self) -> str:
        """＋ Logic node ([CXC-FLAUTH-CREATE]): add a named headless
        widget with the default continuous schedule (catalog defaults) —
        the same composer/canvas/widget_added command an Intelligence
        publishes, so both author the byte-identical document."""
        n = 1
        while self.doc.get_layer(f"logic_{n}") is not None:
            n += 1
        wid = f"logic_{n}"
        self.core.internal_bus.publish_event(
            "composer/canvas/widget_added", "do",
            data={"widget_type": "logic", "widget_id": wid, "x": 0, "y": 0},
            source="flow_pane")
        self.select_logic(wid)
        return wid

    def select_logic(self, widget_id: str) -> None:
        """Logic-node selection: the WC Flow tab becomes its inspector
        ([CXC-FLAUTH-EXPR]/[CXC-FLAUTH-SCHEDULE]/[CXC-FLAUTH-DEPLOY])."""
        self._selected_wire = None
        ib = self.core.internal_bus
        ib.publish_state("composer/flow/selected",
                         {"kind": "logic", "widget_id": widget_id},
                         source="flow_pane")
        ib.publish_state("composer/selection/widget_id", widget_id,
                         source="flow_pane")
        ib.publish_state("composer/selection/type", "widget",
                         source="flow_pane")
        ib.publish_state("composer/wc/active_tab", "flow",
                         source="flow_pane")
        self.request_redraw()

    def author_output_wire(self, widget_id: str, ns: str) -> None:
        """[CXC-FLAUTH-OUTWIRE]: an output-port drag onto a namespace hub
        writes the widget's ``outputs:`` entry into the raw spec — the
        publish wire is first-class for ANY widget, not just Button-class.
        Round-trips byte-identically ([CXC-FLAUTH-C3])."""
        layer = self.doc.get_layer(widget_id)
        if layer is None or not ns or is_pattern(ns):
            return
        outputs = [dict(e) for e in (layer.properties.get("outputs") or [])
                   if isinstance(e, dict)]
        if any(str(e.get("namespace", "")) == ns for e in outputs):
            self.select_logic(widget_id)
            return
        # the design PUBLISHES this namespace -> declared-internal
        self._ensure_declared(ns, external=False)
        outputs.append({"namespace": ns})
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": widget_id, "property": "outputs",
                  "value": outputs},
            source="flow_pane")
        # keep the LOGIC NODE selected: the very next act is writing this
        # output's expr in the inspector ([CXC-FLAUTH-EXPR])
        self.select_logic(widget_id)

    def author_logic_input(self, ns: str, widget_id: str) -> None:
        """Hub/rail drag onto a logic node = subscribe ([CXC-FLAUTH-
        CREATE] wire-inputs): the first drop takes the widget's
        ``namespace:`` subscribe; on a bus-triggered node further drops
        join the trigger list; on a clocked node additional inputs are
        authored where they are consumed — as ``ns('...')`` reads in the
        output exprs (the expr editor) — so the drop declares + selects."""
        from composeio.render.engine.headless import introspect_ports
        layer = self.doc.get_layer(widget_id)
        if layer is None or not ns:
            return
        kind = self.discovery.kind_of(ns) if self.discovery \
            else "live-available"
        self._ensure_declared(ns, external=(kind == "live-available"))
        info = introspect_ports(layer.to_spec(), widget_id)
        if any(i["namespace"] == ns for i in info["inputs"]):
            self.select_logic(widget_id)
            return
        ib = self.core.internal_bus
        if not layer.namespace:
            ib.publish_event(
                "composer/config/widget_changed", "do",
                data={"widget_id": widget_id, "property": "namespace",
                      "value": ns},
                source="flow_pane")
        elif info["schedule"]["mode"] == "bus_triggered":
            import copy as _copy
            headless = _copy.deepcopy(layer.properties.get("headless") or {})
            sched = headless.get("schedule")
            if not isinstance(sched, dict):
                sched = {"mode": "bus_triggered"}
                headless["schedule"] = sched
            trig = sched.get("trigger")
            trig_list = [trig] if isinstance(trig, str) \
                else [str(t) for t in (trig or [])]
            trig_list.append(ns)
            sched["trigger"] = trig_list
            ib.publish_event(
                "composer/config/widget_changed", "do",
                data={"widget_id": widget_id, "property": "headless",
                      "value": headless},
                source="flow_pane")
        else:
            self.select_namespace(ns)
            return
        self.select_logic(widget_id)

    def select_wire(self, ns: str, widget_id: str, attr: str,
                    kind: str) -> None:
        """Connection selection is bus state ([CXC-FL-C9])."""
        self._selected_wire = (ns, widget_id, attr, kind)
        ib = self.core.internal_bus
        ib.publish_state("composer/flow/selected",
                         {"kind": "connection", "namespace": ns,
                          "widget_id": widget_id, "attr": attr,
                          "wire_kind": kind},
                         source="flow_pane")
        ib.publish_state("composer/wc/active_tab", "flow", source="flow_pane")
        self.request_redraw()

    def select_namespace(self, ns: str) -> None:
        self._selected_wire = None
        ib = self.core.internal_bus
        ib.publish_state("composer/flow/selected",
                         {"kind": "namespace", "namespace": ns},
                         source="flow_pane")
        ib.publish_state("composer/wc/active_tab", "flow", source="flow_pane")
        self.request_redraw()

    # -- layout -------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        self._rail_rect = Rect(inner.x, inner.y, RAIL_W, inner.h)
        self._graph_rect = Rect(inner.x + RAIL_W, inner.y,
                                max(0.0, inner.w - RAIL_W), inner.h)
        # the filter input sits in the rail below header+hint
        self.search.arranged_rect = Rect(inner.x + 8, inner.y + 64,
                                         RAIL_W - 16, 40)
        self._layout_graph()

    # -- interaction ---------------------------------------------------------------

    def hit_test_tree(self, x: float, y: float) -> Optional[Widget]:
        r = self.arranged_rect
        if r is None or not _contains(r, x, y):
            return None
        sr = self.search.arranged_rect
        if sr is not None and _contains(sr, x, y):
            return self.search
        return self

    def on_pointer_down(self, x: float, y: float) -> None:
        self._drag = None
        if self._rail_rect and _contains(self._rail_rect, x, y):
            for rect, entry in self._rail_rows:
                if _contains(rect, x, y):
                    self._drag = {"mode": "rail", "entry": entry,
                                  "x": x, "y": y, "moved": False}
                    return
            return
        # graph: port drag beats node drag
        out_hit = self._out_port_at(x, y)
        if out_hit is not None:
            # [CXC-FLAUTH-OUTWIRE] drag FROM a logic output port
            n, port = out_hit
            self._drag = {"mode": "out-port", "widget": n.title,
                          "port": port, "x": x, "y": y, "moved": False}
            return
        hit = self._port_at(x, y)
        if hit is not None:
            n, attr = hit
            self._drag = {"mode": "port", "widget": n.title, "attr": attr,
                          "x": x, "y": y, "moved": False}
            return
        n = self._node_at(x, y)
        if n is not None:
            if n.kind == "namespace":
                self._drag = {"mode": "ns-port", "ns": n.title,
                              "x": x, "y": y, "moved": False}
            else:
                self._drag = {"mode": "node", "node": n, "x": x, "y": y,
                              "ox": n.rect.x, "oy": n.rect.y, "moved": False}

    def on_pan(self, dx: float, dy: float) -> None:
        d = self._drag
        if d is None:
            return
        d["moved"] = True
        d["cx"], d["cy"] = d["x"] + dx, d["y"] + dy
        if d["mode"] == "node":
            n = d["node"]
            self._node_pos[n.node_id] = (d["ox"] + dx, d["oy"] + dy)
            self._layout_graph()
        elif d["mode"] in ("rail", "ns-port"):
            # expand a hovered widget node so the drop can pick an exact port
            n = self._node_at(d["cx"], d["cy"])
            want = n.node_id if n is not None and n.kind == "widget" else ""
            if want != self._hover_expand:
                self._hover_expand = want
                self._layout_graph()
        self.request_redraw()

    def on_pan_end(self) -> None:
        d, self._drag = self._drag, None
        expand, self._hover_expand = self._hover_expand, ""
        if d is None or not d.get("moved"):
            self._layout_graph()
            self.request_redraw()
            return
        cx, cy = d.get("cx", d["x"]), d.get("cy", d["y"])
        if d["mode"] in ("rail", "ns-port"):
            ns = d["entry"].name if d["mode"] == "rail" else d["ns"]
            target = self._node_at(cx, cy)
            if target is not None and target.kind == "logic":
                # [CXC-FLAUTH-CREATE] drop on a logic node = subscribe
                self.author_logic_input(ns, target.title)
                self._layout_graph()
                self.request_redraw()
                return
            self._hover_expand = expand      # port lookup needs expansion
            hit = self._port_at(cx, cy)
            self._hover_expand = ""
            if hit is not None:
                node, attr = hit
                self.author_wire(ns, node.title, attr)   # discover-AND-wire
            elif self._graph_rect and _contains(self._graph_rect, cx, cy) \
                    and d["mode"] == "rail":
                # dropped on open canvas: start listening (declare/subscribe)
                kind = self.discovery.kind_of(ns) if self.discovery else ""
                self._ensure_declared(ns,
                                      external=(kind == "live-available"))
                self.select_namespace(ns)
        elif d["mode"] == "out-port":
            # [CXC-FLAUTH-OUTWIRE] release on a namespace hub = publish
            target = self._node_at(cx, cy)
            if target is not None and target.kind == "namespace" \
                    and not target.pattern:
                self.author_output_wire(d["widget"], target.title)
        self._layout_graph()
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        # ＋ Logic node ([CXC-FLAUTH-CREATE])
        if self._logic_btn_zone is not None and \
                _contains(self._logic_btn_zone, x, y):
            self.add_logic_node()
            return
        # rail chips / + listen / rows
        for rect, chip in self._chip_zones:
            if _contains(rect, x, y):
                self._pick_chip(chip)
                return
        if self._listen_zone is not None and \
                _contains(self._listen_zone[0], x, y):
            ns = self._listen_zone[1]
            self.declare_expected(ns)
            self.select_namespace(ns)
            return
        if self._rail_rect and _contains(self._rail_rect, x, y):
            for rect, entry in self._rail_rows:
                if _contains(rect, x, y):
                    self.select_namespace(entry.name)
                    return
            return
        # graph: wire, then node
        w = self._wire_at(x, y)
        if w is not None:
            self.select_wire(w.namespace, w.widget_id, w.attr, w.kind)
            return
        n = self._node_at(x, y)
        if n is not None:
            if n.kind == "namespace":
                self.select_namespace(n.title)
            elif n.kind == "logic":
                self.select_logic(n.title)
            else:
                ib = self.core.internal_bus
                ib.publish_state("composer/selection/widget_id", n.title,
                                 source="flow_pane")
                ib.publish_state("composer/selection/type", "widget",
                                 source="flow_pane")
            return
        self._selected_wire = None
        self.core.internal_bus.publish_state("composer/flow/selected", {},
                                             source="flow_pane")
        self.request_redraw()

    def on_scroll(self, dx: float, dy: float) -> None:
        # scroll whichever region the pointer... (no pointer here: use last
        # known region via rail hover state is overkill — scroll the graph,
        # rail rows scroll with the wheel when the list overflows and the
        # graph fits).  Pragmatic: rail scroll when rail overflows.
        rows_h = len(self._rail_rows) * (_ROW_H + 5)
        if self._rail_rect is not None and rows_h > self._rail_rect.h - 170:
            self._rail_scroll = max(0.0, self._rail_scroll - dy * 24)
        self._graph_scroll = max(0.0, min(
            self._graph_scroll - dy * 24,
            max(0.0, self._content_h()
                - (self._graph_rect.h if self._graph_rect else 400))))
        self.request_redraw()

    # -- drawing --------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        self._draw_graph(canvas)
        self._draw_rail(canvas)
        if self.search.arranged_rect is not None:
            self.search.draw(canvas, self.search.arranged_rect)
        # drag ghost
        d = self._drag
        if d is not None and d.get("moved") and d["mode"] == "out-port":
            # [CXC-FLAUTH-OUTWIRE] publish-wire ghost
            cx, cy = d.get("cx", d["x"]), d.get("cy", d["y"])
            canvas.set_stroke_color(*chrome.ACCENT)
            canvas.set_stroke_width(2)
            canvas.draw_line(d["x"], d["y"], cx, cy)
        if d is not None and d.get("moved") and d["mode"] in ("rail",
                                                              "ns-port"):
            ns = d["entry"].name if d["mode"] == "rail" else d["ns"]
            cx, cy = d.get("cx", d["x"]), d.get("cy", d["y"])
            canvas.set_stroke_color(*LIVE)
            canvas.set_stroke_width(2)
            canvas.draw_line(d["x"], d["y"], cx, cy)
            w = len(ns) * 6.3 + 16
            canvas.set_fill_color(*chrome.PANEL)
            canvas.fill_round_rect(cx + 8, cy - 10, w, 20, 6)
            canvas.set_stroke_color(*LIVE)
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(cx + 8, cy - 10, w, 20, 6)
            canvas.set_fill_color(*LIVE)
            canvas.set_font(MONO, 10.5, "regular")
            canvas.draw_text(ns, cx + 16, _baseline(canvas, cy), "left")
        canvas.restore()

    # -- rail drawing --------------------------------------------------------------

    def _pulse_alpha(self, phase: float = 0.0) -> int:
        # previs beat: 2s cycle, opacity 1 -> .35
        t = (self._pulse_t / 1.6 + phase) % 1.0
        a = 1.0 - 0.65 * (0.5 - 0.5 * math.cos(t * 2 * math.pi))
        return int(a * 255)

    def _draw_rail(self, canvas: Canvas) -> None:
        r = self._rail_rect
        if r is None:
            return
        canvas.save()
        canvas.set_fill_color(*chrome.PANEL)
        canvas.fill_rect(r.x, r.y, r.w, r.h)
        canvas.set_stroke_color(*chrome.BORDER)
        canvas.set_stroke_width(1)
        canvas.draw_line(r.x + r.w, r.y, r.x + r.w, r.y + r.h)

        x = r.x + 12
        # header: pulse pip + BUS · LIVE
        canvas.set_fill_color(*LIVE, self._pulse_alpha())
        canvas.fill_circle(x + 4, r.y + 16, 3.5)
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 10.5, "medium")
        canvas.draw_text("BUS · LIVE", x + 14, r.y + 20, "left")
        canvas.set_fill_color(*chrome.DIM)
        canvas.set_font(FONT, 10, "regular")
        canvas.draw_text("On the ComposeBus right now — incl.", x, r.y + 38,
                         "left")
        canvas.draw_text("undeclared traffic. Drag one on to listen.", x,
                         r.y + 51, "left")

        # the search input draws itself (child); chips row under it
        entries, total = self._rail_entries()
        cy = r.y + 110
        cx = x
        self._chip_zones = []
        canvas.set_font(MONO, 10, "regular")
        for chip in self._chips():
            w = len(chip) * 6.0 + 16
            if cx + w > r.x + r.w - 8:
                cx = x
                cy += 24
            on = self._chip_active(chip)
            canvas.set_fill_color(*(chrome.ACCENT_SOFT if on
                                    else chrome.PANEL2))
            canvas.fill_round_rect(cx, cy, w, 19, 9)
            canvas.set_stroke_color(*(chrome.ACCENT if on else chrome.BORDER))
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(cx, cy, w, 19, 9)
            canvas.set_fill_color(*(chrome.ACCENT if on else chrome.DIM))
            canvas.draw_text(chip, cx + w / 2, _baseline(canvas, cy + 9.5),
                             "center")
            self._chip_zones.append((Rect(cx, cy, w, 19), chip))
            cx += w + 5
        cy += 26

        # showing N of M ([CXC-FL-C5])
        self._count_line = (len(entries), total)
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 10, "regular")
        canvas.draw_text(f"showing {len(entries)} of {total} namespaces",
                         x, cy + 8, "left")
        cy += 18

        # declare-expected affordance: filter text names something not live
        self._listen_zone = None
        ft = self.filter_text
        if ft and not any(e.name == ft or (is_pattern(ft) and
                                           pattern_matches(ft, e.name))
                          for e in entries) \
                and self.doc.get_namespace(ft) is None \
                and ("/" in ft or is_pattern(ft)):
            zr = Rect(x, cy + 2, r.w - 24, 26)
            canvas.set_fill_color(*LIVE_SOFT)
            canvas.fill_round_rect(zr.x, zr.y, zr.w, zr.h, 7)
            canvas.set_stroke_color(*LIVE)
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(zr.x, zr.y, zr.w, zr.h, 7)
            canvas.set_fill_color(*LIVE)
            canvas.set_font(MONO, 10, "regular")
            label = f"+ listen for {ft}"
            canvas.draw_text(label[:34], zr.x + 8,
                             _baseline(canvas, zr.y + 13), "left")
            self._listen_zone = (zr, ft)
            cy += 30

        # rows
        rows_top = cy + 4
        canvas.save()
        canvas.clip_rect(r.x, rows_top, r.w - 1, r.y + r.h - rows_top)
        self._rail_rows = []
        yy = rows_top - self._rail_scroll
        now = time.time()
        for e in entries:
            rr = Rect(x - 4, yy, r.w - 16, _ROW_H)
            self._rail_rows.append((rr, e))
            if yy + _ROW_H >= rows_top and yy <= r.y + r.h:
                self._draw_rail_row(canvas, rr, e, now)
            yy += _ROW_H + 5
        canvas.restore()
        canvas.restore()

    def _draw_rail_row(self, canvas: Canvas, r: Rect, e: Any,
                       now: float) -> None:
        kind = self.discovery.kind_of(e.name) if self.discovery else ""
        wired = self.discovery.is_wired(e.name) if self.discovery else False
        live = e.live
        canvas.set_fill_color(*(LIVE_SOFT if live else chrome.PANEL2))
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, 8)
        canvas.set_stroke_color(*(LIVE if live else chrome.BORDER),
                                0x55 if live else 0xFF)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(r.x, r.y, r.w, r.h, 8)
        # dot: live pulse / internal faint / expected hollow
        dx, dy = r.x + 11, r.y + 13
        if live:
            canvas.set_fill_color(*LIVE, self._pulse_alpha(0.3))
            canvas.fill_circle(dx, dy, 3)
        elif kind == "expected":
            canvas.set_stroke_color(*LIVE, 0x99)
            canvas.set_stroke_width(1.2)
            canvas.stroke_circle(dx, dy, 3)
        else:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.fill_circle(dx, dy, 3)
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(MONO, 10.5, "medium")
        canvas.draw_text(e.name[:26], r.x + 20, r.y + 17, "left")
        # engine · value  +  tag
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(MONO, 9, "regular")
        canvas.draw_text(str(e.engine)[:14], r.x + 20, r.y + 32, "left")
        val = "—" if e.value is None else str(e.value)
        canvas.set_fill_color(*chrome.DIM)
        canvas.draw_text(val[:10], r.x + 108, r.y + 32, "left")
        tag, tc = "", chrome.FAINT
        if wired:
            tag, tc = "wired", chrome.ACCENT
        elif kind == "declared-internal":
            tag, tc = "in-design", chrome.FAINT
        elif kind == "expected":
            tag, tc = "expected", LIVE
        elif kind == "subscribed-external":
            tag, tc = "listening", LIVE
        elif live:
            tag, tc = "+ listen", LIVE
        if tag:
            canvas.set_fill_color(*tc)
            canvas.set_font(FONT, 8.5, "medium")
            canvas.draw_text(tag, r.x + r.w - 8,
                             _baseline(canvas, r.y + 27), "right")

    # -- graph drawing ---------------------------------------------------------------

    def _draw_graph(self, canvas: Canvas) -> None:
        g = self._graph_rect
        if g is None:
            return
        canvas.save()
        canvas.set_fill_color(*chrome.CANVAS_BG)
        canvas.fill_rect(g.x, g.y, g.w, g.h)
        # dot grid
        canvas.set_fill_color(*GRID_DOT)
        step = 22
        oy = int(self._graph_scroll) % step
        yy = g.y - oy
        while yy < g.y + g.h:
            xx = g.x + 10
            while xx < g.x + g.w:
                canvas.fill_rect(xx, yy, 1.2, 1.2)
                xx += step
            yy += step
        canvas.clip_rect(g.x, g.y, g.w, g.h)

        if not self._nodes:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 12, "regular")
            canvas.draw_text("No wiring yet — drag a live namespace from",
                             g.x + g.w / 2, g.y + g.h / 2 - 10, "center")
            canvas.draw_text("the Bus·Live rail onto the canvas to listen.",
                             g.x + g.w / 2, g.y + g.h / 2 + 8, "center")
            self._draw_logic_btn(canvas, g)
            canvas.restore()
            return

        # wires under nodes
        dash_off = -(self._pulse_t * 26.0) % 28.0
        for w in self._wires:
            pts = self._wire_points(w)
            if pts is None:
                continue
            sel = self._selected_wire == w.key
            color = LIVE if w.live else WIRE
            self._draw_bezier(canvas, pts, color,
                              width=2.6 if sel else 1.8,
                              alpha=0xFF if sel else 0xD0,
                              flowing=w.live, dash_off=dash_off)
            for (px, py) in (pts[:2], pts[2:]):
                canvas.set_fill_color(*chrome.PANEL)
                canvas.fill_circle(px, py, _PORT_R)
                canvas.set_stroke_color(*color)
                canvas.set_stroke_width(2)
                canvas.stroke_circle(px, py, _PORT_R)

        for n in self._nodes:
            self._draw_node(canvas, n)
        self._draw_logic_btn(canvas, g)
        canvas.restore()

    def _draw_logic_btn(self, canvas: Canvas, g: Rect) -> None:
        """＋ Logic node affordance ([CXC-FLAUTH-CREATE]) — fixed chrome
        at the graph's top-right, above the scrolled content."""
        bw, bh = 96.0, 22.0
        bz = Rect(g.x + g.w - bw - 12, g.y + 10, bw, bh)
        self._logic_btn_zone = bz
        canvas.set_fill_color(*LOGIC_SOFT)
        canvas.fill_round_rect(bz.x, bz.y, bz.w, bz.h, 7)
        canvas.set_stroke_color(*LOGIC, 0x99)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(bz.x, bz.y, bz.w, bz.h, 7)
        canvas.set_fill_color(*LOGIC)
        canvas.set_font(MONO, 10.5, "medium")
        canvas.draw_text("+ Logic node", bz.x + bz.w / 2,
                         _baseline(canvas, bz.y + bz.h / 2), "center")

    def _draw_bezier(self, canvas: Canvas, pts, color, *, width: float,
                     alpha: int, flowing: bool, dash_off: float) -> None:
        x0, y0, x1, y1 = pts
        mx = (x0 + x1) / 2
        canvas.set_stroke_width(width)
        canvas.set_stroke_color(*color, alpha)
        prev = (x0, y0)
        n_seg = 22
        for i in range(1, n_seg + 1):
            t = i / n_seg
            p = _bezier(pts, t)
            if flowing:
                # dashed "flow" styling: skip alternating arc-length chunks
                phase = ((t * 7 * 28.0) + dash_off) % 28.0
                if phase < 18.0:
                    canvas.draw_line(prev[0], prev[1], p[0], p[1])
            else:
                canvas.draw_line(prev[0], prev[1], p[0], p[1])
            prev = p

    def _draw_node(self, canvas: Canvas, n: FlowNode) -> None:
        r = Rect(self._gx(n.rect.x), self._gy(n.rect.y), n.rect.w, n.rect.h)
        selected = False
        if self._selected_wire is not None:
            selected = n.node_id in (f"ns:{self._selected_wire[0]}",
                                     f"w:{self._selected_wire[1]}")
        if n.kind == "logic" and not selected:
            sel = self.core.internal_bus.get_state("composer/flow/selected")
            selected = isinstance(sel, dict) and sel.get("kind") == "logic" \
                and str(sel.get("widget_id", "")) == n.title
        canvas.set_fill_color(*chrome.PANEL)
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, 10)
        if n.kind == "logic":
            border = LOGIC if selected else \
                (LIVE if n.live else chrome.BORDER_STRONG)
        else:
            border = chrome.ACCENT if selected else \
                (LIVE if n.live else chrome.BORDER_STRONG)
        canvas.set_stroke_color(*border, 0xFF if selected else 0x88)
        canvas.set_stroke_width(1.4 if selected else 1)
        canvas.stroke_round_rect(r.x, r.y, r.w, r.h, 10)
        if n.kind == "logic":
            # the previs left accent bar
            canvas.set_fill_color(*LOGIC)
            canvas.fill_round_rect(r.x, r.y, 4, r.h, 2)

        # header
        ic_color = LIVE if (n.kind == "namespace" and n.live) else \
            (LOGIC if n.kind == "logic" else chrome.ACCENT)
        canvas.set_fill_color(*chrome.PANEL3)
        canvas.fill_round_rect(r.x + 8, r.y + 7, 18, 18, 5)
        canvas.set_fill_color(*ic_color)
        canvas.set_font(MONO, 10, "medium")
        glyph = "◎" if n.kind == "namespace" else ("λ" if n.kind == "logic"
                                                   else "▤")
        # engine glyphs are outside the atlas — use ASCII-safe marks
        glyph = {"namespace": "()", "logic": "fx", "widget": "[]"}[n.kind] \
            if n.kind in ("namespace", "logic", "widget") else "?"
        canvas.draw_text(glyph, r.x + 17, _baseline(canvas, r.y + 16),
                         "center")
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(MONO, 11, "medium")
        title = n.title if len(n.title) <= 24 else n.title[:23] + "…"
        canvas.draw_text(title, r.x + 32, r.y + 15, "left")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 9, "regular")
        canvas.draw_text(n.subtitle[:36], r.x + 32, r.y + 28, "left")
        if n.live:
            canvas.set_fill_color(*LIVE, self._pulse_alpha(0.15))
            canvas.fill_circle(r.x + r.w - 12, r.y + 14, 3.5)

        canvas.set_stroke_color(*chrome.BORDER)
        canvas.set_stroke_width(1)
        canvas.draw_line(r.x + 6, r.y + 34, r.x + r.w - 6, r.y + 34)

        if n.kind == "namespace":
            canvas.set_font(MONO, 10.5, "regular")
            if n.live:
                canvas.set_fill_color(*chrome.FAINT)
                canvas.draw_text("now", r.x + 10, r.y + 49, "left")
                canvas.set_fill_color(*LIVE)
                val = "—" if n.value is None else str(n.value)
                canvas.draw_text(val[:18], r.x + 40, r.y + 49, "left")
            else:
                canvas.set_fill_color(*chrome.FAINT)
                txt = "expected — not yet live" if n.external else "silent"
                if n.pattern:
                    txt = "family subscription (glob)"
                canvas.draw_text(txt, r.x + 10, r.y + 49, "left")
        elif n.kind == "logic":
            self._draw_logic_lanes(canvas, n, r)
        else:
            expanded = (self._hover_expand == n.node_id)
            ports = self._ports_for(n, expanded)
            yy = r.y + 48
            canvas.set_font(MONO, 10, "regular")
            for attr in ports:
                bound_wire = next(
                    (w for w in self._wires
                     if w.widget_id == n.title and w.attr == attr), None)
                if attr == "(unwired)":
                    canvas.set_fill_color(*chrome.FAINT)
                    canvas.draw_text("no bindings yet", r.x + 12, yy, "left")
                else:
                    canvas.set_fill_color(*chrome.TEXT if bound_wire
                                          else chrome.DIM)
                    canvas.draw_text(attr, r.x + 12, yy, "left")
                    if bound_wire is not None:
                        canvas.set_fill_color(*chrome.ACCENT)
                        canvas.set_font(FONT, 8.5, "regular")
                        src = bound_wire.namespace.split("/")[-1]
                        canvas.draw_text(f"← {src}"[:20], r.x + r.w - 10,
                                         yy, "right")
                        canvas.set_font(MONO, 10, "regular")
                yy += 17.0

    def _draw_logic_lanes(self, canvas: Canvas, n: FlowNode,
                          r: Rect) -> None:
        """A logic node's port lanes ([CXC-FLAUTH-NODE]): input ports on
        the left edge, output ports on the right, live values inline —
        the running node is a live readout ([CXC-FLAUTH-CONFIRM] C9)."""
        canvas.set_font(MONO, 9, "regular")
        for i in self._logic_rows(n):
            yy = r.y + 40 + i * 17.0 + 8
            if i < len(n.in_ports):
                ns = n.in_ports[i]
                is_live, val = n.port_values.get(ns, (False, None))
                canvas.set_fill_color(*chrome.PANEL)
                canvas.fill_circle(r.x, yy, _PORT_R)
                canvas.set_stroke_color(*(LIVE if is_live else chrome.DIM))
                canvas.set_stroke_width(1.6)
                canvas.stroke_circle(r.x, yy, _PORT_R)
                tail = ns.split("/")[-1][:11]
                canvas.set_fill_color(*(chrome.TEXT if is_live
                                        else chrome.DIM))
                canvas.draw_text(tail, r.x + 12,
                                 _baseline(canvas, yy), "left")
                if is_live and val is not None:
                    canvas.set_fill_color(*LIVE)
                    canvas.draw_text(str(val)[:6], r.x + 12 + len(tail) * 5.6
                                     + 5, _baseline(canvas, yy), "left")
            lanes_out = n.out_ports or ["+"]
            if i < len(lanes_out):
                ns = lanes_out[i]
                is_out_port = ns != "+"
                is_live, val = n.port_values.get(ns, (False, None)) \
                    if is_out_port else (False, None)
                canvas.set_fill_color(*chrome.PANEL)
                canvas.fill_circle(r.x + r.w, yy, _PORT_R)
                canvas.set_stroke_color(*(chrome.ACCENT if is_out_port
                                          else chrome.FAINT))
                canvas.set_stroke_width(1.6)
                canvas.stroke_circle(r.x + r.w, yy, _PORT_R)
                if is_out_port:
                    tail = ns.split("/")[-1][:11]
                    lx = r.x + r.w - 12
                    if is_live and val is not None:
                        canvas.set_fill_color(*LIVE)
                        canvas.draw_text(str(val)[:6], lx,
                                         _baseline(canvas, yy), "right")
                        lx -= len(str(val)[:6]) * 5.6 + 5
                    canvas.set_fill_color(*chrome.ACCENT
                                          if is_live else chrome.DIM)
                    canvas.draw_text(tail, lx, _baseline(canvas, yy),
                                     "right")
                else:
                    canvas.set_fill_color(*chrome.FAINT)
                    canvas.draw_text("+ out", r.x + r.w - 12,
                                     _baseline(canvas, yy), "right")


def _bezier(pts, t: float) -> Tuple[float, float]:
    x0, y0, x1, y1 = pts
    mx = (x0 + x1) / 2
    # cubic with horizontal control handles (the previs wire shape)
    u = 1 - t
    bx = (u ** 3) * x0 + 3 * (u ** 2) * t * mx + 3 * u * (t ** 2) * mx \
        + (t ** 3) * x1
    by = (u ** 3) * y0 + 3 * (u ** 2) * t * y0 + 3 * u * (t ** 2) * y1 \
        + (t ** 3) * y1
    return bx, by


# ---------------------------------------------------------------------------
# FlowInspectorPanel — the WC "Flow" tab ([CXC-FL-SCALE] Inspector)
# ---------------------------------------------------------------------------

class FlowInspectorPanel(Container):
    """The selected connection: source, engine, first-seen, when/map/
    default — exactly "what am I listening for".

    [SPEC:CXC-FLAUTH]: selecting a LOGIC NODE turns this tab into its
    authoring surface — schedule picker ([CXC-FLAUTH-SCHEDULE]), live
    ports ([CXC-FLAUTH-CONFIRM]), the inline per-output ``expr`` editor
    live-validated by the DSL parser ([CXC-FLAUTH-EXPR]), the deploy
    card ([CXC-FLAUTH-DEPLOY]), and the "Inspect ▸" affordance — since
    [SPEC:CXC-HMON] wired to the Headless Monitor pop-out (Diagnosis,
    [CXC-HW-INTROSPECT])."""

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, "flow_inspector", **kwargs)
        self.core = core
        self.interactive = True
        self.scroll = 0.0
        self._zones: List[Tuple[Rect, Callable[[], None]]] = []
        # -- logic-node authoring state ([SPEC:CXC-FLAUTH]) ---------------
        self._sel_output: str = ""          # output namespace being edited
        self.expr_state: Tuple[bool, str] = (True, "")
        self.inspect_taps = 0       # "Inspect ▸" taps ([SPEC:CXC-HMON])
        self._seeded_for: tuple = ("", "")
        self._inputs: Dict[str, LiveInput] = {}
        self._mk_inputs()

    def _mk_inputs(self) -> None:
        specs = {
            "expr": dict(placeholder="expr — e.g. ns('a/b') * 2",
                         on_change=self._on_expr_change,
                         commit=self._commit_expr),
            "interval": dict(placeholder="interval seconds",
                             commit=self._commit_interval),
            "triggers": dict(placeholder="trigger ns, comma-separated",
                             commit=self._commit_triggers),
            "add_out": dict(placeholder="+ output namespace",
                            commit=self._commit_add_output),
            "runtime": dict(placeholder="runtime id",
                            commit=self._commit_runtime),
        }
        for name, kw in specs.items():
            commit = kw.pop("commit", None)
            fi = LiveInput(f"fli_{name}", label="", **kw)
            if commit is not None:
                fi.on_commit = commit
            fi.visible = False
            self._inputs[name] = fi
            self.add(fi)

    @property
    def doc(self):
        return self.core.document

    def _selected(self) -> dict:
        v = self.core.internal_bus.get_state("composer/flow/selected")
        return v if isinstance(v, dict) else {}

    def _logic_layer(self):
        sel = self._selected()
        if sel.get("kind") != "logic":
            return None
        layer = self.doc.get_layer(str(sel.get("widget_id", "")))
        if layer is None or not isinstance(layer.properties.get("headless"),
                                           dict):
            return None
        return layer

    def _logic_info(self, layer) -> dict:
        from composeio.render.engine.headless import introspect_ports
        return introspect_ports(layer.to_spec(), layer.widget_id)

    # -- refresh / input seeding ([SPEC:CXC-FLAUTH]) -------------------------

    def refresh(self) -> None:
        """Selection or document changed — reseed the logic-card inputs
        (never clobbering a field the user is typing in)."""
        layer = self._logic_layer()
        if layer is None:
            for fi in self._inputs.values():
                fi.visible = False
            self._seeded_for = ("", "")
            self.request_redraw()
            return
        info = self._logic_info(layer)
        outs = [o["namespace"] for o in info["outputs"]]
        if self._sel_output not in outs:
            self._sel_output = outs[0] if outs else ""
        key = (layer.widget_id, self._sel_output)
        fresh = key != self._seeded_for
        self._seeded_for = key
        self._seed_inputs(layer, info, only_unfocused=not fresh)
        self.request_redraw()

    def _seed_inputs(self, layer, info: dict,
                     only_unfocused: bool = False) -> None:
        def seed(name: str, text: str) -> None:
            fi = self._inputs[name]
            if only_unfocused and fi.focused:
                return
            if fi.text != text:
                fi.set_text(text)
        sched = info["schedule"]
        iv = float(sched.get("interval_s", 60.0))
        seed("interval", f"{iv:g}")
        seed("triggers", ", ".join(sched.get("triggers") or []))
        expr = ""
        for o in info["outputs"]:
            if o["namespace"] == self._sel_output and o["expr"]:
                expr = o["expr"]
        seed("expr", expr)
        if not (only_unfocused and self._inputs["expr"].focused):
            self._set_expr_state(expr)
        from . import deploy as deploy_mod
        seed("runtime", deploy_mod.deploy_state(self.doc)["runtime_id"])
        if not only_unfocused:
            seed("add_out", "")

    def select_output(self, ns: str) -> None:
        self._sel_output = str(ns)
        self.refresh()

    # -- expr authoring ([CXC-FLAUTH-EXPR]) ----------------------------------

    def _set_expr_state(self, text: Any) -> None:
        """Live validation by the DSL parser ([SPEC:CXC-DSL]) — the same
        closed grammar dsl.try_parse enforces at every sanctioned site;
        green valid / red error as you type."""
        from composeio.render.engine import dsl
        t = str(text or "").strip()
        if not t:
            self.expr_state = (True, "empty — publishes the widget attr")
        else:
            try:
                dsl.parse(t)
                self.expr_state = (True, "valid")
            except dsl.DslParseError as e:
                self.expr_state = (False, str(e)[:96])

    def _on_expr_change(self, text: str) -> None:
        self._set_expr_state(text)
        self.request_redraw()

    def _commit_expr(self, text: str) -> None:
        if self._sel_output:
            self.set_output_expr(self._sel_output, text)

    def set_output_expr(self, ns: str, src: Any) -> bool:
        """Write one output's ``expr`` into the raw spec — ONLY when the
        sandboxed DSL parser accepts it (dsl.try_parse; a refused source
        is never written, [CXC-BIND-BOUNDARY]).  This is the sanctioned
        output-expr site of [CXC-DSL-SITES]; there is no other code
        path from this editor into the document."""
        layer = self._logic_layer()
        if layer is None or not ns:
            return False
        from composeio.render.engine import dsl
        src = str(src or "").strip()
        self._set_expr_state(src)
        if src and dsl.try_parse(
                src, f"{layer.widget_id}.outputs[{ns}]") is None:
            self.request_redraw()
            return False                    # refused, document untouched
        outputs = [dict(e) for e in (layer.properties.get("outputs") or [])
                   if isinstance(e, dict)]
        hit = False
        for e in outputs:
            if str(e.get("namespace", "")) == ns:
                hit = True
                if src:
                    e["expr"] = src
                    e.pop("attr", None)
                else:
                    e.pop("expr", None)
        if not hit:
            entry: dict = {"namespace": ns}
            if src:
                entry["expr"] = src
            outputs.append(entry)
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": layer.widget_id, "property": "outputs",
                  "value": outputs},
            source="flow_inspector")
        self._sel_output = ns
        return True

    def add_output(self, ns: str) -> None:
        """Inspector-side [CXC-FLAUTH-OUTWIRE]: declare + append an
        ``outputs:`` entry (the drag-to-hub gesture's keyboard twin)."""
        layer = self._logic_layer()
        ns = str(ns or "").strip()
        if layer is None or not ns or is_pattern(ns):
            return
        outputs = [dict(e) for e in (layer.properties.get("outputs") or [])
                   if isinstance(e, dict)]
        if any(str(e.get("namespace", "")) == ns for e in outputs):
            self.select_output(ns)
            return
        if self.doc.get_namespace(ns) is None:
            self.core.internal_bus.publish_event(
                "composer/config/namespace_changed", "do",
                data={"action": "created", "name": ns,
                      "state_engine": "json", "external": False},
                source="flow_inspector")
        outputs.append({"namespace": ns})
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": layer.widget_id, "property": "outputs",
                  "value": outputs},
            source="flow_inspector")
        self._sel_output = ns

    def _commit_add_output(self, text: str) -> None:
        self.add_output(text)
        self._inputs["add_out"].set_text("")

    # -- schedule picker ([CXC-FLAUTH-SCHEDULE]) -----------------------------

    def set_schedule(self, mode: str, interval_s: Any = None,
                     triggers: Any = None) -> None:
        """Write the widget's ``headless.schedule`` block ([CXC-HW-
        SCHEDULE]): the schedule travels IN the widget; the roster stays
        pure membership."""
        layer = self._logic_layer()
        if layer is None or mode not in ("continuous", "periodic",
                                         "bus_triggered"):
            return
        import copy as _copy
        headless = _copy.deepcopy(layer.properties.get("headless") or {})
        sched: dict = {"mode": mode}
        if mode == "periodic":
            if interval_s is None:
                try:
                    interval_s = float(self._inputs["interval"].text or 60)
                except ValueError:
                    interval_s = 60.0
            iv = float(interval_s)
            sched["interval_s"] = int(iv) if iv == int(iv) else iv
        elif mode == "bus_triggered":
            if triggers is None:
                triggers = [t.strip() for t in
                            self._inputs["triggers"].text.split(",")
                            if t.strip()]
            sched["trigger"] = [str(t) for t in triggers]
        headless["schedule"] = sched
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": layer.widget_id, "property": "headless",
                  "value": headless},
            source="flow_inspector")

    def _commit_interval(self, text: str) -> None:
        layer = self._logic_layer()
        if layer is not None and \
                self._logic_info(layer)["schedule"]["mode"] == "periodic":
            self.set_schedule("periodic")

    def _commit_triggers(self, text: str) -> None:
        layer = self._logic_layer()
        if layer is not None and self._logic_info(layer)["schedule"][
                "mode"] == "bus_triggered":
            self.set_schedule("bus_triggered")

    # -- deploy card ([CXC-FLAUTH-DEPLOY] + [CXC-FLAUTH-CONFIRM]) ------------

    def deploy_info(self) -> dict:
        from . import deploy as deploy_mod
        return deploy_mod.deploy_state(self.doc)

    def _write_deploy(self, state: dict) -> None:
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"property": "deploy",
                  "value": {"runtime_id": state["runtime_id"],
                            "widgets": list(state["widgets"])}},
            source="flow_inspector")

    def set_runtime_id(self, rid: str) -> None:
        st = self.deploy_info()
        rid = str(rid or "").strip()
        if rid:
            st["runtime_id"] = rid
            self._write_deploy(st)

    def _commit_runtime(self, text: str) -> None:
        self.set_runtime_id(text)

    def toggle_member(self, widget_id: str) -> None:
        st = self.deploy_info()
        if widget_id in st["widgets"]:
            st["widgets"].remove(widget_id)
        else:
            st["widgets"].append(widget_id)
        self._write_deploy(st)

    def push(self) -> None:
        """Push roster + widget configs — a bus command, so the deploy
        card and an Intelligence share the exact same path."""
        from . import deploy as deploy_mod
        st = self.deploy_info()
        members = st["widgets"] or [l.widget_id for l in
                                    deploy_mod.logic_layers(self.doc)]
        self.core.internal_bus.publish_event(
            "composer/deploy/push", "do",
            data={"runtime_id": st["runtime_id"], "widgets": members},
            source="flow_inspector")

    def _heartbeat_text(self, rid: str) -> Tuple[str, bool]:
        """The deploy card's runtime health line ([CXC-FLAUTH-CONFIRM]):
        from the _bus/headless/<rid>/health heartbeat via discovery."""
        disc = getattr(self.core, "discovery", None)
        hb = disc.headless_health(rid) if disc is not None else None
        if not hb:
            return ("no heartbeat — push, then start the runtime", False)
        age = float(hb.get("age_s", 0.0))
        n = hb.get("widgets", "?")
        if age <= 5.0:
            ms = int(age * 1000)
            return (f"running · {n} widgets · last beat {ms} ms ago", True)
        return (f"stale · last beat {int(age)}s ago · {n} widgets", False)

    def _inspect(self) -> None:
        """[SPEC:CXC-HMON] "Inspect ▸" — the Pass-3 reserved stub, now
        WIRED ([CXC-HMON-C9]): publishes a `composer/monitor/open` bus
        command scoped to this logic node; the UI host answers with the
        Headless Monitor pop-out (subprocess over MQTT, engine-dialog
        fallback otherwise — the Bus-Monitor convention).  A bus command,
        so an Intelligence opens the Monitor exactly like this button.
        Read-only: the design document is never touched."""
        self.inspect_taps += 1
        layer = self._logic_layer()
        if layer is not None:
            from . import deploy as deploy_mod
            rid = deploy_mod.deploy_state(self.doc)["runtime_id"]
            self.core.internal_bus.publish_event(
                "composer/monitor/open", "do",
                data={"widget_id": layer.widget_id, "runtime_id": rid},
                source="flow_inspector")
        self.request_redraw()

    # back-compat alias (the Pass-3 stub name)
    _inspect_stub = _inspect

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        pass

    def on_scroll(self, dx: float, dy: float) -> None:
        self.scroll = max(0.0, self.scroll - dy * 24)
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        for rect, fn in self._zones:
            if _contains(rect, x, y):
                fn()
                return

    # -- actions -------------------------------------------------------------

    def _edit_in_bindings(self, widget_id: str) -> None:
        ib = self.core.internal_bus
        ib.publish_state("composer/selection/widget_id", widget_id,
                         source="flow_inspector")
        ib.publish_state("composer/selection/type", "widget",
                         source="flow_inspector")
        ib.publish_state("composer/wc/active_tab", "bindings",
                         source="flow_inspector")

    def _unbind(self, widget_id: str, attr: str) -> None:
        prop = "namespace" if attr == "value" else attr
        value: Any = "" if prop == "namespace" else None
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": widget_id, "property": prop, "value": value},
            source="flow_inspector")
        self.core.internal_bus.publish_state("composer/flow/selected", {},
                                             source="flow_inspector")

    # -- drawing ----------------------------------------------------------------

    def _chip(self, canvas: Canvas, x: float, y: float, text: str,
              color, soft) -> float:
        w = len(text) * 6.3 + 16
        canvas.set_fill_color(*soft)
        canvas.fill_round_rect(x, y, w, 20, 6)
        canvas.set_stroke_color(*color, 0x66)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(x, y, w, 20, 6)
        canvas.set_fill_color(*color)
        canvas.set_font(MONO, 10, "regular")
        canvas.draw_text(text, x + w / 2, _baseline(canvas, y + 10), "center")
        return w

    def _button(self, canvas: Canvas, x: float, y: float, label: str,
                accent: bool, fn) -> float:
        w = len(label) * 6.6 + 18
        canvas.set_fill_color(*(chrome.ACCENT_SOFT if accent
                                else chrome.PANEL2))
        canvas.fill_round_rect(x, y, w, 22, 6)
        canvas.set_stroke_color(*(chrome.ACCENT if accent
                                  else chrome.BORDER_STRONG))
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(x, y, w, 22, 6)
        canvas.set_fill_color(*(chrome.ACCENT if accent else chrome.TEXT))
        canvas.set_font(FONT, 10.5, "medium")
        canvas.draw_text(label, x + w / 2, _baseline(canvas, y + 11),
                         "center")
        self._zones.append((Rect(x, y, w, 22), fn))
        return w

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        self._zones = []
        x = rect.x + 10
        w = rect.w - 20
        y = rect.y + 14 - self.scroll
        sel = self._selected()
        disc = getattr(self.core, "discovery", None)

        # [SPEC:CXC-FLAUTH] the logic-node authoring card
        if sel.get("kind") == "logic":
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 10.5, "medium")
            canvas.draw_text("LOGIC NODE", x, y, "left")
            y += 12
            layer = self._logic_layer()
            if layer is not None:
                self._draw_logic(canvas, x, y, w, layer, disc)
            else:
                for fi in self._inputs.values():
                    fi.visible = False
                canvas.set_fill_color(*chrome.DIM)
                canvas.set_font(FONT, 11, "regular")
                canvas.draw_text("Logic node no longer in the design.",
                                 x, y + 16, "left")
            canvas.restore()
            return
        for fi in self._inputs.values():
            fi.visible = False

        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 10.5, "medium")
        canvas.draw_text("CONNECTION", x, y, "left")
        y += 12

        if sel.get("kind") == "connection":
            y = self._draw_connection(canvas, x, y, w, sel, disc)
        elif sel.get("kind") == "namespace":
            y = self._draw_namespace(canvas, x, y, w,
                                     str(sel.get("namespace", "")), disc)
        else:
            canvas.set_fill_color(*chrome.DIM)
            canvas.set_font(FONT, 11, "regular")
            canvas.draw_text("Select a wire or node in the Flow graph,",
                             x, y + 16, "left")
            canvas.draw_text("or drag a namespace from the Bus·Live rail.",
                             x, y + 32, "left")
            y += 52

        # also on the bus, not yet wired
        if disc is not None:
            unwired = [e for e in disc.snapshot()
                       if e.live and not disc.is_wired(e.name)
                       and disc.kind_of(e.name) == "live-available"][:5]
            if unwired:
                y += 18
                canvas.set_fill_color(*chrome.FAINT)
                canvas.set_font(FONT, 10.5, "medium")
                canvas.draw_text("ALSO ON THE BUS — NOT YET WIRED", x, y,
                                 "left")
                y += 8
                for e in unwired:
                    canvas.set_fill_color(*chrome.PANEL2)
                    canvas.fill_round_rect(x, y, w, 24, 6)
                    canvas.set_fill_color(*LIVE)
                    canvas.set_font(MONO, 10, "regular")
                    canvas.draw_text(e.name[:30], x + 8,
                                     _baseline(canvas, y + 12), "left")
                    canvas.set_fill_color(*chrome.FAINT)
                    canvas.draw_text(str(e.engine)[:12], x + w - 8,
                                     _baseline(canvas, y + 12), "right")
                    y += 28
                canvas.set_fill_color(*chrome.FAINT)
                canvas.set_font(FONT, 9.5, "regular")
                canvas.draw_text("drag any from the Bus·Live rail to start",
                                 x, y + 10, "left")
                canvas.draw_text("listening", x, y + 22, "left")
        canvas.restore()

    def _draw_connection(self, canvas: Canvas, x: float, y: float,
                         w: float, sel: dict, disc: Any) -> float:
        ns = str(sel.get("namespace", ""))
        wid = str(sel.get("widget_id", ""))
        attr = str(sel.get("attr", ""))
        layer = self.doc.get_layer(wid)

        # card
        card_top = y + 4
        canvas.set_fill_color(*chrome.PANEL2)
        canvas.fill_round_rect(x, card_top, w, 96, 9)
        canvas.set_stroke_color(*chrome.BORDER)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(x, card_top, w, 96, 9)
        cx = x + 10
        cw = self._chip(canvas, cx, card_top + 10, ns[:26], LIVE, LIVE_SOFT)
        canvas.set_fill_color(*chrome.DIM)
        canvas.set_font(MONO, 11, "regular")
        canvas.draw_text("→", cx + cw + 6,
                         _baseline(canvas, card_top + 20), "left")
        self._chip(canvas, cx + cw + 20, card_top + 10,
                   f"{wid}.{attr}"[:24], chrome.ACCENT, chrome.ACCENT_SOFT)

        # listening-for line
        info = None
        if disc is not None:
            info = next((e for e in disc.snapshot() if e.name == ns), None)
        live = bool(info and info.live)
        canvas.set_fill_color(*(LIVE if live else chrome.FAINT),
                              0xFF)
        canvas.fill_circle(cx + 5, card_top + 48, 4)
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(FONT, 11, "medium")
        canvas.draw_text(f"Listening for: {ns}"[:44], cx + 16, card_top + 52,
                         "left")
        kind = disc.kind_of(ns) if disc is not None else ""
        eng = info.engine if info else "?"
        if live and info:
            age = max(0, int(time.time() - info.first_seen))
            desc = f"{eng} · live · first seen {age}s ago"
            if info.source:
                desc += f" · from {info.source}"
        elif kind == "expected":
            desc = f"{eng} · declared-expected — will light up on first " \
                   f"publish"
        else:
            desc = f"{eng} · {kind or 'silent'}"
        canvas.set_fill_color(*chrome.DIM)
        canvas.set_font(FONT, 9.5, "regular")
        canvas.draw_text(desc[:52], cx + 16, card_top + 68, "left")
        outside = kind in ("subscribed-external", "expected",
                           "live-available")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.draw_text(
            ("outside signal — nothing in this design publishes it"
             if outside else "this design's own namespace")[:52],
            cx + 16, card_top + 82, "left")
        y = card_top + 106

        # source field
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 9.5, "medium")
        canvas.draw_text("SOURCE", x, y + 8, "left")
        canvas.set_fill_color(*chrome.PANEL2)
        canvas.fill_round_rect(x, y + 14, w, 24, 6)
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(MONO, 10.5, "regular")
        canvas.draw_text(ns[:34], x + 8, _baseline(canvas, y + 26), "left")
        y += 46

        # binding detail: when / invert / map / default from the RAW spec
        raw = layer.properties.get(attr) if layer is not None else None
        if attr == "value" and layer is not None and layer.namespace == ns:
            raw = None
        rows: List[Tuple[str, str]] = []
        if isinstance(raw, dict):
            if raw.get("when") is not None:
                rows.append(("when", str(raw["when"])))
            if raw.get("invert"):
                rows.append(("invert", "true"))
            if raw.get("default") is not None:
                rows.append(("default", str(raw["default"])))
            m = raw.get("map")
            if isinstance(m, dict):
                for k, v in list(m.items())[:6]:
                    rows.append((f"map {k}", f"→  {v}"))
        elif isinstance(raw, str):
            rows.append(("mode", "direct (truthy)"))
        elif attr.startswith("»"):
            rows.append(("behavior", attr.lstrip("» ")))
        if rows:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 9.5, "medium")
            canvas.draw_text("WHEN → VALUE", x, y + 6, "left")
            y += 12
            canvas.set_font(MONO, 10.5, "regular")
            for k, v in rows:
                canvas.set_fill_color(*chrome.DIM)
                canvas.draw_text(k[:16], x + 4, y + 12, "left")
                canvas.set_fill_color(*chrome.TEXT)
                canvas.draw_text(str(v)[:26], x + 110, y + 12, "left")
                y += 17
            y += 6

        # actions
        bx = x
        bx += self._button(canvas, bx, y + 4, "Edit in Bindings", True,
                           lambda w_id=wid: self._edit_in_bindings(w_id)) + 8
        if not attr.startswith("»"):
            self._button(canvas, bx, y + 4, "Unbind", False,
                         lambda w_id=wid, a=attr: self._unbind(w_id, a))
        y += 34

        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 9.5, "regular")
        canvas.draw_text("The widget doesn't care what emits this namespace",
                         x, y + 10, "left")
        canvas.draw_text("— an ESP32, a sim, another widget. The bus", x,
                         y + 22, "left")
        canvas.draw_text("decouples the rest.", x, y + 34, "left")
        return y + 44

    def _draw_namespace(self, canvas: Canvas, x: float, y: float, w: float,
                        ns: str, disc: Any) -> float:
        info = None
        if disc is not None:
            info = next((e for e in disc.snapshot() if e.name == ns), None)
        kind = disc.kind_of(ns) if disc is not None else ""
        live = bool(info and info.live)
        canvas.set_fill_color(*chrome.PANEL2)
        canvas.fill_round_rect(x, y + 4, w, 78, 9)
        canvas.set_fill_color(*(LIVE if live else chrome.FAINT))
        canvas.fill_circle(x + 14, y + 22, 4)
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(MONO, 11.5, "medium")
        canvas.draw_text(ns[:30], x + 26, y + 26, "left")
        canvas.set_fill_color(*chrome.DIM)
        canvas.set_font(MONO, 10, "regular")
        eng = info.engine if info else "?"
        val = "—" if not info or info.value is None else str(info.value)
        canvas.draw_text(f"{eng} · now {val}"[:38], x + 26, y + 44, "left")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 9.5, "regular")
        state = {"declared-internal": "declared in this design",
                 "subscribed-external": "outside signal — listening",
                 "expected": "declared-expected — awaiting first publish",
                 "live-available": "live on the bus — not yet wired",
                 }.get(kind, kind)
        canvas.draw_text(state[:50], x + 26, y + 60, "left")
        y += 92
        # subscribers in this design
        subs = [r for r in self.doc.bindings()
                if r.get("namespace") == ns
                or (is_pattern(str(r.get("namespace", "")))
                    and pattern_matches(str(r["namespace"]), ns))]
        if subs:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 9.5, "medium")
            canvas.draw_text("WIRED TO", x, y + 6, "left")
            y += 12
            canvas.set_font(MONO, 10, "regular")
            for r in subs[:8]:
                label = f"{r['widget']}.{r.get('attr', r['kind'])}"
                canvas.set_fill_color(*chrome.TEXT)
                canvas.draw_text(label[:34], x + 4, y + 12, "left")
                y += 16
        return y + 8

    # -- the logic-node card ([SPEC:CXC-FLAUTH]) -----------------------------

    def _section(self, canvas: Canvas, x: float, y: float,
                 label: str) -> float:
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 9.5, "medium")
        canvas.draw_text(label, x, y + 8, "left")
        return y + 14

    def _place_input(self, canvas: Canvas, name: str, x: float, y: float,
                     w: float, h: float = 40.0) -> float:
        fi = self._inputs[name]
        fi.visible = True
        fi.arranged_rect = Rect(x, y, w, h)
        fi.draw(canvas, fi.arranged_rect)
        return y + h + 4

    def _draw_logic(self, canvas: Canvas, x: float, y: float, w: float,
                    layer: Any, disc: Any) -> float:
        from . import deploy as deploy_mod
        wid = layer.widget_id
        info = self._logic_info(layer)
        sched = info["schedule"]
        shown: set = set()

        def place(name: str, yy: float) -> float:
            shown.add(name)
            return self._place_input(canvas, name, x, yy, w)

        # header: fx badge + id + "headless · no render" + Inspect stub
        canvas.set_fill_color(*LOGIC_SOFT)
        canvas.fill_round_rect(x, y + 2, 22, 22, 6)
        canvas.set_stroke_color(*LOGIC, 0x66)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(x, y + 2, 22, 22, 6)
        canvas.set_fill_color(*LOGIC)
        canvas.set_font(MONO, 10.5, "medium")
        canvas.draw_text("fx", x + 11, _baseline(canvas, y + 13), "center")
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(MONO, 13, "medium")
        canvas.draw_text(wid[:20], x + 30, y + 12, "left")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 9, "regular")
        canvas.draw_text("headless widget · no render", x + 30, y + 25,
                         "left")
        # "Inspect ▸" — opens the Headless Monitor ([SPEC:CXC-HMON])
        ilabel = "Inspect >"
        ibw = len(ilabel) * 6.2 + 14
        iz = Rect(x + w - ibw, y + 2, ibw, 22)
        canvas.set_fill_color(*chrome.PANEL2)
        canvas.fill_round_rect(iz.x, iz.y, iz.w, iz.h, 6)
        canvas.set_stroke_color(*chrome.BORDER)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(iz.x, iz.y, iz.w, iz.h, 6)
        canvas.set_fill_color(*chrome.DIM)
        canvas.set_font(MONO, 9.5, "regular")
        canvas.draw_text(ilabel, iz.x + iz.w / 2,
                         _baseline(canvas, iz.y + 11), "center")
        self._zones.append((iz, self._inspect))
        y += 34

        # -- SCHEDULE ([CXC-FLAUTH-SCHEDULE]) --------------------------------
        y = self._section(canvas, x, y, "SCHEDULE")
        cx = x
        canvas.set_font(MONO, 10, "regular")
        for mode, label in (("continuous", "continuous"),
                            ("periodic", "periodic"),
                            ("bus_triggered", "bus-trig")):
            cw = len(label) * 6.3 + 18
            on = sched["mode"] == mode
            canvas.set_fill_color(*(chrome.ACCENT_SOFT if on
                                    else chrome.PANEL2))
            canvas.fill_round_rect(cx, y, cw, 22, 6)
            canvas.set_stroke_color(*(chrome.ACCENT if on else chrome.BORDER))
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(cx, y, cw, 22, 6)
            canvas.set_fill_color(*(chrome.ACCENT if on else chrome.DIM))
            canvas.draw_text(label, cx + cw / 2, _baseline(canvas, y + 11),
                             "center")
            self._zones.append((Rect(cx, y, cw, 22),
                                lambda m=mode: self.set_schedule(m)))
            cx += cw + 6
        y += 27
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 9.5, "regular")
        canvas.draw_text(schedule_summary(sched)[:52], x, y + 8, "left")
        y += 16
        if sched["mode"] == "periodic":
            y = place("interval", y)
        elif sched["mode"] == "bus_triggered":
            y = place("triggers", y)

        # -- PORTS, live ([CXC-FLAUTH-CONFIRM] C9) ---------------------------
        y = self._section(canvas, x, y + 2, "PORTS · LIVE")
        half = (w - 8) / 2
        canvas.set_font(MONO, 9.5, "regular")

        def port_chip(px: float, yy: float, ns: str, out: bool) -> None:
            is_live, val = (False, None)
            if disc is not None:
                for e in disc.snapshot():
                    if e.name == ns:
                        is_live, val = e.live, e.value
                        break
            canvas.set_fill_color(*chrome.PANEL2)
            canvas.fill_round_rect(px, yy, half, 22, 6)
            canvas.set_fill_color(*(chrome.ACCENT if out else LIVE)
                                  if is_live else chrome.FAINT)
            canvas.fill_circle(px + 9, yy + 11, 3)
            canvas.set_fill_color(*chrome.TEXT if is_live else chrome.DIM)
            canvas.draw_text(ns.split("/")[-1][:12], px + 17,
                             _baseline(canvas, yy + 11), "left")
            if is_live and val is not None:
                canvas.set_fill_color(*LIVE)
                canvas.draw_text(str(val)[:7], px + half - 6,
                                 _baseline(canvas, yy + 11), "right")

        in_ns = [i["namespace"] for i in info["inputs"]]
        out_ns = [o["namespace"] for o in info["outputs"]]
        rows = max(len(in_ns), len(out_ns), 1)
        for i in range(rows):
            yy = y + i * 26
            if i < len(in_ns):
                port_chip(x, yy, in_ns[i], out=False)
            if i < len(out_ns):
                port_chip(x + half + 8, yy, out_ns[i], out=True)
                self._zones.append((Rect(x + half + 8, yy, half, 22),
                                    lambda n=out_ns[i]:
                                    self.select_output(n)))
        if not in_ns and not out_ns:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 9.5, "regular")
            canvas.draw_text("no ports yet — drag hubs on, or add an "
                             "output below", x, y + 14, "left")
        y += rows * 26 + 4

        # -- OUTPUT expr editor ([CXC-FLAUTH-EXPR]) --------------------------
        title = f"OUTPUT · {self._sel_output} = expr" if self._sel_output \
            else "OUTPUT — none yet"
        y = self._section(canvas, x, y, title[:56])
        if self._sel_output:
            y = place("expr", y)
            ok, msg = self.expr_state
            canvas.set_font(MONO, 9.5, "regular")
            if ok:
                canvas.set_fill_color(*LIVE)
                canvas.draw_text("v " + (msg or "valid"), x + 2, y + 8,
                                 "left")
            else:
                canvas.set_fill_color(*chrome.CRIT)
                canvas.draw_text(("x " + msg)[:60], x + 2, y + 8, "left")
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 8.5, "regular")
            canvas.draw_text("DSL · sandboxed · Enter commits · Source tab "
                             "is the raw escape hatch", x + 2, y + 20,
                             "left")
            y += 28
        y = place("add_out", y)

        # -- DEPLOY card ([CXC-FLAUTH-DEPLOY] + heartbeat C10) ---------------
        y = self._section(canvas, x, y + 2, "DEPLOY")
        st = deploy_mod.deploy_state(self.doc)
        rid = st["runtime_id"]
        y = place("runtime", y)
        hb_text, hb_ok = self._heartbeat_text(rid)
        canvas.set_fill_color(*(LIVE if hb_ok else chrome.FAINT))
        canvas.set_font(MONO, 9.5, "regular")
        canvas.draw_text(hb_text[:58], x + 2, y + 8, "left")
        y += 16
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(MONO, 8.5, "regular")
        canvas.draw_text(f"bus://assets/headless/{rid}.roster.yaml"[:60],
                         x + 2, y + 8, "left")
        y += 18
        # roster membership chips (every logic node in the design)
        cx = x
        canvas.set_font(MONO, 9.5, "regular")
        members = st["widgets"]
        for l in deploy_mod.logic_layers(self.doc):
            name = l.widget_id
            cw = len(name[:14]) * 6.0 + 16
            if cx + cw > x + w:
                cx = x
                y += 26
            on = name in members or (not members)
            here = name == wid
            color = LOGIC if here else (chrome.ACCENT if on else chrome.DIM)
            canvas.set_fill_color(*(LOGIC_SOFT if here else
                                    (chrome.ACCENT_SOFT if on
                                     else chrome.PANEL2)))
            canvas.fill_round_rect(cx, y, cw, 22, 6)
            canvas.set_stroke_color(*color, 0x88)
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(cx, y, cw, 22, 6)
            canvas.set_fill_color(*color)
            canvas.draw_text(name[:14], cx + cw / 2,
                             _baseline(canvas, y + 11), "center")
            self._zones.append((Rect(cx, y, cw, 22),
                                lambda n=name: self.toggle_member(n)))
            cx += cw + 6
        y += 28
        # Push
        pz = Rect(x, y, w, 26)
        canvas.set_fill_color(*chrome.ACCENT_SOFT)
        canvas.fill_round_rect(pz.x, pz.y, pz.w, pz.h, 7)
        canvas.set_stroke_color(*chrome.ACCENT)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(pz.x, pz.y, pz.w, pz.h, 7)
        canvas.set_fill_color(*chrome.ACCENT)
        canvas.set_font(MONO, 10.5, "medium")
        canvas.draw_text("Push roster -> bus storage", pz.x + pz.w / 2,
                         _baseline(canvas, pz.y + 13), "center")
        self._zones.append((pz, self.push))
        y += 32
        status = self.core.internal_bus.get_state("composer/deploy/status")
        if isinstance(status, dict) and status:
            ok = bool(status.get("ok"))
            canvas.set_fill_color(*(LIVE if ok else chrome.CRIT))
            canvas.set_font(MONO, 9, "regular")
            line = f"pushed {status.get('roster', '')}" if ok \
                else f"push failed: {status.get('error', '?')}"
            canvas.draw_text(line[:60], x + 2, y + 6, "left")
            y += 14

        # hide any input this pass didn't place
        for name, fi in self._inputs.items():
            if name not in shown:
                fi.visible = False
        return y + 8
