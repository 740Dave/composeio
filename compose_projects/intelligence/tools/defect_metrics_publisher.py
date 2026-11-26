#!/usr/bin/env python3
"""defect_metrics_publisher — the bus-native projector for the Defect Registry
roll-up ([SPEC:CIO-DEFECT-REGISTRY]).

Mirrors away_watch_publisher's transport + project-only shape (this one has NO
launch leg — the Defects widget is a read-only summary, nothing to relaunch).

ONE job, bus-native: read the retrospective SQLite defect_investigations
(`defects_by_root_cause()` + `list_defect_investigations()`) AND the defect GRAPH
(the fix-resistant leaders = defects whose attempt-count >= 2), and publish a
roll-up to ``self/<id>/defects`` (scope global -> topic
``composeio/global/self/<id>/defects``; default id ``architect`` ->
``self/architect/defects``) every ``--interval`` seconds — the same ``self/*``
mapping away_watch_publisher / self_publisher use. Payload:

    {"ts": <iso>,
     "total": <int>,                       # total investigation rows
     "by_root_cause": {"under-spec": n, "fork-divergence": n, ...},  # taxonomy histogram
     "structural": {"under_spec": n, "fork": n, "recurrence": n},    # flag tallies
     "recurrence_rate": <0..1>,            # recurrence rows / total
     "fix_resistant": [{"symptom", "attempts", "status"}, ...]}      # attempt-count >= 2

The ``defects_summary`` widget renders this verbatim.

The GRAPH read is best-effort: if the FactLattice engine isn't importable in this
process, the fix-resistant leaders fall back to a SQLite group-by-defect_id
(count >= 2) so the roll-up still publishes (the SQLite aggregate is the load-
bearing signal; the graph adds the human-readable symptom + live status).

Run:  python3 defect_metrics_publisher.py --mqtt mqtt://mqtt-broker.local:1883 [--interval 30]
      python3 defect_metrics_publisher.py --once      # compute + print; publish if reachable
      python3 defect_metrics_publisher.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "defect-metrics-publisher"   # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "defect-metrics-publisher"

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# `intelligence` is a top-level package name; since the tree moved under
# compose_projects/ (2026-08-17) that directory is what makes `import
# intelligence.retrospective` resolve.
sys.path.insert(0, str(REPO_ROOT / "compose_projects"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_GRAPH = str(REPO_ROOT / "defects" / "defect-graph.lts")
DEFAULT_ID = "architect"


def _db():
    import intelligence.retrospective.db as db
    return db


def _graph_path() -> str:
    return os.environ.get("DEFECT_OPS_GRAPH") or DEFAULT_GRAPH


def _fix_resistant_from_graph(graph_path: str) -> Optional[List[Dict[str, Any]]]:
    """Fix-resistant leaders from the GRAPH (defects with attempt-count >= 2),
    each {symptom, attempts, status}. Best-effort: returns None if defect_ops /
    the FactLattice engine can't be imported here (caller falls back to SQLite)."""
    try:
        from composeio.defect_ops import list_defects
    except Exception:
        return None
    try:
        defects = list_defects(graph_path)
    except Exception as exc:
        sys.stderr.write(f"[defect_metrics_publisher] graph read skipped: {exc!r}\n")
        return None
    out = []
    for d in defects:
        if int(d.get("attempt_count", 0)) >= 2:
            out.append({"symptom": d.get("symptom") or d.get("id"),
                        "attempts": int(d.get("attempt_count", 0)),
                        "status": d.get("lifecycle") or ""})
    out.sort(key=lambda x: -x["attempts"])
    return out


def _fix_resistant_from_sqlite(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fallback fix-resistant leaders when the graph is unreadable: group the
    analytics rows by defect_id, keep those with >= 2 attempts. Status is unknown
    from SQLite alone, so it reads 'recurred?' (the count itself is the signal)."""
    by: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        did = r.get("defect_id") or ""
        if not did:
            continue
        b = by.setdefault(did, {"symptom": r.get("symptom") or did,
                                "attempts": 0, "status": ""})
        b["attempts"] += 1
        if r.get("recurrence"):
            b["status"] = "recurred"
    out = [v for v in by.values() if v["attempts"] >= 2]
    out.sort(key=lambda x: -x["attempts"])
    return out


# ---------------------------------------------------------------------------
# graph -> SQLite reconcile (the ONE-TIME backfill for attempts filed BEFORE the
# CLI de-fork [DR-CLI] landed, so those were written to the graph only). Idempotent
# COUNT top-up: for each defect the graph knows N attempts and SQLite holds M rows
# (M<=N going forward, since every new attempt now writes both); insert only the
# missing tail attempts[M:]. Re-running is a no-op; it never double-counts and it
# composes with live investigator writes. Best-effort — never fatal to the loop.
# ---------------------------------------------------------------------------
_TAXONOMY = ("under-spec", "oversight", "fork-divergence", "spec-drift")


def _class_from_hypothesis(hyp: str) -> str:
    """Bucket a graph attempt's `root-cause-hypothesis` (which the agent-def writes
    as the taxonomy, sometimes with trailing prose — 'under-spec — the two…') into
    the SQLite class by LEADING-TOKEN match; unrecognized -> 'other'."""
    s = str(hyp or "").strip().lower()
    if s in ("implementation-oversight",):
        return "oversight"
    for tok in _TAXONOMY:
        if s == tok or s.startswith(tok + " ") or s.startswith(tok + "—") \
                or s.startswith(tok + " —"):
            return tok
    return "other"


def reconcile_graph_to_sqlite(graph_path: Optional[str] = None) -> int:
    """Top up `defect_investigations` from the defect GRAPH so pre-de-fork attempts
    (graph-only) are reflected in the aggregate the widget reads. Returns the number
    of rows inserted (0 when already reconciled). Idempotent."""
    from composeio.defect_ops import list_defects, query
    db = _db()
    gp = graph_path or _graph_path()
    rows = db.list_defect_investigations()
    have: Dict[str, int] = {}
    for r in rows:
        did = r.get("defect_id") or ""
        if did:
            have[did] = have.get(did, 0) + 1
    inserted = 0
    for d in list_defects(gp):
        did = d["id"]
        q = query(gp, did)
        attempts = q.get("attempts") or []
        for att in attempts[have.get(did, 0):]:
            cls = _class_from_hypothesis(att.get("root_cause_hypothesis"))
            outcome = str(att.get("outcome") or "").strip().lower()
            db.record_defect_investigation(
                symptom=q["defect"].get("symptom") or did,
                area=q["defect"].get("area") or "",
                root_cause_class=cls,
                recurrence=(outcome == "recurred"),
                under_spec=(cls == "under-spec"),
                fork=(cls == "fork-divergence"),
                resolution=att.get("what_changed") or "",
                branch=att.get("branch") or "",
                defect_id=did)
            inserted += 1
    return inserted


def build_rollup(graph_path: Optional[str] = None) -> Dict[str, Any]:
    """The self/<id>/defects projection — the roll-up the widget renders."""
    db = _db()
    rows = db.list_defect_investigations()
    hist = db.defects_by_root_cause()
    total = len(rows)
    recurrence = sum(1 for r in rows if r.get("recurrence"))
    under_spec = sum(1 for r in rows if r.get("under_spec"))
    fork = sum(1 for r in rows if r.get("fork"))
    recurrence_rate = round(recurrence / total, 3) if total else 0.0

    leaders = _fix_resistant_from_graph(graph_path or _graph_path())
    if leaders is None:
        leaders = _fix_resistant_from_sqlite(rows)

    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": total,
        "by_root_cause": hist,
        "structural": {"under_spec": under_spec, "fork": fork,
                       "recurrence": recurrence},
        "recurrence_rate": recurrence_rate,
        "fix_resistant": leaders,
    }


# ---------------------------------------------------------------------------
# transport (mirrors away_watch_publisher._parse_mqtt_url + publish loop)
# ---------------------------------------------------------------------------
def _parse_mqtt_url(url: str):
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


def run(mqtt_url: str, interval: float, intelligence_id: str,
        _loops: Optional[int] = None, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 3 — publish-only, session-independent.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): does not subscribe. An unchanged
    defect registry republishes an identical roll-up; in-process subscribers
    dedup it, the wire does not.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global; a second instance yields.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("defect-metrics-publisher")
    if lock is None:
        sys.stderr.write("[defect_metrics_publisher] another instance holds the "
                         "singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    # DECLARE BEFORE ANY STARTUP WORK ([CXC-SOB-DECLARE] / SOB-C3).
    #
    # The manifest is the mechanism by which a reader of the boot log learns what
    # this source will publish, so it must be emitted as soon as there is a bus to
    # declare against — NOT after the source finishes whatever slow work it does on
    # the way up. The reconcile below reads the defect graph and writes SQLite; its
    # duration varies with graph size and disk state, and while it ran this source
    # was live, unannounced, and indistinguishable from one that had hung.
    #
    # That ordering also made `test_converted_source_blocks_and_then_stops
    # [defect_metrics_publisher]` a COIN FLIP — 5 failures in 6 identical isolated
    # runs — because the test asserts the manifest inside a fixed window. The
    # flakiness was the true symptom: a gate that reports pass/fail by luck certifies
    # nothing, and this one blocks every landing onto main.
    #
    # Declaring first is also simply correct: the manifest states an INTENT to
    # publish, which does not depend on the self-heal succeeding, or running at all.
    ns = f"self/{intelligence_id}/defects"
    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    source_bus.declare(b, ns, scope=Scope.GLOBAL, source=SRC,
                       label="defect roll-up")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[defect_metrics_publisher] publishing {ns}\n")

    # ONE-TIME self-heal: reflect graph-only attempts (filed before the CLI
    # de-fork [DR-CLI]) into the aggregate the widget reads. Idempotent; best-
    # effort — a reconcile failure must never stop the publisher coming up.
    try:
        n = reconcile_graph_to_sqlite()
        if n:
            sys.stderr.write(f"[defect_metrics_publisher] reconciled {n} graph "
                             f"attempt(s) into defect_investigations\n")
    except Exception as exc:  # noqa: BLE001 — reconcile is best-effort
        sys.stderr.write(f"[defect_metrics_publisher] reconcile skipped: {exc!r}\n")

    def _tick() -> None:
        try:
            payload = build_rollup()
            b.publish_state(ns, payload, source=SRC)
            sys.stderr.write(f"[defect_metrics_publisher] published "
                             f"{payload['total']} investigation(s), "
                             f"{len(payload['fix_resistant'])} fix-resistant\n")
        except Exception as exc:
            sys.stderr.write(f"[defect_metrics_publisher] read error "
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

    with tempfile.TemporaryDirectory() as d:
        os.environ["COMPOSEIO_RETRO_DB"] = os.path.join(d, "m.db")
        try:
            db = _db()
            # empty store -> a well-formed, zero roll-up (never a crash)
            empty = build_rollup(graph_path=os.path.join(d, "absent.lts"))
            check(empty["total"] == 0 and empty["recurrence_rate"] == 0.0
                  and empty["fix_resistant"] == [] and empty["by_root_cause"] == {},
                  "(1) empty store -> zero roll-up")
            check(json.dumps(empty), "(2) roll-up is JSON-serializable")

            # two attempts on ONE defect (a recurrence) + one on another
            db.record_defect_investigation(
                symptom="hb merge", root_cause_class="oversight", recurrence=True,
                under_spec=True, defect_id="DEFECT-HB-1", branch="defect/hb-1")
            db.record_defect_investigation(
                symptom="hb merge", root_cause_class="under-spec", under_spec=True,
                test_added=True, defect_id="DEFECT-HB-1", branch="defect/hb-2")
            db.record_defect_investigation(
                symptom="flicker", root_cause_class="fork-divergence", fork=True,
                defect_id="DEFECT-FLICK-2", branch="defect/flick")

            roll = build_rollup(graph_path=os.path.join(d, "absent.lts"))
            check(roll["total"] == 3, "(3) total counts every investigation")
            check(roll["by_root_cause"].get("under-spec") == 1
                  and roll["by_root_cause"].get("oversight") == 1
                  and roll["by_root_cause"].get("fork-divergence") == 1,
                  "(4) by_root_cause taxonomy histogram")
            check(roll["structural"]["under_spec"] == 2
                  and roll["structural"]["fork"] == 1
                  and roll["structural"]["recurrence"] == 1,
                  "(5) structural flag tallies")
            check(roll["recurrence_rate"] == round(1 / 3, 3),
                  "(6) recurrence_rate = recurrence rows / total")
            # fix-resistant (graph absent -> SQLite fallback groups by defect_id)
            fr = {x["symptom"]: x for x in roll["fix_resistant"]}
            check(list(fr.keys()) == ["hb merge"]
                  and fr["hb merge"]["attempts"] == 2
                  and fr["hb merge"]["status"] == "recurred",
                  "(7) fix-resistant leaders = defect_id with >= 2 attempts (SQLite fallback)")
        finally:
            os.environ.pop("COMPOSEIO_RETRO_DB", None)

    # ---- reconcile (the graph-only backfill, [DR-CLI]) ----------------------
    with tempfile.TemporaryDirectory() as d2:
        os.environ["COMPOSEIO_RETRO_DB"] = os.path.join(d2, "m2.db")
        try:
            # simulate PRE-de-fork state: attempts written to the GRAPH only (the
            # core writer never touched SQLite) — reconcile must top them up.
            from composeio.defect_ops.core import attempt as _graph_only
            g = os.path.join(d2, "g.lts")
            with open(g, "w") as fh:
                fh.write("---\ndomain: defect\n---\n# Fixture [SYSTEM:DEFECT-CORPUS]\n")
            _graph_only(g, symptom="recon defect one",
                        root_cause_hypothesis="under-spec — trailing prose here",
                        what_changed="x", outcome="recurred", branch="defect/r1")
            _graph_only(g, symptom="recon defect one",
                        root_cause_hypothesis="under-spec", what_changed="y",
                        outcome="held", branch="defect/r2")
            db2 = _db()
            check(db2.list_defect_investigations() == [],
                  "(8) graph-only attempts left SQLite empty (the fork's footprint)")
            n1 = reconcile_graph_to_sqlite(graph_path=g)
            check(n1 == 2, f"(9) reconcile backfills every graph attempt (got {n1})")
            roll2 = build_rollup(graph_path=g)
            check(roll2["total"] == 2 and roll2["structural"]["recurrence"] == 1
                  and roll2["by_root_cause"].get("under-spec") == 2,
                  "(10) reconciled roll-up reflects the graph (leading-token class)")
            n2 = reconcile_graph_to_sqlite(graph_path=g)
            check(n2 == 0 and build_rollup(graph_path=g)["total"] == 2,
                  "(11) reconcile is idempotent (no double-count on re-run)")
        finally:
            os.environ.pop("COMPOSEIO_RETRO_DB", None)

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: defect_metrics_publisher selftest ({total[0] - len(fails)}/{total[0]})")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description="Project the Defect Registry roll-up "
                                 "to self/<id>/defects.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--id", default=DEFAULT_ID,
                    help="the intelligence id (namespace scope); default architect")
    ap.add_argument("--once", action="store_true",
                    help="build + print the roll-up once (publish if reachable)")
    ap.add_argument("--backfill", action="store_true",
                    help="reconcile graph-only attempts into defect_investigations "
                         "then print the roll-up (idempotent); no publish")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if a.backfill:
        n = reconcile_graph_to_sqlite()
        print(json.dumps({"reconciled": n, "rollup": build_rollup()}, indent=2))
        return 0
    if a.once:
        print(json.dumps(build_rollup(), indent=2))
        return 0
    return run(a.mqtt, a.interval, a.id, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
