"""Time BODIES — the semantic realization of ``time/base`` ([CXC-TIME-BODY]).

⛔ RULING — [DD-TIME-BODY-UNIFICATION] = UNIFIED (David, 2026-08-19). A time body
is a time-OBJECT with its OWN source. The idle offset-locale is the DEGENERATE
CASE of that object — a source whose only behavior is "shift the base by a fixed
locale offset". Objects are OBJECT-NAMED (``time/<object>``): multiple ships
(Galactica, Pegasus) each run their own base, so a body's namespace names the
object, never a bare locale. A body SUBSCRIBES a base fact and PUBLISHES
``time/<object>``; no body is aware of any other — the same simultaneous-
realization seam [CXC-VISION-SEMANTIC-REALIZATION] names for widgets.

THE SOURCE KIND is the unifying axis. ``kind`` names WHAT drives an object's time:

  * ``"offset"``   — the degenerate idle case BUILT HERE: base + a fixed IANA-tz
    UTC offset. Realizes ONLY in ``base_mode == "idle"`` (see `realizes_in`).
  * ``"game-base"`` / ``"rotation"`` — a game-world OBJECT's own source (an
    independent ship base, a planet's rotation-inverted phase). ⛔ OUT OF SCOPE
    for time_service: these are the game-world layer's, plugged in at THIS SAME
    ``kind`` seam LATER ([CXC-TIME-PLANET-REALIZATION]). They are NOT built here
    and must not be — the seam is named so the later plug-in is additive, not so
    it is anticipated now.

⚠ INTERIM WIRE FORMAT, IDLE-ONLY (QA note 1). An ``offset`` body's wire value is a
LOCAL EPOCH = base_epoch + the object's UTC offset at that instant (DST-correct
via zoneinfo). A leaf formats it with a UTC/gmtime formatter to render local wall
clock. This local-epoch encoding is an INTERIM format for the idle offset-source
ONLY; it is NOT the settled body-object payload. The richer object payload
(``{source, epoch, offset|phase, ...}``) belongs to the unified body-object model
and firms up when the game-world source kinds are built — deferred, not designed
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

#: The one source kind time_service itself realizes — the degenerate idle locale.
KIND_OFFSET = "offset"


@dataclass
class TimeBody:
    """One time-OBJECT and its source. Today only the ``offset`` (idle locale)
    source is realized here; the object-named, source-kinded shape is what lets
    the game-world source kinds plug in at the same seam later without reshaping
    callers ([DD-TIME-BODY-UNIFICATION] = UNIFIED)."""

    name: str            # the OBJECT name -> time/<name> (ship, caprica, ...)
    tz_name: str         # IANA zone, meaningful for the offset source kind
    kind: str = KIND_OFFSET

    def __post_init__(self) -> None:
        # Resolve the zone once, at construction — a bad tz name is a startup
        # error, never a silent mid-run failure.
        self._tz = ZoneInfo(self.tz_name)

    def realizes_in(self, base_mode: str) -> bool:
        """Whether this object's source produces a value in the given base mode.

        An ``offset`` source is the DEGENERATE IDLE case and realizes ONLY in
        ``idle`` — its offset is applied to a WALL-CLOCK epoch, and applying it to
        a game-elapsed count (seconds since game start, near 1970 as an absolute
        instant) yields nonsense (QA note 2). In game mode the idle offset table
        is REPLACED by the game-world layer's own object sources, which register
        their own realizing modes at this same seam. Unknown/future kinds are
        NOT built here, so they realize in no mode until they are.
        """
        return self.kind == KIND_OFFSET and base_mode == "idle"

    def offset_seconds(self, base_epoch: int) -> int:
        """This object's UTC offset, in seconds, AT the given instant (DST-correct)."""
        dt = datetime.fromtimestamp(int(base_epoch), tz=self._tz)
        off = dt.utcoffset()
        return int(off.total_seconds()) if off is not None else 0

    def realize(self, base_epoch: int) -> int:
        """The offset source's INTERIM idle wire value: base shifted into a local
        wall-clock epoch. Format with a UTC/gmtime formatter to render local time.
        Only valid for the ``offset`` kind in idle mode (see `realizes_in`)."""
        return int(base_epoch) + self.offset_seconds(base_epoch)
