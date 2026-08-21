"""`python -m composeio.adaptors.wled` entry point.

  --selftest   run the fully-software self-test (no real WLED needed)
  --describe   print the default semantic->WLED namespace mapping
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if "--describe" in argv:
        from composeio.adaptors.wled import WLEDMapping
        m = WLEDMapping()
        print("WLED DRIVEN adaptor — default semantic -> WLED mapping:")
        for ns in m.namespaces():
            print(f"  {ns:20s} kind={m.rules[ns].get('kind')}")
        return 0

    if "--selftest" in argv or not argv:
        from composeio.adaptors.wled.selftest import run
        return 0 if run() else 1

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
