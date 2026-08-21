"""
ComposeUX ModelPickerWidget — the model/effort picker modal
([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION], previs ``.picker``).

Reuses ``DialogWidget`` by reference (R3): the full-surface scrim, the
centered panel, modal hit-capture, and the ``visible`` gate all come from the
base — this subclass only draws the picker CONTENT (a Model row, an Effort
row, a live ``→ pty`` preview line, and Apply) and routes taps.

Opened by a bus publish to ``namespace`` (the Intelligence card's Change chip
publishes "on"); Apply publishes ``self/model/select = {model, cmd, effort}``
and closes.  **No launcher call** — the actuation is the architect's follow-on.

Own-every-pixel ([CXC-R-DP-1]): drawn entirely on the Canvas contract with
console-theme tokens; the ▸-free chrome is glyphs the bundled Plex faces cover.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..canvas import Canvas
from ..widget import Rect
from .dialog import DialogWidget
from . import console_theme as ct
from . import style

DEFAULT_OPEN_NAMESPACE = "_app/model_picker"
DEFAULT_SELECT_NAMESPACE = "self/model/select"

# (label, cmd, sub-line, scarce?)
MODELS: List[Tuple[str, str, str, bool]] = [
    ("Opus 4.8", "opus", "everyday", False),
    ("Fable 5", "fable", "render depth", True),
]
# (label, level)
EFFORTS: List[Tuple[str, str]] = [
    ("Low", "low"), ("Med", "medium"), ("High", "high"), ("Max", "max"),
]

PANEL_W = 360.0
PANEL_H = 286.0
PAD = 16.0


class ModelPickerWidget(DialogWidget):
    """Model/effort picker modal ([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION])."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_OPEN_NAMESPACE,
                 select_namespace: str = DEFAULT_SELECT_NAMESPACE,
                 model_cmd: str = "opus",
                 effort: str = "high",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, namespace=namespace,
                         panel_width=PANEL_W, panel_height=PANEL_H,
                         dismissible=True, **kwargs)
        self.widget_type = "model_picker"
        self.select_ns = str(select_namespace or DEFAULT_SELECT_NAMESPACE)
        self._cmd = str(model_cmd or "opus")
        self._effort = str(effort or "high")
        # hit rects recorded by draw()
        self._model_rects: List[Tuple[Rect, str]] = []
        self._effort_rects: List[Tuple[Rect, str]] = []
        self._apply_rect: Optional[Rect] = None
        self._close_rect: Optional[Rect] = None

    # DialogWidget.on_bus_state (non-list branch) maps the open namespace to
    # visibility — inherited verbatim.

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def _model_label(self) -> str:
        for label, cmd, _, _ in MODELS:
            if cmd == self._cmd:
                return label
        return self._cmd

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if not self.visible or self._panel_rect is None:
            return
        t = ct.CONSOLE.active
        canvas.save()
        # scrim
        canvas.set_fill_color(4, 8, 14, 184)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        # panel
        p = self._panel_rect
        ct.draw_card(canvas, p.x, p.y, p.w, p.h, 14.0, t)
        canvas.save()
        canvas.clip_round_rect(p.x, p.y, p.w, p.h, 14.0)

        # header row
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO + 1.0, "regular")
        hm = canvas.measure_text("Ag")
        hbase = p.y + 16.0 + hm.ascent
        ct.draw_tracked(canvas, "MODEL & EFFORT", p.x + PAD, hbase,
                        ct.TRACK_LABEL, t.ink)
        canvas.set_font(style.FONT_UI, 15.0, "regular")
        xm = canvas.measure_text("x")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("x", p.x + p.w - PAD - xm.width,
                         hbase, "left")
        self._close_rect = Rect(p.x + p.w - PAD - xm.width - 6,
                                p.y + 8, xm.width + 12, 28)
        hr_y = p.y + 44.0
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(p.x, hr_y, p.x + p.w, hr_y)

        y = hr_y + 14.0
        y = self._section_cap(canvas, p, y, "MODEL", t)
        y = self._draw_models(canvas, p, y, t)
        y += 10.0
        y = self._section_cap(canvas, p, y, "EFFORT", t)
        y = self._draw_efforts(canvas, p, y, t)

        # foot: preview + Apply
        self._draw_foot(canvas, p, t)

        canvas.restore()
        canvas.restore()

    def _section_cap(self, canvas, p, y, text, t) -> float:
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        m = canvas.measure_text("Ag")
        ct.draw_tracked(canvas, text, p.x + PAD, y + m.ascent, 0.9, t.faint)
        return y + m.ascent + m.descent + 9.0

    def _draw_models(self, canvas, p, y, t) -> float:
        self._model_rects = []
        gap = 8.0
        ow = (p.w - 2 * PAD - gap) / 2.0
        oh = 56.0
        for i, (label, cmd, sub, scarce) in enumerate(MODELS):
            ox = p.x + PAD + i * (ow + gap)
            sel = cmd == self._cmd
            if sel:
                canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2],
                                      26)
                canvas.fill_round_rect(ox, y, ow, oh, 9.0)
                canvas.set_stroke_color(*t.accent)
            else:
                canvas.set_fill_color(*t.panel2)
                canvas.fill_round_rect(ox, y, ow, oh, 9.0)
                canvas.set_stroke_color(*t.line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(ox + 0.5, y + 0.5, ow - 1, oh - 1, 9.0)
            # title (centered) + optional " · scarce" (warn)
            canvas.set_font(style.FONT_UI, 13.0, "bold")
            tm = canvas.measure_text(label)
            scarce_txt = " · scarce" if scarce else ""
            canvas.set_font(ct.FONT_MONO, 8.5, "regular")
            sm = canvas.measure_text(scarce_txt) if scarce_txt else \
                type("m", (), {"width": 0.0})()
            total = tm.width + sm.width
            tx = ox + (ow - total) / 2.0
            ty = y + 16.0
            canvas.set_font(style.FONT_UI, 13.0, "bold")
            canvas.set_fill_color(*t.ink)
            canvas.draw_text(label, tx, ty + tm.ascent, "left")
            if scarce_txt:
                canvas.set_font(ct.FONT_MONO, 8.5, "regular")
                canvas.set_fill_color(*t.warn)
                canvas.draw_text(scarce_txt, tx + tm.width,
                                 ty + tm.ascent, "left")
            # sub-line (uppercase mono; accent when selected, faint else)
            canvas.set_font(ct.FONT_MONO, 8.5, "regular")
            subm = canvas.measure_text("Ag")
            sw = ct.measure_tracked(canvas, sub.upper(), 0.6)
            ct.draw_tracked(canvas, sub.upper(), ox + (ow - sw) / 2.0,
                            ty + tm.ascent + tm.descent + 6 + subm.ascent,
                            0.6, t.accent if sel else t.faint)
            self._model_rects.append((Rect(ox, y, ow, oh), cmd))
        return y + oh

    def _draw_efforts(self, canvas, p, y, t) -> float:
        self._effort_rects = []
        gap = 6.0
        n = len(EFFORTS)
        cw = (p.w - 2 * PAD - (n - 1) * gap) / n
        ch = 34.0
        for i, (label, level) in enumerate(EFFORTS):
            cx = p.x + PAD + i * (cw + gap)
            sel = level == self._effort
            if sel:
                canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2],
                                      26)
                canvas.fill_round_rect(cx, y, cw, ch, 8.0)
                canvas.set_stroke_color(*t.accent)
            else:
                canvas.set_fill_color(*t.panel2)
                canvas.fill_round_rect(cx, y, cw, ch, 8.0)
                canvas.set_stroke_color(*t.line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(cx + 0.5, y + 0.5, cw - 1, ch - 1, 8.0)
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            lw = ct.measure_tracked(canvas, label.upper(), 0.5)
            m = canvas.measure_text("Ag")
            ct.draw_tracked(canvas, label.upper(), cx + (cw - lw) / 2.0,
                            y + ch / 2.0 + (m.ascent - m.descent) / 2.0, 0.5,
                            t.accent if sel else t.dim)
            self._effort_rects.append((Rect(cx, y, cw, ch), level))
        return y + ch

    def _draw_foot(self, canvas, p, t) -> None:
        fy = p.y + p.h - 16.0 - 34.0
        # Apply button (accent bg, ground ink), right
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        am = canvas.measure_text("Apply")
        bw = am.width + 32.0
        bh = 34.0
        bx = p.x + p.w - PAD - bw
        canvas.set_fill_color(*t.accent)
        canvas.fill_round_rect(bx, fy, bw, bh, 8.0)
        canvas.set_fill_color(*t.send_ink)
        canvas.draw_text("Apply", bx + 16,
                         fy + bh / 2.0 + (am.ascent - am.descent) / 2.0,
                         "left")
        self._apply_rect = Rect(bx, fy, bw, bh)
        # preview line: "→ pty: /model <cmd> · effort <lvl>"
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        pm = canvas.measure_text("Ag")
        pbase = fy + bh / 2.0 + (pm.ascent - pm.descent) / 2.0
        pre = "→ pty: "
        cmd = f"/model {self._cmd} · effort {self._effort}"
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(pre, p.x + PAD, pbase, "left")
        prw = canvas.measure_text(pre).width
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(cmd, p.x + PAD + prw, pbase, "left")

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        p = self._panel_rect
        if p is None:
            return
        if self._close_rect and _in(self._close_rect, x, y):
            self.dismiss()
            return
        for r, cmd in self._model_rects:
            if _in(r, x, y):
                self._cmd = cmd
                self.request_redraw()
                return
        for r, level in self._effort_rects:
            if _in(r, x, y):
                self._effort = level
                self.request_redraw()
                return
        if self._apply_rect and _in(self._apply_rect, x, y):
            self.publish(self.select_ns,
                         {"model": self._model_label(), "cmd": self._cmd,
                          "effort": self._effort})
            self.dismiss()
            return
        # a tap outside the panel dismisses (inherited semantics)
        if self.dismissible and not _in(p, x, y):
            self.dismiss()


def _in(r: Rect, x: float, y: float) -> bool:
    return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h
