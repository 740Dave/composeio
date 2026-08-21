"""
ComposeUX Image Widget (M2 — the M31 flagship's bus:// ImageWidget)

Displays an image asset referenced by URI (bus://assets/…, doc://…,
file://…) through the engine AssetManager ([CXC-R-ENGINE-ASSETS]).
doc:// bytes decode via the Canvas contract's decode_image ([CXC-R-DRAW]
promotion, 2026-07-06).  Falls back to a placeholder block when the
asset cannot be resolved ([CXC-R-DRAWIMAGE] — the screen is never
broken).

M2 additions: fit modes (stretch | contain | cover) and namespace-driven
source switching (publish a new URI to the bound namespace and the image
swaps — the M31 testbed's live-image behavior).
"""

from __future__ import annotations

from typing import Any, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget

_DIM = (0x66, 0x66, 0x78)


class ImageWidget(Widget):
    """Image from URI, scaled to its rect."""

    def __init__(self, widget_id: str, *,
                 src: str = "",
                 width: float = 160,
                 height: float = 120,
                 fit: str = "stretch",
                 alt: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "image", **kwargs)
        self.src = src
        self.img_width = width
        self.img_height = height
        self.fit = fit                # stretch | contain | cover
        # human-facing fallback label drawn on the neutral swatch when the
        # asset cannot be resolved (a carousel image tile's label fallback,
        # [CXC-PRIM-CAROUSEL-TILES]); defaults to the src for standalone use.
        self.alt = str(alt)
        self._handle: Any = None
        self._tried = False

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """Namespace value can switch the source URI."""
        if isinstance(value, str) and value != self.src:
            self.src = value
            self._handle = None
            self._tried = False

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(self.img_width, constraints.max_width),
                    min(self.img_height, constraints.max_height))

    def _fit_rect(self, rect: Rect) -> Rect:
        """Destination rect for the configured fit mode.

        Source dimensions come from the backend handle when it exposes
        width()/height() (Skia does); otherwise stretch.
        """
        if self.fit not in ("contain", "cover"):
            return rect
        try:
            sw, sh = float(self._handle.width()), float(self._handle.height())
        except Exception:
            return rect
        if sw <= 0 or sh <= 0:
            return rect
        scale_contain = min(rect.w / sw, rect.h / sh)
        scale_cover = max(rect.w / sw, rect.h / sh)
        s = scale_contain if self.fit == "contain" else scale_cover
        w, h = sw * s, sh * s
        return Rect(rect.x + (rect.w - w) / 2, rect.y + (rect.h - h) / 2, w, h)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self._handle is None and not self._tried and self.src:
            self._tried = True
            assets = getattr(self._ctx, "assets", None) if self._ctx else None
            if assets is not None:
                self._handle = assets.get_image(canvas, self.src)
            else:
                try:
                    self._handle = canvas.load_image(self.src)
                except Exception:
                    self._handle = None

        canvas.save()
        if self._handle is not None:
            dest = self._fit_rect(rect)
            if self.fit == "cover":
                canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
            canvas.draw_image(self._handle, dest.x, dest.y, dest.w, dest.h)
        else:
            # fallback placeholder ([CXC-R-DRAWIMAGE] asset-source: fallback)
            canvas.set_fill_color(0x28, 0x28, 0x3C)
            canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
            canvas.set_stroke_color(*_DIM)
            canvas.set_stroke_width(1)
            canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)
            canvas.draw_line(rect.x, rect.y, rect.x + rect.w, rect.y + rect.h)
            canvas.draw_line(rect.x + rect.w, rect.y, rect.x, rect.y + rect.h)
            canvas.set_fill_color(*_DIM)
            canvas.set_font("sans-serif", 10, "regular")
            canvas.draw_text(self.alt or self.src or "no image",
                             rect.x + rect.w / 2, rect.y + rect.h / 2,
                             "center")
        canvas.restore()
