"""CompositeDocument — the in-memory model of the widget being built.

The M4 Composer edits a **pixel-stack composite** (the model from the
Workshop pre-vis): a fixed-size pixel canvas holding z-ordered layers,
each layer a real ComposeUX widget with absolute px geometry.  Z-order
is list order: index 0 = back (drawn first), last = front.

The document is the source of truth and is mutated ONLY by the
ComposerCore dispatcher in response to bus commands ([DP-CXC-1]) — no UI
module writes it directly.

Serialization targets the render engine's **nested .compose.yaml
dialect** directly, so the exported artifact runs unmodified via
``python -m composeio.render.run_compose`` — the dogfooding gate of
[CXC-RM-M4]: Composer previews exactly what the runtime renders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

# Design-canvas chrome: the runtime's nested-dialect root flex pads 12px,
# so a standalone design surface is composite + 2*12.
_ROOT_PAD = 12


def _num(v: float) -> Any:
    """Emit integral floats as ints so YAML round-trips byte-identically."""
    f = float(v)
    return int(f) if f == int(f) else f


@dataclass
class NamespaceDef:
    name: str
    label: str = ""
    state_engine: Any = "json"
    # [CXC-FL-DISCOVERY] binding-model addition: True marks an OUTSIDE
    # signal this design listens for (subscribed-external) — nothing in
    # the design publishes it; it may not even be live yet
    # (declare-expected).  False = declared-internal (the design's own).
    # The name may be a trailing-glob PATTERN ([CXC-FL-PATTERN]): the
    # design then subscribes the whole family.
    external: bool = False

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"name": self.name, "state_engine": self.state_engine}
        if self.label:
            d["label"] = self.label
        if self.external:
            d["external"] = True     # additive; omitted when False so
        return d                     # pre-FLOW documents round-trip unchanged


@dataclass
class LayerDef:
    """One z-layer of the composite: a widget + absolute px geometry."""
    widget_id: str
    widget_type: str                       # engine type key (image, callout, …)
    x: float = 0.0
    y: float = 0.0
    w: Optional[float] = None              # None -> widget's natural size
    h: Optional[float] = None
    visible: bool = True
    namespace: str = ""                    # subscribe binding
    properties: dict[str, Any] = field(default_factory=dict)

    def to_spec(self) -> dict:
        """The child dict for the pixel_stack in nested .compose.yaml.

        [CXC-BE-RAWSPEC]: ``properties`` holds RAW spec values — binding
        objects, bare-namespace strings, ``behaviors:`` lists — and they
        pass through untouched.  When ``visible`` carries a binding form
        (kept raw in properties), the binding is the runtime authority and
        the design-time bool is NOT emitted over it.
        """
        spec: dict[str, Any] = {"type": self.widget_type, "id": self.widget_id}
        spec.update(self.properties)
        spec["x"] = _num(self.x)
        spec["y"] = _num(self.y)
        if self.w is not None:
            spec["w"] = _num(self.w)
        if self.h is not None:
            spec["h"] = _num(self.h)
        if self.namespace:
            spec["namespace"] = self.namespace
        if not self.visible and "visible" not in self.properties:
            spec["visible"] = False
        return spec

    @classmethod
    def from_spec(cls, spec: dict) -> "LayerDef":
        # ``visible`` is only "known" (design-time bool) when it IS a bool;
        # a bare-namespace string or binding object stays RAW in properties
        # so it round-trips losslessly ([CXC-BE-RAWSPEC]).
        known = {"type", "id", "x", "y", "w", "h", "width", "height",
                 "namespace"}
        vis = spec.get("visible", True)
        if isinstance(vis, bool):
            known = known | {"visible"}
        props = {k: v for k, v in spec.items() if k not in known}
        return cls(
            widget_id=str(spec.get("id", "layer")),
            widget_type=str(spec.get("type", "rect")),
            x=float(spec.get("x", 0) or 0),
            y=float(spec.get("y", 0) or 0),
            w=(float(spec["w"]) if "w" in spec else
               float(spec["width"]) if "width" in spec else None),
            h=(float(spec["h"]) if "h" in spec else
               float(spec["height"]) if "height" in spec else None),
            visible=bool(vis) if isinstance(vis, bool) else True,
            namespace=str(spec.get("namespace", "") or ""),
            properties=props)


class CompositeDocument:
    """The composite being built: canvas + namespaces + z-ordered layers."""

    def __init__(self, name: str = "Untitled Widget",
                 width: int = 800, height: int = 600) -> None:
        self.name = name
        self.width = int(width)
        self.height = int(height)
        self.purpose = ""
        self.namespaces: list[NamespaceDef] = []
        self.layers: list[LayerDef] = []
        # RAW top-level passthrough ([CXC-BE-RAWSPEC]): document-level
        # `variables:`, `components:`, and any future top-level keys the
        # composite model doesn't own round-trip untouched.
        self.extras: dict[str, Any] = {}
        self._counters: dict[str, int] = {}

    # -- ids ---------------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0)
        while True:
            n += 1
            wid = f"{prefix}_{n}"
            if self.get_layer(wid) is None:
                break
        self._counters[prefix] = n
        return wid

    # -- lookup ---------------------------------------------------------------

    def get_layer(self, widget_id: str) -> Optional[LayerDef]:
        for l in self.layers:
            if l.widget_id == widget_id:
                return l
        return None

    def layer_index(self, widget_id: str) -> int:
        for i, l in enumerate(self.layers):
            if l.widget_id == widget_id:
                return i
        return -1

    def get_namespace(self, name: str) -> Optional[NamespaceDef]:
        for ns in self.namespaces:
            if ns.name == name:
                return ns
        return None

    # -- mutation (ComposerCore dispatcher only) ----------------------------

    def add_layer(self, layer: LayerDef, index: Optional[int] = None) -> None:
        if index is None or not (0 <= index <= len(self.layers)):
            self.layers.append(layer)
        else:
            self.layers.insert(index, layer)

    def remove_layer(self, widget_id: str) -> Optional[LayerDef]:
        i = self.layer_index(widget_id)
        return self.layers.pop(i) if i >= 0 else None

    def reorder_layer(self, widget_id: str, new_index: int) -> bool:
        i = self.layer_index(widget_id)
        if i < 0:
            return False
        new_index = max(0, min(int(new_index), len(self.layers) - 1))
        self.layers.insert(new_index, self.layers.pop(i))
        return True

    def add_namespace(self, ns: NamespaceDef) -> None:
        existing = self.get_namespace(ns.name)
        if existing is not None:
            self.namespaces[self.namespaces.index(existing)] = ns
        else:
            self.namespaces.append(ns)

    def remove_namespace(self, name: str) -> Optional[NamespaceDef]:
        ns = self.get_namespace(name)
        if ns is not None:
            self.namespaces.remove(ns)
        return ns

    # -- derived: bindings summary (Overview tab) --------------------------

    def bindings(self) -> list[dict]:
        """Derived binding rows: widget<->namespace relations in the design,
        including the [SPEC:CXC-BIND] attribute-binding forms (bare-string
        namespace refs and {namespace: ...} objects), behaviors, and the
        [SPEC:CXC-FLAUTH] headless forms — declared ``outputs:`` publishes,
        ``ns('...')`` expression reads, and bus-trigger wake sources."""
        from composeio.render.engine.bindings import (BINDABLE_ATTRS,
                                                      NAMESPACE_STRING_ATTRS)
        from composeio.render.engine.headless import (expr_namespaces,
                                                      parse_schedule)
        rows: list[dict] = []
        for l in self.layers:
            pub = l.properties.get("publish_namespace")
            if pub:
                rows.append({"kind": "publish", "widget": l.widget_id,
                             "namespace": str(pub)})
            if l.namespace:
                rows.append({"kind": "subscribe", "widget": l.widget_id,
                             "namespace": l.namespace})
            for attr, v in l.properties.items():
                if attr not in BINDABLE_ATTRS:
                    continue
                if isinstance(v, dict) and v.get("namespace"):
                    rows.append({"kind": "attr", "attr": attr,
                                 "widget": l.widget_id,
                                 "namespace": str(v["namespace"])})
                elif isinstance(v, dict):
                    # [CXC-DSL-SITES] binding-value expr: every ns() read
                    # is a live wire
                    for ns in expr_namespaces(v.get("expr",
                                                    v.get("expression"))):
                        rows.append({"kind": "expr", "attr": attr,
                                     "widget": l.widget_id, "namespace": ns})
                elif isinstance(v, str) and v \
                        and attr in NAMESPACE_STRING_ATTRS:
                    rows.append({"kind": "attr", "attr": attr,
                                 "widget": l.widget_id, "namespace": v})
            # [CXC-FLAUTH-OUTWIRE] declared outputs are first-class
            # publish wires; their expr reads are input wires.
            for entry in l.properties.get("outputs", []) or []:
                if not isinstance(entry, dict) or not entry.get("namespace"):
                    continue
                expr_src = entry.get("expr", entry.get("expression"))
                rows.append({"kind": "publish", "widget": l.widget_id,
                             "namespace": str(entry["namespace"]),
                             "attr": str(entry.get("attr", "value")),
                             "expr": expr_src if isinstance(expr_src, str)
                             else None})
                for ns in expr_namespaces(expr_src):
                    rows.append({"kind": "expr", "attr": "value",
                                 "widget": l.widget_id, "namespace": ns})
            for entry in l.properties.get("behaviors", []) or []:
                if not isinstance(entry, dict):
                    continue
                when = entry.get("when")
                do = entry.get("do", {}) if isinstance(entry.get("do"),
                                                       dict) else {}
                ns = when.get("namespace") if isinstance(when, dict) else None
                rows.append({"kind": "behavior", "widget": l.widget_id,
                             "namespace": str(ns or "always"),
                             "behavior": str(do.get("behavior", "?")),
                             "target": str(do.get("target", ""))})
            # [CXC-FLAUTH-SCHEDULE] bus-trigger wake sources are inputs
            if isinstance(l.properties.get("headless"), dict):
                sched = parse_schedule(l.properties.get("headless"),
                                       l.widget_id)
                for trig in sched.triggers:
                    rows.append({"kind": "trigger", "widget": l.widget_id,
                                 "namespace": str(trig)})
        return rows

    # -- serialization ------------------------------------------------------

    @property
    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return s or "composite"

    def to_dict(self) -> dict:
        """Nested-dialect .compose.yaml structure (the runnable artifact)."""
        out: dict[str, Any] = {
            "app": {"id": self.slug, "transports": []},
            "design": {
                "title": self.name,
                "target": "desktop",
                "width": self.width + 2 * _ROOT_PAD,
                "height": self.height + 2 * _ROOT_PAD,
                "theme": "dark",
            },
        }
        if self.purpose:
            out["design"]["purpose"] = self.purpose
        if self.namespaces:
            out["namespaces"] = [ns.to_dict() for ns in self.namespaces]
        out.update(self.extras)          # variables/components passthrough
        out["widgets"] = [{
            "type": "pixel_stack",
            "id": "composite",
            "width": self.width,
            "height": self.height,
            "background": "#0B0D12",
            "border": "#3A3A50",
            "corner_radius": 4,
            "clip": True,
            "children": [l.to_spec() for l in self.layers],
        }]
        return out

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False,
                              default_flow_style=None, allow_unicode=True)

    @classmethod
    def from_dict(cls, config: dict) -> "CompositeDocument":
        design = config.get("design", {}) or {}
        doc = cls(name=str(design.get("title", "Untitled Widget")))
        doc.purpose = str(design.get("purpose", "") or "")
        import copy as _copy
        doc.extras = {k: _copy.deepcopy(v) for k, v in config.items()
                      if k not in ("app", "design", "namespaces", "widgets")}
        for ns in config.get("namespaces", []) or []:
            if isinstance(ns, dict) and ns.get("name"):
                doc.add_namespace(NamespaceDef(
                    name=str(ns["name"]), label=str(ns.get("label", "") or ""),
                    state_engine=ns.get("state_engine", "json"),
                    external=bool(ns.get("external", False))))
        stack = None
        for w in config.get("widgets", []) or []:
            if str(w.get("type", "")).lower() in ("pixel_stack", "pixelstack"):
                stack = w
                break
        if stack is not None:
            doc.width = int(stack.get("width", 800))
            doc.height = int(stack.get("height", 600))
            for ch in stack.get("children", []) or []:
                doc.add_layer(LayerDef.from_spec(ch))
        else:
            doc.width = int(design.get("width", 824)) - 2 * _ROOT_PAD
            doc.height = int(design.get("height", 624)) - 2 * _ROOT_PAD
        return doc

    @classmethod
    def from_yaml(cls, text: str) -> "CompositeDocument":
        cfg = yaml.safe_load(text)
        if not isinstance(cfg, dict):
            raise ValueError("not a .compose.yaml mapping")
        return cls.from_dict(cfg)

    # -- Intelligence snapshot shape (spec 06 compatible) ---------------------

    def snapshot(self) -> dict:
        """DesignDocument-shaped snapshot: the composite reports as one
        module ('composite') so existing Intelligence clients traverse it
        the same way they traverse the CTk Composer's design."""
        return {
            "compose_version": "0.2",
            "design": {"name": self.name, "target": "desktop",
                       "width": self.width, "height": self.height,
                       "purpose": self.purpose},
            "namespaces": [ns.to_dict() for ns in self.namespaces],
            "modules": [{
                "id": "composite",
                "role": "pixel-stack",
                "layout": {"grid_row": 0, "grid_column": 0},
                "widgets": [{
                    "id": l.widget_id,
                    "type": l.widget_type,
                    "namespace": l.namespace,
                    "properties": dict(l.properties),
                    "visible": l.visible,
                    "z_index": i,
                    "layout": {"x": l.x, "y": l.y, "w": l.w, "h": l.h},
                } for i, l in enumerate(self.layers)],
            }],
            "bindings": self.bindings(),
        }
