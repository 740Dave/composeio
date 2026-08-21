"""composeio.decision_ops._enginepath — make the `lts-engine` library importable.

The editable install exposes only the `factlattice` PARSER package; the ENGINE
(`factlattice_engine`) ships as its own library and lives in the DEPLOYMENT — a clean
checkout of the public lts-engine repo plus a gitignored private overlay. So we prepend
its root to `sys.path` on first use, overridable via `FACTLATTICE_ENGINE_ROOT`.

WHY THIS MODULE EXISTS (2026-07-23): `decision_ops.core` imports `factlattice_engine.lint`
at MODULE level (the lint layer moved into the library when `composeio.lts_lint` was
retired). The MCP servers set `PYTHONPATH` themselves, but a bare `pytest tests/` or a
direct `python3 -m composeio.decision_ops.selftest` does not — without this seat those
invocations die at COLLECTION with `ModuleNotFoundError`. Seating the path in the package
`__init__` (before `.core` is imported) keeps every entry point working identically.
"""
from __future__ import annotations

import os
import sys

# ⚠ RENAMED 2026-08-16: `factlattice-deployment` -> `factlattice-internal-dev`, when the
# checkout was re-pointed from the PUBLIC 740Dave/factlattice to the PRIVATE
# 740Dave/factlattice-internal-dev. The old path no longer exists. Deliberately a single
# hardcoded default and NOT a try-new-then-fall-back-to-old chain — see
# scripts/lts_property_gate.py's identical note for the reasoning. Kept as a HOME-relative
# default so it is not machine-pinned.
DEFAULT_ENGINE_ROOT = os.path.join(os.path.expanduser("~"), "factlattice-internal-dev")


def ensure_engine_on_path() -> None:
    """Idempotently put the lts-engine library root on `sys.path`."""
    root = os.environ.get("FACTLATTICE_ENGINE_ROOT", DEFAULT_ENGINE_ROOT)
    if root and root not in sys.path:
        sys.path.insert(0, root)
