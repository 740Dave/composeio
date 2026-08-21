"""``python3 -m composeio.decision_ops`` — the CLI adapter over the
invocation-agnostic `add`/`update`/`query`/`resolve` core (see `core.py`'s
INVOCATION LAYERING note pattern, matching `lts_lint`'s CLI/core split).

    python3 -m composeio.decision_ops add <graph.lts> --id DEC-FOO --title "..." \\
        --lifecycle-position raised --decided-in-session 2026-07-22 \\
        --importance medium --rationale "..." [--field key=value ...]
    python3 -m composeio.decision_ops update <graph.lts> DEC-FOO --set key=value [--set ...]
    python3 -m composeio.decision_ops query <graph.lts> "prefix:DEC" [--json]
    python3 -m composeio.decision_ops resolve <graph.lts> DEC-FOO \\
        [--status STATUS] [--superseded-by ID] [--note TEXT]
    python3 -m composeio.decision_ops --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

from .core import (SESSION_ENV, DecisionOpsError, add, ambient_session, query,
                   resolve, update)

# Convenience-only CLI default (NOT baked into the core functions, which stay
# domain-agnostic) — mirrors `lts_lint`'s own `_DEFAULT_PROFILE_HINT` pattern.
# Location comes from the single source of truth (bootstrap_paths): the profile
# travels with the platform-specs corpus at corpora/platform-specs/.
from composeio.bootstrap_paths import (
    COMPOSEIO_PROFILE as _PROFILE_ABS,
    COMPOSEIO_PROFILE_REL as _DEFAULT_PROFILE_HINT,
)


def _default_profile() -> Optional[str]:
    if os.path.isfile(_DEFAULT_PROFILE_HINT):
        return _DEFAULT_PROFILE_HINT
    return str(_PROFILE_ABS) if _PROFILE_ABS.is_file() else None


def _parse_kv_list(pairs: Optional[List[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"decision_ops: bad --field/--set {p!r} (want key=value)")
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


def _print_result(res, as_json: bool) -> None:
    if as_json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        status = "changed" if res.changed else "no-op"
        print(f"{res.node_id}: {status} -- {res.message} "
              f"(lint_clean={res.lint_clean})")


def _cmd_add(args) -> int:
    node = {"id": args.id, "title": args.title}
    for k, v in (
        ("lifecycle-position", args.lifecycle_position),
        ("decided-in-session", args.decided_in_session),
        ("importance", args.importance),
        ("rationale", args.rationale),
        ("born-from", args.born_from),
        ("stage-evidence", args.stage_evidence),
        ("re-derive-via", args.re_derive_via),
        ("depends-on", args.depends_on),
        ("relates-to", args.relates_to),
        ("supersedes", args.supersedes),
    ):
        if v is not None:
            node[k] = v
    node.update(_parse_kv_list(args.field))
    res = add(args.graph, node, profile=args.profile or _default_profile())
    _print_result(res, args.json)
    return 0


def _cmd_update(args) -> int:
    fields = _parse_kv_list(args.set)
    if not fields:
        raise SystemExit("decision_ops update: give at least one --set key=value")
    res = update(args.graph, args.node_id, fields, profile=args.profile or _default_profile())
    _print_result(res, args.json)
    return 0


def _cmd_query(args) -> int:
    results = query(args.graph, args.selector, profile=args.profile or _default_profile(),
                    limit=args.limit)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("(no matches)")
        for r in results:
            loc = f"{r['source_file']}:{r['line']}" if r.get("source_file") else "<corpus>"
            print(f"[{r['id']}] {r['title']}\n"
                  f"    lifecycle-position={r['lifecycle_position']}  "
                  f"importance={r['importance']}  {loc}\n"
                  f"    rationale: {r['rationale']}")
    return 0


def _cmd_resolve(args) -> int:
    res = resolve(args.graph, args.node_id, status=args.status,
                  superseded_by=args.superseded_by, note=args.note,
                  profile=args.profile or _default_profile())
    _print_result(res, args.json)
    return 0


def _cmd_session_token(args) -> int:
    """Print the ambient session token this PROCESS sees ([CXC-GV-SCOPE]).
    Exit 0 when one is set, 1 when not — so a script can gate on it."""
    tok = ambient_session()
    if args.json:
        print(json.dumps({"env_var": SESSION_ENV, "session_token": tok,
                          "ambient": tok is not None}))
    elif tok:
        print(f"{SESSION_ENV}={tok}")
    else:
        print(f"{SESSION_ENV} is unset — decided-in-session must be supplied "
              f"explicitly (unchanged behaviour)")
    return 0 if tok else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()

    ap = argparse.ArgumentParser(
        prog="python -m composeio.decision_ops",
        description="Deterministic add/update/query/resolve over a project "
                    "decision-graph .lts file.",
    )
    ap.add_argument("--profile", default=None, help="domain profile path "
                    f"(default: {_DEFAULT_PROFILE_HINT!r} if it exists relative to cwd)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="append a new decision node")
    p_add.add_argument("graph")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--lifecycle-position")
    p_add.add_argument("--decided-in-session")
    p_add.add_argument("--importance")
    p_add.add_argument("--rationale")
    p_add.add_argument("--born-from")
    p_add.add_argument("--stage-evidence")
    p_add.add_argument("--re-derive-via")
    p_add.add_argument("--depends-on")
    p_add.add_argument("--relates-to")
    p_add.add_argument("--supersedes")
    p_add.add_argument("--field", action="append", metavar="KEY=VALUE",
                       help="extra bullet field, repeatable")
    p_add.set_defaults(func=_cmd_add)

    p_update = sub.add_parser("update", help="update fields on an existing node")
    p_update.add_argument("graph")
    p_update.add_argument("node_id")
    p_update.add_argument("--set", action="append", metavar="KEY=VALUE",
                          help="field to set, repeatable")
    p_update.set_defaults(func=_cmd_update)

    p_query = sub.add_parser("query", help="find nodes by id / prefix / free-text")
    p_query.add_argument("graph")
    p_query.add_argument("selector", help='e.g. "DEC-FOO-BAR", "prefix:DEC", "text:context"')
    p_query.add_argument("--limit", type=int, default=None)
    p_query.set_defaults(func=_cmd_query)

    p_resolve = sub.add_parser("resolve", help="annotate a node resolved/superseded")
    p_resolve.add_argument("graph")
    p_resolve.add_argument("node_id")
    p_resolve.add_argument("--status")
    p_resolve.add_argument("--superseded-by")
    p_resolve.add_argument("--note")
    p_resolve.set_defaults(func=_cmd_resolve)

    # [CXC-GV-SCOPE] the ambient-session-token check for THIS process. Useful in a
    # shell/subagent (which inherits the orchestrator's environment directly); the
    # long-lived MCP server answers for ITSELF via the decision_session_token tool.
    p_tok = sub.add_parser("session-token",
                           help="print the ambient session token this process sees "
                                "(what an omitted decided-in-session would inherit)")
    p_tok.set_defaults(func=_cmd_session_token)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except DecisionOpsError as e:
        print(f"decision_ops: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
