"""
Test 5: Reference Image Regression [CXC-R-C2]

Renders the same deterministic widget composition used to generate the
reference image, then compares the result pixel-by-pixel with tolerance
for antialiasing differences.

If this test fails, either:
  1. A rendering change was intentional -> re-run generate_reference.py
  2. A rendering change was unintentional -> investigate the regression

Spec reference: CXC-R-C2 — Backend passes drawing primitive test suite
(render reference shapes, compare output).
"""

from __future__ import annotations

import pytest

from composeio.render.skia_backend import SkiaCanvas

from .conftest import images_match, load_reference, surface_to_array
from .generate_reference import render_reference


class TestReferenceRegression:
    """Compare a fresh render against the committed reference PNG."""

    def test_phase1_regression(self) -> None:
        """Full Phase 1 widget composition matches reference image."""
        # Render fresh
        canvas = SkiaCanvas()
        render_reference(canvas)
        actual = surface_to_array(canvas)

        # Load reference
        try:
            expected = load_reference("phase1_regression")
        except FileNotFoundError:
            pytest.skip(
                "Reference image not found. Run generate_reference.py first: "
                "python tests/render/generate_reference.py"
            )

        # Compare with tolerance
        match, diff_ratio = images_match(
            actual, expected,
            tolerance=2,       # per-channel absolute diff
            max_diff_ratio=0.005,  # <0.5% pixels may differ
        )
        assert match, (
            f"Render output differs from reference. "
            f"{diff_ratio * 100:.2f}% of pixels exceed tolerance. "
            f"If the change is intentional, re-run generate_reference.py."
        )

    def test_pixel_perfect_on_same_machine(self) -> None:
        """Two sequential renders on the same machine are pixel-identical.

        This confirms that the rendering is deterministic — no random
        state, no timing-dependent output.
        """
        canvas1 = SkiaCanvas()
        render_reference(canvas1)
        arr1 = surface_to_array(canvas1)

        canvas2 = SkiaCanvas()
        render_reference(canvas2)
        arr2 = surface_to_array(canvas2)

        match, diff_ratio = images_match(
            arr1, arr2,
            tolerance=0,       # exact match expected
            max_diff_ratio=0.0,
        )
        assert match, (
            f"Two sequential renders differ: {diff_ratio * 100:.4f}% pixels. "
            f"Rendering is non-deterministic."
        )


class TestReferenceImageExists:
    """Guard: the reference image must exist for CI to be meaningful."""

    def test_reference_image_present(self) -> None:
        """reference_images/phase1_regression.png exists."""
        try:
            load_reference("phase1_regression")
        except FileNotFoundError:
            pytest.fail(
                "Reference image missing: tests/render/reference_images/"
                "phase1_regression.png. Run generate_reference.py."
            )
