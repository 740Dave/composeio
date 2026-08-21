"""[BPR-ONE-CHANNEL] — multiplexed substreams are independently backpressured:
a large bulk body cannot starve critical-message / ack forwarding on the shared
channel. Unit-tests the MuxConn priority drain directly.
"""

import json
import struct
import threading
import time

from composeio.peer.frame import MuxConn


class BlockingSock:
    """A fake socket whose FIRST sendall blocks until released, so we can line
    up a bulk frame in-flight and prove the next drained frame is the critical
    one, not the queued bulk."""

    def __init__(self):
        self.sent = []
        self.gate = threading.Event()
        self._first = True
        self._lock = threading.Lock()

    def sendall(self, data):
        with self._lock:
            first = self._first
            self._first = False
        if first:
            self.gate.wait(2.0)
        self.sent.append(data)

    def recv(self, n):
        time.sleep(3600)  # reader idles; daemon thread, torn down with the test
        return b""

    def close(self):
        pass


def _ch(frame_bytes):
    (ln,) = struct.unpack(">I", frame_bytes[:4])
    return json.loads(frame_bytes[4:4 + ln].decode())["ch"]


def test_critical_frame_is_not_starved_by_bulk():
    sock = BlockingSock()
    conn = MuxConn(sock, on_frame=lambda c, f: None, name="bp")
    conn.start()

    conn.send({"ch": "state", "n": 1})   # bulk #1 -> picked, blocks in sendall
    time.sleep(0.05)                     # ensure #1 is in-flight
    conn.send({"ch": "state", "n": 2})   # bulk #2 queued (LOW)
    conn.send({"ch": "state", "n": 3})   # bulk #3 queued (LOW)
    conn.send({"ch": "crit", "n": 99})   # critical queued (HIGH)

    sock.gate.set()                      # release the writer

    end = time.monotonic() + 2.0
    while len(sock.sent) < 4 and time.monotonic() < end:
        time.sleep(0.005)
    assert len(sock.sent) == 4

    order = [_ch(x) for x in sock.sent]
    # #1 went first (already in-flight); the CRITICAL then jumps ahead of the
    # remaining bulk frames.
    assert order[0] == "state"
    assert order[1] == "crit"
    assert order.index("crit") < order.index("state", 1)
    conn.close()
