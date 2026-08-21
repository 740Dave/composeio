"""
Test 4: Widget measure()

Each Phase 1 widget returns a valid Size from measure().
The Size must have positive width and height, and must respect constraints.

Spec reference: CXC-R-WIDGET-CONTRACT — measure(constraints) returns
preferred size given max width/height constraints.
"""

from __future__ import annotations

from composeio.render.canvas import Size
from composeio.render.widget import Constraints
from composeio.render.widgets.gauge import GaugeWidget
from composeio.render.widgets.led import LEDWidget
from composeio.render.widgets.label import LabelWidget
from composeio.render.widgets.rectangle import RectangleWidget
from composeio.render.widgets.circle import CircleWidget


def _assert_valid_size(size: Size, constraints: Constraints) -> None:
    """Helper: size is a Size with positive dimensions within constraints."""
    assert isinstance(size, Size)
    assert size.width > 0
    assert size.height > 0
    assert size.width <= constraints.max_width + 0.01  # float tolerance
    assert size.height <= constraints.max_height + 0.01


# =========================================================================
# GaugeWidget measure
# =========================================================================

class TestGaugeMeasure:

    def test_unconstrained(self) -> None:
        g = GaugeWidget("g1", min_val=0, max_val=100, value=50)
        c = Constraints(max_width=1000, max_height=1000)
        size = g.measure(c)
        _assert_valid_size(size, c)
        # Default preferred size is 200x200
        assert size.width == 200
        assert size.height == 200

    def test_constrained_width(self) -> None:
        g = GaugeWidget("g1")
        c = Constraints(max_width=120, max_height=1000)
        size = g.measure(c)
        _assert_valid_size(size, c)
        # Should be 120x120 (square, constrained by width)
        assert size.width == 120

    def test_constrained_height(self) -> None:
        g = GaugeWidget("g1")
        c = Constraints(max_width=1000, max_height=80)
        size = g.measure(c)
        assert size.width == 80
        assert size.height == 80

    def test_small_constraint(self) -> None:
        g = GaugeWidget("g1")
        c = Constraints(max_width=40, max_height=40)
        size = g.measure(c)
        _assert_valid_size(size, c)


# =========================================================================
# LEDWidget measure
# =========================================================================

class TestLEDMeasure:

    def test_unconstrained_no_label(self) -> None:
        led = LEDWidget("led1", state="on")
        c = Constraints(max_width=500, max_height=500)
        size = led.measure(c)
        _assert_valid_size(size, c)
        # Default 60x60
        assert size.width == 60
        assert size.height == 60

    def test_unconstrained_with_label(self) -> None:
        led = LEDWidget("led1", state="on", label="Status")
        c = Constraints(max_width=500, max_height=500)
        size = led.measure(c)
        _assert_valid_size(size, c)
        # With label: height should be larger (60 + 18 = 78)
        assert size.height > size.width

    def test_constrained(self) -> None:
        led = LEDWidget("led1", state="on")
        c = Constraints(max_width=30, max_height=30)
        size = led.measure(c)
        _assert_valid_size(size, c)


# =========================================================================
# LabelWidget measure
# =========================================================================

class TestLabelMeasure:

    def test_normal_text(self) -> None:
        lbl = LabelWidget("l1", text="Hello World", font_size=16)
        c = Constraints(max_width=500, max_height=500)
        size = lbl.measure(c)
        _assert_valid_size(size, c)
        # Width proportional to text length
        assert size.width > 0

    def test_empty_text(self) -> None:
        lbl = LabelWidget("l1", text="")
        c = Constraints(max_width=500, max_height=500)
        size = lbl.measure(c)
        assert isinstance(size, Size)
        assert size.width == 0
        assert size.height > 0  # height comes from font, not text

    def test_constrained_width(self) -> None:
        lbl = LabelWidget("l1", text="A very long label that exceeds constraints")
        c = Constraints(max_width=100, max_height=500)
        size = lbl.measure(c)
        assert size.width <= 100

    def test_different_font_sizes(self) -> None:
        lbl_small = LabelWidget("l1", text="Text", font_size=10)
        lbl_large = LabelWidget("l2", text="Text", font_size=30)
        c = Constraints(max_width=500, max_height=500)
        s_small = lbl_small.measure(c)
        s_large = lbl_large.measure(c)
        # Larger font should produce larger size
        assert s_large.width > s_small.width
        assert s_large.height > s_small.height


# =========================================================================
# RectangleWidget measure
# =========================================================================

class TestRectangleMeasure:

    def test_default_size(self) -> None:
        r = RectangleWidget("r1", width=100, height=60)
        c = Constraints(max_width=500, max_height=500)
        size = r.measure(c)
        _assert_valid_size(size, c)
        assert size.width == 100
        assert size.height == 60

    def test_constrained_width(self) -> None:
        r = RectangleWidget("r1", width=200, height=60)
        c = Constraints(max_width=80, max_height=500)
        size = r.measure(c)
        assert size.width == 80

    def test_constrained_height(self) -> None:
        r = RectangleWidget("r1", width=100, height=200)
        c = Constraints(max_width=500, max_height=50)
        size = r.measure(c)
        assert size.height == 50


# =========================================================================
# CircleWidget measure
# =========================================================================

class TestCircleMeasure:

    def test_default_size(self) -> None:
        c_widget = CircleWidget("c1", radius=30)
        c = Constraints(max_width=500, max_height=500)
        size = c_widget.measure(c)
        _assert_valid_size(size, c)
        assert size.width == 60  # diameter
        assert size.height == 60

    def test_constrained(self) -> None:
        c_widget = CircleWidget("c1", radius=100)
        c = Constraints(max_width=80, max_height=80)
        size = c_widget.measure(c)
        assert size.width == 80
        assert size.height == 80
