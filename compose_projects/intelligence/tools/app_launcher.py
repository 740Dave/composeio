#!/usr/bin/env python3
"""app_launcher — the ONE generalized ComposePlatform Launch Bar launcher
([SPEC:CXC-LAUNCHBAR] [CXC-LB-LAUNCHER] [DD-LB-LAUNCH-MECHANISM]).

A tile press publishes `_app/<target>/open` ([CXC-LB-INTENT-BINDING]); THIS
in-process launcher subscribes the WHOLE `_app/+/open` family and routes each
intent target -> scene -> spawn through a single DATA TABLE (``TARGET_TABLE``),
NOT six sibling launchers.  That is the narrow-waist move the DSD ratified: one
launcher + a table, no six-fold fork.

It is a near-copy of ``decisions_viewer_launcher`` and REUSES its seams by
reference ([DX-NARROW-WAIST]): ``spawned_windows.SpawnedWindows`` (same-window
SKIP dedupe / reap), the ``singleton`` guard, the paho subscribe ``run()``
shape, the ``COMPOSEIO_WINDOW_X/_Y/_RAISE`` placement handoff, the repo-scoped
path guard, and an injectable spawn for testability.  Control-is-a-peer: a human
click and an Intelligence publish of the identical intent are indistinguishable
here — the launcher must not try to tell them apart ([CXC-LB-CREDO]).

Three spawn KINDS keep the register-guard honest ([CXC-LB-INTENT-TABLE] /
[CXC-DSDV-REGISTER]) — EVERY roster intent resolves to a registered consumer, so
no tile publishes into the void:

  * ``scene``     — spawn `runtime <repo-scoped scene>` (the decisions-launcher
                    shape); ``Target.transports=True`` adds ``--transports`` so a
                    scene that publishes an intent an out-of-process launcher
                    consumes (the storage viewers -> ``_app/doc/open``) reaches
                    the global bus.  LIVE scene targets: ``graph`` (the corpus-
                    agnostic Graph Viewer, [SPEC:CXC-GRAPH-VIEWER]),
                    ``docstore``/``busstore`` (the two storage navigation shells,
                    [CXC-DOCNAV-APP]/[CXC-BUSNAV-APP]).
  * ``host``      — the target IS the hosting console surface (``designer``); a
                    press re-focuses the host, it does NOT spawn a duplicate
                    ([CXC-LB-ACTIVE] default).  Logged, no spawn.
  * ``not_built`` — a roster target whose app is not built yet.  Logged as a
                    no-op; the ribbon renders such tiles DISABLED so they never
                    publish, but the consumer is registered regardless.  As of
                    the D7 storage-viewer slice NO roster target is not_built —
                    the kind is retained for the next unbuilt tile.

The two storage viewers graduated from ``not_built`` to ``scene`` (the D7 slice,
[DD-LB-STORAGE-VIEWERS-HOME]) — a TARGET_TABLE change + two new scenes, no new
launcher.

Run:  python3 app_launcher.py --mqtt mqtt://mqtt-broker.local:1883
      python3 app_launcher.py --selftest
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# tools dir on sys.path so the sibling import resolves in EVERY entry mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawned_windows  # sibling: the shared window-lifecycle tracker (de-fork)

# [SPEC:CXC-SOURCES-ON-BUS] — the ONE bus seam (REPO_ROOT/python is on sys.path above).
from composeio.app_topics import console_bbox_ns   # noqa: E402
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

# The `_app/<target>/open` family, at GLOBAL scope ([CXC-LB-INTENT-BINDING]) — an
# Intelligence on the external bus publishes these identically to the widget.
INTENT_PREFIX = "_app/"
INTENT_SUFFIX = "/open"
# The `_app/*` NAMESPACE FAMILY ([CXC-LB-INTENT-TABLE]). Was
# `"composeio/global/_app/+/open"` — an MQTT topic filter, which named the
# substrate AND pinned the scope; the bus pattern names neither
# ([CXC-SOB-THESIS], [CXC-SOB-EXTERNALITY]'s "classify the family, never a
# narrower glob"). `target_from_topic` reads the last three segments, so it
# works on the namespace unchanged.
INTENT_PATTERN = "_app/*"
# [DEC-LAUNCH-PLACEMENT] the console publishes its screen bbox here.
# [CXC-FC-S4-BBOX] DERIVED, not a literal. This was `"_app/console/bbox"` here AND in four
# sibling launchers — five spellings of one topic. The producer now derives its topic from
# the running app's id, so a consumer that kept the literal would subscribe a topic nobody
# publishes to. One definition, imported: `composeio.app_topics.console_bbox_ns()`.
BBOX_NS = console_bbox_ns()
LAUNCH_MARGIN = 10
DEFAULT_ORIGIN = (60, 60)

# Spawnable entrypoints (each a real, existing app).
# [SPEC:CXC-GRAPH-VIEWER] [CXC-GV-RENAME] the corpus-agnostic GRAPH VIEWER scene.
# This is the SINGLE definition of which scene the `graph` target opens; the
# dedicated decisions_viewer_launcher reads it FROM HERE by reference rather than
# keeping a second copy ([CXC-GV-ROUTE-TABLE] — the two launchers diverged once
# already, which is the recurrence this seam closes).
GRAPH_SCENE = "python/composeio/render/examples/graph_viewer.compose.yaml"
# The two storage-viewer navigation shells ([CXC-DOCNAV-APP]/[CXC-BUSNAV-APP]).
# Both publish `_app/doc/open` (consumed by the out-of-process doc_viewer_launcher
# over the GLOBAL bus), so they spawn with --transports (Target.transports=True).
DOCSTORE_SCENE = "apps/compose_platform_launch_bar/docstore_viewer.compose.yaml"
BUSSTORE_SCENE = "apps/compose_platform_launch_bar/busstore_viewer.compose.yaml"
COMPOSER_MODULE = "composeio.composer"            # python -m -> the UX Composer
MONITOR_MODULE = "composeio.composer.monitor"     # python -m -> the Headless Monitor
HARNESS_SCRIPT = "compose_projects/intelligence/launcher/launcher.py"  # the PTY /compact proxy

_FALSY = {"", "off", "none", "false", "0", "close"}

# Spawn KINDS.
SCENE = "scene"          # `runtime <scene>` — a windowed render app
MODULE = "module"        # `python -m <module>` — a windowed render app
TERMINAL = "terminal"    # a PTY/terminal app opened in a Terminal window
HOST = "host"            # the hosting console surface — re-focus, never spawn
NOT_BUILT = "not_built"  # a roster target whose app is not built yet — logged


class Target:
    """One row of the launch table: a target key -> its spawn kind + payload
    (a repo-relative ``scene``/``command`` path, or a ``module`` for -m)."""

    __slots__ = ("key", "kind", "scene", "module", "command", "note",
                 "transports")

    def __init__(self, key: str, kind: str, *, scene: Optional[str] = None,
                 module: Optional[str] = None, command: Optional[str] = None,
                 note: str = "", transports: bool = False) -> None:
        self.key = key
        self.kind = kind
        self.scene = scene
        self.module = module
        self.command = command
        self.note = note
        # a SCENE that must reach the GLOBAL bus (e.g. it publishes an
        # `_app/doc/open` intent an out-of-process launcher consumes) is spawned
        # with `runtime --transports`.  Default off (a pure static scene like the
        # Decisions viewer needs no bus leg).
        self.transports = transports


# The DATA TABLE ([DD-LB-LAUNCH-MECHANISM]) — the ordered roster of every launch
# intent this launcher consumes.  Every [CXC-LB-ROSTER] tile has a row here, so
# the register-guard ([CXC-LB-INTENT-TABLE]) passes by construction.  Five
# targets are LIVE; only the two storage viewers (a separate D7 slice) are
# not_built (David 2026-07-29).
TARGET_TABLE: Dict[str, Target] = {
    "designer": Target("designer", HOST,
                       note="the compose console host; re-focus, never spawn"),
    # RESOLVED (David 2026-07-29): the AI harness tile FOCUSES the already-running
    # harness, it does NOT spawn a new PTY session — same host-focus semantics as
    # designer (no RESUME_SESSION / env to decide).  The harness is the running
    # Intelligence/console session; a press raises it rather than launching a
    # second one.  (The PTY /compact proxy compose_projects/intelligence/launcher/launcher.py stays
    # the mechanism for a *cold* start, but the tile is a focus, not a cold start.)
    "harness":  Target("harness", HOST,
                       note="focus the already-running harness; never spawn (David 2026-07-29)"),
    "composer": Target("composer", MODULE, module=COMPOSER_MODULE,
                       note="the Visual/UX Composer (python -m composeio.composer)"),
    # [SPEC:CXC-GRAPH-VIEWER] RENAMED decisions -> graph (David 2026-08-06): the
    # viewer is corpus-AGNOSTIC (decisions are one corpus among many), so the
    # target key matches the ratified `_app/graph/*` namespace.  Leaving a
    # decisions-named target driving the general viewer is exactly the misleading
    # debt the namespace decision rejected.
    "graph":    Target("graph", SCENE, scene=GRAPH_SCENE,
                       note="the standalone Graph Viewer — corpus-agnostic "
                            "FactLattice explorer ([SPEC:CXC-GRAPH-VIEWER])"),
    "docstore": Target("docstore", SCENE, scene=DOCSTORE_SCENE, transports=True,
                       note="the Document Storage viewer ([CXC-DOCNAV-APP])"),
    "busstore": Target("busstore", SCENE, scene=BUSSTORE_SCENE, transports=True,
                       note="the Bus Storage viewer ([CXC-BUSNAV-APP])"),
    "bus":      Target("bus", MODULE, module=MONITOR_MODULE,
                       note="the ComposeBus/Headless Monitor "
                            "(python -m composeio.composer.monitor)"),
}


def registered_targets() -> Tuple[str, ...]:
    """Every roster target with a registered consumer ([CXC-LB-INTENT-TABLE]
    register-guard).  A by-construction test asserts this covers the 7 roster
    intents."""
    return tuple(TARGET_TABLE.keys())


def target_from_topic(topic: str) -> Optional[str]:
    """Extract ``<target>`` from ``composeio/global/_app/<target>/open`` (or a
    bare ``_app/<target>/open`` namespace), or None if it is not a launch
    intent topic."""
    parts = topic.split("/")
    # ... _app <target> open  — the last three segments define the intent.
    if len(parts) >= 3 and parts[-3] == "_app" and parts[-1] == "open":
        return parts[-2]
    return None


def parse_intent(payload: Any) -> bool:
    """True iff *payload* is a truthy `_app/<target>/open` OPEN signal (the
    decisions/doc launcher ``parse_intent``, verbatim).  Accepts the bus
    envelope (``{val: ...}``), a bare bool/int/str, or ``{open: ...}``."""
    d = payload
    if isinstance(d, dict) and "val" in d:
        d = d["val"]
    if isinstance(d, dict):
        d = d.get("open", d.get("path", True))
    if isinstance(d, bool):
        return d
    if isinstance(d, (int, float)):
        return d != 0
    if isinstance(d, str):
        return d.strip().lower() not in _FALSY
    if d is None:
        return False
    return bool(d)


def parse_bbox(payload: Any) -> Optional[Dict[str, int]]:
    d = payload
    if isinstance(d, dict) and isinstance(d.get("val"), dict):
        d = d["val"]
    if not isinstance(d, dict):
        return None
    try:
        return {"x": int(d["x"]), "y": int(d["y"]),
                "w": int(d["w"]), "h": int(d["h"])}
    except (KeyError, TypeError, ValueError):
        return None


def launch_origin(bbox: Optional[Dict[str, int]],
                  margin: int = LAUNCH_MARGIN) -> tuple:
    if not bbox:
        return DEFAULT_ORIGIN
    return (int(bbox["x"]) + int(bbox["w"]) + margin, int(bbox["y"]))


def resolve_scene(path: str) -> Optional[str]:
    """Resolve *path* under the repo root, refusing an escape ([CXC-LB-LAUNCHER]
    repo-scoped path guard — the decisions launcher's ``resolve_scene``
    mirrored).  Returns the absolute path iff it is a real file inside the repo,
    else None.  Defense-in-depth: the scene is a table CONSTANT, never from a
    payload, but the guard still runs."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        p = p.resolve()
        p.relative_to(REPO_ROOT.resolve())
    except (ValueError, OSError):
        return None
    return str(p) if p.is_file() else None


class AppLauncher:
    """Consumes the `_app/+/open` family and routes each target through
    TARGET_TABLE.  Spawn is injectable so intent->spawn is testable without a
    subprocess."""

    def __init__(self, spawn: Optional[Callable[[list], Any]] = None,
                 geometry_store: Optional[str] = None) -> None:
        # ONE injectable spawn: it takes a Popen argv and returns a process.  All
        # KINDS lower to (dedupe_key, argv), so a test injects a single recorder.
        self._spawn = spawn or self._spawn_argv
        # SKIP dedupe keyed on the target's window key (a second open while the
        # window is alive is skipped; a closed window respawns) — the SAME
        # shared lifecycle logic the doc/decisions launchers hold by reference.
        self._windows = spawned_windows.SpawnedWindows(spawned_windows.SKIP)
        self._console_bbox: Optional[Dict[str, int]] = None
        self._geometry_store = geometry_store

    def set_console_bbox(self, bbox: Optional[Dict[str, int]]) -> None:
        if bbox is not None:
            self._console_bbox = bbox

    def _plan(self, row: "Target") -> "tuple[Optional[str], Optional[list], str]":
        """Lower a spawnable table row to (dedupe_key, argv, rel_label), or
        (None, None, reason) when it cannot be spawned.  Every path a payload
        could name (a scene, the harness script) runs the repo-scoped guard."""
        if row.kind == SCENE:
            abspath = resolve_scene(row.scene or "")
            if abspath is None:
                return None, None, f"refused (scene missing/out-of-repo): {row.scene}"
            argv = [sys.executable, "-m", "composeio.render.engine.runtime", abspath]
            if row.transports:
                # the scene publishes an intent an out-of-process launcher
                # consumes over the global bus — connect its MQTT leg.
                argv.append("--transports")
            return abspath, argv, os.path.relpath(abspath, REPO_ROOT)
        if row.kind == MODULE:
            key = f"module:{row.module}"
            return key, [sys.executable, "-m", str(row.module)], f"-m {row.module}"
        if row.kind == TERMINAL:
            abspath = resolve_scene(row.command or "")   # repo-scoped guard
            if abspath is None:
                return None, None, (f"refused (command missing/out-of-repo): "
                                    f"{row.command}")
            argv = self._terminal_argv(abspath)
            if argv is None:
                return None, None, (f"no terminal-open on this platform "
                                    f"({sys.platform}) — run {row.command} manually")
            return f"terminal:{abspath}", argv, os.path.relpath(abspath, REPO_ROOT)
        return None, None, f"unspawnable kind: {row.kind}"

    def open(self, target: str) -> str:
        """Route one open intent for *target*.  Returns a human status string;
        never raises into the message loop."""
        row = TARGET_TABLE.get(target)
        if row is None:
            # an UNREGISTERED target — outside the roster (a test/typo).  Logged,
            # never spawned.  The register-guard keeps this off the happy path.
            return f"unregistered target (ignored): {target}"
        if row.kind == HOST:
            return f"host focus (no spawn): {target} — {row.note}"
        if row.kind == NOT_BUILT:
            return f"not built yet (no-op): {target} — {row.note}"
        key, argv, label = self._plan(row)
        if key is None or argv is None:
            return f"{target}: {label}"
        proc, action = self._windows.register(key, lambda: self._spawn(argv))
        if action == "skipped":
            return f"already open (skip): {target} -> {label}"
        if action == "refused":
            return f"spawn refused: {target} -> {label}"
        pid = getattr(proc, "pid", "?")
        return f"opened {target} -> {label} (pid {pid})"

    def _terminal_argv(self, script_abspath: str) -> Optional[list]:
        """A Terminal-window invocation for a PTY/terminal app (the harness).
        macOS: `open -a Terminal <script>` (console-only tier, macOS-primary).
        Other platforms: None -> the caller reports "run it manually".  FLAGGED:
        this opens the launcher with no per-Intelligence id/env — the exact
        harness invocation is unresolved and awaits David's ratification."""
        if sys.platform == "darwin":
            return ["open", "-a", "Terminal", script_abspath]
        return None

    def _spawn_argv(self, argv: list):
        """Spawn *argv* detached, cwd = repo root (the doc/decisions launcher
        spawn idiom).  Render-app placement + geometry-store via env (harmless
        for the terminal open)."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "python") + os.pathsep + \
            env.get("PYTHONPATH", "")
        origin = launch_origin(self._console_bbox)
        env["COMPOSEIO_WINDOW_X"] = str(int(origin[0]))
        env["COMPOSEIO_WINDOW_Y"] = str(int(origin[1]))
        env["COMPOSEIO_WINDOW_RAISE"] = "1"
        if self._geometry_store:
            env["COMPOSEIO_GEOMETRY_STORE"] = self._geometry_store
        return subprocess.Popen(argv, cwd=str(REPO_ROOT), env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)


# ---------------------------------------------------------------------------
# the bus leg — subscribe the `_app/*` intent family, spawn per target
# ---------------------------------------------------------------------------
def run(mqtt_url: str, geometry_store: Optional[str] = None,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS] — the intent->spawn daemon on the bus.

    REPEAT DELIVERY: REQUIRED, obtained via `subscribe_pattern` ([CXC-SOB-SUBSCRIBE]).
    `_app/<target>/open` is a pure OPEN SIGNAL — `parse_intent` collapses the payload
    to a bool, so the value published for the second click is `True` again, identical
    to the first. Under an exact `bus.subscribe` the second click would be absorbed by
    `Namespace.publish_state`'s `new != old` check, and re-opening a window the human
    just closed would silently do nothing. `SpawnedWindows` already owns the
    "is it still alive?" decision (SKIP-while-alive, reap-when-dead); moving that
    judgement into the bus's dedup would make it depend on payload identity instead of
    on the process table, which is the wrong instrument entirely.
    """
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("app-launcher")
    if lock is None:
        sys.stderr.write("[app_launcher] another instance holds the singleton; "
                         "yielding\n")
        return 0

    SRC = "app-launcher"               # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
    launcher = AppLauncher(geometry_store=geometry_store)

    b = source_bus.acquire_bus(mqtt_url, label=SRC, bus=bus)
    # Declare the bbox plus every roster target's intent namespace, so the boot
    # log names the whole surface this daemon actuates (SOB-C3) rather than
    # leaving it implicit in a wildcard.
    source_bus.declare(b, BBOX_NS, scope=Scope.GLOBAL, source=SRC, label="console bbox")
    for _t in registered_targets():
        source_bus.declare(b, f"{INTENT_PREFIX}{_t}{INTENT_SUFFIX}",
                           scope=Scope.GLOBAL, source=SRC, label="launch intent")
    source_bus.manifest(SRC)

    def _on_app(ns: str, value, _old) -> None:
        if ns == BBOX_NS:
            launcher.set_console_bbox(parse_bbox(value))
            return
        target = target_from_topic(ns)
        if target is None or not parse_intent(value):
            return
        sys.stderr.write(f"[app_launcher] {launcher.open(target)}\n")

    b.subscribe_pattern(INTENT_PATTERN, _on_app)
    sys.stderr.write(f"[app_launcher] listening {INTENT_PATTERN} + {BBOX_NS}"
                     f"  (targets: {', '.join(registered_targets())})\n")
    source_bus.park(stop)
    return 0


# ---------------------------------------------------------------------------
# selftest — logic only, no broker, no network, mocked spawn
# ---------------------------------------------------------------------------
def _selftest() -> int:
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # register-guard: every roster intent has a registered consumer (LB-C5).
    roster = {"designer", "harness", "composer", "graph",
              "docstore", "busstore", "bus"}
    check(set(registered_targets()) == roster,
          f"(1) register-guard covers the 7 roster targets: {registered_targets()}")

    # topic -> target extraction (global scope + bare namespace).
    check(target_from_topic("composeio/global/_app/graph/open") == "graph",
          "(2) target from global topic")
    check(target_from_topic("_app/busstore/open") == "busstore",
          "(3) target from bare namespace")
    check(target_from_topic("composeio/global/_app/console/bbox") is None,
          "(4) non-open topic -> None")

    # intent parsing.
    check(parse_intent("open") is True and parse_intent(True) is True,
          "(5) truthy opens")
    check(parse_intent("") is False and parse_intent("close") is False
          and parse_intent(0) is False and parse_intent(None) is False,
          "(6) falsy/close -> no open")

    # path guard.
    check(resolve_scene(GRAPH_SCENE) is not None,
          "(7) decisions scene resolves under repo")
    check(resolve_scene("/etc/passwd") is None
          and resolve_scene("../../../../etc/hosts") is None,
          "(8) outside-repo / traversal refused")

    # intent -> spawn, with a mocked spawn (LB-C3/C6).  The injected spawn takes
    # a Popen argv; a recorder captures every launch.
    class FakeProc:
        def __init__(self):
            self.pid = 4242
            self._alive = True

        def poll(self):
            return None if self._alive else 0

    spawned = []

    def fake_spawn(argv):
        spawned.append(argv)
        return FakeProc()

    lau = AppLauncher(spawn=fake_spawn)
    scene_abs = resolve_scene(GRAPH_SCENE)
    r = lau.open("graph")
    check(r.startswith("opened graph") and len(spawned) == 1,
          "(9) graph -> one spawn of the Graph Viewer scene")
    check(spawned[0][-1] == scene_abs,
          "(10) spawn argv ends at the Graph Viewer scene under repo root")
    # SKIP dedupe: a second open while alive spawns nothing.
    check("already open" in lau.open("graph") and len(spawned) == 1,
          "(11) same-scene dedupe (skip)")
    # reap: a dead window respawns fresh.
    lau._windows.live[scene_abs]._alive = False
    check(lau.open("graph").startswith("opened") and len(spawned) == 2,
          "(12) reopen after close spawns fresh")

    # MODULE targets spawn `python -m <module>` (composer + the ComposeBus/
    # Headless Monitor) — real, existing apps.
    check(lau.open("composer").startswith("opened composer")
          and spawned[-1][1:3] == ["-m", COMPOSER_MODULE],
          "(13) composer -> python -m composeio.composer")
    check(lau.open("bus").startswith("opened bus")
          and spawned[-1][1:3] == ["-m", MONITOR_MODULE],
          "(14) bus -> python -m composeio.composer.monitor")

    # HOST target — never spawns (the console re-focuses itself).
    n = len(spawned)
    check("host focus" in lau.open("designer") and len(spawned) == n,
          "(15) designer is host focus, no spawn")

    # STORAGE VIEWERS (D7): docstore/busstore now spawn their navigation-shell
    # scenes WITH --transports (they publish `_app/doc/open` over the global bus).
    for tgt, scene_const in (("docstore", DOCSTORE_SCENE),
                             ("busstore", BUSSTORE_SCENE)):
        r = lau.open(tgt)
        check(r.startswith(f"opened {tgt}"), f"(16) {tgt} -> scene spawn: {r}")
        check(spawned[-1][-2:] == [resolve_scene(scene_const), "--transports"],
              f"(16b) {tgt} argv ends at its scene + --transports: {spawned[-1]}")
    check(len(spawned) == n + 2, "(17) both storage viewers spawned")
    n = len(spawned)          # rebase after the two storage-viewer spawns

    # HOST target (harness): FOCUSES the already-running harness, never spawns a
    # new PTY session (David 2026-07-29) — same host-focus semantics as designer.
    hrow = TARGET_TABLE["harness"]
    check(hrow.kind == HOST,
          "(18) harness is a HOST kind (focus the running harness, never spawn)")

    # an unregistered/typo target is ignored, never spawns.
    check("unregistered" in lau.open("bogus") and len(spawned) == n,
          "(19) unregistered target ignored")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("PASS: app_launcher selftest (19 checks)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="The generalized Launch Bar launcher: routes _app/<target>/"
                    "open -> scene -> spawn via a data table.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--geometry-store", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, a.geometry_store, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
