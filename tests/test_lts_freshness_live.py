"""Enforcing regression for the STALE-CODE / live-freshness defect (2026-07-24).

THE DEFECT ([INSIGHT-STALE-CODE-IS-A-FRESHNESS-CLASS]): a decision write lands in the
source `.lts`, but `lts_find`/`lts_node` and the boot-read returned STALE until a manual
process BOUNCE. Root cause was stale CODE (the running `factlattice_engine.server` predated
the propagated `[MECH-SELFHEAL]` self-heal), NOT a stale file — the `.lts` file is the
single source of truth and the engine's filesystem-reconcile is the GUARANTEED freshness
path. David's acceptance bar: a decision write must be queryable on a LIVE process with NO
manual restart in between.

`test_write_then_live_query_is_fresh_without_restart` is the acceptance test verbatim: it
boots the engine EXACTLY as `factlattice_engine.server.build_tools` does (the deployed
composition root — kernel watch + self-heal + poll-verified freshness), writes a NEW node
to a live corpus file, and queries it back with NO restart. It fails against any engine
build that drops the file-reconcile guarantee (e.g. the pre-`[MECH-SELFHEAL]` snapshot the
live process was running), and passes against the current one.

`test_refresh_push_failure_degrades_to_file_reconcile_not_silence` pins the decision-ops
half: the best-effort in-process `magma_refresh` failing (the live `ImportError: MagmaStore`
from stale code) must NOT be reported as a write/propagation failure — it degrades to the
guaranteed file-reconcile and flags the stale-code condition loudly.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))


def _seat_engine_on_path():
    """Put the lts-engine (factlattice_engine) on sys.path the way the MCP servers do.

    Honors FACTLATTICE_ENGINE_ROOT (the .mcp.json env), else the default deployment root.
    Skips the whole module if the engine is not importable in this environment.
    """
    root = os.environ.get("FACTLATTICE_ENGINE_ROOT") or os.path.expanduser(
        "~/factlattice-internal-dev")
    if os.path.isdir(root) and root not in sys.path:
        sys.path.insert(0, root)
    try:
        import factlattice_engine.server  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"factlattice_engine not importable ({exc}); engine repo not present")


def _write_corpus(tmp: str):
    """A corpus that mirrors the deployed shape: two `only_file` roots on the SAME
    decisions/ directory (the real config pins 5), plus a specs root."""
    decisions = os.path.join(tmp, "decisions")
    specs = os.path.join(tmp, "specs")
    os.makedirs(decisions)
    os.makedirs(specs)
    graph = os.path.join(decisions, "decision-graph-x.lts")
    with open(graph, "w", encoding="utf-8") as f:
        f.write("### [DEC-ALPHA] first decision\n- lifecycle-position: decided\n")
    with open(os.path.join(decisions, "principles.lts"), "w", encoding="utf-8") as f:
        f.write("### [PRIN-ONE] a principle\n- kind: rule\n")
    with open(os.path.join(specs, "10-spec.lts"), "w", encoding="utf-8") as f:
        f.write("### [SPEC-ONE] a spec\n- status: draft\n")
    cfg = {"projx": {"roots": [
        {"path": decisions, "area": "decisions", "only_file": "decision-graph-x.lts"},
        {"path": decisions, "area": "decisions", "only_file": "principles.lts"},
        {"path": specs, "area": "specs"}], "domain_suffix": "proj.projx"}}
    cfgpath = os.path.join(tmp, "corpus-config.json")
    with open(cfgpath, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return graph, cfgpath


def test_write_then_live_query_is_fresh_without_restart():
    """THE ACCEPTANCE BAR: write a node, then query it live — FRESH, no restart between."""
    _seat_engine_on_path()
    from factlattice_engine import server, tools as tmod

    with tempfile.TemporaryDirectory(prefix="lts-live-") as tmp:
        graph, cfgpath = _write_corpus(tmp)
        os.environ["FACTLATTICE_CORPUS_CONFIG"] = cfgpath
        os.environ["FACTLATTICE_CORPUS_ROOT"] = tmp

        # The deployed composition root: kernel watch + self-heal + poll-verified freshness.
        engine = server.build_tools(watch=True)

        def node(nid):
            return tmod.dispatch("lts_node", {"corpus": "projx", "node_id": nid},
                                 tools=engine)

        # Baseline: the seeded node is present and fresh.
        r0 = node("DEC-ALPHA")
        assert r0.get("success") is True and r0.get("node"), r0

        # WRITE a brand-new node to the live corpus file. NO restart follows.
        with open(graph, "a", encoding="utf-8") as f:
            f.write("### [DEC-BETA] second decision\n- lifecycle-position: raised\n")

        # Query it back on the SAME live engine — must reconcile from the file and serve it.
        r1 = node("DEC-BETA")
        assert r1.get("success") is True, f"live query failed / STALE with no restart: {r1}"
        assert not r1.get("stale"), f"served a STALE marker instead of self-healing: {r1}"
        assert r1.get("node") and r1["node"].get("id") == "DEC-BETA", (
            f"the freshly-written node was NOT visible on the live process: {r1}")


def test_refresh_push_failure_degrades_to_file_reconcile_not_silence(tmp_path, monkeypatch):
    """decision-ops: a stale-code `ImportError` in the best-effort in-process refresh must
    degrade to the guaranteed file-reconcile + flag stale-code — never mask the write."""
    from composeio.decision_ops.lts_refresh import LtsRefresher

    r = LtsRefresher()

    def boom(*_a, **_k):
        raise ImportError("cannot import name 'MagmaStore' from 'factlattice_engine.store'")

    monkeypatch.setattr(r, "_build_tools", boom)

    graph = tmp_path / "decision-graph-x.lts"
    graph.write_text("### [DEC-ALPHA] a decision\n- lifecycle-position: decided\n")

    res = r.refresh(str(graph)).to_dict()
    # The refresh itself failed (stale code) ...
    assert res["ok"] is False and res["refreshed"] is False
    # ... but it is NOT silence: it degraded to the file-reconcile and named the class.
    assert res["degraded_to_file_reconcile"] is True, res
    assert res["stale_code_suspected"] is True, res
    assert "durable" in res["message"] and "file" in res["message"].lower(), res
