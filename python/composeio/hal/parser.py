"""Parse a ``peripherals:`` board profile into the typed model
([CXC-HALD-DECLARE]).

Input is a dict (already-loaded YAML) or a YAML/JSON string or a path to a
``.yaml`` file.  Output is a :class:`~composeio.hal.model.BoardProfile`.  This
is a STRUCTURAL parse only — it normalizes shapes and rejects malformed
entries; the semantic collision/allocation checks live in
:mod:`composeio.hal.validator` ([CXC-HALD-VALIDATE]).

Two namespace forms are accepted and normalized to a single
``{logical_slot: bus_namespace}`` map:

    namespace: gauge/value                 -> {"value": "gauge/value"}
    namespaces: {temperature: env/temperature, humidity: env/humidity}

The single-form default slot is ``"value"``; for a transactional device the
canonical slots are ``cmd`` / ``status`` / ``result`` ([CXC-HALD-ROLE]).
"""

from __future__ import annotations

import os
from typing import Any

import yaml

from .model import (
    ASSURANCE_CLASSES,
    INTERFACE_KINDS,
    ROLES,
    BoardProfile,
    Bridge,
    Interface,
    Peripheral,
    Rule,
)


class HalParseError(ValueError):
    """A board profile is structurally malformed."""


def _as_dict(source: Any) -> dict:
    """Coerce dict / YAML-string / file-path into a profile dict."""
    if isinstance(source, dict):
        return source
    if isinstance(source, str):
        # A path to a file, else treat the string as inline YAML.
        if os.path.exists(source):
            with open(source, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
        else:
            loaded = yaml.safe_load(source)
        if not isinstance(loaded, dict):
            raise HalParseError(
                f"board profile must be a mapping, got {type(loaded).__name__}")
        return loaded
    raise HalParseError(
        f"board profile must be a dict, YAML string, or path — got "
        f"{type(source).__name__}")


def _parse_interface(entry_id: str, raw_iface: Any) -> Interface:
    """A peripheral's interface is EXACTLY ONE kind ([CXC-HALD-KINDS])."""
    if not isinstance(raw_iface, dict) or len(raw_iface) != 1:
        raise HalParseError(
            f"peripheral {entry_id!r}: interface must be a single "
            f"'{{kind: {{params}}}}' mapping, got {raw_iface!r}")
    kind, params = next(iter(raw_iface.items()))
    if kind not in INTERFACE_KINDS:
        raise HalParseError(
            f"peripheral {entry_id!r}: unknown interface kind {kind!r} — "
            f"the closed set is {sorted(INTERFACE_KINDS)}")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise HalParseError(
            f"peripheral {entry_id!r}: interface params for {kind!r} must be "
            f"a mapping, got {params!r}")
    return Interface(kind=kind, params=dict(params))


def _normalize_namespaces(entry: dict) -> dict[str, str]:
    """Fold ``namespace:`` / ``namespaces:`` into one logical->bus map."""
    if "namespaces" in entry and "namespace" in entry:
        raise HalParseError(
            f"peripheral {entry.get('id')!r}: use EITHER 'namespace' or "
            f"'namespaces', not both")
    if "namespaces" in entry:
        ns = entry["namespaces"]
        if not isinstance(ns, dict):
            raise HalParseError(
                f"peripheral {entry.get('id')!r}: 'namespaces' must be a "
                f"mapping of logical-slot -> bus namespace")
        return {str(k): str(v) for k, v in ns.items()}
    if "namespace" in entry:
        return {"value": str(entry["namespace"])}
    return {}


def _parse_peripheral(entry: Any) -> Peripheral:
    if not isinstance(entry, dict):
        raise HalParseError(f"peripheral entry must be a mapping, got {entry!r}")
    if "id" not in entry:
        raise HalParseError(f"peripheral entry missing 'id': {entry!r}")
    pid = str(entry["id"])
    if "interface" not in entry:
        raise HalParseError(f"peripheral {pid!r}: missing 'interface'")
    if "driver" not in entry:
        raise HalParseError(f"peripheral {pid!r}: missing 'driver'")

    iface = _parse_interface(pid, entry["interface"])

    role = entry.get("role")
    provides = entry.get("provides") or {}
    if provides and not isinstance(provides, dict):
        raise HalParseError(
            f"peripheral {pid!r}: 'provides' must be a mapping "
            f"(e.g. {{gpio: 16}})")
    # A port-provider has no role; a non-provider must declare one.
    if role is not None and role not in ROLES:
        raise HalParseError(
            f"peripheral {pid!r}: role {role!r} is not one of {sorted(ROLES)}")
    if role is None and not provides:
        raise HalParseError(
            f"peripheral {pid!r}: needs a 'role' (sensor/actuator/"
            f"transactional) unless it is a port-provider ('provides:')")

    assurance = entry.get("assurance")
    if assurance is not None and assurance not in ASSURANCE_CLASSES:
        raise HalParseError(
            f"peripheral {pid!r}: assurance {assurance!r} is not one of "
            f"{sorted(ASSURANCE_CLASSES)}")

    return Peripheral(
        id=pid,
        interface=iface,
        driver=str(entry["driver"]),
        on=str(entry.get("on", "host")),
        role=role,
        provides={str(k): int(v) for k, v in provides.items()},
        namespaces=_normalize_namespaces(entry),
        config=dict(entry.get("config") or {}),
        assurance=assurance,
        raw=dict(entry),
    )


def _parse_rules(raw_rules: Any) -> list[Rule]:
    if not raw_rules:
        return []
    if not isinstance(raw_rules, list):
        raise HalParseError("'rules' must be a list of {when, set} entries")
    rules: list[Rule] = []
    for r in raw_rules:
        if not isinstance(r, dict) or "when" not in r:
            raise HalParseError(f"rule missing 'when': {r!r}")
        sets = r.get("set") or {}
        if not isinstance(sets, dict):
            raise HalParseError(f"rule 'set' must be a mapping: {r!r}")
        rules.append(Rule(when=str(r["when"]), sets=dict(sets), raw=dict(r)))
    return rules


def _parse_bridge(raw_bridge: Any) -> Bridge | None:
    if not raw_bridge:
        return None
    if not isinstance(raw_bridge, dict):
        raise HalParseError("'bridge' must be a mapping")
    return Bridge(
        transport=raw_bridge.get("transport"),
        export=list(raw_bridge.get("export") or []),
        import_=list(raw_bridge.get("import") or []),
    )


def parse_board_profile(source: Any) -> BoardProfile:
    """Parse a board profile ([CXC-HALD-DECLARE]) from dict / YAML / path."""
    data = _as_dict(source)
    raw_peripherals = data.get("peripherals")
    if not isinstance(raw_peripherals, list):
        raise HalParseError("board profile needs a 'peripherals:' list")
    peripherals = [_parse_peripheral(e) for e in raw_peripherals]

    # Duplicate-id is a structural error (ids are the join key for addressing).
    seen: set[str] = set()
    for p in peripherals:
        if p.id in seen:
            raise HalParseError(f"duplicate peripheral id {p.id!r}")
        seen.add(p.id)

    return BoardProfile(
        peripherals=peripherals,
        rules=_parse_rules(data.get("rules")),
        bridge=_parse_bridge(data.get("bridge")),
    )
