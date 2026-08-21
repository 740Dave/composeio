"""Signal transforms — declarative input processing.

DP-CIO-22: Deadzone, threshold, scale, rate_limit, debounce, map
are YAML-configurable, not procedural code.

Transforms are composable — applied in declaration order.
"""

from __future__ import annotations
import time
from abc import ABC, abstractmethod
from typing import Any


class Transform(ABC):
    """Base class for signal transforms."""

    @abstractmethod
    def apply(self, value: Any) -> Any | None:
        """Apply transform. Returns None to suppress the value (filtered out)."""

    @staticmethod
    def from_config(config: dict) -> Transform:
        """Create a transform from YAML config dict."""
        if "deadzone" in config:
            return Deadzone(config["deadzone"])
        if "threshold" in config:
            return Threshold(config["threshold"])
        if "scale" in config:
            s = config["scale"]
            return Scale(s["in"], s["out"])
        if "rate_limit" in config:
            return RateLimit(config["rate_limit"])
        if "debounce" in config:
            return Debounce(config["debounce"])
        if "map" in config:
            return Map(config["map"])
        raise ValueError(f"Unknown transform config: {config}")


class TransformChain:
    """Composable chain of transforms applied in order."""

    def __init__(self, transforms: list[Transform] | None = None):
        self.transforms = transforms or []

    def apply(self, value: Any) -> Any | None:
        """Apply all transforms in order. Returns None if any transform filters the value."""
        result = value
        for t in self.transforms:
            result = t.apply(result)
            if result is None:
                return None
        return result

    @staticmethod
    def from_config(config_list: list[dict]) -> TransformChain:
        """Create a chain from YAML config list."""
        return TransformChain([Transform.from_config(c) for c in config_list])


class Deadzone(Transform):
    """Suppress values below a threshold (absolute value). DP-CIO-22."""

    def __init__(self, threshold: float):
        self.threshold = threshold

    def apply(self, value: Any) -> Any | None:
        if isinstance(value, (int, float)):
            if abs(value) < self.threshold:
                return 0.0  # Return zero, not None — deadzone centers the value
            return value
        return value


class Threshold(Transform):
    """Map continuous input to discrete events based on thresholds.

    Config is a list of {above: value, emit: label} dicts, checked highest-first.
    """

    def __init__(self, thresholds: list[dict]):
        # Sort descending by 'above' value so highest match wins
        self.thresholds = sorted(thresholds, key=lambda t: t["above"], reverse=True)

    def apply(self, value: Any) -> Any | None:
        if not isinstance(value, (int, float)):
            return value
        for t in self.thresholds:
            if value > t["above"]:
                return t["emit"]
        return None  # Below all thresholds — suppress


class Scale(Transform):
    """Linear scaling from input range to output range."""

    def __init__(self, in_range: list[float], out_range: list[float]):
        self.in_min, self.in_max = in_range
        self.out_min, self.out_max = out_range

    def apply(self, value: Any) -> Any | None:
        if not isinstance(value, (int, float)):
            return value
        # Clamp to input range
        clamped = max(self.in_min, min(self.in_max, value))
        # Linear interpolation
        normalized = (clamped - self.in_min) / (self.in_max - self.in_min)
        return self.out_min + normalized * (self.out_max - self.out_min)


class RateLimit(Transform):
    """Suppress values published faster than the specified rate.

    Rate specified as string like "10hz" or float in Hz.
    """

    def __init__(self, rate):
        if isinstance(rate, str) and rate.endswith("hz"):
            self.min_interval = 1.0 / float(rate[:-2])
        else:
            self.min_interval = 1.0 / float(rate)
        self._last_time: float = 0.0

    def apply(self, value: Any) -> Any | None:
        now = time.monotonic()
        if now - self._last_time < self.min_interval:
            return None  # Suppress — too fast
        self._last_time = now
        return value


class Debounce(Transform):
    """Require value to be stable for a duration before publishing.

    Duration specified as string like "50ms" or float in seconds.
    """

    def __init__(self, duration):
        if isinstance(duration, str) and duration.endswith("ms"):
            self.duration = float(duration[:-2]) / 1000.0
        elif isinstance(duration, str) and duration.endswith("s"):
            self.duration = float(duration[:-1])
        else:
            self.duration = float(duration)
        self._pending_value: Any = None
        self._pending_since: float = 0.0
        self._last_emitted: Any = None

    def apply(self, value: Any) -> Any | None:
        now = time.monotonic()
        if value != self._pending_value:
            # Value changed — start new debounce window
            self._pending_value = value
            self._pending_since = now
            return None
        # Value is stable — check if debounce period has elapsed
        if now - self._pending_since >= self.duration:
            if value != self._last_emitted:
                self._last_emitted = value
                return value
        return None


class Map(Transform):
    """Discrete value mapping. Input value → named output.

    Config is a dict: {0: "off", 1: "low", 2: "high"} or
    a list of {value: X, emit: Y} dicts.
    """

    def __init__(self, mapping):
        if isinstance(mapping, dict):
            self._map = {k: v for k, v in mapping.items()}
        elif isinstance(mapping, list):
            self._map = {entry["value"]: entry["emit"] for entry in mapping}
        else:
            raise ValueError(f"Invalid map config: {mapping}")

    def apply(self, value: Any) -> Any | None:
        # Try exact match first, then string coercion
        if value in self._map:
            return self._map[value]
        str_val = str(value)
        if str_val in self._map:
            return self._map[str_val]
        return None  # No mapping — suppress
