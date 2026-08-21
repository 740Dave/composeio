"""``continuous_0_100`` — the PERCENTAGE state engine, and the migration hazard
that makes its declaration load-bearing.

David 2026-08-15: "The C3 should only be publishing throttle percentages, not
0_255."  This is the engine that statement needs.

⛔ WHY THIS FILE EXISTS AND NOT JUST A REGISTRY ENTRY.  ``continuous_0_100`` was
ALREADY HALF-LANDED before this work: the reactive-core firmware's
``out_max_for_engine()`` has returned 100 for it since the controller tier was
written, and ``compose_projects/bsg/spec/00-bsg-game-design.smd`` already names
it in a namespace declaration.  Neither Python registry had it, so a scene
declaring it raised on load.  A name that exists in two places and works in
none is worse than an absent one — it reads as supported.

The tests below pin the two facts that actually bite: the domain REFUSES
out-of-domain values (which is the entire point of declaring an engine), and
0..100 is a SUBSET of 0..255 (which is why the wrong migration order fails
SILENTLY rather than loudly).
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.config import _VALID_BUILTIN_ENGINES
from composeio.state_engine import (
    ContinuousEngine, ContinuousPercentEngine, StateEngine,
)


def test_percent_engine_is_registered_everywhere_a_name_is_resolved():
    """A state engine is only real if EVERY registry that resolves a name knows
    it.  ``continuous_0_100`` was previously known to the C++ lowering and to a
    spec, and to neither Python registry — so it validated in one tier and
    raised in another."""
    assert StateEngine.from_config("continuous_0_100").engine_type == \
        "continuous_0_100"
    assert "continuous_0_100" in _VALID_BUILTIN_ENGINES


def test_percent_domain_is_enforced_not_coerced():
    """The domain is the guarantee.  An out-of-domain value RAISES — it is not
    clamped, because a silently clamped 255 is indistinguishable from a lever
    genuinely at its stop."""
    e = StateEngine.from_config("continuous_0_100")
    for ok in (0, 1, 50, 99.5, 100):
        e.set(ok)
        assert e.current == ok
    for bad in (101, 128, 200, 255, -1):
        with pytest.raises(ValueError):
            e.set(bad)


def test_percent_engine_shares_one_implementation_with_0_255():
    """Two domains, ONE implementation.  A copied engine class is a fork whose
    halves drift; the shared base is what stops `increment`'s clamp from being
    right on one engine and wrong on the other."""
    assert issubclass(ContinuousPercentEngine, ContinuousEngine.__bases__[0])
    pct = ContinuousPercentEngine()
    pct.set(98)
    assert pct.increment(10) == 100          # clamps at ITS full scale
    assert pct.decrement(500) == 0
    wide = ContinuousEngine()
    wide.set(250)
    assert wide.increment(10) == 255         # ...and the other at ITS own


def test_a_percentage_is_VALID_on_a_0_255_engine_which_is_the_whole_hazard():
    """⛔ THE MIGRATION HAZARD, PINNED AS A TEST.

    0..100 is a strict SUBSET of 0..255.  So a firmware that has been flashed to
    publish percentages, into namespaces whose YAML still declares
    ``continuous_0_255``, produces NO error anywhere: every value validates, and
    every gauge renders it at roughly four tenths of the reading it should show.
    That is a wrong number that looks exactly like a failing pot.

    The reverse order is not loud either — see
    ``test_pre_migration_firmware_FREEZES_a_percent_namespace``.  Because
    NEITHER ordering announces itself, the firmware flash and the YAML push are
    a single atomic step, and this test exists so that nobody has to rediscover
    why.
    """
    wide = StateEngine.from_config("continuous_0_255")
    for pct in (0, 25, 50, 75, 100):
        wide.set(pct)                        # accepted, no diagnostic at all
        assert wide.current == pct
        rendered_fraction = pct / 255.0
        assert rendered_fraction <= 0.393     # ...and reads ~39% at full lever


def test_pre_migration_firmware_FREEZES_a_percent_namespace():
    """The other wrong order, also pinned.

    YAML pushed first, firmware not yet flashed: the C3 still sends 0..255 into
    a ``continuous_0_100`` namespace.  Values <= 100 are accepted; everything
    above is REJECTED — and the bus's external-receive path logs a warning and
    DROPS the message, so the namespace keeps its previous value.

    The glass therefore FREEZES partway up the travel.  That signature is
    already in the field from a different cause (a dead publisher with no
    freshness expiry), so it is very likely to be misread.  Naming it here is
    the cheapest way to make the next person's diagnosis fast.
    """
    e = StateEngine.from_config("continuous_0_100")
    e.set(60)
    accepted, rejected = [], []
    for v in (0, 40, 100, 101, 180, 255):
        try:
            e.set(v)
            accepted.append(v)
        except ValueError:
            rejected.append(v)
    assert accepted == [0, 40, 100]
    assert rejected == [101, 180, 255]
    assert e.current == 100, "the namespace holds its last valid value — FROZEN"
