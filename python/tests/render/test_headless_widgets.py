"""
[SPEC:CXC-HEADLESS] Headless Widgets — Pass-1 compliance tests.

Covers the buildable Pass-1 compliance rows:

  C1   headless model: a .compose.yaml widget with no render pub/subs
       namespaces and runs on the headless runtime
  C3   continuous: output updates every engine tick (10 Hz sim clock)
  C4   periodic: fires only on its declared interval
  C5   bus-triggered: executes only when its trigger publishes
  C8   no inline code: code-shaped fields stripped; expression maps
       refused ([CXC-BIND-BOUNDARY])
  C9   one tick, three clocks: every mode drives RenderApp.tick — no
       per-mode execution path
  C10  deterministic tick: tick(t) reads no wall clock; the same drive
       reproduces the exact published sequence
  C11  roster deploy: local-file loader + bus_storage loader back the
       SAME contract
  C12  bus seam: the same widget runs against different bus instances
       (and, via transports, MQTT) with zero widget changes

  C2 (partial): the jump-drive triad produces jumpdrive/readiness from
       fuel + charge declaratively; the ARITHMETIC portion (variable-rate
       drain, pump rate math) is deferred to the DSL pass and harvested
       as use-cases (see the triad configs' PASS-1 GAP notes).

No canvas, no display, no network: everything drives the logical clock
(HeadlessRuntime.advance_to) — the headless analog of render_frame_at.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.headless import (BusStorageLoader,
                                              HeadlessRuntime,
                                              LocalFileLoader,
                                              parse_schedule)
from composeio.render.engine.runtime import RenderApp

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples",
                        "headless")
RUNTIME_ID = "jump-drive-01"


# ---------------------------------------------------------------------------
# Config fixtures (dict-authored so tests are self-contained + fast)
# ---------------------------------------------------------------------------

def _drainer(drain_ms: int = 2000) -> dict:
    """A fast fuel_tank-shaped continuous widget (2s full drain)."""
    return {
        "app": {"id": "t-drainer"},
        "headless": {"schedule": {"mode": "continuous"}},
        "widgets": [{
            "type": "gauge", "id": "tank", "min": 0, "max": 100,
            "value": 100.0,
            "behaviors": [{
                "when": "always",
                "do": {"behavior": "ramp", "target": "value",
                       "from": 100.0, "to": 0.0, "duration_ms": drain_ms},
            }],
            "outputs": [{"namespace": "t/fuel", "attr": "value"}],
        }],
    }


def _pump(triggers=None) -> dict:
    """A fuel_pump-shaped bus-triggered widget."""
    return {
        "app": {"id": "t-pump"},
        "headless": {"schedule": {
            "mode": "bus_triggered",
            "trigger": triggers or ["t/damaged"]}},
        "widgets": [{
            "type": "gauge", "id": "pump", "min": 0, "max": 10,
            "value": {"namespace": "t/damaged",
                      "map": {"on": 2.5, "off": 10.0}, "default": 10.0},
            "outputs": [{"namespace": "t/flow", "attr": "value"}],
        }],
    }


def _periodic(interval_s: float = 5.0) -> dict:
    return {
        "app": {"id": "t-periodic"},
        "headless": {"schedule": {"mode": "periodic",
                                  "interval_s": interval_s}},
        "widgets": [{
            "type": "gauge", "id": "meter", "min": 0, "max": 100,
            "value": 0.0,
            "behaviors": [{
                "when": "always",
                "do": {"behavior": "ramp", "target": "value",
                       "from": 0.0, "to": 100.0, "duration_ms": 60000},
            }],
            "outputs": [{"namespace": "t/summary", "attr": "value"}],
        }],
    }


def _events_for(events, namespace):
    return [(t, v) for t, ns, v in events if ns == namespace]


# ---------------------------------------------------------------------------
# C1 — the headless model
# ---------------------------------------------------------------------------

def test_c1_headless_widget_pubs_and_subs_no_canvas():
    rt = HeadlessRuntime()
    rt.add_widget("drainer", _drainer())
    rt.add_widget("pump", _pump(["t/damaged", "t/fuel"]))
    rt.start()
    try:
        # NO canvas anywhere — the engine minus the backend
        assert all(hw.app.canvas is None for hw in rt.widgets)
        assert all(hw.app.headless for hw in rt.widgets)

        events = rt.advance_to(0.3)
        fuel = _events_for(events, "t/fuel")
        assert fuel, "headless widget published nothing"
        # the pump SUBSCRIBES t/damaged: publish flips its bound rate
        rt.bus.publish_state("t/damaged", "on", source="test")
        events = rt.pump()
        assert _events_for(events, "t/flow")[-1][1] == 2.5
    finally:
        rt.stop()


def test_c1_headless_app_refuses_pixel_path():
    rt = HeadlessRuntime()
    hw = rt.add_widget("drainer", _drainer())
    try:
        with pytest.raises(RuntimeError):
            hw.app.render_headless("/tmp/never.png")
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# C3 — continuous: an output update every engine tick
# ---------------------------------------------------------------------------

def test_c3_continuous_updates_every_tick():
    rt = HeadlessRuntime()
    rt.add_widget("drainer", _drainer())
    rt.start()
    try:
        events = rt.advance_to(1.0)
        fuel = _events_for(events, "t/fuel")
        # 10 Hz sim clock: ticks at 0.0, 0.1, ... 1.0 -> 11 publishes,
        # every tick a new value (the ramp moves each 0.1s)
        assert len(fuel) == 11
        times = [round(t, 1) for t, _ in fuel]
        assert times == [round(k * 0.1, 1) for k in range(11)]
        values = [v for _, v in fuel]
        assert values[0] == 100.0
        assert all(values[i] > values[i + 1] for i in range(len(values) - 1))
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# C4 — periodic: fires only on its declared interval
# ---------------------------------------------------------------------------

def test_c4_periodic_fires_only_on_interval():
    rt = HeadlessRuntime()
    hw = rt.add_widget("summary", _periodic(interval_s=5.0))
    rt.start()
    try:
        assert not rt.advance_to(4.99), "fired before its interval"
        events = rt.advance_to(5.0)
        assert [round(t, 2) for t, _ in _events_for(events, "t/summary")] \
            == [5.0]
        events = rt.advance_to(14.99)
        assert [round(t, 2) for t, _ in _events_for(events, "t/summary")] \
            == [10.0]
        assert hw.last_tick == pytest.approx(10.0)
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# C5 — bus-triggered: executes only when its trigger publishes
# ---------------------------------------------------------------------------

def test_c5_bus_triggered_executes_only_on_trigger():
    rt = HeadlessRuntime()
    hw = rt.add_widget("pump", _pump())
    rt.start()
    try:
        assert rt.advance_to(2.0) == []          # silence -> no execution
        assert hw.last_tick == -1.0

        rt.bus.publish_state("t/damaged", "on", source="test")
        events = rt.pump()
        assert hw.last_tick == pytest.approx(2.0)  # t = arrival time
        assert _events_for(events, "t/flow") == [(2.0, 2.5)]

        # an unrelated namespace does NOT wake it
        rt.bus.publish_state("t/other", "on", source="test")
        assert rt.advance_to(3.0) == []
    finally:
        rt.stop()


def test_c5_pattern_trigger():
    """A trailing-glob trigger ([SPEC:CXC-PATSUB]) wakes the widget for
    any member of the family, including later-appearing members."""
    cfg = _pump(["t/pumpfam/*"])
    cfg["widgets"][0]["value"] = {"namespace": "t/pumpfam/damaged",
                                  "map": {"on": 2.5, "off": 10.0},
                                  "default": 10.0}
    rt = HeadlessRuntime()
    hw = rt.add_widget("pump", _pump(["t/pumpfam/*"]))
    rt.start()
    try:
        rt.bus.publish_state("t/pumpfam/anything", "on", source="test")
        rt.pump()
        assert hw.last_tick == pytest.approx(0.0)
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# C8 — no inline code ([CXC-BIND-BOUNDARY] enforced for headless configs)
# ---------------------------------------------------------------------------

def test_c8_inline_code_stripped_and_expression_map_refused():
    cfg = _drainer()
    w = cfg["widgets"][0]
    w["script"] = "import os; os.system('never')"          # stripped
    w["behaviors"][0]["do"]["expr"] = "remaining - rate*dt"  # stripped
    # an arithmetic/templated map is refused -> binding dropped, literal
    # default retained ([CXC-BIND-C8])
    w["value"] = {"namespace": "t/x",
                  "map": {"on": "{{ rate * 2 }}"}, "default": 55.0}

    rt = HeadlessRuntime()
    hw = rt.add_widget("drainer", cfg)
    rt.start()
    try:
        assert not hw.app.comp.attr_bindings, "expression map not refused"
        rt.bus.publish_state("t/x", "on", source="test")
        events = rt.advance_to(0.0)
        # ramp still runs from its literal base; no code ever executed
        assert _events_for(events, "t/fuel")
        decl = hw.app.comp.behaviors[0]
        assert "expr" not in decl.params and "script" not in decl.params
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# C9 — one tick, three clocks: every mode drives RenderApp.tick
# ---------------------------------------------------------------------------

def test_c9_all_three_modes_drive_the_one_tick(monkeypatch):
    calls = []
    real_tick = RenderApp.tick

    def spy(self, t):
        calls.append((self.comp.app_id, t))
        return real_tick(self, t)

    monkeypatch.setattr(RenderApp, "tick", spy)

    rt = HeadlessRuntime()
    rt.add_widget("drainer", _drainer())            # continuous
    rt.add_widget("summary", _periodic(0.5))        # periodic
    rt.add_widget("pump", _pump())                  # bus-triggered
    rt.start()
    try:
        rt.advance_to(0.55)
        rt.bus.publish_state("t/damaged", "on", source="test")
        rt.pump()
        ids = {app_id for app_id, _ in calls}
        assert ids == {"t-drainer", "t-periodic", "t-pump"}, \
            "some scheduling mode bypassed the single tick primitive"
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# C10 — deterministic tick: exact sequence reproduction
# ---------------------------------------------------------------------------

def _drive_triad_once():
    """The golden drive: fixed logical schedule, no wall clock anywhere."""
    rt = HeadlessRuntime(runtime_id=RUNTIME_ID)
    rt.load(LocalFileLoader(EXAMPLES))
    rt.start()
    try:
        events = list(rt.advance_to(0.35))
        rt.bus.publish_state("ship/charge", 80.0, source="golden")
        events += rt.advance_to(1.0)
        rt.bus.publish_state("pump/damaged", "on", source="golden")
        events += rt.advance_to(1.5)
        rt.bus.publish_state("pump/damaged", "off", source="golden")
        events += rt.advance_to(1.8)
        rt.bus.publish_state("ship/charge", 20.0, source="golden")
        events += rt.advance_to(2.5)
        return events
    finally:
        rt.stop()


def test_c10_exact_sequence_reproduces():
    a = _drive_triad_once()
    b = _drive_triad_once()
    assert a == b, "identical logical drive produced different sequences"
    assert len(a) > 20


def test_c10_direct_tick_is_pure_function_of_t():
    """Unit-level: driving app.tick(0.0), tick(0.1)... directly yields
    the exact expected drain values (t is the ONLY clock)."""
    from composeio.render.engine.parser import parse_widget_tree
    seqs = []
    for _ in range(2):
        comp = parse_widget_tree(copy.deepcopy(_drainer(drain_ms=1000)))
        app = RenderApp(comp, headless=True)
        app.start()
        seq = []
        for k in range(12):
            seq.extend(app.tick(k * 0.1))
        app.stop()
        seqs.append(seq)
    assert seqs[0] == seqs[1]
    values = [v for ns, v in seqs[0] if ns == "t/fuel"]
    assert values[0] == 100.0
    assert values[-1] == 0.0                     # ramp completed at 1.0s
    assert len(values) == 11                     # 10 changes + no dup at rest


# ---------------------------------------------------------------------------
# C11 — roster deploy: one loader contract, two sources
# ---------------------------------------------------------------------------

def test_c11_local_roster_loads_the_triad():
    rt = HeadlessRuntime(runtime_id=RUNTIME_ID)
    ids = rt.load(LocalFileLoader(EXAMPLES))
    rt.start()
    try:
        assert ids == ["fuel_tank", "fuel_pump", "jump_engine"]
        modes = {hw.widget_id: hw.schedule.mode for hw in rt.widgets}
        # the schedule travels INSIDE each widget ([CXC-HW-DEPLOY]);
        # since [SPEC:CXC-DSL] pump AND engine are reactive
        assert modes == {"fuel_tank": "continuous",
                         "fuel_pump": "bus_triggered",
                         "jump_engine": "bus_triggered"}
        assert rt.widgets[1].schedule.triggers == ["pump/damaged",
                                                   "fuel/remaining"]
        assert rt.widgets[2].schedule.triggers == ["fuel/remaining",
                                                   "ship/charge"]
    finally:
        rt.stop()


def test_c11_bus_storage_loader_backs_the_same_contract(monkeypatch):
    """BusStorageLoader speaks bus://assets/headless/... through
    composeio.resolve; serving those URIs from the local mirror must
    produce the identical deployment (no network in this test)."""
    from composeio import resolve

    def fake_resolve_text(uri: str, **kw) -> str:
        prefix = "bus://assets/headless/"
        assert uri.startswith(prefix), f"off-contract URI: {uri}"
        path = os.path.join(EXAMPLES, uri[len(prefix):])
        with open(path, encoding="utf-8") as f:
            return f.read()

    monkeypatch.setattr(resolve, "resolve_text", fake_resolve_text)

    loader = BusStorageLoader()
    assert loader.load_roster(RUNTIME_ID) == \
        LocalFileLoader(EXAMPLES).load_roster(RUNTIME_ID)
    for wid in ("fuel_tank", "fuel_pump", "jump_engine"):
        assert loader.load_widget(wid) == \
            LocalFileLoader(EXAMPLES).load_widget(wid)


# ---------------------------------------------------------------------------
# C12 — the bus is a seam: same widget, different bus, zero changes
# ---------------------------------------------------------------------------

def test_c12_same_widget_any_bus():
    """The identical config dict runs against two independent bus
    instances and produces the identical published sequence — the widget
    holds no bus reference and cannot tell the difference.  (The MQTT
    :1883 leg of this seam is tests/render/smoke_headless_mqtt.py.)"""
    from composeio.bus import ComposeIOBus

    def run_on(bus):
        rt = HeadlessRuntime(bus=bus)
        rt.add_widget("drainer", copy.deepcopy(_drainer()))
        rt.start()
        try:
            events = rt.advance_to(0.5)
            return events
        finally:
            rt.stop()

    a = run_on(ComposeIOBus(bus_id="bus-a"))
    b = run_on(ComposeIOBus(bus_id="bus-b"))
    assert a == b and len(a) == 6


def test_c12_physical_sensor_binds_with_no_widget_change():
    """[CXC-HW-C6]: 'binding a physical sensor to its input namespace
    requires no change to the logic' — any external publisher on
    pump/damaged (here: a test standing in for an ESP32) drives the
    identical pump config."""
    rt = HeadlessRuntime()
    rt.add_widget("pump", _pump())
    rt.start()
    try:
        # the "sensor" publishes; the widget was authored with zero
        # knowledge of who publishes
        rt.bus.publish_state("t/damaged", "on", source="esp32-sensor-01")
        events = rt.pump()
        assert _events_for(events, "t/flow")[-1][1] == 2.5
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# The jump-drive triad — re-authored on the expression DSL
# ([SPEC:CXC-DSL] [CXC-DSL-TRIAD]; CLOSES [CXC-HW] C2 / [CXC-DSL-C11])
# ---------------------------------------------------------------------------

def test_triad_readiness_needs_both_thresholds():
    """readiness = ns('fuel/remaining') >= 40 and ns('ship/charge') >= 60
    — ONE expression in ONE widget ([CXC-DSL-C11]); the Pass-1 version
    needed 3 widgets + 2 intermediate namespaces + 2 bus hops."""
    rt = HeadlessRuntime(runtime_id=RUNTIME_ID)
    rt.load(LocalFileLoader(EXAMPLES))
    rt.start()
    try:
        events = rt.advance_to(0.5)
        ready = _events_for(events, "jumpdrive/readiness")
        # first wake (fuel change at t=0.1): fuel ok, charge unset -> 0.0
        assert ready == [(pytest.approx(0.1), False)]
        # the Pass-1 intermediate namespaces are GONE
        assert not _events_for(events, "jumpdrive/fuel_ok")
        assert not _events_for(events, "jumpdrive/charge_ok")

        rt.bus.publish_state("ship/charge", 80.0, source="test")
        events = rt.pump()      # reactive: re-evaluates AT arrival time
        ready = _events_for(events, "jumpdrive/readiness")
        assert ready == [(pytest.approx(0.5), True)]

        rt.bus.publish_state("ship/charge", 20.0, source="test")
        events = rt.pump()
        assert _events_for(events, "jumpdrive/readiness") == \
            [(pytest.approx(0.5), False)]

        # restore charge; then let the tank drain below the fuel floor.
        # fuel = 100 - 10t exactly while fuel >= 40 (flow 10/s), so
        # fuel == 40.0 at t == 6.0 (>= holds: still ready); the t = 6.1
        # tick reads 39.0 -> readiness drops on that very wake.
        rt.bus.publish_state("ship/charge", 90.0, source="test")
        events = rt.advance_to(7.0)
        ready = _events_for(events, "jumpdrive/readiness")
        assert [(round(t, 1), v) for t, v in ready] == \
            [(0.5, True), (6.1, False)]
    finally:
        rt.stop()


def test_triad_pump_conditional_rate_and_derating():
    """pump rate = min(damaged ? 2.5 : 10, 0.25 * remaining) — the
    conditional-rate arithmetic (use-case #2) as one expression, with
    proportional derating replacing the Pass-1 empty-cutoff hold."""
    rt = HeadlessRuntime(runtime_id=RUNTIME_ID)
    rt.load(LocalFileLoader(EXAMPLES))
    rt.start()
    try:
        # seeded output ([SPEC:CXC-DSL] initial:) + first wake at 0.1
        assert rt.bus.get_state("pump/flow_rate") == 10.0
        events = rt.advance_to(0.2)
        flow = _events_for(events, "pump/flow_rate")
        assert flow and flow[0][1] == 10.0

        # a BOOL damage publish reads as damaged (canon truthiness —
        # the Pass-1 value map silently missed exactly this)
        rt.bus.publish_state("pump/damaged", True, source="test")
        events = rt.pump()
        assert _events_for(events, "pump/flow_rate")[-1][1] == 2.5

        rt.bus.publish_state("pump/damaged", "off", source="test")
        events = rt.pump()
        assert _events_for(events, "pump/flow_rate")[-1][1] == 10.0

        # drain long: below remaining = 40 the k-term derates the flow
        # (flow = 0.25 * remaining) and the tank NEVER goes negative
        events = rt.advance_to(30.0)
        flow = _events_for(events, "pump/flow_rate")
        fuel = rt.bus.get_state("fuel/remaining")
        assert 0.0 < fuel < 40.0
        assert flow[-1][1] == pytest.approx(0.25 * fuel, rel=1e-6)
        assert all(v >= 0.0 for _, v in flow)
    finally:
        rt.stop()


def test_triad_fuel_stream_is_real_variable_rate_integration():
    """[CXC-DSL-C7]/[CXC-DSL-C11]: the tank integrates the PUMP'S LIVE
    RATE — fuel -= ns('pump/flow_rate') * dt per tick (use-case #1).
    Exact expected sequence computed by an independent Python mirror;
    the drive is repeated to prove the stateless-integration property
    (the bus is the state, tick(t) is deterministic)."""
    def drive():
        rt = HeadlessRuntime(runtime_id=RUNTIME_ID)
        rt.load(LocalFileLoader(EXAMPLES))
        rt.start()
        try:
            events = rt.advance_to(1.0)
            rt.bus.publish_state("pump/damaged", "on", source="golden")
            events += rt.advance_to(2.0)
            return events
        finally:
            rt.stop()

    a, b = drive(), drive()
    assert a == b, "identical drive must reproduce the exact sequence"

    fuel = _events_for(a, "fuel/remaining")
    # independent mirror: healthy flow 10/s from the seeded rate; the
    # tank's first tick (dt=0) republishes the seed; damage at t=1.0
    # takes effect on the 1.1 tick (pump woke at arrival 1.0)
    expect, remaining, flow = [(0.0, 100.0)], 100.0, 10.0
    for k in range(1, 21):
        t = k * 0.1
        if t > 1.0 + 1e-9:
            flow = 2.5
        remaining = remaining - flow * 0.1
        expect.append((t, remaining))
    got = [(round(t, 1), v) for t, v in fuel]
    want = [(round(t, 1), v) for t, v in expect]
    assert got == want


# ---------------------------------------------------------------------------
# Schedule parsing edges
# ---------------------------------------------------------------------------

def test_schedule_defaults_and_degradation():
    assert parse_schedule(None, "w").mode == "continuous"
    assert parse_schedule({}, "w").mode == "continuous"
    assert parse_schedule({"schedule": {"mode": "warp"}}, "w").mode \
        == "continuous"                          # degrade, keep running
    s = parse_schedule({"schedule": {"mode": "periodic",
                                     "interval_s": -3}}, "w")
    assert s.interval_s == 60.0
    s = parse_schedule({"mode": "periodic", "interval_s": 2}, "w")
    assert s.mode == "periodic" and s.interval_s == 2.0   # flat form
    s = parse_schedule({"schedule": {"mode": "bus_triggered",
                                     "trigger": "a/b"}}, "w")
    assert s.triggers == ["a/b"]
