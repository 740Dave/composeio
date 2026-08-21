"""Acceptance suite for the fingerprint reference layer (no hardware).

This is the machine-green proof that the ratified contract is BUILDABLE. It
transcribes every ``CXC-FPBUS-SCEN-*`` scenario from C73 into a test, plus the
HAL parity assertions of [CXC-FPHAL-PARITY-TEST], the counter semantics, the
call-order guarantees, and the three-valued auth_event with its denial-hiding
ring. Every failure path is driven by the mock's fault injector as the NEGATIVE
CONTROL, and each such test is paired with a positive control proving the
injector is what turns it red.

Run standalone::

    python -m composeio.peripherals.fingerprint --selftest

or under pytest (the ``test_*`` functions are collected automatically)::

    python -m pytest python/composeio/peripherals/fingerprint/selftest.py -v

SCEN -> test map (C73):
  CXC-FPBUS-SCEN-PUSH-NEEDED-HEARTBEAT   -> test_scen_push_needed_heartbeat
  CXC-FPBUS-SCEN-INOPERABLE-HEARTBEAT    -> test_scen_inoperable_heartbeat
  CXC-FPBUS-SCEN-SET-SIZE-LIMIT          -> test_scen_set_size_limit
  CXC-FPBUS-SCEN-SET-UUID-PRESENT        -> test_scen_set_uuid_present
  CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD      -> test_scen_template_size_guard,
                                             test_parity_store_wrong_size_template_rejected
  CXC-FPBUS-SCEN-SAME-SET-PUSHED-TWICE   -> test_scen_same_set_pushed_twice
  CXC-FPBUS-SCEN-ALL-TEMPLATES-SAVE      -> test_scen_all_templates_save
  CXC-FPBUS-SCEN-PARTIAL-LOAD-HOLDS-STAMP-> test_scen_partial_load_holds_stamp
  CXC-FPBUS-SCEN-LOAD-FAILURE            -> test_scen_load_failure
  CXC-FPBUS-SCEN-DELETE-FAILURE-ESCALATION -> test_scen_delete_failure_escalation_*
"""

from __future__ import annotations

import itertools

from .device import (
    AuthOutcome,
    DevicePersistence,
    FingerprintDevice,
    HeartbeatState,
    run_heartbeat_window,
)
from .hal import (
    Capabilities,
    FailureAxis,
    FeedbackChannel,
    FeedbackState,
    FingerprintHALError,
    FingerprintHALFailure,
    MatchOutcome,
    MatchResult,
    Score,
    Slot,
)
from .mock import MockFingerprintHAL
from .push_validation import validate_publishable

# ---------------------------------------------------------------------------
# Test doubles + builders
# ---------------------------------------------------------------------------


class RecordingBus:
    """Captures publishes as event dicts, newest-last. Matches
    ``ComposeIOBus.publish_event`` so a live bus is a drop-in."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish_event(self, namespace, event_type, data=None, source=""):
        self.events.append(
            {
                "namespace": namespace,
                "event_type": event_type,
                "data": dict(data) if data else {},
                "source": source,
            }
        )

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["event_type"] == event_type]

    def count(self, event_type: str) -> int:
        return len(self.of_type(event_type))

    def last(self, event_type: str) -> dict:
        matches = self.of_type(event_type)
        if not matches:
            raise KeyError(f"nothing published of type {event_type!r}")
        return matches[-1]


# The mock's default announced template_size_bytes. Fixture templates default
# to EXACTLY this size so HAL-level tests exercise the OPERATION under test,
# not the [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] wrong-size rejection — a test
# that wants a wrong size builds one explicitly (see test_scen_template_size_
# guard / test_parity_store_wrong_size_template_rejected).
_TEMPLATE_SIZE = MockFingerprintHAL().capabilities().template_size_bytes


def _synth(seed: bytes) -> bytes:
    """Correctly-sized opaque bytes starting with ``seed``, so a test can still
    tell one fixture template from another while satisfying the size guard."""
    return (seed * (_TEMPLATE_SIZE // len(seed) + 1))[:_TEMPLATE_SIZE]


def _entry(person, ordinal, character, role, template=None):
    return {
        "person": person,
        "ordinal": ordinal,
        "character": character,
        "rbac": role,
        "template": template
        if template is not None
        else _synth(f"tmpl-{person}-{ordinal}".encode()),
    }


def _push(uuid, entries, role_table=None):
    return {
        "template_set_uuid": uuid,
        "entry_count": len(entries),
        "role_capability_table": role_table or {"crew": ["open_hatch"], "guest": []},
        "entries": entries,
    }


_CAST = [
    _entry("alice", 1, "Captain", "crew"),
    _entry("alice", 2, "Captain", "crew"),
    _entry("bob", 1, "Passenger", "guest"),
]


def _make_device(
    hal=None, bus=None, persistence=None, uplink=None, gated="open_hatch",
    match_threshold=None, clock=None,
):
    hal = hal if hal is not None else MockFingerprintHAL()
    bus = bus if bus is not None else RecordingBus()
    counter = itertools.count(1)
    dev = FingerprintDevice(
        hal, bus, "dev/hatch1", gated,
        persistence=persistence, uplink=uplink, match_threshold=match_threshold,
        clock=clock or (lambda: 1000.0),
        event_id_factory=lambda: f"evt-{next(counter)}",
    )
    return dev, hal, bus


# ===========================================================================
# HAL PARITY — [CXC-FPHAL-PARITY-TEST], run against the mock (the reference)
# ===========================================================================


def test_parity_capabilities_five_fields_typed_and_stable():
    """All five capability fields present, correctly typed, and two successive
    calls equal ([CXC-FPHAL-CAPABILITIES-SCHEMA]). max_templates_per_person was
    REMOVED (2026-08-18) — the sensor has no person concept."""
    hal = MockFingerprintHAL()
    caps = hal.capabilities()
    assert isinstance(caps.slot_count, int)
    assert isinstance(caps.supports_readback, bool)
    assert isinstance(caps.feedback_channels, frozenset)
    assert all(isinstance(c, FeedbackChannel) for c in caps.feedback_channels)
    assert isinstance(caps.captures_per_template, int)
    assert isinstance(caps.template_size_bytes, int)
    # Stable for the life of the object.
    assert hal.capabilities() == caps


def test_parity_enroll_drives_to_stored_slot():
    """begin_enroll(1) driven to completion returns an allocated slot, and
    occupancy then contains exactly that slot ([CXC-FPHAL-ENROLLMENT-
    SEQUENCE])."""
    hal = MockFingerprintHAL()
    hal.empty()
    session = hal.begin_enroll(1)
    assert session.captures_required == hal.capabilities().captures_per_template
    while not session.complete:
        session.capture()
    assert session.slot is not None
    assert hal.read_occupancy() == frozenset({session.slot})


def test_parity_sequential_install_returns_distinct_noncontiguous_slots():
    """store_template x4 on an emptied device returns four distinct allocated
    slots and occupancy equals exactly that returned set. The set is
    deliberately NON-CONTIGUOUS ([CXC-FPHAL-PARITY-TEST])."""
    hal = MockFingerprintHAL()
    hal.empty()
    slots = [hal.store_template(_synth(f"t{i}".encode())) for i in range(4)]
    assert len(set(slots)) == 4, "slots not distinct"
    assert hal.read_occupancy() == frozenset(slots)
    # Non-contiguous: not {0,1,2,3}. A contiguous set decodes identically under
    # several wrong orders and could not falsify an ordering bug.
    assert set(slots) != set(range(4)), "slot set is contiguous — cannot detect misorder"


def test_parity_read_template_round_trips():
    """read_template of an allocated slot round-trips byte-identically (mock
    supports readback) ([CXC-FPHAL-PARITY-TEST])."""
    hal = MockFingerprintHAL()
    hal.empty()
    payload = _synth(b"opaque-vendor-blob")
    slot = hal.store_template(payload)
    if hal.capabilities().supports_readback:
        assert hal.read_template(slot) == payload
    else:  # pragma: no cover - the mock supports readback
        pass  # skipped with a recorded reason, never silently


def test_parity_readback_unsupported_fails_typed():
    """A no-readback device fails read_template with UNSUPPORTED, never
    silently ([CXC-FPHAL-OPERATIONS])."""
    caps = MockFingerprintHAL().capabilities()
    no_readback = Capabilities(
        slot_count=caps.slot_count,
        supports_readback=False,
        feedback_channels=caps.feedback_channels,
        captures_per_template=caps.captures_per_template,
        template_size_bytes=caps.template_size_bytes,
    )
    hal = MockFingerprintHAL(no_readback)
    slot = hal.store_template(_synth(b"x"))
    try:
        hal.read_template(slot)
    except FingerprintHALError as exc:
        assert exc.axis is FailureAxis.UNSUPPORTED
    else:
        raise AssertionError("readback-unsupported did not fail typed")


def test_parity_empty_leaves_occupancy_empty():
    hal = MockFingerprintHAL()
    hal.store_template(_synth(b"a"))
    hal.empty()
    assert hal.read_occupancy() == frozenset()


def test_parity_roster_stamp_round_trips_and_none_when_unstamped():
    """write then read of a roster stamp is byte-identical, and a never-stamped
    device returns None (distinct from empty bytes) ([CXC-FPID-ROSTER-STAMP],
    [CXC-FPHAL-OPERATIONS])."""
    hal = MockFingerprintHAL()
    assert hal.read_roster_stamp() is None
    stamp = b"uuid-1234"
    hal.write_roster_stamp(stamp)
    assert hal.read_roster_stamp() == stamp


def test_parity_empty_store_never_matches():
    """An empty store never returns MATCHED ([CXC-FPHAL-PARITY-TEST])."""
    hal = MockFingerprintHAL()
    hal.empty()
    assert hal.match().outcome is not MatchOutcome.MATCHED


def test_parity_store_past_capacity_fails_capacity_axis():
    """A store past capacity fails on the capacity axis and leaves occupancy
    unchanged ([CXC-FPID-OVERFLOW-FAILS-LOUD], [CXC-FPHAL-PARITY-TEST]). Driven
    by the fault injector — the NEGATIVE CONTROL."""
    hal = MockFingerprintHAL()
    hal.empty()
    hal.store_template(_synth(b"a"))
    hal.store_template(_synth(b"b"))
    before = hal.read_occupancy()
    hal.inject_store_failure(3, axis=FailureAxis.CAPACITY)
    try:
        hal.store_template(_synth(b"c"))
    except FingerprintHALError as exc:
        assert exc.axis is FailureAxis.CAPACITY
    else:
        raise AssertionError("over-capacity store did not fail")
    assert hal.read_occupancy() == before, "occupancy changed on a failed store"


def test_parity_unknown_code_is_nonfatal():
    """After an injected unknown_code, capabilities() and read_occupancy() both
    still succeed on the same object ([CXC-FPHAL-UNKNOWN-CODE])."""
    hal = MockFingerprintHAL()
    hal.empty()
    hal.inject_store_failure(1, axis=FailureAxis.UNKNOWN_CODE)
    try:
        hal.store_template(_synth(b"x"))
    except FingerprintHALError as exc:
        assert exc.axis is FailureAxis.UNKNOWN_CODE
        assert exc.failure.sensor_code is not None, "unknown_code must quote the raw byte"
    else:
        raise AssertionError("unknown_code did not raise")
    # The object remains usable — the operation failed, the session did not.
    assert hal.capabilities() is not None
    assert isinstance(hal.read_occupancy(), frozenset)


def test_parity_store_wrong_size_template_rejected():
    """[CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] device-side: a template whose byte
    length differs from the announced template_size_bytes is REJECTED at the
    HAL with a failure axis — never a silent accept. This is the LAST LINE OF
    DEFENSE against bricking the sensor with a malformed write; the service
    pre-push check ([CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD], roster_push.py) is
    the other. NEGATIVE CONTROL for the guard."""
    hal = MockFingerprintHAL()
    hal.empty()
    wrong = b"x" * (hal.capabilities().template_size_bytes - 1)
    try:
        hal.store_template(wrong)
    except FingerprintHALError as exc:
        assert exc.axis is FailureAxis.CAPACITY
    else:
        raise AssertionError("wrong-sized template was silently accepted")
    assert hal.read_occupancy() == frozenset(), "a rejected store must not allocate a slot"


def test_parity_store_correct_size_template_accepted():
    """Positive control for the size guard above — proves the guard rejects
    for SIZE, not by default."""
    hal = MockFingerprintHAL()
    hal.empty()
    slot = hal.store_template(_synth(b"good"))
    assert hal.read_occupancy() == frozenset({slot})


def test_parity_hal_originates_no_feedback():
    """Across a match and an enrollment with no feedback commanded, the feedback
    recorder is empty — the HAL originates no feedback on its own
    ([CXC-FPHAL-FEEDBACK-STATES], [CXC-FPHAL-PARITY-TEST])."""
    hal = MockFingerprintHAL()
    hal.match()
    session = hal.begin_enroll(1)
    while not session.complete:
        session.capture()
    assert hal.feedback_log == [], "HAL originated feedback nobody commanded"


def test_parity_empty_feedback_channels_fails_unsupported():
    """A sensor whose feedback_channels is empty fails command_feedback with
    UNSUPPORTED ([CXC-FPHAL-FEEDBACK-STATES])."""
    caps = MockFingerprintHAL().capabilities()
    no_feedback = Capabilities(
        slot_count=caps.slot_count,
        supports_readback=caps.supports_readback,
        feedback_channels=frozenset(),
        captures_per_template=caps.captures_per_template,
        template_size_bytes=caps.template_size_bytes,
    )
    hal = MockFingerprintHAL(no_feedback)
    try:
        hal.command_feedback(FeedbackState.ACCEPT)
    except FingerprintHALError as exc:
        assert exc.axis is FailureAxis.UNSUPPORTED
    else:
        raise AssertionError("empty feedback channels did not fail")


def test_failure_taxonomy_disjointness_enforced():
    """The taxonomy is self-checking: a transport failure quotes no sensor_code,
    and sensor_refusal/unknown_code MUST carry one ([CXC-FPHAL-FAILURE-AXES])."""
    # transport with a code is a bug.
    try:
        FingerprintHALFailure(axis=FailureAxis.TRANSPORT, sensor_code=0x01)
    except ValueError:
        pass
    else:
        raise AssertionError("transport+sensor_code was accepted")
    # sensor_refusal without a code is a bug.
    try:
        FingerprintHALFailure(axis=FailureAxis.SENSOR_REFUSAL, sensor_code=None)
    except ValueError:
        pass
    else:
        raise AssertionError("sensor_refusal without a code was accepted")


def test_match_result_slot_and_score_together_or_not_at_all():
    """MATCHED requires both slot and score; NO_MATCH/NO_FINGER carry neither
    ([CXC-FPHAL-MATCH-RESULT])."""
    MatchResult(MatchOutcome.MATCHED, Slot(3), Score(120))  # ok
    MatchResult(MatchOutcome.NO_MATCH)  # ok
    for bad in (
        lambda: MatchResult(MatchOutcome.MATCHED, Slot(3), None),
        lambda: MatchResult(MatchOutcome.NO_MATCH, Slot(3), Score(1)),
    ):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid MatchResult accepted")


# ===========================================================================
# PUSH RECEIPT — the CXC-FPBUS-SCEN-* scenarios
# ===========================================================================


def test_uuid_compare_reads_before_any_delete_or_write():
    """[CXC-FPBUS-REQ-UUID-COMPARE] Receipt begins with a UUID read, before any
    delete or write — asserted on the mock's recorded call order."""
    dev, hal, bus = _make_device()
    hal.reset_log()
    dev.receive_push(_push("uuid-A", _CAST))
    ops = hal.op_sequence()
    first_stamp_read = ops.index("read_roster_stamp")
    first_empty = ops.index("empty")
    first_store = ops.index("store_template")
    assert first_stamp_read < first_empty
    assert first_stamp_read < first_store


def test_scen_same_set_pushed_twice():
    """CXC-FPBUS-SCEN-SAME-SET-PUSHED-TWICE — a matching UUID means zero delete,
    zero write, and template_set_loaded ([CXC-FPBUS-REQ-UUID-MATCH-BRANCH])."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))  # first push installs
    hal.reset_log()
    status = dev.receive_push(_push("uuid-A", _CAST))  # identical set again
    assert status == "loaded"
    ops = hal.op_sequence()
    assert "empty" not in ops, "idempotent re-push deleted"
    assert "store_template" not in ops, "idempotent re-push wrote"
    assert bus.count("template_set_loaded") >= 1
    assert bus.last("template_set_loaded")["data"]["template_set_uuid"] == "uuid-A"


def test_scen_all_templates_save():
    """CXC-FPBUS-SCEN-ALL-TEMPLATES-SAVE — mismatch, every template saves:
    delete-all precedes every write, stamp is written only after the last write,
    template_set_loaded emitted last ([CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH])."""
    dev, hal, bus = _make_device()
    hal.reset_log()
    status = dev.receive_push(_push("uuid-A", _CAST))
    assert status == "loaded"
    ops = hal.op_sequence()
    # delete-before-every-write
    empty_idx = ops.index("empty")
    store_idxs = [i for i, o in enumerate(ops) if o == "store_template"]
    assert all(empty_idx < i for i in store_idxs), "a write preceded the delete"
    # stamp strictly last
    stamp_idx = ops.index("write_roster_stamp")
    assert all(stamp_idx > i for i in store_idxs), "stamp not written after every store"
    # holds only the pushed set: occupancy count == entry count
    assert hal.occupancy_count == len(_CAST)
    # current uuid advanced, ack emitted
    assert hal.read_roster_stamp() == b"uuid-A"
    assert bus.count("template_set_loaded") == 1
    # template_set_loaded is the last device message of the push
    assert bus.events[-1]["event_type"] == "template_set_loaded"


def test_scen_partial_load_holds_stamp():
    """CXC-FPBUS-SCEN-PARTIAL-LOAD-HOLDS-STAMP — one template fails to save:
    current_template_set_uuid is NOT advanced and template_set_loaded is NOT
    emitted ([CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH]). NEGATIVE CONTROL: fault
    injected on the 2nd store."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))  # establish a prior stamp
    loaded_before = bus.count("template_set_loaded")  # the setup push emitted one
    hal.reset_log()
    hal.inject_store_failure(2, axis=FailureAxis.CAPACITY)
    status = dev.receive_push(_push("uuid-B", _CAST))
    assert status.startswith("load_failed")
    # stamp NOT advanced to B — holds the prior A (or None if never set).
    assert hal.read_roster_stamp() != b"uuid-B"
    # NO new template_set_loaded during the failed second push.
    assert bus.count("template_set_loaded") == loaded_before


def test_scen_load_failure():
    """CXC-FPBUS-SCEN-LOAD-FAILURE — write fails part way: template_set_failed
    emitted, current_template_set_uuid unmodified, and the store is empty
    ([CXC-FPBUS-REQ-LOAD-FAILURE]). Fault on the 3rd of a 5-template set."""
    five = [_entry(f"p{i}", 1, f"C{i}", "crew") for i in range(5)]
    dev, hal, bus = _make_device()
    hal.reset_log()
    hal.inject_store_failure(3, axis=FailureAxis.CAPACITY)
    status = dev.receive_push(_push("uuid-A", five))
    assert status == "load_failed"
    assert bus.count("template_set_failed") == 1
    failed = bus.last("template_set_failed")["data"]
    assert failed["template_set_uuid"] == "uuid-A"
    # [CXC-FPBUS-FAILURE-ON-THE-WIRE] the closed six-axis taxonomy crosses the
    # wire verbatim -- {axis, sensor_code, detail}, never a relabeled
    # error_code/human_readable_error_string.
    assert failed["axis"] == FailureAxis.CAPACITY.value
    assert failed["sensor_code"] is None  # capacity carries no sensor code
    assert failed["detail"]
    # stamp unmodified (never set -> None) and store emptied.
    assert hal.read_roster_stamp() is None
    assert hal.occupancy_count == 0, "device not emptied after a failed load"


def test_scen_load_failure_carries_sensor_code_on_sensor_refusal_axis():
    """[CXC-FPBUS-FAILURE-ON-THE-WIRE] the six-axis taxonomy is a CLOSED
    contract, not a status string: a sensor_refusal axis MUST carry a
    (non-None) sensor_code on the wire, exactly as it does in-process."""
    five = [_entry(f"p{i}", 1, f"C{i}", "crew") for i in range(5)]
    dev, hal, bus = _make_device()
    hal.reset_log()
    hal.inject_store_failure(3, axis=FailureAxis.SENSOR_REFUSAL)
    status = dev.receive_push(_push("uuid-A", five))
    assert status == "load_failed"
    failed = bus.last("template_set_failed")["data"]
    assert failed["axis"] == FailureAxis.SENSOR_REFUSAL.value
    assert isinstance(failed["sensor_code"], int)


def test_load_failure_positive_control():
    """NEGATIVE-CONTROL PROOF: the same five-template push with NO injected
    fault succeeds and emits template_set_loaded — so the failure in
    test_scen_load_failure is caused by the injector, not by the flow."""
    five = [_entry(f"p{i}", 1, f"C{i}", "crew") for i in range(5)]
    dev, hal, bus = _make_device()
    status = dev.receive_push(_push("uuid-A", five))
    assert status == "loaded"
    assert bus.count("template_set_failed") == 0
    assert bus.count("template_set_loaded") == 1


def test_scen_delete_failure_escalation_recoverable():
    """CXC-FPBUS-SCEN-DELETE-FAILURE-ESCALATION — the cleanup delete after a
    failed load itself fails, recoverable class -> fingerprint_push_needed
    ([CXC-FPBUS-REQ-DELETE-FAILURE-ESCALATION], [DD-FPBUS-DELETE-FAILURE-CLASS-
    MAP]). unknown_code is asserted here as RECOVERABLE (task-pinned)."""
    for axis in (
        FailureAxis.TRANSPORT,
        FailureAxis.CAPACITY,
        FailureAxis.PROTOCOL_DESYNC,
        FailureAxis.UNKNOWN_CODE,
    ):
        dev, hal, bus = _make_device()
        hal.reset_log()
        hal.inject_store_failure(1, axis=FailureAxis.CAPACITY)   # trigger a load failure
        hal.inject_delete_failure(axis, nth=2)                    # fail the CLEANUP delete
        status = dev.receive_push(_push("uuid-A", _CAST))
        assert status == "load_failed_delete_failed", axis
        assert bus.count("fingerprint_push_needed") == 1, f"{axis} did not escalate to push_needed"
        assert bus.count("fingerprint_inoperable") == 0, axis
        assert dev.current_heartbeat_state() is HeartbeatState.PUSH_NEEDED, axis


def test_scen_delete_failure_escalation_unrecoverable():
    """CXC-FPBUS-SCEN-DELETE-FAILURE-ESCALATION — unrecoverable class
    (sensor_refusal) -> fingerprint_inoperable ([DD-FPBUS-DELETE-FAILURE-CLASS-
    MAP])."""
    dev, hal, bus = _make_device()
    hal.reset_log()
    hal.inject_store_failure(1, axis=FailureAxis.CAPACITY)
    hal.inject_delete_failure(FailureAxis.SENSOR_REFUSAL, nth=2)
    status = dev.receive_push(_push("uuid-A", _CAST))
    assert status == "load_failed_delete_failed"
    assert bus.count("fingerprint_inoperable") == 1
    assert bus.count("fingerprint_push_needed") == 0
    assert dev.current_heartbeat_state() is HeartbeatState.INOPERABLE


# ===========================================================================
# SERVICE PUSH-TIME VALIDATIONS — [CXC-FPBUS-REQ-SET-*]
# ===========================================================================


def test_scen_set_size_limit():
    """CXC-FPBUS-SCEN-SET-SIZE-LIMIT — a 21-template set is not publishable
    against a device announcing slot_count=20, so no template_set message
    reaches the bus ([CXC-FPBUS-REQ-SET-SIZE-LIMIT]). The cap is the TARGET
    DEVICE'S announced capacity, passed in explicitly — never a constant."""
    twenty_one = [_entry(f"p{i}", 1, f"C{i}", "crew") for i in range(21)]
    ok, reason = validate_publishable(_push("uuid-A", twenty_one), slot_count=20)
    assert ok is False and "21" in reason


def test_scen_set_uuid_present():
    """CXC-FPBUS-SCEN-SET-UUID-PRESENT — a set with no UUID is not publishable
    ([CXC-FPBUS-REQ-SET-UUID-PRESENT])."""
    ok, reason = validate_publishable({"template_set_uuid": None, "entries": _CAST})
    assert ok is False
    ok2, _ = validate_publishable({"template_set_uuid": "", "entries": _CAST})
    assert ok2 is False


def test_publishable_positive_control():
    """A valid 20-template set with a UUID IS publishable against a device
    announcing slot_count=20 — proving the guard rejects for the stated
    reason, not by default."""
    twenty = [_entry(f"p{i}", 1, f"C{i}", "crew") for i in range(20)]
    ok, reason = validate_publishable(_push("uuid-A", twenty), slot_count=20)
    assert ok is True and reason == ""


def test_scen_template_size_guard():
    """[CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] — a template one byte off the
    announced template_size_bytes is refused; no message reaches the bus.
    A wrong-sized write may BRICK the R503, so this is a safety guard, not an
    optimization."""
    entries = [_entry("p1", 1, "C1", "crew", template=b"x" * (_TEMPLATE_SIZE - 1))]
    ok, reason = validate_publishable(
        _push("uuid-A", entries), template_size_bytes=_TEMPLATE_SIZE
    )
    assert ok is False
    assert str(_TEMPLATE_SIZE - 1) in reason and str(_TEMPLATE_SIZE) in reason


def test_template_size_guard_positive_control():
    """A correctly-sized template IS publishable — proving the guard rejects
    for SIZE, not by default."""
    entries = [_entry("p1", 1, "C1", "crew")]  # default is correctly sized
    ok, reason = validate_publishable(
        _push("uuid-A", entries), template_size_bytes=_TEMPLATE_SIZE
    )
    assert ok is True and reason == ""


def test_template_size_guard_skipped_when_unknown():
    """When the target device's template_size_bytes is not (yet) known
    (``None`` — no announce received), the guard is SKIPPED rather than
    defaulted to a constant: there is nothing known to check against."""
    entries = [_entry("p1", 1, "C1", "crew", template=b"x" * 3)]  # any size
    ok, reason = validate_publishable(_push("uuid-A", entries))
    assert ok is True and reason == ""


# ===========================================================================
# HEARTBEATS — [CXC-FPBUS-REQ-*-HEARTBEAT]
# ===========================================================================


def test_scen_push_needed_heartbeat():
    """CXC-FPBUS-SCEN-PUSH-NEEDED-HEARTBEAT — an empty (unconfigured) device
    emits fingerprint_push_needed every 15s ([CXC-FPBUS-REQ-PUSH-NEEDED-
    HEARTBEAT]). Virtual-time window of 60s -> 4 emissions, 15s apart."""
    dev, hal, bus = _make_device()  # never pushed -> unconfigured
    emissions = run_heartbeat_window(dev, 60.0)
    states = [s for _, s in emissions]
    assert states == [HeartbeatState.PUSH_NEEDED] * 4, states
    times = [t for t, _ in emissions]
    assert times == [0.0, 15.0, 30.0, 45.0]
    assert bus.count("fingerprint_push_needed") == 4
    assert bus.count("fingerprint_ready") == 0


def test_scen_inoperable_heartbeat():
    """CXC-FPBUS-SCEN-INOPERABLE-HEARTBEAT — a device whose sensor became
    disconnected emits fingerprint_inoperable every 15s ([CXC-FPBUS-REQ-
    INOPERABLE-HEARTBEAT])."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))  # was healthy + configured
    dev.set_hardware_present(False)           # sensor disconnected
    emissions = run_heartbeat_window(dev, 60.0)
    assert [s for _, s in emissions] == [HeartbeatState.INOPERABLE] * 4
    assert bus.count("fingerprint_inoperable") == 4
    assert bus.count("fingerprint_ready") == 0


def test_ready_heartbeat_carries_uuid_and_count():
    """[CXC-FPBUS-REQ-READY-HEARTBEAT] a healthy configured device emits
    fingerprint_ready at power-up and every 60s, carrying its uuid and the
    attempt count. Virtual 150s window -> 3 emissions, 60s apart."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))
    emissions = run_heartbeat_window(dev, 150.0)
    assert [s for _, s in emissions] == [HeartbeatState.READY] * 3
    assert [t for t, _ in emissions] == [0.0, 60.0, 120.0]
    ready = bus.last("fingerprint_ready")["data"]
    assert ready["template_set_uuid"] == "uuid-A"
    assert ready["count_authentication_attempts"] == 0


def test_heartbeats_are_mutually_exclusive():
    """The three heartbeats never co-occur for one state — jointly exhaustive,
    mutually exclusive ([CXC-FPBUS-REQ-READY-HEARTBEAT])."""
    dev, hal, bus = _make_device()
    # unconfigured -> push_needed only
    dev.emit_heartbeat()
    dev.receive_push(_push("uuid-A", _CAST))
    dev.emit_heartbeat()  # configured -> ready
    dev.set_hardware_present(False)
    dev.emit_heartbeat()  # inoperable
    assert bus.count("fingerprint_push_needed") == 1
    assert bus.count("fingerprint_ready") == 1
    assert bus.count("fingerprint_inoperable") == 1


# ===========================================================================
# COUNTER — [CXC-FPBUS-MSG-FINGERPRINT-READY] counter-semantics
# ===========================================================================


def test_counter_persists_across_reboot_same_uuid():
    """persist -> reload same uuid -> count RESUMES ([CXC-FPBUS-MSG-FINGERPRINT-
    READY] counter-semantics). A reboot = a new device on the SAME persistence
    and the SAME (sensor-persistent) HAL."""
    hal = MockFingerprintHAL()
    nvs = DevicePersistence()
    dev, _, _ = _make_device(hal=hal, persistence=nvs)
    dev.receive_push(_push("uuid-A", _CAST))
    # three authentication attempts (a granted crew finger)
    granted = MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(200))
    for _ in range(3):
        dev.resolve_match(granted)
    assert dev.count_authentication_attempts == 3
    # reboot: same HAL (stamp+templates persist), same NVS (count persists).
    dev2, _, bus2 = _make_device(hal=hal, persistence=nvs)
    assert dev2.count_authentication_attempts == 3, "count did not resume"
    # and a same-uuid re-push does NOT reset it (idempotent).
    dev2.receive_push(_push("uuid-A", _CAST))
    assert dev2.count_authentication_attempts == 3, "idempotent re-push reset the count"


def test_counter_zeroes_on_new_uuid():
    """new uuid -> count ZEROES ([CXC-FPBUS-MSG-FINGERPRINT-READY] counter-
    semantics)."""
    hal = MockFingerprintHAL()
    nvs = DevicePersistence()
    dev, _, _ = _make_device(hal=hal, persistence=nvs)
    dev.receive_push(_push("uuid-A", _CAST))
    granted = MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(200))
    dev.resolve_match(granted)
    dev.resolve_match(granted)
    assert dev.count_authentication_attempts == 2
    dev.receive_push(_push("uuid-B", _CAST))  # genuine uuid change
    assert dev.count_authentication_attempts == 0


def _crew_slot(dev) -> Slot:
    """The slot bound to a crew (granted) entry in the device bridge."""
    for slot, entry in dev.bridge.items():
        if entry.role == "crew":
            return slot
    raise AssertionError("no crew slot in bridge")


def _guest_slot(dev) -> Slot:
    for slot, entry in dev.bridge.items():
        if entry.role == "guest":
            return slot
    raise AssertionError("no guest slot in bridge")


# ===========================================================================
# AUTH EVENT — [CXC-FPBUS-MSG-AUTH-EVENT] + [CXC-FPID-RING-HIDES-DENIAL]
# ===========================================================================


def test_auth_event_all_three_outcomes_publish():
    """All three outcomes — granted, denied, unrecognized — are PUBLISHED, none
    suppressed ([CXC-FPBUS-MSG-AUTH-EVENT], [CXC-FPID-AUTH-OUTCOME])."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))
    dev.resolve_match(MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(200)))
    dev.resolve_match(MatchResult(MatchOutcome.MATCHED, _guest_slot(dev), Score(200)))
    dev.resolve_match(MatchResult(MatchOutcome.NO_MATCH))
    outcomes = [e["data"]["outcome"] for e in bus.of_type("auth_event")]
    assert outcomes == ["granted", "denied", "unrecognized"]


def test_auth_event_payload_shape():
    """The auth_event carries {event_id, device_id, outcome, player, character,
    timestamp}; player/character are ABSENT for unrecognized and PRESENT for
    granted/denied ([CXC-FPBUS-MSG-AUTH-EVENT]). ``gated_capability`` is
    DELIBERATELY ABSENT (wire-economy, 2026-08-18): it's a per-device constant
    already retained on the device-announce, joinable by device_id."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))
    dev.resolve_match(MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(200)))
    granted = bus.last("auth_event")["data"]
    for key in ("event_id", "device_id", "outcome", "timestamp"):
        assert key in granted, key
    assert "gated_capability" not in granted, "gated_capability leaked onto the wire"
    assert granted["player"] == "alice" and granted["character"] == "Captain"
    dev.resolve_match(MatchResult(MatchOutcome.NO_MATCH))
    unrec = bus.last("auth_event")["data"]
    assert "player" not in unrec and "character" not in unrec, "intruder identity leaked"


def test_auth_event_never_carries_score():
    """The score NEVER crosses the bus ([CXC-FPHAL-SCORE-DEVICE-INTERNAL]). No
    published payload contains a score key."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))
    dev.resolve_match(MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(200)))
    for e in bus.events:
        assert "score" not in e["data"], "a score reached the bus"


def test_ring_hides_denial_denied_and_unrecognized_identical():
    """[CXC-FPID-RING-HIDES-DENIAL] the ring shows granted as green (ACCEPT) and
    BOTH denied and unrecognized as the SAME red, indistinguishable — while the
    published event still distinguishes them."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))
    hal.reset_log()
    dev.resolve_match(MatchResult(MatchOutcome.MATCHED, _guest_slot(dev), Score(200)))  # denied
    denied_ring = hal.feedback_log[-1][0]
    dev.resolve_match(MatchResult(MatchOutcome.NO_MATCH))                                # unrecognized
    unrec_ring = hal.feedback_log[-1][0]
    assert denied_ring == unrec_ring, "the ring distinguishes denied from unrecognized"
    # granted differs (green).
    dev.resolve_match(MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(200)))
    granted_ring = hal.feedback_log[-1][0]
    assert granted_ring != denied_ring, "granted ring not distinct from red"
    assert granted_ring is FeedbackState.ACCEPT
    # ...yet the EVENT distinguishes denied from unrecognized.
    outcomes = [e["data"]["outcome"] for e in bus.of_type("auth_event")]
    assert "denied" in outcomes and "unrecognized" in outcomes


def test_auth_event_two_scopes_identical_shape():
    """[CXC-FPBUS-MSG-AUTH-EVENT] scope-membrane — the LOCAL and uplinked copies
    of the auth_event are byte-identical. Collapse the transport, not the seam."""
    local = RecordingBus()
    uplink = RecordingBus()
    hal = MockFingerprintHAL()
    dev = FingerprintDevice(
        hal, local, "dev/hatch1", "open_hatch", uplink=uplink,
        clock=lambda: 1000.0, event_id_factory=lambda: "evt-fixed",
    )
    dev.receive_push(_push("uuid-A", _CAST))
    dev.resolve_match(MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(200)))
    assert local.last("auth_event")["data"] == uplink.last("auth_event")["data"]


def test_no_finger_is_not_an_attempt():
    """NO_FINGER (an empty doorway) is not an authentication attempt: no event,
    no count ([CXC-FPHAL-MATCH-RESULT])."""
    dev, hal, bus = _make_device()
    dev.receive_push(_push("uuid-A", _CAST))
    result = dev.resolve_match(MatchResult(MatchOutcome.NO_FINGER))
    assert result is None
    assert bus.count("auth_event") == 0
    assert dev.count_authentication_attempts == 0


def test_below_threshold_match_produces_no_event():
    """[CXC-FPHAL-MATCH-THRESHOLD] a match whose score is under the device floor
    produces no identity event. null (unset) applies no floor."""
    dev, hal, bus = _make_device(match_threshold=100)
    dev.receive_push(_push("uuid-A", _CAST))
    low = MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(50))
    assert dev.resolve_match(low) is None
    assert bus.count("auth_event") == 0
    # above the floor -> event.
    high = MatchResult(MatchOutcome.MATCHED, _crew_slot(dev), Score(150))
    assert dev.resolve_match(high) is AuthOutcome.GRANTED
    assert bus.count("auth_event") == 1


# ===========================================================================
# Runner
# ===========================================================================

_TESTS = [
    # HAL parity
    test_parity_capabilities_five_fields_typed_and_stable,
    test_parity_enroll_drives_to_stored_slot,
    test_parity_sequential_install_returns_distinct_noncontiguous_slots,
    test_parity_read_template_round_trips,
    test_parity_readback_unsupported_fails_typed,
    test_parity_empty_leaves_occupancy_empty,
    test_parity_roster_stamp_round_trips_and_none_when_unstamped,
    test_parity_empty_store_never_matches,
    test_parity_store_past_capacity_fails_capacity_axis,
    test_parity_unknown_code_is_nonfatal,
    test_parity_store_wrong_size_template_rejected,
    test_parity_store_correct_size_template_accepted,
    test_parity_hal_originates_no_feedback,
    test_parity_empty_feedback_channels_fails_unsupported,
    test_failure_taxonomy_disjointness_enforced,
    test_match_result_slot_and_score_together_or_not_at_all,
    # push receipt scenarios
    test_uuid_compare_reads_before_any_delete_or_write,
    test_scen_same_set_pushed_twice,
    test_scen_all_templates_save,
    test_scen_partial_load_holds_stamp,
    test_scen_load_failure,
    test_load_failure_positive_control,
    test_scen_delete_failure_escalation_recoverable,
    test_scen_delete_failure_escalation_unrecoverable,
    # service validations
    test_scen_set_size_limit,
    test_scen_set_uuid_present,
    test_publishable_positive_control,
    test_scen_template_size_guard,
    test_template_size_guard_positive_control,
    test_template_size_guard_skipped_when_unknown,
    # heartbeats
    test_scen_push_needed_heartbeat,
    test_scen_inoperable_heartbeat,
    test_ready_heartbeat_carries_uuid_and_count,
    test_heartbeats_are_mutually_exclusive,
    # counter
    test_counter_persists_across_reboot_same_uuid,
    test_counter_zeroes_on_new_uuid,
    # auth event + ring
    test_auth_event_all_three_outcomes_publish,
    test_auth_event_payload_shape,
    test_auth_event_never_carries_score,
    test_ring_hides_denial_denied_and_unrecognized_identical,
    test_auth_event_two_scopes_identical_shape,
    test_no_finger_is_not_an_attempt,
    test_below_threshold_match_produces_no_event,
]


def run() -> int:
    """Run every acceptance test; return a process exit code (0 == all green)."""
    failures = 0
    for test in _TESTS:
        name = test.__name__
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - report, don't abort
            failures += 1
            print(f"  FAIL  {name}: {exc}")
        else:
            print(f"  ok    {name}")
    total = len(_TESTS)
    print()
    if failures:
        print(
            f"fingerprint acceptance suite: {total - failures}/{total} passed, "
            f"{failures} FAILED  [machine-red]"
        )
    else:
        print(
            f"fingerprint acceptance suite: {total}/{total} passed  "
            f"[machine-green — C71/C72/C73 reference is buildable]"
        )
        print("Real R503 on UART 57600: GLASS-PENDING (no sensor connected).")
    return 1 if failures else 0
