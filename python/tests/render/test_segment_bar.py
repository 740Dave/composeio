"""
[SPEC:CXC-PRIM] [CXC-PRIM-SEGBAR] / [CXC-PRIM-C11] — the segment_bar
composite (repeat generator + one per-segment DSL expression, NO new
engine Widget class).

Drives the real artifact (examples/components/segment_bar.compose.yaml)
offscreen: publish a normalized 0..1 value, render one frame, and assert
the resolved per-segment `fill_color` — lit/dim and green/yellow/red zone
all from the single bound expression.  Modelled on test_bind_behaviors.py.
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
from composeio.render.engine.layout import FlexContainer

EXAMPLE = os.path.join(
    PY_ROOT, "composeio", "render", "examples", "components",
    "segment_bar.compose.yaml")

# The ratified semantic tokens ([CXC-PRIM-TOKENS]) as RGBA, the shape the
# rect stores after color coercion at the binding site.
GREEN = (0x4b, 0xb0, 0x7d, 255)
YELLOW = (0xd9, 0x9a, 0x34, 255)
RED = (0xdd, 0x5b, 0x52, 255)
DIM = (0x22, 0x30, 0x3c, 255)


def _app():
    app = RenderApp.from_file(EXAMPLE, windowed=False)
    app.start()
    return app


def _byid(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}


def _seg_colors(app, prefix, n=10):
    ws = _byid(app)
    return [ws[f"{prefix}_{i}"].fill_color for i in range(n)]


# ---------------------------------------------------------------------------
# The artifact expands to two bars of ten discrete segments each
# ---------------------------------------------------------------------------

def test_segment_bar_expands_to_ten_segments_per_instance():
    app = _app()
    try:
        ws = _byid(app)
        assert all(f"seg_{i}" in ws for i in range(10))     # horizontal bar
        assert all(f"v_{i}" in ws for i in range(10))       # vertical variant
        # each segment is a plain rect (composite, no new Widget class)
        assert type(ws["seg_0"]).__name__ == "RectangleWidget"
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# value 0.0 -> every segment dim
# ---------------------------------------------------------------------------

def test_value_zero_all_dim():
    app = _app()
    try:
        app.publish("audio/level", 0.0)
        app.render_frame()
        assert _seg_colors(app, "seg") == [DIM] * 10
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# value 0.65 -> first 6 lit (green zone), remainder dim
# ---------------------------------------------------------------------------

def test_value_065_lights_six_green_rest_dim():
    app = _app()
    try:
        app.publish("audio/level", 0.65)
        app.render_frame()
        colors = _seg_colors(app, "seg")
        # 0.65 * 10 = 6.5 -> segments 0..5 lit (>= i+1), all in the green
        # zone (i < green=6); segments 6..9 unlit.
        assert colors[:6] == [GREEN] * 6
        assert colors[6:] == [DIM] * 4
    finally:
        app.stop()


def test_lit_count_tracks_value_and_zones_are_correct():
    """As the value climbs into the yellow band, newly-lit segments show the
    yellow zone colour — lit/dim AND zone are one expression."""
    app = _app()
    try:
        app.publish("audio/level", 0.75)        # 7.5 -> 7 lit (0..6)
        app.render_frame()
        colors = _seg_colors(app, "seg")
        assert colors[:6] == [GREEN] * 6         # green zone
        assert colors[6] == YELLOW               # seg 6 now lit, yellow zone
        assert colors[7:] == [DIM] * 3
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# value 1.0 -> all lit; zoned 6 green / 2 yellow / 2 red
# ---------------------------------------------------------------------------

def test_value_full_all_lit_last_two_red():
    app = _app()
    try:
        app.publish("audio/level", 1.0)
        app.render_frame()
        colors = _seg_colors(app, "seg")
        assert colors[0:6] == [GREEN] * 6
        assert colors[6:8] == [YELLOW] * 2
        assert colors[8:10] == [RED] * 2          # red = N - green - yellow
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# Intelligence parity: a foreign publisher drives it identically
# ---------------------------------------------------------------------------

def test_any_publisher_drives_it():
    app = _app()
    try:
        app.bus.publish_state("audio/level", 1.0, source="intelligence-claude")
        app.render_frame()
        assert _seg_colors(app, "seg")[9] == RED
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# The vertical variant lays out in a column
# ---------------------------------------------------------------------------

def test_vertical_variant_is_a_column():
    app = _app()
    try:
        ws = _byid(app)
        hbar = ws["seg_bar"]
        vbar = ws["v_bar"]
        assert isinstance(hbar, FlexContainer) and hbar.direction == "row"
        assert isinstance(vbar, FlexContainer) and vbar.direction == "column"
        # and it still lights/zones identically on its own namespace
        app.publish("audio/level_v", 1.0)
        app.render_frame()
        assert _seg_colors(app, "v")[0] == GREEN
        assert _seg_colors(app, "v")[9] == RED
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# The segments actually stack along the main axis (real geometry)
# ---------------------------------------------------------------------------

def test_horizontal_segments_advance_in_x():
    app = _app()
    try:
        app.render_frame()
        ws = _byid(app)
        xs = [ws[f"seg_{i}"].arranged_rect.x for i in range(10)]
        assert xs == sorted(xs)                  # left-to-right
        assert xs[1] > xs[0]                      # distinct positions
        ys = [ws[f"seg_{i}"].arranged_rect.y for i in range(10)]
        assert len(set(round(y) for y in ys)) == 1   # same row
    finally:
        app.stop()
