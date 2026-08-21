"""
ComposeUX AwayWatchViewWidget — the Collaboration-tab AWAY-WATCH rail card.

David 2026-07-22 redesign: "Match the Artifacts widget's visual language ... ONE
representation, not two.  Remove the in-card detail pane.  The card is now just a
condensed roster (one compact row per watch: label + date + a short stat line).
Clicking a row opens the Night Watch window — the detail IS the window now."

So this is a SIBLING of the Artifacts rail card (``deliverable_card``): the same
``ct.draw_card`` chrome, the same tracked-UPPERCASE-mono card header, and the same
borderless-row rhythm (a leading accent dot, the name in bold ink, a trailing ▸
open affordance) — one card treatment across the rail, not a second design.

TWO bus-native interactions (the leaf owns presentation, publishes intent —
Part II §1/§2):

  * **tapping a watch row publishes ``launch_namespace``** (default
    ``_app/away_watch/open``) with ``{scene_path, session_id, label}`` — the intent
    to relaunch that watch's Night Watch window.  A launcher/supervisor consumes it
    (``compose_projects/intelligence/tools/away_watch_publisher``, which owns the runtime spawn);
    the widget NEVER spawns a process (the deliverable_card row-tap → opener split).
  * **tapping an artifact sub-row publishes ``open_namespace``** (default
    ``_app/doc/open``) = the artifact's ``path`` — restored ([DEC-DOCVIEWER-WINDOW],
    David 2026-07-22).  The redesign collapsed the card to a pure roster and moved
    artifacts "into the window", but the standalone Night Watch window renders from
    static defaults and cannot host a reactive tappable artifact list, so the
    night's artifacts (build log, DRADIS doc) became unreachable.  This restores a
    small artifact-open affordance HERE, reusing the reserved ``open_namespace``
    seam and the per-watch ``artifacts:[{title,path}]`` already in the payload; the
    NEW ``doc_viewer_launcher`` consumes ``_app/doc/open`` and opens each in its own
    read-only doc-viewer window.

DATA-DRIVEN off ONE namespace: ``self/away_watches`` — the ``away_watch_publisher``
projection of ``list_away_watches()`` ({ts, watches:[{id,label,started_ts,ended_ts,
decisions,tokens,elapsed,...}]}).  The widget renders that payload verbatim and
never re-derives it (the perceive/present split, Part II §8); it degrades
gracefully when a projection field is absent (elapsed derived from start/end).

Damage discipline ([CXC-R-DAMAGE]): every bus update and interaction calls
``self.request_redraw()`` ONLY — damage stays this widget's own rect.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce, _open_tri

DEFAULT_NAMESPACE = "self/away_watches"
DEFAULT_OPEN_NAMESPACE = "_app/doc/open"
DEFAULT_LAUNCH_NAMESPACE = "_app/away_watch/open"

# [CXC-RETRO-AWAYWATCH-OPEN-TYPED] The scene-vs-doc predicate: a Night Watch scene
# is a ``.compose.yaml`` (relaunches the window via launch_namespace); ANY other
# non-empty target is a doc (opens via open_namespace / doc_viewer_launcher). This
# SAME predicate is the launcher's ``_scene_ok`` guard (away_watch_publisher) — the
# shared invariant a doc-typed scene_path routes to the doc-open seam, never the
# Night Watch runtime (which cannot parse a .md and dies silently).
SCENE_SUFFIX = ".compose.yaml"


def _is_scene(target: Any) -> bool:
    """True iff *target* is a renderable Night Watch scene (a ``.compose.yaml``)."""
    return bool(target) and str(target).endswith(SCENE_SUFFIX)

# ── geometry (px) — the deliverable_card row rhythm (borderless on the card) ──
PAD_X = 14.0               # header/body horizontal padding (matches Artifacts)
ROW_PAD_X = 8.0           # row-container horizontal padding (matches Artifacts)
ROW_H = 44.0              # label line + compact stat line (two-line condensed row)
ART_ROW_H = 18.0          # a per-artifact open sub-row ([DEC-DOCVIEWER-WINDOW])
ART_INDENT = 24.0         # artifact sub-rows indent under their watch row
ROWS_TOP_GAP = 4.0        # gap below the header rule
_SCROLL_STEP = 40.0


def _fmt_date(ts: Any) -> str:
    """A short local date/time for a watch's start (informational; empty when
    unknown so a missing timestamp reads as blank, never a fake '1970')."""
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    return time.strftime("%b %d · %H:%M", time.localtime(t))


def _fmt_tokens(n: Any) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "0"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}k"
    return str(int(v))


def _fmt_elapsed(w: Dict[str, Any]) -> str:
    """Elapsed as ``7h 52m`` — from the projected ``elapsed`` (seconds), else
    derived from start/end.  Empty when neither is available."""
    secs = w.get("elapsed")
    if not isinstance(secs, (int, float)):
        st, en = w.get("started_ts"), w.get("ended_ts")
        if isinstance(st, (int, float)) and isinstance(en, (int, float)) and en >= st:
            secs = en - st
        else:
            return ""
    secs = int(secs)
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _stat_line(w: Dict[str, Any]) -> str:
    """The condensed stat line — Decisions · Tokens · Elapsed (David's swap)."""
    parts = [f"{int(w.get('decisions', 0) or 0)} decisions",
             f"{_fmt_tokens(w.get('tokens', 0))} tok"]
    el = _fmt_elapsed(w)
    if el:
        parts.append(el)
    return " · ".join(parts)


class AwayWatchViewWidget(Widget):
    """The Away-Watch condensed roster rail card — a sibling of Artifacts;
    tapping a row publishes the Night Watch relaunch intent."""

    def __init__(self, widget_id: str, *,
                 label: str = "Away Watch",
                 namespace: str = DEFAULT_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 launch_namespace: str = DEFAULT_LAUNCH_NAMESPACE,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "away_watch_view", **kwargs)
        self.label = str(label)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        # retained as a reserved seam (frozen scene wiring) — no longer emitted.
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.launch_namespace = str(launch_namespace or DEFAULT_LAUNCH_NAMESPACE)
        self.interactive = True
        # the card's ambient drop shadow paints past the rect ([CXC-R-DAMAGE]).
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        # payload state (the publisher contract)
        self._watches: List[Dict[str, Any]] = []
        self._ts: str = ""
        # view state
        self._scroll = 0.0
        self._max_scroll = 0.0
        # hit geometry recorded at draw
        self._row_zones: List[Tuple[Rect, Dict[str, Any]]] = []
        # artifact-open sub-rows: (rect, path) — tap publishes open_namespace
        self._art_zones: List[Tuple[Rect, str]] = []
        self._list_rect: Optional[Rect] = None

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if not isinstance(payload, dict):
            return
        rows = payload.get("watches")
        if isinstance(rows, list):
            self._watches = [r for r in rows if isinstance(r, dict)]
        self._ts = str(payload.get("ts", self._ts))
        self.request_redraw()                            # [CXC-R-DAMAGE] scoped

    # -- layout / lifecycle ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- interaction -----------------------------------------------------------

    def on_scroll(self, dx: float, dy: float) -> None:
        if self._list_rect is None or self._max_scroll <= 0:
            return
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll - dy * _SCROLL_STEP))
        if self._scroll != before:
            self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        # an artifact sub-row opens that doc in its own window (checked FIRST —
        # sub-rows sit visually inside a watch's block).
        for r, path in self._art_zones:
            if r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h:
                if path:
                    self.publish(self.open_namespace, path)
                return
        # tapping a watch row IS the open — ROUTED BY TARGET TYPE
        # ([CXC-RETRO-AWAYWATCH-OPEN-TYPED]): a `.compose.yaml` scene relaunches
        # the Night Watch window (launch_namespace); a doc target (a report `.md`,
        # e.g. the maker away-watch) opens in the doc-viewer via the SAME
        # open_namespace seam the artifact sub-rows use — never the Night Watch
        # runtime, which cannot parse a doc and dies silently. Empty target: inert.
        for r, wch in self._row_zones:
            if r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h:
                target = wch.get("scene_path")
                if _is_scene(target):
                    self.publish(self.launch_namespace,
                                 {"scene_path": str(target),
                                  "session_id": wch.get("session_id"),
                                  "label": wch.get("label")})
                elif target:
                    self.publish(self.open_namespace, str(target))
                return

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        # the SAME card chrome as the Artifacts rail card (sibling look).
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD, t)
        # the SAME header treatment: tracked UPPERCASE mono ink title + ns tag.
        body_y = ct.draw_card_header(canvas, rect.x, rect.y, rect.w,
                                     self.label, self.namespace, t=t)

        list_y = body_y + ROWS_TOP_GAP
        list_h = max(20.0, rect.y + rect.h - list_y - 6.0)
        self._list_rect = Rect(rect.x, list_y, rect.w, list_h)
        self._draw_rows(canvas, rect, list_y, list_h, t)
        canvas.restore()

    @staticmethod
    def _watch_arts(wch: Dict[str, Any]) -> List[Dict[str, str]]:
        """The watch's openable artifacts ({title, path}), filtered to entries
        that carry a path (degrades to [] when the projection omits them)."""
        out: List[Dict[str, str]] = []
        for a in wch.get("artifacts") or []:
            if isinstance(a, dict) and a.get("path"):
                out.append({"title": str(a.get("title")
                                         or a.get("path")),
                            "path": str(a["path"])})
        return out

    def _block_h(self, wch: Dict[str, Any]) -> float:
        """A watch's total block height: the condensed row + one open sub-row
        per artifact."""
        return ROW_H + len(self._watch_arts(wch)) * ART_ROW_H

    def _draw_rows(self, canvas: Canvas, rect: Rect, y: float, h: float,
                   t) -> None:
        self._row_zones = []
        self._art_zones = []
        x = rect.x + ROW_PAD_X
        w = rect.w - 2 * ROW_PAD_X

        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD)

        if not self._watches:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.draw_text("awaiting self/away_watches…",
                             rect.x + rect.w / 2, y + h / 2, "center")
            canvas.restore()
            self._max_scroll = 0.0
            return

        content_h = sum(self._block_h(wch) for wch in self._watches)
        self._max_scroll = max(0.0, content_h - h)
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))

        ry = y - self._scroll
        for wch in self._watches:
            if ry + ROW_H > y and ry < y + h:
                self._draw_row(canvas, wch, x, ry, w, t)
            self._row_zones.append((Rect(x, ry, w, ROW_H), wch))
            ry += ROW_H
            # artifact open sub-rows (indented, tappable -> open_namespace)
            for art in self._watch_arts(wch):
                if ry + ART_ROW_H > y and ry < y + h:
                    self._draw_art_row(canvas, art, x, ry, w, t)
                self._art_zones.append(
                    (Rect(x + ART_INDENT, ry, w - ART_INDENT, ART_ROW_H),
                     art["path"]))
                ry += ART_ROW_H
        canvas.restore()

        # scrollbar (the deliverable/projects idiom) only when it overflows
        if content_h > h:
            track_w = 4.0
            sx = rect.x + rect.w - 6.0
            sy = y + 4.0
            sh = h - 8.0
            canvas.set_fill_color(*t.line)
            canvas.fill_round_rect(sx, sy, track_w, sh, 2.0)
            thumb_h = max(24.0, sh * (h / content_h))
            travel = max(0.0, sh - thumb_h)
            frac = 0.0 if self._max_scroll <= 0 else self._scroll / self._max_scroll
            canvas.set_fill_color(*t.faint)
            canvas.fill_round_rect(sx, sy + travel * frac, track_w, thumb_h, 2.0)

    def _draw_art_row(self, canvas: Canvas, art: Dict[str, str], x: float,
                      y: float, w: float, t) -> None:
        """A compact tappable artifact-open sub-row: a leading ▸ + the title
        (mono dim) — tapping it opens the doc in its own window."""
        ax = x + ART_INDENT
        cy = y + ART_ROW_H / 2.0
        _open_tri(canvas, ax, cy, 5.0, 7.0, t.accent)
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        m = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.dim)
        label = ct.ellipsize(canvas, art["title"], w - ART_INDENT - 14.0)
        canvas.draw_text(label, ax + 12.0,
                         cy + (m.ascent - m.descent) / 2.0, "left")

    def _draw_row(self, canvas: Canvas, wch: Dict[str, Any], x: float, y: float,
                  w: float, t) -> None:
        cy1 = y + 15.0                         # line-1 (label) center
        # leading accent dot (the deliverable-row kind-dot analog)
        canvas.set_fill_color(*t.accent)
        canvas.fill_circle(x + ROW_PAD_X + 3, cy1, 3.0)
        # trailing ▸ open affordance (faint) — tapping the row opens the window
        op_w = 5.0
        op_x = x + w - ROW_PAD_X - op_w
        _open_tri(canvas, op_x, cy1, op_w, 8.0, t.faint)
        # line 1: label — bold ink (Artifacts row treatment)
        nm_x = x + ROW_PAD_X + 3 + 3 + 10
        canvas.set_font(ct.FONT_UI, 13.0, "bold")
        nm = canvas.measure_text("Ag")
        label = ct.ellipsize(canvas, str(wch.get("label", "?")), op_x - 6 - nm_x)
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(label, nm_x, cy1 + (nm.ascent - nm.descent) / 2.0, "left")
        # line 2: date · stat line (mono dim), condensed — date is day-only here
        # (the full "Tue, Jul 21 @ 23:50" lives in the window; the card stays tight)
        try:
            _t = float(wch.get("started_ts"))
            date = time.strftime("%b %d", time.localtime(_t)) if _t > 0 else ""
        except (TypeError, ValueError):
            date = ""
        line2 = (f"{date} · {_stat_line(wch)}") if date else _stat_line(wch)
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        sm = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(ct.ellipsize(canvas, line2, w - (nm_x - x) - 12.0),
                         nm_x, y + 30.0 + sm.ascent - 2.0, "left")
