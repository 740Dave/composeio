"""bootstrap_paths — the SINGLE SOURCE OF TRUTH for the continuity / bootstrap
directory layout ([SPEC:CXC-IC-BOOTSTRAP-PATHS]).

The wake / handover machinery (the launcher's bootstrap prompt, the bootstrap
manifest widget, the meta/self publishers, the away-watch projector, the
compact-summary extractor, the self/session sensors) all need to name a handful
of continuity directories: where the dusk gifts live, where compact summaries
are written, where away-watch reports land, where queryable corpora sit. Those
locations WILL be reorganized again. When they were hard-coded across ~30
files, one reorg was a 30-file sweep; the fix is that a reorg is now a ONE-LINE
edit HERE.

Every runtime consumer imports these constants instead of writing a path
literal. Two forms are exported:

  * absolute ``Path`` constants (``…_DIR``)   — for opening / globbing on disk;
  * repo-relative POSIX strings (``…_REL``, trailing slash) — for manifest
    defaults, prompt text, and anything that must stay relocatable / display
    a short path.

Root discovery: ``COMPOSEIO_REPO_ROOT`` (or ``COMPOSEIO_ROOT``, the name the
MCP servers already set) overrides; otherwise it is derived from this file's
location (``python/composeio/bootstrap_paths.py`` -> ``parents[2]``), so the
module is correct in-tree with no environment at all.

There are NO heavy imports here (os + pathlib only) — the launcher can import
this without breaking its paho-only startup footprint.
"""

from __future__ import annotations

import os
from pathlib import Path


def _discover_repo_root() -> Path:
    env = os.environ.get("COMPOSEIO_REPO_ROOT") or os.environ.get("COMPOSEIO_ROOT")
    if env:
        return Path(env).resolve()
    # python/composeio/bootstrap_paths.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2]


# ── The one canonical root ────────────────────────────────────────────────────
REPO_ROOT: Path = _discover_repo_root()

# ── Continuity / bootstrap directories (absolute) ─────────────────────────────
#    Change a location HERE and every consumer follows.
BOOTSTRAP_DIR: Path = REPO_ROOT / "bootstrap"
DUSK_GIFTS_DIR: Path = BOOTSTRAP_DIR / "dusk-gifts"
COMPACT_SUMMARIES_DIR: Path = REPO_ROOT / "docs" / "compact-summaries"
WATCH_REPORTS_DIR: Path = REPO_ROOT / "docs" / "watch_reports"
# ⛔ CORPORA LIVE OUTSIDE THE REPO — moved 2026-08-17. Measured cause: 41 live
#    worktrees each carried their OWN copy of `corpora/` (63-90 `.lts` files
#    apiece), i.e. 41 forks of a graph whose entire purpose is to be
#    single-source-of-truth. A node written in a worktree existed only if that
#    branch landed, and committing a corpus file from a stale tree DELETED
#    records — `defects/defect-graph.lts` was one merge away from losing a
#    defect filed sixteen hours earlier. Precedent, already ratified:
#    [INSIGHT-WORKTREES-POLLUTE-SKILL-DISCOVERY] measured the identical failure
#    one namespace over (108 `SKILL.md` on disk, 18x collision, discovery
#    failing SELECTIVELY) and its rule is "keep worktrees outside the repo, or
#    keep the replicated tree out of the walked namespace".
#    Shape copied from [CXC-FC-STORE-ACCESS]: ONE store, outside every worktree,
#    therefore prune-proof. Override with COMPOSEIO_CORPORA_ROOT.
CORPORA_DIR: Path = Path(
    os.environ.get("COMPOSEIO_CORPORA_ROOT")
    or (Path.home() / ".claude" / "factlattice_corpora" / "composeio" / "corpora")
).resolve()
#    The defect graph is a sibling of `corpora/`, not a member of it (it is
#    registered as its own root), so it moves with the same parent.
DEFECTS_DIR: Path = CORPORA_DIR.parent / "defects"
DEFECT_GRAPH: Path = DEFECTS_DIR / "defect-graph.lts"

# ── Individual corpus homes under corpora/ (one dir == one queryable corpus) ───
#    Each is a directory the lts-engine loads whole (no `only_file` workaround).
#    Registered in the out-of-repo corpus-config.json; named here so runtime
#    consumers resolve them via the constant instead of a path literal.
DESIGN_EXCELLENCE_CORPUS_DIR: Path = CORPORA_DIR / "design-excellence"
INSIGHTS_CORPUS_DIR: Path = CORPORA_DIR / "insights-and-warts"

# Shared corpus roots (each is loaded whole by the lts-engine; no `only_file`).
#   platform-specs — the former ``ux_composer/specs`` pool, shared by every
#     ComposeApp project corpus (intelligence-console, visual-composer, …).
#   deliverables   — the single cross-project deliverable ledger corpus.
PLATFORM_SPECS_DIR: Path = CORPORA_DIR / "platform-specs"
DELIVERABLES_CORPUS_DIR: Path = CORPORA_DIR / "deliverables"
DELIVERABLE_LEDGER: Path = DELIVERABLES_CORPUS_DIR / "deliverable-ledger.lts"
#   intelligence-console decision corpus (the architect graph + projects.lts).
INTELLIGENCE_CONSOLE_CORPUS_DIR: Path = CORPORA_DIR / "intelligence-console"
#   The ComposeIO domain profile — prefix registry, document tags, relationship
#   verbs, enums, decision-node schema and decision lifecycle. decision_ops and
#   lts_lint resolve it here.
#
#   ⛔ MOVED + RESERIALIZED 2026-08-11. This was
#   ``platform-specs/99-composeio-profile.yaml``; that file is DELETED. The `.lts`
#   form is the current format ([PROFILE-SELFHOST]: a domain profile is a `.lts`
#   document parsed by the parser it configures), and it absorbed the yaml's full
#   declared vocabulary before the yaml was removed. It now lives beside the
#   decision corpus rather than in the spec pool because a profile is registry, not
#   specification — and because it is REGISTERED (the `profile` key on every
#   composeio corpus in the out-of-repo corpus-config.json) rather than merely
#   discovered, its location no longer determines which roots it governs.
COMPOSEIO_PROFILE: Path = (INTELLIGENCE_CONSOLE_CORPUS_DIR
                           / "platform-composeio-profile.lts")


def project_corpus_dir(slug: str) -> Path:
    """The per-project decision-graph corpus home ``corpora/<slug>/`` — one
    coherent queryable directory per project (the layout that retires the
    ``only_file`` workaround)."""
    return CORPORA_DIR / slug

# ── Repo-relative POSIX strings (trailing slash) ──────────────────────────────
#    For manifest defaults / prompt text / short display. Kept in lockstep with
#    the Path constants above by rel_dir().
def rel_dir(path: Path) -> str:
    """Repo-relative POSIX string for a directory, WITH a trailing slash.

    ⛔ Falls back to the ABSOLUTE path for anything outside the repo. Since
    2026-08-17 the corpora live at ``~/.claude/factlattice_corpora/`` — outside
    every worktree, deliberately — and these ``…_REL`` constants are computed at
    IMPORT time, so a bare ``relative_to`` would raise ``ValueError`` on import
    and take all 18 importers down at once. An absolute path is a correct answer
    to "name this location"; it is only the *short display* that is lost."""
    return _rel_or_abs(path) + "/"


def rel(path: Path) -> str:
    """Repo-relative POSIX string for any path (no trailing slash added).
    Falls back to absolute for out-of-repo paths — see ``rel_dir``."""
    return _rel_or_abs(path)


def _rel_or_abs(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


BOOTSTRAP_REL: str = rel_dir(BOOTSTRAP_DIR)
DUSK_GIFTS_REL: str = rel_dir(DUSK_GIFTS_DIR)
COMPACT_SUMMARIES_REL: str = rel_dir(COMPACT_SUMMARIES_DIR)
WATCH_REPORTS_REL: str = rel_dir(WATCH_REPORTS_DIR)
CORPORA_REL: str = rel_dir(CORPORA_DIR)
DESIGN_EXCELLENCE_CORPUS_REL: str = rel_dir(DESIGN_EXCELLENCE_CORPUS_DIR)
INSIGHTS_CORPUS_REL: str = rel_dir(INSIGHTS_CORPUS_DIR)
PLATFORM_SPECS_REL: str = rel_dir(PLATFORM_SPECS_DIR)
DELIVERABLES_CORPUS_REL: str = rel_dir(DELIVERABLES_CORPUS_DIR)
DELIVERABLE_LEDGER_REL: str = rel(DELIVERABLE_LEDGER)
COMPOSEIO_PROFILE_REL: str = rel(COMPOSEIO_PROFILE)
INTELLIGENCE_CONSOLE_CORPUS_REL: str = rel_dir(INTELLIGENCE_CONSOLE_CORPUS_DIR)


def resolve(*parts: str) -> Path:
    """Resolve ``parts`` under the repo root (a small convenience for consumers
    that need a one-off continuity path not worth its own constant)."""
    return REPO_ROOT.joinpath(*parts)


__all__ = [
    "REPO_ROOT",
    "BOOTSTRAP_DIR", "DUSK_GIFTS_DIR", "COMPACT_SUMMARIES_DIR",
    "WATCH_REPORTS_DIR", "CORPORA_DIR",
    "DESIGN_EXCELLENCE_CORPUS_DIR", "INSIGHTS_CORPUS_DIR",
    "PLATFORM_SPECS_DIR", "DELIVERABLES_CORPUS_DIR", "DELIVERABLE_LEDGER",
    "COMPOSEIO_PROFILE", "INTELLIGENCE_CONSOLE_CORPUS_DIR", "project_corpus_dir",
    "BOOTSTRAP_REL", "DUSK_GIFTS_REL", "COMPACT_SUMMARIES_REL",
    "WATCH_REPORTS_REL", "CORPORA_REL",
    "DESIGN_EXCELLENCE_CORPUS_REL", "INSIGHTS_CORPUS_REL",
    "PLATFORM_SPECS_REL", "DELIVERABLES_CORPUS_REL", "DELIVERABLE_LEDGER_REL",
    "COMPOSEIO_PROFILE_REL", "INTELLIGENCE_CONSOLE_CORPUS_REL",
    "rel", "rel_dir", "resolve",
]
