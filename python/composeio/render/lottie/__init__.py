"""
ComposeUX Lottie Scene Evaluator ([SPEC:CXC-LOTTIE])

Parses a Lottie (Bodymovin) JSON document and renders any frame of it
THROUGH THE CANVAS CONTRACT — every pixel is rasterized by the active
Layer-1 backend (Skia on the workhorse tier), so Lottie output rides the
same cross-platform pixel-identity guarantee as every other widget
([CXC-R-C1]).  Evaluation is pure arithmetic over the logical frame
number: rendering frame f is a pure function of (document, f), which is
what makes `RenderApp.render_frame_at(t)` goldens byte-identical
macOS<->RPi ([CXC-LOT-DETERMINISM]).

RENDERER SEAM (the [CXC-LOT-RENDER] escalation, 2026-07-07)
-----------------------------------------------------------
The spec names **skottie** (Skia's own Lottie module) as the Skia-tier
renderer.  skia-python (m144, current on macOS AND the RPi tier) does
NOT bind Skottie — upstream issue kyamagu/skia-python#57 open since
2020, zero skottie symbols in the shipped binaries — and the alternative
parser dependency python-lottie is AGPLv3+ (license-incompatible with
ComposeIO's open-reuse posture).  This package is therefore a zero-
dependency Lottie-SUBSET evaluator that emits Canvas-contract calls;
Skia still rasterizes everything.  If a skottie-capable Skia build is
adopted later, swap the implementation behind `render_lottie()` /
`LottieAnimation` — the widget, bindings, and tests are renderer-
agnostic.  RENDERER identifies the active implementation.

Supported subset (sufficient for non-interactive motion per
[CXC-LOT-INTENT]: sweeps, pulses, spinners, flourishes):
  layers   shape (ty 4), solid (ty 1), null (ty 3), precomp (ty 0,
           no time-remap), parenting, in/out points, st/sr
  shapes   group, path, rect, ellipse, fill, gradient fill (linear/
           radial, static stops), stroke, group/layer transforms
  anim     keyframes with cubic-bezier temporal easing, hold frames,
           spatial (to/ti) position interpolation, animated paths
  markers  name -> [start, end] frame ranges (playSegments)
Unsupported features degrade gracefully: warn once, skip the item —
the screen is never broken.
"""

from .model import LottieAnimation, LottieError
from .canvas_renderer import render_lottie

# The active Skia-tier renderer implementation (see module docstring).
RENDERER = "canvas-contract"        # alternative: "skottie" (not bound yet)

__all__ = ["LottieAnimation", "LottieError", "render_lottie", "RENDERER"]
