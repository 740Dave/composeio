"""Host-runnable (broker-free) tests for the `relay_actuator:` block —
the break-before-make relay-bank OUTPUT actuator ([CXC-HAL-ACTUATE]).

Mirrors the on-device lowering asserted in
firmware/reactive_core/src/host_test_main.cpp; keeps the Python parser and the
C++ RelayActuatorSpec in step.
"""

import os

import pytest

from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.engine.relay_actuator import (
    NONE_PIN,
    RelayActuatorError,
    parse_gpio,
    parse_relay_actuator,
)

REPO = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
OFFICE = os.path.join(REPO, "compose_projects", "office_devices")


# --- parse_gpio -------------------------------------------------------------

def test_parse_gpio_forms():
    assert parse_gpio("mcu.GPIO33") == 33
    assert parse_gpio("GPIO25") == 25
    assert parse_gpio("26") == 26
    assert parse_gpio("none") == NONE_PIN
    assert parse_gpio(None) == NONE_PIN
    assert parse_gpio("-1") == NONE_PIN


def test_parse_gpio_rejects_rails():
    with pytest.raises(RelayActuatorError):
        parse_gpio("mcu.3V3")
    with pytest.raises(RelayActuatorError):
        parse_gpio("GND")


# --- parse_relay_actuator ---------------------------------------------------

FAN_BLOCK = {
    "subscribes": "office_fan/speed",
    "active_high": True,
    "dead_time_ms": 80,
    "default": "off",
    "pins": ["mcu.GPIO32", "mcu.GPIO33", "mcu.GPIO25", "mcu.GPIO26"],
    "states": [
        {"value": "off", "pin": "none"},
        {"value": "low", "pin": "mcu.GPIO33"},
        {"value": "medium", "pin": "mcu.GPIO25"},
        {"value": "high", "pin": "mcu.GPIO26"},
    ],
}


def test_relay_actuator_lowers():
    ra = parse_relay_actuator(FAN_BLOCK)
    assert ra.subscribes == "office_fan/speed"
    assert ra.active_high is True
    assert ra.dead_time_ms == 80
    assert ra.default == "off"
    assert ra.pins == (32, 33, 25, 26)
    assert ra.values == ("off", "low", "medium", "high")
    assert ra.pin_for("off") == NONE_PIN
    assert ra.pin_for("low") == 33
    assert ra.pin_for("medium") == 25
    assert ra.pin_for("high") == 26
    assert ra.pin_for("turbo") is None  # unknown -> None (withheld on device)


def test_state_pin_must_be_owned():
    bad = dict(FAN_BLOCK)
    bad["pins"] = ["GPIO33"]
    bad["states"] = [
        {"value": "low", "pin": "GPIO33"},
        {"value": "high", "pin": "GPIO26"},  # not owned
    ]
    with pytest.raises(RelayActuatorError, match="not one of the owned pins"):
        parse_relay_actuator(bad)


def test_requires_subscribes_and_states():
    with pytest.raises(RelayActuatorError):
        parse_relay_actuator({"pins": ["GPIO33"], "states": [{"value": "low", "pin": "GPIO33"}]})
    with pytest.raises(RelayActuatorError):
        parse_relay_actuator({"subscribes": "x/speed", "pins": ["GPIO33"]})


def test_default_must_be_declared_state():
    bad = dict(FAN_BLOCK)
    bad["default"] = "turbo"
    with pytest.raises(RelayActuatorError, match="default"):
        parse_relay_actuator(bad)


def test_duplicate_state_value_rejected():
    bad = dict(FAN_BLOCK)
    bad["states"] = FAN_BLOCK["states"] + [{"value": "low", "pin": "mcu.GPIO32"}]
    with pytest.raises(RelayActuatorError, match="duplicate"):
        parse_relay_actuator(bad)


# --- the real fetched configs parse end-to-end ------------------------------

@pytest.mark.parametrize("device", ["office_fan", "office_hepa"])
def test_real_compose_yaml_parses(device):
    path = os.path.join(OFFICE, device, f"{device}.compose.yaml")
    comp = parse_compose_yaml(path)
    ra = comp.relay_actuator
    assert ra is not None, "relay_actuator hung on the Composition"
    assert ra.subscribes == f"{device}/speed"
    assert ra.pins == (32, 33, 25, 26)
    assert ra.pin_for("off") == NONE_PIN
    assert ra.pin_for("low") == 33
    assert ra.pin_for("medium") == 25
    assert ra.pin_for("high") == 26
    assert ra.dead_time_ms == 80
    assert ra.active_high is True
    assert ra.default == "off"
