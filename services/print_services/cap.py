"""Print-freely, cap-safely page cap (Haven DP-PRINT-2/3 shape).

A per-period PAGE CAP, enforced at submit. Over-cap returns an ERROR result — it
is NOT an approval gate: the design prints freely up to a safety rail and then
refuses honestly, rather than blocking every job behind a human yes/no.
"""

from __future__ import annotations

import threading
from datetime import date


class PageCap:
    def __init__(self, cap: int):
        self.cap = cap
        self._used = 0
        self._reset_day = date.today()
        self._lock = threading.Lock()

    def _rollover(self):
        today = date.today()
        if today != self._reset_day:
            self._used = 0
            self._reset_day = today

    def try_consume(self, pages: int) -> tuple[bool, str | None]:
        """Reserve `pages` against the cap. Returns (ok, error_message)."""
        pages = max(int(pages), 1)
        with self._lock:
            self._rollover()
            if self._used + pages > self.cap:
                return False, (f"daily page cap reached "
                               f"({self._used}/{self.cap}); {pages}-page job "
                               f"refused. Resets at local midnight.")
            self._used += pages
            return True, None

    def status(self) -> dict:
        with self._lock:
            self._rollover()
            return {"pages_used": self._used, "cap": self.cap,
                    "reset_day": self._reset_day.isoformat()}
