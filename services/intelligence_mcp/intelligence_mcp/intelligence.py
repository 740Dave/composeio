"""Intelligence lifecycle management.

Implements MCP-LIFECYCLE from spec 09. Manages:
- Auto-join on first tool call (MCP-LIFECYCLE-JOIN)
- Heartbeat timer at 10-second interval (MCP-LIFECYCLE-HEARTBEAT)
- Auto-leave on disconnect (MCP-LIFECYCLE-LEAVE)
- Session state tracking (MCP-LIFECYCLE-STATE)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .correlation import CorrelationTracker

logger = logging.getLogger("composeio.mcp.intelligence")

# Intelligence capabilities derived from the tool set
CAPABILITIES = ["layout", "binding", "property", "structure", "file", "review"]


@dataclass
class SessionState:
    """Per-session state as defined in MCP-LIFECYCLE-STATE."""

    agent_id: str
    connected_at: float = field(default_factory=time.time)
    last_tool_call: float = field(default_factory=time.time)
    active_task: str = "connected"
    pending_requests: dict = field(default_factory=dict)
    batch_id: str | None = None
    batch_index: int = 0
    batch_estimated: int | None = None


class IntelligenceSession:
    """Manages the Intelligence lifecycle for a single MCP client connection.

    Auto-joins the bus on first tool call, heartbeats every 10 seconds,
    and auto-leaves on disconnect.
    """

    def __init__(self, external_bus: Any):
        self._external_bus = external_bus
        self._joined = False
        self._heartbeat_task: asyncio.Task | None = None

        # Generate agent_id: claude-{session_hash[:8]}
        session_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:8]
        self._state = SessionState(agent_id=f"claude-{session_hash}")

        # Correlation tracker for request/response tools
        self.correlation = CorrelationTracker(self._state.agent_id, external_bus)

    @property
    def agent_id(self) -> str:
        return self._state.agent_id

    @property
    def is_joined(self) -> bool:
        return self._joined

    @property
    def batch_id(self) -> str | None:
        return self._state.batch_id

    @property
    def batch_index(self) -> int:
        return self._state.batch_index

    async def ensure_joined(self) -> None:
        """Join the bus if not already joined. Called before any tool execution."""
        if self._joined:
            return

        # Publish joined event (CXC-INTEL-JOIN)
        self._external_bus.publish_event(
            "composer/intelligence/joined",
            "joined",
            data={
                "agent_id": self._state.agent_id,
                "capabilities": CAPABILITIES,
                "mode": "collaborative",
                "source": self._state.agent_id,
                "timestamp": time.time(),
            },
            source=self._state.agent_id,
        )

        self._joined = True
        logger.info(f"Intelligence joined: {self._state.agent_id}")

        # Start heartbeat timer
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat every 10 seconds (CXC-INTEL-HEARTBEAT)."""
        try:
            while True:
                await asyncio.sleep(10)
                self._external_bus.publish_event(
                    "composer/intelligence/heartbeat",
                    "heartbeat",
                    data={
                        "agent_id": self._state.agent_id,
                        "active_task": self._state.active_task,
                        "source": self._state.agent_id,
                        "timestamp": time.time(),
                    },
                    source=self._state.agent_id,
                )
                logger.debug(
                    f"Heartbeat: {self._state.agent_id} "
                    f"(task: {self._state.active_task})"
                )
        except asyncio.CancelledError:
            logger.debug("Heartbeat loop cancelled")

    async def leave(self) -> None:
        """Leave the bus. Called on SSE disconnect (MCP-LIFECYCLE-LEAVE)."""
        if not self._joined:
            return

        # Stop heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Cancel pending correlations
        self.correlation.cancel_all()

        # Publish left event (CXC-INTEL-LEAVE)
        self._external_bus.publish_event(
            "composer/intelligence/left",
            "left",
            data={
                "agent_id": self._state.agent_id,
                "source": self._state.agent_id,
                "timestamp": time.time(),
            },
            source=self._state.agent_id,
        )

        self._joined = False
        logger.info(f"Intelligence left: {self._state.agent_id}")

    def update_active_task(self, task_description: str) -> None:
        """Update the active task description (shown in heartbeat)."""
        self._state.active_task = task_description
        self._state.last_tool_call = time.time()

    # --- Batch management ---

    def begin_batch(self, description: str, estimated_commands: int | None = None) -> str:
        """Start a batch operation. Returns batch_id."""
        batch_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:8]
        self._state.batch_id = f"intel_batch_{batch_hash}"
        self._state.batch_index = 0
        self._state.batch_estimated = estimated_commands
        logger.info(f"Batch started: {self._state.batch_id} ({description})")
        return self._state.batch_id

    def next_batch_index(self) -> int:
        """Increment and return the next batch index."""
        idx = self._state.batch_index
        self._state.batch_index += 1
        return idx

    def end_batch(self) -> dict:
        """End the current batch. Returns summary."""
        if self._state.batch_id is None:
            return {"error": "No active batch"}

        summary = {
            "batch_id": self._state.batch_id,
            "total_commands": self._state.batch_index,
            "estimated": self._state.batch_estimated,
        }

        # Publish batch completion to bus so History module can commit as one undo unit
        self._external_bus.publish_event(
            "composer/intelligence/intent",
            "batch_completed",
            data={
                "agent_id": self._state.agent_id,
                "batch_id": self._state.batch_id,
                "batch_total": self._state.batch_index,
                "source": self._state.agent_id,
                "timestamp": time.time(),
            },
            source=self._state.agent_id,
        )

        self._state.batch_id = None
        self._state.batch_index = 0
        self._state.batch_estimated = None
        logger.info(f"Batch ended: {summary}")
        return summary

    def get_batch_metadata(self) -> dict | None:
        """Get batch metadata to include in bus messages, or None if not in batch."""
        if self._state.batch_id is None:
            return None
        return {
            "batch_id": self._state.batch_id,
            "batch_index": self.next_batch_index(),
        }

    def status(self) -> dict:
        """Session status for diagnostics."""
        return {
            "agent_id": self._state.agent_id,
            "joined": self._joined,
            "connected_at": self._state.connected_at,
            "last_tool_call": self._state.last_tool_call,
            "active_task": self._state.active_task,
            "batch_id": self._state.batch_id,
            "batch_index": self._state.batch_index,
            "pending_requests": self.correlation.pending_count,
        }
