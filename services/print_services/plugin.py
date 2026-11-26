"""print_services as a ComposeServer PLUGIN — a PURELY ADDITIVE wrapper.

WHAT THIS IS. `[SPEC:CXC-PRINT-SERVICES]` (spec 83) ships print_services as a
standalone bare-Python bus service: it opens its OWN MQTT connection
(`server.py::PrintServices` + `bus.py::BusClient`) and hosts N printer backends
as bus peers. This module re-dresses that SAME service as a
`ComposePlugin` ([SPEC:CXC-COMPOSESERVER] [CXC-CS-PLUGIN-CONTRACT]) so it can be
DISCOVERED + LOADED by the config-manifest loader
(`composeserver_loader.py`), run against ComposeServer's per-tenant bus (the
repoint — no private broker connection), and expose an MCP surface
([SPEC:CXC-MCP] [CXC-MCP-TOOL-DECLARATION]) named FOR AN INTELLIGENCE
([DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]).

⛔ ADDITIVE, NOT A REWRITE. This file EDITS NOTHING in the print_services core.
It REUSES the shipped pieces BY REFERENCE:
  * `server.py::PrintServices` — the orchestration (submit handling, the
    submitted→result→record→status-refresh sequence, the graceful-shutdown
    tombstones) is reused VERBATIM by constructing one and repointing its
    `.bus` / `.backends` / `.page_cap` at plugin-config-derived values.
  * `backends.py::HpBackend` / `Elegoo3dBackend`, `cap.py::PageCap` — the
    backend realizations + the page cap, constructed straight from config.
The ONLY net-new surface is (1) `_TenantBusClient`, a thin adapter presenting
the `BusClient` public shape over a `ComposeIOBus` so `PrintServices` runs on
ComposeServer's bus with no code change; (2) a single AGGREGATE submit
subscription + an aggregate registry publish + a job-status query responder,
which are what the three MCP tools bind to; (3) the plugin/config/tool
declarations themselves. None of it reaches into the core.

WHY AN AGGREGATE SUBMIT NAMESPACE. The MCP `write` invoke path
([CXC-MCP-INVOKE-WRITE]) is TABLE-DRIVEN: it publishes the validated args to a
STATIC declared namespace — it cannot route to `print_services/{backend}/submit`
by an argument. So the plugin ALSO listens on one aggregate
`print_services/submit` carrying `backend_name` in the payload and dispatches
internally, while the per-backend `print_services/<id>/submit` subscriptions the
core wires stay intact for external/human transmitters. One Intelligence-facing
tool, zero behavior lost.

DP-CIO-6 holds throughout: this names no transport and imports no paho — the
tenant bus is handed in at `start(bus)`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from pydantic import BaseModel, Field

from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    ToolDirection,
)
from composeio.namespace import Scope
from composeio.transport import MessageType

from .server import PrintServices
from .cap import PageCap
from .backends import HpBackend, Elegoo3dBackend

logger = logging.getLogger("print_services.plugin")

_SOURCE = "print_services"

# Aggregate namespaces the MCP surface binds to (ADDITIVE — the per-backend
# `print_services/<id>/*` namespaces the core wires are untouched).
_AGG_SUBMIT_NS = "print_services/submit"
_AGG_REGISTRY_NS = "print_services/registry"
_JOBS_QUERY_NS = "print_services/jobs/query"


# --------------------------------------------------------------------------- #
# The MCP tool argument models — one typed pydantic model per tool; the MCP
# JSON-schema an Intelligence sees is DERIVED from these ([CXC-MCP-TOOL-
# DECLARATION] input-schema-is-the-single-derivation-source).
# --------------------------------------------------------------------------- #
class SubmitPrintJobToNamedBackendArgs(BaseModel):
    """Arguments for submitting one print job to a named backend."""

    backend_name: str = Field(
        description="Which print backend to submit to — one of the ids returned "
        "by list_available_print_backends (e.g. 'hp' for the HP LaserJet, "
        "'elegoo3d' for the Elegoo 3D printer).")
    content_type: str = Field(
        default="text",
        description="The document content type: 'text' or 'pdf' always; "
        "'markdown'/'html'/'svg' only when the backend advertises them.")
    content: str = Field(
        default="",
        description="The document body to print (text, markdown, HTML/SVG "
        "source, or a base64/path per the backend's content contract).")
    options: dict = Field(
        default_factory=dict,
        description="Backend print options, e.g. {'copies': 2}.")
    job_id: str | None = Field(
        default=None,
        description="Optional caller-chosen job id; when omitted the backend "
        "mints one. Use it later with get_print_job_status_by_id.")


class ListAvailablePrintBackendsArgs(BaseModel):
    """No arguments — returns the retained registry of every print backend."""


class GetPrintJobStatusByIdArgs(BaseModel):
    """Arguments for looking up one previously submitted print job by its id."""

    job_id: str = Field(
        description="The job id to look up — either the id returned when the job "
        "was submitted or the caller-chosen job_id passed at submit time.")


# --------------------------------------------------------------------------- #
# The tenant-bus adapter — presents the `BusClient` public surface over a
# `ComposeIOBus`, so the shipped `PrintServices` orchestration runs UNCHANGED on
# ComposeServer's bus. This is the whole "repoint": no private broker, no paho.
# --------------------------------------------------------------------------- #
class _TenantBusClient:
    """`BusClient`-shaped face over a `ComposeIOBus` ([CXC-CS-MICROKERNEL]:
    a plugin publishes/subscribes on the bus DIRECTLY).

    Exposes exactly the surface `server.py::PrintServices` calls —
    `connect`/`disconnect`/`connected`/`subscribe`/`publish_state`/
    `publish_event`/`publish_tombstone` — mirroring the wire choices the shipped
    `BusClient` made (result rides `data` with an `evt='result'` tag; a
    tombstone is a retained `None`, never a zero-length payload)."""

    def __init__(self, bus, source: str = _SOURCE):
        self._bus = bus
        self.source = source
        self.client_id = source

    def connect(self, connect_wait_s: float = 0.0) -> bool:
        # Already connected — the tenant bus is handed to us live at start().
        return True

    def disconnect(self) -> None:
        # Lifecycle is ComposeServer's; the plugin never owns the transport.
        return None

    @property
    def connected(self) -> bool:
        return True

    def subscribe(self, namespace: str, callback: Callable) -> None:
        def _on_state(_name: str, new_value, _old_value):
            callback(new_value, {})
        self._bus.subscribe(namespace, _on_state)

    def publish_state(self, namespace: str, value: Any, scope: str = "global",
                      retain: bool = True) -> None:
        self._bus.publish_state(namespace, value, source=self.source)

    def publish_event(self, namespace: str, value, scope: str = "global") -> None:
        self._bus.publish_event(namespace, "result", data=value, source=self.source)

    def publish_tombstone(self, namespace: str, scope: str = "global") -> None:
        self._bus.publish_state(namespace, None, source=self.source)


# --------------------------------------------------------------------------- #
# The plugin.
# --------------------------------------------------------------------------- #
class PrintServicesPlugin(ComposePlugin):
    """print_services wearing the ComposeServer plugin contract.

    ONE typed `Config` derives the config form, the announce payload, the
    registry entry AND the MCP tool surface ([CXC-CS-PLUGIN-CONTRACT] +
    [CXC-MCP-TOOL-DECLARATION]). `start(bus)` reuses the shipped
    `PrintServices` orchestration against `bus`; `stop()` runs its graceful
    tombstone shutdown."""

    class Config(ComposePlugin.Config):
        """Print-service config: the backend endpoints + the page cap.

        Field defaults mirror the extant `server.py` env defaults so a manifest
        that sets nothing behaves exactly like the standalone service on the
        home LAN."""

        hp_ipp_ip: str = Field(
            default="192.0.2.77",
            description="HP LaserJet IPP-Everywhere printer IP.")
        elegoo_sdcp_ip: str = Field(
            default="192.0.2.34",
            description="Elegoo Centauri Carbon SDCP printer IP.")
        page_cap_per_period: int = Field(
            default=100,
            description="Per-period page cap; over-cap jobs are refused honestly "
            "(not gated behind approval).")
        heartbeat_seconds: int = Field(
            default=30,
            description="Registry/status age-contract heartbeat period.")
        enable_hp_ipp_backend: bool = Field(
            default=True, description="Host the HP IPP backend.")
        enable_elegoo_sdcp_backend: bool = Field(
            default=True, description="Host the Elegoo SDCP backend.")

    name = "print_services"
    version = "0.1.0"
    # Declared class-level surface is the AGGREGATE MCP-facing namespaces; the
    # LIVE per-backend surface is authoritative via current_publishes/subscribes.
    publishes: list[str] = [_AGG_REGISTRY_NS]
    subscribes: list[str] = [_AGG_SUBMIT_NS, _JOBS_QUERY_NS]

    mcp_tools = [
        McpToolDecl(
            name="submit_print_job_to_named_backend",
            description=(
                "Submit one print job to a named print backend. Publishes the "
                "job onto the print bus; the backend answers asynchronously — "
                "poll get_print_job_status_by_id with the returned/echoed "
                "job_id for the terminal result."),
            input_model=SubmitPrintJobToNamedBackendArgs,
            direction=ToolDirection.WRITE,
            namespace_mapping=NamespaceMapping(publishes=[_AGG_SUBMIT_NS]),
        ),
        McpToolDecl(
            name="list_available_print_backends",
            description=(
                "List every print backend this service hosts, with each "
                "backend's id, kind, advertised content types and current "
                "registry row. Call this first to discover valid backend_name "
                "values for submit_print_job_to_named_backend."),
            input_model=ListAvailablePrintBackendsArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.RETAINED,
            namespace_mapping=NamespaceMapping(subscribes=[_AGG_REGISTRY_NS]),
            idempotent=True,
        ),
        McpToolDecl(
            name="get_print_job_status_by_id",
            description=(
                "Look up one previously submitted print job by its id and "
                "return its most recent recorded status/result, or an explicit "
                "not-found answer when no such job is in recent history."),
            input_model=GetPrintJobStatusByIdArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(subscribes=[_JOBS_QUERY_NS]),
            idempotent=True,
        ),
    ]

    def __init__(self, config=None, *, backends: "dict | None" = None):
        super().__init__(config)
        # `backends` is a TEST/advanced injection seam — the loader only ever
        # passes `config`, so this never widens the loader contract.
        self._injected_backends = backends
        self._backends: "dict | None" = None
        self._service: "PrintServices | None" = None
        self._source = _SOURCE
        self._hb_stop: "threading.Event | None" = None
        self._hb_thread: "threading.Thread | None" = None

    # -- backend construction ---------------------------------------------- #
    def _build_backends(self) -> dict:
        backends: dict = {}
        if self.config.enable_hp_ipp_backend:
            b = HpBackend(self.config.hp_ipp_ip)
            backends[b.id] = b
        if self.config.enable_elegoo_sdcp_backend:
            b = Elegoo3dBackend(self.config.elegoo_sdcp_ip)
            backends[b.id] = b
        return backends

    def _backend_ids(self) -> list[str]:
        if self._backends is not None:
            return list(self._backends)
        ids: list[str] = []
        if self.config.enable_hp_ipp_backend:
            ids.append("hp")
        if self.config.enable_elegoo_sdcp_backend:
            ids.append("elegoo3d")
        return ids

    # -- lifecycle --------------------------------------------------------- #
    def start(self, bus) -> None:
        self._bus = bus
        self._backends = (self._injected_backends
                          if self._injected_backends is not None
                          else self._build_backends())

        # Pre-declare the surface GLOBAL/json (behavior preservation: the
        # standalone client declared everything GLOBAL json so it left the
        # process, and json avoids the binary-engine SIGABRT hazard on free-form
        # dict/None payloads).
        self._register_namespaces(bus)

        # Reuse the shipped orchestration VERBATIM, repointed at the tenant bus
        # and config-derived backends/cap. PrintServices.__init__ reads env for
        # DEFAULTS and builds throwaway backends (no I/O at construction); we
        # replace the four config-driven attributes before start_bus().
        svc = PrintServices()
        svc.bus = _TenantBusClient(bus, self._source)
        svc.page_cap = PageCap(self.config.page_cap_per_period)
        svc.heartbeat_s = self.config.heartbeat_seconds
        svc.backends = self._backends
        self._service = svc

        # Wires the per-backend `print_services/<id>/submit` subscriptions and
        # publishes the per-id registry + status — all core code, unchanged.
        svc.start_bus()

        # ADDITIVE seams the MCP surface binds to.
        bus.subscribe(_AGG_SUBMIT_NS, self._on_aggregate_submit)
        bus.on_message(self._job_query_responder)
        self._publish_aggregate_registry()

        # The domain age-contract heartbeat (registry + status refresh). Its own
        # daemon thread, stopped on stop(); ComposeServer's announce tick is a
        # SEPARATE concern (the plugin surface), not this.
        self._hb_stop = threading.Event()
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="print_services_plugin_hb",
            daemon=True)
        self._hb_thread.start()

    def stop(self) -> None:
        if self._hb_stop is not None:
            self._hb_stop.set()
        if self._bus is not None:
            try:
                self._bus.off_message(self._job_query_responder)
            except Exception:  # noqa: BLE001
                pass
        if self._service is not None:
            try:
                # Tombstones the per-id registry + marks status offline + a
                # (no-op) disconnect — the shipped graceful shutdown, reused.
                self._service.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("print_services core shutdown raised")
            # Also tombstone the AGGREGATE registry the core does not know about.
            try:
                if self._bus is not None:
                    self._bus.publish_state(_AGG_REGISTRY_NS, None,
                                            source=self._source)
            except Exception:  # noqa: BLE001
                pass
        self._service = None
        self._backends = None
        super().stop()

    # -- the live (authoritative) surface ---------------------------------- #
    def current_publishes(self) -> list[str]:
        out = [_AGG_REGISTRY_NS]
        for bid in self._backend_ids():
            out += [f"print_services/registry/{bid}",
                    f"print_services/{bid}/status",
                    f"print_services/{bid}/result"]
        return out

    def current_subscribes(self) -> list[str]:
        out = [_AGG_SUBMIT_NS, _JOBS_QUERY_NS]
        for bid in self._backend_ids():
            out.append(f"print_services/{bid}/submit")
        return out

    # -- ADDITIVE wiring --------------------------------------------------- #
    def _register_namespaces(self, bus) -> None:
        names = [_AGG_SUBMIT_NS, _AGG_REGISTRY_NS, _JOBS_QUERY_NS]
        for bid in self._backend_ids():
            names += [f"print_services/{bid}/submit",
                      f"print_services/{bid}/status",
                      f"print_services/{bid}/result",
                      f"print_services/registry/{bid}"]
        for ns in names:
            bus.register_namespace(ns, "json", scope=Scope.GLOBAL,
                                   label=self._source)

    def _on_aggregate_submit(self, _name: str, value, _old=None) -> None:
        """Route one aggregate submit (`print_services/submit`, carrying
        `backend_name`) to the shipped per-backend handler. An unknown backend
        is a LOUD error result, never a silent drop."""
        svc = self._service
        if svc is None or not isinstance(value, dict):
            return
        backend_name = value.get("backend_name")
        if backend_name not in svc.backends:
            svc._publish_result(backend_name or "unknown", {
                "job_id": value.get("job_id"),
                "status": "error",
                "detail": (f"unknown backend {backend_name!r}; available: "
                           f"{sorted(svc.backends)}"),
            })
            return
        # Reuse the core submit sequence VERBATIM (submitted -> backend.submit
        # under the page cap -> result -> record -> status refresh).
        svc._handle_submit(backend_name, value)

    def _publish_aggregate_registry(self) -> None:
        svc = self._service
        if svc is None or self._bus is None:
            return
        agg: dict = {}
        for bid, backend in svc.backends.items():
            row = dict(backend.descriptor)
            row["heartbeat_ts"] = time.time()
            agg[bid] = row
        self._bus.publish_state(_AGG_REGISTRY_NS, agg, source=self._source)

    def _lookup_job(self, job_id) -> dict:
        svc = self._service
        if not job_id:
            return {"found": False, "detail": "no job_id given"}
        if svc is not None:
            with svc._history_lock:
                for entry in reversed(svc._history):
                    if entry.get("job_id") == job_id:
                        return {"found": True, "job": dict(entry)}
        return {"found": False, "job_id": job_id,
                "detail": "no such job in recent history"}

    def _job_query_responder(self, msg) -> None:
        """Answer a CXC-BUS-QUERY on `print_services/jobs/query`
        ([CXC-BUS-QUERY-IDIOM]): echo `request_id`, answer on
        `.../result/{src}` read from the envelope source."""
        if getattr(msg, "type", None) != MessageType.EVENT:
            return
        if getattr(msg, "namespace", None) != _JOBS_QUERY_NS:
            return
        data = msg.data or {}
        src = msg.source
        rid = data.get("request_id")
        answer = self._lookup_job(data.get("job_id"))
        if self._bus is not None:
            self._bus.publish_event(
                f"print_services/jobs/result/{src}", "result",
                data={"request_id": rid, **answer}, source=self._source)

    # -- heartbeat --------------------------------------------------------- #
    def _heartbeat_loop(self) -> None:
        stop = self._hb_stop
        svc = self._service
        assert stop is not None and svc is not None
        while not stop.wait(svc.heartbeat_s):
            try:
                svc._publish_all_registry()
                self._publish_aggregate_registry()
                svc._publish_all_status()
            except Exception:  # noqa: BLE001 — a heartbeat tick must not die
                logger.exception("print_services plugin heartbeat tick raised")


# --------------------------------------------------------------------------- #
# An example tenant-manifest entry the config-manifest loader consumes
# (`composeserver_loader.load_tenant_manifest`). The manifest FILE format is
# deferred ([DD-CSL-MANIFEST-FORMAT]); this is the already-parsed dict shape.
# --------------------------------------------------------------------------- #
EXAMPLE_MANIFEST_ENTRY = {
    "plugin": "print_services.plugin:PrintServicesPlugin",
    "config": {
        "hp_ipp_ip": "192.0.2.77",
        "elegoo_sdcp_ip": "192.0.2.34",
        "page_cap_per_period": 100,
        "heartbeat_seconds": 30,
    },
}
