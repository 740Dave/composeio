"""Acceptance for the bus_storage ComposeServer plugin (Wave-2b adaptation).

Proves the acceptance bar: the plugin is DISCOVERED + LOADED by the loader from
a dotted manifest ref; it ANNOUNCES; its MCP tools appear in the umbrella tool
list AND round-trip on the tenant bus (WRITE store -> QUERY fetch/list); and the
ratified path-traversal containment guard STILL FIRES (the negative control) on
both the write and the read path, on the SAME imported ``_resolve_contained`` the
HTTP service uses.

These are in-process bus tests. Per [CXC-VISION-EPISTEMICS], a real live-broker
MCP round-trip (streamable-HTTP) against a persistent store on mqtt-broker
is a diamond/human-verify item, not claimed green here.
"""
from __future__ import annotations

import base64

import pytest

from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import load_tenant_manifest
from composeio.transport_mcp import MCPConnector
from composeio.plugins.bus_storage import (
    BusStoragePlugin,
    STORE_REQUEST_NS,
    STORE_ERROR_NS,
    _ConfigStore,
)

REF = "composeio.plugins.bus_storage:BusStoragePlugin"


def _peer(server, tenant):
    conn = MCPConnector(registry=server)
    peer = conn.new_peer()
    peer.select_tenant(tenant)
    return peer


@pytest.fixture
def loaded(tmp_path):
    """A ComposeServer with one tenant carrying the plugin, loaded via a dotted
    manifest ref against a temp data_dir. Returns (server, tenant_id, result)."""
    srv = ComposeServer()
    manifest = {"plugins": [
        {"plugin": REF, "config": {"data_dir": str(tmp_path)}},
    ]}
    result = load_tenant_manifest(srv, "haven", manifest)
    return srv, "haven", result


# --------------------------------------------------------------------------- #
# discovery + load + announce
# --------------------------------------------------------------------------- #
def test_discovered_and_loaded_clean(loaded):
    srv, tenant, result = loaded
    assert result.is_clean(), result.config_parked
    names = [p.name for p in result.standup.loaded]
    assert "bus_storage" in names


def test_announces_on_the_server_surface(loaded):
    srv, tenant, _ = loaded
    bus = srv.tenant_bus(tenant)
    ann = bus.namespaces.get("_server/plugins/bus_storage/announce")
    assert ann is not None
    payload = ann.value
    assert payload["plugin"] == "bus_storage"
    tool_names = {t["local_name"] for t in payload["mcp_tools"]}
    assert tool_names == {
        "store_config_for_namespace",
        "get_stored_config_for_namespace",
        "list_all_stored_config_namespaces",
    }


def test_mcp_tools_appear_in_umbrella_list(loaded):
    srv, tenant, _ = loaded
    peer = _peer(srv, tenant)
    exposed = set(peer.tool_registry())
    assert {
        "bus_storage.store_config_for_namespace",
        "bus_storage.get_stored_config_for_namespace",
        "bus_storage.list_all_stored_config_namespaces",
    } <= exposed


# --------------------------------------------------------------------------- #
# round-trip on the tenant bus: WRITE -> QUERY
# --------------------------------------------------------------------------- #
def test_store_then_fetch_round_trips_verbatim(loaded):
    srv, tenant, _ = loaded
    peer = _peer(srv, tenant)
    raw = b"\x00\x01\x02compiled-config\xff"
    b64 = base64.b64encode(raw).decode()

    w = peer.invoke("bus_storage.store_config_for_namespace", {
        "target_namespace_id": "lights.kitchen",
        "config_payload_base64": b64,
    })
    assert w["ok"], w

    r = peer.invoke("bus_storage.get_stored_config_for_namespace", {
        "target_namespace_id": "lights.kitchen",
    })
    assert r["ok"], r
    assert r["result"]["present"] is True
    assert base64.b64decode(r["result"]["config_payload_base64"]) == raw


def test_fetch_unset_is_present_false_not_a_default(loaded):
    srv, tenant, _ = loaded
    peer = _peer(srv, tenant)
    r = peer.invoke("bus_storage.get_stored_config_for_namespace", {
        "target_namespace_id": "never.stored",
    })
    assert r["ok"] and r["result"]["present"] is False
    assert r["result"]["config_payload_base64"] is None


def test_list_all_stored_namespaces(loaded):
    srv, tenant, _ = loaded
    peer = _peer(srv, tenant)
    for ns in ("a.one", "b.two"):
        peer.invoke("bus_storage.store_config_for_namespace", {
            "target_namespace_id": ns,
            "config_payload_base64": base64.b64encode(b"x").decode(),
        })
    r = peer.invoke("bus_storage.list_all_stored_config_namespaces", {})
    assert r["ok"], r
    assert set(r["result"]["stored_namespace_ids"]) == {"a.one", "b.two"}
    assert r["result"]["count"] == 2


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROL — the ratified containment guard still fires
# --------------------------------------------------------------------------- #
def test_containment_guard_fires_on_direct_store(tmp_path):
    """The imported ratified `_resolve_contained` still refuses a traversal id."""
    store = _ConfigStore(str(tmp_path))
    with pytest.raises(store.PathEscape):
        store.store("../../etc/evil", b"pwn")
    with pytest.raises(store.PathEscape):
        store.store("/etc/passwd", b"pwn")  # absolute-path trap
    with pytest.raises(store.PathEscape):
        store.get("../../etc/shadow")


def test_write_path_refuses_traversal_and_writes_nothing(loaded, tmp_path):
    """A store WRITE with a traversal id publishes a store-error and leaves the
    filesystem untouched outside the config root."""
    srv, tenant, _ = loaded
    bus = srv.tenant_bus(tenant)
    peer = _peer(srv, tenant)

    peer.invoke("bus_storage.store_config_for_namespace", {
        "target_namespace_id": "../../escape",
        "config_payload_base64": base64.b64encode(b"pwn").decode(),
    })
    err = bus.namespaces.get(STORE_ERROR_NS)
    assert err is not None and isinstance(err.value, dict)
    assert "containment refusal" in err.value["error"]
    # nothing written anywhere under the temp tree beyond the configs/ dir itself
    stray = [p for p in tmp_path.rglob("*") if p.is_file() and "escape" in p.name]
    assert stray == []


def test_fetch_path_refuses_traversal(loaded):
    srv, tenant, _ = loaded
    peer = _peer(srv, tenant)
    r = peer.invoke("bus_storage.get_stored_config_for_namespace", {
        "target_namespace_id": "../../etc/passwd",
    })
    assert r["ok"]  # the query itself succeeds...
    assert r["result"]["present"] is False
    assert "containment refusal" in r["result"]["error"]  # ...but the read is refused
