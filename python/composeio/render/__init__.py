"""
ComposeUX Render Engine

Layer 1: drawing contract (canvas.py) + Skia backend (skia_backend.py,
         raster reference) + live window (window.py, M0).
Layer 2: platform-independent engine (engine/ — layout, hit-test, focus,
         animation, assets, .compose.yaml parser, runtime; M1).
Layer 3: widgets on the contract (widgets/).

Run a composition live:
    python -m composeio.render.run_compose path/to/app.compose.yaml

Conformance-check a backend against the Skia goldens:
    python -m composeio.render.conformance --verify

SkiaWindow (SDL2) is imported lazily — headless use of the engine does
not require SDL to be installed.
"""

from .canvas import Canvas, GradientStop, PathCommand, Size, TextMetrics
from .skia_backend import SkiaCanvas
from .widget import Constraints, Rect, Widget
from .widgets import (
    CircleWidget,
    GaugeWidget,
    ImageWidget,
    LabelWidget,
    LEDWidget,
    PlaceholderWidget,
    RectangleWidget,
)

__all__ = [
    # Contract
    "Canvas",
    "GradientStop",
    "PathCommand",
    "Size",
    "TextMetrics",
    # Backend
    "SkiaCanvas",
    # Widget system
    "Constraints",
    "Rect",
    "Widget",
    # Widgets on the contract
    "GaugeWidget",
    "LEDWidget",
    "LabelWidget",
    "RectangleWidget",
    "CircleWidget",
    "PlaceholderWidget",
    "ImageWidget",
]
