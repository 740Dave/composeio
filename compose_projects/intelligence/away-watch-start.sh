#!/usr/bin/env bash
# away-watch-start.sh — THE canonical START of an away/night watch run
# ([CXC-GV-SCOPE], David 2026-08-06).
#
# WHY THIS EXISTS
#   A watch run must be able to name, afterwards, exactly which decisions were made
#   in it. That needs TWO things established at START, and neither can be
#   reconstructed later:
#
#     1. an OBSERVATION WINDOW — a snapshot of the decision corpus, so the run can
#        tell its own decisions from everyone else's by DIFF rather than by guess.
#        Miss it and the run is permanently UNSCOPED; there is no honest repair.
#     2. a RUN IDENTITY — a run-grained session slug (`2026-08-06-away-watch-scope`)
#        exported as the ambient session token, so decisions written during the run
#        carry a token unique to THIS run instead of a bare date that every run
#        that night shares.
#
#   Both were previously checklist prose, which is exactly how the preflight gets
#   skipped. There was no away-watch start script at all — the Architect ran
#   `record_away_watch` by hand. This is that missing entry point.
#
#   The two are ONE mechanism: the slug is the run's identity, and the observation
#   window is what later PROVES which decisions actually carried it. The window
#   keeps the run honest (only observed tokens are ever recorded as scope); the
#   slug makes what is observed run-grained rather than day-grained.
#
# WHAT IT DELIBERATELY DOES NOT DO
#   It never writes the slug into the run's `sessions` scope. At start, no decision
#   carries the slug — recording it would be a prediction, and the scope must only
#   ever name tokens that were OBSERVED on real nodes. The slug is stored
#   separately (`away_watch.run_slug`) as a DECLARATION, and the close compares the
#   two and warns if they diverge.
#
# THE PROPAGATION SEAM (read this before debugging a day-grained scope)
#   The token reaches the decision writer through the PROCESS ENVIRONMENT, so it is
#   inherited only by processes started from here — a concurrent, unrelated
#   instance has it unset and is completely unaffected. Two routes:
#     * `--exec transport.sh <session-id>` — the away-watch preflight ALREADY
#       bounces the session; doing it through this script means the fresh Claude
#       Code process (and every MCP server it spawns, including decision-ops)
#       inherits the token. This is the route that makes MCP decision writes
#       run-grained.
#     * sourcing / subshells — `eval "$(away-watch-start.sh --print-env "<label>")"`
#       exports it into the current shell, so subagents and CLI writes launched
#       from there inherit it.
#   VERIFY, do not assume: after a bounce, call the `decision_session_token` MCP
#   tool (it answers from inside the server process); in a shell,
#   `python3 -m composeio.decision_ops session-token`.
#
# Usage:
#   compose_projects/intelligence/away-watch-start.sh "Overnight 2026-08-06"
#   compose_projects/intelligence/away-watch-start.sh "Overnight 2026-08-06" --print-env
#   compose_projects/intelligence/away-watch-start.sh "Overnight 2026-08-06" --exec <session-id>
#   SCENE=... SESSION_ID=... compose_projects/intelligence/away-watch-start.sh "<label>"
#
# IDEMPOTENT: the row upsert, the slug, and the baseline snapshot are all
# re-runnable; re-running before any decision is written is a clean no-op.
set -euo pipefail

# TWO levels up: this script is compose_projects/intelligence/<me>.sh. See start-console-stack.sh.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECTS_ROOT="$REPO_ROOT/compose_projects"
cd "$REPO_ROOT"

LABEL="${1:?usage: away-watch-start.sh \"<watch label>\" [--print-env | --exec <session-id>]}"
shift || true

MODE="report"
RESUME_SESSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --print-env) MODE="print-env"; shift ;;
    --exec)      MODE="exec"; RESUME_SESSION="${2:?--exec needs a session id}"; shift 2 ;;
    *) echo "away-watch-start: unknown argument: $1" >&2; exit 2 ;;
  esac
done

SCENE="${SCENE:-compose_projects/intelligence/console/nightwatch.compose.yaml}"
SESSION_ID="${SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}}"
export PYTHONPATH="${REPO_ROOT}/python:${PROJECTS_ROOT}:${PYTHONPATH:-}"

# 1. The away_watch ROW (idempotent upsert on the UNIQUE label). Created here and
#    not by the operator, so step 2 can never be reached without it.
python3 - "$LABEL" "$SCENE" "$SESSION_ID" <<'PY'
import sys, time
from intelligence.retrospective import db
label, scene, session_id = sys.argv[1], sys.argv[2], sys.argv[3]
db.record_away_watch(label=label, session_id=session_id or None,
                     started_ts=time.time(), scene_path=scene,
                     git_state=db.git_state_summary())
PY

# 2. OPEN the observation window + establish the run slug. HARD-FAIL on refusal:
#    a run that starts without a window is unscopeable forever, so this must never
#    be a warning the operator walks past.
if ! OPEN_JSON="$(python3 -m intelligence.retrospective.watch_sessions --open "$LABEL" --json)"; then
  echo "[away-watch-start] FAILED to open the observation window:" >&2
  echo "$OPEN_JSON" >&2
  echo "[away-watch-start] REFUSING to start: this run would be permanently UNSCOPED." >&2
  exit 1
fi
RUN_SLUG="$(printf '%s' "$OPEN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_slug"])')"
export COMPOSEIO_DECISION_SESSION="$RUN_SLUG"

case "$MODE" in
  print-env)
    # Machine-consumable: `eval "$(... --print-env "<label>")"`
    printf 'export COMPOSEIO_DECISION_SESSION=%q\n' "$RUN_SLUG"
    ;;
  exec)
    echo "[away-watch-start] '${LABEL}' open; run slug ${RUN_SLUG}" >&2
    echo "[away-watch-start] bouncing into session ${RESUME_SESSION}; the new process" >&2
    echo "[away-watch-start] and its MCP servers inherit COMPOSEIO_DECISION_SESSION." >&2
    echo "[away-watch-start] VERIFY after startup with the decision_session_token tool." >&2
    exec "${PROJECTS_ROOT}/intelligence/launcher/transport.sh" "$RESUME_SESSION"
    ;;
  *)
    echo "[away-watch-start] watch '${LABEL}' STARTED"
    echo "[away-watch-start]   observation window: OPEN (decisions are now attributable by diff)"
    echo "[away-watch-start]   run slug          : ${RUN_SLUG}"
    echo
    echo "  To make decision writes run-grained, the token must reach the WRITER:"
    echo "    this shell + subagents : eval \"\$(compose_projects/intelligence/away-watch-start.sh '${LABEL}' --print-env)\""
    echo "    the MCP decision-ops   : compose_projects/intelligence/away-watch-start.sh '${LABEL}' --exec <session-id>"
    echo "  Then VERIFY: decision_session_token (MCP), or"
    echo "    python3 -m composeio.decision_ops session-token"
    echo
    echo "  At the end of the watch:"
    echo "    python3 -m intelligence.retrospective.watch_sessions --close '${LABEL}'"
    ;;
esac
