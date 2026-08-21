#!/usr/bin/env python3
"""CAN transport binding — ComposeBus over a CAN device bus.

DP-CIO-6: the same bus semantics over CAN as over MQTT/UART/local. An app calls
`publish_state(ns, value)` and never learns which substrate carried it.

FAR-SIDE TYPE: `MAPPED_DEVICE_BUS` per [BPR-TRANSPORT-CONTRACT]. CAN has no
native retained messages, no last-will, no broker-side subscription filtering and
no QoS — so those semantics are MAPPED or HONESTLY DEGRADED here rather than
faked. A degraded semantic that pretends to work is worse than one that reports
itself, which is the whole lesson of this phase.

NO HARDWARE REQUIRED (David, 2026-08-08: "I don't have CAN hardware wired up
currently... but DO make it a real Transport"). The default backend is an
in-process LOOPBACK that implements the full frame path — encode, arbitration-id
assignment, filter match, decode — so every semantic is testable today and the
real interface is a one-word config change when the hardware is wired:

    transports:
      - {type: can, channel: loopback}          # today: no hardware
      - {type: can, channel: can0, backend: socketcan}   # when wired

WIRE MAPPING — the part that makes CAN a good fit
-------------------------------------------------
A CAN frame is an 11/29-bit ARBITRATION ID plus <=8 data bytes. The id is not an
address — receivers filter on it in hardware, which means a namespace hash IN the
id gives us subscription filtering for free, at the controller, with zero CPU on
uninterested nodes. That is why CAN suits a pub/sub bus and i2c does not.

    29-bit extended id:  [ 3b prio ][ 4b type ][ 16b ns_id ][ 4b src ][ 2b scope ]
    payload:             the value, encoded per state engine (reuses
                         `transport_uart`'s encoder — the SAME value encoding as
                         the UART substrate, so an MCU speaks one dialect)

[BP-CAN-ID] v2, ratified 2026-08-13, and it ENDS [BP-CAN-ID-DIVERGENCE]. There
were two layouts in this repo that disagreed about what arbitration is FOR:
`ns_hash(16)|type(4)|src(9)` in the firmware and the deployed bridge, and
`type(4)|scope(2)|ns(16)|seq(7)` here. There is now exactly one, spoken by
`cyd/src/bus_can_id.h`, `services/can_mqtt_bridge` and this module, and
parity-tested (env:native_can_id).

Field by field, because each one is load-bearing:

  prio(3)   [BP-WIRE-PRIORITY] — `composeio.transport.priority_for`, a RULE with
            a default rather than a list. SENDER-SIDE ONLY: nothing decodes it.
            Before it existed, `ns_hash` sat in the top 16 bits, so arbitration
            priority was a pseudo-random function of the namespace NAME —
            `throttle/left` (0x03F6) won everything by luck and `shields`
            (0xCC50) was the lowest-priority thing on the bus.
  type(4)   [BP-TYPE-CODES], UNCHANGED and deliberately NOT renumbered by
            urgency. Renumbering was proposed and rejected: it breaks UART for a
            property UART cannot use (point-to-point has no arbitration), welds
            identity to urgency permanently, and cannot solve WITHIN-class
            priority by construction. `prio` makes it unnecessary.
  ns_id(16) `composeio.config.namespace_hash`, CRC-16/CCITT-FALSE. Same width as
            before, so no new collision exposure — the existing 16-bit birthday
            curve (2.3% at 56 namespaces, 39% at 256) is inherited, not worsened.
  src(4)    An ALLOCATED node id, 1..15, with 0 meaning UNASSIGNED. Not a hash
            slice: the old `hash(source) & 0x1FF` was a nine-bit smear of a
            salted string hash that changed between two runs of the same
            process. Four bits is a diagnostic, not an address.
  scope(2)  local/global/remote/auto, AT THE BOTTOM. This field did not exist in
            the layout the bridge and the firmware spoke, so `scope` was silently
            LOST on every CAN round trip and reconstructed as "global" on the far
            side. That is load-bearing for the mothercard/daughtercard
            architecture: an internal CAN trunk is a LOCALITY, and a relay that
            cannot see scope leaks all local chatter upstream.

WHY `scope` IS AT THE BOTTOM AND NOT UNDER `type`. Directly under `type` it
becomes a second, undesigned arbitration axis — and a backwards one, since
`auto` (3) is the default for anything unspecified and would land every
unspecified message at the bottom of its class. Below `ns_id` it cannot distort
arbitration between different messages at all.

`seq(7)` is GONE. Its own docstring described it as "a rolling counter with no
consumer that needs 512 values"; nothing ever read it.

BRIDGING STAYS FIRST-CLASS. `services/can_mqtt_bridge` translates between two
LIVE substrates (MQTT and CAN both running); this Transport lets an app speak CAN
ALONE. They are complementary, not alternatives, and registering this does not
retire that bridge.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Dict, List, Optional, Tuple

from composeio.transport import (
    BusMessage, MessageType, Transport,
    PRIORITY_BITS, WIRE_TYPE_CODE_BITS,
    message_type_for_wire_code, priority_for, wire_code_for_type,
)

logger = logging.getLogger(__name__)

# --- [BP-CAN-ID] v2 --------------------------------------------------------- #
# 29-bit extended id: [3b prio][4b type][16b ns_id][4b src][2b scope]
# Widths first, then shifts DERIVED from them, so the layout is stated once and
# a change cannot leave a shift behind. 3+4+16+4+2 == 29 is asserted below.
_PRIO_BITS = PRIORITY_BITS                          # 3 — [BP-WIRE-PRIORITY]
_TYPE_BITS = WIRE_TYPE_CODE_BITS                    # 4 — [BP-TYPE-CODES]
_NS_HASH_BITS = 16
_SRC_BITS = 4
_SCOPE_BITS = 2

CAN_ID_BITS = 29
assert _PRIO_BITS + _TYPE_BITS + _NS_HASH_BITS + _SRC_BITS + _SCOPE_BITS \
    == CAN_ID_BITS, "[BP-CAN-ID] v2 must sum to exactly 29 bits"

_SCOPE_SHIFT = 0                                                  # 0
_SRC_SHIFT = _SCOPE_BITS                                          # 2
_NS_HASH_SHIFT = _SRC_SHIFT + _SRC_BITS                           # 6
_TYPE_SHIFT = _NS_HASH_SHIFT + _NS_HASH_BITS                      # 22
_PRIO_SHIFT = _TYPE_SHIFT + _TYPE_BITS                            # 26

_PRIO_MASK = (1 << _PRIO_BITS) - 1
_TYPE_MASK = (1 << _TYPE_BITS) - 1
_NS_HASH_MASK = (1 << _NS_HASH_BITS) - 1
_SRC_MASK = (1 << _SRC_BITS) - 1
_SCOPE_MASK = (1 << _SCOPE_BITS) - 1
CAN_ID_MASK = (1 << CAN_ID_BITS) - 1

#: `src` == 0 means "this node has no allocated id". Honest, and distinguishable
#: from node 1 — which a hash slice never was.
NODE_ID_UNASSIGNED = 0
NODE_ID_MAX = _SRC_MASK                                           # 15

# Type codes are NOT declared here — `wire_code_for_type` /
# `message_type_for_wire_code` ARE the table ([BP-TYPE-CODES], one
# implementation reused by reference). Scope codes are the spec's 2-bit enum
# from [BP-MSG-FIELDS]; `auto` is carried as `auto` (3) and resolved by the
# receiving runtime, not collapsed to `global` on the wire as it was before.
_SCOPE_CODES = {"local": 0x0, "global": 0x1, "remote": 0x2, "auto": 0x3}
_CODE_SCOPES = {v: k for k, v in _SCOPE_CODES.items()}


def _ns_hash(namespace: str) -> int:
    """The SHARED namespace hash — same function UART and the CAN bridge use."""
    from composeio.config import namespace_hash
    return int(namespace_hash(namespace)) & _NS_HASH_MASK


def node_id_from_env(default: int = NODE_ID_UNASSIGNED) -> int:
    """`COMPOSEIO_CAN_NODE_ID` -> an allocated node id, or 0 (unassigned).

    OUT OF RANGE IS NOT MASKED. `& 0xF` would turn node 16 into node 0 and node
    17 into node 1 — two nodes claiming one id, diagnosable only from a capture.
    A bad value is refused loudly and the node stays unassigned, which is a
    condition anything reading a capture can see.
    """
    raw = os.environ.get("COMPOSEIO_CAN_NODE_ID")
    if raw is None or raw == "":
        return default
    try:
        node = int(raw, 0)
    except ValueError:
        logger.error("COMPOSEIO_CAN_NODE_ID=%r is not an integer — this node "
                     "stays UNASSIGNED (src=0), not silently renumbered.", raw)
        return NODE_ID_UNASSIGNED
    if not (NODE_ID_UNASSIGNED <= node <= NODE_ID_MAX):
        logger.error("COMPOSEIO_CAN_NODE_ID=%d is outside 0..%d ([BP-CAN-ID] "
                     "src is 4 bits) — this node stays UNASSIGNED (src=0). It "
                     "is NOT masked: 16 would become 0 and collide.",
                     node, NODE_ID_MAX)
        return NODE_ID_UNASSIGNED
    return node


def encode_arbitration_id(namespace: str, mtype: MessageType, scope: str,
                          node_id: int = NODE_ID_UNASSIGNED,
                          priority: Optional[int] = None) -> int:
    """(namespace, type, scope, node) -> a 29-bit CAN arbitration id.

    `priority` defaults to the ratified rule (`transport.priority_for`); pass it
    explicitly only to override a tier deliberately, never to invent one.

    Raises `UnmappedMessageType` for a type with no allocated wire code —
    every allocated type fits the 4-bit field, so refusing is honest where
    substituting STATE was not ([BP-TYPE-CODES]).
    """
    prio = priority_for(namespace, mtype) if priority is None else priority
    return ((prio & _PRIO_MASK) << _PRIO_SHIFT) \
        | ((wire_code_for_type(mtype) & _TYPE_MASK) << _TYPE_SHIFT) \
        | ((_ns_hash(namespace) & _NS_HASH_MASK) << _NS_HASH_SHIFT) \
        | ((int(node_id) & _SRC_MASK) << _SRC_SHIFT) \
        | ((_SCOPE_CODES.get(scope, 0x3) & _SCOPE_MASK) << _SCOPE_SHIFT)


def decode_arbitration_id(
        arb_id: int) -> Tuple[int, Optional[MessageType], str, int]:
    """Inverse of `encode_arbitration_id` -> (ns_hash, type, scope, src).

    `type` is None when the id carries an unallocated code (10..15) — the
    caller drops the frame and says so. It is never guessed as STATE.

    `prio` is NOT returned. It is the sender's own opinion of its own urgency,
    it has already done its only job (winning or losing arbitration) by the time
    a receiver sees the frame, and a receiver that acted on it would be routing
    on an unverifiable claim. Read it from a capture, not from a decoder.
    """
    return (
        (arb_id >> _NS_HASH_SHIFT) & _NS_HASH_MASK,
        message_type_for_wire_code((arb_id >> _TYPE_SHIFT) & _TYPE_MASK),
        _CODE_SCOPES.get((arb_id >> _SCOPE_SHIFT) & _SCOPE_MASK, "auto"),
        (arb_id >> _SRC_SHIFT) & _SRC_MASK,
    )


def arbitration_priority(arb_id: int) -> int:
    """The 3-bit tier an id was SENT with. For captures and tests only."""
    return (arb_id >> _PRIO_SHIFT) & _PRIO_MASK


def can_id_ns_hash(arb_id: int) -> int:
    """The namespace hash out of an id — for naming a frame that DIDN'T decode.

    Exists so a caller that has to report an undecodable frame does not open-code
    the shift. Every open-coded `(id >> 13) & 0xFFFF` in this repo was a copy of
    the layout, and copies of the layout are what [BP-CAN-ID-DIVERGENCE] was.
    """
    return (arb_id >> _NS_HASH_SHIFT) & _NS_HASH_MASK


class LoopbackCANBus:
    """In-process stand-in for a CAN interface — the hardware-free backend.

    Implements the only two operations a CAN controller owes us: send a frame,
    and deliver frames to a receiver. Frames are delivered to every OTHER
    attached node, never back to the sender, which is what a real bus does (a
    node does not receive its own transmission).
    """

    def __init__(self) -> None:
        self._nodes: List[Callable[[int, bytes], None]] = []
        self._lock = threading.RLock()
        self.sent: List[Tuple[int, bytes]] = []      # inspectable in tests

    def attach(self, on_frame: Callable[[int, bytes], None]) -> None:
        with self._lock:
            self._nodes.append(on_frame)

    def detach(self, on_frame: Callable[[int, bytes], None]) -> None:
        with self._lock:
            if on_frame in self._nodes:
                self._nodes.remove(on_frame)

    def send(self, arb_id: int, data: bytes, sender=None) -> None:
        with self._lock:
            self.sent.append((arb_id, data))
            targets = [n for n in self._nodes if n is not sender]
        for n in targets:
            try:
                n(arb_id, data)
            except Exception as exc:               # one bad node never stops the bus
                logger.warning("CAN loopback node raised: %r", exc)


# One shared loopback per channel name, so two CANTransports on "loopback"
# actually see each other — that is what makes an end-to-end test meaningful.
_LOOPBACKS: Dict[str, LoopbackCANBus] = {}
_LOOPBACK_LOCK = threading.RLock()


def loopback_bus(channel: str = "loopback") -> LoopbackCANBus:
    with _LOOPBACK_LOCK:
        return _LOOPBACKS.setdefault(channel, LoopbackCANBus())


def _reset_loopbacks_for_tests() -> None:
    with _LOOPBACK_LOCK:
        _LOOPBACKS.clear()


class CANTransport(Transport):
    """ComposeBus over CAN. Backend defaults to in-process loopback."""

    def __init__(self, channel: str = "loopback", bitrate: int = 500000,
                 backend: Optional[str] = None, app_id: str = "composeio",
                 node_id: Optional[int] = None) -> None:
        self.channel = channel
        self.bitrate = bitrate
        self.app_id = app_id
        # The ALLOCATED 4-bit node id in the `src` field ([BP-CAN-ID] v2).
        # Defaults to 0 = UNASSIGNED, which is the truth for a node nobody has
        # allocated one to — it is not a fallback identity, and two unassigned
        # nodes are visibly indistinguishable rather than accidentally distinct.
        self.node_id = node_id_from_env() if node_id is None else int(node_id)
        if not (NODE_ID_UNASSIGNED <= self.node_id <= NODE_ID_MAX):
            raise ValueError(
                f"node_id {self.node_id} is outside 0..{NODE_ID_MAX} — the "
                f"[BP-CAN-ID] src field is 4 bits and is NOT masked here, "
                f"because masking makes 16 and 0 the same node.")
        # `socketcan` only when explicitly asked for OR when a non-loopback
        # channel is named. Default stays hardware-free so nothing in CI or on a
        # dev laptop needs a CAN interface to exercise the semantics.
        self.backend = backend or ("loopback" if channel == "loopback" else "socketcan")
        self._subs: Dict[str, List[Callable[[BusMessage], None]]] = {}
        self._all: List[Callable[[BusMessage], None]] = []
        # ns-hash -> namespace, so an inbound frame can be resolved back to a
        # name. CAN carries only the HASH, so a namespace we have never sent or
        # subscribed is genuinely unresolvable — reported, never guessed.
        self._hash_to_ns: Dict[int, str] = {}
        # THE WELL-KNOWN ANNOUNCEMENT ROOT, pre-registered before anything else
        # ([PLAN G1]). Without it this transport can name only namespaces IT has
        # sent or subscribed — so `composeio fleet --transport type=can` could
        # observe frames from a board and never learn what they were called,
        # because `_device/<id>/*` embeds a device id the tool is trying to
        # discover. One constant entry, and the rest is announced.
        from composeio.ns_announce import (
            NS_ANNOUNCE, NS_ANNOUNCE_HASH, AnnounceLearner)
        self._hash_to_ns[NS_ANNOUNCE_HASH] = NS_ANNOUNCE
        #: Namespaces this transport learned from a CAN-side announcement, so a
        #: caller can tell a discovered name from one it configured itself.
        self.announced: Dict[str, int] = {}
        self._announce_learner = AnnounceLearner(
            self._learn_announced, label=f"can:{channel}")
        self._lock = threading.RLock()
        self._started = False
        self._loop: Optional[LoopbackCANBus] = None
        self._can = None
        # Bind the rx callback ONCE. `self._on_frame` yields a NEW bound-method
        # object on every attribute access, so `node is not sender` identity
        # checks silently never match — which made the loopback deliver a frame
        # back to its own sender (a real CAN node never hears its own
        # transmission). Caught by the round-trip test; keep the single binding.
        self._frame_cb = self._on_frame

    # -- lifecycle ---------------------------------------------------------- #
    def start(self) -> None:
        if self._started:
            return
        if self.backend == "loopback":
            self._loop = loopback_bus(self.channel)
            self._loop.attach(self._frame_cb)
        else:
            # Real hardware. Imported lazily and ONLY on this branch, so the
            # loopback path has no python-can/socketcan dependency at all.
            from composeio.peer.socketcan import open_can
            self._can = open_can(self.channel, bitrate=self.bitrate)
            threading.Thread(target=self._rx_loop, name=f"can-rx:{self.channel}",
                             daemon=True).start()
        self._started = True
        logger.info("CAN transport up on %s (backend=%s)", self.channel, self.backend)

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.detach(self._frame_cb)
        if self._can is not None:
            try:
                self._can.shutdown()
            except Exception:
                pass
        self._started = False

    @property
    def is_connected(self) -> bool:
        """Operational.

        A PROPERTY — as the ABC declares and as every other transport
        implements. All 12 call sites read it as an attribute
        (`self._global.is_connected`, no parens), so a plain method hands them a
        bound object that is always truthy and a dead CAN link would report
        CONNECTED forever.

        I wrote this as a plain method first and a conformance test caught it.
        CORRECTION to my own earlier note here, which claimed the ABC declared it
        with `def` and that implementations diverged: that was wrong — I misread
        the `def` line without its `@property` decorator. The ABC was correct all
        along and this class was the sole outlier. The real gap was that ABCs do
        not ENFORCE property-ness, now closed by `Transport.__init_subclass__`.

        Loopback is simply "started": an in-process bus cannot be unreachable.
        Real hardware also requires a live interface handle, so a cable pulled at
        runtime reports honestly rather than looking up.
        """
        if not self._started:
            return False
        return True if self.backend == "loopback" else self._can is not None

    # -- Transport ABC ------------------------------------------------------ #
    def send(self, message: BusMessage) -> None:
        if not self._started:
            self.start()
        with self._lock:
            self._hash_to_ns[_ns_hash(message.namespace)] = message.namespace
        # `message.scope or "auto"` — NOT "global". An unset scope means the
        # sender did not say, and `auto` is exactly the bus's word for that;
        # substituting "global" was a claim about locality nobody made, and on a
        # mothercard trunk that claim is what leaks local chatter upstream.
        arb = encode_arbitration_id(message.namespace, message.type,
                                    message.scope or "auto", self.node_id)
        data = self._encode_value(message.value)
        if len(data) > 8:
            # HONEST DEGRADATION, not silent truncation: a classic CAN frame is
            # 8 bytes. Fragmentation across frames is a real feature and is NOT
            # implemented yet, so say so instead of shipping half a payload.
            logger.warning(
                "CAN: %s payload is %d bytes (>8); classic CAN cannot carry it and "
                "fragmentation is not implemented — DROPPED, not truncated.",
                message.namespace, len(data))
            return
        if self._loop is not None:
            self._loop.send(arb, data, sender=self._frame_cb)
        elif self._can is not None:
            self._can.send(arb, data)

    def subscribe(self, namespace: str, callback: Callable[[BusMessage], None]) -> None:
        with self._lock:
            self._subs.setdefault(namespace, []).append(callback)
            self._hash_to_ns[_ns_hash(namespace)] = namespace   # resolvable on rx

    def subscribe_all(self, callback: Callable[[BusMessage], None]) -> None:
        with self._lock:
            self._all.append(callback)

    def unsubscribe(self, namespace: str, callback: Callable[[BusMessage], None]) -> None:
        with self._lock:
            if callback in self._subs.get(namespace, []):
                self._subs[namespace].remove(callback)

    # -- rx ------------------------------------------------------------------ #
    def _rx_loop(self) -> None:                    # pragma: no cover - hardware path
        while self._started and self._can is not None:
            try:
                frame = self._can.recv(timeout=1.0)
            except Exception as exc:
                logger.warning("CAN rx error: %r", exc)
                continue
            if frame is not None:
                self._on_frame(frame.arbitration_id, bytes(frame.data))

    def _learn_announced(self, name: str) -> str:
        """Install a name a NODE told us about. Refuses a collision, loudly.

        The incumbent keeps the hash for the same reason the bridge's rule says
        so: whatever is already mapped may be in use, and a name we have never
        carried cannot be. An announcement is evidence, not authority."""
        h = _ns_hash(name)
        with self._lock:
            incumbent = self._hash_to_ns.get(h)
            if incumbent is not None and incumbent != name:
                logger.error(
                    "CAN: announced namespace %r hashes to 0x%04X, already held "
                    "by %r — REFUSING the newcomer. Frames carrying 0x%04X keep "
                    "resolving to %r. A 16-bit id collides with ~2.3%% "
                    "probability at 56 namespaces; announcement makes that "
                    "VISIBLE, it does not make it rarer.", name, h, incumbent,
                    h, incumbent)
                return "collision"
            known = incumbent == name
            self._hash_to_ns[h] = name
            self.announced[name] = h
        return "known" if known else "new"

    def _on_frame(self, arb_id: int, data: bytes) -> None:
        ns_hash, mtype, scope, src = decode_arbitration_id(arb_id)
        # THE WELL-KNOWN ROOT IS CONSUMED, NOT DELIVERED ([PLAN G3]). It carries
        # config_chunk frame values, not a bus value: handing `[seq][content]`
        # to a subscriber as though it were a reading would deliver binary
        # fragments under a name that looks like a device state.
        from composeio.ns_announce import NS_ANNOUNCE_HASH
        if ns_hash == NS_ANNOUNCE_HASH:
            self._announce_learner.on_frame(src, bytes(data))
            return
        if mtype is None:
            logger.warning(
                "CAN: frame id 0x%08x carries unallocated message-type code %d "
                "(allocated 0..9, see [BP-TYPE-CODES]) — DROPPED, not guessed.",
                arb_id, (arb_id >> _TYPE_SHIFT) & _TYPE_MASK)
            return
        with self._lock:
            ns = self._hash_to_ns.get(ns_hash)
        if ns is None:
            # A hash we have never seen. CAN carries no namespace STRING, so this
            # is unresolvable by construction — report it rather than invent a
            # name or drop it in silence.
            logger.warning(
                "CAN: frame for unknown namespace hash 0x%04x — no "
                "subscription, prior send, or announcement maps it; ignored. A "
                "node that announces its names on the well-known root makes "
                "this resolvable with no configuration ([PLAN G]); one that "
                "cannot announce stays an irreversible number.", ns_hash)
            return
        # `scope` comes OFF THE WIRE now. It used to be reconstructed as
        # "global" because the layout had no scope field, so every CAN round
        # trip silently relabelled a local message as a global one.
        # `src` names the transmitting node when one has been allocated; it is
        # diagnostic only and never becomes the message's identity.
        source = f"can:{self.channel}"
        if src != NODE_ID_UNASSIGNED:
            source = f"{source}:node{src}"
        msg = BusMessage(namespace=ns, type=mtype, value=self._decode_value(data),
                         source=source, scope=scope)
        with self._lock:
            targets = list(self._subs.get(ns, [])) + list(self._all)
        for cb in targets:
            try:
                cb(msg)
            except Exception as exc:               # one bad subscriber never mutes the rest
                logger.warning("CAN subscriber raised for %s: %r", ns, exc)

    # -- value codec (shared with UART so an MCU speaks ONE dialect) --------- #
    @staticmethod
    def _encode_value(value) -> bytes:
        from composeio.transport_uart import encode_value
        return encode_value(value)

    @staticmethod
    def _decode_value(data: bytes):
        from composeio.transport_uart import decode_value
        return decode_value(data)
