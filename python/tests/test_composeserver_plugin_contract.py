"""Locks the FROZEN ComposePlugin contract + the kernel standup/multi-tenancy/
multi-attach/oracle seams that Phase 1 imports.

[SPEC:CXC-COMPOSESERVER] [SPEC:CXC-COMPOSEBUS-KERNEL] /
[DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23].
"""
from __future__ import annotations

import pytest

from composeio.bus import ComposeIOBus
from composeio.plugin import ComposePlugin, Isolation, RequiresFloor
from composeio.composeserver import ComposeServer, standup
from composeio.transport_local import LocalTransport


class DemoConfig(ComposePlugin.Config):
    port: int = 8420
    label: str = "demo"


class DemoPlugin(ComposePlugin):
    class Config(DemoConfig):
        pass

    name = "demo"
    version = "2.1.0"
    default_loaded = True
    publishes = ["demo/out"]
    subscribes = ["demo/in"]
    requires = RequiresFloor(capabilities=["storage"])

    def start(self, bus):
        self._bus = bus
        self.started = True

    def stop(self):
        self.started = False
        super().stop()


# --- ONE DECLARATION -> THREE ARTIFACTS ----------------------------------- #
def test_one_declaration_derives_form_announce_registry():
    # config form is derived from the Config schema, not hand-written
    form = DemoPlugin.config_form()
    names = {f["name"] for f in form["fields"]}
    assert {"port", "label"} <= names

    # announce is derived from the same declaration
    ann = DemoPlugin().announce()
    assert ann.plugin == "demo" and ann.version == "2.1.0"
    assert ann.publishes == ["demo/out"] and ann.subscribes == ["demo/in"]
    assert ann.config_schema == DemoPlugin.config_schema()
    assert ann.isolation_floor == "inprocess"

    # registry entry is derived too — all three share the one schema
    entry = DemoPlugin.registry_entry()
    assert entry["name"] == "demo"
    assert entry["config_schema"] == ann.config_schema
    assert entry["requires"]["capabilities"] == ["storage"]


def test_config_coercion_from_dict_and_default():
    assert DemoPlugin().config.port == 8420
    assert DemoPlugin({"port": 9}).config.port == 9


# --- the requires: FLOOR: park-loud, name the unmet item ------------------ #
def test_requires_floor_parks_and_names_unmet():
    p = DemoPlugin()
    assert p.unmet_requirements(set()) == ["capability:storage"]
    assert p.unmet_requirements({"storage"}) == []


def test_ready_predicate_fails_closed_without_evaluator():
    class P(DemoPlugin):
        name = "needs_ready"
        requires = RequiresFloor(ready="cap.storage.up")

    unmet = P().unmet_requirements(set())
    assert unmet and "ready:" in unmet[0]


# --- the ISOLATION FLOOR: level >= floor; subprocess pin is HARD ---------- #
def test_isolation_floor_is_a_minimum():
    class InProc(DemoPlugin):
        name = "inp"
        isolation_floor = Isolation.INPROCESS

    class SubProc(DemoPlugin):
        name = "sub"
        isolation_floor = Isolation.SUBPROCESS

    # a floor of INPROCESS cannot FORCE inprocess — the server may raise it
    assert InProc().resolved_isolation(Isolation.SUBPROCESS) == Isolation.SUBPROCESS
    # a SUBPROCESS pin is honored as a hard minimum
    assert SubProc().resolved_isolation(None) == Isolation.SUBPROCESS
    assert SubProc().resolved_isolation(Isolation.INPROCESS) == Isolation.SUBPROCESS


# --- MULTI-ATTACH connectors per scope-class ------------------------------ #
def test_multi_attach_and_backward_compat_single():
    bus = ComposeIOBus()
    assert bus._global is None
    t1, t2 = LocalTransport(), LocalTransport()
    # historical single-attach write still works
    bus._global = t1
    assert bus._global is t1 and bus.connectors("global") == [t1]
    # multi-attach adds a second connector on the same scope-class
    bus.attach_connector(t2, "global")
    assert bus.connectors("global") == [t1, t2]
    # clearing via the single-value setter clears the class
    bus._global = None
    assert bus.connectors("global") == []


# --- STANDUP: bare bus -> attach connectors -> load plugins --------------- #
def test_standup_parks_plugin_with_unmet_floor():
    res = standup(bus_id="t", plugins=[DemoPlugin()], available_capabilities=set())
    assert "demo" in res.parked
    assert not res.loaded


def test_standup_loads_plugin_when_floor_met():
    p = DemoPlugin()
    res = standup(bus_id="t", plugins=[p], available_capabilities={"storage"})
    assert p in res.loaded and not res.parked
    assert getattr(p, "started", False) is True


def test_standup_subprocess_pin_recorded_not_started_inprocess():
    class SubPlugin(DemoPlugin):
        name = "sub"
        requires = RequiresFloor()  # floor met
        isolation_floor = Isolation.SUBPROCESS

    p = SubPlugin()
    res = standup(bus_id="t", plugins=[p])
    assert "sub" in res.subprocess_pending
    assert p not in res.loaded
    assert getattr(p, "started", False) is False


# --- MULTI-TENANCY: per-tenant buses, NO shared plugin instances ---------- #
def test_multi_tenancy_isolated_buses_and_dynamic_rebinding():
    srv = ComposeServer()
    a = srv.add_tenant("haven", plugins=[DemoPlugin()], capabilities=set())
    b = srv.add_tenant("bsg", plugins=[DemoPlugin()], capabilities={"storage"})

    # per-tenant roots are distinct objects
    assert srv.tenant_bus("haven") is not srv.tenant_bus("bsg")

    # haven parked (no storage), bsg loaded
    assert "demo" in srv.parked("haven")
    assert not srv.parked("bsg")

    # the binder unparks with NO restart when the capability arrives
    unparked = srv.provide_capability("haven", "storage")
    assert unparked == ["demo"]
    assert not srv.parked("haven")


def test_registry_is_per_tenant():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DemoPlugin()])
    reg = srv.registry()
    assert "demo" in reg["haven"]
    assert reg["haven"]["demo"]["version"] == "2.1.0"


# --- the LIVENESS-ORACLE HOOK --------------------------------------------- #
def test_liveness_oracle_hook_overrides_builtin_then_restores():
    bus = ComposeIOBus()
    # unset: the built-in receiver answers (a never-heard node is not alive)
    assert bus.node_is_alive("ghost") is False
    bus.set_liveness_oracle(lambda node: node == "ghost")
    assert bus.node_is_alive("ghost") is True
    assert bus.node_is_alive("other") is False
    bus.set_liveness_oracle(None)
    assert bus.node_is_alive("ghost") is False


# --- the ANNOUNCE-BIND SPINE: the LOADER publishes the derived announce ----- #
# [DEC-PHASE0-COMPOSESERVER-PUBLISHES-DERIVED-ANNOUNCE]
from composeio.namespace import Scope  # noqa: E402


def test_loader_publishes_derived_announce_at_load():
    p = DemoPlugin()
    res = standup(bus_id="haven", plugins=[p], available_capabilities={"storage"})
    bus = res.bus
    ann = bus.get_state("_server/plugins/demo/announce")
    st = bus.get_state("_server/plugins/demo/status")
    # the DERIVED payload reached the wire: config schema, publishes, subscribes,
    # requires, isolation — none of it hand-written by the plugin
    assert ann is not None
    assert ann["publishes"] == ["demo/out"]
    assert ann["subscribes"] == ["demo/in"]
    assert "config_schema" in ann and "port" in ann["config_schema"]["properties"]
    assert ann["requires"] == {"capabilities": ["storage"], "ready": None}
    assert ann["isolation_floor"] == "inprocess"
    assert st["status"] == "running"


def test_server_state_is_local_scope():
    res = standup(bus_id="haven", plugins=[DemoPlugin()],
                  available_capabilities={"storage"})
    bus = res.bus
    # LOCAL, never global — intra-server discovery, per-tenant (multi-tenancy lock)
    assert bus.get_namespace("_server/plugins/demo/announce").scope == Scope.LOCAL
    assert bus.get_namespace("_server/plugins/demo/status").scope == Scope.LOCAL
    assert bus.get_namespace("_server/status").scope == Scope.LOCAL


def test_negative_control_no_plugin_leaves_server_silent():
    # a lane with a publisher only when there is something to announce
    res = standup(bus_id="empty", plugins=[])
    plugin_ns = [n for n in res.bus.namespaces if n.startswith("_server/plugins/")]
    assert plugin_ns == []


def test_parked_plugin_status_shows_parked_with_unmet():
    res = standup(bus_id="t", plugins=[DemoPlugin()], available_capabilities=set())
    st = res.bus.get_state("_server/plugins/demo/status")
    assert st["status"] == "parked"
    assert st["unmet"] == ["capability:storage"]
    # a parked plugin STILL announces its derived surface (so a subscriber can
    # see what WOULD run) — the status is what marks it parked
    assert res.bus.get_state("_server/plugins/demo/announce") is not None


def test_server_state_is_per_tenant():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DemoPlugin()], capabilities={"storage"})
    srv.add_tenant("bsg", plugins=[DemoPlugin()], capabilities=set())
    haven = srv.tenant_bus("haven")
    bsg = srv.tenant_bus("bsg")
    # each tenant's _server/* lives on its OWN bus; no cross-tenant leakage
    assert haven.get_state("_server/plugins/demo/status")["status"] == "running"
    assert bsg.get_state("_server/plugins/demo/status")["status"] == "parked"
    assert haven is not bsg


def test_on_change_reannounce_flips_status_on_unpark():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DemoPlugin()], capabilities=set())
    bus = srv.tenant_bus("haven")
    assert bus.get_state("_server/plugins/demo/status")["status"] == "parked"
    srv.provide_capability("haven", "storage")
    # the binder re-announced on the surface change
    assert bus.get_state("_server/plugins/demo/status")["status"] == "running"
    assert bus.get_state("_server/plugins/demo/status")["unmet"] == []


def test_periodic_announce_tick_attaches_and_detaches():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DemoPlugin()], capabilities={"storage"})
    tenant = srv._tenants["haven"]
    assert tenant._announce_tick is None
    srv.start("haven")           # start installs the periodic BusClock tick
    assert tenant._announce_tick is not None
    srv.stop("haven")            # stop detaches it
    assert tenant._announce_tick is None
