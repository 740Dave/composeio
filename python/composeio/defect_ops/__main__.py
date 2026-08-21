"""composeio.defect_ops CLI — a thin argv->DISPATCH shell over the ONE
`DefectOpsMcp.dispatch` composition, matching the decision_ops layout
(`__init__` + `__main__` + core + selftest + mcp_server).

    python3 -m composeio.defect_ops query   "<symptom or DEFECT-id>"
    python3 -m composeio.defect_ops report  --symptom "..." [--area ...] [--note ...]
    python3 -m composeio.defect_ops attempt --symptom "..." \
        --root-cause "..." --what-changed "..." --outcome held|recurred [--branch ...]
    python3 -m composeio.defect_ops resolve --symptom "..." [--status verified|reopened]
    python3 -m composeio.defect_ops --selftest

`<graph.lts>` is an OPTIONAL leading positional. OMIT IT: the default resolves to
the CANONICAL checkout's `defects/defect-graph.lts` via
`git rev-parse --git-common-dir`, which is correct from inside ANY git worktree.
An explicit path that lives inside a linked worktree is REFUSED (loudly, naming
the canonical path) — a record written to a worktree copy is live on NO graph, and
that silently cost this project two defect records on 2026-08-08.

EVERY WRITE RETURNS A RECEIPT: `graph_path` (absolute, resolved) and
`graph_is_canonical`. Quote them — "filed the defect" without them is a claim, not
a fact.

DE-FORK ([SPEC:CIO-DEFECT-REGISTRY] [DR-CLI], reuse-by-reference-never-fork):
this CLI MUST route every op through the SAME `DefectOpsMcp.dispatch(...)` the
stdio MCP server uses — it is a thin argv->dispatch shell exactly as `serve()`
is a thin stdin->dispatch shell. It does NOT call the core ops directly: the core
writes only the GRAPH, so a direct-to-core CLI composes a PARTIAL op that silently
drops the SQLite analytics row ([DR-SQLITE]) and the engine refresh ([DR-MCP]).
That fork is exactly why the Defects roll-up published all-NULL (the graph filled,
`defect_investigations` stayed empty). The write-both-seams-and-refresh op is
composed in ONE place, reused by every entry point.
"""
from __future__ import annotations

import argparse
import json
import sys

# taxonomy the analytics class normalizes to (documented on the CLI surface so a
# CLI investigator sees the same vocabulary the SQLite histogram buckets on).
_ROOT_CAUSE_CHOICES = "under-spec|oversight|fork-divergence|spec-drift|other"


def _print(obj) -> None:
    print(json.dumps(obj if isinstance(obj, dict) else obj.to_dict()
                     if hasattr(obj, "to_dict") else obj, indent=2, default=str))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()

    ap = argparse.ArgumentParser(prog="composeio.defect_ops", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # THE GRAPH ARGUMENT IS NOW OPTIONAL — omit it and the CANONICAL registry is
    # used ($DEFECT_OPS_GRAPH, else <canonical-checkout>/defects/defect-graph.lts,
    # resolved via `git rev-parse --git-common-dir` so it is right from inside any
    # worktree). Making the safe path the SHORT path is half of "structurally
    # impossible": an agent that types the obvious command cannot miss the registry.
    # An explicit path inside a linked worktree is REFUSED, loudly, by the core.
    # `query` keeps two positionals for compatibility; with only one, it is the
    # symptom.
    q = sub.add_parser("query")
    q.add_argument("graph"); q.add_argument("symptom", nargs="?", default=None)
    r = sub.add_parser("report"); r.add_argument("graph", nargs="?", default=None)
    r.add_argument("--symptom", required=True); r.add_argument("--area", default="")
    r.add_argument("--note", default=""); r.add_argument("--related-to", default=None)
    a = sub.add_parser("attempt"); a.add_argument("graph", nargs="?", default=None)
    a.add_argument("--symptom", required=True); a.add_argument("--root-cause", default="")
    a.add_argument("--what-changed", default=""); a.add_argument("--outcome", default="held")
    a.add_argument("--branch", default=""); a.add_argument("--commit", default="")
    a.add_argument("--why-it-didnt-hold", default=""); a.add_argument("--area", default="")
    # analytics-row fields -> the SQLite `defect_investigations` row ([DR-SQLITE]).
    # `--root-cause-class` overrides the class the histogram buckets on; when
    # omitted the dispatch falls back to `--root-cause` (the agent-def passes the
    # taxonomy there), so a minimal CLI call still populates the gauge.
    a.add_argument("--root-cause-class", default="",
                   help=f"analytics class ({_ROOT_CAUSE_CHOICES}); "
                        "default: the --root-cause value")
    a.add_argument("--prior-fix-ref", default="")
    a.add_argument("--under-spec", action="store_true")
    a.add_argument("--fork", action="store_true")
    a.add_argument("--spec-added", default="")
    a.add_argument("--test-added", action="store_true")
    rs = sub.add_parser("resolve"); rs.add_argument("graph", nargs="?", default=None)
    rs.add_argument("--symptom", required=True); rs.add_argument("--status", default="verified")
    rs.add_argument("--note", default="")
    rs.add_argument("--root-cause-class", default="")
    rs.add_argument("--test-added", action="store_true")

    args = ap.parse_args(argv)

    # De-fork: build the arguments dict + drive the ONE dispatch composition.
    from .mcp_server import DefectOpsMcp
    ops = DefectOpsMcp()
    tool = {"query": "defect_query", "report": "defect_report",
            "attempt": "defect_attempt", "resolve": "defect_resolve"}[args.cmd]

    if args.cmd == "query":
        # `query <graph> <symptom>` (legacy) or `query <symptom>` (canonical default).
        graph, symptom = args.graph, args.symptom
        if symptom is None:
            graph, symptom = None, args.graph
        arguments = {"graph": graph, "symptom": symptom}
    elif args.cmd == "report":
        arguments = {"graph": args.graph, "symptom": args.symptom,
                     "area": args.area, "note": args.note,
                     "related_to": args.related_to}
    elif args.cmd == "attempt":
        arguments = {"graph": args.graph, "symptom": args.symptom,
                     "root_cause_hypothesis": args.root_cause,
                     "what_changed": args.what_changed, "outcome": args.outcome,
                     "branch": args.branch, "commit": args.commit,
                     "why_it_didnt_hold": args.why_it_didnt_hold, "area": args.area,
                     "root_cause_class": args.root_cause_class,
                     "prior_fix_ref": args.prior_fix_ref,
                     "under_spec": args.under_spec, "fork": args.fork,
                     "spec_added": args.spec_added, "test_added": args.test_added}
    else:  # resolve
        arguments = {"graph": args.graph, "symptom": args.symptom,
                     "status": args.status, "note": args.note,
                     "root_cause_class": args.root_cause_class,
                     "test_added": args.test_added}

    result = ops.dispatch(tool, arguments)
    _print(result)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
