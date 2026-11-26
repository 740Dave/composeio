"""time_service — a generic, game-agnostic ComposeBus service app that publishes
``time/base`` (raw-monotonic, epoch int, configurable tick, retained) and the idle
time bodies that realize it.

Spec: [SPEC:CXC-TIME-SERVICE] (platform-specs/79-composebus-time-service.lts).
Shaped like ``services/bus_storage`` and ``services/fingerprint_service``.
"""

from __future__ import annotations

from .bodies import TimeBody
from .profile import IDLE_BODIES, RESERVED_BODIES, TimeProfile
from .service import TimeService
from . import topics

# NOTE: TimeBasePublisherPlugin (services/time_service/plugin.py) is intentionally
# NOT re-exported here. It depends on `composeio`, whereas this package's core
# (service/profile/bodies/topics) does not; keeping the plugin out of `__init__`
# preserves a `composeio`-free `import time_service`. The ComposeServer loader
# reaches it by its dotted ref `time_service.plugin:TimeBasePublisherPlugin`
# ([CXC-CSL-MANIFEST-SHAPE]), which imports the submodule directly.

__all__ = [
    "TimeService",
    "TimeProfile",
    "TimeBody",
    "IDLE_BODIES",
    "RESERVED_BODIES",
    "topics",
]
