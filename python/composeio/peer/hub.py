"""PeerHub — the master-side of the peer/relay ([BPR-A-SEGMENT], master leg).

The hub LISTENS (the master side is not the isolated zone, so it may accept);
the edge always DIALS OUT ([BPR-PULL]). The hub plugs into the master bus as a
standard Transport ([SPEC:CIO-APP]) — it is the bus's ``_global`` leg — so:

  * every master publish arrives at ``hub.send`` and is fanned DOWN to each
    authed session whose subscription scope matches ([BPR-MIRROR] scoping);
  * frames arriving from a session are dispatched onto the master bus via the
    wildcard subscriber that ``bus._wire_transport_incoming`` registered, i.e.
    "dispatched identically to live traffic".

The hub is a TRANSPARENT forwarder for critical traffic: it fans the ``crit``
frame down and the returning ``ack`` frame back — it NEVER synthesizes an
``ok`` ([BPR-CRITICAL-ACK]).

Fault injection ([BPR-A-CRITICAL] test hook): ``inject_drop`` / ``sever_all``
model a dropped hop without hardware.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any, Callable

from composeio.bus import pattern_matches
from composeio.transport import BusMessage, MessageType, Transport
from composeio.transport_contract import FarSide, TransportProfile
from composeio.peer import frame as F
from composeio.peer import provenance as P

logger = logging.getLogger("composeio.peer.hub")


class _HubSession:
    """One accepted edge session: auth handshake, subscription scope, and the
    bidirectional bridge to the master bus."""

    def __init__(self, hub: "PeerHub", sock):
        self.hub = hub
        self.conn = F.MuxConn(sock, self._on_frame, self._on_close, name="hub-sess")
        self.authed = False
        self.subs: list[str] = []
        self._fetch_lock = threading.Lock()

    def start(self) -> None:
        self.conn.start()

    def matches(self, namespace: str) -> bool:
        for pat in self.subs:
            if pattern_matches(pat, namespace):
                return True
        return False

    def send(self, frame: dict) -> bool:
        return self.conn.send(frame)

    def _on_frame(self, conn, fr: dict) -> None:
        ch = fr.get("ch")
        if ch == F.CH_HELLO:
            self._handle_hello(fr)
            return
        if not self.authed:
            return  # ignore anything before a good handshake ([BPR-AUTH])
        if ch in (F.CH_WRITE, F.CH_STATE, F.CH_CRIT, F.CH_ACK):
            self.hub._dispatch_incoming(fr)
        elif ch == F.CH_FETCH_REQ:
            self._handle_fetch(fr)

    def _handle_hello(self, fr: dict) -> None:
        token = fr.get("token", "")
        if self.hub.auth_token and token != self.hub.auth_token:
            # Reject at connect: NO partial sync, NO mirror ([BPR-AUTH]).
            logger.warning("hub: peer rejected — auth token mismatch")
            self.conn.send({"ch": F.CH_HELLO_REJECT, "reason": "auth"})
            self.authed = False
            self.conn.drain_and_close()  # flush the reject, THEN close
            return
        self.subs = list(fr.get("subs", []))
        self.authed = True
        index = self.hub._scoped_index(self.subs)
        self.conn.send({"ch": F.CH_HELLO_OK, "index": index})
        logger.info(f"hub: peer authed, scope={self.subs}")

    def _handle_fetch(self, fr: dict) -> None:
        rid = fr.get("rid")
        uri = fr.get("uri", "")
        body = None
        try:
            body = self.hub.resolver(uri) if self.hub.resolver else None
        except Exception as e:
            logger.debug(f"hub fetch failed for {uri}: {e}")
            body = None
        self.conn.send({
            "ch": F.CH_FETCH_RES, "rid": rid, "uri": uri,
            "ok": body is not None, "body": F.b64(body),
        })

    def _on_close(self, conn) -> None:
        self.hub._drop_session(self)


class PeerHub(Transport):
    """Master-side peer. A Transport on the master bus that accepts edge
    sessions and bridges scoped bus state across the seam."""

    def __init__(
        self,
        auth_token: str = "",
        resolver: Callable[[str], bytes | None] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        peer_id: str = "hub",
    ):
        # The FAT / BRIDGE pole ([BPR-TRANSPORT-CONTRACT]): bidirectional,
        # critical-ack, far side is a full ComposeBus peer. The hub is a
        # transparent forwarder for critical traffic — it fans the crit frame
        # down and the ack back, never synthesizing an ``ok``.
        self.profile = TransportProfile.bridge(far_side=FarSide.FULL_PEER)

        self.auth_token = auth_token
        self.resolver = resolver
        self._host = host
        self._req_port = port
        self.peer_id = peer_id

        self._server: socket.socket | None = None
        self._port = port
        self._sessions: list[_HubSession] = []
        self._sess_lock = threading.Lock()
        self._all_subscribers: list[Callable[[BusMessage], None]] = []
        self._running = False
        self._bus = None

        # Fault-injection state ([BPR-A-CRITICAL] test hook).
        self._drop_channels: set[str] = set()
        self._drop_until: float = 0.0

    def bind_bus(self, bus) -> None:
        """Give the hub read access to the master bus for the initial mirror
        index (current values of scoped namespaces)."""
        self._bus = bus

    @property
    def port(self) -> int:
        return self._port

    # --- fault injection (test hooks) --------------------------------------

    def inject_drop(self, channels: set[str], seconds: float) -> None:
        """Drop frames on the named substreams for a window — models a hop that
        loses (e.g.) the returning ack mid-flight ([SCEN-BPR-CRIT-DROP])."""
        self._drop_channels = set(channels)
        self._drop_until = time.monotonic() + seconds

    def clear_drop(self) -> None:
        self._drop_channels = set()
        self._drop_until = 0.0

    def sever_all(self) -> None:
        """Close every session — a full link sever ([SCEN-BPR-LINKDOWN-CONVERGE])."""
        with self._sess_lock:
            sessions = list(self._sessions)
        for s in sessions:
            s.conn.close()

    def _dropping(self, ch: str) -> bool:
        if not self._drop_channels:
            return False
        if time.monotonic() >= self._drop_until:
            self.clear_drop()
            return False
        return ch in self._drop_channels

    # --- Transport interface ----------------------------------------------

    def send(self, msg: BusMessage) -> None:
        # Loop guard ([BPR-PROVENANCE]): never re-forward relayed traffic.
        if P.is_relayed(msg):
            return
        fr = F.msg_to_frame(msg)
        if fr is None:
            return
        if self._dropping(fr.get("ch")):
            return  # fault injection: this frame is lost on the hop
        ns = msg.namespace
        with self._sess_lock:
            sessions = list(self._sessions)
        for s in sessions:
            if s.authed and s.matches(ns):
                s.send(fr)

    def subscribe(self, namespace: str, callback) -> None:
        return None

    def subscribe_all(self, callback: Callable[[BusMessage], None]) -> None:
        self._all_subscribers.append(callback)

    def unsubscribe(self, namespace: str, callback) -> None:
        return None

    def start(self) -> None:
        if self._running:
            return
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._host, self._req_port))
        srv.listen(8)
        self._port = srv.getsockname()[1]
        self._server = srv
        self._running = True
        threading.Thread(target=self._accept_loop, name="hub-accept",
                         daemon=True).start()
        logger.info(f"PeerHub listening on {self._host}:{self._port}")

    def stop(self) -> None:
        self._running = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        self.sever_all()

    @property
    def is_connected(self) -> bool:
        return self._running

    # --- internals ---------------------------------------------------------

    def _accept_loop(self) -> None:
        while self._running and self._server is not None:
            try:
                sock, _ = self._server.accept()
            except OSError:
                break
            sess = _HubSession(self, sock)
            with self._sess_lock:
                self._sessions.append(sess)
            sess.start()

    def _drop_session(self, sess: _HubSession) -> None:
        with self._sess_lock:
            if sess in self._sessions:
                self._sessions.remove(sess)

    def _scoped_index(self, subs: list[str]) -> dict[str, Any]:
        if self._bus is None:
            return {}
        index: dict[str, Any] = {}
        for ns_name, ns_obj in self._bus.namespaces.items():
            if ns_name == "_bus/time":
                continue
            for pat in subs:
                if pattern_matches(pat, ns_name):
                    index[ns_name] = ns_obj.value
                    break
        return index

    def _dispatch_incoming(self, fr: dict) -> None:
        """Turn an inbound edge frame into a BusMessage and dispatch it onto the
        master bus, stamped with provenance ([BPR-PROVENANCE])."""
        ns = fr.get("ns")
        src = P.prefixed_source(self.peer_id, fr.get("src", ""))
        data = P.stamp({}, self.peer_id)
        ch = fr.get("ch")
        if ch == F.CH_ACK:
            data["_cid"] = fr.get("cid")
            msg = BusMessage(namespace=ns, type=MessageType.ACK, value=fr.get("val"),
                             source=src, scope="global", data=data)
        else:
            if ch == F.CH_CRIT:
                data["_priority"] = "critical"
                data["_cid"] = fr.get("cid")
            msg = BusMessage(namespace=ns, type=MessageType.STATE, value=fr.get("val"),
                             source=src, scope="global", data=data)
        for cb in list(self._all_subscribers):
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"hub dispatch error: {e}")
