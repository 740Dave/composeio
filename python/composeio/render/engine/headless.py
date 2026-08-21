"""
ComposeUX Headless Widget Runtime ([SPEC:CXC-HEADLESS])

A headless widget is the engine's per-frame evaluation with the draw
step removed and the clock source swapped ([CXC-HW-RUNTIME] "one tick,
three clocks"): state (namespaces) + bindings + behaviors, authored in
the SAME .compose.yaml shape as a visual widget, with no render.  The
runtime here is the deploy surface ([CXC-HW-DEPLOY]) + the scheduler
([CXC-HW-SCHEDULE]):

    loader   roster + per-widget configs — bus_storage in production
             (bus://assets/headless/...), a local directory for goldens;
             ONE contract, two implementations.
    widget   a RenderApp(headless=True) sharing the runtime's bus; its
             tick(t) is the single execution primitive ([CXC-HW-C9]).
    clocks   continuous  -> tick every 1/10 s of the sim clock (daemon)
             periodic    -> tick once per declared interval_s
             bus-trigger -> subscribe_pattern; a matching publish queues
                            ONE tick at arrival time ([SPEC:CXC-PATSUB])

Execution model: single-process cooperative scheduler — one runtime, one
loop, every roster widget ticked from it.  Determinism ([CXC-HW-C10]):
``advance_to(t)`` drives the logical clock and executes due ticks in
chronological order; nothing in the tick path reads wall time, so a
golden test replays the exact published sequence.  ``run()`` is the
production daemon: the same advance_to fed by the wall clock.

The bus is a seam, not a dependency ([CXC-HW-C6]/[C12]): widgets only
pub/sub namespaces; hand the runtime an in-process ComposeIOBus for
goldens or one carrying an MQTTTransport (:1883) for production — zero
widget changes either way.

Schedule declaration (INSIDE the widget config, never the roster):

    headless:
      schedule:
        mode: continuous            # default
        # mode: periodic
        # interval_s: 3600
        # mode: bus_triggered
        # trigger: pump/damaged     # exact, trailing-glob, or a list

Boundary ([CXC-BIND-BOUNDARY], [CXC-HW-C8]): logic is declarative
bindings + behaviors + the SANDBOXED expression DSL ([SPEC:CXC-DSL],
engine/dsl.py — Pass 2 closed the six use-cases harvested here as
[CXC-HW-DSL]).  Arbitrary code remains refused, never executed: the
only sanctioned computation surface is the closed `expr:` grammar at
the three [CXC-DSL-SITES] (outputs / binding value / trigger condition).
"""

from __future__ import annotations

import logging
import queue
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from composeio.bus import ComposeIOBus

from .parser import Composition, parse_widget_tree
from .runtime import RenderApp

logger = logging.getLogger("composeio.render.headless")

CONTINUOUS_HZ = 10.0        # [CXC-HW-RUNTIME]: fixed 10 Hz sim clock

MODES = ("continuous", "periodic", "bus_triggered")

# ---------------------------------------------------------------------------
# The runtime-observability namespace family ([CXC-FLAUTH-CONFIRM] Pass 3
# reserved it; [SPEC:CXC-HMON] Pass 4 produces it):
#
#     _bus/headless/<runtime_id>/health      Pass 3 — the runtime heartbeat
#     _bus/headless/<widget_id>/health       Pass 4 — per-widget health
#                                            (rate/ticks/last error/uptime,
#                                            [CXC-HMON-C5])
#     _bus/headless/<widget_id>/trace        Pass 4 — the tick-trace ring
#                                            ([CXC-HMON-C1], throttled)
#     _bus/headless/<widget_id>/state        Pass 4 — internal state ($vars,
#                                            behavior/schedule state,
#                                            [CXC-HMON-C4])
#     _bus/headless/<widget_id>/trace/ctl    Pass 4 — the ENABLE request
#                                            ([CXC-HMON-C6]: opt-in is a bus
#                                            publish, not a code change)
#
# Widget ids and runtime ids share the family; a widget named exactly like
# its runtime would collide on /health (author error, documented).
# ---------------------------------------------------------------------------

HEADLESS_BUS_PREFIX = "_bus/headless"

# [CXC-HMON-THROTTLE] defaults: a ring of the last N ticks, published at
# most every sample_s (plus a rate-limited immediate publish on a tick
# that actually changed an output, and on request) — NEVER per-tick.
TRACE_RING_DEPTH = 32
TRACE_SAMPLE_S = 1.0
TRACE_CHANGE_MIN_S = 0.25


def health_namespace(runtime_id: str) -> str:
    """The [CXC-FLAUTH-CONFIRM] heartbeat namespace for a runtime — and,
    since [SPEC:CXC-HMON], the per-WIDGET health namespace too."""
    return f"{HEADLESS_BUS_PREFIX}/{runtime_id}/health"


def trace_namespace(widget_id: str) -> str:
    """[CXC-HMON-TRACE]: the per-widget tick-trace ring."""
    return f"{HEADLESS_BUS_PREFIX}/{widget_id}/trace"


def state_namespace(widget_id: str) -> str:
    """[CXC-HMON-STATE]: the per-widget internal-state publish."""
    return f"{HEADLESS_BUS_PREFIX}/{widget_id}/state"


def trace_ctl_namespace(widget_id: str) -> str:
    """[CXC-HMON-THROTTLE]: the trace-enable request namespace.  Publish
    {"enabled": true|false, "depth": N?, "sample_s": s?, "snapshot": x?}
    here (any bus peer — the Monitor, an Intelligence) to control the
    widget's tracing; the runtime applies it on its scheduler loop."""
    return f"{HEADLESS_BUS_PREFIX}/{widget_id}/trace/ctl"


# ---------------------------------------------------------------------------
# Deploy surface ([CXC-HW-DEPLOY], compliance C11) — one loader contract
# ---------------------------------------------------------------------------
#
# A roster is PURE membership ("runtime hub-headless-01 runs these
# widgets"); the schedule travels inside each widget config.
#
#     <runtime_id>.roster.yaml:      widgets: [fuel_tank, fuel_pump, ...]
#     widgets/<widget_id>.compose.yaml
#

def _parse_roster(data: Any, where: str) -> List[str]:
    if not isinstance(data, dict) or not isinstance(data.get("widgets"), list):
        raise ValueError(f"roster {where} must map 'widgets:' to a list "
                         f"of widget ids ([CXC-HW-DEPLOY] pure membership)")
    return [str(w) for w in data["widgets"]]


def roster_dict(runtime_id: str, widget_ids: List[str]) -> dict:
    """The canonical roster mapping ([CXC-HW-DEPLOY] pure membership) —
    the write-side mirror of _parse_roster."""
    return {"runtime": str(runtime_id),
            "widgets": [str(w) for w in widget_ids]}


def _dump_yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=None,
                          allow_unicode=True)


class LocalFileLoader:
    """The goldens/dev loader: a local directory backing the exact
    bus_storage contract (deterministic, no network — [CXC-HW-C11]).
    save_* is the [CXC-FLAUTH-DEPLOY] write side of the SAME contract."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def load_roster(self, runtime_id: str) -> List[str]:
        path = self.root / f"{runtime_id}.roster.yaml"
        with open(path, encoding="utf-8") as f:
            return _parse_roster(yaml.safe_load(f), str(path))

    def load_widget(self, widget_id: str) -> dict:
        path = self.root / "widgets" / f"{widget_id}.compose.yaml"
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            raise ValueError(f"not a .compose.yaml mapping: {path}")
        return config

    def save_roster(self, runtime_id: str, widget_ids: List[str]) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{runtime_id}.roster.yaml"
        with open(path, "w", encoding="utf-8") as f:
            f.write(_dump_yaml(roster_dict(runtime_id, widget_ids)))
        return str(path)

    def save_widget(self, widget_id: str, config: dict) -> str:
        (self.root / "widgets").mkdir(parents=True, exist_ok=True)
        path = self.root / "widgets" / f"{widget_id}.compose.yaml"
        with open(path, "w", encoding="utf-8") as f:
            f.write(_dump_yaml(config))
        return str(path)


class BusStorageLoader:
    """The production loader: bus_storage (:8420) on the display-boot
    addressing pattern ([CXC-HW-DEPLOY], mirrors [CXC-R-BOOT]):

        bus://assets/headless/<runtime_id>.roster.yaml
        bus://assets/headless/widgets/<widget_id>.compose.yaml

    Resolution goes through composeio.resolve, so COMPOSEIO_STORAGE_URL
    relocates the store without touching this contract."""

    PREFIX = "bus://assets/headless"

    def load_roster(self, runtime_id: str) -> List[str]:
        from composeio import resolve
        uri = f"{self.PREFIX}/{runtime_id}.roster.yaml"
        return _parse_roster(yaml.safe_load(resolve.resolve_text(uri)), uri)

    def load_widget(self, widget_id: str) -> dict:
        from composeio import resolve
        uri = f"{self.PREFIX}/widgets/{widget_id}.compose.yaml"
        config = yaml.safe_load(resolve.resolve_text(uri))
        if not isinstance(config, dict):
            raise ValueError(f"not a .compose.yaml mapping: {uri}")
        return config

    def save_roster(self, runtime_id: str, widget_ids: List[str]) -> str:
        """[CXC-FLAUTH-DEPLOY] Push: the write side of the identical
        contract (bus_storage PUT via composeio.resolve)."""
        from composeio import resolve
        uri = f"{self.PREFIX}/{runtime_id}.roster.yaml"
        resolve.put_text(uri, _dump_yaml(roster_dict(runtime_id,
                                                     widget_ids)))
        return uri

    def save_widget(self, widget_id: str, config: dict) -> str:
        from composeio import resolve
        uri = f"{self.PREFIX}/widgets/{widget_id}.compose.yaml"
        resolve.put_text(uri, _dump_yaml(config))
        return uri


# ---------------------------------------------------------------------------
# Schedule ([CXC-HW-SCHEDULE]) — parsed from the widget's headless: block
# ---------------------------------------------------------------------------

@dataclass
class Schedule:
    mode: str = "continuous"
    interval_s: float = 1.0 / CONTINUOUS_HZ     # tick period (cont/periodic)
    triggers: List[str] = field(default_factory=list)   # bus_triggered


def parse_schedule(headless_block: Optional[dict], where: str) -> Schedule:
    """Parse the widget's declared schedule.  Malformed input degrades to
    continuous with a warning (a headless widget should still run)."""
    block = headless_block if isinstance(headless_block, dict) else {}
    sched = block.get("schedule", block)
    if not isinstance(sched, dict):
        sched = {}
    mode = str(sched.get("mode", "continuous")).lower()
    if mode not in MODES:
        logger.warning("headless widget %s: unknown schedule mode %r "
                       "(modes: %s) — running continuous", where, mode,
                       ", ".join(MODES))
        mode = "continuous"
    if mode == "continuous":
        return Schedule(mode="continuous", interval_s=1.0 / CONTINUOUS_HZ)
    if mode == "periodic":
        try:
            interval = float(sched.get("interval_s", 60.0))
        except (TypeError, ValueError):
            interval = 60.0
        if interval <= 0:
            logger.warning("headless widget %s: interval_s must be > 0 — "
                           "using 60s", where)
            interval = 60.0
        return Schedule(mode="periodic", interval_s=interval)
    trig = sched.get("trigger")
    triggers = [str(t) for t in trig] if isinstance(trig, list) \
        else ([str(trig)] if trig else [])
    if not triggers:
        logger.warning("headless widget %s: bus_triggered needs trigger: "
                       "— widget will never tick", where)
    return Schedule(mode="bus_triggered", triggers=triggers)


# ---------------------------------------------------------------------------
# Static introspection ([SPEC:CXC-FLAUTH] [CXC-FLAUTH-INTROSPECT])
#
# The headless analog of bindings.bindable_attrs_for(): a machine-readable
# table of a headless widget's ports + schedule, derived ONLY from its
# config (design-time structure, no live runtime).  Composer's Flow graph
# and inspector render logic nodes FROM this table — no hardcoding
# ([CXC-BE-INTROSPECT] pattern).
# ---------------------------------------------------------------------------

def expr_namespaces(source: Any) -> Tuple[str, ...]:
    """The namespaces an ``expr:`` string reads (``ns('...')``), or ()
    when the source is absent/unparseable.  Silent on parse errors —
    introspection must not warn on every keystroke of a half-typed expr;
    the sanctioned-site parse (dsl.try_parse) owns the refusal warning."""
    if not isinstance(source, str) or not source.strip():
        return ()
    from . import dsl
    try:
        return tuple(dsl.parse(source).namespaces)
    except dsl.DslParseError:
        return ()


def is_logic_spec(spec: Any) -> bool:
    """Is this widget spec a headless LOGIC NODE ([CXC-FLAUTH-NODE])?
    The marker is the widget-level ``headless:`` block (the schedule
    travels in the widget, [CXC-HW-DEPLOY]) — a visual widget with a
    mere ``outputs:`` publish wire stays a widget node."""
    return isinstance(spec, dict) and isinstance(spec.get("headless"), dict)


def introspect_ports(spec: dict, where: str = "widget") -> dict:
    """The [CXC-FLAUTH-INTROSPECT] table for one widget spec:

        {"inputs":   [{"namespace": str, "via": subscribe|binding|expr|
                                                 behavior|trigger}, ...],
         "outputs":  [{"namespace": str, "attr": str, "expr": str|None}],
         "schedule": {"mode": str, "interval_s": float,
                      "triggers": [str, ...]}}

    inputs  = everything the widget READS: its subscribe ``namespace:``,
              attr-binding namespaces (object + bare-string forms),
              ``ns('...')`` reads inside its sanctioned exprs, behavior
              trigger namespaces, and bus-trigger wake sources — deduped,
              declaration order, first ``via`` wins.
    outputs = its declared ``outputs:`` entries (namespace + attr/expr).
    Read-only: *spec* is never mutated."""
    from .bindings import (BINDABLE_ATTRS, NAMESPACE_STRING_ATTRS)

    inputs: List[dict] = []
    seen: set = set()

    def _add(ns: Any, via: str) -> None:
        ns = str(ns or "")
        if ns and ns != "always" and ns not in seen:
            seen.add(ns)
            inputs.append({"namespace": ns, "via": via})

    if not isinstance(spec, dict):
        return {"inputs": [], "outputs": [],
                "schedule": vars(parse_schedule(None, where))}

    _add(spec.get("namespace"), "subscribe")
    for attr, v in spec.items():
        if attr not in BINDABLE_ATTRS:
            continue
        if isinstance(v, dict):
            if v.get("namespace"):
                _add(v["namespace"], "binding")
            for ns in expr_namespaces(v.get("expr", v.get("expression"))):
                _add(ns, "expr")
        elif isinstance(v, str) and v and attr in NAMESPACE_STRING_ATTRS:
            _add(v, "binding")

    outputs: List[dict] = []
    for entry in spec.get("outputs") or []:
        if not isinstance(entry, dict) or not entry.get("namespace"):
            continue
        expr_src = entry.get("expr", entry.get("expression"))
        outputs.append({"namespace": str(entry["namespace"]),
                        "attr": str(entry.get("attr", "value")),
                        "expr": expr_src if isinstance(expr_src, str)
                        else None})
        for ns in expr_namespaces(expr_src):
            _add(ns, "expr")

    for beh in spec.get("behaviors") or []:
        if isinstance(beh, dict) and isinstance(beh.get("when"), dict):
            _add(beh["when"].get("namespace"), "behavior")

    sched = parse_schedule(spec.get("headless"), where)
    for trig in sched.triggers:
        _add(trig, "trigger")

    return {"inputs": inputs, "outputs": outputs,
            "schedule": {"mode": sched.mode,
                         "interval_s": sched.interval_s,
                         "triggers": list(sched.triggers)}}


# ---------------------------------------------------------------------------
# The hosted widget
# ---------------------------------------------------------------------------

@dataclass
class TraceState:
    """[SPEC:CXC-HMON] per-widget Diagnosis state: OFF by default
    ([CXC-HMON-C6]), a ring buffer of recent tick records, and the
    throttle bookkeeping.  All clocks here are the runtime's LOGICAL
    clock, so telemetry stays deterministic under advance_to()."""
    enabled: bool = False
    depth: int = TRACE_RING_DEPTH
    sample_s: float = TRACE_SAMPLE_S
    change_min_s: float = TRACE_CHANGE_MIN_S
    ring: "deque" = field(default_factory=lambda: deque(
        maxlen=TRACE_RING_DEPTH))
    pending: int = 0                    # ticks recorded since last publish
    last_pub: float = float("-inf")
    snapshot_req: bool = False          # on-request immediate publish
    publishes: int = 0                  # telemetry publishes (throttle QA)
    ticks_total: int = 0
    tick_times: "deque" = field(default_factory=lambda: deque(maxlen=600))
    last_inputs: Optional[dict] = None
    input_changes: Dict[str, float] = field(default_factory=dict)

    def set_depth(self, depth: int) -> None:
        depth = max(1, int(depth))
        if depth != self.depth:
            self.depth = depth
            self.ring = deque(self.ring, maxlen=depth)


@dataclass
class HeadlessWidget:
    """One roster member: its app (the engine minus the backend), its
    declared schedule, and its scheduling state."""
    widget_id: str
    app: RenderApp
    schedule: Schedule
    epoch: float = 0.0          # clock at add-time (scheduled tick origin)
    tick_index: int = 0         # next scheduled tick number (drift-free)
    last_tick: float = -1.0     # last executed tick time (introspection)
    # [SPEC:CXC-HMON] Diagnosis: trace state + the trigger that woke the
    # most recent bus-triggered tick (namespace, arrival time).
    trace: TraceState = field(default_factory=TraceState)
    last_trigger: Optional[Tuple[str, float]] = None

    @property
    def next_due(self) -> Optional[float]:
        """Next scheduled tick time, or None (bus-triggered widgets have
        no scheduled clock — they only tick on trigger arrivals)."""
        if self.schedule.mode == "continuous":
            return self.epoch + self.tick_index * self.schedule.interval_s
        if self.schedule.mode == "periodic":
            # first fire at epoch + interval ([CXC-HW-C4]: fires ON its
            # interval, not at deploy time)
            return self.epoch + (self.tick_index + 1) * self.schedule.interval_s
        return None


# ---------------------------------------------------------------------------
# The runtime — single-process cooperative scheduler ([CXC-HW-RUNTIME])
# ---------------------------------------------------------------------------

class HeadlessRuntime:
    """Hosts many headless widgets on one bus, one loop, one clock.

    Deterministic drive (goldens):

        rt = HeadlessRuntime()
        rt.load(LocalFileLoader(root), "jump-drive-01")
        rt.start()
        events = rt.advance_to(2.0)      # [(t, namespace, value), ...]

    Production daemon:

        rt = HeadlessRuntime(bus=mqtt_bus)   # bus seam: [CXC-HW-C12]
        rt.load(BusStorageLoader(), runtime_id)
        rt.run()
    """

    def __init__(self, *, bus: Optional[ComposeIOBus] = None,
                 runtime_id: str = "headless-runtime",
                 heartbeat_s: Optional[float] = None) -> None:
        self.runtime_id = runtime_id
        self.bus = bus or ComposeIOBus(bus_id=runtime_id)
        self._owns_bus = bus is None
        self.widgets: List[HeadlessWidget] = []
        self.clock: float = 0.0         # logical seconds since start
        self._started = False
        self._running = False
        # [CXC-FLAUTH-CONFIRM] runtime heartbeat: when heartbeat_s is set,
        # the runtime publishes a small periodic health state to
        # _bus/headless/<runtime_id>/health on the LOGICAL clock (opt-in;
        # goldens with the default None are untouched).  This is the ONE
        # Pass-3 runtime addition — the Diagnosis producers stay Pass 4.
        self.heartbeat_s = heartbeat_s if heartbeat_s and heartbeat_s > 0 \
            else None
        self._next_beat: float = 0.0
        # trigger arrivals: (arrival_time, widget, trigger_namespace).
        # Filled from bus callbacks (any thread), drained by the
        # scheduler loop — the cooperative single-process handoff.
        self._pending: "queue.SimpleQueue[Tuple[float, HeadlessWidget, str]]" \
            = queue.SimpleQueue()
        self._due_triggers: List[Tuple[float, HeadlessWidget, str]] = []
        # [SPEC:CXC-HMON] trace-control requests off the bus (any thread),
        # applied on the scheduler loop ([CXC-HMON-C6]: enabling trace is
        # a bus publish, never a code change).
        self._ctl_queue: "queue.SimpleQueue[Tuple[HeadlessWidget, Any]]" = \
            queue.SimpleQueue()

    # -- deploy ([CXC-HW-DEPLOY]) -----------------------------------------

    def load(self, loader: Any, runtime_id: Optional[str] = None) -> List[str]:
        """Fetch the roster + each named widget through *loader* (local
        dir or bus_storage — identical contract, [CXC-HW-C11])."""
        rid = runtime_id or self.runtime_id
        ids = loader.load_roster(rid)
        for wid in ids:
            self.add_widget(wid, loader.load_widget(wid))
        logger.info("runtime %s: roster %s -> %d widgets", self.runtime_id,
                    rid, len(ids))
        return ids

    def add_widget(self, widget_id: str, config: dict) -> HeadlessWidget:
        """Parse + mount one headless widget on the shared bus."""
        comp: Composition = parse_widget_tree(config)
        app = RenderApp(comp, headless=True, bus=self.bus)
        app.start()
        hw = HeadlessWidget(widget_id=widget_id, app=app,
                            schedule=parse_schedule(comp.headless, widget_id),
                            epoch=self.clock)
        self.widgets.append(hw)

        if hw.schedule.mode == "bus_triggered":
            for trig in hw.schedule.triggers:
                def _cb(ns: str, new_val: Any, old_val: Any,
                        _hw: HeadlessWidget = hw) -> None:
                    # arrival time = the runtime clock at observation
                    # ([CXC-HW-RUNTIME] event clock); the tick itself runs
                    # on the scheduler loop, never on the callback thread.
                    self._pending.put((self.clock, _hw, ns))
                # exact names fall through to a plain subscription; globs
                # ride the pattern primitive ([SPEC:CXC-PATSUB]).  Ensure
                # exact trigger namespaces with the permissive json
                # engine FIRST — bus.subscribe's ensure defaults to
                # binary, which would then REJECT a producer's non-binary
                # publish (the runtime.py subscription-wiring convention).
                from composeio.bus import is_pattern
                if not is_pattern(trig):
                    self.bus.ensure_namespace(trig, engine_config="json")
                self.bus.subscribe_pattern(trig, _cb)

        # [SPEC:CXC-HMON] the trace-enable request channel ([CXC-HMON-C6]):
        # always subscribed (a subscription publishes nothing — default-off
        # stays byte-silent), applied on the scheduler loop.
        ctl_ns = trace_ctl_namespace(widget_id)
        self.bus.ensure_namespace(ctl_ns, engine_config="json")

        def _ctl(ns: str, new_val: Any, old_val: Any,
                 _hw: HeadlessWidget = hw) -> None:
            self._ctl_queue.put((_hw, new_val))

        self.bus.subscribe(ctl_ns, _ctl)
        return hw

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        if self._owns_bus:
            self.bus.start()
        self._started = True

    def stop(self) -> None:
        self._running = False
        for hw in self.widgets:
            try:
                hw.app.stop()
            except Exception:
                logger.exception("widget %s stop failed", hw.widget_id)
        if self._owns_bus and self._started:
            self.bus.stop()
        self._started = False

    # -- the deterministic core ([CXC-HW-C10]) --------------------------------

    def _drain_pending(self) -> None:
        while True:
            try:
                self._due_triggers.append(self._pending.get_nowait())
            except queue.Empty:
                return

    def _drain_ctl(self) -> None:
        """Apply queued trace-control requests ([CXC-HMON-C6]) on the
        scheduler loop — the same cooperative handoff trigger arrivals
        use, so control stays deterministic under advance_to()."""
        while True:
            try:
                hw, val = self._ctl_queue.get_nowait()
            except queue.Empty:
                return
            self._apply_ctl(hw, val)

    def _apply_ctl(self, hw: HeadlessWidget, val: Any) -> None:
        if isinstance(val, dict):
            self.set_trace(hw.widget_id,
                           enabled=bool(val.get("enabled",
                                                hw.trace.enabled)),
                           depth=val.get("depth"),
                           sample_s=val.get("sample_s"),
                           snapshot="snapshot" in val)
        else:
            from .bindings import truthy
            self.set_trace(hw.widget_id, enabled=truthy(val))

    def _execute(self, hw: HeadlessWidget, t: float,
                 trigger_ns: Optional[str] = None) -> List[tuple]:
        hw.last_tick = t
        if trigger_ns:
            hw.last_trigger = (trigger_ns, t)
        published = hw.app.tick(t)
        self._after_tick(hw, t)
        return [(t, ns, val) for ns, val in published]

    def advance_to(self, t: float) -> List[tuple]:
        """Advance the logical clock to *t*, executing every due tick in
        chronological order (ties: trigger arrivals first, then roster
        order).  Returns the published events as (time, namespace, value)
        — the golden-test observable.

        Termination: a tick may publish namespaces that trigger further
        ticks at the same time; because outputs only publish on CHANGE,
        such cascades settle once values stabilize."""
        if not self._started:
            self.start()
        events: List[tuple] = []
        while True:
            self._drain_ctl()
            self._drain_pending()
            if self._due_triggers:
                at, hw, trig_ns = self._due_triggers.pop(0)
                # arrival stamps are <= clock <= t by construction; clamp
                # defensively so a tick never runs at a future time
                at = min(max(at, 0.0), t)
                self.clock = max(self.clock, at)
                events.extend(self._execute(hw, at, trigger_ns=trig_ns))
                continue
            best: Optional[HeadlessWidget] = None
            best_t = 0.0
            for hw in self.widgets:
                nd = hw.next_due
                if nd is None or nd > t + 1e-9:
                    continue
                if best is None or nd < best_t - 1e-12:
                    best, best_t = hw, nd
            if best is None:
                break
            self.clock = max(self.clock, best_t)
            best.tick_index += 1
            events.extend(self._execute(best, best_t))
        self.clock = max(self.clock, t)
        self._maybe_heartbeat()
        return events

    def pump(self) -> List[tuple]:
        """Process pending trigger arrivals without advancing the clock
        (deterministic tests: publish -> pump)."""
        return self.advance_to(self.clock)

    # -- [CXC-FLAUTH-CONFIRM] heartbeat ------------------------------------

    def _maybe_heartbeat(self) -> None:
        """Publish the runtime health beat when due (logical clock)."""
        if self.heartbeat_s is None or self.clock < self._next_beat:
            return
        self._next_beat = self.clock + self.heartbeat_s
        ns = health_namespace(self.runtime_id)
        try:
            self.bus.ensure_namespace(ns, engine_config="json")
            self.bus.publish_state(ns, {
                "runtime_id": self.runtime_id,
                "widgets": len(self.widgets),
                "widget_ids": [hw.widget_id for hw in self.widgets],
                "clock": round(self.clock, 3),
                "last_tick": round(max((hw.last_tick for hw in self.widgets),
                                       default=-1.0), 3),
            }, source=self.runtime_id)
        except Exception:
            logger.exception("heartbeat publish failed")

    # -- [SPEC:CXC-HMON] the Diagnosis producer ------------------------------

    def widget(self, widget_id: str) -> Optional[HeadlessWidget]:
        for hw in self.widgets:
            if hw.widget_id == widget_id:
                return hw
        return None

    def set_trace(self, widget_id: str, enabled: bool = True, *,
                  depth: Any = None, sample_s: Any = None,
                  snapshot: bool = False) -> bool:
        """Enable/disable tracing for ONE widget ([CXC-HMON-C6]: opt-in,
        per widget).  Read-only observation: this never pauses or mutates
        execution — the widget's published outputs are identical traced
        or not.  Also reachable from any bus peer via a publish to
        trace_ctl_namespace(widget_id).  An enable (or snapshot=True)
        publishes the current telemetry immediately so an attaching
        Monitor sees state without waiting for the sample clock."""
        hw = self.widget(widget_id)
        if hw is None:
            logger.warning("set_trace: no widget %r on runtime %s",
                           widget_id, self.runtime_id)
            return False
        tr = hw.trace
        if depth is not None:
            try:
                tr.set_depth(int(depth))
            except (TypeError, ValueError):
                pass
        if sample_s is not None:
            try:
                s = float(sample_s)
                if s > 0:
                    tr.sample_s = s
                    tr.change_min_s = min(tr.change_min_s, s)
            except (TypeError, ValueError):
                pass
        was = tr.enabled
        tr.enabled = bool(enabled)
        hw.app.trace_enabled = tr.enabled
        if tr.enabled and (snapshot or not was):
            self._publish_telemetry(hw, force=True)
        logger.info("trace %s on %s/%s", "ON" if tr.enabled else "off",
                    self.runtime_id, widget_id)
        return True

    def _after_tick(self, hw: HeadlessWidget, t: float) -> None:
        """Post-tick bookkeeping: always counts ticks (cheap ints); does
        trace capture + the throttled publish ONLY when tracing is on."""
        tr = hw.trace
        tr.ticks_total += 1
        tr.tick_times.append(t)
        if not tr.enabled:
            return
        rec = hw.app.last_tick_record
        if rec is None:
            return
        if hw.last_trigger is not None and hw.last_trigger[1] == t:
            rec["trigger"] = {"namespace": hw.last_trigger[0], "t": t}
        # last-input-Δ tracking ([CXC-HMON-STATE]): diff this tick's
        # inputs against the previous tick's.
        inputs = rec.get("inputs", {})
        if tr.last_inputs is not None:
            for ns_name, v in inputs.items():
                if ns_name not in tr.last_inputs \
                        or tr.last_inputs[ns_name] != v:
                    tr.input_changes[ns_name] = t
        tr.last_inputs = dict(inputs)
        tr.ring.append(rec)
        tr.pending += 1
        # a publish OR an eval error is an event worth telling sooner
        changed = any(o.get("published") or o.get("error")
                      for o in rec.get("outputs", []))
        self._maybe_publish_telemetry(hw, changed=changed)

    def _maybe_publish_telemetry(self, hw: HeadlessWidget,
                                 changed: bool = False) -> None:
        """[CXC-HMON-THROTTLE]: publish the ring at most every sample_s;
        a change-tick may publish sooner but never faster than
        change_min_s; snapshot requests publish immediately.  A 10 Hz
        widget therefore NEVER produces a 10 Hz trace stream."""
        tr = hw.trace
        if not tr.enabled or tr.pending == 0:
            return
        gap = self.clock - tr.last_pub
        due = gap >= tr.sample_s
        on_change = changed and gap >= tr.change_min_s
        if not (due or on_change or tr.snapshot_req):
            return
        self._publish_telemetry(hw)

    def _publish_telemetry(self, hw: HeadlessWidget,
                           force: bool = False) -> None:
        tr = hw.trace
        tr.snapshot_req = False
        tr.last_pub = self.clock
        tr.pending = 0
        tr.publishes += 1
        now = round(self.clock, 6)
        try:
            self._pub(trace_namespace(hw.widget_id), {
                "widget_id": hw.widget_id,
                "runtime_id": self.runtime_id,
                "clock": now,
                "depth": tr.depth,
                "ticks": list(tr.ring),        # oldest -> newest
            })
            self._pub(state_namespace(hw.widget_id),
                      self._state_payload(hw))
            self._pub(health_namespace(hw.widget_id),
                      self._health_payload(hw))
        except Exception:
            logger.exception("telemetry publish failed for %s",
                             hw.widget_id)

    def _pub(self, ns: str, payload: dict) -> None:
        self.bus.ensure_namespace(ns, engine_config="json")
        self.bus.publish_state(ns, payload, source=self.runtime_id)

    def _state_payload(self, hw: HeadlessWidget) -> dict:
        """[CXC-HMON-STATE]: $vars, behavior state, schedule state
        (next fire / last trigger), last-input-Δ."""
        now = self.clock
        tr = hw.trace
        sched: dict = {"mode": hw.schedule.mode,
                       "interval_s": hw.schedule.interval_s,
                       "triggers": list(hw.schedule.triggers)}
        nd = hw.next_due
        if nd is not None:
            sched["next_due"] = round(nd, 6)
            sched["next_in_s"] = round(max(0.0, nd - now), 6)
        if hw.last_trigger is not None:
            sched["last_trigger"] = {
                "namespace": hw.last_trigger[0],
                "t": round(hw.last_trigger[1], 6),
                "age_s": round(max(0.0, now - hw.last_trigger[1]), 6)}
        behaviors = []
        for inst in hw.app.behaviors.instances:
            b = {"behavior": inst.decl.behavior,
                 "target": inst.decl.target,
                 "active": inst.active}
            if inst.active and inst.start is not None:
                b["elapsed_s"] = round(max(0.0, now - inst.start), 6)
            behaviors.append(b)
        last_change = None
        if tr.input_changes:
            ns_name = max(tr.input_changes, key=lambda k:
                          tr.input_changes[k])
            last_change = {"namespace": ns_name,
                           "t": round(tr.input_changes[ns_name], 6),
                           "age_s": round(max(
                               0.0, now - tr.input_changes[ns_name]), 6)}
        return {"widget_id": hw.widget_id, "clock": round(now, 6),
                "vars": dict(hw.app.comp.variables),
                "behaviors": behaviors, "schedule": sched,
                "last_input_change": last_change}

    def _health_payload(self, hw: HeadlessWidget) -> dict:
        """[CXC-HMON-C5]: rate, tick count, last error, uptime — plus a
        30-bucket 1 s rate history for the Monitor's sparkline."""
        now = self.clock
        tr = hw.trace
        uptime = max(0.0, now - hw.epoch)
        win = min(5.0, uptime) or 1e-9
        n_recent = sum(1 for tt in tr.tick_times if tt > now - win)
        history: List[int] = []
        if tr.tick_times:
            base = now - 30.0
            buckets = [0] * 30
            for tt in tr.tick_times:
                idx = int((tt - base) // 1.0)
                if 0 <= idx < 30:
                    buckets[idx] += 1
            history = buckets
        return {"widget_id": hw.widget_id,
                "rate_hz": round(n_recent / win, 3),
                "ticks": tr.ticks_total,
                "uptime_s": round(uptime, 3),
                "last_tick": round(hw.last_tick, 6),
                "last_error": hw.app.last_error,
                "rate_history": history}

    # -- the wall-clock daemon -------------------------------------------------

    def run(self, duration: Optional[float] = None) -> None:
        """Production loop: feed advance_to from the wall clock until
        stop() (or *duration* logical seconds).  Same tick path as the
        goldens — the clock source is the only difference."""
        self.start()
        self._running = True
        t0 = time.monotonic() - self.clock
        try:
            while self._running:
                now = time.monotonic() - t0
                self.advance_to(now)
                if duration is not None and self.clock >= duration:
                    break
                # sleep to the nearest scheduled tick, capped so trigger
                # arrivals from transport threads stay responsive
                dues = [hw.next_due for hw in self.widgets
                        if hw.next_due is not None]
                wait = (min(dues) - (time.monotonic() - t0)) if dues else 0.05
                time.sleep(min(max(wait, 0.001), 0.05))
        finally:
            self._running = False

    # -- introspection --------------------------------------------------------

    def status(self) -> dict:
        return {
            "runtime_id": self.runtime_id,
            "clock": self.clock,
            "widgets": [{
                "widget_id": hw.widget_id,
                "mode": hw.schedule.mode,
                "interval_s": hw.schedule.interval_s,
                "triggers": hw.schedule.triggers,
                "last_tick": hw.last_tick,
            } for hw in self.widgets],
        }


# ---------------------------------------------------------------------------
# CLI — the deployable runtime process
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="ComposeUX headless widget runtime — run a roster of "
                    "headless .compose.yaml widgets ([SPEC:CXC-HEADLESS])")
    ap.add_argument("runtime_id",
                    help="roster id: bus://assets/headless/"
                         "<runtime_id>.roster.yaml (or <root>/"
                         "<runtime_id>.roster.yaml with --root)")
    ap.add_argument("--root", metavar="DIR",
                    help="load from a local directory instead of "
                         "bus_storage (goldens/dev)")
    ap.add_argument("--mqtt", metavar="BROKER[:PORT]",
                    help="carry the bus over MQTT (production seam, "
                         "default port 1883)")
    ap.add_argument("--duration", type=float, default=None,
                    help="run for N seconds then exit (default: forever)")
    ap.add_argument("--heartbeat", type=float, default=1.0, metavar="SECS",
                    help="publish _bus/headless/<runtime_id>/health every "
                         "SECS ([CXC-FLAUTH-CONFIRM]; 0 disables; "
                         "default 1.0)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")

    bus = None
    if args.mqtt:
        from composeio.transport_mqtt import MQTTTransport
        broker, _, port = args.mqtt.partition(":")
        bus = ComposeIOBus(
            bus_id=f"headless-{args.runtime_id}",
            global_transport=MQTTTransport(broker=broker,
                                           port=int(port or 1883)))

    rt = HeadlessRuntime(bus=bus, runtime_id=args.runtime_id,
                         heartbeat_s=args.heartbeat or None)
    loader = LocalFileLoader(args.root) if args.root else BusStorageLoader()
    rt.load(loader)
    try:
        rt.run(duration=args.duration)
    except KeyboardInterrupt:
        pass
    finally:
        rt.stop()


if __name__ == "__main__":
    main()
