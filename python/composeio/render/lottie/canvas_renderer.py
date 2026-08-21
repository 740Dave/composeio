"""
Lottie -> Canvas-contract renderer ([SPEC:CXC-LOTTIE] [CXC-LOT-RENDER]).

Walks a LottieAnimation's layer/shape tree at a given frame and emits
Layer-1 Canvas calls (fill_path/draw_path/gradients/transforms) — the
backend (Skia on the workhorse tier) rasterizes every pixel, so Lottie
output inherits the engine's cross-platform pixel identity ([CXC-R-C1]).

Opacity is composed by multiplying layer x group x style alpha into the
paint color's alpha channel (deterministic int rounding) rather than
touching the backend's global set_opacity, which engine/layout.py owns.

Unsupported Lottie features warn ONCE per feature and are skipped — the
screen is never broken ([CXC-R-DRAWIMAGE] precedent).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import (Canvas, Close, CurveTo, GradientStop, LineTo, MoveTo,
                      PathCommand)
from ..widget import Rect
from .model import Animated, AnimatedShape, LottieAnimation

logger = logging.getLogger("composeio.render.lottie")

_KAPPA = 0.5522847498307936     # 4/3*(sqrt(2)-1): circle-from-cubics constant

_warned: set = set()


def _warn_once(feature: str) -> None:
    if feature not in _warned:
        _warned.add(feature)
        logger.warning("[CXC-LOTTIE] unsupported Lottie feature %r — skipped "
                       "(subset renderer; see render/lottie/__init__.py)",
                       feature)


# ---------------------------------------------------------------------------
# Geometry -> PathCommand builders
# ---------------------------------------------------------------------------

def _shape_path(shape: Optional[dict]) -> List[PathCommand]:
    """Convert a Lottie bezier shape {c, v, i, o} (i/o tangents relative
    to their vertex) into contract path commands."""
    if not shape:
        return []
    v = shape.get("v") or []
    if len(v) < 2:
        return []
    i_t = shape.get("i") or [[0, 0]] * len(v)
    o_t = shape.get("o") or [[0, 0]] * len(v)
    cmds: List[PathCommand] = [MoveTo(float(v[0][0]), float(v[0][1]))]
    for j in range(1, len(v)):
        cmds.append(CurveTo(
            float(v[j - 1][0]) + float(o_t[j - 1][0]),
            float(v[j - 1][1]) + float(o_t[j - 1][1]),
            float(v[j][0]) + float(i_t[j][0]),
            float(v[j][1]) + float(i_t[j][1]),
            float(v[j][0]), float(v[j][1])))
    if shape.get("c"):
        last = len(v) - 1
        cmds.append(CurveTo(
            float(v[last][0]) + float(o_t[last][0]),
            float(v[last][1]) + float(o_t[last][1]),
            float(v[0][0]) + float(i_t[0][0]),
            float(v[0][1]) + float(i_t[0][1]),
            float(v[0][0]), float(v[0][1])))
        cmds.append(Close())
    return cmds


def _ellipse_path(cx: float, cy: float, rx: float, ry: float,
                  ccw: bool = False) -> List[PathCommand]:
    """Ellipse from four cubics.  ccw reverses the winding — the Lottie
    `d: 3` reversed-direction idiom, used with nonzero fill to author
    donuts/annuli (e.g. deterministic filled rings; see the [CXC-LOT-C1]
    stroke-divergence note in the spec)."""
    ox, oy = rx * _KAPPA, ry * _KAPPA
    if ccw:
        return [
            MoveTo(cx, cy - ry),
            CurveTo(cx - ox, cy - ry, cx - rx, cy - oy, cx - rx, cy),
            CurveTo(cx - rx, cy + oy, cx - ox, cy + ry, cx, cy + ry),
            CurveTo(cx + ox, cy + ry, cx + rx, cy + oy, cx + rx, cy),
            CurveTo(cx + rx, cy - oy, cx + ox, cy - ry, cx, cy - ry),
            Close(),
        ]
    return [
        MoveTo(cx, cy - ry),
        CurveTo(cx + ox, cy - ry, cx + rx, cy - oy, cx + rx, cy),
        CurveTo(cx + rx, cy + oy, cx + ox, cy + ry, cx, cy + ry),
        CurveTo(cx - ox, cy + ry, cx - rx, cy + oy, cx - rx, cy),
        CurveTo(cx - rx, cy - oy, cx - ox, cy - ry, cx, cy - ry),
        Close(),
    ]


def _rect_path(cx: float, cy: float, w: float, h: float,
               r: float) -> List[PathCommand]:
    x0, y0 = cx - w / 2.0, cy - h / 2.0
    x1, y1 = x0 + w, y0 + h
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    if r <= 0.0:
        return [MoveTo(x0, y0), LineTo(x1, y0), LineTo(x1, y1),
                LineTo(x0, y1), Close()]
    k = r * _KAPPA
    return [
        MoveTo(x0 + r, y0),
        LineTo(x1 - r, y0),
        CurveTo(x1 - r + k, y0, x1, y0 + r - k, x1, y0 + r),
        LineTo(x1, y1 - r),
        CurveTo(x1, y1 - r + k, x1 - r + k, y1, x1 - r, y1),
        LineTo(x0 + r, y1),
        CurveTo(x0 + r - k, y1, x0, y1 - r + k, x0, y1 - r),
        LineTo(x0, y0 + r),
        CurveTo(x0, y0 + r - k, x0 + r - k, y0, x0 + r, y0),
        Close(),
    ]


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _alpha255(alpha: float) -> int:
    return max(0, min(255, int(round(alpha * 255.0))))


def _color_rgba(cval: List[float], opacity100: float,
                alpha: float) -> Tuple[int, int, int, int]:
    """Lottie color [r,g,b(,a)] 0..1 + o (0-100) + composed alpha -> RGBA."""
    r = max(0, min(255, int(round(float(cval[0]) * 255.0)))) if cval else 0
    g = max(0, min(255, int(round(float(cval[1]) * 255.0)))) if len(cval) > 1 else 0
    b = max(0, min(255, int(round(float(cval[2]) * 255.0)))) if len(cval) > 2 else 0
    a = alpha * (opacity100 / 100.0)
    if len(cval) > 3:
        a *= float(cval[3])
    return r, g, b, _alpha255(a)


def _gradient_stops(gdata: dict, opacity100: float,
                    alpha: float) -> List[GradientStop]:
    """Parse a gf 'g' block: p color stops + optional trailing alpha stops.
    Animated stop arrays are evaluated at their first keyframe (static-
    stop subset; warn once)."""
    p = int(gdata.get("p", 0))
    karr = gdata.get("k", {})
    arr = karr.get("k", []) if isinstance(karr, dict) else karr
    if arr and isinstance(arr[0], dict):            # animated stops
        _warn_once("animated gradient stops")
        s = arr[0].get("s")
        arr = s if isinstance(s, list) else []
    arr = [float(x) for x in arr]
    color_part = arr[:p * 4]
    alpha_part = arr[p * 4:]
    apairs = [(alpha_part[i], alpha_part[i + 1])
              for i in range(0, len(alpha_part) - 1, 2)]

    def alpha_at(off: float) -> float:
        if not apairs:
            return 1.0
        if off <= apairs[0][0]:
            return apairs[0][1]
        for (o0, a0), (o1, a1) in zip(apairs, apairs[1:]):
            if o0 <= off <= o1:
                span = o1 - o0
                u = (off - o0) / span if span > 0 else 1.0
                return a0 + (a1 - a0) * u
        return apairs[-1][1]

    base = alpha * (opacity100 / 100.0)
    stops: List[GradientStop] = []
    for i in range(0, len(color_part) - 3, 4):
        off, r, g, b = color_part[i:i + 4]
        stops.append(GradientStop(
            offset=float(off),
            r=max(0, min(255, int(round(r * 255.0)))),
            g=max(0, min(255, int(round(g * 255.0)))),
            b=max(0, min(255, int(round(b * 255.0)))),
            a=_alpha255(base * alpha_at(float(off)))))
    return stops


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def _apply_transform(canvas: Canvas, ks: dict, lf: float) -> float:
    """Apply a Lottie transform block (layer ks or shape tr) to the canvas
    CTM.  Returns the block's opacity factor 0..1 (NOT applied to the
    canvas — the caller composes it into paint alpha)."""
    if not isinstance(ks, dict):
        return 1.0
    p = ks.get("p")
    if isinstance(p, dict) and p.get("s"):          # split x/y position
        px = Animated(p.get("x"), 0.0).scalar_at(lf)
        py = Animated(p.get("y"), 0.0).scalar_at(lf)
    else:
        pv = Animated(p, [0.0, 0.0]).vec_at(lf)
        px = pv[0] if pv else 0.0
        py = pv[1] if len(pv) > 1 else 0.0
    av = Animated(ks.get("a"), [0.0, 0.0]).vec_at(lf)
    ax = av[0] if av else 0.0
    ay = av[1] if len(av) > 1 else 0.0
    sv = Animated(ks.get("s"), [100.0, 100.0]).vec_at(lf)
    sx = (sv[0] if sv else 100.0) / 100.0
    sy = (sv[1] if len(sv) > 1 else (sv[0] if sv else 100.0)) / 100.0
    rot = Animated(ks.get("r"), 0.0).scalar_at(lf)
    if ks.get("sk") is not None and Animated(ks.get("sk"), 0.0).scalar_at(lf):
        _warn_once("skew transform")

    if px or py:
        canvas.translate(px, py)
    if rot:
        canvas.rotate(math.radians(rot))
    if sx != 1.0 or sy != 1.0:
        canvas.scale(sx, sy)
    if ax or ay:
        canvas.translate(-ax, -ay)
    return Animated(ks.get("o"), 100.0).scalar_at(lf) / 100.0


# ---------------------------------------------------------------------------
# Shape-item walk
# ---------------------------------------------------------------------------

_GEOMETRY = {"sh", "el", "rc"}
_STYLES = {"fl", "st", "gf"}
_IGNORED_SILENT = {"tr", "nm", "hd"}


def _draw_items(canvas: Canvas, items: List[dict], lf: float,
                alpha: float) -> None:
    """Render one shape-group item list.  Geometry accumulates; each style
    item paints everything accumulated so far (subset semantics —
    matches bodymovin for the common geometry-then-styles authoring)."""
    canvas.save()
    try:
        tr = next((it for it in items
                   if isinstance(it, dict) and it.get("ty") == "tr"), None)
        if tr is not None:
            alpha *= _apply_transform(canvas, tr, lf)
        if alpha <= 0.0:
            return
        paths: List[PathCommand] = []
        for it in items:
            if not isinstance(it, dict) or it.get("hd"):
                continue
            ty = it.get("ty")
            if ty == "gr":
                _draw_items(canvas, it.get("it") or [], lf, alpha)
            elif ty == "sh":
                paths.extend(_shape_path(AnimatedShape(it.get("ks")).at(lf)))
            elif ty == "el":
                pos = Animated(it.get("p"), [0.0, 0.0]).vec_at(lf)
                size = Animated(it.get("s"), [0.0, 0.0]).vec_at(lf)
                paths.extend(_ellipse_path(
                    pos[0] if pos else 0.0, pos[1] if len(pos) > 1 else 0.0,
                    (size[0] if size else 0.0) / 2.0,
                    (size[1] if len(size) > 1 else 0.0) / 2.0,
                    ccw=int(it.get("d", 1)) == 3))
            elif ty == "rc":
                pos = Animated(it.get("p"), [0.0, 0.0]).vec_at(lf)
                size = Animated(it.get("s"), [0.0, 0.0]).vec_at(lf)
                paths.extend(_rect_path(
                    pos[0] if pos else 0.0, pos[1] if len(pos) > 1 else 0.0,
                    size[0] if size else 0.0,
                    size[1] if len(size) > 1 else 0.0,
                    Animated(it.get("r"), 0.0).scalar_at(lf)))
            elif ty == "fl":
                if not paths:
                    continue
                cval = Animated(it.get("c"), [0, 0, 0]).vec_at(lf)
                o100 = Animated(it.get("o"), 100.0).scalar_at(lf)
                r, g, b, a = _color_rgba(cval, o100, alpha)
                if a > 0:
                    canvas.set_fill_color(r, g, b, a)
                    canvas.fill_path(list(paths))
            elif ty == "gf":
                if not paths:
                    continue
                o100 = Animated(it.get("o"), 100.0).scalar_at(lf)
                stops = _gradient_stops(it.get("g") or {}, o100, alpha)
                if not stops:
                    continue
                sp = Animated(it.get("s"), [0.0, 0.0]).vec_at(lf)
                ep = Animated(it.get("e"), [0.0, 0.0]).vec_at(lf)
                sx = sp[0] if sp else 0.0
                sy = sp[1] if len(sp) > 1 else 0.0
                ex = ep[0] if ep else 0.0
                ey = ep[1] if len(ep) > 1 else 0.0
                if int(it.get("t", 1)) == 2:        # radial
                    radius = math.hypot(ex - sx, ey - sy)
                    canvas.set_radial_gradient(sx, sy, radius, stops)
                else:                               # linear
                    canvas.set_linear_gradient(sx, sy, ex, ey, stops)
                canvas.fill_path(list(paths))
                # gradients replace the fill paint; restore a neutral fill
                canvas.set_fill_color(0, 0, 0, 255)
            elif ty == "st":
                if not paths:
                    continue
                cval = Animated(it.get("c"), [0, 0, 0]).vec_at(lf)
                o100 = Animated(it.get("o"), 100.0).scalar_at(lf)
                width = Animated(it.get("w"), 1.0).scalar_at(lf)
                r, g, b, a = _color_rgba(cval, o100, alpha)
                if a > 0 and width > 0:
                    canvas.set_stroke_color(r, g, b, a)
                    canvas.set_stroke_width(width)
                    canvas.draw_path(list(paths))
            elif ty in _IGNORED_SILENT:
                pass
            else:
                _warn_once(f"shape item ty={ty!r}")
    finally:
        canvas.restore()


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

def _layer_local_frame(layer: dict, f: float) -> float:
    st = float(layer.get("st", 0.0))
    sr = float(layer.get("sr", 1.0)) or 1.0
    return (f - st) / sr


def _apply_parent_chain(canvas: Canvas, lot: LottieAnimation,
                        by_ind: Dict[int, dict], layer: dict,
                        f: float) -> None:
    """Apply ancestor transforms outermost-first (parent opacity does NOT
    inherit in Lottie — transform only)."""
    chain: List[dict] = []
    seen: set = set()
    cur = layer
    while cur.get("parent") is not None:
        pind = int(cur["parent"])
        if pind in seen:                            # defensive: cycles
            break
        seen.add(pind)
        parent = by_ind.get(pind)
        if parent is None:
            break
        chain.append(parent)
        cur = parent
    for parent in reversed(chain):
        _apply_transform(canvas, parent.get("ks") or {},
                         _layer_local_frame(parent, f))


def _hex_color(s: str) -> Tuple[int, int, int]:
    h = str(s).lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore
    except ValueError:
        return (0, 0, 0)


def _draw_layer(canvas: Canvas, lot: LottieAnimation,
                by_ind: Dict[int, dict], layer: dict, f: float,
                depth: int = 0) -> None:
    if layer.get("hd"):
        return
    ip = float(layer.get("ip", lot.ip))
    op = float(layer.get("op", lot.op))
    if f < ip or f > op:                            # op-inclusive sampling
        return
    ty = int(layer.get("ty", 4))
    lf = _layer_local_frame(layer, f)

    canvas.save()
    try:
        _apply_parent_chain(canvas, lot, by_ind, layer, f)
        alpha = _apply_transform(canvas, layer.get("ks") or {}, lf)
        if alpha <= 0.0:
            return
        if ty == 4:                                 # shape layer
            _draw_items(canvas, layer.get("shapes") or [], lf, alpha)
        elif ty == 1:                               # solid
            r, g, b = _hex_color(layer.get("sc", "#000000"))
            canvas.set_fill_color(r, g, b, _alpha255(alpha))
            canvas.fill_rect(0.0, 0.0, float(layer.get("sw", lot.w)),
                             float(layer.get("sh", lot.h)))
        elif ty == 3:                               # null: transform-only
            pass
        elif ty == 0:                               # precomp (no time remap)
            if depth > 8:
                _warn_once("precomp nesting > 8")
                return
            if layer.get("tm") is not None:
                _warn_once("precomp time remap (tm)")
            ref = lot.assets.get(str(layer.get("refId")))
            sub = list((ref or {}).get("layers") or [])
            if sub:
                sub_by_ind = {int(l["ind"]): l for l in sub if "ind" in l}
                canvas.clip_rect(0.0, 0.0,
                                 float(layer.get("w", lot.w)),
                                 float(layer.get("h", lot.h)))
                for sl in reversed(sub):
                    _draw_layer(canvas, lot, sub_by_ind, sl, lf, depth + 1)
        else:
            _warn_once(f"layer ty={ty}")
        if layer.get("masksProperties") or layer.get("tt"):
            _warn_once("masks/mattes")
    finally:
        canvas.restore()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def render_lottie(canvas: Canvas, lot: LottieAnimation, frame: float,
                  rect: Rect, fit: str = "contain") -> None:
    """Render *lot* at *frame* into *rect* through the Canvas contract.

    fit: contain (default, letterboxed) | cover | stretch.
    Pure function of (lot, frame, rect, fit) — no clocks, no state.
    """
    f = lot.clamp_frame(float(frame))
    canvas.save()
    try:
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        sx, sy = rect.w / lot.w, rect.h / lot.h
        if fit == "stretch":
            canvas.translate(rect.x, rect.y)
            if sx != 1.0 or sy != 1.0:
                canvas.scale(sx, sy)
        else:
            s = max(sx, sy) if fit == "cover" else min(sx, sy)
            canvas.translate(rect.x + (rect.w - lot.w * s) / 2.0,
                             rect.y + (rect.h - lot.h * s) / 2.0)
            if s != 1.0:
                canvas.scale(s, s)
        by_ind = {int(l["ind"]): l for l in lot.layers if "ind" in l}
        for layer in reversed(lot.layers):          # first in list = topmost
            _draw_layer(canvas, lot, by_ind, layer, f)
    finally:
        canvas.restore()
