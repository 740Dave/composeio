"""Runtime bringup smoke tests for the ComposeServer plugin LOADER
([SPEC:CXC-CS-LOADER], `87-composeserver-plugin-loader.lts`).

Written by `composeio-first-bringup`, NOT the builder — this file exists to
close a specific gap in the builder's own suite (`test_composeserver_loader.py`):
every assertion there reads state back through `bus.get_state(...)`, the
namespace's OWN cache, written by the SAME call that is under test. Per
[CXC-VISION-EPISTEMICS] `rule-self-verification`, a readback through the same
assumption that wrote it is not independent evidence.

This file adds the OTHER vantage: a REAL subscriber registered on the bus's
actual pub/sub dispatch (`subscribe_pattern`), so "the message reached a
non-empty subscriber set" is demonstrated by something actually receiving it
on the wire — not merely present in a dict. It also drives MULTIPLE real
`republish()` ticks (the shape a real ~5s BusClock cadence takes in
production — verified separately, live, in a standalone process; see the
bringup verdict) to check the [CXC-CSL-STATUS-SURFACE] FINDING: does a
config/resolve park actually SURVIVE repeated periodic re-announce without
spuriously unparking or acquiring an `.announce()` it has no instance to
back?

Every test states its negative control inline. One test additionally injects
a real fault to prove the harness is not a certifier — see
`test_negative_control_harness_detects_a_broken_durability_mechanism`.
"""
from __future__ import annotations

import pytest

from composeio.plugin import ComposePlugin, Isolation, RequiresFloor
from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import load_tenant_manifest


class DemoConfig(ComposePlugin.Config):
    port: int = 8420


class DemoPlugin(ComposePlugin):
    class Config(DemoConfig):
        pass

    name = "demo"
    version = "1.0.0"
    publishes = ["demo/out"]

    def start(self, bus):
        self._bus = bus
        self.started = True
        self.start_calls = getattr(self, "start_calls", 0) + 1


class BadPortPlugin(DemoPlugin):
    name = "badport"


class CapPlugin(DemoPlugin):
    name = "capper"
    requires = RequiresFloor(capabilities=["cap-x"])


class SubPlugin(DemoPlugin):
    name = "subby"
    requires = RequiresFloor()
    isolation_floor = Isolation.SUBPROCESS


REGISTRY = {
    "demo": DemoPlugin, "badport": BadPortPlugin,
    "capper": CapPlugin, "subby": SubPlugin,
}


def _spec(server, tenant_id, name):
    tenant = server._tenants[tenant_id]
    return next((p for p in tenant.plugin_specs if p.name == name), None)


class _Collector:
    """A real bus subscriber — records EVERY delivery it actually receives,
    not what a cache says is present."""

    def __init__(self):
        self.events: list[tuple[str, object]] = []

    def __call__(self, namespace, new_value, old_value):
        self.events.append((namespace, new_value))

    def namespaces_seen(self) -> set[str]:
        return {ns for ns, _ in self.events}

    def values_for(self, namespace: str) -> list[object]:
        return [v for ns, v in self.events if ns == namespace]


# --------------------------------------------------------------------------- #
# 1. Real dispatch — config park + valid sibling, across REPEATED real ticks.
#    This is the [CXC-CSL-STATUS-SURFACE] FINDING check: does the aggregate
#    park survive periodic republish untouched, and does the per-plugin
#    namespace for a NEVER-INSTANTIATED park never get a spurious announce?
# --------------------------------------------------------------------------- #
def test_finding_config_park_survives_repeated_real_republish_ticks_no_spurious_announce():
    manifest = {"plugins": [
        {"plugin": "demo", "config": {"port": 5}},          # valid control
        {"plugin": "badport", "config": {"port": "NaN"}},    # config park
    ]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    tenant = srv._tenants["t"]
    bus = srv.tenant_bus("t")

    collector = _Collector()
    bus.subscribe_pattern("_server/*", collector)

    # Drive FIVE real republish ticks — the exact call the ~5s BusClock makes
    # in production ([CXC-CS] `_Tenant.republish`), each one independent, none
    # simulated by re-invoking the loader.
    for _ in range(5):
        tenant.republish()

    # POSITIVE: the always-running sibling gets a fresh push on every tick —
    # proves the subscriber mechanism is alive and this is not a dead wire.
    assert len(collector.values_for("_server/plugins/demo/status")) == 5
    assert len(collector.values_for("_server/status")) == 5

    # The config park SURVIVES every tick, in the private tenant state the
    # dispatch's FINDING targets directly, AND in what actually got published.
    # (Post-remediation: config/resolve parks live in the SEPARATE
    # `external_parked` dict, never the requires-floor `parked` — Finding 1.
    # They still merge into the published `_server/status.parked` aggregate.)
    assert "badport" in tenant.external_parked
    assert "badport" not in tenant.parked  # NEVER contaminates requires-floor parks
    for value in collector.values_for("_server/status"):
        assert "badport" in value["parked"]

    # NEGATIVE CONTROL: badport was NEVER instantiated, so it must NEVER
    # receive a `.announce()`-derived publish — not once, across all 5 ticks.
    # (`plugin.announce()` requires a constructed instance; badport has none
    # in `plugin_specs`, so a regression that tried to announce it would
    # raise, not silently succeed — this assertion is not a coincidence of
    # wiring, it is structurally enforced by `publish_server_state` iterating
    # `plugin_specs` only.)
    assert "_server/plugins/badport/announce" not in collector.namespaces_seen()
    assert bus.get_state("_server/plugins/badport/announce") is None

    # And it never spuriously unparks: still parked at the very end.
    assert "badport" in tenant.external_parked
    assert _spec(srv, "t", "badport") is None  # still never instantiated


def test_negative_control_harness_detects_a_broken_durability_mechanism():
    """Fault injection: proves the assertion above is a real instrument.

    If [CXC-CSL-STATUS-SURFACE]'s durability mechanism regressed — the merge
    into `tenant.external_parked` silently stopped happening, or something
    dropped the key before a republish — this is EXACTLY what that would look
    like. This test manufactures that regression directly and confirms the SAME
    shape of assertion used above now reads FALSE. If it read TRUE here too, the
    positive test would be a certifier (same output whether the mechanism
    works or not); it is not.
    """
    manifest = {"plugins": [{"plugin": "badport", "config": {"port": "NaN"}}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    tenant = srv._tenants["t"]
    assert "badport" in tenant.external_parked  # sanity: real mechanism placed it

    # Simulate the regression: something drops the config park from the
    # tenant's own state before the next periodic tick.
    del tenant.external_parked["badport"]
    tenant.republish()

    agg = srv.tenant_bus("t").get_state("_server/status")
    assert "badport" not in agg["parked"], (
        "the durability assertion is discriminating: removing the park "
        "changes what republish() emits, so its presence in the positive "
        "test is real evidence, not a fixed 'always true' readback.")


# --------------------------------------------------------------------------- #
# 2. Real dispatch — CSL-V4 continuous rebind: SAME instance, no restart,
#    observed via an ACTUAL subscriber delivery, not a cache read.
# --------------------------------------------------------------------------- #
def test_real_dispatch_capability_arrival_same_instance_no_restart():
    manifest = {"plugins": [{"plugin": "capper"}]}
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY, capabilities=set())

    capper_before = _spec(srv, "t", "capper")
    bus = srv.tenant_bus("t")
    bus_before = srv.tenant_bus("t")

    collector = _Collector()
    bus.subscribe_pattern("_server/plugins/capper/*", collector)

    # NEGATIVE CONTROL: an unrelated capability produces NO delivery at all —
    # `_reevaluate` only calls `tenant.republish()` when something actually
    # unparked; a no-op capability must not even push a spurious re-announce.
    assert srv.provide_capability("t", "cap-y") == []
    assert collector.events == []

    # The REAL capability arrival — observed as an actual delivered message,
    # not a get_state() poll.
    unparked = srv.provide_capability("t", "cap-x")
    assert unparked == ["capper"]

    statuses = collector.values_for("_server/plugins/capper/status")
    assert statuses, "no delivery reached the subscriber — dead wire"
    assert statuses[-1]["status"] == "running"
    assert statuses[-1]["unmet"] == []
    assert collector.values_for("_server/plugins/capper/announce"), (
        "capper's derived announce must actually be pushed to a live "
        "subscriber on the transition, not merely computable on demand")

    # SAME instance, no restart — identity-checked, not name-checked.
    capper_after = _spec(srv, "t", "capper")
    assert capper_after is capper_before
    assert capper_after.start_calls == 1
    assert srv.tenant_bus("t") is bus_before


# --------------------------------------------------------------------------- #
# 3. Real dispatch — CSL-V5 subprocess-pending: never transitions to running,
#    across repeated ticks AND unrelated capability arrivals, observed live.
# --------------------------------------------------------------------------- #
def test_real_dispatch_subprocess_pending_never_transitions_across_repeated_ticks():
    manifest = {"plugins": [{"plugin": "subby"}]}  # trivially met requires: floor
    srv = ComposeServer()
    load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    tenant = srv._tenants["t"]
    bus = srv.tenant_bus("t")

    collector = _Collector()
    bus.subscribe_pattern("_server/plugins/subby/*", collector)

    for _ in range(3):
        tenant.republish()
    srv.provide_capability("t", "some-unrelated-capability")
    srv.provide_capability("t", "another-one")

    statuses = collector.values_for("_server/plugins/subby/status")
    assert len(statuses) >= 3, "no live delivery observed — dead wire"
    # NEGATIVE CONTROL: every single delivered status, across every tick and
    # every capability arrival, stays subprocess_pending — never once flips
    # to running, which is what a "starts anyway" regression would produce.
    assert all(s["status"] == "subprocess_pending" for s in statuses)
    assert all(s["status"] != "running" for s in statuses)

    subby = _spec(srv, "t", "subby")
    assert getattr(subby, "started", False) is False
    assert getattr(subby, "start_calls", 0) == 0
    assert subby not in srv._tenants["t"].plugins


# --------------------------------------------------------------------------- #
# 4. Real dispatch — config-invalid entry parks, valid sibling still starts
#    AND is independently confirmed via live subscriber delivery (not just
#    the loader's own return value).
# --------------------------------------------------------------------------- #
def test_real_dispatch_config_invalid_parks_valid_sibling_still_delivers_running():
    manifest = {"plugins": [
        {"plugin": "badport", "config": {"port": "NaN"}},
        {"plugin": "demo", "config": {"port": 42}},
    ]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "t", manifest, registry=REGISTRY)
    tenant = srv._tenants["t"]
    bus = srv.tenant_bus("t")
    collector = _Collector()
    bus.subscribe_pattern("_server/*", collector)
    tenant.republish()

    demo_statuses = collector.values_for("_server/plugins/demo/status")
    assert demo_statuses and demo_statuses[-1]["status"] == "running"
    demo = _spec(srv, "t", "demo")
    assert demo.start_calls == 1

    # NEGATIVE CONTROL: badport genuinely never got an announce/instance —
    # confirmed both via the loader's own result AND via live delivery.
    assert "badport" not in [p.name for p in res.standup.loaded]
    assert _spec(srv, "t", "badport") is None
    assert "_server/plugins/badport/announce" not in collector.namespaces_seen()
