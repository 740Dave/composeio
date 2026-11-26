"""Seat THIS worktree's `python/` (composeio) and `services/` on sys.path FIRST —
before any editable/site install of composeio, and before any test module
imports it.

Why this exists: the service suite runs from REPO_ROOT (`pytest
services/fingerprint_service/tests`); composeio otherwise resolves via whatever
editable/site install is active, which may be a DIFFERENT checkout that lags
this worktree and lacks newer modules (e.g. `composeio.composeserver`). The
top-level `tests/` tree dodges this because `tests/conftest.py` inserts `python/`
AND that tree is NOT a package — so nothing imports composeio before its conftest
runs. This test dir follows the SAME shape (no `__init__.py`), so THIS conftest
is a top-level conftest that runs before `fingerprint_service` (and the composeio
it pulls via `enroll.py`) is ever imported. A single, worktree-resolved composeio
results — no module purge, no duplicate-instance skew.

Insert PACKAGE dirs only, never a test dir (that shadows same-named modules and
makes imports order-dependent, per the note in tests/conftest.py).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_ROOT / "python"), str(_ROOT / "services")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
