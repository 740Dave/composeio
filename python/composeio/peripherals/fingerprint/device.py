"""The device-logic ABOVE the HAL — the firmware's Python twin — C71/C73.

This is the transport-agnostic, HAL-agnostic logic the C++ firmware will be
parity-tested against. It calls the :class:`FingerprintHAL` Protocol and
publishes to a bus interface, knowing NOTHING about mock-vs-real or
in-process-vs-wire ("collapse the transport, not the seam",
[CXC-FPBUS-MSG-AUTH-EVENT]). That is exactly what lets the firmware reuse this
logic and the service test against it.

What the device owns (all below):
  * the push RECEIPT ([CXC-FPBUS-PUSH-SEQUENCE], [CXC-FPBUS-REQ-UUID-COMPARE]):
    read the current UUID first; a MATCH is a no-op + one ack; a MISMATCH is
    empty -> install-all-in-order -> write-stamp-LAST;
  * the slot -> {person, character, role} BRIDGE, built during install — the
    HAL never sees a person ([CXC-FPID-DEVICE-BRIDGE]);
  * a small global role -> capability table and the gate eval, run on the
    device ([DD-FPID-RBAC-EVAL-LOCUS]);
  * the four-state heartbeat machine ([CXC-FPBUS-REQ-*-HEARTBEAT]);
  * count_authentication_attempts, persisted, reset ONLY on a genuine UUID
    change ([CXC-FPBUS-MSG-FINGERPRINT-READY] counter-semantics);
  * the failure obligations ([CXC-FPBUS-REQ-LOAD-FAILURE],
    [CXC-FPBUS-REQ-DELETE-FAILURE-ESCALATION]);
  * the three-valued auth_event ([CXC-FPBUS-MSG-AUTH-EVENT]) and the RING that
    hides denial ([CXC-FPID-RING-HIDES-DENIAL]).
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from .hal import (
    FailureAxis,
    FeedbackState,
    FingerprintHAL,
    FingerprintHALError,
    FingerprintHALFailure,
    MatchOutcome,
    MatchResult,
    Score,
    Slot,
)


# --- the bus seam ------------------------------------------------------------
class FingerprintBusSink(Protocol):
    """The publish seam. Matches ``ComposeIOBus.publish_event`` exactly, so a
    live bus is a drop-in and a test recorder is trivial. The device NEVER
    imports the bus — it depends only on this method, which keeps the logic
    transport-agnostic ([CXC-FPBUS-MSG-AUTH-EVENT] scope-membrane)."""

    def publish_event(
        self, namespace: str, event_type: str, data: dict | None = None, source: str = ""
    ) -> None: ...


# --- the three-valued outcome ------------------------------------------------
class AuthOutcome(Enum):
    """[CXC-FPID-AUTH-OUTCOME] — never a boolean. All three are published,
    unrecognized included."""

    GRANTED = "granted"
    DENIED = "denied"
    UNRECOGNIZED = "unrecognized"


# --- the heartbeat state -----------------------------------------------------
class HeartbeatState(Enum):
    """The three emitted heartbeats; silence is the fourth state (offline) and
    is an ABSENCE, never a message ([CXC-FPBUS-REQ-READY-HEARTBEAT])."""

    READY = "fingerprint_ready"                 # healthy AND configured — 60s
    PUSH_NEEDED = "fingerprint_push_needed"      # healthy but unconfigured — 15s
    INOPERABLE = "fingerprint_inoperable"        # hardware cannot be used — 15s


HEARTBEAT_INTERVALS: dict[HeartbeatState, float] = {
    HeartbeatState.READY: 60.0,
    HeartbeatState.PUSH_NEEDED: 15.0,
    HeartbeatState.INOPERABLE: 15.0,
}

# [DD-FPBUS-DELETE-FAILURE-CLASS-MAP] (proposed): split by whether a fresh push
# can clear the store. Recoverable -> push_needed; unrecoverable -> inoperable.
# The task pins unknown_code -> push_needed.
_RECOVERABLE_DELETE_AXES = frozenset(
    {
        FailureAxis.TRANSPORT,
        FailureAxis.CAPACITY,
        FailureAxis.PROTOCOL_DESYNC,
        FailureAxis.UNKNOWN_CODE,
    }
)
# Everything else (SENSOR_REFUSAL, UNSUPPORTED, and any hardware-absent
# condition) is unrecoverable -> inoperable. UNSUPPORTED is not named in the
# proposed map; it is classified unrecoverable here because a push cannot add a
# capability the sensor lacks. Flagged in the deliverable notes.


@dataclass
class BridgeEntry:
    """One slot's logical binding, built during install ([CXC-FPID-DEVICE-
    BRIDGE]). The person never crosses the HAL; it lives only here."""

    person: str
    ordinal: int
    character: str
    role: str


@dataclass
class DevicePersistence:
    """Device-private MCU NVS ([CXC-FPBUS-MSG-FINGERPRINT-READY] counter-
    semantics: "stored in NVS beside template_set_uuid"). Survives a reboot —
    a reboot is modelled as a NEW FingerprintDevice built with the SAME
    persistence and the SAME (sensor-persistent) HAL.

    NOTE (inferred, flagged in the deliverable): C71 says the bridge is "built
    during install" but does not state it persists across reboot. Yet the
    ready-heartbeat is emitted on reboot from stamp+hardware alone, which means
    the device must be able to authenticate without a fresh push. The bridge
    therefore MUST survive reboot, so it is persisted here beside the count."""

    count_authentication_attempts: int = 0
    bridge: dict[Slot, BridgeEntry] = field(default_factory=dict)
    role_capability_table: dict[str, list[str]] = field(default_factory=dict)


class FingerprintDevice:
    """The device firmware twin. One instance owns one sensor via its HAL."""

    def __init__(
        self,
        hal: FingerprintHAL,
        bus: FingerprintBusSink,
        device_id: str,
        gated_capability: str,
        *,
        persistence: DevicePersistence | None = None,
        uplink: FingerprintBusSink | None = None,
        match_threshold: int | None = None,
        clock: Callable[[], float] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        template_resolver: Callable[[str], bytes] | None = None,
    ):
        self.hal = hal
        self._bus = bus
        # [DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]: a push entry's
        # ``template`` may now be a `fingerprint://<opaque-id>` reference rather
        # than raw bytes (RosterPublisher always converts once validation has
        # passed — DP-CIO-6, no transport-specific carve-out). None (the
        # default) resolves lazily via the REAL ``composeio.resolve.
        # resolve_bytes`` on first use, so a caller that never pushes a
        # reference (selftest.py, the render binder demo — anyone driving
        # ``receive_push`` with raw bytes directly) pays no import/network
        # cost. A test injects an in-memory fake instead.
        self._template_resolver = template_resolver
        # [CXC-FPBUS-MSG-AUTH-EVENT] scope-membrane: one event, two scopes.
        # In the real bus this is ONE publish to a global-scoped namespace the
        # bus bridges to ComposeBus. Here an optional uplink sink lets a test
        # assert the LOCAL and uplinked copies are byte-identical.
        self._uplink = uplink
        self._device_id = device_id
        self._gated_capability = gated_capability
        # [CXC-FPHAL-MATCH-THRESHOLD] device-internal score floor; null = unset.
        self._match_threshold = match_threshold
        self._nvs = persistence or DevicePersistence()
        self._clock = clock or (lambda: 0.0)
        self._event_id_factory = event_id_factory or (lambda: _uuid.uuid4().hex)
        # runtime device conditions (not persisted)
        self._hardware_present = True
        # a delete-failure escalation latches the heartbeat state until the
        # next successful push clears it.
        self._escalation_state: HeartbeatState | None = None

    # ---- accessors used by tests -----------------------------------------

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def count_authentication_attempts(self) -> int:
        return self._nvs.count_authentication_attempts

    @property
    def bridge(self) -> dict[Slot, BridgeEntry]:
        return self._nvs.bridge

    def set_hardware_present(self, present: bool) -> None:
        """Simulate a sensor disconnect/reconnect ([CXC-FPBUS-SCEN-INOPERABLE-
        HEARTBEAT])."""
        self._hardware_present = present

    # ---- current UUID / configured state ---------------------------------

    def _read_current_uuid(self) -> str | None:
        stamp = self.hal.read_roster_stamp()
        return stamp.decode("utf-8") if stamp is not None else None

    # ---- template reference resolution ------------------------------------

    def _resolve_template(self, value: bytes | str) -> bytes:
        """Resolve one push entry's ``template`` field to bytes.

        A `fingerprint://<opaque-id>` reference is fetched from the dedicated
        template store; raw bytes pass through unchanged (a caller that pushes
        pre-resolved templates directly — e.g. selftest.py — never touches
        this branch). [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] runs on these FETCHED
        bytes: it is enforced by ``hal.store_template`` immediately after this
        call returns, so resolve-then-guard-then-store falls out of the
        existing call order with no separate check needed here.

        A resolve failure (store unreachable, id not found, ...) is reported
        as a TRANSPORT ``FingerprintHALError`` so it drives the SAME load-
        failure path ([CXC-FPBUS-REQ-LOAD-FAILURE]) a HAL write failure would:
        report `template_set_failed`, HOLD the stamp, empty the device."""
        if not isinstance(value, str):
            return value
        resolver = self._template_resolver
        if resolver is None:
            # Lazy import: only a caller that actually pushes a reference pays
            # for composeio.resolve's presence.
            from composeio.resolve import resolve_bytes as resolver
        try:
            return resolver(value)
        except Exception as exc:  # noqa: BLE001 — any resolve failure is TRANSPORT
            raise FingerprintHALError(
                FingerprintHALFailure(
                    axis=FailureAxis.TRANSPORT,
                    detail=f"failed to resolve template reference {value!r}: {exc}",
                )
            ) from exc

    # =====================================================================
    # [CXC-FPBUS-PUSH-SEQUENCE] The push receipt
    # =====================================================================

    def receive_push(self, push: dict[str, Any]) -> str:
        """Receive a template-set push and execute the whole mechanism on the
        device. Returns a short status string for the caller/test. The service-
        side validations (set size <= 20, UUID present) live in the service
        (roster_push.py), not here — a push that reaches the device already
        passed them ([CXC-FPBUS-REQ-SET-SIZE-LIMIT], [CXC-FPBUS-REQ-SET-UUID-
        PRESENT])."""
        pushed_uuid: str = push["template_set_uuid"]

        # [CXC-FPBUS-REQ-UUID-COMPARE] Receipt BEGINS with a UUID read, before
        # any delete or write. read_roster_stamp() is a recorded HAL call, so
        # the ordering is assertable on the mock's call_log.
        current = self._read_current_uuid()

        if current == pushed_uuid:
            # [CXC-FPBUS-REQ-UUID-MATCH-BRANCH] no work, one ack. The count is
            # NOT reset — this is an idempotent re-push.
            self._publish("template_set_loaded", {"template_set_uuid": pushed_uuid})
            return "loaded"

        # [CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH] delete all, load all, stamp last.
        try:
            self.hal.empty()  # first move: delete all
        except FingerprintHALError as exc:
            # A delete failure even as the opening wipe escalates per the class
            # map (there was no load to fail first, but the store cannot be
            # cleared, which is exactly the escalation's concern).
            self._escalate_delete_failure(exc)
            return "delete_failed"

        new_bridge: dict[Slot, BridgeEntry] = {}
        role_table = dict(push.get("role_capability_table", {}))
        try:
            for entry in push["entries"]:
                # Resolve a `fingerprint://` reference to bytes BEFORE the
                # store — [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] then runs on the
                # FETCHED bytes via hal.store_template's own size check.
                template = self._resolve_template(entry["template"])
                slot = self.hal.store_template(template)
                new_bridge[slot] = BridgeEntry(
                    person=entry["person"],
                    ordinal=entry["ordinal"],
                    character=entry["character"],
                    role=entry["rbac"],
                )
        except FingerprintHALError as exc:
            return self._handle_load_failure(pushed_uuid, exc)

        # All templates saved successfully -> commit.
        self._nvs.bridge = new_bridge
        self._nvs.role_capability_table = role_table
        # [CXC-FPBUS-REQ-UUID-MISMATCH-BRANCH] stamp STRICTLY LAST — only after
        # the last store returned success.
        self.hal.write_roster_stamp(pushed_uuid.encode("utf-8"))
        # counter-semantics: a GENUINE uuid change zeroes the count.
        self._nvs.count_authentication_attempts = 0
        self._escalation_state = None
        self._publish("template_set_loaded", {"template_set_uuid": pushed_uuid})
        return "loaded"

    def _handle_load_failure(self, pushed_uuid: str, exc: FingerprintHALError) -> str:
        """[CXC-FPBUS-REQ-LOAD-FAILURE] report, HOLD the stamp, and empty the
        device. If that cleanup delete itself fails, escalate."""
        self._publish(
            "template_set_failed",
            {
                "template_set_uuid": pushed_uuid,
                "axis": exc.failure.axis.value,
                "sensor_code": exc.failure.sensor_code,
                "detail": exc.failure.detail,
            },
        )
        # HOLD the stamp: do NOT advance current_template_set_uuid. Then empty.
        try:
            self.hal.empty()  # cleanup delete
        except FingerprintHALError as del_exc:
            # [CXC-FPBUS-REQ-DELETE-FAILURE-ESCALATION]
            self._escalate_delete_failure(del_exc)
            return "load_failed_delete_failed"
        # Cleanup succeeded: templates gone, stamp held -> unambiguously
        # unconfigured -> the push_needed heartbeat announces it.
        self._escalation_state = None
        return "load_failed"

    def _escalate_delete_failure(self, exc: FingerprintHALError) -> None:
        """[CXC-FPBUS-REQ-DELETE-FAILURE-ESCALATION] + [DD-FPBUS-DELETE-FAILURE-
        CLASS-MAP]. Recoverable -> push_needed; unrecoverable -> inoperable."""
        if exc.failure.axis in _RECOVERABLE_DELETE_AXES:
            self._escalation_state = HeartbeatState.PUSH_NEEDED
            self._publish("fingerprint_push_needed", None)
        else:
            self._escalation_state = HeartbeatState.INOPERABLE
            self._publish(
                "fingerprint_inoperable",
                {
                    "inop_code": exc.failure.axis.value,
                    "human_readable_inop_string": exc.failure.detail,
                },
            )

    # =====================================================================
    # [CXC-FPBUS-MSG-MATCH-HAS-NO-COMMAND] The commandless match
    # =====================================================================

    def poll_match(self) -> AuthOutcome | None:
        """Poll the sensor once and resolve. The device polls its OWN sensor;
        nothing off-device commands a match ([CXC-FPBUS-MSG-MATCH-HAS-NO-
        COMMAND])."""
        return self.resolve_match(self.hal.match())

    def resolve_match(self, result: MatchResult) -> AuthOutcome | None:
        """Resolve a MatchResult to a three-valued outcome, publish the
        auth_event (local + uplink), drive the ring, and count the attempt.

        Returns None when there was no authentication attempt (NO_FINGER) or
        when a below-floor score suppressed the identity event
        ([CXC-FPHAL-MATCH-THRESHOLD]). The score NEVER crosses the bus
        ([CXC-FPHAL-SCORE-DEVICE-INTERNAL])."""
        if result.outcome is MatchOutcome.NO_FINGER:
            # An empty doorway is not an authentication attempt.
            return None

        if result.outcome is MatchOutcome.MATCHED and self._match_threshold is not None:
            if result.score is not None and result.score < self._match_threshold:
                # Below the device-internal confidence floor: no identity event.
                return None

        player: str | None = None
        character: str | None = None
        if result.outcome is MatchOutcome.NO_MATCH:
            outcome = AuthOutcome.UNRECOGNIZED
        else:  # MATCHED
            entry = self._nvs.bridge.get(result.slot)
            if entry is None:
                # Matched a slot with no bridge binding -> treat as an intruder.
                outcome = AuthOutcome.UNRECOGNIZED
            else:
                caps = self._nvs.role_capability_table.get(entry.role, [])
                outcome = (
                    AuthOutcome.GRANTED
                    if self._gated_capability in caps
                    else AuthOutcome.DENIED
                )
                player = entry.person
                character = entry.character

        # This IS an authentication attempt against the loaded roster.
        self._nvs.count_authentication_attempts += 1

        self._publish_auth_event(outcome, player, character)
        self._drive_ring(outcome)
        return outcome

    def _publish_auth_event(
        self, outcome: AuthOutcome, player: str | None, character: str | None
    ) -> None:
        """[CXC-FPBUS-MSG-AUTH-EVENT] one event, three-valued, all published.
        player/character are ABSENT (not null) for unrecognized. No score.
        ``gated_capability`` is DELIBERATELY ABSENT (wire-economy, 2026-08-18):
        a device gates exactly ONE capability, a per-device constant already
        retained on the device-announce, joinable by ``device_id``."""
        payload: dict[str, Any] = {
            "event_id": self._event_id_factory(),
            "device_id": self._device_id,
            "outcome": outcome.value,
            "timestamp": self._clock(),
        }
        if outcome in (AuthOutcome.GRANTED, AuthOutcome.DENIED):
            payload["player"] = player
            payload["character"] = character
        # LOCAL bus.
        self._bus.publish_event(
            f"{self._device_id}/auth_event", "auth_event", payload, source=self._device_id
        )
        # Uplinked to ComposeBus — identical shape ([CXC-FPBUS-MSG-AUTH-EVENT]
        # scope-membrane). In the real bus this is the SAME publish bridged by
        # scope; here the identical dict goes to a second sink so a test can
        # assert both scopes see the same event.
        if self._uplink is not None:
            self._uplink.publish_event(
                f"{self._device_id}/auth_event",
                "auth_event",
                dict(payload),
                source=self._device_id,
            )

    def _drive_ring(self, outcome: AuthOutcome) -> None:
        """[CXC-FPID-RING-HIDES-DENIAL] granted -> green (ACCEPT); denied AND
        unrecognized -> the SAME red, indistinguishable. The ring is NOT a
        mirror of the event: the event carries the true three-valued outcome,
        the ring collapses two of them."""
        if outcome is AuthOutcome.GRANTED:
            state = FeedbackState.ACCEPT
        else:
            # denied and unrecognized are identical here, deliberately.
            state = FeedbackState.REJECT_SILENT
        try:
            self.hal.command_feedback(state)
        except FingerprintHALError:
            # A sensor with no feedback channel cannot show the ring; the
            # outcome is still published. Not fatal.
            pass

    # =====================================================================
    # [CXC-FPBUS-REQ-*-HEARTBEAT] The four-state heartbeat machine
    # =====================================================================

    def current_heartbeat_state(self) -> HeartbeatState:
        """Mutually exclusive, jointly exhaustive. Silence (offline) is the
        fourth state and is not emitted here."""
        if not self._hardware_present:
            return HeartbeatState.INOPERABLE
        if self._escalation_state is not None:
            return self._escalation_state
        stamp = self.hal.read_roster_stamp()
        occupancy = self.hal.read_occupancy()
        if stamp is not None and len(occupancy) > 0:
            return HeartbeatState.READY
        # No stamp, or stamp held over an emptied store (post load-failure):
        # unconfigured.
        return HeartbeatState.PUSH_NEEDED

    def emit_heartbeat(self) -> HeartbeatState:
        """Publish exactly one heartbeat for the current state and return it."""
        state = self.current_heartbeat_state()
        if state is HeartbeatState.READY:
            uuid = self._read_current_uuid()
            self._publish(
                "fingerprint_ready",
                {
                    "template_set_uuid": uuid,
                    "count_authentication_attempts": self._nvs.count_authentication_attempts,
                },
            )
        elif state is HeartbeatState.PUSH_NEEDED:
            self._publish("fingerprint_push_needed", None)  # payload: none
        else:  # INOPERABLE
            self._publish(
                "fingerprint_inoperable",
                {
                    "inop_code": "sensor_unavailable",
                    "human_readable_inop_string": "fingerprint hardware not detected",
                },
            )
        return state

    def heartbeat_interval(self) -> float:
        return HEARTBEAT_INTERVALS[self.current_heartbeat_state()]

    # ---- publish helper ---------------------------------------------------

    def _publish(self, message: str, data: dict | None) -> None:
        """Every message is device-addressed as ``<dev>/<message>``
        ([CXC-FPBUS-ADDRESSING])."""
        self._bus.publish_event(
            f"{self._device_id}/{message}", message, data, source=self._device_id
        )


# --- virtual-time heartbeat driver (no real sleeping) -----------------------
def run_heartbeat_window(
    device: FingerprintDevice, duration_s: float, *, start: float = 0.0
) -> list[tuple[float, HeartbeatState]]:
    """Publish heartbeats over a virtual window of ``duration_s`` seconds with
    NO real time: emit one at ``start``, then every interval for the current
    state, while t < start+duration. Returns [(t, state)] for spacing/cadence
    assertions ([CXC-FPBUS-REQ-*-HEARTBEAT] acceptance-machines, run in
    virtual time because the reference touches no hardware and no wall clock)."""
    emissions: list[tuple[float, HeartbeatState]] = []
    t = start
    end = start + duration_s
    epsilon = 1e-9
    while t < end - epsilon:
        state = device.emit_heartbeat()
        emissions.append((t, state))
        t += HEARTBEAT_INTERVALS[state]
    return emissions
