"""ComposeIO Document Store — SQLite-backed, bus-native queryable documents.

The fourth infrastructure pillar:
  1. Bus Storage    (configs, assets, workloads)    port 8420
  2. Service Hub    (workload supervisor)
  3. MQTT Broker    (Mosquitto Docker)              port 1884
  4. Document Store (queryable documents)           port 8421

REST API for Python services + compact bus-native query for edge MCUs.
Zero external dependencies (sqlite3 + http.server from stdlib).

Bus-native namespaces:
  _doc/{collection}              — change notifications
  _doc/{collection}/query        — publish query, results to _doc/{collection}/result/{src}
  _doc/{collection}/{doc_id}     — specific document change notifications
"""

from __future__ import annotations
import json
import logging
import os
import socket
import sqlite3
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("document_store")


class BinaryFileCorruptError(Exception):
    """Raised when binary metadata exists but the filesystem file is missing."""
    pass


class PathEscape(Exception):
    """A request-supplied segment does not resolve inside its storage root."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --- Path containment [mirrors services/bus_storage/server.py _resolve_contained /
# PathEscape LOCALLY -- the two services are separate deploy units, so this is a
# deliberate copy, not a fork: keep both in sync by hand if the primitive changes] ---

def _resolve_contained(root: str, *parts: str) -> str:
    """Resolve `parts` (request-supplied) strictly INSIDE `root`. THE containment
    primitive for every route that turns a request-supplied segment into a
    filesystem path -- building `root + segment` directly is unauthenticated
    arbitrary read/write.

    `doc_id` legitimately contains `/` (nested ids, e.g. "a/b/c.bin"), so this
    contains within the `blobs/` ROOT as a whole, not per-collection: a normal
    nested id still resolves fine, only an escape is refused.

    Refuses, in this order:
      * an embedded NUL in any part -> PathEscape(400). Not an escape, just
        unresolvable.
      * an ABSOLUTE part -> PathEscape(403). The Python-specific trap:
        `os.path.join(root, "/etc/hosts")` == `/etc/hosts` -- `root` is silently
        DISCARDED. There is no `..` in such a request, so a substring check for
        `..` misses it completely.
      * anything resolving outside `root` -> PathEscape(403). Covers `../` in any
        encoding (the route already `unquote`s, so `%2e%2e` arrives as `..` and
        needs no separate rule) AND a symlink pointing out of the root, which no
        string check can see.

    Resolution via os.path.realpath, never string-matching: a `..` grep both
    over-rejects a valid name like `fw/v1..2/image.bin` and under-rejects the
    absolute form.
    """
    for part in parts:
        if "\x00" in part:
            raise PathEscape(400, "Malformed path segment")
        if os.path.isabs(part):
            raise PathEscape(403, f"Path escapes storage root: {part}")
    root_real = os.path.realpath(root)
    candidate = os.path.join(root, *parts) if parts else root
    target = os.path.realpath(candidate)
    if not (target == root_real or target.startswith(root_real + os.sep)):
        raise PathEscape(403, f"Path escapes storage root: {os.path.join(*parts)}")
    return target


_start_time = time.time()

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

DB_PATH = Path(os.environ.get("COMPOSEIO_DOCSTORE_DB", "/var/lib/composeio/docstore.db"))

# How long a writer waits for a competing writer's lock before giving up.
BUSY_TIMEOUT_MS = int(os.environ.get("COMPOSEIO_DOCSTORE_BUSY_TIMEOUT_MS", "5000"))


class DocumentStore:
    """SQLite-backed document store with JSON document support.

    Thread-safety model (ThreadingHTTPServer runs one thread per request):
      * sqlite3 Connection objects are NOT safe to share across threads, so each
        thread lazily opens its OWN connection via a threading.local, accessed
        through the ``_conn`` property. This avoids "SQLite objects created in a
        thread can only be used in that same thread" errors.
      * WAL journaling (a DB-level, persistent setting) allows concurrent readers
        alongside a single writer; busy_timeout lets a writer ride out short
        contention instead of failing with "database is locked".
      * ``_write_lock`` (reentrant) serializes read-modify-write sequences
        (e.g. put()'s get-then-insert/update) and DDL so they stay atomic.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._collections_cache: set[str] = set()
        self._db_dir = os.path.dirname(os.path.abspath(str(self.db_path)))
        self._load_collections()

    @property
    def _conn(self) -> sqlite3.Connection:
        """Per-thread SQLite connection (created on first use in each thread)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            self._local.conn = conn
        return conn

    def _load_collections(self):
        """Load existing collection names from sqlite_master."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS _binary_meta (
                collection TEXT NOT NULL,
                doc_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                original_filename TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (collection, doc_id)
            )
        """)
        self._conn.commit()
        self._db_dir = os.path.dirname(os.path.abspath(str(self.db_path)))
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != '_binary_meta'"
        )
        self._collections_cache = {row[0] for row in cur.fetchall()}

    def _ensure_collection(self, collection: str):
        """Create collection table if it doesn't exist."""
        if collection in self._collections_cache:
            return
        safe_name = collection.replace("-", "_").replace(".", "_")
        with self._write_lock:
            if collection in self._collections_cache:
                return
            self._conn.execute(f"""
                CREATE TABLE IF NOT EXISTS [{safe_name}] (
                    doc_id TEXT PRIMARY KEY,
                    data JSON NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            self._conn.execute(f"""
                CREATE INDEX IF NOT EXISTS [idx_{safe_name}_updated]
                ON [{safe_name}](updated_at)
            """)
            self._conn.commit()
            self._collections_cache.add(collection)

    def _table(self, collection: str) -> str:
        return collection.replace("-", "_").replace(".", "_")

    # --- CRUD ---

    def put(self, collection: str, doc_id: str, data: dict) -> dict:
        """Create or update a document."""
        self._ensure_collection(collection)
        now = time.time()
        table = self._table(collection)
        # get-then-insert/update is a read-modify-write; serialize it so two
        # concurrent writers to the same doc can't both take the INSERT branch.
        with self._write_lock:
            existing = self.get(collection, doc_id)
            if existing:
                self._conn.execute(
                    f"UPDATE [{table}] SET data=?, updated_at=? WHERE doc_id=?",
                    (json.dumps(data), now, doc_id),
                )
                action = "updated"
            else:
                self._conn.execute(
                    f"INSERT INTO [{table}] (doc_id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (doc_id, json.dumps(data), now, now),
                )
                action = "created"
            self._conn.commit()
        logger.info(f"{action}: {collection}/{doc_id}")
        return {"doc_id": doc_id, "action": action}

    def get(self, collection: str, doc_id: str) -> dict | None:
        """Get a document by ID."""
        if collection not in self._collections_cache:
            return None
        table = self._table(collection)
        cur = self._conn.execute(
            f"SELECT data, created_at, updated_at FROM [{table}] WHERE doc_id=?",
            (doc_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        doc = json.loads(row[0])
        doc["_id"] = doc_id
        doc["_created_at"] = row[1]
        doc["_updated_at"] = row[2]
        return doc

    def delete(self, collection: str, doc_id: str) -> bool:
        """Delete a document."""
        if collection not in self._collections_cache:
            return False
        table = self._table(collection)
        with self._write_lock:
            cur = self._conn.execute(
                f"DELETE FROM [{table}] WHERE doc_id=?", (doc_id,)
            )
            self._conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info(f"deleted: {collection}/{doc_id}")
        return deleted

    def list_docs(self, collection: str) -> list[str]:
        """List all document IDs in a collection."""
        if collection not in self._collections_cache:
            return []
        table = self._table(collection)
        cur = self._conn.execute(f"SELECT doc_id FROM [{table}] ORDER BY updated_at DESC")
        return [row[0] for row in cur.fetchall()]

    def list_collections(self) -> list[dict]:
        """List all collections with document counts (JSON + binary)."""
        seen = set()
        result = []
        for coll in sorted(self._collections_cache):
            table = self._table(coll)
            cur = self._conn.execute(f"SELECT COUNT(*) FROM [{table}]")
            json_count = cur.fetchone()[0]
            # Also count binary docs in this collection
            cur2 = self._conn.execute(
                "SELECT COUNT(*) FROM _binary_meta WHERE collection=?", (coll,)
            )
            bin_count = cur2.fetchone()[0]
            result.append({"name": coll, "count": json_count + bin_count})
            seen.add(coll)
        # Binary-only collections (no JSON table)
        for coll in sorted(self.binary_collections() - seen):
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM _binary_meta WHERE collection=?", (coll,)
            )
            count = cur.fetchone()[0]
            result.append({"name": coll, "count": count})
        return result

    # --- Binary CRUD ---

    def _blob_path(self, collection: str, doc_id: str) -> str:
        return _resolve_contained(os.path.join(self._db_dir, "blobs"), collection, doc_id)

    def put_binary(self, collection: str, doc_id: str, data: bytes, content_type: str) -> dict:
        """Store a binary file on the filesystem with metadata in SQLite."""
        blob = self._blob_path(collection, doc_id)
        os.makedirs(os.path.dirname(blob), exist_ok=True)
        now = time.time()
        with self._write_lock:
            # Atomic blob write: temp file in same dir then os.replace() so a
            # concurrent reader never sees a torn/partial blob.
            tmp = blob + f".tmp.{os.getpid()}.{threading.get_ident()}"
            try:
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, blob)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            self._conn.execute(
                """INSERT INTO _binary_meta (collection, doc_id, content_type, size_bytes, original_filename, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(collection, doc_id) DO UPDATE SET
                       content_type=excluded.content_type,
                       size_bytes=excluded.size_bytes,
                       updated_at=excluded.updated_at""",
                (collection, doc_id, content_type, len(data), doc_id, now, now),
            )
            self._conn.commit()
        logger.info(f"binary stored: {collection}/{doc_id} ({len(data)} bytes, {content_type})")
        return {
            "status": "ok",
            "collection": collection,
            "doc_id": doc_id,
            "size_bytes": len(data),
            "content_type": content_type,
        }

    def get_binary_meta(self, collection: str, doc_id: str) -> dict | None:
        """Get binary metadata. Returns None if not a binary document."""
        cur = self._conn.execute(
            "SELECT content_type, size_bytes, created_at, updated_at FROM _binary_meta WHERE collection=? AND doc_id=?",
            (collection, doc_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"content_type": row[0], "size_bytes": row[1], "created_at": row[2], "updated_at": row[3]}

    def get_binary(self, collection: str, doc_id: str) -> tuple[bytes, str] | None:
        """Get binary file data and content type. Returns None if not found.

        Raises BinaryFileCorruptError if metadata exists but the filesystem
        file is missing (corrupt store condition).
        """
        meta = self.get_binary_meta(collection, doc_id)
        if not meta:
            return None
        blob = self._blob_path(collection, doc_id)
        if not os.path.exists(blob):
            logger.warning(
                f"Corrupt store: metadata exists for {collection}/{doc_id} "
                f"but file is missing at {blob}"
            )
            raise BinaryFileCorruptError(
                f"Binary file missing from store — metadata exists but file not found: {collection}/{doc_id}"
            )
        with open(blob, "rb") as f:
            data = f.read()
        return data, meta["content_type"]

    def delete_binary(self, collection: str, doc_id: str) -> bool:
        """Delete a binary file and its metadata."""
        meta = self.get_binary_meta(collection, doc_id)
        if not meta:
            return False
        blob = self._blob_path(collection, doc_id)
        with self._write_lock:
            if os.path.exists(blob):
                os.remove(blob)
            self._conn.execute(
                "DELETE FROM _binary_meta WHERE collection=? AND doc_id=?",
                (collection, doc_id),
            )
            self._conn.commit()
        logger.info(f"binary deleted: {collection}/{doc_id}")
        return True

    def list_binary_docs(self, collection: str) -> list[dict]:
        """List binary documents in a collection."""
        cur = self._conn.execute(
            "SELECT doc_id, content_type, size_bytes FROM _binary_meta WHERE collection=? ORDER BY updated_at DESC",
            (collection,),
        )
        return [{"id": row[0], "type": "binary", "content_type": row[1], "size_bytes": row[2]} for row in cur.fetchall()]

    def binary_collections(self) -> set[str]:
        """Return set of collections that have binary documents."""
        cur = self._conn.execute("SELECT DISTINCT collection FROM _binary_meta")
        return {row[0] for row in cur.fetchall()}

    def binary_stats(self) -> dict:
        """Return binary storage statistics."""
        cur = self._conn.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM _binary_meta")
        row = cur.fetchone()
        return {
            "binary_files": row[0],
            "binary_size_bytes": row[1],
            "blob_directory": os.path.join(self._db_dir, "blobs"),
        }

    # --- Query ---

    def query(
        self,
        collection: str,
        where: dict | None = None,
        fields: list[str] | None = None,
        limit: int = 100,
        order_by: str | None = None,
        order: str = "asc",
    ) -> list[dict]:
        """Query documents with filtering, field selection, ordering."""
        if collection not in self._collections_cache:
            return []
        table = self._table(collection)

        # Fetch all docs (SQLite JSON functions for filtering)
        cur = self._conn.execute(
            f"SELECT doc_id, data FROM [{table}] ORDER BY updated_at DESC"
        )
        results = []
        for row in cur.fetchall():
            doc_id = row[0]
            doc = json.loads(row[1])
            doc["doc_id"] = doc_id

            # Apply where filters
            if where and not self._matches(doc, where):
                continue
            results.append(doc)

        # Order
        if order_by:
            results.sort(
                key=lambda d: self._get_nested(d, order_by, 0),
                reverse=(order == "desc"),
            )

        # Limit
        results = results[:limit]

        # Field selection
        if fields:
            results = [
                {"doc_id": d["doc_id"], **{f: self._get_nested(d, f) for f in fields}}
                for d in results
            ]

        return results

    def _matches(self, doc: dict, where: dict) -> bool:
        """Check if a document matches where conditions."""
        for key, condition in where.items():
            actual = self._get_nested(doc, key)
            if isinstance(condition, dict):
                for op, expected in condition.items():
                    if not self._compare(actual, op, expected):
                        return False
            else:
                if actual != condition:
                    return False
        return True

    def _compare(self, actual, op: str, expected) -> bool:
        """Compare with operator."""
        if actual is None:
            return False
        try:
            a = float(actual) if isinstance(actual, (int, float)) else actual
            e = float(expected) if isinstance(expected, (int, float)) else expected
        except (TypeError, ValueError):
            return False
        if op == ">":
            return a > e
        if op == "<":
            return a < e
        if op == ">=":
            return a >= e
        if op == "<=":
            return a <= e
        if op == "!=":
            return a != e
        if op == "==":
            return a == e
        return False

    def _get_nested(self, doc: dict, path: str, default=None):
        """Get a nested value by dot-separated path."""
        parts = path.split(".")
        current = doc
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    # --- Compact bus query (for edge MCUs) ---

    def compact_query(self, collection: str, query_str: str) -> str:
        """Parse compact query syntax and return compact results.

        Syntax: field=value, field>value, field<value, field!=value
        Multiple conditions: comma-separated (AND)
        Result field: append @field to return values instead of doc IDs
        """
        # Parse result field selector
        result_field = None
        if "@" in query_str:
            query_str, result_field = query_str.rsplit("@", 1)

        # Parse conditions
        where = {}
        for cond in query_str.split(","):
            cond = cond.strip()
            if not cond:
                continue
            for op in ("!=", ">=", "<=", ">", "<", "="):
                if op in cond:
                    key, val = cond.split(op, 1)
                    key = key.strip()
                    val = val.strip()
                    # Try numeric conversion
                    try:
                        val = float(val) if "." in val else int(val)
                    except ValueError:
                        pass
                    if op == "=":
                        where[key] = val
                    else:
                        where[key] = {op: val}
                    break

        results = self.query(collection, where=where)

        if result_field:
            values = [str(self._get_nested(d, result_field, "")) for d in results]
            return ",".join(values)
        else:
            return ",".join(d.get("doc_id", "") for d in results)

    # --- Status ---

    def status(self) -> dict:
        collections = self.list_collections()
        total_docs = sum(c["count"] for c in collections)
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
        stats = {
            "service": "composeio-document-store",
            "version": "0.1.0",
            "hostname": socket.gethostname(),
            "git_sha": _git_sha(),
            "uptime_seconds": int(time.time() - _start_time),
            "collections": len(collections),
            "total_documents": total_docs,
            "db_size_bytes": db_size,
            "db_path": str(self.db_path),
        }
        stats.update(self.binary_stats())
        return stats


# --- HTTP Server ---

_store: DocumentStore | None = None
_store_init_lock = threading.Lock()


def _get_store() -> DocumentStore:
    """Return the shared store, creating it once (thread-safe lazy init)."""
    global _store
    if _store is None:
        with _store_init_lock:
            if _store is None:
                _store = DocumentStore()
    return _store


class DocStoreHandler(BaseHTTPRequestHandler):
    """HTTP request handler for document store API."""

    # Per-connection read/idle timeout (seconds). socketserver.StreamRequestHandler
    # calls socket.settimeout(self.timeout) before reading, so a client that opens
    # a connection and then stalls (e.g. Content-Length announced but body never
    # fully sent) raises a timeout instead of blocking rfile.read() forever.
    # HTTPServer handles one connection at a time, so without this a single
    # stalled client wedges the entire service until it's restarted manually.
    timeout = int(os.environ.get("COMPOSEIO_DOCSTORE_TIMEOUT", "30"))

    def log_message(self, format, *args):
        logger.info(format % args)

    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str):
        self._send_json({"error": message}, status=status)

    def _send_binary(self, data: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _route(self, method: str):
        _store = _get_store()
        path = unquote(self.path)

        if path == "/api/v1/collections" and method == "GET":
            return self._send_json(_store.list_collections())

        if path == "/api/v1/status" and method == "GET":
            return self._send_json(_store.status())

        if path == "/health":
            return self._send_json({"status": "ok"})

        # /api/v1/collections/{coll}
        parts = path.split("/")
        if len(parts) >= 4 and parts[1] == "api" and parts[2] == "v1" and parts[3] == "collections":
            if len(parts) == 5:
                coll = parts[4]
                if coll == "":
                    return self._send_json(_store.list_collections())
                if method == "GET":
                    # Enhanced listing: JSON docs + binary docs
                    json_docs = [{"id": d, "type": "json"} for d in _store.list_docs(coll)]
                    binary_docs = _store.list_binary_docs(coll)
                    return self._send_json({
                        "collection": coll,
                        "documents": json_docs + binary_docs,
                    })

            if len(parts) >= 6:
                coll = parts[4]
                doc_id = "/".join(parts[5:])  # support nested doc IDs
                if doc_id == "query" and method == "POST":
                    body = json.loads(self._read_body())
                    # Check for compact query
                    if isinstance(body, str) or "compact" in body:
                        compact = body if isinstance(body, str) else body.get("compact", "")
                        result = _store.compact_query(coll, compact)
                        return self._send_json({"result": result})
                    results = _store.query(
                        coll,
                        where=body.get("where"),
                        fields=body.get("fields"),
                        limit=body.get("limit", 100),
                        order_by=body.get("order_by"),
                        order=body.get("order", "asc"),
                    )
                    return self._send_json({
                        "results": results,
                        "count": len(results),
                    })
                else:
                    if method == "GET":
                        # Binary-first: check binary metadata before JSON
                        try:
                            result = _store.get_binary(coll, doc_id)
                        except BinaryFileCorruptError as e:
                            return self._send_error(500, str(e))
                        except PathEscape as e:
                            return self._send_error(e.status, e.message)
                        if result:
                            data, ct = result
                            return self._send_binary(data, ct)
                        doc = _store.get(coll, doc_id)
                        if doc:
                            return self._send_json(doc)
                        return self._send_error(404, f"Document {coll}/{doc_id} not found")
                    elif method == "PUT":
                        ct = self.headers.get("Content-Type", "")
                        if ct.startswith("application/json") or ct == "":
                            # Existing JSON path
                            data = json.loads(self._read_body())
                            result = _store.put(coll, doc_id, data)
                            return self._send_json(result, status=201)
                        else:
                            # Binary path
                            raw = self._read_body()
                            try:
                                result = _store.put_binary(coll, doc_id, raw, ct)
                            except PathEscape as e:
                                return self._send_error(e.status, e.message)
                            return self._send_json(result, status=201)
                    elif method == "DELETE":
                        # Binary-first: try binary delete, then JSON
                        try:
                            if _store.delete_binary(coll, doc_id):
                                return self._send_json({"deleted": doc_id})
                        except PathEscape as e:
                            return self._send_error(e.status, e.message)
                        deleted = _store.delete(coll, doc_id)
                        if deleted:
                            return self._send_json({"deleted": doc_id})
                        return self._send_error(404, f"Document {coll}/{doc_id} not found")

        self._send_error(404, f"Not found: {method} {path}")

    def do_GET(self):
        self._route("GET")

    def do_PUT(self):
        self._route("PUT")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")


# Alias for external consumers
DocumentStoreHandler = DocStoreHandler


def seed_bsg_planets(store: DocumentStore):
    """Seed test data — BSG planets collection."""
    planets = [
        {"doc_id": "kobol", "name": "Kobol", "type": "terrestrial", "atmosphere": "breathable",
         "resources": {"water": 85, "tylium": 12}, "discovered": False},
        {"doc_id": "new_caprica", "name": "New Caprica", "type": "terrestrial", "atmosphere": "thin",
         "resources": {"water": 45, "tylium": 0}, "discovered": False},
        {"doc_id": "algae_planet", "name": "Algae Planet", "type": "terrestrial", "atmosphere": "toxic",
         "resources": {"water": 62, "tylium": 0}, "discovered": False},
        {"doc_id": "ragnar", "name": "Ragnar Anchorage", "type": "station", "atmosphere": "none",
         "resources": {"water": 0, "tylium": 90}, "discovered": True},
        {"doc_id": "the_colony", "name": "The Colony", "type": "artificial", "atmosphere": "none",
         "resources": {"water": 0, "tylium": 5}, "discovered": False, "cylon_controlled": True},
    ]
    for planet in planets:
        doc_id = planet.pop("doc_id")
        store.put("bsg_planets", doc_id, planet)
    logger.info(f"Seeded bsg_planets with {len(planets)} documents")


class _ThreadingServer(ThreadingHTTPServer):
    # One thread per request so a single slow/wedged request can't block others.
    daemon_threads = True  # request threads never block shutdown
    # Larger listen backlog than the default 5 so a hung request can't fill the
    # accept queue and strand downstream devices.
    request_queue_size = 128
    allow_reuse_address = True


def main():
    global _store

    host = os.environ.get("COMPOSEIO_DOCSTORE_HOST", "0.0.0.0")
    port = int(os.environ.get("COMPOSEIO_DOCSTORE_PORT", "8421"))

    _store = DocumentStore()

    # Seed test data if collection doesn't exist
    if "bsg_planets" not in {c["name"] for c in _store.list_collections()}:
        seed_bsg_planets(_store)

    server = _ThreadingServer((host, port), DocStoreHandler)
    logger.info(f"ComposeIO Document Store listening on {host}:{port}")
    logger.info(f"Database: {_store.db_path}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
