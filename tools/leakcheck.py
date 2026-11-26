#!/usr/bin/env python3
"""ComposeIO's leak check: the PR-shaped and tree-shaped front end over the two ported gates.

    leakcheck.py diff --base REF [--head REF] [--repo DIR] [--pr-text FILE] [--private-dir DIR] [--report FILE]
    leakcheck.py tree [--repo DIR] [--rev REF] [--private-dir DIR] [--report FILE]
        exit 0 green; 1 red or refused (the guard fails closed); 2 the check could not run (bad ref, not a git repo)

WHY A FRONT END AND NOT A NEW CHECK. The two gates are FactLattice's, ported unchanged: `tools/ci_gitleaks.py` (secrets) and
`tools/disclosure_guard.py --publication` (private terms; mechanism public, vocabulary private). This file decides only WHAT
they are pointed at, so every rule, control and fail-closed behaviour stays the guard's own.

WHAT `diff` SCANS. FactLattice runs its term scan over an export tree at publication. ComposeIO publishes on every merge, and
its public history already holds lines the vocabulary blocks, so a whole-tree scan is red on every PR for reasons the PR did
not cause (the gate the guard's own docstring warns gets switched off). `diff` therefore builds a scratch tree holding ONLY
what the PR adds, and runs `--publication` on it:
  - every line added in merge-base(BASE, HEAD)..HEAD, written at its own line number in a file at its own path (so a finding
    reads `path:line` of the real file, and a rule that matches a PATH fires on an added or renamed file);
  - an added binary file, whole (the guard decodes it with replacement and also matches its path);
  - `_leakcheck_pr/commit-messages.txt`: the message of every commit in the range (a leak in a commit message is published too);
  - `_leakcheck_pr/pr-text.txt`: the PR title and body, when --pr-text names a file holding them.
WHAT `tree` SCANS: `git archive` of one revision, whole (the shape of a snapshot before it lands; step 5 of the cutover).

PUBLISH-EXCLUDE IS A REFUSAL HERE, NOT A SKIP. The guard skips the paths PUBLISH-EXCLUDE names. In a repository that IS the
publication, a skip would let a PR hide a file by naming it there, so neither mode copies PUBLISH-EXCLUDE into the scanned
tree under its own name (its content is scanned as `PUBLISH-EXCLUDE.scanned`), and an added (diff) or present (tree) path
that an entry of BASE's or HEAD's PUBLISH-EXCLUDE names is a finding of its own, `publish-exclude-path`. The file then
records, with a reason per line, what never crosses into this repository.

THE VOCABULARY. --private-dir, else $COMPOSEIO_LEAKCHECK_PRIVATE, else `<repo>/private/` (gitignored). The two files are the
guard's: `publish-vocabulary.tsv` and `disclosure-denylist.tsv`. They are passed to the guard through its own environment
variables; a missing or empty one is the guard's refusal (exit 1), never a pass. In CI the vocabulary exists only on the
self-hosted runner (merge-queue.yml), so it never reaches a GitHub secret, a hosted runner or a public log: the guard prints
rule NAMES and `path:line`, never a pattern, a control or the matched text.

KNOWN LIMITS. A lexical tripwire catches only the terms somebody wrote down. The guard exempts its own file
(tools/disclosure_guard.py) from the scan by design; a change to that file is read by a person.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
GUARD = HERE / "disclosure_guard.py"
VOCAB, DENY = "publish-vocabulary.tsv", "disclosure-denylist.tsv"
EXCLUDE = "PUBLISH-EXCLUDE"
PR_DIR = "_leakcheck_pr"
UNSAFE_PATH = re.compile(r'["\\\x00-\x1f\x7f]')
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class CouldNotRun(Exception):
    pass


def git(repo, *args, text=True):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text)
    if p.returncode != 0:
        err = p.stderr if text else p.stderr.decode(errors="replace")
        raise CouldNotRun(f"git {' '.join(args[:3])}: {err.strip()}")
    return p.stdout


def rev(repo, ref):
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except CouldNotRun:
        raise CouldNotRun(f"{ref!r} does not resolve to a commit (fetch-depth 0 needed)") from None


def private_dir(repo, given):
    d = given or os.environ.get("COMPOSEIO_LEAKCHECK_PRIVATE") or str(pathlib.Path(repo) / "private")
    return pathlib.Path(d).expanduser().resolve()


def exclude_entries(repo, revision):
    """-> the PUBLISH-EXCLUDE entries at `revision` (none when the file is absent). Same grammar as the guard's loader."""
    try:
        text = git(repo, "show", f"{revision}:{EXCLUDE}")
    except CouldNotRun:
        return []
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#PENDING "):
            continue  # undecided: the guard scans it, so it refuses nothing here either
        if not line or line.startswith("#"):
            continue
        path = line.partition("#")[0].strip()
        if path:
            out.append(path)
    return out


def excluded(rel, entries):
    return any(rel.startswith(e) if e.endswith("/") else rel == e for e in entries)


def write_at(root, rel, lines):
    """Write {lineno: text} as a file whose line N holds the added line N and whose other lines are blank."""
    dst = root / ("PUBLISH-EXCLUDE.scanned" if rel == EXCLUDE else rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    top = max(lines) if lines else 0
    dst.write_text("".join((lines.get(n, "") + "\n") for n in range(1, top + 1)), encoding="utf-8", errors="surrogateescape")


def build_diff_tree(repo, base, head, root, pr_text):
    """-> (added paths, commit count). Fills `root` with what the range adds (see the module docstring)."""
    mb = git(repo, "merge-base", base, head).strip()
    names = git(repo, "diff", "--name-status", "-z", "-M", "--diff-filter=ACMRT", mb, head)
    parts = [p for p in names.split("\0") if p]
    added_paths, i = [], 0
    while i < len(parts):
        status = parts[i]
        if status[0] in "RC":
            added_paths.append(parts[i + 2])
            i += 3
        else:
            added_paths.append(parts[i + 1])
            i += 2
    numstat = git(repo, "diff", "--numstat", "-z", "-M", "--diff-filter=ACMRT", mb, head)
    binary = set()
    toks = [t for t in numstat.split("\0")]
    j = 0
    while j < len(toks):
        t = toks[j]
        if not t:
            j += 1
            continue
        fields = t.split("\t")
        if len(fields) == 3 and fields[2] == "":  # a rename: the two paths follow as their own tokens
            path = toks[j + 2]
            j += 3
        else:
            path = fields[2]
            j += 1
        if fields[0] == "-" and fields[1] == "-":
            binary.add(path)
    diff = git(repo, "-c", "core.quotePath=false", "diff", "--unified=0", "--no-color", "--no-ext-diff", "--no-textconv", "-M",
               "--diff-filter=ACMRT", "--src-prefix=a/", "--dst-prefix=b/", mb, head)
    per_file, path, n = {}, None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
            if path is not None:
                per_file.setdefault(path, {})
        elif raw.startswith("@@"):
            m = HUNK.match(raw)
            n = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path is not None:
            per_file[path][n] = raw[1:]
            n += 1
    for rel in added_paths:
        if rel in binary:
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(git(repo, "show", f"{head}:{rel}", text=False))
        elif rel not in per_file and UNSAFE_PATH.search(rel):
            # git quotes such a path in the diff header, so its added lines were not attributed above: scan the whole file
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(git(repo, "show", f"{head}:{rel}", text=False))
        else:
            write_at(root, rel, per_file.get(rel, {}))  # a pure rename adds no line: the empty file still carries its path
    log = git(repo, "log", "--format=commit %H%n%B", f"{mb}..{head}")
    count = len(git(repo, "rev-list", f"{mb}..{head}").split())
    (root / PR_DIR).mkdir(exist_ok=True)
    (root / PR_DIR / "commit-messages.txt").write_text(log + "\n", encoding="utf-8")
    if pr_text:
        (root / PR_DIR / "pr-text.txt").write_text(pathlib.Path(pr_text).read_text(encoding="utf-8") + "\n", encoding="utf-8")
    return added_paths, count, mb


def build_full_tree(repo, revision, root):
    """-> every path of `revision`, exported with `git archive` into root (PUBLISH-EXCLUDE renamed so it is scanned, not obeyed)."""
    tar = root.parent / "export.tar"
    with open(tar, "wb") as fh:
        p = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", revision], stdout=fh, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise CouldNotRun(f"git archive {revision}: {p.stderr.decode(errors='replace').strip()}")
    with tarfile.open(tar) as t:
        for m in t.getmembers():
            rel = pathlib.PurePosixPath(m.name)
            if rel.is_absolute() or ".." in rel.parts:
                raise CouldNotRun(f"git archive {revision}: unsafe member name {m.name!r}")
            dst = root.joinpath(*rel.parts)
            if m.isdir():
                dst.mkdir(parents=True, exist_ok=True)
            elif m.issym() or m.islnk():
                # a link ships as its target STRING (an absolute target is itself a disclosure): write it as text, never follow it
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(m.linkname + "\n", encoding="utf-8", errors="surrogateescape")
            elif m.isfile():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(t.extractfile(m).read())
    if (root / EXCLUDE).exists():
        (root / EXCLUDE).rename(root / "PUBLISH-EXCLUDE.scanned")
    return [x for x in git(repo, "ls-tree", "-r", "-z", "--name-only", revision).split("\0") if x]


def run_guard(root, pdir, report):
    env = dict(os.environ)
    env["FACTLATTICE_PUBLISH_VOCAB"] = str(pdir / VOCAB)
    env["FACTLATTICE_DISCLOSURE_RULES"] = str(pdir / DENY)
    for k in ("FACTLATTICE_NEUTRALITY_TERMS", "FACTLATTICE_NEUTRALITY_CONTEXTS"):
        env.pop(k, None)
    cmd = [sys.executable, "-I", str(GUARD), "--publication", str(root), "--report", str(report)]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    try:
        doc = json.loads(pathlib.Path(report).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"result": "refused", "exit_code": p.returncode or 1, "findings": [], "refusals": ["no report written"]}
    return p.returncode, doc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    d = sub.add_parser("diff")
    d.add_argument("--base", required=True)
    d.add_argument("--head", default="HEAD")
    d.add_argument("--pr-text")
    t = sub.add_parser("tree")
    t.add_argument("--rev", default="HEAD")
    for s in (d, t):
        s.add_argument("--repo", default=".")
        s.add_argument("--private-dir")
        s.add_argument("--report", help="write the combined JSON report here")
        s.add_argument("--keep", action="store_true", help="keep the scratch scan tree and print its path")
    a = ap.parse_args(argv)
    repo = pathlib.Path(a.repo).resolve()
    pdir = private_dir(repo, a.private_dir)
    work = pathlib.Path(tempfile.mkdtemp(prefix="leakcheck-"))
    root = work / "scan"
    root.mkdir()
    try:
        if a.mode == "diff":
            base, head = rev(repo, a.base), rev(repo, a.head)
            paths, ncommits, mb = build_diff_tree(repo, base, head, root, a.pr_text)
            entries = sorted(set(exclude_entries(repo, base)) | set(exclude_entries(repo, head)))
            scope = f"diff {mb[:12]}..{head[:12]}: {ncommits} commit(s), {len(paths)} added or changed path(s)"
        else:
            r = rev(repo, a.rev)
            paths = build_full_tree(repo, r, root)
            entries = exclude_entries(repo, r)
            scope = f"tree {r[:12]}: {len(paths)} path(s)"
    except CouldNotRun as e:
        print(f"[leakcheck] COULD NOT RUN: {e}")
        return 2
    print(f"[leakcheck] {scope}; private dir: {'present' if pdir.is_dir() else 'MISSING'}", flush=True)
    rc, doc = run_guard(root, pdir, work / "guard-report.json")
    held = [p for p in paths if excluded(p, entries)]
    for p in held:
        print(f"  {p}:0  [publish-exclude-path]")
    if held:
        print(f"[leakcheck] RED: {len(held)} path(s) named in PUBLISH-EXCLUDE are in this {a.mode}; they never cross into this repository")
    result = doc.get("result", "refused")
    if held and result == "green":
        result = "red"
    final = 0 if (rc == 0 and not held) else 1
    if a.report:
        out = {"schema": "composeio-leakcheck-report/1", "mode": a.mode, "scope": scope, "result": result, "exit_code": final,
               "guard": doc, "publish_exclude_findings": [{"path": p, "line": 0, "rule": "publish-exclude-path"} for p in held]}
        pathlib.Path(a.report).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    nfind = len(doc.get("findings", [])) + len(held)
    print(f"[leakcheck] RESULT: {result.upper()} ({nfind} finding(s)), exit {final}")
    if a.keep:
        print(f"[leakcheck] scan tree kept at {root}")
    return final


if __name__ == "__main__":
    sys.exit(main())
