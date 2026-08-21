"""
ComposeUX RadioButtonGroupWidget — a reusable radio-button group.

A REUSABLE radio-button group: N selectable items rendered as stadium (fully
rounded) button-shape cells with a pixel-font caption, exactly ONE selected.
The SELECTED item's shape uses the group's HIGHLIGHT color; the rest use the
default (unselected) shape fill.  Size / padding / count / orientation / colors
are all inputs, so the widget is the source of truth for the shape.

Own-every-pixel ([CXC-R-DP-1]): the button-shape is a Canvas-contract
``fill_round_rect`` (radius = height/2, a stadium) and the caption is
WIDGET-DRAWN in the bundled pixel font — so it renders identically on Skia, the
browser, and the CYD (no SVG-native path, no per-backend fork).  This is a
programmatic, fully-parameterized widget, NOT an SVG-art extraction.

Control-is-a-peer: a tap publishes the selected index to ``index_namespace`` —
the SAME index a rotary or an Intelligence publishes — and the selection is
DERIVED from the published index (``on_bus_state``), never from local tap-state
guesswork.  So touch, rotary, and Intelligence move the selection identically.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

RGBA = Tuple[int, int, int, int]

# Default button-shape geometry: a fixed 106x30 device-px stadium, 10px
# inter-button padding, black default fill.
_BTN_W = 106.0
_BTN_H = 30.0
_PADDING = 10.0
_UNSELECTED_FILL: RGBA = (0x00, 0x00, 0x00, 0xFF)   # button-shape default fill
_WHITE: RGBA = (0xFF, 0xFF, 0xFF, 0xFF)             # caption ink

HORIZONTAL = "horizontal"
VERTICAL = "vertical"


class RadioButtonGroupWidget(Widget):
    """A configurable-N radio-button group with one highlighted selection."""

    def __init__(self, widget_id: str, *,
                 labels: Optional[List[str]] = None,
                 selected_index: int = 0,
                 orientation: str = HORIZONTAL,
                 padding: float = _PADDING,
                 button_width: float = _BTN_W,
                 button_height: float = _BTN_H,
                 button_overrides: Optional[Dict[int, Dict[str, float]]] = None,
                 base_color: RGBA = (0x76, 0x24, 0x00, 0xFF),
                 highlight_color: RGBA = (0xCD, 0x67, 0x1D, 0xFF),
                 unselected_fill: RGBA = _UNSELECTED_FILL,
                 text_color: RGBA = _WHITE,
                 font_size: Optional[float] = None,
                 index_namespace: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "radio_button_group", **kwargs)
        self.labels: List[str] = list(labels or [])
        self.orientation = (VERTICAL if str(orientation).strip().lower()
                            in ("v", "vertical") else HORIZONTAL)
        self.padding = float(padding)
        self.button_width = float(button_width)
        self.button_height = float(button_height)
        self.button_overrides: Dict[int, Dict[str, float]] = \
            dict(button_overrides or {})
        self.base_color = base_color
        self.highlight_color = highlight_color
        self.unselected_fill = unselected_fill
        self.text_color = text_color
        # caption default proportional to button height (matches the SVG's
        # ~0.53 tspan/rect ratio); scaled by the container transform when placed.
        self.font_size = float(font_size if font_size is not None
                               else self.button_height * 0.5)
        self.index_namespace = str(index_namespace or "")
        self.selected_index = self._clamp(int(selected_index))
        # a tap publishes the index to index_namespace; the selection derives
        # from the published index (on_bus_state).
        self.interactive = True
        self._button_rects: List[Rect] = []

    # -- selection state -----------------------------------------------------

    def _clamp(self, index: int) -> int:
        n = len(self.labels)
        if n == 0:
            return 0
        return max(0, min(int(index), n - 1))

    def set_labels(self, labels: List[str]) -> None:
        """Swap the item set (the count is configurable from a bound list)."""
        self.labels = list(labels or [])
        self.selected_index = self._clamp(self.selected_index)
        self.request_redraw()

    def set_selected(self, index: int) -> None:
        idx = self._clamp(index)
        if idx != self.selected_index:
            self.selected_index = idx
            self.request_redraw()

    def index_for(self, value: Any) -> Optional[int]:
        """Resolve a published selection payload to an index: an int, a
        ``{index|key}`` dict, or a bare label string."""
        if isinstance(value, bool):
            return None
        if isinstance(value, dict):
            if "index" in value:
                return self.index_for(value.get("index"))
            if "key" in value:
                return self.index_for(value.get("key"))
            return None
        if isinstance(value, (int, float)):
            i = int(value)
            return i if 0 <= i < len(self.labels) else None
        name = str(value or "").strip().lower()
        for i, lb in enumerate(self.labels):
            if str(lb).strip().lower() == name:
                return i
        return None

    def on_bus_state(self, namespace: str, value: Any) -> None:
        # control-is-a-peer: a publish to index_namespace moves the selection
        # identically to a tap, so touch / rotary / Intelligence agree.
        if namespace and namespace == self.index_namespace:
            idx = self.index_for(value)
            if idx is not None:
                self.set_selected(idx)

    # -- layout --------------------------------------------------------------

    def _btn_size(self, i: int) -> Tuple[float, float]:
        ov = self.button_overrides.get(i) or {}
        return (float(ov.get("width", self.button_width)),
                float(ov.get("height", self.button_height)))

    def natural_size(self) -> Tuple[float, float]:
        """The group's intrinsic size at fixed button geometry (before any
        container scale-to-fit): the sum of button extents + inter-button
        padding along the main axis, the max button extent on the cross axis."""
        n = len(self.labels)
        if n == 0:
            return (0.0, 0.0)
        widths = [self._btn_size(i)[0] for i in range(n)]
        heights = [self._btn_size(i)[1] for i in range(n)]
        gap = self.padding * (n - 1)
        if self.orientation == HORIZONTAL:
            return (sum(widths) + gap, max(heights))
        return (max(widths), sum(heights) + gap)

    def measure(self, constraints: Constraints) -> Size:
        w, h = self.natural_size()
        return Size(w, h)

    def _layout_rects(self, rect: Rect) -> List[Rect]:
        """The per-button cell rects for *rect* (the pure layout function shared
        by draw + tap routing)."""
        rects: List[Rect] = []
        cursor_x = rect.x
        cursor_y = rect.y
        for i in range(len(self.labels)):
            bw, bh = self._btn_size(i)
            if self.orientation == HORIZONTAL:
                bx, by = cursor_x, rect.y + (rect.h - bh) / 2.0
                cursor_x += bw + self.padding
            else:
                bx, by = rect.x + (rect.w - bw) / 2.0, cursor_y
                cursor_y += bh + self.padding
            rects.append(Rect(bx, by, bw, bh))
        return rects

    # -- draw ----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        n = len(self.labels)
        self._button_rects = self._layout_rects(rect) if n else []
        if n == 0:
            return
        canvas.save()
        for i, br in enumerate(self._button_rects):
            self._draw_button(canvas, br, str(self.labels[i]),
                              i == self.selected_index)
        canvas.restore()

    # -- interaction (a tap publishes the selected index) ---------------------

    def on_tap(self, x: float, y: float) -> None:
        rect = self.arranged_rect
        if rect is None or not self.labels:
            return
        # the group may be uniform-scaled about its rect center by the container
        # draw pass; inverse-map the tap into the widget's own (unscaled) space so
        # the cell routing is exact regardless of scale (hit_test_tree gates on
        # the axis-aligned arranged rect — the documented limitation).
        scl = float(getattr(self, "scale", 1.0)) or 1.0
        cx = rect.x + rect.w / 2.0
        cy = rect.y + rect.h / 2.0
        lx = cx + (x - cx) / scl
        ly = cy + (y - cy) / scl
        for i, br in enumerate(self._layout_rects(rect)):
            if br.x <= lx <= br.x + br.w and br.y <= ly <= br.y + br.h:
                self.set_selected(i)
                if self.index_namespace:
                    self.publish(self.index_namespace,
                                 {"index": i, "key": self.labels[i]})
                return

    def _draw_button(self, canvas: Canvas, r: Rect, label: str,
                     selected: bool) -> None:
        # button-shape: a stadium (radius == height/2).  SELECTED -> HIGHLIGHT;
        # otherwise the default (unselected) shape fill.
        fill = self.highlight_color if selected else self.unselected_fill
        canvas.set_fill_color(fill[0], fill[1], fill[2],
                              fill[3] if len(fill) > 3 else 0xFF)
        canvas.fill_round_rect(r.x, r.y, r.w, r.h, r.h / 2.0)
        # caption: pixel-font ink, centered, truncated defensively so a long
        # label never overruns the pill.
        canvas.set_font(style.FONT_PIXEL, self.font_size, "regular")
        c = self.text_color
        canvas.set_fill_color(c[0], c[1], c[2], c[3] if len(c) > 3 else 0xFF)
        text = label
        while (canvas.measure_text(text).width > r.w - 6.0 and len(text) > 1):
            text = text[:-1]
        canvas.draw_text(text, r.x + r.w / 2.0,
                         style.baseline_for(canvas, r.y + r.h / 2.0), "center")
