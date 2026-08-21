"""compact_summary — deterministically extract the /compact summary from a
session `.jsonl`, so the compact-summaries corpus needs ZERO reasoning-model
tokens to capture and is mechanically FAITHFUL (no self-attested "verbatim").

Why this exists ([SPEC:CXC-IC-META] bringup step 0 / the compact-summaries
corpus, docs/compact-summaries/README.md):

The corpus README's premise — the summarizer output "lives only in the
successor's ephemeral context, then is gone" — is too pessimistic. Claude Code
PERSISTS every /compact summary in the session transcript as a record with a
top-level ``isCompactSummary: true`` (``type: "user"``), the summary text in
``message.content``. So the "one un-instrumented surface of the crossing" is in
fact mechanically recoverable. This is the deterministic tool David called for:
"pulling the CompactSummary from the .jsonl can be done by a deterministic python
script, so we don't need to waste tokens on that action." It replaces the
hand-transcription that was bringup STEP 0, and it can AUDIT the existing
hand-transcribed corpus for fidelity (``--verify``).

Externalize-the-mechanical-action principle: a summarizer's output is a fixed
byte string on disk — extracting it is a `grep`, not a reasoning task; don't
spend an Opus turn transcribing what a 30-line script copies exactly.

CLI:
    python -m composeio.compact_summary --jsonl SESSION.jsonl --list
    python -m composeio.compact_summary --jsonl SESSION.jsonl --latest --out DIR/summary.md
    python -m composeio.compact_summary --jsonl SESSION.jsonl --index 2 --raw
    python -m composeio.compact_summary --jsonl SESSION.jsonl --verify DIR/summary.md
    python -m composeio.compact_summary --selftest
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class CompactSummary:
    """One /compact summary recovered from the transcript, in file order."""
    index: int            # 0-based position among the summaries in this jsonl
    line: int             # 0-based line number in the jsonl
    timestamp: str        # the record's ISO timestamp (may be "")
    text: str             # the VERBATIM summary text


def _text_of(rec: dict) -> str:
    """The summary text out of a transcript record, tolerant of both a string
    `content` and a list of `{type:text,text:...}` blocks (either shape has been
    observed across Claude Code versions)."""
    msg = rec.get("message")
    if isinstance(msg, dict):
        c = msg.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
    for key in ("content", "summary", "text"):
        v = rec.get(key)
        if isinstance(v, str):
            return v
    return ""


def _is_compact_summary(rec: dict) -> bool:
    """The deterministic marker: a top-level (or message-level) truthy
    ``isCompactSummary``.  A `type == "summary"` record is also accepted for
    forward-compat.  NB: matching the STRING 'isCompactSummary' anywhere is
    wrong — it appears in ordinary message text (e.g. discussing this tool);
    only the BOOLEAN FIELD is authoritative."""
    if rec.get("isCompactSummary") is True:
        return True
    msg = rec.get("message")
    if isinstance(msg, dict) and msg.get("isCompactSummary") is True:
        return True
    return rec.get("type") == "summary" and bool(_text_of(rec))


def extract(jsonl_path: str) -> List[CompactSummary]:
    """All /compact summaries in *jsonl_path*, in transcript (chronological)
    order.  Pure + deterministic: same file -> same list, byte for byte."""
    out: List[CompactSummary] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f):
            line = line.strip()
            if not line or '"isCompactSummary"' not in line \
                    and '"type":"summary"' not in line.replace(" ", ""):
                # cheap pre-filter on the raw line; the JSON check below is
                # authoritative (guards against the substring-in-text confound)
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not _is_compact_summary(rec):
                continue
            out.append(CompactSummary(
                index=len(out),
                line=lineno,
                timestamp=str(rec.get("timestamp", "")),
                text=_text_of(rec),
            ))
    return out


def select(summaries: List[CompactSummary], *, index: Optional[int],
           latest: bool) -> CompactSummary:
    if not summaries:
        raise SystemExit("no compact-summary records found in the jsonl")
    if latest or index is None:
        return summaries[-1]
    try:
        return summaries[index]
    except IndexError:
        raise SystemExit(
            f"--index {index} out of range (0..{len(summaries) - 1})")


# ---- verify (audit an existing hand-transcribed summary.md) ------------------

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def similarity(jsonl_text: str, md_body: str) -> float:
    """Whitespace-normalized similarity ratio in [0,1] between the authoritative
    jsonl summary and a summary.md body — the fidelity signal for the corpus."""
    return difflib.SequenceMatcher(
        None, _norm(jsonl_text), _norm(md_body)).ratio()


def _strip_md_header(md: str) -> str:
    """Best-effort: drop a leading markdown title/quote header so the body lines
    up with the raw summary.  The summary itself reliably starts at the phrase
    Claude Code opens every /compact with."""
    m = re.search(r"This session is being continued from a previous "
                  r"conversation", md)
    if m:
        return md[m.start():]
    # fallback: everything after the first horizontal rule
    parts = re.split(r"\n---\n", md, maxsplit=1)
    return parts[1] if len(parts) == 2 else md


# ---- output -----------------------------------------------------------------

def _provenance_header(cs: CompactSummary, jsonl_path: str) -> str:
    return (
        f"# /compact summary — the OUTPUT half (mechanically extracted)\n\n"
        f"> Extracted VERBATIM from the session transcript by "
        f"`composeio.compact_summary` — NOT hand-transcribed. Source record: "
        f"`isCompactSummary` at line {cs.line} of `{jsonl_path}` "
        f"(ts {cs.timestamp or 'unknown'}). This is the mechanically faithful "
        f"capture the corpus README calls for — zero successor tokens, no "
        f"self-attested 'verbatim'.\n\n---\n\n"
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract the /compact summary from a session .jsonl "
                    "(deterministic; replaces the hand-transcription).")
    ap.add_argument("--jsonl", help="path to the session .jsonl")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--latest", action="store_true",
                   help="the most recent summary (default)")
    g.add_argument("--index", type=int,
                   help="the Nth summary (0-based, transcript order)")
    ap.add_argument("--all", action="store_true",
                    help="with --list, show every summary")
    ap.add_argument("--list", action="store_true",
                    help="list the summaries (index / line / ts / len / head)")
    ap.add_argument("--out", metavar="FILE",
                    help="write the selected summary (with a provenance header) "
                         "to FILE, e.g. a crossing's summary.md")
    ap.add_argument("--raw", action="store_true",
                    help="print ONLY the verbatim text (no header)")
    ap.add_argument("--verify", metavar="SUMMARY_MD",
                    help="audit an existing summary.md against the jsonl; "
                         "prints the fidelity ratio, exits nonzero if < 0.95")
    ap.add_argument("--selftest", action="store_true",
                    help="run the self-test and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.jsonl:
        ap.error("--jsonl is required (unless --selftest)")

    summaries = extract(args.jsonl)

    if args.list:
        print(f"{len(summaries)} compact-summary record(s) in {args.jsonl}")
        for cs in summaries:
            head = _norm(cs.text)[:80]
            print(f"  [{cs.index}] line {cs.line:>6}  {cs.timestamp:24}  "
                  f"len={len(cs.text):>6}  {head!r}")
        return 0

    if args.verify:
        cs = select(summaries, index=args.index, latest=args.latest)
        with open(args.verify, encoding="utf-8") as f:
            md = f.read()
        ratio = similarity(cs.text, _strip_md_header(md))
        ok = ratio >= 0.95
        print(f"{'OK ' if ok else 'DIFF'}  fidelity={ratio:.4f}  "
              f"(jsonl line {cs.line} vs {args.verify})")
        return 0 if ok else 1

    cs = select(summaries, index=args.index, latest=args.latest)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            if not args.raw:
                f.write(_provenance_header(cs, args.jsonl))
            f.write(cs.text)
            if not cs.text.endswith("\n"):
                f.write("\n")
        print(f"wrote summary (line {cs.line}, len {len(cs.text)}) -> {args.out}")
        return 0

    sys.stdout.write(cs.text if args.raw
                     else _provenance_header(cs, args.jsonl) + cs.text)
    return 0


# ---- selftest ---------------------------------------------------------------

def _selftest() -> int:
    import tempfile
    import os
    # a synthetic jsonl: two ordinary records (one whose TEXT contains the
    # string 'isCompactSummary' — the confound), plus two real summaries.
    lines = [
        {"type": "user", "message": {"role": "user",
         "content": "does the isCompactSummary marker matter? just text."}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "discussing isCompactSummary"}]}},
        {"type": "user", "isCompactSummary": True, "timestamp": "T1",
         "message": {"role": "user", "content": "SUMMARY ONE verbatim body."}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "text", "text": "work continues"}]}},
        {"type": "user", "isCompactSummary": True, "timestamp": "T2",
         "message": {"role": "user", "content": [
             {"type": "text", "text": "SUMMARY TWO (block form) verbatim."}]}},
    ]
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in lines:
                f.write(json.dumps(rec) + "\n")

        s = extract(path)
        assert len(s) == 2, f"(1) expected 2 summaries, got {len(s)}"
        assert s[0].text == "SUMMARY ONE verbatim body.", "(2) str-content text"
        assert s[1].text == "SUMMARY TWO (block form) verbatim.", \
            "(3) block-content text"
        assert s[0].timestamp == "T1" and s[1].line == 4, "(4) line/ts"
        # the confound record (text mentions isCompactSummary) is NOT captured
        assert all("does the isCompactSummary" not in cs.text for cs in s), \
            "(5) the string-in-text confound leaked into the results"
        # select
        assert select(s, index=None, latest=True).text.startswith("SUMMARY TWO")
        assert select(s, index=0, latest=False).text.startswith("SUMMARY ONE")
        # verify: identical body -> 1.0; different -> < 0.95
        assert similarity(s[0].text, "SUMMARY ONE verbatim body.") == 1.0, "(6)"
        assert similarity(s[0].text, "totally different bytes") < 0.95, "(7)"
        # determinism: re-extract is byte-identical
        assert [c.text for c in extract(path)] == [c.text for c in s], "(8)"
    finally:
        os.unlink(path)
    print("PASS: compact_summary selftest (8/8)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
