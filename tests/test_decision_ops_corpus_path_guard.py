"""Enforcing guard for [LEC-CORPUS-PATH-MUST-EXIST].

A configured DEFAULT corpus/graph path (`DECISION_OPS_GRAPH`) that is MISSING or EMPTY must
be a loud STARTUP fault, never silently reconciled to `sha256("")` -> terminal stale.

This is the 2026-07-30 defect: the corpora reorg (7daf9ef) renamed
`decisions/decision-graph-composeux-architect.lts` -> `corpora/intelligence-console/`, but the
gitignored `.mcp.json` still pointed `DECISION_OPS_GRAPH` at the retired `decisions/` path. The
running decision-ops server had built its partition while the file existed there; after the move
every `decision_lts_query` answered `success=False, stale=True, index_hash=<real>,
live_hash=sha256("")` -- terminal stale that no rebuild could clear.

`test_reproduces_terminal_stale_when_file_vanishes` demonstrates WHY the guard is needed (the
divergence is real, and it does NOT self-heal). The `validate_default_graph` tests are the
DETECTION guard: RED before it existed (a missing/empty default booted silently), GREEN after
(the fault is detected loud). Per [LEC-DECISION-OPS-BOOTS-AND-REPORTS] (2026-07-31) the RESPONSE
to that detected fault is reconciled: the server BOOTS (exit 0) and reports it per tool call
rather than exiting — an exiting stdio MCP server collapses to an opaque -32000 the operator
cannot act on (see `test_decision_ops_boots_under_bad_corpus.py`).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from composeio.decision_ops.lts_refresh import LtsRefresher
from composeio.decision_ops.mcp_server import (
    CorpusPathError,
    main,
    validate_default_graph,
)

_EMPTY_SHA = "sha256:" + hashlib.sha256(b"").hexdigest()  # e3b0c442...

_FIXTURE = """\
---
domain: PLT.CUX
---

# fixture decision graph

### [DEC-FIXTURE-ALPHA] a fixture decision
- lifecycle-position: decided
- importance: medium
"""


def _write_graph(dirpath: Path) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    g = dirpath / "decision-graph-composeux-architect.lts"
    g.write_text(_FIXTURE)
    return g


# --------------------------------------------------------------------------- #
# WHY the guard is needed: the terminal-stale divergence is real + does not heal
# --------------------------------------------------------------------------- #
def test_reproduces_terminal_stale_when_file_vanishes(tmp_path: Path) -> None:
    """Build a partition from a present file, then remove the file (the reorg rename):
    every subsequent read is STALE with live_hash=sha256("") and does NOT self-heal."""
    graph = _write_graph(tmp_path / "decisions")
    r = LtsRefresher()
    built = r.refresh(str(graph))
    assert built.total_nodes and built.fresh  # fresh-by-construction while present

    graph.unlink()  # the corpus reorg moved the file out from under the running server

    first = r.query(graph_path=str(graph), tool="lts_find", text="fixture")
    assert first.get("stale") is True
    assert first.get("live_hash") == _EMPTY_SHA  # reconciled-to-empty: the defect signature

    # heal-on-next-read cannot fire -- there is no file to rebuild from
    second = r.query(graph_path=str(graph), tool="lts_find", text="fixture")
    assert second.get("stale") is True
    assert second.get("live_hash") == _EMPTY_SHA


# --------------------------------------------------------------------------- #
# the guard itself
# --------------------------------------------------------------------------- #
def test_guard_refuses_missing_default_graph(tmp_path: Path) -> None:
    missing = tmp_path / "decisions" / "decision-graph-composeux-architect.lts"
    with pytest.raises(CorpusPathError) as ei:
        validate_default_graph(str(missing))
    msg = str(ei.value)
    assert "MISSING" in msg and str(missing) in msg and "DECISION_OPS_GRAPH" in msg


def test_guard_refuses_empty_default_graph(tmp_path: Path) -> None:
    empty = tmp_path / "decision-graph-composeux-architect.lts"
    empty.write_text("")
    with pytest.raises(CorpusPathError):
        validate_default_graph(str(empty))


def test_guard_allows_present_nonempty_graph(tmp_path: Path) -> None:
    graph = _write_graph(tmp_path / "corpora" / "intelligence-console")
    validate_default_graph(str(graph))  # no raise


def test_guard_allows_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # no DECISION_OPS_GRAPH -> callers pass explicit graph per op; nothing to validate
    validate_default_graph(None)


def test_main_boots_and_reports_degraded_on_missing_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """[LEC-DECISION-OPS-BOOTS-AND-REPORTS] — reconciles the prior exit-2 enforcement.

    A missing default corpus is still DETECTED loud, but a stdio MCP server must NOT exit on it
    (exiting -> the client sees the process die -> opaque -32000 that a restart cannot clear).
    `main` boots (would call serve) and logs the fault as DEGRADED. We stub `serve` so the test
    does not block on stdin, and assert the boot path is taken (exit 0) with a diagnostic naming
    the missing path — NOT a FATAL exit-2.
    """
    missing = tmp_path / "decisions" / "decision-graph-composeux-architect.lts"
    monkeypatch.setenv("DECISION_OPS_GRAPH", str(missing))
    import composeio.decision_ops.mcp_server as m
    monkeypatch.setattr(m, "serve", lambda *a, **k: None)  # don't block on stdin
    rc = main([])
    assert rc == 0  # boots, never exits on a config fault (was rc==2, the -32000 regression)
    err = capsys.readouterr().err
    assert "config fault" in err and "DEGRADED" in err and str(missing) in err
    assert "FATAL" not in err
