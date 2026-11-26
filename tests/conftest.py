"""Shared test fixtures for ComposeIO tests."""

import sys
from pathlib import Path
import pytest

# Add python package to path
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

# ⛔ THE GLASS POLICY — headless by DEFAULT, glass by ANNOUNCED OPT-IN.
# Applied HERE, at the seam pytest crosses however it is invoked, because the
# same guard living only in `scripts/pre-commit-tests.sh` meant a commit was
# headless and a bare `pytest` drew on the developer's live desktop. See
# tests/_glass_policy.py for the measured incident and the reasoning.
# ⛔ Loaded by explicit path, NEVER via sys.path — see the note in
# python/tests/conftest.py: inserting a test directory onto sys.path shadows
# same-named modules for the code under test and makes imports order-dependent.
def _load_sibling_policy(filename, mod_name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        mod_name, Path(__file__).parent / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_glass = _load_sibling_policy("_glass_policy.py", "_composeio_glass_policy")
_glass_register = _glass.register
_glass_gate = _glass.announce_and_gate

# ⛔ THE HERMETIC-LAND-GATE POLICY — the dual of the glass policy: glass lifts the
# "does a window open" property off the invocation path; this lifts the "does an
# OUT-OF-REPO board happen to be in a particular state" property off the LAND GATE.
# See tests/_hermetic_policy.py for the measured incident (a respun lego_dots board
# failing test_geometry_agrees on pristine main, blocking three unrelated lands).
_hermetic = _load_sibling_policy("_hermetic_policy.py", "_composeio_hermetic_policy")
_hermetic_register = _hermetic.register
_hermetic_gate = _hermetic.announce_and_gate


def pytest_configure(config):
    _glass_register(config)
    _hermetic_register(config)


def pytest_collection_modifyitems(config, items):
    _glass_gate(config, items)
    _hermetic_gate(config, items)

from composeio.bus import ComposeIOBus
from composeio.transport_local import LocalTransport
from composeio.module import Module


@pytest.fixture
def bus():
    """Create a fresh bus with local transport for testing."""
    b = ComposeIOBus(bus_id="test-bus")
    b.start()
    yield b
    b.stop()


@pytest.fixture
def local_transport():
    """Create a standalone local transport."""
    t = LocalTransport()
    t.start()
    yield t
    t.stop()
