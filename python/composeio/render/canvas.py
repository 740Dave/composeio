"""
ComposeUX Render Engine - Platform-Independent Drawing Contract (Layer 1)

Spec: [CXC-R-DRAW-CONTRACT]

Every platform backend (Skia, Canvas 2D, ESP32 framebuffer) implements this
abstract interface. Widget code draws against this contract and never imports
a platform-specific rendering library directly.

All coordinates are in logical pixels. The backend handles DPI scaling.
Angles are in radians. Colors are RGBA 0-255.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Value types used across the contract
# ---------------------------------------------------------------------------

@dataclass
class Size:
    width: float
    height: float


@dataclass
class TextMetrics:
    width: float
    height: float
    ascent: float
    descent: float


@dataclass
class GradientStop:
    """A color stop in a gradient.  offset is 0.0-1.0."""
    offset: float
    r: int
    g: int
    b: int
    a: int = 255


# Path command types used by draw_path / fill_path
@dataclass
class MoveTo:
    x: float
    y: float


@dataclass
class LineTo:
    x: float
    y: float


@dataclass
class CurveTo:
    """Cubic bezier."""
    cp1x: float
    cp1y: float
    cp2x: float
    cp2y: float
    x: float
    y: float


@dataclass
class QuadTo:
    """Quadratic bezier (SVG Q command)."""
    x1: float
    y1: float
    x: float
    y: float


@dataclass
class ArcTo:
    """Elliptical arc (SVG A command)."""
    rx: float
    ry: float
    rotation: float   # x-axis rotation in degrees
    large_arc: bool
    sweep: bool
    x: float
    y: float


@dataclass
class Close:
    pass


PathCommand = MoveTo | LineTo | CurveTo | QuadTo | ArcTo | Close


# ---------------------------------------------------------------------------
# Abstract drawing canvas
# ---------------------------------------------------------------------------

class Canvas(ABC):
    """Platform-independent drawing surface.

    Implements the full Layer 1 drawing primitive contract defined in
    [CXC-R-DRAW-CONTRACT].  A concrete backend (Skia, Canvas 2D, framebuffer)
    subclasses this and translates every call to platform-specific API.
    """

    # -- Geometry -----------------------------------------------------------

    @abstractmethod
    def fill_rect(self, x: float, y: float, w: float, h: float) -> None:
        """Filled rectangle using current fill style."""

    @abstractmethod
    def stroke_rect(self, x: float, y: float, w: float, h: float) -> None:
        """Outlined rectangle using current stroke style."""

    @abstractmethod
    def fill_round_rect(self, x: float, y: float, w: float, h: float,
                        radius: float) -> None:
        """Rounded filled rectangle."""

    @abstractmethod
    def stroke_round_rect(self, x: float, y: float, w: float, h: float,
                          radius: float) -> None:
        """Outlined rounded rectangle using current stroke style."""

    @abstractmethod
    def fill_circle(self, cx: float, cy: float, r: float) -> None:
        """Filled circle."""

    @abstractmethod
    def stroke_circle(self, cx: float, cy: float, r: float) -> None:
        """Circle outline."""

    @abstractmethod
    def fill_arc(self, cx: float, cy: float, r: float,
                 start_angle: float, end_angle: float) -> None:
        """Filled arc sector (pie slice). Angles in radians."""

    @abstractmethod
    def stroke_arc(self, cx: float, cy: float, r: float,
                   start_angle: float, end_angle: float) -> None:
        """Arc outline. Angles in radians."""

    @abstractmethod
    def draw_line(self, x1: float, y1: float,
                  x2: float, y2: float) -> None:
        """Line segment using current stroke style."""

    @abstractmethod
    def draw_path(self, commands: List[PathCommand]) -> None:
        """Stroked arbitrary path."""

    @abstractmethod
    def fill_path(self, commands: List[PathCommand]) -> None:
        """Filled arbitrary path."""

    # -- Style State --------------------------------------------------------

    @abstractmethod
    def set_fill_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        """Set the current fill color (RGBA 0-255)."""

    @abstractmethod
    def set_stroke_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        """Set the current stroke color (RGBA 0-255)."""

    @abstractmethod
    def set_stroke_width(self, w: float) -> None:
        """Set the current stroke width in logical pixels."""

    @abstractmethod
    def set_linear_gradient(self, x1: float, y1: float,
                            x2: float, y2: float,
                            stops: List[GradientStop]) -> None:
        """Set a linear gradient as fill style."""

    @abstractmethod
    def set_radial_gradient(self, cx: float, cy: float, r: float,
                            stops: List[GradientStop]) -> None:
        """Set a radial gradient as fill style."""

    @abstractmethod
    def set_shadow(self, offset_x: float, offset_y: float,
                   blur: float,
                   color: Tuple[int, int, int, int]) -> None:
        """Configure drop shadow for subsequent draws."""

    @abstractmethod
    def clear_shadow(self) -> None:
        """Remove any active drop shadow."""

    @abstractmethod
    def set_opacity(self, a: float) -> None:
        """Global opacity multiplier (0.0 - 1.0)."""

    # -- Transform & Clip ---------------------------------------------------

    @abstractmethod
    def save(self) -> None:
        """Push graphics state (style + transform)."""

    @abstractmethod
    def restore(self) -> None:
        """Pop graphics state."""

    @abstractmethod
    def translate(self, dx: float, dy: float) -> None:
        """Translate the origin."""

    @abstractmethod
    def rotate(self, angle: float) -> None:
        """Rotate around the current origin (radians)."""

    @abstractmethod
    def scale(self, sx: float, sy: float) -> None:
        """Scale the coordinate system."""

    @abstractmethod
    def clip_rect(self, x: float, y: float, w: float, h: float) -> None:
        """Restrict drawing to the given rectangle."""

    @abstractmethod
    def clip_round_rect(self, x: float, y: float, w: float, h: float,
                        radius: float) -> None:
        """Restrict drawing to a rounded rectangle."""

    # -- Text ---------------------------------------------------------------

    def set_font_scale(self, scale: float) -> None:
        """Set a global font-size MULTIPLIER applied at set_font time
        ([CXC-ICP-S2-FONTSCALE], console personalization).  Default 1.0.

        Part of the contract so a personalized console scales identically on
        every backend; a backend that has not implemented it (or the CYD C++
        canvas, whose set_opacity/decode_image set the no-op-stub precedent)
        simply ignores the multiplier.  The default here is a no-op so every
        non-console scene is byte-identical."""

    @abstractmethod
    def set_font(self, family: str, size: float,
                 weight: str = "regular") -> None:
        """Set the current font.  weight: regular | medium | bold."""

    @abstractmethod
    def draw_text(self, text: str, x: float, y: float,
                  align: str = "left") -> None:
        """Draw text at position.  align: left | center | right."""

    @abstractmethod
    def measure_text(self, text: str) -> TextMetrics:
        """Measure text with the current font."""

    # -- Image --------------------------------------------------------------

    @abstractmethod
    def draw_image(self, image_handle: Any, x: float, y: float,
                   w: float, h: float) -> None:
        """Draw a previously loaded image scaled to the destination rect."""

    @abstractmethod
    def load_image(self, uri: str) -> Any:
        """Load and decode an image. Returns an opaque handle."""

    @abstractmethod
    def decode_image(self, data: bytes) -> Any:
        """Decode raw image bytes (PNG/JPEG) to a backend image handle.

        Promoted to the contract 2026-07-06 ([CXC-R-DRAW] change record):
        the engine AssetManager fetches bytes itself (doc:// documents,
        bus:// payloads) and needs the Layer-1 codec without a URI —
        'asset decoding' is a Layer-1 responsibility per [CXC-R-ARCH].
        Returns None when the bytes are not a decodable image (the widget
        renders its fallback; the screen is never broken).
        """

    # -- Surface ------------------------------------------------------------

    @abstractmethod
    def create_surface(self, width: int, height: int) -> None:
        """Create the drawing surface (window, canvas, framebuffer)."""

    @abstractmethod
    def present(self) -> None:
        """Flush drawing commands to the display."""

    def present_partial(self, rects: List[Tuple[float, float, float, float]]) -> None:
        """Flush ONLY the given damaged sub-rectangles to the display
        ([CXC-R-DRAW-DAMAGE], promoted to the contract 2026-07-20).

        The engine's dirty-region path presents the union of what actually
        changed instead of the whole surface — a keystroke blits only the
        composer field.  Each rect is ``(x, y, w, h)`` in logical pixels.

        This is a CONCRETE default, not an abstractmethod: a backend that
        cannot present sub-regions inherits a correct (if unoptimized)
        full-surface ``present()``, so the damage INFO flows generically
        through the engine while each backend implements as much of the
        partial path as its hardware allows.  A raster/PNG backend has
        nothing to flush; a windowed backend overrides to update only the
        damaged texture sub-rect; an ESP32 framebuffer overrides to push
        only the dirty window over SPI (its single biggest refresh win)."""
        self.present()

    @abstractmethod
    def get_surface_size(self) -> Size:
        """Return the current surface size in logical pixels."""
