"""composeio.decision_ops.graph_home — WHERE A GRAPH LIVES, decided by what the
operation is ABOUT rather than by where the code happens to sit on disk.

THE DEFECT THIS EXISTS TO MAKE IMPOSSIBLE (2026-08-08, measured twice in one
session). `defect_ops/mcp_server.py` derived its default registry path from the
module's own file location::

    _REPO_ROOT = str(Path(__file__).resolve().parents[3])      # WRONG

Inside a linked git worktree that IS the worktree's *copy* of the module, so
``<repo>/defects/defect-graph.lts`` resolved to the WORKTREE's copy of the graph.
Two defects filed by dispatched subagents — DEFECT-UART-FRAME-MESSAGE-TYPE-DOES-D62D7F
and DEFECT-DOMAIN-RELATIONSHIP-VERBS-DISCARDED-SILENTLY-D542D5 — therefore landed on
a graph that was live NOWHERE. Both agents honestly reported "filed"; both had to be
excavated from unlanded commits and re-filed by hand. The registry exists to be read
by the NEXT investigator, who would have found nothing and re-dug from scratch.

Same root cause as two sibling fixes landed the same day:
  * ``.githooks/pre-commit`` deriving REPO_ROOT from ``BASH_SOURCE`` (061503b), and
  * ``scripts/pre-commit-tests.sh`` reading only ``--cached`` (7cbfd10).
In all three: **context derived from where the code lives, instead of what the
operation is about.**

--------------------------------------------------------------------------------
THE DISCRIMINATOR

``git rev-parse --git-common-dir`` is deterministic and works from INSIDE any
worktree::

    main checkout   :  --git-dir == --git-common-dir == <root>/.git
    linked worktree :  --git-dir == <root>/.git/worktrees/<name>
                       --git-common-dir == <root>/.git      <-- still the REAL one

So the CANONICAL repo root is ``parent(--git-common-dir)``, obtainable from any
worktree, and ``--git-dir != --git-common-dir`` is an exact test for "I am in a
linked worktree".

GIT_* IS SCRUBBED on every invocation here. git EXPORTS ``GIT_DIR`` /
``GIT_INDEX_FILE`` / ``GIT_WORK_TREE`` into hooks and subprocesses, and those BEAT
``git -C <dir>`` — ``-C`` changes the cwd, it does not repoint discovery. Asking
"which repo does THIS PATH belong to" must be answered from the path, never from an
inherited environment (this module runs inside pre-commit hooks).

--------------------------------------------------------------------------------
THE THREE GUARANTEES

1. DEFAULT RESOLUTION IS CANONICAL — ``canonical_graph_path()`` resolves against
   ``parent(--git-common-dir)``, not against ``__file__`` and not against cwd.
2. AN EXPLICIT WORKTREE-INTERNAL PATH IS REFUSED, LOUDLY — ``guard_graph_write()``
   raises ``WorktreeGraphRefused`` naming BOTH the path given and the canonical
   path. Never a silent redirect: a caller who genuinely meant a scratch graph
   deserves to be told, not quietly re-aimed (loud refusal over plausible-wrong).
   Escape hatch for hermetic tests: ``COMPOSEIO_ALLOW_WORKTREE_GRAPH=1``.
3. EVERY WRITE IS SELF-PROVING — ``graph_receipt()`` returns the ABSOLUTE RESOLVED
   PATH the write landed on, so an agent's "filed the defect" carries a verifiable
   fact instead of costing a human a ``grep`` to check.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

__all__ = [
    "ALLOW_WORKTREE_ENV",
    "WorktreeGraphRefused",
    "RepoContext",
    "repo_context",
    "canonical_repo_root",
    "containing_worktree_root",
    "is_in_linked_worktree",
    "canonical_equivalent",
    "canonical_graph_path",
    "guard_graph_write",
    "graph_receipt",
]

PathLike = Union[str, os.PathLike, Path]

#: Set to 1/true/yes to permit a write to a graph inside a linked worktree.
#: The ONLY sanctioned use is a hermetic test writing to a throwaway fixture graph.
ALLOW_WORKTREE_ENV = "COMPOSEIO_ALLOW_WORKTREE_GRAPH"

_TRUE = {"1", "true", "yes", "on"}


class WorktreeGraphRefused(RuntimeError):
    """A write was aimed at a graph inside a LINKED GIT WORKTREE.

    Carries ``given_path`` and ``canonical_path`` so a caller can act on the
    refusal programmatically instead of re-parsing the message."""

    def __init__(self, message: str, *, given_path: str,
                 canonical_path: Optional[str],
                 worktree_root: Optional[str] = None,
                 canonical_repo_root: Optional[str] = None) -> None:
        super().__init__(message)
        self.given_path = given_path
        self.canonical_path = canonical_path
        self.worktree_root = worktree_root
        self.canonical_repo_root = canonical_repo_root


# --------------------------------------------------------------------------- #
# git interrogation
# --------------------------------------------------------------------------- #
def _clean_env() -> dict:
    """The process environment with EVERY ``GIT_*`` pointer stripped.

    NOT paranoia — earned. git exports GIT_DIR/GIT_INDEX_FILE/GIT_WORK_TREE into
    hooks and child processes, and those BEAT ``git -C <dir>``: ``-C`` changes the
    cwd, it does not repoint discovery. Left in place, a defect filed from inside a
    pre-commit hook would be resolved against whatever repo the HOOK was serving
    rather than the repo the graph path lives in. ``GIT_CEILING_DIRECTORIES`` is
    deliberately NOT set here: legitimate discovery must be able to walk up to the
    real repo root."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _existing_dir(start: PathLike) -> Optional[Path]:
    """The nearest EXISTING directory at or above *start* — so a context can be
    resolved for a graph path that does not exist yet (the first write to a fresh
    registry file must still be aimed correctly)."""
    p = Path(start)
    try:
        p = p.expanduser()
    except (RuntimeError, OSError):
        pass
    if not p.is_absolute():
        p = Path.cwd() / p
    # Do NOT resolve() first: a symlinked worktree path should be interrogated as
    # given, and git answers correctly for it.
    if p.is_dir():
        return p
    for parent in p.parents:
        if parent.is_dir():
            return parent
    return None


@dataclass(frozen=True)
class RepoContext:
    """What git says about the repository containing a path.

    ``in_repo=False`` (every field None) is a NORMAL, correct answer — a graph in a
    tmpdir is not in a repo, and that is exactly how hermetic fixtures stay
    unguarded without weakening the guard."""

    in_repo: bool = False
    worktree_root: Optional[Path] = None      # this path's OWN worktree (--show-toplevel)
    git_dir: Optional[Path] = None            # --absolute-git-dir
    common_dir: Optional[Path] = None         # --git-common-dir (the REAL .git)
    canonical_root: Optional[Path] = None     # parent(common_dir) == the main checkout

    @property
    def in_linked_worktree(self) -> bool:
        """True iff this path sits in a LINKED worktree (``git worktree add``),
        i.e. its git-dir is not the common git-dir."""
        if not self.in_repo or self.git_dir is None or self.common_dir is None:
            return False
        return self.git_dir != self.common_dir


_NO_REPO = RepoContext()


def repo_context(start: PathLike) -> RepoContext:
    """Interrogate git about the repository containing *start* (file or dir).

    Deliberately UNCACHED: a write op runs a handful of times per session, and a
    stale answer here is precisely the class of bug this module exists to kill.
    Never raises — git absent / not a repo / timeout all yield ``_NO_REPO``."""
    d = _existing_dir(start)
    if d is None:
        return _NO_REPO
    try:
        proc = subprocess.run(
            ["git", "-C", str(d), "rev-parse",
             "--show-toplevel", "--absolute-git-dir", "--git-common-dir"],
            capture_output=True, text=True, timeout=15, env=_clean_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return _NO_REPO
    if proc.returncode != 0:
        return _NO_REPO
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) < 3:
        return _NO_REPO
    top_s, gitdir_s, common_s = lines[0], lines[1], lines[2]

    def _abs(value: str) -> Optional[Path]:
        if not value:
            return None
        p = Path(value)
        if not p.is_absolute():
            p = d / p
        try:
            return p.resolve()
        except OSError:
            return p

    top = _abs(top_s)
    git_dir = _abs(gitdir_s)
    common = _abs(common_s)
    # parent(.git) is the main checkout. A bare or non-".git"-named common dir has
    # no main worktree; answer None rather than guess (loud-by-omission).
    canonical = common.parent if (common is not None and common.name == ".git") else None
    return RepoContext(in_repo=True, worktree_root=top, git_dir=git_dir,
                       common_dir=common, canonical_root=canonical)


# --------------------------------------------------------------------------- #
# the questions callers actually ask
# --------------------------------------------------------------------------- #
def canonical_repo_root(start: PathLike) -> Optional[Path]:
    """The MAIN checkout's root for the repo containing *start* — correct even when
    *start* is a linked worktree's copy of a file. ``None`` if *start* is not in a
    (non-bare) git repo."""
    return repo_context(start).canonical_root


def containing_worktree_root(start: PathLike) -> Optional[Path]:
    """The root of the worktree *start* itself sits in (main OR linked)."""
    return repo_context(start).worktree_root


def is_in_linked_worktree(path: PathLike) -> bool:
    """True iff *path* lives inside a LINKED git worktree."""
    return repo_context(path).in_linked_worktree


def canonical_equivalent(path: PathLike) -> Optional[Path]:
    """The same repo-relative path, under the CANONICAL checkout.

    ``<wt>/defects/defect-graph.lts`` -> ``<main>/defects/defect-graph.lts``. This
    is what a refusal names so the caller can simply re-run against it."""
    ctx = repo_context(path)
    if not ctx.in_repo or ctx.worktree_root is None or ctx.canonical_root is None:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        rel = p.resolve().relative_to(ctx.worktree_root)
    except (ValueError, OSError):
        try:
            rel = p.relative_to(ctx.worktree_root)
        except ValueError:
            return None
    return ctx.canonical_root / rel


def canonical_graph_path(relative: str, *, start: PathLike,
                         must_exist: bool = True) -> Optional[str]:
    """Resolve a repo-relative graph path (e.g. ``defects/defect-graph.lts``)
    against the CANONICAL repo root for *start*.

    *start* is normally a module's ``__file__``: even when that module is a linked
    worktree's copy of itself, ``--git-common-dir`` walks the answer back to the
    main checkout. Returns ``None`` when there is no canonical root, or (with
    ``must_exist``) when nothing is there — callers then fall back explicitly
    rather than inventing a path."""
    root = canonical_repo_root(start)
    if root is None:
        return None
    cand = root / relative
    if must_exist and not cand.is_file():
        return None
    return str(cand)


def _allowed_by_env() -> bool:
    return os.environ.get(ALLOW_WORKTREE_ENV, "").strip().lower() in _TRUE


def guard_graph_write(path: PathLike, *, allow_worktree: bool = False) -> str:
    """THE STRUCTURAL GUARD. Return the absolute resolved path, or REFUSE.

    Refuses (``WorktreeGraphRefused``) when *path* lives inside a LINKED git
    worktree, naming BOTH the path given and the canonical path so the message is
    directly actionable. A silent redirect was rejected on purpose: a caller who
    genuinely meant a scratch graph must be told, not quietly re-aimed.

    Escape hatch — ``allow_worktree=True`` or ``COMPOSEIO_ALLOW_WORKTREE_GRAPH=1``
    (hermetic tests writing to a throwaway fixture graph). Tests MUST use the hatch
    rather than weaken the guard."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        abs_path = str(p.resolve())
    except OSError:
        abs_path = str(p)

    if allow_worktree or _allowed_by_env():
        return abs_path

    ctx = repo_context(path)
    if not ctx.in_linked_worktree:
        return abs_path

    canon = canonical_equivalent(path)
    raise WorktreeGraphRefused(
        "REFUSED: this graph path lives inside a LINKED GIT WORKTREE, not the "
        "canonical checkout — a record written there is live on NO graph.\n"
        f"    given (worktree copy)   : {abs_path}\n"
        f"    canonical (the registry): {canon if canon else '<unresolvable>'}\n"
        f"    worktree root           : {ctx.worktree_root}\n"
        f"    canonical repo root     : {ctx.canonical_root}\n"
        "A worktree copy dies with the worktree (or lands only if that branch is "
        "merged), so the NEXT reader of the registry finds nothing and re-digs from "
        "scratch. Two records were lost exactly this way on 2026-08-08.\n"
        "FIX: re-run against the canonical path above, or omit the graph argument "
        "entirely — the default now resolves via `git rev-parse --git-common-dir` "
        "and is canonical from inside any worktree.\n"
        f"If you REALLY mean a scratch graph in this worktree, set "
        f"{ALLOW_WORKTREE_ENV}=1 (hermetic tests do this deliberately).",
        given_path=abs_path,
        canonical_path=str(canon) if canon else None,
        worktree_root=str(ctx.worktree_root) if ctx.worktree_root else None,
        canonical_repo_root=str(ctx.canonical_root) if ctx.canonical_root else None,
    )


def graph_receipt(path: Optional[PathLike]) -> dict:
    """THE SELF-PROVING RECEIPT: where a write actually landed.

    Every write op already returns proof-shaped data (``analytics_row_id``, the
    engine ``index_hash``/``live_hash``) but NOT *where* it wrote — so a dispatched
    agent's "filed the defect" was a CLAIM that cost a human a ``grep``. These two
    keys make the agent's own report carry the verifiable fact.

      ``graph_path``          absolute, resolved.
      ``graph_is_canonical``  True  — inside the canonical (main) checkout;
                              False — inside a linked worktree (only reachable via
                                      the escape hatch);
                              None  — not inside any git repo at all, i.e. a
                                      scratch/temp graph. Honest "not applicable",
                                      never a reassuring `True`.
    """
    if not path:
        return {"graph_path": None, "graph_is_canonical": None}
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        abs_path = str(p.resolve())
    except OSError:
        abs_path = str(p)
    ctx = repo_context(path)
    if not ctx.in_repo or ctx.canonical_root is None:
        canonical: Optional[bool] = None
    elif ctx.in_linked_worktree:
        canonical = False
    else:
        canonical = True
    return {"graph_path": abs_path, "graph_is_canonical": canonical}
