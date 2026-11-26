"""print_services v1 — bus-native print surfaces (HP LaserJet + Elegoo 3D).

Standalone bare-Python ComposeBus service. Connects to MQTT, hosts N printer
backends as bus peers, and exposes an HTTP `/api/v1/status` identity endpoint
(hostname + git sha, per the P-c convention shared with bus_storage /
document_store).

Run:  python -m print_services            (services/ on PYTHONPATH)
      python services/print_services/server.py

Env:
  COMPOSEIO_MQTT_URL         broker URL (default mqtt://127.0.0.1:1883)
  PRINT_SERVICES_PORT        HTTP status port (default 8425)
  PRINT_SERVICES_HOST        HTTP bind host (default 0.0.0.0)
  PRINT_SERVICES_DAILY_CAP   per-period page cap (default 100)
  PRINT_SERVICES_HEARTBEAT   registry heartbeat seconds (default 30)
  PRINT_SERVICES_HP_IP       HP printer IP (default 192.0.2.77)
  PRINT_SERVICES_ELEGOO_IP   Elegoo printer IP (default 192.0.2.34)
  COMPOSEIO_GIT_SHA          build stamp (deploy sets it)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from .bus import BusClient
from .cap import PageCap
from .backends import HpBackend, Elegoo3dBackend

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s %(message)s")
logger = logging.getLogger("print_services")

_start_time = time.time()
_GIT_SHA = None


def _git_sha() -> str:
    """Resolve the build git SHA, cached (mirrors bus_storage/document_store).
    Never a hand-maintained tag — a value no human maintains cannot silently rot."""
    global _GIT_SHA
    if _GIT_SHA is not None:
        return _GIT_SHA
    sha = os.environ.get("COMPOSEIO_GIT_SHA", "").strip()
    if not sha:
        sha_file = Path(__file__).with_name("GIT_SHA")
        if sha_file.exists():
            sha = sha_file.read_text().strip()
    if not sha:
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).parent, stderr=subprocess.DEVNULL, timeout=2,
            ).decode().strip()
        except Exception:  # noqa: BLE001
            sha = "unknown"
    _GIT_SHA = sha
    return _GIT_SHA


class PrintServices:
    """The service: bus wiring, backends, heartbeat, job history."""

    def __init__(self):
        self.mqtt_url = os.environ.get("COMPOSEIO_MQTT_URL", "mqtt://127.0.0.1:1883")
        self.heartbeat_s = int(os.environ.get("PRINT_SERVICES_HEARTBEAT", "30"))
        cap = int(os.environ.get("PRINT_SERVICES_DAILY_CAP", "100"))
        self.page_cap = PageCap(cap)
        hp_ip = os.environ.get("PRINT_SERVICES_HP_IP", "192.0.2.77")
        elegoo_ip = os.environ.get("PRINT_SERVICES_ELEGOO_IP", "192.0.2.34")
        self.backends = {b.id: b for b in (HpBackend(hp_ip), Elegoo3dBackend(elegoo_ip))}
        self.bus = BusClient(self.mqtt_url,
                             client_id=f"print_services@{socket.gethostname()}",
                             source="print_services")
        self._history: deque = deque(maxlen=50)
        self._history_lock = threading.Lock()
        self._stop = threading.Event()

    # -- bus wiring --------------------------------------------------------

    def start_bus(self) -> bool:
        if not self.bus.connect():
            logger.error("bus connect failed — service will run HTTP-only")
            return False
        for bid in self.backends:
            # Exact submit namespace per backend; the '+' scope wildcard is in
            # the filter builder, so this catches global and local submits.
            self.bus.subscribe(f"print_services/{bid}/submit",
                               self._make_submit_handler(bid))
        self._publish_all_registry()
        self._publish_all_status()
        return True

    def _make_submit_handler(self, backend_id: str):
        def handler(value, raw):
            self._handle_submit(backend_id, value)
        return handler

    def _handle_submit(self, backend_id: str, value):
        backend = self.backends.get(backend_id)
        if backend is None:
            return
        if not isinstance(value, dict):
            logger.warning("submit to %s ignored: payload is not an object", backend_id)
            return
        job_id = value.get("job_id") or f"{backend_id}-{int(time.time()*1000)}"
        # Announce receipt first (submitted), then the terminal result.
        self._publish_result(backend_id,
                             {"job_id": job_id, "status": "submitted"})
        try:
            result = backend.submit(value, page_cap=self.page_cap)
        except Exception as exc:  # noqa: BLE001 — a backend bug must not kill the loop
            logger.exception("backend %s submit raised", backend_id)
            result = {"job_id": job_id, "status": "error",
                      "detail": f"backend exception: {exc}"}
        result.setdefault("job_id", job_id)
        self._publish_result(backend_id, result)
        self._record(backend_id, result)
        # A successful job may change printer state (busy) — refresh status.
        self._publish_status(backend_id)

    def _publish_result(self, backend_id: str, result: dict):
        self.bus.publish_event(f"print_services/{backend_id}/result", result)

    def _record(self, backend_id: str, result: dict):
        with self._history_lock:
            self._history.append({"backend": backend_id, "ts": time.time(), **result})

    def _publish_all_registry(self):
        for bid, backend in self.backends.items():
            self._publish_registry(bid, backend)

    def _publish_registry(self, backend_id: str, backend):
        row = dict(backend.descriptor)
        row["heartbeat_ts"] = time.time()
        self.bus.publish_state(f"print_services/registry/{backend_id}", row)

    def _publish_all_status(self):
        for bid in self.backends:
            self._publish_status(bid)

    def _publish_status(self, backend_id: str):
        backend = self.backends[backend_id]
        try:
            st = backend.status()
        except Exception as exc:  # noqa: BLE001
            st = {"state": "offline", "ts": time.time(), "detail": str(exc)}
        self.bus.publish_state(f"print_services/{backend_id}/status", st)
        return st

    # -- heartbeat ---------------------------------------------------------

    def heartbeat_loop(self):
        """Republish the registry (age contract) and refresh status on a period."""
        while not self._stop.wait(self.heartbeat_s):
            if not self.bus.connected:
                continue
            self._publish_all_registry()
            self._publish_all_status()

    # -- shutdown ----------------------------------------------------------

    def shutdown(self):
        self._stop.set()
        # Tombstone the retained registry rows so a future subscriber sees
        # ABSENCE, not a stale row, on graceful shutdown.
        for bid in self.backends:
            try:
                self.bus.publish_tombstone(f"print_services/registry/{bid}")
                self.bus.publish_state(f"print_services/{bid}/status",
                                       {"state": "offline", "ts": time.time(),
                                        "detail": "service shutdown"})
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.3)  # let the tombstones flush
        self.bus.disconnect()

    # -- introspection for HTTP status ------------------------------------

    def status_snapshot(self) -> dict:
        return {
            "service": "composeio-print-services",
            "version": "0.1.0",
            "hostname": socket.gethostname(),
            "git_sha": _git_sha(),
            "uptime_seconds": int(time.time() - _start_time),
            "mqtt": {"url": self.mqtt_url, "connected": self.bus.connected},
            "backends": {bid: b.descriptor for bid, b in self.backends.items()},
            "page_cap": self.page_cap.status(),
            "heartbeat_seconds": self.heartbeat_s,
            "recent_jobs": list(self._history)[-10:],
        }


# --- HTTP status server ------------------------------------------------------

_SERVICE: PrintServices | None = None


class _Handler(BaseHTTPRequestHandler):
    timeout = int(os.environ.get("PRINT_SERVICES_HTTP_TIMEOUT", "30"))

    def log_message(self, fmt, *args):
        logger.info(fmt % args)

    def _json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/v1/status":
            return self._json(_SERVICE.status_snapshot())
        if path == "/health":
            return self._json({"status": "ok"})
        self._json({"error": f"not found: {path}"}, status=404)


class _ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128
    allow_reuse_address = True


def main():
    global _SERVICE
    _SERVICE = PrintServices()

    host = os.environ.get("PRINT_SERVICES_HOST", "0.0.0.0")
    port = int(os.environ.get("PRINT_SERVICES_PORT", "8425"))

    _SERVICE.start_bus()

    hb = threading.Thread(target=_SERVICE.heartbeat_loop, daemon=True)
    hb.start()

    httpd = _ThreadingServer((host, port), _Handler)
    logger.info("print_services HTTP on %s:%s | MQTT %s | backends=%s",
                host, port, _SERVICE.mqtt_url, list(_SERVICE.backends))

    def _sig(_signum, _frame):
        logger.info("signal received — shutting down")
        try:
            _SERVICE.shutdown()
        finally:
            httpd.shutdown()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _SERVICE.shutdown()


if __name__ == "__main__":
    main()
