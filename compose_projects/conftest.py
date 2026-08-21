"""Put THIS directory on ``sys.path`` for every test collected beneath it.

⛔ WHY THIS FILE EXISTS. The intelligence tree imports itself by the dotted path
``intelligence.*`` (``from intelligence.retrospective import db``), and that only
resolves when the directory CONTAINING ``intelligence/`` is on ``sys.path``. Until
2026-08-17 that directory was the repo root, which ``python -m pytest`` put there for
free (``-m`` prepends the CWD, and ``scripts/pre-commit-tests.sh`` runs the arm from
REPO_ROOT precisely for that reason — see its comment at the intelligence arm).

The reorg moved ``intelligence/`` -> ``compose_projects/intelligence/``. The invariant
is UNCHANGED — the parent of ``intelligence/`` must be importable — but the directory it
names moved one level down, so the free CWD entry stopped satisfying it and all seven
``retrospective/tests`` modules died at COLLECTION with ``No module named
'intelligence'``, taking the gate's whole second arm with them.

This is the same shim the runtime modules already apply to themselves (e.g.
``compose_projects/intelligence/tools/away_watch_publisher.py`` inserts
``REPO_ROOT / "compose_projects"``); a conftest is how the test process gets it without
each test file repeating the incantation.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECTS_ROOT = str(Path(__file__).resolve().parent)
if _PROJECTS_ROOT not in sys.path:
    sys.path.insert(0, _PROJECTS_ROOT)
