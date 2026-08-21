"""storage_monitor — a DUMB disk-space SENSOR for skipper, minus its transport.

The service is an ordinary ComposeBus client that originates ONE semantic fact,
``skipper/storage/free_bytes`` — the number of free bytes on the monitored filesystem.
It does NOTHING else: no thresholds, no colors, no policy ([DX-STATE-ON-BUS] — publish
the fact, let leaves react). A downstream consumer, the launch bar, owns ALL reaction.

THE BUS IS INJECTED (``attach``), never constructed here — DP-CIO-6: transport is an
implementation detail, not an architectural decision. The connect is config-driven and
lives in :mod:`.__main__` (the deploy path), which builds a transport from a spec via
``composeio.transport_registry.build``. This module names no substrate and imports no
concrete Transport class, exactly like ``time_service/service.py``.

WHAT THIS BUILD COVERS:
  * ``skipper/storage/free_bytes`` at a CONFIGURABLE interval, DEFAULT 30 s (disk space
    changes slowly; a 1 Hz sensor would be wasteful), carrying an INTEGER byte count from
    ``shutil.disk_usage(path).free``.
  * a RETAINED last-value snapshot: published as a STATE message, which ComposeBus retains
    (``transport_mqtt.RETAIN_BY_TYPE[STATE] is True``), so a leaf joining mid-stream reads
    the current free-space at once instead of waiting up to a (slow) tick.
  * the ``json`` engine on the wire — a free-form byte count is NEVER a ``binary``/range
    engine; an odd value on a strict engine SIGABRTs the process (the engine-mismatch
    hazard time_service records).

OUT OF SCOPE (the leaf's concern, never this sensor's): thresholds, low-space warnings,
colors, launch-bar reaction. Named as a boundary, NOT built here.
"""

from __future__ import annotations

import shutil
import threading
from typing import Any, Callable

from . import config, topics


class StorageMonitor:
    """The skipper disk-space sensor. Give it a bus with ``publish_state`` (an in-process
    ``ComposeIOBus`` or a bridged one) and a path, and it publishes
    ``skipper/storage/free_bytes`` as a retained STATE at a configurable interval."""

    def __init__(
        self,
        path: str | None = None,
        interval_s: float | None = None,
        *,
        measure: Callable[[str], int] | None = None,
        source: str = config.SOURCE,
    ) -> None:
        self.path = path if path is not None else config.monitored_path()
        self.interval_s = interval_s if interval_s is not None else config.interval_s()
        # `measure` is injectable so a test can assert the publish path with a known,
        # deterministic byte count that does not depend on the host's real disk.
        self._measure = measure or self._default_measure
        self.source = source
        self._bus: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- measurement ----------------------------------------------------

    @staticmethod
    def _default_measure(path: str) -> int:
        """Free bytes on the filesystem holding ``path`` — a bare int
        (``shutil.disk_usage(path).free``)."""
        return int(shutil.disk_usage(path).free)

    def free_bytes(self) -> int:
        """The current free-bytes measurement for the monitored path, as an int."""
        return int(self._measure(self.path))

    # ---- transport ------------------------------------------------------

    def attach(self, bus: Any) -> None:
        """Inject the bus and declare the namespace with the ``json`` engine — a byte
        count is a free-form value, never a ``binary``/range engine, per the
        engine-mismatch hazard (an out-of-domain value on a strict engine SIGABRTs the
        whole process)."""
        self._bus = bus
        bus.ensure_namespace(topics.FREE_BYTES, "json")

    # ---- ticking --------------------------------------------------------

    def tick(self) -> int:
        """Measure once, publish ONE ``skipper/storage/free_bytes`` value (retained
        STATE), and return it."""
        if self._bus is None:
            raise RuntimeError("attach(bus) before tick()")
        value = self.free_bytes()
        self._bus.publish_state(topics.FREE_BYTES, value, source=self.source)
        return value

    def start(self) -> None:
        """Publish an IMMEDIATE snapshot so the retained value is present at once, then
        run the periodic measurement in a daemon thread."""
        if self._bus is None:
            raise RuntimeError("attach(bus) before start()")
        # Immediate snapshot: a subscriber present at start must not wait a whole
        # (30 s) interval for the first value.
        self.tick()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="storage_monitor-tick", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        # `Event.wait(interval)` returns True only when stop() is signalled, so the loop
        # ticks every interval and exits promptly on stop — no busy-wait.
        while not self._stop.wait(self.interval_s):
            self.tick()

    def stop(self) -> None:
        """Signal the measurement loop to stop and join it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
