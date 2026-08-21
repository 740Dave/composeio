"""
Test 3: Widget Contract [CXC-R-WIDGET-CONTRACT]

Each Phase 1 widget (gauge, LED, label, rectangle, circle) can be
instantiated and draw() called without error on a SkiaCanvas.

Spec reference: CXC-R-WIDGET-CONTRACT, CXC-R-PHASES Phase 1.
"""

from __future__ import annotations

import numpy as np

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect, Widget
from composeio.render.widgets.gauge import GaugeWidget
from composeio.render.widgets.led import LEDWidget
from composeio.render.widgets.label import LabelWidget
from composeio.render.widgets.rectangle import RectangleWidget
from composeio.render.widgets.circle import CircleWidget

from .conftest import surface_to_array


# =========================================================================
# Base Widget contract
# =========================================================================

class TestBaseWidgetContract:
    """The Widget base class has all spec-required methods and fields."""

    def test_widget_has_required_fields(self) -> None:
        w = Widget("test", "dummy")
        assert w.widget_id == "test"
        assert w.widget_type == "dummy"
        assert w.layout is not None
        assert w.preferred_size is None  # None before first measure()

    def test_lifecycle_stubs_callable(self) -> None:
        w = Widget("test", "dummy")
        w.on_mount()
        w.on_unmount()

    def test_interaction_stubs_callable(self) -> None:
        w = Widget("test", "dummy")
        w.hit_test(10, 10)
        w.on_tap(10, 10)
        w.on_long_press(10, 10)
        w.on_pan_start()
        w.on_pan(1, 1)
        w.on_pan_end()
        w.on_pinch(1.5, 50, 50)
        w.on_scroll(0, 10)
        w.on_key("a")
        w.on_focus()
        w.on_blur()

    def test_bus_stubs_callable(self) -> None:
        w = Widget("test", "dummy")
        w.on_bus_state("ns", "val")
        w.publish("ns", "val")

    def test_animation_stubs_callable(self) -> None:
        w = Widget("test", "dummy")
        w.animate("prop", 0, 1, 500, "ease_out")
        w.on_animation_frame("prop", 0.5)

    def test_hit_test_returns_false(self) -> None:
        """Phase 1: hit_test always returns False (Phase 3 placeholder)."""
        w = Widget("test", "dummy")
        assert w.hit_test(0, 0) is False


# =========================================================================
# GaugeWidget
# =========================================================================

class TestGaugeWidgetDraw:

    def test_instantiation(self) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=50)
        assert g.widget_type == "gauge"
        assert g.min_val == 0
        assert g.max_val == 100
        assert g.value == 50

    def test_draw_no_error(self, canvas: SkiaCanvas) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=50,
                        unit="units", label="Test Gauge")
        g.draw(canvas, Rect(10, 10, 180, 180))
        arr = surface_to_array(canvas)
        # Should have non-black pixels (gauge is visible)
        assert arr[:, :, :3].max() > 0

    def test_draw_at_min(self, canvas: SkiaCanvas) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=0)
        g.draw(canvas, Rect(10, 10, 180, 180))

    def test_draw_at_max(self, canvas: SkiaCanvas) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=100)
        g.draw(canvas, Rect(10, 10, 180, 180))

    def test_draw_in_warning(self, canvas: SkiaCanvas) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=92,
                        warning_threshold=85, danger_threshold=95)
        g.draw(canvas, Rect(10, 10, 180, 180))

    def test_draw_in_danger(self, canvas: SkiaCanvas) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=98,
                        warning_threshold=85, danger_threshold=95)
        g.draw(canvas, Rect(10, 10, 180, 180))

    def test_draw_below_min(self, canvas: SkiaCanvas) -> None:
        """Value below min_val should clamp, not crash."""
        g = GaugeWidget("g1", min_val=0, max_val=100, value=-10)
        g.draw(canvas, Rect(10, 10, 180, 180))

    def test_draw_above_max(self, canvas: SkiaCanvas) -> None:
        """Value above max_val should clamp, not crash."""
        g = GaugeWidget("g1", min_val=0, max_val=100, value=150)
        g.draw(canvas, Rect(10, 10, 180, 180))

    def test_on_bus_state(self) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=0)
        g.on_bus_state("ns", 42.5)
        assert g.value == 42.5

    def test_on_bus_state_string(self) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=0)
        g.on_bus_state("ns", "73.2")
        assert g.value == 73.2

    def test_on_bus_state_invalid(self) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=50)
        g.on_bus_state("ns", "not-a-number")
        assert g.value == 50  # unchanged


# =========================================================================
# LEDWidget
# =========================================================================

class TestLEDWidgetDraw:

    def test_instantiation(self) -> None:
        led = LEDWidget("led1", state="on", label="OK")
        assert led.widget_type == "led"
        assert led.state == "on"
        assert led.label == "OK"

    def test_draw_on(self, canvas: SkiaCanvas) -> None:
        led = LEDWidget("led1", state="on", label="Active")
        led.draw(canvas, Rect(50, 50, 60, 72))
        arr = surface_to_array(canvas)
        assert arr[:, :, :3].max() > 0

    def test_draw_off(self, canvas: SkiaCanvas) -> None:
        led = LEDWidget("led1", state="off")
        led.draw(canvas, Rect(50, 50, 60, 60))

    def test_draw_warning(self, canvas: SkiaCanvas) -> None:
        led = LEDWidget("led1", state="warning")
        led.draw(canvas, Rect(50, 50, 60, 60))

    def test_draw_danger(self, canvas: SkiaCanvas) -> None:
        led = LEDWidget("led1", state="danger")
        led.draw(canvas, Rect(50, 50, 60, 60))

    def test_draw_custom_color(self, canvas: SkiaCanvas) -> None:
        led = LEDWidget("led1", state="on", color=(0, 100, 255))
        led.draw(canvas, Rect(50, 50, 60, 60))

    def test_on_bus_state(self) -> None:
        led = LEDWidget("led1", state="off")
        led.on_bus_state("ns", "warning")
        assert led.state == "warning"

    def test_on_bus_state_non_string_ignored(self) -> None:
        led = LEDWidget("led1", state="on")
        led.on_bus_state("ns", 42)
        assert led.state == "on"  # unchanged — non-string ignored

    def test_active_color_on(self) -> None:
        led = LEDWidget("led1", state="on")
        assert led.active_color == (0x26, 0xA6, 0x9A)

    def test_active_color_off(self) -> None:
        led = LEDWidget("led1", state="off")
        assert led.active_color == (0x44, 0x44, 0x55)

    def test_active_color_custom(self) -> None:
        led = LEDWidget("led1", state="on", color=(255, 0, 0))
        assert led.active_color == (255, 0, 0)


# =========================================================================
# LabelWidget
# =========================================================================

class TestLabelWidgetDraw:

    def test_instantiation(self) -> None:
        lbl = LabelWidget("l1", text="Hello")
        assert lbl.widget_type == "label"
        assert lbl.text == "Hello"

    def test_draw_left(self, canvas: SkiaCanvas) -> None:
        lbl = LabelWidget("l1", text="Hello World", font_size=20, align="left")
        lbl.draw(canvas, Rect(10, 80, 180, 40))
        arr = surface_to_array(canvas)
        white_pixels = arr[:, :, :3].max(axis=-1) > 100
        assert white_pixels.any()

    def test_draw_center(self, canvas: SkiaCanvas) -> None:
        lbl = LabelWidget("l1", text="Center", align="center")
        lbl.draw(canvas, Rect(10, 80, 180, 40))

    def test_draw_right(self, canvas: SkiaCanvas) -> None:
        lbl = LabelWidget("l1", text="Right", align="right")
        lbl.draw(canvas, Rect(10, 80, 180, 40))

    def test_draw_custom_color(self, canvas: SkiaCanvas) -> None:
        lbl = LabelWidget("l1", text="Red", color=(255, 0, 0))
        lbl.draw(canvas, Rect(10, 80, 180, 40))
        arr = surface_to_array(canvas)
        assert arr[:, :, 0].max() > 200

    def test_draw_empty_string(self, canvas: SkiaCanvas) -> None:
        """Empty text should not crash."""
        lbl = LabelWidget("l1", text="")
        lbl.draw(canvas, Rect(10, 80, 180, 40))

    def test_on_bus_state(self) -> None:
        lbl = LabelWidget("l1", text="initial")
        lbl.on_bus_state("ns", "updated")
        assert lbl.text == "updated"

    def test_on_bus_state_numeric(self) -> None:
        lbl = LabelWidget("l1", text="")
        lbl.on_bus_state("ns", 42)
        assert lbl.text == "42"


# =========================================================================
# RectangleWidget
# =========================================================================

class TestRectangleWidgetDraw:

    def test_instantiation(self) -> None:
        r = RectangleWidget("r1", width=80, height=50)
        assert r.widget_type == "rectangle"

    def test_draw_flat(self, canvas: SkiaCanvas) -> None:
        r = RectangleWidget("r1", fill_color=(0, 128, 255, 255))
        r.draw(canvas, Rect(10, 10, 100, 60))
        arr = surface_to_array(canvas)
        assert arr[40, 60, 2] == 255

    def test_draw_rounded(self, canvas: SkiaCanvas) -> None:
        r = RectangleWidget("r1", corner_radius=15,
                            fill_color=(255, 0, 0, 255))
        r.draw(canvas, Rect(10, 10, 100, 60))
        arr = surface_to_array(canvas)
        # Center should be red
        assert arr[40, 60, 0] > 200

    def test_draw_with_stroke(self, canvas: SkiaCanvas) -> None:
        r = RectangleWidget("r1",
                            fill_color=(0, 0, 0, 0),
                            stroke_color=(0, 255, 0, 255),
                            stroke_width=3)
        r.draw(canvas, Rect(20, 20, 80, 60))
        arr = surface_to_array(canvas)
        # Edge should have green
        assert arr[20, 60, 1] > 200

    def test_draw_rounded_with_stroke(self, canvas: SkiaCanvas) -> None:
        """stroke_round_rect is called when corner_radius > 0 (gap-WIDGETS-002 fix)."""
        r = RectangleWidget("r1",
                            corner_radius=10,
                            fill_color=(0, 0, 50, 255),
                            stroke_color=(255, 255, 0, 255),
                            stroke_width=2)
        r.draw(canvas, Rect(20, 20, 80, 60))


# =========================================================================
# CircleWidget
# =========================================================================

class TestCircleWidgetDraw:

    def test_instantiation(self) -> None:
        c = CircleWidget("c1", radius=40)
        assert c.widget_type == "circle"

    def test_draw_filled(self, canvas: SkiaCanvas) -> None:
        c = CircleWidget("c1", radius=40, fill_color=(255, 0, 0, 255))
        c.draw(canvas, Rect(60, 60, 80, 80))
        arr = surface_to_array(canvas)
        assert arr[100, 100, 0] > 200

    def test_draw_with_stroke(self, canvas: SkiaCanvas) -> None:
        c = CircleWidget("c1", radius=40,
                         fill_color=(0, 0, 255, 200),
                         stroke_color=(255, 255, 255, 255),
                         stroke_width=3)
        c.draw(canvas, Rect(60, 60, 80, 80))

    def test_draw_no_stroke(self, canvas: SkiaCanvas) -> None:
        c = CircleWidget("c1", radius=30,
                         fill_color=(0, 200, 100, 255),
                         stroke_color=None)
        c.draw(canvas, Rect(70, 70, 60, 60))
