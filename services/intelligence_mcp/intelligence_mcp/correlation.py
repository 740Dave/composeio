"""Request/response correlation tracker for observation tools.

Implements MCP-CORRELATION from spec 09. Several tools (get_state, get_layout,
validate) follow a request/response pattern: the MCP server publishes a request
and waits for a response on a different namespace with a matching correlation ID.

Protocol:
    1. Generate correlation ID: mcp_{agent_id}_{sequence}
    2. Subscribe to response namespace (if not already subscribed)
    3. Publish request with correlation_id in data
    4. Wait for response with matching correlation_id (timeout: 5 seconds)
    5. Return response payload as tool result
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("composeio.mcp.correlation")


class CorrelationTracker:
    """Tracks pending request/response correlations.

    Supports multiple concurrent requests (e.g., get_state while validate
    is pending). Each is tracked by its correlation ID.
    """

    def __init__(self, agent_id: str, external_bus: Any):
        self._agent_id = agent_id
        self._external_bus = external_bus
        self._sequence = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._subscribed_namespaces: set[str] = set()

    def _next_correlation_id(self) -> str:
        self._sequence += 1
        return f"mcp_{self._agent_id}_{self._sequence}"

    def _ensure_subscribed(self, response_namespace: str) -> None:
        """Subscribe to a response namespace if not already subscribed."""
        if response_namespace in self._subscribed_namespaces:
            return

        def _on_event(ns_name: str, event_type: str, data: dict) -> None:
            correlation_id = data.get("correlation_id")
            if correlation_id and correlation_id in self._pending:
                future = self._pending.pop(correlation_id)
                if not future.done():
                    future.get_loop().call_soon_threadsafe(future.set_result, data)

        self._external_bus.subscribe_events(response_namespace, _on_event)
        self._subscribed_namespaces.add(response_namespace)
        logger.debug(f"Subscribed to response namespace: {response_namespace}")

    async def request(
        self,
        request_namespace: str,
        response_namespace: str,
        data: dict | None = None,
        timeout: float = 5.0,
    ) -> dict:
        """Publish a request and wait for a correlated response.

        Args:
            request_namespace: Namespace to publish the request on.
            response_namespace: Namespace to listen for the response on.
            data: Additional data to include in the request.
            timeout: Seconds to wait before raising TimeoutError.

        Returns:
            The response data dict.

        Raises:
            TimeoutError: If no response received within timeout.
        """
        correlation_id = self._next_correlation_id()

        # Ensure we're listening for responses
        self._ensure_subscribed(response_namespace)

        # Create future for the response
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending[correlation_id] = future

        # Build request data
        request_data = dict(data) if data else {}
        request_data["correlation_id"] = correlation_id
        request_data["agent_id"] = self._agent_id
        request_data["source"] = self._agent_id
        request_data["timestamp"] = time.time()

        # Publish request to external bus
        self._external_bus.publish_event(
            request_namespace,
            "request",
            data=request_data,
            source=self._agent_id,
        )
        logger.info(
            f"Request published: {request_namespace} "
            f"(correlation_id={correlation_id}, awaiting {response_namespace})"
        )

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"Response received for {correlation_id}")
            return result
        except asyncio.TimeoutError:
            self._pending.pop(correlation_id, None)
            raise TimeoutError(
                f"ComposeUX did not respond within {timeout} seconds. "
                f"Is it running and connected to MQTT?"
            )

    def cancel_all(self) -> None:
        """Cancel all pending requests. Called on session disconnect."""
        for correlation_id, future in self._pending.items():
            if not future.done():
                future.cancel()
            logger.debug(f"Cancelled pending request: {correlation_id}")
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)
