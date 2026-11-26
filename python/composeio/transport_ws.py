"""transport_ws — the LISTEN-role connector that makes a browser a live bus peer.

[SPEC:CXC-WS] (`88-composebus-ws-endpoint.lts`). This is the one transport-family
connector that INVERTS the socket direction. Every other registered factory
(`mqtt`/`can`/`uart`) DIALS a fabric that already exists — a broker, a CAN
interface, a serial port. There is no broker between a browser tab and the
kernel, so this factory OPENS A LISTEN SOCKET and each accepted connection
becomes one singleton persistent peer ([CXC-WS-SINGLETON-PEER]). The
declared-config idiom is unchanged (`type: ws`, a `spec` dict, a built object
handed back); only the socket's DIRECTION inverts.

WHAT THIS MODULE OWNS (its entire net-new surface, per [CXC-WS-CONNECTOR-INTERFACE]):
  * the accept loop (`WSConnector`, an asyncio server on a background thread);
  * per-connection peer state and the fixed lifecycle
    (PRE-BINDING -> BOUND -> SWITCH), `WSPeer`;
  * the deep-link designator parse ([DD-WS-DEEPLINK-URL], OPEN — candidate (a)
    `/bus/<tenant_id>` is the implementable DEFAULT, not a ratified shape);
  * the replay-trigger wiring around an otherwise-unmodified subscription.

WHAT IT REUSES BY REFERENCE (never reimplements):
  * `BusMessage.to_json()`/`from_json()` verbatim — the faithful wire envelope
    (`ns`/`type`/`src`/`ts`/`scope`/`seq` + optional `val`/`evt`/`data`),
    encode/decode ONLY, no transform ([CXC-WS-WIRE-FORMAT]);
  * `bus.py`'s `is_pattern`/`pattern_matches`/`validate_pattern` for the
    explicit + default-deny subscription filter ([CXC-WS-SUBSCRIPTION],
    reusing [SPEC:CXC-SUBFILTER]);
  * `ComposeServer.tenant_bus()`/`tenants()` for per-tenant resolution and the
    kernel-served cross-tenant list ([CXC-WS-TENANT-BOUNDARY]) — injected as a
    `TenantRegistry`, never rebuilt or cached per connection.

THE KERNEL SEAM. A LISTEN connector spans tenants, so unlike a DIAL transport it
cannot be attached to ONE bus. It is built by the registry factory
(`_ws(spec, app_id) -> WSConnector`, so `known_types()` gains `ws`) and then
handed the server's `TenantRegistry` at the kernel level via `bind_registry()`.
The cross-tenant bus list is the KERNEL's surface — this connector serves SOME
list from the registry in PRE-BINDING and honors a selection; it never designs
the operator console ([CXC-WS-OUT-OF-SCOPE]).

v1: NO auth, NO identity persistence ([DD-WS-AUTH-GATE], [CXC-WS-IDENTITY-POLICY],
both deferred by David's ruling). No login gate, no localStorage/NVS read on the
connect path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from composeio.bus import is_pattern, pattern_matches, validate_pattern
from composeio.connector_replay import egress_allowed as _egress_allowed
from composeio.connector_replay import replay_retained, wire_scope as _wire_scope
from composeio.transport import BusMessage, MessageType

logger = logging.getLogger(__name__)

# replay_retained / _egress_allowed / _wire_scope are CONNECTOR-GENERIC and now
# live in composeio.connector_replay ([CXC-WS-RETAINED-REPLAY]) so a future
# UART/CAN LISTEN connector reuses them without importing this WS-specific file.
# They are imported (and re-exported) here for the WS connector's own use and for
# backward-compatible callers/tests that reference them from transport_ws.


# --------------------------------------------------------------------------- #
# The kernel seam: what a WS connector needs from the ComposeServer/kernel.
# ComposeServer satisfies this duck-typed (tenant_bus / tenants). Injected, not
# rebuilt — [CXC-WS-TENANT-BOUNDARY]: the cross-tenant list is the KERNEL's
# surface, never a per-connection cache.
# --------------------------------------------------------------------------- #
@runtime_checkable
class TenantRegistry(Protocol):
    def tenant_bus(self, tenant_id: str) -> Any: ...
    def tenants(self) -> list[str]: ...


class UnknownTenantError(ValueError):
    """A deep-link or a selection named a tenant the registry does not serve.

    Deliberately loud — [CXC-WS-ENTRY-DEEPLINK]/[CXC-WS-BOUNDARIES] row 6 forbid
    a silent fallback to the operator list or a default tenant.
    """


# --------------------------------------------------------------------------- #
# WSPeer — one singleton persistent peer per accepted connection.
#
# Deliberately socket-free: it owns lifecycle/subscription/replay LOGIC and
# emits frames through an injected `send_frame` sink, so the whole of
# [CXC-WS-LIFECYCLE]/[CXC-WS-SUBSCRIPTION]/[CXC-WS-RETAINED-REPLAY]/
# [CXC-WS-TENANT-BOUNDARY] is unit-testable with no asyncio and no flaky
# sockets. `WSConnector` binds `send_frame` to a real socket's outbound queue.
# --------------------------------------------------------------------------- #
PRE_BINDING = "PRE-BINDING"
BOUND = "BOUND"


class WSPeer:
    def __init__(
        self,
        peer_id: str,
        registry: TenantRegistry,
        send_frame: Callable[[dict], None],
    ):
        self.peer_id = peer_id
        self._registry = registry
        self._send_frame = send_frame
        self.state: str = PRE_BINDING
        self.bound_tenant: Optional[str] = None
        self.declared_patterns: list[str] = []
        # v1: ephemeral per-connection identity ONLY, for logging/debug. NOT
        # persisted, NOT required stable across reconnects ([CXC-WS-IDENTITY-
        # POLICY], deferred to [DD-WS-AUTH-GATE]). No localStorage/NVS read.
        self.identity: Optional[str] = peer_id
        self._bus = None
        self._on_message = None  # the bus.on_message callback while BOUND

    # -- outbound helpers ------------------------------------------------- #
    def _emit(self, frame: dict) -> None:
        try:
            self._send_frame(frame)
        except Exception as exc:  # a dead socket must not crash bus dispatch
            logger.debug("peer %s send failed: %s", self.peer_id, exc)

    def _error(self, code: str, message: str) -> None:
        self._emit({"ws": "error", "code": code, "message": message})

    # -- entry: operator list -------------------------------------------- #
    def serve_tenant_list(self) -> None:
        """PRE-BINDING operator entry ([CXC-WS-ENTRY-OPERATOR]): serve the
        KERNEL's cross-tenant list, freshly, never a per-connection cache."""
        self._emit({"ws": "tenants", "tenants": list(self._registry.tenants())})

    # -- lifecycle -------------------------------------------------------- #
    def bind(self, tenant_id: str) -> None:
        """PRE-BINDING -> BOUND(tenant_id), the ordered [CXC-WS-LIFECYCLE] entry:
        (1) resolve the tenant bus (loudly if unknown), (2) apply the declared
        subscription, (3) retained-replay every newly-matched namespace —
        replay AFTER subscribe, never before."""
        bus = self._resolve_or_raise(tenant_id)
        self.bound_tenant = tenant_id
        self._bus = bus
        self.state = BOUND
        self._install_subscription()
        self._emit({"ws": "bound", "tenant": tenant_id})
        # (3) replay AFTER the subscription is live.
        self._replay(self.declared_patterns)

    def switch(self, tenant_id: str) -> None:
        """BOUND(A) -> BOUND(B) THROUGH an observable PRE-BINDING
        ([CXC-WS-LIFECYCLE]): (1) clean leave-A (no residual callback fires),
        (2) transition to PRE-BINDING, (3) re-run BOUND-entry against B. The
        declared subscription SET is carried across unchanged (architect-
        synthesis default for the open [DD-WS-SWITCH-UX] fork)."""
        if self.state != BOUND:
            # [CXC-WS-BOUNDARIES] row 7: a switch with no A to leave is a bind.
            return self.bind(tenant_id)
        self._teardown_subscription()  # (1) clean leave-A
        self.bound_tenant = None
        self._bus = None
        self.state = PRE_BINDING       # (2) observable intermediate state
        self._emit({"ws": "prebinding"})
        self.bind(tenant_id)           # (3) join-B via the same entry sequence

    def close(self) -> None:
        """The socket closed; the peer is simply gone, not parked."""
        self._teardown_subscription()
        self.bound_tenant = None
        self._bus = None
        self.state = PRE_BINDING

    # -- subscription ----------------------------------------------------- #
    def set_subscription(self, patterns: list[str]) -> None:
        """Declare the subscription set ([CXC-WS-SUBSCRIPTION], default-deny).

        Each pattern is validated through `bus.py`'s `validate_pattern`; an
        empty set means SUBSCRIBE NOTHING; `*` must be explicit. Returns an
        enforcement report to the client ([CXC-SUB-REPORT], never silent). When
        BOUND, applies the new set live and replays any NEWLY-matched namespace.
        """
        for p in patterns:
            validate_pattern(p)  # raises on a mid-glob or malformed pattern
        previous = list(self.declared_patterns)
        self.declared_patterns = list(patterns)
        self._emit({
            "ws": "subscribed",
            "patterns": list(self.declared_patterns),
            "enforcement": "broker",
        })
        if self.state == BOUND:
            # replay only namespaces the OLD set did not already cover
            # ([CXC-WS-RETAINED-REPLAY]: once per newly-matched namespace).
            newly = [p for p in patterns if p not in previous]
            if newly:
                self._replay_newly(newly, previous)

    def _install_subscription(self) -> None:
        """Register the faithful-envelope egress path on the bound bus.

        `bus.on_message` fires for every published/received BusMessage with the
        FULL envelope (src/seq/ts/scope) — the only bus hook that preserves it,
        so a WS-forwarded value is byte-faithful ([CXC-WS-WIRE-FORMAT]). The
        peer filters to its declared patterns and honors scope. In-process,
        one address space, so this IS the in-process-equivalent `broker`
        enforcement point [CXC-SUB-ENFORCE] names for this substrate.
        """
        if self._bus is None or self._on_message is not None:
            return

        def _forward(msg: BusMessage) -> None:
            if not self.declared_patterns:
                return  # default-deny: an empty declared set receives NOTHING
            if not any(pattern_matches(p, msg.namespace) for p in self.declared_patterns):
                return
            if not _egress_allowed(self._bus, msg.namespace):
                return  # local tenant chatter does not leak
            self._emit(msg.to_json())

        self._on_message = _forward
        self._bus.on_message(_forward)

    def _teardown_subscription(self) -> None:
        if self._bus is not None and self._on_message is not None:
            self._bus.off_message(self._on_message)
        self._on_message = None

    # -- publish (browser -> bus) ---------------------------------------- #
    def handle_bus_frame(self, wire: dict) -> None:
        """A faithful BusMessage envelope arrived from the client; publish it to
        the bound tenant bus. Illegal while PRE-BINDING ([CXC-WS-LIFECYCLE]:
        only the selection exchange is legal there)."""
        if self.state != BOUND or self._bus is None:
            self._error("not_bound",
                        "publish is illegal before a tenant bus is selected")
            return
        try:
            msg = BusMessage.from_json(wire)
        except Exception as exc:
            self._error("bad_envelope", f"{exc}")
            return
        source = msg.source or (self.identity or self.peer_id)
        if msg.type == MessageType.EVENT:
            self._bus.publish_event(msg.namespace, msg.event_type,
                                    data=msg.data, source=source)
        else:
            self._bus.publish_state(msg.namespace, msg.value, source=source)

    # -- inbound frame router -------------------------------------------- #
    def handle_frame(self, wire: dict) -> None:
        """Route one decoded client frame. A control frame carries `ws`; a bus
        frame is a faithful BusMessage envelope (has `ns`). The two channels are
        distinct on purpose: control (the selection exchange) is the ONLY thing
        legal in PRE-BINDING, and it never wraps a BusMessage."""
        if not isinstance(wire, dict):
            self._error("bad_frame", "frame must be a JSON object")
            return
        verb = wire.get("ws")
        if verb is None:
            return self.handle_bus_frame(wire)
        try:
            if verb == "list":
                self.serve_tenant_list()
            elif verb == "subscribe":
                self.set_subscription(list(wire.get("patterns", [])))
            elif verb == "select":
                self.bind(str(wire["tenant"]))
            elif verb == "switch":
                self.switch(str(wire["tenant"]))
            else:
                self._error("unknown_verb", f"unknown control verb {verb!r}")
        except UnknownTenantError as exc:
            self._error("unknown_tenant", str(exc))
        except ValueError as exc:
            self._error("bad_request", str(exc))

    # -- internals -------------------------------------------------------- #
    def _resolve_or_raise(self, tenant_id: str):
        if tenant_id not in set(self._registry.tenants()):
            raise UnknownTenantError(
                f"tenant {tenant_id!r} does not exist; known: "
                f"{sorted(self._registry.tenants())}")
        return self._registry.tenant_bus(tenant_id)

    def _replay(self, patterns: list[str]) -> None:
        if self._bus is not None and patterns:
            replay_retained(self._bus, patterns, self._emit)

    def _replay_newly(self, newly: list[str], previous: list[str]) -> None:
        """Replay namespaces matched by a NEWLY-declared pattern that no PRIOR
        pattern already covered — once per namespace, never duplicated."""
        if self._bus is None:
            return
        def deliver_if_new(wire: dict) -> None:
            name = wire.get("ns")
            if any(pattern_matches(p, name) for p in previous):
                return  # was already covered; not a NEW match
            self._emit(wire)
        replay_retained(self._bus, newly, deliver_if_new)


# --------------------------------------------------------------------------- #
# WSConnector — the LISTEN socket. Owns an asyncio server on a background
# thread and a collection of peer objects, one per accepted connection.
#
# NOT a Transport ABC subclass in the DIAL sense ([CXC-WS-CONNECTOR-INTERFACE]):
# it accepts inbound connections rather than dialing one, so it exposes a
# `peers` collection and an accept loop instead of send/subscribe.
# --------------------------------------------------------------------------- #
class WSConnector:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8430,
        registry: Optional[TenantRegistry] = None,
        *,
        deeplink_parser: Optional[Callable[[str], Optional[str]]] = None,
    ):
        self.host = host
        self.port = port
        self._registry = registry
        # [DD-WS-DEEPLINK-URL] is OPEN: candidate (a) `/bus/<tenant_id>` is the
        # implementable DEFAULT, swappable via `deeplink_parser`, NOT a ratified
        # shape. Root path -> operator mode (None).
        self._deeplink_parser = deeplink_parser or _default_deeplink_parser
        self._peers: dict[str, WSPeer] = {}
        self._peer_seq = 0
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._started = threading.Event()
        self._ready = threading.Event()  # set on EITHER success or bind failure
        self._bind_error: Optional[BaseException] = None
        self._bound_port: Optional[int] = None

    # -- kernel wiring ---------------------------------------------------- #
    def bind_registry(self, registry: TenantRegistry) -> None:
        """The kernel hands the connector its cross-tenant registry (the
        equivalent of a per-tenant `attach_connector`, but ONCE at server
        level). Must be set before a peer can select a tenant."""
        self._registry = registry

    @property
    def peers(self) -> list[WSPeer]:
        with self._lock:
            return list(self._peers.values())

    @property
    def is_connected(self) -> bool:
        return self._server is not None and self._started.is_set()

    @property
    def bound_port(self) -> Optional[int]:
        """The actual listen port (resolves a `port=0` ephemeral bind)."""
        return self._bound_port

    # -- peer factory (also the pure-logic entry point tests use) --------- #
    def _new_peer(self, send_frame: Callable[[dict], None],
                  deeplink: Optional[str] = None) -> WSPeer:
        if self._registry is None:
            raise RuntimeError(
                "WSConnector has no TenantRegistry — call bind_registry() at "
                "kernel standup before accepting connections")
        with self._lock:
            self._peer_seq += 1
            peer_id = f"ws-{self._peer_seq}"
            peer = WSPeer(peer_id, self._registry, send_frame)
            self._peers[peer_id] = peer
        if deeplink is not None:
            # deep-link converges on the SAME BOUND sequence, no list served.
            peer.bind(deeplink)
        else:
            # operator entry: serve the kernel list, stay PRE-BINDING.
            peer.serve_tenant_list()
        return peer

    def _drop_peer(self, peer: WSPeer) -> None:
        peer.close()
        with self._lock:
            self._peers.pop(peer.peer_id, None)

    # -- lifecycle -------------------------------------------------------- #
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"ws-connector-{self.port}", daemon=True)
        self._thread.start()
        # [CXC-WS-BOUNDARIES] row 2: a bind failure (port in use, no
        # permission) MUST degrade LOUDLY, naming the substrate and reason —
        # `_ready` is set on EITHER outcome, so a failed bind surfaces
        # immediately (not after the full timeout) with the real OSError.
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError(
                f"WS connector failed to bind {self.host}:{self.port} in 5s "
                f"(no response from accept-loop thread)")
        if self._bind_error is not None:
            self._thread = None
            raise RuntimeError(
                f"WS connector failed to bind {self.host}:{self.port}: "
                f"{self._bind_error}") from self._bind_error

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        async def _shutdown():
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            fut.result(timeout=5.0)
        except Exception as exc:
            logger.debug("ws shutdown: %s", exc)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None
        self._server = None
        self._started.clear()
        self._ready.clear()
        self._bind_error = None

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            try:
                loop.run_until_complete(self._serve())
            except Exception as exc:
                # [CXC-WS-BOUNDARIES] row 2: name the substrate and the
                # reason, loudly, and unblock start() immediately.
                logger.error(
                    "ws connector failed to bind %s:%s: %s",
                    self.host, self.port, exc)
                self._bind_error = exc
                self._ready.set()
                return
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve
        self._server = await serve(self._handle, self.host, self.port)
        # resolve an ephemeral port=0 bind so a test can find the real port
        try:
            for sock in self._server.sockets:
                self._bound_port = sock.getsockname()[1]
                break
        except Exception:
            self._bound_port = self.port
        self._started.set()
        self._ready.set()

    async def _handle(self, websocket) -> None:
        """One accepted connection = one singleton persistent peer."""
        path = _request_path(websocket)
        try:
            deeplink = self._deeplink_parser(path)
        except UnknownTenantError:
            deeplink = None  # resolved below to a loud in-band error
        loop = asyncio.get_running_loop()
        outbox: asyncio.Queue = asyncio.Queue()

        def send_frame(frame: dict) -> None:
            # called from ANY thread (bus dispatch runs on the publisher's
            # thread); hop onto the socket's loop.
            loop.call_soon_threadsafe(outbox.put_nowait, frame)

        # Validate a deep-link BEFORE minting the peer's bound state, so an
        # unknown tenant fails loudly ([CXC-WS-ENTRY-DEEPLINK]) rather than
        # silently falling through to the operator list.
        pending_deeplink = deeplink
        if deeplink is not None and self._registry is not None:
            if deeplink not in set(self._registry.tenants()):
                await websocket.send(json.dumps({
                    "ws": "error", "code": "unknown_tenant",
                    "message": f"deep-link tenant {deeplink!r} does not exist"}))
                pending_deeplink = None
                await websocket.close()
                return

        peer = self._new_peer(send_frame, deeplink=pending_deeplink)

        async def _pump() -> None:
            while True:
                frame = await outbox.get()
                await websocket.send(json.dumps(frame))

        pump_task = asyncio.create_task(_pump())
        try:
            async for raw in websocket:
                try:
                    wire = json.loads(raw)
                except Exception:
                    peer.handle_frame({"ws": "__bad__"})
                    continue
                peer.handle_frame(wire)
        except Exception as exc:
            logger.debug("ws peer %s closed: %s", peer.peer_id, exc)
        finally:
            pump_task.cancel()
            self._drop_peer(peer)


def _request_path(websocket) -> str:
    """Extract the request path across websockets versions (>=11 moved it to
    `websocket.request.path`)."""
    req = getattr(websocket, "request", None)
    if req is not None and getattr(req, "path", None):
        return req.path
    return getattr(websocket, "path", "/") or "/"


def _default_deeplink_parser(path: str) -> Optional[str]:
    """Candidate (a) of the OPEN [DD-WS-DEEPLINK-URL] fork: `/bus/<tenant_id>`
    pre-selects a tenant; any other path (`/`, `/ws`) is operator mode."""
    if not path:
        return None
    parts = [seg for seg in path.split("?", 1)[0].split("/") if seg]
    if len(parts) >= 2 and parts[0] == "bus":
        return parts[1]
    return None


# --------------------------------------------------------------------------- #
# Registry factory — `type: ws`, registered into the SAME `_FACTORIES` idiom
# the DIAL transports use. Imported LAZILY by the registry so `websockets`
# being absent never breaks the other four factories.
# --------------------------------------------------------------------------- #
def _ws(spec: dict, app_id: str) -> WSConnector:
    """Build the WS LISTEN connector from a declared `type: ws` spec.

    `spec` carries `host`/`port` (and, when built programmatically or by the
    kernel, a live `registry`). `app_id` is accepted for signature consistency
    with the DIAL factories even though a LISTEN connector has less use for it.
    A missing `host`/`port` applies a documented default (127.0.0.1:8430) —
    never a silent bind to an unintended interface. The kernel calls
    `bind_registry()` post-build if it did not pass one in `spec`.
    """
    return WSConnector(
        host=str(spec.get("host", "127.0.0.1")),
        port=int(spec.get("port", 8430)),
        registry=spec.get("registry"),
        deeplink_parser=spec.get("deeplink_parser"),
    )


def register_ws() -> None:
    """Register `type: ws` into the transport registry ([CXC-WS-CONNECTOR-NOT-
    PLUGIN]). Idempotent."""
    from composeio import transport_registry
    transport_registry.register("ws", _ws)
