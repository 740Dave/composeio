"""ComposeIOBus — the central bus implementation.

This is the reference implementation of the ComposeIO bus protocol.
All other implementations (firmware, Arduino library, etc.) must produce
identical behavior for the same config and event sequence.

DP-CIO-2: Pub/sub by namespace
DP-CIO-5: Local/global/remote scope with auto-resolution
DP-CIO-6: Transport-agnostic
DP-CIO-11: Graceful degradation
DP-CIO-17: Infrastructure namespaces (_bus/*)
"""

from __future__ import annotations
import json
import threading
import time
import logging
from typing import Any, Callable
from composebus.namespace import Namespace, Scope, Authority, Cadence
from composebus.freshness import (
    UNKNOWN, FreshnessClass, Verdict, is_unknown,
)
from composebus.bus_clock import BusClock, shared_clock
from composebus.state_engine import StateEngine
from composebus.transport import Transport, BusMessage, MessageType
from composebus.transport_local import LocalTransport
from composebus.module import Module
from composebus import selector as sel

logger = logging.getLogger("composeio.bus")

#: Sentinel for "absent" where None is a legitimate stored value.
_MISSING = object()


# --------------------------------------------------------------------------- #
# THE PERIODIC INFRASTRUCTURE CADENCES — [DEC-BUS-TIME-IS-A-REAL-1HZ-SIGNAL]
# --------------------------------------------------------------------------- #
# These are DEFAULTS FOR A DECLARATION, not constants the publisher reads. The
# clock reads the interval off the NAMESPACE; changing it in YAML or by
# re-registering changes the cadence, which is the whole of what [D4] ratified.
#
# 1 Hz for `_bus/time`, from David: "if `_bus/time` is a time signal ... it
# should be published @ 1 Hz."
BUS_TIME_INTERVAL_S = 1.0

# 5 s for the heartbeat, and the asymmetry with the clock is deliberate. The
# heartbeat answers "is this node alive", whose useful resolution is seconds,
# not one second; at D4's 3x convention that is a 15 s death declaration, which
# survives a Wi-Fi hiccup without making a genuinely dead node linger. Beating
# it at 1 Hz would quintuple the fleet's idle traffic to sharpen a verdict
# nobody consumes faster.
BUS_HEARTBEAT_INTERVAL_S = 5.0


# --- Namespace patterns (trailing-glob) --------------------------------------
#
# [SPEC:CXC-FLOW] [CXC-FL-PATTERN]: ONE pattern language serves discovery
# filtering AND runtime subscription.  Deliberately minimal — a TRAILING
# glob only, aligned with MQTT's native `#` wildcard so the same pattern
# maps 1:1 onto a transport-level subscription:
#
#     "jumpdrive/*"   -> "jumpdrive" and everything under "jumpdrive/"
#     "*"             -> every namespace
#
# Mid-pattern wildcards ("a/*/b") are NOT supported: they have no exact
# MQTT equivalent per-level semantics in our namespace model and would be
# inventing bus semantics — extend only via a spec change.

def is_pattern(name: str) -> bool:
    """True when *name* is a namespace pattern rather than an exact name."""
    return name == "*" or name.endswith("/*")


def pattern_matches(pattern: str, namespace: str) -> bool:
    """Trailing-glob match: 'p/*' matches 'p' and 'p/...'; '*' matches all.
    An exact (non-pattern) *pattern* matches only itself."""
    if pattern == "*":
        return True
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        return namespace == prefix or namespace.startswith(prefix + "/")
    return namespace == pattern


def validate_pattern(pattern: str) -> None:
    """Reject anything but the sanctioned trailing-glob forms."""
    if "*" in pattern and not is_pattern(pattern):
        raise ValueError(
            f"unsupported namespace pattern {pattern!r}: only a trailing "
            f"glob ('prefix/*') or '*' is defined — mid-pattern wildcards "
            f"are not bus semantics (see [CXC-FL-PATTERN])")


class ComposeIOBus:
    """The ComposeIO bus — namespace registry, pub/sub, scope resolution.

    The bus manages namespaces, routes messages between transports,
    and provides the API that modules and applications use.
    """

    def __init__(
        self,
        bus_id: str = "bus-0",
        local_transport: LocalTransport | None = None,
        global_transport: Transport | None = None,
        remote_transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.bus_id = bus_id
        # THE RECEIVER'S MONOTONIC CLOCK  [D4]. Named `_mono`, not `_clock`:
        # `self._clock` is already the BusClock (the periodic TICK SOURCE), and
        # these are opposite things — one DRIVES emissions, the other MEASURES
        # elapsed time for expiry. Reusing the name would have been a silent
        # overwrite of the tick source at construction.
        self._mono: Callable[[], float] = clock if clock is not None else time.monotonic
        # THE HEARTBEAT RECEIVER now lives in the default LIVENESS plugin
        # ([CXC-CS-EXTRACTION-INVENTORY] extraction 3); `_heartbeat_rx` is a
        # DELEGATING PROPERTY onto that engine's receiver so the kernel's
        # single-namespace freshness read-path (which legitimately stays kernel)
        # keeps consulting the same object unchanged.
        #: namespace -> the node id last seen publishing it, so a quiet-by-
        #: construction namespace can be routed to its node's heartbeat.
        self._ns_publisher: dict[str, str] = {}
        self._freshness_classes: dict[str, FreshnessClass] = {}
        self._namespaces: dict[str, Namespace] = {}
        self._modules: dict[str, Module] = {}
        self._sequence: int = 0
        self._bus_time: float = time.time()

        # Transport CONNECTORS, MULTI-ATTACH per scope-class ([CXC-CBK-STANDUP]
        # multi-attach; [DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23] "the bus root
        # holds a LIST of connectors per scope-class, single -> list"). A kernel
        # is not limited to one connector per scope: a same-node MQTT<->CAN
        # bridge COLLAPSES into two connectors attached to one kernel. The
        # single-valued `_local`/`_global`/`_remote` names survive as PROPERTIES
        # over these lists (getter = first, setter = replace) so every existing
        # reader and the app.py attach/detach writer are byte-for-byte
        # unaffected. A connector is a membrane, never a mediator: it forwards by
        # scope and encodes for the wire, and NEVER transforms
        # ([CXC-CBK-CONNECTOR-NEVER-TRANSFORMS]).
        self._local_connectors: list = [local_transport or LocalTransport()]
        self._global_connectors: list = (
            [] if global_transport is None else [global_transport])
        self._remote_connectors: list = (
            [] if remote_transport is None else [remote_transport])

        # THE LIVENESS-ORACLE HOOK ([DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23]
        # ambiguous-ruling: node-liveness is a PLUGIN; the kernel consults it via
        # a sanctioned oracle hook). When a liveness plugin sets this, the read-
        # path node-alive verdict defers to it; unset, the kernel falls back to
        # its own built-in heartbeat receiver, so behavior is unchanged until a
        # plugin claims the seam.
        self._liveness_oracle: Callable[[str], bool] | None = None

        # POST-PUBLISH HOOKS — the seam a reactive plugin (state_manager) binds
        # to so the kernel itself never transforms ([DX-CONNECTOR-FORWARDS-NEVER-
        # TRANSFORMS]). Fired at the exact call site the baked-in bindings engine
        # used, so ordering is preserved when the engine moves out of the kernel.
        self._post_publish_hooks: list[Callable[[str, Any], None]] = []

        # MODULE-REGISTRATION HOOKS — the seam the discovery plugin binds to so
        # the kernel no longer owns the `_bus/discovery` announce.
        self._module_hooks: list[Callable[[Module], None]] = []

        # LIFECYCLE HOOKS — the START/STOP seam the PERIODIC default plugins
        # (time, liveness) bind to ([CXC-CS-DEFAULT-PLUGIN-HOOKS]). Unlike the
        # event-driven post-publish/module hooks, the time/heartbeat sheds are
        # lifecycle-bound: a start-hook lets a plugin attach a periodic publisher
        # to the process clock, a stop-hook lets it detach with no orphan tick.
        # Fired at the EXACT sites the bus starts/stops its BusClock ticks.
        self._start_hooks: list[Callable[["ComposeIOBus"], None]] = []
        self._stop_hooks: list[Callable[["ComposeIOBus"], None]] = []

        # Selector state for dynamic namespaces (DP-CIO-21)
        self._selector_values: dict[str, Any] = {}

        # Message log for debugging/monitoring
        self._message_log: list[BusMessage] = []
        self._log_limit = 1000

        # Callbacks for bus-level events
        self._on_message_callbacks: list[Callable[[BusMessage], None]] = []

        # Pattern (trailing-glob) state subscribers ([CXC-FL-PATTERN]):
        # list of (pattern, callback).  Notified on every state publish
        # whose namespace matches — local publishes AND transport arrivals,
        # including namespaces first seen after the subscription.
        self._pattern_subs: list[tuple[str, Callable]] = []

        # SELF-ECHO SUPPRESSION ([SPEC:CXC-PATSUB] [CXC-PS-NO-SELF-ECHO]).
        # Fingerprints of the envelopes THIS bus handed to an external
        # transport, most-recent last, used as an ordered set (insertion order
        # is a dict guarantee since 3.7).  See `_remember_emitted`.
        self._emitted_fingerprints: dict[str, None] = {}
        # Guards _emitted_fingerprints. publish_* is called from many threads
        # at once (the console's ~20 source threads; SOB-C8) and the incoming
        # dispatch runs on the transport's thread. Unguarded, two publishers
        # evicting together both `del` the same oldest key (KeyError) or one
        # iterates while another inserts (RuntimeError), and either raises out
        # of publish_state BEFORE connector.send: the message is lost.
        self._emitted_lock = threading.Lock()

        # --- THE PERIODIC INFRASTRUCTURE NAMESPACES  [D4] ------------------
        # Registered HERE, at construction, rather than at the send site: a
        # namespace that is only ever an argument to `_send_message` has no
        # object to hang a declaration on, and D4's whole mechanism is a
        # DECLARATION. `_bus/time` was exactly that — a bare send — which is
        # part of why it stayed a single stale integer for the life of the
        # project. Registering it makes its cadence inspectable, re-declarable
        # and YAML-overridable like every other namespace.
        #
        # The engines are `json` deliberately: `_bus/time` carries a uint32 and
        # `_bus/heartbeat` a small dict, and a `binary` engine handed either one
        # raises inside a Tk callback and aborts the process.
        self.register_namespace(
            "_bus/time", "json", scope=Scope.GLOBAL,
            label="Bus Time", refresh_interval=BUS_TIME_INTERVAL_S)
        self.register_namespace(
            "_bus/heartbeat", "json", scope=Scope.GLOBAL,
            label="Bus Heartbeat", refresh_interval=BUS_HEARTBEAT_INTERVAL_S)

        self._clock: "BusClock | None" = None
        # NOTE: the `_bus/time` cadence bookkeeping (`clock_interval`) now lives
        # on the default TIME plugin's engine; the kernel keeps only `_clock` as
        # the "is a periodic clock active" marker set at the hook-fire sites.
        self._started_at: float = time.time()
        # NOTE: `_countdown_claims` used to be initialized HERE — a countdown
        # concern leaked into the kernel constructor. It now lives with
        # countdown (`CountdownService._claims_for`), created lazily on the bus
        # object, so the kernel no longer carries a subsystem's private table
        # ([DEC-...-BUS-STANDUP-2026-08-23] surprises-resolved).

        # DEFAULT PLUGIN: the reactive BINDINGS ENGINE (state_manager) is
        # installed by default so a bare `ComposeIOBus()` keeps `add_binding`
        # working exactly as before — but the engine CODE lives in
        # `composeio.plugins.state_manager`, OUT of the kernel, because deriving
        # a target value from a source change is a TRANSFORM and the kernel must
        # never transform ([DX-CONNECTOR-FORWARDS-NEVER-TRANSFORMS]). It binds
        # via the post-publish hook, fired at the exact site the baked-in engine
        # used, so ordering is preserved.
        self._binding_engine = None
        from composebus.plugins.state_manager import install_default as _sm_install
        _sm_install(self)

        # DEFAULT PLUGIN: module DISCOVERY (`_bus/discovery`) is shed to the
        # discovery plugin, installed by default so a bare bus still announces
        # module registrations — but the announce CODE lives in the plugin.
        from composebus.plugins.discovery import install_default as _disc_install
        _disc_install(self)

        # DEFAULT PLUGIN: the `_bus/time` 1 Hz publisher is shed to the TIME
        # plugin ([CXC-CS-EXTRACTION-INVENTORY] extraction 2). Installed by
        # default so a bare bus still ticks `_bus/time` identically — the engine
        # binds via the LIFECYCLE-HOOK seam, its immediate + periodic tick fired
        # at the exact site `start()` used. (Registered BEFORE liveness so the
        # start-hook order is time-then-heartbeat, matching the former `start()`.)
        self._time_engine = None
        from composebus.plugins.time import install_default as _time_install
        _time_install(self)

        # DEFAULT PLUGIN: the `_bus/heartbeat` publisher + node-liveness RECEIVER
        # are shed to the LIVENESS plugin ([CXC-CS-EXTRACTION-INVENTORY]
        # extraction 3; [CXC-CS-LIVENESS-FRESHNESS-SPLIT]). Installed by default
        # so a bare bus still beats AND still has a receiver backing
        # `node_is_alive`; the oracle seam (`set_liveness_oracle`) is unchanged.
        self._liveness_engine = None
        from composebus.plugins.liveness import install_default as _live_install
        _live_install(self)

        # DEFAULT PLUGIN: log-mode publishing (`publish_log`) is shed to the LOG
        # plugin ([CXC-CS-EXTRACTION-INVENTORY] extraction 5). `bus.publish_log`
        # STAYS a callable method delegating to this engine (behavior-preservation
        # over a purer 'publish to the log namespace directly' shape, per the
        # governing decision). Non-periodic: it has no lifecycle hook.
        self._log_engine = None
        from composebus.plugins.log import install_default as _log_install
        _log_install(self)

    # --- Self-echo suppression ([SPEC:CXC-PATSUB] [CXC-PS-NO-SELF-ECHO]) ---
    #
    # How many emissions we remember.  Bounded so the set cannot grow without
    # limit on a busy bus; an echo that arrives after this many further sends
    # simply is not suppressed, which degrades to the old duplicate rather than
    # to a dropped message.  Every echo measured on the live broker came back
    # within ~0.7 ms, i.e. within the next message or two.
    SELF_ECHO_MEMORY = 512

    @staticmethod
    def _echo_fingerprint(message: BusMessage) -> str:
        """The identity of an envelope, for self-echo matching.

        The FULL serialized envelope — `ts` and `seq` included — because that
        is what makes this envelope-identity and NOT content dedup.  Two
        genuine publishes of the same value differ in both fields and so can
        never collide; a broker echo is byte-identical BY CONSTRUCTION,
        because it is literally the bytes we just sent, round-tripped through
        `to_json` / `from_json`.  Sorted keys so the two serializations are
        comparable regardless of field insertion order.
        """
        return json.dumps(message.to_json(), sort_keys=True,
                          separators=(",", ":"))

    def _remember_emitted(self, message: BusMessage):
        """Record an envelope this bus is handing to an EXTERNAL transport."""
        fp = self._echo_fingerprint(message)
        with self._emitted_lock:
            fps = self._emitted_fingerprints
            fps[fp] = None
            while len(fps) > self.SELF_ECHO_MEMORY:
                del fps[next(iter(fps))]

    def _is_own_echo(self, message: BusMessage) -> bool:
        """True if *message* is this bus's own emission coming back.

        CONSUMES the record on a match, so exactly ONE echo is suppressed per
        emission — a second copy (a genuinely duplicated publisher, which is a
        different defect with a different fix, [CXC-IC-SINGLETON]) still gets
        through and stays visible rather than being silently absorbed here.
        """
        fp = self._echo_fingerprint(message)
        with self._emitted_lock:
            return self._emitted_fingerprints.pop(fp, _MISSING) is not _MISSING

    # --- Connectors: MULTI-ATTACH per scope-class ------------------------- #
    #
    # The single-valued `_local`/`_global`/`_remote` are the FIRST connector of
    # their scope-class, exposed as read/write properties so the historical
    # contract (`bus._global`, `bus._global = t`, `bus._global = None`,
    # `hasattr(bus, "_global")`) is preserved exactly. `attach_connector` is the
    # NEW multi-attach entry point; internal routing iterates the lists.

    _CONNECTOR_LISTS = {
        "local": "_local_connectors",
        "global": "_global_connectors",
        "remote": "_remote_connectors",
    }

    @property
    def _local(self):
        return self._local_connectors[0] if self._local_connectors else None

    @_local.setter
    def _local(self, transport):
        self._local_connectors = [] if transport is None else [transport]

    @property
    def _global(self):
        return self._global_connectors[0] if self._global_connectors else None

    @_global.setter
    def _global(self, transport):
        self._global_connectors = [] if transport is None else [transport]

    @property
    def _remote(self):
        return self._remote_connectors[0] if self._remote_connectors else None

    @_remote.setter
    def _remote(self, transport):
        self._remote_connectors = [] if transport is None else [transport]

    def attach_connector(self, transport, scope_class: str = "global") -> None:
        """Attach a connector to a scope-class, MULTI-ATTACH ([CXC-CBK-STANDUP]).

        More than one connector may be live at once on the same kernel — this is
        the native form of what a same-node bridge did by hand. If the bus is
        already started the caller is responsible for starting the connector
        (standup attaches BEFORE start; a late attach is an operator choice).
        """
        key = self._CONNECTOR_LISTS.get(scope_class)
        if key is None:
            raise ValueError(
                f"unknown scope-class {scope_class!r}; expected one of "
                f"{sorted(self._CONNECTOR_LISTS)}")
        getattr(self, key).append(transport)

    def connectors(self, scope_class: str) -> list:
        """The live connectors for a scope-class (a copy)."""
        key = self._CONNECTOR_LISTS.get(scope_class)
        if key is None:
            raise ValueError(f"unknown scope-class {scope_class!r}")
        return list(getattr(self, key))

    @property
    def _external_connectors(self) -> list:
        """Every external (non-local) connector — global then remote."""
        return [*self._global_connectors, *self._remote_connectors]

    # --- The liveness-oracle hook ([DEC-...-BUS-STANDUP-2026-08-23]) ------- #

    def set_liveness_oracle(self, oracle: Callable[[str], bool] | None) -> None:
        """Install (or clear) the sanctioned node-liveness oracle.

        Node-liveness is a PLUGIN (publisher + receiver); the kernel's read-path
        verdict consults the oracle rather than owning the receiver. Clearing it
        restores the kernel's built-in fallback, so an unset oracle is a working
        default, not a dead read path.
        """
        self._liveness_oracle = oracle

    def register_post_publish_hook(
        self, hook: Callable[[str, Any], None]) -> None:
        """Register a `hook(namespace, new_value)` fired after every state
        publish, at the site the kernel's own bindings engine runs. This is how
        a reactive plugin observes source changes WITHOUT the kernel importing
        it, keeping transformation off the kernel ([DX-CONNECTOR-FORWARDS-NEVER-
        TRANSFORMS])."""
        self._post_publish_hooks.append(hook)

    def unregister_post_publish_hook(
        self, hook: Callable[[str, Any], None]) -> None:
        try:
            self._post_publish_hooks.remove(hook)
        except ValueError:
            pass

    # --- Lifecycle hooks: the START/STOP seam periodic plugins bind to ----- #
    # SYMMETRIC to the post-publish/module hooks, but for LIFECYCLE rather than
    # events: the time/heartbeat sheds are periodic publishers, so they attach at
    # bus start and detach at bus stop ([CXC-CS-DEFAULT-PLUGIN-HOOKS]).

    def register_start_hook(
        self, hook: Callable[["ComposeIOBus"], None]) -> None:
        """Register a `hook(bus)` fired when the bus STARTS its BusClock ticks.

        A periodic plugin (time, liveness) uses it to attach a periodic publisher
        to the process clock at the EXACT site the kernel's baked-in ticks
        started. Errors in a hook are logged, not fatal."""
        self._start_hooks.append(hook)

    def unregister_start_hook(
        self, hook: Callable[["ComposeIOBus"], None]) -> None:
        try:
            self._start_hooks.remove(hook)
        except ValueError:
            pass

    def register_stop_hook(
        self, hook: Callable[["ComposeIOBus"], None]) -> None:
        """Register a `hook(bus)` fired when the bus STOPS its BusClock ticks, so
        a plugin detaches its periodic publisher with NO orphan clock callback
        surviving into a stopped bus. Errors in a hook are logged, not fatal."""
        self._stop_hooks.append(hook)

    def unregister_stop_hook(
        self, hook: Callable[["ComposeIOBus"], None]) -> None:
        try:
            self._stop_hooks.remove(hook)
        except ValueError:
            pass

    # --- Lifecycle ---

    def _wire_transport_incoming(self, transport):
        """Register a wildcard subscriber on a transport so incoming messages
        are dispatched to the bus's namespace objects (state + event subscribers).

        Without this, messages received by a transport (e.g. MQTT) would only
        reach transport-level subscribers, not bus-level namespace subscribers.
        """
        def _dispatch_incoming(msg: BusMessage):
            # [CXC-PS-NO-SELF-ECHO]: our OWN publish, handed back by a
            # broadcast transport.  MQTT 3.1.1 has no `noLocal`, so a broker
            # delivers to every matching subscription INCLUDING the publisher's
            # own — and `subscribe_pattern` makes a bus that publishes a
            # namespace it also watches a subscriber to itself.  We already
            # dispatched this message locally at publish time; dispatching it
            # again is the second delivery David saw in his console channel.
            if self._is_own_echo(msg):
                return
            # THE FRESHNESS RECEIVER SEES EVERY INBOUND MESSAGE, and it sees it
            # FIRST — before any engine validation can reject the payload and
            # `return` out of this function. A peer whose value violates our
            # engine is still a peer that is ALIVE, and losing its heartbeat
            # attribution because its data was malformed would condemn it for
            # the wrong reason. ⛔ HEARTBEAT messages reach neither branch below
            # (they are neither STATE nor EVENT), so before this line a beat
            # arrived at the bus and was counted by nothing at all.
            self._dispatch_freshness(msg)
            ns = self._namespaces.get(msg.namespace)
            if ns is None:
                # An UNKNOWN external namespace has an unknown value shape:
                # auto-create with the permissive json engine, not the
                # binary default — otherwise any non-binary outside signal
                # (e.g. tri_level 'charging') raised and was silently
                # dropped, making discovery deaf ([CXC-FL-DISCOVERY] fix).
                ns = self.ensure_namespace(msg.namespace,
                                           engine_config="json")
            if msg.type == MessageType.STATE:
                old = ns.value
                try:
                    ns.publish_state(msg.value, source=msg.source)
                except ValueError as e:
                    # a REGISTERED engine rejecting an external value is a
                    # peer contract violation — log, don't crash dispatch
                    logger.warning(
                        f"External value rejected on '{msg.namespace}': {e}")
                    return
                self._notify_pattern_subs(msg.namespace, ns.value, old)
            elif msg.type == MessageType.EVENT:
                ns.publish_event(msg.event_type, data=msg.data, source=msg.source)
            # Bus monitors ([CXC-FL-DISCOVERY] fix): externally-received
            # traffic is bus traffic — without this, on_message() monitors
            # were deaf to the outside world (they only saw local sends).
            for cb in self._on_message_callbacks:
                try:
                    cb(msg)
                except Exception as e:
                    logger.error(f"Message callback error: {e}")

        transport.subscribe_all(_dispatch_incoming)

    def start(self, *, announce_time: bool = True):
        """Start the bus and all transports.

        Wires incoming message dispatch on external transports so that
        messages received from MQTT/UART/etc. are dispatched to the bus's
        namespace subscribers (state + event).

        ``announce_time=False`` suppresses the ``_bus/time`` tick this bus emits
        on start ([SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-WIRE-DELTAS]).  Default
        True, so every existing caller is byte-for-byte unaffected.

        WHY THE OPT-OUT EXISTS.  ``_bus/time`` is a bus MASTER's announcement —
        "here is my clock" — and the rest of the stack already treats it as
        per-bus infrastructure rather than shared traffic: ``peer/frame.py`` and
        ``peer/canbridge.py`` both DROP it explicitly with that reasoning.  A
        LEAF bus (a data source's self-created handle, sharing a broker with the
        app that actually owns the scene) announcing its own clock puts a second
        publisher on a namespace it does not own, and shows up in a capture-diff
        as a wire change nobody asked for.  Two masters on one broker is a clock
        argument, not a feature.
        """
        for connector in self._local_connectors:
            connector.start()
        for connector in self._external_connectors:
            self._wire_transport_incoming(connector)
            connector.start()
        # Publish bus time on start.
        #
        # THE IMMEDIATE TICK SURVIVES THE UPGRADE TO A PERIODIC SIGNAL, and it
        # is not redundant with the clock: without it a subscriber present at
        # start would wait a whole interval for the first value, and the very
        # first thing a scene does is come up. Periodic AND prompt.
        # [D4]'s SEAM, FINALLY CONNECTED. Hand every transport the set of
        # namespaces that DECLARED a cadence, so MQTT can set a retained
        # message's expiry to the staleness timeout rather than the 24 h
        # horizon. Done for every bus, master or leaf: a leaf's retained values
        # deserve dating just as much, and the transport no-ops if it cannot use
        # the information.
        self._publish_refresh_intervals()

        if announce_time:
            # START the BusClock ticks. The kernel no longer owns the tick
            # handlers: it acquires the process clock and FIRES START HOOKS, and
            # the default time/liveness plugins attach their periodic publishers
            # (and emit the prompt `_bus/time` + `_bus/heartbeat` ticks) at this
            # exact site ([CXC-CS-DEFAULT-PLUGIN-HOOKS]). `announce_time=False`
            # skips it, so a LEAF bus stays silent exactly as before.
            self._start_clock()
        logger.info(f"Bus '{self.bus_id}' started")

    # --- THE 1 Hz SIGNAL  [DEC-BUS-TIME-IS-A-REAL-1HZ-SIGNAL] -------------- #

    def _publish_refresh_intervals(self) -> None:
        intervals = self.declared_refresh_intervals()
        if not intervals:
            return
        for transport in (*self._local_connectors, *self._external_connectors):
            try:
                transport.declare_refresh_intervals(intervals)
            except AttributeError:
                # A third-party or test-double transport predating this method.
                # Ignored rather than raised: the freshness contract is enforced
                # RECEIVER-side by design, so a transport that cannot carry a
                # cadence hint degrades to the 24 h horizon — the behaviour that
                # shipped with retain — rather than failing the bus start.
                logger.debug("transport %r has no declare_refresh_intervals",
                             type(transport).__name__)

    def _start_clock(self) -> None:
        """Acquire the process clock and FIRE START HOOKS.

        The kernel owns the SEAM and the lifecycle, never the tick handlers: the
        default TIME and LIVENESS plugins attach their periodic publishers here
        via `register_start_hook`, each reading its cadence off its own
        namespace ([CXC-CS-DEFAULT-PLUGIN-HOOKS]). `_clock` is set unconditionally
        so `is_clock_running` reports a started periodic clock — exactly as the
        baked-in version did — even if an operator re-declared a namespace
        event-driven and its plugin therefore attached no tick.

        A hook that raises is LOGGED AND SURVIVED: one misbehaving periodic
        publisher must not take the rest of bus start down with it.
        """
        self._clock = shared_clock()
        for hook in self._start_hooks:
            try:
                hook(self)
            except Exception as exc:
                logger.error(f"Bus start hook error: {exc}")

    def _stop_clock(self) -> None:
        """FIRE STOP HOOKS so periodic plugins detach their publishers, then
        drop the clock marker. Idempotent: a stop-hook whose plugin never
        attached (e.g. `announce_time=False`) detaches nothing."""
        for hook in self._stop_hooks:
            try:
                hook(self)
            except Exception as exc:
                logger.error(f"Bus stop hook error: {exc}")
        self._clock = None

    @property
    def clock_interval(self) -> float | None:
        """The cadence this bus's `_bus/time` tick is actually running at, or
        None. Owned by the default TIME plugin's engine ([CXC-CS-DEFAULT-PLUGINS])
        and surfaced here so the historical bus property keeps answering."""
        eng = getattr(self, "_time_engine", None)
        return eng.clock_interval if eng is not None else None

    @property
    def is_clock_running(self) -> bool:
        return self._clock is not None

    def declared_refresh_intervals(self) -> dict[str, float]:
        """Every namespace that DECLARED a periodic cadence — [D4]'s seam.

        This fills `MQTTTransport(refresh_intervals=...)`, which was built for
        the contract and then sat permanently empty because nothing could
        declare one. With it filled, a retained message's Message Expiry
        Interval becomes D4's staleness timeout instead of the 24 h horizon, so
        the broker drops a retained copy at the same instant the freshness
        contract would call it stale.

        EVENT_DRIVEN namespaces are deliberately ABSENT rather than present with
        a None. An expiry derived from `throttle/left` would delete its retained
        value during exactly the silence it was designed to have — reproducing
        the wiggle-the-levers symptom retain was added to fix.
        """
        return {name: ns.refresh_interval
                for name, ns in self._namespaces.items()
                if ns.cadence is Cadence.PERIODIC}

    def stop(self):
        """Stop the bus and all transports."""
        self._stop_clock()
        for connector in (*self._local_connectors, *self._external_connectors):
            connector.stop()
        logger.info(f"Bus '{self.bus_id}' stopped")

    # --- Namespace Management ---

    def register_namespace(
        self,
        name: str,
        engine_config: str | dict = "binary",
        scope: Scope = Scope.AUTO,
        authority: Authority = Authority.LOCAL,
        label: str | None = None,
        mode: str = "state",
        retention: int = 100,
        access_publish: list[str] | None = None,
        access_subscribe: list[str] | None = None,
        refresh_interval: float | None = None,
        timeout: float | None = None,
        quiet_by_construction: bool = False,
    ) -> Namespace:
        """Register a namespace on the bus. Creates state engine from config.

        `refresh_interval` / `timeout` / `quiet_by_construction` are [D4]'s
        declared freshness contract; see `Namespace.staleness_timeout`.
        """
        if name in self._namespaces:
            ns = self._namespaces[name]
            if label and not ns.label:
                ns.label = label  # Update label if not already set
            # A RE-DECLARATION of the freshness contract is honoured, because
            # this is the one path an app has to declare a cadence on an
            # infrastructure namespace the bus registered for itself — which is
            # exactly how `_bus/time` gets re-tuned. Silently ignoring it would
            # make the declaration decorative on its own proving case.
            if (refresh_interval is not None or timeout is not None
                    or quiet_by_construction):
                # IN PLACE, never a fresh object: the existing Namespace holds
                # the subscriber lists, and replacing it here would silently
                # unsubscribe everything already listening — a re-declaration
                # is a change to the CONTRACT, not to the channel.
                ns.declare_freshness(
                    refresh_interval=refresh_interval, timeout=timeout,
                    quiet_by_construction=quiet_by_construction)
            return ns
        engine = StateEngine.from_config(engine_config)
        ns = Namespace(
            name, engine, scope=scope, authority=authority, label=label,
            mode=mode, retention=retention,
            access_publish=access_publish, access_subscribe=access_subscribe,
            refresh_interval=refresh_interval, timeout=timeout,
            quiet_by_construction=quiet_by_construction,
            # THE BUS'S monotonic clock, not the namespace's own default: every
            # age on this bus must be measured against ONE clock, or two
            # namespaces in the same freshness class could disagree about how
            # much time passed between them ([PRINCIPLE-ONE-MEASUREMENT-PATH]).
            clock=self._mono,
        )
        # A quiet-by-construction namespace is judged by its NODE, so wire it to
        # the heartbeat receiver now; the probe resolves the publisher lazily
        # because the node is not known until the first message arrives.
        ns.attach_node_liveness(lambda n=name: self._namespace_node_is_alive(n))
        self._namespaces[name] = ns
        logger.debug(f"Registered namespace '{name}' ({engine.engine_type}, {scope.value}, mode={mode})")
        return ns

    def get_namespace(self, name: str) -> Namespace | None:
        """Get a namespace by name. Returns None if not registered."""
        return self._namespaces.get(name)

    # --- THE FRESHNESS RECEIVER  [D4] / [SPEC:CXC-FRESHNESS] --------------- #

    @property
    def _heartbeat_rx(self):
        """The node-liveness receiver, now OWNED by the default LIVENESS plugin
        ([CXC-CS-EXTRACTION-INVENTORY]). Exposed as a property so the kernel's
        single-namespace freshness read-path (`note_heartbeat`, `node_is_alive`'s
        fallback, `_dispatch_freshness`, `status`) consults the same object
        unchanged — the receiver moved out of the kernel, not the read-path."""
        return self._liveness_engine.receiver

    def note_heartbeat(self, node: str, interval_s: float | None = None) -> None:
        """Record a liveness beat from `node`.

        ⛔ THE HALF THAT WAS NEVER BUILT. `publish_heartbeat` shipped alone and
        said so in its own docstring; beats went out, arrived, and nothing
        counted them. This is what counts them.
        """
        self._heartbeat_rx.note(node, interval_s)

    def node_is_alive(self, node: str) -> bool:
        """Whether `node` has beaten within 3x its declared interval.

        A node never heard from is NOT alive — see `HeartbeatReceiver.is_alive`
        for why the opposite default is the dangerous one.

        THE ORACLE HOOK: when a liveness plugin has claimed the seam, the verdict
        defers to it; unset, the kernel's built-in receiver answers, so behavior
        is unchanged until the plugin loads ([DEC-...-BUS-STANDUP-2026-08-23]).

        ⛔ SELF IS EXCLUDED FROM THE HEARTBEAT-DERIVED VERDICT (D2 /
        [DEC-PHASE1-DSD-CALLS-SETTLED]). My OWN node's liveness is "am I
        running" — the default liveness plugin being live means this node is
        alive — NOT the receipt of a beat I just emitted. Answering my own id
        from my own beat is circular, and worse, a self-beat can MASK a dead
        same-`bus_id` peer or inflate fleet counts. The receive-side filter in
        `_dispatch_freshness` keeps my own id OUT of the receiver so a self-beat
        (or a same-id peer's beat) never pollutes it; here the fallback answers
        my own id from process liveness instead. A richer ORACLE, when set,
        still decides everything — including self — so this rule lives only on
        the fallback path.
        """
        if self._liveness_oracle is not None:
            return self._liveness_oracle(node)
        if node == self.bus_id:
            return self._self_is_alive()
        return self._heartbeat_rx.is_alive(node)

    def _self_is_alive(self) -> bool:
        """D2's answer to `node_is_alive(self.bus_id)`: am I RUNNING?

        Two conditions, both required — a node is its OWN liveness only while it
        is actually up:
          * the bus is RUNNING (`is_clock_running`: set by `start()`, cleared
            by `stop()`), so a STOPPED node reports itself DEAD — not
            sticky-alive; AND
          * the default liveness engine is INSTALLED (its lifecycle hooks are
            attached), so a node whose liveness plugin was torn down
            (`uninstall` / `LivenessPlugin.stop`) reports itself DEAD too.
        Either teardown flips this False — D2's "am I running" intent. The
        earlier `_liveness_engine is not None` proxy was sticky-True for the
        object's whole lifetime and survived BOTH shutdowns, which is the bug
        first-bringup reproduced live. Restart is left at pre-existing
        behaviour: a re-`start()`ed bus re-acquires the clock and reads True
        again, a natural consequence, not a policy decided here.
        """
        eng = self._liveness_engine
        return (self.is_clock_running
                and eng is not None
                and eng.is_installed)

    def _namespace_node_is_alive(self, namespace: str) -> bool:
        """Is the node that publishes `namespace` still beating?

        ⛔ NO ATTRIBUTED PUBLISHER MEANS NO VERDICT, AND NO VERDICT MEANS ALIVE.
        A locally-published namespace, or one whose envelope carried no `src`,
        has no node to condemn — and condemning it anyway would mark every
        in-process namespace dead the moment this mechanism shipped. The
        heartbeat can only ever REMOVE confidence from a namespace whose
        publishing node we positively identified and then positively lost.
        """
        node = self._ns_publisher.get(namespace)
        if node is None or node == self.bus_id:
            return True
        return self.node_is_alive(node)

    def _dispatch_freshness(self, message: BusMessage) -> None:
        """Feed an INBOUND message to the receiver.

        Two jobs, and they are separate on purpose:

        1. A HEARTBEAT feeds the node monitor. Its payload carries the
           publisher's own `interval_s`, so the timeout is derived from what the
           publisher DECLARED rather than from a configured copy of it here —
           the second copy being the thing that eventually disagrees.

        2. ANY message attributes its namespace to its source node. That
           attribution is what lets a quiet-by-construction namespace be judged
           by its node's heartbeat instead of by a timeout that would fire the
           moment David stopped moving the throttle.
        """
        src = getattr(message, "source", "") or ""
        ns_name = getattr(message, "namespace", "") or ""

        if getattr(message, "type", None) is MessageType.HEARTBEAT:
            val = getattr(message, "value", None)
            node = src
            interval = None
            if isinstance(val, dict):
                # `val.node` is authoritative over the envelope's `src`: the
                # payload carries it precisely because some downstream
                # projections drop `src`, and a liveness record that loses its
                # subject is worse than absent.
                node = val.get("node") or src
                interval = val.get("interval_s")
            # ⛔ D2 / [DEC-PHASE1-DSD-CALLS-SETTLED]: EXCLUDE SELF from the
            # receiver. A beat naming MY own `bus_id` — whether it is my own
            # emission handed back by a broadcast transport, or a genuinely
            # DISTINCT peer that happens to share my id — must never be counted
            # here. Counting it makes my own liveness circular AND, worse, lets
            # my steady self-beat MASK a dead same-`bus_id` peer: the record for
            # that shared id would read FRESH off my beat while the real peer is
            # gone. My own id is answered separately by `_self_is_alive`.
            if node and node != self.bus_id:
                self._heartbeat_rx.note(node, interval)
            return

        if ns_name and src:
            self._ns_publisher[ns_name] = src

    # --- Freshness classes — David's addition to D4 ------------------------ #

    def register_freshness_class(self, name: str,
                                 members: list[str]) -> FreshnessClass:
        """Declare a named group that must ALL be fresh together.

        David, ratifying D4: "or a simple grouping of semantic messages that
        must all have a specific value within the bounding window defined as a
        class." Age is the MAX of the members' ages and the class is unknown if
        ANY member is — D4b's derived-value rule, made nameable.
        """
        fc = FreshnessClass(name, members, bus=self)
        self._freshness_classes[name] = fc
        return fc

    def freshness_class(self, name: str) -> FreshnessClass | None:
        return self._freshness_classes.get(name)

    @property
    def freshness_classes(self) -> dict[str, FreshnessClass]:
        return dict(self._freshness_classes)

    def freshness_report(self) -> dict:
        """Every declared namespace's verdict, every node, every class.

        ⛔ THIS INSTRUMENT CAN REPORT FAILURE. A freshness monitor that only
        ever renders green is not an instrument — so this reports the STALE and
        NODE_SILENT verdicts as first-class rows rather than omitting them, and
        it reports UNDECLARED separately from FRESH so "nothing is wrong" can
        never be confused with "nothing was checked".
        """
        namespaces = {
            name: ns.freshness_status()
            for name, ns in self._namespaces.items()
            if ns.cadence is not Cadence.UNDECLARED
        }
        undeclared = [n for n, ns in self._namespaces.items()
                      if ns.cadence is Cadence.UNDECLARED]
        return {
            "namespaces": namespaces,
            "undeclared_count": len(undeclared),
            "nodes": self._heartbeat_rx.nodes(),
            "classes": {n: c.to_dict()
                        for n, c in self._freshness_classes.items()},
            "stale": [n for n, s in namespaces.items()
                      if s["verdict"] in ("stale", "node_silent",
                                          "never_received", "wire_unknown")],
        }

    def ensure_namespace(self, name: str, engine_config: str | dict = "json",
                         label: str | None = None,
                         scope: Scope = Scope.AUTO) -> Namespace:
        """Get or create a namespace.

        An explicit (non-AUTO) *scope* pins the namespace's scope even when
        it was auto-created earlier (e.g. by a widget self-subscription):
        a scene-declared `scope: global` must win over the has-local-
        subscribers heuristic in _resolve_scope (DP-CIO-5).  We only upgrade
        an AUTO namespace — an already-explicit scope is left untouched so a
        later ensure() can never silently override a deliberate declaration.

        AUTO-CREATE DEFAULT IS ``json``, NOT ``binary`` (2026-08-08, Phase 2a
        step 0 of the bus-connection collapse).  This is the CREATE-ON-DEMAND
        path — it fires exactly when nobody DECLARED the namespace — and a
        2-value engine is the wrong guess for an undeclared namespace in every
        case, not merely an unlucky one.  ``binary`` here rejected a float, a
        dict, a string, and an int alike:

            publish_state("self/context", 0.203)
            -> ValueError: Invalid value '0.203' for binary engine. Valid: ['off','on']

        That single line is why 17 of the console's 18 data sources bypassed the
        bus API entirely and hand-rolled raw paho ([DEC-BUS-ENVELOPE-RAW-PAHO],
        2026-07-19) — which is the root of the 20-connections-from-one-process
        vestige this phase exists to remove.

        WHY ``json`` AND NOT AN INFERRED ENGINE.  Inferring from the first value
        looks smarter and is a trap: ``ContinuousEngine`` is clamped to 0-255, so
        a namespace whose first value happened to be ``42`` would silently start
        REJECTING a later token count of ``475000``.  That converts a loud
        startup error into an unpredictable mid-run one — strictly worse, and
        exactly the silent-failure class this work is eliminating.  The principle
        instead: **validation is opt-in via DECLARATION.**  Declare a namespace
        and you get its engine's guarantees; auto-create one and you get a
        permissive engine, because nobody stated an intent to enforce.

        Explicit ``register_namespace()`` keeps its own ``binary`` default —
        untouched, so a caller that deliberately asks for the old behavior still
        gets it, and every already-declared namespace is completely unaffected.
        """
        if name not in self._namespaces:
            return self.register_namespace(name, engine_config, label=label,
                                           scope=scope)
        ns = self._namespaces[name]
        if label and not ns.label:
            ns.label = label
        if scope != Scope.AUTO and ns.scope == Scope.AUTO:
            ns.scope = scope
        return ns

    @property
    def namespaces(self) -> dict[str, Namespace]:
        """All registered namespaces."""
        return dict(self._namespaces)

    @property
    def global_transport(self):
        """The global (external, e.g. MQTT) transport binding, or None when
        this bus has no external leg.  Exposed so a render-side widget can
        read link health (``transport.is_connected``) without reaching a
        private attribute (used by the Intelligence Console's live-dot)."""
        return self._global

    # --- Module Management ---

    def register_module_hook(self, hook: Callable[[Module], None]) -> None:
        """Register a `hook(module)` fired when a module is registered. This is
        how the DISCOVERY plugin observes registrations WITHOUT the kernel owning
        the `_bus/discovery` announce ([DEC-...-BUS-STANDUP-2026-08-23])."""
        self._module_hooks.append(hook)

    def register_module(self, module: Module):
        """Register a module on the bus."""
        self._modules[module.module_id] = module
        module.attach(self)
        # Module DISCOVERY announce is SHED to the discovery plugin, fired here
        # via the registration hook at the exact site the kernel used to send
        # `_bus/discovery` itself.
        for hook in self._module_hooks:
            try:
                hook(module)
            except Exception as exc:
                logger.error(f"Module hook error for '{module.module_id}': {exc}")
        logger.info(f"Module '{module.module_id}' ({module.role}) registered")

    def unregister_module(self, module_id: str):
        """Unregister a module from the bus."""
        if module_id in self._modules:
            self._modules[module_id].detach()
            del self._modules[module_id]

    # --- Publishing ---

    def publish_state(self, namespace: str, value: Any, source: str = ""):
        """Publish a state change to a namespace.

        The namespace's state engine validates and stores the value.
        All subscribers are notified.
        """
        # Resolve dynamic namespaces
        resolved_ns = self._resolve_namespace(namespace)

        ns = self.ensure_namespace(resolved_ns)

        # BP-EXT-ACCESS: enforce publish whitelist
        if not ns.check_publish_access(source):
            logger.warning(f"Publish denied: '{source}' not authorized for '{resolved_ns}'")
            return

        # `old` IS read through the freshness gate, deliberately: a namespace
        # coming back from STALE has genuinely changed from UNKNOWN to a value,
        # and subscribers need that edge — it is the moment the glass may stop
        # showing a staleness treatment.
        old = ns.value
        # ⛔ `new` IS NOT. Re-reading `ns.value` here would push the value it
        # just accepted back through the expiry check before transmitting it,
        # and for a quiet-by-construction namespace whose NODE is currently
        # marked silent that check answers UNKNOWN — so this bus would publish
        # UNKNOWN over the wire for a value it had in its hand, one line
        # earlier. A publisher reports WHAT IT SENT; only a RECEIVER applies a
        # freshness verdict, which is the whole shape of D4.
        new = ns.publish_state(value, source=source)
        self._notify_pattern_subs(resolved_ns, new, old)

        # Send bus message
        msg = BusMessage(
            namespace=resolved_ns,
            type=MessageType.STATE,
            value=new,
            source=source,
            scope=self._resolve_scope(ns),
            sequence=self._next_seq(),
        )
        self._send_message(msg)

        # REACTIVE BINDINGS (DP-CIO-10) now run through the POST-PUBLISH HOOKS —
        # the default state_manager engine is registered as one, fired at the
        # SAME site the baked-in `_evaluate_bindings` used to run. The kernel no
        # longer derives target values itself ([DX-CONNECTOR-FORWARDS-NEVER-
        # TRANSFORMS]).
        for hook in self._post_publish_hooks:
            try:
                hook(resolved_ns, new)
            except Exception as exc:
                logger.error(f"Post-publish hook error on '{resolved_ns}': {exc}")

    def publish_event(
        self, namespace: str, event_type: str, data: dict | None = None, source: str = ""
    ):
        """Publish a one-shot event."""
        resolved_ns = self._resolve_namespace(namespace)
        ns = self._namespaces.get(resolved_ns)
        if ns:
            ns.publish_event(event_type, data, source=source)

        msg = BusMessage(
            namespace=resolved_ns,
            type=MessageType.EVENT,
            source=source,
            event_type=event_type,
            data=data or {},
            scope=self._resolve_scope(ns) if ns else "auto",
            sequence=self._next_seq(),
        )
        self._send_message(msg)

    def publish_log(self, namespace: str, value: Any, source: str = ""):
        """Publish a log entry to a log-mode namespace (BP-EXT-LOG).

        SHED to the default LOG plugin ([CXC-CS-EXTRACTION-INVENTORY]) — but this
        STAYS a callable bus method delegating to the engine, so `app.py` and the
        module log helpers keep working byte-for-byte (behavior-preservation over
        a purer 'publish to the log namespace directly' shape, per
        [DEC-PHASE0B-LIFECYCLE-HOOK-SEAM-AND-DEFAULT-SHEDS])."""
        return self._log_engine.publish_log(namespace, value, source)

    # NOTE: `publish_critical` / `publish_ack` — the critical/ACK FRAME-STAMP
    # helpers — used to live here. They are transport QoS, not bus-core, and
    # moved BESIDE their retry machinery in `peer/critical.py`
    # (`stamp_and_send_critical` / `stamp_and_send_ack`), which was their only
    # caller ([DEC-...-BUS-STANDUP-2026-08-23] ambiguous-ruling). The kernel
    # keeps only the generic `send_message` seam they now use.

    # --- Subscribing ---

    def subscribe(self, namespace: str, callback: Callable):
        """Subscribe to state changes on a namespace.

        Callback signature: (namespace_name: str, new_value: Any, old_value: Any)
        """
        ns = self.ensure_namespace(namespace)
        ns.subscribe(callback)
        # Also subscribe on transports so they deliver messages for this namespace
        for connector in (*self._local_connectors, *self._external_connectors):
            connector.subscribe(namespace, lambda msg: None)

    def subscribe_events(self, namespace: str, callback: Callable):
        """Subscribe to events on a namespace.

        Callback signature: (namespace_name: str, event_type: str, data: dict)
        """
        ns = self.ensure_namespace(namespace)
        ns.subscribe_events(callback)
        # Subscribe on transports so they deliver messages for this namespace
        for connector in self._external_connectors:
            connector.subscribe(namespace, lambda msg: None)

    def subscribe_pattern(self, pattern: str, callback: Callable):
        """Subscribe to state changes on EVERY namespace matching a
        trailing-glob pattern ([CXC-FL-PATTERN]) — including namespaces
        that first appear after this call.

        Callback signature: (namespace_name: str, new_value: Any, old_value)
        — the actual matching namespace is passed, so a family subscriber
        knows which member spoke.  The callback fires on every matching
        publish (old may equal new: liveness, not just change — discovery
        needs the pulse).

        Pattern language: 'prefix/*' (the prefix and its whole subtree) or
        '*' (everything).  Anything else raises ValueError — this is a
        deliberate bus-semantics boundary (ESCALATED addition, see the
        [SPEC:CXC-FLOW] change record; awaiting ratification).

        Transports that can subscribe broadly do (MQTT maps 'prefix/*' to
        its native '#' wildcard), so matching EXTERNAL traffic arrives even
        for namespaces this bus never registered.
        """
        validate_pattern(pattern)
        if not is_pattern(pattern):
            # exact name: plain subscription semantics, one mechanism
            return self.subscribe(pattern, callback)
        self._pattern_subs.append((pattern, callback))
        for transport in self._external_connectors:
            sub = getattr(transport, "subscribe_pattern", None)
            if sub is not None:
                sub(pattern)

    # --- Foreign channels (peer protocols on the same substrate) --------- #
    # [SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], extended 2026-08-11. The full
    # argument lives on `Transport.send_foreign`; the short version is that
    # three modules relay `compose_projects/intelligence/<id>/{in,out}` — the composeio-channel
    # MCP's OWN wire, a bare topic with a bare payload — and `publish_state`
    # cannot express that without changing the wire. These two methods are how
    # a module reaches a peer protocol WITHOUT importing paho, building a
    # client, or running a second network loop.
    #
    # They are on the BUS, not on the transport, so a caller never holds a
    # transport object. `bus.global_transport` is a status accessor; a module
    # that reached through it to call `send_foreign` would have re-forked the
    # substrate choice, which is the defect, not the fix.

    def publish_foreign(self, channel: str, payload, *, qos: int = 1) -> bool:
        """Carry *payload* to a FOREIGN *channel* over the global transport.

        Returns True iff the transport carried it. A `composeio/...` channel
        RAISES (`Transport._reject_composeio_channel`) — that address belongs
        to `publish_state`, and hand-framing it here is the bypass this whole
        arc removed.

        NO LOCAL LEG, deliberately. A foreign channel is not a namespace: it
        has no state engine, no scope, and no local subscribers. Delivering a
        copy in-process would invent a second, un-typed message path alongside
        the one the bus already owns.
        """
        if self._global is None:
            logger.error(
                "no global transport — foreign frame to %r NOT DELIVERED. "
                "This bus is local-only; the peer protocol is dark.", channel)
            return False
        # getattr, not a direct call: a duck-typed transport double (the test
        # suite is full of them) predates this seam and must degrade to the
        # same LOUD False as a real transport that cannot carry foreign
        # frames — never an AttributeError inside a message callback.
        send = getattr(self._global, "send_foreign", None)
        if send is None:
            logger.error(
                "transport %s has no send_foreign — foreign frame to %r NOT "
                "DELIVERED", type(self._global).__name__, channel)
            return False
        return bool(send(channel, payload, qos=qos))

    def subscribe_foreign(self, channel: str, callback: Callable) -> bool:
        """Receive FOREIGN traffic on *channel*; `callback(channel, payload_bytes)`.

        The payload arrives UNDECODED. A foreign protocol owns its encoding and
        the waist must not guess — `channel_bridge` relays JSON that may not be
        JSON and falls back to a replace-decoded string, and a bus that had
        already tried `json.loads` would have destroyed that distinction.
        """
        if self._global is None:
            logger.error(
                "no global transport — foreign channel %r will never deliver. "
                "This bus is local-only; the peer protocol is dark.", channel)
            return False
        sub = getattr(self._global, "subscribe_foreign", None)
        if sub is None:
            logger.error(
                "transport %s has no subscribe_foreign — foreign channel %r "
                "will never deliver", type(self._global).__name__, channel)
            return False
        return bool(sub(channel, callback))

    def unsubscribe_pattern(self, pattern: str, callback: Callable):
        """Remove a pattern subscription (transport-level wildcard stays —
        harmless surplus traffic; transports dedupe filters)."""
        try:
            self._pattern_subs.remove((pattern, callback))
        except ValueError:
            pass

    def _notify_pattern_subs(self, namespace: str, new_value, old_value):
        if not self._pattern_subs:
            return
        for pattern, cb in list(self._pattern_subs):
            if pattern_matches(pattern, namespace):
                try:
                    cb(namespace, new_value, old_value)
                except Exception as e:
                    logger.error(f"Pattern subscriber error ({pattern}): {e}")

    def on_message(self, callback: Callable[[BusMessage], None]):
        """Subscribe to ALL bus messages (monitoring/debugging).

        EXACTLY ONE registration ([SPEC:CXC-PATSUB] [CXC-PS-EXACTLY-ONCE]).
        This used to ALSO call `self._local.subscribe_all(callback)`, while
        `_send_message` drove BOTH paths — `self._local.send(message)` fans out
        to the local wildcard subscribers, and the loop at the end of
        `_send_message` then walks `_on_message_callbacks`.  Every monitor saw
        every local publish TWICE, and `off_message` (which only ever removed
        the first registration) could not stop delivery at all.

        `_on_message_callbacks` is the surviving path, deliberately: it fires
        for every publish whether or not the local transport has been started,
        whereas `LocalTransport.send` returns early when it is not running — so
        the count stays 1 across the bus's whole lifecycle instead of being 1
        before `start()` and 2 after.
        """
        self._on_message_callbacks.append(callback)

    def off_message(self, callback: Callable[[BusMessage], None]):
        """Unsubscribe a callback previously registered with on_message()."""
        try:
            self._on_message_callbacks.remove(callback)
        except ValueError:
            pass  # Already removed

    # --- State Access ---

    def get_state(self, namespace: str) -> Any:
        """Get current value of a namespace."""
        ns = self._namespaces.get(namespace)
        return ns.value if ns else None

    # --- Bindings (DP-CIO-10) — SHED to the state_manager plugin ---------- #
    #
    # The reactive binding ENGINE moved to `composeio.plugins.state_manager`
    # ([DEC-...-BUS-STANDUP-2026-08-23]): deriving a target value from a source
    # change is a TRANSFORM, and the kernel must never transform
    # ([DX-CONNECTOR-FORWARDS-NEVER-TRANSFORMS]). These are thin DELEGATING
    # shims to the default-loaded engine, kept so `bus.add_binding(...)` (and
    # config.py's binding wiring) work byte-for-byte as before.

    @property
    def _bindings(self) -> list:
        """The bindings list, now owned by the default state_manager engine.
        Read by `status()`; exposed as a property so the historical attribute
        keeps answering."""
        return self._binding_engine.bindings

    def add_binding(self, source_ns: str, target_ns: str,
                    transform_config: list[dict] | None = None):
        """Add a simple sensor-to-actuator binding (delegates to state_manager)."""
        self._binding_engine.add_binding(source_ns, target_ns, transform_config)

    def add_compound_binding(self, config: dict):
        """Add a compound multi-source binding (delegates to state_manager)."""
        self._binding_engine.add_compound_binding(config)

    def add_event_binding(self, config: dict):
        """Add an event-triggered binding (delegates to state_manager)."""
        self._binding_engine.add_event_binding(config)

    # --- Selectors (DP-CIO-21) ---

    def set_selector(self, selector_ns: str, value: Any):
        """Update a selector value. Affects dynamic namespace resolution."""
        self._selector_values[selector_ns] = value
        # Also publish as a normal state change
        self.publish_state(selector_ns, value, source="selector")

    def _resolve_namespace(self, namespace: str) -> str:
        """Resolve dynamic namespace templates."""
        if sel.is_dynamic(namespace):
            return sel.resolve(namespace, self._selector_values)
        return namespace

    # --- Scope Resolution (DP-CIO-5) ---

    def _resolve_scope(self, ns: Namespace | None) -> str:
        """Resolve an AUTO scope against the transport topology.

        AUTO-RESOLVE IS A UNION, NOT A FIRST MATCH
        ([BP-NS-AUTO-RESOLVE], compliance item [BP-C3],
        [DEC-SCOPE-AUTO-RESOLVE-IS-A-UNION-NOT-FIRST-MATCH], 2026-08-14).

        spec/01 §3.2 states the rule twice — once as the algorithm ("deliver
        locally AND continue to step 2 — local delivery does not prevent
        global") and once as a worked example ("a message to `office_lights`
        with a local subscriber AND a global transport is delivered both
        locally AND globally").

        This function used to return on the FIRST match::

            if ns.has_local_subscribers():
                return "local"          # <- and `_send_message` then skipped
            return "global"             #    the global transport entirely

        LOCAL DELIVERY IS UNCONDITIONAL in `_send_message` — `self._local.send`
        runs before any scope test — so `has_local_subscribers()` never
        ENABLED local delivery. Its only effect was to SUPPRESS the global
        copy. The test was pure harm: it dropped the other machine's traffic
        as an invisible side effect of something entirely unrelated, namely a
        local subscriber appearing. Nothing raised, nothing counted a drop,
        and the symptom reaching an operator was "the other machine sometimes
        doesn't see it" — intermittent, load-dependent and indistinguishable
        from a network fault. That is this fleet's worst failure class, so the
        CODE was fixed to match the spec rather than the spec rewritten to
        document the defect.

        WHAT THE RETURNED STRING NOW MEANS. It is the scope the message
        CARRIES on the wire, and per spec/01 §6.2.1 the resolved scope is what
        MQTT's `_topic_for` puts in `composeio/{scope}/{namespace}`. So an
        auto namespace that is crossing to the global transport must resolve
        `global`: resolving `local` while sending globally would publish onto
        `composeio/local/...`, where no global peer subscribes — the identical
        invisible non-delivery, one layer down.

        WHY THE `self._global` TEST AND NOT `is_connected`. A transport that
        exists but has not finished connecting QUEUES (see
        [DEC-CONSOLE-MQTT-STARTUP-RACE] and `MQTTTransport._pending`), and
        that queue is the whole reason a scene's startup burst is not lost.
        Gating on `is_connected` here would resolve those first messages
        `local` and discard exactly the traffic the queue exists to save.

        AUTO NEVER RESOLVES REMOTE (spec/01 §3.2 step 3): remote requires an
        explicit declaration, so auto-resolution cannot produce accidental
        cross-network traffic.
        """
        if ns is None:
            return "global"
        # An EXPLICIT declaration is always honored. Scope.LOCAL in particular
        # is a deliberate statement of confinement — the union below widens
        # AUTO only, because widening a declared local would be a privacy
        # regression wearing a bug fix's clothes.
        if ns.scope == Scope.LOCAL:
            return "local"
        if ns.scope == Scope.GLOBAL:
            return "global"
        if ns.scope == Scope.REMOTE:
            return "remote"
        # AUTO: local delivery already happened unconditionally, so the only
        # question left is whether a global transport exists to also carry it.
        return "global" if self._global is not None else "local"

    # --- Message Routing ---

    def send_message(self, message: BusMessage) -> None:
        """Route a PRE-BUILT envelope through the kernel's scope routing.

        The public seam for a QoS/protocol layer (e.g. the critical/ACK
        machinery in `peer/critical.py`) that frames its own envelope and needs
        the kernel only to ADDRESS and forward it — routing/addressing is
        legitimately the kernel's job, frame-stamping is not."""
        self._send_message(message)

    def _send_message(self, message: BusMessage):
        """Route a message through appropriate transports based on scope.

        MULTI-ATTACH: every connector of the routed scope-class receives the
        message. `_remember_emitted` runs ONCE before the external group (its
        fingerprint is the message, not the connector), which is why a single
        echo is suppressed across all of them ([CXC-PS-NO-SELF-ECHO])."""
        # Always deliver locally
        for connector in self._local_connectors:
            connector.send(message)

        # Route to global connectors if scope is global or auto-resolved to global
        if message.scope in ("global", "auto") and self._global_connectors:
            # Remember BEFORE the send: a synchronous transport (the in-process
            # peer link, a test double) can hand the echo back inside send(),
            # and a record written afterwards would arrive too late to suppress
            # it ([CXC-PS-NO-SELF-ECHO]).
            self._remember_emitted(message)
            for connector in self._global_connectors:
                connector.send(message)

        # Route to remote connectors if scope is remote
        if message.scope == "remote" and self._remote_connectors:
            self._remember_emitted(message)
            for connector in self._remote_connectors:
                connector.send(message)

        # Log
        self._message_log.append(message)
        if len(self._message_log) > self._log_limit:
            self._message_log = self._message_log[-self._log_limit:]

        # Notify bus-level monitors
        for cb in self._on_message_callbacks:
            try:
                cb(message)
            except Exception as e:
                logger.error(f"Message callback error: {e}")

    def _next_seq(self) -> int:
        """Get next sequence number (uint16 rollover)."""
        self._sequence = (self._sequence + 1) % 65536
        return self._sequence

    # --- Infrastructure Services (DP-CIO-17) ---
    #
    # `_bus/time` and `_bus/heartbeat` publishing SHED to the default TIME and
    # LIVENESS plugins ([CXC-CS-EXTRACTION-INVENTORY] extractions 2/3). The
    # engine code (resolution decision, node-naming payload, cadence-from-
    # declaration) lives in `composeio/plugins/time.py` and `.../liveness.py`.
    # `publish_heartbeat` STAYS a callable bus method delegating to the LIVENESS
    # engine, so existing callers (and the heartbeat test) keep working.

    def publish_heartbeat(self):
        """Publish this node's liveness beat (delegates to the liveness plugin)."""
        self._liveness_engine.publish_heartbeat()

    # --- Introspection ---

    @property
    def message_log(self) -> list[BusMessage]:
        """Recent message log for debugging."""
        return list(self._message_log)

    def status(self) -> dict:
        """System status overview (DP-CIO-23 namespace registry)."""
        return {
            "bus_id": self.bus_id,
            "namespaces": {
                name: ns.to_dict() for name, ns in self._namespaces.items()
            },
            "modules": {
                mid: m.discovery_info() for mid, m in self._modules.items()
            },
            "bindings": [
                {"type": b["type"], "output": b.get("target") or b.get("output", "")}
                for b in self._bindings
            ],
            "transports": {
                "local": self._local.is_connected,
                "global": self._global.is_connected if self._global else None,
                "remote": self._remote.is_connected if self._remote else None,
            },
            "message_count": len(self._message_log),
        }


# --- Helper functions ---
#
# `_parse_condition_namespace` and `_compare` — the binding-expression helpers —
# moved WITH the engine to `composeio.plugins.state_manager`
# ([DEC-...-BUS-STANDUP-2026-08-23]). They were used only by the bindings engine,
# which is no longer in the kernel.
