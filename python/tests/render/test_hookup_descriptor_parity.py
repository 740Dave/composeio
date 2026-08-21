"""
Descriptor <-> catalog <-> hookup PIN PARITY — one source, three consumers.

A board descriptor (`docs/board-bringup/*.descriptor.yaml`) names pins.  A parts
catalog entry names pins.  A `hookup:` block names pins.  NOTHING asserted they
agree, so they drifted silently, and the drift surfaces as a wrong joint on a
bench.  This is the same shape as the wire-format parity corpora: one source,
N consumers, an assertion that they match.

The negative controls matter more than the passes here.  A parity check that
has never been seen to FAIL is indistinguishable from a parity check that
cannot fail, so every rule below is exercised in both directions.
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

from composeio.render.engine.hookup import parse_hookup
from composeio.render.engine.hookup_descriptor import (
    DescriptorFinding, PinContract, check_pin_parity, descriptor_dir,
    load_pin_contract, parts_with_descriptors)
from composeio.render.engine.parts import resolve_part

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples")


def _scenes():
    return sorted(
        os.path.join(EXAMPLES, f) for f in os.listdir(EXAMPLES)
        if f.endswith(".compose.yaml"))


def _hookup_scenes():
    out = []
    for path in _scenes():
        with open(path) as fh:
            cfg = yaml.safe_load(fh)
        if isinstance(cfg, dict) and cfg.get("hookup"):
            out.append((os.path.basename(path), cfg))
    return out


# ---------------------------------------------------------------------------
# the link itself
# ---------------------------------------------------------------------------

def test_the_descriptor_dir_resolves_and_is_populated():
    d = descriptor_dir()
    assert os.path.isdir(d), d
    assert any(f.endswith(".descriptor.yaml") for f in os.listdir(d))


def test_at_least_two_catalog_parts_declare_a_descriptor():
    """A check whose SUBJECT SET is empty passes vacuously forever."""
    linked = parts_with_descriptors()
    assert len(linked) >= 2, (
        f"only {len(linked)} catalog part(s) link a descriptor: {linked}")


def test_a_linked_descriptor_yields_a_machine_readable_contract():
    for part_id, desc in parts_with_descriptors().items():
        contract = load_pin_contract(desc)
        assert isinstance(contract, PinContract)
        assert contract.pins, f"{desc} yielded NO pins for '{part_id}'"


# ---------------------------------------------------------------------------
# first-run parity over every shipped artifact
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,cfg", _hookup_scenes(),
                         ids=[n for n, _ in _hookup_scenes()])
def test_every_shipped_scene_agrees_with_its_boards_descriptors(name, cfg):
    findings = check_pin_parity(parse_hookup(cfg["hookup"]))
    errs = [f for f in findings if f.severity == "error"]
    assert errs == [], "\n".join(str(f) for f in errs)


def test_the_catalog_and_descriptor_agree_pin_for_pin():
    for part_id, desc in parts_with_descriptors().items():
        contract = load_pin_contract(desc)
        catalog = set(resolve_part(part_id).pins)
        unknown = sorted(set(contract.pins) - catalog)
        assert not unknown, (
            f"{desc} names {unknown} for catalog part '{part_id}', which has "
            f"no such pin — the descriptor and the catalog have drifted")


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS — proof each rule can fail
# ---------------------------------------------------------------------------

def _contract(**over):
    base = dict(source="synthetic", pins={"GPIO0": "", "GPIO1": "", "GND": ""},
                reserved={"GPIO9": "strapping pin"},
                not_broken_out=["GPIO18"], complete=True)
    base.update(over)
    return PinContract.from_raw("synthetic.descriptor.yaml", base)


def _hookup(*wires, part="esp32-c3-supermini"):
    return parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": part},
                  {"id": "tm1", "part": "tm1637-4digit"}],
        "wires": list(wires),
    })


def test_NEGATIVE_a_hookup_pin_absent_from_the_descriptor_FAILS():
    h = _hookup({"from": "mcu.GPIO7", "to": "tm1.DIO"})
    findings = check_pin_parity(h, contracts={"mcu": _contract()})
    codes = [f.code for f in findings if f.severity == "error"]
    assert "PARITY-PIN-NOT-IN-DESCRIPTOR" in codes
    assert "GPIO7" in " ".join(f.message for f in findings)


def test_NEGATIVE_a_hookup_that_touches_a_RESERVED_pin_FAILS():
    """The fleet-wide 'never use GPIO2/8/9' — sourced from the board's own
    bring-up evidence rather than retyped into every scene's must_not."""
    h = _hookup({"from": "mcu.GPIO9", "to": "tm1.DIO"})
    findings = check_pin_parity(h, contracts={"mcu": _contract()})
    err = [f for f in findings if f.code == "PARITY-PIN-RESERVED"]
    assert err and err[0].severity == "error"
    assert "strapping pin" in err[0].message      # the WHY travels


def test_NEGATIVE_a_hookup_that_uses_a_pin_NOT_BROKEN_OUT_FAILS():
    """The live shape of this: GPIO10 IS on the SuperMini silkscreen and IS in
    the catalog, but the fleet's probe never confirmed it.  A descriptor that
    demoted it to `not_broken_out:` must stop a netlist from soldering to it —
    the catalog alone cannot, because the pin legitimately exists on the die."""
    c = _contract(pins={"GPIO0": "", "GPIO1": "", "GND": ""},
                  not_broken_out=["GPIO10"])
    h = _hookup({"from": "mcu.GPIO10", "to": "tm1.DIO"})
    findings = check_pin_parity(h, contracts={"mcu": c})
    err = [f for f in findings if f.code == "PARITY-PIN-NOT-BROKEN-OUT"]
    assert err and err[0].severity == "error"
    assert "no pad to solder to" in err[0].message


def test_NEGATIVE_a_descriptor_pin_the_catalog_lacks_FAILS():
    c = _contract(pins={"GPIO0": "", "GPIO99": "renamed in the descriptor"})
    h = _hookup({"from": "mcu.GPIO0", "to": "tm1.DIO"})
    findings = check_pin_parity(h, contracts={"mcu": c})
    err = [f for f in findings if f.code == "PARITY-DESCRIPTOR-PIN-UNKNOWN"]
    assert err and "GPIO99" in err[0].message


def test_NEGATIVE_a_COMPLETE_contract_missing_a_catalog_pin_FAILS():
    h = _hookup({"from": "mcu.GPIO0", "to": "tm1.DIO"})
    findings = check_pin_parity(h, contracts={"mcu": _contract()})
    err = [f for f in findings if f.code == "PARITY-CATALOG-PIN-UNDOCUMENTED"]
    assert err, "a `complete: true` contract must account for every catalog pin"


def test_an_INCOMPLETE_contract_downgrades_the_coverage_rule_only():
    """A partial contract (the TM1637's two-wire interface) is legitimate: it
    documents the SIGNAL pins and not the power pads.  It must still be exact
    about the pins it DOES name."""
    c = _contract(complete=False)
    h = _hookup({"from": "mcu.GPIO0", "to": "tm1.DIO"})
    findings = check_pin_parity(h, contracts={"mcu": c})
    assert [f.code for f in findings if f.severity == "error"] == []
    assert "PARITY-CATALOG-PIN-UNDOCUMENTED" in [f.code for f in findings]
    assert all(f.severity == "warning" for f in findings)


def test_the_UNCONFIRMED_third_state_exists_and_speaks():
    """Found by mutation, not by design: wiring GPIO10 on the real TM1637
    netlist drew SILENCE from the first version of this check, because a
    contract of {clean, reserved} has no way to say "this pad exists and nobody
    has confirmed it".  It is a warning — using it is a legitimate choice — but
    it must never be silent."""
    contract = load_pin_contract("esp32c3-supermini-can.descriptor.yaml")
    assert "GPIO10" in contract.caution, "the third state regressed"

    with open(os.path.join(EXAMPLES, "tm1637_quad_panel.compose.yaml")) as fh:
        cfg = yaml.safe_load(fh)
    # `to:` is scaffolding — the subject of this test is the GPIO10 `from:`.
    # It named `ls2.LV2` when the panel used BSS138 level shifters; that part
    # no longer exists since DD-TM1637-LEVEL was reversed to DIRECT CONNECT,
    # so it now names a resistor input on the same shared-CLK net.
    cfg["hookup"]["wires"].append({"from": "mcu.GPIO10", "to": "r1.A"})
    findings = check_pin_parity(parse_hookup(cfg["hookup"]))
    caution = [f for f in findings if f.code == "PARITY-PIN-CAUTION"]
    assert caution and caution[0].severity == "warning"
    assert "not in the probed set" in caution[0].message.lower()


def test_a_clean_hookup_against_a_complete_contract_is_SILENT():
    c = _contract(pins={p: "" for p in resolve_part("esp32-c3-supermini").pins},
                  reserved={"GPIO9": "strapping pin"}, not_broken_out=[])
    h = _hookup({"from": "mcu.GPIO0", "to": "tm1.DIO"})
    assert check_pin_parity(h, contracts={"mcu": c}) == ()
