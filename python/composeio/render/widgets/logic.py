"""
ComposeUX Logic Widget ([SPEC:CXC-FLAUTH] [CXC-FLAUTH-CREATE])

The headless-widget carrier type the Composer's ＋ Logic node creates:
a widget that computes on the bus and renders NOTHING.  Its work is
declarative — attr/value bindings, behaviors, and ``outputs:`` exprs
([CXC-DSL-SITES]) — all handled by the shared parse/tick machinery in
engine/parser.py + RenderApp.tick; the class itself is deliberately
empty of logic.

Geometry-free by design ([CXC-FL-HEADLESS]): it measures 0x0 and its
draw is a no-op, so a logic node placed in a composite's pixel stack
never affects the visual output — the Flow lens is its authoring home,
and a HeadlessRuntime is its deploy target ([SPEC:CXC-HEADLESS]).
"""

from __future__ import annotations

from typing import Any

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget


class LogicWidget(Widget):
    """Headless logic node: bindable ``value``/``label``, no render."""

    def __init__(self, widget_id: str, *, value: float = 0.0,
                 label: str = "", **kwargs: Any) -> None:
        super().__init__(widget_id, "logic", **kwargs)
        self.value = value
        self.label = label

    def measure(self, constraints: Constraints) -> Size:
        return Size(0.0, 0.0)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        pass                    # no render — [CXC-FLAUTH-NODE] "headless"
