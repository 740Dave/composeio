"""
The `relay_actuator:` block — a break-before-make relay-bank OUTPUT actuator.

The direct dual of the `audio_cover:` block (both are top-level actuator halves
of a maker-project `.compose.yaml`): where `audio_cover:` subscribes bus state
and drives a DY-SV5F over UART, `relay_actuator:` subscribes ONE semantic ENUM
namespace (e.g. ``office_fan/speed`` = off|low|medium|high) and drives a set of
relay GPIO pins through a value->pin state map, with a hard motor-safety
invariant: **break-before-make + dead-time** (every owned relay opens, a
dead-time elapses, then the single target relay closes — two speed relays are
NEVER closed at once).

This module PARSES + VALIDATES that block into a :class:`RelayActuator` the
parser hangs on ``Composition.relay_actuator``.  It is the Python-side mirror of
the on-device lowering in ``firmware/reactive_core/src/controller_config.cpp``
(``RelayActuatorSpec``); keep the two in step.  Validation matches the
sensor-descriptor style in :mod:`.hookup` — strict and additive.

Schema (top-level, sibling of ``audio_cover:``)::

    relay_actuator:
      subscribes: office_fan/speed     # the enum namespace this actuator drives
      active_high: true                # relay energizes on GPIO HIGH
      dead_time_ms: 80                 # break-before-make dead-time
      default: off                     # state asserted at boot
      pins: [mcu.GPIO32, mcu.GPIO33, mcu.GPIO25, mcu.GPIO26]   # ALL owned relays
      states:                          # enum value -> energized relay pin
        - {value: off,    pin: none}   # pin-less state: all relays open
        - {value: low,    pin: mcu.GPIO33}
        - {value: medium, pin: mcu.GPIO25}
        - {value: high,   pin: mcu.GPIO26}

Validation:
  * ``subscribes`` is a required namespace string.
  * ``pins`` is a non-empty list of GPIO tokens (``mcu.GPIOn`` / ``GPIOn`` / ``n``).
  * each ``states`` row has a ``value`` and a ``pin`` (``none``/absent -> all open);
    a state that energizes a pin NOT in ``pins`` is an error (break-before-make
    must own every pin it can close).
  * ``dead_time_ms`` >= 0; ``active_high`` is a bool; ``default`` (if given) names
    a declared state.

A scene WITHOUT a ``relay_actuator:`` block yields ``comp.relay_actuator is None``
and is byte-for-byte unchanged — this module is purely additive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

NONE_PIN = -1


class RelayActuatorError(ValueError):
    """The `relay_actuator:` block is malformed or references an unowned pin."""


def _enum_token(raw: Any) -> str:
    """Normalize an enum token, defending against the YAML 1.1 off/on footgun.

    An unquoted ``off`` / ``on`` in YAML parses to a Python ``bool`` (PyYAML),
    which an Intelligence composing the block will hit constantly.  The on-device
    yaml_min parser keeps them as the strings "off"/"on", so we map the bool back
    to match: ``False -> "off"``, ``True -> "on"``.  Everything else is
    ``str(raw).strip()``.
    """
    if isinstance(raw, bool):
        return "off" if raw is False else "on"
    return str(raw).strip()


def parse_gpio(token: Any) -> int:
    """``mcu.GPIO33`` / ``GPIO33`` / ``33`` -> 33; ``none`` / None / ``-1`` -> -1.

    Mirrors ``controller_parse_gpio`` in the firmware (a bare ``3V3`` rail or any
    non-numeric tail is NOT a pin).  Raises on a token that is neither.
    """
    if token is None:
        return NONE_PIN
    s = str(token).strip()
    if s == "" or s.lower() in ("none", "-1", "open"):
        return NONE_PIN
    idx = s.find("GPIO")
    if idx >= 0:
        tail = s[idx + 4:]
        if tail and tail[0].isdigit():
            num = ""
            for ch in tail:
                if ch.isdigit():
                    num += ch
                else:
                    break
            return int(num)
        raise RelayActuatorError(f"pin token {token!r}: no GPIO number")
    if s.isdigit():
        return int(s)
    raise RelayActuatorError(
        f"pin token {token!r} is not a GPIO (expected mcu.GPIOn / GPIOn / n / none)")


@dataclass(frozen=True)
class RelayState:
    """One value->pin row: enum ``value`` energizes GPIO ``pin`` (-1 = all open)."""
    value: str
    pin: int    # energized GPIO, or NONE_PIN (-1) for a pin-less state (off)


@dataclass(frozen=True)
class RelayActuator:
    """Parsed + validated `relay_actuator:` block."""
    subscribes: str                     # the subscribed enum namespace
    pins: Tuple[int, ...]               # ALL owned relay GPIOs (break opens all)
    states: Tuple[RelayState, ...]      # value -> energized pin
    active_high: bool = True
    dead_time_ms: int = 80
    default: str = "off"

    def pin_for(self, value: str) -> Optional[int]:
        """The energized pin for ``value`` (-1 = all open), or None if unknown."""
        for st in self.states:
            if st.value == value:
                return st.pin
        return None

    @property
    def values(self) -> Tuple[str, ...]:
        """The enum values this actuator maps (the state-engine domain)."""
        return tuple(st.value for st in self.states)


def parse_relay_actuator(block: Any) -> RelayActuator:
    """Parse + validate a raw `relay_actuator:` mapping into a :class:`RelayActuator`.

    Raises :class:`RelayActuatorError` on any malformed field or unowned pin.
    """
    if not isinstance(block, dict):
        raise RelayActuatorError(
            f"`relay_actuator` must be a mapping, got {type(block).__name__}")

    subscribes = str(block.get("subscribes", "")).strip()
    if not subscribes:
        raise RelayActuatorError(
            "relay_actuator: missing `subscribes:` namespace")

    raw_pins = block.get("pins", []) or []
    if not isinstance(raw_pins, list) or not raw_pins:
        raise RelayActuatorError(
            "relay_actuator: `pins:` must be a non-empty list of relay GPIOs")
    pins: List[int] = []
    for p in raw_pins:
        gp = parse_gpio(p)
        if gp < 0:
            raise RelayActuatorError(
                f"relay_actuator: owned pin {p!r} must be a real GPIO")
        pins.append(gp)

    raw_states = block.get("states", []) or []
    if not isinstance(raw_states, list) or not raw_states:
        raise RelayActuatorError(
            "relay_actuator: `states:` must be a non-empty value->pin list")
    states: List[RelayState] = []
    seen_values = set()
    for entry in raw_states:
        if not isinstance(entry, dict):
            raise RelayActuatorError(
                f"relay_actuator.states entry must be a mapping: {entry!r}")
        value = _enum_token(entry.get("value", ""))
        if not value:
            raise RelayActuatorError(
                f"relay_actuator.states entry needs a `value`: {entry!r}")
        if value in seen_values:
            raise RelayActuatorError(
                f"relay_actuator: duplicate state value '{value}'")
        seen_values.add(value)
        pin = parse_gpio(entry.get("pin"))
        if pin >= 0 and pin not in pins:
            raise RelayActuatorError(
                f"relay_actuator: state '{value}' energizes GPIO{pin}, which is "
                f"not one of the owned pins {pins} (break-before-make must own "
                f"every pin it can close)")
        states.append(RelayState(value=value, pin=pin))

    active_high = bool(block.get("active_high", True))

    dead_time_ms = block.get("dead_time_ms", 80)
    if not isinstance(dead_time_ms, int) or dead_time_ms < 0:
        raise RelayActuatorError(
            f"relay_actuator: dead_time_ms must be a non-negative int, "
            f"got {dead_time_ms!r}")

    default = _enum_token(block.get("default", "off")) or "off"
    if default not in seen_values:
        raise RelayActuatorError(
            f"relay_actuator: default '{default}' is not a declared state "
            f"({sorted(seen_values)})")

    return RelayActuator(
        subscribes=subscribes,
        pins=tuple(pins),
        states=tuple(states),
        active_high=active_high,
        dead_time_ms=dead_time_ms,
        default=default,
    )


__all__ = [
    "RelayActuator", "RelayState", "RelayActuatorError",
    "parse_relay_actuator", "parse_gpio", "NONE_PIN",
]
