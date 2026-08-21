"""``python3 -m composeio.git_sensor`` — the git sync-state readout.

Instruments a repo's git state onto the ``git/*`` namespaces, prints the sync
summary + the bus ALERT + the head of the generated mermaid gitGraph.

    python3 -m composeio.git_sensor                # instrument the CWD repo
    python3 -m composeio.git_sensor /path/to/repo
    python3 -m composeio.git_sensor --selftest
"""
from __future__ import annotations

import os
import sys

from .sensor import GitSensor


def _readout(sensor: GitSensor) -> None:
    d = sensor.parse()
    alert = d.get("git/alert") or {}
    lc = d.get("git/last_commit") or {}

    print("=" * 60)
    print(f"  GIT SYNC READOUT  ·  repo: {sensor.repo_path}")
    print("=" * 60)
    up = d.get("git/upstream") or "(none)"
    det = "  [DETACHED]" if d.get("git/detached") else ""
    print(f"  git/branch             {d.get('git/branch')}{det}")
    print(f"  git/upstream           {up}")
    print(f"  git/uncommitted        {d.get('git/uncommitted', 0)}"
          f"   (staged {d.get('git/staged', 0)}, "
          f"untracked {d.get('git/untracked', 0)})")
    print(f"  git/unpushed           {d.get('git/unpushed', 0)} ahead")
    print(f"  git/behind             {d.get('git/behind', 0)} behind")
    print(f"  git/stashes            {d.get('git/stashes', 0)}")
    if lc:
        print(f"  git/last_commit        {lc.get('hash')}  {lc.get('subject')}")
    print("-" * 60)
    flag = "OUT OF SYNC" if alert.get("out_of_sync") else "in sync"
    print(f"  git/alert              [{flag}]")
    print(f"     reasons             {alert.get('reasons')}")
    print(f"     message             {alert.get('message')}")
    print("-" * 60)
    print("  git/graph (mermaid gitGraph — renders in the MermaidWidget):")
    for line in (d.get("git/graph") or "").splitlines()[:14]:
        print(f"     {line}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()

    repo = argv[0] if argv else os.getcwd()
    _readout(GitSensor(repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
