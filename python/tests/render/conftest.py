"""
Shared fixtures for ComposeUX Render Engine tests.

Provides a ready-to-use SkiaCanvas and image comparison utilities
used across all test modules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import skia

# Ensure the package root is importable when running pytest from various dirs
_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.render.skia_backend import SkiaCanvas

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REFERENCE_DIR = Path(__file__).resolve().parent / "reference_images"
REFERENCE_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_stale_child_mcp_configs():
    """Sweep any per-instance MCP config a test caused to be written
    ([CXC-IC-CHANNEL-INSTANCE]).

    A terminal test that builds `child_argv()` without ever calling `stop_session()`
    generates a real config file — the widget's own teardown is what normally removes it.
    The file NAMES AN IDENTITY, so the suite must not be the thing that leaves stale ones in
    the operator's tmpdir.  Only files this test created are removed; a config belonging to a
    LIVE hosted child (a console running beside the suite) is left alone."""
    from composeio.ptycore import child_mcp_config_dir
    d = child_mcp_config_dir()
    before = set(os.listdir(d)) if os.path.isdir(d) else set()
    yield
    if os.path.isdir(d):
        for name in set(os.listdir(d)) - before:
            try:
                os.unlink(os.path.join(d, name))
            except OSError:
                pass


@pytest.fixture
def canvas() -> SkiaCanvas:
    """A 200x200 SkiaCanvas ready for drawing."""
    c = SkiaCanvas()
    c.create_surface(200, 200)
    # Clear to black so pixel assertions have a known baseline
    c.set_fill_color(0, 0, 0, 255)
    c.fill_rect(0, 0, 200, 200)
    return c


@pytest.fixture
def small_canvas() -> SkiaCanvas:
    """A 100x100 SkiaCanvas for lightweight tests."""
    c = SkiaCanvas()
    c.create_surface(100, 100)
    c.set_fill_color(0, 0, 0, 255)
    c.fill_rect(0, 0, 100, 100)
    return c


# ---------------------------------------------------------------------------
# Image comparison helpers
# ---------------------------------------------------------------------------

def surface_to_array(canvas_obj: SkiaCanvas) -> np.ndarray:
    """Extract the canvas pixel data as a (H, W, 4) uint8 RGBA numpy array.

    Explicitly requests RGBA: N32 surfaces are BGRA on the Linux/aarch64
    skia-python wheel, so raw toarray() would be channel-swapped there.
    """
    assert canvas_obj._surface is not None, "create_surface() not called"
    image = canvas_obj._surface.makeImageSnapshot()
    return image.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType)


def save_reference(canvas_obj: SkiaCanvas, name: str) -> Path:
    """Save a canvas snapshot as a reference PNG.  Returns the file path."""
    path = REFERENCE_DIR / f"{name}.png"
    canvas_obj.save_png(str(path))
    return path


def load_reference(name: str) -> np.ndarray:
    """Load a reference PNG as a numpy array.  Raises FileNotFoundError if missing."""
    path = REFERENCE_DIR / f"{name}.png"
    if not path.exists():
        raise FileNotFoundError(f"Reference image not found: {path}")
    img = skia.Image.open(str(path))
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType)


def images_match(
    actual: np.ndarray,
    expected: np.ndarray,
    *,
    tolerance: int = 2,
    max_diff_ratio: float = 0.005,
) -> tuple[bool, float]:
    """Compare two RGBA numpy arrays with antialiasing tolerance.

    Parameters
    ----------
    actual, expected : np.ndarray
        (H, W, 4) uint8 arrays.
    tolerance : int
        Per-channel absolute difference that is considered "close enough"
        (accounts for antialiasing, sub-pixel hinting, rounding).
    max_diff_ratio : float
        Maximum fraction of total pixels allowed to exceed *tolerance*
        before the comparison fails.

    Returns
    -------
    (match, diff_ratio) : tuple[bool, float]
        *match* is True when the images are within tolerance.
        *diff_ratio* is the fraction of pixels that differ.
    """
    assert actual.shape == expected.shape, (
        f"Shape mismatch: actual {actual.shape} vs expected {expected.shape}"
    )
    diff = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
    # A pixel "differs" if ANY channel exceeds tolerance
    pixel_diffs = np.any(diff > tolerance, axis=-1)
    diff_ratio = pixel_diffs.sum() / pixel_diffs.size
    return diff_ratio <= max_diff_ratio, diff_ratio
