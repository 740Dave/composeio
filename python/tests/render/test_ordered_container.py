"""
Bindable-order / visibility container ([SPEC:CXC-ORDER]).

The reusable primitive under any customizable-layout surface: a flex flow
whose child ORDER and SHOWN-SET are bound to a bus state namespace, so a
publish reorders / hides / shows the children and the container REFLOWS.
Proves:

  * default is byte-identical to a plain `stack` (pixel-level, no state) —
    including with NO `direction:` declared at all, so the two containers'
    defaults can never drift apart and silently flip a converted scene's axis;
  * a list state pure-reorders; a {order, hidden} / {visible} dict reorders
    AND hides; visibility is sticky (a bare reorder never un-hides);
  * `hidden` beats `visible` in a payload carrying both, independent of key
    order (NOT last-key-wins — key order is not a reliable bus carrier);
  * hidden children leave the flow (no space) and stop drawing/hit-testing,
    and clearing the shown-set restores them;
  * the declared child list is NEVER mutated (order is reversible);
  * the parser builds `ordered_stack` and subscribes its state namespaces.
"""

from __future__ import annotations

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.layout import OrderedFlowContainer
from composeio.render.engine.parser import parse_widget_tree
from composeio.render.engine.runtime import RenderApp

from .conftest import images_match, surface_to_array

GAP = 10.0
CARD_H = 40.0
CARD_W = 60.0


def _col_scene(ctype="ordered_stack", state_ns="dash/order"):
    node = {
        "type": ctype, "id": "rail", "direction": "column",
        "align": "stretch", "gap": GAP,
        "children": [
            {"type": "rectangle", "id": "a", "height": CARD_H,
             "fill_color": [200, 60, 60, 255]},
            {"type": "rectangle", "id": "b", "height": CARD_H,
             "fill_color": [60, 200, 60, 255]},
            {"type": "rectangle", "id": "c", "height": CARD_H,
             "fill_color": [60, 60, 200, 255]},
        ],
    }
    if ctype == "ordered_stack":
        node["state_namespace"] = state_ns
    return {"design": {"width": 300, "height": 400,
                       "padding": 0, "gap": 0}, "widgets": [node]}


def _axis_scene(ctype="ordered_stack", state_ns="dash/order"):
    """The SAME scene with NO ``direction:`` declared, so the layout axis comes
    purely from the container type's DEFAULT.  Fixed-size children and
    ``align`` left at its default so a row and a column are unmistakably
    different geometry (see test_no_direction_given_matches_stack_axis)."""
    node = {
        "type": ctype, "id": "rail", "gap": GAP,
        "children": [
            {"type": "rectangle", "id": "a", "width": CARD_W, "height": CARD_H,
             "fill_color": [200, 60, 60, 255]},
            {"type": "rectangle", "id": "b", "width": CARD_W, "height": CARD_H,
             "fill_color": [60, 200, 60, 255]},
            {"type": "rectangle", "id": "c", "width": CARD_W, "height": CARD_H,
             "fill_color": [60, 60, 200, 255]},
        ],
    }
    if ctype == "ordered_stack":
        node["state_namespace"] = state_ns
    return {"design": {"width": 300, "height": 400,
                       "padding": 0, "gap": 0}, "widgets": [node]}


def _app(scene):
    app = RenderApp.from_dict(copy.deepcopy(scene), windowed=False)
    app.start()
    return app


def _by_id(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}


def _laid_order(rail):
    laid = [(c.widget_id, c.arranged_rect) for c in rail.children
            if getattr(c, "visible", True) and c.arranged_rect is not None]
    laid.sort(key=lambda t: t[1].y)
    return [wid for wid, _ in laid]


# --------------------------------------------------------------------------


def test_default_layout_is_byte_identical_to_stack():
    """No state published -> ordered_stack renders pixel-for-pixel like the
    stack it subclasses (the zero-diff conversion guarantee)."""
    a_ord = _app(_col_scene("ordered_stack"))
    a_flx = _app(_col_scene("stack"))
    a_ord.render_frame()
    a_flx.render_frame()
    match, ratio = images_match(surface_to_array(a_ord.canvas),
                                surface_to_array(a_flx.canvas),
                                tolerance=0, max_diff_ratio=0.0)
    assert match, f"ordered_stack default diverged from stack: {ratio:.4%}"
    # and the arranged rects coincide exactly
    for wid in ("a", "b", "c"):
        ro = _by_id(a_ord)[wid].arranged_rect
        rf = _by_id(a_flx)[wid].arranged_rect
        assert (ro.x, ro.y, ro.w, ro.h) == (rf.x, rf.y, rf.w, rf.h)


def test_no_direction_given_matches_stack_axis():
    """[CXC-ORD-SUPERSET], the UNCONDITIONAL form: a `stack` and an
    `ordered_stack` with identical children and NO `direction:` declared must
    lay out identically — same AXIS, same rects, same pixels.

    This is the test whose absence let the two constructors' `direction`
    defaults drift apart (`ordered_stack` defaulted to `column`, `stack` to
    `row`), so a `direction`-less `stack` -> `ordered_stack` conversion would
    have silently flipped the layout axis.  Every caller in the repo happened
    to pass `direction:` explicitly, so nothing caught it.  Revert either
    default (`layout.py` `OrderedFlowContainer.__init__` or the parser's
    `spec.get("direction", ...)`) and this test fails on the very first
    assertion."""
    a_ord = _app(_axis_scene("ordered_stack"))
    a_flx = _app(_axis_scene("stack"))
    ord_rail = _by_id(a_ord)["rail"]
    flx_rail = _by_id(a_flx)["rail"]

    # 1. the AXIS itself — the thing that silently flipped
    assert ord_rail.direction == flx_rail.direction, (
        f"ordered_stack default axis {ord_rail.direction!r} != "
        f"stack default axis {flx_rail.direction!r} — a `direction`-less "
        f"stack->ordered_stack conversion would silently re-lay-out the scene")
    assert ord_rail.direction == "row"

    # 2. the children actually lie along that axis (row: x advances, y level)
    ro = [_by_id(a_ord)[wid].arranged_rect for wid in ("a", "b", "c")]
    assert [r.y for r in ro] == [ro[0].y] * 3
    assert ro[1].x > ro[0].x and ro[2].x > ro[1].x

    # 3. every rect coincides exactly with the plain stack's
    for wid in ("a", "b", "c"):
        r_o = _by_id(a_ord)[wid].arranged_rect
        r_f = _by_id(a_flx)[wid].arranged_rect
        assert (r_o.x, r_o.y, r_o.w, r_o.h) == (r_f.x, r_f.y, r_f.w, r_f.h), wid

    # 4. and it is zero-diff on glass
    a_ord.render_frame()
    a_flx.render_frame()
    match, ratio = images_match(surface_to_array(a_ord.canvas),
                                surface_to_array(a_flx.canvas),
                                tolerance=0, max_diff_ratio=0.0)
    assert match, (f"ordered_stack with no `direction:` diverged from stack: "
                   f"{ratio:.4%}")


def test_list_state_reorders():
    app = _app(_col_scene())
    rail = _by_id(app)["rail"]
    assert _laid_order(rail) == ["a", "b", "c"]
    app.publish("dash/order", ["c", "a", "b"])
    app.render_frame()
    assert _laid_order(rail) == ["c", "a", "b"]
    # a pure reorder never touches visibility
    assert all(_by_id(app)[w].visible for w in ("a", "b", "c"))


def test_dict_state_reorders_and_hides_and_compacts():
    app = _app(_col_scene())
    rail = _by_id(app)["rail"]
    app.publish("dash/order", {"order": ["a", "b", "c"], "hidden": ["b"]})
    app.render_frame()
    by = _by_id(app)
    assert by["b"].visible is False
    # b left the flow: a then c, and c moved UP into b's slot (compacted)
    assert _laid_order(rail) == ["a", "c"]
    ya = by["a"].arranged_rect.y
    yc = by["c"].arranged_rect.y
    assert abs(yc - (ya + CARD_H + GAP)) < 0.5, (ya, yc)


def test_visibility_is_sticky_until_changed():
    app = _app(_col_scene())
    app.publish("dash/order", {"hidden": ["b"]})
    app.render_frame()
    assert _by_id(app)["b"].visible is False
    # a bare reorder does NOT un-hide b (visibility is sticky)
    app.publish("dash/order", ["c", "b", "a"])
    app.render_frame()
    assert _by_id(app)["b"].visible is False
    # clearing the hidden set restores it
    app.publish("dash/order", {"hidden": []})
    app.render_frame()
    rail = _by_id(app)["rail"]
    assert _by_id(app)["b"].visible is True
    assert set(_laid_order(rail)) == {"a", "b", "c"}


def test_visible_allowlist_shows_only_listed():
    app = _app(_col_scene())
    app.publish("dash/order", {"visible": ["c", "a"]})
    app.render_frame()
    by = _by_id(app)
    assert by["b"].visible is False
    assert by["a"].visible and by["c"].visible
    assert _laid_order(by["rail"]) == ["a", "c"]


def test_hidden_beats_visible_regardless_of_key_order():
    """[CXC-ORD-ORDER-STATE]: when ONE payload carries both `hidden` and
    `visible`, `hidden` WINS — and the outcome does not depend on which key
    was written first.

    The two payloads below are key-order mirrors of each other.  Under the
    chosen rule they resolve IDENTICALLY (`hidden` applies last, discarding the
    allow-list).  Under a "last key wins" rule they would DIVERGE: the first
    payload would hide `c` while the second would show only `a`.  Asserting
    both mirrors agree is what pins the rule; asserting `b` stays visible is
    what proves it is `hidden` that won and not `visible`.

    Deliberately NOT last-key-wins: the payload crosses a JSON/YAML/MQTT
    boundary where mapping key order is not a guaranteed carrier, so hanging
    visibility on it would make the rendered result depend on serializer
    fidelity.  Each mirror runs on its OWN app because dict equality ignores
    key order, so republishing the mirror onto one app would be de-duped by
    `Namespace.publish_state` and never reach the container.
    """
    results = []
    for payload in ({"visible": ["a"], "hidden": ["c"]},
                    {"hidden": ["c"], "visible": ["a"]}):
        app = _app(_col_scene())
        app.publish("dash/order", payload)
        app.render_frame()
        by = _by_id(app)
        results.append({
            "laid": _laid_order(by["rail"]),
            "visible": {w: by[w].visible for w in ("a", "b", "c")},
        })

    assert results[0] == results[1], (
        f"hidden/visible precedence depends on key order: {results[0]} vs "
        f"{results[1]} — the rule must be order-independent")
    # `hidden` won: c is gone, and b — which the `visible: ["a"]` ALLOW-LIST
    # would have hidden — is still shown, proving the allow-list was discarded.
    assert results[0]["visible"] == {"a": True, "b": True, "c": False}
    assert results[0]["laid"] == ["a", "b"]


def test_separate_visibility_namespace():
    scene = _col_scene()
    scene["widgets"][0]["visibility_namespace"] = "dash/hidden"
    app = _app(scene)
    app.publish("dash/hidden", ["a"])
    app.render_frame()
    assert _by_id(app)["a"].visible is False
    # a {id: bool} shown-map also works
    app.publish("dash/hidden", {"a": True, "c": False})
    app.render_frame()
    by = _by_id(app)
    assert by["a"].visible is True and by["c"].visible is False


def test_declared_child_list_is_never_mutated():
    """Order is reversible: reorder + hide leave self.children untouched, so
    z-order is stable and clearing the state restores the original layout."""
    app = _app(_col_scene())
    rail = _by_id(app)["rail"]
    before = [c.widget_id for c in rail.children]
    app.publish("dash/order", {"order": ["c", "b", "a"], "hidden": ["a"]})
    app.render_frame()
    assert [c.widget_id for c in rail.children] == before == ["a", "b", "c"]
    app.publish("dash/order", None)          # clear order
    app.publish("dash/order", {"hidden": []})
    app.render_frame()
    assert _laid_order(rail) == ["a", "b", "c"]


def test_parser_builds_ordered_stack_and_subscribes():
    scene = _col_scene()
    scene["widgets"][0]["visibility_namespace"] = "dash/vis"
    comp = parse_widget_tree(copy.deepcopy(scene))
    rail = next(w for w in comp.root.iter_widgets()
                if w.widget_id == "rail")
    assert isinstance(rail, OrderedFlowContainer)
    assert rail.state_namespace == "dash/order"
    assert rail.visibility_namespace == "dash/vis"
    subbed = {ns for ns, w in comp.subscriptions if w is rail}
    assert {"dash/order", "dash/vis"} <= subbed


def test_reflow_requests_redraw_on_change():
    """A state change damages the container's region (scoped redraw), and a
    no-op republish of the SAME state does not."""
    app = _app(_col_scene())
    app.render_frame()                       # clear pending damage
    assert app._dirty is False
    app.publish("dash/order", ["b", "a", "c"])
    app.drain_updates()
    assert app._dirty is True                # reflow requested
    app.render_frame()
    app.publish("dash/order", ["b", "a", "c"])   # identical -> no change
    app.drain_updates()
    assert app._dirty is False
