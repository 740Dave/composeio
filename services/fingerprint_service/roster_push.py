"""Roster push origination and reconciliation-by-subscription.

[CXC-FPSVC-PUSH-ORIGINATION] — the service originates the push as
``{template_set_uuid, entry_count, role_capability_table, ordered entries of
(person, ordinal, character, rbac, template)}``. Identity of the set is its
UUID; completeness is its entry_count. There is NO hash of the roster or of any
template ([CXC-FPBUS-WHAT-NEVER-CROSSES]). The entries are ORDERED and name NO
slot; the device assigns slots by install order ([CXC-FPBUS-PUSH-SEQUENCE]
assigns-slots-by-order).

[CXC-FPSVC-RBAC-RIDES-TO-DEVICE] — each entry carries its character's role and
the push as a whole carries the small global role-to-capability table. The
service does NOT pre-evaluate the gate ([DD-FPID-RBAC-EVAL-LOCUS]).

[CXC-FPSVC-RECONCILE-BY-SUBSCRIPTION] — the service learns each device's held
set from the ``fingerprint_ready`` heartbeat's ``template_set_uuid``. It MUST
NOT query a device. A device whose held UUID differs from the desired set, or
which emits ``fingerprint_push_needed``, is re-pushed.

The push-time validations ([CXC-FPBUS-REQ-SET-SIZE-LIMIT],
[CXC-FPBUS-REQ-SET-UUID-PRESENT], [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD]) are the
REFERENCE ``validate_publishable`` — reused, not re-implemented — so a set that
fails any of them never reaches the bus. The set-size cap and the template-size
guard are sized from the TARGET DEVICE'S announced capabilities
([CXC-FPBUS-MSG-DEVICE-ANNOUNCE]), never a hardcoded constant — see
:meth:`RosterPublisher._target_capacity`.

[DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18] — ``build_push`` still
computes entries carrying the RAW template bytes (that is what
``validate_publishable``'s template-size guard and the uuid-by-signature
dedup both need — a `fingerprint://` ref's byte length is the length of a
URI string, not of a template, and two pushes that differ only in which fresh
opaque id they minted must still be recognised as the SAME desired set).
``push_to_devices`` converts to the WIRE shape ONLY AFTER validation passes:
every entry's ``template`` becomes a `fingerprint://<opaque-id>` reference
uploaded to the dedicated template store ([DD-FPID-TEMPLATE-REF-IS-SERVICE-
PRIVATE] — a fresh uuid, never a path built from person/ordinal/module), so
the published message is JSON-serialisable on ANY transport. This is
UNCONDITIONAL — never gated on which transport the bus happens to be using
right now (DP-CIO-6: transport is an implementation detail, not an
architectural decision; a JSON-only carve-out here is exactly the mistake it
forbids). The upload is cached by the same desired-set signature the uuid
cache uses, so an idempotent re-push reuses the previously uploaded refs
rather than re-uploading byte-identical templates.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# REUSE the reference precondition, do not re-implement it.
from composeio.peripherals.fingerprint.push_validation import validate_publishable

from . import topics
from .cast import CastMember, select_cast
from .store import SupersetStore

# The publish seam. Matches ``ComposeIOBus.publish_event`` exactly (and the
# reference's FingerprintBusSink), so a live bus is a drop-in and a recorder is
# trivial. The service never imports the bus.
from composeio.peripherals.fingerprint.device import FingerprintBusSink

# The template-store PUT seam. Matches ``composeio.resolve.put_bytes`` exactly
# (uri, bytes) -> uri — so the live dedicated store is a drop-in and a test
# fake (an in-memory dict, no HTTP) is trivial to inject in its place via
# RosterPublisher.template_store_put.
from composeio.resolve import put_bytes as _default_template_store_put


# A logical-identity signature of one entry, WITH the template bytes, compared by
# equality in service memory. This is NOT a hash and never crosses the bus — it
# only decides whether the desired set changed, which governs UUID minting.
_EntrySignature = tuple[str, int, str, str, bytes]


@dataclass
class PushResult:
    """The outcome of an origination attempt."""

    published: bool
    template_set_uuid: str | None
    entry_count: int
    devices: list[str]
    reason: str = ""  # non-empty only when published is False


@dataclass
class RosterPublisher:
    """Originates pushes and reconciles device state from heartbeats. One
    instance per service; it holds the desired-set bookkeeping the push protocol
    needs (a device inventory, per-device desired UUID, and the ack/held state)
    — the cost of PUSH the bus contract accepted ([DD-FPBUS-ROSTER-IS-PUSH])."""

    store: SupersetStore
    bus: FingerprintBusSink
    source: str = "fingerprint_service"
    # device_id -> its announced capabilities dict, SECOND-HAND from
    # [CXC-FPBUS-MSG-DEVICE-ANNOUNCE] ({slot_count, template_size_bytes,
    # supports_readback}) — never queried, never a constant. Defaults to a
    # lookup that knows nothing, so a device this instance hasn't followed
    # simply has no announced cap to enforce yet.
    capabilities_lookup: Callable[[str], dict[str, Any] | None] = field(
        default=lambda device_id: None
    )
    # The template-store PUT seam ([DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-
    # RAISES-69DC18]): (uri, bytes) -> uri, matching ``composeio.resolve.
    # put_bytes`` exactly. Defaults to the REAL dedicated store; a test injects
    # an in-memory fake so the reference/integration suites never need a live
    # HTTP server. Never bus_storage's put_bytes target — the uri this is
    # called with always starts `fingerprint://`.
    template_store_put: Callable[[str, bytes], str] = field(
        default=_default_template_store_put
    )

    # module -> (signature-of-desired-set -> minted uuid). Reused for an
    # idempotent re-push; a genuine change mints a fresh uuid.
    _uuid_by_signature: dict[str, tuple[tuple[_EntrySignature, ...], str]] = field(
        default_factory=dict
    )
    # module -> (signature-of-desired-set -> wire entries, template bytes
    # already replaced by their `fingerprint://` refs). Reused alongside the
    # uuid cache so an idempotent re-push does not re-upload byte-identical
    # templates to the store.
    _wire_entries_by_signature: dict[
        str, tuple[tuple[_EntrySignature, ...], list[dict[str, Any]]]
    ] = field(default_factory=dict)
    # device -> the uuid the service most recently asked it to hold.
    _device_desired_uuid: dict[str, str] = field(default_factory=dict)
    # device -> the module whose cast it was last pushed, so an unconverged
    # device can be re-pushed without an out-of-band mapping.
    _device_module: dict[str, str] = field(default_factory=dict)
    # device -> the uuid the service LEARNED it holds (from fingerprint_ready
    # ONLY — never a query).
    _device_held_uuid: dict[str, str] = field(default_factory=dict)
    # device -> last push ack ("loaded" | "failed").
    _device_ack: dict[str, str] = field(default_factory=dict)

    # ---- origination ([CXC-FPSVC-PUSH-ORIGINATION]) ----------------------

    def build_push(self, module: str) -> dict[str, Any]:
        """Assemble the ordered push payload for a module's cast. No slot, no
        hash. entry_count is the completeness testimony; the UUID is identity.

        Entries here carry the RAW template bytes — this is the shape
        ``validate_publishable`` checks (its size guard needs a real byte
        length) and the shape the uuid-by-signature cache keys on. It is NOT
        the wire shape: :meth:`push_to_devices` converts to `fingerprint://`
        references AFTER validation passes, never before
        ([DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18])."""
        members = select_cast(self.store, module)
        entries = self._ordered_entries(members)
        signature = self._signature_of(entries)
        uuid = self._uuid_for_signature(module, signature)
        role_table = self._role_table(members)
        return {
            "template_set_uuid": uuid,
            "entry_count": len(entries),
            "role_capability_table": role_table,
            "entries": entries,
        }

    @staticmethod
    def _signature_of(entries: list[dict[str, Any]]) -> tuple[_EntrySignature, ...]:
        return tuple(
            (e["person"], e["ordinal"], e["character"], e["rbac"], e["template"])
            for e in entries
        )

    def _ordered_entries(self, members: list[CastMember]) -> list[dict[str, Any]]:
        """One entry per (cast member, ordinal), in install order: cast order,
        then ordinal. Each entry carries the ROLE (not a pre-evaluated yes/no)
        and the template. NO slot ([CXC-FPSVC-SCEN-PUSH-CARRIES-NO-SLOT])."""
        entries: list[dict[str, Any]] = []
        for m in members:
            for rec in self.store.templates_for(m.person):
                entries.append(
                    {
                        "person": rec.person,
                        "ordinal": rec.ordinal,
                        "character": m.character,
                        "rbac": m.role,
                        "template": rec.template,
                    }
                )
        return entries

    def _role_table(self, members: list[CastMember]) -> dict[str, list[str]]:
        """The small global role-to-capability table for the roles in this cast
        ([CXC-FPSVC-RBAC-RIDES-TO-DEVICE])."""
        roles = {m.role for m in members}
        return {r: list(self.store.capabilities_for_role(r)) for r in roles}

    def _uuid_for_signature(
        self, module: str, signature: tuple[_EntrySignature, ...]
    ) -> str:
        """Reuse the module's UUID for an identical desired set; mint a fresh one
        when it changed. This is what makes a genuine change reset the device's
        auth counter and an idempotent re-push a no-op ([CXC-FPBUS-MSG-
        FINGERPRINT-READY] counter-semantics)."""
        cached = self._uuid_by_signature.get(module)
        if cached is not None and cached[0] == signature:
            return cached[1]
        minted = _uuid.uuid4().hex
        self._uuid_by_signature[module] = (signature, minted)
        return minted

    def _wire_entries(
        self, module: str, entries: list[dict[str, Any]],
        signature: tuple[_EntrySignature, ...],
    ) -> list[dict[str, Any]]:
        """The WIRE shape of ``entries``: each ``template`` replaced by a
        `fingerprint://<opaque-id>` reference uploaded to the dedicated
        template store ([DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]
        — a JSON bus transport cannot carry raw bytes, DP-CIO-18). Called ONLY
        after ``validate_publishable`` has passed on the RAW-bytes entries.

        The opaque id is a fresh uuid, minted here and NOWHERE else
        ([DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE]) — it names nothing about
        person, ordinal, module, or character; that mapping lives only in this
        entry, exactly as it already does for the raw-bytes shape.

        Cached by the SAME desired-set signature the uuid cache uses, so a
        genuine re-push of an unchanged cast reuses the previously uploaded
        refs rather than re-uploading byte-identical templates on every call
        (``start_game`` on an unchanged cast, or the reconcile loop's repeated
        ``repush_device``)."""
        cached = self._wire_entries_by_signature.get(module)
        if cached is not None and cached[0] == signature:
            return cached[1]
        wire_entries = []
        for entry in entries:
            opaque_id = _uuid.uuid4().hex
            ref = self.template_store_put(f"fingerprint://{opaque_id}", entry["template"])
            wire_entries.append({**entry, "template": ref})
        self._wire_entries_by_signature[module] = (signature, wire_entries)
        return wire_entries

    # ---- publishing ([CXC-FPSVC-PUSH-TRIGGER] -> [CXC-FPSVC-PUSH-ORIGINATION])

    def _target_capacity(
        self, devices: list[str]
    ) -> tuple[int | None, int | None, str]:
        """Read the ANNOUNCED capacity for every target device
        ([CXC-FPBUS-MSG-DEVICE-ANNOUNCE]) and reduce it to the two scalars
        ``validate_publishable`` needs. NEVER a hardcoded constant.

        ``slot_count`` is the MIN across devices with a known announce — the
        most restrictive device gates the whole push ([CXC-FPBUS-REQ-SET-SIZE-
        LIMIT]: "if a push targets multiple devices, use the min"). A device
        that has never announced contributes nothing — there is no cap to
        enforce against a device the service knows nothing about, so it is
        skipped rather than defaulted.

        ``template_size_bytes`` must be the SAME value for every device that
        has one, since one set is pushed byte-identical to all of them; a
        mismatch across the target devices means no single template length can
        satisfy them and is refused with its own reason before ever reaching
        ``validate_publishable``.

        A capabilities dict that IS present but omits a required key is a
        FAIL-LOUD bug in the announce, never silently defaulted
        ([CXC-FPBUS-MSG-DEVICE-ANNOUNCE] constraint-consumer)."""
        slot_counts: list[int] = []
        template_sizes: set[int] = set()
        for device_id in devices:
            caps = self.capabilities_lookup(device_id)
            if caps is None:
                continue
            if "slot_count" not in caps or "template_size_bytes" not in caps:
                raise KeyError(
                    f"announced capabilities for {device_id!r} are missing a "
                    f"required field (have {sorted(caps.keys())}) — a "
                    "consumer must fail loud on a missing capability field"
                )
            slot_counts.append(caps["slot_count"])
            template_sizes.add(caps["template_size_bytes"])

        slot_count = min(slot_counts) if slot_counts else None
        if len(template_sizes) > 1:
            return (
                slot_count,
                None,
                "target devices announce mismatched template_size_bytes "
                f"{sorted(template_sizes)} — a single push cannot satisfy all "
                "of them",
            )
        template_size_bytes = next(iter(template_sizes)) if template_sizes else None
        return slot_count, template_size_bytes, ""

    def push_to_devices(self, module: str, devices: list[str]) -> PushResult:
        """Originate one push for the module's cast and publish it to every
        device in scope. Every device in a module's scope receives the SAME cast
        ([CXC-FPSVC-CAST-SELECTION]). Refuses (publishing NOTHING) if the set
        fails a push-time validation."""
        push = self.build_push(module)

        slot_count, template_size_bytes, mismatch_reason = self._target_capacity(
            devices
        )
        if mismatch_reason:
            return PushResult(
                published=False,
                template_set_uuid=push.get("template_set_uuid"),
                entry_count=push["entry_count"],
                devices=[],
                reason=mismatch_reason,
            )

        ok, reason = validate_publishable(
            push, slot_count=slot_count, template_size_bytes=template_size_bytes
        )
        if not ok:
            # A set that fails a precondition is never published — no message
            # reaches the bus ([CXC-FPBUS-REQ-SET-SIZE-LIMIT], [CXC-FPBUS-REQ-
            # SET-UUID-PRESENT]).
            return PushResult(
                published=False,
                template_set_uuid=push.get("template_set_uuid"),
                entry_count=push["entry_count"],
                devices=[],
                reason=reason,
            )

        # Validation passed against the RAW-bytes push. NOW, and only now,
        # convert to the WIRE shape — every template a `fingerprint://` ref,
        # never bytes — so the published message is JSON-serialisable on
        # ANY transport ([DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-
        # 69DC18]). Unconditional: no transport-specific carve-out (DP-CIO-6).
        signature = self._signature_of(push["entries"])
        wire_entries = self._wire_entries(module, push["entries"], signature)
        wire_push = {**push, "entries": wire_entries}

        uuid = push["template_set_uuid"]
        for device_id in devices:
            self._device_desired_uuid[device_id] = uuid
            self._device_module[device_id] = module
            self.bus.publish_event(
                topics.template_set(device_id),
                "template_set",
                dict(wire_push),
                source=self.source,
            )
        return PushResult(
            published=True,
            template_set_uuid=uuid,
            entry_count=push["entry_count"],
            devices=list(devices),
        )

    # ---- ack + reconciliation ([CXC-FPSVC-RECONCILE-BY-SUBSCRIPTION]) -----

    def on_template_set_loaded(self, device_id: str, data: dict[str, Any]) -> None:
        """Device acknowledged the push. Recorded as an ack; the HELD uuid is
        still learned only from the ready heartbeat, per the contract."""
        self._device_ack[device_id] = "loaded"

    def on_template_set_failed(self, device_id: str, data: dict[str, Any]) -> None:
        self._device_ack[device_id] = "failed"

    def on_fingerprint_ready(self, device_id: str, data: dict[str, Any]) -> bool:
        """Learn the device's held set from its heartbeat. Returns True if the
        device is UNCONVERGED (held != desired) and should be re-pushed. This is
        the ONLY place the service learns a held uuid — never a query."""
        held = data.get("template_set_uuid")
        if held is not None:
            self._device_held_uuid[device_id] = held
        desired = self._device_desired_uuid.get(device_id)
        return desired is not None and held != desired

    def on_fingerprint_push_needed(self, device_id: str) -> bool:
        """An unconfigured device asks for a set. It is unconverged by
        definition; report that a re-push is due."""
        self._device_held_uuid.pop(device_id, None)
        return self._device_desired_uuid.get(device_id) is not None

    def repush_device(self, device_id: str) -> PushResult | None:
        """Re-push the device's last cast to just that device. Used by the
        subscription-driven repair loop to converge an unconverged device
        ([CXC-FPSVC-RECONCILE-BY-SUBSCRIPTION]). Returns None if the service
        never pushed this device (nothing to reconcile to)."""
        module = self._device_module.get(device_id)
        if module is None:
            return None
        return self.push_to_devices(module, [device_id])

    # ---- introspection for tests / a monitor -----------------------------

    def held_uuid(self, device_id: str) -> str | None:
        return self._device_held_uuid.get(device_id)

    def desired_uuid(self, device_id: str) -> str | None:
        return self._device_desired_uuid.get(device_id)

    def ack(self, device_id: str) -> str | None:
        return self._device_ack.get(device_id)

    def is_converged(self, device_id: str) -> bool:
        desired = self._device_desired_uuid.get(device_id)
        return desired is not None and self._device_held_uuid.get(device_id) == desired
