"""
Lazy PyAV (`av`) support + frame plumbing for the Media Surface
([CXC-RM-MEDIA]).

PyAV is an OPTIONAL / heavy dependency.  This module — and every module
under `widgets/media/` — MUST import cleanly when `av` is absent; decode
just degrades (the widget shows its placeholder, the screen is never
broken).  Import `av` only inside `_require_av()`, never at module load.

Dependency (report only — do NOT add here):
    PyAV — `av>=10.0`  (suggested pyproject optional-extra:
        [project.optional-dependencies]  media = ["av>=10.0"])

Portability: PyAV ships manylinux + macOS wheels bundling FFmpeg, so the
same code decodes H.264/MP4 on macOS (workhorse), Debian x86 (MinnowBoard
'professor'), and RPi.  CYD/ESP32 is NOT a target here (cannot decode
H.264 — see MediaWidget docstring).

v1 compositing path ([CXC-RM-MEDIA]): a decoded RGB frame is encoded to
an in-memory PNG (`rgb_to_png`) and routed through the EXISTING Canvas
contract, `Canvas.decode_image(bytes) -> handle` then
`Canvas.draw_image(handle, ...)`.  The PNG encoder here is pure-stdlib
(zlib), keeping the zero-extra-dep posture of the render tier.

FOLLOW-UP OPTIMIZATION (architect owns the contract, do NOT build here):
a raw-frame Canvas primitive — e.g. `draw_rgb(buf, w, h, x, y, dw, dh)` —
that accepts a decoded RGB buffer directly would remove the
PNG-encode + PNG-decode round-trip per frame (the dominant per-frame cost
at video rates).  Until canvas.py grows that primitive, PNG is the
portable v1 route that works on every existing backend unchanged.
"""

from __future__ import annotations

import logging
import platform
import struct
import zlib
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

_AV: Any = None
_AV_TRIED = False

_LOG = logging.getLogger(__name__)


def av_available() -> bool:
    """True when PyAV can be imported (cached).  Never raises."""
    return _load_av() is not None


def _load_av() -> Any:
    global _AV, _AV_TRIED
    if _AV_TRIED:
        return _AV
    _AV_TRIED = True
    try:
        import av  # noqa: PLC0415 — LAZY heavy import by design
        _AV = av
    except Exception:
        _AV = None
    return _AV


def _require_av() -> Any:
    """Return the `av` module or raise a clear, actionable error."""
    av = _load_av()
    if av is None:
        raise MediaDecodeError(
            "PyAV ('av') is not installed — media decode is unavailable. "
            "Install the optional media extra: pip install 'av>=10.0'"
        )
    return av


class MediaDecodeError(RuntimeError):
    """Raised for decode/open failures (mirrors LottieError's role)."""


# ---------------------------------------------------------------------------
# Hardware decode (hwaccel) OPT-IN  ([CXC-RM-MEDIA] hwaccel; see
# docs/provisioning/minnowboard-vaapi.md §7)
# ---------------------------------------------------------------------------
#
# Software decode is the DEFAULT and stays byte-unchanged: a decoder built
# with hwaccel "none" NEVER touches anything below.  When a caller opts in,
# `make_hwaccel()` resolves the request to a PyAV `HWAccel` (av >= 12) that
# `av.open(..., hwaccel=...)` runs the video codec context on.  The device
# is platform-pure — `vaapi` (Linux/MinnowBoard), `videotoolbox` (macOS),
# `qsv`/`cuda` (others) are the SAME code path with a different device_type.
#
# Post-decode there is NOTHING to change downstream: with `is_hw_owned=False`
# (the PyAV default) FFmpeg transfers each HW-decoded surface back to a CPU
# frame (nv12) before it reaches Python, so the EXISTING `frame_to_rgb`
# reformat("rgb24") -> PNG -> Canvas.decode_image path is unchanged.  (An
# explicit `hwdownload` filter is therefore unnecessary on av 18 — the
# implicit download is the same transfer, one fewer moving part.)
#
# Safety: `allow_software_fallback=True` lets FFmpeg drop to a SW decoder
# for a stream the device can't handle; and a guard here checks the device
# is actually present (`hwdevices_available()`), logging ONCE and returning
# the software path (None) if not — a hwaccel request NEVER crashes decode,
# and `av` absent still degrades to the placeholder exactly as before.

# Best-HW-device preference per platform for hwaccel="auto".  Only a HINT
# ordering — intersected with what the loaded FFmpeg actually exposes — so
# nothing here is hardcoded as required; the first available wins.
_HWACCEL_AUTO_PREF = {
    "Darwin": ("videotoolbox",),
    "Linux": ("vaapi", "qsv", "cuda", "vdpau", "drm"),
    "Windows": ("d3d11va", "dxva2", "qsv", "cuda"),
}

# log-once guard so a fallback line is emitted a single time per request kind
_HWACCEL_LOGGED: set = set()


def hwaccel_devices() -> List[str]:
    """HW device types the loaded PyAV/FFmpeg can decode with.

    Never raises: returns [] when `av` is absent or the HWAccel API is
    missing (av < 12), so callers degrade to software transparently.
    """
    av = _load_av()
    if av is None:
        return []
    try:
        return list(av.codec.hwaccel.hwdevices_available())
    except Exception:
        return []


def _hwaccel_log_once(key: str, msg: str) -> None:
    if key not in _HWACCEL_LOGGED:
        _HWACCEL_LOGGED.add(key)
        _LOG.info("media hwaccel: %s", msg)


def make_hwaccel(request: Any) -> Tuple[Optional[Any], str]:
    """Resolve a hwaccel request to a PyAV HWAccel + the device it uses.

    `request` accepts: "none"/"" (software, default), "auto" (best HW device
    available for this platform, else software), or an explicit device type
    ("vaapi", "videotoolbox", "qsv", "cuda", ...).

    Returns `(hwaccel_or_None, device_type)`:
      * `(None, "none")` for software — the caller must NOT pass a hwaccel to
        av.open (keeps the default path byte-identical).
      * `(HWAccel, "<device>")` when a HW device was resolved and built.

    NEVER raises.  An unavailable/unbuildable device logs once and returns
    the software path — the opt-in degrades, it does not break decode.
    """
    req = str(request or "none").strip().lower()
    if req in ("none", ""):
        return None, "none"

    av = _load_av()
    if av is None:  # decode itself is unavailable; nothing to accelerate
        return None, "none"

    available = hwaccel_devices()
    if req == "auto":
        pref = _HWACCEL_AUTO_PREF.get(platform.system(), ())
        device = next((d for d in pref if d in available),
                      available[0] if available else None)
        if device is None:
            _hwaccel_log_once("auto", "hwaccel='auto' but no HW device is "
                              "available; using software decode")
            return None, "none"
    else:
        device = req
        if device not in available:
            _hwaccel_log_once(device,
                              f"hwaccel {device!r} not available (have "
                              f"{available or 'none'}); using software decode")
            return None, "none"

    try:
        hw = av.codec.hwaccel.HWAccel(device_type=device,
                                      allow_software_fallback=True)
    except Exception as exc:  # noqa: BLE001 — never let opt-in crash decode
        _hwaccel_log_once("build:" + device,
                          f"HWAccel({device!r}) construction failed ({exc}); "
                          "using software decode")
        return None, "none"
    return hw, device


# ---------------------------------------------------------------------------
# The decoded-frame value type crossing the source -> widget seam
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    """One decoded RGB frame (packed rgb24, row-major, no padding).

    `rgb` length == width * height * 3.  `index` is a monotonic decode
    counter; `pts` is presentation time in seconds when the container
    provides it (else None).
    """
    width: int
    height: int
    rgb: bytes
    index: int = 0
    pts: Optional[float] = None


# ---------------------------------------------------------------------------
# PyAV frame -> packed RGB  (strips any line-stride padding)
# ---------------------------------------------------------------------------

def frame_to_rgb(av_frame: Any) -> "tuple[int, int, bytes]":
    """Reformat a PyAV VideoFrame to packed rgb24 bytes (w, h, rgb)."""
    rgb = av_frame.reformat(format="rgb24")
    plane = rgb.planes[0]
    w = int(rgb.width)
    h = int(rgb.height)
    buf = bytes(plane)
    row = w * 3
    stride = int(getattr(plane, "line_size", row))
    if stride == row:
        return w, h, buf
    out = bytearray(row * h)
    for y in range(h):
        src = y * stride
        out[y * row:(y + 1) * row] = buf[src:src + row]
    return w, h, bytes(out)


# ---------------------------------------------------------------------------
# Pure-stdlib RGB -> PNG (v1 route into Canvas.decode_image)
# ---------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return (struct.pack(">I", len(data)) + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))


def rgb_to_png(width: int, height: int, rgb: bytes, *, level: int = 3) -> bytes:
    """Encode packed rgb24 bytes to a PNG byte string (color type 2).

    `level` 3 trades a little size for speed (this runs per frame in v1).
    """
    row = width * 3
    raw = bytearray((row + 1) * height)
    for y in range(height):
        raw[y * (row + 1)] = 0  # filter type 0 (None)
        raw[y * (row + 1) + 1:(y + 1) * (row + 1)] = rgb[y * row:(y + 1) * row]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level)
    return (_PNG_SIG + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b""))
