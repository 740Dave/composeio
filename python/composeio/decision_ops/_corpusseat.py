"""composeio.decision_ops._corpusseat — the CORPUS half of the in-process
lts-engine seat ([LEC-CORPUS-SEAT], the corpus twin of `_enginepath`).

`_enginepath.ensure_engine_on_path` answers "where is the engine LIBRARY".  This
module answers the OTHER half of the same question — "which corpora does an
in-process consumer get to see" — and it exists because that half was left forked.

WHY THIS MODULE EXISTS (2026-08-11): `factlattice_engine.config.build_store()`
derives its corpus set from `FACTLATTICE_CORPUS_CONFIG`, a path into the GITIGNORED
private deployment overlay ([LEC-SPLIT]).  `compose_projects/intelligence/launcher/transport.sh` sets
that variable ONLY inside the `.mcp.json` env blocks of the MCP SUBPROCESSES
(lts-engine, decision-ops).  The console runtime that hosts the IN-PROCESS sources
carries `COMPOSEIO_REPO_ROOT` but NOT `FACTLATTICE_CORPUS_CONFIG` — and neither does
a bare `pytest`.  With it absent, `default_corpora()` falls back SILENTLY to the
engine's generic EXAMPLE registry (`alpha`/`beta`/`gamma`), which matches nothing on
this box, so `build_store()` returns a store serving ZERO corpora.  That is how the
console lts-engine rail ([CXC-DVL-STATUS-SOURCE]) reported "engine unreachable"
against an engine that was up: its probe read `corpus_ids() == []` and fail-closed.

The corpus set therefore MUST be a function of the REPO, with the deployment config
an OVERLAY on top of it rather than a precondition.  `build_engine_store()` is that
one seat, and it is the only sanctioned corpus source for an in-process consumer —
a bare `build_store()` is forbidden by [LEC-CORPUS-SEAT].
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ._enginepath import ensure_engine_on_path

#: The standalone graph that lives OUTSIDE `corpora/` but is a corpus all the same
#: (the defect registry, [SPEC:CIO-DEFECT-REGISTRY]).  Kept here so the rail, the
#: graph viewer and any future consumer agree on it BY REFERENCE.
_STANDALONE_GRAPHS = {
    "defect-graph": (os.path.join("defects", "defect-graph.lts"), "defects"),
}


def repo_root(explicit: Optional[str] = None) -> str:
    """The composeio repo root the corpus set is derived from.

    Precedence: an *explicit* argument, then ``COMPOSEIO_REPO_ROOT`` /
    ``COMPOSEIO_ROOT`` (exported by the launcher and inherited by the in-process
    data layer), then the CANONICAL checkout for this file — canonical, not merely
    containing, so a consumer running out of a linked worktree still resolves the
    MAIN checkout's corpora rather than that worktree's private copies.
    """
    if explicit:
        return explicit
    env = os.environ.get("COMPOSEIO_REPO_ROOT") or os.environ.get("COMPOSEIO_ROOT")
    if env:
        return env
    try:
        from .graph_home import canonical_repo_root
        root = canonical_repo_root(__file__)
        if root is not None:
            return str(root)
    except Exception:  # noqa: BLE001 — a non-git checkout falls through
        pass
    # python/composeio/decision_ops/_corpusseat.py -> repo root
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def repo_corpus_roots(root: Optional[str] = None) -> Dict[str, List[dict]]:
    """``{corpus_id: [root-spec, ...]}`` for every corpus derivable from the REPO
    ALONE — each ``corpora/<id>/`` directory, plus the standalone graphs above.

    The root-spec shape is the engine's own (`{path, area[, only_file]}`, the
    `build_merged_partition` contract), so callers hand it straight to the engine.
    This is the ONE place that knows the repo's corpus LAYOUT; consumers read it by
    reference rather than each re-deriving "corpora/<id>" ([DX-NARROW-WAIST]).
    """
    # ⛔ THE CORPUS HOME IS NO LONGER THE REPO — moved 2026-08-17.
    #    This module's invariant was "the corpus set MUST be a function of the
    #    REPO", written when the corpora lived at `<repo>/corpora/`. The premise
    #    changed, not the invariant: 41 live worktrees each carried their own
    #    fork of that directory, so "a function of the repo" had come to mean "a
    #    function of WHICHEVER COPY the caller happened to be standing in" —
    #    which is the opposite of single-source-of-truth. The invariant is now
    #    stated over the CORPUS HOME: the seat must be non-empty given only a
    #    valid corpus home, with no FACTLATTICE_* variable set. That is what
    #    [LEC-CORPUS-SEAT] was always protecting.
    #    ⚠ [LEC-CORPUS-SEAT] and its enforcing test
    #    (test_lts_engine_rail_probe.py::test_in_process_consumers_resolve_
    #    corpora_without_deployment_env) must be amended to match; the test runs
    #    with FACTLATTICE_* deleted and would otherwise pass over an EMPTY
    #    repo-local set — green while certifying nothing.
    from ..bootstrap_paths import CORPORA_DIR, DEFECTS_DIR
    out: Dict[str, List[dict]] = {}
    cdir = str(CORPORA_DIR)
    if os.path.isdir(cdir):
        for name in sorted(os.listdir(cdir)):
            if name.startswith(".") or not os.path.isdir(os.path.join(cdir, name)):
                continue
            out[name] = [{"path": os.path.join(cdir, name), "area": None}]
    for cid, (rel, area) in _STANDALONE_GRAPHS.items():
        path = os.path.join(str(DEFECTS_DIR.parent), rel)
        if os.path.isfile(path):
            out[cid] = [{"path": os.path.dirname(path), "area": area,
                         "only_file": os.path.basename(path)}]
    return out


def build_engine_store(engine_root: str = "", root: Optional[str] = None) -> Any:
    """THE in-process corpus seat ([LEC-CORPUS-SEAT]): an ``LtsStore`` serving the
    DEDUPED UNION of the deployment-served corpora and the repo-local ones.

    The deployment config (`config.build_store()`, driven by the private
    `FACTLATTICE_CORPUS_CONFIG`) is consulted FIRST and wins for any corpus it
    serves — a deployment that pins richer multi-root/area-labeled mappings keeps
    them.  Every repo-local corpus it does NOT serve is then built from the repo
    files and added.  So the answer is non-empty given only a valid repo root, and
    a configured deployment is a strict superset rather than a precondition.

    RAISES only when the engine itself is unusable (absent/unimportable, or a
    deployment config that blew up AND left nothing to serve) — so a consumer's
    fail-closed path still reports an honestly-unreachable engine, never a
    fabricated reachable.
    """
    if engine_root and engine_root not in __import__("sys").path:
        __import__("sys").path.insert(0, engine_root)
    ensure_engine_on_path()
    # Lazy: an absent engine must be a CALL-time failure the consumer fail-closes
    # on, never an import-time crash of the consumer module.
    from factlattice_engine.config import build_merged_partition, build_store
    from factlattice_engine.store import LtsStore

    deployment_error: Optional[BaseException] = None
    try:
        store = build_store()
    except Exception as exc:  # noqa: BLE001 — a bad private overlay must not
        store = LtsStore()    # blind us to the repo-local corpora
        deployment_error = exc
    try:
        served = set(store.corpus_ids())
    except Exception:  # noqa: BLE001
        served = set()

    for cid, roots in repo_corpus_roots(root).items():
        if cid in served:
            continue
        try:
            part = build_merged_partition(cid, roots, f"proj.{cid}")
        except Exception:  # noqa: BLE001 — one unparseable corpus never dark-outs
            continue        # the rest of the set
        if part is not None and part.nodes:
            store.add_partition(part)
            served.add(cid)

    if not served and deployment_error is not None:
        raise deployment_error
    return store
