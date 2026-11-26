"""Tests for the carousel kiosk host router/supervisor
(``compose_projects/carousel/kiosk.py``).

Covers the intent->action routing with a MOCK scene runner + power action (no
real windows, no real poweroff), the poweroff HARD-GUARD (can never fire on the
dev mac), and — when the shared broker is reachable — a LIVE end-to-end proof
that a published `_app/bsg/open` on MQTT reaches the router and swaps the scene.

The kiosk module lives OUTSIDE the python package (in compose_projects/), so it
is loaded by file path via importlib.
"""
from __future__ import annotations

import importlib.util
import os
import socket
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_KIOSK_PATH = _REPO_ROOT / "compose_projects" / "carousel" / "kiosk.py"


def _load_kiosk():
    spec = importlib.util.spec_from_file_location("carousel_kiosk", _KIOSK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["carousel_kiosk"] = mod
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


kiosk = _load_kiosk()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class RecordingRunner:
    """Stand-in for SceneRunner: records show() calls, never spawns."""

    def __init__(self):
        self.current = None
        self.shows = []          # list of (scene_basename, force)
        self._alive = True

    def show(self, scene, *, force=False):
        self.current = scene
        self.shows.append((os.path.basename(scene), force))
        return f"showing {os.path.basename(scene)}"

    def alive(self):
        return self._alive

    def stop(self):
        self.current = None


def _quiet_supervisor(**kw):
    kw.setdefault("log", lambda m: None)
    return kiosk.KioskSupervisor(**kw)


@pytest.fixture
def bsg_overlay(monkeypatch, tmp_path):
    """The 2-up is OPTIONAL overlay content (private bsg overlay); these routing
    tests install a stub where COMPOSEIO_BSG_SCENE points, so they hold whether
    or not the overlay is present in this checkout."""
    scene = tmp_path / "bsg_app.compose.yaml"
    scene.write_text("app: {id: stub}\n")
    monkeypatch.setenv(kiosk.BSG_SCENE_ENV, str(scene))
    return scene


# ---------------------------------------------------------------------------
# namespace parsing
# ---------------------------------------------------------------------------

def test_target_from_ns_valid():
    assert kiosk.target_from_ns("_app/bsg/open") == "bsg"
    assert kiosk.target_from_ns("_app/launcher/open") == "launcher"
    assert kiosk.target_from_ns("_app/power/open") == "power"


def test_target_from_ns_rejects_non_intents():
    assert kiosk.target_from_ns("launcher/index") is None
    assert kiosk.target_from_ns("_app/x/y/open") is None
    assert kiosk.target_from_ns("_app/bsg/close") is None
    assert kiosk.target_from_ns("garbage") is None


# ---------------------------------------------------------------------------
# routing (mock runner + power) — the acceptance path
# ---------------------------------------------------------------------------

def test_boot_shows_carousel_launcher():
    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner, power=kiosk.PowerAction(
        allowed=lambda: False))
    sup.boot()
    assert runner.shows[-1][0] == "launcher.compose.yaml"


def test_bsg_open_switches_to_two_up(bsg_overlay):
    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner)
    sup.boot()
    res = sup.route("bsg")
    assert res["action"] == "show"
    assert res["scene"] == "bsg_app.compose.yaml"
    assert runner.current.endswith("bsg_app.compose.yaml")


def test_launcher_open_returns_to_carousel(bsg_overlay):
    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner)
    sup.boot()
    sup.route("bsg")
    res = sup.route("launcher")            # BACK affordance
    assert res["action"] == "show"
    assert runner.current.endswith("launcher.compose.yaml")


def test_bsg_open_without_overlay_is_stated_not_spawned(monkeypatch, tmp_path):
    monkeypatch.setenv(kiosk.BSG_SCENE_ENV, str(tmp_path / "absent.compose.yaml"))
    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner)
    sup.boot()
    res = sup.route("bsg")
    assert res["action"] == "unavailable"
    assert res["reason"] == "BSG overlay not installed"
    assert [s for s, _ in runner.shows] == ["launcher.compose.yaml"]


def test_fiat_open_is_deferred_no_spawn():
    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner)
    sup.boot()
    n = len(runner.shows)
    res = sup.route("fiat")
    assert res["action"] == "deferred"
    assert res["target"] == "fiat"
    assert len(runner.shows) == n          # nothing spawned


def test_on_intent_routes_via_bus_callback(bsg_overlay):
    """The bus callback (ns, new_val, old_val) parses the target and routes."""
    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner)
    sup.on_intent("_app/bsg/open", "open", None)
    assert runner.current.endswith("bsg_app.compose.yaml")


def test_unknown_intent_never_raises():
    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner)
    sup.on_intent("_app/nope/open", "open", None)   # deferred, no crash
    sup.on_intent("launcher/index", 2, 1)           # not an intent, ignored


# ---------------------------------------------------------------------------
# poweroff HARD-GUARD
# ---------------------------------------------------------------------------

def test_power_open_does_not_fire_when_not_allowed():
    calls = []
    power = kiosk.PowerAction(allowed=lambda: False,
                              executor=lambda cmd: calls.append(cmd))
    sup = _quiet_supervisor(runner=RecordingRunner(), power=power)
    res = sup.route("power")
    assert res["action"] == "power"
    assert res["fired"] is False
    assert res["command"] == ["sudo", "poweroff"]   # command IS reported
    assert calls == []                              # but NEVER executed


def test_power_open_fires_only_when_allowed():
    calls = []
    power = kiosk.PowerAction(allowed=lambda: True,
                              executor=lambda cmd: calls.append(cmd))
    res = power.trigger()
    assert res["fired"] is True
    assert calls == [["sudo", "poweroff"]]


def test_poweroff_never_allowed_on_dev_mac():
    import platform
    if platform.system() == "Darwin":
        assert kiosk.poweroff_allowed() is False
    else:
        # everywhere else the env kill-switch must still force it off
        os.environ["COMPOSEIO_KIOSK_NO_POWEROFF"] = "1"
        try:
            assert kiosk.poweroff_allowed() is False
        finally:
            del os.environ["COMPOSEIO_KIOSK_NO_POWEROFF"]


def test_selftest_passes():
    assert kiosk._selftest() == 0


# ---------------------------------------------------------------------------
# LIVE end-to-end over MQTT (skipped unless the shared broker is reachable)
# ---------------------------------------------------------------------------

def _broker_up(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_BROKER = os.environ.get("COMPOSEUX_MQTT_BROKER", kiosk.DEFAULT_BROKER)
_PORT = kiosk.DEFAULT_PORT


@pytest.mark.skipif(not _broker_up(_BROKER, _PORT),
                    reason=f"MQTT broker {_BROKER}:{_PORT} not reachable")
def test_live_bsg_open_over_mqtt_switches_scene(bsg_overlay):
    """A published `_app/bsg/open` on the REAL broker reaches the router's bus
    and swaps the scene — the full MQTT -> route -> runner path."""
    from composeio.bus import ComposeIOBus
    from composeio.transport_mqtt import MQTTTransport

    runner = RecordingRunner()
    sup = _quiet_supervisor(runner=runner, broker=_BROKER, port=_PORT)
    bus = sup.wire_bus()

    pub_tp = MQTTTransport(broker=_BROKER, port=_PORT,
                           client_id=f"kiosk-test-pub-{os.getpid()}")
    pub = ComposeIOBus(bus_id="kiosk-test-pub", global_transport=pub_tp)
    pub.ensure_namespace("_app/bsg/open", engine_config="json")
    pub.start()
    try:
        # let both clients finish CONNECT + SUBSCRIBE before publishing
        deadline = time.time() + 5.0
        while time.time() < deadline and not getattr(
                pub_tp, "is_connected", False):
            time.sleep(0.05)
        pub.publish_state("_app/bsg/open", "open", source="kiosk-test")

        deadline = time.time() + 5.0
        while time.time() < deadline and not runner.shows:
            time.sleep(0.1)
        assert runner.current is not None, "router never received the intent"
        assert runner.current.endswith("bsg_app.compose.yaml")
    finally:
        pub.stop()
        bus.stop()
