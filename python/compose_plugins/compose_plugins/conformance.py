"""The conformance script ([CXC-PC-CONFORMANCE-SCRIPT], [CXC-PC-NEGATIVE-CONTROLS]).

Runs the 9-step hello-world lifecycle, then every negative control, against ONE
host instance at ONE isolation level, printing per step the OBSERVED values.
At the subprocess level it also checks [CXC-PC-RUNNER-LOCAL-ONLY] and a child
crash. Exit 0 ONLY when every step and control passes; 1 otherwise.

The host is given as a factory ``module:callable(isolation, workdir)`` that
returns a target with ``.manager`` (PluginManager), ``.bus`` (the bus the
plug-ins run on), ``.host``, ``.isolation``, ``.operator`` and ``.close()``.

Two more target members (spec 95 r24, [CXC-PC-CONFORMANCE-SCRIPT]):

* ``foreign_host: str`` (REQUIRED, ``foreign-host``): another host's name, used
  by NC4's paired positive control, which puts an unknown key inside
  ``hosts.<foreign_host>``. The runner refuses before step 1 when it is absent
  ("foreign_host missing"), equal to ``target.host.name``, or breaks
  [CXC-PC-HOST-SECTIONS] ``host-name-rule``.
* ``host_section(fixture: str) -> dict`` (OPTIONAL, ``host-section-hook``):
  this host's own section VALUE for one of :data:`FIXTURE_IDS`. The runner
  writes it at ``hosts.<target.host.name>`` of every manifest it hands to the
  host, before consent, so the consented ``manifest_sha256`` covers it. A
  target without the hook gets ``{}`` there.

    python -m compose_plugins.conformance \
        --target composeio.composeserver_plugins:conformance_target --isolation subprocess
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import HostNameRefused, ManifestRefused, ManifestShaMismatch
from .fixtures import package_dir
from .lifecycle import ConsentDenied
from .manifest import check_host_name
from .derive import DriftRefused
from .version import HOST_CONTRACT

DOMAIN = "hello_world"
PING, PONG = "plugins/hello_world/ping", "plugins/hello_world/pong"
GREETING = "plugins/hello_world/greeting"
V2_ADDED = ["bus.subscribe:plugins/hello_world/name:EVENT"]

_NET_EVENTS: "list[str]" = []
_NET_WATCH = [False]


def _audit(event: str, args: Any) -> None:
    if _NET_WATCH[0] and event in ("socket.connect", "socket.getaddrinfo",
                                   "urllib.Request", "http.client.connect"):
        _NET_EVENTS.append(event)


class Fail(AssertionError):
    pass


#: the script's own fixture ids, the only values ``host_section`` is called with
#: ([CXC-PC-CONFORMANCE-SCRIPT] ``host-section-hook``, spec 95 r24)
FIXTURE_IDS = ("hello-world-v1", "hello-world-v2",
               "NC1", "NC2", "NC3", "NC4", "NC5", "NC6", "NC7", "NC8")


class ConformanceRefused(Exception):
    """The runner refuses to run at all, before step 1 (r24 ``foreign-host``)."""


def check_foreign_host(target) -> str:
    """[CXC-PC-CONFORMANCE-SCRIPT] ``foreign-host`` (r24): every host's target
    MUST name another, valid host. Returns it, or raises
    :class:`ConformanceRefused` with "foreign_host missing" or a message
    naming the value."""
    if not hasattr(target, "foreign_host"):
        raise ConformanceRefused("foreign_host missing")
    value = target.foreign_host
    try:
        check_host_name(value)
    except HostNameRefused:
        raise ConformanceRefused(
            f"foreign_host {value!r} REFUSED: it breaks [CXC-PC-HOST-SECTIONS] "
            f"host-name-rule (^[a-z][a-z0-9_]*$, never 'bus', 'code' or "
            f"'permission')") from None
    if value == target.host.name:
        raise ConformanceRefused(
            f"foreign_host {value!r} REFUSED: it equals this host's own name "
            f"(target.host.name {target.host.name!r}); it must name ANOTHER host")
    return value


def host_section(target, fixture: str) -> Any:
    """[CXC-PC-CONFORMANCE-SCRIPT] ``host-section-hook`` (r24): the target's own
    section for ``fixture``, or ``{}`` when the target provides no hook."""
    if fixture not in FIXTURE_IDS:   # a runner bug, never a host's
        raise ValueError(f"unknown conformance fixture id {fixture!r}")
    hook = getattr(target, "host_section", None)
    return {} if hook is None else hook(fixture)


def check(cond: bool, what: str) -> None:
    if not cond:
        raise Fail(what)


class Run:
    def __init__(self, factory: Callable, isolation: str, workdir: Path, out):
        self.factory, self.isolation, self.workdir, self.out = factory, isolation, workdir, out
        self.n = 0
        self.main = self.new_target()
        try:
            self.foreign_host = check_foreign_host(self.main)
        except ConformanceRefused:
            self.main.close()
            raise

    def new_target(self):
        self.n += 1
        d = self.workdir / f"host-{self.n}"
        d.mkdir(parents=True)
        return self.factory(self.isolation, d)

    def observe(self, key: str, value: Any) -> None:
        print(f"    observed {key}: {value}", file=self.out)

    def fixture(self, t, version: str, fixture: str,
                edit: "Callable[[dict], None] | None" = None) -> Path:
        """A copy of hello-world ``version`` for target ``t``, the ONE path by
        which the script hands a package to the host. (r24 host-section-hook)
        ``hosts.<t.host.name>`` is set to ``host_section(t, fixture)`` (``{}``
        without the hook) BEFORE ``edit`` and before consent, so the consented
        ``manifest_sha256`` covers the section."""
        self.n += 1
        dst = self.workdir / f"pkg-{self.n}-{version}"
        shutil.copytree(package_dir(version), dst,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        mp = dst / "manifest.json"
        data = json.loads(mp.read_text())
        section = host_section(t, fixture)
        data["hosts"][t.host.name] = section
        self.observe(f"hosts.{t.host.name} written for fixture {fixture}", json.dumps(section))
        if edit is not None:
            edit(data)
        mp.write_text(json.dumps(data, indent=2) + "\n")
        return dst

    def install(self, t, source) -> Any:
        p = t.manager.prepare_install(source)
        t.manager.install(source, consented_sha256=p.manifest_sha256, operator=t.operator,
                          accepted=True)
        return p


def wait_for(pred: Callable[[], bool], timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def ping(t, count: int = 1, expect: int = 1) -> "list[dict]":
    got: list[dict] = []

    def on(ns, et, data):
        got.append(dict(data))
    t.bus.subscribe_events(PONG, on)
    try:
        for _ in range(count):
            t.bus.publish_event(PING, "ping", {})
        wait_for(lambda: len(got) >= expect, 10.0)
        time.sleep(0.2)
    finally:
        ns = t.bus.get_namespace(PONG)
        if ns is not None and on in ns._event_subscribers:
            ns._event_subscribers.remove(on)
    return got


def settle(t, pred: Callable[[str], bool], timeout: float = 10.0) -> str:
    def ok() -> bool:
        t.manager.tick()
        return pred(t.manager.status(DOMAIN))
    wait_for(ok, timeout)
    return t.manager.status(DOMAIN)


# =========================================================================== #
# The 9 steps (one host instance, in order).
# =========================================================================== #
def s1(r: Run) -> None:
    t = r.main
    check(t.host.name in json.loads((package_dir("v1") / "manifest.json").read_text())["hosts"],
          f"fixture does not declare host {t.host.name!r} (not applicable)")
    src = r.fixture(t, "v1", "hello-world-v1")
    p = t.manager.prepare_install(src)
    r.observe("consent screen", "\n      " + p.content.text().replace("\n", "\n      "))
    check(f"runs as: {r.isolation}" in p.content.text(), "consent screen lacks runs as")
    t.manager.install(src, consented_sha256=p.manifest_sha256,
                      operator=t.operator, accepted=True)
    rec = t.manager.record(DOMAIN)
    r.observe("state", rec["state"])
    r.observe("manifest_sha256", rec["manifest_sha256"])
    r.observe("runs_as", rec["runs_as"])
    check(rec["state"] == "installed" and rec["version"] == "1.0.0", "v1 not installed")
    check(rec["runs_as"] == r.isolation, f"actual level {rec['runs_as']} != {r.isolation}")


def s2(r: Run) -> None:
    r.main.manager.configure(DOMAIN, {"greeting": "hello from conformance"})
    r.observe("stored config", r.main.manager.configs.get(DOMAIN))


def s3(r: Run) -> None:
    t = r.main
    before = _listen_sockets()
    status = t.manager.enable(DOMAIN)
    r.observe("enable ->", status)
    r.observe("state", t.manager.state(DOMAIN))
    check(t.manager.state(DOMAIN) == "enabled", "not enabled")
    if r.isolation == "subprocess":
        local_only(r, t, before)


def s4(r: Run) -> None:
    status = settle(r.main, lambda s: s == "running")
    r.observe("health", status)
    check(status == "running", f"health is {status!r}, not running")


def s5(r: Run) -> None:
    pongs = ping(r.main, count=3, expect=3)
    r.observe("pongs", pongs)
    check([p.get("n") for p in pongs] == [1, 2, 3], "pongs n != 1, 2, 3")
    check(all(p.get("greeting") == "hello from conformance" for p in pongs),
          "pong greeting is not the configured one")
    r.observe("retained greeting", r.main.bus.get_state(GREETING))


def s6(r: Run) -> None:
    t = r.main
    src = r.fixture(t, "v2", "hello-world-v2")
    p = t.manager.prepare_upgrade(src)
    r.observe("re-consent widens", p.content.widens)
    r.observe("re-consent ADDED", list(p.content.added))
    r.observe("re-consent highlighted lines", [ln.text() for ln in p.content.lines if ln.added])
    check(p.content.widens and list(p.content.added) == V2_ADDED, "re-consent not as specified")
    t.manager.upgrade(src, consented_sha256=p.manifest_sha256,
                      operator=t.operator, accepted=True)
    rec = t.manager.record(DOMAIN)
    r.observe("version", rec["version"])
    r.observe("stored config (kept)", t.manager.configs.get(DOMAIN))
    check(rec["version"] == "2.0.0", "not upgraded")
    check(t.manager.configs.get(DOMAIN) == {"greeting": "hello from conformance"},
          "stored config not kept")
    settle(t, lambda s: s == "running")
    pongs = ping(t, count=1, expect=1)
    r.observe("pongs for one ping (repeat default 1)", pongs)
    check(len(pongs) == 1 and pongs[0].get("n") == 1, "repeat did not default to 1")


def s7(r: Run) -> None:
    t = r.main
    status = t.manager.disable(DOMAIN)
    r.observe("state", t.manager.state(DOMAIN))
    r.observe("status", status)
    pongs = ping(t, count=1, expect=1)
    r.observe("pongs while disabled", pongs)
    check(t.manager.state(DOMAIN) == "disabled" and status == "stopped" and not pongs,
          "disable did not stop it")
    check((Path(t.manager.record(DOMAIN)["path"])).exists(), "code removed by disable")


def s8(r: Run) -> None:
    t = r.main
    t.manager.enable(DOMAIN)
    status = settle(t, lambda s: s == "running")
    r.observe("status", status)
    r.observe("stored config", t.manager.configs.get(DOMAIN))
    pongs = ping(t, count=1, expect=1)
    r.observe("first pong after re-enable", pongs)
    check(status == "running", "not running after re-enable")
    check(t.manager.configs.get(DOMAIN) == {"greeting": "hello from conformance"},
          "config changed by re-enable")
    check(len(pongs) == 1 and pongs[0].get("n") == 1, "n did not restart at 1")


def s9(r: Run) -> None:
    t = r.main
    path = Path(t.manager.record(DOMAIN)["path"])
    # positive precondition (QA N3): the tombstone check below is only a
    # measurement if the retained greeting EXISTED before uninstall; without
    # this, a plug-in that never published its STATE would pass step 9
    before = t.bus.get_state(GREETING)
    r.observe("retained greeting before uninstall", repr(before))
    check(before is not None, "no retained greeting before uninstall: the tombstone "
          "check would prove nothing")
    t.manager.uninstall(DOMAIN)
    r.observe("state", t.manager.state(DOMAIN))
    r.observe("retained greeting after uninstall (tombstone)", repr(t.bus.get_state(GREETING)))
    r.observe("code present", path.exists())
    r.observe("config present", t.manager.configs.exists(DOMAIN))
    recs = [f"{x.version}:{x.decision}:{x.decided_by}" for x in t.manager.consent.records()]
    r.observe("consent records", recs)
    check(t.bus.get_state(GREETING) is None, "greeting not tombstoned")
    check(not path.exists() and not t.manager.configs.exists(DOMAIN), "code or config remains")
    check([x.split(":")[:2] for x in recs] == [["1.0.0", "granted"], ["2.0.0", "granted"]],
          "consent records for v1 and v2 not kept")


# =========================================================================== #
# Subprocess-level checks.
# =========================================================================== #
def _inet_fds(pid: int) -> "list[str]":
    try:
        import psutil
        return [f"{c.family.name} {c.laddr} {c.status}"
                for c in psutil.Process(pid).net_connections(kind="inet")]
    except ImportError:
        out = subprocess.run(["lsof", "-a", "-nP", "-i", "-p", str(pid)],
                             capture_output=True, text=True).stdout
        return [ln for ln in out.splitlines()[1:] if ln.strip()]


def _listen_sockets() -> "set[str]":
    try:
        import psutil
        return {f"{c.laddr}" for c in psutil.Process(os.getpid()).net_connections(kind="inet")
                if c.status == "LISTEN"}
    except ImportError:
        out = subprocess.run(["lsof", "-a", "-nP", "-iTCP", "-sTCP:LISTEN", "-p",
                              str(os.getpid())], capture_output=True, text=True).stdout
        return {ln for ln in out.splitlines()[1:] if ln.strip()}


def _unix_fds(pid: int) -> int:
    out = subprocess.run(["lsof", "-a", "-nP", "-U", "-p", str(pid)],
                         capture_output=True, text=True).stdout
    return len([ln for ln in out.splitlines()[1:] if ln.strip()])


def local_only(r: Run, t, listen_before: "set[str]") -> None:
    run = t.manager.running(DOMAIN)
    pid = run.pid if run is not None else None
    check(pid is not None and pid != os.getpid(), "plug-in is not in a child process")
    inet = _inet_fds(pid)
    new_listen = _listen_sockets() - listen_before
    r.observe("child pid / host pid", f"{pid} / {os.getpid()}")
    r.observe("child AF_INET/AF_INET6 descriptors", inet)
    r.observe("child AF_UNIX descriptors (lsof -U)", _unix_fds(pid))
    r.observe("new LISTEN sockets in the host", sorted(new_listen))
    check(inet == [] and not new_listen, "local-only violated")


def l1_refuses_non_unix(r: Run) -> None:
    from . import runner as runner_mod

    class _Fake:
        family = socket.AF_INET

        def close(self):
            pass
    real = socket.socketpair
    before = _inet_fds(os.getpid())
    socket.socketpair = lambda *a, **k: (_Fake(), _Fake())
    try:
        try:
            runner_mod.make_socketpair()
        except runner_mod.RunnerRefused as exc:
            r.observe("refusal", exc)
        else:
            raise Fail("runner started with an AF_INET pair")
    finally:
        socket.socketpair = real
    after = _inet_fds(os.getpid())
    r.observe("host inet descriptors before/after", f"{len(before)}/{len(after)}")
    check(len(after) <= len(before), "a TCP socket was opened")


def c1_child_crash(r: Run) -> None:
    t = r.new_target()
    try:
        r.install(t, r.fixture(t, "v1", "hello-world-v1"))
        t.manager.enable(DOMAIN)
        settle(t, lambda s: s == "running")
        pid = t.manager.running(DOMAIN).pid
        os.kill(pid, 9)
        status = settle(t, lambda s: s.startswith("failed"))
        r.observe("after kill -9 of the child", status)
        r.observe("host pid alive", os.getpid())
        t.bus.publish_state("plugins/other/x", 1)
        r.observe("host bus still serves", t.bus.get_state("plugins/other/x"))
        check(status.startswith("failed(killed by signal SIGKILL"), "crash not failed(<reason>)")
    finally:
        t.close()


# =========================================================================== #
# The negative controls (each on a fresh host instance).
# =========================================================================== #
def _fresh(fn):
    def wrapped(r: Run) -> None:
        t = r.new_target()
        try:
            fn(r, t)
        finally:
            t.close()
    wrapped.__name__ = fn.__name__
    return wrapped


def _nothing_installed(r: Run, t) -> None:
    entries = sorted(p.name for p in t.host.install_root.iterdir())
    r.observe("install root entries", entries)
    r.observe("install record", t.manager.record(DOMAIN))
    check(entries == [] and t.manager.record(DOMAIN) is None, "something was installed")


@_fresh
def nc1(r: Run, t) -> None:
    src = r.fixture(t, "v1", "NC1")
    p = t.manager.prepare_install(src)
    mp = src / "manifest.json"
    raw = bytearray(mp.read_bytes())
    i = raw.index(b"Hello World")
    raw[i] = ord("J")
    mp.write_bytes(bytes(raw))
    try:
        t.manager.install(src, consented_sha256=p.manifest_sha256, operator=t.operator,
                          accepted=True)
    except ManifestShaMismatch as exc:
        r.observe("refusal", exc)
        check(exc.expected in str(exc) and exc.actual in str(exc), "digests not both named")
    else:
        raise Fail("tampered manifest was installed")
    _nothing_installed(r, t)
    r.observe("consent records", t.manager.consent.records())
    check(t.manager.consent.records() == [], "a grant was recorded")


@_fresh
def nc2(r: Run, t) -> None:
    src = r.fixture(t, "v1", "NC2",
                    lambda d: d["config_schema"]["properties"].pop("greeting"))
    r.install(t, src)
    try:
        t.manager.enable(DOMAIN)
    except DriftRefused as exc:
        r.observe("refusal", str(exc).replace("\n", " | "))
        check(any(p == "config_schema.properties.greeting" for p, _, _ in exc.diffs),
              "drifting path not named")
    else:
        raise Fail("drifted plug-in was enabled")
    r.observe("state", t.manager.state(DOMAIN))
    check(t.manager.state(DOMAIN) == "installed", "not left installed")


def _refused_at_install(r: Run, t, fixture: str, edit, must: "list[str]") -> None:
    src = r.fixture(t, "v1", fixture, edit)
    try:
        t.manager.prepare_install(src)
    except ManifestRefused as exc:
        text = str(exc).replace("\n", " | ")
        r.observe("refusal", text)
        for m in must:
            check(m in text, f"refusal does not name {m!r}")
    else:
        raise Fail("manifest was accepted")
    _nothing_installed(r, t)


def _next_minor(contract: str) -> str:
    major, minor = contract.split(".")
    return f"{major}.{int(minor) + 1}"


@_fresh
def nc3(r: Run, t) -> None:
    # one MINOR newer than this host (spec 95 r24 wrote "0.2" for a 0.1 host;
    # a 0.2 host accepts "0.2", so the control uses the next MINOR)
    newer = _next_minor(HOST_CONTRACT)
    _refused_at_install(r, t, "NC3", lambda d: d.update(contract=newer),
                        [newer, HOST_CONTRACT])


@_fresh
def nc4(r: Run, t) -> None:
    _refused_at_install(r, t, "NC4", lambda d: d.update(permisions=["bus"]), ["permisions"])
    # paired positive (r24): the unknown key goes in ANOTHER host's section,
    # hosts.<foreign_host>, never a literal hosts.gnomon and never this host's own
    fh = r.foreign_host
    src = r.fixture(t, "v1", "NC4", lambda d: d["hosts"].setdefault(fh, {}).update(
        permisions=f"{fh}'s"))
    written = json.loads((src / "manifest.json").read_text())["hosts"]
    r.observe(f"paired positive: hosts.{fh} / hosts.{t.host.name}",
              f"{json.dumps(written[fh])} / {json.dumps(written[t.host.name])}")
    check("permisions" in written[fh] and "permisions" not in written[t.host.name],
          f"unknown key not placed in hosts.{fh} alone")
    r.install(t, src)
    r.observe(f"paired positive: unknown key in hosts.{fh} -> state", t.manager.state(DOMAIN))
    check(t.manager.state(DOMAIN) == "installed", "other host's section not ignored")


@_fresh
def nc5(r: Run, t) -> None:
    r.install(t, r.fixture(t, "v1", "NC5"))
    t.manager.configure(DOMAIN, {"greeting": 42})
    before = t.manager.instantiations
    status = t.manager.enable(DOMAIN)
    r.observe("status", status)
    r.observe("instances constructed", t.manager.instantiations - before)
    t.bus.publish_state("plugins/other/x", 2)
    r.observe("host bus still serves", t.bus.get_state("plugins/other/x"))
    check(status.startswith("parked(config:greeting:"), "not parked with config: greeting")
    check(t.manager.instantiations == before, "an instance was constructed")


@_fresh
def nc6(r: Run, t) -> None:
    r.install(t, r.fixture(t, "v1", "NC6"))
    t.manager.configure(DOMAIN, {"greeting": "keep me"})
    t.manager.enable(DOMAIN)
    v2 = r.fixture(t, "v2", "NC6")
    p = t.manager.prepare_upgrade(v2)
    try:
        t.manager.upgrade(v2, consented_sha256=p.manifest_sha256,
                          operator=t.operator, accepted=False)
    except ConsentDenied as exc:
        r.observe("refusal", exc)
    else:
        raise Fail("refused widening was applied")
    rec = t.manager.record(DOMAIN)
    last = t.manager.consent.records()[-1]
    r.observe("version / state / config", f"{rec['version']} / {rec['state']} / "
              f"{t.manager.configs.get(DOMAIN)}")
    r.observe("last consent record", f"{last.version} {last.decision} {last.decided_by}")
    check(rec["version"] == "1.0.0" and rec["state"] == "enabled", "v1 not kept as it was")
    check(t.manager.configs.get(DOMAIN) == {"greeting": "keep me"}, "config changed")
    check(last.decision == "refused" and last.version == "2.0.0", "no refused record")


@_fresh
def nc7(r: Run, t) -> None:
    for pin in ("composebus==0.1.0", "compose-plugins==0.1.0", "composeio==1.0.0",
                "compose.plugins==0.1.0", "Compose-_.Plugins==0.1.0", "COMPOSEBUS==0.1.0"):
        _NET_EVENTS.clear()
        _NET_WATCH[0] = True
        try:
            name = pin.split("==")[0]
            _refused_at_install(r, t, "NC7", lambda d, p=pin: d.update(requirements=[p]),
                                [repr(name), "the HOST provides"])
        finally:
            _NET_WATCH[0] = False
        r.observe(f"network/index events for {pin}", list(_NET_EVENTS))
        check(_NET_EVENTS == [], "a network or index call was made")


#: [CXC-PC-NC-MALFORMED-PERMISSION] (r21): the claim's entry and its four variants,
#: each with the ONE field the refusal must name
NC8_VARIANTS = (
    ({"network": [{"direction": "outbound", "target": "*.example.com", "purpose": "fetch"}]},
     "permissions.network[0].target"),
    ({"network": [{"direction": "outbound", "target": "printer.local:631"}]},
     "permissions.network[0].purpose"),
    ({"filesystem": [{"path": "data/x", "access": "read", "purpose": "read data"}]},
     "permissions.filesystem[0].path"),
    ({"filesystem": [{"path": "/tmp/hello", "access": "write", "purpose": "log"}]},
     "permissions.filesystem[0].access"),
    ({"camera": [{"purpose": "photos"}]}, "permissions.camera"),
)


@_fresh
def nc8(r: Run, t) -> None:
    for perms, field in NC8_VARIANTS:
        src = r.fixture(t, "v1", "NC8", lambda d, p=perms: d.update(permissions=p))
        try:
            t.manager.prepare_install(src)
        except ManifestRefused as exc:
            r.observe(f"refusal for {field}", str(exc).replace("\n", " | "))
            check(exc.fields == [field], f"refusal names {exc.fields}, not exactly {field!r}")
        else:
            raise Fail(f"malformed permissions accepted ({field})")
        _nothing_installed(r, t)
        r.observe("consent records", t.manager.consent.records())
        check(t.manager.consent.records() == [], "a consent record was written")
    r.install(t, r.fixture(t, "v1", "NC8"))
    r.observe("paired positive: hello-world (no permissions) -> state", t.manager.state(DOMAIN))
    check(t.manager.state(DOMAIN) == "installed", "hello-world's own manifest did not install")


@_fresh
def nc_r15(r: Run, t) -> None:
    _refused_at_install(r, t, "hello-world-v1",
                        lambda d: d.update(requirements=["requests==2.32.3"]),
                        ["'requests==2.32.3'", "requirements are not installed in contract "
                         "v0.1: installing a pin also installs its transitive dependencies, "
                         "which are code you never consented to, and needs a package index; "
                         "installing requirements arrives in v0.2"])


@_fresh
def h1_degraded(r: Run, t) -> None:
    r.install(t, r.fixture(t, "v1", "hello-world-v1"))
    t.manager.configure(DOMAIN, {"greeting": ""})
    t.manager.enable(DOMAIN)
    status = settle(t, lambda s: s.startswith("degraded"))
    pongs = ping(t, count=1, expect=1)
    r.observe("status", status)
    r.observe("still answers", pongs)
    check(status == "degraded(empty greeting)" and len(pongs) == 1, "degraded not reported")


@dataclass(frozen=True)
class Step:
    sid: str
    name: str
    spec: str
    run: Callable[[Run], None]
    levels: tuple = ("inprocess", "subprocess")


STEPS: "tuple[Step, ...]" = (
    Step("1", "install-v1-with-consent", "CXC-PC-CONFORMANCE-SCRIPT step 1", s1),
    Step("2", "set-config", "CXC-PC-CONFORMANCE-SCRIPT step 2", s2),
    Step("3", "start", "CXC-PC-CONFORMANCE-SCRIPT step 3", s3),
    Step("4", "health-running", "CXC-PC-CONFORMANCE-SCRIPT step 4", s4),
    Step("5", "publish-subscribe-round-trip", "CXC-PC-CONFORMANCE-SCRIPT step 5", s5),
    Step("6", "upgrade-v2-reconsent-migrate", "CXC-PC-CONFORMANCE-SCRIPT step 6", s6),
    Step("7", "disable", "CXC-PC-CONFORMANCE-SCRIPT step 7", s7),
    Step("8", "re-enable", "CXC-PC-CONFORMANCE-SCRIPT step 8", s8),
    Step("9", "uninstall-tombstone-keep-record", "CXC-PC-CONFORMANCE-SCRIPT step 9", s9),
    Step("NC1", "tampered-manifest", "CXC-PC-NC-TAMPERED-MANIFEST", nc1),
    Step("NC2", "class-manifest-drift", "CXC-PC-NC-CLASS-MANIFEST-DRIFT", nc2),
    Step("NC3", "contract-mismatch", "CXC-PC-NC-CONTRACT-MISMATCH", nc3),
    Step("NC4", "unknown-core-key", "CXC-PC-NC-UNKNOWN-CORE-KEY", nc4),
    Step("NC5", "invalid-config", "CXC-PC-NC-INVALID-CONFIG", nc5),
    Step("NC6", "widening-refused", "CXC-PC-NC-WIDENING-REFUSED", nc6),
    Step("NC7", "host-provided-requirement", "CXC-PC-NC-HOST-PROVIDED-REQUIREMENT", nc7),
    Step("NC8", "malformed-permission", "CXC-PC-NC-MALFORMED-PERMISSION", nc8),
    Step("R15", "requirements-refused-in-v0.1", "CXC-PC-LIFECYCLE-INSTALL (r15)", nc_r15),
    Step("H1", "degraded-empty-greeting", "CXC-PC-HELLO-V1 / LIFECYCLE-HEALTH", h1_degraded),
    Step("L1", "runner-refuses-non-AF_UNIX", "CXC-PC-RUNNER-LOCAL-ONLY", l1_refuses_non_unix,
         ("subprocess",)),
    Step("C1", "child-crash-is-failed", "CXC-PC-SUBPROCESS-RUNNER", c1_child_crash,
         ("subprocess",)),
)


def run(factory: Callable, isolation: str, out=None, workdir: "Path | None" = None) -> int:
    out = out or sys.stdout
    sys.addaudithook(_audit) if not getattr(run, "_hooked", False) else None
    run._hooked = True  # type: ignore[attr-defined]
    workdir = Path(workdir or tempfile.mkdtemp(prefix="cp-conformance-"))
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        r = Run(factory, isolation, workdir, out)
    except ConformanceRefused as exc:
        print(f"compose_plugins conformance: REFUSED before step 1: {exc}", file=out)
        return 2
    print(f"compose_plugins conformance: host={r.main.host.name} isolation={isolation} "
          f"contract={HOST_CONTRACT} foreign_host={r.foreign_host} started={started}", file=out)
    results: "list[tuple[Step, str]]" = []
    try:
        for step in STEPS:
            if isolation not in step.levels:
                continue
            print(f"STEP {step.sid} {step.name} [{step.spec}]", file=out)
            try:
                step.run(r)
                status = "PASS"
            except Fail as exc:
                status = f"FAIL ({exc})"
            except Exception as exc:   # a crash is a FAIL, never a pass
                status = f"FAIL ({type(exc).__name__}: {exc})"
                traceback.print_exc(file=out)
            results.append((step, status))
            print(f"  -> {status}", file=out)
    finally:
        r.main.close()
    passed = sum(1 for _, s in results if s == "PASS")
    failed = len(results) - passed
    print(f"SUMMARY host={r.main.host.name} isolation={isolation} contract={HOST_CONTRACT}: "
          f"{passed} passed, {failed} failed of {len(results)}", file=out)
    return 0 if failed == 0 and results else 1


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m compose_plugins.conformance",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", required=True, help="module:factory(isolation, workdir)")
    ap.add_argument("--isolation", required=True, choices=["inprocess", "subprocess"])
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args(argv)
    mod, _, attr = args.target.partition(":")
    factory = getattr(importlib.import_module(mod), attr)
    return run(factory, args.isolation, workdir=Path(args.workdir) if args.workdir else None)


if __name__ == "__main__":
    sys.exit(main())
