"""The ONE unified Transport-emitter contract ([BPR-TRANSPORT-CONTRACT]).

There are not two forked emitters and not one monolith — there is ONE
``Transport`` waist and THREE declared knobs that place any given emitter on a
thin-to-fat spectrum:

  * **QoS / reliability** — ``QoS.CRITICAL_ACK`` (propagate-or-explicit-degrade,
    the correctness bar [BPR-CRITICAL-ACK]) at the reliable pole, vs
    ``QoS.FIRE_AND_FORGET`` (best-effort, no ack machinery) at the cheap pole.
  * **directionality** — ``Directionality.BIDIRECTIONAL`` (a bridge: ComposeBus
    <-> another bus substrate) vs ``Directionality.EGRESS`` (one-way: ComposeBus
    -> a non-bus sink).
  * **far-side type** — ``FarSide.FULL_PEER`` (another ComposeBus, full
    fidelity) / ``FarSide.MAPPED_DEVICE_BUS`` (CAN — semantics mapped or
    synthesized-or-degraded) / ``FarSide.DUMB_SINK`` (a POST endpoint, a
    telemetry collector — no bus semantics on the far side).

The two poles this contract unifies:

  * the **BRIDGE** pole — the FAT peer (``PeerHub`` / ``PeerLink`` [full peer],
    ``CanBridge`` [mapped device bus]): bidirectional, critical-ack, with
    handshake + subscription-scoped mirror + capture/replay. The CAN bridge
    (Slice B) is the first real test of this pole.
  * the **EGRESS** pole — the THIN adaptor (``HttpRelayTransport``): a
    fire-and-forget outbound POST to a dumb sink. Telemetry is the exemplar.

The unification is the INTERFACE; the implementations stay honestly
thin-vs-fat. The narrow-waist rule ([DX-NARROW-WAIST]) plus
cheapest-node-that-fully-owns-the-behavior means we do NOT force trivial
telemetry through the peer's handshake/mirror/ack machinery — the egress pole
declares ``QoS.FIRE_AND_FORGET`` and lowers to the SAME ``Transport`` waist
(``send`` / ``subscribe_all`` / ``start`` / ``stop`` / ``is_connected``) that
the bridge pole does.

This module adds NO new invariant that contradicts ``bus-peer-relay.lts`` §2 —
it REFINES/UNIFIES the existing peer shape. The critical-ack protocol
([AMEND:BP-ACK]) and the bus protocol ([SPEC:CIO-BUS-PROTOCOL]) are REUSED, not
restated: this module only declares WHERE on the spectrum an emitter sits.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable


class QoS(Enum):
    """Reliability knob — the delivery guarantee an emitter offers."""

    FIRE_AND_FORGET = "fire_and_forget"
    """Best-effort; no ack, no retry, no delivery truth. The egress pole. A
    ``pri: critical`` message carries no extra weight here — the emitter never
    opens an ack-pending entry and never invokes the ack machinery."""

    CRITICAL_ACK = "critical_ack"
    """Propagate-or-explicit-degrade for ``pri: critical`` traffic
    ([BPR-CRITICAL-ACK]): the emitter forwards the critical frame + its
    returning ack transparently and NEVER synthesizes an ``ok`` — the origin's
    ack-pending table is the single source of delivery truth. The bridge pole."""


class Directionality(Enum):
    """Directionality knob — which way bus state flows across the seam."""

    EGRESS = "egress"
    """One-way: ComposeBus -> a non-bus sink. No inbound path; the far side
    never publishes back onto the bus."""

    BIDIRECTIONAL = "bidirectional"
    """Two-way: ComposeBus <-> another bus substrate. Reads and writes both
    ride the seam ([SCEN-BPR-A-BIDIR])."""


class FarSide(Enum):
    """Far-side-type knob — what lives on the other end of the seam and how much
    bus semantics it natively carries."""

    FULL_PEER = "full_peer"
    """Another ComposeBus — full fidelity (QoS/retained/critical-ack preserved
    1:1). MQTT<->MQTT segment relay ([BPR-A-SEGMENT])."""

    MAPPED_DEVICE_BUS = "mapped_device_bus"
    """A physical/device bus (CAN) — no native QoS/retained/LWT; the bridge maps
    namespaces to arbitration IDs and synthesizes-or-degrades the missing
    semantics ([BPR-B-CANMAP], [BPR-B-SYNTH])."""

    DUMB_SINK = "dumb_sink"
    """A non-bus endpoint — a telemetry collector, a monitoring POST target. No
    bus semantics on the far side; nothing to preserve, nothing to ack."""


@dataclass(frozen=True)
class TransportProfile:
    """The three knobs, declared. An emitter exposes its profile so the platform
    (and tests) can read WHERE on the thin-to-fat spectrum it sits WITHOUT
    reaching into its implementation.

    Invariant coupling the knobs (the honest thin-vs-fat rule): a
    ``FIRE_AND_FORGET`` emitter MUST be an egress-to-dumb-sink adaptor — it has
    no far-side bus to ack against and no inbound path. ``CRITICAL_ACK`` is
    reserved for a bidirectional bridge whose far side can actually acknowledge
    (a peer bus) or against which an ack can be synthesized-or-degraded from real
    evidence (a device bus). This is validated in ``__post_init__`` so a
    mis-declared profile is a construction error, not a latent runtime fault.
    """

    qos: QoS
    directionality: Directionality
    far_side: FarSide

    def __post_init__(self) -> None:
        if self.qos is QoS.CRITICAL_ACK and self.directionality is Directionality.EGRESS:
            raise ValueError(
                "critical-ack requires a bidirectional bridge: a one-way egress "
                "emitter has no return path to carry an ack, so it cannot honor "
                "[BPR-CRITICAL-ACK] — declare QoS.FIRE_AND_FORGET for egress")
        if self.qos is QoS.CRITICAL_ACK and self.far_side is FarSide.DUMB_SINK:
            raise ValueError(
                "critical-ack requires a far side that can acknowledge (a peer "
                "bus) or against which an ack can be synthesized-or-degraded (a "
                "device bus); a dumb sink cannot ack — declare "
                "QoS.FIRE_AND_FORGET")

    # --- read-the-spectrum helpers ----------------------------------------

    @property
    def is_bridge(self) -> bool:
        """A bridge: bidirectional onto another bus substrate (the fat pole)."""
        return self.directionality is Directionality.BIDIRECTIONAL

    @property
    def is_egress(self) -> bool:
        """An egress: one-way onto a non-bus sink (the thin pole)."""
        return self.directionality is Directionality.EGRESS

    @property
    def preserves_critical_ack(self) -> bool:
        """True iff this emitter carries [BPR-CRITICAL-ACK] end-to-end. When
        False the emitter is fire-and-forget and MUST NOT be routed
        ``pri: critical`` traffic that expects a delivery guarantee."""
        return self.qos is QoS.CRITICAL_ACK

    # --- pole constructors (the two exemplars) ----------------------------

    @classmethod
    def egress(cls, far_side: FarSide = FarSide.DUMB_SINK) -> "TransportProfile":
        """The EGRESS pole — fire-and-forget, one-way. Telemetry is the
        exemplar (``far_side=DUMB_SINK``)."""
        return cls(qos=QoS.FIRE_AND_FORGET,
                   directionality=Directionality.EGRESS,
                   far_side=far_side)

    @classmethod
    def bridge(cls, far_side: FarSide = FarSide.FULL_PEER) -> "TransportProfile":
        """The BRIDGE pole — critical-ack, bidirectional. A full peer
        (``FULL_PEER``) or a mapped device bus (``MAPPED_DEVICE_BUS``, CAN)."""
        return cls(qos=QoS.CRITICAL_ACK,
                   directionality=Directionality.BIDIRECTIONAL,
                   far_side=far_side)


@runtime_checkable
class TransportContract(Protocol):
    """The unified Transport-emitter contract: the minimal method waist BOTH
    poles satisfy, PLUS a declared ``profile``.

    Runtime-checkable so a test can assert ``isinstance(emitter,
    TransportContract)`` for both the thin adaptor and the fat peer — the
    machine proof that ONE contract spans the spectrum. The method waist here is
    exactly the load-bearing subset of ``composeio.transport.Transport`` that
    both poles implement; a concrete emitter is free to carry the full
    ``Transport`` ABC (subscribe/unsubscribe/subscribe_pattern) on top.
    """

    profile: TransportProfile

    def send(self, message: Any) -> Any: ...

    def subscribe_all(self, callback: Callable[[Any], None]) -> Any: ...

    def start(self) -> Any: ...

    def stop(self) -> Any: ...

    @property
    def is_connected(self) -> bool: ...


__all__ = [
    "QoS",
    "Directionality",
    "FarSide",
    "TransportProfile",
    "TransportContract",
]
