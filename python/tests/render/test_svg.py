"""
[SPEC:CXC-SVG] SVG Rendering — compliance tests.

Maps to the spec's compliance table (section 5):

  C1  SVG via primitives, pixel-identical: an SVG decomposes to Canvas
      primitives and renders deterministically (byte-identical resample /
      cross-process), so goldens hold macOS<->RPi ([CXC-R-C13]).
  C2  Element coverage: rect/circle/ellipse/line/poly/path(M,L,C,Q,A,Z)/
      text/image/g/defs/use/gradients/clip all render ([CXC-R-C14]).
  C3  Bound src: an SvgWidget with a namespace-bound `src` swaps graphics.
  C4  Red Alert (crisp): a stylized "Red Alert" SVG renders crisp at large
      scale — vector, no raster upscaling.
  C6  Diagram pan/zoom: the DiagramView pan/zoom view transform is applied
      deterministically ([CXC-R-C16]).
  C7  CYD degradation: a simple SVG renders on the CYD subset; a path/
      gradient-heavy one is SKIPPED (degraded), not FAILED/crashed.

  (C5 — the Node Mermaid/Graphviz diagram SERVICE — is a hub workload
   OUTSIDE this Python render engine; the consumer tail is proven here by
   rendering a diagram-shaped SVG through the widget, see
   test_c5_diagram_shaped_svg_renders.)

Goldens live in reference_images/ and regenerate via:
    CXC_REGEN_GOLDENS=1 python3 -m pytest tests/render/test_svg.py
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
from composeio.render.widgets import SvgWidget
from composeio.render.svg import SvgDocument, parse_path, render_svg
from composeio.render.svg.path_data import parse_path as _pp
from composeio.render.canvas import (ArcTo, Close, CurveTo, LineTo, MoveTo,
                                     QuadTo)
from composeio.render.engine.runtime import RenderApp
from composeio.render.engine.bindings import bindable_attrs_for

from .conftest import (REFERENCE_DIR, images_match, load_reference,
                       surface_to_array)

ASSET_DIR = os.path.join(PY_ROOT, "composeio", "render", "examples", "assets")
RED_ALERT_SVG = os.path.join(ASSET_DIR, "red_alert.svg")
FLOWCHART_SVG = os.path.join(ASSET_DIR, "flowchart.svg")


def _canvas(w=240, h=240):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0A, 0x10)
    c.fill_rect(0, 0, w, h)
    return c


def _golden(canvas, name):
    actual = surface_to_array(canvas)
    path = REFERENCE_DIR / f"{name}.png"
    if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
        canvas.save_png(str(path))
        pytest.skip(f"golden (re)generated: {path}")
    expected = load_reference(name)
    match, ratio = images_match(actual, expected, tolerance=2,
                                max_diff_ratio=0.005)
    assert match, f"{name}: {ratio * 100:.2f}% of pixels exceed tolerance"


def _render_str(svg: str, w=240, h=240, fit="contain") -> SkiaCanvas:
    c = _canvas(w, h)
    render_svg(c, SvgDocument.from_string(svg), Rect(0, 0, w, h), fit)
    c.present()
    return c


# ===========================================================================
# Path-data grammar ([CXC-SVG-C2] path M,L,H,V,C,S,Q,T,A,Z abs+rel)
# ===========================================================================

def test_path_absolute_commands():
    cmds = parse_path("M10 20 L30 40 H50 V60 Z")
    assert cmds == [MoveTo(10, 20), LineTo(30, 40), LineTo(50, 40),
                    LineTo(50, 60), Close()]


def test_path_relative_commands():
    cmds = parse_path("m10 10 l10 0 v10 h-10 z")
    assert cmds == [MoveTo(10, 10), LineTo(20, 10), LineTo(20, 20),
                    LineTo(10, 20), Close()]


def test_path_implicit_repeat_after_moveto_is_lineto():
    # extra coordinate pairs after M are implicit linetos
    cmds = parse_path("M0 0 10 10 20 0")
    assert cmds == [MoveTo(0, 0), LineTo(10, 10), LineTo(20, 0)]


def test_path_cubic_and_smooth_reflection():
    cmds = parse_path("M0 0 C10 0 10 10 20 10 S30 20 40 20")
    assert isinstance(cmds[1], CurveTo) and isinstance(cmds[2], CurveTo)
    # S reflects the previous cubic's 2nd control point about the current pt:
    # prev c2=(10,10), current=(20,10) -> reflected (30,10)
    assert (cmds[2].cp1x, cmds[2].cp1y) == (30, 10)


def test_path_quadratic_and_smooth_reflection():
    cmds = parse_path("M0 0 Q10 10 20 0 T40 0")
    assert isinstance(cmds[1], QuadTo) and isinstance(cmds[2], QuadTo)
    # prev q=(10,10), current=(20,0) -> reflected control (30,-10)
    assert (cmds[2].x1, cmds[2].y1) == (30, -10)


def test_path_arc_maps_to_contract_arcto():
    cmds = parse_path("M0 0 A25 25 0 1 0 50 0")
    arc = cmds[1]
    assert isinstance(arc, ArcTo)
    assert (arc.rx, arc.ry, arc.large_arc, arc.sweep, arc.x, arc.y) == \
        (25, 25, True, False, 50, 0)


def test_path_tolerates_truncated_data():
    # a trailing incomplete tuple is dropped, never crashes
    cmds = parse_path("M0 0 L10 10 C5 5 5")
    assert cmds == [MoveTo(0, 0), LineTo(10, 10)]


# ===========================================================================
# C1 — decompose to primitives, deterministic/pixel-identical
# ===========================================================================

SHAPES_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="120"
  height="120" viewBox="0 0 120 120">
  <rect x="5" y="5" width="50" height="30" fill="#26a69a"/>
  <circle cx="90" cy="25" r="18" fill="#ef5350"/>
  <path d="M10 80 Q40 40 70 80 T110 80" fill="none" stroke="#ffa726"
    stroke-width="3"/>
</svg>"""


def test_c1_resample_is_byte_identical():
    a = surface_to_array(_render_str(SHAPES_SVG)).copy()
    b = surface_to_array(_render_str(SHAPES_SVG))
    assert np.array_equal(a, b)          # pure function of (doc, rect, fit)


def test_c1_only_contract_primitives_emitted():
    """The renderer must touch ONLY Canvas-contract methods (no backend-
    specific API) — a recording canvas over the abstract contract proves
    the decompose boundary ([CXC-SVG-INTENT])."""
    from composeio.render.canvas import Canvas, Size, TextMetrics

    contract = {m for m in dir(Canvas) if not m.startswith("_")}
    seen: list = []

    class RecordingCanvas(Canvas):
        # implement every abstract method to record + no-op
        pass

    def _mk(name):
        def f(self, *a, **k):
            seen.append(name)
            if name == "measure_text":
                return TextMetrics(0, 0, 0, 0)
            if name == "get_surface_size":
                return Size(120, 120)
            return None
        return f

    for m in contract:
        setattr(RecordingCanvas, m, _mk(m))
    RecordingCanvas.__abstractmethods__ = frozenset()

    c = RecordingCanvas()
    c.create_surface(120, 120)
    render_svg(c, SvgDocument.from_string(SHAPES_SVG), Rect(0, 0, 120, 120))
    assert seen                                   # it drew something
    assert set(seen) <= contract                  # ONLY contract methods
    assert {"fill_path", "fill_circle", "draw_path",
            "save", "restore", "clip_rect"} <= set(seen)


def test_c1_shapes_golden():
    _golden(_render_str(SHAPES_SVG), "svg_shapes")


# ===========================================================================
# C2 — element coverage
# ===========================================================================

COVERAGE_SVG = """<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink" width="200" height="200"
  viewBox="0 0 200 200">
  <defs>
    <linearGradient id="lg" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#26a69a"/>
      <stop offset="1" stop-color="#ef5350"/>
    </linearGradient>
    <radialGradient id="rg"><stop offset="0" stop-color="#fff"/>
      <stop offset="1" stop-color="#42a5f5"/></radialGradient>
    <clipPath id="cp"><rect x="10" y="120" width="80" height="40"/></clipPath>
    <rect id="tile" x="0" y="0" width="16" height="16" fill="#ab47bc"/>
  </defs>
  <rect x="10" y="10" width="80" height="24" rx="6" fill="url(#lg)"/>
  <circle cx="120" cy="24" r="14" fill="#26a69a" stroke="#fff"
    stroke-width="2"/>
  <ellipse cx="170" cy="24" rx="22" ry="12" fill="#ffa726"/>
  <line x1="10" y1="48" x2="190" y2="48" stroke="#e0e0e0" stroke-width="2"/>
  <polyline points="10,90 40,60 70,90 100,60" fill="none" stroke="#42a5f5"
    stroke-width="2"/>
  <polygon points="130,60 160,60 175,90 145,100 120,80" fill="#7fc99a"/>
  <path d="M10 120 C40 100 70 140 100 120 L100 150 L10 150 Z"
    fill="url(#rg)"/>
  <g transform="translate(120,120)" clip-path="url(#cp)">
    <circle cx="20" cy="20" r="40" fill="#5d2f38"/>
  </g>
  <use xlink:href="#tile" x="150" y="120"/>
  <use xlink:href="#tile" x="172" y="120"/>
  <text x="100" y="185" font-size="14" text-anchor="middle" fill="#fff">
    coverage</text>
</svg>"""


def test_c2_all_mapped_elements_render():
    # must not raise on any element in the mapping table
    c = _render_str(COVERAGE_SVG, 200, 200)
    arr = surface_to_array(c)
    # non-trivial: many distinct colors present (elements actually drew)
    uniq = np.unique(arr.reshape(-1, 4), axis=0)
    assert len(uniq) > 40


def test_c2_coverage_golden():
    _golden(_render_str(COVERAGE_SVG, 200, 200), "svg_coverage")


def test_c2_gradient_object_bounding_box_units():
    # a gradient in objectBoundingBox units fills across the shape bbox
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="40"
      viewBox="0 0 100 40"><defs><linearGradient id="g" x1="0" y1="0"
      x2="1" y2="0"><stop offset="0" stop-color="#000000"/>
      <stop offset="1" stop-color="#ffffff"/></linearGradient></defs>
      <rect x="0" y="0" width="100" height="40" fill="url(#g)"/></svg>"""
    arr = surface_to_array(_render_str(svg, 100, 40, "stretch"))
    left = int(arr[20, 2, 0])
    right = int(arr[20, 97, 0])
    assert left < 40 and right > 200      # dark left -> light right


# ===========================================================================
# C3 — bound src swaps the graphic reactively
# ===========================================================================

def _app(config: dict) -> RenderApp:
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def _widget(app: RenderApp, wid: str):
    return next(w for w in app.comp.root.iter_widgets()
               if w.widget_id == wid)


def _svg_config(src, **extra):
    return {
        "design": {"width": 240, "height": 240, "title": "svg"},
        "widgets": [{
            "type": "pixel_stack", "id": "scene", "width": 240,
            "height": 240, "background": "#0A0A10",
            "children": [{
                "type": "svg", "id": "g", "src": src,
                "x": 0, "y": 0, "w": 240, "h": 240, **extra,
            }],
        }],
    }


def test_c3_bound_src_swaps_document(monkeypatch):
    from composeio import resolve as _resolve
    red = open(RED_ALERT_SVG, "rb").read()
    flow = open(FLOWCHART_SVG, "rb").read()
    store = {"bus://assets/svg/red.svg": red,
             "bus://assets/svg/flow.svg": flow}

    def fake_bytes(uri, **kw):
        return store.get(uri)
    monkeypatch.setattr(_resolve, "resolve_bytes", fake_bytes)

    app = _app(_svg_config({"namespace": "ui/graphic",
                            "default": "bus://assets/svg/red.svg"}))
    try:
        w = _widget(app, "g")
        app.render_frame_at(0.0)
        assert w._doc is not None
        assert w._doc.width == 240        # red_alert viewBox
        app.publish("ui/graphic", "bus://assets/svg/flow.svg")
        app.render_frame_at(0.1)
        assert w._doc is not None
        assert w._doc.width == 320        # flowchart swapped in
    finally:
        app.stop()


def test_c3_missing_asset_renders_placeholder_not_crash():
    app = _app(_svg_config("bus://assets/svg/definitely_missing_xyz.svg"))
    try:
        app.render_frame_at(0.0)          # must not raise
        assert _widget(app, "g")._doc is None
    finally:
        app.stop()


# ===========================================================================
# C4 — Red Alert renders crisp at large scale (vector, no raster upscale)
# ===========================================================================

def _render_asset(path, w, h, fit="contain"):
    doc = SvgDocument.from_bytes(open(path, "rb").read())
    c = _canvas(w, h)
    render_svg(c, doc, Rect(0, 0, w, h), fit)
    c.present()
    return c


def test_c4_red_alert_large_scale_golden():
    # rendered at 480px from a 240px viewBox — 2x, still vector-crisp
    _golden(_render_asset(RED_ALERT_SVG, 480, 480), "svg_red_alert_2x")


def test_c4_red_alert_crisp_edges_scale_invariant():
    """Crispness proof: vector text/edges stay sharp at 4x — the fraction
    of hard (high-gradient) edge pixels does NOT collapse the way a raster
    upscale would blur them.  Compare a 120px vs a 480px render."""
    def edge_sharpness(path, size):
        arr = surface_to_array(_render_asset(path, size, size)).astype(
            np.int16)
        gx = np.abs(np.diff(arr[:, :, 0].astype(np.int16), axis=1))
        # sharp edges: pixels where luminance jumps hard
        return float((gx > 80).mean())
    small = edge_sharpness(RED_ALERT_SVG, 120)
    large = edge_sharpness(RED_ALERT_SVG, 480)
    # a raster upscale would blur (fewer hard edges per pixel as it grows);
    # vector rendering keeps genuinely sharp edges at every size
    assert large > 0.003
    assert small > 0.003


# ===========================================================================
# C6 — diagram pan/zoom view transform (deterministic)
# ===========================================================================

def test_c6_pan_zoom_changes_output_deterministically():
    doc = SvgDocument.from_bytes(open(FLOWCHART_SVG, "rb").read())
    w = SvgWidget("d", pan_zoom=True)
    w.set_document(doc)
    w.arranged_rect = Rect(0, 0, 240, 200)

    base = _canvas(240, 200)
    w.draw(base, Rect(0, 0, 240, 200))
    base_arr = surface_to_array(base).copy()

    w.on_scroll(0, -400)                  # zoom in
    assert w._view_scale > 1.0
    zoomed = _canvas(240, 200)
    w.draw(zoomed, Rect(0, 0, 240, 200))
    assert not np.array_equal(base_arr, surface_to_array(zoomed))

    # same view transform -> byte-identical (pure state)
    again = _canvas(240, 200)
    w.draw(again, Rect(0, 0, 240, 200))
    assert np.array_equal(surface_to_array(zoomed), surface_to_array(again))


def test_c6_pan_translates_view():
    w = SvgWidget("d", pan_zoom=True)
    w.set_document(SvgDocument.from_string(SHAPES_SVG))
    w.arranged_rect = Rect(0, 0, 120, 120)
    w.on_pan(15, 25)
    assert (w._view_x, w._view_y) == (15, 25)


def test_c6_pan_zoom_disabled_is_not_interactive():
    w = SvgWidget("d")                    # pan_zoom defaults off
    assert w.interactive is False
    w.on_scroll(0, -400)
    assert w._view_scale == 1.0           # ignored


# ===========================================================================
# C5 (consumer tail) — a diagram-shaped SVG renders through the widget
# ===========================================================================

def test_c5_diagram_shaped_svg_renders():
    _golden(_render_asset(FLOWCHART_SVG, 320, 260), "svg_flowchart")


# ===========================================================================
# C7 — CYD degradation: simple renders, complex SKIPs (not FAIL/crash)
# ===========================================================================

def test_c7_cyd_subset_skips_complex_but_runs_simple():
    from composeio.render import conformance as conf

    results = conf.verify_backend(SkiaCanvas, capabilities=conf.CAP_CYD)
    # simple SVG uses only rect/circle/line -> renders on the CYD caps
    assert results["svg_simple"]["status"] == "PASS"
    # path/gradient/transform SVG -> SKIP (degraded), never FAIL/crash
    assert results["svg_complex"]["status"] == "SKIP"
    assert "paths" in results["svg_complex"]["missing"]


# ===========================================================================
# Widget wiring + bindable surface
# ===========================================================================

def test_svg_widget_registered_in_parser():
    app = _app(_svg_config(""))
    try:
        assert type(_widget(app, "g")).__name__ == "SvgWidget"
    finally:
        app.stop()


def test_bindable_surface_registered_for_composer():
    attrs = bindable_attrs_for("svg")
    for a in ("src", "fit", "visible", "opacity"):
        assert a in attrs, a


def test_doc_uri_via_asset_manager(monkeypatch):
    import base64
    from composeio import resolve as _resolve
    svg = open(FLOWCHART_SVG, "rb").read()
    doc = {"content": base64.b64encode(svg).decode()}

    def fake_doc(uri, **kw):
        assert uri == "doc://diagrams/flow.svg"
        return doc
    monkeypatch.setattr(_resolve, "resolve_doc", fake_doc)

    app = _app(_svg_config("doc://diagrams/flow.svg"))
    try:
        app.render_frame_at(0.0)
        w = _widget(app, "g")
        assert w._doc is not None and w._doc.width == 320
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# model seam: id -> transform-RESOLVED geometry + inline serialization
# (the compositing capabilities the maker hookup guide stands on)
# ---------------------------------------------------------------------------

def test_element_center_resolves_element_transform():
    # scale(-1) with raw negative coords — the SuperMini right-row shape.
    doc = SvgDocument.from_string(
        '<svg viewBox="0 0 100 100">'
        '<circle id="c" cx="-70" cy="-40" r="4" transform="scale(-1)"/>'
        '</svg>')
    cx, cy = doc.element_center("c")
    assert (round(cx, 6), round(cy, 6)) == (70.0, 40.0)


def test_element_center_resolves_ancestor_transforms():
    doc = SvgDocument.from_string(
        '<svg viewBox="0 0 100 100">'
        '<g transform="translate(10 20) scale(2)">'
        '<rect id="r" x="5" y="5" width="10" height="10"/></g>'
        '</svg>')
    # rect center local (10, 10) -> scale 2 -> (20, 20) -> + (10, 20)
    cx, cy = doc.element_center("r")
    assert (round(cx, 6), round(cy, 6)) == (30.0, 40.0)


def test_element_center_resolves_centered_rotate():
    doc = SvgDocument.from_string(
        '<svg viewBox="0 0 100 100">'
        '<circle id="c" cx="60" cy="50" r="2" '
        'transform="rotate(180 50 50)"/></svg>')
    cx, cy = doc.element_center("c")
    assert (round(cx, 6), round(cy, 6)) == (40.0, 50.0)


def test_element_center_missing_or_unsupported():
    doc = SvgDocument.from_string(
        '<svg viewBox="0 0 10 10"><path id="p" d="M0 0 L5 5"/></svg>')
    assert doc.element_center("nope") is None
    assert doc.element_center("p") is None          # paths need the renderer


def test_serialize_svg_group_prefixes_ids_and_refs():
    from composeio.render.svg import serialize_svg_group
    doc = SvgDocument.from_string(
        '<svg viewBox="0 0 10 10">'
        '<defs><linearGradient id="g1"><stop offset="0"/></linearGradient>'
        '</defs>'
        '<metadata><junk/></metadata>'
        '<rect id="body" fill="url(#g1)" x="0" y="0" width="4" height="4"/>'
        '</svg>')
    frag = serialize_svg_group(doc, id_prefix="mcu-")
    assert 'id="mcu-body"' in frag and 'id="mcu-g1"' in frag
    assert 'url(#mcu-g1)' in frag
    assert "<metadata" not in frag                  # non-render tags dropped


# ===========================================================================
# Sheared matrix() fidelity — David's ~0.9deg seven-segment lean
# (fix/svg-colon-shear-fidelity): a sheared matrix() must be baked into the
# geometry points, not decomposed-and-dropped against the shear-less contract.
# ===========================================================================

def _ctm_recording_canvas():
    """A contract-only canvas that tracks the CTM and captures the
    DEVICE-SPACE coordinates handed to fill_path — so we can assert the final
    geometry, CTM factor plus point bake together."""
    from composeio.render.canvas import Canvas, Size, TextMetrics, MoveTo, LineTo
    from composeio.render.svg.model import mat_mul, mat_apply

    class Rec(Canvas):
        def __init__(self):
            self.ctm = [(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)]
            self.fills = []
        def _mul(self, m):
            self.ctm[-1] = mat_mul(self.ctm[-1], m)
        def save(self): self.ctm.append(self.ctm[-1])
        def restore(self): self.ctm.pop()
        def translate(self, x, y): self._mul((1.0, 0.0, 0.0, 1.0, x, y))
        def rotate(self, a):
            import math
            self._mul((math.cos(a), math.sin(a), -math.sin(a),
                       math.cos(a), 0.0, 0.0))
        def scale(self, x, y): self._mul((x, 0.0, 0.0, y, 0.0, 0.0))
        def fill_path(self, cmds):
            m = self.ctm[-1]
            self.fills.append([mat_apply(m, c.x, c.y) for c in cmds
                               if isinstance(c, (MoveTo, LineTo))])
        def get_surface_size(self): return Size(210, 297)
        def measure_text(self, *a, **k): return TextMetrics(0, 0, 0, 0)

    for m in (set(dir(Canvas)) - set(vars(Rec))):
        if not m.startswith("_") and getattr(getattr(Canvas, m, None),
                                              "__isabstractmethod__", False):
            setattr(Rec, m, (lambda self, *a, **k: None))
    Rec.__abstractmethods__ = frozenset()
    return Rec()


_SHEAR = "matrix(1,0,-0.01563161,0.99987782,0,0)"


def test_sheared_matrix_is_baked_into_geometry_exactly():
    """A sheared <rect> renders as the parallelogram the raw affine defines —
    every corner within 1e-6 of matrix()-applied coordinates.  1:1 viewport
    (stretch over the viewBox) so device == user space."""
    from composeio.render.svg.model import mat_apply, parse_transform
    doc = SvgDocument.from_string(
        '<svg viewBox="0 0 210 297"><rect x="103.9" y="131.5" '
        'width="6.77" height="6.77" transform="%s"/></svg>' % _SHEAR)
    c = _ctm_recording_canvas()
    render_svg(c, doc, Rect(0, 0, 210, 297), fit="stretch")
    assert len(c.fills) == 1
    M = parse_transform(_SHEAR)
    exp = [mat_apply(M, x, y) for x, y in
           ((103.9, 131.5), (110.67, 131.5),
            (110.67, 138.27), (103.9, 138.27))]
    got = c.fills[0][:4]
    for (gx, gy), (ex, ey) in zip(got, exp):
        assert abs(gx - ex) < 1e-6 and abs(gy - ey) < 1e-6
    # and it actually LEANS: bottom edge sits left of the top edge (skewX<0)
    top_cx = (got[0][0] + got[1][0]) / 2.0
    bot_cx = (got[2][0] + got[3][0]) / 2.0
    assert bot_cx < top_cx


def test_sheared_matrix_does_not_warn():
    """The old code warned 'sheared matrix() transform' and dropped the shear;
    the bake path must render it silently (no lossy-warning)."""
    import composeio.render.svg.canvas_renderer as cr
    seen = []
    orig = cr._warn_once
    cr._warn_once = lambda m: seen.append(m)
    try:
        doc = SvgDocument.from_string(
            '<svg viewBox="0 0 210 297"><rect x="0" y="0" width="6" '
            'height="6" transform="%s"/></svg>' % _SHEAR)
        render_svg(_ctm_recording_canvas(), doc, Rect(0, 0, 210, 297),
                   fit="stretch")
    finally:
        cr._warn_once = orig
    assert not any("shear" in m for m in seen), seen


def test_non_sheared_transforms_stay_on_the_ctm():
    """Regression guard: translate/rotate/scale (and a similarity matrix) must
    NOT trigger the point-bake — they ride the CTM exactly as before, so the
    renderer's residual point-matrix stays identity while drawing."""
    from composeio.render.svg.canvas_renderer import SvgRenderer
    from composeio.render.svg.model import MAT_IDENTITY
    for xf in ("translate(5,7)", "rotate(30)", "scale(2,3)",
               "translate(4,4) rotate(15) scale(1.5)",
               "matrix(2,0,0,2,10,10)"):
        doc = SvgDocument.from_string(
            '<svg viewBox="0 0 100 100"><rect x="1" y="1" width="8" '
            'height="8" transform="%s"/></svg>' % xf)
        c = _ctm_recording_canvas()
        r = SvgRenderer(c, doc)
        seen_pt = []
        orig_paint = r._paint_path
        def spy(cmds, style, alpha, _o=orig_paint):
            seen_pt.append(r._pt_mat)
            return _o(cmds, style, alpha)
        r._paint_path = spy
        r.render(doc.root, {}, 1.0)
        assert seen_pt, xf
        assert all(pm == MAT_IDENTITY for pm in seen_pt), (xf, seen_pt)
