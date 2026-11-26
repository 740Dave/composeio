"""fingerprint_manager — the R503 enroll/cast/match/roster-push ORCHESTRATION
bus-PEER wearing the ComposePlugin contract ([SPEC:CXC-COMPOSESERVER]
[CXC-CS-PLUGIN-CONTRACT], [SPEC:CXC-MCP] [CXC-MCP-TOOL-DECLARATION]).

⛔ NAMED FOR WHAT IT IS, NOT WHERE IT WAS ([DEC-FINGERPRINT-MANAGER-RENAME-
2026-08-26], David's ruling). This wraps the ORCHESTRATION service in
``services/fingerprint_service/`` — the R503 enroll/ingest bus peer that FOLLOWS
device fingerprint events (e.g. ``dev/s3-fp1``), INGESTS ``fingerprint_ready``
heartbeats, orchestrates enrollment, originates the roster push + enroll
command, and reconciles each device's held set by SUBSCRIPTION (never a query).
It is NOT storage: the biometric-blob STORE is the SEPARATE
``fingerprint_template_store`` leg. The earlier misnomer "fingerprint_storage"
was corrected at birth here so no future intelligence is misled. The underlying
service dir keeps its name (``fingerprint_service`` is fine; only the "storage"
misnomer was wrong). This plugin adds no fingerprint behavior; it re-homes the
existing behavior onto the plugin contract.

THE REPOINT ([DP-CIO-6] transport-is-an-implementation-detail). As a *service*
it connected ITSELF to the remote MQTT broker (:1883,
``fingerprint_service.config.broker_url``). As a *plugin* it holds NO transport
and opens NO socket: ComposeServer INJECTS the tenant bus in :meth:`start` and
the plugin publishes/subscribes on THAT bus directly ([CXC-CS-MICROKERNEL]
supervisor-not-mediator). The plugin never learns whether it is one of N
per-tenant instances — that is pure kernel orchestration.

TEMPLATE_STORE INTEGRATION — PRESERVED AS-IS, FLAGGED. The wrapped service
reaches the dedicated template store over the ``composeio.resolve.put_bytes`` /
``resolve_bytes`` HTTP seams (``fingerprint://<id>`` -> the store's PUT/GET).
That integration is preserved UNCHANGED. The plugin default leaves the real
resolve seams in place; a ``template_store_url`` config value binds a
plugin-local, URL-scoped realization of the SAME ``fingerprint://`` HTTP call
(so the endpoint is config-driven without mutating a process global or editing
the shared :mod:`composeio.resolve`).

    ⚠ FLAG (design finding, NOT changed here): in ComposeServer
    ``fingerprint_manager`` AND ``fingerprint_template_store`` are two plugins on
    the SAME tenant bus. This cross-plugin call SHOULD become a bus-native
    [SPEC:CXC-BUS-QUERY] round-trip to the template_store plugin rather than an
    off-bus HTTP hop — and once it is, this plugin should also carry a
    ``requires=RequiresFloor(capabilities=["template_store"])`` floor so it PARKS
    LOUDLY until the store is present. That is a design change (a new bus contract
    between the two plugins), so it is FLAGGED, not made in this mechanical wrap.
"""
from __future__ import annotations

import logging
import urllib.request
from collections import deque
from typing import Any, Callable, Deque

from pydantic import BaseModel, Field

from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    ToolDirection,
)
from composeio.transport import BusMessage, MessageType

# Reuse the orchestration service BY REFERENCE ([DX-NARROW-WAIST] define-once):
# the enroll/cast/push logic stays in services/fingerprint_service/, this plugin
# is a thin host adapter. services/ is on PYTHONPATH (the manifest runs with
# PYTHONPATH=python:services), so these absolute imports resolve.
from fingerprint_service import topics
from fingerprint_service.service import FingerprintService

logger = logging.getLogger("composeio.plugins.fingerprint_manager")


# --------------------------------------------------------------------------- #
# The bus-query namespaces this plugin answers on ([SPEC:CXC-BUS-QUERY]
# [CXC-BUS-QUERY-IDIOM]). A `query`-mode MCP read publishes an EVENT to the
# `.../query` namespace and the responder answers on `.../result/{src}`; the
# MCP router strips the trailing `/query` to derive the resource root
# ([CXC-MCP-INVOKE-READ]). These MUST therefore end in `/query`.
# --------------------------------------------------------------------------- #
QUERY_ENROLLMENT_STATUS = "fingerprint_manager/enrollment_status/query"
QUERY_RECENT_READY = "fingerprint_manager/recent_fingerprint_ready/query"

# The two query-RESULT namespace roots this plugin ALWAYS publishes on (the
# CXC-BUS-QUERY responder answers each read on `{root}/result/{src}`, the src
# leg being the dynamic per-requester address). These are the static half of
# the published surface — the counterpart of QUERY_* on the subscribe side —
# so they live in the class-level `publishes` and thus honestly populate the
# DERIVED registry entry ([CXC-CS-PLUGIN-CONTRACT]).
RESULT_ENROLLMENT_STATUS = "fingerprint_manager/enrollment_status/result"
RESULT_RECENT_READY = "fingerprint_manager/recent_fingerprint_ready/result"


# --------------------------------------------------------------------------- #
# MCP tool argument models — the JSON-schema the Intelligence sees is DERIVED
# from these ([CXC-MCP-TOOL-DECLARATION] input-schema-is-the-single-derivation-
# source). Field descriptions are written FOR AN INTELLIGENCE
# ([DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]): unambiguous, verbose.
# --------------------------------------------------------------------------- #
class GetFingerprintEnrollmentStatusForDeviceArgs(BaseModel):
    device_id: str = Field(
        description=(
            "The bus-addressed device identifier whose fingerprint enrollment "
            "state you want — the SAME opaque id the device publishes under "
            "(e.g. 'dev/s3-fp1'); it may contain a '/'. This is the device's bus "
            "namespace prefix, never a physical sensor slot index."
        )
    )


class ListRecentFingerprintReadyEventsArgs(BaseModel):
    limit: int = Field(
        default=20,
        ge=1,
        le=1000,
        description=(
            "Maximum number of the most-recent 'fingerprint_ready' heartbeat "
            "events to return, newest first. The plugin retains a bounded "
            "in-memory ring of these events observed across every followed "
            "device; ask for fewer to page."
        ),
    )


# --------------------------------------------------------------------------- #
# A plugin-local, URL-scoped realization of the `fingerprint://` HTTP seam. Used
# ONLY when the config pins a `template_store_url`; the DEFAULT path leaves the
# real `composeio.resolve.put_bytes` / `resolve_bytes` seams untouched. This is
# the SAME two-line urllib call `composeio.resolve` makes, scoped to a base URL,
# so the endpoint is config-driven without mutating a process global or editing
# the shared resolve module.
# --------------------------------------------------------------------------- #
class _UrlScopedTemplateStore:
    def __init__(self, base_url: str, *, timeout: int = 30):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _opaque(self, uri: str) -> str:
        if not uri.startswith("fingerprint://"):
            raise ValueError(f"not a fingerprint:// template uri: {uri!r}")
        return uri[len("fingerprint://"):]

    def put(self, uri: str, data: bytes) -> str:
        opaque = self._opaque(uri)
        req = urllib.request.Request(
            f"{self._base}/api/v1/templates/{opaque}", data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=self._timeout):
            return uri

    def resolve(self, uri: str) -> bytes:
        opaque = self._opaque(uri)
        with urllib.request.urlopen(
                f"{self._base}/api/v1/templates/{opaque}", timeout=self._timeout) as r:
            return r.read()


class FingerprintManagerPlugin(ComposePlugin):
    """The fingerprint enroll/cast/match/roster-push orchestration bus-peer as a
    ComposeServer plugin.

    ONE typed :class:`Config` declaration derives the config form, the announce
    payload, the registry entry, and — via :attr:`mcp_tools` — the MCP surface.
    The plugin hand-writes none of the four.
    """

    class Config(ComposePlugin.Config):
        #: device bus namespaces to FOLLOW at start (each is passed to
        #: FingerprintService.follow_device). An id may legitimately contain a
        #: '/' (the firmware convention 'dev/<name>', e.g. 'dev/s3-fp1').
        devices: list[str] = []
        #: the bus identity this plugin publishes under.
        source: str = "fingerprint_manager"
        #: override the dedicated fingerprint_template_store endpoint. None (the
        #: default) uses composeio.resolve's real HTTP seams UNCHANGED
        #: (env COMPOSEIO_TEMPLATE_STORE_URL). A value binds the plugin-local
        #: URL-scoped realization of the same fingerprint:// call.
        template_store_url: str | None = None
        #: capacity of the bounded ring of recent fingerprint_ready events the
        #: list_recent_fingerprint_ready_events tool reads from.
        recent_ready_capacity: int = 100

    # ---- identity + declared surface ------------------------------------ #
    name = "fingerprint_manager"
    version = "1.0.0"
    # NOT default-loaded: enrolling biometric templates is an opt-in capability a
    # deployment installs deliberately, never a silent standard-build default.
    default_loaded = False
    # The STATIC surface. The per-device event topics are dynamic (they depend on
    # the followed set) and are added by current_subscribes() at runtime
    # ([CXC-CS-DYNAMIC-SURFACE]).
    subscribes = [QUERY_ENROLLMENT_STATUS, QUERY_RECENT_READY]
    # The STATIC published surface: the two query-result roots the responder
    # always answers on. The per-device service->device topics (the roster push
    # and enroll commands) are dynamic (they depend on the followed set) and are
    # added by current_publishes() at runtime ([CXC-CS-DYNAMIC-SURFACE]) — the
    # exact mirror of subscribes/current_subscribes() above.
    publishes = [RESULT_ENROLLMENT_STATUS, RESULT_RECENT_READY]

    # ---- ARTIFACT 4/4: the MCP tool surface ----------------------------- #
    # Both tools are `query`-mode READS: this plugin is a CXC-BUS-QUERY RESPONDER
    # for its own in-process state ([CXC-MCP-INVOKE-READ] query). Names are
    # verbose and agent-unambiguous per [DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-
    # 2026-08-25]; the router exposes them as
    # `fingerprint_manager.<name>`.
    mcp_tools = [
        McpToolDecl(
            name="get_fingerprint_enrollment_status_for_device",
            description=(
                "Return the fingerprint enrollment/reconciliation status the "
                "fingerprint_manager service currently holds for ONE device: "
                "whether the service is following it, whether its held template "
                "set is unconverged and awaiting a re-push, and the superset "
                "counts of enrolled persons and stored templates. Read-only; "
                "answered from the service's live in-process state over a "
                "CXC-BUS-QUERY round-trip, never by touching the sensor."
            ),
            input_model=GetFingerprintEnrollmentStatusForDeviceArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(subscribes=[QUERY_ENROLLMENT_STATUS]),
            idempotent=True,
        ),
        McpToolDecl(
            name="list_recent_fingerprint_ready_events",
            description=(
                "List the most-recent 'fingerprint_ready' heartbeat events the "
                "fingerprint_manager service has ingested across every followed "
                "device, newest first. Each entry names the device that emitted "
                "it and carries the event payload the device published. Read-"
                "only; answered from a bounded in-memory ring over a CXC-BUS-"
                "QUERY round-trip."
            ),
            input_model=ListRecentFingerprintReadyEventsArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(subscribes=[QUERY_RECENT_READY]),
            idempotent=True,
        ),
    ]

    def __init__(self, config: "BaseModel | dict | None" = None,
                 *,
                 template_store_put: "Callable[[str, bytes], str] | None" = None,
                 template_resolver: "Callable[[str], bytes] | None" = None):
        super().__init__(config)
        # Test/override injection seams — mirror FingerprintService's own seams
        # so the reference/integration suites never need a live HTTP store. When
        # unset, config.template_store_url decides; when THAT is unset too, the
        # service keeps its real composeio.resolve defaults (integration as-is).
        self._injected_put = template_store_put
        self._injected_resolver = template_resolver
        self._service: FingerprintService | None = None
        self._followed: set[str] = set()
        self._recent_ready: Deque[dict] = deque(maxlen=self.config.recent_ready_capacity)

    # ---- lifecycle ------------------------------------------------------ #
    def start(self, bus) -> None:
        self._bus = bus
        put, resolver = self._resolve_template_seams()
        svc_kwargs: dict[str, Any] = {"source": self.config.source}
        if put is not None:
            svc_kwargs["template_store_put"] = put
        if resolver is not None:
            svc_kwargs["template_resolver"] = resolver
        self._service = FingerprintService(**svc_kwargs)
        self._service.attach(bus)

        # Follow the configured devices: the wrapped service subscribes each
        # device's event topics; the plugin ALSO records fingerprint_ready for
        # the recent-events tool (additive observability, not new fp behavior).
        for device_id in self.config.devices:
            self._follow(device_id)

        # Become the CXC-BUS-QUERY responder for the two read tools. on_message
        # carries the full envelope (we need msg.source to address the result);
        # the extra subscribe_events forces the transport subscription so a real
        # broker delivers the query topic to this bus too.
        bus.on_message(self._on_bus_message)
        bus.subscribe_events(QUERY_ENROLLMENT_STATUS, self._noop_event)
        bus.subscribe_events(QUERY_RECENT_READY, self._noop_event)

    def stop(self) -> None:
        self._service = None
        self._followed.clear()
        super().stop()

    def _resolve_template_seams(self):
        """(put, resolver) or (None, None). Injected seams win (tests); then a
        config template_store_url binds the URL-scoped realization; otherwise
        None leaves FingerprintService's real composeio.resolve defaults."""
        if self._injected_put is not None or self._injected_resolver is not None:
            return self._injected_put, self._injected_resolver
        if self.config.template_store_url:
            client = _UrlScopedTemplateStore(self.config.template_store_url)
            return client.put, client.resolve
        return None, None

    def _follow(self, device_id: str) -> None:
        assert self._service is not None
        self._service.follow_device(device_id)
        self._followed.add(device_id)
        # additive recorder for list_recent_fingerprint_ready_events.
        self._bus.subscribe_events(
            topics.fingerprint_ready(device_id),
            lambda ns, et, data, d=device_id: self._record_ready(d, data),
        )

    # ---- the dynamic surface -------------------------------------------- #
    def current_publishes(self) -> list[str]:
        """The live published surface: the two query-result roots plus, for every
        followed device, the service-originated topics this plugin actually
        publishes ([CXC-CS-DYNAMIC-SURFACE]).

        The plugin is the SOLE AUTHORITY over its own published surface (the spec
        names published AND subscribed as dynamic), so this MUST exist alongside
        current_subscribes() — without it the derived announce/registry report
        `publishes: []` while the plugin originates the roster push and enroll
        commands, i.e. self-describes dishonestly. Per device it names
        `<dev>/template_set` (the DEC-HOSTED-PLUGIN-MUST-REPUBLISH roster push),
        `<dev>/cmd/enroll` and `<dev>/cmd/enroll/cancel`, reusing the same
        `fingerprint_service.topics` builders the service publishes through
        ([DX-NARROW-WAIST] define-once) — exactly as current_subscribes() reuses
        them for the event topics."""
        surface = list(self.publishes)
        for device_id in sorted(self._followed):
            surface.extend([
                topics.template_set(device_id),
                topics.enroll_cmd(device_id),
                topics.enroll_cancel_cmd(device_id),
            ])
        return surface

    def current_subscribes(self) -> list[str]:
        """The live subscribed surface: the query namespaces plus every followed
        device's event topics ([CXC-CS-DYNAMIC-SURFACE])."""
        surface = list(self.subscribes)
        for device_id in sorted(self._followed):
            surface.extend([
                topics.device_announce(device_id),
                topics.fingerprint_ready(device_id),
                topics.fingerprint_push_needed(device_id),
                topics.fingerprint_inoperable(device_id),
                topics.template_set_loaded(device_id),
                topics.template_set_failed(device_id),
                topics.enroll_complete(device_id),
                topics.auth_event(device_id),
            ])
        return surface

    # ---- recent-ready ring ---------------------------------------------- #
    def _record_ready(self, device_id: str, data: dict | None) -> None:
        self._recent_ready.appendleft({"device_id": device_id, "event": data or {}})

    # ---- the CXC-BUS-QUERY responder ------------------------------------ #
    def _noop_event(self, namespace: str, event_type: str, data: dict) -> None:
        # The real answer is produced by _on_bus_message (which sees msg.source);
        # this exists only so subscribe_events registers the transport route.
        return

    def _on_bus_message(self, msg: BusMessage) -> None:
        if msg.type is not MessageType.EVENT:
            return
        if msg.namespace == QUERY_ENROLLMENT_STATUS:
            self._answer(msg, "fingerprint_manager/enrollment_status",
                         self._enrollment_status(msg.data or {}))
        elif msg.namespace == QUERY_RECENT_READY:
            self._answer(msg, "fingerprint_manager/recent_fingerprint_ready",
                         self._recent_ready_payload(msg.data or {}))

    def _answer(self, msg: BusMessage, resource_root: str, payload: dict) -> None:
        """Answer a query on `{resource_root}/result/{src}`, echoing request_id
        VERBATIM ([CXC-BUS-QUERY-IDIOM])."""
        src = msg.source
        request_id = (msg.data or {}).get("request_id")
        self._bus.publish_event(
            f"{resource_root}/result/{src}", "result",
            data={"request_id": request_id, **payload}, source=self.config.source)

    def _enrollment_status(self, args: dict) -> dict:
        device_id = args.get("device_id")
        svc = self._service
        if svc is None:
            return {"ok": False, "error": "plugin not started"}
        pending = set(svc.reconcile_pending())
        return {
            "ok": True,
            "device_id": device_id,
            "followed": device_id in self._followed,
            "reconcile_pending": device_id in pending,
            "enrolled_persons": len(svc.store.persons),
            "stored_templates": len(svc.store.templates),
        }

    def _recent_ready_payload(self, args: dict) -> dict:
        limit = args.get("limit", 20)
        try:
            limit = max(1, min(int(limit), 1000))
        except (TypeError, ValueError):
            limit = 20
        events = list(self._recent_ready)[:limit]
        return {"ok": True, "count": len(events), "events": events}
