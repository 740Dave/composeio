"""Seat the repo's OWN ``python/`` package dir at the front of sys.path.

The pre-commit services-suite arm runs ``pytest services/storage_monitor/tests``
plain from the repo root, with no PYTHONPATH. In that mode ``composeio`` can
resolve to a DIFFERENT checkout already seated on sys.path (observed: a shared
checkout that predates ``composeio.composeserver``), so a test importing the
newer kernel modules fails to collect while the code under test is correct.

Mirror ``tests/conftest.py`` / ``python/tests/conftest.py``: insert THIS repo's
``python/`` at position 0 so ``composeio`` (incl. ``composeserver`` /
``composeserver_loader`` / ``transport_mcp`` / ``plugin``) resolves to the code
being committed. Idempotent and scoped to this suite — it touches only sys.path
ordering and no shared file.
"""
from __future__ import annotations

import sys
from pathlib import Path

# services/storage_monitor/tests/conftest.py -> repo_root/python
_PYTHON = Path(__file__).resolve().parents[3] / "python"
if _PYTHON.is_dir():
    p = str(_PYTHON)
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)
