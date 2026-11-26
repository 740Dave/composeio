#!/usr/bin/env python3
"""Verify every CITATION in an audit DSD before it reaches the human.

⛔ WRITTEN BEFORE THE DSD EXISTED, deliberately (2026-08-15). A checker authored
after seeing the output it checks can be tuned — consciously or not — to pass
that output. This one was committed before the auditing subagent reported.

WHY
---
An auditing subagent produces a document whose entire value is that its claims
are CHECKABLE: it quotes the human verbatim and cites node ids. Both halves are
mechanically verifiable, so there is no excuse for forwarding an unverified one.

A hallucinating auditor is EXACTLY the failure this project spends its time on —
an instrument emitting confident, plausible, wrong output — arriving inside the
tool built to catch that. Forwarding its DSD unchecked would be accepting a
green report on faith, which is the thing being argued against.

⛔ THE LOCATION OF A HALLUCINATION IS DIAGNOSTIC — this is the point of the tool
---------------------------------------------------------------------------
  * Fabricated NODE IDs or LINE NUMBERS  -> the brief was OVER-SCOPED. The
    auditor was asked to cross-reference 184 nodes and could not hold them. That
    is the DISPATCHER'S error, and the remedy is a narrower brief (audit one
    spec at a time, or one category at a time).
  * Fabricated HUMAN QUOTES -> a far deeper failure. The human's statements were
    ~20 short items in a file the auditor was told to read. Fabricating those
    means the method itself is unsound, not merely over-scoped.

Report which, and do not average them into one "accuracy" number — averaging
destroys exactly the distinction that tells you what to fix.

WHAT IS AND IS NOT PROVEN
-------------------------
This verifies that citations RESOLVE. It CANNOT verify that a finding is correct,
fair, or worth acting on — that is judgment and belongs to a reader. A DSD that
passes here is WELL-FORMED, not RIGHT.

  [PRINCIPLE-SELF-PROVING-RECEIPT]: "a receipt covers only the DETERMINISTIC
  half… 'Is the artifact ANY GOOD?' is judgment, and a receipt is worthless
  there." This is the deterministic half and claims nothing more.

⛔ THE FALSE-POSITIVE FLOOD, AND WHY IT WAS FATAL RATHER THAN ANNOYING
----------------------------------------------------------------------
Measured 2026-08-15 against the first real audit DSD: 98 of 106 "quotes" were
flagged unverified. Not one was a fabrication. Three distinct causes, all in
this file rather than in the DSD:

  1. EVERY markdown blockquote was treated as a human quote. A DSD opens with a
     blockquote preamble in its OWN voice — "What this consolidates…", "How to
     answer…" — and none of that is the human speaking.
  2. The blockquote regex was PER LINE (`^…$` with `re.M`), so a quote wrapped
     across four markdown lines was checked as four fragments, each of which
     failed the substring test on its own. A correctly-quoted paragraph
     produced four false positives, one per line.
  3. A node the DSD RECOMMENDS CREATING was indistinguishable from a node it
     CITES. "add a `[CXC-FPID-ENTITY-DEVICE]` node" was reported as an
     unresolved citation, which is exactly backwards: it resolving would have
     been the defect.

⛔ A verifier that flags 98 non-defects is not merely noisy — it is WORSE THAN
NOTHING, because the only sustainable response to it is to stop reading the
output, and at that moment the two REAL findings hiding in the flood are lost.
The remedy is not a lower threshold. A verifier tuned until it stops complaining
is worthless. The remedy is to make the claim being checked EXPLICIT in the
document, so the check is exact instead of guessed — hence the two conventions
below, which are mechanism, not style.

⛔ THE TWO DSD CONVENTIONS THIS TOOL ENFORCES
---------------------------------------------
Both are documented identically in `.claude/skills/end-design-session/SKILL.md`.
If you change one, change the other; a convention that lives in only one of the
two places is a convention the next author will not follow.

1. A HUMAN QUOTE CARRIES AN ATTRIBUTION MARKER AND A TRANSCRIPT LINE:

       - **David said** (line 461): *"what gets loaded into a designed-into-the-game
         R503 is 10 players, but the fingerprint_storage service…"*

   Required: `**<Name> said**`, then `(line <N>)`, then the quote in double
   quotes. The name is not hardcoded — any capitalised name works. Wrapping
   across markdown lines is fine; the quote is read as one unit.

   ⛔ NOTHING ELSE IN THE DOCUMENT IS TREATED AS A HUMAN QUOTE. That is the
   point: the DSD's own framing prose is then unconstrained, and the check on
   the claims that matter becomes exact. The line number is not decoration
   either — it is checked, so a quote that is real but attributed to the wrong
   turn is caught, which a corpus-wide substring test cannot see.

   A long quoted run WITHOUT the marker is still counted and reported as
   `unattributed`, so a fabrication cannot escape simply by dropping the marker.
   `--strict` promotes that to a failure.

2. A NODE THE DSD PROPOSES CREATING IS WRITTEN `[NEW:<ID>]`:

       - **Recommendation:** add a `[NEW:CXC-FPID-ENTITY-DEVICE]` node.

   `[PROPOSED:<ID>]` is accepted as a synonym. These are NOT expected to resolve;
   a plain `[<ID>]` still is. And a `[NEW:<ID>]` that DOES already exist is its
   own finding — the DSD is proposing something that is already there.

USAGE
-----
    python3 verify_dsd_citations.py DSD.md \\
        --transcript ~/.claude/projects/.../SESSION.jsonl \\
        --spec corpus/70-r503-peripheral.lts \\
        --spec corpus/71-fingerprint-identity.lts

    # the negative control: prove it still catches a real fabrication
    python3 verify_dsd_citations.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A bracketed id: [CXC-FPID-FOO], [DD-R503-BAR], [SPEC:CXC-R503]
_NODE_CITE = re.compile(r"\[((?:SPEC:|LEDGER:|REGISTRY:)?[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\]")
# A node the DSD RECOMMENDS CREATING — convention 2 above.
_NODE_PROPOSED = re.compile(r"\[(?:NEW|PROPOSED):\s*([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\]")

#: Convention 1. `**David said** (line 461, verbatim): *"…"*`
#: DOTALL + non-greedy so a quote wrapped over markdown lines reads as one unit —
#: the per-line regex this replaced turned one good quote into four false
#: positives.
_HUMAN_QUOTE = re.compile(
    r"\*\*(?P<who>[A-Z][\w .'\-]{0,40}?)\s+said\*\*"
    r"\s*\(\s*(?:line\s*)?(?P<line>\d+)(?P<note>[^)]*)\)"
    r"\s*[:,]?\s*[*_]{0,2}[\"“](?P<quote>.+?)[\"”]",
    re.S,
)

#: Anything long and quoted that does NOT carry the marker. Counted, reported,
#: never silently ignored — see convention 1.
#:
#: ⛔ BOUNDED ON PURPOSE. A DSD uses `"` for scare-quotes in its own prose, and
#: an unbounded pairing regex then spans from one scare-quote to the next across
#: paragraphs, tables and headings — producing 89 "candidate quotes" on the first
#: real DSD, none of which was a quote. That is the same false-positive flood
#: this file exists to have fixed, reintroduced by the check that was supposed to
#: guard against hiding. A run that crosses a blank line, a table row or a
#: heading is prose, not a quotation, so it is not a candidate.
_UNATTRIBUTED_QUOTE = re.compile(r"[\"“]([^\"”|#]{60,400})[\"”]")

_MACHINERY = (
    "<task-notification>", "<system-reminder>", "<local-command-stdout>",
    "<local-command-caveat>", "<command-name>", "<command-message>",
    "[SYSTEM NOTIFICATION - NOT USER INPUT]", "[Request interrupted by user]",
    "Base directory for this skill:", "This is how Claude Code surfaces messages",
)


def _text_of(rec: dict) -> str:
    if rec.get("type") == "queue-operation":
        content = rec.get("content")
        return content if isinstance(content, str) else ""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text")
    return ""


def human_turns(transcript: Path) -> dict[int, str]:
    """{transcript line -> normalised human turn}. Keyed by line so a quote's
    cited line can be checked, not merely its presence somewhere in the file.

    ⛔ READS THE QUEUE CHANNEL TOO — `type: "queue-operation"`, `operation:
    "enqueue"`, content at the TOP level. A message the human types while the
    assistant is working may never appear as a `type: "user"` record at all;
    measured on session 80559914-…, seven of his design statements existed only
    there.

    This is not an optional nicety for a VERIFIER. When this function read only
    `user` records, the two quotes the first real audit DSD attributed to "line
    519, queued" were reported as FABRICATED. They were verbatim. A verifier
    that calls the human's own words a hallucination — inside the tool built to
    catch hallucination — is the worst possible failure mode of this file, and
    it was one missing record type away. Keep this in sync with
    `extract_design_inputs.py`; they must read the same channels or the verifier
    will contradict the extractor.

    ⛔ THE KEY IS THE 1-BASED `.jsonl` LINE NUMBER — the same number `grep -n`
    prints and the same one `extract_design_inputs.py` emits as `Statement.index`
    (its `index` INVARIANT block is THE ONE AUTHORITY; this reads by reference).
    A DSD's `(line N)` is a `grep -n` line number, so `turns` MUST be keyed the
    same way. Bare `enumerate` (0-based) keyed it low by one and made the
    verifier reject correctly-grepped citations — see `test_transcript_line_numbers.py`.
    """
    turns: dict[int, str] = {}
    for i, line in enumerate(transcript.open(errors="replace"), start=1):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = rec.get("type")
        if kind not in ("user", "queue-operation"):
            continue
        if kind == "queue-operation" and rec.get("operation") != "enqueue":
            continue
        text = _text_of(rec)
        if not text.strip():
            continue
        head = text.lstrip()[:400]
        if any(m in head for m in _MACHINERY):
            continue
        turns[i] = norm(text)
    return turns


def norm(s: str) -> str:
    """Normalise for comparison: collapse whitespace, unify quotes/dashes, casefold."""
    s = (s.replace("’", "'").replace("‘", "'")
          .replace("“", '"').replace("”", '"')
          .replace("—", "-").replace("–", "-"))
    s = re.sub(r"[*_`\\]", "", s)        # markdown emphasis + escaped emphasis
    return re.sub(r"\s+", " ", s).strip().casefold()


def spec_ids(specs: list[Path]) -> set[str]:
    ids: set[str] = set()
    for p in specs:
        for m in re.finditer(r"^#{1,6}\s+.*?\[([^\]]+)\]", p.read_text(errors="replace"), re.M):
            raw = m.group(1)
            ids.add(raw)
            ids.add(raw.split(":", 1)[-1])   # [SPEC:CXC-FPID] also answers to CXC-FPID
    return ids


def longest_run(quote: str, corpus: str) -> int:
    """Longest contiguous word-run of `quote` present verbatim in `corpus`.

    ⛔ The predecessor accepted ANY 8-word run as proof, which lets a quote that
    is 90% invention pass on a borrowed clause. The run length is returned so the
    caller can require it to cover a real FRACTION of the quote — that is what
    keeps the negative control sharp while tolerating an honestly elided middle.
    """
    words = quote.split()
    best = 0
    for i in range(len(words)):
        # extend greedily from i; stop as soon as the run leaves the corpus
        j = i + best                       # no point re-testing shorter runs
        while j < len(words):
            if " ".join(words[i:j + 1]) in corpus:
                j += 1
            else:
                break
        best = max(best, j - i)
    return best


def check_quote(quote: str, cited_line: int | None, turns: dict[int, str],
                corpus: str, min_words: int, cover: float) -> tuple[str, str]:
    """-> (verdict, detail). verdict in verified|elided|misattributed|FABRICATED."""
    qn = norm(quote)
    words = qn.split()
    if len(words) < min_words:
        return "too-short", "%d words" % len(words)

    at_line = turns.get(cited_line, "") if cited_line is not None else ""
    if qn in corpus:
        if cited_line is not None and at_line and qn not in at_line:
            return "misattributed", "verbatim in the transcript but NOT at line %d" % cited_line
        return "verified", "exact"

    run = longest_run(qn, corpus)
    need = max(min_words, int(len(words) * cover))
    if run >= need:
        if cited_line is not None and at_line and longest_run(qn, at_line) < need:
            return "misattributed", "longest run %d/%d words, but not at line %d" % (
                run, len(words), cited_line)
        return "elided", "longest verbatim run %d of %d words" % (run, len(words))
    return "FABRICATED", "longest verbatim run only %d of %d words (needed %d)" % (
        run, len(words), need)


def verify(dsd_text: str, turns: dict[int, str], known: set[str],
           min_words: int, cover: float) -> dict:
    corpus = " ".join(turns.values())

    proposed = sorted(set(_NODE_PROPOSED.findall(dsd_text)))
    # A [NEW:X] must not also be counted as a plain citation of X.
    dsd_wo_proposals = _NODE_PROPOSED.sub(" ", dsd_text)
    cited = sorted(set(_NODE_CITE.findall(dsd_wo_proposals)))
    unresolved = [c for c in cited
                  if c not in known and c.split(":", 1)[-1] not in known]
    already_exists = [p for p in proposed if p in known]

    quotes = []
    for m in _HUMAN_QUOTE.finditer(dsd_text):
        line = int(m.group("line"))
        verdict, detail = check_quote(m.group("quote"), line, turns, corpus,
                                      min_words, cover)
        quotes.append({"who": m.group("who"), "line": line,
                       "quote": m.group("quote"), "verdict": verdict,
                       "detail": detail})

    # long quoted runs with NO attribution marker — reported, never ignored
    marked_spans = [(m.start("quote"), m.end("quote")) for m in _HUMAN_QUOTE.finditer(dsd_text)]
    unattributed = []
    for m in _UNATTRIBUTED_QUOTE.finditer(dsd_text):
        s, e = m.start(1), m.end(1)
        if any(a <= s and e <= b for a, b in marked_spans):
            continue
        if "\n\n" in m.group(1):          # crossed a paragraph break: prose
            continue
        unattributed.append(m.group(1))

    return {"cited": cited, "unresolved": unresolved, "proposed": proposed,
            "proposed_already_exists": already_exists, "quotes": quotes,
            "unattributed": unattributed}


# ── the negative control ────────────────────────────────────────────────────

_SELFTEST_TRANSCRIPT = "\n".join([
    json.dumps({"type": "user", "message": {"role": "user", "content":
        "The device is the R503 fingerprint sensor, and all the specs and sw for "
        "it can be found in that directory."}}),
    json.dumps({"type": "user", "message": {"role": "user", "content":
        "<system-reminder>machinery that is not the human</system-reminder>"}}),
    json.dumps({"type": "user", "message": {"role": "user", "content":
        "what gets loaded into a designed-into-the-game R503 is 10 players, but "
        "the fingerprint_storage service needs to hold an unbounded population."}}),
]) + "\n"

_SELFTEST_SPEC = """
### A real node [CXC-FPID-WORKING-SET]
- kind: spec

### Another real node [SPEC:CXC-R503-PERIPHERAL]
- kind: spec
"""

_SELFTEST_DSD = """
# DSD — negative control

> **What this consolidates.** This blockquote is the DSD's OWN voice and must not
> be checked as a human quote. Before the convention it produced false positives
> exactly like this sentence, which is long enough to have tripped the old rule.

- **David said** (line 1): *"The device is the R503 fingerprint sensor, and all the
  specs and sw for it can be found in that directory."*   <- TRUE NEGATIVE: real, wraps 2 lines
- **David said** (line 3): *"what gets loaded into a designed-into-the-game R503 is
  10 players, but the fingerprint_storage service needs to hold an unbounded population."*
- **David said** (line 1): *"the retention window must be ninety days and every device
  shall replicate the full population to every site without exception whatsoever."*
  <- TRUE POSITIVE: fabricated, must be caught
- **David said** (line 3): *"The device is the R503 fingerprint sensor, and all the
  specs and sw for it can be found in that directory."*   <- MISATTRIBUTED: real, wrong line

Cites [CXC-FPID-WORKING-SET] and [CXC-R503-PERIPHERAL] — both real.
Cites [DD-FPID-TOTALLY-INVENTED] — TRUE POSITIVE: must be caught.
Recommends adding [NEW:CXC-FPID-ENTITY-DEVICE] — must NOT be flagged unresolved.
Recommends adding [NEW:CXC-FPID-WORKING-SET] — must be flagged as already existing.
"""


def self_test(min_words: int, cover: float) -> int:
    """⛔ THE POINT OF THIS TOOL IS THAT IT STILL COMPLAINS.

    Tuning a verifier until it goes quiet produces a green light with nothing
    behind it. So the control asserts BOTH directions: a fabricated quote and a
    fabricated node id MUST be caught, and a real quote — including one that
    wraps across markdown lines, and the document's own framing blockquote —
    MUST NOT be.
    """
    import tempfile
    import os

    fails: list[str] = []
    paths = []
    try:
        for content, suffix in ((_SELFTEST_TRANSCRIPT, ".jsonl"),
                                (_SELFTEST_SPEC, ".lts")):
            fh = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
            fh.write(content)
            fh.close()
            paths.append(Path(fh.name))
        turns = human_turns(paths[0])
        known = spec_ids([paths[1]])
        r = verify(_SELFTEST_DSD, turns, known, min_words, cover)

        verdicts = [q["verdict"] for q in r["quotes"]]
        expect = ["verified", "verified", "FABRICATED", "misattributed"]
        if verdicts != expect:
            fails.append("quote verdicts: want %r got %r" % (expect, verdicts))
        if r["unresolved"] != ["DD-FPID-TOTALLY-INVENTED"]:
            fails.append("unresolved: want ['DD-FPID-TOTALLY-INVENTED'] got %r" % r["unresolved"])
        if "CXC-FPID-ENTITY-DEVICE" not in r["proposed"]:
            fails.append("proposed node not recognised: %r" % r["proposed"])
        if "CXC-FPID-ENTITY-DEVICE" in r["unresolved"]:
            fails.append("proposed node wrongly flagged unresolved")
        if r["proposed_already_exists"] != ["CXC-FPID-WORKING-SET"]:
            fails.append("proposed-but-exists: got %r" % r["proposed_already_exists"])
        if any("What this consolidates" in u for u in r["unattributed"]):
            fails.append("the DSD's own framing blockquote was treated as a quote")
    finally:
        for p in paths:
            os.unlink(p)

    print("── NEGATIVE CONTROL ──")
    print("   TRUE POSITIVE (must catch) : fabricated quote, fabricated node id")
    print("   TRUE NEGATIVE (must pass)  : real quote wrapped over 2 lines, real node ids,")
    print("                                the DSD's own framing blockquote, a [NEW:] proposal")
    print("   EXTRA                      : a real quote attributed to the WRONG line")
    for f in fails:
        print("⛔ FAIL " + f)
    print("self-test: 7 assertions, %d failures" % len(fails))
    print("PASS" if not fails else "⛔ FAIL — do NOT trust this verifier's green until fixed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dsd", type=Path, nargs="?")
    ap.add_argument("--transcript", type=Path)
    ap.add_argument("--spec", type=Path, action="append", default=[])
    ap.add_argument("--min-quote-words", type=int, default=8)
    ap.add_argument("--quote-coverage", type=float, default=0.5,
                    help="fraction of a quote's words that must appear as one "
                         "verbatim run before an elided quote is accepted")
    ap.add_argument("--strict", action="store_true",
                    help="fail when a long quoted run carries no attribution marker")
    ap.add_argument("--self-test", action="store_true",
                    help="run the negative control; no DSD required")
    args = ap.parse_args()

    if args.self_test:
        return self_test(args.min_quote_words, args.quote_coverage)
    if args.dsd is None or args.transcript is None:
        ap.error("a DSD and --transcript are required (or use --self-test)")

    turns = human_turns(args.transcript)
    known = spec_ids(args.spec)
    r = verify(args.dsd.read_text(errors="replace"), turns, known,
               args.min_quote_words, args.quote_coverage)

    print(f"DSD        : {args.dsd}")
    print(f"transcript : {args.transcript}  ({len(turns)} human turns)")
    print(f"specs      : {len(args.spec)} file(s), {len(known)} known ids")
    print()
    print("── NODE CITATIONS ──  (fabrication here => the BRIEF was over-scoped)")
    print(f"   cited        : {len(r['cited'])}")
    print(f"   unresolved   : {len(r['unresolved'])}")
    for u in r["unresolved"]:
        print(f"     ⛔ [{u}] resolves to no heading in any supplied spec")
    print(f"   proposed NEW : {len(r['proposed'])}   (expected not to resolve)")
    for p in r["proposed_already_exists"]:
        print(f"     ⛔ [NEW:{p}] is proposed but ALREADY EXISTS")
    print()
    print("── HUMAN QUOTES ──  (fabrication here => the METHOD is unsound)")
    buckets: dict[str, int] = {}
    for q in r["quotes"]:
        buckets[q["verdict"]] = buckets.get(q["verdict"], 0) + 1
    print(f"   marked quotes: {len(r['quotes'])}  {buckets or '{}'}")
    for q in r["quotes"]:
        if q["verdict"] in ("FABRICATED", "misattributed"):
            print(f"     ⛔ {q['verdict']} ({q['detail']}) — {q['who']} line {q['line']}: "
                  f"{q['quote'][:140]!r}")
    print(f"   unattributed : {len(r['unattributed'])}  "
          f"(long quoted runs with no `**X said** (line N)` marker)")
    for u in r["unattributed"][:10]:
        print(f"     ·  {u[:120]!r}")
    print()

    bad_quotes = [q for q in r["quotes"] if q["verdict"] in ("FABRICATED", "misattributed")]
    ok = not r["unresolved"] and not bad_quotes and not r["proposed_already_exists"]
    if args.strict and r["unattributed"]:
        ok = False
    print("VERDICT: " + ("citations resolve" if ok else "⛔ UNVERIFIED CITATIONS PRESENT"))
    print()
    print("⚠ This proves the DSD is WELL-FORMED, never that it is RIGHT. Whether a")
    print("  finding is correct, fair, or worth acting on is judgment and belongs to")
    print("  a reader. Do not report a pass as though it settled the content.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
