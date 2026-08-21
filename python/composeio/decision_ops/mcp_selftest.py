"""Acceptance self-test for the decision-ops MCP server + lts-engine refresh-on-write.

Operates ENTIRELY on a TEMP COPY of a synthetic decision graph — NEVER on
`decisions/decision-graph-composeux-architect.lts` (a byte-hash tripwire on the real
file asserts it is never touched, mirroring `selftest.py`'s discipline).

Proves the build brief's acceptance bar:
  (a) add a decision via the MCP TOOL (through `DecisionOpsMcp.dispatch`, the same seam
      the stdio server calls) to the temp-copy graph;
  (b) IMMEDIATELY query the lts-engine and get the new node back — proving no stale window (the
      refreshed view is fresh-by-construction), and separately proving the content-hash
      freshness kernel CATCHES an un-refreshed write as a loud STALE (never old data);
  (c) a DangleRefusedError write pushes NO refresh AND leaves the graph byte-identical,
      and the bad node is never queryable through the lts-engine.

Also smoke-tests the JSON-RPC stdio shell (initialize / tools/list / tools/call).

Run::  python3 -m composeio.decision_ops.mcp_server --selftest
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from .lts_refresh import LtsRefresher, corpus_id_for_graph
from .mcp_server import Server, DecisionOpsMcp

from composeio.bootstrap_paths import INTELLIGENCE_CONSOLE_CORPUS_DIR as _IC_CORPUS_DIR
_REAL_GRAPH = str(_IC_CORPUS_DIR / "decision-graph-composeux-architect.lts")

FIXTURE_GRAPH = """\
---
domain: PLT.CUX
confidentiality: internal-only
artifact-readiness: system-design
created: 2026-07-01
---
# Fixture Decision Graph [SPEC:FIXTURE-MCP-DECISIONS]

> Synthetic fixture for the decision-ops MCP server tests. Never the real graph.

### [DEC-MCPFIX-ROOT] A root fixture decision so nothing here starts as a dangle
- lifecycle-position: committed
- decided-in-session: 2026-07-01
- importance: medium
- rationale: exists so add()/update()/resolve() targets have something to point at.

### [DEC-MCPFIX-SECOND] A second fixture decision that depends on the root
- lifecycle-position: decided
- decided-in-session: 2026-07-01
- importance: low
- rationale: a second node to prove query filtering and multi-node behavior.
- depends-on: [DEC-MCPFIX-ROOT]
"""


class _Checker:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, claim: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        line = f"  [{mark}] {claim}"
        if detail:
            line += f"  ({detail})"
        print(line)


def _sha(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run() -> int:
    c = _Checker()
    print("decision-ops MCP server + lts-engine refresh selftest (temp-copy fixture only)")

    real_hash_before = _sha(_REAL_GRAPH) if os.path.isfile(_REAL_GRAPH) else None

    with tempfile.TemporaryDirectory(prefix="decision-ops-mcp-") as tmp:
        path = Path(tmp) / "decision-graph-mcpfix.lts"
        path.write_text(FIXTURE_GRAPH, encoding="utf-8")
        graph = str(path)
        cid = corpus_id_for_graph(graph)
        c.check("temp graph is NOT the real graph", os.path.abspath(graph) != os.path.abspath(_REAL_GRAPH))
        c.check("derived corpus_id from filename", cid == "mcpfix", f"got {cid!r}")

        ops = DecisionOpsMcp(default_graph=graph, profile=None)

        # A SEPARATE lts-engine view built from PRE-write bytes, to prove the freshness kernel
        # catches an un-refreshed write as a loud STALE (never serving old data).
        stale_probe = LtsRefresher()
        pre = stale_probe.query(graph_path=graph, tool="lts_find", prefix="DEC")
        c.check("pre-write lts-engine view is fresh and has the 2 seed nodes",
                pre.get("success") and pre.get("count") == 2, json.dumps(pre)[:200])

        # -- (a) add via the MCP tool -----------------------------------------
        print("\n(a) decision_add via MCP dispatch")
        res = ops.dispatch("decision_add", {
            "id": "DEC-MCPFIX-NEW", "title": "A newly added fixture decision via MCP",
            "lifecycle_position": "raised", "decided_in_session": "2026-07-22",
            "importance": "medium",
            "rationale": "added by the decision-ops MCP selftest, refresh-on-write proof.",
            "fields": {"depends-on": "[DEC-MCPFIX-ROOT]"}})
        c.check("decision_add succeeded", res.get("success") is True, json.dumps(res)[:200])
        c.check("decision_add reports changed=True", res.get("changed") is True)
        c.check("decision_add pushed an lts-engine refresh", isinstance(res.get("engine_refresh"), dict))
        rr = res.get("engine_refresh") or {}
        c.check("refresh view is fresh-by-construction (no stale window)", rr.get("fresh") is True, json.dumps(rr))
        # 3 DEC nodes + the 1 [SPEC:...] document-heading node = 4 in the rebuilt index.
        c.check("refresh view now holds all 4 nodes (3 DEC + SPEC doc)",
                rr.get("total_nodes") == 4, str(rr.get("total_nodes")))

        # -- (b) IMMEDIATELY query the lts-engine and get the new node back ------------
        print("\n(b) query the lts-engine immediately")
        mq = ops.dispatch("decision_lts_query", {
            "tool": "lts_find", "arguments": {"prefix": "DEC"}})
        ids = [n["id"] for n in mq.get("nodes", [])]
        c.check("lts-engine lts_find returns the new node with NO stale window",
                mq.get("success") is True and "DEC-MCPFIX-NEW" in ids, f"ids={ids}")
        node_q = ops.dispatch("decision_lts_query", {
            "tool": "lts_node", "arguments": {"node_id": "DEC-MCPFIX-NEW"}})
        c.check("lts_node fetches the new node's properties",
                node_q.get("success") is True and node_q.get("node")
                and node_q["node"].get("properties", {}).get("lifecycle-position") == "raised",
                json.dumps(node_q)[:160])

        # And prove the pre-write view (un-refreshed) is now caught as STALE.
        post = stale_probe.query(graph_path=graph, tool="lts_find", prefix="DEC")
        c.check("un-refreshed pre-write view is caught STALE (freshness kernel, not old data)",
                post.get("success") is False and post.get("stale") is True, json.dumps(post)[:200])

        # -- update + resolve also refresh ------------------------------------
        print("\n(update/resolve refresh)")
        upd = ops.dispatch("decision_update", {
            "node_id": "DEC-MCPFIX-NEW", "fields": {"lifecycle-position": "committed"}})
        c.check("decision_update refreshed the lts-engine", upd.get("changed") is True
                and isinstance(upd.get("engine_refresh"), dict))
        mq2 = ops.dispatch("decision_lts_query", {
            "tool": "lts_node", "arguments": {"node_id": "DEC-MCPFIX-NEW"}})
        c.check("lts-engine reflects the updated lifecycle-position immediately",
                mq2["node"]["properties"].get("lifecycle-position") == "committed")

        rsv = ops.dispatch("decision_resolve", {
            "node_id": "DEC-MCPFIX-SECOND", "superseded_by": "DEC-MCPFIX-NEW",
            "note": "fixture supersession"})
        c.check("decision_resolve refreshed the lts-engine", rsv.get("changed") is True
                and isinstance(rsv.get("engine_refresh"), dict))

        # idempotent no-op pushes NO refresh
        noop = ops.dispatch("decision_update", {
            "node_id": "DEC-MCPFIX-NEW", "fields": {"lifecycle-position": "committed"}})
        c.check("idempotent no-op write pushes NO refresh",
                noop.get("changed") is False and noop.get("engine_refresh") is None)

        # -- (c) DangleRefusedError -> NO refresh, byte-identical graph -------
        print("\n(c) refused write pushes no refresh, graph byte-identical")
        before_bytes = path.read_bytes()
        before_hash = hashlib.sha256(before_bytes).hexdigest()
        bad = ops.dispatch("decision_add", {
            "id": "DEC-MCPFIX-BAD", "title": "A bad decision pointing nowhere",
            "lifecycle_position": "raised", "decided_in_session": "2026-07-22",
            "importance": "low",
            "rationale": "deliberately dangles, to prove refusal + no-refresh.",
            "fields": {"depends-on": "[DEC-DOES-NOT-EXIST-ANYWHERE]"}})
        c.check("refused add returns success=False + refused=True",
                bad.get("success") is False and bad.get("refused") is True, json.dumps(bad)[:200])
        c.check("refused add names the new forward dangle",
                "forward dangle" in (bad.get("error") or ""))
        c.check("refused add pushed NO refresh (engine_refresh is None)",
                bad.get("engine_refresh") is None)
        after_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        c.check("graph is BYTE-IDENTICAL after the refused write",
                after_hash == before_hash, f"{before_hash[:12]} vs {after_hash[:12]}")
        badq = ops.dispatch("decision_lts_query", {
            "tool": "lts_node", "arguments": {"node_id": "DEC-MCPFIX-BAD"}})
        c.check("the bad node is NOT queryable through the lts-engine",
                badq.get("node") is None, json.dumps(badq)[:160])

        # -- JSON-RPC stdio shell smoke ---------------------------------------
        print("\n(stdio JSON-RPC shell smoke)")
        srv = Server(DecisionOpsMcp(default_graph=graph, profile=None))
        init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        c.check("initialize returns protocolVersion",
                init["result"]["protocolVersion"] == "2024-11-05")
        tl = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in tl["result"]["tools"]}
        c.check("tools/list advertises the 5 decision-ops tools",
                {"decision_add", "decision_update", "decision_query",
                 "decision_resolve", "decision_lts_query"} <= names, str(names))
        call = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "decision_query",
                                      "arguments": {"selector": "prefix:DEC"}}})
        payload = json.loads(call["result"]["content"][0]["text"])
        c.check("tools/call decision_query round-trips through the shell",
                payload.get("success") and payload.get("count") == 3, json.dumps(payload)[:160])
        c.check("notifications/* get no response",
                srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)

    # -- real-graph tripwire --------------------------------------------------
    if real_hash_before is not None:
        c.check("REAL decision graph untouched (byte-hash unchanged)",
                _sha(_REAL_GRAPH) == real_hash_before)
    else:
        print("  [note] real decision graph not present; tripwire skipped")

    print()
    if c.failures:
        print(f"MCP SELFTEST: FAIL — {c.failures} check(s) failed")
        return 1
    print("MCP SELFTEST: PASS — all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(run())
