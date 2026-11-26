"""Document catalog client — a THIN face over the unified bus_storage store.

⛔ document_store IS RETIRED ([SPEC:CXC-STORE] [DEC-STORAGE-CONSOLIDATE-ONE-FACE]).
The old `DocumentStoreClient` talked to a standalone SQLite service on :8421 with a
server-side query engine. That service is killed. A document is now a path-addressed
JSON blob on bus_storage: `doc://<collection>/<id>` == `bus://<collection>/<id>.json`.

`DocStore` keeps the small get/put/list/query surface the catalog consumers use, but
every method routes through `composeio.resolve` to the ONE store's `bus://` face:

    ds = DocStore()                                  # -> bus_storage :8420
    ds.put("bsg_planets", "kobol", {"name": "Kobol", ...})
    planet = ds.get("bsg_planets", "kobol")
    ids    = ds.list_docs("bsg_planets")
    rows   = ds.query("bsg_planets", where={"resources.water": {">": 50}})  # CLIENT-SIDE

The `where`-clause filtering runs CLIENT-SIDE ([DD-STORE-QUERY-DISPOSITION]): list the
collection + get each + filter in Python. Trivial at the current catalog scale; if a
collection ever grows large enough that this hurts, the answer is the platform's existing
bus-native query mechanism ([CXC-BUS-QUERY]), NOT a second storage interface.
"""

from __future__ import annotations

import logging
from typing import Any

from composeio import resolve as _resolve

logger = logging.getLogger("composeio.docstore")


class DocStore:
    """Bus-backed document catalog client (the retired DocumentStoreClient's replacement)."""

    def __init__(self, base_url: str | None = None,
                 host: str | None = None, port: int | None = None):
        # Accept the old host/port call shape but IGNORE :8421 — everything is the
        # unified store now. `base_url` (or COMPOSEIO_STORAGE_URL, the resolve default)
        # is the single source of truth; a passed host repoints only the host.
        if base_url:
            self.base_url = base_url
        elif host:
            self.base_url = f"http://{host}:8420"
        else:
            self.base_url = _resolve.BUS_STORAGE_URL
        # BRINGUP FIX: every CRUD/query method below delegates to the MODULE-LEVEL
        # `composeio.resolve` functions, which read `_resolve.BUS_STORAGE_URL` — not
        # `self.base_url`. Only `status()` used `self.base_url` directly, so a caller
        # passing a non-default `base_url`/`host` (e.g. a test harness, or a per-tenant
        # buildhost instance) got their override honoured for `status()` alone while every
        # other method silently hit the resolve module's default store. Masked today
        # only because every live caller's default host (`mqtt-broker.local`)
        # already matches resolve.py's own default. Repoint the shared global so this
        # instance's URL actually governs every call it makes, matching the retired
        # `DocumentStoreClient`'s per-instance guarantee.
        _resolve.BUS_STORAGE_URL = self.base_url

    # --- Status ---

    def status(self) -> dict:
        """The unified store's status (used as a reachability probe)."""
        import json
        import urllib.request
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/v1/status", timeout=5) as r:
                return json.loads(r.read())
        except Exception as e:
            logger.error(f"status failed: {e}")
            return {"error": "unreachable"}

    # --- Collections ---

    def list_collections(self) -> list[dict]:
        """Top-level entries in the store, each with a doc count. Because the store is
        unified, this now surfaces the whole store root (assets/ included) — one store,
        one browse ([SPEC:CXC-STORE])."""
        try:
            entries = _resolve.list_bus("")
        except Exception as e:
            logger.error(f"list_collections failed: {e}")
            return []
        out: list[dict] = []
        for e in entries:
            if e.get("kind") != "dir":
                continue
            name = e.get("name", "")
            out.append({"name": name, "count": len(self.list_docs(name))})
        return out

    def list_docs(self, collection: str) -> list[str]:
        """Doc ids in a collection (the `.json` blobs one level under it)."""
        try:
            entries = _resolve.list_bus(f"{collection}/")
        except Exception as e:
            logger.error(f"list_docs {collection} failed: {e}")
            return []
        ids: list[str] = []
        for e in entries:
            if e.get("kind") != "file":
                continue
            name = e.get("name", "")
            if name.endswith(".json"):
                ids.append(name[:-len(".json")])
        return ids

    # --- CRUD ---

    def get(self, collection: str, doc_id: str) -> dict | None:
        try:
            return _resolve.resolve_doc(f"bus://{collection}/{doc_id}.json")
        except Exception:
            return None

    def put(self, collection: str, doc_id: str, data: dict) -> dict | None:
        try:
            return _resolve.put_doc(collection, doc_id, data)
        except Exception as e:
            logger.error(f"put {collection}/{doc_id} failed: {e}")
            return None

    # --- Query (CLIENT-SIDE) ---

    def query(self, collection: str, where: dict | None = None,
              fields: list[str] | None = None, limit: int = 100,
              order_by: str | None = None, order: str = "asc") -> list[dict]:
        try:
            return _resolve.query_docs(collection, where=where, fields=fields,
                                       order_by=order_by, order=order, limit=limit)
        except Exception as e:
            logger.error(f"query {collection} failed: {e}")
            return []

    def compact_query(self, collection: str, query_str: str) -> str:
        """Compact query syntax (`field=value,field>value@result_field`) over the
        client-side filter — ported verbatim from the retired server so an edge caller's
        query string returns the same string result."""
        result_field = None
        if "@" in query_str:
            query_str, result_field = query_str.rsplit("@", 1)
        where: dict[str, Any] = {}
        for cond in query_str.split(","):
            cond = cond.strip()
            if not cond:
                continue
            for op in ("!=", ">=", "<=", ">", "<", "="):
                if op in cond:
                    key, val = cond.split(op, 1)
                    key, val = key.strip(), val.strip()
                    try:
                        val = float(val) if "." in val else int(val)
                    except ValueError:
                        pass
                    where[key] = val if op == "=" else {op: val}
                    break
        results = self.query(collection, where=where)
        if result_field:
            return ",".join(str(_resolve._get_nested(d, result_field, "")) for d in results)
        return ",".join(d.get("doc_id", "") for d in results)
