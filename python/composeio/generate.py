"""Trace generator — reads .gen.yaml configs and produces .bustrace files.

CR-SYNTHETIC: Generates synthetic .bustrace files from pattern descriptions.
All computation is offline and deterministic — no bus connection needed.

Supports all pattern types from CR-SYNTHETIC-PATTERNS:
constant, toggle, cycle, ramp, sine, triangle, random, sequence, noise
"""

from __future__ import annotations
import gzip
import json
import logging
import math
import random as rng_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("composeio.generate")

# State engine value tables (mirrors state_engine.py)
_ENGINE_VALUES = {
    "binary": ["off", "on"],
    "tri_level": ["off", "half", "full"],
    "stop_light": ["green", "yellow", "red"],
}


def _parse_duration(s: str) -> float:
    """Parse a duration string like '60s', '5m', '1h' to seconds."""
    s = s.strip()
    if s.endswith("ms"):
        return float(s[:-2]) / 1000.0
    if s.endswith("s"):
        return float(s[:-1])
    if s.endswith("m"):
        return float(s[:-1]) * 60.0
    if s.endswith("h"):
        return float(s[:-1]) * 3600.0
    return float(s)


def _parse_rate(s: str | int | float) -> float:
    """Parse a publish rate like '10hz', '0.5hz', or a number (as Hz)."""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().lower()
    if s.endswith("hz"):
        return float(s[:-2])
    return float(s)


def _get_engine_values(engine_name: str, config: dict | None = None) -> list[str]:
    """Get the value list for a state engine."""
    if engine_name in _ENGINE_VALUES:
        return _ENGINE_VALUES[engine_name]
    if engine_name == "enum" and config and "values" in config:
        return config["values"]
    if engine_name in ("continuous_0_255", "continuous_0_100"):
        return []  # Continuous — values computed by pattern
    return []


class PatternGenerator:
    """Generates a sequence of (time_s, value) pairs for one namespace."""

    def __init__(self, ns_config: dict, total_duration_s: float):
        self.name = ns_config["name"]
        self.engine = ns_config.get("state_engine", "binary")
        self.pattern = ns_config.get("pattern", {})
        self.pattern_type = self.pattern.get("type", "constant")
        self.total_duration_s = total_duration_s
        self.src = ns_config.get("src", f"gen-{self.name}")
        self.scope = ns_config.get("scope", "global")

        # Publish rate
        rate_str = ns_config.get("publish_rate", "1hz")
        self.rate_hz = _parse_rate(rate_str)
        self.interval_s = 1.0 / self.rate_hz if self.rate_hz > 0 else total_duration_s

        # Engine values for discrete patterns
        self.engine_values = _get_engine_values(self.engine, ns_config)

        # RNG for reproducible random patterns
        seed = ns_config.get("seed", hash(self.name) & 0xFFFFFFFF)
        self.rng = rng_module.Random(seed)

    def generate(self) -> list[dict]:
        """Generate all (time_s, record_dict) pairs for this namespace."""
        messages = []
        t = 0.0

        while t <= self.total_duration_s:
            val = self._compute_value(t)
            messages.append({
                "time_s": t,
                "ns": self.name,
                "type": "state",
                "val": val,
                "src": self.src,
                "scope": self.scope,
            })
            t += self.interval_s
            # Avoid floating-point drift accumulation
            t = round(t, 6)

        return messages

    def _compute_value(self, t: float) -> Any:
        """Compute the value at time t based on pattern type."""
        ptype = self.pattern_type

        if ptype == "constant":
            return self.pattern.get("value", self.engine_values[0] if self.engine_values else 0)

        if ptype in ("toggle", "cycle"):
            return self._pattern_cycle(t)

        if ptype == "ramp":
            return self._pattern_ramp(t)

        if ptype == "sine":
            return self._pattern_sine(t)

        if ptype == "triangle":
            return self._pattern_triangle(t)

        if ptype == "random":
            return self._pattern_random(t)

        if ptype == "sequence":
            return self._pattern_sequence(t)

        if ptype == "noise":
            return self._pattern_noise(t)

        logger.warning(f"Unknown pattern type '{ptype}' for {self.name}, using constant")
        return 0

    def _pattern_cycle(self, t: float) -> Any:
        """Toggle/cycle through engine values at interval."""
        interval = _parse_duration(str(self.pattern.get("interval", "1s")))
        if interval <= 0:
            interval = 1.0
        values = self.engine_values
        if not values:
            return 0
        idx = int(t / interval) % len(values)
        return values[idx]

    def _pattern_ramp(self, t: float) -> Any:
        """Linear interpolation from start to end over duration."""
        start = float(self.pattern.get("from", 0))
        end = float(self.pattern.get("to", 255))
        duration_s = _parse_duration(str(self.pattern.get("duration", f"{self.total_duration_s}s")))
        if duration_s <= 0:
            return int(start)
        progress = min(t / duration_s, 1.0)
        val = start + (end - start) * progress
        return int(round(val))

    def _pattern_sine(self, t: float) -> Any:
        """Sinusoidal oscillation between min and max."""
        lo = float(self.pattern.get("min", 0))
        hi = float(self.pattern.get("max", 255))
        period_s = _parse_duration(str(self.pattern.get("period", "10s")))
        if period_s <= 0:
            period_s = 10.0
        mid = (lo + hi) / 2.0
        amp = (hi - lo) / 2.0
        val = mid + amp * math.sin(2 * math.pi * t / period_s)
        return int(round(val))

    def _pattern_triangle(self, t: float) -> Any:
        """Triangle wave between min and max."""
        lo = float(self.pattern.get("min", 0))
        hi = float(self.pattern.get("max", 255))
        period_s = _parse_duration(str(self.pattern.get("period", "10s")))
        if period_s <= 0:
            period_s = 10.0
        # Triangle wave: linearly ramp up for half-period, down for half-period
        phase = (t % period_s) / period_s
        if phase < 0.5:
            progress = phase * 2.0  # 0 -> 1 over first half
        else:
            progress = 2.0 - phase * 2.0  # 1 -> 0 over second half
        val = lo + (hi - lo) * progress
        return int(round(val))

    def _pattern_random(self, t: float) -> Any:
        """Random value in [min, max] at each evaluation."""
        lo = int(self.pattern.get("min", 0))
        hi = int(self.pattern.get("max", 255))
        return self.rng.randint(lo, hi)

    def _pattern_sequence(self, t: float) -> Any:
        """Step through explicit value list, looping."""
        values = self.pattern.get("values", [])
        if not values:
            return 0
        interval = _parse_duration(str(self.pattern.get("interval", "1s")))
        if interval <= 0:
            interval = 1.0
        idx = int(t / interval) % len(values)
        return values[idx]

    def _pattern_noise(self, t: float) -> Any:
        """Gaussian noise around center value."""
        center = float(self.pattern.get("center", 128))
        amplitude = float(self.pattern.get("amplitude", 20))
        val = self.rng.gauss(center, amplitude)
        # Clamp to 0-255 for continuous engines
        val = max(0, min(255, val))
        return int(round(val))


def generate_trace(
    config_path: str | Path,
    output_path: str | Path | None = None,
    duration_override: str | None = None,
) -> Path:
    """Generate a .bustrace file from a .gen.yaml config.

    CR-SYNTHETIC-CLI: Reads pattern config, computes all messages
    deterministically, writes NDJSON .bustrace file.

    Args:
        config_path: Path to .gen.yaml config file
        output_path: Path for output .bustrace file (default: same name, .bustrace ext)
        duration_override: Override duration from config (e.g. "5m")

    Returns:
        Path to the generated .bustrace file
    """
    config_path = Path(config_path)
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Determine output path
    if output_path is None:
        stem = config_path.stem
        if stem.endswith(".gen"):
            stem = stem[:-4]
        output_path = config_path.parent / f"{stem}.bustrace"
    output_path = Path(output_path)

    # Parse duration
    duration_str = duration_override or config.get("duration", "60s")
    total_duration_s = _parse_duration(str(duration_str))

    # Generate messages from all namespace configs
    all_messages = []
    ns_configs = config.get("namespaces", [])

    for ns_cfg in ns_configs:
        gen = PatternGenerator(ns_cfg, total_duration_s)
        all_messages.extend(gen.generate())

    # Sort by time
    all_messages.sort(key=lambda m: m["time_s"])

    # Convert to .bustrace format with dt encoding
    trace_lines = []
    prev_time_s = 0.0
    namespaces_seen = set()

    for msg in all_messages:
        t = msg["time_s"]
        dt_ms = int(round((t - prev_time_s) * 1000))
        if dt_ms < 0:
            dt_ms = 0
        prev_time_s = t

        record = {
            "dt": dt_ms,
            "ns": msg["ns"],
            "type": msg["type"],
            "val": msg["val"],
            "src": msg["src"],
            "scope": msg["scope"],
            "seq": 0,
        }
        trace_lines.append(json.dumps(record, separators=(",", ":")))
        namespaces_seen.add(msg["ns"])

    # Build header
    duration_ms = int(total_duration_s * 1000)
    header = {
        "_header": True,
        "version": 1,
        "captured": None,
        "source": None,
        "duration_ms": duration_ms,
        "message_count": len(trace_lines),
        "namespaces": sorted(namespaces_seen),
    }

    # Write output file (handle gzip)
    use_gzip = output_path.suffix == ".gz" or output_path.name.endswith(".bustrace.gz")
    if use_gzip:
        open_fn = lambda p: gzip.open(p, "wt", encoding="utf-8")
    else:
        open_fn = lambda p: open(p, "w", encoding="utf-8")

    with open_fn(output_path) as f:
        f.write(json.dumps(header, separators=(",", ":")) + "\n")
        for line in trace_lines:
            f.write(line + "\n")

    logger.info(
        f"Generated trace: {output_path} "
        f"({len(trace_lines)} messages, {duration_ms}ms, "
        f"{len(namespaces_seen)} namespaces)"
    )
    return output_path
