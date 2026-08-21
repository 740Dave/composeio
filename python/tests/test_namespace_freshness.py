"""The freshness contract a namespace DECLARES — [D4], YAML-expressible.

[DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML] ratified that each
namespace declares an expected refresh interval and that the declaration must be
EXPRESSIBLE IN YAML.  Nothing declared one: `Namespace` carried no such field, so
`MQTTTransport._refresh_intervals` — the seam built for it — was permanently
empty and every retained value took the 24 h horizon.

These tests are the contract's first consumer-facing surface.  The FIRST real
declaration in the fleet is `_bus/time` at 1 Hz (see `test_bus_clock.py`), which
is why this landed with the clock rather than before it.

THE THIRD CASE IS TESTED HERE TOO.  D4's amendment of 2026-08-14 measured the
throttle as rate-limited AND change-gated — "quiet-bus by construction" — so it
has no interval to multiply BY DESIGN.  A contract that could only express a
cadence would force that namespace to abandon a property it was built to have.
"""
from __future__ import annotations

import pytest

from composeio.namespace import Cadence, Namespace, Scope
from composeio.state_engine import StateEngine


def _ns(**kw) -> Namespace:
    return Namespace("probe/x", StateEngine.from_config("json"), **kw)


# --------------------------------------------------------------------------- #
# 1. PERIODIC — a declared cadence, and D4's 3x-convention timeout
# --------------------------------------------------------------------------- #

def test_declared_interval_makes_a_namespace_periodic():
    ns = _ns(refresh_interval=1.0)
    assert ns.cadence is Cadence.PERIODIC
    assert ns.refresh_interval == 1.0


def test_staleness_timeout_is_three_times_the_interval():
    """3x is a J1939 CONVENTION, so the code says convention, not requirement."""
    assert _ns(refresh_interval=1.0).staleness_timeout == 3.0
    assert _ns(refresh_interval=10.0).staleness_timeout == 30.0


def test_staleness_timeout_is_floored_at_interval_plus_100ms():
    """A very fast cadence must not produce a timeout inside its own jitter."""
    ns = _ns(refresh_interval=0.02)
    assert ns.staleness_timeout == pytest.approx(0.12)


def test_an_explicit_timeout_overrides_the_convention():
    ns = _ns(refresh_interval=1.0, timeout=10.0)
    assert ns.staleness_timeout == 10.0


# --------------------------------------------------------------------------- #
# 2. EVENT-DRIVEN — the third case D4's amendment found by measurement
# --------------------------------------------------------------------------- #

def test_quiet_by_construction_is_a_declaration_not_an_absence():
    """The throttle's silence at rest is a DESIGN GOAL, not a dead publisher.

    `controller_main.cpp:449-468` gates emission on a sample clock AND a
    deadband against the last EMITTED value.  Declaring that explicitly is what
    stops the freshness engine from expiring the fleet's most-watched namespace
    seconds after David stops touching it.
    """
    ns = _ns(quiet_by_construction=True)
    assert ns.cadence is Cadence.EVENT_DRIVEN
    assert ns.staleness_timeout is None      # NOT 0, NOT infinity — meaningless


def test_quiet_by_construction_rejects_a_refresh_interval():
    """Declaring both is a contradiction, and it must fail at the keyboard."""
    with pytest.raises(ValueError, match="quiet_by_construction"):
        _ns(refresh_interval=1.0, quiet_by_construction=True)


# --------------------------------------------------------------------------- #
# 3. UNDECLARED — the honest default, and it is not "fresh"
# --------------------------------------------------------------------------- #

def test_undeclared_is_its_own_case_and_has_no_timeout():
    """UNKNOWN IS NOT FRESH and it is not STALE either.

    Defaulting an undeclared namespace to some invented interval would expire
    live values; defaulting it to "never stale" would bless dead ones.  Both are
    verdicts the declaration never made, so the answer is None and the caller
    must decide what to do about not knowing.
    """
    ns = _ns()
    assert ns.cadence is Cadence.UNDECLARED
    assert ns.staleness_timeout is None
    assert ns.refresh_interval is None


def test_negative_and_zero_intervals_are_refused():
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match="refresh_interval"):
            _ns(refresh_interval=bad)


# --------------------------------------------------------------------------- #
# 4. IT SERIALISES — the contract is visible to anything reading the bus
# --------------------------------------------------------------------------- #

def test_to_dict_carries_the_declaration():
    d = _ns(refresh_interval=1.0).to_dict()
    assert d["freshness"] == {
        "cadence": "periodic",
        "refresh_interval": 1.0,
        "staleness_timeout": 3.0,
    }


def test_to_dict_omits_freshness_when_nothing_is_declared():
    """An UNDECLARED namespace must not sprout a block implying it declared."""
    assert "freshness" not in _ns().to_dict()


def test_to_dict_for_event_driven_says_so_without_inventing_a_timeout():
    d = _ns(quiet_by_construction=True).to_dict()
    assert d["freshness"]["cadence"] == "event_driven"
    assert d["freshness"]["staleness_timeout"] is None


# --------------------------------------------------------------------------- #
# 5. YAML — David's explicit requirement, not a config note
# --------------------------------------------------------------------------- #

def test_yaml_declares_the_freshness_contract(tmp_path):
    """`This should be configurable in YAML, ideally specifying the refresh
    interval, max age, timeout, etc.` — David, ratifying D4."""
    import yaml
    from composeio.bus import ComposeIOBus
    from composeio.config import apply_config, load_config

    cfg = {
        "app": {"id": "freshness-probe"},
        "namespaces": [
            {"name": "beacon/tick", "state_engine": "json",
             "freshness": {"refresh_interval": 1.0}},
            {"name": "throttle/left", "state_engine": "json",
             "freshness": {"quiet_by_construction": True}},
            {"name": "slow/thing", "state_engine": "json",
             "freshness": {"refresh_interval": 5.0, "timeout": 60.0}},
            {"name": "nothing/declared", "state_engine": "json"},
        ],
    }
    p = tmp_path / "freshness.compose.yaml"
    p.write_text(yaml.safe_dump(cfg))

    bus = ComposeIOBus(bus_id="freshness-probe")
    apply_config(bus, load_config(str(p)))

    beacon = bus.get_namespace("beacon/tick")
    assert beacon.cadence is Cadence.PERIODIC
    assert beacon.refresh_interval == 1.0
    assert beacon.staleness_timeout == 3.0

    throttle = bus.get_namespace("throttle/left")
    assert throttle.cadence is Cadence.EVENT_DRIVEN
    assert throttle.staleness_timeout is None

    slow = bus.get_namespace("slow/thing")
    assert slow.staleness_timeout == 60.0

    assert bus.get_namespace("nothing/declared").cadence is Cadence.UNDECLARED


def test_the_bus_exports_declared_intervals_for_the_transport_seam():
    """`MQTTTransport(refresh_intervals=...)` was built empty and stayed empty.

    This is the wire that fills it: the bus can hand a transport the set of
    DECLARED cadences, so a retained message's Message Expiry Interval becomes
    D4's staleness timeout instead of the 24 h horizon.
    """
    from composeio.bus import ComposeIOBus

    bus = ComposeIOBus(bus_id="seam")
    bus.register_namespace("beacon/tick", "json", refresh_interval=1.0)
    bus.register_namespace("throttle/left", "json", quiet_by_construction=True)
    bus.register_namespace("plain/thing", "json")

    intervals = bus.declared_refresh_intervals()
    assert intervals["beacon/tick"] == 1.0
    # The two INFRASTRUCTURE namespaces declare too, and their presence here is
    # the point rather than noise: `_bus/time` is the first periodic namespace in
    # the fleet, so it is the first thing whose retained copy the broker can be
    # told to expire at D4's staleness timeout (3 s) instead of the 24 h horizon.
    assert intervals["_bus/time"] == 1.0
    assert intervals["_bus/heartbeat"] == 5.0
    # An EVENT_DRIVEN namespace is deliberately ABSENT rather than present with
    # a zero/None: an expiry derived from it would delete a retained value
    # during exactly the silence it was designed to have.
    assert "throttle/left" not in intervals
    assert "plain/thing" not in intervals
