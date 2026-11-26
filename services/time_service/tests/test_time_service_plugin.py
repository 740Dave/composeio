"""TimeBasePublisherPlugin — the WAVE-2b repoint of time_service onto the
ComposeServer plugin contract, verified end-to-end in-process (no MQTT, no
broker, no sockets).

    PYTHONPATH=python:services python3 -m pytest services/time_service/tests -v

The four acceptance legs ([SPEC:CXC-COMPOSESERVER], [SPEC:CXC-MCP]):
  * the loader DISCOVERS + LOADS the plugin from a manifest dotted ``ref``;
  * a bad config PARKS it BEFORE instantiation (never a start()-time raise);
  * it ANNOUNCES on the tenant bus (``time/base`` + the derived MCP tool);
  * its MCP tool appears in the umbrella's aggregated list and ROUND-TRIPS.

Every check carries its NEGATIVE CONTROL ([CXC-VISION-EPISTEMICS]): an
instrument that can only report success is not an instrument. The load-bearing
one is the cross-tenant gate — a tenant WITHOUT the plugin must neither list nor
reach the tool.
"""
from __future__ import annotations

import time

import pytest

from composeio.bus import ComposeIOBus
from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import load_tenant_manifest
from composeio.transport_mcp import MCPConnector, ToolNotFoundError

from time_service import topics
from time_service.plugin import TimeBasePublisherPlugin

REF = "time_service.plugin:TimeBasePublisherPlugin"
PLUGIN = "time_base_publisher"
TOOL = "time_base_publisher.get_current_bus_time_base_epoch_seconds"


@pytest.fixture
def server():
    srv = ComposeServer()
    yield srv
    # kill every tenant's tick thread so a daemon publisher never outlives a test.
    srv.stop()


def _peer(server, tenant=None):
    peer = MCPConnector(registry=server).new_peer()
    if tenant is not None:
        peer.select_tenant(tenant)
    return peer


# --------------------------------------------------------------------------- #
# LEG 1 — discovery + load via the loader's dotted ref ([CXC-CSL-MANIFEST-SHAPE])
# --------------------------------------------------------------------------- #
def test_loader_discovers_and_loads_the_plugin_by_dotted_ref(server):
    manifest = {"plugins": [{"plugin": REF, "config": {"tick_hz": 4.0}}]}
    result = load_tenant_manifest(server, "haven", manifest)

    assert result.is_clean(), (result.standup.parked, result.config_parked)
    assert [p.name for p in result.standup.loaded] == [PLUGIN]

    # the repoint itself: the plugin published time/base onto the TENANT bus, as
    # a retained int epoch, with no transport of its own.
    bus = server.tenant_bus("haven")
    value = bus.get_state(topics.BASE)
    assert isinstance(value, int) and value > 0


def test_loader_parks_a_bogus_ref_and_never_raises(server):
    # NEGATIVE CONTROL: an unresolvable ref is a NAMED `resolve:` park, not a
    # crash, and it does not stop a sibling entry from loading.
    manifest = {"plugins": [
        {"plugin": "time_service.plugin:NoSuchClass"},
        {"plugin": REF},
    ]}
    result = load_tenant_manifest(server, "haven", manifest)
    assert "time_service.plugin:NoSuchClass" in result.config_parked
    assert result.config_parked["time_service.plugin:NoSuchClass"][0].startswith("resolve:")
    assert [p.name for p in result.standup.loaded] == [PLUGIN]


# --------------------------------------------------------------------------- #
# LEG 2 — config validated BEFORE instantiation ([CXC-CSL-CONFIG-VALIDATION])
# --------------------------------------------------------------------------- #
def test_a_non_positive_tick_hz_parks_config_before_instantiation(server):
    manifest = {"plugins": [{"plugin": REF, "config": {"tick_hz": 0}}]}
    result = load_tenant_manifest(server, "haven", manifest)
    # a `config:` park keyed by plugin name; the plugin is NOT loaded/started.
    assert PLUGIN in result.config_parked
    assert any(r.startswith("config:tick_hz") for r in result.config_parked[PLUGIN])
    assert [p.name for p in result.standup.loaded] == []
    # and nothing was published — no half-started clock on the tenant bus.
    assert server.tenant_bus("haven").get_state(topics.BASE) is None


def test_an_unknown_base_mode_parks_config(server):
    manifest = {"plugins": [{"plugin": REF, "config": {"base_mode": "warp"}}]}
    result = load_tenant_manifest(server, "haven", manifest)
    assert PLUGIN in result.config_parked
    assert any(r.startswith("config:base_mode") for r in result.config_parked[PLUGIN])


def test_a_valid_config_is_the_negative_control_and_loads_clean(server):
    # NEGATIVE CONTROL for the two park tests above: the SAME path with a valid
    # config loads clean, proving the park was the config, not the path.
    result = load_tenant_manifest(
        server, "haven", {"plugins": [{"plugin": REF, "config": {"tick_hz": 2.0}}]})
    assert not result.config_parked
    assert [p.name for p in result.standup.loaded] == [PLUGIN]


# --------------------------------------------------------------------------- #
# LEG 3 — announce on the tenant bus ([CXC-CS-PLUGIN-CONTRACT] derived announce)
# --------------------------------------------------------------------------- #
def test_announce_names_time_base_and_the_derived_mcp_tool(server):
    load_tenant_manifest(server, "haven", {"plugins": [{"plugin": REF}]})
    announce = server.tenant_bus("haven").get_state(
        f"_server/plugins/{PLUGIN}/announce")

    assert announce is not None
    assert announce["plugin"] == PLUGIN
    assert topics.BASE in announce["publishes"]
    # idle bodies realize in the default idle mode -> they are in the live surface.
    assert topics.body("ship") in announce["publishes"]
    # the FOURTH derived artifact: the MCP tool, exposed dot-namespaced.
    tool_names = {t["name"] for t in announce["mcp_tools"]}
    assert TOOL in tool_names
    # NEGATIVE CONTROL: a namespace this plugin never touches is NOT announced.
    assert "time/kobol" not in announce["publishes"]


# --------------------------------------------------------------------------- #
# LEG 4 — the MCP umbrella lists + round-trips the retained read
# --------------------------------------------------------------------------- #
def test_umbrella_lists_and_round_trips_the_retained_read(server):
    load_tenant_manifest(server, "haven", {"plugins": [{"plugin": REF}]})
    peer = _peer(server, "haven")

    assert TOOL in {t["name"] for t in peer.list_tools()}

    r = peer.invoke(TOOL, {})
    assert r["ok"] and r["present"]
    # the tool returns exactly the retained time/base value, verbatim.
    assert r["value"] == server.tenant_bus("haven").get_state(topics.BASE)
    assert isinstance(r["value"], int)


def test_no_cross_tenant_reach_to_the_tool(server):
    # THE LOAD-BEARING GATE ([CXC-MCP-BOUNDARIES] no cross-tenant dispatch): the
    # plugin is on `haven` only. A `bsg`-bound session must neither LIST nor
    # INVOKE it — "tool not found", never a cross-tenant read.
    load_tenant_manifest(server, "haven", {"plugins": [{"plugin": REF}]})
    server.add_tenant("bsg", plugins=[])  # bsg has NO time_base_publisher

    peer = _peer(server, "bsg")
    assert TOOL not in {t["name"] for t in peer.list_tools()}
    with pytest.raises(ToolNotFoundError):
        peer.invoke(TOOL, {})


def test_exposed_schema_takes_no_arguments_and_forbids_extras(server):
    # The read is a pure snapshot: the schema an Intelligence sees carries NO
    # required params and forbids stray fields (extra='forbid' ->
    # additionalProperties: false), so a client cannot be misled into believing
    # an argument steers the clock. (The RETAINED read path ignores args by
    # framework design; the schema is where the "no args" contract is stated.)
    load_tenant_manifest(server, "haven", {"plugins": [{"plugin": REF}]})
    peer = _peer(server, "haven")
    tool = next(t for t in peer.list_tools() if t["name"] == TOOL)
    schema = tool["inputSchema"]
    assert schema.get("additionalProperties") is False
    assert not schema.get("required")   # NEGATIVE CONTROL: no required argument


# --------------------------------------------------------------------------- #
# behaviour preservation — the wrapped service is byte-faithful
# --------------------------------------------------------------------------- #
def test_direct_start_preserves_retained_int_epoch_and_idle_bodies():
    bus = ComposeIOBus()
    plugin = TimeBasePublisherPlugin()
    try:
        plugin.start(bus)
        base = bus.get_state(topics.BASE)
        assert isinstance(base, int)
        assert abs(base - int(time.time())) < 5   # a real wall clock, not a stub
        # idle bodies realize off the base they hear ([CXC-TIME-BODY]).
        assert bus.get_state(topics.body("ship")) is not None
    finally:
        plugin.stop()


def test_game_mode_publishes_no_idle_body():
    # NEGATIVE CONTROL / preserved gate: in game mode the idle offset table is
    # replaced, so NO idle body publishes ([DD-TIME-BODY-UNIFICATION], QA note 2).
    bus = ComposeIOBus()
    plugin = TimeBasePublisherPlugin(config={"base_mode": "game"})
    try:
        plugin.start(bus)
        assert bus.get_state(topics.BASE) is not None
        assert bus.get_state(topics.body("ship")) is None
        # the live surface reflects it: game mode announces time/base alone.
        assert plugin.current_publishes() == [topics.BASE]
    finally:
        plugin.stop()
