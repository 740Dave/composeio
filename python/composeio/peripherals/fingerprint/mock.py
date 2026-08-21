"""MockFingerprintHAL — THE REFERENCE — [CXC-FPHAL-MOCK-ROLE].

The mock is not a stub. It is the canonical realization of the HAL contract:
where the mock and a backend disagree, the mock is right ([CXC-FPHAL-MOCK-ROLE],
[CXC-FPHAL-PARITY-TEST]). Two properties earn it that status:

  1. FAULT INJECTION — it can produce a no-match, a capture-retry, and each of
     the six failure axes on demand ([CXC-FPHAL-MOCK-ROLE]). This is the
     NEGATIVE CONTROL: a failure test run against a mock that cannot fail
     proves nothing ([CXC-FPHAL-PARITY-TEST] acceptance-machine).
  2. CALL-ORDER RECORDING — every HAL call lands, in order, in :attr:`call_log`,
     so a test can assert delete-before-write and stamp-strictly-last on the
     recorded sequence ([CXC-FPBUS-REQ-UUID-COMPARE],
     [CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH]).

The mock allocates a NON-CONTIGUOUS slot set deliberately: a contiguous set
{0,1,2,3} decodes identically under several wrong install orders, so it could
not falsify an ordering bug ([CXC-FPHAL-PARITY-TEST]).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .hal import (
    Capabilities,
    EnrollmentSession,
    FailureAxis,
    FeedbackChannel,
    FeedbackState,
    FingerprintHALError,
    FingerprintHALFailure,
    MatchOutcome,
    MatchResult,
    Ordinal,
    RosterStamp,
    Slot,
    TemplateBytes,
)

# R503-shaped defaults (C70: a 200-template on-chip store). These are the
# MOCK'S OWN announced values; parity checks their type and stability, never
# a specific number, so a backend announces its own.
_DEFAULT_CAPABILITIES = Capabilities(
    slot_count=200,
    supports_readback=True,
    feedback_channels=frozenset({FeedbackChannel.AURA_LED}),
    captures_per_template=2,
    template_size_bytes=1536,
)


@dataclass
class _CallRecord:
    """One HAL call, newest-last in :attr:`MockFingerprintHAL.call_log`."""

    op: str
    detail: str = ""


class _MockEnrollmentSession:
    """A driven ceremony that commands NO feedback of its own — feedback is the
    device firmware's job ([CXC-FPHAL-FEEDBACK-STATES]). On the final capture it
    asks the HAL to allocate+write, exactly like a template install."""

    def __init__(self, hal: "MockFingerprintHAL", ordinal: Ordinal):
        self._hal = hal
        self._ordinal = ordinal
        self._required = hal.capabilities().captures_per_template
        self._done = 0
        self._slot: Slot | None = None
        self._complete = False

    @property
    def captures_required(self) -> int:
        return self._required

    @property
    def captures_done(self) -> int:
        return self._done

    @property
    def complete(self) -> bool:
        return self._complete

    @property
    def slot(self) -> Slot | None:
        return self._slot

    def capture(self) -> int:
        if self._complete:
            raise RuntimeError("enrollment already complete")
        self._done += 1
        if self._done >= self._required:
            # The ceremony allocates a slot and writes the captured template.
            template = self._hal._synth_template(f"enroll-ord{self._ordinal}")
            self._slot = self._hal.store_template(template)
            self._complete = True
        return self._done

    def abandon(self) -> None:
        # Ends without a write ([CXC-FPHAL-ENROLLMENT-SEQUENCE]).
        self._complete = True


class MockFingerprintHAL:
    """In-memory, hardware-free reference realization of the HAL contract.

    The device ALLOCATES slots: ``store_template`` returns the allocated Slot
    and takes no caller-chosen index. The lifecycle is wipe-then-sequential-
    install — ``empty()`` then ``store_template`` repeatedly — with no random-
    access slot op ([CXC-FPHAL-OPERATIONS]).
    """

    def __init__(self, capabilities: Capabilities | None = None):
        self._caps = capabilities or _DEFAULT_CAPABILITIES
        self._store: dict[Slot, TemplateBytes] = {}
        self._stamp: RosterStamp | None = None
        self._alloc_index = 0  # advances per successful install, across empties
        # call-order + feedback recorders
        self.call_log: list[_CallRecord] = []
        self.feedback_log: list[tuple[FeedbackState, tuple[int, int] | None]] = []
        # fault injection
        self._store_fault: dict[int, FailureAxis] = {}   # nth store (1-based) -> axis
        self._store_call_n = 0
        self._delete_fault: FailureAxis | None = None
        self._delete_fault_nth: int | None = None        # which empty() call fails; None = every
        self._empty_call_n = 0
        self._readback_supported = self._caps.supports_readback
        # scripted runtime face
        self._scripted_matches: list[MatchResult] = []

    # ---- helpers ----------------------------------------------------------

    def _synth_template(self, seed: str) -> TemplateBytes:
        """Deterministic opaque bytes of the announced template size."""
        raw = (seed.encode() * self._caps.template_size_bytes)[
            : self._caps.template_size_bytes
        ]
        return raw

    def _next_slot(self) -> Slot:
        """Allocate a NON-CONTIGUOUS slot deterministically. (7*k+3) % N is a
        bijection for k < N when gcd(7, N)==1, so successive installs land in
        {3,10,17,24,...} — distinct and gappy, never {0,1,2,3}."""
        n = self._caps.slot_count
        slot = Slot((7 * self._alloc_index + 3) % n)
        self._alloc_index += 1
        return slot

    def _record(self, op: str, detail: str = "") -> None:
        self.call_log.append(_CallRecord(op, detail))

    # ---- fault injection (the NEGATIVE CONTROL) ---------------------------

    def inject_store_failure(self, nth: int, axis: FailureAxis = FailureAxis.CAPACITY) -> None:
        """Make the ``nth`` (1-based) ``store_template`` call of the device's
        current install sequence fail on ``axis``. The install-order counter
        resets on every ``empty()`` (the first move of a mismatch push), so
        ``nth`` numbers within one push."""
        self._store_fault[nth] = axis

    def inject_delete_failure(
        self, axis: FailureAxis, nth: int | None = None
    ) -> None:
        """Make ``empty()`` fail on ``axis``. ``nth`` selects which ``empty()``
        call fails (1-based); None fails every ``empty()``. The delete-failure
        escalation scenario ([CXC-FPBUS-REQ-DELETE-FAILURE-ESCALATION]) uses
        ``nth=2`` to fail only the CLEANUP delete that follows a failed load,
        leaving the pre-install wipe to succeed."""
        self._delete_fault = axis
        self._delete_fault_nth = nth

    def clear_faults(self) -> None:
        self._store_fault.clear()
        self._delete_fault = None
        self._delete_fault_nth = None

    def script_match(self, result: MatchResult) -> None:
        """Queue a MatchResult the next ``match()`` returns. Empty queue ->
        NO_FINGER (an empty doorway)."""
        self._scripted_matches.append(result)

    # ---- [CXC-FPHAL-OPERATIONS] the ten operations ------------------------

    def capabilities(self) -> Capabilities:
        self._record("capabilities")
        return self._caps

    def match(self) -> MatchResult:
        self._record("match")
        if self._scripted_matches:
            return self._scripted_matches.pop(0)
        return MatchResult(MatchOutcome.NO_FINGER)

    def begin_enroll(self, ordinal: Ordinal) -> EnrollmentSession:
        self._record("begin_enroll", f"ordinal={ordinal}")
        return _MockEnrollmentSession(self, ordinal)

    def store_template(self, template: TemplateBytes) -> Slot:
        # [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] the LAST LINE OF DEFENSE: a
        # wrong-sized write may BRICK a real sensor, so the device rejects it
        # here even though the service already checked before publishing. A
        # size mismatch is refused BEFORE the fault injector or the install
        # counter sees the call — a malformed template is never a candidate
        # for a scripted failure, it is simply invalid.
        if len(template) != self._caps.template_size_bytes:
            self._record(
                "store_template",
                f"REJECTED wrong-size len={len(template)} "
                f"expected={self._caps.template_size_bytes}",
            )
            raise FingerprintHALError(
                _failure(
                    FailureAxis.CAPACITY,
                    f"template is {len(template)} bytes, device requires "
                    f"{self._caps.template_size_bytes}",
                )
            )
        self._store_call_n += 1
        n = self._store_call_n
        if n in self._store_fault:
            axis = self._store_fault[n]
            self._record("store_template", f"INJECTED-FAIL n={n} axis={axis.value}")
            raise FingerprintHALError(
                _failure(axis, f"injected store failure on install #{n}")
            )
        slot = self._next_slot()
        self._store[slot] = bytes(template)
        self._record("store_template", f"n={n} -> slot={slot}")
        return slot

    def read_template(self, slot: Slot) -> TemplateBytes:
        self._record("read_template", f"slot={slot}")
        if not self._readback_supported:
            raise FingerprintHALError(
                _failure(FailureAxis.UNSUPPORTED, "readback not supported", sensor_code=None)
            )
        if slot not in self._store:
            raise FingerprintHALError(
                _failure(FailureAxis.CAPACITY, f"slot {slot} out of range/empty")
            )
        return self._store[slot]

    def empty(self) -> None:
        self._empty_call_n += 1
        n = self._empty_call_n
        fail = self._delete_fault is not None and (
            self._delete_fault_nth is None or self._delete_fault_nth == n
        )
        if fail:
            axis = self._delete_fault
            self._record("empty", f"INJECTED-FAIL n={n} axis={axis.value}")
            raise FingerprintHALError(
                _failure(axis, f"injected delete failure on empty #{n}")
            )
        self._store.clear()
        # A fresh install sequence begins after every wipe.
        self._store_call_n = 0
        self._record("empty", f"n={n}")

    def read_occupancy(self) -> frozenset[Slot]:
        self._record("read_occupancy")
        return frozenset(self._store.keys())

    def write_roster_stamp(self, stamp: RosterStamp) -> None:
        self._stamp = bytes(stamp)
        self._record("write_roster_stamp", f"len={len(stamp)}")

    def read_roster_stamp(self) -> RosterStamp | None:
        self._record("read_roster_stamp")
        return self._stamp

    def command_feedback(
        self, state: FeedbackState, progress: tuple[int, int] | None = None
    ) -> None:
        if not self._caps.feedback_channels:
            raise FingerprintHALError(
                _failure(FailureAxis.UNSUPPORTED, "no feedback channels", sensor_code=None)
            )
        self.feedback_log.append((state, progress))
        self._record("command_feedback", state.value)

    # ---- test-facing introspection ---------------------------------------

    @property
    def occupancy_count(self) -> int:
        return len(self._store)

    def op_sequence(self) -> list[str]:
        """The ordered list of operation names, for call-order assertions."""
        return [r.op for r in self.call_log]

    def reset_log(self) -> None:
        self.call_log.clear()
        self.feedback_log.clear()


def _failure(
    axis: FailureAxis, detail: str, sensor_code: int | None = None
) -> FingerprintHALFailure:
    """Build a taxonomy-valid failure. sensor_refusal/unknown_code need a code;
    supply a synthetic one so the disjointness invariant holds."""
    if axis in (FailureAxis.SENSOR_REFUSAL, FailureAxis.UNKNOWN_CODE) and sensor_code is None:
        sensor_code = 0x28  # the R503's known-undocumented code, a handy stand-in
    if axis is FailureAxis.TRANSPORT:
        sensor_code = None
    return FingerprintHALFailure(axis=axis, sensor_code=sensor_code, detail=detail)
