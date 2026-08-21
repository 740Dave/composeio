"""ComposerCore — the headless, bus-native heart of the M4 Composer.

Ports the CTk Composer's non-visual module architecture
(DesignCommandDispatcher + IntelligenceCoordinator + FileOps, see
ux_composer/src/composer/) onto the render-engine Composer, with the
CompositeDocument as the design model.  NO UI here: the core runs
identically under the windowed Composer, in tests, and headless.

Protocol compatibility is the point ([CXC-CREDO]): the core speaks the
SAME namespaces the CTk Composer and the intelligence_mcp bridge already
speak — ``composer/command/execute``, ``composer/canvas/*``,
``composer/config/*``, ``composer/intelligence/*`` — so an Intelligence
drives this Composer with zero MCP-server changes.

Composite-mode vocabulary mapping (documented divergence):
    - the composite is the single implicit module ``composite``
    - widget_added/moved:  x/column and y/row are PIXELS on the canvas
    - widget_resized:      w/columnspan and h/rowspan are PIXELS
    - set_property visible=bool     -> layer visibility (Layers tab eye)
    - set_property z_index=int      -> layer reorder (Layers tab drag)
    - design-level properties: name, width, height, purpose
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Optional

import yaml

from composeio.app import ComposeApp

from .catalog import get as catalog_get
from .document import CompositeDocument, LayerDef, NamespaceDef

logger = logging.getLogger("composeio.composer.core")

# Namespaces whose "executed"/"replayed" events mean "the document changed —
# rebuild your view".  Identical to the CTk dispatcher's replay set (minus
# module/binding namespaces the composite model doesn't use yet).
CANVAS_NAMESPACES = [
    "composer/canvas/widget_added",
    "composer/canvas/widget_removed",
    "composer/canvas/widget_moved",
    "composer/canvas/widget_resized",
    "composer/config/widget_changed",
    "composer/config/namespace_changed",
]

INTEL_IN = [
    "composer/intelligence/joined",
    "composer/intelligence/left",
    "composer/intelligence/heartbeat",
    "composer/intelligence/intent",
    "composer/intelligence/suggestion",
    "composer/intelligence/cancel",
    "composer/intelligence/request_state",
    "composer/intelligence/request_layout",
    "composer/intelligence/validate",
]

INTEL_OUT = [
    "composer/intelligence/state_snapshot",
    "composer/intelligence/layout_snapshot",
    "composer/intelligence/validation_result",
    "composer/intelligence/error",
]


class ComposerCore:
    """Dual-bus Composer backbone: document + dispatcher + intelligence.

    - internal bus: tool messages (composer/*)
    - external bus: MQTT bridge (Intelligence protocol) when a broker is
      configured (COMPOSEUX_MQTT_BROKER), same auto-connect contract as
      the CTk Composer.
    - design bus:  the PREVIEW bus ([DP-CXC-4]) — the user's design
      namespaces live here, separate from tool traffic.
    """

    def __init__(self, document: Optional[CompositeDocument] = None,
                 app_id: str = "composeux") -> None:
        self.document = document or CompositeDocument()
        self.app = ComposeApp(app_id=app_id)
        self.file_path: str = ""
        self._change_listeners: list[Callable[[str, dict], None]] = []
        self._started = False
        # undo/redo command stacks ([CXC-BE-POLISH], the CTk history.py
        # pattern): each entry is (undo_step, redo_step) where a step is
        # a (target_namespace, data) pair replayed through _apply.
        self._undo_stack: list[tuple] = []
        self._redo_stack: list[tuple] = []
        self._in_history = False
        # [SPEC:CXC-FLOW] live bus discovery — created in start()
        self.discovery = None

    # -- buses ------------------------------------------------------------

    @property
    def internal_bus(self):
        return self.app._internal_bus

    @property
    def design_bus(self):
        """The preview bus — the built widget's namespaces live here."""
        return self.app._external_bus

    # -- lifecycle -----------------------------------------------------------

    def start(self, connect_mqtt: bool = True) -> None:
        self._register_namespaces()
        self._setup_intelligence_bridging()
        self._subscribe()
        self.app.start()
        self.mqtt_connected = False
        broker = os.environ.get("COMPOSEUX_MQTT_BROKER")
        if connect_mqtt and broker:
            try:
                # unique per process: two Composers (or a kiosk + a dev
                # session) on one broker must NOT share a client_id — the
                # broker kicks duplicates (rc=7 ping-pong, see
                # [CXC-BIND] build note)
                self.app.connect_transport(
                    "mqtt", broker=broker,
                    client_id=f"composeux-m4-{os.getpid()}")
                self.mqtt_connected = True
                logger.info("MQTT bridge LIVE via %s", broker)
            except Exception as e:
                logger.error("MQTT connect failed (%s) — internal-bus-only", e)
        self._started = True
        self._sync_design_namespaces()
        # [SPEC:CXC-FLOW] bus discovery: observe EVERY namespace live on
        # the design/external bus — including traffic this design never
        # declared ([CXC-FL-DISCOVERY]).  Started after transports so the
        # wildcard subscription widens MQTT reception too.
        from .discovery import BusDiscovery
        self.discovery = BusDiscovery(self)
        self.discovery.start()

    def stop(self) -> None:
        if self._started:
            if self.discovery is not None:
                self.discovery.stop()
            self.app.stop()
            self._started = False

    # -- change notification (render thread hop happens in the UI) ---------

    def on_change(self, fn: Callable[[str, dict], None]) -> None:
        """UI hook: fn(namespace, data) after every document mutation."""
        self._change_listeners.append(fn)

    def _notify(self, namespace: str, data: dict, event_type: str) -> None:
        self.internal_bus.publish_event(namespace, event_type, data=data,
                                        source="composer_core")
        for fn in self._change_listeners:
            try:
                fn(namespace, data)
            except Exception:
                logger.exception("change listener failed")

    # -- namespace registration -----------------------------------------------

    def _register_namespaces(self) -> None:
        ib = self.internal_bus
        ib.register_namespace("composer/selection/type", {
            "type": "enum",
            "values": ["none", "widget", "namespace"],
            "default": "none"})
        ib.register_namespace("composer/selection/widget_id", "json")
        ib.register_namespace("composer/wc/active_tab", {
            "type": "enum",
            "values": ["overview", "layers", "widgets", "bindings", "source",
                       "flow"],
            "default": "overview"})
        # [SPEC:CXC-FLOW] Layout<->Flow lens + the selected connection —
        # both are BUS STATE, so an Intelligence toggles/inspects exactly
        # like a human ([CXC-FL-C9]).
        ib.register_namespace("composer/ww/mode", {
            "type": "enum", "values": ["layout", "flow"],
            "default": "layout"})
        ib.register_namespace("composer/flow/selected", "json")
        ib.register_namespace("composer/file/dirty", "binary")
        ib.register_namespace("composer/file/path", "json")
        ib.register_namespace("composer/connection/status", {
            "type": "enum",
            "values": ["offline", "connecting", "connected", "error"],
            "default": "offline"})
        ib.register_namespace("composer/intelligence/heartbeat", "binary")
        ib.register_namespace("composer/window/open", "json")

        ib.register_namespace("composer/source/status", "json")
        # [CXC-FLAUTH-DEPLOY] roster push: a bus command, so an
        # Intelligence deploys exactly like the deploy-card button
        ib.register_namespace("composer/deploy/status", "json")
        for ns in (["composer/command/execute",
                    "composer/file/loaded", "composer/file/saved",
                    "composer/file/exported", "composer/file/save",
                    "composer/file/load", "composer/file/export",
                    "composer/source/replace", "composer/dsl/candidate",
                    "composer/deploy/push",
                    "composer/history/undo", "composer/history/redo"]
                   + CANVAS_NAMESPACES + INTEL_IN + INTEL_OUT):
            ib.ensure_namespace(ns)

    def _setup_intelligence_bridging(self) -> None:
        """MQTT crossover — identical contract to the CTk ComposerApp,
        plus the M4 additions: WC tab switching and selection are
        Intelligence-drivable states."""
        for ns in (INTEL_IN + ["composer/command/execute"]
                   + CANVAS_NAMESPACES
                   + ["composer/wc/active_tab",
                      "composer/ww/mode", "composer/flow/selected",
                      "composer/selection/widget_id",
                      "composer/file/save", "composer/file/export",
                      "composer/file/load",
                      "composer/source/replace", "composer/dsl/candidate",
                      "composer/deploy/push",
                      "composer/history/undo", "composer/history/redo"]):
            self.app.import_namespace(ns)
        for ns in INTEL_OUT:
            self.app.register(ns, engine="binary", export=True)

    def _sync_design_namespaces(self) -> None:
        """Ensure every document namespace exists on the preview bus."""
        from composeio.bus import is_pattern
        for ns in self.document.namespaces:
            if is_pattern(ns.name):
                continue          # a pattern subscription is not a namespace
            try:
                self.design_bus.ensure_namespace(ns.name,
                                                 engine_config=ns.state_engine,
                                                 label=ns.label or None)
            except Exception:
                logger.exception("preview namespace %s", ns.name)

    # -- bus subscriptions ---------------------------------------------------

    def _subscribe(self) -> None:
        ib = self.internal_bus
        ib.subscribe_events("composer/command/execute", self._on_command)
        for ns in CANVAS_NAMESPACES:
            ib.subscribe_events(ns, self._on_canvas_event)
        ib.subscribe_events("composer/intelligence/request_state",
                            self._on_request_state)
        ib.subscribe_events("composer/intelligence/request_layout",
                            self._on_request_layout)
        ib.subscribe_events("composer/intelligence/validate",
                            self._on_validate)
        ib.subscribe_events("composer/file/save", self._on_file_save)
        ib.subscribe_events("composer/file/export", self._on_file_save)
        ib.subscribe_events("composer/file/load", self._on_file_load)
        ib.subscribe_events("composer/source/replace", self._on_source_replace)
        ib.subscribe_events("composer/dsl/candidate", self._on_dsl_candidate)
        ib.subscribe_events("composer/deploy/push", self._on_deploy_push)
        ib.subscribe_events("composer/history/undo",
                            lambda ns, et, d: self.undo()
                            if et not in ("executed", "replayed") else None)
        ib.subscribe_events("composer/history/redo",
                            lambda ns, et, d: self.redo()
                            if et not in ("executed", "replayed") else None)

    # -- command dispatch ([DP-CXC-9] forward-exec path) -----------------------

    def _on_command(self, namespace: str, event_type: str, data: dict) -> None:
        if event_type != "execute":
            return
        ctype = str(data.get("type", ""))
        do = data.get("do", {}) or {}
        target = ctype if ctype.startswith("composer/") else ""
        if not target:
            logger.warning("unknown command type: %s", ctype)
            return
        self._apply(target, do, "executed")

    def _on_canvas_event(self, namespace: str, event_type: str,
                         data: dict) -> None:
        """Direct events on canvas namespaces (the human UI path publishes
        these; MQTT-imported copies arrive here too).  'executed'/'replayed'
        are our own notifications — ignore.  'do'/'direct' apply."""
        if event_type in ("executed", "replayed"):
            return
        self._apply(namespace, data, "executed")

    def _apply(self, target: str, data: dict, notify: str) -> None:
        handler = self._HANDLERS.get(target)
        if handler is None:
            logger.warning("no handler for %s", target)
            return
        inverse = None if self._in_history else self._inverse(target, data)
        try:
            changed = handler(self, data)
        except Exception:
            logger.exception("dispatch %s failed: %s", target, data)
            return
        if changed:
            if not self._in_history:
                if inverse is None and target.endswith("widget_added"):
                    # the handler assigned the widget_id — invertible now
                    inverse = ("composer/canvas/widget_removed",
                               {"widget_id": data.get("widget_id")})
                if inverse is not None:
                    self._undo_stack.append((inverse, (target, dict(data))))
                    self._redo_stack.clear()
            self.internal_bus.publish_state("composer/file/dirty", "on",
                                            source="composer_core")
            self._notify(target, data, notify)

    # -- undo/redo ([CXC-BE-POLISH]) ----------------------------------------

    def _inverse(self, target: str, data: dict) -> Optional[tuple]:
        """The (target, data) step that reverts *data*, captured BEFORE the
        mutation.  widget_added is inverted after apply (id known then)."""
        doc = self.document
        wid = str(data.get("widget_id", "") or "")
        layer = doc.get_layer(wid) if wid else None
        if target.endswith("widget_added"):
            return None                               # post-apply inversion
        if target.endswith("widget_removed"):
            if layer is None:
                return None
            spec = layer.to_spec()
            return ("composer/canvas/widget_added", {
                "widget_id": layer.widget_id,
                "widget_type": layer.widget_type,
                "x": layer.x, "y": layer.y, "w": layer.w, "h": layer.h,
                "namespace": layer.namespace,
                "properties": {k: v for k, v in spec.items()
                               if k not in ("type", "id", "x", "y", "w", "h",
                                            "namespace")},
                "_index": doc.layer_index(wid),
            })
        if target.endswith("widget_moved"):
            if layer is None:
                return None
            return (target, {"widget_id": wid, "x": layer.x, "y": layer.y})
        if target.endswith("widget_resized"):
            if layer is None:
                return None
            return (target, {"widget_id": wid, "x": layer.x, "y": layer.y,
                             "w": layer.w, "h": layer.h})
        if target.endswith("widget_changed"):
            prop = str(data.get("property", data.get("property_name", "")))
            if not prop:
                return None
            if layer is not None:
                if prop == "visible" and not isinstance(
                        layer.properties.get("visible"), (dict, str)):
                    old: Any = layer.visible
                elif prop == "z_index":
                    old = doc.layer_index(wid)
                elif prop == "namespace":
                    old = layer.namespace
                elif prop in ("x", "y", "w", "h"):
                    old = getattr(layer, prop)
                elif prop == "widget_id":
                    old = layer.widget_id
                else:
                    old = layer.properties.get(prop)   # None -> pop on undo
                return (target, {"widget_id": wid, "property": prop,
                                 "value": old})
            if prop in ("name", "width", "height", "purpose"):
                return (target, {"property": prop,
                                 "value": getattr(doc, prop)})
            if prop == "deploy":
                return (target, {"property": "deploy",
                                 "value": doc.extras.get("deploy")})
            return None
        if target.endswith("namespace_changed"):
            name = str(data.get("name", ""))
            ns = doc.get_namespace(name)
            action = data.get("action", "created")
            if action == "removed" and ns is not None:
                return (target, {"action": "created", "name": ns.name,
                                 "label": ns.label,
                                 "state_engine": ns.state_engine,
                                 "external": ns.external})
            if action == "modified" and ns is not None:
                prop = str(data.get("property",
                                    data.get("property_name", "")))
                if prop in ("label", "state_engine", "external"):
                    return (target, {"action": "modified", "name": name,
                                     "property": prop,
                                     "value": getattr(ns, prop)})
                return None
            if action == "created" and ns is None:
                return (target, {"action": "removed", "name": name})
            return None
        if target.endswith("source/replace"):
            return (target, {"yaml": doc.to_yaml()})
        return None

    def _replay(self, step: tuple) -> None:
        target, data = step
        self._in_history = True
        try:
            self._apply(target, dict(data), "executed")
        finally:
            self._in_history = False

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        undo_step, redo_step = self._undo_stack.pop()
        self._replay(undo_step)
        self._redo_stack.append((undo_step, redo_step))
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        undo_step, redo_step = self._redo_stack.pop()
        self._replay(redo_step)
        self._undo_stack.append((undo_step, redo_step))
        return True

    # -- handlers ---------------------------------------------------------

    def _h_widget_added(self, data: dict) -> bool:
        wid = str(data.get("widget_id", "") or "")
        wtype = str(data.get("widget_type", "rect"))
        entry = catalog_get(wtype)
        if not wid:
            wid = self.document.next_id(entry.prefix if entry else wtype)
            data["widget_id"] = wid
        if self.document.get_layer(wid) is not None:
            logger.warning("widget %s already exists", wid)
            return False
        # deep copy: nested default dicts (e.g. the logic entry's
        # headless.schedule) must never be shared across layers
        import copy as _copy
        props = _copy.deepcopy(entry.defaults) if entry else {}
        props.update(data.get("properties", {}) or {})
        vis = props.pop("visible", True)
        if not isinstance(vis, bool):
            props["visible"] = vis        # binding form stays raw
            vis = True
        x = float(data.get("x", data.get("column", 0)) or 0)
        y = float(data.get("y", data.get("row", 0)) or 0)
        w = data.get("w", data.get("width"))
        h = data.get("h", data.get("height"))
        if w is None and entry and entry.size[0]:
            w = entry.size[0]
        if h is None and entry and entry.size[1]:
            h = entry.size[1]
        layer = LayerDef(widget_id=wid, widget_type=wtype, x=x, y=y,
                         w=float(w) if w is not None else None,
                         h=float(h) if h is not None else None,
                         visible=vis,
                         namespace=str(data.get("namespace", "") or ""),
                         properties=props)
        idx = data.get("_index")
        self.document.add_layer(layer,
                                index=int(idx) if idx is not None else None)
        return True

    def _h_widget_removed(self, data: dict) -> bool:
        wid = str(data.get("widget_id", ""))
        return self.document.remove_layer(wid) is not None

    def _h_widget_moved(self, data: dict) -> bool:
        layer = self.document.get_layer(str(data.get("widget_id", "")))
        if layer is None:
            return False
        layer.x = float(data.get("x", data.get("column", layer.x)))
        layer.y = float(data.get("y", data.get("row", layer.y)))
        return True

    def _h_widget_resized(self, data: dict) -> bool:
        layer = self.document.get_layer(str(data.get("widget_id", "")))
        if layer is None:
            return False
        layer.x = float(data.get("x", layer.x))
        layer.y = float(data.get("y", layer.y))
        w = data.get("w", data.get("columnspan"))
        h = data.get("h", data.get("rowspan"))
        if w is not None:
            layer.w = max(8.0, float(w))
        if h is not None:
            layer.h = max(8.0, float(h))
        return True

    def _h_widget_changed(self, data: dict) -> bool:
        wid = data.get("widget_id")
        prop = str(data.get("property", data.get("property_name", "")))
        value = data.get("value")
        if not prop:
            return False
        if wid:
            layer = self.document.get_layer(str(wid))
            if layer is None:
                return False
            if prop == "visible":
                # a dict OR bare-namespace-string value is a [SPEC:CXC-BIND]
                # visible binding — kept RAW in properties ([CXC-BE-RAWSPEC]).
                # Only bools and the bool vocabulary drive the design-time
                # eye.  (FLOW fix: the picker's sanctioned bare-string
                # simplest form was being coerced to the eye bool and
                # silently dropped.)
                if isinstance(value, dict) or (
                        isinstance(value, str)
                        and value.strip().lower() not in _BOOL_WORDS):
                    layer.properties["visible"] = value
                else:
                    layer.properties.pop("visible", None)
                    layer.visible = _to_bool(value)
            elif prop == "z_index":
                self.document.reorder_layer(layer.widget_id, int(value))
            elif prop == "namespace":
                layer.namespace = str(value or "")
            elif prop in ("x", "y"):
                setattr(layer, prop, float(value))
            elif prop in ("w", "h"):
                setattr(layer, prop, float(value) if value is not None else None)
            elif prop == "widget_id" and value:
                layer.widget_id = str(value)
            elif value is None:
                # None removes the property — e.g. unbinding an attribute
                layer.properties.pop(prop, None)
            else:
                layer.properties[prop] = value
            return True
        # design-level property
        if prop == "name":
            self.document.name = str(value)
        elif prop == "width":
            self.document.width = max(64, int(float(value)))
        elif prop == "height":
            self.document.height = max(64, int(float(value)))
        elif prop == "purpose":
            self.document.purpose = str(value or "")
        elif prop == "deploy":
            # [CXC-FLAUTH-DEPLOY] roster assignment ({runtime_id, widgets})
            # — design metadata in the raw-spec extras passthrough
            if value:
                self.document.extras["deploy"] = value
            else:
                self.document.extras.pop("deploy", None)
        else:
            logger.warning("unknown design property %s", prop)
            return False
        return True

    def _h_namespace_changed(self, data: dict) -> bool:
        name = str(data.get("name", ""))
        if not name:
            return False
        action = data.get("action", "created")
        if action == "removed":
            return self.document.remove_namespace(name) is not None
        if action == "modified":
            ns = self.document.get_namespace(name)
            if ns is None:
                return False
            prop = str(data.get("property", data.get("property_name", "")))
            if prop in ("label", "state_engine"):
                setattr(ns, prop, data.get("value"))
                return True
            if prop == "external":
                ns.external = bool(data.get("value"))
                return True
            return False
        self.document.add_namespace(NamespaceDef(
            name=name, label=str(data.get("label", "") or ""),
            state_engine=data.get("state_engine", "json"),
            external=bool(data.get("external", False))))
        self._sync_design_namespaces()
        return True

    # -- Source tab: whole-document replace ([CXC-BE-SOURCE]) ---------------

    def _on_source_replace(self, namespace: str, event_type: str,
                           data: dict) -> None:
        if event_type in ("executed", "replayed"):
            return
        self._apply("composer/source/replace", data, "executed")

    def _h_source_replace(self, data: dict) -> bool:
        """Replace the document from YAML text.  Commit ONLY on a valid
        parse ([CXC-BE-C5]): a failed parse leaves the document untouched
        and reports on composer/source/status."""
        text = str(data.get("yaml", ""))
        try:
            doc = CompositeDocument.from_yaml(text)
        except Exception as e:
            self.internal_bus.publish_state(
                "composer/source/status",
                {"ok": False, "error": _yaml_error_summary(e),
                 "line": _yaml_error_line(e)},
                source="composer_core")
            return False
        self.document.__dict__.update(doc.__dict__)
        self._sync_design_namespaces()
        self.internal_bus.publish_state("composer/source/status",
                                        {"ok": True}, source="composer_core")
        return True

    # -- headless roster push ([SPEC:CXC-FLAUTH] [CXC-FLAUTH-DEPLOY]) --------

    # Injectable loader (tests: LocalFileLoader(tmp)); None -> the
    # production BusStorageLoader (bus://assets/headless/...).
    deploy_loader: Any = None

    def _on_deploy_push(self, namespace: str, event_type: str,
                        data: dict) -> None:
        if event_type in ("executed", "replayed", "pushed"):
            return
        try:
            self.deploy_push(runtime_id=data.get("runtime_id"),
                             widget_ids=data.get("widgets"))
        except Exception as e:
            logger.exception("deploy push failed")
            self.internal_bus.publish_state(
                "composer/deploy/status",
                {"ok": False, "error": str(e)[:200]}, source="composer_core")
            self.internal_bus.publish_event(
                "composer/intelligence/error", "error",
                data={"error": "deploy_failed", "detail": str(e)},
                source="composer_core")

    def deploy_push(self, runtime_id: Optional[str] = None,
                    widget_ids: Optional[list] = None) -> dict:
        """Push the roster + widget configs through the Pass-1 loader
        contract ([CXC-HW-DEPLOY]; write side).  Defaults come from the
        document's ``deploy:`` assignment."""
        from . import deploy as deploy_mod
        loader = self.deploy_loader
        if loader is None:
            from composeio.render.engine.headless import BusStorageLoader
            loader = BusStorageLoader()
        result = deploy_mod.push(self.document, loader,
                                 runtime_id=runtime_id,
                                 widget_ids=widget_ids)
        result["timestamp"] = time.time()
        self.internal_bus.publish_state(
            "composer/deploy/status", {"ok": True, **result},
            source="composer_core")
        self.internal_bus.publish_event(
            "composer/deploy/push", "pushed", data=result,
            source="composer_core")
        logger.info("deployed roster %s (%d widgets) -> %s",
                    result["runtime_id"], len(result["widgets"]),
                    result["roster"])
        return result

    # -- DSL-candidate capture ([CXC-BE-EDGES] / [CXC-BE-C8]) ---------------

    DSL_CANDIDATES_PATH = os.path.expanduser(
        "~/.composeio/dsl_candidates.jsonl")

    def _on_dsl_candidate(self, namespace: str, event_type: str,
                          data: dict) -> None:
        if event_type in ("executed", "replayed", "logged"):
            return
        self.log_dsl_candidate(data)

    def log_dsl_candidate(self, data: dict) -> str:
        """Append an 'I wanted arithmetic here' need to the DSL-candidate
        log — the collection feeding the 5 gating use-cases
        ([CXC-RM-BEHAVIORS]).  NOT an inline-code field."""
        import json
        entry = {
            "timestamp": time.time(),
            "design": self.document.name,
            "file": self.file_path,
            **{k: v for k, v in data.items() if k != "timestamp"},
        }
        path = self.DSL_CANDIDATES_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.warning("[CXC-BE-C8] DSL use-case candidate logged to %s: %s",
                       path, entry.get("need", entry))
        self.internal_bus.publish_event("composer/dsl/candidate", "logged",
                                        data={"path": path},
                                        source="composer_core")
        return path

    _HANDLERS: dict[str, Any] = {
        "composer/canvas/widget_added": _h_widget_added,
        "composer/canvas/widget_removed": _h_widget_removed,
        "composer/canvas/widget_moved": _h_widget_moved,
        "composer/canvas/widget_resized": _h_widget_resized,
        "composer/config/widget_changed": _h_widget_changed,
        "composer/config/namespace_changed": _h_namespace_changed,
        "composer/source/replace": _h_source_replace,
    }

    # -- intelligence responders (spec 06) --------------------------------

    def _on_request_state(self, namespace: str, event_type: str,
                          data: dict) -> None:
        selection = {"type": "none"}
        sel = self.internal_bus.get_state("composer/selection/widget_id")
        if sel:
            selection = {"type": "widget", "widget_id": sel}
        namespaces = {}
        for ns_name, ns_obj in self.design_bus.namespaces.items():
            try:
                namespaces[ns_name] = ns_obj.value
            except Exception:
                namespaces[ns_name] = None
        response = {
            "design": self.document.snapshot(),
            "namespaces": namespaces,
            "connection_status": ("connected" if getattr(self, "mqtt_connected",
                                                         False) else "offline"),
            "selection": selection,
            "source": "composer_core",
            "timestamp": time.time(),
        }
        # [CXC-FL-C9] the Bus·Live rail's content is Intelligence-visible:
        # what a human sees in the discovery rail, a peer sees here.
        if self.discovery is not None:
            response["bus_live"] = self.discovery.snapshot_brief()
        if data.get("correlation_id"):
            response["correlation_id"] = data["correlation_id"]
        self.internal_bus.publish_event("composer/intelligence/state_snapshot",
                                        "snapshot", data=response,
                                        source="composer_core")

    def _on_request_layout(self, namespace: str, event_type: str,
                           data: dict) -> None:
        """Pixel-accurate by construction: composite layers ARE px rects."""
        payload: dict[str, Any] = {
            "modules": [{
                "module_id": "composite",
                "role": "pixel-stack",
                "canvas": {"width": self.document.width,
                           "height": self.document.height},
                "widgets": [{
                    "widget_id": l.widget_id,
                    "widget_type": l.widget_type,
                    "namespace": l.namespace,
                    "visible": l.visible,
                    "z_index": i,
                    "bounds": {"x": l.x, "y": l.y, "w": l.w, "h": l.h},
                } for i, l in enumerate(self.document.layers)],
            }],
            "source": "composer_core",
            "timestamp": time.time(),
        }
        if data.get("correlation_id"):
            payload["correlation_id"] = data["correlation_id"]
        self.internal_bus.publish_event("composer/intelligence/layout_snapshot",
                                        "snapshot", data=payload,
                                        source="composer_core")

    def _on_validate(self, namespace: str, event_type: str,
                     data: dict) -> None:
        errors, warnings = self.validate()
        response: dict[str, Any] = {
            "valid": not errors,
            "errors": [{"type": "validation_error", "message": e}
                       for e in errors],
            "warnings": [{"type": "validation_warning", "message": w}
                         for w in warnings],
            "source": "composer_core",
            "timestamp": time.time(),
        }
        if data.get("correlation_id"):
            response["correlation_id"] = data["correlation_id"]
        self.internal_bus.publish_event(
            "composer/intelligence/validation_result", "result",
            data=response, source="composer_core")

    def validate(self) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        seen: set[str] = set()
        declared = {ns.name for ns in self.document.namespaces}
        used: set[str] = set()
        for l in self.document.layers:
            if l.widget_id in seen:
                errors.append(f"duplicate widget_id '{l.widget_id}'")
            seen.add(l.widget_id)
            if l.namespace:
                used.add(l.namespace)
                if l.namespace not in declared:
                    warnings.append(
                        f"'{l.widget_id}' subscribes undeclared namespace "
                        f"'{l.namespace}'")
            pub = l.properties.get("publish_namespace")
            if pub:
                used.add(str(pub))
                if str(pub) not in declared:
                    warnings.append(
                        f"'{l.widget_id}' publishes undeclared namespace "
                        f"'{pub}'")
        # attr bindings + behavior triggers use namespaces too ([SPEC:CXC-FLOW]
        # fix: Flow/Bindings-authored wires must not false-warn "unused")
        for row in self.document.bindings():
            ns = str(row.get("namespace", ""))
            if ns and ns != "always":
                used.add(ns)
        for ns in declared - used:
            warnings.append(f"namespace '{ns}' is declared but unused")
        return errors, warnings

    # -- file ops ([CXC-FEAT-SERDE]) -----------------------------------------

    def _on_file_save(self, namespace: str, event_type: str,
                      data: dict) -> None:
        path = str(data.get("path", "") or self.file_path)
        if not path:
            path = self.document.slug + ".compose.yaml"
        try:
            self.save(path)
        except Exception as e:
            logger.exception("save failed")
            self.internal_bus.publish_event(
                "composer/intelligence/error", "error",
                data={"error": "save_failed", "detail": str(e)},
                source="composer_core")

    def _on_file_load(self, namespace: str, event_type: str,
                      data: dict) -> None:
        path = str(data.get("path", ""))
        if not path:
            return
        try:
            self.load(path)
        except Exception as e:
            logger.exception("load failed")
            self.internal_bus.publish_event(
                "composer/intelligence/error", "error",
                data={"error": "load_failed", "detail": str(e)},
                source="composer_core")

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.document.to_yaml())
        self.file_path = path
        self.internal_bus.publish_state("composer/file/path", path,
                                        source="composer_core")
        self.internal_bus.publish_state("composer/file/dirty", "off",
                                        source="composer_core")
        self._notify("composer/file/saved", {"path": path}, "saved")
        logger.info("saved %s", path)
        return path

    def load(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            doc = CompositeDocument.from_yaml(f.read())
        # replace contents in-place so every holder of the reference sees it
        self.document.__dict__.update(doc.__dict__)
        self.file_path = path
        self._sync_design_namespaces()
        self.internal_bus.publish_state("composer/file/path", path,
                                        source="composer_core")
        self._notify("composer/file/loaded", {"path": path}, "loaded")
        logger.info("loaded %s", path)


# the strings that mean "the eye", not "a namespace binding"
_BOOL_WORDS = frozenset({"", "0", "off", "false", "none", "hidden", "no",
                         "1", "on", "true", "visible", "shown", "yes"})


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() not in ("", "0", "off", "false", "none", "hidden")


def _yaml_error_summary(e: Exception) -> str:
    """First line of a YAML/parse error — inline-display friendly."""
    return str(e).strip().split("\n")[0][:160] or type(e).__name__


def _yaml_error_line(e: Exception) -> Optional[int]:
    """0-based line of a yaml.MarkedYAMLError, when available."""
    mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
    line = getattr(mark, "line", None)
    return int(line) if line is not None else None
