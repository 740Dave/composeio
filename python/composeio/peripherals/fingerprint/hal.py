"""The agnostic fingerprint HAL contract — [SPEC:CXC-FPHAL].

This module is the pure, vendor-free device-side seam: the typed primitives,
the ten operations with their exact signatures, the five-field capabilities
record, the closed six-axis failure taxonomy, and the twelve feedback states.
It names no vendor, no person, no character, no role — the HAL knows only
hop 1, finger-to-slot ([CXC-FPID-SENSOR-KNOWS-ONLY-HOP-1]).

Two realizations satisfy this contract: ``MockFingerprintHAL`` (the canonical
reference — see :mod:`.mock`) and, later, an ``R503HAL`` backend. A single
parity suite runs unchanged against both; where they disagree the mock is the
expected value ([CXC-FPHAL-MOCK-ROLE], [CXC-FPHAL-PARITY-TEST]).

Nothing here imports the bus, the device firmware, or any transport: the HAL
is called on-device only, one layer below the device firmware that owns the
slot-to-person bridge ([CXC-FPHAL-SEAM], [DD-FPHAL-HOST-OR-VM-BINDING]).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType, Protocol, runtime_checkable

# --- [CXC-FPHAL-PRIMITIVE-TYPES] The five primitives, and no identity type ---
#
# NewType keeps these int/bytes at runtime (zero cost) while making a Slot that
# is really a Score a type-checker error. The HAL carries NO PersonId,
# Character, Role, Capability, or Module type — it knows only hop 1.
Slot = NewType("Slot", int)            # device-local 0-based index, 0 <= s < slot_count
Score = NewType("Score", int)          # vendor-scaled confidence, device-internal only
Ordinal = NewType("Ordinal", int)      # which of a person's templates (1 or 2 today)
TemplateBytes = bytes                  # opaque; the HAL never parses it
RosterStamp = bytes                    # opaque to the HAL; stored and returned whole


# --- [CXC-FPHAL-MATCH-RESULT] the outcome enum -------------------------------
class MatchOutcome(Enum):
    """The three-valued result of a poll. NO_FINGER (nothing presented) is
    distinct from NO_MATCH (a finger the sensor did not recognize): the first
    is shown nothing, the second a silent rejection."""

    MATCHED = "matched"
    NO_MATCH = "no_match"
    NO_FINGER = "no_finger"


# --- [CXC-FPHAL-FAILURE-AXES] six closed, disjoint axes ----------------------
class FailureAxis(Enum):
    """Every failure carries exactly one axis. The set is CLOSED — a backend
    adds no seventh, and a condition it cannot classify is ``unknown_code``,
    which is itself a member. The taxonomy's whole value is that a transport
    failure is numerically disjoint from a sensor refusal."""

    TRANSPORT = "transport"              # the link failed: framing, checksum, timeout, silence
    SENSOR_REFUSAL = "sensor_refusal"    # the part answered, and the answer was a documented refusal
    CAPACITY = "capacity"                # store full, slot out of range, or payload too large
    PROTOCOL_DESYNC = "protocol_desync"  # the stream is phase-shifted
    UNSUPPORTED = "unsupported"          # capabilities says this implementation cannot do this
    UNKNOWN_CODE = "unknown_code"        # the part answered with a code no revision documents


# --- [CXC-FPHAL-FEEDBACK-STATES] the closed set of twelve --------------------
class FeedbackState(Enum):
    """The twelve feedback states. A state is only ever shown when
    ``command_feedback`` is called; the HAL originates none on its own.
    ``reject_silent`` MUST render identically to a genuine non-match
    ([CXC-FPID-RING-HIDES-DENIAL]). ``enroll_progress`` is the one state that
    carries a payload (the capture ordinal and the announced capture count)."""

    IDLE = "idle"
    READY = "ready"
    AWAITING_FINGER = "awaiting_finger"
    CAPTURE_ACCEPTED = "capture_accepted"
    CAPTURE_RETRY = "capture_retry"
    ENROLL_PROGRESS = "enroll_progress"
    BUSY = "busy"
    FAULT = "fault"
    ACCEPT = "accept"
    REJECT_SILENT = "reject_silent"
    REJECT_KNOWN = "reject_known"
    RETRY = "retry"


class FeedbackChannel(Enum):
    """A feedback output the sensor physically owns. The empty set in
    ``Capabilities.feedback_channels`` means the device has no feedback at all,
    and ``command_feedback`` then fails with UNSUPPORTED."""

    AURA_LED = "aura_led"   # the R503's ring LED


# --- [CXC-FPHAL-CAPABILITIES-SCHEMA] five required fields, no defaults -------
@dataclass(frozen=True)
class Capabilities:
    """FIVE REQUIRED fields, no optionals, no defaults, stable for the life of
    the object. An implementation that does not know a value must not omit the
    key — an omitted key is indistinguishable from a value nobody wrote
    ([CXC-FPHAL-CAPABILITIES-SCHEMA]). The service reads these second-hand from
    the device announce (C73) and sizes the cast, resync cost, and overflow
    check from them.

    ``max_templates_per_person`` was REMOVED (2026-08-18): the sensor has no
    person concept — it holds slots and templates — so a per-person limit is
    service-side game policy ([CXC-FPID-ONE-OR-TWO-TEMPLATES]), never a device
    capability, and no code consumed the field."""

    slot_count: int
    supports_readback: bool
    feedback_channels: frozenset[FeedbackChannel]
    captures_per_template: int
    template_size_bytes: int


# --- [CXC-FPHAL-MATCH-RESULT] slot+score together or not at all -------------
@dataclass(frozen=True)
class MatchResult:
    """A slot and a score, and NO verdict of any kind. It never returns a
    person, character, role, capability, or permission decision — those live
    in the device firmware above the HAL. Slot and score are present together
    or not at all: a score without a slot would hide a threshold decision the
    HAL is not entitled to make ([CXC-FPHAL-MATCH-RESULT])."""

    outcome: MatchOutcome
    slot: Slot | None = None
    score: Score | None = None

    def __post_init__(self) -> None:
        if self.outcome is MatchOutcome.MATCHED:
            if self.slot is None or self.score is None:
                raise ValueError("MATCHED requires both a slot and a score")
        else:
            if self.slot is not None or self.score is not None:
                raise ValueError(
                    f"{self.outcome.value} carries neither slot nor score"
                )


# --- [CXC-FPHAL-FAILURE-AXES] the failure payload ---------------------------
@dataclass(frozen=True)
class FingerprintHALFailure:
    """The on-wire and in-process failure shape: {axis, sensor_code, detail}.
    ``sensor_code`` MUST be None on transport (nothing was heard, so nothing
    can be quoted) and MUST be present on sensor_refusal and unknown_code. The
    detail string is for a human reading a log; no caller may parse it
    ([CXC-FPHAL-FAILURE-AXES], [CXC-FPHAL-UNKNOWN-CODE])."""

    axis: FailureAxis
    sensor_code: int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.axis is FailureAxis.TRANSPORT and self.sensor_code is not None:
            raise ValueError("transport failure quotes no sensor_code")
        if self.axis in (FailureAxis.SENSOR_REFUSAL, FailureAxis.UNKNOWN_CODE):
            if self.sensor_code is None:
                raise ValueError(f"{self.axis.value} MUST carry a sensor_code")


class FingerprintHALError(Exception):
    """A typed failure a caller cannot ignore by forgetting to check a return
    ([DD-FPHAL-FAILURE-RAISED-OR-RETURNED], lean: raise). It carries the
    six-axis taxonomy unchanged so the layer above can classify it."""

    def __init__(self, failure: FingerprintHALFailure):
        self.failure = failure
        super().__init__(f"{failure.axis.value}: {failure.detail}")

    @property
    def axis(self) -> FailureAxis:
        return self.failure.axis


# --- [CXC-FPHAL-ENROLLMENT-SEQUENCE] the driven ceremony --------------------
class EnrollmentSession(Protocol):
    """An explicit, device-driven capture ceremony ([CXC-FPHAL-ENROLLMENT-
    SEQUENCE]). ``begin_enroll(ordinal)`` opens it; the device drives exactly
    ``captures_required`` captures (never a hard-coded constant — it is the
    announced ``captures_per_template``) and on success ALLOCATES a slot,
    writes the captured template to it, and yields that slot. It takes no
    caller-chosen slot: the forbidden bus-supplied-index mode is untypeable."""

    @property
    def captures_required(self) -> int: ...

    @property
    def captures_done(self) -> int: ...

    @property
    def complete(self) -> bool: ...

    @property
    def slot(self) -> Slot | None:
        """The allocated slot once the ceremony completes; None before."""
        ...

    def capture(self) -> int:
        """Drive one capture. Returns the 1-based ordinal of the capture just
        taken. On the final capture the ceremony allocates a slot, writes the
        template, sets :attr:`complete`, and populates :attr:`slot`."""
        ...

    def abandon(self) -> None:
        """End the ceremony with no write ([CXC-FPHAL-ENROLLMENT-SEQUENCE])."""
        ...


# --- [CXC-FPHAL-OPERATIONS] the ten operations ------------------------------
@runtime_checkable
class FingerprintHAL(Protocol):
    """The closed set of exactly TEN operations. A backend adds none and drops
    none ([CXC-FPHAL-OPERATIONS]). The template lifecycle is exactly two moves
    — ``empty()`` ALL, then ``store_template`` each SEQUENTIALLY — so there is
    NO random-access slot management: no per-slot delete, no update-by-index,
    no write-to-slot-N. The HAL OWNS physical-slot allocation; ``store_template``
    takes no caller-chosen index and RETURNS the slot it allocated."""

    # -- the runtime face --
    def match(self) -> MatchResult:
        """Poll the sensor once. The whole runtime face. Returns a slot and a
        score on MATCHED and no verdict ever ([CXC-FPHAL-MATCH-RESULT])."""
        ...

    # -- the management face --
    def capabilities(self) -> Capabilities:
        """The five-field record, identical for the life of the object."""
        ...

    def begin_enroll(self, ordinal: Ordinal) -> EnrollmentSession:
        """Open a driven enrollment ceremony. Takes no slot; the ceremony
        allocates the slot it writes to and the session yields it."""
        ...

    def store_template(self, template: TemplateBytes) -> Slot:
        """Install ONE template into the next free slot, in receive order, and
        return the allocated Slot. Takes no caller-chosen index."""
        ...

    def read_template(self, slot: Slot) -> TemplateBytes:
        """Read a template back. MAY fail with UNSUPPORTED when
        ``capabilities().supports_readback`` is False. Exists so an enrolling
        device can upload the captured template and so parity can round-trip."""
        ...

    def empty(self) -> None:
        """Destroy EVERY resident template. The first move of every mismatch
        push ([CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH])."""
        ...

    def read_occupancy(self) -> frozenset[Slot]:
        """Bulk introspection for parity and the device's own count — never
        addressable management ([CXC-FPHAL-OPERATIONS])."""
        ...

    def write_roster_stamp(self, stamp: RosterStamp) -> None:
        """Store the opaque roster stamp whole ([CXC-FPID-ROSTER-STAMP])."""
        ...

    def read_roster_stamp(self) -> RosterStamp | None:
        """Return the stored stamp, or None when the device was never stamped
        (distinct from empty bytes)."""
        ...

    def command_feedback(
        self, state: FeedbackState, progress: tuple[int, int] | None = None
    ) -> None:
        """The ONLY write to the feedback ring, always commanded from outside
        the HAL. ``progress`` is (capture_n, capture_of) for ENROLL_PROGRESS.
        Fails with UNSUPPORTED when ``feedback_channels`` is the empty set."""
        ...
