#!/usr/bin/env python3
"""away_watch_publisher — the bus-native projector for the Away-Watch history
(David 2026-07-22: "maintain a list of Away Watch entries in the sqlite db ...
scroll through prior night watches, clicking on one pulls up the window").

TWO jobs, both bus-native (mirrors project_validator's publish transport +
console_supervisor's paho subscribe/spawn):

  1. PROJECT — read ``list_away_watches()`` from the retrospective SQLite store
     and publish it to ``self/away_watches`` (scope global -> topic
     ``composeio/global/self/away_watches``) every ``--interval`` seconds, the
     same ``self/*`` mapping self_publisher / project_validator use.  Payload:
       {"ts": <iso>, "watches": [ {id,label,title,started_ts,ended_ts,
         started_fmt,ended_fmt,commits,subagents,tests,decisions,tokens,
         elapsed,tasks:[{label,status}],git_state,artifacts:[{title,path}],
         scene_path}, ... ] }
     ``title``/``started_fmt``/``ended_fmt`` are the ``Tue, Jul 21 @ 23:50``
     strings (weekday computed from the date here, once); ``elapsed`` is derived
     from start/end in the store.  The ``away_watch_view`` widget renders this
     verbatim.

  2. LAUNCH — consume the relaunch INTENT the widget publishes on
     ``_app/away_watch/open`` ({scene_path, session_id, label}) and spawn the
     Night Watch window: ``python -m composeio.render.engine.runtime
     <scene_path> --transports``.  The WIDGET never spawns a process — the leaf
     owns presentation + intent, this daemon owns the runtime (Part II §1/§8,
     the deliverable_opener split).  Only a scene UNDER the repo root is
     launched (path-escape guard) — a relaunch can never be pointed elsewhere.

This is the launcher side the widget's ``_app/away_watch/open`` intent needs.
Without this daemon running the widget still lists + browses + opens artifacts
(those ride ``self/away_watches`` / the doc-browser); ONLY the click-to-relaunch
is inert until it is up.

Seeds the ONE real 2026-07-22 overnight entry on start (idempotent label
upsert) unless ``--no-seed``.

[SPEC:CXC-SOURCES-ON-BUS] slice 5b — a PUBLISH+SUBSCRIBE source.  Three notes the
next reader needs:

  REPEAT DELIVERY IS REQUIRED, SO THE SUBSCRIPTION IS A PATTERN
  ([CXC-SOB-SUBSCRIBE]).  Tapping the SAME roster row twice must relaunch the
  Night Watch window both times — the SUPERSEDE tracker exists precisely so a
  re-click reaps and re-spawns.  ``bus.subscribe(ns, cb)`` notifies exact
  subscribers only when ``new != old``, so the second identical intent would be
  SILENTLY ABSORBED and the window would not reopen.  ``subscribe_pattern`` fires
  on EVERY matching publish, so it closes the hazard with no payload change (and
  no nonce), which is what Stage A's frozen wire requires.  It also picks up the
  whole ``_app/*`` FAMILY, honouring [CXC-SOB-EXTERNALITY]'s "classify the family,
  never a narrower glob" rule in code rather than in a comment.

  ``fun/nightwatch/*`` IS NOT PUBLISHED HERE — measured, 2026-08-10.  The spec's
  [CXC-SOB-FUN-IS-EXTERNAL] and [CXC-SOB-INVENTORY] row 9 say this module
  "publishes ``fun/nightwatch/{title,started,...}``".  It does not, and never did:
  those values are handed to the SPAWNED window out-of-band, as a JSON blob in the
  ``COMPOSEIO_SCENE_INITIAL`` env var (see ``nightwatch_initials`` ->
  ``launch_scene``), and the window seeds its OWN bus from them.  No
  ``fun/nightwatch/*`` message crosses the console's bus from this process — the
  before/after capture confirms it.  So this source declares only what it actually
  touches.  The spec's CONCLUSION (``fun/*`` is external, never local) still
  holds — the window is a separate OS process — but its stated evidence is wrong,
  and declaring a namespace we never publish would put a line in the SOB-C3
  manifest that means nothing.

  THE CALLBACK CONTRACT CHANGED: ``(namespace, new_value, old_value)``, not
  paho's ``(client, userdata, msg)``, and the value arrives DECODED — the old
  handler's ``json.loads(msg.payload.decode())`` would raise on every message.
  ``parse_open_intent``/``parse_bbox`` already accept a bare value as well as an
  envelope, so they are reused unchanged.

Run:  python3 away_watch_publisher.py --mqtt mqtt://mqtt-broker.local:1883 [--interval 30]
      python3 away_watch_publisher.py --once      # compute + print; publish if reachable
      python3 away_watch_publisher.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# `intelligence` is a top-level package name; since the tree moved under
# compose_projects/ (2026-08-17) that directory is what makes `import
# intelligence.retrospective` resolve.
sys.path.insert(0, str(REPO_ROOT / "compose_projects"))
# The tools dir on sys.path so the sibling import resolves in EVERY entry mode:
# run-as-script (already there), bare `import away_watch_publisher` (data_sources),
# and exec_module-by-path (the render-suite loader).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import spawned_windows  # sibling: the shared window-lifecycle tracker (de-fork)

# [SPEC:CXC-SOURCES-ON-BUS] slice 5b — the ONE bus seam (REPO_ROOT/python is
# already on sys.path above). This module used to hand-frame onto a paho client
# through `composeio.bus_envelope`; it now states a NAMESPACE and a SCOPE and lets
# `transport_registry` choose the substrate ([CXC-SOB-THESIS]).
from composeio.app_topics import console_bbox_ns   # noqa: E402
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

NS = "self/away_watches"
SRC = "away-watch-publisher"       # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "away-watch-publisher"     # = the old paho client-id ([CXC-SOB-HANDLE])
INTENT_NS = "_app/away_watch/open"
# [CXC-RETRO-AWAYWATCH-SINGLETON] The Night Watch window is a SINGLE-instance
# role: every roster click resolves to ONE window (re-rendered for the clicked
# entry), and a relaunch SUPERSEDES — reaps — the prior process. This is the
# constant key the launch leg tracks in its SpawnedWindows(SUPERSEDE) registry,
# so N clicks never leak N live windows (the overnight-2026-07-23 defect).
WINDOW_ROLE = "nightwatch-window"
# [DEC-LAUNCH-PLACEMENT] (David 2026-07-22): the console publishes its screen
# bbox here; a launched window opens top-aligned, LAUNCH_MARGIN px right of the
# console's rightmost edge, and raised to the front (not centered-behind).
# [CXC-FC-S4-BBOX] DERIVED, not a literal. This was `"_app/console/bbox"` here AND in four
# sibling launchers — five spellings of one topic. The producer now derives its topic from
# the running app's id, so a consumer that kept the literal would subscribe a topic nobody
# publishes to. One definition, imported: `composeio.app_topics.console_bbox_ns()`.
BBOX_NS = console_bbox_ns()
LAUNCH_MARGIN = 10
# Fallback origin when no console bbox has been seen yet (still explicit +
# raised, so the window never lands centered behind the console).
DEFAULT_ORIGIN = (60, 60)
# The env var the launched runtime reads to seed its per-window initial state
# ([SPEC:CXC-RETRO §5c]); MUST match runtime.SCENE_INITIAL_ENV. This is the seam
# that threads the CLICKED watch's data into the relaunched Night Watch window,
# so it shows THAT entry — not the scene's baked-in default (the seeded overnight
# entry). Kept as a bare string (not an import of the render engine) so this
# launcher daemon never has to import skia/the widget registry. Independent of
# the [DEC-LAUNCH-PLACEMENT] origin env vars — a launch threads BOTH.
SCENE_INITIAL_ENV = "COMPOSEIO_SCENE_INITIAL"


def parse_bbox(payload: Any) -> Optional[Dict[str, int]]:
    """The console bbox ``{x, y, w, h}`` from an `_app/console/bbox` publish,
    or None.  Accepts the bus envelope (``{val: {...}}``) or a bare dict; every
    field must be an int-coercible number."""
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
    """The (x, y) screen origin for a launched window ([DEC-LAUNCH-PLACEMENT]):
    top-aligned to the console (bbox.y), *margin* px right of the console's
    right edge (bbox.x + bbox.w).  Falls back to DEFAULT_ORIGIN when no bbox
    has been observed."""
    if not bbox:
        return DEFAULT_ORIGIN
    return (int(bbox["x"]) + int(bbox["w"]) + margin, int(bbox["y"]))


def _db():
    import intelligence.retrospective.db as db
    return db


def _fmt_tokens(n: Any) -> str:
    """A token count as the window's compact string ("23.7M" / "24k" / "312").
    Empty inputs read as "0". Mirrors the Night Watch window's default format."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "0"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}k"
    return str(int(v))


def _fmt_elapsed(secs: Any) -> str:
    """Elapsed seconds as the window's "7h 52m" (or "52m" under an hour).
    Empty when the duration is absent/invalid (reads blank, never a fake 0m)."""
    if not isinstance(secs, (int, float)):
        return ""
    secs = int(secs)
    if secs < 0:
        return ""
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


def format_watch_datetime(ts: Any) -> str:
    """A watch's start as ``Tue, Jul 21 @ 23:50`` (David 2026-07-22): abbreviated
    weekday (%a) COMPUTED from the date — never hardcoded — %b %d, @ HH:MM 24h.
    Empty for a missing/invalid timestamp (reads blank, not a fake 1970)."""
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    return time.strftime("%a, %b %d @ %H:%M", time.localtime(t))


def format_watch_title(ts: Any) -> str:
    """The Night Watch window title: ``Away Watch: <start datetime>`` (David
    2026-07-22). Falls back to a bare ``Away Watch`` when the start is unknown."""
    dt = format_watch_datetime(ts)
    return f"Away Watch: {dt}" if dt else "Away Watch"


def build_payload() -> Dict[str, Any]:
    """The self/away_watches projection: list_away_watches(), most-recent
    first, ready for the widget (a plain JSON-safe dict).

    Enriches each watch with the derived presentation fields the redesigned
    surfaces read but should not each re-derive (perceive/present split):
      * ``title`` / ``started_fmt`` / ``ended_fmt`` — the ``Tue, Jul 21 @ 23:50``
        strings (weekday computed from the date, done ONCE here),
      * the raw decisions/tokens/elapsed/tasks/git_state pass straight through
        from ``list_away_watches`` (already includes the derived ``elapsed``)."""
    watches = _db().list_away_watches()
    for w in watches:
        w["title"] = format_watch_title(w.get("started_ts"))
        w["started_fmt"] = format_watch_datetime(w.get("started_ts"))
        w["ended_fmt"] = format_watch_datetime(w.get("ended_ts"))
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "watches": watches}


# [CXC-RETRO-AWAYWATCH-OPEN-TYPED] The scene-vs-doc predicate — MUST match the
# widget router's SCENE_SUFFIX (away_watch_view._is_scene): only a `.compose.yaml`
# is a renderable Night Watch scene. A doc-typed target (a report `.md`) must NEVER
# reach the runtime here — the widget routes it to `_app/doc/open` instead — but the
# guard rejects it defensively so a mis-typed scene_path is a LOUD refusal, not a
# `runtime <report.md> --transports` that dies with stderr → /dev/null (the silent
# not-clickable defect, 2026-07-26 maker entry).
SCENE_SUFFIX = ".compose.yaml"


def _scene_ok(scene_path: str) -> Optional[str]:
    """Resolve *scene_path* under the repo root, refusing an escape OR a non-scene.
    Returns the absolute path iff it is a real ``.compose.yaml`` file inside the
    repo, else None ([CXC-RETRO-AWAYWATCH-OPEN-TYPED]: a doc target is NOT a scene
    and is refused here rather than spawned into a runtime that cannot parse it)."""
    if not scene_path:
        return None
    if not str(scene_path).endswith(SCENE_SUFFIX):
        return None                            # a doc target is not a Night Watch scene
    p = Path(scene_path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    try:
        p = p.resolve()
        p.relative_to(REPO_ROOT.resolve())     # inside the repo, or ValueError
    except (ValueError, OSError):
        return None
    return str(p) if p.is_file() else None


def launch_scene(scene_path: str, origin: Optional[tuple] = None,
                 initials: Optional[Dict[str, Any]] = None):
    """Spawn the Night Watch runtime for *scene_path* (guarded).  Returns the
    spawned Popen handle, or None if the scene is refused by the repo-scope
    guard; never raises into the message loop.  The daemon tracks the handle in
    a SpawnedWindows registry so a relaunch SUPERSEDES (reaps) the prior window
    rather than leaking it ([CXC-RETRO-AWAYWATCH-SINGLETON]) — see ``relaunch``.

    Two INDEPENDENT parametrizations, either/both/neither:

    *origin* ((x, y) screen pixels, [DEC-LAUNCH-PLACEMENT]) places the new
    window beside the console and raises it to the front, handed to the spawned
    runtime via COMPOSEIO_WINDOW_X/_Y/_RAISE env vars.  None (a direct/legacy
    call) leaves the window OS-centered as before.

    *initials* (a ``{namespace: value}`` map for the CLICKED watch,
    [SPEC:CXC-RETRO §5c]) is handed to the spawned runtime via
    ``COMPOSEIO_SCENE_INITIAL`` so the window shows THAT entry rather than the
    scene's baked-in default. None (a direct/legacy launch) seeds nothing."""
    abspath = _scene_ok(scene_path)
    if abspath is None:
        return None
    cmd = [sys.executable, "-m", "composeio.render.engine.runtime",
           abspath, "--transports"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "python") + os.pathsep + \
        env.get("PYTHONPATH", "")
    if origin is not None:
        env["COMPOSEIO_WINDOW_X"] = str(int(origin[0]))
        env["COMPOSEIO_WINDOW_Y"] = str(int(origin[1]))
        env["COMPOSEIO_WINDOW_RAISE"] = "1"
    if initials:
        env[SCENE_INITIAL_ENV] = json.dumps(initials)
    return subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def relaunch(windows, scene_path: str, origin: Optional[tuple] = None,
             initials: Optional[Dict[str, Any]] = None):
    """Spawn — or SUPERSEDE — the SINGLE Night Watch window through the shared
    lifecycle tracker ([SPEC:CXC-RETRO §5c CXC-RETRO-AWAYWATCH-SINGLETON]).

    Every roster click keys on the constant WINDOW_ROLE, so a new click reaps
    the prior window and at most ONE nightwatch process is ever live — the fix
    for the overnight window leak (which, since [cdf1217] gave the window an
    outbound MQTT leg, was also a duplicate-bus-participant hazard). The repo-
    scope guard stays in launch_scene, so a refused scene returns None and the
    registry keeps any live window untouched (never zero windows).

    Returns ``(proc, action)`` — action in {"spawned","superseded","refused"}."""
    return windows.register(
        WINDOW_ROLE,
        lambda: launch_scene(scene_path, origin=origin, initials=initials))


def parse_open_intent(payload: Any) -> Optional[Dict[str, Any]]:
    """The FULL _app/away_watch/open intent — {scene_path, session_id, label} —
    or None. Accepts the bus envelope (nested `val`), a bare {scene_path,...}
    dict, or a bare string (scene_path only, no identity).

    scene_path is required (it is what gets launched); session_id/label are the
    CLICKED-entry identity the launcher uses to look the watch up and thread its
    data into the window ([SPEC:CXC-RETRO §5c]) — either may be absent."""
    d = payload
    if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), (dict, str)):
        d = d["val"]
    if isinstance(d, str):
        sp = d.strip()
        return {"scene_path": sp, "session_id": None, "label": None} if sp else None
    if isinstance(d, dict):
        sp = d.get("scene_path") or d.get("path")
        if not sp:
            return None
        return {"scene_path": str(sp).strip(),
                "session_id": (str(d["session_id"]).strip()
                               if d.get("session_id") else None),
                "label": (str(d["label"]).strip() if d.get("label") else None)}
    return None


def parse_intent(payload: Any) -> Optional[str]:
    """The scene_path in an _app/away_watch/open intent, or None (back-compat
    shim over parse_open_intent — callers that only need the path)."""
    intent = parse_open_intent(payload)
    return intent["scene_path"] if intent else None


def find_watch(session_id: Optional[str], label: Optional[str],
               watches: Optional[List[Dict[str, Any]]] = None
               ) -> Optional[Dict[str, Any]]:
    """The projected watch row a CLICKED entry's identity names, or None.

    Matches on `label` FIRST (the away_watch table's UNIQUE key — the reliable
    identity), then falls back to `session_id`. `watches` defaults to a live
    build_payload() projection (already carrying the derived title/*_fmt), so the
    launcher looks the entry up the same way the widget rendered it."""
    if watches is None:
        watches = build_payload().get("watches", [])
    if label:
        for w in watches:
            if str(w.get("label")) == str(label):
                return w
    if session_id:
        for w in watches:
            if w.get("session_id") and str(w.get("session_id")) == str(session_id):
                return w
    return None


ARTIFACTS_NS = "fun/nightwatch/artifacts"
# [SPEC:CXC-GRAPH-VIEWER] The Night Watch window's embedded Decisions TAB was
# REMOVED (David 2026-08-06) — decisions are explored in the standalone Graph
# Viewer.  So the per-window `fun/nightwatch/decisions_graph` PROJECTION SEED
# (and its build_decisions_seed builder) is GONE: nothing binds that namespace
# any more, and seeding a surface that no longer exists is exactly the orphan
# feed the emitter retirement removed elsewhere ([CXC-GV-EMITTER-RETIRED] — one
# producer, or none).  What the window carries instead is a SCOPE, not data.
#
# SESSIONS_NS — the session id(s) THIS watch run produced decisions in, carried
# so the window's "View Decisions" button can scope the Graph Viewer to just this
# run ([CXC-GV-SCOPE]).  A LIST because a run may span several sessions.
SESSIONS_NS = "fun/nightwatch/sessions"


# The away_watch column stamped when a run OPENS its decision-observation window
# (owned by the watch-sessions build).  Its presence-and-truth on a row is what
# distinguishes "this run observed, and saw nothing" from "this row predates
# observation entirely" — the two cases that must NOT collapse ([CXC-GV-SCOPE]).
OBSERVATION_WINDOW_FIELD = "decision_baseline_ts"


def has_observation_window(watch: Dict[str, Any]) -> bool:
    """True iff this away_watch row genuinely OPENED a decision-observation
    window.  Read DEFENSIVELY: the column may not exist in this deployment yet,
    and once it does it is NULL on every legacy row — so only a PRESENT and
    TRUTHY stamp counts as a window.  Anything else is a legacy row."""
    return bool(watch.get(OBSERVATION_WINDOW_FIELD))


def watch_sessions(watch: Dict[str, Any]) -> List[str]:
    """The session id(s) a watch run carries, for scoping the Graph Viewer
    ([CXC-GV-SCOPE]).  THREE cases, and the middle one is the whole point:

    1. ``sessions`` recorded            -> those tokens (the run observed them).
    2. observation window OPENED, none  -> ``[]``.  A run that honestly watched
       and saw NOTHING must SAY so.  Falling back to ``session_id`` here would
       hand the viewer the shared UUID — the merged-four-runs scope this feature
       exists to eliminate — as a wrong-but-plausible answer.  ``[]`` makes the
       viewer show its loud UNSCOPED banner, which is the truth.
    3. NO observation window (legacy)   -> the ``session_id`` fallback.  For rows
       predating observation it is the best signal available, and it already
       degrades loudly when it matches nothing.

    HONEST LIMIT — read this before trusting the scope.  The away_watch row's
    ``session_id`` is the Claude-Code SESSION uuid, which is NOT 1:1 with a watch
    RUN: several runs share one session id, and most decisions record
    ``decided-in-session`` as a DATE (``2026-07-22``) or a date-slug, which no
    watch row exposes.  This never DERIVES a session from the run's date/label,
    because a derived match would be a plausible-but-wrong scope (the worst
    outcome: a filtered view the human reads as authoritative)."""
    raw = watch.get("sessions")
    if isinstance(raw, (list, tuple)):
        out = [str(s).strip() for s in raw if str(s or "").strip()]
        if out:
            return out                      # (1) observed sessions win
    if has_observation_window(watch):
        return []                           # (2) watched, saw nothing — say so
    sid = watch.get("session_id")           # (3) legacy row: best available
    return [str(sid).strip()] if str(sid or "").strip() else []


def nightwatch_initials(watch: Dict[str, Any],
                        decisions_projection: Optional[Dict[str, Any]] = None
                        ) -> Dict[str, Any]:
    """The per-window initial-state map for ONE watch — the values the Night
    Watch window binds, formatted exactly as its static defaults are
    ([SPEC:CXC-RETRO §5c/§5d]). Handed to launch_scene(), which passes it to the
    spawned runtime via COMPOSEIO_SCENE_INITIAL so the window shows THIS entry.

    [CXC-RETRO-RELAUNCH-COMPLETE]: this seeds EVERY data-bearing namespace the
    window binds — the header scalars AND the ARTIFACTS tab, not just the
    scalars (the §5c under-spec that let the tabs fall back to the scene's one
    baked-in entry). build_payload() has already derived
    title/started_fmt/ended_fmt; tokens/elapsed are formatted here to the
    window's compact strings.

    [SPEC:CXC-GRAPH-VIEWER]: the Decisions TAB is gone, so no decision
    PROJECTION is seeded any more; what is seeded instead is the run's SCOPE
    (``SESSIONS_NS``) that the "View Decisions" button carries on its
    ``_app/graph/open`` intent.  ``decisions_projection`` is retained ONLY as an
    accepted-and-ignored parameter so an older caller cannot break; it is never
    written into the seed."""
    started = watch.get("started_fmt") or format_watch_datetime(watch.get("started_ts"))
    ended = watch.get("ended_fmt") or format_watch_datetime(watch.get("ended_ts"))
    out: Dict[str, Any] = {
        "fun/nightwatch/title": watch.get("title")
        or format_watch_title(watch.get("started_ts")),
        "fun/nightwatch/decisions": str(int(watch.get("decisions", 0) or 0)),
        "fun/nightwatch/tokens": _fmt_tokens(watch.get("tokens", 0)),
        "fun/nightwatch/elapsed": _fmt_elapsed(watch.get("elapsed")),
        "fun/nightwatch/gitstate": str(watch.get("git_state") or ""),
        "fun/nightwatch/tasks": watch.get("tasks") or [],
        # the ARTIFACTS tab's list — the CLICKED watch's artifacts, so the tab
        # shows THIS night's docs, not a prior watch's ([CXC-RETRO-RELAUNCH-COMPLETE]).
        ARTIFACTS_NS: watch.get("artifacts") or [],
        # [CXC-GV-SCOPE] the run's session id(s) — ALWAYS set (possibly []), so
        # the "View Decisions" button always publishes a well-formed intent and
        # an unscopeable run degrades LOUDLY in the viewer rather than silently.
        SESSIONS_NS: watch_sessions(watch),
        # start/end ALWAYS set — an in-progress watch (no end) reads "In progress"
        # rather than OMITTING the field and letting the prior entry's baked end
        # show ([CXC-RETRO-RELAUNCH-COMPLETE]).
        "fun/nightwatch/started": f"Started  {started or '—'}",
        "fun/nightwatch/ended": f"Ended  {ended}" if ended else "In progress",
    }
    return out


# ---------------------------------------------------------------------------
# transport (mirrors project_validator._publish + console_supervisor paho)
# ---------------------------------------------------------------------------
def _parse_mqtt_url(url: str):
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


def run(mqtt_url: str, interval: float, seed: bool,
        _loops: Optional[int] = None, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 5b — publish + subscribe through the bus.

    See the module docstring for the three slice-5b notes (pattern subscription /
    ``fun/nightwatch/*`` is env-seeded, not published / the callback contract).

    ``run()`` MUST NOT RETURN while the source is alive ([CXC-SOB-RUN-BLOCKS]):
    the old ``while: publish; sleep(interval)`` was both the publish cadence AND
    the thing keeping the source thread alive, and a bare return is read by
    ``DataSourceSupervisor._guarded`` as "yielded my singleton" — a 5-second
    reclaim retry loop that reports nothing. ``park`` preserves the cadence
    exactly (publish now, then every ``interval``) and joins on ``stop``.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global; a second instance yields
    # (before seeding — an idempotent seed still shouldn't run from a loser).
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("away-watch-publisher")
    if lock is None:
        sys.stderr.write("[away_watch_publisher] another away-watch-publisher holds the singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    if seed:
        try:
            rid = _db().seed_default_away_watch()
            sys.stderr.write(f"[away_watch_publisher] seeded overnight entry "
                             f"id={rid}\n")
        except Exception as exc:
            sys.stderr.write(f"[away_watch_publisher] seed skipped: {exc!r}\n")

    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    # EXPLICIT scope, never Scope.AUTO ([CXC-SOB-DECLARE]). The two `_app/*`
    # namespaces are declared even though this source only SUBSCRIBES them: a
    # namespace left at AUTO with a LOCAL subscriber resolves to LOCAL, which is
    # exactly [DEC-COMPOSER-SEND-GLOBAL-SCOPE] — the widget's intent would die
    # in-process LOOKING SENT. `self/away_watches` is a `self/*` census-gated
    # candidate; Stage A pins it GLOBAL, its current effective scope.
    source_bus.declare(b, NS, scope=Scope.GLOBAL, source=SRC,
                       label="away watch history")
    for _ns in (INTENT_NS, BBOX_NS):
        source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                           label="app intent")
    source_bus.manifest(SRC)

    # Latest console bbox seen (updated by the bbox subscription); the launch
    # leg reads it to place the new window ([DEC-LAUNCH-PLACEMENT]).
    latest_bbox: List[Optional[Dict[str, int]]] = [None]
    # The single-instance Night Watch window tracker ([CXC-RETRO-AWAYWATCH-
    # SINGLETON]): SUPERSEDE so each relaunch reaps the prior window — the daemon
    # is long-lived and owns the spawn handle, so it is the natural reaper.
    windows = spawned_windows.SpawnedWindows(spawned_windows.SUPERSEDE)

    def on_app(ns: str, payload, _old) -> None:
        if ns == BBOX_NS:
            bb = parse_bbox(payload)
            if bb is not None:
                latest_bbox[0] = bb
            return
        if ns != INTENT_NS:
            return                     # another `_app/*` member; not ours
        intent = parse_open_intent(payload)
        if not intent:
            return
        # BOTH features on one launch: place the window beside the console
        # ([DEC-LAUNCH-PLACEMENT]) AND thread the CLICKED entry's data through
        # to it ([SPEC:CXC-RETRO §5c]) — look the watch up by its identity and
        # seed the window with ITS values, so a relaunch shows the entry that
        # was tapped (not the scene default), positioned beside the console.
        origin = launch_origin(latest_bbox[0])
        try:
            watch = find_watch(intent.get("session_id"), intent.get("label"))
        except Exception as exc:
            watch = None
            sys.stderr.write(f"[away_watch_publisher] watch lookup failed "
                             f"(launching with scene default): {exc!r}\n")
        # [SPEC:CXC-GRAPH-VIEWER] no decisions projection is built at click time
        # any more (the embedded Decisions tab is gone); the seed carries the
        # run's SCOPE instead ([CXC-GV-SCOPE]) — one cheap dict, no engine call
        # on the launch path.
        initials = nightwatch_initials(watch) if watch else None
        # SUPERSEDE (§5c): reap any prior Night Watch window, spawn the
        # replacement for the clicked entry — at most one nightwatch process
        # ever alive.
        proc, action = relaunch(windows, intent["scene_path"],
                                origin=origin, initials=initials)
        if proc is None:
            sys.stderr.write(f"[away_watch_publisher] open refused (not a repo "
                             f"scene): {intent['scene_path']!r}\n")
        else:
            where = f" @({origin[0]},{origin[1]})" if origin else ""
            tag = f" [{len(initials)} ns]" if initials else ""
            sys.stderr.write(f"[away_watch_publisher] open -> {action} "
                             f"(pid {proc.pid}){where}{tag}\n")

    # PATTERN, not exact — repeat delivery is REQUIRED here; see the module
    # docstring. `_app/*` also covers BBOX_NS, so one subscription serves both
    # legs and the handler filters.
    b.subscribe_pattern("_app/*", on_app)
    sys.stderr.write(f"[away_watch_publisher] listening _app/* "
                     f"(filtering {INTENT_NS} + {BBOX_NS})\n")

    def _tick() -> None:
        try:
            payload = build_payload()
            b.publish_state(NS, payload, source=SRC)
            sys.stderr.write(f"[away_watch_publisher] published "
                             f"{len(payload['watches'])} watch(es)\n")
        except Exception as exc:      # a bad read must never kill the daemon
            sys.stderr.write(f"[away_watch_publisher] read error "
                             f"(continuing): {exc!r}\n")

    if _loops is None:
        _tick()                        # publish NOW, then park — see slice 2
        source_bus.park(stop, poll_s=interval, on_tick=_tick)
    else:
        for _i in range(_loops):       # bounded, for tests only
            _tick()
            if _i + 1 < _loops:
                time.sleep(interval)
    return 0


# ---------------------------------------------------------------------------
# selftest — logic only, no broker, no network, TEMP db
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile

    fails: List[str] = []
    total = [0]

    def check(cond, msg):
        total[0] += 1
        if not cond:
            fails.append(msg)

    # title formatting: weekday COMPUTED from the date (David's exact example)
    check(format_watch_datetime(1784703000.0) == "Tue, Jul 21 @ 23:50",
          "(0a) datetime format (weekday computed)")
    check(format_watch_title(1784703000.0) == "Away Watch: Tue, Jul 21 @ 23:50",
          "(0b) window title format")
    check(format_watch_datetime(None) == "" and format_watch_title(0) ==
          "Away Watch", "(0c) missing timestamp degrades gracefully")

    # intent parsing
    check(parse_intent("a.yaml") == "a.yaml", "(1) bare string")
    check(parse_intent({"scene_path": "b.yaml"}) == "b.yaml", "(2) dict")
    check(parse_intent({"val": {"scene_path": "c.yaml"}}) == "c.yaml", "(3) nested val")
    check(parse_intent({"nope": 1}) is None, "(4) no scene refused")
    check(parse_intent(None) is None, "(5) None refused")

    # full-identity intent parsing (the CLICKED-entry identity, §5c)
    oi = parse_open_intent({"val": {"scene_path": "n.yaml",
                                    "session_id": "sid-9", "label": "L 9"}})
    check(oi == {"scene_path": "n.yaml", "session_id": "sid-9", "label": "L 9"},
          "(1a) open-intent threads scene_path + identity")
    check(parse_open_intent("bare.yaml") ==
          {"scene_path": "bare.yaml", "session_id": None, "label": None},
          "(1b) bare-string open-intent has no identity")
    check(parse_open_intent({"session_id": "x"}) is None,
          "(1c) open-intent without scene_path refused")

    # window-string formatters (mirror the Night Watch defaults)
    check(_fmt_tokens(23_700_000) == "23.7M" and _fmt_tokens(24_000) == "24k"
          and _fmt_tokens(312) == "312", "(1d) token formatter")
    check(_fmt_elapsed(28320) == "7h 52m" and _fmt_elapsed(3120) == "52m"
          and _fmt_elapsed(None) == "", "(1e) elapsed formatter")

    # bbox parsing + launch origin ([DEC-LAUNCH-PLACEMENT])
    check(parse_bbox({"val": {"x": 100, "y": 40, "w": 1280, "h": 800}}) ==
          {"x": 100, "y": 40, "w": 1280, "h": 800}, "(19) bbox from envelope")
    check(parse_bbox({"x": 5, "y": 6, "w": 7, "h": 8}) ==
          {"x": 5, "y": 6, "w": 7, "h": 8}, "(20) bare bbox dict")
    check(parse_bbox({"x": 1}) is None and parse_bbox("nope") is None,
          "(21) malformed bbox -> None")
    # origin = top-aligned, 10px right of the console's right edge
    check(launch_origin({"x": 100, "y": 40, "w": 1280, "h": 800}) ==
          (100 + 1280 + 10, 40), "(22) origin right+10, top-aligned")
    check(launch_origin(None) == DEFAULT_ORIGIN, "(23) no bbox -> default origin")

    # scene guard: a real repo scene resolves; an escape is refused
    real = "compose_projects/intelligence/console/nightwatch.compose.yaml"
    check(_scene_ok(real) is not None, "(6) real repo scene resolves")
    check(_scene_ok("/etc/passwd") is None, "(7) outside-repo refused")
    check(_scene_ok("../../../../etc/hosts") is None, "(8) traversal refused")
    check(_scene_ok("does/not/exist.yaml") is None, "(9) missing file refused")
    # [CXC-RETRO-AWAYWATCH-OPEN-TYPED] scene guard is TYPED: a real repo DOC (a
    # report .md) is NOT a Night Watch scene and is REFUSED here — never spawned
    # into `runtime <report.md> --transports`, which dies silently on a non-scene
    # (the 2026-07-26 maker not-clickable defect). A doc-typed target is the
    # widget's doc-open route, not the launch leg. SCENE_SUFFIX matches the widget
    # router's away_watch_view.SCENE_SUFFIX (the shared scene-vs-doc invariant).
    real_doc = "docs/watch_reports/away-watch-2026-07-26-report.md"
    check(SCENE_SUFFIX == ".compose.yaml", "(9a) scene suffix is .compose.yaml")
    check(_scene_ok(real_doc) is None,
          "(9b) a real repo DOC (.md) is refused — not a renderable scene")
    check(_scene_ok("compose_projects/intelligence/console/nightwatch.yaml") is None,
          "(9c) a bare .yaml (not .compose.yaml) is refused")

    # build_payload round-trips a TEMP db (never the real one)
    with tempfile.TemporaryDirectory() as d:
        os.environ["COMPOSEIO_RETRO_DB"] = os.path.join(d, "t.db")
        try:
            _db().seed_default_away_watch()
            pl = build_payload()
            check(isinstance(pl.get("watches"), list) and len(pl["watches"]) == 1,
                  "(10) payload lists the seeded watch")
            w = pl["watches"][0]
            check(w["label"] == "Overnight 2026-07-22" and w["tests"] == 1334,
                  "(11) seeded stats present")
            check(w["artifacts"] and w["artifacts"][0]["title"] == "Build log",
                  "(12) artifacts present")
            check(json.dumps(pl), "(13) payload is JSON-serializable")
            # the new window fields project onto the payload
            check(w["decisions"] == 12 and w["tokens"] == 23_700_000,
                  "(14) decisions/tokens projected")
            check(w["title"] == "Away Watch: Tue, Jul 21 @ 23:50",
                  "(15) computed title projected")
            check(isinstance(w["tasks"], list) and len(w["tasks"]) == 8
                  and {tk["status"] for tk in w["tasks"]} ==
                  {"done", "partial", "deferred"},
                  "(16) task checklist projected with all three states")
            check(isinstance(w["git_state"], str) and w["git_state"],
                  "(17) git_state projected (live, non-empty)")
            check(w["elapsed"] == 28320.0, "(18) elapsed derived (7h 52m)")

            # -- THE WRONG-ENTRY DEFECT (§5c): with TWO distinct watches, the
            # launch of the CLICKED entry must thread THAT entry's data — never
            # the other/default. Before the fix the launcher dropped the
            # identity and every relaunch showed the seeded overnight entry.
            afternoon_start = time.mktime((2026, 7, 22, 13, 10, 30, 0, 0, -1))
            _db().record_away_watch(
                label="Away 2026-07-22 PM",
                session_id="59f1b46e-afternoon",
                started_ts=afternoon_start, ended_ts=None,
                decisions=3, tokens=1_200_000,
                git_state="clean",
                tasks=[{"label": "wrong-entry defect", "status": "partial"}],
                artifacts=[{"title": "PM build log", "path": "docs/pm.md"}],
                scene_path="compose_projects/intelligence/console/nightwatch.compose.yaml")
            watches = build_payload()["watches"]
            check(len(watches) == 2, "(24) two distinct watches present")
            # the EXACT intent the widget publishes when TODAY's row is tapped
            pm_intent = {"val": {
                "scene_path": "compose_projects/intelligence/console/nightwatch.compose.yaml",
                "session_id": "59f1b46e-afternoon",
                "label": "Away 2026-07-22 PM"}}
            oi2 = parse_open_intent(pm_intent)
            picked = find_watch(oi2["session_id"], oi2["label"], watches)
            check(picked is not None and picked["label"] == "Away 2026-07-22 PM",
                  "(25) clicked identity resolves to the CLICKED watch")
            seed = nightwatch_initials(picked)
            # the window is parametrized by the CLICKED entry, NOT the overnight
            # default (12 decisions / 23.7M tok / the overnight title).
            check(seed["fun/nightwatch/decisions"] == "3",
                  "(26) launch seeds the CLICKED entry's decisions (not 12)")
            check(seed["fun/nightwatch/tokens"] == "1.2M",
                  "(27) launch seeds the CLICKED entry's tokens (not 23.7M)")
            check(seed["fun/nightwatch/title"] ==
                  "Away Watch: Wed, Jul 22 @ 13:10",
                  "(28) launch seeds the CLICKED entry's title (not overnight)")
            check(seed["fun/nightwatch/tasks"] ==
                  [{"label": "wrong-entry defect", "status": "partial"}],
                  "(29) launch seeds the CLICKED entry's tasks")
            # and the DEFAULT the widget-tapped OVERNIGHT row still resolves to
            # the overnight entry (cross-entry symmetry — no clobber).
            over = find_watch("overnight-2026-07-22", "Overnight 2026-07-22",
                              watches)
            check(nightwatch_initials(over)["fun/nightwatch/tokens"] == "23.7M",
                  "(30) the OTHER entry still seeds ITS own data")
            # a bare-string (identity-less) intent degrades to no seed, so the
            # window falls back to its static default (never a crash).
            check(find_watch(None, None, watches) is None,
                  "(31) identity-less intent seeds nothing (scene default)")

            # -- RECONCILIATION ([DEC-LAUNCH-PLACEMENT] × §5c): a single launch
            # must thread BOTH features — the placement origin (window beside the
            # console) AND the clicked entry's initials — independently, neither
            # regressing the other. Capture the env the spawned runtime would get.
            captured: Dict[str, Any] = {}

            class _FakeProc:
                pid = 4242

            def _fake_popen(cmd, cwd=None, env=None, **kw):
                captured.update(env or {})
                return _FakeProc()

            _real_popen = subprocess.Popen
            subprocess.Popen = _fake_popen  # type: ignore[assignment]
            try:
                origin = launch_origin({"x": 100, "y": 40, "w": 1280, "h": 800})
                launch_scene(pm_intent["val"]["scene_path"],
                             origin=origin, initials=seed)
            finally:
                subprocess.Popen = _real_popen  # type: ignore[assignment]
            check(captured.get("COMPOSEIO_WINDOW_X") == str(100 + 1280 + 10)
                  and captured.get("COMPOSEIO_WINDOW_Y") == "40"
                  and captured.get("COMPOSEIO_WINDOW_RAISE") == "1",
                  "(32) launch threads the placement origin env")
            _seed_env = json.loads(captured.get(SCENE_INITIAL_ENV, "{}"))
            check(_seed_env.get("fun/nightwatch/tokens") == "1.2M"
                  and _seed_env.get("fun/nightwatch/decisions") == "3",
                  "(33) launch threads the CLICKED entry's initials env")
            check("COMPOSEIO_WINDOW_X" in captured
                  and SCENE_INITIAL_ENV in captured,
                  "(34) BOTH features present on one launch (no regression)")

            # -- THE TAB-RECURRENCE (§5d): the seed must reach the DETAIL TAB,
            # not just the header (0acecba seeded only scalars, so Artifacts
            # kept the scene default — the 2026-07-23 defect).
            check(seed.get(ARTIFACTS_NS) == picked.get("artifacts"),
                  "(35) seed carries the CLICKED entry's ARTIFACTS list")
            # in-progress watch (no end) -> explicit 'In progress', never the
            # prior entry's baked end.
            check(seed.get("fun/nightwatch/ended") == "In progress",
                  "(36) in-progress watch seeds explicit 'In progress' end")
            # [SPEC:CXC-GRAPH-VIEWER] the removed Decisions tab takes its
            # projection seed with it; the seed now carries the run SCOPE, and
            # a legacy decisions_projection argument is IGNORED, never written.
            _proj = {"nodes": [{"id": "DEC-X", "is_anchor": True}], "edges": []}
            _s2 = nightwatch_initials(picked, decisions_projection=_proj)
            check("fun/nightwatch/decisions_graph" not in _s2,
                  "(37) no decisions projection is seeded (tab removed)")
            check(_s2.get(SESSIONS_NS) == watch_sessions(picked)
                  and isinstance(_s2.get(SESSIONS_NS), list),
                  "(38) seed carries the run's SCOPE (session id list)")
        finally:
            os.environ.pop("COMPOSEIO_RETRO_DB", None)

    # -- THE WINDOW-LEAK DEFECT ([CXC-RETRO-AWAYWATCH-SINGLETON], David
    # 2026-07-23 overnight): every roster click RELAUNCHED the Night Watch window
    # as a fresh OS process and tracked NOTHING, so 8 leaked overnight — and
    # since [cdf1217] gave the window an outbound MQTT leg, each leaked window is
    # a live bus participant (the message-doubling / duplicates-coexist hazard).
    # The fix: relaunch SUPERSEDES — the prior window is reaped, at most one
    # nightwatch process is ever live. This asserts it end-to-end via the real
    # relaunch() path with a faked spawn.
    class _WinProc:
        _n = 0

        def __init__(self):
            _WinProc._n += 1
            self.pid = 9000 + _WinProc._n
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def terminate(self):
            self._alive = False

        def wait(self, timeout=None):
            return 0

    spawned_procs: List[Any] = []

    def _win_popen(cmd, cwd=None, env=None, **kw):
        p = _WinProc()
        spawned_procs.append(p)
        return p

    real_scene = "compose_projects/intelligence/console/nightwatch.compose.yaml"
    _real_popen2 = subprocess.Popen
    subprocess.Popen = _win_popen  # type: ignore[assignment]
    try:
        windows = spawned_windows.SpawnedWindows(spawned_windows.SUPERSEDE)
        p1, act1 = relaunch(windows, real_scene, origin=(10, 20),
                            initials={"fun/nightwatch/title": "First"})
        p2, act2 = relaunch(windows, real_scene, origin=(10, 20),
                            initials={"fun/nightwatch/title": "Second"})
        check(act1 == "spawned" and act2 == "superseded",
              "(35) second relaunch SUPERSEDES the first")
        check(len(spawned_procs) == 2, "(36) each relaunch actually spawns")
        check(p1._alive is False,
              "(37) the FIRST window is reaped on relaunch (no leak)")
        check(p2._alive is True, "(38) the replacement window is alive")
        # eight rapid clicks (the exact overnight symptom) never accumulate
        for i in range(8):
            relaunch(windows, real_scene, origin=(10, 20),
                     initials={"fun/nightwatch/title": f"click{i}"})
        live = [p for p in spawned_procs if p.poll() is None]
        check(len(live) == 1 and len(windows.live) == 1,
              "(39) eight relaunches -> exactly ONE nightwatch process alive")
        # a refused scene (guard) keeps the current window — never zero windows
        p_ref, act_ref = relaunch(windows, "/etc/passwd")
        check(act_ref == "refused"
              and len([p for p in spawned_procs if p.poll() is None]) == 1,
              "(40) a refused relaunch keeps the live window (never zero)")
    finally:
        subprocess.Popen = _real_popen2  # type: ignore[assignment]

    # -- [SPEC:CXC-SOURCES-ON-BUS] slice 5b: `fun/nightwatch/*` reaches the
    # spawned window through COMPOSEIO_SCENE_INITIAL, NOT through the bus. The
    # spec ([CXC-SOB-FUN-IS-EXTERNAL], [CXC-SOB-INVENTORY] row 9) states the
    # opposite, and a future reader who believes it will "fix" this source by
    # declaring + publishing a namespace family nothing here owns. Pin the fact:
    # every fun/* key is in the SEED map, and the ONE namespace this module
    # publishes on the bus is `self/away_watches`.
    _seed_keys = set(nightwatch_initials({"label": "x"}))
    check(any(k.startswith("fun/nightwatch/") for k in _seed_keys),
          "(41) fun/nightwatch/* must be in the ENV-seed map")
    check(NS == "self/away_watches" and not NS.startswith("fun/"),
          "(42) the only namespace PUBLISHED on the bus is self/away_watches")
    _runtime = __import__("inspect").getsource(run)
    check(_runtime.count("publish_state(") == 1
          and "publish_state(NS," in _runtime,
          "(43) run() publishes exactly ONE namespace, and it is NS")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: away_watch_publisher selftest "
          f"({total[0] - len(fails)}/{total[0]})")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description="Project the Away-Watch history to "
                                 "self/away_watches + launch on the open intent.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--no-seed", action="store_true",
                    help="do not seed the 2026-07-22 overnight entry on start")
    ap.add_argument("--once", action="store_true",
                    help="build + print the payload once (publish if reachable)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if not a.no_seed and not a.once:
        pass  # run() seeds
    if a.once:
        if not a.no_seed:
            try:
                _db().seed_default_away_watch()
            except Exception as exc:
                sys.stderr.write(f"[away_watch_publisher] seed skipped: {exc!r}\n")
        payload = build_payload()
        print(json.dumps(payload, indent=2))
        return 0
    return run(a.mqtt, a.interval, not a.no_seed, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
