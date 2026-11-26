"""ComposeServer server/kernel-level WS attach — the kernel-wiring seam
([SPEC:CXC-WS], [CXC-WS-TENANT-BOUNDARY]).

The WS connector's ``bind_registry()`` is meaningless until SOMETHING at the
kernel calls it: a LISTEN connector spans tenants, so it cannot ride the
per-tenant DIAL ``add_tenant(connectors=...)`` path. This proves the net-new
server seam ``ComposeServer.attach_ws_endpoint()`` closes that gap:

  a real ``ComposeServer`` with >=2 tenants starts a WS listener -> a synthetic
  ``websockets`` client connects -> operator list -> select a tenant ->
  subscribe (default-deny) -> retained replay of the pre-existing value ->
  browser->bus publish that a REAL bus subscriber on that tenant sees -> a
  SECOND client on the OTHER tenant proves no cross-tenant leak -> full-server
  ``stop()`` tears the listener down.

⛔ SYNTHETIC-CLIENT proof only. The real-BROWSER GLASS row is David's, owned by
the ComposeUX DOM backend ([CXC-VISION-EPISTEMICS]).
"""
from __future__ import annotations

import json
import time

import pytest

from composeio.composeserver import ComposeServer

pytest.importorskip("websockets")
from websockets.sync.client import connect  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402


@pytest.fixture
def server_with_ws():
    srv = ComposeServer()
    srv.add_tenant("haven")
    srv.add_tenant("bsg")
    # kernel-level attach — NOT a per-tenant connector; bound once to `srv`.
    conn = srv.attach_ws_endpoint(host="127.0.0.1", port=0)
    try:
        yield srv, conn
    finally:
        srv.stop()  # full-server stop must tear the listener down


def _url(conn, path="/"):
    return f"ws://127.0.0.1:{conn.bound_port}{path}"


def _drain(ws, timeout=0.6):
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


def test_attach_binds_this_server_as_registry(server_with_ws):
    srv, conn = server_with_ws
    # the connector was handed the SERVER as its cross-tenant registry, and the
    # listener is live on a real ephemeral port.
    assert conn.is_connected
    assert conn.bound_port and conn.bound_port > 0
    assert conn in srv._server_connectors


def test_full_path_operator_list_select_subscribe_replay_publish(server_with_ws):
    srv, conn = server_with_ws
    haven = srv.tenant_bus("haven")
    haven.publish_state("ship/speed", 12)          # value exists BEFORE connect

    seen = []
    haven.subscribe("bridge/cmd", lambda ns, new, old: seen.append(new))

    with connect(_url(conn)) as ws:
        # operator entry: the KERNEL's cross-tenant list, from THIS server.
        first = _drain(ws)
        tenants = [f for f in first if f.get("ws") == "tenants"]
        assert tenants and set(tenants[0]["tenants"]) == {"haven", "bsg"}
        assert _bus_frames(first) == []            # NEG: no data pre-select

        ws.send(json.dumps({"ws": "subscribe", "patterns": ["ship/*"]}))
        assert any(f.get("ws") == "subscribed" for f in _drain(ws))

        ws.send(json.dumps({"ws": "select", "tenant": "haven"}))
        bound = _drain(ws)
        assert any(f.get("ws") == "bound" and f["tenant"] == "haven" for f in bound)
        # retained replay of the pre-existing value, once, marked retained.
        replays = [f for f in _bus_frames(bound) if f.get("retained")]
        assert len(replays) == 1
        assert replays[0]["ns"] == "ship/speed" and replays[0]["val"] == 12

        # browser -> bus reaches a REAL bus subscriber on the bound tenant.
        ws.send(json.dumps({"ns": "bridge/cmd", "type": "state",
                            "val": 77, "src": "browser"}))
        for _ in range(50):
            if seen:
                break
            time.sleep(0.02)
    assert seen == [77]


def test_two_clients_no_cross_tenant_leak(server_with_ws):
    srv, conn = server_with_ws
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
        assert ga == {1} and gb == {2}             # no cross-tenant leak


def test_full_server_stop_tears_down_listener():
    srv = ComposeServer()
    srv.add_tenant("haven")
    conn = srv.attach_ws_endpoint(host="127.0.0.1", port=0)
    assert conn.is_connected
    srv.stop()
    assert not conn.is_connected
    assert srv._server_connectors == []
