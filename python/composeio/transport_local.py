"""In-process transport — zero serialization, direct function calls.

Used by the Python simulator and test harness. Also the local loop
for single-process applications (DP-CIO-5 local scope).
"""

from __future__ import annotations
from collections import defaultdict
from typing import Callable
from composeio.transport import Transport, BusMessage


class LocalTransport(Transport):
    """In-process transport via direct callback dispatch.

    Messages are delivered synchronously — send() calls subscriber callbacks
    immediately in the same thread. This is the simplest and fastest transport,
    used for the simulator, tests, and local-loop resolution.
    """

    def __init__(self):
        self._subscribers: dict[str, list[Callable[[BusMessage], None]]] = defaultdict(list)
        self._wildcard_subscribers: list[Callable[[BusMessage], None]] = []
        self._running = False

    def send(self, message: BusMessage):
        """Dispatch message to all subscribers for the namespace."""
        if not self._running:
            return
        # Namespace-specific subscribers
        for cb in self._subscribers.get(message.namespace, []):
            cb(message)
        # Wildcard subscribers (bus monitors)
        for cb in self._wildcard_subscribers:
            cb(message)

    def subscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Subscribe to a specific namespace."""
        self._subscribers[namespace].append(callback)

    def subscribe_all(self, callback: Callable[[BusMessage], None]):
        """Subscribe to ALL messages (bus monitor pattern)."""
        self._wildcard_subscribers.append(callback)

    def unsubscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Remove a subscriber."""
        if namespace in self._subscribers:
            try:
                self._subscribers[namespace].remove(callback)
            except ValueError:
                pass

    def unsubscribe_all(self, callback: Callable[[BusMessage], None]):
        """Remove a wildcard subscriber."""
        try:
            self._wildcard_subscribers.remove(callback)
        except ValueError:
            pass

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    @property
    def is_connected(self) -> bool:
        return self._running

    def has_subscribers(self, namespace: str) -> bool:
        """Check if a namespace has any subscribers (for auto-scope resolution)."""
        return len(self._subscribers.get(namespace, [])) > 0
