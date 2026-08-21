"""
`subscribes:` — the ACTUATOR half of the seam — and the two-halves cross-check.

`hookup:` had `publishes:` and no `subscribes:`, so it could express a SENSOR
and not an ACTUATOR.  Two boards worked around that (DY-SV5F: a parallel
top-level block plus a "must match the hookup wire" COMMENT; TM1637 quad panel:
`subscribes:` written on the wires informally, ignored by the parser).  A
comment is not a cross-check, so the single-source property the design lock
exists for was already false for every actuator the fleet will build.

These tests pin:
  * `subscribes:` parses, is DIRECTIONAL, and cannot be confused with publish;
  * the LOGICAL half and the PHYSICAL half of one `.compose.yaml` are asserted
    to agree on BOTH pins and namespaces;
  * `publishes:` used for an actuator's label is REFUSED, not rendered.
"""

from __future__ import annotations

import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.hookup import HookupError, parse_hookup
from composeio.render.engine.hookup_seam import (
    SeamFinding, crosscheck_seam, seam_errors)

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples")
TM1637 = os.path.join(EXAMPLES, "tm1637_quad_panel.compose.yaml")
DUAL_THROTTLE = os.path.join(EXAMPLES, "dual_throttle.compose.yaml")


def _load(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# `subscribes:` on a wire
# ---------------------------------------------------------------------------

def _panel(**wire_extra):
    return {
        "board": "perfboard",
        "parts": [
            {"id": "mcu", "part": "esp32-c3-supermini"},
            {"id": "tm1", "part": "tm1637-4digit"},
        ],
        "wires": [dict({"from": "mcu.GPIO3", "to": "tm1.DIO"}, **wire_extra)],
    }


def test_subscribes_is_parsed_and_directional():
    h = parse_hookup(_panel(subscribes="ship/fuel"))
    w = h.wires[0]
    assert w.subscribes == ("ship/fuel",)
    assert w.direction == "subscribes"
    assert h.subscribing_wires == (w,)
    assert h.publishing_wires == ()
    # the host side of the seam, with its DIRECTION attached
    assert h.pin_seam_map() == {"mcu.GPIO3": ("subscribes", ("ship/fuel",))}
    # publish-only accessors are untouched — S4's sensor contract still holds
    assert h.slots == ()
    assert h.all_slots == ("ship/fuel",)


def test_subscribes_accepts_many_namespaces_on_one_pin():
    """One UART actuator, N subscribed namespaces (the DY-SV5F shape)."""
    h = parse_hookup(_panel(subscribes=["dradis/contacts", "game/jump"]))
    assert h.wires[0].subscribes == ("dradis/contacts", "game/jump")


def test_a_wire_cannot_carry_BOTH_directions():
    with pytest.raises(HookupError, match="ONE direction"):
        parse_hookup(_panel(publishes="a/b", subscribes="c/d"))


def test_a_label_smuggled_through_the_seam_field_is_REFUSED():
    """The TM1637 panel really shipped `subscribes: "-- shared CLK (host->
    panel only)"`.  A seam field is not a caption slot: it would have become a
    namespace nothing ever publishes."""
    with pytest.raises(HookupError, match="not a bus namespace"):
        parse_hookup(_panel(subscribes="-- shared CLK (host->panel only)"))
    ok = parse_hookup(_panel(note="shared CLK (host->panel only)"))
    assert ok.wires[0].note.startswith("shared CLK")
    assert ok.wires[0].direction is None


# ---------------------------------------------------------------------------
# the cross-check: both halves of ONE .compose.yaml must agree
# ---------------------------------------------------------------------------

def _tiny_config():
    return {
        "namespaces": [{"name": "ship/fuel"}],
        "hookup": {
            "board": "perfboard",
            "parts": [
                {"id": "mcu", "part": "esp32-c3-supermini"},
                {"id": "tm1", "part": "tm1637-4digit"},
            ],
            "wires": [
                {"from": "mcu.GPIO3", "to": "tm1.DIO",
                 "subscribes": "ship/fuel"},
            ],
        },
        "tm1637_panel": {
            "displays": [{"id": "tm1", "dio_pin": "GPIO3",
                          "value_ns": "ship/fuel"}],
        },
    }


def test_agreeing_halves_produce_no_findings():
    cfg = _tiny_config()
    findings = crosscheck_seam(cfg, parse_hookup(cfg["hookup"]))
    assert findings == (), [f.message for f in findings]


def test_a_pin_the_device_half_names_but_NOTHING_WIRES_is_an_error():
    cfg = _tiny_config()
    cfg["tm1637_panel"]["displays"][0]["dio_pin"] = "GPIO7"
    errs = seam_errors(cfg, parse_hookup(cfg["hookup"]))
    assert [e.code for e in errs] == ["SEAM-PIN-NOT-WIRED"]
    assert "GPIO7" in errs[0].message
    assert "tm1637_panel.displays[0].dio_pin" == errs[0].where


def test_a_namespace_that_disagrees_with_the_wire_is_an_error():
    """The drift that surfaces as a wrong joint: the logical half says one
    namespace, the wire on that very pin says another."""
    cfg = _tiny_config()
    cfg["tm1637_panel"]["displays"][0]["value_ns"] = "ship/population"
    errs = seam_errors(cfg, parse_hookup(cfg["hookup"]))
    assert [e.code for e in errs] == ["SEAM-NS-MISMATCH"]
    assert "ship/population" in errs[0].message and "ship/fuel" in errs[0].message


def test_a_pin_that_carries_NO_namespace_at_all_is_an_error():
    cfg = _tiny_config()
    cfg["hookup"]["wires"][0].pop("subscribes")
    errs = seam_errors(cfg, parse_hookup(cfg["hookup"]))
    assert [e.code for e in errs] == ["SEAM-NS-MISSING"]


def test_PUBLISHES_used_for_an_ACTUATOR_is_REFUSED():
    """⛔ the inversion guard.  `publishes:` on the wire, a sink in the device
    half — the artifact would claim the panel SOURCES the value it displays."""
    cfg = _tiny_config()
    cfg["hookup"]["wires"][0].pop("subscribes")
    cfg["hookup"]["wires"][0]["publishes"] = "ship/fuel"
    errs = seam_errors(cfg, parse_hookup(cfg["hookup"]))
    assert [e.code for e in errs] == ["SEAM-DIRECTION-INVERTED"]
    assert "publishes" in errs[0].message and "actuator" in errs[0].message


def test_an_undeclared_namespace_is_a_WARNING_not_an_error():
    """A leaf may legitimately subscribe state another app publishes."""
    cfg = _tiny_config()
    cfg["namespaces"] = []
    findings = crosscheck_seam(cfg, parse_hookup(cfg["hookup"]))
    assert [f.code for f in findings] == ["SEAM-NS-UNDECLARED"]
    assert findings[0].severity == "warning"
    assert seam_errors(cfg, parse_hookup(cfg["hookup"])) == ()


def test_a_block_level_part_plus_subscribe_list_must_match_its_wires():
    """The DY-SV5F workaround, closed: `module: <part>` + `subscribes: [...]`
    in the device half is asserted against the wires that reach that part."""
    cfg = {
        "namespaces": [],
        "hookup": {
            "board": "perfboard",
            "parts": [
                {"id": "mcu", "part": "esp32-c3-supermini"},
                {"id": "dysv5f", "part": "dy-sv5f"},
            ],
            "wires": [
                {"from": "mcu.GPIO5", "to": "dysv5f.RXD/IO1",
                 "subscribes": ["dradis/contacts", "game/jump"]},
            ],
        },
        "audio_cover": {
            "module": "dysv5f",
            "uart": {"tx_pin": "GPIO5"},
            "subscribes": ["dradis/contacts", "game/jump"],
        },
    }
    assert seam_errors(cfg, parse_hookup(cfg["hookup"])) == ()

    cfg["audio_cover"]["subscribes"] = ["dradis/contacts", "game/red_alert"]
    errs = seam_errors(cfg, parse_hookup(cfg["hookup"]))
    assert [e.code for e in errs] == ["SEAM-PART-NS-MISMATCH"]
    assert "game/red_alert" in errs[0].message


# ---------------------------------------------------------------------------
# the two real artifacts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [TM1637, DUAL_THROTTLE])
def test_the_shipped_scenes_have_agreeing_halves(path):
    cfg = _load(path)
    errs = seam_errors(cfg, parse_hookup(cfg["hookup"]))
    assert errs == (), "\n".join(f"{e.code} {e.where}: {e.message}" for e in errs)


def test_the_parser_REFUSES_a_scene_whose_halves_disagree(tmp_path):
    """End to end: the seam check is wired into parse_compose_yaml, so a
    drifting artifact does not load at all."""
    from composeio.render.engine.parser import parse_compose_yaml
    cfg = _load(TM1637)
    cfg["tm1637_panel"]["displays"][0]["dio_pin"] = "GPIO0"   # not wired
    p = tmp_path / "drift.compose.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(HookupError, match="SEAM-PIN-NOT-WIRED"):
        parse_compose_yaml(str(p))
