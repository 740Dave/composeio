#!/usr/bin/env python3
"""decisions_viewer_launcher — spawn the STANDALONE Decisions viewer WINDOW on
an `_app/decisions/open` intent ([SPEC:CXC-DVL] [CXC-DVL-LAUNCHER]).

A SIBLING of ``doc_viewer_launcher`` (the narrow waist, [DX-NARROW-WAIST]) — it
REUSES the same seams by reference: ``spawned_windows.SpawnedWindows`` for
window-lifecycle dedupe, the ``singleton`` guard, the paho subscribe ``run()``
shape, the ``COMPOSEIO_WINDOW_X/_Y/_RAISE`` placement handoff, and an injectable
spawn for testability.  The console rail's Open button PUBLISHES the intent; THIS
in-process launcher (registered in compose_projects/intelligence/console/data_sources.py, NOT a
daemon) owns the runtime — the WIDGET-owns-intent / launcher-owns-runtime split
the doc launcher already follows.

Two decisions-specific shapes vs. the doc launcher:

  SINGLE KNOWN SCENE (not per-doc): the Decisions viewer is ONE scene
  (python/composeio/render/examples/decisions_tab.compose.yaml), so there is no
  per-open scene file — the launcher spawns that repo-constant scene.  The
  SpawnedWindows(SKIP) key is the ONE scene path: a second Open while a viewer
  is alive is SKIPPED (at most one standalone Decisions window); a
  closed-then-reopened viewer spawns fresh (dead processes reaped).

  GEOMETRY STORE HANDOFF ([CXC-DVL-GEOMETRY], architect PER-INSTANCE ruling):
  the launcher hands the spawned viewer ``COMPOSEIO_GEOMETRY_STORE`` (the
  instance's meta.<id>.meta.json — the config_persister meta-object seam) so the
  window restores/persists its geometry in the per-instance record.  Absent (a
  direct launch), the runtime falls back to a per-app geometry file.

The repo-scoped path guard is retained as defense-in-depth even though the scene
path is a constant — no intent payload can ever point the launcher at an
arbitrary file.

Run:  python3 decisions_viewer_launcher.py --mqtt mqtt://mqtt-broker.local:1883
      python3 decisions_viewer_launcher.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# tools dir on sys.path so the sibling import resolves in EVERY entry mode
# (run-as-script, bare import from data_sources, exec_module-by-path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawned_windows  # sibling: the shared window-lifecycle tracker (de-fork)
# [CXC-GV-ROUTE-TABLE] the ROUTING CONTRACT (target key -> scene) is owned by ONE
# data table: app_launcher.TARGET_TABLE.  This launcher READS that row instead of
# keeping a second copy of the scene path.  The two launchers already diverged
# once — app_launcher kept routing the ribbon at the OLD decisions_tab scene after
# this one was repointed, so the fix "landed" and changed nothing on the glass
# (DEFECT-FORK-DIVERGENCE-RECURRING-CLASS-A-62CF12 recurrence).  Reading the row
# by reference makes that specific divergence structurally impossible.
import app_launcher  # sibling: owns TARGET_TABLE, the one routing table

# The target key this dedicated launcher serves; its scene + intent are DERIVED
# from the shared table, never restated.
TARGET_KEY = "graph"

# [DD-GV-INTENT-NAMESPACE] the open intent GENERALIZES to `_app/graph/open`
# ([SPEC:CXC-GRAPH-VIEWER] [CXC-GV-INTENTS]): the launcher's INTENT_NS and the
# rail's Open publish move together (one change, both ends).  Derived from the
# target key so the namespace and the table row can never disagree.
INTENT_NS = f"{app_launcher.INTENT_PREFIX}{TARGET_KEY}{app_launcher.INTENT_SUFFIX}"
# The namespace the SPAWNED viewer subscribes for its open scope — the same
# topic, handed in as scene-initial state ([CXC-GV-SCOPE]).
GRAPH_OPEN_NS = INTENT_NS
# The runtime's scene-initial seam ({namespace: value} JSON, applied before the
# first frame) — reused by reference, mirroring away_watch_publisher.
SCENE_INITIAL_ENV = "COMPOSEIO_SCENE_INITIAL"
# [DEC-LAUNCH-PLACEMENT] the console publishes its screen bbox here; the viewer
# opens top-aligned, LAUNCH_MARGIN px right of the console's right edge.
# [CXC-FC-S4-BBOX] DERIVED, not a literal. This was `"_app/console/bbox"` here AND in four
# sibling launchers — five spellings of one topic. The producer now derives its topic from
# the running app's id, so a consumer that kept the literal would subscribe a topic nobody
# publishes to. One definition, imported: `composeio.app_topics.console_bbox_ns()`.
from composeio.app_topics import console_bbox_ns              # noqa: E402
BBOX_NS = console_bbox_ns()

# [SPEC:CXC-SOURCES-ON-BUS] slice 5 — the ONE bus seam.
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402
SRC = "decisions-viewer-launcher"  # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "decisions-viewer-launcher"
LAUNCH_MARGIN = 10
DEFAULT_ORIGIN = (60, 60)

# The ONE standalone Graph Viewer scene ([CXC-DVL-LAUNCHER] SPAWN TARGET;
# [CXC-GV-RENAME] the scene generalizes decisions_tab -> graph_viewer).
# DERIVED from the shared routing table ([CXC-GV-ROUTE-TABLE]) — one definition,
# two consumers, so a future scene move cannot repoint one launcher and miss the
# other.  Falls back to the literal path only if the row somehow vanished, so a
# table edit degrades to a loud missing-scene refusal, never a silent wrong scene.
DECISIONS_SCENE = REPO_ROOT / (
    getattr(app_launcher.TARGET_TABLE.get(TARGET_KEY), "scene", None)
    or "python/composeio/render/examples/graph_viewer.compose.yaml")
# The window-identity key the scene declares (design.window_key); the geometry
# store keys on this, so the launcher and the runtime agree.  MIGRATED from
# `decisions-viewer` to `graph-viewer` ([CXC-GV-RENAME]).
WINDOW_KEY = "graph-viewer"

# open intents that mean "close" — nothing to close (the window model has no
# close leg), so they spawn nothing (mirrors doc parse_intent's falsy handling).
_FALSY = {"", "off", "none", "false", "0", "close"}


def parse_intent(payload: Any) -> bool:
    """True iff *payload* is a truthy `_app/decisions/open` OPEN signal, else
    False.  The Decisions viewer is a single known scene, so the intent is a
    bare open signal (not a path): any non-falsy publish opens it.  Accepts the
    bus envelope (``{val: ...}``), a bare bool/int/str, or ``{open: ...}``."""
    d = payload
    if isinstance(d, dict) and "val" in d:
        d = d["val"]
    if isinstance(d, dict):
        d = d.get("open", d.get("decisions", d.get("path", True)))
    if isinstance(d, bool):
        return d
    if isinstance(d, (int, float)):
        return d != 0
    if isinstance(d, str):
        return d.strip().lower() not in _FALSY
    if d is None:
        return False
    return bool(d)


def parse_scope(payload: Any) -> Optional[Dict[str, Any]]:
    """The optional SCOPE an `_app/graph/open` intent carries — the corpus to
    select and the session id(s) to preselect ([SPEC:CXC-GRAPH-VIEWER]
    [CXC-GV-SCOPE]) — or None when the intent is a bare open signal (the console
    rail's Open), which must stay UNSCOPED.

    Shape: ``{corpus?, sessions?: [...], scope_label?}``.  ``sessions`` accepts a
    list or a single scalar (a run may span SEVERAL sessions).  The intent carries
    only these SCALARS — never graph data ([CXC-GV-INTENTS])."""
    d = payload
    if isinstance(d, dict) and "val" in d:
        d = d["val"]
    if not isinstance(d, dict):
        return None
    raw = d.get("sessions", d.get("session"))
    sessions: List[str] = []
    if isinstance(raw, (list, tuple, set)):
        sessions = [str(s).strip() for s in raw if str(s or "").strip()]
    elif raw not in (None, ""):
        sessions = [str(raw).strip()]
    corpus = str(d.get("corpus") or "").strip()
    label = str(d.get("scope_label") or "").strip()
    if not sessions and not corpus:
        return None
    out: Dict[str, Any] = {"sessions": sessions}
    if corpus:
        out["corpus"] = corpus
    if label:
        out["scope_label"] = label
    return out


def parse_bbox(payload: Any) -> Optional[Dict[str, int]]:
    """The console bbox ``{x, y, w, h}`` from an `_app/console/bbox` publish, or
    None (mirrors doc_viewer_launcher.parse_bbox)."""
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
    """The (x, y) origin for the viewer ([DEC-LAUNCH-PLACEMENT]): top-aligned to
    the console, *margin* px right of its right edge; DEFAULT_ORIGIN with none."""
    if not bbox:
        return DEFAULT_ORIGIN
    return (int(bbox["x"]) + int(bbox["w"]) + margin, int(bbox["y"]))


def resolve_scene(path: str) -> Optional[str]:
    """Resolve *path* under the repo root, refusing an escape ([CXC-DVL-LAUNCHER]
    REPO-SCOPED PATH GUARD — the doc launcher's resolve_doc mirrored).  Returns
    the absolute path iff it is a real file inside the repo, else None."""
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        p = p.resolve()
        p.relative_to(REPO_ROOT.resolve())      # inside the repo, or ValueError
    except (ValueError, OSError):
        return None
    return str(p) if p.is_file() else None


# ---------------------------------------------------------------------------
# the launcher: guard -> spawn the one scene, with same-window dedupe
# ---------------------------------------------------------------------------
class DecisionsViewerLauncher:
    """Consumes decisions-open intents and spawns AT MOST ONE standalone
    Decisions window (SKIP dedupe on the single scene path).  The spawn is
    injectable so the intent->spawn logic is testable without a subprocess."""

    def __init__(self, spawn: Optional[Callable[[str], Any]] = None,
                 geometry_store: Optional[str] = None) -> None:
        self._spawn = spawn or self._spawn_runtime
        # SKIP policy keyed on the ONE decisions scene path: a second Open while
        # a viewer is alive is skipped; a closed viewer spawns fresh (dead
        # reaped) — the same shared lifecycle logic the doc launcher holds by
        # reference, so a leak fix to one can never miss the other.
        self._windows = spawned_windows.SpawnedWindows(spawned_windows.SKIP)
        self._console_bbox: Optional[Dict[str, int]] = None
        # the per-instance geometry store the spawned viewer inherits
        # ([CXC-DVL-GEOMETRY]); None -> the runtime's per-app fallback.
        self._geometry_store = geometry_store
        # the scope the CURRENT open request carries ([CXC-GV-SCOPE]); consumed
        # by _spawn_runtime for this spawn only.
        self._pending_scope: Optional[Dict[str, Any]] = None

    def set_console_bbox(self, bbox: Optional[Dict[str, int]]) -> None:
        if bbox is not None:
            self._console_bbox = bbox

    def open(self, scope: Optional[Dict[str, Any]] = None) -> str:
        """Handle one open request.  Returns a human status string; never
        raises into the message loop.

        ``scope`` ([CXC-GV-SCOPE]) is the optional ``{corpus, sessions,
        scope_label}`` an intent carried; it is handed to the SPAWNED viewer as
        scene-initial state.  None (the rail's bare Open) opens UNSCOPED."""
        abspath = resolve_scene(str(DECISIONS_SCENE))
        if abspath is None:
            return f"refused (decisions scene missing): {DECISIONS_SCENE}"
        self._pending_scope = scope
        proc, action = self._windows.register(abspath,
                                               lambda: self._spawn(abspath))
        rel = os.path.relpath(abspath, REPO_ROOT)
        if action == "skipped":
            return f"already open (skip): {rel}"
        if action == "refused":
            return f"spawn refused: {rel}"
        pid = getattr(proc, "pid", "?")
        return f"opened {rel} (pid {pid})"

    def _spawn_runtime(self, scene_file: str):
        """Spawn the viewer: `runtime <decisions scene>` (no --transports — the
        standalone viewer renders its static neighborhood and persists geometry
        locally).  Detached, output silenced, cwd = repo root (the doc-launcher
        spawn idiom).  Placement + geometry-store handed via env."""
        cmd = [sys.executable, "-m", "composeio.render.engine.runtime", scene_file]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "python") + os.pathsep + \
            env.get("PYTHONPATH", "")
        origin = launch_origin(self._console_bbox)
        env["COMPOSEIO_WINDOW_X"] = str(int(origin[0]))
        env["COMPOSEIO_WINDOW_Y"] = str(int(origin[1]))
        env["COMPOSEIO_WINDOW_RAISE"] = "1"
        # [CXC-DVL-GEOMETRY] per-instance geometry store (meta.<id>) — the
        # persisted geometry overrides the beside-console placement above.
        if self._geometry_store:
            env["COMPOSEIO_GEOMETRY_STORE"] = self._geometry_store
        # [CXC-GV-SCOPE] hand the open intent's scope to the spawned viewer as
        # SCENE-INITIAL state — the same race-free `COMPOSEIO_SCENE_INITIAL`
        # seam the Night Watch relaunch uses (applied before the first frame),
        # NOT a new mechanism.  The viewer subscribes `_app/graph/open`, so the
        # seeded value reaches it exactly as a live intent would.
        if self._pending_scope:
            try:
                env[SCENE_INITIAL_ENV] = json.dumps(
                    {GRAPH_OPEN_NS: dict(self._pending_scope)})
            except (TypeError, ValueError):
                pass
        return subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)


# ---------------------------------------------------------------------------
# transport (mirrors doc_viewer_launcher paho subscribe/consume)
# ---------------------------------------------------------------------------
def _parse_mqtt_url(url: str):
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


def run(mqtt_url: str, geometry_store: Optional[str] = None,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 5 — a SUBSCRIBING source.

    SUBSCRIBES VIA `subscribe_pattern`, NOT `subscribe`, AND THAT IS LOAD-BEARING
    ([CXC-SOB-SUBSCRIBE]). Two reasons, and the second is the one that would have
    bitten:

      1. Scope-agnostic reception. The old code subscribed the literal topics
         `composeio/global/_app/...`; the pattern maps to MQTT's native
         `composeio/+/_app/#`, so the source stops naming a scope as well as a
         substrate. It also picks up the whole `_app/*` FAMILY, which is how
         [CXC-SOB-EXTERNALITY]'s "classify the family, never a narrower glob"
         rule is honoured in code rather than in a comment.

      2. REPEAT DELIVERY. `Namespace.publish_state` notifies exact subscribers
         only when `new != old`, so with `bus.subscribe` the SECOND tap on the
         same row would be silently absorbed and the window would not open —
         a REGRESSION from raw paho, which delivers every message. Pattern
         subscribers are notified UNCONDITIONALLY (`_notify_pattern_subs` fires
         on every publish, local and inbound alike; verified on both paths).
         So the pattern subscription closes the dedup hazard with NO payload
         change — which matters, because Stage A's wire is frozen and the
         alternative fix was a nonce in the publisher's payload.

    THE CALLBACK CONTRACT CHANGED: `(namespace, new_value, old_value)`, not
    paho's `(client, userdata, msg)`, and the value arrives DECODED — a handler
    that still called `json.loads(msg.payload)` would raise on every message.
    The `parse_*` helpers already accept a bare value as well as an envelope, so
    they are reused unchanged.
    """
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("decisions-viewer-launcher")
    if lock is None:
        sys.stderr.write("[decisions_viewer_launcher] another instance holds the "
                         "singleton; yielding\n")
        return 0

    launcher = DecisionsViewerLauncher(geometry_store=geometry_store)
    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    # Declared despite being subscribe-only — see doc_viewer_launcher for why
    # ([DEC-COMPOSER-SEND-GLOBAL-SCOPE]).
    for _ns in (INTENT_NS, BBOX_NS):
        source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                           label="app intent")
    source_bus.manifest(SRC)

    def _on_app(ns: str, value, _old) -> None:
        if ns == BBOX_NS:
            launcher.set_console_bbox(parse_bbox(value))
            return
        if ns != INTENT_NS:
            return                     # another `_app/*` member; not ours
        if not parse_intent(value):
            return
        # [CXC-GV-SCOPE] a scoped intent (the Night Watch "View Decisions"
        # button) carries {corpus, sessions}; the rail's bare Open carries none
        # and opens unscoped.
        scope = parse_scope(value)
        sys.stderr.write(
            f"[decisions_viewer_launcher] open -> {launcher.open(scope)}\n")

    b.subscribe_pattern("_app/*", _on_app)
    sys.stderr.write(f"[decisions_viewer_launcher] listening _app/* "
                     f"(filtering {INTENT_NS} + {BBOX_NS})\n")
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

    # intent parsing: truthy opens, falsy/close does not
    check(parse_intent("open") is True, "(1) bare open string")
    check(parse_intent(True) is True and parse_intent(1) is True,
          "(2) bool/int truthy")
    check(parse_intent({"val": "open"}) is True, "(3) nested val (bus envelope)")
    check(parse_intent({"val": {"open": True}}) is True, "(4) nested val dict")
    check(parse_intent("") is False and parse_intent("off") is False
          and parse_intent("close") is False, "(5) falsy/close -> False")
    check(parse_intent(0) is False and parse_intent(None) is False,
          "(6) zero/None -> False")

    # path guard: the real decisions scene resolves; escapes refused
    check(resolve_scene(str(DECISIONS_SCENE)) is not None,
          "(7) decisions scene resolves under repo")
    check(resolve_scene("/etc/passwd") is None, "(8) outside-repo refused")
    check(resolve_scene("../../../../etc/hosts") is None, "(9) traversal refused")

    # intent -> spawn with a mocked spawn; SKIP dedupe + reap
    class FakeProc:
        def __init__(self):
            self.pid = 4242
            self._alive = True

        def poll(self):
            return None if self._alive else 0

    spawned = []

    def fake_spawn(scene_file):
        spawned.append(scene_file)
        return FakeProc()

    lau = DecisionsViewerLauncher(spawn=fake_spawn)
    r1 = lau.open()
    check(r1.startswith("opened") and len(spawned) == 1, "(10) first open spawns")
    check(spawned[0] == resolve_scene(str(DECISIONS_SCENE)),
          "(11) spawn targets the decisions scene under repo root")
    # SECOND open while alive -> deduped (still one window)
    r2 = lau.open()
    check("already open" in r2 and len(spawned) == 1, "(12) same-scene dedupe (skip)")
    # process dies -> reopen spawns fresh
    lau._windows.live[resolve_scene(str(DECISIONS_SCENE))]._alive = False
    r3 = lau.open()
    check(r3.startswith("opened") and len(spawned) == 2,
          "(13) reopen after close spawns fresh")

    # a falsy intent never reaches open() (guarded in on_message); explicit:
    check(parse_intent({"val": "0"}) is False, "(14) falsy intent -> no open")

    # bbox parsing + launch origin
    check(launch_origin({"x": 100, "y": 40, "w": 1280, "h": 800}) ==
          (100 + 1280 + 10, 40), "(15) origin right+10, top-aligned")
    check(launch_origin(None) == DEFAULT_ORIGIN, "(16) no bbox -> default origin")
    check(parse_bbox({"val": {"x": 1, "y": 2, "w": 3, "h": 4}}) ==
          {"x": 1, "y": 2, "w": 3, "h": 4}, "(17) bbox from envelope")

    # geometry-store handoff is recorded for the real spawn
    lau2 = DecisionsViewerLauncher(spawn=lambda s: None,
                                   geometry_store="/x/meta.json")
    check(lau2._geometry_store == "/x/meta.json", "(18) geometry store recorded")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: decisions_viewer_launcher selftest (18/18)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Spawn the standalone Decisions viewer per _app/decisions/open.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--geometry-store", default=None,
                    help="per-instance geometry store file (meta.<id>.meta.json) "
                         "handed to the spawned viewer ([CXC-DVL-GEOMETRY])")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, a.geometry_store, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
