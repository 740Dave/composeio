"""The RECEIVER half of the freshness contract — [SPEC:CXC-FRESHNESS].

[DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML] ratified a receiver-side
age counter on a MONOTONIC clock, expiring LAZILY ON READ.
[DEC-UNKNOWN-IS-A-TYPE-TAG-NOT-A-RESERVED-VALUE] ratified the state it expires
INTO: `unknown`, wire tag 0xFD, which is the SAME receiver state a publisher
reaches by sending an explicit 0xFD.

`test_namespace_freshness.py` covers the DECLARATION and passes today.  Nothing
consumed it: no age was ever counted, nothing ever expired, and David's Dradis
read `contacts: 3` for hours after the publisher died.

⛔ THE NEGATIVE CONTROL IS THE POINT.  A freshness engine that never marks
anything stale passes every "is it fresh?" test ever written.  So every timeout
here is asserted from BOTH sides — stale AT the timeout, and emphatically NOT
stale one tick before it — and the quiet-by-construction class is asserted to
survive silence that would kill a periodic namespace ten times over.
"""
from __future__ import annotations

import pytest

from composeio.bus import ComposeIOBus
from composeio.freshness import UNKNOWN, Verdict, is_unknown
from composeio.namespace import Cadence, Namespace
from composeio.state_engine import StateEngine


class FakeClock:
    """A monotonic clock we drive by hand.

    MONOTONIC, not wall-clock: D4 rejected wall-clock timestamps because the
    fleet has no shared time base.  A test that advanced `time.time()` would be
    testing a mechanism the decision explicitly refused.
    """

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _ns(clock, **kw) -> Namespace:
    return Namespace("probe/x", StateEngine.from_config("json"), clock=clock, **kw)


# --------------------------------------------------------------------------- #
# 1. PERIODIC — it expires, and it expires AT the timeout and not before
# --------------------------------------------------------------------------- #

def test_a_periodic_value_is_fresh_immediately_after_receipt():
    clock = FakeClock()
    ns = _ns(clock, refresh_interval=1.0)
    ns.publish_state(3)
    assert ns.value == 3
    assert ns.verdict is Verdict.FRESH


def test_a_periodic_value_goes_unknown_at_three_times_the_interval():
    """THE DEFECT, in one assertion.  `dradis/contacts` read 3 forever."""
    clock = FakeClock()
    ns = _ns(clock, refresh_interval=1.0)
    ns.publish_state(3)

    clock.advance(3.0)                     # exactly the staleness timeout
    assert ns.value is UNKNOWN
    assert is_unknown(ns.value)
    assert ns.verdict is Verdict.STALE


def test_a_periodic_value_is_NOT_unknown_one_tick_before_the_timeout():
    """⛔ THE NEGATIVE CONTROL.  Too-eager staleness is the same defect inverted:
    an instrument that marks everything stale is as useless as one that marks
    nothing stale, and it passes exactly the same "did it go unknown?" test."""
    clock = FakeClock()
    ns = _ns(clock, refresh_interval=1.0)
    ns.publish_state(3)

    clock.advance(2.999)
    assert ns.value == 3, "expired INSIDE its declared window"
    assert ns.verdict is Verdict.FRESH


def test_a_republish_of_the_SAME_value_refreshes_the_age():
    """⛔ THE SUBTLE ONE.  `Namespace.publish_state` notifies subscribers only
    when `new != old`, so a heartbeat-shaped publisher restating a steady value
    fires no callback.  If the age counter rode on the CALLBACK it would expire
    a publisher that is transmitting perfectly.  Receipt, not change, is what
    resets the clock."""
    clock = FakeClock()
    ns = _ns(clock, refresh_interval=1.0)
    ns.publish_state(3)

    for _ in range(10):
        clock.advance(1.0)
        ns.publish_state(3)                # identical value, no subscriber fires
        assert ns.value == 3, "a steady periodic publisher was expired"

    clock.advance(3.0)
    assert ns.value is UNKNOWN


def test_expiry_is_lazy_and_reversible_when_the_publisher_returns():
    """Nothing sweeps; the verdict is computed at READ.  D4: a timer-driven
    sweep means a stalled receiver demotes the whole world and blames the
    world."""
    clock = FakeClock()
    ns = _ns(clock, refresh_interval=1.0)
    ns.publish_state(3)
    clock.advance(10.0)
    assert ns.value is UNKNOWN

    ns.publish_state(7)
    assert ns.value == 7
    assert ns.verdict is Verdict.FRESH


def test_a_never_published_declared_namespace_is_unknown_not_its_engine_default():
    """A value never received is unknown.  Reading the state engine's zero here
    is the same lie as reading a dead publisher's last value."""
    ns = _ns(FakeClock(), refresh_interval=1.0)
    assert ns.value is UNKNOWN
    assert ns.verdict is Verdict.NEVER_RECEIVED


# --------------------------------------------------------------------------- #
# 2. QUIET BY CONSTRUCTION — the trap.  Silence here is HEALTH.
# --------------------------------------------------------------------------- #

def test_a_quiet_by_construction_namespace_NEVER_expires_on_its_own_silence():
    """⛔ THE TRAP.  `controller_main.cpp` gates the throttle on a sample clock
    AND a deadband against the last EMITTED value — "quiet-bus by
    construction".  A stationary lever publishes NOTHING, indefinitely, while
    being perfectly healthy.  A naive "no message in N seconds ⇒ stale" rule
    marks David's most-watched namespace DEAD."""
    clock = FakeClock()
    ns = _ns(clock, quiet_by_construction=True)
    ns.publish_state(42)

    clock.advance(86_400.0)                # a full day of honest silence
    assert ns.value == 42
    assert ns.verdict is Verdict.FRESH
    # ...and it has no timeout to be measured against in the first place, so a
    # future refactor cannot quietly give it one and still pass the above.
    assert ns.staleness_timeout is None
    assert ns.cadence is Cadence.EVENT_DRIVEN


def test_a_publisher_never_transmits_its_own_freshness_verdict():
    """⛔ FRESHNESS IS A RECEIVER'S JUDGEMENT, NEVER A PUBLISHER'S.

    Caught by mutation, not by design: `bus.publish_state` used to re-read
    `ns.value` to build the outbound envelope, which pushes the value it just
    accepted back through the expiry check BEFORE transmitting it. For a
    quiet-by-construction namespace whose node is currently marked silent, that
    check answers UNKNOWN — so the bus would have published UNKNOWN over the
    wire for a value it held in its hand one line earlier, corrupting a healthy
    signal on its way out because of a verdict about an unrelated node.
    """
    clock = FakeClock()
    bus = ComposeIOBus(bus_id="pub", clock=clock)
    bus.register_namespace("throttle/left", "json", quiet_by_construction=True)
    bus._ns_publisher["throttle/left"] = "dead-node"   # node never beat

    sent: list = []
    bus.on_message(lambda m: sent.append(m))
    bus.publish_state("throttle/left", 42)

    states = [m for m in sent if m.namespace == "throttle/left"]
    assert states, "nothing was transmitted"
    assert states[-1].value == 42, (
        f"the publisher transmitted its own staleness verdict "
        f"({states[-1].value!r}) instead of the value it was handed")


def test_a_quiet_namespace_DOES_expire_when_its_NODE_stops_beating():
    """...and this is what makes the heartbeat load-bearing rather than
    belt-and-braces.  Silence cannot condemn a quiet namespace; only the
    ABSENCE OF ITS NODE can."""
    clock = FakeClock()
    ns = _ns(clock, quiet_by_construction=True)

    alive = {"v": True}
    ns.attach_node_liveness(lambda: alive["v"])

    ns.publish_state(42)
    clock.advance(86_400.0)
    assert ns.value == 42                  # node still beating

    alive["v"] = False
    assert ns.value is UNKNOWN
    assert ns.verdict is Verdict.NODE_SILENT


# --------------------------------------------------------------------------- #
# 3. UNDECLARED — no verdict was ever authorised, so none is invented
# --------------------------------------------------------------------------- #

def test_an_undeclared_namespace_never_expires_but_says_it_never_judged():
    """UNKNOWN IS NOT FRESH — but inventing an interval would expire live
    values.  The honest answer is that no contract was declared, reported as a
    distinct verdict rather than smuggled in as FRESH."""
    clock = FakeClock()
    ns = _ns(clock)
    ns.publish_state(3)
    clock.advance(86_400.0)
    assert ns.value == 3
    assert ns.verdict is Verdict.UNDECLARED


# --------------------------------------------------------------------------- #
# 4. WIRE-UNKNOWN — "I am alive and I know that I do not know"
# --------------------------------------------------------------------------- #

def test_wire_unknown_and_expiry_unknown_are_the_SAME_receiver_state():
    """D4b: two DIFFERENT MECHANISMS CONVERGING ON ONE RECEIVER STATE.  Both are
    required, because a dead publisher cannot send 0xFD."""
    clock = FakeClock()
    ns = _ns(clock, refresh_interval=1.0)

    ns.publish_state(UNKNOWN)              # explicit 0xFD from a live publisher
    assert ns.value is UNKNOWN
    assert ns.verdict is Verdict.WIRE_UNKNOWN

    other = _ns(clock, refresh_interval=1.0)
    other.publish_state(3)
    clock.advance(3.0)
    assert other.value is UNKNOWN          # the SAME value, a different route
    assert other.verdict is Verdict.STALE


def test_publishing_unknown_bypasses_the_state_engine():
    """A `binary` engine handed UNKNOWN must not raise.  Engine-mismatch is the
    SIGABRT hazard; unknown is a TYPE, not a value in the engine's domain, so it
    can never be validated against one."""
    ns = Namespace("probe/b", StateEngine.from_config("binary"), clock=FakeClock(),
                   refresh_interval=1.0)
    ns.publish_state(UNKNOWN)              # must not raise
    assert ns.value is UNKNOWN


# --------------------------------------------------------------------------- #
# 5. DERIVED VALUES — age is the MAX, unknown if ANY input is unknown
# --------------------------------------------------------------------------- #

def test_a_freshness_class_takes_the_MAX_age_and_ANY_unknown():
    """David, ratifying D4, verbatim: "or a simple grouping of semantic messages
    that must all have a specific value within the bounding window defined as a
    class."  That grouping IS the derived-value rule made nameable."""
    from composeio.freshness import FreshnessClass

    clock = FakeClock()
    bus = ComposeIOBus(bus_id="derive", clock=clock)
    bus.register_namespace("a/x", "json", refresh_interval=1.0)
    bus.register_namespace("b/y", "json", refresh_interval=10.0)

    fc = FreshnessClass("nav_solution", ["a/x", "b/y"], bus=bus)

    bus.publish_state("a/x", 1)
    bus.publish_state("b/y", 2)
    assert fc.verdict is Verdict.FRESH
    assert fc.age == pytest.approx(0.0)

    clock.advance(2.0)
    assert fc.age == pytest.approx(2.0)    # MAX of the two ages, not the min
    assert fc.verdict is Verdict.FRESH

    clock.advance(1.0)                     # a/x is now 3 s old — expired
    assert fc.verdict is Verdict.STALE     # ANY unknown ⇒ the class is unknown
    assert fc.value is UNKNOWN


def test_a_freshness_class_is_declarable_in_YAML(tmp_path):
    """David's addition is a DESIGN REQUIREMENT, not a config note — so the
    class is not reachable only from Python."""
    import yaml
    from composeio.config import apply_config, load_config

    cfg = {
        "app": {"id": "cls"},
        "namespaces": [
            {"name": "a/x", "state_engine": "json",
             "freshness": {"refresh_interval": 1.0}},
            {"name": "b/y", "state_engine": "json",
             "freshness": {"refresh_interval": 10.0}},
        ],
        "freshness_classes": [
            {"name": "nav_solution", "members": ["a/x", "b/y"]},
        ],
    }
    p = tmp_path / "cls.compose.yaml"
    p.write_text(yaml.safe_dump(cfg))

    clock = FakeClock()
    bus = ComposeIOBus(bus_id="cls", clock=clock)
    apply_config(bus, load_config(str(p)))

    fc = bus.freshness_class("nav_solution")
    assert fc is not None
    bus.publish_state("a/x", 1)
    bus.publish_state("b/y", 2)
    assert fc.verdict is Verdict.FRESH

    clock.advance(3.0)
    assert fc.value is UNKNOWN


# --------------------------------------------------------------------------- #
# 6. THE HEARTBEAT RECEIVER — the half that was never built
# --------------------------------------------------------------------------- #

def test_the_heartbeat_receiver_marks_a_node_silent_at_three_intervals():
    clock = FakeClock()
    bus = ComposeIOBus(bus_id="rx", clock=clock)

    bus.note_heartbeat("throttle-node", interval_s=5.0)
    assert bus.node_is_alive("throttle-node") is True

    clock.advance(14.9)
    assert bus.node_is_alive("throttle-node") is True, "condemned a live node"
    clock.advance(0.1)                     # 15.0 s == 3 x 5 s
    assert bus.node_is_alive("throttle-node") is False


def test_a_node_never_heard_from_is_not_asserted_alive():
    bus = ComposeIOBus(bus_id="rx", clock=FakeClock())
    assert bus.node_is_alive("never-seen") is False


def test_the_bus_routes_an_inbound_heartbeat_into_the_receiver():
    """The publisher half shipped and the receiver half did not, so a beat
    arrived and nothing counted it."""
    from composeio.transport import BusMessage, MessageType

    clock = FakeClock()
    bus = ComposeIOBus(bus_id="rx", clock=clock)
    bus._dispatch_freshness(BusMessage(
        namespace="_bus/heartbeat", type=MessageType.HEARTBEAT,
        value={"node": "world-app", "uptime_s": 3, "interval_s": 5.0},
        source="world-app", scope="global"))

    assert bus.node_is_alive("world-app") is True
    clock.advance(15.0)
    assert bus.node_is_alive("world-app") is False


# --------------------------------------------------------------------------- #
# 7. THE ACCEPTANCE CRITERION, deterministically — a publisher that STOPS
# --------------------------------------------------------------------------- #

def test_dradis_contacts_goes_unknown_when_the_world_app_stops():
    """⛔ DAVID'S ACCEPTANCE CRITERION, with the publisher stopped by the test
    rather than by hoping another agent leaves it down.

    "with the world app stopped, `dradis/contacts` must become unknown/stale on
    the receiver rather than reading 3 forever."
    """
    clock = FakeClock()
    bus = ComposeIOBus(bus_id="dradis-rx", clock=clock)
    bus.register_namespace("dradis/contacts", "json", refresh_interval=1.0)

    # --- the world app is ALIVE and publishing ---
    for _ in range(5):
        bus.publish_state("dradis/contacts", 3, source="world-app")
        clock.advance(1.0)
        assert bus.get_state("dradis/contacts") == 3

    # --- the world app STOPS.  Nothing else changes. ---
    clock.advance(1.99)                    # 2.99 s since the last receipt
    assert bus.get_state("dradis/contacts") == 3, "expired early"

    clock.advance(0.01)                    # 3.00 s — the declared timeout
    assert bus.get_state("dradis/contacts") is UNKNOWN
    assert bus.get_namespace("dradis/contacts").verdict is Verdict.STALE
