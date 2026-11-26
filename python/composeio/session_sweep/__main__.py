"""``python3 -m composeio.session_sweep`` — the loose-ends readout.

Parses a session ``.jsonl``, runs the deterministic detectors, and prints a
ranked, ASCII, no-emoji readout (or ``--json`` for the machine surface).  This
is the external check the dusk-gifts README calls load-bearing: *"the author
cannot see their own omissions from inside."*

    python3 -m composeio.session_sweep                     # default session file
    python3 -m composeio.session_sweep path/to/session.jsonl [--repo DIR]
    python3 -m composeio.session_sweep path/to/session.jsonl --json
    python3 -m composeio.session_sweep --bus            # self/loose_ends/* lines
    python3 -m composeio.session_sweep --selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

from .sensor import SessionSweep

_DEFAULT_SESSION = str(
    Path.home() / ".claude/projects/-Users-you/"
    "59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3.jsonl")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()

    as_json = "--json" in argv
    as_bus = "--bus" in argv
    repo = None
    if "--repo" in argv:
        i = argv.index("--repo")
        repo = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    positional = [a for a in argv if not a.startswith("--")]
    path = positional[0] if positional else _DEFAULT_SESSION

    if not Path(path).exists():
        print(f"session .jsonl not found: {path}", file=sys.stderr)
        return 2

    report = SessionSweep(path, repo=repo).run()
    if as_json:
        print(report.to_json())
    elif as_bus:
        for ns, val in report.to_bus_lines():
            print(f"{ns} = {val}")
    else:
        print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
