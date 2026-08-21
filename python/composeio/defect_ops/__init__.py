"""composeio.defect_ops — the deterministic CORE (report / attempt / resolve /
query) over a defect-graph `.lts`, the SISTER of `decision_ops` and a NEW CONSUMER
of the ONE FactLattice engine ([DEC-ONE-FACTLATTICE-PARSER]) — never a second
engine.

WHY ([SPEC:CIO-DEFECT-REGISTRY]): a fix-resistant defect loses its history
between defect-investigator dispatches, so each fresh investigator re-digs from
scratch and misses the pattern ("5 attempts all touched X -> the real cause is
elsewhere"). defect_ops gives a defect a persistent, QUERYABLE home keyed to a
STABLE symptom identity: report -> query (the anti-re-dig gate) -> attempt ->
resolve, with the per-attempt narrative + supersedes/reopens/related-to edges in
the graph and the counts/aggregation in the retrospective SQLite (the
graph/SQLite seam).

    from composeio.defect_ops import report, attempt, resolve, query
    query(graph, "heartbeat get_layout returns a merged snapshot")  # <- run FIRST
    report(graph, symptom="...", area="...")
    attempt(graph, symptom="...", root_cause_hypothesis="...", what_changed="...",
            outcome="recurred", branch="defect/...", why_it_didnt_hold="...")
    resolve(graph, symptom="...", status="verified")

REUSE-BY-REFERENCE: the engine-path seat, the `.lts` splice primitives, and the
dangle/collision rollback guard all come from `decision_ops` (imported by
reference); `lts_refresh.LtsRefresher` is reused verbatim by the MCP server. Same
guarantees: append-only, no-forward-dangle, refresh-the-engine on write.

WHERE IT WRITES ([decision_ops.graph_home], added 2026-08-08). The registry has
exactly ONE home: the CANONICAL checkout, resolved via
`git rev-parse --git-common-dir` — correct from inside ANY git worktree. A `graph`
path that lives inside a LINKED worktree is REFUSED, loudly, naming the canonical
path (escape hatch `COMPOSEIO_ALLOW_WORKTREE_GRAPH=1` for hermetic tests only).
Before that guard, two defects filed by dispatched subagents landed on worktree
copies and were live on no graph at all, while both agents honestly reported
"filed".

EVERY WRITE IS SELF-PROVING: `DefectResult.to_dict()` carries `graph_path`
(absolute, resolved) and `graph_is_canonical`. Quote them when you report a
filing — a claim that costs the reader a `grep` is not a receipt.
"""
from __future__ import annotations

# MUST run before `.core`, which (via decision_ops.core) imports
# `factlattice_engine.lint` at module level. Reuse decision_ops' seat by reference.
from ..decision_ops._enginepath import ensure_engine_on_path

ensure_engine_on_path()

from .core import (  # noqa: E402 — engine path must be seated first
    DangleRefusedError,
    DefectOpsError,
    DefectResult,
    NodeExistsError,
    NodeNotFoundError,
    attempt,
    list_defects,
    query,
    report,
    resolve,
)
from .profile import (  # noqa: E402
    LIFECYCLE,
    EDGE_KINDS,
    attempt_node_id,
    default_profile_path,
    normalize_symptom,
    symptom_slug,
)

__all__ = [
    "report", "attempt", "resolve", "query", "list_defects",
    "DefectResult", "DefectOpsError",
    "DangleRefusedError", "NodeNotFoundError", "NodeExistsError",
    "LIFECYCLE", "EDGE_KINDS", "symptom_slug", "attempt_node_id",
    "normalize_symptom", "default_profile_path",
]
