"""composeio.deliverable_sweep — a DETERMINISTIC non-code-artifact ledger sweep.

Sibling to :mod:`composeio.session_sweep`.  session_sweep reports *undone work left
on the floor* (a session-scoped process signal, parsed from a transcript `.jsonl`);
deliverable_sweep reports *non-code artifacts that exist* (a filesystem-scoped state
signal) — walking project root(s), maintaining a single ``deliverable-ledger.lts``,
and reporting the delta (NEW / MODIFIED / REMOVED) since the last run. No LLM, no
network: it computes every field a script CAN compute and stamps the rest
``NEEDS-ENRICHMENT`` for the reasoning layer — it never guesses semantics, and it
never deletes a node (a REMOVED artifact is flagged, not erased).

Spec: ``corpora/platform-specs/37-deliverable-sweep.lts`` [SPEC:CXC-DS].

    Ledger          the round-trippable deliverable-ledger.lts (load/render/save)
    run_sweep       scan + diff + (optionally) persist — the one entry point
    Delta           the new/modified/removed id lists from one sweep

Readouts::

    python3 -m composeio.deliverable_sweep sweep   # ledger defaults to bootstrap_paths.DELIVERABLE_LEDGER
    python3 -m composeio.deliverable_sweep sweep --ledger PATH --root . --write --json
    python3 -m composeio.deliverable_sweep --selftest
"""
from __future__ import annotations

from .ledger import Ledger, NEEDS_ENRICHMENT, SEMANTIC_FIELDS, needs_enrichment
from .sweep import Delta, ScanEntry, SweepResult, node_id_for, run_sweep

__all__ = [
    "Ledger",
    "NEEDS_ENRICHMENT",
    "SEMANTIC_FIELDS",
    "needs_enrichment",
    "Delta",
    "ScanEntry",
    "SweepResult",
    "node_id_for",
    "run_sweep",
]
