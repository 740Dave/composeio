#!/usr/bin/env python3
"""
Generate the hand-authored dradis Lottie asset ([SPEC:CXC-LOTTIE]
[CXC-LOT-C5] flagship demo) — self-contained, no license-encumbered
sources.  Reproducible: rerunning writes byte-identical JSON.

    python3 scripts/generate_dradis_lottie.py

Design notes:
- 256x256, fr 60, op 180.  Markers: "sweep" [0,120] (one full rotation)
  and "calibrate" [120,180] (an eased +40 deg wobble and back).
- The sweep is a gradient-faded 40 deg wedge + a bright leading needle,
  rotating via an animated layer transform — the interpolation is
  engine-owned; peers publish transport state only.
- DETERMINISM ([CXC-LOT-C1] finding, 2026-07-07): circular rings are
  authored as annulus FILLS (paired `el` shapes, inner one `d: 3`
  reversed) rather than stroked ellipses.  Fills rasterize bit-exactly
  across the macOS-arm64 and linux-aarch64 skia-python builds; STROKED
  cubic contours can differ by +-1 on isolated pixels (cross-compiler
  divergence inside Skia's stroker — it would hit skottie identically).
  Straight-line strokes (needle, crosshair) are stable.
"""

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.abspath(os.path.join(
    HERE, os.pardir, "composeio", "render", "examples", "assets",
    "dradis_lottie.json"))

C = 128.0          # center
R = 100.0          # sweep radius
WEDGE_DEG = 40.0

LIN = {"i": {"x": [1], "y": [1]}, "o": {"x": [0], "y": [0]}}
EASE = {"i": {"x": [0.4], "y": [1]}, "o": {"x": [0.6], "y": [0]}}


def r3(v):
    return [round(x, 3) for x in v]


def tr(o=100):
    return {"ty": "tr", "a": {"a": 0, "k": [0, 0]}, "p": {"a": 0, "k": [0, 0]},
            "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
            "o": {"a": 0, "k": o}}


def ellipse(cx, cy, d, reverse=False):
    e = {"ty": "el", "p": {"a": 0, "k": [cx, cy]},
         "s": {"a": 0, "k": [round(d, 3), round(d, 3)]}}
    if reverse:
        e["d"] = 3
    return e


def annulus_ring(cx, cy, radius, width, color, opacity=100):
    """A filled ring: outer ellipse + reversed inner ellipse + fill.
    Bit-exact across platforms where a stroked ellipse is not."""
    hw = width / 2.0
    return {"ty": "gr", "nm": f"ring r{radius}", "it": [
        ellipse(cx, cy, 2 * (radius + hw)),
        ellipse(cx, cy, 2 * (radius - hw), reverse=True),
        {"ty": "fl", "c": {"a": 0, "k": color}, "o": {"a": 0, "k": opacity}},
        tr()]}


def build() -> dict:
    # -- sweep wedge geometry (one cubic approximates the 40 deg arc) -----
    phi = math.radians(WEDGE_DEG)
    lead = [C, C - R]
    trail = [C - R * math.sin(phi), C - R * math.cos(phi)]
    k = (4.0 / 3.0) * math.tan(phi / 4.0) * R
    o_lead = [-k, 0.0]
    i_trail = [k * math.cos(phi), -k * math.sin(phi)]

    wedge = {"ty": "sh", "nm": "wedge",
             "ks": {"a": 0, "k": {
                 "c": True,
                 "v": [r3([C, C]), r3(lead), r3(trail)],
                 "i": [[0, 0], [0, 0], r3(i_trail)],
                 "o": [[0, 0], r3(o_lead), [0, 0]]}}}
    gs = [C, C - R / 2.0]
    ge = [(C + trail[0]) / 2.0, (C + trail[1]) / 2.0]
    wedge_fill = {"ty": "gf", "nm": "wedge fade", "t": 1,
                  "s": {"a": 0, "k": r3(gs)}, "e": {"a": 0, "k": r3(ge)},
                  "o": {"a": 0, "k": 100},
                  "g": {"p": 2, "k": {"a": 0, "k": [
                      0.0, 0.24, 0.88, 0.42,
                      1.0, 0.24, 0.88, 0.42,
                      0.0, 0.55, 1.0, 0.0]}}}
    needle = {"ty": "sh", "nm": "needle",
              "ks": {"a": 0, "k": {"c": False,
                                   "v": [[C, C], [C, C - R]],
                                   "i": [[0, 0], [0, 0]],
                                   "o": [[0, 0], [0, 0]]}}}
    needle_stroke = {"ty": "st", "nm": "needle stroke",
                     "c": {"a": 0, "k": [0.35, 1.0, 0.55]},
                     "o": {"a": 0, "k": 100}, "w": {"a": 0, "k": 2.5}}

    sweep_layer = {
        "ddd": 0, "ind": 1, "ty": 4, "nm": "sweep", "sr": 1, "st": 0,
        "ip": 0, "op": 180,
        "ks": {"a": {"a": 0, "k": [C, C]}, "p": {"a": 0, "k": [C, C]},
               "s": {"a": 0, "k": [100, 100]}, "o": {"a": 0, "k": 100},
               "r": {"a": 1, "k": [
                   {"t": 0, "s": [0], **LIN},
                   {"t": 120, "s": [360], **EASE},
                   {"t": 150, "s": [400], **EASE},
                   {"t": 180, "s": [360]}]}},
        "shapes": [{"ty": "gr", "nm": "sweep group",
                    "it": [wedge, wedge_fill, needle, needle_stroke, tr()]}],
    }

    def blip(ind, pos, t0):
        return {
            "ddd": 0, "ind": ind, "ty": 4, "nm": f"blip{ind}", "sr": 1,
            "st": 0, "ip": 0, "op": 180,
            "ks": {"a": {"a": 0, "k": [0, 0]}, "p": {"a": 0, "k": pos},
                   "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
                   "o": {"a": 1, "k": [
                       {"t": 0, "s": [0], **LIN},
                       {"t": t0, "s": [0], **EASE},
                       {"t": t0 + 8, "s": [100], **EASE},
                       {"t": t0 + 70, "s": [10], **EASE},
                       {"t": t0 + 90, "s": [0], "h": 1},
                       {"t": 180, "s": [0]}]}},
            "shapes": [{"ty": "gr", "nm": "dot", "it": [
                ellipse(0, 0, 8),
                {"ty": "fl", "c": {"a": 0, "k": [0.35, 1.0, 0.55]},
                 "o": {"a": 0, "k": 100}},
                tr()]}],
        }

    ring_color = [0.122, 0.353, 0.180]
    grid_layer = {
        "ddd": 0, "ind": 4, "ty": 4, "nm": "grid", "sr": 1, "st": 0,
        "ip": 0, "op": 180,
        "ks": {"a": {"a": 0, "k": [0, 0]}, "p": {"a": 0, "k": [0, 0]},
               "s": {"a": 0, "k": [100, 100]}, "o": {"a": 0, "k": 100},
               "r": {"a": 0, "k": 0}},
        "shapes": [
            {"ty": "gr", "nm": "bg", "it": [
                ellipse(C, C, 2 * R),
                {"ty": "gf", "t": 2,
                 "s": {"a": 0, "k": [C, C]}, "e": {"a": 0, "k": [C + R, C]},
                 "o": {"a": 0, "k": 100},
                 "g": {"p": 2, "k": {"a": 0, "k": [
                     0.0, 0.055, 0.102, 0.063,
                     1.0, 0.024, 0.055, 0.031]}}},
                tr()]},
            # deterministic annulus-fill rings (see module docstring)
            annulus_ring(C, C, 100.0, 1.5, ring_color),
            annulus_ring(C, C, 66.5, 1.5, ring_color),
            annulus_ring(C, C, 33.0, 1.5, ring_color),
            {"ty": "gr", "nm": "crosshair", "it": [
                {"ty": "sh", "ks": {"a": 0, "k": {"c": False,
                                                  "v": [[C, C - R], [C, C + R]],
                                                  "i": [[0, 0], [0, 0]],
                                                  "o": [[0, 0], [0, 0]]}}},
                {"ty": "sh", "ks": {"a": 0, "k": {"c": False,
                                                  "v": [[C - R, C], [C + R, C]],
                                                  "i": [[0, 0], [0, 0]],
                                                  "o": [[0, 0], [0, 0]]}}},
                {"ty": "st", "c": {"a": 0, "k": ring_color},
                 "o": {"a": 0, "k": 60}, "w": {"a": 0, "k": 1.0}},
                tr()]},
            annulus_ring(C, C, 104.0, 2.0, [0.180, 0.490, 0.275]),  # bezel
        ],
    }

    return {
        "v": "5.7.0", "nm": "dradis", "fr": 60, "ip": 0, "op": 180,
        "w": 256, "h": 256, "ddd": 0, "assets": [],
        "markers": [
            {"cm": "sweep", "tm": 0, "dr": 120},
            {"cm": "calibrate", "tm": 120, "dr": 60},
        ],
        "layers": [sweep_layer, blip(2, [168, 88], 20),
                   blip(3, [92, 172], 65), grid_layer],
    }


if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(build(), f, indent=1)
        f.write("\n")
    print("wrote", OUT)
