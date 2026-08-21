"""
Descriptor <-> catalog <-> hookup PIN PARITY.

Three artifacts in this repo name the same pins:

  1. a BOARD/PERIPHERAL DESCRIPTOR — `docs/board-bringup/*.descriptor.yaml`,
     the bring-up record, where a pin fact is backed by a probe or a datasheet
     page;
  2. a PARTS CATALOG entry — `render/engine/parts/catalog/*.yaml`, the pin map
     the netlist is validated against;
  3. a `hookup:` block — the netlist itself.

Nothing asserted they agree.  They drift, and the drift surfaces as a WRONG
JOINT: the descriptor records that GPIO4 reaches nothing on this board, the
catalog lists it, a netlist wires it, and the fault is found with a soldering
iron in hand.  This module is the assertion — the same shape as the wire-format
parity corpora, one source and N consumers.

THE LINK IS EXPLICIT.  A catalog part opts in with::

    esp32-c3-supermini:
      descriptor: esp32c3-supermini-can.descriptor.yaml

and the descriptor carries a MACHINE-READABLE contract (prose cannot be
checked, and prose is where every one of these facts previously lived)::

    pin_contract:
      complete: true            # `pins:` accounts for EVERY pad on the part
      source: "probed 2026-08-12 — the CAN bring-up pin scan"
      pins:
        GPIO0: "ADC1 CH0"
        ...
      reserved:                 # exists, but MUST NOT be wired, with the WHY
        GPIO2: "strapping pin — a pull changes boot mode"
      not_broken_out: [GPIO18]  # on the die, not on the header

⛔ `reserved:` means reserved BY THE SILICON OR THE BOARD (straps, console,
USB), never "in use by this project".  A project's own pin claims belong in
that scene's `must_not:`, because two scenes may legitimately claim the same
pin and only one of them is ever built.

WHY THIS IS NOT ENFORCED AT PARSE TIME.  A `.compose.yaml` is pulled by a
device at boot from `bus://assets/...`, where `docs/board-bringup/` does not
exist.  Parse-time checks stay artifact-internal; this one is a REPO check, run
by the test suite and by the hookup-guide generator.  Stated so nobody
concludes from a green boot that the parity was checked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .hookup import Hookup
from .parts import PartDef, load_catalog, resolve_part

_ENV_DIR = "COMPOSEIO_DESCRIPTOR_DIR"


@dataclass(frozen=True)
class DescriptorFinding:
    code: str
    severity: str          # "error" | "warning"
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.code} [{self.severity}] {self.where}: {self.message}"


@dataclass(frozen=True)
class PinContract:
    """The machine-readable pin half of a descriptor."""
    descriptor: str
    pins: Tuple[str, ...]
    roles: Dict[str, str] = field(default_factory=dict)
    reserved: Dict[str, str] = field(default_factory=dict)
    #: pins that EXIST but whose accessibility/behaviour is UNCONFIRMED.  Not
    #: reserved (wiring one is a legitimate choice) and not clean either — the
    #: third state a binary contract cannot express, found by mutating a real
    #: netlist onto GPIO10 and watching the check say nothing.
    caution: Dict[str, str] = field(default_factory=dict)
    not_broken_out: Tuple[str, ...] = ()
    complete: bool = False
    source: str = ""

    @classmethod
    def from_raw(cls, descriptor: str, raw: Dict[str, Any]) -> "PinContract":
        pins_raw = raw.get("pins") or {}
        if isinstance(pins_raw, dict):
            roles = {str(k): str(v) for k, v in pins_raw.items()}
            pins = tuple(roles)
        else:
            pins = tuple(str(p) for p in pins_raw)
            roles = {p: "" for p in pins}
        reserved = {str(k): str(v)
                    for k, v in (raw.get("reserved") or {}).items()}
        caution = {str(k): str(v)
                   for k, v in (raw.get("caution") or {}).items()}
        extra = tuple(p for p in (*reserved, *caution) if p not in pins)
        return cls(
            descriptor=descriptor,
            pins=pins + extra,
            roles=roles,
            reserved=reserved,
            caution=caution,
            not_broken_out=tuple(str(p) for p in (raw.get("not_broken_out") or ())),
            complete=bool(raw.get("complete", False)),
            source=str(raw.get("source", "")),
        )


class DescriptorError(ValueError):
    """A descriptor exists but its `pin_contract:` is unusable."""


def descriptor_dir() -> str:
    """`docs/board-bringup/` — env-overridable for tests and out-of-tree use."""
    env = os.environ.get(_ENV_DIR)
    if env:
        return str(Path(env).expanduser().resolve())
    here = Path(__file__).resolve()
    # python/composeio/render/engine/ -> repo root
    root = here.parents[4]
    return str(root / "docs" / "board-bringup")


def parts_with_descriptors(catalog_path: Optional[str] = None) -> Dict[str, str]:
    """{catalog part id: descriptor filename} for every part that opted in."""
    out: Dict[str, str] = {}
    for pid, pdef in load_catalog(catalog_path).items():
        desc = pdef.extra.get("descriptor")
        if desc:
            out[pid] = str(desc)
    return out


def load_pin_contract(descriptor: str) -> PinContract:
    """Read one descriptor's `pin_contract:`.

    A descriptor named by a catalog part and carrying NO contract is an error,
    not a skip: the link exists precisely to be checked, and a silent skip is
    how a parity check ends up asserting nothing.
    """
    path = Path(descriptor)
    if not path.is_absolute():
        path = Path(descriptor_dir()) / descriptor
    if not path.is_file():
        raise DescriptorError(f"descriptor not found: {path}")
    with open(path) as fh:
        raw = yaml.safe_load(fh) or {}
    contract = raw.get("pin_contract")
    if not isinstance(contract, dict) or not contract.get("pins"):
        raise DescriptorError(
            f"{path.name}: a catalog part links this descriptor but it carries "
            f"no machine-readable `pin_contract.pins` — prose pin facts cannot "
            f"be checked, and unchecked is how they drifted")
    return PinContract.from_raw(path.name, contract)


def _contracts_for(hookup: Hookup) -> Dict[str, PinContract]:
    """{hookup part instance id: contract} for every placed part that links one."""
    linked = parts_with_descriptors()
    out: Dict[str, PinContract] = {}
    for hp in hookup.parts:
        desc = linked.get(hp.part)
        if desc:
            out[hp.id] = load_pin_contract(desc)
    return out


def check_pin_parity(
        hookup: Hookup,
        contracts: Optional[Dict[str, PinContract]] = None,
) -> Tuple[DescriptorFinding, ...]:
    """Assert the netlist, the catalog and the descriptors name the same pins.

    *contracts* maps a hookup part INSTANCE id to its contract; omitted, it is
    resolved from the catalog's `descriptor:` links.
    """
    if contracts is None:
        contracts = _contracts_for(hookup)
    out: List[DescriptorFinding] = []

    used: Dict[str, List[str]] = {}
    for w in hookup.wires:
        for end in (w.frm, w.to):
            used.setdefault(end.part, []).append(end.pin)

    for part_instance, contract in sorted(contracts.items()):
        hp = hookup.part(part_instance)
        if hp is None:
            continue
        where = f"{part_instance} ({hp.part} <- {contract.descriptor})"
        catalog_pins = set(hp.definition.pins if hp.definition else ())

        # (1) descriptor -> catalog
        for pin in contract.pins:
            if catalog_pins and pin not in catalog_pins:
                out.append(DescriptorFinding(
                    "PARITY-DESCRIPTOR-PIN-UNKNOWN", "error", where,
                    f"the descriptor names pin '{pin}' but catalog part "
                    f"'{hp.part}' has no such pin — one of the two was "
                    f"renamed and the other was not"))

        # (2) catalog -> descriptor (only binding on a COMPLETE contract)
        undocumented = sorted(p for p in catalog_pins if p not in contract.pins)
        if undocumented:
            out.append(DescriptorFinding(
                "PARITY-CATALOG-PIN-UNDOCUMENTED",
                "error" if contract.complete else "warning", where,
                f"catalog pins {undocumented} appear in no descriptor pin "
                f"contract"
                + (" and the contract claims to be COMPLETE"
                   if contract.complete else
                   " (contract is partial — coverage not asserted)")))

        # (3) hookup -> descriptor
        for pin in sorted(set(used.get(part_instance, ()))):
            if pin in contract.reserved:
                out.append(DescriptorFinding(
                    "PARITY-PIN-RESERVED", "error", where,
                    f"the netlist wires '{pin}', which the descriptor reserves: "
                    f"{contract.reserved[pin]}"))
            elif pin in contract.not_broken_out:
                out.append(DescriptorFinding(
                    "PARITY-PIN-NOT-BROKEN-OUT", "error", where,
                    f"the netlist wires '{pin}', which the descriptor records "
                    f"as NOT BROKEN OUT on this board — there is no pad to "
                    f"solder to"))
            elif pin in contract.caution:
                out.append(DescriptorFinding(
                    "PARITY-PIN-CAUTION", "warning", where,
                    f"the netlist wires '{pin}', which the descriptor flags as "
                    f"UNCONFIRMED: {contract.caution[pin]}"))
            elif pin not in contract.pins:
                out.append(DescriptorFinding(
                    "PARITY-PIN-NOT-IN-DESCRIPTOR",
                    "error" if contract.complete else "warning", where,
                    f"the netlist wires '{pin}', which the descriptor does not "
                    f"account for"
                    + (f" (contract source: {contract.source})"
                       if contract.source else "")))
    return tuple(out)


def parity_report(hookup: Hookup) -> str:
    """A human line-per-finding report (used by the guide generator + CLI)."""
    findings = check_pin_parity(hookup)
    if not findings:
        return "pin parity: OK (netlist, catalog and descriptors agree)"
    return "\n".join(str(f) for f in findings)


__all__ = [
    "DescriptorFinding", "PinContract", "DescriptorError",
    "check_pin_parity", "descriptor_dir", "load_pin_contract",
    "parts_with_descriptors", "parity_report",
]
