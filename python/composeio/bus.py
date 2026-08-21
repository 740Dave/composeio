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
import time
import logging
from typing import Any, Callable
from composeio.namespace import Namespace, Scope, Authority, Cadence
from composeio.freshness import (
    UNKNOWN, FreshnessClass, HeartbeatReceiver, Verdict, is_unknown,
)
from composeio.bus_clock import BusClock, shared_clock
from composeio.state_engine import StateEngine
from composeio.transport import Transport, BusMessage, MessageType
from composeio.transport_local import LocalTransport
from composeio.module import Module
from composeio import selector as sel

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
        self._heartbeat_rx = HeartbeatReceiver(
            self._mono, default_interval_s=BUS_HEARTBEAT_INTERVAL_S)
        #: namespace -> the node id last seen publishing it, so a quiet-by-
        #: construction namespace can be routed to its node's heartbeat.
        self._ns_publisher: dict[str, str] = {}
        self._freshness_classes: dict[str, FreshnessClass] = {}
        self._namespaces: dict[str, Namespace] = {}
        self._modules: dict[str, Module] = {}
        self._bindings: list[dict] = []
        self._sequence: int = 0
        self._bus_time: float = time.time()

        # Transport layers (DP-CIO-5)
        self._local = local_transport or LocalTransport()
        self._global = global_transport
        self._remote = remote_transport

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
        self._clock_interval: float | None = None
        self._started_at: float = time.time()
        #: subject -> owning CountdownService, so two derivers on one subject
        #: fail loudly instead of interleaving their arithmetic.
        self._countdown_claims: dict[str, Any] = {}

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
        fps = self._emitted_fingerprints
        fps[self._echo_fingerprint(message)] = None
        while len(fps) > self.SELF_ECHO_MEMORY:
            del fps[next(iter(fps))]

    def _is_own_echo(self, message: BusMessage) -> bool:
        """True if *message* is this bus's own emission coming back.

        CONSUMES the record on a match, so exactly ONE echo is suppressed per
        emission — a second copy (a genuinely duplicated publisher, which is a
        different defect with a different fix, [CXC-IC-SINGLETON]) still gets
        through and stays visible rather than being silently absorbed here.
        """
        return self._emitted_fingerprints.pop(
            self._echo_fingerprint(message), _MISSING) is not _MISSING

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
        self._local.start()
        if self._global:
            self._wire_transport_incoming(self._global)
            self._global.start()
        if self._remote:
            self._wire_transport_incoming(self._remote)
            self._remote.start()
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
            self._publish_bus_time()
            self.publish_heartbeat()
            self._start_clock()
        logger.info(f"Bus '{self.bus_id}' started")

    # --- THE 1 Hz SIGNAL  [DEC-BUS-TIME-IS-A-REAL-1HZ-SIGNAL] -------------- #

    def _publish_refresh_intervals(self) -> None:
        intervals = self.declared_refresh_intervals()
        if not intervals:
            return
        for transport in (self._local, self._global, self._remote):
            if transport is None:
                continue
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
        """Attach the periodic infrastructure publishers to the process clock.

        ⛔ THE CADENCE IS READ FROM THE NAMESPACE, NEVER FROM A LITERAL. That is
        the point of the whole change: `_bus/time` is the fleet's first namespace
        that is PERIODIC BY CONSTRUCTION, so it is [D4]'s first real consumer, and
        a hard-coded 1.0 here would make the declaration decorative on the exact
        case meant to prove it. Re-declare the namespace at 0.25 s and this runs
        at 0.25 s, with no code change anywhere.
        """
        self._clock = shared_clock()
        for ns_name, handler in (("_bus/time", self._tick_bus_time),
                                 ("_bus/heartbeat", self._tick_heartbeat)):
            ns = self._namespaces.get(ns_name)
            if ns is None or ns.cadence is not Cadence.PERIODIC:
                # An operator who re-declares `_bus/time` as event-driven has
                # said "no clock here", and that is honoured rather than
                # overridden — a declaration the code ignores is not a
                # declaration.
                continue
            self._clock.attach(handler, interval=ns.refresh_interval)
            if ns_name == "_bus/time":
                self._clock_interval = ns.refresh_interval

    def _stop_clock(self) -> None:
        if self._clock is None:
            return
        self._clock.detach(self._tick_bus_time)
        self._clock.detach(self._tick_heartbeat)
        self._clock = None
        self._clock_interval = None

    def _tick_bus_time(self, now: float) -> None:
        self._publish_bus_time(now=now)

    def _tick_heartbeat(self, now: float) -> None:
        self.publish_heartbeat()

    @property
    def clock_interval(self) -> float | None:
        """The cadence this bus's clock is actually running at, or None."""
        return self._clock_interval

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
        self._local.stop()
        if self._global:
            self._global.stop()
        if self._remote:
            self._remote.stop()
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
        """
        return self._heartbeat_rx.is_alive(node)

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
        return self._heartbeat_rx.is_alive(node)

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
            if node:
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

    def register_module(self, module: Module):
        """Register a module on the bus."""
        self._modules[module.module_id] = module
        module.attach(self)
        # Publish discovery
        self._send_message(BusMessage(
            namespace="_bus/discovery",
            type=MessageType.DISCOVERY,
            source=module.module_id,
            data=module.discovery_info(),
            scope="local",
        ))
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

        # Evaluate bindings (DP-CIO-10)
        self._evaluate_bindings(resolved_ns, new)

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
        """Publish a log entry to a log-mode namespace (BP-EXT-LOG)."""
        ns = self.ensure_namespace(namespace)
        if ns.mode != "log":
            logger.warning(f"publish_log to non-log namespace '{namespace}', treating as state")
            return self.publish_state(namespace, value, source=source)

        # BP-EXT-ACCESS: enforce publish whitelist
        if not ns.check_publish_access(source):
            logger.warning(f"Publish denied: '{source}' not in publish list for '{namespace}'")
            return

        ns.publish_log(value, source=source)
        msg = BusMessage(
            namespace=namespace,
            type=MessageType.LOG_ENTRY,
            value=value,
            source=source,
            scope=self._resolve_scope(ns),
            sequence=self._next_seq(),
        )
        self._send_message(msg)

    def publish_critical(self, namespace: str, value: Any, cid: int, source: str = ""):
        """Publish a ``pri: critical`` STATE message carrying its correlation id
        ([AMEND:BP-ACK] BP-ACK-WHEN / BP-C13).

        The priority + cid ride ``BusMessage.data`` (the ``_priority`` / ``_cid``
        convention already used by capture/replay). The origin-side ack-pending
        machinery ([SPEC:CIO-BUS-PEER] critical.py) owns retry/fault; this method
        only puts the frame on the bus so it routes over whatever transport the
        bus carries (in-process, MQTT, or the peer channel).
        """
        resolved_ns = self._resolve_namespace(namespace)
        ns = self.ensure_namespace(resolved_ns)
        old = ns.value
        try:
            ns.publish_state(value, source=source)
        except ValueError:
            pass  # keep the frame flowing even if the local engine is strict
        self._notify_pattern_subs(resolved_ns, ns.value, old)
        msg = BusMessage(
            namespace=resolved_ns,
            type=MessageType.STATE,
            value=value,
            source=source,
            scope="global",
            sequence=self._next_seq(),
            data={"_priority": "critical", "_cid": cid},
        )
        self._send_message(msg)

    def publish_ack(self, namespace: str, status: Any, cid: int, source: str = ""):
        """Publish an application-level ``ack`` (type code 8) for a critical
        message ([AMEND:BP-ACK] BP-TYPE-ACK / BP-C12).

        ``status`` is ``"ok"`` / ``"reject"`` / ``"error"`` (or an extended
        ``{"status": "reject", "reason": ...}`` dict). Published by the acking
        subscriber, correlated to the original message by ``cid``.
        """
        msg = BusMessage(
            namespace=namespace,
            type=MessageType.ACK,
            value=status,
            source=source,
            scope="global",
            sequence=self._next_seq(),
            data={"_cid": cid},
        )
        self._send_message(msg)

    # --- Subscribing ---

    def subscribe(self, namespace: str, callback: Callable):
        """Subscribe to state changes on a namespace.

        Callback signature: (namespace_name: str, new_value: Any, old_value: Any)
        """
        ns = self.ensure_namespace(namespace)
        ns.subscribe(callback)
        # Also subscribe on transports so they deliver messages for this namespace
        self._local.subscribe(namespace, lambda msg: None)
        if self._global:
            self._global.subscribe(namespace, lambda msg: None)
        if self._remote:
            self._remote.subscribe(namespace, lambda msg: None)

    def subscribe_events(self, namespace: str, callback: Callable):
        """Subscribe to events on a namespace.

        Callback signature: (namespace_name: str, event_type: str, data: dict)
        """
        ns = self.ensure_namespace(namespace)
        ns.subscribe_events(callback)
        # Subscribe on transports so they deliver messages for this namespace
        if self._global:
            self._global.subscribe(namespace, lambda msg: None)
        if self._remote:
            self._remote.subscribe(namespace, lambda msg: None)

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
        for transport in (self._global, self._remote):
            if transport is not None:
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

    # --- Bindings (DP-CIO-10) ---

    def add_binding(self, source_ns: str, target_ns: str, transform_config: list[dict] | None = None):
        """Add a simple sensor-to-actuator binding.

        When source_ns changes, the new value is transformed and published to target_ns.
        No intermediary server needed — the bus handles it directly.
        """
        from composeio.transforms import TransformChain
        chain = TransformChain.from_config(transform_config) if transform_config else None
        self._bindings.append({
            "type": "simple",
            "source": source_ns,
            "target": target_ns,
            "transform": chain,
        })
        logger.debug(f"Binding: {source_ns} → {target_ns}")

    def add_compound_binding(self, config: dict):
        """Add a compound namespace binding — multi-source conditions.

        Extends DP-CIO-10 for cases where an output depends on multiple inputs.
        Example: FTL jump LED depends on both ftl_arm state AND ftl_cap_charge level.

        Config format:
            {
                "output_namespace": "ftl_jump_led",
                "requires": 'ftl_arm == "on"',
                "default": "off",
                "conditions": [
                    {"when": {"ftl_cap_charge": "< 50"}, "value": "blink_fast"},
                    {"when": {"ftl_cap_charge": ">= 50", "ftl_coord_status": "!= committed"}, "value": "blink_slow"},
                    {"when": {"ftl_cap_charge": ">= 50", "ftl_coord_status": "== committed"}, "value": "solid"},
                ]
            }

        Evaluated whenever ANY referenced input namespace changes.
        First matching condition wins (top-to-bottom priority).
        """
        output_ns = config["output_namespace"]
        requires = config.get("requires")
        default = config.get("default", "off")
        conditions = config.get("conditions", [])

        # Collect all input namespaces referenced
        input_namespaces = set()
        if requires:
            ns_name = _parse_condition_namespace(requires)
            if ns_name:
                input_namespaces.add(ns_name)
        for cond in conditions:
            for ns_name in cond.get("when", {}).keys():
                input_namespaces.add(ns_name)

        binding = {
            "type": "compound",
            "output": output_ns,
            "requires": requires,
            "default": default,
            "conditions": conditions,
            "input_namespaces": input_namespaces,
        }
        self._bindings.append(binding)
        logger.debug(f"Compound binding: {input_namespaces} → {output_ns}")

    def add_event_binding(self, config: dict):
        """Add an event-triggered binding with side effects.

        Config format:
            {
                "trigger": 'ftl_jump.event == "jump"',
                "output_namespace": "ftl_jump_led",
                "sequence": [{"pulse": 5, "duration": "1s"}, {"set": "off"}],
                "side_effects": [
                    {"set": "ftl_arm", "value": "off"},
                    {"set": "ftl_cap_charge", "value": 0},
                ]
            }
        """
        trigger = config["trigger"]
        # Parse "namespace.event == value" format
        parts = trigger.split(".")
        trigger_ns = parts[0]
        rest = parts[1] if len(parts) > 1 else ""

        binding = {
            "type": "event_trigger",
            "trigger_namespace": trigger_ns,
            "trigger_condition": trigger,
            "output": config.get("output_namespace"),
            "sequence": config.get("sequence", []),
            "side_effects": config.get("side_effects", []),
        }
        self._bindings.append(binding)

        # Subscribe to events on the trigger namespace
        self.subscribe_events(trigger_ns, lambda ns, evt, data: self._handle_event_binding(binding, ns, evt, data))
        logger.debug(f"Event binding: {trigger_ns} event → {config.get('output_namespace')}")

    def _evaluate_bindings(self, source_ns: str, value: Any):
        """Evaluate all bindings triggered by a source namespace change."""
        for binding in self._bindings:
            if binding["type"] == "simple":
                if binding["source"] == source_ns:
                    result = value
                    if binding["transform"]:
                        result = binding["transform"].apply(value)
                    if result is not None:
                        self.publish_state(binding["target"], result, source=f"binding:{source_ns}")

            elif binding["type"] == "compound":
                if source_ns in binding["input_namespaces"]:
                    self._evaluate_compound_binding(binding)

    def _evaluate_compound_binding(self, binding: dict):
        """Evaluate a compound binding against current namespace states."""
        output_ns = binding["output"]

        # Check requires condition
        if binding["requires"]:
            if not self._eval_condition_expr(binding["requires"]):
                # Requires not met — set to default
                current = self.get_state(output_ns)
                if current != binding["default"]:
                    self.publish_state(output_ns, binding["default"], source="compound_binding")
                return

        # Evaluate conditions top-to-bottom, first match wins
        for cond in binding["conditions"]:
            when = cond.get("when", {})
            if self._eval_when_conditions(when):
                current = self.get_state(output_ns)
                target_val = cond["value"]
                if current != target_val:
                    self.publish_state(output_ns, target_val, source="compound_binding")
                return

        # No condition matched — set to default
        current = self.get_state(output_ns)
        if current != binding["default"]:
            self.publish_state(output_ns, binding["default"], source="compound_binding")

    def _eval_condition_expr(self, expr: str) -> bool:
        """Evaluate a simple condition expression like 'ftl_arm == "on"'."""
        # Parse: namespace op "value"
        parts = expr.strip().split(None, 2)
        if len(parts) != 3:
            return False
        ns_name, op, expected = parts
        expected = expected.strip('"').strip("'")
        actual = self.get_state(ns_name)
        if actual is None:
            return False
        return _compare(actual, op, expected)

    def _eval_when_conditions(self, when: dict) -> bool:
        """Evaluate a 'when' dict — all conditions must be true (AND)."""
        for ns_name, expr in when.items():
            actual = self.get_state(ns_name)
            # Parse the expression: "< 50", ">= 50", "== committed", "!= committed"
            parts = expr.strip().split(None, 1)
            if len(parts) != 2:
                return False
            op, expected = parts
            expected = expected.strip('"').strip("'")
            # Handle None: only != comparisons can be true with None
            if actual is None:
                if op == "!=":
                    return True  # None != anything is true
                return False
            if not _compare(actual, op, expected):
                return False
        return True

    def _handle_event_binding(self, binding: dict, ns_name: str, event_type: str, data: dict):
        """Handle an event-triggered binding."""
        # Check if event matches trigger condition
        trigger = binding["trigger_condition"]
        # Parse "namespace.event == value"
        if ".event" in trigger:
            parts = trigger.split(".event")
            expected_ns = parts[0].strip()
            rest = parts[1].strip()
            # Parse == "value"
            if rest.startswith("=="):
                expected_evt = rest[2:].strip().strip('"').strip("'")
                if ns_name != expected_ns or event_type != expected_evt:
                    return
            else:
                return
        else:
            return

        # Execute side effects
        for effect in binding.get("side_effects", []):
            if "set" in effect:
                target = effect["set"]
                # Handle "ns → value" format
                if "→" in target:
                    t_ns, t_val = target.split("→", 1)
                    self.publish_state(t_ns.strip(), t_val.strip().strip('"'), source="event_binding")
                elif "value" in effect:
                    self.publish_state(target, effect["value"], source="event_binding")

        # Execute output sequence (simplified — just set values)
        for step in binding.get("sequence", []):
            if "set" in step:
                if binding["output"]:
                    self.publish_state(binding["output"], step["set"], source="event_binding")

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

    def _send_message(self, message: BusMessage):
        """Route a message through appropriate transports based on scope."""
        # Always deliver locally
        self._local.send(message)

        # Route to global transport if scope is global or auto-resolved to global
        if message.scope in ("global", "auto") and self._global:
            # Remember BEFORE the send: a synchronous transport (the in-process
            # peer link, a test double) can hand the echo back inside send(),
            # and a record written afterwards would arrive too late to suppress
            # it ([CXC-PS-NO-SELF-ECHO]).
            self._remember_emitted(message)
            self._global.send(message)

        # Route to remote transport if scope is remote
        if message.scope == "remote" and self._remote:
            self._remember_emitted(message)
            self._remote.send(message)

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

    def _publish_bus_time(self, now: float | None = None):
        """Publish current time to `_bus/time` — WHOLE UNIX SECONDS.

        ⛔ THE RESOLUTION DECISION, and it is `int` seconds rather than float or
        millis, on four grounds:

        1. THE CONSUMERS DECIDE, AND THEY ARE 8-BIT. The named consumers are a
           CH32V003 and a TM1637 panel rendering "T-minus 4:32". Whole seconds
           are a uint32 — 4 bytes, integer compare, no FPU. A float is 8 bytes
           and a soft-float parse on parts that have no hardware for it.
        2. spec/01 ALREADY SPECIFIES uint32 SECONDS, with a wrap ~136 years out.
           Widening the wire type to gain precision no consumer asked for would
           break a freshly corrected spec to serve nobody.
        3. THE ONE USE THAT WOULD DEMAND SUB-SECOND WAS EXPLICITLY REJECTED.
           [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML] ruled staleness
           receiver-side precisely BECAUSE this signal is one-way with no delay
           compensation, against parts whose internal oscillators drift ~1%
           (~36 s/hour) — clock-offset error exceeding the staleness being
           measured. Measuring freshness against `_bus/time` is the thing it must
           not be used for, so its resolution is not a freshness question.
        4. A 1 Hz SIGNAL CANNOT CARRY SUB-SECOND TRUTH ANYWAY. Delivered over a
           retained MQTT hop with unbounded jitter, millisecond digits would be
           precision without accuracy — which reads as authority and is not.

        WHAT IT IS FOR, now that it is real: a wall-clock ANNOUNCEMENT — "the
        time is 11:00:01 PDT" — that a node with no RTC can display, log against
        and schedule from. Its first real consumer is the countdown service.
        """
        self._bus_time = time.time() if now is None else now
        self._send_message(BusMessage(
            namespace="_bus/time",
            type=MessageType.TIME_SYNC,
            value=int(self._bus_time),
            source=self.bus_id,
            scope="global",
            sequence=self._next_seq(),
        ))

    def publish_heartbeat(self):
        """Publish this node's liveness beat.

        ⛔ THIS FUNCTION HAD NO CALLER. Repo-wide it occurred exactly once — its
        own definition — while spec/01 §2.5/§5.2/§9.2 described heartbeat cadence
        and heartbeat-timeout-driven degraded mode as working behaviour. It is
        now driven by the bus clock at the cadence `_bus/heartbeat` declares.

        WHY IT IS LOAD-BEARING AND NOT BELT-AND-BRACES. D4: a per-namespace
        timeout cannot distinguish "publisher dead" from "event-driven with
        nothing to say". The fleet's most-watched namespace, `throttle/*`, is
        quiet by construction — so for that entire class the heartbeat is the
        ONLY liveness signal that exists.

        ⚠ WHAT IS STILL MISSING, stated here so it is not mistaken for done: this
        is the PUBLISHER only. The receiver half — a monitor counting missed
        intervals and driving a node to degraded — does not exist anywhere in
        this repo, and spec/01 currently describes it as though it does.

        THE PAYLOAD NAMES ITS NODE. A beat that does not say whose it is cannot
        mark a node dead; it is a pulse with no patient. `source` carries it on
        the envelope, and `val.node` carries it in the body too, because the
        envelope's `src` is dropped by some downstream projections and a
        liveness record that loses its subject is worse than absent.
        """
        self._send_message(BusMessage(
            namespace="_bus/heartbeat",
            type=MessageType.HEARTBEAT,
            value={
                "node": self.bus_id,
                "uptime_s": int(time.time() - self._started_at),
                # The DECLARED cadence rides along, so a receiver can derive the
                # timeout without having to be configured with a copy of it —
                # the second copy being the thing that eventually disagrees.
                "interval_s": self._heartbeat_interval(),
            },
            source=self.bus_id,
            scope="global",
            sequence=self._next_seq(),
        ))

    def _heartbeat_interval(self) -> float | None:
        ns = self._namespaces.get("_bus/heartbeat")
        return None if ns is None else ns.refresh_interval

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

def _parse_condition_namespace(expr: str) -> str | None:
    """Extract namespace name from a condition expression like 'ftl_arm == "on"'."""
    parts = expr.strip().split(None, 1)
    return parts[0] if parts else None


def _compare(actual: Any, op: str, expected: str) -> bool:
    """Compare actual value against expected using operator.

    Supports: ==, !=, <, >, <=, >=
    For numeric comparisons, attempts float conversion.
    """
    if op == "==":
        return str(actual) == expected
    if op == "!=":
        return str(actual) != expected
    # Numeric comparisons
    try:
        a = float(actual)
        e = float(expected)
    except (TypeError, ValueError):
        return False
    if op == "<":
        return a < e
    if op == ">":
        return a > e
    if op == "<=":
        return a <= e
    if op == ">=":
        return a >= e
    return False
