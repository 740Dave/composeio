"""Reorg-proofing enforcement ([SPEC:CXC-IC-BOOTSTRAP-PATHS]).

The continuity / bootstrap directory layout has a SINGLE SOURCE OF TRUTH —
``composeio.bootstrap_paths`` — so a future directory reorg is a one-line edit
there, not a 30-file sweep. This test enforces that invariant two ways:

  1. the config module exports the named constants (absolute + repo-relative)
     and they resolve to the CURRENT locations, and
  2. no runtime code file (``python/composeio`` + ``intelligence``, excluding
     tests) carries a stray hard-coded ``docs/dusk-gifts`` path literal — the
     stale pre-reorg location. Dusk gifts live under ``bootstrap/dusk-gifts/``
     and compact summaries under ``docs/compact-summaries/`` now; any code that
     re-introduces the old literal instead of importing the constant fails here.
"""

from __future__ import annotations

import os

import composeio.bootstrap_paths as bp

# repo root: python/tests/render/<this> -> parents[3]
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir))

# The runtime code surface the invariant governs (dirs of .py under the repo).
_CODE_ROOTS = [
    os.path.join(REPO_ROOT, "python", "composeio"),
    os.path.join(REPO_ROOT, "intelligence"),
]

# The stale pre-reorg literal. `docs/dusk-gifts` was the dusk-gift dir AND the
# `docs/dusk-gifts/compact-summaries` parent; both moved. The bare keyword
# "dusk-gifts" (a filename match with no `docs/` prefix) is legitimate and NOT
# flagged — only the old rooted PATH is.
_STALE = "docs/dusk-gifts"

# The retired top-level deliverable-ledger location. The corpus consolidation
# moved it to `corpora/deliverables/deliverable-ledger.lts` and the single source
# of truth is `bootstrap_paths.DELIVERABLE_LEDGER(_REL)`. A consumer that keeps the
# bare top-level literal loads an EMPTY ledger from the retired path (the file no
# longer exists there), classes every artifact NEW, and writes a fresh all-
# `NEEDS-ENRICHMENT` "ghost" ledger — destroying curated enrichment if that output
# is ever copied onto the real corpus path ([CXC-DS-LEDGER-LOCATION-SINGLE-SOURCE]).
_STALE_LEDGER = "deliverables/deliverable-ledger.lts"
_CANONICAL_LEDGER = "corpora/deliverables/deliverable-ledger.lts"

# The retired spec pool. The corpus consolidation moved the whole `ux_composer/specs/`
# tree to `corpora/platform-specs/` (shared) — `bootstrap_paths.PLATFORM_SPECS_DIR(_REL)`
# — and the per-project graphs to `corpora/<name>/`. A surviving `ux_composer/specs`
# literal is a dead reference to a directory that no longer exists: a spec citation in
# a docstring rots silently, and any functional load would miss. bootstrap_paths.py
# itself documents the *former* pool by name and is auto-excluded (module under test).
_STALE_SPECS_DIR = "ux_composer/specs"


def test_constants_are_the_single_source_of_truth():
    # absolute Path constants exist and sit under the one root
    assert bp.REPO_ROOT.is_dir()
    for d in (bp.BOOTSTRAP_DIR, bp.DUSK_GIFTS_DIR, bp.COMPACT_SUMMARIES_DIR,
              bp.WATCH_REPORTS_DIR):
        assert str(d).startswith(str(bp.REPO_ROOT))
    # ⛔ THE CORPORA ARE DELIBERATELY *OUTSIDE* THE REPO since 2026-08-17 — 41
    #    worktrees each carried their own fork of `corpora/`, so a corpus path
    #    under REPO_ROOT means "whichever copy the caller is standing in". The
    #    invariant is no longer "under the repo"; it is "under the ONE corpus
    #    home", which is what makes it single-source-of-truth.
    for d in (bp.CORPORA_DIR, bp.DESIGN_EXCELLENCE_CORPUS_DIR, bp.INSIGHTS_CORPUS_DIR,
              bp.PLATFORM_SPECS_DIR, bp.DELIVERABLES_CORPUS_DIR,
              bp.INTELLIGENCE_CONSOLE_CORPUS_DIR, bp.project_corpus_dir("visual-composer")):
        assert str(d).startswith(str(bp.CORPORA_DIR))
        assert not str(d).startswith(str(bp.REPO_ROOT)), (
            f"{d} is inside the repo — every worktree would fork it")
    # repo-relative strings pin the CURRENT (post-reorg) layout
    assert bp.DUSK_GIFTS_REL == "bootstrap/dusk-gifts/"
    assert bp.COMPACT_SUMMARIES_REL == "docs/compact-summaries/"
    assert bp.WATCH_REPORTS_REL == "docs/watch_reports/"
    assert bp.BOOTSTRAP_REL == "bootstrap/"
    # Corpus …_REL constants are ABSOLUTE now (rel_dir falls back for out-of-repo
    # paths). Pin the SUFFIX — one dir == one queryable corpus — so the layout is
    # still asserted without re-pinning a location the move exists to change.
    assert bp.CORPORA_REL.endswith("/corpora/")
    assert bp.DESIGN_EXCELLENCE_CORPUS_REL.endswith("/corpora/design-excellence/")
    assert bp.INSIGHTS_CORPUS_REL.endswith("/corpora/insights-and-warts/")
    assert bp.PLATFORM_SPECS_REL.endswith("/corpora/platform-specs/")
    assert bp.DELIVERABLES_CORPUS_REL.endswith("/corpora/deliverables/")
    assert bp.DELIVERABLE_LEDGER_REL.endswith("/corpora/deliverables/deliverable-ledger.lts")
    # The profile MOVED and changed serialization on 2026-08-11: the legacy
    # `platform-specs/99-composeio-profile.yaml` was retired into the `.lts` form
    # after a two-way vocabulary diff, and now sits beside the decision corpus.
    assert bp.COMPOSEIO_PROFILE_REL.endswith(
        "/corpora/intelligence-console/platform-composeio-profile.lts")
    # ...and unlike the yaml it must actually EXIST, or every consumer that resolves
    # the domain profile through this constant is silently profile-less.
    assert bp.COMPOSEIO_PROFILE.is_file()
    assert bp.INTELLIGENCE_CONSOLE_CORPUS_REL.endswith("/corpora/intelligence-console/")
    assert bp.project_corpus_dir("bus-storage") == bp.CORPORA_DIR / "bus-storage"


def _py_files():
    this = os.path.abspath(__file__)
    module = os.path.abspath(bp.__file__)
    for root in _CODE_ROOTS:
        for dirpath, _dirs, files in os.walk(root):
            if os.sep + "tests" + os.sep in dirpath + os.sep:
                continue
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                if p in (this, module):
                    continue
                yield p


def test_no_stale_dusk_gifts_literal_in_runtime_code():
    """No runtime .py may hard-code the pre-reorg `docs/dusk-gifts` path.

    Reorg-proofing: continuity paths come from composeio.bootstrap_paths. If a
    reorg moves a directory, the constant changes in ONE place; a hard-coded
    literal would silently rot. This guard makes the rot a red test instead."""
    offenders = []
    for path in _py_files():
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if _STALE in line:
                    offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "stale hard-coded '%s' path literal(s) found in runtime code — import "
        "the constant from composeio.bootstrap_paths instead:\n%s"
        % (_STALE, "\n".join(offenders))
    )


def test_no_retired_toplevel_deliverable_ledger_literal_in_runtime_code():
    """No runtime .py may hard-code the RETIRED top-level `deliverables/deliverable-
    ledger.lts` path — the location is resolved through
    `composeio.bootstrap_paths.DELIVERABLE_LEDGER(_REL)`.

    This is the regression guard for DEFECT-DELIVERABLE-LEDGER-RETIRED-PATH: the
    sweep, its live monitor, its filesystem watcher, its bus publisher, and the
    console data layer must all name the ledger via the single source of truth.
    A surviving literal points a `--write` at the empty retired path, which both
    materializes a placeholder 'ghost' ledger there AND, if that output reaches the
    real corpus path, clobbers curated enrichment ([CXC-DS-LEDGER-LOCATION-SINGLE-
    SOURCE]). The `corpora/`-rooted canonical spelling is the ONLY permitted form."""
    offenders = []
    for path in _py_files():
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if _STALE_LEDGER in line and _CANONICAL_LEDGER not in line:
                    offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "retired top-level '%s' ledger literal(s) found in runtime code — resolve "
        "the location via composeio.bootstrap_paths.DELIVERABLE_LEDGER(_REL) "
        "(canonical: '%s') instead:\n%s"
        % (_STALE_LEDGER, _CANONICAL_LEDGER, "\n".join(offenders))
    )


def test_no_retired_ux_composer_specs_literal_in_runtime_code():
    """No runtime .py may cite the retired `ux_composer/specs` spec pool.

    The corpus consolidation moved the whole `ux_composer/specs/` tree to
    `corpora/platform-specs/` (shared) and the per-project graphs to
    `corpora/<name>/`; the single source of truth is
    `composeio.bootstrap_paths.PLATFORM_SPECS_DIR(_REL)`. A surviving literal
    points at a directory that no longer exists — a spec citation in a docstring
    rots silently and any functional load would miss. bootstrap_paths.py itself is
    exempt (it documents the *former* pool by name) and is auto-excluded here as
    the module under test."""
    offenders = []
    for path in _py_files():
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, start=1):
                if _STALE_SPECS_DIR in line:
                    offenders.append(f"{os.path.relpath(path, REPO_ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "retired '%s' spec-pool literal(s) found in runtime code — cite the current "
        "`corpora/platform-specs/…` location (single source of truth: "
        "composeio.bootstrap_paths.PLATFORM_SPECS_DIR(_REL)) instead:\n%s"
        % (_STALE_SPECS_DIR, "\n".join(offenders))
    )
