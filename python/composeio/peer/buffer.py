"""Link-down outbound buffer — capture-replay applied to a link ([BPR-LINK-DOWN]).

REUSE, not reinvention: this is the capture-replay-across-a-link semantics of
[AMEND:BP-CAPTURE-REPLAY] bound to the peer's outbound edge. Frames written
while the cross-seam session is down are RECORDED in capture order (the
``.bustrace`` discipline) and REPLAYED in that order on reconnect (the
``ReplayTransport`` discipline), so:

  * non-critical writes are delivered best-effort (idempotent state overwrites
    tolerate loss);
  * a critical write buffered during the outage retains its ``cid`` so the
    origin's end-to-end ack survives the outage and resolves as ack-or-fault.

Bounded like the MQTT pre-connect queue ([DEC-CONSOLE-MQTT-STARTUP-RACE]) so an
unreachable master cannot grow it without limit — the OLDEST non-critical frame
is dropped first; critical frames are never dropped by the cap.
"""

from __future__ import annotations

import threading
from collections import deque

from composeio.peer.frame import CH_CRIT, CH_ACK


class ReplayBuffer:
    """Capture-ordered outbound frame buffer with a bounded cap."""

    def __init__(self, cap: int = 1024):
        self._q: deque[dict] = deque()
        self._cap = cap
        self._lock = threading.Lock()
        self.dropped = 0

    def add(self, frame: dict) -> None:
        with self._lock:
            self._q.append(frame)
            while len(self._q) > self._cap:
                # Drop the oldest NON-critical frame; never a crit/ack.
                dropped = self._drop_oldest_noncritical()
                if not dropped:
                    break

    def _drop_oldest_noncritical(self) -> bool:
        for i, f in enumerate(self._q):
            if f.get("ch") not in (CH_CRIT, CH_ACK):
                del self._q[i]
                self.dropped += 1
                return True
        return False  # all frames are critical — keep them all

    def drain(self) -> list[dict]:
        """Return buffered frames in capture order and clear the buffer."""
        with self._lock:
            frames = list(self._q)
            self._q.clear()
            return frames

    def __len__(self) -> int:
        with self._lock:
            return len(self._q)
