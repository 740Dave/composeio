"""composeio.decision_ops.lts_refresh — the lts-engine "refresh on write" primitive.

The decision-ops CORE (`core.py`) is the single-writer over a project decision
graph `.lts`. This module is the OTHER half of the write path the MCP server needs:
after the core COMMITS an edit, make the just-written change IMMEDIATELY queryable
through the lts-engine, with **no stale window** — and, on a rolled-back write, push nothing.

--------------------------------------------------------------------------------
WHY REBUILD-FROM-CORPUS IS THE REFRESH (the design freshness.py supports)
--------------------------------------------------------------------------------
The lts-engine (the SEPARATE `factlattice_engine`, formerly "MAGMA-lite" before the
magma_*->lts_* rename) has no incremental-update path and
no long-lived on-disk index the server tails: `factlattice_engine.config.build_store`
constructs every `Partition` IN MEMORY from the live corpus at process start, and
`ContentHashFreshness` (verdict.py) declares a partition FRESH iff its stored
`files_hash` (computed at build time over the corpus bytes) equals a freshly recomputed
live `combined_files_hash`. Two consequences follow directly, and they fix the design:

  1. A partition built AFTER a write is FRESH BY CONSTRUCTION — its stored hash is
     taken over the post-write bytes, so it equals the live hash. There is no window
     in which the rebuilt view is stale. (Verified empirically: build over a temp
     corpus yields `freshness().fresh is True` immediately.)
  2. A partition built BEFORE a write goes STALE the instant the bytes change — the
     freshness kernel CATCHES it (returns a loud `stale` marker, never old data).

So "refresh on write" is exactly **rebuild the affected corpus partition from the
just-written `.lts`** using `factlattice_engine`'s OWN build path. That is the only
operation the content-hash freshness model both needs and rewards: an incremental
mutate of an in-memory index would leave `files_hash` lying about content and the very
next freshness check would (correctly) call it stale. Rebuild is not the heavy option
here — it is the ONLY option that keeps the freshness invariant honest.

This module NEVER modifies `factlattice_engine` (read-only to this build): it imports
`config`/`tools`/`store` and calls them as published. `factlattice_engine` is not on
the editable-install path (only the `factlattice` PARSER is), so we prepend its repo
root to `sys.path` on first use — override with `FACTLATTICE_ENGINE_ROOT`.

CROSS-PROCESS NOTE (flagged for David): a SEPARATELY launched, long-running
`factlattice_engine.server` process cannot see this in-process rebuild — it holds its
own start-time snapshot and will (correctly, via the freshness kernel) report STALE
after a hand-edit or a decision-ops write until IT is rebuilt/restarted. The zero-stale
guarantee here is for the lts-engine view this decision-ops server itself manages and serves
(the write+refresh+query loop in one process). Wiring the write-side refresh to also
invalidate a standalone server's view is a follow-up that needs an engine change there.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

# The engine-path seat is shared with the package __init__ (which must seat it before
# `.core` imports `factlattice_engine.lint`). Old default was the now-DELETED
# ~/factlattice, then ~/factlattice-deployment (also now DELETED, renamed 2026-08-16);
# it is ~/factlattice-internal-dev now. See `_enginepath`.
from ._enginepath import DEFAULT_ENGINE_ROOT, ensure_engine_on_path as _ensure_engine_on_path  # noqa: F401,E501


# --------------------------------------------------------------------------- #
# corpus-id derivation (a decision graph file -> its project corpus id)
# --------------------------------------------------------------------------- #
def corpus_id_for_graph(graph_path: str) -> str:
    """Map a decision-graph `.lts` path to its lts-engine corpus_id (= project id).

    Prefers the authoritative mapping in `factlattice_engine.config.DECLARED_PROJECTS`
    (which pins each project's `decision_lts` file); falls back to a filename-derived
    id (`decision-graph-<x>.lts` -> `<x>`) for a temp/fixture graph not in the registry.
    """
    abs_graph = os.path.abspath(graph_path)
    try:
        _ensure_engine_on_path()
        from factlattice_engine import config as _cfg
        for pr in _cfg.DECLARED_PROJECTS:
            if os.path.abspath(_cfg._p(pr["decision_lts"])) == abs_graph:
                return pr["id"]
    except Exception:
        pass
    base = os.path.basename(graph_path)
    stem = base[:-4] if base.endswith(".lts") else base
    if stem.startswith("decision-graph-"):
        stem = stem[len("decision-graph-"):]
    return stem or base


# --------------------------------------------------------------------------- #
# refresh result
# --------------------------------------------------------------------------- #
@dataclass
class RefreshResult:
    ok: bool
    corpus_id: str
    refreshed: bool
    fresh: Optional[bool]
    total_nodes: Optional[int]
    index_hash: Optional[str]
    live_hash: Optional[str]
    message: str
    # --- [SPEC:FL-FRESHNESS] file-is-truth degradation contract -------------------
    # This in-process refresh maintains only THIS server's LOCAL lts-engine view (the one
    # `decision_lts_query` reads). It is a best-effort OPTIMIZATION, never the path that
    # makes a write visible to the standalone `factlattice_engine.server` (which serves
    # `lts_find`/`lts_node` and the boot-read): that server reconciles the change from the
    # `.lts` FILE — the single source of truth — via its own filesystem self-heal
    # ([MECH-SELFHEAL]). So a FAILED refresh degrades to a guaranteed file-event pickup,
    # NEVER to silence: the write is durable on disk and the engine will serve it fresh.
    degraded_to_file_reconcile: bool = False
    # An ImportError-class failure here (e.g. `ImportError: MagmaStore`) means THIS
    # decision-ops process is executing a STALE CODE snapshot that predates a propagated
    # consolidation — the [RISK-STALE-CODE] freshness class. Surfaced loudly so it is
    # diagnosed + the process relaunched, never read as "the write did not propagate".
    stale_code_suspected: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "corpus_id": self.corpus_id, "refreshed": self.refreshed,
            "fresh": self.fresh, "total_nodes": self.total_nodes,
            "index_hash": self.index_hash, "live_hash": self.live_hash,
            "message": self.message,
            "degraded_to_file_reconcile": self.degraded_to_file_reconcile,
            "stale_code_suspected": self.stale_code_suspected,
        }


# --------------------------------------------------------------------------- #
# the refresher — owns an in-process lts-engine view keyed by corpus_id
# --------------------------------------------------------------------------- #
class LtsRefresher:
    """Holds a per-corpus lts-engine view (a `factlattice_engine` LtsEngine built from the
    live corpus) and rebuilds it on demand. `refresh()` is the write-path trigger;
    `query()` proves the just-written change is immediately visible + fresh.

    One instance per decision-ops server process. Rebuild is O(corpus), which for a
    single decision-graph file is trivial; it is deliberately NOT incremental (see the
    module docstring: an incremental mutate would desync `files_hash` from content and
    the next freshness check would call the view stale)."""

    def __init__(self) -> None:
        # corpus_id -> (graph_path, area). Registered lazily as writes/queries name a graph.
        self._graphs: Dict[str, tuple] = {}
        # corpus_id -> LtsEngine (the freshly built view). Rebuilt by refresh().
        self._tools: Dict[str, object] = {}

    def _build_tools(self, corpus_id: str, graph_path: str, area: str):
        _ensure_engine_on_path()
        from factlattice_engine.config import build_merged_partition
        from factlattice_engine.store import LtsStore
        from factlattice_engine.tools import LtsEngine

        abs_graph = os.path.abspath(graph_path)
        roots: List[dict] = [{
            "path": os.path.dirname(abs_graph),
            "area": area,
            "only_file": os.path.basename(abs_graph),
        }]
        part = build_merged_partition(corpus_id, roots, f"proj.{corpus_id}")
        store = LtsStore()
        if part is not None and part.nodes:
            store.add_partition(part)
        return LtsEngine(store), part

    def refresh(self, graph_path: str, *, corpus_id: Optional[str] = None,
                area: str = "decisions") -> RefreshResult:
        """Rebuild the corpus partition from the CURRENT bytes of `graph_path`, making
        every node it now contains immediately queryable + fresh. Call this ONLY after
        the core has committed a real edit (never after a rollback)."""
        cid = corpus_id or corpus_id_for_graph(graph_path)
        try:
            tools, part = self._build_tools(cid, graph_path, area)
        except Exception as exc:  # noqa: BLE001 — refresh failure must not crash the write
            # [SPEC:FL-FRESHNESS] file-is-truth: the write already landed on disk (the
            # source of truth) and the standalone lts-engine reconciles it from the FILE on
            # the next query ([MECH-SELFHEAL]). This best-effort LOCAL-view refresh failing
            # therefore degrades to a guaranteed file-event pickup, never to silence.
            # NOTE: "MagmaStore" below is the ACTUAL legacy symbol a stale pre-consolidation
            # process throws at runtime (`ImportError: MagmaStore`) — a real detection literal,
            # NOT fossil naming; it must stay verbatim to catch [RISK-STALE-CODE].
            is_stale_code = isinstance(exc, ImportError) or "MagmaStore" in str(exc)
            note = ("this decision-ops process is running STALE CODE ([RISK-STALE-CODE]) — "
                    "relaunch it to clear; the write is durable and the lts-engine will "
                    "serve it fresh from the file regardless"
                    if is_stale_code else
                    "the write is durable and the lts-engine reconciles it from the file")
            return RefreshResult(
                ok=False, corpus_id=cid, refreshed=False, fresh=None,
                total_nodes=None, index_hash=None, live_hash=None,
                degraded_to_file_reconcile=True, stale_code_suspected=is_stale_code,
                message=(f"local-view refresh FAILED (write already committed; degraded to "
                         f"lts-engine file-reconcile): {type(exc).__name__}: {exc} — {note}"))
        self._graphs[cid] = (os.path.abspath(graph_path), area)
        self._tools[cid] = tools
        if part is None:
            return RefreshResult(ok=True, corpus_id=cid, refreshed=True, fresh=None,
                                 total_nodes=0, index_hash=None, live_hash=None,
                                 message="refreshed (corpus parsed to zero nodes)")
        fr = part.freshness()
        return RefreshResult(
            ok=True, corpus_id=cid, refreshed=True, fresh=fr.fresh,
            total_nodes=len(part.nodes), index_hash=fr.index_hash, live_hash=fr.live_hash,
            message=("refreshed; view is fresh-by-construction" if fr.fresh
                     else f"refreshed but freshness verdict is stale: {fr.reason}"))

    def _tools_for(self, corpus_id: str, graph_path: Optional[str], area: str):
        if corpus_id in self._tools:
            return self._tools[corpus_id]
        gp = graph_path or (self._graphs.get(corpus_id, (None,))[0])
        if gp is None:
            return None
        tools, _ = self._build_tools(corpus_id, gp, area)
        self._graphs[corpus_id] = (os.path.abspath(gp), area)
        self._tools[corpus_id] = tools
        return tools

    def query(self, *, corpus_id: Optional[str] = None, graph_path: Optional[str] = None,
              area: str = "decisions", tool: str = "lts_find", **arguments) -> dict:
        """Dispatch an lts-engine read (`lts_find` / `lts_node` / `lts_traverse` /
        `lts_capabilities` / `lts_list_corpora`) against the refreshed in-process
        view. Reuses `factlattice_engine.tools.dispatch` verbatim — this is real lts-engine
        query code, not a reimplementation."""
        _ensure_engine_on_path()
        from factlattice_engine.tools import dispatch

        cid = corpus_id or (corpus_id_for_graph(graph_path) if graph_path else None)
        if cid is None:
            return {"success": False, "error": "query needs corpus_id or graph_path"}
        tools = self._tools_for(cid, graph_path, area)
        if tools is None:
            return {"success": False, "error": f"no lts-engine view for corpus {cid!r} "
                                               "(refresh or supply graph_path first)"}
        args = dict(arguments)
        if tool != "lts_list_corpora":
            args.setdefault("corpus", cid)
        return dispatch(tool, args, tools=tools)


__all__ = ["LtsRefresher", "RefreshResult", "corpus_id_for_graph"]
