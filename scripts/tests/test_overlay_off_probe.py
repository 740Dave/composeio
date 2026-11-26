"""Probe: a test directory with no conftest of its own still runs with the host overlay OFF.

scripts/tests has no conftest.py; only the repo-root conftest covers it. If that file is
missing or loses its guard, this fails, instead of the suite quietly reading a developer's
live ~/.config/composeio/live.env ([CXC-SCL-SCRUB-OVERLAY-PARITY], review P2-3).
"""
import os
import pathlib


def test_this_directory_has_no_conftest_of_its_own():
    # the probe is only meaningful while scripts/tests relies on the root conftest
    assert not (pathlib.Path(__file__).parent / "conftest.py").exists()


def test_the_overlay_is_off_here():
    assert os.environ.get("COMPOSEIO_OVERLAY_ENV") == "none"
