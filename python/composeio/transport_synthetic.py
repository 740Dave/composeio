"""Synthetic transport — generates traffic from .gen.yaml pattern configs in real-time.

CR-SYNTHETIC-LIVE: Reads a generator config and produces messages in real-time,
behaving identically to ReplayTransport but without a pre-generated file.
Useful for development — no capture step needed.

Shares the same dispatch interface as ReplayTransport. The difference is
source: file vs real-time computation.
"""

from __future__ import annotations
import logging
import math
import random as rng_module
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import yaml

from composeio.transport import Transport, BusMessage, MessageType
from composeio.generate import PatternGenerator, _parse_duration, _parse_rate

logger = logging.getLogger("composeio.transport.synthetic")


class _LiveNamespace:
    """Tracks real-time generation state for one namespace pattern."""

    def __init__(self, ns_config: dict, total_duration_s: float):
        self.name = ns_config["name"]
        self.engine = ns_config.get("state_engine", "binary")
        self.src = ns_config.get("src", f"gen-{self.name}")
        self.scope = ns_config.get("scope", "global")
        self.pattern = ns_config.get("pattern", {})
        self.pattern_type = self.pattern.get("type", "constant")
        self.total_duration_s = total_duration_s

        rate_str = ns_config.get("publish_rate", "1hz")
        self.rate_hz = _parse_rate(rate_str)
        self.interval_s = 1.0 / self.rate_hz if self.rate_hz > 0 else total_duration_s

        # Use PatternGenerator for value computation (reuse logic)
        self._gen = PatternGenerator(ns_config, total_duration_s)

        # Sequence counter
        self._seq = 0

    def compute_value(self, elapsed_s: float) -> Any:
        """Compute the current value based on elapsed time."""
        return self._gen._compute_value(elapsed_s)

    def next_seq(self) -> int:
        self._seq = (self._seq + 1) % 65536
        return self._seq


class SyntheticTransport(Transport):
    """Transport that generates traffic from .gen.yaml pattern configs in real-time.

    CR-SYNTHETIC-LIVE: Plugs into the same transport slot as MQTT or ReplayTransport.
    The bus dispatch path handles it identically to live traffic.
    """

    def __init__(
        self,
        config: str | Path,
        speed: float = 1.0,
        loop: bool = False,
        src_prefix: str = "",
    ):
        self._config_path = Path(config)
        self._speed = speed
        self._loop = loop
        self._src_prefix = src_prefix

        self._subscribers: dict[str, list[Callable[[BusMessage], None]]] = defaultdict(list)
        self._all_subscribers: list[Callable[[BusMessage], None]] = []

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False

        self._live_namespaces: list[_LiveNamespace] = []
        self._total_duration_s: float = 60.0

    # --- Transport interface ---

    def start(self):
        """Load config and begin real-time generation thread."""
        if self._thread is not None:
            return

        if not self._config_path.exists():
            logger.error(f"Synthetic config not found: {self._config_path}")
            return

        # Load config
        with open(self._config_path, "r") as f:
            config = yaml.safe_load(f)

        duration_str = config.get("duration", "60s")
        self._total_duration_s = _parse_duration(str(duration_str))

        # Build live namespace generators
        self._live_namespaces = []
        for ns_cfg in config.get("namespaces", []):
            self._live_namespaces.append(_LiveNamespace(ns_cfg, self._total_duration_s))

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._generation_loop,
            name="synthetic-transport",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Synthetic transport started: {self._config_path} "
            f"(speed={self._speed}, loop={self._loop}, "
            f"{len(self._live_namespaces)} namespaces)"
        )

    def stop(self):
        """Stop generation."""
        self._stop_event.set()
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Synthetic transport stopped")

    def send(self, message: BusMessage):
        """No-op — synthetic transport is read-only."""
        logger.warning(
            f"send() called on SyntheticTransport (read-only), "
            f"dropping message to '{message.namespace}'"
        )

    def subscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Register namespace-specific callback."""
        self._subscribers[namespace].append(callback)

    def subscribe_all(self, callback: Callable[[BusMessage], None]):
        """Register wildcard callback (bus-level dispatch)."""
        self._all_subscribers.append(callback)

    def unsubscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        """Remove a namespace subscriber."""
        if namespace in self._subscribers:
            try:
                self._subscribers[namespace].remove(callback)
            except ValueError:
                pass

    @property
    def is_connected(self) -> bool:
        return self._running

    # --- Real-time generation engine ---

    def _generation_loop(self):
        """Main generation thread — compute and dispatch messages with real-time timing."""
        while not self._stop_event.is_set():
            try:
                self._generate_once()
            except Exception as e:
                logger.error(f"Synthetic generation error: {e}")
                self._running = False
                return

            if not self._loop or self._stop_event.is_set():
                break
            logger.info("Synthetic loop: restarting generation")

        self._running = False

    def _generate_once(self):
        """Run one pass of the generation duration."""
        # Build a schedule of (time_s, namespace_index) events
        schedule = []
        for i, lns in enumerate(self._live_namespaces):
            t = 0.0
            while t <= self._total_duration_s:
                schedule.append((t, i))
                t += lns.interval_s
                t = round(t, 6)

        # Sort by time
        schedule.sort(key=lambda x: x[0])

        if not schedule:
            return

        start_wall = time.monotonic()

        for idx, (event_time_s, ns_idx) in enumerate(schedule):
            if self._stop_event.is_set():
                return

            # Wait until it's time to dispatch this event
            if self._speed != 0.0:
                target_wall = start_wall + (event_time_s / self._speed)
                now = time.monotonic()
                wait = target_wall - now
                if wait > 0:
                    if self._stop_event.wait(timeout=wait):
                        return

            lns = self._live_namespaces[ns_idx]
            val = lns.compute_value(event_time_s)

            src = lns.src
            if self._src_prefix:
                src = f"{self._src_prefix}{src}"

            msg = BusMessage(
                namespace=lns.name,
                type=MessageType.STATE,
                value=val,
                source=src,
                scope=lns.scope,
                sequence=lns.next_seq(),
            )

            self._dispatch(msg)

    def _dispatch(self, msg: BusMessage):
        """Dispatch a message to namespace-specific and wildcard subscribers."""
        for cb in self._subscribers.get(msg.namespace, []):
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"Subscriber callback error for '{msg.namespace}': {e}")

        for cb in self._all_subscribers:
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"All-subscriber callback error: {e}")
