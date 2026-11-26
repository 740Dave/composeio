"""RUNTIME bringup smoke tests for [SPEC:CXC-STORE] — composeio-first-bringup.

These are DELIBERATELY separate from `tests/test_storage_consolidation.py` (the builder's
own acceptance suite). Per [CXC-VISION-EPISTEMICS] / dusk-gift P6b, a test written by the
same author from the same mental model as the code inherits that model's blind spot — the
builder's suite never starts a broker and never puts a byte on the MQTT wire, and it never
constructs `DocStore` with a non-default `base_url` (the exact path a latent wiring bug
was hiding behind). Both gaps are closed here, each with a stated negative control: what
this assertion would show if the component under test were actually broken.

Run: `python3 -m pytest tests/test_storage_consolidation_bringup.py -v`
     or directly: `python3 tests/test_storage_consolidation_bringup.py`
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "services" / "bus_storage" / "server.py"
sys.path.insert(0, str(REPO / "python"))

from composeio import resolve  # noqa: E402
from composeio.docstore import DocStore  # noqa: E402

MOSQUITTO = shutil.which("mosquitto")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Broker:
    """A scratch, loopback-only mosquitto instance. NEVER the fleet broker —
    isolated port, isolated tmp config, no persistence, torn down on exit."""

    def __init__(self):
        self.port = _free_port()
        self.dir = Path(tempfile.mkdtemp(prefix="cxc_store_bringup_mosq_"))
        self.conf = self.dir / "mosquitto.conf"
        self.conf.write_text(
            f"listener {self.port} 127.0.0.1\n"
            "allow_anonymous true\n"
            "persistence false\n"
            f"log_dest file {self.dir / 'mosquitto.log'}\n"
        )
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [MOSQUITTO, "-c", str(self.conf)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            with socket.socket() as s:
                s.settimeout(0.2)
                try:
                    s.connect(("127.0.0.1", self.port))
                    return self
                except OSError:
                    time.sleep(0.05)
        raise RuntimeError("scratch mosquitto did not come up")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


class _StoreOnBus:
    """bus_storage launched WITH a broker configured (COMPOSEUX_MQTT_BROKER),
    unlike test_storage_consolidation.py's `_Server`, which deliberately runs
    broker-less. This is the ONLY way to exercise [CXC-ASSET-CHANGE-NOTIFY]."""

    def __init__(self, data_dir: Path, broker_port: int):
        self.data_dir = data_dir
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.broker_port = broker_port
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        env = dict(
            os.environ,
            COMPOSEIO_HOST="127.0.0.1", COMPOSEIO_PORT=str(self.port),
            COMPOSEIO_DATA_DIR=str(self.data_dir),
            COMPOSEUX_MQTT_BROKER="127.0.0.1", COMPOSEUX_MQTT_PORT=str(self.broker_port),
            PYTHONPATH=str(REPO / "python"),
        )
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        import urllib.request
        deadline = time.time() + 10
        up = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base}/health", timeout=1) as r:
                    if r.read() == b"ok":
                        up = True
                        break
            except Exception:
                time.sleep(0.05)
        if not up:
            raise RuntimeError("bus_storage did not come up")
        resolve.BUS_STORAGE_URL = self.base
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


class _MqttCollector:
    """A LIVE subscriber, connected and subscribed BEFORE the caller triggers any
    publish — so it observes the actual live event stream, not just whatever a
    late joiner's retained tail happens to hold (that distinction IS the point of
    [CXC-ACN-RETENTION], and conflating the two was this file's own first draft
    bug: subscribing after both PUTs collected only the single retained message,
    which looked like a failure of the producer when it was a test-methodology
    error). Use as a context manager; call `.wait(n, timeout_s)` for delivery."""

    def __init__(self, port: int, topic: str):
        import paho.mqtt.client as mqtt

        self.msgs: list[dict] = []
        if hasattr(mqtt, "CallbackAPIVersion"):
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        else:
            self._client = mqtt.Client()
        self._client.on_message = lambda _c, _u, msg: self.msgs.append(
            json.loads(msg.payload.decode()))
        self._client.connect("127.0.0.1", port, keepalive=5)
        self._client.subscribe(topic)
        self._client.loop_start()
        # Give the CONNECT+SUBSCRIBE a moment to actually land at the broker
        # before the caller starts publishing, or an immediate publish can race
        # ahead of the subscription and be missed — a broker-timing artifact,
        # not a producer defect.
        time.sleep(0.3)

    def wait(self, n: int, timeout_s: float = 5.0) -> list[dict]:
        deadline = time.time() + timeout_s
        while time.time() < deadline and len(self.msgs) < n:
            time.sleep(0.05)
        return list(self.msgs)

    def close(self):
        self._client.loop_stop()
        self._client.disconnect()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _mqtt_collector(port: int, topic: str, n: int, timeout_s: float = 5.0):
    """Late-subscribe convenience form (for retained-message tests): connect,
    subscribe, and collect whatever arrives (including the retained tail) within
    `timeout_s`. NOT suitable for counting a live sequence of events — use
    `_MqttCollector` (subscribe-before-publish) for that."""
    with _MqttCollector(port, topic) as c:
        return c.wait(n, timeout_s)


# --------------------------------------------------------------------------- #
# 1. LIVE BUS PEER: asset-change-notify actually reaches the MQTT wire.
#
# The builder's own suite (test_storage_consolidation.py) never starts a broker
# and never asserts anything about the wire — it is entirely silent on
# [SPEC:CXC-ASSET-CHANGE-NOTIFY], which this consolidation's blob route touches
# by sharing `_handle_asset_put`'s call site. This is the P6b-shaped gap: "the
# widget renders correctly in an in-process harness... the transport is
# connected... where do you look?" — the WIRE.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(MOSQUITTO is None, reason="mosquitto not installed — cannot prove the wire")
def test_asset_change_notify_reaches_the_wire(tmp_path=None):
    data = _mk_tmp(tmp_path, "acn")
    with _Broker() as broker:
        with _StoreOnBus(data, broker.port) as srv:
            b = srv.base
            with _MqttCollector(broker.port,
                                "composeio/global/bus_storage/asset/changed") as coll:
                _put(b, "/api/v1/assets/displays/scene.compose.yaml", b"v1 bytes")
                _put(b, "/api/v1/assets/displays/scene.compose.yaml", b"v2 bytes DIFFERENT")
                # NEGATIVE CONTROL A: re-PUT of IDENTICAL bytes must NOT produce a
                # third event. If the notifier fired unconditionally on every PUT
                # (an instrument that can only report SUCCESS), this would be a
                # 3rd message and the collection below would reach 3, not 2.
                _put(b, "/api/v1/assets/displays/scene.compose.yaml", b"v2 bytes DIFFERENT")
                msgs = coll.wait(n=2, timeout_s=5.0)
                time.sleep(0.5)  # let a wrongly-fired 3rd event (if any) arrive too
                msgs = list(coll.msgs)
            assert len(msgs) == 2, (
                f"expected exactly 2 change events (create + change; identical re-PUT "
                f"suppressed), got {len(msgs)}: {msgs}"
            )
            assert msgs[0]["type"] == "state", msgs[0]  # STATE lane, per [CXC-ACN-RETENTION]
            assert msgs[0]["val"]["old"] is None, msgs[0]           # create: no prior hash
            assert msgs[0]["val"]["new"], msgs[0]
            assert msgs[1]["val"]["old"] == msgs[0]["val"]["new"], msgs  # old->new chains
            assert msgs[1]["val"]["new"] != msgs[1]["val"]["old"], msgs

    # NEGATIVE CONTROL B: subscribing to a topic the code does NOT publish stays
    # empty. Proves the collector itself discriminates — if it "found" messages
    # on every topic regardless of subject, the positive assertions above would
    # be meaningless (an instrument that can't fail is not an instrument).
    with _Broker() as broker:
        with _StoreOnBus(data, broker.port) as srv:
            _put(srv.base, "/api/v1/assets/displays/other.compose.yaml", b"x")
            msgs = _mqtt_collector(broker.port, "composeio/global/bus_storage/asset/NOPE",
                                   n=1, timeout_s=1.5)
            assert msgs == [], f"phantom messages on an unpublished topic: {msgs}"
    print("PASS asset-change-notify reaches the wire (+ suppressed-on-no-change negative control)")


@pytest.mark.skipif(MOSQUITTO is None, reason="mosquitto not installed")
def test_asset_change_notify_retained_for_late_subscriber(tmp_path=None):
    """[CXC-ACN-RETENTION]: a subscriber joining AFTER the PUT still gets the
    latest {old,new} without polling — MQTT retain, not a timing accident."""
    data = _mk_tmp(tmp_path, "acn_retain")
    with _Broker() as broker:
        with _StoreOnBus(data, broker.port) as srv:
            _put(srv.base, "/api/v1/assets/late.bin", b"first")
            _put(srv.base, "/api/v1/assets/late.bin", b"second")
            time.sleep(0.3)  # let both publishes land before the LATE subscribe
            msgs = _mqtt_collector(broker.port, "composeio/global/bus_storage/asset/changed",
                                   n=1, timeout_s=3.0)
            assert len(msgs) == 1, msgs
            assert msgs[0]["val"]["old"] is not None, msgs  # the 2nd (retained) event, not the 1st
    print("PASS retained event delivered to a late subscriber")


# --------------------------------------------------------------------------- #
# 2. DocStore(base_url=...) actually governs every method, not just status().
#
# Found BY ACCIDENT during bringup: `DocStore.put/get/list_docs/query/
# list_collections` all delegate to module-level `composeio.resolve` functions,
# which read `resolve.BUS_STORAGE_URL` — NOT `self.base_url`. Only `status()`
# used `self.base_url` directly. Masked in production because every live
# caller's default host (`mqtt-broker.local`) happens to already match
# resolve.py's own default — admin_app.py and store_browser.py both construct
# `DocStore(host=...)` and would silently talk to the WRONG store the moment
# that host ever diverges (a test harness, a per-tenant buildhost deployment).
# Fixed in python/composeio/docstore.py — DocStore.__init__ now repoints the
# shared module-level global to self.base_url.
# --------------------------------------------------------------------------- #
def test_docstore_base_url_governs_every_method(tmp_path=None):
    data = _mk_tmp(tmp_path, "docstore_wiring")
    with _StoreOnBusNoAcn(data) as srv:
        # NEGATIVE CONTROL: point the shared global at an address that cannot
        # possibly answer, THEN construct DocStore with the real scratch URL.
        # If DocStore.__init__ did not repoint the global (the pre-fix
        # behaviour), every call below would try to reach
        # `http://127.0.0.1:1` and raise ConnectionRefusedError/URLError —
        # this is what makes the test capable of failing, not just passing.
        resolve.BUS_STORAGE_URL = "http://127.0.0.1:1"
        ds = DocStore(base_url=srv.base)
        assert resolve.BUS_STORAGE_URL == srv.base, (
            "DocStore did not repoint composeio.resolve.BUS_STORAGE_URL — "
            "every CRUD/query call below would hit the unreachable sentinel"
        )
        assert ds.put("bringup", "doc1", {"n": 1}) is not None
        assert ds.get("bringup", "doc1") == {"n": 1}
        assert ds.list_docs("bringup") == ["doc1"]
        assert [r["doc_id"] for r in ds.query("bringup", where={"n": 1})] == ["doc1"]
    print("PASS DocStore(base_url=...) governs put/get/list/query, not just status()")


class _StoreOnBusNoAcn:
    """bus_storage launched broker-less (mirrors test_storage_consolidation.py's
    `_Server`) — this test is about DocStore wiring, not the bus."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        env = dict(os.environ, COMPOSEIO_HOST="127.0.0.1", COMPOSEIO_PORT=str(self.port),
                   COMPOSEIO_DATA_DIR=str(self.data_dir))
        self.proc = subprocess.Popen([sys.executable, str(SERVER)], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import urllib.request
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{self.base}/health", timeout=1) as r:
                    if r.read() == b"ok":
                        break
            except Exception:
                time.sleep(0.05)
        else:
            raise RuntimeError("bus_storage did not come up")
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


# --------------------------------------------------------------------------- #
# 3. Blob-route containment negative control, exercised on DELETE too — the
# builder's own traversal test (test_traversal_refused_on_blob) only covers
# GET and PUT. DELETE is new surface this consolidation added
# (_handle_blob_delete) and containment must not have been forgotten for it,
# per [CXC-BUSSTORE-CONTAINMENT]'s own history ("six sibling routes did not
# forget the guard; they were never able to reach it").
# --------------------------------------------------------------------------- #
def test_blob_delete_traversal_refused(tmp_path=None):
    import urllib.error
    import urllib.request
    data = _mk_tmp(tmp_path, "del_trav")
    with _StoreOnBusNoAcn(data) as srv:
        b = srv.base
        # POSITIVE CONTROL first: a real nested blob DELETE must still work —
        # a guard that also breaks legitimate deletes has traded a
        # vulnerability for an outage, per [CXC-BUSSTORE-CONTAINMENT].
        _put(b, "/api/v1/blob/coll/real.json", b"{}")
        req = urllib.request.Request(f"{b}/api/v1/blob/coll/real.json", method="DELETE")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        # NEGATIVE CONTROL: absolute-path escape on DELETE (curl-normalization-
        # proof form — built with urllib.request.Request directly, no shell/curl
        # client-side dot-segment collapsing in the way).
        req = urllib.request.Request(f"{b}/api/v1/blob//etc/passwd", method="DELETE")
        try:
            code = urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 403, f"absolute-path escape on blob DELETE not refused: {code}"
    print("PASS blob DELETE containment (positive nested-delete + negative absolute-path)")


def _put(base: str, path: str, body: bytes) -> int:
    import urllib.request
    req = urllib.request.Request(f"{base}{path}", data=body, method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def _mk_tmp(tmp_path, name: str) -> Path:
    if tmp_path is not None:
        d = Path(tmp_path) / name
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(tempfile.mkdtemp(prefix=f"cxcstore_bringup_{name}_"))


if __name__ == "__main__":
    test_asset_change_notify_reaches_the_wire()
    test_asset_change_notify_retained_for_late_subscriber()
    test_docstore_base_url_governs_every_method()
    test_blob_delete_traversal_refused()
    print("\nALL bringup smoke checks PASS")
