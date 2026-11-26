#!/usr/bin/env python3
"""decisions_publisher — RETIRED bus emitter, kept as the PURE projection shaper
([SPEC:CXC-GRAPH-VIEWER] [CXC-GV-EMITTER-RETIRED], David 2026-08-05).

The always-on MQTT projector that pushed the decision graph to ``self/decisions``
every interval is GONE: the transport + daemon half (``run()``, the paho publish
loop, the ``self/decisions`` publish, the ``singleton`` acquisition, and the
re-anchor subscription) is DELETED and the source is DE-REGISTERED from the console
data layer.  The Graph Viewer now QUERIES lts-engine DIRECTLY in-process through
``composeio.decision_ops.graph_projection`` (a PULL, never a pushed feed) — that
shared, corpus-parameterized module is where the shaper LIVES for the viewer.

What survives here is the PURE, decision-specific projection shaper
(``build_projection`` / ``build_full_projection`` / ``_shape_node`` / ``_shape_edges``
/ ``_norm_lifecycle`` / ``_source_str``) that ``away_watch_publisher`` and
``decisions_curator`` still call by reference — reuse-by-reference, never a fork.
Nothing in this module publishes anything or imports an MQTT transport.

Original job (RETIRED — no longer runs):

  PROJECT — build the lts-engine store and project the decision graph over the
  SEMANTIC edge alphabet (SUPERSEDES / DEPENDS_ON / RELATES_TO / RE_DERIVE_VIA /
  FOLLOWS), shaping a widget-ready projection over ``self/decisions``.
  The DEFAULT mode is the FULL decision graph (every DEC-/IC-/FIX- node + its
  semantic edges — David 2026-08-05, "show the whole history"); ``--mode
  neighborhood`` opts into the anchored, depth-bounded slice ([CXC-DT-QUERY-DRIVEN]:
  a bounded picture around one node, also what a node-tap re-anchor surfaces).

  Payload ([SPEC:CXC-DPE] — the RICH, topology-only projection the built
  Decisions tab consumes; enrichment is ADDITIVE over the shipped thin schema)::
    {"ts": <iso>, "corpus": <id>, "anchor": <DEC id>, "depth": <int>,
     "nodes": [{"id", "title",
                # thin (shipped) fields — retained verbatim
                "lifecycle", "superseded"(bool), "is_anchor"(bool),
                # rich props ([CXC-DPE-NODE-SHAPE]) — present-but-empty, never
                # absent/null; every value a JSON scalar (str / bool)
                "lifecycle-position",   # canonical key ([DD-DPE-LIFECYCLE-NAMING]
                                        #   ruled: canonicalize at the PUBLISHER;
                                        #   `lifecycle` retained as a compat mirror)
                "importance",           # "high"|"medium"|"low"|""
                "session",              # decided-in-session (date str) | ""
                "rationale",            # FULL text, unmodified | ""
                "source",               # "<rel-path>:<start>[-<end>]" | ""
                "dsd"}, ...],           # dsd-source path | ""
     # top-level edge list ([DD-DPE-EDGE-EMISSION] ruled: SEPARATE list, matches
     # the built tab's _coerce_edges); type is ONE of the five canonical kinds
     "edges": [{"type": "SUPERSEDES|DEPENDS_ON|RELATES_TO|RE_DERIVE_VIA|FOLLOWS",
                "source": <id>, "target": <id>}, ...]}

  LIVE-SUPERSESSION: a node is ``superseded`` iff its lifecycle-position is
  "superseded" OR it is the TARGET of a SUPERSEDES edge in the neighborhood — so
  the ``decisions_graph``/Decisions-tab widget renders it dimmed + struck with an
  arrow to its heir the instant the corpus records the supersession.  The
  projection CARRIES the SUPERSEDES edges so the widget can draw the heir arrow.

NAMESPACE — default ``self/decisions`` (the simple first-cut, mirroring
``self/away_watches``).  ``--namespace self/<id>/decisions`` adopts the
carried-identity convention (spec 41 intelligence-host-spawner) if wanted; the
widget binds whatever namespace the scene declares.

The lts-engine library lives in the SEPARATE DEPLOYMENT (``~/factlattice-internal-dev``;
override the engine root with ``--factlattice`` or ``FACTLATTICE_ENGINE_ROOT``), seated
via the SHARED ``composeio.decision_ops._enginepath`` resolver — de-fork, never a private
top-level import or a hardcoded ``~/factlattice`` root ([LEC-CONSUMER-WAIST], the sibling
of the rail-probe defect).  This tool only READS the corpus: it builds the
``intelligence-console`` partition straight from the repo's ``corpora/<corpus>/`` dir
(an ``LtsEngine`` over a fresh ``LtsStore``, mirroring ``decision_ops.lts_refresh`` —
env-robust, needs only the repo root, NOT the deployment's private corpus-config), and
never modifies it.

Run:  python3 decisions_publisher.py --mqtt mqtt://mqtt-broker.local:1883 [--interval 30]
      python3 decisions_publisher.py --once            # build + print the projection
      python3 decisions_publisher.py --anchor DEC-M4-COMPOSER-CURRENT --once
      python3 decisions_publisher.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))

NS = "self/decisions"
# The re-anchor intent the decisions_graph widget publishes on a node tap; a
# future daemon leg can consume it to re-traverse around the tapped node (the
# query-driven neighborhood is explorable).  Reserved seam — not consumed yet.
ANCHOR_INTENT_NS = "_app/decisions/anchor"

DEFAULT_CORPUS = "intelligence-console"
# The default anchor shows a real SUPERSEDES relationship AND a DEPENDS_ON edge
# in its 2-hop neighborhood (DEC-95-HANDOFF-LINE supersedes DEC-90-COMPACT-PREP;
# DEC-CROSSING-IS-CHEAP depends on it) — the ideal live-supersession demo.
DEFAULT_ANCHOR = "DEC-95-HANDOFF-LINE"
DEFAULT_DEPTH = 2

# PROJECTION MODE ([CXC-DT-QUERY-DRIVEN] revisited, David 2026-08-05).  The Decisions
# app wants the WHOLE decision history visible, so ``full`` is the DEFAULT: every
# decision node in the corpus + its semantic edges, NOT a depth-2 slice of one anchor.
# ``neighborhood`` (the original anchored, token-bounded traverse) is DEMOTED to an
# opt-in mode — still reachable via ``--mode neighborhood`` and via a node-tap re-anchor
# (the interactive zoom), just no longer the default.
MODE_FULL = "full"
MODE_NEIGHBORHOOD = "neighborhood"
DEFAULT_MODE = MODE_FULL

# The decision-node id prefixes the FULL projection admits ([DD-DPE-FULL-GRAPH]).  The
# corpus dir also parses structural/registry nodes (the CONTAINS section hub ``CXC-*``,
# ``PROJECTS``/``PROPOSAL`` registries) that are NOT decisions — excluding them keeps the
# full graph the DECISION history, not the parser's every-node dump.
DECISION_PREFIXES = ("DEC-", "IC-", "FIX-")
# The engine root is seated via the SHARED decision_ops resolver (honors
# FACTLATTICE_ENGINE_ROOT, default ~/factlattice-internal-dev); "" => use that seat.
# An explicit --factlattice still overrides.  The dead ~/factlattice literal + the
# retired MagmaTools import were the FORK that kept this feed DARK — de-forked onto
# the same in-process path lts_engine_status / decision_ops use ([LEC-CONSUMER-WAIST]).
DEFAULT_FACTLATTICE = ""

# The meaningful edge alphabet ([CXC-DT-EDGE-SEMANTICS] / [CXC-DPE-EDGE-KINDS]).
# CONTAINS is EXCLUDED: in this corpus every decision is CONTAINS-ed by one
# section hub, so including it reproduces the hairball the query is meant to
# avoid.  RESOLVES is retained as an accepted INPUT (a legacy spelling the corpus may
# still carry) and normalized to RE_DERIVE_VIA on emit (mirrors the tab's alias).
SEMANTIC_EDGES = ["SUPERSEDES", "DEPENDS_ON", "RELATES_TO", "RE_DERIVE_VIA",
                  "FOLLOWS", "RESOLVES"]

# The FIVE canonical edge kinds the built Decisions tab draws in distinct inks
# ([CXC-DPE-EDGE-KINDS]).  Every EMITTED edge type MUST be exactly one of these.
CANONICAL_EDGE_KINDS = frozenset({"SUPERSEDES", "DEPENDS_ON", "RELATES_TO",
                                  "RE_DERIVE_VIA", "FOLLOWS"})
# Emit-time normalization: fold a legacy/aliased input type onto its canonical
# kind so we emit the underscore form ourselves ([CXC-DPE-EDGE-KINDS]: "do not
# rely on the tab's alias fallback for correctness — it is a safety net only").
_EDGE_EMIT_ALIASES = {
    "RESOLVES": "RE_DERIVE_VIA",     # the tab aliases RESOLVES -> RE_DERIVE_VIA
    "DEPENDS-ON": "DEPENDS_ON",
    "RELATES-TO": "RELATES_TO",
    "RE-DERIVE-VIA": "RE_DERIVE_VIA",
}


# Geometry/layout tokens that MUST NEVER appear on a node/edge key
# ([CXC-DPE-TOPOLOGY-ONLY]).  Layout is a RENDER concern owned by the tab; a
# position on the bus is either the wrong owner's choice or dead weight.
_FORBIDDEN_GEOMETRY_TOKENS = (
    "x", "y", "w", "h", "pos", "position", "coord", "coordinate", "layout",
    "level", "rank", "row", "col", "column", "px", "py",
)
# The EXACT key alphabet this projection emits (the positive contract).  A key
# outside this set is what the geometry scan interrogates — so the ratified
# semantic key ``lifecycle-position`` (which contains the token "position") is
# never a false positive, while a future ``x``/``level``/``pos`` slip IS caught.
_ALLOWED_NODE_KEYS = frozenset({
    "id", "title", "lifecycle", "superseded", "is_anchor", "lifecycle-position",
    "importance", "session", "rationale", "source", "dsd",
})
_ALLOWED_EDGE_KEYS = frozenset({"type", "source", "target"})
_ALLOWED_KEYS = {"nodes": _ALLOWED_NODE_KEYS, "edges": _ALLOWED_EDGE_KEYS}


def _geometry_key_violations(payload: Dict[str, Any]) -> List[str]:
    """Return ``"<list>[<i>].<key>"`` for every node/edge key that carries a
    forbidden geometry token ([CXC-DPE-TOPOLOGY-ONLY]).  Keys in the emitted
    allowlist are skipped (they are the ratified topology/semantic contract); any
    OTHER key is flagged if it EQUALS a forbidden token, splits (on non-alnum)
    into a word that is one, or contains a multi-char token as a substring."""
    import re as _re
    multichar = tuple(tok for tok in _FORBIDDEN_GEOMETRY_TOKENS if len(tok) > 2)
    bad: List[str] = []
    for coll in ("nodes", "edges"):
        allowed = _ALLOWED_KEYS[coll]
        for i, item in enumerate(payload.get(coll) or []):
            if not isinstance(item, dict):
                continue
            for k in item:
                if k in allowed:
                    continue
                lk = str(k).lower()
                words = set(_re.split(r"[^a-z0-9]+", lk)) | {lk}
                if (words & set(_FORBIDDEN_GEOMETRY_TOKENS)
                        or any(tok in lk for tok in multichar)):
                    bad.append(f"{coll}[{i}].{k}")
    return bad


def _repo_root() -> str:
    """The composeio repo root — COMPOSEIO_REPO_ROOT (exported by the launcher /
    inherited by the in-process data layer) if set, else the path derived from this
    file.  This is what makes the corpus build env-robust: the running console carries
    COMPOSEIO_REPO_ROOT but NOT the deployment's private FACTLATTICE_CORPUS_CONFIG."""
    return os.environ.get("COMPOSEIO_REPO_ROOT") or str(REPO_ROOT)


def _seat_engine(factlattice_root: str = "") -> None:
    """Put the lts-engine library on ``sys.path`` via the ONE shared resolver
    decision_ops + lts_engine_status use (de-fork, [LEC-CONSUMER-WAIST] /
    [DX-NARROW-WAIST]).  An explicit *factlattice_root* (from ``--factlattice``)
    wins; otherwise the seat honors ``FACTLATTICE_ENGINE_ROOT`` (default
    ``~/factlattice-internal-dev``).  Imported LAZILY so an absent engine/resolver is
    a query-time failure, never an import-time crash of the source module."""
    if factlattice_root and factlattice_root not in sys.path:
        sys.path.insert(0, factlattice_root)
    from composeio.decision_ops._enginepath import ensure_engine_on_path  # noqa: E402
    ensure_engine_on_path()


def _build_engine(corpus: str = DEFAULT_CORPUS, factlattice_root: str = ""):
    """Return an ``LtsEngine`` serving composeio's ``corpus`` from its
    ``corpora/<corpus>/`` dir under the repo root.  De-forked engine seat; corpus
    built straight from the repo FILES (``build_merged_partition`` + a fresh
    ``LtsStore``), mirroring ``decision_ops.lts_refresh._build_tools`` — so it works
    with ONLY the repo root, independent of the deployment's private corpus-config.
    READ-ONLY — never writes to the engine or a corpus."""
    _seat_engine(factlattice_root)
    from factlattice_engine.config import build_merged_partition   # noqa: E402
    from factlattice_engine.store import LtsStore                  # noqa: E402
    from factlattice_engine.tools import LtsEngine                 # noqa: E402
    # ⛔ THIRD FORK of the corpus-layout literal, found 2026-08-17 when the corpora
    #    moved out of the repo. [LEC-CORPUS-SEAT] and [DX-NARROW-WAIST] both say the
    #    layout is known in exactly ONE place; `_corpusseat` is that place, and this
    #    line re-derived `<repo>/corpora/<id>` independently. The tell that it was a
    #    fork and not a copy: it kept working for months and then went dark the
    #    moment the shared seat moved, with the feed rendering EMPTY against a
    #    healthy engine.
    import sys as _s, os as _o
    _p = _o.path.join(_repo_root(), "python")
    if _p not in _s.path:
        _s.path.insert(0, _p)
    from composeio.bootstrap_paths import CORPORA_DIR as _CORPORA   # noqa: E402
    corpus_dir = str(_CORPORA / corpus)
    part = build_merged_partition(corpus, [{"path": corpus_dir, "area": "decisions"}],
                                  f"proj.{corpus}")
    store = LtsStore()
    if part is not None and part.nodes:
        store.add_partition(part)
    return LtsEngine(store)


def _norm_lifecycle(v: Any) -> str:
    """lifecycle-position token stripped of any trailing inline `# ...` comment
    the parser preserved (`superseded   # applies to ...` -> `superseded`)."""
    return str(v or "").split("#", 1)[0].strip().lower()


def _source_str(nd: Dict[str, Any]) -> str:
    """The node's parsed source location as ``<source_file>:<start>[-<end>]``
    (``source_file`` is already corpus-root-relative from the engine; ``line_range``
    is ``[start, end]``).  A single-line node renders ``<source_file>:<line>``.
    Returns ``""`` when the location is unavailable ([CXC-DPE-NODE-SHAPE]).
    TOPOLOGY-ONLY: this is a source-EVIDENCE string (a doc coordinate), not a
    layout position — it names WHERE the decision is authored, not where the
    node is drawn ([CXC-DPE-TOPOLOGY-ONLY])."""
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


def _shape_edges(raw_edges: List[Dict[str, Any]]) -> tuple:
    """Dedupe + canonicalize the raw edge list, returning ``(edges,
    superseded_targets)``.  Each type is normalized to EXACTLY ONE of the five
    canonical kinds ([CXC-DPE-EDGE-KINDS]); a non-canonical type after aliasing is
    dropped.  Shared by the neighborhood AND the full-graph projection so both emit
    the identical edge contract (de-fork, one shaper)."""
    edges: List[Dict[str, str]] = []
    seen = set()
    superseded_targets = set()
    for e in raw_edges:
        etype = str(e.get("type") or "").upper().replace(" ", "_")
        etype = _EDGE_EMIT_ALIASES.get(etype, etype)
        src, tgt = e.get("source"), e.get("target")
        if not src or not tgt or etype not in CANONICAL_EDGE_KINDS:
            continue
        key = (etype, src, tgt)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"type": etype, "source": src, "target": tgt})
        if etype == "SUPERSEDES":
            superseded_targets.add(tgt)   # the heir arrow's tail
    return edges, superseded_targets


def _shape_node(nd: Dict[str, Any], anchor: Optional[str],
                superseded_targets: set) -> Optional[Dict[str, Any]]:
    """Shape one raw corpus node into the widget's rich [CXC-DPE-NODE-SHAPE] dict
    (or None when it carries no id).  Shared by both projection modes."""
    nid = nd.get("id")
    if not nid:
        return None
    props = nd.get("properties") or {}
    lifecycle = _norm_lifecycle(props.get("lifecycle-position"))
    superseded = lifecycle == "superseded" or nid in superseded_targets
    return {
        "id": nid,
        "title": str(nd.get("title") or ""),
        # -- thin (shipped) fields — retained verbatim ([CXC-DPE-BACKWARD-COMPAT])
        "lifecycle": lifecycle,        # compat mirror (older thin consumers)
        "superseded": superseded,
        "is_anchor": nid == anchor,
        # -- canonical lifecycle key ([DD-DPE-LIFECYCLE-NAMING] ruled: emit
        #    lifecycle-position at the publisher; the tab reads either)
        "lifecycle-position": lifecycle,
        # -- rich props ([CXC-DPE-NODE-SHAPE]) — present-but-empty, never
        #    absent/null; each a JSON scalar string
        "importance": str(props.get("importance") or "").strip().lower(),
        "session": str(props.get("decided-in-session") or "").strip(),
        "rationale": str(props.get("rationale") or ""),   # FULL, no truncation
        "source": _source_str(nd),
        "dsd": str(props.get("dsd-source") or "").strip(),
    }


def build_full_projection(corpus: str = DEFAULT_CORPUS,
                          factlattice_root: str = DEFAULT_FACTLATTICE,
                          _tools: Any = None) -> Dict[str, Any]:
    """The FULL decision graph ([DD-DPE-FULL-GRAPH], David 2026-08-05): EVERY decision
    node in ``corpus`` (ids prefixed ``DEC-``/``IC-``/``FIX-``) plus every SEMANTIC edge
    between them — the whole history, not a depth-2 anchor slice.

    Reads the partition DIRECTLY (``store.get_partition`` + ``iter_edges``) rather than a
    token-bounded ``traverse``, so NO node is dropped to a token budget — the widget gets
    the whole set (the CONTAINS section hub is excluded by the semantic edge filter, so
    this is the graph without the hairball).  ``anchor`` is empty and ``depth`` null in
    the full payload; the payload keys are otherwise IDENTICAL to the neighborhood mode's,
    so the widget renders either without change."""
    if _tools is None:
        _tools = _build_engine(corpus, factlattice_root)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    part = _tools.store.get_partition(corpus)
    if part is None:
        return {"ts": ts, "corpus": corpus, "anchor": "", "depth": None,
                "mode": MODE_FULL, "nodes": [], "edges": [],
                "error": f"corpus not served: {corpus!r}"}

    admitted = {nid for nid in part.nodes
                if str(nid).startswith(DECISION_PREFIXES)}
    # edges only among admitted decision nodes; the shaper drops CONTAINS + non-canonical
    edges, superseded_targets = _shape_edges(
        [e for e in part.iter_edges()
         if e.get("source") in admitted and e.get("target") in admitted])
    nodes: List[Dict[str, Any]] = []
    for nid in sorted(admitted):
        shaped = _shape_node(part.get_node(nid), None, superseded_targets)
        if shaped is not None:
            nodes.append(shaped)
    return {"ts": ts, "corpus": corpus, "anchor": "", "depth": None,
            "mode": MODE_FULL, "nodes": nodes, "edges": edges}


def build_projection(anchor: str = DEFAULT_ANCHOR,
                     corpus: str = DEFAULT_CORPUS,
                     depth: int = DEFAULT_DEPTH,
                     factlattice_root: str = DEFAULT_FACTLATTICE,
                     _tools: Any = None, *, mode: str = MODE_NEIGHBORHOOD) -> Dict[str, Any]:
    """Project the decision graph for the ``decisions_graph`` widget.

    ``mode=full`` returns the WHOLE decision graph (delegates to
    ``build_full_projection``); ``mode=neighborhood`` (the historical default of THIS
    function, kept so the enrichment/selftest gates and node-tap re-anchor stay valid)
    traverses the anchor's SEMANTIC neighborhood to ``depth``.  ``_tools`` may be injected
    (tests); otherwise a corpus-backed ``LtsEngine`` is built from the repo."""
    if mode == MODE_FULL:
        return build_full_projection(corpus, factlattice_root, _tools=_tools)
    if _tools is None:
        _tools = _build_engine(corpus, factlattice_root)
    res = _tools.traverse(corpus=corpus, start_node=anchor,
                          edge_types=SEMANTIC_EDGES, max_depth=depth,
                          token_budget=8000)
    if not res.get("success"):
        return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "corpus": corpus, "anchor": anchor, "depth": depth,
                "nodes": [], "edges": [],
                "error": res.get("error") or res.get("note")}

    edges, superseded_targets = _shape_edges(res.get("edges", []))
    nodes: List[Dict[str, Any]] = []
    for nd in res.get("nodes", []):
        shaped = _shape_node(nd, anchor, superseded_targets)
        if shaped is not None:
            nodes.append(shaped)

    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "corpus": corpus, "anchor": anchor, "depth": depth,
            "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# transport (mirrors away_watch_publisher._publish + paho subscribe)
# ---------------------------------------------------------------------------
# [CXC-GV-EMITTER-RETIRED] the transport + daemon half (``_parse_mqtt_url`` and
# ``run()`` — the paho publish loop, the ``self/decisions`` publish, the singleton
# acquisition, and the re-anchor subscription) is DELETED.  The Graph Viewer QUERIES
# lts-engine directly in-process ([CXC-GV-DATA-PATH]); the pure shaper above
# survives for ``away_watch_publisher`` / ``decisions_curator``.


# ---------------------------------------------------------------------------
# selftest — logic only, no broker, no network (reads the real corpus read-only)
# ---------------------------------------------------------------------------
def _selftest(factlattice_root: str = DEFAULT_FACTLATTICE) -> int:
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    check(_norm_lifecycle("superseded   # applies to the OLD ...")
          == "superseded", "(0) lifecycle comment stripped")
    check(_norm_lifecycle(None) == "", "(1) None lifecycle -> empty")

    # Corpus-backed gates: need the lts-engine importable AND the composeio corpus
    # dir present.  Absent either (a box without the deployment / the repo) => SKIP the
    # corpus tier; the logic-only checks above stay the always-on gate.
    try:
        _eng = _build_engine(DEFAULT_CORPUS, factlattice_root)
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: lts-engine not available ({type(exc).__name__}: {exc})")
        return 0

    pl = build_projection(DEFAULT_ANCHOR, DEFAULT_CORPUS, DEFAULT_DEPTH,
                          factlattice_root, _tools=_eng)
    if not pl.get("nodes"):
        print(f"SKIP: corpus {DEFAULT_CORPUS!r} not served / empty "
              f"(anchor {DEFAULT_ANCHOR!r} -> 0 nodes)")
        return 0
    check(pl["anchor"] == DEFAULT_ANCHOR, "(2) anchor echoed")
    check(pl["nodes"] and pl["edges"], "(3) non-empty neighborhood")
    ids = {n["id"] for n in pl["nodes"]}
    check(DEFAULT_ANCHOR in ids, "(4) anchor node present")
    anchor_n = next(n for n in pl["nodes"] if n["id"] == DEFAULT_ANCHOR)
    check(anchor_n["is_anchor"] is True, "(5) anchor flagged")
    # the live-supersession relationship is carried + rendered-ready
    sup = [e for e in pl["edges"] if e["type"] == "SUPERSEDES"]
    check(sup, "(6) SUPERSEDES edge carried")
    check(any(e["source"] == DEFAULT_ANCHOR
              and e["target"] == "DEC-90-COMPACT-PREP" for e in sup),
          "(7) real DEC-95 supersedes DEC-90 edge")
    old = next((n for n in pl["nodes"] if n["id"] == "DEC-90-COMPACT-PREP"),
               None)
    check(old is not None and old["superseded"] is True,
          "(8) superseded node marked superseded")
    check(any(e["type"] == "DEPENDS_ON" for e in pl["edges"]),
          "(9) DEPENDS_ON edge in neighborhood")
    # neighborhood, not hairball ([CXC-DT-QUERY-DRIVEN]).  NOTE: the [CXC-DPE]
    # widening from 3 -> 5 edge kinds can pull a few more 2-hop neighbors in, so
    # the bound is relaxed from the pre-widening 12 to a value still WELL under
    # the 70+-node full graph (the point is "bounded, not the whole hairball").
    check(len(pl["nodes"]) <= 30, "(10) neighborhood bounded (<=30 nodes)")
    check(not any(e["type"] == "CONTAINS" for e in pl["edges"]),
          "(11) CONTAINS hub excluded (no hairball)")
    check(json.dumps(pl), "(12) projection JSON-serializable")
    # a different anchor also resolves (query is the frame)
    pl2 = build_projection("DEC-M4-COMPOSER-CURRENT", DEFAULT_CORPUS, 1,
                           factlattice_root, _tools=_eng)
    check(any(e["type"] == "SUPERSEDES"
              and e["target"] == "DEC-CTK-COMPOSER" for e in pl2["edges"]),
          "(13) alt anchor supersedes DEC-CTK-COMPOSER")

    # -- [SPEC:CXC-DPE] enrichment gates ----------------------------------
    rich_keys = ("importance", "session", "rationale", "source", "dsd",
                 "lifecycle-position")
    check(all(all(k in n for k in rich_keys) for n in pl["nodes"]),
          "(14) every node carries the six rich keys (present, maybe empty)")
    check(all(n.get("lifecycle-position") == n.get("lifecycle")
              for n in pl["nodes"]),
          "(15) canonical lifecycle-position mirrors the compat lifecycle key")
    check(any(n["importance"] in ("high", "medium", "low")
              for n in pl["nodes"]),
          "(16) at least one non-empty importance in {high,medium,low}")
    check(any(n["session"] for n in pl["nodes"]),
          "(17) at least one non-empty session (decided-in-session)")
    check(any(n["rationale"] for n in pl["nodes"]),
          "(18) at least one non-empty rationale")
    import re as _re
    check(any(_re.match(r"^.+:\d+(-\d+)?$", n["source"])
              for n in pl["nodes"]),
          "(19) at least one source matching <path>:<line>[-<line>]")
    check(pl["edges"] and all(e["type"] in CANONICAL_EDGE_KINDS
                              for e in pl["edges"]),
          "(20) every edge type is one of the five canonical kinds")
    # TOPOLOGY-ONLY: no node/edge key carries a geometry token ([CXC-DPE-VERIFY-TOPOLOGY])
    geo = _geometry_key_violations(pl)
    check(not geo, f"(21) topology-only — no geometry keys ({geo})")

    # -- [DD-DPE-FULL-GRAPH] full-graph mode (David 2026-08-05) -----------
    full = build_full_projection(DEFAULT_CORPUS, factlattice_root, _tools=_eng)
    check(full.get("mode") == MODE_FULL, "(22) full projection tags mode=full")
    check(len(full["nodes"]) > len(pl["nodes"]),
          "(23) full projection is BIGGER than the depth-2 neighborhood slice")
    check(all(n["id"].startswith(DECISION_PREFIXES) for n in full["nodes"]),
          "(24) full graph is DECISION nodes only (DEC-/IC-/FIX-, no CXC hub/registries)")
    check(all(e["type"] in CANONICAL_EDGE_KINDS for e in full["edges"])
          and not any(e["type"] == "CONTAINS" for e in full["edges"]),
          "(25) full-graph edges are canonical semantic kinds (no CONTAINS hairball)")
    check(not any(n["is_anchor"] for n in full["nodes"]),
          "(26) full graph has no single anchor (whole history, not a slice)")
    check(geo == _geometry_key_violations(full),
          "(27) full projection is topology-only too")
    check(json.dumps(full), "(28) full projection JSON-serializable")

    total = 28
    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: decisions_publisher selftest ({total}/{total})")
    return 0


def main(argv=None) -> int:
    # [CXC-GV-EMITTER-RETIRED] RETIRED emitter — no longer publishes.  The Graph
    # Viewer queries lts-engine directly ([CXC-GV-DATA-PATH]); this entry point is
    # now a SHAPER shim (``--once`` prints one projection, ``--selftest`` gates it).
    ap = argparse.ArgumentParser(
        description="RETIRED emitter — shaper shim ([CXC-GV-EMITTER-RETIRED]). "
                    "No longer publishes self/decisions; the Graph Viewer queries "
                    "lts-engine directly. Use --once / --selftest for the shaper.")
    ap.add_argument("--mode", choices=(MODE_FULL, MODE_NEIGHBORHOOD),
                    default=DEFAULT_MODE,
                    help="full = the WHOLE decision graph (default); neighborhood = "
                         "the anchored, depth-bounded slice ([CXC-DT-QUERY-DRIVEN])")
    ap.add_argument("--anchor", default=DEFAULT_ANCHOR,
                    help="the decision id whose neighborhood to project "
                         "(neighborhood mode only)")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--depth", type=int, default=DEFAULT_DEPTH,
                    help="traverse BFS depth (neighborhood mode only)")
    ap.add_argument("--factlattice", default=DEFAULT_FACTLATTICE,
                    help="explicit lts-engine LIBRARY root override; default '' "
                         "resolves via the shared FACTLATTICE_ENGINE_ROOT seat "
                         "(~/factlattice-internal-dev). The corpus is read from the "
                         "repo (COMPOSEIO_REPO_ROOT), not from this path.")
    ap.add_argument("--mqtt", default=None,
                    help="ACCEPTED-BUT-IGNORED (retired): kept so the console data "
                         "layer's stale registration, if any, spawns a clean no-op.")
    ap.add_argument("--interval", type=float, default=None,
                    help="ACCEPTED-BUT-IGNORED (retired).")
    ap.add_argument("--once", action="store_true",
                    help="build + print the projection once (no broker needed)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest(a.factlattice)
    if a.once:
        pl = build_projection(a.anchor, a.corpus, a.depth, a.factlattice,
                              mode=a.mode)
        print(json.dumps(pl, indent=2))
        return 0
    sys.stderr.write(
        "[decisions_publisher] RETIRED ([CXC-GV-EMITTER-RETIRED]) — this emitter no "
        "longer publishes self/decisions. The Graph Viewer queries lts-engine "
        "directly. Use --once / --selftest for the shaper.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
