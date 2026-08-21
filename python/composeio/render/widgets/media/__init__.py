"""
ComposeUX Media Surface ([CXC-RM-MEDIA]) — video file + webcam + stream.

One frame-source `MediaWidget` compositing externally-decoded frames via
the existing Canvas contract (`decode_image` -> `draw_image`), with
PLUGGABLE SOURCES chosen by URI scheme:

    FileSource     file://clip.mp4  (H.264/MP4, PyAV)
    CameraSource   camera://N       (webcam: AVFoundation/V4L2 via PyAV)
    NetworkSource  rtsp:// mjpeg://  (network stream via PyAV)

Video and webcam are two sources of the SAME widget.  PyAV (`av`) is an
OPTIONAL, LAZY dependency — this package imports cleanly without it and
every instance degrades to a placeholder (use `av_available()` to probe).

NOTE: this module does NOT self-register with the global widget registry
(module-local by design, per the build constraint).  An embedder wiring it
into the parser maps widget type "media" -> MediaWidget itself.

Run the self-test:  python -m composeio.render.widgets.media --selftest
"""

from __future__ import annotations

from .pyav_support import Frame, MediaDecodeError, av_available, rgb_to_png
from .sources import (
    CameraSource,
    Decoder,
    FileSource,
    FrameSource,
    NetworkSource,
    SOURCE_REGISTRY,
    make_source,
    register_source,
)
from .widget import MediaWidget

__all__ = [
    "MediaWidget",
    "FrameSource",
    "FileSource",
    "CameraSource",
    "NetworkSource",
    "Decoder",
    "Frame",
    "MediaDecodeError",
    "make_source",
    "register_source",
    "SOURCE_REGISTRY",
    "av_available",
    "rgb_to_png",
]


if __name__ == "__main__":  # pragma: no cover
    import sys
    from .selftest import main
    sys.exit(main(sys.argv[1:]))
