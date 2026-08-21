"""
ComposeUX SVG Rendering ([SPEC:CXC-SVG] [CXC-R-DRAW-SVG])

A platform-independent SVG parser that DECOMPOSES an SVG document into the
existing Layer-1 Canvas drawing contract (paths, rects, circles, text,
fills, strokes, gradients, transforms, clips).  Because it emits only the
one contract, an SVG renders identically on EVERY backend — Skia (crisp
desktop/RPi), the future CanvasKit browser tier, and the CYD (subject to
its primitive subset, [CXC-SVG-TIERS]) — never through a backend-specific
SVG renderer.

This mirrors the Lottie subset evaluator's architecture (render/lottie/):
a hand-written, ZERO-heavy-dependency parser (stdlib `xml.etree` for XML,
a hand-written path-data grammar) that emits Canvas calls.  No AGPL /
copyleft-incompatible SVG library is imported (the same license posture
that rejected python-lottie for the Lottie pass).

Supported subset ([CXC-SVG-C2] element coverage):
  shapes     rect (+rx/ry rounded), circle, ellipse, line, polyline,
             polygon, path (M,L,H,V,C,S,Q,T,A,Z — abs + rel)
  structure  g, defs, use, symbol, viewBox + preserveAspectRatio, nested
             groups + transform (translate/rotate/scale/matrix)
  paint      fill, stroke, stroke-width, opacity, fill-opacity,
             stroke-opacity, currentColor, named + #hex + rgb() colors
  gradients  linearGradient, radialGradient (objectBoundingBox +
             userSpaceOnUse units, stops, href stop inheritance)
  clip       clipPath (bbox approximation — CONTRACT GAP, see renderer)
  text       set_font + draw_text (x/y, font-size/family/weight,
             text-anchor); flattened tspans
  image      <image> data:/file: hrefs via decode_image/load_image

Deferred / degraded (warn once, never crash): fill-rule=evenodd,
sheared matrix()/skew, non-rect path clips, per-tspan repositioning,
CSS <style> selectors, filters/masks/patterns, animation (SMIL).

RENDERER identifies the active implementation ("canvas-contract"); if a
backend gains a native SVG DOM later, swap behind render_svg()/
SvgDocument — the widget, bindings, and tests are renderer-agnostic.
"""

from .model import (SvgDocument, SvgError, mat_apply, mat_mul,
                    parse_transform, serialize_svg_group)
from .canvas_renderer import render_element, render_svg
from .path_data import parse_path

RENDERER = "canvas-contract"

__all__ = ["SvgDocument", "SvgError", "render_svg", "render_element",
           "parse_path", "RENDERER", "parse_transform", "mat_mul", "mat_apply",
           "serialize_svg_group"]
