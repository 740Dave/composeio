"""Provenance stamping + actuation quarantine ([BPR-PROVENANCE]).

Relayed/mirrored traffic is, from the far bus's view, indistinguishable from
live local traffic — the same hazard [AMEND:BP-CAPTURE-REPLAY] closes for
replay. A peer therefore stamps every relayed message with an authoritative
provenance marker (``relayed: True`` + a ``peer:<id>/`` source prefix), and
actuation-capable consumers refuse relayed input unless explicitly opted in
([CR-SAFETY-2]).

The stamp doubles as the peer's LOOP GUARD: a transport never re-forwards a
message it can see is already relayed (the [APP-C7] feedback-guard idea, with
the provenance marker doing the bookkeeping for free).
"""

from __future__ import annotations

from composeio.transport import BusMessage, MessageType


def stamp(data: dict, peer_id: str) -> dict:
    """Return a copy of ``data`` stamped as relayed by ``peer_id``."""
    out = dict(data or {})
    out["relayed"] = True
    out["peer"] = peer_id
    return out


def prefixed_source(peer_id: str, original: str) -> str:
    return f"peer:{peer_id}/{original}"


def is_relayed(msg: BusMessage) -> bool:
    return bool(msg.data.get("relayed"))


class ActuationGuard:
    """Refuses to drive an actuator from peer-relayed control input unless the
    namespace is explicitly opted in ([BPR-PROVENANCE] / [CR-SAFETY-2]).

    Operates at the message level (via ``bus.on_message``) because the flat
    ``(ns, new, old)`` state-subscription signature cannot carry provenance —
    the ``relayed`` marker lives on the ``BusMessage.data``.
    """

    def __init__(self, bus):
        self._bus = bus
        self._actuators: dict[str, callable] = {}
        self._opt_in: set[str] = set()
        self.refused: list[tuple[str, str]] = []  # (namespace, source)
        self.fired: list[tuple[str, object]] = []  # (namespace, value)
        bus.on_message(self._on_msg)

    def protect(self, namespace: str, actuate) -> None:
        """Register an actuator callback for ``namespace``. It fires on live
        local input, and on relayed input ONLY if the namespace is opted in."""
        self._actuators[namespace] = actuate

    def opt_in(self, namespace: str) -> None:
        """Explicitly accept relayed actuation for a namespace."""
        self._opt_in.add(namespace)

    def _on_msg(self, msg) -> None:
        if msg.type != MessageType.STATE:
            return
        actuate = self._actuators.get(msg.namespace)
        if actuate is None:
            return
        if is_relayed(msg) and msg.namespace not in self._opt_in:
            self.refused.append((msg.namespace, msg.source))
            return
        self.fired.append((msg.namespace, msg.value))
        actuate(msg.namespace, msg.value)

    def close(self) -> None:
        try:
            self._bus.off_message(self._on_msg)
        except Exception:
            pass
