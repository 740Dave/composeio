"""pytest coverage for `composeio.magma_agg` (count / group-by over MAGMA `find`).

Hermetic: every aggregation test runs against a hand-built IN-MEMORY MAGMA
partition (see `magma_agg.selftest`), never a real project corpus. The unit tests
for canonicalization / date bucketing need nothing but the pure functions.

`python3 -m composeio.magma_agg --selftest` is the equivalent no-pytest run (and it
additionally does a best-effort tier against the live intelligence-console corpus).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from composeio.magma_agg import canonical_token, date_bucket


def _engine_available() -> bool:
    try:
        from composeio.magma_agg.selftest import _build_hermetic_tools, _AlwaysFresh
        _build_hermetic_tools(_AlwaysFresh())
        return True
    except Exception:  # noqa: BLE001 — engine repo not present on this box
        return False


needs_engine = pytest.mark.skipif(
    not _engine_available(),
    reason="factlattice_engine not importable (set FACTLATTICE_ENGINE_ROOT)")


# --- pure-function units (no engine needed) ---------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("committed   # proven across c9/c10", "committed"),
    ("committed (via COMMIT-abc123)", "committed"),
    ("built   # spec-authored 2026-07-19", "built"),
    ("spec-authored", "spec-authored"),
    ("raised", "raised"),
    ("  ", None),
    (None, None),
    (123, None),
])
def test_canonical_token(raw, expected):
    assert canonical_token(raw) == expected


@pytest.mark.parametrize("raw,gran,expected", [
    ("2026-07-20 (c14)", "day", "2026-07-20"),
    ("2026-07-20 (c14)", "month", "2026-07"),
    ("2026-07-20", "year", "2026"),
    ("2026-07", "month", "2026-07"),
    ("2026", "year", "2026"),
    ("garbage", "month", None),
    (None, "day", None),
])
def test_date_bucket(raw, gran, expected):
    assert date_bucket(raw, gran) == expected


# --- aggregation over a hermetic partition ----------------------------------
def _agg():
    from composeio.magma_agg import MagmaAggregator
    from composeio.magma_agg.selftest import _build_hermetic_tools, _AlwaysFresh
    return MagmaAggregator(_build_hermetic_tools(_AlwaysFresh()))


@needs_engine
def test_count_total_drains_cursor():
    # tools built with default_token_budget=1 -> count must still see all 6 nodes.
    assert _agg().count(corpus="hermetic")["total"] == 6


@needs_engine
def test_group_by_lifecycle_canonical():
    g = _agg().group_by("lifecycle-position", corpus="hermetic")
    assert g["groups"] == {"committed": 3, "built": 1, "raised": 1, "(none)": 1}
    assert g["buckets"][0]["value"] == "committed"  # sorted -count, then value
    assert sum(b["count"] for b in g["buckets"]) == g["total"] == 6


@needs_engine
def test_raw_exposes_more_distinct_than_canonical():
    agg = _agg()
    canon = agg.group_by("lifecycle-position", corpus="hermetic")
    raw = agg.group_by("lifecycle-position", corpus="hermetic", raw=True)
    assert raw["distinct"] > canon["distinct"]


@needs_engine
def test_group_by_prefix_and_domain():
    agg = _agg()
    assert agg.group_by("prefix", corpus="hermetic")["groups"] == {"DEC": 5, "FIX": 1}
    dom = agg.group_by("domain", corpus="hermetic")["groups"]
    assert dom == {"D1": 4, "D2": 2}


@needs_engine
def test_group_by_session_month():
    gs = _agg().group_by("decided-in-session", corpus="hermetic", date_granularity="month")
    assert gs["groups"].get("2026-07") == 3
    assert gs["groups"].get("(none)") == 1


@needs_engine
def test_filter_pass_through():
    agg = _agg()
    assert agg.count(corpus="hermetic", prefix="FIX")["total"] == 1
    gi = agg.group_by("importance", corpus="hermetic", prefix="DEC")
    assert gi["total"] == 5 and gi["groups"].get("high") == 2


@needs_engine
def test_stale_corpus_is_loud_not_silently_wrong():
    from composeio.magma_agg import MagmaAggregator
    from composeio.magma_agg.selftest import _build_hermetic_tools, _AlwaysStale
    s = MagmaAggregator(_build_hermetic_tools(_AlwaysStale())).count(corpus="hermetic")
    assert s.get("success") is False and s.get("stale") is True


@needs_engine
def test_unknown_corpus_fails():
    assert _agg().count(corpus="does-not-exist")["success"] is False


@needs_engine
def test_full_selftest_passes():
    from composeio.magma_agg.selftest import run
    assert run() is True


# --- pure-function units for the numeric fold (no engine needed) ------------
def test_coerce_number():
    from composeio.magma_agg import coerce_number
    assert coerce_number(5) == 5.0
    assert coerce_number(5.5) == 5.5
    assert coerce_number("42") == 42.0
    assert coerce_number("7 # measured") == 7.0
    assert coerce_number("oops") is None
    assert coerce_number(None) is None
    assert coerce_number("") is None
    assert coerce_number(True) is None   # a bool is a flag, not a metric


def test_numeric_summary():
    from composeio.magma_agg import numeric_summary
    assert numeric_summary([]) == {"count": 0, "sum": 0.0, "avg": None,
                                   "min": None, "max": None}
    s = numeric_summary([10.0, 20.0, 30.0])
    assert (s["count"], s["sum"], s["avg"], s["min"], s["max"]) == (3, 60.0, 20.0, 10.0, 30.0)


# --- Tier 1 / Tier 2 over the hermetic GRAPH partition (numeric attrs + edges)
# The `graph` partition (6 nodes, 6 edges incl. 1 dangling + 1 unresolved-target)
# lives in `magma_agg.selftest`; reuse it so the fixture is defined once.
def _graph_agg(token_budget=1000):
    from composeio.magma_agg import MagmaAggregator
    from composeio.magma_agg.selftest import _build_graph_tools, _AlwaysFresh
    return MagmaAggregator(_build_graph_tools(_AlwaysFresh(), token_budget))


def _graph_agg_stale():
    from composeio.magma_agg import MagmaAggregator
    from composeio.magma_agg.selftest import _build_graph_tools, _AlwaysStale
    return MagmaAggregator(_build_graph_tools(_AlwaysStale()))


@needs_engine
def test_numeric_global_skips_nonnumeric_and_absent():
    r = _graph_agg().sum("tokens", corpus="graph")
    assert r["total"] == 6 and r["count"] == 5 and r["skipped"] == 1  # N4 absent -> skip
    assert r["sum"] == 400.0 and r["value"] == 400.0
    assert (r["avg"], r["min"], r["max"]) == (80.0, 10.0, 200.0)


@needs_engine
def test_numeric_commits_skips_nonnumeric():
    r = _graph_agg().avg("commits", corpus="graph")
    # N1=5,N2=3,N5=1,N6=2 numeric; N3 "oops" + N4 absent skipped
    assert r["count"] == 4 and r["skipped"] == 2 and r["sum"] == 11.0
    assert r["value"] == pytest.approx(2.75)


@needs_engine
def test_numeric_grouped_sorted_by_op():
    r = _graph_agg().numeric("tokens", op="sum", corpus="graph", group_by="domain")
    assert r["groups"] == {"D1": 350.0, "D2": 50.0}
    assert [b["value"] for b in r["buckets"]] == ["D1", "D2"]  # sum desc


@needs_engine
def test_numeric_unknown_op():
    r = _graph_agg().numeric("tokens", op="median", corpus="graph")
    assert r["success"] is False and "unknown numeric op" in r["error"]


@needs_engine
def test_distinct():
    r = _graph_agg().distinct("prefix", corpus="graph")
    assert r["distinct"] == 2 and r["values"] == ["DEC", "FIX"] and r["counted"] == 6


@needs_engine
def test_cross_tab_matrix_and_determinism():
    r = _graph_agg().cross_tab("prefix", "domain", corpus="graph")
    assert r["rows"] == ["DEC", "FIX"] and r["cols"] == ["D1", "D2"]
    assert r["matrix"] == {"DEC": {"D1": 3, "D2": 2}, "FIX": {"D1": 1, "D2": 0}}
    assert r["buckets"][0] == {"attr1": "DEC", "attr2": "D1", "count": 3}
    assert sum(b["count"] for b in r["buckets"]) == r["total"] == 6


@needs_engine
def test_top_k_by_count_and_by_numeric():
    agg = _graph_agg()
    tkc = agg.top_k("prefix", 1, corpus="graph")
    assert tkc["by"] == "count" and tkc["buckets"] == [{"value": "DEC", "count": 5}]
    tkn = agg.top_k("domain", 5, by="tokens", agg="sum", corpus="graph")
    assert [b["value"] for b in tkn["buckets"]] == ["D1", "D2"]
    assert tkn["buckets"][0]["sum"] == 350.0


@needs_engine
def test_degree_single_node():
    r = _graph_agg().degree("DEC-N1", corpus="graph")
    assert (r["in_degree"], r["out_degree"], r["degree"]) == (1, 2, 3) and r["found"]


@needs_engine
def test_top_by_degree_deterministic():
    r = _graph_agg().top_by_degree(3, corpus="graph")
    # N1 deg3; N2/N3 deg2 tie -> id order (DEC-N2 < DEC-N3)
    assert [b["node"] for b in r["buckets"]] == ["DEC-N1", "DEC-N2", "DEC-N3"]
    assert r["total"] == 6


@needs_engine
def test_count_by_edge_type_excludes_dangling_and_unresolved():
    r = _graph_agg().count_by_edge_type(corpus="graph")
    assert r["total_edges"] == 6 and r["resolved_edges"] == 4
    assert r["groups"] == {"DEPENDS_ON": 2, "RELATES_TO": 1, "SUPERSEDES": 1}


@needs_engine
def test_orphans():
    r = _graph_agg().orphans(corpus="graph")
    assert r["orphan_count"] == 3
    assert r["orphans"] == ["DEC-N4", "DEC-N6", "FIX-N5"]
    assert r["orphan_ratio"] == pytest.approx(0.5)
    lim = _graph_agg().orphans(corpus="graph", limit=2)
    assert len(lim["orphans"]) == 2 and lim["truncated"] is True


@needs_engine
def test_completeness_under_1token_budget():
    aggd = _graph_agg(token_budget=1)
    assert aggd.sum("tokens", corpus="graph")["sum"] == 400.0     # numeric drain
    assert aggd.top_by_degree(10, corpus="graph")["total"] == 6   # node universe drain
    assert aggd.count_by_edge_type(corpus="graph")["resolved_edges"] == 4  # edges unbudgeted


@needs_engine
@pytest.mark.parametrize("call", [
    lambda a: a.sum("tokens", corpus="graph"),
    lambda a: a.numeric("tokens", op="avg", corpus="graph", group_by="domain"),
    lambda a: a.distinct("domain", corpus="graph"),
    lambda a: a.cross_tab("prefix", "domain", corpus="graph"),
    lambda a: a.top_k("prefix", 3, corpus="graph"),
    lambda a: a.degree("DEC-N1", corpus="graph"),
    lambda a: a.top_by_degree(5, corpus="graph"),
    lambda a: a.count_by_edge_type(corpus="graph"),
    lambda a: a.orphans(corpus="graph"),
])
def test_stale_propagates_loudly_for_every_op(call):
    r = call(_graph_agg_stale())
    assert r.get("success") is False and r.get("stale") is True
