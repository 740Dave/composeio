"""EmitButtonWidget — publishes a configured sequence of bus messages on tap.

Covers the dual-throttle audio-test button: a tap must fire the two-frame
`dradis/contacts` (unknown -> hostile) sequence IN ORDER so the DY-SV5F
cover's baseline-guarded condition_one edge trips.
"""

from composeio.render.widgets.emit_button import EmitButtonWidget
from composeio.render.engine.parser import parse_widget_tree


def _capture(widget):
    sent = []
    widget.publish = lambda ns, val: sent.append((ns, val))
    return sent


def test_tap_publishes_all_emits_in_order():
    eb = EmitButtonWidget("audio", label="Test", emits=[
        {"namespace": "dradis/contacts",
         "value": [{"id": "cal", "allegiance": "unknown"}]},
        {"namespace": "dradis/contacts",
         "value": [{"id": "cal", "allegiance": "hostile"}]},
    ])
    sent = _capture(eb)
    eb.on_tap(0, 0)
    assert len(sent) == 2
    assert sent[0] == ("dradis/contacts", [{"id": "cal", "allegiance": "unknown"}])
    assert sent[1] == ("dradis/contacts", [{"id": "cal", "allegiance": "hostile"}])


def test_tap_is_idempotent_in_shape_and_re_arms():
    """Every tap re-fires the full sequence (each press re-arms the edge)."""
    eb = EmitButtonWidget("x", emits=[
        {"namespace": "n", "value": 1},
        {"namespace": "n", "value": 2},
    ])
    sent = _capture(eb)
    eb.on_tap(0, 0)
    eb.on_tap(0, 0)
    assert sent == [("n", 1), ("n", 2), ("n", 1), ("n", 2)]


def test_malformed_emits_are_dropped_not_crashed():
    eb = EmitButtonWidget("x", emits=[
        {"value": 1},                 # no namespace -> dropped
        "not a dict",                 # wrong type -> dropped
        {"namespace": "ok", "value": 7},
        {"ns": "alias", "value": 8},  # 'ns' alias accepted
    ])
    sent = _capture(eb)
    eb.on_tap(0, 0)
    assert sent == [("ok", 7), ("alias", 8)]


def test_empty_emits_is_a_noop():
    eb = EmitButtonWidget("x")
    sent = _capture(eb)
    eb.on_tap(0, 0)
    assert sent == []


def test_depressed_state_follows_pointer_and_releases():
    """Depress on pointer-down, RELEASE on pointer-up (never sticks)."""
    eb = EmitButtonWidget("x", emits=[{"namespace": "n", "value": 1}])
    eb.publish = lambda ns, val: None
    assert eb._pressed is False
    eb.on_pointer_down(0, 0)
    assert eb._pressed is True           # depressed while held
    eb.on_pointer_up(0, 0)
    assert eb._pressed is False          # pops back up on release


def test_authorable_in_compose_yaml():
    """`type: emit_button` builds an EmitButtonWidget, not a placeholder."""
    comp = parse_widget_tree({
        "widgets": [{
            "type": "emit_button", "id": "audio", "label": "Go",
            "emits": [{"namespace": "n", "value": 1}],
        }],
    })
    found = []

    def walk(w):
        found.append(w)
        for c in (getattr(w, "children", None) or []):
            walk(c)

    root = getattr(comp, "root", None)
    if root is not None:
        walk(root)
    names = {type(w).__name__ for w in found}
    assert "EmitButtonWidget" in names
    assert "PlaceholderWidget" not in names
