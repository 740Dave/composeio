#!/usr/bin/env python3
"""daemon_watch — the HEARTBEAT/CANARY liveness watcher for the persistent
ComposePlatform console daemons ([DEC-CAROUSEL-PLATFORM-CALLS-SETTLED], DSD D5).

WHY IT EXISTS: decision-ops silently ran as 3 duplicate `decision_ops.mcp_server`
instances (observed 2026-07-30) with NO signal — the singleton flock
([SPEC:CXC-IC-SINGLETON]) prevents doubling for daemons that TAKE the flock, but
the stdio MCP server takes none, so nothing surfaced the fork. This daemon is the
missing signal: it POLLS every daemon's liveness every ~5s and

  1) raises a CANARY when a daemon's singleton has had no live holder for > 30s
     (stale/down), REUSING the defect-registry (`composeio.defect_ops.report` /
     `resolve`) as the canary primitive — a stale daemon becomes a DEFECT node,
     not a new parallel mechanism ([SPEC:CIO-DEFECT-REGISTRY]); and
  2) FLAGS a DUPLICATE when more than one live process claims one daemon id (the
     singleton SHOULD make this impossible, so a duplicate is itself a defect).

TWO LIVENESS SIGNALS, unioned (the crux — a console daemon can run either way):
  * FLOCK (primary): most console daemons now run IN-PROCESS on daemon THREADS of
    the console runtime ([DEC-INPROCESS-SOURCES]); there is NO separate process to
    pgrep, only the per-role singleton flock the thread holds (whose recorded pid
    is the console runtime's). So liveness = "is a live process HOLDING the
    role's flock?" — probed non-destructively by a try-acquire (LOCK_EX|LOCK_NB):
    if WE can take it, nobody holds it -> DOWN (we release immediately).
  * PROCESS-SCAN (duplicate + flock-less): a flock records only ONE holder, so a
    duplicate that BYPASSES the flock (the flock-less `decision_ops.mcp_server`,
    or a non-POSIX degrade) is invisible to the flock. A `ps` cmdline scan counts
    live instances. This is also the ONLY signal for the flock-less mcp server.
  * HTTP (storage services): bus_storage (:8420) is the ONE store ([SPEC:CXC-STORE];
    document_store is retired), an HTTP service with a `/health` endpoint, usually on
    the bus-master host — not a local flock holder. It is probed by health GET when a
    `health` URL is declared.

  alive_ids = {flock holder pid?} ∪ {process-scan pids} ∪ {"health" if healthy}
  |alive_ids| == 0 -> down    == 1 -> up    >= 2 -> duplicate

The core (derive-status + the staleness/duplicate STATE MACHINE + the map build)
is PURE and fully injectable (probe fn, clock, canary sink), so the >30s-canary /
fresh-flock / duplicate / recovery behaviour is host-testable with mock flocks,
mock pids, and a mock clock — see `--selftest` and tests/test_daemon_watch.py.

SURFACES:
  (a) the Defects dashboard, via the defect-registry (report/resolve) — durable;
  (b) `self/daemons` on the bus (scope global -> composeio/global/self/daemons):
      the full liveness map {each daemon: up|down|duplicate, last_seen, pids, ...}
      so the console can badge it. Emitted every poll (5s = 0.2 Hz, within the
      [CXC-PS-RATE] ≤1 Hz external-emit ceiling; internal poll ≠ external emit).

Run:  python3 daemon_watch.py --mqtt mqtt://mqtt-broker.local:1883 [--interval 5] [--threshold 30]
      python3 daemon_watch.py --once      # probe + print the map once (READ-ONLY: never files a defect)
      python3 daemon_watch.py --list      # print the daemon registry it watches
      python3 daemon_watch.py --selftest  # pure-logic tests, no broker, no network, no files
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

# repo-root import shim (sibling daemons do the same: compose_projects/intelligence/publisher/X.py -> <root>/python)
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))

NS = "self/daemons"
DEFAULT_THRESHOLD_S = 30.0          # DSD D5: canary a daemon stale for > 30s
DEFAULT_INTERVAL_S = 5.0            # DSD D5: poll every ~5s (0.2 Hz emit ≤ [CXC-PS-RATE] 1 Hz)
DEFAULT_ID = "architect"
DEFAULT_GRAPH = str(REPO_ROOT / "defects" / "defect-graph.lts")


# --------------------------------------------------------------------------- #
# the registry — expected daemon id <-> singleton flock name <-> process match
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DaemonSpec:
    """One expected persistent daemon. `flock` is its [SPEC:CXC-IC-SINGLETON] lock
    NAME (a `{id}` placeholder is filled with the intelligence id for the
    id-scoped ones); `match` is a distinctive cmdline substring for the `ps` scan
    (matches BOTH `python3 .../x.py` and `python -m ...x` invocations); `health`
    is an HTTP URL for storage services with no local flock. Any of the three may
    be None — a daemon is watched by whichever signals it declares. EASY TO
    EXTEND: append a DaemonSpec to `default_registry`."""
    id: str
    flock: Optional[str] = None
    match: Optional[str] = None
    health: Optional[str] = None
    note: str = ""
    # ── Two ways a daemon can be ABSENT-or-MULTIPLE without being BROKEN ──────
    # Added 2026-08-08, when arming the defect sink (dropping --no-registry) would
    # have filed two bogus defects on its very first tick. A watcher that cries
    # wolf gets ignored, which defeats the whole point of having one.
    #
    # episodic: the daemon is legitimately absent much of the time — it exists only
    #   while something is happening. `intelligence_launcher` is the case: it runs
    #   ONLY while a Claude session is hosted under the harness, so "down" is its
    #   normal resting state, not a defect. Status is still reported (so the map
    #   tells the truth); it just never raises a staleness CANARY.
    episodic: bool = False
    # per_session: exactly one instance is spawned PER CLAUDE SESSION, so N live
    #   processes means N sessions, not a fork. `decision_ops_mcp` is the case —
    #   it is a stdio MCP CHILD of each session that registers it
    #   ([DEC-2026-07-24-STALENESS-FIX-AND-RECONCILE-STRATEGY]), so an ABSOLUTE
    #   count cannot distinguish "4 sessions open" from "a duplicate fork". Until
    #   the check is made session-count-aware, report the count but never raise a
    #   DUPLICATE. NOTE this deliberately gives up the signal this module was
    #   originally built for (3 duplicate decision-ops seen 2026-07-30) — a
    #   knowingly-blind check beats a knowingly-wrong alarm, and the honest fix is
    #   to compare against the live session count, not to guess from a bare total.
    per_session: bool = False

    def flock_name(self, ident: str) -> Optional[str]:
        return self.flock.format(id=ident) if self.flock else None


def default_registry(ident: str = DEFAULT_ID,
                     storage_host: str = "mqtt-broker.local") -> List[DaemonSpec]:
    """The persistent daemon set to watch, sourced from
    `compose_projects/intelligence/start-console-daemons.sh` (the console data layer, whether it
    runs standalone or in-process) + the launcher-spawned launchers + decision-ops
    + the storage services. Filenames are the `ps`-scan tokens; flock names are the
    verbatim `acquire_singleton(...)` keys grep-proven from the daemon sources."""
    S = "http://%s" % storage_host
    return [
        # ---- the console data layer (start-console-daemons.sh; in-process today) ----
        DaemonSpec("self_publisher", "self-publisher-{id}", "self_publisher.py",
                   note="self/<id>/* telemetry"),
        DaemonSpec("transcript_publisher", "transcript-publisher-{id}",
                   "transcript_publisher.py", note="thread/self narration"),
        DaemonSpec("channel_bridge", "channel-bridge-{id}", "channel_bridge.py",
                   note="intelligence/<id>/{in,out} relay"),
        DaemonSpec("deliverable_publisher", "deliverable-publisher",
                   "deliverable_publisher.py"),
        DaemonSpec("deliverable_monitor", "deliverable-monitor",
                   "deliverable_monitor.py"),
        DaemonSpec("deliverable_opener", "deliverable-opener",
                   "deliverable_opener.py"),
        DaemonSpec("deliverable_watcher", "deliverable-watcher",
                   "deliverable_watcher.py"),
        DaemonSpec("memory_monitor", "memory-monitor", "memory_monitor.py"),
        DaemonSpec("away_watch_publisher", "away-watch-publisher",
                   "away_watch_publisher.py"),
        DaemonSpec("project_validator", "project-validator", "project_validator.py"),
        DaemonSpec("config_persister", "config-persister", "config_persister.py"),
        DaemonSpec("agents_skills_publisher", "agents-skills-publisher-{id}",
                   "agents_skills_publisher.py"),
        DaemonSpec("defect_metrics_publisher", "defect-metrics-publisher",
                   "defect_metrics_publisher.py", note="self/<id>/defects roll-up"),
        # Added 2026-08-08: these two were in build_console_sources but absent
        # HERE, so the watcher's registry was a THIRD hand-maintained copy of the
        # roster (roster 18 / this registry / start-console-daemons.sh 13) and
        # under-watched by two. The durable fix is to DERIVE the console-data-layer
        # slice of this registry from build_console_sources and keep only the
        # non-roster entries below (launchers, MCP, storage) explicit —
        # [DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS].
        DaemonSpec("memory_publisher", "memory-publisher-{id}",
                   "memory_publisher.py", note="self/<id>/memories index"),
        DaemonSpec("lts_engine_status", "lts-engine-status",
                   "lts_engine_status.py", note="self/lts_engine/status"),
        DaemonSpec("meta_publisher", "meta-publisher", "meta_publisher.py",
                   note="self/meta bootstrap manifest"),
        # ---- launchers (spawned by the launch bar / launcher) ----
        DaemonSpec("app_launcher", "app-launcher", "app_launcher"),
        DaemonSpec("doc_viewer_launcher", "doc-viewer-launcher", "doc_viewer_launcher"),
        DaemonSpec("dsd_viewer_launcher", "dsd-viewer-launcher", "dsd_viewer_launcher"),
        DaemonSpec("decisions_viewer_launcher", "decisions-viewer-launcher",
                   "decisions_viewer_launcher"),
        DaemonSpec("intelligence_launcher", "intelligence-launcher-{id}",
                   "launcher/launcher.py", episodic=True,
                   note="the harness launcher (superseding singleton) — EPISODIC: up only "
                        "while a session is hosted under the harness, so 'down' is its "
                        "normal resting state, not a defect"),
        # ---- decision-ops MCP: NO flock (stdio subprocess per client). Watched by
        #      process-scan ONLY, and PER_SESSION: one child per Claude session that
        #      registers it, so N live == N sessions, not a fork. ----
        DaemonSpec("decision_ops_mcp", None, "decision_ops.mcp_server", per_session=True,
                   note="stdio MCP; NO singleton -> process-scan is the only signal. "
                        "per_session: an ABSOLUTE count cannot distinguish N sessions "
                        "from a duplicate fork — needs a session-count-aware check"),
        # ---- bus-native storage: HTTP services, no local flock ([CXC storage]) ----
        DaemonSpec("bus_storage", None, "bus_storage/server", health=f"{S}:8420/health",
                   note="the ONE store — bus:// assets + doc-as-blob; usually on the bus-master host"),
        # document_store RETIRED ([SPEC:CXC-STORE]) — the doc catalog is now bus:// blobs on
        # bus_storage; there is no separate :8421 daemon to watch.
        # ---- self-watch: this watcher is a daemon too ----
        DaemonSpec("daemon_watch", "daemon-watch", "daemon_watch.py",
                   note="this liveness watcher"),
    ]


# --------------------------------------------------------------------------- #
# probes (the injectable seam — real ones here; tests inject fakes)
# --------------------------------------------------------------------------- #
def _pid_alive(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, just not ours to signal
    except OSError:
        return False
    return True


def probe_flock(path: str) -> tuple[Optional[bool], Optional[int]]:
    """(held, pid) for a singleton lock file. `held` True == a live process holds
    the flock (daemon UP); False == nobody holds it (DOWN); the file's absence is
    DOWN. NON-DESTRUCTIVE: a try-acquire (LOCK_EX|LOCK_NB) that SUCCEEDS proves
    nobody holds it — released immediately, never disturbing a real holder.

    DE-FORKED (IC-C17): this delegates to the ONE shared flock primitive
    (`singleton.probe_singleton_path`) instead of hand-rolling a flock here — which is
    why there is exactly ONE flock CALL SITE in the repo (the prose avoids the literal
    open-paren token the invariant test counts). This watcher exists to SURFACE forks;
    it must not BE one."""
    from singleton import probe_singleton_path   # sys.path shim seated at import
    return probe_singleton_path(path)


def scan_processes(pattern: str, exclude_pids: Set[int]) -> List[int]:
    """Live pids whose `ps` cmdline contains `pattern`, minus `exclude_pids` (this
    process). Deterministic and dependency-free (parses `ps -eo pid=,command=`);
    never raises — a scan failure returns []."""
    out: List[int] = []
    try:
        res = subprocess.run(["ps", "-eo", "pid=,command="],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return out
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, cmd = line.partition(" ")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid in exclude_pids:
            continue
        if pattern in cmd:
            out.append(pid)
    return out


def probe_health(url: str, timeout: float = 1.0) -> bool:
    """Best-effort HTTP health GET; True iff a 2xx comes back within `timeout`."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (trusted local URL)
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def derive_status(alive_count: int) -> str:
    return "down" if alive_count <= 0 else ("up" if alive_count == 1 else "duplicate")


def real_probe(spec: DaemonSpec, ident: str, *, lock_dir: Optional[str] = None,
               self_pids: Set[int] = frozenset(), do_health: bool = True) -> dict:
    """Probe ONE daemon by every signal it declares; union the live identities and
    derive up/down/duplicate. Returns the dict `build_liveness_map` consumes."""
    from singleton import singleton_lock_path  # sys.path shim seated at import
    flock_held: Optional[bool] = None
    flock_pid: Optional[int] = None
    process_pids: List[int] = []
    health_ok: Optional[bool] = None

    name = spec.flock_name(ident)
    if name:
        flock_held, flock_pid = probe_flock(singleton_lock_path(name, lock_dir))
    if spec.match:
        process_pids = scan_processes(spec.match, set(self_pids))
    if spec.health and do_health:
        health_ok = probe_health(spec.health)

    alive: Set = set()
    if flock_held:
        alive.add(flock_pid if flock_pid else f"flock:{spec.id}")
    alive.update(process_pids)
    if health_ok:
        alive.add(f"health:{spec.id}")

    return {
        "status": derive_status(len(alive)),
        "pids": sorted(p for p in alive if isinstance(p, int)),
        "flock_pid": flock_pid,
        "flock_held": flock_held,
        "process_pids": process_pids,
        "health_ok": health_ok,
    }


# --------------------------------------------------------------------------- #
# the staleness / duplicate STATE MACHINE (pure; injected clock)
# --------------------------------------------------------------------------- #
@dataclass
class _DState:
    down_since: Optional[float] = None
    last_up: Optional[float] = None
    canary_open: bool = False
    dup_open: bool = False


class LivenessTracker:
    """Turns a stream of per-daemon (status, now) observations into edge EVENTS:
    `canary_raised` (down continuously > threshold), `recovered` (a canaried
    daemon came back up), `duplicate_raised` / `duplicate_cleared`. Idempotent
    within a state (no repeat raises). The clock is passed IN (`now`, epoch
    seconds) so tests are deterministic."""

    def __init__(self, threshold_s: float = DEFAULT_THRESHOLD_S):
        self.threshold = float(threshold_s)
        self.state: Dict[str, _DState] = {}

    def observe(self, did: str, status: str, now: float) -> List[tuple]:
        st = self.state.setdefault(did, _DState())
        events: List[tuple] = []

        if status == "duplicate":
            st.down_since = None
            st.last_up = now
            if st.canary_open:
                st.canary_open = False
                events.append(("recovered", did))
            if not st.dup_open:
                st.dup_open = True
                events.append(("duplicate_raised", did))
            return events

        if status == "up":
            st.down_since = None
            st.last_up = now
            if st.dup_open:
                st.dup_open = False
                events.append(("duplicate_cleared", did))
            if st.canary_open:
                st.canary_open = False
                events.append(("recovered", did))
            return events

        # status == "down"
        if st.dup_open:                    # was duplicated, now fully absent
            st.dup_open = False
            events.append(("duplicate_cleared", did))
        if st.down_since is None:
            st.down_since = now
        if (now - st.down_since) > self.threshold and not st.canary_open:
            st.canary_open = True
            events.append(("canary_raised", did))
        return events


# --------------------------------------------------------------------------- #
# canary SINK — reuse the defect-registry (report/resolve) as the canary primitive
# --------------------------------------------------------------------------- #
def _canary_symptom(did: str, threshold_s: float) -> str:
    return (f"console daemon {did} stale — no live holder of its singleton for "
            f"> {threshold_s:.0f}s (liveness canary)")


def _duplicate_symptom(did: str) -> str:
    return (f"console daemon {did} running as DUPLICATE live instances — the "
            f"[SPEC:CXC-IC-SINGLETON] flock was bypassed")


class RecordingSink:
    """Test/dry-run sink: records (kind, did, detail) instead of writing the graph."""

    def __init__(self):
        self.events: List[tuple] = []

    def canary_raised(self, did, detail=""):    self.events.append(("canary_raised", did, detail))
    def recovered(self, did, detail=""):        self.events.append(("recovered", did, detail))
    def duplicate_raised(self, did, detail=""): self.events.append(("duplicate_raised", did, detail))
    def duplicate_cleared(self, did, detail=""):self.events.append(("duplicate_cleared", did, detail))


class DefectRegistrySink:
    """The real canary primitive: a stale/duplicate daemon becomes a DEFECT node
    via `composeio.defect_ops.report`; recovery ANNOTATES it verified via
    `resolve`. Symptom strings are STABLE (id-only, no volatile pids) so a
    recurrence dedups onto the same node and accumulates — exactly the registry's
    stable-symptom-identity contract. Best-effort: a registry write failure is
    logged, never fatal to the watch loop."""

    def __init__(self, graph_path: str, threshold_s: float, log=None):
        self.graph = graph_path
        self.threshold = threshold_s
        self._log = log or (lambda m: sys.stderr.write(m + "\n"))

    def _report(self, symptom, did, detail):
        try:
            from composeio.defect_ops import report
            report(self.graph, symptom=symptom, area=f"daemons/{did}", note=detail)
        except Exception as exc:  # noqa: BLE001 — registry write is best-effort
            self._log(f"[daemon_watch] defect report skipped: {exc!r}")

    def _resolve(self, symptom, detail):
        try:
            from composeio.defect_ops import resolve
            resolve(self.graph, symptom=symptom, status="verified", note=detail)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[daemon_watch] defect resolve skipped: {exc!r}")

    def canary_raised(self, did, detail=""):
        self._report(_canary_symptom(did, self.threshold), did, detail)

    def recovered(self, did, detail=""):
        self._resolve(_canary_symptom(did, self.threshold),
                      detail or "daemon liveness recovered")

    def duplicate_raised(self, did, detail=""):
        self._report(_duplicate_symptom(did), did, detail)

    def duplicate_cleared(self, did, detail=""):
        self._resolve(_duplicate_symptom(did),
                      detail or "duplicate cleared (single live instance)")


def _dispatch(sink, kind: str, did: str, detail: str) -> None:
    getattr(sink, kind, lambda *_a, **_k: None)(did, detail)


# --------------------------------------------------------------------------- #
# map build (pure; wires probe -> tracker -> sink -> payload)
# --------------------------------------------------------------------------- #
def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc) \
        .isoformat(timespec="seconds").replace("+00:00", "Z")


def build_liveness_map(registry: List[DaemonSpec],
                       probe_fn: Callable[[DaemonSpec], dict],
                       tracker: LivenessTracker,
                       sink,
                       now: float,
                       *, detail_of: Optional[Callable[[DaemonSpec, dict], str]] = None
                       ) -> tuple[dict, List[tuple]]:
    """Probe every daemon, advance the tracker, dispatch edge events to the sink,
    and assemble the `self/daemons` payload. Returns (payload, events). PURE given
    `probe_fn`/`now`/`sink` — the whole acceptance surface drives through here."""
    daemons: Dict[str, dict] = {}
    summary = {"up": 0, "down": 0, "duplicate": 0}
    all_events: List[tuple] = []

    for spec in registry:
        pr = probe_fn(spec)
        status = pr["status"]
        summary[status] += 1
        events = tracker.observe(spec.id, status, now)
        # SUPPRESS the two alarms a spec has declared meaningless for itself
        # (2026-08-08). The state machine still RUNS — state, last_seen and
        # down_for_s stay truthful in the map below, and `recovered`/`*_cleared`
        # still fire so nothing latches open — but the raise never reaches the
        # sink, so no defect node is filed for a condition that is normal.
        #   episodic    -> a daemon that is only up while something is happening
        #                  (intelligence_launcher) must not canary at rest.
        #   per_session -> N instances means N Claude sessions, not a fork
        #                  (decision_ops_mcp); an absolute count cannot tell them
        #                  apart, so do not claim it can.
        suppressed = set()
        if spec.episodic:
            suppressed.add("canary_raised")
        if spec.per_session:
            suppressed.add("duplicate_raised")
        for kind, did in events:
            if kind in suppressed:
                continue
            detail = detail_of(spec, pr) if detail_of else ""
            _dispatch(sink, kind, did, detail)
        all_events.extend(events)

        st = tracker.state[spec.id]
        daemons[spec.id] = {
            "status": status,
            "pids": pr.get("pids", []),
            "flock_pid": pr.get("flock_pid"),
            "last_seen": _iso(st.last_up),
            "down_since": _iso(st.down_since),
            "down_for_s": round(now - st.down_since, 1) if st.down_since else 0.0,
            "canary": st.canary_open,
            "note": spec.note,
        }

    payload = {
        "ts": _iso(now),
        "threshold_s": tracker.threshold,
        "summary": summary,
        "canaries": [d for d, v in daemons.items() if v["canary"]],
        "duplicates": [d for d, v in daemons.items() if v["status"] == "duplicate"],
        "daemons": daemons,
    }
    return payload, all_events


def _detail_of(spec: DaemonSpec, pr: dict) -> str:
    bits = [f"status={pr['status']}"]
    if pr.get("pids"):
        bits.append(f"pids={pr['pids']}")
    if pr.get("flock_pid"):
        bits.append(f"flock_pid={pr['flock_pid']}")
    if pr.get("health_ok") is not None:
        bits.append(f"health_ok={pr['health_ok']}")
    return "; ".join(bits)


# --------------------------------------------------------------------------- #
# transport (mirrors memory_monitor: shared MQTTTransport + BusMessage)
# --------------------------------------------------------------------------- #
def _parse_mqtt_url(url: str) -> tuple[str, int]:
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


# [SPEC:CXC-SOURCES-ON-BUS] slice 3 — the bus seam. This module used to build an
# `MQTTTransport` itself. That touched no paho and was STILL a substrate name: a
# module that imports ONE transport class is exactly as dark on a CAN-only
# device as one that hand-rolls a topic string. It now states a NAMESPACE and a
# SCOPE and lets `transport_registry` choose.
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "daemon-watch"               # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "daemon-watch"


def _make_bus(mqtt_url: str, bus=None):
    """The bus this watcher publishes on ([CXC-SOB-HANDLE]).

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): NOT needed, and not available. This
    watcher does not subscribe, and `self/daemons` is a STATE MAP — the widget
    renders the current liveness picture, never the event of re-measuring it. An
    unchanged picture would in any case republish an identical value, which
    `Namespace.publish_state` absorbs for in-process subscribers; that is the
    right answer here. (In practice it never happens: the payload carries a
    wall-clock `ts` and per-daemon `last_seen`, so every tick differs.)
    """
    return source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)


def _publish(bus, payload: dict, seq: int) -> None:
    """`seq` accepted and IGNORED — the bus owns sequencing
    ([DD-SOB-SEQ-SEMANTICS])."""
    bus.publish_state(NS, payload, source=SRC)


# --------------------------------------------------------------------------- #
# run loop
# --------------------------------------------------------------------------- #
def run(mqtt_url: str, *, ident: str, interval: float, threshold: float,
        graph_path: str, lock_dir: Optional[str] = None, file_registry: bool = True,
        storage_host: str = "mqtt-broker.local", _loops: Optional[int] = None,
        bus=None, stop=None) -> int:
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): this watcher is a daemon too; a second yields.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
        from singleton import acquire_singleton
    lock = acquire_singleton("daemon-watch", lock_dir)
    if lock is None:
        sys.stderr.write("[daemon_watch] another daemon-watch holds the singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    registry = default_registry(ident, storage_host)
    tracker = LivenessTracker(threshold)
    sink = (DefectRegistrySink(graph_path, threshold) if file_registry
            else RecordingSink())
    self_pids = {os.getpid()}

    b = _make_bus(mqtt_url, bus)
    source_bus.declare(b, NS, scope=Scope.GLOBAL, source=SRC,
                       label="daemon liveness")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[daemon_watch] watching {len(registry)} daemons -> {NS} "
                     f"every {interval}s; canary > {threshold:.0f}s; "
                     f"registry={'defect-graph' if file_registry else 'dry-run'}\n")
    sys.stderr.flush()

    def probe_fn(spec):
        return real_probe(spec, ident, lock_dir=lock_dir, self_pids=self_pids)

    def _tick() -> None:
        try:
            now = time.time()
            payload, events = build_liveness_map(
                registry, probe_fn, tracker, sink, now, detail_of=_detail_of)
            _publish(b, payload, 0)
            s = payload["summary"]
            extra = ""
            if payload["canaries"]:
                extra += f" CANARY={payload['canaries']}"
            if payload["duplicates"]:
                extra += f" DUPLICATE={payload['duplicates']}"
            sys.stderr.write(f"[daemon_watch] up={s['up']} down={s['down']} "
                             f"dup={s['duplicate']}{extra}\n")
            sys.stderr.flush()
        except Exception as exc:  # a bad poll must never kill the watcher
            sys.stderr.write(f"[daemon_watch] poll error (continuing): {exc!r}\n")
            sys.stderr.flush()

    if _loops is None:
        _tick()                        # publish NOW, then park — see slice 2
        source_bus.park(stop, poll_s=interval, on_tick=_tick)
    else:
        for _i in range(_loops):       # bounded, for tests only
            _tick()
            if _i + 1 < _loops:
                time.sleep(interval)
    return 0


# --------------------------------------------------------------------------- #
# --once / --list
# --------------------------------------------------------------------------- #
def probe_once(ident: str, threshold: float, *, lock_dir: Optional[str] = None,
               storage_host: str = "mqtt-broker.local",
               do_health: bool = True) -> dict:
    """READ-ONLY single probe of every daemon. Never files a defect (dry-run sink)
    — used by `--once` and the live-detection report."""
    registry = default_registry(ident, storage_host)
    tracker = LivenessTracker(threshold)
    sink = RecordingSink()
    self_pids = {os.getpid()}

    def probe_fn(spec):
        return real_probe(spec, ident, lock_dir=lock_dir, self_pids=self_pids,
                          do_health=do_health)

    payload, _events = build_liveness_map(
        registry, probe_fn, tracker, sink, time.time(), detail_of=_detail_of)
    return payload


# --------------------------------------------------------------------------- #
# selftest — PURE LOGIC ONLY, no broker, no network, no files
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    fails: List[str] = []
    total = [0]

    def check(cond, msg):
        total[0] += 1
        if not cond:
            fails.append(msg)

    # ---- (A) tracker: canary only AFTER >threshold, recovery clears ----
    tr = LivenessTracker(threshold_s=30.0)
    e = tr.observe("d", "down", now=1000.0)                 # first seen down
    check(e == [], f"(A1) first down -> no canary yet, got {e}")
    e = tr.observe("d", "down", now=1000.0 + 30.0)          # exactly 30s: NOT yet (> strict)
    check(e == [], f"(A2) down for exactly 30s -> no canary (strict >), got {e}")
    e = tr.observe("d", "down", now=1000.0 + 31.0)          # 31s: canary
    check(e == [("canary_raised", "d")], f"(A3) down >30s -> canary_raised, got {e}")
    e = tr.observe("d", "down", now=1000.0 + 45.0)          # still down: no repeat
    check(e == [], f"(A4) still down -> no repeat canary, got {e}")
    e = tr.observe("d", "up", now=1000.0 + 50.0)            # recovered
    check(e == [("recovered", "d")], f"(A5) back up -> recovered, got {e}")
    check(tr.state["d"].last_up == 1050.0, "(A6) last_up stamped on recovery")

    # ---- (B) a fresh flock (always up) -> never a canary ----
    tr2 = LivenessTracker(30.0)
    ev_any = []
    for t in range(0, 120, 5):
        ev_any += tr2.observe("fresh", "up", now=float(t))
    check(ev_any == [], f"(B) a continuously-up daemon never canaries, got {ev_any}")
    check(tr2.state["fresh"].canary_open is False, "(B) canary stays closed while up")

    # ---- (C) duplicate raised once, then cleared on return to single ----
    tr3 = LivenessTracker(30.0)
    e = tr3.observe("dup", "duplicate", now=1.0)
    check(e == [("duplicate_raised", "dup")], f"(C1) 2 live -> duplicate_raised, got {e}")
    e = tr3.observe("dup", "duplicate", now=2.0)
    check(e == [], f"(C2) still duplicate -> no repeat, got {e}")
    e = tr3.observe("dup", "up", now=3.0)
    check(e == [("duplicate_cleared", "dup")], f"(C3) back to single -> duplicate_cleared, got {e}")

    # ---- (C') duplicate that then goes fully DOWN clears the dup, then canaries ----
    tr3b = LivenessTracker(30.0)
    tr3b.observe("dd", "duplicate", now=0.0)
    e = tr3b.observe("dd", "down", now=1.0)
    check(e == [("duplicate_cleared", "dd")], f"(C'1) dup->down clears dup, got {e}")
    e = tr3b.observe("dd", "down", now=40.0)
    check(e == [("canary_raised", "dd")], f"(C'2) then >30s down -> canary, got {e}")

    # ---- (D) derive_status boundaries ----
    check(derive_status(0) == "down" and derive_status(1) == "up"
          and derive_status(2) == "duplicate" and derive_status(5) == "duplicate",
          "(D) derive_status 0/1/>=2 -> down/up/duplicate")

    # ---- (E) build_liveness_map end-to-end with a scripted probe + sink ----
    reg = [DaemonSpec("alpha", "alpha"), DaemonSpec("beta", "beta"),
           DaemonSpec("mcp", None, "mcp")]
    script = {"alpha": "up", "beta": "down", "mcp": "duplicate"}
    tr4 = LivenessTracker(30.0)
    sink = RecordingSink()

    def probe(spec):
        st = script[spec.id]
        pids = {"up": [111], "down": [], "duplicate": [201, 202, 203]}[st]
        return {"status": st, "pids": pids, "flock_pid": (pids[0] if pids else None),
                "flock_held": st != "down", "process_pids": pids, "health_ok": None}

    payload, events = build_liveness_map(reg, probe, tr4, sink, now=5000.0,
                                         detail_of=_detail_of)
    check(payload["summary"] == {"up": 1, "down": 1, "duplicate": 1},
          f"(E1) summary tallies, got {payload['summary']}")
    check(payload["duplicates"] == ["mcp"], f"(E2) mcp flagged duplicate, got {payload['duplicates']}")
    check(("duplicate_raised", "mcp") in events, "(E3) mcp duplicate event emitted")
    check(any(k == "duplicate_raised" and d == "mcp" for k, d, _ in sink.events),
          "(E4) sink received the duplicate_raised")
    check(payload["daemons"]["alpha"]["status"] == "up"
          and payload["daemons"]["alpha"]["pids"] == [111],
          "(E5) alpha up with its pid")
    # beta down but < threshold on first observe -> no canary yet
    check(payload["canaries"] == [], f"(E6) no canary on first-seen down, got {payload['canaries']}")
    check(json.loads(json.dumps(payload)) == payload, "(E7) payload JSON-round-trips")

    # advance 31s: beta now canaries through the SAME map builder + sink
    payload2, events2 = build_liveness_map(reg, probe, tr4, sink, now=5031.0,
                                           detail_of=_detail_of)
    check(payload2["canaries"] == ["beta"], f"(E8) beta canaries after 31s, got {payload2['canaries']}")
    check(any(k == "canary_raised" and d == "beta" for k, d, _ in sink.events),
          "(E9) sink received beta canary_raised with detail")
    check(payload2["daemons"]["beta"]["down_for_s"] == 31.0, "(E10) down_for_s reported")

    # ---- (F) probe_flock against a REAL held/absent lock (via the shared helper) ----
    # Hold the lock through the ONE shared primitive (acquire_singleton), not a raw
    # fcntl here — a watcher that exists to surface forks must not itself fork the lock.
    import tempfile
    from singleton import acquire_singleton, singleton_lock_path
    with tempfile.TemporaryDirectory() as d:
        held = singleton_lock_path("held", d)
        holder = acquire_singleton("held", d)   # a real live holder (records our pid)
        check(holder is not None, "(F0) the selftest must hold the lock")
        try:
            h, pid = probe_flock(held)
            check(h is True and pid == os.getpid(),
                  f"(F1) a held flock -> up + recorded pid, got {h}/{pid}")
        finally:
            holder.close()                      # release by CLOSE (no LOCK_UN call)
        h2, pid2 = probe_flock(held)            # file present, nobody holds it
        check(h2 is False, f"(F2) released flock -> down (stale file), got {h2}")
        h3, pid3 = probe_flock(os.path.join(d, "absent.lock"))
        check(h3 is False and pid3 is None, f"(F3) absent lock -> down, got {h3}/{pid3}")

    # ---- (G) real_probe unions flock + process into a status (mock lock_dir) ----
    with tempfile.TemporaryDirectory() as d2:
        # a daemon whose flock is held by THIS process' pid, no process match -> up
        holder2 = acquire_singleton("wt-inproc", d2)
        check(holder2 is not None, "(G0) the selftest must hold the lock")
        try:
            spec = DaemonSpec("wt", "wt-inproc", match="zzz-no-such-process-zzz")
            pr = real_probe(spec, "architect", lock_dir=d2, do_health=False)
            check(pr["status"] == "up" and pr["flock_pid"] == os.getpid(),
                  f"(G) in-process daemon (flock held, no proc) -> up, got {pr['status']}")
        finally:
            holder2.close()

    if fails:
        for f in fails:
            sys.stderr.write("FAIL " + f + "\n")
        return 1
    sys.stderr.write(f"PASS: daemon_watch selftest ({total[0]}/{total[0]})\n")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--id", default=DEFAULT_ID, help="the intelligence id (id-scoped flocks)")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S,
                    help="poll interval seconds (default 5)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_S,
                    help="canary a daemon down longer than this many seconds (default 30)")
    ap.add_argument("--graph", default=os.environ.get("DAEMON_WATCH_GRAPH", DEFAULT_GRAPH),
                    help="defect-graph.lts the canary reuses (report/resolve)")
    ap.add_argument("--storage-host", default=os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local"),
                    help="host for the bus_storage /health probe")
    ap.add_argument("--lock-dir", default=None, help="override the singleton lock dir")
    ap.add_argument("--no-registry", action="store_true",
                    help="do NOT file/resolve defects (dry-run sink) — probe + publish only")
    ap.add_argument("--once", action="store_true",
                    help="probe + print the map once (READ-ONLY; never files a defect)")
    ap.add_argument("--no-health", action="store_true",
                    help="skip the HTTP /health probes (offline / faster --once)")
    ap.add_argument("--list", action="store_true", help="print the daemon registry and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    if a.list:
        reg = default_registry(a.id, a.storage_host)
        for s in reg:
            print(f"{s.id:26s} flock={str(s.flock_name(a.id)):32s} "
                  f"match={str(s.match):26s} health={s.health or '-'}")
        print(f"\n{len(reg)} daemons watched.")
        return 0

    if a.once:
        payload = probe_once(a.id, a.threshold, lock_dir=a.lock_dir,
                             storage_host=a.storage_host, do_health=not a.no_health)
        print(json.dumps(payload, indent=2))
        return 0

    return run(a.mqtt, ident=a.id, interval=a.interval, threshold=a.threshold,
               graph_path=a.graph, lock_dir=a.lock_dir,
               file_registry=not a.no_registry, storage_host=a.storage_host,
               bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
