"""fingerprint_manager ComposePlugin — acceptance ([SPEC:CXC-COMPOSESERVER]
[CXC-CS-PLUGIN-CONTRACT], [SPEC:CXC-CS-LOADER], [SPEC:CXC-MCP]
[CXC-MCP-TOOL-DECLARATION]).

Proves the extant fingerprint_service enroll/ingest bus-peer, re-homed as a
plugin, is: DISCOVERED by the loader from a dotted manifest ref, LOADED and
ANNOUNCED by ComposeServer, SUBSCRIBES its configured device namespace and
INGESTS its events, and exposes its MCP surface in the umbrella tool-list with a
working query round-trip.

Every check carries its NEGATIVE CONTROL ([CXC-VISION-EPISTEMICS]): an
instrument that can only report success is not an instrument. Run::

    PYTHONPATH=python:services python3 -m pytest \
        services/fingerprint_service/tests/test_plugin.py -v
"""
from __future__ import annotations

import pytest

from composeio.composeserver import ComposeServer, standup
from composeio.composeserver_loader import load_tenant_manifest
from composeio.transport_mcp import MCPConnector

from fingerprint_service import topics
from composeio.plugins.fingerprint_manager import (
    QUERY_ENROLLMENT_STATUS,
    FingerprintManagerPlugin,
)

DEV = "dev/s3-fp1"  # a live id that legitimately contains a '/'


# --------------------------------------------------------------------------- #
# ONE DECLARATION -> FOUR ARTIFACTS ([CXC-CS-PLUGIN-CONTRACT] + the MCP 4th)
# --------------------------------------------------------------------------- #
def test_registry_entry_derives_config_and_mcp_surface():
    entry = FingerprintManagerPlugin.registry_entry()
    assert entry["name"] == "fingerprint_manager"
    # config form is DERIVED from Config, not hand-written
    props = entry["config_schema"]["properties"]
    assert {"devices", "source", "template_store_url", "recent_ready_capacity"} <= set(props)
    # the MCP surface is the 4th derived artifact, exposed as {plugin}.{tool}
    names = {t["name"] for t in entry["mcp_tools"]}
    assert names == {
        "fingerprint_manager.get_fingerprint_enrollment_status_for_device",
        "fingerprint_manager.list_recent_fingerprint_ready_events",
    }
    # each tool's JSON-schema is DERIVED from its input_model
    by_name = {t["name"]: t for t in entry["mcp_tools"]}
    status = by_name["fingerprint_manager.get_fingerprint_enrollment_status_for_device"]
    assert "device_id" in status["input_schema"]["properties"]
    assert status["read_mode"] == "query"


def test_negative_default_loaded_is_false_biometric_is_opt_in():
    # NEGATIVE CONTROL: enrolling biometrics is never a silent standard-build
    # default — a deployment must install it deliberately.
    assert FingerprintManagerPlugin.default_loaded is False


# --------------------------------------------------------------------------- #
# STANDUP + ANNOUNCE ([DEC-PHASE0-COMPOSESERVER-PUBLISHES-DERIVED-ANNOUNCE])
# --------------------------------------------------------------------------- #
def test_standup_loads_and_announces_derived_surface():
    p = FingerprintManagerPlugin({"devices": [DEV]})
    res = standup(bus_id="haven", plugins=[p])
    assert p in res.loaded and not res.parked
    ann = res.bus.get_state("_server/plugins/fingerprint_manager/announce")
    st = res.bus.get_state("_server/plugins/fingerprint_manager/status")
    assert ann is not None and st["status"] == "running"
    assert {t["name"] for t in ann["mcp_tools"]} == {
        "fingerprint_manager.get_fingerprint_enrollment_status_for_device",
        "fingerprint_manager.list_recent_fingerprint_ready_events",
    }


def test_negative_empty_tenant_leaves_server_silent():
    # NEGATIVE CONTROL: no plugin, no _server/plugins/* announce.
    res = standup(bus_id="empty", plugins=[])
    assert [n for n in res.bus.namespaces if n.startswith("_server/plugins/")] == []


# --------------------------------------------------------------------------- #
# SUBSCRIBES ITS DEVICE NAMESPACE + INGESTS ([CXC-CS-DYNAMIC-SURFACE])
# --------------------------------------------------------------------------- #
def test_subscribes_configured_device_namespace():
    p = FingerprintManagerPlugin({"devices": [DEV]})
    standup(bus_id="haven", plugins=[p])
    surface = p.current_subscribes()
    # the query namespaces AND the followed device's event topics are live
    assert QUERY_ENROLLMENT_STATUS in surface
    assert topics.fingerprint_ready(DEV) in surface
    assert topics.auth_event(DEV) in surface


def test_negative_unfollowed_device_not_in_surface():
    # NEGATIVE CONTROL: a device that was never configured contributes NOTHING
    # to the subscribed surface.
    p = FingerprintManagerPlugin({"devices": [DEV]})
    standup(bus_id="haven", plugins=[p])
    assert topics.fingerprint_ready("dev/never-configured") not in p.current_subscribes()


# --------------------------------------------------------------------------- #
# PUBLISHES ITS DEVICE-ADDRESSED SURFACE ([CXC-CS-DYNAMIC-SURFACE] — the plugin
# is the SOLE AUTHORITY over its PUBLISHED surface too, not just subscribed).
# REGRESSION GUARD for DEFECT-FINGERPRINTMANAGERPLUGIN-ANNOUNCE-REGISTRY-UNDER-
# DECLARES-CE8672: current_publishes() was never overridden, so the derived
# announce/registry reported `publishes: []` while the plugin actually
# originates the roster push + enroll commands. The old announce test asserted
# mcp_tools but never publishes, so this was invisible to the green suite.
# --------------------------------------------------------------------------- #
def test_publishes_configured_device_namespace():
    p = FingerprintManagerPlugin({"devices": [DEV]})
    standup(bus_id="haven", plugins=[p])
    surface = p.current_publishes()
    # NON-EMPTY and honest: the two query-result roots AND the followed device's
    # service-originated topics (roster push + enroll commands) are live.
    assert surface, "current_publishes() must not be empty"
    assert topics.template_set(DEV) in surface          # the roster push
    assert topics.enroll_cmd(DEV) in surface            # <dev>/cmd/enroll
    assert topics.enroll_cancel_cmd(DEV) in surface     # <dev>/cmd/enroll/cancel


def test_standup_announce_publishes_is_honest():
    # The DERIVED announce must carry the device-addressed publish surface, not
    # an empty list — the assertion the original announce test lacked.
    p = FingerprintManagerPlugin({"devices": [DEV]})
    res = standup(bus_id="haven", plugins=[p])
    ann = res.bus.get_state("_server/plugins/fingerprint_manager/announce")
    assert ann["publishes"], "announce.publishes must not be empty"
    assert topics.template_set(DEV) in ann["publishes"]
    assert topics.enroll_cmd(DEV) in ann["publishes"]
    assert topics.enroll_cancel_cmd(DEV) in ann["publishes"]


def test_registry_publishes_declares_static_result_roots():
    # The class-level registry entry can't know the configured devices, but it
    # MUST still honestly declare the always-on query-result roots rather than [].
    entry = FingerprintManagerPlugin.registry_entry()
    assert entry["publishes"], "registry publishes must not be empty"
    assert "fingerprint_manager/enrollment_status/result" in entry["publishes"]
    assert "fingerprint_manager/recent_fingerprint_ready/result" in entry["publishes"]


def test_negative_unfollowed_device_not_in_publish_surface():
    # NEGATIVE CONTROL: a device never configured contributes NOTHING to the
    # published surface — so the assertions above are measuring follow, not a
    # blanket always-true membership.
    p = FingerprintManagerPlugin({"devices": [DEV]})
    standup(bus_id="haven", plugins=[p])
    assert topics.template_set("dev/never-configured") not in p.current_publishes()


def test_ingests_fingerprint_ready_from_its_device():
    p = FingerprintManagerPlugin({"devices": [DEV]})
    res = standup(bus_id="haven", plugins=[p])
    bus = res.bus
    # a real device heartbeat on the followed namespace is INGESTED (recorded).
    bus.publish_event(topics.fingerprint_ready(DEV), "fingerprint_ready",
                      data={"template_set_uuid": "u-123"}, source=DEV)
    payload = p._recent_ready_payload({"limit": 20})
    assert payload["count"] == 1
    assert payload["events"][0]["device_id"] == DEV
    assert payload["events"][0]["event"]["template_set_uuid"] == "u-123"


def test_negative_event_from_unfollowed_device_not_ingested():
    # NEGATIVE CONTROL: an identical heartbeat from a device the plugin does NOT
    # follow is never recorded — the recorder is device-scoped, not global.
    p = FingerprintManagerPlugin({"devices": [DEV]})
    res = standup(bus_id="haven", plugins=[p])
    res.bus.publish_event(topics.fingerprint_ready("dev/other"), "fingerprint_ready",
                          data={"template_set_uuid": "x"}, source="dev/other")
    assert p._recent_ready_payload({"limit": 20})["count"] == 0


# --------------------------------------------------------------------------- #
# LOADER DISCOVERY ([SPEC:CXC-CS-LOADER] [CXC-CSL-MANIFEST-SHAPE])
# --------------------------------------------------------------------------- #
def test_loader_discovers_plugin_from_dotted_manifest_ref():
    srv = ComposeServer()
    manifest = {"plugins": [{
        "plugin": "composeio.plugins.fingerprint_manager:FingerprintManagerPlugin",
        "config": {"devices": [DEV]},
    }]}
    result = load_tenant_manifest(srv, "haven", manifest, start=False)
    assert result.is_clean()
    assert "fingerprint_manager" in srv.registry()["haven"]


def test_negative_bad_ref_parks_not_crash():
    # NEGATIVE CONTROL: an unresolvable ref is a NAMED resolve: park, never a
    # crash, and never a silent success ([CXC-CSL-ERROR-TAXONOMY]).
    srv = ComposeServer()
    manifest = {"plugins": [{
        "plugin": "composeio.plugins.fingerprint_manager:NoSuchPlugin",
        "config": {},
    }]}
    result = load_tenant_manifest(srv, "haven", manifest)
    assert not result.is_clean()
    assert any("resolve:" in r
               for parks in result.config_parked.values() for r in parks)


def test_negative_bad_config_parks_before_instantiation():
    # NEGATIVE CONTROL: a config that fails pydantic validation parks with a
    # config:<field> reason, BEFORE the plugin is constructed
    # ([CXC-CSL-CONFIG-VALIDATION]).
    srv = ComposeServer()
    manifest = {"plugins": [{
        "plugin": "composeio.plugins.fingerprint_manager:FingerprintManagerPlugin",
        "config": {"recent_ready_capacity": "not-an-int"},
    }]}
    result = load_tenant_manifest(srv, "haven", manifest)
    assert "fingerprint_manager" in result.config_parked
    assert any("config:recent_ready_capacity" in r
               for r in result.config_parked["fingerprint_manager"])


# --------------------------------------------------------------------------- #
# MCP UMBRELLA: tools appear in the list + round-trip ([SPEC:CXC-MCP])
# --------------------------------------------------------------------------- #
def _bound_peer(srv, tenant="haven"):
    peer = MCPConnector(registry=srv).new_peer()
    peer.select_tenant(tenant)
    return peer


def _server_with_plugin(devices=(DEV,)):
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[FingerprintManagerPlugin({"devices": list(devices)})])
    return srv


def test_mcp_tools_appear_in_umbrella_list():
    peer = _bound_peer(_server_with_plugin())
    listed = {t["name"] for t in peer.list_tools()}
    assert "fingerprint_manager.get_fingerprint_enrollment_status_for_device" in listed
    assert "fingerprint_manager.list_recent_fingerprint_ready_events" in listed


def test_negative_tenant_without_plugin_has_no_fingerprint_tools():
    # NEGATIVE CONTROL: a tenant that never installed the plugin exposes NONE of
    # its tools ([CXC-MCP-TENANT-BOUNDARY]).
    srv = ComposeServer()
    srv.add_tenant("bare", plugins=[])
    peer = _bound_peer(srv, "bare")
    listed = {t["name"] for t in peer.list_tools()}
    assert not any(n.startswith("fingerprint_manager.") for n in listed)


def test_enrollment_status_query_round_trips():
    srv = _server_with_plugin()
    peer = _bound_peer(srv)
    r = peer.invoke(
        "fingerprint_manager.get_fingerprint_enrollment_status_for_device",
        {"device_id": DEV})
    assert r["ok"]
    assert r["result"]["followed"] is True
    assert r["result"]["device_id"] == DEV
    assert r["result"]["request_id"] == r["request_id"]  # echoed verbatim


def test_negative_status_query_for_unknown_device_reports_not_followed():
    # NEGATIVE CONTROL: the tool answers for an unknown device with followed=
    # False — a real answer, never a fabricated "yes".
    srv = _server_with_plugin()
    peer = _bound_peer(srv)
    r = peer.invoke(
        "fingerprint_manager.get_fingerprint_enrollment_status_for_device",
        {"device_id": "dev/ghost"})
    assert r["ok"] and r["result"]["followed"] is False


def test_recent_ready_query_round_trips_after_ingest():
    srv = _server_with_plugin()
    bus = srv.tenant_bus("haven")
    bus.publish_event(topics.fingerprint_ready(DEV), "fingerprint_ready",
                      data={"template_set_uuid": "u-9"}, source=DEV)
    peer = _bound_peer(srv)
    r = peer.invoke("fingerprint_manager.list_recent_fingerprint_ready_events",
                    {"limit": 5})
    assert r["ok"] and r["result"]["count"] == 1
    assert r["result"]["events"][0]["event"]["template_set_uuid"] == "u-9"


def test_negative_recent_ready_empty_before_any_ingest():
    # NEGATIVE CONTROL: with no heartbeat ingested, the tool returns an empty
    # list — not a stale or fabricated one.
    srv = _server_with_plugin()
    peer = _bound_peer(srv)
    r = peer.invoke("fingerprint_manager.list_recent_fingerprint_ready_events",
                    {"limit": 5})
    assert r["ok"] and r["result"]["count"] == 0


# --------------------------------------------------------------------------- #
# TEMPLATE_STORE SEAM — config-driven endpoint, real default preserved
# --------------------------------------------------------------------------- #
def test_injected_template_seams_win_over_config():
    calls = {}

    def fake_put(uri, data):
        calls["put"] = uri
        return uri

    p = FingerprintManagerPlugin(
        {"devices": [DEV], "template_store_url": "http://ignored:9999"},
        template_store_put=fake_put)
    put, resolver = p._resolve_template_seams()
    assert put is fake_put  # injection beats config


def test_default_leaves_real_resolve_seams_untouched():
    # NEGATIVE CONTROL: with neither injection nor a config URL, the plugin
    # hands FingerprintService None — i.e. its REAL composeio.resolve defaults,
    # integration preserved exactly as-is.
    p = FingerprintManagerPlugin({"devices": [DEV]})
    assert p._resolve_template_seams() == (None, None)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
