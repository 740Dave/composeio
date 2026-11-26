"""Enroll orchestration — the station and editor seam ([CXC-FPSVC-ENROLL-
ORCHESTRATION], [CXC-FPSVC-STATION-EDITOR]).

To enrol, the EDITOR binds a person to up to two templates, a character, and the
character's role, and the STATION issues a device-addressed enroll ``{person,
ordinal, request_id}``. The device runs the capture ceremony, allocates its OWN
slot, and stores the template; the captured template is read back and uploaded
into the superset keyed by ``(person, ordinal)`` — NEVER by slot.

The station names only the logical identity. It never picks or reads a physical
slot ([CXC-FPSVC-SCEN-ENROLL-KEYS-BY-LOGICAL]). The enroll command shape is
[CXC-FPBUS-MSG-ENROLL]; the readback shape is the proposed
[DD-FPBUS-ENROLL-READBACK-SHAPE] ``<dev>/result/enroll/complete {person,
ordinal, template_ref, request_id}``.

[DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F] — the readback used to carry
the raw template BYTES inline (``data["template"]``), which a JSON bus transport
cannot serialise (DP-CIO-18) — the exact defect class
[DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18] closed in the opposite
(SERVICE->DEVICE) direction. This is that fix's mirror: the device side (the
station/reference-device harness that captured the bytes) uploads them to the
SAME dedicated ``fingerprint_template_store`` (never bus_storage) under a fresh
opaque id and publishes ``template_ref = "fingerprint://<id>"`` instead
(unconditional — DP-CIO-6, no transport-specific carve-out). This orchestrator
is the CONSUMER: it resolves the reference back to bytes before landing the
template in the superset, which still holds raw bytes exactly as it always has
([CXC-FPSVC-ENROLL-ORCHESTRATION] — the reference-vs-bytes distinction is a
wire concern only, never a storage-model one). A bare ``data["template"]``
(raw bytes) is still accepted as a fallback for a caller that lands a template
without crossing the bus at all (e.g. the in-process
``EnrollmentStation``/``EnrollmentSession`` shortcut, which never calls this
method in the first place, or a legacy in-process test double).

Person + casting management is a UI-to-service API, never sensor bus RPC
([CXC-FPSVC-PERSON-CRUD]); those calls land directly on :class:`SupersetStore`.
Only the enroll COMMAND and the readback cross the device seam.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import topics
from .store import SupersetStore

from composeio.peripherals.fingerprint.device import FingerprintBusSink

# The template-store GET seam. Matches ``composeio.resolve.resolve_bytes``
# exactly (uri) -> bytes — so the live dedicated store is a drop-in and a test
# fake (an in-memory dict, no HTTP) is trivial to inject via
# EnrollOrchestrator.template_resolver. Mirrors RosterPublisher's
# ``template_store_put`` seam on the opposite (SERVICE->DEVICE) leg.
from composeio.resolve import resolve_bytes as _default_template_resolver


@dataclass
class _Pending:
    device_id: str
    person: str
    ordinal: int


@dataclass
class EnrollOrchestrator:
    """Issues logical enroll commands and lands the readback in the superset.

    ``readback_capable`` answers whether a device can upload what it captured
    ([DD-FPSVC-READBACK-REQUIRED], PROPOSED): True proceeds, False refuses,
    None (unknown, no announce seen) proceeds. The guard is enforced only when
    the capability is known, so it never blocks an in-process test that skips the
    announce, and it is flagged as proposed rather than ruled."""

    store: SupersetStore
    bus: FingerprintBusSink
    source: str = "fingerprint_service"
    readback_capable: Callable[[str], bool | None] | None = None
    # The template-store GET seam ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-
    # A4128F]): uri -> bytes, matching ``composeio.resolve.resolve_bytes``
    # exactly. Defaults to the REAL dedicated store; a test injects an
    # in-memory fake so the reference/integration suites never need a live
    # HTTP server. Never bus_storage's resolve_bytes target — only ever called
    # with a uri that starts `fingerprint://`.
    template_resolver: Callable[[str], bytes] = field(
        default=_default_template_resolver
    )
    _pending: dict[str, _Pending] = field(default_factory=dict)

    def enroll(self, device_id: str, person: str, ordinal: int) -> str:
        """Issue a device-addressed enroll for a LOGICAL identity and return the
        request_id ([CXC-FPBUS-CORRELATION]: every command carries a client
        request_id the result echoes). The template lands via
        :meth:`on_enroll_complete` when the device reads it back."""
        if person not in self.store.persons:
            raise KeyError(f"unknown person: {person}")
        if self.readback_capable is not None:
            cap = self.readback_capable(device_id)
            if cap is False:
                raise ValueError(
                    f"device {device_id} is not readback-capable; enrollment "
                    "must run on a readback-capable station "
                    "([DD-FPSVC-READBACK-REQUIRED], proposed)"
                )
        request_id = _uuid.uuid4().hex
        self._pending[request_id] = _Pending(device_id, person, ordinal)
        # LOGICAL identity only — no slot ([CXC-FPBUS-MSG-ENROLL]).
        self.bus.publish_event(
            topics.enroll_cmd(device_id),
            "enroll",
            {"person": person, "ordinal": ordinal, "request_id": request_id},
            source=self.source,
        )
        return request_id

    def cancel(self, device_id: str, request_id: str) -> None:
        """Abandon the ceremony in progress ([CXC-FPBUS-MSG-ENROLL-CANCEL]). The
        payload carries no ceremony id, session token, or slot."""
        self._pending.pop(request_id, None)
        self.bus.publish_event(
            topics.enroll_cancel_cmd(device_id),
            "enroll_cancel",
            {"request_id": request_id},
            source=self.source,
        )

    def on_enroll_complete(self, device_id: str, data: dict[str, Any]) -> None:
        """Land a read-back template into the superset keyed by (person,
        ordinal). The device already allocated and owns the slot; no slot appears
        in this record ([CXC-FPSVC-SCEN-ENROLL-KEYS-BY-LOGICAL]).

        The wire template is a `fingerprint://` reference
        ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]), resolved here to
        the real bytes before the superset upsert — the superset itself still
        holds raw bytes, exactly as it always has; only the WIRE shape
        changed."""
        request_id = data.get("request_id")
        pending = self._pending.pop(request_id, None)
        # The command echoed a request_id; trust the pending correlation for the
        # (person, ordinal) rather than any slot the device might mention.
        person = pending.person if pending else data["person"]
        ordinal = pending.ordinal if pending else data["ordinal"]
        template = self._resolve_template(data)
        self.store.upsert_template(person, ordinal, template)

    def _resolve_template(self, data: dict[str, Any]) -> bytes:
        """Resolve the readback payload's template to bytes.

        `template_ref` (a `fingerprint://<opaque-id>` reference) is the
        current, bus-safe shape. A bare `template` (raw bytes) is accepted as
        a fallback for a caller that lands a template without crossing the
        bus at all — the in-process `EnrollmentStation`/`EnrollmentSession`
        shortcut never calls this method in the first place, but a legacy
        in-process caller might still pass bytes directly."""
        ref = data.get("template_ref")
        if ref is not None:
            return self.template_resolver(ref)
        return data["template"]

    def pending_count(self) -> int:
        return len(self._pending)
