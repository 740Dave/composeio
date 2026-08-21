"""Cross-ENTRY-POINT enforcement of the graph<->SQLite seam
([SPEC:CIO-DEFECT-REGISTRY] [DR-CLI]/[DR-SEAM]/[DR-SQLITE]).

THE DEFECT THIS GUARDS: the SQLite-analytics write (and the engine refresh) were
composed only in `DefectOpsMcp.dispatch`, but the CLI (`__main__.py`) FORKED a
direct-to-core path that dropped them. So every defect-investigator that filed an
attempt via the documented CLI (`python3 -m composeio.defect_ops attempt ...`)
wrote the GRAPH and left `defect_investigations` EMPTY (0 rows) — the Defects
roll-up publisher then emitted an all-NULL projection and the Dashboard widget
showed blanks, even though defects HAD been investigated.

The invariant: EVERY entry point (the CLI argv shell + the MCP dispatch, and any
future bus handler) MUST land a `defect_investigations` row cross-linked by
`defect_id` for an attempt. This test is PARAMETRIZED over the entry points so
ANY future one that re-composes the op and drops the SQLite seam fails loudly.
It FAILS before the de-fork (CLI wrote 0 rows) and passes after.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "compose_projects"))  # `import intelligence.*` post-reorg

from composeio.defect_ops import symptom_slug  # noqa: E402

_FIXTURE = """\
---
domain: defect
confidentiality: internal-only
artifact-readiness: system-design
---
# Fixture Defect Corpus [SYSTEM:DEFECT-CORPUS]

> Synthetic fixture for the CLI<->MCP bridge test. Seeded empty.

- status: fixture
"""


@pytest.fixture()
def graph(tmp_path):
    p = tmp_path / "defect-graph.lts"
    p.write_text(_FIXTURE, encoding="utf-8")
    return str(p)


@pytest.fixture()
def retro_db(tmp_path, monkeypatch):
    """Point the retrospective store at a TEMP db (never the real metrics.db)."""
    monkeypatch.setenv("COMPOSEIO_RETRO_DB", str(tmp_path / "metrics.db"))
    from intelligence.retrospective import db as _db
    return _db


# --- the two entry points, each a callable (symptom, graph) -> None ----------

def _via_cli(symptom: str, graph: str) -> None:
    from composeio.defect_ops.__main__ import main
    rc = main(["attempt", graph, "--symptom", symptom,
               "--root-cause", "under-spec", "--what-changed", "de-forked the CLI",
               "--outcome", "held", "--branch", "defect/cli-bridge"])
    assert rc == 0


def _via_dispatch(symptom: str, graph: str) -> None:
    from composeio.defect_ops.mcp_server import DefectOpsMcp
    res = DefectOpsMcp(default_graph=graph).dispatch("defect_attempt", {
        "symptom": symptom, "root_cause_class": "under-spec",
        "what_changed": "de-forked the CLI", "outcome": "held",
        "branch": "defect/cli-bridge"})
    assert res["success"] and res.get("analytics_row_id")


@pytest.mark.parametrize("entry", [_via_cli, _via_dispatch],
                         ids=["cli", "mcp-dispatch"])
def test_attempt_writes_sqlite_row_from_every_entry_point(entry, graph, retro_db):
    symptom = "defects widget shows NULL because SQLite is unfed"
    entry(symptom, graph)

    rows = retro_db.list_defect_investigations()
    # (1) the seam: an analytics row landed at all (the fork left this at 0)
    assert len(rows) == 1, "every entry point MUST land exactly one SQLite row"
    # (2) cross-link to the graph node ([DR-SEAM] defect_id)
    assert rows[0]["defect_id"] == symptom_slug(symptom)
    # (3) the class fallback feeds the histogram + the under-spec gauge from a
    #     minimal call (CLI passes the taxonomy as --root-cause)
    assert retro_db.defects_by_root_cause().get("under-spec") == 1
    assert rows[0]["under_spec"] == 1


def test_cli_and_dispatch_agree_on_the_rollup(graph, retro_db):
    """Both entry points feed the SAME roll-up the publisher projects — de-fork
    proof: two attempts (one per entry point) on two symptoms => total 2."""
    _via_cli("first symptom via the cli path", graph)
    _via_dispatch("second symptom via the dispatch path", graph)
    rows = retro_db.list_defect_investigations()
    assert len(rows) == 2
    assert {r["root_cause_class"] for r in rows} == {"under-spec"}
