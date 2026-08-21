#!/usr/bin/env python3
"""png_to_bmp32.py — convert a PNG (with alpha) to a 32bpp BGRA BMP that the
ComposeUX ESP32 / Qualia canvas `decode_image()` reads.

The on-device canvas decodes **BMP24/32 + CI16 only — NOT PNG** (and its CI16
path is opaque RGB565, so alpha needs BMP32). So icons/artwork are converted to
BMP32 here, pushed to bus_storage, and referenced from a `.compose.yaml` image
widget's `src: bus://assets/...` (the device http_fetches + decodes them at
scene build; config-only, no reflash).

Byte format (matches canvas_qualia.cpp decode_image): 14-byte BMP header +
40-byte BITMAPINFOHEADER (bpp=32, BI_RGB) + bottom-up pixel rows, each pixel
B,G,R,A (decode reads p[0]=b, p[1]=g, p[2]=r, p[3]=alpha).

  python3 png_to_bmp32.py in.png out.bmp [square_size]

Then, e.g.:
  from composeio.resolve import put_bytes
  put_bytes("bus://assets/icons/dradis/basestar.bmp", open("out.bmp","rb").read())
"""
import struct
import sys

from PIL import Image


def png_to_bmp32(src, out, size=None):
    im = Image.open(src).convert("RGBA")
    if size:
        im = im.resize((int(size), int(size)), Image.LANCZOS)
    w, h = im.size
    px = im.load()
    data = bytearray()
    for y in range(h - 1, -1, -1):          # bottom-up rows
        for x in range(w):
            r, g, b, a = px[x, y]
            data += bytes((b, g, r, a))       # BGRA
    px_off = 54
    hdr = b"BM" + struct.pack("<IHHI", px_off + len(data), 0, 0, px_off)
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(data), 2835, 2835, 0, 0)
    with open(out, "wb") as f:
        f.write(hdr + dib + bytes(data))
    return w, h, px_off + len(data)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: png_to_bmp32.py in.png out.bmp [square_size]")
    sz = sys.argv[3] if len(sys.argv) > 3 else None
    w, h, n = png_to_bmp32(sys.argv[1], sys.argv[2], sz)
    print(f"{sys.argv[2]}: {w}x{h} BMP32, {n} bytes")
