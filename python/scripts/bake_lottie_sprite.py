#!/usr/bin/env python3
"""
Lottie -> CI16 Sprite Baker for the embedded tier ([CXC-R-C18], M5)

Pre-renders a Lottie animation OFFLINE through the ratified Canvas-
contract evaluator (composeio.render.lottie — the same evaluator the
Skia tier plays live), samples N frames across the loop, and bakes each
into the CYD's CI16 raw format (RGB565, no alpha — sprites are baked
over the destination background color), then pushes the frames to
bus_storage:

    bus://assets/animations/<name>/frame_<i>.ci16

The CYD's SpriteWidget fetches + decodes these at scene build and plays
the sequence with draw_image — no Lottie evaluation on-device, exactly
the [CXC-R-C18] degradation: same authored asset, pre-rendered for the
constrained tier.

Usage:
    cd ${COMPOSEIO_HOME}/python
    python scripts/bake_lottie_sprite.py \
        composeio/render/examples/assets/dradis_lottie.json \
        --name dradis-cyd --size 80 --frames 8 --bg 101318 [--push]
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import urllib.request

_here = os.path.dirname(os.path.abspath(__file__))
_pkg_root = os.path.abspath(os.path.join(_here, os.pardir))
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.lottie import LottieAnimation, render_lottie


def bake(lottie_path: str, name: str, size: int, frames: int,
         bg: tuple, out_dir: str, push: bool, bus_url: str) -> None:
    import json

    with open(lottie_path) as f:
        lot = LottieAnimation(json.load(f))

    os.makedirs(out_dir, exist_ok=True)
    span = lot.op - lot.ip

    for i in range(frames):
        frame = lot.ip + span * i / frames        # evenly across the loop
        canvas = SkiaCanvas()
        canvas.create_surface(size, size)
        canvas.set_fill_color(*bg)
        canvas.fill_rect(0, 0, size, size)
        render_lottie(canvas, lot, frame, Rect(0, 0, size, size), fit="contain")

        png_path = os.path.join(out_dir, f"frame_{i}.png")
        canvas.save_png(png_path)

        # PNG -> CI16 (RGB565 LE)
        from PIL import Image
        img = Image.open(png_path).convert("RGB")
        px = img.load()
        data = bytearray(b"CI16")
        data += struct.pack("<HH", size, size)
        for y in range(size):
            for x in range(size):
                r, g, b = px[x, y]
                c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                data += struct.pack("<H", c)
        ci16_path = os.path.join(out_dir, f"frame_{i}.ci16")
        with open(ci16_path, "wb") as f:
            f.write(data)
        print(f"frame {i}: lottie f={frame:.1f} -> {ci16_path} ({len(data)} B)")

        if push:
            url = f"{bus_url}/api/v1/assets/animations/{name}/frame_{i}.ci16"
            req = urllib.request.Request(url, data=bytes(data), method="PUT")
            with urllib.request.urlopen(req, timeout=15) as r:
                print(f"  PUT {url} -> {r.status}")

    total = frames * (8 + size * size * 2)
    print(f"\n{frames} frames x {size}x{size} = {total} bytes decoded on-device")
    if total > 110 * 1024:
        print("WARNING: decoded total exceeds the classic-CYD heap comfort zone")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("lottie")
    ap.add_argument("--name", required=True)
    ap.add_argument("--size", type=int, default=80)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--bg", default="101318", help="hex RRGGBB baked background")
    ap.add_argument("--out", default="/tmp/sprite_bake")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--bus-url", default=os.environ.get("COMPOSEIO_STORAGE_URL", "http://mqtt-broker.local:8420"))
    args = ap.parse_args()

    bg = tuple(int(args.bg[i:i + 2], 16) for i in (0, 2, 4))
    out_dir = os.path.join(args.out, args.name)
    bake(args.lottie, args.name, args.size, args.frames, bg, out_dir,
         args.push, args.bus_url)


if __name__ == "__main__":
    main()
