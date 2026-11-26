"""The lifecycle ([CXC-PC-LIFECYCLE-*]) at the library level, with a test Host."""
from __future__ import annotations

import json
import sys
import zipfile

import pytest

from compose_plugins.derive import DriftRefused
from compose_plugins.errors import HostNameRefused, ManifestRefused, ManifestShaMismatch
from compose_plugins.host import Host
from compose_plugins.lifecycle import (
    ALLOWED, OPERATIONS, REQUIREMENTS_V01, STATES, ConsentDenied, PluginManager,
    TransitionRefused,
)

from wave2_helpers import TestHost, fixture_copy, install, make, pongs, wait_for

D = "hello_world"


# ---- the Host protocol ----------------------------------------------------- #
@pytest.mark.parametrize("name", ["bus", "code", "Bad", ""])
def test_host_name_is_checked_at_construction(tmp_path, name):
    with pytest.raises(HostNameRefused):
        Host(name, state_root=tmp_path)


@pytest.mark.parametrize("minimum,floor,actual", [
    ("inprocess", "inprocess", "inprocess"), ("inprocess", "subprocess", "subprocess"),
    ("subprocess", "inprocess", "subprocess"), ("subprocess", "subprocess", "subprocess")])
def test_isolation_level_is_never_below_the_floor(tmp_path, minimum, floor, actual):
    assert TestHost(tmp_path, minimum).isolation_level(floor) == actual


# ---- the transition table -------------------------------------------------- #
def _bring_to(pm, tmp_path, state):
    src = fixture_copy(tmp_path, "v1")
    if state == "available":
        return
    install(pm, src)
    if state in ("enabled", "disabled"):
        pm.enable(D)
    if state == "disabled":
        pm.disable(D)
    if state == "uninstalled":
        pm.uninstall(D)


def _attempt(pm, tmp_path, op):
    v1, v2 = tmp_path / "pkg-v1", fixture_copy(tmp_path, "v2")
    if op == "install":
        install(pm, v1)
    elif op == "configure":
        pm.configure(D, {"greeting": "x"})
    elif op == "enable":
        pm.enable(D)
    elif op == "disable":
        pm.disable(D)
    elif op == "upgrade":
        p = pm.prepare_upgrade(v2)
        pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    elif op == "uninstall":
        pm.uninstall(D)


@pytest.mark.parametrize("state", [s for s in STATES])
@pytest.mark.parametrize("op", OPERATIONS)
def test_every_operation_from_every_state(tmp_path, state, op):
    host, pm = make(tmp_path)
    _bring_to(pm, tmp_path, state)
    assert pm.state(D) == state
    if state in ALLOWED[op]:
        _attempt(pm, tmp_path, op)
    else:
        with pytest.raises(TransitionRefused) as ei:
            _attempt(pm, tmp_path, op)
        assert repr(state) in str(ei.value) and op in str(ei.value)
    for d in list(pm._running):
        pm._stop(d)


def test_upgrade_from_disabled_is_refused_naming_the_state(tmp_path):
    host, pm = make(tmp_path)
    _bring_to(pm, tmp_path, "disabled")
    with pytest.raises(TransitionRefused) as ei:
        pm.prepare_upgrade(fixture_copy(tmp_path, "v2"))
    print(ei.value)
    assert "it is 'disabled'" in str(ei.value)


# ---- install --------------------------------------------------------------- #
def test_requirements_are_refused_in_v0_1_with_the_exact_reason_naming_each_pin(tmp_path):
    host, pm = make(tmp_path)
    src = fixture_copy(tmp_path, "v1", lambda d: d.update(requirements=["a==1", "b==2"]))
    with pytest.raises(ManifestRefused) as ei:
        pm.prepare_install(src)
    print(ei.value)
    assert ei.value.fields == ["requirements[0]", "requirements[1]"]
    assert ei.value.refusals[0].message == f"'a==1': {REQUIREMENTS_V01}"
    assert REQUIREMENTS_V01 == (
        "requirements are not installed in contract v0.1: installing a pin also installs its "
        "transitive dependencies, which are code you never consented to, and needs a package "
        "index; installing requirements arrives in v0.2")


def test_consent_comes_before_any_import(tmp_path, monkeypatch):
    host, pm = make(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    sys.modules.pop("hello_world", None)
    sys.modules.pop("hello_world.plugin", None)
    install(pm, src)
    assert "hello_world" not in sys.modules and "hello_world.plugin" not in sys.modules
    pm.enable(D)
    assert "hello_world.plugin" in sys.modules
    pm.disable(D)


def test_tampered_manifest_is_refused_and_nothing_is_installed(tmp_path):
    host, pm = make(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    p = pm.prepare_install(src)
    mp = src / "manifest.json"
    mp.write_bytes(mp.read_bytes().replace(b"Hello World", b"Jello World"))
    with pytest.raises(ManifestShaMismatch) as ei:
        pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert p.manifest_sha256 in str(ei.value)
    assert list(host.install_root.iterdir()) == [] and pm.record(D) is None
    assert pm.consent.records() == [] and host.gate_calls == []


def test_refused_consent_installs_nothing_and_records_the_refusal(tmp_path):
    host, pm = make(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    p = pm.prepare_install(src)
    with pytest.raises(ConsentDenied):
        pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=False)
    assert list(host.install_root.iterdir()) == [] and pm.record(D) is None
    assert [r.decision for r in pm.consent.records()] == ["refused"]


def test_wheel_install_and_run(tmp_path):
    host, pm = make(tmp_path)
    pkg = fixture_copy(tmp_path, "v1")
    whl = tmp_path / "hello_world-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.write(pkg / "manifest.json", "hello_world/manifest.json")
        for f in (pkg / "hello_world").glob("*.py"):
            zf.write(f, f"hello_world/{f.name}")
        zf.writestr("hello_world-1.0.0.dist-info/METADATA", "Name: hello-world\n")
    install(pm, whl)
    assert pm.enable(D) == "running"
    assert [p["n"] for p in pongs(host.bus(), 2, 2)] == [1, 2]
    pm.disable(D)


def test_zip_slip_member_is_refused(tmp_path):
    host, pm = make(tmp_path)
    pkg = fixture_copy(tmp_path, "v1")
    whl = tmp_path / "hello_world-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.write(pkg / "manifest.json", "hello_world/manifest.json")
        zf.writestr("../escaped.py", "x = 1\n")
    p = pm.prepare_install(whl)
    with pytest.raises(Exception) as ei:
        pm.install(whl, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    print(ei.value)
    assert "outside the install root" in str(ei.value)
    assert not (tmp_path / "escaped.py").exists() and pm.record(D) is None


# ---- enable / config / drift ----------------------------------------------- #
@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_drift_refuses_enable_naming_paths_and_values(tmp_path, level):
    host, pm = make(tmp_path, level)
    src = fixture_copy(tmp_path, "v1", lambda d: d["bus"]["subscribes"].clear())
    install(pm, src)
    with pytest.raises(DriftRefused) as ei:
        pm.enable(D)
    print(ei.value)
    assert ("bus.subscribes", [], [("plugins/hello_world/ping", "EVENT")]) in ei.value.diffs
    assert pm.state(D) == "installed"


@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_drift_check_runs_on_every_enable(tmp_path, level):
    host, pm = make(tmp_path, level)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    pm.disable(D)
    rec = pm.record(D)
    rec["manifest"]["config_schema"]["properties"]["greeting"]["default"] = "changed"
    pm.installs.put(D, rec)
    with pytest.raises(DriftRefused):
        pm.enable(D)


@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_invalid_config_parks_and_constructs_nothing(tmp_path, level):
    host, pm = make(tmp_path, level)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.configure(D, {"greeting": 42})
    assert pm.enable(D).startswith("parked(config:greeting:")
    assert pm.instantiations == 0 and pm.running(D) is None


@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_degraded_from_health_reason_and_keeps_running(tmp_path, level):
    host, pm = make(tmp_path, level)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.configure(D, {"greeting": ""})
    pm.enable(D)
    assert wait_for(lambda: (pm.tick(), pm.status(D))[1] == "degraded(empty greeting)")
    assert len(pongs(host.bus())) == 1
    pm.disable(D)


def test_inprocess_start_exception_is_failed_and_host_survives(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    run = pm.running(D)
    pm.disable(D)

    class Boom(run.cls):
        def start(self, bus):
            raise RuntimeError("no")
    from compose_plugins.runner import InprocessPlugin
    st = []
    InprocessPlugin(Boom, Boom.Config(), host.bus(), st.append).start()
    assert st == ["failed(start raised RuntimeError: no)"]


# ---- upgrade --------------------------------------------------------------- #
@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_v1_to_v2_keeps_config_and_defaults_repeat(tmp_path, level):
    host, pm = make(tmp_path, level)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.configure(D, {"greeting": "g"})
    pm.enable(D)
    v2 = fixture_copy(tmp_path, "v2")
    p = pm.prepare_upgrade(v2)
    assert p.content.widens and p.content.added == ("bus.subscribe:plugins/hello_world/name:EVENT",)
    pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert pm.record(D)["version"] == "2.0.0" and pm.configs.get(D) == {"greeting": "g"}
    assert wait_for(lambda: pm.status(D) == "running")
    assert [x["n"] for x in pongs(host.bus())] == [1]
    pm.configure(D, {"greeting": "g", "repeat": 3})
    pm.disable(D)
    pm.enable(D)
    assert wait_for(lambda: pm.status(D) == "running")
    assert [x["n"] for x in pongs(host.bus(), 2, 6)] == [1, 2, 3, 4, 5, 6]
    pm.disable(D)


def test_refused_widening_leaves_v1_exactly(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.configure(D, {"greeting": "keep"})
    pm.enable(D)
    v2 = fixture_copy(tmp_path, "v2")
    p = pm.prepare_upgrade(v2)
    with pytest.raises(ConsentDenied):
        pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=False)
    assert pm.record(D)["version"] == "1.0.0" and pm.state(D) == "enabled"
    assert pm.configs.get(D) == {"greeting": "keep"} and pm.status(D) == "running"
    assert pm.consent.records()[-1].decision == "refused"
    pm.disable(D)


def test_lowering_the_floor_widens_when_the_actual_level_drops(tmp_path):
    """r12: a v1 with floor subprocess on an inprocess-minimum host runs at
    subprocess; v2 lowering the floor to inprocess would drop it: widening."""
    host, pm = make(tmp_path, "inprocess")
    install(pm, fixture_copy(tmp_path, "v1", lambda d: d.update(isolation_floor="subprocess")))
    assert pm.record(D)["runs_as"] == "subprocess"
    v1b = fixture_copy(tmp_path, "v1", lambda d: d.update(version="1.0.1"), name="v1b")
    p = pm.prepare_upgrade(v1b)
    print(p.content.text())
    assert p.content.widens and p.content.added == ()
    with pytest.raises(ConsentDenied):
        pm.upgrade(v1b, consented_sha256=p.manifest_sha256, operator="op", accepted=False)
    assert pm.record(D)["version"] == "1.0.0"


def test_non_widening_upgrade_does_not_ask_the_gate(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    calls = len(host.gate_calls)
    v1b = fixture_copy(tmp_path, "v1", lambda d: d.update(version="1.0.1"), name="v1b")
    p = pm.prepare_upgrade(v1b)
    assert not p.content.widens
    pm.upgrade(v1b, consented_sha256=p.manifest_sha256, operator=None, accepted=False)
    assert pm.record(D)["version"] == "1.0.1" and len(host.gate_calls) == calls


def test_nothing_auto_upgrades(tmp_path):
    """A newer package next to the install never upgrades it by itself."""
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    fixture_copy(tmp_path, "v2")
    for _ in range(5):
        pm.tick()
    assert pm.record(D)["version"] == "1.0.0"
    assert not [n for n in dir(PluginManager) if "auto" in n.lower()]
    pm.disable(D)


def test_stored_config_invalid_under_v2_parks_and_is_not_rewritten(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.configure(D, {"greeting": "g", "repeat": "many"})
    pm.enable(D)          # v1 ignores repeat
    v2 = fixture_copy(tmp_path, "v2")
    p = pm.prepare_upgrade(v2)
    pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert pm.status(D).startswith("parked(config:repeat:")
    assert pm.configs.get(D) == {"greeting": "g", "repeat": "many"}


# ---- uninstall ------------------------------------------------------------- #
def test_uninstall_tombstones_removes_code_and_config_keeps_consent(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.configure(D, {"greeting": "g"})
    pm.enable(D)
    assert host.bus().get_state("plugins/hello_world/greeting") == "g"
    path = pm.record(D)["path"]
    pm.uninstall(D)
    assert host.bus().get_state("plugins/hello_world/greeting") is None
    assert not (tmp_path / path).exists() and not pm.configs.exists(D)
    assert [r.decision for r in pm.consent.records()] == ["granted"]
    assert pm.state(D) == "uninstalled"
