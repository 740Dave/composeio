"""Fixtures for the composebus kernel-only test subset.

The copied root-tree tests (test_bus, test_compound_bindings, test_modal_priority,
test_selector, test_state_engines, test_transforms) take the `bus` and
`local_transport` fixtures from the repo-root ``tests/conftest.py``. These two
fixtures are copied from there VERBATIM with only their import lines rewritten
from composeio to composebus ([CXC-CBK-PKG-STANDALONE-PROOF] test-import-rule).
The root conftest's glass/hermetic policies are not copied: they gate
display and out-of-repo-board tests, and this subset holds neither.
"""

__import__("os").environ["COMPOSEIO_OVERLAY_ENV"] = "none"  # no test reads a developer's live host overlay ([CXC-SCL-SCRUB-OVERLAY-PARITY])

import pytest

from composebus.bus import ComposeIOBus
from composebus.transport_local import LocalTransport
from composebus.module import Module


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
