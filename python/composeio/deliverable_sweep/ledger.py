"""composeio.deliverable_sweep.ledger — the deliverable-ledger `.lts` reader/writer.

A deliberately RESTRICTED, self-parseable subset of FactLattice notation (see
corpora/platform-specs/37-deliverable-sweep.lts [SPEC:CXC-DS]) — not a general FactLattice
parser.  Five sections, fixed order:

    frontmatter (---/---)     -> document classification (domain/confidentiality/...)
    ## Config                 -> a fenced ``yaml`` block: roots + filters (the ONLY
                                  section a human hand-tunes)
    ## Deliverables           -> one ``### [DELIV-<SLUG>-<HASH8>] <path>`` node per
                                  artifact ever seen (live or ``missing``), each
                                  followed by flat ``- key: value`` property lines.
                                  The id is UPPERCASE because the FactLattice parser's
                                  node-id grammar is ``^[A-Z][A-Z0-9_\\-]*$``; the
                                  human-readable path is the node TITLE, not the id.
    ## Run History            -> a fenced ``jsonl`` block, one line per sweep run
    ## Findings — Latest Run  -> human rollup, WHOLLY REGENERATED on every write

Multi-writer note (a future live-monitor will share this file, see [CXC-DS-MULTI-
WRITER-SAFE]): node identity is a pure function of (project-root, path) — see
``node_id_for`` in :mod:`composeio.deliverable_sweep.sweep`; ``Ledger.apply_scan``
NEVER writes a key on a pre-existing node other than {hash, modified, missing,
missing-since}; and :meth:`Ledger.save` writes atomically (temp file + ``os.replace``)
so a concurrent reader never observes a torn file.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# ---- the perceive/act field vocabulary --------------------------------------

#: exactly the seven fields GUIDELINES.md names as Intelligence-owned; the app
#: must NEVER assign any value to these other than the NEEDS_ENRICHMENT stamp.
SEMANTIC_FIELDS = [
    "kind", "topics", "summary", "target-audience", "confidentiality",
    "review-outcome", "lifecycle-position",
]
NEEDS_ENRICHMENT = "NEEDS-ENRICHMENT"

#: the field checked for the NEEDS_ENRICHMENT sentinel to compute pending-enrichment
#: — semantic fields plus the conditionally-mechanical generated-in-session.
ENRICHABLE_FIELDS = SEMANTIC_FIELDS + ["generated-in-session"]

#: canonical render order: mechanical fields, then semantic, then anything else
#: (unknown/future schema fields) sorted alphabetically — schema-tolerant pass-through.
FIELD_ORDER = [
    "path", "project-root", "format", "created", "modified", "hash",
    "missing", "missing-since",
] + SEMANTIC_FIELDS + ["generated-in-session", "pending-enrichment"]

DEFAULT_INCLUDE_GLOBS = [
    "docs/**/*.md",
    "docs/**/*.html",
    "docs/**/*.svg",
    "docs/**/*.pdf",
    "decisions/**/*.lts",
]

DEFAULT_EXCLUDE_GLOBS = [
    "**/*.py", "**/*.js", "**/*.ts", "**/*.tsx", "**/*.jsx", "**/*.mjs", "**/*.cjs",
    "**/*.cpp", "**/*.c", "**/*.h", "**/*.hpp", "**/*.go", "**/*.rs", "**/*.java",
    "**/*.sh", "**/*.ino", "**/*.yaml", "**/*.yml", "**/*.json",
]

DEFAULT_DO_NOT_TRACK_DIRS = [
    ".git", "node_modules", "__pycache__", ".pio", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "env", "build", "dist", ".eggs", "site", ".ray", "_generated",
    ".claude",
]

DEFAULT_DO_NOT_TRACK_SUFFIXES = [
    ".pyc", ".pyo", ".egg-info",
    # secrets — belt-and-suspenders alongside the exclude-globs/dirs above
    ".key", ".pem", ".creds", ".env",
    "secrets.yaml", "secrets.yml", "wifi_config.h",
]

#: domain-agnostic on purpose — this is a generic FactLattice-ecosystem tool, not
#: composeio-specific (see corpora/platform-specs/37-deliverable-sweep.lts's portability
#: note). NO domain/confidentiality/artifact-readiness default is baked in; a caller
#: seeds those via ``--domain``/``--confidentiality``/``--artifact-readiness`` (or a
#: hand-edit) on first creation if its domain profile wants them. The one universal
#: marker is `machine-maintained: true` — signals: a TOOL owns this file, don't
#: hand-edit structure, don't lint it as an authored spec (only the `NEEDS-
#: ENRICHMENT` node fields and the Config block are meant for a human/Intelligence
#: hand).
DEFAULT_FRONTMATTER = {
    "machine-maintained": True,
}


def _render_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def default_config() -> Dict[str, List[str]]:
    """NB: ``project-roots`` starts EMPTY, not ``["."]`` — callers explicitly opt in
    via ``--root`` / :meth:`Ledger.ensure_roots`. ``run_sweep`` falls back to ``["."]``
    only when the ledger ends up with no configured root at all, so an explicit
    ``--root`` never silently unions with an unwanted default."""
    return {
        "project-roots": [],
        "include-globs": list(DEFAULT_INCLUDE_GLOBS),
        "exclude-globs": list(DEFAULT_EXCLUDE_GLOBS),
        "do-not-track-dirs": list(DEFAULT_DO_NOT_TRACK_DIRS),
        "do-not-track-suffixes": list(DEFAULT_DO_NOT_TRACK_SUFFIXES),
    }


def ordered_items(fields: Dict[str, str]) -> List[Tuple[str, str]]:
    """*fields* rendered in the canonical order: mechanical, semantic, then any
    unrecognized (future-schema / pass-through) keys sorted alphabetically."""
    items = [(k, fields[k]) for k in FIELD_ORDER if k in fields]
    extras = sorted(k for k in fields if k not in FIELD_ORDER)
    items.extend((k, fields[k]) for k in extras)
    return items


def pending_fields(fields: Dict[str, str]) -> List[str]:
    """The mechanically-computable answer to 'which of this node's enrichable
    fields still carry the NEEDS_ENRICHMENT sentinel' — a literal string-equality
    scan, never a guess. Source of truth for both ``needs_enrichment()`` and the
    node's own ``pending-enrichment`` property."""
    return [k for k in ENRICHABLE_FIELDS if fields.get(k) == NEEDS_ENRICHMENT]


def render_pending_enrichment(fields: Dict[str, str]) -> str:
    return "[" + ", ".join(pending_fields(fields)) + "]"


def stamp_pending_enrichment(fields: Dict[str, str]) -> None:
    """Recompute and set ``fields['pending-enrichment']`` in place from the fields'
    CURRENT values — safe to call any time the app already touches a node (on NEW
    creation, and again whenever a later sweep revisits it), so a node that was only
    partially hand-enriched self-heals to an accurate worklist on the next sweep."""
    fields["pending-enrichment"] = render_pending_enrichment(fields)


def needs_enrichment(fields: Dict[str, str]) -> bool:
    return bool(pending_fields(fields))


# ---- identity merge (the ONE de-forked collapse rule) -------------------------
#
# Two node-field dicts that resolve to the SAME artifact identity — the same
# (project-root, path) — MUST collapse to exactly one node, never two twins. This
# happens in two places that MUST agree byte-for-byte, so both call this single
# helper (reuse-by-reference, never fork): the loader (`_parse_deliverables`, when a
# legacy ledger carries both case-variants of one id) and the sweep's id reconcile
# (`Ledger.reconcile_ids`, when `node_id_for`'s derivation drifts across a code
# version). See [SPEC:CXC-DS] [CXC-DS-NODE-ID] identity-reconcile clause.


def _prefer_primary(a: Dict[str, str], b: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Deterministically pick which of two same-identity field dicts is authoritative.

    Order-independent (``_prefer_primary(a, b)`` and ``(b, a)`` agree on the winner):
      1. a LIVE node beats a ``missing: true`` one (never let a tombstone win);
      2. else the NEWER ``modified`` wins — for a hash-drift pair (same path, two
         content hashes) this keeps the CURRENT-hash version, drops the stale one;
      3. else the MORE-ENRICHED node wins (fewer ``NEEDS-ENRICHMENT`` fields) — so a
         real enrichment is never dropped in favour of a bare placeholder twin;
      4. else stable: the first argument.
    """
    a_missing = a.get("missing") == "true"
    b_missing = b.get("missing") == "true"
    if a_missing != b_missing:
        return (b, a) if a_missing else (a, b)
    am, bm = a.get("modified", ""), b.get("modified", "")
    if am != bm:
        return (a, b) if am > bm else (b, a)
    ap, bp = len(pending_fields(a)), len(pending_fields(b))
    if ap != bp:
        return (a, b) if ap <= bp else (b, a)
    return (a, b)


def merge_identity(a: Dict[str, str], b: Dict[str, str]) -> Dict[str, str]:
    """Collapse two same-identity field dicts into one, PRESERVING ENRICHMENT.

    The primary (see :func:`_prefer_primary`) supplies every field; the secondary
    only FILLS a field the primary left empty or still stamped ``NEEDS-ENRICHMENT``.
    A hand-authored/enriched value therefore never loses to a placeholder twin, and
    ``pending-enrichment`` is recomputed so the merged node's worklist is accurate."""
    primary, secondary = _prefer_primary(a, b)
    merged = dict(primary)
    for k, v in secondary.items():
        pv = merged.get(k)
        if (pv is None or pv == "" or pv == NEEDS_ENRICHMENT) and v not in (None, "", NEEDS_ENRICHMENT):
            merged[k] = v
    stamp_pending_enrichment(merged)
    return merged


# ---- parsing ------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_SECTION_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
#: READ side — deliberately permissive so a LEGACY ledger (ids written before the
#: uppercase fix) still loads. If this were tightened to the conformant grammar, a
#: legacy ledger's 71 nodes would simply not be seen, the scan would find no matching
#: id for any of them, and the sweep would append 71 duplicate un-enriched nodes while
#: the hand-authored ones were dropped on render. Reading loosely + normalising (see
#: ``_conformant_id``) is what makes the migration lossless instead of destructive.
_NODE_HEADER_RE = re.compile(r"^### \[(DELIV-[^\]\s]+)\]\s*(.*?)\s*$", re.MULTILINE)
#: WRITE side — the FactLattice node-id grammar (``factlattice/parser.py::_UPPER_ID_RE``).
#: Every id this module renders MUST match, or the ledger is invisible to lts-engine.
_CONFORMANT_ID_RE = re.compile(r"^[A-Z][A-Z0-9_\-]*$")
_PROP_RE = re.compile(r"^- ([A-Za-z][A-Za-z0-9_-]*): ?(.*)$")


def _conformant_id(node_id: str) -> str:
    """Normalise a legacy lowercase node id to the parser-valid uppercase form.

    A pure case fold is EXACT, not a guess: ``node_id_for`` builds the id from a
    lowercase slug plus lowercase hex, so ``old.upper()`` is byte-identical to what
    ``node_id_for`` now returns for the same (project-root, path). That is what lets
    an existing ledger re-key in place with every hand-authored semantic field intact.
    """
    return node_id if _CONFORMANT_ID_RE.match(node_id) else node_id.upper()


def _split_sections(body: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[heading] = body[start:end]
    return sections


def _extract_fenced(text: str, lang: str) -> Optional[str]:
    m = re.search(r"```" + re.escape(lang) + r"\n(.*?)\n```", text, re.DOTALL)
    return m.group(1) if m else None


def _parse_deliverables(text: str) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    nodes: Dict[str, Dict[str, str]] = {}
    order: List[str] = []
    matches = list(_NODE_HEADER_RE.finditer(text))
    for i, m in enumerate(matches):
        # normalise legacy lowercase ids ON READ, so a pre-fix ledger converges to the
        # conformant id carrying all of its hand-authored fields (never duplicated).
        nid = _conformant_id(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        fields: Dict[str, str] = {}
        for line in block.splitlines():
            pm = _PROP_RE.match(line)
            if pm:
                fields[pm.group(1)] = pm.group(2)
        if nid in nodes:
            # a half-migrated ledger carrying both cases of one id must converge to
            # ONE entry, never two — and the survivor must keep the ENRICHED twin's
            # semantic fields (an earlier last-wins overwrite silently dropped a
            # hand-authored node under a bare placeholder). One shared rule:
            nodes[nid] = merge_identity(nodes[nid], fields)
        else:
            nodes[nid] = fields
            order.append(nid)
    return nodes, order


# ---- the Ledger -----------------------------------------------------------------

class Ledger:
    """The in-memory, round-trippable representation of one deliverable-ledger.lts."""

    def __init__(
        self,
        frontmatter: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, List[str]]] = None,
        nodes: Optional[Dict[str, Dict[str, str]]] = None,
        node_order: Optional[List[str]] = None,
        run_history: Optional[List[Dict[str, Any]]] = None,
    ):
        self.frontmatter: Dict[str, Any] = dict(frontmatter) if frontmatter else dict(DEFAULT_FRONTMATTER)
        self.config: Dict[str, List[str]] = config if config is not None else default_config()
        self.nodes: Dict[str, Dict[str, str]] = nodes if nodes is not None else {}
        self.node_order: List[str] = node_order if node_order is not None else []
        self.run_history: List[Dict[str, Any]] = run_history if run_history is not None else []

    # -- construction --------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        if not path.exists():
            return cls()
        text = path.read_text(encoding="utf-8")
        frontmatter = dict(DEFAULT_FRONTMATTER)
        body = text
        m = _FRONTMATTER_RE.match(text)
        if m:
            fm = yaml.safe_load(m.group(1)) or {}
            if isinstance(fm, dict):
                frontmatter.update(fm)
            body = text[m.end():]
        sections = _split_sections(body)

        config = default_config()
        cfg_block = _extract_fenced(sections.get("Config", ""), "yaml")
        if cfg_block:
            loaded = yaml.safe_load(cfg_block) or {}
            if isinstance(loaded, dict):
                config.update(loaded)

        nodes, node_order = {}, []
        if "Deliverables" in sections:
            nodes, node_order = _parse_deliverables(sections["Deliverables"])

        run_history: List[Dict[str, Any]] = []
        rh_block = _extract_fenced(sections.get("Run History", ""), "jsonl")
        if rh_block:
            for line in rh_block.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    run_history.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return cls(
            frontmatter=frontmatter, config=config,
            nodes=nodes, node_order=node_order, run_history=run_history,
        )

    # -- mutation -------------------------------------------------------------

    def seed_frontmatter(self, extra: Dict[str, Any]) -> None:
        """Set frontmatter keys that are NOT already present. Used to let a caller
        (a CLI flag, a domain profile) supply document-classification metadata
        (domain/confidentiality/artifact-readiness/...) the tool itself has no
        business inventing — never overwrites a key that already has a value,
        whether that value came from a prior write or a hand-edit."""
        for k, v in extra.items():
            if v is not None and k not in self.frontmatter:
                self.frontmatter[k] = v

    def ensure_roots(self, roots: List[str]) -> None:
        """Add *roots* not already configured, comparing by RESOLVED path.

        Raw-string comparison is not enough: ``.`` and ``/abs/path/to/the/same/tree``
        are the same tree but different strings, so a caller passing an absolute root
        would append a SECOND root for a tree already covered. Because a node id is
        ``f(project-root, path)``, every artifact then acquires a second id and the
        ledger silently doubles (observed live: 41 -> 82 nodes, all 41 paths twice,
        the duplicates un-enriched). Comparing realpaths makes that impossible rather
        than merely unlikely."""
        import os as _os
        existing = self.config.setdefault("project-roots", [])
        seen = {_os.path.realpath(str(e)) for e in existing}
        for r in roots:
            rp = _os.path.realpath(str(r))
            if rp not in seen:
                existing.append(r)
                seen.add(rp)

    def reconcile_ids(self, id_fn) -> Dict[str, str]:
        """Re-key every node to the canonical id its OWN (project-root, path) derives
        via *id_fn* (the sweep's ``node_id_for``), so the ledger's ids always match
        what the CURRENT derivation emits.

        This is the dedup-on-identity gate ([CXC-DS-NODE-ID] reconcile clause): the
        artifact's identity is (project-root, path), NOT the derived id STRING. When
        the derivation's case/format drifts across a code version, the stored id no
        longer equals what a scan computes; keying the diff on the id string alone
        then mints a case-variant TWIN (bare ``NEEDS-ENRICHMENT``) beside the enriched
        original and tombstones the original as ``missing`` — the exact ~66-phantom
        duplication this reconcile prevents. Two ids that collapse to one identity are
        MERGED enrichment-preserving (:func:`merge_identity`), never left as twins.

        Returns the ``{old_id: new_id}`` remap (for run-history/report), empty when a
        node lacks the fields to re-derive (kept under its case-folded id, defensively).
        """
        new_nodes: Dict[str, Dict[str, str]] = {}
        new_order: List[str] = []
        remap: Dict[str, str] = {}
        for old_id in self.node_order:
            fields = self.nodes[old_id]
            pr, path = fields.get("project-root"), fields.get("path")
            canon = id_fn(pr, path) if pr and path else _conformant_id(old_id)
            if canon != old_id:
                remap[old_id] = canon
            if canon in new_nodes:
                new_nodes[canon] = merge_identity(new_nodes[canon], fields)
            else:
                new_nodes[canon] = fields
                new_order.append(canon)
        self.nodes = new_nodes
        self.node_order = new_order
        return remap

    def add_new_node(self, node_id: str, fields: Dict[str, str]) -> None:
        """Insert a brand-new node. No-op (returns without change) if the id
        already exists — idempotent, so a re-run (or a future live-monitor racing
        this app) never duplicates a node for the same path.

        Refuses a node id the FactLattice parser would silently skip. Enforced here
        because this is the single entry point for a NEW id, and a non-conformant id
        does not fail loudly downstream — it just makes the entry invisible to
        lts-engine, which is exactly how 71 unparseable entries went unnoticed."""
        if not _CONFORMANT_ID_RE.match(node_id):
            raise ValueError(
                f"node id {node_id!r} violates the FactLattice node-id grammar "
                f"{_CONFORMANT_ID_RE.pattern} — it would be invisible to the parser"
            )
        if node_id in self.nodes:
            return
        self.nodes[node_id] = fields
        self.node_order.append(node_id)

    def touch_mechanical(self, node_id: str, **updates: str) -> None:
        """Update ONLY mechanical keys on an existing node. Refuses (raises) any
        attempt to pass a semantic key — the perceive/act split enforced at the
        write boundary, not just by convention."""
        bad = set(updates) & set(SEMANTIC_FIELDS)
        if bad:
            raise ValueError(f"refusing to write semantic field(s) {bad} on {node_id}")
        self.nodes[node_id].update(updates)

    # -- rendering --------------------------------------------------------------

    def render(self) -> str:
        lines: List[str] = ["---"]
        for k, v in self.frontmatter.items():
            lines.append(f"{k}: {_render_scalar(v)}")
        lines.append("---")
        lines.append("# Deliverable Ledger [LEDGER:DELIV-LEDGER]")
        lines.append("")
        lines.append(
            "> Machine-owned — maintained by `python -m composeio.deliverable_sweep "
            "--write` (spec [SPEC:CXC-DS], corpora/platform-specs/37-deliverable-sweep.lts). "
            "Hand-edit only the `NEEDS-ENRICHMENT` semantic fields under `## "
            "Deliverables`, or the `## Config` filters — every other section (including "
            "this ledger's own `## Findings`) is regenerated on the next write."
        )
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Config")
        lines.append("")
        lines.append("```yaml")
        lines.append(yaml.safe_dump(self.config, sort_keys=False, default_flow_style=False).rstrip("\n"))
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Deliverables")
        lines.append("")
        if not self.node_order:
            lines.append("_(no deliverables recorded yet)_")
            lines.append("")
        for nid in self.node_order:
            fields = self.nodes[nid]
            title = fields.get("path", nid)
            lines.append(f"### [{nid}] {title}")
            for k, v in ordered_items(fields):
                lines.append(f"- {k}: {v}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Run History")
        lines.append("")
        lines.append("```jsonl")
        for entry in self.run_history:
            lines.append(json.dumps(entry, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Findings — Latest Run")
        lines.append("")
        lines.append(self._render_findings())
        lines.append("")
        return "\n".join(lines) + "\n"

    def _render_findings(self) -> str:
        if not self.run_history:
            return "_(no sweep run recorded yet)_"
        last = self.run_history[-1]
        parts = [
            f"Run {last.get('date', '?')} — NEW {last.get('new', 0)}, "
            f"MODIFIED {last.get('modified', 0)}, REMOVED {last.get('removed', 0)}."
        ]
        if last.get("new_ids"):
            parts.append("- NEW: " + ", ".join(last["new_ids"]))
        if last.get("modified_ids"):
            parts.append("- MODIFIED: " + ", ".join(last["modified_ids"]))
        if last.get("removed_ids"):
            parts.append("- REMOVED (flagged, not deleted): " + ", ".join(last["removed_ids"]))
        needs = [nid for nid in self.node_order if needs_enrichment(self.nodes[nid])]
        if needs:
            parts.append(
                f"- NEEDS-ENRICHMENT outstanding on {len(needs)} node(s): " + ", ".join(needs)
            )
        return "\n".join(parts)

    def save(self, path: Path) -> None:
        """Atomic write: render to a sibling temp file, then ``os.replace`` it into
        place, so a concurrent reader (or a future live-monitor co-writer) never
        observes a torn/partial ledger. See [CXC-DS-MULTI-WRITER-SAFE]."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self.render()
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
