"""`_bus/time` is a 1 Hz TIME SIGNAL — [DEC-BUS-TIME-IS-A-REAL-1HZ-SIGNAL].

WHAT IT WAS.  `_publish_bus_time` had exactly one caller repo-wide — `Bus.start()`
— so it fired ONCE, publishing `int(time.time())` and never again.  Nothing
consumed it; `peer/frame.py`, `peer/hub.py` and `peer/canbridge.py` each EXCLUDE
it explicitly.  It was neither a clock nor a heartbeat: it was a single stale
integer that looked like both.

WHAT IT IS.  A periodic signal at a cadence the NAMESPACE DECLARES.  It is the
fleet's first namespace that is PERIODIC BY CONSTRUCTION, which makes it the
first real consumer of D4's declared-refresh-interval: the cadence is READ FROM
THE DECLARATION, never a literal `1.0` at the call site.  Change the declaration
and the clock changes with it — that is the whole of what D4 ratified, proved on
its first case.

RESOLUTION: WHOLE SECONDS.  See `test_the_value_is_whole_unix_seconds`.
"""
from __future__ import annotations

import time

import pytest

from composebus.bus import ComposeIOBus
from composebus.bus_clock import BusClock, shared_clock
from composebus.namespace import Cadence
from composebus.transport import MessageType
from composebus.transport_local import LocalTransport


class RecordingTransport(LocalTransport):
    def __init__(self):
        super().__init__()
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        super().send(message)


@pytest.fixture
def bus():
    b = ComposeIOBus(bus_id="clock-probe")
    yield b
    b.stop()


# --------------------------------------------------------------------------- #
# 1. THE NAMESPACE DECLARES ITS OWN CADENCE  (D4's proving case)
# --------------------------------------------------------------------------- #

def test_bus_time_declares_a_1hz_refresh_interval(bus):
    ns = bus.get_namespace("_bus/time")
    assert ns is not None, "_bus/time must be a REGISTERED namespace, not an ad-hoc send"
    assert ns.cadence is Cadence.PERIODIC
    assert ns.refresh_interval == 1.0
    assert ns.staleness_timeout == 3.0


def test_the_clock_reads_its_cadence_from_the_declaration_not_a_literal(bus):
    """Re-declare the namespace and the clock MUST follow it.

    This is the test that would fail if someone hard-coded `1.0` in the ticker.
    D4 ratified a DECLARED cadence; a literal at the call site would make the
    declaration decorative on the very namespace that proves it.
    """
    bus.register_namespace("_bus/time", "json", refresh_interval=0.25)
    bus.start()
    assert bus.clock_interval == 0.25


def test_a_bus_with_announce_time_false_runs_no_clock(bus):
    """A LEAF bus does not announce a clock — two masters is a clock argument.

    `source_bus.py` already passes `announce_time=False` for exactly this
    reason; the periodic signal must inherit that opt-out rather than
    reintroduce the second publisher the opt-out was added to remove.
    """
    bus.start(announce_time=False)
    assert bus.clock_interval is None
    assert not bus.is_clock_running


# --------------------------------------------------------------------------- #
# 2. IT ACTUALLY TICKS
# --------------------------------------------------------------------------- #

def test_start_still_publishes_an_immediate_tick(bus):
    """Unchanged behaviour: a subscriber must not wait a whole interval."""
    bus.start()
    ticks = [m for m in bus.message_log if m.namespace == "_bus/time"]
    assert len(ticks) == 1


def test_the_clock_republishes_on_every_tick(bus):
    sink = RecordingTransport()
    b = ComposeIOBus(bus_id="ticker", global_transport=sink)
    b.register_namespace("_bus/time", "json", refresh_interval=0.05)
    b.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            ticks = [m for m in sink.messages if m.namespace == "_bus/time"]
            if len(ticks) >= 5:
                break
            time.sleep(0.02)
        ticks = [m for m in sink.messages if m.namespace == "_bus/time"]
        assert len(ticks) >= 5, (
            f"a 20 Hz declared cadence produced only {len(ticks)} ticks in 2 s "
            f"— the signal is not periodic")
    finally:
        b.stop()


def test_stop_stops_the_clock(bus):
    sink = RecordingTransport()
    b = ComposeIOBus(bus_id="stopper", global_transport=sink)
    b.register_namespace("_bus/time", "json", refresh_interval=0.05)
    b.start()
    time.sleep(0.3)
    b.stop()
    settled = len([m for m in sink.messages if m.namespace == "_bus/time"])
    time.sleep(0.4)
    after = len([m for m in sink.messages if m.namespace == "_bus/time"])
    assert after == settled, "the clock kept ticking after stop()"


# --------------------------------------------------------------------------- #
# 3. RESOLUTION — the decision, stated as a test
# --------------------------------------------------------------------------- #

def test_the_value_is_whole_unix_seconds(bus):
    """int SECONDS, not float, not millis.  THE DECISION, and its four reasons:

    1. THE CONSUMERS DECIDE, AND THEY ARE 8-BIT.  The named consumers are a
       CH32V003 and a TM1637 panel rendering "T-minus 4:32".  Whole seconds are
       a uint32 (4 bytes, no FPU); a float is 8 bytes and a soft-float parse on
       a part that has no hardware for it.
    2. spec/01 ALREADY SAYS uint32 SECONDS, and a wrap is ~136 years out.
       Changing the wire type to gain precision nothing asked for would break a
       corrected spec to serve no consumer.
    3. THE ONE USE THAT WOULD DEMAND SUB-SECOND WAS EXPLICITLY REJECTED.  D4
       ruled freshness receiver-side precisely because this signal is one-way
       with no delay compensation; measuring staleness against it is the thing
       it must not be used for, so its resolution is not a staleness question.
    4. A 1 Hz SIGNAL CANNOT CARRY SUB-SECOND TRUTH ANYWAY.  Delivered over a
       retained MQTT hop with unbounded jitter, millisecond digits would be
       precision without accuracy — the most expensive kind of lie.
    """
    bus.start()
    tick = [m for m in bus.message_log if m.namespace == "_bus/time"][0]
    assert isinstance(tick.value, int), (
        f"_bus/time carried {type(tick.value).__name__}; the wire type is "
        f"uint32 SECONDS per spec/01")
    assert not isinstance(tick.value, bool)
    assert abs(tick.value - int(time.time())) <= 1
    assert 0 <= tick.value < 2 ** 32


def test_the_tick_is_a_time_sync_message(bus):
    bus.start()
    tick = [m for m in bus.message_log if m.namespace == "_bus/time"][0]
    assert tick.type is MessageType.TIME_SYNC
    assert tick.scope == "global"


def test_successive_ticks_never_go_backwards():
    sink = RecordingTransport()
    b = ComposeIOBus(bus_id="mono", global_transport=sink)
    b.register_namespace("_bus/time", "json", refresh_interval=0.05)
    b.start()
    try:
        time.sleep(0.6)
        vals = [m.value for m in sink.messages if m.namespace == "_bus/time"]
        assert vals == sorted(vals), f"time went backwards: {vals}"
    finally:
        b.stop()


# --------------------------------------------------------------------------- #
# 4. THE CLOCK ITSELF — one per process, deterministic under test
# --------------------------------------------------------------------------- #

def test_the_shared_clock_is_one_thread_per_process_not_one_per_bus():
    """N buses must not mean N threads.  There is one wall clock per process."""
    assert shared_clock() is shared_clock()


def test_tick_once_drives_the_clock_deterministically():
    """A test must be able to advance the clock without sleeping."""
    calls = []
    clock = BusClock(autostart=False)
    clock.attach(lambda now: calls.append(now), interval=1.0)
    clock.tick_once(now=1000.0)
    clock.tick_once(now=1000.5)      # inside the interval — must NOT fire
    clock.tick_once(now=1001.0)
    assert calls == [1000.0, 1001.0]


def test_a_collected_bus_detaches_itself_from_the_clock():
    """The clock holds WEAK references.

    A test (or a script) that starts a bus and drops it without calling stop()
    must not leave a callback publishing into a dead bus forever.  Making that
    structural beats documenting it: 3,539 tests will not all remember.
    """
    import gc

    clock = BusClock(autostart=False)

    class Sink:
        def __init__(self):
            self.n = 0

        def tick(self, now):
            self.n += 1

    s = Sink()
    clock.attach(s.tick, interval=1.0)
    clock.tick_once(now=1.0)
    assert s.n == 1
    assert clock.subscriber_count == 1

    del s
    gc.collect()
    clock.tick_once(now=2.0)
    assert clock.subscriber_count == 0


def test_the_clock_runs_at_the_finest_declared_cadence():
    """Two subscribers, two declared intervals, ONE thread.

    The thread's period is the finest declared interval; each subscriber fires
    on its OWN.  That is what lets a second periodic namespace land later
    without a second thread and without renegotiating the first one's cadence.
    """
    clock = BusClock(autostart=False)
    fast, slow = [], []
    clock.attach(lambda now: fast.append(now), interval=0.5)
    clock.attach(lambda now: slow.append(now), interval=2.0)
    assert clock.period == 0.5
    for t in (0.0, 0.5, 1.0, 1.5, 2.0):
        clock.tick_once(now=t)
    assert fast == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert slow == [0.0, 2.0]


# --------------------------------------------------------------------------- #
# 5. THE PER-NODE HEARTBEAT — D4's load-bearing half, previously UNBUILT
# --------------------------------------------------------------------------- #
# `Bus.publish_heartbeat()` was DEFINED and had exactly one occurrence in the
# whole `composeio` package: its own definition. No caller, no timer. Meanwhile
# spec/01 §2.5/§5.2/§9.2 describe heartbeat cadence and heartbeat-timeout-driven
# degraded mode as working behaviour.
#
# It matters because D4 made it LOAD-BEARING, not belt-and-braces: a
# per-namespace timeout cannot distinguish "publisher dead" from "event-driven
# with nothing to say", and the fleet's most-watched namespace (`throttle/*`) is
# quiet-by-construction. For that class the heartbeat is the ONLY liveness
# signal there is.
#
# ⛔ SCOPE, STATED HONESTLY. What lands here is the PUBLISHER: a periodic
# heartbeat at a DECLARED cadence, riding the same clock as `_bus/time` because
# it is the same shape of object. The RECEIVER half — a monitor that counts
# missed intervals and drives a node to degraded — is NOT built here and is
# named as a prerequisite in the report.

def test_bus_heartbeat_declares_its_own_cadence(bus):
    ns = bus.get_namespace("_bus/heartbeat")
    assert ns is not None, "_bus/heartbeat must be a REGISTERED namespace"
    assert ns.cadence is Cadence.PERIODIC
    assert ns.refresh_interval == 5.0
    assert ns.staleness_timeout == 15.0


def test_the_heartbeat_actually_beats():
    """The defect being closed: `publish_heartbeat` had no caller anywhere."""
    sink = RecordingTransport()
    b = ComposeIOBus(bus_id="beater", global_transport=sink)
    b.register_namespace("_bus/heartbeat", "json", refresh_interval=0.05)
    b.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            beats = [m for m in sink.messages
                     if m.namespace == "_bus/heartbeat"]
            if len(beats) >= 5:
                break
            time.sleep(0.02)
        beats = [m for m in sink.messages if m.namespace == "_bus/heartbeat"]
        assert len(beats) >= 5, (
            f"declared 20 Hz, got {len(beats)} beats in 2 s — the heartbeat is "
            f"still a function nobody calls")
        assert beats[0].type is MessageType.HEARTBEAT
    finally:
        b.stop()


def test_the_heartbeat_names_the_node_it_is_the_liveness_of(bus):
    """A heartbeat whose payload does not identify its node cannot mark a node
    dead — it is a pulse with no patient."""
    bus.start()
    bus.publish_heartbeat()
    beat = [m for m in bus.message_log
            if m.namespace == "_bus/heartbeat"][-1]
    assert beat.source == "clock-probe"
    assert beat.value["node"] == "clock-probe"
    assert isinstance(beat.value["uptime_s"], int)
    assert beat.value["interval_s"] == 5.0


def test_announce_time_false_also_silences_the_heartbeat(bus):
    """A LEAF bus is not a node whose liveness the fleet tracks; it shares the
    hosting app's process and the app already beats for it."""
    bus.start(announce_time=False)
    beats = [m for m in bus.message_log if m.namespace == "_bus/heartbeat"]
    assert beats == []
