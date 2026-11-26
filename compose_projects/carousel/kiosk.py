#!/usr/bin/env python3
"""carousel kiosk — the HOST ROUTER / supervisor for the ComposePlatform
carousel launcher ([SPEC:CXC-PRIM-CAROUSEL]).  The bus-native replacement for
the CTk kiosk ``app.py`` on display-node.local.

The carousel widget ONLY *publishes* launch intents (`_app/<target>/open` =
"open" on a center-tap); it never spawns.  THIS process is the missing half —
the launcher-owns-runtime side of the widget-owns-intent / launcher-owns-runtime
split (README "The `_app/bsg/open` binding").  It:

  * boots showing the carousel launcher FULLSCREEN;
  * subscribes the app-open intents on the shared broker
    (`_app/launcher/open`, `_app/bsg/open`, `_app/fiat/open`, `_app/power/open`);
  * on an intent, SWAPS the visible scene (subprocess-per-scene: kill the
    current `run_compose` child, spawn the target fullscreen) or runs the
    system action (`_app/power/open` -> `sudo poweroff`, GUARDED — see
    ``poweroff_allowed``).

Scene-swap mechanism — subprocess-per-scene.  The render runtime binds ONE
``Composition`` for the whole life of ``RenderApp.run()`` (start() creates the
window + mounts that tree; there is no clean in-process "load a different
scene" seam — namespaces, the widget tree, and the window are all wired to the
one composition).  So a kiosk swap = terminate the current child + launch the
target as a fresh fullscreen ``run_compose`` process.  This mirrors
``console_supervisor``'s process ownership exactly, is trivially robust (a
crashed scene is just a dead child the watchdog relaunches), and needs no new
runtime surface.

Back affordance: publishing `_app/launcher/open` returns to the carousel — the
same bus intent a BACK button widget, an MCP publish, or an Intelligence would
send (the credo: an Intelligence drives identically to a human).  A physical
hotkey is a deferred bonus (the render child owns the window/keyboard).

Autostart (the deployer puts this in wayfire.ini — see README "rpi5 deploy"):

    env: PYTHONPATH, COMPOSEIO_FONT_DIR, the venv python, and the Wayland/XDG
    session env come from the wayfire autostart environment; this supervisor
    ALSO fills PYTHONPATH + COMPOSEIO_FONT_DIR for its children from --repo-root
    if unset (belt and suspenders).

Run:  python3 kiosk.py --broker mqtt-broker.local
      python3 kiosk.py --selftest        # no broker, no windows
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

# This file lives in compose_projects/carousel/ ; the repo root is two up.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))

DEFAULT_BROKER = os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")    # == mqtt-broker.local, the bus master
DEFAULT_PORT = 1883

# target -> scene FILE (relative to this dir).  A target with NO entry here is
# either a system action (`power`), an OPTIONAL overlay app (`bsg`, below) or a
# deferred app (`fiat`, whose LIVE tile rides the carousel — no standalone
# fullscreen scene exists yet); a deferred target routes but never spawns.
SCENES: Dict[str, str] = {
    "launcher": "launcher.compose.yaml",
}

# ── OPTIONAL OVERLAY SCENES ─────────────────────────────────────────────────
# The BSG 2-up (`bsg_app.compose.yaml`) is NOT public content: it ships in the
# private bsg overlay, which places it at the repo-relative default below (the
# same path it has inside the overlay repo).  `COMPOSEIO_BSG_SCENE` names any
# other location (absolute, or relative to the repo root).  When no file is
# there, the overlay is not installed: the `bsg` intent is answered with
# BSG_NOT_INSTALLED and the kiosk stays on the current scene, and the launcher
# tile is told so through its `bsg_tile_hint` launch variable (see
# ``launcher_overrides``).  An absent overlay is a stated state, never a
# file-not-found from a spawned child.
BSG_SCENE_ENV = "COMPOSEIO_BSG_SCENE"
BSG_SCENE_DEFAULT = "compose_projects/carousel/bsg_app.compose.yaml"
BSG_NOT_INSTALLED = "BSG overlay not installed"
BSG_TILE_HINT_READY = "tap to launch"
# The launcher scene declares `variables: {bsg_tile_hint: ...}`; the kiosk sets
# it per spawn through the runtime's COMPOSEIO_VAR_<NAME> launch override.
BSG_TILE_HINT_ENV = "COMPOSEIO_VAR_BSG_TILE_HINT"


def bsg_scene_path(repo_root: str = REPO_ROOT,
                   env: Optional[Dict[str, str]] = None) -> str:
    """Absolute path where the BSG overlay's 2-up scene is expected."""
    env = os.environ if env is None else env
    raw = (env.get(BSG_SCENE_ENV) or "").strip() or BSG_SCENE_DEFAULT
    raw = os.path.expanduser(raw)
    return raw if os.path.isabs(raw) else os.path.join(repo_root, raw)


def bsg_installed(repo_root: str = REPO_ROOT,
                  env: Optional[Dict[str, str]] = None) -> bool:
    return os.path.isfile(bsg_scene_path(repo_root, env))


# target -> (resolver of an OPTIONAL scene's absolute path, the message the
# kiosk gives when that file is absent, the env name that relocates it).
OPTIONAL_SCENES: Dict[str, tuple] = {
    "bsg": (bsg_scene_path, BSG_NOT_INSTALLED, BSG_SCENE_ENV),
}


def launcher_overrides(repo_root: str = REPO_ROOT,
                       env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The launch-variable env the LAUNCHER child gets, computed at each spawn
    so installing (or removing) the overlay shows on the next return to it."""
    hint = (BSG_TILE_HINT_READY if bsg_installed(repo_root, env)
            else BSG_NOT_INSTALLED)
    return {BSG_TILE_HINT_ENV: hint}
BOOT_TARGET = "launcher"
# Every intent the router subscribes.  `fiat` is subscribed so the intent is
# OBSERVED (and logged as deferred) rather than silently dropped.
INTENT_TARGETS = ("launcher", "bsg", "fiat", "power")


def scene_path(name: str) -> str:
    """Absolute path to a scene file that lives beside this supervisor."""
    return os.path.join(HERE, name)


def target_from_ns(ns: str) -> Optional[str]:
    """Extract ``<target>`` from an ``_app/<target>/open`` namespace, or None
    for anything that is not a launch intent."""
    parts = ns.split("/")
    if len(parts) == 3 and parts[0] == "_app" and parts[2] == "open":
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# Power action — HARD-GUARDED so `poweroff` can NEVER fire on the dev mac.
# ---------------------------------------------------------------------------

def poweroff_allowed() -> bool:
    """True ONLY where a real poweroff should fire: Linux with a ``poweroff``
    binary on PATH.  On the dev mac (Darwin) this is ALWAYS False, so the
    command can never fire there — the hard guard the task requires.  An
    explicit ``COMPOSEIO_KIOSK_NO_POWEROFF=1`` disables it even on the Pi (dev
    on the target hardware)."""
    if os.environ.get("COMPOSEIO_KIOSK_NO_POWEROFF", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return False
    return platform.system() == "Linux" and shutil.which("poweroff") is not None


class PowerAction:
    """The `_app/power/open` action.  Fires ``sudo poweroff`` ONLY where
    ``poweroff_allowed()`` (injectable for tests); everywhere else it reports
    the command it WOULD run WITHOUT firing.  ``trigger()`` returns
    ``{"command": [...], "fired": bool}`` so a caller/test can prove the intent
    was received and the command would fire without a real shutdown."""

    def __init__(self, command=("sudo", "poweroff"),
                 allowed: Optional[Callable[[], bool]] = None,
                 executor: Optional[Callable[[List[str]], None]] = None) -> None:
        self.command: List[str] = list(command)
        self._allowed = allowed or poweroff_allowed
        self._executor = executor or (lambda cmd: subprocess.Popen(cmd))

    def trigger(self) -> dict:
        fired = bool(self._allowed())
        if fired:
            self._executor(self.command)
        return {"command": list(self.command), "fired": fired}


# ---------------------------------------------------------------------------
# SceneRunner — owns the SINGLE active run_compose child (subprocess-per-scene).
# ---------------------------------------------------------------------------

class SceneRunner:
    """Owns the one visible ``run_compose`` fullscreen child.  A scene swap =
    kill the current child + spawn the target.  Thread-safe (the bus callback
    that requests a swap runs on the MQTT transport thread)."""

    def __init__(self, repo_root: str = REPO_ROOT, broker: str = DEFAULT_BROKER,
                 port: int = DEFAULT_PORT,
                 spawn: Optional[Callable[[str, dict], "subprocess.Popen"]] = None,
                 fullscreen: bool = True) -> None:
        self.repo_root = repo_root
        self.broker = broker
        self.port = port
        self.fullscreen = fullscreen
        self._spawn = spawn or self._spawn_run_compose
        self._proc: Optional[subprocess.Popen] = None
        self.current: Optional[str] = None
        self._lock = threading.RLock()

    # -- child env / spawn ----------------------------------------------------

    def _child_env(self, scene: Optional[str] = None) -> dict:
        """The environment a spawned scene inherits.  The wayfire autostart
        provides PYTHONPATH / COMPOSEIO_FONT_DIR / the Wayland+XDG session env;
        we FILL PYTHONPATH + COMPOSEIO_FONT_DIR from --repo-root if unset so a
        forgetful autostart line still yields a working child.  The LAUNCHER
        child also gets ``launcher_overrides`` (the BSG tile's state)."""
        env = dict(os.environ)
        if scene is not None and os.path.basename(scene) == SCENES[BOOT_TARGET]:
            env.update(launcher_overrides(self.repo_root, env))
        py = os.path.join(self.repo_root, "python")
        existing = env.get("PYTHONPATH", "")
        if py not in existing.split(os.pathsep):
            env["PYTHONPATH"] = py + (os.pathsep + existing if existing else "")
        env.setdefault("COMPOSEIO_REPO_ROOT", self.repo_root)
        env.setdefault("COMPOSEIO_FONT_DIR",
                       os.path.join(self.repo_root, "compose_resources",
                                    "fonts"))
        env.setdefault("COMPOSEUX_MQTT_BROKER", self.broker)
        return env

    def _spawn_run_compose(self, scene: str, env: dict) -> "subprocess.Popen":
        cmd = [sys.executable, "-m", "composeio.render.run_compose", scene,
               "--transports"]
        if self.fullscreen:
            cmd.insert(-1, "--fullscreen")
        return subprocess.Popen(cmd, cwd=self.repo_root, env=env,
                                start_new_session=True)

    # -- process ownership ----------------------------------------------------

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill_locked(self, timeout: float = 5.0) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            pid = self._proc.pid
            try:
                # own the whole session so a windowed child + helpers go down
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self._proc.terminate()
                except Exception:
                    pass
            deadline = time.time() + timeout
            while time.time() < deadline and self._proc.poll() is None:
                time.sleep(0.1)
            if self._proc.poll() is None:                    # refused to die
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
        self._proc = None

    def show(self, scene: str, *, force: bool = False) -> str:
        """Swap the visible scene to *scene* (an absolute path).  Idempotent:
        a request for the scene already showing (child still alive) is a no-op
        unless ``force`` (used by the crash-watchdog to relaunch)."""
        with self._lock:
            if not force and scene == self.current and self.alive():
                return f"already showing {os.path.basename(scene)}"
            self._kill_locked()
            self._proc = self._spawn(scene, self._child_env(scene))
            self.current = scene
            return f"showing {os.path.basename(scene)} (pid {self._proc.pid})"

    def stop(self) -> None:
        with self._lock:
            self._kill_locked()
            self.current = None


# ---------------------------------------------------------------------------
# KioskSupervisor — the intent -> action router.
# ---------------------------------------------------------------------------

class KioskSupervisor:
    """Routes `_app/<target>/open` intents to scene swaps + the power action,
    and owns the intent bus + boot + crash-watchdog loop.  The routing
    (``route``) is a thin, pure-ish method separate from the bus/loop so a test
    drives the exact intent->action path with a mock runner + power."""

    def __init__(self, repo_root: str = REPO_ROOT, broker: str = DEFAULT_BROKER,
                 port: int = DEFAULT_PORT,
                 scenes: Optional[Dict[str, str]] = None,
                 runner: Optional[SceneRunner] = None,
                 power: Optional[PowerAction] = None,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.repo_root = repo_root
        self.broker = broker
        self.port = port
        self.scenes = dict(SCENES) if scenes is None else dict(scenes)
        self.optional = dict(OPTIONAL_SCENES)
        self.runner = runner if runner is not None else SceneRunner(
            repo_root, broker, port)
        self.power = power if power is not None else PowerAction()
        self._log = log or (lambda m: print(f"[kiosk] {m}", flush=True))
        self._bus = None
        self._stop = threading.Event()

    # -- routing --------------------------------------------------------------

    def boot(self) -> str:
        """Show the carousel launcher fullscreen (the kiosk's initial state)."""
        return self.runner.show(scene_path(self.scenes[BOOT_TARGET]))

    def route(self, target: str) -> dict:
        """Map ONE launch target to an action.  Returns an observability dict
        (also what the tests assert on).  Never raises for an unknown target —
        a kiosk never guesses."""
        if target == "power":
            res = self.power.trigger()
            self._log(f"power intent -> command={res['command']} "
                      f"fired={res['fired']}")
            return {"action": "power", **res}
        if target in self.optional and target not in self.scenes:
            resolve, absent_msg, env_name = self.optional[target]
            path = resolve(self.repo_root)
            if not os.path.isfile(path):
                self._log(f"intent '{target}' -> {absent_msg} (no scene at "
                          f"{path}; set {env_name}) — staying on current")
                return {"action": "unavailable", "target": target,
                        "reason": absent_msg, "scene": path}
            detail = self.runner.show(path)
            self._log(f"intent '{target}' -> {detail}")
            return {"action": "show", "target": target,
                    "scene": os.path.basename(path), "detail": detail}
        name = self.scenes.get(target)
        if name is None:
            # `fiat` has no standalone fullscreen scene yet (its LIVE tile rides
            # the carousel); an unknown target lands here too.  Stay put.
            self._log(f"intent '{target}' has no scene — deferred (staying on "
                      f"current)")
            return {"action": "deferred", "target": target}
        detail = self.runner.show(scene_path(name))
        self._log(f"intent '{target}' -> {detail}")
        return {"action": "show", "target": target, "scene": name,
                "detail": detail}

    def on_intent(self, ns: str, new_val, old_val=None) -> None:
        """Bus subscription callback (runs on the MQTT transport thread).
        Fires on any `_app/<target>/open` publish (value is "open")."""
        target = target_from_ns(ns)
        if target is None:
            return
        try:
            self.route(target)
        except Exception as exc:                  # a bad intent never kills us
            self._log(f"intent '{target}' FAILED: {exc!r}")

    # -- bus wiring (split from run() so a test drives the receive path) ------

    def wire_bus(self):
        """Build the intent bus (MQTT global transport), subscribe every
        `_app/<target>/open`, start it, and return it.  Caller owns stop."""
        from composeio.bus import ComposeIOBus
        from composeio.transport_mqtt import MQTTTransport

        transport = MQTTTransport(broker=self.broker, port=self.port,
                                  client_id=f"carousel-kiosk-{os.getpid()}")
        bus = ComposeIOBus(bus_id="carousel-kiosk", global_transport=transport)
        for t in INTENT_TARGETS:
            ns = f"_app/{t}/open"
            bus.ensure_namespace(ns, engine_config="json")
            bus.subscribe(ns, self.on_intent)
        bus.start()
        self._bus = bus
        self._log(f"listening _app/*/open on {self.broker}:{self.port} "
                  f"({', '.join(INTENT_TARGETS)})")
        return bus

    def run(self, autostart: bool = True, poll_s: float = 2.0) -> int:
        """Wire the bus, boot the carousel, then supervise: relaunch the
        current scene if its child dies (kiosk self-heal), until SIGTERM/INT."""
        bus = self.wire_bus()

        def _sig(_signum, _frame):
            self._stop.set()
        try:
            signal.signal(signal.SIGTERM, _sig)
            signal.signal(signal.SIGINT, _sig)
        except ValueError:
            pass                                  # not on the main thread (tests)

        if autostart:
            self._log(self.boot())
        try:
            while not self._stop.wait(poll_s):
                if self.runner.current is not None and not self.runner.alive():
                    self._log("scene child died — relaunching current scene")
                    self._log(self.runner.show(self.runner.current, force=True))
        finally:
            self.runner.stop()
            try:
                bus.stop()
            except Exception:
                pass
        return 0


# ---------------------------------------------------------------------------
# selftest — no broker, no windows (a fast Pi/CI sanity check).
# ---------------------------------------------------------------------------

def _selftest() -> int:
    calls: List[str] = []

    class FakeRunner:
        def __init__(self):
            self.current = None
        def show(self, scene, *, force=False):
            self.current = scene
            calls.append(os.path.basename(scene))
            return f"showing {os.path.basename(scene)}"
        def alive(self):
            return True
        def stop(self):
            calls.append("stop")

    fired = {"n": 0}

    def never_allowed():
        return False

    power = PowerAction(allowed=never_allowed,
                        executor=lambda cmd: fired.__setitem__("n", fired["n"] + 1))
    sup = KioskSupervisor(runner=FakeRunner(), power=power,
                          log=lambda m: None)

    assert target_from_ns("_app/bsg/open") == "bsg", "(1) ns parse"
    assert target_from_ns("_app/x/y/open") is None, "(2) ns parse reject"
    assert target_from_ns("launcher/index") is None, "(3) ns parse reject"

    sup.boot()
    assert calls[-1] == "launcher.compose.yaml", "(4) boot shows launcher"

    r = sup.route("bsg")
    if bsg_installed():
        assert r["action"] == "show" and calls[-1] == "bsg_app.compose.yaml", "(5) bsg"
    else:
        assert (r["action"] == "unavailable" and r["reason"] == BSG_NOT_INSTALLED
                and calls[-1] == "launcher.compose.yaml"), "(5) bsg absent -> stated, no spawn"

    r = sup.route("launcher")
    assert calls[-1] == "launcher.compose.yaml", "(6) back to launcher"

    n_before = len(calls)
    r = sup.route("fiat")
    assert r["action"] == "deferred" and len(calls) == n_before, "(7) fiat deferred"

    r = sup.route("power")
    assert r["action"] == "power" and r["fired"] is False, "(8) power guarded off"
    assert r["command"] == ["sudo", "poweroff"], "(9) power command reported"
    assert fired["n"] == 0, "(10) executor NOT called when not allowed"

    # power DOES fire when allowed — proven via injected executor
    fired2 = {"cmd": None}
    p2 = PowerAction(allowed=lambda: True,
                     executor=lambda cmd: fired2.__setitem__("cmd", cmd))
    res = p2.trigger()
    assert res["fired"] is True and fired2["cmd"] == ["sudo", "poweroff"], "(11) fires when allowed"

    # on the dev mac (Darwin) the real guard is always False
    if platform.system() == "Darwin":
        assert poweroff_allowed() is False, "(12) mac guard"

    print("PASS: kiosk selftest (12/12)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="ComposePlatform carousel kiosk host router/supervisor.")
    ap.add_argument("--broker", default=DEFAULT_BROKER,
                    help="MQTT broker host (default mqtt-broker.local)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--repo-root", default=REPO_ROOT)
    ap.add_argument("--no-autostart", action="store_true",
                    help="wire the bus but do not boot the carousel")
    ap.add_argument("--windowed", action="store_true",
                    help="spawn scenes WINDOWED (dev), not fullscreen")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    runner = SceneRunner(a.repo_root, a.broker, a.port,
                         fullscreen=not a.windowed)
    sup = KioskSupervisor(a.repo_root, a.broker, a.port, runner=runner)
    return sup.run(autostart=not a.no_autostart)


if __name__ == "__main__":
    raise SystemExit(main())
