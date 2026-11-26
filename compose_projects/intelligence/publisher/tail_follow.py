#!/usr/bin/env python3
"""tail_follow — the ONE shared seek-to-end/follow reader for the transcript daemons
([SPEC:CXC-IC-STREAMS], durability invariant).

WHY THIS EXISTS (the defect it closes): `transcript_publisher.py` and `deliverable_monitor.py`
each hand-rolled the SAME tail loop — `seek(0, SEEK_END)` then `line = fh.readline(); json.loads(
line) except: continue`. That loop SILENTLY DROPS any JSONL line whose terminating newline has not
yet been written when the tailer catches the file mid-append. `readline()` returns the pre-EOF
FRAGMENT (a line WITHOUT a trailing '\\n'); `json.loads` fails; `continue` discards it — AND the
file offset has already advanced PAST the fragment, so the remainder read on the next poll is a
second unparseable fragment. The whole line is lost forever. Both daemons' comments even asserted
"the next read completes it" — it does not.

This bites the LARGE lines hardest: an `Agent` subagent dispatch (carries the full sub-prompt) and
its completion (carries the full sub-output) are multi-KB and are exactly the lines most likely to
be split across writes. That is why `self/subagents` went SILENT (no running chip ever appeared)
while the smaller `self/activity`/`thread/narration` lines mostly survived — a size-correlated,
intermittent-looking drop with a deterministic cause.

THE FIX (de-fork + correct): one `LineFollower` that BUFFERS a partial trailing line until its
newline arrives, so a line written across multiple flushes is delivered WHOLE, never as a fragment.
Both daemons reuse it BY REFERENCE — a future daemon that tails the transcript inherits the
durability guarantee instead of re-deriving (and re-breaking) it.

Scope note (unchanged from the originals, still v1): this follows APPENDS to a growing file. Log
rotation / truncation / inode replacement mid-run is out of scope here (a durable-offset refinement),
exactly as the original tailers documented.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional


class LineFollower:
    """Non-blocking, partial-line-safe reader over a file positioned by the caller (typically at
    SEEK_END). `poll()` returns the COMPLETE lines available right now; a partial trailing line
    (no terminating newline yet) is retained in an internal buffer and completed on a later poll —
    it is NEVER emitted as a fragment. Lines are returned stripped, with blank lines skipped
    (matching what both daemons already did after `readline()`)."""

    def __init__(self, fh):
        self._fh = fh
        self._buf = ""

    def poll(self) -> List[str]:
        out: List[str] = []
        while True:
            chunk = self._fh.readline()
            if not chunk:
                break  # true EOF — nothing more to read this poll
            self._buf += chunk
            if not self._buf.endswith("\n"):
                # Partial mid-write line: the newline has not landed yet. Keep buffering; do NOT
                # parse or emit a fragment, and do NOT lose it. This is the whole defect.
                break
            line = self._buf.strip()
            self._buf = ""
            if line:
                out.append(line)
        return out


def follow_lines(fh, poll_s: float = 0.5, on_idle: Optional[Callable[[], None]] = None):
    """Blocking generator: yields each COMPLETE line appended to `fh` from its current position,
    forever. On an idle poll (no complete line available — including when only a partial line is
    buffered) it invokes `on_idle` (if given) then sleeps `poll_s`, so callers can run a low-cadence
    side task (e.g. the avatar re-classifier) without a second loop."""
    follower = LineFollower(fh)
    while True:
        lines = follower.poll()
        if not lines:
            if on_idle is not None:
                on_idle()
            time.sleep(poll_s)
            continue
        for line in lines:
            yield line
