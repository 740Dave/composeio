#!/usr/bin/env python3
"""data_sources — run the console's session-owned data-source daemons IN-PROCESS.

WHY (David approved, 2026-07-22): the standalone-daemon model
(`start-console-daemons.sh`) is operationally fragile. It bit us twice in one day:
  1. the launcher picked the WRONG session's transcript — `ls -t | head -1` grabs
     whatever .jsonl is newest, so a *concurrent* session wins; and
  2. `project_validator` was simply never started, so the Projects tab was empty.
For the console's OWN session-tied data-sources, separate OS processes buy nothing
and cost start-order / wrong-session / duplicate / forgot-to-start fragility.

THE FIX (principle II.3, "collapse the transport, not the seam"): keep the bus
contract UNCHANGED — every data-source still publishes the SAME namespaces + SAME
payloads to the SAME MQTT broker exactly as today. Only the PROCESS MODEL changes:
each daemon's existing run-loop runs on a DAEMON THREAD the console owns, pinned to
the console's OWN session, instead of a standalone subprocess. The peer model and
per-daemon testability survive untouched.

HOW (no daemon logic changes): every daemon already exposes an importable
`main(argv)` that parses its args and calls `run(...)` with the correct defaults.
We invoke each module's `main(argv)` on a daemon thread with the EXACT argv the
shell script uses — so the wire is byte-for-byte the script's, with zero
arg-derivation drift. Nothing in `compose_projects/intelligence/publisher/*` or `compose_projects/intelligence/tools/*`
is modified.

Session pinning — the real fix for the wrong-session bug: the session transcript
is resolved DETERMINISTICALLY (an EXPLICIT `COMPOSEIO_SESSION_JSONL` / `JSONL` /
arg, else the globally-unique `$CLAUDE_CODE_SESSION_ID` glob, else a single
unambiguous .jsonl), NEVER `ls -t`. When none of those resolves — the ambiguous
case — we REFUSE LOUDLY (return None + shout) rather than fall back to the newest
.jsonl: that newest-wins guess is exactly the cross-instance leak this refactor
exists to kill, so the transcript-based sources are SKIPPED, never pointed at a
SIBLING's session.

Disable (headless-daemon path): set `COMPOSEIO_INPROCESS_SOURCES=0` to keep the
console a pure viewer and run the daemons the old way via `start-console-daemons.sh`.
"""
from __future__ import annotations

import functools
import glob
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_THIS = os.path.dirname(os.path.abspath(__file__))          # compose_projects/intelligence/console
_INTEL = os.path.dirname(_THIS)                              # compose_projects/intelligence
_PROJECTS = os.path.dirname(_INTEL)                          # compose_projects
REPO_ROOT = os.path.dirname(_PROJECTS)                       # repo root
_PUBLISHER_DIR = os.path.join(_INTEL, "publisher")
_TOOLS_DIR = os.path.join(_INTEL, "tools")

# The console scene — the ONE file that names which Intelligence the chat surface is
# bound to (its document-level `variables: {chat_id: ...}`, [DEC-CONSOLE-CHAT-ID-PARAM]).
CONSOLE_SCENE = os.path.join(_THIS, "console.compose.yaml")

# The ledger location comes from the ONE source of truth (bootstrap_paths), so the
# LEDGER default this bring-up hands the daemons is never a stale path literal
# ([CXC-DS-LEDGER-LOCATION-SINGLE-SOURCE]).
if os.path.join(REPO_ROOT, "python") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "python"))
from composeio.bootstrap_paths import DELIVERABLE_LEDGER_REL  # noqa: E402

# The project transcript directory (session .jsonl files live here).
#: ~/.claude/projects/<launch dir with "/" -> "-"> of the HOME-launched project.
#: Override: COMPOSEIO_CLAUDE_PROJECT_DIR.
_PROJ_DIR = os.environ.get("COMPOSEIO_CLAUDE_PROJECT_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "projects", os.path.expanduser("~").replace(os.sep, "-"))

# The ROOT under which every project-slug dir of session .jsonl files lives.
# CLAUDE_CODE_SESSION_ID self-resolution globs `<root>/*/<id>.jsonl` across ALL
# slugs (the id is globally unique, so it resolves THIS session regardless of
# which project-dir slug the transcript landed in). Module global so tests can
# repoint it at a fixture dir, exactly like `_PROJ_DIR`.
_PROJECTS_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")

# The ambient ENV that STEERS session resolution — enumerated in ONE place so every
# neutralizer clears EXACTLY the set the resolver reads ([DX-NARROW-WAIST]: one
# source of truth, consumed by reference, never re-listed). Fork-divergence defect:
# resolve_session_jsonl read THREE env vars, but the two neutralization sites (this
# module's _selftest and the test suite's autouse fixture) each hardcoded only
# CLAUDE_CODE_SESSION_ID, so an ambient COMPOSEIO_SESSION_JSONL / JSONL (the harness
# exports the live session's transcript) leaked into leg 1 and resolution returned the
# DEV session's transcript as PINNED — the id/refuse legs never ran. That made the
# session tests pass-or-fail on whether a live session was present (phantom reds).
# INVARIANT: any env var a resolution leg reads MUST be listed here, so a hermetic
# test that clears _ALL_SESSION_ENV is guaranteed complete by construction.
_SESSION_JSONL_ENV = ("COMPOSEIO_SESSION_JSONL", "JSONL")   # leg 1: explicit path pin
_SESSION_ID_ENV = "CLAUDE_CODE_SESSION_ID"                  # leg 2: unique-id glob
_ALL_SESSION_ENV = (*_SESSION_JSONL_ENV, _SESSION_ID_ENV)   # the hermetic-clear set


def _ensure_importable() -> None:
    """Put the daemon dirs on sys.path so `import self_publisher` etc. resolve —
    exactly as running them as scripts does."""
    for d in (_PUBLISHER_DIR, _TOOLS_DIR, os.path.join(REPO_ROOT, "python")):
        if d not in sys.path:
            sys.path.insert(0, d)


# --------------------------------------------------------------------------- #
# WHICH INTELLIGENCE THE CHAT SURFACE TALKS TO ([DEC-CONSOLE-CHAT-ID-PARAM])
# --------------------------------------------------------------------------- #
def scene_instances(scene_path: str = CONSOLE_SCENE) -> List[dict]:
    """The console scene's DECLARED `instances:` list — `[{id, role}, ...]`
    ([CXC-FC-UNIT]).

    THE SINGLE SOURCE the whole data layer reads.  The render runtime normally HANDS
    us the already-parsed list off the live `Composition` (the authoritative path — it
    knows which scene is actually running); this reader exists for the OTHER two launch
    paths (a bare `python3 data_sources.py`, and the headless
    `start-console-daemons.sh`), because a data layer that starts one bridge while the
    widgets show two panes is precisely the silent half-flip this parameterisation
    exists to make impossible.

    [CXC-FC-VAR-SCOPE]'s constraint, honoured here: the headless fallback used to read
    `variables: {chat_id}` out of this same file.  Moving the widgets to the declared
    list and leaving that reader behind would have kept the headless path on a
    one-Intelligence view of a fleet scene.  Both paths read `instances:` now.

    Falls back to the derived `$chat_id` variable so a scene that still declares one
    keeps working, and returns [] for a scene with neither — never raises, because a
    data layer that refuses to start over a parse hiccup is worse than one on defaults.
    """
    try:
        import yaml                                       # type: ignore
        with open(scene_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        if not isinstance(cfg, dict):
            return []
        try:
            if os.path.join(REPO_ROOT, "python") not in sys.path:
                sys.path.insert(0, os.path.join(REPO_ROOT, "python"))
            from composeio.render.engine.instances import parse_instances
            found = parse_instances(cfg)
        except Exception:
            found = [e for e in (cfg.get("instances") or []) if isinstance(e, dict)]
        if found:
            return found
        legacy = (cfg.get("variables") or {}).get("chat_id")
        return [{"id": str(legacy), "role": ""}] if legacy else []
    except Exception as exc:                               # missing file, bad YAML
        sys.stderr.write(f"[data_sources] could not read instances from "
                         f"{scene_path}: {exc}\n")
        return []


def resolve_instance_jsonl(instance_id: str, role: str) -> Optional[str]:
    """The newest transcript in `role`'s OWN project dir, or None.

    ⚠ THE SEARCH IS SCOPED BY ROLE, WHICH IS WHAT MAKES IT SAFE.  An `ls -t` newest-wins
    guess across the SHARED project pile is the cross-instance transcript leak
    [CXC-IC-SELF-IDENTITY] closed: two Intelligences launched from a home-ish cwd drop
    their `.jsonl` in one directory and newest-wins ping-pongs between them.  That hazard
    is a property of the PILE, and [CXC-FC-PROJECT-DIR] removed the pile — each role now
    owns `~/compose-instances/<role>/`, whose project dir contains only that role's
    sessions.  Picking the newest INSIDE one role's dir is therefore a choice among that
    role's own transcripts, not among strangers'.

    It is still a best-effort resolution and it is honest about its limits:
      * a role with no project dir yet -> None (that instance simply has no gauges);
      * a role running TWO live instances -> both write the same project dir, and newest
        wins, which can attribute one instance's transcript to the other.  That is the
        remaining gap, it is narrower than the original by exactly the amount role-keying
        bought, and it is what the RE-RESOLVABLE pin ([CXC-FC-S2-TELEMETRY], deferred)
        closes properly by keying on the session uuid the heartbeat already carries.
    """
    iid, r = str(instance_id or "").strip(), str(role or "").strip()
    if not iid or not r:
        return None
    try:
        if os.path.join(REPO_ROOT, "python") not in sys.path:
            sys.path.insert(0, os.path.join(REPO_ROOT, "python"))
        from composeio.instance_home import project_dir_for, project_dir_key
        proj = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                            project_dir_key(project_dir_for(r)))
        candidates = [os.path.join(proj, f) for f in os.listdir(proj)
                      if f.endswith(".jsonl")]
    except Exception:
        return None
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def scene_chat_id(scene_path: str = CONSOLE_SCENE) -> Optional[str]:
    """The FIRST declared instance's id — the compatibility spelling of
    `scene_instances()[0]["id"]`.

    KEPT, not forked: `start-console-daemons.sh`'s single-bridge leg and a few callers
    ask "which one Intelligence", and answering that from the same list is a
    projection, not a second source.  A caller that wants the fleet calls
    `scene_instances`.

    Returns None when the scene is absent or declares no instance (callers then keep
    the host `intelligence_id`, i.e. the pre-2026-08-11 behaviour)."""
    found = scene_instances(scene_path)
    return str(found[0]["id"]) if found else None


# --------------------------------------------------------------------------- #
# Session pinning
# --------------------------------------------------------------------------- #
def resolve_session_jsonl(explicit: Optional[str] = None,
                          *, log=sys.stderr) -> Optional[str]:
    """Resolve THIS console's session transcript, deterministically ([CXC-IC-SELF-IDENTITY]).

    Order (each leg DETERMINISTIC — its own session, never `ls -t` newest-wins):
      1. explicit arg -> $COMPOSEIO_SESSION_JSONL -> $JSONL. An explicit pin, set
         and existing, wins (the correct session, PASSED — the launcher/host export
         it on a re-host/spawn).
      2. $CLAUDE_CODE_SESSION_ID glob. The harness sets this in EVERY session's env
         (inherited by children — the console is a descendant), and the session id
         is globally UNIQUE, so `<projects>/*/<id>.jsonl` globs to THIS session's own
         transcript regardless of the project-dir slug. This makes every launch path
         (manual bounce, /bounce-console, the launcher) pinned-BY-CONSTRUCTION, not
         reliant on a per-launch env-export a bounce can forget
         ([DEC-TRANSCRIPT-SOURCES-BREAK-AT-HANDOVER-SEAM]).
      3. an UNAMBIGUOUS single .jsonl in the shared project dir (no pin, only one
         session present) is used.
      4. otherwise REFUSE LOUDLY (return None + shout). We do NOT `ls -t` newest-wins:
         that guess is exactly the cross-instance leak this refactor exists to kill
         (two sessions coexist in the shared home project dir, so newest ping-pongs to
         whichever wrote last). The console shows NO self/thread rather than a
         SIBLING's ([INSIGHT-TRUST-IS-A-CONSTRUCTED-PROPERTY]: loud refusal over
         plausible wrong).

    Returns None when nothing usable can be determined (transcript-based sources are
    then skipped).
    """
    # leg 1 env sources come from the single source of truth (_SESSION_JSONL_ENV) so
    # the hermetic-clear set can never drift out of sync with what leg 1 reads.
    candidate = explicit or next(
        (v for n in _SESSION_JSONL_ENV if (v := os.environ.get(n))), None)
    if candidate:
        if os.path.isfile(candidate):
            log.write(f"[data_sources] session PINNED -> {candidate}\n")
            return candidate
        log.write(f"[data_sources] !! pinned session not found: {candidate} "
                  f"— falling back\n")

    # (2) No explicit pin. Resolve THIS session by its own unique identity BEFORE any
    # directory scan: CLAUDE_CODE_SESSION_ID is harness-set + child-inherited, and the
    # id is globally unique, so one glob deterministically finds our OWN transcript even
    # when >1 coexist — the durable pin every launch path inherits, NOT a newest-wins
    # guess. Zero or (impossibly) ambiguous matches fall through to the refuse logic.
    session_id = os.environ.get(_SESSION_ID_ENV, "").strip()
    if session_id:
        matches = sorted(m for m in glob.glob(
            os.path.join(_PROJECTS_ROOT, "*", f"{session_id}.jsonl"))
            if os.path.isfile(m))
        if len(matches) == 1:
            log.write(f"[data_sources] session resolved by CLAUDE_CODE_SESSION_ID"
                      f"={session_id} -> {matches[0]}\n")
            return matches[0]
        if len(matches) > 1:
            log.write(f"[data_sources] !! CLAUDE_CODE_SESSION_ID={session_id} globbed "
                      f"{len(matches)} transcripts (ambiguous) — NOT guessing; "
                      "falling through\n")
        else:
            log.write(f"[data_sources] CLAUDE_CODE_SESSION_ID={session_id} set but no "
                      "matching transcript yet — falling through\n")

    # (3)/(4) No pin, no id resolution. REFUSE to guess when AMBIGUOUS (never `ls -t`).
    try:
        jsonls = [os.path.join(_PROJ_DIR, f) for f in os.listdir(_PROJ_DIR)
                  if f.endswith(".jsonl")]
    except OSError:
        jsonls = []
    if not jsonls:
        log.write("[data_sources] !! NO session .jsonl found and none pinned — "
                  "transcript-based sources will be SKIPPED. Set "
                  "COMPOSEIO_SESSION_JSONL to pin this console's session.\n")
        return None
    if len(jsonls) > 1:
        log.write(f"[data_sources] !! REFUSING to guess: {len(jsonls)} session .jsonl "
                  f"coexist in {_PROJ_DIR} and NONE is pinned. An ls -t newest-wins pick "
                  "can instrument a CONCURRENT instance's transcript (the cross-instance "
                  "context/thread leak). Transcript-based sources SKIPPED — set "
                  "COMPOSEIO_SESSION_JSONL to pin THIS session.\n")
        return None
    only = jsonls[0]
    log.write(f"[data_sources] single unambiguous session .jsonl (none pinned) -> {only}\n")
    return only


# --------------------------------------------------------------------------- #
# The supervisor
# --------------------------------------------------------------------------- #
@dataclass
class DataSource:
    """One in-process data-source: a name + a zero-arg run callable (the daemon's
    blocking run-loop, already bound to its argv).

    `stop` is the source's PARKING SIGNAL ([SPEC:CXC-SOURCES-ON-BUS]
    [CXC-SOB-RUN-BLOCKS]). A source converted off raw paho no longer has a
    `loop_forever()` to block on, so it parks on this Event through
    `composeio.source_bus.park` and returns only when the supervisor sets it.
    The supervisor OWNS these events: `stop()` sets every one BEFORE joining, so
    the real sources join cleanly instead of relying on daemon-thread death.
    None for an unconverted source (it still blocks in its own client loop).
    """
    name: str
    run: Callable[[], object]
    thread: Optional[threading.Thread] = field(default=None, repr=False)
    error: Optional[BaseException] = field(default=None, repr=False)
    stop: Optional[threading.Event] = field(default=None, repr=False)


class DataSourceSupervisor:
    """Owns the console's session-owned data-sources as DAEMON THREADS.

    Non-blocking: `start()` spawns each source on its own daemon thread and
    returns immediately, so the SDL render loop keeps the main thread. `stop()`
    joins them best-effort (they are daemon threads — the ones whose run-loops
    block on MQTT never return, but they die with the process; test doubles that
    honour a stop signal join cleanly, proving the join path).

    RECLAIM-ON-RELEASE at the SOURCE CLASS ([SPEC:CXC-IC-SINGLETON] IC-C27,
    generalized 2026-08-05 after DEFECT-IN-PROCESS-SINGLETON-SOURCE-YIELDS): every
    in-process source acquires a host-level `flock` singleton at the top of its
    `run()` and, if another instance holds it (a `/bounce-console` where this fresh
    console boots WHILE the dying old console's thread still owns the flock), YIELDS
    by returning. A ONE-SHOT yield goes DARK FOREVER — when the predecessor dies and
    the flock frees, nothing reclaims it. So this supervisor makes reclaim a CLASS
    property through ONE seam (not a per-source edit — that copy-paste is the very
    fork-divergence that made the channel-bridge fix recur here): `_guarded`
    RE-INVOKES a source whose `run()` RETURNS, after a bounded `reclaim_poll_s`, so it
    re-attempts `acquire_singleton` and comes up the moment the incumbent releases. A
    source that CRASHES (raises) still dies alone — only a clean return reclaims.
    """

    def __init__(self, sources: List[DataSource], *, reclaim_poll_s: float = 5.0):
        self.sources = list(sources)
        self._started = False
        self._lock = threading.Lock()
        # bounded wait between a source RETURNING (yielded its singleton) and the
        # supervisor re-invoking it to reclaim; also the interval it re-checks _stop.
        self._reclaim_poll_s = reclaim_poll_s
        # set on stop() to break the reclaim wait AND end the supervise loop — so a
        # source that keeps yielding does not restart forever after shutdown.
        self._stop = threading.Event()

    def start(self) -> "DataSourceSupervisor":
        with self._lock:
            if self._started:
                return self
            for src in self.sources:
                t = threading.Thread(target=self._guarded, args=(src,),
                                     name=f"ds:{src.name}", daemon=True)
                src.thread = t
                t.start()
                sys.stderr.write(f"[data_sources] + {src.name} (daemon thread)\n")
            self._started = True
            sys.stderr.write(f"[data_sources] started {len(self.sources)} "
                             f"in-process source(s)\n")
        return self

    def _guarded(self, src: DataSource) -> None:
        """Supervise ONE source. A CRASH (raise) logs + dies alone — never takes the
        process or its siblings down (mirrors each daemon's own 'a bad read never
        kills me'). A clean RETURN means a long-running in-process source YIELDED its
        host singleton (lost the boot race to a still-held predecessor flock): rather
        than leave it DARK FOREVER, RECLAIM — wait `reclaim_poll_s`, then re-invoke
        `run()`, which re-attempts `acquire_singleton` and comes up the moment the
        incumbent releases ([SPEC:CXC-IC-SINGLETON] IC-C27, source-class). This is the
        ONE seam that gives EVERY source bounce-safety without a per-source edit."""
        while not self._stop.is_set():
            try:
                src.run()
            except BaseException as exc:        # noqa: BLE001 — isolation boundary
                src.error = exc
                sys.stderr.write(f"[data_sources] !! {src.name} EXITED: {exc!r}\n")
                return                          # a crash dies alone — no restart-loop
            # run() returned WITHOUT raising: a long-running source that returns has
            # yielded its singleton. Wait, then re-run to RECLAIM on release. The
            # _stop wait doubles as the shutdown break so we never restart after stop.
            if self._stop.wait(self._reclaim_poll_s):
                return                          # stopped during the reclaim wait
            sys.stderr.write(f"[data_sources] ~ {src.name} yielded its singleton; "
                             f"retrying to RECLAIM on release\n")

    def stop(self, timeout: float = 2.0) -> None:
        # Signal FIRST so any source parked in its reclaim wait wakes and the
        # supervise loop exits, THEN join — else a re-invoking source never joins.
        self._stop.set()
        # ...and set every SOURCE's own parking Event ([CXC-SOB-RUN-BLOCKS]).
        # A converted source blocks in `source_bus.park(stop)`; without this it
        # would only die with the process, which makes stop() a no-op for exactly
        # the sources this arc converts. Setting them BEFORE the join turns the
        # "test doubles that honour a stop signal join cleanly" path into the
        # REAL path.
        for src in self.sources:
            if src.stop is not None:
                src.stop.set()
        with self._lock:
            for src in self.sources:
                t = src.thread
                if t is not None and t.is_alive():
                    t.join(timeout=timeout)
            self._started = False

    # -- introspection (for the launch path + tests) ----------------------- #
    def alive_names(self) -> List[str]:
        return [s.name for s in self.sources
                if s.thread is not None and s.thread.is_alive()]

    def thread_names(self) -> List[str]:
        return [s.thread.name for s in self.sources if s.thread is not None]

    @property
    def started(self) -> bool:
        return self._started


# --------------------------------------------------------------------------- #
# The real console source set — a faithful in-process mirror of
# start-console-daemons.sh (same modules, same argv => same wire).
# --------------------------------------------------------------------------- #
def _accepts(fn: Callable, name: str) -> bool:
    """Does `fn` take a keyword parameter called `name`?

    THE BRIDGE ACROSS THE CONVERSION ([SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-SLICES]).
    The sources convert to the bus one SLICE at a time, so for several commits
    this list holds a mix: a converted source's `main(argv, *, bus=None,
    stop=None)` wants the handles, an unconverted one's `main(argv)` would raise
    TypeError if handed them. Introspection binds each kwarg exactly where it is
    accepted, so no per-slice registry of "which ones are converted yet" has to
    be maintained — a table that would be one forgotten edit away from a source
    silently running on its own transport.

    Introspection alone WOULD be silent if a converted source forgot the kwarg,
    so it does not stand alone: the grep guard (`test_sources_have_no_paho.py`)
    fails any source that still imports paho unless it is on the explicit
    [CXC-SOB-EXEMPT] allow-list. Between them, "converted" and "gets the bus"
    cannot drift apart without a red test.
    """
    try:
        import inspect
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):              # a builtin / C callable
        return False
    p = params.get(name)
    if p is not None and p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD):
        return True
    return any(q.kind is q.VAR_KEYWORD for q in params.values())


def build_console_sources(
    *,
    session_jsonl: Optional[str],
    intelligence_id: str,
    mqtt_url: str,
    ledger: str,
    session: str,
    repo_root: str = REPO_ROOT,
    bus=None,
    chat_id: Optional[str] = None,
    instances: Optional[List[dict]] = None,
    session_jsonl_by_id: Optional[dict] = None,
) -> List[DataSource]:
    """Construct the DataSource list. `session_jsonl` None -> the three
    transcript-based sources (self_publisher, transcript_publisher,
    deliverable_monitor) are omitted (they cannot run without a transcript);
    everything else still comes up.

    N INSTANCES, N BRIDGES ([CXC-FC-S1-BRIDGES], 2026-08-12).  `instances` is the
    scene's declared `[{id, role}, ...]` list.  This function used to append EXACTLY
    ONE `channel_bridge`, steered by a single `chat_id` — which is why, when the
    console's single bridge was pointed at the hosted child on 2026-08-12, the HOST
    lost its bus voice entirely.  That was not a pane change, it was a TRANSPORT
    change: there was one bridge and it could only be in one place.  The mechanism was
    already correct — the flock is keyed `channel-bridge-<id>` and the client id is the
    same stable non-PID string, so distinct ids provably cannot contend — it was simply
    never instantiated more than once.

    `chat_id` remains accepted as the single-instance spelling and is folded into the
    list, so an omitted `instances` reproduces the pre-fleet wiring byte for byte.

    TWO IDS, TWO ROLES ([DEC-CONSOLE-CHAT-ID-PARAM], 2026-08-11):
      * `intelligence_id` — the HOST whose session this console instruments.  Every
        TELEMETRY source (self_publisher, transcript_publisher, deliverable_monitor,
        defect_metrics_publisher, daemon_watch) is pinned to it, because each one
        reads the HOST's transcript/meta.  An id here that does not match the
        transcript being tailed is the mismatch class [CXC-IH-5A] closed.
      * `chat_id` — the Intelligence the CHAT/CHANNEL surface talks to (today the
        Terminal Widget's hosted child, `architect-hosted`).  It steers EXACTLY ONE
        source, `channel_bridge`, and nothing else.  Defaults to `intelligence_id`,
        so an omitted chat_id reproduces the pre-parameterisation wiring byte for
        byte.  The bridge's singleton flock is keyed `channel-bridge-<id>`, so a
        repointed bridge takes a DIFFERENT lock and cannot contend with a live
        `architect` bridge in another console.

    `bus` is the console's own `ComposeIOBus`, threaded into every CONVERTED
    source's `main(argv, bus=..., stop=...)` ([CXC-SOB-HANDLE]). None — a headless
    `python3 data_sources.py` run, or a slice where a source is not converted yet
    — leaves each source to self-create through `composeio.source_bus.acquire_bus`,
    which memoizes so they still share ONE transport.
    """
    chat_id = chat_id or intelligence_id
    # THE DECLARED FLEET.  Order-preserving, de-duplicated, and never empty: with no
    # declared list the single `chat_id` IS the fleet, which is the pre-2026-08-12
    # console exactly.
    #
    # THE HOST IS APPENDED AS AN ORDINARY ENTRY ([CXC-FC-HOST-AS-PANE]).  It is not a
    # special case that gets its own hardcoded sources — it is one more id in the list,
    # so the telemetry loop below can have exactly one code path.  Appended LAST so an
    # explicit declaration of the host (with its role) keeps its position and its role;
    # de-duplication drops this fallback when the scene already named it.
    declared: List[dict] = []
    _seen: set = set()
    for entry in (list(instances or []) + [{"id": chat_id, "role": ""}]
                  + [{"id": intelligence_id, "role": ""}]):
        iid = str((entry or {}).get("id") or "").strip()
        if iid and iid not in _seen:
            _seen.add(iid)
            declared.append({"id": iid, "role": str(entry.get("role") or "").strip()})
    _ensure_importable()
    import fleet_roster
    import self_publisher
    import transcript_publisher
    import channel_bridge
    import deliverable_publisher
    import deliverable_monitor
    import deliverable_opener
    import deliverable_watcher
    import memory_monitor
    import away_watch_publisher
    import defect_metrics_publisher
    import project_validator
    import doc_viewer_launcher
    # [SPEC:CXC-GRAPH-VIEWER] [CXC-GV-EMITTER-RETIRED] decisions_publisher is NO
    # LONGER a data source — the pushed `self/decisions` feed is retired; the Graph
    # Viewer queries lts-engine directly ([CXC-GV-DATA-PATH]).  Not imported here.
    import decisions_viewer_launcher
    import dsd_viewer_launcher
    import hookup_viewer_launcher
    import beepout_viewer_launcher
    import lts_engine_status
    import config_persister
    import agents_skills_publisher
    import memory_publisher
    import meta_publisher
    import daemon_watch

    def _src(name: str, main: Callable, argv: List[str]) -> DataSource:
        # bind the module's OWN main(argv) — it derives every default exactly as
        # the CLI/script does, so run() sees identical args. No logic duplicated.
        # argv stays POSITIONAL and FIRST ([CXC-SOB-CONTRACT-FROZEN] argv parity);
        # `bus`/`stop` are bound as keywords, and only where accepted (`_accepts`).
        kwargs = {}
        stop_evt: Optional[threading.Event] = None
        if _accepts(main, "bus"):
            kwargs["bus"] = bus
        if _accepts(main, "stop"):
            stop_evt = threading.Event()
            kwargs["stop"] = stop_evt
        return DataSource(name=name,
                          run=functools.partial(main, argv, **kwargs),
                          stop=stop_evt)

    sources: List[DataSource] = []

    # --- N TELEMETRY SETS, one per declared instance with a KNOWN transcript ---------
    # [CXC-FC-S2-TELEMETRY] + [CXC-FC-HOST-AS-PANE].
    #
    # ⚠ NO PRIVILEGED SELF.  This loop used to be TWO code paths — a hardcoded set for
    # `intelligence_id` above, then a loop over the others that opened with
    # `if iid == intelligence_id: continue`.  That branch is literally "is this the
    # host", which [CXC-FC-HOST-AS-PANE] forbids BY NAME, and it is the shape that
    # produced the 2026-08-12 chimera: a privileged self is a second code path for one
    # concept, and the second path is the one that drifts.  There is ONE path now, and
    # the host reaches it the same way every other instance does — through
    # `transcripts`, with its own resolved `.jsonl` as an ordinary entry.
    #
    # Each set publishes to DISJOINT `self/<id>/*` and `thread/<id>/*` namespaces
    # (`--namespace-by-id`), so two instances can never merge onto one topic
    # ([CXC-IH-5A]).  An instance whose transcript is not known gets NO set rather than
    # one pointed at somebody else's — a gauge attached to the wrong Intelligence is
    # worse than a missing one, because it reports a state the system is not in.
    transcripts = dict(session_jsonl_by_id or {})
    if session_jsonl and intelligence_id not in transcripts:
        # The host's pinned transcript is an ENTRY, not an exception.  Supplied here
        # rather than by every caller so the uniform loop below has no special case to
        # make; an explicit entry for the host in `session_jsonl_by_id` still wins.
        transcripts[intelligence_id] = session_jsonl
    for entry in declared:
        iid = entry["id"]
        jsonl = transcripts.get(iid)
        if not jsonl:
            sys.stderr.write(f"[data_sources] instance '{iid}': no transcript known — "
                             f"no telemetry set (its gauges stay empty rather than "
                             f"showing another instance's numbers)\n")
            continue
        # The HOST keeps its historical unsuffixed source names so the daemon roster,
        # the supervisor's log lines and [CXC-SOB-CONTRACT-FROZEN]'s argv parity are
        # unchanged.  This is a NAMING continuity, not a branch: both arms run the same
        # argv through the same loop.
        suffix = "" if iid == intelligence_id else f":{iid}"
        sources.append(_src(f"self_publisher{suffix}", self_publisher.main,
                            ["--jsonl", jsonl, "--id", iid,
                             "--mqtt", mqtt_url, "--namespace-by-id"]))
        sources.append(_src(f"transcript_publisher{suffix}", transcript_publisher.main,
                            ["--jsonl", jsonl, "--id", iid,
                             "--mqtt", mqtt_url, "--namespace-by-id"]))

    # deliverable_monitor is CONSOLE-SELF, not per-instance: it tails a transcript for
    # doc writes into the shared LEDGER ([CXC-FC-SPLIT-RULE] — could its correct value
    # differ between two Intelligences?  No: there is one ledger).
    if session_jsonl:
        sources.append(_src("deliverable_monitor", deliverable_monitor.main,
                            ["--jsonl", session_jsonl, "--ledger", ledger,
                             "--repo-root", repo_root, "--mqtt", mqtt_url,
                             "--session", session]))
    else:
        sys.stderr.write("[data_sources] !! no session transcript — SKIPPING "
                         "deliverable_monitor\n")

    # --- session-independent ---------------------------------------------
    # ONE CHANNEL BRIDGE PER DECLARED INSTANCE ([CXC-FC-S1-BRIDGES]).  Each is the
    # console's WRITE path for its own Intelligence: the pane's composer publishes
    # `compose_projects/intelligence/<id>/compose` and THAT id's bridge unwraps it onto the raw
    # `compose_projects/intelligence/<id>/in` the channel delivers.  Distinct ids take distinct flocks
    # (`composeio-channel-bridge-<id>.lock`), so adding an instance can never take
    # another instance's voice away — which is exactly what happened when there was one.
    if len(declared) > 1 or chat_id != intelligence_id:
        sys.stderr.write(f"[data_sources] fleet: "
                         f"{[e['id'] for e in declared]} — one channel_bridge each "
                         f"(host telemetry stays '{intelligence_id}')\n")
    for entry in declared:
        sources.append(_src(f"channel_bridge:{entry['id']}", channel_bridge.main,
                            ["--id", entry["id"], "--mqtt", mqtt_url]))

    # ONE ROSTER COMPUTATION, NEVER ONE PER PANE ([CXC-FC-S3-ROSTER]).  A single
    # subscriber to `compose_projects/intelligence/+/channel/alive` applies the liveness rule and
    # publishes `fleet/roster`.  N per-pane discoveries would be N implementations of
    # one liveness rule, guaranteed to disagree under load — fork-divergence BY
    # CONSTRUCTION.  The declared pairs are handed in so a declared instance's role is
    # reported from its DECLARATION rather than guessed; an undeclared peer resolves
    # through the manifest chain and publishes `unknown` when it cannot.
    _roster_argv = ["--mqtt", mqtt_url]
    for entry in declared:
        _roster_argv += ["--instance", f"{entry['id']}:{entry['role']}"
                         if entry["role"] else entry["id"]]
    sources.append(_src("fleet_roster", fleet_roster.main, _roster_argv))
    sources.append(_src("deliverable_publisher", deliverable_publisher.main,
                        ["--ledger", ledger, "--mqtt", mqtt_url,
                         "--session", session]))
    sources.append(_src("deliverable_opener", deliverable_opener.main,
                        ["--mqtt", mqtt_url, "--repo-root", repo_root]))
    sources.append(_src("deliverable_watcher", deliverable_watcher.main,
                        ["--ledger", ledger, "--repo-root", repo_root,
                         "--mqtt", mqtt_url, "--interval", "3"]))
    sources.append(_src("memory_monitor", memory_monitor.main,
                        ["--mqtt", mqtt_url]))
    sources.append(_src("away_watch_publisher", away_watch_publisher.main,
                        ["--mqtt", mqtt_url]))
    # defect_metrics_publisher — [SPEC:CIO-DEFECT-REGISTRY] projects the Defect
    # Registry roll-up (SQLite defects_by_root_cause + list_defect_investigations
    # + the graph's fix-resistant leaders) to self/<id>/defects for the Dashboard
    # tab's Defects widget. Session-independent (reads the metrics.db + the defect
    # graph, not the transcript); ADDED additively.
    sources.append(_src("defect_metrics_publisher", defect_metrics_publisher.main,
                        ["--mqtt", mqtt_url, "--id", intelligence_id,
                         "--interval", "30"]))
    sources.append(_src("project_validator", project_validator.main,
                        ["--mqtt", mqtt_url, "--interval", "60"]))
    sources.append(_src("doc_viewer_launcher", doc_viewer_launcher.main,
                        ["--mqtt", mqtt_url]))
    # [SPEC:CXC-DVL] the console launcher rail's two in-process sources
    # ([CXC-DVL-REGISTER]): the decisions launcher spawns the standalone viewer
    # on _app/decisions/open (mirroring doc_viewer_launcher's registration), and
    # the lts-engine-status source publishes self/lts_engine/status @10s.  BOTH
    # session-independent (they read the engine + the bus, not the transcript),
    # so they come up even with no session pinned.  The launcher is handed the
    # per-instance geometry store (meta.<id>) so the spawned viewer restores +
    # persists its geometry per-instance ([CXC-DVL-GEOMETRY], architect ruling).
    _meta_path = os.path.join(repo_root, "compose_projects", "intelligence", "meta",
                              f"{intelligence_id}.meta.json")
    # [SPEC:CXC-GRAPH-VIEWER] [CXC-GV-EMITTER-RETIRED] the decisions_publisher FEED
    # source is RETIRED (was the pushed `self/decisions` emitter).  The Graph Viewer
    # the launcher below spawns now QUERIES lts-engine DIRECTLY in-process
    # ([CXC-GV-DATA-PATH]) — no always-on feed, no drift, no dark-when-daemon-down.
    # The launcher STAYS (it is CONTROL, not data, [CXC-GV-EMITTER-RETIRED] BOUNDARY).
    sources.append(_src("decisions_viewer_launcher",
                        decisions_viewer_launcher.main,
                        ["--mqtt", mqtt_url, "--geometry-store", _meta_path]))
    # [SPEC:CXC-DSDV] [CXC-DSDV-REGISTER] the DSD-viewer launcher: spawns a
    # standalone INTERACTIVE DSD-form window on _app/dsd/open (mirroring
    # doc_viewer_launcher's registration) AND runs the save-close notify leg
    # (subscribes _app/dsd/answered, correlates it to the send, notifies the
    # architect).  Session-independent (reads the bus + repo DSDs, not the
    # transcript), so it comes up even with no session pinned.
    sources.append(_src("dsd_viewer_launcher", dsd_viewer_launcher.main,
                        ["--mqtt", mqtt_url]))
    # The hookup-guide launcher: spawns a standalone READ window on
    # _app/hookup/open — the CONSUMER half of
    # `composeio.decision_ops.send_hookup_for_human_review`. Registered IN THE
    # SAME CHANGE as the endpoint on purpose: an intent nobody subscribes is an
    # intent published into the void, and built-tested-never-registered is a
    # known class here ([DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS]).
    # Session-independent (reads the bus + repo scenes, not the transcript).
    sources.append(_src("hookup_viewer_launcher", hookup_viewer_launcher.main,
                        ["--mqtt", mqtt_url]))
    # The beep-out sheet launcher: opens a bare-board BEEP-OUT SHEET in the
    # composeio DOC VIEWER on _app/beepout/open — the CONSUMER half of
    # `composeio.decision_ops.send_beepout_for_human_review`. Registered IN THE
    # SAME CHANGE as that endpoint's publish leg, which is not a convention here
    # but the rule [CXC-BPO-MCP] stated when it kept the namespace deliberately
    # MUTE: "the intent leg lands WITH its launcher or not at all", because
    # publishing where nothing subscribes reports success while doing nothing
    # (_app/dsd/notice already paid for that one).
    # ⛔ IT IS NOT A SECOND VIEWER. David 2026-08-15: "the viewer can be the
    # composeio doc viewer" — the module reuses `doc_viewer_launcher`'s scene
    # build, dedupe, placement and spawn by REFERENCE ([DX-NARROW-WAIST]).
    # Session-independent (reads the bus + repo sheets, not the transcript).
    sources.append(_src("beepout_viewer_launcher", beepout_viewer_launcher.main,
                        ["--mqtt", mqtt_url]))
    sources.append(_src("lts_engine_status", lts_engine_status.main,
                        ["--mqtt", mqtt_url, "--interval", "10"]))
    # config_persister — the DURABLE write-back for the Configuration tab's global
    # settings (retention · heartbeat cadence). Subscribes the config namespaces and
    # folds edits into the meta-object; re-seeds persisted values so a late-joining
    # widget shows them. The missing subscriber that made config edits evaporate on
    # restart (BUG 2). Session-independent; ADDED additively.
    sources.append(_src("config_persister", config_persister.main,
                        ["--meta", os.path.join(repo_root, "compose_projects", "intelligence", "meta",
                                                f"{intelligence_id}.meta.json"),
                         "--mqtt", mqtt_url, "--id", intelligence_id]))
    # agents_skills_publisher — [CXC-IC-CONFIG-LOADED] projects the registered
    # agent/skill file set to self/<id>/{agents,skills} for the Configuration
    # tab's Agents/Skills master-detail sections. Session-independent (reads the
    # .claude/ file set, not the transcript); ADDED additively.
    sources.append(_src("agents_skills_publisher", agents_skills_publisher.main,
                        ["--claude-dir", os.path.join(repo_root, ".claude"),
                         "--id", intelligence_id, "--mqtt", mqtt_url]))
    # memory_publisher — [DEC-LOCAL-MEMORY-CONFIG-SECTION] projects the instance's
    # automemory index to self/<id>/memories for the Configuration pane's "Local
    # Memory" master-detail section. The memory dir is INSTANCE-SCOPED: it is the
    # `memory/` sibling of THIS console's pinned session transcript's project dir
    # (~/.claude/projects/<slug>/memory) — derived, never hardcoded, so it is
    # correct for the architect AND any spawned builder. With no session pinned we
    # fall back to the project dir the transcripts live in. Session-independent
    # (reads the memory file set, not the transcript); ADDED additively.
    memory_dir = (os.path.join(os.path.dirname(session_jsonl), "memory")
                  if session_jsonl else os.path.join(_PROJ_DIR, "memory"))
    sources.append(_src("memory_publisher", memory_publisher.main,
                        ["--memory-dir", memory_dir,
                         "--id", intelligence_id, "--mqtt", mqtt_url]))
    # meta_publisher — [DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS] (David
    # 2026-08-08).  Projects the meta-object to `self/meta`, the ONLY publisher of
    # the namespace the Configuration tab's `bootstrap_manifest` widget subscribes
    # (parser.py registers it against self/meta).  It was built, tested, and never
    # registered, so that widget has been rendering console.compose.yaml's
    # hardcoded `initial:` seed (ok:true on every dusk-gift) — static values that
    # LOOK live, the Problem-6 shape of [DEC-REHOST-LEAVES-LONGRUNNING-CONSOLE-STALE].
    # Session-independent (reads the meta-object file, not the transcript).
    sources.append(_src("meta_publisher", meta_publisher.main,
                        ["--meta", _meta_path, "--mqtt", mqtt_url]))
    # daemon_watch — [DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS] (David
    # 2026-08-08).  The HEARTBEAT/CANARY liveness watcher (DSD D5) that exists to
    # surface silently-dead or silently-DUPLICATED daemons.  It too was built,
    # tested (test_start_console_stack.py asserts it watches app_launcher), and
    # never registered — the watchdog for silent death was itself silently dead.
    #
    # It is CORRECT in-process by construction: it unions TWO liveness signals
    # (flock primary + process-scan), so a source hosted on one of THIS process's
    # daemon threads reads UP from its held flock even though no separate process
    # matches.  Its probe is non-destructive — `singleton.probe_singleton_path`
    # try-acquires via the ONE `fcntl.flock` call site, which is per-open-file-
    # description, so probing a lock a SIBLING THREAD holds cannot disturb it.
    #
    # ARMED (David 2026-08-08, after validating the tools): `--no-registry` was
    # DROPPED, so the watcher now FILES defect nodes for a genuinely stale or
    # duplicated daemon, not just publishes `self/daemons`.
    #
    # Two registry miscategorizations were fixed FIRST, because arming as-is would
    # have filed two bogus defects on the very first tick — and a watcher that
    # cries wolf is worse than no watcher:
    #   * `intelligence_launcher` -> episodic=True. It runs ONLY while a session is
    #     hosted under the harness, so "down" is its resting state.
    #   * `decision_ops_mcp` -> per_session=True. One stdio MCP child per Claude
    #     session ([DEC-2026-07-24-STALENESS-FIX-AND-RECONCILE-STRATEGY]), so the 3-4
    #     live processes mean 3-4 sessions, not a fork. An absolute count cannot tell
    #     those apart; the honest fix is a session-count-aware check, and until then
    #     the module does not claim a signal it does not have.
    # Both still report their true status in `self/daemons`; only the RAISE is
    # suppressed, so the map stays honest while the alarm stays credible.
    sources.append(_src("daemon_watch", daemon_watch.main,
                        ["--mqtt", mqtt_url, "--id", intelligence_id]))
    return sources


def sources_enabled() -> bool:
    """In-process sources are ON unless COMPOSEIO_INPROCESS_SOURCES is a false-y
    value (`0`/`false`/`no`/`off`)."""
    v = os.environ.get("COMPOSEIO_INPROCESS_SOURCES", "1").strip().lower()
    return v not in ("0", "false", "no", "off", "")


def start_console_data_layer(
    *,
    session_jsonl: Optional[str] = None,
    intelligence_id: Optional[str] = None,
    mqtt_url: Optional[str] = None,
    ledger: Optional[str] = None,
    session: Optional[str] = None,
    repo_root: str = REPO_ROOT,
    bus=None,
    chat_id: Optional[str] = None,
    instances: Optional[List[dict]] = None,
    session_jsonl_by_id: Optional[dict] = None,
) -> Optional[DataSourceSupervisor]:
    """One call the console launch path makes to bring the WHOLE data layer up
    in-process, pinned to ITS session. Returns the running supervisor, or None
    when disabled via COMPOSEIO_INPROCESS_SOURCES=0. Non-blocking.

    Defaults mirror start-console-daemons.sh (env overrides): ID, MQTT, LEDGER,
    SESSION. The session TRANSCRIPT is resolved via resolve_session_jsonl (pinned,
    never ls -t unless nothing was pinned).

    `chat_id` ([DEC-CONSOLE-CHAT-ID-PARAM]) is the Intelligence the CHAT surface is
    bound to — passed by the render runtime off the LIVE scene's resolved
    `variables.chat_id`, else $CHAT_ID, else read from the console scene directly
    (`scene_chat_id`), else the host `intelligence_id`.  It steers `channel_bridge`
    and NOTHING else: telemetry stays pinned to the host whose transcript we tail.

    `bus` is the hosting app's `ComposeIOBus` ([CXC-SOB-HANDLE]). The render
    runtime should pass `app.bus` so the sources publish on the SAME bus the
    widgets subscribe — that is what collapses the console to a single transport
    and what makes Stage B's `Scope.LOCAL` flips possible at all. None (today's
    default, and the headless case) is safe: the sources self-create ONE shared
    bus between them via `source_bus.acquire_bus`."""
    if not sources_enabled():
        sys.stderr.write("[data_sources] COMPOSEIO_INPROCESS_SOURCES disabled — "
                         "console is a pure viewer; run start-console-daemons.sh "
                         "for the data layer\n")
        return None

    intelligence_id = intelligence_id or os.environ.get("ID", "architect")
    # The DECLARED FLEET ([CXC-FC-UNIT]), most-specific first:
    #   1. what the render runtime read off the LIVE scene's `instances:` list,
    #   2. the console scene's own `instances:` — the single source, read directly on
    #      the headless path (a bare `python3 data_sources.py`).
    # A scene with no list yields [], and the `chat_id` leg below then reproduces the
    # one-Intelligence console exactly.
    instances = instances if instances is not None else scene_instances()
    # The chat/channel id, kept as the single-instance spelling:
    #   1. an explicit argument, 2. $CHAT_ID (an operator override for a headless run),
    #   3. the FIRST declared instance, 4. the host id (the pre-2026-08-11 behaviour).
    chat_id = (chat_id or os.environ.get("CHAT_ID")
               or (str(instances[0]["id"]) if instances else None)
               or intelligence_id)
    mqtt_url = mqtt_url or os.environ.get("MQTT", "mqtt://mqtt-broker.local:1883")
    ledger = ledger or os.environ.get(
        "LEDGER", DELIVERABLE_LEDGER_REL)
    session = session or os.environ.get(
        "SESSION", time.strftime("%Y-%m-%d"))
    jsonl = resolve_session_jsonl(session_jsonl)

    # THE PER-INSTANCE TRANSCRIPT MAP, RESOLVED HERE ([CXC-FC-S2-TELEMETRY]).
    #
    # ⚠ WHY THIS EXISTS AT ALL.  `session_jsonl_by_id` was a parameter that NO LIVE
    # CALLER EVER SUPPLIED: the render runtime passes only bus/instances/chat_id, the
    # headless script passes none, and both defaults are None.  So the per-instance
    # telemetry capability could not occur in production — `self_publisher:<id>` could
    # never start for a non-host instance — while tests that passed the parameter
    # explicitly certified it as working.  A capability reachable only from a test is
    # not a capability; it is a claim.  Resolving it on the LIVE path is what makes the
    # test's subject real.
    resolved_by_id = dict(session_jsonl_by_id or {})
    for entry in (instances or []):
        iid = str(entry.get("id") or "").strip()
        role = str(entry.get("role") or "").strip()
        if not iid or iid in resolved_by_id or iid == intelligence_id:
            continue
        found = resolve_instance_jsonl(iid, role)
        if found:
            resolved_by_id[iid] = found

    sources = build_console_sources(
        session_jsonl=jsonl, intelligence_id=intelligence_id,
        mqtt_url=mqtt_url, ledger=ledger, session=session, repo_root=repo_root,
        bus=bus, chat_id=chat_id, instances=instances,
        session_jsonl_by_id=resolved_by_id)
    return DataSourceSupervisor(sources).start()


# --------------------------------------------------------------------------- #
# selftest — no broker, no network, no threads that touch MQTT
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    import tempfile

    fails: List[str] = []
    ran: List[int] = []          # count checks ACTUALLY run, so the banner can't drift

    def check(cond, msg):
        ran.append(1)
        if not cond:
            fails.append(msg)

    # (1) supervisor starts N threads, each run-loop is invoked, stop() joins
    started = {}
    stop_evt = threading.Event()

    def make(name):
        def _run():
            started[name] = True
            stop_evt.wait(5.0)          # honours a stop signal -> clean join
        return DataSource(name=name, run=_run)

    sup = DataSourceSupervisor([make("a"), make("b"), make("c")])
    sup.start()
    time.sleep(0.15)
    check(sorted(started) == ["a", "b", "c"], "(1) each run-loop invoked")
    check(len(sup.alive_names()) == 3, "(2) N threads alive")
    check(all(n.startswith("ds:") for n in sup.thread_names()),
          "(3) named daemon threads")
    check(all(s.thread.daemon for s in sup.sources), "(4) threads are daemon")
    stop_evt.set()
    sup.stop(timeout=2.0)
    check(sup.alive_names() == [], "(5) stop() joined all threads cleanly")

    # (6) a crashing source is isolated — logged, siblings unaffected. The sibling
    # PARKS on a stop event (exactly as check (1) above does) rather than returning:
    # a bare clean return is read by `_guarded` as "yielded my singleton" and starts
    # a reclaim RETRY LOOP, so an un-stopped supervisor here leaves a daemon thread
    # (`ds:fine`) alive for the life of the PROCESS, waking every `reclaim_poll_s` to
    # re-invoke `fine` and write to whatever `sys.stderr` has since become. In the
    # pre-commit collection order that stray thread wrote into
    # `test_sources_run_blocks.py`'s monkeypatched stderr and reddened the gate for
    # every committer. A supervisor that is start()ed MUST be stop()ed — the
    # supervisor's own contract ([DEC-INPROCESS-SOURCES]).
    ok = {}
    sib_stop = threading.Event()

    def boom():
        raise RuntimeError("nope")

    def fine():
        ok["fine"] = True
        sib_stop.wait(5.0)          # park -> clean join, never a reclaim loop

    sup2 = DataSourceSupervisor([DataSource("boom", boom),
                                 DataSource("fine", fine)])
    sup2.start()
    try:
        time.sleep(0.15)
        check(ok.get("fine") is True, "(6) sibling ran despite crash")
        check(sup2.sources[0].error is not None, "(7) crash captured, not raised")
    finally:
        sib_stop.set()
        sup2.stop(timeout=2.0)

    # (8) session pinning takes the EXPLICIT arg, never ls -t
    # Neutralize the WHOLE ambient session env (_ALL_SESSION_ENV — the harness exports
    # COMPOSEIO_SESSION_JSONL / JSONL / CLAUDE_CODE_SESSION_ID) so the pin/refuse legs
    # are tested in isolation. Clearing only the id leaked leg-1 pins in from the env.
    global _PROJECTS_ROOT
    _saved_env = {n: os.environ.pop(n, None) for n in _ALL_SESSION_ENV}
    _saved_root = _PROJECTS_ROOT
    try:
        with tempfile.TemporaryDirectory() as d:
            s = os.path.join(d, "mysession.jsonl")
            open(s, "w").close()
            import io
            buf = io.StringIO()
            check(resolve_session_jsonl(s, log=buf) == s,
                  "(8) explicit session wins")
            # env pin
            os.environ["COMPOSEIO_SESSION_JSONL"] = s
            try:
                check(resolve_session_jsonl(None, log=buf) == s,
                      "(9) env COMPOSEIO_SESSION_JSONL pins")
            finally:
                del os.environ["COMPOSEIO_SESSION_JSONL"]
            # missing pin -> not silently accepted (returns None or a warned fallback)
            buf2 = io.StringIO()
            _ = resolve_session_jsonl(os.path.join(d, "nope.jsonl"), log=buf2)
            check("not found" in buf2.getvalue(), "(10) missing pin is warned")

        # (10a-c) CLAUDE_CODE_SESSION_ID self-resolution: DETERMINISTIC own session,
        # never ls -t. A matching transcript resolves EVEN when a sibling coexists;
        # a set-but-unmatched id, or no id, still REFUSES among >1 (no newest-wins).
        with tempfile.TemporaryDirectory() as d:
            import io
            sid = "aaaaaaaa-1111-1111-1111-111111111111"
            slug = os.path.join(d, "-Users-you-proj")
            os.makedirs(slug)
            own = os.path.join(slug, f"{sid}.jsonl")
            open(own, "w").close()
            # a sibling in ANOTHER slug so a bare listdir/newest-wins would be ambiguous
            other = os.path.join(d, "-Users-you-other")
            os.makedirs(other)
            open(os.path.join(other, "bbbbbbbb-2222-2222-2222-222222222222.jsonl"),
                 "w").close()
            _PROJECTS_ROOT = d
            os.environ["CLAUDE_CODE_SESSION_ID"] = sid
            b = io.StringIO()
            check(resolve_session_jsonl(None, log=b) == own,
                  "(10a) CLAUDE_CODE_SESSION_ID resolves OWN session amid a sibling")
            check("CLAUDE_CODE_SESSION_ID" in b.getvalue(),
                  "(10b) id-resolution is logged")
            # id set but NO matching transcript -> fall through to refuse (never ls -t)
            os.environ["CLAUDE_CODE_SESSION_ID"] = "cccccccc-3333-3333-3333-333333333333"
            b2 = io.StringIO()
            global _PROJ_DIR
            _saved_projdir = _PROJ_DIR
            _PROJ_DIR = d  # d has no top-level .jsonl -> None (SKIPPED), never a guess
            try:
                check(resolve_session_jsonl(None, log=b2) is None,
                      "(10c) unmatched id + no top-level pin REFUSES (no ls -t)")
            finally:
                _PROJ_DIR = _saved_projdir
    finally:
        _PROJECTS_ROOT = _saved_root
        for _n, _v in _saved_env.items():
            if _v is not None:
                os.environ[_n] = _v
            else:
                os.environ.pop(_n, None)

    # (11) disable flag
    os.environ["COMPOSEIO_INPROCESS_SOURCES"] = "0"
    try:
        check(sources_enabled() is False, "(11) disable flag honoured")
        check(start_console_data_layer() is None, "(12) disabled -> no supervisor")
    finally:
        del os.environ["COMPOSEIO_INPROCESS_SOURCES"]
    check(sources_enabled() is True, "(13) enabled by default")

    # (14) build_console_sources omits transcript sources when no session
    src_names = [s.name for s in build_console_sources(
        session_jsonl=None, intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22")]
    check("self_publisher" not in src_names
          and "transcript_publisher" not in src_names
          and "deliverable_monitor" not in src_names,
          "(15) no-session omits transcript sources")
    check("project_validator" in src_names and "memory_monitor" in src_names
          and "config_persister" in src_names
          and "agents_skills_publisher" in src_names
          and "memory_publisher" in src_names
          and "decisions_viewer_launcher" in src_names
          and "dsd_viewer_launcher" in src_names
          and "hookup_viewer_launcher" in src_names
          and "beepout_viewer_launcher" in src_names
          and "lts_engine_status" in src_names
          and "defect_metrics_publisher" in src_names
          and "meta_publisher" in src_names
          and "daemon_watch" in src_names,
          "(16) session-independent sources still present (incl. "
          "config_persister + agents_skills_publisher + memory_publisher + "
          "decisions_viewer_launcher + dsd_viewer_launcher + "
          "hookup_viewer_launcher + beepout_viewer_launcher + "
          "lts_engine_status + defect_metrics_publisher + meta_publisher + "
          "daemon_watch)")
    check("decisions_publisher" not in src_names,
          "(16b) [CXC-GV-EMITTER-RETIRED] decisions_publisher de-registered")
    # with a session, 20 are present (11 originals + config_persister +
    # agents_skills_publisher + memory_publisher + decisions_viewer_launcher +
    # lts_engine_status + dsd_viewer_launcher + defect_metrics_publisher +
    # meta_publisher + daemon_watch).
    # [SPEC:CXC-GRAPH-VIEWER] [CXC-GV-EMITTER-RETIRED] dropped decisions_publisher
    # (the retired pushed feed) — the source-count assertion drops by one (19 -> 18).
    # [DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS] wired meta_publisher +
    # daemon_watch, both built-and-tested but never registered (18 -> 20).
    src_all = [s.name for s in build_console_sources(
        session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22")]
    # 20 -> 21: [CXC-FC-S3-ROSTER] added `fleet_roster`, THE one subscriber to
    # `compose_projects/intelligence/+/channel/alive`. The bridge name also gained its instance suffix
    # (`channel_bridge:<id>`) because there is now one PER DECLARED INSTANCE
    # ([CXC-FC-S1-BRIDGES]) — a bare `channel_bridge` would be the singleton again.
    # 21 -> 22: `hookup_viewer_launcher`, the consumer half of
    # `send_hookup_for_human_review` (_app/hookup/open).
    # 22 -> 23: `beepout_viewer_launcher`, the consumer half of
    # `send_beepout_for_human_review` (_app/beepout/open) — landed in the SAME
    # change that turned that endpoint's publish leg on ([CXC-BPO-LAUNCHER]).
    check(len(src_all) == 23, f"(17) full set is 23, got {len(src_all)}")

    if fails:
        for m in fails:
            sys.stderr.write(f"FAIL {m}\n")
        return 1
    print(f"PASS: data_sources selftest ({len(ran)}/{len(ran)} checks)")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--session-jsonl", default=None,
                    help="pin THIS console's session transcript (else "
                         "$COMPOSEIO_SESSION_JSONL/$JSONL)")
    ap.add_argument("--mqtt", default=None)
    ap.add_argument("--id", default=None)
    ap.add_argument("--chat-id", default=None,
                    help="the Intelligence the CHAT surface talks to (steers "
                         "channel_bridge ONLY); default: the console scene's "
                         "variables.chat_id, else --id")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--hold", type=float, default=None,
                    help="run the data layer for N seconds then stop (headless "
                         "proof); default: run until Ctrl-C")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(_selftest())

    sup = start_console_data_layer(session_jsonl=a.session_jsonl, mqtt_url=a.mqtt,
                                   intelligence_id=a.id, ledger=a.ledger,
                                   chat_id=a.chat_id)
    if sup is None:
        raise SystemExit(0)
    try:
        if a.hold is not None:
            time.sleep(a.hold)
            sup.stop()
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        sup.stop()
