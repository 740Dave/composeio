"""The lifecycle operations ([CXC-PC-LIFECYCLE-STATES] to -UNINSTALL).

States: ``available`` -> (consent) ``installed`` -> ``enabled`` <-> ``disabled``
-> ``uninstalled``. Upgrade runs from ``installed`` or ``enabled`` (never
``disabled``). Every other (operation, state) pair is refused naming both.
Inside ``enabled`` the plug-in's health is ``running``, ``parked(<reason>)``,
``degraded(<reason>)``, ``stopped`` or ``failed(<reason>)``.

Consent-before-code: ``prepare_install``/``prepare_upgrade`` read the manifest
WITHOUT executing anything and render the consent content; ``install`` /
``upgrade`` re-read the package, verify ``manifest_sha256`` against the digest
that was shown, and ask the HOST's consent gate. Only ``enable`` imports the
plug-in (in-process level) or a child does (subprocess level).

v0.1 limits that bind every host: a manifest whose ``requirements`` is not
``[]`` is refused at install (r15); nothing ever auto-upgrades
([CXC-PC-LIFECYCLE-UPGRADE] (3)).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import config_errors
from .consent import (
    ConsentContent, ConsentStore, granted_set, make_record, render_consent, upgrade_widens,
)
from .derive import DriftRefused, check_drift, drift_from, resolve_entry
from .errors import ManifestRefused, PluginContractRefusal, Refusal
from .host import ConsentRequest, Host
from .manifest import ISOLATION_LEVELS, Manifest, validate_manifest
from .errors import ManifestShaMismatch
from .package import ManifestFile, read_manifest, verify_manifest_sha256
from .probe_client import run_probe
from .runner import InprocessPlugin, SubprocessPlugin
from .store import ConfigStore, InstallStore

STATES = ("available", "installed", "enabled", "disabled", "uninstalled")
ALLOWED: "dict[str, frozenset[str]]" = {
    "install": frozenset({"available", "uninstalled"}),
    "configure": frozenset({"installed", "enabled", "disabled"}),
    "enable": frozenset({"installed", "disabled"}),
    "disable": frozenset({"enabled"}),
    "upgrade": frozenset({"installed", "enabled"}),
    "uninstall": frozenset({"installed", "enabled", "disabled"}),
}
OPERATIONS = tuple(ALLOWED)

REQUIREMENTS_V01 = ("requirements are not installed in contract v0.1: installing a pin "
                    "also installs its transitive dependencies, which are code you never "
                    "consented to, and needs a package index; installing requirements "
                    "arrives in v0.2")


class TransitionRefused(PluginContractRefusal):
    def __init__(self, domain: str, state: str, operation: str):
        self.domain, self.state, self.operation = domain, state, operation
        allowed = sorted(ALLOWED[operation])
        super().__init__(f"{operation} REFUSED for {domain!r}: it is {state!r}; "
                         f"{operation} is allowed only from {allowed}")


class ConsentDenied(PluginContractRefusal):
    """The host's gate refused; the refusal is recorded and nothing changed."""


@dataclass(frozen=True)
class ConsentPrompt:
    kind: str
    source: str
    manifest: Manifest
    manifest_sha256: str
    content: ConsentContent
    runs_as: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


#: host-provided import packages ([CXC-PC-RUN] import-package-name-rule, r18)
HOST_PROVIDED_MODULES = ("composebus", "compose_plugins", "composeio")


def import_package(m: "Manifest | dict") -> str:
    """The top-level module of ``run.entry`` (the plug-in's import package)."""
    data = m.data if isinstance(m, Manifest) else m
    return data["run"]["entry"].split(":")[0].split(".")[0]


#: PROCESS-WIDE: in-process import package -> (id(manager), domain) holding it.
#: [CXC-PC-RUN] inprocess-collision (r19): module objects are never shared
#: between hosts or tenants; a second load is raised to subprocess instead.
_HOLDERS: "dict[str, tuple[int, str]]" = {}


def holder_of(package: str) -> "tuple[int, str] | None":
    return _HOLDERS.get(package)


def refuse_shadowing(m: Manifest, *, owned: "set[str]" = frozenset(),
                     wheel_package: "str | None" = None) -> None:
    """r18, binds every host: the import package may not shadow a host-provided
    package, a standard-library module, or any top-level module already loaded
    in the host (``owned`` = this same plug-in's current install). A package
    held by ANOTHER plug-in is not refused here: that collision is resolved by
    raising the level to subprocess (r19 inprocess-collision)."""
    top = import_package(m)
    reason = None
    if top in HOST_PROVIDED_MODULES:
        reason = "a host-provided package"
    elif top in sys.stdlib_module_names or top in sys.builtin_module_names:
        reason = "a standard-library module"
    elif top in sys.modules and top not in owned and top not in _HOLDERS:
        reason = "a module already loaded in the host process"
    if reason is not None:
        raise ManifestRefused([Refusal("run.entry", (
            f"import package {top!r} REFUSED: it would shadow {reason} "
            "([CXC-PC-RUN] import-package-name-rule)"))])
    if wheel_package is not None and wheel_package != top:
        raise ManifestRefused([Refusal("run.entry", (
            f"import package {top!r} REFUSED: the wheel's package directory is "
            f"{wheel_package!r}; they must be the same"))])


def _snapshot() -> "tuple[list, dict]":
    return list(sys.path), dict(sys.modules)


def _restore(snap: "tuple[list, dict]") -> None:
    """Put sys.path and sys.modules back EXACTLY as they were."""
    path, mods = snap
    sys.path[:] = path
    for name in [n for n in sys.modules if n not in mods]:
        del sys.modules[name]
    for name, mod in mods.items():
        if sys.modules.get(name) is not mod:
            sys.modules[name] = mod


def refuse_requirements(m: Manifest) -> None:
    """r15, binds every v0.1 host: requirements must be []."""
    reqs = m.data["requirements"]
    if reqs:
        raise ManifestRefused([Refusal(f"requirements[{i}]", f"{pin!r}: {REQUIREMENTS_V01}")
                               for i, pin in enumerate(reqs)])


class PluginManager:
    """The host-neutral manager. One per host instance."""

    def __init__(self, host: Host):
        if not isinstance(host, Host):
            raise TypeError("PluginManager needs a Host")
        self.host = host
        root = host.state_root
        self.installs = InstallStore(root / "installs.json")
        self.configs = ConfigStore(root / "config")
        self.consent = ConsentStore(root / "consent.jsonl")
        self._running: "dict[str, Any]" = {}
        self._status: "dict[str, str]" = {}
        #: count of plug-in objects constructed in THIS process (in-process
        #: level) or children spawned (subprocess level): NC5's canary
        self.instantiations = 0
        #: install records whose code this manager imported in-process
        self._loaded: "dict[str, dict]" = {}

    # ---- state ------------------------------------------------------------ #
    def state(self, domain: str) -> str:
        rec = self.installs.get(domain)
        return rec["state"] if rec else "available"

    def record(self, domain: str) -> "dict | None":
        return self.installs.get(domain)

    def status(self, domain: str) -> str:
        st = self.state(domain)
        if st != "enabled":
            return "stopped" if st in ("installed", "disabled") else st
        return self._status.get(domain, "stopped")

    def _check(self, op: str, domain: str) -> str:
        st = self.state(domain)
        if st not in ALLOWED[op]:
            raise TransitionRefused(domain, st, op)
        return st

    def _set_status(self, domain: str, status: str) -> None:
        self._status[domain] = status
        self.host.publish_status(domain, status)

    # ---- consent ---------------------------------------------------------- #
    def _read(self, source) -> "tuple[ManifestFile, Manifest]":
        mf = read_manifest(source)
        m = validate_manifest(mf.data, host=self.host.name,
                              section_validator=self.host.validate_section, package=mf.package)
        refuse_requirements(m)
        rec = self.installs.get(m.domain)
        owned = ({import_package(rec["manifest"])}
                 if rec and rec.get("state") in ("installed", "enabled", "disabled") else set())
        refuse_shadowing(m, owned=owned,
                         wheel_package=mf.package.import_package
                         if mf.package.kind == "wheel" else None)
        return mf, m

    def _granted(self, m: Manifest) -> "list[str]":
        return granted_set(m, host=self.host.name, render_host_section=self.host.render_section)

    def prepare_install(self, source) -> ConsentPrompt:
        mf, m = self._read(source)
        self._check("install", m.domain)
        runs_as = self.actual_level(m.data, m.domain)
        content = render_consent(m, host=self.host.name, runs_as=runs_as,
                                 render_host_section=self.host.render_section)
        return ConsentPrompt("install", str(source), m, mf.sha256, content, runs_as)

    def prepare_upgrade(self, source) -> ConsentPrompt:
        mf, m = self._read(source)
        self._check("upgrade", m.domain)
        rec = self.installs.get(m.domain)
        runs_as = self.actual_level(m.data, m.domain)
        content = render_consent(m, host=self.host.name, runs_as=runs_as,
                                 render_host_section=self.host.render_section,
                                 previous_granted=rec["granted"],
                                 previous_runs_as=rec["runs_as"])
        return ConsentPrompt("upgrade", str(source), m, mf.sha256, content, runs_as)

    def _gate(self, prompt: ConsentPrompt, operator, accepted) -> str:
        decision = self.host.consent_gate(ConsentRequest(
            kind=prompt.kind, domain=prompt.manifest.domain, version=prompt.manifest.version,
            manifest_sha256=prompt.manifest_sha256, content=prompt.content,
            operator=operator, accepted=accepted))
        self.consent.append(make_record(
            manifest=prompt.manifest, manifest_sha256=prompt.manifest_sha256,
            granted=prompt.content.granted, decided_by=decision.decided_by,
            host=self.host.name, decision=decision.decision))
        if decision.decision != "granted":
            raise ConsentDenied(f"{prompt.kind} of {prompt.manifest.domain!r} "
                                f"{prompt.manifest.version} REFUSED at the consent gate "
                                f"(decided_by {decision.decided_by!r}); nothing changed")
        return decision.decided_by

    def _fresh(self, kind: str, source, consented_sha256: str) -> ConsentPrompt:
        """Re-read the package and verify it is the one shown at consent; the
        prompt used from here on is built from THESE bytes."""
        prompt = (self.prepare_install if kind == "install" else self.prepare_upgrade)(source)
        verify_manifest_sha256(consented_sha256, read_manifest(source))
        if prompt.manifest_sha256 != consented_sha256:
            raise ManifestShaMismatch(expected=consented_sha256, actual=prompt.manifest_sha256,
                                      location=str(source))
        return prompt

    # ---- install ---------------------------------------------------------- #
    def install(self, source, *, consented_sha256: str, operator: "str | None",
                accepted: bool) -> dict:
        prompt = self._fresh("install", source, consented_sha256)
        self._gate(prompt, operator, accepted)
        m = prompt.manifest
        path = self._place_code(source, m.domain, prompt.manifest_sha256)
        rec = {"domain": m.domain, "version": m.version, "state": "installed",
               "manifest_sha256": prompt.manifest_sha256, "manifest": m.data,
               "granted": list(prompt.content.granted), "runs_as": prompt.runs_as,
               "consented_runs_as": prompt.runs_as,
               "path": str(path), "installed_at": _now()}
        self.installs.put(m.domain, rec)
        return rec

    def _place_code(self, source, domain: str, sha256: str) -> Path:
        """Copy (directory) or unpack (wheel, zipfile only) into a staging dir,
        then rename into ``<install_root>/<domain>``. Nothing is run."""
        root = self.host.install_root
        staging = root / f".staging-{domain}"
        target = root / domain
        if staging.exists():
            shutil.rmtree(staging)
        src = Path(str(source))
        try:
            if src.is_dir():
                shutil.copytree(src, staging, symlinks=False,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                staging.mkdir()
                base = staging.resolve()
                with zipfile.ZipFile(src) as zf:
                    for info in zf.infolist():
                        dest = (staging / info.filename).resolve()
                        if base not in dest.parents and dest != base:
                            raise PluginContractRefusal(
                                f"install REFUSED: wheel member {info.filename!r} "
                                "would land outside the install root")
                    zf.extractall(staging)
            # the placed copy must still be the consented manifest (TOCTOU)
            verify_manifest_sha256(sha256, read_manifest(
                staging if src.is_dir() else staging / _wheel_pkg(src)))
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return target

    def _code_root(self, rec: dict) -> Path:
        """The directory holding the plug-in's import package."""
        p = Path(rec["path"])
        top = rec["manifest"]["run"]["entry"].split(":")[0].split(".")[0]
        if (p / top).exists():
            return p
        raise PluginContractRefusal(f"installed code for {rec['domain']!r} has no "
                                    f"package {top!r} under {p}")

    # ---- config ----------------------------------------------------------- #
    def configure(self, domain: str, config: Any) -> None:
        self._check("configure", domain)
        self.configs.put(domain, config)

    # ---- enable / disable ------------------------------------------------- #
    def enable(self, domain: str) -> str:
        self._check("enable", domain)
        rec = self.installs.get(domain)
        level = self.actual_level(rec["manifest"], domain,
                                  consented=rec.get("consented_runs_as"))
        config = self.configs.get(domain)
        code_root = str(self._code_root(rec))
        if level == "inprocess":
            snap = _snapshot()                         # r18: enable is ATOMIC
            prev_loaded = self._loaded.get(domain)
            try:
                cls = self._import_inprocess(rec, code_root)
                self._loaded[domain] = rec
                _HOLDERS[import_package(rec["manifest"])] = self._key(domain)
                check_drift(rec["manifest"], cls)      # refuses; state stays put
                errs = config_errors(cls.Config, config)
            except BaseException:
                if prev_loaded is None:
                    self._loaded.pop(domain, None)
                    if _HOLDERS.get(import_package(rec["manifest"])) == self._key(domain):
                        del _HOLDERS[import_package(rec["manifest"])]
                else:
                    self._loaded[domain] = prev_loaded
                _restore(snap)
                raise
        else:
            probe = self._probe(rec, code_root, config)
            if probe["resolve_error"]:
                diffs = [("run.entry", rec["manifest"]["run"]["entry"],
                          f"<resolve: {probe['resolve_error']}>")]
                raise DriftRefused(domain, diffs)
            diffs = _probe_drift(rec["manifest"], probe)
            if diffs:
                raise DriftRefused(domain, diffs)
            errs = probe["config_errors"]
        rec["state"] = "enabled"
        rec["runs_as"] = level
        self.installs.put(domain, rec)
        if errs:
            self._set_status(domain, f"parked({'; '.join(errs)})")
            return self.status(domain)
        sink = lambda status, d=domain: self._set_status(d, status)  # noqa: E731
        if level == "inprocess":
            run = InprocessPlugin(cls, cls.Config(**config), self.host.bus(), sink)
        else:
            m = Manifest(rec["manifest"])
            run = SubprocessPlugin(domain=domain, entry=rec["manifest"]["run"]["entry"],
                                   sys_path=[code_root], config=config, bus=self.host.bus(),
                                   publishes=set(m.publishes), subscribes=set(m.subscribes),
                                   on_status=sink)
        self.instantiations += 1
        self._running[domain] = run
        run.start()
        return self.status(domain)

    def _import_inprocess(self, rec: dict, code_root: str) -> type:
        top = rec["manifest"]["run"]["entry"].split(":")[0].split(".")[0]
        if self.collision(rec["manifest"], rec["domain"]) is not None:
            # unreachable: actual_level raised it to subprocess. Never purge or
            # replace another host's modules.
            raise PluginContractRefusal(f"in-process load of {top!r} refused: held by "
                                        "another host ([CXC-PC-RUN] inprocess-collision)")
        for name in [n for n in sys.modules if n == top or n.startswith(top + ".")]:
            del sys.modules[name]          # an upgraded package must not reuse old code
        if code_root not in sys.path:
            sys.path.insert(0, code_root)
        try:
            return resolve_entry(rec["manifest"]["run"]["entry"])
        except PluginContractRefusal:
            raise
        except Exception as exc:
            raise DriftRefused(rec["domain"], [("run.entry", rec["manifest"]["run"]["entry"],
                                                f"<resolve: {type(exc).__name__}: {exc}>")])

    def _probe(self, rec: dict, code_root: str, config: Any) -> dict:
        """The verdict comes back on a dedicated fd with a nonce, never stdout."""
        return run_probe(rec["manifest"]["run"]["entry"], [code_root], config,
                         what=f"enable of {rec['domain']!r}")

    def tick(self) -> None:
        """A status tick: in-process plug-ins are asked ``health_reason()``."""
        for run in list(self._running.values()):
            run.tick()

    def _stop(self, domain: str) -> None:
        run = self._running.pop(domain, None)
        if run is not None:
            run.stop()
            if isinstance(run, InprocessPlugin):
                rec = self.installs.get(domain)
                if rec:
                    code_root = str(Path(rec["path"]))
                    if code_root in sys.path:
                        sys.path.remove(code_root)

    def disable(self, domain: str) -> str:
        self._check("disable", domain)
        self._stop(domain)
        rec = self.installs.get(domain)
        rec["state"] = "disabled"
        self.installs.put(domain, rec)
        self._status.pop(domain, None)
        return self.status(domain)

    def running(self, domain: str):
        return self._running.get(domain)

    # ---- upgrade (MANUAL only) -------------------------------------------- #
    def upgrade(self, source, *, consented_sha256: str, operator: "str | None",
                accepted: bool) -> dict:
        prompt = self._fresh("upgrade", source, consented_sha256)
        m = prompt.manifest
        old = self.installs.get(m.domain)
        was = old["state"]
        if upgrade_widens(old["granted"], prompt.content.granted, old["runs_as"],
                          prompt.runs_as):
            self._gate(prompt, operator, accepted)   # refused -> ConsentDenied, no change
        if was == "enabled":
            self._stop(m.domain)
        path = self._place_code(source, m.domain, prompt.manifest_sha256)
        rec = dict(old, version=m.version, manifest_sha256=prompt.manifest_sha256,
                   manifest=m.data, granted=list(prompt.content.granted),
                   runs_as=prompt.runs_as, consented_runs_as=prompt.runs_as,
                   path=str(path), upgraded_at=_now(),
                   state="installed")
        self.installs.put(m.domain, rec)     # stored config is KEPT, never rewritten
        if was == "enabled":
            self.enable(m.domain)            # re-validated; invalid config parks
        return self.installs.get(m.domain)

    # ---- uninstall -------------------------------------------------------- #
    def uninstall(self, domain: str) -> dict:
        self._check("uninstall", domain)
        self._stop(domain)
        rec = self.installs.get(domain)
        bus = self.host.bus()
        if bus is not None:
            for ns, typ in Manifest(rec["manifest"]).publishes:
                if typ == "STATE":
                    bus.publish_state(ns, None, source=f"host:{self.host.name}")  # tombstone
        target = Path(rec["path"])
        if target.exists():
            shutil.rmtree(target)
        self._purge(rec)                         # no in-process module outlives its code
        self._loaded.pop(domain, None)
        self.configs.remove(domain)
        self._status.pop(domain, None)
        self.installs.put(domain, {"domain": domain, "state": "uninstalled",
                                   "uninstalled_at": _now(), "last_version": rec["version"]})
        return self.installs.get(domain)

    def close(self) -> None:
        """Release this host: stop every plug-in and purge every module it
        imported in-process, so the process holds nothing of them."""
        for d in list(self._running):
            self._stop(d)
        for rec in list(self._loaded.values()):
            self._purge(rec)
        self._loaded.clear()

    # ---- in-process collisions (r19) -------------------------------------- #
    def _key(self, domain: str) -> "tuple[int, str]":
        return (id(self), domain)

    def collision(self, manifest_data: dict, domain: str) -> "tuple[int, str] | None":
        """The holder of this plug-in's import package, if it is held by ANOTHER
        host, tenant or plug-in in this process; None otherwise."""
        h = _HOLDERS.get(import_package(manifest_data))
        return h if h is not None and h != self._key(domain) else None

    def actual_level(self, manifest_data: dict, domain: str,
                     consented: "str | None" = None) -> str:
        """The level this host will ACTUALLY use: its choice (>= the floor),
        never below the level consent showed, and RAISED to subprocess on an
        in-process collision. A host without subprocess support refuses."""
        level = self.host.isolation_level(manifest_data.get("isolation_floor", "inprocess"))
        if consented in ISOLATION_LEVELS and \
                ISOLATION_LEVELS.index(consented) > ISOLATION_LEVELS.index(level):
            level = consented
        if level == "inprocess":
            holder = self.collision(manifest_data, domain)
            if holder is not None:
                if "subprocess" not in self.host.isolation_levels:
                    raise PluginContractRefusal(
                        f"{domain!r} REFUSED: its import package "
                        f"{import_package(manifest_data)!r} is already loaded in this "
                        f"process by plug-in {holder[1]!r} of another host, and host "
                        f"{self.host.name!r} cannot run it at the subprocess level "
                        "([CXC-PC-RUN] inprocess-collision)")
                level = "subprocess"
        return level

    def _purge(self, rec: dict) -> None:
        """Purge a plug-in's modules ONLY if this host holds them: never another
        host's modules."""
        top = import_package(rec["manifest"])
        if _HOLDERS.get(top) != self._key(rec["domain"]):
            return
        _purge_modules(rec)
        del _HOLDERS[top]

    def has_install(self, domain: str) -> bool:
        return self.state(domain) in ("installed", "enabled", "disabled")


def _purge_modules(rec: dict) -> None:
    top = import_package(rec["manifest"])
    if top in HOST_PROVIDED_MODULES or top in sys.stdlib_module_names:
        return
    for name in [n for n in sys.modules if n == top or n.startswith(top + ".")]:
        del sys.modules[name]
    root = str(Path(rec["path"]))
    while root in sys.path:
        sys.path.remove(root)


def _wheel_pkg(whl: Path) -> str:
    from .package import open_package
    return open_package(whl).import_package


def _probe_drift(manifest: dict, probe: dict) -> "list[tuple[str, Any, Any]]":
    return drift_from(manifest, probe["config_schema"], probe["bus"], probe["bus_error"])


__all__ = ["ALLOWED", "ConsentDenied", "ConsentPrompt", "OPERATIONS", "PluginManager",
           "REQUIREMENTS_V01", "STATES", "TransitionRefused", "refuse_requirements"]
