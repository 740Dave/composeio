"""
ComposeUX console launcher rail — lts-engine status + Open the Decisions viewer
([SPEC:CXC-DVL] [CXC-DVL-RAIL]).

A CONSOLE-side rail card (it does NOT embed the viewer): it subscribes
``self/lts_engine/status`` ([CXC-DVL-STATUS-SOURCE]) and renders the engine's
status, its FRESH-vs-STALE serving state, and basic metrics (corpus count,
total nodes + edges, last refresh), plus an Open button whose tap PUBLISHES the
``_app/decisions/open`` intent the in-process decisions launcher consumes.

THREE visual states, distinguishable on the glass ([CXC-DVL-RAIL]):
  UP + FRESH  — reachable, every corpus fresh (ok dot + FRESH badge)
  UP + STALE  — reachable, serving a stale index (ok dot + STALE badge)
  DOWN        — not reachable at all (crit dot, "offline", no fresh/stale)
``reachable`` gates fresh/stale — a down engine NEVER shows a false "fresh".

Before the first status arrives the card shows an explicit "awaiting lts-engine
status…" placeholder (never blank, never a fabricated up/fresh).  A bus update
calls request_redraw() only (damage stays the card's rect, [CXC-R-DAMAGE]).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct

DEFAULT_NAMESPACE = "self/lts_engine/status"
# [DD-GV-INTENT-NAMESPACE] the Open intent GENERALIZES to `_app/graph/open`
# ([SPEC:CXC-GRAPH-VIEWER] [CXC-GV-INTENTS]) — the rail Open publish and the
# launcher's INTENT_NS move together.
DEFAULT_OPEN_NAMESPACE = "_app/graph/open"

PAD = 14.0
HEAD_H = 34.0
ROW_H = 20.0
BTN_H = 30.0
PAD_BOTTOM = 14.0


class LtsEngineRailWidget(Widget):
    """The launcher rail card: lts-engine status/freshness/metrics + Open."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 label: str = "LTS Engine",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "lts_engine_rail", **kwargs)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.label = str(label or "LTS Engine")
        self.interactive = True
        self._status: Optional[Dict[str, Any]] = None
        self._open_zone: Optional[Rect] = None

    # -- bus ---------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """Latest ``self/lts_engine/status`` payload -> re-render
        ([CXC-DVL-RAIL] LIVE UPDATE)."""
        payload = value
        if isinstance(payload, str):
            s = payload.strip()
            if not s or s[0] != "{":
                return
            try:
                payload = json.loads(s)
            except (ValueError, TypeError):
                return
        if isinstance(payload, dict) and "val" in payload \
                and isinstance(payload["val"], dict):
            payload = payload["val"]
        if not isinstance(payload, dict):
            return
        self._status = payload
        self.request_redraw()

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def measure(self, constraints: Constraints) -> Size:
        need = HEAD_H + 3 * ROW_H + 10.0 + BTN_H + PAD_BOTTOM
        return Size(min(360.0, constraints.max_width),
                    min(constraints.max_height, need))

    # -- state coercion ([CXC-DVL-RAIL] three states) ----------------------

    def _visual_state(self) -> str:
        """UP_FRESH | UP_STALE | DOWN | AWAITING (before first status)."""
        st = self._status
        if not isinstance(st, dict):
            return "AWAITING"
        if not bool(st.get("reachable")):
            return "DOWN"
        return "UP_FRESH" if bool(st.get("fresh")) else "UP_STALE"

    @staticmethod
    def _fmt_refresh(value: Any) -> str:
        """A relative/absolute 'last refresh' label.  Epoch (num or numeric
        str) -> 'Nm ago'/'Ns ago'; an ISO string -> its time-of-day tail; None
        -> '—'."""
        if value is None or value == "":
            return "—"
        epoch: Optional[float] = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            epoch = float(value)
        elif isinstance(value, str):
            try:
                epoch = float(value)
            except ValueError:
                # ISO-8601: show HH:MM(:SS) if present, else the date.
                v = value.replace("T", " ")
                return v[11:19] if len(v) >= 19 else v
        if epoch is not None:
            delta = max(0.0, time.time() - epoch)
            if delta < 90:
                return f"{int(delta)}s ago"
            if delta < 5400:
                return f"{int(delta / 60)}m ago"
            if delta < 172800:
                return f"{int(delta / 3600)}h ago"
            return f"{int(delta / 86400)}d ago"
        return str(value)

    # -- draw --------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._open_zone = None
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD, t)
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD)

        state = self._visual_state()
        x = rect.x + PAD
        w = rect.w - 2 * PAD

        # header: status dot + tracked UPPERCASE title + ns tag
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        hm = canvas.measure_text("Ag")
        base = rect.y + 13.0 + hm.ascent
        dot_color, _dot_halo = self._status_dot_colors(state, t)
        ct.draw_status_dot(canvas, x + 4, base - hm.ascent / 2.0 + 1.0, 3.5,
                           dot_color, halo=_dot_halo)
        ct.draw_tracked(canvas, self.label.upper(), x + 14, base,
                        ct.TRACK_LABEL, t.ink)
        canvas.set_font(ct.FONT_MONO, ct.NS_TAG, "regular")
        canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2],
                              ct.NS_TAG_ALPHA)
        canvas.draw_text("self/lts_engine", rect.x + rect.w - PAD, base, "right")

        if state == "AWAITING":
            canvas.set_font(ct.FONT_UI, 11.5, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text("awaiting lts-engine status…", x,
                             rect.y + HEAD_H + 14.0, "left")
            self._draw_open_button(canvas, rect, t, enabled=False)
            canvas.restore()
            canvas.restore()
            return

        st = self._status or {}
        y = rect.y + HEAD_H

        # (1) engine status + (2) fresh/stale badge, on one row
        self._draw_status_row(canvas, x, y, w, state, t)
        y += ROW_H + 2.0

        # (3) metrics
        cc = int(st.get("corpus_count", 0) or 0)
        tn = int(st.get("total_nodes", 0) or 0)
        te = int(st.get("total_edges", 0) or 0)
        self._draw_metric(canvas, x, y, w, "CORPORA", str(cc), t)
        y += ROW_H
        self._draw_metric(canvas, x, y, w, "NODES · EDGES",
                          f"{tn:,} · {te:,}", t)
        y += ROW_H
        # "UPDATED" (not "LAST REFRESH") avoids reading as a stale/FRESH badge.
        self._draw_metric(canvas, x, y, w, "UPDATED",
                          self._fmt_refresh(st.get("last_refresh")), t)

        self._draw_open_button(canvas, rect, t, enabled=True)
        canvas.restore()
        canvas.restore()

    def _status_dot_colors(self, state: str, t) -> Tuple[Any, Any]:
        if state == "DOWN":
            return t.crit, t.crit_dim
        if state == "UP_STALE":
            return t.warn, t.warn_dim
        if state == "UP_FRESH":
            return t.ok, t.ok_dim
        return t.faint, None

    def _draw_status_row(self, canvas, x, y, w, state, t) -> None:
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("ENGINE", x, y + 13, "left")
        # engine up/down label
        if state == "DOWN":
            canvas.set_font(ct.FONT_MONO, 10.5, "bold")
            canvas.set_fill_color(*t.crit)
            canvas.draw_text("DOWN", x + 62, y + 13, "left")
            # a down engine shows an offline badge, NEVER fresh/stale.
            self._draw_badge(canvas, x + w, y, "OFFLINE", t.crit, t.crit_dim,
                             t.crit_line, right=True)
            return
        canvas.set_font(ct.FONT_MONO, 10.5, "bold")
        canvas.set_fill_color(*t.ok)
        canvas.draw_text("UP", x + 62, y + 13, "left")
        if state == "UP_FRESH":
            self._draw_badge(canvas, x + w, y, "FRESH", t.ok, t.ok_dim,
                             t.ok_line, right=True)
        else:
            self._draw_badge(canvas, x + w, y, "STALE", t.warn, t.warn_dim,
                             t.warn_line, right=True)

    def _draw_badge(self, canvas, x, y, text, ink, fill, line,
                    right: bool = False) -> None:
        canvas.set_font(ct.FONT_MONO, 8.5, "bold")
        tw = canvas.measure_text(text).width
        bw = tw + 16.0
        bx = x - bw if right else x
        by = y + 1.0
        canvas.set_fill_color(*fill)
        canvas.fill_round_rect(bx, by, bw, 16.0, 8.0)
        canvas.set_stroke_color(*line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(bx + 0.5, by + 0.5, bw - 1, 15.0, 8.0)
        canvas.set_fill_color(*ink)
        canvas.draw_text(text, bx + 8, by + 11, "left")

    def _draw_metric(self, canvas, x, y, w, label, value, t) -> None:
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(label, x, y + 13, "left")
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(ct.ellipsize(canvas, value, w - 96),
                         x + w, y + 13, "right")

    def _draw_open_button(self, canvas, rect, t, *, enabled: bool) -> None:
        bw = rect.w - 2 * PAD
        bx = rect.x + PAD
        by = rect.y + rect.h - PAD_BOTTOM - BTN_H
        fill = t.accent_dim if enabled else t.panel3
        line = t.accent_line if enabled else t.line
        ink = t.accent if enabled else t.faint
        canvas.set_fill_color(*fill)
        canvas.fill_round_rect(bx, by, bw, BTN_H, 7.0)
        canvas.set_stroke_color(*line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(bx + 0.5, by + 0.5, bw - 1, BTN_H - 1, 7.0)
        canvas.set_font(ct.FONT_MONO, 10.5, "bold")
        canvas.set_fill_color(*ink)
        mm = canvas.measure_text("Ag")
        canvas.draw_text("Open Decisions viewer ▸", bx + bw / 2.0,
                         by + BTN_H / 2.0 + (mm.ascent - mm.descent) / 2.0,
                         "center")
        if enabled:
            self._open_zone = Rect(bx, by, bw, BTN_H)

    # -- interaction -------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        """Open button -> publish the `_app/decisions/open` intent (the
        launcher spawns the standalone viewer, [CXC-DVL-LAUNCHER])."""
        if self._open_zone and self._in(self._open_zone, x, y):
            self.publish(self.open_namespace, "open")

    @staticmethod
    def _in(r: Rect, x: float, y: float) -> bool:
        return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h
