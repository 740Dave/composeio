"""Phase 0b: the LIFECYCLE-HOOK seam + the three remaining DEFAULT SHEDS
(`time`, `liveness`, `log`) out of the ComposeBus kernel.

[SPEC:CXC-COMPOSESERVER] [CXC-CS-EXTRACTION-INVENTORY] [CXC-CS-DEFAULT-PLUGIN-HOOKS]
/ [DEC-PHASE0B-LIFECYCLE-HOOK-SEAM-AND-DEFAULT-SHEDS].

BEHAVIOR-PRESERVATION IS THE HARD GATE, so every capability that moved is checked
BOTH byte-identical on a bare bus AND with a NEGATIVE CONTROL that fails when it
should ([CXC-VISION-EPISTEMICS] rule-negative-control): a plugin whose engine is
uninstalled must publish NOTHING (proving the plugin is what publishes, not an
always-on kernel), and a stopped bus must leave NO orphan periodic tick (proving
the lifecycle stop-hook actually tore down). An instrument that can only report
SUCCESS is not an instrument.
"""
from __future__ import annotations

import time

import pytest

from composeio.bus import ComposeIOBus
from composeio.bus_clock import shared_clock
from composeio.composeserver import standup, publish_server_state
from composeio.plugin import ComposePlugin
from composeio.plugins.time import TimePlugin
from composeio.plugins.liveness import LivenessPlugin
from composeio.plugins.log import LogPlugin
from composeio.transport import BusMessage, MessageType
from composeio.transport_local import LocalTransport


class RecordingTransport(LocalTransport):
    def __init__(self):
        super().__init__()
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        super().send(message)


def _count(sink, ns):
    return len([m for m in sink.messages if m.namespace == ns])


# =========================================================================== #
# 1. THE LIFECYCLE-HOOK SEAM ITSELF
# =========================================================================== #
def test_start_and_stop_hooks_fire_at_bus_start_and_stop():
    """`register_start_hook`/`register_stop_hook` fire with the bus, at start/stop.

    NEGATIVE CONTROL: a start-hook UNREGISTERED before start must NOT fire — if
    it did, the seam would be an always-fires callback, not a registration.
    """
    bus = ComposeIOBus(bus_id="lifecycle")
    started, stopped, ghost = [], [], []
    bus.register_start_hook(lambda b: started.append(b))
    bus.register_stop_hook(lambda b: stopped.append(b))

    doomed = lambda b: ghost.append(b)
    bus.register_start_hook(doomed)
    bus.unregister_start_hook(doomed)   # unregister BEFORE start

    bus.start()
    assert started == [bus], "start hook did not fire with the bus"
    assert ghost == [], "an UNREGISTERED start hook fired — seam is always-on"
    assert stopped == [], "stop hook fired before stop()"

    bus.stop()
    assert stopped == [bus], "stop hook did not fire at stop()"


def test_a_raising_hook_is_logged_not_fatal_and_peers_still_fire():
    """Errors in a hook are logged, not fatal (matches the post-publish/module
    hooks). NEGATIVE CONTROL: the peer hook registered AFTER the exploder must
    STILL fire — proving one bad hook does not abort the rest of bus start."""
    bus = ComposeIOBus(bus_id="raising-hook")
    peer = []

    def boom(b):
        raise RuntimeError("hook went bang")

    bus.register_start_hook(boom)
    bus.register_start_hook(lambda b: peer.append("fired"))
    bus.start()   # must not raise
    assert peer == ["fired"], "a raising start hook took its peer down with it"
    bus.stop()


# =========================================================================== #
# 2. TimePlugin — the `_bus/time` 1 Hz publisher
# =========================================================================== #
def test_bare_bus_still_ticks_bus_time_byte_identical():
    """Behavior-preservation: a bare `ComposeIOBus()` emits the immediate
    `_bus/time` tick on start, as whole UNIX seconds, TIME_SYNC, global."""
    bus = ComposeIOBus(bus_id="time-bare")
    bus.start()
    try:
        ticks = [m for m in bus.message_log if m.namespace == "_bus/time"]
        assert len(ticks) == 1, "the prompt _bus/time tick did not survive the shed"
        assert ticks[0].type is MessageType.TIME_SYNC
        assert ticks[0].scope == "global"
        assert isinstance(ticks[0].value, int) and not isinstance(ticks[0].value, bool)
        assert bus.clock_interval == 1.0, "clock_interval must still report the cadence"
        assert bus.is_clock_running
    finally:
        bus.stop()


def test_time_ticks_periodically_at_the_declared_cadence():
    sink = RecordingTransport()
    bus = ComposeIOBus(bus_id="time-periodic", global_transport=sink)
    bus.register_namespace("_bus/time", "json", refresh_interval=0.05)
    bus.start()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and _count(sink, "_bus/time") < 5:
            time.sleep(0.02)
        assert _count(sink, "_bus/time") >= 5, "the shed _bus/time is not periodic"
    finally:
        bus.stop()


def test_NEGATIVE_a_bus_without_the_time_plugin_emits_no_bus_time():
    """NEGATIVE CONTROL: uninstall the time engine (the only thing that
    publishes `_bus/time`) and start — NOTHING must tick on `_bus/time`. If it
    still ticked, the earlier PASS was measuring a kernel built-in, not the
    plugin the capability was shed into."""
    bus = ComposeIOBus(bus_id="time-absent")
    bus._time_engine.uninstall()          # remove the plugin's start/stop hooks
    bus.start()
    try:
        assert _count_log(bus, "_bus/time") == 0, (
            "a bus WITHOUT the time plugin still emitted _bus/time — the kernel, "
            "not the plugin, is publishing it")
        assert bus.clock_interval is None
    finally:
        bus.stop()


def test_NEGATIVE_stop_detaches_the_time_tick_no_orphan():
    """NEGATIVE CONTROL for the STOP hook: after `stop()`, driving the shared
    clock must produce NO further `_bus/time` — the periodic publisher was torn
    down, not left orphaned publishing into a stopped bus."""
    sink = RecordingTransport()
    bus = ComposeIOBus(bus_id="time-teardown", global_transport=sink)
    bus.register_namespace("_bus/time", "json", refresh_interval=0.05)
    bus.start()
    # positive control: it IS ticking while running
    deadline = time.time() + 2.0
    while time.time() < deadline and _count(sink, "_bus/time") < 3:
        time.sleep(0.02)
    assert _count(sink, "_bus/time") >= 3, "never ticked while running"

    bus.stop()
    settled = _count(sink, "_bus/time")
    # drive the process clock well past several intervals; a detached tick fires 0
    for i in range(5):
        shared_clock().tick_once(now=time.time() + 10.0 + i)
    assert _count(sink, "_bus/time") == settled, (
        "the _bus/time tick kept firing after stop() — the stop hook did not "
        "detach it (orphan clock callback)")


# =========================================================================== #
# 3. LivenessPlugin — `_bus/heartbeat` publisher + receiver + oracle seam
# =========================================================================== #
def test_bare_bus_still_beats_and_the_oracle_still_resolves():
    """Behavior-preservation: bare bus beats `_bus/heartbeat` naming its node,
    and the liveness oracle seam resolves exactly as before."""
    bus = ComposeIOBus(bus_id="live-bare")
    bus.start()
    try:
        beats = [m for m in bus.message_log if m.namespace == "_bus/heartbeat"]
        assert len(beats) == 1 and beats[0].type is MessageType.HEARTBEAT
        assert beats[0].value["node"] == "live-bare"
        assert beats[0].value["interval_s"] == 5.0

        # the receiver (now owned by the plugin) still backs node_is_alive
        assert bus.node_is_alive("ghost") is False
        bus.note_heartbeat("ghost", interval_s=5.0)
        assert bus.node_is_alive("ghost") is True

        # the ORACLE seam is untouched: set overrides, clear falls back to rx
        bus.set_liveness_oracle(lambda node: node == "phantom")
        assert bus.node_is_alive("phantom") is True
        assert bus.node_is_alive("ghost") is False       # oracle overrides rx
        bus.set_liveness_oracle(None)
        assert bus.node_is_alive("ghost") is True         # back to the receiver
    finally:
        bus.stop()


# --------------------------------------------------------------------------- #
# 3a. D2 — EXCLUDE SELF from the node's own heartbeat-derived liveness verdict
#     ([DEC-PHASE1-DSD-CALLS-SETTLED]). A node's OWN id is answered by "am I
#     running"; a self-beat (or a same-`bus_id` peer's beat) must NEVER pollute
#     the receiver, because a steady self-beat would MASK a dead same-id peer.
# --------------------------------------------------------------------------- #
def _heartbeat_msg(node, *, source=None, interval_s=5.0):
    """An inbound `_bus/heartbeat` exactly as `_dispatch_freshness` consumes."""
    return BusMessage(
        namespace="_bus/heartbeat", type=MessageType.HEARTBEAT,
        value={"node": node, "uptime_s": 3, "interval_s": interval_s},
        source=source if source is not None else node, scope="global")


def test_D2_own_id_is_alive_via_am_I_running_with_NO_peer_beats():
    """A node with NO peer heartbeats still answers its OWN `node_is_alive`
    correctly — TRUE, via 'am I running' (the liveness plugin is live), not via
    a beat it emitted. The receiver has heard nothing, yet self resolves alive."""
    bus = ComposeIOBus(bus_id="solo")
    bus.start()  # "am I running" is only True while the bus is actually up
    try:
        # nothing has beaten; the peer-liveness receiver is empty by construction
        assert bus._heartbeat_rx.is_alive("solo") is False, (
            "the receiver claims to have heard 'solo' before any beat — the "
            "test's own premise (self is NOT answered by the receiver) is broken")
        # ...but the node's own liveness is still TRUE, answered by am-I-running
        assert bus.node_is_alive("solo") is True, (
            "a running node with the liveness plugin live resolved its OWN id "
            "NOT-alive — D2 answers self by 'am I running', not by any heartbeat")
    finally:
        bus.stop()


def test_D2_NEGATIVE_a_self_beat_does_NOT_mask_a_dead_same_bus_id_peer():
    """⛔ THE EXACT HARM D2 PREVENTS. Feed an INBOUND beat naming this bus's OWN
    `bus_id` — the shape a same-`bus_id` peer (or a broadcast-transport echo)
    produces. It must NOT land in the receiver: were it counted, the record for
    that shared id would read FRESH off the self-beat while the real same-id
    peer is dead, masking the death.

    TEETH: a DISTINCT peer's beat IS recorded (proving the receiver can and does
    count real peers), while the self-id beat is NOT — the contrast fails the
    instant the `node != self.bus_id` filter is removed (self then records and
    `is_alive('twin')` flips True). Manually verified RED with the filter off."""
    clock_bus = ComposeIOBus(bus_id="twin")

    # a genuine DISTINCT peer beat lands — the mechanism works
    clock_bus._dispatch_freshness(_heartbeat_msg("other-peer"))
    assert clock_bus._heartbeat_rx.is_alive("other-peer") is True, (
        "the receiver dropped a legitimate DISTINCT peer's beat — the filter is "
        "over-broad and this test has no teeth")

    # a beat naming our OWN id (a same-`bus_id` peer / self-echo) is EXCLUDED
    clock_bus._dispatch_freshness(_heartbeat_msg("twin"))
    assert clock_bus._heartbeat_rx.is_alive("twin") is False, (
        "a beat naming our own bus_id polluted the receiver — a self-beat now "
        "MASKS a dead same-bus_id peer, the exact harm D2 forbids")
    # 'twin' must not even appear as a known node in the receiver's roster
    assert "twin" not in bus_nodes(clock_bus), (
        "our own id leaked into the peer-liveness roster via a self-beat")


def bus_nodes(bus):
    # ⛔ THE REAL ROSTER. `ComposeIOBus.status()` has NO "nodes" key, so the
    # old `status().get("nodes", {})` was membership in a permanently-empty
    # dict — always true, teeth-free. The heartbeat receiver IS the roster.
    return bus._heartbeat_rx.nodes()


def test_D2_self_liveness_TRACKS_RUNNING_STATE_true_running_false_stopped_or_uninstalled():
    """⛔ D2's "am I running" must track ACTUAL running state, not object
    lifetime. `_self_is_alive` was `_liveness_engine is not None` — sticky-True
    across BOTH `bus.stop()` (common shutdown) and `LivenessPlugin`/engine
    teardown (multi-tenant), so a STOPPED node reported ITSELF alive. Now:

      * running + engine installed -> own id ALIVE;
      * after `stop()`             -> own id DEAD (bus not running);
      * after `uninstall()`        -> own id DEAD (plugin torn down).

    TEETH per transition below: each `is False` flips RED if `_self_is_alive`
    reverts to the sticky `_liveness_engine is not None` proxy (manually
    verified). Restart is left at pre-existing behaviour and NOT asserted here."""
    # (1) running -> alive
    bus = ComposeIOBus(bus_id="runner")
    bus.start()
    assert bus.node_is_alive("runner") is True, (
        "a RUNNING node with its liveness plugin installed reported itself DEAD")

    # (2) stopped -> dead (the most common shutdown; engine object still present)
    bus.stop()
    assert bus._liveness_engine is not None, (
        "premise check: stop() left no engine, so this can't prove the "
        "sticky-True proxy is fixed")
    assert bus.node_is_alive("runner") is False, (
        "a STOPPED node reported itself ALIVE — the sticky `_liveness_engine is "
        "not None` proxy survived stop(), violating D2's 'am I running'")

    # (3) uninstalled while running -> dead (multi-tenant plugin teardown)
    bus2 = ComposeIOBus(bus_id="tenant")
    bus2.start()
    assert bus2.node_is_alive("tenant") is True
    bus2._liveness_engine.uninstall()
    assert bus2._liveness_engine is not None and \
        bus2._liveness_engine.is_installed is False, "premise: engine not marked uninstalled"
    assert bus2.node_is_alive("tenant") is False, (
        "a node whose liveness plugin was UNINSTALLED reported itself ALIVE — "
        "the self-verdict must require the plugin be live")
    bus2.stop()


# --------------------------------------------------------------------------- #
# 3b. D1 GUARD — the DEFAULT liveness plugin REMAINS THE FALLBACK: it must NOT
#     claim the oracle seat. The seat stays empty; a bare bus's liveness comes
#     from the fallback receiver ([DEC-PHASE1-DSD-CALLS-SETTLED]).
# --------------------------------------------------------------------------- #
def test_D1_GUARD_default_liveness_plugin_leaves_the_oracle_seat_EMPTY():
    """The default plugin must NOT self-install as the oracle. TEETH: revert
    `install_default`/`LivenessPlugin.start` to a self-installing-oracle stub
    and this guard fails at the first assert. Manually verified RED with such a
    stub, then restored."""
    # (1) the construction path (install_default from ComposeIOBus.__init__)
    bus = ComposeIOBus(bus_id="d1-ctor")
    assert bus._liveness_engine is not None, "default liveness engine not installed"
    assert bus._liveness_oracle is None, (
        "the DEFAULT liveness plugin claimed the oracle seat at construction — "
        "D1 says it must remain the FALLBACK, leaving the seat for a richer plugin")

    # (2) the standup path (LivenessPlugin.start explicitly)
    bare = ComposeIOBus(bus_id="d1-standup")
    LivenessPlugin().start(bare)
    assert bare._liveness_oracle is None, (
        "LivenessPlugin.start installed itself as the oracle — it must not")

    # (3) and a bare bus's PEER liveness therefore comes from the fallback
    #     receiver, not an oracle: a never-heard peer is dead, a noted one alive
    bus.start()
    try:
        assert bus.node_is_alive("nobody") is False
        bus.note_heartbeat("nobody", interval_s=5.0)
        assert bus.node_is_alive("nobody") is True, (
            "the fallback receiver did not back node_is_alive — with the oracle "
            "seat empty, the receiver is what must answer for a peer")
    finally:
        bus.stop()


def test_NEGATIVE_a_bus_without_the_liveness_plugin_emits_no_heartbeat():
    """NEGATIVE CONTROL: uninstall the liveness engine and start — NO
    `_bus/heartbeat`. Proves the beat is the plugin's, not a kernel built-in."""
    bus = ComposeIOBus(bus_id="live-absent")
    bus._liveness_engine.uninstall()
    bus.start()
    try:
        assert _count_log(bus, "_bus/heartbeat") == 0, (
            "a bus WITHOUT the liveness plugin still beat — kernel is publishing it")
    finally:
        bus.stop()


def test_NEGATIVE_stop_detaches_the_heartbeat_tick_no_orphan():
    """NEGATIVE CONTROL for the STOP hook on liveness: after `stop()`, driving
    the clock produces NO further `_bus/heartbeat`."""
    sink = RecordingTransport()
    bus = ComposeIOBus(bus_id="beat-teardown", global_transport=sink)
    bus.register_namespace("_bus/heartbeat", "json", refresh_interval=0.05)
    bus.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and _count(sink, "_bus/heartbeat") < 3:
        time.sleep(0.02)
    assert _count(sink, "_bus/heartbeat") >= 3, "never beat while running"

    bus.stop()
    settled = _count(sink, "_bus/heartbeat")
    for i in range(5):
        shared_clock().tick_once(now=time.time() + 10.0 + i)
    assert _count(sink, "_bus/heartbeat") == settled, (
        "the heartbeat kept beating after stop() — stop hook left an orphan")


def test_announce_time_false_silences_both_time_and_heartbeat():
    """The `announce_time=False` opt-out (a LEAF bus) must still silence BOTH
    sheds — the start hooks fire only when the kernel starts its clock."""
    bus = ComposeIOBus(bus_id="leaf")
    bus.start(announce_time=False)
    try:
        assert _count_log(bus, "_bus/time") == 0
        assert _count_log(bus, "_bus/heartbeat") == 0
        assert not bus.is_clock_running
        assert bus.clock_interval is None
    finally:
        bus.stop()


# =========================================================================== #
# 4. LogPlugin — `publish_log` stays a callable bus method delegating to it
# =========================================================================== #
def test_publish_log_still_routes_to_the_log_namespace():
    """Behavior-preservation: `bus.publish_log` (kept callable per the decision)
    emits a LOG_ENTRY on a log-mode namespace.

    NEGATIVE CONTROL: a log namespace never written stays empty (off-lane), and
    publish_log to a NON-log namespace does NOT emit a LOG_ENTRY (it falls back
    to a state publish) — proving the LOG path is specifically the log-mode one.
    """
    bus = ComposeIOBus(bus_id="logger")
    bus.register_namespace("app/log", "json", mode="log")
    seen = []
    bus.on_message(lambda m: seen.append(m)
                   if m.type is MessageType.LOG_ENTRY else None)

    bus.publish_log("app/log", "hello", source="test")
    assert [m.namespace for m in seen] == ["app/log"]
    assert seen[0].value == "hello"

    # off-lane: a different log namespace we never wrote stays silent
    assert _count_log(bus, "app/other") == 0

    # non-log namespace: publish_log must NOT emit a LOG_ENTRY (falls back to state)
    bus.register_namespace("app/state", "json")   # default mode = state
    seen.clear()
    bus.publish_log("app/state", "coerced", source="test")
    assert seen == [], "publish_log to a non-log namespace emitted a LOG_ENTRY"
    assert bus.get_state("app/state") == "coerced", "state fallback did not happen"


def test_NEGATIVE_log_engine_is_load_bearing_not_a_passthrough():
    """NEGATIVE CONTROL: `bus.publish_log` delegates to the LOG engine — if the
    engine is the one routing, swapping it for a recording double must capture
    the call and the kernel method must add nothing of its own."""
    bus = ComposeIOBus(bus_id="log-delegate")
    captured = []

    class _Spy:
        def publish_log(self, namespace, value, source=""):
            captured.append((namespace, value, source))

    bus._log_engine = _Spy()
    bus.publish_log("x/y", 42, source="s")
    assert captured == [("x/y", 42, "s")], (
        "bus.publish_log did not delegate to the log engine — the kernel is "
        "still doing the log routing itself")


# =========================================================================== #
# 5. REGISTRY + STANDUP — the three appear as default plugins and announce
# =========================================================================== #
@pytest.mark.parametrize("cls,name,pub", [
    (TimePlugin, "time", ["_bus/time"]),
    (LivenessPlugin, "liveness", ["_bus/heartbeat"]),
    (LogPlugin, "log", []),
])
def test_registry_entry_marks_each_a_default_plugin(cls, name, pub):
    entry = cls.registry_entry()
    assert entry["name"] == name
    assert entry["default_loaded"] is True
    assert entry["publishes"] == pub


def test_standup_publishes_each_shed_plugins_derived_announce():
    """The loader announces every declared plugin to `_server/plugins/<name>/*`.

    NEGATIVE CONTROL: a plugin NOT in the standup list has no announce — the
    announce is per-declared-plugin, not a blanket registration.
    """
    res = standup(bus_id="standup-sheds",
                  plugins=[TimePlugin(), LivenessPlugin(), LogPlugin()])
    bus = res.bus
    for name in ("time", "liveness", "log"):
        ann = bus.get_state(f"_server/plugins/{name}/announce")
        assert ann is not None and ann["plugin"] == name, (
            f"standup did not announce the default {name} plugin")

    # negative control: a plugin we never declared has no announce
    assert bus.get_state("_server/plugins/discovery/announce") is None, (
        "an undeclared plugin was announced — the spine is not per-plugin")


def test_republish_with_FRESH_instances_still_announces_all_not_dropped():
    """P1 REGRESSION ([CXC-CS-LOADER-PUBLISHES-ANNOUNCE]): re-publishing the
    `_server/*` surface with FRESH INSTANCES of the same plugin classes must
    (re)publish every announce — not silently drop them.

    The public `publish_server_state()` matched the declared `plugins` against
    the `loaded` list. Keyed on `id(plugin)` that match FAILS across instances:
    a re-publish with fresh instances (same names) dropped ALL announces, only
    `_server/status` surviving — no log, no error. Keyed on `plugin.name` (the
    unique per-tenant registry key) it holds.

    TEETH: with the old `id()` keying every `_server/plugins/<name>/announce`
    below is None (this asserts non-None) AND `_server/status.loaded` still lists
    all three — proving the drop was SILENT, exactly the P6b-class hole.
    """
    bus = ComposeIOBus(bus_id="republish-fresh")

    # "originally loaded" instances and a DISTINCT fresh declared set, same names
    loaded = [TimePlugin(), LivenessPlugin(), LogPlugin()]
    fresh = [TimePlugin(), LivenessPlugin(), LogPlugin()]
    assert {id(p) for p in fresh}.isdisjoint({id(p) for p in loaded}), (
        "test bug: instances must be distinct by identity to exercise the fix")

    publish_server_state(bus, plugins=fresh, loaded=loaded,
                         parked={}, subprocess_pending=[])

    for name in ("time", "liveness", "log"):
        ann = bus.get_state(f"_server/plugins/{name}/announce")
        assert ann is not None and ann["plugin"] == name, (
            f"re-publish with fresh instances DROPPED the {name} announce — the "
            f"loader matched on id(plugin), not plugin.name")
        st = bus.get_state(f"_server/plugins/{name}/status")
        assert st is not None and st["status"] == "running", (
            f"{name} should be marked running (its name is in loaded)")

    # the SILENT-DROP witness: _server/status still lists all three, so a caller
    # trusting the summary would never notice the announces vanished.
    summary = bus.get_state("_server/status")
    assert set(summary["loaded"]) == {"time", "liveness", "log"}



# helper used above (defined late so the module reads top-down)
def _count_log(bus, ns):
    return len([m for m in bus.message_log if m.namespace == ns])
