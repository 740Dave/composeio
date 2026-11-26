"""Repo-root conftest: no test anywhere reads a developer's live host overlay.

[CXC-SCL-SCRUB-OVERLAY-PARITY]. Several test directories have no conftest of their own
(scripts/tests, examples/fingerprint, services/test_concurrency.py). `import composeio` fills
unset names from ~/.config/composeio/live.env unless COMPOSEIO_OVERLAY_ENV is `none`, so this
file forces it for every test pytest collects beneath the repo root, and for their children.
Every other conftest repeats the same guard, because a suite run from inside python/ may not
load this file. test_overlay.py enforces both.
"""
__import__("os").environ["COMPOSEIO_OVERLAY_ENV"] = "none"  # no test reads a developer's live host overlay ([CXC-SCL-SCRUB-OVERLAY-PARITY])
