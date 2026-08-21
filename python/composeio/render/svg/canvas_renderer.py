"""
SVG -> Canvas-contract renderer ([SPEC:CXC-SVG] [CXC-SVG-PARSER]).

Walks a parsed SvgDocument and emits Layer-1 Canvas calls
(fill_path/draw_path/fill_circle/gradients/transforms/clip/text) — the
active backend (Skia on the workhorse tier) rasterizes every pixel, so
SVG output inherits the engine's cross-platform pixel identity
([CXC-R-C13]/[CXC-SVG-C1]).  This is the whole point of the spec: SVG
decomposes to the EXISTING contract, so it renders on every backend for
free (Skia crisp, CanvasKit browser, CYD subset).

Element -> primitive map (per [CXC-R-DRAW-SVG]):
    rect      -> fill_round_rect/fill_rect + stroke_(round_)rect
    circle    -> fill/stroke_circle           (a CYD-native primitive)
    ellipse   -> fill/draw_path (4-cubic arc approx)
    line      -> draw_line
    polyline  -> draw/fill_path (open)
    polygon   -> draw/fill_path (closed)
    path      -> draw/fill_path (M,L,H,V,C,S,Q,T,A,Z via path_data.py)
    g         -> save/restore + transform
    use/defs  -> parse-time id lookup + inline expansion
    linear/radialGradient -> set_linear/radial_gradient
    clipPath  -> clip_rect (bbox; see CONTRACT GAPS)
    text      -> set_font + draw_text
    image     -> decode_image + draw_image (data:/file: hrefs)

CONTRACT GAPS (flagged, [CXC-SVG] decisions for ratification) — the
Canvas contract intentionally exposes translate/rotate/scale + clip_rect
but NOT a general 2x3 matrix, path-clip, or even-odd fill.  So:
  * transform="matrix(...)"/skew is decomposed to translate+rotate+scale;
    residual shear warns once and is approximated (like Lottie's skew).
  * clip-path with a non-rect shape approximates to the clip's bbox.
  * fill-rule="evenodd" warns once; the contract fills nonzero-winding.
Group `opacity` is composed into paint alpha (no offscreen layer), the
same seam the Lottie renderer documents.  Unsupported features warn once
and are skipped — the screen is never broken.
"""

from __future__ import annotations

import base64
import logging
import math
from typing import Dict, List, Optional, Tuple

from ..canvas import (ArcTo, Canvas, Close, CurveTo, GradientStop, LineTo,
                      MoveTo, PathCommand, QuadTo)
from ..widget import Rect
from .model import (MAT_IDENTITY, Matrix, SvgDocument, SvgNode, mat_apply,
                    mat_inverse, mat_mul, parse_length, parse_transform)
from .path_data import parse_path

logger = logging.getLogger("composeio.render.svg")

_KAPPA = 0.5522847498307936     # circle-from-cubics constant (see Lottie)

_warned: set = set()


def _warn_once(feature: str) -> None:
    if feature not in _warned:
        _warned.add(feature)
        logger.warning("[CXC-SVG] unsupported/degraded SVG feature %r — "
                       "skipped or approximated (subset renderer; see "
                       "render/svg/canvas_renderer.py)", feature)


def _is_hidden(style: Dict[str, str]) -> bool:
    """True if the effective style hides the element (display:none or
    visibility:hidden/collapse) — the SVG visibility contract ([CXC-CIC])."""
    disp = style.get("display")
    if disp is not None and disp.strip().lower() == "none":
        return True
    vis = style.get("visibility")
    return vis is not None and vis.strip().lower() in ("hidden", "collapse")


# Decoded-image handle cache keyed by (canvas identity, href) — a bus-referenced
# EXT image ([CXC-CIC-RADIO-MODE] Fix 1) is resolved + decoded ONCE per canvas,
# so the 15s rotation swap (and any per-frame redraw) never re-fetches/re-decodes
# a ~0.5 MB JPEG.  Only successful handles are cached (a transient store failure
# retries next draw).
_IMAGE_HANDLE_CACHE: dict = {}


def _resolve_href_image(canvas: Canvas, href: str):
    """Resolve a non-``data:`` image href to a decoded backend handle, resolved
    AT RENDER TIME through the bus-asset model ([CXC-CIC-RADIO-MODE] Fix 1): a
    ``bus://assets/…`` / ``file://`` / local-path href is fetched via
    :func:`composeio.resolve.resolve_bytes` and decoded through the Canvas
    contract's ``decode_image``; anything the backend can load directly (e.g. an
    ``http`` URL a browser backend understands) falls back to ``load_image``.
    Cached per (canvas, href)."""
    key = (id(canvas), href)
    if key in _IMAGE_HANDLE_CACHE:
        return _IMAGE_HANDLE_CACHE[key]
    handle = None
    data = None
    try:
        from composeio import resolve as _resolve
        data = _resolve.resolve_bytes(href, timeout=10)
    except Exception:                                     # noqa: BLE001
        data = None
    if data is not None:
        decode = getattr(canvas, "decode_image", None)
        if decode is not None:
            try:
                handle = decode(data)
            except Exception:                            # noqa: BLE001
                handle = None
    if handle is None:                                   # backend-native load
        try:
            handle = canvas.load_image(href)
        except Exception:                                # noqa: BLE001
            handle = None
    if handle is not None:
        _IMAGE_HANDLE_CACHE[key] = handle
    return handle


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

# A small but practical set of CSS/SVG named colors (the ones our badges,
# diagrams, and Mermaid output actually use).  Unknown names -> None (skip).
_NAMED = {
    "none": None, "transparent": None,
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "lime": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "aqua": (0, 255, 255),
    "magenta": (255, 0, 255), "fuchsia": (255, 0, 255),
    "gray": (128, 128, 128), "grey": (128, 128, 128),
    "silver": (192, 192, 192), "maroon": (128, 0, 0), "olive": (128, 128, 0),
    "navy": (0, 0, 128), "teal": (0, 128, 128), "purple": (128, 0, 128),
    "orange": (255, 165, 0), "gold": (255, 215, 0), "pink": (255, 192, 203),
    "darkred": (139, 0, 0), "crimson": (220, 20, 60), "tomato": (255, 99, 71),
    "steelblue": (70, 130, 180), "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211), "dimgray": (105, 105, 105),
    "darkgray": (169, 169, 169), "darkgrey": (169, 169, 169),
    "whitesmoke": (245, 245, 245), "ghostwhite": (248, 248, 255),
    "royalblue": (65, 105, 225), "cornflowerblue": (100, 149, 237),
    "slategray": (112, 128, 144), "darkslategray": (47, 79, 79),
}


def _parse_color(value: Optional[str],
                 current: Optional[Tuple[int, int, int]]
                 ) -> Optional[Tuple[int, int, int]]:
    """Parse an SVG paint color to (r, g, b), or None for none/unknown.
    Does NOT handle url() gradients — the caller detects those first."""
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("", "none", "transparent"):
        return None
    if v == "currentcolor":
        return current
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
        if len(h) >= 6:
            try:
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            except ValueError:
                return None
        return None
    if v.startswith("rgb"):
        nums = _floats(v)
        if len(nums) >= 3:
            def ch(x: float) -> int:
                return max(0, min(255, int(round(x))))
            return (ch(nums[0]), ch(nums[1]), ch(nums[2]))
        return None
    return _NAMED.get(v, None) if v in _NAMED else None


def _floats(s: str) -> List[float]:
    import re
    return [float(m.group())
            for m in re.finditer(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?",
                                 s)]


# ---------------------------------------------------------------------------
# Style resolution (inherited)
# ---------------------------------------------------------------------------

_INHERITED = ("fill", "stroke", "stroke-width", "fill-opacity",
              "stroke-opacity", "fill-rule", "color", "font-family",
              "font-size", "font-weight", "text-anchor")


def _element_style(node: SvgNode) -> Dict[str, str]:
    """Merge presentation attributes with an inline style='' declaration
    (the style block wins, per CSS specificity for our subset)."""
    st: Dict[str, str] = {}
    for k, v in node.attrs.items():
        st[k] = v
    inline = node.attrs.get("style")
    if inline:
        for decl in inline.split(";"):
            if ":" in decl:
                key, val = decl.split(":", 1)
                st[key.strip()] = val.strip()
    return st


def _inherit(parent: Dict[str, str], node: SvgNode) -> Dict[str, str]:
    """Compute the effective style dict for *node* given *parent* style."""
    st = dict((k, parent[k]) for k in _INHERITED if k in parent)
    st.update(_element_style(node))
    return st


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def _apply_transform(canvas: Canvas, transform: str) -> None:
    """Parse and apply an SVG transform list to the canvas CTM using the
    contract's translate/rotate/scale ops.  matrix()/skew are decomposed
    (see CONTRACT GAPS)."""
    import re
    for name, arg in re.findall(r"(\w+)\s*\(([^)]*)\)", transform):
        vals = _floats(arg)
        if name == "translate":
            tx = vals[0] if vals else 0.0
            ty = vals[1] if len(vals) > 1 else 0.0
            canvas.translate(tx, ty)
        elif name == "scale":
            sx = vals[0] if vals else 1.0
            sy = vals[1] if len(vals) > 1 else sx
            canvas.scale(sx, sy)
        elif name == "rotate":
            ang = vals[0] if vals else 0.0
            if len(vals) >= 3:
                canvas.translate(vals[1], vals[2])
                canvas.rotate(math.radians(ang))
                canvas.translate(-vals[1], -vals[2])
            else:
                canvas.rotate(math.radians(ang))
        elif name == "matrix" and len(vals) == 6:
            _apply_matrix(canvas, vals)
        elif name in ("skewX", "skewY"):
            _warn_once("skew transform")
        else:
            _warn_once(f"transform {name!r}")


def _apply_matrix(canvas: Canvas, m: List[float]) -> None:
    """Decompose matrix(a,b,c,d,e,f) into translate+rotate+scale.  Exact for
    the common similarity case (typical exporter output); residual shear
    warns once and is dropped (CONTRACT GAP — no 2x3 matrix op)."""
    a, b, c, d, e, f = m
    canvas.translate(e, f)
    scale_x = math.hypot(a, b)
    if scale_x == 0.0:
        return
    a1, b1 = a / scale_x, b / scale_x
    shear = a1 * c + b1 * d
    c1, d1 = c - a1 * shear, d - b1 * shear
    scale_y = math.hypot(c1, d1)
    if scale_y != 0.0 and abs(shear / scale_y) > 1e-4:
        _warn_once("sheared matrix() transform")
    if (a * d - b * c) < 0.0:            # reflection
        scale_x = -scale_x
    rotation = math.atan2(b1, a1)
    if rotation:
        canvas.rotate(rotation)
    if scale_x != 1.0 or scale_y != 1.0:
        canvas.scale(scale_x, scale_y)


# Any residual shear whose magnitude (relative to scale_y) exceeds this is
# baked into geometry rather than dropped — matches the old warn threshold, so
# exactly the transforms that used to warn+lose their shear now render it.
_SHEAR_EPS = 1e-4


def _decompose_affine(m: Matrix
                      ) -> Tuple[float, float, float, float, float, float]:
    """(e, f, rotation, scale_x, scale_y, shear) for the standard 2D affine
    factorization M = Translate(e,f) · Rotate(rotation) · [[sx, shear],
    [0, sy]].  scale_x carries the reflection sign when det < 0 — identical
    math to ``_apply_matrix`` so the CTM factor is byte-for-byte the same."""
    a, b, c, d, e, f = m
    scale_x = math.hypot(a, b)
    if scale_x == 0.0:
        return (e, f, 0.0, 0.0, math.hypot(c, d), 0.0)
    a1, b1 = a / scale_x, b / scale_x
    shear = a1 * c + b1 * d
    c1, d1 = c - a1 * shear, d - b1 * shear
    scale_y = math.hypot(c1, d1)
    if (a * d - b * c) < 0.0:
        scale_x = -scale_x
    rotation = math.atan2(b1, a1)
    return (e, f, rotation, scale_x, scale_y, shear)


def _cmds_through(m: Matrix, cmds: List[PathCommand]) -> List[PathCommand]:
    """Apply an affine to every coordinate of a command list — points map
    exactly under any affine, so a sheared shape becomes the right sheared
    geometry with NO Canvas-contract shear op (the same principle as
    ``_pts_through`` for gradient endpoints).  Works on the CYD tier too."""
    out: List[PathCommand] = []
    for cmd in cmds:
        if isinstance(cmd, MoveTo):
            x, y = mat_apply(m, cmd.x, cmd.y)
            out.append(MoveTo(x, y))
        elif isinstance(cmd, LineTo):
            x, y = mat_apply(m, cmd.x, cmd.y)
            out.append(LineTo(x, y))
        elif isinstance(cmd, CurveTo):
            c1x, c1y = mat_apply(m, cmd.cp1x, cmd.cp1y)
            c2x, c2y = mat_apply(m, cmd.cp2x, cmd.cp2y)
            px, py = mat_apply(m, cmd.x, cmd.y)
            out.append(CurveTo(c1x, c1y, c2x, c2y, px, py))
        elif isinstance(cmd, QuadTo):
            x1, y1 = mat_apply(m, cmd.x1, cmd.y1)
            px, py = mat_apply(m, cmd.x, cmd.y)
            out.append(QuadTo(x1, y1, px, py))
        elif isinstance(cmd, ArcTo):
            # The endpoint transforms exactly; radii/rotation under a
            # non-similarity affine would need an ellipse refit (rare) —
            # endpoint-accurate is the documented degradation.
            _warn_once("sheared elliptical arc (endpoint-approximated)")
            px, py = mat_apply(m, cmd.x, cmd.y)
            out.append(ArcTo(cmd.rx, cmd.ry, cmd.rotation,
                             cmd.large_arc, cmd.sweep, px, py))
        else:                                   # Close
            out.append(cmd)
    return out


def _round_rect_cmds(x: float, y: float, w: float, h: float, r: float
                     ) -> List[PathCommand]:
    """Rounded-rect as an explicit bezier path — the fallback a sheared
    rounded <rect> uses so its geometry can be baked (native round-rect ops
    take an axis-aligned box the contract cannot shear)."""
    k = r * _KAPPA
    x2, y2 = x + w, y + h
    return [
        MoveTo(x + r, y),
        LineTo(x2 - r, y),
        CurveTo(x2 - r + k, y, x2, y + r - k, x2, y + r),
        LineTo(x2, y2 - r),
        CurveTo(x2, y2 - r + k, x2 - r + k, y2, x2 - r, y2),
        LineTo(x + r, y2),
        CurveTo(x + r - k, y2, x, y2 - r + k, x, y2 - r),
        LineTo(x, y + r),
        CurveTo(x, y + r - k, x + r - k, y, x + r, y),
        Close(),
    ]


# ---------------------------------------------------------------------------
# Geometry -> PathCommand
# ---------------------------------------------------------------------------

def _ellipse_cmds(cx: float, cy: float, rx: float, ry: float
                  ) -> List[PathCommand]:
    ox, oy = rx * _KAPPA, ry * _KAPPA
    return [
        MoveTo(cx, cy - ry),
        CurveTo(cx + ox, cy - ry, cx + rx, cy - oy, cx + rx, cy),
        CurveTo(cx + rx, cy + oy, cx + ox, cy + ry, cx, cy + ry),
        CurveTo(cx - ox, cy + ry, cx - rx, cy + oy, cx - rx, cy),
        CurveTo(cx - rx, cy - oy, cx - ox, cy - ry, cx, cy - ry),
        Close(),
    ]


def _points_cmds(points: str, closed: bool) -> List[PathCommand]:
    nums = _floats(points)
    if len(nums) < 4:
        return []
    cmds: List[PathCommand] = [MoveTo(nums[0], nums[1])]
    for i in range(2, len(nums) - 1, 2):
        cmds.append(LineTo(nums[i], nums[i + 1]))
    if closed:
        cmds.append(Close())
    return cmds


def _cmds_bbox(cmds: List[PathCommand]) -> Tuple[float, float, float, float]:
    """Conservative bbox over all command coordinates (control points
    included) — used for objectBoundingBox gradients + bbox clips."""
    xs: List[float] = []
    ys: List[float] = []
    for cmd in cmds:
        if isinstance(cmd, MoveTo) or isinstance(cmd, LineTo):
            xs.append(cmd.x); ys.append(cmd.y)
        elif isinstance(cmd, CurveTo):
            xs += [cmd.cp1x, cmd.cp2x, cmd.x]
            ys += [cmd.cp1y, cmd.cp2y, cmd.y]
        elif isinstance(cmd, QuadTo):
            xs += [cmd.x1, cmd.x]
            ys += [cmd.y1, cmd.y]
        elif isinstance(cmd, ArcTo):
            xs.append(cmd.x); ys.append(cmd.y)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class SvgRenderer:
    """Stateful walk of a document; created per render_svg() call."""

    def __init__(self, canvas: Canvas, doc: SvgDocument) -> None:
        self.c = canvas
        self.doc = doc
        # [CXC-LB-TILE] BOUND STATUS TINT — an OPTIONAL wholesale recolour.
        # When set (an (r, g, b) tuple), every resolved INK colour (fills +
        # strokes on paths/rects/circles/lines/text) becomes the tint, so a
        # monochrome icon is recoloured regardless of the asset's own explicit
        # fill (these Material icons pin fill="#e3e3e3" on the root <svg>, which
        # defeats base_fill).  Gradient stops are left alone (a monochrome asset
        # has none).  None => the asset's own colours, unchanged.
        self.tint: Optional[Tuple[int, int, int]] = None
        # Residual point transform: the shear factor a sheared matrix()/skew
        # leaves behind after the CTM absorbs the similarity part.  Identity in
        # the overwhelmingly common case (so shapes take the untouched fast
        # path); baked into shape geometry when non-identity.
        self._pt_mat: Matrix = MAT_IDENTITY

    # -- entry -------------------------------------------------------------

    def render(self, node: SvgNode, parent_style: Dict[str, str],
               alpha: float) -> None:
        tag = node.tag
        style = _inherit(parent_style, node)

        # Honor display:none / visibility:hidden ([CXC-CIC] David's ext_mode
        # hidden groups: row2-tabs / row3-tabs / in-show-non-interactive-
        # animations) — a hidden element AND its subtree do not render.  This is
        # applied during the recursive walk, so it hides DESCENDANTS; an element
        # that is EXPLICITLY extracted for isolated rendering (the modal
        # overlay-dialog-box layer is display:none in source but drawn on
        # demand) has this neutralized on its top node by render_element, so the
        # extraction intent still wins.  (visibility is treated as a subtree
        # hide, matching David's intent for these fully-hidden groups; the SVG
        # child visibility:visible override is out of scope for this subset.)
        if _is_hidden(style):
            return

        # element opacity multiplies into the alpha we thread through
        # (composed into paint alpha, not an offscreen layer — the same
        # seam the Lottie renderer documents)
        local_alpha = alpha
        op = node.attrs.get("opacity")
        if op is not None:
            try:
                local_alpha = alpha * max(0.0, min(1.0, float(op)))
            except ValueError:
                pass
        if local_alpha <= 0.0:
            return

        transform = node.attrs.get("transform")
        clip_ref = node.attrs.get("clip-path")

        saved_pt = self._pt_mat
        self.c.save()
        try:
            if transform:
                self._apply_node_transform(transform)
            if clip_ref:
                self._apply_clip(clip_ref)

            if tag in ("svg", "g", "a"):
                for child in node.children:
                    self.render(child, style, local_alpha)
            elif tag == "use":
                self._draw_use(node, style, local_alpha)
            elif tag in ("defs", "clipPath", "linearGradient",
                         "radialGradient", "symbol", "title", "desc",
                         "metadata", "style"):
                return                          # non-drawing / by-reference
            elif tag == "rect":
                self._draw_rect(node, style, local_alpha)
            elif tag == "circle":
                self._draw_circle(node, style, local_alpha)
            elif tag == "ellipse":
                self._draw_ellipse(node, style, local_alpha)
            elif tag == "line":
                self._draw_line(node, style, local_alpha)
            elif tag in ("polyline", "polygon"):
                self._draw_poly(node, style, local_alpha, tag == "polygon")
            elif tag == "path":
                self._draw_path(node, style, local_alpha)
            elif tag == "text":
                self._draw_text(node, style, local_alpha)
            elif tag == "image":
                self._draw_image(node, local_alpha)
            else:
                _warn_once(f"element <{tag}>")
        finally:
            self.c.restore()
            self._pt_mat = saved_pt

    def _apply_node_transform(self, transform: str) -> None:
        """Apply an element ``transform`` to the CTM, baking any residual shear
        into geometry instead of dropping it.

        Non-sheared transforms (translate/rotate/scale/similarity matrix) take
        the exact legacy per-function path — byte-identical CTM, zero
        regression.  A sheared matrix() (David's ~0.9deg seven-segment lean)
        splits as M = C · S: the similarity factor C rides the CTM
        (translate/rotate/scale — all the contract has), and the shear residual
        S = C^-1 · M is baked into shape points downstream."""
        if self._pt_mat != MAT_IDENTITY:
            # Already baking: the CTM is frozen for this subtree (the contract
            # applies it OUTSIDE the bake), so fold the whole transform in.
            self._pt_mat = mat_mul(self._pt_mat, parse_transform(transform))
            return
        m = parse_transform(transform)
        e, f, rotation, scale_x, scale_y, shear = _decompose_affine(m)
        if scale_y == 0.0 or abs(shear / scale_y) <= _SHEAR_EPS:
            _apply_transform(self.c, transform)     # legacy float-exact path
            return
        # Apply the similarity factor to the CTM and rebuild it as a matrix so
        # the residual is defined by construction: C · (C^-1 · M) == M.
        cmat = MAT_IDENTITY
        self.c.translate(e, f)
        cmat = mat_mul(cmat, (1.0, 0.0, 0.0, 1.0, e, f))
        if rotation:
            self.c.rotate(rotation)
            cmat = mat_mul(cmat, (math.cos(rotation), math.sin(rotation),
                                  -math.sin(rotation), math.cos(rotation),
                                  0.0, 0.0))
        if scale_x != 1.0 or scale_y != 1.0:
            self.c.scale(scale_x, scale_y)
            cmat = mat_mul(cmat, (scale_x, 0.0, 0.0, scale_y, 0.0, 0.0))
        inv = mat_inverse(cmat)
        if inv is None:
            _warn_once("degenerate sheared matrix() transform")
            return
        self._pt_mat = mat_mul(inv, m)

    # -- paint helpers -----------------------------------------------------

    def _current(self, style: Dict[str, str]) -> Optional[Tuple[int, int, int]]:
        return _parse_color(style.get("color"), None)

    def _ink(self, col: Optional[Tuple[int, int, int]]
             ) -> Optional[Tuple[int, int, int]]:
        """Apply the optional wholesale tint override to a resolved ink colour
        ([CXC-LB-TILE] bound status tint).  A None colour (unpainted) passes
        through unchanged, so downstream None-checks still short-circuit."""
        if self.tint is not None and col is not None:
            return self.tint
        return col

    def _alpha255(self, base: float, key: str, style: Dict[str, str]) -> int:
        v = style.get(key)
        a = base
        if v is not None:
            try:
                a *= max(0.0, min(1.0, float(v)))
            except ValueError:
                pass
        return max(0, min(255, int(round(a * 255.0))))

    def _fill(self, cmds: List[PathCommand], style: Dict[str, str],
              alpha: float, *, default_black: bool = True) -> bool:
        """Set up + emit a fill for *cmds*.  Returns True if it painted."""
        raw = style.get("fill")
        if raw is None:
            raw = "black" if default_black else "none"
        raw = raw.strip()
        if raw.lower() == "none":
            return False
        if style.get("fill-rule", "").strip().lower() == "evenodd":
            _warn_once("fill-rule evenodd")
        if raw.lower().startswith("url("):
            return self._fill_gradient(raw, cmds, style, alpha)
        col = self._ink(_parse_color(raw, self._current(style)))
        if col is None:
            return False
        a = self._alpha255(alpha, "fill-opacity", style)
        if a <= 0:
            return False
        self.c.set_fill_color(col[0], col[1], col[2], a)
        self.c.fill_path(cmds)
        return True

    def _stroke(self, cmds: List[PathCommand], style: Dict[str, str],
                alpha: float) -> None:
        raw = style.get("stroke")
        if raw is None or raw.strip().lower() in ("none", ""):
            return
        width = parse_length(style.get("stroke-width"), 1.0)
        if width <= 0:
            return
        col = self._ink(_parse_color(raw, self._current(style)))
        if col is None:
            return
        a = self._alpha255(alpha, "stroke-opacity", style)
        if a <= 0:
            return
        self.c.set_stroke_color(col[0], col[1], col[2], a)
        self.c.set_stroke_width(width)
        self.c.draw_path(cmds)

    def _paint_path(self, cmds: List[PathCommand], style: Dict[str, str],
                    alpha: float) -> None:
        if not cmds:
            return
        if self._pt_mat != MAT_IDENTITY:      # bake residual shear into points
            cmds = _cmds_through(self._pt_mat, cmds)
        self._fill(cmds, style, alpha)
        self._stroke(cmds, style, alpha)

    # -- gradients ---------------------------------------------------------

    def _resolve_gradient(self, gid: str) -> Optional[SvgNode]:
        node = self.doc.ids.get(gid)
        return node if node and node.tag in ("linearGradient",
                                             "radialGradient") else None

    def _gradient_stops(self, node: SvgNode, alpha: float
                        ) -> List[GradientStop]:
        # href stop inheritance
        stops_src = node
        href = node.attrs.get("href")
        if not any(c.tag == "stop" for c in node.children) and href:
            ref = self.doc.ids.get(href.lstrip("#"))
            if ref is not None:
                stops_src = ref
        out: List[GradientStop] = []
        for i, c in enumerate(stops_src.children):
            if c.tag != "stop":
                continue
            st = _element_style(c)
            off = st.get("offset", "0")
            offset = (float(off.rstrip("%")) / 100.0 if "%" in off
                      else float(off or 0))
            col = _parse_color(st.get("stop-color", "#000000"), None) \
                or (0, 0, 0)
            so = st.get("stop-opacity")
            sa = alpha
            if so is not None:
                try:
                    sa *= max(0.0, min(1.0, float(so)))
                except ValueError:
                    pass
            out.append(GradientStop(offset=max(0.0, min(1.0, offset)),
                                    r=col[0], g=col[1], b=col[2],
                                    a=max(0, min(255, int(round(sa * 255.0))))))
        return out

    def _fill_gradient(self, raw: str, cmds: List[PathCommand],
                       style: Dict[str, str], alpha: float) -> bool:
        gid = raw[raw.find("(") + 1:raw.find(")")].strip().strip("'\"")
        gid = gid.lstrip("#")
        node = self._resolve_gradient(gid)
        if node is None:
            _warn_once(f"gradient url #{gid}")
            return False
        stops = self._gradient_stops(node, self._alpha255(
            alpha, "fill-opacity", style) / 255.0)
        if not stops:
            return False
        units = node.attrs.get("gradientUnits", "objectBoundingBox")
        bx, by, bw, bh = _cmds_bbox(cmds)

        def ux(val: str, default: float, span: float, origin: float) -> float:
            if units == "userSpaceOnUse":
                return parse_length(val, default)
            # objectBoundingBox: fraction of the shape bbox
            frac = (float(val.rstrip("%")) / 100.0 if val and "%" in val
                    else float(val) if val not in (None, "") else default)
            return origin + frac * span

        if node.tag == "linearGradient":
            x1 = ux(node.attrs.get("x1", ""), 0.0, bw, bx)
            y1 = ux(node.attrs.get("y1", ""), 0.0, bh, by)
            x2 = ux(node.attrs.get("x2", ""), 1.0, bw, bx)
            y2 = ux(node.attrs.get("y2", ""), 0.0, bh, by)
            gt = node.attrs.get("gradientTransform")
            if gt:
                (x1, y1), (x2, y2) = _pts_through(gt, [(x1, y1), (x2, y2)])
            self.c.set_linear_gradient(x1, y1, x2, y2, stops)
        else:
            cx = ux(node.attrs.get("cx", ""), 0.5, bw, bx)
            cy = ux(node.attrs.get("cy", ""), 0.5, bh, by)
            if units == "userSpaceOnUse":
                r = parse_length(node.attrs.get("r"),
                                 0.5 * math.hypot(bw, bh))
            else:
                rf = (float(node.attrs.get("r", "0.5").rstrip("%")) / 100.0
                      if "%" in node.attrs.get("r", "") else
                      float(node.attrs.get("r", 0.5)))
                r = rf * math.hypot(bw, bh) / math.sqrt(2)
            gt = node.attrs.get("gradientTransform")
            if gt:
                _warn_once("radial gradientTransform")
            self.c.set_radial_gradient(cx, cy, r, stops)
        self.c.fill_path(cmds)
        self.c.set_fill_color(0, 0, 0, 255)      # neutralize gradient paint
        return True

    # -- clip --------------------------------------------------------------

    def _apply_clip(self, clip_ref: str) -> None:
        cid = clip_ref
        if "url(" in clip_ref:
            cid = clip_ref[clip_ref.find("(") + 1:clip_ref.find(")")]
        cid = cid.strip().strip("'\"").lstrip("#")
        node = self.doc.ids.get(cid)
        if node is None or node.tag != "clipPath":
            return
        # collect the union bbox of the clip's child shapes (CONTRACT GAP:
        # the contract has no path clip, so we clip to the bounding box)
        boxes = []
        rect_only = True
        for ch in node.children:
            cmds = self._shape_to_cmds(ch)
            if ch.tag != "rect":
                rect_only = False
            if cmds:
                boxes.append(_cmds_bbox(cmds))
        if not boxes:
            return
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        if not rect_only or len(boxes) > 1:
            _warn_once("non-rect clip-path (approximated to bbox)")
        self.c.clip_rect(x0, y0, x1 - x0, y1 - y0)

    def _shape_to_cmds(self, node: SvgNode) -> List[PathCommand]:
        """Geometry-only conversion (no paint) — used by clip + use."""
        if node.tag == "rect":
            x = parse_length(node.attrs.get("x"), 0.0)
            y = parse_length(node.attrs.get("y"), 0.0)
            w = parse_length(node.attrs.get("width"), 0.0)
            h = parse_length(node.attrs.get("height"), 0.0)
            return [MoveTo(x, y), LineTo(x + w, y), LineTo(x + w, y + h),
                    LineTo(x, y + h), Close()]
        if node.tag == "circle":
            cx = parse_length(node.attrs.get("cx"), 0.0)
            cy = parse_length(node.attrs.get("cy"), 0.0)
            r = parse_length(node.attrs.get("r"), 0.0)
            return _ellipse_cmds(cx, cy, r, r)
        if node.tag == "ellipse":
            return _ellipse_cmds(parse_length(node.attrs.get("cx"), 0.0),
                                 parse_length(node.attrs.get("cy"), 0.0),
                                 parse_length(node.attrs.get("rx"), 0.0),
                                 parse_length(node.attrs.get("ry"), 0.0))
        if node.tag in ("polyline", "polygon"):
            return _points_cmds(node.attrs.get("points", ""),
                                 node.tag == "polygon")
        if node.tag == "path":
            return parse_path(node.attrs.get("d", ""))
        return []

    # -- shapes ------------------------------------------------------------

    def _draw_rect(self, node: SvgNode, style: Dict[str, str],
                   alpha: float) -> None:
        x = parse_length(node.attrs.get("x"), 0.0)
        y = parse_length(node.attrs.get("y"), 0.0)
        w = parse_length(node.attrs.get("width"), 0.0)
        h = parse_length(node.attrs.get("height"), 0.0)
        if w <= 0 or h <= 0:
            return
        rx = node.attrs.get("rx")
        ry = node.attrs.get("ry")
        radius = 0.0
        if rx is not None or ry is not None:
            radius = parse_length(rx if rx is not None else ry, 0.0)
        # rounded rects use the native round-rect primitives (CYD-friendly)
        if radius > 0:
            radius = min(radius, w / 2.0, h / 2.0)
            if self._pt_mat != MAT_IDENTITY:
                # native round-rect takes an axis-aligned box the contract
                # cannot shear — emit an explicit path so the bake applies.
                self._paint_path(_round_rect_cmds(x, y, w, h, radius),
                                 style, alpha)
                return
            fill = self._rect_fill_setup(style, alpha, x, y, w, h)
            if fill:
                self.c.fill_round_rect(x, y, w, h, radius)
                self._reset_gradient_if(fill)
            self._rect_stroke_setup(style, alpha)
            if self._has_stroke(style):
                self.c.stroke_round_rect(x, y, w, h, radius)
            return
        cmds = [MoveTo(x, y), LineTo(x + w, y), LineTo(x + w, y + h),
                LineTo(x, y + h), Close()]
        self._paint_path(cmds, style, alpha)

    def _rect_fill_setup(self, style, alpha, x, y, w, h) -> str:
        """Configure fill paint for a round-rect; returns 'color'|'grad'|''."""
        raw = style.get("fill")
        if raw is None:
            raw = "black"
        raw = raw.strip()
        if raw.lower() == "none":
            return ""
        if raw.lower().startswith("url("):
            # reuse the gradient setup against the rect bbox
            cmds = [MoveTo(x, y), LineTo(x + w, y), LineTo(x + w, y + h),
                    LineTo(x, y + h), Close()]
            gid = raw[raw.find("(") + 1:raw.find(")")].strip().strip("'\"#")
            node = self._resolve_gradient(gid)
            if node is None:
                return ""
            stops = self._gradient_stops(
                node, self._alpha255(alpha, "fill-opacity", style) / 255.0)
            if not stops:
                return ""
            self._setup_gradient_paint(node, cmds, stops, style)
            return "grad"
        col = self._ink(_parse_color(raw, self._current(style)))
        if col is None:
            return ""
        a = self._alpha255(alpha, "fill-opacity", style)
        if a <= 0:
            return ""
        self.c.set_fill_color(col[0], col[1], col[2], a)
        return "color"

    def _setup_gradient_paint(self, node, cmds, stops, style):
        """Set the gradient shader (shared by rect + generic path)."""
        units = node.attrs.get("gradientUnits", "objectBoundingBox")
        bx, by, bw, bh = _cmds_bbox(cmds)

        def ux(val, default, span, origin):
            if units == "userSpaceOnUse":
                return parse_length(val, default)
            frac = (float(val.rstrip("%")) / 100.0 if val and "%" in val
                    else float(val) if val not in (None, "") else default)
            return origin + frac * span
        if node.tag == "linearGradient":
            x1 = ux(node.attrs.get("x1", ""), 0.0, bw, bx)
            y1 = ux(node.attrs.get("y1", ""), 0.0, bh, by)
            x2 = ux(node.attrs.get("x2", ""), 1.0, bw, bx)
            y2 = ux(node.attrs.get("y2", ""), 0.0, bh, by)
            gt = node.attrs.get("gradientTransform")
            if gt:
                (x1, y1), (x2, y2) = _pts_through(gt, [(x1, y1), (x2, y2)])
            self.c.set_linear_gradient(x1, y1, x2, y2, stops)
        else:
            cx = ux(node.attrs.get("cx", ""), 0.5, bw, bx)
            cy = ux(node.attrs.get("cy", ""), 0.5, bh, by)
            if units == "userSpaceOnUse":
                r = parse_length(node.attrs.get("r"), 0.5 * math.hypot(bw, bh))
            else:
                rstr = node.attrs.get("r", "0.5")
                rf = (float(rstr.rstrip("%")) / 100.0 if "%" in rstr
                      else float(rstr))
                r = rf * math.hypot(bw, bh) / math.sqrt(2)
            self.c.set_radial_gradient(cx, cy, r, stops)

    def _reset_gradient_if(self, fill: str) -> None:
        if fill == "grad":
            self.c.set_fill_color(0, 0, 0, 255)

    def _has_stroke(self, style: Dict[str, str]) -> bool:
        raw = style.get("stroke")
        return raw is not None and raw.strip().lower() not in ("none", "") \
            and parse_length(style.get("stroke-width"), 1.0) > 0

    def _rect_stroke_setup(self, style: Dict[str, str], alpha: float) -> None:
        if not self._has_stroke(style):
            return
        col = self._ink(_parse_color(style.get("stroke"), self._current(style)))
        if col is None:
            return
        a = self._alpha255(alpha, "stroke-opacity", style)
        self.c.set_stroke_color(col[0], col[1], col[2], a)
        self.c.set_stroke_width(parse_length(style.get("stroke-width"), 1.0))

    def _draw_circle(self, node: SvgNode, style: Dict[str, str],
                     alpha: float) -> None:
        cx = parse_length(node.attrs.get("cx"), 0.0)
        cy = parse_length(node.attrs.get("cy"), 0.0)
        r = parse_length(node.attrs.get("r"), 0.0)
        if r <= 0:
            return
        # native circle primitives (a CYD-supported primitive) — but a
        # url() gradient fill needs a path, so fall back for that case
        raw = (style.get("fill") or "black").strip().lower()
        # a url() gradient fill OR an active geometry bake needs a path
        # (native circle ops draw an axis-aligned disc the contract can't shear)
        if raw.startswith("url(") or self._pt_mat != MAT_IDENTITY:
            self._paint_path(_ellipse_cmds(cx, cy, r, r), style, alpha)
            return
        col = self._ink(_parse_color(style.get("fill", "black"),
                                     self._current(style)))
        if col is not None:
            a = self._alpha255(alpha, "fill-opacity", style)
            if a > 0:
                self.c.set_fill_color(col[0], col[1], col[2], a)
                self.c.fill_circle(cx, cy, r)
        if self._has_stroke(style):
            self._rect_stroke_setup(style, alpha)
            self.c.stroke_circle(cx, cy, r)

    def _draw_ellipse(self, node: SvgNode, style: Dict[str, str],
                      alpha: float) -> None:
        rx = parse_length(node.attrs.get("rx"), 0.0)
        ry = parse_length(node.attrs.get("ry"), 0.0)
        if rx <= 0 or ry <= 0:
            return
        cmds = _ellipse_cmds(parse_length(node.attrs.get("cx"), 0.0),
                             parse_length(node.attrs.get("cy"), 0.0), rx, ry)
        self._paint_path(cmds, style, alpha)

    def _draw_line(self, node: SvgNode, style: Dict[str, str],
                   alpha: float) -> None:
        if not self._has_stroke(style):
            return
        col = self._ink(_parse_color(style.get("stroke"), self._current(style)))
        if col is None:
            return
        a = self._alpha255(alpha, "stroke-opacity", style)
        self.c.set_stroke_color(col[0], col[1], col[2], a)
        self.c.set_stroke_width(parse_length(style.get("stroke-width"), 1.0))
        x1 = parse_length(node.attrs.get("x1"), 0.0)
        y1 = parse_length(node.attrs.get("y1"), 0.0)
        x2 = parse_length(node.attrs.get("x2"), 0.0)
        y2 = parse_length(node.attrs.get("y2"), 0.0)
        if self._pt_mat != MAT_IDENTITY:      # a line stays a line under affine
            x1, y1 = mat_apply(self._pt_mat, x1, y1)
            x2, y2 = mat_apply(self._pt_mat, x2, y2)
        self.c.draw_line(x1, y1, x2, y2)

    def _draw_poly(self, node: SvgNode, style: Dict[str, str],
                   alpha: float, closed: bool) -> None:
        self._paint_path(_points_cmds(node.attrs.get("points", ""), closed),
                         style, alpha)

    def _draw_path(self, node: SvgNode, style: Dict[str, str],
                   alpha: float) -> None:
        self._paint_path(parse_path(node.attrs.get("d", "")), style, alpha)

    # -- text --------------------------------------------------------------

    def _draw_text(self, node: SvgNode, style: Dict[str, str],
                   alpha: float) -> None:
        # SVG2 flowed text ([CXC-SVG-FLOW]): a <text> whose style names a
        # layout shape via shape-inside:url(#id) is wrapped/aligned inside
        # that shape's box.  The plain single-line path below is UNCHANGED
        # when shape-inside is absent (colonial_console chrome + all current
        # SVG text depend on it), so this dispatch is purely additive.
        shape_inside = style.get("shape-inside")
        if shape_inside and "url(" in shape_inside:
            self._draw_flowed_text(node, style, alpha, shape_inside)
            return
        # flatten this text element + its tspans into one run at the
        # element's x/y (per-tspan repositioning is deferred; warn once)
        text = node.text
        for ch in node.children:
            if ch.tag == "tspan":
                text = (text + ch.text).strip()
                if ch.attrs.get("x") or ch.attrs.get("dx"):
                    _warn_once("per-tspan x/dx positioning")
            else:
                _warn_once(f"<text> child <{ch.tag}>")
        if not text:
            return
        x = parse_length(node.attrs.get("x"), 0.0)
        y = parse_length(node.attrs.get("y"), 0.0)
        size = parse_length(style.get("font-size"), 16.0)
        family = _map_family(style.get("font-family", "sans-serif"))
        weight = _map_weight(style.get("font-weight", "regular"))
        anchor = style.get("text-anchor", "start").strip().lower()
        align = {"middle": "center", "end": "right"}.get(anchor, "left")
        col = self._ink(_parse_color(style.get("fill", "black"),
                                     self._current(style))) or (0, 0, 0)
        a = self._alpha255(alpha, "fill-opacity", style)
        if a <= 0:
            return
        self.c.set_fill_color(col[0], col[1], col[2], a)
        self.c.set_font(family, size, weight)
        self.c.draw_text(text, x, y, align)

    # -- flowed text (SVG2 shape-inside, [CXC-SVG-FLOW]) -------------------

    def _resolve_shape_inside(self, ref: str
                              ) -> Optional[Tuple[float, float, float, float]]:
        """Resolve ``shape-inside:url(#id)`` to the referenced shape's
        (x, y, w, h) layout box in the CURRENT (post-transform) user space.

        Reuses the parser's ``<defs>`` id index + ``_shape_to_cmds`` (the
        SAME geometry path clip/use use) — no forked parser.  <rect> is the
        exact/primary case (Inkscape flowed text); any other shape falls back
        to the bbox of its geometry so the screen is never broken."""
        sid = ref[ref.find("(") + 1:ref.find(")")].strip().strip("'\"").lstrip("#")
        node = self.doc.ids.get(sid)
        if node is None:
            return None
        if node.tag == "rect":
            return (parse_length(node.attrs.get("x"), 0.0),
                    parse_length(node.attrs.get("y"), 0.0),
                    parse_length(node.attrs.get("width"), 0.0),
                    parse_length(node.attrs.get("height"), 0.0))
        cmds = self._shape_to_cmds(node)
        if cmds:
            return _cmds_bbox(cmds)
        return None

    def _collect_text_runs(self, node: SvgNode, node_style: Dict[str, str],
                           runs: List[Tuple[str, Tuple[int, int, int]]]) -> None:
        """Depth-first flatten of a <text>'s character data across nested
        <tspan>s, resolving each run's effective fill through style
        inheritance.  The golden nests <tspan x y><tspan fill:#000000>… so the
        old direct-child-only flatten missed the text entirely — this recurses."""
        if node.text:
            col = self._ink(_parse_color(node_style.get("fill", "black"),
                            self._current(node_style))) or (0, 0, 0)
            runs.append((node.text, col))
        for ch in node.children:
            if ch.tag == "tspan":
                self._collect_text_runs(ch, _inherit(node_style, ch), runs)
            else:
                _warn_once(f"flowed <text> child <{ch.tag}>")

    @staticmethod
    def _flow_align(style: Dict[str, str]) -> str:
        """Per-line alignment from text-align (SVG2/CSS) or text-anchor.
        left | center | right — David requires all three carry through."""
        ta = style.get("text-align", "").strip().lower()
        if ta in ("center", "middle"):
            return "center"
        if ta in ("end", "right"):
            return "right"
        if ta in ("start", "left", "justify"):
            return "left"
        anchor = style.get("text-anchor", "start").strip().lower()
        return {"middle": "center", "end": "right"}.get(anchor, "left")

    def _draw_flowed_text(self, node: SvgNode, style: Dict[str, str],
                          alpha: float, shape_ref: str) -> None:
        box = self._resolve_shape_inside(shape_ref)
        if box is None:
            _warn_once("shape-inside unresolved shape")
            return
        bx, by, bw, bh = box
        if bw <= 0 or bh <= 0:
            return

        runs: List[Tuple[str, Tuple[int, int, int]]] = []
        self._collect_text_runs(node, style, runs)
        parts = [r[0].strip() for r in runs if r[0].strip()]
        full = " ".join(parts)
        words = full.split()
        if not words:
            return
        colors = {r[1] for r in runs if r[0].strip()}
        col = next(iter(r[1] for r in runs if r[0].strip()))
        if len(colors) > 1:
            # mid-line multi-color runs are out of scope; first run's color
            # wins (faithful for single-color flowed text like the golden)
            _warn_once("multi-color flowed text (single color applied)")

        size = parse_length(style.get("font-size"), 16.0)
        family = _map_family(style.get("font-family", "sans-serif"))
        weight = _map_weight(style.get("font-weight", "regular"))
        a = self._alpha255(alpha, "fill-opacity", style)
        if a <= 0:
            return

        # Measure THROUGH the contract (set_font -> measure_text): the exact
        # deterministic integer-advance path draw_text uses, so a wrap
        # decision never disagrees with the rendered advance ([CXC-SVG-C1]).
        self.c.set_font(family, size, weight)
        lm = self.c.measure_text(full)
        line_height = lm.height if lm.height > 0 else size * 1.2
        ascent = lm.ascent if lm.ascent > 0 else size * 0.8

        # Greedy word-wrap to the box WIDTH (measure the candidate line, which
        # is exactly what will be drawn as one draw_text call).
        lines: List[str] = []
        cur = ""
        for w in words:
            trial = w if not cur else cur + " " + w
            if cur and self.c.measure_text(trial).width > bw:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)

        align = self._flow_align(style)
        if align == "center":
            tx = bx + bw / 2.0
        elif align == "right":
            tx = bx + bw
        else:
            tx = bx

        self.c.set_fill_color(col[0], col[1], col[2], a)
        # Clip to the box: text past the bottom is hidden (match Inkscape's
        # flow region).  Pure function of (doc, rect, fit) — no clocks/state.
        self.c.save()
        try:
            self.c.clip_rect(bx, by, bw, bh)
            baseline = by + ascent
            for line in lines:
                if baseline - ascent >= by + bh:   # line top past box bottom
                    break
                self.c.draw_text(line, tx, baseline, align)
                baseline += line_height
        finally:
            self.c.restore()

    # -- image + use -------------------------------------------------------

    def _draw_image(self, node: SvgNode, alpha: float) -> None:
        href = node.attrs.get("href", "")
        x = parse_length(node.attrs.get("x"), 0.0)
        y = parse_length(node.attrs.get("y"), 0.0)
        w = parse_length(node.attrs.get("width"), 0.0)
        h = parse_length(node.attrs.get("height"), 0.0)
        handle = None
        if href.startswith("data:"):
            try:
                meta, b64 = href.split(",", 1)
                data = base64.b64decode(b64) if ";base64" in meta \
                    else b64.encode()
                decode = getattr(self.c, "decode_image", None)
                if decode is not None:
                    handle = decode(data)
            except Exception:
                handle = None
        elif href:
            # bus://assets/… (the EXT image, [CXC-CIC-RADIO-MODE] Fix 1), file://,
            # or a backend-native href — resolved at render time + cached.
            handle = _resolve_href_image(self.c, href)
        if handle is not None and w > 0 and h > 0:
            self.c.draw_image(handle, x, y, w, h)
        else:
            _warn_once("<image> href (unresolved)")

    def _draw_use(self, node: SvgNode, style: Dict[str, str],
                  alpha: float) -> None:
        href = node.attrs.get("href", "")
        ref = self.doc.ids.get(href.lstrip("#"))
        if ref is None:
            _warn_once(f"<use> href {href!r}")
            return
        x = parse_length(node.attrs.get("x"), 0.0)
        y = parse_length(node.attrs.get("y"), 0.0)
        self.c.save()
        try:
            if x or y:
                self.c.translate(x, y)
            # a <use> of a <symbol>/<svg> renders its children; else the ref
            target = ref
            if ref.tag in ("symbol", "svg"):
                for child in ref.children:
                    self.render(child, style, alpha)
            else:
                self.render(target, style, alpha)
        finally:
            self.c.restore()


def _map_family(family: str) -> str:
    f = (family or "").split(",")[0].strip().strip("'\"").lower()
    # Named custom faces pass through to font resolution (classify_family);
    # do NOT collapse them to the generic sans/mono buckets.  Tiny5 is the
    # Colonial-OS pixel font ([SPEC:CXC-CIC]); other families still bucket.
    if "tiny5" in f:
        return "tiny5"
    if f in ("monospace", "mono", "courier", "ibm plex mono", "consolas"):
        return "monospace"
    return "sans-serif"


def _map_weight(weight: str) -> str:
    w = str(weight or "").strip().lower()
    if w in ("bold", "700", "800", "900", "bolder"):
        return "bold"
    if w in ("500", "600", "medium"):
        return "medium"
    return "regular"


def _pts_through(transform: str,
                 pts: List[Tuple[float, float]]
                 ) -> List[Tuple[float, float]]:
    """Apply an SVG transform list to gradient endpoints exactly (points
    transform under any affine, unlike shape primitives).  Delegates to the
    ONE transform-math implementation in model.py — do not fork it here."""
    m = parse_transform(transform)
    return [mat_apply(m, px, py) for px, py in pts]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render_svg(canvas: Canvas, doc: SvgDocument, rect: Rect,
               fit: str = "contain",
               base_fill: Optional[str] = None,
               default_font_family: Optional[str] = None,
               tint: Optional[Tuple[int, int, int]] = None) -> None:
    """Render *doc* into *rect* through the Canvas contract.

    fit: contain (default, letterboxed) | cover | stretch — mirrors the
    Lottie/Image fit modes and honours viewBox aspect ratio.  Pure
    function of (doc, rect, fit): no clocks, no state, so goldens are
    byte-identical macOS<->RPi ([CXC-SVG-C1]).

    ``base_fill`` (a CSS color string, e.g. ``"#e7ecf3"``) overrides the
    inherited base fill AND the ``currentColor`` value, so a monochrome icon
    authored with ``fill="currentColor"`` renders in the caller's ink
    ([CXC-DVL-ICON-ASSET] TINTING) — the header recenter glyph in ``t.ink``.

    ``default_font_family`` (e.g. ``"Tiny5"``) is a SCENE/APP-SCOPED default:
    text that does NOT name its own ``font-family`` inherits this instead of
    the global Plex fallback ([SPEC:CXC-CIC]).  It is seeded into the base
    style and rides the normal ``font-family`` inheritance, so an explicit
    ``font-family:`` on any element still wins.  Left None, the global Plex
    fallback is unchanged (the Intelligence Console keeps Plex) — this is
    additive and NEVER global.

    ``tint`` (an (r, g, b) tuple) is an OPTIONAL WHOLESALE RECOLOUR: every
    resolved ink colour (fills + strokes) becomes the tint, so a monochrome
    icon is recoloured regardless of the asset's own explicit fill
    ([CXC-LB-TILE] bound status tint).  Unlike ``base_fill`` (which only
    seeds the INHERITED base and loses to an element's explicit fill), the
    tint overrides the final colour — the launch-bar Material icons pin
    fill="#e3e3e3" on the root <svg>, so only a true override recolours them.
    Left None, the asset's own colours render unchanged."""
    vb_x, vb_y, vb_w, vb_h = doc.viewbox
    if vb_w <= 0 or vb_h <= 0:
        return
    canvas.save()
    try:
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        sx, sy = rect.w / vb_w, rect.h / vb_h
        if fit == "stretch":
            canvas.translate(rect.x, rect.y)
            if sx != 1.0 or sy != 1.0:
                canvas.scale(sx, sy)
        else:
            s = max(sx, sy) if fit == "cover" else min(sx, sy)
            canvas.translate(rect.x + (rect.w - vb_w * s) / 2.0,
                             rect.y + (rect.h - vb_h * s) / 2.0)
            if s != 1.0:
                canvas.scale(s, s)
        if vb_x or vb_y:
            canvas.translate(-vb_x, -vb_y)
        renderer = SvgRenderer(canvas, doc)
        renderer.tint = tint
        base_style = {"fill": "black", "stroke": "none"}
        if base_fill:
            base_style["fill"] = base_fill
            base_style["color"] = base_fill      # resolves currentColor
        if default_font_family:
            # seed the inherited font-family; explicit element font-family wins
            base_style["font-family"] = default_font_family
        renderer.render(doc.root, base_style, 1.0)
    finally:
        canvas.restore()


def _top_node_attrs(attrs: Dict[str, str]) -> Dict[str, str]:
    """Attributes for an explicitly-extracted node's rendered copy: drop
    ``transform`` (folded into the wrapper CTM) and strip any display:none /
    visibility:hidden (the extraction is the caller's intent to draw it —
    [CXC-CIC], the modal dialog layer)."""
    out = {k: v for k, v in attrs.items()
           if k not in ("transform", "display", "visibility")}
    st = out.get("style")
    if st and ("display" in st or "visibility" in st):
        kept = []
        for decl in st.split(";"):
            key = decl.split(":", 1)[0].strip().lower()
            if key in ("display", "visibility") or not decl.strip():
                continue
            kept.append(decl.strip())
        out["style"] = ";".join(kept)
    return out


def _collect_ids(node: SvgNode, ids: Dict[str, SvgNode]) -> None:
    nid = node.attrs.get("id")
    if nid:
        ids[nid] = node
    for ch in node.children:
        _collect_ids(ch, ids)


def render_element(canvas: Canvas, doc: SvgDocument, node: SvgNode, rect: Rect,
                   design: Tuple[float, float, float, float],
                   fit: str = "stretch") -> None:
    """Render a single labeled subtree of *doc* into *rect* through the Canvas
    contract, mapping the *design* box (a viewBox-space (x, y, w, h) box) onto
    the rect.  Reuses :func:`render_svg` entirely — the same decomposition,
    winding fills, transforms, and colours as any SVG, so a decomposed element
    renders identically on every backend.

    An element extracted for isolated rendering LOSES its ancestor transforms:
    artwork may carry a subtree's placement on a PARENT group's ``transform``,
    so a deep child's raw path coords no longer register with the viewBox-space
    *design* box.  Re-root the node under its FULL element CTM (resolved from the
    source doc) and strip the node's OWN transform so it is not applied twice.
    For an identity-CTM element this is a transparent no-op wrapper."""
    ids: Dict[str, SvgNode] = dict(doc.ids)
    _collect_ids(node, ids)
    ctm = doc.element_ctm(node.attrs.get("id") or "") or MAT_IDENTITY
    # An EXPLICITLY-extracted node is drawn because the caller asked for it: fold
    # its own transform into ``ctm`` (so the wrapper does not double-apply it) AND
    # NEUTRALIZE a source display:none / visibility:hidden on THIS top node — the
    # modal overlay-dialog-box layer is display:none in David's SVG but drawn on
    # demand ([CXC-CIC]).  Descendants remain subject to the visibility rule in
    # ``render``.  For an identity-CTM, visible, transform-less node this is a
    # transparent no-op wrapper.
    inner = SvgNode(node.tag, _top_node_attrs(node.attrs),
                    node.children, node.text)
    a, b, c, d, e, f = ctm
    wrapped = SvgNode("g", {"transform": f"matrix({a},{b},{c},{d},{e},{f})"},
                      [inner])
    mini = SvgDocument(root=SvgNode("svg", {}, [wrapped]),
                       width=design[2], height=design[3], viewbox=design,
                       preserve_aspect_ratio="none", ids=ids)
    render_svg(canvas, mini, rect, fit)
