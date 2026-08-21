"""
Pluggable frame sources for the Media Surface ([CXC-RM-MEDIA]).

"Video and webcam are two sources of the SAME widget, not two special
cases."  A source is a small open()/read()/close() transport that yields
decoded RGB `Frame`s; the MediaWidget composites whatever it produces.
Three ship here:

    FileSource     file://clip.mp4 / *.mp4     H.264/MP4, PyAV demux+decode
    CameraSource   camera://N                  attached webcam via PyAV
                                                device input (AVFoundation
                                                on macOS, V4L2 on Linux)
    NetworkSource  rtsp:// mjpeg:// http://     network stream via PyAV

Extensibility (open-source seam): sources are chosen by URI scheme via
SOURCE_REGISTRY; `register_source(scheme, factory)` lets a stranger add a
new source (e.g. a GStreamer or NDI transport) without touching this file
or the widget.  This is the HAL/adaptor idiom applied to frame input.

The DECODER is injectable (`decoder=` / `set_decoder`): real sources build
a PyAV-backed decoder lazily on open(); tests inject a mock transport with
the same open()/read()->Frame|None/close() shape to verify wiring without
hardware or a network (the structural, glass-pending tier).
"""

from __future__ import annotations

import platform
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from .pyav_support import (
    Frame,
    MediaDecodeError,
    frame_to_rgb,
    make_hwaccel,
    _require_av,
)


# ---------------------------------------------------------------------------
# Decoder transports (the I/O + codec layer, shared across sources)
# ---------------------------------------------------------------------------

class Decoder(ABC):
    """open()/read()/close() transport yielding decoded RGB Frames.

    `width`/`height`/`fps` are populated on open() (or first read()).
    read() returns None at end-of-stream / when no frame is ready.
    """

    width: int = 0
    height: int = 0
    fps: float = 30.0

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def read(self) -> Optional[Frame]: ...

    def seek(self, seconds: float) -> None:  # optional (file only)
        raise NotImplementedError("this source is not seekable")

    @abstractmethod
    def close(self) -> None: ...


class _PyAVDecoder(Decoder):
    """PyAV-backed decoder: demux the first video stream, emit rgb24.

    `target`/`format`/`options` are the only things that differ between a
    file, a camera device, and a network URL — so all three real sources
    reuse this one decode loop.

    `hwaccel` is the hardware-decode OPT-IN ([CXC-RM-MEDIA]; see
    pyav_support.make_hwaccel): "none" (default, software, unchanged),
    "auto", or an explicit device type.  Requesting HW that isn't available
    logs once and decodes in software — open() never fails for that reason.
    `hwaccel_used` records the device actually in effect ("none" for SW).
    """

    def __init__(self, target: str, *,
                 format: Optional[str] = None,
                 options: Optional[Dict[str, str]] = None,
                 seekable: bool = False,
                 hwaccel: str = "none") -> None:
        self.target = target
        self.format = format
        self.options = options
        self._seekable = seekable
        self._hwaccel_request = str(hwaccel or "none")
        self.hwaccel_used = "none"
        self._container: Any = None
        self._stream: Any = None
        self._gen: Any = None
        self._i = 0

    def open(self) -> None:
        av = _require_av()
        # Resolve the hwaccel opt-in.  make_hwaccel NEVER raises and returns
        # (None, "none") for the software path, so the default is byte-for-
        # byte the original av.open below (the hwaccel kwarg is omitted).
        hw, self.hwaccel_used = make_hwaccel(self._hwaccel_request)
        open_kwargs: Dict[str, Any] = {"format": self.format,
                                       "options": self.options}
        if hw is not None:
            open_kwargs["hwaccel"] = hw
        try:
            self._container = av.open(self.target, **open_kwargs)
        except Exception as exc:  # noqa: BLE001 — normalize to our error
            raise MediaDecodeError(
                f"cannot open media {self.target!r}: {exc}") from exc
        try:
            self._stream = self._container.streams.video[0]
        except (IndexError, KeyError) as exc:
            raise MediaDecodeError(
                f"no video stream in {self.target!r}") from exc
        cc = self._stream.codec_context
        self.width = int(cc.width or 0)
        self.height = int(cc.height or 0)
        rate = (self._stream.average_rate
                or getattr(self._stream, "guessed_rate", None)
                or getattr(self._stream, "base_rate", None))
        try:
            self.fps = float(rate) if rate else 30.0
        except (TypeError, ValueError):
            self.fps = 30.0
        self._gen = self._container.decode(video=0)
        self._i = 0

    def read(self) -> Optional[Frame]:
        if self._gen is None:
            return None
        try:
            av_frame = next(self._gen)
        except StopIteration:
            return None
        w, h, rgb = frame_to_rgb(av_frame)
        if not self.width:
            self.width, self.height = w, h
        idx = self._i
        self._i += 1
        pts = getattr(av_frame, "time", None)
        return Frame(width=w, height=h, rgb=rgb, index=idx,
                     pts=float(pts) if pts is not None else None)

    def seek(self, seconds: float) -> None:
        if not self._seekable or self._container is None:
            raise NotImplementedError("stream is not seekable")
        # PyAV seeks in stream time_base ticks; offset is in AV_TIME_BASE.
        av = _require_av()
        offset = int(max(0.0, seconds) * av.time_base)
        self._container.seek(offset)
        self._gen = self._container.decode(video=0)

    def close(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = self._stream = self._gen = None


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

class FrameSource(ABC):
    """Base frame source: a named origin (uri) + a Decoder transport."""

    def __init__(self, uri: str, *, decoder: Optional[Decoder] = None,
                 hwaccel: str = "none") -> None:
        self.uri = str(uri)
        self._decoder = decoder
        # hardware-decode opt-in threaded to the PyAV decoder at _make_decoder
        # time (see _PyAVDecoder); "none" == software == current behavior.
        self.hwaccel = str(hwaccel or "none")
        self._opened = False

    # -- decoder injection (tests / embedders) --------------------------
    def set_decoder(self, decoder: Decoder) -> None:
        self._decoder = decoder

    @abstractmethod
    def _make_decoder(self) -> Decoder:
        """Build the real PyAV-backed decoder for this source kind."""

    # -- lifecycle ------------------------------------------------------
    def open(self) -> None:
        if self._opened:
            return
        if self._decoder is None:
            self._decoder = self._make_decoder()
        self._decoder.open()
        self._opened = True

    def read(self) -> Optional[Frame]:
        if not self._opened or self._decoder is None:
            return None
        return self._decoder.read()

    def seek(self, seconds: float) -> None:
        if self._decoder is not None:
            self._decoder.seek(seconds)

    def close(self) -> None:
        if self._decoder is not None:
            self._decoder.close()
        self._opened = False

    # -- info -----------------------------------------------------------
    @property
    def width(self) -> int:
        return self._decoder.width if self._decoder else 0

    @property
    def height(self) -> int:
        return self._decoder.height if self._decoder else 0

    @property
    def fps(self) -> float:
        return self._decoder.fps if self._decoder else 30.0

    @property
    def is_open(self) -> bool:
        return self._opened

    @property
    def seekable(self) -> bool:
        return False


class FileSource(FrameSource):
    """A local video file (file://clip.mp4 or a bare *.mp4 path)."""

    def _make_decoder(self) -> Decoder:
        path = self.uri
        if path.startswith("file://"):
            path = path[len("file://"):]
        return _PyAVDecoder(path, seekable=True, hwaccel=self.hwaccel)

    def seek(self, seconds: float) -> None:  # file transport supports it
        super().seek(seconds)

    @property
    def seekable(self) -> bool:
        return True


class CameraSource(FrameSource):
    """An attached webcam (camera://N).

    Device target + input format are platform facts (a board-profile-like
    concern): AVFoundation on macOS, Video4Linux2 on Linux.  Live camera
    content is NON-golden-able ([CXC-RM-MEDIA] pixel-gate exception) — the
    chrome around it is verifiable, the feed is not reproducible.
    """

    def _device_spec(self) -> "tuple[str, Optional[str]]":
        idx = self.uri
        if idx.startswith("camera://"):
            idx = idx[len("camera://"):]
        idx = idx.strip("/") or "0"
        sysname = platform.system()
        if sysname == "Darwin":
            return idx, "avfoundation"          # `av.open("0", "avfoundation")`
        if sysname == "Linux":
            dev = idx if idx.startswith("/dev/") else f"/dev/video{idx}"
            return dev, "video4linux2"
        # Windows / other: dshow best-effort; real bring-up is glass-pending.
        return f"video={idx}", "dshow"

    def _make_decoder(self) -> Decoder:
        target, fmt = self._device_spec()
        return _PyAVDecoder(target, format=fmt, hwaccel=self.hwaccel)


class NetworkSource(FrameSource):
    """A network stream (rtsp://, mjpeg://, http://)."""

    def _make_decoder(self) -> Decoder:
        url = self.uri
        options: Dict[str, str] = {}
        fmt: Optional[str] = None
        if url.startswith("rtsp://"):
            options = {"rtsp_transport": "tcp", "stimeout": "5000000"}
        elif url.startswith("mjpeg://"):
            # mjpeg:// is our scheme; FFmpeg wants http:// + mjpeg demuxer.
            url = "http://" + url[len("mjpeg://"):]
            fmt = "mjpeg"
        return _PyAVDecoder(url, format=fmt, options=options or None,
                            hwaccel=self.hwaccel)


# ---------------------------------------------------------------------------
# Scheme registry (the pluggable-source seam)
# ---------------------------------------------------------------------------

SourceFactory = Callable[[str], FrameSource]

SOURCE_REGISTRY: Dict[str, SourceFactory] = {}


def register_source(scheme: str, factory: SourceFactory) -> None:
    """Register a FrameSource factory for a URI scheme (e.g. 'ndi').

    Lets a downstream project add a transport without editing this module
    — the open-source extensibility contract ([CXC-RM-MEDIA] pluggable
    sources).
    """
    SOURCE_REGISTRY[scheme.lower()] = factory


def _scheme_of(uri: str) -> Optional[str]:
    s = str(uri)
    if "://" in s:
        return s.split("://", 1)[0].lower()
    if s.lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")):
        return "file"
    return None


def make_source(uri: str, *, hwaccel: str = "none") -> Optional[FrameSource]:
    """Build the right FrameSource for `uri`, or None if unrecognized.

    None (not an exception) so an unknown/absent source degrades to the
    widget placeholder — the screen is never broken.

    `hwaccel` (the HW-decode opt-in) is applied to the built source AFTER
    construction, so the registry factory contract stays `factory(uri)` — a
    stranger's registered source ([CXC-RM-MEDIA] pluggable seam) needs no
    hwaccel kwarg; it just inherits the FrameSource.hwaccel attribute (which
    only the PyAV decoder honors).
    """
    scheme = _scheme_of(uri)
    if scheme is None:
        return None
    factory = SOURCE_REGISTRY.get(scheme)
    if factory is None:
        return None
    source = factory(uri)
    if source is not None and hwaccel and hwaccel != "none":
        source.hwaccel = str(hwaccel)
    return source


# Built-in scheme registrations.
register_source("file", FileSource)
register_source("camera", CameraSource)
register_source("rtsp", NetworkSource)
register_source("mjpeg", NetworkSource)
register_source("http", NetworkSource)
register_source("https", NetworkSource)
