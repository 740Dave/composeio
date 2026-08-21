"""The driver registry — the ``driver`` join key ([CXC-HALD-DRIVER]).

``driver`` names the decoder/adaptor that gives a raw stream meaning.  This
registry maps a driver id -> a :class:`DriverSpec` (category + host-side
instantiator + self-description).  It is the narrow waist: N boards x M chips
collapses to N + M because a chip is ONE registry entry reused by every board.

The registry is OPEN ([CXC-HALD-DRIVER] "OPEN, authored registry"):
:func:`register_driver` adds a user-authored driver at runtime; a mined
ESPHome component or a data-descriptor lands here the same way the built ones
do.  :meth:`DriverRegistry.suggest` powers the validator's "did you mean"
([CXC-HALD-VALIDATE] rule 4).

Seeded set (the first-slice proof, per the spec status block "Suggested first
slice"):
  * ``pn532``  -> the built NFC driver (transactional)   [CXC-HALD-BUILT]
  * ``ws281x`` / ``wled`` -> the built WLED adaptor (actuator)
  * ``audio``  -> the built audio actuator
  * ``bme280`` -> a DATA-DESCRIPTOR driver (register map + DSL decode) — the
                  tier-(a) proof ([CXC-HALD-DRIVER-DIST])
  * ``mcp23017`` -> a PORT-PROVIDER (16 gpio sub-pins) — the provider proof
  * ``tca9548a`` -> an I2C mux port-provider (8 channels)
  * ``pca9685``  -> a PWM port-provider STUB (provider-level shared freq) —
                    enough to exercise [CXC-HALD-VALIDATE] rule 5
  * ``gpio`` / ``relay`` / ``display`` / ``x27168`` -> generic host drivers
    (a digital_in sensor, two actuators, a compound actuator-group)
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class DriverSpec:
    """A registry entry ([CXC-HALD-DRIVER]).

    ``instantiate(peripheral, ctx) -> instance`` performs the HOST-side wiring
    (subscribe/publish per role) and returns the live driver instance.
    ``category`` is the role shape the driver realizes; ``tier`` records the
    artifact tier ([CXC-HALD-DRIVER-DIST]) for honesty in ``describe``.
    """

    driver_id: str
    category: str                       # sensor|actuator|transactional|provider
    instantiate: Callable[[Any, Any], Any]
    tier: str = "generic"               # descriptor|builtin|provider|generic|vm
    provides_template: dict[str, int] = field(default_factory=dict)
    interface_kinds: frozenset = field(default_factory=frozenset)
    summary: str = ""

    def describe(self) -> dict:
        """Self-description for the Composer peripheral palette
        ([CXC-HALD-DRIVER] / [CXC-HALD-PALETTE])."""
        return {
            "driver": self.driver_id,
            "category": self.category,
            "tier": self.tier,
            "provides": dict(self.provides_template),
            "interface_kinds": sorted(self.interface_kinds),
            "summary": self.summary,
        }


class DriverRegistry:
    """An OPEN, driver-keyed registry ([CXC-HALD-DRIVER])."""

    def __init__(self) -> None:
        self._drivers: dict[str, DriverSpec] = {}

    def register(self, spec: DriverSpec) -> None:
        self._drivers[spec.driver_id] = spec

    def get(self, driver_id: str) -> Optional[DriverSpec]:
        return self._drivers.get(driver_id)

    def has(self, driver_id: str) -> bool:
        return driver_id in self._drivers

    def ids(self) -> list[str]:
        return sorted(self._drivers)

    def suggest(self, driver_id: str, n: int = 3) -> list[str]:
        """"did you mean" candidates for an unknown driver
        ([CXC-HALD-VALIDATE] rule 4)."""
        return difflib.get_close_matches(driver_id, self._drivers, n=n, cutoff=0.4)


# A module-level convenience so ``register_driver`` mutates the default
# registry an application gets from ``default_registry()``.
_DEFAULT: Optional[DriverRegistry] = None


def default_registry() -> DriverRegistry:
    """The default registry, seeded once with the first-slice drivers."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DriverRegistry()
        from . import drivers as _d
        _d.seed(_DEFAULT)
    return _DEFAULT


def register_driver(spec: DriverSpec) -> None:
    """Add a user-authored driver to the default registry ([CXC-HALD-DRIVER]
    "OPEN, authored registry")."""
    default_registry().register(spec)
