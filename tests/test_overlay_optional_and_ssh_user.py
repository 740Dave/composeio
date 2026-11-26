"""Optional overlay content says so, and deploy logins come from the environment.

Three public callers named overlay-only content (the BSG 2-up `bsg_app.compose.yaml`, which ships in
the private bsg overlay) and failed with a file-not-found when it was absent. The fleet HUD guessed
its soak log names and probed a placeholder firewall address, so an unconfigured HUD showed a blank
panel and a false "unreachable". Each ABSENT path must now state its message; each test below fails
on the code before this change.

The deploy scripts defaulted the SSH login to a literal user name. They now default to the invoking
user (`$USER` in shell, `getpass.getuser()` in Python), and every override still wins.

Hermetic: no ssh, no broker, no window. Shell scripts run with a fake `python3` first on PATH and a
temporary repo where they need one.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import stat
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))
from composeio.render.engine.bindings import substitute_variables  # noqa: E402
from composeio.render.engine.runtime import _variable_overrides_from_env  # noqa: E402

CAROUSEL = REPO / "compose_projects" / "carousel"
NOT_INSTALLED = "BSG overlay not installed"


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)
    elif isinstance(node, str):
        yield node


def _bsg_hint(env):
    """The BSG tile's hint label text, as the launcher scene resolves under `env`."""
    cfg = yaml.safe_load((CAROUSEL / "launcher.compose.yaml").read_text())
    scene = substitute_variables(cfg, _variable_overrides_from_env(env))
    tiles = [t for t in scene["widgets"][0]["tiles"] if t.get("target") == "bsg"]
    assert len(tiles) == 1
    labels = [c for c in tiles[0]["widget"]["children"] if c.get("type") == "label"]
    assert len(labels) == 1
    return labels[0]["text"]


class _Runner:
    def __init__(self):
        self.current, self.shows = None, []

    def show(self, scene, *, force=False):
        self.current = scene
        self.shows.append(os.path.basename(scene))
        return f"showing {os.path.basename(scene)}"

    def alive(self):
        return True

    def stop(self):
        self.current = None


# --- 7c-L6: the kiosk --------------------------------------------------------------------------------
def test_kiosk_bsg_absent_is_stated_and_spawns_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPOSEIO_BSG_SCENE", str(tmp_path / "absent.compose.yaml"))
    kiosk = _load("compose_projects/carousel/kiosk.py", "_kiosk_absent")
    logs = []
    runner = _Runner()
    sup = kiosk.KioskSupervisor(runner=runner, log=logs.append,
                                power=kiosk.PowerAction(allowed=lambda: False))
    sup.boot()
    res = sup.route("bsg")
    assert res["action"] == "unavailable"
    assert res["reason"] == NOT_INSTALLED
    assert runner.shows == ["launcher.compose.yaml"]          # stayed on the carousel
    assert any(NOT_INSTALLED in m and "COMPOSEIO_BSG_SCENE" in m for m in logs)


def test_kiosk_bsg_present_resolves_through_the_env_name(monkeypatch, tmp_path):
    scene = tmp_path / "bsg_app.compose.yaml"
    scene.write_text("app: {id: stub}\n")
    monkeypatch.setenv("COMPOSEIO_BSG_SCENE", str(scene))
    kiosk = _load("compose_projects/carousel/kiosk.py", "_kiosk_present")
    runner = _Runner()
    sup = kiosk.KioskSupervisor(runner=runner, log=lambda m: None)
    res = sup.route("bsg")
    assert res["action"] == "show" and runner.current == str(scene)


def test_kiosk_default_path_is_the_overlay_location(monkeypatch):
    monkeypatch.delenv("COMPOSEIO_BSG_SCENE", raising=False)
    kiosk = _load("compose_projects/carousel/kiosk.py", "_kiosk_default")
    assert kiosk.bsg_scene_path() == str(CAROUSEL / "bsg_app.compose.yaml")


def test_launcher_tile_says_not_installed_when_the_overlay_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPOSEIO_BSG_SCENE", str(tmp_path / "absent.compose.yaml"))
    kiosk = _load("compose_projects/carousel/kiosk.py", "_kiosk_tile_absent")
    env = kiosk.SceneRunner(spawn=lambda s, e: None)._child_env(
        str(CAROUSEL / "launcher.compose.yaml"))
    assert _bsg_hint(env) == NOT_INSTALLED
    assert _bsg_hint({}) == NOT_INSTALLED     # outside the kiosk nothing routes the tap


def test_launcher_tile_says_tap_to_launch_when_the_overlay_is_present(monkeypatch, tmp_path):
    scene = tmp_path / "bsg_app.compose.yaml"
    scene.write_text("app: {id: stub}\n")
    monkeypatch.setenv("COMPOSEIO_BSG_SCENE", str(scene))
    monkeypatch.delenv("COMPOSEIO_VAR_BSG_TILE_HINT", raising=False)
    kiosk = _load("compose_projects/carousel/kiosk.py", "_kiosk_tile_present")
    runner = kiosk.SceneRunner(spawn=lambda s, e: None)
    assert _bsg_hint(runner._child_env(str(CAROUSEL / "launcher.compose.yaml"))) == "tap to launch"
    # the override rides ONLY the launcher child, never the 2-up
    assert "COMPOSEIO_VAR_BSG_TILE_HINT" not in runner._child_env(str(scene))


# --- 7c-L6: start-colonial-ux.sh ---------------------------------------------------------------------
def _fake_python(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    record = tmp_path / "python3.calls"
    shim = bindir / "python3"
    shim.write_text(f'#!/bin/sh\necho "$@" >> "{record}"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return bindir, record


def test_colonial_2up_absent_overlay_is_stated_not_launched(tmp_path):
    bindir, record = _fake_python(tmp_path)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", NO_CLOCK="1",
               LOG_DIR=str(tmp_path / "logs"),
               COMPOSEIO_BSG_SCENE=str(tmp_path / "absent.compose.yaml"))
    r = subprocess.run(["bash", str(CAROUSEL / "start-colonial-ux.sh")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert NOT_INSTALLED in r.stdout
    assert not record.exists(), record.read_text()   # nothing was launched


# --- 7c-L6 + 7f: deploy_rpi5.sh --plan (local preflight, no ssh) ---------------------------------------
def _fixture_repo(tmp_path, with_bsg):
    root = tmp_path / "repo"
    car = root / "compose_projects" / "carousel"
    car.mkdir(parents=True)
    (root / "python").mkdir()
    shutil.copy2(CAROUSEL / "deploy_rpi5.sh", car / "deploy_rpi5.sh")
    (car / "deploy.manifest").write_text("python/\ncompose_projects/carousel/\n")
    for f in ("kiosk.py", "launcher.compose.yaml"):
        (car / f).write_text("# stub\n")
    if with_bsg:
        (car / "bsg_app.compose.yaml").write_text("app: {id: stub}\n")
    return car / "deploy_rpi5.sh"


def _plan(script, **env_over):
    env = {k: v for k, v in os.environ.items()
           if k not in ("RPI_HOST", "RESTART_TARGETS", "COMPOSEIO_BSG_SCENE")}
    env.update(env_over)
    # no ssh: any attempt would be a failure of the --plan contract, so make it fail loudly
    return subprocess.run(["bash", str(script), "--plan"], env=env, capture_output=True,
                          text=True, timeout=60)


def test_deploy_preflight_says_bsg_overlay_not_installed(tmp_path):
    r = _plan(_fixture_repo(tmp_path, with_bsg=False), USER="deployer")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert NOT_INSTALLED in out
    assert "restart targets: launcher\n" in out.replace("\x1b[0m", "")
    assert "bsg_app.compose.yaml" not in out.split("manifest closure")[1].split("\n")[0]


def test_deploy_preflight_ships_the_overlay_scene_when_present(tmp_path):
    r = _plan(_fixture_repo(tmp_path, with_bsg=True), USER="deployer")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert NOT_INSTALLED not in out
    closure = out.split("manifest closure")[1].split("\n")[0]
    assert "compose_projects/carousel/bsg_app.compose.yaml" in closure
    assert "restart targets: launcher bsg" in out


def test_deploy_ssh_login_defaults_to_the_invoking_user(tmp_path):
    script = _fixture_repo(tmp_path, with_bsg=False)
    r = _plan(script, USER="deployer")
    assert r.returncode == 0, r.stderr
    assert "target: deployer@display-node.local:" in r.stdout
    r = _plan(script, USER="deployer", RPI_HOST="ops@kiosk.example")   # the override still wins
    assert "target: ops@kiosk.example:" in r.stdout


def _shell_assignments(path, names, env):
    """Evaluate only the named `NAME=...` assignment lines of a deploy script, under `env`."""
    lines = [ln for ln in path.read_text().splitlines()
             if any(ln.startswith(f"{n}=") for n in names)]
    assert len(lines) == len(names), lines
    body = "\n".join(lines + [f'echo "{n}=${{{n}}}"' for n in names])
    r = subprocess.run(["bash", "-euc", body], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return dict(ln.split("=", 1) for ln in r.stdout.splitlines())


def test_node_red_deploy_login_defaults_to_the_invoking_user():
    path = REPO / "services" / "node-red-composeio" / "build-and-deploy.sh"
    base = {"PATH": os.environ["PATH"], "USER": "deployer"}
    got = _shell_assignments(path, ("REMOTE_USER", "REMOTE_HOST"), base)
    assert got == {"REMOTE_USER": "deployer", "REMOTE_HOST": "mqtt-broker.local"}
    got = _shell_assignments(path, ("REMOTE_USER", "REMOTE_HOST"),
                             dict(base, NODERED_USER="ops", NODERED_HOST="nr.example"))
    assert got == {"REMOTE_USER": "ops", "REMOTE_HOST": "nr.example"}


# --- 7c-L7 + 7f: the fleet HUD -----------------------------------------------------------------------
_FLEET_NAMES = ("FLEET_SOAK_NODE_NAME", "FLEET_BENCH_NAME", "FLEET_FW_STATUS_URL",
                "FLEET_SOAK_SSH_USER")


def _hud(monkeypatch, name, **env):
    for k in _FLEET_NAMES:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    hud = _load("compose_projects/fleet-hud/fleet_hud.py", name)
    calls = []
    hud._sh = lambda cmd, timeout=20: calls.append(cmd) or ""
    return hud, calls


def test_soak_panel_says_not_configured_without_the_overlay_names(monkeypatch):
    hud, calls = _hud(monkeypatch, "_hud_soak_unset")
    vals = hud.poll()
    assert "not configured" in vals["soak_config"]
    assert "FLEET_SOAK_NODE_NAME" in vals["soak_config"] and "FLEET_BENCH_NAME" in vals["soak_config"]
    assert not any("node_" in c or "iperf_" in c for c in calls)   # no guessed log names
    soak = yaml.safe_load((REPO / "compose_projects/fleet-hud/fleet-540-soak.compose.yaml").read_text())
    assert "soak_config" in [n["name"] for n in soak["namespaces"]]
    assert any(isinstance(w.get("text"), dict) and w["text"].get("namespace") == "soak_config"
               for m in soak["modules"] for w in m["widgets"])


def test_soak_panel_tails_the_configured_logs(monkeypatch):
    hud, calls = _hud(monkeypatch, "_hud_soak_set", FLEET_SOAK_NODE_NAME="n9", FLEET_BENCH_NAME="b9")
    vals = hud.poll()
    assert "not configured" not in vals["soak_config"]
    assert any("node_n9.jsonl" in c and "iperf_b9.jsonl" in c for c in calls)


def test_firewall_probe_unset_says_not_configured_never_unreachable(monkeypatch):
    hud, calls = _hud(monkeypatch, "_hud_fw_unset")
    res = hud._act_fw_status({})
    assert "not configured" in res["summary"] and "FLEET_FW_STATUS_URL" in res["summary"]
    assert "unreachable" not in res["summary"].lower()
    assert res.get("not_configured") is True
    assert calls == []                                             # nothing was probed


def test_firewall_probe_uses_the_configured_target(monkeypatch):
    hud, calls = _hud(monkeypatch, "_hud_fw_set",
                      FLEET_FW_STATUS_URL="https://fw.example/api/core/system/status")
    hud._act_fw_status({})
    assert len(calls) == 1 and "https://fw.example/api/core/system/status" in calls[0]


def test_command_widget_shows_not_configured_in_amber_not_red():
    from composeio.render.widgets.command import CommandWidget
    w = CommandWidget("fw", label="540 status", binding={
        "type": "mcp_action", "tool": "fleet540.fw_status", "result_namespace": "velo540_action"})
    published = []
    w.publish = lambda ns, v: published.append((ns, v))
    w._apply_event({"kind": "result", "ok": False, "not_configured": True,
                    "summary": "540 status not configured: set FLEET_FW_STATUS_URL"})
    assert w._status_text == "not configured"
    assert published == [("velo540_action", "yellow")]


def test_soak_tunnel_login_defaults_to_the_invoking_user(monkeypatch):
    monkeypatch.setenv("LOGNAME", "deployer")
    monkeypatch.setenv("USER", "deployer")
    hud, _ = _hud(monkeypatch, "_hud_user_default")
    assert hud._FLEETNODE.endswith("-p 22222 deployer@localhost")
    hud, _ = _hud(monkeypatch, "_hud_user_override", FLEET_SOAK_SSH_USER="ops")
    assert hud._FLEETNODE.endswith("-p 22222 ops@localhost")


# --- review follow-ups (P2-1, P2-2, P3) ----------------------------------------------------------------
def test_deploy_plan_passes_preflight_on_this_checkout_with_the_overlay_absent(tmp_path):
    """The REAL manifest of this checkout, not a fixture: every entry must exist in a public clone, so
    `--plan` gets past preflight. (`fonts/` used to be listed; it moved to compose_resources/fonts/.)"""
    r = _plan(CAROUSEL / "deploy_rpi5.sh", USER="deployer",
              COMPOSEIO_BSG_SCENE=str(tmp_path / "absent.compose.yaml"))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "manifest path missing locally" not in out
    assert NOT_INSTALLED in out
    assert "plan complete" in out


def test_kiosk_font_dir_default_is_a_shipped_directory():
    kiosk = _load("compose_projects/carousel/kiosk.py", "_kiosk_fonts")
    env = kiosk.SceneRunner(spawn=lambda s, e: None)._child_env()
    font_dir = pathlib.Path(env["COMPOSEIO_FONT_DIR"])
    if "COMPOSEIO_FONT_DIR" not in os.environ:
        assert (font_dir / "IBMPlexSans-Regular.ttf").is_file()
        rel = font_dir.relative_to(REPO).as_posix() + "/"
        manifest = (CAROUSEL / "deploy.manifest").read_text().splitlines()
        assert rel in manifest


def test_publish_exclude_refuses_the_bsg_scene():
    entries = [ln.split("#", 1)[0].strip()
               for ln in (REPO / "PUBLISH-EXCLUDE").read_text().splitlines()]
    assert "compose_projects/carousel/bsg_app.compose.yaml" in entries


def test_quoted_tilde_in_bsg_scene_matches_kiosk_in_both_scripts(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "ov").mkdir(parents=True)
    (home / "ov" / "bsg_app.compose.yaml").write_text("app: {id: stub}\n")
    quoted = "~/ov/bsg_app.compose.yaml"                   # as a quoted export leaves it
    monkeypatch.setenv("HOME", str(home))                  # expanduser reads HOME
    kiosk = _load("compose_projects/carousel/kiosk.py", "_kiosk_tilde")
    assert kiosk.bsg_installed(env={"COMPOSEIO_BSG_SCENE": quoted})
    # deploy_rpi5.sh: the scene is found (outside the fixture repo, so not shipped, but not "absent")
    r = _plan(_fixture_repo(tmp_path, with_bsg=False), USER="deployer", HOME=str(home),
              COMPOSEIO_BSG_SCENE=quoted)
    assert r.returncode == 0, r.stderr
    assert NOT_INSTALLED not in r.stdout + r.stderr
    assert str(home / "ov" / "bsg_app.compose.yaml") in r.stdout + r.stderr
    # start-colonial-ux.sh: the 2-up launches with the expanded path
    bindir, record = _fake_python(tmp_path)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", NO_CLOCK="1", HOME=str(home),
               LOG_DIR=str(tmp_path / "logs"), COMPOSEIO_BSG_SCENE=quoted)
    r = subprocess.run(["bash", str(CAROUSEL / "start-colonial-ux.sh")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert NOT_INSTALLED not in r.stdout
    assert str(home / "ov" / "bsg_app.compose.yaml") in record.read_text()


def test_command_widget_not_configured_needs_a_real_true():
    from composeio.render.widgets.command import CommandWidget
    w = CommandWidget("fw", label="540 status", binding={
        "type": "mcp_action", "tool": "fleet540.fw_status", "result_namespace": "velo540_action"})
    published = []
    w.publish = lambda ns, v: published.append((ns, v))
    w._apply_event({"kind": "result", "ok": False, "not_configured": "false", "summary": "down"})
    assert w._status_text == "failed ✗"
    assert published == [("velo540_action", "red")]
