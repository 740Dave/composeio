"""CLI entry: ``python -m composeio.peripherals.nfc [--selftest]``."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="composeio.peripherals.nfc",
        description="PN532 NFC input peripheral for ComposeUX.",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="run the hardware-free protocol self-test (mock I2C transport)",
    )
    args = parser.parse_args(argv)

    if args.selftest:
        from .selftest import run
        return run()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
