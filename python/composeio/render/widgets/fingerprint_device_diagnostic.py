"""
FingerprintDeviceDiagnosticWidget — the "R503 internals" panel: a live
SINGLE-DEVICE diagnostic view, the deeper sibling of ``fleet_status.py``'s
fleet-wide table ([CXC-FPBUS-ADDRESSING], [CXC-FPBUS-REQ-LOAD-FAILURE]).

Where ``fleet_status`` answers "which devices are unhealthy right now" across
the whole roster from a republished STATE snapshot, this widget answers "what
is THIS device doing" as a live EVENT timeline — every heartbeat transition,
every template-set load/failure, every auth attempt, most-recent-first, so a
troubleshooter can watch one board's actual bus traffic instead of a
polled summary.

Genuinely a different shape than either existing surface, not a fork:
``fleet_status`` needs a controller (``fleet_monitor.py``) because it must
enumerate devices it cannot know in advance and reduces them to ONE row each;
this widget is bound to exactly ONE known ``device_id`` up front, so it wires
its own ``bus.on_message`` directly in ``on_mount`` — the SAME idiom
``bus_monitor.py`` (widgets/bus_monitor.py) and ``auth_event_feed``'s
color-by-kind row rendering already prove, just filtered to one device's
closed FPBUS message vocabulary instead of everything or a republished list.

Event contract consumed verbatim from ``composeio.peripherals.fingerprint.
device.FingerprintDevice`` (the AUTHORITATIVE publisher — read there before
touching field names here), all addressed ``f"{device_id}/{message}"``
([CXC-FPBUS-ADDRESSING]):

  * heartbeat (device STATE, one of three, mutually exclusive):
      - ``fingerprint_ready``        data: {template_set_uuid,
                                             count_authentication_attempts}
      - ``fingerprint_push_needed``  data: {} (or None)
      - ``fingerprint_inoperable``   data: {inop_code,
                                             human_readable_inop_string}
  * ``template_set_loaded``  data: {template_set_uuid}
  * ``template_set_failed``  data: {template_set_uuid, axis, detail} and an
    OPTIONAL ``sensor_code`` — ABSENT (not null) when the axis carries none.
    ``axis`` is the six-axis HAL failure taxonomy (transport /
    sensor_refusal / capacity / protocol_desync / unsupported /
    unknown_code). This is the diagnostic payload that matters most and gets
    an ALERT row: red band, axis/detail/sensor_code all rendered inline.
  * ``auth_event``  data: {device_id, outcome, player, character, timestamp}
    — outcome in {granted, denied, unrecognized}; player/character are
    ABSENT (not null) on unrecognized ([CXC-FPID-AUTH-OUTCOME]).

Silence past this one device's own declared heartbeat cadence is NOT
computed here (that is ``fleet_status``'s job across a roster); this widget
only shows what it has actually observed on the bus for its bound device.

⛔ FUNCTIONAL PLACEHOLDER STYLING — the card chrome mirrors
``auth_event_feed``/``fleet_status``'s scaffold (dark card, titled,
monospace body); colors are a first pass David restyles to the Colonial
(BSG) visual language. Draws only against the Canvas contract, so a restyle
is a draw() change, never a rewire. Redraw discipline: ``request_redraw()``
with no args, ONLY ([CXC-R-DAMAGE]) — never ``mark_dirty``/``add_full``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TITLE_H = 22.0
_HEADER_LINE_H = 18.0
_HEADER_BLOCK_H = _HEADER_LINE_H * 2 + 6.0
_COL_HEADER_H = 16.0
_ROW_H = 20.0
_PAD = 10.0
_DOT_R = 5.0

_TITLE_COLOR = (0x8A, 0xD4, 0xFF)          # cool cyan — placeholder accent
_PANEL_BG = (0x1E, 0x20, 0x28, 235)
_PANEL_BORDER = (0x3A, 0x3E, 0x4C)
_HEADER_COLOR = style.TEXT_DIM
_DIVIDER = (0x3A, 0x3E, 0x4C)
_ALERT_ROW_BG = (0x4A, 0x18, 0x18, 210)     # loud red wash — the failure row
_UNKNOWN_COLOR = style.GRAY

# The four-valued heartbeat STATE this device reports (silence/"never seen"
# is a fifth, purely local "no evidence yet" value — never confused with
# fleet_status's roster-wide "offline" verdict, which needs a cadence this
# widget does not compute).
_STATE_COLOR = {
    "ready": style.GREEN,
    "push_needed": style.YELLOW,
    "inoperable": style.RED,
}
_STATE_LABEL = {
    "ready": "READY",
    "push_needed": "PUSH NEEDED",
    "inoperable": "INOPERABLE",
}

# The closed FPBUS message vocabulary this widget renders
# ([CXC-FPBUS-ADDRESSING] dev-address-shape) — every message this contract
# emits is ``f"{device_id}/{message}"`` (device.py), so with device_id known
# up front we match the FULL namespace, never a first/last-slash split.
_HEARTBEAT_MESSAGES = frozenset(
    {"fingerprint_ready", "fingerprint_push_needed", "fingerprint_inoperable"})
_MESSAGE_VOCAB = _HEARTBEAT_MESSAGES | {
    "template_set_loaded", "template_set_failed", "auth_event"}

_OUTCOME_COLOR = {
    "granted": style.GREEN,
    "denied": style.RED,
    "unrecognized": style.YELLOW,
}


def _short_uuid(uuid: Any) -> str:
    return f"{str(uuid)[:8]}…" if uuid else "—"


def _fmt_ts(raw: Any) -> str:
    try:
        return datetime.fromtimestamp(float(raw)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "--:--:--"


class FingerprintDeviceDiagnosticWidget(Widget):
    """Titled, per-device live FPBUS event timeline + header health strip."""

    def __init__(self, widget_id: str, *,
                 title: str = "",
                 device_id: str = "",
                 max_rows: int = 30,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "fingerprint_device_diagnostic", **kwargs)
        self.title = str(title)
        self.device_id = str(device_id)
        self.max_rows = int(max_rows)
        self.text_selectable = True

        # header health strip — the device's OWN last-observed state, never
        # inferred, never timed-out (that is fleet_status's job).
        self.heartbeat_state: Optional[str] = None       # ready/push_needed/inoperable
        self.template_set_uuid: Optional[str] = None      # the HELD uuid
        self.count_authentication_attempts: Optional[int] = None

        # scrolling timeline, most-recent-first, capped at max_rows.
        self.rows: List[Dict[str, Any]] = []

        self._bus: Any = None

    # -- bus wiring (direct — one known device, no controller needed) -------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        bus = getattr(ctx, "bus", None) if ctx else None
        if bus is not None and self.device_id and hasattr(bus, "on_message"):
            self._bus = bus
            bus.on_message(self._on_bus_message)

    def on_unmount(self) -> None:
        if self._bus is not None and hasattr(self._bus, "off_message"):
            try:
                self._bus.off_message(self._on_bus_message)
            except Exception:
                pass
        self._bus = None
        super().on_unmount()

    def _on_bus_message(self, message: Any) -> None:
        """Transport-thread callback — filter to THIS device's closed
        vocabulary, format, marshal onto the engine thread (mirrors
        bus_monitor.py's split: format here, mutate/redraw on the marshaled
        callback)."""
        event_type = getattr(message, "event_type", "") or ""
        if event_type not in _MESSAGE_VOCAB:
            return
        namespace = getattr(message, "namespace", "") or ""
        if namespace != f"{self.device_id}/{event_type}":
            return
        data = dict(getattr(message, "data", None) or {})
        ts = getattr(message, "timestamp", None)
        row = self._format_row(event_type, data, ts)

        def _apply() -> None:
            self._apply_header(event_type, data)
            self.rows.insert(0, row)
            if len(self.rows) > self.max_rows:
                del self.rows[self.max_rows:]
            self.request_redraw()

        if self._ctx is not None and hasattr(self._ctx, "schedule_update"):
            self._ctx.schedule_update(_apply)
        else:
            _apply()

    # -- pure formatting (no widget mutation) --------------------------------

    def _format_row(self, event_type: str, data: Dict[str, Any],
                     ts: Any) -> Dict[str, Any]:
        if event_type == "fingerprint_ready":
            uuid = data.get("template_set_uuid")
            count = data.get("count_authentication_attempts")
            count_s = str(count) if count is not None else "—"
            detail = f"template_set={_short_uuid(uuid)}  attempts={count_s}"
            return dict(ts=ts, label="HEARTBEAT: READY", detail=detail,
                        color=style.GREEN, alert=False)
        if event_type == "fingerprint_push_needed":
            return dict(ts=ts, label="HEARTBEAT: PUSH NEEDED",
                        detail="no template set loaded",
                        color=style.YELLOW, alert=False)
        if event_type == "fingerprint_inoperable":
            inop = data.get("inop_code") or "—"
            human = data.get("human_readable_inop_string") or "—"
            detail = f"inop_code={inop}  {human}"
            return dict(ts=ts, label="HEARTBEAT: INOPERABLE", detail=detail,
                        color=style.RED, alert=False)
        if event_type == "template_set_loaded":
            uuid = data.get("template_set_uuid")
            return dict(ts=ts, label="TEMPLATE SET LOADED",
                        detail=f"template_set={_short_uuid(uuid)}",
                        color=style.GREEN, alert=False)
        if event_type == "template_set_failed":
            uuid = data.get("template_set_uuid")
            axis = data.get("axis", "?")
            fail_detail = data.get("detail", "")
            # sensor_code is OPTIONAL — absent from the payload dict on some
            # axes (per the fourth-slug contract note) and, on device.py's
            # own TRANSPORT path, present but carrying an explicit None
            # (sensor_code MUST be None on transport — [CXC-FPHAL-FAILURE-
            # AXES]). Both read as "not applicable" here, never the literal
            # string "None".
            raw_sensor_code = data.get("sensor_code")
            sensor_code = raw_sensor_code if raw_sensor_code is not None else "—"
            detail = (f"AXIS={axis}  DETAIL={fail_detail}  "
                      f"SENSOR_CODE={sensor_code}  "
                      f"template_set={_short_uuid(uuid)}")
            return dict(ts=ts, label="TEMPLATE SET FAILED", detail=detail,
                        color=style.RED, alert=True)
        # auth_event
        outcome = str(data.get("outcome", "?")).lower()
        if "player" in data or "character" in data:
            who = f"{data.get('player', '?')} -> {data.get('character', '?')}"
        else:
            who = "unrecognized"
        color = _OUTCOME_COLOR.get(outcome, _UNKNOWN_COLOR)
        return dict(ts=ts, label=f"AUTH: {outcome.upper()}", detail=who,
                    color=color, alert=False)

    def _apply_header(self, event_type: str, data: Dict[str, Any]) -> None:
        if event_type in _HEARTBEAT_MESSAGES:
            self.heartbeat_state = {
                "fingerprint_ready": "ready",
                "fingerprint_push_needed": "push_needed",
                "fingerprint_inoperable": "inoperable",
            }[event_type]
            if event_type == "fingerprint_ready":
                uuid = data.get("template_set_uuid")
                if uuid:
                    self.template_set_uuid = uuid
                self.count_authentication_attempts = data.get(
                    "count_authentication_attempts")
        elif event_type == "template_set_loaded":
            uuid = data.get("template_set_uuid")
            if uuid:
                self.template_set_uuid = uuid
        # template_set_failed HOLDS the stamp (device.py
        # [CXC-FPBUS-REQ-LOAD-FAILURE]) — the header's held uuid must NOT
        # advance on a failure; the timeline row already shows the failed
        # uuid in its detail string.

    # -- measure / draw --------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        rows = max(1, min(self.max_rows, len(self.rows) or 1))
        h = (_TITLE_H + _HEADER_BLOCK_H + _COL_HEADER_H
             + rows * _ROW_H + 2 * _PAD)
        return Size(min(1040.0, constraints.max_width),
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

        # -- header health strip -------------------------------------------
        head_y = rect.y + _TITLE_H + _PAD
        canvas.set_font(style.FONT_MONO, 13, "bold")
        canvas.set_fill_color(*style.TEXT)
        canvas.draw_text(self.device_id or "(no device_id bound)",
                         rect.x + _PAD, head_y + 12, "left")

        state = self.heartbeat_state
        state_color = _STATE_COLOR.get(state, _UNKNOWN_COLOR)
        state_label = _STATE_LABEL.get(state, "UNKNOWN")
        state_x = rect.x + rect.w - 220
        style.draw_dot(canvas, state_x, head_y + 7, _DOT_R, state_color)
        canvas.set_fill_color(*state_color)
        canvas.set_font(style.FONT_UI, 12, "bold")
        canvas.draw_text(state_label, state_x + _DOT_R * 2 + 6,
                         head_y + 12, "left")

        head_y2 = head_y + _HEADER_LINE_H
        canvas.set_font(style.FONT_MONO, 11, "regular")
        canvas.set_fill_color(*style.TEXT_DIM)
        count = self.count_authentication_attempts
        count_s = str(count) if count is not None else "—"
        canvas.draw_text(
            f"template_set: {_short_uuid(self.template_set_uuid)}"
            f"    auth attempts: {count_s}",
            rect.x + _PAD, head_y2 + 10, "left")

        # divider
        div_y = head_y2 + _HEADER_LINE_H + 4
        canvas.set_stroke_color(*_DIVIDER)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(rect.x + _PAD, div_y, rect.x + rect.w - _PAD, div_y)

        # -- timeline (clipped to the card) ---------------------------------
        canvas.save()
        body_top = div_y + 2
        canvas.clip_rect(rect.x + 1, body_top, rect.w - 2,
                         rect.y + rect.h - body_top - 1)

        col_event_x = rect.x + _PAD + 74
        col_detail_x = col_event_x + 170

        canvas.set_font(style.FONT_UI, 10, "bold")
        canvas.set_fill_color(*_HEADER_COLOR)
        y = body_top + 4
        canvas.draw_text("TIME", rect.x + _PAD, y + 10, "left")
        canvas.draw_text("EVENT", col_event_x, y + 10, "left")
        canvas.draw_text("DETAIL", col_detail_x, y + 10, "left")
        y += _COL_HEADER_H

        canvas.set_font(style.FONT_MONO, 11, "regular")
        rows = self.rows[: self.max_rows]

        if not rows:
            canvas.set_fill_color(*style.TEXT_DIM)
            canvas.draw_text("no events observed yet", rect.x + _PAD,
                             y + 12, "left")
        else:
            for r in rows:
                if y > rect.y + rect.h:
                    break
                if r.get("alert"):
                    canvas.set_fill_color(*_ALERT_ROW_BG)
                    canvas.fill_rect(rect.x + 1, y, rect.w - 2, _ROW_H)

                color = r.get("color", _UNKNOWN_COLOR)
                ts_s = _fmt_ts(r.get("ts"))
                canvas.set_fill_color(*style.TEXT_DIM)
                canvas.draw_text(ts_s, rect.x + _PAD, y + 14, "left")

                label = r.get("label", "?")
                if r.get("alert"):
                    label = "⚠ " + label
                canvas.set_fill_color(*color)
                canvas.draw_text(label, col_event_x, y + 14, "left")

                detail = r.get("detail", "")
                canvas.set_fill_color(*(color if r.get("alert") else style.TEXT))
                canvas.draw_text(detail, col_detail_x, y + 14, "left")

                y += _ROW_H
        canvas.restore()
        canvas.restore()


# -- registration ([CXC-ICP-FOUND-B] / [CXC-PROJECT-WIDGETS]) ---------------
# A scene declares ``widget_modules: [composeio.render.widgets.
# fingerprint_device_diagnostic]`` so the parser imports THIS module
# (self-registering the type) before it resolves any ``type:`` — zero engine
# edits, same idiom as ``fleet_status.py`` / ``auth_event_feed.py``.

def _build_fingerprint_device_diagnostic(spec: dict, wid: str, comp: Any) -> Widget:
    return FingerprintDeviceDiagnosticWidget(
        wid,
        title=str(spec.get("title", spec.get("label", ""))),
        device_id=str(spec.get("device_id", "")),
        max_rows=int(spec.get("max_rows", 30)),
    )


try:  # register at import time; tolerate a partially-initialized engine
    from ..engine.widget_registry import WidgetDescriptor, register

    register(WidgetDescriptor(
        type="fingerprint_device_diagnostic",
        cls=FingerprintDeviceDiagnosticWidget,
        display_name="Fingerprint Device Diagnostic", required_namespaces=[],
        category="collaboration", categories=("collaboration",),
        aliases=("fp_device_diagnostic", "r503_internals"),
        build=_build_fingerprint_device_diagnostic, min_size=(560, 160)))
except Exception:  # pragma: no cover - registry import guard
    pass
