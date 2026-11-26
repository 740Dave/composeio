"""One bus, 20 publisher threads — PROVE it before the fan-out lands.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THREAD-SAFETY], acceptance criterion SOB-C8.

WHY THIS IS A TEST AND NOT A PARAGRAPH. Routing the console's 20 data sources
through one `ComposeIOBus` makes ~20 daemon threads call `publish_state` on a
single object concurrently, where today each has its own paho client. paho's
`publish()` is documented thread-safe against one `loop_start()` client, but the
strategy that motivated this work explicitly said the fan-out should be PROVEN
under the real shape rather than trusted from a doc — because the bus adds its
own shared mutable state on top of paho's:

  * `ComposeIOBus._next_seq` is a read-modify-write with NO lock, so two threads
    can be handed the same sequence number;
  * `_message_log` is REBOUND by slicing (`self._message_log = ...[-limit:]`)
    while other threads append to it, so an append can land on a list that is
    about to be discarded.

Neither is expected to be fatal — `seq` is advisory ([CXC-SOB-WIRE-DELTAS]
normalizes it out of the capture-diff) and a lost log entry is cosmetic — but
"expected to be" is exactly the kind of assumption this arc exists to replace
with a measurement. What MUST hold is that no message is lost and no envelope is
corrupted, because those are the failures that would show up as a silently dark
widget.

If this test ever finds real corruption, the fix belongs in `bus.py` under a
lock, NOT in a per-source workaround.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.bus import ComposeIOBus                      # noqa: E402
from composeio.namespace import Scope                       # noqa: E402
from composeio.transport import BusMessage                  # noqa: E402

N_THREADS = 20
M_MESSAGES = 500


class RecordingTransport:
    """A Transport-shaped sink that records every message it is handed.

    Appends under its OWN lock so the RECORDER can never be the thing that loses
    a message — otherwise a failure would be ambiguous between the bus and the
    instrument.
    """

    def __init__(self):
        self.messages: list[BusMessage] = []
        self._lock = threading.Lock()
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def send(self, message):
        with self._lock:
            self.messages.append(message)

    def subscribe(self, namespace, callback):
        pass

    def subscribe_all(self, callback):
        pass

    def subscribe_pattern(self, pattern):
        pass

    @property
    def is_connected(self):
        return True


@pytest.fixture(scope="module")
def fanned_out():
    """Drive the REAL shape ONCE — N threads x M publishes on one bus — and hand
    every test the same evidence. Module-scoped deliberately: re-running 10 000
    concurrent publishes per assertion buys nothing, and every test below is a
    different question about the SAME run."""
    sink = RecordingTransport()
    bus = ComposeIOBus(bus_id="concurrency-probe", global_transport=sink)
    for i in range(N_THREADS):
        bus.register_namespace(f"probe/t{i}", "json", scope=Scope.GLOBAL)
    bus.start()
    # MEASURE ONLY WHAT THE THREADS ADD.
    #
    # This used to be `baseline = len(sink.messages)` plus a tail slice, which
    # worked only while the bus's infrastructure traffic was a ONE-SHOT burst at
    # start(). `_bus/time` is now a real 1 Hz signal and `_bus/heartbeat` really
    # beats ([DEC-BUS-TIME-IS-A-REAL-1HZ-SIGNAL]), so infrastructure messages now
    # INTERLEAVE with the fan-out for the whole ~10 s of this run and a positional
    # slice silently counts them as thread output.
    #
    # Filtering by namespace states the intent directly and is durable against
    # the next periodic namespace: this test is about `probe/t*`, and `_bus/*` is
    # somebody else's traffic no matter when it arrives.
    def _thread_traffic():
        return [m for m in sink.messages if m.namespace.startswith("probe/")]

    errors: list[BaseException] = []
    ready = threading.Barrier(N_THREADS)

    def publisher(i: int):
        ns = f"probe/t{i}"
        try:
            ready.wait(timeout=10)               # maximize real overlap
            for m in range(M_MESSAGES):
                bus.publish_state(ns, {"t": i, "m": m}, source=f"probe-{i}")
        except BaseException as exc:             # noqa: BLE001 — the assertion
            errors.append(exc)

    threads = [threading.Thread(target=publisher, args=(i,), daemon=True,
                                name=f"pub{i}") for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), f"{t.name} did not finish"

    yield bus, sink, _thread_traffic(), errors
    bus.stop()


def test_twenty_threads_five_hundred_messages_each(fanned_out):
    """(a) no exception, (b) exactly N*M messages arrive, (c) no interleaved
    corruption — every captured payload parses and its `ns` matches the thread
    that published it."""
    _bus, _sink, published, errors = fanned_out

    assert errors == [], f"(a) concurrent publish raised: {errors[:3]}"

    assert len(published) == N_THREADS * M_MESSAGES, (
        f"(b) expected {N_THREADS * M_MESSAGES} messages, got {len(published)} "
        "— a lost publish here is a silently dark widget in production")

    # (c) every envelope is internally consistent: the namespace, the source and
    # the payload's own thread id must all agree. A torn envelope shows up as a
    # value belonging to a different namespace.
    seen: dict[str, set[int]] = {}
    for msg in published:
        raw = json.dumps(msg.to_json())          # exercise the real serializer
        env = json.loads(raw)
        ns = env["ns"]
        val = env["val"]
        assert ns == f"probe/t{val['t']}", (
            f"(c) envelope corruption: ns={ns!r} carries value {val!r}")
        assert env["src"] == f"probe-{val['t']}", (
            f"(c) src corruption: {env['src']!r} on {ns!r}")
        assert env["type"] == "state" and env["scope"] == "global"
        seen.setdefault(ns, set()).add(val["m"])

    assert len(seen) == N_THREADS, f"(c) only {len(seen)} namespaces reached the sink"
    for ns, ms in seen.items():
        assert ms == set(range(M_MESSAGES)), (
            f"(c) {ns} lost or duplicated: {M_MESSAGES - len(ms)} missing")


def test_message_log_survives_the_rebind_race(fanned_out):
    """`_message_log` is rebound by slicing under concurrent append.

    The log is capped at `_log_limit`, so 10 000 messages FORCE the rebind path
    repeatedly. Assert only what is contractual: the bus does not raise, and the
    log stays a list bounded by its limit. A dropped log entry is cosmetic and
    deliberately NOT asserted — pinning it would freeze an accident.
    """
    bus, _sink, _published, _errors = fanned_out
    assert isinstance(bus._message_log, list)
    assert len(bus._message_log) <= bus._log_limit, (
        "the message log exceeded its cap under concurrent append")


def test_seq_is_advisory_not_unique_under_concurrency(fanned_out):
    """`_next_seq` is unlocked, so `seq` may collide. PIN that we know it.

    This is not an endorsement — it is [DD-SOB-SEQ-SEMANTICS] made executable. If
    someone later makes `seq` load-bearing (gap detection, replay ordering), this
    test is where they will discover that the bus does not currently guarantee
    it, instead of discovering it from a replay that silently reorders.
    """
    _bus, _sink, published, _errors = fanned_out
    seqs = [m.sequence for m in published]
    assert len(seqs) == N_THREADS * M_MESSAGES
    assert all(0 <= s < 65536 for s in seqs), "seq must stay in the uint16 domain"
    # No uniqueness assertion, deliberately: see the docstring.


def test_self_echo_memory_under_forced_contention():
    """The fixture above caught a REAL loss only ~1 run in 20: `_remember_emitted`
    evicted from a shared dict with no lock, so two publishers deleted the same
    oldest key (KeyError) or one iterated while another inserted (RuntimeError),
    and the exception left publish_state BEFORE connector.send, losing the
    message. A 1 us GIL switch interval makes that interleaving near-certain
    (measured unfixed: ~800 of 10 000 publishes raised, every run), so this test
    is red on the old code every time instead of once in twenty.
    """
    sink = RecordingTransport()
    bus = ComposeIOBus(bus_id="echo-contention", global_transport=sink)
    for i in range(N_THREADS):
        bus.register_namespace(f"probe/t{i}", "json", scope=Scope.GLOBAL)
    bus.start()
    errors: list[BaseException] = []
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        def publisher(i: int):
            for m in range(M_MESSAGES):
                try:
                    bus.publish_state(f"probe/t{i}", {"t": i, "m": m}, source=f"probe-{i}")
                except BaseException as exc:     # noqa: BLE001 — the assertion
                    errors.append(exc)
        threads = [threading.Thread(target=publisher, args=(i,), daemon=True) for i in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
    finally:
        sys.setswitchinterval(old)
        bus.stop()
    got = [m for m in sink.messages if m.namespace.startswith("probe/")]
    assert errors == [], f"{len(errors)} publishes raised, e.g. {errors[:2]}"
    assert len(got) == N_THREADS * M_MESSAGES
    assert len(bus._emitted_fingerprints) <= bus.SELF_ECHO_MEMORY
