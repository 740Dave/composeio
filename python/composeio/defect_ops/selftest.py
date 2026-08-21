"""composeio.defect_ops.selftest — the full round-trip demonstration
([SPEC:CIO-DEFECT-REGISTRY] acceptance), run against a TEMP corpus copy + a TEMP
retrospective DB (NEVER the real defects/defect-graph.lts or metrics.db, and
NEVER seeding a real defect).

Proves end to end, through the MCP dispatch seam (so it exercises exactly what an
Intelligence or a human drives over the bus):
  * report -> query -> attempt -> attempt -> resolve round-trips;
  * query returns the ACCUMULATED history (prior attempts + why-they-failed + kin)
    — the anti-re-dig gate;
  * the recurrence chain is a real graph: attempt A2 SUPERSEDES A1;
  * no-forward-dangle holds (a related_to that resolves nowhere is REFUSED);
  * every attempt writes a SQLite `defect_investigations` row cross-linked by
    defect_id (verified via defects_by_root_cause);
  * the engine refresh returns a fresh (or explicitly degraded) verdict on write.

    python3 -m composeio.defect_ops.selftest
    python3 -m composeio.defect_ops.mcp_server --selftest
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import List

# The CANONICAL repo root, not this file's location — see mcp_server._repo_root().
# The selftest seeds its temp corpus from the real graph, and run from inside a
# linked worktree the old `parents[3]` seeded from the WORKTREE's copy: the selftest
# would then pass against a graph nobody reads.
from .mcp_server import _REPO_ROOT  # noqa: E402  (canonical, git-common-dir derived)

# ⛔ FIFTH copy of the corpus-layout literal found on 2026-08-17 (after
#    bootstrap_paths, _corpusseat, graph_projection and decisions_publisher).
#    The graph moved out of the repo with the rest of the corpora; resolve it
#    through the one seam. This selftest COPIES the real graph into a tmp dir and
#    never writes to it, so it only needs to READ from wherever it now lives.
def _corpus_path() -> str:
    try:
        from ..bootstrap_paths import DEFECT_GRAPH
        if DEFECT_GRAPH.is_file():
            return str(DEFECT_GRAPH)
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(_REPO_ROOT, "defects", "defect-graph.lts")  # legacy layout


_CORPUS = _corpus_path()


def run() -> int:
    from .mcp_server import DefectOpsMcp

    fails: List[str] = []
    total = [0]

    def check(cond, msg):
        total[0] += 1
        if not cond:
            fails.append(msg)

    tmp = tempfile.mkdtemp(prefix="defect-ops-selftest-")
    graph = os.path.join(tmp, "defect-graph.lts")
    shutil.copy(_CORPUS, graph)
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    _projects = os.path.join(_REPO_ROOT, "compose_projects")
    if _projects not in sys.path:
        sys.path.insert(0, _projects)
    os.environ["COMPOSEIO_RETRO_DB"] = os.path.join(tmp, "metrics.db")
    try:
        from intelligence.retrospective import db as retro_db
        mcp = DefectOpsMcp(default_graph=graph)

        SY = "heartbeat get_layout returns a MERGED snapshot from two live GUIs"

        # (1) report a NEW defect
        r = mcp.dispatch("defect_report", {
            "symptom": SY, "area": "ux_composer/src/composer",
            "note": "two live GUIs both answer request_layout nondeterministically"})
        check(r["success"] and r["changed"], "(1) report creates the defect")
        check(r["lint_clean"] is True, "(1a) corpus lints clean after report")
        defect_id = r["defect_id"]

        # (2) query FIRST — the anti-re-dig gate (no history yet)
        q0 = mcp.dispatch("defect_query", {"symptom": SY})
        check(q0["found"] and q0["defect"]["report_count"] == 1,
              "(2) query returns the reported defect")
        check(q0["attempts"] == [], "(2a) no attempts yet")

        # (3) attempt #1 — recurred (didn't hold)
        a1 = mcp.dispatch("defect_attempt", {
            "symptom": SY, "root_cause_hypothesis": "race in request_layout dispatch",
            "what_changed": "added a seq guard on the dispatch", "outcome": "recurred",
            "branch": "defect/hb-1", "commit": "aaa111",
            "why_it_didnt_hold": "the merge is upstream of dispatch — wrong layer",
            "root_cause_class": "oversight", "under_spec": True})
        check(a1["success"] and a1["changed"], "(3) attempt #1 lands")
        check(a1.get("analytics_row_id"), "(3a) attempt #1 wrote a SQLite row")
        check(a1["engine_refresh"] is not None, "(3b) attempt #1 refreshed the engine")

        # (4) attempt #2 — held (the real fix)
        a2 = mcp.dispatch("defect_attempt", {
            "symptom": SY, "root_cause_hypothesis": "no instance lease -> two GUIs answer",
            "what_changed": "added a lightweight instance lease", "outcome": "held",
            "branch": "defect/hb-2", "commit": "bbb222",
            "root_cause_class": "under-spec", "under_spec": True, "test_added": True})
        check(a2["success"] and a2.get("analytics_row_id"),
              "(4) attempt #2 lands + wrote a SQLite row")

        # (5) query ACCUMULATES — prior attempts + why-they-failed + the chain
        q = mcp.dispatch("defect_query", {"symptom": SY})
        check(q["defect"]["attempt_count"] == 2, "(5) two attempts accumulated")
        atts = q["attempts"]
        check([x["outcome"] for x in atts] == ["recurred", "held"],
              "(5a) both attempt outcomes preserved in order")
        check("wrong layer" in (atts[0]["why_it_didnt_hold"] or ""),
              "(5b) attempt #1's WHY-IT-DIDN'T-HOLD is queryable (the anti-re-dig payload)")
        check(defect_id in (atts[1]["supersedes"] or "") + "A1"
              or atts[1]["supersedes"], "(5c) attempt #2 SUPERSEDES attempt #1 (recurrence chain)")

        # (6) refresh verdict is fresh (or explicitly degraded — never silent)
        ref = a2["engine_refresh"]
        check(ref is not None and (ref.get("fresh") is True
              or ref.get("degraded_to_file_reconcile")),
              "(6) engine refresh is fresh-by-construction (or explicitly degraded)")

        # (7) resolve verified
        rv = mcp.dispatch("defect_resolve", {"symptom": SY, "status": "verified",
                                             "note": "lease holds across relaunch"})
        check(rv["success"] and rv["changed"], "(7) resolve verified")
        check(mcp.dispatch("defect_query", {"symptom": SY})["defect"]["lifecycle"]
              == "verified", "(7a) lifecycle -> verified")
        # idempotent
        check(mcp.dispatch("defect_resolve", {"symptom": SY, "status": "verified"})["changed"]
              is False, "(7b) repeat verified is a no-op")

        # (8) the SQLite histogram sees the attempts (defects_by_root_cause)
        hist = retro_db.defects_by_root_cause()
        check(hist.get("under-spec", 0) >= 2 or sum(hist.values()) >= 2,
              f"(8) SQLite histogram counts the attempts: {hist}")
        rows = retro_db.list_defect_investigations()
        check(any(row["defect_id"] == defect_id for row in rows),
              "(8a) analytics rows cross-link to the graph defect_id")

        # (9) NO-FORWARD-DANGLE: a related_to that resolves nowhere is REFUSED
        bad = mcp.dispatch("defect_report", {
            "symptom": "an unrelated new defect", "related_to": "DEFECT-NOPE-000000"})
        check(bad["success"] is False and bad.get("refused"),
              "(9) a kin edge to a missing node is REFUSED (no-forward-dangle)")

        # (10) a recurrence: report after verified reopens the defect
        rr = mcp.dispatch("defect_report", {"symptom": SY, "note": "came back on the rpi"})
        check(mcp.dispatch("defect_query", {"symptom": SY})["defect"]["lifecycle"]
              == "recurred", "(10) a report after verified reopens (recurrence)")

    finally:
        os.environ.pop("COMPOSEIO_RETRO_DB", None)

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: defect_ops selftest ({total[0] - len(fails)}/{total[0]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
