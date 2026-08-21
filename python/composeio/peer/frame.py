"""Peer/relay wire framing + the multiplexed, backpressure-isolated connection.

[BPR-ONE-CHANNEL]: read/write/mirror-sync/asset-doc-fetch/critical-ack all
ride ONE session, as framed substreams (the ``ch`` field). Substreams are
independently backpressured — a large ``fetch_res`` body cannot starve a
``crit`` frame — by draining a HIGH-priority queue (crit/ack/control) ahead
of a LOW-priority queue (bulk state/fetch) on the single writer thread.

Framing is deliberately trivial (4-byte big-endian length prefix + JSON body):
the wire format of ``BusMessage`` is owned by [SPEC:CIO-BUS-PROTOCOL]; this
module only multiplexes it. One writer thread per connection means
``sendall`` is never called re-entrantly.
"""

from __future__ import annotations

import base64
import json
import logging
import struct
import threading
from collections import deque
from typing import Callable

from composeio.transport import BusMessage, MessageType

logger = logging.getLogger("composeio.peer.frame")

# Substreams carried on the one channel. HIGH drains before LOW so bulk
# bodies never starve critical-message / ack forwarding ([BPR-ONE-CHANNEL]).
CH_HELLO = "hello"
CH_HELLO_OK = "hello_ok"
CH_HELLO_REJECT = "hello_reject"
CH_STATE = "state"
CH_CRIT = "crit"
CH_ACK = "ack"
CH_WRITE = "write"
CH_MIRROR_IDX = "mirror_idx"
CH_FETCH_REQ = "fetch_req"
CH_FETCH_RES = "fetch_res"

_HIGH = {CH_HELLO, CH_HELLO_OK, CH_HELLO_REJECT, CH_CRIT, CH_ACK}


def b64(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode("ascii")


def unb64(s: str | None) -> bytes | None:
    return None if s is None else base64.b64decode(s.encode("ascii"))


def msg_to_frame(msg: BusMessage) -> dict | None:
    """Classify a BusMessage onto a substream frame, or None to not forward.

    ``_bus/time`` heartbeats are dropped (per-bus infrastructure, not shared
    state). Critical STATE (``_priority == "critical"``) rides ``crit``
    carrying its ``cid``; an ``ack`` rides ``ack``; other STATE is ``state``
    (the caller may rewrite to ``write`` for the edge->master direction).
    """
    if msg.namespace == "_bus/time":
        return None
    if msg.type == MessageType.ACK:
        return {
            "ch": CH_ACK,
            "ns": msg.namespace,
            "cid": msg.data.get("_cid"),
            "val": msg.value,
            "src": msg.source,
        }
    if msg.type == MessageType.STATE:
        if msg.data.get("_priority") == "critical":
            return {
                "ch": CH_CRIT,
                "ns": msg.namespace,
                "val": msg.value,
                "src": msg.source,
                "cid": msg.data.get("_cid"),
            }
        return {
            "ch": CH_STATE,  # caller may rewrite to CH_WRITE
            "ns": msg.namespace,
            "val": msg.value,
            "src": msg.source,
        }
    return None


def _recvall(sock, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("peer connection closed")
        buf += chunk
    return bytes(buf)


def _send_frame(sock, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


def _recv_frames(sock):
    while True:
        hdr = _recvall(sock, 4)
        (ln,) = struct.unpack(">I", hdr)
        body = _recvall(sock, ln)
        yield json.loads(body.decode("utf-8"))


class MuxConn:
    """One multiplexed session over a connected socket.

    A single writer thread drains a HIGH queue (crit/ack/hello) before a LOW
    queue (bulk state/fetch), giving the substreams independent backpressure
    on the shared channel ([BPR-ONE-CHANNEL]). A single reader thread parses
    frames and hands each to ``on_frame(conn, frame)``.
    """

    def __init__(
        self,
        sock,
        on_frame: Callable[["MuxConn", dict], None],
        on_close: Callable[["MuxConn"], None] | None = None,
        name: str = "mux",
    ):
        self._sock = sock
        self._on_frame = on_frame
        self._on_close = on_close
        self._name = name
        self._hi: deque[dict] = deque()
        self._lo: deque[dict] = deque()
        self._cv = threading.Condition()
        self._alive = True
        self._close_when_drained = False
        self._wt = threading.Thread(target=self._writer, name=f"{name}-w", daemon=True)
        self._rt = threading.Thread(target=self._reader, name=f"{name}-r", daemon=True)

    def start(self) -> None:
        self._wt.start()
        self._rt.start()

    @property
    def alive(self) -> bool:
        return self._alive

    def send(self, frame: dict) -> bool:
        """Enqueue a frame. Returns False if the connection is already dead
        (the caller then buffers it for replay — [BPR-LINK-DOWN])."""
        ch = frame.get("ch")
        with self._cv:
            if not self._alive:
                return False
            (self._hi if ch in _HIGH else self._lo).append(frame)
            self._cv.notify()
        return True

    def drain_and_close(self) -> None:
        """Flush every queued frame, THEN shut down. Used for a handshake
        reject so the ``hello_reject`` frame reaches the peer before the socket
        closes (otherwise the peer just sees a bare connection drop)."""
        with self._cv:
            self._close_when_drained = True
            self._cv.notify()

    def _writer(self) -> None:
        while True:
            with self._cv:
                while (self._alive and not self._hi and not self._lo
                       and not self._close_when_drained):
                    self._cv.wait()
                if not self._alive:
                    return
                if self._hi:
                    frame = self._hi.popleft()
                elif self._lo:
                    frame = self._lo.popleft()
                else:
                    frame = None  # drained + close requested
            if frame is None:
                self._shutdown()
                return
            try:
                _send_frame(self._sock, frame)
            except OSError:
                self._shutdown()
                return

    def _reader(self) -> None:
        try:
            for frame in _recv_frames(self._sock):
                if not self._alive:
                    break
                try:
                    self._on_frame(self, frame)
                except Exception as e:  # a bad frame must not kill the session
                    logger.error(f"[{self._name}] frame handler error: {e}")
        except OSError:
            pass
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        with self._cv:
            if not self._alive:
                return
            self._alive = False
            self._cv.notify_all()
        try:
            self._sock.close()
        except OSError:
            pass
        if self._on_close:
            try:
                self._on_close(self)
            except Exception as e:
                logger.error(f"[{self._name}] on_close error: {e}")

    def close(self) -> None:
        self._shutdown()
