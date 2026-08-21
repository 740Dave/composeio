"""composeio.session_sweep — a DETERMINISTIC end-of-session loose-ends linter.

Sibling to :mod:`composeio.self_sensor`.  The self-sensor reports *how much
fuel is left*; the sweep reports *what got left on the floor* — work both
parties started and dropped.  It runs at dusk, BEFORE the handoff / ``/compact``
is authored, so real findings can be folded into the bequest and raised for the
human.  No LLM, no network: it SURFACES candidates with evidence + an honest
confidence grade (HIGH only for git-verifiable truth); it does not decide.

    SessionSweep    parse a session .jsonl, run the detectors, return a report
    SweepReport     ranked findings + to_json / render / to_bus_lines
    GitProbe        self-contained, read-only, offline git query surface

Readouts::

    python3 -m composeio.session_sweep                     # default session file
    python3 -m composeio.session_sweep path/to/session.jsonl
    python3 -m composeio.session_sweep path/to/session.jsonl --repo /path/to/repo
    python3 -m composeio.session_sweep path/to/session.jsonl --json
    python3 -m composeio.session_sweep --selftest
"""
from __future__ import annotations

from .sensor import (
    CONFIDENCE_RANK,
    GitProbe,
    SessionSweep,
    SweepReport,
)

__all__ = [
    "SessionSweep",
    "SweepReport",
    "GitProbe",
    "CONFIDENCE_RANK",
]
