"""``python3 -m composeio.hal`` entry point.

    python3 -m composeio.hal --selftest    run the host-slice self-test

The self-test proves the first slice end-to-end against a MOCK/host bus, with
no hardware/firmware ([SPEC:CXC-HAL-DECLARE] §8).
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
