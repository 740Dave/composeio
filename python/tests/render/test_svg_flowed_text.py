"""
SVG2 flowed-text (shape-inside) + Tiny5 font routing ([CXC-SVG-FLOW]).

David authors the Colonial-OS UI in Inkscape 1.4, which encodes wrapped
("flowed") text as SVG2 ``shape-inside:url(#id)`` — a <text> whose layout box
is a shape in <defs>.  The old renderer drew every <text> as ONE unwrapped
line at the tspan x/y (the CIC dialog-title clip).  These tests pin:

  * shape-inside resolves the referenced <rect> as the layout box, and text
    wraps to the box WIDTH via the deterministic ft_text integer-advance path
    (macOS<->RPi pixel-parity, [CXC-SVG-C1]);
  * per-line alignment left/center/right (text-align / text-anchor);
  * text past the box bottom is clipped (Inkscape flow-region semantics);
  * the plain single-line <text> path is UNCHANGED when shape-inside is absent
    (colonial_console chrome + all current SVG text depend on it);
  * font-family:Tiny5 (the Colonial-OS pixel font) routes end-to-end to the
    bundled Tiny5-Regular.ttf, additively — Plex stays the default;
  * a scene/app-scoped default_font_family fallback (BSG-only), explicit
    element font-family still winning.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.svg import SvgDocument, render_svg
from composeio.render.svg.canvas_renderer import _map_family
from composeio.render.fonts import (classify_family, font_file_name,
                                    resolve_font_path)
from composeio.render.ft_text import get_engine

from .conftest import surface_to_array

REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
GOLDEN_SVG = os.path.join(
    REPO_ROOT, "compose_projects", "bsg", "resources",
    "screen_base_layout", "test-flowed-text.svg")

# Box used by the synthetic fixtures below, in viewBox units == device px
# (rendered fit="stretch" into a same-size rect so coordinates map 1:1).
BOX = (20, 20, 120, 80)     # x, y, w, h


def _render(svg: str, w: int, h: int, fit: str = "stretch",
            default_font_family=None) -> np.ndarray:
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(255, 255, 255)
    c.fill_rect(0, 0, w, h)
    render_svg(c, SvgDocument.from_string(svg), Rect(0, 0, w, h), fit,
               default_font_family=default_font_family)
    c.present()
    return surface_to_array(c)


def _ink_mask(arr: np.ndarray, thresh: int = 128) -> np.ndarray:
    """Boolean mask of 'dark' (ink) pixels on the white background."""
    return arr[..., 0].astype(np.int32) < thresh


def _flow_fixture(align_style: str, text: str, *, font: str = "Helvetica",
                  fs: int = 14) -> str:
    bx, by, bw, bh = BOX
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120" '
        f'viewBox="0 0 200 120">'
        f'<defs><rect id="box" x="{bx}" y="{by}" width="{bw}" height="{bh}"/>'
        f'</defs>'
        f'<text style="font-size:{fs}px;font-family:{font};{align_style};'
        f'shape-inside:url(#box);fill:#000000;">'
        f'<tspan x="{bx}" y="{by + fs}"><tspan>{text}</tspan></tspan></text>'
        f'</svg>')


def _text_rows(mask: np.ndarray) -> int:
    """Count contiguous horizontal ink bands (== wrapped line count)."""
    row_has = mask.any(axis=1)
    rows, prev = 0, False
    for v in row_has:
        if v and not prev:
            rows += 1
        prev = v
    return rows


def _ink_x_bounds(mask: np.ndarray):
    cols = np.where(mask.any(axis=0))[0]
    return (int(cols.min()), int(cols.max())) if cols.size else (None, None)


# ===========================================================================
# C1 — shape-inside resolves the rect box; text wraps INSIDE it
# ===========================================================================

def test_golden_flowed_text_renders_inside_box_not_overflowing():
    with open(GOLDEN_SVG, "rb") as f:
        doc = SvgDocument.from_bytes(f.read())
    c = SkiaCanvas()
    c.create_surface(420, 594)
    c.set_fill_color(255, 255, 255)
    c.fill_rect(0, 0, 420, 594)
    render_svg(c, doc, Rect(0, 0, 420, 594), "contain")
    c.present()
    mask = _ink_mask(surface_to_array(c))
    assert mask.any(), "golden flowed text drew NOTHING (nested tspan missed)"
    # rect1 (62,181,351,256) under scale(0.264) -> viewBox ~ (16,48,93,68),
    # fit into 420x594 (~2x). Ink must sit inside that mapped box, not spill.
    xs = np.where(mask.any(axis=0))[0]
    ys = np.where(mask.any(axis=1))[0]
    # left edge of the box maps to ~ x=32 device; text starts at/after it
    assert xs.min() >= 25, f"ink left {xs.min()} spilled left of the box"
    assert ys.min() >= 90, f"ink top {ys.min()} above the box"
    # short string stays a single band placed at the box top
    assert _text_rows(mask) == 1


def test_shape_inside_missing_shape_is_safe():
    # url points at a non-existent id -> no draw, no crash (screen unbroken)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120" '
           'viewBox="0 0 200 120"><text style="font-size:14px;'
           'shape-inside:url(#nope);fill:#000"><tspan><tspan>hi</tspan>'
           '</tspan></text></svg>')
    mask = _ink_mask(_render(svg, 200, 120))
    assert not mask.any()


# ===========================================================================
# C2 — wrapping to the box width; multi-line
# ===========================================================================

def test_long_text_wraps_to_multiple_lines():
    long = ("The quick brown fox jumps over the lazy dog and keeps going "
            "well past a single line")
    mask = _ink_mask(_render(_flow_fixture(
        "text-align:start;text-anchor:start", long), 200, 120))
    assert _text_rows(mask) >= 3, "long text did not wrap to >=3 lines"


def test_wrapped_lines_stay_within_box_width():
    long = "aaa bbb ccc ddd eee fff ggg hhh iii jjj kkk lll mmm nnn ooo"
    mask = _ink_mask(_render(_flow_fixture(
        "text-align:start;text-anchor:start", long), 200, 120))
    lo, hi = _ink_x_bounds(mask)
    bx, by, bw, bh = BOX
    # every wrapped line breaks at/under the box width (+1px AA slack)
    assert hi <= bx + bw + 1, f"ink right {hi} exceeded box right {bx + bw}"
    assert lo >= bx - 1


# ===========================================================================
# C3 — clip past the box bottom (Inkscape flow region)
# ===========================================================================

def test_overflowing_text_is_clipped_at_box_bottom():
    long = " ".join(["word"] * 60)     # far more than the box can hold
    mask = _ink_mask(_render(_flow_fixture(
        "text-align:start;text-anchor:start", long), 200, 120))
    bx, by, bw, bh = BOX
    below = mask[by + bh + 1:, :]
    assert not below.any(), "ink rendered below the box bottom (not clipped)"


# ===========================================================================
# C4 — per-line alignment: left / center / right
# ===========================================================================

def test_alignment_left_center_right_place_text_differently():
    word = "Foo bar"
    bx, by, bw, bh = BOX
    ml = _ink_mask(_render(_flow_fixture(
        "text-align:start;text-anchor:start", word), 200, 120))
    mc = _ink_mask(_render(_flow_fixture(
        "text-align:center;text-anchor:middle", word), 200, 120))
    mr = _ink_mask(_render(_flow_fixture(
        "text-align:end;text-anchor:end", word), 200, 120))
    ll, lh = _ink_x_bounds(ml)
    cl, ch = _ink_x_bounds(mc)
    rl, rh = _ink_x_bounds(mr)
    box_l, box_r = bx, bx + bw
    # left: hugs the left edge
    assert abs(ll - box_l) <= 3, f"left start {ll} not at box left {box_l}"
    # right: hugs the right edge
    assert abs(rh - box_r) <= 3, f"right end {rh} not at box right {box_r}"
    # center: roughly centered, and strictly between left and right layouts
    c_mid = (cl + ch) / 2
    assert abs(c_mid - (box_l + box_r) / 2) <= 4
    assert ll < cl < rl and lh < ch < rh


# ===========================================================================
# C5 — plain <text> (no shape-inside) is UNCHANGED (regression guard)
# ===========================================================================

def test_plain_text_without_shape_inside_unchanged():
    # anchored at x/y, single line, no box — the existing path
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60" '
           'viewBox="0 0 200 60"><text x="10" y="30" font-size="16" '
           'fill="#000">Hello</text></svg>')
    mask = _ink_mask(_render(svg, 200, 60))
    assert mask.any()
    ys = np.where(mask.any(axis=1))[0]
    xs = np.where(mask.any(axis=0))[0]
    # baseline at y=30, size 16 -> ink around the baseline, starting near x=10
    assert 10 <= ys.min() and ys.max() <= 34
    assert 8 <= xs.min() <= 14


# ===========================================================================
# C6 — Tiny5 font routes end-to-end (additive; Plex still the default)
# ===========================================================================

def test_tiny5_family_classification_and_resolution():
    assert _map_family("Tiny5") == "tiny5"
    assert _map_family("'Tiny5, Normal'") == "tiny5"
    assert classify_family("tiny5") == "tiny5"
    # bold maps to the single Regular file (only weight in the TTF)
    assert font_file_name("tiny5", "bold") == "Tiny5-Regular.ttf"
    path = resolve_font_path("tiny5", "regular")
    assert path is not None and path.endswith("Tiny5-Regular.ttf")


def test_tiny5_does_not_disturb_plex_default():
    # non-Tiny5 families keep resolving to Plex
    assert _map_family("Helvetica") == "sans-serif"
    assert classify_family("Helvetica") == "sans"
    assert font_file_name("sans-serif", "bold") == "IBMPlexSans-Bold.ttf"


def test_tiny5_measures_differently_from_plex():
    eng = get_engine()
    mt = eng.measure("tiny5", "regular", 20, "REBOOT CONSOLE")
    mp = eng.measure("sans-serif", "regular", 20, "REBOOT CONSOLE")
    assert mt is not None and mp is not None
    # different faces -> different advances/line metrics (this is WHY wrapping
    # must measure in the named font, not Plex)
    assert (mt.width, mt.height) != (mp.width, mp.height)


def test_tiny5_flowed_text_wraps_in_tiny5():
    # Tiny5-family flowed text must wrap using Tiny5 metrics; a long run wraps
    long = "REBOOT CONSOLE CONFIRM CANCEL SYSTEM RESTART NOW PLEASE"
    mask = _ink_mask(_render(_flow_fixture(
        "text-align:start;text-anchor:start", long, font="Tiny5"), 200, 120))
    assert mask.any()
    assert _text_rows(mask) >= 2


# ===========================================================================
# C7 — scene/app-scoped default font (BSG-only), explicit family wins
# ===========================================================================

def test_scoped_default_font_applies_to_unnamed_text():
    # SAME svg (no font-family), rendered with vs without a scoped default:
    # the glyph coverage must differ (Tiny5 pixel font vs Plex).
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60" '
           'viewBox="0 0 200 60"><text x="10" y="35" font-size="20" '
           'fill="#000">REBOOT</text></svg>')
    plex = _ink_mask(_render(svg, 200, 60, fit="stretch",
                             default_font_family=None))
    tiny = _ink_mask(_render(svg, 200, 60, fit="stretch",
                             default_font_family="Tiny5"))
    assert plex.any() and tiny.any()
    assert int(plex.sum()) != int(tiny.sum()), \
        "scoped default_font_family had no effect on un-named text"


def test_explicit_family_beats_scoped_default():
    # text NAMES Helvetica; a Tiny5 scoped default must NOT override it
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="60" '
           'viewBox="0 0 200 60"><text x="10" y="35" font-size="20" '
           'font-family="Helvetica" fill="#000">REBOOT</text></svg>')
    plain = _ink_mask(_render(svg, 200, 60, fit="stretch",
                              default_font_family=None))
    with_def = _ink_mask(_render(svg, 200, 60, fit="stretch",
                                 default_font_family="Tiny5"))
    assert int(plain.sum()) == int(with_def.sum()), \
        "explicit font-family was overridden by the scoped default"
