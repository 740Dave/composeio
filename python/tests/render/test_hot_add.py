"""
Dashboard hot-add / live-subscribe ([CXC-ICP-REBIND]).

A widget dropped onto a RUNNING surface must bind its bus subscriptions
IMMEDIATELY and light up with live data — not stay blank until the next
launch.  ``RenderApp.add_widget_live`` parses the drop spec, wires ONLY the
newly-collected subscriptions / bindings / behaviors to the live bus, mounts
the child, and damages the region.  Proves:

  * a hot-added widget receives the RETAINED value at add time (live now, not
    blank-until-restart);
  * a subsequent publish reaches it (the subscription is genuinely live);
  * attr bindings on the added subtree wire too;
  * an ordered container keeps a hot-added child SHOWN under an active
    shown-set (append, not hide-by-omission);
  * add before start() is refused (needs a live mount context).
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp


def _dash(ctype="ordered_stack"):
    node = {"type": ctype, "id": "dashboard", "direction": "column",
            "align": "stretch", "gap": 8, "children": []}
    if ctype == "ordered_stack":
        node["state_namespace"] = "dash/order"
    return {"design": {"width": 400, "height": 400, "padding": 0, "gap": 0},
            "widgets": [node]}


def _app(scene):
    app = RenderApp.from_dict(scene, windowed=False)
    app.start()
    return app


def _by_id(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}


# --------------------------------------------------------------------------


def test_hot_add_delivers_retained_value_immediately():
    """The producer already published -> the dropped widget shows live data
    at add time, not blank-until-restart."""
    app = _app(_dash())
    app.publish("dash/sensor", 42.0)          # producer live BEFORE the drop
    dash = _by_id(app)["dashboard"]
    g = app.add_widget_live(dash, {
        "type": "gauge", "id": "g1", "namespace": "dash/sensor",
        "min_val": 0, "max_val": 100})
    assert g.widget_id == "g1"
    assert g in dash.children
    assert g.value == 42.0                     # lit up immediately


def test_hot_add_subscription_is_live():
    """A publish AFTER the drop reaches the widget (subscription wired live)."""
    app = _app(_dash())
    dash = _by_id(app)["dashboard"]
    g = app.add_widget_live(dash, {
        "type": "gauge", "id": "g2", "namespace": "dash/sensor2",
        "min_val": 0, "max_val": 100})
    assert g.value == 0.0                      # no producer yet
    app.publish("dash/sensor2", 77.0)
    app.render_frame()                         # drain the marshalled update
    assert g.value == 77.0


def test_hot_add_attr_binding_wires():
    """An attribute binding on the added subtree also binds live."""
    app = _app(_dash())
    dash = _by_id(app)["dashboard"]
    app.publish("dash/level", 63.0)
    g = app.add_widget_live(dash, {
        "type": "gauge", "id": "g3", "min_val": 0, "max_val": 100,
        "value": {"namespace": "dash/level"}})
    assert g.value == 63.0                     # applied at wire time
    app.publish("dash/level", 12.0)
    app.render_frame()
    assert g.value == 12.0


def test_hot_add_into_ordered_container_stays_shown():
    """Under an active shown-set the container appends the new child rather
    than hiding it by omission."""
    app = _app(_dash())
    dash = _by_id(app)["dashboard"]
    app.add_widget_live(dash, {"type": "rectangle", "id": "r1", "height": 20})
    # constrain the shown-set to the existing card only
    app.publish("dash/order", {"visible": ["r1"]})
    app.render_frame()
    g = app.add_widget_live(dash, {
        "type": "gauge", "id": "r2", "namespace": "dash/sensor3",
        "min_val": 0, "max_val": 100})
    app.render_frame()
    assert g.visible is True                    # kept shown, not hidden
    assert "r2" in dash._visible_only


def test_hot_add_before_start_refused():
    app = RenderApp.from_dict(_dash(), windowed=False)   # NOT started
    dash = next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == "dashboard")
    with pytest.raises(RuntimeError):
        app.add_widget_live(dash, {"type": "rectangle", "id": "x"})


def test_hot_add_marks_region_dirty():
    app = _app(_dash())
    app.render_frame()
    assert app._dirty is False
    dash = _by_id(app)["dashboard"]
    app.add_widget_live(dash, {"type": "rectangle", "id": "r9", "height": 20})
    assert app._dirty is True                   # parent region damaged
