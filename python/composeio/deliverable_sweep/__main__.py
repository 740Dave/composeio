"""``python3 -m composeio.deliverable_sweep`` — sweep project root(s) for non-code
deliverables, diff against the ledger, print the delta (or persist it with
``--write``). ASCII, no emoji, matching the session_sweep / compact_summary CLI idiom.

    python3 -m composeio.deliverable_sweep sweep --ledger PATH [--root DIR ...] \\
        [--write] [--json] [--session KEY] [--now ISO8601]
    python3 -m composeio.deliverable_sweep --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from .ledger import Ledger
from .sweep import DEFAULT_LEDGER_PATH, SweepResult, run_sweep


def build_report(ledger_path: Path, ledger: Ledger, result: SweepResult) -> Dict[str, Any]:
    """The [CXC-DS-REPORT-FORMAT] JSON schema — what bootstrap/session-end consume."""

    def brief(nid: str, extra_keys: list) -> Dict[str, Any]:
        fields = ledger.nodes[nid]
        d: Dict[str, Any] = {"id": nid, "path": fields.get("path", "")}
        for k in extra_keys:
            if k in fields:
                d[k] = fields[k]
        return d

    return {
        "ledger": str(ledger_path),
        "run": {
            "date": result.now,
            "new": len(result.delta.new),
            "modified": len(result.delta.modified),
            "removed": len(result.delta.removed),
        },
        "new": [brief(n, ["project-root", "format"]) for n in result.delta.new],
        "modified": [brief(n, ["modified"]) for n in result.delta.modified],
        "removed": [brief(n, ["missing-since"]) for n in result.delta.removed],
        "needs-enrichment": result.needs_enrichment,
    }


def render_report_text(report: Dict[str, Any], written: bool) -> str:
    r = report["run"]
    mode = "WRITE" if written else "DRY-RUN"
    lines = [
        f"deliverable_sweep [{mode}]  ledger={report['ledger']}",
        f"  new={r['new']}  modified={r['modified']}  removed={r['removed']}",
    ]
    for n in report["new"]:
        lines.append(f"  NEW      {n['id']:40s} {n['path']}")
    for n in report["modified"]:
        lines.append(f"  MODIFIED {n['id']:40s} {n['path']}")
    for n in report["removed"]:
        lines.append(f"  REMOVED  {n['id']:40s} {n['path']}  (flagged, not deleted)")
    if not (r["new"] or r["modified"] or r["removed"]):
        lines.append("  (no changes)")
    if report["needs-enrichment"]:
        lines.append("  needs-enrichment: " + ", ".join(report["needs-enrichment"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()

    ap = argparse.ArgumentParser(
        prog="python -m composeio.deliverable_sweep",
        description="Deterministic sweep of non-code deliverables into a ledger .lts "
                    "([SPEC:CXC-DS], corpora/platform-specs/37-deliverable-sweep.lts).",
    )
    ap.add_argument("subcommand", nargs="?", default="sweep", choices=["sweep"])
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH),
                    help="path to the deliverable-ledger.lts")
    ap.add_argument("--root", action="append", default=None,
                    help="a project root to sweep (repeatable); seeds/extends the "
                        "ledger's project-roots config on --write")
    ap.add_argument("--write", action="store_true",
                    help="persist the delta (mechanical fields only); default is a "
                        "safe dry-run report")
    ap.add_argument("--json", action="store_true", help="emit the machine report")
    ap.add_argument("--session", default=None,
                    help="the current session key, stamped on NEW nodes' "
                        "generated-in-session instead of NEEDS-ENRICHMENT")
    ap.add_argument("--now", default=None,
                    help="override the injected 'now' (ISO-8601) — for reproducible "
                        "testing; defaults to real UTC now")
    ap.add_argument("--domain", default=None,
                    help="frontmatter 'domain' to seed on a BRAND NEW ledger only "
                        "(never overwrites an existing value); this tool is "
                        "domain-agnostic and invents no default")
    ap.add_argument("--confidentiality", default=None,
                    help="frontmatter 'confidentiality' to seed on a brand new ledger")
    ap.add_argument("--artifact-readiness", default=None,
                    help="frontmatter 'artifact-readiness' to seed on a brand new ledger")
    args = ap.parse_args(argv)

    ledger_path = Path(args.ledger)
    frontmatter = {
        "domain": args.domain,
        "confidentiality": args.confidentiality,
        "artifact-readiness": args.artifact_readiness,
    }
    ledger, result = run_sweep(
        ledger_path, roots=args.root, write=args.write, now=args.now,
        session_key=args.session, frontmatter=frontmatter,
    )
    report = build_report(ledger_path, ledger, result)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_report_text(report, written=args.write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
