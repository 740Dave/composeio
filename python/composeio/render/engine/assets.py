"""
ComposeUX Asset Manager (M1)

Spec: [CXC-R-ENGINE-ASSETS] — assets referenced by URI in widget
definitions, fetched from the bus master's storage services, decoded by
the platform backend, cached in memory.

URI schemes (resolution via composeio.resolve, DP-CIO-23):
    bus://assets/<path>       raw bytes from bus_storage      (:8420)
    doc://<collection>/<id>   document from document_store    (:8421)
                              — image docs carry base64 payloads or a
                                content field; both handled here
    file://<path> or <path>   local filesystem (desktop dev only)

The manager caches BYTES per URI (backend-independent) and decoded image
HANDLES per (backend, URI).  Decoding goes through canvas.load_image /
the backend codec, so the engine never imports a platform library.
"""

from __future__ import annotations

import base64
import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from composeio import resolve as _resolve


class AssetManager:
    """Fetch + cache assets by URI; decode via the active Canvas backend.

    ``base_dir`` (the scene's own directory, from ``Composition.scene_dir``)
    makes a RELATIVE ``file://``/bare-path asset URI resolve SCENE-RELATIVE
    first, so a scene's bundled art loads independent of the launch cwd
    ([CXC-R-ENGINE-ASSETS]).  Empty -> the legacy cwd-relative behavior.
    """

    def __init__(self, base_dir: str = "") -> None:
        self._bytes_cache: Dict[str, bytes] = {}
        self._handle_cache: Dict[tuple, Any] = {}
        self._lock = threading.Lock()
        self.base_dir = base_dir or ""

    # -- fetch -----------------------------------------------------------

    def get_bytes(self, uri: str) -> Optional[bytes]:
        """Resolve a URI to raw bytes (cached).  Returns None on failure —
        widgets render their fallback placeholder rather than crashing
        ([CXC-R-DRAWIMAGE] tiered pipeline)."""
        with self._lock:
            if uri in self._bytes_cache:
                return self._bytes_cache[uri]
        data = self._fetch(uri)
        if data is not None:
            with self._lock:
                self._bytes_cache[uri] = data
        return data

    def _scene_relative(self, uri: str) -> Optional[str]:
        """Map a RELATIVE ``file://``/bare-path URI to an absolute path under
        ``base_dir`` when that file exists; None otherwise (so the caller falls
        back to the cwd-relative resolve).  bus://, doc://, absolute paths, and
        anything with a URL scheme are left alone ([CXC-R-ENGINE-ASSETS])."""
        if not self.base_dir:
            return None
        path = uri[len("file://"):] if uri.startswith("file://") else uri
        if "://" in path or os.path.isabs(path):
            return None
        candidate = os.path.join(self.base_dir, path)
        return candidate if os.path.exists(candidate) else None

    def _fetch(self, uri: str) -> Optional[bytes]:
        try:
            if uri.startswith("doc://"):
                doc = _resolve.resolve_doc(uri)
                return self._bytes_from_doc(doc)
            rel = self._scene_relative(uri)
            if rel is not None:
                with open(rel, "rb") as f:
                    return f.read()
            return _resolve.resolve_bytes(uri)
        except Exception:
            return None

    @staticmethod
    def _bytes_from_doc(doc: dict) -> Optional[bytes]:
        """Extract binary content from a document_store document.
        Accepts data/content/payload fields, base64-encoded or raw str."""
        for key in ("data", "content", "payload", "image"):
            val = doc.get(key)
            if val is None:
                continue
            if isinstance(val, bytes):
                return val
            if isinstance(val, str):
                try:
                    return base64.b64decode(val, validate=True)
                except Exception:
                    return val.encode()
        return None

    # -- decoded image handles ---------------------------------------------

    def get_image(self, canvas: Any, uri: str) -> Any:
        """Return a backend image handle for *uri*, or None.

        file:// and bus:// go straight to canvas.load_image (the backend
        already resolves those).  doc:// bytes are decoded via the
        backend's codec (Skia: Image.open on the byte stream).
        """
        key = (id(canvas), uri)
        with self._lock:
            if key in self._handle_cache:
                return self._handle_cache[key]

        handle = None
        if uri.startswith("doc://"):
            data = self.get_bytes(uri)
            if data is not None:
                decode = getattr(canvas, "decode_image", None)
                if decode is not None:
                    handle = decode(data)
        else:
            try:
                handle = canvas.load_image(uri)
            except Exception:
                handle = None

        if handle is not None:
            with self._lock:
                self._handle_cache[key] = handle
        return handle

    # -- prefetch ------------------------------------------------------------

    def prefetch(self, uris: list[str], parallel: int = 4) -> None:
        """Fetch asset bytes in parallel at startup ([CXC-R-ENGINE-ASSETS] #2)."""
        uris = [u for u in uris if u and u not in self._bytes_cache]
        if not uris:
            return
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            list(pool.map(self.get_bytes, uris))

    def invalidate(self, uri: Optional[str] = None) -> None:
        with self._lock:
            if uri is None:
                self._bytes_cache.clear()
                self._handle_cache.clear()
            else:
                self._bytes_cache.pop(uri, None)
                for k in [k for k in self._handle_cache if k[1] == uri]:
                    self._handle_cache.pop(k)
