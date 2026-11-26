#!/usr/bin/env python3
"""Release gate CLEANUP (docs/leakcheck/PUBLISHING.md): a push adds no path that must never be published.

    cleanup_check.py --base REF [--head REF] [--repo DIR] [--scope GLOB ...]
        exit 0 clean; 1 a violation (each printed); 2 the check could not run (bad ref, not a git repo)

Over merge-base(BASE, HEAD)..HEAD it refuses:
  publish-exclude  an added or changed path that an entry of BASE's or HEAD's PUBLISH-EXCLUDE names;
  generated        a generated or scratch file: anything under .qa/, __pycache__/, .venv/ or a private/ folder,
                   a file whose name contains `.bak`, or a re-rendered python/tests/render/captures/*.png;
  out-of-scope     with --scope (one or more fnmatch globs, the scope the brief declares), any changed path,
                   deletions included, that matches none of them.
It reads git only; it never changes the tree.
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
GENERATED = [
    (re.compile(r"(^|/)\.qa/"), ".qa/ scratch"),
    (re.compile(r"(^|/)__pycache__/"), "__pycache__/"),
    (re.compile(r"(^|/)\.venv/"), ".venv/"),
    (re.compile(r"(^|/)private/"), "a private/ folder"),
    (re.compile(r"(^|/)[^/]*\.bak[^/]*$"), "a *.bak* file"),
    (re.compile(r"^python/tests/render/captures/[^/]+\.png$"), "a re-rendered capture"),
]


def _leakcheck():
    spec = importlib.util.spec_from_file_location("_composeio_leakcheck", HERE / "leakcheck.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def changed(repo, mb, head):
    """-> [(status, path)] for merge-base..head; a rename or copy contributes its new path (and a rename its old one as D)."""
    out = subprocess.run(["git", "-C", str(repo), "diff", "--name-status", "-z", "-M", mb, head],
                         capture_output=True, text=True, check=True).stdout
    parts, rows, i = [p for p in out.split("\0") if p], [], 0
    while i < len(parts):
        st = parts[i]
        if st[0] in "RC":
            if st[0] == "R":
                rows.append(("D", parts[i + 1]))
            rows.append((st[0], parts[i + 2]))
            i += 3
        else:
            rows.append((st[0], parts[i + 1]))
            i += 2
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--scope", nargs="+", help="fnmatch globs of the scope the brief declares")
    a = ap.parse_args(argv)
    lc = _leakcheck()
    repo = pathlib.Path(a.repo).resolve()
    try:
        base, head = lc.rev(repo, a.base), lc.rev(repo, a.head)
        mb = lc.git(repo, "merge-base", base, head).strip()
        rows = changed(repo, mb, head)
    except (lc.CouldNotRun, subprocess.CalledProcessError) as e:
        print(f"[cleanup] COULD NOT RUN: {e}")
        return 2
    entries = sorted(set(lc.exclude_entries(repo, base)) | set(lc.exclude_entries(repo, head)))
    bad = []
    for st, p in rows:
        if st != "D" and lc.excluded(p, entries):
            bad.append((p, "publish-exclude", "named in PUBLISH-EXCLUDE"))
        if st != "D":
            for rx, what in GENERATED:
                if rx.search(p):
                    bad.append((p, "generated", what))
        if a.scope and not any(fnmatch.fnmatch(p, g) for g in a.scope):
            bad.append((p, "out-of-scope", f"{st}: outside the declared scope"))
    for p, kind, why in bad:
        print(f"  {p}  [{kind}] {why}")
    print(f"[cleanup] {mb[:12]}..{head[:12]}: {len(rows)} changed path(s), {len(bad)} violation(s)"
          + (f", scope {a.scope}" if a.scope else ", no scope declared"))
    print(f"[cleanup] RESULT: {'RED' if bad else 'GREEN'}, exit {1 if bad else 0}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
