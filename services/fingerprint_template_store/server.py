"""fingerprint_template_store — dedicated biometric template HTTP server.

A DELIBERATELY MINIMAL, PURPOSE-BUILT blob store — not a re-skinned
``bus_storage`` instance. Opaque ids only, no path hierarchy, no asset
listing, no content-type sniffing: the narrow shape is part of keeping this
store legibly "biometric templates, and only that" for anyone auditing it.

REST API:
    GET    /api/v1/templates/{id}   — Fetch a template's bytes
    PUT    /api/v1/templates/{id}   — Store a template's bytes
    DELETE /api/v1/templates/{id}   — Remove a template (retention sweep, re-enroll)
    GET    /api/v1/status           — System overview
    GET    /health                  — Liveness

``{id}`` is a SERVICE-PRIVATE opaque id ([DD-FPID-TEMPLATE-REF-IS-SERVICE-
PRIVATE]) — a uuid minted by ``fingerprint_service.RosterPublisher``, never a
path leaking person/ordinal/module. It must match ``^[A-Za-z0-9_-]+$``: no
slash, so there is no path hierarchy to traverse and nothing for a `../`
escape to reach — the id becomes exactly one filename, one level under
``DATA_DIR``.

Runs as its OWN lightweight HTTP server, its OWN process, its OWN port
(:8422 by default — never bus_storage's :8420), its OWN data directory (never
bus_storage's ``assets/``). That separation — not merely a different URI
prefix inside a shared store — is David's ruling this module exists to
satisfy.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fingerprint_template_store")

_start_time = time.time()

# Data directory — DELIBERATELY not bus_storage's DATA_DIR/assets. Distinct env
# var, distinct default path, so no accidental config collision can point the
# two stores at the same tree.
DATA_DIR = Path(os.environ.get(
    "FINGERPRINT_TEMPLATE_STORE_DATA_DIR", "/var/lib/composeio/fingerprint_templates"))

# An opaque id is a bare token: no '/', no '.', no leading dot. This is
# stricter than bus_storage's path containment because a template id is never
# supposed to be a path in the first place ([DD-FPID-TEMPLATE-REF-IS-SERVICE-
# PRIVATE]) — a uuid4 hex satisfies this trivially.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Per-id locks serialize concurrent writers to the SAME id while preserving
# write parallelism across different ids (mirrors bus_storage's pattern).
_id_locks: dict[str, threading.Lock] = {}
_id_locks_guard = threading.Lock()


def _get_id_lock(template_id: str) -> threading.Lock:
    with _id_locks_guard:
        lock = _id_locks.get(template_id)
        if lock is None:
            lock = threading.Lock()
            _id_locks[template_id] = lock
        return lock


class BadTemplateId(Exception):
    """A request-supplied id is not a bare opaque token. 400, not 403 — this
    is a malformed request, not a path-escape attempt (there is no path)."""

    def __init__(self, template_id: str):
        super().__init__(f"malformed template id: {template_id!r}")
        self.template_id = template_id


def _template_path(template_id: str) -> Path:
    """The ONE place a request-supplied id becomes a filesystem path. Refuses
    anything not matching ``_ID_RE`` — in particular '/' and '..' can never
    reach ``Path.resolve()`` at all, so there is no escape to contain."""
    if not _ID_RE.match(template_id):
        raise BadTemplateId(template_id)
    return DATA_DIR / f"{template_id}.tmpl"


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write bytes atomically: temp file in the same dir, then os.replace()."""
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


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class TemplateStoreHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the dedicated template store."""

    def log_message(self, format, *args):
        logger.info(format % args)

    _responded = False

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
        """The request ERROR BOUNDARY — mirrors bus_storage's
        [CXC-BUSSTORE-ERROR-BOUNDARY]: no request may terminate without an
        HTTP status line, so the service stays observably healthy even when
        one request is malformed."""
        self._responded = False
        try:
            self._dispatch(method)
        except BadTemplateId as exc:
            if not self._responded:
                self._send_error(400, str(exc))
        except Exception:
            logger.exception("Unhandled error serving %s %s", method, self.path)
            if not self._responded:
                self._send_error(500, "Internal server error")

    def _dispatch(self, method: str):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)

        if path.startswith("/api/v1/templates/"):
            template_id = path[len("/api/v1/templates/"):]
            if method == "GET":
                return self._handle_get(template_id)
            elif method == "PUT":
                return self._handle_put(template_id)
            elif method == "DELETE":
                return self._handle_delete(template_id)
        elif path == "/api/v1/status":
            if method == "GET":
                return self._handle_status()
        elif path == "/health":
            return self._send_text("ok")

        self._send_error(404, f"Not found: {method} {path}")

    # --- Template handlers ---

    def _handle_get(self, template_id: str):
        file_path = _template_path(template_id)
        if not file_path.exists() or not file_path.is_file():
            return self._send_error(404, f"No template for id '{template_id}'")
        self._send_binary(file_path.read_bytes())

    def _handle_put(self, template_id: str):
        file_path = _template_path(template_id)
        data = self._read_body()
        with _get_id_lock(template_id):
            _atomic_write_bytes(file_path, data)
        logger.info(f"Template stored: {template_id} ({len(data)} bytes)")
        self._send_json({"status": "ok", "id": template_id, "size": len(data)}, status=201)

    def _handle_delete(self, template_id: str):
        file_path = _template_path(template_id)
        with _get_id_lock(template_id):
            if file_path.exists():
                file_path.unlink()
        self._send_json({"status": "deleted", "id": template_id})

    # --- Status ---

    def _handle_status(self):
        import shutil
        templates = list(DATA_DIR.glob("*.tmpl")) if DATA_DIR.exists() else []
        total_bytes = sum(f.stat().st_size for f in templates)
        disk = shutil.disk_usage(DATA_DIR) if DATA_DIR.exists() else None
        self._send_json({
            "service": "composeio-fingerprint-template-store",
            "version": "0.1.0",
            "uptime_seconds": int(time.time() - _start_time),
            "templates": len(templates),
            "storage_bytes": total_bytes,
            "disk_free_bytes": disk.free if disk else None,
            "data_dir": str(DATA_DIR),
        })

    def do_GET(self):
        self._route("GET")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")


class _ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128
    allow_reuse_address = True


def main() -> int:
    host = os.environ.get("FINGERPRINT_TEMPLATE_STORE_HOST", "0.0.0.0")
    port = int(os.environ.get("FINGERPRINT_TEMPLATE_STORE_PORT", "8422"))

    ensure_dirs()
    server = _ThreadingServer((host, port), TemplateStoreHandler)
    logger.info(f"fingerprint_template_store listening on {host}:{port}")
    logger.info(f"Data directory: {DATA_DIR}  (SEPARATE from bus_storage's DATA_DIR)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
