"""Phase 0b runtime bringup smoke — composeio-first-bringup, 2026-08-24.

Independent of the builder's own `test_composeserver_default_sheds.py` (which
already exists and is green). This file re-derives the SAME acceptance surface
directly from the ratified/proposed spec nodes
([CXC-CS-LIFECYCLE-HOOKS], [CXC-CS-EXTRACTION-INVENTORY], [CXC-CS-DEFAULT-PLUGINS],
[CXC-CS-LOADER-PUBLISHES-ANNOUNCE]) and proves the thing the P6b class of hole is
about: a message a REAL subscriber, on the REAL bus dispatch path, actually
receives — never a spy that patches an internal method and calls that agreement
"delivery".

WIRE OBSERVATION: `_bus/time` / `_bus/heartbeat` (and the loader's `_server/*`
announce) are sent via the raw envelope path (`bus.send_message`), which does
NOT run through `ns.publish_state()` — so `bus.subscribe()` (a STATE-CHANGE
callback keyed off the namespace's own value-change notification) never fires
for them. The genuine wire-level observer, the one every bus monitor actually
uses, is `bus.on_message(callback)` — it fires for every envelope this bus
sends, local AND global. That is the API used below throughout, matching the
builder's own test suite (`tests/test_composeserver_default_sheds.py`).

Every assertion below carries its own negative control: what would this show if
the component were dead, or the wire were empty?
"""
from __future__ import annotations

import time

from composeio.bus import ComposeIOBus
from composeio.bus_clock import shared_clock
from composeio.composeserver import standup, ComposeServer, publish_server_state
from composeio.namespace import Scope
from composeio.plugins.time import TimePlugin
from composeio.plugins.liveness import LivenessPlugin
from composeio.plugins.log import LogPlugin
from composeio.transport_local import LocalTransport


class RecordingTransport(LocalTransport):
    """A real transport that records every envelope it is asked to send —
    the actual wire, not a patched method."""

    def __init__(self):
        super().__init__()
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        super().send(message)


def _ns(sink, ns):
    return [m for m in sink.messages if m.namespace == ns]


# --------------------------------------------------------------------------- #
# 1. `_bus/time` at ~1 Hz, default-loaded — AND the negative control: no
#    TimePlugin engine attached => zero `_bus/time` on the wire.
# --------------------------------------------------------------------------- #
def test_1_bus_time_emits_and_negative_control_with_time_removed():
    seen = []
    bus = ComposeIOBus(bus_id="assert1")
    bus.on_message(lambda m: seen.append(m))
    bus.start()
    time_msgs = [m for m in seen if m.namespace == "_bus/time"]
    assert len(time_msgs) == 1, "expected exactly the PROMPT _bus/time tick on start()"
    bus.stop()

    # NEGATIVE CONTROL: uninstall the time engine's hooks (simulate "no time
    # plugin") and prove the wire goes SILENT — not just "fewer events."
    seen_dead = []
    dead_bus = ComposeIOBus(bus_id="assert1-dead")
    dead_bus.on_message(lambda m: seen_dead.append(m))
    dead_bus._time_engine.uninstall()   # the exact seam a plugin detaches through
    dead_bus.start()
    dead_bus.stop()
    dead_time_msgs = [m for m in seen_dead if m.namespace == "_bus/time"]
    assert dead_time_msgs == [], (
        "NEGATIVE CONTROL FAILED — _bus/time still emitted with the time "
        "engine uninstalled; the publisher is not what the test thinks it is")


# --------------------------------------------------------------------------- #
# 2. `_bus/heartbeat` emits, liveness oracle resolves freshness — AND after
#    stop() the periodic ticks are TORN DOWN: drive the clock post-stop and
#    assert no further `_bus/time`/`_bus/heartbeat`.
# --------------------------------------------------------------------------- #
def test_2_heartbeat_and_liveness_oracle_then_teardown_survives_a_driven_clock():
    seen = []
    bus = ComposeIOBus(bus_id="assert2")
    bus.on_message(lambda m: seen.append(m))
    bus.start()

    hb_msgs = [m for m in seen if m.namespace == "_bus/heartbeat"]
    assert len(hb_msgs) == 1, "expected the PROMPT heartbeat on start()"
    node, interval = hb_msgs[0].value["node"], hb_msgs[0].value["interval_s"]

    # D2 ([DEC-PHASE1-DSD-CALLS-SETTLED]): a bus's OWN id resolves alive from
    # "am I running" — the liveness plugin being live — NOT from a beat it
    # emitted. So `node` (this bus's own id) is alive WITHOUT any note, and the
    # receiver is deliberately NOT what answers for it.
    assert node == bus.bus_id
    assert bus.node_is_alive(node) is True, (
        "a bus resolved its OWN id NOT-alive — D2 answers self by 'am I "
        "running', which is true while the liveness plugin is live")
    # The receiver still answers for a DISTINCT peer heard over the wire via
    # `_dispatch_freshness` — the third-party path, using the beat's own
    # declared payload exactly as an inbound dispatch would.
    assert bus.node_is_alive("peer-3p") is False
    bus.note_heartbeat("peer-3p", interval_s=interval)
    assert bus.node_is_alive("peer-3p") is True, (
        "liveness oracle/receiver did not resolve a freshly-noted peer as alive")

    bus.stop()
    before_time = len([m for m in seen if m.namespace == "_bus/time"])
    before_hb = len([m for m in seen if m.namespace == "_bus/heartbeat"])

    # DRIVE THE CLOCK POST-STOP, deterministically: `tick_once(now=...)` fires
    # every subscriber still attached to the shared process clock whose
    # interval has elapsed. Forcing `now` far in the future guarantees any
    # leaked subscriber is "due" regardless of its declared interval. If the
    # stop-hook failed to detach this bus's tick callbacks, THIS is what fires
    # them again — the orphan-callback failure the lifecycle-hook seam exists
    # to prevent.
    shared_clock().tick_once(now=time.time() + 10_000)

    after_time = len([m for m in seen if m.namespace == "_bus/time"])
    after_hb = len([m for m in seen if m.namespace == "_bus/heartbeat"])
    assert after_time == before_time, (
        "NEGATIVE CONTROL FAILED — _bus/time ticked again after stop(); the "
        "stop-hook left an orphan periodic callback")
    assert after_hb == before_hb, (
        "NEGATIVE CONTROL FAILED — _bus/heartbeat ticked again after stop(); "
        "the stop-hook left an orphan periodic callback")


# --------------------------------------------------------------------------- #
# 3. bus.publish_log(...) routes through the LogPlugin ENGINE (delegation, not
#    kernel routing) — swap a spy engine to prove the kernel method is a thin
#    delegator, not an independent implementation that happens to agree.
# --------------------------------------------------------------------------- #
def test_3_publish_log_delegates_to_the_log_plugin_engine_not_kernel_routing():
    bus = ComposeIOBus(bus_id="assert3")
    bus.register_namespace("app/log", "json", mode="log", scope=Scope.LOCAL)

    calls = []

    class SpyLogEngine:
        def publish_log(self, namespace, value, source=""):
            calls.append((namespace, value, source))
            return "SPY-SENTINEL"

    bus._log_engine = SpyLogEngine()  # swap the delegate
    result = bus.publish_log("app/log", {"msg": "hello"}, source="tester")

    assert calls == [("app/log", {"msg": "hello"}, "tester")], (
        "publish_log did not hand off to bus._log_engine — kernel is still "
        "doing its own routing instead of delegating")
    assert result == "SPY-SENTINEL", (
        "publish_log's return value did not come back through the engine — "
        "kernel is intercepting/replacing the call, not delegating")


# --------------------------------------------------------------------------- #
# 4. THE PHASE-0 CLASS OF HOLE: standup(plugins=[Time, Liveness, Log]) must
#    publish each plugin's DERIVED announce to `_server/plugins/<name>/announce`
#    such that a REAL SUBSCRIBER on the real dispatch path receives it — not
#    merely that .announce() returns a dict. Verify LOCAL scope (must NOT cross
#    to a global connector) and per-tenant isolation.
# --------------------------------------------------------------------------- #
def test_4_standup_announce_reaches_a_real_subscriber_and_stays_local_scope():
    global_spy = RecordingTransport()
    global_spy._running = True

    declared = [TimePlugin(), LivenessPlugin(), LogPlugin()]
    result = standup(bus_id="tenant-a", plugins=declared)
    bus = result.bus

    # STANDUP ITSELF, WITH NO HELP: read `bus.message_log` — every envelope
    # `standup()` actually sent, before this test does anything else. This is
    # the part that was left too weak on the first pass: calling
    # `publish_server_state` manually afterwards would pass even if `standup`
    # never announced anything at all (verified: deleting the announce call
    # inside `standup()` did NOT fail this assertion until it was rewritten
    # to read `message_log` instead of re-triggering the publish itself).
    for name in ("time", "liveness", "log"):
        announces = [m for m in bus.message_log
                    if m.namespace == f"_server/plugins/{name}/announce"]
        assert len(announces) >= 1, (
            f"standup() NEVER PUBLISHED _server/plugins/{name}/announce — the "
            f"plugin's .announce() may be correctly DERIVED but never reaches "
            f"the wire (the exact Phase-0 class of hole)")
        payload = announces[-1].value
        assert payload["plugin"] == name
        expected_pub = {"time": TimePlugin, "liveness": LivenessPlugin,
                        "log": LogPlugin}[name].publishes
        assert payload["publishes"] == list(expected_pub)
        assert announces[-1].scope == "local", (
            f"_server/plugins/{name}/announce did not carry LOCAL scope on "
            f"the envelope")

    # Now attach a GLOBAL connector AFTER standup (mirrors a real deployment:
    # the local loop always exists, an uplink is attached separately) and a
    # REAL subscriber, then drive the loader's PERIODIC re-announce (the same
    # path `ComposeServer._Tenant.republish` uses) to prove a subscriber
    # attached AFTER load still gets a live delivery — cadence is PERIODIC,
    # not once-only, per [CXC-CS-LOADER-PUBLISHES-ANNOUNCE].
    bus.attach_connector(global_spy, "global")
    seen = []
    bus.on_message(lambda m: seen.append(m))
    publish_server_state(bus, declared, result.loaded, result.parked,
                          result.subprocess_pending)
    live_announces = [m for m in seen if m.namespace == "_server/plugins/time/announce"]
    assert len(live_announces) >= 1, (
        "a subscriber attached AFTER load received nothing on the periodic "
        "re-announce — the announce is once-only, not periodic")

    # NEGATIVE CONTROL / SCOPE: the announce is `_server/*` = LOCAL. It must
    # NOT have reached the "global" connector — a lane leaking scope is a FAIL
    # even though the local delivery above looks green.
    leaked = [m for m in global_spy.messages if m.namespace.startswith("_server/")]
    assert leaked == [], (
        f"SCOPE VIOLATION — a _server/* (LOCAL) announce reached the GLOBAL "
        f"connector: {[m.namespace for m in leaked]}")


def test_4b_per_tenant_server_surface_is_isolated_not_shared():
    """Two tenants' `_server/*` announces must not cross — proves PER-TENANT,
    not a shared/global registry an operator could mistake for one server."""
    server = ComposeServer()
    server.add_tenant("tenant-1", plugins=[TimePlugin()], start=False)
    server.add_tenant("tenant-2", plugins=[TimePlugin()], start=False)

    bus1 = server.tenant_bus("tenant-1")
    bus2 = server.tenant_bus("tenant-2")

    seen_on_1, seen_on_2 = [], []
    bus1.on_message(lambda m: seen_on_1.append(m))
    bus2.on_message(lambda m: seen_on_2.append(m))

    # Re-publish tenant-1's surface explicitly (same identity as declared to
    # standup, matching how `ComposeServer._Tenant.republish` really calls it)
    # and prove tenant-1's OWN wire got it (this check is non-vacuous — proves
    # the setup actually produces a delivery at all) while tenant-2's real
    # wire sees NOTHING from it — genuinely separate ComposeIOBus/LocalTransport
    # instances, not a shared in-process registry keyed by name.
    tenant1_plugins = server._tenants["tenant-1"].plugin_specs
    publish_server_state(bus1, tenant1_plugins,
                          server._tenants["tenant-1"].plugins, {}, [])

    delivered_on_1 = [m for m in seen_on_1 if m.namespace == "_server/plugins/time/announce"]
    assert len(delivered_on_1) >= 1, (
        "SETUP FAILED — tenant-1 itself never received its own announce; "
        "the cross-tenant check below would be vacuous without this")

    leaked = [m for m in seen_on_2 if m.namespace.startswith("_server/")]
    assert leaked == [], (
        "CROSS-TENANT LEAK — tenant-1's _server/plugins/time/announce reached "
        "a subscriber on tenant-2's bus; multi-tenancy isolation is broken")


# --------------------------------------------------------------------------- #
# 5. Multi-attach / multi-tenant standup, reachable and live.
# --------------------------------------------------------------------------- #
def test_5_multi_tenant_standup_each_tenant_is_an_independent_live_peer():
    server = ComposeServer()
    r1 = server.add_tenant("alpha", plugins=[TimePlugin(), LivenessPlugin()],
                            start=True)
    r2 = server.add_tenant("bravo", plugins=[TimePlugin(), LivenessPlugin()],
                            start=True)
    assert r1.is_clean() and r2.is_clean(), "a plugin parked unexpectedly at standup"

    alpha_seen, bravo_seen = [], []
    server.tenant_bus("alpha").on_message(lambda m: alpha_seen.append(m))
    server.tenant_bus("bravo").on_message(lambda m: bravo_seen.append(m))

    # Neither subscription saw the PROMPT tick (registered after start()) —
    # publish one real tick per tenant's own time engine and confirm BOTH
    # tenants are independently live (not one bus silently carrying both, and
    # not one silently dead while the other's traffic covers for it).
    server.tenant_bus("alpha")._time_engine.publish_bus_time()
    server.tenant_bus("bravo")._time_engine.publish_bus_time()

    alpha_time = [m for m in alpha_seen if m.namespace == "_bus/time"]
    bravo_time = [m for m in bravo_seen if m.namespace == "_bus/time"]
    assert len(alpha_time) == 1 and len(bravo_time) == 1, (
        "multi-tenant standup did not produce two independently-live buses")
    # cross-check: alpha's monitor must not have seen bravo's tick or vice versa
    assert alpha_time[0].source == "alpha" and bravo_time[0].source == "bravo", (
        "tenant time ticks crossed buses — not independent peers")

    server.stop()


# --------------------------------------------------------------------------- #
# 6. THE STRONGEST live-bus-peer proof available offline: two REAL
#    ComposeIOBus instances sharing one transport as their GLOBAL connector —
#    genuine cross-bus wire delivery (subscribe -> transport.send() ->
#    dispatch on a DIFFERENT bus's own incoming-message path), not one bus
#    observing itself. Peer B must actually COUNT peer A's heartbeat through
#    its real `_dispatch_incoming`/`_dispatch_freshness` path — proving
#    round-trip liveness across the wire, the thing a subscriber actually
#    needs, not merely that A can construct a well-formed envelope.
# --------------------------------------------------------------------------- #
def test_6_two_real_buses_share_a_transport_and_peer_b_hears_peer_as_heartbeat():
    shared_wire = LocalTransport()
    shared_wire._running = True

    peer_a = ComposeIOBus(bus_id="peer-a")
    peer_b = ComposeIOBus(bus_id="peer-b")
    peer_a.attach_connector(shared_wire, "global")
    peer_b.attach_connector(shared_wire, "global")

    # NEGATIVE CONTROL FIRST: before either starts, B must not already think A
    # is alive — a receiver that defaults to "alive" would make this whole
    # proof meaningless (that is exactly [CXC-VISION-EPISTEMICS]'s point).
    assert peer_b.node_is_alive("peer-a") is False, (
        "peer B resolved peer A alive before any beat crossed the wire — "
        "the receiver defaults the wrong way")

    # peer_b starts FIRST so its incoming-dispatch subscription is live on the
    # shared transport before peer_a's PROMPT (start-of-life, synchronous,
    # no-retention) heartbeat fires — a LocalTransport delivers only to
    # currently-registered subscribers, so start ORDER matters here the same
    # way it would if peer_b's real MQTT client hadn't subscribed yet.
    peer_b.start()
    peer_a.start()
    try:
        # peer_a's PROMPT heartbeat on start() should have crossed the SHARED
        # transport and landed in peer_b's real dispatch path.
        assert peer_b.node_is_alive("peer-a") is True, (
            "peer B never registered peer A's heartbeat across the shared "
            "transport — this is not a live bus peer, it is two isolated "
            "processes that happen to share a Python object")
    finally:
        peer_a.stop()
        peer_b.stop()
