#!/usr/bin/env python3
"""
CYD Dashboard Cross-Platform Visual Validation

Recreates the Phase 1b dashboard from cyd/src/main.cpp using the desktop
Skia render engine. Produces a PNG for side-by-side comparison with the
physical CYD display (ESP32 + ILI9341 240x320).

Layout from main.cpp:
  - Background fill: (0x1E, 0x1E, 0x2E)
  - Panel background (rounded rect): Rect(5, 5, 230, 310)
  - Title label: Rect(0, 8, 240, 20)
  - Gauge (Coolant Temp): Rect(5, 30, 115, 115) min=40 max=120 val=82
  - Gauge (Oil Pressure): Rect(120, 30, 115, 115) min=0 max=150 val=97
  - 4 LEDs at y=155: ON, WARN, DANGER, OFF
  - Subtitle label: Rect(0, 300, 240, 18)

The C++ and Python GaugeWidget text offsets are now aligned:
  - value text Y: cy + outer_r * 0.58
  - label text Y: cy + outer_r * 0.78
No subclass override is needed — the standard GaugeWidget is used directly.

Usage:
    cd ${COMPOSEIO_HOME}/python
    python scripts/cyd_dashboard_crosscheck.py

Output:
    scripts/cyd_dashboard_crosscheck.png
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

# Allow running from the python/ directory
_here = os.path.dirname(os.path.abspath(__file__))
_pkg_root = os.path.abspath(os.path.join(_here, os.pardir))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets.gauge import GaugeWidget
from composeio.render.widgets.led import LEDWidget
from composeio.render.widgets.label import LabelWidget
from composeio.render.widgets.rectangle import RectangleWidget

# ---------------------------------------------------------------------------
# CYD display constants
# ---------------------------------------------------------------------------
CYD_SCREEN_W = 240
CYD_SCREEN_H = 320

# Background color -- matches C++ kBgR/kBgG/kBgB and Python _BG
BG = (0x1E, 0x1E, 0x2E)

# ---------------------------------------------------------------------------
# Main rendering
# ---------------------------------------------------------------------------

def draw_cyd_dashboard(canvas: SkiaCanvas) -> None:
    """Reproduce the exact CYD Phase 1b dashboard layout from main.cpp."""

    canvas.create_surface(CYD_SCREEN_W, CYD_SCREEN_H)

    # -- Background fill --
    canvas.set_fill_color(*BG)
    canvas.fill_rect(0, 0, CYD_SCREEN_W, CYD_SCREEN_H)

    # -- Panel background (dark with slight highlight, rounded) --
    # C++: panel.set_fill_color(0x22, 0x22, 0x33, 220)
    #      panel.set_stroke_color(0x44, 0x44, 0x55)
    #      panel.set_corner_radius(8.0f)
    #      panel.draw(canvas, Rect{5, 5, 230, 310})
    panel = RectangleWidget(
        "panel",
        width=230, height=310,
        fill_color=(0x22, 0x22, 0x33, 220),
        stroke_color=(0x44, 0x44, 0x55, 255),
        stroke_width=1.0,
        corner_radius=8.0,
    )
    panel.draw(canvas, Rect(5, 5, 230, 310))

    # -- Title label --
    # C++: LabelWidget("title", "ComposeUX Dashboard", 14.0f, "center", "bold")
    #      title_label.set_color(0xDD, 0xDD, 0xEE)
    #      title_label.draw(canvas, Rect{0, 8, 240, 20})
    title_label = LabelWidget(
        "title",
        text="ComposeUX Dashboard v1.7",
        font_size=14,
        font_weight="bold",
        align="center",
        color=(0xDD, 0xDD, 0xEE),
    )
    title_label.draw(canvas, Rect(0, 8, 240, 20))

    # -- Gauges: side by side --
    # C++: GaugeWidget("gauge_temp", 40, 120, 82, "C", "Coolant Temp")
    #      gauge_temp.set_warning_threshold(95)
    #      gauge_temp.set_danger_threshold(110)
    #      gauge_temp.draw(canvas, Rect{5, 30, 115, 115})
    gauge_temp = GaugeWidget(
        "gauge_temp",
        min_val=40, max_val=120, value=82,
        unit="C", label="Coolant Temp",
        warning_threshold=95, danger_threshold=110,
    )
    gauge_temp.draw(canvas, Rect(5, 30, 115, 115))

    # C++: GaugeWidget("gauge_oil", 0, 150, 97, "psi", "Oil Pressure")
    #      gauge_oil.set_warning_threshold(120)
    #      gauge_oil.set_danger_threshold(140)
    #      gauge_oil.draw(canvas, Rect{120, 30, 115, 115})
    gauge_oil = GaugeWidget(
        "gauge_oil",
        min_val=0, max_val=150, value=97,
        unit="psi", label="Oil Pressure",
        warning_threshold=120, danger_threshold=140,
    )
    gauge_oil.draw(canvas, Rect(120, 30, 115, 115))

    # -- LEDs: 4 in a row below gauges --
    # C++: kLedY=155, kLedSize=50, kLedSpacing=58, kLedX0=5
    #      led height = kLedSize + 16 = 66
    led_y = 155.0
    led_size = 50.0
    led_spacing = 58.0
    led_x0 = 5.0
    led_h = led_size + 16.0  # C++ uses kLedSize + 16.0f for rect height

    # C++: LEDWidget("led_on", LEDState::On, "ON")
    led_on = LEDWidget("led_on", state="on", label="ON")
    led_on.draw(canvas, Rect(led_x0 + 0 * led_spacing, led_y, led_size, led_h))

    # C++: LEDWidget("led_warn", LEDState::Warning, "WARN")
    led_warn = LEDWidget("led_warn", state="warning", label="WARN")
    led_warn.draw(canvas, Rect(led_x0 + 1 * led_spacing, led_y, led_size, led_h))

    # C++: LEDWidget("led_danger", LEDState::Danger, "DANGER")
    led_danger = LEDWidget("led_danger", state="danger", label="DANGER")
    led_danger.draw(canvas, Rect(led_x0 + 2 * led_spacing, led_y, led_size, led_h))

    # C++: LEDWidget("led_off", LEDState::Off, "OFF")
    led_off = LEDWidget("led_off", state="off", label="OFF")
    led_off.draw(canvas, Rect(led_x0 + 3 * led_spacing, led_y, led_size, led_h))

    # -- Subtitle --
    # C++: LabelWidget("subtitle", "Phase 1b - Band Rendering", 10.0f, "center", "regular")
    #      subtitle_label.set_color(0x88, 0x88, 0x99)
    #      subtitle_label.draw(canvas, Rect{0, CYD_SCREEN_H-20, 240, 18})
    subtitle_label = LabelWidget(
        "subtitle",
        text="Phase 1b - Band Rendering",
        font_size=10,
        font_weight="regular",
        align="center",
        color=(0x88, 0x88, 0x99),
    )
    subtitle_label.draw(canvas, Rect(0, CYD_SCREEN_H - 20, 240, 18))


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Progression output directory
    progression_dir = os.path.abspath(os.path.join(
        _pkg_root, os.pardir, "cyd", "screenshots", "progression"
    ))
    os.makedirs(progression_dir, exist_ok=True)

    # --- Render with bitmap atlas ---
    atlas_path = os.path.join(_pkg_root, "composeio", "render", "font_atlas_data.json")
    canvas_atlas = SkiaCanvas()
    if os.path.isfile(atlas_path):
        canvas_atlas.enable_bitmap_atlas(atlas_path)
        print(f"Bitmap atlas enabled: {atlas_path}")
    else:
        print(f"WARNING: Atlas not found at {atlas_path}, rendering with native text only")
    draw_cyd_dashboard(canvas_atlas)

    atlas_out = os.path.join(progression_dir, f"{timestamp}-desktop-atlas.png")
    canvas_atlas.save_png(atlas_out)
    print(f"  Atlas render: {atlas_out}")

    # --- Render with native Skia text (for comparison) ---
    canvas_native = SkiaCanvas()
    draw_cyd_dashboard(canvas_native)

    native_out = os.path.join(progression_dir, f"{timestamp}-desktop-native.png")
    canvas_native.save_png(native_out)
    print(f"  Native render: {native_out}")

    # --- Backwards-compat output ---
    compat_path = os.path.join(_here, "cyd_dashboard_crosscheck.png")
    canvas_atlas.save_png(compat_path)
    print(f"  Compat output: {compat_path}")

    # --- Run pixel comparison ---
    try:
        from pixel_comparison import pixel_equivalency
    except ImportError:
        # Also try importing from the scripts directory
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "pixel_comparison",
            os.path.join(_here, "pixel_comparison.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pixel_equivalency = mod.pixel_equivalency

    print(f"\n--- Pixel Comparison (atlas vs native) ---")
    stats = pixel_equivalency(atlas_out, native_out, output_dir=progression_dir)

    # Rename output files with timestamp prefix
    import shutil
    for name, path in stats["output_files"].items():
        basename = os.path.basename(path)
        stamped = os.path.join(progression_dir, f"{timestamp}-{basename}")
        shutil.copy2(path, stamped)

    print(f"  Identical: {stats['identical_pct']:.2f}%")
    print(f"  Max delta: {stats['max_delta']}")
    print(f"  Mean delta: {stats['mean_delta']:.4f}")

    print(f"\nCYD Dashboard Cross-Check complete")
    print(f"  Canvas: {CYD_SCREEN_W}x{CYD_SCREEN_H} (ILI9341 portrait)")
    print(f"  Widgets: panel, title, 2 gauges, 4 LEDs, subtitle")
    print(f"  Timestamp: {timestamp}")

    return atlas_out, native_out, progression_dir, timestamp


if __name__ == "__main__":
    main()
