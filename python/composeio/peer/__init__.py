"""ComposeBus PEER/RELAY primitive — Slice A ([SPEC:CIO-BUS-PEER]).

A pull-initiated, multiplexed, segment-crossing bus peer. The isolated/edge
side ALWAYS dials out ([BPR-PULL]); one session multiplexes read/write/
mirror-sync/fetch/critical-ack ([BPR-ONE-CHANNEL]); a ``pri: critical`` message
resolves end-to-end as a genuine ack OR an explicit ``_bus/fault`` degrade,
never silent-loss ([BPR-CRITICAL-ACK]); the peer mirrors only its subscription
scope and fetches bodies lazily ([BPR-MIRROR]); it tolerates the link being
down and converges on reconnect ([BPR-LINK-DOWN]); relayed traffic carries
provenance and cannot silently actuate ([BPR-PROVENANCE]).

Slice A (network-segment edge over Tailscale) and Slice B (MQTT<->CANbus bridge)
are both built. Slice B is a CONSUMER of the Slice A core: ``CanBridge`` plugs
into the local bus as the same ``_global`` Transport a ``PeerHub`` /
``PeerLink`` does, mapping a scoped namespace slice to 29-bit CAN arbitration
IDs ([BPR-B-CANMAP]), segmenting large payloads ISO-TP-style ([BPR-B-FRAG]),
and synthesizing-or-degrading QoS/retained/critical-ack on the CAN side
([BPR-B-SYNTH]). It is tested against a pure-Python mock CAN interface
([DD-BPR-MOCK-CAN] alternative b). The existing ``coordination_relay``
(transport_http_relay.py) is left intact — the replace-vs-wrap disposition is
the open decision [DD-BPR-RELAY-WRAP].

Reuse-by-reference bindings ([BPR-REUSE]):
  * capture-replay-across-a-link -> buffer.py ([AMEND:BP-CAPTURE-REPLAY])
  * critical-ack cid/retry/_bus/fault -> critical.py ([AMEND:BP-ACK])
  * trailing-glob prefix subscription -> mirror.py ([SPEC:CXC-PATSUB])
  * BusMessage + Transport interface -> hub.py / link.py ([SPEC:CIO-APP])
"""

from composeio.transport_contract import (
    Directionality,
    FarSide,
    QoS,
    TransportContract,
    TransportProfile,
)
from composeio.peer.hub import PeerHub
from composeio.peer.link import PeerLink
from composeio.peer.critical import CriticalAckOrigin, CriticalAcker
from composeio.peer.provenance import ActuationGuard
from composeio.peer.mirror import Resolution
# Slice B — MQTT<->CANbus bridge edge.
from composeio.peer.canmap import CanIdRegistry, MappingMiss, admits
from composeio.peer.frag import fragment, Reassembler
from composeio.peer.mockcan import MockCanBus, MockCanNode, MockCanEcu
from composeio.peer.canbridge import CanBridge
# Cross-tier CAN — foreign-vehicle ingestion ([SPEC:CXC-CAN], Phase 1).
from composeio.peer.socketcan import (
    CanReader, SocketCanReader, MockCanReader, open_can,
    parse_can_frame, pack_can_frame, make_can_id,
    CAN_EFF_FLAG, CAN_RTR_FLAG, CAN_ERR_FLAG, CAN_EFF_MASK, CAN_SFF_MASK,
)
from composeio.peer.candecode import (
    DecoderMap, SignalEntry, DecoderMapError, CanIngest,
    extract_bits, raw_namespace,
)

__all__ = [
    # Unified Transport contract ([BPR-TRANSPORT-CONTRACT]).
    "TransportContract",
    "TransportProfile",
    "QoS",
    "Directionality",
    "FarSide",
    "PeerHub",
    "PeerLink",
    "CriticalAckOrigin",
    "CriticalAcker",
    "ActuationGuard",
    "Resolution",
    # Slice B
    "CanIdRegistry",
    "MappingMiss",
    "admits",
    "fragment",
    "Reassembler",
    "MockCanBus",
    "MockCanNode",
    "MockCanEcu",
    "CanBridge",
    # Cross-tier CAN — Phase 1 ([SPEC:CXC-CAN]).
    "CanReader",
    "SocketCanReader",
    "MockCanReader",
    "open_can",
    "parse_can_frame",
    "pack_can_frame",
    "make_can_id",
    "CAN_EFF_FLAG",
    "CAN_RTR_FLAG",
    "CAN_ERR_FLAG",
    "CAN_EFF_MASK",
    "CAN_SFF_MASK",
    "DecoderMap",
    "SignalEntry",
    "DecoderMapError",
    "CanIngest",
    "extract_bits",
    "raw_namespace",
]
