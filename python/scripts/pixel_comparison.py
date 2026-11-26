#!/usr/bin/env python3
"""
Pixel Comparison Tool for Cross-Platform Visual QA

Compares a desktop Skia reference render (RGB888 PNG) against a CYD ESP32
capture (BMP or PNG, originally RGB565). Produces:
  1. variance_map.png  -- heatmap of per-pixel divergence
  2. side_by_side.png   -- reference | test | variance, concatenated
  3. comparison_report.txt -- text summary with statistics

RGB565 awareness:
  The CYD renders in RGB565 (5-6-5 bits per channel), then capture-time
  conversion to RGB888 uses bit replication. Even identical visual output
  yields per-channel deltas up to +/-3 from quantization. The tool
  distinguishes quantization noise (<=3) from real rendering differences (>3).

Usage:
    python3 scripts/pixel_comparison.py reference.png test.png [--output-dir DIR]
    python3 scripts/pixel_comparison.py reference.png --from-store URL [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
import time
from typing import Any

from PIL import Image

# ---------------------------------------------------------------------------
# Try numpy for fast pixel math; fall back to pure-PIL path
# ---------------------------------------------------------------------------
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
QUANTIZATION_THRESH = 3   # per-channel delta from RGB565 bit-replication
MODERATE_THRESH = 30       # above this is "significant"

# Heatmap colors (R, G, B)
COLOR_IDENTICAL = (0, 0, 0)         # black
COLOR_QUANTIZATION = (0, 200, 0)    # green  -- within RGB565 noise
COLOR_MODERATE = (255, 230, 0)      # yellow -- delta 4..30
COLOR_SIGNIFICANT = (255, 0, 0)     # red    -- delta 31+


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def pixel_equivalency(
    reference_path: str,
    test_path: str,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """
    Compare two rendered images pixel-by-pixel.

    Args:
        reference_path: Path to desktop Skia reference PNG
        test_path: Path to CYD capture (BMP or PNG)
        output_dir: Where to write output files (default: same dir as test_path)

    Returns dict with:
        - identical_pct: percentage of pixels that are exactly identical
        - near_match_pct: percentage within threshold (+/-3 per channel for RGB565 quantization)
        - max_delta: maximum per-channel difference found
        - mean_delta: average per-channel difference across all pixels
        - problem_areas: list of rectangular regions with highest divergence
    """

    ref_img = Image.open(reference_path).convert("RGB")
    test_img = Image.open(test_path).convert("RGB")

    # Resize test to match reference if dimensions differ
    if ref_img.size != test_img.size:
        print(f"  [resize] test {test_img.size} -> reference {ref_img.size}")
        test_img = test_img.resize(ref_img.size, Image.NEAREST)

    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(test_path))
    os.makedirs(output_dir, exist_ok=True)

    w, h = ref_img.size
    total_pixels = w * h

    if HAS_NUMPY:
        result = _compare_numpy(ref_img, test_img, w, h)
    else:
        result = _compare_pil(ref_img, test_img, w, h)

    # Unpack intermediate arrays
    variance_img: Image.Image = result["variance_img"]
    identical_count: int = result["identical_count"]
    near_match_count: int = result["near_match_count"]
    max_delta: int = result["max_delta"]
    sum_delta: float = result["sum_delta"]
    delta_grid = result["delta_grid"]  # 2D grid of max-channel deltas per pixel

    identical_pct = 100.0 * identical_count / total_pixels
    near_match_pct = 100.0 * near_match_count / total_pixels
    mean_delta = sum_delta / total_pixels

    # Detect problem areas via simple block scan
    problem_areas = _find_problem_areas(delta_grid, w, h)

    # Write outputs
    variance_path = os.path.join(output_dir, "variance_map.png")
    variance_img.save(variance_path)

    side_by_side = Image.new("RGB", (w * 3, h))
    side_by_side.paste(ref_img, (0, 0))
    side_by_side.paste(test_img, (w, 0))
    side_by_side.paste(variance_img, (w * 2, 0))
    sbs_path = os.path.join(output_dir, "side_by_side.png")
    side_by_side.save(sbs_path)

    stats = {
        "identical_pct": round(identical_pct, 2),
        "near_match_pct": round(near_match_pct, 2),
        "max_delta": int(max_delta),
        "mean_delta": round(mean_delta, 4),
        "problem_areas": problem_areas,
        "dimensions": (w, h),
        "total_pixels": total_pixels,
    }

    report_path = os.path.join(output_dir, "comparison_report.txt")
    _write_report(report_path, stats, reference_path, test_path)

    stats["output_files"] = {
        "variance_map": variance_path,
        "side_by_side": sbs_path,
        "report": report_path,
    }

    return stats


# ---------------------------------------------------------------------------
# Numpy-accelerated comparison
# ---------------------------------------------------------------------------

def _compare_numpy(ref_img, test_img, w, h):
    ref = np.array(ref_img, dtype=np.int16)   # (h, w, 3)
    tst = np.array(test_img, dtype=np.int16)

    diff = np.abs(ref - tst)                  # per-channel absolute delta
    max_ch = diff.max(axis=2)                 # max delta across channels per pixel

    identical_mask = max_ch == 0
    near_mask = (max_ch > 0) & (max_ch <= QUANTIZATION_THRESH)
    moderate_mask = (max_ch > QUANTIZATION_THRESH) & (max_ch <= MODERATE_THRESH)
    significant_mask = max_ch > MODERATE_THRESH

    # Build heatmap
    heatmap = np.zeros((h, w, 3), dtype=np.uint8)
    heatmap[identical_mask] = COLOR_IDENTICAL
    heatmap[near_mask] = COLOR_QUANTIZATION
    heatmap[moderate_mask] = COLOR_MODERATE
    heatmap[significant_mask] = COLOR_SIGNIFICANT

    variance_img = Image.fromarray(heatmap)

    return {
        "variance_img": variance_img,
        "identical_count": int(identical_mask.sum()),
        "near_match_count": int(identical_mask.sum() + near_mask.sum()),
        "max_delta": int(max_ch.max()),
        "sum_delta": float(max_ch.sum()),
        "delta_grid": max_ch,   # numpy array (h, w)
    }


# ---------------------------------------------------------------------------
# Pure-PIL fallback
# ---------------------------------------------------------------------------

def _compare_pil(ref_img, test_img, w, h):
    ref_px = ref_img.load()
    tst_px = test_img.load()

    variance_img = Image.new("RGB", (w, h))
    var_px = variance_img.load()

    identical_count = 0
    near_match_count = 0
    max_delta = 0
    sum_delta = 0.0
    delta_grid = [[0] * w for _ in range(h)]

    for y in range(h):
        for x in range(w):
            r1, g1, b1 = ref_px[x, y]
            r2, g2, b2 = tst_px[x, y]
            dr = abs(r1 - r2)
            dg = abs(g1 - g2)
            db = abs(b1 - b2)
            d = max(dr, dg, db)

            delta_grid[y][x] = d
            sum_delta += d
            if d > max_delta:
                max_delta = d

            if d == 0:
                identical_count += 1
                near_match_count += 1
                var_px[x, y] = COLOR_IDENTICAL
            elif d <= QUANTIZATION_THRESH:
                near_match_count += 1
                var_px[x, y] = COLOR_QUANTIZATION
            elif d <= MODERATE_THRESH:
                var_px[x, y] = COLOR_MODERATE
            else:
                var_px[x, y] = COLOR_SIGNIFICANT

    return {
        "variance_img": variance_img,
        "identical_count": identical_count,
        "near_match_count": near_match_count,
        "max_delta": max_delta,
        "sum_delta": sum_delta,
        "delta_grid": delta_grid,
    }


# ---------------------------------------------------------------------------
# Problem-area detection (block-scan)
# ---------------------------------------------------------------------------

def _find_problem_areas(delta_grid, w, h, block_size=16, top_n=5):
    """
    Scan the image in non-overlapping blocks. Return the top-N blocks by
    mean delta (only blocks with mean delta > QUANTIZATION_THRESH).
    """
    blocks = []

    if HAS_NUMPY and isinstance(delta_grid, np.ndarray):
        for by in range(0, h, block_size):
            for bx in range(0, w, block_size):
                bh = min(block_size, h - by)
                bw = min(block_size, w - bx)
                region = delta_grid[by:by + bh, bx:bx + bw]
                mean_d = float(region.mean())
                max_d = int(region.max())
                if mean_d > QUANTIZATION_THRESH:
                    blocks.append({
                        "rect": {"x": int(bx), "y": int(by), "w": int(bw), "h": int(bh)},
                        "mean_delta": round(mean_d, 2),
                        "max_delta": max_d,
                    })
    else:
        for by in range(0, h, block_size):
            for bx in range(0, w, block_size):
                bh = min(block_size, h - by)
                bw = min(block_size, w - bx)
                total = 0.0
                local_max = 0
                count = 0
                for y in range(by, by + bh):
                    for x in range(bx, bx + bw):
                        d = delta_grid[y][x]
                        total += d
                        if d > local_max:
                            local_max = d
                        count += 1
                mean_d = total / count if count else 0
                if mean_d > QUANTIZATION_THRESH:
                    blocks.append({
                        "rect": {"x": bx, "y": by, "w": bw, "h": bh},
                        "mean_delta": round(mean_d, 2),
                        "max_delta": local_max,
                    })

    blocks.sort(key=lambda b: b["mean_delta"], reverse=True)
    return blocks[:top_n]


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _write_report(path, stats, ref_path, test_path):
    w, h = stats["dimensions"]
    lines = [
        "=" * 60,
        "PIXEL COMPARISON REPORT",
        "=" * 60,
        "",
        f"Reference : {ref_path}",
        f"Test      : {test_path}",
        f"Dimensions: {w} x {h}  ({stats['total_pixels']:,} pixels)",
        "",
        "--- Summary ---",
        f"Identical pixels : {stats['identical_pct']:6.2f}%",
        f"Near-match (<=3) : {stats['near_match_pct']:6.2f}%  (includes identical; RGB565 quantization OK)",
        f"Max delta        : {stats['max_delta']}",
        f"Mean delta       : {stats['mean_delta']:.4f}",
        "",
    ]

    if stats["near_match_pct"] >= 99.9:
        lines.append("VERDICT: PASS -- images are visually equivalent (RGB565 quantization only)")
    elif stats["near_match_pct"] >= 95.0:
        lines.append("VERDICT: MARGINAL -- mostly equivalent with minor rendering differences")
    else:
        lines.append("VERDICT: FAIL -- significant rendering differences detected")

    lines.append("")

    if stats["problem_areas"]:
        lines.append("--- Problem Areas (top blocks with mean delta > 3) ---")
        for i, area in enumerate(stats["problem_areas"], 1):
            r = area["rect"]
            lines.append(
                f"  #{i}: ({r['x']},{r['y']}) {r['w']}x{r['h']}  "
                f"mean_delta={area['mean_delta']}  max_delta={area['max_delta']}"
            )
    else:
        lines.append("No problem areas detected (all blocks within RGB565 quantization noise).")

    lines.extend(["", "=" * 60])
    report = "\n".join(lines)

    with open(path, "w") as f:
        f.write(report + "\n")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Pixel comparison tool for cross-platform visual QA (desktop Skia vs CYD ESP32)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 scripts/pixel_comparison.py ref.png test.png --output-dir /tmp/cmp/
              python3 scripts/pixel_comparison.py ref.png --from-store http://host:8420/api/v1/blob/captures/cyd/6891.bmp
        """),
    )
    parser.add_argument("reference", help="Path to desktop Skia reference PNG")
    parser.add_argument("test", nargs="?", default=None, help="Path to CYD capture (BMP or PNG)")
    # document_store is RETIRED ([SPEC:CXC-STORE]); the capture is a bus:// blob on the ONE
    # store, e.g. http://host:8420/api/v1/blob/captures/cyd/<id>.bmp. `--from-docstore` is
    # retained as a back-compat alias for `--from-store`.
    parser.add_argument("--from-store", "--from-docstore", dest="docstore_url", default=None,
                        help="Fetch the CYD capture from a store URL instead of a local file")
    parser.add_argument("--output-dir", dest="output_dir", default=None,
                        help="Directory for output files (default: same dir as test image)")
    args = parser.parse_args()

    # Resolve test image
    if args.docstore_url:
        import urllib.request
        import tempfile
        url = args.docstore_url
        # Determine extension from URL
        ext = ".bmp" if url.lower().endswith(".bmp") else ".png"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
        os.close(tmp_fd)
        print(f"Fetching CYD capture from {url} ...")
        urllib.request.urlretrieve(url, tmp_path)
        # Convert BMP to PNG if needed
        if ext == ".bmp":
            png_path = tmp_path.replace(".bmp", ".png")
            Image.open(tmp_path).save(png_path)
            os.unlink(tmp_path)
            test_path = png_path
        else:
            test_path = tmp_path
        print(f"  -> saved to {test_path}")
    elif args.test:
        test_path = args.test
    else:
        parser.error("Provide either a test image path or --from-docstore URL")
        return

    reference_path = args.reference

    if not os.path.isfile(reference_path):
        print(f"ERROR: reference image not found: {reference_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(test_path):
        print(f"ERROR: test image not found: {test_path}", file=sys.stderr)
        sys.exit(1)

    print(f"\nPixel Comparison: desktop Skia vs CYD capture")
    print(f"  Reference: {reference_path}")
    print(f"  Test:      {test_path}")
    print(f"  Backend:   {'numpy' if HAS_NUMPY else 'pure-PIL (numpy not available)'}")
    print()

    t0 = time.monotonic()
    stats = pixel_equivalency(reference_path, test_path, output_dir=args.output_dir)
    elapsed = time.monotonic() - t0

    print(f"--- Results ({elapsed:.2f}s) ---")
    print(f"  Identical pixels : {stats['identical_pct']:.2f}%")
    print(f"  Near-match (<=3) : {stats['near_match_pct']:.2f}%")
    print(f"  Max delta        : {stats['max_delta']}")
    print(f"  Mean delta       : {stats['mean_delta']:.4f}")
    print()

    if stats["near_match_pct"] >= 99.9:
        print("  VERDICT: PASS")
    elif stats["near_match_pct"] >= 95.0:
        print("  VERDICT: MARGINAL")
    else:
        print("  VERDICT: FAIL")

    if stats["problem_areas"]:
        print()
        print(f"  Problem areas ({len(stats['problem_areas'])}):")
        for i, area in enumerate(stats["problem_areas"], 1):
            r = area["rect"]
            print(f"    #{i}: ({r['x']},{r['y']}) {r['w']}x{r['h']}  "
                  f"mean={area['mean_delta']}  max={area['max_delta']}")

    print()
    print("Output files:")
    for name, path in stats["output_files"].items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
