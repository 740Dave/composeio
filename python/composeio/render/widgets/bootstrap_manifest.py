"""
ComposeUX BootstrapManifestWidget — the bootstrap-manifest viewer
([SPEC:CXC-CONSOLE-VISUAL] Configuration tab; the meta-object made
visible).

"The meta-object un-hidden": the self-sensor un-blinds the Intelligence's
CONTEXT; the registered-checklist un-blinds ONE element of its IDENTITY;
this widget renders the WHOLE meta-object
(``compose_projects/intelligence/meta/architect.meta.json``) as the "what I read to wake"
view — the wake sequence + dusk gifts + an editable compact-threshold +
identity/channel.  It EXTENDS the ``registered_checklist`` family (the
same "meta-object made visible" idiom) rather than forking it: the same
``console_theme`` tokens/chrome, the same ``_coerce`` reader
(reuse-by-reference from ``deliverable_card``), the same ONE Open seam
(``_app/deliverable/open`` — the ``deliverable_opener`` daemon actuates
every file the console surfaces, D11), and one ``measure_text`` path for
every text layout.

Namespaces (TWO, both subscribed):

  * ``self/meta`` — the meta-object projection (``meta_publisher.py``).
    Payload is the meta-object JSON as-is:
    ``{id, role, role_descriptor{path,why}, soul, soul_note,
       principles{path,why,role,standalone_ux}, dusk_gifts[]{path,role,why},
       channel{server,mqtt,topics}, lifecycle{compact_threshold,note},
       registration{definition,required[]}}``.
  * ``intelligence/architect/config/compact_threshold`` — the BUS-NATIVE
    editable threshold (D13).  A threshold edit PUBLISHES the new value
    here (state on the bus, the launcher applies it — the widget STOPS at
    the publish, it never writes the meta-object file).  Read pattern:
    the value is cached locally, seeded from ``lifecycle.compact_threshold``,
    and a bus value on this namespace is authoritative-if-newer.

Post-previs decisions honored (the previs is a STYLE guide, not a build
contract — principle II.12):

  * The NEW ``principles`` bootstrap element is a THIRD always-loaded core
    wake step (alongside role_descriptor and dusk_gifts), with PROMINENT
    standalone treatment (accent number box + accent-tinted row + accent
    pill) — David reviews it frequently.  It is NOT in the previs; added.
  * D11 — every wake step with an artifact opens it via the ONE Open seam.
  * D12 — role/gift text is a POINTER-with-open (path + why), never the
    inline artifact body; the manifest stays scannable.
  * D13 — the threshold edit is bus-native (publish, don't write the file).

Damage discipline ([CXC-R-DAMAGE]): bus updates + edits call ONLY
``self.request_redraw()``.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce   # reuse-by-reference (R3): the same reader

DEFAULT_NAMESPACE = "self/meta"
DEFAULT_OPEN_NAMESPACE = "_app/deliverable/open"
DEFAULT_CONFIG_NAMESPACE = "intelligence/architect/config/compact_threshold"

# The one non-file wake step (mechanical): capture the summary the
# summarizer handed over.  A directory pointer, shown but not opened.
# SINGLE SOURCE OF TRUTH: composeio.bootstrap_paths (a reorg is one edit there).
from composeio.bootstrap_paths import COMPACT_SUMMARIES_REL

SUMMARY_STEP_PATH = COMPACT_SUMMARIES_REL

# Editable-threshold range (fraction of context window).  The knob maps
# linearly across this span; the previs ticks (0.65/0.80/0.90/0.95) sit
# inside it.
THR_MIN, THR_MAX = 0.60, 0.98

STEP_INDENT = 40.0        # number box (26) + 14 gap
PILL_H = 14.0


def _base(canvas: Canvas, cy: float) -> float:
    """The single baseline path (measure-driven), shared by every text row."""
    m = canvas.measure_text("Ag")
    return cy + (m.ascent - m.descent) / 2.0


def _wrap(canvas: Canvas, text: str, max_w: float, max_lines: int = 3) -> List[str]:
    """measure_text-driven word wrap (backend-identical, one measurement
    path).  Truncates to *max_lines*, ellipsizing the last on overflow."""
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


class BootstrapManifestWidget(Widget):
    """The Configuration tab's bootstrap-manifest viewer."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 config_namespace: str = DEFAULT_CONFIG_NAMESPACE,
                 meta_path: Optional[str] = None,
                 sections: Optional[List[str]] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "bootstrap_manifest", **kwargs)
        # ADDITIVE section filter ([CXC-IC-CONFIG-MASTERDETAIL], the same pattern
        # as config_settings): when set to a subset of {wake, identity, channel,
        # lifecycle, dusk_gifts} the widget draws ONLY those cards single-column
        # full-width (the master/detail right pane). UNSET (None) preserves the
        # legacy lead + two-column layout byte-for-byte. Content MOVES between
        # panes; nothing is dropped.
        self.sections: Optional[List[str]] = (
            [str(s).strip().lower() for s in sections] if sections else None)
        # [CXC-R-DAMAGE] the cards' ambient drop shadows paint past their rects.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.config_namespace = str(config_namespace
                                    or DEFAULT_CONFIG_NAMESPACE)
        # The threshold edit is a DIRECT WRITE to the meta-object file
        # (David 2026-07-22, [DEC-HANDOVER-MEASURED] — supersedes D13's
        # bus-native path): when `meta_path` is set, a slider edit
        # read-modify-writes `lifecycle.compact_threshold`, preserving every
        # other key.  With NO meta_path the widget falls back to the
        # bus-native publish (the back-compat path for a headless/test widget).
        self.meta_path = str(meta_path) if meta_path else None
        self.interactive = True
        self._meta: Dict[str, Any] = {}
        # threshold cache: seeded from meta.lifecycle, overridden by a config
        # publish (authoritative-if-newer) or a local edit.  Default 0.93 —
        # the data-collection default: "start crossing at 93% · before the 95%
        # pencils-down" ([DEC-HANDOVER-MEASURED]).
        self._threshold: float = 0.93
        self._config_seen: bool = False
        # hit geometry recorded by draw() (like deliverable_card._chip_rect):
        # openable artifact zones + the threshold track rect.
        self._open_zones: List[Tuple[float, float, float, float, str]] = []
        self._thr_track: Optional[Tuple[float, float, float, float]] = None

    # -- bus wiring ------------------------------------------------------------

    @staticmethod
    def _coerce_threshold(value: Any) -> Optional[float]:
        v = _coerce(value)
        if isinstance(v, dict):
            v = v.get("value", v.get("compact_threshold"))
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            f = float(v)
        elif isinstance(v, str):
            try:
                f = float(v.strip())
            except ValueError:
                return None
        else:
            return None
        # a 0-100 publish is normalized to a fraction (graceful)
        if f > 1.0:
            f = f / 100.0
        return max(THR_MIN, min(THR_MAX, f))

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.config_namespace:
            v = self._coerce_threshold(value)
            if v is not None:
                self._threshold = v
                self._config_seen = True   # authoritative bus value wins
                self.request_redraw()
            return
        payload = _coerce(value)
        if not isinstance(payload, dict):
            return
        self._meta = payload
        # seed the threshold from the meta default UNLESS a config value is
        # already authoritative (the config namespace is the live truth).
        if not self._config_seen:
            lc = payload.get("lifecycle")
            if isinstance(lc, dict):
                cv = lc.get("compact_threshold")
                if isinstance(cv, (int, float)) and not isinstance(cv, bool):
                    self._threshold = float(cv)
        self.request_redraw()

    # -- wake-sequence model (the heart) ---------------------------------------

    def _wake_steps(self) -> List[Dict[str, Any]]:
        """The ordered steps derived from the meta-object.  role_descriptor,
        principles, and dusk_gifts are the three always-loaded CORE elements;
        principles is prominent (David reviews it frequently)."""
        m = self._meta
        rd = m.get("role_descriptor") if isinstance(m.get("role_descriptor"),
                                                     dict) else {}
        pr = m.get("principles") if isinstance(m.get("principles"),
                                               dict) else {}
        # ADDITIVE bootstrap element (the maker pivot, [FLOW:MAKER-PROJECT]
        # [MPF-BOOTSTRAP]): a first-class `method_flow` wake step, surfaced the
        # same way `principles` is (prominent, live-linked).  Emitted ONLY when
        # the meta-object declares it, so a payload without the key renders
        # exactly as before.
        mf = m.get("method_flow") if isinstance(m.get("method_flow"),
                                                dict) else {}
        gifts = m.get("dusk_gifts") if isinstance(m.get("dusk_gifts"),
                                                  list) else []
        soul = m.get("soul")
        steps: List[Dict[str, Any]] = [
            {"num": "0", "name": "Capture the compact summary",
             "role": "mechanical", "path": SUMMARY_STEP_PATH, "open": False,
             "prominent": False,
             "why": "Before reading anything else: write out the summary the "
                    "summarizer handed me, verbatim — the one artifact of the "
                    "crossing otherwise never persisted."},
            {"num": "1", "name": "Role descriptor", "role": "identity",
             "path": rd.get("path"), "open": bool(rd.get("path")),
             "prominent": False, "why": str(rd.get("why", ""))},
            {"num": "–", "name": "Soul", "role": "optional",
             "path": soul, "open": bool(soul), "prominent": False,
             "why": str(m.get("soul_note", "")) if not soul
             else "The subjective / continuity voice, distinct from the role."},
            {"num": "2", "name": "Principles", "role": "principles",
             "path": pr.get("path"), "open": bool(pr.get("path")),
             "prominent": True, "why": str(pr.get("why", ""))},
        ]
        # method_flow slots in AFTER principles, BEFORE dusk gifts — a prominent
        # orientation step (present only when the meta-object declares it).
        if mf.get("path"):
            steps.append(
                {"num": "3", "name": "Maker-project flow", "role": "method",
                 "path": mf.get("path"), "open": bool(mf.get("path")),
                 "prominent": True, "why": str(mf.get("why", ""))})
        gifts_num = "4" if mf.get("path") else "3"
        steps += [
            {"num": gifts_num, "name": "Dusk gifts", "role": "gifts",
             "path": None, "open": False, "prominent": False,
             "count": len(gifts),
             "why": "Read each and re-register — the reasoning IS the "
                    "registration; restores the live model to ~40% vs a cold "
                    "summary's ~10%.  Detailed below."},
            {"num": "✓", "name": "Re-register, then work", "role": "terminal",
             "path": None, "open": False, "prominent": False,
             "why": "When every required artifact above is read, self/registered "
                    "flips true — and only then do I begin.  Watch "
                    "self/context_level; at my threshold, pause & reflect."},
        ]
        return steps

    @staticmethod
    def _role_color(role: str, t) -> Tuple[int, int, int, int]:
        r = str(role or "").lower()
        if r == "keystone":
            return t.warn
        if r == "relationship":
            return t.violet if hasattr(t, "violet") else (0x9A, 0x86, 0xFF, 255)
        if r in ("principles", "identity", "method"):
            return t.accent
        return t.faint      # theory / supporting / optional / mechanical

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # section-key -> card draw method (the additive `sections` filter).
    def _section_drawers(self):
        return {
            "wake": self._draw_wake_card,
            "dusk_gifts": self._draw_gifts_card,
            "gifts": self._draw_gifts_card,
            "lifecycle": self._draw_lifecycle_card,
            "identity": self._draw_identity_card,
            "channel": self._draw_channel_card,
        }

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._open_zones = []
        self._thr_track = None
        pad = ct.PAD_CARD_BODY

        if self.sections is not None:
            # master/detail right pane: only the named card(s), single column,
            # full-width, stacked.  No lead (the nav labels the section).
            canvas.save()
            drawers = self._section_drawers()
            y = rect.y + 4.0
            for i, key in enumerate(self.sections):
                fn = drawers.get(key)
                if fn is None:
                    continue
                if i:
                    y += ct.GAP_RAIL
                y = fn(canvas, rect.x, y, rect.w, t, pad)
            canvas.restore()
            return

        canvas.save()
        col_gap = 18.0
        left_w = round((rect.w - col_gap) * 0.58)
        right_w = rect.w - col_gap - left_w
        left_x = rect.x
        right_x = rect.x + left_w + col_gap

        # ── lead: title · meta-object · id · sub ──
        y = rect.y + 4.0
        canvas.set_font(ct.FONT_UI, ct.DISPLAY, "bold")
        m = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.ink)
        base = y + m.ascent
        canvas.draw_text("Bootstrap manifest", rect.x, base, "left")
        tw = canvas.measure_text("Bootstrap manifest").width
        canvas.set_font(ct.FONT_MONO, 12.0, "regular")
        mid = base
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("meta-object · ", rect.x + tw + 12, mid, "left")
        pw = canvas.measure_text("meta-object · ").width
        canvas.set_fill_color(*t.accent)
        canvas.draw_text(str(self._meta.get("id", "architect")),
                         rect.x + tw + 12 + pw, mid, "left")
        y = base + m.descent + 10.0
        sub = ("What this Intelligence reads to wake with rich context — the "
               "meta-object a cold start re-registers against.  View-first; "
               "editable only where it's safe.")
        canvas.set_font(ct.FONT_UI, 12.5, "regular")
        for ln in _wrap(canvas, sub, rect.w - 4, 2):
            lm = canvas.measure_text(ln)
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(ln, rect.x, y + lm.ascent, "left")
            y += lm.ascent + lm.descent + 4.0
        y += 12.0

        # ── two columns ──
        left_y = self._draw_wake_card(canvas, left_x, y, left_w, t, pad)
        left_y += ct.GAP_RAIL
        left_y = self._draw_gifts_card(canvas, left_x, left_y, left_w, t, pad)

        right_y = self._draw_lifecycle_card(canvas, right_x, y, right_w, t, pad)
        right_y += ct.GAP_RAIL
        right_y = self._draw_identity_card(canvas, right_x, right_y, right_w,
                                           t, pad)
        right_y += ct.GAP_RAIL
        right_y = self._draw_channel_card(canvas, right_x, right_y, right_w,
                                          t, pad)
        canvas.restore()

    # -- cards -----------------------------------------------------------------

    def _draw_wake_card(self, canvas: Canvas, x: float, y: float, w: float,
                        t, pad: float) -> float:
        steps = self._wake_steps()
        content_w = w - 2 * pad
        # prepare wrapped whys (one measurement path) to size the card
        for s in steps:
            s["_lines"] = _wrap(canvas, s["why"], content_w - STEP_INDENT, 3)
            s["_h"] = 12.0 + 18.0 + len(s["_lines"]) * 15.0
        # header height probe
        hh = ct.PAD_CARD_HEADER[0] * 2 + canvas.measure_text("Ag").ascent \
            + canvas.measure_text("Ag").descent
        n_ok = 4  # required-count hint mirrors the registration manifest
        body_h = sum(s["_h"] for s in steps)
        card_h = hh + 8.0 + body_h + 8.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, x, y, w, "Wake sequence",
                                     "read in order",
                                     count_text=f"self/registered {n_ok}/4",
                                     count_color=t.ok, t=t)
        sy = body_y + 8.0
        for s in steps:
            self._draw_step(canvas, x + pad, sy, content_w, s, t)
            sy += s["_h"]
        return y + card_h

    def _draw_step(self, canvas: Canvas, x: float, y: float, w: float,
                   s: Dict[str, Any], t) -> None:
        h = s["_h"]
        prominent = bool(s.get("prominent"))
        if prominent:
            # accent-tinted standalone treatment for the principles element
            canvas.set_fill_color(*t.accent_dim)
            canvas.fill_round_rect(x - 6, y, w + 12, h - 4, 9.0)
        # number box
        box = 26.0
        by = y + 4.0
        canvas.set_fill_color(*(t.accent_dim if not prominent else t.accent))
        canvas.fill_round_rect(x, by, box, box, 8.0)
        canvas.set_stroke_color(*(t.accent_line if prominent else t.line2))
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, by + 0.5, box - 1, box - 1, 8.0)
        canvas.set_font(ct.FONT_MONO, 12.0, "bold")
        canvas.set_fill_color(*(t.send_ink if prominent else t.accent))
        nm = canvas.measure_text(str(s["num"]))
        canvas.draw_text(str(s["num"]), x + box / 2 - nm.width / 2,
                         by + box / 2 + (nm.ascent - nm.descent) / 2, "left")
        # title row: name · pill · path
        tx = x + STEP_INDENT
        title_cy = y + 4.0 + 9.0
        canvas.set_font(ct.FONT_UI, 13.5, "bold")
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(str(s["name"]), tx, _base(canvas, title_cy), "left")
        adv = tx + canvas.measure_text(str(s["name"])).width + 9.0
        role = str(s.get("role", ""))
        if role and role not in ("terminal",):
            adv += self._pill(canvas, adv, title_cy, role,
                              self._role_color(role, t)) + 9.0
        if s.get("count") is not None:
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(f"{s['count']} · warm calibration", adv,
                             _base(canvas, title_cy), "left")
        elif s.get("path"):
            canvas.set_font(ct.FONT_MONO, 11.0, "regular")
            pth = ct.ellipsize(canvas, str(s["path"]), (x + w) - adv)
            canvas.set_fill_color(*(t.accent if s.get("open") else t.faint))
            canvas.draw_text(pth, adv, _base(canvas, title_cy), "left")
        # why lines
        wy = y + 4.0 + 22.0
        canvas.set_font(ct.FONT_UI, 12.0, "regular")
        for ln in s["_lines"]:
            lm = canvas.measure_text(ln)
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(ln, tx, wy + lm.ascent, "left")
            wy += 15.0
        # D11: an openable step registers the ONE Open seam zone
        if s.get("open") and s.get("path"):
            self._open_zones.append((x - 6, y, x + w + 6, y + h - 2,
                                     str(s["path"])))

    def _draw_gifts_card(self, canvas: Canvas, x: float, y: float, w: float,
                         t, pad: float) -> float:
        gifts = self._meta.get("dusk_gifts")
        gifts = gifts if isinstance(gifts, list) else []
        content_w = w - 2 * pad
        rows = []
        for g in gifts:
            if not isinstance(g, dict):
                continue
            lines = _wrap(canvas, str(g.get("why", "")), content_w - 92.0, 2)
            rows.append((g, lines, 12.0 + 16.0 + len(lines) * 15.0))
        hh = ct.PAD_CARD_HEADER[0] * 2 + canvas.measure_text("Ag").ascent \
            + canvas.measure_text("Ag").descent
        body_h = sum(r[2] for r in rows) or 40.0
        card_h = hh + 6.0 + body_h + 8.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, x, y, w, "Dusk gifts",
                                     "the warm-start bequest",
                                     count_text=str(len(rows)) if rows else "",
                                     count_color=t.accent, t=t)
        sy = body_y + 6.0
        for g, lines, rh in rows:
            role = str(g.get("role", ""))
            path = str(g.get("path", ""))
            # role pill (fixed left gutter) + filename + why
            self._pill(canvas, x + pad, sy + 4.0 + 8.0, role,
                       self._role_color(role, t))
            fx = x + pad + 84.0
            canvas.set_font(ct.FONT_MONO, 12.0, "medium")
            canvas.set_fill_color(*t.ink)
            name = os.path.basename(path) or path
            canvas.draw_text(ct.ellipsize(canvas, name, (x + w - pad) - fx),
                             fx, _base(canvas, sy + 4.0 + 8.0), "left")
            wy = sy + 4.0 + 20.0
            canvas.set_font(ct.FONT_UI, 11.5, "regular")
            for ln in lines:
                lm = canvas.measure_text(ln)
                canvas.set_fill_color(*t.faint)
                canvas.draw_text(ln, fx, wy + lm.ascent, "left")
                wy += 15.0
            if path:
                self._open_zones.append((x + pad, sy, x + w - pad, sy + rh,
                                         path))
            sy += rh
        if not rows:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.draw_text("awaiting self/meta…", x + w / 2,
                             body_y + 24.0, "center")
        return y + card_h

    def _draw_lifecycle_card(self, canvas: Canvas, x: float, y: float,
                             w: float, t, pad: float) -> float:
        hh = ct.PAD_CARD_HEADER[0] * 2 + canvas.measure_text("Ag").ascent \
            + canvas.measure_text("Ag").descent
        content_w = w - 2 * pad
        hint = ("Compact threshold.  I watch my own self/context_level and, "
                "at MY chosen threshold, run the /handover ritual and fire the "
                "/compact directive.  A default hint I own — the launcher "
                "never forces it; judgment overrides (wrap early when "
                "decohering).  An edit writes the meta-object directly.")
        hint_lines = _wrap(canvas, hint, content_w - 12.0, 6)
        card_h = hh + 14.0 + 34.0 + 20.0 + 8.0 + len(hint_lines) * 15.0 + 12.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, x, y, w, "Lifecycle",
                                     "editable · writes the meta-object", t=t)
        # slider track + fill + knob + value
        val = self._threshold
        frac = (val - THR_MIN) / (THR_MAX - THR_MIN)
        frac = max(0.0, min(1.0, frac))
        val_w = 62.0
        track_x = x + pad
        track_y = body_y + 20.0
        track_w = content_w - val_w - 14.0
        track_h = 6.0
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(track_x, track_y, track_w, track_h, 3.0)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(track_x + 0.5, track_y + 0.5,
                                 track_w - 1, track_h - 1, 3.0)
        canvas.set_fill_color(*t.ok)
        canvas.fill_round_rect(track_x, track_y, max(3.0, frac * track_w),
                               track_h, 3.0)
        # the 95% harness pencils-down BACKSTOP tick (warn), shown alongside
        back_frac = max(0.0, min(1.0, (0.95 - THR_MIN) / (THR_MAX - THR_MIN)))
        bx = track_x + back_frac * track_w
        canvas.set_stroke_color(*t.warn)
        canvas.set_stroke_width(2.0)
        canvas.draw_line(bx, track_y - 5.0, bx, track_y + track_h + 5.0)
        canvas.set_font(ct.FONT_MONO, 8.0, "regular")
        canvas.set_fill_color(*t.warn)
        canvas.draw_text("95% pencils-down", bx, track_y - 9.0, "center")
        kx = track_x + frac * track_w
        ky = track_y + track_h / 2.0
        canvas.set_fill_color(*t.ink)
        canvas.fill_circle(kx, ky, 7.0)
        canvas.set_stroke_color(*t.accent)
        canvas.set_stroke_width(2.0)
        canvas.stroke_circle(kx, ky, 7.0)
        # value readout: the percent + a "start crossing at X%" caption
        canvas.set_font(ct.FONT_MONO, 16.0, "bold")
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(f"{val * 100:.0f}%", x + w - pad,
                         _base(canvas, ky), "right")
        # a generous tap band around the track (finger-friendly)
        self._thr_track = (track_x, track_y - 12.0, track_w, track_h + 24.0)
        # caption under the track: start crossing at X% · before the 95% floor
        cap_y = track_y + 18.0
        canvas.set_font(ct.FONT_UI, 11.5, "medium")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(f"start crossing at {val * 100:.0f}% · before the "
                         f"95% pencils-down", track_x, _base(canvas, cap_y),
                         "left")
        # hint
        wy = track_y + 34.0
        canvas.set_font(ct.FONT_UI, 11.5, "regular")
        for ln in hint_lines:
            lm = canvas.measure_text(ln)
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(ln, track_x, wy + lm.ascent, "left")
            wy += 15.0
        return y + card_h

    def _draw_identity_card(self, canvas: Canvas, x: float, y: float,
                            w: float, t, pad: float) -> float:
        rows = [
            ("id", str(self._meta.get("id", "architect")), True),
            ("role", str(self._meta.get("role", "—")), False),
            ("soul", "none yet" if self._meta.get("soul") is None
             else str(self._meta.get("soul")), False),
        ]
        hh = ct.PAD_CARD_HEADER[0] * 2 + canvas.measure_text("Ag").ascent \
            + canvas.measure_text("Ag").descent
        row_h = 26.0
        card_h = hh + 6.0 + len(rows) * row_h + 8.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, x, y, w, "Identity", "view",
                                     t=t)
        self._kv_rows(canvas, x, body_y + 6.0, w, pad, rows, t, row_h)
        return y + card_h

    def _draw_channel_card(self, canvas: Canvas, x: float, y: float,
                           w: float, t, pad: float) -> float:
        ch = self._meta.get("channel") if isinstance(
            self._meta.get("channel"), dict) else {}
        topics = ch.get("topics") if isinstance(ch.get("topics"), dict) else {}
        rows = [
            ("server", str(ch.get("server", "—")), True),
            ("bus", str(ch.get("mqtt", "—")), True),
            ("in / out", f"{topics.get('in', '—')}  {topics.get('out', '')}",
             True),
            ("permission", "…/permission/{request,verdict}", True),
        ]
        hh = ct.PAD_CARD_HEADER[0] * 2 + canvas.measure_text("Ag").ascent \
            + canvas.measure_text("Ag").descent
        row_h = 26.0
        card_h = hh + 6.0 + len(rows) * row_h + 8.0
        ct.draw_card(canvas, x, y, w, card_h, ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, x, y, w, "Channel", "view", t=t)
        self._kv_rows(canvas, x, body_y + 6.0, w, pad, rows, t, row_h)
        return y + card_h

    def _kv_rows(self, canvas: Canvas, x: float, y: float, w: float,
                 pad: float, rows, t, row_h: float) -> None:
        k_w = 108.0
        for k, v, mono in rows:
            cy = y + row_h / 2.0
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.set_fill_color(*t.faint)
            ct.draw_tracked(canvas, k.upper(), x + pad, _base(canvas, cy),
                            0.6, t.faint)
            canvas.set_font(ct.FONT_MONO if mono else ct.FONT_UI,
                            11.5 if mono else 13.0, "regular")
            canvas.set_fill_color(*(t.dim if mono else t.ink))
            vx = x + pad + k_w
            canvas.draw_text(ct.ellipsize(canvas, v, (x + w - pad) - vx),
                             vx, _base(canvas, cy), "left")
            y += row_h

    # -- interaction -----------------------------------------------------------

    def _pill(self, canvas: Canvas, x: float, cy: float, text: str,
              color) -> float:
        """A bordered role pill (previs .role-badge).  Returns its width."""
        label = str(text).upper()
        canvas.set_font(ct.FONT_MONO, 8.5, "regular")
        tw = canvas.measure_text(label).width
        pw = tw + 12.0
        py = cy - PILL_H / 2.0
        canvas.set_stroke_color(color[0], color[1], color[2], 110)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, py + 0.5, pw - 1, PILL_H - 1, 7.0)
        canvas.set_fill_color(*color)
        m = canvas.measure_text(label)
        canvas.draw_text(label, x + 6.0,
                         cy + (m.ascent - m.descent) / 2.0, "left")
        return pw

    def _write_meta_threshold(self, value: float) -> bool:
        """DIRECT WRITE the compact-threshold into the meta-object file
        ([DEC-HANDOVER-MEASURED]; supersedes D13).  Read-modify-write:
        load the JSON, set ONLY ``lifecycle.compact_threshold``, preserve
        every other key + the key order, and write atomically (temp file +
        replace) so a crash can never leave a truncated meta-object.  Returns
        True on success; a read/parse/write failure leaves the file untouched
        and the caller falls back to the bus publish."""
        path = self.meta_path
        if not path:
            return False
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return False
            lc = data.get("lifecycle")
            if not isinstance(lc, dict):
                lc = {}
                data["lifecycle"] = lc
            lc["compact_threshold"] = round(float(value), 2)
            d = os.path.dirname(os.path.abspath(path)) or "."
            fd, tmp = tempfile.mkstemp(prefix=".meta.", suffix=".json", dir=d)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except (OSError, ValueError):
            return False

    def on_tap(self, x: float, y: float) -> None:
        # The threshold track — a tap sets the value and DIRECT-WRITES the
        # meta-object file (David 2026-07-22, [DEC-HANDOVER-MEASURED];
        # supersedes D13's bus-native path).  With no meta_path the widget
        # falls back to the bus publish (headless/test path).
        tr = self._thr_track
        if tr and tr[0] <= x <= tr[0] + tr[2] and tr[1] <= y <= tr[1] + tr[3]:
            frac = max(0.0, min(1.0, (x - tr[0]) / max(1.0, tr[2])))
            val = round(THR_MIN + frac * (THR_MAX - THR_MIN), 2)
            self._threshold = val
            self._config_seen = True          # optimistic; the projection confirms
            if not self._write_meta_threshold(val):
                self.publish(self.config_namespace, val)
            self.request_redraw()
            return
        # D11: an artifact zone tap opens via the ONE Open seam
        for (x0, y0, x1, y1, path) in self._open_zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.publish(self.open_namespace, path)
                return
