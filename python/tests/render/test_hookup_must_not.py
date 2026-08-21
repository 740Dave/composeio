"""
`must_not:` — the NEGATIVE SPACE of a netlist, and `facts:` — its provenance.

A netlist can only express connections that EXIST.  In a hookup guide the
load-bearing knowledge is frequently about connections that must NOT exist
("leave the whole -B header unconnected", "never use GPIO2/8/9", "do not add
pull-ups").  Today that lives in prose beside the diagram, where a diagram
cannot draw it and no machine can check it.

These tests pin BOTH halves of the design:
  * ENFORCED clauses (`unwired`, `distinct_nets`, `absent_parts`) are checked
    against the wire list at PARSE time and REFUSE a violating netlist;
  * STATED clauses (`stated:`) are carried, labelled unenforceable, and
    rendered — never silently promoted to "checked".
  * a clause that binds to NOTHING is an ERROR, because a constraint that
    cannot fire is worse than no constraint at all (it reads as protection).
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

from composeio.render.engine.hookup import (
    Hookup, HookupError, MustNot, HookupFact, parse_hookup)


def _base(**extra):
    """A minimal two-TM1637 panel — the shape every case below mutates."""
    block = {
        "board": "perfboard",
        "parts": [
            {"id": "mcu", "part": "esp32-c3-supermini"},
            {"id": "tm1", "part": "tm1637-4digit"},
            {"id": "tm2", "part": "tm1637-4digit"},
        ],
        "wires": [
            {"from": "mcu.GPIO3", "to": "tm1.DIO"},
            {"from": "mcu.GPIO4", "to": "tm2.DIO"},
            {"from": "mcu.GPIO6", "to": "tm1.CLK"},
            {"from": "tm1.CLK", "to": "tm2.CLK"},
        ],
    }
    block.update(extra)
    return block


# ---------------------------------------------------------------------------
# unwired: — "never use GPIO2/8/9", "leave the entire -B header unconnected"
# ---------------------------------------------------------------------------

def test_unwired_passes_when_nothing_touches_the_forbidden_pins():
    h = parse_hookup(_base(must_not=[
        {"id": "no-boot-straps", "why": "ESP32-C3 strapping pins",
         "unwired": ["mcu.GPIO2", "mcu.GPIO8", "mcu.GPIO9"]},
    ]))
    assert len(h.must_not) == 1
    assert h.must_not[0].enforced is True
    assert h.must_not[0].kind == "unwired"


def test_unwired_REFUSES_a_wire_that_touches_a_forbidden_pin():
    block = _base(must_not=[
        {"id": "no-boot-straps", "why": "ESP32-C3 strapping pins",
         "unwired": ["mcu.GPIO2", "mcu.GPIO8", "mcu.GPIO9"]},
    ])
    block["wires"].append({"from": "mcu.GPIO9", "to": "tm2.CLK"})
    with pytest.raises(HookupError) as exc:
        parse_hookup(block)
    msg = str(exc.value)
    assert "no-boot-straps" in msg and "mcu.GPIO9" in msg
    assert "strapping" in msg          # the `why` travels with the refusal


def test_unwired_accepts_a_wildcard_and_REFUSES_the_b_header_joint():
    """'leave the entire -B header unconnected' as ONE line, and it bites."""
    ok = parse_hookup(_base(must_not=[
        {"id": "dio-b-unconnected",
         "why": "the B header busses DIO; a joint there commons it",
         "unwired": ["*.DIO-B"]},
    ]))
    assert ok.must_not[0].resolved == ("tm1.DIO-B", "tm2.DIO-B")

    block = _base(must_not=[
        {"id": "dio-b-unconnected", "why": "the B header busses DIO",
         "unwired": ["*.DIO-B"]},
    ])
    block["wires"].append({"from": "tm1.DIO-B", "to": "tm2.DIO-B"})
    with pytest.raises(HookupError, match="dio-b-unconnected"):
        parse_hookup(block)


def test_a_clause_that_binds_to_nothing_is_an_ERROR():
    """A typo'd constraint reads as protection and provides none."""
    with pytest.raises(HookupError) as exc:
        parse_hookup(_base(must_not=[
            {"id": "typo", "why": "...", "unwired": ["mcu.GPIO99"]},
        ]))
    assert "matches no pin" in str(exc.value)


# ---------------------------------------------------------------------------
# distinct_nets: — "do not butt the modules together; DIO gets bussed"
# ---------------------------------------------------------------------------

def test_distinct_nets_passes_on_individual_dio():
    h = parse_hookup(_base(must_not=[
        {"id": "dio-never-common", "why": "commoned DIO = four identical displays",
         "distinct_nets": ["tm1.DIO", "tm2.DIO"]},
    ]))
    assert h.must_not[0].kind == "distinct_nets"


def test_distinct_nets_REFUSES_a_direct_common():
    block = _base(must_not=[
        {"id": "dio-never-common", "why": "commoned DIO = identical displays",
         "distinct_nets": ["tm1.DIO", "tm2.DIO"]},
    ])
    block["wires"].append({"from": "tm1.DIO", "to": "tm2.DIO"})
    with pytest.raises(HookupError, match="dio-never-common"):
        parse_hookup(block)


def test_distinct_nets_sees_THROUGH_the_pass_through_header():
    """The one that matters: DIO-B *is* DIO, so a B-header joint commons DIO.

    A checker that treats `DIO-B` as a different node passes this netlist and
    the builder gets two identical displays.  The catalog declares the
    intra-part net identity (`same_net:`) and the checker honours it.
    """
    block = _base(must_not=[
        {"id": "dio-never-common", "why": "commoned DIO = identical displays",
         "distinct_nets": ["tm1.DIO", "tm2.DIO"]},
    ])
    block["wires"].append({"from": "tm1.DIO-B", "to": "tm2.DIO-B"})
    with pytest.raises(HookupError) as exc:
        parse_hookup(block)
    assert "dio-never-common" in str(exc.value)
    assert "same-net" in str(exc.value)     # names WHY they are one net


# ---------------------------------------------------------------------------
# absent_parts: — "do not add pull-ups, the module already carries them"
# ---------------------------------------------------------------------------

def test_absent_parts_naming_a_part_NO_CATALOG_DEFINES_is_an_ERROR():
    """The same anti-vacuity rule as a pin pattern.  'do not add pull-ups —
    resistor-10k' looks like protection and is unfireable while no catalog
    defines that part, so it must be `stated:` until one does."""
    with pytest.raises(HookupError, match="cannot fire"):
        parse_hookup(_base(must_not=[
            {"id": "no-added-pullups", "why": "the module carries them",
             "absent_parts": ["resistor-10k"]},
        ]))


def test_absent_parts_REFUSES_the_redundant_pullup():
    block = _base(must_not=[
        {"id": "no-added-pullups",
         "why": "the module carries 10k on CLK and DIO already",
         "absent_parts": ["pot-10k"]},
    ])
    block["parts"].append({"id": "r1", "part": "pot-10k"})
    with pytest.raises(HookupError, match="no-added-pullups"):
        parse_hookup(block)


# ---------------------------------------------------------------------------
# stated: — carried, LABELLED unenforceable, never silently "checked"
# ---------------------------------------------------------------------------

def test_stated_is_carried_and_marked_unenforceable():
    h = parse_hookup(_base(must_not=[
        {"id": "no-butt-joint",
         "why": "the headers bus all four nets including DIO",
         "stated": "Do not butt the modules together on the pass-through "
                   "headers — use them pin-by-pin."},
    ]))
    assert h.must_not[0].enforced is False
    assert h.must_not[0].kind == "stated"
    assert h.stated_must_not and not h.enforced_must_not[0:1] == h.must_not[0:1]


def test_an_entry_needs_exactly_one_clause():
    with pytest.raises(HookupError, match="exactly one"):
        parse_hookup(_base(must_not=[{"id": "empty", "why": "..."}]))
    with pytest.raises(HookupError, match="exactly one"):
        parse_hookup(_base(must_not=[
            {"id": "two", "why": "...", "stated": "x", "unwired": ["mcu.GPIO2"]}]))


def test_an_entry_needs_an_id_and_a_why():
    with pytest.raises(HookupError, match="needs a string `id`"):
        parse_hookup(_base(must_not=[{"why": "...", "stated": "x"}]))
    with pytest.raises(HookupError, match="needs a `why`"):
        parse_hookup(_base(must_not=[{"id": "x", "stated": "x"}]))


def test_duplicate_must_not_ids_are_refused():
    with pytest.raises(HookupError, match="duplicate"):
        parse_hookup(_base(must_not=[
            {"id": "a", "why": "w", "stated": "x"},
            {"id": "a", "why": "w", "stated": "y"},
        ]))


# ---------------------------------------------------------------------------
# facts: — provenance.  measured vs computed vs datasheet vs ASSUMED.
# ---------------------------------------------------------------------------

_PULLUP = {
    "id": "module-pullups",
    "what": "onboard pull-up per line, per module",
    "value": "10 kOhm",
    "provenance": "assumed",
    "source": "TM1637 V2.4 p.8 R1/R2 — clones ship 4.7k silently",
    "measure_before_build": True,
    "if_different": "at 4.7k the level-shift arithmetic inverts",
    "measured": "EMPTY",
}


def test_a_fact_carries_its_provenance_and_an_EMPTY_sentinel():
    h = parse_hookup(_base(facts=[dict(_PULLUP)]))
    f = h.facts[0]
    assert isinstance(f, HookupFact)
    assert f.provenance == "assumed"
    assert f.is_empty is True                 # EMPTY == never touched a bench
    assert f.measure_before_build is True
    assert h.measure_before_build            # the guide's "check this" list


def test_an_ASSUMED_fact_must_declare_what_happens_if_it_differs():
    bad = dict(_PULLUP)
    bad.pop("if_different")
    with pytest.raises(HookupError, match="if_different"):
        parse_hookup(_base(facts=[bad]))


def test_an_ASSUMED_fact_cannot_opt_out_of_being_measured():
    """An assumption nobody intends to check is not an assumption, it is a lie."""
    bad = dict(_PULLUP, measure_before_build=False)
    with pytest.raises(HookupError, match="measure_before_build"):
        parse_hookup(_base(facts=[bad]))


def test_an_absent_measured_key_is_an_ERROR_not_an_empty_slot():
    """[DX-SPEC-CARRIES-ITS-EVIDENCE]: absent is indistinguishable from
    'nobody thought to write it'.  EMPTY is the only legal placeholder."""
    bad = dict(_PULLUP)
    bad.pop("measured")
    with pytest.raises(HookupError, match="measured"):
        parse_hookup(_base(facts=[bad]))


def test_a_MEASURED_fact_may_not_still_be_EMPTY():
    bad = dict(_PULLUP, provenance="measured", measure_before_build=False)
    with pytest.raises(HookupError, match="measured"):
        parse_hookup(_base(facts=[bad]))
    ok = parse_hookup(_base(facts=[dict(
        _PULLUP, provenance="measured", measure_before_build=False,
        measured="4.7 kOhm on 4 of 4 modules, DMM, 2026-08-15 dlb")]))
    assert ok.facts[0].is_empty is False
    assert not ok.measure_before_build


def test_a_DATASHEET_fact_must_cite_a_source():
    bad = {"id": "vih", "what": "V_IH min", "value": "3.5 V",
           "provenance": "datasheet", "measured": "EMPTY"}
    with pytest.raises(HookupError, match="source"):
        parse_hookup(_base(facts=[bad]))


def test_an_unknown_provenance_is_refused():
    bad = dict(_PULLUP, provenance="probably")
    with pytest.raises(HookupError, match="provenance"):
        parse_hookup(_base(facts=[bad]))


# ---------------------------------------------------------------------------
# purely additive: a hookup with neither block is unchanged
# ---------------------------------------------------------------------------

def test_no_must_not_and_no_facts_is_unchanged():
    h = parse_hookup(_base())
    assert h.must_not == () and h.facts == ()
    assert h.measure_before_build == ()
