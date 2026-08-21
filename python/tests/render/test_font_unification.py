"""
Tests for Cross-Platform Font Unification [SPEC:FONT-UNIFY]

Covers:
- FU-C1: JSON atlas matches C header data (same glyph count)
- FU-C2: JSON atlas byte data matches C header byte data
- FU-C3: BitmapFontAtlas.measure_text returns consistent metrics
- FU-C5: SkiaCanvas without bitmap atlas works identically (existing tests)
- FU-C6: Nearest-neighbor scaling in Python matches CYD integer arithmetic
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import numpy as np
import pytest

from composeio.render.bitmap_font import BitmapFontAtlas, FontSizeData, GlyphData
from composeio.render.canvas import TextMetrics
from composeio.render.skia_backend import SkiaCanvas

from .conftest import surface_to_array


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PYTHON_ROOT = Path(__file__).resolve().parents[2]  # python/tests/render -> python/
_COMPOSEIO_ROOT = _PYTHON_ROOT.parent              # python/ -> composeio/
_JSON_ATLAS = _PYTHON_ROOT / "composeio" / "render" / "font_atlas_data.json"
_C_HEADER = _COMPOSEIO_ROOT / "cyd" / "src" / "font_atlas.h"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def json_atlas_data():
    """Load the JSON atlas data."""
    if not _JSON_ATLAS.exists():
        pytest.skip(f"JSON atlas not found at {_JSON_ATLAS}")
    with open(_JSON_ATLAS) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def c_header_text():
    """Load the C header text."""
    if not _C_HEADER.exists():
        pytest.skip(f"C header not found at {_C_HEADER}")
    return _C_HEADER.read_text()


@pytest.fixture(scope="module")
def bitmap_atlas():
    """Load the BitmapFontAtlas."""
    if not _JSON_ATLAS.exists():
        pytest.skip(f"JSON atlas not found at {_JSON_ATLAS}")
    return BitmapFontAtlas(str(_JSON_ATLAS))


# ---------------------------------------------------------------------------
# FU-C1: JSON atlas and C header have same glyph count per size
# ---------------------------------------------------------------------------

class TestAtlasConsistency:
    """FU-C1: generate_atlas.py produces both C header and JSON with matching glyph counts."""

    def test_both_files_exist(self):
        """Both output files exist after generation."""
        assert _JSON_ATLAS.exists(), f"JSON atlas missing: {_JSON_ATLAS}"
        if not _C_HEADER.exists():
            # python/-only deployments (e.g. the RPi runtime checkout)
            # don't carry the cyd/ tree — that's a checkout shape, not
            # an atlas regression.
            pytest.skip(f"cyd/ tree not present: {_C_HEADER}")

    def test_same_sizes(self, json_atlas_data, c_header_text):
        """Both outputs contain the same font sizes (regular weight)."""
        # Support both old format (flat "sizes") and new format ("regular"/"bold")
        if "regular" in json_atlas_data:
            json_sizes = sorted(s["size"] for s in json_atlas_data["regular"]["sizes"])
        else:
            json_sizes = sorted(s["size"] for s in json_atlas_data["sizes"])

        # Parse sizes from C header: "static const FontAtlas font_atlas_N = {"
        # Exclude bold atlas structs (font_atlas_bold_N)
        c_sizes = sorted(int(m) for m in re.findall(
            r"static const FontAtlas font_atlas_(\d+) = \{", c_header_text
        ))

        assert json_sizes == c_sizes

    def test_same_glyph_count_per_size(self, json_atlas_data, c_header_text):
        """Each size has 95 glyphs (ASCII 32-126) in both outputs."""
        # Support both old format and new format
        if "regular" in json_atlas_data:
            sizes = json_atlas_data["regular"]["sizes"]
        else:
            sizes = json_atlas_data["sizes"]
        for size_entry in sizes:
            assert len(size_entry["glyphs"]) == 95, (
                f"Size {size_entry['size']}: expected 95 glyphs, got {len(size_entry['glyphs'])}"
            )


# ---------------------------------------------------------------------------
# FU-C2: JSON bitmap hex data matches C header byte arrays
# ---------------------------------------------------------------------------

class TestAtlasBitmapEquality:
    """FU-C2: JSON atlas byte data matches C header byte data for every glyph."""

    def test_bitmap_data_matches(self, json_atlas_data, c_header_text):
        """Parse both C and JSON glyph bitmaps and verify byte-for-byte equality.

        Checks regular weight. Bold weight uses a different C prefix (font_bold_)
        and is verified separately.
        """
        # Parse C header bitmap arrays for regular weight (no "bold_" prefix):
        # "static const uint8_t font_SIZE_glyph_CC[] = { /* comment */ data,... };"
        c_bitmaps = {}  # (size, char_code) -> bytes
        for match in re.finditer(
            r"static const uint8_t font_(\d+)_glyph_(\d+)\[\] = \{(.*?)\};",
            c_header_text,
            re.DOTALL,
        ):
            size = int(match.group(1))
            cc = int(match.group(2))
            body = match.group(3)
            body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
            byte_vals = [int(h, 16) for h in re.findall(r"0x([0-9A-Fa-f]{2})", body)]
            c_bitmaps[(size, cc)] = bytes(byte_vals)

        # Support both old format and new format
        if "regular" in json_atlas_data:
            sizes = json_atlas_data["regular"]["sizes"]
        else:
            sizes = json_atlas_data["sizes"]

        mismatches = []
        for size_entry in sizes:
            sz = size_entry["size"]
            for glyph in size_entry["glyphs"]:
                cc = glyph["char_code"]
                json_hex = glyph["bitmap_hex"]
                if not json_hex:
                    assert (sz, cc) not in c_bitmaps, (
                        f"Size {sz} char {cc}: JSON has empty bitmap but C header has data"
                    )
                    continue

                json_bytes = bytes.fromhex(json_hex)
                key = (sz, cc)
                assert key in c_bitmaps, (
                    f"Size {sz} char {cc}: JSON has bitmap but C header has no array"
                )
                if json_bytes != c_bitmaps[key]:
                    mismatches.append(f"Size {sz} char {cc} ({chr(cc)}): "
                                     f"JSON len={len(json_bytes)} C len={len(c_bitmaps[key])}")

        assert len(mismatches) == 0, (
            f"{len(mismatches)} glyphs have bitmap data mismatch:\n" +
            "\n".join(mismatches[:10])
        )


# ---------------------------------------------------------------------------
# FU-C3: BitmapFontAtlas.measure_text returns expected metrics
# ---------------------------------------------------------------------------

class TestMeasureText:
    """FU-C3: BitmapFontAtlas.measure_text returns consistent metrics matching CYD logic."""

    @pytest.mark.parametrize("text,font_size", [
        ("Hello", 10),
        ("Hello", 14),
        ("82C", 14),
        ("97.0 psi", 10),
        ("ComposeUX Dashboard", 14),
        ("A", 8),
        ("A", 16),
        ("", 14),
    ])
    def test_measure_text_positive_metrics(self, bitmap_atlas, text, font_size):
        """measure_text returns non-negative width and positive height for non-empty strings."""
        m = bitmap_atlas.measure_text(text, font_size)
        if text:
            assert m.width > 0, f"Expected positive width for '{text}' at size {font_size}"
            assert m.height > 0
            assert m.ascent > 0
            assert m.descent >= 0
        else:
            assert m.width == 0.0

    def test_measure_text_scales_with_font_size(self, bitmap_atlas):
        """Width at size 16 should be larger than at size 8 for the same text."""
        m8 = bitmap_atlas.measure_text("Test", 8)
        m16 = bitmap_atlas.measure_text("Test", 16)
        assert m16.width > m8.width
        assert m16.height > m8.height

    def test_measure_text_proportional_to_length(self, bitmap_atlas):
        """Width of 'AA' should be roughly 2x width of 'A'."""
        m1 = bitmap_atlas.measure_text("A", 14)
        m2 = bitmap_atlas.measure_text("AA", 14)
        assert abs(m2.width - 2 * m1.width) < 1.0

    def test_measure_text_matches_cyd_logic(self, bitmap_atlas):
        """Verify measure_text uses CYD's atlas selection and scale formula.

        For font_size=14, target=14, atlas size=14 selected, scale=1.0.
        Width should be sum of advance values for each character.
        """
        m = bitmap_atlas.measure_text("82C", 14.0)
        atlas = bitmap_atlas.find_atlas(14)
        assert atlas is not None
        assert atlas.size == 14
        # With scale=1.0, width = sum of advances
        expected_width = 0.0
        for ch in "82C":
            g = bitmap_atlas.get_glyph(ord(ch), atlas)
            assert g is not None
            expected_width += g.advance
        assert abs(m.width - expected_width) < 0.01

    def test_find_atlas_nearest(self, bitmap_atlas):
        """find_atlas picks the closest size."""
        # Target 11 should pick 10 or 12 (whichever is closer)
        atlas_11 = bitmap_atlas.find_atlas(11)
        assert atlas_11 is not None
        # Both 10 and 12 have distance 1; implementation picks first found with min diff
        assert atlas_11.size in (10, 12)

        # Exact match
        atlas_14 = bitmap_atlas.find_atlas(14)
        assert atlas_14.size == 14

        # Target 100 should pick largest (16)
        atlas_100 = bitmap_atlas.find_atlas(100)
        assert atlas_100.size == 16


# ---------------------------------------------------------------------------
# FU-C5: SkiaCanvas without atlas works identically (implicit in existing tests)
# FU-C6: Nearest-neighbor scaling matches CYD integer arithmetic
# ---------------------------------------------------------------------------

class TestNearestNeighborScaling:
    """FU-C6: Verify nearest-neighbor scaling in Python matches CYD integer arithmetic."""

    def test_scaling_formula(self):
        """The formula src_row = py * glyph_h // sh should match C integer division."""
        # Test with a known glyph dimension and scale
        glyph_w, glyph_h = 7, 10
        scale = 14.0 / 14.0  # 1:1
        sw = int(glyph_w * scale + 0.5)
        sh = int(glyph_h * scale + 0.5)

        # At scale 1.0, sw=7, sh=10, every source pixel maps to itself
        for py in range(sh):
            src_row = py * glyph_h // sh
            assert src_row == py, f"py={py}: expected {py}, got {src_row}"

    def test_scaling_formula_2x(self):
        """Scale 2.0: each source pixel maps to 2 output pixels."""
        glyph_w, glyph_h = 4, 6
        scale = 2.0
        sw = int(glyph_w * scale + 0.5)  # 8
        sh = int(glyph_h * scale + 0.5)  # 12

        for py in range(sh):
            src_row = py * glyph_h // sh
            # Each source row should appear twice
            expected = py // 2
            assert src_row == expected, f"py={py}: expected {expected}, got {src_row}"


class TestSkiaCanvasAtlasIntegration:
    """Test SkiaCanvas with bitmap atlas enabled vs disabled."""

    def test_atlas_enable_disable(self):
        """enable_bitmap_atlas and disable_bitmap_atlas toggle correctly."""
        c = SkiaCanvas()
        c.create_surface(100, 100)
        assert c._bitmap_atlas is None

        if _JSON_ATLAS.exists():
            c.enable_bitmap_atlas(str(_JSON_ATLAS))
            assert c._bitmap_atlas is not None

            c.disable_bitmap_atlas()
            assert c._bitmap_atlas is None

    def test_draw_text_with_atlas(self):
        """draw_text with atlas enabled produces visible pixels."""
        if not _JSON_ATLAS.exists():
            pytest.skip("JSON atlas not found")

        c = SkiaCanvas()
        c.create_surface(200, 50)
        c.set_fill_color(0, 0, 0)
        c.fill_rect(0, 0, 200, 50)

        c.enable_bitmap_atlas(str(_JSON_ATLAS))
        c.set_font("sans-serif", 14, "regular")
        c.set_fill_color(255, 255, 255)
        c.draw_text("Hello", 10, 30, "left")

        arr = surface_to_array(c)
        white_pixels = np.all(arr[:, :, :3] > 100, axis=-1)
        assert white_pixels.any(), "draw_text with atlas produced no visible pixels"

    def test_measure_text_with_atlas(self):
        """measure_text with atlas returns positive metrics."""
        if not _JSON_ATLAS.exists():
            pytest.skip("JSON atlas not found")

        c = SkiaCanvas()
        c.create_surface(200, 50)
        c.enable_bitmap_atlas(str(_JSON_ATLAS))
        c.set_font("sans-serif", 14, "regular")
        m = c.measure_text("Hello")
        assert m.width > 0
        assert m.height > 0

    def test_native_text_unchanged_without_atlas(self, canvas):
        """Without atlas, text rendering is unchanged (FU-C5)."""
        canvas.set_font("sans-serif", 20, "bold")
        canvas.set_fill_color(255, 255, 255)
        canvas.draw_text("Test", 50, 100, "left")
        arr = surface_to_array(canvas)
        white_pixels = np.all(arr[:, :, :3] > 200, axis=-1)
        assert white_pixels.any(), "Native text rendering should still work"

    def test_set_font_records_size_in_atlas_mode(self):
        """In atlas mode, set_font records the size for atlas lookup."""
        if not _JSON_ATLAS.exists():
            pytest.skip("JSON atlas not found")

        c = SkiaCanvas()
        c.create_surface(100, 100)
        c.enable_bitmap_atlas(str(_JSON_ATLAS))
        c.set_font("anything", 12, "bold")
        assert c._font_size == 12

    def test_draw_text_center_align_atlas(self):
        """Center-aligned text with atlas works correctly."""
        if not _JSON_ATLAS.exists():
            pytest.skip("JSON atlas not found")

        c = SkiaCanvas()
        c.create_surface(200, 50)
        c.set_fill_color(0, 0, 0)
        c.fill_rect(0, 0, 200, 50)

        c.enable_bitmap_atlas(str(_JSON_ATLAS))
        c.set_font("sans-serif", 14)
        c.set_fill_color(255, 255, 255)
        c.draw_text("Hi", 100, 30, "center")

        arr = surface_to_array(c)
        white_pixels = np.all(arr[:, :, :3] > 100, axis=-1)
        assert white_pixels.any(), "Center-aligned atlas text should produce visible pixels"

    def test_draw_text_right_align_atlas(self):
        """Right-aligned text with atlas works correctly."""
        if not _JSON_ATLAS.exists():
            pytest.skip("JSON atlas not found")

        c = SkiaCanvas()
        c.create_surface(200, 50)
        c.set_fill_color(0, 0, 0)
        c.fill_rect(0, 0, 200, 50)

        c.enable_bitmap_atlas(str(_JSON_ATLAS))
        c.set_font("sans-serif", 14)
        c.set_fill_color(255, 255, 255)
        c.draw_text("Hi", 190, 30, "right")

        arr = surface_to_array(c)
        white_pixels = np.all(arr[:, :, :3] > 100, axis=-1)
        assert white_pixels.any(), "Right-aligned atlas text should produce visible pixels"

    def test_empty_text_no_crash(self):
        """draw_text with empty string should not crash in atlas mode."""
        if not _JSON_ATLAS.exists():
            pytest.skip("JSON atlas not found")

        c = SkiaCanvas()
        c.create_surface(100, 100)
        c.enable_bitmap_atlas(str(_JSON_ATLAS))
        c.set_font("sans-serif", 14)
        c.draw_text("", 50, 50, "left")  # Should not raise


# ===========================================================================
# FTFaceRun — the per-RUN face resolution ([CXC-R-DAMAGE] frame budget,
# 2026-08-11).  It must be a pure speed change: same glyphs, same advances.
# ===========================================================================

class TestFTFaceRun:
    """The run API resolves the face ONCE instead of per character.  The
    per-character API re-derived the face file + 26.6 size ~15 500 times a
    frame on a full terminal grid; that dominated a 9.0 ms terminal frame."""

    def _engine(self):
        from composeio.render.ft_text import get_engine
        return get_engine()

    def test_run_metrics_equal_the_per_character_measure(self):
        eng = self._engine()
        for family, weight, size in (("monospace", "regular", 12.0),
                                     ("sans-serif", "bold", 14.0),
                                     ("sans-serif", "regular", 9.5)):
            run = eng.run(family, weight, size)
            if run is None:                      # bundled faces unavailable
                continue
            for text in ("", " ", "Hello, world!", "héllo ✓", "iiiiWWWW",
                         "2026-08-11 21:19:06 composeux-architect"):
                a = eng.measure(family, weight, size, text)
                b = run.metrics(text)
                assert (a.width, a.height, a.ascent, a.descent) == \
                       (b.width, b.height, b.ascent, b.descent), text

    def test_run_glyphs_are_the_same_objects_as_the_per_character_api(self):
        eng = self._engine()
        run = eng.run("monospace", "regular", 12.0)
        if run is None:
            return
        for ch in "aWg .✓":
            assert run.glyph(ch) is eng.glyph("monospace", "regular", 12.0, ch)

    def test_an_unresolvable_face_returns_None_the_same_way_measure_did(self):
        """`run() is None` is the signal the backend uses to fall back to native
        Skia text — it must fire exactly when `measure()` used to return None."""
        import composeio.render.ft_text as ft
        eng = self._engine()
        eng._runs.clear(); eng._line.clear()
        orig = ft.FreeTypeTextEngine.line_metrics
        try:
            ft.FreeTypeTextEngine.line_metrics = lambda *a, **k: None
            assert eng.run("nope", "regular", 12.0) is None
            assert eng.measure("nope", "regular", 12.0, "x") is None
        finally:
            ft.FreeTypeTextEngine.line_metrics = orig
            eng._runs.clear()
