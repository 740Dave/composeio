#!/usr/bin/env bash
# start-colonial-ux.sh — the ONE canonical bringup for the Mac BSG COLONIAL UX render stack:
# the colonial 2-up console (two colonial_console widgets stacked) + the colonial clock.
#
# WHY THIS EXISTS (David 2026-08-19): the Intelligence Console has start-console-stack.sh and the
# rpi5 has kiosk.py, but the Mac BSG colonial was launched BY HAND — detached `nohup` render
# runtimes that had to be reverse-engineered from ps/lsof to restart. That ad-hoc launch was a
# FINDING, not a fixture. This is the de-forked single source of truth ([DX-NARROW-WAIST]).
# The scene-edit -> get-it-live flow is a DIFFERENT job: see the /widget-reload skill.
#
# IDEMPOTENT: each member launches ONLY if not already alive (surface_absent guard, below), so this
# is safe to run on every bounce. --restart stops the matching members first, then relaunches.
#
# ⛔ THE surface_absent GUARD DECLINES ON AN UNANSWERABLE PROBE (mirrors start-console-stack.sh /
# [CXC-IC-SURFACE-SINGLETON]): pgrep exit 1 = affirmatively absent -> launch; exit 0 = alive -> skip;
# ANY OTHER exit means the probe FAILED, and a failed probe must NOT be read as "nothing running"
# (that read is how a duplicate surface once got spawned on top of two live ones). A missed launch
# is recoverable by re-running; a spurious duplicate window is not.
#
# GUI/display: the render windows appear only because this runs as the human's user in the Aqua
# login session. Launched from ssh / a headless context, the processes start but no window shows.
#
# Usage:
#   compose_projects/carousel/start-colonial-ux.sh              # bring up the colonial UX (idempotent)
#   compose_projects/carousel/start-colonial-ux.sh --restart    # stop matching members, then relaunch
#   MQTT=mqtt://mqtt-broker.local:1883 compose_projects/carousel/start-colonial-ux.sh
#   NO_CLOCK=1 compose_projects/carousel/start-colonial-ux.sh   # 2-up only
set -euo pipefail

# Repo root = TWO levels up from THIS script (compose_projects/carousel/ -> repo). Location-relative,
# so it is correct from the canonical checkout OR a worktree.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECTS_ROOT="$REPO_ROOT/compose_projects"
cd "$REPO_ROOT"

MQTT="${MQTT:-mqtt://mqtt-broker.local:1883}"        # informational; scenes DECLARE their own transport
LOG_DIR="${LOG_DIR:-/tmp/composeio-colonial}"
mkdir -p "$LOG_DIR"

export COMPOSEIO_REPO_ROOT="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/python:$PROJECTS_ROOT${PYTHONPATH:+:$PYTHONPATH}"

RESTART=""
[[ "${1:-}" == "--restart" ]] && RESTART=1

# ── the colonial UX roster: name|entry-module|scene (relative to REPO_ROOT) ───────────────────
# The 2-up IS the colonial console (bsg_app stacks cic_top + cic_bottom, each mounting
# std-helm-heading -> engine_thrust). run_compose re-exports engine.runtime's main.
ROSTER=(
  "colonial-2up|composeio.render.run_compose|compose_projects/carousel/bsg_app.compose.yaml"
  "colonial-clock|composeio.render.engine.runtime|python/composeio/render/examples/colonial_clock.compose.yaml"
)
# NOTE the standalone engine_thrust widget window is a DEV PREVIEW — the 2-up already renders
# engine_thrust via the std-helm-heading scene mount, so it is deliberately NOT in the roster.

# surface_absent <pattern> — TRUE only when pgrep AFFIRMATIVELY reports no match (exit 1).
surface_absent() {
  local pattern="$1" rc=0
  pgrep -f "$pattern" >/dev/null 2>&1 || rc=$?
  case "$rc" in
    0) return 1 ;;                                     # alive
    1) return 0 ;;                                     # affirmatively absent -> launch
    *) echo "  ! pgrep could not probe '$pattern' (exit $rc) — NOT launching on an" \
            "unanswerable probe; re-run once the probe works" >&2
       return 1 ;;
  esac
}

echo "colonial UX -> bus $MQTT  repo=$REPO_ROOT  logs=$LOG_DIR${RESTART:+  (RESTART)}"

for row in "${ROSTER[@]}"; do
  IFS='|' read -r name module scene <<< "$row"
  if [[ -n "$RESTART" ]]; then
    if pgrep -f "$scene" >/dev/null 2>&1; then
      echo "  ~ stopping $name ($(pgrep -f "$scene" | tr '\n' ' '))"
      pkill -f "$scene" 2>/dev/null || true
      sleep 1
    fi
  fi
  if surface_absent "$scene"; then
    echo "  + $name"
    nohup python3 -m "$module" "$scene" --transports >"$LOG_DIR/$name.log" 2>&1 &
    disown
  else
    echo "  = $name already up (pid $(pgrep -f "$scene" | head -1))"
  fi
  [[ "$name" == "colonial-clock" && -n "${NO_CLOCK:-}" ]] && continue
done

sleep 2
echo "colonial UX up:"
for row in "${ROSTER[@]}"; do
  IFS='|' read -r name _module scene <<< "$row"
  echo "  $name -> pid $(pgrep -f "$scene" | head -1 || echo '-')  (log $LOG_DIR/$name.log)"
done
echo "prove it: re-run — idempotent. --restart to force a fresh window. A missing window is usually a"
echo "          missing --transports (values dark) or a non-GUI launch context, not a broken widget."
