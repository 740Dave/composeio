"""
InfoListWidget — a bus-bound, titled, multi-line TEXT PANEL.

The ONE new widget for the R503 player/character/RBAC binding UI
([SPEC:CXC-FPSVC-*] the fingerprint_storage service surface).  It is a
GENUINE gap in the catalog, not a fork: ``label`` is single-line and never
subscribes; ``markdown_viewer`` renders multi-line but is not a bus
subscriber (its ``content`` is not a bindable attribute); the console list
widgets (master_detail / doc_browser / store_browser) are model-coupled to
the Intelligence Console.  This widget is the small, generic, reusable
missing piece: subscribe ONE namespace, render whatever text lands there as a
scrolling-free wrapped list under a title.

It is DELIBERATELY dumb about the domain — the binder
(``examples/fingerprint/binder.py``) formats the person / casting / RBAC /
push-status model into plain lines and publishes them; this widget just draws
them.  So the same widget renders the roster, the castings, the roles, and the
push receipt with zero per-panel code.

⛔ FUNCTIONAL PLACEHOLDER STYLING.  The panel chrome here (dark card, one title
row, monospace body) is a scaffold so the FUNCTION is visible and pixel-
verifiable.  The Colonial (Battlestar Galactica) visual language is David's to
author — this widget draws only against the Canvas contract, so a restyle is a
draw() change, never a rewire.

on_bus_state accepts either a ``str`` (split on newlines) or a ``list`` of
scalars (one line each), so a producer can publish whichever is convenient.
Redraw discipline: ``request_redraw()`` with no args ([CXC-R-DAMAGE]).
"""

from __future__ import annotations

from typing import Any, List

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TITLE_H = 22.0
_LINE_H = 17.0
_PAD = 10.0
_TITLE_COLOR = (0x8A, 0xD4, 0xFF)          # cool cyan — placeholder accent
_BODY_COLOR = style.TEXT
_EMPTY_COLOR = style.TEXT_DIM
_PANEL_BG = (0x1E, 0x20, 0x28, 235)
_PANEL_BORDER = (0x3A, 0x3E, 0x4C)


class InfoListWidget(Widget):
    """Titled multi-line text panel, driven by a single bus namespace."""

    def __init__(self, widget_id: str, *,
                 title: str = "",
                 namespace: str = "",
                 empty_text: str = "(empty)",
                 mono: bool = True,
                 title_color: Any = _TITLE_COLOR,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "info_list", **kwargs)
        self.title = str(title)
        self.bound_namespace = str(namespace)
        self.empty_text = str(empty_text)
        self.font_family = style.FONT_MONO if mono else style.FONT_UI
        self.title_color = tuple(title_color)
        self.lines: List[str] = []
        # a read surface — selectable text affordance ([CXC-R-ENGINE-HOVER]),
        # no editing.
        self.text_selectable = True

    # -- bus reflection ----------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        self.lines = self._coerce(value)
        self.request_redraw()

    @staticmethod
    def _coerce(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return value.split("\n") if value else []
        if isinstance(value, (list, tuple)):
            out: List[str] = []
            for item in value:
                out.extend(str(item).split("\n"))
            return out
        return [str(value)]

    # -- measure / draw ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        rows = max(1, len(self.lines))
        h = _TITLE_H + rows * _LINE_H + 2 * _PAD
        return Size(min(420.0, constraints.max_width),
                    min(h, constraints.max_height))

    def _wrap(self, canvas: Canvas, text: str, avail: float) -> List[str]:
        """Greedy word wrap on the measured advance of the current font.  A
        single over-long token is hard-broken so nothing paints past the box."""
        if not text:
            return [""]
        out: List[str] = []
        line = ""
        for word in text.split(" "):
            trial = word if not line else line + " " + word
            if canvas.measure_text(trial).width <= avail or not line:
                # accept, but hard-break a lone token wider than the box
                if canvas.measure_text(trial).width > avail and not line:
                    broken = word
                    while canvas.measure_text(broken).width > avail and len(broken) > 1:
                        cut = len(broken)
                        while cut > 1 and canvas.measure_text(broken[:cut]).width > avail:
                            cut -= 1
                        out.append(broken[:cut])
                        broken = broken[cut:]
                    line = broken
                else:
                    line = trial
            else:
                out.append(line)
                line = word
        out.append(line)
        return out

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        # panel
        canvas.set_fill_color(*_PANEL_BG)
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 8)
        canvas.set_stroke_color(*_PANEL_BORDER)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(rect.x + 0.5, rect.y + 0.5,
                                 rect.w - 1, rect.h - 1, 8)

        # title
        if self.title:
            canvas.set_fill_color(*self.title_color)
            canvas.set_font(style.FONT_UI, 13, "bold")
            canvas.draw_text(self.title, rect.x + _PAD,
                             rect.y + _PAD + 12, "left")

        # body (clipped to the card)
        canvas.save()
        canvas.clip_rect(rect.x + 1, rect.y + _TITLE_H, rect.w - 2,
                         rect.h - _TITLE_H - 1)
        canvas.set_font(self.font_family, 12, "regular")
        avail = rect.w - 2 * _PAD
        y = rect.y + _TITLE_H + _PAD
        if not self.lines:
            canvas.set_fill_color(*_EMPTY_COLOR)
            canvas.draw_text(self.empty_text, rect.x + _PAD, y + 12, "left")
        else:
            canvas.set_fill_color(*_BODY_COLOR)
            for raw in self.lines:
                for seg in self._wrap(canvas, raw, avail):
                    if y > rect.y + rect.h:
                        break
                    canvas.draw_text(seg, rect.x + _PAD, y + 12, "left")
                    y += _LINE_H
        canvas.restore()
        canvas.restore()


# -- registration ([CXC-ICP-FOUND-B] / [CXC-PROJECT-WIDGETS]) ---------------
# A NEW widget is "its module + ONE descriptor with a ``build`` hook, no
# build_leaf edit".  A scene declares ``widget_modules: [composeio.render.
# widgets.fingerprint_panel]`` so the parser imports THIS module (self-
# registering the type) before it resolves any ``type:`` — zero engine edits.

def _build_info_list(spec: dict, wid: str, comp: Any) -> Widget:
    w = InfoListWidget(
        wid,
        title=str(spec.get("title", spec.get("label", ""))),
        namespace=str(spec.get("namespace", "")),
        empty_text=str(spec.get("empty_text", "(empty)")),
        mono=bool(spec.get("mono", True)),
    )
    if w.bound_namespace:
        comp.subscriptions.append((w.bound_namespace, w))
    return w


try:  # register at import time; tolerate a partially-initialized engine
    from ..engine.widget_registry import WidgetDescriptor, register

    register(WidgetDescriptor(
        type="info_list", cls=InfoListWidget, display_name="Info List",
        required_namespaces=[], category="collaboration",
        categories=("collaboration",), aliases=("infolist", "text_panel"),
        build=_build_info_list, min_size=(220, 80)))
except Exception:  # pragma: no cover - registry import guard
    pass
