"""CLI entry: ``python -m composeio.render.composition <svg>`` runs the
composition linter ([CXC-COMP-LINT]) — the derivation in ``--check`` mode."""

from __future__ import annotations

import sys

from .lint import main

if __name__ == "__main__":
    sys.exit(main())
