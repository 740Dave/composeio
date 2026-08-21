#!/usr/bin/env python3
"""
Generate the reference PNG for the regression test.

Renders a deterministic widget composition using all five Phase 1 widgets
onto a 400x300 canvas. The output is saved to reference_images/phase1_regression.png.

This script should be re-run whenever intentional rendering changes are made
to update the baseline. The test suite compares future renders against this
reference.

Usage:
    python -m tests.render.generate_reference       # from python/ dir
    python tests/render/generate_reference.py       # also works
"""

from __future__ import annotations

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_pkg_root = os.path.abspath(os.path.join(_here, os.pardir, os.pardir))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets.gauge import GaugeWidget
from composeio.render.widgets.led import LEDWidget
from composeio.render.widgets.label import LabelWidget
from composeio.render.widgets.rectangle import RectangleWidget
from composeio.render.widgets.circle import CircleWidget

WIDTH, HEIGHT = 400, 300

# Deterministic dark background
BG = (0x1E, 0x1E, 0x2E)


def render_reference(canvas: SkiaCanvas) -> None:
    """Draw a fixed composition of all five Phase 1 widget types."""
    canvas.create_surface(WIDTH, HEIGHT)

    # Background
    canvas.set_fill_color(*BG)
    canvas.fill_rect(0, 0, WIDTH, HEIGHT)

    # -- Gauge (top-left) --
    gauge = GaugeWidget("ref_gauge", min_val=0, max_val=100, value=65,
                        unit="%", label="CPU",
                        warning_threshold=80, danger_threshold=95)
    gauge.draw(canvas, Rect(10, 10, 150, 150))

    # -- LED indicators (top-right row) --
    led_on = LEDWidget("ref_led_on", state="on", label="OK")
    led_on.draw(canvas, Rect(180, 20, 50, 62))

    led_warn = LEDWidget("ref_led_warn", state="warning", label="Warn")
    led_warn.draw(canvas, Rect(240, 20, 50, 62))

    led_off = LEDWidget("ref_led_off", state="off", label="Off")
    led_off.draw(canvas, Rect(300, 20, 50, 62))

    # -- Labels (middle row) --
    lbl_title = LabelWidget("ref_lbl_title", text="ComposeUX Phase 1",
                            font_size=18, font_weight="bold",
                            color=(0x26, 0xA6, 0x9A))
    lbl_title.draw(canvas, Rect(180, 100, 200, 30))

    lbl_sub = LabelWidget("ref_lbl_sub", text="Regression Reference",
                          font_size=12, color=(0x88, 0x88, 0x99))
    lbl_sub.draw(canvas, Rect(180, 130, 200, 22))

    # -- Rectangle (bottom-left) --
    rect = RectangleWidget("ref_rect", width=120, height=60,
                           fill_color=(0x26, 0xA6, 0x9A, 180),
                           stroke_color=(0xFF, 0xA7, 0x26, 255),
                           stroke_width=2,
                           corner_radius=10)
    rect.draw(canvas, Rect(20, 180, 120, 60))

    # -- Circles (bottom-right) --
    circ1 = CircleWidget("ref_circ1", radius=25,
                         fill_color=(0xEF, 0x53, 0x50, 200))
    circ1.draw(canvas, Rect(180, 190, 50, 50))

    circ2 = CircleWidget("ref_circ2", radius=25,
                         fill_color=(0xFF, 0xA7, 0x26, 160),
                         stroke_color=(0xFF, 0xFF, 0xFF, 100),
                         stroke_width=2)
    circ2.draw(canvas, Rect(250, 190, 50, 50))

    circ3 = CircleWidget("ref_circ3", radius=20,
                         fill_color=(0x42, 0xA5, 0xF5, 220))
    circ3.draw(canvas, Rect(320, 195, 40, 40))

    # -- Bottom label --
    lbl_footer = LabelWidget("ref_footer",
                             text="5 widgets | deterministic composition",
                             font_size=10,
                             color=(0x55, 0x55, 0x66),
                             align="center")
    lbl_footer.draw(canvas, Rect(0, 270, WIDTH, 20))


def main() -> None:
    canvas = SkiaCanvas()
    render_reference(canvas)

    ref_dir = os.path.join(_here, "reference_images")
    os.makedirs(ref_dir, exist_ok=True)
    out_path = os.path.join(ref_dir, "phase1_regression.png")
    canvas.save_png(out_path)
    print(f"Reference image saved to {out_path}")
    print(f"  Canvas: {WIDTH}x{HEIGHT}")


if __name__ == "__main__":
    main()
