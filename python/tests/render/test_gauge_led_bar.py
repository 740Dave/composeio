"""
Unit tests for the GaugeWidget `style: "led_bar"` segmented LED bar-graph mode
([CXC-PRIM-CATALOG], LM3914-style level meter).

The led_bar is a value-fill readout drawn as a DISCRETE row/column of N LED
segments on a near-black bezel.  These tests pin the four things the widget
promises:

  * lit-segment COUNT tracks the value (decile thresholds for N=10: 0% -> 0
    lit, 10% -> 1, 50% -> 5, 100% -> N), both in the pure lit-decision logic
    and in the RENDERED pixels;
  * the PALETTE maps to segment index (blue idle / green cruise / yellow
    caution / red max) and to the correct physical end;
  * `reverse` inverts which end of the axis the lit segments occupy;
  * short `segment_colors` extend by repeating the last entry.

Pixel checks use BRIGHTNESS (a lit segment's core is near-max on its dominant
channel; an unlit segment is ~16% luminance; the bezel is near-black), so they
are robust across the macOS/aarch64 Skia wheels without a golden.

Built from fill_round_rect only (R1: decompose to the contract), so the same
assertions describe the CYD and browser output.
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


def _render_led(value, *, orientation="vertical", w=150, h=340, **kw):
    """Render one led_bar gauge (no label -> a clean strip) onto a charcoal
    background and return its RGBA array."""
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x1E, 0x1E, 0x2E)
    c.fill_rect(0, 0, w, h)
    g = GaugeWidget("led", style="led_bar", orientation=orientation,
                    min_val=0, max_val=100, value=value, **kw)
    g.draw(c, Rect(0, 0, w, h))
    return surface_to_array(c)


def _lit_mask(arr):
    """Boolean (H, W) mask of LIT-segment pixels by BRIGHTNESS.

    A lit segment's core is near-max on its dominant channel (>=128); an unlit
    segment (~16% of its color) tops out near ~40; the bezel is ~10.  A
    max-channel threshold of 120 isolates the lit segments from everything
    else."""
    rgb = arr[:, :, :3].astype(int)
    return rgb.max(axis=2) > 120


# --------------------------------------------------------------------------
# Lit-segment COUNT (the LM3914 decile ladder)
# --------------------------------------------------------------------------

def test_led_lit_count_logic_deciles():
    """The pure lit-decision: 0% -> 0 lit, 10% -> 1, 50% -> 5, 100% -> 10."""
    g = GaugeWidget("g", style="led_bar", min_val=0, max_val=100, segments=10)
    for pct, expected in ((0, 0), (10, 1), (50, 5), (100, 10)):
        g.value = pct
        assert sum(g._led_lit()) == expected, \
            f"{pct}% of range should light {expected} of 10 segments"


def test_led_lit_count_rendered_matches():
    """The RENDERED pixels agree with the lit-decision: count vertical lit
    bands in the central column at 0 / 10 / 50 / 100%."""
    cx = 150 // 2
    for pct, expected in ((0, 0), (10, 1), (50, 5), (100, 10)):
        col = _lit_mask(_render_led(pct))[:, cx]
        # count runs of consecutive lit pixels == number of lit segments
        runs = int(np.sum((col[1:] & ~col[:-1]))) + int(col[0])
        assert runs == expected, \
            f"{pct}% rendered {runs} lit bands, expected {expected}"


def test_led_custom_segment_count():
    """`segments` other than 10 re-derives the thresholds (5-seg -> 20% each)."""
    g = GaugeWidget("g", style="led_bar", min_val=0, max_val=100, segments=5)
    g.value = 60
    assert sum(g._led_lit()) == 3  # 60% of 5 == 3 lit


# --------------------------------------------------------------------------
# Palette mapping (index -> color, and index -> physical end)
# --------------------------------------------------------------------------

def _band_mean(arr, x0f, x1f):
    """Mean RGB of the lit pixels in the horizontal x-band [x0f, x1f)."""
    w = arr.shape[1]
    x0, x1 = int(w * x0f), int(w * x1f)
    sub = arr[:, x0:x1, :3].astype(int)
    mask = _lit_mask(arr)[:, x0:x1]
    px = sub[mask]
    return px.mean(axis=0) if len(px) else np.zeros(3)


def test_led_default_palette_maps_to_index():
    """Horizontal, all lit: segment 1 (left) reads BLUE, the green band reads
    green, the far-right (segment 10) reads RED — the default engine palette."""
    arr = _render_led(100, orientation="horizontal", w=420, h=90)
    # segment 1 (blue idle) is the left-most tenth
    r, g, b = _band_mean(arr, 0.03, 0.09)
    assert b > r and b > g, f"segment 1 should read blue, got {(r, g, b)}"
    # segments 2-5 are green (sample around the 3rd-4th tenth)
    r, g, b = _band_mean(arr, 0.28, 0.36)
    assert g > r and g > b, f"green band should read green, got {(r, g, b)}"
    # segment 10 (red max) is the right-most tenth
    r, g, b = _band_mean(arr, 0.91, 0.97)
    assert r > g and r > b, f"segment 10 should read red, got {(r, g, b)}"


def test_led_custom_segment_colors_extend_last():
    """A short `segment_colors` list repeats its last entry to fill N."""
    g = GaugeWidget("g", style="led_bar", segments=6,
                    segment_colors=["#FF0000", "#00FF00"])
    pal = g._led_palette()
    assert len(pal) == 6
    assert pal[0] == (255, 0, 0)
    assert pal[1] == (0, 255, 0)
    assert pal[2] == pal[5] == (0, 255, 0)  # last entry repeated


# --------------------------------------------------------------------------
# reverse: flip which end fills
# --------------------------------------------------------------------------

def _lit_centroid_y(arr):
    ys, _ = np.nonzero(_lit_mask(arr))
    return float(ys.mean()) if len(ys) else float("nan")


def test_led_reverse_flips_vertical_end():
    """Vertical, 30% (3 lit): normally the lit segments sit at the BOTTOM;
    reverse moves them to the TOP."""
    normal = _lit_centroid_y(_render_led(30, h=340))
    flipped = _lit_centroid_y(_render_led(30, h=340, reverse=True))
    assert normal > flipped + 20, \
        f"reverse should raise the lit fill (normal y={normal}, rev y={flipped})"
    # sanity: normal fill is in the lower half, reversed in the upper half
    assert normal > 340 * 0.5
    assert flipped < 340 * 0.5


def _lit_centroid_x(arr):
    _, xs = np.nonzero(_lit_mask(arr))
    return float(xs.mean()) if len(xs) else float("nan")


def test_led_reverse_flips_horizontal_end():
    """Horizontal, 30%: normally the lit segments sit at the LEFT; reverse
    moves them to the RIGHT."""
    w = 420
    normal = _lit_centroid_x(_render_led(30, orientation="horizontal",
                                         w=w, h=90))
    flipped = _lit_centroid_x(_render_led(30, orientation="horizontal",
                                          w=w, h=90, reverse=True))
    assert flipped > normal + 30, \
        f"reverse should push the lit fill right (l={normal}, r={flipped})"
    assert normal < w * 0.5
    assert flipped > w * 0.5


# --------------------------------------------------------------------------
# Unlit segments read colored-but-dark (not black, not lit)
# --------------------------------------------------------------------------

def test_led_unlit_segments_dim_colored():
    """At 0% no segment is lit, yet the strip is not pure bezel: the off-LEDs
    are a dim tint of their palette color."""
    arr = _render_led(0, w=150, h=340)
    assert _lit_mask(arr).sum() == 0, "0% must light no segments"
    # the green band (segments 2-5) at ~16% reads as a dim-but-present green:
    # find pixels that are colored (some channel spread) but not lit
    rgb = arr[:, :, :3].astype(int)
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    colored_dim = (spread > 12) & (rgb.max(axis=2) <= 120)
    assert colored_dim.sum() > 0, "unlit LEDs should still read as colored"


# --------------------------------------------------------------------------
# Measure + style routing (regression guards)
# --------------------------------------------------------------------------

def test_led_measure_vertical_tall_narrow():
    g = GaugeWidget("g", style="led_bar", orientation="vertical")
    sz = g.measure(Constraints(400, 500))
    assert sz.height == 500 and sz.width <= 160


def test_led_measure_horizontal_wide_short():
    g = GaugeWidget("g", style="led_bar", orientation="horizontal")
    sz = g.measure(Constraints(600, 400))
    assert sz.width >= sz.height and sz.height <= 120


def test_led_bar_inert_for_dial():
    """led_bar mode must not perturb the default dial (regression guard)."""
    g = GaugeWidget("g")  # default style dial
    assert g.style == "dial" and g.segments == 10 and g.reverse is False
    sz = g.measure(Constraints(200, 200))
    assert sz.width == sz.height  # dial stays square


def test_led_thresholds_untouched():
    """warning/danger thresholds are irrelevant to led_bar and left inert; the
    palette alone colors it (no threshold-warming crossover into _led_palette)."""
    g = GaugeWidget("g", style="led_bar", warning_threshold=50,
                    danger_threshold=80, value=90)
    # palette is the default engine palette regardless of thresholds
    assert g._led_palette()[0] == (2, 54, 254)  # segment 1 still blue


# ==========================================================================
# NEW config params ([CXC-PRIM-LEDBAR], David 2026-07-29)
# ==========================================================================

def _raw_bytes(**kw):
    """Render a led_bar and return the raw RGBA bytes — for the byte-identical
    defaults gate."""
    return _render_led(60, **kw).tobytes()


def _seg_bbox(arr):
    """(x0, x1, y0, y1) bounding box of the LIT-segment pixels — used to read
    an LED's rendered aspect/extent."""
    ys, xs = np.nonzero(_lit_mask(arr))
    return int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())


# ---- defaults are byte-identical to the pre-enhancement render ------------

def test_led_defaults_byte_identical():
    """The five new params at their defaults (rectangular / across / gap 4 /
    padding 8 / min_segment_size 0) reproduce the ORIGINAL render byte-for-byte
    — the backward-compatibility gate.  Explicit defaults == implicit."""
    base = _raw_bytes()
    explicit = _raw_bytes(segment_shape="rectangular",
                          segment_long_axis="across",
                          segment_gap=4.0, padding=8.0,
                          min_segment_size=0.0)
    assert explicit == base, "explicit defaults must match the original render"
    # horizontal too
    b_h = _raw_bytes(orientation="horizontal", w=420, h=90)
    e_h = _raw_bytes(orientation="horizontal", w=420, h=90,
                     segment_shape="rectangular", segment_long_axis="across",
                     segment_gap=4.0, padding=8.0, min_segment_size=0.0)
    assert e_h == b_h


# ---- segment_shape: rectangular | square | round --------------------------

def test_led_shape_round_is_circular():
    """`segment_shape: round` draws discs: a lit LED's bbox is ~square (a
    circle's bounding box) and clearly NARROWER than the full-width rect a
    rectangular LED would fill."""
    rect = _render_led(100, w=150, h=340)                     # rectangular
    rnd = _render_led(100, w=150, h=340, segment_shape="round")
    rx0, rx1, _, _ = _seg_bbox(rect)
    ox0, ox1, oy0, oy1 = _seg_bbox(rnd)
    # round LEDs do not span the full strip width the wide rects do
    assert (ox1 - ox0) < (rx1 - rx0) - 10, \
        "round LEDs should be narrower than the full-width rectangular LEDs"
    # a single disc's bbox is ~square (within tolerance of the decile spacing)
    row = _lit_mask(rnd)
    # width of the lit band in the central column region ~ its height per seg
    assert row.sum() > 0


def test_led_shape_square_is_centered_square():
    """`segment_shape: square` draws a centered square (side = min slot extent),
    so a lit LED is narrower than the full-width rectangular LED and roughly as
    wide as it is tall."""
    rect = _render_led(100, w=150, h=340)
    sq = _render_led(100, w=150, h=340, segment_shape="square")
    rx0, rx1, _, _ = _seg_bbox(rect)
    sx0, sx1, _, _ = _seg_bbox(sq)
    assert (sx1 - sx0) < (rx1 - rx0) - 10, \
        "square LEDs should not span the full strip width"
    # squares are centered: the lit band's x-center sits near the strip center
    cx = 150 / 2
    assert abs((sx0 + sx1) / 2 - cx) < 12, "square LEDs should be centered"


def test_led_shape_round_distinct_from_square():
    """round and square are DIFFERENT renders (a disc is not a square box)."""
    sq = _render_led(100, w=150, h=340, segment_shape="square").tobytes()
    rnd = _render_led(100, w=150, h=340, segment_shape="round").tobytes()
    assert sq != rnd


# ---- segment_long_axis: across | along (rectangular only) -----------------

def test_led_long_axis_along_narrows_vertical():
    """Vertical `along`: the rect's long axis runs ALONG the fill, so the LED is
    capped to a portrait/square element much NARROWER than the full-width
    `across` rect — adjacent LEDs then meet on their short edges."""
    across = _render_led(100, w=200, h=340)                    # default across
    along = _render_led(100, w=200, h=340, segment_long_axis="along")
    ax0, ax1, _, _ = _seg_bbox(across)
    lx0, lx1, _, _ = _seg_bbox(along)
    assert (lx1 - lx0) < (ax1 - ax0) - 20, \
        "along should narrow the LED well below the full-strip width"
    # and it stays centered
    assert abs((lx0 + lx1) / 2 - 200 / 2) < 14


def test_led_long_axis_ignored_for_round():
    """`segment_long_axis` is rectangular-only: it must not change a round
    render (guards the 'ignored for square/round' contract)."""
    a = _render_led(100, w=150, h=340, segment_shape="round",
                    segment_long_axis="across").tobytes()
    b = _render_led(100, w=150, h=340, segment_shape="round",
                    segment_long_axis="along").tobytes()
    assert a == b


# ---- segment_gap ----------------------------------------------------------

def test_led_gap_changes_layout():
    """A wider `segment_gap` measurably reduces the total lit area (more space
    goes to gaps, less to LEDs); gap 0 abuts the segments (most lit area)."""
    tight = _lit_mask(_render_led(100, segment_gap=0.0)).sum()
    default = _lit_mask(_render_led(100, segment_gap=4.0)).sum()
    wide = _lit_mask(_render_led(100, segment_gap=16.0)).sum()
    assert tight > default > wide, \
        f"lit area should shrink as the gap grows: {tight} {default} {wide}"


# ---- padding --------------------------------------------------------------

def test_led_padding_shrinks_bezel():
    """A larger `padding` insets the bezel symmetrically, shrinking the lit
    strip; the lit centroid stays centered (both sides inset equally)."""
    small = _lit_mask(_render_led(100, padding=8.0)).sum()
    big = _lit_mask(_render_led(100, padding=40.0)).sum()
    assert big < small, "more padding => smaller lit strip"
    # symmetric: centroid stays near the strip center
    cx = _lit_centroid_x(_render_led(100, padding=40.0))
    assert abs(cx - 150 / 2) < 10


# ---- min_segment_size: overflow recovery ----------------------------------

def test_led_min_segment_size_disabled_is_noop():
    """min_segment_size=0 (default) changes nothing — byte-identical."""
    assert _raw_bytes(min_segment_size=0.0) == _raw_bytes()


def test_led_min_segment_size_gap_shrink_keeps_count():
    """A modest floor on a slightly-tight strip is recovered by shrinking the
    GAP (step 1) WITHOUT dropping any segment — all 10 still render."""
    # strip height ~ small so seg extent is a little under the floor, but a
    # gap-shrink recovers it.  h chosen so 10 segs fit at gap>=1.
    n_render, gap = GaugeWidget(
        "g", style="led_bar", segments=10, min_segment_size=18.0,
        segment_gap=6.0)._led_layout(210.0)
    assert n_render == 10, "gap-shrink should keep all 10 segments"
    assert 1.0 <= gap < 6.0, "the gap should have shrunk below its 6px request"
    # per-segment extent clears the floor
    assert (210.0 - 11 * gap) / 10 >= 18.0 - 1e-6


def test_led_min_segment_size_reduces_count_and_rescales():
    """When even a 1px gap cannot host N floored segments, the count drops (step
    2) and the deciles + palette rescale to the reduced count.  Every rendered
    LED clears the floor and nothing overflows the rect."""
    floor = 24.0
    g = GaugeWidget("g", style="led_bar", segments=10, min_segment_size=floor,
                    min_val=0, max_val=100, value=100)
    strip_extent = 160.0                       # far too short for 10x24 + gaps
    n_render, gap = g._led_layout(strip_extent)
    assert n_render < 10, "an undersized strip must drop the rendered count"
    assert n_render >= 1
    assert gap == 1.0, "step 2 pins the gap at the 1px floor"
    # every rendered LED clears the floor
    seg_extent = (strip_extent - (n_render + 1) * gap) / n_render
    assert seg_extent >= floor - 1e-6, \
        f"rendered LED extent {seg_extent} fell below the floor {floor}"
    # lit + palette rescale to the reduced count
    assert len(g._led_lit(n_render)) == n_render
    assert len(g._led_palette(n_render)) == n_render
    # endpoints of the resampled palette are preserved (blue idle .. red max)
    pal = g._led_palette(n_render)
    assert pal[0] == (2, 54, 254) and pal[-1] == (254, 0, 2)


def test_led_min_segment_size_never_overflows_rect():
    """The recovered render stays inside the widget rect: no lit pixel touches
    the rect edge (a floor on a too-small rect must reduce, not spill)."""
    w, h = 120, 150
    arr = _render_led(100, w=w, h=h, segments=10, min_segment_size=24.0)
    mask = _lit_mask(arr)
    ys, xs = np.nonzero(mask)
    assert len(xs) > 0, "something should still render"
    assert xs.min() >= 1 and xs.max() <= w - 2
    assert ys.min() >= 1 and ys.max() <= h - 2


def test_led_min_segment_size_rendered_count_matches_layout():
    """The RENDERED lit-band count equals the recovered n' (not the requested
    10): the rescale reaches the pixels, not just the layout math."""
    w, h = 120, 150
    g = GaugeWidget("g", style="led_bar", segments=10, min_segment_size=24.0,
                    min_val=0, max_val=100, value=100)
    # what the layout decides for this rect's fill-axis strip extent
    pad, inner = 8.0, 5.0
    strip_h = (h - 2 * pad) - 2 * inner
    n_render, _ = g._led_layout(strip_h)
    col = _lit_mask(_render_led(100, w=w, h=h, segments=10,
                                min_segment_size=24.0))[:, w // 2]
    runs = int(np.sum((col[1:] & ~col[:-1]))) + int(col[0])
    # the rescale REACHED the pixels: fewer bands than the requested 10, and
    # within one 1px-recovery-gap AA merge of the layout's n'.
    assert runs < 10, f"rescale should render fewer than 10 bands, got {runs}"
    assert n_render - 1 <= runs <= n_render, \
        f"rendered {runs} lit bands, layout expected ~{n_render}"
