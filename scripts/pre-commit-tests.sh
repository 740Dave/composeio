#!/usr/bin/env bash
# pre-commit-tests.sh — FAST relevant-test gate for commits AND for landings. (bash 3.2 compatible.)
#
# WHY THIS EXISTS (2026-07-22 postmortem): a commit shipped a RED test
# (python/tests/render/test_data_sources_supervisor.py went 11 -> 12 checks) because only a
# targeted selftest was run by hand, not the suite. This hook runs the FAST, RELEVANT subset
# before every commit so a red render test can't land unseen:
#   * the PYTHON SUITE  (python/tests — render + maker + the top-level bus/transport tests,
#     ~42s, ~2845 tests) whenever a python/ change is in the change set — this covers the exact
#     suite that catches the data_sources_supervisor regression;
#   * the INTELLIGENCE SUITE (compose_projects/intelligence/, ~2.5s, 140 tests) whenever an compose_projects/intelligence/ change is
#     in the change set — the console data layer, the launcher, and every publisher;
#   * the TOUCHED-AREA SELFTESTS — for each changed file under python/composeio/<pkg>/, the
#     nearest `selftest.py` (each ~0.1s).
# It is deliberately fast (seconds) so it is not bypassed out of annoyance, and it runs NOTHING
# when the change set touches no python (docs/specs commit -> instant pass).
#
# PLUS, AHEAD OF ALL OF THEM, ARM 0: the .lts PROPERTY RECONCILIATION GATE
# (scripts/lts_property_gate.py, LAYER 3 of [SPEC:AUTH-PATH]). ~0.2s. It parses every staged
# `.lts` with the REAL parser and REJECTS the commit if a property-shaped line the author wrote
# did not become a property. Deliberately NOT covered by SKIP_PRECOMMIT_TESTS — that flag exists
# to skip a 42s python suite on a docs commit, and a docs commit is exactly what arm 0 protects.
#
# ---------------------------------------------------------------------------------------------
# USAGE
#   scripts/pre-commit-tests.sh                          # default: gate the STAGED set (git hook)
#   scripts/pre-commit-tests.sh --cached                 # explicit form of the default
#   scripts/pre-commit-tests.sh --range <base>..<head>   # gate a LANDING (cherry-pick, merge, PR)
#   scripts/pre-commit-tests.sh --range <b>..<h> --dry-run   # classify + report, execute nothing
#
# EXIT CODES — three outcomes, never two:
#   0 = gated and green (or legitimately nothing to gate)
#   1 = a test FAILED (block)
#   2 = the gate could not DETERMINE what to test (block, loudly). Never a pass.
# ---------------------------------------------------------------------------------------------
#
# WHY --range EXISTS (2026-08-08 — the THIRD hole found in this one script). `git cherry-pick`
# does NOT fire the pre-commit hook, and during/after a cherry-pick the index comparison is empty
# — so running this gate against a landing printed "nothing staged -> pass" HAVING TESTED ZERO
# FILES. Every dispatched subagent branch is landed onto main by cherry-pick, so every landing in
# this project was ungated BY CONSTRUCTION, while emitting a green line that was then cited as
# coverage. `--range` routes `git diff --name-only <base>..<head>` through the SAME classifier as
# `--cached` — one routing table, two input sources. Do not fork the routing.
#
# WHAT --range DOES AND DOES NOT DO: it CLASSIFIES from the range but EXECUTES against the
# CHECKED-OUT WORKING TREE (pytest, the selftests, and the selftest.py existence probe all read
# the tree, not the range's blobs). Run it with the landing checked out — that is the intended
# use: land, then gate what you landed. Gating a range you have not checked out tests the wrong
# bytes and this script cannot tell you so.
#
# THE EMPTY-SET RULE (the load-bearing half of the --range fix). An empty file list must NEVER
# silently mean pass:
#   * "the range genuinely touches no python" -> the file list is NON-EMPTY and holds no *.py
#     -> legitimate pass, and the closing line says so WITH COUNTS.
#   * "I could not determine the file set" (bad --range argument, unresolvable revision, git diff
#     error, empty range) -> EXIT 2, loudly. Those two were the same code path; that conflation
#     WAS the defect.
#
# WHY THE PYTHON ARM RUNS `python/tests`, NOT `python/tests/render` (2026-08-10 — the FOURTH hole,
# same shape as the first three). The arm was named "the render suite" and ran exactly
# `pytest tests/render`, while `python/tests/maker/` sat right beside it, mapped by NOTHING. A
# commit under python/composeio/maker/** therefore printed "green -> render suite" HAVING NEVER RUN
# maker's own tests — and two of them had been red since 7866f90 (2026-07-28), for 13 days, unseen.
# The recurring defect is not any one missing arm: it is that a MAPPING CAN SILENTLY COVER LESS
# THAN ITS NAME CLAIMS. A per-directory arm reproduces that hazard on every future suite (add
# python/tests/foo/, forget the arm, ship red). Rooting the arm at the TREE cannot: every present
# and future suite under python/tests is selected by construction. It costs ~2s (39s -> 42s).
# The structural guard is python/tests/render/test_precommit_gate_reachability.py, which parses the
# pytest invocations BELOW and fails if any test directory is unreachable from all of them.
#
# TREE ROUTING ([DEFECT-THE-PRE-COMMIT-TEST-GATE-9CB4EE], 2026-08-08). Changed *.py is routed to
# the suite covering its tree. `compose_projects/intelligence/` used to match no rule at all and was skipped in
# SILENCE — the gate printed a pass having run nothing. Now: an unmapped .py tree is named LOUDLY,
# and the closing line reports only the suites that ACTUALLY ran, so a green can never be misread
# as coverage it never had. Unmapped trees warn rather than block, because ~14 top-level trees
# hold tracked .py and a blanket refusal would just train everyone to --no-verify.
#
# BYPASS (documented, for genuine WIP only):
#   git commit --no-verify                  # git skips the hook entirely
#   SKIP_PRECOMMIT_TESTS=1 git commit ...   # skips the TEST SUITES (not arm 0)
#   SKIP_LTS_PROPERTY_GATE=1 git commit ... # skips arm 0 only
# Each is a DELIBERATE action that leaves a trace, which is the property wanted
# ([AUTH-PATH-NOT-A-CAGE]); none of them is removed, because a gate nobody can pass gets
# patched out under deadline and the patch is permanent.
#
set -uo pipefail

# Repo root = parent of this script's dir (scripts/..). Works from any cwd / git worktree.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYDIR="$REPO_ROOT/python"
PY="${PYTHON:-python3}"


usage() {
  sed -n '/^# USAGE$/,/^# ----/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# Exit 2, not 1: "the gate cannot determine the change set" must be distinguishable IN A LOG from
# "a test went red". A gate whose failure modes are indistinguishable is the thing being fixed.
fatal() {
  echo "pre-commit-tests: !! $*" >&2
  echo "pre-commit-tests: !! REFUSING to report a pass on an undetermined change set (exit 2)." >&2
  exit 2
}

# ---------------------------------------------------------------------------------------------
# INPUT MODE. Two sources, ONE classifier. Default (no args) is --cached so the git hook, which
# invokes this with no arguments, behaves exactly as it did before --range existed.
# ---------------------------------------------------------------------------------------------
MODE="cached"
RANGE=""
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cached)  MODE="cached"; shift ;;
    --range)
      [ $# -ge 2 ] || fatal "--range requires an argument: --range <base>..<head>"
      # Never swallow a following FLAG as if it were a revision: `--range --dry-run` would
      # otherwise be reported as a bad revision rather than as the missing argument it is,
      # and the --dry-run would silently go unapplied.
      case "$2" in
        --*) fatal "--range requires a <base>..<head> argument, but got the flag '$2'" ;;
      esac
      MODE="range"; RANGE="$2"; shift 2 ;;
    --range=*) MODE="range"; RANGE="${1#--range=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fatal "unknown argument: '$1' (see --help)" ;;
  esac
done

# ---------------------------------------------------------------------------------------------
# ARM 0 — THE .lts PROPERTY RECONCILIATION GATE  (LAYER 3 of [SPEC:AUTH-PATH] §5)
#
# A `.lts` is PARSED INPUT, not a doc. A property-shaped line whose key region violates
# `^[a-z][a-z0-9_-]*$` parses as PROSE: the field vanishes from the node's `properties`, and
# `lts_lint` still reports the corpus CLEAN, because prose is not an error. `lost: 0` at 100%
# coverage is likewise not a gate — a dropped key produced exactly that reading. Eight such
# losses by one author in one day; the guard caught eight of eight, the author zero of eight.
# This arm reconciles the bytes ABOUT TO BE COMMITTED against the real parser's own
# line-consumption record and REJECTS — never warns, because a warning at commit time is read
# by the same author who wrote the losing line, which is the failure mode being fixed.
#
# ⛔ IT RUNS BEFORE THE SKIP_PRECOMMIT_TESTS BYPASS, ON PURPOSE, AND HAS ITS OWN.
# SKIP_PRECOMMIT_TESTS exists to skip a ~42s python suite IRRELEVANT to a docs/spec commit.
# But a docs/spec commit is EXACTLY the commit this arm protects, and it costs ~0.2s. Letting
# the test bypass disable it would leave the gate absent precisely where it matters most, and
# green-by-construction is the recurring defect this whole file is a monument to.
# Bypass it deliberately, and it leaves a trace:  SKIP_LTS_PROPERTY_GATE=1 git commit ...
#
# WHY HERE AND NOT IN .githooks/pre-commit: `core.hooksPath` is ONE ABSOLUTE path shared by
# every worktree, so logic added to the wrapper is whatever the MAIN checkout happens to have
# checked out — NOT what is being committed. This script is reached through the wrapper's
# `git rev-parse --show-toplevel`, so it is the committing worktree's own copy. That is the
# whole lesson of [DEFECT-THE-PRE-COMMIT-TEST-GATE-6E7171]: put the logic where the commit
# can reach it, or the gate is green by construction.
#
# It is CONTENT-based, not intent-based: it never asks which tool wrote the file or who ran
# git. It runs in git, outside the agent runtime, so unlike a PreToolUse hook its firing does
# not depend on `--dangerously-skip-permissions` or any Claude Code configuration.
# ---------------------------------------------------------------------------------------------
LTS_GATE="$REPO_ROOT/scripts/lts_property_gate.py"
if [ ! -f "$LTS_GATE" ]; then
  # Exit 2, not 1: "the gate is absent" is not "a check failed", and must never be a pass.
  fatal "scripts/lts_property_gate.py is MISSING from $REPO_ROOT — the .lts reconciliation arm cannot run"
fi
if [ "$DRY_RUN" -eq 1 ]; then
  echo "pre-commit-tests: DRY RUN — .lts property gate not executed"
elif [ "$MODE" = "range" ]; then
  ( cd "$REPO_ROOT" && "$PY" "$LTS_GATE" --range "$RANGE" ) || exit $?
else
  ( cd "$REPO_ROOT" && "$PY" "$LTS_GATE" --staged ) || exit $?
fi

# The TEST-suite bypass. Deliberately BELOW arm 0 — see the note there.
if [ "${SKIP_PRECOMMIT_TESTS:-}" = "1" ]; then
  echo "pre-commit-tests: SKIP_PRECOMMIT_TESTS=1 -> skipping the TEST SUITES (WIP bypass)"
  echo "pre-commit-tests: (the .lts property gate above is NOT skipped by this flag — use"
  echo "pre-commit-tests:  SKIP_LTS_PROPERTY_GATE=1 for that, or --no-verify for everything)"
  exit 0
fi

# RANGE-MODE git, with the ambient repo pointers STRIPPED. `git -C <dir>` changes the working
# directory but does NOT beat GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE, which git EXPORTS into every
# hook it runs. Inherit those and range mode silently classifies whatever repo invoked us instead
# of REPO_ROOT — the 6E7171 wrong-tree class, back through a side door. There is no legitimate
# caller that wants range mode to read a different repo than the one whose tests it then runs.
# (Deliberately NOT applied to --cached: during `git commit` GIT_INDEX_FILE can legitimately point
# at a temporary index — e.g. a partial/amend commit — and stripping it would gate the wrong set.)
git_range() {
  ( unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_PREFIX
    git -C "$REPO_ROOT" "$@" )
}

# Changed files (Added/Copied/Modified/Renamed -> new path), from whichever source the mode names.
# BOTH git invocations are pinned to REPO_ROOT so the file set and the tests-that-run always come
# from the SAME tree — the cwd/worktree mismatch that made 6E7171 green-by-construction.
changed=""
source_desc=""
case "$MODE" in
  cached)
    changed="$(git -C "$REPO_ROOT" diff --cached --name-only --diff-filter=ACMR)"
    git_rc=$?
    [ $git_rc -eq 0 ] || fatal "git diff --cached exited $git_rc — the staged set is UNKNOWN"
    source_desc="cached"
    if [ -z "$changed" ]; then
      # Preserved verbatim (default behaviour must not move): git never fires the hook with an
      # empty index, so this line is only reached by a hand-run, where "nothing staged" is an
      # honest, unambiguous statement about a source that answered successfully.
      echo "pre-commit-tests: nothing staged -> pass"
      exit 0
    fi
    ;;
  range)
    [ -n "$RANGE" ] || fatal "--range was given an empty argument"
    # Require the two-dot shape TEXTUALLY. `git diff <rev>` (no range) silently compares the
    # WORKING TREE against <rev> — a typo like `--range HEAD` would gate something else entirely
    # and still look like it worked. That is precisely the class of bug this file exists to kill.
    case "$RANGE" in
      *..*) : ;;
      *) fatal "--range '$RANGE' is not a range; expected <base>..<head>. A bare revision would make git compare the WORKING TREE instead, silently gating the wrong thing." ;;
    esac
    r_base="${RANGE%%..*}"   # 'A..B' -> A ; 'A...B' -> A ; '..B' -> ''  (empty = HEAD, per git)
    r_head="${RANGE##*..}"   # 'A..B' -> B ; 'A...B' -> B ; 'A..' -> ''
    for rev in "${r_base:-HEAD}" "${r_head:-HEAD}"; do
      git_range rev-parse --verify --quiet "${rev}^{commit}" >/dev/null \
        || fatal "--range endpoint '$rev' does not resolve to a commit in $REPO_ROOT"
    done
    changed="$(git_range diff --name-only --diff-filter=ACMR "$RANGE" --)"
    git_rc=$?
    [ $git_rc -eq 0 ] || fatal "git diff --name-only '$RANGE' exited $git_rc — the changed-file set is UNKNOWN"
    source_desc="range $RANGE"
    if [ -z "$changed" ]; then
      # THE EMPTY-SET RULE. A range asked to gate a landing that changed no file at all is not a
      # legitimate pass — it is a range that did not describe the landing (reversed endpoints,
      # already-merged base, wrong branch). A range that genuinely touches no python still yields
      # a NON-EMPTY list, and that case passes below, with counts.
      fatal "range '$RANGE' changed NO files — an undetermined change set, not a pass. Check the endpoints (reversed? already-merged base? wrong branch?)."
    fi
    ;;
esac

# ---------------------------------------------------------------------------------------------
# CLASSIFIER — ONE routing table, fed by EITHER input mode. Route each changed *.py to the suite
# that covers its TREE.
#
# [DEFECT-THE-PRE-COMMIT-TEST-GATE-9CB4EE] (2026-08-08): this used to be a single
# `case "$f" in python/*.py) touches_python=1` — a case-glob `*` spans `/`, so everything under
# python/ counted, but `compose_projects/intelligence/` matched NOTHING. That tree is a second first-class python
# tree (140 tests: the console data layer, the launcher, every publisher), so a commit touching
# only it printed "no python changes staged -> pass" and exited 0 having run no test at all. Two
# commits landed green-by-construction that way. The hazard is not the skip — it is that the green
# then gets cited as proof the guard-rail held.
#
# WHY LOUD-BUT-ALLOW RATHER THAN REFUSE for an unmapped tree: there are ~14 top-level trees holding
# tracked .py (ux_composer, compose_projects, services, tools, ...) and most have no fast suite. A
# hard refusal across all of them would be bypassed with --no-verify as a matter of routine, which
# destroys the gate outright — the exact failure this script's own header warns about. So an
# unmapped .py is reported LOUDLY and by name, and the closing line reports only what ACTUALLY ran.
# Silence is the bug; a refusal that trains people to bypass is a worse one.
#
# TO ADD A TREE: add a `case` arm below setting its run_* flag, run it in the numbered section
# further down, and name it in the summary. Keep those three in step.
run_python=0          # python/**.py changed       -> pytest python/tests + touched-area selftests
run_intelligence=0    # compose_projects/intelligence/**.py changed -> pytest compose_projects/intelligence/
run_toplevel=0        # tests/**.py OR corpora/**.lts changed -> pytest tests (repo-root tree)
run_fpstorage=0       # services/fingerprint_storage/**.py changed -> pytest services/fingerprint_storage/tests
run_timeservice=0     # services/time_service/**.py changed -> pytest services/time_service/tests
run_storagemon=0      # services/storage_monitor/**.py changed -> pytest services/storage_monitor/tests
unmapped=""           # changed .py in a tree with NO suite mapping (reported, never silent)
selftest_mods=""
n_files=0             # every changed file — so a log reader can tell COVERAGE from VACUITY
n_python=0            # ...of which *.py
while IFS= read -r f; do
  [ -z "$f" ] && continue
  n_files=$((n_files + 1))
  case "$f" in *.py) n_python=$((n_python + 1)) ;; esac
  # NOTE the compose_projects/intelligence/ arm sets BOTH flags. Tests are NOT tree-local here:
  # python/tests/render/ holds test_data_sources_supervisor.py and test_dsd_viewer.py,
  # which import compose_projects/intelligence/console/data_sources.py and assert its source ROSTER.
  # Routing compose_projects/intelligence/ to `pytest intelligence` alone let a88b540 ship RED — it grew
  # the roster 18 -> 20 and broke two render tests that `pytest intelligence` never runs.
  # Caught by hand minutes later; the gate must catch it, not a human.
  # services/ holds the LIVE services -- bus_storage (:8420), document_store (:8421),
  # the bridges -- and their tests live in the top-level tests/ tree, not beside them.
  # Mapped 2026-08-15 by the path-containment defect: tests/test_bus_storage.py had
  # existed since Phase 1 and tests/test_bus_storage_containment.py was added to enforce
  # [CXC-BUSSTORE-CONTAINMENT], yet editing services/bus_storage/server.py ran NEITHER.
  # A guard whose test never runs on a change to the guarded file is barely a guard: the
  # gate would have reported green while the containment primitive was being deleted.
  # Rooted at the TREE so every service, current and future, is selected by construction.
  case "$f" in
    python/*.py)       run_python=1 ;;
    compose_projects/intelligence/*.py|compose_projects/intelligence/*/*.py) run_intelligence=1; run_python=1 ;;
    tests/*.py)        run_toplevel=1 ;;
    services/*.py)     run_toplevel=1 ;;
    *.py)              unmapped="$unmapped  $f"$'\n' ;;
  esac
  # A CORPUS FILE IS CODE, NOT A DOC. `.lts` is parsed input: a mis-cased property key
  # or a malformed relationship triple SILENTLY drops content from the graph. The
  # enforcing suite is tests/test_lts_authored_edges_survive_parse.py
  # ([CONV-RELVERB-NO-SILENT-DROP]). Without this arm an .lts-only landing routed to
  # NOTHING and printed a green "no python changes -> pass" -- which is exactly what
  # happened landing the 17 near-miss property-key fixes, whose entire purpose was
  # "make these parse". Coverage of a parser's INPUT cannot key on the file extension
  # of its implementation.
  case "$f" in
    corpora/*.lts) run_toplevel=1 ;;
  esac

  # services/fingerprint_storage/ ships its OWN fast suite BESIDE it
  # (services/fingerprint_storage/tests), NOT in the top-level tests/ tree, so the
  # services/*.py -> top-level arm above does not run it. Route it explicitly, rooted at
  # the service's own test dir, so a change to the fingerprint_storage service runs the 10
  # in-process integration tests that guard it. Separate case block so it STACKS with the
  # top-level arm (same shape as the corpora arm) rather than replacing it. Added 2026-08-18
  # to close the reachability orphan services/fingerprint_storage/tests landed by fee0e8a.
  case "$f" in
    services/fingerprint_storage/*.py) run_fpstorage=1 ;;
  esac

  # services/time_service/ ships its OWN fast suite (services/time_service/tests), same shape
  # as fingerprint_storage. Added 2026-08-20 to close the reachability orphan
  # services/time_service/tests landed with the time_service platform build.
  case "$f" in
    services/time_service/*.py) run_timeservice=1 ;;
  esac

  # services/storage_monitor/ ships its OWN fast suite (services/storage_monitor/tests), same shape.
  # Added 2026-08-20 to close the reachability orphan for the skipper disk-space sensor.
  case "$f" in
    services/storage_monitor/*.py) run_storagemon=1 ;;
  esac
  case "$f" in
    python/composeio/*.py)
      dir="$(dirname "$f")"
      while : ; do
        if [ -f "$REPO_ROOT/$dir/selftest.py" ]; then
          mod="${dir#python/}"; mod="$(printf '%s' "$mod" | tr '/' '.').selftest"
          selftest_mods="$selftest_mods$mod"$'\n'
          break
        fi
        [ "$dir" = "python/composeio" ] && break
        dir="$(dirname "$dir")"
        case "$dir" in python/composeio*) : ;; *) break ;; esac
      done
      ;;
  esac
done <<EOF
$changed
EOF

# SAY WHAT IT IS ABOUT TO RUN, naming the MODE, the COUNTS, and the SUITE PATHS. A gate that
# cannot be told apart from a no-op by its own output is the thing being fixed — and a gate that
# names a suite ("render") narrower than the tree it claims to cover is how the maker hole hid.
# Name the PATH pytest is actually given, so the output cannot outrun the coverage.
plan=""
[ "$run_python" -eq 1 ] && plan="$plan python/tests"
[ "$run_intelligence" -eq 1 ] && plan="$plan compose_projects/intelligence"
[ "$run_toplevel" -eq 1 ] && plan="$plan tests"
[ "$run_fpstorage" -eq 1 ] && plan="$plan services/fingerprint_storage/tests"
[ "$run_timeservice" -eq 1 ] && plan="$plan services/time_service/tests"
[ "$run_storagemon" -eq 1 ] && plan="$plan services/storage_monitor/tests"
if [ -n "$plan" ]; then
  echo "pre-commit-tests: $source_desc: $n_files changed files, $n_python python ->$plan suite(s)"
else
  echo "pre-commit-tests: $source_desc: $n_files changed files, $n_python python -> no mapped suite"
fi

# An unmapped .py is NEVER silent — name it, so "green" is never mistaken for "covered".
if [ -n "$unmapped" ]; then
  echo "pre-commit-tests: !! changed python with NO test mapping (NOT covered by this gate):"
  printf '%s' "$unmapped"
  echo "pre-commit-tests: !! add a tree mapping in scripts/pre-commit-tests.sh if these deserve a suite."
fi

if [ "$run_python" -eq 0 ] && [ "$run_intelligence" -eq 0 ] && [ "$run_toplevel" -eq 0 ] && [ "$run_fpstorage" -eq 0 ] && [ "$run_timeservice" -eq 0 ] && [ "$run_storagemon" -eq 0 ]; then
  # Legitimate pass: the file set was DETERMINED (non-empty) and holds nothing this gate maps.
  if [ -n "$unmapped" ]; then
    echo "pre-commit-tests: no MAPPED python in $source_desc ($n_files files) -> pass (nothing run; see the unmapped list above)"
  else
    echo "pre-commit-tests: no python changes in $source_desc ($n_files files) -> pass (skipping tests)"
  fi
  exit 0
fi

selftest_mods="$(printf '%s' "$selftest_mods" | sort -u | sed '/^$/d')"

# ---------------------------------------------------------------------------------------------
# HEADLESS BY DEFAULT (2026-08-12). This gate runs the render suite, whose display-guarded tests
# create REAL `SkiaWindow`s when SDL has a real video driver. Because the hook fires inside
# `git commit`, that meant windows opening and closing on the operator's desktop during routine
# commits — indistinguishable, at a glance, from a crash loop, and it cost a human a real scare.
# A test gate must be INVISIBLE while it passes.
#
# ⚠️ DEFAULT, NEVER CLOBBER. A caller who deliberately exported SDL_VIDEODRIVER (debugging a
# renderer against a real window, or a CI image pinning its own driver) is stating a requirement;
# a gate that silently overrode it would be a second, quieter version of the same defect — the
# environment saying one thing and the process doing another.
#
# The decision is ANNOUNCED, with its origin, so the hook's own output proves which branch ran.
# The exported value is inherited by every pytest and selftest child launched below.
if [ -z "${SDL_VIDEODRIVER:-}" ]; then
  export SDL_VIDEODRIVER=dummy
  sdl_origin="defaulted by this gate — no window will open"
else
  export SDL_VIDEODRIVER
  sdl_origin="INHERITED from the caller, not overridden"
fi
echo "pre-commit-tests: headless: SDL_VIDEODRIVER=$SDL_VIDEODRIVER ($sdl_origin)"

fail=0
ran=""
if [ "$DRY_RUN" -eq 1 ]; then
  echo "pre-commit-tests: DRY RUN — selecting suites, executing none"
else
  echo "pre-commit-tests: running FAST relevant suite (bypass: git commit --no-verify)"
fi

# 1. Python suite — the fast core guard (catches the data_sources_supervisor class of regression).
#    Rooted at python/tests, NOT python/tests/render: see "WHY THE PYTHON ARM RUNS python/tests"
#    in the header. Every suite in the tree — render, maker, and the top-level bus/transport
#    tests — is selected by construction, so a NEW suite is covered the day it is written.
if [ "$run_python" -eq 1 ]; then
  echo "  -> python suite (python/tests: render + maker + top-level)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "     (dry-run: not executed)"
  else
    if ! ( cd "$PYDIR" && "$PY" -m pytest tests -q -p no:cacheprovider ) ; then
      echo "  !! PYTHON SUITE FAILED"
      fail=1
    fi
    ran="$ran python/tests"
  fi
fi

# 1b. Intelligence suite — the SECOND first-class python tree (the gap 9CB4EE closed). ~2.5s.
#     Run from REPO_ROOT, not PYDIR: these tests import `intelligence.*` as a package, which only
#     resolves with the repo root on sys.path (from python/ it is ModuleNotFoundError).
if [ "$run_intelligence" -eq 1 ]; then
  echo "  -> intelligence suite (compose_projects/intelligence/)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "     (dry-run: not executed)"
  else
    if ! ( cd "$REPO_ROOT" && "$PY" -m pytest compose_projects/intelligence -q -p no:cacheprovider ) ; then
      echo "  !! INTELLIGENCE SUITE FAILED"
      fail=1
    fi
    ran="$ran intelligence"
  fi
fi

# 1c. Top-level tests/ tree — the repo-root suite (bus, transport, decision_ops, document
#     store, the LTS parse invariants, tests/peer). Mapped 2026-08-11, when the last of the
#     three failures naming it in DELIBERATELY_UNMAPPED was cleared. Rooted at the TREE, so
#     tests/peer and any future subtree are selected by construction -- the same reasoning as
#     the python arm running python/tests rather than python/tests/render.
if [ "$run_toplevel" -eq 1 ]; then
  echo "  -> top-level suite (tests: bus, transport, decision_ops, LTS parse invariants)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "     (dry-run: not executed)"
  else
    if ! ( cd "$REPO_ROOT" && "$PY" -m pytest tests -q -p no:cacheprovider ) ; then
      echo "  !! TOP-LEVEL SUITE FAILED"
      fail=1
    fi
    ran="$ran tests"
  fi
fi

# 1d. fingerprint_storage service suite — its 10 in-process integration tests live BESIDE
#     the service (services/fingerprint_storage/tests), not in the top-level tree, so this
#     arm runs them when the service changes. Rooted at the service's tests dir; the
#     reachability guard (test_precommit_gate_reachability.py) parses THIS invocation to
#     prove the suite is reachable. Runs plain from REPO_ROOT: pytest seats services/ on
#     sys.path (no services/__init__.py) and composeio resolves as in the top-level arm.
if [ "$run_fpstorage" -eq 1 ]; then
  echo "  -> fingerprint_storage suite (services/fingerprint_storage/tests)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "     (dry-run: not executed)"
  else
    if ! ( cd "$REPO_ROOT" && "$PY" -m pytest services/fingerprint_storage/tests -q -p no:cacheprovider ) ; then
      echo "  !! FINGERPRINT_STORAGE SUITE FAILED"
      fail=1
    fi
    ran="$ran services/fingerprint_storage/tests"
  fi
fi

if [ "$run_timeservice" -eq 1 ]; then
  echo "  -> time_service suite (services/time_service/tests)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "     (dry-run: not executed)"
  else
    if ! ( cd "$REPO_ROOT" && "$PY" -m pytest services/time_service/tests -q -p no:cacheprovider ) ; then
      echo "  !! TIME_SERVICE SUITE FAILED"
      fail=1
    fi
    ran="$ran services/time_service/tests"
  fi
fi

if [ "$run_storagemon" -eq 1 ]; then
  echo "  -> storage_monitor suite (services/storage_monitor/tests)"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "     (dry-run: not executed)"
  else
    if ! ( cd "$REPO_ROOT" && "$PY" -m pytest services/storage_monitor/tests -q -p no:cacheprovider ) ; then
      echo "  !! STORAGE_MONITOR SUITE FAILED"
      fail=1
    fi
    ran="$ran services/storage_monitor/tests"
  fi
fi

# 2. Touched-area selftests.
if [ -n "$selftest_mods" ]; then
  while IFS= read -r mod; do
    [ -z "$mod" ] && continue
    echo "  -> selftest: $mod"
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "     (dry-run: not executed)"
      continue
    fi
    if ! ( cd "$PYDIR" && "$PY" -m "$mod" ) ; then
      echo "  !! SELFTEST FAILED: $mod"
      fail=1
    fi
  done <<EOF
$selftest_mods
EOF
fi

echo
if [ "$fail" -ne 0 ]; then
  {
    echo "pre-commit-tests: BLOCKED — a fast test failed. Fix it before committing."
    echo "  If this is genuine WIP you must land anyway:"
    echo "    git commit --no-verify"
    echo "  (or: SKIP_PRECOMMIT_TESTS=1 git commit ...)"
  } >&2
  exit 1
fi
if [ "$DRY_RUN" -eq 1 ]; then
  echo "pre-commit-tests: DRY RUN complete for $source_desc — NOTHING was executed; this is NOT a pass"
  exit 0
fi
# Report ONLY what actually ran, and over WHICH change set. A bare "all fast tests green" is what
# let 9CB4EE read as coverage it never had — the closing line must never claim more than this.
echo "pre-commit-tests: green ->${ran:- (none)} suite(s) run over $source_desc ($n_python python files); allowing commit"
exit 0
