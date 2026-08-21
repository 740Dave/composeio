"""Stdlib SocketCAN reader — the whole ``python-can`` dep, replaced ([CXC-CAN-SOCKETCAN]).

SocketCAN lives in the Linux kernel and the Python stdlib already speaks it
(``socket.AF_CAN`` / ``SOCK_RAW`` / ``CAN_RAW`` / ``PF_CAN``). ``python-can``'s
``interface="socketcan"`` backend is a thin convenience wrapper over exactly
these socket calls; ``services/can_mqtt_bridge/bridge.py`` used only raw frames
(no notifiers, no CAN-FD, no ISO-TP), so the wrapper earned nothing but an
LGPL-3.0 obligation ([DD-CAN-NATIVE-PER-TIER], [CXC-CAN-LICENSE]).

This module has **ZERO third-party imports** — stdlib only ([CXC-CAN-VERIFY]
CAN-C3). The Linux-only ``socket`` constants are referenced INSIDE methods, never
at import time, so the module imports cleanly on macOS CI behind the mockcan seam.

The seam ([CXC-CAN-SOCKETCAN], [DD-BPR-MOCK-CAN]): one abstraction — "give me the
next ``(arb, eff, data)``" (:class:`CanReader`) — with a real ``AF_CAN`` impl
(:class:`SocketCanReader`) and a mock impl (:class:`MockCanReader`) over the
``mockcan.py`` substrate. Both funnel through the SAME pure decode
(:func:`parse_can_frame`), so the RTR/error/over-DLC drop rules ([CXC-CAN-FRAME])
are proven on the exact wire-decode path used by real hardware.

SocketCAN 16-byte ``struct can_frame`` (``=IB3x8s``):

    canid_t can_id;   // 32-bit: EFF/RTR/ERR flags in the high bits + the id
    __u8    can_dlc;  // 0..8 classic
    __u8    __pad, __res0, __res1;
    __u8    data[8];

Bitrate is NOT a socket/userspace concern — SocketCAN takes it from the
interface config (``ip link set can0 type can bitrate 500000``), which the
carousel deploy does via ``can0.service`` (systemd) ([CXC-CAN-HW-MCP2515]).
The foreign-ingestion path is RX-only ([CXC-CAN-LOOPGUARD]); ``send`` exists
ONLY for the ComposeBus-over-CAN transport role ([BP-MSG-CAN]) that ``bridge.py``
fills, never for foreign ingestion.
"""

from __future__ import annotations

import logging
import struct
from collections import deque
from typing import Protocol

logger = logging.getLogger("composeio.can.socketcan")

# --- SocketCAN can_id flag geometry (kernel ABI; stable) ---------------------
CAN_EFF_FLAG = 0x80000000   # extended (29-bit) frame format
CAN_RTR_FLAG = 0x40000000   # remote-transmission-request frame
CAN_ERR_FLAG = 0x20000000   # error frame
CAN_SFF_MASK = 0x000007FF   # standard 11-bit id
CAN_EFF_MASK = 0x1FFFFFFF   # extended 29-bit id

# --- setsockopt / socket constants (Linux kernel ABI) ------------------------
# Referenced only inside SocketCanReader methods so this module imports on macOS.
PF_CAN = 29
AF_CAN = 29
CAN_RAW = 1
SOL_CAN_RAW = 101
CAN_RAW_FILTER = 1

CAN_FRAME_FMT = "=IB3x8s"          # 16-byte struct can_frame
CAN_FRAME_SIZE = struct.calcsize(CAN_FRAME_FMT)  # == 16
CAN_MAX_DLC = 8


def make_can_id(arb: int, *, eff: bool = False, rtr: bool = False,
                err: bool = False) -> int:
    """Compose a SocketCAN 32-bit ``can_id`` (id + EFF/RTR/ERR flags).

    Used by tests to inject flag-bearing frames and by :meth:`SocketCanReader.send`
    to mark extended frames. A reader MUST derive ``eff`` from this flag, never
    from the id's magnitude ([CXC-CAN-FRAME])."""
    cid = arb & (CAN_EFF_MASK if eff else CAN_SFF_MASK)
    if eff:
        cid |= CAN_EFF_FLAG
    if rtr:
        cid |= CAN_RTR_FLAG
    if err:
        cid |= CAN_ERR_FLAG
    return cid


def pack_can_frame(can_id: int, data: bytes, *, dlc: int | None = None) -> bytes:
    """Pack a flag-bearing ``can_id`` + payload into the 16-byte wire frame.

    This is byte-for-byte what ``python-can``'s socketcan backend put on the wire
    ([CXC-CAN-VERIFY] CAN-C5). ``dlc`` defaults to ``len(data)``; an explicit
    ``dlc`` (e.g. >8) lets a test forge an over-DLC frame for the drop path."""
    payload = bytes(data[:CAN_MAX_DLC])
    d = len(payload) if dlc is None else dlc
    return struct.pack(CAN_FRAME_FMT, can_id & 0xFFFFFFFF, d & 0xFF,
                       payload.ljust(CAN_MAX_DLC, b"\x00"))


def parse_can_frame(raw: bytes) -> tuple[int, bool, bytes] | None:
    """Decode a 16-byte SocketCAN frame to ``(arb, eff, data[:dlc])``.

    Returns ``None`` (a DROP) — never raises — for:
      * an under-length read (fewer than 16 bytes) ([CXC-CAN-RAW] error-case),
      * an RTR or error frame ([CXC-CAN-FRAME]),
      * an over-DLC frame (dlc > 8) ([CXC-CAN-FRAME]).
    ``eff`` is taken from the EFF flag, never inferred from id magnitude."""
    if len(raw) < CAN_FRAME_SIZE:
        return None
    can_id, dlc, data8 = struct.unpack(CAN_FRAME_FMT, raw[:CAN_FRAME_SIZE])
    if can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG):
        return None
    if dlc > CAN_MAX_DLC:
        return None
    eff = bool(can_id & CAN_EFF_FLAG)
    arb = can_id & (CAN_EFF_MASK if eff else CAN_SFF_MASK)
    return arb, eff, data8[:dlc]


class CanReader(Protocol):
    """The one seam ([CXC-CAN-SOCKETCAN]): give me the next data frame.

    ``read_frame`` returns a VALID data frame ``(arb, eff, data)`` or ``None``
    when none is available within ``timeout``. RTR/error/over-DLC frames are
    dropped INSIDE the reader (logged, counted), never surfaced and never
    raised ([CXC-CAN-FRAME], [CXC-CAN-VERIFY] CAN-C2)."""

    def read_frame(self, timeout: float | None = None
                   ) -> tuple[int, bool, bytes] | None: ...

    def set_filters(self, filters: list[tuple[int, int]]) -> None: ...

    def close(self) -> None: ...


class _DropStats:
    """Per-reader drop counters ([CXC-CAN-VERIFY] CAN-C2 assertions)."""

    __slots__ = ("short", "rtr_or_err", "over_dlc")

    def __init__(self) -> None:
        self.short = 0
        self.rtr_or_err = 0
        self.over_dlc = 0

    @property
    def total(self) -> int:
        return self.short + self.rtr_or_err + self.over_dlc


def _classify_and_log(raw: bytes) -> tuple[int, bool, bytes] | None:
    """Parse + emit the right dropped-frame log; return the frame or None."""
    if len(raw) < CAN_FRAME_SIZE:
        logger.warning("Dropping under-length CAN read (%d < %d bytes)",
                       len(raw), CAN_FRAME_SIZE)
        return None
    can_id, dlc, _ = struct.unpack(CAN_FRAME_FMT, raw[:CAN_FRAME_SIZE])
    if can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG):
        logger.debug("Dropping RTR/error frame can_id=0x%08x", can_id)
        return None
    if dlc > CAN_MAX_DLC:
        logger.debug("Dropping over-DLC frame can_id=0x%08x dlc=%d", can_id, dlc)
        return None
    return parse_can_frame(raw)


class SocketCanReader:
    """Real ``AF_CAN``/``SOCK_RAW``/``CAN_RAW`` reader (Linux/RPi).

    ZERO third-party imports. Bitrate is an interface concern
    ([CXC-CAN-HW-MCP2515]); this class never sets it. Kernel-side acceptance
    filters are honored via ``CAN_RAW_FILTER`` ([CXC-PS-CAN] realization)."""

    def __init__(self, channel: str = "can0"):
        import socket  # stdlib
        self.channel = channel
        self.stats = _DropStats()
        # AF_CAN/CAN_RAW may be absent as module attrs on some builds — fall
        # back to the stable kernel constants above.
        af_can = getattr(socket, "AF_CAN", AF_CAN)
        can_raw = getattr(socket, "CAN_RAW", CAN_RAW)
        self._sock = socket.socket(af_can, socket.SOCK_RAW, can_raw)
        self._sock.bind((channel,))  # OSError here => caller degrades

    def set_filters(self, filters: list[tuple[int, int]]) -> None:
        """Install kernel acceptance filters — the silicon/kernel drops
        non-matching frames before userspace ([CXC-CAN-SOCKETCAN]). Each entry
        is ``(can_id, can_mask)``; an empty list means accept-all (the
        firehose)."""
        import socket
        sol = getattr(socket, "SOL_CAN_RAW", SOL_CAN_RAW)
        opt = getattr(socket, "CAN_RAW_FILTER", CAN_RAW_FILTER)
        blob = b"".join(struct.pack("=II", cid & 0xFFFFFFFF, mask & 0xFFFFFFFF)
                        for cid, mask in filters)
        self._sock.setsockopt(sol, opt, blob)

    def read_frame(self, timeout: float | None = None
                   ) -> tuple[int, bool, bytes] | None:
        import select
        if timeout is not None:
            ready, _, _ = select.select([self._sock], [], [], timeout)
            if not ready:
                return None
        raw = self._sock.recv(CAN_FRAME_SIZE)
        frame = _classify_and_log(raw)
        if frame is None:
            self._bump(raw)
        return frame

    def send(self, arb: int, data: bytes, *, eff: bool = False) -> None:
        """TX one frame — RESERVED for the ComposeBus-over-CAN transport role
        ([BP-MSG-CAN]), never foreign ingestion ([CXC-CAN-LOOPGUARD])."""
        self._sock.send(pack_can_frame(make_can_id(arb, eff=eff), data))

    def _bump(self, raw: bytes) -> None:
        if len(raw) < CAN_FRAME_SIZE:
            self.stats.short += 1
            return
        can_id, dlc, _ = struct.unpack(CAN_FRAME_FMT, raw[:CAN_FRAME_SIZE])
        if can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG):
            self.stats.rtr_or_err += 1
        elif dlc > CAN_MAX_DLC:
            self.stats.over_dlc += 1

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class MockCanReader:
    """Mock reader over the ``mockcan.py`` substrate ([DD-BPR-MOCK-CAN]).

    Runs the SAME :func:`parse_can_frame` decode as the real reader, so
    RTR/error/over-DLC drop and EFF handling are exercised on macOS CI with no
    ``can0`` and no ``python-can`` ([CXC-CAN-VERIFY] CAN-C3). Frames arrive
    either directly via :meth:`inject` or off a :class:`~composeio.peer.mockcan.MockCanBus`
    (flag-bearing ``can_id`` routed through a node), matching real SocketCAN
    where the ``can_id`` carries EFF/RTR/ERR."""

    def __init__(self, bus=None, filters: list[tuple[int, int]] | None = None,
                 name: str = "socketcan-reader"):
        self.stats = _DropStats()
        self._q: deque[bytes] = deque()
        self._filters = list(filters) if filters else []
        self._bus = bus
        self._node = None
        if bus is not None:
            from composeio.peer.mockcan import MockCanNode
            self._node = MockCanNode(name, on_receive=self._on_bus_frame)
            if self._filters:
                self._node.set_filters(self._filters)
            bus.attach(self._node)

    def _on_bus_frame(self, can_id: int, data: bytes) -> None:
        # MockCanBus routes a flag-bearing can_id exactly as the wire does.
        self._q.append(pack_can_frame(can_id, data))

    def inject(self, arb: int, data: bytes = b"", *, eff: bool = False,
               rtr: bool = False, err: bool = False,
               dlc: int | None = None) -> None:
        """Queue a raw frame for the next :meth:`read_frame` (test hook)."""
        self._q.append(pack_can_frame(make_can_id(arb, eff=eff, rtr=rtr, err=err),
                                      data, dlc=dlc))

    def inject_short(self, raw: bytes) -> None:
        """Queue an under-length read to exercise the short-frame drop."""
        self._q.append(raw)

    def set_filters(self, filters: list[tuple[int, int]]) -> None:
        self._filters = list(filters)
        if self._node is not None:
            self._node.set_filters(self._filters or [(0, 0)])

    def read_frame(self, timeout: float | None = None
                   ) -> tuple[int, bool, bytes] | None:
        while self._q:
            raw = self._q.popleft()
            frame = _classify_and_log(raw)
            if frame is None:
                self._bump(raw)
                continue
            return frame
        return None

    def _bump(self, raw: bytes) -> None:
        if len(raw) < CAN_FRAME_SIZE:
            self.stats.short += 1
            return
        can_id, dlc, _ = struct.unpack(CAN_FRAME_FMT, raw[:CAN_FRAME_SIZE])
        if can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG):
            self.stats.rtr_or_err += 1
        elif dlc > CAN_MAX_DLC:
            self.stats.over_dlc += 1

    def close(self) -> None:
        self._q.clear()


def open_can(channel: str = "can0") -> SocketCanReader:
    """Open a real SocketCAN reader. Raises ``OSError`` on a host without the
    interface — the caller degrades to MQTT-only ([CXC-CAN-BRIDGE-MIGRATE])."""
    return SocketCanReader(channel)
