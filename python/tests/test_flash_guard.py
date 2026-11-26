"""Slice 7d — the flash identity gate fails CLOSED and names no device.

No hardware: every port list is a fake, and the passive enumerator is patched so a
test can never touch the USB stack. Nothing here opens a serial port or runs pio.

What is proven:
  * the library refuses with the deny-list unset/empty, naming FLASH_DENY_SERIALS,
    BEFORE it enumerates any port; refuses a denied serial and allows another;
    enforces a per-board expected serial; refuses auto-detect and port-less uploads.
  * the PlatformIO hook refuses an upload with the deny-list unset, leaves a plain
    build alone, and fails a live build whose baked overlay values are unset.
  * every device-writing env in every platformio.ini loads the hook.
  * firmware/r503_probe/build.sh refuses before copying, building or resolving.
  * no guard-bearing file carries a pseudonymous MAC or serial placeholder.
"""
from __future__ import annotations

import configparser
import importlib.util
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "python" / "composeio" / "flash_guard.py"
HOOK = REPO / "python" / "scripts" / "pio_flash_guard.py"
BUILD_SH = REPO / "firmware" / "r503_probe" / "build.sh"
R503PROBE = REPO / "docs" / "r503" / "probe-archive" / "r503probe.py"

TEST_DENIED = "AA:BB:CC:00:00:01"     # test values only; never a real device
TEST_OTHER = "AA:BB:CC:00:00:02"


def _load(path: Path, name: str):
    assert path.is_file(), f"{path} does not exist"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclasses resolve their module via sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fg():
    return _load(LIB, "flash_guard_under_test")


@pytest.fixture()
def ports(fg):
    return [fg.PortInfo("/dev/cu.usbmodemDENY", TEST_DENIED),
            fg.PortInfo("/dev/cu.usbmodemOK", TEST_OTHER),
            fg.PortInfo("/dev/cu.Bluetooth-Incoming-Port", None)]


# --------------------------------------------------------------------------- library

@pytest.mark.parametrize("deny", [None, "", "   ", " , ;"])
def test_unset_or_empty_deny_list_refuses_before_enumerating(fg, monkeypatch, deny):
    def boom():
        raise AssertionError("enumerated ports before checking the overlay")
    monkeypatch.setattr(fg, "list_ports_passive", boom)
    environ = {} if deny is None else {"FLASH_DENY_SERIALS": deny}
    with pytest.raises(fg.FlashRefused, match="FLASH_DENY_SERIALS"):
        fg.check_flash("/dev/cu.usbmodemOK", environ)


def test_denied_serial_refused_other_allowed(fg, ports):
    env = {"FLASH_DENY_SERIALS": TEST_DENIED}
    with pytest.raises(fg.FlashRefused, match="never flash"):
        fg.check_flash("/dev/cu.usbmodemDENY", env, ports=ports)
    assert fg.check_flash("/dev/cu.usbmodemOK", env, ports=ports) == TEST_OTHER


def test_deny_match_is_case_and_separator_insensitive(fg, ports):
    env = {"FLASH_DENY_SERIALS": "zz:zz, " + TEST_DENIED.lower() + " ;"}
    with pytest.raises(fg.FlashRefused):
        fg.check_flash("/dev/tty.usbmodemDENY", env, ports=ports)   # tty. == cu.


def test_expect_var_unset_mismatch_and_match(fg, ports):
    env = {"FLASH_DENY_SERIALS": TEST_DENIED}
    with pytest.raises(fg.FlashRefused, match="BOARD_EXPECT_SERIAL"):
        fg.check_flash("/dev/cu.usbmodemOK", env, ports=ports, expect_var="BOARD_EXPECT_SERIAL")
    env["BOARD_EXPECT_SERIAL"] = "AA:BB:CC:00:00:09"
    with pytest.raises(fg.FlashRefused, match="different board"):
        fg.check_flash("/dev/cu.usbmodemOK", env, ports=ports, expect_var="BOARD_EXPECT_SERIAL")
    env["BOARD_EXPECT_SERIAL"] = TEST_OTHER
    assert fg.check_flash("/dev/cu.usbmodemOK", env, ports=ports,
                          expect_var="BOARD_EXPECT_SERIAL") == TEST_OTHER


def test_expected_board_on_deny_list_is_still_refused(fg, ports):
    env = {"FLASH_DENY_SERIALS": TEST_DENIED, "BOARD_EXPECT_SERIAL": TEST_DENIED}
    with pytest.raises(fg.FlashRefused, match="never flash"):
        fg.check_flash("/dev/cu.usbmodemDENY", env, ports=ports, expect_var="BOARD_EXPECT_SERIAL")


@pytest.mark.parametrize("port", ["", "   ", None])
def test_autodetect_refused(fg, ports, port):
    with pytest.raises(fg.FlashRefused, match="no explicit upload port"):
        fg.check_flash(port, {"FLASH_DENY_SERIALS": TEST_DENIED}, ports=ports)


def test_absent_port_and_serial_less_port_refused(fg, ports):
    env = {"FLASH_DENY_SERIALS": TEST_DENIED}
    with pytest.raises(fg.FlashRefused, match="not a present serial port"):
        fg.check_flash("/dev/cu.usbmodemGONE", env, ports=ports)
    with pytest.raises(fg.FlashRefused, match="no USB serial"):
        fg.check_flash("/dev/cu.Bluetooth-Incoming-Port", env, ports=ports)


def test_port_var_unset_refused(fg, ports):
    with pytest.raises(fg.FlashRefused, match="OFFICE_RELAY_SERIAL_PORT"):
        fg.check_flash("", {"FLASH_DENY_SERIALS": TEST_DENIED}, ports=ports,
                       port_var="OFFICE_RELAY_SERIAL_PORT")


@pytest.mark.parametrize("proto", ["wchisp", "esp-builtin", "stlink"])
def test_portless_protocol_refused(fg, ports, proto):
    with pytest.raises(fg.FlashRefused, match="identity-checked"):
        fg.check_flash("/dev/cu.usbmodemOK", {"FLASH_DENY_SERIALS": TEST_DENIED},
                       ports=ports, protocol=proto)


def test_resolve_port(fg, ports):
    env = {"FLASH_DENY_SERIALS": TEST_DENIED, "B": TEST_OTHER}
    assert fg.resolve_port(env, "B", ports=ports) == "/dev/cu.usbmodemOK"
    with pytest.raises(fg.FlashRefused, match="never flash"):
        fg.resolve_port({"FLASH_DENY_SERIALS": TEST_DENIED, "B": TEST_DENIED}, "B", ports=ports)
    with pytest.raises(fg.FlashRefused, match="want exactly 1"):
        fg.resolve_port({"FLASH_DENY_SERIALS": TEST_DENIED, "B": "AA:00"}, "B", ports=ports)
    with pytest.raises(fg.FlashRefused, match="FLASH_DENY_SERIALS"):
        fg.resolve_port({"B": TEST_OTHER}, "B", ports=ports)


@pytest.mark.parametrize("target,guarded", [
    ("upload", True), ("uploadfs", True), ("uploadfsota", True), ("erase", True),
    ("erase_upload", True), ("program", True), ("__debug", True),
    ("buildprog", False), ("checkprogsize", False), ("size", False), ("buildfs", False),
    ("__test", False), ("compiledb", False), ("monitor", False)])
def test_flash_target_classification(fg, target, guarded):
    assert fg.is_flash_target(target) is guarded


def test_cli_refuses_with_deny_list_unset():
    env = {k: v for k, v in os.environ.items() if k != "FLASH_DENY_SERIALS"}
    r = subprocess.run([sys.executable, str(LIB), "check", "--port", "/dev/cu.usbmodemOK"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 3, r
    assert "FLASH_DENY_SERIALS" in r.stderr


# --------------------------------------------------------------------------- PIO hook

class FakeEnv:
    def __init__(self, project_dir, options=None, upload_port="", protocol="", pioenv="e"):
        self.vars = {"$PROJECT_DIR": str(project_dir), "$UPLOAD_PORT": upload_port,
                     "$UPLOAD_PROTOCOL": protocol, "$PIOENV": pioenv}
        self.options = options or {}

    def subst(self, s):
        return self.vars.get(s, "")

    def GetProjectOption(self, name, default=None):
        return self.options.get(name, default)

    def IsCleanTarget(self):
        return False


@pytest.fixture()
def hook():
    return _load(HOOK, "pio_flash_guard_under_test")


@pytest.fixture()
def fake_comports(monkeypatch):
    """Patch pyserial's enumerator so the hook path can never see real ports."""
    lp = types.ModuleType("serial.tools.list_ports")
    lp.comports = lambda: [types.SimpleNamespace(device="/dev/cu.usbmodemDENY", serial_number=TEST_DENIED),
                           types.SimpleNamespace(device="/dev/cu.usbmodemOK", serial_number=TEST_OTHER)]
    tools = types.ModuleType("serial.tools")
    tools.list_ports = lp
    ser = types.ModuleType("serial")
    ser.tools = tools
    monkeypatch.setitem(sys.modules, "serial", ser)
    monkeypatch.setitem(sys.modules, "serial.tools", tools)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", lp)


PROJ = REPO / "firmware" / "reactive_core"


def test_hook_refuses_upload_with_deny_list_unset(hook, fake_comports, capsys):
    rc = hook.run(FakeEnv(PROJ, upload_port="/dev/cu.usbmodemOK"), ["upload"], {})
    assert rc != 0
    assert "FLASH_DENY_SERIALS" in capsys.readouterr().err


def test_hook_pio_test_upload_is_guarded(hook, fake_comports, capsys):
    rc = hook.run(FakeEnv(PROJ, upload_port="/dev/cu.usbmodemOK"), ["upload", "__test"], {})
    assert rc != 0


def test_hook_denies_listed_serial_allows_other(hook, fake_comports, capsys):
    env = {"FLASH_DENY_SERIALS": TEST_DENIED}
    assert hook.run(FakeEnv(PROJ, upload_port="/dev/cu.usbmodemDENY"), ["upload"], env) != 0
    assert "never flash" in capsys.readouterr().err
    assert hook.run(FakeEnv(PROJ, upload_port="/dev/cu.usbmodemOK"), ["upload"], env) == 0


def test_hook_expect_and_port_vars(hook, fake_comports, capsys):
    env = {"FLASH_DENY_SERIALS": TEST_DENIED}
    e = FakeEnv(PROJ, {"custom_flash_expect_env": "R503_EXPECT_SERIAL"}, upload_port="/dev/cu.usbmodemOK")
    assert hook.run(e, ["upload"], env) != 0
    assert "R503_EXPECT_SERIAL" in capsys.readouterr().err
    assert hook.run(e, ["upload"], dict(env, R503_EXPECT_SERIAL=TEST_OTHER)) == 0
    e = FakeEnv(PROJ, {"custom_flash_port_env": "OFFICE_RELAY_SERIAL_PORT"})
    assert hook.run(e, ["upload"], env) != 0
    assert "OFFICE_RELAY_SERIAL_PORT" in capsys.readouterr().err


def test_hook_leaves_plain_build_alone(hook):
    assert hook.run(FakeEnv(PROJ), [], {}) == 0
    assert hook.run(FakeEnv(PROJ), ["checkprogsize", "size"], {}) == 0


def test_hook_fails_live_build_with_unset_overlay_values(hook, capsys):
    e = FakeEnv(PROJ, {"custom_fw_required_env": "COMPOSEIO_FW_STORAGE_URL COMPOSEIO_FW_MQTT_BROKER"})
    assert hook.run(e, [], {"COMPOSEIO_FW_MQTT_BROKER": "b"}) != 0
    assert "COMPOSEIO_FW_STORAGE_URL" in capsys.readouterr().err
    assert hook.run(e, [], {"COMPOSEIO_FW_MQTT_BROKER": "b", "COMPOSEIO_FW_STORAGE_URL": "u"}) == 0


def test_hook_refuses_when_library_cannot_be_found(hook, tmp_path, capsys):
    rc = hook.run(FakeEnv(tmp_path, upload_port="/dev/cu.x"), ["upload"],
                  {"FLASH_DENY_SERIALS": TEST_DENIED})
    assert rc != 0
    assert "cannot locate" in capsys.readouterr().err


# --------------------------------------------------------------------------- every ini

def _ini_files():
    out = subprocess.run(["git", "ls-files", "*platformio.ini"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout.split()
    files = [REPO / f for f in out]
    assert files, "found no platformio.ini (the test would pass vacuously)"
    return files


def _parse(path):
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",),
                                   comment_prefixes=(";", "#"), strict=False)
    cp.read(path)
    return cp


def _opt(cp, section, key, seen=()):
    if cp.has_option(section, key):
        return cp.get(section, key)
    if cp.has_option(section, "extends"):
        for base in cp.get(section, "extends").split(","):
            base = base.strip()
            name = base if cp.has_section(base) else "env:" + base
            if name not in seen and cp.has_section(name):
                v = _opt(cp, name, key, seen + (name,))
                if v is not None:
                    return v
    if section != "env" and cp.has_section("env") and cp.has_option("env", key):
        return cp.get("env", key)
    return None


def _device_envs():
    for ini in _ini_files():
        cp = _parse(ini)
        for sec in cp.sections():
            if not sec.startswith("env:"):
                continue
            if (_opt(cp, sec, "platform") or "").strip() == "native":
                continue
            yield ini, cp, sec


def test_every_device_env_loads_the_flash_guard():
    missing, count = [], 0
    for ini, cp, sec in _device_envs():
        count += 1
        scripts = (_opt(cp, sec, "extra_scripts") or "").split()
        hits = [s for s in scripts if s.endswith("pio_flash_guard.py")]
        if not hits:
            missing.append(f"{ini.relative_to(REPO)} [{sec}]")
            continue
        rel = hits[0].split(":", 1)[1] if ":" in hits[0] else hits[0]
        assert (ini.parent / rel).resolve() == HOOK.resolve(), (ini, rel)
        assert scripts.index(hits[0]) == 0, f"{ini} [{sec}]: the guard must run first"
    assert count >= 20, f"only {count} device envs seen — parser is not reading the inis"
    assert not missing, "device envs with no flash guard:\n  " + "\n  ".join(missing)


def test_baked_overlay_values_are_declared_required():
    """A ${sysenv.X} baked into build_flags/upload_port must be in that env's
    custom_fw_required_env or custom_flash_port_env, or it bakes an empty string."""
    bad = []
    for ini, cp, sec in _device_envs():
        used = set()
        for key in ("build_flags", "upload_port"):
            used |= set(re.findall(r"\$\{sysenv\.(\w+)\}", _opt(cp, sec, key) or ""))
        declared = set((_opt(cp, sec, "custom_fw_required_env") or "").split())
        declared |= set((_opt(cp, sec, "custom_flash_port_env") or "").split())
        if used - declared:
            bad.append(f"{ini.relative_to(REPO)} [{sec}]: {sorted(used - declared)}")
    assert not bad, "\n".join(bad)


def test_no_broker_placeholder_baked_into_firmware():
    bad = []
    for ini, cp, sec in _device_envs():
        flags = _opt(cp, sec, "build_flags") or ""
        if re.search(r"(MQTT_BROKER|BUS_STORAGE_URL)=\\?\"[^$]", flags):
            bad.append(f"{ini.relative_to(REPO)} [{sec}]")
    assert not bad, "literal broker/storage URL baked in:\n" + "\n".join(bad)


# --------------------------------------------------------------------------- no identities

GUARD_FILES = [
    "cyd/platformio.ini", "firmware/reactive_core/platformio.ini",
    "firmware/r503_probe/platformio.ini", "firmware/r503_probe/build.sh",
    "firmware/esp32_mqtt_client/platformio.ini", "cyd/src/gauge_diag.cpp",
    "cyd/include/esp32c3_st7796_panel.h",
    "python/composeio/flash_guard.py", "python/scripts/pio_flash_guard.py",
    "docs/r503/probe-archive/r503probe.py",
    "docs/board-bringup/FLEET-DEVICE-REGISTRY.md", "docs/board-bringup/RUNBOOK.md",
    "docs/board-bringup/factory-images/README.md",
    "compose_projects/office_devices/office_fan/device.yaml",
    "compose_projects/office_devices/office_hepa/device.yaml",
    "compose_projects/office_devices/README.md",
]
PSEUDONYM = re.compile(r"00:00:5e:00:53:[0-9a-f]{2}|<device-serial>|047dd8|080739|07f199",
                       re.IGNORECASE)


def test_no_pseudonymous_identity_in_guard_files():
    files = [REPO / f for f in GUARD_FILES] + sorted((REPO / "docs" / "board-bringup").glob("*.descriptor.yaml"))
    hits = []
    for f in files:
        assert f.is_file(), f
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if PSEUDONYM.search(line):
                hits.append(f"{f.relative_to(REPO)}:{n}: {line.strip()[:100]}")
    assert not hits, "a guard compares against a made-up identity:\n" + "\n".join(hits)


# --------------------------------------------------------------------------- build.sh

def _stub_bin(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("pio", "rsync", "python3"):
        p = b / tool
        p.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{log}"\n'
                     + ('echo "refusing (stub)" >&2; exit 3\n' if tool == "python3" else "exit 0\n"))
        p.chmod(0o755)
    return b, log


def _run_build(tmp_path, extra_env):
    b, log = _stub_bin(tmp_path)
    env = {"PATH": f"{b}:/usr/bin:/bin", "HOME": str(tmp_path / "home")}
    env.update(extra_env)
    r = subprocess.run(["bash", str(BUILD_SH), "upload"], env=env, capture_output=True, text=True)
    calls = log.read_text() if log.exists() else ""
    return r, calls


def test_build_sh_refuses_with_deny_list_unset(tmp_path):
    r, calls = _run_build(tmp_path, {"R503_EXPECT_SERIAL": TEST_OTHER})
    assert r.returncode != 0
    assert "FLASH_DENY_SERIALS" in r.stderr
    assert calls == "", f"tools ran before the refusal: {calls!r}"


def test_build_sh_refuses_with_expect_unset(tmp_path):
    r, calls = _run_build(tmp_path, {"FLASH_DENY_SERIALS": TEST_DENIED})
    assert r.returncode != 0
    assert "R503_EXPECT_SERIAL" in r.stderr
    assert calls == ""


def test_build_sh_guard_refusal_stops_before_copy_or_pio(tmp_path):
    r, calls = _run_build(tmp_path, {"FLASH_DENY_SERIALS": TEST_DENIED,
                                     "R503_EXPECT_SERIAL": TEST_OTHER})
    assert r.returncode != 0
    assert "flash_guard.py resolve --expect-var R503_EXPECT_SERIAL" in calls
    tools = [ln.split(" ", 1)[0] for ln in calls.splitlines()]
    assert tools == ["python3"], f"only the guard may run before a refusal: {calls!r}"


# --------------------------------------------------------------------------- r503probe

def _probe(env):
    e = {k: v for k, v in os.environ.items()
         if k not in ("R503_EXPECT_SERIAL", "FLASH_DENY_SERIALS")}
    e.update(env)
    return subprocess.run([sys.executable, str(R503PROBE), "port"], env=e,
                          capture_output=True, text=True)


def test_r503probe_refuses_without_overlay():
    pytest.importorskip("serial")
    r = _probe({})
    assert r.returncode == 2 and "R503_EXPECT_SERIAL" in r.stderr, r
    r = _probe({"R503_EXPECT_SERIAL": TEST_OTHER})
    assert r.returncode == 2 and "FLASH_DENY_SERIALS" in r.stderr, r
    r = _probe({"R503_EXPECT_SERIAL": TEST_DENIED, "FLASH_DENY_SERIALS": TEST_DENIED})
    assert r.returncode == 2 and "FLASH_DENY_SERIALS" in r.stderr, r
