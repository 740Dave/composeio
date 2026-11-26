"""Tests for the fingerprint_template_store ComposeServer plugin.

Covers the acceptance bar: the plugin is DISCOVERED + LOADED via the loader
manifest, ANNOUNCES its derived surface (incl. the MCP tools), its MCP tools
APPEAR in the umbrella tool list, and each one ROUND-TRIPS over the
CXC-BUS-QUERY idiom. The extant HTTP surface is exercised too (existing-caller
compatibility). Every behavioral test carries an inline NEGATIVE CONTROL so a
green run is evidence, not a certifier that can only report success
([CXC-VISION-EPISTEMICS]).
"""
from __future__ import annotations

import base64
import urllib.request

import pytest

from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import load_tenant_manifest
from composeio.transport_mcp import MCPConnector
from composeio.plugins.fingerprint_template_store import (
    BadTemplateId,
    FingerprintTemplateStorePlugin,
    TemplateStore,
)

REF = ("composeio.plugins.fingerprint_template_store:"
       "FingerprintTemplateStorePlugin")


# --------------------------------------------------------------------------- #
# 1. The store core — discipline preserved, with negative controls.
# --------------------------------------------------------------------------- #
def test_store_put_get_delete_round_trip(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.ensure_dirs()
    assert store.get("abc123") is None                      # neg: absent -> None
    assert store.put("abc123", b"\x01\x02\x03") == 3
    assert store.get("abc123") == b"\x01\x02\x03"
    assert store.delete("abc123") is True
    assert store.delete("abc123") is False                  # neg: idempotent
    assert store.get("abc123") is None


def test_store_rejects_path_traversal_id(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.ensure_dirs()
    # Positive control: a legal id is accepted.
    assert store.put("legal_id-1", b"x") == 1
    # Negative controls: every path-shaped id is refused BEFORE touching disk.
    for bad in ("../escape", "a/b", "with.dot", "", "has space"):
        with pytest.raises(BadTemplateId):
            store.get(bad)
        with pytest.raises(BadTemplateId):
            store.put(bad, b"x")


def test_store_list_ids_reports_sizes(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.ensure_dirs()
    assert store.list_ids() == []                           # neg: empty store
    store.put("id_a", b"aa")
    store.put("id_b", b"bbbb")
    rows = store.list_ids()
    assert {r["fingerprint_template_id"] for r in rows} == {"id_a", "id_b"}
    by_id = {r["fingerprint_template_id"]: r["size_bytes"] for r in rows}
    assert by_id == {"id_a": 2, "id_b": 4}


# --------------------------------------------------------------------------- #
# 2. Discovery + load via the loader manifest (dotted ref), then announce.
# --------------------------------------------------------------------------- #
def _load(tmp_path):
    srv = ComposeServer()
    manifest = {"plugins": [{
        "plugin": REF,
        "config": {"data_dir": str(tmp_path), "http_enabled": False},
    }]}
    result = load_tenant_manifest(srv, "haven", manifest)
    return srv, result


def test_discovered_loaded_and_announces(tmp_path):
    srv, result = _load(tmp_path)
    assert result.is_clean()                                # neg: no parks
    loaded = {p.name for p in result.standup.loaded}
    assert "fingerprint_template_store" in loaded
    plugin = next(p for p in result.standup.loaded
                  if p.name == "fingerprint_template_store")
    ann = plugin.announce().model_dump()
    tool_names = {t["name"] for t in ann["mcp_tools"]}
    assert tool_names == {
        "fingerprint_template_store.list_enrolled_fingerprint_template_ids",
        "fingerprint_template_store.get_fingerprint_template_by_id",
        "fingerprint_template_store.delete_fingerprint_template_by_id",
    }
    # Each tool's JSON-schema is DERIVED from its input_model, not hand-written.
    get_tool = next(t for t in ann["mcp_tools"]
                    if t["local_name"] == "get_fingerprint_template_by_id")
    assert "fingerprint_template_id" in get_tool["input_schema"]["properties"]


def test_bad_config_parks_not_crashes(tmp_path):
    # Negative control for the discovery path: a wrong-typed config field PARKS
    # the plugin (config: taxonomy), it does not crash the loader.
    srv = ComposeServer()
    manifest = {"plugins": [{
        "plugin": REF, "config": {"http_port": "not-an-int"},
    }]}
    result = load_tenant_manifest(srv, "haven", manifest)
    assert not result.is_clean()
    assert "fingerprint_template_store" in result.config_parked


# --------------------------------------------------------------------------- #
# 3. MCP umbrella: tools appear in the list + each round-trips.
# --------------------------------------------------------------------------- #
def _peer(srv):
    conn = MCPConnector(registry=srv)
    peer = conn.new_peer()
    peer.select_tenant("haven")
    return peer


def test_mcp_tools_appear_in_umbrella_list(tmp_path):
    srv, _ = _load(tmp_path)
    peer = _peer(srv)
    names = {t["name"] for t in peer.list_tools()}
    for expected in (
        "fingerprint_template_store.list_enrolled_fingerprint_template_ids",
        "fingerprint_template_store.get_fingerprint_template_by_id",
        "fingerprint_template_store.delete_fingerprint_template_by_id",
    ):
        assert expected in names
    # Negative control: a name NOT declared is absent (no phantom tools).
    assert "fingerprint_template_store.enroll" not in names


def test_mcp_list_round_trips(tmp_path):
    srv, result = _load(tmp_path)
    store = next(p for p in result.standup.loaded
                 if p.name == "fingerprint_template_store").store
    store.put("tmpl_one", b"12345")
    peer = _peer(srv)
    r = peer.invoke(
        "fingerprint_template_store.list_enrolled_fingerprint_template_ids", {})
    assert r["ok"]
    ids = r["result"]["fingerprint_template_ids"]
    assert r["result"]["count"] == 1
    assert ids[0]["fingerprint_template_id"] == "tmpl_one"
    assert ids[0]["size_bytes"] == 5


def test_mcp_get_round_trips_bytes_as_base64(tmp_path):
    srv, result = _load(tmp_path)
    store = next(p for p in result.standup.loaded
                 if p.name == "fingerprint_template_store").store
    raw = bytes(range(64))
    store.put("tmpl_bytes", raw)
    peer = _peer(srv)
    r = peer.invoke(
        "fingerprint_template_store.get_fingerprint_template_by_id",
        {"fingerprint_template_id": "tmpl_bytes"})
    assert r["ok"] and r["result"]["found"] is True
    assert base64.b64decode(r["result"]["template_bytes_base64"]) == raw
    assert r["result"]["size_bytes"] == 64

    # Negative control: a missing id returns found=false, no fabricated bytes.
    r2 = peer.invoke(
        "fingerprint_template_store.get_fingerprint_template_by_id",
        {"fingerprint_template_id": "does_not_exist"})
    assert r2["ok"] and r2["result"]["found"] is False
    assert r2["result"]["template_bytes_base64"] is None


def test_mcp_delete_round_trips_and_confirms(tmp_path):
    srv, result = _load(tmp_path)
    store = next(p for p in result.standup.loaded
                 if p.name == "fingerprint_template_store").store
    store.put("tmpl_del", b"gone-soon")
    peer = _peer(srv)
    r = peer.invoke(
        "fingerprint_template_store.delete_fingerprint_template_by_id",
        {"fingerprint_template_id": "tmpl_del"})
    assert r["ok"] and r["result"]["existed"] is True and r["result"]["deleted"] is True
    assert store.get("tmpl_del") is None                    # actually removed

    # Negative control: deleting an absent id confirms existed=false, no error.
    r2 = peer.invoke(
        "fingerprint_template_store.delete_fingerprint_template_by_id",
        {"fingerprint_template_id": "tmpl_del"})
    assert r2["ok"] and r2["result"]["existed"] is False


def test_mcp_get_malformed_id_answered_not_hung(tmp_path):
    # A path-shaped id is reported IN the result (ok=false), never raised, never
    # a hang. Negative control against the "instrument can only report success"
    # failure — the malformed path must produce a distinct, non-ok answer.
    srv, _ = _load(tmp_path)
    peer = _peer(srv)
    r = peer.invoke(
        "fingerprint_template_store.get_fingerprint_template_by_id",
        {"fingerprint_template_id": "../etc/passwd"})
    assert r["ok"]  # the query itself round-tripped
    assert r["result"]["ok"] is False and "malformed" in r["result"]["error"]


# --------------------------------------------------------------------------- #
# 4. The extant HTTP surface still serves existing callers (fingerprint://).
# --------------------------------------------------------------------------- #
def test_http_surface_preserves_existing_caller_behavior(tmp_path):
    srv = ComposeServer()
    manifest = {"plugins": [{
        "plugin": REF,
        # port 0 => OS-assigned ephemeral; bind loopback for the test.
        "config": {"data_dir": str(tmp_path), "http_host": "127.0.0.1",
                   "http_port": 0, "http_enabled": True},
    }]}
    result = load_tenant_manifest(srv, "haven", manifest)
    plugin = next(p for p in result.standup.loaded
                  if p.name == "fingerprint_template_store")
    host, port = plugin.http_address
    base = f"http://127.0.0.1:{port}"

    # PUT then GET — the exact routes composeio.resolve.put_bytes/resolve_bytes use.
    put = urllib.request.Request(
        f"{base}/api/v1/templates/http_id", data=b"payload", method="PUT")
    with urllib.request.urlopen(put, timeout=5) as resp:
        assert resp.status == 201
    with urllib.request.urlopen(
            f"{base}/api/v1/templates/http_id", timeout=5) as resp:
        assert resp.read() == b"payload"

    # Negative control: a GET for an absent id is a 404, not a fabricated body.
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/v1/templates/absent", timeout=5)
    assert exc.value.code == 404

    plugin.stop()
