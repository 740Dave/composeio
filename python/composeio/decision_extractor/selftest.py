"""End-to-end self-test for the DETERMINISTIC decision-extractor.

Proves BOTH decision sources against a PLANTED fixture ``.jsonl`` with a matched
CLEAN control, so the extractor must capture each planted decision AND stay
silent on the no-decision control (no false positives).  No network, no LLM; the
timestamp line is NOT asserted on (it is real-time in normal runs).

Planted, in turn order:

  1. FREEFORM marker  — ``load-bearing decision: X`` with NO tails
        -> a node with the title populated + TODO for every missing field.
  2. FULLY-STRUCTURED marker — title + all six tails on following lines
        -> a node with every field populated, tag parsed to recoverability.
  3. COMPACT single-line marker — ``load-bearing decision(TAG): title —
        chose X over Y because Z; flips if W``
        -> parsed into the right fields, tag from the parens.
  4. CHANNEL-REPLY markers — an ``mcp__composeio-channel__reply`` tool_use whose
        ``input.text`` embeds BOTH a fully-structured and a compact marker
        -> two nodes, same grammar, source ``channel-marker``.
  5. AskUserQuestion tool_use + its recorded answer
        -> an AUTO node with decision/alternatives/chosen filled and
           rationale/recoverability TODO.
  6. CONTROL turn — no marker, no question
        -> produces NO node.

Run::  python3 -m composeio.decision_extractor --selftest
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from .extractor import DecisionExtractor, render_lts


class _Checker:
    """Tiny PASS/FAIL harness (same shape as session_sweep.selftest)."""

    def __init__(self) -> None:
        self.failures = 0

    def check(self, claim: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        line = f"  [{mark}] {claim}"
        if detail:
            line += f"  ({detail})"
        print(line)


def _fixture_lines() -> list[dict]:
    """The planted session .jsonl, in turn order."""
    ask_answer = (
        'Your questions have been answered: '
        '"Who fires /compact, and how?"="Launcher-as-actuator (the launcher '
        'types /compact on the instance\'s own directive)", '
        '"How much endpoint hardware, and how long?"="Add 1-2 minnows, 48-72h".'
        ' You can now continue with these answers in mind.')
    return [
        # -- 1. FREEFORM marker: title only, no tails -----------------------
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text":
                "Here is a note.\n"
                "- load-bearing decision: use MQTT as the native channel backend\n"
                "and then we moved on to other things."},
        ]}},

        # -- 2. FULLY-STRUCTURED marker: all tails on following lines --------
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text":
                "load-bearing decision: channel as a 10th HAL interface KIND\n"
                "chose: a tenth KIND over its own family\n"
                "because: makes 'an Intelligence is just another peripheral' "
                "literal in the type system\n"
                "flips if: David vetoes extending spec 32's closed kind-set\n"
                "tag: CONTESTED\n"
                "status: open"},
        ]}},

        # -- 3. COMPACT single-line marker ----------------------------------
        {"type": "user", "message": {"content":
            "load-bearing decision(PREFERENCE): surface the forgotten stash — "
            "chose surface it untouched over dropping it because it is not the "
            "architect's git history; flips if David confirms it is dead"}},

        # -- 4. CHANNEL-REPLY markers: an Intelligence speaking over the bus
        #       via the channel `reply` tool drops BOTH marker forms inside
        #       input.text.  These are MISSED by the text scan and must be
        #       captured here with source == channel-marker. ------------------
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Replying over the bus now."},
            {"type": "tool_use", "id": "tu_reply1",
             "name": "mcp__composeio-channel__reply",
             "input": {"to": "architect", "text":
                "Here is where we landed.\n"
                "\n"
                "load-bearing decision: render the Console with a single Skia scene\n"
                "chose: one retained Skia scene-graph over per-widget canvases\n"
                "because: one retained scene lets the compositor batch and diff "
                "in a single pass\n"
                "flips if: Skia text shaping proves too slow on the minnow GPUs\n"
                "tag: CONTESTED\n"
                "status: open\n"
                "\n"
                "And a quick one:\n"
                "load-bearing decision(PREFERENCE): ship a single ComposeApp — "
                "chose one binary over a per-surface app because it keeps the bus "
                "wiring in one process; flips if a surface must outlive the app"}},
        ]}},

        # -- 5. AskUserQuestion tool_use + its recorded answer --------------
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text":
                "I'd recommend the launcher-as-actuator route here."},
            {"type": "tool_use", "id": "tu_ask1", "name": "AskUserQuestion",
             "input": {"questions": [
                 {"question": "Who fires /compact, and how?",
                  "header": "Lifecycle firing",
                  "options": [
                      {"label": "Launcher-as-actuator",
                       "description": "the launcher owns the PTY (Recommended)"},
                      {"label": "Human-in-the-loop",
                       "description": "a human fires it"},
                      {"label": "Remote Control API",
                       "description": "unverified as programmable"},
                  ]},
                 {"question": "How much endpoint hardware, and how long?",
                  "header": "Scope",
                  "options": [
                      {"label": "fleetnode2 + benchnode, 24h",
                       "description": "two endpoints"},
                      {"label": "Add 1-2 minnows, 48-72h",
                       "description": "more coverage"},
                  ]},
             ]}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_ask1",
             "content": ask_answer},
        ]}},

        # -- 6. CONTROL: no marker, no question -> NO node ------------------
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text":
                "Now I'll run the tests and check that everything is green. "
                "This sentence mentions a decision but carries no marker."},
        ]}},
        {"type": "user", "message": {"content":
            "Thanks, that looks good. Please continue."}},
    ]


def run() -> int:
    c = _Checker()
    print("ComposeIO decision-extractor self-test "
          "(deterministic; three sources)\n")

    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "session.jsonl"
        fixture.write_text(
            "\n".join(json.dumps(e) for e in _fixture_lines()) + "\n",
            encoding="utf-8")

        decisions = DecisionExtractor(str(fixture)).extract()
        by_src = {"marker": 0, "channel-marker": 0, "askuserquestion": 0}
        for d in decisions:
            by_src[d.source] = by_src.get(d.source, 0) + 1

        # -- node counts (no false positives) -------------------------------
        # The planted AskUserQuestion carries TWO questions; each question is
        # its own fork, so it yields TWO auto nodes (not one).  7 total:
        # 3 inline markers + 2 channel-reply markers + 2 ask; the control turn
        # produces NO node.
        print("Node counts:")
        c.check("exactly 7 decisions extracted "
                "(3 markers + 2 channel-markers + 2 ask-questions; "
                "control produced NO node)",
                len(decisions) == 7, f"got {len(decisions)}")
        c.check("3 marker nodes", by_src["marker"] == 3,
                f"got {by_src['marker']}")
        c.check("2 channel-marker nodes (both marker forms in one reply)",
                by_src["channel-marker"] == 2, f"got {by_src['channel-marker']}")
        c.check("2 askuserquestion nodes (one per question in the fork)",
                by_src["askuserquestion"] == 2, f"got {by_src['askuserquestion']}")

        # index by source + a keyword for precise assertions.
        def find(pred) -> object:
            return next((d for d in decisions if pred(d)), None)

        # -- 1. FREEFORM marker: title present, everything else TODO --------
        print("\nS1 — freeform marker (title only -> TODO placeholders):")
        free = find(lambda d: d.source == "marker" and d.decision
                    and "MQTT" in d.decision)
        c.check("title captured", free is not None
                and "MQTT" in (free.decision or ""),
                free.decision if free else "MISSING")
        if free:
            c.check("missing 'chosen' is literal TODO", free.chosen is None)
            c.check("missing 'alternatives' is literal TODO",
                    free.alternatives is None)
            c.check("missing 'rationale' is literal TODO", free.rationale is None)
            c.check("missing 'invalidating' is literal TODO",
                    free.invalidating is None)
            c.check("recoverability defaults to UNTRIAGED",
                    free.tag == "UNTRIAGED", free.tag)
            c.check("status defaults to draft", free.status == "draft",
                    free.status)

        # -- 2. FULLY-STRUCTURED marker: every field populated --------------
        print("\nS2 — fully-structured marker (all tails -> all fields):")
        full = find(lambda d: d.source == "marker" and d.decision
                    and "10th HAL" in d.decision)
        c.check("title captured", full is not None,
                full.decision if full else "MISSING")
        if full:
            c.check("chosen parsed from 'chose: X over Y'",
                    full.chosen == "a tenth KIND", full.chosen)
            c.check("alternatives parsed ('over Y')",
                    full.alternatives == "its own family", full.alternatives)
            c.check("rationale captured",
                    "literal in the type system" in (full.rationale or ""),
                    full.rationale)
            c.check("invalidating captured from 'flips if:'",
                    "David vetoes" in (full.invalidating or ""),
                    full.invalidating)
            c.check("tag parsed to recoverability CONTESTED",
                    full.tag == "CONTESTED", full.tag)
            c.check("status parsed 'open'", full.status == "open", full.status)

        # -- 3. COMPACT single-line marker ----------------------------------
        print("\nS3 — compact single-line marker:")
        comp = find(lambda d: d.source == "marker" and d.decision
                    and "forgotten stash" in d.decision)
        c.check("title parsed (before 'chose')", comp is not None,
                comp.decision if comp else "MISSING")
        if comp:
            c.check("tag parsed from parens -> PREFERENCE",
                    comp.tag == "PREFERENCE", comp.tag)
            c.check("chosen parsed", "surface it untouched" in (comp.chosen or ""),
                    comp.chosen)
            c.check("alternatives parsed ('over ...')",
                    "dropping it" in (comp.alternatives or ""), comp.alternatives)
            c.check("rationale parsed ('because ...')",
                    "not the architect's" in (comp.rationale or ""),
                    comp.rationale)
            c.check("invalidating parsed ('flips if ...')",
                    "David confirms it is dead" in (comp.invalidating or ""),
                    comp.invalidating)

        # -- 4. CHANNEL-REPLY markers (same grammar, over the bus) ----------
        print("\nS4 — channel-reply markers (source=channel-marker):")
        cstruct = find(lambda d: d.source == "channel-marker" and d.decision
                       and "Skia" in d.decision)
        c.check("fully-structured channel marker captured",
                cstruct is not None,
                cstruct.decision if cstruct else "MISSING")
        if cstruct:
            c.check("chosen parsed from 'chose: X over Y'",
                    cstruct.chosen == "one retained Skia scene-graph",
                    cstruct.chosen)
            c.check("alternatives parsed ('over Y')",
                    cstruct.alternatives == "per-widget canvases",
                    cstruct.alternatives)
            c.check("rationale captured",
                    "batch and diff" in (cstruct.rationale or ""),
                    cstruct.rationale)
            c.check("invalidating captured from 'flips if:'",
                    "minnow GPUs" in (cstruct.invalidating or ""),
                    cstruct.invalidating)
            c.check("tag parsed to recoverability CONTESTED",
                    cstruct.tag == "CONTESTED", cstruct.tag)
            c.check("status parsed 'open'", cstruct.status == "open",
                    cstruct.status)
        ccomp = find(lambda d: d.source == "channel-marker" and d.decision
                     and "single ComposeApp" in d.decision)
        c.check("compact channel marker captured", ccomp is not None,
                ccomp.decision if ccomp else "MISSING")
        if ccomp:
            c.check("tag parsed from parens -> PREFERENCE",
                    ccomp.tag == "PREFERENCE", ccomp.tag)
            c.check("chosen parsed", "one binary" in (ccomp.chosen or ""),
                    ccomp.chosen)
            c.check("alternatives parsed ('over ...')",
                    "per-surface app" in (ccomp.alternatives or ""),
                    ccomp.alternatives)
            c.check("rationale parsed ('because ...')",
                    "bus wiring in one process" in (ccomp.rationale or ""),
                    ccomp.rationale)
            c.check("invalidating parsed ('flips if ...')",
                    "outlive the app" in (ccomp.invalidating or ""),
                    ccomp.invalidating)

        # -- 5. AskUserQuestion auto node -----------------------------------
        print("\nS5 — AskUserQuestion fork (auto, first question):")
        ask = find(lambda d: d.source == "askuserquestion")
        c.check("decision = the question text", ask is not None
                and ask.decision == "Who fires /compact, and how?",
                ask.decision if ask else "MISSING")
        if ask:
            c.check("alternatives = the option labels",
                    "Launcher-as-actuator" in (ask.alternatives or "")
                    and "Human-in-the-loop" in (ask.alternatives or ""),
                    ask.alternatives)
            c.check("chosen = the recorded answer",
                    "Launcher-as-actuator" in (ask.chosen or ""), ask.chosen)
            c.check("rationale seeded but marked TODO to confirm",
                    "TODO: confirm rationale" in (ask.rationale or ""),
                    ask.rationale)
            c.check("recoverability UNTRIAGED (TODO for auto forks)",
                    ask.tag == "UNTRIAGED", ask.tag)

        # -- 6. CONTROL produced no node ------------------------------------
        print("\nS6 — control (no marker, no question):")
        c.check("no node mentions the control sentence's topic",
                not any("run the tests" in (d.decision or "")
                        for d in decisions),
                "no false positive")

        # -- 7. render_lts: draft is well-formed + TODOs land literally -----
        print("\nS7 — draft .lts render:")
        draft = render_lts(decisions, source="session.jsonl", today="2026-07-19")
        c.check("frontmatter present + DRAFT status",
                draft.startswith("---") and "DRAFT — auto-extracted" in draft)
        c.check("one node per decision (7 [CXC-ICD-D*] headings)",
                draft.count("### [CXC-ICD-D") == 7,
                f"count={draft.count('### [CXC-ICD-D')}")
        c.check("source tags present (marker + channel-marker + "
                "askuserquestion comments)",
                "source: marker" in draft and "source: channel-marker" in draft
                and "source: askuserquestion" in draft)
        c.check("missing fields render as literal TODO bullets",
                "- chosen: TODO" in draft)
        c.check("Relationships block + Status footer present",
                "## Relationships" in draft and "**Status**:" in draft)
        c.check("DOCUMENTS edge to the Console spec present",
                "DOCUMENTS [SPEC:CXC-INTELLIGENCE-CONSOLE]" in draft)

        # print the per-source tally + the first node for the record.
        print("\n  per-source decision counts on the fixture:")
        for src in sorted(by_src):
            print(f"    {src:<18} {by_src[src]}")
        print("\n  first draft node:")
        first = draft.split("### [CXC-ICD-D", 1)[1]
        first = "### [CXC-ICD-D" + first.split("\n---", 1)[0]
        for ln in first.strip().splitlines():
            print(f"    {ln}")

    print()
    if c.failures:
        print(f"SELFTEST: FAIL — {c.failures} check(s) failed")
        return 1
    print("SELFTEST: PASS — all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(run())
