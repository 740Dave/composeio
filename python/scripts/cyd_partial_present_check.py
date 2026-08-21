#!/usr/bin/env python3
"""
CYD Partial-Present Check — [SPEC:CXC-LINE] [CXC-LINE-DIRTY] C6/C7

Companion to cyd_config_crosscheck.py for the dirty-band lift: given
two on-device captures taken BEFORE and AFTER a single bus publish
that dirties a known widget, assert

  C7  clean bands are byte-stable  — every pixel OUTSIDE the declared
      dirty band range is IDENTICAL across the two captures (no
      global clear, no stray repaint), and
  C6  the dirty bands actually changed — at least `--min-changed`
      pixels differ INSIDE the dirty range (the needle moved).

HONESTY NOTE: /capture.bmp is a fresh re-render (the capture server
streams a full band re-render), NOT a GRAM readback — so this check
proves render determinism + damage containment, and the serial-side
proof of the transfer skip is the [REACT] "repainted N/20 bands" line
plus the canvas transfer counters.  Stale-band GLASS artifacts remain
the C13 human gate.

Workflow (attached CYD running the normal `cyd` firmware):

    curl -s http://<cyd-ip>/capture.bmp -o before.bmp
    mosquitto_pub -h mqtt-broker.local -p 1883 \
        -t 'composeio/global/demo/gauge' \
        -m '{"ns":"demo/gauge","type":"state","val":88,"src":"check","scope":"global"}'
    # wait ~1s for the repaint; note the serial [REACT] band line
    curl -s http://<cyd-ip>/capture.bmp -o after.bmp
    python scripts/cyd_partial_present_check.py before.bmp after.bmp \
        --dirty-bands 2-12

--dirty-bands takes the band range the bound widget occupies (the
serial WIDGET/[REACT] log prints it: "repaint bands B0..B1").  Band N
covers scanlines [N*16, N*16+16).
"""

from __future__ import annotations

import argparse
import sys

from PIL import Image

BAND_H = 16


def parse_bands(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("before", help="capture BEFORE the publish (BMP)")
    ap.add_argument("after", help="capture AFTER the repaint (BMP)")
    ap.add_argument("--dirty-bands", required=True,
                    help="expected dirty band indices, e.g. '14-16' or '3,5-7'")
    ap.add_argument("--min-changed", type=int, default=20,
                    help="minimum changed pixels inside the dirty bands "
                         "(default 20; 0 skips the C6 change assertion)")
    args = ap.parse_args()

    before = Image.open(args.before).convert("RGB")
    after = Image.open(args.after).convert("RGB")
    if before.size != after.size:
        print(f"FAIL: size mismatch {before.size} vs {after.size}")
        return 1

    w, h = before.size
    dirty = parse_bands(args.dirty_bands)
    pb, pa = before.load(), after.load()

    clean_diffs = 0
    dirty_diffs = 0
    first_clean_diff = None
    for y in range(h):
        band = y // BAND_H
        for x in range(w):
            if pb[x, y] != pa[x, y]:
                if band in dirty:
                    dirty_diffs += 1
                else:
                    clean_diffs += 1
                    if first_clean_diff is None:
                        first_clean_diff = (x, y, band)

    n_bands = (h + BAND_H - 1) // BAND_H
    print(f"panel {w}x{h}, {n_bands} bands; dirty bands declared: "
          f"{sorted(dirty)}")
    print(f"  clean-band diffs: {clean_diffs}"
          + (f" (first at {first_clean_diff[:2]}, band "
             f"{first_clean_diff[2]})" if first_clean_diff else ""))
    print(f"  dirty-band diffs: {dirty_diffs}")

    ok = True
    if clean_diffs != 0:
        print("C7 FAIL: clean bands are NOT byte-stable "
              "(global clear or stray repaint)")
        ok = False
    else:
        print("C7 PASS: clean bands byte-stable across the partial present")

    if args.min_changed > 0:
        if dirty_diffs >= args.min_changed:
            print(f"C6 PASS: dirty bands changed ({dirty_diffs} px)")
        else:
            print(f"C6 FAIL: dirty bands changed only {dirty_diffs} px "
                  f"(< {args.min_changed}) — did the publish land?")
            ok = False

    print(f"PARTIAL-PRESENT RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
