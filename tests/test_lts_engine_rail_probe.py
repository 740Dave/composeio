"""Enforcing regression for the console lts-engine status rail's "engine unreachable"
false negative ([CXC-DVL-STATUS-SOURCE] / [LEC-CONSUMER-WAIST]).

THE DEFECT (2026-07-25): the rail (`compose_projects/intelligence/tools/lts_engine_status.py`) was born
permanently "unreachable". Its probe FORKED the pre-consolidation pattern — a hardcoded
`~/factlattice` root (deleted 2026-07-23) plus `from factlattice_engine.tools import
MagmaTools` (a symbol retired in the magma->lts-engine rename). Both raise, so the probe
fell through to `unreachable_status()` and the rail showed "engine unreachable" forever,
while decision_ops reached + refreshed the SAME engine on the box all session. A false
negative from re-forking the exact breakage class the d414054 re-point had already closed.

THE FIX: de-fork onto the ONE shared in-process path decision_ops uses — the shared
engine-path seat (`composeio.decision_ops._enginepath.ensure_engine_on_path`) + the
engine's published live API (`config.build_store` + `store.stats`/`store.freshness`),
with the corpus set taken from the engine's OWN `store.corpus_ids()`.

`test_rail_reports_reachable_with_real_counts` is the acceptance test: it runs the REAL
probe against the engine on this box and asserts REACHABLE with real (>0) corpus counts.
It FAILS against the forked probe (which returns `reachable:false`) and passes against the
de-forked one. `test_genuine_engine_down_degrades_to_unreachable` pins the honest fallback:
a probe that finds no served corpus (a real query-path failure) still degrades to
`unreachable_status()`, never a faked reachable. `test_no_forked_engine_resolution` is the
cross-consumer [LEC-CONSUMER-WAIST] guard: the source must not carry a retired `MagmaTools`
import or a hardcoded engine root, and must route through the shared seat.

THE RECURRENCE (2026-08-11) — the SAME "engine unreachable" false negative, reached by a
different road, because the 2026-07-25 de-fork above only covered HALF the seat. It fixed
engine-LIBRARY resolution (`sys.path`) and then took the corpus set from a bare
`config.build_store()` — whose set comes from `FACTLATTICE_CORPUS_CONFIG`, a path into the
GITIGNORED private deployment overlay ([LEC-SPLIT]). `transport.sh` sets that variable ONLY
in the `.mcp.json` env blocks of the MCP SUBPROCESSES. The console runtime that HOSTS this
in-process source carries `COMPOSEIO_REPO_ROOT` but not it; nor does a bare `pytest`. With
it absent, `default_corpora()` degrades silently to the engine's generic EXAMPLE registry
(`alpha`/`beta`/`gamma`), `corpus_ids()` is `[]`, the probe raises "lts-engine reachable but
no served corpora matched []" and fail-closes. The 2026-07-25 fix was "verified live" in a
shell that HAPPENED to export the variable, so the acceptance test above was ENV-CONDITIONAL
from birth: green in that shell, red everywhere else — including inside the live console.

THE FIX: [LEC-CORPUS-SEAT] — the corpus half of the seat, `composeio.decision_ops.
_corpusseat.build_engine_store()`, serving the DEDUPED UNION of the deployment-served
corpora and the corpora derivable from the REPO ROOT ALONE. The deployment config becomes a
superset overlay rather than a precondition.

`test_in_process_consumers_resolve_corpora_without_deployment_env` is the enforcing test and
the one that is NOT env-conditional: it DELETES `FACTLATTICE_CORPUS_CONFIG`/`_ROOT` from the
environment and is PARAMETRIZED over EVERY in-process engine consumer, so the invariant is
proven for all of them at once and a future consumer that reintroduces the dependency fails
loudly regardless of what the invoking shell exports.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO / "python"))  # composeio (+ decision_ops._enginepath)

_RAIL_SRC = _REPO / "compose_projects" / "intelligence" / "tools" / "lts_engine_status.py"

_KEYS = {"ts", "reachable", "fresh", "corpus_count", "total_nodes",
         "total_edges", "last_refresh", "corpora"}


def _load_rail():
    spec = importlib.util.spec_from_file_location("lts_engine_status", _RAIL_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _require_engine():
    """Skip when the lts-engine library is not importable in this environment
    (a box without ~/factlattice-internal-dev). On a box that HAS it, the rail must
    report reachable — that is the defect this test enforces."""
    root = os.environ.get("FACTLATTICE_ENGINE_ROOT") or os.path.expanduser(
        "~/factlattice-internal-dev")
    if os.path.isdir(root) and root not in sys.path:
        sys.path.insert(0, root)
    try:
        import factlattice_engine.config  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"factlattice_engine not importable ({exc}); engine not present")


def test_rail_reports_reachable_with_real_counts():
    """The rail probe reaches the engine on this box and reports real counts —
    NOT the "engine unreachable" false negative."""
    _require_engine()
    mod = _load_rail()
    # root="" => resolve via the shared decision_ops seat; corpora=None => the
    # engine's OWN served set (never a hardcoded guess).
    status = mod.sample(mod.FactlatticeProbe("", None))
    assert set(status) == _KEYS, "status payload keeps the [CXC-DVL-STATUS-SOURCE] shape"
    assert status["reachable"] is True, (
        "rail must report REACHABLE when the engine is up on this box "
        f"(regression: forked probe returned {status!r})")
    assert status["corpus_count"] >= 1, "at least one engine-served corpus"
    assert status["total_nodes"] > 0, "real corpus node count, not zeroed"
    # totals are the honest sum over the per-corpus detail
    assert status["total_nodes"] == sum(c["nodes"] for c in status["corpora"])
    assert status["total_edges"] == sum(c["edges"] for c in status["corpora"])


def test_genuine_engine_down_degrades_to_unreachable():
    """A REAL query-path failure (no served corpus matched) still degrades to the
    honest unreachable_status() — the fix never fakes reachability."""
    _require_engine()
    mod = _load_rail()
    # Engine builds, but this corpus id is not served -> __call__ raises ->
    # sample() fail-closes to the unreachable payload (exercises the real probe path).
    status = mod.sample(mod.FactlatticeProbe("", ["__no_such_corpus__"]))
    assert set(status) == _KEYS
    assert status["reachable"] is False and status["fresh"] is False
    assert status["corpus_count"] == 0 and status["total_nodes"] == 0
    assert status["corpora"] == [] and status["last_refresh"] is None
    # identical to the canonical fail-closed payload (bar the timestamp)
    canon = mod.unreachable_status(ts=status["ts"])
    assert status == canon


def test_no_forked_engine_resolution():
    """[LEC-CONSUMER-WAIST] guard: the rail must reuse the shared engine seat, not
    re-fork a retired symbol or a hardcoded root. Fails loudly if a future edit
    reintroduces the pattern that caused this defect."""
    # Only flag ACTUAL use (import / construction) of the retired symbols — a
    # docstring naming them as the historical breakage is allowed (and wanted).
    src = _RAIL_SRC.read_text()
    for sym in ("MagmaTools", "MagmaStore"):
        assert f"import {sym}" not in src and f"{sym}(" not in src, (
            f"retired {sym} symbol reintroduced (import/construct)")
    assert "magma_" not in src, "retired magma_* dispatch name reintroduced"
    assert "/factlattice\"" not in src and "/factlattice'" not in src, (
        "hardcoded engine root reintroduced (the deleted ~/factlattice pattern)")
    assert "_enginepath" in src, "rail no longer routes through the shared engine seat"


# ---------------------------------------------------------------------------
# [LEC-CORPUS-SEAT] — the CORPUS half of the seat (the 2026-08-11 recurrence)
# ---------------------------------------------------------------------------

def _strip_deployment_env(monkeypatch):
    """Reproduce the environment the LIVE CONSOLE RUNTIME actually has.

    `compose_projects/intelligence/launcher/transport.sh` exports FACTLATTICE_CORPUS_CONFIG /
    FACTLATTICE_CORPUS_ROOT ONLY into the `.mcp.json` env blocks of the MCP
    SUBPROCESSES. The console runtime that hosts the in-process sources carries
    COMPOSEIO_REPO_ROOT and nothing else. Deleting them here is what makes this
    test NOT env-conditional — the flaw that let the 2026-07-25 fix be "verified
    live" in a shell that happened to export them, and recur."""
    monkeypatch.delenv("FACTLATTICE_CORPUS_CONFIG", raising=False)
    monkeypatch.delenv("FACTLATTICE_CORPUS_ROOT", raising=False)
    monkeypatch.setenv("COMPOSEIO_REPO_ROOT", str(_REPO))


def _corpora_via_rail():
    """The console lts-engine status rail ([CXC-DVL-STATUS-SOURCE])."""
    status = _load_rail().sample(_load_rail().FactlatticeProbe("", None))
    if not status["reachable"]:
        return []
    return [c["corpus"] for c in status["corpora"]]


def _corpora_via_seat():
    """The shared corpus seat itself ([LEC-CORPUS-SEAT])."""
    from composeio.decision_ops._corpusseat import build_engine_store
    return list(build_engine_store().corpus_ids())


def _corpora_via_graph_viewer():
    """The Graph Viewer's selectable set ([CXC-GV-CORPUS-SELECTOR])."""
    from composeio.decision_ops.graph_projection import GraphProjector
    return list(GraphProjector(repo_root=str(_REPO)).list_corpora())


#: EVERY in-process lts-engine consumer. A new one belongs HERE — that is what makes
#: this a cross-implementation guard rather than a single-site regression, and what
#: stops the fix from ping-ponging between forked copies (rail fixed 2026-07-25,
#: sibling publisher 2026-08-05, rail broken again by the half it did not cover).
_IN_PROCESS_CONSUMERS = [
    ("lts_engine_status rail", _corpora_via_rail),
    ("_corpusseat.build_engine_store", _corpora_via_seat),
    ("graph_projection.GraphProjector", _corpora_via_graph_viewer),
]


@pytest.mark.parametrize("name,resolve",
                         _IN_PROCESS_CONSUMERS,
                         ids=[n for n, _ in _IN_PROCESS_CONSUMERS])
def test_in_process_consumers_resolve_corpora_without_deployment_env(
        name, resolve, monkeypatch):
    """[LEC-CORPUS-SEAT]: an in-process consumer resolves a NON-EMPTY corpus set
    from the REPO ROOT ALONE, with no FACTLATTICE_* variable set.

    RED before the fix for the rail: a bare `config.build_store()` falls back to the
    engine's generic alpha/beta/gamma example registry, `corpus_ids()` is `[]`, and
    the probe fail-closes to `reachable:false` — the "engine unreachable" false
    negative, against an engine that is up."""
    _require_engine()
    _strip_deployment_env(monkeypatch)
    corpora = resolve()
    assert corpora, (
        f"{name} resolved NO corpora without the deployment-private "
        "FACTLATTICE_CORPUS_CONFIG — it depends on a gitignored overlay the console "
        "runtime does not carry ([LEC-CORPUS-SEAT]); resolve via "
        "composeio.decision_ops._corpusseat.build_engine_store()")
    # the repo's own corpora are reachable from the repo alone, not just some
    # accidental subset — pin one that is unambiguously repo-local.
    assert "platform-specs" in corpora, (
        f"{name} did not see the repo-local corpora/platform-specs corpus "
        f"(saw {sorted(corpora)!r})")


def test_rail_reports_real_counts_without_deployment_env(monkeypatch):
    """The rail's ACCEPTANCE assertion, made env-INDEPENDENT: reachable with real
    counts under the live console runtime's actual environment. This is the exact
    assertion `test_rail_reports_reachable_with_real_counts` makes, minus its
    dependence on what the invoking shell happens to export."""
    _require_engine()
    _strip_deployment_env(monkeypatch)
    mod = _load_rail()
    status = mod.sample(mod.FactlatticeProbe("", None))
    assert set(status) == _KEYS
    assert status["reachable"] is True, (
        "rail must report REACHABLE from the repo root alone — the console runtime "
        f"carries no FACTLATTICE_CORPUS_CONFIG (regression: {status!r})")
    assert status["corpus_count"] >= 1 and status["total_nodes"] > 0
    assert status["total_nodes"] == sum(c["nodes"] for c in status["corpora"])


def test_rail_uses_the_shared_corpus_seat():
    """[LEC-CORPUS-SEAT] source guard: the rail must take its corpus set from the
    shared seat, never from a bare `build_store()` whose set is deployment-private."""
    src = _RAIL_SRC.read_text()
    assert "_corpusseat" in src, "rail no longer routes through the shared corpus seat"
    # Only flag ACTUAL use (import / call), never prose — same convention as
    # `test_no_forked_engine_resolution`: a docstring naming the historical breakage
    # is allowed and wanted.
    assert "import build_store" not in src, (
        "rail reintroduced a bare config.build_store() import — its corpus set then "
        "comes from the gitignored FACTLATTICE_CORPUS_CONFIG overlay, which the "
        "console runtime does not carry ([LEC-CORPUS-SEAT])")
    assert "= build_store()" not in src, "rail reintroduced a bare build_store() call"
