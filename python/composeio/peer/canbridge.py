"""CanBridge — the MQTT<->CANbus bridge edge ([BPR-SHAPE] transport-bridge row).

Slice B is a CONSUMER of the Slice A core, not a fork: the bridge is a peer
whose far side speaks CAN. It plugs into the local ComposeBus as the standard
``_global`` Transport ([SPEC:CIO-APP]) — exactly where a ``PeerHub`` /
``PeerLink`` plugs — so an egress publish arrives at ``send`` and an inbound CAN
frame is dispatched onto the local bus via the wildcard subscriber that
``bus._wire_transport_incoming`` registered ("dispatched identically to live
traffic"). It reuses the Slice A pieces by reference:

  * addressing seam        -> canmap.CanIdRegistry     ([BPR-B-CANMAP])
  * segmentation           -> frag.fragment/Reassembler ([BPR-B-FRAG])
  * provenance / loop guard-> provenance.stamp/is_relayed ([BPR-PROVENANCE])
  * critical-ack truth     -> the ORIGIN's ack-pending table (critical.py) —
                              the bridge adds NO ack authority ([BPR-CRITICAL-ACK])

[BPR-B-SYNTH] discipline — the CAN side has no QoS/retained/LWT, so the bridge
SYNTHESIZES or DEGRADES EXPLICITLY:

  * retained  -> ``rebroadcast_retained()`` re-broadcasts last-known state to a
    late CAN joiner (synthesized retain);
  * critical-ack -> a CONFIRMED upstream ack is derived ONLY from an ACTUAL
    [BP-ACK-CAN] type-8 frame observed on the CAN side. A bare electrical
    hardware ack ("seen on the wire") is recorded in ``hw_acks`` but NEVER
    turned into an upstream ``ok``. Absent a type-8 frame, the origin's own
    retry/fault budget degrades to ``_bus/fault`` — silent-clear stays 0. This
    EXTENDS the Slice A transparent-forwarder discipline to the CAN edge.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

from composeio.transport import BusMessage, MessageType, Transport
from composeio.transport_contract import FarSide, TransportProfile
from composeio.peer import provenance as P
from composeio.peer.canmap import CanIdRegistry, MappingMiss
from composeio.peer.frag import fragment, Reassembler
from composeio.peer.mockcan import MockCanBus, MockCanNode, decode_ack_status

logger = logging.getLogger("composeio.peer.canbridge")


class CanBridge(Transport):
    """A ComposeBus Transport bridging a scoped namespace slice to a CAN bus."""

    def __init__(
        self,
        registry: CanIdRegistry,
        can_bus: MockCanBus,
        subscriptions: list[str] | None = None,
        node_id: str = "can-bridge",
        acker_id: str = "can-ecu",
        frame_size: int = 8,
        reassembly_timeout: float = 1.0,
        now: Callable[[], float] = time.monotonic,
    ):
        # The FAT / BRIDGE pole, far side = a MAPPED DEVICE BUS
        # ([BPR-TRANSPORT-CONTRACT]): CAN has no native QoS/retained/ack, so the
        # bridge maps namespaces to arbitration IDs ([BPR-B-CANMAP]) and
        # synthesizes-or-degrades critical-ack from real type-8 evidence
        # ([BPR-B-SYNTH]) — never fabricating an ``ok``. This is the first real
        # test of the BRIDGE pole against a non-peer far side.
        self.profile = TransportProfile.bridge(far_side=FarSide.MAPPED_DEVICE_BUS)

        self.registry = registry
        self.can = can_bus
        self._subs = list(subscriptions or [])
        self.node_id = node_id
        self.acker_id = acker_id
        self.frame_size = frame_size
        self._now = now

        self._all_subscribers: list[Callable[[BusMessage], None]] = []
        self._last_state: dict[str, Any] = {}
        self._last_lock = threading.RLock()

        # Audit trails (test probes).
        self.faults: list[dict] = []
        self.hw_acks: list[tuple[int, bool]] = []   # (cid, electrical-ack) — MUST NOT drive ok
        self.app_acks_in: list[tuple[str, int, str]] = []  # (ns, cid, status)
        self.egress_frames = 0

        self._reasm = Reassembler(frame_size, reassembly_timeout,
                                  on_fault=self._on_reasm_fault)
        self._node = MockCanNode(node_id, on_receive=self._on_can_frame)
        self._install_filters()
        self._running = False

    # --- acceptance filters ([BPR-B-CANMAP]) -------------------------------

    def _install_filters(self) -> None:
        """One acceptance (filter, mask) per prefix subscription ([SCEN-BPR-B-MASK])
        PLUS one filter admitting every type-8 ack frame (acks ride back to the
        bridge regardless of the data subscription scope)."""
        from composeio.peer.canmap import ACK_FLAG
        filters: list[tuple[int, int]] = []
        for pat in self._subs:
            try:
                filters.append(self.registry.prefix_to_mask(pat))
            except MappingMiss:
                logger.warning(f"can bridge: no CAN block for subscription {pat!r}")
        filters.append((ACK_FLAG, ACK_FLAG))  # admit all application-ack frames
        self._node.set_filters(filters)

    def subscribe_can(self, pattern: str) -> tuple[int, int]:
        """Widen the CAN acceptance scope by ONE prefix subscription and return
        the single (filter, mask) it lowered to."""
        if pattern not in self._subs:
            self._subs.append(pattern)
        pair = self.registry.prefix_to_mask(pattern)
        self._install_filters()
        return pair

    def acceptance_filters(self) -> list[tuple[int, int]]:
        return list(self._node._filters)

    # --- Transport interface ----------------------------------------------

    def send(self, msg: BusMessage) -> None:
        """Egress: a local publish -> CAN frames. Critical frames carry their
        cid in the arbitration ID; the bridge never synthesizes an ack here."""
        if P.is_relayed(msg):
            return  # loop guard ([BPR-PROVENANCE]) — don't re-forward CAN-sourced traffic
        if msg.type != MessageType.STATE:
            return  # acks come FROM CAN; nothing else egresses to CAN in Slice B
        if msg.namespace == "_bus/time":
            return
        critical = msg.data.get("_priority") == "critical"
        cid = int(msg.data.get("_cid", 0)) if critical else 0
        try:
            can_id = self.registry.namespace_to_id(msg.namespace, cid)
        except MappingMiss:
            self._emit_fault("mapping_miss", msg.namespace, cid=cid,
                             direction="egress")
            return  # NEVER fabricate a CAN-ID ([BPR-B-CANMAP])
        payload = json.dumps(msg.value, separators=(",", ":")).encode("utf-8")
        with self._last_lock:
            self._last_state[msg.namespace] = msg.value
        hw = False
        for fr in fragment(payload, self.frame_size):
            self.egress_frames += 1
            hw = self.can.send(can_id, fr, sender=self._node) or hw
        if critical:
            # Record the ELECTRICAL ack only. It is proof of "seen on the wire",
            # not "processed" — it MUST NOT become an upstream ok. The upstream
            # ack (if any) arrives as a type-8 frame in _on_can_frame; absent it,
            # the origin's ack-pending table degrades to _bus/fault.
            self.hw_acks.append((cid, hw))

    def subscribe(self, namespace: str, callback) -> None:
        return None

    def subscribe_all(self, callback: Callable[[BusMessage], None]) -> None:
        self._all_subscribers.append(callback)

    def unsubscribe(self, namespace: str, callback) -> None:
        return None

    def start(self) -> None:
        if self._running:
            return
        self.can.attach(self._node)
        self._running = True
        logger.info(f"CanBridge '{self.node_id}' up, scope={self._subs}")

    def stop(self) -> None:
        self._running = False

    @property
    def is_connected(self) -> bool:
        return self._running

    # --- CAN ingress -------------------------------------------------------

    def _on_can_frame(self, can_id: int, data: bytes) -> None:
        if self.registry.is_ack(can_id):
            self._handle_app_ack(can_id, data)
            return
        payload = self._reasm.feed(can_id, data, self._now())
        if payload is None:
            return  # mid-transfer, or a gap/fault already surfaced by the reassembler
        try:
            ns = self.registry.id_to_namespace(can_id)
        except MappingMiss:
            self._emit_fault("mapping_miss", None, direction="ingress",
                             can_id=can_id)
            return
        try:
            val = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._emit_fault("decode_error", ns, direction="ingress")
            return
        with self._last_lock:
            self._last_state[ns] = val
        self._dispatch_state(ns, val, self.registry.cid_of(can_id))

    def _handle_app_ack(self, can_id: int, data: bytes) -> None:
        """A [BP-ACK-CAN] type-8 frame is CONFIRMED far-side delivery evidence.
        THIS — and only this — drives a genuine upstream ack ([BPR-B-SYNTH])."""
        try:
            ns = self.registry.id_to_namespace(can_id)
        except MappingMiss:
            return
        cid = self.registry.cid_of(can_id)
        status = decode_ack_status(data)
        self.app_acks_in.append((ns, cid, status))
        stamped = P.stamp({"_cid": cid}, self.node_id)
        msg = BusMessage(
            namespace=ns, type=MessageType.ACK, value=status,
            source=P.prefixed_source(self.node_id, self.acker_id),
            scope="global", data=stamped,
        )
        self._dispatch(msg)

    def _dispatch_state(self, namespace: str, value: Any, cid: int) -> None:
        data = P.stamp({}, self.node_id)
        if cid != 0:
            data["_priority"] = "critical"
            data["_cid"] = cid
        msg = BusMessage(
            namespace=namespace, type=MessageType.STATE, value=value,
            source=P.prefixed_source(self.node_id, self.acker_id),
            scope="global", data=data,
        )
        self._dispatch(msg)

    def _dispatch(self, msg: BusMessage) -> None:
        for cb in list(self._all_subscribers):
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"can bridge dispatch error: {e}")

    # --- synthesize-or-degrade ([BPR-B-SYNTH]) -----------------------------

    def rebroadcast_retained(self) -> int:
        """Synthesized retain: re-broadcast last-known state for every mapped
        namespace to a late CAN joiner. Returns the frame count sent."""
        with self._last_lock:
            snapshot = dict(self._last_state)
        sent = 0
        for ns, val in snapshot.items():
            try:
                can_id = self.registry.namespace_to_id(ns)
            except MappingMiss:
                continue
            payload = json.dumps(val, separators=(",", ":")).encode("utf-8")
            for fr in fragment(payload, self.frame_size):
                self.can.send(can_id, fr, sender=self._node)
                sent += 1
        return sent

    def poll_reassembly(self, now: float | None = None) -> list[int]:
        """Advance the ingress reassembly timeout ([BPR-B-FRAG]). A stalled
        transfer (lost tail fragment) is discarded and faulted here."""
        return self._reasm.poll_timeouts(self._now() if now is None else now)

    def last_state(self, namespace: str) -> Any:
        with self._last_lock:
            return self._last_state.get(namespace)

    # --- fault surfacing ---------------------------------------------------

    def _on_reasm_fault(self, can_id: int, reason: str) -> None:
        ns = None
        try:
            ns = self.registry.id_to_namespace(can_id)
        except MappingMiss:
            pass
        self._emit_fault(reason, ns, direction="ingress", can_id=can_id)

    def _emit_fault(self, kind: str, namespace: str | None, **extra) -> None:
        fault = {"fault": kind, "target_ns": namespace, "peer": self.node_id}
        fault.update(extra)
        self.faults.append(fault)
        msg = BusMessage(
            namespace="_bus/fault", type=MessageType.EVENT, event_type="fault",
            source=self.node_id, scope="global", data=fault,
        )
        self._dispatch(msg)
