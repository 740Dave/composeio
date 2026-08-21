#!/usr/bin/env python3
"""smoke_device_announce.py — the POSITIVE CONTROL for `composeio fleet`.

[SPEC:CXC-BOARD-HW] — workstream 0.2/0.5 of
``docs/PLAN-fleet-baseline-and-bus-native.md``.

    python3 python/tests/render/smoke_device_announce.py

WHY THIS EXISTS, and it is not "coverage"
-----------------------------------------
Run against the live broker on 2026-08-13, ``composeio fleet`` printed
"0 device(s) announced".  That is the CORRECT answer — nothing on the fleet
had the firmware yet — and it is also EXACTLY what the tool would print if it
subscribed to the wrong topic, decoded the wrong field, or never connected.
Ask the question the discipline demands: *what would this instrument show if it
were broken?*  The same.  Without a positive control it is not an instrument.

So this stands up a real MQTT broker, a device that announces, and — crucially
— an INDEPENDENT SUBSCRIBER that shares NO CODE with the fleet tool, and
checks all three against each other.

WHAT IT PROVES
--------------
  1. A device's five announcement namespaces cross a real MQTT broker with the
     topic and payload shapes the firmware produces.
  2. An independent subscriber sees them — the observation does not come from
     the publishing process's own counters.  (A counter that cannot increment
     reports zero and looks healthy; that defect shipped in this codebase
     twice.)
  3. ``_device/broadcast/announce`` triggers an IMMEDIATE republication — the
     poll works, and the answer is measured at the subscriber, not claimed by
     the publisher.
  4. ``composeio fleet``, run as a SUBPROCESS, finds the device and resolves
     its build fingerprint back to the real build string via
     ``cyd/build_fingerprints.json``.

WHAT IT DOES NOT PROVE, stated because a PASS here gets believed
----------------------------------------------------------------
The DEVICE is a Python stand-in.  This says nothing about whether ESP32
firmware actually publishes, about its real 5 s / 60 s cadence, or about the
UART and CAN substrates.  Those need a board, and the acceptance test for them
is the same shape as this one: an independent subscriber on the live bus.

The encoder/decoder AGREEMENT between the two tiers is proven separately and
properly by ``pio run -e native_announce`` (247 checks against a corpus
generated from the Python packer), so this file is deliberately allowed to use
the Python packer to build its payloads — it is testing the transport, the
poll and the report, not the arithmetic.

NO RAW PAHO ABOVE THE WAIST
---------------------------
The device and the fleet tool both go through ``MQTTTransport``.  The mini
broker and the independent subscriber speak MQTT 3.1.1 on RAW SOCKETS — that
is the point of the subscriber (it must share nothing with the code under
test), and the broker is not a bus participant at all.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "python"))

from composeio import device_announce as da  # noqa: E402
from composeio.transport import BusMessage, MessageType  # noqa: E402
from composeio.transport_mqtt import MQTTTransport  # noqa: E402

DEVICE_ID = "smoketest-aa11bb"

# The real firmware cadence is 5 s / 60 s (announce_pack.h).  The heartbeat is
# kept REAL so the interval the tool measures is the interval the firmware
# uses; the full-set period is scaled so the run finishes in ~20 s rather than
# ~2 minutes.  Both are stated in the output so nobody mistakes the scaled one
# for a measurement of the firmware.
HEARTBEAT_S = 5.0
FULL_S = 6.0


# --------------------------------------------------------------------------- #
# A minimal MQTT 3.1.1 broker.  Raw sockets; no dependency, no daemon, no
# writes to the LIVE production broker at mqtt-broker.local.
# --------------------------------------------------------------------------- #

def _remaining_length(sock_recv) -> int:
    mult, value = 1, 0
    while True:
        b = sock_recv(1)
        if not b:
            raise ConnectionError("eof in remaining length")
        value += (b[0] & 0x7F) * mult
        if not (b[0] & 0x80):
            return value
        mult *= 128
        if mult > 128 ** 3:
            raise ValueError("malformed remaining length")


def _encode_remaining(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            return bytes(out)


def _topic_matches(filt: str, topic: str) -> bool:
    f, t = filt.split("/"), topic.split("/")
    for i, seg in enumerate(f):
        if seg == "#":
            return True
        if i >= len(t):
            return False
        if seg != "+" and seg != t[i]:
            return False
    return len(f) == len(t)


class MiniBroker:
    def __init__(self) -> None:
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        self._clients: list[tuple[socket.socket, list[str]]] = []
        self._lock = threading.Lock()
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,),
                             daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        subs: list[str] = []
        with self._lock:
            self._clients.append((conn, subs))

        def recv(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = conn.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("eof")
                buf += chunk
            return buf

        try:
            while not self._stop:
                header = recv(1)[0]
                length = _remaining_length(recv)
                body = recv(length) if length else b""
                kind = header >> 4
                if kind == 1:               # CONNECT
                    conn.sendall(b"\x20\x02\x00\x00")
                elif kind == 8:             # SUBSCRIBE
                    mid = struct.unpack(">H", body[:2])[0]
                    i, qoses = 2, []
                    while i < len(body):
                        tl = struct.unpack(">H", body[i:i + 2])[0]
                        subs.append(body[i + 2:i + 2 + tl].decode())
                        qoses.append(body[i + 2 + tl])
                        i += 3 + tl
                    conn.sendall(b"\x90" + _encode_remaining(2 + len(qoses))
                                 + struct.pack(">H", mid) + bytes(qoses))
                elif kind == 3:             # PUBLISH
                    qos = (header >> 1) & 3
                    tl = struct.unpack(">H", body[:2])[0]
                    topic = body[2:2 + tl].decode()
                    off = 2 + tl
                    if qos:
                        mid = struct.unpack(">H", body[off:off + 2])[0]
                        off += 2
                        conn.sendall(b"\x40\x02" + struct.pack(">H", mid))
                    self._fanout(topic, body[off:])
                elif kind == 12:            # PINGREQ
                    conn.sendall(b"\xd0\x00")
                elif kind == 14:            # DISCONNECT
                    break
                elif kind == 10:            # UNSUBSCRIBE
                    mid = struct.unpack(">H", body[:2])[0]
                    conn.sendall(b"\xb0\x02" + struct.pack(">H", mid))
        except (ConnectionError, OSError, struct.error, IndexError):
            pass
        finally:
            with self._lock:
                self._clients = [c for c in self._clients if c[0] is not conn]
            try:
                conn.close()
            except OSError:
                pass

    def _fanout(self, topic: str, payload: bytes) -> None:
        # QoS 0 outbound: a smoke harness must not need a retransmit engine.
        frame = (b"\x30" + _encode_remaining(2 + len(topic) + len(payload))
                 + struct.pack(">H", len(topic)) + topic.encode() + payload)
        with self._lock:
            targets = [c for c, s in self._clients
                       if any(_topic_matches(f, topic) for f in s)]
        for c in targets:
            try:
                c.sendall(frame)
            except OSError:
                pass

    def stop(self) -> None:
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# THE INDEPENDENT SUBSCRIBER.  Raw MQTT on a socket — it shares no module, no
# constant and no decoder with `composeio fleet`.  If both agree, they are not
# agreeing because they are the same code.
# --------------------------------------------------------------------------- #

class IndependentSubscriber(threading.Thread):
    def __init__(self, port: int, topic_filter: str = "composeio/#") -> None:
        super().__init__(daemon=True)
        self.port = port
        self.filter = topic_filter
        self.seen: list[tuple[float, str, bytes]] = []
        self._stop = False
        self._ready = threading.Event()

    def run(self) -> None:
        s = socket.create_connection(("127.0.0.1", self.port))
        cid = b"independent-observer"
        payload = (struct.pack(">H", 4) + b"MQTT" + bytes([4, 2])
                   + struct.pack(">H", 60) + struct.pack(">H", len(cid)) + cid)
        s.sendall(b"\x10" + _encode_remaining(len(payload)) + payload)

        def recv(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                c = s.recv(n - len(buf))
                if not c:
                    raise ConnectionError("eof")
                buf += c
            return buf

        recv(4)  # CONNACK
        f = self.filter.encode()
        sub = struct.pack(">H", 1) + struct.pack(">H", len(f)) + f + b"\x00"
        s.sendall(b"\x82" + _encode_remaining(len(sub)) + sub)
        recv(5)  # SUBACK
        self._ready.set()

        s.settimeout(0.5)
        while not self._stop:
            try:
                h = recv(1)[0]
                n = _remaining_length(recv)
                body = recv(n) if n else b""
            except (socket.timeout, TimeoutError):
                continue
            except (ConnectionError, OSError):
                return
            if h >> 4 != 3:
                continue
            tl = struct.unpack(">H", body[:2])[0]
            self.seen.append((time.monotonic(), body[2:2 + tl].decode(),
                              body[2 + tl:]))

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._ready.wait(timeout)

    def stop(self) -> None:
        self._stop = True


# --------------------------------------------------------------------------- #
# The device stand-in
# --------------------------------------------------------------------------- #

class FakeDevice(threading.Thread):
    """Publishes exactly what announce.cpp publishes, in the same shapes.

    32/16-bit words as lowercase hex STRINGS, heartbeat and transport as
    NUMBERS — see the ⚠ block in cyd/src/announce_pack.h for why.
    """

    def __init__(self, port: int, build_fp: int) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.build_fp = build_fp
        self.transport = da.pack_transport(
            da.TRANSPORT_CODE_BY_NAME["mqtt"], from_nvs=False)
        self.scene = 0x6F62
        self.health = da.pack_health(50 * 1024, 1, 1, 0x01 | 0x04 | 0x08)
        self._stop = False
        self.full_publications: list[float] = []
        self._t0 = time.monotonic()
        self._tx: MQTTTransport | None = None

    def _ns(self, leaf: str) -> str:
        return da.announce_namespace(DEVICE_ID, leaf)

    def _send(self, leaf: str, value) -> None:
        assert self._tx is not None
        self._tx.send(BusMessage(namespace=self._ns(leaf), value=value,
                                 type=MessageType.STATE, source=DEVICE_ID,
                                 scope="global"))

    def _publish_full(self) -> None:
        self.full_publications.append(time.monotonic())
        self._send("build", "%08x" % self.build_fp)
        self._send("transport", self.transport)
        self._send("scene", "%04x" % self.scene)
        self._send("health", "%08x" % self.health)

    def run(self) -> None:
        tx = self._tx = MQTTTransport(broker="127.0.0.1", port=self.port,
                                      client_id=f"dev-{DEVICE_ID}")
        polled = threading.Event()

        def on_poll(msg: BusMessage) -> None:
            if msg.namespace in (da.POLL_BROADCAST,
                                 da.poll_namespace(DEVICE_ID)):
                polled.set()

        tx.subscribe(da.POLL_BROADCAST, on_poll)
        tx.subscribe(da.poll_namespace(DEVICE_ID), on_poll)
        tx.start()
        deadline = time.monotonic() + 5
        while not tx.is_connected and time.monotonic() < deadline:
            time.sleep(0.05)

        self._publish_full()
        self._send("heartbeat", int(time.monotonic() - self._t0))
        next_hb = time.monotonic() + HEARTBEAT_S
        next_full = time.monotonic() + FULL_S
        while not self._stop:
            time.sleep(0.05)
            now = time.monotonic()
            if polled.is_set():
                polled.clear()
                self._publish_full()
                self._send("heartbeat", int(now - self._t0))
                next_hb = now + HEARTBEAT_S
                next_full = now + FULL_S
            if now >= next_hb:
                next_hb = now + HEARTBEAT_S
                self._send("heartbeat", int(now - self._t0))
            if now >= next_full:
                next_full = now + FULL_S
                self._publish_full()
        tx.stop()

    def stop(self) -> None:
        self._stop = True


# --------------------------------------------------------------------------- #

FAILURES: list[str] = []


def check(cond: bool, what: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        FAILURES.append(what)


def main() -> int:
    fp_map = da.load_fingerprint_map()
    if not fp_map:
        print("cyd/build_fingerprints.json is empty — run "
              "`python3 cyd/tools/gen_build_stamp.py --env smoke` first")
        return 1
    build_fp = sorted(fp_map)[0]
    build_stamp = fp_map[build_fp]["stamp"]

    broker = MiniBroker()
    print(f"mini MQTT broker on 127.0.0.1:{broker.port} "
          f"(NOT the production broker)")

    observer = IndependentSubscriber(broker.port)
    observer.start()
    if not observer.wait_ready():
        print("independent subscriber never subscribed")
        return 1
    print("independent subscriber attached to composeio/# on raw sockets")

    device = FakeDevice(broker.port, build_fp)
    device.start()
    print(f"device {DEVICE_ID} announcing "
          f"(heartbeat {HEARTBEAT_S:.0f}s = the real firmware cadence; "
          f"full set {FULL_S:.0f}s, SCALED from the firmware's 60s so this "
          f"run finishes)")

    # Long enough for two heartbeats, so an INTERVAL is measurable rather than
    # a single arrival being mistaken for a cadence.
    time.sleep(13)

    print("\n[1] INDEPENDENT SUBSCRIBER — what actually crossed the broker")
    topics = {t for _, t, _ in observer.seen}
    for leaf in da.ANNOUNCE_FIELDS:
        want = f"composeio/global/_device/{DEVICE_ID}/{leaf}"
        check(want in topics, f"observed {want}")

    hb_times = [ts for ts, t, _ in observer.seen if t.endswith("/heartbeat")]
    check(len(hb_times) >= 3,
          f"observed {len(hb_times)} heartbeats (need >= 3 to measure a "
          f"cadence)")
    if len(hb_times) >= 3:
        gaps = [b - a for a, b in zip(hb_times, hb_times[1:])]
        mean = sum(gaps) / len(gaps)
        check(3.5 <= mean <= 6.5,
              f"heartbeat interval {mean:.2f}s is within 5s +/- 1.5s")

    # Payload SHAPE, read by the independent subscriber and not by the decoder
    # under test: the 32-bit word must be 8 hex digits in a JSON string, or the
    # value silently went through a float somewhere.
    build_payloads = [p for _, t, p in observer.seen if t.endswith("/build")]
    check(bool(build_payloads), "build namespace carried a payload")
    if build_payloads:
        val = json.loads(build_payloads[-1].decode())["val"]
        check(isinstance(val, str) and len(val) == 8,
              f"build value is an 8-char hex STRING, not a lossy number "
              f"(got {val!r})")
        check(val == "%08x" % build_fp,
              f"build value {val!r} is the fingerprint exactly")

    print("\n[2] POLL — does _device/broadcast/announce force a republish?")
    before = len(device.full_publications)
    baseline_health = len([1 for _, t, _ in observer.seen
                           if t.endswith("/health")])
    poller = MQTTTransport(broker="127.0.0.1", port=broker.port,
                           client_id="poller")
    poller.start()
    deadline = time.monotonic() + 5
    while not poller.is_connected and time.monotonic() < deadline:
        time.sleep(0.05)
    poller.send(BusMessage(namespace=da.POLL_BROADCAST, value=1,
                           type=MessageType.STATE, source="poller",
                           scope="global"))
    time.sleep(2.0)   # well under the 6s periodic, so a republish IS the poll
    poller.stop()
    after_health = len([1 for _, t, _ in observer.seen
                        if t.endswith("/health")])
    check(after_health > baseline_health,
          f"the poll produced a republication within 2s "
          f"(health messages {baseline_health} -> {after_health}; the periodic "
          f"interval is {FULL_S:.0f}s, so this cannot be the timer)")
    check(len(device.full_publications) > before,
          "the device itself recorded the poll-driven republication")

    print("\n[3] composeio fleet — as a SUBPROCESS, against the same broker")
    env = dict(os.environ, PYTHONPATH=str(REPO / "python"))
    proc = subprocess.run(
        [sys.executable, "-m", "composeio.cli", "fleet",
         "--broker", "127.0.0.1", "--port", str(broker.port), "--wait", "8"],
        capture_output=True, text=True, env=env, timeout=90)
    print(proc.stdout)
    if proc.stderr.strip():
        print("stderr:", proc.stderr.strip()[:2000])
    check(proc.returncode == 0,
          f"exit status 0 means 'a device answered' (got {proc.returncode})")
    check(DEVICE_ID in proc.stdout, f"fleet listed {DEVICE_ID}")
    check(build_stamp in proc.stdout,
          f"fleet RESOLVED the fingerprint to the build string "
          f"{build_stamp!r}")
    check("mqtt(dflt)" in proc.stdout,
          "fleet decoded the transport byte as mqtt-from-compiled-default")
    check("6f62" in proc.stdout, "fleet decoded the scene content hash")
    check("50K" in proc.stdout, "fleet decoded free heap from the health word")
    check("poweron" in proc.stdout,
          "fleet decoded the reset reason from the health word")

    print("\n[4] NEGATIVE CONTROL — the tool must report NOTHING when nothing "
          "announces")
    device.stop()
    time.sleep(1.0)
    proc2 = subprocess.run(
        [sys.executable, "-m", "composeio.cli", "fleet",
         "--broker", "127.0.0.1", "--port", str(broker.port), "--wait", "3"],
        capture_output=True, text=True, env=env, timeout=60)
    check(proc2.returncode == 2,
          f"exit status 2 means 'nobody answered' (got {proc2.returncode}) — "
          f"without this, a silent fleet and a broken tool are the same signal")
    check("NOTHING ANSWERED" in proc2.stdout,
          "the empty report says WHY it is empty")

    observer.stop()
    broker.stop()

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)} check(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    print("\nNOTE: the DEVICE here is a Python stand-in.  This proves the "
          "transport, the poll and the report.  It proves NOTHING about "
          "whether ESP32 firmware publishes, about its real cadence, or about "
          "UART/CAN.  Those need a board and the same independent-subscriber "
          "shape.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
