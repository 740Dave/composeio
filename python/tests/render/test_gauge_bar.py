"""
Unit tests for the GaugeWidget `style: "bar"` linear fill / fader mode
([CXC-PRIM-CATALOG]).

The bar is a value-fill readout: an inset track the bound value fills as a
fraction of [min_val, max_val], bottom->top (vertical, the throttle-lever /
VU look) or left->right (horizontal).  These tests pin the three things the
widget promises:

  * fill FRACTION tracks the value (monotonic; empty at min; ~half at mid);
  * ORIENTATION + origin (vertical grows up from the track floor; horizontal
    grows right from the left);
  * NAMESPACE binding (the calib scene's two bars follow throttle/left|right).

Pixel checks use hue detection (the fill is far brighter than the dark inset
track), so they are robust across the macOS/aarch64 Skia wheels without a
golden.
"""

from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import GaugeWidget

from .conftest import surface_to_array

_CALIB = os.path.join(PY_ROOT, "composeio", "render", "examples",
                      "calib_throttle.compose.yaml")


def _render_bar(value, *, orientation="vertical", w=150, h=340, **kw):
    """Render one bar gauge (no label -> a clean track) and return its RGBA
    array plus the canvas."""
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x1E, 0x1E, 0x2E)
    c.fill_rect(0, 0, w, h)
    g = GaugeWidget("bar", style="bar", orientation=orientation,
                    min_val=0, max_val=255, value=value, **kw)
    g.draw(c, Rect(0, 0, w, h))
    return surface_to_array(c)


def _fill_mask(arr):
    """Boolean (H, W) mask of 'fill' pixels by SATURATION (max-min channel).

    The value fill is saturated teal/amber/red (>=128 spread); every piece of
    chrome that is *bright* but grey — the dim label, scale ticks, and end
    labels (all `_DIM` (102,102,120), spread 18) — is NOT saturated, so this
    isolates the fill from the chrome that a pure-brightness test would catch.
    Note the colored value READOUT is also saturated: callers exclude it by
    slicing to the track band (it is drawn below the track)."""
    rgb = arr[:, :, :3].astype(int)
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    return spread > 40


# Vertical bar geometry: the readout sits below `rect.h - 40` (readout_h 30 +
# pad 10, independent of the label); slicing there keeps only the track.
def _track_band_v(h):
    return h - 40


# --------------------------------------------------------------------------
# Fill fraction
# --------------------------------------------------------------------------

def test_bar_fill_fraction_monotonic():
    """Fill area grows monotonically with the bound value; empty at min."""
    yb = _track_band_v(340)
    counts = []
    for v in (0, 64, 128, 192, 255):
        mask = _fill_mask(_render_bar(v))[:yb]
        counts.append(int(mask.sum()))
    assert counts[0] == 0, "value at min_val must show no fill"
    assert counts == sorted(counts), f"fill must be monotonic, got {counts}"
    assert counts[-1] > counts[1], "full fill must exceed a quarter fill"


def test_bar_fill_fraction_half_at_midpoint():
    """A half-scale value fills ~half the track (within AA tolerance)."""
    cx = 150 // 2
    yb = _track_band_v(340)
    full = _fill_mask(_render_bar(255))[:yb, cx].sum()
    half = _fill_mask(_render_bar(128))[:yb, cx].sum()
    ratio = half / full
    assert 0.44 <= ratio <= 0.56, f"128/255 fill ratio {ratio:.3f} != ~0.5"


# --------------------------------------------------------------------------
# Orientation + origin
# --------------------------------------------------------------------------

def test_bar_vertical_fills_bottom_up():
    """Vertical fill is anchored at the track FLOOR and grows upward: the
    fill's bottom edge is the same for any positive value, while its top edge
    rises as the value grows."""
    cx = 150 // 2
    yb = _track_band_v(340)
    col64 = np.flatnonzero(_fill_mask(_render_bar(64))[:yb, cx])
    col200 = np.flatnonzero(_fill_mask(_render_bar(200))[:yb, cx])
    # bottom (max y) is anchored at the track floor for both
    assert abs(int(col64.max()) - int(col200.max())) <= 2
    # the larger value's fill starts higher up (smaller min y)
    assert int(col200.min()) < int(col64.min())
    # and the fill sits in the LOWER half of the surface for a quarter value
    assert int(col64.min()) > 340 * 0.4


def test_bar_horizontal_fills_left_right():
    """Horizontal fill is anchored at the LEFT and grows rightward."""
    arr128 = _render_bar(128, orientation="horizontal", w=360, h=90)
    arr255 = _render_bar(255, orientation="horizontal", w=360, h=90)
    # scan a row INSIDE the track (no label -> track_y 10, track_h 22 -> ~y21);
    # the right-aligned readout is drawn BELOW the track, so a mid-track row is
    # pure fill.
    cy = 20
    row128 = np.flatnonzero(_fill_mask(arr128)[cy, :])
    row255 = np.flatnonzero(_fill_mask(arr255)[cy, :])
    # both start near the left edge
    assert abs(int(row128.min()) - int(row255.min())) <= 3
    # the larger value reaches farther right
    assert int(row255.max()) > int(row128.max())


# --------------------------------------------------------------------------
# Threshold warming (accent -> warn -> crit)
# --------------------------------------------------------------------------

def _fill_hue(value, **kw):
    """Mean RGB of the fill pixels for a value with warn=180/danger=230."""
    arr = _render_bar(value, warning_threshold=180, danger_threshold=230)
    mask = _fill_mask(arr)
    px = arr[:, :, :3][mask].astype(int)
    return px.mean(axis=0)


def test_bar_threshold_color_warms():
    """Below warn the fill is teal (green+blue high, red low); past danger it
    is red-dominant."""
    r_ok, g_ok, b_ok = _fill_hue(100)
    assert g_ok > r_ok and b_ok > r_ok, "ok fill should read teal"
    r_hi, g_hi, b_hi = _fill_hue(250)
    assert r_hi > g_hi and r_hi > b_hi, "past-danger fill should read red"


# --------------------------------------------------------------------------
# Measure + style routing
# --------------------------------------------------------------------------

def test_bar_measure_vertical_tall_narrow():
    g = GaugeWidget("g", style="bar", orientation="vertical")
    sz = g.measure(Constraints(400, 500))
    assert sz.height == 500, "vertical bar fills the offered height"
    assert sz.width <= 160, "vertical bar stays narrow"


def test_bar_measure_horizontal_wide_short():
    g = GaugeWidget("g", style="bar", orientation="horizontal")
    sz = g.measure(Constraints(600, 400))
    assert sz.width >= sz.height, "horizontal bar is wider than tall"
    assert sz.height <= 120


def test_bar_readout_integer_when_whole():
    g = GaugeWidget("g", style="bar")
    g.value = 200.0
    assert g._readout_text() == "200"
    g.value = 200.5
    assert g._readout_text() == "200.5"


def test_style_dial_unchanged_when_not_bar():
    """The bar mode is inert for the default dial (regression guard)."""
    g = GaugeWidget("g")  # default style dial
    assert g.style == "dial"
    sz = g.measure(Constraints(200, 200))
    assert sz.width == sz.height  # dial is square


# --------------------------------------------------------------------------
# Namespace binding — the calib scene
# --------------------------------------------------------------------------

def _bars(app):
    out = []

    def walk(n):
        out.append(n)
        for c in getattr(n, "children", []) or []:
            walk(c)

    walk(app.comp.root)
    return {w.widget_id: w for w in out
            if getattr(w, "widget_type", None) == "gauge"}


def test_calib_scene_two_bars_bound():
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(_CALIB, windowed=False)
    app.start()
    try:
        assert app.comp.title == "Dual-Throttle · Calibration"
        bars = _bars(app)
        # v3: ONE readout per engine — the led_bar ENGINE telegraph.
        assert {"L_engine", "R_engine"} <= set(bars)
        for wid in ("L_engine", "R_engine"):
            g = bars[wid]
            assert g.style == "led_bar" and g.orientation == "vertical"
            # min/max are pinned to the continuous_0_100 engine domain (the
            # wire carries a PERCENTAGE since 2026-08-15): the scene maps the
            # wire value onto itself.  Any other pair would be a SECOND
            # conditioning stage on top of the leaf's.
            assert (g.min_val, g.max_val) == (0.0, 100.0)
        # drive the two pot namespaces; the bound engine bars follow
        app.bus.publish_state("throttle/left", 80)
        app.bus.publish_state("throttle/right", 25)
        app.drain_updates()
        assert bars["L_engine"].value == 80.0
        assert bars["R_engine"].value == 25.0
    finally:
        app.stop()


def test_calib_scene_raw_and_corr_bars_are_gone():
    """NEGATIVE CONTROL for the listen-only cut.

    The test above can only report SUCCESS — it passes just as happily with
    the deleted bars still present.  This one fails if they come back.

    RAW was bound to throttle/*/raw, which NOTHING on the bus publishes, so
    it rendered a value that never arrived; CORR duplicated the ENGINE
    binding.  Both the widgets AND the two dead namespace declarations must
    be absent, or the scene is subscribing to a stream with no producer.
    """
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(_CALIB, windowed=False)
    app.start()
    try:
        assert not ({"L_raw", "R_raw", "L_corrected", "R_corrected"}
                    & set(_bars(app)))
        declared = set(app.bus.namespaces)
        # POSITIVE CONTROL FIRST: if this set were empty (a renamed bus API,
        # say), every "not in" below would pass vacuously and prove nothing.
        assert {"throttle/left", "throttle/right"} <= declared
        assert "throttle/left/raw" not in declared
        assert "throttle/right/raw" not in declared
    finally:
        app.stop()


def test_calib_scene_no_initial_value_lights_nothing():
    """NO RETAINED VALUE -> NO LIT SEGMENT.

    These namespaces carry no retained value today (the C++ publisher uses
    PubSubClient's 2-arg publish(), retain=false), so a freshly-started scene
    has heard NOTHING.  The led_bar must not present that as a reading: at
    the default value zero deciles are reached, so every segment renders dim
    and the bar reads 'dark', not 'zero throttle with a lit floor segment'.
    """
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(_CALIB, windowed=False)
    app.start()
    try:
        for wid in ("L_engine", "R_engine"):
            g = _bars(app)[wid]
            assert not any(g._led_lit())     # zero segments illuminated
    finally:
        app.stop()


def test_calib_scene_fill_responds_to_bus():
    """End-to-end: a bus publish changes the RENDERED pixels, not just the attr.

    Measured on the LED bar: `_fill_mask` keys on saturation > 40, and an
    UNLIT segment is its hue times _LED_DIM_FACTOR (0.16), which lands at
    spread == 40 for every palette entry — so lit segments are counted and
    dim ones are not, with no golden image.
    """
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(_CALIB, windowed=False)
    app.start()
    try:
        left = _bars(app)["L_engine"]

        def lit_px():
            c = SkiaCanvas()
            c.create_surface(160, 360)
            c.set_fill_color(0x1E, 0x1E, 0x2E)
            c.fill_rect(0, 0, 160, 360)
            left.draw(c, Rect(0, 0, 160, 360))
            return int(_fill_mask(surface_to_array(c)).sum())

        app.bus.publish_state("throttle/left", 0)
        app.drain_updates()
        assert lit_px() == 0
        app.bus.publish_state("throttle/left", 86)
        app.drain_updates()
        assert lit_px() > 0
    finally:
        app.stop()
