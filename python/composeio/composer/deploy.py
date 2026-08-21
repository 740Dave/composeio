"""Composer headless deploy — roster assembly & push ([SPEC:CXC-FLAUTH]
[CXC-FLAUTH-DEPLOY]).

Turns each logic node of the composite document into the STANDALONE
headless widget config a HeadlessRuntime loads ([SPEC:CXC-HEADLESS]
[CXC-HW-DEPLOY]), assembles the pure-membership roster, and pushes both
through the Pass-1 loader contract (LocalFileLoader / BusStorageLoader —
no new deploy plumbing):

    bus://assets/headless/<runtime_id>.roster.yaml
    bus://assets/headless/widgets/<widget_id>.compose.yaml

Shape mapping (composite layer -> standalone config): the layer's
widget-level ``headless:`` block (the schedule travels IN the widget)
hoists to the document level the runtime parses; design-canvas geometry
(x/y/w/h) is stripped — a logic node is geometry-free; document-level
``variables:`` ride along so ``$name`` exprs keep resolving; each output
namespace is declared with the design's state engine (json fallback) so
downstream subscribers get a legible namespace.
"""

from __future__ import annotations

import copy
from typing import Any, List, Optional

from ..render.engine.headless import roster_dict  # noqa: F401  (re-export)
from .document import CompositeDocument, LayerDef

# design-canvas placement — meaningless on a headless runtime
_GEOMETRY_KEYS = ("x", "y", "w", "h", "width", "height")


def default_runtime_id(doc: CompositeDocument) -> str:
    return f"{doc.slug}-headless"


def deploy_state(doc: CompositeDocument) -> dict:
    """The document's deploy assignment ({runtime_id, widgets}) — design
    metadata carried in the top-level ``deploy:`` extras key (raw-spec
    passthrough; absent until first assignment)."""
    d = doc.extras.get("deploy")
    d = dict(d) if isinstance(d, dict) else {}
    d.setdefault("runtime_id", default_runtime_id(doc))
    wids = d.get("widgets")
    d["widgets"] = [str(w) for w in wids] if isinstance(wids, list) else []
    return d


def logic_layers(doc: CompositeDocument) -> List[LayerDef]:
    """Every layer that is a logic node (widget-level ``headless:``)."""
    return [l for l in doc.layers
            if isinstance(l.properties.get("headless"), dict)]


def widget_config(doc: CompositeDocument, layer: LayerDef) -> dict:
    """The standalone headless .compose.yaml for one logic node — the
    exact shape LocalFileLoader/BusStorageLoader.load_widget returns and
    HeadlessRuntime.add_widget parses."""
    spec = copy.deepcopy(layer.to_spec())
    headless = spec.pop("headless", None)
    for k in _GEOMETRY_KEYS:
        spec.pop(k, None)

    cfg: dict[str, Any] = {"app": {"id": f"headless-{layer.widget_id}"}}
    if isinstance(headless, dict):
        cfg["headless"] = headless          # [CXC-HW-SCHEDULE] doc level
    variables = doc.extras.get("variables")
    if isinstance(variables, dict) and variables:
        cfg["variables"] = copy.deepcopy(variables)

    ns_decls = []
    for entry in spec.get("outputs") or []:
        if not isinstance(entry, dict) or not entry.get("namespace"):
            continue
        name = str(entry["namespace"])
        decl = doc.get_namespace(name)
        ns_decls.append({
            "name": name,
            "state_engine": decl.state_engine if decl is not None else "json",
        })
    if ns_decls:
        cfg["namespaces"] = ns_decls
    cfg["widgets"] = [spec]
    return cfg


def push(doc: CompositeDocument, loader: Any,
         runtime_id: Optional[str] = None,
         widget_ids: Optional[List[str]] = None) -> dict:
    """[CXC-FLAUTH-DEPLOY] Push: write the roster + each member's widget
    config through *loader* (save_roster/save_widget — the Pass-1
    contract's write side).  Returns a summary the deploy card and the
    ``composer/deploy/pushed`` event report."""
    state = deploy_state(doc)
    rid = str(runtime_id or state["runtime_id"])
    members = [str(w) for w in (widget_ids
                                if widget_ids is not None
                                else state["widgets"])]
    if not members:
        members = [l.widget_id for l in logic_layers(doc)]
    written = []
    for wid in members:
        layer = doc.get_layer(wid)
        if layer is None or not isinstance(layer.properties.get("headless"),
                                           dict):
            raise ValueError(f"'{wid}' is not a headless logic node")
        written.append(loader.save_widget(wid, widget_config(doc, layer)))
    roster_uri = loader.save_roster(rid, members)
    return {"runtime_id": rid, "widgets": members,
            "roster": roster_uri, "written": written}
