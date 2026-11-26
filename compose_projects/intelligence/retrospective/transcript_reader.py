#!/usr/bin/env python3
"""transcript_reader.py — a small SHARED deterministic reader for Claude Code session-transcript
JSONL files ([SPEC:CXC-RETRO] §5b — "dusk-parse for everything the durable transcript already
holds"; the transcript-query skill's documented record schema + traps).

WHY THIS EXISTS, AND THE BLAST-RADIUS DECISION (read before touching):
`compose_projects/intelligence/tools/permission_audit.py` and `compose_projects/intelligence/publisher/transcript_publisher.py` each
already stream a transcript JSONL with their own small inline loop —
`open(path, errors="replace"); for line in fh: json.loads(line)` (permission_audit), and the same
shape via a plain `open(...).readline()` tail loop (transcript_publisher) — both tolerant of
malformed lines (skip on `ValueError`/`JSONDecodeError`).

Rather than fork a THIRD copy of that shape for the retrospective injector, this module factors it
out. **It does NOT modify either existing file**: neither `permission_audit.py` nor
`transcript_publisher.py` imports this module, and their inline loops are untouched byte-for-byte.
Retrofitting them to import this reader was considered and REJECTED as higher blast-radius than
benefit — it would touch two working, tested, production-hosted consumers (one runs as a live daemon)
to save ~6 lines each, for zero behavior change. The brief explicitly invites this fallback ("if
factoring risks touching them, instead import/duplicate minimally and FLAG it"): this IS that choice,
flagged. A future cleanup pass can retrofit both consumers onto this reader once it has independent
test coverage of its own (this module's fixture-driven selftest, run via
`compose_projects/intelligence/retrospective/tests/test_retrospective.py`, is exactly that coverage-in-waiting).

Provides:
  iter_records(path)                -> stream parsed dict records from one JSONL transcript.
  iso_to_epoch(ts)                  -> parse a `timestamp` (ISO-8601, often Z-suffixed) to epoch
                                        seconds, or None. Duplicated 1:1 from
                                        transcript_publisher.py's `_iso_to_epoch` (a 4-line pure
                                        function) rather than imported, to avoid coupling this module
                                        to a private underscore-name in another package.
  session_id_of(path, first_record) -> the session id: `first_record["sessionId"]` if present, else
                                        the filename stem (the transcript-query skill: "the
                                        transcript's on-disk name IS the session-uuid").
  content_blocks(entry)             -> the list of `message.content` parts for a user/assistant
                                        record, or [] when content is a plain string or absent (the
                                        list-vs-string trap the transcript-query skill documents:
                                        a denial tool_result can be a bare-string user message).
  content_text(entry)               -> ALL text in a record, string content and `{"type":"text"}`
                                        blocks alike, concatenated — for marker/pattern scanning that
                                        doesn't care about block structure.
  usage_tokens(entry)               -> this TURN's own token cost (input + output +
                                        newly-created cache), or None when the record carries no
                                        usage block. Deliberately EXCLUDES cache_read_input_tokens
                                        — see the function docstring for why (FIX-TOKENSPIKE-
                                        SATURATION).
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime


def iso_to_epoch(ts) -> float | None:
    """Parse a JSONL entry `timestamp` (ISO-8601, often Z-suffixed) to epoch seconds, or None.
    (Duplicated from transcript_publisher._iso_to_epoch — see module docstring for why.)"""
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iter_records(path: str) -> Iterator[dict]:
    """Stream parsed JSON records from a transcript JSONL, one dict per non-empty line. Tolerant of
    malformed lines (skipped, never raised) and of encoding noise (`errors="replace"`, matching
    permission_audit.py's open() call) — a single bad byte or truncated tail line must never abort a
    dusk-parse over a multi-hour session."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict):
                yield rec


def session_id_of(path: str, first_record: dict | None = None) -> str:
    """The session id for a transcript file: the record's `sessionId` field when present, else the
    filename stem (transcripts live at `<session-uuid>.jsonl` — the transcript-query skill)."""
    if isinstance(first_record, dict):
        sid = first_record.get("sessionId")
        if isinstance(sid, str) and sid:
            return sid
    return os.path.splitext(os.path.basename(path))[0]


def content_blocks(entry: dict) -> list[dict]:
    """The `message.content` parts list for a user/assistant record, or [] when content is a plain
    string (a denial tool_result can be a bare-string user message — the transcript-query skill's
    documented trap) or the record carries no message at all."""
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    return content if isinstance(content, list) else []


def content_text(entry: dict) -> str:
    """All text in a record — plain-string content AND `{"type":"text"}` blocks — concatenated with
    newlines, for marker/pattern scanning that should not care which shape the content took."""
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    return ""


# All four fields Claude Code's usage block can carry — used only to detect PRESENCE of a usage
# block (a turn whose only populated field happens to be cache_read_input_tokens still counts as
# "usage present").
_USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                  "cache_read_input_tokens")

# The fields actually SUMMED into the returned turn-cost — see usage_tokens()'s docstring for why
# cache_read_input_tokens is deliberately excluded.
_TURN_COST_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens")


def usage_tokens(entry: dict) -> int | None:
    """This turn's own token COST: `input_tokens + output_tokens + cache_creation_input_tokens`,
    deliberately EXCLUDING `cache_read_input_tokens`. Returns None only when the record carries no
    usage block at all (checked via presence across all 4 raw fields, so a turn whose only
    populated field is cache_read_input_tokens still reports "usage present, cost 0" rather than
    None).

    WHY cache_read_input_tokens is excluded [FIX-TOKENSPIKE-SATURATION — see
    docs/intelligence-console/retrospective-test-findings-2026-07-22.md §2a]: it is the running
    CUMULATIVE size of the context Claude Code resends every turn, not a cost incurred BY that
    turn — on a long real session it climbs monotonically (the whole prior conversation gets
    resent as cache-hit context) and essentially never drops. Summing it into a "usage total"
    makes that total track session length, not turn cost: on a real 24k-record/17-day transcript,
    doing so flagged 9,305 of 9,319 usage-bearing turns (99.85%) as "spikes" — the total crossed
    the threshold by the SECOND assistant turn and stayed there, so nothing was discriminated.
    input_tokens + output_tokens + cache_creation_input_tokens is the honest per-turn delta: new
    prompt content sent this turn, tokens generated this turn, and any context newly WRITTEN into
    the cache this turn (a real one-time cost) — none of it is "the same context resent again."
    """
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    if not any(isinstance(usage.get(k), (int, float)) for k in _USAGE_FIELDS):
        return None
    total = 0
    for k in _TURN_COST_FIELDS:
        v = usage.get(k)
        if isinstance(v, (int, float)):
            total += int(v)
    return total
