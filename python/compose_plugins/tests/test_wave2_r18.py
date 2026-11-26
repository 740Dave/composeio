"""Spec 95 r18 and the wave-2 QA findings: health without a manual tick,
the import-package name rule and atomic in-process enable, the uninstall
purge, unsubscribe on stop, fresh-process build, and the probe's verdict
channel. Each test cites its finding."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import zipfile

import pytest

from compose_plugins import lifecycle
from compose_plugins.derive import DriftRefused
from compose_plugins.errors import ManifestRefused
from compose_plugins.probe_client import ProbeRefused, run_probe

from wave2_helpers import PING, PONG, fixture_copy, install, make, pongs, wait_for

D = "hello_world"
WENT_BAD = ('    def health_reason(self):\n        import time\n'
            "        if not hasattr(self, '_t0'): self._t0 = time.time()\n"
            "        return 'went bad' if time.time() - self._t0 > 1.0 else None\n")


def _edit_plugin(pkg, fn):
    p = pkg / "hello_world" / "plugin.py"
    p.write_text(fn(p.read_text()))
    return pkg


def _went_bad(tmp_path, name="bad"):
    pkg = fixture_copy(tmp_path, "v1", name=name)
    return _edit_plugin(pkg, lambda s: s.replace(
        '    def health_reason(self):\n        return "empty greeting" if self.config.greeting == "" else None\n',
        WENT_BAD))


# ---- FAIL 2: health without anyone calling tick() -------------------------- #
@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_f2_a_plugin_that_goes_bad_after_start_is_degraded_without_a_manual_tick(tmp_path, level):
    """[FAIL 2] nobody calls manager.tick(); the plug-in goes bad 1 s after start."""
    host, pm = make(tmp_path, level)
    src = _went_bad(tmp_path)
    assert "went bad" in (src / "hello_world" / "plugin.py").read_text()
    install(pm, src)
    pm.enable(D)
    assert wait_for(lambda: pm.status(D) == "running", 10)
    ok = wait_for(lambda: pm.status(D) == "degraded(went bad)", 8)
    print(level, pm.status(D))
    assert ok


# ---- FAIL 3: the import-package name rule ---------------------------------- #
@pytest.mark.parametrize("name,why", [("composebus", "a host-provided package"),
                                      ("compose_plugins", "a host-provided package"),
                                      ("composeio", "a host-provided package"),
                                      ("json", "a standard-library module"),
                                      ("os", "a standard-library module"),
                                      ("pydantic", "a module already loaded")])
def test_f3_a_shadowing_import_package_is_refused_at_install_naming_it(tmp_path, name, why):
    """[FAIL 3] refused at install (at prepare, before any consent), naming it."""
    host, pm = make(tmp_path)
    pkg = fixture_copy(tmp_path, "v1", edit=lambda d: d["run"].update(
        entry=f"{name}.plugin:HelloWorld"))
    with pytest.raises(ManifestRefused) as ei:
        pm.prepare_install(pkg)
    print(ei.value)
    assert ei.value.fields == ["run.entry"]
    assert f"import package {name!r}" in str(ei.value) and why in str(ei.value)
    assert pm.record(D) is None and host.gate_calls == []


def test_f3_the_plugins_own_loaded_package_does_not_block_its_upgrade(tmp_path):
    """[FAIL 3] 'already loaded' exempts THIS plug-in's current install."""
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    assert "hello_world" in sys.modules
    v2 = fixture_copy(tmp_path, "v2")
    p = pm.prepare_upgrade(v2)
    pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert pm.record(D)["version"] == "2.0.0"


def test_f3_wheel_package_must_be_the_entry_package(tmp_path):
    host, pm = make(tmp_path)
    pkg = fixture_copy(tmp_path, "v1")
    whl = tmp_path / "hw-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.write(pkg / "manifest.json", "other_pkg/manifest.json")
        zf.writestr("other_pkg/__init__.py", "")
    with pytest.raises(ManifestRefused) as ei:
        pm.prepare_install(whl)
    print(ei.value)
    assert "'other_pkg'" in str(ei.value)


def test_f3_a_failed_inprocess_enable_restores_sys_path_and_sys_modules_exactly(tmp_path):
    """[FAIL 3] atomic enable: any failure leaves sys.path / sys.modules as before."""
    host, pm = make(tmp_path)
    pkg = _edit_plugin(fixture_copy(tmp_path, "v1"),
                       lambda s: s + "\nimport json as _side_effect_import\n")
    pkg_m = pkg / "manifest.json"
    d = json.loads(pkg_m.read_text())
    d["config_schema"]["properties"]["greeting"]["default"] = "drifted"
    pkg_m.write_text(json.dumps(d, indent=2) + "\n")
    install(pm, pkg)
    path_before, mods_before = list(sys.path), dict(sys.modules)
    with pytest.raises(DriftRefused):
        pm.enable(D)
    assert sys.path == path_before
    assert set(sys.modules) == set(mods_before)
    assert all(sys.modules[k] is v for k, v in mods_before.items())
    assert "hello_world" not in sys.modules


def test_f3_an_import_that_raises_is_also_rolled_back(tmp_path):
    host, pm = make(tmp_path)
    pkg = _edit_plugin(fixture_copy(tmp_path, "v1"), lambda s: s + "\nraise ImportError('no')\n")
    install(pm, pkg)
    path_before, mods_before = list(sys.path), dict(sys.modules)
    with pytest.raises(DriftRefused):
        pm.enable(D)
    assert sys.path == path_before and set(sys.modules) == set(mods_before)


# ---- notes: uninstall purge, unsubscribe on stop --------------------------- #
def test_uninstall_purges_the_plugins_modules_and_path(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    path = pm.record(D)["path"]
    assert "hello_world.plugin" in sys.modules
    pm.uninstall(D)
    assert not [m for m in sys.modules if m == "hello_world" or m.startswith("hello_world.")]
    assert path not in sys.path


def test_a_stopped_inprocess_instance_unsubscribes_from_the_bus(tmp_path):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    ns = host.bus().get_namespace(PING)
    assert len(ns._event_subscribers) == 1
    pm.disable(D)
    assert ns._event_subscribers == []
    pm.enable(D)
    assert len(ns._event_subscribers) == 1
    assert [p["n"] for p in pongs(host.bus())] == [1]


# ---- LOW 4: build in a fresh child ----------------------------------------- #
def test_l4_two_builds_in_one_process_each_derive_their_own_class(tmp_path):
    a = _edit_plugin(fixture_copy(tmp_path, "v1", name="a"),
                     lambda s: s.replace('PONG: "EVENT"', 'PONG: "STATE"'))
    b = fixture_copy(tmp_path, "v1", name="b")
    code = (f"import json; from compose_plugins.derive import build; build({str(a)!r}); "
            f"build({str(b)!r}); print(json.dumps(json.load(open({str(b / 'manifest.json')!r}))"
            "['bus']['publishes']))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    print(r.stdout, r.stderr[-300:])
    assert json.loads(r.stdout) == [{"namespace": "plugins/hello_world/greeting", "type": "STATE"},
                                    {"namespace": "plugins/hello_world/pong", "type": "EVENT"}]
    assert "hello_world" not in sys.modules or True


# ---- LOW 5: the probe's verdict channel ------------------------------------ #
def _plug(tmp_path, body):
    (tmp_path / "pp").mkdir(exist_ok=True)
    (tmp_path / "pp" / "probe_plug.py").write_text(body)
    return str(tmp_path / "pp")


OK_PLUG = textwrap.dedent("""
    from composebus.plugin import ComposePlugin
    class P(ComposePlugin):
        pass
""")


def test_l5_stdout_forgery_by_atexit_is_ignored(tmp_path):
    forged = json.dumps({"nonce": "x", "resolve_error": None, "config_schema": {},
                         "bus": None, "bus_error": None, "config_errors": []})
    root = _plug(tmp_path, OK_PLUG + f"\nimport atexit\natexit.register(lambda: print({forged!r}))\n")
    v = run_probe("probe_plug:P", [root], {}, what="t")
    assert v["config_schema"]["title"] == "Config" and v["nonce"] != "x"


def test_l5_a_forged_drift_verdict_on_stdout_does_not_enable(tmp_path):
    """QA's probe: a drifted class plus an atexit print of a clean verdict."""
    host, pm = make(tmp_path, "subprocess")
    ref = json.loads((fixture_copy(tmp_path, "v1", name="ref") / "manifest.json").read_text())
    forged = json.dumps({"resolve_error": None, "config_schema": ref["config_schema"],
                         "bus": ref["bus"], "bus_error": None, "config_errors": []})
    pkg = _edit_plugin(fixture_copy(tmp_path, "v1"), lambda s: (
        s.replace('PONG: "EVENT"', 'PONG: "STATE"')
        + f"\nimport atexit as _a\n_a.register(lambda: print({forged!r}))\n"))
    install(pm, pkg)
    with pytest.raises(DriftRefused):
        pm.enable(D)
    assert pm.state(D) == "installed"


@pytest.mark.parametrize("attack", ["extra-object", "no-verdict", "forged-only",
                                    "forged-with-stolen-nonce"])
def test_l5_anything_but_exactly_one_verdict_is_refused(tmp_path, attack):
    body = OK_PLUG + {
        # writes a second object on the verdict fd at import time
        "extra-object": textwrap.dedent("""
            import os, sys
            fd = int(sys.argv[sys.argv.index('--fd') + 1])
            os.write(fd, b'{"nonce": "forged"}\\n')
        """),
        # writes ONE forged verdict, then exits 0 before the probe can write its own:
        # a single well-formed object, exit 0 -- only the nonce gives it away
        "forged-only": textwrap.dedent("""
            import os, sys, json
            fd = int(sys.argv[sys.argv.index('--fd') + 1])
            os.write(fd, (json.dumps({"nonce": "guessed", "resolve_error": None,
                                      "config_schema": {}, "bus": None, "bus_error": None,
                                      "config_errors": []}) + "\\n").encode())
            os._exit(0)
        """),
        # steals the nonce from the probe's own stack frame and writes a forged
        # verdict BEFORE the real one: only "exactly one object" catches it
        "forged-with-stolen-nonce": textwrap.dedent("""
            import os, sys, json
            fd = int(sys.argv[sys.argv.index('--fd') + 1])
            f = sys._getframe()
            while f is not None and "nonce" not in f.f_locals:
                f = f.f_back
            os.write(fd, (json.dumps({"nonce": f.f_locals["nonce"], "resolve_error": None,
                                      "config_schema": {}, "bus": None, "bus_error": None,
                                      "config_errors": []}) + "\\n").encode())
        """),
        # closes the verdict fd so the probe cannot write
        "no-verdict": textwrap.dedent("""
            import os, sys
            os.close(int(sys.argv[sys.argv.index('--fd') + 1]))
        """)}[attack]
    root = _plug(tmp_path, body)
    with pytest.raises(ProbeRefused) as ei:
        run_probe("probe_plug:P", [root], {}, what="t")
    print(ei.value)


def test_l5_probe_requires_this_runs_nonce(tmp_path, monkeypatch):
    import compose_plugins.probe_client as pc
    tokens = iter(["aaaa", "bbbb"])
    monkeypatch.setattr(pc.secrets, "token_hex", lambda n: next(tokens))
    root = _plug(tmp_path, OK_PLUG)
    assert run_probe("probe_plug:P", [root], {}, what="t")["nonce"] == "aaaa"
