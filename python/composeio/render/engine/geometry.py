"""
ComposeUX standalone-window geometry persistence ([SPEC:CXC-DVL]
[CXC-DVL-GEOMETRY]).

A standalone runtime window (the Decisions viewer) must reopen at the SAME
size and placement the user last left it.  This module is the PURE half of
that contract — the write-on-change / restore-on-launch precedence, the
off-screen clamp, the debounce, and a JSON-file store — with ZERO SDL / skia
dependency, so the whole thing is unit-testable with an injected store and no
real window ([CXC-DVL-VERIFY-WINDOW] verify.machine).

Where the geometry lives is NOT decided here (that is the runtime's wiring):
the store is an injectable object with ``load(key)`` / ``save(key, geom)``.
The architect's ruling ([DD-DVL-GEOMETRY-STORE] resolved PER-INSTANCE) is
realized by pointing ``JsonFileGeometryStore`` at the instance's
``meta.<id>.meta.json`` under a dedicated ``window_geometry`` section — the
config_persister meta-object seam, reused by reference (same atomic
temp-file + os.replace discipline, [DX-NARROW-WAIST]), preserving every
other key the meta-object holds.

Determinism ([CXC-R-C1]) is unaffected: geometry only sizes/places the OS
window; layout is a pure function of the client size the runtime feeds it.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

@dataclass
class WindowGeometry:
    """A window's screen geometry: origin ``(x, y)`` + client size ``(w, h)``.

    ``x`` / ``y`` MAY be ``None`` (the OS picks the origin — the centered
    default); ``w`` / ``h`` are always concrete positive ints once valid."""
    x: Optional[int]
    y: Optional[int]
    w: int
    h: int

    @property
    def has_pos(self) -> bool:
        return self.x is not None and self.y is not None

    @property
    def is_valid(self) -> bool:
        """A record is usable iff it carries a positive client size.  A
        zero/negative size is treated as MALFORMED — the fail-safe default
        applies, never a degenerate window ([CXC-DVL-GEOMETRY] FAIL-SAFE)."""
        try:
            return int(self.w) > 0 and int(self.h) > 0
        except (TypeError, ValueError):
            return False

    def to_dict(self) -> Dict[str, Any]:
        return {"x": self.x, "y": self.y, "w": int(self.w), "h": int(self.h)}

    @classmethod
    def from_dict(cls, d: Any) -> Optional["WindowGeometry"]:
        """Parse a persisted record; return None for anything malformed
        (never raise — a corrupt store is a silent fall-back, not a crash)."""
        if not isinstance(d, dict):
            return None
        try:
            w = int(d["w"])
            h = int(d["h"])
        except (KeyError, TypeError, ValueError):
            return None
        x = d.get("x")
        y = d.get("y")
        try:
            x = None if x is None else int(x)
            y = None if y is None else int(y)
        except (TypeError, ValueError):
            x = y = None
        g = cls(x, y, w, h)
        return g if g.is_valid else None


# ---------------------------------------------------------------------------
# The store contract + a JSON-file backend (the meta-object seam)
# ---------------------------------------------------------------------------

class GeometryStore:
    """Interface: ``load(key)`` reads a window's saved geometry (or None),
    ``save(key, geom)`` durably records it.  Concrete backends decide WHERE."""

    def load(self, key: str) -> Optional[WindowGeometry]:      # pragma: no cover
        raise NotImplementedError

    def save(self, key: str, geom: WindowGeometry) -> bool:    # pragma: no cover
        raise NotImplementedError


class NullGeometryStore(GeometryStore):
    """No-op store: never restores, never persists.  The default when a scene
    does not opt into ``persist_geometry``."""

    def load(self, key: str) -> Optional[WindowGeometry]:
        return None

    def save(self, key: str, geom: WindowGeometry) -> bool:
        return False


class JsonFileGeometryStore(GeometryStore):
    """Geometry in a JSON file under a dedicated ``section`` key, keyed by
    window identity ([CXC-DVL-GEOMETRY] WINDOW IDENTITY).

    Pointed at the instance's ``meta.<id>.meta.json`` this IS the per-instance
    meta-object seam ([DD-DVL-GEOMETRY-STORE] resolved): a read-modify-write
    that preserves every sibling key (``lifecycle``, ``id``, …) exactly as
    config_persister's ``_write_meta`` does — the same atomic temp-file +
    os.replace so a crash can never leave a truncated meta-object.  A missing
    file reads as "no geometry" (first launch); an unreadable/corrupt file is
    a silent None (fail-safe)."""

    def __init__(self, path: str, section: str = "window_geometry") -> None:
        self.path = path
        self.section = section

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def load(self, key: str) -> Optional[WindowGeometry]:
        data = self._read()
        section = data.get(self.section)
        if not isinstance(section, dict):
            return None
        return WindowGeometry.from_dict(section.get(key))

    def save(self, key: str, geom: WindowGeometry) -> bool:
        if not geom.is_valid:
            return False
        data = self._read()
        section = data.get(self.section)
        if not isinstance(section, dict):
            section = {}
            data[self.section] = section
        section[key] = geom.to_dict()
        return _atomic_write_json(self.path, data)


def _atomic_write_json(path: str, data: Dict[str, Any]) -> bool:
    """Atomic JSON write (temp file + os.replace) — mirrors
    config_persister._write_meta so two writers of the same meta-object never
    leave it truncated ([DX-NARROW-WAIST])."""
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".geom.", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Precedence + off-screen clamp ([CXC-DVL-GEOMETRY] PRECEDENCE / FAIL-SAFE)
# ---------------------------------------------------------------------------

Display = Tuple[int, int, int, int]     # (x, y, w, h) of a display's bounds

# [CXC-DVL-GEOMETRY] first-launch placement inset: a window with NOTHING saved
# and no launcher placement opens this many px in from the top-left of the
# display's VISIBLE WORK AREA (David 2026-08-05, DEFECT-LAUNCH-BAR-CENTERED:
# "20px from the top-left corner of the SCREEN — not counting the OS X menu
# bar").  Work area, NOT raw display bounds — see ``default_origin``.
DEFAULT_ORIGIN_INSET = 20


def default_origin(work_areas: Optional[List[Display]],
                   size: Tuple[int, int],
                   inset: int = DEFAULT_ORIGIN_INSET
                   ) -> Optional[Tuple[int, int]]:
    """The fail-safe FIRST-LAUNCH origin: the top-left of the PRIMARY
    display's visible WORK AREA, inset by *inset* px on both axes.

    *work_areas* are usable bounds (the backend's
    ``get_display_usable_bounds`` / SDL_GetDisplayUsableBounds) — the screen
    MINUS the macOS menu bar and Dock, so the returned origin can never tuck a
    window under the menu bar.  ``work_areas[0]`` is the primary display.

    The inset is shrunk (never negative, never past the area's own origin) when
    the window is as large as the work area, so a window bigger than the screen
    still opens at the work area's corner rather than pushed off it.  Returns
    None when no work area is known — the caller then keeps whatever default
    the OS/runtime would have used (a missing display report is never a
    crash)."""
    if not work_areas:
        return None
    try:
        wx, wy, ww, wh = (int(v) for v in work_areas[0])
        w, h = int(size[0]), int(size[1])
        i = max(0, int(inset))
    except (TypeError, ValueError):
        return None
    return (wx + min(i, max(0, ww - w)),
            wy + min(i, max(0, wh - h)))


def resolve_geometry(persisted: Optional[WindowGeometry],
                     launcher_pos: Optional[Tuple[int, int]],
                     default_size: Tuple[int, int],
                     displays: Optional[List[Display]] = None,
                     default_pos: Optional[Tuple[int, int]] = None
                     ) -> Tuple[WindowGeometry, str]:
    """The deterministic precedence ([CXC-DVL-GEOMETRY]), returned as
    ``(geometry, source)`` where source is ``persisted`` | ``launcher`` |
    ``screen-origin`` | ``default``:

      (1) persisted (valid) wins for BOTH size and position — clamped back
          onto a visible display if it was saved off-screen;
      (2) no persisted -> size = ``default_size``, position = the launcher
          placement env (beside-the-console) when present;
      (3) no launcher placement -> ``default_pos``, the work-area top-left
          policy origin (see ``default_origin``) when the caller supplied one;
      (4) none of the above -> size = default, position unknown (the
          OS/runtime default origin, i.e. centered).

    A persisted geometry therefore OVERRIDES both the launcher's
    beside-console placement AND the first-launch policy origin — the user's
    chosen spot is honored on reopen ([CXC-DVL-GEOMETRY] PRECEDENCE)."""
    if persisted is not None and persisted.is_valid:
        geom = persisted
        if displays and geom.has_pos:
            geom = clamp_onscreen(geom, displays)
        return geom, "persisted"
    w, h = int(default_size[0]), int(default_size[1])
    if launcher_pos is not None:
        return WindowGeometry(int(launcher_pos[0]), int(launcher_pos[1]),
                              w, h), "launcher"
    if default_pos is not None:
        return WindowGeometry(int(default_pos[0]), int(default_pos[1]),
                              w, h), "screen-origin"
    return WindowGeometry(None, None, w, h), "default"


# minimum on-screen sliver so a restored window is always grabbable.
_MIN_VISIBLE = 48


def clamp_onscreen(geom: WindowGeometry,
                   displays: List[Display]) -> WindowGeometry:
    """Clamp *geom*'s origin so the window is visible on SOME display
    ([CXC-DVL-GEOMETRY] FAIL-SAFE: a position saved on a since-disconnected
    monitor is pulled back onto a connected one).

    Visible = the window's rect intersects a display by at least
    ``_MIN_VISIBLE`` px on both axes.  If it already does, it is returned
    untouched; otherwise it is re-anchored to the top-left of the display it
    is nearest to (falling back to the first display's origin), keeping its
    size.  A geometry with no position, or no displays, is returned as-is."""
    if not geom.has_pos or not displays:
        return geom
    x, y, w, h = int(geom.x), int(geom.y), int(geom.w), int(geom.h)

    def _visible_on(d: Display) -> bool:
        dx, dy, dw, dh = d
        ox = min(x + w, dx + dw) - max(x, dx)     # x-overlap
        oy = min(y + h, dy + dh) - max(y, dy)     # y-overlap
        return ox >= _MIN_VISIBLE and oy >= _MIN_VISIBLE

    if any(_visible_on(d) for d in displays):
        return geom

    # Re-anchor onto the nearest display (by origin distance), inset a little.
    def _dist(d: Display) -> float:
        dx, dy, _, _ = d
        return (x - dx) ** 2 + (y - dy) ** 2

    dx, dy, dw, dh = min(displays, key=_dist)
    nx = dx + max(0, min(40, dw - w))
    ny = dy + max(0, min(40, dh - h))
    return WindowGeometry(nx, ny, w, h)


# ---------------------------------------------------------------------------
# Write-on-change debounce ([CXC-DVL-GEOMETRY] WRITE-ON-CHANGE)
# ---------------------------------------------------------------------------

class GeometryWriteThrottle:
    """Coalesces a drag's flood of resize/move events into at most one write
    per ``min_interval`` seconds, plus one final flush on close
    ([CXC-DVL-GEOMETRY]: "a drag does not thrash the store").

    ``submit(geom, now)`` records the latest geometry and returns it iff it
    should be written NOW (the interval has elapsed since the last write),
    else None (it is held pending).  ``poll(now)`` is called each frame to
    flush a held-pending geometry once the interval passes.  ``flush()``
    returns any still-pending geometry unconditionally (the resize-end /
    window-close final write)."""

    def __init__(self, min_interval: float = 0.5) -> None:
        self.min_interval = float(min_interval)
        self._last_write_t: Optional[float] = None
        self._pending: Optional[WindowGeometry] = None

    def submit(self, geom: WindowGeometry,
               now: float) -> Optional[WindowGeometry]:
        self._pending = geom
        return self._maybe(now)

    def poll(self, now: float) -> Optional[WindowGeometry]:
        return self._maybe(now)

    def _maybe(self, now: float) -> Optional[WindowGeometry]:
        if self._pending is None:
            return None
        if (self._last_write_t is None
                or (now - self._last_write_t) >= self.min_interval):
            self._last_write_t = now
            out, self._pending = self._pending, None
            return out
        return None

    def flush(self) -> Optional[WindowGeometry]:
        out, self._pending = self._pending, None
        return out
