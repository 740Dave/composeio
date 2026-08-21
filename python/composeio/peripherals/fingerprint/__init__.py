"""composeio.peripherals.fingerprint — the R503 fingerprint reference layer.

This package is the EXECUTABLE REFERENCE for the fingerprint integration
([SPEC:CXC-FPHAL] C72, [SPEC:CXC-FPBUS] C73, [SPEC:CXC-FPID] C71). It is the
Python twin the C++ firmware is parity-tested against, and it runs entirely
in-process with no hardware.

Three layers, mirroring the NFC peripheral's shape:
  * :mod:`.hal` — the agnostic ``FingerprintHAL`` Protocol: the ten operations,
    the typed primitives, the six-field ``Capabilities``, the closed six-axis
    ``FailureAxis``, the twelve ``FeedbackState`` values.
  * :mod:`.mock` — ``MockFingerprintHAL``, THE REFERENCE: an in-memory,
    slot-allocating HAL with FAULT INJECTION (the negative control) and
    CALL-ORDER RECORDING.
  * :mod:`.device` — ``FingerprintDevice``, the transport- and HAL-agnostic
    firmware twin: the push receipt, the slot->person bridge, the local RBAC
    gate, the heartbeat machine, the persisted attempt counter, the failure
    obligations, and the three-valued auth_event with the denial-hiding ring.

Acceptance suite (no hardware; macOS-safe)::

    python -m composeio.peripherals.fingerprint --selftest
"""

from __future__ import annotations

from .device import (
    AuthOutcome,
    BridgeEntry,
    DevicePersistence,
    FingerprintBusSink,
    FingerprintDevice,
    HeartbeatState,
    run_heartbeat_window,
)
from .hal import (
    Capabilities,
    EnrollmentSession,
    FailureAxis,
    FeedbackChannel,
    FeedbackState,
    FingerprintHAL,
    FingerprintHALError,
    FingerprintHALFailure,
    MatchOutcome,
    MatchResult,
    Ordinal,
    RosterStamp,
    Score,
    Slot,
    TemplateBytes,
)
from .mock import MockFingerprintHAL

__all__ = [
    # hal
    "FingerprintHAL",
    "Capabilities",
    "MatchResult",
    "MatchOutcome",
    "FailureAxis",
    "FeedbackState",
    "FeedbackChannel",
    "FingerprintHALFailure",
    "FingerprintHALError",
    "EnrollmentSession",
    "Slot",
    "Score",
    "Ordinal",
    "TemplateBytes",
    "RosterStamp",
    # mock
    "MockFingerprintHAL",
    # device
    "FingerprintDevice",
    "FingerprintBusSink",
    "AuthOutcome",
    "HeartbeatState",
    "BridgeEntry",
    "DevicePersistence",
    "run_heartbeat_window",
]
