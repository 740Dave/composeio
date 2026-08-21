"""
ComposeUX ActivityWidget — "what's it doing now" panel ([SPEC:CXC-IC]).

A small, glanceable sub-widget of the Intelligence Console that lights up on
tool-activity semantic events.  State on the bus, behavior in the widget (the
mermaid/bus_monitor idiom): the Intelligence emits one message per tool phase
on a SINGLE namespace and every redraw is pure widget behavior — no model
round-trip.

Namespace (ONE): ``self/activity``.

Payload {tool, target, phase:"start"|"done", id, dur_s, ts}:
    phase=="start"  -> the tool is running NOW; the row draws ACTIVE (accent,
                       a "›" caret): e.g. "› Read spec 34".
    phase=="done"   -> the matching row (same ``id``) is marked COMPLETE (dim)
                       and gains its duration: e.g. "Read spec 34 · 0.3s".

(The active caret is "›" (U+203A) and the done marker "✓" (U+2713) in the
ledger — both present in the bundled IBM Plex face; the "▸"/"▶" triangles the
mock used are notdef in that font, so "›" is substituted.)

Rows are keyed by ``id`` so a start followed by its done UPDATES the same line
in place (never two lines).  A ``done`` with no prior ``start`` still creates a
completed row (graceful).  Newest activity renders at the TOP; the panel keeps
the last ``max_lines`` (default 6) and older rows clip off the bottom.

All drawing is Canvas-contract only ([CXC-R-DRAW-FONT]); ``measure_text``
drives ellipsis truncation so text is identical on every backend.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style
from . import console_theme as ct

# -- instrument palette defaults ([CXC-IC]) ----------------------------------
_ACCENT = (0x4E, 0xA8, 0xFF)    # active tool
_DIM = (0x6D, 0x7C, 0x92)       # completed / secondary
_OK = (0x2F, 0xD0, 0x7B)        # done marker
_BG = (0x0D, 0x12, 0x1B)        # panel background
_BORDER = (0x1B, 0x24, 0x32)    # panel border
_HEADER = (0x8F, 0xA0, 0xB4)

DEFAULT_NAMESPACE = "self/activity"


def format_dur(dur_s: Any) -> str:
    """Human duration: >=60s -> "4m48s" (seconds zero-padded); >=10s -> "12s";
    else one decimal "0.3s".  None/garbage -> "" (caller omits the field)."""
    try:
        s = float(dur_s)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    # values that round to a whole minute (incl. e.g. 59.7 -> 60) show as m/s
    if s >= 60 or (s >= 10 and round(s) >= 60):
        total = int(round(s))
        m = total // 60
        rem = total % 60
        return f"{m}m{rem:02d}s"
    if s >= 10:
        return f"{s:.0f}s"
    return f"{s:.1f}s"


@dataclass
class _Activity:
    """One tool-activity row, keyed by id."""
    id: str
    tool: str
    target: str
    active: bool           # True while running (phase=="start")
    dur: str               # formatted duration, set on done
    # [CXC-ICV-ACTIVITY] additive: arrival stamp so draw() derives the
    # relative-time cell ("now"/"14s"/"1m") — no new subscription.
    ts: float = field(default_factory=time.monotonic)


class ActivityWidget(Widget):
    """Glanceable current/recent tool activity ([SPEC:CXC-IC])."""

    def __init__(self, widget_id: str, *,
                 label: str = "Activity",
                 namespace: str = DEFAULT_NAMESPACE,
                 max_lines: int = 6,
                 font_size: float = 12.0,
                 accent: Tuple[int, int, int] = _ACCENT,
                 dim: Tuple[int, int, int] = _DIM,
                 ok: Tuple[int, int, int] = _OK,
                 background: Tuple[int, int, int] = _BG,
                 border: Tuple[int, int, int] = _BORDER,
                 corner_radius: float = 6.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "activity", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        self.namespace = str(namespace)
        self.max_lines = max(1, int(max_lines))
        self.font_size = float(font_size)
        self.accent = tuple(accent)
        self.dim = tuple(dim)
        self.ok = tuple(ok)
        self.background = tuple(background)
        self.border = tuple(border)
        self.corner_radius = float(corner_radius)
        # id -> _Activity, insertion order = arrival order (newest last).
        self._rows: "OrderedDict[str, _Activity]" = OrderedDict()
        self._auto = 0     # fallback id counter for payloads with no id

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce_payload(value)
        if payload is None:
            return
        tool = str(payload.get("tool") or "").strip()
        target = str(payload.get("target") or "").strip()
        phase = str(payload.get("phase") or "start").strip().lower()
        rid = payload.get("id")
        if rid is None or str(rid) == "":
            # No id: synthesize one so the row is still tracked (can't be
            # updated by a later done, but it renders).
            self._auto += 1
            rid = f"_auto{self._auto}"
        rid = str(rid)

        existing = self._rows.get(rid)
        if phase == "done":
            dur = format_dur(payload.get("dur_s"))
            if existing is not None:
                existing.active = False
                existing.dur = dur
                # keep it where it is — updated in place
                if tool:
                    existing.tool = tool
                if target:
                    existing.target = target
            else:
                # done with no prior start: create a completed row
                self._rows[rid] = _Activity(rid, tool, target, False, dur)
        else:
            # start (default): active row
            if existing is not None:
                existing.active = True
                existing.dur = ""
                if tool:
                    existing.tool = tool
                if target:
                    existing.target = target
            else:
                self._rows[rid] = _Activity(rid, tool, target, True, "")

        # cap: drop oldest (front of the OrderedDict)
        while len(self._rows) > self.max_lines:
            self._rows.popitem(last=False)
        self.request_redraw()

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(300.0, constraints.max_width),
                    min(160.0, constraints.max_height))

    def _line_text(self, row: _Activity) -> Tuple[str, tuple]:
        """(text, color) for a row.  Active: "▸ Read spec 34" accent.
        Done: "Read spec 34 · 0.3s" dim."""
        label = " ".join(p for p in (row.tool, row.target) if p) or "(activity)"
        if row.active:
            return f"› {label}", self.accent
        tail = f" · {row.dur}" if row.dur else ""
        return f"{label}{tail}", self.dim

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def _time_cell(self, row: _Activity) -> str:
        """Relative-time cell: active rows read "now"; done rows show their
        duration when known, else their age ("14s"/"1m") — derived, additive
        ([CXC-ICV-ACTIVITY])."""
        if row.active:
            return "now"
        if row.dur:
            return row.dur
        age = max(0.0, time.monotonic() - row.ts)
        if age < 60:
            return f"{age:.0f}s"
        return f"{int(age // 60)}m"

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        """[CXC-ICV-ACTIVITY] the pre-vis .act rows: card chrome + a glyph
        cell (vector dot for the NOW row — "●" is notdef in Plex), a
        tool+target middle, a right-aligned time cell, 1px separators.
        The active row's glyph + tool read ok green; the rest stays calm."""
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h,
                     ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, rect.x, rect.y, rect.w,
                                     self.label or "self / activity",
                                     self.namespace, t=t)

        pad = ct.PAD_CARD_BODY
        box_x = rect.x + pad
        content_w = max(20.0, rect.w - 2 * pad)
        glyph_w = 16.0
        row_h = self.font_size * 1.9
        fsize = max(9.0, self.font_size * 0.85)

        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h,
                               ct.RADIUS_CARD)
        rows = list(self._rows.values())
        ly = body_y + 6.0
        shown = [r for r in reversed(rows)]      # newest at TOP
        for i, row in enumerate(shown):
            if ly + row_h > rect.y + rect.h - 6:
                break
            cy = ly + row_h / 2.0
            # glyph cell: active -> ok dot; done -> dim "»"
            if row.active:
                ct.draw_status_dot(canvas, box_x + glyph_w / 2 - 2, cy,
                                   3.2, t.ok)
            else:
                canvas.set_font(style.FONT_MONO, fsize, "regular")
                canvas.set_fill_color(*t.dim)
                canvas.draw_text("»", box_x + glyph_w / 2 - 2,
                                 style.baseline_for(canvas, cy), "center")
            # right-aligned time cell
            canvas.set_font(style.FONT_MONO, fsize, "regular")
            tm = self._time_cell(row)
            tm_w = canvas.measure_text(tm).width
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(tm, box_x + content_w,
                             style.baseline_for(canvas, cy), "right")
            # middle: tool (bold; ok when NOW) + target (faint)
            mid_x = box_x + glyph_w + 4
            mid_w = content_w - glyph_w - 4 - tm_w - 8
            tool = row.tool or "(activity)"
            canvas.set_font(style.FONT_MONO, fsize, "bold")
            tool = _ellipsize(canvas, tool, mid_w)
            canvas.set_fill_color(*(t.ok if row.active else t.ink))
            base = style.baseline_for(canvas, cy)
            canvas.draw_text(tool, mid_x, base, "left")
            tw = canvas.measure_text(tool).width
            if row.target and tw + 6 < mid_w:
                canvas.set_font(style.FONT_MONO, fsize, "regular")
                tgt = _ellipsize(canvas, row.target, mid_w - tw - 6)
                canvas.set_fill_color(*t.faint)
                canvas.draw_text(tgt, mid_x + tw + 6, base, "left")
            # 1px separator under each row except the last shown
            if i < len(shown) - 1:
                canvas.set_stroke_color(*t.line)
                canvas.set_stroke_width(1.0)
                canvas.draw_line(box_x, ly + row_h,
                                 box_x + content_w, ly + row_h)
            ly += row_h
        canvas.restore()

        if not self._rows:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(style.FONT_MONO, 9.5, "regular")
            canvas.draw_text("idle", rect.x + rect.w / 2,
                             (body_y + rect.y + rect.h) / 2, "center")
        canvas.restore()


# -- shared helpers -----------------------------------------------------------

def _coerce_payload(value: Any) -> Optional[dict]:
    """dict passthrough; JSON-string parse; anything else -> None."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s or s[0] not in "{[":
            return None
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _ellipsize(canvas: Canvas, text: str, max_w: float) -> str:
    """Truncate *text* with an ellipsis so it fits *max_w* under the current
    font (measure_text-driven, backend-identical)."""
    if canvas.measure_text(text).width <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        if canvas.measure_text(cand).width <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best
