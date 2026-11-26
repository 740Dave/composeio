#!/usr/bin/env bash
# Transport a running Claude Code session INTO the Intelligence Console (spec 34, CXC-IC-LIFECYCLE).
#
# It re-hosts an EXISTING session via `claude --resume <id>` under the launcher, which is a
# TRANSPARENT PTY PROXY + /compact relay: it forwards your terminal <-> claude byte-for-byte (so
# YOU answer the startup dialogs, exactly as if you ran claude yourself) and does only ONE special
# thing — when the resumed instance emits a self-directed compact directive out its channel, the
# launcher types `/compact <direction>` into it. A re-host, NOT a compaction (context crosses
# intact), so NOTHING is injected at startup.
#
# Usage:   ./transport.sh <session-id-to-resume>
# Example: ./transport.sh 59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3
#
# IMPORTANT — run this from the SAME directory the session was originally started in (its project
# dir). `claude --resume <id>` is project-scoped BY CWD: it only finds the session, and only loads
# the channel `.mcp.json`, relative to the cwd it is resumed in. This script does NOT `cd`
# anywhere — it runs from your current directory and ensures a `.mcp.json` exists HERE. For a
# home-project session, run it from ~ (it will ensure ~/.mcp.json).
#
# NOTE: exit the target session first (a session can't be resumed while it's still active). On
# /exit, Claude Code prints the resume id; pass it here.

set -euo pipefail
SESSION="${1:?usage: transport.sh <session-id-to-resume> (run from its project dir)}"

# Repo root = THREE levels up from THIS script (compose_projects/intelligence/launcher/ -> repo).
# ⛔ This comment said "two levels up" and had ALREADY been updated to name compose_projects/, so it
#    argued the stale count was right. $REPO was <repo>/compose_projects, and it is exported below as
#    COMPOSEIO_ROOT / FACTLATTICE_CORPUS_ROOT / COMPOSEIO_REPO_ROOT — the variables
#    python/composeio/bootstrap_paths.py and decision_ops/_corpusseat.py read as THE REPO ROOT. Every
#    session spawned from the regenerated .mcp.json inherited the wrong root.
# $PROJECTS is the separate name for "where the compose_projects live"; the launcher and the channel
# server are located under it by ABSOLUTE path. We do NOT cd into either.
REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
PROJECTS="$REPO/compose_projects"
[ -f "$REPO/scripts/lib/overlay-env.sh" ] && . "$REPO/scripts/lib/overlay-env.sh"  # host overlay, before our defaults ([CXC-SCL-SCRUB-OVERLAY-PARITY])

CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.claude/local/claude")}"
MQTT="${COMPOSEBUS_MQTT:-mqtt://mqtt-broker.local:1883}"
ID="${INTELLIGENCE_ID:-architect}"

# 0. [DEC-INSTANCE-KEYED-HOMES] (David 2026-07-23) OPTIONAL instance home. An instance works ACROSS
#    many Compose* projects, so keying its memory/statusline/agents by PROJECT re-fragments the
#    moment it cds between them — the key is the INSTANCE (role/identity), not the project. Set
#    INSTANCE_HOME=~/compose-instances/<role>/ to cd there before launching, so the harness keys all
#    of that to the INSTANCE. OPT-IN + SAFE: default empty = the exact prior behavior (run from cwd),
#    so a legacy home-created session's resume is UNCHANGED. NOTE the resume constraint: `claude
#    --resume` re-pins to the project dir the session was CREATED in — so this RE-KEYS only a session
#    COLD-STARTED in the instance home; a legacy home session stays home until re-created there.
#    The Compose* projects are reached as `additionalDirectories` in $INSTANCE_HOME/.claude/settings*.
INSTANCE_HOME="${INSTANCE_HOME:-}"
if [[ -n "$INSTANCE_HOME" ]]; then
  mkdir -p "$INSTANCE_HOME"
  cd "$INSTANCE_HOME"
  echo "[transport] instance home: cd '$INSTANCE_HOME' — memory/statusline/agents keyed to instance '$ID'"
fi

# 1. Ensure the architect's MCP servers are registered as project MCP servers IN THE CURRENT
#    DIRECTORY, all by ABSOLUTE path — so they load no matter which project dir this session is
#    resumed in (a relative path would only resolve from the repo root, which we are not in).
#    THREE servers: composeio-channel (the bus channel), magma (the FactLattice/MAGMA query engine,
#    [DEC-DECISION-MCP-MAGMA-REFRESH]), and decision-ops (the decision-graph MCP that pushes a MAGMA
#    refresh on write). .mcp.json is gitignored, so THIS block is the durable source of truth — a
#    fresh machine regenerates the full manifest. Keyed on `decision-ops` so an older channel-only
#    file gets upgraded to all three.
CHANNEL_JS="${PROJECTS}/intelligence/channel/channel.mjs"
# [lts-engine cutover 2026-07-23] The engine lives in the PRIVATE DEPLOYMENT (~/factlattice-internal-dev):
# a clean checkout of the public lts-engine repo + the gitignored private overlay (real corpora config).
# The old ~/factlattice was retired. FL -> deployment; the real 8-corpus mapping is the overlay JSON
# (FACTLATTICE_CORPUS_CONFIG), so the generic public config.py loads our corpora without any private
# content in the tracked tree. The "magma" server was renamed to "lts-engine" (tools are now lts_*).
# ⚠ RENAMED 2026-08-16: `~/factlattice-deployment` -> `~/factlattice-internal-dev` (the checkout
# was re-pointed from the PUBLIC 740Dave/factlattice to the PRIVATE 740Dave/factlattice-internal-dev;
# the old path no longer exists). Single hardcoded default, deliberately no fallback chain.
# [rename 2026-08-16] ~/factlattice-deployment was renamed to ~/factlattice-internal-dev. This file
# kept the OLD path for a day, and it is the durable source of truth that regenerates .mcp.json — so
# every server spawned from it carried PYTHONPATH + FACTLATTICE_ENGINE_ROOT pointing at a directory
# that no longer exists. Seven such processes were still live on 2026-08-17, reads working only
# because their modules were already in memory. Verify with: cd /tmp && python3 -c "import
# factlattice; print(factlattice.__file__)" — the live root is whatever that names.
FL="${FACTLATTICE_ROOT:-$HOME/factlattice-internal-dev}"
CORPUS_CFG="${FACTLATTICE_CORPUS_CONFIG:-$HOME/factlattice-internal-dev/private/corpus-config.json}"
# ⛔ THE CORPORA LEFT THE REPO 2026-08-17. Deriving this from ${REPO} was doubly
#    wrong: the path no longer exists there, AND ${REPO} is derived from this
#    script's own location (line 28), so running it from a worktree copy pointed
#    every decision write at a graph inside that worktree — live on no graph, and
#    destroyed with the worktree. Override with COMPOSEIO_CORPORA_ROOT, the same
#    variable python/composeio/bootstrap_paths.py reads.
CORPORA_HOME="${COMPOSEIO_CORPORA_ROOT:-$HOME/.claude/factlattice_corpora/composeio/corpora}"
DECISION_GRAPH="${CORPORA_HOME}/intelligence-console/decision-graph-composeux-architect.lts"
# DSD roots OUTSIDE this repo. A project staged outside composeio (and possibly not under git at all)
# still needs the DSD viewer; without this, send_dsd_for_human_review answers "not a repo file" and
# the workaround people reach for is copying the DSD into someone else's tree to satisfy the check.
# os.pathsep-separated. Entries MUST be absolute — a relative entry is refused, because it would
# silently mean "under whatever cwd this process happens to have". `/` is refused too: it would
# admit every path while still looking like a containment guard. See [CXC-DSDV] / COMPOSEIO_DSD_ROOTS.
DSD_ROOTS="${COMPOSEIO_DSD_ROOTS:-${DATASHEETS_HOME:-$HOME/datasheets}/R503}"
if ! grep -q '"lts-engine"' .mcp.json 2>/dev/null; then
  cat > .mcp.json <<JSON
{
  "mcpServers": {
    "composeio-channel": {
      "command": "node",
      "args": ["${CHANNEL_JS}"],
      "env": { "INTELLIGENCE_ID": "${ID}", "COMPOSEBUS_MQTT": "${MQTT}" }
    },
    "lts-engine": {
      "command": "python3",
      "args": ["-m", "factlattice_engine.server"],
      "env": {
        "PYTHONPATH": "${FL}",
        "COMPOSEIO_ROOT": "${REPO}",
        "FACTLATTICE_CORPUS_ROOT": "${REPO}",
        "FACTLATTICE_CORPUS_CONFIG": "${CORPUS_CFG}"
      }
    },
    "decision-ops": {
      "command": "python3",
      "args": ["-m", "composeio.decision_ops.mcp_server"],
      "env": {
        "PYTHONPATH": "${FL}",
        "COMPOSEIO_ROOT": "${REPO}",
        "FACTLATTICE_ENGINE_ROOT": "${FL}",
        "FACTLATTICE_CORPUS_ROOT": "${REPO}",
        "FACTLATTICE_CORPUS_CONFIG": "${CORPUS_CFG}",
        "COMPOSEIO_DSD_ROOTS": "${DSD_ROOTS}",
        "DECISION_OPS_GRAPH": "${DECISION_GRAPH}"
      }
    }
  }
}
JSON
  echo "[transport] wrote $(pwd)/.mcp.json (composeio-channel + lts-engine + decision-ops @ deployment)"
fi

# 1b. [CXC-GV-SCOPE] Pin the run-grained session token INTO the decision-ops server's own env block
#     when one is set. Belt AND braces: an MCP server's declared `env` is the one environment we can
#     be certain it gets, whereas whether the client MERGES the parent environment into it is a client
#     detail we should not bet a run's decision provenance on. OPT-IN BY PRESENCE and self-cleaning —
#     with the variable unset the key is REMOVED, so a stale token from a finished watch can never
#     leak into the next session's decision writes. Touches only that one key; .mcp.json is
#     gitignored local config.
if [[ -f .mcp.json ]]; then
  COMPOSEIO_DECISION_SESSION="${COMPOSEIO_DECISION_SESSION:-}" python3 - <<'PY' || echo "[transport] (could not pin the session token into .mcp.json; env-inheritance route still applies)"
import json, os, pathlib
tok = os.environ.get("COMPOSEIO_DECISION_SESSION", "").strip()
p = pathlib.Path(".mcp.json")
cfg = json.loads(p.read_text())
env = cfg.get("mcpServers", {}).get("decision-ops", {}).get("env")
if isinstance(env, dict):
    before = env.get("COMPOSEIO_DECISION_SESSION")
    if tok:
        env["COMPOSEIO_DECISION_SESSION"] = tok
    else:
        env.pop("COMPOSEIO_DECISION_SESSION", None)
    if before != env.get("COMPOSEIO_DECISION_SESSION"):
        p.write_text(json.dumps(cfg, indent=2) + "\n")
        print(f"[transport] decision-ops session token -> {tok or '(cleared)'}")
PY
fi

# 2. Resume the session into the console under the launcher (transparent proxy + /compact relay).
#    No move-note, no bootstrap: a resume carries its context. Run from the current cwd (do NOT cd).
#    COMPOSEIO_REPO_ROOT pins the repo by ABSOLUTE path so anything launched inside this session
#    (e.g. the console's in-process data layer, _maybe_start_inprocess_sources) resolves repo-relative
#    files regardless of the resumed cwd — `claude --resume` restores the session's ORIGINAL cwd
#    (often ~), which would otherwise make data_sources.py silently unresolvable -> viewer-only.
echo "[transport] resuming session ${SESSION} into the console on ${MQTT} (claude=${CLAUDE_BIN}) from $(pwd)"
#    COMPOSEIO_DECISION_SESSION ([CXC-GV-SCOPE], David 2026-08-06) is FORWARDED, not set: when an
#    orchestrator (away-watch-start.sh) established a run-grained session token before bouncing, the
#    resumed process — and the decision-ops MCP server it spawns — inherit it, so decisions written
#    during that run carry the run's slug instead of a bare date. UNSET is the normal case and means
#    exactly the prior behaviour (decided-in-session supplied explicitly per write); nothing is ever
#    defaulted or invented here. Verify propagation from inside the server with `decision_session_token`.
INTELLIGENCE_ID="${ID}" \
COMPOSEBUS_MQTT="${MQTT}" \
COMPOSEIO_REPO_ROOT="${REPO}" \
RESUME_SESSION="${SESSION}" \
CLAUDE_BIN="${CLAUDE_BIN}" \
COMPOSEIO_DECISION_SESSION="${COMPOSEIO_DECISION_SESSION:-}" \
exec python3 "${PROJECTS}/intelligence/launcher/launcher.py"
