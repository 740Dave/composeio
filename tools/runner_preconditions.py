#!/usr/bin/env python3
"""Preconditions for the self-hosted runner that holds the private vocabulary (docs/leakcheck/RUNNER.md).

    runner_preconditions.py [--repo OWNER/NAME] [--fixture DIR] [--min-days N]
        exit 0 every precondition holds; 1 one does not (printed); 2 a setting could not be read (never a pass)

The runner is registered ONLY after this passes, and is deregistered if it ever fails. The merge queue's self-hosted job
runs it first and refuses to scan when it does not pass. Two repository settings are read through `gh api`:
  1. the interaction limit (GET /repos/{repo}/interaction-limits): `limit` must be `collaborators_only`, with an
     `expires_at` at least --min-days (default 7) in the future; the expiry is printed so its renewal is visible;
  2. the approval policy for workflow runs from fork PRs (GET /repos/{repo}/actions/permissions/fork-pr-contributor-approval):
     `approval_policy` must be `all_external_contributors`.
Both need a token that can read the repository's administration settings: `gh` uses GH_TOKEN, else its own login. A
setting that cannot be read (no token, 403, 404, bad JSON) is exit 2, not a pass. --fixture DIR reads
`interaction-limits.json` and `fork-pr-contributor-approval.json` from DIR instead (for the negative control).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

ENDPOINTS = {
    "interaction-limits": "repos/{repo}/interaction-limits",
    "fork-pr-contributor-approval": "repos/{repo}/actions/permissions/fork-pr-contributor-approval",
}


class CouldNotRead(Exception):
    pass


def read(name, repo, fixture):
    if fixture:
        p = pathlib.Path(fixture) / f"{name}.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise CouldNotRead(f"{name}: fixture unreadable ({type(e).__name__})") from e
    p = subprocess.run(["gh", "api", ENDPOINTS[name].format(repo=repo)], capture_output=True, text=True)
    if p.returncode != 0:
        raise CouldNotRead(f"{name}: gh api failed: {(p.stderr or p.stdout).strip()[:200]}")
    try:
        return json.loads(p.stdout or "null")
    except ValueError as e:
        raise CouldNotRead(f"{name}: not JSON") from e


def check(repo, fixture, min_days, now=None):
    """-> (failures, notes). Raises CouldNotRead."""
    now = now or dt.datetime.now(dt.timezone.utc)
    failures, notes = [], []
    lim = read("interaction-limits", repo, fixture)
    if not isinstance(lim, dict) or lim.get("limit") != "collaborators_only":
        failures.append(f"interaction limit is {(lim or {}).get('limit') if isinstance(lim, dict) else lim!r}, not collaborators_only")
    else:
        try:
            exp = dt.datetime.fromisoformat(str(lim.get("expires_at")).replace("Z", "+00:00"))
        except ValueError:
            exp = None
        if exp is None:
            failures.append("interaction limit has no readable expires_at")
        else:
            days = (exp - now).total_seconds() / 86400
            notes.append(f"interaction limit collaborators_only, expires {exp.isoformat()} ({days:.1f} days left)")
            if days < min_days:
                failures.append(f"interaction limit expires in {days:.1f} days (< {min_days}): renew it first")
    fork = read("fork-pr-contributor-approval", repo, fixture)
    policy = fork.get("approval_policy") if isinstance(fork, dict) else None
    if policy != "all_external_contributors":
        failures.append(f"fork-PR workflow approval policy is {policy!r}, not all_external_contributors")
    else:
        notes.append("fork-PR workflow approval policy all_external_contributors")
    return failures, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="740Dave/composeio")
    ap.add_argument("--fixture")
    ap.add_argument("--min-days", type=float, default=7)
    a = ap.parse_args(argv)
    try:
        failures, notes = check(a.repo, a.fixture, a.min_days)
    except CouldNotRead as e:
        print(f"[runner-preconditions] COULD NOT READ ({a.repo}): {e}. Not a pass: do not register the runner, or deregister it.")
        return 2
    for n in notes:
        print(f"[runner-preconditions] ok    {n}")
    for f in failures:
        print(f"[runner-preconditions] FAIL  {f}")
    if failures:
        print(f"[runner-preconditions] {a.repo}: {len(failures)} precondition(s) unmet. Do not register the runner; deregister it if registered.")
        return 1
    print(f"[runner-preconditions] {a.repo}: all preconditions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
