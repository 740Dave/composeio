"""``python3 -m composeio.decision_extractor`` — draft a spec-35 decision ledger.

Parses a session ``.jsonl``, extracts decisions from BOTH sources (inline
``load-bearing decision:`` markers + AskUserQuestion forks), and prints a DRAFT
LTS document (or writes it with ``--out``).  This is the mechanical first pass
the dusk-gifts README calls for: externalize the rationale channel the
``/compact`` summarizer cannot carry — BEFORE the seam — so the architect
refines a draft rather than authoring the ledger cold.

    python3 -m composeio.decision_extractor path/to/session.jsonl
    python3 -m composeio.decision_extractor path/to/session.jsonl --out draft.lts
    python3 -m composeio.decision_extractor path/to/session.jsonl --tail
    python3 -m composeio.decision_extractor --selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

from .extractor import DecisionExtractor, tail

_DEFAULT_SESSION = str(
    Path.home() / ".claude/projects/-Users-davidleeburnett/"
    "59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3.jsonl")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()

    as_tail = "--tail" in argv
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    positional = [a for a in argv if not a.startswith("--")]
    path = positional[0] if positional else _DEFAULT_SESSION

    if not as_tail and not Path(path).exists():
        print(f"session .jsonl not found: {path}", file=sys.stderr)
        return 2

    if as_tail:
        return tail(path, out=out)

    draft = DecisionExtractor(path).to_lts()
    if out:
        Path(out).write_text(draft, encoding="utf-8")
        print(f"draft written: {out}", file=sys.stderr)
    else:
        print(draft)
    return 0


if __name__ == "__main__":
    sys.exit(main())
