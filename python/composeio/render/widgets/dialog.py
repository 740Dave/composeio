"""
ComposeUX DialogWidget — engine-rendered modal overlay (M4).

Spec: [CXC-RM-M4] engine-addition #3.  The portable, bus-native,
own-every-pixel alternative to OS dialogs: a full-surface scrim with a
centered panel that hosts child widgets and captures ALL pointer input
while visible.

Behavior:
    - ``visible`` gates everything (draw + hit-test), so the dialog binds
      to a namespace either via the generic ``visible:`` binding
      ([CXC-RM-M4] engine-addition #2) or via its own ``namespace:``
      (on_bus_state maps truthy -> shown).
    - While shown, hit_test_tree returns the dialog (or one of its panel
      children) for EVERY point of its arranged rect — nothing behind the
      scrim is reachable (modal hit-capture).
    - Tapping the scrim dismisses when ``dismissible`` (default True);
      dismissal publishes "off" to the bound namespace when one exists,
      keeping bus state coherent with the visual.
    - Place it as the LAST child of a pixel_stack covering the full canvas
      (the parser defaults x/y=0, w/h=100%% inside a pixel stack) so it is
      z-top in both draw order and hit-test order.

Uses ONLY the abstract Canvas contract.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from ..engine.layout import Container, _contains, _measure_child
from . import style

_TITLE_H = 36.0
_PAD = 14.0
_GAP = 8.0
_ROW_H = 30.0                       # list-select mode ([CXC-PRIM-POPUP])
# Ratified interactive accent ([CXC-PRIM-TOKENS], dark #34c3d0).
_ACCENT = (0x34, 0xC3, 0xD0, 255)


class DialogWidget(Container):
    """Modal overlay: scrim + centered panel + child widgets + hit capture.

    Two modes share the one overlay mechanism ([CXC-R-DIALOG]):

      * child mode (M4): the panel hosts arbitrary child widgets (the
        Command arm-confirm, the BusMonitor pop-out).
      * list-select mode ([SPEC:CXC-PRIM] [CXC-PRIM-POPUP], the dropdown
        popup): pass ``options`` + ``select_namespace``.  The panel renders
        the option rows; tapping a row publishes that value to
        ``select_namespace`` and dismisses; an outside tap / dismiss leaves
        the selection UNCHANGED.  ``anchor=(x, y, w)`` places the list under
        a field instead of centering it.
    """

    def __init__(self, widget_id: str, *,
                 title: str = "",
                 panel_width: float = 340,
                 panel_height: float = 0,      # 0 -> from children
                 dismissible: bool = True,
                 scrim_color: Tuple[int, int, int, int] = (0, 0, 0, 150),
                 namespace: str = "",
                 visible: bool = False,
                 options: Optional[list] = None,
                 select_namespace: str = "",
                 selected_namespace: str = "",
                 max_visible: int = 6,
                 anchor: Optional[Tuple[float, float, float]] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "dialog", **kwargs)
        self.title = title
        self.panel_width = float(panel_width)
        self.panel_height = float(panel_height)
        self.dismissible = dismissible
        self.scrim_color = tuple(scrim_color)
        self.bound_namespace = namespace
        self.visible = visible
        self.interactive = True
        self._panel_rect: Optional[Rect] = None
        # -- list-select mode ([CXC-PRIM-POPUP]) --------------------------
        self.options = [str(o) for o in (options or [])]
        # namespace the picked value is published to (accept either spelling)
        self.select_namespace = select_namespace or selected_namespace
        self.max_visible = int(max_visible)
        self.anchor = tuple(anchor) if anchor else None
        self._selected: Any = None        # current value (for the check mark)

    @property
    def is_list(self) -> bool:
        return bool(self.options)

    # -- bus ---------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        # list-select mode: the selected-value namespace drives the check
        # mark; the open/close namespace drives visibility.
        if self.is_list and namespace == self.select_namespace:
            self._selected = value
            if self.visible:
                self.request_redraw()
            return
        shown = str(value).lower() not in ("", "0", "off", "false", "none")
        if shown != self.visible:
            self.visible = shown
            self.request_redraw()

    def open(self) -> None:
        self.visible = True
        if self.bound_namespace:
            self.publish(self.bound_namespace, "on")
        self.request_redraw()

    def dismiss(self) -> None:
        self.visible = False
        if self.bound_namespace:
            self.publish(self.bound_namespace, "off")
        self.request_redraw()

    # -- layout --------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # The dialog claims the whole surface (scrim).
        return Size(constraints.max_width, constraints.max_height)

    def _content_height(self, inner_w: float) -> float:
        h = 0.0
        for ch in self.children:
            s = _measure_child(ch, inner_w, float("inf"))
            h += s.height
        if self.children:
            h += _GAP * (len(self.children) - 1)
        return h

    def _visible_rows(self) -> int:
        return min(len(self.options), max(1, self.max_visible))

    def _row_rects(self) -> list:
        """(value, Rect) for each rendered option row (list-select mode)."""
        p = self._panel_rect
        if p is None or not self.is_list:
            return []
        top = p.y + (_TITLE_H if self.title else 0.0)
        rows = []
        for i, opt in enumerate(self.options[:self._visible_rows()]):
            rows.append((opt, Rect(p.x, top + i * _ROW_H, p.w, _ROW_H)))
        return rows

    def _arrange_children(self, inner: Rect) -> None:
        if self.is_list:
            title_h = _TITLE_H if self.title else 0.0
            ph = title_h + self._visible_rows() * _ROW_H
            if self.anchor is not None:
                ax, ay, aw = self.anchor
                pw = min(float(aw), inner.w - 8)
                px = inner.x + min(max(0.0, float(ax)), inner.w - pw)
                py = inner.y + min(float(ay), max(0.0, inner.h - ph))
            else:
                pw = min(self.panel_width, inner.w - 16)
                px = inner.x + (inner.w - pw) / 2
                py = inner.y + (inner.h - ph) / 2
            self._panel_rect = Rect(px, py, pw, ph)
            return
        pw = min(self.panel_width, inner.w - 16)
        inner_w = pw - 2 * _PAD
        title_h = _TITLE_H if self.title else 0.0
        ph = self.panel_height
        if ph <= 0:
            ph = title_h + self._content_height(inner_w) + 2 * _PAD
        ph = min(ph, inner.h - 16)
        px = inner.x + (inner.w - pw) / 2
        py = inner.y + (inner.h - ph) / 2
        self._panel_rect = Rect(px, py, pw, ph)

        y = py + title_h + _PAD
        for ch in self.children:
            s = _measure_child(ch, inner_w, ph - title_h - 2 * _PAD)
            r = Rect(px + _PAD, y, inner_w, s.height)
            ch.arranged_rect = r
            if isinstance(ch, Container):
                ch.arrange(r)
            y += s.height + _GAP

    # -- draw -----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if not self.visible or self._panel_rect is None:
            return
        canvas.save()
        # scrim
        sc = self.scrim_color
        canvas.set_fill_color(*sc[:3], sc[3] if len(sc) > 3 else 150)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        # panel
        p = self._panel_rect
        canvas.set_fill_color(*style.FRAME_TOP_BG)
        canvas.fill_round_rect(p.x, p.y, p.w, p.h, 10)
        canvas.set_stroke_color(0x3A, 0x3A, 0x50)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(p.x, p.y, p.w, p.h, 10)

        if self.title:
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 14, "medium")
            canvas.draw_text(self.title, p.x + _PAD,
                             style.baseline_for(canvas, p.y + _TITLE_H / 2 + 4),
                             "left")
            canvas.set_stroke_color(0x3A, 0x3A, 0x50)
            canvas.draw_line(p.x + 1, p.y + _TITLE_H,
                             p.x + p.w - 1, p.y + _TITLE_H)

        if self.is_list:
            self._draw_rows(canvas)
        else:
            for ch in self.children:
                if getattr(ch, "visible", True) and ch.arranged_rect is not None:
                    ch.draw(canvas, ch.arranged_rect)
        canvas.restore()

    def _draw_rows(self, canvas: Canvas) -> None:
        sel = None if self._selected is None else str(self._selected)
        cy_off = _ROW_H / 2
        for opt, r in self._row_rects():
            selected = str(opt) == sel
            if selected:                              # selected row: accent
                canvas.set_fill_color(_ACCENT[0], _ACCENT[1], _ACCENT[2], 40)
                canvas.fill_rect(r.x + 1, r.y, r.w - 2, r.h)
                canvas.set_fill_color(*_ACCENT)
            else:
                canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 13, "regular")
            canvas.draw_text(str(opt), r.x + _PAD,
                             style.baseline_for(canvas, r.y + cy_off + 4),
                             "left")
            if selected:                              # check dot
                canvas.set_fill_color(*_ACCENT)
                canvas.fill_circle(r.x + r.w - _PAD, r.y + cy_off, 3)

    # -- hit capture ---------------------------------------------------------

    def hit_test_tree(self, x: float, y: float) -> Optional[Widget]:
        """Modal capture: while visible, EVERY point inside the arranged
        rect resolves to this dialog or one of its panel children."""
        if not self.visible:
            return None
        r = self.arranged_rect
        if r is None or not _contains(r, x, y):
            return None
        p = self._panel_rect
        if p is not None and _contains(p, x, y):
            for ch in reversed(self.children):
                if not getattr(ch, "visible", True):
                    continue
                if isinstance(ch, Container):
                    hit = ch.hit_test_tree(x, y)
                    if hit is not None:
                        return hit
                elif ch.arranged_rect is not None and ch.hit_test(x, y):
                    return ch
        return self          # scrim / panel chrome: captured by the dialog

    def hit_test(self, x: float, y: float) -> bool:
        return self.visible and self.arranged_rect is not None and \
            _contains(self.arranged_rect, x, y)

    def on_tap(self, x: float, y: float) -> None:
        p = self._panel_rect
        if self.is_list and p is not None and _contains(p, x, y):
            # tap on an option row -> publish the value + dismiss
            for opt, r in self._row_rects():
                if _contains(r, x, y):
                    self.select(opt)
                    return
            return                        # dead panel space: swallow, no change
        # outside the panel (or non-list child mode): dismiss, no change
        if self.dismissible and (p is None or not _contains(p, x, y)):
            self.dismiss()

    def select(self, value: Any) -> None:
        """Publish *value* to the select namespace and dismiss ([CXC-PRIM-POPUP]).
        Dismissal publishes off to the open/close namespace, so the popup
        closes and the bus stays coherent."""
        self._selected = value
        if self.select_namespace:
            self.publish(self.select_namespace, value)
        self.dismiss()
