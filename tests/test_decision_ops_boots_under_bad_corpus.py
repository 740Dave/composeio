"""Enforcing guard for [LEC-DECISION-OPS-BOOTS-AND-REPORTS] — the 2026-07-31 -32000 defect.

Symptom: the `decision-ops` MCP server returned -32000 on every `/mcp reconnect`, and a full
Claude Code quit + relaunch did NOT clear it. It was NOT a Claude-Code-side transient (that prior
conclusion was falsified by the persistence across a full restart).

Root cause (spec-drift): [LEC-CORPUS-PATH-MUST-EXIST]'s original enforcement had `main()` print
FATAL and `return 2` on a missing default corpus. The GOVERNING config is the HOME-level
`~/.mcp.json` (loaded because the Claude Code session cwd is the home dir, not the repo root),
whose `DECISION_OPS_GRAPH` still named the RETIRED pre-reorg `decisions/…` path (the 7daf9ef reorg
moved the graph to `corpora/intelligence-console/` and swept the in-repo `.mcp.json`, but not the
gitignored home copy). So the server launched with a missing default -> exit 2 -> the MCP client
observed the process die -> opaque -32000, persistent because the config is persistently wrong.

RED before the fix: `main([])` with a missing `DECISION_OPS_GRAPH` returns 2 (the process dies ->
-32000) and `DecisionOpsMcp` has no way to report the fault per call.
GREEN after: `main` returns 0 (boots, stays alive) and a graph tool call returns the actionable
`config_fault` diagnostic naming `DECISION_OPS_GRAPH` and the missing path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from composeio.decision_ops import mcp_server as m
from composeio.decision_ops.mcp_server import DecisionOpsMcp, main


_MISSING_REL = "decisions/decision-graph-composeux-architect.lts"  # the retired pre-reorg path


# --------------------------------------------------------------------------- #
# The launch always BOOTS (exit 0) — never exit-2 -> -32000
# --------------------------------------------------------------------------- #
def test_main_boots_exit_zero_when_default_corpus_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    missing = tmp_path / _MISSING_REL
    monkeypatch.setenv("DECISION_OPS_GRAPH", str(missing))
    monkeypatch.setattr(m, "serve", lambda *a, **k: None)  # don't block on stdin

    rc = main([])

    assert rc == 0, "server must BOOT on a config fault, never exit (exit != 0 -> -32000)"
    err = capsys.readouterr().err
    assert "config fault" in err and "DEGRADED" in err
    assert "FATAL" not in err  # the old fail-loud that regressed to fail-silent(-32000)


# --------------------------------------------------------------------------- #
# The alive server reports the fault PER TOOL CALL — actionable, not a dead pipe
# --------------------------------------------------------------------------- #
def test_graph_tool_call_returns_actionable_config_fault(tmp_path: Path) -> None:
    missing = tmp_path / _MISSING_REL
    ops = DecisionOpsMcp(default_graph=str(missing))

    assert ops.default_graph_fault, "the missing default must be detected + carried"

    res = ops.dispatch("decision_lts_query",
                       {"tool": "lts_find", "arguments": {"text": "anything"}})

    assert res["success"] is False
    assert res.get("config_fault") is True
    # the diagnostic must be ACTIONABLE: name the env var and the missing path so the operator
    # knows the exact fix (repoint DECISION_OPS_GRAPH), instead of an opaque transport error.
    assert "DECISION_OPS_GRAPH" in res["error"]
    assert str(missing) in res["error"]


def test_explicit_graph_arg_overrides_the_faulted_default(tmp_path: Path) -> None:
    """A caller that passes an explicit valid `graph` is NOT blocked by a faulted default —
    the config_fault short-circuit only fires for calls that would use the default corpus."""
    missing = tmp_path / _MISSING_REL
    ops = DecisionOpsMcp(default_graph=str(missing))
    assert ops.default_graph_fault

    # explicit graph present -> the short-circuit must NOT fire (it proceeds to the handler,
    # which may then fail for its own reasons, but NOT with our config_fault marker).
    res = ops.dispatch("decision_query", {"graph": str(tmp_path / "other.lts"),
                                          "selector": "*"})
    assert res.get("config_fault") is not True


def test_send_dsd_is_not_blocked_by_default_corpus_fault(tmp_path: Path) -> None:
    """`send_dsd_for_human_review` is graph-independent; a faulted default corpus must not
    short-circuit it (it is not in _GRAPH_TOOLS)."""
    missing = tmp_path / _MISSING_REL
    ops = DecisionOpsMcp(default_graph=str(missing))
    assert ops.default_graph_fault

    res = ops.dispatch("send_dsd_for_human_review", {"dsd_path": str(tmp_path / "nope.md")})
    # it runs its own handler (and fails on the missing DSD), but NEVER with our config_fault.
    assert res.get("config_fault") is not True


def test_healthy_default_has_no_fault(tmp_path: Path) -> None:
    """Control: a present, non-empty graph yields no fault and no per-call short-circuit.

    Uses a fixture with a real node so the build-nonempty check passes even when the engine is
    present; degrades gracefully (no fault) when the engine is absent.
    """
    graph = tmp_path / "corpora" / "intelligence-console" / "decision-graph.lts"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        "---\ndomain: PLT.CUX\n---\n# fixture\n\n"
        "### [DEC-FIXTURE-ALPHA] a fixture decision\n"
        "- lifecycle-position: decided\n- importance: medium\n"
    )
    ops = DecisionOpsMcp(default_graph=str(graph))
    assert ops.default_graph_fault is None
