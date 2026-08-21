"""
ComposeUX DeliverableAlertWidget — the ``deliverable/new`` LIVE-ALERT
banner (previs ``docs/intelligence-console/previs/deliverable-history.html``
``.alert``), shown on the Collaboration view so David sees a fresh
document land WHILE working.

DATA-DRIVEN off the deliverable monitor's semantic alert events
(``compose_projects/intelligence/publisher/deliverable_monitor.py``): a publish on
``deliverable/new`` or ``deliverable/modified`` carries

    {"path": rel_path, "name": basename, "node_id": nid, "change": kind}

and raises the banner: green pulse dot · the event namespace in mono ok
ink · "New document created — <b>name</b> · just now" · an **Open ▸**
button (publishes ``_app/deliverable/open`` = path — the opener daemon
actuates it) · a **Dismiss** button.  The banner auto-dismisses after
``ttl_s`` (the sensible-TTL rule) or on Dismiss/Open.

LAYOUT DISCIPLINE ([DEC-BANNER-OVERLAY-NOT-SLOT]): the banner is an
OVERLAY, placed absolutely over the thread's top edge by a `pixel_stack`
in the scene — it reserves NO column space (it is visible ~25s at a
time) while keeping a REAL ``arranged_rect``, which is what
``hit_test_tree`` walks, so Open ▸/Dismiss stay clickable.  It draws
NOTHING while idle and ``hit_test`` returns False there, so taps fall
through to the widget underneath; appearing/disappearing re-flows no
sibling, so the damage stays the widget's own rect ([CXC-R-DAMAGE]).  The pulse rides the engine clock (link_health idiom):
``on_clock_tick`` animates only while an alert is live, damage scoped to
the dot's sliver via ``clock_damage_rect``; ``design.reduced_motion``
renders the rest frame.  The ▸ is a drawn path (bundled Plex has no
U+25B8 — the deliverable_card discipline).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from ..canvas import Canvas, GradientStop, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce, _open_tri

DEFAULT_NAMESPACES = ("deliverable/new", "deliverable/modified")
DEFAULT_OPEN_NAMESPACE = "_app/deliverable/open"

BANNER_H = 46.0        # the fixed slot (previs: 11px pad + text + 11px)
PAD_X = 14.0
_PULSE_PERIOD = 2.0    # previs @keyframes pulse 2s infinite
_PULSE_QUANT = 24      # halo alpha quantization -> repaint only on change


class DeliverableAlertWidget(Widget):
    """The deliverable/new live-alert banner (previs .alert)."""

    def __init__(self, widget_id: str, *,
                 namespaces=DEFAULT_NAMESPACES,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 ttl_s: float = 25.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "deliverable_alert", **kwargs)
        self.namespaces = [str(n) for n in namespaces]
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.ttl_s = float(ttl_s)
        self.interactive = True
        self._alert: Optional[Dict[str, Any]] = None
        self._alert_ns = ""
        self._shown_at = 0.0
        self._clocking = False
        self._halo_step = -1            # quantized pulse phase (repaint gate)
        self._now = time.monotonic      # test seam
        # hit rects recorded at draw ([open], [dismiss])
        self._open_rect: Optional[Tuple[float, float, float, float]] = None
        self._dismiss_rect: Optional[Tuple[float, float, float, float]] = None

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if not isinstance(payload, dict) or not payload.get("name"):
            return
        self._alert = payload
        self._alert_ns = namespace
        self._shown_at = self._now()
        self._start_clock()
        # [CXC-R-DAMAGE] scoped damage only.
        self.request_redraw()

    # -- lifecycle / clock (link_health idiom) ---------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def on_unmount(self) -> None:
        self._stop_clock()

    def _start_clock(self) -> None:
        ctx = self._ctx
        if ctx is not None and hasattr(ctx, "register_clocked") \
                and not self._clocking:
            ctx.register_clocked(self)
            self._clocking = True

    def _stop_clock(self) -> None:
        if self._clocking and self._ctx is not None \
                and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)
        self._clocking = False

    def on_clock_tick(self, now: float) -> bool:
        """TTL expiry + the previs 2s pulse.  Expiry hides the banner and
        damages the widget's own rect; the pulse repaints only the dot's
        sliver, and only on quantized phase changes — never every frame.
        Under reduced_motion the dot rests but the TTL still runs."""
        if self._alert is None:
            self._stop_clock()
            return False
        if self._now() - self._shown_at >= self.ttl_s:
            self.dismiss()
            return False
        if ct.reduced_motion(self._ctx):
            return False
        step = int((now % _PULSE_PERIOD) / _PULSE_PERIOD * _PULSE_QUANT)
        if step == self._halo_step:
            return False
        self._halo_step = step
        return True

    def clock_damage_rect(self) -> Optional[Rect]:
        """The pulse repaints only the dot's halo reach."""
        r = self.arranged_rect
        if r is None:
            return None
        return Rect(r.x + PAD_X - 8, r.y + r.h / 2 - 14, 28, 28)

    # -- actions ---------------------------------------------------------------

    def dismiss(self) -> None:
        self._alert = None
        self._alert_ns = ""
        self._open_rect = None
        self._dismiss_rect = None
        self._stop_clock()
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        if self._alert is None:
            return

        def _in(rect) -> bool:
            return rect is not None and rect[0] <= x <= rect[0] + rect[2] \
                and rect[1] <= y <= rect[1] + rect[3]

        if _in(self._open_rect):
            path = str(self._alert.get("path", ""))
            if path:
                self.publish(self.open_namespace, path)
            self.dismiss()
        elif _in(self._dismiss_rect):
            self.dismiss()

    def hit_test(self, x: float, y: float) -> bool:
        # idle slot is transparent ground — never swallow taps there
        if self._alert is None:
            return False
        return super().hit_test(x, y)

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # A full-width BANNER_H rect — but it costs no column space, because the
        # scene places it in a `pixel_stack` OVER the thread ([DEC-BANNER-OVERLAY-NOT-SLOT],
        # option (a)).  Option (b) — a zero-height measure + paint_overflow — was tried
        # and REVERTED: `draw_child` doesn't clip (so the painting works), but
        # `hit_test_tree` tests `arranged_rect` (layout.py), so a zero-height widget
        # becomes UNCLICKABLE and Open/Dismiss silently stop working.  Keeping a real
        # rect and moving it in Z is the fix that doesn't trade a cosmetic gap for a
        # functional regression.
        return Size(constraints.max_width,
                    min(BANNER_H, constraints.max_height))

    def _pulse_halo(self) -> float:
        """Halo radius fraction 0..1 for the current quantized phase."""
        if self._halo_step < 0:
            return 0.0
        frac = self._halo_step / float(_PULSE_QUANT)
        return frac if frac <= 0.7 else 0.0     # previs: 0->7px by 70%, rest

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self._alert is None:
            return                     # idle: the slot stays ground
        t = ct.CONSOLE.active
        canvas.save()

        # previs .alert chrome: ok gradient wash, ok border, 3px left bar
        canvas.set_linear_gradient(rect.x, rect.y, rect.x + rect.w, rect.y, [
            GradientStop(0.0, t.ok[0], t.ok[1], t.ok[2], 31),
            GradientStop(1.0, t.ok[0], t.ok[1], t.ok[2], 8)])
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 9.0)
        canvas.set_fill_color(0, 0, 0, 0)      # clear gradient override
        canvas.set_stroke_color(t.ok[0], t.ok[1], t.ok[2], 89)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(rect.x + 0.5, rect.y + 0.5,
                                 rect.w - 1, rect.h - 1, 9.0)
        canvas.set_fill_color(*t.ok)
        canvas.fill_round_rect(rect.x, rect.y, 3.0, rect.h, 1.5)

        cy = rect.y + rect.h / 2.0

        # pulse dot (halo expands with the clock phase; rest = no halo)
        halo = self._pulse_halo()
        if halo > 0.0 and not ct.reduced_motion(self._ctx):
            a = int(128 * (1.0 - halo / 0.7))
            canvas.set_fill_color(t.ok[0], t.ok[1], t.ok[2], max(0, a))
            canvas.fill_circle(rect.x + PAD_X + 4, cy, 4.0 + 7.0 * halo / 0.7)
        canvas.set_fill_color(*t.ok)
        canvas.fill_circle(rect.x + PAD_X + 4, cy, 4.0)

        # namespace tag in mono ok ink (previs .a-ns)
        x = rect.x + PAD_X + 16
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        ns_label = self._alert_ns or "deliverable/new"
        m = canvas.measure_text(ns_label)
        base = cy + (m.ascent - m.descent) / 2.0
        canvas.set_fill_color(*t.ok)
        canvas.draw_text(ns_label, x, base, "left")
        x += m.width + 12

        # ---- right-side buttons first (text truncates against them) ----
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        dm_label = "Dismiss"
        dm_w = canvas.measure_text(dm_label).width + 22
        dm_h = 24.0
        dm_x = rect.x + rect.w - PAD_X - dm_w
        op_label = "Open"
        tri_w, tri_gap = 4.5, 5.0
        op_w = canvas.measure_text(op_label).width + tri_gap + tri_w + 24
        op_x = dm_x - 8 - op_w

        # Open ▸ (previs .open: azure bordered chip; ▸ drawn — no glyph)
        canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 71)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(op_x, cy - dm_h / 2, op_w, dm_h, 7.0)
        canvas.set_fill_color(*t.accent)
        om = canvas.measure_text(op_label)
        canvas.draw_text(op_label, op_x + 12,
                         cy + (om.ascent - om.descent) / 2.0, "left")
        _open_tri(canvas, op_x + 12 + om.width + tri_gap, cy, tri_w, 7.0,
                  t.accent)
        self._open_rect = (op_x - 4, cy - dm_h / 2 - 4, op_w + 8, dm_h + 8)

        # Dismiss (previs .btn)
        canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 77)
        canvas.stroke_round_rect(dm_x, cy - dm_h / 2, dm_w, dm_h, 7.0)
        canvas.set_fill_color(*t.accent)
        dm = canvas.measure_text(dm_label)
        canvas.draw_text(dm_label, dm_x + 11,
                         cy + (dm.ascent - dm.descent) / 2.0, "left")
        self._dismiss_rect = (dm_x - 4, cy - dm_h / 2 - 4, dm_w + 8,
                              dm_h + 8)

        # ---- the message: "New document created — <b>name</b> · just now"
        change = str(self._alert.get("change", "new"))
        lead = "Document modified — " if change == "modified" \
            else "New document created — "
        name = str(self._alert.get("name", "?"))
        tail = " · just now"
        max_x = op_x - 12
        canvas.set_font(ct.FONT_UI, 13.0, "regular")
        lm = canvas.measure_text(lead)
        base = cy + (lm.ascent - lm.descent) / 2.0
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(lead, x, base, "left")
        x += lm.width
        canvas.set_font(ct.FONT_UI, 13.0, "bold")
        tail_w = 0.0
        canvas.set_font(ct.FONT_UI, 13.0, "regular")
        tail_w = canvas.measure_text(tail).width
        canvas.set_font(ct.FONT_UI, 13.0, "bold")
        shown = ct.ellipsize(canvas, name, max(20.0, max_x - x - tail_w))
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(shown, x, base, "left")
        x += canvas.measure_text(shown).width
        canvas.set_font(ct.FONT_UI, 13.0, "regular")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(tail, x, base, "left")

        canvas.restore()
