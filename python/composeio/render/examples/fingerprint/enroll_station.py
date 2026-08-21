"""EnrollmentStation — the rich fingerprint CAPTURE CEREMONY controller
([CXC-FPSVC-STATION-EDITOR], [CXC-FPSVC-ENROLL-ORCHESTRATION],
[CXC-FPHAL-ENROLLMENT-SEQUENCE], [CXC-FPHAL-FEEDBACK-STATES]).

A NEW, DISTINCT runtime surface from both existing fingerprint surfaces:

  * the BINDING UX (``binder.py`` / ``fingerprint_casting.compose.yaml``) —
    its "Capture finger" button fires ``svc.enroller.enroll()``, which the
    reference device answers in ONE SHOT (``_ReferenceDeviceHarness._on_enroll``
    loops ``session.capture()`` to completion synchronously and publishes
    ``enroll_complete`` — the whole place/lift/place ceremony collapsed to a
    single instant). That is correct FOR THAT SURFACE (it only needs the
    outcome), but it is exactly the ceremony this station makes visible.
  * the auth-MONITOR (``auth_monitor.py``) — watches ``auth_event`` (the
    match/authenticate path), never enrollment.

This station drives the SAME reference apparatus (``FingerprintStorageService``
+ ``MockFingerprintHAL`` + the HAL's own ``EnrollmentSession`` —
[CXC-FPHAL-ENROLLMENT-SEQUENCE]) but GRANULARLY: it calls ``begin_enroll``,
then for each of the announced ``captures_per_template`` captures it commands
the feedback ring itself — AWAITING_FINGER ("place finger"), optionally
CAPTURE_RETRY ("lift and place again") for a scripted bad placement, then
CAPTURE_ACCEPTED, drives ``session.capture()``, and commands ENROLL_PROGRESS
carrying the (capture_n, capture_of) payload — exactly per
[CXC-FPHAL-FEEDBACK-STATES]: "the HAL originates no feedback on its own",
feedback is COMMANDED from outside. On the final capture the session
allocates a slot and writes the template; the station then reads it back
(``hal.read_template``) and uploads it into the superset keyed by
``(person, ordinal)`` — NEVER by slot — via the SAME
``SupersetStore.upsert_template`` call ``EnrollOrchestrator.on_enroll_complete``
uses, so the upload path is the real one, not a hand-rolled shortcut.

⚠ This station drives the MOCK enroll IN-PROCESS (direct HAL/session calls),
not the bus ``enroll`` event round-trip the binding UX uses. It therefore
never depended on ``DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F`` (the
``enroll_complete`` publish used to carry raw template bytes, which a JSON
transport refuses) — CLOSED: ``binder.py``'s ``_ReferenceDeviceHarness``
now uploads the readback to the dedicated template store and publishes a
``fingerprint://`` reference, mirroring the push-direction fix. A future pass
that wires this station to a REAL bus-addressed device can now do so freely.

Publishes ONE structured dict per step to a single namespace (the shape
``EnrollCeremonyWidget`` — ``widgets/enroll_ceremony.py`` — draws):

    {person, display_name, template_ordinal, template_count,
     capture_done, capture_required, feedback, message, retry_count,
     templates: [{ordinal, status, retries}, ...], enrolled_count, done}
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from fingerprint_storage.service import FingerprintStorageService

from composeio.peripherals.fingerprint.device import FingerprintDevice
from composeio.peripherals.fingerprint.hal import FeedbackState
from composeio.peripherals.fingerprint.mock import MockFingerprintHAL

DEFAULT_DEVICE = "enroll-station-01"

VIEW_STATION = "fp/enroll/station"

# Human copy for the feedback states this ceremony actually commands. The
# widget renders the FULL closed set of twelve legibly regardless; the
# driver only emits the subset an enrollment ceremony genuinely produces
# (it never authenticates, so it never emits accept/reject_*/retry).
_MESSAGE = {
    FeedbackState.READY: "station ready",
    FeedbackState.AWAITING_FINGER: "place finger",
    FeedbackState.CAPTURE_RETRY: "lift and place again",
    FeedbackState.CAPTURE_ACCEPTED: "capture accepted",
    FeedbackState.ENROLL_PROGRESS: "",
    FeedbackState.BUSY: "reading back → uploading to superset",
    FeedbackState.ACCEPT: "template captured & uploaded",
    FeedbackState.IDLE: "",
    FeedbackState.FAULT: "capture ceremony faulted",
}


class EnrollmentStation:
    """Owns the SAME reference apparatus the binding UX uses
    (``FingerprintStorageService`` + ``MockFingerprintHAL`` +
    ``FingerprintDevice``) and drives a granular, step-scripted enrollment
    ceremony over it, publishing the live feedback state at every step."""

    def __init__(self, bus: Any, *, device_id: str = DEFAULT_DEVICE,
                 gated_capability: str = "open_hatch") -> None:
        self.bus = bus
        self.device_id = device_id
        self.svc = FingerprintStorageService()
        self.svc.attach(bus)
        self.store = self.svc.store
        self.hal = MockFingerprintHAL()
        self.device = FingerprintDevice(
            hal=self.hal, bus=bus, device_id=device_id,
            gated_capability=gated_capability)
        self.bus.ensure_namespace(VIEW_STATION, engine_config="json")
        self._templates: List[Dict[str, Any]] = []
        self._person = ""
        self._display_name = ""
        self._template_count = 0
        # optional hook, called with every published step dict — lets a
        # driver capture a mid-ceremony frame (e.g. at CAPTURE_RETRY) without
        # the station knowing anything about rendering.
        self.on_step: Optional[Any] = None

    # -- the ceremony --------------------------------------------------

    def enroll_person(
        self,
        person_id: str,
        display_name: str,
        *,
        ordinals: Sequence[int] = (1, 2),
        retry_at: Optional[Dict[int, Iterable[int]]] = None,
    ) -> Dict[str, Any]:
        """Drive a FULL enrollment ceremony for one person: up to two
        templates, each running its announced ``captures_per_template``
        captures. ``retry_at`` maps ordinal -> the set of 1-based capture
        ATTEMPT numbers (within that template) to reject once before
        accepting — the way a real place-lift-place retry happens: the
        attempt is re-presented, not skipped.

        Returns the final published station dict.
        """
        retry_at = retry_at or {}
        # Person CRUD lands directly on the store — a UI-to-service API call
        # ([CXC-FPSVC-PERSON-CRUD]), never sensor bus RPC — mirroring how the
        # binding UX's binder does it, reused here as the same store method.
        try:
            self.store.create_person(person_id, display_name or person_id)
        except KeyError:
            pass  # already exists; enrollment does not rename

        self._person = person_id
        self._display_name = display_name or person_id
        self._template_count = len(ordinals)
        self._templates = [
            {"ordinal": o, "status": "pending", "retries": 0} for o in ordinals
        ]
        self._publish(FeedbackState.READY, template_ordinal=None,
                      capture_done=0, capture_required=0)

        for ordinal in ordinals:
            self._enroll_one_template(ordinal, retry_at.get(ordinal, ()))

        return self._publish(FeedbackState.IDLE, template_ordinal=None,
                             capture_done=0, capture_required=0, done=True)

    def _enroll_one_template(self, ordinal: int,
                             retry_attempts: Iterable[int]) -> None:
        retry_set = set(retry_attempts)
        session = self.device.hal.begin_enroll(ordinal)  # [CXC-FPHAL-ENROLLMENT-SEQUENCE]
        required = session.captures_required
        self._mark_template(ordinal, status="capturing")
        self._publish(FeedbackState.READY, template_ordinal=ordinal,
                      capture_done=0, capture_required=required)

        attempt = 0
        while not session.complete:
            attempt += 1
            self._publish(FeedbackState.AWAITING_FINGER, template_ordinal=ordinal,
                          capture_done=session.captures_done,
                          capture_required=required)
            if attempt in retry_set:
                self._mark_template(ordinal, retries=self._retries(ordinal) + 1)
                self._publish(FeedbackState.CAPTURE_RETRY, template_ordinal=ordinal,
                              capture_done=session.captures_done,
                              capture_required=required,
                              retry_count=self._retries(ordinal))
                continue  # re-present the SAME capture; nothing advanced

            self._publish(FeedbackState.CAPTURE_ACCEPTED, template_ordinal=ordinal,
                          capture_done=session.captures_done,
                          capture_required=required,
                          retry_count=self._retries(ordinal))
            done = session.capture()  # HAL/session drives itself; on the
                                       # final capture it ALLOCATES the slot
                                       # and writes the template.
            self._publish(FeedbackState.ENROLL_PROGRESS, template_ordinal=ordinal,
                          capture_done=done, capture_required=required,
                          retry_count=self._retries(ordinal))

        # Ceremony complete for this template: readback + upload
        # ([CXC-FPSVC-ENROLL-ORCHESTRATION] — keyed by (person, ordinal),
        # NEVER by slot).
        self._mark_template(ordinal, status="captured")
        self._publish(FeedbackState.BUSY, template_ordinal=ordinal,
                      capture_done=required, capture_required=required,
                      retry_count=self._retries(ordinal))
        template = self.device.hal.read_template(session.slot)
        self.store.upsert_template(self._person, ordinal, template)
        self._mark_template(ordinal, status="uploaded")
        self._publish(FeedbackState.ACCEPT, template_ordinal=ordinal,
                      capture_done=required, capture_required=required,
                      retry_count=self._retries(ordinal))

    # -- template bookkeeping -------------------------------------------

    def _template_row(self, ordinal: int) -> Dict[str, Any]:
        for t in self._templates:
            if t["ordinal"] == ordinal:
                return t
        raise KeyError(ordinal)

    def _retries(self, ordinal: int) -> int:
        return int(self._template_row(ordinal).get("retries", 0))

    def _mark_template(self, ordinal: int, **fields: Any) -> None:
        self._template_row(ordinal).update(fields)

    # -- publish ----------------------------------------------------------

    def _publish(self, feedback: FeedbackState, *, template_ordinal, capture_done,
                capture_required, retry_count: int = 0,
                done: bool = False) -> Dict[str, Any]:
        # Actually drive the reference apparatus's feedback ring — the ONLY
        # write to it, commanded from OUTSIDE the HAL ([CXC-FPHAL-FEEDBACK-
        # STATES]). Progress is carried ONLY on ENROLL_PROGRESS.
        progress = ((capture_done, capture_required)
                   if feedback is FeedbackState.ENROLL_PROGRESS else None)
        try:
            self.hal.command_feedback(feedback, progress)
        except Exception:
            pass  # UNSUPPORTED on a no-feedback-channel device; not fatal
        payload = {
            "person": self._person,
            "display_name": self._display_name,
            "template_ordinal": template_ordinal,
            "template_count": self._template_count,
            "capture_done": capture_done,
            "capture_required": capture_required,
            "feedback": feedback.value,
            "message": _MESSAGE.get(feedback, ""),
            "retry_count": retry_count,
            "templates": [dict(t) for t in self._templates],
            "enrolled_count": (len(self.store.ordinals_for(self._person))
                              if self._person in self.store.persons else 0),
            "done": done,
        }
        self.bus.publish_state(VIEW_STATION, payload, source="fp_enroll_station")
        if self.on_step is not None:
            self.on_step(payload)
        return payload
