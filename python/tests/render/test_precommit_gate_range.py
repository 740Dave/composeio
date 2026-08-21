"""
Tests for `scripts/pre-commit-tests.sh` — specifically its --range mode, the gate
for LANDINGS.

WHY THIS TEST LIVES IN python/tests/render/ : this is the tree the gate itself maps
(`python/*.py -> the python suite, python/tests`), so the gate runs its own tests. A test for the gate
that the gate never runs is exactly the vacuity this file exists to prevent.

WHAT IT PINS (the defect, 2026-08-08 — the third hole in that one script):
  `git cherry-pick` does not fire the pre-commit hook, and during/after a cherry-pick
  the index comparison is empty. Every dispatched subagent branch is landed onto main
  by cherry-pick, so running the gate against a landing printed
  "nothing staged -> pass" HAVING TESTED ZERO FILES — every landing ungated BY
  CONSTRUCTION, while emitting a green that was then cited as coverage.

THE TWO INVARIANTS, in priority order:
  1. AN EMPTY FILE LIST MUST NEVER SILENTLY MEAN PASS. "This range genuinely touches
     no python" (non-empty file list, zero *.py) is a legitimate pass; "I could not
     determine the file set" (bad argument, unresolvable rev, empty range, git error)
     MUST exit non-zero. Those were the same code path, and that conflation WAS the
     defect. Several tests below assert the *distinction*, not just the exit code.
  2. THE OUTPUT MUST NAME MODE + COUNTS, so a human reading a log can tell coverage
     from vacuity. A gate indistinguishable from a no-op by its own output is the
     thing being fixed.

Most cases run against a HERMETIC fixture repo (a real git repo built in a tmpdir with
a copy of the script, so REPO_ROOT resolves to the fixture). That drives the REAL
classifier through the REAL `git diff` — no mocking — while staying independent of
this repo's history. `--dry-run` is used so suite SELECTION is asserted without
recursively executing the python suite from inside the python suite.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "pre-commit-tests.sh")

# The landing used in the acceptance demo: the UART message-type de-fork, which DID touch
# python. The --cached path reports "nothing staged -> pass" over it; --range must select
# the python suite. Skipped (not failed) if the objects are absent, e.g. a shallow clone.
REAL_LANDING_RANGE = "32dba14..23c82d4"

# git invoked with every ambient influence pinned off: the user's global core.hooksPath
# points at THIS repo's .githooks, so an unguarded commit in the fixture would re-enter
# the very gate under test.
_GIT_ISOLATION = [
    "-c", "user.name=precommit gate test",
    "-c", "user.email=gate@test.invalid",
    "-c", "commit.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "init.defaultBranch=main",
]


def _clean_env():
    """Environment with EVERY GIT_* pointer stripped.

    NOT paranoia — this was earned. `git` EXPORTS GIT_DIR/GIT_INDEX_FILE/GIT_WORK_TREE
    into every hook it runs, and those BEAT `git -C <dir>`: -C changes the cwd, it does
    not repoint discovery. So when this very file was first run from inside the
    pre-commit hook (the gate testing its own commit), the fixture's `git -C <tmp>
    commit` wrote its five throw-away commits onto the REAL worktree's HEAD, and the
    commit then died on `cannot lock ref 'HEAD'`. A test for the gate must be incapable
    of touching the repo it is gating, under EVERY environment it can be launched from —
    hook, bare pytest, or CI. GIT_CEILING_DIRECTORIES additionally stops upward
    discovery from escaping the tmpdir if a `-C` is ever dropped.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = tempfile.gettempdir()
    return env


def _run_script(args, script=SCRIPT, cwd=None, env=None):
    """Run the gate. cwd is deliberately varied/irrelevant — the script pins every git
    call to its own REPO_ROOT (the 6E7171 lesson: the file set and the tests that run
    must come from the same tree)."""
    return subprocess.run(
        ["bash", script, *args],
        capture_output=True, text=True,
        cwd=cwd or os.path.dirname(script),
        env=_clean_env() if env is None else env,
    )


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", repo, *_GIT_ISOLATION, *args],
        capture_output=True, text=True, check=check, env=_clean_env(),
    )


def _plan_suite_tokens(stdout):
    """The selected-suite tokens from the gate's plan line
    ('... N python ->T1 T2 suite(s)'). Returns [] when no suite is mapped. Robust to how
    MANY suites fire — the plan lists one token per selected tree, so callers assert
    MEMBERSHIP, never that a suite is the SOLE one. Coupling to exclusivity is exactly what
    rots against a frozen range as the routing table grows."""
    for ln in stdout.splitlines():
        if "->" in ln and ln.rstrip().endswith("suite(s)"):
            return ln.split("->", 1)[1].rsplit("suite(s)", 1)[0].split()
    return []


def _python_suite_selected(stdout):
    """python/tests is AMONG the selected suites AND its detail block was emitted. The detail
    line is printed only when run_python fires, so it proves selection independent of how the
    plan line is worded or how many other trees also changed."""
    return (
        "python/tests" in _plan_suite_tokens(stdout)
        and "-> python suite (python/tests: render + maker + top-level)" in stdout
    )


def _write(repo, rel, text):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def _commit(repo, files, message):
    """Write files ({rel: text}), stage them, commit, return the new sha."""
    for rel, text in files.items():
        _write(repo, rel, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "--no-verify", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@contextlib.contextmanager
def _fixture_repo():
    """A real git repo carrying a copy of the gate, with a history that exercises every
    arm of the routing table. Yields (repo_path, script_path, shas)."""
    tmp = tempfile.mkdtemp(prefix="precommit-gate-")
    try:
        # -c options are GIT-level and must precede the subcommand. With GIT_DIR inherited,
        # `git init <dir>` would re-init the AMBIENT repo instead of the tmpdir.
        subprocess.run(["git", *_GIT_ISOLATION, "init", "-q", tmp],
                       capture_output=True, text=True, check=True, env=_clean_env())
        os.makedirs(os.path.join(tmp, "scripts"), exist_ok=True)
        script = os.path.join(tmp, "scripts", "pre-commit-tests.sh")
        shutil.copy2(SCRIPT, script)
        # ARM 0 (the .lts property gate, LAYER 3 of [SPEC:AUTH-PATH]) is a SIBLING script,
        # and pre-commit-tests.sh REFUSES (exit 2) if it is absent — deliberately, because
        # an absent gate must say so rather than pass by default. So the fixture has to
        # carry it too: a fixture that omits a hard dependency does not exercise the script
        # under test, it exercises the refusal path.
        shutil.copy2(os.path.join(REPO_ROOT, "scripts", "lts_property_gate.py"),
                     os.path.join(tmp, "scripts", "lts_property_gate.py"))

        shas = {}
        shas["base"] = _commit(tmp, {"docs/readme.md": "start\n"}, "base: docs only")
        shas["python"] = _commit(
            tmp,
            {"python/composeio/render/thing.py": "VALUE = 1\n", "docs/note.md": "n\n"},
            "touch the python/ tree",
        )
        shas["intelligence"] = _commit(
            tmp,
            {"compose_projects/intelligence/console/data_sources.py": "ROSTER = []\n"},
            "touch the compose_projects/intelligence/ tree",
        )
        shas["unmapped"] = _commit(
            tmp, {"tools/helper.py": "x = 2\n"}, "touch an UNMAPPED python tree"
        )
        shas["docsonly"] = _commit(
            tmp, {"docs/more.md": "m\n"}, "touch no python at all"
        )
        # A commit touching python/ AND a top-level-routed tree (services/) in ONE change — the
        # exact shape of the real acceptance landing (python transport + services/can_mqtt_bridge).
        # Range docsonly..multitree fires run_python AND run_toplevel, so the plan carries TWO
        # tokens ("python/tests tests"). It exists to pin, HERMETICALLY, the membership invariant
        # the frozen-SHA real-landing test rotted on: python/tests must be AMONG the suites when
        # more than one tree changed. A fixture cannot fall out of a shallow clone or drift as the
        # routing table grows, so this arm of the coverage can never rot the way the SHA range did.
        shas["multitree"] = _commit(
            tmp,
            {
                "python/composeio/render/multi.py": "M = 1\n",
                "services/can_mqtt_bridge/bridge.py": "B = 1\n",
            },
            "touch python/ AND services/ (top-level) in one commit",
        )
        yield tmp, script, shas
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------------------
# 1. The feature: a range containing a python change selects the right suite.
# --------------------------------------------------------------------------------------

def test_range_with_python_change_selects_the_python_suite():
    with _fixture_repo() as (repo, script, shas):
        res = _run_script(
            ["--range", f"{shas['base']}..{shas['python']}", "--dry-run"], script=script
        )
        assert res.returncode == 0, res.stderr
        assert "-> python/tests suite(s)" in res.stdout, res.stdout
        assert "python suite (python/tests: render + maker + top-level)" in res.stdout, res.stdout
        # Selection must be EXACT — a range touching only python/ must not drag in
        # the intelligence suite.
        assert "intelligence suite" not in res.stdout, res.stdout


def test_range_with_intelligence_change_selects_both_suites():
    """compose_projects/intelligence/ routes to BOTH suites: python/tests/render holds tests that import
    intelligence.console.data_sources and assert its ROSTER (a88b540 shipped red that
    way). The range path must inherit that, not re-derive it."""
    with _fixture_repo() as (repo, script, shas):
        res = _run_script(
            ["--range", f"{shas['python']}..{shas['intelligence']}", "--dry-run"],
            script=script,
        )
        assert res.returncode == 0, res.stderr
        # The plan line names the PATH pytest is actually given (script comment: "Name the
        # PATH pytest is actually given, so the output cannot outrun the coverage"). The tree
        # moved intelligence/ -> compose_projects/intelligence/, so the named path moved with
        # it; the invariant is unchanged — the plan token still equals the pytest argument.
        assert "-> python/tests compose_projects/intelligence suite(s)" in res.stdout, res.stdout
        assert "intelligence suite (compose_projects/intelligence/)" in res.stdout, res.stdout


def test_range_reports_mode_and_file_counts():
    """Requirement 2: the output names the mode and the counts it classified, so a log
    reader can tell coverage from vacuity."""
    with _fixture_repo() as (repo, script, shas):
        rng = f"{shas['base']}..{shas['python']}"
        res = _run_script(["--range", rng, "--dry-run"], script=script)
        assert res.returncode == 0, res.stderr
        # 2 files changed (one .py, one .md), 1 of them python.
        assert f"range {rng}: 2 changed files, 1 python ->" in res.stdout, res.stdout


def test_range_mode_ignores_an_inherited_GIT_DIR():
    """`git -C <dir>` does NOT beat GIT_DIR — and git EXPORTS GIT_DIR into every hook it
    runs. So a gate invoked from a hook, or from any wrapper that sets it, would classify
    THAT repo while running THIS repo's tests: the 6E7171 wrong-tree class through a side
    door, and green by construction again. Range mode must strip the ambient pointers.

    Pinned by pointing GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE at a DECOY repo whose history
    does not contain the fixture's shas: if the decoy won, endpoint resolution would fail
    and the gate would exit 2 instead of classifying the fixture's range.
    """
    with _fixture_repo() as (repo, script, shas):
        decoy = tempfile.mkdtemp(prefix="precommit-decoy-")
        try:
            subprocess.run(["git", *_GIT_ISOLATION, "init", "-q", decoy],
                           capture_output=True, text=True, check=True, env=_clean_env())
            _commit(decoy, {"docs/decoy.md": "decoy\n"}, "decoy history")

            polluted = _clean_env()
            polluted["GIT_DIR"] = os.path.join(decoy, ".git")
            polluted["GIT_WORK_TREE"] = decoy
            polluted["GIT_INDEX_FILE"] = os.path.join(decoy, ".git", "index")

            rng = f"{shas['base']}..{shas['python']}"
            res = _run_script(["--range", rng, "--dry-run"], script=script, env=polluted)
            assert res.returncode == 0, res.stdout + res.stderr
            assert f"range {rng}: 2 changed files, 1 python ->" in res.stdout, res.stdout
            assert "-> python/tests suite(s)" in res.stdout, res.stdout
        finally:
            shutil.rmtree(decoy, ignore_errors=True)


def test_range_with_unmapped_python_is_named_loudly_and_runs_nothing():
    with _fixture_repo() as (repo, script, shas):
        res = _run_script(
            ["--range", f"{shas['intelligence']}..{shas['unmapped']}", "--dry-run"],
            script=script,
        )
        assert res.returncode == 0, res.stderr
        assert "NO test mapping" in res.stdout, res.stdout
        assert "tools/helper.py" in res.stdout, res.stdout
        assert "no MAPPED python" in res.stdout, res.stdout


def test_range_touching_python_AND_a_toplevel_tree_lists_python_AMONG_suites():
    """MULTI-TREE membership — the hermetic guard for the class that rotted the real-landing
    test. A range touching python/ AND services/ selects BOTH suites, so the plan carries two
    tokens ("python/tests tests"). python/tests must be AMONG them. A naive exact single-suite
    substring ("-> python/tests suite(s)") does NOT match here — which is precisely the assertion
    the services/*.py arm silently broke against the frozen acceptance range on 2026-08-16. A
    fixture pins this without a hardcoded SHA, so it cannot rot as the routing table grows."""
    with _fixture_repo() as (repo, script, shas):
        res = _run_script(
            ["--range", f"{shas['docsonly']}..{shas['multitree']}", "--dry-run"],
            script=script,
        )
        assert res.returncode == 0, res.stderr
        tokens = _plan_suite_tokens(res.stdout)
        assert "python/tests" in tokens, res.stdout   # the python arm
        assert "tests" in tokens, res.stdout          # the top-level (services/) arm
        # The exact single-suite substring MUST NOT match a multi-tree landing — that mismatch
        # IS the rot, reproduced hermetically so it can never silently return.
        assert "-> python/tests suite(s)" not in res.stdout, res.stdout
        # ...and the python detail block still proves the python suite was really selected.
        assert _python_suite_selected(res.stdout), res.stdout


# --------------------------------------------------------------------------------------
# 2. THE EMPTY-SET RULE — the load-bearing half. "no python" and "no idea" must not be
#    the same outcome.
# --------------------------------------------------------------------------------------

def test_range_touching_no_python_is_a_LEGITIMATE_pass_with_counts():
    """The good half of the distinction: a DETERMINED, non-empty file set that holds no
    python passes — and says how many files it looked at, so the pass is auditable."""
    with _fixture_repo() as (repo, script, shas):
        rng = f"{shas['unmapped']}..{shas['docsonly']}"
        res = _run_script(["--range", rng, "--dry-run"], script=script)
        assert res.returncode == 0, res.stderr
        assert "1 changed files, 0 python -> no mapped suite" in res.stdout, res.stdout
        assert "no python changes in range" in res.stdout, res.stdout


@pytest.mark.parametrize(
    "args, expect_in_stderr",
    [
        # Unresolvable revisions — the classic typo'd landing range.
        (["--range", "deadbee1111..deadbee2222", "--dry-run"], "does not resolve to a commit"),
        # Argument missing entirely.
        (["--range"], "--range requires"),
        # A following FLAG must not be swallowed as if it were a revision — otherwise the
        # real mistake (missing argument) is misreported AND the --dry-run goes unapplied,
        # which in a non-dry run would have executed suites the caller never asked for.
        (["--range", "--dry-run"], "--range requires"),
        # A bare revision: `git diff <rev>` would silently compare the WORKING TREE and
        # look like it worked. Must be refused on shape, before git is ever asked.
        (["--range", "HEAD", "--dry-run"], "is not a range"),
        (["--range", "", "--dry-run"], "empty argument"),
        # Unknown flags must not be shrugged off into a default-mode pass.
        (["--nonsense"], "unknown argument"),
    ],
)
def test_undeterminable_range_EXITS_NONZERO(args, expect_in_stderr):
    with _fixture_repo() as (repo, script, shas):
        res = _run_script(args, script=script)
        assert res.returncode != 0, f"expected non-zero, got 0\n{res.stdout}"
        assert expect_in_stderr in res.stderr, res.stderr
        # And it must not have emitted anything a log-skimmer would read as a green.
        assert "-> pass" not in res.stdout, res.stdout
        assert "green" not in res.stdout, res.stdout


def test_empty_range_EXITS_NONZERO_rather_than_passing():
    """A range whose endpoints both resolve but which changed NOTHING did not describe a
    landing (reversed endpoints / already-merged base / wrong branch). Passing there is
    the exact "reports green having run nothing" failure."""
    with _fixture_repo() as (repo, script, shas):
        res = _run_script(
            ["--range", f"{shas['python']}..{shas['python']}", "--dry-run"], script=script
        )
        assert res.returncode != 0, res.stdout
        assert "changed NO files" in res.stderr, res.stderr
        assert "REFUSING" in res.stderr, res.stderr


def test_determination_failure_uses_a_distinct_exit_code_from_test_failure():
    """Exit 2 = cannot determine the change set; exit 1 = a test went red. A gate whose
    failure modes are indistinguishable in a log is the thing being fixed."""
    with _fixture_repo() as (repo, script, shas):
        res = _run_script(["--range", "nope1234..nope5678"], script=script)
        assert res.returncode == 2, res.returncode


# --------------------------------------------------------------------------------------
# 3. The default (--cached) path must not have moved — the git hook calls this with no
#    arguments and must behave byte-identically.
# --------------------------------------------------------------------------------------

def test_default_mode_is_cached_and_unchanged():
    with _fixture_repo() as (repo, script, shas):
        # Nothing staged -> the historical line, verbatim. It is asserted as a LINE rather
        # than as the whole of stdout because arm 0 (the .lts property gate) now announces
        # that a --dry-run did NOT execute it. A dry run that stayed silent about a gate it
        # skipped would be the "green that outruns its coverage" this file exists to kill,
        # so the extra line is the correct behaviour and the assertion is what moved.
        res = _run_script(["--dry-run"], script=script)
        assert res.returncode == 0, res.stderr
        lines = [ln for ln in res.stdout.strip().splitlines()]
        assert "pre-commit-tests: nothing staged -> pass" in lines, res.stdout
        assert "pre-commit-tests: DRY RUN — .lts property gate not executed" in lines, res.stdout

        # Stage a python file -> cached mode names itself and selects the python suite.
        _write(repo, "python/composeio/render/staged.py", "X = 3\n")
        _git(repo, "add", "python/composeio/render/staged.py")
        res = _run_script(["--dry-run"], script=script)
        assert res.returncode == 0, res.stderr
        assert "cached: 1 changed files, 1 python -> python/tests suite(s)" in res.stdout, res.stdout


def test_explicit_cached_flag_matches_the_no_argument_default():
    """One classifier, two input sources — and the explicit spelling of the default must
    be indistinguishable from the default itself."""
    with _fixture_repo() as (repo, script, shas):
        _write(repo, "python/composeio/render/staged.py", "X = 4\n")
        _git(repo, "add", "python/composeio/render/staged.py")
        implicit = _run_script(["--dry-run"], script=script)
        explicit = _run_script(["--cached", "--dry-run"], script=script)
        assert implicit.returncode == explicit.returncode
        assert implicit.stdout == explicit.stdout


# --------------------------------------------------------------------------------------
# 4. The real thing: the acceptance range, against this repo's own history.
# --------------------------------------------------------------------------------------

def test_real_landing_range_selects_the_python_suite_where_cached_runs_nothing():
    have = subprocess.run(
        ["git", "-C", REPO_ROOT, "rev-parse", "--verify", "--quiet",
         REAL_LANDING_RANGE.split("..")[1] + "^{commit}"],
        capture_output=True, text=True, env=_clean_env(),
    )
    if have.returncode != 0:
        pytest.skip(f"{REAL_LANDING_RANGE} not present in this clone")

    res = _run_script(["--range", REAL_LANDING_RANGE, "--dry-run"])
    assert res.returncode == 0, res.stderr
    assert f"range {REAL_LANDING_RANGE}:" in res.stdout, res.stdout
    # python/tests must be AMONG the selected suites — NOT the sole one. This is a REAL landing
    # against FROZEN history that also touches services/can_mqtt_bridge/bridge.py, which correctly
    # adds the top-level `tests` token (plan: "python/tests tests suite(s)"). The old exact
    # single-suite substring "-> python/tests suite(s)" coupled this fixed range to the routing
    # table and ROTTED on 2026-08-16 (afa27ad) when the services/*.py -> run_toplevel arm landed:
    # the gate's output was correct, the assertion's assumption of exclusivity was not. Assert
    # MEMBERSHIP so a future arm this fixed range happens to hit cannot break it the same way.
    assert _python_suite_selected(res.stdout), res.stdout
    # The whole point: this landing touches python, and the count must prove it was seen.
    counted = [ln for ln in res.stdout.splitlines() if "changed files," in ln]
    assert counted, res.stdout
    n_python = int(counted[0].split("changed files,")[1].split("python")[0].strip())
    assert n_python > 0, counted[0]
