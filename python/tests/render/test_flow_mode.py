"""
Composer Flow mode + bus discovery tests ([SPEC:CXC-FLOW] compliance
[CXC-FL-C1..C9]):

  C1  graph renders wiring — bindings project as widget<->namespace wires,
      namespaces are hub nodes
  C2  synced projection — a wire authored in Flow writes the RAW binding;
      Bindings/Source reflect it; raw-spec fidelity holds
  C3  Layout<->Flow toggle — bus-state lens swap on the same design
  C4  live discovery — an external namespace appears in the registry the
      moment it speaks (MQTT peer case: smoke_flow_discovery_mqtt.py)
  C5  filter (P0) — keyword + prefix-glob + category narrow a 60+ rail;
      "N of M" reflects it
  C6  discover-and-wire — the rail drag's commit authors a subscription
  C7  declare-expected — binding a not-yet-live namespace activates on
      its first publish
  C8  pattern subscription — a widget bound to jumpdrive/* receives every
      matching namespace incl. ones appearing later (engine + bus level)
  C9  Intelligence parity — the same wiring over bus commands yields the
      identical graph

Plus: the bus pattern-primitive unit semantics, the MQTT topic mapping,
the external-flag round-trip, and the bare-string-visible fix.
"""

from __future__ import annotations

import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.bus import (ComposeIOBus, is_pattern, pattern_matches,
                           validate_pattern)
from composeio.composer.app import ComposerApp
from composeio.composer.document import CompositeDocument
from composeio.composer.flowpanel import build_graph
from composeio.render.engine.runtime import RenderApp


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    os.environ.pop("COMPOSEUX_MQTT_BROKER", None)
    a = ComposerApp("", windowed=False, connect_mqtt=False)
    a.start()
    yield a
    a.stop()


def _ext_publish(core, ns, value, source="peer"):
    """What an external peer's traffic looks like once the transport has
    dispatched it: a json-engine namespace on the design/external bus."""
    core.design_bus.ensure_namespace(ns, engine_config="json")
    core.design_bus.publish_state(ns, value, source=source)


def _build_console(core):
    ib = core.internal_bus
    ib.publish_event("composer/config/namespace_changed", "do",
                     data={"action": "created", "name": "ship/scanning",
                           "state_engine": "binary"}, source="t")
    ib.publish_event("composer/canvas/widget_added", "do",
                     data={"widget_type": "gauge",
                           "widget_id": "jump_readiness",
                           "x": 60, "y": 60, "w": 180, "h": 150,
                           "properties": {"label": "READINESS"}},
                     source="t")
    ib.publish_event("composer/canvas/widget_added", "do",
                     data={"widget_type": "rect", "widget_id": "dradis",
                           "x": 300, "y": 220, "w": 160, "h": 120,
                           "properties": {"fill_color": "#10202C"}},
                     source="t")


# ---------------------------------------------------------------------------
# the bus primitive ([CXC-FL-PATTERN] — the escalated addition)
# ---------------------------------------------------------------------------

def test_pattern_semantics():
    assert pattern_matches("jumpdrive/*", "jumpdrive/readiness")
    assert pattern_matches("jumpdrive/*", "jumpdrive/a/b/c")
    assert pattern_matches("jumpdrive/*", "jumpdrive")     # MQTT-# aligned
    assert not pattern_matches("jumpdrive/*", "jumpdrive2/x")
    assert not pattern_matches("jumpdrive/*", "ship/scanning")
    assert pattern_matches("*", "anything")
    assert pattern_matches("a/b", "a/b") and not pattern_matches("a/b", "a")
    assert is_pattern("jumpdrive/*") and is_pattern("*")
    assert not is_pattern("jumpdrive")


def test_mid_pattern_rejected():
    with pytest.raises(ValueError):
        validate_pattern("a/*/b")
    with pytest.raises(ValueError):
        validate_pattern("jump*drive")
    bus = ComposeIOBus()
    with pytest.raises(ValueError):
        bus.subscribe_pattern("a/*/b", lambda *a: None)


def test_bus_pattern_subscription_includes_later_namespaces():
    bus = ComposeIOBus()
    got = []
    bus.subscribe_pattern("jumpdrive/*", lambda ns, nv, ov: got.append((ns, nv)))
    bus.ensure_namespace("jumpdrive/a", engine_config="json")
    bus.publish_state("jumpdrive/a", 1, source="t")
    # a namespace that did not exist at subscribe time
    bus.ensure_namespace("jumpdrive/later", engine_config="json")
    bus.publish_state("jumpdrive/later", "x", source="t")
    bus.ensure_namespace("ship/x", engine_config="json")
    bus.publish_state("ship/x", 9, source="t")
    assert got == [("jumpdrive/a", 1), ("jumpdrive/later", "x")]


def test_bus_pattern_liveness_fires_on_unchanged_value():
    bus = ComposeIOBus()
    got = []
    bus.subscribe_pattern("jd/*", lambda ns, nv, ov: got.append(nv))
    bus.ensure_namespace("jd/a", engine_config="json")
    bus.publish_state("jd/a", 5, source="t")
    bus.publish_state("jd/a", 5, source="t")     # same value: still liveness
    assert got == [5, 5]


def test_mqtt_pattern_topic_mapping():
    from composeio.transport_mqtt import MQTTTransport
    t = MQTTTransport(broker="unused")
    assert t._pattern_topic("*") == "composeio/#"
    assert t._pattern_topic("jumpdrive/*") == "composeio/+/jumpdrive/#"
    t.subscribe_pattern("jumpdrive/*")
    t.subscribe_pattern("jumpdrive/*")          # dedupe
    assert t._pattern_topics == {"composeio/+/jumpdrive/#"}


def test_incoming_unknown_namespace_gets_permissive_engine():
    """The deafness fix: unknown EXTERNAL traffic must not be dropped by
    the binary default engine."""
    from composeio.transport import BusMessage, MessageType
    from composeio.transport_local import LocalTransport

    class FakeTransport(LocalTransport):
        pass

    ft = FakeTransport()
    bus = ComposeIOBus(global_transport=ft)
    bus.start()
    seen = []
    bus.subscribe_pattern("*", lambda ns, nv, ov: seen.append((ns, nv)))
    # simulate an incoming external message for an unknown namespace with
    # a NON-binary value
    for cb in ft._wildcard_subscribers:
        cb(BusMessage(namespace="jumpdrive/readiness",
                      type=MessageType.STATE, value="charging",
                      source="esp32"))
    assert ("jumpdrive/readiness", "charging") in seen
    assert bus.get_state("jumpdrive/readiness") == "charging"


def test_monitor_sees_external_traffic():
    """bus.on_message monitors were deaf to transport arrivals — fixed."""
    from composeio.transport import BusMessage, MessageType
    from composeio.transport_local import LocalTransport
    ft = LocalTransport()
    bus = ComposeIOBus(global_transport=ft)
    bus.start()
    msgs = []
    bus.on_message(lambda m: msgs.append(m.namespace))
    for cb in ft._wildcard_subscribers:
        cb(BusMessage(namespace="outside/sig", type=MessageType.STATE,
                      value=1, source="peer"))
    assert "outside/sig" in msgs


# ---------------------------------------------------------------------------
# C8 — engine-level pattern subscription
# ---------------------------------------------------------------------------

_PATTERN_DOC = {
    "design": {"title": "fam", "width": 320, "height": 240},
    "widgets": [{
        "type": "pixel_stack", "id": "root", "width": 320, "height": 240,
        "children": [
            {"type": "bus_monitor", "id": "mon", "namespace": "jumpdrive/*",
             "x": 0, "y": 0, "w": 300, "h": 200},
            {"type": "label", "id": "cap", "text": "t",
             "opacity": {"namespace": "jumpdrive/*"},
             "x": 0, "y": 210, "w": 100, "h": 20},
        ],
    }],
}


def test_c8_widget_pattern_subscription_engine_level():
    import copy
    app = RenderApp.from_dict(copy.deepcopy(_PATTERN_DOC), windowed=False)
    got = []
    mon = next(w for w in app.comp.root.iter_widgets()
               if w.widget_id == "mon")
    mon.on_bus_state = lambda ns, v: got.append((ns, v))
    app.bus.ensure_namespace("jumpdrive/readiness", engine_config="json")
    app.bus.publish_state("jumpdrive/readiness", "charging", source="t")
    # a family member that appears AFTER wiring
    app.bus.ensure_namespace("jumpdrive/new_member", engine_config="json")
    app.bus.publish_state("jumpdrive/new_member", 7, source="t")
    app.bus.ensure_namespace("ship/other", engine_config="json")
    app.bus.publish_state("ship/other", 1, source="t")
    app.drain_updates()
    assert ("jumpdrive/readiness", "charging") in got
    assert ("jumpdrive/new_member", 7) in got
    assert not any(ns == "ship/other" for ns, _ in got)
    # the attr binding rode the family too (last-writer-wins)
    cap = next(w for w in app.comp.root.iter_widgets()
               if w.widget_id == "cap")
    app.bus.publish_state("jumpdrive/readiness", 0.25, source="t")
    app.drain_updates()
    assert cap.opacity == pytest.approx(0.25)


def test_c8_pattern_namespace_declaration_not_registered():
    """A declared pattern subscription is not a literal namespace."""
    import copy
    doc = copy.deepcopy(_PATTERN_DOC)
    doc["namespaces"] = [{"name": "jumpdrive/*", "state_engine": "json",
                          "external": True}]
    app = RenderApp.from_dict(doc, windowed=False)
    assert "jumpdrive/*" not in app.bus.namespaces


# ---------------------------------------------------------------------------
# C4 / C5 — discovery + the filter
# ---------------------------------------------------------------------------

def test_c4_external_namespace_appears_in_discovery(app):
    disc = app.core.discovery
    assert disc is not None
    before = {e.name for e in disc.snapshot()}
    _ext_publish(app.core, "jumpdrive/readiness", "charging", "esp32-rig")
    snap = disc.snapshot()
    e = next(x for x in snap if x.name == "jumpdrive/readiness")
    assert "jumpdrive/readiness" not in before
    assert e.live and e.value == "charging" and e.source == "esp32-rig"
    assert disc.kind_of("jumpdrive/readiness") == "live-available"
    # rail-facing summary carries it too (Intelligence parity data)
    brief = disc.snapshot_brief()
    row = next(r for r in brief if r["namespace"] == "jumpdrive/readiness")
    assert row["kind"] == "live-available" and row["live"] is True


def test_c4_peer_discovery_supplies_engine(app):
    disc = app.core.discovery
    app.core.design_bus.publish_event(
        "_bus/discovery", "discovery",
        data={"app_id": "jumpdrive-rig",
              "namespaces": ["jumpdrive/readiness"],
              "engines": {"jumpdrive/readiness": "tri_level"}},
        source="jumpdrive-rig")
    _ext_publish(app.core, "jumpdrive/readiness", "charging")
    e = next(x for x in disc.snapshot()
             if x.name == "jumpdrive/readiness")
    assert e.engine == "tri_level" and e.engine_source == "peer"
    assert e.app_id == "jumpdrive-rig"


def test_c5_filter_narrows_sixty_plus(app):
    disc = app.core.discovery
    for i in range(58):
        _ext_publish(app.core, f"ship/sensor/{i:02d}", i, "hull")
    for ns, v in [("jumpdrive/readiness", "charging"),
                  ("jumpdrive/charge", 142),
                  ("jumpdrive/ftl_lock", "off"),
                  ("dradis/contact", {"n": 2})]:
        _ext_publish(app.core, ns, v, "rig")
    snap = disc.snapshot()
    assert len(snap) >= 60
    # prefix glob
    f = disc.filter(snap, text="jumpdrive/*")
    assert {e.name for e in f} == {"jumpdrive/readiness",
                                   "jumpdrive/charge",
                                   "jumpdrive/ftl_lock"}
    # keyword substring
    f2 = disc.filter(snap, text="contact")
    assert {e.name for e in f2} == {"dradis/contact"}
    # category: external (nothing declared -> everything qualifies)
    f3 = disc.filter(snap, category="active")
    assert len(f3) >= 60          # all just spoke
    # the rail count line reflects the filter (N of M)
    ui_flow = app.ui.flow
    ui_flow._set_filter_text("jumpdrive/*")
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    n, m = ui_flow._count_line
    assert n == 3 and m >= 60
    # prefix chips emerge from the traffic (hierarchical namespaces)
    assert "ship/*" in disc.prefix_chips()


def test_c5_internal_category(app):
    _build_console(app.core)
    _ext_publish(app.core, "jumpdrive/charge", 1)
    disc = app.core.discovery
    f = disc.filter(disc.snapshot(), category="internal")
    assert {e.name for e in f} == {"ship/scanning"}


# ---------------------------------------------------------------------------
# C1 / C9 — the graph projection
# ---------------------------------------------------------------------------

def test_c1_graph_renders_wiring(app):
    _build_console(app.core)
    ib = app.core.internal_bus
    ib.publish_event("composer/config/widget_changed", "do",
                     data={"widget_id": "jump_readiness",
                           "property": "value",
                           "value": {"namespace": "jumpdrive/readiness"}},
                     source="t")
    ib.publish_event("composer/config/widget_changed", "do",
                     data={"widget_id": "dradis", "property": "behaviors",
                           "value": [{"when": {"namespace": "ship/scanning",
                                               "is": "on"},
                                      "do": {"behavior": "rotate",
                                             "target": "rotation",
                                             "deg_per_s": 90}}]},
                     source="t")
    nodes, wires = build_graph(app.core)
    ids = {n.node_id for n in nodes}
    assert "ns:jumpdrive/readiness" in ids and "ns:ship/scanning" in ids
    assert "w:jump_readiness" in ids and "w:dradis" in ids
    ns_nodes = [n for n in nodes if n.kind == "namespace"]
    assert all(n.node_id.startswith("ns:") for n in ns_nodes)
    keys = {w.key for w in wires}
    assert ("jumpdrive/readiness", "jump_readiness", "value",
            "attr") in keys
    assert any(w.kind == "behavior" and w.widget_id == "dradis"
               for w in wires)


def test_c9_intelligence_wiring_yields_identical_graph(app):
    """The graph derives ONLY from the raw spec: the same commands over
    the bus (the MQTT-imported path) produce the identical projection."""
    _build_console(app.core)
    app.ui.flow.author_wire("jumpdrive/readiness", "jump_readiness",
                            "value")
    g1 = build_graph(app.core)
    doc_yaml = app.core.document.to_yaml()

    # a second composer driven purely by bus commands (what a remote
    # Intelligence's MQTT-imported events replay as)
    b = ComposerApp("", windowed=False, connect_mqtt=False)
    b.start()
    try:
        _build_console(b.core)
        ib = b.core.internal_bus
        ib.publish_event("composer/config/namespace_changed", "do",
                         data={"action": "created",
                               "name": "jumpdrive/readiness",
                               "state_engine": "json", "external": True},
                         source="intelligence")
        ib.publish_event("composer/config/widget_changed", "do",
                         data={"widget_id": "jump_readiness",
                               "property": "namespace",
                               "value": "jumpdrive/readiness"},
                         source="intelligence")
        g2 = build_graph(b.core)
        assert {n.node_id for n in g1[0]} == {n.node_id for n in g2[0]}
        assert {w.key for w in g1[1]} == {w.key for w in g2[1]}
        assert b.core.document.to_yaml() == doc_yaml
    finally:
        b.stop()


def test_headless_node_kind_slot():
    """[CXC-FL-HEADLESS] forward hook: the node model accepts a non-visual
    node without special-casing."""
    from composeio.composer.flowpanel import FlowNode
    n = FlowNode(node_id="l:threshold", kind="logic", title="threshold",
                 subtitle="headless logic", ports=["in", "out"])
    assert n.kind == "logic" and n.rect is None


# ---------------------------------------------------------------------------
# C3 — the toggle
# ---------------------------------------------------------------------------

def test_c3_layout_flow_toggle(app):
    ib = app.core.internal_bus
    assert app.ui.workshop.visible and not app.ui.flow.visible
    ib.publish_state("composer/ww/mode", "flow", source="t")
    app.render.drain_updates()
    assert app.ui.flow.visible and not app.ui.workshop.visible
    assert ib.get_state("composer/wc/active_tab") == "flow"
    ib.publish_state("composer/ww/mode", "layout", source="t")
    app.render.drain_updates()
    assert app.ui.workshop.visible and not app.ui.flow.visible
    # both lenses render without error in either mode
    app.render.render_frame()


def test_c3_toggle_buttons_publish_state(app):
    app.ui.btn_flow.on_press()
    app.render.drain_updates()
    assert app.core.internal_bus.get_state("composer/ww/mode") == "flow"
    app.ui.btn_layout.on_press()
    app.render.drain_updates()
    assert app.core.internal_bus.get_state("composer/ww/mode") == "layout"


# ---------------------------------------------------------------------------
# C2 / C6 — authoring in Flow writes the raw spec; projections stay synced
# ---------------------------------------------------------------------------

def test_c6_discover_and_wire_authors_subscription(app):
    _build_console(app.core)
    _ext_publish(app.core, "jumpdrive/readiness", "charging", "esp32-rig")
    app.ui.flow.author_wire("jumpdrive/readiness", "jump_readiness",
                            "value")
    doc = app.core.document
    layer = doc.get_layer("jump_readiness")
    assert layer.namespace == "jumpdrive/readiness"
    ns = doc.get_namespace("jumpdrive/readiness")
    assert ns is not None and ns.external is True
    assert app.core.discovery.kind_of("jumpdrive/readiness") == \
        "subscribed-external"
    assert app.core.discovery.is_wired("jumpdrive/readiness")


def test_c6_drag_gesture_rail_to_port(app):
    """The REAL pointer path: down on a rail row, pan over the widget
    node (which expands its ports), release on the value port."""
    _build_console(app.core)
    _ext_publish(app.core, "jumpdrive/readiness", "charging", "esp32-rig")
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    flow = app.ui.flow
    row = next(rect for rect, e in flow._rail_rows
               if e.name == "jumpdrive/readiness")
    sx, sy = row.x + 10, row.y + 10
    flow.on_pointer_down(sx, sy)
    node = next(n for n in flow._nodes
                if n.node_id == "w:jump_readiness")
    # first pan: hover the widget node -> it expands to show all ports
    hx = flow._gx(node.rect.x) + 30
    hy = flow._gy(node.rect.y) + 20
    flow.on_pan(hx - sx, hy - sy)
    assert flow._hover_expand == "w:jump_readiness"
    # drop on the (expanded) value port
    px, py = flow._widget_port(node, "value", expanded=True)
    flow.on_pan(px + 6 - sx, py - sy)
    flow.on_pan_end()
    layer = app.core.document.get_layer("jump_readiness")
    assert layer.namespace == "jumpdrive/readiness"
    ns = app.core.document.get_namespace("jumpdrive/readiness")
    assert ns is not None and ns.external


def test_c6_drag_to_open_canvas_declares(app):
    """Rail drag released on empty graph = start listening (declare)."""
    _build_console(app.core)
    _ext_publish(app.core, "dradis/contact", {"n": 2}, "sim")
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    flow = app.ui.flow
    row = next(rect for rect, e in flow._rail_rows
               if e.name == "dradis/contact")
    sx, sy = row.x + 10, row.y + 10
    g = flow._graph_rect
    flow.on_pointer_down(sx, sy)
    flow.on_pan(g.x + g.w - 60 - sx, g.y + g.h - 60 - sy)
    flow.on_pan_end()
    ns = app.core.document.get_namespace("dradis/contact")
    assert ns is not None and ns.external
    assert app.core.discovery.kind_of("dradis/contact") == \
        "subscribed-external"


def test_c2_flow_wire_syncs_bindings_and_source(app, tmp_path):
    _build_console(app.core)
    _ext_publish(app.core, "jumpdrive/charge", 142, "esp32-rig")
    app.ui.flow.author_wire("jumpdrive/charge", "dradis", "opacity")
    app.render.drain_updates()
    # raw spec carries the simplest CXC-BIND form
    layer = app.core.document.get_layer("dradis")
    assert layer.properties["opacity"] == "jumpdrive/charge"
    # Bindings summary sees it
    rows = app.core.document.bindings()
    assert any(r["kind"] == "attr" and r["attr"] == "opacity"
               and r["namespace"] == "jumpdrive/charge" for r in rows)
    # Source projection sees it
    assert "opacity: jumpdrive/charge" in app.ui.source.text()
    # export round-trips byte-identical (raw-spec fidelity, C2/C7 of
    # BINDEDIT held under Flow authoring)
    out = tmp_path / "flow.compose.yaml"
    app.core.save(str(out))
    doc2 = CompositeDocument.from_yaml(out.read_text())
    assert doc2.to_yaml() == out.read_text()
    spec = yaml.safe_load(out.read_text())
    ns_specs = {n["name"]: n for n in spec["namespaces"]}
    assert ns_specs["jumpdrive/charge"]["external"] is True


def test_c2_bare_string_visible_binding_survives_widget_changed(app):
    """The FLOW-found fix: the picker/graph's simplest visible form must
    not be coerced to the design-time eye bool."""
    _build_console(app.core)
    app.core.internal_bus.publish_event(
        "composer/config/widget_changed", "do",
        data={"widget_id": "dradis", "property": "visible",
              "value": "jumpdrive/spool_temp"}, source="t")
    layer = app.core.document.get_layer("dradis")
    assert layer.properties["visible"] == "jumpdrive/spool_temp"
    assert layer.visible is True
    # while bools still drive the eye
    app.core.internal_bus.publish_event(
        "composer/config/widget_changed", "do",
        data={"widget_id": "dradis", "property": "visible", "value": False},
        source="t")
    layer = app.core.document.get_layer("dradis")
    assert layer.visible is False and "visible" not in layer.properties


def test_c2_unbind_from_inspector(app):
    _build_console(app.core)
    app.ui.flow.author_wire("jumpdrive/charge", "dradis", "opacity")
    app.ui.flow_inspector._unbind("dradis", "opacity")
    assert "opacity" not in app.core.document.get_layer("dradis").properties


# ---------------------------------------------------------------------------
# C7 — declare-expected
# ---------------------------------------------------------------------------

def test_c7_declare_expected_activates_on_first_publish(app):
    _build_console(app.core)
    flow = app.ui.flow
    flow.declare_expected("jumpdrive/spool_temp")
    disc = app.core.discovery
    assert disc.kind_of("jumpdrive/spool_temp") == "expected"
    ns = app.core.document.get_namespace("jumpdrive/spool_temp")
    assert ns is not None and ns.external is True
    # bind visible to it while silent
    flow.author_wire("jumpdrive/spool_temp", "dradis", "visible")
    app.render.drain_updates()
    app.render.render_frame()
    preview = [c for c in app.ui.workshop._preview.children
               if c.widget_id == "dradis"][0]
    # the rig powers on: FIRST publish activates the binding + the rail
    _ext_publish(app.core, "jumpdrive/spool_temp", "off", "esp32-rig")
    app.render.drain_updates()
    assert preview.visible is False        # truthy('off') -> hidden
    _ext_publish(app.core, "jumpdrive/spool_temp", "on", "esp32-rig")
    app.render.drain_updates()
    assert preview.visible is True
    assert disc.kind_of("jumpdrive/spool_temp") == "subscribed-external"


def test_c7_rail_listen_affordance_declares(app):
    flow = app.ui.flow
    flow._set_filter_text("jumpdrive/spool_temp")
    flow.declare_expected("jumpdrive/spool_temp")
    ns = app.core.document.get_namespace("jumpdrive/spool_temp")
    assert ns is not None and ns.external


# ---------------------------------------------------------------------------
# C8 at the composer/preview tier — pattern binding in a design
# ---------------------------------------------------------------------------

def test_c8_pattern_binding_in_preview(app):
    _build_console(app.core)
    ib = app.core.internal_bus
    ib.publish_event("composer/config/widget_changed", "do",
                     data={"widget_id": "dradis", "property": "opacity",
                           "value": "jumpdrive/*"}, source="t")
    app.render.drain_updates()
    app.render.render_frame()
    preview = [c for c in app.ui.workshop._preview.children
               if c.widget_id == "dradis"][0]
    _ext_publish(app.core, "jumpdrive/charge", 0.5, "rig")
    app.render.drain_updates()
    assert preview.opacity == pytest.approx(0.5)
    # a LATER family member drives it too
    _ext_publish(app.core, "jumpdrive/new_signal", 0.9, "rig")
    app.render.drain_updates()
    assert preview.opacity == pytest.approx(0.9)
    # the pattern reads back as a wire on a family hub node
    nodes, wires = build_graph(app.core)
    hub = next(n for n in nodes if n.node_id == "ns:jumpdrive/*")
    assert hub.pattern is True


# ---------------------------------------------------------------------------
# document/undo plumbing for the external flag
# ---------------------------------------------------------------------------

def test_external_flag_roundtrip_and_pre_flow_stability(app, tmp_path):
    doc = CompositeDocument()
    from composeio.composer.document import NamespaceDef
    doc.add_namespace(NamespaceDef(name="a/b", state_engine="binary"))
    doc.add_namespace(NamespaceDef(name="jump/*", state_engine="json",
                                   external=True))
    text = doc.to_yaml()
    doc2 = CompositeDocument.from_yaml(text)
    assert doc2.get_namespace("a/b").external is False
    assert doc2.get_namespace("jump/*").external is True
    assert doc2.to_yaml() == text
    # a pre-FLOW document (no external keys) emits none
    assert "external" not in CompositeDocument.from_yaml(
        CompositeDocument().to_yaml()).to_yaml()


def test_undo_restores_external_flag(app):
    ib = app.core.internal_bus
    ib.publish_event("composer/config/namespace_changed", "do",
                     data={"action": "created", "name": "jumpdrive/x",
                           "state_engine": "json", "external": True},
                     source="t")
    ib.publish_event("composer/config/namespace_changed", "do",
                     data={"action": "removed", "name": "jumpdrive/x"},
                     source="t")
    assert app.core.document.get_namespace("jumpdrive/x") is None
    app.core.undo()
    ns = app.core.document.get_namespace("jumpdrive/x")
    assert ns is not None and ns.external is True


def test_validate_counts_attr_bindings_as_use(app):
    _build_console(app.core)
    app.ui.flow.author_wire("jumpdrive/charge", "dradis", "opacity")
    errors, warnings = app.core.validate()
    assert not any("jumpdrive/charge" in w and "unused" in w
                   for w in warnings)


# ---------------------------------------------------------------------------
# pixel gate — the Flow chrome golden (deterministic scene)
# ---------------------------------------------------------------------------

def test_flow_chrome_golden(app):
    from .conftest import (REFERENCE_DIR, images_match, load_reference,
                           surface_to_array)
    _build_console(app.core)
    _ext_publish(app.core, "jumpdrive/readiness", "charging", "esp32-rig")
    _ext_publish(app.core, "jumpdrive/charge", 142, "esp32-rig")
    app.ui.flow.author_wire("jumpdrive/readiness", "jump_readiness",
                            "value")
    app.core.internal_bus.publish_state("composer/ww/mode", "flow",
                                        source="t")
    # neutral inspector: the connection card carries "first seen Ns ago"
    # (wall-clock text) — deselect so the golden is time-independent
    app.ui.flow._selected_wire = None
    app.core.internal_bus.publish_state("composer/flow/selected", {},
                                        source="t")
    app.render.drain_updates()
    app.render.render_frame()
    canvas = app.render.canvas
    actual = surface_to_array(canvas)
    path = REFERENCE_DIR / "flow_mode_chrome.png"
    if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
        canvas.save_png(str(path))
        pytest.skip(f"golden (re)generated: {path}")
    expected = load_reference("flow_mode_chrome")
    match, ratio = images_match(actual, expected, tolerance=2,
                                max_diff_ratio=0.005)
    assert match, f"flow chrome: {ratio * 100:.2f}% differ"
