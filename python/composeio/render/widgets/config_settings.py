"""
ComposeUX ConfigSettingsWidget — the Configuration tab's SETTINGS surface
([SPEC:CXC-CONSOLE-VISUAL] Configuration tab; the previs's remaining
element map, honoured as a STYLE guide — principle II.12).

The bootstrap-manifest widget above renders "what I read to wake" (identity /
gifts / channel / the editable threshold).  THIS widget renders the remaining
Configuration sections — the ones the previs groups as global + meta settings,
built as REAL controls where the manipulation is the point (deterministic
software drives off these values, so they are steppers/indicators, not prose):

  * **Retention** (``global``) — ``cleanupPeriodDays``, a single-value
    STEPPER.  Bus-native: an edit publishes ``config_retention_ns`` (state on
    the bus, the launcher applies — the widget STOPS at the publish).
  * **Quiescence-nudge cadence** (``global``) — the adaptive three-value
    cadence (base / back-off-after / then) of composeio's OWN stall-nudge
    (``compose_projects/intelligence/launcher/monitor.py``), three STEPPERS + a conversational
    note ("or tell me 'cadence 15/90'").  Publishes ``config_heartbeat_ns``.
    HONESTLY SCOPED ([CXC-IC-HEARTBEAT-TWO-SOURCES]): this drives ONLY the
    quiescence nudge, NOT the Claude-Code session-runtime heartbeat the human
    sees in the transcript (that ~10-min cadence lives in the harness runtime,
    outside this console's reach) — the card header + note say so, because
    every prior fix that treated the two as one thing recurred.
    SET-vs-CURRENT readback ([CXC-IC-HEARTBEAT-TWO-SOURCES] readback half):
    the harness is a co-equal bus peer that PUBLISHES the cadence it is
    ACTUALLY running on ``heartbeat_current_ns``
    (``…/config/heartbeat/current``, retained).  The card shows a ✓/⚠
    delta indicator comparing SET vs CURRENT so the human can see whether a
    cadence edit actually took effect.  This readback namespace is
    READ-ONLY — the widget SUBSCRIBES it and never writes it.
  * **Notification escalation filter** ([DEC-NOTIFICATION-ESCALATION-FILTER],
    ``meta``) — the CAN-acceptance-filter / firewall ruleset that decides
    which bus messages escalate into MY context.  DEFAULT-DENY: nothing pages
    me unless a rule escalates it.  The rules are bus-native STATE on
    ``rules_ns`` (``intelligence/architect/notify/rules``); this is the
    view/tune surface — tapping a rule's ACTION pill cycles
    escalate -> glass-only -> drop and republishes the ruleset.  The
    channel-bridge ENFORCES the rules (a separate seam, not built here).
    Seeded MINIMAL (David 2026-07-22): permission requests, direct channel
    messages to me, my own lifecycle alerts.
  * **Permissions** (``locked``) — dangerous-mode (skip-permissions) is
    HARDWIRED ON (D10).  Shown as a LOCKED indicator, NOT a toggle: David
    explicitly does not want the ability to flip it, so there is no hit
    target — a reinforcement statement, not a control.

Reuse-by-reference (R3 / the narrow waist): the SAME ``console_theme`` tokens
+ chrome (``draw_card`` / ``draw_card_header``) + the ``_coerce`` reader as the
rail cards and the bootstrap manifest; ONE ``measure_text`` path for every
layout number.

Damage discipline ([CXC-R-DAMAGE]): bus updates + edits call ONLY
``self.request_redraw()`` — never ``mark_dirty`` / ``add_full`` / a rect.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce   # reuse-by-reference: the same reader

DEFAULT_RETENTION_NS = "intelligence/architect/config/retention"
DEFAULT_HEARTBEAT_NS = "intelligence/architect/config/heartbeat"
# The harness's EFFECTIVE-cadence readback (the co-equal bus peer publishes
# what it is ACTUALLY running; retained).  READ-ONLY — subscribed, never
# written ([CXC-IC-HEARTBEAT-TWO-SOURCES] readback half).  FROZEN namespace.
DEFAULT_HEARTBEAT_CURRENT_NS = DEFAULT_HEARTBEAT_NS + "/current"
DEFAULT_RULES_NS = "intelligence/architect/notify/rules"

# The three escalation actions, cycled on tap (CAN acceptance filter verbs).
ACTIONS = ("escalate", "glass-only", "drop")

# The MINIMAL default-deny seed (David 2026-07-22): only these escalate; every
# other bus message is glass-only by default.  Bus-native STATE — the launcher
# / channel-bridge consumes it; the widget only views + tunes.
SEED_RULES: List[Dict[str, Any]] = [
    {"pattern": "intelligence/architect/permission/request",
     "condition": "always", "action": "escalate", "rate_limit": ""},
    {"pattern": "intelligence/architect/in",
     "condition": "direct message to me", "action": "escalate",
     "rate_limit": ""},
    {"pattern": "intelligence/architect/lifecycle",
     "condition": "my own lifecycle alerts", "action": "escalate",
     "rate_limit": "coalesce 1/min"},
]

# Retention + heartbeat defaults (previs seed values).
RETENTION_DEFAULT = 30
HEARTBEAT_DEFAULT = {"base": 10, "backoff_after": 60, "then": 60}

# Stepper geometry.
_STEP_BTN = 26.0
_STEP_VAL = 44.0
_STEP_H = 26.0


def _base(canvas: Canvas, cy: float) -> float:
    """The single baseline path (measure-driven), shared by every text row."""
    m = canvas.measure_text("Ag")
    return cy + (m.ascent - m.descent) / 2.0


class ConfigSettingsWidget(Widget):
    """The Configuration tab's settings surface (retention · heartbeat ·
    escalation filter · permissions)."""

    def __init__(self, widget_id: str, *,
                 retention_ns: str = DEFAULT_RETENTION_NS,
                 heartbeat_ns: str = DEFAULT_HEARTBEAT_NS,
                 heartbeat_current_ns: Optional[str] = None,
                 rules_ns: str = DEFAULT_RULES_NS,
                 sections: Optional[List[str]] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "config_settings", **kwargs)
        # the cards' ambient drop shadows paint past their rects ([CXC-R-DAMAGE]).
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.retention_ns = str(retention_ns or DEFAULT_RETENTION_NS)
        self.heartbeat_ns = str(heartbeat_ns or DEFAULT_HEARTBEAT_NS)
        # the harness's effective-cadence readback ([CXC-IC-HEARTBEAT-TWO-SOURCES]
        # readback half); follows heartbeat_ns when not explicitly given so an
        # override keeps the SET/CURRENT pair coherent.  READ-ONLY.
        self.heartbeat_current_ns = str(
            heartbeat_current_ns or (self.heartbeat_ns + "/current"))
        self.rules_ns = str(rules_ns or DEFAULT_RULES_NS)
        # ADDITIVE section filter ([CXC-IC-CONFIG-MASTERDETAIL]): when set to a
        # subset of {retention, heartbeat, escalation, permissions} the widget
        # draws ONLY those cards, single-column full-width (the master/detail
        # right pane). UNSET (None) preserves the legacy 2x2 grid byte-for-byte.
        self.sections: Optional[List[str]] = (
            [str(s).strip().lower() for s in sections] if sections else None)
        self.interactive = True
        # local state (seeded so a static render shows real values; a bus
        # publish overrides — the cache-check-newer pattern, D13).
        self._retention: int = RETENTION_DEFAULT
        self._heartbeat: Dict[str, int] = dict(HEARTBEAT_DEFAULT)
        # the harness readback (READ-ONLY): None until the peer publishes it —
        # a missing/None current renders "awaiting harness readback".
        self._heartbeat_current: Optional[Dict[str, int]] = None
        self._rules: List[Dict[str, Any]] = [dict(r) for r in SEED_RULES]
        self._rules_seen: bool = False
        # hit geometry recorded by draw()
        # (x0, y0, x1, y1, kind, key, delta)
        self._steppers: List[Tuple[float, float, float, float, str, str, int]] = []
        # (x0, y0, x1, y1, rule_index) for the action pills
        self._action_zones: List[Tuple[float, float, float, float, int]] = []

    # -- bus wiring ------------------------------------------------------------

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value.strip()))
            except ValueError:
                return default
        return default

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if namespace == self.retention_ns:
            if isinstance(payload, dict):
                payload = payload.get("cleanupPeriodDays", payload.get("value"))
            self._retention = max(1, self._as_int(payload, self._retention))
            self.request_redraw()
            return
        if namespace == self.heartbeat_ns:
            if isinstance(payload, dict):
                for k in ("base", "backoff_after", "then"):
                    if k in payload:
                        self._heartbeat[k] = max(1, self._as_int(payload[k],
                                                                 self._heartbeat[k]))
                self.request_redraw()
            return
        if namespace == self.heartbeat_current_ns:
            # READ-ONLY effective-cadence readback: record what the harness
            # peer says it is ACTUALLY running.  The widget NEVER publishes
            # this namespace ([CXC-IC-HEARTBEAT-TWO-SOURCES] readback half).
            if isinstance(payload, dict):
                cur: Dict[str, int] = {}
                for k in ("base", "backoff_after", "then"):
                    if k in payload:
                        cur[k] = max(1, self._as_int(payload[k], 1))
                if cur:
                    self._heartbeat_current = cur
                    self.request_redraw()
            return
        if namespace == self.rules_ns:
            rules = payload.get("rules") if isinstance(payload, dict) else payload
            if isinstance(rules, list):
                clean = [r for r in rules if isinstance(r, dict)]
                if clean:
                    self._rules = clean
                    self._rules_seen = True
                    self.request_redraw()
            return

    # -- lifecycle / measure ---------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- draw ------------------------------------------------------------------

    # section-key -> card draw method (the additive `sections` filter).
    def _section_drawers(self):
        return {
            "retention": self._draw_retention_card,
            "heartbeat": self._draw_heartbeat_card,
            "escalation": self._draw_escalation_card,
            "permissions": self._draw_permissions_card,
        }

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._steppers = []
        self._action_zones = []
        canvas.save()
        pad = ct.PAD_CARD_BODY

        if self.sections is not None:
            # master/detail right pane: only the named section(s), single
            # column, full-width, stacked.
            drawers = self._section_drawers()
            y = rect.y
            for i, key in enumerate(self.sections):
                fn = drawers.get(key)
                if fn is None:
                    continue
                if i:
                    y += ct.GAP_RAIL
                y = fn(canvas, rect.x, y, rect.w, t, pad)
            canvas.restore()
            return

        # legacy 2x2 grid (UNSET sections — byte-for-byte the original).
        col_gap = 18.0
        col_w = round((rect.w - col_gap) / 2.0)
        left_x = rect.x
        right_x = rect.x + col_w + col_gap

        # LEFT column: escalation filter, permissions
        ly = rect.y
        ly = self._draw_escalation_card(canvas, left_x, ly, col_w, t, pad)
        ly += ct.GAP_RAIL
        ly = self._draw_permissions_card(canvas, left_x, ly, col_w, t, pad)

        # RIGHT column: retention, heartbeat
        ry = rect.y
        ry = self._draw_retention_card(canvas, right_x, ry, col_w, t, pad)
        ry += ct.GAP_RAIL
        ry = self._draw_heartbeat_card(canvas, right_x, ry, col_w, t, pad)

        canvas.restore()

    # -- a reusable stepper ([−] value [+]) ------------------------------------

    def _stepper(self, canvas: Canvas, x: float, cy: float, value: int,
                 key: str, t) -> float:
        """Draw a [−][value][+] stepper centred on *cy*.  Records two hit
        zones (key, ∓1).  Returns its total width."""
        y = cy - _STEP_H / 2.0
        total_w = _STEP_BTN * 2 + _STEP_VAL
        # frame
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(x, y, total_w, _STEP_H, 7.0)
        canvas.set_stroke_color(*t.line2)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, total_w - 1, _STEP_H - 1, 7.0)
        # inner separators
        canvas.set_stroke_color(*t.line)
        canvas.draw_line(x + _STEP_BTN, y + 4, x + _STEP_BTN, y + _STEP_H - 4)
        canvas.draw_line(x + _STEP_BTN + _STEP_VAL, y + 4,
                         x + _STEP_BTN + _STEP_VAL, y + _STEP_H - 4)
        # − / + glyphs (accent)
        canvas.set_font(ct.FONT_MONO, 14.0, "bold")
        canvas.set_fill_color(*t.accent)
        mm = canvas.measure_text("−")
        canvas.draw_text("−", x + _STEP_BTN / 2 - mm.width / 2,
                         _base(canvas, cy), "left")
        pm = canvas.measure_text("+")
        canvas.draw_text("+", x + _STEP_BTN + _STEP_VAL + _STEP_BTN / 2
                         - pm.width / 2, _base(canvas, cy), "left")
        # value (ink, mono)
        canvas.set_font(ct.FONT_MONO, 13.0, "regular")
        vs = str(value)
        vm = canvas.measure_text(vs)
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(vs, x + _STEP_BTN + _STEP_VAL / 2 - vm.width / 2,
                         _base(canvas, cy), "left")
        # hit zones
        self._steppers.append((x, y, x + _STEP_BTN, y + _STEP_H,
                               "step", key, -1))
        self._steppers.append((x + _STEP_BTN + _STEP_VAL, y,
                               x + total_w, y + _STEP_H, "step", key, +1))
        return total_w

    # -- cards -----------------------------------------------------------------

    def _draw_retention_card(self, canvas: Canvas, x: float, y: float,
                             w: float, t, pad: float) -> float:
        hh = self._header_h(canvas)
        note = ("Transcript retention.  The compact-summaries corpus + the "
                "session JSONL live here, so a short value erodes the "
                "continuity study — nudge it down with care.")
        note_lines = self._wrap(canvas, note, w - 2 * pad, 3)
        card_h = hh + 12.0 + _STEP_H + 10.0 + len(note_lines) * 15.0 + 10.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w, "Retention",
                                 ns_tag="global", t=t)
        cy = by + 12.0 + _STEP_H / 2.0
        # label
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        ct.draw_tracked(canvas, "CLEANUPPERIODDAYS", x + pad, _base(canvas, cy),
                        0.6, t.faint)
        sx = x + pad + ct.measure_tracked(canvas, "CLEANUPPERIODDAYS", 0.6) + 12.0
        sw = self._stepper(canvas, sx, cy, self._retention, "retention", t)
        canvas.set_font(ct.FONT_UI, 12.0, "regular")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text("days", sx + sw + 8.0, _base(canvas, cy), "left")
        ny = by + 12.0 + _STEP_H + 10.0
        self._draw_note(canvas, note_lines, x + pad, ny, t)
        return y + card_h

    def _draw_heartbeat_card(self, canvas: Canvas, x: float, y: float,
                             w: float, t, pad: float) -> float:
        hh = self._header_h(canvas)
        rows = [("base", "base", "min"),
                ("backoff_after", "back off after", "min idle"),
                ("then", "then", "min")]
        # HONESTLY SCOPED ([CXC-IC-HEARTBEAT-TWO-SOURCES]): this control drives
        # composeio's quiescence nudge ONLY — NOT the session-runtime heartbeat
        # the human sees in the transcript.  Every prior fix (2cc5076 -> e46927f
        # -> 832e479/a307159 -> cb9c60a/2ff5b6f) conflated the two and recurred;
        # the note DISCLAIMS the runtime stream so the knob says what it governs.
        note = ("How often composeio nudges me when I go quiet (my stall-sensor "
                "/ presence signal).  This does NOT set the session-runtime "
                "heartbeat you see in the transcript — that cadence lives in the "
                "harness runtime, outside this console.  Or tell me "
                "(\"cadence 15/90\") and I set it.")
        note_lines = self._wrap(canvas, note, w - 2 * pad, 4)
        row_h = 30.0
        ind_h = 22.0        # the SET-vs-CURRENT readback band
        card_h = hh + 10.0 + len(rows) * row_h + 8.0 + ind_h \
            + len(note_lines) * 15.0 + 10.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w, "Quiescence-nudge cadence",
                                 ns_tag="global", t=t)
        ry = by + 10.0
        label_w = 118.0
        for key, label, suffix in rows:
            cy = ry + row_h / 2.0
            canvas.set_font(ct.FONT_UI, 12.5, "regular")
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(label, x + pad, _base(canvas, cy), "left")
            sw = self._stepper(canvas, x + pad + label_w, cy,
                               self._heartbeat.get(key, 0), "hb:" + key, t)
            canvas.set_font(ct.FONT_UI, 11.5, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(suffix, x + pad + label_w + sw + 8.0,
                             _base(canvas, cy), "left")
            ry += row_h
        # SET-vs-CURRENT delta indicator ([CXC-IC-HEARTBEAT-TWO-SOURCES]
        # readback half): does the harness's effective cadence match what the
        # steppers publish?  ✓ applied / ⚠ not-yet-applied / ⚠ awaiting.
        self._draw_cadence_readback(canvas, x + pad, ry + 8.0, t)
        self._draw_note(canvas, note_lines, x + pad, ry + ind_h + 6.0, t)
        return y + card_h

    def _cadence_applied(self, cur: Dict[str, int]) -> bool:
        """True when the harness readback equals the SET triple field-for-field
        (the three frozen keys)."""
        return all(cur.get(k) == self._heartbeat.get(k)
                   for k in ("base", "backoff_after", "then"))

    def _draw_cadence_readback(self, canvas: Canvas, x: float, cy_top: float,
                               t) -> None:
        """The set-vs-current affordance: a semantic status dot (ok/warn — the
        good/caution distinction, NOT the accent) + a ✓/⚠ readout comparing the
        SET cadence against the harness's effective readback.  READ-ONLY."""
        cur = self._heartbeat_current
        if not cur:
            color, halo, text = t.warn, t.warn_dim, "⚠ awaiting harness readback"
        elif self._cadence_applied(cur):
            color, halo = t.ok, t.ok_dim
            text = "✓ applied · current %d min" % int(cur.get("base", 0))
        else:
            color, halo = t.warn, t.warn_dim
            triple = "/".join(str(int(cur.get(k, 0)))
                              for k in ("base", "backoff_after", "then"))
            text = "⚠ not yet applied · current %s" % triple
        cy = cy_top + 6.0
        ct.draw_status_dot(canvas, x + 4.0, cy, 4.0, color,
                           halo=halo, halo_w=3.0)
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        canvas.set_fill_color(*color)
        canvas.draw_text(text, x + 16.0, _base(canvas, cy), "left")

    def _draw_escalation_card(self, canvas: Canvas, x: float, y: float,
                              w: float, t, pad: float) -> float:
        hh = self._header_h(canvas)
        content_w = w - 2 * pad
        rule_h = 42.0
        intro = ("Which bus messages escalate into my context — a CAN "
                 "acceptance-filter / firewall ruleset, DEFAULT-DENY.  Tap an "
                 "action to cycle it.")
        intro_lines = self._wrap(canvas, intro, content_w, 3)
        card_h = (hh + 8.0 + len(intro_lines) * 15.0 + 8.0
                  + len(self._rules) * rule_h + 30.0 + 8.0)
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w,
                                 "Notification escalation filter",
                                 ns_tag="meta", t=t)
        iy = by + 8.0
        self._draw_note(canvas, intro_lines, x + pad, iy, t)
        ry = iy + len(intro_lines) * 15.0 + 8.0
        for i, rule in enumerate(self._rules):
            self._draw_rule_row(canvas, x + pad, ry, content_w, i, rule,
                                rule_h, t)
            ry += rule_h
        # the default row (default-deny)
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        ct.draw_tracked(canvas, "DEFAULT", x + pad, _base(canvas, ry + 12.0),
                        0.6, t.faint)
        canvas.set_font(ct.FONT_UI, 12.0, "regular")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text("everything else", x + pad + 70.0,
                         _base(canvas, ry + 12.0), "left")
        self._pill(canvas, x + w - pad - 74.0, ry + 12.0, "glass-only", t.faint)
        return y + card_h

    def _draw_rule_row(self, canvas: Canvas, x: float, y: float, w: float,
                       idx: int, rule: Dict[str, Any], h: float, t) -> None:
        pattern = str(rule.get("pattern", "—"))
        condition = str(rule.get("condition", "always"))
        rate = str(rule.get("rate_limit", "") or "")
        action = str(rule.get("action", "escalate"))
        # pattern (accent mono) on the first line
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        canvas.set_fill_color(*t.accent)
        cy1 = y + 12.0
        canvas.draw_text(ct.ellipsize(canvas, pattern, w - 96.0), x,
                         _base(canvas, cy1), "left")
        # action pill, right-aligned on the first line (the tap target)
        color = {"escalate": t.ok, "glass-only": t.faint,
                 "drop": t.crit}.get(action, t.faint)
        pill_w = self._measure_pill(canvas, action)
        px = x + w - pill_w
        self._pill(canvas, px, cy1, action, color)
        self._action_zones.append((px - 4, y, x + w, y + h, idx))
        # condition · rate-limit on the second line (faint)
        cy2 = y + 28.0
        canvas.set_font(ct.FONT_UI, 11.0, "regular")
        canvas.set_fill_color(*t.faint)
        sub = "if " + condition if condition and condition != "always" \
            else "always"
        if rate:
            sub += "  ·  " + rate
        canvas.draw_text(ct.ellipsize(canvas, sub, w - 8.0), x,
                         _base(canvas, cy2), "left")

    def _draw_permissions_card(self, canvas: Canvas, x: float, y: float,
                               w: float, t, pad: float) -> float:
        hh = self._header_h(canvas)
        note = ("Hardwired ON, not a toggle (D10) — a reinforcement statement, "
                "not a control.  There is deliberately no way to flip it here.")
        note_lines = self._wrap(canvas, note, w - 2 * pad, 3)
        card_h = hh + 12.0 + 20.0 + 10.0 + len(note_lines) * 15.0 + 10.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, x, y, w, "Permissions",
                                 ns_tag="locked", t=t)
        # the LOCKED indicator: a lock dot + "Dangerous-mode: bypassed · always on"
        cy = by + 12.0 + 10.0
        ct.draw_status_dot(canvas, x + pad + 4.0, cy, 4.0, t.ok,
                           halo=t.ok_dim, halo_w=3.0)
        canvas.set_font(ct.FONT_MONO, 11.5, "regular")
        canvas.set_fill_color(*t.ok)
        canvas.draw_text("Dangerous-mode: bypassed · always on",
                         x + pad + 16.0, _base(canvas, cy), "left")
        ny = by + 12.0 + 20.0 + 10.0
        self._draw_note(canvas, note_lines, x + pad, ny, t)
        return y + card_h

    # -- helpers ---------------------------------------------------------------

    def _header_h(self, canvas: Canvas) -> float:
        m = canvas.measure_text("Ag")
        return ct.PAD_CARD_HEADER[0] * 2 + m.ascent + m.descent

    def _draw_note(self, canvas: Canvas, lines: List[str], x: float, y: float,
                   t) -> None:
        canvas.set_font(ct.FONT_UI, 11.5, "regular")
        wy = y
        for ln in lines:
            lm = canvas.measure_text(ln)
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(ln, x, wy + lm.ascent, "left")
            wy += 15.0

    def _wrap(self, canvas: Canvas, text: str, max_w: float,
              max_lines: int) -> List[str]:
        canvas.set_font(ct.FONT_UI, 11.5, "regular")
        words = (text or "").split()
        lines: List[str] = []
        cur = ""
        for wd in words:
            trial = wd if not cur else cur + " " + wd
            if canvas.measure_text(trial).width <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = ct.ellipsize(canvas, lines[-1] + " …", max_w)
        return lines

    def _measure_pill(self, canvas: Canvas, text: str) -> float:
        canvas.set_font(ct.FONT_MONO, 8.5, "regular")
        return canvas.measure_text(str(text).upper()).width + 12.0

    def _pill(self, canvas: Canvas, x: float, cy: float, text: str,
              color) -> float:
        label = str(text).upper()
        canvas.set_font(ct.FONT_MONO, 8.5, "regular")
        tw = canvas.measure_text(label).width
        pw = tw + 12.0
        ph = 14.0
        py = cy - ph / 2.0
        canvas.set_stroke_color(color[0], color[1], color[2], 110)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, py + 0.5, pw - 1, ph - 1, 7.0)
        canvas.set_fill_color(*color)
        m = canvas.measure_text(label)
        canvas.draw_text(label, x + 6.0, cy + (m.ascent - m.descent) / 2.0,
                         "left")
        return pw

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        # a stepper button: adjust the value + publish (bus-native)
        for (x0, y0, x1, y1, kind, key, delta) in self._steppers:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._apply_step(key, delta)
                return
        # a rule action pill: cycle escalate -> glass-only -> drop + republish
        for (x0, y0, x1, y1, idx) in self._action_zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._cycle_action(idx)
                return

    def _apply_step(self, key: str, delta: int) -> None:
        if key == "retention":
            self._retention = max(1, self._retention + delta)
            self.publish(self.retention_ns,
                         {"cleanupPeriodDays": self._retention})
        elif key.startswith("hb:"):
            field = key[3:]
            self._heartbeat[field] = max(1, self._heartbeat.get(field, 0)
                                         + delta)
            self.publish(self.heartbeat_ns, dict(self._heartbeat))
        self.request_redraw()

    def _cycle_action(self, idx: int) -> None:
        if not (0 <= idx < len(self._rules)):
            return
        cur = str(self._rules[idx].get("action", "escalate"))
        nxt = ACTIONS[(ACTIONS.index(cur) + 1) % len(ACTIONS)] \
            if cur in ACTIONS else ACTIONS[0]
        self._rules[idx]["action"] = nxt
        self.publish(self.rules_ns, {"rules": self._rules})
        self.request_redraw()
