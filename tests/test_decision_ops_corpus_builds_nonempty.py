"""Enforcing regression for the 2026-07-31 RECURRENCE of the composeux-architect staleness.

Prior attempt (2026-07-30, defect/decision-ops-corpus-path-missing) fixed the STARTUP path:
[LEC-CORPUS-PATH-MUST-EXIST] refuses to boot when DECISION_OPS_GRAPH is missing/empty-BYTES.
It explicitly FLAGGED, and deferred, the read-path residual that recurred here:

  - [LEC-CORPUS-BUILDS-NONEMPTY]: a configured file that EXISTS + is non-empty but BUILDS to
    ZERO nodes (path drifted to the wrong file / parser regression / `area` mismatch) sails
    past the byte guard and serves every query as an empty corpus. Root-caused live:
    `LtsRefresher().refresh(<non-empty prose-only .lts>)` -> `ok=True, total_nodes=0`, silent.

  - [LEC-READ-PATH-VANISHED-ROOT]: a read whose served partition's ROOT vanished answers
    `stale=True, index_hash=<real>, live_hash=sha256("")`. Root-caused live:
    `combined_files_hash(["<repo>/decisions"])` (the retired pre-reorg root) == sha256("")
    EXACTLY, because that dir holds no `.lts`. The engine's stale hint says
    `python -m factlattice_engine.build` — which cannot fix an in-process view whose root
    BINDING is wrong; the real remedy is repoint DECISION_OPS_GRAPH + relaunch.

These tests are RED before the mcp_server.py change (a non-empty zero-node file boots silently;
a vanished-root read returns the raw misleading hint) and GREEN after. The first test is the
one the defect brief asked for: the TRACKED composeux-architect graph builds >0 nodes with a
non-empty, fresh live_hash.
"""
from __future__ import annotations

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'python'))
from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS, INTELLIGENCE_CONSOLE_CORPUS_DIR as _ICC  # corpora live OUTSIDE the repo since 2026-08-17
import hashlib
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO / "python"))

_EMPTY_SHA = "sha256:" + hashlib.sha256(b"").hexdigest()  # e3b0c442...b855

_TRACKED_GRAPH = (
    _ICC / "decision-graph-composeux-architect.lts"
)


def _engine_or_skip():
    """Seat the lts-engine on sys.path as the MCP servers do; skip if the engine repo is
    absent in this environment (file-is-truth: the build check degrades, it does not fail)."""
    root = os.environ.get("FACTLATTICE_ENGINE_ROOT") or os.path.expanduser(
        "~/factlattice-internal-dev")
    if os.path.isdir(root) and root not in sys.path:
        sys.path.insert(0, root)
    try:
        import factlattice_engine.config  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"factlattice_engine not importable ({exc}); engine repo not present")


# --------------------------------------------------------------------------- #
# THE requested test: the TRACKED graph builds real content (catches ANY future
# cause — path drift, parser regression, area mismatch — that empties this corpus)
# --------------------------------------------------------------------------- #
def test_tracked_composeux_architect_graph_builds_nonempty_and_fresh() -> None:
    _engine_or_skip()
    from composeio.decision_ops.lts_refresh import LtsRefresher

    assert _TRACKED_GRAPH.is_file(), f"tracked canonical graph is missing: {_TRACKED_GRAPH}"
    assert _TRACKED_GRAPH.stat().st_size > 0

    res = LtsRefresher().refresh(str(_TRACKED_GRAPH))
    assert res.ok, res.message
    assert (res.total_nodes or 0) > 0, "tracked graph built to ZERO nodes"
    assert res.live_hash and res.live_hash != _EMPTY_SHA, "live_hash reconciled to sha256('')"
    assert res.fresh is True, res.message


# --------------------------------------------------------------------------- #
# [LEC-CORPUS-BUILDS-NONEMPTY]: bytes present but zero nodes -> loud startup fault
# --------------------------------------------------------------------------- #
def test_validate_refuses_nonempty_file_that_builds_zero_nodes(tmp_path: Path) -> None:
    _engine_or_skip()
    from composeio.decision_ops.mcp_server import CorpusPathError, validate_default_graph

    # non-empty bytes, but NO markdown header / [ID] node -> the engine extracts zero nodes
    # (a header line would itself become a node, so keep the body header-free).
    prose = tmp_path / "decision-graph-composeux-architect.lts"
    prose.write_text("---\ndomain: PLT.CUX\n---\nplain prose, no headers, no nodes here\n")
    assert prose.stat().st_size > 0  # passes the byte guard

    with pytest.raises(CorpusPathError) as ei:
        validate_default_graph(str(prose))
    msg = str(ei.value)
    assert "ZERO NODES" in msg and "DECISION_OPS_GRAPH" in msg


def test_validate_allows_tracked_graph_that_builds_nonempty() -> None:
    _engine_or_skip()
    from composeio.decision_ops.mcp_server import validate_default_graph

    if not _TRACKED_GRAPH.is_file():
        pytest.skip("tracked graph absent in this checkout")
    validate_default_graph(str(_TRACKED_GRAPH))  # no raise


# --------------------------------------------------------------------------- #
# [LEC-READ-PATH-VANISHED-ROOT]: a vanished-root read self-heals from the live
# path, or returns an ACCURATE remedy (never the raw factlattice_engine.build hint)
# --------------------------------------------------------------------------- #
def test_lts_query_vanished_root_self_heals_or_gives_accurate_remedy(tmp_path: Path) -> None:
    _engine_or_skip()
    from composeio.decision_ops.lts_refresh import LtsRefresher
    from composeio.decision_ops.mcp_server import DecisionOpsMcp

    graph = tmp_path / "decisions" / "decision-graph-composeux-architect.lts"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        "---\ndomain: PLT.CUX\n---\n# fixture\n\n"
        "### [DEC-FIXTURE-ALPHA] a fixture decision\n"
        "- lifecycle-position: decided\n- importance: medium\n"
    )

    refresher = LtsRefresher()
    ops = DecisionOpsMcp(default_graph=str(graph), refresher=refresher)

    # build the view while the file is present, then move it out (the reorg rename): the
    # cached partition's root now reads empty -> terminal stale with live_hash=sha256("").
    refresher.refresh(str(graph))
    graph.unlink()

    res = ops.dispatch("decision_lts_query", {"tool": "lts_find", "arguments": {"text": "fixture"}})

    # the configured path is gone, so self-heal cannot rebuild -> we MUST get the accurate
    # remedy, and it must NOT be the misleading engine on-disk-index build hint.
    assert res.get("remedy"), f"no vanished-root remedy attached: {res}"
    assert "DECISION_OPS_GRAPH" in res["remedy"]
    assert "relaunch" in res["remedy"].lower()
    assert "factlattice_engine.build" in res["remedy"]  # named only to say DO NOT run it
    assert "Do NOT run" in res["remedy"]
