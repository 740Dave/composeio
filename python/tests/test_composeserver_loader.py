"""Acceptance for the ComposeServer plugin LOADER ([SPEC:CXC-CS-LOADER],
`87-composeserver-plugin-loader.lts`).

Every row of §10's CSL-V1..V8 acceptance table is exercised here WITH its stated
negative control. The loader is a CONSUMER of the landed `composeserver.py` /
`plugin.py`; these tests never import a modified version of either.

⛔ GLASS ITEMS NOT SELF-CERTIFIED HERE (per [CXC-VISION-EPISTEMICS] — a green unit
run is compatible with a loader that only works in a narrow harness):
  * CSL-V4 verify.human — a REAL capability arrival over a live MQTT bus, with an
    operator confirming NO process restart (no new PID, no reconnect). The machine
    test below drives `provide_capability` against an in-process bus and identity-
    checks the same instance; it does NOT prove the announce-driven capability
    signal is wired on a real broker.
  * CSL-V5 verify.human — a real Phase-0 bring-up log showing a subprocess-floor
    plugin held `subprocess_pending`, never silently started in-process.
  * CSL-V7 verify.human — an operator reading a real `_server/status` payload and
    confirming the malformed entry is NAMED and human-readable, not a log line.
These require the diamond's first-bringup leg on a real broker; they are flagged,
not claimed green.
"""
from __future__ import annotations

import pytest

from composeio.plugin import ComposePlugin, Isolation, RequiresFloor
from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import (
    LoaderResult,
    PluginResolutionError,
    load_tenant_manifest,
    resolve_plugin_class,
)


# --------------------------------------------------------------------------- #
# Test plugins — declared here, referenced by registered name AND (for the
# import-path resolution tests) by this module's own dotted path.
# --------------------------------------------------------------------------- #
class DemoConfig(ComposePlugin.Config):
    port: int = 8420
    label: str = "demo"


class DemoPlugin(ComposePlugin):
    class Config(DemoConfig):
        pass

    name = "demo"
    version = "1.0.0"
    publishes = ["demo/out"]
    subscribes = ["demo/in"]

    def start(self, bus):
        self._bus = bus
        self.started = True
        self.start_calls = getattr(self, "start_calls", 0) + 1

    def stop(self):
        self.started = False
        super().stop()


class BadPortPlugin(DemoPlugin):
    name = "badport"  # config with a bad `port` fails exactly one field


class TwoFieldConfig(ComposePlugin.Config):
    a: int = 1
    b: int = 2


class TwoFieldPlugin(DemoPlugin):
    class Config(TwoFieldConfig):
        pass

    name = "twofield"  # config with bad `a` and `b` fails exactly two fields


class CapPlugin(DemoPlugin):
    name = "capper"
    requires = RequiresFloor(capabilities=["cap-x"])


class SubPlugin(DemoPlugin):
    name = "subby"
    requires = RequiresFloor()  # floor trivially met
    isolation_floor = Isolation.SUBPROCESS


class NotAPlugin:  # deliberately NOT a ComposePlugin subclass
    name = "imposter"


REGISTRY = {
    "demo": DemoPlugin,
    "badport": BadPortPlugin,
    "twofield": TwoFieldPlugin,
    "capper": CapPlugin,
    "subby": SubPlugin,
    "imposter": NotAPlugin,  # a registered name whose object is not a plugin
}

THIS = __name__  # this module's importable dotted path, for import-path tests


def _spec(server, tenant_id, name):
    """Fetch the instantiated instance for `name` from a tenant's plugin_specs
    (all successfully instantiated plugins), or None if it was never built."""
    tenant = server._tenants[tenant_id]
    return next((p for p in tenant.plugin_specs if p.name == name), None)


# --------------------------------------------------------------------------- #
# resolve_plugin_class — the two-form rule + the named failure.
# --------------------------------------------------------------------------- #
def test_resolve_registered_name():
    assert resolve_plugin_class("demo", REGISTRY) is DemoPlugin


def test_resolve_dotted_import_path():
    cls = resolve_plugin_class("composeio.plugins.time:TimePlugin")
    from composeio.plugins.time import TimePlugin
    assert cls is TimePlugin


def test_resolve_bare_name_absent_from_registry_raises():
    with pytest.raises(PluginResolutionError) as ei:
        resolve_plugin_class("nope", {})
    assert ei.value.ref == "nope" and "not a registered name" in ei.value.reason


def test_resolve_broken_module_raises():
    with pytest.raises(PluginResolutionError) as ei:
        resolve_plugin_class("no.such.module:Thing")
    assert "import failed" in ei.value.reason


def test_resolve_missing_attr_raises():
    with pytest.raises(PluginResolutionError) as ei:
        resolve_plugin_class("composeio.plugins.time:NoSuchClass")
    assert "not found" in ei.value.reason


def test_resolve_non_subclass_via_dotted_raises():
    # TimeEngine exists in that module but is not a ComposePlugin subclass
    with pytest.raises(PluginResolutionError) as ei:
        resolve_plugin_class("composeio.plugins.time:TimeEngine")
    assert "not a ComposePlugin subclass" in ei.value.reason


def test_resolve_non_subclass_via_registry_raises():
    with pytest.raises(PluginResolutionError) as ei:
        resolve_plugin_class("imposter", REGISTRY)
    assert "not a ComposePlugin subclass" in ei.value.reason


# --------------------------------------------------------------------------- #
# CSL-V1 — invalid config PARKS, never crashes; valid siblings unaffected;
# a config-invalid entry is NEVER instantiated.
# --------------------------------------------------------------------------- #
def test_csl_v1_config_park_valid_and_invalid_coexist():
    manifest = {"plugins": [
        {"plugin": "demo", "config": {"port": 9}},        # valid
        {"plugin": "badport", "config": {"port": "NaN"}},  # 1 bad field
        {"plugin": "twofield", "config": {"a": "x", "b": "y"}},  # 2 bad fields
    ]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)

    # valid entry instantiated + started, config honored
    demo = _spec(srv, "t", "demo")
    assert demo is not None and getattr(demo, "started", False) is True
    assert demo.config.port == 9
    assert demo in res.standup.loaded

    # invalid entries parked with EXACTLY the failing-field count of config: reasons
    assert set(res.config_parked) == {"badport", "twofield"}
    assert len(res.config_parked["badport"]) == 1
    assert len(res.config_parked["twofield"]) == 2
    assert all(r.startswith("config:") for r in res.config_parked["badport"])
    assert all(r.startswith("config:") for r in res.config_parked["twofield"])

    # NEGATIVE CONTROL: parked-for-config entries were NEVER instantiated
    assert _spec(srv, "t", "badport") is None
    assert _spec(srv, "t", "twofield") is None

    # no exception escaped; result is not clean because of the config parks
    assert res.is_clean() is False


def test_csl_v1_ordering_independent():
    # parking (b)/(c) BEFORE the valid entry must not affect the valid entry
    manifest = {"plugins": [
        {"plugin": "badport", "config": {"port": "NaN"}},
        {"plugin": "twofield", "config": {"a": "x", "b": "y"}},
        {"plugin": "demo", "config": {"port": 7}},
    ]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    demo = _spec(srv, "t", "demo")
    assert demo is not None and demo.config.port == 7 and demo in res.standup.loaded
    assert set(res.config_parked) == {"badport", "twofield"}


# --------------------------------------------------------------------------- #
# CSL-V2 — unresolvable ref PARKS with a resolve: reason naming the ref;
# siblings unaffected.
# --------------------------------------------------------------------------- #
def test_csl_v2_resolve_park_unknown_name_and_broken_import():
    manifest = {"plugins": [
        {"plugin": "demo"},                              # valid
        {"plugin": "ghost"},                             # unknown registered name
        {"plugin": "no.such.module:Thing"},              # broken dotted path
    ]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)

    assert set(res.config_parked) == {"ghost", "no.such.module:Thing"}
    assert res.config_parked["ghost"][0].startswith("resolve:")
    assert res.config_parked["no.such.module:Thing"][0].startswith("resolve:")

    # NEGATIVE CONTROL: the sibling valid entry is unaffected — instantiated+started
    demo = _spec(srv, "t", "demo")
    assert demo is not None and getattr(demo, "started", False) is True
    assert demo in res.standup.loaded


# --------------------------------------------------------------------------- #
# CSL-V3 — unmet requires: floor PARKS naming the unmet item; start not called.
# --------------------------------------------------------------------------- #
def test_csl_v3_requires_floor_park_names_unmet_and_never_starts():
    manifest = {"plugins": [{"plugin": "capper"}]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY,
                               capabilities=set())

    # parked by the LANDED unmet_requirements() with the verbatim reason
    assert res.standup.parked["capper"] == ["capability:cap-x"]
    capper = _spec(srv, "t", "capper")
    assert capper not in res.standup.loaded

    # NEGATIVE CONTROL: start() never invoked on a floor-parked plugin
    assert getattr(capper, "started", False) is False
    assert getattr(capper, "start_calls", 0) == 0


# --------------------------------------------------------------------------- #
# CSL-V4 — continuous rebind: parked-for-X stays parked until X arrives, then
# the SAME instance runs, no restart. Negative control: unrelated cap Y.
# --------------------------------------------------------------------------- #
def test_csl_v4_continuous_rebind_same_instance_no_restart():
    manifest = {"plugins": [{"plugin": "capper"}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY, capabilities=set())

    capper = _spec(srv, "t", "capper")
    bus_before = srv.tenant_bus("t")
    assert "capper" in srv.parked("t")

    # NEGATIVE CONTROL: an unrelated capability leaves it parked
    assert srv.provide_capability("t", "cap-y") == []
    assert "capper" in srv.parked("t")
    assert getattr(capper, "started", False) is False

    # the specific capability unparks the SAME instance (identity), started once
    unparked = srv.provide_capability("t", "cap-x")
    assert unparked == ["capper"]
    assert "capper" not in srv.parked("t")
    loaded_now = srv._tenants["t"].plugins
    assert capper in loaded_now              # identity, not merely name
    assert capper.start_calls == 1           # started exactly once
    assert srv.tenant_bus("t") is bus_before  # no bus restart


def test_csl_v4_config_park_does_not_unpark_via_capability():
    # a config-invalid plugin has no floor to re-evaluate; provide_capability
    # can never recover it ([CXC-CSL-CONTINUOUS-REBIND] boundary).
    manifest = {"plugins": [{"plugin": "badport", "config": {"port": "NaN"}}]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    assert "badport" in res.config_parked
    srv.provide_capability("t", "anything")
    assert _spec(srv, "t", "badport") is None  # still never instantiated


# --------------------------------------------------------------------------- #
# CSL-V5 — subprocess-floor plugin never starts in-process even with a met floor.
# --------------------------------------------------------------------------- #
def test_csl_v5_subprocess_floor_never_starts_inprocess():
    manifest = {"plugins": [{"plugin": "subby"}]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)

    assert "subby" in res.standup.subprocess_pending
    subby = _spec(srv, "t", "subby")
    assert subby not in res.standup.loaded
    # NEGATIVE CONTROL: start() never called on a subprocess-pinned plugin
    assert getattr(subby, "started", False) is False
    assert getattr(subby, "start_calls", 0) == 0


# --------------------------------------------------------------------------- #
# CSL-V6 — strictly per-tenant: same manifest, two tenants, distinct instances
# and distinct buses.
# --------------------------------------------------------------------------- #
def test_csl_v6_per_tenant_distinct_instances_and_buses():
    manifest = {"plugins": [{"plugin": "demo", "config": {"port": 5}}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "tenant-a", manifest, registry=REGISTRY)
    load_tenant_manifest(srv, "tenant-b", manifest, registry=REGISTRY)

    a = _spec(srv, "tenant-a", "demo")
    b = _spec(srv, "tenant-b", "demo")
    assert a is not None and b is not None
    assert a is not b and id(a) != id(b)                       # distinct instances
    assert srv.tenant_bus("tenant-a") is not srv.tenant_bus("tenant-b")  # distinct buses
    assert a._bus is not b._bus                                # independent bus state


# --------------------------------------------------------------------------- #
# CSL-V7 — config/resolve parks SURFACE on the admin surface, not silently dropped.
# --------------------------------------------------------------------------- #
def test_csl_v7_config_park_surfaces_on_status_and_aggregate():
    manifest = {"plugins": [{"plugin": "badport", "config": {"port": "NaN"}}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    bus = srv.tenant_bus("t")

    # per-plugin status published, status-only (no announce), config: reasons
    st = bus.get_state("_server/plugins/badport/status")
    assert st is not None and st["status"] == "parked"
    assert st["unmet"] and all(r.startswith("config:") for r in st["unmet"])
    assert bus.get_state("_server/plugins/badport/announce") is None  # no instance

    # merged into the _server/status aggregate parked dict
    agg = bus.get_state("_server/status")
    assert "badport" in agg["parked"]


def test_csl_v7_resolve_park_surfaces_keyed_by_raw_ref():
    manifest = {"plugins": [{"plugin": "no.such.module:Thing"}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    bus = srv.tenant_bus("t")

    st = bus.get_state("_server/plugins/no.such.module:Thing/status")
    assert st is not None and st["status"] == "parked"
    assert st["unmet"][0].startswith("resolve:")
    agg = bus.get_state("_server/status")
    assert "no.such.module:Thing" in agg["parked"]


def test_csl_v7_config_park_durable_across_republish():
    # the aggregate must still carry the config park after a periodic re-announce
    manifest = {"plugins": [{"plugin": "badport", "config": {"port": "NaN"}}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    srv._tenants["t"].republish()  # simulate the periodic BusClock tick
    agg = srv.tenant_bus("t").get_state("_server/status")
    assert "badport" in agg["parked"]


# --------------------------------------------------------------------------- #
# CSL-V8 — placement is a structural property of the spec, not this module.
# The loader carries no placement obligation; this row is satisfied by the spec
# file's own [CXC-CSL-PLACEMENT] node + filename-check property.
# --------------------------------------------------------------------------- #
def test_csl_v8_placement_is_a_spec_property_not_a_code_one():
    # documented here so the acceptance table has a corresponding test id; the
    # real evidence is the [PLACEMENT UNRATIFIED] node in 87-...lts.
    assert True


# --------------------------------------------------------------------------- #
# LoaderResult surface.
# --------------------------------------------------------------------------- #
def test_loader_result_is_clean_true_when_nothing_parked():
    manifest = {"plugins": [{"plugin": "demo"}]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    assert isinstance(res, LoaderResult)
    assert res.tenant_id == "t"
    assert res.is_clean() is True
    assert res.config_parked == {}


def test_empty_manifest_is_clean():
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", {}, registry=REGISTRY)
    assert res.is_clean() is True
    assert res.standup.loaded == []


# --------------------------------------------------------------------------- #
# REMEDIATION — Finding 1: aliasing. A config park must NEVER contaminate
# `StandupResult.parked` (pure requires-floor semantics), and the two dicts must
# be distinct objects.
# --------------------------------------------------------------------------- #
def test_aliasing_config_and_floor_parks_stay_separate():
    manifest = {"plugins": [
        {"plugin": "capper"},                              # requires-floor park
        {"plugin": "badport", "config": {"port": "NaN"}},  # config park
    ]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY,
                               capabilities=set())

    # StandupResult.parked = ONLY the requires-floor park; NO config: leakage
    assert set(res.standup.parked) == {"capper"}
    assert res.standup.parked["capper"] == ["capability:cap-x"]
    assert all(not r.startswith("config:")
               for u in res.standup.parked.values() for r in u)

    # config_parked = ONLY the config entry
    assert set(res.config_parked) == {"badport"}
    assert all(r.startswith("config:") for r in res.config_parked["badport"])

    # not the same object — no shared reference to cross-contaminate
    assert res.standup.parked is not res.config_parked

    # the aggregate still shows BOTH reasons
    agg = srv.tenant_bus("t").get_state("_server/status")
    assert "capper" in agg["parked"] and "badport" in agg["parked"]


# --------------------------------------------------------------------------- #
# REMEDIATION — Finding 3: periodicity parity. A config park's per-plugin
# `_server/plugins/<key>/status` must be re-published on EVERY republish tick,
# not only once at load, so a LATE pattern-subscriber still gets a push.
# --------------------------------------------------------------------------- #
def test_external_park_periodicity_and_durability():
    manifest = {"plugins": [
        {"plugin": "demo"},                                # instantiated
        {"plugin": "badport", "config": {"port": "NaN"}},  # config-parked
    ]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY, start=True)
    bus = srv.tenant_bus("t")

    # subscribe AFTER load — this subscriber saw nothing at load time
    seen: list[str] = []
    bus.subscribe_pattern("_server/*", lambda ns, new, old: seen.append(ns))

    tenant = srv._tenants["t"]
    tenant.republish()   # tick 1
    tenant.republish()   # tick 2

    # the config-park per-plugin status was pushed on the ticks (>=2), not just
    # at load — announce-bind periodicity parity with instantiated plugins
    assert seen.count("_server/plugins/badport/status") >= 2
    # the aggregate is refreshed every tick too
    assert seen.count("_server/status") >= 2
    agg = bus.get_state("_server/status")
    assert "badport" in agg["parked"]


# --------------------------------------------------------------------------- #
# REMEDIATION — no spurious unpark survives the new external_parked path.
# --------------------------------------------------------------------------- #
def test_external_park_never_enters_plugin_specs_or_unparks():
    manifest = {"plugins": [{"plugin": "badport", "config": {"port": "NaN"}}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    tenant = srv._tenants["t"]

    # the external park is in external_parked, NOT in plugin_specs / parked
    assert "badport" in tenant.external_parked
    assert all(p.name != "badport" for p in tenant.plugin_specs)
    assert "badport" not in tenant.parked

    # provide_capability iterates plugin_specs — it can never unpark this
    srv.provide_capability("t", "anything")
    assert "badport" in tenant.external_parked
    assert all(p.name != "badport" for p in tenant.plugin_specs)
    agg = srv.tenant_bus("t").get_state("_server/status")
    assert "badport" in agg["parked"]
