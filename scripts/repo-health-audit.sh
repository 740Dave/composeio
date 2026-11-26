#!/usr/bin/env bash
# repo-health-audit.sh — the STANDING safety-net the strategy doc named but never
# stood up (§ Phase 3 step 13: "Keep the daily launchd prune + wake/dusk audit as
# the standing safety net"). Runs the branch-hygiene audit NON-INTERACTIVELY,
# writes a dated report, and emits a bus alert when it finds drift.
#
# WHY a scheduled run when wake/dusk already gate: wake/dusk fire only when an agent
# is present to cross a session boundary. Unlanded work and dirty worktrees
# accumulate on wall-clock time, not session time — a weekend of parallel fanout
# with no dusk crossing is exactly when the pile re-forms unseen. This runs on the
# clock so drift is surfaced even when no one is looking.
#
# It reuses the SAME instrument the gates use (scripts/branch_hygiene.py audit), so
# there is one oracle, no second implementation to drift. The audit now includes the
# dirty-worktree pass, so a scheduled run also catches uncommitted UNIQUE work before
# a prune can destroy it — AND the orphaned-origin-ref sweep (2026-09-02), which lists
# LIVE `git ls-remote --heads origin` and flags every head whose content is already on
# origin/main as a MEDIUM prunable orphan (the land dance's durability-floor push left
# it behind). Report-only: orphans do NOT flip the exit code (deletion is a human/
# git-expert action), but they appear in the report and in the `orphan-origin=N` field
# of the totals line the bus-alert payload already carries.
#
# ⛔ CANONICAL CHECKOUT ONLY. The launchd plist points at the PRIMARY checkout's copy
# of this script; never a worktree. The script derives the repo root from its own
# location so a mis-pointed agent cannot audit the wrong tree silently.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || { echo "repo-health-audit: cannot cd to repo root $REPO_ROOT" >&2; exit 2; }
[ -f "$REPO_ROOT/scripts/lib/overlay-env.sh" ] && . "$REPO_ROOT/scripts/lib/overlay-env.sh"  # host overlay, before our defaults ([CXC-SCL-SCRUB-OVERLAY-PARITY])

# wt may live under ~/.cargo/bin, off a launchd agent's minimal PATH.
export PATH="$PATH:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin"

BASE="${1:-origin/main}"

# Stable, discoverable output locations (created if absent).
LOG_DIR="$HOME/Library/Logs/composeio-repo-health"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y-%m-%dT%H%M%S)"
REPORT="$LOG_DIR/audit-$STAMP.txt"
LATEST="$LOG_DIR/latest.txt"

# Best-effort fetch so the ancestor oracle judges against a current origin/main.
git fetch --quiet origin 2>/dev/null || true

{
  echo "== repo-health-audit :: $(date -u '+%Y-%m-%dT%H:%M:%SZ') :: root=$REPO_ROOT base=$BASE =="
  echo
  python3 "$SCRIPT_DIR/branch_hygiene.py" audit "$BASE"
} >"$REPORT" 2>&1
RC=$?

# Orphaned-origin-ref highlight — report-only, so it never flips $RC, but a scheduled
# reader scanning latest.txt on a green run must still SEE prunable origin cruft. The
# audit above already ran the sweep; this pulls its count forward as a MEDIUM banner.
ORPHAN_TOTAL="$(grep -m1 '^== totals:' "$REPORT" 2>/dev/null | sed -n 's/.*orphan-origin=\([0-9][0-9]*\).*/\1/p')"
if [ -n "${ORPHAN_TOTAL:-}" ] && [ "$ORPHAN_TOTAL" -gt 0 ] 2>/dev/null; then
  {
    echo
    echo ">> MEDIUM: $ORPHAN_TOTAL orphaned origin ref(s) hold landed content — prunable,"
    echo ">>         report-only (deletion is a human/git-expert action). See the"
    echo ">>         ORPHANED-ORIGIN-REFS section above; prune with git push origin --delete <branch>."
  } >>"$REPORT"
fi

cp "$REPORT" "$LATEST" 2>/dev/null || true

# --- Bus alert on findings (non-zero audit) — best-effort, never fails the run ----
# Config-not-hardcoded: broker host from env, defaulting to the live master. Publish
# a compact JSON alert to a stable topic; a subscriber (console/launch-bar) surfaces it.
if [ "$RC" -ne 0 ]; then
  BROKER_HOST="${COMPOSEIO_MQTT_BROKER:-mqtt-broker.local}"
  BROKER_PORT="${COMPOSEIO_MQTT_PORT:-1883}"
  TOPIC="composeio/repo-health/alert"
  # Summarise: pull the totals + any FAIL lines for the payload.
  TOTALS="$(grep -m1 '^== totals:' "$REPORT" 2>/dev/null | sed 's/"/'"'"'/g')"
  PAYLOAD="$(printf '{"severity":"warning","source":"repo-health-audit","host":"%s","stamp":"%s","report":"%s","summary":"%s","exit":%d}' \
    "$(hostname -s 2>/dev/null || echo unknown)" "$STAMP" "$REPORT" "${TOTALS:-drift detected}" "$RC")"
  if command -v mosquitto_pub >/dev/null 2>&1; then
    if mosquitto_pub -h "$BROKER_HOST" -p "$BROKER_PORT" -t "$TOPIC" -m "$PAYLOAD" -q 1 2>>"$LATEST"; then
      echo "repo-health-audit: bus alert published to $BROKER_HOST:$BROKER_PORT $TOPIC" >>"$LATEST"
    else
      echo "repo-health-audit: WARN bus alert publish failed (broker $BROKER_HOST:$BROKER_PORT unreachable?) — report still on disk at $REPORT" >>"$LATEST"
    fi
  else
    echo "repo-health-audit: WARN mosquitto_pub not found — no bus alert; report on disk at $REPORT" >>"$LATEST"
  fi
fi

# Echo the report to stdout too (launchd captures it to the plist's StandardOutPath).
cat "$REPORT"
exit "$RC"
