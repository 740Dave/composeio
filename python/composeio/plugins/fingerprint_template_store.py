"""fingerprint_template_store — the DEDICATED biometric template blob store,
wearing the ComposeServer plugin contract ([SPEC:CXC-COMPOSESERVER]
[CXC-CS-PLUGIN-CONTRACT]).

WHAT THIS IS (the Wave-2b repoint). The extant standalone service in
``services/fingerprint_template_store/`` is a purpose-built HTTP blob store for
R503 fingerprint templates: opaque ids only, no path hierarchy, its OWN port,
its OWN data directory (never bus_storage's), per David's ruling that biometric
templates are stored SEPARATELY from bus_storage. This module wraps that exact
behavior as a self-contained ``ComposePlugin`` so it stands up under
ComposeServer against a tenant bus and exposes an MCP surface an Intelligence
can drive — WITHOUT losing the standalone HTTP surface the ``fingerprint://``
resolver ([composeio.resolve]) already backs onto.

TWO SURFACES, ONE STORE CORE. The plugin owns exactly one :class:`TemplateStore`
core (the store logic — id validation, path containment, atomic write, per-id
locks, all preserved VERBATIM from the extant ``server.py``). It presents that
core two ways:

  * an HTTP server (the extant REST API, byte-for-byte the same routes) so
    EXISTING CALLERS keep resolving ``fingerprint://<id>`` unchanged
    ([composeio.resolve.resolve_bytes] / ``put_bytes``). This is the
    behavior-preservation half of the ruling: "keep existing behavior for
    existing callers."
  * a bus/MCP surface (this file's net-new deliverable) — three MCP tools an
    Intelligence invokes over the ComposeServer umbrella router
    ([SPEC:CXC-MCP] [CXC-MCP-TOOL-DECLARATION]), resolved through the
    [SPEC:CXC-BUS-QUERY] request/response idiom against the SAME core.

THE MCP SURFACE IS NAMED FOR AN INTELLIGENCE (David's ruling,
[DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]): every tool name, param
name and description is verbose and self-describing — an agent reads the
purpose off the name, never a terse mnemonic. The tools are
``list_enrolled_fingerprint_template_ids`` / ``get_fingerprint_template_by_id``
/ ``delete_fingerprint_template_by_id``, exposed by the router as
``fingerprint_template_store.<tool>``.

PATH-CONTAINMENT DISCIPLINE PRESERVED. An id must match ``^[A-Za-z0-9_-]+$`` —
no ``/``, no ``.``, no ``..`` ever reaches a filesystem path
([DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE]); a malformed id is a rejected
request, not a traversal to contain.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, Field

from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    ToolDirection,
)

logger = logging.getLogger("composeio.plugins.fingerprint_template_store")

# An opaque id is a bare token: no '/', no '.', no leading dot. Preserved
# VERBATIM from the extant service ([DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE]).
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# The plugin's bus namespace root. Each MCP tool round-trips through
# `<ROOT>/templates/<op>/query` -> `<ROOT>/templates/<op>/result/{src}`, the
# [SPEC:CXC-BUS-QUERY] request/response idiom ([CXC-BUS-QUERY-IDIOM]).
NS_ROOT = "fingerprint_template_store"


class BadTemplateId(Exception):
    """A supplied id is not a bare opaque token. A malformed request, never a
    path-escape attempt (there is no path) — preserved from the extant service."""

    def __init__(self, template_id):
        super().__init__(f"malformed fingerprint template id: {template_id!r}")
        self.template_id = template_id


# --------------------------------------------------------------------------- #
# TemplateStore — the ONE store core. Both surfaces (HTTP + bus/MCP) call THIS;
# there is no second copy of the storage discipline to drift.
# --------------------------------------------------------------------------- #
class TemplateStore:
    """Flat opaque-id -> bytes map on disk, with the extant service's exact
    discipline: id validation, path containment, atomic write, per-id locks.

    DELIBERATELY minimal — no path hierarchy, no content-type sniffing, no asset
    listing — so the store stays legibly "biometric templates, and only that."
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self._start_time = time.time()
        self._id_locks: dict[str, threading.Lock] = {}
        self._id_locks_guard = threading.Lock()

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_id_lock(self, template_id: str) -> threading.Lock:
        with self._id_locks_guard:
            lock = self._id_locks.get(template_id)
            if lock is None:
                lock = threading.Lock()
                self._id_locks[template_id] = lock
            return lock

    def _template_path(self, template_id: str) -> Path:
        """The ONE place a supplied id becomes a filesystem path. Refuses
        anything not matching ``_ID_RE`` — '/' and '..' can never reach a path
        at all, so there is no escape to contain."""
        if not _ID_RE.match(template_id or ""):
            raise BadTemplateId(template_id)
        return self.data_dir / f"{template_id}.tmpl"

    def _atomic_write_bytes(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".{target.name}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # --- the operations both surfaces share -------------------------------- #
    def get(self, template_id: str) -> Optional[bytes]:
        """Bytes for *template_id*, or ``None`` if absent. Raises
        :class:`BadTemplateId` for a malformed id."""
        path = self._template_path(template_id)
        if not path.exists() or not path.is_file():
            return None
        return path.read_bytes()

    def put(self, template_id: str, data: bytes) -> int:
        """Store *data* under *template_id* atomically; returns the byte count.
        Raises :class:`BadTemplateId` for a malformed id."""
        path = self._template_path(template_id)
        with self._get_id_lock(template_id):
            self._atomic_write_bytes(path, data)
        logger.info("template stored: %s (%d bytes)", template_id, len(data))
        return len(data)

    def delete(self, template_id: str) -> bool:
        """Remove *template_id*; returns whether a file existed to remove.
        Raises :class:`BadTemplateId` for a malformed id."""
        path = self._template_path(template_id)
        with self._get_id_lock(template_id):
            if path.exists():
                path.unlink()
                return True
        return False

    def list_ids(self) -> list[dict]:
        """Every enrolled template id with its size, sorted by id. The listing
        the extant service deliberately does NOT expose over HTTP is exactly
        what an auditing Intelligence needs, so it is a bus/MCP-only capability."""
        if not self.data_dir.exists():
            return []
        rows = []
        for f in sorted(self.data_dir.glob("*.tmpl")):
            rows.append({
                "fingerprint_template_id": f.name[: -len(".tmpl")],
                "size_bytes": f.stat().st_size,
            })
        return rows

    def status(self) -> dict:
        import shutil
        templates = list(self.data_dir.glob("*.tmpl")) if self.data_dir.exists() else []
        total_bytes = sum(f.stat().st_size for f in templates)
        disk = shutil.disk_usage(self.data_dir) if self.data_dir.exists() else None
        return {
            "service": "composeio-fingerprint-template-store",
            "version": FingerprintTemplateStorePlugin.version,
            "uptime_seconds": int(time.time() - self._start_time),
            "templates": len(templates),
            "storage_bytes": total_bytes,
            "disk_free_bytes": disk.free if disk else None,
            "data_dir": str(self.data_dir),
        }


# --------------------------------------------------------------------------- #
# The HTTP surface — the extant REST API, preserved so existing `fingerprint://`
# callers keep resolving unchanged. A thin adapter over TemplateStore.
# --------------------------------------------------------------------------- #
def _make_http_handler(store: TemplateStore):
    class TemplateStoreHandler(BaseHTTPRequestHandler):
        _responded = False

        def log_message(self, fmt, *args):  # route access logs to our logger
            logger.info(fmt % args)

        def _send_json(self, data, status=200):
            body = json.dumps(data, indent=2).encode()
            self._responded = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_binary(self, data: bytes, status=200):
            self._responded = True
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, status=200):
            body = text.encode()
            self._responded = True
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status: int, message: str):
            self._send_json({"error": message}, status=status)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(length)

        def _route(self, method: str):
            # The request ERROR BOUNDARY, preserved from the extant service: no
            # request terminates without an HTTP status line.
            self._responded = False
            try:
                self._dispatch(method)
            except BadTemplateId as exc:
                if not self._responded:
                    self._send_error(400, str(exc))
            except Exception:
                logger.exception("unhandled error serving %s %s", method, self.path)
                if not self._responded:
                    self._send_error(500, "Internal server error")

        def _dispatch(self, method: str):
            path = unquote(urlsplit(self.path).path)
            if path.startswith("/api/v1/templates/"):
                template_id = path[len("/api/v1/templates/"):]
                if method == "GET":
                    data = store.get(template_id)
                    if data is None:
                        return self._send_error(404, f"No template for id '{template_id}'")
                    return self._send_binary(data)
                if method == "PUT":
                    size = store.put(template_id, self._read_body())
                    return self._send_json(
                        {"status": "ok", "id": template_id, "size": size}, status=201)
                if method == "DELETE":
                    store.delete(template_id)
                    return self._send_json({"status": "deleted", "id": template_id})
            elif path == "/api/v1/status" and method == "GET":
                return self._send_json(store.status())
            elif path == "/health":
                return self._send_text("ok")
            self._send_error(404, f"Not found: {method} {path}")

        def do_GET(self):
            self._route("GET")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

    return TemplateStoreHandler


class _ThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128
    allow_reuse_address = True


# --------------------------------------------------------------------------- #
# MCP tool argument models — VERBOSE, agent-unambiguous field names + docs
# ([DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]).
# --------------------------------------------------------------------------- #
class ListEnrolledFingerprintTemplateIdsArgs(BaseModel):
    """No arguments — this lists EVERY currently-enrolled template id."""


class GetFingerprintTemplateByIdArgs(BaseModel):
    fingerprint_template_id: str = Field(
        ...,
        description=(
            "The opaque service-private id of the fingerprint template to "
            "retrieve, exactly as returned by "
            "list_enrolled_fingerprint_template_ids. Must match ^[A-Za-z0-9_-]+$ "
            "(no slashes or dots — it is an id, never a path)."
        ),
    )


class DeleteFingerprintTemplateByIdArgs(BaseModel):
    fingerprint_template_id: str = Field(
        ...,
        description=(
            "The opaque service-private id of the fingerprint template to "
            "permanently delete (retention sweep or re-enrolment). Must match "
            "^[A-Za-z0-9_-]+$. Deletion is idempotent: deleting an absent id "
            "succeeds and reports existed=false."
        ),
    )


# --------------------------------------------------------------------------- #
# The plugin.
# --------------------------------------------------------------------------- #
class FingerprintTemplateStorePlugin(ComposePlugin):
    """The dedicated biometric template store as a ComposeServer plugin.

    Runs the extant HTTP surface (existing-caller compatibility) AND a
    bus/MCP surface (the Intelligence-facing repoint), both over one
    :class:`TemplateStore` core. Not default-loaded: a deployment opts it in
    per tenant via the loader manifest
    (``composeio.plugins.fingerprint_template_store:FingerprintTemplateStorePlugin``).
    """

    class Config(ComposePlugin.Config):
        """Store path + HTTP listen surface. Defaults mirror the extant
        service's own defaults (``FINGERPRINT_TEMPLATE_STORE_*`` env)."""

        data_dir: str = Field(
            default="/var/lib/composeio/fingerprint_templates",
            description="Directory holding template files (SEPARATE from "
                        "bus_storage's assets/). One file per opaque id.",
        )
        http_host: str = Field(
            default="0.0.0.0",
            description="HTTP bind host for the existing-caller REST surface. "
                        "Matches the extant service's default.",
        )
        http_port: int = Field(
            default=8422,
            description="HTTP listen port for the existing-caller REST surface "
                        "(the extant service's default; deployments that front "
                        "it on :8423 set it here).",
        )
        http_enabled: bool = Field(
            default=True,
            description="Whether to run the HTTP surface. Set false to expose "
                        "ONLY the bus/MCP surface (e.g. in tests, or when a "
                        "sibling process already serves fingerprint:// over HTTP).",
        )

    name = "fingerprint_template_store"
    version = "1.0.0"
    default_loaded = False
    # The bus surface: three request namespaces (one per MCP tool). Result
    # namespaces are per-requester (`.../result/{src}`) and created on demand.
    publishes: list[str] = [
        f"{NS_ROOT}/templates/list/result",
        f"{NS_ROOT}/templates/get/result",
        f"{NS_ROOT}/templates/delete/result",
    ]
    subscribes: list[str] = [
        f"{NS_ROOT}/templates/list/query",
        f"{NS_ROOT}/templates/get/query",
        f"{NS_ROOT}/templates/delete/query",
    ]

    mcp_tools = [
        McpToolDecl(
            name="list_enrolled_fingerprint_template_ids",
            description=(
                "List the opaque ids of every fingerprint template currently "
                "enrolled in the dedicated biometric template store, each with "
                "its size in bytes. Takes no arguments. Use this to audit which "
                "templates exist before getting or deleting one."
            ),
            input_model=ListEnrolledFingerprintTemplateIdsArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(
                subscribes=[f"{NS_ROOT}/templates/list/query"]),
            idempotent=True,
        ),
        McpToolDecl(
            name="get_fingerprint_template_by_id",
            description=(
                "Retrieve one fingerprint template's raw bytes by its opaque "
                "id. The bytes are returned base64-encoded in "
                "'template_bytes_base64' (a JSON bus cannot carry raw bytes), "
                "with 'size_bytes' and a 'found' flag; a missing id returns "
                "found=false and no bytes, never a fabricated default."
            ),
            input_model=GetFingerprintTemplateByIdArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(
                subscribes=[f"{NS_ROOT}/templates/get/query"]),
            idempotent=True,
        ),
        McpToolDecl(
            name="delete_fingerprint_template_by_id",
            description=(
                "Permanently delete one fingerprint template by its opaque id "
                "(retention sweep or re-enrolment) and return a confirmation "
                "reporting whether a template existed ('existed') and was "
                "removed ('deleted'). Idempotent: deleting an absent id "
                "succeeds with existed=false. This is a MUTATION resolved over "
                "the request/response query idiom so the caller gets a "
                "confirmed result, not a fire-and-forget publish."
            ),
            input_model=DeleteFingerprintTemplateByIdArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(
                subscribes=[f"{NS_ROOT}/templates/delete/query"]),
            idempotent=True,
        ),
    ]

    def __init__(self, config=None):
        super().__init__(config)
        self._store: Optional[TemplateStore] = None
        self._http: Optional[_ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None

    @property
    def store(self) -> Optional[TemplateStore]:
        return self._store

    @property
    def http_address(self) -> Optional[tuple]:
        """The actual (host, port) the HTTP server bound, or None. With
        http_port=0 the OS assigns a port — read it back here."""
        return self._http.server_address if self._http else None

    # -- lifecycle ---------------------------------------------------------- #
    def start(self, bus) -> None:
        self._bus = bus
        self._store = TemplateStore(self.config.data_dir)
        self._store.ensure_dirs()

        # Register the three request namespaces as event channels so the bus
        # (and any external transport bridging this tenant) knows them.
        from composeio.namespace import Scope
        for op in ("list", "get", "delete"):
            bus.register_namespace(
                f"{NS_ROOT}/templates/{op}/query", "json",
                scope=Scope.LOCAL, mode="event")

        # The bus/MCP responder: one on_message handler routes every
        # `.../query` event to its op and answers on `.../result/{src}`,
        # echoing request_id ([SPEC:CXC-BUS-QUERY] [CXC-BUS-QUERY-IDIOM]).
        bus.on_message(self._on_bus_message)

        if self.config.http_enabled:
            self._start_http()
        logger.info(
            "fingerprint_template_store plugin started (data_dir=%s, http=%s)",
            self.config.data_dir,
            self.http_address if self.config.http_enabled else "disabled")

    def _start_http(self) -> None:
        handler = _make_http_handler(self._store)
        self._http = _ThreadingHTTPServer(
            (self.config.http_host, self.config.http_port), handler)
        self._http_thread = threading.Thread(
            target=self._http.serve_forever, name="fp-template-store-http",
            daemon=True)
        self._http_thread.start()
        logger.info("fingerprint_template_store HTTP listening on %s",
                    self._http.server_address)

    def stop(self) -> None:
        if self._bus is not None:
            try:
                self._bus.off_message(self._on_bus_message)
            except Exception:  # pragma: no cover - defensive
                pass
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        self._http_thread = None
        self._store = None
        super().stop()

    # -- the CXC-BUS-QUERY responder --------------------------------------- #
    def _on_bus_message(self, msg) -> None:
        """Answer any `<ROOT>/templates/<op>/query` request on
        `<ROOT>/templates/<op>/result/{src}`, reading the requester `src` off
        the envelope and echoing `request_id` verbatim ([CXC-BUS-QUERY-IDIOM])."""
        from composeio.transport import MessageType
        if msg.type != MessageType.EVENT:
            return
        ns = msg.namespace
        prefix = f"{NS_ROOT}/templates/"
        if not (ns.startswith(prefix) and ns.endswith("/query")):
            return
        op = ns[len(prefix): -len("/query")]
        data = msg.data or {}
        request_id = data.get("request_id")
        src = msg.source or "unknown"
        result_ns = f"{prefix}{op}/result/{src}"

        answer = self._answer(op, data)
        self._bus.publish_event(
            result_ns, "result",
            data={"request_id": request_id, **answer},
            source=self.name)

    def _answer(self, op: str, data: dict) -> dict:
        """Perform one store operation, returning a JSON-safe result dict. A
        malformed id or unknown op is reported IN the result (ok=false), never
        raised — the requester always gets an answer, never a hang."""
        store = self._store
        if store is None:  # pragma: no cover - stopped mid-flight
            return {"ok": False, "error": "store not started"}
        try:
            if op == "list":
                rows = store.list_ids()
                return {"ok": True, "fingerprint_template_ids": rows,
                        "count": len(rows)}
            if op == "get":
                tid = data.get("fingerprint_template_id")
                raw = store.get(tid)
                if raw is None:
                    return {"ok": True, "found": False,
                            "fingerprint_template_id": tid,
                            "template_bytes_base64": None, "size_bytes": 0}
                return {"ok": True, "found": True,
                        "fingerprint_template_id": tid,
                        "encoding": "base64",
                        "template_bytes_base64":
                            base64.b64encode(raw).decode("ascii"),
                        "size_bytes": len(raw)}
            if op == "delete":
                tid = data.get("fingerprint_template_id")
                existed = store.delete(tid)
                return {"ok": True, "fingerprint_template_id": tid,
                        "existed": existed, "deleted": existed}
            return {"ok": False, "error": f"unknown operation {op!r}"}
        except BadTemplateId as exc:
            return {"ok": False, "error": str(exc),
                    "fingerprint_template_id": exc.template_id}
