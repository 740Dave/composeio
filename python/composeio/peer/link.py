"""PeerLink — the edge-side of the peer/relay ([BPR-A-CHANNEL], edge leg).

The edge ALWAYS initiates ([BPR-PULL]): the link opens ONE outbound session to
the hub and opens NO listener. It plugs into the edge bus as a standard
Transport ([SPEC:CIO-APP]) — the bus's ``_global`` leg — so read AND write both
ride the single edge-initiated session ([BPR-ONE-CHANNEL], [SCEN-BPR-A-BIDIR]).

Behaviours:
  * classify each edge publish onto a substream (write / crit / ack) and send
    it up; buffer it in capture order when the link is down ([BPR-LINK-DOWN]);
  * on (re)connect: handshake (auth + subscription scope), receive the scoped
    mirror index, then REPLAY the buffered outbound frames in order;
  * dispatch frames arriving from the hub onto the edge bus, stamped relayed
    ([BPR-PROVENANCE]);
  * ``fetch`` resolves a ``bus://`` / ``doc://`` body lazily over the session,
    caching it (last-known-good survives a link-down); an unsatisfiable resolve
    returns an explicit ``stale`` / ``unresolved`` marker ([BPR-MIRROR]);
  * reconnect with exponential backoff (1s x2, cap 30s, +/-20% jitter).

The link is a TRANSPARENT forwarder for critical traffic — it forwards the
``crit`` frame and the returning ``ack`` unchanged and NEVER synthesizes an
``ok`` ([BPR-CRITICAL-ACK]).
"""

from __future__ import annotations

import logging
import random
import socket
import threading
import time
from typing import Any, Callable

from composeio.transport import BusMessage, MessageType, Transport
from composeio.transport_contract import FarSide, TransportProfile
from composeio.peer import frame as F
from composeio.peer import provenance as P
from composeio.peer.buffer import ReplayBuffer
from composeio.peer.mirror import MirrorIndex, Resolution

logger = logging.getLogger("composeio.peer.link")


class PeerLink(Transport):
    """Edge-side peer. A Transport on the edge bus that opens exactly one
    outbound session to a PeerHub and never listens."""

    def __init__(
        self,
        host: str,
        port: int,
        subscriptions: list[str],
        auth_token: str = "",
        peer_id: str = "edge",
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
        jitter: float = 0.2,
        connect_timeout: float = 2.0,
        auto_reconnect: bool = True,
    ):
        # The FAT / BRIDGE pole of the unified Transport contract
        # ([BPR-TRANSPORT-CONTRACT]): bidirectional, critical-ack, far side is a
        # full ComposeBus peer. Read+write ride one edge-initiated session and a
        # ``pri: critical`` message resolves end-to-end as ack-or-fault.
        self.profile = TransportProfile.bridge(far_side=FarSide.FULL_PEER)

        self._host = host
        self._port = port
        self._subs = list(subscriptions)
        self._auth_token = auth_token
        self.peer_id = peer_id
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._jitter = jitter
        self._connect_timeout = connect_timeout
        self._auto_reconnect = auto_reconnect

        self._conn: F.MuxConn | None = None
        self._connected = False
        self._send_lock = threading.Lock()
        self._buffer = ReplayBuffer()
        self.mirror = MirrorIndex(subscriptions)

        self._all_subscribers: list[Callable[[BusMessage], None]] = []
        self._subscribers: dict[str, list] = {}

        self._fetch_waiters: dict[int, dict] = {}
        self._rid = 0

        self._stop = threading.Event()
        self._ready = threading.Event()
        self._auth_rejected = False
        self._closed = threading.Event()
        self._runner: threading.Thread | None = None

    # --- pull-initiated posture proof --------------------------------------

    def listening_sockets(self) -> list:
        """The edge peer opens NO listener ([BPR-PULL]). Always empty — the
        mechanical assertion for [SCEN-BPR-PULL-NO-INBOUND]."""
        return []

    # --- Transport interface ----------------------------------------------

    def send(self, msg: BusMessage) -> None:
        if P.is_relayed(msg):
            return  # loop guard ([BPR-PROVENANCE])
        fr = F.msg_to_frame(msg)
        if fr is None:
            return
        # Edge->master STATE is a 'write' substream; crit/ack keep their ch.
        if fr.get("ch") == F.CH_STATE:
            fr["ch"] = F.CH_WRITE
        with self._send_lock:
            if self._connected and self._conn is not None:
                if not self._conn.send(fr):
                    self._buffer.add(fr)
            else:
                self._buffer.add(fr)  # link-down buffering ([BPR-LINK-DOWN])

    def subscribe(self, namespace: str, callback) -> None:
        self._subscribers.setdefault(namespace, []).append(callback)

    def subscribe_all(self, callback: Callable[[BusMessage], None]) -> None:
        self._all_subscribers.append(callback)

    def unsubscribe(self, namespace: str, callback) -> None:
        if namespace in self._subscribers:
            try:
                self._subscribers[namespace].remove(callback)
            except ValueError:
                pass

    def subscribe_pattern(self, pattern: str) -> None:
        """Widen the subscription scope ([SPEC:CXC-PATSUB])."""
        if pattern not in self._subs:
            self._subs.append(pattern)
            self.mirror.set_subscriptions(self._subs)

    def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = threading.Thread(target=self._run, name="peer-link",
                                        daemon=True)
        self._runner.start()

    def stop(self) -> None:
        self._stop.set()
        conn = self._conn
        if conn is not None:
            conn.close()
        if self._runner is not None:
            self._runner.join(timeout=3.0)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Block until the outbound session is up and the handshake completed."""
        return self._ready.wait(timeout) and self._connected

    @property
    def auth_rejected(self) -> bool:
        return self._auth_rejected

    # --- lazy fetch on resolve ([BPR-MIRROR]) ------------------------------

    def resolve_state(self, namespace: str) -> Resolution:
        return self.mirror.resolve_state(namespace)

    def fetch(self, uri: str, timeout: float = 2.0) -> Resolution:
        """Resolve a ``bus://`` / ``doc://`` body lazily.

        First resolve triggers ONE upstream fetch and caches it; a second is
        served from cache with ZERO upstream fetches; when the link is down a
        cached body is returned (last-known-good) and an uncached URI returns
        an explicit ``stale`` marker — never a fabricated empty value.
        """
        cached = self.mirror.cached_body(uri)
        if cached is not None:
            return Resolution(cached, "cached")
        if not self._connected or self._conn is None:
            return Resolution(None, "stale")
        rid = self._next_rid()
        ev = threading.Event()
        waiter = {"ev": ev, "ok": False, "body": None}
        self._fetch_waiters[rid] = waiter
        self._conn.send({"ch": F.CH_FETCH_REQ, "rid": rid, "uri": uri})
        if not ev.wait(timeout):
            self._fetch_waiters.pop(rid, None)
            return Resolution(None, "unresolved")
        self._fetch_waiters.pop(rid, None)
        if not waiter["ok"] or waiter["body"] is None:
            return Resolution(None, "unresolved")
        body = F.unb64(waiter["body"])
        self.mirror.cache_body(uri, body)
        return Resolution(body, "fetched")

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid

    # --- session loop ------------------------------------------------------

    def _run(self) -> None:
        delay = self._backoff_initial
        while not self._stop.is_set():
            try:
                sock = socket.create_connection(
                    (self._host, self._port), timeout=self._connect_timeout)
            except OSError:
                if not self._auto_reconnect or self._stop.is_set():
                    return
                self._sleep_backoff(delay)
                delay = min(delay * 2, self._backoff_max)
                continue
            self._closed.clear()
            self._ready.clear()
            conn = F.MuxConn(sock, self._on_frame, self._on_close, name="link")
            self._conn = conn
            conn.start()
            conn.send({"ch": F.CH_HELLO, "token": self._auth_token,
                       "subs": self._subs, "peer_id": self.peer_id})
            # Wait for handshake result (hello_ok / hello_reject) or close.
            if self._ready.wait(self._connect_timeout) and self._connected:
                delay = self._backoff_initial  # reset on a good session
                self._closed.wait()            # block until this session drops
            else:
                conn.close()
            self._connected = False
            self._conn = None
            if self._auth_rejected or not self._auto_reconnect or self._stop.is_set():
                return
            self._sleep_backoff(delay)
            delay = min(delay * 2, self._backoff_max)

    def _sleep_backoff(self, delay: float) -> None:
        jitter = 1.0 + random.uniform(-self._jitter, self._jitter)
        self._stop.wait(max(0.0, delay * jitter))

    def _on_frame(self, conn, fr: dict) -> None:
        ch = fr.get("ch")
        if ch == F.CH_HELLO_OK:
            self.mirror.load_index(fr.get("index", {}))
            with self._send_lock:
                self._connected = True
                self._replay_buffer(conn)
            self._ready.set()
        elif ch == F.CH_HELLO_REJECT:
            self._auth_rejected = True
            self._connected = False
            self._ready.set()
        elif ch in (F.CH_STATE, F.CH_CRIT):
            self._deliver(fr)
        elif ch == F.CH_ACK:
            self._deliver_ack(fr)
        elif ch == F.CH_MIRROR_IDX:
            self.mirror.update(fr.get("ns"), fr.get("val"))
        elif ch == F.CH_FETCH_RES:
            w = self._fetch_waiters.get(fr.get("rid"))
            if w is not None:
                w["ok"] = fr.get("ok", False)
                w["body"] = fr.get("body")
                w["ev"].set()

    def _replay_buffer(self, conn) -> None:
        """Replay buffered outbound frames in capture order on reconnect."""
        for fr in self._buffer.drain():
            conn.send(fr)

    def _on_close(self, conn) -> None:
        self._connected = False
        self._closed.set()
        self._ready.set()

    # --- dispatch onto the edge bus ----------------------------------------

    def _deliver(self, fr: dict) -> None:
        ns = fr.get("ns")
        val = fr.get("val")
        self.mirror.update(ns, val)
        data = P.stamp({}, self.peer_id)
        if fr.get("ch") == F.CH_CRIT:
            data["_priority"] = "critical"
            data["_cid"] = fr.get("cid")
        src = P.prefixed_source(self.peer_id, fr.get("src", ""))
        msg = BusMessage(namespace=ns, type=MessageType.STATE, value=val,
                         source=src, scope="global", data=data)
        for cb in list(self._all_subscribers):
            cb(msg)

    def _deliver_ack(self, fr: dict) -> None:
        data = P.stamp({"_cid": fr.get("cid")}, self.peer_id)
        msg = BusMessage(namespace=fr.get("ns"), type=MessageType.ACK,
                         value=fr.get("val"),
                         source=P.prefixed_source(self.peer_id, fr.get("src", "")),
                         scope="global", data=data)
        for cb in list(self._all_subscribers):
            cb(msg)
