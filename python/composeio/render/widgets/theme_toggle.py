"""
ComposeUX ThemeToggleWidget — the console header theme button
([SPEC:CXC-CONSOLE-VISUAL] [CXC-ICV-CHROME] / [CXC-ICV-THEME]).

The pre-vis ``.themebtn``: a panel-3 pill with a tracked UPPERCASE mono
label.  Tapping it re-points the console_theme ``active`` table
(dark <-> light) and re-syncs the scene background pass through the
EngineContext's ``set_background`` hook — the token swap in action: no
widget code changes, one tap re-skins every console surface.

NOTE: colors declared literally in the scene YAML (header labels,
composer chrome) are scene DATA, not token reads — they do not follow a
live toggle.  The full light scene ships as its own design.theme: light
document; the toggle is the interactive proof of [CXC-ICV-THEME].
"""

from __future__ import annotations

from typing import Any

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style


class ThemeToggleWidget(Widget):
    """Tap-to-toggle dark/light ([CXC-ICV-THEME])."""

    def __init__(self, widget_id: str, *,
                 label: str = "theme",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "theme_toggle", **kwargs)
        self.label = str(label)
        self.interactive = True

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def on_tap(self, x: float, y: float) -> None:
        ct.toggle_theme()
        t = ct.CONSOLE.active
        # re-sync the scene background pass (ground + grid + wash) so the
        # swap is coherent edge-to-edge; graceful when the host lacks it.
        setter = getattr(self._ctx, "set_background", None)
        if callable(setter):
            setter(t.bg,
                   grid={"color": list(t.bg_grid), "size": 40},
                   wash={"color": list(t.accent_dim),
                         "cx": 0.84, "cy": -0.10, "r": 0.9})
        self.request_redraw()

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(78.0, constraints.max_width),
                    min(30.0, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 8)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(rect.x + 0.5, rect.y + 0.5,
                                 rect.w - 1, rect.h - 1, 8)
        canvas.set_font(style.FONT_MONO, 9.0, "regular")
        text = self.label.upper()
        tw = ct.measure_tracked(canvas, text, ct.TRACK_LABEL)
        m = canvas.measure_text("Ag")
        base = rect.y + rect.h / 2 + (m.ascent - m.descent) / 2
        ct.draw_tracked(canvas, text, rect.x + (rect.w - tw) / 2, base,
                        ct.TRACK_LABEL, t.dim)
        canvas.restore()
