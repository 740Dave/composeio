"""URI resolution for bus-native assets and documents (DP-CIO-23).

Turns location-independent references into bytes/dicts by fetching from the bus master's
storage services, so recipes/surfaces/widgets can point at `bus://` / `doc://` instead of
a host-specific path. Local paths and `file://` still work (dev / offline).

    bus://assets/<path>          -> bytes  (bus_storage      :8420  /api/v1/assets/<path>)
    doc://<collection>/<id>      -> dict   (document_store   :8421  /api/v1/collections/<c>/<id>)
    fingerprint://<opaque-id>    -> bytes  (fingerprint_template_store  :8423  /api/v1/templates/<id>)
    file://<path> | <path>       -> bytes  (local filesystem)

Endpoints default to the bus master (mqtt-broker.local) and are overridable by env
(COMPOSEIO_STORAGE_URL / COMPOSEIO_DOCSTORE_URL / COMPOSEIO_TEMPLATE_STORE_URL) so the SAME
reference resolves wherever the store happens to live — the whole point of DP-CIO-23
("storage backend is transport-level").

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
import urllib.request

BUS_STORAGE_URL = os.environ.get("COMPOSEIO_STORAGE_URL", "http://mqtt-broker.local:8420")
DOC_STORE_URL = os.environ.get("COMPOSEIO_DOCSTORE_URL", "http://mqtt-broker.local:8421")
#: The dedicated biometric template store — a SEPARATE service, SEPARATE port, SEPARATE
#: data directory from bus_storage (:8420). See services/fingerprint_template_store/.
TEMPLATE_STORE_URL = os.environ.get(
    "COMPOSEIO_TEMPLATE_STORE_URL", "http://node.example.local:8423")


def resolve_bytes(uri: str, *, timeout: int = 30) -> bytes:
    """Resolve a `bus://assets/…`, `fingerprint://…`, `file://…`, or local path to bytes."""
    if uri.startswith("bus://assets/"):
        path = uri[len("bus://assets/"):]
        with urllib.request.urlopen(f"{BUS_STORAGE_URL}/api/v1/assets/{path}", timeout=timeout) as r:
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
    """Resolve a `doc://<collection>/<doc_id>` reference to a document dict."""
    if not uri.startswith("doc://"):
        raise ValueError(f"not a doc:// URI: {uri}")
    coll, _, doc_id = uri[len("doc://"):].partition("/")
    if not coll or not doc_id:
        raise ValueError(f"doc:// URI must be doc://<collection>/<doc_id>: {uri}")
    with urllib.request.urlopen(f"{DOC_STORE_URL}/api/v1/collections/{coll}/{doc_id}", timeout=timeout) as r:
        return json.loads(r.read())


def query_docs(collection: str, *, where: dict | None = None, fields: list | None = None,
               order_by: str | None = None, order: str = "asc", limit: int = 100,
               timeout: int = 15) -> list:
    """Query a collection (the doc store's POST …/query). Returns the result rows — the
    exact set a restore pick list renders."""
    body: dict = {"limit": limit, "order": order}
    if where:
        body["where"] = where
    if fields:
        body["fields"] = fields
    if order_by:
        body["order_by"] = order_by
    req = urllib.request.Request(
        f"{DOC_STORE_URL}/api/v1/collections/{collection}/query",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("results", [])


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
    """Store a document (the write side of doc://)."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{DOC_STORE_URL}/api/v1/collections/{collection}/{doc_id}",
        data=body, headers={"Content-Type": "application/json"}, method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())
