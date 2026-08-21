"""
ComposeUX SegmentedTabRowWidget — a reusable segmented tab / selector row.

A single equal-width tab row with one of two selection-indicator styles: the
``pill-overlay`` indicator (a pill drawn over the selected tab) or the
``filled-background`` indicator (the whole selected tab filled).  The two differ
ONLY in the ``selected_indicator`` parameter + its fill opacity — one class,
instanced with either style.

Reuse-by-reference (EXTEND, do NOT fork — [CXC-R-WIDGET-CONTAINER]): this
subclasses :class:`~composeio.render.widgets.tab.TabWidget`, keeping the
container's hit-test-the-bar idiom (``bar_h`` spans the whole row, so
``hit_test_tree`` routes any tap in the row to this widget) and the ``_seg_bounds``
tap-mapping.  It swaps ONLY the bar CHROME (the two indicator styles) and points
the publish/subscribe seam at a configurable selection-index namespace.

Control-is-a-peer: a tab TAP publishes the selection index to ``index_namespace``
— the SAME index a rotary turn or an Intelligence publishes.  The selected
indicator is DERIVED from the published index (``on_bus_state``), never from local
tap-state guesswork, so touch, rotary, and Intelligence move the indicator
identically.

Own-every-pixel ([CXC-R-DP-1]): the indicators have a fully PROGRAMMATIC path
(Canvas-contract rects / round-rects), so the widget is self-contained and renders
identically on every backend.  A project MAY additionally inject already-prepared
SVG artwork nodes (``art_*``) to draw exact vector indicators; those nodes are
rendered through the engine's generic :func:`~composeio.render.svg.render_element`
(the same Canvas decomposition as any SVG) — the caller prepares/themes the nodes,
so no project-specific artwork logic lives in this widget.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..svg import render_element
from ..widget import Constraints, Rect
from . import style
from .tab import TabWidget

RGBA = Tuple[int, int, int, int]
Box = Tuple[float, float, float, float]

# The two indicator styles.  A builder MUST NOT invent a fill; these are the
# closed set.
FILLED_BACKGROUND = "filled-background"   # whole selected tab bg, #333 @100%
PILL_OVERLAY = "pill-overlay"             # a pill over the selected tab, #333 @70%

_INDICATOR_FILL = (0x33, 0x33, 0x33)
_STYLE_OPACITY = {FILLED_BACKGROUND: 1.0, PILL_OVERLAY: 0.70}

_WHITE = (0xFF, 0xFF, 0xFF)               # default label ink

# Fraction of a tab CELL left as inter-tab gutter when placing injected tab
# artwork across N slots (the artwork draws ~6% gutters).
_TAB_GAP_FRAC = 0.07


class SegmentedTabRowWidget(TabWidget):
    """A segmented selector row — equal-width tabs, one of two indicator styles,
    a configurable index publish/subscribe seam."""

    def __init__(self, widget_id: str, *,
                 labels: Optional[List[str]] = None,
                 selected_indicator: str = FILLED_BACKGROUND,
                 fill_color: RGBA = None,          # type: ignore[assignment]
                 fill_opacity: Optional[float] = None,
                 index_namespace: str = "",
                 base_color: RGBA = (0x76, 0x24, 0x00, 0xFF),
                 highlight_color: RGBA = (0xCD, 0x67, 0x1D, 0xFF),
                 font_size: float = 18.0,
                 text_color: Optional[RGBA] = None,
                 # -- optional INJECTED SVG indicator artwork (already prepared /
                 # themed by the caller; the widget only renders it) -----------
                 art_doc: Any = None,
                 art_tab_base: Any = None,
                 art_tab_selected: Any = None,
                 art_tab_bbox: Optional[Box] = None,
                 art_pill: Any = None,
                 art_pill_bbox: Optional[Box] = None,
                 art_bg: Any = None,
                 art_bg_bbox: Optional[Box] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, labels=list(labels or []),
                         widget_type="segmented_tab_row", **kwargs)
        style_name = str(selected_indicator or FILLED_BACKGROUND)
        if style_name not in _STYLE_OPACITY:
            style_name = FILLED_BACKGROUND
        self.selected_indicator = style_name
        fc = fill_color or _INDICATOR_FILL
        self.fill_color: RGBA = (fc[0], fc[1], fc[2],
                                 fc[3] if len(fc) > 3 else 0xFF)
        self.fill_opacity = float(fill_opacity if fill_opacity is not None
                                  else _STYLE_OPACITY[style_name])
        # publish (on tap) AND subscribe (derive indicator) — the same ns.
        self.index_namespace = str(index_namespace or "")
        self.base_color = base_color
        self.highlight_color = highlight_color
        self.font_size = float(font_size)
        # Row-UNIFORM label ink: selection is shown by the pill / filled cell,
        # NOT by recoloring the text.  Defaults to white so an un-styled
        # construction still renders a legible label.
        ti = text_color if text_color is not None else _WHITE
        self.text_color: RGBA = (ti[0], ti[1], ti[2],
                                 ti[3] if len(ti) > 3 else 0xFF)
        # the whole row is the tap target (no separate content region)
        self.interactive = True
        self.active_index = 0
        # bar spans the full row; set by the host from its geometry
        self.bar_h = 48.0

        # -- optional INJECTED SVG indicator artwork ---------------------------
        # A caller MAY inject already-prepared (found + themed) SVG nodes to draw
        # exact vector indicators: a per-slot tab artwork (base + selected copy),
        # a selected-tab pill, and/or a whole-row background band, each with its
        # own viewBox-space bbox.  These REPLACE the programmatic indicator rects
        # per slot; the programmatic path below is the fallback when a given
        # artwork is absent.  The widget renders these through the engine's
        # generic ``render_element`` — it does not find, theme, or recolor them.
        self._art_doc = art_doc
        self._art_template_bbox: Optional[Box] = art_tab_bbox
        self._art_template_base: Any = art_tab_base
        self._art_template_selected: Any = art_tab_selected
        self._art_pill_bbox: Optional[Box] = art_pill_bbox
        self._art_pill_node: Any = art_pill
        self._art_bg_bbox: Optional[Box] = art_bg_bbox
        self._art_bg_node: Any = art_bg

    def _has_tab_art(self) -> bool:
        return (self._art_doc is not None
                and self._art_template_bbox is not None
                and self._art_template_selected is not None)

    def _has_pill_art(self) -> bool:
        return (self._art_doc is not None
                and self._art_pill_bbox is not None
                and self._art_pill_node is not None)

    def _has_bg_art(self) -> bool:
        return (self._art_doc is not None
                and self._art_bg_bbox is not None
                and self._art_bg_node is not None)

    # -- label + selection state (label-driven; base keys off children) --------

    def set_labels(self, labels: List[str]) -> None:
        """Swap the label set (the tabs may change at runtime).  Clamps the
        active index into the new set and repaints."""
        self.labels = list(labels or [])
        if self.labels:
            self.active_index = max(0, min(self.active_index, len(self.labels) - 1))
        else:
            self.active_index = 0
        self.request_redraw()

    def set_active(self, index: int) -> None:
        """Move the selected indicator (label-based — this row hosts no child
        containers, so the base's children-keyed ``set_active`` does not fit)."""
        n = len(self.labels)
        if n == 0:
            return
        idx = max(0, min(int(index), n - 1))
        if idx != self.active_index:
            self.active_index = idx
            self.request_redraw()

    def index_for(self, value: Any) -> Optional[int]:
        """Resolve a published selection payload to an index: an int, or a
        ``{index, key}`` dict, or a bare label KEY string."""
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

    # -- bus wiring (derive indicator from the published index) ---------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.index_namespace:
            idx = self.index_for(value)
            if idx is not None:
                self.set_active(idx)

    # -- interaction (a tap publishes the selected index) ---------------------

    def on_tap(self, x: float, y: float) -> None:
        for i, (x0, x1) in enumerate(self._seg_bounds):
            if x0 <= x <= x1:
                self.set_active(i)
                if self.index_namespace:
                    # control-is-a-peer: the SAME index a rotary/Intelligence
                    # publishes.
                    self.publish(self.index_namespace,
                                 {"index": i, "key": self.labels[i]})
                return

    # -- layout ---------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, min(self.bar_h, constraints.max_height))

    def _arrange_children(self, inner: Rect) -> None:      # no child containers
        pass

    # -- draw (injected tab artwork if present; programmatic path = fallback) --

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        n = len(self.labels)
        if n == 0:
            self._seg_bounds = []
            return
        canvas.save()
        # the whole-row BACKGROUND band first (behind labels + pill), mapped from
        # its own bbox into the full bar rect so it registers with the rest.
        if self._has_bg_art():
            render_element(canvas, self._art_doc, self._art_bg_node,
                           rect, self._art_bg_bbox)
        seg_w = rect.w / n
        self._seg_bounds = []
        filled = (self.selected_indicator == FILLED_BACKGROUND)
        tab_art = filled and self._has_tab_art()
        pill_art = (not filled) and self._has_pill_art()
        for i, lb in enumerate(self.labels):
            x0 = rect.x + i * seg_w
            x1 = x0 + seg_w
            selected = (i == self.active_index)

            if filled:
                if tab_art:
                    self._draw_art_tab(canvas, rect, x0, seg_w, selected)
                else:
                    self._draw_fallback_filled(canvas, rect, x0, seg_w, selected)
            else:                                   # PILL_OVERLAY
                if selected and pill_art:
                    self._draw_art_pill(canvas, rect, x0, seg_w)
                elif selected:
                    self._draw_fallback_pill(canvas, rect, x0, seg_w)

            ink = self.text_color
            canvas.set_font(style.FONT_PIXEL, self.font_size, "regular")
            canvas.set_fill_color(ink[0], ink[1], ink[2],
                                  ink[3] if len(ink) > 3 else 0xFF)
            label = str(lb)
            # truncate defensively so a long label never overruns its cell
            while (canvas.measure_text(label).width > seg_w - 10.0
                   and len(label) > 1):
                label = label[:-1]
            canvas.draw_text(label, x0 + seg_w / 2.0,
                             style.baseline_for(canvas, rect.y + rect.h / 2.0),
                             "center")
            self._seg_bounds.append((x0, x1))
        canvas.restore()

    # -- injected SVG-art indicators (rendered via the engine's render_element) -

    def _tab_slot(self, rect: Rect, x0: float, seg_w: float) -> Rect:
        gap = seg_w * _TAB_GAP_FRAC
        return Rect(x0 + gap / 2.0, rect.y, seg_w - gap, rect.h)

    def _draw_art_tab(self, canvas: Canvas, rect: Rect, x0: float, seg_w: float,
                      selected: bool) -> None:
        """Draw the injected per-slot tab artwork; the SELECTED slot renders the
        caller-supplied selected copy."""
        node = (self._art_template_selected if selected
                else self._art_template_base)
        render_element(canvas, self._art_doc, node,
                       self._tab_slot(rect, x0, seg_w),
                       self._art_template_bbox)

    def _draw_art_pill(self, canvas: Canvas, rect: Rect, x0: float,
                       seg_w: float) -> None:
        """Draw the injected selected-tab pill (already themed by the caller)
        over the selected slot."""
        render_element(canvas, self._art_doc, self._art_pill_node,
                       self._tab_slot(rect, x0, seg_w),
                       self._art_pill_bbox)

    # -- programmatic indicators (fallback when no artwork is injected) --------

    def _draw_fallback_filled(self, canvas: Canvas, rect: Rect, x0: float,
                              seg_w: float, selected: bool) -> None:
        if selected:
            canvas.set_fill_color(self.fill_color[0], self.fill_color[1],
                                  self.fill_color[2], 0xFF)
            canvas.fill_rect(x0 + 1, rect.y + 2, seg_w - 2, rect.h - 4)
        else:
            b = self.base_color
            canvas.set_fill_color(b[0], b[1], b[2], 0x33)
            canvas.fill_rect(x0 + 1, rect.y + 2, seg_w - 2, rect.h - 4)

    def _draw_fallback_pill(self, canvas: Canvas, rect: Rect, x0: float,
                            seg_w: float) -> None:
        pill_a = int(round(self.fill_opacity * 255))
        canvas.set_fill_color(self.fill_color[0], self.fill_color[1],
                              self.fill_color[2], pill_a)
        pad = 4.0
        canvas.fill_round_rect(x0 + pad, rect.y + pad,
                               seg_w - 2 * pad, rect.h - 2 * pad,
                               (rect.h - 2 * pad) / 2.0)
