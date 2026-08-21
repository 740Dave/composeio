"""HTTP Relay transport binding (BP-EXT-HTTP-RELAY).

Forwards bus messages as HTTP POST to a configurable endpoint.
Fire-and-forget with optional namespace filtering and batching.
Used for telemetry, monitoring, and Nexus coordination relay integration.

DP-CIO-6: Transport is an implementation detail, not an architectural decision.
"""

from __future__ import annotations
import json
import logging
import threading
from collections import deque
from typing import Any, Callable
from urllib.request import urlopen, Request
from urllib.error import URLError

from composeio.transport import Transport, BusMessage
from composeio.transport_contract import (
    Directionality,
    FarSide,
    QoS,
    TransportProfile,
)

logger = logging.getLogger("composeio.transport.http_relay")


class HttpRelayTransport(Transport):
    """HTTP relay transport — forwards bus messages as HTTP POST.

    The THIN / EGRESS pole of the unified Transport contract
    ([BPR-TRANSPORT-CONTRACT]): fire-and-forget onto a dumb sink. Outbound
    (bus -> HTTP) by default, with optional namespace filtering and batching.
    Each POST runs on a daemon thread to avoid blocking the bus. Telemetry is
    the exemplar of this pole.

    It declares ``QoS.FIRE_AND_FORGET`` and is DELIBERATELY not forced through
    the peer's handshake / mirror / critical-ack machinery ([BPR-CRITICAL-ACK]
    lives at the BRIDGE pole). ``send`` treats a ``pri: critical`` message
    exactly like any other — it never opens an ack-pending entry, never awaits a
    return frame — because a one-way emitter to a dumb sink has no return path
    to carry an ack. The ``profile`` makes that honest thinness declarable and
    machine-readable rather than implicit.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:8422/messages",
        channel: str = "composeio",
        direction: str = "outbound",  # "outbound" or "bidirectional"
        filter_namespaces: list[str] | None = None,
        batch_interval_ms: int = 0,
        timeout: float = 5.0,
    ):
        self._endpoint = endpoint
        self._channel = channel
        self._direction = direction
        self._filters = filter_namespaces
        self._batch_interval = batch_interval_ms / 1000.0 if batch_interval_ms else 0
        self._timeout = timeout
        self._running = False
        self._batch: deque[dict] = deque()
        self._batch_timer: threading.Timer | None = None
        self._subscribers: dict[str, list[Callable]] = {}
        self._all_subscribers: list[Callable] = []

        # Declared position on the thin-to-fat spectrum ([BPR-TRANSPORT-CONTRACT]).
        # Always fire-and-forget onto a dumb sink; directionality follows the
        # configured ``direction``. Even a "bidirectional" HTTP relay is
        # fire-and-forget on the OUTBOUND leg — it never carries critical-ack,
        # which is why it stays QoS.FIRE_AND_FORGET regardless of direction.
        self.profile = TransportProfile(
            qos=QoS.FIRE_AND_FORGET,
            directionality=(Directionality.BIDIRECTIONAL
                            if direction == "bidirectional"
                            else Directionality.EGRESS),
            far_side=FarSide.DUMB_SINK,
        )

    def _matches_filter(self, namespace: str) -> bool:
        """Check if a namespace matches the configured filter list."""
        if not self._filters:
            return True
        for f in self._filters:
            if f.endswith("/*"):
                if namespace.startswith(f[:-1]):
                    return True
            elif f == namespace:
                return True
        return False

    def send(self, message: BusMessage):
        """Send a message via HTTP POST. Fire-and-forget."""
        if not self._running:
            return
        if not self._matches_filter(message.namespace):
            return

        msg_dict = message.to_json()

        if self._batch_interval > 0:
            self._batch.append(msg_dict)
            if self._batch_timer is None:
                self._batch_timer = threading.Timer(self._batch_interval, self._flush_batch)
                self._batch_timer.daemon = True
                self._batch_timer.start()
        else:
            self._post([msg_dict])

    def _flush_batch(self):
        """Flush accumulated batch of messages."""
        self._batch_timer = None
        if self._batch:
            messages = list(self._batch)
            self._batch.clear()
            self._post(messages)

    def _post(self, messages: list[dict]):
        """POST messages to the relay endpoint on a daemon thread."""
        payload = json.dumps({
            "from": self._channel,
            "channel": self._channel,
            "messages": messages,
        }).encode()

        def _do_post():
            try:
                req = Request(self._endpoint, data=payload,
                              headers={"Content-Type": "application/json"})
                urlopen(req, timeout=self._timeout)
            except (URLError, OSError) as e:
                logger.debug(f"HTTP relay POST failed: {e}")

        t = threading.Thread(target=_do_post, daemon=True)
        t.start()

    def subscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Subscribe to inbound messages (bidirectional mode only)."""
        if namespace not in self._subscribers:
            self._subscribers[namespace] = []
        self._subscribers[namespace].append(callback)

    def subscribe_all(self, callback: Callable[[BusMessage], None]):
        """Register a wildcard inbound subscriber to lower to the unified
        Transport waist ([BPR-TRANSPORT-CONTRACT]).

        On the EGRESS pole there is no inbound path — a one-way relay to a dumb
        sink never delivers to this callback; it exists only so the emitter
        satisfies the same method waist the bridge pole does (and so
        ``bus._wire_transport_incoming`` can attach uniformly). Any inbound leg
        in a future ``bidirectional`` mode would fan through here — still
        fire-and-forget, still no ack.
        """
        self._all_subscribers.append(callback)

    def unsubscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Unsubscribe from inbound messages."""
        if namespace in self._subscribers:
            try:
                self._subscribers[namespace].remove(callback)
            except ValueError:
                pass

    def start(self):
        """Start the HTTP relay transport."""
        self._running = True
        logger.info(f"HTTP relay transport started -> {self._endpoint} (channel: {self._channel})")

    def stop(self):
        """Stop the HTTP relay transport. Flushes remaining batch."""
        self._running = False
        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None
        # Flush remaining messages
        if self._batch:
            self._flush_batch()

    @property
    def is_connected(self) -> bool:
        """Check if transport is running."""
        return self._running
