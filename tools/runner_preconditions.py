#!/usr/bin/env python3
"""Preconditions for the self-hosted runner that holds the private vocabulary (docs/leakcheck/RUNNER.md).

    runner_preconditions.py [--repo OWNER/NAME] [--fixture DIR] [--min-days N]                 check live (maintainer's Mac)
    runner_preconditions.py --record [--publish] [--repo R] [--fixture DIR] [--now ISO]      check live, emit a dated record
    runner_preconditions.py --verify-record [--record-file F] [--repo R] [--max-age-hours H] [--min-days N] [--now ISO]
        exit 0 every precondition holds; 1 one does not (printed); 2 a setting or record could not be read (never a pass)

WHERE EACH HALF RUNS. Reading the two settings below needs a token that can read the repository's administration
settings. The self-hosted runner's account has no GitHub login, the job's GITHUB_TOKEN cannot read them, and no admin
token is stored on the runner or in Actions secrets (that token would be the exposure these settings guard against).
So the LIVE check runs on the maintainer's Mac under its own `gh` login (git-expert, on a schedule and before a
queue run): `--record --publish` checks, then writes a dated JSON record to the repository Actions variable
RUNNER_PRECONDITIONS (`gh variable set`), pass OR fail. The job reads that variable through the workflow's `vars`
context, which GitHub injects with no token, and runs `--verify-record`: it refuses unless the record exists, parses,
names this repository, has verdict `pass`, is no older than --max-age-hours (default 26) and not future-dated, and
its interaction-limit expiry is still --min-days ahead of NOW (so a record cannot outlive the limit it saw).

The runner is registered ONLY after the live check passes, and is deregistered if it ever fails. Two repository
settings are read through `gh api`:
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
import os
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
    try:
        p = subprocess.run(["gh", "api", ENDPOINTS[name].format(repo=repo)], capture_output=True, text=True)
    except OSError as e:  # gh not installed or not runnable: a could-not-read, never a traceback read as "unmet"
        raise CouldNotRead(f"{name}: cannot run gh ({type(e).__name__}: {e.strerror or e})") from e
    if p.returncode != 0:
        raise CouldNotRead(f"{name}: gh api failed: {(p.stderr or p.stdout).strip()[:200]}")
    try:
        return json.loads(p.stdout or "null")
    except ValueError as e:
        raise CouldNotRead(f"{name}: not JSON") from e


def check(repo, fixture, min_days, now=None, seen=None):
    """-> (failures, notes). Raises CouldNotRead. `seen`, if a dict, receives the raw settings read."""
    now = now or dt.datetime.now(dt.timezone.utc)
    failures, notes = [], []
    seen = seen if seen is not None else {}
    lim = read("interaction-limits", repo, fixture)
    seen["interaction-limits"] = lim
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
    seen["fork-pr-contributor-approval"] = fork
    policy = fork.get("approval_policy") if isinstance(fork, dict) else None
    if policy != "all_external_contributors":
        failures.append(f"fork-PR workflow approval policy is {policy!r}, not all_external_contributors")
    else:
        notes.append("fork-PR workflow approval policy all_external_contributors")
    return failures, notes


RECORD_SCHEMA = "composeio-runner-preconditions/1"
RECORD_VARIABLE = "RUNNER_PRECONDITIONS"


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(v):
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def make_record(repo, fixture, min_days, now):
    """The dated result of one live check: verdict pass | fail | unreadable. Emitted in every case, so a lapse
    REPLACES an older pass instead of leaving it to be read until it goes stale."""
    seen = {}
    try:
        failures, notes = check(repo, fixture, min_days, now, seen)
        verdict = "fail" if failures else "pass"
    except CouldNotRead as e:
        failures, notes, verdict = [str(e)], [], "unreadable"
    lim = seen.get("interaction-limits")
    return {"schema": RECORD_SCHEMA, "repo": repo, "checked_at": iso(now), "verdict": verdict,
            "interaction_limit_expires_at": lim.get("expires_at") if isinstance(lim, dict) else None,
            "fork_pr_approval_policy": (seen.get("fork-pr-contributor-approval") or {}).get("approval_policy")
            if isinstance(seen.get("fork-pr-contributor-approval"), dict) else None,
            "failures": failures, "notes": notes}


def verify_record(text, repo, max_age_hours, min_days, now):
    """-> (exit, lines). The job's half: judges a record it did not make and cannot refresh."""
    if not (text or "").strip():
        return 2, [f"no record: the Actions variable {RECORD_VARIABLE} is unset or empty. Run on the maintainer's Mac: "
                   f"python3 -I tools/runner_preconditions.py --record --publish --repo {repo}"]
    try:
        r = json.loads(text)
    except ValueError:
        return 2, ["record is not JSON"]
    if not isinstance(r, dict) or r.get("schema") != RECORD_SCHEMA:
        return 2, [f"record schema is not {RECORD_SCHEMA}"]
    bad = []
    if r.get("repo") != repo:
        bad.append(f"record is for {r.get('repo')!r}, not {repo}")
    at = parse_time(r.get("checked_at"))
    if at is None:
        return 2, ["record has no readable checked_at"]
    age_h = (now - at).total_seconds() / 3600
    if age_h < -5 / 60:
        bad.append(f"record is dated {iso(at)}, in the future")
    elif age_h > max_age_hours:
        bad.append(f"record is {age_h:.1f} h old (> {max_age_hours} h): re-run the check on the maintainer's Mac")
    if r.get("verdict") != "pass":
        bad.append(f"record verdict is {r.get('verdict')!r}: {'; '.join(map(str, r.get('failures') or []))}")
    exp = parse_time(r.get("interaction_limit_expires_at"))
    if exp is None:
        bad.append("record has no readable interaction-limit expiry")
    elif (exp - now).total_seconds() / 86400 < min_days:
        bad.append(f"interaction limit expires {iso(exp)}, under {min_days} days from now: renew it first")
    lines = [f"record checked_at {iso(at)} ({age_h:.1f} h ago), verdict {r.get('verdict')!r}, "
             f"interaction limit expires {r.get('interaction_limit_expires_at')}, fork-PR approval {r.get('fork_pr_approval_policy')!r}"]
    return (1 if bad else 0), lines + [f"FAIL  {b}" for b in bad]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="740Dave/composeio")
    ap.add_argument("--fixture")
    ap.add_argument("--min-days", type=float, default=7)
    ap.add_argument("--record", action="store_true", help="check live and print a dated JSON record (exit as the check)")
    ap.add_argument("--publish", action="store_true", help=f"with --record: also store it in the Actions variable {RECORD_VARIABLE} (gh login)")
    ap.add_argument("--verify-record", action="store_true", help="judge a record instead of reading the settings (the job's half)")
    ap.add_argument("--record-file", help=f"read the record from this file (default: the environment variable {RECORD_VARIABLE})")
    ap.add_argument("--max-age-hours", type=float, default=26)
    ap.add_argument("--now", help="ISO time to judge against (tests only)")
    a = ap.parse_args(argv)
    now = parse_time(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    if now is None:
        ap.error("--now must be an ISO time with a zone")
    if a.verify_record:
        try:
            text = pathlib.Path(a.record_file).read_text(encoding="utf-8") if a.record_file else os.environ.get(RECORD_VARIABLE, "")
        except OSError as e:
            text = ""
            print(f"[runner-preconditions] record file unreadable ({type(e).__name__})")
        rc, lines = verify_record(text, a.repo, a.max_age_hours, a.min_days, now)
        for ln in lines:
            print(f"[runner-preconditions] {ln}")
        print(f"[runner-preconditions] {a.repo}: recorded preconditions "
              + {0: "hold", 1: "do NOT hold: the job refuses", 2: "COULD NOT BE READ: not a pass, the job refuses"}[rc])
        return rc
    if a.publish and (a.fixture or a.now or not a.record):
        ap.error("--publish needs --record and a LIVE check (no --fixture, no --now)")
    if a.record:
        rec = make_record(a.repo, a.fixture, a.min_days, now)
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"))
        print(body)
        if a.publish:
            p = subprocess.run(["gh", "variable", "set", RECORD_VARIABLE, "--repo", a.repo, "--body", body],
                               capture_output=True, text=True)
            if p.returncode != 0:
                print(f"[runner-preconditions] COULD NOT PUBLISH: {(p.stderr or p.stdout).strip()[:200]}", file=sys.stderr)
                return 2
            print(f"[runner-preconditions] published to Actions variable {RECORD_VARIABLE} on {a.repo}", file=sys.stderr)
        return {"pass": 0, "fail": 1}.get(rec["verdict"], 2)
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
