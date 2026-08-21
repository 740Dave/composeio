"""composeio.decision_extractor — a DETERMINISTIC draft-authoring tool.

Sibling to :mod:`composeio.session_sweep`.  The sweep reports *what got left on
the floor*; the extractor reports *what got DECIDED* — it reads a Claude Code
session ``.jsonl`` and emits a DRAFT LTS decision-provenance document (spec-35
shaped) pre-populated from the decisions found in the transcript, so the
architect REFINES a draft instead of authoring the ledger from scratch.  No LLM,
no network: it captures decisions from two sources — explicit inline
``load-bearing decision:`` markers and AskUserQuestion forks — and leaves every
field it cannot derive from the bytes as a literal ``TODO``.

    DecisionExtractor   parse a session .jsonl, extract decisions, render draft
    Decision            one extracted decision (source: marker | askuserquestion)
    render_lts          decisions -> a draft spec-35-shaped .lts string
    tail                poll a live file, re-emit the draft as decisions accrue

Readouts::

    python3 -m composeio.decision_extractor path/to/session.jsonl
    python3 -m composeio.decision_extractor path/to/session.jsonl --out draft.lts
    python3 -m composeio.decision_extractor path/to/session.jsonl --tail
    python3 -m composeio.decision_extractor --selftest
"""
from __future__ import annotations

from .extractor import (
    DRAFT_SPEC_ID,
    Decision,
    DecisionExtractor,
    render_lts,
    tail,
)

__all__ = [
    "DecisionExtractor",
    "Decision",
    "render_lts",
    "tail",
    "DRAFT_SPEC_ID",
]
