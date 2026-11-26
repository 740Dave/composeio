#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  BREAK GLASS — start the Architect OUTSIDE the Intelligence Console.
#
#  WHEN TO RUN THIS
#    The console is dead, wedged, or cannot host an Intelligence, and you need
#    the Architect anyway.  This is the RECOVERY path ([DEC-ICL-UNHARNESSED-
#    RECOVERY-INSTANCE]) — not normal operations.  Normal operations is a pane
#    in the console.
#
#  WHY NOT transport.sh — AND WHY THIS IS A COLD START (David, 2026-08-12)
#    `transport.sh` RESUMES an existing session, so it depends on Claude Code's
#    resume data — which Claude Code CULLS after a while.  The longer the outage,
#    the less likely a resume is even possible; the failure mode is worst exactly
#    when you need recovery most.  So this script COLD-STARTS
#    (`RESUME_SESSION=` empty) and therefore has NO dependency on resume data.
#    It works after an outage of any length.
#
#    Corollary worth internalizing: session resume is PERISHABLE, so a role's
#    continuity can never ride on it.  What survives a cull is the role's MEMORY,
#    the decision graph, the specs and the handoff.  That is why continuity lives
#    in files and not in sessions — and why per-role memory is load-bearing rather
#    than a convenience.
#
#    Side effect worth knowing: because this is a cold start from the instance
#    home, it is ALSO the cutover the home's README describes as never yet done
#    (`claude --resume` re-pins to the dir a session was CREATED in, so a resume
#    can never re-key an instance; only a cold start can).
#
#  WHAT IT DOES
#    Cold-starts a NEW Architect session in a plain terminal, under the
#    launcher (a transparent PTY proxy — YOU answer the startup dialogs), with:
#      * a DISTINCT id so it can never displace a running Architect
#      * the instance-keyed home, so it gets the ROLE'S MEMORY
#        ([DEC-INSTANCE-KEYED-HOMES])
#      * the repo mounted as an additional directory, so it can edit everything
#
#  HOW TO RUN IT
#      ${COMPOSEIO_HOME:-$HOME/composeio}/intelligence/launcher/break-glass.sh
#
#    That is the whole command.  No arguments.  Add --check to run every safety
#    test and print what WOULD happen, without starting anything.
#
#  WHEN YOU ARE DONE
#    /exit as normal.  Nothing to clean up.  This instance wears a `recovery-`
#    id on purpose, so it never claims to be the canonical Architect.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CHECK=0
[[ "${1:-}" == "--check" || "${1:-}" == "-n" ]] && CHECK=1

# THREE levels up (compose_projects/intelligence/launcher/ -> repo). The old two-`..` fallback
# disagreed with the COMPOSEIO_REPO_ROOT branch, which is a real repo root: $REPO/intelligence/...
# below resolved only in the fallback branch and broke whenever the env var was set correctly.
REPO="${COMPOSEIO_REPO_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
PROJECTS="$REPO/compose_projects"
[ -f "$REPO/scripts/lib/overlay-env.sh" ] && . "$REPO/scripts/lib/overlay-env.sh"  # host overlay, before our defaults ([CXC-SCL-SCRUB-OVERLAY-PARITY])
ROLE="${ROLE:-composeux-architect}"
ID="${INTELLIGENCE_ID:-recovery-architect-2}"
# ⚠ MIGRATION PENDING. This defaults to the home that EXISTS TODAY —
# `~/compose-instances/architect/`, created 2026-07-23 under [DEC-INSTANCE-KEYED-HOMES],
# which is keyed by INSTANCE id. David ratified ROLE-keyed homes on 2026-08-12
# ([DD-FC-PROJECT-DIR-LAYOUT], [SPEC:CXC-FLEET]), so the correct home becomes
# `~/compose-instances/composeux-architect/`. Break-glass MUST keep pointing at what
# actually exists — a recovery path that depends on an unperformed migration is not a
# recovery path — so update this line WITH the migration, not before it.
HOME_DIR="${INSTANCE_HOME:-$HOME/compose-instances/architect}"
MQTT="${COMPOSEBUS_MQTT:-mqtt://mqtt-broker.local:1883}"
CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.claude/local/claude")}"

say()  { printf '  %s\n' "$*"; }
fail() { printf '\n  ✗ %s\n\n' "$*" >&2; exit 1; }

printf '\n  BREAK GLASS — Architect outside the console\n\n'

# ── 1. the claude binary ─────────────────────────────────────────────────────
[[ -x "$CLAUDE_BIN" ]] || fail "claude not found at '$CLAUDE_BIN'. Set CLAUDE_BIN=/path/to/claude and re-run."
say "claude .......... $CLAUDE_BIN"

# ── 2. the repo ──────────────────────────────────────────────────────────────
[[ -f "$PROJECTS/intelligence/launcher/launcher.py" ]] || fail "repo not found at '$REPO' (no compose_projects/intelligence/launcher/launcher.py)."
say "repo ............ $REPO"

# ── 3. the instance home — this is what gives it the ROLE'S MEMORY ───────────
#    Created if absent, with the repo mounted so the instance can edit it.
if [[ ! -d "$HOME_DIR" ]]; then
  say "home ............ creating $HOME_DIR"
  if [[ $CHECK -eq 0 ]]; then
    mkdir -p "$HOME_DIR/.claude"
    cat > "$HOME_DIR/.claude/settings.json" <<JSON
{
  "permissions": {
    "additionalDirectories": ["$REPO"]
  }
}
JSON
  fi
else
  say "home ............ $HOME_DIR"
fi
[[ -f "$HOME_DIR/.claude/settings.json" ]] && \
  say "                  (repo mounted via additionalDirectories — it can edit the tree)"

# ── 4. REFUSE to displace a live instance of the same id ─────────────────────
#    A second channel server under one id is the displacement defect; refuse
#    loudly BEFORE the fork rather than discovering it on the glass.
LIVE=""
for p in $(pgrep -f "channel.mjs" 2>/dev/null || true); do
  pid_id="$(ps eww -o command= -p "$p" 2>/dev/null | tr ' ' '\n' | grep '^INTELLIGENCE_ID=' | cut -d= -f2 || true)"
  [[ "$pid_id" == "$ID" ]] && LIVE="$LIVE $p"
done
[[ -n "$LIVE" ]] && fail "id '$ID' is ALREADY LIVE (pid:$LIVE). Use a different id:  INTELLIGENCE_ID=recovery-architect-3 $0"
say "id .............. $ID  (free — will not displace anything)"

# ── 4b. THE MANIFEST CHAIN MUST RESOLVE — resolved HERE exactly as the launcher
#    does it ([CXC-TERM-MANIFEST-CHAIN]: <id>.meta.json, then <role>.meta.json,
#    then REFUSE).  This check exists because the first live firing of this
#    script (2026-08-12) was REFUSED for no-manifest: the script printed the role
#    but never PASSED it, so the launcher derived role=id and chain leg 3 was
#    never tried.  The other six checks were all green.  A preflight that does
#    not exercise the real failure certifies a narrower class than its name
#    claims — so this leg resolves the same files the launcher will.
CHAIN_OK=""
for cand in "$PROJECTS/intelligence/meta/$ID.meta.json" "$PROJECTS/intelligence/meta/$ROLE.meta.json"; do
  [[ -f "$cand" ]] && { CHAIN_OK="$cand"; break; }
done
[[ -n "$CHAIN_OK" ]] || fail "manifest chain EXHAUSTED — no '$ID.meta.json' and no '$ROLE.meta.json' in $PROJECTS/intelligence/meta/.
    The launcher will REFUSE to spawn (correctly — an unoriented Architect is worse than none).
    Fix: author a manifest for the role, or set ROLE=<a role that has one> and re-run."
say "role ............ $ROLE"
say "manifest ........ ${CHAIN_OK#"$REPO/"}  (chain resolves — launcher will not refuse)"

# ── 5. broker — a WARNING, never a blocker ───────────────────────────────────
#    You may be running this precisely because the bus is sick.
BROKER_HOST="$(printf '%s' "$MQTT" | sed -E 's#^[a-z]+://##; s#:.*##')"
BROKER_PORT="$(printf '%s' "$MQTT" | sed -E 's#.*:##')"
if python3 -c "
import socket,sys
s=socket.socket(); s.settimeout(3)
try: s.connect(('$BROKER_HOST',int('$BROKER_PORT'))); print('ok')
except Exception: sys.exit(1)
" >/dev/null 2>&1; then
  say "bus ............. $MQTT  (reachable)"
else
  say "bus ............. $MQTT  ⚠ NOT REACHABLE — starting anyway."
  say "                  You get the Architect; it just has no voice on the bus."
fi

# ── 6. go ────────────────────────────────────────────────────────────────────
if [[ $CHECK -eq 1 ]]; then
  printf '\n  ✓ all checks passed — this WOULD cold-start now (--check, so nothing started)\n\n'
  exit 0
fi

printf '\n  Starting. You answer the startup dialogs yourself, exactly as if you ran claude.\n'
printf '  Say "complete bootstrap" when it comes up.\n\n'

cd "$HOME_DIR"
# INTELLIGENCE_ROLE is chain leg 3 and is NOT optional here: it defaults to the
# ID, and a `recovery-` id has no manifest of its own by design — the whole point
# is that N instances (architect, architect-hosted, recovery-architect-2) share
# ONE role manifest.  Omitting it is what made the first live firing refuse.
INTELLIGENCE_ID="$ID" \
INTELLIGENCE_ROLE="$ROLE" \
COMPOSEBUS_MQTT="$MQTT" \
COMPOSEIO_REPO_ROOT="$REPO" \
RESUME_SESSION= \
CLAUDE_BIN="$CLAUDE_BIN" \
exec python3 "$PROJECTS/intelligence/launcher/launcher.py"
