#!/usr/bin/env bash
# uninstall.sh — unload + remove the com.composeio.console-stack LaunchAgent.
# Does NOT kill a currently-running console stack (bounce/kill those separately if wanted); it only
# stops the stack from coming up at the NEXT login.
set -euo pipefail

LABEL="com.composeio.console-stack"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "== unloading $LABEL"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl unload "$PLIST_DST" 2>/dev/null || true
else
  echo "== $LABEL not loaded"
fi

if [[ -f "$PLIST_DST" ]]; then
  echo "== removing $PLIST_DST"
  rm -f "$PLIST_DST"
else
  echo "== $PLIST_DST not present"
fi

echo "OK: $LABEL uninstalled — the console stack will NOT autostart at the next login."
echo "(A running stack is untouched; kill it manually if you want it down now.)"
