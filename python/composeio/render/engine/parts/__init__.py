"""
Parts catalog + resolver — maker-project-flow design lock §4.

The `parts/` catalog holds multi-target part definitions (one def, N
consumers), resolved by the `part:` id used in a scene's `hookup.parts`.
Heavy per-part data (pin maps, Fritzing/KiCad refs, MCU hints) lives here so
the maker `.compose.yaml` stays a thin netlist of REFERENCES.

Consumers of this resolver:
  * S2 (`engine/hookup.py`)  — validates every `part:` + wire `<part>.<pin>`.
  * S3 (hookup SVG generator) — reads `pins` + `fritzing` for component art.
  * S4 (MCU config-emitter)   — reads `adc_pins` to map an ADC pin -> slot.

Catalog layout: this package's ``catalog/`` directory holds one or more
``*.yaml`` files, each a mapping of ``{part-id: definition}``.  All files are
merged; a part id defined in two files is a hard error.  ``COMPOSEIO_PARTS_DIR``
overrides the directory (a maker project may ship its own parts).

Public API (stable — S3/S4 import these):
    resolve_part(part_id)   -> PartDef            (raises PartNotFound)
    part_pins(part_id)      -> tuple[str, ...]    (canonical pin map)
    part_adc_pins(part_id)  -> tuple[str, ...]
    has_part(part_id)       -> bool
    part_ids()              -> list[str]          (sorted)
    load_catalog(path=None) -> dict[str, PartDef] (cached per path)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_CATALOG_DIRNAME = "catalog"
_DEFAULT_DIR = Path(__file__).resolve().parent / _CATALOG_DIRNAME

# Reserved keys lifted onto typed PartDef fields; everything else on a part
# passes through unchanged in ``.extra`` (forward-compat for new consumers).
_TYPED_KEYS = {"pins", "adc_pins", "fritzing", "kicad", "value", "description"}


class PartNotFound(KeyError):
    """A `part:` reference did not resolve in the catalog."""


class CatalogError(ValueError):
    """The catalog itself is malformed (bad file, duplicate id, bad shape)."""


@dataclass(frozen=True)
class PartDef:
    """A resolved catalog part — multi-target, one def / N consumers (§4)."""
    id: str
    pins: Tuple[str, ...]
    adc_pins: Tuple[str, ...] = ()
    fritzing: Optional[str] = None
    kicad: Optional[Dict[str, Any]] = None
    value: Optional[str] = None
    description: str = ""
    # Any catalog field not lifted above (fritzing_part, fritzing_note, ...).
    extra: Dict[str, Any] = field(default_factory=dict)

    def has_pin(self, pin: str) -> bool:
        return pin in self.pins

    def is_adc_pin(self, pin: str) -> bool:
        return pin in self.adc_pins


def _coerce_part(part_id: str, raw: Any) -> PartDef:
    if not isinstance(raw, dict):
        raise CatalogError(
            f"part '{part_id}': definition must be a mapping, got "
            f"{type(raw).__name__}")
    pins = raw.get("pins")
    if not isinstance(pins, (list, tuple)) or not pins:
        raise CatalogError(
            f"part '{part_id}': `pins` must be a non-empty list "
            f"(the canonical pin map)")
    pin_names = [str(p) for p in pins]
    if len(set(pin_names)) != len(pin_names):
        raise CatalogError(f"part '{part_id}': duplicate pin names in `pins`")

    adc = raw.get("adc_pins") or ()
    adc_names = tuple(str(p) for p in adc)
    for a in adc_names:
        if a not in pin_names:
            raise CatalogError(
                f"part '{part_id}': adc_pin '{a}' is not in `pins`")

    kicad = raw.get("kicad")
    if kicad is not None and not isinstance(kicad, dict):
        raise CatalogError(f"part '{part_id}': `kicad` must be a mapping")

    extra = {k: v for k, v in raw.items() if k not in _TYPED_KEYS}
    return PartDef(
        id=part_id,
        pins=tuple(pin_names),
        adc_pins=adc_names,
        fritzing=(str(raw["fritzing"]) if raw.get("fritzing") else None),
        kicad=kicad,
        value=(str(raw["value"]) if raw.get("value") is not None else None),
        description=str(raw.get("description", "")),
        extra=extra,
    )


# Cache keyed by resolved directory path so a test-supplied dir is independent
# of the shipped catalog.
_CACHE: Dict[str, Dict[str, PartDef]] = {}


def _resolve_dir(path: Optional[str]) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    env = os.environ.get("COMPOSEIO_PARTS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_DIR


def load_catalog(path: Optional[str] = None, *,
                 refresh: bool = False) -> Dict[str, PartDef]:
    """Load + merge every ``*.yaml`` under the catalog dir into {id: PartDef}.

    Cached per resolved directory.  ``refresh=True`` re-reads from disk.
    """
    directory = _resolve_dir(path)
    key = str(directory)
    if not refresh and key in _CACHE:
        return _CACHE[key]

    if not directory.is_dir():
        raise CatalogError(f"parts catalog dir not found: {directory}")

    merged: Dict[str, PartDef] = {}
    origin: Dict[str, str] = {}
    for yml in sorted(directory.glob("*.yaml")) + sorted(
            directory.glob("*.yml")):
        with open(yml, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        if doc is None:
            continue
        if not isinstance(doc, dict):
            raise CatalogError(
                f"catalog file {yml.name}: top level must be a mapping of "
                f"part-id -> definition")
        for part_id, raw in doc.items():
            pid = str(part_id)
            if pid in merged:
                raise CatalogError(
                    f"duplicate part id '{pid}' in {yml.name} "
                    f"(already defined in {origin[pid]})")
            merged[pid] = _coerce_part(pid, raw)
            origin[pid] = yml.name

    _CACHE[key] = merged
    return merged


def resolve_part(part_id: str, *, path: Optional[str] = None) -> PartDef:
    """Return the PartDef for ``part_id`` or raise :class:`PartNotFound`."""
    catalog = load_catalog(path)
    try:
        return catalog[part_id]
    except KeyError:
        raise PartNotFound(
            f"unknown part '{part_id}' — not in the catalog "
            f"(known: {', '.join(sorted(catalog)) or '<empty>'})") from None


def part_pins(part_id: str, *, path: Optional[str] = None) -> Tuple[str, ...]:
    """Canonical pin map for ``part_id``."""
    return resolve_part(part_id, path=path).pins


def part_adc_pins(part_id: str, *,
                  path: Optional[str] = None) -> Tuple[str, ...]:
    """ADC-capable pins for ``part_id`` (empty for non-MCU parts)."""
    return resolve_part(part_id, path=path).adc_pins


def has_part(part_id: str, *, path: Optional[str] = None) -> bool:
    return part_id in load_catalog(path)


def part_ids(*, path: Optional[str] = None) -> List[str]:
    return sorted(load_catalog(path))


__all__ = [
    "PartDef", "PartNotFound", "CatalogError",
    "load_catalog", "resolve_part", "part_pins", "part_adc_pins",
    "has_part", "part_ids",
]
