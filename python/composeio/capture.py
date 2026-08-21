"""Bus capture — tap a transport and record messages to .bustrace files.

CR-CAPTURE: Read-only bus participant that subscribes to all namespaces
on a transport and writes every received message to a .bustrace file.

The capture format is NDJSON with delta-time encoding. The header is
written on stop() since we need message_count and duration_ms.
"""

from __future__ import annotations
import gzip
import json
import logging
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from composeio.transport import Transport, BusMessage

logger = logging.getLogger("composeio.capture")


class BusCapture:
    """Tap a transport and record messages to a .bustrace file.

    Usage:
        capture = BusCapture(output="highway_drive.bustrace")
        capture.tap(bus, transport="mqtt")
        # ... time passes, traffic flows ...
        capture.stop()
    """

    def __init__(
        self,
        output: str | Path,
        namespaces: list[str] | None = None,
        flush_interval: int = 100,
        flush_seconds: float = 1.0,
    ):
        self._output = Path(output)
        self._filter_namespaces = set(namespaces) if namespaces else None
        self._flush_interval = flush_interval
        self._flush_seconds = flush_seconds

        self._running = False
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._message_count = 0
        self._namespaces_seen: set[str] = set()
        self._last_timestamp: float | None = None
        self._start_time: float | None = None
        self._transport_source: str = ""

        # Write to a temp file, then prepend header on stop()
        self._temp_file = None
        self._temp_path: Path | None = None
        self._last_flush_time: float = 0.0

    def tap(
        self,
        bus=None,
        transport: Transport | str | None = None,
        namespaces: list[str] | None = None,
    ):
        """Begin capturing messages from a transport.

        Args:
            bus: ComposeIOBus instance (used to resolve transport by name)
            transport: Transport instance or name string ("mqtt", "local", etc.)
            namespaces: Optional list of namespaces to filter (overrides init)

        CR-CAPTURE-SCOPE: Supports all-traffic, filtered, and multi-transport modes.
        """
        if namespaces:
            self._filter_namespaces = set(namespaces)

        # Resolve transport from bus if given as string
        resolved_transport = self._resolve_transport(bus, transport)
        if resolved_transport is None:
            logger.error("No transport to tap — provide a Transport instance or bus + name")
            return

        self._transport_source = self._describe_transport(resolved_transport)

        # Open temp file for message lines
        self._temp_path = self._output.with_suffix(self._output.suffix + ".tmp")
        use_gzip = self._output.suffix == ".gz" or self._output.name.endswith(".bustrace.gz")
        if use_gzip:
            self._temp_file = gzip.open(self._temp_path, "wt", encoding="utf-8")
        else:
            self._temp_file = open(self._temp_path, "w", encoding="utf-8")

        self._start_time = time.time()
        self._last_flush_time = self._start_time
        self._running = True

        # Subscribe to transport
        resolved_transport.subscribe_all(self._on_message)
        logger.info(f"Capture started: {self._output} (source={self._transport_source})")

    def _resolve_transport(self, bus, transport) -> Transport | None:
        """Resolve a transport instance from bus + name or direct instance."""
        if isinstance(transport, Transport):
            return transport

        if bus is None:
            return None

        if isinstance(transport, str):
            name = transport.lower()
            if name == "mqtt" and hasattr(bus, "_global"):
                return bus._global
            if name == "local" and hasattr(bus, "_local"):
                return bus._local
            if name == "remote" and hasattr(bus, "_remote"):
                return bus._remote
            logger.warning(f"Unknown transport name: {transport}")
            return None

        # Default: tap the global transport if available, else local
        if hasattr(bus, "_global") and bus._global is not None:
            return bus._global
        if hasattr(bus, "_local"):
            return bus._local
        return None

    def _describe_transport(self, transport: Transport) -> str:
        """Generate a source descriptor for the header."""
        cls_name = type(transport).__name__.lower()
        if "mqtt" in cls_name:
            broker = getattr(transport, "_broker", "unknown")
            port = getattr(transport, "_port", 1883)
            return f"mqtt:{broker}:{port}"
        if "uart" in cls_name:
            device = getattr(transport, "_device", "unknown")
            return f"uart:{device}"
        if "local" in cls_name:
            return "local"
        return cls_name

    def _on_message(self, msg: BusMessage):
        """Callback for each incoming message — record to buffer."""
        if not self._running:
            return

        # Namespace filtering
        if self._filter_namespaces and msg.namespace not in self._filter_namespaces:
            return

        now = time.time()

        with self._lock:
            # Calculate dt in milliseconds from previous message
            if self._last_timestamp is None:
                dt = 0
            else:
                dt = int((now - self._last_timestamp) * 1000)
                if dt < 0:
                    dt = 0
            self._last_timestamp = now

            # Build trace record
            record = {
                "dt": dt,
                "ns": msg.namespace,
                "type": msg.type.value,
                "val": msg.value,
                "src": msg.source,
                "scope": msg.scope,
                "seq": msg.sequence,
            }

            # Optional fields
            if msg.data.get("_ttl") is not None:
                record["ttl"] = msg.data["_ttl"]
            if msg.data.get("_priority") is not None:
                record["pri"] = msg.data["_priority"]
            if msg.data.get("_cid") is not None:
                record["cid"] = msg.data["_cid"]

            self._namespaces_seen.add(msg.namespace)
            self._message_count += 1

            line = json.dumps(record, separators=(",", ":"))
            self._buffer.append(line)

            # Flush if buffer is full or enough time has passed
            if (
                len(self._buffer) >= self._flush_interval
                or (now - self._last_flush_time) >= self._flush_seconds
            ):
                self._flush_buffer()

    def _flush_buffer(self):
        """Write buffered lines to temp file. Must be called with lock held."""
        if not self._buffer or self._temp_file is None:
            return
        for line in self._buffer:
            self._temp_file.write(line + "\n")
        self._temp_file.flush()
        self._buffer.clear()
        self._last_flush_time = time.time()

    def stop(self):
        """Stop capture, write header, finalize output file.

        CR-CAPTURE-TAP: On stop(), flush remaining buffer, compute header,
        write final file with header prepended.
        """
        if not self._running:
            return

        self._running = False
        end_time = time.time()

        with self._lock:
            # Flush remaining buffer
            self._flush_buffer()

        # Close temp file
        if self._temp_file:
            self._temp_file.close()
            self._temp_file = None

        # Compute header
        duration_ms = int((end_time - self._start_time) * 1000) if self._start_time else 0
        header = {
            "_header": True,
            "version": 1,
            "captured": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": self._transport_source,
            "duration_ms": duration_ms,
            "message_count": self._message_count,
            "namespaces": sorted(self._namespaces_seen),
        }

        # Write final file: header + message lines
        use_gzip = self._output.suffix == ".gz" or self._output.name.endswith(".bustrace.gz")

        if use_gzip:
            open_final = lambda p: gzip.open(p, "wt", encoding="utf-8")
            open_temp = lambda p: gzip.open(p, "rt", encoding="utf-8")
        else:
            open_final = lambda p: open(p, "w", encoding="utf-8")
            open_temp = lambda p: open(p, "r", encoding="utf-8")

        with open_final(self._output) as out:
            # Header first
            out.write(json.dumps(header, separators=(",", ":")) + "\n")
            # Copy message lines from temp file
            if self._temp_path and self._temp_path.exists():
                with open_temp(self._temp_path) as tmp:
                    for line in tmp:
                        out.write(line)

        # Clean up temp file
        if self._temp_path and self._temp_path.exists():
            self._temp_path.unlink()

        logger.info(
            f"Capture stopped: {self._output} "
            f"({self._message_count} messages, {duration_ms}ms, "
            f"{len(self._namespaces_seen)} namespaces)"
        )

    @property
    def message_count(self) -> int:
        """Number of messages captured so far."""
        return self._message_count

    @property
    def is_running(self) -> bool:
        """Whether capture is currently active."""
        return self._running
