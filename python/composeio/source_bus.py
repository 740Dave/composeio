#!/usr/bin/env python3
"""source_bus — the ONE seam a console data source uses to reach ComposeBus.

[SPEC:CXC-SOURCES-ON-BUS] slice 1 ([CXC-SOB-HANDLE], [CXC-SOB-DECLARE],
[CXC-SOB-RUN-BLOCKS]).

THE THESIS ([CXC-SOB-THESIS], David 2026-08-08). "ComposeBus might for some
devices be instantiated as MQTT, and for some it might ride on CANBUS. But in
both cases the ComposeIO/UX app shouldn't have to care how ComposeBus is
instantiated — it should just listen to / publish to ComposeBus."  A source that
writes ``client.publish(f"composeio/global/{ns}", ...)`` has hardcoded MQTT; on a
CAN-only device it publishes into the void, with no error. This module is the
narrow waist that removes the substrate name from every source: a source states
a NAMESPACE and a SCOPE, and `composeio.transport_registry` decides — once, from
a ``type:`` declaration — what carries it.

THREE FUNCTIONS, AND WHY EACH IS HERE RATHER THAN COPIED 20 TIMES
-----------------------------------------------------------------
``acquire_bus``  — get the bus handle. Injected in-process, self-created
                   standalone. A per-source copy would re-fork the transport
                   choice, which is the defect this whole arc removes.
``declare``      — pin the namespace's SCOPE explicitly, and RAISE on a
                   disagreement. See below: this is the load-bearing one.
``park``         — block for the source's lifetime on a stop Event. See below:
                   this is the dangerous one.

WHY ``declare`` RAISES ([CXC-SOB-DECLARE]). ``ComposeIOBus.ensure_namespace``
only upgrades an ``AUTO`` namespace and otherwise leaves an existing explicit
scope untouched — CONFIRMED by experiment 2026-08-08: declare ``Scope.LOCAL``,
then ``ensure(GLOBAL)``, and it stays LOCAL with no warning of any kind. So a
scene-declared ``scope: global`` in ``console.compose.yaml`` silently wins over a
source's ``Scope.LOCAL`` (and vice versa). A scope disagreement between the scene
and the source is a REAL defect — one of them is wrong about who consumes the
namespace — and resolving it silently is the exact failure class this arc exists
to remove. So ``declare`` reads the namespace FIRST and raises at startup with
both values named, rather than letting the bus pick a winner.

Relatedly, ``Scope.AUTO`` is FORBIDDEN here. ``_resolve_scope`` falls back to
"local if local subscribers exist, global otherwise", and that heuristic already
caused a real defect — [DEC-COMPOSER-SEND-GLOBAL-SCOPE], where a composer
self-subscribed its own outbound namespace, auto-resolved to LOCAL, and the
message died in-process LOOKING SENT. The console is full of self-subscribing
widgets; leaving scope to the heuristic would reproduce that across 30+
namespaces at once.

WHY ``park`` EXISTS AT ALL ([CXC-SOB-RUN-BLOCKS]) — the non-obvious hazard.
``DataSourceSupervisor._guarded`` treats a CLEAN RETURN from a source's ``run()``
as "this source yielded its host singleton" and RE-INVOKES it after
``reclaim_poll_s`` (the reclaim-on-release behaviour from [SPEC:CXC-IC-SINGLETON]
IC-C27, generalized to the source class). Today the two subscribing sources stay
alive by blocking in ``client.loop_forever()``. Once they publish/subscribe
through the bus there is no ``loop_forever`` to block on — and a converted source
that simply RETURNS does not error, it enters a reclaim RETRY LOOP that
re-subscribes every 5 seconds forever. The failure is a slow leak of duplicate
routes plus a log the operator learns to ignore. Hence: every converted source
parks HERE, and ``test_sources_run_blocks.py`` enforces it for the whole class.

WHAT THIS MODULE IS NOT. It is not an uplink-policy layer
([CXC-SOB-NO-POLICY-LAYER]). There is deliberately no table of namespaces, no
allow-list, no ``uplink.yaml``: ``scope`` already IS the uplink control
(``bus._send_message`` forwards to the global transport only when scope is
global/auto), and a parallel mechanism for it was built and RETRACTED once
already. Declarations live WITH their publisher; the inventory in the spec is an
audit reference, not a runtime artifact.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from composeio.bus import ComposeIOBus
from composeio.namespace import Scope

logger = logging.getLogger("composeio.source_bus")

__all__ = [
    "acquire_bus", "declare", "declared_namespaces", "manifest", "park",
    "parse_mqtt_url", "mqtt_spec", "reset_for_tests", "ScopeConflictError",
]


# --------------------------------------------------------------------------- #
# URL -> transport spec
# --------------------------------------------------------------------------- #
def parse_mqtt_url(url: str) -> Tuple[str, int]:
    """``'mqtt://host:port'`` -> ``(host, port)``. Bare ``'host'`` -> port 1883.

    Re-exported from ``bus_client`` rather than re-implemented: every data source
    used to carry its own four-line copy of this parser, which is the same fork
    at a smaller scale.
    """
    from composeio.bus_client import parse_mqtt_url as _p
    return _p(url)


def mqtt_spec(mqtt_url: str) -> dict:
    """The ``transports:``-shaped spec dict for an ``mqtt://`` URL.

    A source names a SPEC, never a transport class. Constructing
    ``MQTTTransport`` directly here would reintroduce [CXC-SOB-THESIS]'s defect
    one level down: the standalone path would be hardcoded to MQTT even though
    the in-process path is substrate-free.
    """
    host, port = parse_mqtt_url(mqtt_url)
    return {"type": "mqtt", "broker": host, "port": port}


# --------------------------------------------------------------------------- #
# acquire_bus — injected in-process, self-created standalone
# --------------------------------------------------------------------------- #
# key -> the one self-created bus for this process.
#
# WHY MEMOIZE. A source's `bus=` kwarg is the primary path and always wins; this
# memo governs only the SELF-CREATE fallback. Without it, N standalone-shaped
# sources in one process would build N buses and therefore N transports — i.e.
# the connection fan-out this work exists to collapse, rebuilt one layer up. With
# it, the fallback collapses exactly the way `bus_client.acquire()` already does
# for raw paho.
#
# This is NOT the ambient-accessor design [DD-SOB-HANDLE-SEAM] rejected: no
# source READS the memo to find its bus, it is not a process-wide `current()`,
# and `reset_for_tests()` keeps it from leaking between test cases.
_BUSES: Dict[Tuple[str, Any], ComposeIOBus] = {}
_BUS_LOCK = threading.RLock()
_BUS_REFCOUNT: Dict[Tuple[str, Any], int] = {}


def _spec_key(spec: dict) -> Tuple[str, Any]:
    ttype = str(spec.get("type", "")).strip().lower()
    if ttype == "mqtt":
        return ("mqtt", (spec.get("broker"), int(spec.get("port", 1883))))
    return (ttype, tuple(sorted((k, str(v)) for k, v in spec.items() if k != "type")))


def acquire_bus(mqtt_url: str = "", *, label: str,
                bus: Optional[ComposeIOBus] = None,
                transport_spec: Optional[dict] = None,
                transport_builder: Optional[Callable[..., Any]] = None,
                log=None) -> ComposeIOBus:
    """The bus this source publishes/subscribes on.

    ``bus`` is the INJECTED handle (the console's own ``ComposeIOBus``, threaded
    down by ``build_console_sources``); when it is given it is returned as-is and
    NO transport is constructed. That is the in-process path, and it is what
    makes a source testable with a fake and Stage B's scope flips meaningful.

    ``bus=None`` MUST self-create and MUST NOT fail — that is the standalone path
    [DEC-INPROCESS-SOURCES] depends on. The transport comes from
    ``composeio.transport_registry.build`` (an unknown ``type:`` raises; a known
    type whose backend is merely unreachable returns None), never from a
    hardcoded ``MQTTTransport``.

    A self-created bus whose transport is unavailable degrades LOUDLY: the source
    still runs, publishing local-only, and says so on stderr. Silently running
    local-only is precisely the defect ``transport_registry`` was added to remove,
    so it must never be silent here either.
    """
    if bus is not None:
        return bus

    # `log=None` -> resolve sys.stderr AT CALL TIME, never as a default argument.
    # A `log=sys.stderr` default binds the stream that existed at IMPORT time, so
    # any caller that later redirects stderr (a test harness, a console that
    # captures its children's output) silently loses these lines — including the
    # SOB-C3 startup manifest, whose entire job is to be readable in the boot log.
    log = sys.stderr if log is None else log
    spec = dict(transport_spec) if transport_spec else mqtt_spec(mqtt_url)
    key = _spec_key(spec)
    build = transport_builder
    if build is None:
        from composeio import transport_registry
        build = transport_registry.build

    with _BUS_LOCK:
        existing = _BUSES.get(key)
        if existing is not None:
            _BUS_REFCOUNT[key] = _BUS_REFCOUNT.get(key, 0) + 1
            log.write(f"[source_bus] {label} joined the self-created bus "
                      f"{existing.bus_id!r} ({key[0]}) — now "
                      f"{_BUS_REFCOUNT[key]} source(s), 1 transport\n")
            return existing

        transport = build(spec, app_id=label)
        if transport is None:
            log.write(f"[source_bus] !! {label}: transport {spec.get('type')!r} is "
                      f"UNAVAILABLE ({spec!r}) — this bus is LOCAL-ONLY, nothing "
                      f"it publishes will leave the process\n")
        # bus_id is the source's own label so broker-side / log-side
        # identification stays recognisable ([CXC-SOB-HANDLE] default). With the
        # memo the FIRST source to arrive names the shared bus, exactly as the
        # first caller names `bus_client`'s shared client today.
        new_bus = ComposeIOBus(bus_id=label, global_transport=transport)
        # announce_time=False: a source's bus is a LEAF, not a bus master. The
        # capture-diff caught this — `bus.start()` emits a `_bus/time` tick, so
        # every self-created source bus was putting a SECOND publisher on a
        # namespace the hosting app owns. That is a wire change NOT on
        # [CXC-SOB-WIRE-DELTAS]'s list of four, and the spec is explicit that
        # anything not on the list is a defect rather than an expected delta.
        new_bus.start(announce_time=False)
        _BUSES[key] = new_bus
        _BUS_REFCOUNT[key] = 1
        log.write(f"[source_bus] {label} created THE self-created bus for "
                  f"{spec.get('type')!r} {key[1]!r} (1 transport for all "
                  f"self-creating sources in this process)\n")
        return new_bus


# --------------------------------------------------------------------------- #
# declare — explicit scope, conflict is LOUD
# --------------------------------------------------------------------------- #
class ScopeConflictError(RuntimeError):
    """A namespace already carries an EXPLICIT scope that differs from the one a
    source is declaring.

    Deliberately fatal at startup. ``ensure_namespace`` would silently keep the
    incumbent scope, so the alternative to raising is a source that believes it
    published globally while the message died in-process — a dark surface with no
    error, which is this system's worst failure mode.
    """


# source label -> ordered [(namespace, resolved scope)] it declared.
_DECLARED: Dict[str, List[Tuple[str, str]]] = {}
# source label -> how many namespaces the last emitted manifest line covered.
_MANIFESTED: Dict[str, int] = {}
_DECLARED_LOCK = threading.RLock()


def declare(bus: ComposeIOBus, ns: str, *,
            scope: Scope = Scope.GLOBAL,
            engine: str | dict = "json",
            label: Optional[str] = None,
            source: str = "") -> Any:
    """Declare ``ns`` on ``bus`` with an EXPLICIT scope, before the first publish.

    Raises ``ValueError`` on ``Scope.AUTO`` and ``ScopeConflictError`` when the
    namespace already carries a DIFFERENT explicit scope (see the module
    docstring for why that is fatal rather than merged).

    ``engine`` defaults to ``json`` — permissive by design. Validation is opt-in
    via declaration ([CXC-SOB-BYPASS-EXPIRED]); a data source's payload is a dict
    or a float or a multi-kilobyte list, and guessing a 2-value engine for it is
    how these sources ended up bypassing the bus in the first place.

    ``source`` is the source's log label; it is what ``manifest()`` reports.
    """
    if scope == Scope.AUTO:
        raise ValueError(
            f"source_bus.declare({ns!r}, scope=Scope.AUTO) is FORBIDDEN "
            "([CXC-SOB-DECLARE]). _resolve_scope's 'local if local subscribers "
            "exist, global otherwise' heuristic already produced a real defect "
            "([DEC-COMPOSER-SEND-GLOBAL-SCOPE]: a self-subscribed outbound "
            "namespace auto-resolved to LOCAL and the message died in-process "
            "LOOKING SENT). Name the scope.")

    existing = bus.get_namespace(ns)
    if existing is not None and existing.scope != Scope.AUTO \
            and existing.scope != scope:
        raise ScopeConflictError(
            f"scope conflict on namespace {ns!r}: it is already declared "
            f"{existing.scope.value!r} and {source or 'a source'} is declaring "
            f"{scope.value!r}. ensure_namespace would SILENTLY keep "
            f"{existing.scope.value!r} (it only upgrades an AUTO namespace), so "
            f"one of the two would be wrong about who consumes this namespace "
            f"with no error anywhere. Fix the disagreement — a scene's "
            f"`scope:` and its publisher's declaration must agree.")

    namespace = bus.ensure_namespace(ns, engine_config=engine, label=label,
                                     scope=scope)
    with _DECLARED_LOCK:
        entries = _DECLARED.setdefault(source, [])
        if not any(n == ns for n, _ in entries):
            entries.append((ns, namespace.scope.value))
    return namespace


def declared_namespaces(source: str) -> List[Tuple[str, str]]:
    """``[(namespace, scope)]`` this source declared, in declaration order."""
    with _DECLARED_LOCK:
        return list(_DECLARED.get(source, ()))


def manifest(source: str, *, log=None, only_if_grown: bool = False) -> str:
    """Emit (and return) the line naming every namespace ``source`` declared,
    with its resolved scope.

    This is acceptance criterion SOB-C3's mechanism: "no namespace silently goes
    dark" has to be checkable by reading a boot log, not by reasoning about which
    of 20 threads came up. One line per source, every namespace on it::

        [lts_engine_status] declared: self/lts_engine/status scope=global

    ``only_if_grown=True`` emits ONLY when new namespaces have appeared since the
    last emission for this source, and returns ``""`` otherwise.

    WHY THAT MODE EXISTS. SOB-C3 assumes a source knows its namespace set at
    startup. Two do not: ``self_publisher``'s set is whatever
    ``SelfSensor.parse()`` yields, and ``transcript_publisher``'s is whatever the
    transcript produces — a namespace can legitimately appear for the first time
    an hour into a session. A hardcoded startup list for those would be a LIE
    that passes the audit, and a manifest you cannot trust is worse than none:
    the whole point is that reading it tells you the truth. So they declare
    lazily and call this after each batch. The boot log gets a line as soon as
    the first batch lands, and a further line the moment a new namespace first
    appears; a quiet log means the set is stable.
    """
    log = sys.stderr if log is None else log   # resolved at CALL time — see acquire_bus
    entries = declared_namespaces(source)
    with _DECLARED_LOCK:
        seen = _MANIFESTED.get(source, 0)
        if only_if_grown and len(entries) <= seen:
            return ""
        _MANIFESTED[source] = len(entries)
    if not entries:
        line = (f"[{source}] declared: NOTHING — this source publishes without "
                f"declaring, which SOB-C3 treats as a defect\n")
    else:
        body = " ".join(f"{ns} scope={sc}" for ns, sc in entries)
        prefix = "declared" if seen == 0 else f"declared (+{len(entries) - seen} new)"
        line = f"[{source}] {prefix}: {body}\n"
    log.write(line)
    try:
        log.flush()
    except Exception:                            # noqa: BLE001 — a StringIO in tests
        pass
    return line


# --------------------------------------------------------------------------- #
# park — the run()-must-block invariant
# --------------------------------------------------------------------------- #
def park(stop: Optional[threading.Event] = None, *, poll_s: float = 1.0,
         on_tick: Optional[Callable[[], None]] = None) -> None:
    """Block until ``stop`` is set. THE way a converted source stays alive.

    ``stop=None`` creates a private Event and parks forever — the standalone
    case, where the process itself is the lifetime and Ctrl-C ends it.

    The wait is POLLED rather than a single unbounded ``Event.wait()`` so a
    ``KeyboardInterrupt`` lands promptly when a source is run as a script; on the
    supervisor's daemon threads the poll is irrelevant, the Event wakes it
    immediately.

    ``on_tick`` runs once per poll — for a source that has periodic work to do
    but no blocking client to hang it off.

    DO NOT replace this with ``client.loop_forever()``. The bus's transport
    already runs its own paho loop; a second one conflicts, and the grep guard
    ([CXC-SOB-THESIS]) fails the build for it.
    """
    ev = stop if stop is not None else threading.Event()
    try:
        while not ev.wait(poll_s):
            if on_tick is not None:
                on_tick()
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------------------- #
# test support
# --------------------------------------------------------------------------- #
def reset_for_tests() -> None:
    """Drop the self-created-bus memo and the declaration ledger.

    Does NOT stop the buses: tests inject fakes, and stopping a real one here
    would make this function unsafe to call from a passing test.
    """
    with _BUS_LOCK:
        _BUSES.clear()
        _BUS_REFCOUNT.clear()
    with _DECLARED_LOCK:
        _DECLARED.clear()
        _MANIFESTED.clear()


def _selftest() -> int:
    import io

    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    reset_for_tests()

    # ---- acquire_bus ------------------------------------------------------ #
    built: List[dict] = []

    class _FakeTransport:
        def __init__(self, spec):
            self.spec = spec
            self.sent = []
            self.started = False
            self._subs = []

        def start(self): self.started = True
        def stop(self): self.started = False
        def send(self, msg): self.sent.append(msg)
        def subscribe(self, ns, cb): self._subs.append(ns)
        def subscribe_all(self, cb): pass
        def subscribe_pattern(self, p): self._subs.append(p)

        @property
        def is_connected(self): return True

    def _builder(spec, app_id=""):
        built.append(spec)
        return _FakeTransport(spec)

    log = io.StringIO()
    b1 = acquire_bus("mqtt://h:1883", label="src-a", transport_builder=_builder,
                     log=log)
    b2 = acquire_bus("mqtt://h:1883", label="src-b", transport_builder=_builder,
                     log=log)
    check(b1 is b2, "(1) self-create memoizes: two sources, ONE bus")
    check(len(built) == 1, f"(2) exactly ONE transport built, got {len(built)}")
    check(built[0] == {"type": "mqtt", "broker": "h", "port": 1883},
          f"(3) spec goes through the registry shape: {built[0]}")
    check(b1.bus_id == "src-a", f"(4) first caller names the bus: {b1.bus_id}")
    b3 = acquire_bus("mqtt://other:1883", label="src-c",
                     transport_builder=_builder, log=log)
    check(b3 is not b1, "(5) a different broker is a different bus")

    # an INJECTED bus wins and builds nothing
    reset_for_tests()
    built.clear()
    injected = ComposeIOBus(bus_id="injected")
    got = acquire_bus("mqtt://h:1883", label="src-d", bus=injected,
                      transport_builder=_builder, log=log)
    check(got is injected, "(6) an injected bus is returned as-is")
    check(built == [], "(7) an injected bus constructs NO transport")

    # an unavailable transport degrades LOUDLY, never raises
    reset_for_tests()
    loud = io.StringIO()
    degraded = acquire_bus("mqtt://h:1883", label="src-e",
                           transport_builder=lambda s, app_id="": None, log=loud)
    check(degraded is not None, "(8) an unavailable transport still yields a bus")
    check("LOCAL-ONLY" in loud.getvalue(),
          f"(9) degradation must be LOUD: {loud.getvalue()!r}")

    # ---- declare ---------------------------------------------------------- #
    reset_for_tests()
    bus = ComposeIOBus(bus_id="t")
    ns = declare(bus, "self/x", scope=Scope.GLOBAL, source="s1")
    check(ns.scope == Scope.GLOBAL, "(10) explicit GLOBAL pinned")
    check(ns.state_engine.engine_type == "json",
          f"(11) default engine is json, got {ns.state_engine.engine_type}")
    # re-declaring the SAME scope is idempotent, not an error
    declare(bus, "self/x", scope=Scope.GLOBAL, source="s1")
    check(len(declared_namespaces("s1")) == 1, "(12) re-declare does not duplicate")
    # AUTO is forbidden
    try:
        declare(bus, "self/y", scope=Scope.AUTO, source="s1")
        fails.append("(13) Scope.AUTO was accepted")
    except ValueError as exc:
        check("FORBIDDEN" in str(exc), f"(13b) unclear AUTO refusal: {exc}")
    # a CONFLICTING explicit scope raises and names BOTH values
    bus.register_namespace("self/z", "json", scope=Scope.LOCAL)
    try:
        declare(bus, "self/z", scope=Scope.GLOBAL, source="s1")
        fails.append("(14) a conflicting explicit scope was silently accepted")
    except ScopeConflictError as exc:
        check("'local'" in str(exc) and "'global'" in str(exc),
              f"(14b) the conflict must name BOTH scopes: {exc}")
    # an AUTO incumbent is UPGRADED, not a conflict (that IS ensure's contract)
    bus.register_namespace("self/w", "json", scope=Scope.AUTO)
    nsw = declare(bus, "self/w", scope=Scope.GLOBAL, source="s1")
    check(nsw.scope == Scope.GLOBAL, "(15) an AUTO incumbent upgrades cleanly")

    # ---- manifest --------------------------------------------------------- #
    mbuf = io.StringIO()
    line = manifest("s1", log=mbuf)
    check(line.startswith("[s1] declared:"), f"(16) manifest prefix: {line!r}")
    check("self/x scope=global" in line, f"(17) manifest names ns+scope: {line!r}")
    check(line.count("\n") == 1, "(18) the manifest is exactly ONE line")
    empty = manifest("nobody", log=io.StringIO())
    check("NOTHING" in empty, "(19) a source that declared nothing says so")

    # only_if_grown — the DYNAMIC-namespace mode. Untested, this shipped as a
    # TypeError on every publish of the two transcript sources; the capture-diff
    # caught it as a fully dark namespace set, which is exactly the failure the
    # diff exists to catch, but a selftest is cheaper than a capture.
    check(manifest("s1", log=io.StringIO(), only_if_grown=True) == "",
          "(23) only_if_grown is silent when the set has not grown")
    declare(bus, "self/late", scope=Scope.GLOBAL, source="s1")
    grown = manifest("s1", log=io.StringIO(), only_if_grown=True)
    check("self/late" in grown and "+1 new" in grown,
          f"(24) a newly-appeared namespace gets its own line: {grown!r}")
    check(manifest("s1", log=io.StringIO(), only_if_grown=True) == "",
          "(25) ...and then goes quiet again")

    # ---- park ------------------------------------------------------------- #
    stop = threading.Event()
    done = threading.Event()

    def _runner():
        park(stop, poll_s=0.01)
        done.set()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    check(not done.wait(0.25), "(20) park() BLOCKS while stop is unset")
    stop.set()
    check(done.wait(2.0), "(21) park() returns promptly once stop is set")

    ticks = {"n": 0}
    stop2 = threading.Event()

    def _runner2():
        park(stop2, poll_s=0.01, on_tick=lambda: ticks.__setitem__("n", ticks["n"] + 1))

    t2 = threading.Thread(target=_runner2, daemon=True)
    t2.start()
    threading.Event().wait(0.1)
    stop2.set()
    t2.join(2.0)
    check(ticks["n"] > 0, "(22) on_tick fires while parked")

    reset_for_tests()
    if fails:
        for m in fails:
            sys.stderr.write(f"FAIL {m}\n")
        return 1
    print(f"PASS: source_bus selftest (all {25} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
