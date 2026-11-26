#!/usr/bin/env bash
# start-console-stack.sh — the ONE canonical bringup for the whole Intelligence Console
# STACK: the console GUI + the always-on Launch Bar ribbon + its app_launcher daemon
# ([SPEC:CXC-LAUNCHBAR] [CXC-LB-AUTOSTART]).
#
# WHY THIS EXISTS (DEFECT-LAUNCH-BAR-RIBBON-NOT-RUNNING, David 2026-08-04):
#   The contract "the Launch Bar autostarts WITH the console" (David 2026-07-29) lived ONLY as
#   prose in the bounce-console skill runbook — never in a spec invariant, never in a code launch
#   path. So EVERY launch path that did not manually re-run the skill's ribbon block (the console
#   coming up via `runtime --transports` directly, via console_supervisor.start(), or at boot)
#   brought the console up WITHOUT the ribbon. Observed 2026-08-04: console.compose.yaml alive,
#   `pgrep app_launcher` / `launch_bar` -> nothing. This script is the missing single source of
#   truth: the de-forked ([DX-NARROW-WAIST], reuse-by-reference-never-fork) bringup that the
#   interactive bounce, the boot LaunchAgent, and any operator all reuse — so the ribbon can never
#   again be forgotten by a launch path.
#
# IDEMPOTENT: each of the three legs launches ONLY if not already alive (pgrep guard), so this is
# safe to run on every bounce and safe for a KeepAlive-less LaunchAgent RunAtLoad.
#
# ⛔ THE pgrep GUARD IS A FAST PATH, NOT THE GUARANTEE ([CXC-IC-SURFACE-SINGLETON],
# DEFECT-TWO-CONSOLES-SPAWNED-THEN-KILLED, David 2026-08-15). This comment used to end "Re-running
# it never doubles a live surface." It could not promise that, and the promise is what let the
# duplicate-console defect live:
#   (1) ADVISORY. The guard binds THIS script and nothing else. console_supervisor's start() is
#       guarded only by its own child handle, a hand `runtime --transports` is guarded by nothing,
#       and the bounce runbook relaunches after a kill that SIGTERM did not actually complete.
#   (2) IT CANNOT SAY "I DON'T KNOW". `if ! pgrep …` reads EVERY nonzero exit as "not running" —
#       including exit 2, which means the probe FAILED. Measured: `pgrep -fc …` (`-c` is a Linux
#       flag macOS pgrep rejects) reported nothing while two consoles ran, and a third was
#       launched on top of them. `surface_absent` below closes (2) at this call site.
# The real guarantee is the host flock the SURFACE itself takes before it opens a window
# ([CXC-IC-SURFACE-SINGLETON]): a second console yields whatever this script decides.
#
# SESSION PIN (best-effort): the console's three transcript-dependent in-process sources
# ([DEC-INPROCESS-SOURCES]) only start when the console can resolve WHICH transcript is this
# session's; we derive the pin from CLAUDE_CODE_SESSION_ID (never `ls -t`-guess,
# [CXC-IC-SELF-IDENTITY]). At LOGIN there is no Claude session yet, so the pin is absent and the
# console renders its UX with telemetry dark until a session attaches (via transport.sh) — that is
# expected at boot. The RIBBON + app_launcher have NO transcript dependency, so they always come
# up clean, which is the whole point of the always-on ribbon.
#
# Usage:
#   compose_projects/intelligence/start-console-stack.sh                 # bring up the whole stack (idempotent)
#   MQTT=mqtt://mqtt-broker.local:1883 compose_projects/intelligence/start-console-stack.sh
#   NO_CONSOLE=1 compose_projects/intelligence/start-console-stack.sh    # ribbon + app_launcher only (no console GUI)
set -euo pipefail

# Repo root = TWO levels up from THIS script (compose_projects/intelligence/ -> repo). Derived from
# the script's own location so it is correct whether run from the canonical checkout or a worktree;
# the LaunchAgent references the CANONICAL path directly.
# ⛔ THIS COMMENT ONCE SAID "one level up" AND HAD ALREADY BEEN UPDATED TO NAME compose_projects/,
#    so it argued the stale `..` was correct while $REPO_ROOT silently became <repo>/compose_projects.
#    The two roots are now SEPARATELY NAMED, because every use below needs one or the other and the
#    single conflated name is what let the error hide.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECTS_ROOT="$REPO_ROOT/compose_projects"
cd "$REPO_ROOT"

MQTT="${MQTT:-mqtt://mqtt-broker.local:1883}"
LOG_DIR="${LOG_DIR:-/tmp/composeio-daemons}"
mkdir -p "$LOG_DIR"

export COMPOSEIO_REPO_ROOT="$REPO_ROOT"                 # so in-process sources resolve data_sources.py
export PYTHONPATH="$REPO_ROOT/python:$PROJECTS_ROOT${PYTHONPATH:+:$PYTHONPATH}"

CONSOLE_SCENE="compose_projects/intelligence/console/console.compose.yaml"
RIBBON_SCENE="apps/compose_platform_launch_bar/launch_bar.compose.yaml"
APP_LAUNCHER_MODULE="intelligence.tools.app_launcher"

echo "console stack -> bus $MQTT  repo=$REPO_ROOT  logs=$LOG_DIR"

# surface_absent <pattern> — TRUE only when pgrep AFFIRMATIVELY reports no match (exit 1).
# Exit 0 => alive. ANY OTHER exit (2 = usage/error, and anything a future pgrep invents) means the
# probe could not answer, and an unanswerable probe must NOT be read as "nothing is running" —
# that read is what put a third console on top of two ([CXC-IC-SURFACE-SINGLETON] clause (2)).
# We report the doubt out loud and DECLINE to launch: the surface flock makes a missed launch
# recoverable (re-run the bringup), whereas a spurious launch used to be invisible.
surface_absent() {
  local pattern="$1" rc=0
  pgrep -f "$pattern" >/dev/null 2>&1 || rc=$?
  case "$rc" in
    0) return 1 ;;                                     # alive
    1) return 0 ;;                                     # affirmatively absent -> launch
    *) echo "  ! pgrep could not probe '$pattern' (exit $rc) — NOT launching on an" \
            "unanswerable probe; re-run the bringup once the probe works" >&2
       return 1 ;;
  esac
}

# ── 1. CONSOLE (viewer + in-process data layer) — session-pinned, only if absent ──────────────
if [[ -z "${NO_CONSOLE:-}" ]]; then
  # PIN this session's transcript by its unique id — robust, placeholder-free ([CXC-IC-SELF-IDENTITY]).
  # Only set it if not already set (a launcher-inherited pin is never clobbered). Absent at boot.
  if [[ -z "${COMPOSEIO_SESSION_JSONL:-}" && -n "${CLAUDE_CODE_SESSION_ID:-}" ]]; then
    COMPOSEIO_SESSION_JSONL="$(ls "$HOME"/.claude/projects/*/"${CLAUDE_CODE_SESSION_ID}".jsonl 2>/dev/null | head -1 || true)"
    [[ -n "$COMPOSEIO_SESSION_JSONL" ]] && export COMPOSEIO_SESSION_JSONL
  fi
  if surface_absent "$CONSOLE_SCENE"; then
    if [[ -n "${COMPOSEIO_SESSION_JSONL:-}" && -f "${COMPOSEIO_SESSION_JSONL:-}" ]]; then
      echo "  + console (pinned $COMPOSEIO_SESSION_JSONL)"
    else
      echo "  + console (NO session pin — UX renders, telemetry dark until a session attaches)"
    fi
    nohup python3 -m composeio.render.engine.runtime "$CONSOLE_SCENE" --transports \
          >"$LOG_DIR/console.log" 2>&1 &
  else
    echo "  = console already up (pid $(pgrep -f "$CONSOLE_SCENE" | head -1))"
  fi
fi

# ── 2. LAUNCH BAR RIBBON — pure tile scene, no session dependency, only if absent ─────────────
if surface_absent "$RIBBON_SCENE"; then
  echo "  + launch bar ribbon"
  nohup python3 -m composeio.render.engine.runtime "$RIBBON_SCENE" --transports \
        >"$LOG_DIR/launch_bar.log" 2>&1 &
else
  echo "  = launch bar ribbon already up (pid $(pgrep -f "$RIBBON_SCENE" | head -1))"
fi

# ── 3. app_launcher DAEMON — turns a tile's _app/<target>/open intent into a spawn, only if absent ─
if surface_absent "$APP_LAUNCHER_MODULE"; then
  echo "  + app_launcher"
  nohup python3 -m "$APP_LAUNCHER_MODULE" --mqtt "$MQTT" \
        >"$LOG_DIR/app_launcher.log" 2>&1 &
else
  echo "  = app_launcher already up (pid $(pgrep -f "$APP_LAUNCHER_MODULE" | head -1))"
fi

sleep 1
echo "console stack up: console $(pgrep -f "$CONSOLE_SCENE" | head -1 || echo '-'), ribbon $(pgrep -f "$RIBBON_SCENE" | head -1 || echo '-'), app_launcher $(pgrep -f "$APP_LAUNCHER_MODULE" | head -1 || echo '-')"
echo "prove it: re-run this script — it is idempotent. And the guarantee does not rest on that:"
echo "          the SURFACE holds a host flock ([CXC-IC-SURFACE-SINGLETON]), so a console launched"
echo "          by ANY other path yields too. COMPOSEIO_SURFACE_SUPERSEDE=1 to win a bounce."
