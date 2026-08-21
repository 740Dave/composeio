"""The time PROFILE — MINIMAL loader, deliberately not over-fit.

[DD-TIME-PROFILE-SHAPE] is a RATIFIED-OPEN fork: David left the full profile shape
to firm up WHEN the loader is built (a deferral, not an oversight). So this loader
reads only the three things the platform base clock actually needs today —

  * ``base_mode``  — the BASE MODE, ruled a legitimate CANDIDATE field for
    time_service to read (audit C3): ``"idle"`` (wall-clock, UTC epoch) vs
    ``"game"`` (game-elapsed-seconds-from-start). This is the one profile field
    [DD-TIME-PROFILE-SHAPE] names as scoped to time_service itself.
  * ``tick_hz``    — the publish cadence ([CXC-TIME-BASE] `tick-schedule`,
    default 1 Hz, configurable per profile).
  * ``bodies``     — the idle "time body" table ([CXC-TIME-IDLE-LOCALES]):
    ``{slug: IANA-tz}``.

— and is STRUCTURED so the full shape (per-body offsets/rotation defaults, a
consumer-split, a game body table) can be added later WITHOUT reshaping callers.
It does NOT invent that structure now. Unknown keys are IGNORED, not rejected, so
a richer future profile still loads against this minimal reader.

Idle locale table ([CXC-TIME-IDLE-LOCALES], `status: decided`):
  ship=San Francisco (America/Los_Angeles, INVARIANT — [CXC-TIME-SHIP-INVARIANT]);
  caprica=Copenhagen; virgon=Singapore; tauron=Ho Chi Minh City (Asia/Ho_Chi_Minh,
  DECIDED audit Q1). Kobol is RESERVED — intentionally NOT present in idle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Idle "time bodies": OBJECT slug -> IANA timezone ([DD-TIME-BODY-UNIFICATION] =
# UNIFIED — each is a time-OBJECT whose source is the degenerate ``offset`` kind;
# object-named as ``time/<slug>``). Ho Chi Minh City resolves Vietnam (a single
# tz), audit Q1. KOBOL is deliberately absent — reserved for gameplay. This table
# governs IDLE ONLY; in game mode the game-world layer supplies the object table.
IDLE_BODIES: dict[str, str] = {
    "ship": "America/Los_Angeles",   # San Francisco — INVARIANT ship time
    "caprica": "Europe/Copenhagen",
    "virgon": "Asia/Singapore",
    "tauron": "Asia/Ho_Chi_Minh",
}

#: Named but NOT realized in idle ([CXC-TIME-IDLE-LOCALES] `locale-kobol`). Kept
#: as a named reservation so a reader sees it was OMITTED on purpose, not missed.
RESERVED_BODIES: tuple[str, ...] = ("kobol",)

VALID_BASE_MODES: tuple[str, ...] = ("idle", "game")


@dataclass
class TimeProfile:
    """What time_service itself reads from a profile — the minimal shape.

    Everything a time BODY or the game-world layer might additionally read
    ([DD-TIME-PROFILE-SHAPE]) is intentionally NOT modeled here yet.
    """

    base_mode: str = "idle"
    tick_hz: float = 1.0
    bodies: dict[str, str] = field(default_factory=lambda: dict(IDLE_BODIES))

    def __post_init__(self) -> None:
        if self.base_mode not in VALID_BASE_MODES:
            raise ValueError(
                f"base_mode {self.base_mode!r} not one of {VALID_BASE_MODES}")
        if self.tick_hz <= 0:
            raise ValueError(f"tick_hz must be > 0, got {self.tick_hz!r}")

    @classmethod
    def idle_default(cls) -> "TimeProfile":
        """The idle realization: wall clock, 1 Hz, the four idle locale bodies."""
        return cls()

    @classmethod
    def load(cls, source: Any) -> "TimeProfile":
        """Load from a dict, a JSON string, or a path to a JSON file.

        Reads ONLY ``base_mode`` / ``tick_hz`` / ``bodies``; any other key is
        ignored so a richer future profile still loads. Absent fields fall back
        to the idle defaults, so a partial profile (e.g. one that sets only
        ``base_mode: game``) is valid.
        """
        data = cls._as_dict(source)
        kwargs: dict[str, Any] = {}
        if "base_mode" in data:
            kwargs["base_mode"] = str(data["base_mode"])
        if "tick_hz" in data:
            kwargs["tick_hz"] = float(data["tick_hz"])
        if "bodies" in data and isinstance(data["bodies"], dict):
            kwargs["bodies"] = {str(k): str(v) for k, v in data["bodies"].items()}
        return cls(**kwargs)

    @staticmethod
    def _as_dict(source: Any) -> dict:
        if isinstance(source, dict):
            return source
        if isinstance(source, (str, Path)):
            text = source
            p = Path(source)
            # A path that exists is read as a file; otherwise treat the string as
            # JSON text directly.
            if isinstance(source, Path) or p.exists():
                text = p.read_text(encoding="utf-8")
            return json.loads(text)
        raise TypeError(f"cannot load a TimeProfile from {type(source).__name__}")
