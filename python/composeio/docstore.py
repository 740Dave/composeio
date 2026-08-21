"""Document Store client — REST + bus-native query interface.

DP-CIO-23 extension: queryable document collections as a bus-native service.

Usage:
    ds = DocumentStore(host="mqtt-broker.local", port=8421)
    ds.put("bsg_planets", "kobol", {"name": "Kobol", "type": "terrestrial", ...})
    planet = ds.get("bsg_planets", "kobol")
    results = ds.query("bsg_planets", where={"resources.water": {">": 50}})
"""

from __future__ import annotations
import json
import logging
import urllib.request
import urllib.error
from typing import Any

logger = logging.getLogger("composeio.docstore")


class DocumentStoreClient:
    """HTTP client for the ComposeIO Document Store REST API."""

    def __init__(self, host: str = "localhost", port: int = 8421):
        self.base_url = f"http://{host}:{port}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def _get(self, path: str) -> Any:
        try:
            resp = urllib.request.urlopen(self._url(path), timeout=5)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.error(f"HTTP {e.code}: {path}")
            return None
        except Exception as e:
            logger.error(f"Request failed {path}: {e}")
            return None

    def _put(self, path: str, data: dict) -> Any:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            self._url(path), data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read())
        except Exception as e:
            logger.error(f"PUT failed {path}: {e}")
            return None

    def _post(self, path: str, data: dict) -> Any:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            self._url(path), data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read())
        except Exception as e:
            logger.error(f"POST failed {path}: {e}")
            return None

    def _delete(self, path: str) -> Any:
        req = urllib.request.Request(self._url(path), method="DELETE")
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            return json.loads(resp.read())
        except Exception as e:
            logger.error(f"DELETE failed {path}: {e}")
            return None

    # --- Collections ---

    def list_collections(self) -> list[dict]:
        return self._get("/collections") or []

    def list_docs(self, collection: str) -> list[str]:
        return self._get(f"/collections/{collection}") or []

    # --- CRUD ---

    def put(self, collection: str, doc_id: str, data: dict) -> dict | None:
        return self._put(f"/collections/{collection}/{doc_id}", data)

    def get(self, collection: str, doc_id: str) -> dict | None:
        return self._get(f"/collections/{collection}/{doc_id}")

    def delete(self, collection: str, doc_id: str) -> dict | None:
        return self._delete(f"/collections/{collection}/{doc_id}")

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
        result = self._post(f"/collections/{collection}/query", {
            "where": where or {},
            "fields": fields,
            "limit": limit,
            "order_by": order_by,
            "order": order,
        })
        if result and "results" in result:
            return result["results"]
        return []

    def compact_query(self, collection: str, query_str: str) -> str:
        """Bus-native compact query syntax."""
        result = self._post(f"/collections/{collection}/query", {"compact": query_str})
        if result and "result" in result:
            return result["result"]
        return ""

    # --- Binary ---

    def put_binary(self, collection: str, doc_id: str, data: bytes, content_type: str) -> dict:
        """Upload binary file to document store."""
        req = urllib.request.Request(
            self._url(f"/collections/{collection}/{doc_id}"),
            data=data,
            headers={"Content-Type": content_type, "Content-Length": str(len(data))},
            method="PUT",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            return json.loads(resp.read())
        except Exception as e:
            logger.error(f"put_binary failed {collection}/{doc_id}: {e}")
            return None

    def get_binary(self, collection: str, doc_id: str) -> tuple[bytes, str] | None:
        """Download binary file. Returns (data, content_type) or None."""
        try:
            resp = urllib.request.urlopen(
                self._url(f"/collections/{collection}/{doc_id}"), timeout=30
            )
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.read(), ct
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            logger.error(f"get_binary HTTP {e.code}: {collection}/{doc_id}")
            return None
        except Exception as e:
            logger.error(f"get_binary failed {collection}/{doc_id}: {e}")
            return None

    # --- Status ---

    def status(self) -> dict:
        return self._get("/status") or {"error": "unreachable"}
