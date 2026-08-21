"""
ComposeUX Canvas Contract Conformance Suite (M0)

Spec: [CXC-R-C2] — "Platform backend implements drawing contract: backend
passes drawing primitive test suite (render reference shapes, compare output)".

The scenes below are the single source of truth for what a conforming
backend must draw.  Each scene is a pure function of the abstract Canvas
contract — no backend imports.  A backend is conformant when every scene
it has the primitives for renders pixel-equivalent to the Skia-raster
golden (tolerance handled by pixel_comparison.py, which already knows
about RGB565 quantization for the CYD).

Backends declare capabilities; scenes declare requirements.  The CYD
backend implements a documented subset (no fill_arc / paths / transforms /
draw_image / opacity — M5 work), so those scenes are skipped for it and
reported as SKIP, not FAIL.

Usage:
    # (Re)generate goldens from the reference backend:
    python -m composeio.render.conformance --generate

    # Verify a backend (default: SkiaCanvas raster — a self-check /
    # regression gate for the reference itself):
    python -m composeio.render.conformance --verify

The C++/CYD side runs the same scenes via cyd/ (scene list mirrored in
firmware); its captures are compared with the same goldens through
pixel_comparison.py / cyd_dashboard_crosscheck.py.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from .canvas import (ArcTo, Canvas, Close, CurveTo, GradientStop, LineTo,
                     MoveTo, QuadTo)

SCENE_W, SCENE_H = 320, 240

# Capability tags a backend may lack (mirrors the CYD subset gap list)
CAP_FULL = {"fill_arc", "paths", "transforms", "draw_image", "opacity",
            "round_rect", "gradients", "shadow", "clip_round_rect",
            "vector_text"}
# What the CYD ESP32Canvas implements today (see cyd/include/canvas.h).
# vector_text = rasterizing the bundled Plex TTFs directly; the CYD
# renders text from the pre-baked Plex atlas instead ([CXC-R-DRAW-FONT]).
CAP_CYD = {"round_rect", "gradients", "shadow"}


@dataclass
class Scene:
    name: str
    draw: Callable[[Canvas], None]
    requires: frozenset = frozenset()


def _bg(c: Canvas) -> None:
    c.set_fill_color(0x1E, 0x1E, 0x2E)
    c.fill_rect(0, 0, SCENE_W, SCENE_H)


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

def scene_rects(c: Canvas) -> None:
    _bg(c)
    c.set_fill_color(0x26, 0xA6, 0x9A)
    c.fill_rect(20, 20, 120, 80)
    c.set_fill_color(0xEF, 0x53, 0x50, 160)          # alpha fill
    c.fill_rect(80, 60, 120, 80)                      # overlaps -> blend
    c.set_stroke_color(0xFF, 0xA7, 0x26)
    c.set_stroke_width(3)
    c.stroke_rect(180, 30, 110, 70)
    c.set_stroke_color(0xE0, 0xE0, 0xE0)
    c.set_stroke_width(1)
    c.stroke_rect(30.5, 150.5, 100, 60)               # hairline on half-pixel


def scene_round_rects(c: Canvas) -> None:
    _bg(c)
    c.set_fill_color(0x42, 0xA5, 0xF5)
    c.fill_round_rect(20, 20, 130, 90, 16)
    c.set_stroke_color(0x26, 0xA6, 0x9A)
    c.set_stroke_width(4)
    c.stroke_round_rect(170, 20, 130, 90, 24)
    c.set_fill_color(0xAB, 0x47, 0xBC, 200)
    c.fill_round_rect(90, 130, 140, 80, 40)           # radius = h/2 (pill)


def scene_circles(c: Canvas) -> None:
    _bg(c)
    c.set_fill_color(0x26, 0xA6, 0x9A)
    c.fill_circle(80, 80, 50)
    c.set_stroke_color(0xFF, 0xA7, 0x26)
    c.set_stroke_width(5)
    c.stroke_circle(220, 80, 50)
    c.set_fill_color(0xEF, 0x53, 0x50, 140)
    c.fill_circle(150, 170, 45)                        # alpha blend over bg


def scene_arcs(c: Canvas) -> None:
    _bg(c)
    c.set_stroke_color(0x26, 0xA6, 0x9A)
    c.set_stroke_width(6)
    c.stroke_arc(80, 90, 55, math.radians(-225), math.radians(45))
    c.set_stroke_color(0xFF, 0xA7, 0x26)
    c.set_stroke_width(3)
    c.stroke_arc(230, 90, 55, math.radians(0), math.radians(270))
    c.set_stroke_color(0xE0, 0xE0, 0xE0)
    c.set_stroke_width(10)
    c.stroke_arc(155, 190, 35, math.radians(180), math.radians(360))


def scene_fill_arcs(c: Canvas) -> None:
    _bg(c)
    c.set_fill_color(0x26, 0xA6, 0x9A)
    c.fill_arc(90, 110, 70, math.radians(-90), math.radians(120))
    c.set_fill_color(0xEF, 0x53, 0x50, 180)
    c.fill_arc(230, 110, 70, math.radians(30), math.radians(330))


scene_fill_arcs._requires = {"fill_arc"}


def scene_lines(c: Canvas) -> None:
    _bg(c)
    c.set_stroke_color(0xE0, 0xE0, 0xE0)
    for i, w in enumerate([1, 2, 4, 8]):
        c.set_stroke_width(w)
        y = 30 + i * 30
        c.draw_line(20, y, 300, y)
    c.set_stroke_color(0x26, 0xA6, 0x9A)
    c.set_stroke_width(2)
    for i in range(8):                                 # radial fan
        a = math.radians(i * 22.5)
        c.draw_line(160, 190, 160 + 45 * math.cos(a), 190 - 45 * math.sin(a))


def scene_paths(c: Canvas) -> None:
    _bg(c)
    c.set_fill_color(0xFF, 0xA7, 0x26)
    c.fill_path([MoveTo(40, 120), LineTo(90, 30), LineTo(140, 120), Close()])
    c.set_stroke_color(0x26, 0xA6, 0x9A)
    c.set_stroke_width(3)
    c.draw_path([MoveTo(170, 110), QuadTo(230, 20, 290, 110)])
    c.set_stroke_color(0xE0, 0xE0, 0xE0)
    c.set_stroke_width(2)
    c.draw_path([MoveTo(40, 200),
                 CurveTo(90, 140, 150, 250, 200, 190),
                 ArcTo(30, 30, 0, False, True, 280, 200)])


scene_paths._requires = {"paths"}


def scene_gradients(c: Canvas) -> None:
    _bg(c)
    c.set_linear_gradient(20, 20, 300, 20, [
        GradientStop(0.0, 0x26, 0xA6, 0x9A),
        GradientStop(0.5, 0xFF, 0xA7, 0x26),
        GradientStop(1.0, 0xEF, 0x53, 0x50)])
    c.fill_rect(20, 20, 280, 70)
    c.set_radial_gradient(160, 165, 60, [
        GradientStop(0.0, 0xFF, 0xFF, 0xFF, 255),
        GradientStop(0.6, 0x42, 0xA5, 0xF5, 200),
        GradientStop(1.0, 0x42, 0xA5, 0xF5, 0)])
    c.fill_circle(160, 165, 60)


scene_gradients._requires = {"gradients"}


def scene_shadow_opacity(c: Canvas) -> None:
    _bg(c)
    c.set_shadow(4, 4, 8, (0, 0, 0, 180))
    c.set_fill_color(0x26, 0xA6, 0x9A)
    c.fill_round_rect(30, 30, 110, 70, 10)
    c.clear_shadow()
    c.set_opacity(0.5)
    c.set_fill_color(0xEF, 0x53, 0x50)
    c.fill_circle(230, 65, 45)
    c.set_opacity(1.0)
    c.set_fill_color(0xFF, 0xA7, 0x26)
    c.fill_rect(120, 140, 90, 60)


scene_shadow_opacity._requires = {"shadow", "opacity", "round_rect"}


def scene_transform_clip(c: Canvas) -> None:
    _bg(c)
    c.save()
    c.translate(80, 80)
    c.rotate(math.radians(30))
    c.set_fill_color(0x26, 0xA6, 0x9A)
    c.fill_rect(-40, -25, 80, 50)
    c.restore()
    c.save()
    c.translate(230, 80)
    c.scale(1.5, 0.75)
    c.set_fill_color(0xFF, 0xA7, 0x26)
    c.fill_circle(0, 0, 40)
    c.restore()
    c.save()
    c.clip_rect(60, 150, 200, 60)
    c.set_fill_color(0xEF, 0x53, 0x50)
    c.fill_circle(160, 180, 70)                        # clipped to band
    c.restore()


scene_transform_clip._requires = {"transforms"}


def scene_text(c: Canvas) -> None:
    """Text via the shared bitmap atlas when available (cross-backend
    glyph identity per [CXC-R-DRAW-FONT]); native text otherwise."""
    _bg(c)
    atlas = os.path.join(os.path.dirname(__file__), "font_atlas_data.json")
    if hasattr(c, "enable_bitmap_atlas") and os.path.isfile(atlas):
        c.enable_bitmap_atlas(atlas)
    c.set_fill_color(0xE0, 0xE0, 0xE0)
    c.set_font("sans-serif", 24, "bold")
    c.draw_text("ComposeUX", 160, 50, "center")
    c.set_font("sans-serif", 16, "regular")
    c.draw_text("left aligned", 20, 100, "left")
    c.draw_text("center aligned", 160, 130, "center")
    c.draw_text("right aligned", 300, 160, "right")
    c.set_fill_color(0x26, 0xA6, 0x9A)
    c.set_font("sans-serif", 12, "regular")
    c.draw_text("0123456789 -+.%", 160, 200, "center")
    if hasattr(c, "disable_bitmap_atlas"):
        c.disable_bitmap_atlas()


def scene_text_native(c: Canvas) -> None:
    """Native vector text from the BUNDLED IBM Plex faces — NO atlas.

    This is the [CXC-R-DRAW-FONT] M2 unlock proof: because every desktop
    backend loads the same Plex TTFs via Typeface.MakeFromFile, this
    scene must be pixel-identical across macOS and Linux/aarch64 (the
    M1 known-gap was fontconfig/CoreText resolving different faces).
    """
    _bg(c)
    c.set_fill_color(0xE0, 0xE0, 0xE0)
    c.set_font("sans-serif", 24, "bold")
    c.draw_text("IBM Plex Sans", 160, 44, "center")
    c.set_font("sans-serif", 16, "regular")
    c.draw_text("regular 16px — bundled TTF", 160, 78, "center")
    c.set_font("sans-serif", 16, "medium")
    c.draw_text("medium 16px", 160, 104, "center")
    c.set_fill_color(0x26, 0xA6, 0x9A)
    c.set_font("monospace", 14, "regular")
    c.draw_text("IBM Plex Mono 0123456789", 160, 136, "center")
    c.set_font("monospace", 14, "bold")
    c.draw_text("mono bold {[(=>)]}", 160, 162, "center")
    c.set_fill_color(0xFF, 0xA7, 0x26)
    c.set_font("sans-serif", 11, "regular")
    c.draw_text("pixel-identical on every desktop backend", 160, 200, "center")


scene_text_native._requires = {"vector_text"}


# ---------------------------------------------------------------------------
# SVG scenes ([SPEC:CXC-SVG] [CXC-SVG-C1]/[CXC-SVG-C7]) — an SVG document
# rendered THROUGH the contract (render/svg decomposes it to primitives),
# so the same golden-verified pixels appear on every backend that has the
# primitives.  The "simple" scene uses only rects/circles/lines (CYD
# renders it now); the "complex" scene uses paths/transforms/gradients
# (SKIP on the CYD subset until M5 — degradation, not a crash).
# ---------------------------------------------------------------------------

_SVG_SIMPLE = """<svg xmlns="http://www.w3.org/2000/svg" width="320"
  height="240" viewBox="0 0 320 240">
  <rect x="0" y="0" width="320" height="240" fill="#1e1e2e"/>
  <rect x="20" y="20" width="120" height="80" fill="#26a69a"/>
  <rect x="180" y="30" width="110" height="70" rx="14" fill="none"
    stroke="#ffa726" stroke-width="4"/>
  <circle cx="80" cy="170" r="45" fill="#42a5f5"/>
  <circle cx="230" cy="170" r="45" fill="none" stroke="#ef5350"
    stroke-width="6"/>
  <line x1="20" y1="120" x2="300" y2="120" stroke="#e0e0e0"
    stroke-width="2"/>
</svg>"""

_SVG_COMPLEX = """<svg xmlns="http://www.w3.org/2000/svg" width="320"
  height="240" viewBox="0 0 320 240">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#26a69a"/>
      <stop offset="0.5" stop-color="#ffa726"/>
      <stop offset="1" stop-color="#ef5350"/>
    </linearGradient>
    <radialGradient id="r" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#42a5f5" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect x="0" y="0" width="320" height="240" fill="#1e1e2e"/>
  <rect x="20" y="18" width="280" height="46" fill="url(#g)"/>
  <path d="M30 150 L80 90 L130 150 Z" fill="#ffa726"/>
  <path d="M150 150 Q210 70 270 150" fill="none" stroke="#26a69a"
    stroke-width="3"/>
  <path d="M40 210 C90 160 150 220 200 190" fill="none" stroke="#e0e0e0"
    stroke-width="2"/>
  <g transform="translate(250,175) rotate(20)">
    <rect x="-30" y="-20" width="60" height="40" fill="#ab47bc"
      opacity="0.85"/>
  </g>
  <circle cx="160" cy="150" r="42" fill="url(#r)"/>
  <ellipse cx="70" cy="200" rx="24" ry="14" fill="#5d2f38"
    stroke="#c98f9a" stroke-width="1.5"/>
</svg>"""


def _svg_scene(source: str):
    def draw(c: Canvas) -> None:
        from .svg import SvgDocument, render_svg
        from .widget import Rect
        doc = SvgDocument.from_string(source)
        render_svg(c, doc, Rect(0, 0, SCENE_W, SCENE_H), fit="stretch")
    return draw


scene_svg_simple = _svg_scene(_SVG_SIMPLE)
scene_svg_complex = _svg_scene(_SVG_COMPLEX)


SCENES: List[Scene] = [
    Scene("rects", scene_rects),
    Scene("round_rects", scene_round_rects, frozenset({"round_rect"})),
    Scene("circles", scene_circles),
    Scene("arcs", scene_arcs),
    Scene("fill_arcs", scene_fill_arcs, frozenset({"fill_arc"})),
    Scene("lines", scene_lines),
    Scene("paths", scene_paths, frozenset({"paths"})),
    Scene("gradients", scene_gradients, frozenset({"gradients"})),
    Scene("shadow_opacity", scene_shadow_opacity,
          frozenset({"shadow", "opacity", "round_rect"})),
    Scene("transform_clip", scene_transform_clip, frozenset({"transforms"})),
    Scene("text", scene_text),
    Scene("text_native", scene_text_native, frozenset({"vector_text"})),
    Scene("svg_simple", scene_svg_simple),
    Scene("svg_complex", scene_svg_complex,
          frozenset({"paths", "transforms", "gradients", "opacity"})),
]

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "conformance_golden")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def render_scene(canvas: Canvas, scene: Scene) -> None:
    canvas.create_surface(SCENE_W, SCENE_H)
    scene.draw(canvas)
    canvas.present()


def generate_goldens(golden_dir: str = GOLDEN_DIR) -> List[str]:
    """Render every scene through the reference (Skia raster) backend."""
    from .skia_backend import SkiaCanvas
    os.makedirs(golden_dir, exist_ok=True)
    paths = []
    for scene in SCENES:
        c = SkiaCanvas()
        render_scene(c, scene)
        out = os.path.join(golden_dir, f"{scene.name}.png")
        c.save_png(out)
        paths.append(out)
    return paths


def verify_backend(canvas_factory: Callable[[], Canvas],
                   capabilities: set = CAP_FULL,
                   golden_dir: str = GOLDEN_DIR,
                   out_dir: str | None = None,
                   near_match_gate: float = 99.9) -> Dict[str, dict]:
    """Render every scene through *canvas_factory* and pixel-diff against
    goldens.  Returns {scene: {status, stats}}.  A backend that cannot
    save_png must expose read-back another way (CYD uses its capture path).
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                    os.pardir, os.pardir, "scripts"))
    from pixel_comparison import pixel_equivalency  # noqa: E402

    import tempfile
    out_dir = out_dir or tempfile.mkdtemp(prefix="cxc_conformance_")
    results: Dict[str, dict] = {}

    for scene in SCENES:
        missing = scene.requires - capabilities
        if missing:
            results[scene.name] = {"status": "SKIP",
                                   "missing": sorted(missing)}
            continue
        golden = os.path.join(golden_dir, f"{scene.name}.png")
        if not os.path.isfile(golden):
            results[scene.name] = {"status": "NO_GOLDEN"}
            continue
        c = canvas_factory()
        render_scene(c, scene)
        test_png = os.path.join(out_dir, f"{scene.name}.png")
        c.save_png(test_png)
        scene_out = os.path.join(out_dir, scene.name)
        stats = pixel_equivalency(golden, test_png, output_dir=scene_out)
        status = "PASS" if stats["near_match_pct"] >= near_match_gate else "FAIL"
        results[scene.name] = {"status": status,
                               "near_match_pct": stats["near_match_pct"],
                               "max_delta": stats["max_delta"],
                               "artifacts": scene_out}
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--generate", action="store_true",
                    help="(re)generate golden images from the Skia reference")
    ap.add_argument("--verify", action="store_true",
                    help="verify the Skia raster backend against goldens")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    if args.generate:
        for p in generate_goldens():
            print(f"golden: {p}")
        return 0

    # default / --verify: self-check the reference backend
    from .skia_backend import SkiaCanvas
    results = verify_backend(SkiaCanvas, CAP_FULL, out_dir=args.out_dir)
    failed = 0
    for name, r in results.items():
        line = f"  {name:16s} {r['status']}"
        if "near_match_pct" in r:
            line += f"  near-match={r['near_match_pct']:.2f}%  max_delta={r['max_delta']}"
        if r["status"] == "FAIL":
            failed += 1
        print(line)
    print(f"\n{'FAIL' if failed else 'PASS'}: "
          f"{len(results) - failed}/{len(results)} scenes conformant")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
