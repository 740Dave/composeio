"""
Tests for `scripts/pre-commit-tests.sh` — ROUTING REACHABILITY.

WHY THIS FILE EXISTS (2026-08-10, the FOURTH hole in that one script). Three holes
had been found in the gate and each was patched as an instance:

  * 6E7171 — REPO_ROOT derived from BASH_SOURCE, so a worktree commit was gated by
    the MAIN checkout (fixed 061503b);
  * 9CB4EE — `compose_projects/intelligence/` matched no routing arm and was skipped in SILENCE;
  * the --range hole — a cherry-picked landing reported "nothing staged -> pass"
    having tested zero files (fixed 3d5d7cb).

The fourth: the python arm was named "the render suite" and ran exactly
`pytest tests/render`, while `python/tests/maker/` sat right beside it, mapped by
NOTHING. A commit under `python/composeio/maker/**` printed
"green -> render suite" HAVING NEVER RUN maker's tests — and two of them had been
red since 7866f90 (2026-07-28) for 13 days, unseen.

THE CLASS, which is what this file pins rather than the instance: **a mapping can
silently cover less than its name claims.** Widening one arm fixes today's suite and
leaves tomorrow's to be discovered by a shipped-red commit months later. So:

  EVERY directory in this repository that holds tests must either be REACHABLE from
  a pytest invocation the gate can actually make, or appear in the EXPLICIT,
  reasoned exclusion list below.

That makes an orphaned suite fail at AUTHORING time — you cannot add
`python/tests/foo/` (or a whole new tests tree) without either the gate covering it
or a human writing down, here, why it deliberately does not.

HOW IT READS THE ROUTING: it parses the REAL `( cd "$X" && "$PY" -m pytest <target>`
invocations out of the script — the commands that actually execute — rather than a
comment or a second copy of the routing table. A second source of truth for the
routing would be the same drift hazard one level up. If the invocation SHAPE ever
changes, the parse finds nothing and the vacuity guard fails loudly; it never
silently passes on an empty target set (that conflation is the whole family of bugs
this script's history is made of).

NO GIT SUBPROCESS HERE, deliberately: `git ls-files` would only see the index, and
git exports GIT_DIR/GIT_INDEX_FILE into hooks and subprocesses where they beat
`git -C <dir>`. A plain filesystem walk sees a brand-new suite the moment it is
written, before it is even staged, and has no ambient-environment failure mode.
"""

from __future__ import annotations

import fnmatch
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "pre-commit-tests.sh")

# Directories a source walk must never descend into. Nothing here can hold a suite
# the gate is expected to run.
_PRUNE = {"__pycache__", "node_modules", "site-packages", "build", "dist", "venv",
          ".git"}

# --------------------------------------------------------------------------------------
# THE EXCLUSION LIST — deliberately-unmapped test trees, each with its reason.
#
# This list is the POINT of the file: an orphaned suite is either mapped or WRITTEN DOWN.
# Adding an entry here is a decision that gets reviewed in a diff; forgetting to map a
# suite is a silence that does not. Removing a tree from the gate's reach without
# recording why is exactly how python/tests/maker went unrun for 13 days.
# --------------------------------------------------------------------------------------
# RETIRED 2026-08-11 — "tests" and "tests/peer". Their entry read: "carries 3 known
# failures ... PLUS 4 deliberately-red cases in tests/test_lts_authored_edges_survive_parse.py,
# which are correct-as-red until the FactLattice parser proposal is applied. Mapping this tree
# would block every commit and train everyone to --no-verify, destroying the gate outright.
# Map it when those are resolved, not before."
#
# All four are resolved. The parse cases went green with the engine parser work (SL-9/SL-10,
# then SL-18 and the P5 mapping form); test_document_store asserted a superseded contract
# ([DS-BINARY-C5]); test_lts_engine_rail_probe was a live defect, fixed by [LEC-CORPUS-SEAT];
# test_decisions_feed_wiring asserted a decision superseded by [CXC-GV-EMITTER-RETIRED].
# tests/ = 378 passed, tests/peer = 74 passed. The precondition the entry set is met, so the
# entry is removed rather than left as a stale exemption.
DELIBERATELY_UNMAPPED = {
    "services": (
        "services/ has no fast suite and its .py already routes to the gate's LOUD "
        "'unmapped python' notice, which names the file rather than pretending to cover "
        "it. Mapping it would mean standing up a services suite first."
    ),
}


def _read_script() -> str:
    with open(SCRIPT, "r", encoding="utf-8") as fh:
        return fh.read()


def _pytest_targets():
    """The pytest invocations the gate can actually make, as (cwd_var, target) pairs.

    Parsed from the EXECUTED commands, not from a comment: the routing must have one
    source of truth, and it is the code that runs.
    """
    pat = re.compile(
        r'\(\s*cd\s+"\$(\w+)"\s*&&\s*"\$PY"\s+-m\s+pytest\s+(\S+)'
    )
    return pat.findall(_read_script())


def _resolved_roots():
    """The pytest targets as absolute directories."""
    text = _read_script()
    # Pin the two cwd variables to what the script defines them as, so a rename of
    # PYDIR cannot silently repoint this test at the wrong tree.
    assert 'REPO_ROOT="$(cd "$(dirname' in text or "REPO_ROOT=" in text, \
        "the gate no longer defines REPO_ROOT; this test's cwd mapping is stale"
    assert 'PYDIR="$REPO_ROOT/python"' in text, \
        "the gate no longer defines PYDIR as $REPO_ROOT/python; update this test"
    base = {"REPO_ROOT": REPO_ROOT, "PYDIR": os.path.join(REPO_ROOT, "python")}

    roots = []
    for var, target in _pytest_targets():
        assert var in base, (
            f"the gate runs pytest from an unrecognised cwd variable '${var}'; "
            f"this test cannot resolve it — teach it the mapping"
        )
        roots.append(os.path.realpath(os.path.join(base[var], target)))
    return roots


def _test_dirs():
    """Every directory in the repo holding a `test_*.py`, repo-relative."""
    found = []
    for root, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in _PRUNE and not d.startswith(".")]
        if any(fnmatch.fnmatch(f, "test_*.py") for f in filenames):
            found.append(os.path.relpath(root, REPO_ROOT))
    return sorted(found)


def _is_under(child: str, parent: str) -> bool:
    child = os.path.realpath(child)
    parent = os.path.realpath(parent)
    return child == parent or child.startswith(parent + os.sep)


# --------------------------------------------------------------------------------------
# 1. Vacuity guards. This whole file is an argument about silent under-coverage; a
#    version of it that passes because it found nothing to check would be the same bug.
# --------------------------------------------------------------------------------------

def test_the_parse_finds_the_gates_pytest_invocations():
    targets = _pytest_targets()
    assert len(targets) >= 2, (
        f"parsed {len(targets)} pytest invocation(s) from {SCRIPT}. The invocation "
        f"shape changed and this test can no longer read the routing — it must FAIL "
        f"here rather than pass over an empty target set."
    )
    assert ("PYDIR", "tests") in targets, (
        f"the python arm no longer runs `pytest tests` from python/: {targets}"
    )
    assert ("REPO_ROOT", "compose_projects/intelligence") in targets, (
        f"the intelligence arm no longer runs `pytest intelligence`: {targets}"
    )


def test_the_walk_finds_the_known_suites():
    dirs = _test_dirs()
    for known in ("python/tests", "python/tests/render", "python/tests/maker", "tests"):
        assert known in dirs, (
            f"the test-directory walk missed {known!r} — it is broken, and a broken "
            f"walk would make every assertion below vacuously true. Found: {dirs}"
        )


# --------------------------------------------------------------------------------------
# 2. THE STRUCTURAL INVARIANT.
# --------------------------------------------------------------------------------------

def test_every_test_directory_is_reachable_or_explicitly_excluded():
    """No test directory may be ORPHANED — covered by no routing arm and named by no
    exclusion. This is the class, not the instance: it fires the day a new suite is
    written, not months later via a shipped-red commit."""
    roots = _resolved_roots()
    orphans = []
    for d in _test_dirs():
        if d in DELIBERATELY_UNMAPPED:
            continue
        abs_d = os.path.join(REPO_ROOT, d)
        if not any(_is_under(abs_d, r) for r in roots):
            orphans.append(d)

    assert not orphans, (
        "these test directories are reachable by NO routing arm in "
        "scripts/pre-commit-tests.sh:\n"
        + "".join(f"  {o}\n" for o in orphans)
        + "The gate would report a green having never run them — the exact shape of "
          "the python/tests/maker hole (13 days red, unseen).\n"
        "Fix ONE of:\n"
        "  (a) widen/add a routing arm so a pytest target covers the directory, or\n"
        "  (b) add it to DELIBERATELY_UNMAPPED in this file WITH the reason.\n"
        "Do not delete this assertion."
    )


def test_exclusion_list_has_no_stale_entries():
    """An exclusion that no longer names a real test directory is rot: it reads as a
    reviewed decision while covering nothing. Delete it when the tree goes away."""
    dirs = set(_test_dirs())
    stale = sorted(k for k in DELIBERATELY_UNMAPPED if k not in dirs)
    assert not stale, (
        f"DELIBERATELY_UNMAPPED names directories that hold no tests any more: {stale}. "
        f"Remove them so the list keeps meaning what it says."
    )


def test_every_exclusion_carries_a_reason():
    for tree, reason in DELIBERATELY_UNMAPPED.items():
        assert isinstance(reason, str) and len(reason) >= 60, (
            f"the exclusion for {tree!r} has no substantive reason. An unexplained "
            f"exclusion is indistinguishable from an oversight, which is the thing "
            f"this file exists to prevent."
        )


# --------------------------------------------------------------------------------------
# 3. The instance, pinned by name — so the regression reads clearly if it recurs.
# --------------------------------------------------------------------------------------

def test_python_tests_maker_is_covered_by_the_python_arm():
    """The specific hole: python/composeio/maker/** routes to the python arm, and that
    arm must actually collect python/tests/maker. Before 2026-08-10 it ran
    `pytest tests/render` and did not."""
    roots = _resolved_roots()
    maker = os.path.join(REPO_ROOT, "python", "tests", "maker")
    assert os.path.isdir(maker)
    assert any(_is_under(maker, r) for r in roots), (
        "python/tests/maker is not under any pytest target the gate runs; a change to "
        "python/composeio/maker/** would report green having skipped its own tests."
    )


def test_top_level_python_tests_are_covered_by_the_python_arm():
    """python/tests/ also holds LOOSE suites (test_bus_concurrent_publish.py,
    test_transport_mqtt_pending.py) that a `tests/render`-rooted arm silently skipped
    right alongside maker."""
    roots = _resolved_roots()
    top = os.path.join(REPO_ROOT, "python", "tests")
    assert any(_is_under(top, r) for r in roots), (
        "the loose suites directly under python/tests/ are unreachable from the gate."
    )
