#!/usr/bin/env python3
"""pregenerate.py — the retrospective-input PRE-GENERATOR: assembles the STAGED structured evidence a
future JUDGMENT pass will consume ([SPEC:CXC-RETRO] §0/§2 — the capture-types' raw signal).

╔══════════════════════════════════════════════════════════════════════════════════════════════════╗
║ THE HARD BOUNDARY (read this before changing anything in this file):                               ║
║ This module STAGES. It does NOT JUDGE. It never emits a score, a verdict, a grade, a ranking of    ║
║ the human or the instance, or a sentence shaped like "you did X wrong." Every field below is either║
║ a raw count, a verbatim quote, or a MECHANICAL arithmetic product (frequency × cost — the spec's   ║
║ own explicit formula, §0: "opportunities ... ranked by leverage — never a tally of failures"). The ║
║ output is deliberately closer to a lab notebook than a report card. A later human-blessed JUDGMENT ║
║ pass (NOT built here, NOT built by this task) reads this structure and reasons over it — clusters  ║
║ corrections by axis, drafts the "why" of a decision, decides what a corpus-gap should contain. If  ║
║ you find yourself adding a field that describes the HUMAN or the INSTANCE rather than an EVENT in  ║
║ the transcript, stop — that belongs in the judgment pass, not here.                                ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════╝

## Which of the 8 capture-types this module stages (deterministic tier) vs. leaves for judgment

Covered here (raw evidence only, from the SQLite tables `inject.py` populated):
  2. repetitive-task frequency  — `repetitive_commands` tally, thresholded (a mechanical cutoff, not
                                   a verdict) into the tooling-backlog evidence list.
  3. corrections — RAW SLICE ONLY: the pattern-match + verbatim quote (`correction_markers`). The
                    spec calls capture-type 3 a JUDGMENT type (axis-clustering + feedback-memory
                    routing) — that clustering/interpretation is explicitly NOT done here; only the
                    deterministic "a correction-shaped utterance occurred, here it is verbatim" is
                    staged. Flagged clearly in the output under `tier`.
  5. read-frequency — `file_reads` tally (deliberate vs. incidental), unranked beyond raw counts.
  6. Magma queries + NULL-result corpus-gaps — `magma_queries`; a `corpus_gap_candidates` slice where
     `null_count > 0` (the raw negative-space FLAG the spec calls for — never a claim about what the
     corpus SHOULD contain, only that a query came back empty).
  7. token-spikes — `token_spikes`, located events with the threshold that fired.
  8. hedging-markers — `hedging_markers`, pattern-match + verbatim quote.

Left for the judgment pass (genuinely NOT deterministic; NOT built here — do not add them):
  1. Decisions — the rulings/commitments + their WHY. Reference implementation: the
     decision-archivist agent (dusk-aide mode), per [DEC-AUTO-CROSSING-ARCHIVIST]. Unchanged by this
     task.
  3. Corrections' AXIS-CLUSTERING + feedback-memory/role-def-gap routing (the interpretive half of
     capture-type 3 — "what blind spot does this reveal" is a judgment call).
  4. Dusk-gift fodder — recognizing "lesson-shape" (a falsified instinct) requires reading meaning
     into an exchange, not just pattern-matching text.

The `leverage_product` field on the tooling-backlog entries (types 2 / 6-null / 7) is the spec's own
explicit mechanical ranking formula (§0: "frequent × expensive = toolify first") — arithmetic over
raw counts, not an assessment of the human or instance, so it stays in this deterministic stage.

Usage:
  python3 -m intelligence.retrospective.pregenerate --session-id SID [--db PATH] [--out FILE.json]
  python3 -m intelligence.retrospective.pregenerate --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from intelligence.retrospective import db as db_mod

# A cheap per-instance cost proxy for the leverage-product arithmetic (§0's "frequent × expensive"):
# a repetitive command's "cost" is approximated as 1 (no per-call token data at that granularity); a
# token-spike's cost is its own token count; a magma null-result's cost is 1 (a wasted round-trip).
# This is intentionally crude — it is arithmetic on counts, not a claim about actual $ cost.
REPETITIVE_THRESHOLD_DEFAULT = 2   # a mechanical cutoff (§7 config knob), not a verdict


def _rows(conn, sql, params=()) -> list[dict]:
    conn.row_factory = None
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def pregenerate(session_id: str, db_path: str | None = None,
                 repetitive_threshold: int = REPETITIVE_THRESHOLD_DEFAULT) -> dict:
    """Assemble the staged structure for one session. Pure read over the metrics DB — writes
    nothing, judges nothing."""
    conn = db_mod.connect(db_path)
    try:
        sess_rows = _rows(conn, "SELECT * FROM sessions WHERE session_id=?", (session_id,))
        session = sess_rows[0] if sess_rows else None

        handover_events = _rows(
            conn, "SELECT seq, start_ts, end_ts, cost_pct, context_pct_start, context_pct_end, "
                  "context_source FROM handover_events WHERE session_id=? ORDER BY seq",
            (session_id,))

        file_reads = _rows(
            conn, "SELECT path, deliberate, count FROM file_reads WHERE session_id=? "
                  "ORDER BY count DESC", (session_id,))

        repetitive_all = _rows(
            conn, "SELECT tool, signature, count FROM repetitive_commands WHERE session_id=? "
                  "ORDER BY count DESC", (session_id,))
        repetitive_flagged = [r for r in repetitive_all if r["count"] >= repetitive_threshold]
        for r in repetitive_flagged:
            r["leverage_product"] = r["count"] * 1  # arithmetic only — see module docstring

        magma_all = _rows(
            conn, "SELECT tool, query, call_count, null_count FROM magma_queries WHERE session_id=? "
                  "ORDER BY call_count DESC", (session_id,))
        corpus_gap_candidates = [dict(r, leverage_product=r["null_count"] * 1)
                                  for r in magma_all if r["null_count"] > 0]

        token_spikes = _rows(
            conn, "SELECT turn, ts, tokens, prev_tokens, reason FROM token_spikes "
                  "WHERE session_id=? ORDER BY ts", (session_id,))
        for r in token_spikes:
            r["leverage_product"] = r["tokens"]

        correction_markers = _rows(
            conn, "SELECT turn, ts, speaker, marker, quote FROM correction_markers "
                  "WHERE session_id=? ORDER BY ts", (session_id,))

        hedging_markers = _rows(
            conn, "SELECT turn, ts, marker, quote FROM hedging_markers WHERE session_id=? "
                  "ORDER BY ts", (session_id,))

        tooling_backlog_ranked = sorted(
            repetitive_flagged + corpus_gap_candidates + token_spikes,
            key=lambda r: r["leverage_product"], reverse=True)

        return {
            "spec": "[SPEC:CXC-RETRO]",
            "session_id": session_id,
            "generated_at": time.time(),
            "session": session,
            "handover": {
                "events": handover_events,
                "handover_cost_pct": session.get("handover_cost_pct") if session else None,
                "context_trajectory_source": session.get("context_trajectory_source") if session
                else None,
            },
            "capture_types": {
                "2_repetitive_task_frequency": {
                    "tier": "deterministic",
                    "threshold": repetitive_threshold,
                    "all_tallies": repetitive_all,
                    "flagged_evidence": repetitive_flagged,
                },
                "3_correction_markers_raw": {
                    "tier": "deterministic-raw (axis-clustering + feedback-memory routing left to "
                            "the judgment pass — NOT computed here)",
                    "evidence": correction_markers,
                },
                "5_read_frequency": {
                    "tier": "deterministic",
                    "evidence": file_reads,
                },
                "6_magma_queries": {
                    "tier": "deterministic",
                    "all_queries": magma_all,
                    "corpus_gap_candidates": corpus_gap_candidates,
                },
                "7_token_spikes": {
                    "tier": "deterministic",
                    "evidence": token_spikes,
                },
                "8_hedging_markers": {
                    "tier": "deterministic-ish",
                    "evidence": hedging_markers,
                },
            },
            "tooling_backlog_ranked": tooling_backlog_ranked,
            "judgment_capture_types_not_staged_here": [
                "1_decisions (rulings/commitments + why — decision-archivist agent, "
                "[DEC-AUTO-CROSSING-ARCHIVIST])",
                "3_correction_axis_clustering (the interpretive half of corrections; only the raw "
                "marker is staged above)",
                "4_dusk_gift_fodder (lesson-shape recognition)",
            ],
        }
    finally:
        conn.close()


# ── Judgment-vocabulary guard: the staged structure must carry no key that reads as a verdict about
# the human/instance. Checked by the selftest AND callable standalone as a lint. ─────────────────────
_BANNED_KEYS = {"score", "verdict", "grade", "rating", "severity", "quality", "blame", "fault",
                "criticism", "failure_count", "mistake_count", "wrongness", "competence"}


def _scan_keys(obj, path="$") -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _BANNED_KEYS:
                hits.append(f"{path}.{k}")
            hits += _scan_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _scan_keys(v, f"{path}[{i}]")
    return hits


def _selftest() -> int:
    import os
    import tempfile

    from intelligence.retrospective.inject import inject_transcript
    from intelligence.retrospective.transcript_reader import iso_to_epoch

    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.join(here, "tests", "fixture_transcript.jsonl")
    context_samples = {
        int(iso_to_epoch("2026-07-21T10:00:00Z")): 40.0,
        int(iso_to_epoch("2026-07-21T10:05:00Z")): 41.2,
    }

    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    with tempfile.TemporaryDirectory() as td:
        db_file = os.path.join(td, "metrics.db")
        staged = inject_transcript(fixture, db_path=db_file, context_samples=context_samples)
        out = pregenerate(staged["session_id"], db_path=db_file)

        # (a) no judgment vocabulary anywhere in the structure's KEYS.
        hits = _scan_keys(out)
        check(not hits, f"judgment-vocabulary keys found in staged output: {hits}")

        # (b) the right evidence landed.
        ct = out["capture_types"]
        check(any(r["signature"] == "ls -la" and r["count"] == 3
                  for r in ct["2_repetitive_task_frequency"]["flagged_evidence"]),
              f"repetitive evidence missing 'ls -la'x3: {ct['2_repetitive_task_frequency']}")
        check(any(g["query"] == "rate limiting policy" and g["null_count"] == 1
                  for g in ct["6_magma_queries"]["corpus_gap_candidates"]),
              f"corpus-gap candidate missing: {ct['6_magma_queries']}")
        check(len(ct["7_token_spikes"]["evidence"]) == 1,
              f"expected 1 token-spike, got {ct['7_token_spikes']}")
        check(len(ct["3_correction_markers_raw"]["evidence"]) >= 1,
              "expected >=1 correction marker in staged output")
        check(len(ct["8_hedging_markers"]["evidence"]) >= 1,
              "expected >=1 hedging marker in staged output")
        check(any(f["path"] == "/repo/src/fileA.py" and f["deliberate"] == 1 and f["count"] == 2
                  for f in ct["5_read_frequency"]["evidence"]),
              f"read-frequency missing deliberate fileA.py x2: {ct['5_read_frequency']}")

        # (c) handover section present with the same numbers inject.py computed.
        check(out["handover"]["handover_cost_pct"] is None,  # last pair mirrored = pair 2 = thin
              f"handover_cost_pct (last pair) should be None: {out['handover']}")
        check(len(out["handover"]["events"]) == 2, "expected 2 handover events staged")

        # (d) the judgment types are explicitly NAMED AS NOT STAGED, not silently absent.
        check(len(out["judgment_capture_types_not_staged_here"]) == 3,
              "expected the 3 judgment-type disclaimers to be present")

        # (e) it's valid JSON (a real staging artifact, not just a python dict).
        json.dumps(out)

    if failures:
        sys.stderr.write("FAIL:\n  " + "\n  ".join(failures) + "\n")
        return 1
    print("PASS: pregenerate.py selftest (staged evidence correct; NO judgment-vocabulary keys)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session-id")
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=None, help="write JSON here (default: stdout)")
    ap.add_argument("--threshold", type=int, default=REPETITIVE_THRESHOLD_DEFAULT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if not a.session_id:
        ap.print_usage()
        return 2

    out = pregenerate(a.session_id, db_path=a.db, repetitive_threshold=a.threshold)
    blob = json.dumps(out, indent=2)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(blob)
        print(f"staged {len(blob)} bytes -> {a.out}")
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
