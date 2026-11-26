"""Make this suite run against the IN-TREE ``composeio``.

An editable ``composeio`` install may be on the ambient path pointing at a
DIFFERENT checkout than this one. That stale tree can lag this worktree (e.g.
lack ``composeio.composeserver`` / ``composeio.composeserver_loader``), which
would make the plugin suite fail to COLLECT even though the code is correct
against the tree it ships in.

Prepending this repo's own ``python/`` to ``sys.path`` resolves ``composeio`` to
the sibling source here (the standard path finder runs ahead of the editable
finder once the directory is at the front), so the suite tests the code it lives
beside rather than whatever happens to be installed. Harmless when the two
already agree.
"""
from __future__ import annotations

__import__("os").environ["COMPOSEIO_OVERLAY_ENV"] = "none"  # no test reads a developer's live host overlay ([CXC-SCL-SCRUB-OVERLAY-PARITY])

import sys
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parents[3] / "python"
if _PYTHON_DIR.is_dir():
    p = str(_PYTHON_DIR)
    if sys.path[:1] != [p]:
        sys.path.insert(0, p)
