"""Closes [DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18] (SERVICE->
DEVICE) and its mirror, [DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]
(DEVICE->SERVICE).

The reproduction: a REAL BINARY (non-ASCII) fingerprint template used to blow
up ``RosterPublisher.push_to_devices()`` the instant it crossed a JSON
transport, because ``BusMessage.to_json()`` refuses raw ``bytes``
(DP-CIO-18) — the transport this repo actually deploys over
(``composeio.transport_mqtt.MQTTTransport``). The fix carries a
``fingerprint://<opaque-id>`` REFERENCE on the wire instead, resolved by the
device from a DEDICATED template store — never ``bus_storage``
([DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE]). The same defect existed in
reverse on the enroll READBACK path — ``enroll.py``'s
``EnrollOrchestrator.on_enroll_complete`` reading raw bytes off
``data["template"]`` — and is closed the same way: the DEVICE uploads the
captured readback and the SERVICE resolves the reference back to bytes.

This test spins up the REAL ``fingerprint_template_store`` HTTP server on an
ephemeral loopback port with its own tmp data directory, points
``composeio.resolve`` at it, and DELIBERATELY breaks ``composeio.resolve``'s
bus_storage endpoint (an unroutable loopback port) so any accidental traffic
to bus_storage fails loudly instead of silently succeeding against the wrong
store. It then drives a REAL ``MQTTTransport`` (fake paho client, no broker —
same harness as ``python/tests/test_transport_mqtt_pending.py``) so the exact
substrate that raised ``UnserialisableValue`` is exercised end to end, in
BOTH directions:
  * push:     origination -> JSON-serialise -> wire -> device resolve ->
              fetch -> size-guard -> ``loaded``.
  * enroll:   device capture -> upload -> wire -> JSON-serialise ->
              service resolve -> fetch -> superset upsert.

Run standalone::

    PYTHONPATH=python:services python3 -m fingerprint_service.tests.test_template_reference

or under pytest::

    PYTHONPATH=python:services python3 -m pytest services/fingerprint_service/tests/test_template_reference.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

import composeio.resolve as resolve
from composeio.bus import ComposeIOBus
from composeio.peripherals.fingerprint.device import FingerprintDevice
from composeio.peripherals.fingerprint.mock import MockFingerprintHAL
from composeio.transport import BusMessage, MessageType
from composeio.transport_mqtt import MQTTTransport

from fingerprint_service import topics
from fingerprint_service.service import FingerprintService

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/ on path
import fingerprint_template_store.server as tstore_server  # noqa: E402


_TEMPLATE_SIZE = MockFingerprintHAL().capabilities().template_size_bytes


class _FakeMqttClient:
    """Records publishes the way paho would receive them (mirrors
    ``python/tests/test_transport_mqtt_pending.py::FakeClient``). No network,
    no broker — this IS the substrate that raised ``UnserialisableValue``,
    exercised without needing a live MQTT broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, int]] = []

    def publish(self, topic, payload, qos=0, retain=False, properties=None):
        self.published.append((topic, payload, qos))

    def subscribe(self, topic, qos=1):
        pass


class _FakeMqttMsg:
    """Stands in for paho's incoming message object: `.topic` + `.payload`
    are all `MQTTTransport._on_message` reads off it (mirrors
    ``test_integration.py``'s twin)."""

    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class _RunningTemplateStore:
    """A REAL ``fingerprint_template_store`` HTTP server on an ephemeral
    loopback port, with its own tmp data directory — never bus_storage's."""

    def __init__(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fp-template-store-")
        self.data_dir = Path(self._tmpdir.name)
        tstore_server.DATA_DIR = self.data_dir
        tstore_server.ensure_dirs()
        self._httpd = tstore_server._ThreadingServer(
            ("127.0.0.1", 0), tstore_server.TemplateStoreHandler
        )
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stored_files(self) -> list[Path]:
        return sorted(self.data_dir.glob("*.tmpl"))

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._tmpdir.cleanup()


def _non_ascii_template(size: int = _TEMPLATE_SIZE) -> bytes:
    """The exact shape of value that raised ``UnserialisableValue``: real
    binary bytes across the WHOLE byte range, including every non-ASCII value
    (0x80-0xFF) and the NUL byte — nothing a text codec could round-trip
    through by accident."""
    return bytes(i % 256 for i in range(size))


def _wire_mqtt() -> tuple[MQTTTransport, _FakeMqttClient]:
    transport = MQTTTransport(broker="test.local", client_id="fp-template-ref-test")
    fake = _FakeMqttClient()
    transport._client = fake
    transport._on_connect(fake, None, {}, 0)  # mark connected, flush
    return transport, fake


def test_binary_template_push_over_json_mqtt_transport_succeeds():
    store = _RunningTemplateStore()
    # Point resolve.py at the REAL dedicated store, and DELIBERATELY break its
    # bus_storage endpoint — proof-by-experiment that nothing here falls back
    # to bus_storage: an accidental use would fail loudly, not succeed quietly
    # against the wrong store.
    orig_template_url = resolve.TEMPLATE_STORE_URL
    orig_bus_storage_url = resolve.BUS_STORAGE_URL
    resolve.TEMPLATE_STORE_URL = store.url
    resolve.BUS_STORAGE_URL = "http://127.0.0.1:1"  # nothing listens here

    try:
        # ---- the reproduction fixture: ONE real binary, non-ASCII template
        device_id = "dev-r503-1"
        svc = FingerprintService()  # default template_store_put -> REAL resolve.put_bytes
        transport, fake_client = _wire_mqtt()
        bus = ComposeIOBus(global_transport=transport)
        bus._wire_transport_incoming(transport)
        svc.attach(bus)

        svc.store.define_role("crew", ["open_hatch"])
        svc.store.create_person("p1", "Person 1")
        svc.store.define_character("c1", "crew")
        svc.store.cast("m", "p1", "c1")
        template_bytes = _non_ascii_template()
        assert any(b >= 0x80 for b in template_bytes), "fixture must be genuinely non-ASCII"
        svc.store.upsert_template("p1", 1, template_bytes)

        svc.follow_device(device_id)
        # the device's announced capabilities gate the service-side size guard
        caps = MockFingerprintHAL().capabilities()
        bus.publish_event(
            topics.device_announce(device_id),
            "announce",
            {
                "device_id": device_id,
                "capabilities": {
                    "slot_count": caps.slot_count,
                    "template_size_bytes": caps.template_size_bytes,
                    "supports_readback": caps.supports_readback,
                },
            },
            source=device_id,
        )

        # ---- origination: THIS is the call that used to raise
        # UnserialisableValue the moment it reached transport_mqtt.send().
        result = svc.start_game("m", [device_id])
        assert result.published is True, f"push was refused: {result.reason}"

        # ---- proof #1: it really did cross the JSON/MQTT substrate.
        published = [p for p in fake_client.published if p[0].endswith("/template_set")]
        assert published, "no template_set message reached the (fake) MQTT client"
        topic, payload, qos = published[-1]
        assert topic == f"composeio/global/{device_id}/template_set"
        wire_msg = BusMessage.from_json(json.loads(payload))  # round-trips: proves it was valid JSON
        wire_entries = wire_msg.data["entries"]
        assert len(wire_entries) == 1
        assert isinstance(wire_entries[0]["template"], str)
        assert wire_entries[0]["template"].startswith("fingerprint://"), (
            f"push still carried inline bytes on the wire: {wire_entries[0]['template']!r}"
        )

        # ---- proof #2: the template bytes landed in the DEDICATED store,
        # under the OPAQUE ref — never bus_storage (whose endpoint we broke
        # above; reaching it here would have raised, not silently worked).
        opaque_id = wire_entries[0]["template"][len("fingerprint://"):]
        stored_files = store.stored_files()
        assert any(f.stem == opaque_id for f in stored_files), (
            f"opaque id {opaque_id!r} not found in the dedicated store's data "
            f"dir {store.data_dir}: {stored_files}"
        )
        assert (store.data_dir / f"{opaque_id}.tmpl").read_bytes() == template_bytes
        # the opaque id names nothing about person/ordinal/module
        # ([DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE]).
        assert "p1" not in opaque_id and "m" != opaque_id

        # ---- proof #3: the device resolves the reference, fetches the REAL
        # bytes over HTTP from the dedicated store, the size-guard passes, and
        # the template lands correctly — driven straight off the decoded WIRE
        # message, not an in-process shortcut.
        hal = MockFingerprintHAL()
        device = FingerprintDevice(
            hal=hal,
            bus=svc.publisher.bus,  # unused by receive_push; any sink satisfies the type
            device_id=device_id,
            gated_capability="open_hatch",
            # template_resolver left at its default: the REAL
            # composeio.resolve.resolve_bytes, hitting the dedicated store
            # above via resolve.TEMPLATE_STORE_URL.
        )
        status = device.receive_push(wire_msg.data)
        assert status == "loaded", f"device receipt did not load: {status}"
        (installed_slot,) = device.bridge.keys()
        assert hal.read_template(installed_slot) == template_bytes, (
            "wrong bytes reached the sensor"
        )
    finally:
        resolve.TEMPLATE_STORE_URL = orig_template_url
        resolve.BUS_STORAGE_URL = orig_bus_storage_url
        store.shutdown()


def test_binary_template_enroll_readback_over_json_mqtt_transport_succeeds():
    """Closes [DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F] — the
    DEVICE->SERVICE mirror of the push-direction defect above.

    The reproduction: ``enroll.py``'s ``EnrollOrchestrator.on_enroll_complete``
    used to read ``data["template"]`` as raw bytes off the wire, which a real
    device publishing over MQTT could never have sent in the first place —
    ``BusMessage.to_json()`` refuses raw ``bytes`` (DP-CIO-18). The fix mints a
    `fingerprint://<opaque-id>` reference on the DEVICE side (uploaded to the
    SAME dedicated store the push direction uses — never bus_storage) and
    resolves it back to bytes on the SERVICE side before the superset upsert.

    Drives the REAL capture ceremony (``MockFingerprintHAL``), a REAL upload
    to the REAL ``fingerprint_template_store`` HTTP server, a genuinely
    JSON-serialisable wire message, and the REAL ``MQTTTransport`` inbound
    path (fake paho client, no broker) straight into
    ``FingerprintService``'s live subscription — the exact substrate
    that would have raised ``UnserialisableValue`` on the old shape.
    """
    store = _RunningTemplateStore()
    orig_template_url = resolve.TEMPLATE_STORE_URL
    orig_bus_storage_url = resolve.BUS_STORAGE_URL
    resolve.TEMPLATE_STORE_URL = store.url
    resolve.BUS_STORAGE_URL = "http://127.0.0.1:1"  # nothing listens here

    try:
        device_id = "dev-r503-enroll"
        person, ordinal = "p1", 1

        svc = FingerprintService()  # default template_resolver -> REAL resolve.resolve_bytes
        transport, fake_client = _wire_mqtt()
        bus = ComposeIOBus(global_transport=transport)
        bus._wire_transport_incoming(transport)
        svc.attach(bus)

        svc.store.create_person(person, "Person 1")
        svc.follow_device(device_id)

        # ---- the enroll command mints the request_id the readback echoes
        # ([CXC-FPBUS-CORRELATION]).
        request_id = svc.enroller.enroll(device_id, person, ordinal)
        assert svc.enroller.pending_count() == 1

        # ---- the DEVICE side: the REAL HAL store/read path (mirrors
        # binder.py's _ReferenceDeviceHarness._on_enroll's use of
        # ``hal.read_template(session.slot)`` exactly), fed the exact
        # non-ASCII fixture shape that raised ``UnserialisableValue`` — the
        # mock ceremony's own synthesized bytes are ASCII-only (a seed string
        # repeated), so a REAL binary fixture is written directly rather than
        # relying on ``session.capture()``'s synthetic content. Then a REAL
        # HTTP upload to the dedicated store — never an in-memory shortcut.
        hal = MockFingerprintHAL()
        template_bytes = _non_ascii_template()
        assert any(b >= 0x80 for b in template_bytes), "fixture must be genuinely non-ASCII"
        slot = hal.store_template(template_bytes)
        assert hal.read_template(slot) == template_bytes
        opaque_id = f"enroll-{device_id}-{ordinal}"
        ref = resolve.put_bytes(f"fingerprint://{opaque_id}", template_bytes)
        assert ref.startswith("fingerprint://")

        # ---- proof #1: the wire message a real device would publish is
        # genuinely JSON-serialisable ONLY because the template is a
        # reference — the old raw-bytes shape would raise
        # UnserialisableValue right here.
        wire_msg = BusMessage(
            namespace=topics.enroll_complete(device_id),
            type=MessageType.EVENT,
            event_type="enroll_complete",
            data={
                "person": person,
                "ordinal": ordinal,
                "template_ref": ref,
                "request_id": request_id,
            },
            source=device_id,
            scope="global",
        )
        payload = json.dumps(wire_msg.to_json()).encode()  # must not raise
        topic = f"composeio/global/{topics.enroll_complete(device_id)}"

        # ---- hand it to the transport EXACTLY as paho would on message
        # arrival — the substrate this defect was reported against.
        transport._on_message(fake_client, None, _FakeMqttMsg(topic, payload))

        # ---- proof #2: the SERVICE resolved the reference — a REAL HTTP GET
        # against the dedicated store (bus_storage's endpoint is broken
        # above; reaching it would have raised, not silently worked) — and
        # landed the REAL bytes in the superset, keyed by (person, ordinal).
        # The pending correlation cleared, proving the request_id round-trip.
        assert svc.enroller.pending_count() == 0, "the readback must clear the pending entry"
        assert svc.store.ordinals_for(person) == [ordinal]
        rec = svc.store.templates[(person, ordinal)]
        assert isinstance(rec.template, (bytes, bytearray)), (
            f"superset must hold raw bytes, not a reference: {rec.template!r}"
        )
        assert rec.template == template_bytes, "wrong bytes landed in the superset"
    finally:
        resolve.TEMPLATE_STORE_URL = orig_template_url
        resolve.BUS_STORAGE_URL = orig_bus_storage_url
        store.shutdown()


ALL_TESTS = [
    test_binary_template_push_over_json_mqtt_transport_succeeds,
    test_binary_template_enroll_readback_over_json_mqtt_transport_succeeds,
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
    print(f"\ntemplate reference: {passed}/{len(ALL_TESTS)} passed "
          "[DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18 CLOSED, "
          "DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F CLOSED]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
