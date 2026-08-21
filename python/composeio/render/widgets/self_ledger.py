"""
ComposeUX SelfLedgerWidget — the self/* telemetry grid
([SPEC:CXC-CONSOLE-VISUAL] [CXC-ICV-LEDGER]).

The pre-vis ``.ledger`` card: a CELL GRID with 1px ``line`` gutters
(cells ``panel`` fill) — a top row of two cells (subagents, compactions),
then a full-width ``effort`` cell — then TWO full-width rate-limit cells
each carrying a mini BAR (5h window: accent; 7d window: ok — fixed per
the pre-vis, NOT state-driven; the rate-limit windows are not alarms
here — calm rule).

The widget subscribes a LIST of ``self/*`` ledger namespaces (the
chat_thread ``namespaces:`` idiom); each ``on_bus_state`` caches that
namespace's latest payload and the cells derive from the cache.  A
namespace that has not published renders "—" (graceful).

Payload shapes accepted per cell (all graceful):
    self/effort        "high" | {"level": "high"}
    self/compactions   3 | {"count": 3, "ids": ["c9","c10","c11"]}
    self/rate_limit    {"5h": 38, "7d": 12}
                       | {"5h": {"pct": 38, "resets": "14:22"},
                          "7d": {"pct": 12}}
    self/subagents     the live dispatch events ({status, id, tokens…}) —
                       the cell counts running ids + sums done tokens
    self/output_tokens 63200 | {"total": 63200}
"""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style
from .subagent_ledger import format_tokens

DEFAULT_NAMESPACES = (
    "self/rate_limit",
    "self/compactions",
    "self/output_tokens",
    "self/effort",
    "self/subagents",
)


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        s = value.strip()
        if s and s[0] in "{[":
            try:
                return json.loads(s)
            except (ValueError, TypeError):
                return value
    return value


class SelfLedgerWidget(Widget):
    """The 2-column self/* telemetry cell grid ([CXC-ICV-LEDGER])."""

    def __init__(self, widget_id: str, *,
                 label: str = "self / ledger",
                 ns_tag: str = "self/*",
                 namespaces: Optional[List[str]] = None,
                 font_size: float = 10.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "self_ledger", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        self.ns_tag = str(ns_tag)
        self.namespaces = list(namespaces) if namespaces \
            else list(DEFAULT_NAMESPACES)
        self.font_size = float(font_size)
        # namespace tail ("cost", "rate_limit", …) -> latest payload
        self._state: Dict[str, Any] = {}
        # subagent tally derived from self/subagents events
        self._sub_running: "OrderedDict[str, bool]" = OrderedDict()
        self._sub_tokens: float = 0.0

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        tail = namespace.rsplit("/", 1)[-1].lower()
        payload = _coerce(value)
        if tail == "subagents" and isinstance(payload, dict):
            rid = str(payload.get("id") or f"_{len(self._sub_running)}")
            status = str(payload.get("status") or "dispatched").lower()
            self._sub_running[rid] = status != "done"
            try:
                self._sub_tokens += float(payload.get("tokens") or 0)
            except (TypeError, ValueError):
                pass
        self._state[tail] = payload
        self.request_redraw()

    # -- cell derivation ---------------------------------------------------------

    @staticmethod
    def _pick(payload: Any, *keys: str) -> Any:
        if isinstance(payload, dict):
            for k in keys:
                if k in payload:
                    return payload[k]
            return None
        return payload

    def _cell_values(self):
        """[(k label, v main, v suffix, v accent?)] for the three small
        cells (subagents, compactions, effort); missing state -> em-dash.
        The draw pass lays the first two side-by-side and effort full-width."""
        # subagents
        if self._sub_running:
            n = sum(1 for r in self._sub_running.values() if r)
            tok = format_tokens(self._sub_tokens)
            sub_v, sub_s = str(n), f"· {tok} tok" if tok else ""
        else:
            sub_v, sub_s = "—", ""
        # compactions
        comp = self._state.get("compactions")
        cnt = self._pick(comp, "count")
        ids = self._pick(comp, "ids") if isinstance(comp, dict) else None
        comp_v = "—" if cnt is None else f"{cnt}"
        comp_s = "· " + "·".join(str(i) for i in ids) \
            if isinstance(ids, (list, tuple)) and ids else ""
        # effort (value in accent per the pre-vis)
        eff = self._pick(self._state.get("effort"), "level", "value")
        eff_v = str(eff) if eff is not None else "—"
        return [
            ("subagents", sub_v, sub_s, False),
            ("compactions", comp_v, comp_s, False),
            ("effort", eff_v, "", eff_v != "—"),
        ]

    def _rate_cells(self):
        """[(k label, pct or None, suffix, bar color token name)] for the
        two wide rate-limit cells."""
        rl = self._state.get("rate_limit")
        out = []
        for key, alt, tname in (("5h", "h5", "accent"), ("7d", "d7", "ok")):
            entry = None
            if isinstance(rl, dict):
                entry = rl.get(key, rl.get(alt))
            pct, suffix = None, ""
            if isinstance(entry, dict):
                try:
                    pct = float(entry.get("pct"))
                except (TypeError, ValueError):
                    pct = None
                if entry.get("resets"):
                    suffix = f"· resets {entry['resets']}"
            elif entry is not None:
                try:
                    pct = float(entry)
                except (TypeError, ValueError):
                    pct = None
            out.append((f"rate_limit · {key} window", pct, suffix, tname))
        return out

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(320.0, constraints.max_width),
                    min(constraints.max_height, 210.0))

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h,
                     ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, rect.x, rect.y, rect.w,
                                     self.label, self.ns_tag, t=t)

        pad = ct.PAD_CARD_BODY
        gx = rect.x + pad
        gy = body_y + pad * 0.8
        gw = rect.w - 2 * pad
        gh = rect.y + rect.h - gy - pad
        if gw < 40 or gh < 40:
            canvas.restore()
            return

        # grid chrome: a `line` plate behind, cells inset by 1px gutters
        canvas.set_fill_color(*t.line)
        canvas.fill_round_rect(gx, gy, gw, gh, 8)
        # cell heights: content-driven with a floor so k+v never clip
        small_h = min(48.0, max(38.0, (gh - 3) * 0.225))
        wide_h = ((gh - 3) - 2 * small_h) / 2.0
        col_w = (gw - 3) / 2.0

        def cell(cx: float, cy: float, cw_: float, ch_: float):
            canvas.set_fill_color(*t.panel)
            canvas.fill_rect(cx, cy, cw_, ch_)
            return cx + 10.0, cy + 7.0

        def k_label(kx: float, ky: float, text: str):
            canvas.set_font(style.FONT_MONO, 8.4, "regular")
            m = canvas.measure_text("Ag")
            ct.draw_tracked(canvas, text.upper(), kx, ky + m.ascent, 1.0,
                            t.faint)
            return ky + m.ascent + m.descent + 3

        def v_value(vx: float, vy: float, main: str, suffix: str,
                    accent: bool):
            canvas.set_font(style.FONT_MONO, ct.READOUT, "bold")
            m = canvas.measure_text(main)
            canvas.set_fill_color(*(t.accent if accent else t.ink))
            canvas.draw_text(main, vx, vy + m.ascent, "left")
            if suffix:
                canvas.set_font(style.FONT_MONO, ct.READOUT_SMALL,
                                "regular")
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(suffix, vx + m.width + 5, vy + m.ascent,
                                 "left")
            return vy + m.ascent + m.descent

        canvas.save()
        canvas.clip_round_rect(gx, gy, gw, gh, 8)
        # small cells: row 0 = subagents | compactions (two cols),
        # row 1 = effort spanning the full width (no empty 2x2 hole).
        small = self._cell_values()
        for c, (k, v, s, acc) in enumerate(small[:2]):
            cx = gx + 1 + c * (col_w + 1)
            cy = gy + 1
            ix, iy = cell(cx, cy, col_w, small_h)
            iy = k_label(ix, iy, k)
            v_value(ix, iy, v, s, acc)
        for k, v, s, acc in small[2:3]:
            cy = gy + 1 + (small_h + 1)
            ix, iy = cell(gx + 1, cy, gw - 2, small_h)
            iy = k_label(ix, iy, k)
            v_value(ix, iy, v, s, acc)
        # two wide rate-limit cells with mini bars
        for j, (k, pct, suffix, tname) in enumerate(self._rate_cells()):
            cy = gy + 1 + 2 * (small_h + 1) + j * (wide_h + 1)
            ix, iy = cell(gx + 1, cy, gw - 2, wide_h)
            iy = k_label(ix, iy, k)
            main = f"{pct:.0f}%" if pct is not None else "—"
            iy = v_value(ix, iy, main, suffix, False) + 4
            # bar: panel3 track + fixed-color inner (accent / ok)
            bar_w = gw - 2 - 20
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(ix, iy, bar_w, 4, 2)
            if pct:
                color = t.accent if tname == "accent" else t.ok
                canvas.set_fill_color(*color)
                canvas.fill_round_rect(
                    ix, iy, bar_w * max(0.0, min(1.0, pct / 100.0)), 4, 2)
        canvas.restore()
        canvas.restore()
