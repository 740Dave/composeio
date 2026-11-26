"""Cross-implementation guard for the 1-BASED `.jsonl` line-number invariant.

DEFECT (2026-08-19, design-session auditor): `extract_design_inputs.py` reported
an `index` that was LOW BY ONE versus the raw transcript line (it cited 17809 for
text at raw line 17810, +1 across a whole bracket). Root cause: bare `enumerate`
(0-based). The verifier `verify_dsd_citations.py` keyed `human_turns` the same
0-based way, so the two tools AGREED WITH EACH OTHER while both disagreed with
`grep -n` (1-based) — the number every human and every DSD `(line N)` cite uses.
Fixing one file alone would have made the pair contradict; this is a FORK.

THE INVARIANT (its ONE AUTHORITY is the `index` block in extract_design_inputs.py):
    a statement/boundary/turn's line number is the 1-BASED `.jsonl` line number —
    the same number `grep -n <session>.jsonl` prints.

This test is CROSS-IMPLEMENTATION on purpose: it asserts the invariant against
BOTH the producer (`collect`) and the verifier (`human_turns`), and asserts they
agree with each other AND with `grep -n`. Any current-or-future implementation
that drops back to a 0-based counter fails here loudly. It FAILS on the pre-fix
(0-based) code and passes after.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import extract_design_inputs as edi  # noqa: E402
import verify_dsd_citations as vdc  # noqa: E402


# Each record is ONE physical line, so the 1-based grep -n line number of a
# record is its position in this list. A queue-operation ENQUEUE that never
# drains is included: the auditor warned the offset "needs care" where queue
# records interleave, and its index must be its own line like any other.
_RECORDS = [
    ("STATEMENT ALPHA on line one.",   # line 1, user
     {"type": "user", "message": {"role": "user", "content": "STATEMENT ALPHA on line one."}}),
    (None,                              # line 2, assistant — never a statement
     {"type": "assistant", "message": {"role": "assistant",
                                       "content": [{"type": "text", "text": "ack"}]}}),
    ("STATEMENT BRAVO queued on line three.",  # line 3, queued (never drains)
     {"type": "queue-operation", "operation": "enqueue",
      "content": "STATEMENT BRAVO queued on line three."}),
    (None,                              # line 4, the remove echo — must not double
     {"type": "queue-operation", "operation": "remove",
      "content": "STATEMENT BRAVO queued on line three."}),
    ("STATEMENT CHARLIE on line five.",  # line 5, user
     {"type": "user", "message": {"role": "user", "content": "STATEMENT CHARLIE on line five."}}),
]

#: {expected 1-based line number -> statement text}, from the list positions.
_EXPECTED = {i: text for i, (text, _rec) in enumerate(_RECORDS, start=1) if text}


def _fixture() -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    fh.write("\n".join(json.dumps(rec) for _t, rec in _RECORDS) + "\n")
    fh.close()
    return Path(fh.name)


def _grep_n_lines(path: Path, needle: str) -> set[int]:
    """The 1-based line numbers `grep -n needle` would print. This is TRUTH."""
    return {n for n, line in enumerate(path.read_text().splitlines(), start=1)
            if needle in line}


def test_extractor_index_is_1based_grep_line():
    p = _fixture()
    try:
        stmts, _bounds, _rc = edi.collect(p)
        by_text = {s.text: s.index for s in stmts}
        for line_no, text in _EXPECTED.items():
            assert text in by_text, f"{text!r} not extracted at all"
            assert by_text[text] == line_no, (
                f"index {by_text[text]} != 1-based line {line_no} for {text!r}")
            # and it agrees with grep -n on the raw file
            assert line_no in _grep_n_lines(p, text.split(" queued")[0].split(" on ")[0])
    finally:
        os.unlink(p)


def test_verifier_human_turns_is_1based_grep_line():
    p = _fixture()
    try:
        turns = vdc.human_turns(p)
        for line_no, text in _EXPECTED.items():
            assert line_no in turns, f"no turn keyed at 1-based line {line_no}"
            assert vdc.norm(text) in turns[line_no], (
                f"line {line_no} does not carry {text!r}")
        # the queued line that never drains is present exactly once, at its line
        assert 3 in turns and vdc.norm("STATEMENT BRAVO queued on line three.") in turns[3]
    finally:
        os.unlink(p)


def test_producer_and_verifier_agree_on_the_base():
    """The load-bearing FORK assertion: the producer's `index` is a valid key
    into the verifier's line-map AND resolves to the same text there. If either
    file reverts to 0-based, this breaks even though each file's own tests pass.
    """
    p = _fixture()
    try:
        stmts, _b, _rc = edi.collect(p)
        turns = vdc.human_turns(p)
        for s in stmts:
            assert s.index in turns, (
                f"producer emitted line {s.index} the verifier has no turn at — "
                "the two tools disagree on the line base (a fork drift)")
            assert vdc.norm(s.text) in turns[s.index]
    finally:
        os.unlink(p)


if __name__ == "__main__":
    test_extractor_index_is_1based_grep_line()
    test_verifier_human_turns_is_1based_grep_line()
    test_producer_and_verifier_agree_on_the_base()
    print("PASS: 3 cross-implementation checks")
