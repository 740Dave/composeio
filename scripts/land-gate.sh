#!/usr/bin/env bash
# =============================================================================
#  scripts/land-gate.sh — worktrunk pre-merge QA gate
# =============================================================================
#  Wired as the first [[pre-merge]] hook in .config/wt.toml. wt runs it AFTER
#  rebasing the branch onto live main and BEFORE the fast-forward merge, in the
#  rebased worktree (cwd = worktree root). A NON-ZERO exit ABORTS the land —
#  nothing reaches main.
#
#  This makes "QA verified" a STRUCTURAL precondition of "landed": the QA
#  subagent must COMMIT its verdict to `.qa/verdict.json` on the branch. Chat
#  text evaporates; a committed file lands-or-not with the code it judges.
#
#  Verdict schema (minimum):   {"pass": true|false, "spec": "<id>", ...}
#  The gate passes IFF `pass === true`. A missing file, unparseable JSON, or
#  `pass != true` all ABORT the land.
#
#  ⚠ EPISTEMIC HYGIENE — [CXC-VISION-EPISTEMICS] rule-negative-control:
#    a pipeline's exit status is its LAST command's, so
#      `check | tail` ; if $? ...   reports success when `check` failed.
#    This gate captures the verdict-reader's OWN exit status (never a pipe tail)
#    and branches on that. An instrument that can only report success is not an
#    instrument — so this one refuses loudly on a bad/absent verdict.
# -----------------------------------------------------------------------------
set -u

VERDICT="${1:-.qa/verdict.json}"

if [ ! -f "$VERDICT" ]; then
  echo "land-gate: ABORT — no QA verdict at '$VERDICT' (cwd: $(pwd))." >&2
  echo "land-gate: a build is landable ONLY with a committed, passing verdict." >&2
  exit 1
fi

# The verdict logic lives in python (robust JSON) invoked as its OWN process, so
# its exit status IS the status we branch on — not a pipe tail.
python3 - "$VERDICT" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path) as fh:
        verdict = json.load(fh)
except Exception as exc:
    print(f"land-gate: ABORT — verdict '{path}' is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(1)
if verdict.get("pass") is not True:
    print(
        f"land-gate: ABORT — QA verdict pass={verdict.get('pass')!r} "
        f"(need exactly true). spec={verdict.get('spec', '<unspecified>')}. "
        f"Not landing.",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"land-gate: OK — QA verdict pass=true (spec={verdict.get('spec', '<unspecified>')}).")
sys.exit(0)
PY
status=$?

# Propagate the reader's OWN status. Non-zero here => wt aborts the merge.
if [ "$status" -ne 0 ]; then
  exit "$status"
fi
exit 0
