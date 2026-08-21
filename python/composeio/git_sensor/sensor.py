"""GitSensor — a SENSOR peripheral that instruments a git repo's sync state.

In HAL terms ([SPEC:CXC-HAL-DECLARE] / [CXC-HALD-ROLE]) this is a SENSOR whose
namespaces are ``git/*``: a repository instruments its OWN sync state onto the
ComposeBus exactly the way a bme280 publishes ``env/temperature`` or the
SelfSensor publishes ``self/*``.  A sensor publishes; a subscriber (the console,
a notifier, the mermaid widget) reads ``git/*`` to surface forgotten,
uncommitted, or unpushed work — visually AND as a bus alert.

Three faces of the same readout:

  1. **Scalar readings** — ``git/branch``, ``git/dirty``, ``git/uncommitted``,
     ``git/unpushed``, ``git/behind``, ``git/stashes`` … the instruments.

  2. **``git/alert``** — a STRUCTURED value ``{out_of_sync, reasons, message}``
     that flips ``out_of_sync=true`` the moment ANY drift exists (dirty tree,
     unpushed commits, behind upstream, detached HEAD, or hidden stashes).  This
     is the bus alert the console/notifier consumes — the "you forgot to push"
     nudge, wired into an instrument.

  3. **``git/graph``** — a generated mermaid ``gitGraph`` SOURCE string for the
     recent history.  ``gitGraph`` is a first-class diagram type in mermaid v11,
     so this string renders UNCHANGED in the already-built MermaidWidget (bind a
     mermaid widget to ``git/graph``; see render/examples/git_graph.compose.yaml).

This slice COMPUTES + EXPOSES the publish set (``to_bus_lines``); like the
SelfSensor it does not open a live bus/MQTT link — that wiring is the console's
job in a later slice.  Only ``git`` (via subprocess) + the stdlib are used.
"""
from __future__ import annotations

import subprocess
from typing import Any, Optional

# Default depth of recent history mapped into the gitGraph ([CXC-IC-CANVAS]).
DEFAULT_MAX_COMMITS = 12

# ASCII unit separators for robust `git log` field/record parsing.
_FS = "\x1f"   # between fields on one commit line
_NL = "\n"

# gitGraph's default trunk branch name (mermaid renders the base line as `main`;
# the actual branch name is reported by git/branch and noted as a %% comment).
_MAIN = "main"


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def _run(repo: str, args: list[str]) -> tuple[int, str]:
    """Run ``git -C <repo> <args>`` and return ``(returncode, stdout)``.

    stderr is swallowed (we branch on returncode) and stdout is stripped of a
    single trailing newline.  No shell, no network."""
    try:
        p = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, check=False,
        )
    except (OSError, ValueError) as exc:  # git missing / bad args
        return 127, ""
    return p.returncode, p.stdout.rstrip("\n")


def _sanitize_label(text: str, limit: int = 32) -> str:
    """Make a commit subject safe inside a gitGraph ``id: "..."`` literal:
    no double-quotes, no newlines, collapsed whitespace, truncated."""
    t = " ".join(str(text or "").replace('"', "'").split())
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t


def _sanitize_branch(name: str) -> str:
    """Make a branch name safe as a gitGraph ``branch <name>`` token: keep
    alnum / ``-`` / ``_``; everything else (``/``, ``.`` …) becomes ``_``."""
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(name))
    return out or "branch"


# ---------------------------------------------------------------------------
# GitSensor
# ---------------------------------------------------------------------------

class GitSensor:
    """The ``git/*`` sensor peripheral: read a repo's sync state + draw it.

    Construct with a ``repo_path`` (any dir inside the work tree).  :meth:`parse`
    returns the merged ``git/*`` readings dict (including the structured
    ``git/alert`` and the mermaid ``git/graph``); :meth:`to_bus_lines` returns
    the scalar publish set for the ComposeBus.
    """

    def __init__(self, repo_path: str, *, max_commits: int = DEFAULT_MAX_COMMITS):
        self.repo_path = str(repo_path)
        self.max_commits = int(max_commits)

    # -- individual readings -------------------------------------------------

    def _is_repo(self) -> bool:
        rc, out = _run(self.repo_path, ["rev-parse", "--is-inside-work-tree"])
        return rc == 0 and out.strip() == "true"

    def _branch(self) -> tuple[str, bool]:
        """(branch_name, detached).  Detached HEAD -> ('HEAD', True)."""
        rc, out = _run(self.repo_path, ["symbolic-ref", "--short", "-q", "HEAD"])
        if rc == 0 and out:
            return out, False
        # detached: report the short hash as a stand-in name
        _, sha = _run(self.repo_path, ["rev-parse", "--short", "HEAD"])
        return (sha or "HEAD"), True

    def _upstream(self) -> Optional[str]:
        rc, out = _run(self.repo_path,
                       ["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                        "@{upstream}"])
        return out if rc == 0 and out else None

    def _ahead_behind(self, has_upstream: bool) -> tuple[int, int]:
        """(unpushed_ahead, behind).  Zeros when no upstream exists."""
        if not has_upstream:
            return 0, 0
        rc, out = _run(self.repo_path,
                       ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
        if rc != 0 or not out:
            return 0, 0
        try:
            behind_s, ahead_s = out.split()
            return int(ahead_s), int(behind_s)
        except ValueError:
            return 0, 0

    def _porcelain(self) -> tuple[int, int, int]:
        """(uncommitted, staged, untracked) from ``git status --porcelain``.

        - untracked  = lines beginning ``??``
        - staged     = lines whose INDEX column (col 0) is a change (MADRC)
        - uncommitted= tracked changes = every porcelain line that is NOT ``??``
                       (staged and/or unstaged modifications needing a commit)
        """
        rc, out = _run(self.repo_path, ["status", "--porcelain"])
        if rc != 0 or not out:
            return 0, 0, 0
        uncommitted = staged = untracked = 0
        for line in out.split(_NL):
            if not line:
                continue
            if line.startswith("??"):
                untracked += 1
                continue
            uncommitted += 1
            if line[:1] in "MADRC":
                staged += 1
        return uncommitted, staged, untracked

    def _stashes(self) -> int:
        rc, out = _run(self.repo_path, ["stash", "list"])
        if rc != 0 or not out:
            return 0
        return len([ln for ln in out.split(_NL) if ln])

    def _last_commit(self) -> Optional[dict]:
        rc, out = _run(self.repo_path, ["log", "-1", f"--pretty=%h{_FS}%s"])
        if rc != 0 or not out:
            return None
        parts = out.split(_FS, 1)
        return {"hash": parts[0], "subject": parts[1] if len(parts) > 1 else ""}

    # -- the mermaid gitGraph ------------------------------------------------

    def _commits(self, revrange: list[str]) -> list[tuple[str, str, str]]:
        """Return ``[(full, short, subject), …]`` OLDEST->NEWEST for a rev
        selection, capped to :attr:`max_commits`."""
        rc, out = _run(self.repo_path,
                       ["log", "--first-parent", f"-n{self.max_commits}",
                        f"--pretty=%H{_FS}%h{_FS}%s", *revrange])
        if rc != 0 or not out:
            return []
        rows: list[tuple[str, str, str]] = []
        for line in out.split(_NL):
            if not line:
                continue
            f = line.split(_FS, 2)
            if len(f) == 3:
                rows.append((f[0], f[1], f[2]))
        rows.reverse()  # git log is newest-first; gitGraph builds oldest-first
        return rows

    def _sibling_branches(self, current: str) -> list[str]:
        rc, out = _run(self.repo_path,
                       ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
        if rc != 0 or not out:
            return []
        return [b for b in out.split(_NL) if b and b != current]

    def build_graph(self, branch: str, detached: bool) -> str:
        """Generate a VALID mermaid ``gitGraph`` source string for the recent
        history.  Primary path: the current branch's first-parent line as the
        trunk, with sibling local branches attached at their merge-base (when it
        falls inside the window).  Degrades to a linear commit list — and never
        to empty/garbled output — if the topology can't be mapped cleanly.
        """
        try:
            return self._build_graph(branch, detached)
        except Exception:  # noqa: BLE001 — robustness: never emit invalid syntax
            return self._linear_graph(branch)

    def _build_graph(self, branch: str, detached: bool) -> str:
        mainline = self._commits(["HEAD"])
        if not mainline:
            return self._linear_graph(branch)

        main_hashes = {full for full, _, _ in mainline}

        # Sibling branches attached at a merge-base that lives in our window.
        # attach[base_full] -> list of (branch_name, [(short, subj), …])
        attach: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {}
        if not detached:
            for sib in self._sibling_branches(branch):
                rc, base = _run(self.repo_path, ["merge-base", "HEAD", sib])
                if rc != 0 or not base or base not in main_hashes:
                    continue  # diverges outside the window -> just omit this one
                sib_commits = self._commits([f"{base}..{sib}"])
                if not sib_commits:
                    continue
                attach.setdefault(base, []).append(
                    (_sanitize_branch(sib),
                     [(s, subj) for _, s, subj in sib_commits]))

        lines = ["gitGraph"]
        if branch and branch != _MAIN:
            lines.append(f"   %% branch: {branch}")
        for full, short, subj in mainline:
            lines.append(f'   commit id: "{short} {_sanitize_label(subj)}"')
            for bname, bcommits in attach.get(full, []):
                lines.append(f"   branch {bname}")
                lines.append(f"   checkout {bname}")
                for s, subj2 in bcommits:
                    lines.append(f'   commit id: "{s} {_sanitize_label(subj2)}"')
                lines.append(f"   checkout {_MAIN}")
        return _NL.join(lines)

    def _linear_graph(self, branch: str) -> str:
        """Degrade path: a straight ``commit`` list on the current branch.
        Always valid; a repo with no commits still yields a bare ``commit``."""
        commits = self._commits(["HEAD"])
        lines = ["gitGraph"]
        if branch and branch != _MAIN:
            lines.append(f"   %% branch: {branch}")
        if not commits:
            lines.append("   commit")
            return _NL.join(lines)
        for _, short, subj in commits:
            lines.append(f'   commit id: "{short} {_sanitize_label(subj)}"')
        return _NL.join(lines)

    # -- the composed readout ------------------------------------------------

    def parse(self) -> dict:
        """Compute the full ``git/*`` readings dict for this repo."""
        if not self._is_repo():
            return {
                "git/branch": None,
                "git/detached": False,
                "git/dirty": False,
                "git/uncommitted": 0,
                "git/staged": 0,
                "git/untracked": 0,
                "git/unpushed": 0,
                "git/behind": 0,
                "git/stashes": 0,
                "git/upstream": None,
                "git/last_commit": None,
                "git/alert": {
                    "out_of_sync": False,
                    "reasons": [],
                    "message": "not a git repository",
                },
                "git/graph": "gitGraph\n   commit",
            }

        branch, detached = self._branch()
        upstream = self._upstream()
        unpushed, behind = self._ahead_behind(upstream is not None)
        uncommitted, staged, untracked = self._porcelain()
        stashes = self._stashes()
        dirty = uncommitted > 0
        last_commit = self._last_commit()
        graph = self.build_graph(branch, detached)

        alert = self._alert(dirty, uncommitted, unpushed, behind, detached, stashes)

        return {
            "git/branch": branch,
            "git/detached": detached,
            "git/dirty": dirty,
            "git/uncommitted": uncommitted,
            "git/staged": staged,
            "git/untracked": untracked,
            "git/unpushed": unpushed,
            "git/behind": behind,
            "git/stashes": stashes,
            "git/upstream": upstream,
            "git/last_commit": last_commit,
            "git/alert": alert,
            "git/graph": graph,
        }

    @staticmethod
    def _alert(dirty: bool, uncommitted: int, unpushed: int, behind: int,
               detached: bool, stashes: int) -> dict:
        """The bus ALERT: out_of_sync when ANY drift exists, with enumerable
        machine ``reasons`` tags and a human ``message``."""
        reasons: list[str] = []
        fragments: list[str] = []
        if dirty:
            reasons.append("dirty")
            fragments.append(f"{uncommitted} uncommitted")
        if unpushed > 0:
            reasons.append("unpushed")
            fragments.append(f"{unpushed} unpushed")
        if behind > 0:
            reasons.append("behind")
            fragments.append(f"{behind} behind")
        if detached:
            reasons.append("detached")
            fragments.append("detached HEAD")
        if stashes > 0:
            reasons.append("stashes")
            fragments.append(f"{stashes} stashed")
        out_of_sync = bool(reasons)
        message = " · ".join(fragments) if out_of_sync else "in sync"
        return {"out_of_sync": out_of_sync, "reasons": reasons, "message": message}

    def to_bus_lines(self) -> list[tuple[str, object]]:
        """The (namespace, value) pairs this sensor publishes to the ComposeBus.

        Mirrors the SelfSensor convention: keep the scalar ``git/*`` readings,
        the structured ``git/alert`` (the whole point — the console consumes it),
        and the ``git/graph`` mermaid source (the mermaid widget binds it); drop
        the bulky structured ``git/last_commit`` detail record."""
        d = self.parse()
        drop = {"git/last_commit"}
        return [(k, v) for k, v in d.items() if k not in drop]

    def describe(self) -> dict:
        """HAL-style self-description ([CXC-HALD-DRIVER] ``describe``): a SENSOR
        peripheral in the ``git/*`` namespace family."""
        return {
            "driver": "git",
            "category": "sensor",
            "tier": "vm",
            "namespaces": sorted(k for k, _ in self.to_bus_lines()),
            "summary": "git repo sync-state instrumentation (branch/dirty/"
                       "unpushed/behind/stashes) + git/alert + a mermaid "
                       "gitGraph on git/graph",
        }
