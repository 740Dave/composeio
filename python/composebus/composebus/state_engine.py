"""State engines — value domains that inputs cycle through.

DP-CIO-4: Input mechanics (toggle, momentary, hold_ramp) are separate from
state domains (binary, tri_level, stop_light, continuous, enum).
The state engine defines what values exist and how they transition.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class StateEngine(ABC):
    """Base class for all state engines."""

    @property
    @abstractmethod
    def engine_type(self) -> str:
        """Engine type identifier."""

    @property
    @abstractmethod
    def values(self) -> list:
        """Ordered list of valid values."""

    @property
    @abstractmethod
    def default(self) -> Any:
        """Default/initial value."""

    def __init__(self):
        self._current = self.default

    @property
    def current(self) -> Any:
        return self._current

    @current.setter
    def current(self, value: Any):
        if not self.is_valid(value):
            raise ValueError(f"Invalid value '{value}' for {self.engine_type} engine. Valid: {self.values}")
        self._current = value

    def is_valid(self, value: Any) -> bool:
        """Check if a value is valid for this engine."""
        return value in self.values

    def advance(self) -> Any:
        """Advance to the next state (for toggle input). Returns new value."""
        idx = self.values.index(self._current)
        self._current = self.values[(idx + 1) % len(self.values)]
        return self._current

    def reset(self):
        """Reset to default value."""
        self._current = self.default

    def set(self, value: Any) -> Any:
        """Set to a specific value. Returns the value."""
        self.current = value
        return self._current

    def to_dict(self) -> dict:
        """Serialize engine state."""
        return {
            "type": self.engine_type,
            "values": self.values,
            "default": self.default,
            "current": self._current,
        }

    @staticmethod
    def from_config(config) -> StateEngine:
        """Create a state engine from YAML config.

        Config can be:
        - A string: "binary", "tri_level", "stop_light", "continuous_0_255"
        - A dict: {"type": "enum", "values": [...], "default": "..."}
        """
        if isinstance(config, str):
            return _BUILTIN_ENGINES[config]()
        if isinstance(config, dict):
            engine_type = config["type"]
            if engine_type == "enum":
                return EnumEngine(
                    values=config["values"],
                    default=config.get("default", config["values"][0]),
                )
            if engine_type == "json":
                return JsonEngine(default=config.get("default"))
            return _BUILTIN_ENGINES[engine_type]()
        raise ValueError(f"Invalid state engine config: {config}")


class BinaryEngine(StateEngine):
    """Two states: off, on."""

    engine_type = "binary"
    values = ["off", "on"]
    default = "off"


class TriLevelEngine(StateEngine):
    """Three states: off, half, full."""

    engine_type = "tri_level"
    values = ["off", "half", "full"]
    default = "off"


class StopLightEngine(StateEngine):
    """Three states: green, yellow, red."""

    engine_type = "stop_light"
    values = ["green", "yellow", "red"]
    default = "green"


class _ContinuousEngineBase(StateEngine):
    """Numeric range 0..``full_scale`` for PWM/analog. Accepts int or float.

    The full scale is a CLASS attribute so the two continuous engines are one
    implementation with two domains, not two implementations that must be kept
    in sync.  ``is_valid`` is what makes a domain load-bearing: an out-of-domain
    publish RAISES rather than coercing, which is the whole reason a namespace
    declares an engine at all.
    """

    full_scale: int = 255

    def is_valid(self, value) -> bool:
        if isinstance(value, (int, float)):
            return 0 <= value <= self.full_scale
        return False

    def increment(self, step: int = 1) -> int:
        """Increment value, clamping to the engine's domain."""
        self._current = max(0, min(self.full_scale, self._current + step))
        return self._current

    def decrement(self, step: int = 1) -> int:
        """Decrement value, clamping to the engine's domain."""
        return self.increment(-step)


class ContinuousEngine(_ContinuousEngineBase):
    """Numeric range 0-255 for PWM/analog. Accepts int or float."""

    engine_type = "continuous_0_255"
    values = list(range(256))  # Nominal values; is_valid accepts any number in range
    default = 0
    full_scale = 255


class ContinuousPercentEngine(_ContinuousEngineBase):
    """Numeric range 0-100 — a PERCENTAGE of full scale.

    The engine for any quantity whose natural unit is "how much of the
    available range", rather than a hardware code that happens to be 8 bits
    wide.  Engine thrust is the founding case (David 2026-08-15: "the C3
    should only be publishing throttle percentages, not 0_255").

    ⛔ 0..100 IS A SUBSET OF 0..255, so a percentage published to a namespace
    still declared ``continuous_0_255`` VALIDATES CLEANLY and then renders at
    roughly 40% on every 0..255 gauge.  The engine declaration is therefore the
    only thing that distinguishes the two on the wire, and a namespace carrying
    percentages MUST declare this engine — see the migration ordering note in
    [SPEC:CXC-THROTTLE-PERCENT-BANDS].
    """

    engine_type = "continuous_0_100"
    values = list(range(101))  # Nominal; is_valid accepts any number in range
    default = 0
    full_scale = 100


class EnumEngine(StateEngine):
    """User-defined ordered list of named values."""

    engine_type = "enum"

    def __init__(self, values: list[str], default: str | None = None):
        self._values = values
        self._default = default or values[0]
        super().__init__()

    @property
    def values(self) -> list[str]:
        return self._values

    @property
    def default(self) -> str:
        return self._default


class JsonEngine(StateEngine):
    """Accepts any JSON-serializable value as state.

    BP-EXT-JSON: Structured data engine for namespaces carrying JSON objects —
    session lists, thread metadata, configuration snapshots, diagnostic reports.
    """

    engine_type = "json"

    def __init__(self, default=None):
        self._default = default
        self._values = []  # No fixed value list
        super().__init__()

    @property
    def values(self):
        return self._values

    @property
    def default(self):
        return self._default

    def is_valid(self, value):
        import json
        try:
            json.dumps(value)
            return True
        except (TypeError, ValueError):
            return False

    def advance(self):
        return self._current  # No meaningful next for JSON

    def set(self, value):
        """Set to a specific value. Override to skip the values-list check."""
        if not self.is_valid(value):
            raise ValueError(f"Value is not JSON-serializable: {value}")
        self._current = value
        return self._current


# Registry of built-in engines
_BUILTIN_ENGINES: dict[str, type[StateEngine]] = {
    "binary": BinaryEngine,
    "tri_level": TriLevelEngine,
    "stop_light": StopLightEngine,
    "continuous_0_255": ContinuousEngine,
    "continuous_0_100": ContinuousPercentEngine,
    "json": JsonEngine,
}
