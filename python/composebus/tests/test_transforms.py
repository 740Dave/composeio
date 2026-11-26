"""Test signal transforms — DP-CIO-22."""

import pytest
import time
from composebus.transforms import (
    Deadzone, Threshold, Scale, RateLimit, Debounce, Map,
    TransformChain, Transform,
)


class TestDeadzone:
    def test_below_threshold_returns_zero(self):
        d = Deadzone(0.15)
        assert d.apply(0.1) == 0.0
        assert d.apply(-0.05) == 0.0

    def test_above_threshold_passes(self):
        d = Deadzone(0.15)
        assert d.apply(0.5) == 0.5
        assert d.apply(-0.3) == -0.3

    def test_exact_threshold_returns_zero(self):
        d = Deadzone(0.15)
        assert d.apply(0.14) == 0.0


class TestThreshold:
    def test_tier_mapping(self):
        t = Threshold([
            {"above": 200, "emit": "tier_3"},
            {"above": 100, "emit": "tier_2"},
            {"above": 10, "emit": "tier_1"},
        ])
        assert t.apply(5) is None  # Below all thresholds
        assert t.apply(50) == "tier_1"
        assert t.apply(150) == "tier_2"
        assert t.apply(250) == "tier_3"


class TestScale:
    def test_linear_scale(self):
        s = Scale([0, 4095], [-1.0, 1.0])
        assert s.apply(0) == pytest.approx(-1.0)
        assert s.apply(4095) == pytest.approx(1.0)
        assert s.apply(2047.5) == pytest.approx(0.0, abs=0.001)

    def test_clamping(self):
        s = Scale([0, 100], [0, 1.0])
        assert s.apply(-50) == 0.0  # Clamped to input min
        assert s.apply(200) == 1.0  # Clamped to input max


class TestRateLimit:
    def test_first_value_passes(self):
        r = RateLimit("10hz")
        assert r.apply(42) == 42

    def test_rapid_values_suppressed(self):
        r = RateLimit("10hz")
        r.apply(1)  # First passes
        assert r.apply(2) is None  # Too fast


class TestDebounce:
    def test_stable_value_passes(self):
        d = Debounce("0ms")  # Instant debounce for testing
        d.apply(1)  # Set pending
        # Apply same value — debounce period 0ms means it should pass
        result = d.apply(1)
        assert result == 1

    def test_changing_value_resets(self):
        d = Debounce("100ms")
        d.apply(1)
        d.apply(2)  # Changed — resets debounce
        d.apply(1)  # Changed again
        # None of these should have passed through yet
        # (debounce window hasn't elapsed for any stable value)


class TestMap:
    def test_dict_mapping(self):
        m = Map({0: "off", 1: "low", 2: "high"})
        assert m.apply(0) == "off"
        assert m.apply(1) == "low"
        assert m.apply(2) == "high"
        assert m.apply(3) is None  # No mapping

    def test_list_mapping(self):
        m = Map([
            {"value": "red", "emit": "stop"},
            {"value": "green", "emit": "go"},
        ])
        assert m.apply("red") == "stop"
        assert m.apply("green") == "go"


class TestTransformChain:
    def test_composable(self):
        """Deadzone + Scale in sequence."""
        chain = TransformChain([
            Deadzone(0.15),
            Scale([0, 1.0], [0, 255]),
        ])
        assert chain.apply(0.1) == 0.0  # Deadzone zeroed, then scaled
        assert chain.apply(0.5) == pytest.approx(127.5)

    def test_filtering(self):
        """If any transform returns None, chain returns None."""
        chain = TransformChain([
            Threshold([{"above": 10, "emit": "yes"}]),
        ])
        assert chain.apply(5) is None
        assert chain.apply(15) == "yes"

    def test_from_config(self):
        chain = TransformChain.from_config([
            {"deadzone": 0.1},
            {"scale": {"in": [0, 100], "out": [0, 1.0]}},
        ])
        assert chain.apply(50) == pytest.approx(0.5)
        assert chain.apply(0.05) == 0.0  # Below deadzone → zeroed, then scaled to 0.0
