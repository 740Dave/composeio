"""WS connector — LIVE-socket acceptance ([SPEC:CXC-WS] §-acceptance).

A synthetic WS client (the `websockets` sync client) connects to a real
`WSConnector` accept socket and drives the full path: operator list ->
subscribe (default-deny) -> select -> retained replay -> browser->bus publish a
real bus subscriber sees -> bus->browser live -> scope/deny negative controls ->
deep-link + unknown-tenant.

⛔ This is the SYNTHETIC-CLIENT proof. The `verify-human` GLASS row (a real
BROWSER rendering a live scene) is owned by the ComposeUX DOM backend building
in parallel and is NOT self-certifiable here ([CXC-VISION-EPISTEMICS]).
"""
from __future__ import annotations

import json

import pytest

from composeio.composeserver import ComposeServer
from composeio.namespace import Scope

pytest.importorskip("websockets")
from websockets.sync.client import connect  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402

from composeio.transport_ws import WSConnector  # noqa: E402


@pytest.fixture
def live():
    srv = ComposeServer()
    srv.add_tenant("haven")
    srv.add_tenant("bsg")
    conn = WSConnector(host="127.0.0.1", port=0, registry=srv)
    conn.start()
    try:
        yield srv, conn
    finally:
        conn.stop()


def _url(conn, path="/"):
    return f"ws://127.0.0.1:{conn.bound_port}{path}"


def _drain(ws, timeout=0.6):
    """Collect every frame that arrives within `timeout` of quiescence."""
    frames = []
    while True:
        try:
            frames.append(json.loads(ws.recv(timeout=timeout)))
        except TimeoutError:
            break
        except ConnectionClosed:
            break
    return frames


def _bus_frames(frames):
    return [f for f in frames if "ws" not in f]


def test_live_operator_list_then_select_then_replay(live):
    srv, conn = live
    haven = srv.tenant_bus("haven")
    haven.publish_state("ship/speed", 12)   # value exists BEFORE the client
    with connect(_url(conn)) as ws:
        # operator entry: the kernel list arrives before any data (WS-V5)
        first = _drain(ws)
        tenants = [f for f in first if f.get("ws") == "tenants"]
        assert tenants and set(tenants[0]["tenants"]) == {"haven", "bsg"}
        assert _bus_frames(first) == []          # NEG CONTROL: no data pre-select

        ws.send(json.dumps({"ws": "subscribe", "patterns": ["ship/*"]}))
        rep = _drain(ws)
        assert any(f.get("ws") == "subscribed" and f["enforcement"] == "broker"
                   for f in rep)                 # enforcement report, never None

        ws.send(json.dumps({"ws": "select", "tenant": "haven"}))
        bound = _drain(ws)
        assert any(f.get("ws") == "bound" and f["tenant"] == "haven" for f in bound)
        # retained replay: current value delivered ONCE, marked retained (WS-V10)
        replays = [f for f in _bus_frames(bound) if f.get("retained")]
        assert len(replays) == 1
        assert replays[0]["ns"] == "ship/speed" and replays[0]["val"] == 12


def test_live_browser_publish_reaches_real_bus_subscriber(live):
    srv, conn = live
    haven = srv.tenant_bus("haven")
    seen = []
    haven.subscribe("bridge/cmd", lambda ns, new, old: seen.append(new))
    with connect(_url(conn, "/bus/haven")) as ws:   # deep-link straight to BOUND
        _drain(ws)                                   # bound frame
        # browser -> bus: a faithful BusMessage envelope (val/src, not payload)
        ws.send(json.dumps({"ns": "bridge/cmd", "type": "state",
                            "val": 77, "src": "browser"}))
        # give the server loop a moment to publish onto the bus
        import time
        for _ in range(50):
            if seen:
                break
            time.sleep(0.02)
    assert seen == [77]


def test_live_bus_to_browser_and_default_deny_and_scope(live):
    srv, conn = live
    haven = srv.tenant_bus("haven")
    haven.register_namespace("secret/key", "json", scope=Scope.LOCAL)
    with connect(_url(conn, "/bus/haven")) as ws:
        _drain(ws)
        ws.send(json.dumps({"ws": "subscribe", "patterns": ["ship/*"]}))
        _drain(ws)
        # bus -> browser LIVE
        haven.publish_state("ship/speed", 9)
        # NEG CONTROL default-deny: undeclared namespace never arrives
        haven.publish_state("system/alert", "boom")
        # NEG CONTROL scope: an explicit local namespace never leaks
        haven.publish_state("secret/key", "hunter2")
        got = _bus_frames(_drain(ws))
        names = {f["ns"] for f in got}
        assert any(f["ns"] == "ship/speed" and f["val"] == 9 for f in got)
        assert "system/alert" not in names
        assert "secret/key" not in names


def test_live_deeplink_unknown_tenant_fails_loudly(live):
    srv, conn = live
    with connect(_url(conn, "/bus/nope")) as ws:
        frames = _drain(ws)
        errs = [f for f in frames if f.get("ws") == "error"]
        assert errs and errs[0]["code"] == "unknown_tenant"
        # NEG CONTROL: never a silent fallback to the operator list.
        assert not any(f.get("ws") == "tenants" for f in frames)


def test_live_bind_failure_is_loud_and_fast_not_a_5s_timeout():
    """[CXC-WS-BOUNDARIES] row 2: a bind failure (port already in use) MUST
    degrade LOUDLY, naming the substrate and the real reason — never a
    silent `None`, and never disguised as the generic 5s liveness timeout.

    NEGATIVE CONTROL: before the fix, `start()` on a blocked port waited the
    full 5s and raised a message with NO underlying reason ("failed to bind
    ... in 5s"); a caller could not tell "port in use" from "thread never
    scheduled". This test pins BOTH: fails in well under 5s, and the real
    OSError text (`address already in use` / errno 48) is in the message.
    """
    import socket
    import time

    from composeio.transport_ws import WSConnector

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        conn = WSConnector(host="127.0.0.1", port=port)
        t0 = time.time()
        with pytest.raises(RuntimeError, match=r"(?i)address already in use|errno 48"):
            conn.start()
        assert time.time() - t0 < 2.0, "bind failure must not hide behind the 5s liveness timeout"
    finally:
        blocker.close()


def test_live_two_tabs_are_independent_peers(live):
    srv, conn = live
    haven, bsg = srv.tenant_bus("haven"), srv.tenant_bus("bsg")
    with connect(_url(conn, "/bus/haven")) as a, connect(_url(conn, "/bus/bsg")) as b:
        _drain(a); _drain(b)
        assert len(conn.peers) == 2
        a.send(json.dumps({"ws": "subscribe", "patterns": ["ship/*"]}))
        b.send(json.dumps({"ws": "subscribe", "patterns": ["ship/*"]}))
        _drain(a); _drain(b)
        haven.publish_state("ship/speed", 1)
        bsg.publish_state("ship/speed", 2)
        ga = {f["val"] for f in _bus_frames(_drain(a)) if f["ns"] == "ship/speed"}
        gb = {f["val"] for f in _bus_frames(_drain(b)) if f["ns"] == "ship/speed"}
        assert ga == {1} and gb == {2}   # no cross-tenant leak between tabs
