"""ComposeIO Bus Storage Service — HTTP REST API.

DP-CIO-23: Persistent storage for configs, assets, workloads, and namespace registry.
Local flash on edge devices is a cache; bus storage is the source of truth.

Runs as a lightweight HTTP server. Designed for deployment on mqtt-broker.local.

REST API:
    GET    /api/v1/config/{module_id}          — Get compiled config binary
    PUT    /api/v1/config/{module_id}          — Push compiled config binary
    DELETE /api/v1/config/{module_id}          — Remove config
    GET    /api/v1/config/{module_id}/checksum — Get config CRC-32 checksum
    GET    /api/v1/namespaces                  — List all namespaces
    GET    /api/v1/namespaces/{namespace}      — Get namespace details
    PUT    /api/v1/namespaces/{namespace}      — Register/update namespace
    GET    /api/v1/assets?prefix={path}        — List assets one level under prefix
    GET    /api/v1/assets/{path}               — Fetch asset
    PUT    /api/v1/assets/{path}               — Upload asset
    GET    /api/v1/workloads                   — List workloads
    PUT    /api/v1/workloads/{id}              — Push workload definition
    GET    /api/v1/status                      — System overview
"""

from __future__ import annotations
import binascii
import json
import logging
import os
import socket
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bus_storage")

_start_time = time.time()

# Asset-change notification producer ([SPEC:CXC-ASSET-CHANGE-NOTIFY] P-a). Set in
# main() from the environment; None until then AND whenever no broker is configured,
# in which case the write path simply skips notification. Best-effort by construction.
_NOTIFIER = None  # type: ignore[var-annotated]  # services.bus_storage.change_notify.AssetChangeNotifier | None


def _notify_asset_change(old_bytes, new_bytes):
    """Broadcast a retained {old->new} content-hash event, best-effort.

    ⛔ MUST NOT break or block an asset PUT. The notifier already swallows its own
    publish failures; this second guard covers a defect in the notifier itself so a bug
    in change advertisement can never fault the storage write path."""
    notifier = _NOTIFIER
    if notifier is None:
        return
    try:
        notifier.notify(old_bytes, new_bytes)
    except Exception:
        logger.exception("asset-change notify raised (asset write unaffected)")


_GIT_SHA = None


def _git_sha() -> str:
    """Resolve the build's git SHA, cached. The deploy stamps COMPOSEIO_GIT_SHA (or a
    sibling GIT_SHA file); a dev checkout falls back to `git rev-parse`. Never a
    hand-maintained tag -- a value no human maintains cannot silently rot."""
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
        except Exception:
            sha = "unknown"
    _GIT_SHA = sha
    return _GIT_SHA

# Data directory — all persistent data lives here
DATA_DIR = Path(os.environ.get("COMPOSEIO_DATA_DIR", "/var/lib/composeio"))
CONFIG_DIR = DATA_DIR / "configs"
ASSETS_DIR = DATA_DIR / "assets"
WORKLOADS_DIR = DATA_DIR / "workloads"
REGISTRY_FILE = DATA_DIR / "namespace_registry.json"

# --- Concurrency primitives (ThreadingHTTPServer runs one thread per request) ---
# Per-target-path locks serialize concurrent writers to the SAME path while
# preserving write parallelism across different paths. A guarding lock protects
# the dict-of-locks itself.
_path_locks: dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()
# The namespace registry is a single shared file mutated with read-modify-write,
# so it needs its own dedicated lock.
_registry_lock = threading.Lock()


def _get_path_lock(key: str) -> threading.Lock:
    """Return a stable per-path lock, creating it on first use."""
    with _path_locks_guard:
        lock = _path_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _path_locks[key] = lock
        return lock


# --- Path containment [SPEC:CXC-BUSSTORE] [CXC-BUSSTORE-CONTAINMENT] ---

class PathEscape(Exception):
    """A request-supplied segment does not resolve inside its family root."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _resolve_contained(root: Path, rel: str, *, suffix: str = "") -> Path:
    """Resolve `rel` (request-supplied) strictly INSIDE `root`. THE containment primitive.

    ⛔ EVERY route that turns a request-supplied segment into a filesystem path
    MUST go through here. Building `ROOT / segment` directly reintroduces
    unauthenticated arbitrary read/write — which is exactly how this defect
    happened: the guard existed in ONE handler (`_handle_asset_list`) as a local
    detail of a new feature and was never lifted to the seam, so the six sibling
    handlers could not reach it and none of them had it.

    Refuses, in this order:
      * an embedded NUL  -> PathEscape(400). Not an escape, just unresolvable.
      * an ABSOLUTE path -> PathEscape(403). The Python-specific trap:
        `Path("/root") / "/etc/hosts"` == `Path("/etc/hosts")` — the base is
        silently DISCARDED. There is no `..` in such a request, so a substring
        check for `..` misses it completely.
      * anything resolving outside `root` -> PathEscape(403). Covers `../` in
        any encoding (`_route` has already `unquote`d, so `%2e%2e` arrives as
        `..` and needs no separate rule) AND a symlink pointing out of the root,
        which no string check can see.

    Resolution, never string-matching: a `..` grep both over-rejects a valid name
    like `fw/v1..2/image.bin` and under-rejects the absolute form. Legitimate
    nested paths resolve normally — `bus://assets/<path>` and the fleet's boot
    config fetch run through these routes, and a guard that breaks them has
    traded a vulnerability for a fleet outage.

    `suffix` is appended AFTER containment is computed on the joined path, so the
    config family's `.bin` / `.meta.json` is part of what gets contained rather
    than something an escape can slip past.
    """
    if "\x00" in rel:
        raise PathEscape(400, "Malformed path segment")
    if rel.startswith("/"):
        raise PathEscape(403, f"Path escapes storage root: {rel}")
    try:
        root_res = root.resolve()
        candidate = (root / f"{rel}{suffix}") if rel or suffix else root
        cand_res = candidate.resolve()
    except (OSError, ValueError):
        raise PathEscape(400, "Malformed path segment")
    if not (cand_res == root_res or root_res in cand_res.parents):
        raise PathEscape(403, f"Path escapes storage root: {rel}")
    return cand_res


def _atomic_write_bytes(target: Path, data: bytes):
    """Write bytes atomically: temp file in the same dir, then os.replace().

    os.replace() is atomic on POSIX, so a concurrent reader always sees either
    the complete old file or the complete new file — never a torn write.
    """
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


def _atomic_write_text(target: Path, text: str):
    """Atomic text write (UTF-8)."""
    _atomic_write_bytes(target, text.encode())


def ensure_dirs():
    """Create data directories if they don't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    WORKLOADS_DIR.mkdir(parents=True, exist_ok=True)


def load_registry() -> dict:
    """Load namespace registry from disk."""
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text())
    return {}


def save_registry(registry: dict):
    """Save namespace registry to disk (atomic)."""
    _atomic_write_text(REGISTRY_FILE, json.dumps(registry, indent=2))


class BusStorageHandler(BaseHTTPRequestHandler):
    """HTTP request handler for bus storage API."""

    def log_message(self, format, *args):
        logger.info(format % args)

    # `_responded` is the error boundary's re-entrancy guard: it records that a
    # status line has already gone out, so a failure AFTER the first byte logs
    # instead of writing a second response into the same stream. A boundary that
    # corrupts the response it was added to protect is a downgrade.
    _responded = False

    def _send_json(self, data, status=200, headers=None):
        body = json.dumps(data, indent=2).encode()
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_binary(self, data: bytes, content_type="application/octet-stream", status=200):
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
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
        """The request ERROR BOUNDARY. [CXC-BUSSTORE-ERROR-BOUNDARY]

        ⛔ No request may terminate without an HTTP status line. Before this
        boundary existed, a malformed JSON body propagated out of the handler,
        `BaseHTTPRequestHandler` closed the socket, and the caller got a bare
        `RemoteDisconnected` — which tells an operator nothing about WHICH layer
        failed and sends them to the network to debug a JSON parse error. The
        service stays UP throughout, so every other signal reads healthy.
        """
        self._responded = False
        try:
            self._dispatch(method)
        except PathEscape as exc:
            # Containment refusal — an expected, specified outcome, not an error.
            if not self._responded:
                self._send_error(exc.status, exc.message)
        except json.JSONDecodeError as exc:
            if not self._responded:
                self._send_error(400, f"Malformed JSON body: {exc}")
        except Exception:
            # Log the traceback, answer with a status line. An unhandled failure
            # is a 500 the caller can see, never a silently dropped connection.
            logger.exception("Unhandled error serving %s %s", method, self.path)
            if not self._responded:
                self._send_error(500, "Internal server error")

    def _dispatch(self, method: str):
        """Route request to handler based on path."""
        # Split off the query string so it never leaks into `path`. No existing
        # route uses a query string, so stripping it is behaviour-preserving for
        # them; the new asset-listing route reads `prefix` from `query`.
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        # Config endpoints
        if path.startswith("/api/v1/config/"):
            remainder = path[len("/api/v1/config/"):]
            if remainder.endswith("/checksum"):
                module_id = remainder[:-len("/checksum")]
                if method == "GET":
                    return self._handle_config_checksum(module_id)
            else:
                module_id = remainder
                if method == "GET":
                    return self._handle_config_get(module_id)
                elif method == "PUT":
                    return self._handle_config_put(module_id)
                elif method == "DELETE":
                    return self._handle_config_delete(module_id)

        # Namespace endpoints
        elif path == "/api/v1/namespaces":
            if method == "GET":
                return self._handle_namespaces_list()
        elif path.startswith("/api/v1/namespaces/"):
            namespace = path[len("/api/v1/namespaces/"):]
            if method == "GET":
                return self._handle_namespace_get(namespace)
            elif method == "PUT":
                return self._handle_namespace_put(namespace)

        # Asset endpoints
        #   the bare collection path lists one level under ?prefix=; a
        #   non-empty <path> is a single-asset GET/PUT (unchanged).
        elif path == "/api/v1/assets" or path == "/api/v1/assets/":
            if method == "GET":
                prefix = query.get("prefix", [""])[0]
                return self._handle_asset_list(prefix)
        elif path.startswith("/api/v1/assets/"):
            asset_path = path[len("/api/v1/assets/"):]
            if method == "GET":
                return self._handle_asset_get(asset_path)
            elif method == "PUT":
                return self._handle_asset_put(asset_path)

        # Workload endpoints
        elif path == "/api/v1/workloads":
            if method == "GET":
                return self._handle_workloads_list()
        elif path.startswith("/api/v1/workloads/"):
            workload_id = path[len("/api/v1/workloads/"):]
            if method == "PUT":
                return self._handle_workload_put(workload_id)

        # Status
        elif path == "/api/v1/status":
            if method == "GET":
                return self._handle_status()

        # Health check
        elif path == "/health":
            return self._send_text("ok")

        self._send_error(404, f"Not found: {method} {path}")

    # --- Config Handlers ---

    def _handle_config_get(self, module_id: str):
        config_file = _resolve_contained(CONFIG_DIR, module_id, suffix=".bin")
        if not config_file.exists():
            return self._send_error(404, f"No config for module '{module_id}'")
        self._send_binary(config_file.read_bytes())

    def _handle_config_put(self, module_id: str):
        config_file = _resolve_contained(CONFIG_DIR, module_id, suffix=".bin")
        data = self._read_body()
        # SEAM, not a gap: [CXC-ASSET-CHANGE-NOTIFY] P-a is scoped to the asset tree
        # only ([CXC-ACN-PRECEDENT] — unifying this route's own config-push notifier
        # into asset-change-notify is a marked FUTURE follow-on, "not yet unified in
        # code"). Do not hang `_notify_asset_change` off this route.
        # Store metadata if provided
        meta_header = self.headers.get("X-ComposeIO-Metadata")
        with _get_path_lock(f"config:{module_id}"):
            _atomic_write_bytes(config_file, data)
            if meta_header:
                meta_file = _resolve_contained(CONFIG_DIR, module_id,
                                               suffix=".meta.json")
                _atomic_write_text(meta_file, meta_header)
        logger.info(f"Config stored for '{module_id}' ({len(data)} bytes)")
        self._send_json({"status": "ok", "module": module_id, "size": len(data)}, status=201)

    def _handle_config_delete(self, module_id: str):
        # Containment matters MOST here: without it this route is an
        # unauthenticated arbitrary-file-REMOVAL primitive.
        config_file = _resolve_contained(CONFIG_DIR, module_id, suffix=".bin")
        meta_file = _resolve_contained(CONFIG_DIR, module_id, suffix=".meta.json")
        with _get_path_lock(f"config:{module_id}"):
            if config_file.exists():
                config_file.unlink()
            if meta_file.exists():
                meta_file.unlink()
        self._send_json({"status": "deleted", "module": module_id})

    def _handle_config_checksum(self, module_id: str):
        config_file = _resolve_contained(CONFIG_DIR, module_id, suffix=".bin")
        if not config_file.exists():
            return self._send_error(404, f"No config for module '{module_id}'")
        data = config_file.read_bytes()
        checksum = binascii.crc32(data) & 0xFFFFFFFF
        self._send_text(f"{checksum:08x}")

    # --- Namespace Handlers ---

    def _handle_namespaces_list(self):
        registry = load_registry()
        self._send_json(list(registry.values()))

    def _handle_namespace_get(self, namespace: str):
        registry = load_registry()
        if namespace not in registry:
            return self._send_error(404, f"Namespace '{namespace}' not found")
        self._send_json(registry[namespace])

    def _handle_namespace_put(self, namespace: str):
        data = json.loads(self._read_body())
        data["name"] = namespace
        with _registry_lock:
            registry = load_registry()
            registry[namespace] = data
            save_registry(registry)
        self._send_json({"status": "ok", "namespace": namespace}, status=201)

    # --- Asset Handlers ---

    def _handle_asset_get(self, asset_path: str):
        file_path = _resolve_contained(ASSETS_DIR, asset_path)
        if not file_path.exists() or not file_path.is_file():
            return self._send_error(404, f"Asset not found: {asset_path}")
        # Guess content type from extension
        ext = file_path.suffix.lower()
        content_types = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".wav": "audio/wav", ".mp3": "audio/mpeg", ".json": "application/json",
            ".yaml": "text/yaml", ".yml": "text/yaml", ".bin": "application/octet-stream",
        }
        ct = content_types.get(ext, "application/octet-stream")
        self._send_binary(file_path.read_bytes(), content_type=ct)

    def _handle_asset_put(self, asset_path: str):
        file_path = _resolve_contained(ASSETS_DIR, asset_path)
        data = self._read_body()
        with _get_path_lock(f"asset:{asset_path}"):
            # Read the PRIOR bytes INSIDE the lock, before overwriting: the old
            # content hash is half the change event, and reading it under the same
            # per-path lock that serialises writers keeps old/new a consistent pair
            # ([CXC-ACN-OLDNEW]). A brand-new asset has no prior -> `old` is null
            # ([CXC-ACN-SHAPE], create case).
            old_bytes = file_path.read_bytes() if file_path.is_file() else None
            # _atomic_write_bytes creates parent dirs (exist_ok) and os.replace()s
            # the target so concurrent GETs never observe a torn asset.
            _atomic_write_bytes(file_path, data)
            # ACN P-a: broadcast a retained {old->new} content-hash event, still
            # INSIDE the per-path lock so publish order matches write order. Two
            # concurrent PUTs to the SAME path serialize their writes via this lock;
            # publishing outside it let the two publishes race independently of the
            # writes, so a late-arriving publish for an earlier write could land
            # AFTER a later write's publish, leaving the retained {old->new} event
            # advertising a superseded new-hash while the file already holds newer
            # bytes -- defeating [CXC-ACN-RETENTION] for a late subscriber. Moving
            # the call in here costs no PUT latency: `publish_state` only enqueues
            # to an in-memory queue and does not block on the network. Best-effort
            # and never blocking/breaking the write itself -- see
            # `_notify_asset_change`'s own guard.
            _notify_asset_change(old_bytes, data)
        logger.info(f"Asset stored: {asset_path} ({len(data)} bytes)")
        self._send_json({"status": "ok", "path": asset_path, "size": len(data)}, status=201)

    def _handle_asset_list(self, prefix: str):
        """List assets ONE LEVEL under `prefix` (subdirs + files, not recursive).

        Contract:
          * 200 with dirs-first, then files, each group sorted case-insensitively
            by name. Each entry carries `name`, the full slash-joined `path` from
            the assets root, and `kind` ("dir"/"file"); files also carry `size`.
          * A prefix that does not exist yet is a valid EMPTY listing -> 200 with
            `entries: []` (an absent/empty dir is not an error).
          * A prefix that escapes the assets root (`../`) -> 403.
          * A genuinely malformed prefix (e.g. an embedded NUL) -> 400.
        Dotfiles are skipped; the atomic-write temp files created by
        `_atomic_write_bytes` are named `.{name}.tmp.{pid}.{tid}` (leading dot),
        so the dotfile skip already excludes a torn temp from a listing.
        """
        # DE-FORKED 2026-08-15: this handler used to carry its own inline
        # resolve-and-contain check — the ONLY one in the service. It now calls
        # the shared primitive like every other route. The leading/trailing
        # slash strip stays here because prefix normalisation is this route's
        # documented behaviour ([CXC-BUSSTORE-API-ASSET-LIST]) and not something
        # the path-segment routes should inherit: for them a leading slash is an
        # absolute-path escape attempt and is refused, not silently repaired.
        raw = prefix or ""
        rel = raw.strip("/") if "\x00" not in raw else raw
        base_res = _resolve_contained(ASSETS_DIR, rel)

        norm_prefix = (rel + "/") if rel else ""
        dirs: list[dict] = []
        files: list[dict] = []
        # An absent/empty dir (or a prefix that names a file) yields no children
        # -> a valid 200 empty listing, never a 404.
        if base_res.is_dir():
            for child in base_res.iterdir():
                name = child.name
                if name.startswith("."):
                    continue  # dotfiles AND `.{name}.tmp.{pid}.{tid}` temps
                child_path = f"{rel}/{name}" if rel else name
                if child.is_dir():
                    dirs.append({"name": name, "path": child_path, "kind": "dir"})
                elif child.is_file():
                    files.append({"name": name, "path": child_path,
                                  "kind": "file", "size": child.stat().st_size})
        dirs.sort(key=lambda e: e["name"].lower())
        files.sort(key=lambda e: e["name"].lower())
        self._send_json({"prefix": norm_prefix, "entries": dirs + files})

    # --- Workload Handlers ---

    def _handle_workloads_list(self):
        """List workload definitions. ONE bad row must not deny the collection.

        [CXC-BUSSTORE-ERROR-BOUNDARY]: this loop used to `json.loads` every file
        unguarded, so a single corrupt or truncated file raised on the first
        iteration and the response never started — one unreadable file denied
        EVERY workload in the store. The service survived; the endpoint did not.

        A survived skip must not be SILENT, or the caller cannot tell a partial
        listing from a complete one and acts on the partial. The count rides on
        a response header because the body is a specified BARE array
        ([CXC-BUSSTORE-API-WORKLOAD]) and wrapping it to report an edge case
        would break every existing client.
        """
        workloads = []
        skipped = []
        for f in WORKLOADS_DIR.glob("*.json"):
            try:
                workloads.append(json.loads(f.read_text()))
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                skipped.append(f.name)
                logger.warning("Skipping unreadable workload '%s': %s", f.name, exc)
        headers = {"X-ComposeIO-Skipped-Entries": str(len(skipped))} if skipped else None
        self._send_json(workloads, headers=headers)

    def _handle_workload_put(self, workload_id: str):
        workload_file = _resolve_contained(WORKLOADS_DIR, workload_id, suffix=".json")
        data = json.loads(self._read_body())
        data["id"] = workload_id
        with _get_path_lock(f"workload:{workload_id}"):
            _atomic_write_text(workload_file, json.dumps(data, indent=2))
        logger.info(f"Workload stored: {workload_id}")
        self._send_json({"status": "ok", "workload": workload_id}, status=201)

    # --- Status ---

    def _handle_status(self):
        import shutil
        configs = list(CONFIG_DIR.glob("*.bin"))
        registry = load_registry()
        workloads = list(WORKLOADS_DIR.glob("*.json"))
        # Storage usage
        total_bytes = sum(f.stat().st_size for f in DATA_DIR.rglob("*") if f.is_file())
        disk = shutil.disk_usage(DATA_DIR)
        # Uptime
        uptime_s = int(time.time() - _start_time)
        self._send_json({
            "service": "composeio-bus-storage",
            "version": "0.1.0",
            "hostname": socket.gethostname(),
            "git_sha": _git_sha(),
            "uptime_seconds": uptime_s,
            "configs": len(configs),
            "namespaces": len(registry),
            "workloads": len(workloads),
            "storage_bytes": total_bytes,
            "disk_free_bytes": disk.free,
            "data_dir": str(DATA_DIR),
            "modules": [f.stem for f in configs],
        })

    # --- HTTP Method Dispatch ---

    def do_GET(self):
        self._route("GET")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")


class _ThreadingServer(ThreadingHTTPServer):
    # One thread per request so a single slow/wedged request can't block others.
    daemon_threads = True  # request threads never block shutdown
    # Larger listen backlog: the production incident was a hung request filling
    # the (default 5) accept queue and stranding downstream devices.
    request_queue_size = 128
    allow_reuse_address = True


def main():
    host = os.environ.get("COMPOSEIO_HOST", "0.0.0.0")
    port = int(os.environ.get("COMPOSEIO_PORT", "8420"))

    ensure_dirs()

    # ACN P-a: bring up the asset-change producer from the environment. Failure to build
    # it must never stop the storage service — it is an additive broadcast, not a
    # dependency of the REST API.
    global _NOTIFIER
    try:
        from change_notify import AssetChangeNotifier  # sibling module (script dir on path)
        _NOTIFIER = AssetChangeNotifier.from_env()
    except Exception as exc:  # noqa: BLE001 — producer is optional, storage is not
        logger.error("asset-change producer init failed — serving without it: %s", exc)

    server = _ThreadingServer((host, port), BusStorageHandler)
    logger.info(f"ComposeIO Bus Storage listening on {host}:{port}")
    logger.info(f"Data directory: {DATA_DIR}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
