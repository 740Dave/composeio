"""
AuthEventFeedWidget — the runtime auth-event MONITOR ("cylon detector" / GM
view), the ONE new widget for this build.

A genuine gap, not a fork: ``info_list`` (fingerprint_panel.py) renders
uniform-color text lines and knows nothing about a row's semantics; the
whole point of this monitor is that the THREE auth-event outcomes must be
visually distinct PER ROW — granted / denied / unrecognized each get their
own color + label, doubly-coded (color AND text) so the distinction survives
even a bad monitor. That per-row structured coloring is exactly what
``info_list`` cannot do with a plain string body, and exactly what
``bus_monitor``'s color-by-message-type idiom (widgets/bus_monitor.py)
already proves is the right shape for a log widget — this widget applies
that SAME idiom, keyed on ``outcome`` instead of message type.

Why this exists at all ([CXC-FPID-RING-HIDES-DENIAL]): the physical R503's
ring shows denied and unrecognized as the SAME red — a deliberate collapse.
This is the PRIVILEGED view that un-collapses it: e.g. ``player=Dave
authenticated as character=Boomer on device=safe -> outcome=denied`` is
information the ring conceals and this feed reveals.

Bound to a namespace carrying a JSON list of auth_event dicts, most-recent-
first (produced by ``examples/fingerprint/auth_monitor.py::AuthEventMonitor``
— auth_event is a bus EVENT [publish_event], not STATE, so a controller
translates it to STATE the same way FingerprintCastingBinder formats the
superset model for ``info_list``; this widget only draws what lands there).

⛔ FUNCTIONAL PLACEHOLDER STYLING — the card chrome mirrors info_list's
scaffold (dark card, titled, monospace body); the three outcome colors are a
first pass. David restyles to the Colonial (BSG) visual language; this widget
draws only against the Canvas contract so a restyle is a draw() change, never
a rewire. Redraw discipline: ``request_redraw()`` with no args
([CXC-R-DAMAGE]).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TITLE_H = 22.0
_LINE_H = 20.0
_PAD = 10.0
_DOT_R = 5.0

_TITLE_COLOR = (0x8A, 0xD4, 0xFF)          # cool cyan — placeholder accent
_PANEL_BG = (0x1E, 0x20, 0x28, 235)
_PANEL_BORDER = (0x3A, 0x3E, 0x4C)

# The three-valued outcome, doubly-coded (color AND label) so the
# distinction is never carried by color alone ([CXC-FPID-AUTH-OUTCOME]).
_OUTCOME_COLOR = {
    "granted": style.GREEN,
    "denied": style.RED,
    "unrecognized": style.YELLOW,
}
_OUTCOME_LABEL = {
    "granted": "GRANTED",
    "denied": "DENIED",
    "unrecognized": "UNRECOGNIZED",
}
_UNKNOWN_COLOR = style.GRAY


class AuthEventFeedWidget(Widget):
    """Titled, color-coded, most-recent-first auth_event log."""

    def __init__(self, widget_id: str, *,
                 title: str = "",
                 namespace: str = "",
                 max_rows: int = 12,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "auth_event_feed", **kwargs)
        self.title = str(title)
        self.bound_namespace = str(namespace)
        self.max_rows = int(max_rows)
        self.events: List[Dict[str, Any]] = []
        self.text_selectable = True

    # -- bus reflection ----------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if isinstance(value, list):
            self.events = [e for e in value if isinstance(e, dict)]
        else:
            self.events = []
        self.request_redraw()

    # -- measure / draw ------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        rows = max(1, min(self.max_rows, len(self.events) or 1))
        h = _TITLE_H + rows * _LINE_H + 2 * _PAD
        return Size(min(760.0, constraints.max_width),
                    min(h, constraints.max_height))

    @staticmethod
    def _fmt_ts(raw: Any) -> str:
        try:
            return datetime.fromtimestamp(float(raw)).strftime("%H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "--:--:--"

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
            canvas.set_fill_color(*_TITLE_COLOR)
            canvas.set_font(style.FONT_UI, 13, "bold")
            canvas.draw_text(self.title, rect.x + _PAD,
                             rect.y + _PAD + 12, "left")

        # body (clipped to the card)
        canvas.save()
        canvas.clip_rect(rect.x + 1, rect.y + _TITLE_H, rect.w - 2,
                         rect.h - _TITLE_H - 1)
        canvas.set_font(style.FONT_MONO, 12, "regular")
        y = rect.y + _TITLE_H + _PAD
        rows = self.events[: self.max_rows]

        if not rows:
            canvas.set_fill_color(*style.TEXT_DIM)
            canvas.draw_text("no auth events yet", rect.x + _PAD, y + 12, "left")
        else:
            for ev in rows:
                if y > rect.y + rect.h:
                    break
                outcome = str(ev.get("outcome", "")).lower()
                color = _OUTCOME_COLOR.get(outcome, _UNKNOWN_COLOR)
                label = _OUTCOME_LABEL.get(outcome, outcome.upper() or "?")
                device = str(ev.get("device_id", "?"))
                ts = self._fmt_ts(ev.get("timestamp"))
                # player/character are ABSENT (not None) on unrecognized —
                # render an em-dash, never a fabricated identity.
                if "player" in ev or "character" in ev:
                    who = f"{ev.get('player', '?')} -> {ev.get('character', '?')}"
                else:
                    who = "—"

                dot_cx = rect.x + _PAD + _DOT_R
                dot_cy = y + 6
                style.draw_dot(canvas, dot_cx, dot_cy, _DOT_R, color)

                text_x = dot_cx + _DOT_R + 8
                label_padded = f"{label:<13}"
                canvas.set_fill_color(*color)
                canvas.draw_text(label_padded, text_x, y + 12, "left")
                label_w = canvas.measure_text(label_padded).width

                canvas.set_fill_color(*style.TEXT)
                rest = f"{device:<12} {who:<28} {ts}"
                canvas.draw_text(rest, text_x + label_w + 8, y + 12, "left")

                y += _LINE_H
        canvas.restore()
        canvas.restore()


# -- registration ([CXC-ICP-FOUND-B] / [CXC-PROJECT-WIDGETS]) ---------------
# A scene declares ``widget_modules: [composeio.render.widgets.
# auth_event_feed]`` so the parser imports THIS module (self-registering the
# type) before it resolves any ``type:`` — zero engine edits, same idiom as
# ``fingerprint_panel.py``'s ``info_list``.

def _build_auth_event_feed(spec: dict, wid: str, comp: Any) -> Widget:
    w = AuthEventFeedWidget(
        wid,
        title=str(spec.get("title", spec.get("label", ""))),
        namespace=str(spec.get("namespace", "")),
        max_rows=int(spec.get("max_rows", 12)),
    )
    if w.bound_namespace:
        comp.subscriptions.append((w.bound_namespace, w))
    return w


try:  # register at import time; tolerate a partially-initialized engine
    from ..engine.widget_registry import WidgetDescriptor, register

    register(WidgetDescriptor(
        type="auth_event_feed", cls=AuthEventFeedWidget,
        display_name="Auth Event Feed", required_namespaces=[],
        category="collaboration", categories=("collaboration",),
        aliases=("authfeed", "cylon_detector"),
        build=_build_auth_event_feed, min_size=(320, 100)))
except Exception:  # pragma: no cover - registry import guard
    pass
