"""`python -m composeio.render.widgets.media --selftest` entry point."""

from __future__ import annotations

import sys

from .selftest import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
