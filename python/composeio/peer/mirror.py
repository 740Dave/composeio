"""Subscription-scoped mirror + lazy fetch-on-resolve ([BPR-MIRROR]).

A peer mirrors ONLY the namespaces its local subscribers subscribe to
([SPEC:CXC-PATSUB] trailing-glob set) and fetches ``bus://`` / ``doc://``
BODIES lazily on ``resolve``, not eagerly. The mirror INDEX (namespace list +
last-known state values) syncs eagerly; BODIES are fetched-on-resolve and
cached with NO eviction by default so last-known-good survives a link-down.

A resolve that cannot be satisfied returns an explicit ``unresolved`` / ``stale``
marker — NEVER a fabricated empty value ([BPR-MIRROR] negative case).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from composeio.bus import pattern_matches, is_pattern


@dataclass
class Resolution:
    """Result of a mirror resolve. ``status`` is one of:
    ``live`` (index value present), ``cached`` (body served from cache),
    ``fetched`` (body fetched upstream this call), ``stale`` (link down, only
    cached last-known-good available or nothing), ``unresolved`` (absent
    upstream / not subscribed / uncached with link down)."""
    value: Any
    status: str

    @property
    def ok(self) -> bool:
        return self.status in ("live", "cached", "fetched")


class MirrorIndex:
    """Scope-to-subscription index of namespace -> last-known state value,
    plus a no-evict body cache for resolved ``bus://`` / ``doc://`` bodies."""

    def __init__(self, subscriptions: list[str] | None = None):
        self._subs = list(subscriptions or [])
        self._state: dict[str, Any] = {}
        self._bodies: dict[str, bytes] = {}
        self._lock = threading.RLock()

    # --- subscription scope ------------------------------------------------

    def set_subscriptions(self, subs: list[str]) -> None:
        with self._lock:
            self._subs = list(subs)

    def subscriptions(self) -> list[str]:
        with self._lock:
            return list(self._subs)

    def in_scope(self, namespace: str) -> bool:
        with self._lock:
            for pat in self._subs:
                if pattern_matches(pat, namespace):
                    return True
            return False

    # --- index -------------------------------------------------------------

    def update(self, namespace: str, value: Any) -> None:
        with self._lock:
            self._state[namespace] = value

    def load_index(self, index: dict[str, Any]) -> None:
        with self._lock:
            self._state.update(index)

    def namespaces(self) -> list[str]:
        with self._lock:
            return sorted(self._state)

    def get(self, namespace: str) -> Any:
        with self._lock:
            return self._state.get(namespace)

    def resolve_state(self, namespace: str) -> Resolution:
        """Resolve a mirrored namespace's last-known state. A namespace outside
        the subscription scope or absent from the index returns ``unresolved``
        — never a fabricated empty value."""
        with self._lock:
            if not self.in_scope(namespace):
                return Resolution(None, "unresolved")
            if namespace in self._state:
                return Resolution(self._state[namespace], "live")
            return Resolution(None, "unresolved")

    # --- body cache (no eviction by default) -------------------------------

    def cached_body(self, uri: str) -> bytes | None:
        with self._lock:
            return self._bodies.get(uri)

    def cache_body(self, uri: str, body: bytes) -> None:
        with self._lock:
            self._bodies[uri] = body
