"""
ComposeUX AccountCardWidget — the consolidated "Account" rail card
([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION], previs
``docs/intelligence-console/previs/intelligence-consolidated.html`` rev 5).

The windowed rate-limit bars (5-hour / 7-day) off ``self/rate_limit`` plus a
``Sign in ▸`` control at the foot.  No Fable-weekly bar (dropped — no source).

The header is near-white ink, tracked UPPERCASE mono, no leading dot, no
namespace label; ``· <id>`` is a mock string until the ``~/.claude.json``
publisher lands (the architect's follow-on).  ``Sign in ▸`` publishes a stub
intent ``self/account/signin`` and STOPS — the actuation is the architect's.

Rate-limit payload (graceful; ``self_ledger`` idiom):
    {"5h": {"pct": 38, "resets": "14:22"}, "7d": {"pct": 12}}
    {"5h": 38, "7d": 12}                       (bare pct)

Damage discipline: ``self.request_redraw()`` (no args) only.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style

DEFAULT_NAMESPACE = "self/rate_limit"
DEFAULT_SIGNIN_NAMESPACE = "self/account/signin"

PAD = 14.0
HEAD_H = 34.0
BAR_H = 40.0             # a labelled bar (label row + track)
BTN_H = 34.0
PAD_BOTTOM = 14.0


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        s = value.strip()
        if s and s[0] in "{[":
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return value
    return value


def _signin_tri(canvas: Canvas, x: float, cy: float, w: float, h: float,
                color) -> None:
    """The ▸ affordance as a filled path (Plex has no U+25B8;
    [CXC-R-DP-1] decompose to the contract, never a font)."""
    canvas.set_fill_color(*color)
    canvas.fill_path([MoveTo(x, cy - h / 2.0),
                      LineTo(x + w, cy),
                      LineTo(x, cy + h / 2.0),
                      Close()])


class AccountCardWidget(Widget):
    """The consolidated Account rail card ([DEC-INTELLIGENCE-WIDGET-
    CONSOLIDATION])."""

    def __init__(self, widget_id: str, *,
                 account_id: str = "david740burnett",
                 namespace: str = DEFAULT_NAMESPACE,
                 signin_namespace: str = DEFAULT_SIGNIN_NAMESPACE,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "account_card", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.account_id = str(account_id)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.signin_namespace = str(signin_namespace
                                    or DEFAULT_SIGNIN_NAMESPACE)
        self.interactive = True
        self._rl: dict = {}
        self._btn_rect: Optional[Tuple[float, float, float, float]] = None

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if isinstance(payload, dict):
            self._rl = payload
            self.request_redraw()

    def _bars(self) -> List[Tuple[str, Optional[float], str]]:
        """[(label, pct, resets-suffix)] for the 5h / 7d windows."""
        out = []
        for key, alt, label in (("5h", "h5", "5-hour window"),
                                 ("7d", "d7", "7-day window")):
            entry = self._rl.get(key, self._rl.get(alt)) \
                if isinstance(self._rl, dict) else None
            pct, resets = None, ""
            if isinstance(entry, dict):
                try:
                    pct = float(entry.get("pct"))
                except (TypeError, ValueError):
                    pct = None
                if entry.get("resets"):
                    resets = f"resets {entry['resets']}"
            elif entry is not None:
                try:
                    pct = float(entry)
                except (TypeError, ValueError):
                    pct = None
            out.append((label, pct, resets))
        return out

    # -- lifecycle -------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def measure(self, constraints: Constraints) -> Size:
        need = HEAD_H + 2 * BAR_H + 8.0 + BTN_H + PAD_BOTTOM
        return Size(min(332.0, constraints.max_width),
                    min(constraints.max_height, need))

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD, t)
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD)

        # header: tracked UPPERCASE mono ink, no dot, "· <id>"
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        hm = canvas.measure_text("Ag")
        base = rect.y + 13.0 + hm.ascent
        title = f"ACCOUNT · {self.account_id.upper()}"
        ct.draw_tracked(canvas, title, rect.x + PAD, base, ct.TRACK_LABEL,
                        t.ink)

        y = rect.y + HEAD_H
        for label, pct, resets in self._bars():
            self._draw_bar(canvas, rect, y, label, pct, resets, t)
            y += BAR_H
        y += 8.0

        # Sign in ▸ button (bordered accent, centered)
        bx = rect.x + PAD
        bw = rect.w - 2 * PAD
        canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 71)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(bx, y, bw, BTN_H, 8.0)
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        lbl = "Sign in"
        lm = canvas.measure_text(lbl)
        tri_w, tri_gap = 4.5, 6.0
        total = lm.width + tri_gap + tri_w
        lx = rect.x + rect.w / 2.0 - total / 2.0
        lcy = y + BTN_H / 2.0
        canvas.set_fill_color(*t.accent)
        canvas.draw_text(lbl, lx, lcy + (lm.ascent - lm.descent) / 2.0, "left")
        _signin_tri(canvas, lx + lm.width + tri_gap, lcy, tri_w, 8.0, t.accent)
        self._btn_rect = (bx, y, bw, BTN_H)

        canvas.restore()
        canvas.restore()

    def _draw_bar(self, canvas, rect, y, label, pct, resets, t) -> None:
        # label row: LABEL (faint tracked) · PCT (ink bold) ... resets (right)
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        lm = canvas.measure_text("Ag")
        lbase = y + 6.0 + lm.ascent
        lw = ct.draw_tracked(canvas, label.upper(), rect.x + PAD, lbase, 0.7,
                             t.faint)
        pct_txt = f"{pct:.0f}%" if pct is not None else "—"
        canvas.set_font(ct.FONT_MONO, 12.0, "bold")
        pm = canvas.measure_text(pct_txt)
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(pct_txt, rect.x + PAD + lw + 8, lbase, "left")
        if resets:
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            rm = canvas.measure_text(resets)
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(resets, rect.x + rect.w - PAD - rm.width, lbase,
                             "left")
        # track + fill (accent, both windows per the previs)
        ty = y + 6.0 + lm.ascent + lm.descent + 6.0
        tw = rect.w - 2 * PAD
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(rect.x + PAD, ty, tw, 6.0, 3.0)
        if pct:
            frac = max(0.0, min(1.0, pct / 100.0))
            canvas.set_fill_color(*t.accent)
            canvas.fill_round_rect(rect.x + PAD, ty, max(6.0, tw * frac), 6.0,
                                   3.0)

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        r = self._btn_rect
        if r and r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]:
            # stub intent — the architect wires the actuation (inject /login).
            self.publish(self.signin_namespace, "on")
