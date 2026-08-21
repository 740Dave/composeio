"""
Bitmap Font Atlas for Cross-Platform Text Rendering

Loads the JSON font atlas produced by generate_atlas.py and provides
glyph lookup and text measurement that exactly matches the CYD ESP32
canvas_esp32.cpp implementation.

Spec: [FONT-UNIFY-RENDERER]
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .canvas import TextMetrics


@dataclass
class GlyphData:
    """Decoded glyph from the atlas."""
    char_code: int
    width: int
    height: int
    bearing_x: int
    bearing_y: int
    advance: int
    bitmap: bytes  # decoded 8-bit alpha bitmap (width * height bytes)


@dataclass
class FontSizeData:
    """All glyphs and metrics for a single atlas size."""
    size: int
    ascent: int
    descent: int
    line_height: int
    glyphs: dict[int, GlyphData]  # char_code -> GlyphData


class BitmapFontAtlas:
    """Loads a JSON font atlas and provides glyph lookup + text measurement.

    The measure_text() and find_atlas() logic matches the CYD's
    ESP32Canvas implementation exactly (canvas_esp32.cpp lines 1342-1370).

    Supports both old format (flat {"sizes": [...]}) and new format
    ({"regular": {"sizes": [...]}, "bold": {"sizes": [...]}}).
    """

    def __init__(self, json_path: str) -> None:
        with open(json_path) as f:
            data = json.load(f)

        # New format: {"regular": {"sizes": [...]}, "bold": {"sizes": [...]}}
        # Old format: {"sizes": [...]}
        if "regular" in data and "bold" in data:
            regular_sizes = data["regular"]["sizes"]
            bold_sizes = data["bold"]["sizes"]
        else:
            # Backwards compatibility: old flat format has no bold
            regular_sizes = data["sizes"]
            bold_sizes = data["sizes"]  # fall back to regular

        self._sizes: list[FontSizeData] = self._load_sizes(regular_sizes)
        self._bold_sizes: list[FontSizeData] = self._load_sizes(bold_sizes)

    @staticmethod
    def _load_sizes(size_entries: list) -> list[FontSizeData]:
        """Parse a list of size entries into FontSizeData objects."""
        result: list[FontSizeData] = []
        for size_entry in size_entries:
            glyphs: dict[int, GlyphData] = {}
            for g in size_entry["glyphs"]:
                bitmap_hex = g["bitmap_hex"]
                bitmap = bytes.fromhex(bitmap_hex) if bitmap_hex else b""
                glyphs[g["char_code"]] = GlyphData(
                    char_code=g["char_code"],
                    width=g["width"],
                    height=g["height"],
                    bearing_x=g["bearing_x"],
                    bearing_y=g["bearing_y"],
                    advance=g["advance"],
                    bitmap=bitmap,
                )
            result.append(FontSizeData(
                size=size_entry["size"],
                ascent=size_entry["ascent"],
                descent=size_entry["descent"],
                line_height=size_entry["line_height"],
                glyphs=glyphs,
            ))
        return result

    def find_atlas(self, target_size: int, bold: bool = False) -> Optional[FontSizeData]:
        """Find the best matching atlas size for the requested size.

        Matches CYD font_atlas_find() / font_atlas_find_bold(): picks the
        atlas with the smallest absolute difference from target_size.
        """
        sizes = self._bold_sizes if bold else self._sizes
        if not sizes:
            return None
        best = sizes[0]
        best_diff = abs(target_size - best.size)
        for s in sizes[1:]:
            diff = abs(target_size - s.size)
            if diff < best_diff:
                best = s
                best_diff = diff
        return best

    def measure_text(self, text: str, font_size: float, bold: bool = False) -> TextMetrics:
        """Measure text using the atlas, matching CYD ESP32Canvas::measure_text.

        Logic from canvas_esp32.cpp lines 1342-1370:
        1. target = int(font_size + 0.5)
        2. atlas = font_atlas_find(target) or font_atlas_find_bold(target)
        3. scale = font_size / atlas.size
        4. width = sum(glyph.advance * scale) for each char
        5. height = atlas.line_height * scale
        6. ascent = atlas.ascent * scale
        7. descent = atlas.descent * scale
        """
        target = int(font_size + 0.5)
        atlas = self.find_atlas(target, bold=bold)
        if atlas is None:
            return TextMetrics(width=0.0, height=0.0, ascent=0.0, descent=0.0)

        scale = font_size / float(atlas.size)
        width = 0.0
        for ch in text:
            char_code = ord(ch)
            glyph = self.get_glyph(char_code, atlas)
            if glyph is not None:
                width += glyph.advance * scale
            else:
                width += atlas.size * 0.5 * scale  # fallback width
        height = atlas.line_height * scale
        ascent = atlas.ascent * scale
        descent = atlas.descent * scale
        return TextMetrics(width=width, height=height, ascent=ascent, descent=descent)

    def get_glyph(self, char_code: int, atlas: FontSizeData) -> Optional[GlyphData]:
        """Get glyph for a character from an atlas.

        Matches CYD font_atlas_glyph(): returns None if char_code
        is outside ASCII 32-126 range.
        """
        if char_code < 32 or char_code > 126:
            return None
        return atlas.glyphs.get(char_code)
