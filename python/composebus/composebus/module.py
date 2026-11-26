"""Module — base class for simulated ComposeIO modules.

A module is a bus participant with an ID, a role, and a set of configured
namespaces. Simulated modules (CTk widgets) extend this class.
"""

from __future__ import annotations
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from composebus.bus import ComposeIOBus


class Module:
    """Base class for all ComposeIO modules (simulated or real)."""

    def __init__(self, module_id: str, role: str):
        self.module_id = module_id
        self.role = role
        self._bus: ComposeIOBus | None = None
        self._namespaces: list[str] = []  # Namespaces this module publishes to
        self._subscriptions: list[str] = []  # Namespaces this module consumes

    def attach(self, bus: ComposeIOBus):
        """Attach this module to a bus. Called by the bus during registration."""
        self._bus = bus

    def detach(self):
        """Detach from the bus."""
        self._bus = None

    def publish_state(self, namespace: str, value: Any):
        """Publish a state change to a namespace."""
        if self._bus is None:
            raise RuntimeError(f"Module '{self.module_id}' is not attached to a bus")
        self._bus.publish_state(namespace, value, source=self.module_id)

    def publish_event(self, namespace: str, event_type: str, data: dict | None = None):
        """Publish a one-shot event."""
        if self._bus is None:
            raise RuntimeError(f"Module '{self.module_id}' is not attached to a bus")
        self._bus.publish_event(namespace, event_type, data=data, source=self.module_id)

    def subscribe(self, namespace: str, callback: Callable):
        """Subscribe to state changes on a namespace."""
        if self._bus is None:
            raise RuntimeError(f"Module '{self.module_id}' is not attached to a bus")
        self._bus.subscribe(namespace, callback)
        self._subscriptions.append(namespace)

    def get_state(self, namespace: str) -> Any:
        """Get current value of a namespace."""
        if self._bus is None:
            raise RuntimeError(f"Module '{self.module_id}' is not attached to a bus")
        return self._bus.get_state(namespace)

    def discovery_info(self) -> dict:
        """Return discovery announcement data."""
        return {
            "module_id": self.module_id,
            "role": self.role,
            "publishes": self._namespaces,
            "subscribes": self._subscriptions,
        }
