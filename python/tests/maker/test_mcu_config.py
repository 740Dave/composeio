"""
[FLOW:MAKER-PROJECT] S4 — the MCU config-emitter.

Proves step (e) falls out of the YAML:
  1. dual_throttle -> the correct pin->slot sampling config (GPIO0->throttle/
     left, GPIO1->throttle/right; analog; BANDED ADC 96..3999 -> engine 0..100 %);
  2. the emitted MCU pin matches S2's ``pin_slot_map()`` (single-source);
  3. a hookup with NO ``publishes:`` wires -> an empty/no-op config;
  4. a composition with NO ``hookup:`` -> an empty/no-op config;
  5. the serialized shapes (`sensors:` YAML block / doc_store JSON) round-trip.
"""

from __future__ import annotations

import copy
import json
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.parser import parse_compose_yaml, parse_widget_tree
from composeio.maker import (
    emit_mcu_config, McuConfig, SensorSpec, Normalize,
    DEFAULT_SAMPLE_HZ, ADC_FULL_SCALE,
)

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples")
DUAL_THROTTLE = os.path.join(EXAMPLES, "dual_throttle.compose.yaml")


# ---------------------------------------------------------------------------
# 1 + 2. dual_throttle — the correct sampling config, matching pin_slot_map
# ---------------------------------------------------------------------------

def test_dual_throttle_emits_two_analog_sensors():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    cfg = emit_mcu_config(comp)

    assert isinstance(cfg, McuConfig)
    assert cfg.device == "dual-throttle"
    assert cfg.board == "esp32-c3-supermini"
    assert not cfg.is_noop
    assert len(cfg.sensors) == 2

    by_slot = {s.publish: s for s in cfg.sensors}
    assert set(by_slot) == {"throttle/left", "throttle/right"}

    left = by_slot["throttle/left"]
    assert left.pin == "GPIO0"
    assert left.mode == "analog"
    assert left.engine == "continuous_0_100"
    assert left.sensor == "pot-10k"          # source peripheral, from the wire
    assert left.sample_hz == DEFAULT_SAMPLE_HZ
    # ADC raw (12-bit) -> engine domain, the normalization surfaced as config.
    # The BANDED span, not the full ADC span: in_min/in_max are the safety
    # bands declared on the wire.  [CXC-HAL-CONDITION-BANDS]
    assert left.normalize == Normalize(96.0, 3999.0, 0.0, 100.0)

    right = by_slot["throttle/right"]
    assert right.pin == "GPIO1"
    assert right.mode == "analog"
    assert right.normalize.out_max == 100.0

    # no derivation warnings on the clean reference project
    assert cfg.warnings == ()


def test_emitted_pin_matches_pin_slot_map():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    cfg = emit_mcu_config(comp)

    # S2's single-source contract: {"mcu.GPIO0": "throttle/left", ...}
    pin_slot = comp.hookup.pin_slot_map()
    emitted = {f"mcu.{s.pin}": s.publish for s in cfg.sensors}
    assert emitted == pin_slot == {
        "mcu.GPIO0": "throttle/left",
        "mcu.GPIO1": "throttle/right",
    }


# ---------------------------------------------------------------------------
# 3. no publishing wires -> no-op
# ---------------------------------------------------------------------------

_POWER_ONLY = {
    "app": {"id": "power-only"},
    "design": {"title": "Power", "width": 100, "height": 100},
    "namespaces": [],
    "hookup": {
        "board": "perfboard",
        "parts": [
            {"id": "mcu", "part": "esp32-c3"},
            {"id": "potL", "part": "pot-10k"},
        ],
        "wires": [
            {"from": "potL.vcc", "to": "mcu.3V3"},
            {"from": "potL.gnd", "to": "mcu.GND"},
        ],
    },
    "widgets": [],
}


def test_no_publishing_wires_is_noop():
    comp = parse_widget_tree(copy.deepcopy(_POWER_ONLY))
    cfg = emit_mcu_config(comp)
    assert cfg.is_noop
    assert cfg.sensors == ()
    assert cfg.to_dict()["sensors"] == []


# ---------------------------------------------------------------------------
# 4. no hookup block at all -> no-op
# ---------------------------------------------------------------------------

_NO_HOOKUP = {
    "app": {"id": "plain"},
    "design": {"title": "Plain", "width": 100, "height": 100},
    "namespaces": [{"name": "x/y", "state_engine": "binary"}],
    "widgets": [],
}


def test_no_hookup_is_noop():
    comp = parse_widget_tree(copy.deepcopy(_NO_HOOKUP))
    assert comp.hookup is None
    cfg = emit_mcu_config(comp)
    assert cfg.is_noop
    assert cfg.board is None
    assert cfg.to_dict()["sensors"] == []


# ---------------------------------------------------------------------------
# 5. serialization shapes
# ---------------------------------------------------------------------------

def test_yaml_block_round_trips():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    cfg = emit_mcu_config(comp)
    doc = yaml.safe_load(cfg.to_yaml())

    assert doc["device"] == "dual-throttle"
    assert doc["board"] == "esp32-c3-supermini"
    assert isinstance(doc["sensors"], list) and len(doc["sensors"]) == 2

    s0 = doc["sensors"][0]
    assert s0["publish"] == "throttle/left"
    assert s0["pin"] == "GPIO0"
    assert s0["mode"] == "analog"
    assert s0["engine"] == "continuous_0_100"
    # whole floats emit as ints for a clean config
    assert s0["normalize"] == {"in_min": 96, "in_max": 3999,
                               "out_min": 0, "out_max": 100}
    # The conditioning knobs survive serialization — this emitter used to drop
    # invert/deadband/ema entirely, so the emitted config described a device
    # that did not exist.  `step` (output-scale level quantization, wire-declared
    # step: 10) is a leaf-owned conditioning knob too and must round-trip with them.
    assert s0["conditioning"] == {"deadband": 1, "step": 10}
    assert s0["sample_hz"] == DEFAULT_SAMPLE_HZ


def test_json_shape():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    cfg = emit_mcu_config(comp)
    doc = json.loads(cfg.to_json())
    assert [s["publish"] for s in doc["sensors"]] == [
        "throttle/left", "throttle/right"]


# ---------------------------------------------------------------------------
# overrides + digital fallback
# ---------------------------------------------------------------------------

def test_adc_full_scale_override():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    cfg = emit_mcu_config(comp, adc_full_scale=1023, sample_hz=50)
    left = next(s for s in cfg.sensors if s.publish == "throttle/left")
    # The scene's band (96..3999) is calibrated for a 12-bit ADC.  Against a
    # 10-bit one it is nonsense, and the emitter says so OUT LOUD rather than
    # emitting an in_max beyond full scale: the device applies the identical
    # fallback, so a silent pass here would hide a REAL loss of the
    # rail-reachability guarantee behind a plausible-looking descriptor.
    assert left.normalize == Normalize(0.0, 1023.0, 0.0, 100.0)
    assert left.sample_hz == 50
    assert any("out-of-range band" in w for w in cfg.warnings), cfg.warnings


_DIGITAL = {
    "app": {"id": "door"},
    "design": {"title": "Door", "width": 100, "height": 100},
    "namespaces": [{"name": "door/state", "state_engine": "binary"}],
    "hookup": {
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": "esp32-c3"}],
        # a binary-engine slot on an ADC pin -> threshold (digital) read.
        "wires": [{"from": "mcu.GPIO4", "to": "mcu.GND",
                   "publishes": "door/state"}],
    },
    "widgets": [],
}


def test_binary_engine_emits_digital_sensor():
    comp = parse_widget_tree(copy.deepcopy(_DIGITAL))
    cfg = emit_mcu_config(comp)
    assert len(cfg.sensors) == 1
    s = cfg.sensors[0]
    assert s.pin == "GPIO4"
    assert s.mode == "digital"
    assert s.engine == "binary"
    assert s.normalize is None
    assert s.debounce_ms == 20
    # the non-linear-engine-on-ADC-pin note is surfaced, not swallowed.
    assert any("threshold" in w for w in cfg.warnings)


def test_esp32_adc_full_scale_registered():
    # the catalog default the emitter relies on (12-bit ESP32-C3).
    assert ADC_FULL_SCALE["esp32-c3"] == 4095
