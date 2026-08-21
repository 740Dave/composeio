"""
End-to-end write-path proof for the Decisions tab curation surface
([SPEC:CXC-DECISIONS-TAB] "the tab is a WRITE surface").

The tab publishes a curation INTENT; the ``decisions_curator`` consumes it and
calls the decision-ops MCP ``decision_update`` (the single-writer that splices
the source ``.lts`` in its exact byte format AND refreshes the lts-engine index),
then re-projects so the pane reflects the change on re-query.  These tests drive
that against a REAL temp-fixture ``.lts`` (no broker, no network) — proving the
prioritize-up + append-note writes land on disk and are immediately queryable.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.dirname(PY_ROOT)
for p in (PY_ROOT, os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from composeio.decision_ops.mcp_server import DecisionOpsMcp
from composeio.decision_ops.mcp_selftest import FIXTURE_GRAPH

import decisions_curator as dc


@pytest.fixture
def fixture_graph(tmp_path):
    p = tmp_path / "decision-graph-mcpfix.lts"
    p.write_text(FIXTURE_GRAPH, encoding="utf-8")
    return str(p)


def _curator(graph):
    ops = DecisionOpsMcp(default_graph=graph, profile=None)
    return dc.Curator(
        ops, graph_for=lambda corpus: graph,
        project=lambda anchor, corpus: {
            "anchor": anchor, "corpus": corpus, "edges": [],
            "nodes": [{"id": n["id"], "importance": n.get("importance", "")}
                      for n in ops.dispatch("decision_query",
                                            {"selector": "prefix:DEC"})
                      .get("results", [])]})


def test_prioritize_writes_lts_and_refreshes(fixture_graph):
    cur = _curator(fixture_graph)
    assert "importance: low" in Path(fixture_graph).read_text()  # DEC-...-SECOND
    out = cur.apply({"id": "DEC-MCPFIX-SECOND", "op": "prioritize",
                     "value": "high", "corpus": "mcpfix"})
    assert out["ok"] is True
    assert out["update"]["changed"] is True
    # the .lts was actually rewritten in its exact byte format
    text = Path(fixture_graph).read_text()
    assert "importance: high" in text
    assert "importance: low" not in text.split("DEC-MCPFIX-SECOND")[1].split(
        "###")[0]                                  # SECOND's low is gone
    # the no-stale-window guarantee is proven by test_reprojection_reflects_...
    # reading the fresh value (decision_ops' own refresh-on-write is separately
    # covered by decision_ops.mcp_selftest).


def test_append_note_writes_lts(fixture_graph):
    cur = _curator(fixture_graph)
    out = cur.apply({"id": "DEC-MCPFIX-ROOT", "op": "note",
                     "value": "carried on this decision", "corpus": "mcpfix"})
    assert out["ok"] is True
    assert "note: carried on this decision" in Path(fixture_graph).read_text()


def test_reprojection_reflects_the_write(fixture_graph):
    cur = _curator(fixture_graph)
    cur.apply({"id": "DEC-MCPFIX-SECOND", "op": "prioritize",
               "value": "high", "corpus": "mcpfix"})
    pl = cur.reproject("mcpfix", "DEC-MCPFIX-SECOND")
    node = next(n for n in pl["nodes"] if n["id"] == "DEC-MCPFIX-SECOND")
    assert node["importance"] == "high"           # re-query sees the write


def test_unsupported_op_refused_no_write(fixture_graph):
    cur = _curator(fixture_graph)
    before = Path(fixture_graph).read_bytes()
    out = cur.apply({"id": "DEC-MCPFIX-SECOND", "op": "delete", "value": "x",
                     "corpus": "mcpfix"})
    assert out["ok"] is False
    assert Path(fixture_graph).read_bytes() == before    # byte-identical


def test_unknown_node_no_crash(fixture_graph):
    cur = _curator(fixture_graph)
    out = cur.apply({"id": "NO-SUCH", "op": "prioritize", "value": "high",
                     "corpus": "mcpfix"})
    assert out["ok"] is False


def test_widget_intent_shape_matches_curator_contract():
    # the exact payload DecisionsTabWidget._curate publishes is what the curator
    # consumes — a contract cross-check (no drift between the two files).
    from composeio.render.widgets.decisions_tab import DecisionsTabWidget

    published = []

    class Ctx:
        theme = "dark"
        reduced_motion = False
        def request_redraw(self, rect=None): pass
        def publish(self, ns, val, source=None): published.append((ns, val))

    w = DecisionsTabWidget("dt",
                           nodes=[{"id": "DEC-X", "title": "x",
                                   "importance": "low"}], edges=[],
                           corpus="mcpfix")
    w.on_mount(Ctx())
    w._curate("DEC-X", "prioritize", "medium")
    ns, intent = published[-1]
    assert ns == "_app/decisions/curate"
    # the curator accepts exactly this shape
    assert set(intent) >= {"id", "op", "value", "corpus"}
    assert dc._fields_for(intent["op"], intent["value"]) == {"importance": "medium"}
