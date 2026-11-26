#!/usr/bin/env bash
# start-console-daemons.sh — HEADLESS-ONLY FALLBACK for the console's data-layer daemons.
#
# ⚠ AS OF 2026-07-22 THE CONSOLE RUNS THESE IN-PROCESS. A single console launch
#   (`python -m composeio.render.engine.runtime <scene> --transports`) now brings the whole data
#   layer up on DAEMON THREADS the console owns, pinned to ITS session — see
#   `compose_projects/intelligence/console/data_sources.py` ([DEC-INPROCESS-SOURCES]). That fixes the two bugs this
#   script caused: `ls -t | head -1` grabbing a CONCURRENT session's transcript, and
#   `project_validator` being forgotten (empty Projects tab). Pin the console's session with
#   `COMPOSEIO_SESSION_JSONL=<session.jsonl>` (or `JSONL=...`); disable in-process with
#   `COMPOSEIO_INPROCESS_SOURCES=0` to fall back to THIS script.
#
# USE THIS SCRIPT ONLY for a HEADLESS monitor with NO console GUI (the data layer on the bus with
# nothing rendering it), or when in-process sources are disabled. It shares the in-process path's
# other fragilities (no dedupe, forgot-to-start) — the console path is the supported one — but the
# ls -t WRONG-SESSION guess is CLOSED here ([CXC-IC-SELF-IDENTITY]): it honors the passed
# COMPOSEIO_SESSION_JSONL pin and REFUSES to guess when >1 unpinned .jsonl coexist, rather than
# newest-wins onto a concurrent instance's transcript.
#
# The console (`compose_projects/intelligence/console/console.compose.yaml`, run with --transports) is a VIEWER;
# these daemons are what put live state on the bus for it to render. This script is the canonical,
# self-describing bringup — the operational knowledge that otherwise lives only in a shell history.
#
# Idempotent-ish: it does NOT dedupe existing processes; run `stop-console-daemons.sh` (or pkill the
# publisher modules) first if re-launching. Each daemon is resilient (a bad read never kills it) and
# logs to stderr -> the per-daemon logfile under $LOG_DIR.
#
# Usage:
#   compose_projects/intelligence/start-console-daemons.sh                 # uses the current session + defaults
#   JSONL=/path/to/session.jsonl compose_projects/intelligence/start-console-daemons.sh
#   MQTT=mqtt://mqtt-broker.local:1883 ID=architect compose_projects/intelligence/start-console-daemons.sh
set -euo pipefail

# TWO levels up: this script is compose_projects/intelligence/<me>.sh. One `..` stops at
# compose_projects/, and $REPO_ROOT is handed to --repo-root and --claude-dir below, both of
# which mean the REPO root. PROJECTS_ROOT is the separate name for the other meaning.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECTS_ROOT="$REPO_ROOT/compose_projects"
cd "$REPO_ROOT"

ID="${ID:-architect}"
# WHICH INTELLIGENCES THIS CONSOLE HOSTS ([CXC-FC-UNIT], [CXC-FC-S1-BRIDGES], 2026-08-12).
# NOT a second source of truth: this shells out to data_sources.scene_instances(), which reads the
# console scene's OWN `instances:` block — the same declared list the widgets derive from. It
# steers the channel bridges ONLY; every telemetry daemon below stays on $ID (the HOST whose
# transcript they tail). $CHAT_ID overrides for a deliberate single-instance headless run; an
# unreadable scene falls back to $ID, i.e. the pre-parameterisation wiring.
#
# ⚠ [CXC-FC-VAR-SCOPE]'s CONSTRAINT, and why this line changed: this used to read
# `variables: {chat_id}` out of the same file. Moving the widgets to the declared instance list
# and leaving this reader behind would have kept the HEADLESS path on a one-Intelligence view of
# a fleet scene — a console showing two panes with one bridge running, which looks identical to a
# working console until the second Intelligence's messages silently go nowhere.
INSTANCE_IDS="${CHAT_ID:-$(python3 -c 'import sys;sys.path.insert(0,"'"$PROJECTS_ROOT"'/intelligence/console");import data_sources as d;print(" ".join(e["id"] for e in d.scene_instances()))' 2>/dev/null || true)}"
INSTANCE_IDS="${INSTANCE_IDS:-$ID}"
# The FIRST declared instance, for the messages + any single-id consumer.
CHAT_ID="${INSTANCE_IDS%% *}"
MQTT="${MQTT:-mqtt://mqtt-broker.local:1883}"
# ⛔ NOT A PATH LITERAL. The corpora left the repo 2026-08-17, so the old in-repo default named a
#    file that exists nowhere. data_sources.py already reads this from bootstrap_paths and calls
#    that "the ONE source of truth"; this sibling bring-up hardcoded it and drifted. Same source.
LEDGER="${LEDGER:-$(PYTHONPATH="$REPO_ROOT/python" python3 -c 'from composeio.bootstrap_paths import DELIVERABLE_LEDGER_REL as r; print(r)' 2>/dev/null)}"
SESSION="${SESSION:-$(date +%Y-%m-%d)}"
LOG_DIR="${LOG_DIR:-/tmp/composeio-daemons}"
mkdir -p "$LOG_DIR"

# ── ONE SESSION OWNER: refuse to co-run beside an in-process console ([CXC-IC-SELF-IDENTITY]) ──
# As of [DEC-INPROCESS-SOURCES] a `runtime … --transports` console OWNS the whole data layer on
# daemon threads it runs IN-PROCESS, pinned to ITS session. Launching THIS standalone set beside it
# is the twin-fork of [DEC-DOUBLING-IS-TRANSCRIPT-FORK]: the per-<id> singleton flock stops the
# doubling, but whichever publisher wins the lock FIRST serves the bus — and if this standalone set
# wins with an ls -t transcript guess it pins a CONCURRENT instance's .jsonl and leaks its
# context/thread into the console. So REFUSE + complain LOUDLY (David's remembered plan) rather than
# rely on the silent flock-yield. FORCE=1 overrides for a deliberate headless-only run.
# DAEMON_CAPTURE mode is INTROSPECTION only (restart-daemon.sh reads the canonical daemon roster
# from here without launching anything), so the co-run guard does NOT apply there — skip it.
if [[ -z "${FORCE:-}" && -z "${DAEMON_CAPTURE:-}" ]] \
   && pgrep -f "render\.engine\.runtime.*--transports" >/dev/null 2>&1; then
  echo "!! an in-process console (runtime --transports) is already up and OWNS this session's" >&2
  echo "!! data layer in-process ([DEC-INPROCESS-SOURCES]). Running these standalone daemons TOO" >&2
  echo "!! is the twin-fork that leaked a CONCURRENT instance's transcript (2026-07-24). REFUSING." >&2
  echo "!! This script is the HEADLESS-ONLY fallback — use it only with NO console GUI up." >&2
  echo "!! (Deliberate headless run anyway? re-invoke with FORCE=1.)" >&2
  exit 1
fi

# The session transcript to instrument — PASSED, never guessed ([CXC-IC-SELF-IDENTITY]).
# A pin wins: COMPOSEIO_SESSION_JSONL (what the launcher/host export) or the legacy JSONL. Only if
# NEITHER is set do we look at the project dir — and then we REFUSE to guess when the choice is
# ambiguous. `ls -t | head -1` newest-wins is exactly the cross-instance transcript leak this closes:
# two intelligences launched from a home-ish cwd drop their .jsonl in the SAME shared project dir, so
# newest-wins ping-pongs to whichever wrote most recently. LOUD REFUSAL over PLAUSIBLE WRONG.
if [[ -z "${JSONL:-}" && -n "${COMPOSEIO_SESSION_JSONL:-}" ]]; then
  JSONL="$COMPOSEIO_SESSION_JSONL"
fi
if [[ -z "${JSONL:-}" ]]; then
  PROJ_DIR="${COMPOSEIO_CLAUDE_PROJECT_DIR:-$HOME/.claude/projects/${HOME//\//-}}"   # HOME with "/" -> "-"
  shopt -s nullglob
  candidates=("$PROJ_DIR"/*.jsonl)
  shopt -u nullglob
  if [[ ${#candidates[@]} -eq 1 ]]; then
    JSONL="${candidates[0]}"          # unambiguous single session — safe to use
  elif [[ ${#candidates[@]} -gt 1 ]]; then
    echo "!! ${#candidates[@]} session .jsonl files coexist in $PROJ_DIR and NONE is pinned." >&2
    echo "!! REFUSING to guess (an ls -t newest-wins pick can instrument a CONCURRENT instance's" >&2
    echo "!! transcript — the cross-instance context/thread leak). Pin THIS session explicitly:" >&2
    echo "!!   COMPOSEIO_SESSION_JSONL=<this-session>.jsonl $0" >&2
    exit 1
  fi
fi
if [[ -z "${JSONL:-}" || ! -f "$JSONL" ]]; then
  echo "!! no session .jsonl found (set COMPOSEIO_SESSION_JSONL=... or JSONL=...); the transcript-based daemons need it" >&2
  exit 1
fi

echo "console daemons -> bus $MQTT  id=$ID  chat_id=$CHAT_ID"
echo "  transcript: $JSONL"
echo "  ledger:     $LEDGER"
echo "  logs:       $LOG_DIR"

launch() {  # launch <name> <module-or-script> <args...>
  local name="$1"; shift
  # Introspection hook — ADDITIVE and INERT unless DAEMON_CAPTURE is set (normal bringup is
  # unchanged). When set, each launch EMITS its canonical spec instead of starting the daemon,
  # so restart-daemon.sh reads the single source of truth for argv + logfile straight from this
  # file rather than re-parsing it (no arg drift, ever). Line format (tab-separated):
  #   DAEMON_SPEC <TAB> name <TAB> logfile <TAB> %q-quoted argv...
  if [[ -n "${DAEMON_CAPTURE:-}" ]]; then
    printf 'DAEMON_SPEC\t%s\t%s\t' "$name" "$LOG_DIR/$name.log"
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  echo "  + $name"
  nohup python3 "$@" >"$LOG_DIR/$name.log" 2>&1 &
}

# 1. self_publisher       — self/<id>/* telemetry (context gauge, ledger, registered checklist).
#    [CXC-IH-5A] --namespace-by-id scopes every reading to self/<id>/* so two intelligences on the
#    shared broker never MERGE; --id carries the identity the console's scene binds.
launch self_publisher      compose_projects/intelligence/publisher/self_publisher.py --jsonl "$JSONL" --id "$ID" --namespace-by-id
# 2. transcript_publisher  — thread/<id>/narration, self/<id>/activity, self/<id>/subagents, avatar.
#    [CXC-IH-5A] same carried-identity scoping (families self+thread) as self_publisher.
launch transcript_publisher compose_projects/intelligence/publisher/transcript_publisher.py --jsonl "$JSONL" --id "$ID" --namespace-by-id
# 3. channel_bridge        — relays compose_projects/intelligence/<id>/{in,out} <-> that pane's composer + thread.
#    [CXC-FC-S1-BRIDGES] ONE PER DECLARED INSTANCE, not one for the console: the bridges serve the
#    chat SURFACES, each of which may be pointed at a hosted child while the telemetry above stays
#    on the host.  Each flock is keyed channel-bridge-<id>, so distinct ids provably cannot
#    contend — the mechanism was always right, it was simply never instantiated more than once.
#    A single-id list reproduces the previous single launch byte for byte.
for _iid in $INSTANCE_IDS; do
  launch "channel_bridge-$_iid" intelligence/publisher/channel_bridge.py --id "$_iid"
done
# 3b. fleet_roster         — THE one subscriber to compose_projects/intelligence/+/channel/alive -> fleet/roster.
#    [CXC-FC-S3-ROSTER] one computation, one topic, N readers.  Per-pane discovery would be N
#    implementations of one liveness rule, guaranteed to disagree under load.
launch fleet_roster        compose_projects/intelligence/publisher/fleet_roster.py --mqtt "$MQTT" \
       $(for _iid in $INSTANCE_IDS; do printf -- '--instance %s ' "$_iid"; done)
# 4. deliverable_publisher — projects the ledger to self/deliverables (the compact card + Artifacts).
launch deliverable_publisher compose_projects/intelligence/publisher/deliverable_publisher.py \
       --ledger "$LEDGER" --mqtt "$MQTT" --session "$SESSION"
# 5. deliverable_monitor   — live: tails the transcript for doc writes -> deliverable/new alert.
launch deliverable_monitor compose_projects/intelligence/publisher/deliverable_monitor.py \
       --jsonl "$JSONL" --ledger "$LEDGER" --repo-root "$REPO_ROOT" --mqtt "$MQTT" --session "$SESSION"
# 6. deliverable_opener    — the Open actuation: _app/deliverable/open -> open the file + stamp seen.
launch deliverable_opener  compose_projects/intelligence/publisher/deliverable_opener.py \
       --mqtt "$MQTT" --repo-root "$REPO_ROOT"
# 7. deliverable_watcher   — FILESYSTEM poll: catches EXTERNAL edits (David's own,
#    which the transcript-tailing monitor is blind to) -> deliverable/modified alert.
launch deliverable_watcher compose_projects/intelligence/publisher/deliverable_watcher.py \
       --ledger "$LEDGER" --repo-root "$REPO_ROOT" --mqtt "$MQTT" --interval 3
# 8. memory_monitor        — [DEC-MEMORY-BLOAT-MONITOR] deterministic, READ-ONLY stat of the
#    automemory dir -> self/memory (bytes/files/entries/pct/status) for the Intelligence card's
#    memory gauge. Defaults to $MEMORY_MONITOR_DIR or the built-in automemory path; ADDED
#    additively — does not touch daemons 1-7 above.
launch memory_monitor      compose_projects/intelligence/publisher/memory_monitor.py --mqtt "$MQTT"
# 9. away_watch_publisher  — projects the retrospective `away_watch` table to
#    self/away_watches (the Collaboration tab's Away-Watch history browser) AND
#    consumes _app/away_watch/open to relaunch that watch's Night Watch window.
#    Seeds the 2026-07-22 overnight entry on start (idempotent). ADDED
#    additively — does not touch daemons 1-8 above.
launch away_watch_publisher compose_projects/intelligence/tools/away_watch_publisher.py --mqtt "$MQTT"
# 10. project_validator  — validates the projects.lts registry + publishes self/projects
#     (the Projects tab's data source). Also compose_projects/intelligence/tools/, poll loop like the others.
launch project_validator   compose_projects/intelligence/tools/project_validator.py --mqtt "$MQTT" --interval 60
# 11. doc_viewer_launcher — [DEC-DOCVIEWER-WINDOW] consumes _app/doc/open {path} and spawns
#     a STANDALONE read-only doc-viewer window per doc (docviewer.compose.yaml), replacing the
#     old in-console doc-browser overlay. Repo-scoped path guard + same-path dedupe. Pure
#     consumer (no periodic publish); ADDED additively — does not touch daemons 1-10 above.
launch doc_viewer_launcher compose_projects/intelligence/tools/doc_viewer_launcher.py --mqtt "$MQTT"
# 12. config_persister    — DURABLE write-back for the Configuration tab's global settings
#     (retention · heartbeat cadence). Subscribes compose_projects/intelligence/<id>/config/{retention,heartbeat},
#     folds edits into compose_projects/intelligence/meta/<id>.meta.json (lifecycle), and re-seeds persisted
#     values so a late-joining widget shows them. The missing subscriber that made config edits
#     evaporate on restart (BUG 2). ADDED additively — does not touch daemons 1-11 above.
launch config_persister    compose_projects/intelligence/publisher/config_persister.py \
       --meta "compose_projects/intelligence/meta/$ID.meta.json" --mqtt "$MQTT" --id "$ID"
# 13. agents_skills_publisher — [CXC-IC-CONFIG-LOADED] projects the registered
#     agent/skill file set (.claude/agents/*.md, .claude/skills/*/SKILL.md) to
#     self/<id>/{agents,skills} for the Configuration tab's Agents/Skills
#     master-detail sections. Reads the FILE SET (not the transcript), so it is
#     session-independent. ADDED additively — does not touch daemons 1-12.
launch agents_skills_publisher compose_projects/intelligence/publisher/agents_skills_publisher.py \
       --claude-dir "$REPO_ROOT/.claude" --mqtt "$MQTT" --id "$ID"

echo "started 13 daemons. tail a log: tail -f $LOG_DIR/<name>.log"
echo "the harness monitor (context alerts / {cmd:context}) is the launcher's job, not this script."
