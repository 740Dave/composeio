"""
ComposeUX ConsolePrefsWidget — the Configuration-tab APPEARANCE selector, the
app-surface WRITE side of the render-deep personalization ([SPEC:CXC-CONSOLE-PROD]
[CXC-ICP-S3-THEME] / [CXC-ICP-S2-FONTSCALE], David's D2 dark-custom-colors add).

personalization.py (compose_projects/intelligence/console) is the READ+APPLY half: it reads
Foundation A's ``meta.console`` at launch and skins the process.  THIS widget is
the WRITE half: a maker picks a theme / a font step / a dark bg+accent, and the
widget PUBLISHES the edit on Foundation A's namespace family
``compose_projects/intelligence/<id>/config/console/*`` — ``config_persister`` folds it into
``meta.console`` (durable), and the NEXT launch applies it (modulo restart,
[CXC-ICP-RESTART]).  The widget STOPS at the publish (bus-native; it never
touches disk or the theme engine directly).

Three cards, all DRIVEN BY the registry — never a hardcoded literal:
  * **Theme** — one chip per ``console_theme.list_themes()`` (dark / light /
    high-contrast); a tap publishes ``.../console/theme``.
  * **Font size** — one chip per ``console_theme.FONT_SCALE_STEPS`` (S / M / L,
    David's D3 discrete ruling); a tap publishes the STEP'S FLOAT on
    ``.../console/font_scale`` (a float — ``config_persister`` coerces
    ``font_scale`` via ``float()``, so the numeric value persists, not the label).
  * **Custom (dark)** — David's D2: for the DARK theme, a BACKGROUND swatch row +
    an ACCENT/HIGHLIGHT swatch row (+ a Reset); a tap publishes the merged
    ``{bg, accent}`` on ``.../console/theme_custom``.  The render-deep applier
    already reads ``console.theme_custom`` and overlays it — no draw-fn change.

Reuse-by-reference (R3 / narrow waist): the SAME ``console_theme`` tokens +
chrome (``draw_card`` / ``draw_card_header`` / ``ellipsize``) as the rail cards
and ``config_settings``.  Damage discipline ([CXC-R-DAMAGE]): bus updates + taps
call ONLY ``self.request_redraw()``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce   # reuse-by-reference: the same reader

# Foundation A namespace family ([CXC-ICP-FOUND-A]); the id-scoped prefix is
# resolved by the scene (the console points these at intelligence/architect/...).
DEFAULT_THEME_NS = "intelligence/architect/config/console/theme"
DEFAULT_FONT_NS = "intelligence/architect/config/console/font_scale"
DEFAULT_CUSTOM_NS = "intelligence/architect/config/console/theme_custom"
DEFAULT_SCROLL_NS = "intelligence/architect/config/console/scroll_direction"

# [CXC-ICP-S6-SCROLLDIR] the two scroll-direction choices, in offered order.
# (label shown on the chip, persisted value).
_SCROLL_CHOICES: List[Tuple[str, str]] = [
    ("Natural", "natural"),
    ("Traditional", "traditional"),
]

# Curated swatch palettes for the dark custom-color pickers (David's D2 examples:
# the send-button color + the account bar color — both accent-driven — plus the
# ground).  Every entry is (label, [r,g,b]); the last bg/accent restores default.
_BG_SWATCHES: List[Tuple[str, List[int]]] = [
    ("ink", [7, 10, 15]),         # the shipped DARK ground (default)
    ("void", [3, 4, 8]),
    ("slate", [14, 16, 22]),
    ("plum", [16, 8, 22]),
    ("forest", [6, 16, 12]),
    ("navy", [6, 10, 26]),
]
_ACCENT_SWATCHES: List[Tuple[str, List[int]]] = [
    ("azure", [78, 168, 255]),    # the shipped DARK accent (default)
    ("amber", [255, 138, 0]),
    ("magenta", [230, 60, 160]),
    ("green", [47, 208, 123]),
    ("violet", [150, 120, 255]),
    ("cyan", [40, 210, 220]),
]

_CHIP_H = 30.0
_CHIP_PAD = 12.0
_CHIP_GAP = 8.0
_SW = 34.0            # swatch size
_SW_GAP = 8.0


def _base(canvas: Canvas, cy: float) -> float:
    m = canvas.measure_text("Ag")
    return cy + (m.ascent - m.descent) / 2.0


class ConsolePrefsWidget(Widget):
    """The Configuration tab's Appearance selector (theme · font size · dark
    custom colors) — publishes Foundation A ``console/*`` edits, bus-native."""

    def __init__(self, widget_id: str, *,
                 theme_ns: str = DEFAULT_THEME_NS,
                 font_ns: str = DEFAULT_FONT_NS,
                 custom_ns: str = DEFAULT_CUSTOM_NS,
                 scroll_ns: str = DEFAULT_SCROLL_NS,
                 sections: Optional[List[str]] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "console_prefs", **kwargs)
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.theme_ns = str(theme_ns or DEFAULT_THEME_NS)
        self.font_ns = str(font_ns or DEFAULT_FONT_NS)
        self.custom_ns = str(custom_ns or DEFAULT_CUSTOM_NS)
        self.scroll_ns = str(scroll_ns or DEFAULT_SCROLL_NS)
        # ADDITIVE section filter (mirrors config_settings): a subset of
        # {theme, font, custom, scroll}; unset draws all four stacked.
        self.sections: Optional[List[str]] = (
            [str(s).strip().lower() for s in sections] if sections else None)
        self.interactive = True
        # local selection state, seeded from the theme engine so a static render
        # shows the live look; a bus re-seed (config_persister) overrides.
        self._theme: str = ct.CONSOLE.active.name
        self._font_scale: float = float(ct.CONSOLE.font_scale or 1.0)
        self._custom: Dict[str, List[int]] = {}
        # [CXC-ICP-S6-SCROLLDIR] scroll-direction preference, default "natural"
        # (respect the OS delta); a bus re-seed from config_persister overrides.
        self._scroll_direction: str = "natural"
        # hit geometry recorded by draw(): (x0,y0,x1,y1, kind, value)
        self._zones: List[Tuple[float, float, float, float, str, Any]] = []

    # -- bus wiring (reflect the persisted value; the persister re-seeds each) --

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if namespace == self.theme_ns:
            if isinstance(payload, str) and payload.strip():
                self._theme = payload.strip().lower()
                self.request_redraw()
            return
        if namespace == self.font_ns:
            fs = ct.resolve_font_scale(payload)
            if fs != self._font_scale:
                self._font_scale = fs
                self.request_redraw()
            return
        if namespace == self.custom_ns:
            norm = ct.normalize_custom(payload)
            self._custom = {k: list(v) for k, v in norm.items()}
            self.request_redraw()
            return
        if namespace == self.scroll_ns:
            if isinstance(payload, str) and payload.strip().lower() in (
                    "natural", "traditional"):
                self._scroll_direction = payload.strip().lower()
                self.request_redraw()
            return

    # -- lifecycle / measure ---------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        self._theme = ct.CONSOLE.active.name
        self._font_scale = float(ct.CONSOLE.font_scale or 1.0)

    # -- draw ------------------------------------------------------------------

    def _section_drawers(self):
        return {
            "theme": self._draw_theme_card,
            "font": self._draw_font_card,
            "custom": self._draw_custom_card,
            "scroll": self._draw_scroll_card,
        }

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._zones = []
        canvas.save()
        pad = ct.PAD_CARD_BODY
        keys = self.sections if self.sections is not None \
            else ["theme", "font", "custom", "scroll"]
        drawers = self._section_drawers()
        y = rect.y
        for i, key in enumerate(keys):
            fn = drawers.get(key)
            if fn is None:
                continue
            if i:
                y += ct.GAP_RAIL
            y = fn(canvas, rect.x, y, rect.w, t, pad)
        canvas.restore()

    # -- theme picker ----------------------------------------------------------

    def _draw_theme_card(self, canvas: Canvas, x: float, y: float, w: float,
                         t, pad: float) -> float:
        hh = self._header_h(canvas)
        themes = ct.list_themes()          # registry-driven — NEVER a literal
        card_h = hh + 12.0 + _CHIP_H + 12.0 + 15.0 + 10.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w, "Theme", ns_tag="console/theme",
                                 t=t)
        cy = by + 12.0 + _CHIP_H / 2.0
        cx = x + pad
        for name in themes:
            selected = (name == self._theme)
            cw = self._chip(canvas, cx, cy, name.replace("-", " "), selected, t)
            self._zones.append((cx, cy - _CHIP_H / 2.0, cx + cw,
                                cy + _CHIP_H / 2.0, "theme", name))
            cx += cw + _CHIP_GAP
        ny = by + 12.0 + _CHIP_H + 12.0
        self._note(canvas, "Applies at launch (modulo restart).", x + pad, ny, t)
        return y + card_h

    # -- font-scale picker -----------------------------------------------------

    def _draw_font_card(self, canvas: Canvas, x: float, y: float, w: float,
                        t, pad: float) -> float:
        hh = self._header_h(canvas)
        steps = ct.FONT_SCALE_STEPS        # registry-driven S/M/L (David D3)
        card_h = hh + 12.0 + _CHIP_H + 12.0 + 15.0 + 10.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w, "Font size",
                                 ns_tag="console/font_scale", t=t)
        cy = by + 12.0 + _CHIP_H / 2.0
        cx = x + pad
        cur = self._nearest_step()
        for label in ("s", "m", "l"):
            if label not in steps:
                continue
            selected = (label == cur)
            txt = f"{label.upper()}  {steps[label]:g}x"
            cw = self._chip(canvas, cx, cy, txt, selected, t)
            self._zones.append((cx, cy - _CHIP_H / 2.0, cx + cw,
                                cy + _CHIP_H / 2.0, "font", steps[label]))
            cx += cw + _CHIP_GAP
        ny = by + 12.0 + _CHIP_H + 12.0
        self._note(canvas, "Scales ALL console text uniformly.", x + pad, ny, t)
        return y + card_h

    # -- scroll-direction picker ([CXC-ICP-S6-SCROLLDIR]) ----------------------

    def _draw_scroll_card(self, canvas: Canvas, x: float, y: float, w: float,
                          t, pad: float) -> float:
        hh = self._header_h(canvas)
        card_h = hh + 12.0 + _CHIP_H + 12.0 + 15.0 + 10.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w, "Scroll direction",
                                 ns_tag="console/scroll_direction", t=t)
        cy = by + 12.0 + _CHIP_H / 2.0
        cx = x + pad
        for label, value in _SCROLL_CHOICES:
            selected = (value == self._scroll_direction)
            cw = self._chip(canvas, cx, cy, label, selected, t)
            self._zones.append((cx, cy - _CHIP_H / 2.0, cx + cw,
                                cy + _CHIP_H / 2.0, "scroll", value))
            cx += cw + _CHIP_GAP
        ny = by + 12.0 + _CHIP_H + 12.0
        self._note(canvas, "Natural matches your OS; Traditional inverts it.",
                   x + pad, ny, t)
        return y + card_h

    def _nearest_step(self) -> str:
        best, bestd = "m", 1e9
        for label, val in ct.FONT_SCALE_STEPS.items():
            d = abs(val - self._font_scale)
            if d < bestd:
                best, bestd = label, d
        return best

    # -- dark custom colors ----------------------------------------------------

    def _draw_custom_card(self, canvas: Canvas, x: float, y: float, w: float,
                          t, pad: float) -> float:
        hh = self._header_h(canvas)
        is_dark = (self._theme == "dark")
        row_h = _SW + 22.0
        note_h = 15.0 if is_dark else 30.0
        card_h = hh + 10.0 + 2 * row_h + 10.0 + note_h + 10.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w, "Custom colors",
                                 ns_tag="console/theme_custom",
                                 count_text="dark only",
                                 count_color=t.accent if is_dark else t.faint,
                                 t=t)
        ry = by + 10.0
        ry = self._swatch_row(canvas, x + pad, ry, "Background", "bg",
                              _BG_SWATCHES, is_dark, t)
        ry = self._swatch_row(canvas, x + pad, ry, "Accent", "accent",
                              _ACCENT_SWATCHES, is_dark, t)
        # Reset chip
        rcy = ry + _CHIP_H / 2.0
        if is_dark:
            rw = self._chip(canvas, x + pad, rcy, "Reset", False, t)
            self._zones.append((x + pad, rcy - _CHIP_H / 2.0, x + pad + rw,
                                rcy + _CHIP_H / 2.0, "custom_reset", None))
        ny = ry + _CHIP_H + 8.0
        msg = ("Personalize the DARK ground + highlight (send button, bars)."
               if is_dark else
               "Custom colors apply to the DARK theme — pick Dark above to edit.")
        self._note(canvas, msg, x + pad, ny, t)
        return y + card_h

    def _swatch_row(self, canvas: Canvas, x: float, y: float, label: str,
                    channel: str, swatches: List[Tuple[str, List[int]]],
                    enabled: bool, t) -> float:
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        ct.draw_tracked(canvas, label.upper(), x, _base(canvas, y + 7.0), 0.6,
                        t.dim if enabled else t.faint)
        sy = y + 16.0
        sel = self._custom.get(channel)
        sx = x
        for name, rgb in swatches:
            is_sel = (sel is not None and list(sel)[:3] == rgb[:3])
            self._swatch(canvas, sx, sy, rgb, is_sel, enabled, t)
            if enabled:
                self._zones.append((sx, sy, sx + _SW, sy + _SW,
                                    "custom:" + channel, rgb))
            sx += _SW + _SW_GAP
        return sy + _SW + 6.0

    def _swatch(self, canvas: Canvas, x: float, y: float, rgb: List[int],
                selected: bool, enabled: bool, t) -> None:
        a = 255 if enabled else 90
        canvas.set_fill_color(rgb[0], rgb[1], rgb[2], a)
        canvas.fill_round_rect(x, y, _SW, _SW, 8.0)
        ring = t.accent if selected else t.line2
        canvas.set_stroke_color(ring[0], ring[1], ring[2],
                                255 if selected else 160)
        canvas.set_stroke_width(2.0 if selected else 1.0)
        canvas.stroke_round_rect(x + 1.0, y + 1.0, _SW - 2, _SW - 2, 8.0)

    # -- a selectable chip -----------------------------------------------------

    def _chip(self, canvas: Canvas, x: float, cy: float, text: str,
              selected: bool, t) -> float:
        canvas.set_font(ct.FONT_MONO, 11.0, "medium" if selected else "regular")
        tw = canvas.measure_text(text).width
        cw = tw + 2 * _CHIP_PAD
        y = cy - _CHIP_H / 2.0
        canvas.set_fill_color(*(t.accent_dim if selected else t.panel3))
        canvas.fill_round_rect(x, y, cw, _CHIP_H, 8.0)
        line = t.accent if selected else t.line2
        canvas.set_stroke_color(line[0], line[1], line[2],
                                255 if selected else 160)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, cw - 1, _CHIP_H - 1, 8.0)
        canvas.set_fill_color(*(t.accent if selected else t.dim))
        canvas.draw_text(text, x + _CHIP_PAD, _base(canvas, cy), "left")
        return cw

    # -- helpers ---------------------------------------------------------------

    def _header_h(self, canvas: Canvas) -> float:
        m = canvas.measure_text("Ag")
        return ct.PAD_CARD_HEADER[0] * 2 + m.ascent + m.descent

    def _note(self, canvas: Canvas, text: str, x: float, y: float, t) -> None:
        canvas.set_font(ct.FONT_UI, 11.5, "regular")
        m = canvas.measure_text(text)
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(text, x, y + m.ascent, "left")

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        for (x0, y0, x1, y1, kind, value) in self._zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._activate(kind, value)
                return

    def _activate(self, kind: str, value: Any) -> None:
        if kind == "theme":
            self._theme = str(value)
            self.publish(self.theme_ns, self._theme)
        elif kind == "font":
            self._font_scale = float(value)
            # publish the FLOAT — config_persister coerces font_scale via float()
            self.publish(self.font_ns, self._font_scale)
        elif kind.startswith("custom:"):
            channel = kind.split(":", 1)[1]
            self._custom[channel] = list(value)
            self.publish(self.custom_ns, {k: list(v)
                                          for k, v in self._custom.items()})
        elif kind == "custom_reset":
            self._custom = {}
            self.publish(self.custom_ns, {})     # empty map = clear (persisted)
        elif kind == "scroll":
            # [CXC-ICP-S6-SCROLLDIR] publish the string; config_persister folds
            # it into meta.console.scroll_direction (applies at next launch,
            # modulo restart — same as theme/font).
            self._scroll_direction = str(value)
            self.publish(self.scroll_ns, self._scroll_direction)
        self.request_redraw()
