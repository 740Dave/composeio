"""connector_replay — retained-state replay + egress rules for LISTEN connectors.

GENERALIZED to the connector layer ([CXC-WS-RETAINED-REPLAY]): MQTT gets current
state free from the broker's native retain; NO other substrate has an equivalent,
so a non-broker LISTEN connector (WS today, a UART/CAN LISTEN connector tomorrow)
must replay explicitly or a late subscriber renders BLANK until the next publish.

This lives OUTSIDE transport_ws.py on purpose: a future UART/CAN LISTEN connector
calls ``replay_retained`` here rather than importing from a WS-specific file, and
neither the Transport ABC nor the DIAL transports are touched (their tests stay
green). ``transport_ws.py`` re-exports these for its own use.
"""
from __future__ import annotations

from typing import Callable

from composeio.bus import pattern_matches
from composeio.freshness import is_unknown
from composeio.namespace import Scope
from composeio.transport import BusMessage, MessageType


def replay_retained(bus, patterns: list[str], deliver: Callable[[dict], None]) -> int:
    """Deliver each currently-valued namespace matching `patterns` ONCE.

    Enumerates `bus.namespaces` (the same registry `subscribe_pattern` consults)
    and, for every namespace whose name matches a declared pattern through the
    SAME `pattern_matches` helper, carries a believable current value with
    `retained: True` on the wire dict. Honors default-deny (never replays an
    undeclared namespace) and scope (never replays an explicit `scope: local`
    namespace across the membrane) — [CXC-WS-RETAINED-REPLAY]'s two negative
    controls. Returns the number of namespaces replayed.

    `retained` is a receive-side annotation `BusMessage.to_json()` deliberately
    does NOT serialise (it is set from MQTT's retain FLAG on receipt, not carried
    in JSON). For a non-broker substrate the flag has no transport of its own, so
    it is added EXPLICITLY to the wire dict here — additively, the envelope is
    otherwise byte-identical to `to_json()`. `transport.py` is not touched.
    """
    replayed = 0
    for name, ns in bus.namespaces.items():
        if not any(pattern_matches(p, name) for p in patterns):
            continue
        if not egress_allowed(bus, name):
            continue
        value = ns.value
        if value is None or is_unknown(value):
            continue
        msg = BusMessage(
            namespace=name,
            type=MessageType.STATE,
            value=value,
            scope=wire_scope(bus, ns),
            retained=True,
        )
        wire = {**msg.to_json(), "retained": True}
        deliver(wire)
        replayed += 1
    return replayed


def egress_allowed(bus, namespace: str) -> bool:
    """Whether a namespace may cross the connector membrane to a peer.

    A membrane-crossing peer is like MQTT's global leg — so an EXPLICIT
    `scope: local` namespace (deliberate confinement) never reaches it, mirroring
    [CXC-SUB-SCOPE]'s publish-path enforcement. Everything else (GLOBAL/REMOTE/
    AUTO, or an unknown external namespace) is allowed. The check is on the
    namespace's DECLARED scope, not the topology-resolved wire scope, so an AUTO
    namespace on a broker-less tenant bus is not accidentally hidden.
    """
    ns = bus.get_namespace(namespace)
    if ns is None:
        return True
    return ns.scope != Scope.LOCAL


def wire_scope(bus, ns) -> str:
    """The scope string a live publish of `ns` would carry, reused unchanged."""
    return bus._resolve_scope(ns)
