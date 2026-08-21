"""FingerprintCastingBinder — in-process controller behind the binding UI.

It is the ComposerCore-shaped glue between the render scene and the
``fingerprint_storage`` service:

  scene widgets  --(fp/cmd/* state)-->  BINDER  --(store mutation)-->  SERVICE
       ^                                   |
       +----------(fp/view/* state)--------+

The binder OWNS a :class:`FingerprintStorageService` (which owns the superset
:class:`SupersetStore`) plus an in-process REFERENCE device (the same
``FingerprintDevice`` + ``MockFingerprintHAL`` the service integration test
drives), so:

* template capture runs the REAL enroll ceremony (``svc.enroller.enroll`` ->
  device begin/capture/read-back -> superset upsert keyed by (person, ordinal));
* "start game" calls the REAL push trigger (``svc.start_game``), and the
  reference device runs its REAL receipt and acks — so the roster push actually
  FIRES on the bus and is received, not merely simulated.

It rides the EXISTING ComposeIOBus (no new transport): commands arrive as bus
STATE on ``fp/cmd/*`` (published by the scene's emit_buttons, which interpolate
the live text_input values); views are published as bus STATE on ``fp/view/*``
(rendered by the scene's info_list panels).

Slots are never named anywhere — the store is logical (person, ordinal) only
([CXC-FPSVC-LOGICAL-IDENTITY-ONLY]).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any, Callable

# service surface (PYTHONPATH must include ``services``)
from fingerprint_storage import topics
from fingerprint_storage.service import FingerprintStorageService

# the reference device layer — reused unchanged
from composeio.peripherals.fingerprint.device import FingerprintDevice
from composeio.peripherals.fingerprint.mock import MockFingerprintHAL

# The template-store PUT seam ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-
# A4128F] — the DEVICE->SERVICE mirror of [DEFECT-ROSTERPUBLISHER-PUSH-TO-
# DEVICES-RAISES-69DC18]). Matches ``composeio.resolve.put_bytes`` exactly, so
# a live dedicated store is a drop-in and a test fake is trivial to inject.
from composeio.resolve import put_bytes as _default_template_store_put

DEFAULT_MODULE = "galactica"
DEFAULT_DEVICE = "hatch-01"

CMD_ADD_PERSON = "fp/cmd/add_person"
CMD_CAPTURE = "fp/cmd/capture_template"
CMD_DEFINE_ROLE = "fp/cmd/define_role"
CMD_DEFINE_CHARACTER = "fp/cmd/define_character"
CMD_CAST = "fp/cmd/cast"
CMD_START_GAME = "fp/cmd/start_game"

VIEW_ROSTER = "fp/view/roster"
VIEW_CASTINGS = "fp/view/castings"
VIEW_ROLES = "fp/view/roles"
VIEW_PUSH = "fp/view/push"
VIEW_STATUS = "fp/view/status"

# templates allowed per person — sourced from the store's own count, NOT
# hard-coded into a slot model (the store carries no bound; two is a UI policy).
MAX_TEMPLATES = 2


class _ReferenceDeviceHarness:
    """The device-firmware BUS CLIENT twin — the ONLY glue not owned by the
    service.  Subscribes the REFERENCE device to the service-originated push +
    enroll topics and drives the reference ceremony; it adds NO device logic
    (identical in spirit to the service's own integration test)."""

    def __init__(self, bus: Any, device_id: str, gated_capability: str, *,
                 template_store_put: Callable[[str, bytes], str] | None = None):
        self.bus = bus
        self.device_id = device_id
        self.hal = MockFingerprintHAL()
        self.device = FingerprintDevice(
            hal=self.hal, bus=bus, device_id=device_id,
            gated_capability=gated_capability)
        self.pushes_received = 0
        # [DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]: the captured
        # readback template is uploaded to the dedicated template store under
        # a fresh opaque id and published as a `fingerprint://` REFERENCE,
        # never inline bytes — unconditional, DP-CIO-6. None (the default)
        # is the REAL dedicated store; a test injects an in-memory fake.
        self.template_store_put = template_store_put or _default_template_store_put
        bus.subscribe_events(topics.template_set(device_id), self._on_push)
        bus.subscribe_events(topics.enroll_cmd(device_id), self._on_enroll)

    def announce(self) -> None:
        # [CXC-FPBUS-MSG-DEVICE-ANNOUNCE] — ONLY the three externally-consumed
        # fields; feedback_channels and captures_per_template are device-local
        # and NOT announced (2026-08-18 consumer-audit trim).
        caps = self.hal.capabilities()
        self.bus.publish_event(
            topics.device_announce(self.device_id), "announce",
            {"device_id": self.device_id,
             "capabilities": {
                 "slot_count": caps.slot_count,
                 "template_size_bytes": caps.template_size_bytes,
                 "supports_readback": caps.supports_readback}},
            source=self.device_id)

    def _on_push(self, namespace, event_type, data):
        self.pushes_received += 1
        self.device.receive_push(data)          # REAL receipt logic

    def _on_enroll(self, namespace, event_type, data):
        session = self.hal.begin_enroll(data["ordinal"])
        while not session.complete:
            session.capture()
        template = self.hal.read_template(session.slot)
        # Upload the captured bytes to the dedicated template store and put
        # the REFERENCE on the wire — never the raw bytes
        # ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]). The opaque id
        # is a fresh uuid minted here, naming nothing about person/ordinal
        # ([DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE]).
        opaque_id = _uuid.uuid4().hex
        ref = self.template_store_put(f"fingerprint://{opaque_id}", template)
        self.bus.publish_event(
            topics.enroll_complete(self.device_id), "enroll_complete",
            {"person": data["person"], "ordinal": data["ordinal"],
             "template_ref": ref, "request_id": data["request_id"]},
            source=self.device_id)

    def emit_ready(self):
        return self.device.emit_heartbeat()


class FingerprintCastingBinder:
    """Owns the service + reference device; bridges the bus command/view
    namespaces to the superset model."""

    def __init__(self, bus: Any, *, module: str = DEFAULT_MODULE,
                 device_id: str = DEFAULT_DEVICE,
                 gated_capability: str = "open_hatch",
                 template_store_put: Callable[[str, bytes], str] | None = None,
                 template_resolver: Callable[[str], bytes] | None = None) -> None:
        self.bus = bus
        self.module = module
        self.device_id = device_id
        # template_resolver is the SERVICE's GET seam ([DEFECT-ENROLL-PY-
        # ENROLL-COMPLETE-READBACK-A4128F]); template_store_put (below) is the
        # reference DEVICE's PUT seam — a test injects the SAME in-memory fake
        # store to both so an enroll round-trips without a live HTTP server.
        self.svc = FingerprintStorageService(template_resolver=template_resolver)
        self.svc.attach(bus)
        self.store = self.svc.store
        # a captured copy of the last roster push, for the receipt panel
        self.last_push_msg: dict | None = None
        # the in-process reference device
        self.device = _ReferenceDeviceHarness(
            bus, device_id, gated_capability,
            template_store_put=template_store_put)
        self.svc.follow_device(device_id)
        self.device.announce()
        # watch the push actually land on the bus ([CXC-FPSVC-PUSH-ORIGINATION])
        bus.subscribe_events(topics.template_set(device_id), self._record_push)

    # -- wiring ------------------------------------------------------------

    def wire(self) -> None:
        """Subscribe every command namespace + seed empty views.  Idempotent
        enough for a single call at setup."""
        self.bus.subscribe(CMD_ADD_PERSON, self._h_add_person)
        self.bus.subscribe(CMD_CAPTURE, self._h_capture)
        self.bus.subscribe(CMD_DEFINE_ROLE, self._h_define_role)
        self.bus.subscribe(CMD_DEFINE_CHARACTER, self._h_define_character)
        self.bus.subscribe(CMD_CAST, self._h_cast)
        self.bus.subscribe(CMD_START_GAME, self._h_start_game)
        self._status("ready — enter a person to begin")
        self.refresh_views()

    def _record_push(self, namespace, event_type, data):
        self.last_push_msg = dict(data or {})

    # -- command handlers (bus STATE callbacks: ns, new, old) --------------

    @staticmethod
    def _payload(value: Any) -> dict:
        return value if isinstance(value, dict) else {}

    def _h_add_person(self, ns, value, old=None):
        d = self._payload(value)
        pid = str(d.get("person_id", "")).strip()
        name = str(d.get("display_name", "")).strip()
        if not pid:
            return self._status("add person: person_id is required")
        try:
            self.store.create_person(pid, name or pid)
            self._status(f"added person {pid!r}")
        except KeyError:
            # already exists -> treat as a rename (mutable editor op)
            self.store.update_person(pid, display_name=name or pid)
            self._status(f"updated person {pid!r}")
        self.refresh_views()

    def _h_capture(self, ns, value, old=None):
        # The ordinal is EXPLICIT (finger #1 / finger #2), so two capture
        # commands carry distinct payloads and both fire — a command modeled as
        # bus STATE dedups an identical consecutive payload, and re-capturing
        # the SAME finger is an idempotent overwrite anyway ([CXC-FPSVC-ENROLL-
        # ORCHESTRATION]: upsert keyed by (person, ordinal)).  See the escalation
        # note in the deliverable.
        d = self._payload(value)
        person = str(d.get("person", "")).strip()
        try:
            ordinal = int(d.get("ordinal", 0))
        except (TypeError, ValueError):
            ordinal = 0
        if person not in self.store.persons:
            return self._status(f"capture: unknown person {person!r}")
        if ordinal < 1 or ordinal > MAX_TEMPLATES:
            return self._status(
                f"capture: ordinal must be 1..{MAX_TEMPLATES} (got {ordinal})")
        # REAL enroll ceremony via the reference device.
        self.svc.enroller.enroll(self.device_id, person, ordinal)
        self._status(f"captured finger #{ordinal} for {person!r}")
        self.refresh_views()

    def _h_define_role(self, ns, value, old=None):
        d = self._payload(value)
        role = str(d.get("role", "")).strip()
        caps_raw = d.get("capabilities", "")
        if not role:
            return self._status("define role: role is required")
        caps = [c.strip() for c in str(caps_raw).replace(";", ",").split(",")
                if c.strip()]
        self.store.define_role(role, caps)
        self._status(f"role {role!r} = {caps or '[]'}")
        self.refresh_views()

    def _h_define_character(self, ns, value, old=None):
        d = self._payload(value)
        character = str(d.get("character", "")).strip()
        role = str(d.get("role", "")).strip()
        if not character or not role:
            return self._status("define character: character and role required")
        if role not in self.store.role_capabilities:
            # be forgiving — auto-create an empty role so casting can proceed
            self.store.define_role(role, [])
        self.store.define_character(character, role)
        self._status(f"character {character!r} -> role {role!r}")
        self.refresh_views()

    def _h_cast(self, ns, value, old=None):
        d = self._payload(value)
        person = str(d.get("person", "")).strip()
        character = str(d.get("character", "")).strip()
        module = str(d.get("module", "") or self.module).strip()
        if person not in self.store.persons:
            return self._status(f"cast: unknown person {person!r}")
        if character not in self.store.characters:
            return self._status(
                f"cast: unknown character {character!r} — define it first")
        self.store.cast(module, person, character)
        self._status(f"cast {person!r} as {character!r} in {module!r}")
        self.refresh_views()

    def _h_start_game(self, ns, value, old=None):
        d = self._payload(value)
        module = str(d.get("module", "") or self.module).strip()
        result = self.svc.start_game(module, [self.device_id])
        if result.published:
            self.device.emit_ready()            # device confirms convergence
            ack = self.svc.publisher.ack(self.device_id)
            conv = self.svc.publisher.is_converged(self.device_id)
            self._status(
                f"START GAME {module!r}: push fired "
                f"({result.entry_count} entries) ack={ack} converged={conv}")
        else:
            self._status(f"START GAME refused: {result.reason}")
        self.refresh_views()

    # -- view formatting + publish ----------------------------------------

    def refresh_views(self) -> None:
        self._publish(VIEW_ROSTER, self._fmt_roster())
        self._publish(VIEW_CASTINGS, self._fmt_castings())
        self._publish(VIEW_ROLES, self._fmt_roles())
        self._publish(VIEW_PUSH, self._fmt_push())

    def _fmt_roster(self) -> str:
        if not self.store.persons:
            return ""
        # who each person is cast as, for a one-glance roster
        cast_by_person: dict[str, str] = {}
        for c in self.store.castings_for(self.module):
            role = self.store.characters.get(c.character, "?")
            cast_by_person[c.person] = f"{c.character} [{role}]"
        rows = []
        for pid, p in self.store.persons.items():
            n = len(self.store.ordinals_for(pid))
            cast = cast_by_person.get(pid, "— uncast —")
            rows.append(f"{pid:<10} {p.display_name:<14} fp:{n}/{MAX_TEMPLATES}"
                        f"  {cast}")
        return "\n".join(rows)

    def _fmt_castings(self) -> str:
        rows = []
        for c in self.store.castings_for(self.module):
            role = self.store.characters.get(c.character, "?")
            rows.append(f"{c.character:<12} <- {c.person:<10} role:{role}")
        return "\n".join(rows)

    def _fmt_roles(self) -> str:
        rows = []
        for role, caps in self.store.role_capabilities.items():
            chars = [ch for ch, r in self.store.characters.items() if r == role]
            cap_s = ", ".join(caps) if caps else "(no capabilities)"
            char_s = ("chars: " + ", ".join(chars)) if chars else "chars: —"
            rows.append(f"{role:<10} {cap_s}")
            rows.append(f"           {char_s}")
        return "\n".join(rows)

    def _fmt_push(self) -> str:
        m = self.last_push_msg
        if not m:
            return ""
        lines = [
            f"template_set_uuid: {str(m.get('template_set_uuid',''))[:16]}…",
            f"entry_count:       {m.get('entry_count')}",
            "role_capability_table:",
        ]
        for role, caps in (m.get("role_capability_table") or {}).items():
            lines.append(f"   {role}: {', '.join(caps) or '[]'}")
        lines.append("entries (person/ordinal/character/rbac, NO slot):")
        for e in m.get("entries", []):
            lines.append(
                f"   {e['person']}#{e['ordinal']} as {e['character']} "
                f"[{e['rbac']}]")
        return "\n".join(lines)

    # -- helpers -----------------------------------------------------------

    def _status(self, text: str) -> None:
        self._publish(VIEW_STATUS, text)

    def _publish(self, ns: str, value: Any) -> None:
        self.bus.ensure_namespace(ns, engine_config="json")
        self.bus.publish_state(ns, value, source="fp_binder")
