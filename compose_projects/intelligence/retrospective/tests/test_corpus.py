"""Regression assertions for the SYNTHETIC ground-truth corpus under `tests/corpus/`
([SPEC:CXC-RETRO] corpora/platform-specs/39-session-retrospective.lts).

Each transcript in `tests/corpus/*.jsonl` plants exactly one known signal, documented (with the exact
expected values, verified by construction) in `tests/corpus/GROUND_TRUTH.md`. This suite injects each
one into a SCRATCH SQLite DB (via `COMPOSEIO_RETRO_DB`-style isolation — a `tmp_path` DB, never the
real `compose_projects/intelligence/retrospective/data/metrics.db`), runs `pregenerate`, and asserts the pipeline
surfaces EXACTLY the planted ground truth. These are regression fixtures: a future change to
`inject.py`/`pregenerate.py` that silently stops catching one of these signals should fail here.

`false_binary_dsd.jsonl` is the one deliberate exception: it asserts the deterministic pass stages
NOTHING for it (the correct, honest answer — see GROUND_TRUTH.md's boundary discussion). It is training
material for the harvest lens (`.claude/skills/harvest-signal/SKILL.md`), not a deterministic-pass
fixture; its KIND/ROUTE ground truth lives only in the manifest prose, not in an executable assertion,
because no judgment pass exists in this repo to assert against.

Run:  cd <repo root> && python3 -m pytest compose_projects/intelligence/retrospective/tests/test_corpus.py -q
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from intelligence.retrospective.inject import inject_transcript
from intelligence.retrospective.pregenerate import _scan_keys, pregenerate
from intelligence.retrospective.transcript_reader import iso_to_epoch

CORPUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")


def _corpus(name: str) -> str:
    path = os.path.join(CORPUS_DIR, name)
    assert os.path.exists(path), f"synthetic corpus fixture missing: {path}"
    return path


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    """A scratch metrics DB, isolated via the same env var the pipeline reads
    (`COMPOSEIO_RETRO_DB`) so this suite never touches the real data/metrics.db."""
    p = str(tmp_path / "corpus_metrics.db")
    monkeypatch.setenv("COMPOSEIO_RETRO_DB", p)
    return p


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 1. repetitive_commands.jsonl — capture-type 2
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_repetitive_commands_corpus(db_file):
    staged = inject_transcript(_corpus("repetitive_commands.jsonl"), db_path=db_file)
    assert staged["session_id"] == "corpus-repetitive-0001"
    assert staged["record_count"] == 10
    assert staged["repetitive_commands"] == {("Bash", "npm test"): 4}

    out = pregenerate(staged["session_id"], db_path=db_file)
    flagged = out["capture_types"]["2_repetitive_task_frequency"]["flagged_evidence"]
    assert flagged == [{"tool": "Bash", "signature": "npm test", "count": 4, "leverage_product": 4}]
    assert out["tooling_backlog_ranked"] == flagged
    # nothing else should have fired
    assert out["capture_types"]["3_correction_markers_raw"]["evidence"] == []
    assert out["capture_types"]["5_read_frequency"]["evidence"] == []
    assert out["capture_types"]["6_magma_queries"]["all_queries"] == []
    assert out["capture_types"]["7_token_spikes"]["evidence"] == []
    assert out["capture_types"]["8_hedging_markers"]["evidence"] == []


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 2. token_spike.jsonl — capture-type 7
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_token_spike_corpus(db_file):
    staged = inject_transcript(_corpus("token_spike.jsonl"), db_path=db_file)
    assert staged["session_id"] == "corpus-tokenspike-0001"
    assert len(staged["token_spikes"]) == 1
    spike = staged["token_spikes"][0]
    assert spike["tokens"] == 31000
    assert spike["prev_tokens"] == 380
    assert spike["reason"] == "absolute"

    out = pregenerate(staged["session_id"], db_path=db_file)
    evidence = out["capture_types"]["7_token_spikes"]["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["tokens"] == 31000
    assert evidence[0]["leverage_product"] == 31000
    assert out["tooling_backlog_ranked"][0]["tokens"] == 31000


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 3. correction.jsonl — capture-type 3 (raw slice)
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_correction_corpus(db_file):
    staged = inject_transcript(_corpus("correction.jsonl"), db_path=db_file)
    assert staged["session_id"] == "corpus-correction-0001"
    assert len(staged["correction_markers"]) == 1
    marker = staged["correction_markers"][0]
    assert marker["speaker"] == "user"
    assert marker["marker"] == "actually-correction"

    out = pregenerate(staged["session_id"], db_path=db_file)
    ct = out["capture_types"]["3_correction_markers_raw"]
    assert len(ct["evidence"]) == 1
    assert ct["evidence"][0]["marker"] == "actually-correction"
    assert "axis-clustering" in ct["tier"]  # the tiering-nuance disclaimer stays attached


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 4. hedging.jsonl — capture-type 8
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_hedging_corpus(db_file):
    staged = inject_transcript(_corpus("hedging.jsonl"), db_path=db_file)
    assert staged["session_id"] == "corpus-hedging-0001"
    assert len(staged["hedging_markers"]) == 1
    assert staged["hedging_markers"][0]["marker"] == "not-sure"

    out = pregenerate(staged["session_id"], db_path=db_file)
    ev = out["capture_types"]["8_hedging_markers"]["evidence"]
    assert len(ev) == 1
    assert ev[0]["marker"] == "not-sure"


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 5. magma_null.jsonl — capture-type 6, null-result corpus-gap
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_magma_null_corpus(db_file):
    staged = inject_transcript(_corpus("magma_null.jsonl"), db_path=db_file)
    assert staged["session_id"] == "corpus-magmanull-0001"
    key = ("mcp__haven__haven_magma_find", "rate-limit backoff strategy")
    assert staged["magma_queries"][key] == [1, 1]  # [call_count, null_count]

    out = pregenerate(staged["session_id"], db_path=db_file)
    gaps = out["capture_types"]["6_magma_queries"]["corpus_gap_candidates"]
    assert len(gaps) == 1
    assert gaps[0]["query"] == "rate-limit backoff strategy"
    assert gaps[0]["null_count"] == 1
    assert gaps[0]["leverage_product"] == 1
    # documented incidental fallout (see GROUND_TRUTH.md #5): the magma tool_use also leaves a
    # count=1 repetitive_commands row (below threshold, never flagged) — assert it stays unflagged.
    assert out["capture_types"]["2_repetitive_task_frequency"]["flagged_evidence"] == []


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 6. handover.jsonl — sessions.handover_cost_pct / handover_events
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_handover_corpus(db_file):
    context_samples = {
        int(iso_to_epoch("2026-07-22T13:00:00Z")): 40.0,
        int(iso_to_epoch("2026-07-22T13:06:00Z")): 47.5,
    }
    staged = inject_transcript(_corpus("handover.jsonl"), db_path=db_file,
                                context_samples=context_samples)
    assert staged["session_id"] == "corpus-handover-0001"
    assert len(staged["handover_events"]) == 1
    ev = staged["handover_events"][0]
    assert ev["start_ts"] == iso_to_epoch("2026-07-22T13:00:00Z")
    assert ev["end_ts"] == iso_to_epoch("2026-07-22T13:06:00Z")
    assert ev["cost_pct"] == pytest.approx(7.5)
    assert ev["context_source"] == "sample-provided"

    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT handover_cost_pct, context_trajectory_source FROM sessions "
                        "WHERE session_id=?", (staged["session_id"],)).fetchone()
    conn.close()
    assert row == (pytest.approx(7.5), "sample-provided")


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 7. false_binary_dsd.jsonl — capture-type 4, dusk-gift fodder — the DETERMINISTIC/JUDGMENT boundary
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_false_binary_corpus_stages_nothing_deterministically(db_file):
    """The headline finding this fixture exists to pin down: a textbook false-binary + reframe does
    NOT trip any of inject.py's regex patterns. The deterministic pass staging an EMPTY result here is
    the CORRECT behavior (see GROUND_TRUTH.md #7) — this is a regression guard against either (a) a
    future overly-broad regex accidentally "catching" it for the wrong reason, or (b) losing track of
    the fact that this capture-type genuinely needs the judgment lens."""
    staged = inject_transcript(_corpus("false_binary_dsd.jsonl"), db_path=db_file)
    assert staged["session_id"] == "corpus-falsebinary-0001"
    assert staged["correction_markers"] == []
    assert staged["hedging_markers"] == []
    assert staged["repetitive_commands"] == {}
    assert staged["magma_queries"] == {}
    assert staged["token_spikes"] == []
    assert staged["handover_events"] == []

    out = pregenerate(staged["session_id"], db_path=db_file)
    ct = out["capture_types"]
    assert ct["2_repetitive_task_frequency"]["flagged_evidence"] == []
    assert ct["3_correction_markers_raw"]["evidence"] == []
    assert ct["5_read_frequency"]["evidence"] == []
    assert ct["6_magma_queries"]["all_queries"] == []
    assert ct["7_token_spikes"]["evidence"] == []
    assert ct["8_hedging_markers"]["evidence"] == []
    assert out["tooling_backlog_ranked"] == []
    # capture-type 4 is explicitly named as un-staged (the judgment pass's job, not this one's)
    assert any("4_dusk_gift_fodder" in item for item in out["judgment_capture_types_not_staged_here"])


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 8. read_frequency.jsonl — capture-type 5
# ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_read_frequency_corpus(db_file):
    staged = inject_transcript(_corpus("read_frequency.jsonl"), db_path=db_file)
    assert staged["session_id"] == "corpus-readfreq-0001"
    assert staged["file_reads"] == {("/repo/docs/architecture.md", 1): 3}

    out = pregenerate(staged["session_id"], db_path=db_file)
    reads = out["capture_types"]["5_read_frequency"]["evidence"]
    assert reads == [{"path": "/repo/docs/architecture.md", "deliberate": 1, "count": 3}]
    # documented incidental fallout (see GROUND_TRUTH.md #8): Read's signature IS the path, so this
    # transcript ALSO clears the repetitive-task threshold — genuinely the same underlying event.
    flagged = out["capture_types"]["2_repetitive_task_frequency"]["flagged_evidence"]
    assert flagged == [{"tool": "Read", "signature": "/repo/docs/architecture.md", "count": 3,
                         "leverage_product": 3}]


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Cross-cutting: every corpus transcript's staged output must stay judgment-vocabulary-clean, and the
# session_ids must be unique across the corpus (the manifest's own internal consistency).
# ─────────────────────────────────────────────────────────────────────────────────────────────────
CORPUS_FILES = [
    "repetitive_commands.jsonl", "token_spike.jsonl", "correction.jsonl", "hedging.jsonl",
    "magma_null.jsonl", "handover.jsonl", "false_binary_dsd.jsonl", "read_frequency.jsonl",
]


def test_corpus_manifest_present_and_complete():
    manifest = os.path.join(CORPUS_DIR, "GROUND_TRUTH.md")
    assert os.path.exists(manifest), "GROUND_TRUTH.md answer-key manifest missing"
    text = open(manifest).read()
    for f in CORPUS_FILES:
        assert f in text, f"{f} not documented in GROUND_TRUTH.md"


@pytest.mark.parametrize("filename", CORPUS_FILES)
def test_every_corpus_transcript_stages_clean_of_judgment_vocabulary(filename, db_file):
    staged = inject_transcript(_corpus(filename), db_path=db_file)
    out = pregenerate(staged["session_id"], db_path=db_file)
    hits = _scan_keys(out)
    assert not hits, f"{filename}: judgment-vocabulary keys leaked into staged output: {hits}"
    json.dumps(out)  # must stay a valid JSON-serializable staging artifact


def test_corpus_session_ids_are_unique():
    seen = {}
    for filename in CORPUS_FILES:
        path = _corpus(filename)
        with open(path) as fh:
            first = json.loads(fh.readline())
        sid = first["sessionId"]
        assert sid not in seen, f"duplicate session_id {sid!r} in {filename} and {seen.get(sid)}"
        seen[sid] = filename
