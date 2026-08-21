"""Hierarchical addressing + port-provider channel resolution
([CXC-HALD-TOPOLOGY] / [CXC-HALD-EXPANDER]).

A ``pin`` or ``bus`` is a PATH, not a flat number.  The grammar is a single
dotted hop:

    "qwiic0"        -> root bus, no provider
    "d5"            -> root pin, no provider
    "io.gpa3"       -> provider "io", channel "gpa3"
    "fanout.ch0"    -> provider "fanout", channel "ch0"
    "mux0.ch2"      -> provider "mux0", channel "ch2"

Only ONE hop is modelled here (the common case on the four real boards).
Chained providers (``sr0.bit5`` off another shift register) would nest the
path; that is a documented TODO for a later slice — the four reference boards
never chain, so the single-hop resolver covers the first slice fully.

This module is pure string/topology logic — no bus, no drivers.  The runtime
(:mod:`composeio.hal.runtime`) turns a resolved ``(provider, channel)`` into a
live read/write through the provider driver instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Address:
    """A resolved addressing path.  ``provider`` is None for a root address."""

    provider: Optional[str]
    channel: str          # the leaf (the raw pin/bus name when provider is None)
    raw: str              # the original path string, for messages + conflict keys

    @property
    def is_hierarchical(self) -> bool:
        return self.provider is not None


def parse_path(path: str) -> Address:
    """Parse a ``pin``/``bus`` path into an :class:`Address`.

    A single ``.`` splits ``provider.channel``; a bare token is a root
    address.  A trailing/leading dot or a multi-dot path is rejected (chained
    providers are a later slice — see the module docstring)."""
    if path is None:
        raise ValueError("addressing path is required")
    s = str(path)
    if "." not in s:
        return Address(provider=None, channel=s, raw=s)
    parts = s.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"unsupported addressing path {s!r}: exactly one "
            f"'provider.channel' hop is modelled in this slice "
            f"(chained providers are a documented TODO)")
    return Address(provider=parts[0], channel=parts[1], raw=s)


# --- Channel vocabularies per provider TYPE ---------------------------------
#
# A provider re-exposes N TYPED sub-channels ([CXC-HALD-EXPANDER]).  The
# validator ([CXC-HALD-VALIDATE] rule 3) needs (a) the set of legal channel
# names for a provider of capacity N and (b) membership tests.  Each entry
# maps a `provides:` key to a generator of the ordered channel names.

def _gpio_names(n: int) -> list[str]:
    """MCP23017-style GPIO: gpa0..gpa7, gpb0..gpb7 (up to 16)."""
    names = [f"gpa{i}" for i in range(8)] + [f"gpb{i}" for i in range(8)]
    return names[:n]


def _ch_names(n: int) -> list[str]:
    """Generic channel naming: ch0..ch{n-1} (mux / pwm / led / analog)."""
    return [f"ch{i}" for i in range(n)]


def _bit_names(n: int) -> list[str]:
    """Shift-register bit naming: bit0..bit{n-1}."""
    return [f"bit{i}" for i in range(n)]


# provides-key -> (channel-name generator)
CHANNEL_VOCAB = {
    "gpio": _gpio_names,
    "i2c_channel": _ch_names,
    "pwm_ch": _ch_names,
    "analog_ch": _ch_names,
    "led_ch": _ch_names,
    "in": _bit_names,
    "out": _bit_names,
}


def provider_channels(provides: dict[str, int]) -> list[str]:
    """The ordered legal channel names a provider exposes, across all its
    ``provides:`` typed groups."""
    out: list[str] = []
    for key, count in provides.items():
        gen = CHANNEL_VOCAB.get(key, _ch_names)
        out.extend(gen(int(count)))
    return out


def provider_capacity(provides: dict[str, int]) -> int:
    """Total sub-channels a provider offers."""
    return sum(int(v) for v in provides.values())
