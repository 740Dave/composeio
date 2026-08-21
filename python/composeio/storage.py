"""Bus storage client — HTTP REST interface to the bus storage service.

DP-CIO-23: Bus storage holds compiled configs, assets, workloads,
and the namespace registry. Modules pull config on boot.
Local flash is a cache, not the source of truth.
"""

from __future__ import annotations
import json
import logging
from typing import Any

logger = logging.getLogger("composeio.storage")


class BusStorageClient:
    """HTTP client for the ComposeIO bus storage REST API."""

    def __init__(self, base_url: str = "http://localhost:8420"):
        self.base_url = base_url.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    # --- Config Operations ---

    def get_config(self, module_id: str) -> bytes | None:
        """Get compiled config binary for a module."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(self._url(f"/config/{module_id}"))
            return resp.read()
        except Exception as e:
            logger.error(f"Failed to get config for '{module_id}': {e}")
            return None

    def push_config(self, module_id: str, config_binary: bytes, metadata: dict | None = None):
        """Push compiled config binary to bus storage."""
        import urllib.request
        data = config_binary
        headers = {"Content-Type": "application/octet-stream"}
        if metadata:
            headers["X-ComposeIO-Metadata"] = json.dumps(metadata)
        req = urllib.request.Request(
            self._url(f"/config/{module_id}"),
            data=data,
            headers=headers,
            method="PUT",
        )
        try:
            urllib.request.urlopen(req)
            logger.info(f"Config pushed for '{module_id}' ({len(data)} bytes)")
        except Exception as e:
            logger.error(f"Failed to push config for '{module_id}': {e}")
            raise

    def delete_config(self, module_id: str):
        """Delete config for a module."""
        import urllib.request
        req = urllib.request.Request(
            self._url(f"/config/{module_id}"),
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            logger.error(f"Failed to delete config for '{module_id}': {e}")

    def get_config_checksum(self, module_id: str) -> str | None:
        """Get just the checksum for a module's config (for change detection)."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(self._url(f"/config/{module_id}/checksum"))
            return resp.read().decode()
        except Exception:
            return None

    # --- Namespace Registry ---

    def list_namespaces(self) -> list[dict]:
        """List all registered namespaces."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(self._url("/namespaces"))
            return json.loads(resp.read())
        except Exception as e:
            logger.error(f"Failed to list namespaces: {e}")
            return []

    def get_namespace(self, namespace: str) -> dict | None:
        """Get details for a specific namespace."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(self._url(f"/namespaces/{namespace}"))
            return json.loads(resp.read())
        except Exception:
            return None

    def register_namespace(self, namespace: str, metadata: dict):
        """Register or update a namespace in the registry."""
        import urllib.request
        data = json.dumps(metadata).encode()
        req = urllib.request.Request(
            self._url(f"/namespaces/{namespace}"),
            data=data,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            logger.error(f"Failed to register namespace '{namespace}': {e}")

    # --- Assets ---

    def get_asset(self, path: str) -> bytes | None:
        """Fetch an asset by path."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(self._url(f"/assets/{path}"))
            return resp.read()
        except Exception:
            return None

    def list_assets(self, prefix: str = "") -> list[dict] | None:
        """List assets one level under `prefix` (subdirs + files).

        Returns the server's `entries` list on 200. Returns **None** if the
        server has no listing endpoint (404 — an older/un-restarted server) or
        is unreachable, so callers can fall back to curated roots. The
        None-vs-`[]` distinction is load-bearing: `[]` is a real empty dir;
        `None` means "no endpoint / unreachable".
        """
        import urllib.error
        import urllib.parse
        import urllib.request
        q = urllib.parse.urlencode({"prefix": prefix})
        try:
            resp = urllib.request.urlopen(self._url(f"/assets?{q}"))
            data = json.loads(resp.read())
            return data.get("entries", [])
        except urllib.error.HTTPError:
            return None      # 404 => server without the listing endpoint
        except Exception:
            return None      # connection failure / unreachable

    def put_asset(self, path: str, data: bytes, content_type: str = "application/octet-stream"):
        """Upload an asset."""
        import urllib.request
        req = urllib.request.Request(
            self._url(f"/assets/{path}"),
            data=data,
            headers={"Content-Type": content_type},
            method="PUT",
        )
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            logger.error(f"Failed to upload asset '{path}': {e}")

    # --- Workloads ---

    def list_workloads(self) -> list[dict]:
        """List all registered workloads."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(self._url("/workloads"))
            return json.loads(resp.read())
        except Exception:
            return []

    def push_workload(self, workload_id: str, config: dict):
        """Push a workload definition."""
        import urllib.request
        data = json.dumps(config).encode()
        req = urllib.request.Request(
            self._url(f"/workloads/{workload_id}"),
            data=data,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            urllib.request.urlopen(req)
        except Exception as e:
            logger.error(f"Failed to push workload '{workload_id}': {e}")

    # --- System Status ---

    def status(self) -> dict:
        """Get system overview."""
        import urllib.request
        try:
            resp = urllib.request.urlopen(self._url("/status"))
            return json.loads(resp.read())
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
            return {"error": str(e)}
