"""
ComposeUX AvatarWidget — the self-animated presence card
([SPEC:CXC-CONSOLE-VISUAL] [CXC-ICV-AVATAR]).

The pre-vis ``.presence`` card: a radial accent-dim wash, a 132-box vector
FACE (breathing halo, panel2 disc with accent-line stroke, brow pair, a
blinking eye group, a mouth), the name, an ``expr`` line (accent,
uppercase) and a ``state`` line.

Expression states SHARE the game's ``expression`` enum ([CXC-ICD-D20],
ratification #4 — no forked vocabulary).  FOUR states carry distinct
path sets — ``grounded`` / ``focused`` / ``contemplative`` / ``spark`` —
and every other enum member renders the NEAREST drawn face; an unknown
value renders ``grounded``.  The face is NEVER blank ([CXC-ICV-ACCEPT]
ICV-C6).

``spark`` is the lit-up-delight face (raised relaxed brow, wider bright
eyes each with an accent highlight glint, a genuine smile, and a signature
4-point accent sparkle at the temple).  It is FIRST-CLASS but EXPLICIT-ONLY:
the inferred transcript publisher never emits it — only the Intelligence
deliberately publishing ``spark`` reaches this face, so no game-enum
synonym routes to it.

Binding: ONE namespace, ``self/avatar/expression``.  The two-publisher
arbitration (explicit Intelligence > inferred monitor) is BUS-side
([CXC-ICD-D20], frozen); this widget renders whatever value arrives.

Motion ([CXC-ICV-MOTION]): ``breathe`` (halo, 5.5s, scale 1->1.06 +
opacity .85->1) and ``blink`` (eyes, 6.4s, a ~60ms scaleY step-collapse)
run on ``on_clock_tick``; the ``design.reduced_motion`` gate renders the
rest frame (halo 1.0/1.0, eyes open) and requests no frames.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..canvas import (Canvas, Close, LineTo, MoveTo, PathCommand, QuadTo,
                      Size)
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style

DEFAULT_NAMESPACE = "self/avatar/expression"

# The FOUR drawn states.  Enum members from the shared game vocabulary
# map to the nearest drawn face; anything unknown -> grounded (never blank).
# ``spark`` is FIRST-CLASS but EXPLICIT-ONLY: the inferred transcript
# publisher never emits it — only the Intelligence deliberately publishing
# ``self/avatar/expression = "spark"`` reaches this face.  It therefore has
# exactly one key here (itself); no game-enum synonym routes to it.
_NEAREST: Dict[str, str] = {
    "grounded": "grounded", "rest": "grounded", "calm": "grounded",
    "content": "grounded", "happy": "grounded", "warm": "grounded",
    "focused": "focused", "intent": "focused", "determined": "focused",
    "alert": "focused", "working": "focused",
    "contemplative": "contemplative", "thinking": "contemplative",
    "reflective": "contemplative", "curious": "contemplative",
    "wistful": "contemplative",
    "spark": "spark",
}

# Marker glyphs ("◐" etc. are notdef in Plex) — a small vector marker is
# drawn beside the expr text instead; per-state fill fraction of the dot.
_EXPR_DOT_FRAC = {"grounded": 1.0, "focused": 0.5, "contemplative": 0.25,
                  "spark": 1.0}


def _sparkle(cx: float, cy: float, r_o: float, r_i: float) -> List[PathCommand]:
    """A small 4-point sparkle (concave diamond) centered at (cx, cy): outer
    points N/E/S/W at radius ``r_o``, inner waist points on the diagonals at
    ``r_i``.  Drawn filled in the accent — the ``spark`` signature mark."""
    d = r_i * 0.70710678   # inner points on the 45° diagonals
    return [
        MoveTo(cx, cy - r_o),
        LineTo(cx + d, cy - d),
        LineTo(cx + r_o, cy),
        LineTo(cx + d, cy + d),
        LineTo(cx, cy + r_o),
        LineTo(cx - d, cy + d),
        LineTo(cx - r_o, cy),
        LineTo(cx - d, cy - d),
        Close(),
    ]


def _paths(state: str) -> dict:
    """The face part paths per drawn state, in the 132-box ([CXC-ICV-AVATAR]
    geometry; 'focused' verbatim from the pre-vis SVG)."""
    if state == "focused":
        return {
            "brows": [
                [MoveTo(44, 52), LineTo(58, 56)],
                [MoveTo(88, 52), LineTo(74, 56)],
            ],
            "eyes": [
                [MoveTo(48, 64), QuadTo(54, 60, 60, 64)],
                [MoveTo(72, 64), QuadTo(78, 60, 84, 64)],
            ],
            "eye_dots": [],
            "mouth": [MoveTo(56, 84), LineTo(76, 84)],
        }
    if state == "spark":
        # the face for the moment an abstraction meets real glass and WORKS:
        # genuine, lit-up delight — brighter/warmer, never goofy.  Same
        # halo/disc/scale as the others; only brow/eyes/mouth + the signature
        # temple sparkle differ.
        return {
            # lifted, relaxed, ALERT brow — an open arch higher than
            # contemplative's (44,48->44 vs here 44,47->43); NOT furrowed.
            "brows": [
                [MoveTo(44, 47), QuadTo(51, 43, 58, 47)],
                [MoveTo(74, 47), QuadTo(81, 43, 88, 47)],
            ],
            # eyes a touch wider/rounder than grounded (r 4.0 vs 3.6) ...
            "eyes": [],
            "eye_dots": [(54, 64, 4.0), (78, 64, 4.0)],
            # ... EACH with a small accent highlight glint in the upper-inner
            # corner — the "lit up" read (drawn inside the blink group).
            "eye_glints": [(52.6, 62.4, 1.25), (76.6, 62.4, 1.25)],
            # a real, genuine smile — wider AND deeper than grounded's gentle
            # up-curve (52->80 span, control y 92 vs grounded's 78->89).
            "mouth": [MoveTo(52, 82), QuadTo(66, 92, 80, 82)],
            # SIGNATURE: a small 4-point sparkle at the upper-right halo edge
            # (temple), in accent, with two tiny radiating micro-glints.
            "spark": _sparkle(104.0, 30.0, 7.0, 2.2),
            "spark_glints": [(113.0, 21.5, 1.15), (97.5, 20.0, 0.9)],
        }
    if state == "contemplative":
        return {
            "brows": [
                [MoveTo(44, 48), QuadTo(51, 44, 58, 48)],
                [MoveTo(74, 48), QuadTo(81, 44, 88, 48)],
            ],
            "eyes": [],
            "eye_dots": [(54, 62, 3.0), (78, 62, 3.0)],
            "mouth": [MoveTo(60, 84), LineTo(72, 84)],
        }
    # grounded (the rest face)
    return {
        "brows": [
            [MoveTo(44, 50), LineTo(58, 50)],
            [MoveTo(74, 50), LineTo(88, 50)],
        ],
        "eyes": [],
        "eye_dots": [(54, 64, 3.6), (78, 64, 3.6)],
        "mouth": [MoveTo(54, 82), QuadTo(66, 89, 78, 82)],
    }


class AvatarWidget(Widget):
    """The presence card + self-animated face ([CXC-ICV-AVATAR])."""

    def __init__(self, widget_id: str, *,
                 name: str = "architect",
                 namespace: str = DEFAULT_NAMESPACE,
                 expression: str = "grounded",
                 compact: bool = False,
                 state_prefix: str = "self/registered",
                 state_ok: str = "✓ grounded",
                 state_suffix: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "avatar", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.compact = bool(compact)
        self.name = str(name)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.expression = str(expression or "grounded")
        self.state_prefix = str(state_prefix)
        self.state_ok = str(state_ok)
        self.state_suffix = str(state_suffix)
        # motion state (rest frame defaults — correct with no clock at all,
        # e.g. the single-frame headless golden)
        self._halo_scale: float = 1.0
        self._halo_op: float = 1.0
        self._blink: float = 1.0

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if isinstance(value, dict):
            value = value.get("expression", value.get("expr",
                                                      value.get("value")))
        if value is None:
            return
        self.expression = str(value).strip() or "grounded"
        self.request_redraw()

    def drawn_state(self) -> str:
        """expression enum -> the nearest DRAWN face (never blank)."""
        return _NEAREST.get(self.expression.lower(), "grounded")

    # -- motion ------------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        if ctx is not None and hasattr(ctx, "register_clocked"):
            ctx.register_clocked(self)

    def on_unmount(self) -> None:
        if self._ctx is not None and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)
        super().on_unmount()

    def on_clock_tick(self, now: float) -> bool:
        if ct.reduced_motion(self._ctx):
            changed = (self._halo_scale, self._halo_op, self._blink) \
                != (1.0, 1.0, 1.0)
            self._halo_scale = self._halo_op = self._blink = 1.0
            if changed:
                self.request_redraw()
            return False
        self._halo_scale, self._halo_op = ct.breathe(now)
        self._blink = ct.blink_scale(now)
        return True

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(320.0, constraints.max_width),
                    min(constraints.max_height, 232.0))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        if self.compact:
            self._draw_compact(canvas, rect, t)
            return
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h,
                     ct.RADIUS_CARD, t)
        # the radial accent-dim wash, top-center (presence, not alarm)
        from ..canvas import GradientStop
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h,
                               ct.RADIUS_CARD)
        canvas.set_radial_gradient(rect.x + rect.w / 2, rect.y,
                                   rect.w * 0.75, [
                                       GradientStop(0.0, *t.accent_dim),
                                       GradientStop(1.0, t.accent_dim[0],
                                                    t.accent_dim[1],
                                                    t.accent_dim[2], 0)])
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h * 0.8)
        canvas.set_fill_color(*t.panel)      # clear gradient override

        # face box: 132-square scaled, top-centered
        label_block = 64.0
        box = min(132.0, rect.w - 40.0,
                  rect.h - label_block - 24.0)
        s = box / 132.0
        ox = rect.x + (rect.w - box) / 2.0
        oy = rect.y + 14.0
        canvas.save()
        canvas.translate(ox, oy)
        canvas.scale(s, s)
        self._draw_face(canvas, t)
        canvas.restore()

        # name / expr / state lines
        y = oy + box + 8.0
        canvas.set_font(style.FONT_UI, 18, "bold")
        m = canvas.measure_text(self.name)
        canvas.set_fill_color(*t.ink)
        cx = rect.x + rect.w / 2
        canvas.draw_text(self.name, cx, y + m.ascent, "center")
        y += m.ascent + m.descent + 4

        # expr line: vector state dot + "SELF/AVATAR · FOCUSED" (accent)
        expr = self.expression.upper()
        line = f"SELF/AVATAR · {expr}"
        canvas.set_font(style.FONT_MONO, ct.LABEL_MONO, "regular")
        em = canvas.measure_text("Ag")
        lw = ct.measure_tracked(canvas, line, 1.0)
        lx = cx - (lw + 10) / 2 + 10
        dot_cy = y + em.ascent / 2 + 2
        frac = _EXPR_DOT_FRAC.get(self.drawn_state(), 1.0)
        canvas.set_stroke_color(*t.accent)
        canvas.set_stroke_width(1.2)
        canvas.stroke_circle(lx - 8, dot_cy, 3.2)
        canvas.set_fill_color(*t.accent)
        canvas.fill_circle(lx - 8, dot_cy, 3.2 * frac)
        ct.draw_tracked(canvas, line, lx, y + em.ascent, 1.0, t.accent)
        y += em.ascent + em.descent + 6

        # state line: prefix faint · ok segment · suffix faint
        canvas.set_font(style.FONT_MONO, 9.0, "regular")
        sm = canvas.measure_text("Ag")
        parts = [(self.state_prefix + " ", t.faint),
                 (self.state_ok, t.ok)]
        if self.state_suffix:
            parts.append((" " + self.state_suffix, t.faint))
        total_w = sum(canvas.measure_text(p).width for p, _ in parts)
        sx = cx - total_w / 2
        for text, col in parts:
            canvas.set_fill_color(*col)
            canvas.draw_text(text, sx, y + sm.ascent, "left")
            sx += canvas.measure_text(text).width

        canvas.restore()   # clip
        canvas.restore()

    def _draw_compact(self, canvas: Canvas, rect: Rect, t) -> None:
        """Chromeless header-corner presence: a LARGER ring FACE with the
        EXPRESSION WORD centered BENEATH it, nothing else.

        David 2026-07-21 (screenshot 14:03): the avatar sits in the header
        space above the tab fold, top-right — the ring at its real size with
        the expression word (near-white, tracked uppercase) directly under it.
        No name, no `SELF/AVATAR` namespace label, no state/registered line,
        no card chrome — the face + one word is the whole presence signal.
        (Earlier it drew face + word HORIZONTALLY at ~44px; this is the
        vertical header treatment David's reference calls for —
        [DEC-INTELLIGENCE-WIDGET-CONSOLIDATION].)"""
        canvas.save()

        # the expression word block (sans, tracked uppercase, near-white ink)
        expr = self.expression.upper()
        canvas.set_font(style.FONT_UI, 12.5, "medium")
        wm = canvas.measure_text("Ag")
        word_gap = 8.0
        word_block = wm.ascent + wm.descent + word_gap

        # the ring/face: as large as the rect allows after the word block,
        # capped at the pre-vis 96-box, centered horizontally.
        box = max(24.0, min(rect.w, rect.h - word_block, 96.0))
        s = box / 132.0
        ox = rect.x + (rect.w - box) / 2.0
        oy = rect.y + max(0.0, (rect.h - word_block - box) / 2.0)
        canvas.save()
        canvas.translate(ox, oy)
        canvas.scale(s, s)
        self._draw_face(canvas, t)
        canvas.restore()

        cx = rect.x + rect.w / 2.0
        base = oy + box + word_gap + wm.ascent
        wtot = ct.measure_tracked(canvas, expr, 1.2)
        ct.draw_tracked(canvas, expr, cx - wtot / 2.0, base, 1.2, t.ink)
        canvas.restore()

    def _draw_face(self, canvas: Canvas, t) -> None:
        """The face in 132-box coordinates (caller sets the transform)."""
        state = _paths(self.drawn_state())

        # breathing halo (scale + opacity about its center)
        canvas.save()
        canvas.translate(66, 66)
        canvas.scale(self._halo_scale, self._halo_scale)
        canvas.translate(-66, -66)
        canvas.set_opacity(self._halo_op)
        canvas.set_fill_color(*t.accent_dim)
        canvas.fill_circle(66, 66, 52)
        canvas.set_opacity(1.0)
        canvas.restore()

        # face disc
        canvas.set_fill_color(*t.panel2)
        canvas.fill_circle(66, 66, 42)
        canvas.set_stroke_color(*t.accent_line)
        canvas.set_stroke_width(1.5)
        canvas.stroke_circle(66, 66, 42)

        # brows (accent, w3)
        canvas.set_stroke_color(*t.accent)
        canvas.set_stroke_width(3.0)
        for path in state["brows"]:
            canvas.draw_path(path)

        # eyes (the blink group: scaleY about the eye line)
        canvas.save()
        canvas.translate(0, 63)
        canvas.scale(1.0, self._blink)
        canvas.translate(0, -63)
        canvas.set_stroke_color(*t.ink)
        canvas.set_stroke_width(3.0)
        for path in state["eyes"]:
            canvas.draw_path(path)
        canvas.set_fill_color(*t.ink)
        for ex, ey, er in state["eye_dots"]:
            canvas.fill_circle(ex, ey, er)
        # 'spark' only: a bright accent highlight glint in each eye ("lit up").
        # Empty for the other three -> zero extra calls (byte-identical).
        glints = state.get("eye_glints", ())
        if glints:
            canvas.set_fill_color(*t.accent)
            for gx, gy, gr in glints:
                canvas.fill_circle(gx, gy, gr)
        canvas.restore()

        # mouth (dim, w2.5)
        canvas.set_stroke_color(*t.dim)
        canvas.set_stroke_width(2.5)
        canvas.draw_path(state["mouth"])

        # 'spark' signature: the temple sparkle + micro-glints (accent).
        # Absent for the other three -> zero extra calls (byte-identical).
        spark = state.get("spark")
        if spark:
            canvas.set_fill_color(*t.accent)
            canvas.fill_path(spark)
            for sx, sy, sr in state.get("spark_glints", ()):
                canvas.fill_circle(sx, sy, sr)
