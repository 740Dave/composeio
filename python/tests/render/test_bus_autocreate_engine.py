"""Auto-created namespaces get a PERMISSIVE engine; declared ones keep theirs.

Phase 2a step 0 of the bus-connection collapse (2026-08-08).

THE DEFECT THIS PINS. `ComposeIOBus.ensure_namespace` — the create-on-demand path,
which fires exactly when nobody DECLARED a namespace — defaulted to a `binary`
engine accepting only 'off'/'on'. So the very first real publish blew up:

    publish_state("self/context", 0.203)
    -> ValueError: Invalid value '0.203' for binary engine. Valid: ['off','on']

That one line is why 17 of the console's 18 data sources bypassed the bus API and
hand-rolled raw paho ([DEC-BUS-ENVELOPE-RAW-PAHO], 2026-07-19) — the root of the
20-connections-from-one-process vestige. The workaround long outlived the reason:
the sources became in-process THREADS in 2026-07-22's [DEC-INPROCESS-SOURCES],
but the per-source client pattern stayed and got copy-pasted to 17 sites.

WHY `json` AND NOT AN INFERRED ENGINE — the load-bearing design note. Inferring
from the first value looks smarter and is a trap: `ContinuousEngine` is clamped to
0-255, so a namespace whose first value happened to be 42 would silently begin
REJECTING a later token count of 475000. That trades a loud startup error for an
unpredictable mid-run one, which is strictly worse and is the exact silent-failure
class this work exists to remove. The principle instead: **validation is opt-in via
DECLARATION.** Declare a namespace and you get its engine's guarantees; auto-create
one and you get a permissive engine, because nobody stated an intent to enforce.

These tests live under python/tests/render/ because that is the tree the
pre-commit gate runs for a `python/**.py` change. The natural home would be
tests/test_bus.py, but the top-level `tests/` tree is UNMAPPED by the gate
(scripts/pre-commit-tests.sh) and cannot be mapped until its 4 pre-existing
failures are fixed — mapping it today would block every commit. Noted rather than
worked around silently.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from composeio.bus import ComposeIOBus  # noqa: E402


@pytest.fixture
def bus():
    b = ComposeIOBus(bus_id="test-autocreate")
    b.start()
    yield b
    b.stop()


# The payload shapes the console's data sources actually publish. Every one of
# these raised ValueError before the fix.
@pytest.mark.parametrize("value", [
    0.203,                      # self/context — the exact value in the 07-19 report
    475000,                     # a token count: > 255, so ContinuousEngine would reject it too
    {"a": 1, "b": [2, 3]},      # self/deliverables, self/meta, self/projects ...
    "hello",                    # a free-form token
    [1, 2, 3],                  # self/memories, self/agents
    True,                       # a plain bool
    0,                          # falsy, but still a legitimate value
])
def test_autocreated_namespace_accepts_real_payloads(bus, value):
    """An UNDECLARED namespace must accept whatever the publisher sends."""
    ns = f"probe/auto_{type(value).__name__}_{abs(hash(str(value))) % 9999}"
    bus.publish_state(ns, value)          # must not raise
    assert bus.ensure_namespace(ns).value == value


def test_autocreate_default_engine_is_permissive(bus):
    """The create-on-demand engine is `json`, not `binary`."""
    ns = bus.ensure_namespace("probe/fresh")
    assert ns.state_engine.engine_type == "json", (
        f"auto-create engine regressed to {ns.state_engine.engine_type!r}; a strict "
        "engine on an UNDECLARED namespace is the 2026-07-19 defect")


def test_declared_binary_namespace_still_enforces(bus):
    """The fix must NOT weaken a namespace someone deliberately declared.

    This is the guard that keeps step 0 narrow: validation is still real, it is
    just opt-in via declaration.
    """
    bus.register_namespace("probe/declared_binary", "binary")
    with pytest.raises(ValueError):
        bus.publish_state("probe/declared_binary", 0.203)
    bus.publish_state("probe/declared_binary", "on")
    assert bus.ensure_namespace("probe/declared_binary").value == "on"


def test_explicit_register_namespace_default_unchanged(bus):
    """`register_namespace()` — EXPLICIT registration — keeps its binary default.

    Only the auto-create path moved. A caller who deliberately omits the engine
    still gets exactly what it always got, so this change cannot surprise anyone
    who was relying on the old default on purpose.
    """
    ns = bus.register_namespace("probe/explicit_default")
    assert ns.state_engine.engine_type == "binary"


def test_declared_engine_survives_a_later_ensure(bus):
    """A later `ensure_namespace()` must never downgrade a declared engine.

    ensure() only applies its engine_config when CREATING; this pins that, so the
    permissive default can never leak onto an already-declared namespace.
    """
    bus.register_namespace("probe/declared_enum", {"type": "enum",
                                                   "values": ["a", "b"]})
    again = bus.ensure_namespace("probe/declared_enum")
    assert again.state_engine.engine_type == "enum"
    with pytest.raises(ValueError):
        bus.publish_state("probe/declared_enum", "not-in-the-enum")
