"""storage_monitor ComposeServer PLUGIN — acceptance for the Wave-2b adaptation.

    PYTHONPATH=python:services python3 -m pytest \
        services/storage_monitor/tests/test_storage_monitor_plugin.py -v

Pins the four acceptance gates of turning the extant sensor into a plugin:
  * DISCOVERED + LOADED by the config-manifest loader via a dotted ``ref``
    ([SPEC:CXC-CS-LOADER] [CXC-CSL-MANIFEST-SHAPE]);
  * it ANNOUNCES on the derived-announce spine, carrying its MCP tool descriptor
    ([CXC-CS-PLUGIN-CONTRACT], [CXC-MCP-TOOL-DECLARATION]);
  * the MCP tool APPEARS in the umbrella tool-list once a tenant is bound
    ([CXC-MCP-ENDPOINT-TOPOLOGY]);
  * the retained read ROUND-TRIPS the live free-bytes value ([CXC-MCP-INVOKE-READ]).

Every check carries its NEGATIVE CONTROL ([CXC-VISION-EPISTEMICS]): an instrument
that can only report success is not an instrument. The load-bearing one is the
retained read — a tool that returns "no value yet" whether or not the sensor ran
would be a fixed answer, not a measurement; the negative control drives a bus that
never published and confirms the SAME tool reads present=false there.
"""
from __future__ import annotations

import shutil

import pytest

from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import (
    load_tenant_manifest,
    resolve_plugin_class,
)
from composeio.plugin import ReadMode, ToolDirection
from composeio.transport_mcp import MCPConnector

from storage_monitor import topics
from storage_monitor.plugin import StorageMonitorPlugin


# A deterministic byte count so the publish/read path is asserted without
# depending on the host's real disk.
FIXED_FREE = 987_654_321

# The dotted manifest ref a tenant manifest carries ([CXC-CSL-MANIFEST-SHAPE]
# ref-resolution #2). Requires `services` on PYTHONPATH (the suite's convention).
PLUGIN_REF = "storage_monitor.plugin:StorageMonitorPlugin"

# The exposed MCP tool name an Intelligence sees: {plugin}.{tool}.
TOOL = "storage_monitor.get_current_free_bytes_on_monitored_filesystem"


def _fixed_measure(_path: str) -> int:
    return FIXED_FREE


# --------------------------------------------------------------------------- #
# 1. DISCOVERY — the dotted ref resolves to the plugin class.
# --------------------------------------------------------------------------- #
def test_manifest_ref_resolves_to_the_plugin_class():
    assert resolve_plugin_class(PLUGIN_REF) is StorageMonitorPlugin


def test_a_bogus_ref_does_not_resolve_to_this_plugin():
    """NEGATIVE CONTROL: resolution is specific — a wrong class name in the same
    module is a named resolution failure, not a silent fallback to this plugin."""
    from composeio.composeserver_loader import PluginResolutionError

    with pytest.raises(PluginResolutionError):
        resolve_plugin_class("storage_monitor.plugin:NoSuchPlugin")


# --------------------------------------------------------------------------- #
# 2. LOAD — the loader stands the plugin up from a manifest and it announces.
# --------------------------------------------------------------------------- #
def test_loader_loads_the_plugin_and_it_announces_with_its_mcp_tool():
    manifest = {"plugins": [
        {"plugin": PLUGIN_REF, "config": {"path": "/tmp", "interval_s": 45.0}},
    ]}
    srv = ComposeServer()
    try:
        res = load_tenant_manifest(srv, "buildhost", manifest, start=True)

        # LOADED clean — no resolve/config park.
        assert res.is_clean(), res.config_parked
        assert [p.name for p in res.standup.loaded] == ["storage_monitor"]

        # It ANNOUNCES on the derived-announce spine, and the announce carries
        # the derived MCP tool descriptor (the FOURTH derived artifact).
        bus = srv.tenant_bus("buildhost")
        announce = bus.get_state("_server/plugins/storage_monitor/announce")
        assert announce is not None
        assert topics.FREE_BYTES in announce["publishes"]
        names = [t["name"] for t in announce["mcp_tools"]]
        assert TOOL in names
        desc = next(t for t in announce["mcp_tools"] if t["name"] == TOOL)
        assert desc["direction"] == ToolDirection.READ.value
        assert desc["read_mode"] == ReadMode.RETAINED.value
        assert desc["namespace_mapping"]["subscribes"] == [topics.FREE_BYTES]

        # config actually took effect on the wrapped sensor.
        spec = next(p for p in srv.tenant_plugin_specs("buildhost")
                    if p.name == "storage_monitor")
        assert spec.service is not None
        assert spec.service.path == "/tmp"
        assert spec.service.interval_s == 45.0
    finally:
        _stop_all(srv)


def test_a_bad_interval_parks_on_config_not_crash():
    """NEGATIVE CONTROL for validation: interval_s must be > 0. A zero interval is
    a `config:` park keyed by the plugin name, and the loader run survives it —
    never a crash, never a silently-running sensor ([CXC-CSL-CONFIG-VALIDATION])."""
    manifest = {"plugins": [
        {"plugin": PLUGIN_REF, "config": {"interval_s": 0.0}},
    ]}
    srv = ComposeServer()
    try:
        res = load_tenant_manifest(srv, "buildhost", manifest, start=True)
        assert not res.is_clean()
        assert "storage_monitor" in res.config_parked
        assert any("interval_s" in r for r in res.config_parked["storage_monitor"])
        # never instantiated / started.
        assert [p.name for p in res.standup.loaded] == []
    finally:
        _stop_all(srv)


# --------------------------------------------------------------------------- #
# 3. MCP LIST + 4. ROUND-TRIP — the tool appears and reads the live value.
# --------------------------------------------------------------------------- #
def test_mcp_tool_appears_in_umbrella_list_and_retained_read_round_trips():
    srv = ComposeServer()
    try:
        # Construct the plugin directly so a deterministic measure is injected —
        # the loader path (real df) is exercised by the round-trip below with the
        # real filesystem; here we pin the exact byte count end to end.
        plugin = StorageMonitorPlugin(
            config={"path": "/tmp"}, measure=_fixed_measure)
        srv.add_tenant("buildhost", plugins=[plugin], start=True)

        conn = MCPConnector(registry=srv)
        peer = conn.new_peer()

        # PRE-SELECTION: no tenant-scoped tool is reachable ([CXC-MCP-ENDPOINT-
        # TOPOLOGY]) — the negative control for the tenant boundary.
        pre = [t["name"] for t in peer.list_tools()]
        assert TOOL not in pre

        peer.select_tenant("buildhost")
        post = [t["name"] for t in peer.list_tools()]
        assert TOOL in post

        # ROUND-TRIP: the retained read returns the live published value.
        result = peer.invoke(TOOL, {})
        assert result["ok"] is True
        assert result["namespace"] == topics.FREE_BYTES
        assert result["present"] is True
        assert result["value"] == FIXED_FREE
    finally:
        _stop_all(srv)


def test_retained_read_flips_to_no_value_when_the_sensor_fact_is_absent():
    """NEGATIVE CONTROL for the round-trip, by fault injection (the pattern the
    loader bringup smoke uses): the SAME tool, same peer, reads present=true while
    the sensor's fact is retained and present=false once it is gone — never a
    fabricated default ([CXC-MCP-INVOKE-READ]). If the read returned the same shape
    either way it would be a fixed answer, not a measurement; this proves it is
    discriminating. (A late MCP read before the first tick is the production shape
    of the absent case; the immediate-snapshot start makes it unreachable in a
    live-sensor test, so the absent state is manufactured directly here.)"""
    srv = ComposeServer()
    try:
        plugin = StorageMonitorPlugin(config={"path": "/tmp"}, measure=_fixed_measure)
        srv.add_tenant("buildhost", plugins=[plugin], start=True)

        conn = MCPConnector(registry=srv)
        peer = conn.new_peer()
        peer.select_tenant("buildhost")
        assert TOOL in [t["name"] for t in peer.list_tools()]

        # POSITIVE: the sensor published — present, with the exact value.
        before = peer.invoke(TOOL, {})
        assert before["present"] is True and before["value"] == FIXED_FREE

        # Manufacture the "value not believable" state via the freshness gate —
        # a wire-unknown sentinel, one of the three "no value yet" cases
        # `_read_retained` honors ([CXC-MCP-INVOKE-READ]). (interval is the
        # default 30 s, so the daemon tick does not overwrite it within the test.)
        bus = srv.tenant_bus("buildhost")
        bus.namespaces[topics.FREE_BYTES].mark_received(wire_unknown=True)

        after = peer.invoke(TOOL, {})
        assert after["ok"] is True
        assert after["present"] is False
        assert after.get("value") is None
    finally:
        _stop_all(srv)


# --------------------------------------------------------------------------- #
# 5. BEHAVIOUR PRESERVED — the plugin publishes REAL df, retained, on the
#    frozen namespace, exactly as the standalone sensor did.
# --------------------------------------------------------------------------- #
def test_plugin_publishes_real_free_bytes_on_the_frozen_namespace():
    """With no injected measure the plugin uses the extant service's REAL
    measurement (shutil.disk_usage(path).free) — the fleet lesson made structural:
    a real statvfs of the live mount, close to a fresh direct reading."""
    srv = ComposeServer()
    try:
        plugin = StorageMonitorPlugin(config={"path": "/"})
        srv.add_tenant("buildhost", plugins=[plugin], start=True)

        value = srv.tenant_bus("buildhost").get_state(topics.FREE_BYTES)
        assert isinstance(value, int)
        assert value > 0
        direct = shutil.disk_usage("/").free
        assert abs(value - direct) < (1 << 30)  # within 1 GiB — moments apart
    finally:
        _stop_all(srv)


def _stop_all(srv: ComposeServer) -> None:
    """Join every tenant's plugin threads so no daemon tick outlives the test."""
    for tid in list(srv.tenants()):
        for plugin in srv.tenant_plugin_specs(tid):
            try:
                plugin.stop()
            except Exception:
                pass
