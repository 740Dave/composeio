#!/usr/bin/env python3
"""branch-hygiene — thin reporter over the NATIVE worktrunk landed oracle.

WHY THIS EXISTS (rewritten 2026-08-26 for the worktrunk model)
    The old classifier re-implemented a SUBSET of worktrunk's landed-detection
    (a byte-restricted trees-match + a patch-id `git cherry`) and disagreed with
    wt by construction — the mechanical cause of the "contradictory audits".
    The rebase-ff land model (`wt merge`) makes a landed commit a literal
    ANCESTOR of trunk, so the oracle is now NATIVE and needs no heuristic:

      * `git merge-base --is-ancestor <tip> origin/main`  -> landed via rebase-ff
      * `wt list` `main_state == "integrated"`  (the `⊂` marker)  -> landed via
        DIFFERENT history (a legacy cherry-pick/squash `--merged` cannot see)

    This file is now a THIN reporter over exactly those two natives. The
    patch-id/file-diff equivalence code is RETIRED. There is no CI, so the
    wake/dusk skills call this as the only enforcement surface — the loud-fail
    thresholds and exit codes are preserved verbatim.

    ⛔ `⚑` is NOT a "foreign worktree" marker (verified wt 0.74.0: it means
    duplicate-branch / branch-path mismatch). wt treats Claude-Code worktrees as
    ordinary; there is no foreign marker. So this reporter keys ONLY on the
    ancestor test and `main_state == "integrated"`.

SUBCOMMANDS
    audit [base]                     classify every local+remote ref vs base
    dirty-worktrees                  report worktrees holding uncommitted UNIQUE work
    orphan-origin-refs [base]        LIVE `git ls-remote` sweep for landed origin refs
                                     the dance left behind (MEDIUM, report-only)
    land-and-prune <branch> [base]   delete a branch IFF it is landed on base

EXIT STATUS (David-ratified thresholds, unchanged)
    audit exits NON-ZERO when either:
      * more than 10 stranded (unlanded) branches survive, OR
      * ANY landed / wt-agent-* branch older than 7 days survives un-pruned.
    On failure the STRANDED / STALE worklist is the failure message.

DEGRADED MODE
    If `wt` is not on PATH or `wt list` fails, the `⊂` cross-check is skipped and
    the report SAYS SO — the ancestor oracle (pure git) still runs and still
    gates. A skipped cross-check is stated, never silently treated as "clean".
"""
import json
import os
import subprocess
import sys
import time

MAX_UNLANDED = 10
STALE_DAYS = 7
PROTECTED = ("main", "origin/main", "origin/HEAD")

# wt may live under ~/.cargo/bin, which is not always on a hook's PATH.
_WT_PATH = os.environ.get("PATH", "") + os.pathsep + os.path.expanduser("~/.cargo/bin")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def out(*args):
    return git(*args).stdout.strip()


def is_ancestor(a, b):
    return git("merge-base", "--is-ancestor", a, b).returncode == 0


def _obj_present(sha):
    """True if `sha` is a commit object present in THIS clone (needed before any
    ancestor/patch-id test — an ls-remote SHA may not have been fetched)."""
    return git("cat-file", "-e", sha + "^{commit}").returncode == 0


def _cherry_all_landed(base, tip):
    """Patch-id oracle: True IFF every commit on `tip` not on `base` has a patch-id
    equivalent already in `base` — `git cherry base tip` emits ONLY `-` lines (or
    nothing, meaning `tip` has no unique commits and is a pure ancestor). This is
    the rebase-ff case: `wt merge` rebases the branch, so its origin ref keeps the
    PRE-rebase SHA (not an ancestor of the new main) while its content is on main by
    patch-id. Returns None if `git cherry` cannot run (e.g. missing objects) so the
    caller reports UNKNOWN rather than a false 'landed'. Report-only — never a
    delete trigger. NOT a diff-stat: patch-id equivalence, per the ratified oracle."""
    r = git("cherry", base, tip)
    if r.returncode != 0:
        return None
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not lines:
        return True                       # no unique commits -> pure ancestor
    return all(ln.startswith("-") for ln in lines)


def is_wt_agent(ref):
    return ref.startswith("wt-agent-") or ref.startswith("origin/wt-agent-")


def all_refs():
    txt = out("for-each-ref", "--format=%(refname:short)",
              "refs/heads/", "refs/remotes/origin/")
    return [r for r in txt.splitlines() if r]


def wt_integrated_set():
    """Short branch names wt reports as `main_state == "integrated"` (the `⊂`
    marker): content landed via DIFFERENT history. Best-effort — returns
    (set, note): note is None on success, else a human string explaining the
    skip so the report can state it rather than imply clean."""
    env = dict(os.environ, PATH=_WT_PATH)
    try:
        p = subprocess.run(
            ["wt", "list", "--branches", "--format", "json"],
            capture_output=True, text=True, env=env, timeout=60,
        )
    except FileNotFoundError:
        return set(), "wt not on PATH — `⊂` integrated cross-check SKIPPED"
    except subprocess.TimeoutExpired:
        return set(), "wt list timed out — `⊂` integrated cross-check SKIPPED"
    if p.returncode != 0:
        return set(), "wt list failed (rc=%d) — `⊂` cross-check SKIPPED" % p.returncode
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return set(), "wt list JSON unparseable — `⊂` cross-check SKIPPED"
    integ = set()
    for b in data:
        if b.get("main_state") == "integrated" and b.get("branch"):
            integ.add(b["branch"])
    return integ, None


def worktree_branches():
    """Short-names of branches checked out in ANY worktree — never -D these."""
    names = set()
    txt = out("worktree", "list", "--porcelain")
    for line in txt.splitlines():
        if line.startswith("branch refs/heads/"):
            names.add(line.split("refs/heads/", 1)[1].strip())
    return names


# ── DIRTY-WORKTREE SCAN — the axis the branch-oracle is structurally blind to ──
# The landed-oracle above reasons about COMMITTED history only. Uncommitted unique
# work in a worktree is invisible to it — worse, once that worktree's branch lands
# (or its content lands via other history) a `--merged` census reports the worktree
# as "landed cruft, prune me", and a forced prune HARD-DELETES the uncommitted work
# with no trash (verified: `.git/wt/trash/` stays empty; `refs/wt-backup` only ever
# holds tracked-swept-into-squash, never untracked). That is the exact WS-transport/
# render-dom loss this pass exists to surface BEFORE a prune can destroy it.
#
# Files that CHURN by design and are never unique work — excluded so the scan flags
# real WIP, not runtime noise. Kept in sync with .githooks/pre-commit's runtime set.
NEVER_COMMIT_CHURN = (
    ".mcp.json",       # per-worktree MCP seat, copied in, never landed
    "metrics.db",      # render/test metrics sqlite, runtime-written
)


def _is_churn(path):
    """True if `path` is known never-commit churn (excluded from unique-work)."""
    base = path.rsplit("/", 1)[-1]
    if base in NEVER_COMMIT_CHURN:
        return True
    # captures/*.png — the render capture harness re-dirties these constantly.
    if base.endswith(".png") and ("captures/" in path or "/captures" in path):
        return True
    return False


def _porcelain_files(wt_path):
    """Return (files, error): the list of changed/untracked paths in `wt_path`
    per `git -C <wt> status --porcelain`, with the known-churn set removed.
    `error` is a human string if the status call itself failed, else None.
    Ignored files never appear (no --ignored), so gitignored artifacts are already
    absent; the churn filter covers the tracked-modified / non-ignored-untracked
    ones that DO appear."""
    p = git("-C", wt_path, "status", "--porcelain")
    if p.returncode != 0:
        return [], "git status failed (rc=%d) in %s" % (p.returncode, wt_path)
    files = []
    for line in p.stdout.splitlines():
        if not line:
            continue
        # Porcelain v1: 'XY <path>' or 'XY <old> -> <new>' for renames/copies.
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if not path or _is_churn(path):
            continue
        files.append(path)
    return files, None


def _worktree_paths_via_wt():
    """(list-of-(path,branch,wt_says_dirty), note). Uses wt's OWN signal
    (`working_tree.modified`/`.untracked`/`.staged`/`.deleted`/`.renamed`) as the
    fast dirty flag; note explains any fallback. Returns note != None to signal the
    caller should fall back to `git worktree list`."""
    env = dict(os.environ, PATH=_WT_PATH)
    try:
        p = subprocess.run(
            ["wt", "list", "--format", "json"],
            capture_output=True, text=True, env=env, timeout=60,
        )
    except FileNotFoundError:
        return [], "wt not on PATH — enumerating worktrees via `git worktree list`"
    except subprocess.TimeoutExpired:
        return [], "wt list timed out — enumerating via `git worktree list`"
    if p.returncode != 0:
        return [], "wt list failed (rc=%d) — enumerating via `git worktree list`" % p.returncode
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return [], "wt list JSON unparseable — enumerating via `git worktree list`"
    rows = []
    for w in data:
        path = w.get("path")
        if not path:
            continue
        wtf = w.get("working_tree") or {}
        # wt's own booleans. If the field names ever drift, they read as False and
        # the per-worktree porcelain pass below is the authoritative backstop.
        wt_dirty = bool(wtf.get("modified") or wtf.get("untracked")
                        or wtf.get("staged") or wtf.get("deleted")
                        or wtf.get("renamed"))
        rows.append((path, w.get("branch") or "(detached)", wt_dirty))
    return rows, None


def _worktree_paths_via_git():
    """Fallback enumeration: (path, branch, None) from `git worktree list`."""
    rows = []
    cur_path = cur_branch = None
    for line in out("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            cur_path = line.split("worktree ", 1)[1].strip()
            cur_branch = "(detached)"
        elif line.startswith("branch refs/heads/"):
            cur_branch = line.split("refs/heads/", 1)[1].strip()
        elif line == "" and cur_path:
            rows.append((cur_path, cur_branch, None))
            cur_path = None
    if cur_path:
        rows.append((cur_path, cur_branch, None))
    return rows


def dirty_worktree_findings():
    """Every worktree holding uncommitted UNIQUE work (modified tracked or
    non-ignored untracked, excluding known churn) is a HIGH finding — it is
    exactly the loss a `--merged` prune would silently destroy. Returns
    (findings, note) where findings is a list of dicts and note is a human string
    about degraded enumeration (or None)."""
    rows, note = _worktree_paths_via_wt()
    if note is not None:          # wt unavailable/unusable — fall back to git
        rows = _worktree_paths_via_git()
    findings = []
    for path, branch, wt_dirty in rows:
        files, err = _porcelain_files(path)
        if err:
            findings.append({"path": path, "branch": branch, "files": [],
                             "count": 0, "error": err, "wt_dirty": wt_dirty})
            continue
        if files:
            findings.append({"path": path, "branch": branch, "files": files,
                             "count": len(files), "error": None,
                             "wt_dirty": wt_dirty})
    return findings, note


def show_dirty(findings, note):
    print("### DIRTY-WORKTREES — uncommitted UNIQUE work (HIGH — a prune would destroy it) (%d)"
          % len(findings))
    if note:
        print("   ⚠ " + note)
    if not findings:
        print("  (none — every worktree is clean or holds only known churn)")
    for f in findings:
        if f.get("error"):
            print("  %-58s %s  !! %s" % (f["path"], f["branch"], f["error"]))
            continue
        # wt's flag vs the authoritative porcelain result — show a disagreement.
        wt_note = ""
        if f.get("wt_dirty") is False:
            wt_note = "  (wt reported clean; porcelain found work)"
        print("  %-58s %s  %d file(s)%s" % (f["path"], f["branch"], f["count"], wt_note))
        for p in f["files"][:20]:
            print("      + %s" % p)
        if f["count"] > 20:
            print("      … +%d more" % (f["count"] - 20))
    print()


def is_landed(ref, tip, base_sha, integrated):
    """Landed IFF the tip is an ancestor of base (rebase-ff land) OR wt reports
    the branch integrated via different history (`⊂`)."""
    if is_ancestor(tip, base_sha):
        return True
    short = ref.split("origin/", 1)[1] if ref.startswith("origin/") else ref
    return short in integrated


def classify(ref, base_sha, integrated, now):
    tip = out("rev-parse", "--verify", ref + "^{commit}")
    if not tip:
        return None
    if is_ancestor(tip, base_sha):
        # tip is at or behind base and merged — nothing ahead, not cruft
        # (a rebase-ff landed branch is normally auto-deleted; if present it is
        # landed-unpruned, caught below only when it is ALSO ahead — here it is
        # a pure ancestor, so skip).
        return None
    landed = is_landed(ref, tip, base_sha, integrated)
    tipdate = int(out("log", "-1", "--format=%ct", tip) or 0)
    age = (now - tipdate) // 86400
    return {
        "ref": ref, "tip": tip[:9], "age": age,
        "kind": "landed" if landed else "stranded",
        "wt": is_wt_agent(ref),
        "stale": (now - tipdate) > STALE_DAYS * 86400,
    }


def show(title, items):
    print("### %s (%d)" % (title, len(items)))
    if not items:
        print("  (none)")
    for c in items:
        flag = "  [STALE]" if c["stale"] else ""
        print("  %-58s %s  %sd%s" % (c["ref"], c["tip"], c["age"], flag))
    print()


# ── ORPHANED-ORIGIN-REF SWEEP — the axis the LOCAL-ref audit is structurally blind to ──
# The main audit reads `refs/remotes/origin/*` — the STALE remote-tracking mirror. The
# worktrunk land dance's durability-floor auto-push creates an origin/<branch> ref; the
# dance tears down the LOCAL branch+worktree but (before 2026-09-02) NEVER deleted that
# origin ref, so lands leave orphaned origin heads that pile up (MEASURED: 51 accrued
# before a manual prune; the just-landed branch + 6 cleanup refs `push origin --delete`'d
# BY HAND, twice in one session). This sweep queries the LIVE remote via `git ls-remote`
# — authoritative about what is ACTUALLY on origin right now, unlike the tracking refs —
# and flags every head whose content is landed on origin/main (a proven ancestor, or every
# unique commit patch-id-present) as a MEDIUM prunable orphan. Report ONLY: deletion stays a
# human/git-expert action. Belt-and-suspenders to the dance's own origin --delete step; this
# catches what the dance misses (a land that errored after push but before the delete).


def _ls_remote_heads(remote="origin"):
    """(dict short-name -> sha, note). LIVE remote enumeration via `git ls-remote
    --heads`; note is a human string if the call failed (network/remote down), else
    None so a failure is stated, never read as 'no orphans'."""
    r = git("ls-remote", "--heads", remote)
    if r.returncode != 0:
        return {}, "git ls-remote %s failed (rc=%d) — orphan sweep SKIPPED" % (
            remote, r.returncode)
    heads = {}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        sha, ref = line.split("\t", 1)
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return heads, None


def orphan_origin_refs(base="origin/main", remote="origin"):
    """Sweep LIVE origin heads and classify each vs `base`. Returns
    (orphans, unlanded, unknown, note):
      orphans  — content landed on base (ancestor OR patch-id-present): prunable.
      unlanded — real unlanded work still on origin: LEAVE IT.
      unknown  — SHA not fetched locally / cherry indeterminate: cannot verify.
    MEDIUM, report-only — never auto-deletes."""
    heads, note = _ls_remote_heads(remote)
    base_sha = out("rev-parse", "--verify", base + "^{commit}")
    if not base_sha:
        return [], [], [], "cannot resolve base %s — orphan sweep SKIPPED" % base
    # base short-name (e.g. 'main' for 'origin/main') is never itself an orphan.
    base_short = base.split("origin/", 1)[1] if base.startswith("origin/") else base
    orphans, unlanded, unknown = [], [], []
    for name in sorted(heads):
        if name in ("main", "HEAD", base_short):
            continue
        sha = heads[name]
        row = {"branch": name, "sha": sha[:9]}
        if not _obj_present(sha):
            row["why"] = "origin SHA not fetched locally — cannot verify"
            unknown.append(row)
            continue
        if is_ancestor(sha, base_sha):
            row["why"] = "ancestor of %s (fully landed)" % base
            orphans.append(row)
            continue
        cherry = _cherry_all_landed(base_sha, sha)
        if cherry is True:
            row["why"] = "all unique commits patch-id-present on %s (rebase-ff landed)" % base
            orphans.append(row)
        elif cherry is None:
            row["why"] = "git cherry indeterminate — cannot verify"
            unknown.append(row)
        else:
            unlanded.append(row)
    return orphans, unlanded, unknown, note


def show_orphans(orphans, unlanded, unknown, note):
    print("### ORPHANED-ORIGIN-REFS — landed content left on origin (MEDIUM — prunable, report-only) (%d)"
          % len(orphans))
    if note:
        print("   ⚠ " + note)
    if not orphans:
        print("  (none — no landed origin head survives; the dance's origin --delete is holding)")
    for o in orphans:
        print("  origin/%-48s %s  %s" % (o["branch"], o["sha"], o["why"]))
    if orphans:
        print("  → prune (human/git-expert): git push origin --delete <branch>  (NEVER auto-deleted here)")
    if unlanded:
        print("  -- unlanded origin heads (real work — LEAVE) (%d):" % len(unlanded))
        for u in unlanded:
            print("       origin/%-45s %s" % (u["branch"], u["sha"]))
    if unknown:
        print("  -- UNKNOWN — could not verify (fetch, then re-run) (%d):" % len(unknown))
        for u in unknown:
            print("       origin/%-45s %s  %s" % (u["branch"], u["sha"], u["why"]))
    print()


def audit(base):
    base_sha = out("rev-parse", "--verify", base + "^{commit}")
    if not base_sha:
        print("branch-hygiene: cannot resolve base ref", base)
        return 2
    integrated, note = wt_integrated_set()
    now = int(time.time())
    stranded, landed, wtcruft, stale = [], [], [], []
    for ref in all_refs():
        if ref in PROTECTED:
            continue
        c = classify(ref, base_sha, integrated, now)
        if c is None:
            continue
        if c["kind"] == "stranded":
            stranded.append(c)
        elif c["wt"]:
            wtcruft.append(c)
        else:
            landed.append(c)
        if c["stale"] and c["kind"] == "landed":
            stale.append(c)
    print("== branch-hygiene :: base=%s (%s) :: NATIVE oracle ==" % (base, base_sha[:9]))
    print("   landed = ancestor of base (rebase-ff)  OR  wt `⊂` integrated")
    if note:
        print("   ⚠ " + note)
    print()
    show("STRANDED — NOT landed (real unlanded work)", stranded)
    show("LANDED-UNPRUNED — on base via history, prune me", landed)
    show("WT-AGENT-CRUFT — landed dispatch branches, prune me", wtcruft)
    # The axis the landed-oracle above cannot see: uncommitted unique work.
    dirty, dnote = dirty_worktree_findings()
    dirty_real = [f for f in dirty if not f.get("error")]
    show_dirty(dirty, dnote)
    # The axis the LOCAL-ref oracle cannot see: landed refs left on the LIVE remote.
    orphans, o_unlanded, o_unknown, onote = orphan_origin_refs(base)
    show_orphans(orphans, o_unlanded, o_unknown, onote)
    print("== totals: stranded=%d landed=%d wt-agent=%d stale=%d dirty-worktrees=%d orphan-origin=%d ==" % (
        len(stranded), len(landed), len(wtcruft), len(stale), len(dirty_real), len(orphans)))
    over = len(stranded) > MAX_UNLANDED
    fail = over or bool(stale) or bool(dirty_real)
    if over:
        print("\n!! FAIL: %d stranded exceed the limit of %d. WORKLIST:" % (
            len(stranded), MAX_UNLANDED))
        for c in stranded:
            print("   -", c["ref"], c["tip"])
    if stale:
        print("\n!! FAIL: %d landed/wt-agent older than %dd survive un-pruned:" % (
            len(stale), STALE_DAYS))
        for c in stale:
            print("   -", c["ref"], c["tip"], "%sd" % c["age"])
    if dirty_real:
        print("\n!! FAIL(HIGH): %d worktree(s) hold uncommitted UNIQUE work — COMMIT or"
              " land it BEFORE any prune; a `--merged` prune would hard-delete it (no trash):"
              % len(dirty_real))
        for f in dirty_real:
            print("   -", f["path"], "(%s)" % f["branch"], "%d file(s)" % f["count"])
    if orphans:
        # MEDIUM, report-only — does NOT change the ratified exit thresholds. Left as a
        # visible worklist for the human/git-expert to `push origin --delete`.
        print("\n?? MEDIUM: %d orphaned origin ref(s) hold landed content — prunable (report-only,"
              " NOT auto-deleted). Prune via git-expert / git push origin --delete <branch>:" % len(orphans))
        for o in orphans:
            print("   -", "origin/" + o["branch"], o["sha"])
    if fail:
        print("\nbranch-hygiene: DRIFT DETECTED — see worklist above.")
        return 1
    print("\nbranch-hygiene: CLEAN — no stranded overflow, no stale landed cruft, no dirty WIP.")
    if orphans:
        print("  (note: %d orphaned origin ref(s) reported above — MEDIUM, prune manually.)" % len(orphans))
    return 0


def dirty_worktrees_cmd():
    """Standalone: report ONLY the dirty-worktree axis; non-zero if any found."""
    findings, note = dirty_worktree_findings()
    real = [f for f in findings if not f.get("error")]
    print("== branch-hygiene :: dirty-worktree scan (wt signal + porcelain backstop) ==")
    show_dirty(findings, note)
    if real:
        print("branch-hygiene: %d dirty worktree(s) hold uncommitted UNIQUE work — "
              "commit/land before prune." % len(real))
        return 1
    print("branch-hygiene: CLEAN — no dirty WIP in any worktree.")
    return 0


def orphan_origin_refs_cmd(base):
    """Standalone: LIVE origin sweep for landed orphan refs. Report-only, so this
    NEVER returns non-zero on found orphans (deletion is a human action); it exits
    non-zero only when the sweep itself could not run (remote unreachable)."""
    orphans, unlanded, unknown, note = orphan_origin_refs(base)
    print("== branch-hygiene :: orphaned-origin-ref sweep :: base=%s (git ls-remote, LIVE) ==" % base)
    show_orphans(orphans, unlanded, unknown, note)
    if note:
        print("branch-hygiene: orphan sweep DEGRADED —", note)
        return 2
    if orphans:
        print("branch-hygiene: %d orphaned origin ref(s) hold landed content (MEDIUM, report-only). "
              "Prune via git push origin --delete <branch>." % len(orphans))
    else:
        print("branch-hygiene: CLEAN — no orphaned origin refs (the dance's origin --delete is holding).")
    return 0


def land_and_prune(branch, base):
    base_sha = out("rev-parse", "--verify", base + "^{commit}")
    tip = out("rev-parse", "--verify", branch + "^{commit}")
    if not tip:
        print("land-and-prune: no such branch:", branch)
        return 2
    if branch in worktree_branches():
        print("land-and-prune: SKIP", branch,
              "is checked out in a live worktree; not disturbing it.")
        return 3
    integrated, _ = wt_integrated_set()
    if not is_landed(branch, tip, base_sha, integrated):
        print("land-and-prune: REFUSING —", branch, "is NOT landed on", base)
        print("  (neither an ancestor nor wt `⊂` integrated). Land it first;")
        print("  this never deletes unlanded work.")
        return 1
    print("land-and-prune:", branch, "is landed on", base,
          "(ancestor or `⊂` integrated). Pruning.")
    # -D (not -d): an integrated-via-different-history branch is not SHA-merged,
    # so -d would refuse — the is_landed() guard above makes -D safe.
    r = git("branch", "-D", branch)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    remote = out("for-each-ref", "--format=%(refname:short)",
                 "refs/remotes/origin/" + branch)
    if remote:
        r2 = git("push", "origin", "--delete", branch)
        sys.stdout.write(r2.stdout)
        sys.stderr.write(r2.stderr)
    return 0


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "audit"
    rest = args[1:]
    if cmd == "audit":
        return audit(rest[0] if rest else "origin/main")
    if cmd == "dirty-worktrees":
        return dirty_worktrees_cmd()
    if cmd == "orphan-origin-refs":
        return orphan_origin_refs_cmd(rest[0] if rest else "origin/main")
    if cmd == "land-and-prune":
        if not rest:
            print("usage: land-and-prune <branch> [base]")
            return 2
        return land_and_prune(rest[0], rest[1] if rest[1:] else "origin/main")
    return audit(cmd)


if __name__ == "__main__":
    sys.exit(main())
