"""
Compliance tests — the launch_tile GENERAL bound-status affordance and the
launch-bar disk-space alert ([SPEC:CXC-LAUNCHBAR], [DX-NARROW-WAIST],
[DX-STATE-ON-BUS]).

Two things under test, at two scopes:
  * the WIDGET is general — an OPTIONAL bound icon TINT (colour) that recolours
    the monochrome SVG, and an OPTIONAL bound BLINK (bool) whose 0.5 s phase is
    a pure function of the logical clock.  A tile that declares neither is
    byte-for-byte its old self.
  * the SCENE holds the POLICY — the two storage tiles bind tint+blink off
    `skipper/storage/free_bytes` with the thresholds IN THE EXPR, and an
    absent/unset value (reads 0.0) must NOT false-alarm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]          # .../python
_REPO_ROOT = _PKG_ROOT.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.render.engine import dsl
from composeio.render.engine.bindings import (BINDABLE_ATTRS, COLOR_ATTRS,
                                              apply_expr_binding,
                                              bindable_attrs_for)
from composeio.render.engine.dsl import EvalContext
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.widgets.launch_tile import (_BLINK_PERIOD_S,
                                                  LaunchTileWidget)

SCENE = _REPO_ROOT / "apps/compose_platform_launch_bar" / "launch_bar.compose.yaml"
NS = "skipper/storage/free_bytes"

# The band table the SCENE encodes ([DX-STATE-ON-BUS] policy-in-the-expr).
# (free_bytes, tint_hex_or_None_meaning_normal, blinks)
_NORMAL = (0xE3, 0xE3, 0xE3, 255)
_ORANGE = (0xFF, 0x95, 0x00, 255)
_RED = (0xE0, 0x3B, 0x3B, 255)
BANDS = [
    (None,          _NORMAL, False),   # sensor not up -> absent reads 0.0
    (0,             _NORMAL, False),   # 0 == absent (safe default, NOT critical)
    (6_000_000_000, _NORMAL, False),
    (5_000_000_000, _NORMAL, False),   # >= 5e9 boundary is normal
    (4_000_000_000, _ORANGE, False),
    (3_000_000_000, _ORANGE, False),   # >= 3e9 boundary is still orange
    (2_500_000_000, _RED,    False),
    (2_000_000_000, _RED,    False),   # >= 2e9 boundary is red, no blink
    (1_500_000_000, _RED,    True),
]


def _tiles():
    comp = parse_compose_yaml(str(SCENE))
    out = {}

    def walk(w):
        if isinstance(w, LaunchTileWidget):
            out[w.widget_id] = w
        for ch in getattr(w, "children", []) or []:
            walk(ch)
    walk(comp.root)
    return comp, out


# ---------------------------------------------------------------------------
# The affordance is registered as GENERAL + bindable (not launch_tile-special).
# ---------------------------------------------------------------------------
def test_icon_tint_and_blink_are_bindable():
    assert "icon_tint" in BINDABLE_ATTRS and "blink" in BINDABLE_ATTRS
    # icon_tint must coerce as a colour even from a None default.
    assert "icon_tint" in COLOR_ATTRS
    # surfaced for the Composer binding editor with zero editor hardcoding.
    attrs = bindable_attrs_for("launch_tile")
    assert "icon_tint" in attrs and "blink" in attrs


# ---------------------------------------------------------------------------
# A tile that declares NEITHER binding is its old self (both OPTIONAL).
# ---------------------------------------------------------------------------
def test_defaults_are_inert():
    t = LaunchTileWidget("t", label="X", target="x", icon="")
    assert t.icon_tint is None and t.blink is False and t._blink_on is True
    # steady: on_clock_tick reports "not animating" and never hides the icon.
    assert t.on_clock_tick(0.0) is False
    assert t.on_clock_tick(0.5) is False
    assert t._blink_on is True


# ---------------------------------------------------------------------------
# BLINK phase is a pure function of the logical clock (deterministic).
# ---------------------------------------------------------------------------
def test_blink_phase_is_clock_pure():
    t = LaunchTileWidget("t", label="X", target="x", icon="", blink=True)
    # visible for the first half of each period, hidden for the second.
    assert t.on_clock_tick(0.0) is True and t._blink_on is True
    assert t.on_clock_tick(_BLINK_PERIOD_S * 0.4) is True and t._blink_on is True
    assert t.on_clock_tick(_BLINK_PERIOD_S) is True and t._blink_on is False
    assert t.on_clock_tick(_BLINK_PERIOD_S * 1.9) is True and t._blink_on is False
    assert t.on_clock_tick(_BLINK_PERIOD_S * 2) is True and t._blink_on is True
    # clearing blink while the icon is HIDDEN (off-phase) un-hides it on the
    # next tick (one repaint), then goes steady.
    assert t.on_clock_tick(_BLINK_PERIOD_S) is True and t._blink_on is False
    t.blink = False
    assert t.on_clock_tick(_BLINK_PERIOD_S * 3) is True     # one repaint to show
    assert t._blink_on is True
    assert t.on_clock_tick(_BLINK_PERIOD_S * 3.5) is False  # steady thereafter


# ---------------------------------------------------------------------------
# The SVG tint recolours the monochrome icon wholesale (contract-level).
# ---------------------------------------------------------------------------
def test_svg_tint_recolours_every_fill():
    from composeio.render.svg import SvgDocument, render_svg
    from composeio.render.widget import Rect

    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
           b'fill="#e3e3e3"><path d="M0 0h10v10H0z"/></svg>')
    doc = SvgDocument.from_bytes(svg)

    class _CaptureCanvas:
        def __init__(self):
            self.fills = []
        def save(self): pass
        def restore(self): pass
        def clip_rect(self, *a): pass
        def translate(self, *a): pass
        def scale(self, *a): pass
        def set_fill_color(self, r, g, b, a): self.fills.append((r, g, b, a))
        def fill_path(self, cmds): pass

    # untinted: the asset's own #e3e3e3 (227,227,227).
    c = _CaptureCanvas()
    render_svg(c, doc, Rect(0, 0, 10, 10), "contain")
    assert (227, 227, 227) in [(r, g, b) for r, g, b, _ in c.fills]

    # tinted: the same path is recoloured to the override, NOT the asset colour.
    c = _CaptureCanvas()
    render_svg(c, doc, Rect(0, 0, 10, 10), "contain", tint=(0xE0, 0x3B, 0x3B))
    painted = [(r, g, b) for r, g, b, _ in c.fills]
    assert (0xE0, 0x3B, 0x3B) in painted
    assert (227, 227, 227) not in painted


# ---------------------------------------------------------------------------
# SCENE POLICY: the two storage tiles bind tint+blink; the bands resolve as
# specified; absent/0 is NORMAL, not critical.
# ---------------------------------------------------------------------------
def test_only_storage_tiles_are_wired():
    comp, tiles = _tiles()
    bound = {b.widget.widget_id for b in comp.attr_bindings
             if b.attr in ("icon_tint", "blink")}
    assert bound == {"tile_docstore", "tile_busstore"}
    # every wiring is an EXPRESSION reading exactly the storage namespace.
    for b in comp.attr_bindings:
        if b.attr in ("icon_tint", "blink"):
            assert b.expr is not None and b.namespace == ""
            assert b.expr.namespaces == (NS,)


@pytest.mark.parametrize("free,tint,blinks", BANDS)
def test_bands_resolve(free, tint, blinks):
    comp, tiles = _tiles()
    binds = {(b.widget.widget_id, b.attr): b for b in comp.attr_bindings
             if b.attr in ("icon_tint", "blink")}
    state = {} if free is None else {NS: free}
    ctx = EvalContext(ns=lambda n: state.get(n))
    for tid in ("tile_docstore", "tile_busstore"):
        w = tiles[tid]
        for attr in ("icon_tint", "blink"):
            b = binds[(tid, attr)]
            apply_expr_binding(w, b, b.expr.evaluate(ctx))
        assert tuple(w.icon_tint) == tint, f"{tid} @ {free}: tint {w.icon_tint}"
        assert bool(w.blink) is blinks, f"{tid} @ {free}: blink {w.blink}"


def test_absent_never_false_alarms():
    """The single most important band: a not-yet-started sensor reads 0.0 and
    must render NORMAL, never red/blink ([DX-STATE-ON-BUS] safe default)."""
    comp, tiles = _tiles()
    binds = {(b.widget.widget_id, b.attr): b for b in comp.attr_bindings
             if b.attr in ("icon_tint", "blink")}
    ctx = EvalContext(ns=lambda n: None)          # namespace never published
    w = tiles["tile_docstore"]
    for attr in ("icon_tint", "blink"):
        b = binds[("tile_docstore", attr)]
        apply_expr_binding(w, b, b.expr.evaluate(ctx))
    assert tuple(w.icon_tint) == _NORMAL and w.blink is False
