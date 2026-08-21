"""
Entry point for ``python -m composeio.audio``.

    python -m composeio.audio --selftest    run the machine-green self-test
"""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv or not argv:
        from .selftest import run
        return run()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
