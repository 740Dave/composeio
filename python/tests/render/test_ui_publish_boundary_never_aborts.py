"""A UI-callback publish of an OUT-OF-DOMAIN value must LOG-AND-DROP, never abort.

THE DEFECT THIS PINS ([CXC-IBUS-ENGINE-REJECT-UNIFORM], 0x70 ENGINE_VALUE_REJECTED).
The ComposeBus had TWO value-ingest BOUNDARIES that treated an out-of-domain value
differently:

  * the EXTERNAL transport boundary (`ComposeIOBus._dispatch_incoming`) already
    wrapped the engine publish in `try/except ValueError` -> log-and-drop (safe);
  * the RENDER UI boundary (`RenderApp.publish`, through which EVERY widget,
    gesture and interaction callback funnels) did NOT. A declared strict engine
    handed an out-of-domain value RAISED inside the callback; propagated into the
    native (SDL/Tk) event dispatch that on macOS becomes an NSException, it
    SIGABRTs the WHOLE surface.

The concrete trigger is not exotic: a `gestures:` entry that names a namespace
but omits `value` publishes the empty string `""` on tap (states.GesturePublish
default). `""` is the CONV-3 "no value" string for STRING namespaces — but on a
`binary` namespace (`['off','on']`) it is out-of-domain, so a single tap aborts
the process.

THE INVARIANT. No value-ingest BOUNDARY (external transport OR UI callback) may
let an engine-domain rejection escape into an uncatchable native context; every
boundary log-and-drops. The internal `bus.publish_state` API STILL raises for a
programmatic caller (that raise is catchable and is a real bug signal) — this
test pins that the fix did NOT over-reach and weaken declared-engine validation.

Lives under python/tests/render/ because that is the tree the pre-commit gate maps
for a `python/**.py` change (see test_bus_autocreate_engine.py's note).
"""
import sys
from pathlib import Path

import pytest

PY_ROOT = str(Path(__file__).resolve().parents[2])
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp  # noqa: E402
from composeio.render.engine.states import GesturePublish  # noqa: E402


_BINARY_NS = "device/relay"

# A minimal headless scene declaring a STRICT binary namespace and one tappable
# widget whose tap-gesture publishes to it.
_SCENE = {
    "design": {"width": 120, "height": 80, "title": "relay"},
    "namespaces": [
        {"name": _BINARY_NS, "state_engine": "binary"},
    ],
    "widgets": [{
        "type": "pixel_stack", "id": "root",
        "width": 120, "height": 80, "background": "#0A0E17",
        "children": [
            {"type": "label", "id": "lbl", "x": 8, "y": 8, "text": "relay"},
        ],
    }],
}


@pytest.fixture
def app():
    a = RenderApp.from_dict(_SCENE, windowed=False)
    a.start()
    yield a


# Every one of these is out-of-domain for a `binary` engine (`['off','on']`).
# `""` is the exact reported crash input; the others are the same class.
_OUT_OF_DOMAIN = ["", "maybe", 0.203, 1, None, {"x": 1}]


@pytest.mark.parametrize("bad", _OUT_OF_DOMAIN)
def test_app_publish_boundary_drops_not_aborts(app, bad):
    """RenderApp.publish (the UI->bus chokepoint) must not let the engine's
    ValueError escape — before the fix this raised and, in a live callback,
    SIGABRTed."""
    app.publish(_BINARY_NS, bad, source="ui/tap")   # must NOT raise
    # dropped: the namespace keeps its last good value (the binary default)
    assert app.bus.get_state(_BINARY_NS) == "off"


@pytest.mark.parametrize("bad", _OUT_OF_DOMAIN)
def test_gesture_publish_route_drops_not_aborts(app, bad):
    """The specifically-unguarded route: a `gestures:` producer publishing an
    out-of-domain literal via _fire_gesture_publishes. This is the tap-with-no-
    value == '' path that aborts on a binary namespace."""
    root = app.tree if hasattr(app, "tree") else None
    # Stub a widget carrying a tap-gesture publish of the bad value.
    class _W:
        widget_id = "relay_btn"
    w = _W()
    w._gesture_publishes = {"tap": [GesturePublish(namespace=_BINARY_NS, value=bad)]}
    app._fire_gesture_publishes(w, "tap", 1.0, 1.0)   # must NOT raise
    assert app.bus.get_state(_BINARY_NS) == "off"


def test_internal_bus_api_still_raises_for_programmatic_caller(app):
    """The fix must NOT weaken declared-engine validation for a programmatic
    caller: bus.publish_state still raises, because a raise there is catchable
    and is a real bug signal (guards against the over-reaching fix)."""
    with pytest.raises(ValueError):
        app.bus.publish_state(_BINARY_NS, "", source="module-code")
