"""Red guard for DEFECT — the deliverable sweep must NEVER mint a case-variant /
id-format-drift DUPLICATE for one artifact identity ([SPEC:CXC-DS] [CXC-DS-NODE-ID]
identity-reconcile clause).

Root cause this pins: the sweep once keyed its dedup on the derived id STRING, so a
change in the id's case/format (the lowercase->uppercase migration, or any future
slug change) failed to match the pre-existing node and APPENDED a bare
`NEEDS-ENRICHMENT` twin — ~66 phantom duplicates that ~doubled the ledger. Identity
is (project-root, path); a derivation change must re-key the EXISTING node in place.

These tests FAIL against the pre-fix code (two nodes for one path; enrichment lost to
a placeholder) and pass once dedup is keyed on identity with an enrichment-preserving
merge. They exercise BOTH collapse sites (the sweep reconcile AND the loader) so any
future implementation that drops the invariant fails loudly.
"""
from __future__ import annotations

import re

from composeio.deliverable_sweep.ledger import Ledger
from composeio.deliverable_sweep import sweep as S

_CASE_INSENSITIVE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")


def _enriched(path: str, project_root: str, summary: str, *, missing="false",
              modified="2026-07-01", hash="sha256:seed") -> dict:
    return {
        "path": path, "project-root": project_root, "format": "md",
        "created": "2026-07-01", "modified": modified, "hash": hash, "missing": missing,
        "kind": "brief", "topics": "x", "summary": summary, "target-audience": "arch",
        "confidentiality": "internal", "review-outcome": "accepted",
        "lifecycle-position": "current", "generated-in-session": "S1",
        "pending-enrichment": "[]",
    }


def _assert_one_node_per_identity(ledger: Ledger) -> None:
    """The load-bearing invariant: one artifact identity -> exactly one node, and no
    two node ids are case-variants of each other."""
    paths = [ledger.nodes[n].get("path") for n in ledger.node_order]
    assert len(paths) == len(set(paths)), f"duplicate path(s) in ledger: {paths}"
    lowered = [n.lower() for n in ledger.node_order]
    assert len(lowered) == len(set(lowered)), \
        f"case-variant duplicate id(s): {ledger.node_order}"


def test_sweep_reconciles_id_drift_without_minting_a_twin(tmp_path):
    """A stored node whose id predates the current derivation must be RE-KEYED in
    place on the next sweep — never joined by a fresh placeholder twin."""
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "brief.md").write_text("current bytes\n", encoding="utf-8")
    root_label = str(root)

    # A ledger holding an OLD-FORMAT id (different hash8 == a derivation drift) for the
    # SAME path, carrying hand-authored enrichment.
    stale_id = "DELIV-BRIEF-0BADF00D"
    ledger = Ledger(
        nodes={stale_id: _enriched("docs/brief.md", root_label, "HAND-AUTHORED",
                                   hash="sha256:stale")},
        node_order=[stale_id],
        config={"project-roots": [root_label], "include-globs": ["docs/**/*.md"],
                "exclude-globs": [], "do-not-track-dirs": [], "do-not-track-suffixes": []},
    )

    scan = S.scan_project_root(root_label, ["docs/**/*.md"], [], [], [])
    delta = S.apply_scan(ledger, scan, now="2026-07-28T00:00:00Z")

    # exactly one node for the path — no twin minted, no tombstone left behind
    assert len(ledger.node_order) == 1, f"drift minted a twin: {ledger.node_order}"
    _assert_one_node_per_identity(ledger)
    assert not delta.new, f"a re-key must NOT be reported as NEW: {delta.new}"
    (only_id,) = ledger.node_order
    assert only_id == S.node_id_for(root_label, "docs/brief.md"), "node not re-keyed to canonical id"
    assert ledger.nodes[only_id]["summary"] == "HAND-AUTHORED", "enrichment lost on re-key"
    assert ledger.nodes[only_id]["missing"] == "false", "live artifact wrongly tombstoned"


def test_loader_collapses_case_variants_preserving_enrichment(tmp_path):
    """A half-migrated ledger carrying BOTH cases of one id must load to ONE node that
    keeps the enriched twin's fields — even when the bare placeholder is written LAST
    (the ordering that a last-wins overwrite silently corrupted)."""
    ledger_path = tmp_path / "deliverable-ledger.lts"
    # uppercase = enriched; lowercase placeholder appears AFTER it in file order.
    ledger_path.write_text(
        "---\nmachine-maintained: true\n---\n"
        "# Deliverable Ledger [LEDGER:DELIV-LEDGER]\n\n"
        "## Config\n\n```yaml\nproject-roots:\n- .\n```\n\n"
        "## Deliverables\n\n"
        "### [DELIV-AWAY-WATCH-CHECKLIST-24D3E920] docs/away-watch-checklist.md\n"
        "- path: docs/away-watch-checklist.md\n- project-root: .\n- format: md\n"
        "- created: 2026-07-01\n- modified: 2026-07-01\n- hash: sha256:aa\n- missing: false\n"
        "- kind: checklist\n- summary: REAL ENRICHED SUMMARY\n- pending-enrichment: []\n\n"
        "### [DELIV-away-watch-checklist-24d3e920] docs/away-watch-checklist.md\n"
        "- path: docs/away-watch-checklist.md\n- project-root: .\n- format: md\n"
        "- created: 2026-07-01\n- modified: 2026-07-01\n- hash: sha256:aa\n- missing: false\n"
        "- kind: NEEDS-ENRICHMENT\n- summary: NEEDS-ENRICHMENT\n- pending-enrichment: [kind, summary]\n\n"
        "## Run History\n\n```jsonl\n```\n\n## Findings — Latest Run\n\n_none_\n",
        encoding="utf-8",
    )

    ledger = Ledger.load(ledger_path)
    assert len(ledger.node_order) == 1, f"case-variant pair not collapsed: {ledger.node_order}"
    _assert_one_node_per_identity(ledger)
    (nid,) = ledger.node_order
    assert nid == "DELIV-AWAY-WATCH-CHECKLIST-24D3E920", "survivor id not canonical uppercase"
    assert ledger.nodes[nid]["summary"] == "REAL ENRICHED SUMMARY", \
        "enrichment lost: a placeholder twin overwrote the enriched node"
    assert ledger.nodes[nid]["kind"] == "checklist"


def test_hash_drift_pair_keeps_current_hash_version(tmp_path):
    """Two same-path case-variant nodes whose CONTENT hash drifted (the twin was
    re-enriched after the other) must collapse to the CURRENT-hash (newer `modified`)
    version, not the stale one — even when the STALE twin is written LAST (the ordering
    a last-wins overwrite would keep). Exercised through the loader, no internal API."""
    ledger_path = tmp_path / "deliverable-ledger.lts"

    def _block(nid: str, h: str, mod: str, summary: str) -> str:
        return (
            f"### [{nid}] decisions/projects.lts\n"
            "- path: decisions/projects.lts\n- project-root: .\n- format: lts\n"
            f"- created: 2026-07-01\n- modified: {mod}\n- hash: {h}\n- missing: false\n"
            f"- kind: log\n- summary: {summary}\n- pending-enrichment: []\n\n"
        )

    up = S.node_id_for(".", "decisions/projects.lts")
    # the real case-variant keeps the `DELIV-` prefix uppercase; only slug+hex vary
    lower_twin = "DELIV-" + up[len("DELIV-"):].lower()
    ledger_path.write_text(
        "---\nmachine-maintained: true\n---\n"
        "# Deliverable Ledger [LEDGER:DELIV-LEDGER]\n\n"
        "## Config\n\n```yaml\nproject-roots:\n- .\n```\n\n"
        "## Deliverables\n\n"
        + _block(up, "sha256:new", "2026-07-24", "CURRENT")           # newer twin first
        + _block(lower_twin, "sha256:old", "2026-07-22", "STALE")     # stale twin LAST
        + "## Run History\n\n```jsonl\n```\n\n## Findings — Latest Run\n\n_none_\n",
        encoding="utf-8",
    )

    ledger = Ledger.load(ledger_path)
    assert len(ledger.node_order) == 1, f"hash-drift pair not collapsed: {ledger.node_order}"
    _assert_one_node_per_identity(ledger)
    (nid,) = ledger.node_order
    assert ledger.nodes[nid]["hash"] == "sha256:new", "kept the stale hash over current"
    assert ledger.nodes[nid]["summary"] == "CURRENT"
