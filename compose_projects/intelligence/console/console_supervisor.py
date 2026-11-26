#!/usr/bin/env python3
"""console_supervisor — own the Intelligence Console process so it can be
stopped / started / restarted over the ComposeBus.

Why a supervisor rather than a self-terminating console: something must be
alive to bring it BACK. A supervisor that owns the child can also restart a
console that CRASHED, not just one that was asked to stop.

ADDRESSED BY CONSTRUCTION (the multi-instance requirement, David 2026-07-20):
more than one console may run — e.g. two Intelligences on different projects.
So the lifecycle topic is PER-INSTANCE:

    _app/console/<instance-id>/lifecycle      <- commands, one instance only
    _app/console/<instance-id>/state          <- this supervisor's state
    _app/console/registry/<instance-id>       <- discovery: who is running

There is deliberately NO broadcast lifecycle topic: a terminate cannot reach
another instance because there is no topic that addresses them all. (Defense in
depth: a payload may carry `target`, and a mismatch is refused.) To act on
several consoles you must address each one — an explicit act, never a slip.

Commands (payload `{"cmd": ...}` on the lifecycle topic):
    restart | stop | start | status

Run:  python3 console_supervisor.py --id architect \
          --scene compose_projects/intelligence/console/console.compose.yaml --mqtt mqtt://mqtt-broker.local:1883
      python3 console_supervisor.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import List, Optional

# [SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS] — the ONE bus seam. This module used
# to reach the `bus_envelope` framer over a hand-built paho client; it now takes a
# bus handle and states a NAMESPACE + a SCOPE. `bus_envelope` is MQTT
# implementation detail that [CXC-SOB-ENDSTATE] folds behind `transport_mqtt`, so
# importing it from a console peer was a live path back around the waist.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

DEFAULT_SCENE = "compose_projects/intelligence/console/console.compose.yaml"
_VALID = ("restart", "stop", "start", "status")


def lifecycle_ns(instance_id: str) -> str:
    return f"_app/console/{instance_id}/lifecycle"


def state_ns(instance_id: str) -> str:
    return f"_app/console/{instance_id}/state"


def registry_ns(instance_id: str) -> str:
    return f"_app/console/registry/{instance_id}"


def parse_command(payload, instance_id: str) -> Optional[str]:
    """The command in *payload* iff it is well-formed AND addressed to us.

    Accepts the bare envelope value or a nested `val`. A `target` field, when
    present, MUST equal our instance id (defense in depth on top of the
    per-instance topic). Returns None for anything malformed/misaddressed —
    a supervisor never guesses what a garbled command meant."""
    d = payload
    if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), (dict, str)):
        d = d["val"]
    if isinstance(d, str):
        d = {"cmd": d}
    if not isinstance(d, dict):
        return None
    target = d.get("target")
    if target is not None and str(target) != instance_id:
        return None                      # addressed elsewhere — refuse
    cmd = str(d.get("cmd", "")).strip().lower()
    return cmd if cmd in _VALID else None


class ConsoleSupervisor:
    def __init__(self, instance_id: str, scene: str, repo_root: str,
                 extra_args: Optional[List[str]] = None):
        self.id = instance_id
        self.scene = scene
        self.repo_root = repo_root
        self.extra_args = extra_args or ["--transports"]
        self.proc: Optional[subprocess.Popen] = None
        self.started_at: Optional[float] = None
        self.restarts = 0
        self._lock = threading.Lock()

    # -- process ownership ----------------------------------------------------

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> str:
        with self._lock:
            if self.is_running():
                return f"already running (pid {self.proc.pid})"
            cmd = [sys.executable, "-m", "composeio.render.engine.runtime",
                   self.scene] + self.extra_args
            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.join(self.repo_root, "python") + \
                os.pathsep + env.get("PYTHONPATH", "")
            # [SPEC:CXC-CONSOLE-PROD] per-instance personalization storage: point
            # the geometry seam ([CXC-ICP-S1-RESIZE] window persistence) AND the
            # personalization read ([CXC-ICP-FOUND-A]) at THIS instance's
            # meta-object, so window/font/theme all live in compose_projects/intelligence/meta/
            # <id>.meta.json — the reuse-by-reference per-instance store.
            env.setdefault("COMPOSEIO_REPO_ROOT", self.repo_root)
            meta = os.path.join(self.repo_root, "compose_projects", "intelligence", "meta",
                                f"{self.id}.meta.json")
            env.setdefault("COMPOSEIO_GEOMETRY_STORE", meta)
            env.setdefault("COMPOSEIO_CONSOLE_META", meta)
            self.proc = subprocess.Popen(cmd, cwd=self.repo_root, env=env,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,
                                         start_new_session=True)
            self.started_at = time.time()
            return f"started (pid {self.proc.pid})"

    def stop(self, timeout: float = 6.0) -> str:
        with self._lock:
            if not self.is_running():
                self.proc = None
                return "not running"
            pid = self.proc.pid
            try:
                # own the whole process group (start_new_session) so a windowed
                # child with helpers goes down cleanly
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            deadline = time.time() + timeout
            while time.time() < deadline and self.proc.poll() is None:
                time.sleep(0.15)
            if self.proc.poll() is None:                    # refused to die
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    self.proc.kill()
            self.proc = None
            self.started_at = None
            return f"stopped (pid {pid})"

    def restart(self) -> str:
        s = self.stop()
        time.sleep(0.4)                                     # let the surface release
        r = self.start()
        self.restarts += 1
        return f"restarted — {s}; {r}"

    # -- state ---------------------------------------------------------------

    def state(self) -> dict:
        running = self.is_running()
        return {
            "id": self.id, "scene": self.scene,
            "state": "running" if running else "stopped",
            "pid": self.proc.pid if running else None,
            "uptime_s": round(time.time() - self.started_at, 1)
            if (running and self.started_at) else 0,
            "restarts": self.restarts,
            "lifecycle_ns": lifecycle_ns(self.id),
        }

    def handle(self, cmd: str) -> str:
        if cmd == "restart":
            return self.restart()
        if cmd == "stop":
            return self.stop()
        if cmd == "start":
            return self.start()
        return json.dumps(self.state())                      # status


def run(instance_id: str, scene: str, repo_root: str, mqtt_url: str,
        extra_args: List[str], autostart: bool, poll_s: float,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS] — the out-of-process supervisor
    on the bus.

    Not a console DATA SOURCE (it is a separate OS process that OWNS the console),
    so it is not in [CXC-SOB-INVENTORY] — but it is squarely inside the seam lint's
    governed surface, and its `_app/console/*` family is exactly the traffic
    [CXC-SOB-EXTERNALITY] classifies as external. Nothing about "it runs in its own
    process" makes hardcoding MQTT correct.

    REPEAT DELIVERY: REQUIRED on the lifecycle leg, obtained via `subscribe_pattern`
    ([CXC-SOB-SUBSCRIBE]). `{"cmd": "restart"}` twice in a row is the ordinary case —
    a console that came back wrong gets restarted again — and the payload carries no
    nonce. An exact `bus.subscribe` would notify only when `new != old`, so the
    second restart would be silently ignored and the operator would watch a broken
    console not come back. Pattern subscribers are notified unconditionally.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): per-<instance_id> — one supervisor per
    # console instance. Drop the PID suffix; a second instance yields before it
    # spawns a rival console child.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    _key = f"console-supervisor-{instance_id}"
    lock = acquire_singleton(_key)
    if lock is None:
        sys.stderr.write(f"[console_supervisor:{instance_id}] another {_key} holds the singleton; yielding\n")
        return 0
    sup = ConsoleSupervisor(instance_id, scene, repo_root, extra_args)
    SRC = f"console-supervisor:{instance_id}"    # FROZEN wire field
    life_ns, st_ns, reg_ns = (lifecycle_ns(instance_id), state_ns(instance_id),
                              registry_ns(instance_id))

    b = source_bus.acquire_bus(mqtt_url, label=_key, bus=bus)
    for _ns in (life_ns, st_ns, reg_ns):
        source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                           label="console lifecycle")
    source_bus.manifest(SRC)

    def publish(ns: str, val) -> None:
        b.publish_state(ns, val, source=SRC)

    def on_lifecycle(ns: str, value, _old) -> None:
        if ns != life_ns:
            return                       # another `_app/console/*` member; not ours
        cmd = parse_command(value, instance_id)
        if cmd is None:
            sys.stderr.write(f"[console_supervisor:{instance_id}] ignored malformed/misaddressed command\n")
            return
        try:
            result = sup.handle(cmd)
            sys.stderr.write(f"[console_supervisor:{instance_id}] {cmd} -> {result}\n")
        except Exception as exc:            # a bad command must never kill the supervisor
            sys.stderr.write(f"[console_supervisor:{instance_id}] {cmd} FAILED: {exc!r}\n")
        publish(st_ns, sup.state())

    b.subscribe_pattern("_app/console/*", on_lifecycle)
    sys.stderr.write(f"[console_supervisor:{instance_id}] listening {life_ns}\n")

    if autostart:
        sys.stderr.write(f"[console_supervisor:{instance_id}] {sup.start()}\n")

    def _beat() -> None:                                     # heartbeat state + registry
        publish(st_ns, sup.state())
        publish(reg_ns, sup.state())

    try:
        _beat()                             # first beat immediately, as before
        # [CXC-SOB-RUN-BLOCKS]: park instead of `while True: sleep`. `on_tick`
        # carries the heartbeat, and the stop Event makes shutdown prompt rather
        # than up to `poll_s` late — which matters here, because this process
        # OWNS the console child and a slow exit orphans a window.
        source_bus.park(stop, poll_s=poll_s, on_tick=_beat)
    except KeyboardInterrupt:
        pass
    finally:
        sup.stop()                                           # never orphan the console
    return 0


def _selftest() -> int:
    # addressing: only our own id is accepted
    assert parse_command({"cmd": "restart"}, "architect") == "restart", "(1) bare cmd"
    assert parse_command({"val": {"cmd": "stop"}}, "architect") == "stop", "(2) nested val"
    assert parse_command("start", "architect") == "start", "(3) bare string"
    assert parse_command({"cmd": "restart", "target": "architect"}, "architect") == "restart", "(4) matching target"
    assert parse_command({"cmd": "restart", "target": "other"}, "architect") is None, "(5) MISADDRESSED refused"
    assert parse_command({"cmd": "nuke"}, "architect") is None, "(6) unknown cmd refused"
    assert parse_command({"nope": 1}, "architect") is None, "(7) malformed refused"
    assert parse_command(None, "architect") is None, "(8) None refused"
    # topics are per-instance — no shared/broadcast topic exists
    a, b = lifecycle_ns("architect"), lifecycle_ns("second")
    assert a != b and "architect" in a, "(9) lifecycle topic is per-instance"
    assert registry_ns("x") == "_app/console/registry/x", "(10) registry topic"
    # lifecycle over a real child process (a trivial sleeper stands in for the console)
    import tempfile
    root = tempfile.mkdtemp()
    sup = ConsoleSupervisor("t", "scene.yaml", root)
    sup.extra_args = []
    sup.start = lambda: (_start_sleeper(sup))              # type: ignore
    msg = sup.start()
    assert sup.is_running(), f"(11) child started: {msg}"
    pid = sup.proc.pid
    assert sup.state()["state"] == "running" and sup.state()["pid"] == pid, "(12) state reports running"
    out = sup.stop()
    assert not sup.is_running() and "stopped" in out, f"(13) child stopped: {out}"
    assert sup.state()["state"] == "stopped" and sup.state()["pid"] is None, "(14) state reports stopped"
    assert sup.stop() == "not running", "(15) idempotent stop"
    print("PASS: console_supervisor selftest (15/15)")
    return 0


def _start_sleeper(sup: "ConsoleSupervisor") -> str:
    sup.proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    sup.started_at = time.time()
    return f"started (pid {sup.proc.pid})"


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description="Supervise the Intelligence Console over the ComposeBus.")
    ap.add_argument("--id", default="architect", help="console instance id (addresses its topics)")
    ap.add_argument("--scene", default=DEFAULT_SCENE)
    ap.add_argument("--repo-root", default=os.getcwd())
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--console-args", default="--transports",
                    help="args passed to the console runtime (quoted)")
    ap.add_argument("--no-autostart", action="store_true")
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.id, a.scene, a.repo_root, a.mqtt, shlex.split(a.console_args),
               not a.no_autostart, a.poll, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
