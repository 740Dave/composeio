"""storage_monitor tests — against the REAL ``ComposeIOBus`` in-process (no MQTT, no
broker, no network).

    PYTHONPATH=python:services python3 -m pytest services/storage_monitor/tests -v

Pins the FROZEN contract a peer render build (the launch bar) depends on EXACTLY:
  * the namespace is ``skipper/storage/free_bytes``;
  * the payload is a bare INT number of free bytes;
  * it is published as a RETAINED STATE (so a late joiner reads it at once);
  * default cadence 30 s, configurable;
  * the monitored path is configurable.
"""

from __future__ import annotations

import shutil
import time

from composeio.bus import ComposeIOBus
from composeio.transport import MessageType
from composeio.transport_mqtt import RETAIN_BY_TYPE

from storage_monitor import topics
from storage_monitor.service import StorageMonitor


# A deterministic byte count so the publish path is asserted without depending on the
# host's real disk.
FIXED_FREE = 123_456_789


def _fixed_measure(_path: str) -> int:
    return FIXED_FREE


# --------------------------------------------------------------------------- #
# the frozen contract: namespace + payload shape
# --------------------------------------------------------------------------- #
def test_the_namespace_is_exactly_skipper_storage_free_bytes():
    """The peer render build binds this literal — pin the spelling."""
    assert topics.FREE_BYTES == "skipper/storage/free_bytes"


def test_publishes_free_bytes_as_a_bare_int_for_the_configured_path():
    """[DX-STATE-ON-BUS]: publish the FACT. The value is an int number of free bytes for
    the monitored path, readable back off the bus latch."""
    bus = ComposeIOBus()
    svc = StorageMonitor(path="/tmp", measure=_fixed_measure)
    svc.attach(bus)

    value = svc.tick()
    assert value == FIXED_FREE
    assert isinstance(value, int)
    assert bus.get_state(topics.FREE_BYTES) == FIXED_FREE


def test_measures_real_free_bytes_for_a_known_path():
    """The default measurement is ``shutil.disk_usage(path).free`` — a positive int that
    matches a fresh direct reading closely (disk moves slowly between the two calls)."""
    bus = ComposeIOBus()
    svc = StorageMonitor(path="/")
    svc.attach(bus)

    value = svc.tick()
    assert isinstance(value, int)
    assert value > 0
    direct = shutil.disk_usage("/").free
    # Within 1 GiB — the two readings are moments apart on a live filesystem.
    assert abs(value - direct) < (1 << 30)


# --------------------------------------------------------------------------- #
# retained STATE — the late joiner reads the snapshot immediately
# --------------------------------------------------------------------------- #
def test_free_bytes_is_published_as_a_retained_state_message():
    """The retention is realized by publishing STATE, the type ComposeBus retains. If it
    were ever emitted as an EVENT it would NOT retain and a late joiner would wait up to a
    (slow, 30 s) tick."""
    assert RETAIN_BY_TYPE[MessageType.STATE] is True
    assert RETAIN_BY_TYPE[MessageType.EVENT] is False

    bus = ComposeIOBus()
    captured: list = []
    bus.on_message(lambda msg: captured.append(msg))
    svc = StorageMonitor(path="/tmp", measure=_fixed_measure)
    svc.attach(bus)
    svc.tick()

    msgs = [m for m in captured if m.namespace == topics.FREE_BYTES]
    assert msgs and all(m.type == MessageType.STATE for m in msgs)


def test_a_late_joiner_reads_the_retained_snapshot_without_waiting():
    """A subscriber that appears AFTER the sensor has ticked reads the current value
    immediately from the retained latch (``get_state``)."""
    bus = ComposeIOBus()
    svc = StorageMonitor(path="/tmp", measure=_fixed_measure)
    svc.attach(bus)
    svc.tick()

    assert bus.get_state(topics.FREE_BYTES) == FIXED_FREE


# --------------------------------------------------------------------------- #
# cadence + path are CONFIG
# --------------------------------------------------------------------------- #
def test_interval_defaults_to_thirty_seconds_and_is_configurable():
    """Disk space changes slowly — default 30 s, overridable."""
    assert StorageMonitor(measure=_fixed_measure).interval_s == 30.0
    assert StorageMonitor(interval_s=60.0, measure=_fixed_measure).interval_s == 60.0


def test_the_monitored_path_is_configurable():
    """David flagged skipper's drive is SMALL and a spinny disk may be added on a
    different mount — the path is re-pointable without a code change."""
    svc = StorageMonitor(path="/mnt/spinny", measure=_fixed_measure)
    assert svc.path == "/mnt/spinny"


def test_env_drives_path_and_interval(monkeypatch):
    """The deploy path reads config from the environment."""
    monkeypatch.setenv("COMPOSEIO_STORAGE_MONITOR_PATH", "/data")
    monkeypatch.setenv("COMPOSEIO_STORAGE_MONITOR_INTERVAL", "45")
    svc = StorageMonitor(measure=_fixed_measure)
    assert svc.path == "/data"
    assert svc.interval_s == 45.0


# --------------------------------------------------------------------------- #
# the threaded loop actually publishes repeatedly
# --------------------------------------------------------------------------- #
def test_start_publishes_an_immediate_snapshot_then_ticks():
    """start() publishes at once (retained value present immediately) and then ticks on
    the interval — a short interval proves the loop runs."""
    bus = ComposeIOBus()
    svc = StorageMonitor(path="/tmp", interval_s=0.05, measure=_fixed_measure)
    svc.attach(bus)

    ticks: list = []
    bus.on_message(
        lambda msg: ticks.append(msg) if msg.namespace == topics.FREE_BYTES else None
    )
    svc.start()          # immediate snapshot, then loops every 0.05 s
    time.sleep(0.3)
    svc.stop()

    assert len(ticks) >= 2, f"expected repeated ticks, got {len(ticks)}"
    assert all(m.type == MessageType.STATE for m in ticks)


def test_tick_before_attach_raises():
    svc = StorageMonitor(measure=_fixed_measure)
    try:
        svc.tick()
    except RuntimeError:
        return
    raise AssertionError("tick() before attach() should raise RuntimeError")
