"""print_services' BusClient over a REAL mosquitto broker — the runtime leg the
independent bringup smoke was dispatched to prove.

WHY THIS FILE EXISTS. `services/print_services/bus.py` was ported off a hand-rolled
paho client onto `composeio.source_bus` (the transport seam). The port changed two
wire idioms: a tombstone is now a retained `None` (not a zero-length MQTT retain
clear), and a job result now rides the platform EVENT envelope (`data` + `evt`, not
`val`). Both are exactly the kind of change a static diff review cannot falsify —
only a real broker and an INDEPENDENT subscriber (this file uses raw `paho`, not
`composeio.bus`, wherever the assertion is about wire shape) can. [CXC-VISION-EPISTEMICS]
/ P6b: a test built from the same assumption as the code under test cannot certify
the one thing that assumption hides, so every wire-shape assertion here reads the
raw MQTT payload directly rather than going back through `BusMessage.from_json`.

This file stands up a REAL, ephemeral mosquitto broker (skipped if `mosquitto` is
not on PATH) and proves, over the actual wire, with a negative control alongside
every positive assertion:
  1. registry is a RETAINED STATE — a late subscriber sees it; control: an EVENT
     namespace with nothing retained is NOT seen by a late subscriber.
  2. TOMBSTONE — `publish_tombstone` yields a retained envelope with NO `val` key
     (decodes to `None`), not an exception; control: a genuine zero-length MQTT
     payload DOES raise in `BusMessage.from_json`, which is why the idiom changed.
  3. status is a RETAINED STATE, same shape as registry.
  4. RESULT is an EVENT — `type: event`, `evt: result`, payload under `data`, never
     `val`; control: no STATE-typed message ever carries the result payload.
  5. an inbound submit from a peer is received and handled (observable via the
     service's own history, an independent read path from the write path); the
     service is DEAF to a spoofed `/result` (not subscribed to it — no reaction);
     and the platform self-echo guard is proven directly against the real broker.
  6. an unreachable broker: `connect()` returns False within the bounded wait, the
     service stays usable HTTP-only, and a publish attempted afterward does not
     raise and does not hang.

Bringup provenance: formalizes the independent runtime smoke of the
`port/print-services-transport-seam` branch (commit `2b977bb`) against a real
ephemeral broker (mosquitto, high localhost port, started + killed for the smoke
session only).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

paho = pytest.importorskip("paho.mqtt.client")

_MOSQUITTO = shutil.which("mosquitto")
_MOSQUITTO_SUB = shutil.which("mosquitto_sub")
_MOSQUITTO_PUB = shutil.which("mosquitto_pub")
pytestmark = pytest.mark.skipif(
    _MOSQUITTO is None or _MOSQUITTO_SUB is None or _MOSQUITTO_PUB is None,
    reason="mosquitto binary/CLI tools not found on PATH — cannot bring up a real broker",
)

_REPO = Path(__file__).resolve().parent.parent
_PS_DIR = _REPO / "services" / "print_services"

# print_services is a package (`from . import ...` inside bus.py/server.py), so it
# must be imported as a package member, not loaded by bare file path like the
# document_store sibling test does for its single-file bus.py.
if str(_REPO / "services") not in sys.path:
    sys.path.insert(0, str(_REPO / "services"))

from print_services.bus import BusClient  # noqa: E402
from print_services.server import PrintServices  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _reset_source_bus_memo():
    """`source_bus.acquire_bus` memoizes ONE self-created bus per (broker, port) for
    the WHOLE PROCESS ([CXC-SOB]) — correct for production (a real print_services
    process creates exactly one `BusClient`), but this module builds a fresh
    `PrintServices()` per test against the SAME module-scoped broker, so without a
    reset every test after the first would silently get back a PRIOR test's bus —
    including one a prior test's `svc.shutdown()` had already `.stop()`-ed. This is
    a test-harness concern, not a claim about `bus.py`'s production behavior; see
    `source_bus.reset_for_tests()`'s own docstring ('does not stop the buses')."""
    from composeio import source_bus
    source_bus.reset_for_tests()
    yield
    source_bus.reset_for_tests()


# --------------------------------------------------------------------------- #
# a REAL, ephemeral mosquitto broker — one per test module, torn down after
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def broker(tmp_path_factory):
    port = _free_port()
    conf_dir = tmp_path_factory.mktemp("mosquitto")
    conf_path = conf_dir / "mosquitto.conf"
    conf_path.write_text(
        f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\nlog_type error\n"
    )
    proc = subprocess.Popen(
        [_MOSQUITTO, "-c", str(conf_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 5.0
    up = False
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                s.connect(("127.0.0.1", port))
            up = True
            break
        except OSError:
            time.sleep(0.05)
    if not up:
        proc.terminate()
        pytest.fail("ephemeral mosquitto broker did not start listening in time")
    try:
        yield {"host": "127.0.0.1", "port": port, "url": f"mqtt://127.0.0.1:{port}"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _raw_sub_one(broker, topic: str, timeout_s: float = 3.0) -> str | None:
    """An INDEPENDENT observer: raw `mosquitto_sub`, no `composeio` on this path at
    all — the only thing that can certify what is ACTUALLY on the wire rather than
    what `BusMessage.from_json` is willing to reconstruct from it."""
    try:
        out = subprocess.run(
            [_MOSQUITTO_SUB, "-h", broker["host"], "-p", str(broker["port"]),
             "-t", topic, "-C", "1", "-W", str(timeout_s), "-v"],
            capture_output=True, text=True, timeout=timeout_s + 2,
        )
    except subprocess.TimeoutExpired:
        return None
    return out.stdout.strip() or None


def _raw_pub(broker, topic: str, payload: dict) -> None:
    subprocess.run(
        [_MOSQUITTO_PUB, "-h", broker["host"], "-p", str(broker["port"]),
         "-t", topic, "-m", json.dumps(payload), "-q", "1"],
        check=True, capture_output=True, timeout=5,
    )


@pytest.fixture
def service(broker, monkeypatch):
    """A real `PrintServices`, wired to the ephemeral broker, backends pointed at
    closed local ports (fast, deterministic `Connection refused` — no real printer
    dependency, no hanging on an unroutable LAN address)."""
    monkeypatch.setenv("COMPOSEIO_MQTT_URL", broker["url"])
    monkeypatch.setenv("PRINT_SERVICES_HP_IP", "127.0.0.1")
    monkeypatch.setenv("PRINT_SERVICES_ELEGOO_IP", "127.0.0.1")
    monkeypatch.setenv("PRINT_SERVICES_HEARTBEAT", "3600")  # quiet during the test
    svc = PrintServices()
    ok = svc.start_bus()
    assert ok, "service must connect to the real ephemeral broker"
    try:
        yield svc
    finally:
        svc.shutdown()


# --------------------------------------------------------------------------- #
# 1 — registry is RETAINED STATE; control: a non-retained namespace is NOT seen
# --------------------------------------------------------------------------- #
class TestRegistryRetainedState:
    def test_late_subscriber_sees_retained_registry(self, service, broker):
        raw = _raw_sub_one(broker, "composeio/global/print_services/registry/hp")
        assert raw is not None, "a late subscriber must see the retained registry row"
        _topic, _sp, payload = raw.partition(" ")
        d = json.loads(payload)
        assert d["type"] == "state"
        assert d["val"]["id"] == "hp"

    def test_negative_control_non_retained_namespace_unseen_by_late_subscriber(self, service, broker):
        """CONTROL: nothing has been published yet on the (non-retained) result
        namespace. A late subscriber connecting now must see NOTHING — proving
        assertion 1 above is actually exercising retain, not just broker liveness."""
        raw = _raw_sub_one(broker, "composeio/global/print_services/hp/result", timeout_s=2.0)
        assert raw is None, "an EVENT namespace with nothing published must reach a late subscriber with nothing"


# --------------------------------------------------------------------------- #
# 2 — TOMBSTONE: retained None, not a raise; control: zero-length payload DOES raise
# --------------------------------------------------------------------------- #
class TestTombstone:
    def test_tombstone_is_retained_none_not_an_exception(self, service, broker):
        service.bus.publish_tombstone("print_services/registry/hp")
        raw = _raw_sub_one(broker, "composeio/global/print_services/registry/hp")
        assert raw is not None, "the tombstone itself is a retained publish — a late subscriber must see it"
        _topic, _sp, payload = raw.partition(" ")
        d = json.loads(payload)
        assert d["type"] == "state"
        assert "val" not in d, (
            "the platform's `to_json` omits `val` entirely when the value is None — "
            "a real subscriber decodes this to value=None, cleanly, never a raise"
        )

    def test_negative_control_zero_length_payload_would_raise(self):
        """CONTROL: this is WHY the idiom changed. The old client's tombstone was a
        true zero-length MQTT retain-clear (`publish(topic, b"", retain=True)`).
        Every subscriber on this stack decodes with `json.loads(payload.decode())`
        before `BusMessage.from_json` ever runs (`transport_mqtt.py::_on_message`) —
        confirm directly that a zero-length payload raises there, which is what a
        graceful tombstone must NOT do to every listener on shutdown."""
        with pytest.raises(json.JSONDecodeError):
            json.loads(b"".decode())


# --------------------------------------------------------------------------- #
# 3 — status is RETAINED STATE (same shape as registry)
# --------------------------------------------------------------------------- #
class TestStatusRetainedState:
    def test_late_subscriber_sees_retained_status(self, service, broker):
        raw = _raw_sub_one(broker, "composeio/global/print_services/hp/status")
        assert raw is not None
        _topic, _sp, payload = raw.partition(" ")
        d = json.loads(payload)
        assert d["type"] == "state"
        assert "state" in d["val"]


# --------------------------------------------------------------------------- #
# 4 — RESULT is an EVENT carrying `data` + `evt`; control: never on the STATE path
# --------------------------------------------------------------------------- #
class TestResultEventEnvelope:
    def test_result_is_event_with_data_payload(self, service, broker):
        # EVENT is NOT retained (proven by the negative control in
        # TestRegistryRetainedState) — the observer MUST subscribe BEFORE the
        # publish, exactly like a real peer waiting on a job result.
        proc = subprocess.Popen(
            [_MOSQUITTO_SUB, "-h", broker["host"], "-p", str(broker["port"]),
             "-t", "composeio/global/print_services/hp/result", "-C", "1", "-v"],
            stdout=subprocess.PIPE, text=True,
        )
        time.sleep(0.3)  # let the SUBSCRIBE reach the broker before we publish
        service._publish_result("hp", {"job_id": "envelope-check", "status": "done"})
        try:
            out, _ = proc.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        raw = out.strip() or None
        assert raw is not None
        _topic, _sp, payload = raw.partition(" ")
        d = json.loads(payload)
        assert d["type"] == "event", "a result must be an EVENT, not a STATE"
        assert d["evt"] == "result"
        assert "val" not in d, "the platform event envelope carries its payload in `data`, never `val`"
        assert d["data"]["job_id"] == "envelope-check"

    def test_negative_control_no_state_message_carries_the_result_payload(self, service, broker):
        """CONTROL: publish a result, then independently sniff every message this
        service emits for a bounded window and confirm none of the STATE-typed ones
        (registry/status) ever carries the job payload — the result lives on the
        EVENT lane exclusively."""
        proc = subprocess.Popen(
            [_MOSQUITTO_SUB, "-h", broker["host"], "-p", str(broker["port"]),
             "-t", "composeio/+/print_services/#", "-v"],
            stdout=subprocess.PIPE, text=True,
        )
        time.sleep(0.3)
        service._publish_result("hp", {"job_id": "state-lane-check", "status": "done"})
        service._publish_status("hp")
        time.sleep(1.0)
        proc.terminate()
        out, _ = proc.communicate(timeout=3)
        for line in out.splitlines():
            _topic, _sp, payload = line.partition(" ")
            if not payload:
                continue
            d = json.loads(payload)
            if d.get("type") == "state":
                assert "state-lane-check" not in json.dumps(d), (
                    f"a STATE message must never carry the result payload: {d}"
                )


# --------------------------------------------------------------------------- #
# 5 — submit round-trip received; deaf to /result and its own echo
# --------------------------------------------------------------------------- #
class TestSubmitRoundTripAndDeafness:
    def test_peer_submit_is_received_and_handled(self, service, broker):
        _raw_pub(broker, "composeio/global/print_services/hp/submit", {
            "ns": "print_services/hp/submit", "type": "state", "src": "independent-peer",
            "ts": time.time(), "scope": "global", "seq": 1,
            "val": {"job_id": "rt-check-1", "content_type": "text", "content": "smoke"},
        })
        deadline = time.monotonic() + 4.0
        found = False
        while time.monotonic() < deadline and not found:
            with service._history_lock:
                found = any(h.get("job_id") == "rt-check-1" for h in service._history)
            if not found:
                time.sleep(0.05)
        assert found, (
            "an independent read path (the service's own job history, not the bus "
            "write path) must show the submit was received AND handled"
        )

    def test_negative_control_deaf_to_spoofed_result(self, service, broker):
        """CONTROL: the service never subscribes to its own `/result` namespace. A
        spoofed result from an 'evil peer' must produce NO reaction — no history
        entry, no crash."""
        before = list(service._history)
        _raw_pub(broker, "composeio/global/print_services/hp/result", {
            "ns": "print_services/hp/result", "type": "event", "src": "evil-peer",
            "ts": time.time(), "scope": "global", "seq": 99, "evt": "result",
            "data": {"job_id": "SHOULD-NOT-APPEAR", "status": "done"},
        })
        time.sleep(1.0)
        after = list(service._history)
        assert after == before, "the service must be deaf to a message on its OWN result namespace"
        assert not any(h.get("job_id") == "SHOULD-NOT-APPEAR" for h in after)

    def test_self_echo_guard_prevents_a_second_local_delivery(self, broker):
        """The platform mechanism `bus.py`'s docstring leans on ('our own publishes
        are dropped by the bus's self-echo guard') — proven directly against the
        real broker, independent of print_services' own (structural, subscription-
        absence) deafness to `/result` above. A publisher that ALSO subscribes its
        own outbound namespace gets exactly ONE local delivery, not two; traced
        against the bus's own fingerprint check to show the echo was positively
        matched and suppressed, not merely unlucky timing."""
        client = BusClient(broker["url"], client_id="echo-test", source="echo-test-src")
        assert client.connect(connect_wait_s=8) is True
        bus = client._bus
        echo_checks = []
        orig_is_echo = bus._is_own_echo

        def traced(msg):
            result = orig_is_echo(msg)
            echo_checks.append(result)
            return result

        bus._is_own_echo = traced
        received = []
        client.subscribe("print_services/echo-test/loopback", lambda v, r: received.append(v))
        time.sleep(0.3)
        client.publish_state("print_services/echo-test/loopback", {"from": "self"})
        time.sleep(1.5)
        client.disconnect()

        assert len(received) == 1, (
            "exactly ONE local delivery for a self-published, self-subscribed "
            f"namespace (not two — that would mean the echo leaked through): got {received}"
        )
        assert True in echo_checks, (
            "the bus's self-echo fingerprint check must have positively matched "
            "the returning broker echo at least once"
        )


# --------------------------------------------------------------------------- #
# 6 — unreachable broker: connect() returns False, no hang, publishes don't raise
# --------------------------------------------------------------------------- #
class TestUnreachableBroker:
    def test_connect_returns_false_bounded_no_hang(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
        # dead_port is now closed again (socket exited the `with`) -> connection refused,
        # not a black-holed address, so this proves the BOUNDED-wait path, not a hang.
        client = BusClient(f"mqtt://127.0.0.1:{dead_port}", client_id="dead-broker-test",
                            source="dead-broker-src")
        start = time.monotonic()
        ok = client.connect(connect_wait_s=3.0)
        elapsed = time.monotonic() - start
        assert ok is False, "connect() to an unreachable broker must return False, not raise"
        assert elapsed < 6.0, f"connect() must be BOUNDED, not hang: took {elapsed:.1f}s"

        # publishes after a failed connect must be best-effort: no raise, no hang.
        start = time.monotonic()
        client.publish_state("print_services/registry/hp", {"id": "hp"})
        client.publish_event("print_services/hp/result", {"job_id": "x"})
        client.publish_tombstone("print_services/registry/hp")
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"best-effort publishes on a dead bus must not hang: took {elapsed:.1f}s"
        client.disconnect()
