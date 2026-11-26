"""
Module-local self-test for the Media Surface ([CXC-RM-MEDIA]).

Two tiers, reported separately (per the build's two-tier verdict):

  MACHINE-GREEN  — synthesize a tiny H.264/MP4 clip with PyAV, decode it
                   through FileSource + MediaWidget, and assert frames
                   advance, dimensions are correct, and a composited image
                   handle is produced per frame through the REAL Canvas
                   contract call sequence (decode_image -> draw_image).
                   If PyAV is unavailable, this tier is SKIPPED and FLAGGED,
                   and the structural tier still runs on a mock decoder.

  GLASS-PENDING  — CameraSource / NetworkSource wired to a MOCK decoder:
                   assert the transport wiring (open/read/close) and the
                   tick -> frame -> composite path, with no hardware or
                   network.  On-screen playback + real camera/rtsp remain
                   glass-pending (need eyes on a display / real devices).

Run:  python -m composeio.render.widgets.media --selftest
"""

from __future__ import annotations

import os
import tempfile
from fractions import Fraction
from typing import Any, List, Optional

from ...widget import Rect
from .pyav_support import Frame, av_available, hwaccel_devices, make_hwaccel
from .sources import (
    CameraSource,
    Decoder,
    FileSource,
    NetworkSource,
    make_source,
)
from .widget import MediaWidget


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _RecordingCanvas:
    """Duck-typed Canvas that records decode_image / draw_image.

    Not a Canvas subclass (avoids implementing every abstractmethod); the
    widget only calls the handful of methods stubbed here.
    """

    def __init__(self) -> None:
        self.decoded: List[bytes] = []
        self.drawn: List[tuple] = []

    def decode_image(self, data: bytes) -> Any:
        self.decoded.append(data)
        return ("handle", len(self.decoded), len(data))

    def draw_image(self, handle: Any, x: float, y: float,
                   w: float, h: float) -> None:
        self.drawn.append((handle, x, y, w, h))

    # chrome no-ops
    def save(self) -> None: ...
    def restore(self) -> None: ...
    def set_fill_color(self, *a: Any) -> None: ...
    def fill_rect(self, *a: Any) -> None: ...
    def set_stroke_color(self, *a: Any) -> None: ...
    def set_stroke_width(self, *a: Any) -> None: ...
    def stroke_rect(self, *a: Any) -> None: ...
    def clip_rect(self, *a: Any) -> None: ...
    def fill_path(self, *a: Any) -> None: ...
    def set_font(self, *a: Any) -> None: ...
    def draw_text(self, *a: Any) -> None: ...


class MockDecoder(Decoder):
    """Scripted decoder: emits a fixed list of Frames, logs its calls."""

    def __init__(self, frames: List[Frame], *,
                 width: int = 64, height: int = 48, fps: float = 15.0) -> None:
        self._frames = list(frames)
        self.width = width
        self.height = height
        self.fps = fps
        self.events: List[str] = []
        self._i = 0

    def open(self) -> None:
        self.events.append("open")
        self._i = 0

    def read(self) -> Optional[Frame]:
        self.events.append("read")
        if self._i >= len(self._frames):
            return None
        frame = self._frames[self._i]
        self._i += 1
        return frame

    def close(self) -> None:
        self.events.append("close")


# ---------------------------------------------------------------------------
# Synthetic H.264/MP4 clip
# ---------------------------------------------------------------------------

def _make_rgb_frame(av: Any, w: int, h: int, color: tuple) -> Any:
    """Build a solid-color rgb24 VideoFrame without numpy (stride-aware)."""
    frame = av.VideoFrame(w, h, "rgb24")
    plane = frame.planes[0]
    stride = int(getattr(plane, "line_size", w * 3))
    row = bytes(color) * w
    buf = bytearray(stride * h)
    for y in range(h):
        buf[y * stride:y * stride + len(row)] = row
    plane.update(bytes(buf))
    return frame


def _encode_clip(path: str, *, w: int, h: int, fps: int,
                 colors: List[tuple]) -> str:
    """Encode solid-color frames to an H.264/MP4 file. Returns the codec."""
    import av
    container = av.open(path, mode="w")
    for codec in ("libx264", "h264", "mpeg4"):
        try:
            stream = container.add_stream(codec, rate=fps)
            break
        except Exception:
            stream = None
    if stream is None:  # pragma: no cover
        container.close()
        raise RuntimeError("no usable video encoder in this PyAV build")
    stream.width = w
    stream.height = h
    stream.pix_fmt = "yuv420p"
    stream.codec_context.time_base = Fraction(1, fps)
    for i, color in enumerate(colors):
        frame = _make_rgb_frame(av, w, h, color)
        frame.pts = i
        frame.time_base = Fraction(1, fps)
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():  # flush
        container.mux(packet)
    container.close()
    return stream.codec_context.name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _drive(widget: MediaWidget, canvas: _RecordingCanvas, *,
           n_ticks: int, dt: float, rect: Rect) -> List[int]:
    """Tick + draw n times; return the frame index observed at each tick."""
    observed: List[int] = []
    now = 0.0
    for _ in range(n_ticks):
        widget.on_clock_tick(now)
        widget.draw(canvas, rect)
        observed.append(widget._frame.index if widget._frame else -1)
        now += dt
    return observed


def test_file_source_machine_green() -> str:
    """MACHINE-GREEN: real H.264 decode file -> frames -> composited handles."""
    if not av_available():
        return ("SKIP (PyAV absent) — FLAG: file-decode machine-green "
                "unavailable on this host; ran structural mock only below")

    w, h, fps = 96, 64, 12
    colors = [(200, 30, 30), (30, 200, 30), (30, 30, 200),
              (200, 200, 30), (30, 200, 200), (200, 30, 200)]
    tmp = tempfile.mkdtemp(prefix="cxc_media_")
    path = os.path.join(tmp, "clip.mp4")
    codec = _encode_clip(path, w=w, h=h, fps=fps, colors=colors)
    assert os.path.getsize(path) > 0, "encoded clip is empty"

    widget = MediaWidget("m_file", src="file://" + path,
                         width=w, height=h, fit="stretch", loop=False)
    canvas = _RecordingCanvas()
    interval = 1.0 / fps
    observed = _drive(widget, canvas, n_ticks=len(colors) + 3,
                      dt=interval * 1.001, rect=Rect(0, 0, w, h))

    # frames advance: strictly increasing distinct indices, covering the clip
    distinct = sorted(set(i for i in observed if i >= 0))
    assert distinct[0] == 0, f"first frame index != 0: {distinct}"
    assert len(distinct) >= len(colors) - 1, \
        f"expected ~{len(colors)} frames, advanced through {distinct}"
    assert distinct == list(range(distinct[0], distinct[-1] + 1)), \
        f"frame indices not monotonic/contiguous: {distinct}"

    # dimensions correct (decoded frame matches the encoded clip)
    assert widget._frame is not None
    assert (widget._frame.width, widget._frame.height) == (w, h), \
        f"decoded dims {widget._frame.width}x{widget._frame.height} != {w}x{h}"

    # a composited image handle produced per frame via decode_image->draw_image
    assert len(canvas.decoded) >= len(distinct), \
        f"only {len(canvas.decoded)} handles for {len(distinct)} frames"
    assert len(canvas.drawn) >= len(distinct), "draw_image not called per frame"
    # the PNG route really ran: first payload is a PNG
    assert canvas.decoded[0][:8] == b"\x89PNG\r\n\x1a\n", "not a PNG payload"

    widget.on_unmount()
    return (f"PASS — codec={codec}, {len(distinct)} frames advanced "
            f"{distinct[0]}..{distinct[-1]}, dims {w}x{h}, "
            f"{len(canvas.decoded)} PNG->decode_image handles, "
            f"{len(canvas.drawn)} draw_image calls")


def test_file_source_via_mock() -> str:
    """Structural fallback for the file path (no PyAV): MediaWidget +
    injected FileSource+MockDecoder still advances + composites."""
    frames = [Frame(80, 60, b"\x10\x20\x30" * (80 * 60), index=i)
              for i in range(4)]
    src = FileSource("file://nonexistent.mp4")
    src.set_decoder(MockDecoder(frames, width=80, height=60, fps=10))
    widget = MediaWidget("m_file_mock", width=80, height=60, loop=False)
    widget.set_source_object(src)
    canvas = _RecordingCanvas()
    observed = _drive(widget, canvas, n_ticks=6, dt=0.1001,
                      rect=Rect(0, 0, 80, 60))
    distinct = sorted(set(i for i in observed if i >= 0))
    assert distinct == [0, 1, 2, 3], f"mock file frames: {distinct}"
    assert len(canvas.decoded) >= 4, "handles not produced on mock file path"
    return f"PASS — mock file path advanced {distinct}, {len(canvas.decoded)} handles"


def test_camera_source_structural() -> str:
    """GLASS-PENDING: CameraSource wiring via a mock decoder."""
    frames = [Frame(64, 48, b"\x00\x80\xff" * (64 * 48), index=i)
              for i in range(3)]
    mock = MockDecoder(frames, width=64, height=48, fps=15)
    cam = CameraSource("camera://0")
    cam.set_decoder(mock)

    # platform device mapping is resolved without hardware
    target, fmt = cam._device_spec()
    assert fmt in ("avfoundation", "video4linux2", "dshow"), fmt

    widget = MediaWidget("m_cam", width=64, height=48, loop=False)
    widget.set_source_object(cam)
    canvas = _RecordingCanvas()
    observed = _drive(widget, canvas, n_ticks=5, dt=1.0 / 15 * 1.001,
                      rect=Rect(0, 0, 64, 48))

    assert mock.events[0] == "open", f"open not first: {mock.events[:2]}"
    assert "read" in mock.events, "read never called"
    distinct = sorted(set(i for i in observed if i >= 0))
    assert distinct == [0, 1, 2], f"camera frames: {distinct}"
    assert len(canvas.decoded) >= 3, "no composited handles from camera frames"

    widget.on_unmount()
    assert mock.events[-1] == "close", f"close not last: {mock.events[-3:]}"
    return (f"PASS — camera://0 -> ({target!r}, {fmt}); "
            f"open/read/close wired; frames {distinct}")


def test_network_source_structural() -> str:
    """GLASS-PENDING: NetworkSource (rtsp/mjpeg) wiring via a mock decoder."""
    frames = [Frame(32, 24, b"\xff\xff\xff" * (32 * 24), index=i)
              for i in range(2)]
    mock = MockDecoder(frames, width=32, height=24, fps=25)
    net = NetworkSource("rtsp://camera.local/stream")
    net.set_decoder(mock)
    widget = MediaWidget("m_net", width=32, height=24, loop=False)
    widget.set_source_object(net)
    canvas = _RecordingCanvas()
    observed = _drive(widget, canvas, n_ticks=4, dt=1.0 / 25 * 1.001,
                      rect=Rect(0, 0, 32, 24))
    distinct = sorted(set(i for i in observed if i >= 0))
    assert distinct == [0, 1], f"network frames: {distinct}"

    # scheme routing picks the right source classes
    assert isinstance(make_source("file://a.mp4"), FileSource)
    assert isinstance(make_source("camera://2"), CameraSource)
    assert isinstance(make_source("rtsp://h/s"), NetworkSource)
    assert isinstance(make_source("mjpeg://h/s"), NetworkSource)
    assert make_source("gopher://x") is None, "unknown scheme should be None"
    assert isinstance(make_source("clip.mp4"), FileSource), "bare .mp4 -> file"
    return f"PASS — rtsp wired, frames {distinct}; scheme routing correct"


def test_trigger_model() -> str:
    """TRIGGER model: a pure semantic value maps to a local source; a bare
    source ref on the bus also plays; both are 'state on the bus'."""
    # 1. semantic -> local source via trigger_map
    w = MediaWidget("m_trig", trigger_map={"alert": "file://alert.mp4",
                                           "idle": "file://idle.mp4"})
    w.on_bus_state("scene", "alert")
    assert w.src == "file://alert.mp4" and w.autoplay, "semantic map failed"
    w.on_bus_state("scene", "idle")
    assert w.src == "file://idle.mp4", "semantic remap failed"
    w.on_bus_state("scene", "unknown_scene")
    assert not w.autoplay, "unmapped semantic should stand down"

    # 2. a source ref published directly on the bus
    w2 = MediaWidget("m_trig2")
    w2.on_bus_state("feed", "rtsp://cam/stream")
    assert w2.src == "rtsp://cam/stream" and w2.autoplay, "source-ref failed"

    # 3. plain play/pause gate against `trigger`
    w3 = MediaWidget("m_trig3", src="file://x.mp4", trigger="on", autoplay=False)
    w3.on_bus_state("power", "on")
    assert w3.autoplay, "trigger gate on failed"
    w3.on_bus_state("power", "off")
    assert not w3.autoplay, "trigger gate off failed"
    return "PASS — semantic->source, source-ref, and play/pause gate all wired"


def test_imports_clean_without_av() -> str:
    """The module imports + placeholder-degrades regardless of PyAV.

    (We can't uninstall av here; assert the degrade path is reachable — a
    widget with an unresolvable source draws the placeholder, no exception.)"""
    w = MediaWidget("m_noav", src="file:///no/such/file.mp4", loop=False)
    canvas = _RecordingCanvas()
    w.on_clock_tick(0.0)          # open() fails -> source dropped
    w.draw(canvas, Rect(0, 0, 40, 30))   # must not raise; placeholder path
    assert w._frame is None, "no frame expected from a bad source"
    return "PASS — bad source degrades to placeholder, no exception"


# ---------------------------------------------------------------------------
# Hardware-decode (hwaccel) OPT-IN suite  ([CXC-RM-MEDIA] hwaccel; see
# docs/provisioning/minnowboard-vaapi.md §7).  Kept SEPARATE from the 6-test
# default suite so `--selftest` stays 6/6 (the byte-unchanged software path);
# run this via `--selftest --hwaccel`.  Two tiers per the build verdict:
#   MACHINE-GREEN  — if the loaded FFmpeg exposes a HW device (macOS
#                    VideoToolbox here), decode the synthetic clip ON that
#                    device and assert frames + dims + composite handle. This
#                    proves the GENERIC hwaccel plumbing on REAL hardware.
#   STRUCTURAL     — where no HW device is compiled in, assert HWAccel
#                    resolves/guards correctly and decode falls back to
#                    software cleanly; FLAG full HW verification as pending.
# vaapi-on-a-MinnowBoard is the IDENTICAL path with device_type="vaapi" —
# glass-pending on 'benchnode' until a provisioned board is in reach.
# ---------------------------------------------------------------------------

def _hw_clip() -> "tuple[str, str, int, int, int, list]":
    """Encode the shared synthetic H.264 clip; return (path, codec, w, h, fps,
    colors)."""
    w, h, fps = 96, 64, 12
    colors = [(200, 30, 30), (30, 200, 30), (30, 30, 200),
              (200, 200, 30), (30, 200, 200), (200, 30, 200)]
    tmp = tempfile.mkdtemp(prefix="cxc_media_hw_")
    path = os.path.join(tmp, "clip.mp4")
    codec = _encode_clip(path, w=w, h=h, fps=fps, colors=colors)
    return path, codec, w, h, fps, colors


def _drive_and_assert_decode(widget: MediaWidget, *, w: int, h: int,
                             fps: int, n_colors: int) -> "tuple[int, int]":
    """Drive the widget over the clip; assert frames advance, dims are right,
    and a composited handle is produced.  Returns (n_frames, n_handles)."""
    canvas = _RecordingCanvas()
    observed = _drive(widget, canvas, n_ticks=n_colors + 3,
                      dt=(1.0 / fps) * 1.001, rect=Rect(0, 0, w, h))
    distinct = sorted(set(i for i in observed if i >= 0))
    assert distinct and distinct[0] == 0, f"frames did not advance: {distinct}"
    assert distinct == list(range(distinct[0], distinct[-1] + 1)), \
        f"frame indices not monotonic/contiguous: {distinct}"
    assert widget._frame is not None
    assert (widget._frame.width, widget._frame.height) == (w, h), \
        f"decoded dims {widget._frame.width}x{widget._frame.height} != {w}x{h}"
    assert len(canvas.decoded) >= len(distinct), "composite handle missing"
    assert canvas.decoded[0][:8] == b"\x89PNG\r\n\x1a\n", "not a PNG payload"
    return len(distinct), len(canvas.decoded)


def _decoder_of(widget: MediaWidget) -> Any:
    """The live PyAV decoder behind the widget's source (post-open)."""
    src = widget._source
    return getattr(src, "_decoder", None) if src is not None else None


def test_hw_default_byte_unchanged() -> str:
    """The default (hwaccel='none') decode is byte-identical, frame-for-frame,
    to a plain software av.open+frame_to_rgb — the opt-in adds NOTHING to the
    default path."""
    if not av_available():
        return "SKIP (PyAV absent) — default path degrades to placeholder"
    from .pyav_support import frame_to_rgb

    path, codec, w, h, fps, colors = _hw_clip()

    # reference: every frame decoded straight through PyAV, no hwaccel object.
    import av
    cont = av.open(path)
    ref = [frame_to_rgb(f)[2] for f in cont.decode(video=0)]
    cont.close()

    # subject: the SAME decoder the widget uses, hwaccel='none' (default).
    src = FileSource("file://" + path)          # hwaccel defaults to 'none'
    src.open()
    got = []
    while True:
        frame = src.read()
        if frame is None:
            break
        got.append(frame.rgb)
    assert src._decoder.hwaccel_used == "none", \
        f"default path took a HW device: {src._decoder.hwaccel_used}"
    src.close()

    assert len(got) == len(ref) and got == ref, \
        f"default decode diverged: {len(got)} frames vs {len(ref)}, equal={got == ref}"
    # and the widget default really is 'none'
    assert MediaWidget("m_hw_none").hwaccel == "none", \
        "widget default hwaccel must be 'none'"
    return (f"PASS — hwaccel='none' byte-matches plain software decode "
            f"frame-for-frame ({len(got)} frames), hwaccel_used='none'")


def test_hw_resolver() -> str:
    """make_hwaccel resolves requests without ever raising."""
    assert make_hwaccel("none") == (None, "none"), "none must be software"
    assert make_hwaccel("") == (None, "none"), "empty must be software"
    devices = hwaccel_devices()
    # explicit-unavailable -> software fallback (no exception)
    hw, dev = make_hwaccel("definitely_not_a_device")
    assert hw is None and dev == "none", "bogus device must resolve to software"
    if devices:
        hw, dev = make_hwaccel(devices[0])
        assert hw is not None and dev == devices[0], \
            f"available device {devices[0]!r} should build an HWAccel"
        hw2, dev2 = make_hwaccel("auto")
        assert hw2 is not None and dev2 in devices, \
            f"auto should pick an available device, got {dev2!r}"
        note = f"available={devices}; built HWAccel for {devices[0]!r} + auto->{dev2!r}"
    else:
        hw2, dev2 = make_hwaccel("auto")
        assert hw2 is None and dev2 == "none", "auto with no HW -> software"
        note = "no HW devices in this FFmpeg build; auto->software (structural)"
    return f"PASS — resolver never raised; {note}"


def test_hw_real_or_structural() -> str:
    """MACHINE-GREEN on real HW where available; else STRUCTURAL + FLAG.

    On this host: if the loaded FFmpeg exposes a HW device (macOS
    VideoToolbox), decode the clip ON it and prove the generic plumbing on
    REAL hardware.  Otherwise prove the plumbing structurally (HWAccel
    resolves, decode falls back to software) and flag HW verification pending.
    """
    if not av_available():
        return "SKIP (PyAV absent) — hwaccel unavailable, placeholder path only"
    path, codec, w, h, fps, colors = _hw_clip()
    devices = hwaccel_devices()
    if not devices:
        # structural: request a HW device; decode must still work (software
        # fallback) and the decoder must report it fell back to 'none'.
        widget = MediaWidget("m_hw_struct", src="file://" + path, width=w,
                             height=h, fit="stretch", loop=False,
                             hwaccel="videotoolbox")
        n_frames, _ = _drive_and_assert_decode(
            widget, w=w, h=h, fps=fps, n_colors=len(colors))
        dec = _decoder_of(widget)
        assert dec.hwaccel_used == "none", "structural: expected SW fallback"
        widget.on_unmount()
        return ("PASS (STRUCTURAL) — FLAG: no HW decoder compiled into this "
                "PyAV/FFmpeg; hwaccel plumbing resolves + falls back to "
                f"software cleanly ({n_frames} frames). Full HW decode "
                "verification PENDING (macOS VideoToolbox or MinnowBoard vaapi).")

    device = devices[0]  # macOS: 'videotoolbox' — the real-HW proof
    widget = MediaWidget("m_hw_real", src="file://" + path, width=w, height=h,
                         fit="stretch", loop=False, hwaccel=device)
    n_frames, n_handles = _drive_and_assert_decode(
        widget, w=w, h=h, fps=fps, n_colors=len(colors))
    dec = _decoder_of(widget)
    assert dec is not None and dec.hwaccel_used == device, \
        f"expected HW decode on {device!r}, got {getattr(dec,'hwaccel_used','?')}"
    widget.on_unmount()
    return (f"PASS (MACHINE-GREEN, REAL HW) — decoded on device={device!r} "
            f"(codec={codec}): {n_frames} frames, dims {w}x{h}, {n_handles} "
            "composite handles. Generic hwaccel plumbing proven on real "
            "hardware; vaapi-on-MinnowBoard is the identical path.")


def test_hw_fallback_unavailable() -> str:
    """Requesting an UNAVAILABLE device type decodes in software, no crash."""
    if not av_available():
        return "SKIP (PyAV absent)"
    path, codec, w, h, fps, colors = _hw_clip()
    available = set(hwaccel_devices())
    # a device type this host does NOT have (mac never has vaapi/cuda)
    unavailable = next((d for d in ("vaapi", "cuda", "qsv", "d3d11va", "vdpau")
                        if d not in available), "vaapi")
    hw, dev = make_hwaccel(unavailable)
    assert hw is None and dev == "none", \
        f"unavailable {unavailable!r} should resolve to software, got {dev!r}"
    widget = MediaWidget("m_hw_fallback", src="file://" + path, width=w,
                         height=h, fit="stretch", loop=False,
                         hwaccel=unavailable)
    n_frames, _ = _drive_and_assert_decode(
        widget, w=w, h=h, fps=fps, n_colors=len(colors))
    dec = _decoder_of(widget)
    assert dec.hwaccel_used == "none", "must have fallen back to software"
    widget.on_unmount()
    return (f"PASS — hwaccel={unavailable!r} (absent here) fell back to "
            f"software, {n_frames} frames decoded, no exception")


HWACCEL_TESTS = [
    ("hw_default_byte_unchanged", test_hw_default_byte_unchanged),
    ("hw_resolver", test_hw_resolver),
    ("hw_real_or_structural", test_hw_real_or_structural),
    ("hw_fallback_unavailable", test_hw_fallback_unavailable),
]


TESTS = [
    ("file_source (MACHINE-GREEN)", test_file_source_machine_green),
    ("file_source_mock (structural)", test_file_source_via_mock),
    ("camera_source (GLASS-PENDING)", test_camera_source_structural),
    ("network_source (GLASS-PENDING)", test_network_source_structural),
    ("trigger_model", test_trigger_model),
    ("degrade_without_source", test_imports_clean_without_av),
]


def _run(label: str, tests: List) -> int:
    failures = 0
    for name, fn in tests:
        try:
            msg = fn()
            print(f"  ok  {name}: {msg}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"[media {label}] {len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or []
    print(f"[media selftest] PyAV available: {av_available()}; "
          f"HW devices: {hwaccel_devices() or 'none'}")
    # `--hwaccel` runs the SEPARATE hwaccel opt-in suite; the default suite
    # stays exactly 6/6 (the byte-unchanged software path).
    if "--hwaccel" in argv:
        return _run("hwaccel selftest", HWACCEL_TESTS)
    return _run("selftest", TESTS)
