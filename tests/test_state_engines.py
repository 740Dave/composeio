"""Test state engines — DP-CIO-4: Separate input mechanics from state domain."""

import pytest
from composeio.state_engine import (
    BinaryEngine, TriLevelEngine, StopLightEngine,
    ContinuousEngine, EnumEngine, StateEngine,
)


class TestBinaryEngine:
    def test_default(self):
        e = BinaryEngine()
        assert e.current == "off"

    def test_advance_toggles(self):
        e = BinaryEngine()
        assert e.advance() == "on"
        assert e.advance() == "off"
        assert e.advance() == "on"

    def test_set_valid(self):
        e = BinaryEngine()
        e.set("on")
        assert e.current == "on"

    def test_set_invalid(self):
        e = BinaryEngine()
        with pytest.raises(ValueError):
            e.set("maybe")

    def test_reset(self):
        e = BinaryEngine()
        e.advance()
        e.reset()
        assert e.current == "off"


class TestTriLevelEngine:
    def test_cycle(self):
        e = TriLevelEngine()
        assert e.advance() == "half"
        assert e.advance() == "full"
        assert e.advance() == "off"

    def test_values(self):
        e = TriLevelEngine()
        assert e.values == ["off", "half", "full"]


class TestStopLightEngine:
    def test_cycle(self):
        e = StopLightEngine()
        assert e.current == "green"
        assert e.advance() == "yellow"
        assert e.advance() == "red"
        assert e.advance() == "green"


class TestContinuousEngine:
    def test_default(self):
        e = ContinuousEngine()
        assert e.current == 0

    def test_increment(self):
        e = ContinuousEngine()
        assert e.increment(10) == 10
        assert e.increment(250) == 255  # Clamped

    def test_decrement(self):
        e = ContinuousEngine()
        e.set(100)
        assert e.decrement(30) == 70
        assert e.decrement(200) == 0  # Clamped


class TestEnumEngine:
    def test_custom_values(self):
        e = EnumEngine(["standby", "warp_1", "warp_5", "warp_9", "maximum"])
        assert e.current == "standby"
        assert e.advance() == "warp_1"
        assert e.advance() == "warp_5"
        assert e.advance() == "warp_9"
        assert e.advance() == "maximum"
        assert e.advance() == "standby"  # Wraps

    def test_custom_default(self):
        e = EnumEngine(["a", "b", "c"], default="b")
        assert e.current == "b"

    def test_set_valid(self):
        e = EnumEngine(["red", "green", "blue"])
        e.set("blue")
        assert e.current == "blue"

    def test_set_invalid(self):
        e = EnumEngine(["red", "green", "blue"])
        with pytest.raises(ValueError):
            e.set("purple")


class TestFactoryMethod:
    """DP-CIO-4: State engines created from YAML config."""

    def test_from_string(self):
        e = StateEngine.from_config("binary")
        assert isinstance(e, BinaryEngine)

    def test_from_dict_enum(self):
        e = StateEngine.from_config({
            "type": "enum",
            "values": ["a", "b", "c"],
            "default": "b",
        })
        assert isinstance(e, EnumEngine)
        assert e.current == "b"

    def test_from_dict_builtin(self):
        e = StateEngine.from_config({"type": "tri_level"})
        assert isinstance(e, TriLevelEngine)

    def test_invalid(self):
        with pytest.raises(ValueError):
            StateEngine.from_config(42)
