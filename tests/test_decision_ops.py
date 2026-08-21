"""pytest coverage for `composeio.decision_ops` (add/update/query/resolve).

Every test operates on a `tmp_path` COPY of a synthetic fixture graph -- never
`decisions/decision-graph-composeux-architect.lts` or any other real project
graph. See `python/composeio/decision_ops/selftest.py` for the equivalent
no-pytest end-to-end run (``python3 -m composeio.decision_ops --selftest``).
"""
from __future__ import annotations

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'python'))
from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS, INTELLIGENCE_CONSOLE_CORPUS_DIR as _ICC  # corpora live OUTSIDE the repo since 2026-08-17
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from composeio.decision_ops import (
    DangleRefusedError,
    NodeExistsError,
    NodeNotFoundError,
    add,
    query,
    resolve,
    update,
)
from factlattice_engine.lint import lint

FIXTURE_GRAPH = """\
---
domain: PLT.CUX
confidentiality: internal-only
artifact-readiness: system-design
created: 2026-07-01
---
# Fixture Decision Graph [SPEC:FIXTURE-DECISIONS]

> Synthetic fixture for `composeio.decision_ops` pytest coverage.

### [DEC-FIXTURE-ROOT] A root fixture decision, referenced so nothing here starts as a dangle
- lifecycle-position: committed
- decided-in-session: 2026-07-01
- importance: medium
- rationale: exists purely so update()/resolve() targets have something to point at without inventing dangling refs.

### [DEC-FIXTURE-SECOND] A second fixture decision that depends on the root
- lifecycle-position: decided
- decided-in-session: 2026-07-01
- importance: low
- rationale: a second node to prove query() filtering and multi-node behavior.
- depends-on: [DEC-FIXTURE-ROOT]
"""

REAL_GRAPH = _ICC / "decision-graph-composeux-architect.lts"
# Captured at MODULE IMPORT time (before any test in this file runs) so the
# tripwire test at the bottom compares against the real graph's state prior to
# this whole suite, not just within one test function.
_REAL_GRAPH_BYTES_AT_IMPORT = REAL_GRAPH.read_bytes() if REAL_GRAPH.is_file() else None


@pytest.fixture
def graph(tmp_path) -> str:
    p = tmp_path / "fixture-decisions.lts"
    p.write_text(FIXTURE_GRAPH, encoding="utf-8")
    return str(p)


NEW_NODE = {
    "id": "DEC-FIXTURE-NEW", "title": "A newly added fixture decision",
    "lifecycle-position": "raised", "decided-in-session": "2026-07-22",
    "importance": "medium",
    "rationale": "added by pytest, round-trip proof.",
    "depends-on": "[DEC-FIXTURE-ROOT]",
}


def test_fixture_starts_lint_clean(graph):
    result = lint(graph, dangle=True)
    assert not result.has_errors


def test_add_appends_byte_compatible_node(graph):
    res = add(graph, NEW_NODE)
    assert res.changed
    assert res.lint_clean
    text = Path(graph).read_text(encoding="utf-8")
    assert "### [DEC-FIXTURE-NEW] A newly added fixture decision" in text
    assert "- lifecycle-position: raised" in text
    # no NEW forward dangle
    assert not lint(graph, dangle=True).has_errors


def test_add_is_idempotent(graph):
    add(graph, NEW_NODE)
    before = Path(graph).read_text(encoding="utf-8")
    res2 = add(graph, NEW_NODE)
    assert res2.changed is False
    assert Path(graph).read_text(encoding="utf-8") == before


def test_add_duplicate_id_different_content_raises(graph):
    add(graph, NEW_NODE)
    conflicting = dict(NEW_NODE)
    conflicting["rationale"] = "a totally different rationale"
    with pytest.raises(NodeExistsError):
        add(graph, conflicting)


def test_add_missing_required_field_raises(graph):
    incomplete = {"id": "DEC-FIXTURE-INCOMPLETE", "title": "no rationale",
                 "lifecycle-position": "raised", "decided-in-session": "2026-07-22",
                 "importance": "low"}
    with pytest.raises(ValueError):
        add(graph, incomplete)


def test_add_refused_on_new_forward_dangle(graph):
    before = Path(graph).read_text(encoding="utf-8")
    bad = {
        "id": "DEC-FIXTURE-BAD", "title": "points nowhere",
        "lifecycle-position": "raised", "decided-in-session": "2026-07-22",
        "importance": "low", "rationale": "deliberately dangling depends-on.",
        "depends-on": "[DEC-DOES-NOT-EXIST-ANYWHERE]",
    }
    with pytest.raises(DangleRefusedError):
        add(graph, bad)
    # rolled back exactly
    assert Path(graph).read_text(encoding="utf-8") == before
    assert query(graph, "id:DEC-FIXTURE-BAD") == []
    assert not lint(graph, dangle=True).has_errors


def test_query_by_exact_id(graph):
    add(graph, NEW_NODE)
    results = query(graph, "DEC-FIXTURE-NEW")
    assert len(results) == 1
    r = results[0]
    assert r["lifecycle_position"] == "raised"
    assert r["source_file"] and r["line"]
    assert "round-trip proof" in r["rationale"]


def test_query_by_prefix(graph):
    add(graph, NEW_NODE)
    results = query(graph, "prefix:DEC")
    assert {r["id"] for r in results} == {
        "DEC-FIXTURE-ROOT", "DEC-FIXTURE-SECOND", "DEC-FIXTURE-NEW"}


def test_query_by_free_text(graph):
    add(graph, NEW_NODE)
    results = query(graph, "text:round-trip")
    assert any(r["id"] == "DEC-FIXTURE-NEW" for r in results)


def test_query_no_match_returns_empty_list(graph):
    assert query(graph, "id:DEC-NOPE") == []


def test_update_lifecycle_progression(graph):
    add(graph, NEW_NODE)
    r1 = update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "decided"})
    assert r1.changed
    assert query(graph, "id:DEC-FIXTURE-NEW")[0]["lifecycle_position"] == "decided"

    r2 = update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "decided"})
    assert r2.changed is False  # idempotent

    r3 = update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "committed"})
    assert r3.changed
    assert query(graph, "id:DEC-FIXTURE-NEW")[0]["lifecycle_position"] == "committed"
    assert not lint(graph, dangle=True).has_errors


def test_update_preserves_other_fields(graph):
    add(graph, NEW_NODE)
    update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "committed"})
    r = query(graph, "id:DEC-FIXTURE-NEW")[0]
    assert "round-trip proof" in r["rationale"]
    assert r["importance"] == "medium"


def test_update_missing_node_raises(graph):
    with pytest.raises(NodeNotFoundError):
        update(graph, "DEC-NOPE", {"importance": "high"})


def test_resolve_annotates_never_deletes(graph):
    before_ids = {r["id"] for r in query(graph, "prefix:DEC")}
    res = resolve(graph, "DEC-FIXTURE-SECOND", superseded_by="DEC-FIXTURE-ROOT",
                  note="pytest supersession", today="2026-07-22")
    assert res.changed
    assert res.lint_clean

    after_ids = {r["id"] for r in query(graph, "prefix:DEC")}
    assert before_ids == after_ids  # nothing deleted

    r = query(graph, "id:DEC-FIXTURE-SECOND")[0]
    assert r["lifecycle_position"] == "superseded"

    text = Path(graph).read_text(encoding="utf-8")
    assert "RESOLVED" in text and "DEC-FIXTURE-ROOT" in text and "2026-07-22" in text
    assert "A second fixture decision that depends on the root" in text  # title intact
    assert not lint(graph, dangle=True).has_errors


def test_resolve_is_idempotent(graph):
    resolve(graph, "DEC-FIXTURE-SECOND", superseded_by="DEC-FIXTURE-ROOT",
           note="pytest supersession", today="2026-07-22")
    before = Path(graph).read_text(encoding="utf-8")
    res2 = resolve(graph, "DEC-FIXTURE-SECOND", superseded_by="DEC-FIXTURE-ROOT",
                   note="pytest supersession", today="2026-07-22")
    assert res2.changed is False
    assert Path(graph).read_text(encoding="utf-8") == before


def test_resolve_missing_node_raises(graph):
    with pytest.raises(NodeNotFoundError):
        resolve(graph, "DEC-NOPE", status="parked")


def test_full_roundtrip(graph):
    """add -> query returns it -> update lifecycle-position -> resolve it,
    lint-clean throughout -- the acceptance-bar round-trip in one test."""
    assert not lint(graph, dangle=True).has_errors

    add(graph, NEW_NODE)
    assert not lint(graph, dangle=True).has_errors
    found = query(graph, "DEC-FIXTURE-NEW")
    assert len(found) == 1 and found[0]["lifecycle_position"] == "raised"

    update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "committed"})
    assert not lint(graph, dangle=True).has_errors
    assert query(graph, "DEC-FIXTURE-NEW")[0]["lifecycle_position"] == "committed"

    resolve(graph, "DEC-FIXTURE-NEW", status="parked", note="pytest full round-trip",
            today="2026-07-22")
    assert not lint(graph, dangle=True).has_errors
    resolved = query(graph, "DEC-FIXTURE-NEW")[0]
    assert resolved["lifecycle_position"] == "parked"


# --------------------------------------------------------------------------- #
# Guardrail: the REAL project decision graph must never be touched by this
# suite. Not a decision_ops feature test -- a hygiene check on the tests
# themselves (hard requirement in the build brief).
# --------------------------------------------------------------------------- #
def test_real_decision_graph_untouched_by_this_suite():
    """Byte-for-byte tripwire: compares the real graph's content at MODULE
    IMPORT time (before any test in this file ran) against its content right
    now. This suite never passes `REAL_GRAPH` to add/update/query/resolve --
    this test exists to CATCH a future regression that accidentally does."""
    assert _REAL_GRAPH_BYTES_AT_IMPORT is not None, "sanity: the real graph should exist on disk"
    assert REAL_GRAPH.read_bytes() == _REAL_GRAPH_BYTES_AT_IMPORT
