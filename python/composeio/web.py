"""WebBusModule — bridges internal bus events to browsers via SSE.

Server-side half of the ComposeApp web bus bridge (APP-MOD-WEB).
Extends AppModule to serve HTTP and SSE on the same port using only
stdlib http.server.  No web frameworks.

Usage:
    class MyDashboard(WebBusModule):
        MODULE_NAME = "dashboard"

        def setup_routes(self):
            self.watch("test_status")
            self.watch("test_progress")
            self.watch("_app/pipeline_stage")

        def handle_request(self, path, method, params):
            if path == "/":
                return 200, "text/html", self._render_index()
            return None  # fall through to 404

    app = ComposeApp()
    app.add_module(MyDashboard(port=8889))
    app.start()

SSE format (named event, not default message):
    event: bus
    data: {"namespace": "...", "type": "state_change", "value": ...}

Browser-side:
    <script src="/static/bus-events.js"></script>
    <script>
        busEvents.on('test_status', function(evt) { ... });
    </script>
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse, parse_qs

from composeio.app import AppModule

logger = logging.getLogger("composeio.web")

# Absolute path to the static/ directory shipped with this package.
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# MIME types for static file serving.
_MIME_TYPES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class _SSEClient:
    """One connected browser SSE stream.

    Wraps a wfile (socket output) with thread-safe write.  If the
    client disconnects, send() catches BrokenPipeError and marks the
    client dead so the module can prune it.
    """

    def __init__(self, wfile):
        self._wfile = wfile
        self._lock = threading.Lock()
        self.alive = True

    def send(self, event_data: dict) -> None:
        """Push one SSE message.  Silent on broken pipe."""
        if not self.alive:
            return
        payload = json.dumps(event_data, default=str)
        message = f"event: bus\ndata: {payload}\n\n"
        with self._lock:
            try:
                self._wfile.write(message.encode("utf-8"))
                self._wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.alive = False


class WebBusModule(AppModule):
    """AppModule that serves HTTP + SSE, bridging bus events to browsers.

    Subclass this, override ``handle_request()`` for your own routes,
    and call ``watch()`` during ``setup_routes()`` to select which
    namespaces are pushed to browsers.

    Built-in routes:
        GET /events         — SSE stream (event name: ``bus``)
        GET /static/<file>  — serves bus-events.js (and any other static assets)

    Everything else is routed to ``handle_request()``.
    """

    MODULE_NAME: str = "web"
    MODULE_MANIFEST: dict[str, Any] = {
        "publishes": [],
        "subscribes": [],  # Populated dynamically from watch list
    }

    def __init__(self, host: str = "0.0.0.0", port: int = 8889):
        super().__init__()
        self.host = host
        self.port = port

        # Thread-safe SSE client list.
        self._sse_clients: list[_SSEClient] = []
        self._clients_lock = threading.Lock()

        # Namespaces to forward to browsers.
        self._watch_namespaces: list[str] = []

        # The HTTP server and its thread are created in on_ready().
        self._httpd: HTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def watch(self, namespace: str) -> None:
        """Register a namespace for browser push.  Call during setup_routes()."""
        if namespace not in self._watch_namespaces:
            self._watch_namespaces.append(namespace)

    def setup_routes(self) -> None:
        """Override to call ``watch()`` and configure routes.

        Called automatically from ``on_ready()`` before subscriptions
        are wired and the HTTP server starts.
        """
        pass

    def handle_request(
        self, path: str, method: str, params: dict[str, list[str]]
    ) -> tuple[int, str, str | bytes] | None:
        """Override to handle application-specific HTTP routes.

        Args:
            path: URL path (e.g. ``/``, ``/board/42``).
            method: HTTP method (``GET``, ``POST``, ...).
            params: Parsed query-string parameters (from ``parse_qs``).

        Returns:
            ``(status_code, content_type, body)`` or ``None`` to fall
            through to the default 404 handler.  *body* may be ``str``
            or ``bytes``.
        """
        return None

    # ------------------------------------------------------------------
    # Lifecycle (called by ComposeApp)
    # ------------------------------------------------------------------

    def on_ready(self) -> None:
        """Wire bus subscriptions and start the HTTP server."""
        # Let the subclass declare watches and custom setup.
        self.setup_routes()

        # Subscribe to each watched namespace for state *and* events.
        for ns in self._watch_namespaces:
            self.subscribe(ns, self._on_state_change)
            self.subscribe_events(ns, self._on_bus_event)

        # Update manifest so the app framework knows what we subscribe to.
        self.MODULE_MANIFEST = {
            "publishes": [],
            "subscribes": list(self._watch_namespaces),
        }

        # Build and start the HTTP server on a daemon thread.
        module_ref = self  # prevent closure over self name shadowing

        class _Handler(BaseHTTPRequestHandler):
            """Request handler that delegates to the WebBusModule."""

            # Silence per-request log lines — use composeio.web logger instead.
            def log_message(self, format, *args):
                logger.debug("HTTP %s", format % args)

            def do_GET(self):
                module_ref._dispatch(self, "GET")

            def do_POST(self):
                module_ref._dispatch(self, "POST")

        self._httpd = HTTPServer((self.host, self.port), _Handler)
        self._server_thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True
        )
        self._server_thread.start()
        logger.info(
            "WebBusModule '%s' listening on http://%s:%d  "
            "(watching %d namespaces)",
            self.MODULE_NAME,
            self.host,
            self.port,
            len(self._watch_namespaces),
        )

    def on_stop(self) -> None:
        """Shut down the HTTP server and drop SSE clients."""
        if self._httpd:
            self._httpd.shutdown()
            self._httpd = None
        with self._clients_lock:
            self._sse_clients.clear()
        logger.info("WebBusModule '%s' stopped", self.MODULE_NAME)

    # ------------------------------------------------------------------
    # Bus callbacks → SSE fan-out
    # ------------------------------------------------------------------

    def _on_state_change(self, namespace: str, new_value: Any, old_value: Any) -> None:
        """Internal bus state change → push to all SSE clients."""
        self._broadcast({
            "namespace": namespace,
            "type": "state_change",
            "value": new_value,
        })

    def _on_bus_event(self, namespace: str, event_type: str, data: dict) -> None:
        """Internal bus event → push to all SSE clients."""
        self._broadcast({
            "namespace": namespace,
            "type": "event",
            "event_type": event_type,
            "value": data,
        })

    def _broadcast(self, event_data: dict) -> None:
        """Send an SSE message to every connected client, prune dead ones."""
        with self._clients_lock:
            alive: list[_SSEClient] = []
            for client in self._sse_clients:
                client.send(event_data)
                if client.alive:
                    alive.append(client)
            self._sse_clients = alive

    # ------------------------------------------------------------------
    # HTTP request dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        """Route an HTTP request to SSE, static, subclass, or 404."""
        parsed = urlparse(handler.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # --- SSE endpoint ---
        if path == "/events" and method == "GET":
            self._handle_sse(handler)
            return

        # --- Static files (bus-events.js and friends) ---
        if path.startswith("/static/"):
            self._handle_static(handler, path)
            return

        # --- POST body parsing ---
        if method == "POST":
            content_length = int(handler.headers.get("Content-Length", 0))
            if content_length:
                body = handler.rfile.read(content_length)
                content_type = handler.headers.get("Content-Type", "")
                if "application/x-www-form-urlencoded" in content_type:
                    params.update(parse_qs(body.decode("utf-8")))
                elif "application/json" in content_type:
                    try:
                        params["_json"] = [json.loads(body.decode("utf-8"))]
                    except json.JSONDecodeError:
                        pass

        # --- Subclass routes ---
        result = self.handle_request(path, method, params)
        if result is not None:
            status, content_type, body = result
            self._send_response(handler, status, content_type, body)
            return

        # --- 404 ---
        self._send_response(handler, 404, "text/plain", "Not Found")

    # ------------------------------------------------------------------
    # SSE connection handling
    # ------------------------------------------------------------------

    def _handle_sse(self, handler: BaseHTTPRequestHandler) -> None:
        """Upgrade the connection to an SSE stream and hold it open."""
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        client = _SSEClient(handler.wfile)
        with self._clients_lock:
            self._sse_clients.append(client)

        logger.debug("SSE client connected (%d total)", len(self._sse_clients))

        # Hold the connection open until the client disconnects.
        # We detect disconnect when the client is marked dead (broken pipe
        # during a send) or when the server is shutting down.
        try:
            while client.alive and self._httpd is not None:
                time.sleep(1)
        except Exception:
            pass
        finally:
            with self._clients_lock:
                if client in self._sse_clients:
                    self._sse_clients.remove(client)
            logger.debug(
                "SSE client disconnected (%d remaining)",
                len(self._sse_clients),
            )

    # ------------------------------------------------------------------
    # Static file serving
    # ------------------------------------------------------------------

    def _handle_static(
        self, handler: BaseHTTPRequestHandler, path: str
    ) -> None:
        """Serve a file from the package's static/ directory."""
        # Strip leading /static/ and sanitise against traversal.
        relative = path[len("/static/"):]
        if ".." in relative or relative.startswith("/"):
            self._send_response(handler, 403, "text/plain", "Forbidden")
            return

        filepath = os.path.join(_STATIC_DIR, relative)
        if not os.path.isfile(filepath):
            self._send_response(handler, 404, "text/plain", "Not Found")
            return

        ext = os.path.splitext(filepath)[1].lower()
        mime = _MIME_TYPES.get(ext, "application/octet-stream")
        with open(filepath, "rb") as f:
            body = f.read()
        self._send_response(handler, 200, mime, body)

    # ------------------------------------------------------------------
    # Response helper
    # ------------------------------------------------------------------

    @staticmethod
    def _send_response(
        handler: BaseHTTPRequestHandler,
        status: int,
        content_type: str,
        body: str | bytes,
    ) -> None:
        """Write a complete HTTP response."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
