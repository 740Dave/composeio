"""
[SPEC:CXC-DPE] — decisions_publisher enrichment gates (machine tier).

Proves ``build_projection`` widens the SHIPPED thin per-node schema
({id,title,lifecycle,superseded,is_anchor}) to the RICH, TOPOLOGY-ONLY schema
the built Decisions tab consumes:

  * every node carries the six rich props (importance, session, rationale,
    source, dsd, lifecycle-position), present-but-empty never absent/null
    ([CXC-DPE-NODE-SHAPE]);
  * the typed edges are emitted as the FIVE distinct canonical kinds, with the
    legacy RESOLVES input folded to RE_DERIVE_VIA and non-canonical kinds
    (CONTAINS) dropped ([CXC-DPE-EDGE-KINDS]);
  * the payload is TOPOLOGY-ONLY — no node/edge key carries a geometry token
    ([CXC-DPE-TOPOLOGY-ONLY]);
  * the thin fields survive verbatim ([CXC-DPE-BACKWARD-COMPAT]);
  * edges ride a SEPARATE top-level list ([DD-DPE-EDGE-EMISSION] ruled).

Corpus-free: a FAKE MagmaTools is injected via ``build_projection(_tools=...)``,
so this runs GREEN with no factlattice repo, no broker, no render deps.  The
corpus-backed variant lives in ``decisions_publisher._selftest`` (SKIPs without
the repo); the tab-side render gate ([CXC-DPE-VERIFY-GLASS]) lives beside
``test_decisions_tab.py`` on the branch that carries the tab.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import decisions_publisher as dp  # noqa: E402

# The FULL rationale string must survive unmodified (no truncation, no strip).
_FULL_RATIONALE = ("Extend the productive-work runway. 90 is now a plain FYI; "
                   "95 is the pencils-down guidance.   ")


def _magma_result():
    """A MAGMA-shaped traverse() result: nodes carry top-level id/title/
    source_file/line_range + a `properties` dict; edges carry type/source/
    target with the canonical uppercase-underscore type MAGMA stores (plus a
    legacy RESOLVES + a CONTAINS + a duplicate, to exercise the projection)."""
    return {
        "success": True,
        "nodes": [
            {"id": "DEC-95-HANDOFF-LINE",
             "title": "95% is the pencils-down handoff line",
             "source_file": "decisions/decision-graph-composeux-architect.lts",
             "line_range": [113, 120],
             "properties": {
                 "lifecycle-position": "committed",
                 "importance": "  High ",              # trims + lowers -> "high"
                 "decided-in-session": "2026-07-20",
                 "rationale": _FULL_RATIONALE,
                 "dsd-source":
                     "docs/intelligence-console/DECISIONS-2026-07-24-open-calls.md",
             }},
            {"id": "DEC-90-COMPACT-PREP",
             "title": "90% was the prior line",
             "source_file": "decisions/decision-graph-composeux-architect.lts",
             "line_range": [893],                       # single-line -> ":893"
             "properties": {"lifecycle-position":
                            "superseded   # applies to the OLD 90% rule"}},
            {"id": "DEC-CROSSING-IS-CHEAP",
             "title": "a crossing is cheap",
             "source_file": "decisions/decision-graph-composeux-architect.lts",
             "line_range": [200, 200],                  # start==end -> ":200"
             "properties": {"lifecycle-position": "committed",
                            "importance": "MEDIUM"}},
            {"id": "DEC-RELATES-NODE", "title": "a related decision",
             "properties": {"lifecycle-position": "decided",
                            "importance": "low"}},
            {"id": "DEC-CARRY-POINTER", "title": "carry the least pointer",
             "properties": {"lifecycle-position": "committed",
                            "importance": "high"}},
            {"id": "DEC-FOLLOWS-NODE", "title": "the second half",
             "properties": {"lifecycle-position": "committed"}},
            {"id": "DEC-BARE", "title": "no properties, no source"},  # all-empty
        ],
        "edges": [
            {"type": "SUPERSEDES", "source": "DEC-95-HANDOFF-LINE",
             "target": "DEC-90-COMPACT-PREP"},
            {"type": "SUPERSEDES", "source": "DEC-95-HANDOFF-LINE",
             "target": "DEC-90-COMPACT-PREP"},           # duplicate -> deduped
            {"type": "DEPENDS_ON", "source": "DEC-CROSSING-IS-CHEAP",
             "target": "DEC-95-HANDOFF-LINE"},
            {"type": "RELATES_TO", "source": "DEC-RELATES-NODE",
             "target": "DEC-95-HANDOFF-LINE"},
            {"type": "RE_DERIVE_VIA", "source": "DEC-CARRY-POINTER",
             "target": "FIXTURE-registration-protocol-P1"},  # non-DEC target: kept
            {"type": "RESOLVES", "source": "DEC-CARRY-POINTER",
             "target": "FIXTURE-continuity-handoff-process"},  # legacy -> RE_DERIVE_VIA
            {"type": "FOLLOWS", "source": "DEC-FOLLOWS-NODE",
             "target": "DEC-95-HANDOFF-LINE"},
            {"type": "CONTAINS", "source": "SECTION-HUB",
             "target": "DEC-95-HANDOFF-LINE"},            # non-canonical -> dropped
        ],
    }


class _FakeTools:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def traverse(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _project(anchor="DEC-95-HANDOFF-LINE"):
    tools = _FakeTools(_magma_result())
    pl = dp.build_projection(anchor=anchor, corpus="intelligence-console",
                             depth=2, _tools=tools)
    return pl, tools


def _node(pl, nid):
    return next(n for n in pl["nodes"] if n["id"] == nid)


# ---------------------------------------------------------------------------
# [CXC-DPE-NODE-SHAPE] — the six rich props
# ---------------------------------------------------------------------------

RICH_KEYS = ("importance", "session", "rationale", "source", "dsd",
             "lifecycle-position")


def test_every_node_carries_the_six_rich_keys_present_maybe_empty():
    pl, _ = _project()
    for n in pl["nodes"]:
        for k in RICH_KEYS:
            assert k in n, f"{n['id']} missing rich key {k}"
            assert isinstance(n[k], str)          # JSON scalar string, never None


def test_importance_trimmed_and_lowercased():
    pl, _ = _project()
    assert _node(pl, "DEC-95-HANDOFF-LINE")["importance"] == "high"
    assert _node(pl, "DEC-CROSSING-IS-CHEAP")["importance"] == "medium"
    assert _node(pl, "DEC-RELATES-NODE")["importance"] == "low"
    assert _node(pl, "DEC-BARE")["importance"] == ""          # present-but-empty


def test_session_is_decided_in_session_verbatim():
    pl, _ = _project()
    assert _node(pl, "DEC-95-HANDOFF-LINE")["session"] == "2026-07-20"
    assert _node(pl, "DEC-BARE")["session"] == ""


def test_rationale_is_full_and_unmodified():
    pl, _ = _project()
    # FULL text — no truncation, no strip ([CXC-DPE-NODE-SHAPE])
    assert _node(pl, "DEC-95-HANDOFF-LINE")["rationale"] == _FULL_RATIONALE
    assert _node(pl, "DEC-BARE")["rationale"] == ""


def test_source_location_formatting():
    pl, _ = _project()
    root = "decisions/decision-graph-composeux-architect.lts"
    assert _node(pl, "DEC-95-HANDOFF-LINE")["source"] == f"{root}:113-120"
    assert _node(pl, "DEC-90-COMPACT-PREP")["source"] == f"{root}:893"  # single
    assert _node(pl, "DEC-CROSSING-IS-CHEAP")["source"] == f"{root}:200"  # eq
    assert _node(pl, "DEC-BARE")["source"] == ""            # unavailable -> ""


def test_dsd_present_when_sourced_else_empty():
    pl, _ = _project()
    assert (_node(pl, "DEC-95-HANDOFF-LINE")["dsd"]
            == "docs/intelligence-console/DECISIONS-2026-07-24-open-calls.md")
    assert _node(pl, "DEC-90-COMPACT-PREP")["dsd"] == ""


def test_lifecycle_position_canonical_and_comment_stripped():
    pl, _ = _project()
    n90 = _node(pl, "DEC-90-COMPACT-PREP")
    assert n90["lifecycle-position"] == "superseded"        # inline comment gone
    # canonical mirrors the shipped compat key ([DD-DPE-LIFECYCLE-NAMING])
    for n in pl["nodes"]:
        assert n["lifecycle-position"] == n["lifecycle"]


# ---------------------------------------------------------------------------
# [CXC-DPE-EDGE-KINDS] — five distinct canonical kinds, aliasing, dedup
# ---------------------------------------------------------------------------

def test_all_five_edge_kinds_emitted_distinctly():
    pl, _ = _project()
    kinds = {e["type"] for e in pl["edges"]}
    assert kinds == {"SUPERSEDES", "DEPENDS_ON", "RELATES_TO",
                     "RE_DERIVE_VIA", "FOLLOWS"}


def test_every_edge_type_is_canonical():
    pl, _ = _project()
    for e in pl["edges"]:
        assert e["type"] in dp.CANONICAL_EDGE_KINDS


def test_legacy_resolves_normalized_to_re_derive_via():
    pl, _ = _project()
    assert not any(e["type"] == "RESOLVES" for e in pl["edges"])
    assert any(e["type"] == "RE_DERIVE_VIA"
               and e["target"] == "FIXTURE-continuity-handoff-process"
               for e in pl["edges"])


def test_non_canonical_contains_edge_dropped():
    pl, _ = _project()
    assert not any(e["type"] == "CONTAINS" for e in pl["edges"])


def test_edges_deduped_by_type_source_target():
    pl, _ = _project()
    sup = [e for e in pl["edges"]
           if e["type"] == "SUPERSEDES"
           and e["source"] == "DEC-95-HANDOFF-LINE"
           and e["target"] == "DEC-90-COMPACT-PREP"]
    assert len(sup) == 1


def test_re_derive_via_to_nondecision_target_is_kept():
    pl, _ = _project()
    assert any(e["type"] == "RE_DERIVE_VIA"
               and e["target"] == "FIXTURE-registration-protocol-P1"
               for e in pl["edges"])


def test_traverse_uses_the_widened_alphabet():
    _, tools = _project()
    et = set(tools.calls[0]["edge_types"])
    assert {"SUPERSEDES", "DEPENDS_ON", "RELATES_TO", "RE_DERIVE_VIA",
            "FOLLOWS"} <= et


# ---------------------------------------------------------------------------
# superseded flag ([CXC-DPE-EDGE-KINDS] live-supersession, retained)
# ---------------------------------------------------------------------------

def test_supersede_target_and_lifecycle_superseded_marked():
    pl, _ = _project()
    assert _node(pl, "DEC-90-COMPACT-PREP")["superseded"] is True
    assert _node(pl, "DEC-95-HANDOFF-LINE")["superseded"] is False


# ---------------------------------------------------------------------------
# [CXC-DPE-BACKWARD-COMPAT] — the thin fields survive; edges are a top-level list
# ---------------------------------------------------------------------------

def test_thin_shipped_fields_survive_verbatim():
    pl, _ = _project()
    anchor = _node(pl, "DEC-95-HANDOFF-LINE")
    assert anchor["id"] == "DEC-95-HANDOFF-LINE"
    assert anchor["title"] == "95% is the pencils-down handoff line"
    assert anchor["lifecycle"] == "committed"
    assert anchor["superseded"] is False
    assert anchor["is_anchor"] is True
    # envelope keys unchanged
    for k in ("ts", "corpus", "anchor", "depth", "nodes", "edges"):
        assert k in pl


def test_edges_are_a_separate_top_level_list_not_inline():
    pl, _ = _project()
    assert isinstance(pl["edges"], list)
    for n in pl["nodes"]:
        assert "edges" not in n                 # not inlined per node (ruled)


# ---------------------------------------------------------------------------
# [CXC-DPE-TOPOLOGY-ONLY] — no positions on the bus
# ---------------------------------------------------------------------------

def test_no_geometry_keys_on_any_node_or_edge():
    pl, _ = _project()
    assert dp._geometry_key_violations(pl) == []


def test_lifecycle_position_key_not_flagged_as_geometry():
    # regression guard: "lifecycle-position" contains the token "position" but is
    # a ratified semantic key — the allowlist must exempt it.
    pl, _ = _project()
    assert all("lifecycle-position" in n for n in pl["nodes"])
    assert dp._geometry_key_violations(pl) == []


def test_projection_is_json_serializable():
    pl, _ = _project()
    assert json.loads(json.dumps(pl))["anchor"] == "DEC-95-HANDOFF-LINE"


# ---------------------------------------------------------------------------
# error path — an unsuccessful traverse still returns the envelope, no crash
# ---------------------------------------------------------------------------

def test_failed_traverse_returns_empty_envelope():
    tools = _FakeTools({"success": False, "error": "boom"})
    pl = dp.build_projection(anchor="DEC-X", _tools=tools)
    assert pl["nodes"] == [] and pl["edges"] == []
    assert pl["anchor"] == "DEC-X"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
