#!/usr/bin/env python3
"""path_snapshot — capture the repo's file layout, then DERIVE a to-from map across a move.

WHY THIS EXISTS (2026-08-17). A directory reorganisation breaks every hardcoded path
that names the moved tree. The usual remedy is a hand-written to-from list, which has
two defects: somebody has to author it (so it is incomplete exactly where the move was
untidy), and it records INTENT rather than what happened (so a file that was moved
somewhere unexpected is described wrongly, confidently).

This derives the map instead. Snapshot before, snapshot after, and a file that appears
at a new path with an IDENTICAL CONTENT HASH *is* a move — proven, not asserted. Nobody
has to remember what they did.

    python3 scripts/path_snapshot.py --out docs/moves/before.json
    …reorganise…
    python3 scripts/path_snapshot.py --out docs/moves/after.json
    python3 scripts/path_snapshot.py --diff docs/moves/before.json docs/moves/after.json \
        --out docs/moves/to-from.json --markdown docs/moves/TO-FROM.md

⛔ WHAT IT DELIBERATELY DOES NOT CLAIM. It finds files that MOVED. It cannot find a
reference that broke, because a reference may be assembled at runtime from pieces
(`os.path.join(root, "defects", "defect-graph.lts")`) and no grep for the literal will
see it. Measured the same day this was written: ONE subsystem held FIVE independent
copies of one path literal, four of them invisible until the target vanished. Treat the
to-from map as the input to a sweep, never as the sweep's result.

⚠ AMBIGUITY IS REPORTED, NOT RESOLVED. Two files with identical content (an empty
`__init__.py`, a duplicated fixture) cannot be told apart by hash. Those land in
`ambiguous` with every candidate listed, rather than being silently paired off — a
wrong pairing in a to-from map is worse than a gap, because the gap gets investigated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Dict, List

# Directories never worth hashing: version-control internals, build caches, and virtual
# environments. `.git` especially — it is 1.3 GiB here and its churn is not layout.
SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".cache", ".DS_Store", "site-packages",
}
# Hash cap. A file larger than this is recorded by size+mtime only; content identity for
# a 200 MB blender model is not worth the read, and layout is what we are tracking.
MAX_HASH_BYTES = 8 * 1024 * 1024


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: str) -> dict:
    root = os.path.abspath(root)
    files: Dict[str, dict] = {}
    skipped_big = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rec = {"size": st.st_size, "mtime": int(st.st_mtime)}
            if st.st_size <= MAX_HASH_BYTES:
                try:
                    rec["sha256"] = _sha256(full)
                except OSError:
                    rec["sha256"] = None
            else:
                rec["sha256"] = None
                skipped_big += 1
            files[rel] = rec
    return {
        "schema": "path_snapshot/1",
        "root": root,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": _git_head(root),
        "git_branch": _git_branch(root),
        "file_count": len(files),
        "unhashed_oversize": skipped_big,
        "skip_dirs": sorted(SKIP_DIRS),
        "max_hash_bytes": MAX_HASH_BYTES,
        "files": files,
    }


def _git(root: str, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", root, *args], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 — a snapshot outside git is still a snapshot
        return ""


def _git_head(root: str) -> str:
    return _git(root, "rev-parse", "HEAD")


def _git_branch(root: str) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD")


def diff(before: dict, after: dict) -> dict:
    b, a = before["files"], after["files"]
    same_path = set(b) & set(a)
    gone = {p: b[p] for p in set(b) - set(a)}
    new = {p: a[p] for p in set(a) - set(b)}

    # index the disappeared files by content
    by_hash: Dict[str, List[str]] = {}
    for p, rec in gone.items():
        if rec.get("sha256"):
            by_hash.setdefault(rec["sha256"], []).append(p)

    moves: List[dict] = []
    ambiguous: List[dict] = []
    claimed = set()
    for np_, rec in sorted(new.items()):
        h = rec.get("sha256")
        if not h or h not in by_hash:
            continue
        cands = [c for c in by_hash[h] if c not in claimed]
        if not cands:
            continue
        if len(cands) == 1 and len([n for n, r in new.items()
                                    if r.get("sha256") == h]) == 1:
            claimed.add(cands[0])
            moves.append({"from": cands[0], "to": np_, "sha256": h,
                          "size": rec["size"]})
        else:
            ambiguous.append({"to": np_, "sha256": h, "candidates": sorted(cands),
                              "why": "identical content at multiple paths — a hash "
                                     "cannot distinguish them; resolve by hand"})

    moved_from = {m["from"] for m in moves}
    moved_to = {m["to"] for m in moves}
    modified = sorted(p for p in same_path
                      if b[p].get("sha256") != a[p].get("sha256"))
    return {
        "schema": "path_snapshot_diff/1",
        "before": {k: before.get(k) for k in
                   ("captured_at", "git_head", "git_branch", "file_count")},
        "after": {k: after.get(k) for k in
                  ("captured_at", "git_head", "git_branch", "file_count")},
        "moves": sorted(moves, key=lambda m: m["from"]),
        "ambiguous": ambiguous,
        "deleted": sorted(p for p in gone if p not in moved_from),
        "added": sorted(p for p in new if p not in moved_to),
        "modified_in_place": modified,
        "summary": {
            "moved": len(moves),
            "ambiguous": len(ambiguous),
            "deleted": len([p for p in gone if p not in moved_from]),
            "added": len([p for p in new if p not in moved_to]),
            "modified_in_place": len(modified),
        },
    }


def to_markdown(d: dict) -> str:
    s = d["summary"]
    out = [
        "# Path to-from map — DERIVED, not authored",
        "",
        f"> Before: `{d['before']['git_head'][:8]}` on `{d['before']['git_branch']}`, "
        f"{d['before']['captured_at']}, {d['before']['file_count']} files  ",
        f"> After:  `{d['after']['git_head'][:8]}` on `{d['after']['git_branch']}`, "
        f"{d['after']['captured_at']}, {d['after']['file_count']} files",
        "",
        "Every row below is a file whose CONTENT HASH is identical at a new path. That is "
        "proof of a move, not an inference about one.",
        "",
        "⛔ **This map lists files that MOVED. It does NOT list references that BROKE.** A "
        "path assembled at runtime from pieces is invisible to any search for the literal "
        "— measured the day this tool was written, one subsystem held five independent "
        "copies of a single path and four were undetectable until the target vanished. "
        "Use this as the INPUT to a reference sweep.",
        "",
        f"**moved {s['moved']} · ambiguous {s['ambiguous']} · added {s['added']} · "
        f"deleted {s['deleted']} · modified-in-place {s['modified_in_place']}**",
        "",
    ]
    if d["moves"]:
        out += ["## Moves", "", "| from | to |", "|---|---|"]
        out += [f"| `{m['from']}` | `{m['to']}` |" for m in d["moves"]]
        out += [""]
    if d["ambiguous"]:
        out += ["## ⚠ Ambiguous — resolve by hand", "",
                "Identical content at more than one path; a hash cannot tell them apart, "
                "and guessing would put a confident wrong row in a reference map.", "",
                "| appeared at | candidate origins |", "|---|---|"]
        out += [f"| `{x['to']}` | {', '.join('`'+c+'`' for c in x['candidates'])} |"
                for x in d["ambiguous"]]
        out += [""]
    for key, title in (("deleted", "Deleted (no content match anywhere)"),
                       ("added", "Added (new content, not a move)")):
        if d[key]:
            out += [f"## {title}", ""] + [f"- `{p}`" for p in d[key][:200]]
            if len(d[key]) > 200:
                out += [f"- … and {len(d[key]) - 200} more"]
            out += [""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo root to snapshot (default: cwd)")
    ap.add_argument("--out", help="write JSON here")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"),
                    help="derive the to-from map from two snapshots")
    ap.add_argument("--markdown", help="also write a human-readable map here")
    args = ap.parse_args()

    if args.diff:
        before = json.load(open(args.diff[0]))
        after = json.load(open(args.diff[1]))
        d = diff(before, after)
        payload = json.dumps(d, indent=2)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            open(args.out, "w").write(payload + "\n")
            print(f"to-from map -> {args.out}")
        else:
            print(payload)
        if args.markdown:
            os.makedirs(os.path.dirname(os.path.abspath(args.markdown)), exist_ok=True)
            open(args.markdown, "w").write(to_markdown(d) + "\n")
            print(f"markdown    -> {args.markdown}")
        s = d["summary"]
        print(f"  moved {s['moved']} · ambiguous {s['ambiguous']} · added {s['added']} "
              f"· deleted {s['deleted']} · modified-in-place {s['modified_in_place']}")
        if s["ambiguous"]:
            print("  ⚠ ambiguous rows need a human — identical content at several paths")
        return 0

    snap = snapshot(args.root)
    payload = json.dumps(snap, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        open(args.out, "w").write(payload + "\n")
        print(f"snapshot -> {args.out}")
    else:
        print(payload)
    print(f"  {snap['file_count']} files · head {snap['git_head'][:8]} "
          f"on {snap['git_branch']} · {snap['unhashed_oversize']} oversize unhashed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
