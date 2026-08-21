"""composeio.hal — the declarative Hardware-Abstraction runtime (host slice).

Spec: [SPEC:CXC-HAL-DECLARE] (32-composeux-hal-declaration.lts).

This package is the INPUT/OUTPUT dual of the render-widget surface: a widget
declares screen output; a ``peripheral:`` entry declares physical I/O
([CXC-HALD-INTENT]).  A peripheral is a bus peer — a SENSOR publishes, an
ACTUATOR subscribes, a TRANSACTIONAL device is commanded — and only semantic
state crosses the bus.

This first slice is HOST-side and platform-pure (no MCU firmware, no
game/BSG specifics).  It realizes:

    parse_board_profile   the ``peripherals:`` schema ([CXC-HALD-DECLARE])
    default_registry      the ``driver`` join key + seeded drivers
                          ([CXC-HALD-DRIVER] / [CXC-HALD-BUILT])
    validate              the five collision/allocation rules
                          ([CXC-HALD-VALIDATE])
    LeafRuleEngine        ``rules:`` on the internal bus, via the existing DSL
                          ([CXC-HALD-RULES])
    HalRuntime            per-peer instantiation + the internal<->ComposeBus
                          membrane ([CXC-HALD-BRIDGE])

Optional heavy deps (miniaudio, smbus2, paho-mqtt) are lazy — importing this
package needs only stdlib + pyyaml.

Prove the slice end-to-end (no hardware)::

    python3 -m composeio.hal --selftest
"""

from __future__ import annotations

from .model import (
    BoardProfile,
    Bridge,
    Interface,
    Peripheral,
    Rule,
)
from .parser import HalParseError, parse_board_profile
from .registry import (
    DriverRegistry,
    DriverSpec,
    default_registry,
    register_driver,
)
from .rules import LeafRuleEngine
from .runtime import HalRuntime
from .validator import HalValidationError, validate

__all__ = [
    "BoardProfile",
    "Bridge",
    "Interface",
    "Peripheral",
    "Rule",
    "parse_board_profile",
    "HalParseError",
    "DriverRegistry",
    "DriverSpec",
    "default_registry",
    "register_driver",
    "LeafRuleEngine",
    "HalRuntime",
    "validate",
    "HalValidationError",
]
