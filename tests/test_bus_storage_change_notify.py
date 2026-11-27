"""Asset-change notification PRODUCER — [SPEC:CXC-ASSET-CHANGE-NOTIFY] slice P-a.

Acceptance bar (all machine-checkable; this is a headless bus producer, no glass):

  A1  PUT new bytes  -> exactly ONE retained change event carrying old+new hash, with
      NO asset id/name anywhere in the payload.
  A2  PUT the SAME bytes again -> NO event.
  A3  create a brand-new asset -> one event with a null/empty `old`.
  A4  the event payload is <= 512 B.
  A5  broker unreachable -> the asset PUT still succeeds (201) and the publish failure
      is logged LOUDLY, not swallowed.
  A6  a subscriber that connects AFTER the change still receives the latest event
      (MQTT retain works).

A1-A5 are hermetic: A1-A4 drive the producer contract through a recording transport (no
broker), A5 drives the real HTTP server against a deliberately-unreachable broker. A6
needs a real broker and SKIPS cleanly when none is reachable — set BUS_STORAGE_TEST_BROKER
(default mqtt-broker.local:1883) to run it.

`tests/conftest.py` puts `python/` on sys.path, so `composeio` imports directly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SERVER = _REPO / "services" / "bus_storage" / "server.py"
_CHANGE_NOTIFY = _REPO / "services" / "bus_storage" / "change_notify.py"


def _load_change_notify():
    """Load the sibling `change_notify.py` by path (never via sys.path — the conftest
    warns that inserting a dir shadows same-named modules for the code under test)."""
    spec = importlib.util.spec_from_file_location("bus_storage_change_notify", _CHANGE_NOTIFY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cn = _load_change_notify()


# --------------------------------------------------------------------------- #
# recording transport — the hermetic wire, no broker
# --------------------------------------------------------------------------- #
class _RecordingTransport:
    """A `Transport`-shaped double that records every `send(BusMessage)`.

    `is_connected` True so the producer's loud-degrade path stays quiet on the happy
    path; the A5 test exercises the disconnected case through the REAL transport."""

    def __init__(self):
        self.sent = []
        self._connected = True

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, message):
        self.sent.append(message)

    def subscribe_all(self, callback):
        # bus.start() wires an incoming-dispatch callback here; this producer
        # never receives, so it is a no-op sink.
        pass

    @property
    def is_connected(self):
        return self._connected


NS = "bus_storage/asset/changed"


@pytest.fixture
def producer():
    """A producer wired to a real ComposeIOBus whose global transport is the recorder."""
    from composeio.bus import ComposeIOBus
    from composeio.namespace import Scope

    transport = _RecordingTransport()
    bus = ComposeIOBus(bus_id="test-bus-storage", global_transport=transport)
    bus.start(announce_time=False)
    bus.register_namespace(NS, engine_config="json", scope=Scope.GLOBAL)
    notifier = cn.AssetChangeNotifier(bus=bus, namespace=NS)
    yield notifier, transport
    bus.stop()


def _change_events(transport):
    """Only the messages on the change namespace (filters bus housekeeping)."""
    return [m for m in transport.sent if m.namespace == NS]


class TestProducerContract:
    def test_A1_changed_bytes_emit_one_event_old_and_new_no_asset_id(self, producer):
        notifier, transport = producer
        old, new = b"scene-v1-bytes", b"scene-v2-different-bytes"

        event = notifier.notify(old, new)

        # exactly ONE event on the change namespace
        msgs = _change_events(transport)
        assert len(msgs) == 1, f"expected exactly one change event, got {len(msgs)}"
        msg = msgs[0]

        # carries old AND new content hash, and NOTHING else
        assert set(msg.value.keys()) == {"old", "new"}
        assert msg.value["old"] == cn.content_hash(old)
        assert msg.value["new"] == cn.content_hash(new)
        assert event == msg.value

        # NO asset id/name anywhere in the payload. The producer never receives the
        # asset path, so structurally it cannot leak — assert the whole wire dict.
        wire = json.dumps(msg.to_json())
        assert "scene" not in wire.replace('"ns"', "")  # our test bytes' word, not the ns key
        assert msg.value.get("old") and msg.value.get("new")
        # the only namespace on the wire is the change topic itself (no per-asset topic)
        assert msg.namespace == NS

    def test_A2_identical_bytes_emit_no_event(self, producer):
        notifier, transport = producer
        same = b"unchanged-content"

        event = notifier.notify(same, same)

        assert event is None
        assert _change_events(transport) == []

    def test_A3_create_has_null_old(self, producer):
        notifier, transport = producer
        new = b"brand-new-asset"

        event = notifier.notify(None, new)  # no prior bytes == create

        msgs = _change_events(transport)
        assert len(msgs) == 1
        assert msgs[0].value["old"] is None
        assert msgs[0].value["new"] == cn.content_hash(new)
        assert event["old"] is None

    def test_A4_payload_within_512_bytes(self, producer):
        notifier, transport = producer
        # worst case for size: both hashes present (a change, not a create).
        notifier.notify(b"a" * 10240, b"b" * 10240)
        msg = _change_events(transport)[0]
        size = len(json.dumps(msg.to_json()).encode())
        assert size <= cn.PAYLOAD_CAP_BYTES, f"payload {size} B exceeds {cn.PAYLOAD_CAP_BYTES} B cap"

    def test_content_hash_is_sha256_hex(self):
        h = cn.content_hash(b"")
        # sha256 of empty string, lowercase hex
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_A6b_retain_is_structurally_guaranteed_no_broker_needed(self, producer):
        """[CXC-ACN-RETENTION] does not need a live broker to verify: retain is a pure
        function of the message TYPE (`RETAIN_BY_TYPE` in `transport_mqtt.py`), decided
        once at import time, not at connect time. A6 is the only assertion of the
        ratified retain requirement and SKIPS broker-less, so in broker-less CI the
        property goes unverified. This proves it BY CONSTRUCTION instead: the producer
        publishes via `publish_state`, which stamps `MessageType.STATE`, and
        `RETAIN_BY_TYPE[MessageType.STATE]` is `True` — so retain follows for every
        transport (including a real MQTT one) without connecting to anything."""
        from composeio.transport import MessageType
        from composeio.transport_mqtt import RETAIN_BY_TYPE

        notifier, transport = producer
        notifier.notify(b"scene-v1-bytes", b"scene-v2-different-bytes")

        msgs = _change_events(transport)
        assert len(msgs) == 1
        # the producer used the STATE message type ...
        assert msgs[0].type == MessageType.STATE
        # ... and STATE is retained by construction, per the platform's own table.
        assert RETAIN_BY_TYPE[MessageType.STATE] is True


# --------------------------------------------------------------------------- #
# HTTP integration helpers
# --------------------------------------------------------------------------- #
def _put_asset(base_url, path, data):
    req = urllib.request.Request(f"{base_url}/api/v1/assets/{path}", data=data, method="PUT")
    return urllib.request.urlopen(req, timeout=5)


def _wait_health(base_url, tries=80, delay=0.2):
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"{base_url}/health", timeout=1)
            return True
        except Exception:
            time.sleep(delay)
    return False


def _start_server(tmp_path, port, extra_env):
    env = os.environ.copy()
    env["COMPOSEIO_DATA_DIR"] = str(tmp_path)
    env["COMPOSEIO_PORT"] = str(port)
    env["COMPOSEIO_HOST"] = "127.0.0.1"
    # the producer needs `composeio` importable when a broker is configured
    env["PYTHONPATH"] = str(_REPO / "python") + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, str(_SERVER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class TestBrokerUnreachable:
    def test_A5_put_succeeds_and_failure_is_loud(self, tmp_path):
        port = 18424
        base_url = f"http://127.0.0.1:{port}"
        # a fast-refusing, definitely-unreachable broker target
        proc = _start_server(
            tmp_path,
            port,
            {"COMPOSEUX_MQTT_BROKER": "127.0.0.1", "COMPOSEUX_MQTT_PORT": "47654"},
        )
        try:
            assert _wait_health(base_url), "server did not become healthy (broker-connect wait?)"
            # PUT new bytes must still succeed while the broker is down
            r1 = _put_asset(base_url, "flip/config.yaml", b"first-bytes")
            assert r1.status == 201
            r2 = _put_asset(base_url, "flip/config.yaml", b"second-different-bytes")
            assert r2.status == 201
        finally:
            proc.terminate()
            out = proc.stdout.read().decode(errors="replace")
            proc.wait(timeout=5)
        # the publish failure must be LOUD, not swallowed
        loud = ("could NOT connect" in out) or ("DISCONNECTED bus" in out) or ("LOCAL-ONLY" in out)
        assert loud, f"expected a loud degrade log, server output was:\n{out}"


# --------------------------------------------------------------------------- #
# A6 — retain, against a real broker (skips when none reachable)
# --------------------------------------------------------------------------- #
def _broker_host_port():
    url = os.environ.get("BUS_STORAGE_TEST_BROKER", "mqtt-broker.local:1883")
    host, _, port = url.partition(":")
    return host, int(port or "1883")


def _broker_reachable(host, port, timeout=2.0):
    try:
        import paho.mqtt.client as mqtt
    except Exception:
        return False
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


class TestRetainAgainstLiveBroker:
    def test_A6_late_subscriber_receives_retained_event(self, tmp_path):
        host, port = _broker_host_port()
        if not _broker_reachable(host, port):
            pytest.skip(f"no MQTT broker reachable at {host}:{port} (set BUS_STORAGE_TEST_BROKER)")

        import paho.mqtt.client as mqtt

        # ephemeral namespace so the production topic's retained value is untouched
        ns = f"bus_storage/asset/changed_test_{uuid.uuid4().hex[:12]}"
        topic = f"composeio/global/{ns}"
        srv_port = 18425
        base_url = f"http://127.0.0.1:{srv_port}"

        proc = _start_server(
            tmp_path,
            srv_port,
            {
                "COMPOSEUX_MQTT_BROKER": host,
                "COMPOSEUX_MQTT_PORT": str(port),
                "BUS_STORAGE_CHANGE_NAMESPACE": ns,
            },
        )
        received = []
        sub = None
        try:
            assert _wait_health(base_url), "server did not become healthy"
            # A create then a change -> the retained value is the latest {old,new}.
            assert _put_asset(base_url, "flip/scene.yaml", b"scene-A").status == 201
            time.sleep(0.5)
            assert _put_asset(base_url, "flip/scene.yaml", b"scene-B").status == 201
            time.sleep(1.0)  # let the retained publish settle on the broker

            # a FRESH subscriber that joins AFTER both writes must still see the latest.
            from composeio.paho_compat import new_paho_client  # paho 1.x and 2.x
            sub = new_paho_client(mqtt=mqtt, client_id=f"acn-test-sub-{uuid.uuid4().hex[:8]}", protocol=mqtt.MQTTv5)
            sub.on_message = lambda c, u, m: received.append((m.topic, m.payload, m.retain))
            sub.connect(host, port, keepalive=30)
            sub.loop_start()
            sub.subscribe(topic, qos=1)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not received:
                time.sleep(0.05)
        finally:
            if sub is not None:
                # clear the retained value so the ephemeral topic leaves no trace
                try:
                    sub.publish(topic, payload=b"", qos=1, retain=True)
                    time.sleep(0.3)
                except Exception:
                    pass
                sub.loop_stop()
                sub.disconnect()
            proc.terminate()
            out = proc.stdout.read().decode(errors="replace")
            proc.wait(timeout=5)

        assert received, f"late subscriber received nothing (retain failed). server:\n{out}"
        rtopic, payload, retain_flag = received[0]
        assert rtopic == topic
        assert retain_flag, "message was not delivered as RETAINED to a late subscriber"
        body = json.loads(payload)
        val = body["val"]
        # latest event: old = hash(scene-A), new = hash(scene-B)
        assert val["old"] == cn.content_hash(b"scene-A")
        assert val["new"] == cn.content_hash(b"scene-B")
        # NO asset id/name anywhere on the wire
        assert "scene.yaml" not in payload.decode(errors="replace")
        assert "flip" not in payload.decode(errors="replace")
        # payload within the 512 B device cap
        assert len(payload) <= cn.PAYLOAD_CAP_BYTES
