"""The HERMETIC-LAND-GATE POLICY — the land gate reads only THIS repo, board-QA is opt-in.

⛔ WHY THIS EXISTS, MEASURED 2026-08-28.  `tests/test_decision_ops_beepout.py`
derives its assertions from David's REAL fabricated boards, which live OUTSIDE
this repo at ``${MAKER_HOME}/pcb`` (`GAUGE`, `BUTTON`,
`LEGO`).  One of them — ``lego_dots.kicad_pcb`` — was respun/bodged after the
suite was authored: the spec pinned its geometry coincidence at ``92/92`` and the
live file is now ``94/121`` (0.777 < 0.9).  So
``test_geometry_agrees_with_copper_written_by_a_different_code_path[path2]``
FAILS on pristine ``origin/main`` with NO delta, and because
``scripts/pre-commit-tests.sh`` routes a ``services/**`` change through the whole
top-level ``tests/`` tree (``services/*.py) run_toplevel=1``), that one failure
BLOCKED every composeio land that touched python — including three unrelated
Wave-2b plugins.

⭐ THE GENERALISATION, the SAME shape ``_glass_policy.py`` names for glass:
A TEST WHOSE OUTCOME DEPENDS ON STATE NO DELTA CONTROLS IS NOT A DELTA-REGRESSION
CHECK — it is a property of the environment it read.  ``_glass_policy`` lifted the
"does a window open" property off the invocation path to the seam pytest crosses;
this lifts the dual property — "does an OUT-OF-REPO file happen to be in a
particular state" — off the land gate.  A beep-out sheet derived from a fabricated
board is a genuine and valuable falsification instrument, but it is BOARD-QA: its
subject is David's board, which is HIS to bodge or respin, and a fabricated board
changing must never block a composeio CODE land.

⛔ AND THE POINT THAT KEEPS THIS FROM DELETING A REAL CHECK (David, 2026-08-28):

    "the lego_dots board is already fabricated — it is what it is; its geometry
     is MY concern.  It must NOT block composeio code lands."

He is right, and — exactly as with glass — the answer is not suppression, it is
SCOPE.  The board-derived checks are the only thing that proves the deriver READS
the board (``[CXC-BPO-NEGATIVE-CONTROL]``), so a policy of "always delete them"
would quietly remove the negative control.  So they are OPT-IN: skipped in the
default (land-gate) invocation, run in a BOARD-QA pass that announces itself::

    COMPOSEIO_BOARD_QA=1 pytest tests -m board_qa

HOW TO USE IT
-------------
Default (any invocation, any suite) — the land gate, hermetic, no out-of-repo reads::

    pytest tests
    scripts/pre-commit-tests.sh          # routes services/** here; now GREEN

A test whose outcome is a function of a live board OUTSIDE this repo::

    @pytest.mark.board_qa
    @pytest.mark.parametrize("path", [GAUGE, BUTTON, LEGO])
    def test_geometry_agrees_with_copper(path):
        ...

Such a test is SKIPPED unless board-QA is explicitly enabled::

    COMPOSEIO_BOARD_QA=1 pytest tests -m board_qa

⛔ AUTO-MARK, so a NEW board-reading test cannot forget the marker and re-arm the
gate: any test that requests a fixture which parses an out-of-repo board (the
``gauge_board`` / ``gauge_sheet`` fixtures) is marked ``board_qa`` for free.  The
three tests that parametrise the raw board PATHS carry the marker explicitly,
because they request no such fixture.
"""

from __future__ import annotations

import os
import sys

#: The env var the caller sets to opt IN to the out-of-repo board-QA checks.
BOARD_QA_ENV = "COMPOSEIO_BOARD_QA"

#: The marker a test carries when its outcome depends on a live board OUTSIDE the repo.
BOARD_QA_MARKER = "board_qa"

#: Fixtures that parse a board from the out-of-repo pcb tree. A test that requests any
#: of these is board-QA by construction, whether or not it remembered the marker.
_BOARD_FIXTURES = frozenset({"gauge_board", "gauge_sheet"})


def board_qa_enabled() -> bool:
    """True when the caller has explicitly opted in to the out-of-repo board checks."""
    return os.environ.get(BOARD_QA_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def register(config) -> None:
    """Register the marker. Call from conftest."""
    config.addinivalue_line(
        "markers",
        f"{BOARD_QA_MARKER}: this test's outcome depends on a live board OUTSIDE this "
        f"repo (David's pcb/ tree). It is BOARD-QA, not a delta-regression check, so it "
        f"is SKIPPED in the land gate and runs only under {BOARD_QA_ENV}=1.",
    )


def announce_and_gate(config, items) -> None:
    """Auto-mark board-fixture users, then skip every board-QA test unless opted in.

    Mirrors ``_glass_policy.announce_and_gate``: the board-QA tests are still COLLECTED
    (so ``-m board_qa`` can select them and ``--collect-only`` lists them) but carry a
    skip marker in the default invocation, so a land-gate run of the top-level tree is
    GREEN without ever reading the out-of-repo pcb tree.
    """
    import pytest

    # Auto-mark: any test that pulls in an out-of-repo board fixture IS board-QA, even
    # if its author forgot the marker. This is what keeps the gate from silently
    # re-arming when a new board-reading test lands beside the ones we marked by hand.
    for it in items:
        if _BOARD_FIXTURES & set(getattr(it, "fixturenames", ())):
            it.add_marker(getattr(pytest.mark, BOARD_QA_MARKER))

    board_items = [it for it in items if it.get_closest_marker(BOARD_QA_MARKER)]
    if not board_items:
        return

    if not board_qa_enabled():
        skip = pytest.mark.skip(
            reason=f"board-QA: reads a live board OUTSIDE this repo; set "
                   f"{BOARD_QA_ENV}=1 to run (the land gate is hermetic by default)"
        )
        for it in board_items:
            it.add_marker(skip)
        print(f"[hermetic-policy] {len(board_items)} board-QA test(s) SKIPPED — the land "
              f"gate reads only this repo. {BOARD_QA_ENV}=1 to run them.", file=sys.stderr)
        return

    print(f"\n[hermetic-policy] ⛔ {BOARD_QA_ENV}=1 — {len(board_items)} board-QA test(s) "
          f"WILL read David's live pcb/ tree (outside this repo):", file=sys.stderr)
    for it in board_items:
        print(f"[hermetic-policy]   · {it.nodeid}", file=sys.stderr)
    print("[hermetic-policy] (a fabricated board changing can make these fail — that is a "
          "BOARD finding, not a code regression)\n", file=sys.stderr)
