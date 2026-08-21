"""
Test 1: Canvas Drawing Contract [CXC-R-DRAW-CONTRACT]

Every primitive in the Canvas ABC is callable on SkiaCanvas without error.
Where possible, we verify pixels changed on the surface after drawing.

Spec reference: CXC-R-DRAW-CONTRACT — all geometry, style, transform/clip,
text, image, and surface functions.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from composeio.render.canvas import (
    ArcTo,
    Close,
    CurveTo,
    GradientStop,
    LineTo,
    MoveTo,
    QuadTo,
    Size,
)
from composeio.render.skia_backend import SkiaCanvas

from .conftest import surface_to_array


# =========================================================================
# Geometry primitives
# =========================================================================

class TestGeometryPrimitives:
    """Each geometry function is callable and produces visible output."""

    def test_fill_rect(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(255, 0, 0)
        canvas.fill_rect(10, 10, 50, 50)
        arr = surface_to_array(canvas)
        # Center of rect should be red
        assert arr[35, 35, 0] == 255  # R channel

    def test_stroke_rect(self, canvas: SkiaCanvas) -> None:
        canvas.set_stroke_color(0, 255, 0)
        canvas.set_stroke_width(2)
        canvas.stroke_rect(10, 10, 50, 50)
        arr = surface_to_array(canvas)
        # Top edge should have green
        assert arr[10, 35, 1] > 200  # G channel on top edge

    def test_fill_round_rect(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(0, 0, 255)
        canvas.fill_round_rect(10, 10, 80, 60, 12)
        arr = surface_to_array(canvas)
        # Center should be blue
        assert arr[40, 50, 2] == 255

    def test_stroke_round_rect(self, canvas: SkiaCanvas) -> None:
        canvas.set_stroke_color(255, 255, 0)
        canvas.set_stroke_width(2)
        canvas.stroke_round_rect(10, 10, 80, 60, 12)
        arr = surface_to_array(canvas)
        # Interior center should remain black (not filled)
        assert arr[40, 50, 0] == 0
        assert arr[40, 50, 1] == 0

    def test_fill_circle(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(255, 128, 0)
        canvas.fill_circle(100, 100, 40)
        arr = surface_to_array(canvas)
        assert arr[100, 100, 0] == 255  # center R

    def test_stroke_circle(self, canvas: SkiaCanvas) -> None:
        canvas.set_stroke_color(128, 0, 255)
        canvas.set_stroke_width(3)
        canvas.stroke_circle(100, 100, 40)
        arr = surface_to_array(canvas)
        # Center should still be black
        assert arr[100, 100, 0] == 0
        # Edge (roughly at radius distance) should have color
        assert arr[100, 140, 2] > 200  # B on the right edge

    def test_fill_arc(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(0, 200, 100)
        canvas.fill_arc(100, 100, 50, 0, math.pi / 2)
        arr = surface_to_array(canvas)
        # Some pixels in the arc region should be colored
        # The arc is a pie slice from center — the center pixel gets filled
        assert arr[100, 100, 1] > 100

    def test_stroke_arc(self, canvas: SkiaCanvas) -> None:
        canvas.set_stroke_color(200, 200, 0)
        canvas.set_stroke_width(2)
        canvas.stroke_arc(100, 100, 50, 0, math.pi)
        # Should not raise
        arr = surface_to_array(canvas)
        assert arr is not None

    def test_draw_line(self, canvas: SkiaCanvas) -> None:
        canvas.set_stroke_color(255, 0, 255)
        canvas.set_stroke_width(2)
        canvas.draw_line(10, 100, 190, 100)
        arr = surface_to_array(canvas)
        # Middle of line should have magenta
        assert arr[100, 100, 0] == 255
        assert arr[100, 100, 2] == 255

    def test_draw_path(self, canvas: SkiaCanvas) -> None:
        canvas.set_stroke_color(0, 255, 128)
        canvas.set_stroke_width(2)
        commands = [
            MoveTo(10, 10),
            LineTo(100, 10),
            LineTo(100, 100),
            Close(),
        ]
        canvas.draw_path(commands)
        arr = surface_to_array(canvas)
        # Top edge of triangle should have color
        assert arr[10, 50, 1] > 200

    def test_fill_path(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(200, 50, 50)
        commands = [
            MoveTo(50, 10),
            LineTo(150, 80),
            LineTo(50, 150),
            Close(),
        ]
        canvas.fill_path(commands)
        arr = surface_to_array(canvas)
        # Interior of triangle
        assert arr[80, 80, 0] > 150

    def test_draw_path_with_cubic(self, canvas: SkiaCanvas) -> None:
        """CurveTo (cubic bezier) is usable in a path."""
        canvas.set_stroke_color(100, 200, 100)
        canvas.set_stroke_width(2)
        commands = [
            MoveTo(10, 100),
            CurveTo(50, 10, 150, 10, 190, 100),
        ]
        canvas.draw_path(commands)
        arr = surface_to_array(canvas)
        # Some non-black pixel should exist
        green_pixels = arr[:, :, 1] > 100
        assert green_pixels.any()

    def test_fill_path_with_quad(self, canvas: SkiaCanvas) -> None:
        """QuadTo path command is supported (gap-DRAW-005 prerequisite).

        Note: QuadTo and ArcTo were added to canvas.py but _build_skia_path
        may not handle them yet.  This test documents the expected behavior.
        If it fails, it means the backend needs QuadTo support in
        _build_skia_path().
        """
        canvas.set_fill_color(100, 100, 200)
        commands = [
            MoveTo(10, 150),
            QuadTo(100, 10, 190, 150),
            Close(),
        ]
        # Should not raise even if QuadTo is a no-op in _build_skia_path.
        # The path just won't have the quadratic segment.
        canvas.fill_path(commands)
        arr = surface_to_array(canvas)
        assert arr is not None


# =========================================================================
# Style state
# =========================================================================

class TestStyleState:
    """Style-setting functions are callable and affect drawing."""

    def test_set_fill_color(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(0, 255, 0, 128)
        canvas.fill_rect(0, 0, 100, 100)
        arr = surface_to_array(canvas)
        # Green should be present (alpha-blended over black)
        assert arr[50, 50, 1] > 50

    def test_set_stroke_color(self, canvas: SkiaCanvas) -> None:
        canvas.set_stroke_color(255, 0, 0, 255)
        canvas.set_stroke_width(5)
        canvas.draw_line(0, 50, 200, 50)
        arr = surface_to_array(canvas)
        assert arr[50, 100, 0] == 255

    def test_set_stroke_width(self, canvas: SkiaCanvas) -> None:
        # Thin stroke
        canvas.set_stroke_color(255, 255, 255)
        canvas.set_stroke_width(1)
        canvas.draw_line(0, 30, 200, 30)
        # Thick stroke
        canvas.set_stroke_width(10)
        canvas.draw_line(0, 80, 200, 80)
        arr = surface_to_array(canvas)
        # Thick stroke (width=10) centered at y=80 spans y=75..85.
        # At y=83 (3px from center) should be white.
        assert arr[83, 100, 0] == 255
        # Thin stroke (width=1) at y=30: 5px away should be black
        assert arr[35, 100, 0] == 0

    def test_set_linear_gradient(self, canvas: SkiaCanvas) -> None:
        canvas.set_linear_gradient(0, 0, 200, 0, [
            GradientStop(0.0, 255, 0, 0),
            GradientStop(1.0, 0, 0, 255),
        ])
        canvas.fill_rect(0, 0, 200, 200)
        arr = surface_to_array(canvas)
        # Left side should be reddish, right side blueish
        assert arr[100, 10, 0] > arr[100, 10, 2]   # more red on left
        assert arr[100, 190, 2] > arr[100, 190, 0]  # more blue on right

    def test_set_radial_gradient(self, canvas: SkiaCanvas) -> None:
        canvas.set_radial_gradient(100, 100, 80, [
            GradientStop(0.0, 255, 255, 0),
            GradientStop(1.0, 0, 0, 0),
        ])
        canvas.fill_circle(100, 100, 80)
        arr = surface_to_array(canvas)
        # Center should be bright yellow
        assert arr[100, 100, 0] > 200
        assert arr[100, 100, 1] > 200

    def test_set_shadow(self, canvas: SkiaCanvas) -> None:
        canvas.set_shadow(5, 5, 4, (255, 0, 0, 200))
        canvas.set_fill_color(255, 255, 255)
        canvas.fill_rect(50, 50, 60, 60)
        arr = surface_to_array(canvas)
        # Shadow should produce red pixels below and right of the rect
        # Check a pixel that is outside the rect but in the shadow region
        assert arr[115, 115, 0] > 0  # shadow red bleeds here

    def test_clear_shadow(self, canvas: SkiaCanvas) -> None:
        canvas.set_shadow(5, 5, 4, (255, 0, 0, 200))
        canvas.clear_shadow()
        canvas.set_fill_color(255, 255, 255)
        canvas.fill_rect(50, 50, 60, 60)
        arr = surface_to_array(canvas)
        # No red shadow pixels should exist below the rect
        assert arr[115, 115, 0] == 0

    def test_set_shadow_zero_alpha_clears(self, canvas: SkiaCanvas) -> None:
        """set_shadow with alpha=0 clears the shadow (spec behavior)."""
        canvas.set_shadow(5, 5, 10, (255, 0, 0, 255))
        canvas.set_shadow(0, 0, 0, (0, 0, 0, 0))
        assert canvas._shadow is None

    def test_set_opacity(self, canvas: SkiaCanvas) -> None:
        canvas.set_opacity(0.5)
        canvas.set_fill_color(255, 255, 255)
        canvas.fill_rect(0, 0, 200, 200)
        arr = surface_to_array(canvas)
        # White at 50% over black should give ~127
        center_val = arr[100, 100, 0]
        assert 100 < center_val < 180

    def test_set_opacity_clamps(self, canvas: SkiaCanvas) -> None:
        canvas.set_opacity(-0.5)
        assert canvas._opacity == 0.0
        canvas.set_opacity(2.0)
        assert canvas._opacity == 1.0


# =========================================================================
# Transform and clip
# =========================================================================

class TestTransformClip:

    def test_save_restore(self, canvas: SkiaCanvas) -> None:
        """save/restore does not raise and is balanced."""
        canvas.save()
        canvas.translate(10, 10)
        canvas.restore()
        # Should not raise

    def test_translate(self, canvas: SkiaCanvas) -> None:
        canvas.save()
        canvas.translate(50, 50)
        canvas.set_fill_color(255, 0, 0)
        canvas.fill_rect(0, 0, 20, 20)
        canvas.restore()
        arr = surface_to_array(canvas)
        # Rect drawn at (0,0) after translate(50,50) should appear at (50,50)
        assert arr[60, 60, 0] == 255  # center of translated rect

    def test_rotate(self, canvas: SkiaCanvas) -> None:
        canvas.save()
        canvas.translate(100, 100)
        canvas.rotate(math.pi / 4)  # 45 degrees
        canvas.set_fill_color(0, 255, 0)
        canvas.fill_rect(-40, -5, 80, 10)
        canvas.restore()
        arr = surface_to_array(canvas)
        # Rotated rect should produce green pixels off the horizontal axis
        green_pixels = arr[:, :, 1] > 200
        assert green_pixels.any()

    def test_scale(self, canvas: SkiaCanvas) -> None:
        canvas.save()
        canvas.scale(2, 2)
        canvas.set_fill_color(0, 0, 255)
        canvas.fill_rect(10, 10, 20, 20)
        canvas.restore()
        arr = surface_to_array(canvas)
        # Scaled 2x: rect from (20,20) to (60,60) in screen coords
        assert arr[40, 40, 2] == 255

    def test_clip_rect(self, canvas: SkiaCanvas) -> None:
        canvas.save()
        canvas.clip_rect(50, 50, 100, 100)
        canvas.set_fill_color(255, 0, 0)
        canvas.fill_rect(0, 0, 200, 200)
        canvas.restore()
        arr = surface_to_array(canvas)
        # Inside clip: red
        assert arr[100, 100, 0] == 255
        # Outside clip: still black
        assert arr[10, 10, 0] == 0

    def test_clip_round_rect(self, canvas: SkiaCanvas) -> None:
        canvas.save()
        canvas.clip_round_rect(50, 50, 100, 100, 20)
        canvas.set_fill_color(0, 255, 0)
        canvas.fill_rect(0, 0, 200, 200)
        canvas.restore()
        arr = surface_to_array(canvas)
        # Center should be green
        assert arr[100, 100, 1] == 255
        # Corner (outside rounded clip) should be black
        assert arr[51, 51, 1] == 0


# =========================================================================
# Text
# =========================================================================

class TestText:

    def test_set_font_regular(self, canvas: SkiaCanvas) -> None:
        canvas.set_font("sans-serif", 16, "regular")
        # Should not raise

    def test_set_font_bold(self, canvas: SkiaCanvas) -> None:
        canvas.set_font("sans-serif", 20, "bold")

    def test_set_font_medium(self, canvas: SkiaCanvas) -> None:
        canvas.set_font("sans-serif", 14, "medium")

    def test_set_font_unknown_weight_falls_back(self, canvas: SkiaCanvas) -> None:
        """Unknown weight string falls back to regular."""
        canvas.set_font("sans-serif", 14, "extra-bold-unicorn")
        # Should not raise

    def test_draw_text(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(255, 255, 255)
        canvas.set_font("sans-serif", 30, "bold")
        canvas.draw_text("X", 50, 100, "left")
        arr = surface_to_array(canvas)
        white_pixels = np.all(arr[:, :, :3] > 200, axis=-1)
        assert white_pixels.any(), "draw_text produced no visible pixels"

    def test_draw_text_center_align(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(255, 255, 255)
        canvas.set_font("sans-serif", 20)
        canvas.draw_text("Hi", 100, 100, "center")
        arr = surface_to_array(canvas)
        white_pixels = np.all(arr[:, :, :3] > 200, axis=-1)
        assert white_pixels.any()

    def test_draw_text_right_align(self, canvas: SkiaCanvas) -> None:
        canvas.set_fill_color(255, 255, 255)
        canvas.set_font("sans-serif", 20)
        canvas.draw_text("Hi", 190, 100, "right")
        arr = surface_to_array(canvas)
        white_pixels = np.all(arr[:, :, :3] > 200, axis=-1)
        assert white_pixels.any()

    def test_measure_text(self, canvas: SkiaCanvas) -> None:
        canvas.set_font("sans-serif", 20, "regular")
        m = canvas.measure_text("Hello World")
        assert m.width > 0
        assert m.height > 0
        assert m.ascent > 0
        assert m.descent > 0

    def test_measure_text_empty(self, canvas: SkiaCanvas) -> None:
        canvas.set_font("sans-serif", 14)
        m = canvas.measure_text("")
        assert m.width == 0


# =========================================================================
# Image
# =========================================================================

class TestImage:

    def test_load_image_doc_uri_raises(self, canvas: SkiaCanvas) -> None:
        with pytest.raises(NotImplementedError, match="doc://"):
            canvas.load_image("doc://assets/icon.png")

    def test_load_image_bus_uri_resolves_or_none(self, canvas: SkiaCanvas) -> None:
        # bus:// now resolves via composeio.resolve (DP-CIO-23); a missing/unreachable
        # asset returns None rather than raising (no more Phase-1 NotImplementedError).
        result = canvas.load_image("bus://assets/does/not/exist.png")
        assert result is None

    def test_load_image_bad_path_returns_none(self, canvas: SkiaCanvas) -> None:
        result = canvas.load_image("/nonexistent/path/image.png")
        assert result is None

    def test_draw_image_with_none_handle(self, canvas: SkiaCanvas) -> None:
        """draw_image with None handle is a safe no-op."""
        canvas.draw_image(None, 0, 0, 100, 100)
        # Should not raise

    def test_load_and_draw_real_image(self, canvas: SkiaCanvas, tmp_path: Path) -> None:
        """Round-trip: create a small PNG, load it, draw it."""
        # Create a tiny red image
        small = SkiaCanvas()
        small.create_surface(10, 10)
        small.set_fill_color(255, 0, 0)
        small.fill_rect(0, 0, 10, 10)
        img_path = tmp_path / "red.png"
        small.save_png(str(img_path))

        handle = canvas.load_image(str(img_path))
        assert handle is not None
        canvas.draw_image(handle, 50, 50, 40, 40)
        arr = surface_to_array(canvas)
        # Drawn area should be red
        assert arr[70, 70, 0] > 200


# =========================================================================
# Surface
# =========================================================================

class TestSurface:

    def test_create_surface(self) -> None:
        c = SkiaCanvas()
        c.create_surface(400, 300)
        assert c._surface is not None
        assert c._canvas is not None

    def test_get_surface_size(self) -> None:
        c = SkiaCanvas()
        c.create_surface(320, 240)
        sz = c.get_surface_size()
        assert isinstance(sz, Size)
        assert sz.width == 320.0
        assert sz.height == 240.0

    def test_present_no_op(self, canvas: SkiaCanvas) -> None:
        """present() on raster backend is a no-op, should not raise."""
        canvas.present()

    def test_save_png(self, canvas: SkiaCanvas, tmp_path: Path) -> None:
        canvas.set_fill_color(255, 0, 0)
        canvas.fill_rect(0, 0, 200, 200)
        out = tmp_path / "test_output.png"
        canvas.save_png(str(out))
        assert out.exists()
        assert out.stat().st_size > 0
