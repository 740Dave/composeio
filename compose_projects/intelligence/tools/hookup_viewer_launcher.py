#!/usr/bin/env python3
"""hookup_viewer_launcher — spawn a STANDALONE hookup-guide WINDOW per
`_app/hookup/open` intent.

THE CONSUMER HALF of `composeio.decision_ops.hookup_review`.  An intent nobody
subscribes is an intent published into the void — the DSD notify leg already
shipped that defect once (it published to `_app/dsd/notice`, a namespace no party
consumed), so this launcher lands WITH the endpoint, not after it.

A SIBLING of `doc_viewer_launcher` / `dsd_viewer_launcher`, reusing the same
seams by reference ([DX-NARROW-WAIST]): `spawned_windows.SpawnedWindows` for the
same-path window dedupe, the `singleton` guard, the `subscribe_pattern("_app/*")`
shape, the `COMPOSEIO_WINDOW_X/_Y/_RAISE` placement handoff, a per-open scene
written to a temp file, and an injectable spawn for testability.

TWO HOOKUP-SPECIFIC SHAPES:

  IT RENDERS THE SCENE, NOT THE PICTURE.  The window hosts the NATIVE
  `HookupWidget` pointed at the source `.compose.yaml` the intent carries, not
  the rasterized PNG.  The native widget draws from the same layout intermediate
  the SVG exporter serializes, with SCREEN-SIZED labels and fit-to-pane — so the
  human gets a resizable, readable guide instead of a fixed-resolution image, and
  the picture on screen cannot disagree with the netlist because it IS the
  netlist.  (The rendered `.svg`/`.png` still ride the intent, for a caller that
  wants to attach them elsewhere.)

  IT SHOWS THE NEGATIVE SPACE.  A netlist can only express connections that
  EXIST; a drawing cannot draw an absence.  The intent's `warnings:` list —
  "do not butt the modules together, it commons DIO", "leave the -B header
  unconnected" — is rendered as a DO-NOT panel BESIDE the drawing, in the one
  place the human is already looking.  A guide whose warnings live in a separate
  markdown file is a guide whose warnings are not read.

Run:  python3 hookup_viewer_launcher.py --mqtt mqtt://mqtt-broker.local:1883
      python3 hookup_viewer_launcher.py --selftest
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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# tools dir on sys.path so the sibling import resolves in EVERY entry mode
# (run-as-script, bare import from data_sources, exec_module-by-path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawned_windows  # noqa: E402  sibling: shared window-lifecycle tracker

from composeio import source_bus                              # noqa: E402
from composeio.app_topics import console_bbox_ns              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

INTENT_NS = "_app/hookup/open"
SRC = "hookup-viewer-launcher"     # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
# [DEC-LAUNCH-PLACEMENT] the console publishes its screen bbox here.
# [CXC-FC-S4-BBOX] DERIVED, never a literal: the console's bbox topic is built from its
# scene's `app: id:`, so a launcher that hardcodes the old literal subscribes a topic
# NOBODY publishes to — a silent placement regression that is worse than a loud one.
# One definition, imported, exactly as `dsd_viewer_launcher` resolves it.
BBOX_NS = console_bbox_ns()
LAUNCH_MARGIN = 10
DEFAULT_ORIGIN = (60, 60)

_SCENE_DIR = Path(tempfile.gettempdir()) / "composeio-hookupviewer"

# open intents that mean "close" — nothing to close, so they spawn nothing.
_FALSY = {"", "off", "none", "false", "0", "close"}


# ---------------------------------------------------------------------------
# intent parsing (the doc/dsd family shape: `path` alias + correlation id, plus
# the hookup-specific warnings/artifacts)
# ---------------------------------------------------------------------------
def parse_intent(payload: Any) -> Optional[Dict[str, Any]]:
    """The `{scene, svg, png, correlation_id, warnings, ...}` in an
    `_app/hookup/open` intent, or None.  Accepts the bare envelope, a nested
    ``val`` (the bus wraps a bare string as ``{val: "<path>"}``), a bare string,
    or the full dict.  A falsy/close publish returns None."""
    d = payload
    if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), (dict, str)):
        d = d["val"]
    scene: Optional[str] = None
    extra: Dict[str, Any] = {}
    if isinstance(d, str):
        scene = d
    elif isinstance(d, dict):
        p = d.get("scene") or d.get("path") or d.get("hookup")
        scene = str(p) if p else None
        extra = {
            "correlation_id": str(d.get("correlation_id") or d.get("corr") or ""),
            "svg": str(d.get("svg") or ""),
            "png": str(d.get("png") or ""),
            "board": str(d.get("board") or ""),
            "warnings": _warnings_of(d.get("warnings")),
        }
    if scene is None:
        return None
    text = scene.strip()
    if text == "" or text.lower() in _FALSY:
        return None
    out: Dict[str, Any] = {"scene": text, "correlation_id": "", "svg": "",
                           "png": "", "board": "", "warnings": []}
    out.update(extra)
    return out


def _warnings_of(raw: Any) -> List[Dict[str, Any]]:
    """Normalize the intent's negative-space list to
    ``[{text, kind, about, source}]``, accepting bare strings too (a hand-published
    intent should not have to know the record shape)."""
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for w in raw:
        if isinstance(w, str) and w.strip():
            out.append({"text": w.strip(), "kind": "note", "about": [],
                        "source": "intent"})
        elif isinstance(w, dict) and str(w.get("text") or "").strip():
            about = w.get("about") or []
            if isinstance(about, str):
                about = [about]
            out.append({"text": str(w["text"]).strip(),
                        "kind": str(w.get("kind") or "note"),
                        "about": [str(a) for a in about],
                        "source": str(w.get("source") or "intent")})
    return out


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
    """Resolve *path* under the repo root, refusing an escape — the doc/dsd
    launcher guard mirrored.  An open intent can never point the viewer at an
    arbitrary file."""
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


# ---------------------------------------------------------------------------
# the per-open scene: the NATIVE hookup widget + the DO-NOT panel
# ---------------------------------------------------------------------------
_DO_NOT_INK = "#F2B6B6"
_DO_NOT_BG = "#2A1418"
_DO_NOT_BORDER = "#7A2E36"


def _warning_line(w: Dict[str, Any]) -> str:
    about = ", ".join(w.get("about") or [])
    kind = w.get("kind") or "note"
    tag = "" if kind == "note" else f"[{kind}] "
    tail = f"  ({about})" if about else ""
    return f"⛔ {tag}{w['text']}{tail}"


def build_open_scene(scene_abspath: str, *, correlation_id: str = "",
                     warnings: Optional[List[Dict[str, Any]]] = None,
                     board: str = "") -> dict:
    """The per-open scene dict — a self-contained ComposeUX scene hosting the
    NATIVE `hookup` widget on *scene_abspath* plus the DO-NOT panel.

    Pure: returns a new JSON-safe dict; the caller writes it and spawns
    `runtime` against it.  Resizable + geometry-persisting under its own
    `hookup-viewer` window key, so a maker sizes the guide once."""
    warns = warnings or []
    name = os.path.basename(scene_abspath)
    subtitle = f"HOOKUP GUIDE · {board.upper()} · READ-ONLY" if board \
        else "HOOKUP GUIDE · READ-ONLY"
    if correlation_id:
        subtitle += f" · {correlation_id}"

    children: List[dict] = [
        {"type": "label", "id": "header", "text": name, "font_size": 14,
         "font_weight": "bold", "color": "#EAF0F7", "font_family": "monospace"},
        {"type": "label", "id": "badge", "text": subtitle, "font_size": 9,
         "color": "#5C6E82", "font_family": "monospace"},
        {"type": "hookup", "id": "guide", "grow": 1,
         "src": "file://" + scene_abspath},
    ]

    # THE DO-NOT PANEL. A netlist can only express connections that EXIST; these
    # are the ones that must NOT. It sits BESIDE the drawing because that is
    # where the human is already looking — a warning in another file is a warning
    # that is not read. Absent warnings, the panel is absent too (no empty chrome,
    # but the endpoint's status has already said the scene declared none).
    if warns:
        panel: List[dict] = [
            {"type": "label", "id": "dn-title",
             "text": f"DO NOT  ·  {len(warns)} constraint(s) the drawing cannot draw",
             "font_size": 11, "font_weight": "bold", "color": _DO_NOT_INK,
             "font_family": "monospace"},
        ]
        for i, w in enumerate(warns):
            panel.append({"type": "label", "id": f"dn-{i}",
                          "text": _warning_line(w), "font_size": 11,
                          "color": _DO_NOT_INK, "font_family": "monospace",
                          "wrap": True})
        children.append(
            {"type": "stack", "id": "do-not", "direction": "column",
             "align": "stretch", "background": _DO_NOT_BG,
             "border": _DO_NOT_BORDER, "corner_radius": 4,
             "padding": [10, 12, 10, 12], "gap": 5, "children": panel})

    return {
        "app": {"id": "hookup-viewer", "transports": []},
        "design": {"title": f"Hookup · {name}", "target": "desktop",
                   "width": 1100, "height": 900,
                   "resizable": True, "min_width": 640, "min_height": 480,
                   "persist_geometry": True, "window_key": "hookup-viewer",
                   "theme": "dark", "padding": 0, "gap": 0},
        "widgets": [
            {"type": "stack", "id": "frame", "direction": "column",
             "align": "stretch", "grow": 1,
             "background": "#0A0E17", "border": "#1E2B3C",
             "corner_radius": 0, "clip": True,
             "padding": [15, 10, 10, 20], "gap": 6, "children": children},
        ],
    }


def _scene_file_for(abspath: str) -> Path:
    h = hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(abspath))[:48]
    return _SCENE_DIR / f"{stem}.{h}.viewer.compose.yaml"


# ---------------------------------------------------------------------------
# the launcher: guard -> per-open scene -> spawn, with same-path dedupe
# ---------------------------------------------------------------------------
class HookupViewerLauncher:
    """Consumes hookup-open intents and spawns one guide window per SCENE, with
    same-path dedupe.  The spawn function is injectable so intent->spawn is
    testable without a subprocess."""

    def __init__(self, spawn: Optional[Callable[[str], Any]] = None) -> None:
        self._spawn = spawn or self._spawn_runtime
        self._windows = spawned_windows.SpawnedWindows(spawned_windows.SKIP)
        self._console_bbox: Optional[Dict[str, int]] = None

    def set_console_bbox(self, bbox: Optional[Dict[str, int]]) -> None:
        if bbox is not None:
            self._console_bbox = bbox

    def open(self, intent: Dict[str, Any]) -> str:
        """Handle one open request.  Returns a human status string; never raises
        into the message loop."""
        abspath = resolve_scene(str(intent.get("scene") or ""))
        if abspath is None:
            return f"refused (not a repo scene): {intent.get('scene')!r}"
        write_err: Dict[str, str] = {}

        def _do_spawn():
            try:
                scene = build_open_scene(
                    abspath,
                    correlation_id=str(intent.get("correlation_id") or ""),
                    warnings=intent.get("warnings") or [],
                    board=str(intent.get("board") or ""))
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
        return f"opened {rel} (pid {getattr(proc, 'pid', '?')})"

    def _spawn_runtime(self, scene_file: str):
        """Spawn the guide window: `runtime <scene>`.  Detached, output silenced,
        cwd = repo root.  Placement via env; no transports (a hookup guide is a
        READ surface — it writes nothing back, so it needs no bus)."""
        cmd = [sys.executable, "-m", "composeio.render.engine.runtime", scene_file]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "python") + os.pathsep + \
            env.get("PYTHONPATH", "")
        origin = launch_origin(self._console_bbox)
        env["COMPOSEIO_WINDOW_X"] = str(int(origin[0]))
        env["COMPOSEIO_WINDOW_Y"] = str(int(origin[1]))
        env["COMPOSEIO_WINDOW_RAISE"] = "1"
        env["COMPOSEIO_INPROCESS_SOURCES"] = "0"
        return subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)


# ---------------------------------------------------------------------------
# the bus leg — a subscriber on `_app/*`
# ---------------------------------------------------------------------------
def run(mqtt_url: str, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] — a subscribing source, no publish leg.

    REPEAT DELIVERY IS REQUIRED, so the subscription is `subscribe_pattern`
    ([CXC-SOB-SUBSCRIBE]): re-sending the SAME hookup after a correction is the
    normal case, and `Namespace.publish_state` notifies EXACT subscribers only
    when `new != old` — a `bus.subscribe(INTENT_NS, ...)` would swallow the
    second send with no error at all."""
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("hookup-viewer-launcher")
    if lock is None:
        sys.stderr.write("[hookup_viewer_launcher] another instance holds the "
                         "singleton; yielding\n")
        return 0

    b = source_bus.acquire_bus(mqtt_url, label=SRC, bus=bus)
    for _ns in (INTENT_NS, BBOX_NS):
        source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                           label="app intent")
    source_bus.manifest(SRC)

    launcher = HookupViewerLauncher()

    def _on_app(ns: str, value, _old) -> None:
        if ns == BBOX_NS:
            launcher.set_console_bbox(parse_bbox(value))
            return
        if ns != INTENT_NS:
            return                     # another `_app/*` member; not ours
        intent = parse_intent(value)
        if not intent:
            return
        sys.stderr.write("[hookup_viewer_launcher] open -> "
                         + launcher.open(intent) + "\n")

    b.subscribe_pattern("_app/*", _on_app)
    sys.stderr.write(f"[hookup_viewer_launcher] listening _app/* (filtering "
                     f"{INTENT_NS} + {BBOX_NS})\n")
    source_bus.park(stop)
    return 0


# ---------------------------------------------------------------------------
# selftest — logic only, no broker, no window, mocked spawn
# ---------------------------------------------------------------------------
def _selftest() -> int:
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # intent parsing
    i = parse_intent("python/x.compose.yaml")
    check(i and i["scene"] == "python/x.compose.yaml", "(1) bare string")
    i = parse_intent({"scene": "a.yaml", "correlation_id": "c1", "svg": "/t/a.svg",
                      "png": "/t/a.png", "board": "perfboard"})
    check(i and i["correlation_id"] == "c1" and i["png"] == "/t/a.png"
          and i["board"] == "perfboard", "(2) full dict")
    check((parse_intent({"val": {"path": "b.yaml"}}) or {}).get("scene") == "b.yaml",
          "(3) nested val + `path` alias (doc/dsd family shape)")
    check(parse_intent("") is None and parse_intent("close") is None
          and parse_intent({"nope": 1}) is None and parse_intent(None) is None,
          "(4) empty/close/no-scene -> None")

    # warnings normalize (bare strings AND records)
    w = _warnings_of(["do not common DIO",
                      {"text": "never use GPIO2/8/9", "kind": "reserved-pin",
                       "about": ["mcu.GPIO2"]}])
    check(len(w) == 2 and w[0]["kind"] == "note"
          and w[1]["about"] == ["mcu.GPIO2"], "(5) warnings normalize both shapes")
    check(_warnings_of(None) == [] and _warnings_of([{"text": "  "}]) == [],
          "(6) empty/blank warnings dropped")

    # path guard
    real = "python/composeio/render/examples/dual_throttle.compose.yaml"
    check(resolve_scene(real) is not None, "(7) real repo scene resolves")
    check(resolve_scene("/etc/passwd") is None, "(8) outside-repo refused")
    check(resolve_scene("../../../../etc/hosts") is None, "(9) traversal refused")
    check(resolve_scene("python/nope.compose.yaml") is None, "(10) missing refused")

    # the per-open scene: NATIVE hookup widget on the SCENE, not the picture
    sc = build_open_scene("/repo/x.compose.yaml", correlation_id="c9",
                          board="perfboard")
    flat = json.dumps(sc)
    check('"type": "hookup"' in flat, "(11) hosts the NATIVE hookup widget")
    check('"src": "file:///repo/x.compose.yaml"' in flat,
          "(12) widget points at the SOURCE scene, not a rendered image")
    check(".png" not in flat, "(13) no rasterized image in the window")
    check("c9" in flat, "(14) correlation id shown in the badge")
    check(sc["design"]["window_key"] == "hookup-viewer" and sc["design"]["resizable"],
          "(15) resizable + its own geometry key")

    # the DO-NOT panel — the negative space, beside the drawing
    sc_w = build_open_scene("/repo/x.compose.yaml", warnings=[
        {"text": "do not butt the modules together", "kind": "no-common",
         "about": ["disp0", "disp1"], "source": "scene"}])
    flat_w = json.dumps(sc_w)
    check('"id": "do-not"' in flat_w, "(16) DO-NOT panel present when warned")
    check("do not butt the modules together" in flat_w
          and "no-common" in flat_w and "disp0" in flat_w,
          "(17) the warning, its kind and its `about:` all reach the window")
    check('"id": "do-not"' not in json.dumps(build_open_scene("/repo/x.compose.yaml")),
          "(18) no warnings -> no empty chrome")

    # intent -> spawn, dedupe, reap
    class FakeProc:
        def __init__(self):
            self.pid = 5151
            self._alive = True

        def poll(self):
            return None if self._alive else 0

    spawned: List[str] = []
    lau = HookupViewerLauncher(spawn=lambda s: (spawned.append(s), FakeProc())[1])
    r1 = lau.open({"scene": real, "correlation_id": "cA", "warnings": []})
    check(r1.startswith("opened") and len(spawned) == 1, "(19) first open spawns")
    written = yaml.safe_load(Path(spawned[0]).read_text(encoding="utf-8"))
    check(json.dumps(written).count('"type": "hookup"') == 1,
          "(20) written scene carries the hookup widget")
    r2 = lau.open({"scene": real})
    check("already open" in r2 and len(spawned) == 1, "(21) same-scene dedupe")
    lau._windows.live[resolve_scene(real)]._alive = False
    r3 = lau.open({"scene": real})
    check(r3.startswith("opened") and len(spawned) == 2,
          "(22) reopen after close spawns fresh")
    lau.open({"scene": "/etc/passwd"})
    check(len(spawned) == 2, "(23) refused path never spawns")

    # bbox / placement
    check(launch_origin({"x": 100, "y": 40, "w": 1280, "h": 800})
          == (100 + 1280 + 10, 40), "(24) origin right+10, top-aligned")
    check(launch_origin(None) == DEFAULT_ORIGIN, "(25) no bbox -> default origin")
    check(parse_bbox({"val": {"x": 1, "y": 2, "w": 3, "h": 4}})
          == {"x": 1, "y": 2, "w": 3, "h": 4}, "(26) bbox parses through the envelope")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("PASS: hookup_viewer_launcher selftest (26/26)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Spawn a standalone hookup-guide window per "
                    "_app/hookup/open intent.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
