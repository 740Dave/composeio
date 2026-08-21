"""
ComposeUX Deterministic Text Rasterizer — FreeType glyphs, engine-owned.

Spec: [CXC-R-DRAW-FONT] (M2).  Why this exists: loading the SAME Plex
TTF via skia Typeface.MakeFromFile is NOT enough for pixel identity —
skia-python delegates glyph rasterization to the platform scaler
(CoreText on macOS, FreeType on Linux), and their antialiasing coverage
differs (~8% of text pixels diverged macOS↔RPi5, measured 2026-07-06).

The own-every-pixel resolution: the ENGINE rasterizes glyphs itself with
freetype-py (pinned; the wheel bundles its own libfreetype, so the same
version runs on every desktop platform) using the exact settings of
cyd/fonts/generate_atlas.py — no autohinting (font's built-in hints at
>=8px, none below), grayscale AA, 72 DPI, integer 26.6->px advances.
The platform backend (Skia) only BLITS the resulting coverage bitmaps.
Text is therefore byte-identical on macOS, Linux/aarch64, and — because
generate_atlas.py bakes the CYD atlas from the same faces with the same
settings — design-identical on the CYD.

Glyph bitmaps cache per (face, size, char); colorized skia-blittable
images cache per (face, size, char, rgba) in the backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import freetype
import numpy as np

from .canvas import TextMetrics
from .fonts import classify_family, font_file_name, resolve_font_path

DPI = 72  # matches generate_atlas.py


@dataclass
class FTGlyph:
    """A rasterized glyph: 8-bit coverage bitmap + integer metrics."""
    bitmap: np.ndarray          # (h, w) uint8 alpha coverage; may be empty
    width: int
    height: int
    bearing_x: int
    bearing_y: int
    advance: int                # integer px (26.6 >> 6), like the CYD atlas


@dataclass
class FTLineMetrics:
    ascent: int
    descent: int
    line_height: int


class FTFaceRun:
    """One resolved (family, weight, size) — the unit a TEXT RUN actually needs.

    WHY ([CXC-R-DAMAGE] frame budget, measured 2026-08-11): the per-CHARACTER API
    below re-derives the face file and the 26.6 size for every glyph of every run.
    On the Terminal Widget's 151x46 grid that is ~15 500 rebuilds of an immutable
    key per frame, and it dominated the 9.0 ms terminal frame.  A run resolves the
    face ONCE and the inner loop becomes a single dict lookup keyed by character.

    Output is bit-identical by construction: the same faces, the same load flags,
    the same integer 26.6 advances, in the same order — this changes only HOW OFTEN
    the key is computed, never what is rasterized."""

    __slots__ = ("key", "line", "size", "_glyphs", "_engine", "_family",
                 "_weight", "_fallback_advance")

    def __init__(self, engine: "FreeTypeTextEngine", family: str, weight: str,
                 size: float, key: Tuple, line: FTLineMetrics) -> None:
        self._engine = engine
        self._family, self._weight, self.size = family, weight, size
        self.key = key                     # (face file name, size in 26.6)
        self.line = line
        self._glyphs: Dict[str, Optional[FTGlyph]] = {}
        # The advance the caller must use when a glyph cannot be rasterized —
        # IDENTICAL to the per-character path's fallback (see `measure`).
        self._fallback_advance = int(size * 0.5)

    def glyph(self, char: str) -> Optional[FTGlyph]:
        try:
            return self._glyphs[char]
        except KeyError:
            g = self._engine.glyph(self._family, self._weight, self.size, char)
            self._glyphs[char] = g
            return g

    def advance(self, char: str) -> int:
        g = self.glyph(char)
        return g.advance if g is not None else self._fallback_advance

    def text_width(self, text: str) -> int:
        w = 0
        for ch in text:
            w += self.advance(ch)
        return w

    def metrics(self, text: str) -> TextMetrics:
        lm = self.line
        return TextMetrics(width=float(self.text_width(text)),
                           height=float(lm.line_height),
                           ascent=float(lm.ascent),
                           descent=float(lm.descent))


class FreeTypeTextEngine:
    """Process-wide deterministic glyph rasterizer for the bundled faces."""

    def __init__(self) -> None:
        self._faces: Dict[str, freetype.Face] = {}
        self._glyphs: Dict[Tuple, FTGlyph] = {}
        self._line: Dict[Tuple, FTLineMetrics] = {}
        self._runs: Dict[Tuple, Optional[FTFaceRun]] = {}

    # -- faces -----------------------------------------------------------

    def _face_key(self, family: str, weight: str) -> str:
        return font_file_name(family, weight)     # e.g. IBMPlexSans-Bold.ttf

    def _face(self, family: str, weight: str) -> Optional[freetype.Face]:
        key = self._face_key(family, weight)
        face = self._faces.get(key)
        if face is None:
            path = resolve_font_path(family, weight)
            if path is None:
                return None
            face = freetype.Face(path)
            self._faces[key] = face
        return face

    @staticmethod
    def _size_26_6(size: float) -> int:
        return max(1, int(round(size * 64)))

    def _set_size(self, face: freetype.Face, size: float) -> None:
        face.set_char_size(0, self._size_26_6(size), DPI, DPI)

    # -- metrics ------------------------------------------------------------

    def line_metrics(self, family: str, weight: str,
                     size: float) -> Optional[FTLineMetrics]:
        key = (self._face_key(family, weight), self._size_26_6(size))
        lm = self._line.get(key)
        if lm is None:
            face = self._face(family, weight)
            if face is None:
                return None
            self._set_size(face, size)
            m = face.size
            lm = FTLineMetrics(ascent=m.ascender >> 6,
                               descent=-(m.descender >> 6),
                               line_height=m.height >> 6)
            self._line[key] = lm
        return lm

    # -- glyphs -----------------------------------------------------------------

    def glyph(self, family: str, weight: str, size: float,
              char: str) -> Optional[FTGlyph]:
        key = (self._face_key(family, weight), self._size_26_6(size), char)
        g = self._glyphs.get(key)
        if g is None:
            face = self._face(family, weight)
            if face is None:
                return None
            self._set_size(face, size)
            # generate_atlas.py settings: no autohint (built-in hints) at
            # normal sizes; no hinting at tiny sizes (TT interpreter div0)
            if size < 8:
                flags = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_HINTING
            else:
                flags = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_AUTOHINT
            face.load_char(char, flags)
            slot = face.glyph
            bm = slot.bitmap
            w, h, pitch = bm.width, bm.rows, bm.pitch
            if w > 0 and h > 0:
                buf = np.asarray(bm.buffer, dtype=np.uint8)
                rows = [buf[r * pitch: r * pitch + w] for r in range(h)]
                bitmap = np.stack(rows) if rows else np.zeros((0, 0), np.uint8)
            else:
                bitmap = np.zeros((0, 0), np.uint8)
            g = FTGlyph(bitmap=bitmap, width=w, height=h,
                        bearing_x=slot.bitmap_left,
                        bearing_y=slot.bitmap_top,
                        advance=slot.advance.x >> 6)
            self._glyphs[key] = g
        return g

    # -- measurement (integer advances -> deterministic) ----------------------------

    def measure(self, family: str, weight: str, size: float,
                text: str) -> Optional[TextMetrics]:
        run = self.run(family, weight, size)
        if run is None:
            return None
        return run.metrics(text)

    # -- runs (the per-TEXT-RUN entry point; see FTFaceRun) ------------------------

    def run(self, family: str, weight: str,
            size: float) -> Optional[FTFaceRun]:
        """The resolved face+size for a whole text run, or None when the bundled
        face cannot be loaded (the caller then falls back to native Skia text —
        the same signal `measure` returning None used to carry)."""
        key = (family, weight, size)
        try:
            return self._runs[key]
        except KeyError:
            pass
        lm = self.line_metrics(family, weight, size)
        run = None if lm is None else FTFaceRun(
            self, family, weight, size,
            (self._face_key(family, weight), self._size_26_6(size)), lm)
        self._runs[key] = run
        return run


# Singleton used by the Skia backend (and any future desktop backend)
_ENGINE: Optional[FreeTypeTextEngine] = None


def get_engine() -> FreeTypeTextEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = FreeTypeTextEngine()
    return _ENGINE
