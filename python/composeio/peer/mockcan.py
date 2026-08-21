"""Pure-Python in-memory virtual CAN bus — the Slice B test substrate.

[DD-BPR-MOCK-CAN] is OPEN in the spec; this realizes alternative (b): a
portable, zero-dependency in-memory CAN mock that models 29-bit arbitration
IDs + hardware acceptance masks. It is chosen over (a) Linux ``vcan`` (Linux +
kernel module, off the table on macOS CI) and (c) ``python-can``'s virtual bus
(absent from this environment; adds a dependency). The trade the spec flags —
CI simplicity vs hardware-path fidelity — is taken toward CI: the acceptance
mask is emulated in software (``admits``), the electrical/"hardware ack" is
modelled as "at least one non-sender node received the frame", and delivery is
SYNCHRONOUS so tests are deterministic with NO threads and NO sleeps.

The physical-CAN glass gate ([BPR-B-SYNTH] verify-glass — a real device
actuates-with-ack) is DEFERRED to hardware; nothing here claims to close it.

  * ``MockCanBus``  — the wire: routes a frame to every node whose acceptance
    filter admits its ID (minus the sender); a per-frame drop predicate models
    a lost fragment on the wire.
  * ``MockCanNode`` — an attachment point with acceptance filters.
  * ``MockCanEcu``  — a far-side ECU that reassembles data frames and, for a
    critical transfer (cid != 0), emits a [BP-ACK-CAN] type-8 APPLICATION ack
    frame ONLY when ``emit_app_ack`` is set — the distinction between "seen
    electrically" and "processed by firmware" that [BPR-B-SYNTH] rests on.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from composeio.peer.canmap import CanIdRegistry, admits
from composeio.peer.frag import Reassembler

# type-8 application-ack status <-> single payload byte.
_STATUS_TO_BYTE = {"ok": 0, "reject": 1, "error": 2}
_BYTE_TO_STATUS = {v: k for k, v in _STATUS_TO_BYTE.items()}


def encode_ack_status(status: str) -> bytes:
    return bytes([_STATUS_TO_BYTE.get(status, 0)])


def decode_ack_status(data: bytes) -> str:
    return _BYTE_TO_STATUS.get(data[0] if data else 0, "ok")


class MockCanNode:
    """A node on the mock bus: acceptance filters + an on-receive callback."""

    def __init__(self, name: str, on_receive: Callable[[int, bytes], None]):
        self.name = name
        self._on_receive = on_receive
        self._filters: list[tuple[int, int]] = [(0, 0)]  # default: promiscuous

    def set_filters(self, filters: list[tuple[int, int]]) -> None:
        self._filters = list(filters) if filters else [(0, 0)]

    def accepts(self, can_id: int) -> bool:
        return any(admits(f, m, can_id) for (f, m) in self._filters)

    def _receive(self, can_id: int, data: bytes) -> None:
        self._on_receive(can_id, data)


class MockCanBus:
    """In-memory CAN wire. Synchronous delivery; software acceptance masks."""

    def __init__(self):
        self._nodes: list[MockCanNode] = []
        self._lock = threading.RLock()
        self._drop: Callable[[int, bytes], bool] | None = None
        self.tx_count = 0
        self.dropped = 0

    def attach(self, node: MockCanNode) -> None:
        with self._lock:
            self._nodes.append(node)

    def set_drop(self, predicate: Callable[[int, bytes], bool] | None) -> None:
        """Install a per-frame drop predicate — models a fragment lost on the
        wire ([SCEN-BPR-B-REASSEMBLE]). ``predicate(can_id, data) -> True`` drops
        that frame (not delivered, no electrical ack)."""
        self._drop = predicate

    def send(self, can_id: int, data: bytes, sender: MockCanNode | None = None) -> bool:
        """Transmit one frame. Returns the ELECTRICAL/"hardware ack": True iff at
        least one non-sender node received it. This boolean means "seen on the
        wire", NOT "processed" — the bridge MUST NOT turn it into an upstream
        ``ok`` ([BPR-B-SYNTH])."""
        self.tx_count += 1
        if self._drop is not None and self._drop(can_id, bytes(data)):
            self.dropped += 1
            return False  # lost on the wire — no delivery, no electrical ack
        with self._lock:
            nodes = list(self._nodes)
        delivered = False
        for node in nodes:
            if node is sender:
                continue
            if node.accepts(can_id):
                node._receive(can_id, bytes(data))
                delivered = True
        return delivered


class MockCanEcu:
    """A far-side CAN ECU used by the Slice B harness.

    Reassembles inbound data frames (ISO-TP) and records them. For a CRITICAL
    transfer (arbitration-ID cid field != 0) it emits a type-8 application-ack
    frame back onto the bus ONLY if ``emit_app_ack`` is True. With
    ``emit_app_ack=False`` it still RECEIVES the frame electrically (the bus
    reports a hardware ack) but its firmware emits no application ack — the
    exact [SCEN-BPR-B-DEGRADE] condition.
    """

    def __init__(
        self,
        registry: CanIdRegistry,
        name: str = "can-ecu",
        emit_app_ack: bool = True,
        frame_size: int = 8,
        handler: Callable[[str, object], str] | None = None,
        now: Callable[[], float] = time.monotonic,
    ):
        self.registry = registry
        self.name = name
        self.emit_app_ack = emit_app_ack
        self.frame_size = frame_size
        self._handler = handler
        self._now = now
        self.received: list[tuple[str, object]] = []
        self.app_acks_sent = 0
        self._bus: MockCanBus | None = None
        self._reasm = Reassembler(frame_size)
        self.node = MockCanNode(name, on_receive=self._on_frame)
        self.node.set_filters([(0, 0)])  # promiscuous over data frames

    def attach_to(self, bus: MockCanBus) -> None:
        self._bus = bus
        bus.attach(self.node)

    def _on_frame(self, can_id: int, data: bytes) -> None:
        if self.registry.is_ack(can_id):
            return  # the ECU is an ack SOURCE, not a sink
        payload = self._reasm.feed(can_id, data, self._now())
        if payload is None:
            return
        import json
        try:
            ns = self.registry.id_to_namespace(can_id)
        except Exception:
            return
        val = json.loads(payload.decode("utf-8"))
        self.received.append((ns, val))
        status = "ok"
        if self._handler is not None:
            status = self._handler(ns, val) or "ok"
        cid = self.registry.cid_of(can_id)
        if cid != 0 and self.emit_app_ack and self._bus is not None:
            # [BP-ACK-CAN] type-8 APPLICATION ack — proof of PROCESSING, distinct
            # from the electrical hardware ack the wire already reported.
            self.app_acks_sent += 1
            self._bus.send(self.registry.ack_id(ns, cid),
                           encode_ack_status(status), sender=self.node)
