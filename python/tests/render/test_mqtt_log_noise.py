"""The 256-byte guideline must not turn the console log into a warning storm.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-LOG-NOISE].

DP-CIO-18 asks that a bus message stay under 256 bytes — a real DESIGN guideline
for a constrained substrate (a CAN frame is 8 bytes; a UART line is precious).
`MQTTTransport._send_now` enforced it with a `logger.warning` on every oversized
payload, which was harmless while nothing large went through the bus.

Routing the Intelligence Console's 20 data sources through the bus makes that
false. The console's payloads are routinely KILOBYTES: transcript narration, the
deliverable ledger, the agents/skills arrays, the defect roll-up, the projects
report. At 256 bytes that is hundreds of warnings a minute where today there are
none — and it would bury the LOUD failures this whole arc depends on being
visible (a `source_bus.declare` scope conflict, a transport degrading to
local-only). A warning that fires constantly is a warning nobody reads, so
preserving the guideline by making the log useless is a net loss.

The guideline is therefore still CHECKED and still LOGGED, at DEBUG; WARNING is
reserved for a genuine runaway (an unbounded accumulator, a whole file pasted
into a value).

AMENDED 2026-08-13 ([CXC-SOB-LOG-NOISE-CALIBRATION]). The first cut of this file
pinned `LARGE_PAYLOAD_WARN_BYTES == 64 * 1024` and probed the tiers at 4 KiB and
128 KiB. Neither probe is a real console payload, and every actual one — 16 KB to
110 KB — falls in the untested gap between them. `self/deliverables` was 70,333
bytes on the wire on the very commit that chose 64 KiB, so this suite shipped
GREEN while defending a live defect: 27,009 runaway warnings in 58,589 log lines,
46% of `console.log`. The pin is gone. What is asserted now is the INVARIANT —
above every enumerated payload, MEASURED from the real projection — plus the
structural half that makes the absolute value non-critical: one warning per
namespace per process, so a re-crossing costs one line, not a storm.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.transport import BusMessage, MessageType        # noqa: E402
from composeio.transport_mqtt import MQTTTransport             # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LEDGER_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "deliverable_ledger_178.lts"


def _compute_payload():
    """The REAL projection function, imported by path.

    `compose_projects/intelligence/publisher/` is not an importable package, so this loads the
    module the daemon actually runs rather than re-implementing its shape — a
    re-implementation would drift and this test would then measure a fiction.
    """
    path = _REPO_ROOT / "compose_projects" / "intelligence" / "publisher" / "deliverable_publisher.py"
    spec = importlib.util.spec_from_file_location("_deliverable_publisher", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute_payload


def _real_deliverables_message() -> tuple[BusMessage, int]:
    """The `self/deliverables` STATE message as the live daemon builds it, from a
    checked-in 178-node ledger fixture, plus its size ON THE WIRE."""
    compute_payload = _compute_payload()
    value = compute_payload(_LEDGER_FIXTURE.read_text(encoding="utf-8"),
                            "2026-08-13", {})
    msg = BusMessage(namespace="self/deliverables", type=MessageType.STATE,
                     value=value, source="deliverables", scope="global",
                     sequence=1)
    return msg, len(json.dumps(msg.to_json()))


class _FakePahoClient:
    def __init__(self):
        self.published = []

    # `retain`/`properties` mirror the real paho signature — the transport now
    # passes both ([BP-MQTT-QOS] retain + the MQTT 5 age contract). These tests
    # are about LOG NOISE at payload size, so the extra arguments are recorded
    # and ignored; retain semantics live in test_transport_mqtt_retain.py.
    def publish(self, topic, payload, qos=0, retain=False, properties=None):
        self.published.append((topic, payload, qos))


@pytest.fixture
def transport():
    t = MQTTTransport(broker="unused", port=1883, client_id="log-noise-test")
    t._client = _FakePahoClient()
    t._connected = True
    return t


def _state(value):
    return BusMessage(namespace="self/<id>/agents", type=MessageType.STATE,
                      value=value, source="agents-skills", scope="global",
                      sequence=1)


def _payload_of(n_bytes: int) -> str:
    """A value whose SERIALIZED envelope comfortably exceeds `n_bytes`."""
    return "x" * n_bytes


def test_a_four_kib_console_payload_logs_at_debug(transport, caplog):
    """The console's normal case: kilobytes, every few seconds, from 20 sources.

    It must not warn — and it must not be silent either, or a substrate-
    portability audit loses the only record that these payloads exist.
    """
    msg = _state(_payload_of(4 * 1024))
    with caplog.at_level(logging.DEBUG, logger="composeio.transport.mqtt"):
        transport._send_now(msg)

    records = [r for r in caplog.records if "bytes" in r.getMessage()]
    assert records, "the size check must still LOG something at 4 KiB"
    assert all(r.levelno == logging.DEBUG for r in records), (
        f"a 4 KiB console payload must not WARN: "
        f"{[(r.levelname, r.getMessage()) for r in records]}")
    assert "DP-CIO-18" in records[0].getMessage(), (
        "the debug line must still name the guideline it is reporting")


def test_a_genuine_runaway_still_warns(transport, caplog):
    """A genuine runaway is still LOUD. The point of raising the threshold is to
    keep THIS line readable, not to stop drawing it."""
    msg = _state(_payload_of(300 * 1024))
    with caplog.at_level(logging.DEBUG, logger="composeio.transport.mqtt"):
        transport._send_now(msg)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, (
        f"a 300 KiB payload must WARN exactly once, got "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}")
    assert "runaway" in warnings[0].getMessage()


def test_the_real_deliverables_payload_does_not_warn(transport, caplog):
    """THE REGRESSION. Built from the real `compute_payload` over a real-sized
    ledger — not from a `"x" * n` probe chosen to sit on one side of a threshold.

    `self/deliverables` is the payload [CXC-SOB-LOG-NOISE] names FIRST in its own
    hazard list, and it was 70,333 bytes on the wire on the commit that picked
    64 KiB — over on day one, and 27,009 warnings later it was 46% of the console
    log. A probe at 4 KiB and one at 128 KiB both passed while that was true. This
    one cannot: it IS the payload.
    """
    msg, size = _real_deliverables_message()
    assert msg.value["stats"]["tracked"] >= 170, (
        "the fixture must stay a REALISTIC ledger; a shrunken one would make "
        "this test pass for the wrong reason")
    assert size > 64 * 1024, (
        f"the fixture no longer reproduces the defect ({size} B): it must exceed "
        "the OLD 64 KiB threshold or it cannot fail on the unfixed tree")

    with caplog.at_level(logging.DEBUG, logger="composeio.transport.mqtt"):
        transport._send_now(msg)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [], (
        f"the real {size}-byte self/deliverables payload is BOUNDED and CORRECT "
        f"(178 rows <-> 178 ledger nodes, no duplicates, every declared cap "
        f"functioning) — warning on it is the defect: "
        f"{[r.getMessage() for r in warnings]}")


def test_an_oversize_namespace_warns_ONCE_not_once_per_send(transport, caplog):
    """The structural half of the fix, and the half that matters.

    Raising the number alone only resets the clock — `all` is an unbounded
    projection of a corpus that grew 104 -> 178 nodes in five days. Once-per-
    namespace is what makes the absolute value non-critical: a re-crossing costs
    ONE line, and that line is then a durable audit record rather than a storm.
    A growth-rate detector was considered and rejected — it needs a rate and a
    window calibrated against payloads its author has in hand, which is the
    identical disease one level up.
    """
    sends = 25
    with caplog.at_level(logging.DEBUG, logger="composeio.transport.mqtt"):
        for _ in range(sends):
            transport._send_now(_state(_payload_of(300 * 1024)))

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, (
        f"{sends} oversize sends on ONE namespace must produce 1 warning, got "
        f"{len(warnings)} — that ratio is the log-noise defect in miniature")
    assert len(transport._client.published) == sends, (
        "suppressing the WARNING must not suppress the MESSAGE")


def test_the_once_per_namespace_guard_is_PER_NAMESPACE(transport, caplog):
    """Not once per process. A second oversized namespace is a second fact and
    must get its own line, or the audit record is incomplete by construction."""
    big = _payload_of(300 * 1024)
    with caplog.at_level(logging.DEBUG, logger="composeio.transport.mqtt"):
        for ns in ("self/deliverables", "thread/x/narration", "self/deliverables"):
            transport._send_now(BusMessage(
                namespace=ns, type=MessageType.STATE, value=big, source="s",
                scope="global", sequence=1))

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    named = sorted(ns for ns in ("self/deliverables", "thread/x/narration")
                   if any(ns in r.getMessage() for r in warnings))
    assert len(warnings) == 2, (
        f"two distinct oversized namespaces must yield two warnings, got "
        f"{[r.getMessage() for r in warnings]}")
    assert named == ["self/deliverables", "thread/x/narration"]


def test_the_warning_makes_no_unmeasured_claim_about_the_cause(transport, caplog):
    """The old text asserted 'this is very likely a bug, not a large payload'.

    It was not measured, it was not true, and it is what made a CALIBRATION MISS
    read as a payload defect for five days while the bounded, correct projection
    it was accusing sat there unchanged. A warning reports a MEASUREMENT; the
    diagnosis is the reader's.
    """
    with caplog.at_level(logging.DEBUG, logger="composeio.transport.mqtt"):
        transport._send_now(_state(_payload_of(300 * 1024)))
    text = " ".join(r.getMessage() for r in caplog.records
                    if r.levelno >= logging.WARNING)
    assert "very likely a bug" not in text, (
        f"the warning still diagnoses instead of measuring: {text}")


def test_a_small_payload_logs_nothing(transport, caplog):
    """Under the guideline: no log at any level. The overwhelming majority of
    bus traffic is small and must stay completely quiet."""
    msg = BusMessage(namespace="self/x", type=MessageType.STATE, value=1,
                     source="s", scope="global", sequence=1)
    with caplog.at_level(logging.DEBUG, logger="composeio.transport.mqtt"):
        transport._send_now(msg)
    assert [r for r in caplog.records if "bytes" in r.getMessage()] == []


def test_the_thresholds_are_module_constants_not_call_arguments():
    """[CXC-SOB-LOG-NOISE] requires a CLASS constant, deliberately.

    A per-call argument would let one noisy call site opt itself out and would
    have to be threaded through every publisher — the fork-divergence shape
    [DX-NARROW-WAIST] exists to prevent. Pinned as a test so a future 'just add a
    quiet=True kwarg' has to argue with the spec first.
    """
    assert MQTTTransport.GUIDELINE_PAYLOAD_BYTES == 256

    import inspect
    sig = inspect.signature(MQTTTransport._send_now)
    assert list(sig.parameters) == ["self", "message"], (
        f"_send_now grew a parameter: {list(sig.parameters)} — the size "
        "threshold must stay a class constant, not a per-call knob")


def test_the_warn_threshold_clears_every_enumerated_payload_MEASURED():
    """The invariant that replaced `== 64 * 1024`.

    The pin was not merely useless, it was ACTIVELY HARMFUL: it made a green
    suite the defence of a live defect, because a magic number can be asserted
    without ever being compared to a payload. The requirement was always
    relational — the threshold must sit above every enumerated console payload,
    MEASURED — so that is what is asserted, against the largest one the spec's
    own hazard list names.

    Deliberately NO headroom multiplier is pinned. A multiplier is a second
    uncalibrated constant, and picking one is how 64 KiB happened (16,465 x 4).
    What makes the absolute value non-critical is the once-per-namespace guard,
    not a fatter margin — see the test above.
    """
    _, size = _real_deliverables_message()
    assert MQTTTransport.LARGE_PAYLOAD_WARN_BYTES > size, (
        f"the runaway threshold ({MQTTTransport.LARGE_PAYLOAD_WARN_BYTES} B) is "
        f"at or below a REAL, BOUNDED, CORRECT console payload "
        f"(self/deliverables = {size} B). Every send of it will WARN.")


def test_the_payload_still_reaches_the_broker_at_every_size(transport):
    """The demotion changes LOGGING only. An oversized payload was never dropped
    and must still not be."""
    for size in (10, 4 * 1024, 128 * 1024, 300 * 1024):
        transport._client.published.clear()
        transport._send_now(_state(_payload_of(size)))
        assert len(transport._client.published) == 1, (
            f"a {size}-byte payload was not published")
        topic, payload, qos = transport._client.published[0]
        assert topic == "composeio/global/self/<id>/agents"
        assert qos == 1, "STATE stays qos 1 ([CXC-SOB-WIRE-DELTAS] delta 3)"
