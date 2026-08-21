"""
[FLOW:MAKER-PROJECT] S2 — the `hookup:` foundation (schema + parts catalog).

Verifies the four acceptance points:
  1. a scene with a valid `hookup:` parses and exposes `comp.hookup`;
  2. an invalid pin OR part is REJECTED with a clear error;
  3. a hookup-less scene is byte-for-byte unchanged (comp.hookup is None,
     nothing else on the Composition moves);
  4. the catalog resolver returns esp32-c3 + pot-10k with their pin maps.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.parser import parse_widget_tree, parse_compose_yaml
from composeio.render.engine.hookup import (
    Hookup, HookupError, parse_hookup, BOARDS)
from composeio.render.engine import parts as parts_mod
from composeio.render.engine.parts import (
    resolve_part, part_pins, part_adc_pins, has_part, part_ids,
    PartNotFound, PartDef)

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples")
DUAL_THROTTLE = os.path.join(EXAMPLES, "dual_throttle.compose.yaml")


# ---------------------------------------------------------------------------
# 4. Parts catalog resolver
# ---------------------------------------------------------------------------

def test_catalog_seeds_esp32_and_pot():
    ids = part_ids()
    assert "esp32-c3" in ids
    assert "pot-10k" in ids
    assert has_part("esp32-c3") and has_part("pot-10k")


def test_esp32_pin_map_and_adc_hints():
    esp = resolve_part("esp32-c3")
    assert isinstance(esp, PartDef)
    for pin in ("3V3", "GND", "GPIO0", "GPIO1"):
        assert pin in esp.pins, f"{pin} missing from esp32-c3 pin map"
    # ADC hint for the S4 config-emitter; every adc pin is a real pin.
    assert esp.adc_pins  # non-empty
    assert set(esp.adc_pins).issubset(set(esp.pins))
    assert "GPIO0" in esp.adc_pins and esp.is_adc_pin("GPIO0")
    # multi-target refs present for S3/future-KiCad.
    assert esp.fritzing == "esp32-c3.svg"
    assert esp.kicad and "symbol" in esp.kicad


def test_pot_pin_map_and_value():
    pot = resolve_part("pot-10k")
    assert tuple(pot.pins) == ("vcc", "wiper", "gnd")
    assert pot.value == "10k"
    assert pot.fritzing == "potentiometer.svg"
    assert part_pins("pot-10k") == ("vcc", "wiper", "gnd")
    assert part_adc_pins("pot-10k") == ()  # not an MCU


def test_resolve_unknown_part_raises():
    with pytest.raises(PartNotFound) as exc:
        resolve_part("no-such-part")
    assert "no-such-part" in str(exc.value)


def test_extra_fields_passthrough():
    # fritzing_part / fritzing_note are not typed fields but must survive.
    esp = resolve_part("esp32-c3")
    assert esp.extra.get("fritzing_part") == "ESP32-C3"
    assert "fritzing_note" in esp.extra


# ---------------------------------------------------------------------------
# 1. A valid hookup parses and is exposed on the Composition
# ---------------------------------------------------------------------------

def test_valid_hookup_scene_parses_and_exposes():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    hk = comp.hookup
    assert isinstance(hk, Hookup)
    assert hk.board == "perfboard"
    # S6 additive: throttle cluster + the DY-SV5F audio module + speaker terminus.
    assert {p.id for p in hk.parts} == {"mcu", "potL", "potR", "dysv5f", "spk"}
    # placement survives as an (row, col) int pair
    assert hk.part("mcu").at == (8, 2)
    # resolved catalog def is attached to each placed part
    assert hk.part("mcu").definition.id == "esp32-c3-supermini"
    # 12 wires now (6 throttle + 6 audio); still exactly 2 carry a publishes slot
    # (the audio wires are OUTPUT/actuator wires — no `publishes:`).
    assert len(hk.wires) == 12
    assert set(hk.slots) == {"throttle/left", "throttle/right"}
    # the scene still renders its widgets — logical half untouched
    assert comp.root is not None


def test_pin_slot_map_picks_mcu_adc_side():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    mapping = comp.hookup.pin_slot_map()
    # the seam maps the MCU's ADC pin (not the pot wiper) to the slot
    assert mapping == {
        "mcu.GPIO0": "throttle/left",
        "mcu.GPIO1": "throttle/right",
    }


def test_publishing_wires_subset():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    pubs = comp.hookup.publishing_wires
    assert len(pubs) == 2
    assert all(w.publishes for w in pubs)


# ---------------------------------------------------------------------------
# 2. Invalid pin / part is rejected with a clear error
# ---------------------------------------------------------------------------

def _hookup(**over):
    base = {
        "board": "perfboard",
        "parts": [
            {"id": "mcu", "part": "esp32-c3", "at": [0, 0]},
            {"id": "potL", "part": "pot-10k", "at": [1, 0]},
        ],
        "wires": [
            {"from": "potL.wiper", "to": "mcu.GPIO0", "publishes": "t/l"},
        ],
    }
    base.update(over)
    return base


def test_bad_pin_rejected():
    bad = _hookup(wires=[{"from": "potL.wiper", "to": "mcu.GPIO99"}])
    with pytest.raises(HookupError) as exc:
        parse_hookup(bad)
    msg = str(exc.value)
    assert "GPIO99" in msg and "esp32-c3" in msg


def test_bad_pin_on_pot_rejected():
    bad = _hookup(wires=[{"from": "potL.middle", "to": "mcu.GPIO0"}])
    with pytest.raises(HookupError) as exc:
        parse_hookup(bad)
    assert "middle" in str(exc.value)


def test_unknown_part_rejected():
    bad = _hookup(parts=[{"id": "mcu", "part": "esp32-c99", "at": [0, 0]}],
                  wires=[])
    with pytest.raises(HookupError) as exc:
        parse_hookup(bad)
    assert "esp32-c99" in str(exc.value)


def test_wire_names_undeclared_part_rejected():
    bad = _hookup(wires=[{"from": "ghost.wiper", "to": "mcu.GPIO0"}])
    with pytest.raises(HookupError) as exc:
        parse_hookup(bad)
    assert "ghost" in str(exc.value)


def test_bad_board_rejected():
    with pytest.raises(HookupError):
        parse_hookup(_hookup(board="stripboard"))


def test_duplicate_part_id_rejected():
    bad = _hookup(parts=[
        {"id": "mcu", "part": "esp32-c3"},
        {"id": "mcu", "part": "pot-10k"},
    ], wires=[])
    with pytest.raises(HookupError):
        parse_hookup(bad)


def test_bad_endpoint_shape_rejected():
    with pytest.raises(HookupError):
        parse_hookup(_hookup(wires=[{"from": "potL", "to": "mcu.GPIO0"}]))


def test_bad_at_rejected():
    bad = _hookup(parts=[{"id": "mcu", "part": "esp32-c3", "at": [1, 2, 3]}],
                  wires=[])
    with pytest.raises(HookupError):
        parse_hookup(bad)


# ---------------------------------------------------------------------------
# 3. A hookup-less scene is unchanged (additive)
# ---------------------------------------------------------------------------

_PLAIN = {
    "app": {"id": "plain"},
    "design": {"title": "Plain", "width": 200, "height": 120},
    "namespaces": [{"name": "x/y", "state_engine": "binary"}],
    "widgets": [{"type": "led", "id": "l", "state": "on"}],
}


def test_hookupless_scene_has_none():
    comp = parse_widget_tree(copy.deepcopy(_PLAIN))
    assert comp.hookup is None


def test_hookupless_parse_is_stable():
    # Parsing a hookup-less scene twice yields identical structural output,
    # and adding NOTHING but a hookup does not perturb the rest.
    a = parse_widget_tree(copy.deepcopy(_PLAIN))
    with_hk = copy.deepcopy(_PLAIN)
    with_hk["hookup"] = {
        "board": "breadboard",
        "parts": [{"id": "mcu", "part": "esp32-c3"}],
        "wires": [],
    }
    b = parse_widget_tree(with_hk)
    # everything the render engine reads is untouched by the hookup block
    assert a.title == b.title
    assert a.width == b.width and a.height == b.height
    assert [n["name"] for n in a.namespaces] == [n["name"] for n in b.namespaces]
    assert a.hookup is None and isinstance(b.hookup, Hookup)


def test_publishes_optional():
    hk = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": "esp32-c3"}],
        "wires": [{"from": "mcu.3V3", "to": "mcu.GND"}],
    })
    assert hk.publishing_wires == ()
    assert hk.slots == ()


def test_at_is_optional():
    hk = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": "esp32-c3"}],
        "wires": [],
    })
    assert hk.part("mcu").at is None
