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
# DETACHED: each member runs in its own session (spawn_detached), so it outlives the bringup and
# whatever launched it. Without that, the LaunchAgent reaps all three as soon as this one-shot exits.
#
# IDEMPOTENT: each of the three legs launches ONLY if its member's host LOCK is free (see
# surface_absent; until 2026-11-26 this was a pgrep guard), so this is safe to run on every bounce
# and safe for a KeepAlive-less LaunchAgent RunAtLoad.
#
# ⛔ THE (former) pgrep GUARD WAS A FAST PATH, NOT THE GUARANTEE ([CXC-IC-SURFACE-SINGLETON],
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
[ -f "$REPO_ROOT/scripts/lib/overlay-env.sh" ] && . "$REPO_ROOT/scripts/lib/overlay-env.sh"  # host overlay, before our defaults ([CXC-SCL-SCRUB-OVERLAY-PARITY])

MQTT="${MQTT:-mqtt://mqtt-broker.local:1883}"
LOG_DIR="${LOG_DIR:-/tmp/composeio-daemons}"
mkdir -p "$LOG_DIR"

export COMPOSEIO_REPO_ROOT="$REPO_ROOT"                 # so in-process sources resolve data_sources.py
export PYTHONPATH="$REPO_ROOT/python:$PROJECTS_ROOT${PYTHONPATH:+:$PYTHONPATH}"

CONSOLE_SCENE="compose_projects/intelligence/console/console.compose.yaml"
RIBBON_SCENE="apps/compose_platform_launch_bar/launch_bar.compose.yaml"
APP_LAUNCHER_MODULE="intelligence.tools.app_launcher"
APP_LAUNCHER_ROLE="app-launcher"     # == app_launcher.SINGLETON_ROLE (pinned by the test)

echo "console stack -> bus $MQTT  repo=$REPO_ROOT  logs=$LOG_DIR"

# member_probe --scene <scene> | --role <role> — ask the member's OWN host lock whether it is up.
# Exit 0 = held (stdout: holder pid), 1 = free, anything else = could not probe.
#
# WHY A LOCK, NOT THE PROCESS TABLE (DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E, 2026-11-26).
# The guards used to infer membership from command-line text (`pgrep -f <token>`), and text
# failed in both directions. (1) A shell that merely MENTIONED a member counted as one: a
# caller's `zsh -c "… pgrep -f intelligence.tools.app_launcher"` made the bringup print "=
# app_launcher already up (pid <that shell>)", and app_launcher was never launched. (2) A
# pattern anchored to stop that missed real members started another way (an absolute scene path,
# `python3 -u -m`, `./scene`, app_launcher run as a script, an interpreter path with a space), and
# a duplicate was started. Every member already holds an authoritative lock: the surface flock
# ([CXC-IC-SURFACE-SINGLETON]) for the console and the ribbon (which declares
# `design.surface_singleton: true`), and the `app-launcher` role singleton for app_launcher.
# `stack_member_probe` asks that lock through the repo's one flock primitive (non-blocking, never
# disturbs a holder) at the same path the member uses, honouring $COMPOSEIO_LOCK_DIR.
member_probe() { python3 -m composeio.stack_member_probe "$@"; }

# surface_absent <probe args…> — TRUE only when the probe AFFIRMATIVELY reports the lock free
# (exit 1). Exit 0 => alive, and the holder pid is left in $MEMBER_PID for the message. ANY OTHER
# exit means the probe could not answer, and an unanswerable probe must NOT be read as "nothing
# is running": that read is what put a third console on top of two ([CXC-IC-SURFACE-SINGLETON]
# clause (2)). We report the doubt out loud and DECLINE to launch: a missed launch is recoverable
# (re-run the bringup), whereas a spurious launch used to be invisible.
# The window this leaves: a member takes its lock a moment after it starts (the console parses its
# scene first). A second bringup inside that window launches a duplicate, which finds the lock held
# and yields (exit 0) without opening a window. The lock is still the guarantee.
MEMBER_PID=""
surface_absent() {
  local rc=0 out
  out="$(member_probe "$@" 2>/dev/null)" || rc=$?
  case "$rc" in
    0) MEMBER_PID="${out:-?}"; return 1 ;;             # alive
    1) return 0 ;;                                     # affirmatively absent -> launch
    *) echo "  ! could not probe the lock for '$*' (exit $rc) — NOT launching on an" \
            "unanswerable probe; re-run the bringup once the probe works" >&2
       return 1 ;;
  esac
}

# A member whose spawn fails (a log it cannot write, or exec failing with 127) must not stop the
# OTHERS. Under `set -e` an unguarded failure aborted the whole bringup (candidate review N7), so
# each spawn records its failure and the script carries on, then exits non-zero naming them.
FAILED_MEMBERS=()
spawn_member() {
  local name="$1"; shift
  local rc=0
  spawn_detached "$@" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    echo "  ! $name failed to start (spawn exit $rc; see its log) — carrying on with the others" >&2
    FAILED_MEMBERS+=("$name")
  fi
}

member_status() { local out; out="$(member_probe "$@" 2>/dev/null)" && echo "${out:-?}" || echo '-'; }

# spawn_detached <log> <cmd...> — launch a stack member in its OWN SESSION (new process group),
# stdin from /dev/null and output to <log>. It returns only AFTER the member has left this
# script's process group.
#
# WHY (DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E, 2026-11-26): this script used `nohup cmd
# &`, and the LaunchAgent comment called that "detached". It is not. nohup ignores SIGHUP and
# nothing else, so every member stayed in THIS script's process group. The LaunchAgent runs this
# script as a one-shot job, and when a launchd job exits, launchd SIGTERMs whatever is left in its
# process group (launchd.plist(5), AbandonProcessGroup). Measured on the live host: the job
# exited 1176 ms after start, the ribbon was gone 0.5 s later and the console 4 s later. SDL turns
# SIGTERM into SDL_QUIT, so the console stopped its sources and exited 0 with no traceback.
# app_launcher took Python's default SIGTERM and died.
# STRUCTURAL, NOT TIMED: the shim runs in the FOREGROUND and ALWAYS forks. The child calls
# setsid() and reports on a pipe; the parent exits only after that report. So when this function
# returns, the member is already out of the group, and nothing depends on the trailing `sleep 1`
# or on how slowly a cold interpreter starts at login. (A backgrounded shim left each member in
# the group for tens of ms; a review measured a group SIGTERM in that window killing the ribbon
# 6 times in 6.) If exec fails, the child reports that too and the shim exits 127.
# macOS ships no setsid(1); python3 is already this stack's interpreter.
spawn_detached() {
  local log="$1"; shift
  python3 -c 'import os, sys
r, w = os.pipe()                      # non-inheritable: the write end closes at a successful exec
if os.fork() == 0:
    os.close(r)
    os.setsid()
    os.write(w, b"S")                 # out of the launcher process group, now
    try:
        os.execvp(sys.argv[1], sys.argv[1:])
    except OSError as exc:
        os.write(w, b"E" + str(exc).encode())
        os._exit(127)
os.close(w)
got = b""
while True:
    chunk = os.read(r, 4096)
    if not chunk:
        break
    got += chunk
if got[:1] != b"S":
    sys.stderr.write("spawn_detached: the child never reported setsid()\n"); os._exit(1)
if got[1:2] == b"E":
    sys.stderr.write("spawn_detached: exec failed: %s\n" % got[2:].decode(errors="replace")); os._exit(127)
os._exit(0)' "$@" >"$log" 2>&1 </dev/null
}

# ── 1. CONSOLE (viewer + in-process data layer) — session-pinned, only if absent ──────────────
if [[ -z "${NO_CONSOLE:-}" ]]; then
  # PIN this session's transcript by its unique id — robust, placeholder-free ([CXC-IC-SELF-IDENTITY]).
  # Only set it if not already set (a launcher-inherited pin is never clobbered). Absent at boot.
  if [[ -z "${COMPOSEIO_SESSION_JSONL:-}" && -n "${CLAUDE_CODE_SESSION_ID:-}" ]]; then
    COMPOSEIO_SESSION_JSONL="$(ls "$HOME"/.claude/projects/*/"${CLAUDE_CODE_SESSION_ID}".jsonl 2>/dev/null | head -1 || true)"
    [[ -n "$COMPOSEIO_SESSION_JSONL" ]] && export COMPOSEIO_SESSION_JSONL
  fi
  if surface_absent --scene "$CONSOLE_SCENE"; then
    if [[ -n "${COMPOSEIO_SESSION_JSONL:-}" && -f "${COMPOSEIO_SESSION_JSONL:-}" ]]; then
      echo "  + console (pinned $COMPOSEIO_SESSION_JSONL)"
    else
      echo "  + console (NO session pin — UX renders, telemetry dark until a session attaches)"
    fi
    spawn_member console "$LOG_DIR/console.log" python3 -m composeio.render.engine.runtime "$CONSOLE_SCENE" --transports
  else
    echo "  = console already up (pid $MEMBER_PID)"
  fi
fi

# ── 2. LAUNCH BAR RIBBON — pure tile scene, no session dependency, only if absent ─────────────
if surface_absent --scene "$RIBBON_SCENE"; then
  echo "  + launch bar ribbon"
  spawn_member ribbon "$LOG_DIR/launch_bar.log" python3 -m composeio.render.engine.runtime "$RIBBON_SCENE" --transports
else
  echo "  = launch bar ribbon already up (pid $MEMBER_PID)"
fi

# ── 3. app_launcher DAEMON — turns a tile's _app/<target>/open intent into a spawn, only if absent ─
if surface_absent --role "$APP_LAUNCHER_ROLE"; then
  echo "  + app_launcher"
  spawn_member app_launcher "$LOG_DIR/app_launcher.log" python3 -m "$APP_LAUNCHER_MODULE" --mqtt "$MQTT"
else
  echo "  = app_launcher already up (pid $MEMBER_PID)"
fi

sleep 1
echo "console stack up (lock holders; '-' = not yet locked): console $(member_status --scene "$CONSOLE_SCENE"), ribbon $(member_status --scene "$RIBBON_SCENE"), app_launcher $(member_status --role "$APP_LAUNCHER_ROLE")"
echo "prove it: re-run this script — it is idempotent. And the guarantee does not rest on that:"
echo "          the SURFACE holds a host flock ([CXC-IC-SURFACE-SINGLETON]), so a console launched"
echo "          by ANY other path yields too. COMPOSEIO_SURFACE_SUPERSEDE=1 to win a bounce."
if [[ "${#FAILED_MEMBERS[@]}" -gt 0 ]]; then
  echo "console stack INCOMPLETE: failed to start: ${FAILED_MEMBERS[*]}" >&2
  exit 1
fi
