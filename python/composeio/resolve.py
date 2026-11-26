"""URI resolution for bus-native assets and documents (DP-CIO-23).

Turns location-independent references into bytes/dicts by fetching from the bus master's
storage service, so recipes/surfaces/widgets can point at `bus://` instead of a
host-specific path. Local paths and `file://` still work (dev / offline).

    bus://assets/<path>          -> bytes  (bus_storage  :8420  /api/v1/assets/<path>)
    bus://<collection>/<id>.json -> bytes  (bus_storage  :8420  /api/v1/blob/<collection>/<id>.json)
    fingerprint://<opaque-id>    -> bytes  (fingerprint_template_store  :8423  /api/v1/templates/<id>)
    file://<path> | <path>       -> bytes  (local filesystem)

⛔ document_store IS RETIRED ([SPEC:CXC-STORE] [DEC-STORAGE-CONSOLIDATE-ONE-FACE]). There is
ONE store, bus_storage, with ONE face, `bus://` (path-addressed bytes/blobs). A document is
now a JSON blob at a path: `bus://<collection>/<id>.json`. The server-side `where`-clause
query engine is DROPPED ([DD-STORE-QUERY-DISPOSITION]); `query_docs` filters CLIENT-SIDE
(list the collection + get each + filter in Python — trivial at the current catalog scale).

⛔ `doc://` IS FULLY DEPRECATED (David 2026-09-03: "deprecating doc:// fully … the hard
breaks will surface fast" — NO alias, NO rewrite). The compat translator is GONE:
`resolve_doc` now HARD-BREAKS on a `doc://` URI instead of translating it. A stored
`doc://` cross-reference (e.g. an app doc's `surface` field) is meant to break loudly at
resolve time and be fixed at source by rewriting it to its `bus://<collection>/<id>.json`
blob address — not papered over by a silent translation.

Endpoints default to the bus master (mqtt-broker.local) and are overridable by env
(COMPOSEIO_STORAGE_URL / COMPOSEIO_TEMPLATE_STORE_URL) so the SAME reference resolves
wherever the store happens to live — the whole point of DP-CIO-23 ("storage backend is
transport-level").

`fingerprint://` is a DELIBERATELY SEPARATE scheme from `bus://`, resolving against a
DEDICATED store on its OWN port (:8422 by default, never :8420) — David's ruling that
biometric templates must be stored separately from bus_storage, not merely a different
prefix inside the same asset tree ([DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]).
Per DP-CIO-6 ("transport is an implementation detail, not an architectural decision"),
callers resolve/put a `fingerprint://` reference the SAME way regardless of which bus
transport carried the message that named it — there is no JSON-vs-framed carve-out here.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

BUS_STORAGE_URL = os.environ.get("COMPOSEIO_STORAGE_URL", "http://mqtt-broker.local:8420")
#: The dedicated biometric template store — a SEPARATE service, SEPARATE port, SEPARATE
#: data directory from bus_storage (:8420). See services/fingerprint_template_store/.
TEMPLATE_STORE_URL = os.environ.get(
    "COMPOSEIO_TEMPLATE_STORE_URL", "http://buildhost.local:8423")


def resolve_bytes(uri: str, *, timeout: int = 30) -> bytes:
    """Resolve a `bus://…`, `fingerprint://…`, `file://…`, or local path to bytes.

    `bus://assets/<path>` goes to the fleet-facing asset route (content-type +
    asset-change-notify); any other `bus://<path>` goes to the unified blob route."""
    if uri.startswith("bus://assets/"):
        path = uri[len("bus://assets/"):]
        with urllib.request.urlopen(f"{BUS_STORAGE_URL}/api/v1/assets/{path}", timeout=timeout) as r:
            return r.read()
    if uri.startswith("bus://"):
        path = uri[len("bus://"):]
        with urllib.request.urlopen(f"{BUS_STORAGE_URL}/api/v1/blob/{path}", timeout=timeout) as r:
            return r.read()
    if uri.startswith("fingerprint://"):
        opaque_id = uri[len("fingerprint://"):]
        with urllib.request.urlopen(
                f"{TEMPLATE_STORE_URL}/api/v1/templates/{opaque_id}", timeout=timeout) as r:
            return r.read()
    if uri.startswith("file://"):
        uri = uri[len("file://"):]
    with open(uri, "rb") as f:
        return f.read()


def resolve_text(uri: str, **kw) -> str:
    return resolve_bytes(uri, **kw).decode()


def resolve_doc(uri: str, *, timeout: int = 15) -> dict:
    """Resolve a `bus://<collection>/<id>.json` reference to a document dict. A document is a
    JSON blob at a path on the unified store ([CXC-STORE-CAP-GET]).

    ⛔ `doc://` IS FULLY DEPRECATED (David 2026-09-03, no alias / no rewrite): a `doc://`
    URI HARD-BREAKS here rather than being silently translated. The breaks are meant to
    surface and be fixed AT SOURCE (a stored reference is rewritten to `bus://…json`), not
    papered over by a compat translator. Pass the `bus://` blob address directly."""
    if uri.startswith("doc://"):
        raise ValueError(
            "doc:// is removed (fully deprecated 2026-09-03) — use "
            f"bus://<collection>/<id>.json instead of: {uri}")
    return json.loads(resolve_bytes(uri, timeout=timeout))


def list_bus(prefix: str, *, timeout: int = 15) -> list[dict]:
    """List ONE level under a `bus://` prefix — the prefix-LIST behind the client-side
    query ([CXC-STORE-CAP-LIST]). `prefix` is a store-relative path (e.g. `apps/` or
    `apps`); returns the server's entry rows ({name, path, kind, size?})."""
    q = urllib.parse.urlencode({"prefix": prefix})
    with urllib.request.urlopen(f"{BUS_STORAGE_URL}/api/v1/blob?{q}", timeout=timeout) as r:
        return json.loads(r.read()).get("entries", [])


def _get_nested(doc: dict, path: str, default=None):
    """Get a nested value by dot-separated path (client-side query support)."""
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _compare(actual, op: str, expected) -> bool:
    """One where-operator, matching the retired server engine's semantics exactly."""
    if actual is None:
        return False
    try:
        a = float(actual) if isinstance(actual, (int, float)) else actual
        e = float(expected) if isinstance(expected, (int, float)) else expected
    except (TypeError, ValueError):
        return False
    return {
        ">": a > e, "<": a < e, ">=": a >= e, "<=": a <= e,
        "!=": a != e, "==": a == e,
    }.get(op, False)


def _matches(doc: dict, where: dict) -> bool:
    """Whether a document matches a where clause (client-side, [DD-STORE-QUERY-DISPOSITION])."""
    for key, condition in where.items():
        actual = _get_nested(doc, key)
        if isinstance(condition, dict):
            for op, expected in condition.items():
                if not _compare(actual, op, expected):
                    return False
        elif actual != condition:
            return False
    return True


def query_docs(collection: str, *, where: dict | None = None, fields: list | None = None,
               order_by: str | None = None, order: str = "asc", limit: int = 100,
               timeout: int = 15) -> list:
    """Query a collection CLIENT-SIDE ([DD-STORE-QUERY-DISPOSITION] — the server-side query
    engine is retired). Lists the collection's blobs, gets each, filters/orders/projects in
    Python. Returns the result rows — the exact set a restore pick list renders. Each row
    carries `doc_id`, mirroring the retired engine's output shape.

    A missing collection (empty listing) yields `[]`. An unreadable single blob is skipped,
    not fatal — one bad doc must not deny the whole pick list."""
    rows: list[dict] = []
    for entry in list_bus(f"{collection}/", timeout=timeout):
        if entry.get("kind") != "file":
            continue
        name = entry.get("name", "")
        if not name.endswith(".json"):
            continue
        doc_id = name[:-len(".json")]
        try:
            doc = json.loads(resolve_bytes(f"bus://{collection}/{name}", timeout=timeout))
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError):
            continue
        doc["doc_id"] = doc_id
        if where and not _matches(doc, where):
            continue
        rows.append(doc)

    if order_by:
        rows.sort(key=lambda d: _get_nested(d, order_by, 0) or 0,
                  reverse=(order == "desc"))
    rows = rows[:limit]
    if fields:
        rows = [{"doc_id": d["doc_id"], **{f: _get_nested(d, f) for f in fields}}
                for d in rows]
    return rows


def put_bytes(uri: str, data: bytes, *, timeout: int = 30) -> str:
    """Store bytes at a `bus://assets/…` or `fingerprint://…` reference (the
    write side of resolve_bytes — bus_storage PUT /api/v1/assets/<path>, or
    the dedicated template store's PUT /api/v1/templates/<id>).  `file://` and
    local paths write the filesystem (dev/offline parity)."""
    if uri.startswith("bus://assets/"):
        path = uri[len("bus://assets/"):]
        req = urllib.request.Request(
            f"{BUS_STORAGE_URL}/api/v1/assets/{path}", data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=timeout):
            return uri
    if uri.startswith("bus://"):
        path = uri[len("bus://"):]
        req = urllib.request.Request(
            f"{BUS_STORAGE_URL}/api/v1/blob/{path}", data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=timeout):
            return uri
    if uri.startswith("fingerprint://"):
        opaque_id = uri[len("fingerprint://"):]
        req = urllib.request.Request(
            f"{TEMPLATE_STORE_URL}/api/v1/templates/{opaque_id}", data=data, method="PUT")
        with urllib.request.urlopen(req, timeout=timeout):
            return uri
    if uri.startswith("file://"):
        uri = uri[len("file://"):]
    os.makedirs(os.path.dirname(os.path.abspath(uri)), exist_ok=True)
    with open(uri, "wb") as f:
        f.write(data)
    return uri


def put_text(uri: str, text: str, **kw) -> str:
    return put_bytes(uri, text.encode("utf-8"), **kw)


def put_doc(collection: str, doc_id: str, data: dict, *, timeout: int = 15) -> dict:
    """Store a document as a JSON blob at `bus://<collection>/<id>.json` ([CXC-STORE-CAP-GET],
    the write side of resolve_doc). Returns a small ack dict."""
    put_bytes(f"bus://{collection}/{doc_id}.json",
              json.dumps(data).encode(), timeout=timeout)
    return {"doc_id": doc_id, "collection": collection, "action": "stored"}
