#!/usr/bin/env python3
"""
CYD Config-Driven Crosscheck — [CXC-RM-M5]

Renders a CYD-class .compose.yaml with the desktop Skia reference using
the SAME semantics as the CYD scene builder (cyd/src/compose_scene.cpp:
absolute pixel_stack layout, engine widget implementations), then
pixel-compares against an on-device capture pushed by the firmware.

This is the config-driven successor to cyd_dashboard_crosscheck.py:
instead of a hand-coded scene, BOTH sides render from the config —
the CYD via the M5 embedded parser, the reference via the engine's
build_leaf factory.

Expected delta class (M2 finding, unchanged): AA coverage differences +
RGB565 quantization.  Anything structural (misplaced/missing widgets)
is a failure.

Usage:
    cd ~/Dropbox/Maker/composeio/python
    python scripts/cyd_config_crosscheck.py <config.compose.yaml> <capture.bmp>

Outputs (next to the capture):
    <capture>_ref.png    the Skia reference render
    <capture>_diff.png   amplified abs-diff visualization
"""

from __future__ import annotations

import os
import sys

import yaml

_here = os.path.dirname(os.path.abspath(__file__))
_pkg_root = os.path.abspath(os.path.join(_here, os.pardir))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.engine.parser import Composition, build_leaf
from composeio.render.engine.bindings import substitute_variables

# ---------------------------------------------------------------------------
# Color / value helpers (mirror compose_scene.cpp)
# ---------------------------------------------------------------------------


def _color(v, default=None):
    if v is None:
        return default
    if isinstance(v, str) and v.startswith("#"):
        h = v[1:]
        vals = [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
        return tuple(vals + [255] * (4 - len(vals)))[:4]
    if isinstance(v, (list, tuple)):
        return tuple(list(v) + [255])[:4] if len(v) == 3 else tuple(v)[:4]
    return default


# ---------------------------------------------------------------------------
# Scene render (CYD scene-builder semantics on the Skia reference)
# ---------------------------------------------------------------------------


def render_reference(config_path: str, out_png: str) -> tuple:
    with open(config_path) as f:
        doc = yaml.safe_load(f)
    doc = substitute_variables(doc)   # [CXC-BIND-VARS], whole-document

    design = doc.get("design", {})
    width = int(design.get("width", 240))
    height = int(design.get("height", 320))
    bg = _color(design.get("background"), (0x1E, 0x1E, 0x2E, 255))
    variables = doc.get("variables", {}) or {}

    canvas = SkiaCanvas()
    canvas.create_surface(width, height)
    canvas.set_fill_color(*bg[:3])
    canvas.fill_rect(0, 0, width, height)

    comp = Composition(root=None)  # build_leaf needs a Composition context

    def draw_children(children, off_x, off_y):
        for spec in children or []:
            if not isinstance(spec, dict):
                continue
            wtype = str(spec.get("type", "unknown"))
            x = off_x + float(spec.get("x", 0))
            y = off_y + float(spec.get("y", 0))
            w = float(spec.get("w", spec.get("width", 100)))
            h = float(spec.get("h", spec.get("height", 60)))

            if wtype == "pixel_stack":
                pbg = _color(spec.get("background"))
                cr = float(spec.get("corner_radius", 0))
                if pbg:
                    canvas.set_fill_color(*pbg)
                    if cr > 0:
                        canvas.fill_round_rect(x, y, w, h, cr)
                    else:
                        canvas.fill_rect(x, y, w, h)
                pbd = _color(spec.get("border"))
                if pbd:
                    canvas.set_stroke_color(*pbd)
                    canvas.set_stroke_width(1)
                    if cr > 0:
                        canvas.stroke_round_rect(x, y, w, h, cr)
                    else:
                        canvas.stroke_rect(x, y, w, h)
                draw_children(spec.get("children"), x, y)
                continue

            widget = build_leaf(dict(spec), comp)

            if wtype == "image" and getattr(widget, "src", ""):
                # Headless: resolve + decode the asset directly (no
                # EngineContext/AssetManager in this harness).
                try:
                    from composeio.resolve import resolve_bytes
                    data = resolve_bytes(widget.src)
                    widget._handle = canvas.decode_image(data)
                    widget._tried = True
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] image {widget.src}: {e}")

            widget.draw(canvas, Rect(x, y, w, h))

    draw_children(doc.get("widgets"), 0.0, 0.0)
    canvas.save_png(out_png)
    return width, height


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare(ref_png: str, capture_path: str, diff_png: str) -> dict:
    import numpy as np
    from PIL import Image

    ref = np.asarray(Image.open(ref_png).convert("RGB"), dtype=np.int16)
    cap = np.asarray(Image.open(capture_path).convert("RGB"), dtype=np.int16)
    if ref.shape != cap.shape:
        raise SystemExit(f"size mismatch: ref {ref.shape} vs capture {cap.shape}")

    # Quantize the reference to RGB565 (what the panel/capture stores)
    q = ref.copy()
    q[..., 0] &= ~7
    q[..., 1] &= ~3
    q[..., 2] &= ~7
    q[..., 0] |= q[..., 0] >> 5
    q[..., 1] |= q[..., 1] >> 6
    q[..., 2] |= q[..., 2] >> 5

    delta = np.abs(q - cap)
    dmax = delta.max(axis=2)

    # AA fringes (glyph edges, arc strokes, gradient rims) are THIN —
    # erode the >32 mask so only SOLID divergent regions survive; those
    # are the structural failures (missing/misplaced widgets).
    mask = (dmax > 32)
    core = (mask[1:-1, 1:-1] & mask[:-2, 1:-1] & mask[2:, 1:-1] &
            mask[1:-1, :-2] & mask[1:-1, 2:])

    stats = {
        "exact_pct": float((dmax == 0).mean() * 100),
        "within8_pct": float((dmax <= 8).mean() * 100),
        "within32_pct": float((dmax <= 32).mean() * 100),
        "mean_delta": float(dmax.mean()),
        "max_delta": int(dmax.max()),
        "solid_divergent_px": int(core.sum()),
        "solid_pct": float(core.sum() * 100.0 / (ref.shape[0] * ref.shape[1])),
    }

    vis = np.clip(delta * 4, 0, 255).astype(np.uint8)
    Image.fromarray(vis).save(diff_png)
    return stats


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    config_path, capture_path = sys.argv[1], sys.argv[2]

    base = os.path.splitext(capture_path)[0]
    ref_png = base + "_ref.png"
    diff_png = base + "_diff.png"

    w, h = render_reference(config_path, ref_png)
    print(f"reference: {ref_png} ({w}x{h})")

    stats = compare(ref_png, capture_path, diff_png)
    print(f"diff:      {diff_png}")
    print(f"exact:     {stats['exact_pct']:.2f}%")
    print(f"within 8:  {stats['within8_pct']:.2f}%   (AA + RGB565 class)")
    print(f"within 32: {stats['within32_pct']:.2f}%")
    print(f"mean/max:  {stats['mean_delta']:.2f} / {stats['max_delta']}")

    # Verdict: the documented delta class (AA + RGB565) is THIN —
    # 1px glyph/arc/rim fringes.  Structural divergence (missing or
    # misplaced widgets) leaves SOLID >32-delta regions, measured by
    # the eroded mask.  Text/arc-dense compositions legitimately have
    # a high raw >32 fringe count.
    # Known non-structural residue inside the solid count: BOLD glyph
    # interiors displaced by <=1px (atlas integer advances vs FreeType
    # fractional accumulation — the M2 text class).  Empirically ~0.6%
    # of frame on the gauge dashboard.  A genuinely missing/misplaced
    # widget is an order of magnitude above 1% (one 40x40 widget alone
    # is ~2% of a 240x320 frame), so gate at 1%.
    fringe_pct = 100.0 - stats["within32_pct"]
    solid = stats["solid_divergent_px"]
    solid_pct = stats["solid_pct"]
    verdict = "PASS" if solid_pct < 1.0 else "CHECK DIFF (structural?)"
    print(f"fringe:    {fringe_pct:.2f}% pixels >32 (thin AA class)")
    print(f"solid:     {solid} px ({solid_pct:.2f}%) in solid >32 regions")
    print(f"verdict:   {verdict}")


if __name__ == "__main__":
    main()
