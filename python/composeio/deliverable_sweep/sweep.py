"""composeio.deliverable_sweep.sweep — the deterministic scan + diff + orchestration.

Sibling to :mod:`composeio.session_sweep` (undone WORK) and
:mod:`composeio.compact_summary` (deterministic mechanical extraction, matched here for
``--selftest`` rigor). No LLM, no network: walks project root(s) per the ledger's
filters, hashes candidate files, diffs against the ledger, and — on ``--write`` —
persists exactly the mechanical fields ([SPEC:CXC-DS] [CXC-DS-FIELD-OWNERSHIP]). Never
assigns a semantic field; never deletes a node.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ledger import (
    NEEDS_ENRICHMENT,
    SEMANTIC_FIELDS,
    Ledger,
    needs_enrichment,
    render_pending_enrichment,
    stamp_pending_enrichment,
)

# The single deliverable ledger now lives in its own corpus dir; resolve its
# location from the single source of truth (bootstrap_paths).
from composeio.bootstrap_paths import DELIVERABLE_LEDGER as DEFAULT_LEDGER_PATH


# ---- identity + content -------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slug(name: str) -> str:
    s = _SLUG_RE.sub("-", name).strip("-").lower()
    return (s or "artifact")[:40]


def node_id_for(project_root: str, relpath: str) -> str:
    """A pure function of (project-root, path) — the SAME file always yields the
    SAME id, whichever process (this batch sweep, or a future live-monitor) computes
    it. This is the multi-writer convergence point ([CXC-DS-MULTI-WRITER-SAFE]).

    The id MUST satisfy the FactLattice node-id grammar ``^[A-Z][A-Z0-9_\\-]*$``
    (``factlattice/parser.py::_UPPER_ID_RE``) or the whole ledger is invisible to
    lts-engine: the parser silently skips every non-conformant bracketed id, so a
    lowercased slug made all 71 entries unparseable while the document header
    ``[LEDGER:DELIV-LEDGER]`` was the only DELIV node in the graph. Uppercasing is
    applied at the id boundary — the human-readable path stays the node TITLE, so
    nothing legible is lost."""
    base = Path(relpath).stem
    slug = _slug(base)
    h = hashlib.sha256(f"{project_root}::{relpath}".encode("utf-8")).hexdigest()[:8]
    return f"DELIV-{slug}-{h}".upper()


def format_of(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return ext or "other"


def content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def mtime_date(path: Path) -> str:
    dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return dt.date().isoformat()


def now_iso() -> str:
    """The single wall-clock read per invocation ([CXC-DS-DETERMINISM]) — callers
    that want reproducible output inject their own value instead of calling this."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- scanning -----------------------------------------------------------------

@dataclass
class ScanEntry:
    node_id: str
    path: str            # relative POSIX path from project_root
    project_root: str    # the configured root label (as given in config)
    abs_path: Path
    format: str
    mtime_date: str
    content_hash: str


def _is_do_not_tracked(rel: Path, filename: str, dnt_dirs: List[str], dnt_suffixes: List[str]) -> bool:
    if any(part in dnt_dirs for part in rel.parts[:-1]):
        return True
    return any(filename.endswith(suf) for suf in dnt_suffixes)


def scan_project_root(
    root_label: str,
    include_globs: List[str],
    exclude_globs: List[str],
    do_not_track_dirs: List[str],
    do_not_track_suffixes: List[str],
) -> Dict[str, ScanEntry]:
    """Scan one project root. Precedence ([CXC-DS-FILTER-PRECEDENCE]): do-not-track
    beats everything; exclude-globs beat include-globs; unmatched files are skipped
    by default (the whitelist is authoritative)."""
    root = Path(root_label)
    if not root.is_dir():
        return {}

    included: set = set()
    for pat in include_globs:
        try:
            included.update(p for p in root.glob(pat) if p.is_file())
        except (ValueError, NotImplementedError):
            continue

    excluded: set = set()
    for pat in exclude_globs:
        try:
            excluded.update(p for p in root.glob(pat) if p.is_file())
        except (ValueError, NotImplementedError):
            continue

    entries: Dict[str, ScanEntry] = {}
    for p in included - excluded:
        rel = p.relative_to(root)
        if _is_do_not_tracked(rel, p.name, do_not_track_dirs, do_not_track_suffixes):
            continue
        relpath = rel.as_posix()
        nid = node_id_for(root_label, relpath)
        entries[nid] = ScanEntry(
            node_id=nid, path=relpath, project_root=root_label, abs_path=p,
            format=format_of(p), mtime_date=mtime_date(p), content_hash=content_hash(p),
        )
    return entries


def _new_node_fields(entry: ScanEntry, session_key: Optional[str]) -> Dict[str, str]:
    fields = {
        "path": entry.path,
        "project-root": entry.project_root,
        "format": entry.format,
        "created": entry.mtime_date,
        "modified": entry.mtime_date,
        "hash": entry.content_hash,
        "missing": "false",
    }
    for sf in SEMANTIC_FIELDS:
        fields[sf] = NEEDS_ENRICHMENT
    fields["generated-in-session"] = session_key if session_key else NEEDS_ENRICHMENT
    stamp_pending_enrichment(fields)
    return fields


# ---- diff ----------------------------------------------------------------------

@dataclass
class Delta:
    new: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)


def apply_scan(
    ledger: Ledger,
    scan_entries: Dict[str, ScanEntry],
    now: str,
    session_key: Optional[str] = None,
) -> Delta:
    """Diff *scan_entries* (the current filesystem truth) against *ledger* and mutate
    *ledger* in place with ONLY mechanical fields — see [CXC-DS-DIFF]. Safe to call on
    a throwaway in-memory Ledger for a dry-run (the caller simply doesn't persist it)."""
    # Dedup on IDENTITY, not the id STRING. Before diffing, re-key every existing
    # node to the id its own (project-root, path) derives NOW — so a change in the
    # derived id's case/format (e.g. the historical lowercase->uppercase migration,
    # or any future slug change) updates the EXISTING node in place instead of
    # minting a case-variant twin. See [CXC-DS-NODE-ID] identity-reconcile clause.
    ledger.reconcile_ids(node_id_for)

    new_ids: List[str] = []
    modified_ids: List[str] = []
    removed_ids: List[str] = []

    for nid in list(ledger.node_order):
        node = ledger.nodes[nid]
        entry = scan_entries.get(nid)
        if entry is not None:
            was_missing = node.get("missing") == "true"
            if was_missing or node.get("hash") != entry.content_hash:
                updates = {"hash": entry.content_hash, "modified": entry.mtime_date}
                if was_missing:
                    updates["missing"] = "false"
                    node.pop("missing-since", None)
                ledger.touch_mechanical(nid, **updates)
                # pending-enrichment is a mechanical index over the semantic fields'
                # sentinel-ness (literal string equality) — safe, and worth
                # refreshing here since a hand-enrichment since the last sweep may
                # have only partially filled the semantic fields.
                ledger.touch_mechanical(
                    nid, **{"pending-enrichment": render_pending_enrichment(node)}
                )
                modified_ids.append(nid)
        else:
            if node.get("missing") != "true":
                ledger.touch_mechanical(nid, missing="true", **{"missing-since": now})
            # reported every run while missing, per [CXC-DS-REMOVED-NOT-DELETED]
            removed_ids.append(nid)

    for nid in sorted(scan_entries):
        if nid not in ledger.nodes:
            ledger.add_new_node(nid, _new_node_fields(scan_entries[nid], session_key))
            new_ids.append(nid)

    return Delta(new=sorted(new_ids), modified=sorted(modified_ids), removed=sorted(removed_ids))


# ---- orchestration --------------------------------------------------------------

@dataclass
class SweepResult:
    ledger_path: Path
    delta: Delta
    needs_enrichment: List[str]
    now: str
    written: bool


def run_sweep(
    ledger_path: Path,
    roots: Optional[List[str]] = None,
    write: bool = False,
    now: Optional[str] = None,
    session_key: Optional[str] = None,
    frontmatter: Optional[Dict[str, Any]] = None,
) -> "tuple[Ledger, SweepResult]":
    """The one entry point both the CLI and --selftest use. Loads (or defaults) the
    ledger, scans every configured root, computes the delta, and — only if
    ``write=True`` — persists it (mechanical fields + one run-history line) and
    regenerates the Findings section. A dry run touches the ledger file NOT AT ALL
    ([CXC-DS-DRY-RUN])."""
    now = now or now_iso()
    ledger = Ledger.load(ledger_path)
    if frontmatter:
        ledger.seed_frontmatter(frontmatter)
    if roots:
        ledger.ensure_roots(roots)
    if not ledger.config.get("project-roots"):
        # only fall back to CWD when NOTHING configured a root anywhere — an
        # explicit --root must never silently union with an unwanted "."
        ledger.config["project-roots"] = ["."]

    scan_entries: Dict[str, ScanEntry] = {}
    for root_label in ledger.config.get("project-roots", ["."]):
        scan_entries.update(
            scan_project_root(
                root_label,
                ledger.config.get("include-globs", []),
                ledger.config.get("exclude-globs", []),
                ledger.config.get("do-not-track-dirs", []),
                ledger.config.get("do-not-track-suffixes", []),
            )
        )

    delta = apply_scan(ledger, scan_entries, now=now, session_key=session_key)

    needs = [
        nid for nid in sorted(set(delta.new) | set(delta.modified))
        if needs_enrichment(ledger.nodes[nid])
    ]

    if write:
        if not ledger.frontmatter.get("created"):
            ledger.frontmatter["created"] = now[:10]
        ledger.run_history.append({
            "type": "deliverable_sweep.run",
            "id": f"RUN-{now}",
            "date": now,
            "new": len(delta.new),
            "modified": len(delta.modified),
            "removed": len(delta.removed),
            "new_ids": delta.new,
            "modified_ids": delta.modified,
            "removed_ids": delta.removed,
        })
        ledger.save(ledger_path)

    return ledger, SweepResult(
        ledger_path=ledger_path, delta=delta, needs_enrichment=needs, now=now, written=write,
    )
