"""composeio.decision_ops.graph_projection — the shared, CORPUS-PARAMETERIZED
FactLattice projection shaper the Graph Viewer queries in-process
([SPEC:CXC-GRAPH-VIEWER] [CXC-GV-CONTRACT] / [CXC-GV-DATA-PATH]).

This is the PURE projection shaper EXTRACTED out of the retired
``compose_projects/intelligence/tools/decisions_publisher.py`` bus emitter ([CXC-GV-EMITTER-RETIRED]:
the shaper survives + is generalized; the MQTT daemon half is deleted).  The
Graph Viewer widget imports ``GraphProjector`` and calls it IN-PROCESS through
the ONE shared engine seat (``composeio.decision_ops._enginepath.ensure_engine_on_path``
-> ``factlattice_engine.config.build_store``) — a PULL, never a pushed feed.

Two projection modes ([CXC-GV-PROJECTION-MODES]):

  FULL-GRAPH (default) — ``build_full_projection(part, profile)``: read a corpus
  partition DIRECTLY (``part.nodes`` + ``part.iter_edges()``), admit the profile's
  node kinds, keep only the profile's semantic edges among admitted nodes, and
  ALWAYS drop the CONTAINS / PART_OF hierarchy hubs (the hairball the query avoids,
  [CXC-DT-QUERY-DRIVEN]).  No token budget — the whole set is intended.

  NEIGHBORHOOD (re-anchor zoom) — ``build_projection(anchor, corpus, depth, _tools)``:
  ``LtsEngine(store).traverse(...)`` around an anchor over the profile's edge
  alphabet, shaped to the same topology-only schema.

The emitted shape is topology-only ([CXC-DPE-TOPOLOGY-ONLY] preserved: no geometry
keys) so the widget's existing ``_coerce_nodes`` / ``_coerce_edges`` consume it
unchanged::

    {"ts", "corpus", "anchor", "depth", "mode", "nodes":[...], "edges":[...]}

READ-ONLY: nothing here ever writes the engine or a corpus.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Edge alphabet ([CXC-DT-EDGE-SEMANTICS] / [CXC-DPE-EDGE-KINDS]).
# CONTAINS + PART_OF are the hierarchy hubs — ALWAYS excluded (every node hangs
# off one section hub, so including them reproduces the hairball the query
# avoids).  The FIVE canonical kinds the graph widget draws in distinct inks.
# ---------------------------------------------------------------------------
CANONICAL_EDGE_KINDS = frozenset({"SUPERSEDES", "DEPENDS_ON", "RELATES_TO",
                                  "RE_DERIVE_VIA", "FOLLOWS"})
HUB_EDGE_KINDS = frozenset({"CONTAINS", "PART_OF"})

# Fold a legacy / corpus-spelling input type onto its canonical kind so we emit
# the underscore form ourselves ([CXC-DPE-EDGE-KINDS]).  RELATED_TO is the
# intelligence-console corpus's spelling of RELATES_TO; RESOLVES the decisions
# corpus's spelling of RE_DERIVE_VIA.
_EDGE_EMIT_ALIASES = {
    "RESOLVES": "RE_DERIVE_VIA",
    "RELATED_TO": "RELATES_TO",
    "DEPENDS-ON": "DEPENDS_ON",
    "RELATES-TO": "RELATES_TO",
    "RE-DERIVE-VIA": "RE_DERIVE_VIA",
}

# The full input alphabet a full-graph / traverse read admits before folding.
DEFAULT_SEMANTIC_EDGES = ["SUPERSEDES", "DEPENDS_ON", "RELATES_TO", "RELATED_TO",
                          "RE_DERIVE_VIA", "FOLLOWS", "RESOLVES"]

DEFAULT_CORPUS = "intelligence-console"
DEFAULT_DEPTH = 2


def _norm_edge_type(raw: Any) -> str:
    """Normalize a raw edge type to its canonical underscore kind (or the
    upper-cased raw when unknown — a corpus may carry recurrence verbs like
    REOPENS/INVESTIGATES the widget draws in the default ink)."""
    etype = str(raw or "").upper().replace(" ", "_").replace("-", "_")
    return _EDGE_EMIT_ALIASES.get(etype, etype)


def _norm_lifecycle(v: Any) -> str:
    """lifecycle-position token stripped of any trailing inline ``# ...`` comment
    the parser preserved (``superseded   # applies to ...`` -> ``superseded``)."""
    return str(v or "").split("#", 1)[0].strip().lower()


def _source_str(nd: Dict[str, Any]) -> str:
    """The node's parsed source location as ``<source_file>:<start>[-<end>]``.
    TOPOLOGY-ONLY: a source-EVIDENCE string (a doc coordinate), not a layout
    position ([CXC-DPE-TOPOLOGY-ONLY]).  ``""`` when unavailable."""
    sf = str(nd.get("source_file") or "").strip()
    if not sf:
        return ""
    lr = nd.get("line_range") or []
    if not isinstance(lr, (list, tuple)) or not lr:
        return sf
    try:
        start = int(lr[0]) if lr[0] is not None else None
        end = int(lr[1]) if len(lr) > 1 and lr[1] is not None else None
    except (TypeError, ValueError):
        return sf
    if start is None:
        return sf
    if end is not None and end > start:
        return f"{sf}:{start}-{end}"
    return f"{sf}:{start}"


def _badge_value(nd: Dict[str, Any], props: Dict[str, Any],
                 profile: Dict[str, Any]) -> str:
    """The value of the profile's ``badge_field`` for this node — the corpus's
    OWN semantic field ([CXC-GV-STYLE-PROFILE]).  Looked up in properties first,
    then top-level node keys (``status`` / ``area`` live at the top level), and
    normalized like a lifecycle token.  ``""`` when absent (neutral badge)."""
    field = str((profile or {}).get("badge_field") or "").strip()
    if not field:
        return ""
    raw = props.get(field)
    if raw in (None, ""):
        raw = nd.get(field)
    return _norm_lifecycle(raw)


def _admit_node(node_id: str, profile: Dict[str, Any]) -> bool:
    """A node is admitted to the full-graph projection iff its id carries one of
    the profile's ``node_kinds`` prefixes; an EMPTY node_kinds admits ALL nodes
    (the neutral default profile — legible-but-plain)."""
    kinds = (profile or {}).get("node_kinds") or []
    if not kinds:
        return True
    return any(str(node_id).startswith(str(k)) for k in kinds)


def _admitted_edge_kinds(profile: Dict[str, Any]) -> frozenset:
    """The canonical edge kinds admitted for this corpus: the profile's
    ``edge_alphabet`` keys (normalized) plus the five canonical kinds, minus the
    hierarchy hubs.  A corpus's own recurrence verbs (REOPENS/INVESTIGATES) are
    admitted when the profile lists them."""
    alpha = set(CANONICAL_EDGE_KINDS)
    for k in ((profile or {}).get("edge_alphabet") or {}):
        alpha.add(_norm_edge_type(k))
    return frozenset(alpha - HUB_EDGE_KINDS)


def _shape_edges(raw_edges: Any,
                 admitted_kinds: frozenset) -> Tuple[List[Dict[str, str]], set]:
    """Dedupe + canonicalize a raw edge list to ``[{type, source, target}]`` and
    return the SUPERSEDES targets (each such node renders dimmed+struck with a
    heir arrow, [CXC-DPE] live-supersession).  Hubs are always dropped; a kind
    outside ``admitted_kinds`` is dropped."""
    edges: List[Dict[str, str]] = []
    seen: set = set()
    superseded_targets: set = set()
    for e in raw_edges or []:
        etype = _norm_edge_type(e.get("type"))
        src, tgt = e.get("source"), e.get("target")
        if not src or not tgt:
            continue
        if etype in HUB_EDGE_KINDS or etype not in admitted_kinds:
            continue
        key = (etype, src, tgt)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"type": etype, "source": str(src), "target": str(tgt)})
        if etype == "SUPERSEDES":
            superseded_targets.add(tgt)
    return edges, superseded_targets


def _shape_node(nd: Dict[str, Any], anchor: str, superseded_targets: set,
                profile: Dict[str, Any]) -> Dict[str, Any]:
    """Shape ONE engine node dict to the topology-only widget schema.  A node's
    ``badge`` is its profile ``badge_field`` value ([CXC-GV-STYLE-PROFILE]) —
    the ONE field that makes styling corpus-agnostic + data-driven."""
    nid = nd.get("id")
    props = nd.get("properties") or {}
    lifecycle = _norm_lifecycle(props.get("lifecycle-position"))
    superseded = lifecycle == "superseded" or nid in superseded_targets
    return {
        "id": nid,
        "title": str(nd.get("title") or ""),
        # thin (shipped) fields — retained verbatim ([CXC-DPE-BACKWARD-COMPAT])
        "lifecycle": lifecycle,
        "superseded": superseded,
        "is_anchor": nid == anchor,
        # canonical lifecycle key ([DD-DPE-LIFECYCLE-NAMING])
        "lifecycle-position": lifecycle,
        # the corpus's OWN semantic badge field ([CXC-GV-STYLE-PROFILE])
        "badge": _badge_value(nd, props, profile),
        # rich props ([CXC-DPE-NODE-SHAPE]) — present-but-empty, never absent
        "importance": str(props.get("importance") or "").strip().lower(),
        "session": str(props.get("decided-in-session") or "").strip(),
        "rationale": str(props.get("rationale") or ""),
        "source": _source_str(nd),
        "dsd": str(props.get("dsd-source") or "").strip(),
    }


def resolve_badge_role(badge_value: Any, badge_map: Dict[str, Any]) -> str:
    """The theme role a node's badge maps to under a corpus profile's
    ``badge_map`` ([CXC-GV-STYLE-PROFILE]) — the SHARED, corpus-agnostic resolver
    the renderer uses.  Tries the exact normalized value, then its first token (so
    a freeform ``status`` like ``"live (empty) — <date>"`` still keys on ``live``).
    ``""`` when no entry matches (a neutral badge)."""
    if not badge_map:
        return ""
    v = _norm_lifecycle(badge_value)
    if not v:
        return ""
    role = badge_map.get(v)
    if role:
        return str(role)
    first = v.split()[0] if v.split() else ""
    role = badge_map.get(first)
    return str(role) if role else ""


def _empty(corpus: str, anchor: str, depth: int, mode: str,
           error: Optional[str] = None) -> Dict[str, Any]:
    out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "corpus": corpus, "anchor": anchor, "depth": depth, "mode": mode,
           "nodes": [], "edges": []}
    if error:
        out["error"] = error
    return out


def build_full_projection(part: Any, profile: Optional[Dict[str, Any]] = None,
                          corpus: str = "", anchor: str = "") -> Dict[str, Any]:
    """FULL-GRAPH projection ([CXC-GV-PROJECTION-MODES] default): shape the WHOLE
    corpus partition — every admitted node + its semantic edges, the CONTAINS /
    PART_OF hubs excluded, no token budget ([CXC-GV-QUERY-CALLS]).

    ``part`` is a ``factlattice_engine.store.Partition`` (or any object exposing
    ``.nodes`` dict + ``.iter_edges()``), so an injected fixture works in tests
    with no real engine."""
    profile = profile or {}
    corpus = corpus or getattr(part, "corpus_id", "") or ""
    nodes_map = getattr(part, "nodes", {}) or {}
    admitted_ids = {nid for nid in nodes_map if _admit_node(nid, profile)}
    admitted_kinds = _admitted_edge_kinds(profile)

    raw_edges = [e for e in part.iter_edges()
                 if e.get("source") in admitted_ids
                 and e.get("target") in admitted_ids]
    edges, superseded_targets = _shape_edges(raw_edges, admitted_kinds)

    nodes: List[Dict[str, Any]] = []
    for nid in admitted_ids:
        nd = nodes_map.get(nid)
        if not isinstance(nd, dict):
            continue
        nodes.append(_shape_node(nd, anchor, superseded_targets, profile))

    out = _empty(corpus, anchor, 0, "full")
    out["nodes"] = nodes
    out["edges"] = edges
    return out


def build_projection(anchor: str, corpus: str = DEFAULT_CORPUS,
                     depth: int = DEFAULT_DEPTH,
                     profile: Optional[Dict[str, Any]] = None,
                     _tools: Any = None) -> Dict[str, Any]:
    """NEIGHBORHOOD projection ([CXC-GV-PROJECTION-MODES] re-anchor zoom): the
    bounded ``traverse`` around ``anchor`` over the profile's edge alphabet,
    shaped to the same topology-only schema.  ``_tools`` is an
    ``LtsEngine``-like object (injected in tests / by the projector)."""
    profile = profile or {}
    if _tools is None:
        raise ValueError("build_projection needs an LtsEngine (_tools=...)")
    edge_types = list((profile.get("edge_alphabet") or {}).keys()) \
        or DEFAULT_SEMANTIC_EDGES
    res = _tools.traverse(corpus=corpus, start_node=anchor,
                          edge_types=edge_types, max_depth=depth,
                          token_budget=int(profile.get("token_budget", 8000)))
    if not res.get("success"):
        return _empty(corpus, anchor, depth, "neighborhood",
                      error=res.get("error") or res.get("note"))
    admitted_kinds = _admitted_edge_kinds(profile)
    edges, superseded_targets = _shape_edges(res.get("edges", []), admitted_kinds)
    nodes: List[Dict[str, Any]] = []
    for nd in res.get("nodes", []):
        if not nd or not nd.get("id"):
            continue
        nodes.append(_shape_node(nd, anchor, superseded_targets, profile))
    out = _empty(corpus, anchor, depth, "neighborhood")
    out["nodes"] = nodes
    out["edges"] = edges
    return out


# ===========================================================================
# GraphProjector — the in-process engine seat the Graph Viewer holds
# ([CXC-GV-DATA-PATH] / [CXC-GV-QUERY-CALLS]).  Builds the store ONCE per
# session; reuses it across selector changes / refreshes / re-anchors.
# ===========================================================================
class GraphProjector:
    """Owns ONE lts-engine view for a Graph Viewer session.

    * ``list_corpora()`` — the deduped UNION of the engine's served set and the
      repo-local ``corpora/*`` dirs + ``defects/defect-graph.lts``
      ([CXC-GV-CORPUS-SELECTOR]); engine-unreachable falls back to the repo-local
      list (never a blank dropdown).
    * ``full_projection(corpus)`` / ``neighborhood_projection(corpus, anchor)`` —
      the two query modes, each shaped under the corpus's style profile.

    The store is built ONCE (``_ensure_store``) and reused; ``refresh()`` re-reads
    THROUGH the same store (the engine self-heals a stale index on read,
    [LEC-SELF-HEAL]), never rebuilding per frame ([CXC-GV-QUERY-CALLS] STORE REUSE).

    ``store`` / ``engine`` may be INJECTED (tests) so the data path is provable
    with no real engine ([CXC-GV-VERIFY-DATA-PATH])."""

    def __init__(self, *, style_profiles: Optional[Dict[str, Any]] = None,
                 default_profile: Optional[Dict[str, Any]] = None,
                 engine_root: Optional[str] = None,
                 repo_root: Optional[str] = None,
                 store: Any = None, engine: Any = None) -> None:
        self._profiles = style_profiles or {}
        self._default_profile = default_profile or {}
        self._engine_root = engine_root
        self._repo_root = repo_root or _guess_repo_root()
        self._store = store            # injected or lazily built
        self._engine = engine          # injected or lazily built (LtsEngine)
        self._store_built = store is not None
        self.engine_available: bool = store is not None
        self.last_error: str = ""
        # repo-local partitions built on demand (defect-graph / unserved corpora)
        self._repo_parts: Dict[str, Any] = {}

    # -- profiles ----------------------------------------------------------
    def profile_for(self, corpus: str) -> Dict[str, Any]:
        """The corpus's style profile, or the neutral DEFAULT ([CXC-GV-STYLE-PROFILE]
        DEFAULT PROFILE: an unprofiled corpus is legible-but-plain, never
        unrenderable)."""
        return self._profiles.get(corpus) or self._default_profile

    # -- engine seat -------------------------------------------------------
    def _seat(self) -> None:
        import sys
        root = self._engine_root or os.environ.get(
            "FACTLATTICE_ENGINE_ROOT",
            os.path.join(os.path.expanduser("~"), "factlattice-internal-dev"))
        if root and root not in sys.path:
            sys.path.insert(0, root)
        try:
            from composeio.decision_ops._enginepath import ensure_engine_on_path
            ensure_engine_on_path()
        except Exception:
            pass

    def _ensure_store(self) -> Any:
        """Build the store ONCE via the shared seat; cache it.  Sets
        ``engine_available`` / ``last_error`` (a failure is a rendered state,
        never a crash — [CXC-GV-DATA-PATH] ERROR/EMPTY)."""
        if self._store_built:
            return self._store
        self._store_built = True
        try:
            self._seat()
            # ⛔ WAS a bare `config.build_store()`, which [LEC-CORPUS-SEAT] names as
            #    FORBIDDEN in as many words: it serves ONLY what the private,
            #    gitignored deployment overlay maps, so with no
            #    FACTLATTICE_CORPUS_CONFIG in the environment it returns a store
            #    serving ZERO corpora and the feed renders dark against a perfectly
            #    healthy engine. The sibling `_repo_corpus_ids` was de-forked onto
            #    the shared seat; this path never was, so the two could drift — and
            #    did. Surfaced 2026-08-17 by moving the corpora out of the repo:
            #    the test went red with "NO corpora are loaded at all".
            from ._corpusseat import build_engine_store  # noqa: E402
            self._store = build_engine_store()
            self.engine_available = True
        except Exception as exc:  # noqa: BLE001 — a dark engine is a render state
            self._store = None
            self.engine_available = False
            self.last_error = f"{type(exc).__name__}: {exc}"
        return self._store

    def _ensure_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        store = self._ensure_store()
        if store is None:
            return None
        try:
            self._seat()
            from factlattice_engine.tools import LtsEngine  # noqa: E402
            self._engine = LtsEngine(store)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._engine = None
        return self._engine

    def refresh(self) -> None:
        """Re-read through the SAME store on a user/intent refresh
        ([CXC-GV-FRESHNESS]); the engine self-heals a stale index on read, so no
        rebuild + no viewer restart.  Only a HARD failure re-seats a fresh store."""
        if self._store is None and not self.engine_available:
            # engine was down; try a fresh build (it may be back up)
            self._store_built = False
            self._engine = None
            self._ensure_store()

    # -- corpus enumeration ([CXC-GV-CORPUS-SELECTOR]) --------------------
    def _repo_corpus_ids(self) -> List[str]:
        """The repo-local selectable corpora: every ``corpora/<id>/`` dir plus the
        standalone ``defects/defect-graph.lts`` graph (so defect-graph is
        selectable even when the deployment engine does not pre-serve it).

        DE-FORKED onto the shared corpus seat ([LEC-CORPUS-SEAT]) — the repo's
        corpus LAYOUT is known in exactly one place, so the viewer's selectable set
        and the rail's reported set cannot drift apart."""
        if not self._repo_root:
            return []
        from ._corpusseat import repo_corpus_roots
        return list(repo_corpus_roots(self._repo_root))

    def list_corpora(self) -> List[str]:
        """The DEDUPED UNION of the engine's served set + the repo-local corpora
        ([CXC-GV-CORPUS-SELECTOR]).  Engine-unreachable -> repo-local only (never a
        blank dropdown), with ``last_error`` set for the "engine unavailable" note."""
        served: List[str] = []
        store = self._ensure_store()
        if store is not None:
            try:
                served = list(store.corpus_ids())
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
        seen: set = set()
        out: List[str] = []
        for cid in list(served) + self._repo_corpus_ids():
            if cid and cid not in seen:
                seen.add(cid)
                out.append(cid)
        return out

    def _repo_partition(self, corpus: str) -> Any:
        """Build a repo-local partition for a corpus not in the served set
        (``defect-graph`` from ``defects/defect-graph.lts``; a ``corpora/<id>/``
        dir otherwise).  Cached; None when it cannot be built.

        The root-spec comes from the shared corpus seat ([LEC-CORPUS-SEAT]), so the
        area labels / ``only_file`` pins are authored ONCE."""
        if corpus in self._repo_parts:
            return self._repo_parts[corpus]
        part = None
        try:
            self._seat()
            from factlattice_engine.config import build_merged_partition
            from ._corpusseat import repo_corpus_roots
            roots = repo_corpus_roots(self._repo_root).get(corpus)
            if roots is None:  # not a repo-local corpus at all
                self._repo_parts[corpus] = None
                return None
            part = build_merged_partition(corpus, roots, f"proj.{corpus}")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            part = None
        self._repo_parts[corpus] = part
        return part

    def _partition(self, corpus: str) -> Any:
        store = self._ensure_store()
        part = None
        if store is not None:
            try:
                part = store.get_partition(corpus)
            except Exception:  # noqa: BLE001
                part = None
        if part is None:
            part = self._repo_partition(corpus)
        return part

    # -- the two query modes ([CXC-GV-QUERY-CALLS]) ----------------------
    def full_projection(self, corpus: str) -> Dict[str, Any]:
        """Query the corpus's FULL graph directly ([CXC-GV-PROJECTION-MODES]
        default).  Returns the topology-only shape, or an ``error`` state (engine
        down / corpus empty) the widget renders explicitly — never a crash."""
        profile = self.profile_for(corpus)
        part = self._partition(corpus)
        if part is None:
            if not self.engine_available:
                return _empty(corpus, "", 0, "full",
                              error="lts-engine unavailable")
            return _empty(corpus, "", 0, "full",
                          error=f"corpus {corpus} is empty")
        proj = build_full_projection(part, profile, corpus=corpus)
        if not proj["nodes"]:
            proj["error"] = f"corpus {corpus} is empty"
        return proj

    def neighborhood_projection(self, corpus: str, anchor: str,
                                depth: Optional[int] = None) -> Dict[str, Any]:
        """Query a bounded neighborhood around ``anchor`` ([CXC-GV-PROJECTION-MODES]
        re-anchor zoom) via ``LtsEngine.traverse``, shaped under the profile."""
        profile = self.profile_for(corpus)
        if depth is None:
            depth = int(profile.get("default_depth", DEFAULT_DEPTH))
        engine = self._ensure_engine()
        if engine is None:
            return _empty(corpus, anchor, depth, "neighborhood",
                          error="lts-engine unavailable")
        return build_projection(anchor, corpus, depth, profile=profile,
                                _tools=engine)


def _guess_repo_root() -> str:
    """The CANONICAL composeio repo root — the MAIN checkout, even when this file
    is a linked worktree's copy of itself.

    This root anchors the repo-local partitions the graph viewer builds (the
    defect-graph and other unserved corpora). Derived from `__file__` it silently
    answered "the worktree", so a viewer launched from a worktree rendered that
    worktree's private copy of the registry as if it were the registry — the read
    half of the 2026-08-08 defect that lost two records on the write half. The
    four-`dirname` form survives only as the fallback for a non-git checkout."""
    from .graph_home import canonical_repo_root
    root = canonical_repo_root(__file__)
    if root is not None:
        return str(root)
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


# ---------------------------------------------------------------------------
# style profile loader ([CXC-GV-STYLE-PROFILE] — the D2 declarative DATA FILE)
# ---------------------------------------------------------------------------
def load_style_profiles(path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load the per-corpus style/projection profile YAML beside the scene
    ([DD-GV-GENERALIZE-VS-SUPERSEDE] fixed WHERE it lives: a ``.yaml`` DATA file,
    not a Python registry).  Returns ``(profiles_by_corpus, default_profile)``.
    A missing file yields empty maps (every corpus renders neutral)."""
    try:
        import yaml
    except Exception:
        return {}, {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError:
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    profiles = data.get("profiles") or {}
    default = data.get("default") or {}
    if not isinstance(profiles, dict):
        profiles = {}
    if not isinstance(default, dict):
        default = {}
    return profiles, default


__all__ = [
    "GraphProjector", "build_full_projection", "build_projection",
    "load_style_profiles", "resolve_badge_role", "_norm_lifecycle",
    "_source_str", "_shape_node", "_shape_edges",
    "CANONICAL_EDGE_KINDS", "HUB_EDGE_KINDS",
    "DEFAULT_CORPUS", "DEFAULT_DEPTH",
]
