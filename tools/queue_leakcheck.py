#!/usr/bin/env python3
"""The merge queue's self-hosted leak-check job, as one script that runs from MAIN's checkout (merge-queue.yml, `leakcheck`).

    queue_leakcheck.py --head SHA (--pr N --repo OWNER/NAME | --pr-text FILE) [--private-dir DIR] [--output FILE] [--local-probe]
        exit 0 every check passed; 1 a check failed or refused; 2 a check could not run

Every tool it runs is MAIN's copy (this checkout), never the PR's: the PR head is only fetched and read as data, so a PR that
edits tools/ci_gitleaks.py, tools/leakcheck.py, the guard or the negative control does not decide its own result. In order:
  1. tools/runner_preconditions.py (interaction limit, fork-PR approval policy). Unmet -> refuse; no scan is run.
     --local-probe skips this step, LOUDLY; the workflow never passes it.
  2. the head must contain main's tip (the PR is up to date), so the head tested is the tree that lands;
  3. the PR title and body are read (from the public API, no token) and written to a file;
  4. tools/leakcheck.py diff over main..head, with that text and the private vocabulary;
  5. tools/ci_gitleaks.py (main's copy) over a detached worktree of the head and the history main..head;
  6. tests/leakcheck/negative_control.py with the private vocabulary (every rule must still fire);
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

MAIN = pathlib.Path(__file__).resolve().parents[1]
TOOLS = MAIN / "tools"


def say(msg):
    print(f"[queue-leakcheck] {msg}", flush=True)


def run(*cmd, cwd=MAIN):
    say("run: " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], cwd=cwd).returncode


def pr_text_from_api(repo, number):
    """-> 'title\\n\\nbody\\n' read from the public REST API without a token (the repository is public)."""
    url = f"https://api.github.com/repos/{repo}/pulls/{int(number)}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "composeio-queue-leakcheck"})
    with urllib.request.urlopen(req, timeout=30) as r:
        pr = json.load(r)
    return pr_text(pr)


def pr_text(pr):
    """The ONE rendering of a PR's text, shared with the status job (pr_text_digest)."""
    return (pr.get("title") or "") + "\n\n" + (pr.get("body") or "") + "\n"


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--head")
    ap.add_argument("--pr")
    ap.add_argument("--repo")
    ap.add_argument("--pr-text", help="a file holding the PR title and body (local probes only; the workflow reads the API)")
    ap.add_argument("--private-dir")
    ap.add_argument("--output", help="append key=value lines here (GITHUB_OUTPUT)")
    ap.add_argument("--local-probe", action="store_true", help="skip the runner preconditions (local probes only)")
    a = ap.parse_args(argv)
    if not a.pr_text and not (a.pr and a.repo):
        ap.error("--pr and --repo, or --pr-text, are required")
    if not a.head:
        ap.error("--head is required")
    pdir = a.private_dir or os.environ.get("COMPOSEIO_LEAKCHECK_PRIVATE") or str(pathlib.Path(tempfile.gettempdir()) / "no-private-vocabulary")
    work = pathlib.Path(tempfile.mkdtemp(prefix="queue-leakcheck-"))

    if a.local_probe:
        say("NOTE: --local-probe: the runner preconditions were NOT checked (never passed by the workflow)")
    else:
        rc = run(sys.executable, "-I", TOOLS / "runner_preconditions.py", "--repo", a.repo or "740Dave/composeio")
        if rc != 0:
            say(f"REFUSED: runner preconditions unmet (exit {rc}); no scan was run. Deregister the runner until they hold.")
            return 1

    base = subprocess.run(["git", "-C", str(MAIN), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if subprocess.run(["git", "-C", str(MAIN), "merge-base", "--is-ancestor", base, a.head]).returncode != 0:
        say("REFUSED: the head does not contain main's tip: update the branch, then re-queue")
        return 1

    def read_text():
        if a.pr_text:
            return pathlib.Path(a.pr_text).read_text(encoding="utf-8")
        return pr_text_from_api(a.repo, a.pr)

    try:
        text = read_text()
    except (OSError, urllib.error.URLError, ValueError) as e:
        say(f"COULD NOT RUN: cannot read the PR text ({type(e).__name__})")
        return 2
    tfile = work / "pr-text.txt"
    tfile.write_text(text, encoding="utf-8")

    results = {}
    results["leakcheck diff (main's copy)"] = run(sys.executable, "-I", TOOLS / "leakcheck.py", "diff", "--repo", MAIN, "--base", base,
                                                  "--head", a.head, "--pr-text", tfile, "--private-dir", pdir)
    wt = work / "head"
    if subprocess.run(["git", "-C", str(MAIN), "worktree", "add", "--quiet", "--detach", str(wt), a.head]).returncode != 0:
        say("COULD NOT RUN: cannot make a worktree of the head")
        return 2
    try:
        env_clean = {k: v for k, v in os.environ.items() if k not in ("GITLEAKS_BIN", "BASE_REF")}
        say(f"run: ci_gitleaks.py (main's copy) --tree {wt} --history-base {base[:12]}")
        results["gitleaks (main's copy)"] = subprocess.run(
            [sys.executable, "-I", str(TOOLS / "ci_gitleaks.py"), "--tree", str(wt), "--history-base", base, "--require-history"],
            cwd=MAIN, env=env_clean).returncode
    finally:
        subprocess.run(["git", "-C", str(MAIN), "worktree", "remove", "--force", str(wt)])
    results["negative control (main's copy)"] = run(sys.executable, "-I", MAIN / "tests" / "leakcheck" / "negative_control.py",
                                                    "--private-dir", pdir)

    bad = {k: v for k, v in results.items() if v != 0}
    for k, v in results.items():
        say(f"{'ok  ' if v == 0 else 'FAIL'}  {k}: exit {v}")
    say(f"RESULT: {'GREEN' if not bad else 'RED'} ({len(results) - len(bad)}/{len(results)} passed); pr_text_sha256={digest(text)[:16]}")
    return 0 if not bad else (2 if 2 in bad.values() and 1 not in bad.values() else 1)


if __name__ == "__main__":
    sys.exit(main())
