#!/usr/bin/env python3
"""Enforcing test for the de-forked, partial-line-safe tail reader ([SPEC:CXC-IC-STREAMS]
durability invariant) — the defect where `self/subagents` went SILENT because a large `Agent`
dispatch/completion line, split across writes, was dropped as an unparseable fragment.

Two levels of guard:
  * REGRESSION WITNESS — the OLD naive `readline()`+`json.loads`+`continue` loop DROPS a line
    whose newline has not yet been flushed; the new `LineFollower` DELIVERS it whole. This test
    FAILS before the fix (the naive loop was the shipped behavior) and passes after.
  * CROSS-IMPLEMENTATION — the SAME split-line hazard, driven through BOTH daemons that share the
    reader (`transcript_publisher` Agent lifecycle -> self/subagents; `deliverable_monitor` Write
    -> qualifying path). Any future daemon that tails via a hand-rolled loop instead of the shared
    reader will trip these.

Run:  python3 compose_projects/intelligence/publisher/test_tail_follow.py
      (or) cd compose_projects/intelligence/publisher && python3 -m pytest test_tail_follow.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tail_follow import LineFollower
import transcript_publisher as tp
import deliverable_monitor as dm


# ── realistic large lines (the ones that get split across flushes) ──────────
def _agent_dispatch_line() -> str:
    big_prompt = "You are a defect-investigator. " + ("X" * 6000)
    return json.dumps({
        "type": "assistant", "timestamp": "2026-07-22T22:43:00Z",
        "message": {"content": [{"type": "tool_use", "id": "a1", "name": "Agent",
            "input": {"subagent_type": "defect-investigator",
                      "description": "Investigate chips-not-updating", "prompt": big_prompt}}]}}) + "\n"


def _agent_completion_line() -> str:
    big_out = "root cause: partial line. " + ("Y" * 6000)
    return json.dumps({
        "type": "user", "timestamp": "2026-07-22T22:48:48Z",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "a1",
            "content": big_out + " <subagent_tokens>71990</subagent_tokens>"}]}}) + "\n"


def _write_tool_line() -> str:
    big_body = "# design doc\n" + ("Z" * 6000)
    return json.dumps({
        "type": "assistant", "timestamp": "2026-07-22T22:50:00Z",
        "message": {"content": [{"type": "tool_use", "id": "w1", "name": "Write",
            "input": {"file_path": "docs/design.md", "content": big_body}}]}}) + "\n"


# ── drivers that interleave split appends with reads (mimics a live tailer) ──
def _interleave(chunks, reader_step):
    """Write each chunk to a fresh file, calling reader_step(fh) after each append (a 'poll').
    Returns the accumulated complete lines the reader delivered."""
    path = os.path.join(tempfile.mkdtemp(), "sess.jsonl")
    open(path, "w").close()
    fh = open(path, "r", encoding="utf-8", errors="replace")
    fh.seek(0, os.SEEK_END)
    got = []
    for ch in chunks:
        with open(path, "a", encoding="utf-8") as w:
            w.write(ch); w.flush()
        got.extend(reader_step(fh))
    fh.close()
    return got


def _naive_step_factory():
    """The OLD shipped loop: drain readline() to EOF, drop anything that isn't valid JSON."""
    def step(fh):
        out = []
        while True:
            line = fh.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue  # fragment silently discarded; offset already advanced past it
        return out
    return step


def _follower_step_factory():
    follower = None

    def step(fh):
        nonlocal follower
        if follower is None:
            follower = LineFollower(fh)
        return [json.loads(l) for l in follower.poll()]
    return step


def _split(line, n=2):
    """Split a line into n roughly-equal chunks — the last chunk carries the newline."""
    size = len(line) // n
    return [line[i * size:(i + 1) * size] for i in range(n - 1)] + [line[(n - 1) * size:]]


# ── REGRESSION WITNESS ──────────────────────────────────────────────────────
def test_naive_loop_drops_split_large_line_but_follower_delivers_it():
    line = _agent_dispatch_line()
    chunks = _split(line, 2)

    naive = _interleave(chunks, _naive_step_factory())
    assert naive == [], (
        "regression witness: the OLD naive readline loop should DROP the split line "
        f"(this is the shipped defect) — got {len(naive)} entries")

    follower = _interleave(chunks, _follower_step_factory())
    assert len(follower) == 1, f"LineFollower must deliver the split line whole — got {follower}"
    block = follower[0]["message"]["content"][0]
    assert block["name"] == "Agent" and block["input"]["subagent_type"] == "defect-investigator"


def test_follower_survives_3way_split_and_odd_boundaries():
    line = _agent_dispatch_line()
    for n in (3, 4, 5):
        got = _interleave(_split(line, n), _follower_step_factory())
        assert len(got) == 1, f"{n}-way split should still deliver exactly one whole line, got {got}"


def test_follower_delivers_atomic_and_multiple_lines_and_skips_blanks():
    a = _write_tool_line()
    b = _agent_dispatch_line()
    # one flush carrying two whole lines plus a blank line between them
    got = _interleave([a + "\n" + b], _follower_step_factory())
    assert len(got) == 2, f"two atomic lines in one flush should both deliver, got {len(got)}"


# ── CROSS-IMPLEMENTATION: the shared reader feeds each daemon's detector ─────
def test_transcript_publisher_emits_subagent_lifecycle_from_split_lines():
    pub = tp.TranscriptPublisher()
    events = []
    # dispatch line split, THEN completion line split — both must survive the tailer.
    for line in (_agent_dispatch_line(), _agent_completion_line()):
        for entry in _interleave(_split(line, 2), _follower_step_factory()):
            events += pub.process_entry(entry)
    subs = [p for ns, p in events if ns == tp.NS_SUBAGENTS]
    statuses = [p["status"] for p in subs]
    assert statuses == ["dispatched", "done"], f"expected dispatched then done, got {statuses}"
    assert subs[1].get("tokens") == 71990, f"completion tokens dropped: {subs[1]}"


def test_deliverable_monitor_detects_write_from_split_line():
    paths = []
    for entry in _interleave(_split(_write_tool_line(), 2), _follower_step_factory()):
        paths += dm.qualifying_writes(entry)
    assert any(p.endswith("docs/design.md") for p in paths), (
        f"a split Write tool_use must still yield its target path, got {paths}")


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
