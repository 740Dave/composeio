"""Enforcing regression for the DARK Decisions feed
([CXC-DVL-FEED-SOURCE] / [SPEC:CXC-DPE] / [LEC-CONSUMER-WAIST]).

THE DEFECT (2026-08-05): the Decisions viewer (console tab AND standalone window)
showed NO decisions — `self/decisions` was empty on the wire. Two layered causes:

  1. IMPORT FORK (regression): `compose_projects/intelligence/tools/decisions_publisher.py` still did
     `from factlattice_engine.tools import MagmaTools` + defaulted to the deleted
     `~/factlattice` root, and relied on `build_store()`'s deployment defaults for the
     corpus. The magma->lts-engine consolidation de-forked the SIBLING engine consumer
     (`lts_engine_status.py`) onto the shared `composeio.decision_ops._enginepath` seat
     but MISSED this one — classic fork-divergence: a fix to one copy skips the other.
     Result: `ImportError: cannot import name 'MagmaTools'` -> the source crashed, the
     feed never published.
  2. WIRING GAP (under-spec): even with the import fixed, NOTHING launched the feed. It
     was absent from BOTH `compose_projects/intelligence/console/data_sources.py` (the console's
     in-process sources, [DEC-INPROCESS-SOURCES]) AND `start-console-daemons.sh`. The
     console registered the decisions VIEWER LAUNCHER (spawns the window) but not the
     FEED (fills it) — so the window opened DARK. No spec said the feed must be a
     registered running source, so it drifted dark with no alarm.

THE FIX: de-fork the engine seat onto `_enginepath` + build the corpus straight from
the repo (env-robust), and REGISTER `decisions_publisher` as a session-independent
in-process source in `data_sources.py` ([CXC-DVL-FEED-SOURCE]).

`test_publisher_imports_and_projects_nonempty` is the acceptance test: the REAL
`build_projection` reaches the engine on this box and returns a non-empty node set for
the default anchor/corpus. It FAILS against the forked/unwired publisher (ImportError
or empty store) and passes against the fixed one. `test_no_forked_engine_resolution` is
the cross-consumer [LEC-CONSUMER-WAIST] guard, PARAMETRIZED over EVERY in-process engine
consumer, so a future edit that re-forks a retired symbol or a hardcoded root in ANY of
them fails loudly. `test_decisions_feed_is_a_registered_source` pins the wiring: the feed
is a session-independent source the console brings up.
"""
from __future__ import annotations

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'python'))
from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS, INTELLIGENCE_CONSOLE_CORPUS_DIR as _ICC  # corpora live OUTSIDE the repo since 2026-08-17
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO / "python"))              # composeio (+ _enginepath)
sys.path.insert(0, str(_REPO / "compose_projects" / "intelligence" / "tools"))
sys.path.insert(0, str(_REPO / "compose_projects" / "intelligence" / "console"))

_PUBLISHER_SRC = _REPO / "compose_projects" / "intelligence" / "tools" / "decisions_publisher.py"
_RAIL_SRC = _REPO / "compose_projects" / "intelligence" / "tools" / "lts_engine_status.py"

# EVERY in-process source that reaches the lts-engine library. Parametrizing the
# de-fork guard over the SET (not just the source that broke this time) is what turns a
# one-instance fix into a class fix: any consumer that re-forks fails the SAME test.
_ENGINE_CONSUMERS = [_PUBLISHER_SRC, _RAIL_SRC]


def _require_engine():
    """Skip when the lts-engine library is not importable here (a box without
    ~/factlattice-internal-dev). On a box that HAS it, the feed must project real nodes."""
    root = os.environ.get("FACTLATTICE_ENGINE_ROOT") or os.path.expanduser(
        "~/factlattice-internal-dev")
    if os.path.isdir(root) and root not in sys.path:
        sys.path.insert(0, root)
    try:
        import factlattice_engine.config  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"factlattice_engine not importable ({exc}); engine not present")


def _load_publisher():
    spec = importlib.util.spec_from_file_location("decisions_publisher", _PUBLISHER_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # must not raise (the ImportError regression)
    return mod


def test_publisher_imports_and_projects_nonempty():
    """The publisher imports cleanly against the CURRENT engine API and projects a
    NON-EMPTY neighborhood for the default anchor/corpus — not the ImportError-crash /
    empty-store dark feed. Reaches the real corpus via the repo (COMPOSEIO_REPO_ROOT),
    no deployment corpus-config env required."""
    _require_engine()
    assert _ICC.is_dir(), f"corpus home missing: {_ICC} — a skip here hides a dead feed"
    os.environ.setdefault("COMPOSEIO_REPO_ROOT", str(_REPO))
    dp = _load_publisher()
    pl = dp.build_projection()            # default anchor DEC-95-HANDOFF-LINE, depth 2
    assert pl["anchor"] == dp.DEFAULT_ANCHOR
    assert pl["nodes"], (
        "the feed must project real nodes — regression: forked/unwired publisher "
        f"returned an empty projection ({pl!r})")
    ids = {n["id"] for n in pl["nodes"]}
    assert dp.DEFAULT_ANCHOR in ids, "the anchor node itself is present"
    # the live-supersession relationship the feed exists to show is carried
    assert any(e["type"] == "SUPERSEDES" for e in pl["edges"]), (
        "the SUPERSEDES neighborhood edge is carried (the feed's whole point)")


def test_full_graph_is_default_and_complete():
    """[DD-DPE-FULL-GRAPH] (David 2026-08-05): the app default is the WHOLE decision
    graph, not a depth-2 anchor slice. The full projection must carry MANY more nodes
    than the neighborhood, be decision-nodes-only (DEC-/IC-/FIX-), and drop the CONTAINS
    hairball. This is the gate that keeps the app from regressing to a fractional view."""
    _require_engine()
    assert _ICC.is_dir(), f"corpus home missing: {_ICC} — a skip here hides a dead feed"
    os.environ.setdefault("COMPOSEIO_REPO_ROOT", str(_REPO))
    dp = _load_publisher()
    full = dp.build_full_projection()
    nbhd = dp.build_projection(mode=dp.MODE_NEIGHBORHOOD)
    assert full["mode"] == dp.MODE_FULL and full["anchor"] == ""
    assert len(full["nodes"]) > len(nbhd["nodes"]) + 10, (
        "full graph must be substantially bigger than the anchored slice "
        f"(full={len(full['nodes'])}, neighborhood={len(nbhd['nodes'])})")
    assert all(n["id"].startswith(dp.DECISION_PREFIXES) for n in full["nodes"]), (
        "full graph is decision nodes only — no CXC section hub / registry nodes")
    assert not any(e["type"] == "CONTAINS" for e in full["edges"]), (
        "the CONTAINS hairball hub must be excluded from the full graph"
    ) and all(e["type"] in dp.CANONICAL_EDGE_KINDS for e in full["edges"])
    # and build_projection(mode=full) delegates to the same whole-graph result
    via_dispatch = dp.build_projection(mode=dp.MODE_FULL)
    assert len(via_dispatch["nodes"]) == len(full["nodes"])


@pytest.mark.parametrize("src_path", _ENGINE_CONSUMERS,
                         ids=[p.name for p in _ENGINE_CONSUMERS])
def test_no_forked_engine_resolution(src_path):
    """[LEC-CONSUMER-WAIST] guard: EVERY in-process engine consumer must reuse the
    shared engine seat, not re-fork a retired symbol or a hardcoded root. Fails loudly
    if a future edit reintroduces the pattern that caused this defect — in ANY consumer.
    (A docstring naming the retired symbols as the historical breakage is allowed.)"""
    src = src_path.read_text()
    for sym in ("MagmaTools", "MagmaStore"):
        assert f"import {sym}" not in src and f"{sym}(" not in src, (
            f"{src_path.name}: retired {sym} symbol reintroduced (import/construct)")
    assert "magma_" not in src, f"{src_path.name}: retired magma_* dispatch name reintroduced"
    assert '/factlattice"' not in src and "/factlattice'" not in src, (
        f"{src_path.name}: hardcoded engine root reintroduced (the deleted ~/factlattice pattern)")
    assert "_enginepath" in src, (
        f"{src_path.name}: no longer routes through the shared engine seat")


def test_decisions_feed_emitter_is_retired_and_its_launcher_survives():
    """[CXC-GV-EMITTER-RETIRED] — `decisions_publisher` is GONE, and that is correct.

    THIS TEST USED TO ASSERT THE OPPOSITE, and was right to at the time. The original
    gate ([CXC-DVL-FEED-SOURCE]) existed because the feed had gone dark: the publisher
    was session-GATED, so a console with no session pinned brought up a viewer with
    nothing to fill it. Registering it session-independently fixed that.

    The Graph Viewer then superseded the whole data path. `40-decisions-tab.lts:29`:
    *"the widget no longer subscribes the pushed `self/decisions` feed — its
    {nodes, edges} projection is PULLED by a DIRECT lts-engine query on open + refresh
    ([CXC-GV-DATA-PATH]); the `decisions_publisher` bus emitter is retired."* A pushed
    feed for a surface that now pulls is a second copy of the graph, which is the drift
    [INV-DERIVED] exists to eliminate.

    ⛔ WHY THIS ASSERTION IS A GATE AND NOT A TAUTOLOGY. Retirement was DEFERRED once,
    deliberately ([DEC-GRAPH-VIEWER-TRANSITION-SEQUENCING]): landing it early would have
    re-broken the just-fixed feed and stranded the launch bar's `_app/decisions/open`
    tile on a namespace nothing served. So this test pins the two things that had to be
    true BEFORE the emitter could go, and both are verifiable here:
      * the emitter is de-registered, and
      * `decisions_viewer_launcher` SURVIVES it — the window still opens; only its
        filling mechanism changed. A retirement that took the launcher with it would
        pass a naive "publisher absent" check while leaving no way to reach the surface.

    David ratified the retirement 2026-08-11 after the migration was confirmed landed:
    the tile was repointed to the ratified `_app/graph/*` namespace
    (`compose_projects/intelligence/tools/app_launcher.py`, with its own selftest).
    """
    import data_sources  # noqa: E402  (compose_projects/intelligence/console on sys.path)

    def names(session_jsonl):
        return [s.name for s in data_sources.build_console_sources(
            session_jsonl=session_jsonl, intelligence_id="architect",
            mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-08-05")]

    no_session = names(None)
    with_session = names("/tmp/x.jsonl")

    assert "decisions_publisher" not in no_session, (
        "[CXC-GV-EMITTER-RETIRED]: the pushed feed is retired — the Graph Viewer PULLS "
        "its projection by direct lts-engine query. A re-registered emitter is a second "
        "copy of the graph.")
    assert "decisions_publisher" not in with_session, (
        "retired in BOTH pinnings — a session-gated resurrection is how the feed went "
        "dark the first time")

    # The consumer that must NOT have been taken with it.
    assert "decisions_viewer_launcher" in no_session, (
        "the launcher spawns the window and is session-INDEPENDENT; retiring the feed "
        "must not strand the surface")
    assert "decisions_viewer_launcher" in with_session
