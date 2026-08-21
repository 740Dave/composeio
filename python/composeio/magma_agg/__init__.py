"""composeio.magma_agg — a small count / group-by layer over MAGMA's `find`.

MAGMA (the `factlattice_engine` graph query engine) answers "which nodes / how
connected" — it returns nodes, edges, subgraphs, never a scalar or an aggregate row
(see `docs/magma-primer.md` §8). The meta-questions the decision-reconciliation and
defect-tracking rollups actually need are AGGREGATES: "count decisions group-by
lifecycle-position", "count nodes group-by domain / prefix", "how many FIX nodes this
month". This module is that missing primitive, built COMPOSEIO-SIDE over the read-only
engine (it imports + calls `factlattice_engine`; it never modifies it — the same
boundary `decision_ops.magma_refresh` observes).

    from composeio.magma_agg import (count, group_by, numeric, sum, avg, min, max,
                                     distinct, cross_tab, top_k,
                                     degree, top_by_degree, count_by_edge_type, orphans)

    # --- the original count/group_by kernel ---
    # count decisions group-by lifecycle-position (the reconciliation histogram)
    group_by("lifecycle-position", corpus="intelligence-console", area="decisions")
    #   -> {"buckets": [{"value":"committed","count":52}, {"value":"built","count":9}, ...]}
    group_by("domain", corpus="intelligence-console")   # / "prefix"
    count(corpus="intelligence-console", prefix="DEC")  # -> {"total": ...}

    # --- TIER 1: numeric / distinct / cross-tab / top_k ---
    # "sum tokens by session"  (non-numeric/absent SKIPPED + reported, never coerced)
    sum("token_count", corpus="intelligence-console", group_by="decided-in-session")
    avg("dur_s", corpus="intelligence-console", group_by="root-cause")   # "avg dur per root-cause"
    distinct("lifecycle-position", corpus="intelligence-console", area="decisions")

    # "decisions lifecycle x domain"  (2-D pivot: matrix + flat buckets)
    cross_tab("lifecycle-position", "domain", corpus="intelligence-console", area="decisions")

    # "top-5 root-causes"  (by count) and "top sessions by token spend" (by a numeric agg)
    top_k("decided-in-session", 5, corpus="intelligence-console", prefix="DEC")
    top_k("decided-in-session", 5, by="token_count", agg="sum", corpus="intelligence-console")

    # --- TIER 2: graph-native (degree / edge-type / orphans) ---
    # "most-connected decisions"  (load-bearing nodes)
    top_by_degree(5, corpus="intelligence-console", area="decisions")
    degree("CXC-DECISIONS", corpus="intelligence-console")     # in/out/total
    count_by_edge_type(corpus="intelligence-console")          # DEPENDS_ON/SUPERSEDES/...
    orphans(corpus="intelligence-console", area="decisions")   # nodes with no incoming edge

CLI (also prints a menu of worked example queries)::

    python3 -m composeio.magma_agg count  --corpus intelligence-console --group-by lifecycle-position
    python3 -m composeio.magma_agg sum token_count --group-by decided-in-session
    python3 -m composeio.magma_agg cross-tab lifecycle-position domain --area decisions
    python3 -m composeio.magma_agg top-k decided-in-session --k 5 --prefix DEC
    python3 -m composeio.magma_agg top-by-degree --k 5 --area decisions
    python3 -m composeio.magma_agg edge-types
    python3 -m composeio.magma_agg orphans --area decisions
    python3 -m composeio.magma_agg examples        # run the worked example queries
    python3 -m composeio.magma_agg --selftest

DESIGN NOTES
------------
* DETERMINISTIC, NO LLM — a pure fold over the found node set.
* COMPLETE — drives `find` across its token-budget cursor so counts see every match
  (`core._find_all`), never a truncated slice.
* FRESHNESS-HONEST — propagates the engine's not-found / STALE markers verbatim; a
  stale corpus never yields a silently-wrong count ([INV-MAGMA-FRESH]).
* CANONICALIZING — `lifecycle-position` / `status` collapse to their leading status
  token (the corpus stores `committed   # prose` as a distinct raw string from
  `committed`); dates bucket to day/month/year. Pass `raw=True` to see raw values.

RECOMMENDATION FOR DAVID (engine placement)
-------------------------------------------
This layer is a pure fold over `find` and lives cleanly composeio-side, so nothing
forces it into `factlattice_engine`. BUT it is a general MAGMA capability, not a
composeio-specific one, and every corpus/consumer would want it. If/when the engine
adds a native `magma_count` / `magma_group_by` MCP tool, this module is the reference
implementation to port (the canonicalization + cursor-drain + freshness-passthrough
are the load-bearing parts). Until then it is the aggregation surface, and a thin
`magma_count`/`magma_group_by` MCP adapter over `MagmaAggregator` is the natural next
step to make it drivable by the Intelligence identically to a human (the credo).
Engine-side placement is DAVID'S push to 740Dave/factlattice — flagged, not assumed.
"""

from __future__ import annotations

from .core import (
    MagmaAggregator,
    avg,
    build_default_tools,
    canonical_token,
    coerce_number,
    count,
    count_by_edge_type,
    cross_tab,
    date_bucket,
    degree,
    distinct,
    group_by,
    make_key_fn,
    max,
    min,
    numeric,
    numeric_summary,
    orphans,
    resolve_attr,
    sum,
    top_by_degree,
    top_k,
    NONE_KEY,
)

__all__ = [
    "MagmaAggregator", "count", "group_by", "numeric", "sum", "avg", "min", "max",
    "distinct", "cross_tab", "top_k", "degree", "top_by_degree",
    "count_by_edge_type", "orphans",
    "resolve_attr", "make_key_fn", "canonical_token", "date_bucket",
    "coerce_number", "numeric_summary", "build_default_tools", "NONE_KEY",
]
