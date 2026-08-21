"""
[SPEC:CXC-FLAUTH] Composer Flow mode — headless widget authoring
(Pass 3) compliance tests, CXC-FLAUTH-C1..C11:

  C1   logic node — a headless widget renders as kind="logic" with its
       declared in/out ports + schedule badge, from static introspection
  C2   static introspection — the ports/schedule table derives from the
       .compose.yaml; build_graph + inspector render FROM it (no
       hardcoding: extend the config, the table follows)
  C3   output wires first-class — dragging a logic output port -> hub
       writes the outputs: entry; round-trips byte-identically;
       Bindings/Source reflect it
  C4   create & wire — ＋ Logic node adds a headless widget; input/
       output wiring reuses the existing drag-to-wire (no new mechanic)
  C5   expr authoring — inline per-output expr editor live-validated by
       the DSL parser; arbitrary code refused, never written
  C6   schedule — the picker writes headless.schedule; the schedule
       travels in the widget, the roster stays pure membership
  C7   deploy — Push writes roster + widget configs via the Pass-1
       loader; a HeadlessRuntime fetches AND RUNS the result
       (author -> deploy -> run, end to end)
  C8   projection — every Flow edit writes the .compose.yaml; an
       Intelligence authoring over bus commands yields the identical
       graph and byte-identical YAML
  C9   live ports — a running widget's node shows live port values
       (discovery liveness; zero new producer)
  C10  heartbeat — the deploy card reads _bus/headless/<rid>/health
       (the ONE Pass-3 runtime addition)
  C11  reserved seam — the _bus/headless/<id>/* convention is declared
       and the "Inspect ▸" affordance exists as a no-op (Pass-4 handoff)
"""

from __future__ import annotations

import copy
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.composer.app import ComposerApp
from composeio.composer.document import CompositeDocument
from composeio.composer.flowpanel import build_graph, schedule_summary
from composeio.composer import deploy as deploy_mod
from composeio.render.engine import dsl
from composeio.render.engine.headless import (HEADLESS_BUS_PREFIX,
                                              HeadlessRuntime,
                                              LocalFileLoader,
                                              health_namespace,
                                              introspect_ports,
                                              is_logic_spec)

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples",
                        "headless")

PUMP_EXPR = "min(ns('pump/damaged') ? 2.5 : 10.0, 0.25 * ns('fuel/remaining'))"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    os.environ.pop("COMPOSEUX_MQTT_BROKER", None)
    a = ComposerApp("", windowed=False, connect_mqtt=False)
    a.start()
    yield a
    a.stop()


def _ext_publish(core, ns, value, source="peer"):
    core.design_bus.ensure_namespace(ns, engine_config="json")
    core.design_bus.publish_state(ns, value, source=source)


def _author_pump(app):
    """UI-author a fuel_pump-shaped logic node and return its id."""
    flow, insp = app.ui.flow, app.ui.flow_inspector
    wid = flow.add_logic_node()
    flow.author_output_wire(wid, "pump/flow_rate")
    insp.refresh()
    assert insp.set_output_expr("pump/flow_rate", PUMP_EXPR)
    insp.set_schedule("bus_triggered",
                      triggers=["pump/damaged", "fuel/remaining"])
    return wid


def _select_logic(app, wid):
    app.ui.flow.select_logic(wid)
    app.ui.flow_inspector.refresh()


# ---------------------------------------------------------------------------
# C2 — static introspection ([CXC-FLAUTH-INTROSPECT])
# ---------------------------------------------------------------------------

def test_c2_introspection_table_from_triad_config():
    """The table derives ONLY from the config — proven against the
    shipped jump-drive pump (the previs scene)."""
    with open(os.path.join(EXAMPLES, "widgets",
                           "fuel_pump.compose.yaml")) as f:
        cfg = yaml.safe_load(f)
    spec = dict(cfg["widgets"][0])
    spec["headless"] = cfg["headless"]         # widget-level in a composite
    t = introspect_ports(spec, "fuel_pump")
    assert {i["namespace"] for i in t["inputs"]} == \
        {"pump/damaged", "fuel/remaining"}
    assert [o["namespace"] for o in t["outputs"]] == ["pump/flow_rate"]
    assert t["outputs"][0]["expr"].startswith("min(")
    assert t["schedule"]["mode"] == "bus_triggered"
    assert t["schedule"]["triggers"] == ["pump/damaged", "fuel/remaining"]
    assert is_logic_spec(spec)
    # a visual widget with a mere publish wire is NOT a logic node
    assert not is_logic_spec({"type": "button",
                              "outputs": [{"namespace": "a/b"}]})


def test_c2_table_driven_no_hardcoding():
    """Extend the config -> the table (and therefore the graph) follows
    with zero editor/graph changes ([CXC-BE-INTROSPECT] pattern)."""
    spec = {"type": "logic", "id": "n",
            "headless": {"schedule": {"mode": "continuous"}},
            "outputs": [{"namespace": "a/out",
                         "expr": "ns('a/in') + 1"}]}
    t1 = introspect_ports(spec, "n")
    assert [i["namespace"] for i in t1["inputs"]] == ["a/in"]
    spec["outputs"].append({"namespace": "b/out",
                            "expr": "ns('b/in') * dt"})
    spec["namespace"] = "c/sub"
    t2 = introspect_ports(spec, "n")
    assert [o["namespace"] for o in t2["outputs"]] == ["a/out", "b/out"]
    assert {i["namespace"] for i in t2["inputs"]} == \
        {"a/in", "b/in", "c/sub"}
    # via records provenance; subscribe comes first (declaration order)
    assert t2["inputs"][0] == {"namespace": "c/sub", "via": "subscribe"}


def test_c2_schedule_badge_text():
    assert schedule_summary({"mode": "continuous"}) == "continuous · 10 Hz"
    assert schedule_summary({"mode": "periodic", "interval_s": 3600}) == \
        "periodic · every 3600s"
    assert schedule_summary({"mode": "bus_triggered",
                             "triggers": ["a/b", "c/d"]}) == "on a/b, c/d"


# ---------------------------------------------------------------------------
# C1 — the logic node in the graph
# ---------------------------------------------------------------------------

def test_c1_logic_node_from_introspection(app):
    wid = _author_pump(app)
    nodes, wires = build_graph(app.core)
    node = next(n for n in nodes if n.node_id == f"w:{wid}")
    assert node.kind == "logic"
    assert set(node.in_ports) == {"pump/damaged", "fuel/remaining"}
    assert node.out_ports == ["pump/flow_rate"]
    assert node.schedule["mode"] == "bus_triggered"
    assert node.subtitle == "logic · on pump/damaged, fuel/remaining"
    # ports wired: subscribe wires into input ports, publish wire out
    keys = {w.key for w in wires}
    assert ("pump/damaged", wid, "pump/damaged", "subscribe") in keys
    assert ("fuel/remaining", wid, "fuel/remaining", "subscribe") in keys
    assert ("pump/flow_rate", wid, "pump/flow_rate", "publish") in keys
    # the output namespace became a hub node
    assert any(n.node_id == "ns:pump/flow_rate" for n in nodes)


def test_c1_logic_node_renders(app):
    """The Flow lens draws logic nodes + the ＋ Logic chip without error."""
    wid = _author_pump(app)
    _ext_publish(app.core, "fuel/remaining", 62.4, "rig")
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    flow = app.ui.flow
    assert flow._logic_btn_zone is not None
    node = next(n for n in flow._nodes if n.node_id == f"w:{wid}")
    assert node.rect is not None
    # port geometry: inputs on the left edge, outputs on the right
    ix, _iy = flow._logic_port(node, "fuel/remaining", "in")
    ox, _oy = flow._logic_port(node, "pump/flow_rate", "out")
    assert ix == flow._gx(node.rect.x)
    assert ox == flow._gx(node.rect.x + node.rect.w)


# ---------------------------------------------------------------------------
# C3 — output wires are first-class ([CXC-FLAUTH-OUTWIRE])
# ---------------------------------------------------------------------------

def test_c3_output_wire_writes_raw_spec_and_roundtrips(app, tmp_path):
    flow = app.ui.flow
    wid = flow.add_logic_node()
    flow.author_output_wire(wid, "pump/flow_rate")
    layer = app.core.document.get_layer(wid)
    assert layer.properties["outputs"] == [{"namespace": "pump/flow_rate"}]
    # the design publishes it -> declared internal (not external)
    ns = app.core.document.get_namespace("pump/flow_rate")
    assert ns is not None and ns.external is False
    # Bindings summary sees the publish wire
    rows = app.core.document.bindings()
    assert any(r["kind"] == "publish" and r["widget"] == wid
               and r["namespace"] == "pump/flow_rate" for r in rows)
    # Source projection sees it
    app.render.drain_updates()
    assert "pump/flow_rate" in app.ui.source.text()
    # byte-identical round-trip ([CXC-FLAUTH-C3]/[C8])
    out = tmp_path / "logic.compose.yaml"
    app.core.save(str(out))
    assert CompositeDocument.from_yaml(out.read_text()).to_yaml() \
        == out.read_text()


def test_c3_output_wire_drag_gesture(app):
    """The REAL pointer path: down on the logic node's (phantom) output
    port, pan onto a namespace hub, release — existing drag-to-wire, no
    new mechanic ([CXC-FLAUTH-C4])."""
    flow = app.ui.flow
    wid = flow.add_logic_node()
    # a hub to land on
    flow.declare_expected("jumpdrive/readiness")
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    node = next(n for n in flow._nodes if n.node_id == f"w:{wid}")
    hub = next(n for n in flow._nodes
               if n.node_id == "ns:jumpdrive/readiness")
    px, py = flow._logic_port(node, "+", "out")
    hx = flow._gx(hub.rect.x + hub.rect.w / 2)
    hy = flow._gy(hub.rect.y + hub.rect.h / 2)
    flow.on_pointer_down(px - 2, py)
    assert flow._drag is not None and flow._drag["mode"] == "out-port"
    flow.on_pan(hx - (px - 2), hy - py)
    flow.on_pan_end()
    layer = app.core.document.get_layer(wid)
    assert layer.properties["outputs"] == \
        [{"namespace": "jumpdrive/readiness"}]


# ---------------------------------------------------------------------------
# C4 — create & wire ([CXC-FLAUTH-CREATE])
# ---------------------------------------------------------------------------

def test_c4_add_logic_node_defaults(app):
    wid = app.ui.flow.add_logic_node()
    layer = app.core.document.get_layer(wid)
    assert layer is not None and layer.widget_type == "logic"
    assert layer.properties["headless"] == \
        {"schedule": {"mode": "continuous"}}
    # named + selected; the WC follows to the Flow inspector
    ib = app.core.internal_bus
    assert ib.get_state("composer/flow/selected") == \
        {"kind": "logic", "widget_id": wid}
    assert ib.get_state("composer/wc/active_tab") == "flow"
    # a second one gets the next name
    assert app.ui.flow.add_logic_node() != wid


def test_c4_input_wire_first_drop_subscribes(app):
    flow = app.ui.flow
    wid = flow.add_logic_node()
    _ext_publish(app.core, "fuel/remaining", 88.0, "rig")
    flow.author_logic_input("fuel/remaining", wid)
    layer = app.core.document.get_layer(wid)
    assert layer.namespace == "fuel/remaining"
    # discovered-external signal -> declared external ([CXC-FL-DISCOVERY])
    assert app.core.document.get_namespace("fuel/remaining").external is True
    node = next(n for n in build_graph(app.core)[0]
                if n.node_id == f"w:{wid}")
    assert node.in_ports == ["fuel/remaining"]


def test_c4_input_drop_on_bus_triggered_joins_triggers(app):
    flow, insp = app.ui.flow, app.ui.flow_inspector
    wid = flow.add_logic_node()
    flow.author_logic_input("fuel/remaining", wid)
    _select_logic(app, wid)
    insp.set_schedule("bus_triggered", triggers=["fuel/remaining"])
    flow.author_logic_input("ship/charge", wid)
    layer = app.core.document.get_layer(wid)
    assert layer.properties["headless"]["schedule"]["trigger"] == \
        ["fuel/remaining", "ship/charge"]
    node = next(n for n in build_graph(app.core)[0]
                if n.node_id == f"w:{wid}")
    assert set(node.in_ports) == {"fuel/remaining", "ship/charge"}


def test_c4_rail_drag_onto_logic_node(app):
    """Rail row dragged onto the logic node = subscribe (the same
    gesture that wires visual widgets)."""
    flow = app.ui.flow
    wid = flow.add_logic_node()
    _ext_publish(app.core, "dradis/contact", 2, "sim")
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    row = next(rect for rect, e in flow._rail_rows
               if e.name == "dradis/contact")
    node = next(n for n in flow._nodes if n.node_id == f"w:{wid}")
    sx, sy = row.x + 10, row.y + 10
    tx = flow._gx(node.rect.x + node.rect.w / 2)
    ty = flow._gy(node.rect.y + node.rect.h / 2)
    flow.on_pointer_down(sx, sy)
    flow.on_pan(tx - sx, ty - sy)
    flow.on_pan_end()
    assert app.core.document.get_layer(wid).namespace == "dradis/contact"


# ---------------------------------------------------------------------------
# C5 — expression authoring ([CXC-FLAUTH-EXPR])
# ---------------------------------------------------------------------------

def test_c5_expr_editor_valid_commit(app):
    wid = _author_pump(app)
    layer = app.core.document.get_layer(wid)
    assert layer.properties["outputs"] == \
        [{"namespace": "pump/flow_rate", "expr": PUMP_EXPR}]
    assert app.ui.flow_inspector.expr_state[0] is True
    app.render.drain_updates()
    # the Source tab shows the same YAML (wrapped/quoted by the emitter,
    # so compare parsed)
    src_cfg = yaml.safe_load(app.ui.source.text())
    child = src_cfg["widgets"][0]["children"][0]
    assert child["outputs"] == [{"namespace": "pump/flow_rate",
                                 "expr": PUMP_EXPR}]


def test_c5_expr_editor_refuses_invalid_and_arbitrary_code(app):
    wid = _author_pump(app)
    insp = app.ui.flow_inspector
    before = app.core.document.to_yaml()
    for bad in ("__import__('os').system('rm')",
                "ns('a/b') +",                       # syntax error
                "open('/etc/passwd')",
                "lambda: 1"):
        assert insp.set_output_expr("pump/flow_rate", bad) is False
        ok, msg = insp.expr_state
        assert ok is False and msg
    # refused means NEVER written ([CXC-BIND-BOUNDARY])
    assert app.core.document.to_yaml() == before


def test_c5_live_validation_agrees_with_dsl_try_parse(app):
    """The editor's green/red state IS the DSL parser's verdict —
    property-checked over a matrix of sources."""
    wid = _author_pump(app)
    insp = app.ui.flow_inspector
    _select_logic(app, wid)
    cases = [PUMP_EXPR, "dt * 3", "ns('a/b') >= 40 and ns('c/d') >= 60",
             "clamp(ns('x/y'), 0, 100)", "1 +", "import os", "x..y",
             "system('x')", "round(t, 1)"]
    for src in cases:
        insp._on_expr_change(src)
        expected = dsl.try_parse(src, "matrix") is not None
        assert insp.expr_state[0] is expected, src


# ---------------------------------------------------------------------------
# C6 — schedule picker ([CXC-FLAUTH-SCHEDULE])
# ---------------------------------------------------------------------------

def test_c6_schedule_picker_writes_headless_schedule(app):
    flow, insp = app.ui.flow, app.ui.flow_inspector
    wid = flow.add_logic_node()
    _select_logic(app, wid)
    layer = app.core.document.get_layer(wid)

    insp.set_schedule("periodic", interval_s=3600)
    assert layer.properties["headless"]["schedule"] == \
        {"mode": "periodic", "interval_s": 3600}
    insp.set_schedule("bus_triggered", triggers=["pump/damaged"])
    assert layer.properties["headless"]["schedule"] == \
        {"mode": "bus_triggered", "trigger": ["pump/damaged"]}
    insp.set_schedule("continuous")
    assert layer.properties["headless"]["schedule"] == \
        {"mode": "continuous"}
    # the graph badge follows the table
    node = next(n for n in build_graph(app.core)[0]
                if n.node_id == f"w:{wid}")
    assert node.schedule["mode"] == "continuous"


def test_c6_schedule_travels_in_widget_roster_is_membership(app, tmp_path):
    wid = _author_pump(app)
    insp = app.ui.flow_inspector
    app.core.deploy_loader = LocalFileLoader(str(tmp_path))
    insp.set_runtime_id("sched-rt")
    insp.push()
    roster = yaml.safe_load(
        (tmp_path / "sched-rt.roster.yaml").read_text())
    assert roster == {"runtime": "sched-rt", "widgets": [wid]}
    assert "schedule" not in str(roster)     # pure membership
    wcfg = yaml.safe_load(
        (tmp_path / "widgets" / f"{wid}.compose.yaml").read_text())
    assert wcfg["headless"]["schedule"]["mode"] == "bus_triggered"


# ---------------------------------------------------------------------------
# C7 — deploy + the end-to-end author -> deploy -> run loop
# ---------------------------------------------------------------------------

def test_c7_author_deploy_run_end_to_end(app, tmp_path):
    """A Composer-authored jump-drive-style pump pushes through the
    Pass-1 loader contract and RUNS on a HeadlessRuntime: publish its
    inputs, watch its expr output arrive ([CXC-FLAUTH-C7])."""
    wid = _author_pump(app)
    insp = app.ui.flow_inspector
    app.core.deploy_loader = LocalFileLoader(str(tmp_path))
    insp.set_runtime_id("e2e-rt-01")
    insp.toggle_member(wid)
    insp.push()
    status = app.core.internal_bus.get_state("composer/deploy/status")
    assert status["ok"] is True and status["widgets"] == [wid]
    assert status["roster"].endswith("e2e-rt-01.roster.yaml")

    rt = HeadlessRuntime(runtime_id="e2e-rt-01")
    ids = rt.load(LocalFileLoader(str(tmp_path)))
    rt.start()
    try:
        assert ids == [wid]
        assert rt.widgets[0].schedule.mode == "bus_triggered"
        rt.bus.ensure_namespace("fuel/remaining", engine_config="json")
        rt.bus.publish_state("fuel/remaining", 62.4, source="rig")
        events = rt.pump()
        flow_evs = [(ns, v) for _t, ns, v in events
                    if ns == "pump/flow_rate"]
        # min(damaged? 2.5 : 10, 0.25*62.4) = 10.0
        assert flow_evs and flow_evs[-1][1] == 10.0
        rt.bus.publish_state("pump/damaged", True, source="rig")
        events = rt.pump()
        assert [(ns, v) for _t, ns, v in events
                if ns == "pump/flow_rate"][-1][1] == 2.5
    finally:
        rt.stop()


def test_c7_push_over_bus_command(app, tmp_path):
    """Push is a bus command (composer/deploy/push) — an Intelligence
    deploys exactly like the deploy-card button."""
    wid = _author_pump(app)
    app.core.deploy_loader = LocalFileLoader(str(tmp_path))
    app.core.internal_bus.publish_event(
        "composer/deploy/push", "do",
        data={"runtime_id": "intel-rt", "widgets": [wid]},
        source="intelligence")
    assert (tmp_path / "intel-rt.roster.yaml").exists()
    assert (tmp_path / "widgets" / f"{wid}.compose.yaml").exists()


def test_c7_deploy_assignment_roundtrips(app, tmp_path):
    wid = _author_pump(app)
    insp = app.ui.flow_inspector
    insp.set_runtime_id("hub-headless-01")
    insp.toggle_member(wid)
    assert deploy_mod.deploy_state(app.core.document) == \
        {"runtime_id": "hub-headless-01", "widgets": [wid]}
    out = tmp_path / "d.compose.yaml"
    app.core.save(str(out))
    doc2 = CompositeDocument.from_yaml(out.read_text())
    assert deploy_mod.deploy_state(doc2)["runtime_id"] == "hub-headless-01"
    assert doc2.to_yaml() == out.read_text()
    # undo restores the previous assignment
    app.core.undo()
    assert deploy_mod.deploy_state(app.core.document)["widgets"] == []


# ---------------------------------------------------------------------------
# C8 — Flow stays a projection; Intelligence parity
# ---------------------------------------------------------------------------

def test_c8_intelligence_authoring_identical_graph_and_yaml(app):
    """The same commands over the bus (the MQTT-imported path) produce
    the IDENTICAL graph and byte-identical YAML — Flow is a projection,
    not an authority (DP-CXC-2/7)."""
    wid = _author_pump(app)
    g1 = build_graph(app.core)
    yaml1 = app.core.document.to_yaml()

    b = ComposerApp("", windowed=False, connect_mqtt=False)
    b.start()
    try:
        ib = b.core.internal_bus
        ib.publish_event("composer/canvas/widget_added", "do",
                         data={"widget_type": "logic", "widget_id": wid,
                               "x": 0, "y": 0}, source="intelligence")
        ib.publish_event("composer/config/namespace_changed", "do",
                         data={"action": "created",
                               "name": "pump/flow_rate",
                               "state_engine": "json", "external": False},
                         source="intelligence")
        ib.publish_event("composer/config/widget_changed", "do",
                         data={"widget_id": wid, "property": "outputs",
                               "value": [{"namespace": "pump/flow_rate",
                                          "expr": PUMP_EXPR}]},
                         source="intelligence")
        ib.publish_event("composer/config/widget_changed", "do",
                         data={"widget_id": wid, "property": "headless",
                               "value": {"schedule": {
                                   "mode": "bus_triggered",
                                   "trigger": ["pump/damaged",
                                               "fuel/remaining"]}}},
                         source="intelligence")
        g2 = build_graph(b.core)
        assert {n.node_id for n in g1[0]} == {n.node_id for n in g2[0]}
        assert {w.key for w in g1[1]} == {w.key for w in g2[1]}
        n1 = next(n for n in g1[0] if n.kind == "logic")
        n2 = next(n for n in g2[0] if n.kind == "logic")
        assert (n1.in_ports, n1.out_ports, n1.schedule) == \
            (n2.in_ports, n2.out_ports, n2.schedule)
        assert b.core.document.to_yaml() == yaml1
    finally:
        b.stop()


def test_c8_flow_edits_stay_in_lockstep(app):
    """Layout/Bindings/Source all read the same document the Flow edits
    wrote — no second authority."""
    wid = _author_pump(app)
    app.render.drain_updates()
    # Source shows the raw spec (parse: the emitter wraps long exprs)
    src = app.ui.source.text()
    assert "headless" in src and "outputs" in src
    child = yaml.safe_load(src)["widgets"][0]["children"][0]
    assert child["outputs"][0]["expr"] == PUMP_EXPR
    # Bindings summary shows publish + expr-read rows
    rows = app.core.document.bindings()
    kinds = {(r["kind"], r["namespace"]) for r in rows
             if r["widget"] == wid}
    assert ("publish", "pump/flow_rate") in kinds
    assert ("expr", "fuel/remaining") in kinds
    assert ("trigger", "pump/damaged") in kinds
    # validate() counts them as used (no false "unused" warnings)
    errors, warnings = app.core.validate()
    assert not errors
    assert not any("pump/flow_rate" in w and "unused" in w
                   for w in warnings)


# ---------------------------------------------------------------------------
# C9 — live port values (Confirmation tier)
# ---------------------------------------------------------------------------

def test_c9_live_port_values_from_discovery(app):
    wid = _author_pump(app)
    _ext_publish(app.core, "fuel/remaining", 62.4, "rig")
    _ext_publish(app.core, "pump/flow_rate", 8.0, "runtime")
    nodes, _wires = build_graph(app.core)
    node = next(n for n in nodes if n.node_id == f"w:{wid}")
    assert node.port_values["fuel/remaining"] == (True, 62.4)
    assert node.port_values["pump/flow_rate"] == (True, 8.0)
    assert node.port_values["pump/damaged"][0] is False   # silent input
    # a running node reads as live (its output speaks)
    assert node.live is True
    # and the node draws its live readout without error
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()


# ---------------------------------------------------------------------------
# C10 — the runtime heartbeat
# ---------------------------------------------------------------------------

def test_c10_runtime_publishes_heartbeat():
    rt = HeadlessRuntime(runtime_id="hb-rt", heartbeat_s=0.5)
    rt.add_widget("n", {
        "app": {"id": "hb-n"},
        "headless": {"schedule": {"mode": "continuous"}},
        "widgets": [{"type": "logic", "id": "n", "value": 1.0,
                     "outputs": [{"namespace": "hb/out", "attr": "value"}]}],
    })
    rt.start()
    try:
        rt.advance_to(1.6)
        hb = rt.bus.get_state(health_namespace("hb-rt"))
        assert hb["runtime_id"] == "hb-rt"
        assert hb["widgets"] == 1 and hb["widget_ids"] == ["n"]
        assert hb["clock"] >= 1.5 and hb["last_tick"] >= 0.0
    finally:
        rt.stop()


def test_c10_heartbeat_default_off_keeps_goldens():
    """heartbeat is opt-in: the deterministic golden drive publishes
    nothing to _bus/headless/* by default."""
    rt = HeadlessRuntime(runtime_id="quiet-rt")
    rt.add_widget("n", {
        "app": {"id": "q-n"},
        "widgets": [{"type": "logic", "id": "n", "value": 1.0,
                     "outputs": [{"namespace": "q/out", "attr": "value"}]}],
    })
    rt.start()
    try:
        rt.advance_to(2.0)
        assert health_namespace("quiet-rt") not in rt.bus.namespaces
    finally:
        rt.stop()


def test_c10_deploy_card_reads_heartbeat(app):
    wid = _author_pump(app)
    insp = app.ui.flow_inspector
    insp.set_runtime_id("hub-headless-01")
    text, ok = insp._heartbeat_text("hub-headless-01")
    assert ok is False and "no heartbeat" in text
    # the runtime speaks (what an MQTT-bridged runtime's beat looks like
    # once the transport delivers it to the design bus)
    _hb_ns = health_namespace("hub-headless-01")
    app.core.design_bus.ensure_namespace(_hb_ns, engine_config="json")
    app.core.design_bus.publish_state(
        _hb_ns, {"runtime_id": "hub-headless-01", "widgets": 3,
                 "clock": 12.3, "last_tick": 12.25},
        source="hub-headless-01")
    hb = app.core.discovery.headless_health("hub-headless-01")
    assert hb is not None and hb["widgets"] == 3 and hb["age_s"] < 5
    text, ok = insp._heartbeat_text("hub-headless-01")
    assert ok is True and "running" in text and "3 widgets" in text
    # heartbeats are tool telemetry, NOT design signals: the rail/graph
    # never see them
    assert all(not e.name.startswith("_bus/")
               for e in app.core.discovery.snapshot())


# ---------------------------------------------------------------------------
# C11 — the reserved Pass-4 seam
# ---------------------------------------------------------------------------

def test_c11_namespace_convention_declared():
    assert HEADLESS_BUS_PREFIX == "_bus/headless"
    assert health_namespace("x-01") == "_bus/headless/x-01/health"


def test_c11_inspect_wired_to_monitor_design_untouched(app):
    """The "Inspect ▸" affordance exists in the logic inspector.  Since
    Pass 4 ([SPEC:CXC-HMON] [CXC-HMON-C9]) it is WIRED — the tap
    publishes the `composer/monitor/open` bus command and the host opens
    the Headless Monitor scoped to this widget — while the DESIGN stays
    untouched (read-only observation)."""
    wid = _author_pump(app)
    _select_logic(app, wid)
    app.core.internal_bus.publish_state("composer/wc/active_tab", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    insp = app.ui.flow_inspector
    zone = next((r for r, fn in insp._zones
                 if fn == insp._inspect), None)
    assert zone is not None, "Inspect affordance missing"
    opened = []
    app.core.internal_bus.subscribe_events(
        "composer/monitor/open", lambda ns, et, d: opened.append(d))
    before = app.core.document.to_yaml()
    taps = insp.inspect_taps
    insp.on_tap(zone.x + zone.w / 2, zone.y + zone.h / 2)
    assert insp.inspect_taps == taps + 1
    assert opened and opened[0]["widget_id"] == wid   # the bus command
    app.render.drain_updates()
    # no MQTT in this rig -> the engine-dialog host opened, scoped
    assert app.ui.monitor_dialog.visible
    assert app.ui.monitor_pane.scope_widget == wid
    assert app.core.document.to_yaml() == before      # design untouched


# ---------------------------------------------------------------------------
# the logic engine widget itself
# ---------------------------------------------------------------------------

def test_logic_widget_parses_and_renders_nothing():
    from composeio.render.engine.parser import parse_widget_tree
    from composeio.render.widgets import LogicWidget
    comp = parse_widget_tree({
        "design": {"title": "t", "width": 200, "height": 100},
        "widgets": [{"type": "pixel_stack", "id": "root", "width": 200,
                     "height": 100,
                     "children": [{"type": "logic", "id": "n",
                                   "headless": {"schedule": {
                                       "mode": "continuous"}},
                                   "outputs": [{"namespace": "a/b",
                                                "attr": "value"}]}]}],
    })
    w = next(x for x in comp.root.iter_widgets() if x.widget_id == "n")
    assert isinstance(w, LogicWidget)
    assert comp.outputs and comp.outputs[0].namespace == "a/b"
    from composeio.render.widget import Constraints
    size = w.measure(Constraints(200, 100))
    assert (size.width, size.height) == (0.0, 0.0)   # geometry-free


def test_logic_type_in_bindable_table():
    from composeio.render.engine.bindings import bindable_attrs_for
    attrs = bindable_attrs_for("logic")
    assert "value" in attrs and "visible" in attrs
