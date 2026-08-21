#!/usr/bin/env bash
# install.sh — install (and load) the com.composeio.console-stack LaunchAgent so the Intelligence
# Console + Launch Bar ribbon + app_launcher come up at LOGIN
# ([SPEC:CXC-LAUNCHBAR] [CXC-LB-AUTOSTART]; DEFECT-LAUNCH-BAR-RIBBON-NOT-RUNNING).
#
# It copies the tracked plist to ~/Library/LaunchAgents/ and `launchctl load`s it. The plist
# references the CANONICAL checkout (/Users/davidleeburnett/Dropbox/Maker/composeio) — NEVER a
# worktree — so RUN THIS FROM THE CANONICAL CHECKOUT after review (a boot agent pointing at a
# worktree would be wrong). The install is a system action; David runs it.
#
# Usage:
#   deploy/launchagents/install.sh            # validate, copy, load
#   deploy/launchagents/install.sh --lint     # validate the plist only (plutil -lint), no install
#   deploy/launchagents/uninstall.sh          # unload + remove
set -euo pipefail

LABEL="com.composeio.console-stack"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SRC_DIR/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

[[ -f "$PLIST_SRC" ]] || { echo "!! plist not found: $PLIST_SRC" >&2; exit 1; }

echo "== validating $PLIST_SRC"
plutil -lint "$PLIST_SRC"

if [[ "${1:-}" == "--lint" ]]; then
  echo "lint only — not installing."
  exit 0
fi

# Sanity: the plist must point at the CANONICAL script, and it must exist + be executable.
CANONICAL_SCRIPT="/Users/davidleeburnett/Dropbox/Maker/composeio/intelligence/start-console-stack.sh"
if ! grep -q "$CANONICAL_SCRIPT" "$PLIST_SRC"; then
  echo "!! plist does not reference the canonical bringup ($CANONICAL_SCRIPT) — refusing" >&2
  exit 1
fi
if [[ ! -x "$CANONICAL_SCRIPT" ]]; then
  echo "!! canonical bringup missing/not executable: $CANONICAL_SCRIPT" >&2
  echo "!! (are you running from the canonical checkout, and is it committed + chmod +x?)" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

# If already loaded, unload first so a re-install re-reads the plist (bootout is the modern verb;
# fall back to legacy unload on older macOS).
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "== unloading existing $LABEL"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

echo "== installing -> $PLIST_DST"
cp "$PLIST_SRC" "$PLIST_DST"

echo "== loading $LABEL"
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST" 2>/dev/null || launchctl load "$PLIST_DST"

echo "== verify"
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \
  && echo "OK: $LABEL is loaded. It will bring up the console stack at every login." \
  || { echo "!! $LABEL did not load" >&2; exit 1; }

echo
echo "It also RunAtLoad-fires now — verify the stack came up:"
echo "  pgrep -fl 'console.compose.yaml'"
echo "  pgrep -fl 'launch_bar.compose.yaml'"
echo "  pgrep -fl 'intelligence.tools.app_launcher'"
echo "  tail -n 40 ~/Library/Logs/composeio-console-stack.log"
