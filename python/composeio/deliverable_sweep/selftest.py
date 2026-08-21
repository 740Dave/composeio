"""composeio.deliverable_sweep --selftest — a synthetic temp tree, run against the
REAL scan/diff/ledger code path (no mocking). Matches compact_summary.py's selftest
rigor: numbered asserts, an explicit confound guard, and a determinism check.

    python3 -m composeio.deliverable_sweep --selftest
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .ledger import ENRICHABLE_FIELDS, NEEDS_ENRICHMENT, SEMANTIC_FIELDS, Ledger
from .sweep import node_id_for, run_sweep

DAY1 = "2026-01-01"
DAY2 = "2026-06-15"
NOW1 = "2026-01-02T00:00:00Z"
NOW2 = "2026-06-16T00:00:00Z"
NOW3 = "2026-07-01T00:00:00Z"


def _ts(iso_date: str) -> float:
    return datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def _write(path: Path, content: str, mtime_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    t = _ts(mtime_date)
    os.utime(path, (t, t))


def run() -> int:
    checks = 0
    tmp = Path(tempfile.mkdtemp(prefix="deliverable_sweep_selftest_"))
    try:
        root = tmp / "project"
        ledger_path = tmp / "deliverables" / "deliverable-ledger.lts"

        # -- seed a synthetic tree ------------------------------------------------
        keep_md = root / "docs" / "keep.md"
        nested_html = root / "docs" / "sub" / "nested.html"
        decision_lts = root / "decisions" / "sample.lts"
        # confounds: each would be swept in by a NAIVE filter
        code_in_docs = root / "docs" / "code_confound.py"          # code under docs/
        nm_readme = root / "docs" / "node_modules" / "pkg" / "readme.md"  # do-not-track dir
        pycache_md = root / "docs" / "__pycache__" / "cache.md"    # do-not-track dir
        secrets_yaml = root / "docs" / "secrets.yaml"               # do-not-track suffix
        outside_md = root / "reviews" / "not_included.md"           # outside include-globs

        for p in (keep_md, nested_html, decision_lts, code_in_docs, nm_readme,
                  pycache_md, secrets_yaml, outside_md):
            _write(p, f"content of {p.name}\n", DAY1)

        root_label = str(root)

        # -- (1) dry run: NEW detected, confounds excluded, nothing persisted -----
        ledger, result = run_sweep(ledger_path, roots=[root_label], write=False, now=NOW1)
        got_new = set(result.delta.new)
        expect_ids = {
            node_id_for(root_label, "docs/keep.md"),
            node_id_for(root_label, "docs/sub/nested.html"),
            node_id_for(root_label, "decisions/sample.lts"),
        }
        assert got_new == expect_ids, f"(1) NEW set mismatch: {got_new} != {expect_ids}"
        assert not ledger_path.exists(), "(2) dry-run must not touch the ledger file"
        checks += 2

        # -- (3) confound exclusion, explicit per-file ----------------------------
        confound_ids = {
            node_id_for(root_label, "docs/code_confound.py"),
            node_id_for(root_label, "docs/node_modules/pkg/readme.md"),
            node_id_for(root_label, "docs/__pycache__/cache.md"),
            node_id_for(root_label, "docs/secrets.yaml"),
            node_id_for(root_label, "reviews/not_included.md"),
        }
        assert not (confound_ids & got_new), "(3) a confound leaked into NEW"
        checks += 1

        # -- (4) --write persists mechanical fields + NEEDS-ENRICHMENT stamps -----
        ledger, result = run_sweep(ledger_path, roots=[root_label], write=True, now=NOW1)
        assert ledger_path.exists(), "(4) --write must create the ledger file"
        assert len(result.delta.new) == 3 and not result.delta.modified and not result.delta.removed, \
            f"(5) unexpected delta on first write: {result.delta}"
        checks += 2

        keep_id = node_id_for(root_label, "docs/keep.md")
        node = ledger.nodes[keep_id]
        assert node["path"] == "docs/keep.md", "(6) path"
        assert node["project-root"] == root_label, "(7) project-root"
        assert node["format"] == "md", "(8) format"
        assert node["created"] == DAY1 and node["modified"] == DAY1, "(9) created/modified from mtime"
        assert node["hash"].startswith("sha256:"), "(10) hash present"
        assert node["missing"] == "false", "(11) missing default"
        for sf in SEMANTIC_FIELDS:
            assert node[sf] == NEEDS_ENRICHMENT, f"(12) semantic field {sf} not stamped"
        assert node["generated-in-session"] == NEEDS_ENRICHMENT, "(13) generated-in-session default"
        assert keep_id in result.needs_enrichment, "(14) needs-enrichment worklist"
        checks += 9

        # -- pending-enrichment: mechanically-computed per-node worklist ----------
        expect_pending = "[" + ", ".join(ENRICHABLE_FIELDS) + "]"
        assert node["pending-enrichment"] == expect_pending, \
            f"(14b) pending-enrichment on a fresh NEW node should list all 8: {node['pending-enrichment']}"
        checks += 1

        # -- machine-maintained frontmatter marker (base-spec convention) ---------
        assert ledger.frontmatter.get("machine-maintained") is True, \
            "(14c) machine-maintained: true must be set on every ledger"
        rendered = ledger.render()
        assert "machine-maintained: true" in rendered, \
            "(14d) machine-maintained must render lowercase (YAML bool), not Python True"
        checks += 2

        # confounds never made it into the ledger at all
        assert not (confound_ids & set(ledger.nodes)), "(15) confound persisted into ledger"
        checks += 1

        # -- (16) round-trip: reload from disk, structurally identical ------------
        reloaded = Ledger.load(ledger_path)
        assert set(reloaded.nodes) == set(ledger.nodes), "(16) round-trip node set"
        assert reloaded.nodes[keep_id] == ledger.nodes[keep_id], "(17) round-trip fields"
        assert reloaded.config["project-roots"] == [root_label], "(18) round-trip config"
        checks += 3

        # -- (19) determinism: two dry runs with the same now -> identical report -
        _, r_a = run_sweep(ledger_path, write=False, now=NOW1)
        _, r_b = run_sweep(ledger_path, write=False, now=NOW1)
        assert json.dumps(vars(r_a.delta), sort_keys=True) == json.dumps(vars(r_b.delta), sort_keys=True), \
            "(19) determinism: repeated dry-run delta differs"
        assert r_a.delta.new == [] and r_a.delta.modified == [] and r_a.delta.removed == [], \
            "(20) unchanged tree must report zero delta on the second look"
        checks += 2

        # -- (21) MODIFIED bumps `modified`, not `created`, semantics untouched ---
        keep_md.write_text("changed content\n", encoding="utf-8")
        os.utime(keep_md, (_ts(DAY2), _ts(DAY2)))
        ledger, result = run_sweep(ledger_path, write=True, now=NOW2)
        assert result.delta.modified == [keep_id] and not result.delta.new and not result.delta.removed, \
            f"(21) expected only keep.md modified: {result.delta}"
        node = ledger.nodes[keep_id]
        assert node["created"] == DAY1, "(22) created must not move"
        assert node["modified"] == DAY2, "(23) modified must bump to the new mtime date"
        assert node["hash"] != reloaded.nodes[keep_id]["hash"], "(24) hash must change"
        for sf in SEMANTIC_FIELDS:
            assert node[sf] == NEEDS_ENRICHMENT, f"(25) MODIFIED must not touch semantic field {sf}"
        checks += 5

        # -- (26) hand-enrich, then re-sweep unchanged: enrichment survives -------
        ledger.nodes[keep_id]["kind"] = "report"
        ledger.nodes[keep_id]["summary"] = "a hand-written summary"
        ledger.save(ledger_path)
        ledger, result = run_sweep(ledger_path, write=True, now=NOW2)
        assert result.delta.modified == [] and result.delta.new == [], \
            "(27) re-sweeping unchanged content must not report a change"
        assert ledger.nodes[keep_id]["kind"] == "report", "(28) hand-enrichment must survive a reconcile"
        assert ledger.nodes[keep_id]["summary"] == "a hand-written summary", "(29) summary survives"
        checks += 4

        # -- (30b) a later real content change recomputes pending-enrichment ------
        keep_md.write_text("changed again\n", encoding="utf-8")
        os.utime(keep_md, (_ts("2026-06-20"), _ts("2026-06-20")))
        ledger, result = run_sweep(ledger_path, write=True, now="2026-06-21T00:00:00Z")
        assert result.delta.modified == [keep_id], f"(30b) content change must be MODIFIED: {result.delta}"
        pending = ledger.nodes[keep_id]["pending-enrichment"]
        assert "kind" not in pending and "summary" not in pending, \
            f"(30c) pending-enrichment must drop hand-enriched fields: {pending}"
        assert "topics" in pending and "lifecycle-position" in pending, \
            f"(30d) pending-enrichment must still list not-yet-enriched fields: {pending}"
        checks += 3

        # -- (30) REMOVED flagged, not deleted; lifecycle-position untouched ------
        nested_id = node_id_for(root_label, "docs/sub/nested.html")
        nested_html.unlink()
        ledger, result = run_sweep(ledger_path, write=True, now=NOW3)
        assert nested_id in result.delta.removed, "(30) removed file must be reported"
        node = ledger.nodes[nested_id]
        assert node["missing"] == "true", "(31) missing flag set"
        assert node.get("missing-since") == NOW3, "(32) missing-since stamped from injected now"
        assert node["lifecycle-position"] == NEEDS_ENRICHMENT, \
            "(33) lifecycle-position must stay untouched by removal (app never writes it)"
        checks += 4

        # still-missing is reported again on a subsequent no-op sweep
        ledger, result = run_sweep(ledger_path, write=True, now="2026-07-02T00:00:00Z")
        assert nested_id in result.delta.removed, "(34) still-missing must keep surfacing"
        checks += 1

        # -- (35) restoration: same path reappears -> MODIFIED, same id, not NEW --
        _write(nested_html, "restored\n", "2026-07-03")
        ledger, result = run_sweep(ledger_path, write=True, now="2026-07-04T00:00:00Z")
        assert nested_id in result.delta.modified and nested_id not in result.delta.new, \
            f"(35) restoration must be MODIFIED not NEW: {result.delta}"
        assert ledger.nodes[nested_id]["missing"] == "false", "(36) missing cleared on restore"
        assert "missing-since" not in ledger.nodes[nested_id], "(37) missing-since cleared on restore"
        checks += 3

        # -- (38) multi-writer safety: add_new_node idempotent, touch guards ------
        before = dict(ledger.nodes[keep_id])
        ledger.add_new_node(keep_id, {"path": "should-not-overwrite"})
        assert ledger.nodes[keep_id] == before, "(38) add_new_node must be a no-op on an existing id"
        checks += 1

        raised = False
        try:
            ledger.touch_mechanical(keep_id, kind="should-not-be-allowed")
        except ValueError:
            raised = True
        assert raised, "(39) touch_mechanical must refuse a semantic field"
        checks += 1

        # -- (40) atomic save: no stray temp file left behind ----------------------
        ledger.save(ledger_path)
        leftovers = [p for p in ledger_path.parent.iterdir() if p.name.startswith(".") and p.suffix == ".tmp"]
        assert not leftovers, f"(40) stray temp file(s) left behind: {leftovers}"
        checks += 1

        # -- (41) --session stamps generated-in-session on a NEW node -------------
        another = root / "docs" / "another.md"
        _write(another, "another\n", DAY1)
        ledger, result = run_sweep(ledger_path, write=True, now="2026-07-05T00:00:00Z", session_key="c14")
        another_id = node_id_for(root_label, "docs/another.md")
        assert ledger.nodes[another_id]["generated-in-session"] == "c14", \
            "(41) --session must stamp generated-in-session on a NEW node"
        checks += 1

        # -- (42) id-derivation DRIFT re-keys in place; NEVER mints a case-variant twin
        # The regression that ~doubled a real ledger: dedup keyed on the derived id
        # STRING, so a change in the id's case/format (the lowercase->uppercase
        # migration, or any future slug change) failed to match the stored node and
        # APPENDED a bare NEEDS-ENRICHMENT twin beside the enriched original. Identity
        # is (project-root, path); a derivation change must re-key IN PLACE.
        keep_enriched = dict(ledger.nodes[keep_id])
        assert keep_enriched.get("summary") == "a hand-written summary", "(42-pre) node enriched"
        stale_twin = "DELIV-KEEP-0BADF00D"          # an OLD-format id for docs/keep.md
        del ledger.nodes[keep_id]
        ledger.node_order = [n for n in ledger.node_order if n != keep_id]
        ledger.nodes[stale_twin] = keep_enriched
        ledger.node_order.insert(0, stale_twin)
        ledger.save(ledger_path)
        ledger, result = run_sweep(ledger_path, write=True, now="2026-07-06T00:00:00Z")
        paths = [ledger.nodes[n]["path"] for n in ledger.node_order]
        assert paths.count("docs/keep.md") == 1, "(42) drift minted a duplicate node for one path"
        assert keep_id in ledger.nodes and stale_twin not in ledger.nodes, \
            "(43) stale-format id must re-key to the canonical id, never persist as a twin"
        assert ledger.nodes[keep_id]["summary"] == "a hand-written summary", \
            "(44) enrichment must survive the id re-key (never dropped for a placeholder)"
        assert keep_id not in result.delta.new, "(45) a re-key must NOT be reported as NEW"
        checks += 4

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"PASS: deliverable_sweep selftest ({checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
