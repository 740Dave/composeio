"""composeio.magma_agg.core — the deterministic count / group-by kernel.

This is the whole aggregation layer. It runs MAGMA's OWN `find` (verbatim — real
query code, real freshness gate, real taxonomy/date/area filters) to obtain the
matching node set, then folds that set into a histogram. There is no second store,
no LLM, no reimplementation of the graph engine: the aggregator is a thin fold over
`factlattice_engine.tools.MagmaTools.find`.

--------------------------------------------------------------------------------
WHY A PAGINATION LOOP INSTEAD OF ONE `find` CALL
--------------------------------------------------------------------------------
`find` applies a TOKEN BUDGET and truncates its returned node list (context-hygiene
for an LLM consumer). An aggregate MUST see EVERY matching node or the counts lie.
So `_find_all` drives `find` in a cursor loop (offset := cursor while truncated),
accumulating the full match set regardless of budget. This reuses the engine's
predicate + freshness logic exactly; it never bypasses the gate. If `find` returns
a failure (corpus-not-found, STALE), the aggregator propagates it VERBATIM — a stale
corpus yields a loud stale marker, never a silently-wrong count. [INV-MAGMA-FRESH].

--------------------------------------------------------------------------------
WHY CANONICALIZATION IS A FIRST-CLASS STEP (the real-corpus lesson)
--------------------------------------------------------------------------------
The console decision corpus stores `lifecycle-position` values with trailing prose
annotations — `committed   # proven across c9/c10/c11` is a DISTINCT raw string from
`committed`. A naive group-by over the raw value produces ~30 singleton buckets, not
the committed/built/raised/decided histogram a human means. So a group-by carries a
per-attribute VALUE NORMALIZER: `lifecycle-position` collapses to its leading status
token; `decided-in-session` buckets to a date granularity via the engine's own
`normalize_date`. Pass `canonical=False` / `raw=True` to see the un-normalized values.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

# Builtins pinned BEFORE the module-level `sum` / `min` / `max` convenience wrappers
# (Tier-1 numeric aggregates) shadow them for the rest of the module. Every internal
# fold uses these aliases so the wrappers never break the helpers.
_bsum, _bmin, _bmax = sum, min, max

DEFAULT_ENGINE_ROOT = "/Users/davidleeburnett/factlattice"

# Sentinel bucket key for a node whose group attribute is absent/None/unparseable.
NONE_KEY = "(none)"


# --------------------------------------------------------------------------- #
# engine import (factlattice_engine is not on the editable path — only the
# `factlattice` PARSER is; add the engine repo root, overridable, exactly as
# decision_ops.magma_refresh does). READ-ONLY: we import + call, never modify.
# --------------------------------------------------------------------------- #
def _ensure_engine_on_path() -> None:
    root = os.environ.get("FACTLATTICE_ENGINE_ROOT", DEFAULT_ENGINE_ROOT)
    if root and root not in sys.path:
        sys.path.insert(0, root)


def _normalize_date(value: object) -> Optional[str]:
    """The engine's OWN date normalizer (YYYY-MM-DD | None), reused verbatim so the
    aggregator's date bucketing matches `find`'s since/until semantics exactly."""
    _ensure_engine_on_path()
    from factlattice_engine.tools import normalize_date
    return normalize_date(value)


def build_default_tools():
    """Build a `MagmaTools` over the composeio corpus config (the 8 declared projects;
    only the ones with nodes register). Used when no tools instance is injected."""
    _ensure_engine_on_path()
    from factlattice_engine.config import build_store
    from factlattice_engine.tools import MagmaTools
    return MagmaTools(build_store())


# --------------------------------------------------------------------------- #
# attribute resolution — where a group-by attribute lives on a node dict
# --------------------------------------------------------------------------- #
# `find` returns each node as `{corpus, id, type_prefix, title, domain, status,
# area, document_tag, properties: {...}, ...}`. An attribute may be a top-level
# field, a friendly alias, or a key inside `properties`.
_TOPLEVEL_ALIASES: Dict[str, str] = {
    "prefix": "type_prefix",              # group "by prefix" == the [PREFIX-...] class
    "type-prefix": "type_prefix",
    "doctype": "document_tag",
    "document-tag": "document_tag",
    "lifecycle": "lifecycle-position",    # convenience alias -> the property
    "session": "decided-in-session",
    "date": "decided-in-session",
}
_TOPLEVEL_FIELDS = {
    "id", "type_prefix", "title", "domain", "status", "area",
    "document_tag", "definition_form", "parent_section",
}


def resolve_attr(node: dict, attr: str) -> Any:
    """Return the raw (un-normalized) value of `attr` on a node dict, searching
    friendly aliases -> top-level fields -> `properties` -> None."""
    key = _TOPLEVEL_ALIASES.get(attr, attr)
    if key in _TOPLEVEL_FIELDS:
        return node.get(key)
    props = node.get("properties") or {}
    if key in props:
        return props.get(key)
    if key in node:  # any other top-level field (e.g. content_hash) by exact name
        return node.get(key)
    return None


# --------------------------------------------------------------------------- #
# value normalization — collapse messy raw strings into meaningful buckets
# --------------------------------------------------------------------------- #
# A lifecycle value in the wild: "committed   # proven across c9/c10/c11" or
# "committed (principles doc CREATED; ...)". The MEANINGFUL group is the leading
# status token; everything after the first whitespace / '#' / '(' is annotation.
_LEADING_TOKEN_RE = re.compile(r"^([^\s#(]+)")

# Attributes whose raw values are lifecycle-style status tokens with trailing prose.
_CANONICAL_TOKEN_ATTRS = {"lifecycle-position", "lifecycle", "status"}
# Attributes that are dates and bucket to a granularity.
_DATE_ATTRS = {"decided-in-session", "session", "date"}


def canonical_token(value: object) -> Optional[str]:
    """The leading status token of a possibly-annotated value.
    "committed # ..." / "committed (…)" / "spec-authored" -> "committed" / "committed"
    / "spec-authored". None / empty -> None."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    m = _LEADING_TOKEN_RE.match(v)
    return m.group(1) if m else None


def date_bucket(value: object, granularity: str = "month") -> Optional[str]:
    """Bucket a corpus date value to `day` (YYYY-MM-DD) / `month` (YYYY-MM) /
    `year` (YYYY), via the engine's `normalize_date`. Unparseable -> None."""
    d = _normalize_date(value)
    if d is None:
        return None
    if granularity == "year":
        return d[:4]
    if granularity == "month":
        return d[:7]
    return d  # day


def make_key_fn(attr: str, *, canonical: bool = True,
                date_granularity: str = "month") -> Callable[[dict], Optional[str]]:
    """Build the node -> bucket-key function for `attr`, applying the right
    normalizer. `canonical=False` disables status-token collapsing (raw values)."""
    key = _TOPLEVEL_ALIASES.get(attr, attr)

    def key_fn(node: dict) -> Optional[str]:
        raw = resolve_attr(node, attr)
        if key in _DATE_ATTRS:
            return date_bucket(raw, date_granularity)
        if canonical and key in _CANONICAL_TOKEN_ATTRS:
            return canonical_token(raw)
        if raw is None:
            return None
        return raw if isinstance(raw, str) else str(raw)

    return key_fn


# --------------------------------------------------------------------------- #
# numeric coercion — the HONEST fold (skip, never coerce-to-zero)
# --------------------------------------------------------------------------- #
# A numeric aggregate over a graph attribute must be honest about what actually
# contributed: a node whose metric attr is absent / None / non-numeric is SKIPPED and
# counted as `skipped`, never silently folded in as 0. A `sum`/`avg` that quietly
# treats a missing `tokens` as 0 lies exactly the way a truncated count lies.
_NUMERIC_OPS = ("sum", "avg", "min", "max")


def coerce_number(value: object) -> Optional[float]:
    """Best-effort numeric coercion. `int`/`float` pass through (but a `bool` is a flag,
    not a metric -> None). A string is parsed directly, then as its leading token
    (so `"5 # measured"` -> 5.0). Anything else / unparseable -> None (skip)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            m = _LEADING_TOKEN_RE.match(s)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    return None
    return None


def numeric_summary(values: List[float]) -> Dict[str, Any]:
    """sum/avg/min/max over already-coerced numeric values. Empty -> all-None except
    count=0 / sum=0.0 (an empty sum is honestly 0; an empty avg/min/max is undefined)."""
    n = len(values)
    if n == 0:
        return {"count": 0, "sum": 0.0, "avg": None, "min": None, "max": None}
    total = _bsum(values)
    return {"count": n, "sum": total, "avg": total / n,
            "min": _bmin(values), "max": _bmax(values)}


# --------------------------------------------------------------------------- #
# the aggregator
# --------------------------------------------------------------------------- #
# recognized find() filter kwargs the aggregator forwards to narrow the node set.
_FIND_FILTER_KEYS = ("domain", "function", "service", "prefix", "status",
                     "text", "since", "until", "date_property", "area")


class MagmaAggregator:
    """count / group-by over MAGMA's `find`. Deterministic, no LLM.

    Inject a `factlattice_engine.tools.MagmaTools` (e.g. the one a decision_ops
    `MagmaRefresher` already holds), or let it build the default composeio store.
    """

    def __init__(self, tools: object = None):
        self._tools = tools

    def _get_tools(self):
        if self._tools is None:
            self._tools = build_default_tools()
        return self._tools

    # -- the complete-match fetch (drives find across the token-budget cursor) --
    def _find_all(self, corpus: Optional[str], filters: dict):
        """Return (nodes, error). On a find failure (not-found / STALE) `nodes` is
        None and `error` is the engine's response dict, propagated verbatim."""
        tools = self._get_tools()
        args = {k: filters.get(k) for k in _FIND_FILTER_KEYS if filters.get(k) is not None}
        nodes: List[dict] = []
        offset = 0
        seen_cursor = -1
        while True:
            res = tools.find(corpus=corpus, offset=offset, **args)
            if not res.get("success"):
                return None, res
            nodes.extend(res.get("nodes") or [])
            if not res.get("truncated"):
                return nodes, res
            cursor = res.get("cursor")
            if cursor is None or cursor <= seen_cursor:  # guard against a stuck cursor
                return nodes, res
            seen_cursor = cursor
            offset = cursor

    # -- public: group-by ------------------------------------------------------
    def group_by(self, attr: str, *, corpus: Optional[str] = None,
                 canonical: bool = True, raw: bool = False,
                 date_granularity: str = "month", **filters) -> dict:
        """Histogram the matching node set by `attr`. `raw=True` is an alias for
        `canonical=False` (show un-collapsed values). Extra kwargs are `find`
        filters (domain/prefix/status/area/since/until/text/function/...).

        Returns a deterministic dict:
          {success, corpus, attr, total, distinct, groups: {value: count},
           buckets: [{value, count}, ...] sorted (-count, value), filters, normalize}
        """
        if raw:
            canonical = False
        nodes, res = self._find_all(corpus, filters)
        if nodes is None:
            return res  # verbatim not-found / STALE
        key_fn = make_key_fn(attr, canonical=canonical,
                             date_granularity=date_granularity)
        groups: Dict[str, int] = {}
        for nd in nodes:
            k = key_fn(nd)
            bucket = NONE_KEY if k is None else str(k)
            groups[bucket] = groups.get(bucket, 0) + 1
        buckets = [{"value": v, "count": c} for v, c in groups.items()]
        buckets.sort(key=lambda b: (-b["count"], b["value"]))
        out = {
            "success": True,
            "corpus": corpus,
            "attr": attr,
            "total": len(nodes),
            "distinct": len(groups),
            "groups": groups,
            "buckets": buckets,
            "filters": {k: v for k, v in filters.items() if v is not None},
            "normalize": {"canonical": canonical,
                          "date_granularity": (date_granularity
                                               if _TOPLEVEL_ALIASES.get(attr, attr)
                                               in _DATE_ATTRS else None)},
        }
        if not corpus and "stale_corpora" in res:
            out["stale_corpora"] = res["stale_corpora"]
        return out

    # -- public: count ---------------------------------------------------------
    def count(self, *, corpus: Optional[str] = None,
              group_by: Optional[str] = None, canonical: bool = True,
              raw: bool = False, date_granularity: str = "month",
              **filters) -> dict:
        """Count matching nodes. With `group_by` set, delegate to `group_by()` (the
        histogram carries the same `total`). Without it, return just the scalar total.
        """
        if group_by:
            return self.group_by(group_by, corpus=corpus, canonical=canonical,
                                 raw=raw, date_granularity=date_granularity,
                                 **filters)
        nodes, res = self._find_all(corpus, filters)
        if nodes is None:
            return res
        out = {"success": True, "corpus": corpus, "total": len(nodes),
               "filters": {k: v for k, v in filters.items() if v is not None}}
        if not corpus and "stale_corpora" in res:
            out["stale_corpora"] = res["stale_corpora"]
        return out

    # ===================================================================== #
    # TIER 1 — numeric aggregates / distinct / cross-tab / top_k
    # (all pure deterministic folds over the SAME complete, freshness-gated
    #  node set `count`/`group_by` use; they inherit completeness + [INV-MAGMA-FRESH]
    #  for free because they go through `_find_all`.)
    # ===================================================================== #

    # -- public: numeric aggregate (sum/avg/min/max), optionally grouped ---------
    def numeric(self, attr: str, *, op: str = "sum", corpus: Optional[str] = None,
                group_by: Optional[str] = None, canonical: bool = True,
                raw: bool = False, date_granularity: str = "month",
                **filters) -> dict:
        """sum/avg/min/max of the NUMERIC node attribute `attr` (tokens, commits,
        dur_s, ...). Non-numeric / absent values are SKIPPED and reported as `skipped`
        (never coerced to 0). With `group_by` set, each bucket carries its own
        sum/avg/min/max over that bucket's contributing nodes.

        Global returns `{success, corpus, attr, op, value, count, skipped, total,
        sum, avg, min, max, filters}` (all four stats are always present; `value` is
        the one you asked for). Grouped returns a bucket list sorted by the requested
        op (desc), value, — see the group branch."""
        if op not in _NUMERIC_OPS:
            return {"success": False, "error": f"unknown numeric op {op!r}; "
                    f"expected one of {_NUMERIC_OPS}"}
        if raw:
            canonical = False
        nodes, res = self._find_all(corpus, filters)
        if nodes is None:
            return res

        if group_by:
            key_fn = make_key_fn(group_by, canonical=canonical,
                                 date_granularity=date_granularity)
            per: Dict[str, List[float]] = {}
            skipped = 0
            for nd in nodes:
                num = coerce_number(resolve_attr(nd, attr))
                if num is None:
                    skipped += 1
                    continue
                k = key_fn(nd)
                bucket = NONE_KEY if k is None else str(k)
                per.setdefault(bucket, []).append(num)
            buckets = []
            groups: Dict[str, Any] = {}
            for bkt, vals in per.items():
                s = numeric_summary(vals)
                row = {"value": bkt, "count": s["count"], "sum": s["sum"],
                       "avg": s["avg"], "min": s["min"], "max": s["max"]}
                buckets.append(row)
                groups[bkt] = s[op]
            # deterministic: by the requested op DESC (None last), then value ASC
            buckets.sort(key=lambda b: (-(b[op] if b[op] is not None else float("-inf")),
                                        b["value"]))
            out = {"success": True, "corpus": corpus, "attr": attr, "op": op,
                   "group_attr": group_by, "total": len(nodes), "skipped": skipped,
                   "distinct": len(per), "groups": groups, "buckets": buckets,
                   "filters": {k: v for k, v in filters.items() if v is not None},
                   "normalize": {"canonical": canonical,
                                 "date_granularity": (date_granularity
                                     if _TOPLEVEL_ALIASES.get(group_by, group_by)
                                     in _DATE_ATTRS else None)}}
            if not corpus and "stale_corpora" in res:
                out["stale_corpora"] = res["stale_corpora"]
            return out

        vals = []
        skipped = 0
        for nd in nodes:
            num = coerce_number(resolve_attr(nd, attr))
            if num is None:
                skipped += 1
            else:
                vals.append(num)
        s = numeric_summary(vals)
        out = {"success": True, "corpus": corpus, "attr": attr, "op": op,
               "value": s[op], "count": s["count"], "skipped": skipped,
               "total": len(nodes), "sum": s["sum"], "avg": s["avg"],
               "min": s["min"], "max": s["max"],
               "filters": {k: v for k, v in filters.items() if v is not None}}
        if not corpus and "stale_corpora" in res:
            out["stale_corpora"] = res["stale_corpora"]
        return out

    # thin op wrappers (mirror the numeric() surface)
    def sum(self, attr, **kw):
        return self.numeric(attr, op="sum", **kw)

    def avg(self, attr, **kw):
        return self.numeric(attr, op="avg", **kw)

    def min(self, attr, **kw):
        return self.numeric(attr, op="min", **kw)

    def max(self, attr, **kw):
        return self.numeric(attr, op="max", **kw)

    # -- public: distinct (cardinality of an attribute's values) -----------------
    def distinct(self, attr: str, *, corpus: Optional[str] = None,
                 canonical: bool = True, raw: bool = False,
                 date_granularity: str = "month", **filters) -> dict:
        """Cardinality of `attr`'s distinct (canonicalized unless raw) values over the
        complete match set. `(none)` is NOT counted as a distinct value; `counted` is
        the number of nodes that carried a value. Returns `{success, corpus, attr,
        distinct, counted, total, values:[sorted], filters}`."""
        if raw:
            canonical = False
        nodes, res = self._find_all(corpus, filters)
        if nodes is None:
            return res
        key_fn = make_key_fn(attr, canonical=canonical,
                             date_granularity=date_granularity)
        seen: Dict[str, int] = {}
        counted = 0
        for nd in nodes:
            k = key_fn(nd)
            if k is None:
                continue
            counted += 1
            seen[str(k)] = seen.get(str(k), 0) + 1
        out = {"success": True, "corpus": corpus, "attr": attr,
               "distinct": len(seen), "counted": counted, "total": len(nodes),
               "values": sorted(seen.keys()),
               "filters": {k: v for k, v in filters.items() if v is not None}}
        if not corpus and "stale_corpora" in res:
            out["stale_corpora"] = res["stale_corpora"]
        return out

    # -- public: 2-D cross-tab (pivot / matrix) ----------------------------------
    def cross_tab(self, attr1: str, attr2: str, *, corpus: Optional[str] = None,
                  canonical: bool = True, raw: bool = False,
                  date_granularity: str = "month", **filters) -> dict:
        """Pivot the match set by `attr1` (rows) x `attr2` (cols) — e.g. decisions by
        lifecycle x domain. Deterministic: `matrix` is a nested dict keyed by SORTED
        row/col value; `rows`/`cols` are the sorted axis labels; `buckets` is the flat
        cell list sorted (-count, attr1, attr2). Returns `{success, corpus, attr1,
        attr2, total, rows, cols, distinct1, distinct2, matrix, buckets, filters}`."""
        if raw:
            canonical = False
        nodes, res = self._find_all(corpus, filters)
        if nodes is None:
            return res
        k1 = make_key_fn(attr1, canonical=canonical, date_granularity=date_granularity)
        k2 = make_key_fn(attr2, canonical=canonical, date_granularity=date_granularity)
        cells: Dict[Tuple[str, str], int] = {}
        rows: Dict[str, int] = {}
        cols: Dict[str, int] = {}
        for nd in nodes:
            a = k1(nd)
            b = k2(nd)
            av = NONE_KEY if a is None else str(a)
            bv = NONE_KEY if b is None else str(b)
            rows[av] = rows.get(av, 0) + 1
            cols[bv] = cols.get(bv, 0) + 1
            cells[(av, bv)] = cells.get((av, bv), 0) + 1
        row_labels = sorted(rows.keys())
        col_labels = sorted(cols.keys())
        matrix = {rv: {cv: cells.get((rv, cv), 0) for cv in col_labels}
                  for rv in row_labels}
        buckets = [{"attr1": rv, "attr2": cv, "count": c}
                   for (rv, cv), c in cells.items()]
        buckets.sort(key=lambda b: (-b["count"], b["attr1"], b["attr2"]))
        out = {"success": True, "corpus": corpus, "attr1": attr1, "attr2": attr2,
               "total": len(nodes), "rows": row_labels, "cols": col_labels,
               "distinct1": len(rows), "distinct2": len(cols),
               "row_totals": rows, "col_totals": cols,
               "matrix": matrix, "buckets": buckets,
               "filters": {k: v for k, v in filters.items() if v is not None},
               "normalize": {"canonical": canonical}}
        if not corpus and "stale_corpora" in res:
            out["stale_corpora"] = res["stale_corpora"]
        return out

    # -- public: top_k (bounded slice of a sorted group-by) ----------------------
    def top_k(self, attr: str, k: int = 10, *, by: str = "count",
              agg: str = "sum", corpus: Optional[str] = None,
              canonical: bool = True, raw: bool = False,
              date_granularity: str = "month", **filters) -> dict:
        """Top-`k` values of `attr`, by node COUNT (`by='count'`, default) or by a
        numeric aggregate of another attr (`by=<numeric_attr>`, `agg` = sum/avg/min/max).
        The underlying group-by is already deterministically sorted; this is a bounded
        slice. Returns `{success, corpus, attr, by, agg, k, total, distinct, buckets,
        filters}` where `buckets` is the (<= k) leading rows."""
        if by == "count":
            g = self.group_by(attr, corpus=corpus, canonical=canonical, raw=raw,
                              date_granularity=date_granularity, **filters)
            if not g.get("success"):
                return g
            buckets = g["buckets"][:_bmax(0, k)]
            out = {"success": True, "corpus": corpus, "attr": attr, "by": "count",
                   "agg": None, "k": k, "total": g["total"], "distinct": g["distinct"],
                   "buckets": buckets, "filters": g.get("filters", {})}
        else:
            g = self.numeric(by, op=agg, corpus=corpus, group_by=attr,
                             canonical=canonical, raw=raw,
                             date_granularity=date_granularity, **filters)
            if not g.get("success"):
                return g
            buckets = g["buckets"][:_bmax(0, k)]
            out = {"success": True, "corpus": corpus, "attr": attr, "by": by,
                   "agg": agg, "k": k, "total": g["total"], "distinct": g["distinct"],
                   "buckets": buckets, "skipped": g.get("skipped"),
                   "filters": g.get("filters", {})}
        if not corpus and "stale_corpora" in g:
            out["stale_corpora"] = g["stale_corpora"]
        return out

    # ===================================================================== #
    # TIER 2 — graph-native aggregates (degree / edge-type / orphans)
    # ===================================================================== #
    # These need EDGE/adjacency data, which `find()` does not carry. They build a
    # COMPLETE, freshness-gated adjacency snapshot: the node universe + freshness
    # verdict come from `_find_all` (so STALE/not-found propagate VERBATIM exactly as
    # for Tier 1), and the edges are read from the SAME fresh partitions via the PUBLIC
    # `Partition.iter_edges()` — which returns EVERY edge with no token-budget
    # truncation, so degree/edge-type counts are complete. Edge resolution follows the
    # engine's own `resolved_edge_count` rule (skip `dangling` + `unresolved_target`).
    #
    # ENGINE-ASSIST FOLLOW-ON (flagged, not built): a whole-corpus degree/edge-type
    # histogram is O(edges) here per call and re-scans layers each time. The efficient
    # form is an engine-native adjacency index / a `magma_edges` read tool exposing
    # (type, in/out degree) directly — that is a factlattice engine change and a
    # dependency on the DEFERRED adjacency-traverse fix. Do NOT modify factlattice to
    # get it; this composeio-side scan is the correct-now implementation.

    def _adjacency(self, corpus: Optional[str], filters: dict):
        """Return (ctx, err). `ctx` = {found_nodes, found_ids, in_deg, out_deg,
        edge_types, total_edges, resolved_edges, stale_corpora}. On a find failure
        (not-found / STALE) `ctx` is None and `err` is the engine dict, verbatim."""
        nodes, res = self._find_all(corpus, filters)
        if nodes is None:
            return None, res
        tools = self._get_tools()
        if corpus:
            part = tools.store.get_partition(corpus)
            parts = [part] if part is not None else []
        else:
            stale = set(res.get("stale_corpora") or [])
            parts = [p for p in tools.store.partitions()
                     if p.corpus_id not in stale]
        in_deg: Dict[str, int] = {}
        out_deg: Dict[str, int] = {}
        edge_types: Dict[str, int] = {}
        total_edges = 0
        resolved = 0
        for p in parts:
            for e in p.iter_edges():
                total_edges += 1
                if e.get("dangling"):
                    continue
                if "unresolved_target" in (e.get("flags") or ()):
                    continue
                resolved += 1
                et = e.get("type") or "(untyped)"
                edge_types[et] = edge_types.get(et, 0) + 1
                src = e.get("source")
                tgt = e.get("target")
                if src is not None:
                    out_deg[src] = out_deg.get(src, 0) + 1
                if tgt is not None:
                    in_deg[tgt] = in_deg.get(tgt, 0) + 1
        ctx = {
            "found_nodes": nodes,
            "found_ids": [n["id"] for n in nodes],
            "in_deg": in_deg,
            "out_deg": out_deg,
            "edge_types": edge_types,
            "total_edges": total_edges,
            "resolved_edges": resolved,
            "stale_corpora": res.get("stale_corpora") if not corpus else None,
        }
        return ctx, None

    # -- public: degree of a single node -----------------------------------------
    def degree(self, node: str, *, corpus: Optional[str] = None, **filters) -> dict:
        """Connectivity of one node: in / out / total resolved-edge degree, computed
        from the complete freshness-gated adjacency (consistent with `top_by_degree`).
        Returns `{success, corpus, node, in_degree, out_degree, degree, found}`."""
        ctx, err = self._adjacency(corpus, filters)
        if ctx is None:
            return err
        ind = ctx["in_deg"].get(node, 0)
        outd = ctx["out_deg"].get(node, 0)
        out = {"success": True, "corpus": corpus, "node": node,
               "in_degree": ind, "out_degree": outd, "degree": ind + outd,
               "found": node in set(ctx["found_ids"]),
               "filters": {k: v for k, v in filters.items() if v is not None}}
        if ctx.get("stale_corpora") is not None:
            out["stale_corpora"] = ctx["stale_corpora"]
        return out

    # -- public: the k most-connected nodes --------------------------------------
    def top_by_degree(self, k: int = 10, *, corpus: Optional[str] = None,
                      direction: str = "both", **filters) -> dict:
        """The k load-bearing nodes by degree over the found set. `direction` =
        `both` (in+out) | `in` | `out`. Deterministic: sorted (-degree, id). Returns
        `{success, corpus, k, direction, total, buckets:[{node,in_degree,out_degree,
        degree}], filters}`."""
        ctx, err = self._adjacency(corpus, filters)
        if ctx is None:
            return err
        rows = []
        for nid in ctx["found_ids"]:
            ind = ctx["in_deg"].get(nid, 0)
            outd = ctx["out_deg"].get(nid, 0)
            deg = ind if direction == "in" else outd if direction == "out" else ind + outd
            rows.append({"node": nid, "in_degree": ind, "out_degree": outd,
                         "degree": deg})
        rows.sort(key=lambda r: (-r["degree"], r["node"]))
        out = {"success": True, "corpus": corpus, "k": k, "direction": direction,
               "total": len(ctx["found_ids"]), "buckets": rows[:_bmax(0, k)],
               "filters": {k2: v for k2, v in filters.items() if v is not None}}
        if ctx.get("stale_corpora") is not None:
            out["stale_corpora"] = ctx["stale_corpora"]
        return out

    # -- public: edge-type histogram ---------------------------------------------
    def count_by_edge_type(self, *, corpus: Optional[str] = None, **filters) -> dict:
        """Histogram of relationship types (DEPENDS_ON / SUPERSEDES / RELATES_TO / ...)
        over the fresh corpus edges. `filters` gate freshness + choose the corpus/corpora
        (edge counts are whole-corpus, not filtered to a node subset — an edge type is a
        corpus-level fact). Returns `{success, corpus, total_edges, resolved_edges,
        distinct, groups:{type:count}, buckets:[{value,count}] sorted (-count, value)}`."""
        ctx, err = self._adjacency(corpus, filters)
        if ctx is None:
            return err
        groups = dict(ctx["edge_types"])
        buckets = [{"value": v, "count": c} for v, c in groups.items()]
        buckets.sort(key=lambda b: (-b["count"], b["value"]))
        out = {"success": True, "corpus": corpus, "total_edges": ctx["total_edges"],
               "resolved_edges": ctx["resolved_edges"], "distinct": len(groups),
               "groups": groups, "buckets": buckets,
               "filters": {k: v for k, v in filters.items() if v is not None}}
        if ctx.get("stale_corpora") is not None:
            out["stale_corpora"] = ctx["stale_corpora"]
        return out

    # -- public: orphans / dangle-count ------------------------------------------
    def orphans(self, *, corpus: Optional[str] = None, **filters) -> dict:
        """Nodes in the found set with NO incoming resolved edge (in_degree == 0) —
        corpus health as a query (an un-referenced decision/spec). Returns `{success,
        corpus, total, orphan_count, orphan_ratio, orphans:[sorted node ids],
        filters}`. `orphans` is capped in the flat list only for readability via
        `limit` (default: all)."""
        limit = filters.pop("limit", None)
        ctx, err = self._adjacency(corpus, filters)
        if ctx is None:
            return err
        found = ctx["found_ids"]
        orphan_ids = sorted(nid for nid in found if ctx["in_deg"].get(nid, 0) == 0)
        total = len(found)
        shown = orphan_ids if limit is None else orphan_ids[:_bmax(0, int(limit))]
        out = {"success": True, "corpus": corpus, "total": total,
               "orphan_count": len(orphan_ids),
               "orphan_ratio": (len(orphan_ids) / total) if total else 0.0,
               "orphans": shown,
               "filters": {k: v for k, v in filters.items() if v is not None}}
        if limit is not None:
            out["truncated"] = len(orphan_ids) > len(shown)
        if ctx.get("stale_corpora") is not None:
            out["stale_corpora"] = ctx["stale_corpora"]
        return out


# --------------------------------------------------------------------------- #
# module-level convenience (build tools once, reuse)
# --------------------------------------------------------------------------- #
_DEFAULT_AGG: Optional[MagmaAggregator] = None


def _default_agg() -> MagmaAggregator:
    global _DEFAULT_AGG
    if _DEFAULT_AGG is None:
        _DEFAULT_AGG = MagmaAggregator()
    return _DEFAULT_AGG


def count(*, corpus: Optional[str] = None, group_by: Optional[str] = None,
          tools: object = None, **kwargs) -> dict:
    """Module-level `count` over the default (or injected) MAGMA view."""
    agg = MagmaAggregator(tools) if tools is not None else _default_agg()
    return agg.count(corpus=corpus, group_by=group_by, **kwargs)


def group_by(attr: str, *, corpus: Optional[str] = None, tools: object = None,
             **kwargs) -> dict:
    """Module-level `group_by` over the default (or injected) MAGMA view."""
    agg = MagmaAggregator(tools) if tools is not None else _default_agg()
    return agg.group_by(attr, corpus=corpus, **kwargs)


def _agg_for(tools):
    return MagmaAggregator(tools) if tools is not None else _default_agg()


# -- Tier 1 module-level wrappers (these SHADOW the builtins sum/min/max for the rest
#    of this module; every internal fold uses _bsum/_bmin/_bmax, so this is safe) --
def numeric(attr: str, *, tools: object = None, **kwargs) -> dict:
    """Module-level numeric aggregate (op=sum|avg|min|max, optional group_by)."""
    return _agg_for(tools).numeric(attr, **kwargs)


def sum(attr: str, *, tools: object = None, **kwargs) -> dict:  # noqa: A001
    """Module-level `sum(attr)` over the default (or injected) MAGMA view."""
    return _agg_for(tools).numeric(attr, op="sum", **kwargs)


def avg(attr: str, *, tools: object = None, **kwargs) -> dict:
    """Module-level `avg(attr)`."""
    return _agg_for(tools).numeric(attr, op="avg", **kwargs)


def min(attr: str, *, tools: object = None, **kwargs) -> dict:  # noqa: A001
    """Module-level `min(attr)`."""
    return _agg_for(tools).numeric(attr, op="min", **kwargs)


def max(attr: str, *, tools: object = None, **kwargs) -> dict:  # noqa: A001
    """Module-level `max(attr)`."""
    return _agg_for(tools).numeric(attr, op="max", **kwargs)


def distinct(attr: str, *, tools: object = None, **kwargs) -> dict:
    """Module-level `distinct(attr)` cardinality."""
    return _agg_for(tools).distinct(attr, **kwargs)


def cross_tab(attr1: str, attr2: str, *, tools: object = None, **kwargs) -> dict:
    """Module-level 2-D cross-tab `group_by(attr1, attr2)`."""
    return _agg_for(tools).cross_tab(attr1, attr2, **kwargs)


def top_k(attr: str, k: int = 10, *, tools: object = None, **kwargs) -> dict:
    """Module-level `top_k(attr, k, by='count'|<numeric_attr>)`."""
    return _agg_for(tools).top_k(attr, k, **kwargs)


# -- Tier 2 module-level wrappers --
def degree(node: str, *, tools: object = None, **kwargs) -> dict:
    """Module-level single-node `degree(node)`."""
    return _agg_for(tools).degree(node, **kwargs)


def top_by_degree(k: int = 10, *, tools: object = None, **kwargs) -> dict:
    """Module-level `top_by_degree(k)` — the most-connected nodes."""
    return _agg_for(tools).top_by_degree(k, **kwargs)


def count_by_edge_type(*, tools: object = None, **kwargs) -> dict:
    """Module-level edge-type histogram."""
    return _agg_for(tools).count_by_edge_type(**kwargs)


def orphans(*, tools: object = None, **kwargs) -> dict:
    """Module-level `orphans()` — nodes with no incoming edge."""
    return _agg_for(tools).orphans(**kwargs)


__all__ = [
    "MagmaAggregator", "count", "group_by", "numeric", "sum", "avg", "min", "max",
    "distinct", "cross_tab", "top_k", "degree", "top_by_degree",
    "count_by_edge_type", "orphans",
    "resolve_attr", "make_key_fn", "canonical_token", "date_bucket",
    "coerce_number", "numeric_summary", "build_default_tools", "NONE_KEY",
]
