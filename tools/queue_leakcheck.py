#!/usr/bin/env python3
"""The merge queue's self-hosted leak-check job, as one script that runs from MAIN's checkout (merge-queue.yml, `leakcheck`).

    queue_leakcheck.py --head SHA (--pr N --repo OWNER/NAME | --pr-text FILE) [--private-dir DIR] [--output FILE] [--local-probe]
        exit 0 every check passed; 1 a check failed or refused; 2 a check could not run
    queue_leakcheck.py --print-digest (--pr N --repo OWNER/NAME | --pr-text FILE)
        exit 0 and print ONLY the sha256 of the PR text read now; 3 (EXIT_UNREADABLE) the text could not be read, and
        nothing is printed on stdout. The status job's re-read; it sends GH_TOKEN when set (a hosted runner's
        unauthenticated read can fail). The PR text itself is never printed.

Every tool it runs is MAIN's copy (this checkout), never the PR's: the PR head is only fetched and read as data, so a PR that
edits tools/ci_gitleaks.py, tools/leakcheck.py, the guard or the negative control does not decide its own result. In order:
  1. tools/runner_preconditions.py --verify-record (interaction limit, fork-PR approval policy): the job cannot read
     those settings itself (no login on the runner, GITHUB_TOKEN lacks admin read), so it judges the dated record the
     maintainer's Mac writes to the Actions variable RUNNER_PRECONDITIONS, which the workflow passes in through `vars`.
     Missing, stale, failing or unreadable -> refuse; no scan is run.
     --local-probe skips this step, LOUDLY; the workflow never passes it.
  2. the head must contain main's tip (the PR is up to date), so the head tested is the tree that lands;
  3. the PR title and body are read (from the public API, no token) and written to a file;
  4. tools/leakcheck.py diff over main..head, with that text and the private vocabulary;
  5. tools/ci_gitleaks.py (main's copy) over a detached worktree of the head and the history main..head;
  6. tests/leakcheck/negative_control.py with the private vocabulary (every rule must still fire);
  7. the title and body are read AGAIN and, if they changed, scanned again; the sha256 of the text last scanned is written
     to --output as `pr_text_sha256`, so the job that posts `merge-queue/tested` can refuse when the text it re-reads then differs.
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


EXIT_UNREADABLE = 3  # --print-digest only: the PR text could not be read (argparse's own usage error is 2)


def say(msg, file=None):
    print(f"[queue-leakcheck] {msg}", file=file or sys.stdout, flush=True)


def run(*cmd, cwd=MAIN):
    say("run: " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], cwd=cwd).returncode


def pr_text_from_api(repo, number, token=None):
    """-> 'title\\n\\nbody\\n' read from the public REST API. The leakcheck job passes no token (the repository is public);
    the status job's re-read (--print-digest) passes GH_TOKEN, because a hosted runner's unauthenticated read can fail."""
    url = f"https://api.github.com/repos/{repo}/pulls/{int(number)}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "composeio-queue-leakcheck"}
    req = urllib.request.Request(url, headers=headers)
    if token:
        # unredirected: urllib copies ordinary headers onto a redirected request (even to another host); never this one
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        pr = json.load(r)
    return pr_text(pr)


def pr_text(pr):
    """The ONE rendering of a PR's text, shared with the status job (pr_text_digest)."""
    return (pr.get("title") or "") + "\n\n" + (pr.get("body") or "") + "\n"


def read_pr_text(pr_text_file, repo, number):
    """The leakcheck job's read (steps 3 and 7): a --pr-text file, or the public API with NO token, even when GH_TOKEN is
    in the environment. The self-hosted runner holds no token for this read, and must not start sending one."""
    if pr_text_file:
        return pathlib.Path(pr_text_file).read_text(encoding="utf-8")
    return pr_text_from_api(repo, number)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--head")
    ap.add_argument("--print-digest", action="store_true",
                    help="only print the sha256 of the PR text as read now (the status job's re-read; needs --pr and --repo, or --pr-text)")
    ap.add_argument("--pr")
    ap.add_argument("--repo")
    ap.add_argument("--pr-text", help="a file holding the PR title and body (local probes only; the workflow reads the API)")
    ap.add_argument("--private-dir")
    ap.add_argument("--output", help="append key=value lines here (GITHUB_OUTPUT)")
    ap.add_argument("--local-probe", action="store_true", help="skip the runner preconditions (local probes only)")
    a = ap.parse_args(argv)
    if not a.pr_text and not (a.pr and a.repo):
        ap.error("--pr and --repo, or --pr-text, are required")
    if a.print_digest:
        # Any failure to read is EXIT_UNREADABLE with nothing on stdout, so the caller can never take an error message (or
        # PR text) for a digest, nor a failed read for a changed text. Only the exception's class name is logged, on stderr.
        try:
            if a.pr_text:
                t = pathlib.Path(a.pr_text).read_text(encoding="utf-8")
            else:
                t = pr_text_from_api(a.repo, a.pr, token=os.environ.get("GH_TOKEN") or None)
        except Exception as e:  # noqa: BLE001 -- every read failure is the same answer: unreadable
            say(f"COULD NOT READ the PR text ({type(e).__name__}): unreadable, not known to have changed", file=sys.stderr)
            return EXIT_UNREADABLE
        print(digest(t))
        return 0
    if not a.head:
        ap.error("--head is required")
    pdir = a.private_dir or os.environ.get("COMPOSEIO_LEAKCHECK_PRIVATE") or str(pathlib.Path(tempfile.gettempdir()) / "no-private-vocabulary")
    work = pathlib.Path(tempfile.mkdtemp(prefix="queue-leakcheck-"))

    if a.local_probe:
        say("NOTE: --local-probe: the runner preconditions were NOT checked (never passed by the workflow)")
    else:
        rc = run(sys.executable, "-I", TOOLS / "runner_preconditions.py", "--verify-record", "--repo", a.repo or "740Dave/composeio")
        if rc != 0:
            say(f"REFUSED: the recorded runner preconditions do not hold or could not be read (exit {rc}); no scan was run. "
                "Re-run the check on the maintainer's Mac; deregister the runner if a setting has lapsed.")
            return 1

    base = subprocess.run(["git", "-C", str(MAIN), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if subprocess.run(["git", "-C", str(MAIN), "merge-base", "--is-ancestor", base, a.head]).returncode != 0:
        say("REFUSED: the head does not contain main's tip: update the branch, then re-queue")
        return 1

    def read_text():
        return read_pr_text(a.pr_text, a.repo, a.pr)

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

    try:
        again = read_text()
    except (OSError, urllib.error.URLError, ValueError) as e:
        say(f"COULD NOT RUN: cannot re-read the PR text ({type(e).__name__})")
        return 2
    if again != text:
        say("the PR title or body changed during the run: scanning the new text")
        tfile.write_text(again, encoding="utf-8")
        results["leakcheck diff, re-read PR text"] = run(sys.executable, "-I", TOOLS / "leakcheck.py", "diff", "--repo", MAIN, "--base", base,
                                                         "--head", a.head, "--pr-text", tfile, "--private-dir", pdir)
    if a.output:
        with open(a.output, "a") as fh:
            fh.write(f"pr_text_sha256={digest(again)}\n")

    bad = {k: v for k, v in results.items() if v != 0}
    for k, v in results.items():
        say(f"{'ok  ' if v == 0 else 'FAIL'}  {k}: exit {v}")
    say(f"RESULT: {'GREEN' if not bad else 'RED'} ({len(results) - len(bad)}/{len(results)} passed); pr_text_sha256={digest(again)[:16]}")
    return 0 if not bad else (2 if 2 in bad.values() and 1 not in bad.values() else 1)


if __name__ == "__main__":
    sys.exit(main())
