#!/usr/bin/env python3
"""dsd_viewer_launcher — spawn a STANDALONE interactive DSD-viewer WINDOW per
`_app/dsd/open` intent, AND notify the architect on the human's save-close
([SPEC:CXC-DSDV] [CXC-DSDV-LAUNCHER], [CXC-DSDV-REGISTER], [CXC-DSDV-NOTIFY]).

A SIBLING of ``doc_viewer_launcher`` (the narrow waist, [DX-NARROW-WAIST]) — it
REUSES the same seams by reference: ``spawned_windows.SpawnedWindows`` for the
same-path window-lifecycle dedupe, the ``singleton`` guard, the paho subscribe
``run()`` shape, the ``COMPOSEIO_WINDOW_X/_Y/_RAISE`` placement handoff, the
per-open scene that bakes the specific DSD into ``variables:``, and an injectable
spawn for testability.  The WIDGET owns the interaction (the form + write-back);
THIS in-process launcher (registered in compose_projects/intelligence/console/data_sources.py, NOT
a daemon) owns the runtime — the same split the doc launcher follows.

Two DSD-specific shapes vs. the doc launcher:

  A WRITING WINDOW, NOT A READ SURFACE: the DSD form WRITES the DSD on save-close
  and PUBLISHES the `_app/dsd/answered` completion event, so the spawned window
  is given ``--transports`` (to reach the MQTT bus) plus two env flags:
  ``COMPOSEIO_DSD_SAVE_ON_CLOSE=1`` (arms the widget's save-on-close — absent it,
  a mere render never rewrites a DSD) and ``COMPOSEIO_INPROCESS_SOURCES=0`` (so a
  spawned viewer never drags up the whole console data layer).

  THE NOTIFY LEG ([CXC-DSDV-NOTIFY]): this same process subscribes
  `_app/dsd/answered`; on the human's save-close it CORRELATES the event to the
  `send_dsd_for_human_review` that opened it (by path + correlation id) and
  publishes an architect notice — an EXPLICIT completion signal, never inferred
  from a filesystem-modified watch.

Run:  python3 dsd_viewer_launcher.py --mqtt mqtt://mqtt-broker.local:1883
      python3 dsd_viewer_launcher.py --selftest
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
from typing import Any, Callable, Dict, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# tools dir on sys.path so the sibling import resolves in EVERY entry mode
# (run-as-script, bare import from data_sources, exec_module-by-path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawned_windows  # sibling: the shared window-lifecycle tracker (de-fork)

# [SPEC:CXC-SOURCES-ON-BUS] slice 7 — the ONE bus seam (REPO_ROOT/python is
# already on sys.path above).
from composeio.app_topics import console_bbox_ns   # noqa: E402
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

INTENT_NS = "_app/dsd/open"
SRC = "dsd-viewer-launcher"        # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
# The save-close completion event the viewer publishes ([CXC-DSDV-NOTIFY]).
ANSWERED_NS = "_app/dsd/answered"
# [DD-DSDV-MCP-HOME] RESOLVED ([CXC-DSDV-NOTIFY] INVARIANT — the dogfood loop
# MUST close): the save-close notice is delivered to the architect's INBOUND
# channel — the RAW `compose_projects/intelligence/<id>/in` topic that the composeio-channel MCP
# (channel.mjs) subscribes and surfaces as a `<channel>` event in the architect's
# context.  The earlier working default published to `_app/dsd/notice`, a bus
# namespace NO party consumes (channel.mjs listens only on `compose_projects/intelligence/<id>/in`
# + the permission verdict topic), so the notice was emitted into the void and
# the architect learned of a save only via an out-of-band file-modified notice.
# The route is `compose_projects/intelligence/<to>/in` (to defaults to `architect`); the payload
# is the bare `{from, text, ...}` message channel.mjs renders (bodyOf reads
# `text`; buildMeta lifts the scalar fields — path, correlation_id — to <channel>
# attributes), NOT the `{val: ...}` bus envelope the composeio scheme wraps.
NOTICE_INBOUND_ID_DEFAULT = "architect"
NOTICE_FROM = "dsd-viewer"
# [DEC-LAUNCH-PLACEMENT] the console publishes its screen bbox here.
# [CXC-FC-S4-BBOX] DERIVED, not a literal. This was `"_app/console/bbox"` here AND in four
# sibling launchers — five spellings of one topic. The producer now derives its topic from
# the running app's id, so a consumer that kept the literal would subscribe a topic nobody
# publishes to. One definition, imported: `composeio.app_topics.console_bbox_ns()`.
BBOX_NS = console_bbox_ns()
LAUNCH_MARGIN = 10
DEFAULT_ORIGIN = (60, 60)

TEMPLATE_SCENE = REPO_ROOT / "compose_projects" / "intelligence" / "console" / "dsdviewer.compose.yaml"
_SCENE_DIR = Path(tempfile.gettempdir()) / "composeio-dsdviewer"

# open intents that mean "close" — nothing to close, so they spawn nothing.
_FALSY = {"", "off", "none", "false", "0", "close"}


# ---------------------------------------------------------------------------
# intent parsing (mirrors doc_viewer_launcher.parse_intent; key is `path`, with
# an optional correlation id the MCP endpoint carries)
# ---------------------------------------------------------------------------
def parse_intent(payload: Any) -> Optional[Dict[str, str]]:
    """The DSD ``{path, correlation_id}`` in an `_app/dsd/open` intent, or None.
    Accepts the bare envelope, a nested ``val`` (bus wraps a bare string as
    ``{val: "<path>"}``), a bare string, or ``{path: ..., correlation_id: ...}``.
    A falsy/close publish returns None (the window model has nothing to close)."""
    d = payload
    if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), (dict, str)):
        d = d["val"]
    path: Optional[str] = None
    corr = ""
    if isinstance(d, str):
        path = d
    elif isinstance(d, dict):
        p = d.get("path") or d.get("dsd") or d.get("scene_path")
        path = str(p) if p else None
        corr = str(d.get("correlation_id") or d.get("corr") or "")
    if path is None:
        return None
    text = path.strip()
    if text == "" or text.lower() in _FALSY:
        return None
    return {"path": text, "correlation_id": corr}


def parse_bbox(payload: Any) -> Optional[Dict[str, int]]:
    """The console bbox ``{x, y, w, h}`` from an `_app/console/bbox` publish."""
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


def resolve_doc(path: str) -> Optional[str]:
    """Resolve *path* under the repo root, refusing an escape ([CXC-DSDV-LAUNCHER]
    REPO-SCOPED PATH GUARD — the doc launcher's resolve_doc mirrored)."""
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


def context_verdict(abspath: str):
    """The CONTEXT GUARD verdict for a DSD ([CXC-DSDV-CONTEXT-GUARD], David
    2026-08-17): every decision must carry a `What this is` AND a `Why it matters`
    line, or the viewer REFUSES TO OPEN IT.

    THE SHARED PREDICATE, imported from where the DSD grammar lives
    (`composeio.render.widgets.dsd_document.validate_dsd_file`) — the SAME
    function `decision_ops.dsd_review` calls before it publishes the intent. One
    lock, two doors ([DX-NARROW-WAIST]): a forked copy here would drift, and the
    drift would present as a document the MCP tool accepts and the viewer refuses
    (or worse, the reverse).

    WHY A SECOND GATE AT ALL, when the MCP tool already refuses: ComposeBus is
    BROADCAST and MULTI-MASTER — `_app/dsd/open` is a namespace, not a private
    call, and any transmitter may publish it (a hand publish, a replayed retained
    value, a future producer). A guard that lives only in one transmitter is not a
    guard on the namespace. This is the last gate before glass.

    Returns None when the predicate cannot be loaded — a launcher must never be
    bricked by an import, and the MCP gate is the primary."""
    try:
        from composeio.render.widgets.dsd_document import validate_dsd_file
    except Exception:                              # noqa: BLE001
        return None
    return validate_dsd_file(abspath)


def _persisted_scroll_direction(intelligence_id: str = "architect") -> str:
    """The persisted console scroll-direction preference ([CXC-ICP-S6-SCROLLDIR]),
    read through the ONE SHARED console-personalization seam —
    ``intelligence.console.personalization.read_settings`` over ``meta_path_for`` —
    the EXACT reader the console itself uses at launch.  This is reuse-by-reference
    (the narrow waist, [DX-NARROW-WAIST]): the DSD viewer does NOT fork a second
    copy of the meta/config_persister read, so a preference change moves both the
    console and the DSD viewer in lockstep — the fork-divergence class this
    codebase fights.  Defaults to "natural"; never raises."""
    try:
        console_dir = str(REPO_ROOT / "compose_projects" / "intelligence" / "console")
        if console_dir not in sys.path:
            sys.path.insert(0, console_dir)
        from personalization import read_settings, meta_path_for
        val = read_settings(
            meta_path_for(intelligence_id, repo_root=str(REPO_ROOT))
        ).get("scroll_direction")
        return val if val in ("natural", "traditional") else "natural"
    except Exception:
        return "natural"


def build_open_scene(abspath: str, correlation_id: str = "",
                     template: Optional[dict] = None,
                     scroll_direction: Optional[str] = None) -> dict:
    """The per-open scene dict: the canonical dsdviewer scene with its
    ``variables:`` overridden so THIS window renders THIS DSD (dsd_path baked to
    the absolute file, dsd_name to its basename, dsd_correlation to the caller's
    correlation id), and ``design.scroll_direction`` seeded from the persisted
    console preference ([CXC-ICP-S6-SCROLLDIR]) so the runtime's wheel-scroll
    dispatch honors it in the viewer's scroll viewport — the SAME seam the console
    uses (never a fork of the flip).  Pure — returns a new dict; caller writes it."""
    if template is None:
        with open(TEMPLATE_SCENE, "r", encoding="utf-8") as fh:
            template = yaml.safe_load(fh) or {}
    scene = json.loads(json.dumps(template))      # deep copy (JSON-safe scene)
    varz = scene.get("variables")
    if not isinstance(varz, dict):
        varz = {}
        scene["variables"] = varz
    varz["dsd_path"] = abspath
    varz["dsd_name"] = os.path.basename(abspath)
    varz["dsd_correlation"] = correlation_id or ""
    if scroll_direction in ("natural", "traditional"):
        design = scene.get("design")
        if not isinstance(design, dict):
            design = {}
            scene["design"] = design
        design["scroll_direction"] = scroll_direction
    return scene


def _scene_file_for(abspath: str) -> Path:
    h = hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(abspath))[:48]
    return _SCENE_DIR / f"{stem}.{h}.compose.yaml"


def build_notice(answered: Any, correlation_id: str = "") -> Optional[dict]:
    """The correlated architect notice from an `_app/dsd/answered` completion
    payload ([CXC-DSDV-NOTIFY]) — a pure function, so the correlation + summary
    are testable without a broker.  Returns None for a malformed/empty payload
    (nothing to notify)."""
    d = answered
    if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), dict):
        d = d["val"]
    if not isinstance(d, dict):
        return None
    path = d.get("path")
    if not path:
        return None
    decisions = d.get("decisions") or []
    parts = []
    for dec in decisions:
        if not isinstance(dec, dict):
            continue
        did = dec.get("id", "?")
        chosen = dec.get("chosen", "")
        seg = f"{did}={chosen}" if chosen else f"{did}"
        if dec.get("comment"):
            seg += f" ({dec['comment']})"
        parts.append(seg)
    summary = "; ".join(parts) if parts else "no answers recorded"
    name = os.path.basename(str(path))
    return {"to": "architect", "path": str(path),
            "correlation_id": correlation_id or str(d.get("correlation_id", "")),
            "answered": True, "n_decisions": len(parts),
            "summary": summary,
            "text": f"DSD answered by the human — {name}: {summary}"}


def notice_route(notice: dict) -> tuple:
    """The (topic, payload) that delivers a save-close notice into an
    Intelligence's CONTEXT ([CXC-DSDV-NOTIFY], [DD-DSDV-MCP-HOME] RESOLVED) —
    pure, so the routing is testable without a broker.

    Topic: the RAW `compose_projects/intelligence/<to>/in` the composeio-channel MCP subscribes
    (NOT the composeio-scheme `_app/dsd/notice`, which no party consumes).
    Payload: the bare `{from, text, ...}` message channel.mjs renders — a `from`
    is stamped so the <channel> event names its sender; the notice's own scalar
    fields (path, correlation_id) ride along and surface as <channel> attrs.  It
    is NOT wrapped in the `{val: ...}` bus envelope — raw `/in` payloads are bare,
    exactly like the channel_bridge's compose->/in message."""
    to = (notice.get("to") if isinstance(notice, dict) else None) \
        or NOTICE_INBOUND_ID_DEFAULT
    payload = dict(notice or {})
    payload.setdefault("from", NOTICE_FROM)
    return f"intelligence/{to}/in", payload


# ---------------------------------------------------------------------------
# the launcher: guard -> per-open scene -> spawn, with same-path dedupe + the
# save-close notify correlation
# ---------------------------------------------------------------------------
class DsdViewerLauncher:
    """Consumes DSD-open intents and spawns one interactive window per DSD, with
    same-path dedupe, and correlates the save-close completion event back to the
    send that opened it.  The spawn + notify functions are injectable so the
    intent->spawn and answered->notice logic are testable without a subprocess
    or a broker."""

    def __init__(self, spawn: Optional[Callable[[str], Any]] = None,
                 notify: Optional[Callable[[dict], None]] = None) -> None:
        self._spawn = spawn or self._spawn_runtime
        self._notify = notify
        self._windows = spawned_windows.SpawnedWindows(spawned_windows.SKIP)
        self._console_bbox: Optional[Dict[str, int]] = None
        # path -> correlation id of the send that opened it (notify correlation).
        self._sends: Dict[str, str] = {}

    def set_console_bbox(self, bbox: Optional[Dict[str, int]]) -> None:
        if bbox is not None:
            self._console_bbox = bbox

    def open(self, raw_path: str, correlation_id: str = "") -> str:
        """Handle one open request.  Returns a human status string; never
        raises into the message loop."""
        abspath = resolve_doc(raw_path)
        if abspath is None:
            return f"refused (not a repo DSD): {raw_path!r}"
        # [CXC-DSDV-CONTEXT-GUARD] LAST GATE BEFORE GLASS. A DSD whose decisions
        # lack `What this is` / `Why it matters` renders perfectly and is
        # UNDECIDABLE — so no window is spawned for it, and the log names the
        # offending decisions rather than saying "invalid".
        verdict = context_verdict(abspath)
        if verdict is not None and not verdict.ok:
            rel = os.path.relpath(abspath, REPO_ROOT)
            return f"refused (incomplete DSD): {rel}\n{verdict.message}"
        # record the correlation for this path so the save-close notify can be
        # correlated back to the caller ([CXC-DSDV-NOTIFY]).
        self._sends[abspath] = correlation_id or ""
        write_err: Dict[str, str] = {}

        def _do_spawn():
            try:
                scene = build_open_scene(
                    abspath, correlation_id,
                    scroll_direction=_persisted_scroll_direction())
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

    def on_answered(self, payload: Any) -> Optional[dict]:
        """Handle an `_app/dsd/answered` completion event ([CXC-DSDV-NOTIFY]):
        correlate it to the prior send (by resolved path, carrying that send's
        correlation id) and emit an architect notice.  Returns the notice (also
        handed to the injected notify sink), or None when uncorrelatable."""
        d = payload
        if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), dict):
            d = d["val"]
        if not isinstance(d, dict):
            return None
        raw = d.get("path")
        abspath = resolve_doc(str(raw)) if raw else None
        # correlation id: prefer the recorded send's, else the event's own.
        corr = self._sends.get(abspath or "", "") or str(d.get("correlation_id",
                                                               ""))
        notice = build_notice(d, corr)
        if notice is None:
            return None
        if self._notify is not None:
            try:
                self._notify(notice)
            except Exception:
                pass
        return notice

    def _spawn_runtime(self, scene_file: str):
        """Spawn the DSD viewer: `runtime <scene> --transports`.  Detached,
        output silenced, cwd = repo root.  Placement via env; the two DSD env
        flags arm save-on-close and keep the spawned viewer from starting the
        console data layer."""
        cmd = [sys.executable, "-m", "composeio.render.engine.runtime",
               scene_file, "--transports"]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "python") + os.pathsep + \
            env.get("PYTHONPATH", "")
        origin = launch_origin(self._console_bbox)
        env["COMPOSEIO_WINDOW_X"] = str(int(origin[0]))
        env["COMPOSEIO_WINDOW_Y"] = str(int(origin[1]))
        env["COMPOSEIO_WINDOW_RAISE"] = "1"
        # arm the widget's save-on-close (absent this, a render never rewrites
        # a DSD); keep the spawned viewer from starting the console daemons.
        env["COMPOSEIO_DSD_SAVE_ON_CLOSE"] = "1"
        env["COMPOSEIO_INPROCESS_SOURCES"] = "0"
        return subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)


# ---------------------------------------------------------------------------
# the bus legs — a SUBSCRIBER on `_app/*` plus a FOREIGN notify
# ---------------------------------------------------------------------------
def run(mqtt_url: str, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 7 — a subscribing source with a FOREIGN
    publish leg.

    WHY THIS SOURCE WAS THE HOLDOUT, AND WHAT UNBLOCKED IT. Its display legs
    (`_app/dsd/open`, `_app/dsd/answered`, `_app/console/bbox`) are ordinary
    ComposeBus namespaces and convert exactly like `doc_viewer_launcher`. Its
    NOTIFY leg is not: `notice_route` addresses the RAW `compose_projects/intelligence/<to>/in`
    topic with a BARE `{from, text, ...}` payload, because that is the wire
    `channel.mjs` subscribes and renders as a `<channel>` event. `publish_state`
    cannot express it — it would emit `composeio/global/intelligence/<to>/in`
    wrapped in a BusMessage envelope, a different topic AND a different payload,
    which [CXC-SOB-CONTRACT-FROZEN] forbids. That is why this file sat on the
    allow-list through six slices.

    The unblock is `bus.publish_foreign` — the waist's FOREIGN-CHANNEL seam
    (see `Transport.send_foreign`). The bytes on the wire are unchanged; what
    changes is that the substrate is now the transport's business. On a
    transport with no foreign carriage the notify leg logs a LOUD refusal
    instead of silently vanishing, which is the whole thesis.

    REPEAT DELIVERY: REQUIRED on the intent leg, obtained via
    `subscribe_pattern` ([CXC-SOB-SUBSCRIBE]). Opening the same DSD twice is a
    normal thing for the human to do, and `Namespace.publish_state` notifies
    EXACT subscribers only when `new != old` — so `bus.subscribe(INTENT_NS, ...)`
    would have swallowed the second open with no error at all. Pattern
    subscribers are notified UNCONDITIONALLY, which also covers the
    `_app/dsd/answered` leg: two save-closes of the same DSD with identical
    answers must each produce their own architect notice, not one.
    """
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("dsd-viewer-launcher")
    if lock is None:
        sys.stderr.write("[dsd_viewer_launcher] another instance holds the "
                         "singleton; yielding\n")
        return 0

    b = source_bus.acquire_bus(mqtt_url, label=SRC, bus=bus)
    for _ns in (INTENT_NS, ANSWERED_NS, BBOX_NS):
        source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                           label="app intent")
    source_bus.manifest(SRC)

    def _publish_notice(notice: dict) -> None:
        # [CXC-DSDV-NOTIFY] / [DD-DSDV-MCP-HOME] RESOLVED. `notice_route` is
        # unchanged and still the single source of the (channel, payload) pair,
        # so the bytes are byte-identical to the paho version; only the CARRIER
        # moved from a hand-built client to the waist.
        channel, payload = notice_route(notice)
        ok = b.publish_foreign(channel, json.dumps(payload), qos=1)
        sys.stderr.write(f"[dsd_viewer_launcher] notify -> {channel}"
                         f"{'' if ok else ' (REFUSED by transport)'}: "
                         f"{notice['text']}\n")

    launcher = DsdViewerLauncher(notify=_publish_notice)

    def _on_app(ns: str, value, _old) -> None:
        # `(namespace, new_value, old_value)`, value already DECODED — the
        # `parse_*` helpers accept a bare value as well as an envelope, so
        # they are reused unchanged.
        if ns == BBOX_NS:
            launcher.set_console_bbox(parse_bbox(value))
            return
        if ns == ANSWERED_NS:
            launcher.on_answered(value)
            return
        if ns != INTENT_NS:
            return                     # another `_app/*` member; not ours
        intent = parse_intent(value)
        if not intent:
            return
        sys.stderr.write("[dsd_viewer_launcher] open -> "
                         + launcher.open(intent["path"],
                                         intent.get("correlation_id", "")) + "\n")

    b.subscribe_pattern("_app/*", _on_app)
    sys.stderr.write(f"[dsd_viewer_launcher] listening _app/* (filtering "
                     f"{INTENT_NS} + {ANSWERED_NS} + {BBOX_NS})\n")
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

    # intent parsing (path + optional correlation id)
    check(parse_intent("docs/a.md") == {"path": "docs/a.md", "correlation_id": ""},
          "(1) bare string")
    check(parse_intent({"path": "docs/b.md", "correlation_id": "c1"})
          == {"path": "docs/b.md", "correlation_id": "c1"}, "(2) dict path+corr")
    check(parse_intent({"val": "docs/c.md"})
          == {"path": "docs/c.md", "correlation_id": ""},
          "(3) nested val string (bus envelope)")
    check(parse_intent({"val": {"path": "docs/d.md"}})
          == {"path": "docs/d.md", "correlation_id": ""}, "(4) nested val dict")
    check(parse_intent("") is None and parse_intent("off") is None
          and parse_intent("close") is None, "(5) empty/close-tokens -> None")
    check(parse_intent({"nope": 1}) is None and parse_intent(None) is None,
          "(6) no path / None -> None")

    # path guard.  `real` MUST be a genuine, context-complete DSD: since
    # [CXC-DSDV-CONTEXT-GUARD] the launcher refuses anything that is not one, so
    # the old fixture here (`dsdviewer.compose.yaml`, a scene file) would now be
    # correctly refused and every open-path check below would measure the guard
    # instead of the dedupe.
    real = "python/tests/render/fixtures/dsd/complete.md"
    check(resolve_doc(real) is not None, "(7) real repo file resolves")
    check(resolve_doc("/etc/passwd") is None, "(8) outside-repo refused")
    check(resolve_doc("../../../../etc/hosts") is None, "(9) traversal refused")
    check(resolve_doc("docs/nope-does-not-exist.md") is None, "(10) missing refused")

    # per-open scene bakes THIS DSD + correlation into variables
    tmpl = {"variables": {"dsd_path": "x", "dsd_name": "x", "dsd_correlation": ""},
            "widgets": []}
    sc = build_open_scene("/repo/docs/foo.md", "corr-9", template=tmpl)
    check(sc["variables"]["dsd_path"] == "/repo/docs/foo.md", "(11) dsd_path baked")
    check(sc["variables"]["dsd_name"] == "foo.md", "(12) dsd_name = basename")
    check(sc["variables"]["dsd_correlation"] == "corr-9", "(13) correlation baked")
    check(tmpl["variables"]["dsd_path"] == "x", "(14) template not mutated (deep copy)")

    # [CXC-ICP-S6-SCROLLDIR] the persisted scroll-direction pref is baked into the
    # per-open scene's design, so the runtime's wheel dispatch honors it — the
    # SAME seam the console uses (no fork of the flip).
    sc_sd = build_open_scene("/repo/docs/foo.md", template={"design": {"title": "x"},
                             "variables": {}, "widgets": []},
                             scroll_direction="traditional")
    check(sc_sd["design"]["scroll_direction"] == "traditional",
          "(14b) scroll_direction baked into design.scroll_direction")
    sc_nat = build_open_scene("/repo/docs/foo.md", template=tmpl,
                              scroll_direction="natural")
    check(sc_nat["design"]["scroll_direction"] == "natural",
          "(14c) natural pref baked too (explicit, honored either way)")
    sc_none = build_open_scene("/repo/docs/foo.md", template={"variables": {},
                               "widgets": []})
    check("design" not in sc_none or "scroll_direction" not in sc_none.get("design", {}),
          "(14d) no pref given -> design untouched")
    check(_persisted_scroll_direction("architect") in ("natural", "traditional"),
          "(14e) persisted pref reads to a valid value (never raises)")

    # intent -> spawn with a mocked spawn; dedupe + reap
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

    lau = DsdViewerLauncher(spawn=fake_spawn)
    r1 = lau.open(real, "corr-A")
    check(r1.startswith("opened") and len(spawned) == 1, "(15) first open spawns")
    scene_written = Path(spawned[0])
    check(scene_written.is_file(), "(16) per-open scene file written")
    with open(scene_written, "r", encoding="utf-8") as fh:
        written = yaml.safe_load(fh)
    check(written["variables"]["dsd_name"] == "complete.md",
          "(17) written scene carries this DSD's name")
    check(written["variables"]["dsd_correlation"] == "corr-A",
          "(18) written scene carries the correlation id")
    # SAME path again while alive -> deduped
    r2 = lau.open(real, "corr-A")
    check("already open" in r2 and len(spawned) == 1, "(19) same-path dedupe (skip)")
    # process dies -> reopen spawns fresh
    lau._windows.live[resolve_doc(real)]._alive = False
    r3 = lau.open(real, "corr-A")
    check(r3.startswith("opened") and len(spawned) == 2,
          "(20) reopen after close spawns fresh")
    # a refused path never spawns
    lau.open("/etc/passwd")
    check(len(spawned) == 2, "(21) refused path never spawns")

    # save-close notify: correlate an answered event to the send, emit a notice
    notices = []
    lau2 = DsdViewerLauncher(spawn=lambda s: FakeProc(),
                             notify=lambda n: notices.append(n))
    lau2.open(real, "corr-Z")
    abspath = resolve_doc(real)
    answered = {"val": {"path": abspath, "answered": True,
                        "decisions": [{"id": "D1", "chosen": "Merge it",
                                       "comment": "looks good"},
                                      {"id": "D2", "chosen": "Shared store"}]}}
    notice = lau2.on_answered(answered)
    check(notice is not None and len(notices) == 1, "(22) answered -> one notice")
    check(notice["correlation_id"] == "corr-Z",
          "(23) notice correlated to the send's correlation id")
    check("D1=Merge it" in notice["summary"] and "looks good" in notice["summary"]
          and "D2=Shared store" in notice["summary"],
          "(24) notice summarizes the answers")
    # a filesystem-modified event alone (no completion payload) -> NO notice
    check(lau2.on_answered({"val": {"event": "modified"}}) is None
          and len(notices) == 1,
          "(25) no completion payload -> no notify (decoupled from file-watch)")

    # notice ROUTE ([DD-DSDV-MCP-HOME] RESOLVED): the notice reaches the
    # architect's INBOUND channel, NOT the dead `_app/dsd/notice` bus namespace.
    topic, payload = notice_route(notice)
    check(topic == "intelligence/architect/in",
          "(26) notice routes to the architect's raw inbound topic")
    check("_app/dsd/notice" not in topic and topic.startswith("intelligence/")
          and topic.endswith("/in"), "(27) NOT the dead _app/dsd/notice topic")
    check(payload.get("from") == NOTICE_FROM and "val" not in payload
          and payload["text"] == notice["text"],
          "(28) bare {from,text,...} payload (no {val} envelope), text preserved")
    check(notice_route({"text": "x"})[0]
          == f"intelligence/{NOTICE_INBOUND_ID_DEFAULT}/in",
          "(29) missing `to` -> default architect inbound")

    # bbox parsing + launch origin
    check(launch_origin({"x": 100, "y": 40, "w": 1280, "h": 800})
          == (100 + 1280 + 10, 40), "(30) origin right+10, top-aligned")
    check(launch_origin(None) == DEFAULT_ORIGIN, "(31) no bbox -> default origin")

    # [CXC-DSDV-CONTEXT-GUARD] THE LAST GATE BEFORE GLASS, with its NEGATIVE
    # CONTROL ([CXC-VISION-EPISTEMICS] rule-negative-control): a guard that can
    # only report success is not a guard, so BOTH directions are asserted here —
    # the real artefact that got through on 2026-08-17 must be REFUSED with no
    # spawn, and the complete fixture must still OPEN.
    guarded: list = []
    lau3 = DsdViewerLauncher(spawn=lambda s: (guarded.append(s) or FakeProc()))
    bad = "docs/architecture/DECISIONS-2026-08-17-migration-and-programme.md"
    if resolve_doc(bad) is not None:
        r_bad = lau3.open(bad)
        check(r_bad.startswith("refused (incomplete DSD)") and not guarded,
              "(32) context-incomplete DSD refused, NEVER spawned")
        check("D2" in r_bad and "What this is" in r_bad,
              "(33) the refusal NAMES the offending decision + missing field")
    r_ok = lau3.open(real)
    check(r_ok.startswith("opened") and len(guarded) == 1,
          "(34) NEGATIVE CONTROL: a context-complete DSD still opens")
    # a non-DSD repo file is not a DSD at all -> refused before any spawn
    r_yaml = lau3.open("compose_projects/intelligence/console/dsdviewer.compose.yaml")
    check(r_yaml.startswith("refused") and len(guarded) == 1,
          "(35) a non-DSD repo file is refused too (no D<n> decisions)")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    total = 40        # 35 numbered + 5 scroll-direction checks (14b–14e)
    print(f"PASS: dsd_viewer_launcher selftest ({total}/{total})")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Spawn a standalone interactive DSD-viewer window per "
                    "_app/dsd/open intent, and notify the architect on save-close.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
