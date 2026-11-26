"""storage_monitor — a bare-Python ComposeBus service that measures free disk space on
buildhost and publishes it as a semantic state fact, ``buildhost/storage/free_bytes`` (an
integer byte count, retained, configurable cadence).

A DUMB SENSOR ([DX-STATE-ON-BUS]): it publishes the FACT and does nothing else — no
thresholds, no colors, no policy. A downstream consumer (the launch bar) owns all
reaction. Shaped like ``services/time_service`` (the reference publisher).
"""

from __future__ import annotations

from .service import StorageMonitor
from . import config, topics

__all__ = ["StorageMonitor", "config", "topics"]
