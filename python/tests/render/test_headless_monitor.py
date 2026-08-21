"""
[SPEC:CXC-HMON] Headless Monitor (Pass 4 — Diagnosis) compliance tests,
CXC-HMON-C1..C10:

  C1   tick trace — with trace enabled, each tick leaves a record
       (inputs, per-output eval, published/suppressed) and the runtime
       publishes the ring to _bus/headless/<id>/trace
  C2   DSL trace mode — evaluate_traced returns the SAME value as
       evaluate (bit-exact over the whole conformance corpus) PLUS
       branch-taken + intermediates; dead branches NOT evaluated
  C3   suppression visible — a publish-on-change suppression is a
       first-class record ("why nothing published")
  C4   internal state — $vars + behavior/schedule state on
       _bus/headless/<id>/state
  C5   per-widget health — rate/ticks/last-error/uptime on
       _bus/headless/<id>/health
  C6   opt-in + throttled — default OFF (deterministic drives publish
       NOTHING on the family and are byte-identical traced-or-not);
       enable is a bus publish; ring buffer + sampled/on-change publish,
       never per-tick
  C7   annotated hero — the viewer renders the output expr with the
       tick's live values + lit taken branch + result + verdict
  C8   replay — clicking a timeline tick re-annotates the hero
  C9   two hosts — Flow pop-out (see also
       test_flow_headless_authoring.test_c11_*) and the standalone
       fleet app: one MonitorPane surface, two frames
  C10  bus-native — a raw bus subscriber / an Intelligence reads the
       identical trace off _bus/headless/*
"""

from __future__ import annotations

import json
import os
import struct
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine import dsl
from composeio.render.engine.dsl_conformance import runner
from composeio.render.engine.headless import (HEADLESS_BUS_PREFIX,
                                              HeadlessRuntime,
                                              LocalFileLoader,
                                              health_namespace,
                                              state_namespace,
                                              trace_ctl_namespace,
                                              trace_namespace)
from composeio.composer.monitor import (MonitorModel, MonitorPane, annotate,
                                        build_monitor_app)

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples",
                        "headless")

PUMP_EXPR = "ns('pump/damaged') ? 0.0 : min(ns('fuel/remaining'), 8.0)"


def pump_config(expr: str = PUMP_EXPR, mode: str = "bus_triggered") -> dict:
    """The previs fuel_pump shape, inline (no file dependency)."""
    headless: dict = {"schedule": {"mode": mode}}
    if mode == "bus_triggered":
        headless["schedule"]["trigger"] = ["pump/damaged", "fuel/remaining"]
    return {
        "app": {"id": "hmon-pump"},
        "headless": headless,
        "variables": {"normal_rate": 8.0, "damaged_rate": 0.0},
        "widgets": [{"type": "logic", "id": "pump",
                     "outputs": [{"namespace": "pump/flow_rate",
                                  "expr": expr}]}],
    }


def family_events(rt) -> list:
    """Every _bus/headless/* namespace currently holding STATE."""
    out = []
    for name in rt.bus.namespaces:
        if name.startswith(HEADLESS_BUS_PREFIX) \
                and rt.bus.get_state(name) is not None:
            out.append(name)
    return sorted(out)


@pytest.fixture
def rt():
    r = HeadlessRuntime(runtime_id="hmon-rt")
    r.add_widget("pump", pump_config())
    r.start()
    r.bus.ensure_namespace("pump/damaged", engine_config="json")
    r.bus.ensure_namespace("fuel/remaining", engine_config="json")
    yield r
    r.stop()


def drive_damage_story(r, t0: float = 0.0) -> None:
    """The previs damage-flip: healthy -> damaged -> damaged (again,
    suppressed) -> recovered.  *t0* places the story after any clock the
    runtime has already reached (bus-trigger arrivals stamp the CURRENT
    clock)."""
    r.advance_to(t0)
    r.bus.publish_state("fuel/remaining", 62.4, source="t")
    r.advance_to(t0 + 0.3)
    r.bus.publish_state("pump/damaged", True, source="t")     # the event
    r.advance_to(t0 + 0.6)
    r.bus.publish_state("fuel/remaining", 64.0, source="t")   # suppressed
    r.advance_to(t0 + 0.9)
    r.bus.publish_state("pump/damaged", False, source="t")    # recovered
    r.advance_to(t0 + 1.2)


# ---------------------------------------------------------------------------
# C2 — DSL trace mode
# ---------------------------------------------------------------------------

_CASES = runner.load_corpus()["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_c2_traced_value_parity_whole_corpus(case):
    """evaluate_traced == evaluate over the ENTIRE conformance corpus:
    identical IEEE-754 bits for numbers, identical type/value otherwise,
    identical error class on eval errors ([CXC-HMON-C2])."""
    try:
        e1 = dsl.parse(case["expr"])
        e2 = dsl.parse(case["expr"])
    except dsl.DslParseError:
        return                                  # parse-error cases: no eval
    ctx = dsl.EvalContext(ns=case.get("ns", {}), vars=case.get("vars", {}),
                          dt=float(case.get("dt", 0.0)),
                          t=float(case.get("t", 0.0)))
    try:
        v1 = e1.evaluate(ctx)
    except dsl.DslEvalError:
        with pytest.raises(dsl.DslEvalError):
            e2.evaluate_traced(ctx)
        return
    tr = e2.evaluate_traced(ctx)
    assert type(tr.value) is type(v1)
    if isinstance(v1, float):
        assert struct.pack(">d", v1) == struct.pack(">d", tr.value)
    else:
        assert tr.value == v1


def test_c2_dead_ternary_branch_never_evaluated():
    reads = []
    e = dsl.parse("ns('a') ? ns('b') : ns('c')")
    tr = e.evaluate_traced(dsl.EvalContext(
        ns=lambda n: (reads.append(n), {"a": 1, "b": 5, "c": 7}[n])[1]))
    assert tr.value == 5.0
    assert reads == ["a", "b"]                  # 'c' NEVER read
    tern = [s for s in tr.steps if s["kind"] == "ternary"][0]
    assert tern["taken"] == "then"
    assert tern["dead_evaluated"] is False
    assert tern["dead_span"] is not None
    src = e.source
    assert src[tern["dead_span"][0]:tern["dead_span"][1]] == "ns('c')"


def test_c2_short_circuit_recorded():
    reads = []
    e = dsl.parse("ns('a') and ns('b')")
    tr = e.evaluate_traced(dsl.EvalContext(
        ns=lambda n: (reads.append(n), {"a": 0.0, "b": 9}[n])[1]))
    assert tr.value == 0.0 and reads == ["a"]
    logic = [s for s in tr.steps if s["kind"] == "logic"][0]
    assert logic["short_circuit"] is True
    assert logic["dead_evaluated"] is False
    assert e.source[logic["dead_span"][0]:logic["dead_span"][1]] == "ns('b')"
    # the non-short-circuit case records both operands
    tr2 = dsl.parse("ns('a') and ns('b')").evaluate_traced(
        dsl.EvalContext(ns={"a": 1, "b": 9}))
    logic2 = [s for s in tr2.steps if s["kind"] == "logic"][0]
    assert logic2["short_circuit"] is False and logic2["operands"] == [1.0, 9.0]


def test_c2_intermediates_and_reads():
    tr = dsl.parse("1 + 2 * ns('x')").evaluate_traced(
        dsl.EvalContext(ns={"x": 3}))
    ops = [(s["op"], s["result"]) for s in tr.steps if s["kind"] == "op"]
    assert ops == [("*", 6.0), ("+", 7.0)]      # post-order intermediates
    assert tr.reads() == {"x": 3.0}
    fn = dsl.parse("min(4, 9)").evaluate_traced(dsl.EvalContext())
    step = [s for s in fn.steps if s["kind"] == "fn"][0]
    assert step["fn"] == "min" and step["operands"] == [4.0, 9.0] \
        and step["result"] == 4.0


def test_c2_trace_is_json_native():
    tr = dsl.parse(PUMP_EXPR).evaluate_traced(dsl.EvalContext(
        ns={"pump/damaged": True, "fuel/remaining": 62.4}))
    round_trip = json.loads(json.dumps(tr.to_dict()))
    assert round_trip["value"] == tr.value
    assert round_trip["steps"] == tr.steps


# ---------------------------------------------------------------------------
# C1 + C3 — the tick trace on the bus, suppression represented
# ---------------------------------------------------------------------------

def test_c1_trace_records_on_bus(rt):
    assert rt.set_trace("pump", True)
    drive_damage_story(rt)
    payload = rt.bus.get_state(trace_namespace("pump"))
    assert payload["widget_id"] == "pump"
    assert payload["runtime_id"] == "hmon-rt"
    ticks = payload["ticks"]
    assert ticks, "ring published no ticks"
    for rec in ticks:
        assert "t" in rec and "inputs" in rec and "outputs" in rec
        out = rec["outputs"][0]
        assert out["namespace"] == "pump/flow_rate"
        assert out["expr"] == PUMP_EXPR
        assert "published" in out
        assert isinstance(out.get("trace"), list)   # the traced evaluation
    # inputs carry namespace -> value for THIS tick
    assert any(rec["inputs"].get("pump/damaged") == "on" for rec in ticks)
    # the woken trigger is stamped on bus-triggered ticks
    assert any(rec.get("trigger", {}).get("namespace") == "pump/damaged"
               for rec in ticks)


def test_c3_suppression_and_branch_flip_visible(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    ticks = rt.bus.get_state(trace_namespace("pump"))["ticks"]

    def branch(rec):
        for s in rec["outputs"][0]["trace"]:
            if s["kind"] == "ternary":
                return s["taken"]
        return None

    verdicts = [(branch(r), r["outputs"][0]["published"],
                 r["outputs"][0].get("suppressed")) for r in ticks]
    # the damage event: then-branch, published (8.0 -> 0.0)
    assert ("then", True, None) in verdicts
    # still damaged: then-branch, SUPPRESSED as unchanged ([CXC-HMON-C3])
    assert ("then", False, "unchanged") in verdicts
    # recovered: else-branch, published again
    assert ("else", True, None) in verdicts
    sup = next(r for r in ticks if r["outputs"][0].get("suppressed"))
    assert sup["outputs"][0]["previous"] == sup["outputs"][0]["value"]


# ---------------------------------------------------------------------------
# C4 — internal state
# ---------------------------------------------------------------------------

def test_c4_state_vars_schedule_last_trigger(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    st = rt.bus.get_state(state_namespace("pump"))
    assert st["vars"] == {"normal_rate": 8.0, "damaged_rate": 0.0}
    sched = st["schedule"]
    assert sched["mode"] == "bus_triggered"
    assert sched["triggers"] == ["pump/damaged", "fuel/remaining"]
    assert sched["last_trigger"]["namespace"] in ("pump/damaged",
                                                  "fuel/remaining")
    assert st["last_input_change"]["namespace"] in ("pump/damaged",
                                                    "fuel/remaining")
    assert st["last_input_change"]["age_s"] >= 0.0


def test_c4_state_periodic_next_fire_and_behaviors():
    r = HeadlessRuntime(runtime_id="hmon-per")
    cfg = {
        "app": {"id": "hmon-per-w"},
        "headless": {"schedule": {"mode": "periodic", "interval_s": 2.0}},
        "widgets": [{"type": "logic", "id": "n", "value": 1.0,
                     "behaviors": [{"when": {"namespace": "alarm",
                                             "is": "on"},
                                    "do": {"behavior": "blink",
                                           "target": "value",
                                           "period_ms": 500}}],
                     "outputs": [{"namespace": "n/out", "attr": "value"}]}],
    }
    r.add_widget("n", cfg)
    r.start()
    try:
        r.set_trace("n", True)
        r.bus.ensure_namespace("alarm", engine_config="json")
        r.bus.publish_state("alarm", "on", source="t")
        r.advance_to(4.1)
        st = r.bus.get_state(state_namespace("n"))
        sched = st["schedule"]
        assert sched["mode"] == "periodic" and sched["interval_s"] == 2.0
        assert sched["next_due"] > 4.0          # next fire ([CXC-HMON-C4])
        assert 0.0 <= sched["next_in_s"] <= 2.0
        beh = st["behaviors"][0]
        assert beh["behavior"] == "blink" and beh["target"] == "value"
        assert beh["active"] is True and beh["elapsed_s"] >= 0.0
    finally:
        r.stop()


# ---------------------------------------------------------------------------
# C5 — per-widget health
# ---------------------------------------------------------------------------

def test_c5_health_rate_ticks_uptime(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    h = rt.bus.get_state(health_namespace("pump"))
    assert h["widget_id"] == "pump"
    assert h["ticks"] >= 4
    assert h["rate_hz"] > 0.0
    assert h["uptime_s"] >= 0.9
    assert h["last_error"] is None
    assert isinstance(h["rate_history"], list)


def test_c5_last_error_from_output_eval():
    r = HeadlessRuntime(runtime_id="hmon-err")
    # an ordering comparison against a non-numeric bus value is the
    # canonical author error (DslEvalError at the output site)
    r.add_widget("bad", pump_config(expr="ns('pump/damaged') < 5"))
    r.start()
    try:
        r.set_trace("bad", True)
        r.bus.ensure_namespace("pump/damaged", engine_config="json")
        r.advance_to(0.3)                       # move past the enable pub
        r.bus.publish_state("pump/damaged", "not-a-number", source="t")
        r.advance_to(0.6)
        h = r.bus.get_state(health_namespace("bad"))
        assert h is not None and h["last_error"] is not None
        assert "comparison" in h["last_error"]
        ticks = r.bus.get_state(trace_namespace("bad"))["ticks"]
        errs = [t["outputs"][0] for t in ticks
                if t["outputs"][0].get("error")]
        assert errs and errs[0]["suppressed"] == "error"
        assert errs[0]["published"] is False
    finally:
        r.stop()


# ---------------------------------------------------------------------------
# C6 — opt-in + throttled (CRITICAL)
# ---------------------------------------------------------------------------

def test_c6_default_off_publishes_nothing_on_family(rt):
    drive_damage_story(rt)
    assert family_events(rt) == []              # total bus silence
    assert rt.bus.get_state(trace_namespace("pump")) is None
    assert rt.bus.get_state(state_namespace("pump")) is None
    assert rt.bus.get_state(health_namespace("pump")) is None


def test_c6_observation_never_perturbs_execution():
    """Byte-identical published sequence traced vs untraced — proof that
    default-off (and even ON) leaves deterministic drives untouched."""
    def run(traced: bool):
        r = HeadlessRuntime(runtime_id="hmon-eq")
        r.add_widget("pump", pump_config())
        r.start()
        r.bus.ensure_namespace("pump/damaged", engine_config="json")
        r.bus.ensure_namespace("fuel/remaining", engine_config="json")
        if traced:
            r.set_trace("pump", True)
        try:
            events = []
            r.bus.publish_state("fuel/remaining", 62.4, source="t")
            events += r.advance_to(0.1)
            r.bus.publish_state("pump/damaged", True, source="t")
            events += r.advance_to(0.2)
            r.bus.publish_state("pump/damaged", False, source="t")
            events += r.advance_to(0.3)
            return [e for e in events
                    if not e[1].startswith(HEADLESS_BUS_PREFIX)]
        finally:
            r.stop()

    assert run(False) == run(True)


def test_c6_enable_is_a_bus_publish(rt):
    """[CXC-HMON-C6]: enabling trace is a REQUEST on the bus (what the
    Monitor publishes), not a code change."""
    rt.bus.publish_state(trace_ctl_namespace("pump"),
                         {"enabled": True, "depth": 5, "sample_s": 0.5},
                         source="monitor")
    rt.pump()                                   # ctl applies on the loop
    hw = rt.widget("pump")
    assert hw.trace.enabled and hw.app.trace_enabled
    assert hw.trace.depth == 5 and hw.trace.sample_s == 0.5
    drive_damage_story(rt)
    assert len(rt.bus.get_state(trace_namespace("pump"))["ticks"]) <= 5
    rt.bus.publish_state(trace_ctl_namespace("pump"), {"enabled": False},
                         source="monitor")
    rt.pump()
    assert not hw.trace.enabled and not hw.app.trace_enabled


def test_c6_ring_buffer_and_throttle_never_per_tick():
    r = HeadlessRuntime(runtime_id="hmon-thr")
    # continuous 10 Hz widget whose output CHANGES every tick (worst case)
    r.add_widget("hot", {
        "app": {"id": "hmon-hot"},
        "headless": {"schedule": {"mode": "continuous"}},
        "widgets": [{"type": "logic", "id": "hot",
                     "outputs": [{"namespace": "hot/t", "expr": "t"}]}],
    })
    r.start()
    try:
        r.set_trace("hot", True)
        r.advance_to(5.0)
        hw = r.widget("hot")
        assert hw.trace.ticks_total >= 50
        # ring bounded ([CXC-HMON-THROTTLE])
        assert len(hw.trace.ring) <= hw.trace.depth
        # NEVER a 10 Hz firehose: change-rate-limited + sampled
        assert hw.trace.publishes < hw.trace.ticks_total / 2
        assert hw.trace.publishes <= 1 + 5.0 / hw.trace.change_min_s
    finally:
        r.stop()


def test_c6_quiet_widget_publishes_at_sample_rate():
    r = HeadlessRuntime(runtime_id="hmon-quiet")
    r.add_widget("q", {
        "app": {"id": "hmon-quiet-w"},
        "headless": {"schedule": {"mode": "continuous"}},
        "widgets": [{"type": "logic", "id": "q",
                     "outputs": [{"namespace": "q/o", "expr": "1.0"}]}],
    })
    r.start()
    try:
        r.set_trace("q", True)
        r.advance_to(5.0)
        tr = r.widget("q").trace
        # first publish + one change-publish + ~1/s samples
        assert 2 <= tr.publishes <= 8
    finally:
        r.stop()


def test_c6_snapshot_on_request(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    before = rt.widget("pump").trace.publishes
    rt.bus.publish_state(trace_ctl_namespace("pump"),
                         {"enabled": True, "snapshot": 1}, source="monitor")
    rt.pump()
    assert rt.widget("pump").trace.publishes == before + 1


# ---------------------------------------------------------------------------
# C7 — the annotated hero (viewer, pure)
# ---------------------------------------------------------------------------

def _traced_output(rt, wid="pump"):
    ticks = rt.bus.get_state(trace_namespace(wid))["ticks"]
    return ticks


def test_c7_annotate_damage_tick(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    ticks = _traced_output(rt)
    dmg = next(t for t in ticks
               if t["inputs"].get("pump/damaged") == "on"
               and t["outputs"][0]["published"])
    out = dmg["outputs"][0]
    segs = annotate(out["expr"], out["trace"])
    text = "".join(s.get("text", "") for s in segs)
    assert text == out["expr"]                  # lossless segmentation
    roles = {s["text"].strip(): s["role"] for s in segs if "text" in s}
    assert roles.get("0.0") == "taken"          # lit taken branch
    dead = [s for s in segs if s["role"] == "dead" and "text" in s]
    assert dead and "min(" in "".join(s["text"] for s in dead)
    chips = [s["chip"] for s in segs if "chip" in s]
    assert "on" in chips                        # the live substituted read
    # dead-branch reads have NO chip (never evaluated)
    assert not any("62" in c for c in chips)


def test_c7_annotate_recovered_tick(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    rec = next(t for t in _traced_output(rt)
               if t["inputs"].get("pump/damaged") == "off")
    out = rec["outputs"][0]
    segs = annotate(out["expr"], out["trace"])
    taken = "".join(s["text"] for s in segs
                    if "text" in s and s["role"] == "taken")
    assert "min(" in taken and out["value"] == 8.0
    chips = [s["chip"] for s in segs if "chip" in s]
    assert "off" in chips and "64" in chips[-1]  # fuel value substituted


# ---------------------------------------------------------------------------
# C8 — replay
# ---------------------------------------------------------------------------

def test_c8_model_replay(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    m = MonitorModel()
    m.ingest(trace_namespace("pump"), rt.bus.get_state(trace_namespace("pump")))
    m.select("pump")
    ticks = m.ticks("pump")
    assert len(ticks) >= 3
    newest = m.hero_record()
    assert newest["t"] == ticks[0]["t"]         # live = newest
    target = ticks[-1]                           # replay the oldest
    m.replay(float(target["t"]))
    assert m.hero_record()["t"] == target["t"]  # [CXC-HMON-C8]
    m.replay(None)
    assert m.hero_record()["t"] == newest["t"]  # back to live


def test_c8_pane_timeline_tap_replays(rt):
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    app = build_monitor_app(bus=rt.bus, windowed=False,
                            scope_widget="pump", runtime_id="hmon-rt")
    app.start()
    try:
        rt.pump()                # deliver the pane's snapshot request
        app.drain_updates()
        pane = app.comp.root
        assert pane.model.ticks("pump")
        png = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                           "hmon_scoped.png")
        app.render_headless(png)                # populates tap zones
        # tap the LAST timeline row (an older tick)
        rows = [z for z in pane._zones if z[0].h == 19]
        assert len(rows) >= 2
        rect = rows[-1][0]
        pane.on_tap(rect.x + 5, rect.y + 5)
        assert pane.model.hero_t is not None
        assert pane.model.hero_record()["t"] == pane.model.hero_t
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C9 — two hosts, one surface
# ---------------------------------------------------------------------------

def test_c9_scoped_frame_requests_trace_and_scopes(rt):
    """Host (a), the Flow pop-out frame: scoped to ONE widget, enables
    its trace over the bus on attach.  (The Composer-side wiring of
    "Inspect ▸" -> composer/monitor/open -> this frame is proven in
    test_flow_headless_authoring.test_c11_*.)"""
    app = build_monitor_app(bus=rt.bus, windowed=False,
                            scope_widget="pump", runtime_id="hmon-rt")
    app.start()
    try:
        rt.pump()                                # apply the ctl request
        assert rt.widget("pump").trace.enabled   # enabled BY the Monitor
        pane = app.comp.root
        assert pane.scope_widget == "pump"
        assert pane.model.selected == "pump"
    finally:
        app.stop()
    rt.pump()
    # the Monitor leaves the fleet as it found it
    assert not rt.widget("pump").trace.enabled


def test_c9_fleet_frame_rail_and_drilldown(rt):
    """Host (b), the standalone ops app: runtime heartbeats build the
    fleet rail; selecting a widget drills down (enables trace)."""
    rt.heartbeat_s = 0.5
    app = build_monitor_app(bus=rt.bus, windowed=False)
    app.start()
    try:
        rt.advance_to(1.1)                       # heartbeats flow
        app.drain_updates()
        pane = app.comp.root
        assert "hmon-rt" in pane.model.runtimes
        assert "pump" in pane.model.widgets_of("hmon-rt")
        pane.select("pump")
        rt.pump()
        assert rt.widget("pump").trace.enabled
        drive_damage_story(rt, t0=2.0)          # past the heartbeat clock
        app.drain_updates()
        assert pane.model.ticks("pump")          # drill-down shows ticks
        png = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                           "hmon_fleet.png")
        assert os.path.exists(app.render_headless(png))
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C10 — bus-native: the raw bus / an Intelligence reads the same trace
# ---------------------------------------------------------------------------

def test_c10_raw_subscriber_reads_identical_trace(rt):
    raw: list = []
    rt.bus.subscribe_pattern(HEADLESS_BUS_PREFIX + "/*",
                             lambda ns, v, old: raw.append((ns, v)))
    rt.set_trace("pump", True)
    drive_damage_story(rt)
    got = {ns: v for ns, v in raw}
    assert trace_namespace("pump") in got
    assert state_namespace("pump") in got
    assert health_namespace("pump") in got
    # identical payload to the retained state; JSON round-trips whole
    payload = got[trace_namespace("pump")]
    assert json.loads(json.dumps(payload)) == \
        json.loads(json.dumps(rt.bus.get_state(trace_namespace("pump"))))
    # an Intelligence diagnoses straight off the raw record: find the
    # suppression + the branch flip with no Monitor code at all
    ticks = payload["ticks"]
    suppressed = [t for t in ticks if any(o.get("suppressed") == "unchanged"
                                          for o in t["outputs"])]
    branches = {s["taken"] for t in ticks for o in t["outputs"]
                for s in o.get("trace", []) if s["kind"] == "ternary"}
    assert suppressed and branches == {"then", "else"}


# ---------------------------------------------------------------------------
# End-to-end: the previs story through the WHOLE stack
# ---------------------------------------------------------------------------

def test_e2e_triad_monitor_damage_flip():
    """A RUNNING headless widget (the shipped jump-drive pump) with trace
    enabled -> the Monitor reads the annotated tick -> the damage flip
    shows the branch change AND a suppression ([CXC-HMON] acceptance)."""
    rt = HeadlessRuntime(runtime_id="hub-headless-01")
    rt.load(LocalFileLoader(EXAMPLES), "jump-drive-01")
    rt.start()
    app = build_monitor_app(bus=rt.bus, windowed=False)
    app.start()
    try:
        pane = app.comp.root
        rt.bus.ensure_namespace("pump/damaged", engine_config="json")
        rt.advance_to(0.5)                       # tank integrates, pump flows
        pane.select("fuel_pump")                 # the deliberate act
        rt.pump()
        assert rt.widget("fuel_pump").trace.enabled
        rt.bus.publish_state("pump/damaged", True, source="ops")
        rt.advance_to(1.5)
        rt.bus.publish_state("pump/damaged", False, source="ops")
        rt.advance_to(2.5)
        app.drain_updates()

        ticks = pane.model.ticks("fuel_pump")
        assert len(ticks) >= 10
        taken = lambda r: next((s["taken"] for o in r["outputs"]
                                for s in o.get("trace", [])
                                if s["kind"] == "ternary"), None)
        # branch change visible across the story
        assert {taken(r) for r in ticks} == {"then", "else"}
        # a suppression is in the timeline
        assert any(o.get("suppressed") == "unchanged"
                   for r in ticks for o in r["outputs"])
        # the hero annotates the newest tick with live values
        rec = pane.model.hero_record()
        out = rec["outputs"][0]
        segs = annotate(out["expr"], out["trace"])
        assert "".join(s.get("text", "") for s in segs) == out["expr"]
        assert any(s.get("role") == "taken" for s in segs)
        assert any(s.get("role") == "dead" for s in segs)
        assert any("chip" in s for s in segs)
        # internal state + health arrived too
        assert pane.model.state["fuel_pump"]["vars"]["k"] == 0.25
        assert pane.model.health["fuel_pump"]["ticks"] > 0
    finally:
        app.stop()
        rt.stop()
