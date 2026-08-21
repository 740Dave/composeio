"""
EnrollCeremonyWidget — the enrollment-STATION capture-progress / feedback-
state affordance ([CXC-FPSVC-STATION-EDITOR], [CXC-FPHAL-FEEDBACK-STATES],
[CXC-FPHAL-ENROLLMENT-SEQUENCE]).

A genuine gap, not a fork: ``info_list`` (fingerprint_panel.py) and
``auth_event_feed`` (auth_event_feed.py) both render LOG-shaped state — lines
or most-recent-first rows. The enrollment ceremony is not a log: it is ONE
live, structured moment — who is enrolling, which of up to two templates,
how many of the announced ``captures_per_template`` captures have landed, and
the CURRENT feedback state out of the closed set of twelve
([CXC-FPHAL-FEEDBACK-STATES]) — a place/lift/place ceremony needs a stepper
plus a state banner to make that legible. Neither existing widget has a
segmented capture-progress stepper or a doubly-coded (color AND label)
12-state banner; this is that missing piece, and it is dumb about the domain
exactly like its siblings: a controller (``examples/fingerprint/
enroll_station.py``) drives the real HAL/session ceremony and publishes ONE
structured dict; this widget only draws it.

⛔ FUNCTIONAL PLACEHOLDER STYLING — same scaffold idiom as info_list /
auth_event_feed (dark card, titled). The Colonial (BSG) visual language is
David's to author; this widget draws only against the Canvas contract, so a
restyle is a draw() change, never a rewire. Redraw discipline:
``request_redraw()`` with no args ([CXC-R-DAMAGE]).

Bound to ONE namespace carrying a dict shaped:
    {person, display_name, template_ordinal, template_count,
     capture_done, capture_required, feedback, message, retry_count,
     templates: [{ordinal, status, retries}, ...], enrolled_count, done}
published by ``EnrollmentStation`` ([CXC-FPSVC-ENROLL-ORCHESTRATION]).
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_TITLE_H = 24.0
_PAD = 12.0
_BANNER_H = 46.0
_STEPPER_H = 34.0
_TEMPLATE_ROW_H = 40.0
_FOOTER_H = 24.0

_TITLE_COLOR = (0x8A, 0xD4, 0xFF)          # cool cyan — placeholder accent
_PANEL_BG = (0x1E, 0x20, 0x28, 235)
_PANEL_BORDER = (0x3A, 0x3E, 0x4C)
_DOT_EMPTY = (0x3A, 0x3E, 0x4C)
_DOT_DONE = style.GREEN
_DOT_CURRENT = (0x3B, 0x8E, 0xD0)
_RETRY_COLOR = (0xE6, 0x7E, 0x22)

# The closed set of TWELVE feedback states, doubly-coded (color AND label) —
# [CXC-FPHAL-FEEDBACK-STATES]. The ceremony driver only ever emits a subset
# of these (enrollment does not authenticate), but the widget renders any of
# the twelve legibly, because the state is a closed vocabulary this widget
# owns, not a private enum of the driver's.
_FEEDBACK_STYLE = {
    "idle": (style.GRAY, "IDLE"),
    "ready": (_DOT_CURRENT, "READY"),
    "awaiting_finger": (style.YELLOW, "PLACE FINGER"),
    "capture_accepted": (style.GREEN, "CAPTURE ACCEPTED"),
    "capture_retry": (_RETRY_COLOR, "LIFT & PLACE AGAIN"),
    "enroll_progress": (_DOT_CURRENT, "CAPTURING…"),
    "busy": (style.YELLOW, "BUSY"),
    "fault": (style.RED, "FAULT"),
    "accept": (style.GREEN, "ACCEPTED"),
    "reject_silent": (style.RED, "REJECTED"),
    "reject_known": (style.RED, "REJECTED (KNOWN)"),
    "retry": (_RETRY_COLOR, "RETRY"),
}
_UNKNOWN_STYLE = (style.GRAY, "?")

_TEMPLATE_STATUS_COLOR = {
    "pending": style.TEXT_DIM,
    "capturing": style.YELLOW,
    "captured": _DOT_CURRENT,
    "uploaded": style.GREEN,
}


class EnrollCeremonyWidget(Widget):
    """WHO + template-progress stepper + 12-state feedback banner + per-
    template outcome row, driven by ONE structured bus dict."""

    def __init__(self, widget_id: str, *,
                 title: str = "", namespace: str = "", **kwargs: Any) -> None:
        super().__init__(widget_id, "enroll_ceremony", **kwargs)
        self.title = str(title)
        self.bound_namespace = str(namespace)
        self.state: Dict[str, Any] = {}
        self.text_selectable = True

    # -- bus reflection ----------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        self.state = value if isinstance(value, dict) else {}
        self.request_redraw()

    # -- measure -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        h = (_TITLE_H + _BANNER_H + _STEPPER_H + _TEMPLATE_ROW_H
             + _FOOTER_H + 2 * _PAD)
        return Size(min(560.0, constraints.max_width),
                    min(h, constraints.max_height))

    # -- draw ------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        s = self.state
        canvas.save()
        canvas.set_fill_color(*_PANEL_BG)
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 8)
        canvas.set_stroke_color(*_PANEL_BORDER)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(rect.x + 0.5, rect.y + 0.5,
                                 rect.w - 1, rect.h - 1, 8)

        x = rect.x + _PAD
        w = rect.w - 2 * _PAD
        y = rect.y + _PAD

        # -- WHO header --------------------------------------------------
        person = str(s.get("person", "") or "")
        display_name = str(s.get("display_name", "") or person or "—")
        t_ord = s.get("template_ordinal")
        t_cnt = int(s.get("template_count", 0) or 0)
        who = self.title or "ENROLLMENT"
        if person:
            who = f"{who} — {display_name} ({person})"
        canvas.set_fill_color(*_TITLE_COLOR)
        canvas.set_font(style.FONT_UI, 13, "bold")
        canvas.draw_text(who, x, y + 12, "left")
        if t_ord and t_cnt:
            tmpl_txt = f"template {int(t_ord)} of {t_cnt}"
            tw = canvas.measure_text(tmpl_txt).width
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 12, "regular")
            canvas.draw_text(tmpl_txt, x + w - tw, y + 12, "left")
        y += _TITLE_H

        # -- feedback banner (the 12-state ring, made legible) -----------
        feedback = str(s.get("feedback", "idle") or "idle")
        color, label = _FEEDBACK_STYLE.get(feedback, _UNKNOWN_STYLE)
        canvas.set_fill_color(color[0], color[1], color[2], 40)
        canvas.fill_round_rect(x, y, w, _BANNER_H - 6, 6)
        canvas.set_stroke_color(color[0], color[1], color[2])
        canvas.set_stroke_width(1.5)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, w - 1, _BANNER_H - 7, 6)
        style.draw_dot(canvas, x + 16, y + (_BANNER_H - 6) / 2, 6, color)
        canvas.set_fill_color(*color)
        canvas.set_font(style.FONT_UI, 14, "bold")
        canvas.draw_text(label, x + 30, y + (_BANNER_H - 6) / 2 + 5, "left")
        message = str(s.get("message", "") or "")
        if message:
            canvas.set_fill_color(*style.TEXT_DIM)
            canvas.set_font(style.FONT_UI, 11, "regular")
            mw = canvas.measure_text(message).width
            canvas.draw_text(message, x + w - mw - 10,
                             y + (_BANNER_H - 6) / 2 + 4, "left")
        y += _BANNER_H

        # -- capture-progress stepper (place-lift-place, made visible) ---
        c_done = int(s.get("capture_done", 0) or 0)
        c_req = int(s.get("capture_required", 0) or 0)
        canvas.set_fill_color(*style.TEXT_DIM)
        canvas.set_font(style.FONT_UI, 11, "regular")
        cap_txt = (f"capture {c_done} of {c_req}" if c_req
                   else "capture — of —")
        canvas.draw_text(cap_txt, x, y + 11, "left")
        retry_n = int(s.get("retry_count", 0) or 0)
        if retry_n:
            retry_txt = (f"({retry_n} retry)" if retry_n == 1
                         else f"({retry_n} retries)")
            rw = canvas.measure_text(retry_txt).width
            canvas.set_fill_color(*_RETRY_COLOR)
            canvas.draw_text(retry_txt, x + w - rw, y + 11, "left")
        dot_y = y + 24
        n_dots = max(c_req, c_done, 1)
        dot_r = 7.0
        gap = min(28.0, (w - 2 * dot_r) / max(1, n_dots - 1)) if n_dots > 1 else 0.0
        for i in range(n_dots):
            cx = x + dot_r + i * gap
            filled = i < c_done
            is_retry_flash = (i == c_done and feedback == "capture_retry")
            if is_retry_flash:
                col = _RETRY_COLOR
            elif filled:
                col = _DOT_DONE
            elif i == c_done and feedback in ("awaiting_finger", "capture_accepted"):
                col = _DOT_CURRENT
            else:
                col = _DOT_EMPTY
            style.draw_dot(canvas, cx, dot_y, dot_r, col)
        y += _STEPPER_H

        # -- per-template outcome row (up to 2) ---------------------------
        templates: List[Dict[str, Any]] = [
            t for t in (s.get("templates") or []) if isinstance(t, dict)
        ]
        box_gap = 10.0
        box_w = (w - box_gap) / 2.0
        for i in range(2):
            bx = x + i * (box_w + box_gap)
            t = next((t for t in templates if int(t.get("ordinal", 0)) == i + 1),
                     None)
            status = str((t or {}).get("status", "pending"))
            tcolor = _TEMPLATE_STATUS_COLOR.get(status, style.TEXT_DIM)
            canvas.set_fill_color(0x16, 0x18, 0x1E, 200)
            canvas.fill_round_rect(bx, y, box_w, _TEMPLATE_ROW_H - 6, 6)
            canvas.set_stroke_color(*tcolor)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(bx + 0.5, y + 0.5, box_w - 1,
                                     _TEMPLATE_ROW_H - 7, 6)
            style.draw_dot(canvas, bx + 14, y + (_TEMPLATE_ROW_H - 6) / 2, 5,
                           tcolor)
            canvas.set_fill_color(*style.TEXT)
            canvas.set_font(style.FONT_UI, 12, "bold")
            canvas.draw_text(f"template {i + 1}", bx + 24,
                             y + (_TEMPLATE_ROW_H - 6) / 2 + 4, "left")
            canvas.set_fill_color(*tcolor)
            canvas.set_font(style.FONT_UI, 11, "regular")
            status_txt = status.upper()
            retries = int((t or {}).get("retries", 0) or 0)
            if retries:
                status_txt += f" ({retries}r)"
            stw = canvas.measure_text(status_txt).width
            canvas.draw_text(status_txt, bx + box_w - stw - 10,
                             y + (_TEMPLATE_ROW_H - 6) / 2 + 4, "left")
        y += _TEMPLATE_ROW_H

        # -- footer: enrolled count + ceremony-done -----------------------
        enrolled = int(s.get("enrolled_count", 0) or 0)
        done = bool(s.get("done", False))
        canvas.set_fill_color(*style.TEXT_DIM)
        canvas.set_font(style.FONT_UI, 11, "regular")
        foot = f"enrolled: {enrolled} template(s)"
        if done:
            foot += "  —  ceremony complete"
        canvas.draw_text(foot, x, y + 12, "left")

        canvas.restore()


# -- registration ([CXC-ICP-FOUND-B] / [CXC-PROJECT-WIDGETS]) ---------------
# A scene declares ``widget_modules: [composeio.render.widgets.
# enroll_ceremony]`` so the parser imports THIS module (self-registering the
# type) before it resolves any ``type:`` — zero engine edits, same idiom as
# ``fingerprint_panel.py``'s ``info_list`` and ``auth_event_feed.py``.

def _build_enroll_ceremony(spec: dict, wid: str, comp: Any) -> Widget:
    w = EnrollCeremonyWidget(
        wid,
        title=str(spec.get("title", spec.get("label", ""))),
        namespace=str(spec.get("namespace", "")),
    )
    if w.bound_namespace:
        comp.subscriptions.append((w.bound_namespace, w))
    return w


try:  # register at import time; tolerate a partially-initialized engine
    from ..engine.widget_registry import WidgetDescriptor, register

    register(WidgetDescriptor(
        type="enroll_ceremony", cls=EnrollCeremonyWidget,
        display_name="Enrollment Ceremony", required_namespaces=[],
        category="collaboration", categories=("collaboration",),
        aliases=("enrollment_ceremony", "capture_ceremony"),
        build=_build_enroll_ceremony, min_size=(420, 170)))
except Exception:  # pragma: no cover - registry import guard
    pass
