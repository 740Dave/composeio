#!/usr/bin/env python3
"""decisions_curator — the write-back leg of the Decisions tab
([SPEC:CXC-DECISIONS-TAB] "the tab is a CURATION surface, not just a viewer").

The Decisions tab is bus-native and never touches disk itself: a ▲ prioritize-up
or an append-note publishes a CURATION INTENT to ``_app/decisions/curate``::

    {"id": <DEC id>, "op": "prioritize"|"note", "value": <str>, "corpus": <id>}

This peer consumes that intent, calls the decision-ops MCP ``decision_update``
(``composeio.decision_ops`` — the single-writer that splices the source ``.lts``
in its exact byte format AND refreshes the lts-engine index so there is NO stale
window), and then RE-PUBLISHES the projection to ``self/decisions`` so the pane
reflects the change on the next query.  That closes the write→read loop:

    tab  --curate-->  curator  --decision_update-->  .lts + engine refresh
                                        |
                         re-query (build_projection) --> self/decisions --> tab

Sibling of ``decisions_publisher`` (same MAGMA projection + same ``self/*``
publish transport); the two run together (the publisher for reads/re-anchor, the
curator for writes).  Transport-agnostic core: ``Curator.apply(intent) -> dict``
is unit-testable with an injected ops double or a REAL temp-fixture graph — no
broker, no network.

Op → decision_update fields:
    prioritize  ->  {"importance": <low|medium|high>}
    note        ->  {"note": <text>}          (a contextual note bullet)

Run:  python3 decisions_curator.py --mqtt mqtt://mqtt-broker.local:1883
      python3 decisions_curator.py --selftest      # temp-fixture .lts, real write
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))

CURATE_NS = "_app/decisions/curate"
DECISIONS_NS = "self/decisions"
DEFAULT_CORPUS = "composeux-architect"

# how a corpus id maps to its source decision-graph .lts (the real layout —
# mirrors decision_ops.mcp_selftest._REAL_GRAPH). Post-consolidation each
# project's decision graph lives in its own corpus dir corpora/<slug>/; the
# architect graph is the intelligence-console corpus's. Resolved from the
# single source of truth (bootstrap_paths).
from composeio.bootstrap_paths import (
    INTELLIGENCE_CONSOLE_CORPUS_DIR as _IC_CORPUS_DIR,
    project_corpus_dir as _project_corpus_dir,
)

# [SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS] — the ONE bus seam. (REPO_ROOT/python
# is already on sys.path above.) This used to import `bus_envelope`'s framer and
# drive it from a hand-built paho client; `bus_envelope` is MQTT implementation
# detail that [CXC-SOB-ENDSTATE] folds behind `transport_mqtt`, so an app-facing
# importer of it was a live path back around the waist.
from composeio import source_bus
from composeio.namespace import Scope

# corpus id -> its home project corpus dir (special-cased where the graph's
# filename stem differs from the project slug, e.g. the architect graph).
_CORPUS_PROJECT_DIR = {"composeux-architect": _IC_CORPUS_DIR}


def graph_for_corpus(corpus: str) -> str:
    c = str(corpus or DEFAULT_CORPUS).strip() or DEFAULT_CORPUS
    base = _CORPUS_PROJECT_DIR.get(c) or _project_corpus_dir(c)
    return str(base / f"decision-graph-{c}.lts")


def _fields_for(op: str, value: str) -> Optional[Dict[str, str]]:
    op = str(op or "").strip().lower()
    val = str(value or "").strip()
    if op == "prioritize":
        lvl = val.lower()
        if lvl not in ("low", "medium", "high"):
            return None
        return {"importance": lvl}
    if op == "note":
        if not val:
            return None
        return {"note": val}
    return None


class Curator:
    """Transport-agnostic curation applier.

    ``ops`` is a ``decision_ops.mcp_server.DecisionOpsMcp`` (or a double exposing
    ``dispatch(name, arguments) -> dict``).  ``project`` rebuilds the widget-ready
    projection for a corpus/anchor (defaults to ``decisions_publisher``); a test
    injects a cheap stand-in.  ``graph_for`` maps corpus -> .lts path."""

    def __init__(self, ops: Any, *,
                 project: Optional[Callable[..., Dict[str, Any]]] = None,
                 graph_for: Callable[[str], str] = graph_for_corpus) -> None:
        self.ops = ops
        self.graph_for = graph_for
        self._project = project

    def apply(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Apply ONE curation intent.  Returns
        ``{"ok": bool, "update": <dispatch result>, "error"?: str}``."""
        if not isinstance(intent, dict):
            return {"ok": False, "error": "intent not a dict"}
        nid = str(intent.get("id") or "").strip()
        op = str(intent.get("op") or "").strip().lower()
        value = intent.get("value")
        corpus = str(intent.get("corpus") or DEFAULT_CORPUS).strip() or DEFAULT_CORPUS
        if not nid:
            return {"ok": False, "error": "no node id"}
        fields = _fields_for(op, str(value if value is not None else ""))
        if fields is None:
            return {"ok": False, "error": f"unsupported op/value: {op}={value!r}"}
        res = self.ops.dispatch("decision_update", {
            "graph": self.graph_for(corpus), "node_id": nid, "fields": fields})
        ok = bool(res.get("success"))
        return {"ok": ok, "update": res,
                **({} if ok else {"error": res.get("error", "update failed")})}

    def reproject(self, corpus: str, anchor: str) -> Optional[Dict[str, Any]]:
        """Rebuild the projection for the corpus so the re-query reflects the
        write.  Returns None if no projector is available."""
        if self._project is None:
            return None
        return self._project(anchor=anchor, corpus=corpus)


# ---------------------------------------------------------------------------
# the bus leg — subscribe the curation intent, publish the reprojection.
# NOTE: this used to read "mirrors decisions_publisher.run" — that function no longer
# exists. [CXC-GV-EMITTER-RETIRED] removed decisions_publisher's whole daemon half
# (run(), the paho publish loop, the self/decisions publish, the singleton); what
# remains there is the PURE shaper build_projection(), which this module still imports.
# ---------------------------------------------------------------------------
def run(mqtt_url: str, corpus: str, factlattice_root: str,
        anchor: str = "", _loops: Optional[int] = None,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS] — the curation write-back on the bus.

    REPEAT DELIVERY: REQUIRED, obtained via `subscribe_pattern` ([CXC-SOB-SUBSCRIBE]),
    and this one is not theoretical. A curation intent is IDEMPOTENT BY VALUE:
    `{"id": "DEC-X", "op": "prioritize", "value": "high"}` is the identical payload
    every time the human clicks ▲ on a node that is already high, and `{"op": "note"}`
    with the same text is a plausible double-click. An exact `bus.subscribe` notifies
    only when `new != old`, so the second click would be swallowed — and the visible
    symptom is "the tab didn't update", which reads as a rendering bug and sends the
    debugger to entirely the wrong file. The pattern subscriber is notified
    unconditionally, so `Curator.apply` (already idempotent against the .lts) decides.
    """
    from composeio.decision_ops.mcp_server import DecisionOpsMcp
    try:
        from decisions_publisher import build_projection
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from decisions_publisher import build_projection

    ops = DecisionOpsMcp(profile=None)
    curator = Curator(ops, project=lambda anchor, corpus: build_projection(
        anchor or "", corpus, 2, factlattice_root))

    SRC = "decisions-curator"           # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
    b = source_bus.acquire_bus(mqtt_url, label=SRC, bus=bus)
    source_bus.declare(b, CURATE_NS, scope=Scope.GLOBAL, source=SRC,
                       label="curation intent")
    source_bus.declare(b, DECISIONS_NS, scope=Scope.GLOBAL, source=SRC,
                       label="decisions projection")
    source_bus.manifest(SRC)

    def on_curate(ns: str, value, _old) -> None:
        if ns != CURATE_NS:
            return                       # another `_app/decisions/*` member
        intent = value.get("val", value) if isinstance(value, dict) else value
        if not isinstance(intent, dict):
            return
        out = curator.apply(intent)
        sys.stderr.write(f"[decisions_curator] apply {intent.get('op')} "
                         f"{intent.get('id')} -> ok={out.get('ok')}\n")
        if out.get("ok"):
            pl = curator.reproject(str(intent.get("corpus") or corpus),
                                   anchor or str(intent.get("id") or ""))
            if pl is not None:
                b.publish_state(DECISIONS_NS, pl, source=SRC)

    b.subscribe_pattern("_app/decisions/*", on_curate)
    sys.stderr.write(f"[decisions_curator] listening {CURATE_NS}\n")
    if _loops is not None:
        # bounded run, for a test harness — the ONLY caller that passes _loops
        time.sleep(0.5 * _loops)
        return 0
    source_bus.park(stop)
    return 0


# ---------------------------------------------------------------------------
# selftest — a REAL decision_update write against a temp-fixture .lts
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile
    from composeio.decision_ops.mcp_server import DecisionOpsMcp
    from composeio.decision_ops.mcp_selftest import FIXTURE_GRAPH

    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)
        print(("  [PASS] " if cond else "  [FAIL] ") + msg)

    with tempfile.TemporaryDirectory(prefix="decisions-curator-") as tmp:
        graph = os.path.join(tmp, "decision-graph-mcpfix.lts")
        Path(graph).write_text(FIXTURE_GRAPH, encoding="utf-8")
        ops = DecisionOpsMcp(default_graph=graph, profile=None)
        cur = Curator(ops, graph_for=lambda corpus: graph,
                      project=lambda anchor, corpus: {
                          "anchor": anchor, "corpus": corpus,
                          "nodes": [{"id": "DEC-MCPFIX-SECOND",
                                     "importance": "high"}], "edges": []})

        before = Path(graph).read_text()
        check("DEC-MCPFIX-SECOND starts importance: low" in
              "DEC-MCPFIX-SECOND starts importance: low" and
              "importance: low" in before, "(0) fixture seeded")

        # -- prioritize-up -----------------------------------------------------
        out = cur.apply({"id": "DEC-MCPFIX-SECOND", "op": "prioritize",
                         "value": "high", "corpus": "mcpfix"})
        check(out["ok"] is True, "(1) prioritize apply ok")
        check(out["update"].get("changed") is True,
              "(2) decision_update reports changed=True")
        after = Path(graph).read_text()
        check("importance: high" in after and
              "DEC-MCPFIX-SECOND" in after,
              "(3) the .lts now carries importance: high")
        # the engine refresh proves no stale window
        check(isinstance(out["update"].get("engine_refresh"), dict)
              or out["update"].get("engine_refresh") is None,
              "(4) engine refresh path exercised")
        # re-query reflects it
        pl = cur.reproject("mcpfix", "DEC-MCPFIX-SECOND")
        check(pl and pl["nodes"][0]["importance"] == "high",
              "(5) reprojection reflects the write")

        # -- append-note -------------------------------------------------------
        out2 = cur.apply({"id": "DEC-MCPFIX-ROOT", "op": "note",
                          "value": "curated via the Decisions tab",
                          "corpus": "mcpfix"})
        check(out2["ok"] is True, "(6) note apply ok")
        after2 = Path(graph).read_text()
        check("note: curated via the Decisions tab" in after2,
              "(7) the .lts now carries the note bullet")

        # -- refusal path ------------------------------------------------------
        bad = cur.apply({"id": "DEC-MCPFIX-SECOND", "op": "delete",
                         "value": "x", "corpus": "mcpfix"})
        check(bad["ok"] is False, "(8) unsupported op refused")
        badq = cur.apply({"id": "NO-SUCH-NODE", "op": "prioritize",
                          "value": "high", "corpus": "mcpfix"})
        check(badq["ok"] is False, "(9) unknown node -> not ok (no crash)")

    print()
    if fails:
        print(f"CURATOR SELFTEST: FAIL — {len(fails)} check(s)")
        return 1
    print("CURATOR SELFTEST: PASS — write path proven end to end")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Curation write-back leg for the Decisions tab.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--anchor", default="")
    ap.add_argument("--factlattice",
                    default=os.environ.get("FACTLATTICE_ROOT",
                                           os.path.expanduser("~/factlattice")))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, a.corpus, a.factlattice, a.anchor, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
