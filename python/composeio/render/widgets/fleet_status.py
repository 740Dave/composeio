"""
FleetStatusWidget — the FOURTH fingerprint UX surface's ONE new widget: a
per-device FLEET / DEVICE-STATUS table (troubleshooting-at-a-glance before
game night).

A genuine gap, not a fork: ``info_list`` (fingerprint_panel.py) renders
uniform-color text lines and knows nothing about a row's semantics;
``auth_event_feed`` (auth_event_feed.py) already proves the right SHAPE for a
per-row-colored log — this widget applies that SAME idiom, keyed on a
device's STATE instead of an auth outcome. The state is FOUR-valued, not
three: ready / push_needed / inoperable / offline
([CXC-FPBUS-REQ-READY-HEARTBEAT] — "silence is the fourth state and means
offline"), and offline is the most important troubleshooting signal, so it
gets its own doubly-coded (color AND label) treatment, never folded into
"inoperable" or left to look merely quiet.

Bound to a namespace carrying a JSON list of per-device row dicts,
device_id-sorted (produced by
``examples/fingerprint/fleet_monitor.py::FleetStatusMonitor`` — the
heartbeats and announce ride bus EVENTS [publish_event], not STATE, so a
controller translates them to STATE the same way ``AuthEventMonitor``
formats auth_events for ``auth_event_feed``; this widget only draws what
lands there). Row shape: ``{device_id, state, template_set_uuid,
gated_capability, location, capabilities, last_seen_age_s}``.

⛔ FUNCTIONAL PLACEHOLDER STYLING — the card chrome mirrors
``auth_event_feed``'s scaffold (dark card, titled, monospace body); the four
state colors are a first pass. David restyles to the Colonial (BSG) visual
language; this widget draws only against the Canvas contract so a restyle is
a draw() change, never a rewire. Redraw discipline: ``request_redraw()``
with no args ([CXC-R-DAMAGE]).
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TITLE_H = 22.0
_HEADER_H = 18.0
_ROW_H = 22.0
_PAD = 10.0
_DOT_R = 5.0

_TITLE_COLOR = (0x8A, 0xD4, 0xFF)          # cool cyan — placeholder accent
_PANEL_BG = (0x1E, 0x20, 0x28, 235)
_PANEL_BORDER = (0x3A, 0x3E, 0x4C)
_HEADER_COLOR = style.TEXT_DIM
_OFFLINE_ROW_BG = (0x28, 0x22, 0x22, 160)   # a faint dimmed wash on the row

# The four-valued device state, doubly-coded (color AND label) so the
# distinction is never carried by color alone — OFFLINE gets its own hue,
# never collapsed into INOPERABLE (both are "bad", but only one means "we
# don't even know").
_STATE_COLOR = {
    "ready": style.GREEN,
    "push_needed": style.YELLOW,
    "inoperable": style.RED,
    "offline": (0x6E, 0x7C, 0x93),           # cool slate — "no signal"
}
_STATE_LABEL = {
    "ready": "READY",
    "push_needed": "PUSH NEEDED",
    "inoperable": "INOPERABLE",
    "offline": "OFFLINE",
}
_UNKNOWN_COLOR = style.GRAY


def _fmt_age(age_s: Any) -> str:
    if age_s is None:
        return "never"
    try:
        a = float(age_s)
    except (TypeError, ValueError):
        return "never"
    if a < 60:
        return f"{a:.0f}s ago"
    if a < 3600:
        return f"{a / 60:.0f}m ago"
    return f"{a / 3600:.1f}h ago"


class FleetStatusWidget(Widget):
    """Titled, color-coded, device_id-sorted fleet status table."""

    def __init__(self, widget_id: str, *,
                 title: str = "",
                 namespace: str = "",
                 max_rows: int = 20,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "fleet_status", **kwargs)
        self.title = str(title)
        self.bound_namespace = str(namespace)
        self.max_rows = int(max_rows)
        self.rows: List[Dict[str, Any]] = []
        self.text_selectable = True

    # -- bus reflection ----------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if isinstance(value, list):
            self.rows = [r for r in value if isinstance(r, dict)]
        else:
            self.rows = []
        self.request_redraw()

    # -- measure / draw ------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        rows = max(1, min(self.max_rows, len(self.rows) or 1))
        h = _TITLE_H + _HEADER_H + rows * _ROW_H + 2 * _PAD
        return Size(min(920.0, constraints.max_width),
                    min(h, constraints.max_height))

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
        body_top = rect.y + _TITLE_H
        canvas.clip_rect(rect.x + 1, body_top, rect.w - 2,
                         rect.h - _TITLE_H - 1)

        col_device_w = 150.0
        col_state_x = rect.x + _PAD + col_device_w
        col_uuid_x = col_state_x + 150
        col_cap_x = col_uuid_x + 130
        col_loc_x = col_cap_x + 150
        col_age_x = col_loc_x + 140

        canvas.set_font(style.FONT_UI, 11, "bold")
        canvas.set_fill_color(*_HEADER_COLOR)
        y = body_top + _PAD
        canvas.draw_text("DEVICE", rect.x + _PAD, y + 10, "left")
        canvas.draw_text("STATE", col_state_x, y + 10, "left")
        canvas.draw_text("TEMPLATE SET", col_uuid_x, y + 10, "left")
        canvas.draw_text("CAPABILITY", col_cap_x, y + 10, "left")
        canvas.draw_text("LOCATION", col_loc_x, y + 10, "left")
        canvas.draw_text("LAST SEEN", col_age_x, y + 10, "left")
        y += _HEADER_H

        canvas.set_font(style.FONT_MONO, 12, "regular")
        rows = self.rows[: self.max_rows]

        if not rows:
            canvas.set_fill_color(*style.TEXT_DIM)
            canvas.draw_text("no devices seen yet", rect.x + _PAD, y + 12, "left")
        else:
            for r in rows:
                if y > rect.y + rect.h:
                    break
                state = str(r.get("state", "")).lower()
                color = _STATE_COLOR.get(state, _UNKNOWN_COLOR)
                label = _STATE_LABEL.get(state, state.upper() or "?")

                if state == "offline":
                    canvas.set_fill_color(*_OFFLINE_ROW_BG)
                    canvas.fill_rect(rect.x + 1, y, rect.w - 2, _ROW_H)

                device_id = str(r.get("device_id", "?"))
                text_color = style.TEXT_DIM if state == "offline" else style.TEXT
                canvas.set_fill_color(*text_color)
                canvas.draw_text(device_id, rect.x + _PAD, y + 14, "left")

                dot_cx = col_state_x + _DOT_R
                dot_cy = y + 8
                style.draw_dot(canvas, dot_cx, dot_cy, _DOT_R, color)
                canvas.set_fill_color(*color)
                canvas.draw_text(label, col_state_x + _DOT_R * 2 + 6,
                                 y + 14, "left")

                uuid = r.get("template_set_uuid")
                uuid_s = f"{str(uuid)[:8]}…" if uuid else "—"
                canvas.set_fill_color(*text_color)
                canvas.draw_text(uuid_s, col_uuid_x, y + 14, "left")

                cap = r.get("gated_capability") or "—"
                canvas.draw_text(str(cap), col_cap_x, y + 14, "left")

                loc = r.get("location") or "—"
                canvas.draw_text(str(loc), col_loc_x, y + 14, "left")

                age_s = _fmt_age(r.get("last_seen_age_s"))
                canvas.draw_text(age_s, col_age_x, y + 14, "left")

                y += _ROW_H
        canvas.restore()
        canvas.restore()


# -- registration ([CXC-ICP-FOUND-B] / [CXC-PROJECT-WIDGETS]) ---------------
# A scene declares ``widget_modules: [composeio.render.widgets.
# fleet_status]`` so the parser imports THIS module (self-registering the
# type) before it resolves any ``type:`` — zero engine edits, same idiom as
# ``auth_event_feed.py``.

def _build_fleet_status(spec: dict, wid: str, comp: Any) -> Widget:
    w = FleetStatusWidget(
        wid,
        title=str(spec.get("title", spec.get("label", ""))),
        namespace=str(spec.get("namespace", "")),
        max_rows=int(spec.get("max_rows", 20)),
    )
    if w.bound_namespace:
        comp.subscriptions.append((w.bound_namespace, w))
    return w


try:  # register at import time; tolerate a partially-initialized engine
    from ..engine.widget_registry import WidgetDescriptor, register

    register(WidgetDescriptor(
        type="fleet_status", cls=FleetStatusWidget,
        display_name="Fleet Status", required_namespaces=[],
        category="collaboration", categories=("collaboration",),
        aliases=("device_status", "fp_fleet"),
        build=_build_fleet_status, min_size=(560, 100)))
except Exception:  # pragma: no cover - registry import guard
    pass
