#!/usr/bin/env python3
"""
ComposeUX Phase 1 Demo

Creates a Skia canvas and renders every Phase 1 widget onto a single
dark-themed dashboard image, then saves to PNG.

Usage:
    python -m composeio.render.demo          # from the python/ directory
    python composeio/render/demo.py          # also works

Output: composeux_phase1_demo.png in the current working directory.
"""

from __future__ import annotations

import os
import sys

# Allow running as a script from various locations
_here = os.path.dirname(os.path.abspath(__file__))
_pkg_root = os.path.abspath(os.path.join(_here, os.pardir, os.pardir))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from composeio.render.canvas import GradientStop
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets.gauge import GaugeWidget
from composeio.render.widgets.led import LEDWidget
from composeio.render.widgets.label import LabelWidget
from composeio.render.widgets.rectangle import RectangleWidget
from composeio.render.widgets.circle import CircleWidget

# ComposeUX dark theme colours
BG = (0x1E, 0x1E, 0x2E)
TEAL = (0x26, 0xA6, 0x9A)
AMBER = (0xFF, 0xA7, 0x26)
RED = (0xEF, 0x53, 0x50)
PANEL_BG = (0x28, 0x28, 0x3C)

WIDTH, HEIGHT = 960, 600


def draw_panel(canvas: SkiaCanvas, x: float, y: float,
               w: float, h: float, title: str) -> None:
    """Draw a dark panel with a title bar."""
    canvas.set_fill_color(*PANEL_BG, 230)
    canvas.fill_round_rect(x, y, w, h, 10)
    canvas.set_stroke_color(0x3A, 0x3A, 0x50)
    canvas.set_stroke_width(1)
    canvas.stroke_rect(x, y, w, h)
    canvas.set_fill_color(0xAA, 0xAA, 0xBB)
    canvas.set_font("sans-serif", 13, "medium")
    canvas.draw_text(title, x + 12, y + 20, "left")


def main() -> None:
    canvas = SkiaCanvas()
    canvas.create_surface(WIDTH, HEIGHT)

    # --- background ---
    canvas.set_fill_color(*BG)
    canvas.fill_rect(0, 0, WIDTH, HEIGHT)

    # --- title ---
    canvas.set_fill_color(*TEAL)
    canvas.set_font("sans-serif", 22, "bold")
    canvas.draw_text("ComposeUX Render Engine  -  Phase 1 Demo", WIDTH / 2, 36, "center")

    canvas.set_fill_color(0x88, 0x88, 0x99)
    canvas.set_font("sans-serif", 12, "regular")
    canvas.draw_text("All drawing goes through the abstract Canvas contract. Widgets never import Skia.",
                     WIDTH / 2, 56, "center")

    # =====================================================================
    # ROW 1: Gauges
    # =====================================================================
    draw_panel(canvas, 20, 75, 620, 240, "Gauges")

    gauge1 = GaugeWidget("temp", min_val=0, max_val=120, value=82,
                         unit="\u00b0C", label="Coolant",
                         warning_threshold=95, danger_threshold=105)
    gauge1.draw(canvas, Rect(40, 95, 190, 190))

    gauge2 = GaugeWidget("rpm", min_val=0, max_val=8000, value=3200,
                         unit="RPM", label="Engine",
                         warning_threshold=6000, danger_threshold=7000)
    gauge2.draw(canvas, Rect(240, 95, 190, 190))

    # Gauge in warning state
    gauge3 = GaugeWidget("oil", min_val=0, max_val=100, value=97,
                         unit="psi", label="Oil Pressure",
                         warning_threshold=85, danger_threshold=95)
    gauge3.draw(canvas, Rect(440, 95, 190, 190))

    # =====================================================================
    # ROW 1 right: LEDs
    # =====================================================================
    draw_panel(canvas, 660, 75, 280, 240, "LED Indicators")

    led_on = LEDWidget("led_ok", state="on", label="System OK")
    led_on.draw(canvas, Rect(680, 110, 60, 72))

    led_warn = LEDWidget("led_warn", state="warning", label="Warning")
    led_warn.draw(canvas, Rect(750, 110, 60, 72))

    led_danger = LEDWidget("led_err", state="danger", label="Error")
    led_danger.draw(canvas, Rect(820, 110, 60, 72))

    led_off = LEDWidget("led_off", state="off", label="Offline")
    led_off.draw(canvas, Rect(890, 110, 60, 72))

    # A row of custom-colour LEDs
    for i, (r, g, b) in enumerate([(0x42, 0xA5, 0xF5),   # blue
                                    (0xAB, 0x47, 0xBC),   # purple
                                    (0x66, 0xBB, 0x6A),   # green
                                    (0xFF, 0xEE, 0x58)]):  # yellow
        led = LEDWidget(f"led_c{i}", state="on", color=(r, g, b))
        led.draw(canvas, Rect(685 + i * 65, 200, 50, 56))

    # =====================================================================
    # ROW 2: Labels + shapes
    # =====================================================================
    draw_panel(canvas, 20, 340, 450, 240, "Text Labels")

    lbl_title = LabelWidget("lbl1", text="ComposeUX",
                            font_size=28, font_weight="bold",
                            color=TEAL, align="left")
    lbl_title.draw(canvas, Rect(40, 375, 400, 40))

    lbl_sub = LabelWidget("lbl2",
                          text="Own every pixel. Every platform. One contract.",
                          font_size=15, font_weight="regular",
                          color=(0x99, 0x99, 0xAA), align="left")
    lbl_sub.draw(canvas, Rect(40, 415, 400, 28))

    lbl_val = LabelWidget("lbl3", text="Engine: 3200 RPM  |  82 \u00b0C  |  97 psi",
                          font_size=14, font_weight="medium",
                          color=AMBER, align="left")
    lbl_val.draw(canvas, Rect(40, 448, 400, 24))

    lbl_status = LabelWidget("lbl4", text="STATUS: ALL SYSTEMS NOMINAL",
                             font_size=13, font_weight="bold",
                             color=TEAL, align="center")
    lbl_status.draw(canvas, Rect(40, 488, 400, 24))

    # Dim info text
    for i, line in enumerate([
        "Phase 1: canvas.py | skia_backend.py | widget.py",
        "Widgets: gauge, led, label, rectangle, circle",
        "Spec: [CXC-R-DRAW-CONTRACT] + [CXC-R-WIDGET-CONTRACT]",
    ]):
        lbl = LabelWidget(f"info{i}", text=line, font_size=11,
                          color=(0x66, 0x66, 0x78))
        lbl.draw(canvas, Rect(40, 525 + i * 18, 420, 18))

    # =====================================================================
    # ROW 2 right: Rectangles + Circles
    # =====================================================================
    draw_panel(canvas, 490, 340, 450, 240, "Shape Primitives")

    # Rectangles
    rect1 = RectangleWidget("r1", width=100, height=60,
                            fill_color=(*TEAL, 200),
                            corner_radius=8)
    rect1.draw(canvas, Rect(510, 375, 100, 60))

    rect2 = RectangleWidget("r2", width=100, height=60,
                            fill_color=(*AMBER, 180),
                            stroke_color=(*AMBER, 255),
                            stroke_width=2,
                            corner_radius=0)
    rect2.draw(canvas, Rect(625, 375, 100, 60))

    rect3 = RectangleWidget("r3", width=100, height=60,
                            fill_color=(0x1E, 0x1E, 0x2E, 255),
                            stroke_color=(*RED, 255),
                            stroke_width=2.5,
                            corner_radius=12)
    rect3.draw(canvas, Rect(740, 375, 100, 60))

    rect4 = RectangleWidget("r4", width=100, height=60,
                            fill_color=(0x42, 0xA5, 0xF5, 140),
                            corner_radius=30)
    rect4.draw(canvas, Rect(855, 375, 80, 60))

    # Circles
    circ1 = CircleWidget("c1", radius=30,
                         fill_color=(*TEAL, 200))
    circ1.draw(canvas, Rect(520, 460, 60, 60))

    circ2 = CircleWidget("c2", radius=30,
                         fill_color=(*RED, 180),
                         stroke_color=(0xFF, 0xFF, 0xFF, 100),
                         stroke_width=2)
    circ2.draw(canvas, Rect(610, 460, 60, 60))

    circ3 = CircleWidget("c3", radius=30,
                         fill_color=(*AMBER, 160))
    circ3.draw(canvas, Rect(700, 460, 60, 60))

    circ4 = CircleWidget("c4", radius=30,
                         fill_color=(0xAB, 0x47, 0xBC, 200),
                         stroke_color=(0xAB, 0x47, 0xBC, 255),
                         stroke_width=3)
    circ4.draw(canvas, Rect(790, 460, 60, 60))

    circ5 = CircleWidget("c5", radius=22,
                         fill_color=(0x66, 0xBB, 0x6A, 220))
    circ5.draw(canvas, Rect(880, 470, 44, 44))

    # --- save ---
    out_path = os.path.join(os.getcwd(), "composeux_phase1_demo.png")
    canvas.save_png(out_path)
    print(f"Demo saved to {out_path}")
    print(f"  Canvas: {WIDTH}x{HEIGHT}  |  Widgets rendered: 5 types, 22 instances")


if __name__ == "__main__":
    main()
