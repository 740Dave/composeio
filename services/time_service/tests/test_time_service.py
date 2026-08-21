"""time_service platform-core tests — against the REAL ``ComposeIOBus`` in-process
(no MQTT, no broker, no network).

    PYTHONPATH=python:services python3 -m pytest services/time_service/tests -v

Covers the three ruled forks the build was dispatched to
([SPEC:CXC-TIME-SERVICE] [CXC-TIME-AUDIT-R1] `forks-ruled-r1`):
  * [DD-TIME-WIRE-FORMAT]  = epoch (int) on time/base;
  * [DD-TIME-SCHEDULE-RETAINED] = a retained last-value snapshot for late joiners;
  * default 1 Hz tick, configurable ([CXC-TIME-BASE]);
plus the idle locale table's offsets ([CXC-TIME-IDLE-LOCALES]) and Kobol reserved.
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from composeio.bus import ComposeIOBus
from composeio.transport import MessageType
from composeio.transport_mqtt import RETAIN_BY_TYPE

from time_service import topics
from time_service.profile import IDLE_BODIES, RESERVED_BODIES, TimeProfile
from time_service.service import TimeService


# A fixed instant so offset assertions are deterministic and DST-correct:
# 2024-08-07T12:00:00Z (northern summer -> CEST, PDT in effect).
FIXED_EPOCH = 1723032000


def _fixed_clock():
    return lambda: float(FIXED_EPOCH)


# --------------------------------------------------------------------------- #
# time/base — value shape and wire format
# --------------------------------------------------------------------------- #
def test_base_is_a_bare_int_epoch_when_idle():
    """[DD-TIME-WIRE-FORMAT] = epoch(int); [CXC-TIME-BASE] idle = UTC epoch."""
    bus = ComposeIOBus()
    svc = TimeService(clock=_fixed_clock())
    svc.attach(bus)
    value = svc.tick()
    assert value == FIXED_EPOCH
    assert isinstance(value, int)
    assert bus.get_state(topics.BASE) == FIXED_EPOCH


def test_a_subscriber_sees_each_one_hz_tick():
    """A subscriber sees time/base advance once per second. Driven with a
    controllable clock so the 1 Hz cadence is asserted deterministically without
    sleeping — the int-epoch value changes each second, which is what a 1 Hz
    idle clock does on the wire."""
    bus = ComposeIOBus()
    seconds = iter([FIXED_EPOCH, FIXED_EPOCH + 1, FIXED_EPOCH + 2])
    svc = TimeService(clock=lambda: float(next(seconds)))
    svc.attach(bus)

    seen: list[int] = []
    bus.subscribe(topics.BASE, lambda ns, new, old: seen.append(new))

    svc.tick()
    svc.tick()
    svc.tick()
    assert seen == [FIXED_EPOCH, FIXED_EPOCH + 1, FIXED_EPOCH + 2]


def test_the_tick_rate_is_configurable_and_defaults_to_one_hz():
    """[CXC-TIME-BASE] `tick-schedule`: 1 Hz DEFAULT, configurable via profile."""
    assert TimeProfile.idle_default().tick_hz == 1.0
    assert TimeService().tick_interval == 1.0

    fast = TimeService(profile=TimeProfile(tick_hz=4.0))
    assert fast.tick_interval == 0.25


def test_base_actually_publishes_at_the_configured_rate():
    """The threaded loop honors tick_hz. Counted via bus.on_message (every wire
    publish, not only value CHANGES) so a fast rate at integer-second resolution
    is still measured correctly."""
    bus = ComposeIOBus()
    svc = TimeService(profile=TimeProfile(tick_hz=20.0), clock=time.time)
    svc.attach(bus)

    ticks: list = []
    bus.on_message(
        lambda msg: ticks.append(msg) if msg.namespace == topics.BASE else None
    )
    svc.start()          # publishes an immediate snapshot, then loops at 20 Hz
    time.sleep(0.6)
    svc.stop()

    # ~12 in 0.6 s at 20 Hz plus the immediate snapshot; assert a generous floor
    # so the test is not flaky under load, but enough to prove it is ticking.
    assert len(ticks) >= 6, f"expected the loop to tick repeatedly, got {len(ticks)}"
    assert all(m.type == MessageType.STATE for m in ticks)


# --------------------------------------------------------------------------- #
# [DD-TIME-SCHEDULE-RETAINED] — the late joiner reads the snapshot immediately
# --------------------------------------------------------------------------- #
def test_time_base_is_published_as_a_retained_state_message():
    """The ruling is realized by publishing STATE, the type ComposeBus retains
    (over MQTT the broker serves it to every future subscriber). This pins the
    mechanism: if time/base were ever emitted as an EVENT it would NOT retain and
    a late joiner would wait up to a tick."""
    assert RETAIN_BY_TYPE[MessageType.STATE] is True
    assert RETAIN_BY_TYPE[MessageType.EVENT] is False

    bus = ComposeIOBus()
    captured: list = []
    bus.on_message(lambda msg: captured.append(msg))
    svc = TimeService(clock=_fixed_clock())
    svc.attach(bus)
    svc.tick()

    base_msgs = [m for m in captured if m.namespace == topics.BASE]
    assert base_msgs and all(m.type == MessageType.STATE for m in base_msgs)


def test_a_late_joiner_reads_the_retained_snapshot_without_waiting():
    """A subscriber that appears AFTER the service has been ticking reads the
    current value immediately from the retained latch — it does not wait for the
    next tick. In-process this is ``get_state`` (the bus latch); over MQTT it is
    the retained STATE the broker replays on subscribe."""
    bus = ComposeIOBus()
    svc = TimeService(clock=_fixed_clock())
    svc.attach(bus)
    svc.start()          # immediate snapshot published
    svc.stop()           # stop ticking; the retained value must survive

    # The "late joiner" arrives now, after all ticking has ceased.
    assert bus.get_state(topics.BASE) == FIXED_EPOCH
    for name in IDLE_BODIES:
        assert bus.get_state(topics.body(name)) is not None


# --------------------------------------------------------------------------- #
# [CXC-TIME-IDLE-LOCALES] — idle bodies carry the right offsets; Kobol reserved
# --------------------------------------------------------------------------- #
def test_idle_bodies_carry_the_right_offsets():
    """Each idle body publishes base shifted by its IANA-tz UTC offset at that
    instant (DST-correct). Ship=SF, Caprica=Copenhagen, Virgon=Singapore,
    Tauron=Ho Chi Minh City ([CXC-TIME-IDLE-LOCALES], audit Q1)."""
    bus = ComposeIOBus()
    svc = TimeService(clock=_fixed_clock())
    svc.attach(bus)
    svc.tick()

    base = bus.get_state(topics.BASE)
    for name, tz_name in IDLE_BODIES.items():
        expected_offset = int(
            datetime.fromtimestamp(FIXED_EPOCH, tz=ZoneInfo(tz_name))
            .utcoffset()
            .total_seconds()
        )
        got = bus.get_state(topics.body(name))
        assert got - base == expected_offset, (
            f"{name} ({tz_name}) offset wrong: {got - base} != {expected_offset}")

    # Ship is San Francisco (America/Los_Angeles), and in August that is PDT
    # (UTC-7). The spec's INVARIANT ship default.
    assert IDLE_BODIES["ship"] == "America/Los_Angeles"
    assert bus.get_state(topics.body("ship")) - base == -7 * 3600  # PDT


def test_kobol_is_reserved_and_absent_from_idle():
    """[CXC-TIME-IDLE-LOCALES] `locale-kobol`: Kobol is NOT realized in idle."""
    assert "kobol" not in IDLE_BODIES
    assert "kobol" in RESERVED_BODIES

    bus = ComposeIOBus()
    svc = TimeService(clock=_fixed_clock())
    svc.attach(bus)
    svc.tick()
    assert bus.get_state(topics.body("kobol")) is None


def test_a_body_realizes_off_the_base_it_hears_not_its_own_clock():
    """Bodies re-publish when time/base changes — they subscribe the fact, they
    do not read a clock ([CXC-TIME-BODY])."""
    bus = ComposeIOBus()
    clock_values = iter([float(FIXED_EPOCH), float(FIXED_EPOCH + 100)])
    svc = TimeService(clock=lambda: next(clock_values))
    svc.attach(bus)

    svc.tick()
    first = bus.get_state(topics.body("caprica"))
    svc.tick()
    second = bus.get_state(topics.body("caprica"))
    assert second - first == 100


# --------------------------------------------------------------------------- #
# game mode — base_mode from the (minimal) profile
# --------------------------------------------------------------------------- #
def test_game_mode_publishes_elapsed_seconds_from_start():
    """[CXC-TIME-BASE]: game -> game-elapsed-seconds-from-start. The minimal
    profile's `base_mode: game` selects it; start() latches the reference."""
    ticks = iter([1000.0, 1003.5, 1010.0])   # each base_value() reads once
    svc = TimeService(profile=TimeProfile(base_mode="game"), clock=lambda: next(ticks))
    bus = ComposeIOBus()
    svc.attach(bus)

    # Latch the game start reference explicitly (start() does this from the clock
    # in the live path); each base_value() then reads the clock once.
    svc._game_start = 1000.0
    assert svc.base_value() == 0      # reads 1000.0 -> 0 elapsed
    assert svc.base_value() == 3      # reads 1003.5 -> 3 elapsed (int)
    assert svc.base_value() == 10     # reads 1010.0 -> 10 elapsed


def test_no_idle_body_emits_a_value_in_game_mode():
    """QA note 2 / [DD-TIME-BODY-UNIFICATION]: the idle ``offset`` bodies apply a
    locale offset to a WALL-CLOCK epoch. In game mode the base is
    game-elapsed-seconds (near 1970 as an absolute instant), so an offset body
    would publish nonsense (e.g. time/ship = -28800). Gated OFF: NO idle body may
    subscribe or publish when base_mode == 'game' — the game-world layer replaces
    the object table there."""
    bus = ComposeIOBus()
    svc = TimeService(profile=TimeProfile(base_mode="game"), clock=lambda: 5.0)
    svc.attach(bus)

    published: list = []
    bus.on_message(lambda msg: published.append(msg.namespace))

    svc.start()          # game base ticks; NO idle body should follow
    svc.stop()

    # time/base itself is published (game-elapsed), but no idle body namespace is.
    assert bus.get_state(topics.BASE) is not None
    for name in IDLE_BODIES:
        assert bus.get_state(topics.body(name)) is None, (
            f"idle body {name!r} emitted a value in game mode")
    assert not any(ns != topics.BASE and ns.startswith("time/") for ns in published), (
        f"an idle body published in game mode: {sorted(set(published))}")

    # ...and each such body correctly reports it does not realize in game mode.
    for b in svc.bodies:
        assert b.realizes_in("idle") is True
        assert b.realizes_in("game") is False


# --------------------------------------------------------------------------- #
# profile loader — minimal, per [DD-TIME-PROFILE-SHAPE] (left OPEN)
# --------------------------------------------------------------------------- #
def test_profile_loader_reads_only_the_minimal_fields_and_ignores_the_rest():
    """[DD-TIME-PROFILE-SHAPE] is ruled OPEN: read base_mode/tick_hz/bodies,
    IGNORE unknown keys so a richer future profile still loads."""
    prof = TimeProfile.load({
        "base_mode": "game",
        "tick_hz": 2.0,
        "bodies": {"kobol": "UTC"},
        "some_future_field": {"rotation": 26.0},   # must be ignored, not rejected
    })
    assert prof.base_mode == "game"
    assert prof.tick_hz == 2.0
    assert prof.bodies == {"kobol": "UTC"}


def test_profile_loader_defaults_are_the_idle_realization():
    prof = TimeProfile.load({})
    assert prof.base_mode == "idle"
    assert prof.tick_hz == 1.0
    assert prof.bodies == IDLE_BODIES
