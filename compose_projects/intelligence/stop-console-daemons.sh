#!/usr/bin/env bash
# stop-console-daemons.sh — stop the HEADLESS-FALLBACK data-layer daemons started by
# start-console-daemons.sh. As of 2026-07-22 the console runs these IN-PROCESS
# (compose_projects/intelligence/console/data_sources.py); those are DAEMON THREADS inside the console process, so
# they stop when the console does — this script does NOT (cannot) stop them, only the standalone
# subprocess fallback. Matches the publisher/tool module names; leaves the launcher + the console
# GUI + your claude session alone.
set -uo pipefail
for name in self_publisher transcript_publisher channel_bridge \
            deliverable_publisher deliverable_monitor deliverable_opener \
            deliverable_watcher memory_monitor; do
  if pkill -f "compose_projects/intelligence/publisher/$name.py"; then
    echo "  stopped $name"
  fi
done
# away_watch_publisher lives under compose_projects/intelligence/tools/ (not publisher/)
if pkill -f "compose_projects/intelligence/tools/away_watch_publisher.py"; then
  echo "  stopped away_watch_publisher"
fi
if pkill -f "compose_projects/intelligence/tools/project_validator.py"; then
  echo "  stopped project_validator"
fi
if pkill -f "compose_projects/intelligence/tools/doc_viewer_launcher.py"; then
  echo "  stopped doc_viewer_launcher"
fi
echo "done. (the launcher + your claude session are untouched.)"
