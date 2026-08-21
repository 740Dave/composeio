"""``hookup.wires[*]`` leaf-owned CONDITIONING keys survive the Python parser.

⛔ THE DEFECT THIS PINS WAS A SILENT DROP, NOT A CRASH.

``firmware/reactive_core/src/controller_config.cpp`` reads ``invert``,
``deadband``, ``ema`` and (since 2026-08-15) ``in_min``/``in_max`` directly off
each wire mapping in the SAME ``.compose.yaml`` this parser reads.  ``HookupWire``
kept only ``from`` / ``to`` / ``publishes`` / ``subscribes`` / ``note``.

Nothing failed.  Dropping a key that no Python code reads raises nothing, breaks
no test, and lints clean — so every Python consumer of the netlist (the MCU
config emitter above all, whose entire stated purpose is "derived so it cannot
drift from the wiring") reasoned about a wire with NO conditioning at all, and
agreed with itself perfectly while describing a different device than the one
that booted.

A fork that drops a knob is invisible to every "are these two lists the same?"
check, because the dropped knob is in NEITHER list.  These tests are the list.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.hookup import HookupError, parse_hookup

#: Every conditioning key the FIRMWARE currently reads off a wire.  If the
#: firmware gains one, it belongs here — and the carry-by-default parser means
#: it will already work when it is added.
FIRMWARE_WIRE_KEYS = ("invert", "deadband", "ema", "in_min", "in_max")


def _wire(**extra):
    block = {
        "board": "perfboard",
        "parts": [
            {"id": "mcu", "part": "esp32-c3-supermini", "at": [8, 2]},
            {"id": "potL", "part": "pot-10k", "at": [1, 12]},
        ],
        "wires": [dict({"from": "potL.wiper", "to": "mcu.GPIO0",
                        "publishes": "throttle/left"}, **extra)],
    }
    return parse_hookup(block).wires[0]


def test_every_firmware_conditioning_key_survives_the_parse():
    """The whole vocabulary, in one assertion, keyed by what the DEVICE reads."""
    w = _wire(invert=True, deadband=3, ema=0.25, in_min=96, in_max=3999)
    assert w.conditioning == {
        "invert": True, "deadband": 3, "ema": 0.25,
        "in_min": 96, "in_max": 3999,
    }
    assert set(w.conditioning) == set(FIRMWARE_WIRE_KEYS), (
        "the parser and the firmware disagree about the wire vocabulary")


def test_a_wire_with_no_conditioning_carries_an_empty_mapping():
    """Additive: the overwhelming majority of wires declare none, and they must
    serialize downstream exactly as before."""
    assert _wire().conditioning == {}
    assert _wire(note="power rail").conditioning == {}


def test_an_UNKNOWN_key_rides_through_rather_than_being_filtered():
    """⛔ CARRY-BY-DEFAULT IS THE FIX, NOT AN ALLOW-LIST.

    The drift ran firmware-first: the device grew knobs and this parser did not.
    An allow-list here would reproduce that exact failure the next time the
    firmware gains one — the key would vanish again, silently, and the person
    adding it would have no reason to look here.  So an unrecognised key is
    CARRIED, and only the known ones are type-checked.
    """
    w = _wire(some_future_knob=7)
    assert w.conditioning["some_future_knob"] == 7


@pytest.mark.parametrize("bad", [
    {"deadband": "2"},      # a string reaches yaml_min's get_int -> DEFAULT used
    {"in_min": "96"},
    {"in_max": 3.5},
    {"invert": 1},          # int, not bool
    {"deadband": True},     # bool, not int
])
def test_a_MISTYPED_conditioning_value_is_refused_loudly(bad):
    """A wrong type is worth an exception because the on-device failure is
    silent: ``deadband: "2"`` reaches the firmware's ``get_int("deadband", 2)``,
    which cannot parse it and returns its DEFAULT.  The author's declared
    denoiser is replaced by a different one, with no diagnostic on any tier."""
    with pytest.raises(HookupError):
        _wire(**bad)


def test_the_shipped_dual_throttle_scene_declares_the_safety_bands():
    """The real artifact, not a fixture — the bands are a SAFETY property and a
    scene that quietly lost them would still render and still boot."""
    from composeio.render.engine.parser import parse_compose_yaml

    scene = os.path.join(PY_ROOT, "composeio", "render", "examples",
                         "dual_throttle.compose.yaml")
    comp = parse_compose_yaml(scene)
    pub = {w.publishes: w for w in comp.hookup.wires if w.publishes}
    assert set(pub) == {"throttle/left", "throttle/right"}
    for slot, w in pub.items():
        assert w.conditioning["in_min"] == 96, slot
        assert w.conditioning["in_max"] == 3999, slot
    # the right pot is mounted backwards; the left is not
    assert pub["throttle/right"].conditioning["invert"] is True
    assert "invert" not in pub["throttle/left"].conditioning
