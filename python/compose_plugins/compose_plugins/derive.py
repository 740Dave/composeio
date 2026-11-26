"""Derive once: `compose-plugin build` and the class/manifest drift check
([CXC-PC-DERIVE-ONCE], including `bus-types-attribute`, r15).

The author writes the ``ComposePlugin`` class and a hand-written CORE manifest.
``compose-plugin build <package-dir>`` imports the class (on the AUTHOR'S
machine, never on a host before consent) and writes ``config_schema`` and
``bus`` into ``manifest.json``.

``bus_types`` (r15): a plug-in that declares bus traffic MUST define a
``bus_types: ClassVar[dict[str, "STATE" | "EVENT"]]`` covering EXACTLY its
``publishes`` and ``subscribes``. composebus is unchanged; the library reads
the attribute. The build refuses a missing or extra entry, loudly.

The drift check runs on EVERY enable: the class's derived ``config_schema``
and its ``(namespace, type)`` pairs must equal the manifest's, or the enable is
refused naming each differing path and both values.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from composebus.plugin import ComposePlugin

from .errors import PluginContractRefusal
from .manifest import BUS_TYPES_ALLOWED

MANIFEST_NAME = "manifest.json"


class BuildRefused(PluginContractRefusal):
    """``compose-plugin build`` cannot derive the manifest blocks from the class."""


class DriftRefused(PluginContractRefusal):
    """The class and the shipped manifest disagree; the enable is refused.
    ``diffs`` lists ``(path, manifest_value, class_value)``."""

    def __init__(self, domain: str, diffs: "list[tuple[str, Any, Any]]"):
        self.domain = domain
        self.diffs = diffs
        lines = "\n".join(f"  - {p}: manifest has {m!r}, class has {c!r}" for p, m, c in diffs)
        super().__init__(f"enable REFUSED for {domain!r}: class/manifest drift "
                         f"({len(diffs)} path(s)):\n{lines}")


def resolve_entry(entry: str) -> type:
    """``module:attr`` -> the object, which MUST be a ComposePlugin subclass.
    Imports the module: call only AFTER consent (host) or at build (author)."""
    module_name, _, attr = entry.partition(":")
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    if not (isinstance(obj, type) and issubclass(obj, ComposePlugin)):
        raise BuildRefused(f"run.entry {entry!r} resolves to {obj!r}, which is not a "
                           "ComposePlugin subclass ([CXC-PC-RUN])")
    return obj


def derive_bus(cls: type) -> "dict | None":
    """The ``bus`` block from ``publishes``/``subscribes`` + ``bus_types``;
    None when the class declares no traffic (the build then omits ``bus``)."""
    pubs = list(getattr(cls, "publishes", []) or [])
    subs = list(getattr(cls, "subscribes", []) or [])
    types = getattr(cls, "bus_types", None)
    if not pubs and not subs:
        if types:
            raise BuildRefused(f"{cls.__name__}.bus_types names {sorted(types)} but the "
                               "class declares no publishes or subscribes")
        return None
    if not isinstance(types, dict):
        raise BuildRefused(f"{cls.__name__} declares bus traffic but no bus_types "
                           "ClassVar (r15 bus-types-attribute: namespace -> STATE|EVENT)")
    declared = set(pubs) | set(subs)
    missing = sorted(declared - set(types))
    extra = sorted(set(types) - declared)
    bad = sorted(ns for ns, t in types.items() if t not in BUS_TYPES_ALLOWED)
    problems = []
    if missing:
        problems.append(f"no type for {missing}")
    if extra:
        problems.append(f"types for namespaces in neither publishes nor subscribes: {extra}")
    if bad:
        problems.append(f"type not STATE or EVENT for {bad}")
    if problems:
        raise BuildRefused(f"{cls.__name__}.bus_types REFUSED: " + "; ".join(problems))
    return {"publishes": [{"namespace": ns, "type": types[ns]} for ns in pubs],
            "subscribes": [{"namespace": ns, "type": types[ns]} for ns in subs]}


def derive_config_schema(cls: type) -> dict:
    """The JSON Schema of the class's ``Config`` (empty-object schema when the
    plug-in has no Config of its own)."""
    if cls.Config is ComposePlugin.Config:
        return {"properties": {}, "title": "Config", "type": "object"}
    return cls.Config.model_json_schema()


def manifest_bytes_from(core: dict, config_schema: dict, bus: "dict | None") -> bytes:
    """The shipped manifest: the core plus the generated ``config_schema`` and
    ``bus`` (previous generated values are replaced; ``bus`` omitted if None)."""
    out = {k: v for k, v in core.items() if k not in ("config_schema", "bus")}
    out["config_schema"] = config_schema
    if bus is not None:
        out["bus"] = bus
    return (json.dumps(out, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def build_manifest_bytes(core: dict, cls: type) -> bytes:
    """``manifest_bytes_from`` for an already-imported class (tests, tools)."""
    return manifest_bytes_from(core, derive_config_schema(cls), derive_bus(cls))


def _pairs(bus: "dict | None", key: str) -> "list[tuple[str, str]]":
    return sorted((e["namespace"], e["type"]) for e in (bus or {}).get(key, []))


def drift_from(manifest: dict, config_schema: "dict | None", bus: "dict | None",
               bus_error: "str | None" = None) -> "list[tuple[str, Any, Any]]":
    """Every path where a DERIVED declaration differs from the manifest. The
    absent ``bus`` block equals the empty declaration. The one comparison, used
    for an imported class and for a subprocess probe alike."""
    diffs: list[tuple[str, Any, Any]] = []
    ms, cs = manifest.get("config_schema") or {}, config_schema or {}
    if manifest.get("config_schema") != config_schema:
        for k in sorted(set(ms) | set(cs)):
            mv, cv = ms.get(k), cs.get(k)
            if mv == cv:
                continue
            if k == "properties" and isinstance(mv, dict) and isinstance(cv, dict):
                for prop in sorted(set(mv) | set(cv)):
                    if mv.get(prop) != cv.get(prop):
                        diffs.append((f"config_schema.properties.{prop}",
                                      mv.get(prop), cv.get(prop)))
            else:
                diffs.append((f"config_schema.{k}", mv, cv))
        if not diffs:
            diffs.append(("config_schema", manifest.get("config_schema"), config_schema))
    if bus_error is not None:
        diffs.append(("bus", manifest.get("bus"), f"<not derivable: {bus_error}>"))
        return diffs
    for key in ("publishes", "subscribes"):
        mp, cp = _pairs(manifest.get("bus"), key), _pairs(bus, key)
        if mp != cp:
            diffs.append((f"bus.{key}", mp, cp))
    return diffs


def drift(manifest: dict, cls: type) -> "list[tuple[str, Any, Any]]":
    """Drift between an imported class and the manifest."""
    try:
        bus, err = derive_bus(cls), None
    except BuildRefused as exc:
        bus, err = None, str(exc)
    return drift_from(manifest, derive_config_schema(cls), bus, err)


def check_drift(manifest: dict, cls: type) -> None:
    d = drift(manifest, cls)
    if d:
        raise DriftRefused(manifest.get("domain", "?"), d)


def build(package_dir: "str | Path") -> Path:
    """Run the build in ``package_dir`` (its root holds ``manifest.json``).

    The class is ALWAYS loaded in a fresh child process (the probe), so two
    builds in one process never share a module ([qa-w2 LOW 4])."""
    from .probe_client import run_probe
    root = Path(package_dir).resolve()
    mpath = root / MANIFEST_NAME
    core = json.loads(mpath.read_text(encoding="utf-8"))
    entry = core.get("run", {}).get("entry")
    if not isinstance(entry, str) or ":" not in entry:
        raise BuildRefused(f"{mpath}: run.entry {entry!r} is not 'module:Class'")
    v = run_probe(entry, [str(root)], {}, what="compose-plugin build")
    if v["resolve_error"]:
        raise BuildRefused(f"{mpath}: run.entry {entry!r} does not resolve: {v['resolve_error']}")
    if v["bus_error"]:
        raise BuildRefused(v["bus_error"])
    mpath.write_bytes(manifest_bytes_from(core, v["config_schema"], v["bus"]))
    return mpath


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="compose-plugin",
                                 description="compose-plugins author tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="generate config_schema and bus into manifest.json")
    b.add_argument("package_dir")
    args = ap.parse_args(argv)
    try:
        path = build(args.package_dir)
    except PluginContractRefusal as exc:
        print(f"compose-plugin build: {exc}", file=sys.stderr)
        return 1
    print(f"compose-plugin build: wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
