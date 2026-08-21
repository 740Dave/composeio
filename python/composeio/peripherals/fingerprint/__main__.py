"""CLI entry: ``python -m composeio.peripherals.fingerprint [--selftest]``."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="composeio.peripherals.fingerprint",
        description="R503 fingerprint reference layer (mock HAL + device twin).",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run the hardware-free acceptance suite (mock HAL, device twin)",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        from .selftest import run

        return run()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
