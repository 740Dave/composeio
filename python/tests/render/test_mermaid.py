"""
[SPEC:CXC-IC] [CXC-IC-CANVAS] Mermaid Widget — compliance tests.

The composable canvas at its purest: an Intelligence emits mermaid TEXT on a
bus namespace (default ``thread/diagram``) and a subscribed widget renders it —
state on the bus, behavior in the widget, no model round-trip (IC-C7).

Mirrors the SvgWidget wiring tests (parser registration + bindable surface).
The LIVE mermaid.js rasterization is a BROWSER workload proven by opening
render/examples/mermaid_widget_demo.html; here we assert the bus-driven
BEHAVIOR (subscribe + re-render) and the self-contained web contract.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.widget import Rect
from composeio.render.widgets import MermaidWidget
from composeio.render.widgets.mermaid import (DEFAULT_NAMESPACE,
                                              MERMAID_VERSION, web_asset_path)
from composeio.render.engine.runtime import RenderApp
from composeio.render.engine.bindings import bindable_attrs_for


FLOW = "graph TD\n  A[Start] --> B{Ready?}\n  B -->|yes| C[Go]"
SEQ = "sequenceDiagram\n  David->>Architect: emit\n  Architect-->>Widget: render"


def _app(config: dict) -> RenderApp:
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def _widget(app: RenderApp, wid: str):
    return next(w for w in app.comp.root.iter_widgets()
               if w.widget_id == wid)


def _mermaid_config(**extra):
    node = {"type": "mermaid", "id": "g", "x": 0, "y": 0, "w": 440, "h": 320,
            **extra}
    return {
        "design": {"width": 480, "height": 380, "title": "mermaid"},
        "widgets": [{
            "type": "pixel_stack", "id": "scene", "width": 480, "height": 380,
            "background": "#0A0A10", "children": [node],
        }],
    }


# ===========================================================================
# Parser wiring (mirrors test_svg_widget_registered_in_parser)
# ===========================================================================

def test_mermaid_widget_registered_in_parser():
    app = _app(_mermaid_config())
    try:
        assert type(_widget(app, "g")).__name__ == "MermaidWidget"
    finally:
        app.stop()


def test_default_namespace_subscribed_without_declaration():
    """No `namespace:` in the spec, yet the widget is bus-live on
    thread/diagram — the parser defaults + subscribes it ([CXC-IC-CANVAS])."""
    app = _app(_mermaid_config())
    try:
        w = _widget(app, "g")
        assert w.namespace == DEFAULT_NAMESPACE == "thread/diagram"
        subs = [ns for ns, ww in app.comp.subscriptions if ww is w]
        assert "thread/diagram" in subs
    finally:
        app.stop()


# ===========================================================================
# IC-C7 — emit on the bus, it re-renders (no model round-trip)
# ===========================================================================

def test_emit_on_namespace_rerenders():
    app = _app(_mermaid_config(source=FLOW))
    try:
        w = _widget(app, "g")
        app.render_frame_at(0.0)             # Canvas tier must not raise
        assert w.source == FLOW
        app.publish("thread/diagram", SEQ)   # an Intelligence emits a NEW diagram
        app.render_frame_at(0.1)
        assert w.source == SEQ               # swapped, purely in the widget
    finally:
        app.stop()


def test_configurable_namespace_subscribed():
    app = _app(_mermaid_config(namespace="self/plan/diagram"))
    try:
        w = _widget(app, "g")
        assert w.namespace == "self/plan/diagram"
        subs = [ns for ns, ww in app.comp.subscriptions if ww is w]
        assert "self/plan/diagram" in subs
        app.publish("self/plan/diagram", FLOW)
        app.render_frame_at(0.0)
        assert w.source == FLOW
    finally:
        app.stop()


def test_empty_source_renders_placeholder_not_crash():
    app = _app(_mermaid_config())
    try:
        app.render_frame_at(0.0)             # no source -> placeholder, no raise
        assert _widget(app, "g").source == ""
    finally:
        app.stop()


# ===========================================================================
# Bindable surface (mirrors test_bindable_surface_registered_for_composer)
# ===========================================================================

def test_bindable_surface_registered_for_composer():
    attrs = bindable_attrs_for("mermaid")
    for a in ("source", "theme", "visible", "opacity"):
        assert a in attrs, a


def test_catalog_entry_present():
    from composeio.composer import catalog
    e = catalog.get("mermaid")
    assert e is not None
    assert e.defaults.get("namespace") == "thread/diagram"
    assert "mermaid.js" in e.description


# ===========================================================================
# Self-contained web tier — vendored bundle + self-mounting fragment
# ===========================================================================

def test_vendored_mermaid_present_no_cdn():
    path = web_asset_path()
    assert os.path.exists(path)
    data = open(path, "rb").read()
    assert len(data) > 1_000_000            # the real UMD bundle
    assert b'globalThis["mermaid"]' in data[-4000:]   # sets window.mermaid


def test_web_fragment_embeds_source_and_rerender_hook():
    w = MermaidWidget("g", source=FLOW)
    html = w.render_html()
    assert "window.mermaid.render" in html   # runs the vendored engine
    assert "graph TD" in html                 # source embedded
    assert "__composeMermaid" in html         # bus re-render hook exposed
    assert MERMAID_VERSION                     # version pinned/known


def test_web_fragment_escapes_break_out():
    tricky = 'graph LR\n  X["</script>"] --> Y'
    html = MermaidWidget("g", source=tricky).render_html()
    assert "<\\/" in html                      # </ escaped -> no fragment break-out
