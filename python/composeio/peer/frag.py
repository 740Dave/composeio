"""ISO-TP-style fragmentation / reassembly over CAN frames ([BPR-B-FRAG]).

A ComposeBus payload routinely exceeds one CAN frame (8 bytes classic, 64
CAN-FD). This module segments an egress payload and reassembles it on ingress
using the bounded, standard ISO-TP (ISO 15765-2 / J1939-TP) scheme:

    Single Frame (SF):  PCI byte  0x0L            + L data bytes (L <= fs-1)
    First   Frame (FF): PCI bytes 0x1H 0xLL       + first (fs-2) data bytes
                        (12-bit total length H<<8|LL)
    Consec. Frame (CF): PCI byte  0x2S            + next (fs-1) data bytes
                        (4-bit sequence S, 1..15 wrapping to 0)

THE load-bearing rule ([BPR-B-FRAG]): a reassembly that loses a fragment or
times out MUST be dropped AND surfaced as an explicit fault — NEVER delivered
partial. A missing middle fragment is caught immediately as a sequence gap; a
truncated tail is caught by the reassembly timeout (default 1000ms). Both
discard the partial and emit a fault via the ``on_fault`` callback.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

# PCI high-nibble type codes.
PCI_SF = 0x00
PCI_FF = 0x10
PCI_CF = 0x20
PCI_MASK = 0xF0

DEFAULT_REASSEMBLY_TIMEOUT = 1.0  # seconds ([BPR-B-FRAG] default 1000ms)
MAX_ISOTP_LEN = (1 << 12) - 1     # 12-bit FF length field -> 4095 bytes


def _next_seq(seq: int) -> int:
    return 0 if seq >= 15 else seq + 1


def fragment(payload: bytes, frame_size: int = 8) -> list[bytes]:
    """Segment ``payload`` into a list of CAN frame payloads (ISO-TP).

    A payload that fits a single frame emits one SF; otherwise an FF followed
    by CFs. ``frame_size`` is the CAN frame data length (8 classic, 64 FD).
    """
    if frame_size < 3:
        raise ValueError("frame_size must be >= 3 for ISO-TP framing")
    if len(payload) > MAX_ISOTP_LEN:
        raise ValueError(
            f"payload {len(payload)}B exceeds ISO-TP 12-bit length {MAX_ISOTP_LEN}B")

    total = len(payload)
    if total <= frame_size - 1:
        return [bytes([PCI_SF | (total & 0x0F)]) + payload]

    frames: list[bytes] = []
    ff_data = payload[: frame_size - 2]
    frames.append(bytes([PCI_FF | ((total >> 8) & 0x0F), total & 0xFF]) + ff_data)
    rest = payload[frame_size - 2:]
    seq = 1
    while rest:
        chunk = rest[: frame_size - 1]
        frames.append(bytes([PCI_CF | (seq & 0x0F)]) + chunk)
        rest = rest[frame_size - 1:]
        seq = _next_seq(seq)
    return frames


@dataclass
class _Transfer:
    total: int
    buf: bytearray
    exp_seq: int
    started: float


class Reassembler:
    """Per-arbitration-ID ISO-TP reassembler with sequence-gap + timeout faults.

    ``feed(can_id, data, now)`` returns the complete payload on the final frame,
    else ``None``. A sequence gap discards the transfer and fires ``on_fault``
    immediately; ``poll_timeouts(now)`` fires ``on_fault`` for any transfer that
    has stalled past the timeout. Neither path ever returns a partial payload.
    """

    def __init__(
        self,
        frame_size: int = 8,
        timeout: float = DEFAULT_REASSEMBLY_TIMEOUT,
        on_fault: Callable[[int, str], None] | None = None,
    ):
        self.frame_size = frame_size
        self.timeout = timeout
        self._on_fault = on_fault
        self._transfers: dict[int, _Transfer] = {}
        self._lock = threading.RLock()

    def feed(self, can_id: int, data: bytes, now: float) -> bytes | None:
        if not data:
            return None
        pci = data[0] & PCI_MASK
        with self._lock:
            if pci == PCI_SF:
                self._transfers.pop(can_id, None)  # abandon any stale partial
                length = data[0] & 0x0F
                return bytes(data[1:1 + length])

            if pci == PCI_FF:
                total = ((data[0] & 0x0F) << 8) | data[1]
                self._transfers[can_id] = _Transfer(
                    total=total, buf=bytearray(data[2:]), exp_seq=1, started=now)
                return self._maybe_complete(can_id)

            if pci == PCI_CF:
                t = self._transfers.get(can_id)
                if t is None:
                    return None  # orphan CF (its FF was never seen) — ignore
                seq = data[0] & 0x0F
                if seq != t.exp_seq:
                    # A lost/reordered middle fragment. Discard the partial and
                    # fault — NEVER deliver partial ([BPR-B-FRAG]).
                    del self._transfers[can_id]
                    self._fault(can_id, "reassembly_sequence_gap")
                    return None
                t.buf += data[1:]
                t.exp_seq = _next_seq(t.exp_seq)
                return self._maybe_complete(can_id)
        return None

    def _maybe_complete(self, can_id: int) -> bytes | None:
        t = self._transfers[can_id]
        if len(t.buf) >= t.total:
            del self._transfers[can_id]
            return bytes(t.buf[: t.total])
        return None

    def poll_timeouts(self, now: float) -> list[int]:
        """Fault + discard any transfer stalled past the timeout (a lost TAIL
        fragment leaves no gap to catch, so the timeout is the safety net).
        Returns the list of timed-out arbitration IDs."""
        timed_out: list[int] = []
        with self._lock:
            for can_id, t in list(self._transfers.items()):
                if now - t.started > self.timeout:
                    del self._transfers[can_id]
                    timed_out.append(can_id)
        for can_id in timed_out:
            self._fault(can_id, "reassembly_timeout")
        return timed_out

    def pending_ids(self) -> list[int]:
        with self._lock:
            return list(self._transfers)

    def _fault(self, can_id: int, reason: str) -> None:
        if self._on_fault is not None:
            self._on_fault(can_id, reason)
