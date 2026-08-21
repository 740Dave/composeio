"""
ComposeUX SVG Widget ([SPEC:CXC-SVG] [CXC-SVG-WIDGET])

Renders an SVG referenced as `src: doc://…/x.svg`, `bus://assets/…/x.svg`,
or `file://…` through the engine AssetManager ([CXC-R-ENGINE-ASSETS]),
scaled to its rect with Image-style fit modes (contain | cover | stretch).
The SVG is DECOMPOSED to the Canvas contract (render/svg/), so it draws
crisp at any scale on every backend — a stylized "Red Alert", a Mermaid /
Graphviz diagram, matplotlib output ([CXC-SVG-INTENT]).

A bound `src` swaps the graphic reactively ([SPEC:CXC-BIND] [CXC-SVG-C3]);
publishing a new URI to the bound namespace re-fetches and re-parses.

Diagram pan/zoom ([CXC-SVG-C6], the DiagramView variant): set
`pan_zoom: true` to make the widget interactive — scroll/pinch zooms about
the cursor, drag pans.  The view transform is pure state (offset + scale),
so a given (offset, scale) renders deterministically.  The upstream
Mermaid/Graphviz -> SVG diagram SERVICE ([CXC-SVG-C5]) is a Node hub
workload (out of this Python render engine); this widget is its universal
consumer — point `src` at the `doc://diagrams/{id}.svg` it produces.
"""

from __future__ import annotations

from typing import Any, Optional

from ..canvas import Canvas, Size
from ..svg import SvgDocument, SvgError, render_svg
from ..widget import Constraints, Rect, Widget

_DIM = (0x66, 0x66, 0x78)


class SvgWidget(Widget):
    """Vector graphic from an SVG asset, decomposed to Canvas primitives."""

    def __init__(self, widget_id: str, *,
                 src: str = "",
                 width: float = 200,
                 height: float = 200,
                 fit: str = "contain",
                 pan_zoom: Any = False,
                 default_font_family: str = "",
                 **kwargs: Any) -> None:
        # state exists BEFORE super().__init__ so property setters invoked
        # via base-class kwargs find their fields (the Lottie convention).
        self._doc: Optional[SvgDocument] = None
        self._load_tried = False
        self._src = ""
        # pan/zoom view transform ([CXC-SVG-C6])
        self.pan_zoom = bool(pan_zoom)
        self._view_scale = 1.0
        self._view_x = 0.0
        self._view_y = 0.0

        super().__init__(widget_id, "svg", **kwargs)
        self.img_width = float(width)
        self.img_height = float(height)
        self.fit = str(fit)
        # scene/app-scoped default font for un-named <text> ([SPEC:CXC-CIC]);
        # "" keeps the global Plex fallback (Intelligence Console stays Plex)
        self.default_font_family = str(default_font_family or "")
        self._src = str(src)
        if self.pan_zoom:
            self.interactive = True

    # ------------------------------------------------------------------
    # Bindable src ([CXC-SVG-C3])
    # ------------------------------------------------------------------

    @property
    def src(self) -> str:
        return self._src

    @src.setter
    def src(self, value: Any) -> None:
        value = str(value or "")
        if value == self._src:
            return
        self._src = value
        self._doc = None
        self._load_tried = False
        self.request_redraw()

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """A bound namespace value switches the source URI reactively."""
        if isinstance(value, str) and value != self._src:
            self.src = value

    def set_document(self, doc: SvgDocument) -> None:
        """Directly install a parsed document (tests / embedders)."""
        self._doc = doc
        self._load_tried = True

    # ------------------------------------------------------------------
    # Loading ([CXC-SVG-WIDGET] via AssetManager)
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> Optional[SvgDocument]:
        if self._doc is not None or self._load_tried or not self._src:
            return self._doc
        self._load_tried = True
        data: Optional[bytes] = None
        assets = getattr(self._ctx, "assets", None) if self._ctx else None
        if assets is not None:
            data = assets.get_bytes(self._src)
        else:                                        # unmounted fallback (dev)
            try:
                from composeio import resolve as _resolve
                data = _resolve.resolve_bytes(self._src)
            except Exception:
                data = None
        if data is not None:
            try:
                self._doc = SvgDocument.from_bytes(data)
            except SvgError:
                self._doc = None
        return self._doc

    # ------------------------------------------------------------------
    # Pan / zoom interaction ([CXC-SVG-C6])
    # ------------------------------------------------------------------

    def _zoom_about(self, factor: float, cx: float, cy: float) -> None:
        new_scale = max(0.2, min(20.0, self._view_scale * factor))
        factor = new_scale / self._view_scale
        # keep the cursor point fixed while scaling
        self._view_x = cx - (cx - self._view_x) * factor
        self._view_y = cy - (cy - self._view_y) * factor
        self._view_scale = new_scale
        self.request_redraw()

    def on_scroll(self, dx: float, dy: float) -> None:
        if not self.pan_zoom:
            return
        r = self.arranged_rect
        cx = r.x + r.w / 2 if r else 0.0
        cy = r.y + r.h / 2 if r else 0.0
        self._zoom_about(1.0 + (0.0015 * -dy), cx, cy)

    def hover_cursor(self) -> str:
        # [CXC-R-ENGINE-HOVER] the svg canvas is interactive only to CATCH
        # pan/zoom/pinch — it is not a click-activation target and holds no
        # selectable text, so the click-hand over its whole body would mislead.
        # Show the arrow (a plain diagram reads as static; the pan gesture is
        # discovered by dragging, not promised by the cursor).
        return "default"

    def on_pinch(self, scale: float, cx: float, cy: float) -> None:
        if self.pan_zoom and scale > 0:
            self._zoom_about(scale, cx, cy)

    def on_pan(self, dx: float, dy: float) -> None:
        if not self.pan_zoom:
            super().on_pan(dx, dy)
            return
        self._view_x += dx
        self._view_y += dy
        self.request_redraw()

    def reset_view(self) -> None:
        self._view_scale = 1.0
        self._view_x = self._view_y = 0.0
        self.request_redraw()

    # ------------------------------------------------------------------
    # Measure / draw
    # ------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(self.img_width, constraints.max_width),
                    min(self.img_height, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        doc = self._ensure_loaded()
        if doc is not None:
            if self.pan_zoom and (self._view_scale != 1.0
                                  or self._view_x or self._view_y):
                canvas.save()
                canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
                canvas.translate(self._view_x, self._view_y)
                if self._view_scale != 1.0:
                    # zoom about the rect origin (offset already accounts
                    # for the cursor anchor)
                    canvas.translate(rect.x, rect.y)
                    canvas.scale(self._view_scale, self._view_scale)
                    canvas.translate(-rect.x, -rect.y)
                render_svg(canvas, doc, rect, self.fit,
                           default_font_family=self.default_font_family or None)
                canvas.restore()
            else:
                render_svg(canvas, doc, rect, self.fit,
                           default_font_family=self.default_font_family or None)
            return
        # fallback placeholder — the screen is never broken
        canvas.save()
        canvas.set_fill_color(0x28, 0x28, 0x3C)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*_DIM)
        canvas.set_stroke_width(1)
        canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*_DIM)
        canvas.set_font("sans-serif", 10, "regular")
        canvas.draw_text(self._src or "no svg",
                         rect.x + rect.w / 2, rect.y + rect.h / 2, "center")
        canvas.restore()
