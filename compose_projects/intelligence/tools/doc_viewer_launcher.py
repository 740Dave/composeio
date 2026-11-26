#!/usr/bin/env python3
"""doc_viewer_launcher — spawn a STANDALONE doc-viewer WINDOW per `_app/doc/open`
intent ([DEC-DOCVIEWER-WINDOW], David 2026-07-22).

This is the CONSUMER half of the doc-browser refactor: the in-console doc-browser
OVERLAY (cramped, one-at-a-time) is gone; the `_app/doc/open {path}` intent the
console's live-links / Open ▸ buttons already publish is now consumed HERE, and
each doc opens in its OWN window (like the Away/Night Watch windows) — several
readable at once, alongside the console, to discuss live.

Mirrors away_watch_publisher's LAUNCH leg (paho subscribe + repo-scoped path
guard + subprocess spawn; the WIDGET owns intent, this daemon owns the runtime —
the deliverable_opener split), with two doc-viewer specifics:

  PER-WINDOW CONTENT (each window its own doc): SDL2 is one-window-per-process,
  so each doc == its own `runtime` process.  We give each window its OWN doc by
  loading the canonical scene (`compose_projects/intelligence/console/docviewer.compose.yaml`) and
  baking the specific doc into its `variables:` block (doc_path=<abspath>,
  doc_name=<basename>) before writing a per-open scene file — then spawning
  `runtime <that-scene>`.  There is NO shared bus namespace, so N windows never
  collapse onto one doc.  (Variables substitute at parse time, [CXC-BIND-VARS];
  runtime.py is untouched.)

  LIGHT SAME-PATH DEDUPE: we track spawned windows path->process.  If a window
  for the EXACT same doc path is already alive, we skip re-spawning it (SDL2
  gives no cross-process window focus, so skip-while-alive is the honest
  behavior) rather than piling up duplicates.  Dead processes are reaped so a
  closed-then-reopened doc spawns fresh.

The path guard resolves the requested doc UNDER the repo root and refuses an
escape — an open intent can never point the viewer at an arbitrary file.

Run:  python3 doc_viewer_launcher.py --mqtt mqtt://mqtt-broker.local:1883
      python3 doc_viewer_launcher.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# The tools dir on sys.path so the sibling import resolves in EVERY entry mode:
# run-as-script (already there), bare `import doc_viewer_launcher` (data_sources),
# and exec_module-by-path (the render-suite loader).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawned_windows  # sibling: the shared window-lifecycle tracker (de-fork)

# [SPEC:CXC-SOURCES-ON-BUS] slice 5 — the ONE bus seam (REPO_ROOT/python is
# already on sys.path above).
from composeio.app_topics import console_bbox_ns   # noqa: E402
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

INTENT_NS = "_app/doc/open"
SRC = "doc-viewer-launcher"        # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "doc-viewer-launcher"
# [DEC-LAUNCH-PLACEMENT] (David 2026-07-22): the console publishes its screen
# bbox here; each doc window opens top-aligned, LAUNCH_MARGIN px right of the
# console's rightmost edge, and raised to the front (not centered-behind).
# [CXC-FC-S4-BBOX] DERIVED, not a literal. This was `"_app/console/bbox"` here AND in four
# sibling launchers — five spellings of one topic. The producer now derives its topic from
# the running app's id, so a consumer that kept the literal would subscribe a topic nobody
# publishes to. One definition, imported: `composeio.app_topics.console_bbox_ns()`.
BBOX_NS = console_bbox_ns()
LAUNCH_MARGIN = 10
DEFAULT_ORIGIN = (60, 60)
# the canonical doc-viewer window scene (layout + defaults); the launcher swaps
# only its `variables:` per open.
TEMPLATE_SCENE = REPO_ROOT / "compose_projects" / "intelligence" / "console" / "docviewer.compose.yaml"
# where per-open scene files are written (one small YAML per distinct doc).
_SCENE_DIR = Path(tempfile.gettempdir()) / "composeio-docviewer"

# open intents that mean "close" — the overlay accepted these; the window model
# has nothing to close, so they are simply ignored (never spawn a window).
_FALSY = {"", "off", "none", "false", "0"}


# ---------------------------------------------------------------------------
# intent parsing (mirrors away_watch_publisher.parse_intent; key is `path`)
# ---------------------------------------------------------------------------
def parse_intent(payload: Any) -> Optional[str]:
    """The doc PATH in an `_app/doc/open` intent, or None.  Accepts the bare
    envelope, a nested ``val`` (the bus wraps a bare-string publish as
    ``{val: "<path>"}``), a bare string, or ``{path: ...}``.  A falsy/close
    publish returns None (the window model has nothing to close)."""
    d = payload
    if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), (dict, str)):
        d = d["val"]
    path: Optional[str] = None
    if isinstance(d, str):
        path = d
    elif isinstance(d, dict):
        p = d.get("path") or d.get("doc") or d.get("scene_path")
        path = str(p) if p else None
    if path is None:
        return None
    text = path.strip()
    if text == "" or text.lower() in _FALSY:
        return None
    return text


def parse_bbox(payload: Any) -> Optional[Dict[str, int]]:
    """The console bbox ``{x, y, w, h}`` from an `_app/console/bbox` publish,
    or None ([DEC-LAUNCH-PLACEMENT]).  Accepts the bus envelope
    (``{val: {...}}``) or a bare dict; every field must be int-coercible."""
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
    """The (x, y) screen origin for a doc window ([DEC-LAUNCH-PLACEMENT]):
    top-aligned to the console (bbox.y), *margin* px right of its right edge
    (bbox.x + bbox.w).  Falls back to DEFAULT_ORIGIN with no bbox."""
    if not bbox:
        return DEFAULT_ORIGIN
    return (int(bbox["x"]) + int(bbox["w"]) + margin, int(bbox["y"]))


def resolve_doc(path: str) -> Optional[str]:
    """Resolve *path* under the repo root, refusing an escape.  Returns the
    absolute path iff it is a real file inside the repo, else None (the same
    guard away_watch_publisher._scene_ok applies to relaunch scenes)."""
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


def build_svg_scene(abspath: str) -> dict:
    """A self-contained scene that renders an SVG doc through the `svg` widget
    ([SPEC:CXC-SVG]) — the doc-viewer's SVG lowering (Canvas contract, no
    external app).  This is the open path for maker HOOKUP GUIDES (S3,
    `composeio.maker.hookup_svg`): a `.svg` doc opens as a pan/zoom vector
    surface rather than being (wrongly) read as markdown text.  Pure; returns a
    JSON-safe scene dict the caller writes + spawns with `runtime`."""
    name = os.path.basename(abspath)
    w, h = 900, 900
    # RESIZABLE + reflow + persisted geometry ([SPEC:CXC-RENDER]
    # [CXC-R-WINDOW-RESIZE]): the SVG doc viewer meets the same Designer-family
    # parity bar as the markdown viewer.  It shares the `doc-viewer` geometry
    # key deliberately — a maker resizes "the doc window" once and both the
    # markdown and SVG open paths restore that size.  The scene is a flex COLUMN
    # that fills the window (header + badge measure to text; the pan/zoom vector
    # surface GROWS to consume the rest), so a resize re-lays-out with no clip,
    # overlap, or dead gutter.
    return {
        "app": {"id": "doc-viewer-svg", "transports": []},
        "design": {"title": f"Doc · {name}", "target": "desktop",
                   "width": w, "height": h,
                   "resizable": True, "min_width": 560, "min_height": 420,
                   "persist_geometry": True, "window_key": "doc-viewer",
                   "theme": "dark", "padding": 0, "gap": 0},
        "widgets": [
            {"type": "stack", "id": "frame", "direction": "column",
             "align": "stretch", "grow": 1,
             "background": "#0A0E17", "border": "#1E2B3C",
             "corner_radius": 0, "clip": True,
             "padding": [15, 10, 10, 20], "gap": 4, "children": [
                 {"type": "label", "id": "header",
                  "text": name, "font_size": 14, "font_weight": "bold",
                  "color": "#EAF0F7", "font_family": "monospace"},
                 {"type": "label", "id": "badge",
                  "text": "DOC VIEWER · SVG · READ-ONLY", "font_size": 9,
                  "color": "#5C6E82", "font_family": "monospace"},
                 {"type": "svg", "id": "doc", "grow": 1,
                  "src": "file://" + abspath, "fit": "contain",
                  "pan_zoom": True},
             ]},
        ],
    }


def build_open_scene(abspath: str, template: Optional[dict] = None) -> dict:
    """The per-open scene dict.  For an ``.svg`` doc, a vector scene hosting the
    `svg` widget (lowered through the Canvas contract); otherwise the canonical
    markdown docviewer scene with its ``variables:`` overridden so THIS window
    renders THIS doc (doc_path baked to the absolute file, doc_name to its
    basename).  Pure — returns a new dict; the caller writes it."""
    if abspath.lower().endswith(".svg"):
        return build_svg_scene(abspath)
    if template is None:
        with open(TEMPLATE_SCENE, "r", encoding="utf-8") as fh:
            template = yaml.safe_load(fh) or {}
    scene = json.loads(json.dumps(template))      # deep copy (JSON-safe scene)
    vars = scene.get("variables")
    if not isinstance(vars, dict):
        vars = {}
        scene["variables"] = vars
    vars["doc_path"] = abspath
    vars["doc_name"] = os.path.basename(abspath)
    return scene


def _scene_file_for(abspath: str) -> Path:
    """A stable per-doc scene filename (so re-opening the same doc reuses the
    file rather than littering)."""
    h = hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(abspath))[:48]
    return _SCENE_DIR / f"{stem}.{h}.compose.yaml"


# ---------------------------------------------------------------------------
# the launcher: guard -> per-open scene -> spawn, with same-path dedupe
# ---------------------------------------------------------------------------
class DocViewerLauncher:
    """Consumes doc-open intents and spawns one read-only window per doc, with
    light same-path dedupe.  The spawn function is injectable so the intent->
    spawn logic is testable without a real subprocess."""

    def __init__(self, spawn: Optional[Callable[[str], Any]] = None) -> None:
        self._spawn = spawn or self._spawn_runtime
        # The shared window-lifecycle tracker, keyed on the doc PATH with SKIP
        # policy: N distinct docs => N windows, but the SAME doc twice is deduped
        # (skip-while-alive) and dead windows are reaped so a reopened doc spawns
        # fresh. De-forked from the ad-hoc `path -> proc` map + `_reap()` this
        # launcher used to carry — the SAME lifecycle logic the away-watch leg
        # forked WITHOUT, which is how that leg leaked windows
        # ([DX-NARROW-WAIST], [CXC-RETRO-AWAYWATCH-SINGLETON]).
        self._windows = spawned_windows.SpawnedWindows(spawned_windows.SKIP)
        # [DEC-LAUNCH-PLACEMENT] latest console bbox (from the bbox
        # subscription); the real spawn reads it to place the new window.
        self._console_bbox: Optional[Dict[str, int]] = None

    def set_console_bbox(self, bbox: Optional[Dict[str, int]]) -> None:
        """Record the latest console bbox so subsequently-opened doc windows
        are placed beside the console ([DEC-LAUNCH-PLACEMENT])."""
        if bbox is not None:
            self._console_bbox = bbox

    def open(self, raw_path: str) -> str:
        """Handle one open request.  Returns a human status string; never
        raises into the message loop."""
        abspath = resolve_doc(raw_path)
        if abspath is None:
            return f"refused (not a repo doc): {raw_path!r}"
        write_err: Dict[str, str] = {}

        def _do_spawn():
            # Build the per-open scene lazily — only when we actually spawn (a
            # SKIP-deduped open never touches disk).
            try:
                scene = build_open_scene(abspath)
                scene_file = _scene_file_for(abspath)
                scene_file.parent.mkdir(parents=True, exist_ok=True)
                with open(scene_file, "w", encoding="utf-8") as fh:
                    yaml.safe_dump(scene, fh, sort_keys=False, allow_unicode=True)
            except OSError as exc:
                write_err["msg"] = f"scene-write failed for {abspath!r}: {exc}"
                return None
            return self._spawn(str(scene_file))

        proc, action = self._windows.register(abspath, _do_spawn)
        if write_err:
            return write_err["msg"]
        rel = os.path.relpath(abspath, REPO_ROOT)
        if action == "skipped":
            return f"already open (skip): {rel}"
        pid = getattr(proc, "pid", "?")
        return f"opened {rel} (pid {pid})"

    def _spawn_runtime(self, scene_file: str):
        """Spawn the doc-viewer window: `runtime <scene> ` (no --transports — a
        doc viewer is a pure static read surface).  Detached, output silenced,
        cwd = repo root (so a repo-relative default resolves), the away_watch
        spawn idiom.

        Placement ([DEC-LAUNCH-PLACEMENT]): the window origin (beside the
        console, from the latest bbox or the default) + raise-to-front are
        handed to the runtime via COMPOSEIO_WINDOW_X/_Y/_RAISE env vars.
        COMPOSEIO_WINDOW_X also flags the child as a launched window so it does
        not itself publish a console bbox."""
        cmd = [sys.executable, "-m", "composeio.render.engine.runtime", scene_file]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "python") + os.pathsep + \
            env.get("PYTHONPATH", "")
        origin = launch_origin(self._console_bbox)
        env["COMPOSEIO_WINDOW_X"] = str(int(origin[0]))
        env["COMPOSEIO_WINDOW_Y"] = str(int(origin[1]))
        env["COMPOSEIO_WINDOW_RAISE"] = "1"
        return subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)


# ---------------------------------------------------------------------------
# transport (mirrors away_watch_publisher paho subscribe/consume)
# ---------------------------------------------------------------------------
def _parse_mqtt_url(url: str):
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


def run(mqtt_url: str, *, bus=None, stop=None) -> int:
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
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global; a second instance yields.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("doc-viewer-launcher")
    if lock is None:
        sys.stderr.write("[doc_viewer_launcher] another doc-viewer-launcher holds the singleton; yielding\n")
        return 0

    launcher = DocViewerLauncher()
    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    # DECLARED even though this source only SUBSCRIBES. Scope is not just an
    # uplink control for publishers: a namespace left at AUTO with a LOCAL
    # subscriber resolves to LOCAL, which is precisely
    # [DEC-COMPOSER-SEND-GLOBAL-SCOPE] — a self-subscribed namespace whose
    # messages died in-process LOOKING SENT. Once this launcher shares the
    # console's bus (Stage B), leaving `_app/*` undeclared would do exactly that
    # to every viewer intent.
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
        path = parse_intent(value)
        if not path:
            return
        sys.stderr.write(f"[doc_viewer_launcher] open -> {launcher.open(path)}\n")

    b.subscribe_pattern("_app/*", _on_app)
    sys.stderr.write(f"[doc_viewer_launcher] listening _app/* "
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

    # intent parsing
    check(parse_intent("docs/a.md") == "docs/a.md", "(1) bare string")
    check(parse_intent({"path": "docs/b.md"}) == "docs/b.md", "(2) dict path")
    check(parse_intent({"val": "docs/c.md"}) == "docs/c.md", "(3) nested val string (bus envelope)")
    check(parse_intent({"val": {"path": "docs/d.md"}}) == "docs/d.md", "(4) nested val dict")
    check(parse_intent("") is None, "(5) empty -> None")
    check(parse_intent("off") is None and parse_intent({"val": "none"}) is None,
          "(6) close-tokens -> None")
    check(parse_intent({"nope": 1}) is None, "(7) no path -> None")
    check(parse_intent(None) is None, "(8) None -> None")

    # path guard: a real repo doc resolves; escapes refused
    real = "compose_projects/intelligence/console/docviewer.compose.yaml"
    check(resolve_doc(real) is not None, "(9) real repo doc resolves")
    check(resolve_doc("/etc/passwd") is None, "(10) outside-repo refused")
    check(resolve_doc("../../../../etc/hosts") is None, "(11) traversal refused")
    check(resolve_doc("docs/nope-does-not-exist.md") is None, "(12) missing refused")

    # per-open scene bakes THIS doc into variables (per-window content)
    tmpl = {"variables": {"doc_path": "x", "doc_name": "x"}, "widgets": []}
    sc = build_open_scene("/repo/docs/foo.md", template=tmpl)
    check(sc["variables"]["doc_path"] == "/repo/docs/foo.md", "(13) doc_path baked")
    check(sc["variables"]["doc_name"] == "foo.md", "(14) doc_name = basename")
    check(tmpl["variables"]["doc_path"] == "x", "(15) template not mutated (deep copy)")

    # intent -> spawn with a mocked spawn; dedupe + reap
    class FakeProc:
        def __init__(self):
            self.pid = 1234
            self._alive = True
        def poll(self):
            return None if self._alive else 0

    spawned = []

    def fake_spawn(scene_file):
        spawned.append(scene_file)
        return FakeProc()

    lau = DocViewerLauncher(spawn=fake_spawn)
    r1 = lau.open(real)
    check(r1.startswith("opened") and len(spawned) == 1, "(16) first open spawns")
    scene_written = Path(spawned[0])
    check(scene_written.is_file(), "(17) per-open scene file written")
    with open(scene_written, "r", encoding="utf-8") as fh:
        written = yaml.safe_load(fh)
    check(written["variables"]["doc_name"] == "docviewer.compose.yaml",
          "(18) written scene carries this doc's name")
    # SAME path again while alive -> deduped (no second spawn)
    r2 = lau.open(real)
    check("already open" in r2 and len(spawned) == 1, "(19) same-path dedupe (skip)")
    # process dies -> reopen spawns fresh (reach through the shared tracker)
    lau._windows.live[resolve_doc(real)]._alive = False
    r3 = lau.open(real)
    check(r3.startswith("opened") and len(spawned) == 2, "(20) reopen after close spawns fresh")
    # a refused path never spawns
    lau.open("/etc/passwd")
    check(len(spawned) == 2, "(21) refused path never spawns")

    # bbox parsing + launch origin ([DEC-LAUNCH-PLACEMENT])
    check(parse_bbox({"val": {"x": 100, "y": 40, "w": 1280, "h": 800}}) ==
          {"x": 100, "y": 40, "w": 1280, "h": 800}, "(22) bbox from envelope")
    check(parse_bbox({"x": 5, "y": 6, "w": 7, "h": 8}) ==
          {"x": 5, "y": 6, "w": 7, "h": 8}, "(23) bare bbox dict")
    check(parse_bbox({"x": 1}) is None and parse_bbox("nope") is None,
          "(24) malformed bbox -> None")
    check(launch_origin({"x": 100, "y": 40, "w": 1280, "h": 800}) ==
          (100 + 1280 + 10, 40), "(25) origin right+10, top-aligned")
    check(launch_origin(None) == DEFAULT_ORIGIN, "(26) no bbox -> default origin")
    # the launcher records the latest bbox and places subsequent windows by it
    lau2 = DocViewerLauncher(spawn=lambda s: None)
    lau2.set_console_bbox({"x": 10, "y": 20, "w": 900, "h": 700})
    check(launch_origin(lau2._console_bbox) == (10 + 900 + 10, 20),
          "(27) launcher tracks bbox for placement")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: doc_viewer_launcher selftest ({27 - len(fails)}/27)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Spawn a standalone doc-viewer window per _app/doc/open intent.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
