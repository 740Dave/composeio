#!/usr/bin/env python3
"""inject.py — the dusk-parse tailer-injector: one transcript JSONL -> SQLite metric rows
([SPEC:CXC-RETRO] §5a/§5b — "the deterministic script parses the session transcript AT THE CROSSING
and writes that session's metric rows to SQLite in one batch — no live per-tool-call logging seam").

THE HARD BOUNDARY: this module TALLIES. It never decides a tally is "good" or "bad", never ranks the
human, never fabricates evidence it didn't find in the transcript. Every row it writes is a raw count
or a verbatim quote traceable to a specific transcript record.

IDEMPOTENT BY CONSTRUCTION:
  - `sessions` and `handover_events` are UPSERTed by primary key (session_id [, seq]) — re-running
    on the same transcript recomputes identical values (a pure function of file content) and simply
    overwrites, never duplicates.
  - The aggregate tally tables (`file_reads`, `repetitive_commands`, `magma_queries`) are UPSERTed by
    their natural key with `count = excluded.count` (not incremented) — a full re-parse yields the
    same counts, so re-injection is a no-op on the data, not a double-count.
  - The per-EVENT tables (`token_spikes`, `correction_markers`, `hedging_markers`) key on
    `(session_id, event_hash)` where `event_hash` is a content-derived hash (session_id + turn + ts +
    the matched value) — `INSERT OR IGNORE` means the identical event reinjected is silently skipped,
    never duplicated.

Usage:
  python3 -m intelligence.retrospective.inject --once TRANSCRIPT.jsonl [--db PATH]
      [--spike-abs N] [--spike-delta N]
  python3 -m intelligence.retrospective.inject --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time

from intelligence.retrospective import db as db_mod
from intelligence.retrospective.transcript_reader import (
    content_blocks, content_text, iso_to_epoch, iter_records, session_id_of, usage_tokens,
)

# ── Tunable thresholds (§7 config-surface knobs; CLI-overridable, no hardcoded verdict) ──────────
DEFAULT_SPIKE_ABS = 20_000       # a single turn's total tokens at/above this = a spike on its own
DEFAULT_SPIKE_DELTA = 15_000     # a jump vs. the previous assistant turn's total at/above this = a spike

# ── Handover markers (the `handover` skill's stamped boundaries) ─────────────────────────────────
_HANDOVER_RE = re.compile(r"HANDOVER_(START|END)\s+([0-9T:\-\.Z]+)")

# ── Correction markers (capture-type 3, RAW slice only — see db.py/pregenerate.py docstrings for
# the tiering nuance: axis-clustering + feedback-memory routing stay judgment, NOT built here) ─────
#
# [FIX-CORRECTION-RECALL — docs/intelligence-console/retrospective-test-findings-2026-07-22.md §2e]
# A real 24k-record session hand-grepped at ~76 "reframe"-shaped corrective exchanges, but this list
# matched exactly ONE (0 for a second, 6.5k-record session) — including a ZERO hit on the two turns
# that seeded that session's entire meta-improvement arc. Root cause: this collaborator's dominant
# corrective register is REFRAMING / gap-naming ("let's step back", "that's a gap", "reframe", "the
# whole point of..."), not blunt negation. The two patterns added below ("named-gap", "step-back")
# are the genuinely LEXICAL forms recovered from that session's actual missed turns (each is a
# precise, low-noise phrase — verified against the real transcript: 1 hit each in 24,474 lines, not
# a vocabulary flood). Deliberately NOT added: "reframe" (59 hits) and "the whole point" (27 hits) —
# both fire constantly on ordinary prose that isn't corrective at all (design commentary, spec
# text), and the turn that most needed to be caught ("the whole point of moments like this... it's
# an 'ah-ha!' moment") is corrective only by MEANING, not by any distinguishing phrase — no regex
# addition here would recover it without also flagging dozens of unrelated turns.
#
# THE BOUNDARY THIS FILE STAYS ON: a fixed phrase list can only ever catch corrections that are
# LEXICALLY shaped — literal, recognizable phrasings. It cannot and must not try to catch corrections
# that are SEMANTIC — recognizable only by understanding what was said, not by matching how it was
# said (a reframe, a "that's not quite it" delivered as a paragraph of reasoning, a change of
# position with no correcting-shaped words at all). That tail is explicitly the judgment/harvest
# pass's job (see pregenerate.py's "3_correction_axis_clustering... left to the judgment pass"), not
# a regex-tuning problem — broadening this list further chases recall at the cost of precision
# without ever closing the register gap. If you're tempted to add a pattern to catch a specific
# semantic correction you saw once, don't; that's a judgment-pass / harvest-signal problem.
CORRECTION_PATTERNS = [
    ("explicit-no", re.compile(r"\bno[,.]?\s+that(?:'s| is)\b", re.I)),
    ("not-what-asked", re.compile(r"not what i (?:asked|meant|wanted)", re.I)),
    ("please-redo", re.compile(r"\bplease (?:redo|fix|correct)\b", re.I)),
    ("thats-wrong", re.compile(r"that'?s (?:not right|wrong|incorrect)", re.I)),
    ("actually-correction", re.compile(r"\bactually,? i (?:meant|want|need)\b", re.I)),
    ("self-correct", re.compile(r"\bwait,? that'?s (?:wrong|not right)\b", re.I)),
    ("named-gap", re.compile(r"\bthat'?s a gap\b", re.I)),
    ("step-back", re.compile(r"\blet'?s step back\b", re.I)),
]

# ── Hedging markers (capture-type 8, spec tier "deterministic-ish") ────────────────────────────────
HEDGE_PATTERNS = [
    ("not-sure", re.compile(r"\bi'?m not (?:sure|certain)\b", re.I)),
    ("no-source", re.compile(r"\bno source for that\b", re.I)),
    ("dont-know", re.compile(r"\bi don'?t (?:know|have information)\b", re.I)),
    ("might-be-wrong", re.compile(r"\bmight be (?:wrong|incorrect)\b", re.I)),
    ("grain-of-salt", re.compile(r"\btake (?:this|that|it) with a grain of salt\b", re.I)),
    ("cannot-verify", re.compile(r"\bi (?:can'?t|cannot) verify\b", re.I)),
]

_MAGMA_TOOL_RE = re.compile(r"magma", re.I)
_NULL_RESULT_RE = re.compile(r'"(?:results|matches|nodes)"\s*:\s*\[\]|no results found', re.I)
_GREP_PATH_RE = re.compile(r"^([^\s:][^:]*\.[A-Za-z0-9_]+):")  # ripgrep-style "path:line:match"


def _event_hash(session_id: str, kind: str, turn: int, ts: float | None, value: str) -> str:
    raw = f"{session_id}|{kind}|{turn}|{ts}|{value}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# [FIX-MCP-TOOL-SIGNATURE — retrospective-test-findings-2026-07-22.md §2b] Priority-ordered
# discriminating-arg keys for MCP/agent/orchestration tools that carry neither a command, a search
# pattern, nor a file path (Agent, SendMessage, mcp__composeio-channel__reply, ToolSearch, TaskCreate,
# TaskUpdate, mcp__*_find/query tools, ...). Without this, `_tool_arg` fell through to the tool NAME
# for every one of these, so e.g. 142 structurally-distinct `Agent` dispatches (different briefs)
# collapsed onto one identical signature and dominated the repetitive-command tally as false
# "toolify this" noise. Ordered CONTENT-shaped keys (query/summary/description/subject/prompt/text/
# message/content — what actually varies call-to-call) before ADDRESSING-shaped keys (target/taskId/
# to/recipient — which often repeat, e.g. the same channel recipient across many distinct messages),
# so the chosen signature is the one most likely to distinguish "genuinely the same call" from
# "coincidentally the same tool". Verified against this real session's actual tool_use payload shapes
# (Agent: description/subagent_type/prompt; SendMessage: to/summary/message/...; ToolSearch: query;
# TaskCreate: subject/description; mcp__composeio-channel__reply: text).
_MCP_SIGNATURE_KEYS = ("query", "q", "summary", "description", "subject", "caption", "prompt",
                       "text", "message", "content", "target", "taskId", "task_id", "to",
                       "recipient")


def _tool_arg(name: str, inp: dict) -> str | None:
    """Mirrors permission_audit.py's `_tool_arg` convention (Bash->command, file tools->path) —
    duplicated (a few lines) rather than imported, matching this package's stated blast-radius choice
    not to couple to permission_audit.py's private names. Extended here (permission_audit has no
    reason to care) with Grep/Glob->pattern, so the repetitive-command tally distinguishes what was
    searched, not just that a search happened. Further extended with an MCP/agent-tool fallback (see
    _MCP_SIGNATURE_KEYS above) so those tools don't collapse onto the bare tool name."""
    if not isinstance(inp, dict):
        return None
    if name == "Bash":
        return inp.get("command")
    if name in ("Grep", "Glob"):
        return inp.get("pattern")
    fp = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if fp:
        return fp
    for key in _MCP_SIGNATURE_KEYS:
        v = inp.get(key)
        if isinstance(v, str) and v:
            return v[:200]
    return None


class Injector:
    """Pure parse logic over one transcript's records. `parse(records)` returns the staged rows;
    `write(conn, session_id, transcript_path, staged)` performs the idempotent upserts. Split so the
    selftest can assert on `staged` directly, independent of SQLite."""

    def __init__(self, spike_abs: int = DEFAULT_SPIKE_ABS, spike_delta: int = DEFAULT_SPIKE_DELTA,
                 context_samples: dict[int, float] | None = None):
        self.spike_abs = spike_abs
        self.spike_delta = spike_delta
        # context_samples: {epoch_second: pct} — a caller-supplied synthetic/real context-%
        # trajectory (§5b: statusline-only, thin). Keyed to the exact second of a marker for a
        # best-effort correlation; absent/unmatched -> None (never guessed).
        self.context_samples = context_samples or {}

    def _context_at(self, ts: float | None) -> tuple[float | None, str]:
        if ts is None:
            return None, "unavailable"
        pct = self.context_samples.get(int(round(ts)))
        return (pct, "sample-provided") if pct is not None else (None, "unavailable")

    def parse(self, path: str) -> dict:
        records = list(iter_records(path))
        session_id = session_id_of(path, records[0] if records else None)

        start_ts: float | None = None
        end_ts: float | None = None
        turn = 0

        file_reads: dict[tuple[str, int], int] = {}          # (path, deliberate) -> count
        repetitive: dict[tuple[str, str], int] = {}           # (tool, signature) -> count
        magma: dict[tuple[str, str], list[int]] = {}          # (tool, query) -> [call_count, null_count]
        token_spikes: list[dict] = []
        corrections: list[dict] = []
        hedges: list[dict] = []
        handover_marks: list[tuple[str, float]] = []          # [(START|END, ts), ...] in order

        pending_tool: dict[str, tuple[str, dict]] = {}        # tool_use id -> (name, input)
        prev_assistant_tokens: int | None = None

        for rec in records:
            ts = iso_to_epoch(rec.get("timestamp"))
            if ts is not None:
                start_ts = ts if start_ts is None else min(start_ts, ts)
                end_ts = ts if end_ts is None else max(end_ts, ts)

            rtype = rec.get("type")
            has_msg = isinstance(rec.get("message"), dict)
            if has_msg:
                turn += 1

            # -- handover markers: scan ALL text (tool_use command text AND tool_result echo text) --
            text = content_text(rec)
            for m in _HANDOVER_RE.finditer(text):
                handover_marks.append((m.group(1), iso_to_epoch(m.group(2)) or 0.0))
            for block in content_blocks(rec):
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    cmd = (block.get("input") or {}).get("command", "")
                    for m in _HANDOVER_RE.finditer(cmd):
                        handover_marks.append((m.group(1), iso_to_epoch(m.group(2)) or 0.0))

            # -- correction / hedging markers (raw pattern match; verbatim quote) --
            if text:
                speaker = "assistant" if rtype == "assistant" else "user"
                for label, pat in CORRECTION_PATTERNS:
                    mm = pat.search(text)
                    if mm:
                        corrections.append({
                            "turn": turn, "ts": ts, "speaker": speaker, "marker": label,
                            "quote": text[max(0, mm.start() - 20):mm.end() + 40].strip()[:200],
                        })
                if rtype == "assistant":
                    for label, pat in HEDGE_PATTERNS:
                        mm = pat.search(text)
                        if mm:
                            hedges.append({
                                "turn": turn, "ts": ts, "marker": label,
                                "quote": text[max(0, mm.start() - 20):mm.end() + 40].strip()[:200],
                            })

            # -- token-spike detection (assistant records only) --
            if rtype == "assistant":
                tot = usage_tokens(rec)
                if tot is not None:
                    is_abs = tot >= self.spike_abs
                    delta = None if prev_assistant_tokens is None else tot - prev_assistant_tokens
                    is_delta = delta is not None and delta >= self.spike_delta
                    if is_abs or is_delta:
                        token_spikes.append({
                            "turn": turn, "ts": ts, "tokens": tot,
                            "prev_tokens": prev_assistant_tokens,
                            "reason": "absolute" if is_abs else "delta",
                        })
                    prev_assistant_tokens = tot

            # [DEFERRED: FIX-HEREDOC-INVISIBLE — retrospective-test-findings-2026-07-22.md §2c]
            # `cat > path <<'EOF' ... EOF`-shaped Bash commands that author a file are (a) invisible
            # to capture-type 5 (file_reads has no "written" notion at all — only Read/Grep/Glob) and
            # (b) each stores its full multi-KB heredoc body verbatim as the Bash signature below,
            # bloating `all_tallies` with one-off rows. NOT fixed here: (a) needs a genuinely new
            # capture-type (write-frequency — a new table + a new pregenerate.py section + doc/spec
            # updates), out of proportion for this pass; (b) was considered (truncate the Bash
            # signature) but REJECTED as not obviously low-risk — truncating to a fixed prefix length
            # risks two DIFFERENT heredoc-authored files colliding onto one signature if their opening
            # lines match (e.g. two `cat > .../SKILL.md <<'EOF'\n---\ntitle:` writes), which would
            # manufacture a FALSE "repetitive command" the current full-body exact-match never would.
            # It's also cosmetic, not a ranking bug: these one-off signatures never reach the
            # threshold (>=2) so they never pollute `flagged_evidence`/`tooling_backlog_ranked` (the
            # only fields anything downstream actually ranks on) — only `all_tallies`' raw row count.
            # Left for a future pass that adds a real write-frequency capture-type.
            # -- tool_use: read-frequency (deliberate), repetitive-command tally, magma-query open --
            for block in content_blocks(rec):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "?")
                inp = block.get("input") or {}
                tid = block.get("id") or ""
                pending_tool[tid] = (name, inp)

                if name == "Read":
                    fp = inp.get("file_path")
                    if fp:
                        file_reads[(fp, 1)] = file_reads.get((fp, 1), 0) + 1

                sig = _tool_arg(name, inp) or name
                repetitive[(name, sig)] = repetitive.get((name, sig), 0) + 1

                if _MAGMA_TOOL_RE.search(name):
                    query = inp.get("query") or inp.get("q") or json.dumps(inp)[:120]
                    key = (name, str(query))
                    magma.setdefault(key, [0, 0])
                    magma[key][0] += 1

            # -- tool_result: incidental reads from Grep/Glob output; magma null-result flag --
            for block in content_blocks(rec):
                if block.get("type") != "tool_result":
                    continue
                tid = block.get("tool_use_id") or ""
                pend = pending_tool.get(tid)
                if pend is None:
                    continue
                pname, pinput = pend
                raw = block.get("content")
                blob = raw if isinstance(raw, str) else json.dumps(raw)
                blob = blob or ""

                if pname in ("Grep", "Glob"):
                    seen_paths: set[str] = set()
                    for line in blob.splitlines():
                        mm = _GREP_PATH_RE.match(line.strip())
                        if mm:
                            seen_paths.add(mm.group(1))
                    for p in seen_paths:
                        file_reads[(p, 0)] = file_reads.get((p, 0), 0) + 1

                if _MAGMA_TOOL_RE.search(pname):
                    query = pinput.get("query") or pinput.get("q") or json.dumps(pinput)[:120]
                    key = (pname, str(query))
                    magma.setdefault(key, [0, 0])
                    if _NULL_RESULT_RE.search(blob) or blob.strip() in ("", "[]", "{}", "null"):
                        magma[key][1] += 1

        # -- pair up handover marks in order (START then END) into events --
        handover_events: list[dict] = []
        open_start: float | None = None
        for kind, mts in handover_marks:
            if kind == "START":
                open_start = mts
            elif kind == "END" and open_start is not None:
                s_pct, s_src = self._context_at(open_start)
                e_pct, e_src = self._context_at(mts)
                cost = (e_pct - s_pct) if (s_pct is not None and e_pct is not None) else None
                src = "sample-provided" if (s_src == "sample-provided" and e_src == "sample-provided") \
                    else "unavailable"
                handover_events.append({
                    "start_ts": open_start, "end_ts": mts, "cost_pct": cost,
                    "context_pct_start": s_pct, "context_pct_end": e_pct, "context_source": src,
                })
                open_start = None

        return {
            "session_id": session_id,
            "start_ts": start_ts, "end_ts": end_ts, "record_count": len(records),
            "handover_events": handover_events,
            "file_reads": file_reads, "repetitive_commands": repetitive, "magma_queries": magma,
            "token_spikes": token_spikes, "correction_markers": corrections,
            "hedging_markers": hedges,
        }

    def write(self, conn: sqlite3.Connection, staged: dict, transcript_path: str) -> None:
        sid = staged["session_id"]
        events = staged["handover_events"]
        last = events[-1] if events else None

        conn.execute(
            """INSERT INTO sessions (session_id, transcript_path, start_ts, end_ts, record_count,
                    handover_start_ts, handover_end_ts, handover_cost_pct,
                    context_pct_at_handover_start, context_pct_at_handover_end,
                    context_trajectory_source, injected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                    transcript_path=excluded.transcript_path, start_ts=excluded.start_ts,
                    end_ts=excluded.end_ts, record_count=excluded.record_count,
                    handover_start_ts=excluded.handover_start_ts,
                    handover_end_ts=excluded.handover_end_ts,
                    handover_cost_pct=excluded.handover_cost_pct,
                    context_pct_at_handover_start=excluded.context_pct_at_handover_start,
                    context_pct_at_handover_end=excluded.context_pct_at_handover_end,
                    context_trajectory_source=excluded.context_trajectory_source,
                    injected_at=excluded.injected_at""",
            (sid, transcript_path, staged["start_ts"], staged["end_ts"], staged["record_count"],
             last["start_ts"] if last else None, last["end_ts"] if last else None,
             last["cost_pct"] if last else None,
             last["context_pct_start"] if last else None,
             last["context_pct_end"] if last else None,
             last["context_source"] if last else None,
             time.time()))

        for seq, ev in enumerate(events, start=1):
            conn.execute(
                """INSERT INTO handover_events (session_id, seq, start_ts, end_ts, cost_pct,
                        context_pct_start, context_pct_end, context_source)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id, seq) DO UPDATE SET
                        start_ts=excluded.start_ts, end_ts=excluded.end_ts,
                        cost_pct=excluded.cost_pct, context_pct_start=excluded.context_pct_start,
                        context_pct_end=excluded.context_pct_end,
                        context_source=excluded.context_source""",
                (sid, seq, ev["start_ts"], ev["end_ts"], ev["cost_pct"],
                 ev["context_pct_start"], ev["context_pct_end"], ev["context_source"]))

        for (path_, deliberate), count in staged["file_reads"].items():
            conn.execute(
                """INSERT INTO file_reads (session_id, path, deliberate, count) VALUES (?,?,?,?)
                   ON CONFLICT(session_id, path, deliberate) DO UPDATE SET count=excluded.count""",
                (sid, path_, deliberate, count))

        for (tool, sig), count in staged["repetitive_commands"].items():
            conn.execute(
                """INSERT INTO repetitive_commands (session_id, tool, signature, count)
                   VALUES (?,?,?,?)
                   ON CONFLICT(session_id, tool, signature) DO UPDATE SET count=excluded.count""",
                (sid, tool, sig, count))

        for (tool, query), (call_count, null_count) in staged["magma_queries"].items():
            conn.execute(
                """INSERT INTO magma_queries (session_id, tool, query, call_count, null_count)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(session_id, tool, query) DO UPDATE SET
                        call_count=excluded.call_count, null_count=excluded.null_count""",
                (sid, tool, query, call_count, null_count))

        for ev in staged["token_spikes"]:
            eh = _event_hash(sid, "token_spike", ev["turn"], ev["ts"], str(ev["tokens"]))
            conn.execute(
                """INSERT OR IGNORE INTO token_spikes
                        (session_id, event_hash, turn, ts, tokens, prev_tokens, reason)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, eh, ev["turn"], ev["ts"], ev["tokens"], ev["prev_tokens"], ev["reason"]))

        for ev in staged["correction_markers"]:
            eh = _event_hash(sid, "correction", ev["turn"], ev["ts"], ev["marker"])
            conn.execute(
                """INSERT OR IGNORE INTO correction_markers
                        (session_id, event_hash, turn, ts, speaker, marker, quote)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, eh, ev["turn"], ev["ts"], ev["speaker"], ev["marker"], ev["quote"]))

        for ev in staged["hedging_markers"]:
            eh = _event_hash(sid, "hedge", ev["turn"], ev["ts"], ev["marker"])
            conn.execute(
                """INSERT OR IGNORE INTO hedging_markers
                        (session_id, event_hash, turn, ts, marker, quote)
                   VALUES (?,?,?,?,?,?)""",
                (sid, eh, ev["turn"], ev["ts"], ev["marker"], ev["quote"]))

        conn.commit()


def inject_transcript(path: str, db_path: str | None = None, spike_abs: int = DEFAULT_SPIKE_ABS,
                       spike_delta: int = DEFAULT_SPIKE_DELTA,
                       context_samples: dict[int, float] | None = None) -> dict:
    """The `--once` entry point: parse one transcript, write it to the DB, return the staged dict
    (so callers/tests can inspect exactly what was written)."""
    inj = Injector(spike_abs=spike_abs, spike_delta=spike_delta, context_samples=context_samples)
    staged = inj.parse(path)
    conn = db_mod.connect(db_path)
    try:
        inj.write(conn, staged, path)
    finally:
        conn.close()
    return staged


# ── Selftest (imports the fixture; see tests/test_retrospective.py for the full assertion suite —
# this is a fast smoke check runnable with zero pytest dependency) ──────────────────────────────────
def _selftest() -> int:
    import os
    import tempfile

    here = os.path.dirname(os.path.abspath(__file__))
    fixture = os.path.join(here, "tests", "fixture_transcript.jsonl")
    if not os.path.exists(fixture):
        sys.stderr.write(f"FAIL: fixture not found at {fixture}\n")
        return 1

    # A synthetic context-% trajectory for handover pair 1 only (pair 2 stays intentionally thin —
    # demonstrates §5b's "the trajectory is thin" honestly rather than faking it).
    context_samples = {
        int(iso_to_epoch("2026-07-21T10:00:00Z")): 40.0,
        int(iso_to_epoch("2026-07-21T10:05:00Z")): 41.2,
    }

    with tempfile.TemporaryDirectory() as td:
        db_file = os.path.join(td, "metrics.db")
        staged1 = inject_transcript(fixture, db_path=db_file, context_samples=context_samples)
        staged2 = inject_transcript(fixture, db_path=db_file, context_samples=context_samples)  # re-inject

        failures = []

        def check(cond, msg):
            if not cond:
                failures.append(msg)

        check(staged1["session_id"] == staged2["session_id"], "session_id unstable across re-inject")
        check(len(staged1["handover_events"]) == 2,
              f"expected 2 handover pairs, got {len(staged1['handover_events'])}")
        check(abs((staged1["handover_events"][0]["cost_pct"] or -1) - 1.2) < 1e-6,
              f"pair 1 cost_pct wrong: {staged1['handover_events'][0]}")
        check(staged1["handover_events"][1]["cost_pct"] is None,
              f"pair 2 should stay thin/None (no samples given): {staged1['handover_events'][1]}")

        conn = db_mod.connect(db_file)
        cur = conn.execute("SELECT count(*) FROM sessions")
        check(cur.fetchone()[0] == 1, "sessions should have exactly 1 row")
        cur = conn.execute("SELECT count(*) FROM handover_events WHERE session_id=?",
                            (staged1["session_id"],))
        check(cur.fetchone()[0] == 2, "handover_events should have exactly 2 rows (idempotent)")
        cur = conn.execute(
            "SELECT count FROM repetitive_commands WHERE session_id=? AND tool='Bash' AND signature='ls -la'",
            (staged1["session_id"],))
        row = cur.fetchone()
        check(row is not None and row[0] == 3, f"expected Bash 'ls -la' count=3, got {row}")
        cur = conn.execute(
            "SELECT null_count FROM magma_queries WHERE session_id=? AND query='rate limiting policy'",
            (staged1["session_id"],))
        row = cur.fetchone()
        check(row is not None and row[0] == 1, f"expected 1 null-result magma query, got {row}")
        cur = conn.execute("SELECT count(*) FROM token_spikes WHERE session_id=?",
                            (staged1["session_id"],))
        n_spikes = cur.fetchone()[0]
        check(n_spikes == 1, f"expected 1 token-spike row, got {n_spikes}")
        cur = conn.execute("SELECT count(*) FROM correction_markers WHERE session_id=?",
                            (staged1["session_id"],))
        check(cur.fetchone()[0] >= 1, "expected >=1 correction marker")
        cur = conn.execute("SELECT count(*) FROM hedging_markers WHERE session_id=?",
                            (staged1["session_id"],))
        check(cur.fetchone()[0] >= 1, "expected >=1 hedging marker")
        conn.close()

        if failures:
            sys.stderr.write("FAIL:\n  " + "\n  ".join(failures) + "\n")
            return 1
    print("PASS: inject.py selftest (fixture inject + idempotent re-inject)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("transcript", nargs="?", help="session .jsonl transcript")
    ap.add_argument("--once", action="store_true", help="inject the given transcript (default mode)")
    ap.add_argument("--db", default=None, help="metrics DB path (default: db.py's db_path())")
    ap.add_argument("--spike-abs", type=int, default=DEFAULT_SPIKE_ABS)
    ap.add_argument("--spike-delta", type=int, default=DEFAULT_SPIKE_DELTA)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()
    if not a.transcript:
        ap.print_usage()
        return 2
    staged = inject_transcript(a.transcript, db_path=a.db, spike_abs=a.spike_abs,
                                spike_delta=a.spike_delta)
    print(f"injected session={staged['session_id']} records={staged['record_count']} "
          f"handover_pairs={len(staged['handover_events'])} "
          f"file_reads={len(staged['file_reads'])} repetitive={len(staged['repetitive_commands'])} "
          f"magma={len(staged['magma_queries'])} spikes={len(staged['token_spikes'])} "
          f"corrections={len(staged['correction_markers'])} hedges={len(staged['hedging_markers'])} "
          f"-> {a.db or db_mod.db_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
