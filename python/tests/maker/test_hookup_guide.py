"""
The rendered guide's "Warnings and Disclaimers" section — DERIVED, not written.

David, 2026-08-15: "Probably a good idea to add a 'Warnings and Disclaimers'
section to the rendered hookup guide with any salient items from the must_not
block and the 4.7k vs 10k pullups."

Generated from `hookup.must_not:` and `hookup.facts:` so it CANNOT drift from
the netlist it warns about — a hand-written warning section is a second source
of truth, and the whole point of the two new blocks is that there is one.  The
drift test at the bottom is what makes that claim true rather than aspirational:
it regenerates the block and compares it to the one on disk.
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

from composeio.maker.hookup_guide import (
    BEGIN_MARK, END_MARK, inject_section, warnings_section)
from composeio.render.engine.hookup import parse_hookup

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples")
TM1637_SCENE = os.path.join(EXAMPLES, "tm1637_quad_panel.compose.yaml")
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
TM1637_GUIDE = os.path.join(
    REPO_ROOT, "docs", "board-bringup", "tm1637-quad-panel.md")


def _hookup(path=TM1637_SCENE):
    with open(path) as fh:
        return parse_hookup(yaml.safe_load(fh)["hookup"])


# ---------------------------------------------------------------------------
# what the section says
# ---------------------------------------------------------------------------

def test_the_section_separates_CHECKED_from_STATED():
    text = warnings_section(_hookup())
    assert "Warnings and Disclaimers" in text
    # the enforced ones say so, in the words that matter to a builder
    assert "REFUSES" in text or "refuses" in text
    # ...and the stated ones are never dressed as checked
    assert "no machine can check" in text.lower()


def test_every_must_not_reaches_the_page_with_its_why():
    h = _hookup()
    text = warnings_section(h)
    assert h.must_not, "the TM1637 panel must carry must_not entries"
    for m in h.must_not:
        assert m.id in text, f"must_not '{m.id}' never reached the guide"
        assert m.why[:40] in text, f"must_not '{m.id}' lost its WHY"


def test_the_4k7_vs_10k_pullup_is_in_MEASURE_BEFORE_YOU_BUILD():
    """The motivating case: the level-shift arithmetic all MOVES at 4.7k, and
    vendors vary it silently.  The reader must see the assumed value, how to
    check it, and what breaks if it differs."""
    text = warnings_section(_hookup())
    assert "MEASURE BEFORE YOU BUILD" in text
    assert "10 k" in text and "4.7 k" in text
    # ...and WHAT TO DO about it.  Note the artifact declines the brief's own
    # word: at 4.7 kOhm the decision does not INVERT, it gets stronger (the
    # back-drive current doubles).  Every number moves, the conclusion does
    # not, and the guide says which — "inverts" would have read as permission
    # to delete the level shifters.
    assert "recompute" in text.lower()
    assert "does NOT invert" in text


def test_filled_evidence_is_NOT_listed_as_something_to_measure():
    h = _hookup()
    text = warnings_section(h)
    for f in h.facts:
        if not f.is_empty:
            assert f.measured in text or f.value in text
    assert h.empty_evidence, "EMPTY slots are the record of never-benched"


def test_the_section_states_how_many_slots_are_EMPTY():
    h = _hookup()
    text = warnings_section(h)
    assert str(len(h.empty_evidence)) in text
    assert "EMPTY" in text


def test_a_hookup_with_neither_block_produces_nothing():
    """Purely additive: a scene that declares no negative space and no facts
    gets no section at all, rather than an empty heading that reads as
    'checked and clean'."""
    h = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": "esp32-c3-supermini"}],
        "wires": [{"from": "mcu.GPIO0", "to": "mcu.GND"}],
    })
    assert warnings_section(h) == ""


# ---------------------------------------------------------------------------
# injection + the drift test
# ---------------------------------------------------------------------------

def test_injection_replaces_between_markers_and_is_idempotent():
    doc = "# Title\n\nintro\n\n---\n\n## 1. Analysis\n\nbody\n"
    once = inject_section(doc, "GENERATED\n", anchor="---")
    twice = inject_section(once, "GENERATED\n", anchor="---")
    assert once == twice
    assert once.count(BEGIN_MARK) == 1 and once.count(END_MARK) == 1
    # ...and it lands BEFORE the analysis, where a person about to solder is
    assert once.index(BEGIN_MARK) < once.index("## 1. Analysis")


def test_injection_updates_a_stale_block_in_place():
    doc = f"# T\n\n{BEGIN_MARK}\nOLD\n{END_MARK}\n\n## 1. x\n"
    out = inject_section(doc, "NEW\n")
    assert "OLD" not in out and "NEW" in out
    assert out.count(BEGIN_MARK) == 1


@pytest.mark.skipif(not os.path.isfile(TM1637_GUIDE), reason="guide absent")
def test_the_SHIPPED_guide_matches_a_FRESH_generation():
    """⛔ THE DRIFT TEST.  'Derived so it cannot drift' is a claim about a
    process until something compares the two.  Regenerate and diff."""
    with open(TM1637_GUIDE) as fh:
        doc = fh.read()
    assert BEGIN_MARK in doc, "the shipped guide carries no generated section"
    fresh = inject_section(doc, warnings_section(_hookup()))
    assert fresh == doc, (
        "the guide's Warnings section is STALE — regenerate it with\n"
        "  python3 -m composeio.maker.hookup_guide "
        "composeio/render/examples/tm1637_quad_panel.compose.yaml "
        "--inject docs/board-bringup/tm1637-quad-panel.md")
