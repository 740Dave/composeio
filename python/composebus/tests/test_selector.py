"""Test dynamic namespace selectors — DP-CIO-21."""

import pytest
from composebus.selector import is_dynamic, extract_selector_names, resolve


class TestSelectorDetection:
    def test_static_namespace(self):
        assert not is_dynamic("ftl_arm")

    def test_dynamic_namespace(self):
        assert is_dynamic("${active_camera}_ptz")

    def test_multiple_selectors(self):
        assert is_dynamic("${zone}_${device}_power")


class TestExtractSelectors:
    def test_single(self):
        assert extract_selector_names("${camera}_ptz") == ["camera"]

    def test_multiple(self):
        names = extract_selector_names("${zone}_${device}_power")
        assert names == ["zone", "device"]


class TestResolve:
    def test_simple_resolution(self):
        result = resolve("${active_camera}_ptz", {"active_camera": "camera_2"})
        assert result == "camera_2_ptz"

    def test_multiple_selectors(self):
        result = resolve("${zone}_${device}_power", {
            "zone": "office",
            "device": "heater",
        })
        assert result == "office_heater_power"

    def test_missing_selector_raises(self):
        with pytest.raises(KeyError):
            resolve("${missing}_test", {})

    def test_numeric_value(self):
        result = resolve("camera_${cam_id}_ptz", {"cam_id": 3})
        assert result == "camera_3_ptz"
