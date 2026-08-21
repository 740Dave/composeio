"""End-to-end self-test for the ``git/*`` git sync-state sensor.

Deterministic + hermetic: builds a TEMP git repo (and a local BARE remote so
ahead/behind is real) entirely in a tempdir, drives it into a known drifted
state, and asserts every reading.  No network, no reliance on the outer repo,
fixed GIT_* identity + dates so hashes/counts are reproducible.

Constructed state (the drifted repo):

    * a bare remote + two commits on ``main``, pushed (upstream set)
    * a feature branch ``feature/widget`` with one commit
    * an extra local commit on main            -> git/unpushed == 1
    * a modified tracked file                   -> git/dirty, +1 uncommitted
    * a staged new file                         -> git/staged == 1, +1 uncommitted
    * an untracked file                         -> git/untracked == 1
    * one stash                                 -> git/stashes == 1

Checks:

    G1  readings match the constructed state (branch, upstream, counts)
    G2  git/alert.out_of_sync TRUE with the right reasons + a sane message
    G3  a CLEAN, fully-synced repo yields out_of_sync FALSE ("in sync")
    G4  git/graph is VALID mermaid gitGraph (starts `gitGraph`, has commit
        lines, and — with a branch — a branch/checkout line), never empty
    G5  to_bus_lines drops the bulky git/last_commit but keeps git/alert +
        git/graph; describe() advertises the git/* namespaces

Run::  python3 -m composeio.git_sensor --selftest
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .sensor import GitSensor

# Deterministic git identity + timestamps (hermetic hashes / no config bleed).
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Compose Tester",
    "GIT_AUTHOR_EMAIL": "tester@composeio.local",
    "GIT_COMMITTER_NAME": "Compose Tester",
    "GIT_COMMITTER_EMAIL": "tester@composeio.local",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00 +0000",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00 +0000",
    # keep the outer user's config from leaking in
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}


class _Checker:
    """Tiny PASS/FAIL harness — same shape as composeio.self_sensor.selftest."""

    def __init__(self) -> None:
        self.failures = 0

    def check(self, claim: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        line = f"  [{mark}] {claim}"
        if detail:
            line += f"  ({detail})"
        print(line)


def _git(repo: str, *args: str) -> str:
    """Run git in ``repo`` with the deterministic env; raise on failure."""
    p = subprocess.run(["git", "-C", repo, *args],
                       capture_output=True, text=True, env=_ENV)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {p.returncode}: {p.stderr}")
    return p.stdout.rstrip("\n")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _build_clean_synced(root: Path) -> str:
    """A repo cloned from a bare remote, HEAD == upstream, tree clean."""
    remote = root / "clean_remote.git"
    work = root / "clean_work"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)],
                   check=True, env=_ENV)
    _git(str(root), "clone", "-q", str(remote), str(work))
    _git(str(work), "checkout", "-q", "-b", "main")
    _write(work / "README.md", "hello\n")
    _git(str(work), "add", "README.md")
    _git(str(work), "commit", "-q", "-m", "initial commit")
    _git(str(work), "push", "-q", "-u", "origin", "main")
    return str(work)


def _build_drifted(root: Path) -> str:
    """A repo driven into a fully-drifted state (see module docstring)."""
    remote = root / "remote.git"
    work = root / "work"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)],
                   check=True, env=_ENV)
    _git(str(root), "clone", "-q", str(remote), str(work))
    w = str(work)
    _git(w, "checkout", "-q", "-b", "main")

    # two commits, pushed -> upstream established
    _write(work / "a.txt", "one\n")
    _git(w, "add", "a.txt")
    _git(w, "commit", "-q", "-m", "first commit")
    _write(work / "b.txt", "two\n")
    _git(w, "add", "b.txt")
    _git(w, "commit", "-q", "-m", "second commit")
    _git(w, "push", "-q", "-u", "origin", "main")

    # a feature branch with its own commit (drives the gitGraph branch/checkout)
    _git(w, "checkout", "-q", "-b", "feature/widget")
    _write(work / "feature.txt", "feature work\n")
    _git(w, "add", "feature.txt")
    _git(w, "commit", "-q", "-m", "feature commit")
    _git(w, "checkout", "-q", "main")

    # an extra LOCAL commit on main -> unpushed == 1
    _write(work / "c.txt", "three\n")
    _git(w, "add", "c.txt")
    _git(w, "commit", "-q", "-m", "third commit (unpushed)")

    # a modified tracked file -> dirty + uncommitted
    _write(work / "a.txt", "one modified\n")
    # a staged NEW file -> staged + uncommitted
    _write(work / "staged.txt", "staged\n")
    _git(w, "add", "staged.txt")
    # an untracked file
    _write(work / "untracked.txt", "untracked\n")
    # one stash (stash a fresh tracked modification, then keep the above state)
    _write(work / "b.txt", "two stashed\n")
    _git(w, "stash", "push", "-q", "-m", "wip", "b.txt")

    return w


def run() -> int:
    c = _Checker()
    print("ComposeIO git/* sensor self-test (git sync-state instrumentation)\n")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # --- G1/G2/G4: the DRIFTED repo -----------------------------------
        drifted = _build_drifted(root)
        d = GitSensor(drifted).parse()

        print("G1 — readings match the constructed drifted state:")
        c.check("git/branch is the checked-out branch",
                d["git/branch"] == "main", f"branch={d['git/branch']}")
        c.check("git/detached is False on a normal branch",
                d["git/detached"] is False)
        c.check("git/upstream resolves to origin/main",
                d["git/upstream"] == "origin/main",
                f"upstream={d['git/upstream']}")
        c.check("git/uncommitted counts tracked changes (mod + staged)",
                d["git/uncommitted"] == 2, f"uncommitted={d['git/uncommitted']}")
        c.check("git/staged counts the staged new file",
                d["git/staged"] == 1, f"staged={d['git/staged']}")
        c.check("git/untracked counts the untracked file",
                d["git/untracked"] == 1, f"untracked={d['git/untracked']}")
        c.check("git/unpushed == 1 (the extra local commit ahead of upstream)",
                d["git/unpushed"] == 1, f"unpushed={d['git/unpushed']}")
        c.check("git/behind == 0 (remote has no newer commits)",
                d["git/behind"] == 0, f"behind={d['git/behind']}")
        c.check("git/stashes == 1",
                d["git/stashes"] == 1, f"stashes={d['git/stashes']}")
        c.check("git/dirty True with tracked changes present",
                d["git/dirty"] is True)
        c.check("git/last_commit carries {hash, subject}",
                isinstance(d["git/last_commit"], dict)
                and d["git/last_commit"]["subject"] == "third commit (unpushed)",
                f"last={d['git/last_commit']}")

        print("\nG2 — git/alert is the bus ALERT (out_of_sync + reasons + msg):")
        alert = d["git/alert"]
        c.check("git/alert.out_of_sync is True",
                alert["out_of_sync"] is True)
        expect_reasons = {"dirty", "unpushed", "stashes"}
        c.check("reasons name every drift (dirty · unpushed · stashes)",
                set(alert["reasons"]) == expect_reasons,
                f"reasons={alert['reasons']}")
        c.check("message is a sane human summary",
                "2 uncommitted" in alert["message"]
                and "1 unpushed" in alert["message"]
                and "1 stashed" in alert["message"],
                f"message={alert['message']!r}")

        print("\nG4 — git/graph is VALID mermaid gitGraph (renders in widget):")
        g = d["git/graph"]
        c.check("starts with the gitGraph header",
                g.startswith("gitGraph"), repr(g.splitlines()[0]))
        c.check("contains commit lines",
                'commit id: "' in g, f"{g.count('commit id')} commit lines")
        c.check("with a feature branch present, emits branch + checkout lines",
                "branch feature_widget" in g and "checkout feature_widget" in g,
                "branch/checkout for feature/widget")
        c.check("checks back out to main after the branch",
                "checkout main" in g)
        c.check("git/graph is never empty / garbled",
                bool(g.strip()) and g.count("gitGraph") == 1)

        # --- G3: the CLEAN, fully-synced repo -----------------------------
        print("\nG3 — a CLEAN synced repo yields out_of_sync FALSE:")
        clean = _build_clean_synced(root)
        cd = GitSensor(clean).parse()
        c.check("clean repo: git/dirty False, all drift counts zero",
                cd["git/dirty"] is False and cd["git/uncommitted"] == 0
                and cd["git/unpushed"] == 0 and cd["git/behind"] == 0
                and cd["git/stashes"] == 0,
                f"uncommitted={cd['git/uncommitted']} unpushed={cd['git/unpushed']}")
        c.check("clean repo: git/alert.out_of_sync is False, message 'in sync'",
                cd["git/alert"]["out_of_sync"] is False
                and cd["git/alert"]["message"] == "in sync"
                and cd["git/alert"]["reasons"] == [])
        c.check("clean repo git/graph still valid (linear or trunk)",
                cd["git/graph"].startswith("gitGraph")
                and 'commit id: "' in cd["git/graph"])

        # --- G5: the publish set + self-description -----------------------
        print("\nG5 — the ComposeBus publish set + HAL self-description:")
        lines = dict(GitSensor(drifted).to_bus_lines())
        c.check("to_bus_lines keeps scalar git/* + git/alert + git/graph, "
                "drops bulky git/last_commit",
                "git/last_commit" not in lines
                and "git/alert" in lines and "git/graph" in lines
                and "git/branch" in lines and "git/unpushed" in lines,
                f"{len(lines)} namespaces published")
        desc = GitSensor(drifted).describe()
        c.check("describe() advertises a git/* SENSOR",
                desc["driver"] == "git" and desc["category"] == "sensor"
                and "git/alert" in desc["namespaces"],
                f"{len(desc['namespaces'])} namespaces")

    print(f"\n{'=' * 60}")
    total = "ALL PASS" if c.failures == 0 else f"{c.failures} FAILURE(S)"
    print(f"  git/* sensor self-test: {total}")
    print("=" * 60)
    return 1 if c.failures else 0
