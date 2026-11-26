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
# The UNIFIED-store blob root ([SPEC:CXC-STORE]). Doc collections absorbed from the
# retired document_store land as `bus://<collection>/<id>.json` path-addressed blobs
# directly under the store root, alongside `assets/` — the two services already shared
# this one data dir ([CXC-STORE-EVIDENCE-COLOCATION]), so consolidation roots the blob
# face at DATA_DIR rather than a private tree.
BLOB_ROOT = DATA_DIR

# --- Storage deny-list [SPEC:CXC-STORE] (David ruled 2026-09-03: deny-list) ---
# ⛔ Applied to BOTH the generic blob route AND the asset route (PRE-SECRETS): the
# `assets/fleet/private/` credential tree is reachable through EITHER face, so the
# guard is a property of the storage root, not of one route. Wiring it onto the blob
# route alone left `GET /api/v1/assets/fleet/private/velo.creds` returning real
# credentials, unauthenticated, over the LAN — the reproduction the negative control
# in tests/test_bus_storage_device_baseline.py now pins closed on the asset route.
#
# Because the generic blob route is rooted at BLOB_ROOT == DATA_DIR it structurally
# reaches the WHOLE data dir — including two trees that have DEDICATED, narrower
# routes and MUST NOT be reachable through the generic byte-by-path face:
#   * `configs/`               — writable ONLY through /api/v1/config/<id>, which
#                                maintains the `.meta.json` checksum sidecar. A
#                                `PUT /api/v1/blob/configs/<id>.bin` would write a
#                                device config BYPASSING that checksum.
#   * `assets/fleet/private/`  — the fleet's private credential tree (a real
#                                `velo.creds` lives here on the live host). The
#                                generic blob face is unauthenticated bytes-by-path;
#                                it must never be a second door to secrets.
# Deny-list (David's ruling), not allow-list: any resolved blob path AT or UNDER a
# denied prefix is refused 403 on GET/PUT/DELETE and filtered from the prefix-LIST.
# The check runs on a path ALREADY resolved by `_resolve_contained`, so a `../`
# escape that stays inside BLOB_ROOT but lands in a denied tree is caught here too.
_BLOB_DENY_PREFIXES = ("configs", "assets/fleet/private")


def _blob_denied(resolved: Path) -> bool:
    """True if a RESOLVED blob path lies AT or UNDER any denied prefix.

    `resolved` must already be an absolute, symlink-resolved path (the output of
    `_resolve_contained`) so this compares real locations, not request strings —
    a `../`-escape or symlink into a denied tree is caught, a name that merely
    contains the word `configs` is not."""
    for rel in _BLOB_DENY_PREFIXES:
        denied = (BLOB_ROOT / rel).resolve()
        if resolved == denied or denied in resolved.parents:
            return True
    return False

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


# --- Server-provided content-hash on GET [SPEC:CXC-DEVICE-BASELINE] cap 3 ----------
# [CXC-DEVICE-BASELINE-CAP-HASH-ON-GET]. The server ALREADY computes content_hash()
# (sha256, lowercase hex) on the write path to drive the {old->new} change event
# (change_notify.content_hash). This capability PERSISTS that hash at PUT time in a
# dotfile sidecar and SERVES it back on GET as `ETag` + `X-Content-Hash`, so:
#   * there is NO recompute-on-GET — GET reads the stored value, never re-hashes;
#   * a DEVICE NEVER COMPUTES ITS OWN sha256 — it reads the server-provided hash off
#     the SAME response it fetched the bytes with and holds it as `held` for
#     [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD]'s string-compare self-select rule.
# Sidecar over in-memory map: it survives a server restart (a device fetches config at
# boot AFTER a restart) and is naturally per-path-consistent with the atomic write.
try:
    from change_notify import content_hash  # sibling module (server dir on sys.path[0])
except Exception:  # pragma: no cover — server imported outside its own dir
    import hashlib as _hashlib

    def content_hash(data: bytes) -> str:  # type: ignore[misc]
        """sha256 lowercase hex — byte-identical to change_notify.content_hash."""
        return _hashlib.sha256(data).hexdigest()


def _content_hash_sidecar(target: Path) -> Path:
    """The dotfile persisting `target`'s content hash. The leading dot means
    `_list_one_level` skips it, so a sidecar never pollutes an asset/blob listing."""
    return target.parent / f".{target.name}.chash"


def _store_content_hash(target: Path, data: bytes) -> str:
    """Persist and return the sha256 of `data` at PUT time (NEVER on GET). Best-effort:
    a sidecar write failure is logged and swallowed so it can never break the write."""
    h = content_hash(data)
    try:
        _atomic_write_text(_content_hash_sidecar(target), h)
    except OSError:
        logger.exception("content-hash sidecar write failed for %s", target)
    return h


def _load_content_hash(target: Path):
    """Read the STORED content hash for `target`, or None if none was stored (an asset
    predating this capability). Never recomputes from content — GET does no hashing."""
    sidecar = _content_hash_sidecar(target)
    try:
        if sidecar.is_file():
            return sidecar.read_text().strip() or None
    except OSError:
        logger.exception("content-hash sidecar read failed for %s", target)
    return None


def _content_hash_headers(target: Path) -> dict:
    """GET headers carrying the server-provided content hash, or {} when none is stored.
    `X-Content-Hash` is the raw hex a device holds verbatim; `ETag` is the same digest
    in HTTP's quoted form."""
    h = _load_content_hash(target)
    if not h:
        return {}
    return {"X-Content-Hash": h, "ETag": f'"{h}"'}


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


def _list_one_level(root: Path, rel: str) -> tuple[str, list[dict]]:
    """List ONE level under `root / rel` (dirs-first, each group sorted
    case-insensitively). THE shared listing primitive behind both the asset
    listing and the unified-store blob listing ([CXC-STORE-CAP-LIST]) — one
    implementation so the two faces cannot drift.

    Returns `(normalised_prefix, entries)`. `rel` is request-supplied and is
    contained through `_resolve_contained` before any directory is read. An
    absent/empty dir (or a prefix naming a file) yields a valid EMPTY listing.
    Dotfiles — and the `.{name}.tmp.{pid}.{tid}` temps `_atomic_write_bytes`
    creates — are skipped.
    """
    base_res = _resolve_contained(root, rel)
    norm_prefix = (rel + "/") if rel else ""
    dirs: list[dict] = []
    files: list[dict] = []
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
    return norm_prefix, dirs + files


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

    def _send_binary(self, data: bytes, content_type="application/octet-stream", status=200,
                     headers=None):
        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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

        # Blob endpoints — the UNIFIED-store face ([SPEC:CXC-STORE]). Generic
        #   bytes-by-path GET/PUT + a prefix-LIST, rooted at DATA_DIR so a doc
        #   absorbed from the retired document_store lands as `bus://<collection>/
        #   <id>.json` (a JSON blob at a path) and is served here. The bare
        #   collection path lists one level under ?prefix= (ids for the
        #   client-side query [DD-STORE-QUERY-DISPOSITION]); a non-empty <path>
        #   is a single-blob GET/PUT. Assets keep their own route above so the
        #   fleet-facing content-type + asset-change-notify behaviour is
        #   preserved; `bus://assets/...` still resolves there, `bus://<other>...`
        #   resolves here.
        elif path == "/api/v1/blob" or path == "/api/v1/blob/":
            if method == "GET":
                prefix = query.get("prefix", [""])[0]
                return self._handle_blob_list(prefix)
        elif path.startswith("/api/v1/blob/"):
            blob_path = path[len("/api/v1/blob/"):]
            if method == "GET":
                return self._handle_blob_get(blob_path)
            elif method == "PUT":
                return self._handle_blob_put(blob_path)
            elif method == "DELETE":
                return self._handle_blob_delete(blob_path)

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
        # PRE-SECRETS: the deny-list must guard the ASSET route too — the fleet's
        # private credential tree (`assets/fleet/private/`) is reachable here, not
        # only through the blob face. Without this an unauthenticated
        # `GET /api/v1/assets/fleet/private/velo.creds` returns real secrets over the
        # LAN. `_blob_denied` resolves DATA_DIR-relative and the asset route resolves
        # under DATA_DIR/assets, so it correctly refuses the credential tree
        # (`configs/` is structurally unreachable via this route — harmless).
        if _blob_denied(file_path):
            raise PathEscape(403, f"Asset route may not reach a protected tree: {asset_path}")
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
        # cap 3: serve the STORED content hash (ETag / X-Content-Hash); no recompute.
        self._send_binary(file_path.read_bytes(), content_type=ct,
                          headers=_content_hash_headers(file_path))

    def _handle_asset_put(self, asset_path: str):
        file_path = _resolve_contained(ASSETS_DIR, asset_path)
        # PRE-SECRETS: refuse a write INTO the protected tree on the asset route too
        # (see `_handle_asset_get`) — the deny-list is a property of the storage root,
        # not of one route.
        if _blob_denied(file_path):
            raise PathEscape(403, f"Asset route may not reach a protected tree: {asset_path}")
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
            # cap 3: persist the content hash at PUT so GET serves it without
            # recomputing and the device never hashes its own bytes.
            _store_content_hash(file_path, data)
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
        norm_prefix, entries = _list_one_level(ASSETS_DIR, rel)
        self._send_json({"prefix": norm_prefix, "entries": entries})

    # --- Blob Handlers (the UNIFIED-store face, [SPEC:CXC-STORE]) ---

    def _handle_blob_get(self, blob_path: str):
        """Fetch a blob by path from the store root (doc-as-blob GET)."""
        file_path = _resolve_contained(BLOB_ROOT, blob_path)
        if _blob_denied(file_path):
            raise PathEscape(403, f"Blob route may not reach a protected tree: {blob_path}")
        if not file_path.exists() or not file_path.is_file():
            return self._send_error(404, f"Blob not found: {blob_path}")
        ext = file_path.suffix.lower()
        content_types = {
            ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".bin": "application/octet-stream",
        }
        ct = content_types.get(ext, "application/octet-stream")
        # cap 3: serve the STORED content hash (ETag / X-Content-Hash); no recompute.
        self._send_binary(file_path.read_bytes(), content_type=ct,
                          headers=_content_hash_headers(file_path))

    def _handle_blob_put(self, blob_path: str):
        """Store a blob by path (doc-as-blob PUT).

        cap 4 ([CXC-DEVICE-BASELINE-CAP-BLOB-NOTIFY]): the blob route is the
        [SPEC:CXC-STORE] unified-store face now serving migrated document_store docs as
        `bus://<collection>/<id>.json` blobs. It rides the SAME `AssetChangeNotifier`
        (by reference — NOT a second producer) the asset PUT uses, so a migrated doc
        emits the retained {old->new} content-hash event exactly like an asset does, and
        an identical re-PUT emits NOTHING (the notifier is content-discriminating).
        Wired onto `_handle_blob_put` per the cap's own `what-changes`; the DELETE route
        is deliberately NOT wired — the ratified {old->new} shape carries a real
        new-content hash and cannot express a removal, and the asset route has no
        delete-emit to mirror. cap 3 stores the content-hash sidecar here too."""
        file_path = _resolve_contained(BLOB_ROOT, blob_path)
        if _blob_denied(file_path):
            raise PathEscape(403, f"Blob route may not reach a protected tree: {blob_path}")
        data = self._read_body()
        with _get_path_lock(f"blob:{blob_path}"):
            # Old bytes read INSIDE the lock, before overwrite, so old/new stay a
            # consistent pair ([CXC-ACN-OLDNEW]); a brand-new blob has a null `old`.
            old_bytes = file_path.read_bytes() if file_path.is_file() else None
            _atomic_write_bytes(file_path, data)
            _store_content_hash(file_path, data)   # cap 3: persist hash for GET
            _notify_asset_change(old_bytes, data)   # cap 4: {old->new}, same notifier
        logger.info(f"Blob stored: {blob_path} ({len(data)} bytes)")
        self._send_json({"status": "ok", "path": blob_path, "size": len(data)}, status=201)

    def _handle_blob_delete(self, blob_path: str):
        # Containment matters MOST here: without it this route is an
        # unauthenticated arbitrary-file-REMOVAL primitive.
        file_path = _resolve_contained(BLOB_ROOT, blob_path)
        if _blob_denied(file_path):
            raise PathEscape(403, f"Blob route may not reach a protected tree: {blob_path}")
        with _get_path_lock(f"blob:{blob_path}"):
            existed = file_path.is_file()
            if existed:
                file_path.unlink()
                # cap 3: drop the orphaned content-hash sidecar with the blob.
                _content_hash_sidecar(file_path).unlink(missing_ok=True)
        if not existed:
            return self._send_error(404, f"Blob not found: {blob_path}")
        self._send_json({"status": "deleted", "path": blob_path})

    def _handle_blob_list(self, prefix: str):
        """List blobs ONE LEVEL under `prefix` (the prefix-LIST the client-side
        query walks, [CXC-STORE-CAP-LIST]). Same contract/shape as the asset
        listing, rooted at the store root instead of the asset subtree.

        The deny-list applies to LIST too: a prefix that resolves INTO a protected
        tree is refused 403, and any protected child (e.g. the `configs/` dir at the
        store root) is filtered out so the blob face never even enumerates it."""
        raw = prefix or ""
        rel = raw.strip("/") if "\x00" not in raw else raw
        base_res = _resolve_contained(BLOB_ROOT, rel)
        if _blob_denied(base_res):
            raise PathEscape(403, f"Blob route may not reach a protected tree: {rel}")
        norm_prefix, entries = _list_one_level(BLOB_ROOT, rel)
        entries = [e for e in entries
                   if not _blob_denied((BLOB_ROOT / e["path"]).resolve())]
        self._send_json({"prefix": norm_prefix, "entries": entries})

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
