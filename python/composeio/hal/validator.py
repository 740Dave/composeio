"""Collision & allocation validation — fail at PARSE, not on the bench
([CXC-HALD-VALIDATE]).

All five rules, each producing a clear message:

  (1) selector collision on a segment — a duplicate I2C ``addr`` on the SAME
      bus/mux-channel.  A mux RESOLVES it: the same addr on two mux channels
      is two DIFFERENT segments, so it is fine ([CXC-HALD-TOPOLOGY]).
  (2) resolved-pin conflict — two point-to-point peripherals on the same
      resolved pin, INCLUDING expander sub-pins (two things on ``io.gpa0``).
  (3) provider over-allocation — more sub-channels claimed than a provider
      ``provides:`` (or a claimed channel outside the provider's vocabulary).
  (4) selector-required / driver-unknown — a bus-shared kind missing its
      selector, or a ``driver`` absent from the registry (with "did you mean").
  (5) shared-provider frequency agreement — channels on a single-prescaler
      provider (PCA9685) must share one PWM frequency ([CXC-HALD-CONFIG]).

The check runs against the WHOLE profile so cross-entry conflicts surface.  It
raises :class:`HalValidationError` on the FIRST violation with an actionable
message — the payoff of declaring-before-building.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .model import SELECTOR_REQUIRED, BoardProfile, Peripheral
from .registry import DriverRegistry, default_registry
from .topology import parse_path, provider_capacity, provider_channels

# Kinds whose addressing is a POINT-TO-POINT pin (rule 2 domain).
_PIN_KINDS = frozenset({
    "analog", "digital_in", "pwm_out", "onewire", "single_wire", "ws281x",
})


class HalValidationError(ValueError):
    """A board profile has a wiring conflict caught at parse time
    ([CXC-HALD-VALIDATE])."""


def _iface_channels_claimed(p: Peripheral) -> list[tuple[str, str, str]]:
    """Every (provider_id, channel, why) this peripheral claims THROUGH a
    provider — from a pin path, an i2c bus path, or actuator_group coils."""
    claims: list[tuple[str, str, str]] = []
    iface = p.interface
    # A hierarchical pin (digital_in/analog/pwm_out/...).
    if iface.pin:
        addr = parse_path(iface.pin)
        if addr.provider:
            claims.append((addr.provider, addr.channel, f"{p.id}.pin"))
    # A hierarchical i2c bus (behind a mux).
    if iface.bus:
        addr = parse_path(iface.bus)
        if addr.provider:
            claims.append((addr.provider, addr.channel, f"{p.id}.bus"))
    # A compound actuator_group's coils, driven ``via`` a provider.
    if iface.kind == "actuator_group":
        via = iface.params.get("via")
        for coil in iface.params.get("coils", []) or []:
            if via:
                claims.append((str(via), str(coil), f"{p.id}.coil[{coil}]"))
    return claims


def validate(profile: BoardProfile,
             registry: DriverRegistry | None = None) -> None:
    """Validate *profile*; raise :class:`HalValidationError` on the first
    violation ([CXC-HALD-VALIDATE], all five rules)."""
    registry = registry or default_registry()
    providers = {p.id: p for p in profile.providers()}

    # -- Rule 4a: driver-unknown (with "did you mean") -----------------------
    for p in profile.peripherals:
        if not registry.has(p.driver):
            hint = registry.suggest(p.driver)
            did_you_mean = f" — did you mean {hint}?" if hint else ""
            raise HalValidationError(
                f"peripheral {p.id!r}: unknown driver {p.driver!r}{did_you_mean}")

    # -- Rule 4b: selector-required for bus-shared kinds ---------------------
    for p in profile.peripherals:
        sel_field = SELECTOR_REQUIRED.get(p.interface.kind)
        if sel_field and p.interface.params.get(sel_field) is None:
            raise HalValidationError(
                f"peripheral {p.id!r}: interface {p.interface.kind!r} requires "
                f"a {sel_field!r} selector (bus-shared kinds need a selector "
                f"per [CXC-HALD-TOPOLOGY])")

    # -- Rule 1: I2C selector collision on a SEGMENT -------------------------
    # segment = the resolved bus path; a mux channel is a distinct segment.
    seg_addr: dict[tuple[str, Any], str] = {}
    for p in profile.peripherals:
        if p.interface.kind != "i2c":
            continue
        bus = p.interface.bus or "i2c0"
        addr = p.interface.addr
        key = (bus, addr)
        if key in seg_addr:
            raise HalValidationError(
                f"I2C selector collision: {p.id!r} and {seg_addr[key]!r} both "
                f"claim addr {addr!r} on segment {bus!r} — put them on "
                f"different mux channels ([CXC-HALD-VALIDATE] rule 1)")
        seg_addr[key] = p.id

    # -- Rule 2: resolved-pin conflict (incl. expander sub-pins) -------------
    pin_owner: dict[str, str] = {}
    for p in profile.peripherals:
        if p.interface.kind not in _PIN_KINDS:
            continue
        pin = p.interface.pin
        if not pin:
            continue
        resolved = parse_path(pin).raw       # full path is the conflict key
        if resolved in pin_owner:
            raise HalValidationError(
                f"resolved-pin conflict: {p.id!r} and {pin_owner[resolved]!r} "
                f"both use pin {resolved!r} ([CXC-HALD-VALIDATE] rule 2)")
        pin_owner[resolved] = p.id

    # -- Rule 3: provider over-allocation ------------------------------------
    claimed: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in profile.peripherals:
        for prov_id, channel, why in _iface_channels_claimed(p):
            claimed[prov_id].append((channel, why))
    for prov_id, refs in claimed.items():
        prov = providers.get(prov_id)
        if prov is None:
            raise HalValidationError(
                f"addressing references unknown provider {prov_id!r} "
                f"(from {', '.join(w for _, w in refs)}) — declare a "
                f"'provides:' peripheral with id {prov_id!r}")
        legal = set(provider_channels(prov.provides))
        capacity = provider_capacity(prov.provides)
        distinct = {c for c, _ in refs}
        for channel, why in refs:
            if channel not in legal:
                raise HalValidationError(
                    f"provider over-allocation: {why} claims channel "
                    f"{channel!r} outside provider {prov_id!r}'s "
                    f"{prov.provides} ({sorted(legal)}) "
                    f"([CXC-HALD-VALIDATE] rule 3)")
        if len(distinct) > capacity:
            raise HalValidationError(
                f"provider over-allocation: provider {prov_id!r} offers "
                f"{capacity} channel(s) but {len(distinct)} distinct are "
                f"claimed ([CXC-HALD-VALIDATE] rule 3)")

    # -- Rule 5: shared-provider frequency agreement (PCA9685) ---------------
    # Channels on a single-prescaler provider must agree on freq_hz.  A
    # channel's freq comes from its own config, defaulting to the provider's.
    for prov_id, prov in providers.items():
        if prov.driver != "pca9685":
            continue
        prov_freq = prov.config.get("freq_hz")
        freqs: dict[Any, str] = {}
        if prov_freq is not None:
            freqs[prov_freq] = f"{prov_id}(provider)"
        for p in profile.peripherals:
            if p.interface.kind == "pwm_out" and p.interface.pin:
                addr = parse_path(p.interface.pin)
                if addr.provider != prov_id:
                    continue
                # A per-channel freq may be declared in the interface params
                # or the config block; either way it must agree with siblings.
                f = p.interface.params.get(
                    "freq_hz", p.config.get("freq_hz", prov_freq))
                if f is not None:
                    freqs[f] = p.id
        if len(freqs) > 1:
            detail = ", ".join(f"{who}={f}Hz" for f, who in freqs.items())
            raise HalValidationError(
                f"shared-provider frequency disagreement on {prov_id!r} "
                f"(PCA9685 has one prescaler): {detail} — chain a second "
                f"PCA9685 at another address ([CXC-HALD-VALIDATE] rule 5)")
