"""pytest coverage for `factlattice_engine.analytics` (the lts-engine ANALYTICS layer).

Hermetic: every aggregation test runs against a hand-built IN-MEMORY lts-engine
partition (see `factlattice_engine.analytics.selftest`), never a real project corpus. The unit tests
for canonicalization / date bucketing need nothing but the pure functions.

`python3 -m factlattice_engine.analytics --selftest` is the equivalent no-pytest run (and it
additionally does a best-effort tier against the live intelligence-console corpus).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
# lts-engine lives in the DEPLOYMENT (a checkout of the public repo + private overlay).
# Seat it on sys.path so a bare `pytest tests/` degrades to a SKIP, not a collect error.
_ENGINE_ROOT = os.environ.get("FACTLATTICE_ENGINE_ROOT",
                              str(Path.home() / "factlattice-internal-dev"))
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

pytest.importorskip("factlattice_engine.analytics",
                    reason="lts-engine library not importable (set FACTLATTICE_ENGINE_ROOT)")
from factlattice_engine.analytics import canonical_token, date_bucket


def _engine_available() -> bool:
    try:
        from factlattice_engine.analytics.selftest import _build_hermetic_tools, _AlwaysFresh
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
    from factlattice_engine.analytics import LtsAggregator
    from factlattice_engine.analytics.selftest import _build_hermetic_tools, _AlwaysFresh
    return LtsAggregator(_build_hermetic_tools(_AlwaysFresh()))


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
    from factlattice_engine.analytics import LtsAggregator
    from factlattice_engine.analytics.selftest import _build_hermetic_tools, _AlwaysStale
    s = LtsAggregator(_build_hermetic_tools(_AlwaysStale())).count(corpus="hermetic")
    assert s.get("success") is False and s.get("stale") is True


@needs_engine
def test_unknown_corpus_fails():
    assert _agg().count(corpus="does-not-exist")["success"] is False


@needs_engine
def test_full_selftest_passes():
    from factlattice_engine.analytics.selftest import run
    assert run() is True
