"""Replay transport — plays .bustrace files as live bus traffic.

CR-REPLAY: ReplayTransport implements the standard Transport interface.
It reads an NDJSON .bustrace file and dispatches messages with timing,
behaving identically to a live transport from the bus's perspective.

Playback runs on a dedicated thread (like MQTT's network thread).
Consumers MUST handle callbacks from a non-main thread.
"""

from __future__ import annotations
import gzip
import json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from composeio.transport import Transport, BusMessage, MessageType

logger = logging.getLogger("composeio.transport.replay")


class ReplayTransport(Transport):
    """Transport that replays .bustrace files as live bus traffic.

    CR-REPLAY-TRANSPORT: Plugs into ComposeApp.connect_transport() and
    the bus dispatch path handles it identically to live traffic.
    """

    def __init__(
        self,
        file: str | Path,
        speed: float = 1.0,
        loop: bool = False,
        start_offset_ms: int = 0,
        src_prefix: str = "",
    ):
        self._file = Path(file)
        self._speed = speed
        self._loop = loop
        self._start_offset_ms = start_offset_ms
        self._src_prefix = src_prefix

        self._subscribers: dict[str, list[Callable[[BusMessage], None]]] = defaultdict(list)
        self._all_subscribers: list[Callable[[BusMessage], None]] = []

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._header: dict[str, Any] | None = None

    # --- Transport interface ---

    def start(self):
        """Open the trace file, parse header, begin playback thread."""
        if self._thread is not None:
            return  # Already started

        if not self._file.exists():
            logger.error(f"Trace file not found: {self._file}")
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._playback_loop,
            name="replay-transport",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Replay transport started: {self._file} (speed={self._speed}, loop={self._loop})")

    def stop(self):
        """Stop playback, close file."""
        self._stop_event.set()
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Replay transport stopped")

    def send(self, message: BusMessage):
        """No-op — replay is read-only. CR-REPLAY-INTERFACE."""
        logger.warning(
            f"send() called on ReplayTransport (read-only), "
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

    @property
    def header(self) -> dict[str, Any] | None:
        """The parsed header from the trace file, available after start()."""
        return self._header

    # --- Playback engine ---

    def _open_file(self):
        """Open trace file, handling gzip transparently. CR-C2."""
        path = self._file
        # Detect gzip by extension or magic bytes
        if path.suffix == ".gz" or path.name.endswith(".bustrace.gz"):
            return gzip.open(path, "rt", encoding="utf-8")
        # Also try magic bytes for .bustrace files that are actually gzipped
        try:
            with open(path, "rb") as f:
                magic = f.read(2)
            if magic == b"\x1f\x8b":
                return gzip.open(path, "rt", encoding="utf-8")
        except OSError:
            pass
        return open(path, "r", encoding="utf-8")

    def _playback_loop(self):
        """Main playback thread — reads lines and dispatches with timing."""
        while not self._stop_event.is_set():
            try:
                self._play_once()
            except Exception as e:
                logger.error(f"Replay playback error: {e}")
                self._running = False
                return

            if not self._loop or self._stop_event.is_set():
                break
            logger.info("Replay loop: restarting from beginning")

        self._running = False

    def _play_once(self):
        """Play through the trace file once."""
        fh = self._open_file()
        try:
            self._play_file(fh)
        finally:
            fh.close()

    def _play_file(self, fh):
        """Read and dispatch all lines from an open file handle."""
        accumulated_ms = 0
        past_offset = self._start_offset_ms == 0

        for line_no, raw_line in enumerate(fh, start=1):
            if self._stop_event.is_set():
                return

            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping malformed line {line_no}: {e}")
                continue

            # Handle header line
            if record.get("_header"):
                self._header = record
                logger.info(
                    f"Trace header: version={record.get('version')}, "
                    f"messages={record.get('message_count')}, "
                    f"duration={record.get('duration_ms')}ms"
                )
                continue

            dt = record.get("dt", 0)
            accumulated_ms += dt

            # Skip until we pass start_offset_ms
            if not past_offset:
                if accumulated_ms < self._start_offset_ms:
                    continue
                past_offset = True

            # Sleep for dt (adjusted by speed multiplier)
            if dt > 0 and self._speed != 0.0:
                sleep_s = (dt / 1000.0) / self._speed
                # Use stop_event.wait() so we can be interrupted
                if self._stop_event.wait(timeout=sleep_s):
                    return

            # Build BusMessage from trace record
            msg = self._record_to_message(record)
            if msg is None:
                continue

            # Dispatch to subscribers
            self._dispatch(msg)

    def _record_to_message(self, record: dict) -> BusMessage | None:
        """Convert a .bustrace NDJSON record to a BusMessage."""
        try:
            ns = record["ns"]
            msg_type = MessageType(record["type"])
            val = record.get("val")
            src = record.get("src", "")
            scope = record.get("scope", "auto")
            seq = record.get("seq", 0)

            # Apply src_prefix for distinguishing replayed traffic
            if self._src_prefix:
                src = f"{self._src_prefix}{src}"

            msg = BusMessage(
                namespace=ns,
                type=msg_type,
                value=val,
                source=src,
                scope=scope,
                sequence=seq,
            )

            # Map optional fields
            if msg_type == MessageType.EVENT:
                msg.event_type = val if isinstance(val, str) else ""
            if "pri" in record:
                msg.data["_priority"] = record["pri"]
            if "ttl" in record:
                msg.data["_ttl"] = record["ttl"]
            if "cid" in record:
                msg.data["_cid"] = record["cid"]

            return msg
        except (KeyError, ValueError) as e:
            logger.warning(f"Skipping invalid trace record: {e}")
            return None

    def _dispatch(self, msg: BusMessage):
        """Dispatch a message to namespace-specific and wildcard subscribers."""
        # Namespace-specific subscribers
        for cb in self._subscribers.get(msg.namespace, []):
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"Subscriber callback error for '{msg.namespace}': {e}")

        # Wildcard subscribers (bus-level dispatch)
        for cb in self._all_subscribers:
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"All-subscriber callback error: {e}")
