"""Acceptance tests for the session-retrospective DETERMINISTIC perceive-half
([SPEC:CXC-RETRO] corpora/platform-specs/39-session-retrospective.lts), against the SYNTHETIC fixture
transcript in this directory (`fixture_transcript.jsonl`) — never a real transcript.

Proves: (a) injecting the fixture populates `sessions` + every signal table with the EXACT expected
deterministic values; (b) re-injecting is idempotent (no duplicate rows, same values); (c) the
pre-generator emits the staged structure with the right evidence and NO judgment/score keys.

Run:  cd <repo root> && python3 -m pytest compose_projects/intelligence/retrospective/tests/test_retrospective.py -q
      (or, dependency-free) python3 -m intelligence.retrospective.inject --selftest
                             python3 -m intelligence.retrospective.pregenerate --selftest
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from intelligence.retrospective import db as db_mod
from intelligence.retrospective.inject import inject_transcript
from intelligence.retrospective.pregenerate import _scan_keys, pregenerate
from intelligence.retrospective.transcript_reader import iso_to_epoch

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixture_transcript.jsonl")

# The synthetic context-% trajectory for handover pair 1 ONLY (pair 2 is deliberately left thin —
# §5b: "the trajectory is thin by design", a real gap this test asserts is handled honestly, not
# papered over).
CONTEXT_SAMPLES = {
    int(iso_to_epoch("2026-07-21T10:00:00Z")): 40.0,
    int(iso_to_epoch("2026-07-21T10:05:00Z")): 41.2,
}


@pytest.fixture()
def db_file(tmp_path):
    return str(tmp_path / "metrics.db")


def test_fixture_exists():
    assert os.path.exists(FIXTURE), f"synthetic fixture missing: {FIXTURE}"


def test_inject_populates_sessions_and_handover(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file, context_samples=CONTEXT_SAMPLES)
    conn = sqlite3.connect(db_file)
    row = conn.execute("SELECT record_count, handover_start_ts, handover_end_ts, "
                        "handover_cost_pct, context_trajectory_source FROM sessions "
                        "WHERE session_id=?", (staged["session_id"],)).fetchone()
    assert row is not None
    record_count, h_start, h_end, cost_pct, src = row
    assert record_count == 29
    # sessions.* mirrors the LAST (most recent) pair -> pair 2, which is intentionally thin.
    assert h_start == iso_to_epoch("2026-07-21T14:00:00Z")
    assert h_end == iso_to_epoch("2026-07-21T14:04:30Z")
    assert cost_pct is None
    assert src == "unavailable"
    conn.close()


def test_inject_handover_events_both_pairs(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file, context_samples=CONTEXT_SAMPLES)
    conn = sqlite3.connect(db_file)
    rows = conn.execute("SELECT seq, start_ts, end_ts, cost_pct, context_source FROM "
                         "handover_events WHERE session_id=? ORDER BY seq",
                         (staged["session_id"],)).fetchall()
    conn.close()
    assert len(rows) == 2
    seq1, start1, end1, cost1, src1 = rows[0]
    assert start1 == iso_to_epoch("2026-07-21T10:00:00Z")
    assert end1 == iso_to_epoch("2026-07-21T10:05:00Z")
    assert cost1 == pytest.approx(1.2)
    assert src1 == "sample-provided"
    seq2, start2, end2, cost2, src2 = rows[1]
    assert cost2 is None
    assert src2 == "unavailable"


def test_repetitive_commands_exact_tally(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file)
    conn = sqlite3.connect(db_file)
    count = conn.execute(
        "SELECT count FROM repetitive_commands WHERE session_id=? AND tool='Bash' "
        "AND signature='ls -la'", (staged["session_id"],)).fetchone()[0]
    conn.close()
    assert count == 3


def test_read_frequency_deliberate_vs_incidental(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file)
    conn = sqlite3.connect(db_file)
    deliberate = conn.execute(
        "SELECT count FROM file_reads WHERE session_id=? AND path=? AND deliberate=1",
        (staged["session_id"], "/repo/src/fileA.py")).fetchone()[0]
    incidental_b = conn.execute(
        "SELECT count FROM file_reads WHERE session_id=? AND path=? AND deliberate=0",
        (staged["session_id"], "/repo/src/fileB.py")).fetchone()[0]
    conn.close()
    assert deliberate == 2       # Read tool_use fired twice on fileA.py
    assert incidental_b == 1     # fileB.py surfaced once via Grep output


def test_magma_null_result_flagged_as_corpus_gap(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file)
    conn = sqlite3.connect(db_file)
    call_count, null_count = conn.execute(
        "SELECT call_count, null_count FROM magma_queries WHERE session_id=? AND "
        "query='rate limiting policy'", (staged["session_id"],)).fetchone()
    non_null = conn.execute(
        "SELECT null_count FROM magma_queries WHERE session_id=? AND query='decision lifecycle'",
        (staged["session_id"],)).fetchone()[0]
    conn.close()
    assert call_count == 1 and null_count == 1
    assert non_null == 0


def test_token_spike_located(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file)
    conn = sqlite3.connect(db_file)
    rows = conn.execute("SELECT tokens, reason FROM token_spikes WHERE session_id=?",
                         (staged["session_id"],)).fetchall()
    conn.close()
    # 22000 = input_tokens(18000) + output_tokens(4000) — cache_read_input_tokens(3000) is
    # deliberately EXCLUDED (FIX-TOKENSPIKE-SATURATION; see transcript_reader.usage_tokens's
    # docstring): it is the cumulative resent-context total, not this turn's own cost.
    assert rows == [(22000, "absolute")]


def test_correction_and_hedging_markers_captured(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file)
    conn = sqlite3.connect(db_file)
    n_corr = conn.execute("SELECT count(*) FROM correction_markers WHERE session_id=?",
                           (staged["session_id"],)).fetchone()[0]
    n_hedge = conn.execute("SELECT count(*) FROM hedging_markers WHERE session_id=?",
                            (staged["session_id"],)).fetchone()[0]
    conn.close()
    assert n_corr >= 1
    assert n_hedge >= 1


def test_reinjection_is_idempotent(db_file):
    inject_transcript(FIXTURE, db_path=db_file, context_samples=CONTEXT_SAMPLES)
    inject_transcript(FIXTURE, db_path=db_file, context_samples=CONTEXT_SAMPLES)
    inject_transcript(FIXTURE, db_path=db_file, context_samples=CONTEXT_SAMPLES)  # thrice for good measure
    conn = sqlite3.connect(db_file)
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in (
        "sessions", "handover_events", "file_reads", "repetitive_commands", "magma_queries",
        "token_spikes", "correction_markers", "hedging_markers")}
    conn.close()
    assert counts["sessions"] == 1
    assert counts["handover_events"] == 2
    # 9, not 8: post-FIX-MCP-TOOL-SIGNATURE, the two mcp__haven__haven_magma_find calls now get
    # distinct signatures (their `query` arg) instead of both collapsing onto the bare tool name,
    # so they split from 1 repetitive_commands row into 2 — see test_repetitive_commands.py-style
    # note in GROUND_TRUTH.md #5 for the corpus-fixture equivalent of this.
    assert counts["repetitive_commands"] == 9
    assert counts["magma_queries"] == 2
    assert counts["token_spikes"] == 1
    assert counts["correction_markers"] == 3
    assert counts["hedging_markers"] == 2


def test_pregenerate_stages_evidence_with_no_judgment_keys(db_file):
    staged = inject_transcript(FIXTURE, db_path=db_file, context_samples=CONTEXT_SAMPLES)
    out = pregenerate(staged["session_id"], db_path=db_file)

    hits = _scan_keys(out)
    assert not hits, f"judgment-vocabulary keys leaked into staged output: {hits}"

    ct = out["capture_types"]
    assert any(r["signature"] == "ls -la" and r["count"] == 3
               for r in ct["2_repetitive_task_frequency"]["flagged_evidence"])
    assert any(g["query"] == "rate limiting policy" and g["null_count"] == 1
               for g in ct["6_magma_queries"]["corpus_gap_candidates"])
    assert len(ct["7_token_spikes"]["evidence"]) == 1
    assert len(ct["3_correction_markers_raw"]["evidence"]) >= 1
    assert len(ct["8_hedging_markers"]["evidence"]) >= 1
    assert len(out["judgment_capture_types_not_staged_here"]) == 3

    import json
    json.dumps(out)  # must be a valid JSON-serializable staging artifact
