"""In-process integration test: fingerprint_service <-> the reference device.

This is the machine-green proof that the SERVICE composes against the FROZEN
device contract. It runs against the REAL ``ComposeIOBus`` in-process (no MQTT,
no server, no hardware) and against the REFERENCE device layer
(``composeio.peripherals.fingerprint``) unchanged: the service pushes a cast,
the reference ``FingerprintDevice`` runs its REAL receipt logic on a
``MockFingerprintHAL``, confirms ``template_set_loaded``, and the service
reconciles the held uuid from the device's ``fingerprint_ready`` heartbeat.

The only thing this test SUPPLIES is the device firmware BUS CLIENT — the glue
C73 leaves "EMPTY. Device firmware bus client" — which subscribes the reference
device to the push and enroll topics. That glue calls only reference methods
(``receive_push``, ``begin_enroll``/``capture``/``read_template``,
``emit_heartbeat``); it adds no device logic.

Run standalone::

    PYTHONPATH=python:services python3 -m fingerprint_service.tests.test_integration

or under pytest::

    PYTHONPATH=python:services python3 -m pytest services/fingerprint_service/tests/test_integration.py -v
"""

from __future__ import annotations

import dataclasses
import json
import uuid as _uuid

from composeio.bus import ComposeIOBus
from composeio.peripherals.fingerprint.device import FingerprintDevice
from composeio.peripherals.fingerprint.hal import (
    Capabilities,
    MatchOutcome,
    MatchResult,
    Score,
)
from composeio.peripherals.fingerprint.mock import MockFingerprintHAL
from composeio.peripherals.fingerprint.push_validation import validate_publishable
from composeio.transport import BusMessage, MessageType
from composeio.transport_mqtt import MQTTTransport

from fingerprint_service import topics
from fingerprint_service.service import FingerprintService
from fingerprint_service.store import SupersetStore


# ---------------------------------------------------------------------------
# An in-memory stand-in for the dedicated template store
# ([DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]): no HTTP, no real
# `fingerprint_template_store` process, just the same (uri, bytes) -> uri /
# uri -> bytes seam `composeio.resolve.put_bytes`/`resolve_bytes` present —
# the reference/integration suite stays network-free. The real HTTP round
# trip through the actual store is proven separately in
# `test_template_reference.py`.
# ---------------------------------------------------------------------------
class _FakeTemplateStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put(self, uri: str, data: bytes) -> str:
        self._blobs[uri] = bytes(data)
        return uri

    def get(self, uri: str) -> bytes:
        return self._blobs[uri]


# ---------------------------------------------------------------------------
# The device firmware BUS CLIENT twin — the only glue this test supplies.
# It subscribes the REFERENCE device to the service-originated topics. It owns
# no device logic; every decision runs inside the reference FingerprintDevice
# and MockFingerprintHAL.
# ---------------------------------------------------------------------------
class DeviceHarness:
    def __init__(
        self,
        bus: ComposeIOBus,
        device_id: str,
        gated_capability: str,
        hal: MockFingerprintHAL | None = None,
        template_resolver=None,
        template_store_put=None,
    ):
        self.bus = bus
        self.device_id = device_id
        self.hal = hal if hal is not None else MockFingerprintHAL()
        self.device = FingerprintDevice(
            hal=self.hal,
            bus=bus,
            device_id=device_id,
            gated_capability=gated_capability,
            template_resolver=template_resolver,
        )
        self.pushes_received = 0
        self.enroll_commands: list[dict] = []
        # [DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]: the DEVICE-side
        # PUT seam for the enroll readback — the mirror of
        # ``template_resolver`` above, which is the DEVICE-side GET seam for
        # a roster push. None leaves the real dedicated store in place; a
        # test injects the SAME in-memory fake the service's
        # ``template_resolver`` reads from.
        self.template_store_put = template_store_put
        # subscribe the reference device to inbound service topics
        bus.subscribe_events(topics.template_set(device_id), self._on_push)
        bus.subscribe_events(topics.enroll_cmd(device_id), self._on_enroll)

    def announce(self) -> None:
        """[CXC-FPBUS-MSG-DEVICE-ANNOUNCE] — carries ONLY the three
        externally-consumed fields {slot_count, template_size_bytes,
        supports_readback}. feedback_channels and captures_per_template are
        device-local and NOT announced (2026-08-18 consumer-audit trim)."""
        caps = self.hal.capabilities()
        self.bus.publish_event(
            topics.device_announce(self.device_id),
            "announce",
            {
                "device_id": self.device_id,
                "capabilities": {
                    "slot_count": caps.slot_count,
                    "template_size_bytes": caps.template_size_bytes,
                    "supports_readback": caps.supports_readback,
                },
            },
            source=self.device_id,
        )

    def _on_push(self, namespace, event_type, data):
        self.pushes_received += 1
        # THE REFERENCE receipt logic runs here, unchanged.
        self.device.receive_push(data)

    def _on_enroll(self, namespace, event_type, data):
        self.enroll_commands.append(dict(data))
        # Drive the REFERENCE ceremony: begin_enroll -> capture until complete,
        # then read the captured template back and upload it.
        session = self.hal.begin_enroll(data["ordinal"])
        while not session.complete:
            session.capture()
        template = self.hal.read_template(session.slot)
        # [DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]: upload to the
        # dedicated template store and publish the REFERENCE, never inline
        # bytes. A lazy import mirrors FingerprintDevice._resolve_template's
        # own default — a caller that always injects a fake never pays for it.
        put = self.template_store_put
        if put is None:
            from composeio.resolve import put_bytes as put
        opaque_id = _uuid.uuid4().hex
        ref = put(f"fingerprint://{opaque_id}", template)
        self.bus.publish_event(
            topics.enroll_complete(self.device_id),
            "enroll_complete",
            {
                "person": data["person"],
                "ordinal": data["ordinal"],
                "template_ref": ref,
                "request_id": data["request_id"],
            },
            source=self.device_id,
        )

    def emit_ready(self):
        return self.device.emit_heartbeat()


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
# The mock's default announced template_size_bytes. Seeded fixture templates
# are sized to EXACTLY this by default so a push against a device announcing
# the default capabilities passes [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] and
# exercises the behaviour actually under test in each integration scenario.
_TEMPLATE_SIZE = MockFingerprintHAL().capabilities().template_size_bytes


def _synth_template(seed: bytes, size: int = _TEMPLATE_SIZE) -> bytes:
    return (seed * (size // len(seed) + 1))[:size]


def _seed_cast(
    store: SupersetStore,
    module: str,
    n_persons: int,
    templates_each: int,
    template_size: int = _TEMPLATE_SIZE,
):
    """Create n persons, a shared crew role gating 'open_hatch', a character per
    person, cast them in the module, and enroll ``templates_each`` templates
    each (directly, standing in for a prior enrollment)."""
    store.define_role("crew", ["open_hatch"])
    store.define_role("guest", [])  # a role WITHOUT the gated capability
    for i in range(1, n_persons + 1):
        pid = f"p{i}"
        store.create_person(pid, f"Person {i}")
        store.define_character(f"c{i}", "crew")
        store.cast(module, pid, f"c{i}")
        for ordinal in range(1, templates_each + 1):
            seed = f"tmpl-{pid}-{ordinal}".encode()
            store.upsert_template(pid, ordinal, _synth_template(seed, template_size))


class _Clock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def _run_push_ack_reconcile():
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)
    _seed_cast(svc.store, "m", n_persons=3, templates_each=2)

    dev = DeviceHarness(bus, "dev-1", gated_capability="open_hatch",
                         template_resolver=tstore.get)
    svc.follow_device("dev-1")
    dev.announce()

    result = svc.start_game("m", ["dev-1"])

    assert result.published is True
    assert result.template_set_uuid, "a set must carry a UUID"
    assert result.entry_count == 6, "3 persons * 2 templates = 6 ordered entries"
    # the device ran its REAL receipt and acked
    assert svc.publisher.ack("dev-1") == "loaded"

    # [DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]: the message that
    # actually went out over the bus carries `fingerprint://` REFERENCES, never
    # raw template bytes — and is genuinely JSON-serialisable (`to_json()`
    # would raise `UnserialisableValue` on the old shape).
    published = [m for m in bus.message_log if m.namespace == "dev-1/template_set"]
    assert published, "no template_set message was recorded on the bus"
    wire_entries = published[-1].data["entries"]
    assert wire_entries, "push carried no entries"
    for entry in wire_entries:
        assert isinstance(entry["template"], str) and entry["template"].startswith(
            "fingerprint://"
        ), f"entry template is not a fingerprint:// reference: {entry['template']!r}"
    published[-1].to_json()  # must not raise UnserialisableValue

    # and the bytes really did land in the DEDICATED template store, keyed by
    # the opaque ref — never inlined on the wire.
    for entry in wire_entries:
        assert tstore.get(entry["template"])  # resolves to real template bytes

    # reconcile-by-subscription: learn the held uuid from the heartbeat ONLY
    dev.emit_ready()
    assert svc.publisher.held_uuid("dev-1") == result.template_set_uuid
    assert svc.publisher.is_converged("dev-1") is True
    assert svc.reconcile_pending() == [], "nothing to re-push once converged"
    return result, bus, svc, dev


def test_push_ack_reconcile_loop_is_green():
    _run_push_ack_reconcile()


def test_push_carries_no_slot():
    # [CXC-FPSVC-SCEN-PUSH-CARRIES-NO-SLOT]
    store = SupersetStore()
    _seed_cast(store, "m", n_persons=3, templates_each=2)
    from fingerprint_service.roster_push import RosterPublisher

    class _Sink:
        def publish_event(self, *a, **k):
            pass

    pub = RosterPublisher(store=store, bus=_Sink())
    push = pub.build_push("m")
    assert "slot" not in push  # no envelope slot field
    for entry in push["entries"]:
        assert "slot" not in entry, f"an entry named a slot: {entry.keys()}"
        assert set(entry.keys()) == {"person", "ordinal", "character", "rbac", "template"}
    # completeness is entry_count, identity is the uuid, and NO hash anywhere
    assert push["entry_count"] == len(push["entries"])
    assert "hash" not in push and not any("hash" in k for k in push)


def test_re_push_same_cast_is_device_confirmed_noop():
    result, bus, svc, dev = _run_push_ack_reconcile()
    first_uuid = result.template_set_uuid

    dev.hal.reset_log()  # watch only the second push
    second = svc.start_game("m", ["dev-1"])

    assert second.template_set_uuid == first_uuid, "same cast reuses the uuid"
    ops = dev.hal.op_sequence()
    # a UUID match is no work: the device reads the stamp and does nothing else
    assert "empty" not in ops, f"re-push deleted templates: {ops}"
    assert "store_template" not in ops, f"re-push wrote templates: {ops}"
    assert "read_roster_stamp" in ops, "receipt must begin with a uuid compare"
    assert svc.publisher.ack("dev-1") == "loaded", "device still acks the no-op"


def test_missed_push_is_reconciled_and_repushed_by_subscription():
    # [CXC-FPSVC-RECONCILE-BY-SUBSCRIPTION] — a device that missed its push
    # announces push_needed; the service learns that by SUBSCRIPTION (never a
    # query), re-pushes, and the device converges on its next heartbeat.
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)
    _seed_cast(svc.store, "m", n_persons=3, templates_each=2)
    svc.follow_device("dev-1")

    # push while NO device is listening — the device misses it entirely
    first = svc.start_game("m", ["dev-1"])
    assert first.published is True

    # now the (empty) device appears and announces it needs a set
    dev = DeviceHarness(bus, "dev-1", gated_capability="open_hatch",
                         template_resolver=tstore.get)
    state = dev.device.emit_heartbeat()
    assert state.value == "fingerprint_push_needed"
    assert dev.pushes_received == 0, "the first push was genuinely missed"
    assert "dev-1" in svc.reconcile_pending()

    # the subscription-driven repair loop re-pushes it
    repushed = svc.reconcile_and_repush()
    assert repushed == ["dev-1"]
    assert dev.pushes_received == 1, "the re-push reached the device"
    assert svc.publisher.ack("dev-1") == "loaded"

    # the device confirms convergence on its next heartbeat
    dev.emit_ready()
    assert svc.publisher.is_converged("dev-1") is True
    assert svc.reconcile_pending() == []


def test_push_size_limit_refuses_and_publishes_nothing():
    # [CXC-FPBUS-REQ-SET-SIZE-LIMIT] via the reference validate_publishable —
    # the cap is the TARGET DEVICE's announced slot_count, never a constant.
    # A device announcing slot_count=20 makes this the same 22-over-20 case
    # the reference used to hardcode.
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)
    _seed_cast(svc.store, "m", n_persons=11, templates_each=2)  # 22 > 20

    small_caps = dataclasses.replace(
        MockFingerprintHAL().capabilities(), slot_count=20
    )
    dev = DeviceHarness(
        bus, "dev-big", gated_capability="open_hatch",
        hal=MockFingerprintHAL(small_caps),
        template_resolver=tstore.get,
    )
    svc.follow_device("dev-big")
    dev.announce()  # the service learns slot_count=20 from THIS device

    result = svc.start_game("m", ["dev-big"])
    assert result.published is False
    assert "22" in result.reason and "20" in result.reason
    assert dev.pushes_received == 0, "no template_set message reached the bus"


def test_push_size_limit_unknown_when_device_never_announced():
    # No announce -> no known cap -> nothing to check against, so the push is
    # NOT refused (there is no constant to fall back to). This is the exact
    # case [CXC-FPBUS-MSG-DEVICE-ANNOUNCE] exists to CLOSE once wired: until a
    # device announces, the service has no capacity to enforce.
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)
    _seed_cast(svc.store, "m", n_persons=11, templates_each=2)  # 22 entries
    svc.follow_device("dev-unknown")

    result = svc.start_game("m", ["dev-unknown"])
    assert result.published is True, "no known cap means nothing to enforce yet"


def test_push_template_size_guard_refuses_and_publishes_nothing():
    # [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] — a template set with a mis-sized
    # template is refused before publish; the device is never asked to write
    # it. A wrong-sized write may BRICK a real R503.
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)
    svc.store.define_role("crew", ["open_hatch"])
    svc.store.create_person("p1", "Person 1")
    svc.store.define_character("c1", "crew")
    svc.store.cast("m", "p1", "c1")
    # one byte short of the announced template_size_bytes
    svc.store.upsert_template("p1", 1, b"x" * (_TEMPLATE_SIZE - 1))

    dev = DeviceHarness(bus, "dev-1", gated_capability="open_hatch",
                         template_resolver=tstore.get)
    svc.follow_device("dev-1")
    dev.announce()

    result = svc.start_game("m", ["dev-1"])
    assert result.published is False
    assert str(_TEMPLATE_SIZE - 1) in result.reason
    assert str(_TEMPLATE_SIZE) in result.reason
    assert dev.pushes_received == 0, "a mis-sized template never reached the bus"


def test_push_without_uuid_refuses():
    # [CXC-FPBUS-REQ-SET-UUID-PRESENT] — the same reference precondition the
    # service uses. build_push always mints a uuid, so a uuid-absent set can only
    # arise from a malformed push; assert the reference gate the service imports
    # refuses it and yields nothing publishable.
    ok, reason = validate_publishable(
        {"template_set_uuid": "", "entries": [{"person": "p1"}]}
    )
    assert ok is False and "UUID" in reason


def test_enroll_orchestration_keys_by_logical_identity():
    # [CXC-FPSVC-ENROLL-ORCHESTRATION], [CXC-FPSVC-SCEN-ENROLL-KEYS-BY-LOGICAL]
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    # tstore.put is the DEVICE's upload seam for the enroll readback
    # ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]); tstore.get is the
    # SERVICE's resolve seam for the SAME reference. template_store_put here
    # is the PUSH direction's seam (unrelated to this test, but the service
    # always needs one); template_resolver is the readback direction's.
    svc = FingerprintService(
        template_store_put=tstore.put, template_resolver=tstore.get
    )
    svc.attach(bus)
    svc.store.create_person("pz", "Zoe")

    dev = DeviceHarness(bus, "dev-station", gated_capability="open_hatch",
                         template_resolver=tstore.get,
                         template_store_put=tstore.put)
    svc.follow_device("dev-station")
    dev.announce()

    svc.enroller.enroll("dev-station", "pz", 1)
    svc.enroller.enroll("dev-station", "pz", 2)

    # the superset gained two templates keyed by (person, ordinal) — and the
    # SUPERSET holds raw bytes, never a reference ([DEFECT-ENROLL-PY-ENROLL-
    # COMPLETE-READBACK-A4128F]: the reference is a wire-only concern).
    assert svc.store.ordinals_for("pz") == [1, 2]
    assert ("pz", 1) in svc.store.templates and ("pz", 2) in svc.store.templates
    for rec in svc.store.templates_for("pz"):
        assert isinstance(rec.template, (bytes, bytearray)), (
            f"superset must hold raw bytes, not a reference: {rec.template!r}"
        )
    # the enroll commands carried NO slot
    for cmd in dev.enroll_commands:
        assert "slot" not in cmd, f"enroll command named a slot: {cmd}"
        assert set(cmd.keys()) == {"person", "ordinal", "request_id"}
    # and no slot in the uploaded records
    for rec in svc.store.templates_for("pz"):
        assert not hasattr(rec, "slot")


def test_total_delete_retention_fires_on_six_month_inactivity():
    # [DD-FPSVC-DELETE-IS-TOTAL], [DD-FPSVC-INACTIVITY-CLOCK]
    clock = _Clock(now=1_000_000.0)
    svc = FingerprintService(clock=clock)
    bus = ComposeIOBus()
    svc.attach(bus)
    _seed_cast(svc.store, "m", n_persons=2, templates_each=2)

    # p2 authenticates just before the sweep; p1 never plays again
    six_months = 6 * 30 * 24 * 60 * 60
    clock.now = 1_000_000.0 + six_months + 1
    svc.store.record_auth_attempt("p2", at=clock.now - 10)  # recent activity

    deleted = svc.sweep_retention(now=clock.now)

    assert deleted == ["p1"], f"only the inactive person is deleted: {deleted}"
    # TOTAL: templates AND person record gone, castings gone, NO tombstone
    assert svc.store.get_person("p1") is None
    assert svc.store.templates_for("p1") == []
    assert all(c.person != "p1" for c in svc.store.castings)
    # the active person survives entirely
    assert svc.store.get_person("p2") is not None
    assert len(svc.store.templates_for("p2")) == 2


def test_auth_event_advances_the_retention_clock():
    # the inactivity clock is last-auth-attempt-at-any-device, learned from the
    # auth_event subscription ([CXC-FPBUS-MSG-AUTH-EVENT])
    clock = _Clock(now=2_000_000.0)
    svc = FingerprintService(clock=clock)
    bus = ComposeIOBus()
    svc.attach(bus)
    svc.store.create_person("pq", "Quinn")
    svc.follow_device("dev-q")

    before = svc.store.get_person("pq").last_auth_attempt_at
    # a granted event for pq at a later time
    bus.publish_event(
        topics.auth_event("dev-q"),
        "auth_event",
        {"device_id": "dev-q", "outcome": "granted", "player": "pq",
         "character": "cq", "timestamp": 2_500_000.0},
        source="dev-q",
    )
    assert svc.store.get_person("pq").last_auth_attempt_at == 2_500_000.0 > before
    # an unrecognized event carries no player and touches nobody's clock
    bus.publish_event(
        topics.auth_event("dev-q"),
        "auth_event",
        {"device_id": "dev-q", "outcome": "unrecognized", "timestamp": 9_000_000.0},
        source="dev-q",
    )
    assert svc.store.get_person("pq").last_auth_attempt_at == 2_500_000.0


def test_store_never_holds_a_physical_slot_index():
    # grep-assert the service's OWN records: no slot anywhere in the record
    # STRUCTURE, and every template key is a logical (person:str, ordinal:int).
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)
    _seed_cast(svc.store, "m", n_persons=3, templates_each=2)
    dev = DeviceHarness(bus, "dev-1", gated_capability="open_hatch",
                         template_resolver=tstore.get)
    svc.follow_device("dev-1")
    svc.start_game("m", ["dev-1"])

    # 1. no record dataclass carries a slot field
    for rec_type in (
        type(next(iter(svc.store.templates.values()))),
        type(next(iter(svc.store.persons.values()))),
        type(svc.store.castings[0]),
    ):
        fields = {f.name for f in dataclasses.fields(rec_type)}
        assert not any("slot" in f for f in fields), f"{rec_type.__name__}: {fields}"

    # 2. every template key is (person:str, ordinal:int) — a LOGICAL identity
    for key in svc.store.templates:
        assert isinstance(key, tuple) and len(key) == 2
        assert isinstance(key[0], str) and isinstance(key[1], int)

    # 3. grep the serialized record structure (template bytes excluded — opaque)
    blob = []
    for rec in svc.store.templates.values():
        blob.append(f"{rec.person}|{rec.ordinal}|{rec.enrolled_at}")
    for p in svc.store.persons.values():
        blob.append(f"{p.person_id}|{p.display_name}")
    for c in svc.store.castings:
        blob.append(f"{c.module}|{c.person}|{c.character}")
    text = "\n".join(blob).lower()
    assert "slot" not in text, "a service record leaked the word slot"


# ---------------------------------------------------------------------------
# [DEFECT] a device_id containing '/' — the firmware's own "dev/<name>"
# convention (e.g. "dev/s3-fp1") — never converges service bookkeeping, even
# though the heartbeat genuinely arrives. `_on_event` used to recover
# `device_id` with a LEFT partition on the first '/', which is not the true
# inverse of how `topics.py` / the device twin BUILD a namespace (they treat
# device_id as opaque and happily embed '/'). Invisible in every test above
# because they all use flat ids ("dev-1", "dev-big", ...).
# ---------------------------------------------------------------------------
def test_device_id_containing_slash_reconciles_in_process():
    """Transport-agnostic reproduction: the parsing bug lives in
    `FingerprintService._on_event`, not in any transport, so it
    reproduces over `LocalTransport` too once the device id itself embeds a
    '/' the way the live firmware's does."""
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)
    _seed_cast(svc.store, "m", n_persons=3, templates_each=2)

    device_id = "dev/s3-fp1"  # the firmware's actual device_id convention
    dev = DeviceHarness(bus, device_id, gated_capability="open_hatch",
                         template_resolver=tstore.get)
    svc.follow_device(device_id)
    dev.announce()

    result = svc.start_game("m", [device_id])
    assert result.published is True
    assert svc.publisher.ack(device_id) == "loaded"

    # reconcile-by-subscription: learn the held uuid from the heartbeat ONLY
    dev.emit_ready()
    assert svc.publisher.held_uuid(device_id) == result.template_set_uuid, (
        "on_fingerprint_ready was never reached — the event router dropped "
        "the heartbeat because it mis-split a device_id containing '/'"
    )
    assert svc.publisher.is_converged(device_id) is True
    assert svc.reconcile_pending() == []


class _FakeMqttClient:
    """Records publishes/subscribes the way paho would — mirrors the double
    in test_transport_mqtt_pending.py. No network, no broker."""

    def __init__(self):
        self.published: list[tuple[str, str, int]] = []
        self.subscribed: list[str] = []

    def publish(self, topic, payload, qos=0, retain=False, properties=None):
        self.published.append((topic, payload, qos))

    def subscribe(self, topic, qos=1):
        self.subscribed.append(topic)


class _FakeMqttMsg:
    """Stands in for paho's incoming message object: `.topic` + `.payload`
    are all `MQTTTransport._on_message` reads off it (plus optional
    `.retain`/`.properties`, defaulted via getattr for a LIVE, non-retained
    message)."""

    def __init__(self, topic: str, payload: bytes):
        self.topic = topic
        self.payload = payload


def test_device_id_containing_slash_reconciles_over_mqtt():
    """The MQTT-path proof: drives a REAL `MQTTTransport` (fake client, no
    broker) through its REAL inbound path — `_on_message` -> `BusMessage.
    from_json` -> the bus's `_dispatch_incoming` -> the service's `_on_event`
    — so this is the transport this defect was actually reported against,
    exercised end to end without a live broker.

    Reproduces the reported symptom exactly: an independent listener (this
    test, reading the raw topic/payload it hands to `_on_message`) confirms
    the device published `composeio/global/dev/s3-fp1/fingerprint_ready`
    with the correct `template_set_uuid`, while the service's own
    reconciliation state (`held_uuid`/`is_converged`) must still pick it up.

    Origination (`start_game` -> `push_to_devices`) is DELIBERATELY not
    exercised here: a real push carries raw template bytes, and publishing
    those over a bus with a global (JSON) transport hits the separate,
    already-flagged-but-unfixed defect that a JSON substrate cannot carry
    `bytes` (DP-CIO-18, noted in the commit that first brought this device
    onto live MQTT). This test is scoped to the RECONCILIATION defect only,
    so the precondition (a device the service has already pushed) is set up
    directly on the publisher, exactly as it would read after a real push.
    """
    device_id = "dev/s3-fp1"
    desired_uuid = "a1b2c3d4e5f6"

    transport = MQTTTransport(broker="test.local", client_id="fp-mqtt-test")
    fake_client = _FakeMqttClient()
    transport._client = fake_client
    transport._on_connect(fake_client, None, {}, 0)  # mark connected, flush

    bus = ComposeIOBus(global_transport=transport)
    # what `bus.start()` does for the global leg, without a real connect:
    bus._wire_transport_incoming(transport)

    svc = FingerprintService()
    svc.attach(bus)
    svc.follow_device(device_id)
    # the state a real push leaves behind (see docstring: origination itself
    # is out of scope for this reconciliation-focused test).
    svc.publisher._device_desired_uuid[device_id] = desired_uuid
    svc.publisher._device_module[device_id] = "m"
    assert svc.publisher.is_converged(device_id) is False, "not yet reconciled"

    # The wire topic an INDEPENDENT MQTT subscriber would see for this
    # device's heartbeat — built the same way the firmware does:
    # composeio/{scope}/{device_id}/{message}.
    topic = f"composeio/global/{device_id}/fingerprint_ready"
    wire_msg = BusMessage(
        namespace=f"{device_id}/fingerprint_ready",
        type=MessageType.EVENT,
        event_type="fingerprint_ready",
        data={"template_set_uuid": desired_uuid, "count_authentication_attempts": 0},
        source=device_id,
        scope="global",
    )
    payload = json.dumps(wire_msg.to_json()).encode()
    assert topic == "composeio/global/dev/s3-fp1/fingerprint_ready"

    # Hand it to the transport EXACTLY as paho would on message arrival.
    transport._on_message(fake_client, None, _FakeMqttMsg(topic, payload))

    assert svc.publisher.held_uuid(device_id) == desired_uuid, (
        "the heartbeat arrived on the wire (proven above) but the service's "
        "own bookkeeping never updated"
    )
    assert svc.publisher.is_converged(device_id) is True
    assert svc.reconcile_pending() == []


ALL_TESTS = [
    test_push_ack_reconcile_loop_is_green,
    test_push_carries_no_slot,
    test_re_push_same_cast_is_device_confirmed_noop,
    test_missed_push_is_reconciled_and_repushed_by_subscription,
    test_push_size_limit_refuses_and_publishes_nothing,
    test_push_size_limit_unknown_when_device_never_announced,
    test_push_template_size_guard_refuses_and_publishes_nothing,
    test_push_without_uuid_refuses,
    test_enroll_orchestration_keys_by_logical_identity,
    test_total_delete_retention_fires_on_six_month_inactivity,
    test_auth_event_advances_the_retention_clock,
    test_store_never_holds_a_physical_slot_index,
    test_device_id_containing_slash_reconciles_in_process,
    test_device_id_containing_slash_reconciles_over_mqtt,
]


def main() -> int:
    passed = 0
    for t in ALL_TESTS:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  {t.__name__}: {exc!r}")
            import traceback

            traceback.print_exc()
            return 1
        print(f"  ok    {t.__name__}")
        passed += 1
    print(
        f"\nfingerprint_service integration: {passed}/{len(ALL_TESTS)} passed  "
        "[machine-green — service<->reference-device composes against C71/C73/C74]"
    )
    print("Live-bus deploy (MQTT to the verified broker host): GLASS-PENDING "
          "— Haven-Deployer's job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def test_changed_roster_swaps_the_device_end_to_end():
    """A genuinely CHANGED roster (one player removed, a new one added) mints a
    fresh template_set_uuid, and pushing it drives the device's uuid-MISMATCH
    swap end to end THROUGH the service: delete-all, reload the new set, stamp
    strictly last, and zero the auth counter ([CXC-FPBUS-REQ-UUID-MISMATCH-
    BRANCH]).

    This stitches the seam the two existing halves prove only separately: the
    device-level parity golden (selftest.py, uuid-A -> uuid-B) and the
    service-level SAME-cast no-op (test_re_push_same_cast_is_device_confirmed_
    noop). Nothing before this pushed a genuinely DIFFERENT roster from the
    service and watched the device swap."""
    bus = ComposeIOBus()
    tstore = _FakeTemplateStore()
    svc = FingerprintService(template_store_put=tstore.put)
    svc.attach(bus)

    # roster A: p0 + p1, both crew, two templates each
    svc.store.define_role("crew", ["open_hatch"])
    for pid in ("p0", "p1"):
        svc.store.create_person(pid, pid.upper())
        svc.store.define_character(f"ch-{pid}", "crew")
        svc.store.cast("m", pid, f"ch-{pid}")
        svc.store.upsert_template(pid, 1, _synth_template(f"{pid}-1".encode()))
        svc.store.upsert_template(pid, 2, _synth_template(f"{pid}-2".encode()))

    dev = DeviceHarness(bus, "dev-1", gated_capability="open_hatch",
                        template_resolver=tstore.get)
    svc.follow_device("dev-1")
    dev.announce()

    first = svc.start_game("m", ["dev-1"])
    assert first.published and first.entry_count == 4
    uuid_a = first.template_set_uuid
    assert dev.hal.occupancy_count == 4
    assert dev.hal.read_roster_stamp() == uuid_a.encode("utf-8")
    assert {e.person for e in dev.device.bridge.values()} == {"p0", "p1"}

    # a granted match bumps the counter, so the swap's zeroing is observable
    crew_slot = next(s for s, e in dev.device.bridge.items() if e.role == "crew")
    dev.device.resolve_match(MatchResult(MatchOutcome.MATCHED, crew_slot, Score(200)))
    assert dev.device.count_authentication_attempts >= 1

    # --- change the roster: p0 leaves, p2 joins => a genuinely different set ---
    svc.store.delete_person("p0")
    svc.store.create_person("p2", "P2")
    svc.store.define_character("ch-p2", "crew")
    svc.store.cast("m", "p2", "ch-p2")
    svc.store.upsert_template("p2", 1, _synth_template(b"p2-1"))
    svc.store.upsert_template("p2", 2, _synth_template(b"p2-2"))

    dev.hal.reset_log()
    second = svc.start_game("m", ["dev-1"])
    assert second.published and second.entry_count == 4
    uuid_b = second.template_set_uuid

    # 1. a changed roster mints a fresh uuid (the desired-set signature changed)
    assert uuid_b != uuid_a, "a changed roster must mint a new uuid"

    # 2. the device SWAPPED: delete-all before any reload, stamp strictly last
    ops = dev.hal.op_sequence()
    assert "empty" in ops, "the device did not delete-all on the roster change"
    last_store = max(i for i, o in enumerate(ops) if o == "store_template")
    assert ops.index("empty") < last_store, "delete-all must precede the reload"
    assert ops.index("write_roster_stamp") > last_store, "stamp must be written last"

    # 3. the device now holds ONLY the new roster — p0's binding is gone
    assert dev.hal.occupancy_count == 4
    assert dev.hal.read_roster_stamp() == uuid_b.encode("utf-8")
    persons_now = {e.person for e in dev.device.bridge.values()}
    assert persons_now == {"p1", "p2"}, f"stale roster on device: {persons_now}"

    # 4. the genuine uuid change zeroed the auth counter, and the device acked
    assert dev.device.count_authentication_attempts == 0
    assert svc.publisher.ack("dev-1") == "loaded"
