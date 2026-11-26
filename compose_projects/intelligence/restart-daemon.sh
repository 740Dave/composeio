#!/usr/bin/env bash
# restart-daemon.sh — SAFE, dedup-aware restart of ONE console data-layer daemon.
#
# WHY THIS EXISTS (2026-07-22 postmortem): daemons were hand-restarted with ad-hoc
# `pkill` + `nohup` + fragile arg-extraction. Two failures resulted:
#   (a) BAD ARGS  — a hand-typed launch line left transcript_publisher + deliverable_monitor
#       dead for hours (wrong/missing flags → they crashed on start, unnoticed).
#   (b) DUPLICATES — a relaunch without a clean stop left two copies fighting on the bus.
# This tool makes "restart transcript_publisher" ONE safe command that can never do either.
#
# It NEVER hard-codes a daemon's args. It reads the CANONICAL launch line straight from
# `start-console-daemons.sh` (the single source of truth) via that script's DAEMON_CAPTURE
# introspection hook, so args can never drift from bringup. It then:
#   1. cleanly stops every running instance of JUST that daemon and VERIFIES the kill,
#   2. relaunches it with the canonical argv to the canonical logfile,
#   3. verifies it came up (process alive, not immediately exited, no fatal line in its log),
#   4. REFUSES to leave a duplicate (aborts if >1 instance is ever seen).
#
# Usage:
#   compose_projects/intelligence/restart-daemon.sh <daemon-name>     # safe restart (revives a dead one too)
#   compose_projects/intelligence/restart-daemon.sh --status          # roster: count + health of every daemon
#   compose_projects/intelligence/restart-daemon.sh --list            # just the canonical daemon names
#
# Env passthrough (same knobs as start-console-daemons.sh): JSONL, MQTT, ID, LEDGER, SESSION,
# LOG_DIR. For a RESTART, if JSONL is unset it is auto-pinned to the session the running fleet
# is already instrumenting (prevents the `ls -t` wrong-session bug on a bare restart).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CANONICAL="$SCRIPT_DIR/start-console-daemons.sh"
GRACE_SECS="${RESTART_GRACE_SECS:-5}"   # TERM grace before escalating to KILL
UP_SECS="${RESTART_UP_SECS:-2}"         # settle time before the liveness check

die() { echo "restart-daemon: $*" >&2; exit 1; }

[[ -f "$CANONICAL" ]] || die "canonical bringup script not found: $CANONICAL"

# --- pin JSONL to the running fleet's session on a bare restart (avoids ls -t picking wrong one) -
# Look at any live transcript-instrumented daemon and reuse its --jsonl. Only when caller left it
# unset; an explicit JSONL= always wins.
autopin_jsonl() {
  [[ -n "${JSONL:-}" ]] && return 0
  local line found
  # -ww = full, untruncated argv
  while IFS= read -r line; do
    if [[ "$line" == *"--jsonl "* ]]; then
      found="${line#*--jsonl }"; found="${found%% *}"
      if [[ -f "$found" ]]; then export JSONL="$found"; return 0; fi
    fi
  done < <(ps -ww -o args= 2>/dev/null | grep -E 'compose_projects/intelligence/(publisher|tools)/[a-z_]+\.py' | grep -v grep)
  return 0
}

# --- load the canonical roster from the source of truth (DAEMON_CAPTURE mode) ----------------
# Emits: name<TAB>logfile<TAB>quoted-argv  (one line per daemon). Fails loudly if the canonical
# script cannot resolve its env (e.g. no session .jsonl) — the SAME precondition real bringup has.
load_specs() {
  local out err rc
  err="$(mktemp)"
  out="$(cd "$REPO_ROOT" && DAEMON_CAPTURE=1 bash "$CANONICAL" 2>"$err")" || rc=$?
  rc="${rc:-0}"
  local specs
  specs="$(printf '%s\n' "$out" | grep '^DAEMON_SPEC	' | sed 's/^DAEMON_SPEC	//')" || true
  if [[ -z "$specs" ]]; then
    echo "restart-daemon: could not read canonical daemon specs from $CANONICAL" >&2
    echo "  (bringup precondition failed — is a session .jsonl available? set JSONL=...)" >&2
    sed 's/^/  | /' "$err" >&2
    rm -f "$err"; exit 1
  fi
  rm -f "$err"
  printf '%s\n' "$specs"
}

# spec_field <specs> <name> <1=logfile|2=argv>  -> prints the field, empty if name absent
spec_line_for() { printf '%s\n' "$1" | awk -F'\t' -v n="$2" '$1==n{print; exit}'; }

# pids matching a daemon's unique script-path pattern (mirrors stop-console-daemons.sh's pkill -f).
# Always succeeds: pgrep exits 1 on no-match, which under `set -e -o pipefail` would otherwise abort.
pids_for() { pgrep -f -- "$1" 2>/dev/null | tr '\n' ' ' || true; }

names_from_specs() { printf '%s\n' "$1" | awk -F'\t' '{print $1}'; }

# ------------------------------------ --status --------------------------------------------------
cmd_status() {
  local specs; specs="$(load_specs)"
  local dup=0 dead=0 name logfile argv pattern pids count flag
  printf '%-24s %5s  %-9s %s\n' "DAEMON" "COUNT" "STATUS" "PIDS"
  printf '%-24s %5s  %-9s %s\n' "------" "-----" "------" "----"
  while IFS=$'\t' read -r name logfile argv; do
    [[ -z "$name" ]] && continue
    eval "set -- $argv"; pattern="$1"
    pids="$(pids_for "$pattern")"; pids="${pids% }"
    count=$(printf '%s' "$pids" | wc -w | tr -d ' ')
    if   [[ "$count" -eq 0 ]]; then flag="DEAD";      dead=$((dead+1))
    elif [[ "$count" -gt 1 ]]; then flag="DUPLICATE"; dup=$((dup+1))
    else                            flag="ok"; fi
    printf '%-24s %5s  %-9s %s\n' "$name" "$count" "$flag" "$pids"
  done < <(printf '%s\n' "$specs")
  echo
  if [[ "$dup" -gt 0 || "$dead" -gt 0 ]]; then
    echo "FLAGGED: $dead dead, $dup duplicated. Fix with: restart-daemon.sh <name>"
    return 1
  fi
  echo "all daemons healthy (exactly one instance each)."
}

# ------------------------------------ restart <name> --------------------------------------------
cmd_restart() {
  local target="$1"
  autopin_jsonl
  local specs; specs="$(load_specs)"
  local line; line="$(spec_line_for "$specs" "$target")"
  if [[ -z "$line" ]]; then
    echo "restart-daemon: unknown daemon '$target'. Known daemons:" >&2
    names_from_specs "$specs" | sed 's/^/  - /' >&2
    exit 1
  fi
  local logfile argv pattern
  logfile="$(printf '%s' "$line" | cut -f2)"
  argv="$(printf '%s' "$line" | cut -f3-)"
  eval "set -- $argv"; pattern="$1"

  echo "restart-daemon: $target"
  echo "  pattern : $pattern"
  echo "  logfile : $logfile"
  [[ -n "${JSONL:-}" ]] && echo "  session : $JSONL"

  # 1. STOP every existing instance, verifying the kill (TERM -> grace -> KILL). -----------------
  local pids; pids="$(pids_for "$pattern")"; pids="${pids% }"
  if [[ -n "$pids" ]]; then
    echo "  stopping existing: $pids"
    kill -TERM $pids 2>/dev/null || true
    local waited=0
    while [[ -n "$(pids_for "$pattern")" && "$waited" -lt "$GRACE_SECS" ]]; do
      sleep 1; waited=$((waited+1))
    done
    local left; left="$(pids_for "$pattern")"; left="${left% }"
    if [[ -n "$left" ]]; then
      echo "  escalating to KILL: $left"
      kill -KILL $left 2>/dev/null || true
      sleep 1
    fi
    left="$(pids_for "$pattern")"; left="${left% }"
    [[ -n "$left" ]] && die "FAILED to stop $target (still alive: $left) — refusing to launch a duplicate"
    echo "  stopped (verified 0 running)."
  else
    echo "  none running (will start fresh)."
  fi

  # 2. LAUNCH with the canonical argv to the canonical log (truncates -> log holds only this run). -
  echo "  launching canonical: python3 $argv"
  ( cd "$REPO_ROOT" && eval "nohup python3 $argv >\"$logfile\" 2>&1 &" )

  # 3. VERIFY it came up: settle, then require EXACTLY ONE live instance, not immediately exited,
  #    and no fatal marker in the fresh log. -----------------------------------------------------
  sleep "$UP_SECS"
  local now; now="$(pids_for "$pattern")"; now="${now% }"
  local count; count=$(printf '%s' "$now" | wc -w | tr -d ' ')
  if [[ "$count" -eq 0 ]]; then
    echo "  !! $target did not stay up. Last log lines:" >&2
    tail -n 15 "$logfile" 2>/dev/null | sed 's/^/    /' >&2
    die "launch verification FAILED (process exited immediately)"
  fi
  if [[ "$count" -gt 1 ]]; then
    die "DUPLICATE detected after launch ($now) — a concurrent start raced us; investigate, do not leave as-is"
  fi
  if tail -n 40 "$logfile" 2>/dev/null | grep -qE 'Traceback \(most recent call last\)|ModuleNotFoundError|^usage:|error: (unrecognized|argument)'; then
    echo "  !! fatal marker in $target log despite a live pid ($now). Last log lines:" >&2
    tail -n 15 "$logfile" 2>/dev/null | sed 's/^/    /' >&2
    die "launch verification FAILED (fatal line in log)"
  fi
  echo "  OK — $target up as pid $now (single instance)."
  echo "  tail: tail -f $logfile"
}

# ------------------------------------ dispatch --------------------------------------------------
case "${1:-}" in
  --status|-s) cmd_status ;;
  --list|-l)   load_specs | awk -F'\t' '{print $1}' ;;
  ""|--help|-h)
    sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  --*) die "unknown option '$1' (try --status, --list, or a daemon name)" ;;
  *)   cmd_restart "$1" ;;
esac
