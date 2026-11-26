"""bus_storage — the config-over-bus STORE, wearing the ComposeServer plugin
contract ([SPEC:CXC-COMPOSESERVER] [CXC-CS-PLUGIN-CONTRACT], Wave-2b adaptation).

WHAT THIS IS. The extant ``services/bus_storage/`` is an HTTP config-over-bus
store: it holds per-namespace compiled config (``bus://`` assets, workloads and a
namespace registry too), served today over a standalone ``ThreadingHTTPServer``
on ``:8420``. DP-CIO-23: bus storage is the SOURCE OF TRUTH; local flash on edge
devices is only a cache. This module REPOINTS that store's config face onto
ComposeServer's per-tenant bus — the same store, reached over the bus and over
MCP instead of only over HTTP — without rewriting the storage engine.

REUSE, NEVER RE-IMPLEMENT ([CXC-CSL-LOADER-INTERFACE] reuse-by-reference). The
ratified path-traversal containment ``_resolve_contained()``
([CXC-BUSSTORE-CONTAINMENT]) and the atomic-write primitives are IMPORTED from
the extant ``services/bus_storage/server.py`` VERBATIM — not copied, so they
cannot drift and the negative control exercises the SAME guard the HTTP service
does. The optional HTTP face reuses that service's own ``BusStorageHandler`` /
``_ThreadingServer`` unchanged.

THE CONTRACT, DERIVED FROM ONE DECLARATION ([CXC-CS-PLUGIN-CONTRACT]). The
plugin declares one pydantic ``Config`` plus its class-level ``mcp_tools``; the
config form, the announce payload, the registry entry and the MCP tool
descriptors are all DERIVED by the base class. This author writes none of them.

THE BUS SURFACE (fixed namespaces — the announce carries them statically, so
they are class constants, never config-derived [CXC-MCP-TOOL-DECLARATION]):
  * ``bus_storage/config/store/request`` — a ``state`` publish (the MCP WRITE
    tool's target) carrying ``{target_namespace_id, config_payload_base64,
    config_metadata_json}``; the plugin persists it, contained.
  * ``bus_storage/config/fetch/query`` — a [SPEC:CXC-BUS-QUERY] request; the
    plugin answers on ``bus_storage/config/fetch/result/{src}`` with the stored
    config for one namespace.
  * ``bus_storage/config/list/query`` — a query the plugin answers on
    ``bus_storage/config/list/result/{src}`` with every stored namespace id.

CONFIG BYTES OVER A JSON BUS. Compiled config is BINARY; the bus carries JSON.
So every payload crosses the bus base64-encoded, and the field names SAY so
([DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]) — an Intelligence must
never have to guess an encoding.
"""
from __future__ import annotations

import base64
import binascii
import importlib.util
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from composeio.namespace import Scope as _Scope
from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    ToolDirection,
)

logger = logging.getLogger("composeio.plugins.bus_storage")

# --------------------------------------------------------------------------- #
# Fixed bus namespaces — STATIC on purpose. The derived announce carries each
# tool's namespace_mapping literally, and the MCP router binds off that announce
# ([CXC-MCP-ROUTER-MECHANICS]); a per-instance, config-driven namespace could not
# be expressed in the class-level ``mcp_tools`` the announce is derived from.
# --------------------------------------------------------------------------- #
NS_PREFIX = "bus_storage"
STORE_REQUEST_NS = f"{NS_PREFIX}/config/store/request"
STORE_ERROR_NS = f"{NS_PREFIX}/config/store/error"
FETCH_QUERY_NS = f"{NS_PREFIX}/config/fetch/query"
FETCH_RESULT_ROOT = f"{NS_PREFIX}/config/fetch"
LIST_QUERY_NS = f"{NS_PREFIX}/config/list/query"
LIST_RESULT_ROOT = f"{NS_PREFIX}/config/list"


# --------------------------------------------------------------------------- #
# The ratified storage primitives — IMPORTED from the extant service, never
# copied ([CXC-BUSSTORE-CONTAINMENT], preserve-do-not-regress). Loaded from the
# service file by path because ``services/bus_storage/`` is not (yet) an
# importable package; the resolved module is cached for the process.
# --------------------------------------------------------------------------- #
_SERVICE_MOD: Any = None
_SERVICE_LOCK = threading.Lock()


def _service_module() -> Any:
    """Load ``services/bus_storage/server.py`` once and return it.

    The path is resolved relative to this file's repo root, overridable via
    ``COMPOSEIO_BUS_STORAGE_SERVICE`` for deployments that relocate the service
    tree. Raises a clear ``RuntimeError`` (surfaced as a loud plugin-start
    failure, never a silent degrade) if the ratified guard cannot be reached —
    the store must NEVER run without its containment primitive.
    """
    global _SERVICE_MOD
    with _SERVICE_LOCK:
        if _SERVICE_MOD is not None:
            return _SERVICE_MOD
        override = os.environ.get("COMPOSEIO_BUS_STORAGE_SERVICE")
        if override:
            server_path = Path(override)
        else:
            # plugins/ -> composeio/ -> python/ -> <repo root>
            repo_root = Path(__file__).resolve().parents[3]
            server_path = repo_root / "services" / "bus_storage" / "server.py"
        if not server_path.is_file():
            raise RuntimeError(
                f"bus_storage plugin cannot locate the ratified storage service "
                f"at {server_path!r}; set COMPOSEIO_BUS_STORAGE_SERVICE to its "
                f"server.py. The containment guard lives there and the plugin "
                f"refuses to run without it ([CXC-BUSSTORE-CONTAINMENT]).")
        spec = importlib.util.spec_from_file_location(
            "composeio._vendored_bus_storage_service", server_path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise RuntimeError(f"cannot import bus_storage service at {server_path!r}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SERVICE_MOD = module
        return module


_CHANGE_NOTIFY_MOD: Any = None


def _change_notify_module() -> Any:
    """Load ``services/bus_storage/change_notify.py`` once and return it.

    Sits next to ``server.py`` (resolved off the same path as ``_service_module``
    / the ``COMPOSEIO_BUS_STORAGE_SERVICE`` override). This is the asset-change
    PRODUCER the standalone ``server.main()`` wired into the module-global
    ``_NOTIFIER``; the plugin must load + wire it itself because it never calls
    ``main()`` ([CXC-ACN-SHAPE], fork-divergence fix)."""
    global _CHANGE_NOTIFY_MOD
    with _SERVICE_LOCK:
        if _CHANGE_NOTIFY_MOD is not None:
            return _CHANGE_NOTIFY_MOD
        override = os.environ.get("COMPOSEIO_BUS_STORAGE_SERVICE")
        server_path = (Path(override) if override
                       else Path(__file__).resolve().parents[3]
                       / "services" / "bus_storage" / "server.py")
        cn_path = server_path.with_name("change_notify.py")
        if not cn_path.is_file():
            raise RuntimeError(
                f"bus_storage plugin cannot locate the asset-change producer at "
                f"{cn_path!r} (expected beside server.py).")
        spec = importlib.util.spec_from_file_location(
            "composeio._vendored_bus_storage_change_notify", cn_path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise RuntimeError(f"cannot import change_notify at {cn_path!r}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CHANGE_NOTIFY_MOD = module
        return module


# --------------------------------------------------------------------------- #
# The config store — a thin adapter over the ratified primitives. It owns ONLY
# the config family (the MCP surface's scope); assets/workloads/registry stay
# with the HTTP service, reachable through the optional HTTP face below.
# --------------------------------------------------------------------------- #
class _ConfigStore:
    """Per-namespace compiled-config persistence, contained.

    EVERY request-supplied ``namespace_id`` is resolved through the imported
    ``_resolve_contained`` before it touches the filesystem, so a traversal id
    (``../../etc/x``) or an absolute id raises ``PathEscape`` and writes/reads
    NOTHING outside the config root — the same refusal the HTTP service gives.
    """

    def __init__(self, data_dir: str):
        svc = _service_module()
        self._svc = svc
        self._config_dir = Path(data_dir) / "configs"
        self._config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def PathEscape(self):  # the ratified refusal type, for callers to catch
        return self._svc.PathEscape

    def store(self, namespace_id: str, data: bytes,
              metadata: Optional[str] = None) -> None:
        target = self._svc._resolve_contained(
            self._config_dir, namespace_id, suffix=".bin")
        self._svc._atomic_write_bytes(target, data)
        if metadata is not None:
            meta = self._svc._resolve_contained(
                self._config_dir, namespace_id, suffix=".meta.json")
            self._svc._atomic_write_text(meta, metadata)

    def get(self, namespace_id: str) -> Optional[bytes]:
        target = self._svc._resolve_contained(
            self._config_dir, namespace_id, suffix=".bin")
        if not target.exists():
            return None
        return target.read_bytes()

    def list_namespace_ids(self) -> list[str]:
        return sorted(p.stem for p in self._config_dir.glob("*.bin"))


# --------------------------------------------------------------------------- #
# MCP tool argument models — NAMED FOR AN INTELLIGENCE
# ([DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]). Verbose over terse:
# every field name states WHAT it is and, where it matters, its ENCODING.
# --------------------------------------------------------------------------- #
class StoreConfigForNamespaceArgs(BaseModel):
    target_namespace_id: str = Field(
        ...,
        description=(
            "The namespace / module id whose compiled config is being stored "
            "(e.g. 'lights.kitchen'). This becomes the storage key; a value that "
            "escapes the config root is refused by the containment guard."),
    )
    config_payload_base64: str = Field(
        ...,
        description=(
            "The compiled config BINARY, base64-encoded. The bus carries JSON, "
            "so binary config must be base64 for transport; the plugin decodes it "
            "back to raw bytes before persisting."),
    )
    config_metadata_json: Optional[str] = Field(
        default=None,
        description=(
            "Optional metadata for this config, as a JSON string, stored "
            "alongside the binary (mirrors the HTTP X-ComposeIO-Metadata header). "
            "Omit when there is no metadata."),
    )


class GetStoredConfigForNamespaceArgs(BaseModel):
    target_namespace_id: str = Field(
        ...,
        description=(
            "The namespace / module id whose stored compiled config to fetch. "
            "Returns present=false when nothing has been stored for it yet, never "
            "a fabricated default."),
    )


class ListAllStoredConfigNamespacesArgs(BaseModel):
    """No arguments: this tool enumerates every namespace that currently has a
    stored config in this tenant's store."""


# --------------------------------------------------------------------------- #
# The plugin.
# --------------------------------------------------------------------------- #
class BusStoragePlugin(ComposePlugin):
    """The config-over-bus store as a ComposeServer plugin.

    Repoints the extant HTTP store's config face onto the tenant bus + MCP,
    preserving the ratified containment guard by importing it. Not
    ``default_loaded`` — a deployment opts a tenant into persistent storage via
    its manifest (``composeio.plugins.bus_storage:BusStoragePlugin``)."""

    class Config(ComposePlugin.Config):
        #: root directory for all persistent data (configs/, assets/, ...).
        #: Matches the extant service's COMPOSEIO_DATA_DIR default.
        data_dir: str = "/var/lib/composeio"
        #: run the extant HTTP face too (reuses BusStorageHandler verbatim). OFF
        #: by default: the plugin's primary face is the bus repoint, and the
        #: standalone HTTP service remains independently deployable for existing
        #: callers. See FINDING in the module header / report.
        serve_http: bool = False
        #: HTTP bind host/port when serve_http is set (extant COMPOSEIO_HOST/PORT).
        http_host: str = "0.0.0.0"
        http_port: int = 8420

    name = "bus_storage"
    version = "1.0.0"
    default_loaded = False

    # declared bus surface (announce). Result namespaces are per-requester
    # (``.../result/{src}``); the roots are listed for discoverability.
    subscribes = [STORE_REQUEST_NS, FETCH_QUERY_NS, LIST_QUERY_NS]
    publishes = [
        f"{FETCH_RESULT_ROOT}/result/*",
        f"{LIST_RESULT_ROOT}/result/*",
        STORE_ERROR_NS,
    ]

    # ARTIFACT 4/4 — the MCP surface, derived into the announce.
    mcp_tools = [
        McpToolDecl(
            name="store_config_for_namespace",
            description=(
                "Persist the compiled config for one namespace to bus storage — "
                "the SOURCE OF TRUTH (edge-device flash is only a cache). Supply "
                "the namespace id and its config binary base64-encoded; an "
                "optional JSON metadata string is stored alongside. The write is "
                "path-traversal contained: a namespace id that escapes the config "
                "root is refused and nothing is written."),
            input_model=StoreConfigForNamespaceArgs,
            direction=ToolDirection.WRITE,
            namespace_mapping=NamespaceMapping(publishes=[STORE_REQUEST_NS]),
            idempotent=True,
        ),
        McpToolDecl(
            name="get_stored_config_for_namespace",
            description=(
                "Fetch the compiled config stored for one namespace, returned "
                "base64-encoded. Reports present=false (never a fabricated "
                "default) when no config has been stored for that namespace. "
                "Path-traversal contained on the namespace id."),
            input_model=GetStoredConfigForNamespaceArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(subscribes=[FETCH_QUERY_NS]),
            idempotent=True,
        ),
        McpToolDecl(
            name="list_all_stored_config_namespaces",
            description=(
                "List every namespace id that currently has a compiled config "
                "stored in this tenant's bus storage. Takes no arguments."),
            input_model=ListAllStoredConfigNamespacesArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(subscribes=[LIST_QUERY_NS]),
            idempotent=True,
        ),
    ]

    def __init__(self, config=None):
        super().__init__(config)
        self._store: Optional[_ConfigStore] = None
        self._http_server = None
        self._http_thread: Optional[threading.Thread] = None

    # ---- lifecycle ------------------------------------------------------ #
    def start(self, bus) -> None:
        self._bus = bus
        self._store = _ConfigStore(self.config.data_dir)
        # the WRITE face: a state publish onto the store-request namespace.
        bus.subscribe(STORE_REQUEST_NS, self._on_store_request)
        # the QUERY face: a raw message hook so the responder can read the
        # requester's {src} off the envelope ([CXC-BUS-QUERY-IDIOM]).
        bus.on_message(self._on_message)
        if self.config.serve_http:
            self._start_http()
        logger.info(
            "bus_storage plugin started (data_dir=%s, serve_http=%s)",
            self.config.data_dir, self.config.serve_http)

    def stop(self) -> None:
        if self._http_server is not None:
            try:
                self._http_server.shutdown()
                # server_close() RELEASES the listen socket. shutdown() alone
                # only stops serve_forever's loop; the socket stays bound, so a
                # stop->start (systemd Restart, or a cutover rollback that must
                # hand :8420 back) would hit "address already in use". The
                # fingerprint_template_store plugin already pairs the two — match
                # it. (Found by test_composeserver_host_smoke stop-frees-bind.)
                self._http_server.server_close()
            except Exception:  # pragma: no cover - best-effort teardown
                logger.exception("bus_storage HTTP shutdown raised")
            self._http_server = None
            self._http_thread = None
        self._store = None
        super().stop()

    # ---- the WRITE face ------------------------------------------------- #
    def _on_store_request(self, namespace: str, new_value: Any, old_value: Any) -> None:
        """Handle a store-config publish. NEVER raises into the bus: a bad
        payload or a contained-path refusal is logged and published to the
        store-error namespace, best-effort, so one bad write can never fault the
        publisher or the bus."""
        store = self._store
        if store is None:  # pragma: no cover - stopped between publish and call
            return
        try:
            payload = new_value if isinstance(new_value, dict) else {}
            args = StoreConfigForNamespaceArgs(**payload)
            raw = base64.b64decode(args.config_payload_base64, validate=True)
            store.store(args.target_namespace_id, raw, args.config_metadata_json)
            logger.info("bus_storage stored config for %r (%d bytes)",
                        args.target_namespace_id, len(raw))
        except store.PathEscape as exc:
            self._publish_store_error(new_value, f"containment refusal: {exc}")
        except (binascii.Error, ValueError) as exc:
            self._publish_store_error(new_value, f"invalid payload: {exc}")
        except Exception as exc:  # noqa: BLE001 - never fault the bus on a write
            logger.exception("bus_storage store handler failed")
            self._publish_store_error(new_value, f"store failed: {exc}")

    def _publish_store_error(self, request: Any, message: str) -> None:
        logger.warning("bus_storage store refused/failed: %s", message)
        try:
            ns_id = request.get("target_namespace_id") if isinstance(request, dict) else None
            self._bus.publish_state(
                STORE_ERROR_NS,
                {"target_namespace_id": ns_id, "error": message},
                source=self.name)
        except Exception:  # pragma: no cover - error path is itself best-effort
            logger.exception("bus_storage failed to publish store error")

    # ---- the QUERY face ([CXC-BUS-QUERY] responder) --------------------- #
    def _on_message(self, msg: Any) -> None:
        """Answer fetch/list queries. Reads {src} off the envelope and replies on
        ``{root}/result/{src}``, echoing request_id verbatim ([CXC-BUS-QUERY-IDIOM])."""
        # a defensive import-free type check: EVENTs carry an event_type + data.
        if getattr(msg, "event_type", None) is None:
            return
        ns = getattr(msg, "namespace", None)
        if ns == FETCH_QUERY_NS:
            self._answer_fetch(msg)
        elif ns == LIST_QUERY_NS:
            self._answer_list(msg)

    def _answer_fetch(self, msg: Any) -> None:
        data = msg.data or {}
        rid = data.get("request_id")
        src = msg.source
        result_ns = f"{FETCH_RESULT_ROOT}/result/{src}"
        namespace_id = data.get("target_namespace_id")
        try:
            raw = self._store.get(namespace_id) if namespace_id else None
        except self._store.PathEscape as exc:
            # containment fires on the READ path too — the negative control.
            self._bus.publish_event(
                result_ns, "result",
                data={"request_id": rid, "present": False,
                      "error": f"containment refusal: {exc}"},
                source=self.name)
            return
        if raw is None:
            payload = {"request_id": rid, "target_namespace_id": namespace_id,
                       "present": False, "config_payload_base64": None}
        else:
            payload = {"request_id": rid, "target_namespace_id": namespace_id,
                       "present": True,
                       "config_payload_base64": base64.b64encode(raw).decode()}
        self._bus.publish_event(result_ns, "result", data=payload, source=self.name)

    def _answer_list(self, msg: Any) -> None:
        data = msg.data or {}
        rid = data.get("request_id")
        src = msg.source
        result_ns = f"{LIST_RESULT_ROOT}/result/{src}"
        ids = self._store.list_namespace_ids()
        self._bus.publish_event(
            result_ns, "result",
            data={"request_id": rid, "stored_namespace_ids": ids, "count": len(ids)},
            source=self.name)

    # ---- the optional HTTP face (extant behavior, reused verbatim) ------ #
    def _start_http(self) -> None:
        """Run the extant BusStorageHandler against this plugin's data_dir.

        Reuses the ratified handler + threading server UNCHANGED. It binds the
        service module's globals to this data_dir first; because those globals
        are process-wide, only one HTTP data_dir per process is meaningful — see
        FINDING. Best-effort: an HTTP bind failure must never fault the bus face.
        """
        try:
            svc = _service_module()
            data_dir = Path(self.config.data_dir)
            svc.DATA_DIR = data_dir
            svc.CONFIG_DIR = data_dir / "configs"
            svc.ASSETS_DIR = data_dir / "assets"
            svc.WORKLOADS_DIR = data_dir / "workloads"
            svc.REGISTRY_FILE = data_dir / "namespace_registry.json"
            svc.ensure_dirs()
            # WIRE THE ASSET-CHANGE PRODUCER onto the TENANT bus. The reused
            # BusStorageHandler PUT path calls svc._notify_asset_change(), which
            # reads the module-global svc._NOTIFIER; standalone server.main()
            # set that from COMPOSEUX_MQTT_BROKER (a SECOND broker connection),
            # but the plugin already HOLDS the tenant bus, so it injects it
            # directly — AssetChangeNotifier(bus=...), the in-process path,
            # reuse-by-reference not a forked second client ([CXC-ACN-SHAPE],
            # DP-CIO-6). Without this the {old->new} change event is INERT under
            # ComposeServer — the wiring lived ONLY in main() and the repoint
            # dropped it (fork-divergence). The namespace is pinned GLOBAL+json
            # so the event leaves the process onto the wire (mirrors from_env;
            # an AUTO namespace already resolves global here, but the explicit
            # pin matches the daemon and the json engine accepts the hash dict).
            cn = _change_notify_module()
            self._bus.register_namespace(
                cn.DEFAULT_NAMESPACE, engine_config="json", scope=_Scope.GLOBAL)
            svc._NOTIFIER = cn.AssetChangeNotifier(
                bus=self._bus, namespace=cn.DEFAULT_NAMESPACE)
            server = svc._ThreadingServer(
                (self.config.http_host, self.config.http_port), svc.BusStorageHandler)
            self._http_server = server
            self._http_thread = threading.Thread(
                target=server.serve_forever, name="bus_storage-http", daemon=True)
            self._http_thread.start()
            logger.info("bus_storage HTTP face on %s:%s",
                        self.config.http_host, self.config.http_port)
        except Exception:  # noqa: BLE001 - HTTP is additive, the bus face is not
            logger.exception("bus_storage HTTP face failed to start (bus face unaffected)")
