"""
Golden pixel tests for the M1-added widgets (PlaceholderWidget, ImageWidget).

Guardrail p5: every widget on the contract is pixel-verified against the
Skia reference before it counts as done.  Goldens live in
reference_images/ and regenerate via CXC_REGEN_GOLDENS=1:

    CXC_REGEN_GOLDENS=1 python -m pytest tests/render/test_new_widgets_golden.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import ImageWidget, PlaceholderWidget

from .conftest import REFERENCE_DIR, images_match, load_reference, surface_to_array


def _canvas(w=240, h=160):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x1E, 0x1E, 0x2E)
    c.fill_rect(0, 0, w, h)
    return c


def _render_placeholder():
    c = _canvas()
    w = PlaceholderWidget("ph", original_type="ButtonWidget",
                          label="FTL Arm")
    w.draw(c, Rect(30, 30, 180, 100))
    return c


def _render_image_fallback():
    c = _canvas()
    w = ImageWidget("img", src="bus://assets/nonexistent/missing.png")
    w._tried = True          # force the fallback path (no network in tests)
    w.draw(c, Rect(40, 20, 160, 120))
    return c


_SCENES = {
    "widget_placeholder": _render_placeholder,
    "widget_image_fallback": _render_image_fallback,
}


@pytest.mark.parametrize("name", sorted(_SCENES))
def test_new_widget_matches_golden(name):
    c = _SCENES[name]()
    actual = surface_to_array(c)
    path = REFERENCE_DIR / f"{name}.png"
    if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
        c.save_png(str(path))
        pytest.skip(f"golden (re)generated: {path}")
    expected = load_reference(name)
    match, ratio = images_match(actual, expected, tolerance=2,
                                max_diff_ratio=0.005)
    assert match, f"{name}: {ratio * 100:.2f}% of pixels exceed tolerance"
