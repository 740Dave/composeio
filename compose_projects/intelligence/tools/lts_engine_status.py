#!/usr/bin/env python3
"""lts_engine_status — the IN-PROCESS lts-engine status source for the console
launcher rail ([SPEC:CXC-DVL] [CXC-DVL-STATUS-SOURCE]).

Periodically QUERIES the lts-engine READ-ONLY (corpus list + per-corpus
freshness verdict + node/edge counts, [SPEC:FL-FRESHNESS]) and PUBLISHES a
status object to ``self/lts_engine/status`` under the existing state-envelope
``val`` — so the rail widget subscribes it exactly like any other ``self/*``
state topic.  It NEVER writes to the engine or a corpus.

FAIL-CLOSED ([SPEC:FL-FRESHNESS] never-serve-stale, surfaced): a crashing /
absent / unconfigured engine is not a silent gap — the source STILL publishes,
with ``reachable:false, fresh:false`` and zeroed metrics.  ``reachable:false``
(no answer) and ``reachable:true, fresh:false`` (answered but serving a stale
index) are DISTINCT states the rail renders differently.

The engine is a SEPARATE library (the renamed ``factlattice_engine``,
[SPEC:LTS-ENGINE-CONSOLIDATION]).  This tool RESOLVES + READS it through the SAME
in-process path decision_ops uses ([LEC-CONSUMER-WAIST]): the shared engine-path
seat ``composeio.decision_ops._enginepath`` (env ``FACTLATTICE_ENGINE_ROOT``,
default ``~/factlattice-internal-dev``) plus the engine's PUBLISHED live API
(``store.stats``/``store.freshness``) with the corpus set from the shared CORPUS
seat ``composeio.decision_ops._corpusseat.build_engine_store`` ([LEC-CORPUS-SEAT] —
never a bare ``config.build_store()``, whose set comes from the deployment-private
``FACTLATTICE_CORPUS_CONFIG`` this process does not carry).  It NEVER forks a
private top-level import or a hardcoded root — that fork (a dead ``~/factlattice``
root + a retired ``MagmaTools`` import) is exactly how this rail was born
permanently "unreachable".  Any probe failure still degrades to the
reachable:false payload rather than taking the source thread down.

Run:  python3 lts_engine_status.py --mqtt mqtt://mqtt-broker.local:1883 --interval 10
      python3 lts_engine_status.py --once        # print one status, no bus
      python3 lts_engine_status.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# [SPEC:CXC-SOURCES-ON-BUS] slice 2 — the ONE bus seam, see composeio/source_bus.py.
# This module names a NAMESPACE and a SCOPE and nothing else: no client, no
# topic string, no transport class. Whether that reaches the broker over MQTT,
# rides a CAN frame, or goes nowhere at all is `transport_registry`'s business.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                                  # noqa: E402
from composeio.namespace import Scope                             # noqa: E402

NS = "self/lts_engine/status"
#: the `src` stamped on every envelope. FROZEN ([CXC-SOB-CONTRACT-FROZEN]) — the
#: chat/thread widgets key voices off `src`, so a change here is a wire change.
SRC = "lts_engine_status"
#: the label the singleton, the log lines and the self-created bus share.
LABEL = "lts-engine-status"
DEFAULT_INTERVAL = 10.0
# Empty => resolve the engine via the SHARED seat decision_ops uses (env
# FACTLATTICE_ENGINE_ROOT, default ~/factlattice-internal-dev); an explicit
# --factlattice still overrides.  The old FACTLATTICE_ROOT / ~/factlattice default
# was the pre-consolidation root, DELETED 2026-07-23 — a hardcoded dead root is one
# of the three rename breakages [LEC-CONSUMER-WAIST] forbids a consumer to reintroduce.
DEFAULT_FACTLATTICE = ""
# No hardcoded corpus list: the engine's OWN served set (store.corpus_ids()) is the
# source of truth, so the rail reports whatever the engine actually serves.  --corpus
# (repeatable) can still narrow it to a subset for a diagnostic.


# ---------------------------------------------------------------------------
# Pure status assembly (fully testable, no engine) ([CXC-DVL-STATUS-SOURCE])
# ---------------------------------------------------------------------------

def _iso(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S",
                         time.gmtime(ts if ts is not None else time.time()))


def build_status(corpora: List[Dict[str, Any]],
                 ts: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the REACHABLE status payload from a list of per-corpus dicts
    (each ``{corpus, fresh, nodes, edges, last_refresh}``).  Totals are the
    sum over corpora; the aggregate ``fresh`` is the AND of every corpus's
    freshness (true iff EVERY corpus is fresh).  ``last_refresh`` is the most
    recent per-corpus refresh.  Every key of the [CXC-DVL-STATUS-SOURCE] shape
    is present and JSON-serializable."""
    ts = ts or _iso()
    items: List[Dict[str, Any]] = []
    total_nodes = 0
    total_edges = 0
    all_fresh = True
    last_refresh: Optional[str] = None
    for c in corpora:
        nodes = int(c.get("nodes", 0) or 0)
        edges = int(c.get("edges", 0) or 0)
        fresh = bool(c.get("fresh", False))
        lr = c.get("last_refresh")
        items.append({"corpus": str(c.get("corpus", "")),
                      "fresh": fresh, "nodes": nodes, "edges": edges,
                      "last_refresh": lr})
        total_nodes += nodes
        total_edges += edges
        all_fresh = all_fresh and fresh
        if lr is not None and (last_refresh is None or str(lr) > str(last_refresh)):
            last_refresh = lr
    return {
        "ts": ts,
        "reachable": True,
        "fresh": bool(items) and all_fresh,
        "corpus_count": len(items),
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "last_refresh": last_refresh,
        "corpora": items,
    }


def unreachable_status(ts: Optional[str] = None) -> Dict[str, Any]:
    """The FAIL-CLOSED payload for an engine that could not be queried
    ([CXC-DVL-STATUS-SOURCE] UNREACHABLE): every key present, metrics zeroed,
    ``corpora`` empty, ``reachable`` and ``fresh`` both false, fresh ``ts``."""
    return {
        "ts": ts or _iso(),
        "reachable": False,
        "fresh": False,
        "corpus_count": 0,
        "total_nodes": 0,
        "total_edges": 0,
        "last_refresh": None,
        "corpora": [],
    }


# ---------------------------------------------------------------------------
# The engine probe (best-effort, read-only) ([SPEC:FL-FRESHNESS])
# ---------------------------------------------------------------------------

def _seat_engine(root: str = "") -> None:
    """Put the lts-engine library on ``sys.path`` via the ONE shared resolver
    decision_ops uses (de-fork, [LEC-CONSUMER-WAIST] / [DX-NARROW-WAIST]).  An
    explicit *root* override (from ``--factlattice``) wins; otherwise the seat
    honors ``FACTLATTICE_ENGINE_ROOT`` (default ``~/factlattice-internal-dev``).

    Imported LAZILY (inside the probe) so an absent engine/resolver is a
    query-time failure (=> reachable:false), never an import-time crash of the
    source module."""
    if root and root not in sys.path:
        sys.path.insert(0, root)
    from composeio.decision_ops._enginepath import ensure_engine_on_path
    ensure_engine_on_path()


class FactlatticeProbe:
    """READ-ONLY probe of the lts-engine over its served corpora, routed through
    the SAME in-process path decision_ops uses ([LEC-CONSUMER-WAIST]): the shared
    engine-path seat (``_seat_engine`` -> ``composeio.decision_ops._enginepath``)
    plus the shared CORPUS seat (``_corpusseat.build_engine_store``,
    [LEC-CORPUS-SEAT]) and the engine's PUBLISHED live API
    (``store.stats``/``store.freshness``).  Returns a per-corpus
    ``[{corpus, fresh, nodes, edges, last_refresh}]`` list, or RAISES on any
    failure (seat/build/query) so the source publishes the reachable:false
    payload.  NEVER writes to the engine or a corpus.

    ``corpora`` empty/None => the engine's OWN served set (``store.corpus_ids()``),
    so the rail reports whatever the engine actually serves rather than a
    hardcoded guess; a non-empty ``corpora`` narrows to that subset."""

    def __init__(self, factlattice_root: str, corpora: Optional[List[str]]) -> None:
        self.root = factlattice_root
        self.corpora = corpora

    def __call__(self) -> List[Dict[str, Any]]:
        _seat_engine(self.root)
        # Lazy import: an absent engine is a query-time failure (=> reachable:false),
        # not an import-time crash of the source module.
        # [LEC-CORPUS-SEAT] the CORPUS seat, not a bare `config.build_store()`. A bare
        # build_store() derives its set from FACTLATTICE_CORPUS_CONFIG — the GITIGNORED
        # private deployment overlay, which transport.sh sets ONLY in the MCP
        # subprocesses' .mcp.json env. THIS source runs in the console runtime, which
        # does not carry it, so build_store() served ZERO corpora and the rail reported
        # "engine unreachable" against an engine that was up. The seat unions the
        # deployment-served set with the corpora derivable from the repo root alone.
        from composeio.decision_ops._corpusseat import build_engine_store  # noqa: E402
        store = build_engine_store(self.root)
        ids = list(self.corpora) if self.corpora else list(store.corpus_ids())
        out: List[Dict[str, Any]] = []
        for corpus in ids:
            part = store.get_partition(corpus)
            if part is None:                # requested a corpus the engine doesn't serve
                continue
            out.append({
                "corpus": corpus,
                "fresh": _probe_fresh(store, corpus),
                "nodes": _probe_count(store, corpus, "total_nodes"),
                "edges": _probe_count(store, corpus, "total_edges"),
                "last_refresh": _probe_last_refresh(part),
            })
        if not out:
            raise RuntimeError(
                f"lts-engine reachable but no served corpora matched {ids!r}")
        return out


def _probe_count(store: Any, corpus: str, key: str) -> int:
    """Node/edge count off the engine's published per-corpus ``stats(corpus)``
    dict (``total_nodes`` / ``total_edges``).  Absent/non-int => 0."""
    stats = store.stats(corpus) or {}
    v = stats.get(key, 0)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _probe_fresh(store: Any, corpus: str) -> bool:
    """Freshness verdict off the engine's published ``freshness(corpus)``
    ([SPEC:FL-FRESHNESS]).  A probe that cannot determine it conservatively
    reports False (never a fabricated fresh)."""
    fr = store.freshness(corpus)
    return bool(getattr(fr, "fresh", False))


def _probe_last_refresh(part: Any) -> Optional[str]:
    """Best-effort last-refresh: the mtime of the corpus source path the engine
    built this partition from — the real "when did the underlying data last
    change" signal.  None if unavailable (never a fabricated timestamp)."""
    sp = getattr(part, "source_path", None)
    try:
        if sp and os.path.exists(sp):
            return _iso(os.path.getmtime(sp))
    except OSError:
        pass
    return None


def sample(probe: Callable[[], List[Dict[str, Any]]]) -> Dict[str, Any]:
    """One status sample: run *probe* and assemble the reachable payload; ANY
    exception degrades to the fail-closed reachable:false payload.  Never
    raises — a bad read never kills the source thread ([CXC-DVL-STATUS-SOURCE])."""
    try:
        corpora = probe()
        return build_status(corpora)
    except Exception as exc:                # noqa: BLE001 — isolation boundary
        sys.stderr.write(f"[lts_engine_status] engine unreachable: {exc!r}\n")
        return unreachable_status()


# ---------------------------------------------------------------------------
# The bus leg ([SPEC:CXC-SOURCES-ON-BUS] slice 2 — the REFERENCE conversion)
# ---------------------------------------------------------------------------
# This source was chosen to go FIRST because it is the least dangerous thing in
# the console to get wrong: ONE namespace, a 10 s cadence, publish-only, no
# subscriber, and nothing catastrophic if it blips. It is also the qos 0 -> 1
# case ([CXC-SOB-WIRE-DELTAS] delta 3), so the one wire delta that is not pure
# bookkeeping gets exercised on the least important namespace on the bus.
#
# REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): this source does not subscribe, and its
# payload carries a fresh `ts` on every sample, so consecutive values always
# differ and `Namespace.publish_state`'s new != old dedup can never absorb a
# tick. Stated explicitly because it is exactly the property a future edit that
# strips the timestamp would break, silently.

def run(mqtt_url: str, interval: float = DEFAULT_INTERVAL,
        factlattice_root: str = DEFAULT_FACTLATTICE,
        corpora: Optional[List[str]] = None,
        *, bus=None, stop=None) -> int:
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]) FIRST, before the bus handle: a
    # yielding instance must never open a transport it is about to abandon.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton(LABEL)
    if lock is None:
        sys.stderr.write("[lts_engine_status] another instance holds the singleton; yielding\n")
        # THE ONE LEGITIMATE EARLY RETURN ([CXC-SOB-RUN-BLOCKS] constraint): the
        # supervisor's reclaim loop re-invokes us so we come up the moment the
        # incumbent releases the flock. Every OTHER path below must block.
        return 0

    probe = FactlatticeProbe(factlattice_root, corpora)
    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    source_bus.declare(b, NS, scope=Scope.GLOBAL, source=SRC,
                       label="lts-engine status")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[lts_engine_status] publishing {NS} every {interval}s\n")

    def _tick() -> None:
        # A bad read never kills the source thread: `sample()` degrades to the
        # fail-closed reachable:false payload rather than raising.
        b.publish_state(NS, sample(probe), source=SRC)

    # Publish IMMEDIATELY, then park. `park` waits before its first tick, so
    # parking without this would leave the launcher rail blank for a whole
    # interval on every boot — a visible regression from a scheduling detail.
    _tick()
    source_bus.park(stop, poll_s=max(1.0, float(interval)), on_tick=_tick)
    return 0


# ---------------------------------------------------------------------------
# selftest — no broker, no engine; the payload shape + fail-closed contract
# ---------------------------------------------------------------------------
def _selftest() -> int:
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    KEYS = {"ts", "reachable", "fresh", "corpus_count", "total_nodes",
            "total_edges", "last_refresh", "corpora"}

    up = build_status([
        {"corpus": "a", "fresh": True, "nodes": 312, "edges": 540,
         "last_refresh": "2026-07-24T10:00:00"},
        {"corpus": "b", "fresh": True, "nodes": 700, "edges": 1400,
         "last_refresh": "2026-07-25T09:00:00"},
        {"corpus": "c", "fresh": True, "nodes": 272, "edges": 591,
         "last_refresh": "2026-07-25T08:00:00"},
    ])
    check(set(up) == KEYS, f"(1) up payload keys: {set(up) ^ KEYS}")
    check(up["reachable"] is True and up["fresh"] is True, "(2) up+fresh")
    check(up["corpus_count"] == 3, "(3) corpus_count")
    check(up["total_nodes"] == 312 + 700 + 272, "(4) total_nodes = sum")
    check(up["total_edges"] == 540 + 1400 + 591, "(5) total_edges = sum")
    check(up["last_refresh"] == "2026-07-25T09:00:00", "(6) last_refresh = max")
    check(len(up["corpora"]) == 3, "(7) per-corpus detail")
    check(json.loads(json.dumps(up)) == up, "(8) json-serializable")

    # fresh is the AND of per-corpus fresh: one stale corpus => aggregate stale.
    mixed = build_status([
        {"corpus": "a", "fresh": True, "nodes": 1, "edges": 2},
        {"corpus": "b", "fresh": False, "nodes": 3, "edges": 4},
    ])
    check(mixed["reachable"] is True and mixed["fresh"] is False,
          "(9) reachable but stale (fresh = AND)")
    check(mixed["total_nodes"] == 4 and mixed["total_edges"] == 6,
          "(10) totals with a stale corpus")

    # DOWN payload: reachable/fresh false, zeroed metrics, empty corpora.
    down = unreachable_status()
    check(set(down) == KEYS, "(11) down payload has every key")
    check(down["reachable"] is False and down["fresh"] is False,
          "(12) down not reachable / not fresh")
    check(down["corpus_count"] == 0 and down["total_nodes"] == 0
          and down["total_edges"] == 0 and down["corpora"] == [],
          "(13) down metrics zeroed + corpora empty")
    check(down["last_refresh"] is None and isinstance(down["ts"], str),
          "(14) down last_refresh None + fresh ts")

    # sample() never raises: a crashing probe yields the down payload.
    def _boom():
        raise RuntimeError("engine down")

    s = sample(_boom)
    check(s["reachable"] is False and set(s) == KEYS,
          "(15) crashing probe -> fail-closed down payload (no raise)")

    def _ok():
        return [{"corpus": "x", "fresh": True, "nodes": 5, "edges": 6,
                 "last_refresh": "2026-07-25T00:00:00"}]

    s2 = sample(_ok)
    check(s2["reachable"] is True and s2["corpus_count"] == 1
          and s2["total_nodes"] == 5, "(16) good probe -> up payload")

    # empty-corpus reachable is NOT fresh (no fabricated fresh on empty).
    check(build_status([])["fresh"] is False, "(17) empty reachable not fresh")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: lts_engine_status selftest (17/17)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    """argv stays FIRST and POSITIONAL; `bus`/`stop` are keyword-only additions
    ([CXC-SOB-HANDLE]). `DataSourceSupervisor` binds
    `functools.partial(main, argv, ...)` and `__main__` below still passes argv
    alone, so standalone runnability is unchanged ([CXC-SOB-STANDALONE])."""
    ap = argparse.ArgumentParser(
        description="Publish lts-engine status/freshness/metrics to "
                    "self/lts_engine/status for the console launcher rail.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--factlattice", default=DEFAULT_FACTLATTICE,
                    help="override the engine library root; empty (default) "
                         "resolves via the shared FACTLATTICE_ENGINE_ROOT seat")
    ap.add_argument("--corpus", action="append", dest="corpora",
                    help="narrow to a corpus the engine serves (repeatable); "
                         "default = every corpus the engine serves")
    ap.add_argument("--once", action="store_true",
                    help="print one status object and exit (no bus)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.once:
        probe = FactlatticeProbe(a.factlattice, a.corpora)
        print(json.dumps(sample(probe), indent=2))
        return 0
    return run(a.mqtt, a.interval, a.factlattice, a.corpora,
               bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
