"""Spec 95 r19, [CXC-PC-RUN] inprocess-collision: in-process module objects
are never shared between hosts or tenants in one process. A collision RAISES
the plug-in's actual level to subprocess (consent and status show it); a host
without subprocess support refuses, naming the holder. Each test cites r19."""
from __future__ import annotations

import sys

import pytest

from compose_plugins import lifecycle
from compose_plugins.errors import ManifestRefused, PluginContractRefusal
from compose_plugins.runner import InprocessPlugin, SubprocessPlugin

from wave2_helpers import PING, PONG, fixture_copy, install, make, wait_for

D = "hello_world"
COUNTER = ("\nPINGS_SEEN = 0\n\n"
           "def _count():\n    global PINGS_SEEN\n    PINGS_SEEN += 1\n    return PINGS_SEEN\n")


def _counting(tmp_path, version, name):
    """hello-world whose MODULE holds state: a module-level ping counter that
    each pong reports as `seen`."""
    pkg = fixture_copy(tmp_path, version, name=name)
    p = pkg / "hello_world" / "plugin.py"
    src = p.read_text().replace('{"greeting": self.config.greeting, "n": self._n}',
                                '{"greeting": self.config.greeting, "n": self._n, '
                                '"seen": _count()}')
    p.write_text(src + COUNTER)
    return pkg


def _two_hosts(tmp_path):
    a_host, a = make(tmp_path / "a")
    b_host, b = make(tmp_path / "b")
    return a_host, a, b_host, b


def _pongs(bus, count=1, expect=1):
    got = []
    bus.subscribe_events(PONG, lambda ns, et, d: got.append(dict(d)))
    for _ in range(count):
        bus.publish_event(PING, "ping", {})
    wait_for(lambda: len(got) >= expect)
    return got


def test_r19_same_plugin_in_two_hosts_second_is_raised_to_subprocess(tmp_path):
    """[r19] two hosts, one process, same plug-in: the second runs at subprocess,
    consent and status show it, and both work."""
    a_host, a, b_host, b = _two_hosts(tmp_path)
    install(a, _counting(tmp_path, "v1", "a-pkg"))
    a.configure(D, {"greeting": "from A"})
    a.enable(D)
    assert isinstance(a.running(D), InprocessPlugin)
    held = sys.modules["hello_world.plugin"]
    p = b.prepare_install(_counting(tmp_path, "v1", "b-pkg"))
    print(p.content.text())
    assert ("runs as", "subprocess") in [(ln.label, ln.value) for ln in p.content.lines]
    b.install(p.source, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    b.configure(D, {"greeting": "from B"})
    assert b.enable(D) == "running" or wait_for(lambda: b.status(D) == "running")
    assert isinstance(b.running(D), SubprocessPlugin) and b.running(D).pid is not None
    assert b.record(D)["runs_as"] == "subprocess"
    assert sys.modules["hello_world.plugin"] is held        # A's modules untouched
    a_p = _pongs(a_host.bus(), 2, 2)
    b_p = _pongs(b_host.bus(), 3, 3)
    print("A", a_p, "B", b_p)
    assert [x["greeting"] for x in a_p] == ["from A"] * 2
    assert [x["greeting"] for x in b_p] == ["from B"] * 3
    # neither sees the other's MODULE state: each counter counts its own pings only
    assert [x["seen"] for x in a_p] == [1, 2]
    assert [x["seen"] for x in b_p] == [1, 2, 3]
    assert held.PINGS_SEEN == 2


def test_r19_a_different_version_is_raised_too(tmp_path):
    """[r19] host B installing v2 while host A holds v1: also raised."""
    a_host, a, b_host, b = _two_hosts(tmp_path)
    install(a, fixture_copy(tmp_path, "v1", name="a-pkg"))
    a.enable(D)
    v1_module = sys.modules["hello_world.plugin"]
    p = b.prepare_install(fixture_copy(tmp_path, "v2", name="b-pkg"))
    assert p.runs_as == "subprocess"
    b.install(p.source, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    b.enable(D)
    assert wait_for(lambda: b.status(D) == "running")
    assert isinstance(b.running(D), SubprocessPlugin)
    assert sys.modules["hello_world.plugin"] is v1_module
    assert v1_module.HelloWorld.version == "1.0.0"


def test_r19_a_collision_found_only_at_enable_is_raised_then(tmp_path):
    """[r19] B installed before A loaded the package: B's enable is raised."""
    a_host, a, b_host, b = _two_hosts(tmp_path)
    install(b, fixture_copy(tmp_path, "v1", name="b-pkg"))
    assert b.record(D)["consented_runs_as"] == "inprocess"
    install(a, fixture_copy(tmp_path, "v1", name="a-pkg"))
    a.enable(D)
    b.enable(D)
    assert isinstance(b.running(D), SubprocessPlugin)
    assert b.record(D)["runs_as"] == "subprocess"


def test_r19_never_below_the_consented_level_after_the_holder_leaves(tmp_path):
    """[r19 with r12] once consent showed subprocess, the plug-in never drops to
    inprocess without fresh consent, even after the holder releases."""
    a_host, a, b_host, b = _two_hosts(tmp_path)
    install(a, fixture_copy(tmp_path, "v1", name="a-pkg"))
    a.enable(D)
    install(b, fixture_copy(tmp_path, "v1", name="b-pkg"))
    assert b.record(D)["consented_runs_as"] == "subprocess"
    a.close()
    assert lifecycle.holder_of("hello_world") is None
    b.enable(D)
    assert isinstance(b.running(D), SubprocessPlugin)


def test_r19_a_host_without_subprocess_support_refuses_naming_the_holder(tmp_path):
    a_host, a, b_host, b = _two_hosts(tmp_path)
    b_host.isolation_levels = frozenset({"inprocess"})
    install(a, fixture_copy(tmp_path, "v1", name="a-pkg"))
    a.enable(D)
    with pytest.raises(PluginContractRefusal) as ei:
        b.prepare_install(fixture_copy(tmp_path, "v1", name="b-pkg"))
    print(ei.value)
    assert "already loaded in this process by plug-in 'hello_world'" in str(ei.value)
    assert "cannot run it at the subprocess level" in str(ei.value)


def test_r19_closing_or_uninstalling_one_host_never_purges_the_other_hosts_modules(tmp_path):
    a_host, a, b_host, b = _two_hosts(tmp_path)
    install(a, fixture_copy(tmp_path, "v1", name="a-pkg"))
    a.enable(D)
    held = sys.modules["hello_world.plugin"]
    install(b, fixture_copy(tmp_path, "v1", name="b-pkg"))
    b.enable(D)
    b.uninstall(D)                         # B's uninstall purges only what B holds
    assert sys.modules.get("hello_world.plugin") is held
    assert lifecycle.holder_of("hello_world") == (id(a), D)
    b.close()
    assert sys.modules.get("hello_world.plugin") is held
    assert len(_pongs(a_host.bus())) == 1


def test_r19_host_owned_and_stdlib_and_host_provided_are_still_refused(tmp_path):
    """[r19] the 'already loaded' refusal still covers the host's own modules."""
    host, pm = make(tmp_path)
    for name in ("pydantic", "json", "composebus"):
        pkg = fixture_copy(tmp_path, "v1", name=f"p-{name}",
                           edit=lambda d, n=name: d["run"].update(entry=f"{n}.plugin:HelloWorld"))
        with pytest.raises(ManifestRefused):
            pm.prepare_install(pkg)


def test_r19_same_host_upgrade_is_not_a_collision(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    p = pm.prepare_upgrade(fixture_copy(tmp_path, "v2"))
    assert p.runs_as == "inprocess"


def test_r19_an_inprocess_only_host_refuses_a_subprocess_floor(tmp_path):
    """[r19 details (a)] a level the host lacks is refused, naming it."""
    host, pm = make(tmp_path)
    host.isolation_levels = frozenset({"inprocess"})
    with pytest.raises(PluginContractRefusal) as ei:
        pm.prepare_install(fixture_copy(tmp_path, "v1",
                                        edit=lambda d: d.update(isolation_floor="subprocess")))
    print(ei.value)
    assert "supports only ['inprocess']" in str(ei.value)


def test_r19_a_subprocess_only_host_runs_everything_at_subprocess(tmp_path):
    """[r19 details (a)] {subprocess} (Gnomon's set): never in-process, and the
    consent screen says so."""
    from compose_plugins.host import Host
    host, pm = make(tmp_path)
    host.isolation_levels = frozenset({"subprocess"})
    p = pm.prepare_install(fixture_copy(tmp_path, "v1"))
    assert p.runs_as == "subprocess"
    assert ("runs as", "subprocess") in [(ln.label, ln.value) for ln in p.content.lines]
    pm.install(p.source, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    pm.enable(D)
    assert isinstance(pm.running(D), SubprocessPlugin)
    assert "hello_world.plugin" not in sys.modules or lifecycle.holder_of("hello_world") is None
    with pytest.raises(PluginContractRefusal):
        Host("x", state_root=tmp_path / "x", isolation_levels=["container"])
    with pytest.raises(PluginContractRefusal):
        Host("x", state_root=tmp_path / "y", isolation_levels=[])


def test_r19_own_earlier_load_is_not_a_collision_reenable_and_upgrade_stay_inprocess(tmp_path):
    """[r19 details (b)] re-enable and upgrade in the SAME host stay in-process."""
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    assert isinstance(pm.running(D), InprocessPlugin)
    pm.disable(D)
    pm.enable(D)
    assert isinstance(pm.running(D), InprocessPlugin)
    v2 = fixture_copy(tmp_path, "v2")
    p = pm.prepare_upgrade(v2)
    assert p.runs_as == "inprocess"
    pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert isinstance(pm.running(D), InprocessPlugin) and pm.record(D)["runs_as"] == "inprocess"
    assert sys.modules["hello_world.plugin"].HelloWorld.version == "2.0.0"
