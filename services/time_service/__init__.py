"""time_service — a generic, game-agnostic ComposeBus service app that publishes
``time/base`` (raw-monotonic, epoch int, configurable tick, retained) and the idle
time bodies that realize it.

Spec: [SPEC:CXC-TIME-SERVICE] (platform-specs/79-composebus-time-service.lts).
Shaped like ``services/bus_storage`` and ``services/fingerprint_storage``.
"""

from __future__ import annotations

from .bodies import TimeBody
from .profile import IDLE_BODIES, RESERVED_BODIES, TimeProfile
from .service import TimeService
from . import topics

__all__ = [
    "TimeService",
    "TimeProfile",
    "TimeBody",
    "IDLE_BODIES",
    "RESERVED_BODIES",
    "topics",
]
