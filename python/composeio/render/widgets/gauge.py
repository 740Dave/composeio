"""
ComposeUX Gauge Widget

Arc gauge with needle, scale marks, and value text.
Looks like a real dashboard gauge: arc sweeps from 225 deg (7 o'clock)
clockwise to 315 deg (5 o'clock) giving a 270-degree sweep.

STYLE MODES ([SPEC:CXC-CONSOLE-VISUAL] [CXC-ICV-GAUGE], architect
ratification: generalize, don't fork):

    style: "dial"        (default) the original 270-degree needle dial —
                          byte-for-byte the pre-fidelity rendering.
    style: "semicircle"  the Intelligence Console context gauge: a 180-degree
                          semicircle with a 4-segment zone band (accent-dim /
                          warn-dim / warn / crit), a state-colored lead arc,
                          threshold ticks, a value-marker dot, the readout-xl
                          big number + tok/src lines, the threshold row, and
                          the crit-zone foot.  The ONE console surface allowed
                          to warm with state; the lead arc hot-pulses only in
                          the crit zone ([CXC-ICV-MOTION] `hotpulse`).

    style: "bar"         a linear FILL / FADER gauge — an inset track the value
                          fills as a fraction, with a bright level-cap line, a
                          min/max scale, and a big raw-value readout.  Same
                          namespace-bound API as the dial (namespace, min_val,
                          max_val, label, warning/danger thresholds — the fill
                          warms teal->amber->red on threshold).  `orientation`
                          selects the axis and origin:
                            "vertical"   (default) fills BOTTOM->TOP — a throttle
                                         lever / VU meter.
                            "horizontal" fills LEFT->RIGHT — a level meter.
                          This is one more style MODE on the ONE GaugeWidget
                          ([CXC-ICV-GAUGE] "generalize, don't fork"), NOT a new
                          widget — a readout in the primitive catalog
                          ([CXC-PRIM-CATALOG]), distinct from the forkable INPUT
                          composites ([CXC-PRIM-WHY]).

    style: "led_bar"     a simulated SEGMENTED LED bar-graph (LM3914-style level
                          meter) — a discrete row/column of `segments` (default
                          10) LEDs on a near-black bezel, like a physical LED
                          bar-graph module.  Segment i (1..N) is LIT when
                          value >= min_val + (i/N)*(max_val - min_val) (deciles
                          for N=10); a lit segment glows its palette color, an
                          unlit one is a dim (~16% luminance) version of the SAME
                          color so off-LEDs still read as colored-but-dark.  The
                          palette IS the zone coloring (default blue idle / green
                          cruise / yellow caution / red max — sampled from a real
                          module), so warning/danger thresholds are irrelevant
                          here and untouched.  `orientation` picks the axis
                          (vertical fills bottom->top, horizontal left->right);
                          `reverse` flips the fill direction along that axis
                          (top->bottom / right->left).  Same style-MODE-not-a-
                          fork discipline ([CXC-ICV-GAUGE], [CXC-PRIM-FADER]);
                          built from fill_round_rect / fill_circle ONLY so it
                          renders identically on Skia, the browser, and the CYD
                          band renderer ([CXC-R-DRAW-CONTRACT]).
                          [CXC-PRIM-LEDBAR] geometry params (David 2026-07-29,
                          all additive — defaults keep the render byte-
                          identical):  `segment_shape` rectangular (default) |
                          square | round;  `segment_long_axis` across (default)
                          | along (rectangular only — flips the rect's long
                          axis relative to the fill direction);  `segment_gap`
                          px between LEDs (was hardcoded 4);  `padding` px bezel
                          inset (was hardcoded 8);  `min_segment_size` px floor
                          on the LED short axis — when the strip cannot host N
                          LEDs at the floor it shrinks the gap toward 1px then
                          reduces the rendered count (rescaling the deciles +
                          palette), never rendering below the floor nor
                          overflowing the rect.

Uses ONLY the abstract Canvas contract — never imports skia directly.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ..canvas import Canvas, GradientStop, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct

# ComposeUX dark theme
_BG = (0x1E, 0x1E, 0x2E)           # charcoal
_TEAL = (0x26, 0xA6, 0x9A)         # accent
_AMBER = (0xFF, 0xA7, 0x26)        # warm amber
_RED = (0xEF, 0x53, 0x50)          # danger
_TEXT = (0xE0, 0xE0, 0xE0)         # light grey text
_DIM = (0x66, 0x66, 0x78)          # dim marks
_TRACK = (0x14, 0x14, 0x20)        # inset bar-track channel ([CXC-PRIM-TOKENS])

# -- led_bar-mode tokens ([CXC-PRIM-CATALOG], LM3914 level-meter look) ---------
_LED_BEZEL = (0x0A, 0x0A, 0x0C)    # near-black module body the segments sit on
# The dim luminance an UNLIT segment reads at: the same hue as its lit color
# multiplied by this factor, so an off-LED is colored-but-dark, not black.
# JUDGMENT CALL (tunable) — 0.16 puts an off-green at ~(0,40,0) vs the bezel's
# ~(10,10,12): clearly colored, clearly off.
_LED_DIM_FACTOR = 0.16
# Fraction a LIT segment's inner "lens" core is brightened toward white — gives
# the segment a glossy illuminated centre with a saturated rim (still hue-true).
_LED_CORE_LIFT = 0.30
# Default palette sampled from the reference module photo (10 deciles):
#   seg 1 blue (idle) · segs 2-5 green (cruise) · 6-8 yellow (caution) ·
#   9-10 red (max).  Fewer entries than `segments` -> the last repeats.
_LED_DEFAULT_PALETTE = [
    "#0236FE",
    "#01FF03", "#01FF03", "#01FF03", "#01FF03",
    "#FFEB01", "#FFEB01", "#FFEB01",
    "#FE0002", "#FE0002",
]


def _hex_to_rgb(s: str) -> tuple:
    """'#RRGGBB' (or 'RRGGBB') -> (r, g, b).  Bad input -> mid-grey (never
    breaks the screen — the module still renders)."""
    try:
        h = str(s).lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except (ValueError, IndexError, TypeError):
        return (0x80, 0x80, 0x80)


# Gauge arc geometry (degrees, converted to radians for drawing)
_ARC_START_DEG = 225   # 7 o'clock position
_ARC_SWEEP_DEG = 270   # sweeps clockwise to 5 o'clock


def _lerp_angle(frac: float) -> float:
    """Return the angle (radians) for a 0-1 fraction along the gauge arc."""
    # 0 = start (225 deg), 1 = end (225 - 270 = -45 deg, i.e. 315 deg)
    deg = _ARC_START_DEG - frac * _ARC_SWEEP_DEG
    return math.radians(deg)


class GaugeWidget(Widget):
    """Arc gauge with needle, tick marks, and value display."""

    def __init__(self, widget_id: str, *,
                 min_val: float = 0,
                 max_val: float = 100,
                 value: float = 0,
                 unit: str = "",
                 label: str = "",
                 warning_threshold: float | None = None,
                 danger_threshold: float | None = None,
                 style: str = "dial",
                 orientation: str = "vertical",
                 mid_threshold: float | None = None,
                 ns_tag: str = "",
                 chrome: str = "",
                 segments: int = 10,
                 segment_colors: list[str] | None = None,
                 reverse: bool = False,
                 segment_shape: str = "rectangular",
                 segment_long_axis: str = "across",
                 segment_gap: float = 4.0,
                 padding: float = 8.0,
                 min_segment_size: float = 0.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "gauge", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.min_val = min_val
        self.max_val = max_val
        self.value = value
        self.unit = unit
        self.label = label
        self.warning_threshold = warning_threshold
        self.danger_threshold = danger_threshold
        # [CXC-ICV-GAUGE] semicircle-mode style attrs (inert for "dial"):
        self.style = str(style or "dial")
        # [CXC-PRIM-CATALOG] bar-mode fill axis+origin (inert for dial/semicircle):
        # "vertical" fills bottom->top (fader/VU), "horizontal" fills left->right.
        self.orientation = str(orientation or "vertical").lower()
        # chrome="bare" ([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION]): the arc +
        # readout ONLY — no card, no header, no tok/src lines, no threshold
        # row, no crit foot.  The ARC GEOMETRY itself is byte-identical (David
        # rejected redesigning the gauge twice); bare only GATES the decoration
        # so the gauge can be embedded inside another card that owns the chrome.
        self.chrome = str(chrome or "")
        # [CXC-PRIM-CATALOG] led_bar-mode config (inert for dial/semicircle/bar):
        # a segmented LM3914-style level meter.  `segments` = LED count;
        # `segment_colors` = per-segment hex palette (default = the engine
        # palette above; short lists repeat the last entry); `reverse` flips the
        # fill direction along the orientation axis.
        try:
            self.segments = max(1, int(segments))
        except (TypeError, ValueError):
            self.segments = 10
        self.segment_colors = segment_colors
        self.reverse = bool(reverse)
        # [CXC-PRIM-LEDBAR] NEW led_bar geometry params (David 2026-07-29).
        # All defaults chosen so the render is byte-identical to the
        # pre-enhancement output (rectangular / across / gap 4 / pad 8 / no
        # floor).  Bad input falls back to the backward-compatible default so a
        # config typo never breaks the screen.
        self.segment_shape = str(segment_shape or "rectangular").lower()
        if self.segment_shape not in ("rectangular", "square", "round"):
            self.segment_shape = "rectangular"
        self.segment_long_axis = str(segment_long_axis or "across").lower()
        if self.segment_long_axis not in ("across", "along"):
            self.segment_long_axis = "across"
        try:
            self.segment_gap = max(0.0, float(segment_gap))
        except (TypeError, ValueError):
            self.segment_gap = 4.0
        try:
            self.padding = max(0.0, float(padding))
        except (TypeError, ValueError):
            self.padding = 8.0
        try:
            self.min_segment_size = max(0.0, float(min_segment_size))
        except (TypeError, ValueError):
            self.min_segment_size = 0.0
        self.mid_threshold = mid_threshold      # the .80 boundary (defaults
        self.ns_tag = str(ns_tag)               # between warn and danger)
        # additive telemetry surfaced when the payload is a dict
        # ({pct, tokens, max_tokens, source}) — [CXC-ICV-GAUGE] additive read.
        self._tokens: Optional[float] = None
        self._max_tokens: Optional[float] = None
        self._source: str = ""
        self._lead_opacity: float = 1.0         # hotpulse rest frame

    # -- bus hook -----------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if isinstance(value, dict):
            # ADDITIVE read ([CXC-ICV-GAUGE]): a dict payload additionally
            # carries tokens/source.  A float payload takes the original
            # path below, byte-for-byte (a dict previously no-op'd here).
            try:
                self.value = float(value.get("pct", value.get("value")))
            except (TypeError, ValueError):
                pass
            for k, attr in (("tokens", "_tokens"),
                            ("max_tokens", "_max_tokens")):
                try:
                    setattr(self, attr, float(value[k]))
                except (KeyError, TypeError, ValueError):
                    pass
            src = value.get("source")
            if src is not None:
                self._source = str(src)
            self.request_redraw()
            return
        try:
            self.value = float(value)
        except (TypeError, ValueError):
            pass

    # -- semicircle motion ([CXC-ICV-MOTION] hotpulse) ------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        if self.style == "semicircle":
            ct.theme_from_ctx(ctx)
            if ctx is not None and hasattr(ctx, "register_clocked"):
                ctx.register_clocked(self)

    def on_unmount(self) -> None:
        if self._ctx is not None and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)
        super().on_unmount()

    def on_clock_tick(self, now: float) -> bool:
        """hotpulse: the lead arc pulses ONLY in the crit zone; the reduced-
        motion gate renders the rest frame and requests no more frames."""
        if self.style != "semicircle":
            return False
        if ct.reduced_motion(self._ctx) or not self._in_crit_zone():
            changed = self._lead_opacity != 1.0
            self._lead_opacity = 1.0
            if changed:
                self.request_redraw()
            return False
        self._lead_opacity = ct.pulse(now, ct.HOTPULSE_PERIOD, ct.HOTPULSE_LOW)
        return True

    def _in_crit_zone(self) -> bool:
        danger = self.danger_threshold if self.danger_threshold is not None \
            else self.min_val + 0.9 * (self.max_val - self.min_val)
        return self.value >= danger

    # -- measure / draw -----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        if self.style == "semicircle":
            w = min(constraints.max_width, 300)
            return Size(w, min(constraints.max_height, w * 1.28))
        if self.style in ("bar", "led_bar"):
            if self.orientation == "horizontal":
                w = constraints.max_width
                if not math.isfinite(w) or w <= 0:
                    w = 300.0
                return Size(min(w, 420.0), min(constraints.max_height, 120.0))
            # vertical (default): narrow + tall, fills the offered height.
            h = constraints.max_height
            if not math.isfinite(h) or h <= 0:
                h = 300.0
            return Size(min(constraints.max_width, 160.0), h)
        side = min(constraints.max_width, constraints.max_height, 200)
        return Size(side, side)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self.style == "semicircle":
            self._draw_semicircle(canvas, rect)
            return
        if self.style == "bar":
            self._draw_bar(canvas, rect)
            return
        if self.style == "led_bar":
            self._draw_led_bar(canvas, rect)
            return
        self._draw_dial(canvas, rect)

    # -- shared value helpers (bar + dial thresholds) -----------------------

    def _threshold_color(self) -> tuple:
        """teal ok / amber past warn / red past danger — the fill hue the bar
        (and the dial arc) warm to.  A None threshold never fires."""
        if self.danger_threshold is not None and \
                self.value >= self.danger_threshold:
            return _RED
        if self.warning_threshold is not None and \
                self.value >= self.warning_threshold:
            return _AMBER
        return _TEAL

    def _readout_text(self) -> str:
        """The raw value: integer when whole (0..255 pot reads), else 1 dp."""
        v = self.value
        try:
            if float(v).is_integer():
                return f"{int(v)}"
        except (TypeError, ValueError, OverflowError):
            pass
        return f"{v:.1f}"

    # -- the original 270-degree dial (UNCHANGED rendering) -------------------

    def _draw_dial(self, canvas: Canvas, rect: Rect) -> None:
        cx = rect.x + rect.w / 2
        cy = rect.y + rect.h / 2
        outer_r = min(rect.w, rect.h) / 2 - 4
        inner_r = outer_r * 0.72

        # --- background circle ---
        canvas.save()
        canvas.set_fill_color(*_BG, 200)
        canvas.fill_circle(cx, cy, outer_r)

        # --- arc track (dim) ---
        canvas.set_stroke_color(*_DIM)
        canvas.set_stroke_width(4)
        start_rad = math.radians(-_ARC_START_DEG)
        end_rad = math.radians(-_ARC_START_DEG + _ARC_SWEEP_DEG)
        canvas.stroke_arc(cx, cy, inner_r, start_rad, end_rad)

        # --- coloured arc (value) ---
        frac = max(0.0, min(1.0, (self.value - self.min_val) /
                                  (self.max_val - self.min_val)))
        if frac > 0:
            value_sweep = _ARC_SWEEP_DEG * frac
            # Pick colour based on thresholds
            if self.danger_threshold is not None and self.value >= self.danger_threshold:
                arc_color = _RED
            elif self.warning_threshold is not None and self.value >= self.warning_threshold:
                arc_color = _AMBER
            else:
                arc_color = _TEAL

            canvas.set_stroke_color(*arc_color)
            canvas.set_stroke_width(6)
            value_end_rad = math.radians(-_ARC_START_DEG + value_sweep)
            canvas.stroke_arc(cx, cy, inner_r, start_rad, value_end_rad)

        # --- tick marks ---
        num_ticks = 10
        canvas.set_stroke_color(*_DIM)
        canvas.set_stroke_width(1.5)
        for i in range(num_ticks + 1):
            angle = _lerp_angle(i / num_ticks)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            # major ticks are longer
            is_major = (i % 5 == 0)
            tick_inner = inner_r - (10 if is_major else 5)
            tick_outer = inner_r + 2
            canvas.draw_line(
                cx + cos_a * tick_inner, cy - sin_a * tick_inner,
                cx + cos_a * tick_outer, cy - sin_a * tick_outer,
            )

        # --- tick labels (at 0%, 50%, 100%) ---
        canvas.set_fill_color(*_TEXT)
        canvas.set_font("sans-serif", outer_r * 0.13, "regular")
        for i, pct in enumerate([0, 0.5, 1.0]):
            angle = _lerp_angle(pct)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            lbl_r = inner_r - 18
            lx = cx + cos_a * lbl_r
            ly = cy - sin_a * lbl_r
            val = self.min_val + pct * (self.max_val - self.min_val)
            canvas.draw_text(f"{val:.0f}", lx, ly, "center")

        # --- needle ---
        needle_angle = _lerp_angle(frac)
        cos_n, sin_n = math.cos(needle_angle), math.sin(needle_angle)
        needle_len = inner_r * 0.85
        canvas.set_stroke_color(*_TEXT)
        canvas.set_stroke_width(2.5)
        canvas.draw_line(cx, cy,
                         cx + cos_n * needle_len, cy - sin_n * needle_len)

        # Needle hub
        canvas.set_fill_color(*_TEXT)
        canvas.fill_circle(cx, cy, outer_r * 0.06)

        # --- value text ---
        value_text = f"{self.value:.1f}"
        if self.unit:
            value_text += f" {self.unit}"
        canvas.set_font("sans-serif", outer_r * 0.22, "bold")
        canvas.set_fill_color(*_TEXT)
        canvas.draw_text(value_text, cx, cy + outer_r * 0.58, "center")

        # --- label text ---
        if self.label:
            canvas.set_font("sans-serif", outer_r * 0.13, "regular")
            canvas.set_fill_color(*_DIM)
            canvas.draw_text(self.label, cx, cy + outer_r * 0.78, "center")

        canvas.restore()

    # -- the linear fill / fader bar ([CXC-PRIM-CATALOG]) ---------------------
    #
    # An inset track the value fills as a fraction of [min_val, max_val].  The
    # fill grows FROM the origin edge (vertical -> from the bottom up, the
    # throttle-lever / VU look; horizontal -> from the left).  A bright
    # level-cap line marks the fill edge; a faint scale + min/max end labels
    # frame it; a big raw-value readout sits opposite the label.  The fill
    # warms teal->amber->red on the thresholds ([CXC-PRIM-TOKENS]: accent is
    # the active fill; semantic is a separate threshold axis).

    def _draw_bar(self, canvas: Canvas, rect: Rect) -> None:
        vertical = self.orientation != "horizontal"
        frac = self._frac(self.value)
        color = self._threshold_color()
        bright = tuple(min(c + 46, 255) for c in color)
        radius = 6.0
        pad = 10.0

        canvas.save()

        if vertical:
            label_h = 20.0 if self.label else 0.0
            readout_h = 30.0
            track_top = rect.y + label_h + pad
            track_bottom = rect.y + rect.h - readout_h - pad
            track_h = max(1.0, track_bottom - track_top)
            track_w = min(max(rect.w - 2 * pad, 8.0), 64.0)
            track_x = rect.x + (rect.w - track_w) / 2
            track_y = track_top

            # inset track channel + border
            canvas.set_fill_color(*_TRACK)
            canvas.fill_round_rect(track_x, track_y, track_w, track_h, radius)

            # fill from the bottom up (clip to the track's rounded channel so the
            # fill's bottom corners are rounded and its top is a flat level edge)
            if frac > 0:
                fill_h = track_h * frac
                fill_y = track_bottom - fill_h
                canvas.save()
                canvas.clip_round_rect(track_x, track_y, track_w, track_h,
                                       radius)
                canvas.set_linear_gradient(
                    track_x, fill_y, track_x, track_bottom,
                    [GradientStop(0.0, *bright, 255),
                     GradientStop(1.0, *color, 255)])
                canvas.fill_rect(track_x, fill_y, track_w, fill_h)
                canvas.restore()
                # bright level-cap line at the fill edge
                canvas.set_stroke_color(*bright)
                canvas.set_stroke_width(2.0)
                canvas.draw_line(track_x, fill_y, track_x + track_w, fill_y)

            canvas.set_stroke_color(*_DIM)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(track_x, track_y, track_w, track_h, radius)

            # scale ticks along the right edge (quarter marks)
            for i in range(5):
                ty = track_bottom - (i / 4.0) * track_h
                canvas.draw_line(track_x + track_w, ty,
                                 track_x + track_w + 4, ty)

            # min/max end labels (mono, faint) just outside the track top/bottom
            canvas.set_font("monospace", 10.0, "regular")
            canvas.set_fill_color(*_DIM)
            canvas.draw_text(f"{self.max_val:.0f}", track_x + track_w + 7,
                             track_y + 8, "left")
            canvas.draw_text(f"{self.min_val:.0f}", track_x + track_w + 7,
                             track_bottom, "left")

            # big raw-value readout (state-warmed) below the track
            canvas.set_font("monospace", 22.0, "bold")
            canvas.set_fill_color(*color)
            canvas.draw_text(self._readout_text(), rect.x + rect.w / 2,
                             track_bottom + 26, "center")

            # label above the track
            if self.label:
                canvas.set_font("sans-serif", 12.0, "regular")
                canvas.set_fill_color(*_DIM)
                canvas.draw_text(self.label, rect.x + rect.w / 2,
                                 rect.y + 14, "center")
        else:
            # horizontal: fills left->right
            label_h = 18.0 if self.label else 0.0
            readout_h = 26.0
            track_x = rect.x + pad
            track_right = rect.x + rect.w - pad
            track_w = max(1.0, track_right - track_x)
            track_h = min(max(rect.h - label_h - readout_h - 2 * pad, 6.0),
                          22.0)
            track_y = rect.y + label_h + pad

            canvas.set_fill_color(*_TRACK)
            canvas.fill_round_rect(track_x, track_y, track_w, track_h, radius)

            if frac > 0:
                fill_w = track_w * frac
                canvas.save()
                canvas.clip_round_rect(track_x, track_y, track_w, track_h,
                                       radius)
                canvas.set_linear_gradient(
                    track_x, track_y, track_x + fill_w, track_y,
                    [GradientStop(0.0, *color, 255),
                     GradientStop(1.0, *bright, 255)])
                canvas.fill_rect(track_x, track_y, fill_w, track_h)
                canvas.restore()
                canvas.set_stroke_color(*bright)
                canvas.set_stroke_width(2.0)
                canvas.draw_line(track_x + fill_w, track_y,
                                 track_x + fill_w, track_y + track_h)

            canvas.set_stroke_color(*_DIM)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(track_x, track_y, track_w, track_h, radius)

            if self.label:
                canvas.set_font("sans-serif", 12.0, "regular")
                canvas.set_fill_color(*_DIM)
                canvas.draw_text(self.label, track_x, rect.y + 13, "left")

            canvas.set_font("monospace", 20.0, "bold")
            canvas.set_fill_color(*color)
            canvas.draw_text(self._readout_text(), track_right,
                             track_y + track_h + 22, "right")

        canvas.restore()

    # -- the segmented LED bar-graph ([CXC-PRIM-CATALOG], LM3914 level meter) --
    #
    # A discrete row/column of `segments` LEDs on a near-black bezel.  Segment i
    # (1..N) is LIT when value >= min_val + (i/N)*range (deciles for N=10); a lit
    # segment glows its palette color, an unlit one is a dim version of the SAME
    # color so an off-LED still reads as colored-but-dark.  Built from
    # fill_round_rect ONLY (R1: decompose to the contract) so the module renders
    # identically on Skia, the browser, and the CYD band renderer.

    def _led_lit(self, n: int | None = None) -> list:
        """Per-segment lit flags (index 0 == segment 1).  Segment i is lit when
        the value fraction has reached i/N (the tiny epsilon makes the threshold
        inclusive, i.e. value == threshold -> LIT, matching the spec `>=`).

        `n` overrides the segment count for the min_segment_size RESCALE path
        ([CXC-PRIM-LEDBAR]): when the rendered count drops to n', the deciles
        are recomputed against n' so the lit fraction is preserved.  Default
        (n=None -> self.segments) keeps the existing call sites byte-identical.
        """
        if n is None:
            n = self.segments
        frac = self._frac(self.value)
        return [frac >= (i / n) - 1e-9 for i in range(1, n + 1)]

    def _led_palette(self, n: int | None = None) -> list:
        """n (r,g,b) tuples: `segment_colors` (or the default engine palette),
        extended by REPEATING the last entry when shorter than `segments`.

        When `n` < self.segments (the min_segment_size RESCALE path), the
        FULL-count palette is SAMPLED down to n entries preserving the
        endpoints (segment 1 and segment N colors are kept), so the color zones
        span the same blue->red progression at the reduced count.  n=None (or
        n == self.segments) reproduces the original per-segment palette exactly.
        """
        if n is None:
            n = self.segments
        full_n = self.segments
        src = self.segment_colors if self.segment_colors else _LED_DEFAULT_PALETTE
        cols = [_hex_to_rgb(c) for c in src] or [(0x80, 0x80, 0x80)]
        if len(cols) < full_n:
            cols = cols + [cols[-1]] * (full_n - len(cols))
        full = cols[:full_n]
        if n >= full_n:
            return full
        if n <= 1:
            return [full[0]]
        # resample full[0..full_n-1] -> n entries, endpoints preserved
        return [full[round(j * (full_n - 1) / (n - 1))] for j in range(n)]

    def _led_draw_seg(self, canvas: Canvas, x: float, y: float,
                      w: float, h: float, r: float,
                      color: tuple, is_lit: bool) -> None:
        if w <= 0 or h <= 0:
            return
        if is_lit:
            canvas.set_fill_color(*color)
            canvas.fill_round_rect(x, y, w, h, r)
            # brighter inner lens core (saturated rim stays hue-true)
            core = tuple(min(255, int(c + (255 - c) * _LED_CORE_LIFT))
                         for c in color)
            inset = min(1.8, w * 0.18, h * 0.18)
            if w - 2 * inset > 0 and h - 2 * inset > 0:
                canvas.set_fill_color(*core)
                canvas.fill_round_rect(x + inset, y + inset,
                                       w - 2 * inset, h - 2 * inset,
                                       max(1.0, r - 1))
        else:
            dim = tuple(max(0, int(c * _LED_DIM_FACTOR)) for c in color)
            canvas.set_fill_color(*dim)
            canvas.fill_round_rect(x, y, w, h, r)

    def _led_draw_circle(self, canvas: Canvas, cx: float, cy: float, r: float,
                         color: tuple, is_lit: bool) -> None:
        """Round segment ([CXC-PRIM-LEDBAR] `segment_shape: round`): a filled
        LED disc with the same lit/dim/lens model as the rect, built from
        fill_circle ONLY (R1: decompose to the Canvas contract)."""
        if r <= 0:
            return
        if is_lit:
            canvas.set_fill_color(*color)
            canvas.fill_circle(cx, cy, r)
            core = tuple(min(255, int(c + (255 - c) * _LED_CORE_LIFT))
                         for c in color)
            inset = min(1.8, r * 0.18)
            if r - inset > 0:
                canvas.set_fill_color(*core)
                canvas.fill_circle(cx, cy, r - inset)
        else:
            dim = tuple(max(0, int(c * _LED_DIM_FACTOR)) for c in color)
            canvas.set_fill_color(*dim)
            canvas.fill_circle(cx, cy, r)

    def _led_draw_shape(self, canvas: Canvas, sx: float, sy: float,
                        sw: float, sh: float, *, vertical: bool,
                        radius: float, color: tuple, is_lit: bool) -> None:
        """Draw one LED into its slot (sx,sy,sw,sh) per `segment_shape` /
        `segment_long_axis` ([CXC-PRIM-LEDBAR]).  The along-fill slot extent is
        sh (vertical) / sw (horizontal); every derived shape is CENTERED in the
        slot and never larger than min(sw,sh) on its short axis, so it can never
        overflow the slot (=> never overflows the rect)."""
        if sw <= 0 or sh <= 0:
            return
        shape = self.segment_shape
        if shape == "round":
            # diameter = min slot extent, centered in the slot.
            self._led_draw_circle(canvas, sx + sw / 2, sy + sh / 2,
                                  min(sw, sh) / 2, color, is_lit)
            return
        if shape == "square":
            # side = min slot extent, centered both ways.
            side = min(sw, sh)
            self._led_draw_seg(canvas, sx + (sw - side) / 2,
                               sy + (sh - side) / 2, side, side,
                               min(radius, side * 0.2), color, is_lit)
            return
        # rectangular
        if self.segment_long_axis == "along":
            # long axis runs ALONG the fill direction: cap the across-fill
            # extent to the along-fill extent so adjacent LEDs meet on their
            # SHORT (along-facing) edges — a portrait/square LED, centered.
            if vertical:
                w = min(sw, sh)
                self._led_draw_seg(canvas, sx + (sw - w) / 2, sy, w, sh,
                                   radius, color, is_lit)
            else:
                h = min(sh, sw)
                self._led_draw_seg(canvas, sx, sy + (sh - h) / 2, sw, h,
                                   radius, color, is_lit)
            return
        # across (default / current geometry): fill the whole slot.
        self._led_draw_seg(canvas, sx, sy, sw, sh, radius, color, is_lit)

    def _led_layout(self, strip_extent: float):
        """min_segment_size overflow recovery ([CXC-PRIM-LEDBAR]): given the
        fill-axis strip extent, return (n_render, gap).  Recovery, in order:
        (1) shrink `segment_gap` toward a 1px floor; (2) if still infeasible,
        reduce the rendered count to the largest n' whose per-segment along-fill
        extent still clears `min_segment_size`.  Disabled floor (0) or an
        already-feasible layout returns (self.segments, self.segment_gap)
        unchanged, so the defaults render byte-identical."""
        n = self.segments
        gap = self.segment_gap
        floor = self.min_segment_size
        if floor <= 0 or strip_extent <= 0 or n <= 0:
            return n, gap

        def slot(n_: int, g_: float) -> float:
            return (strip_extent - (n_ + 1) * g_) / n_

        if slot(n, gap) >= floor:
            return n, gap
        # (1) the gap that makes the per-segment extent == floor at full count.
        needed_gap = (strip_extent - n * floor) / (n + 1)
        if needed_gap >= 1.0:
            return n, min(gap, needed_gap)
        # (2) gap floored at 1px; drop the count to the largest n' that fits:
        #   (strip_extent - (n'+1)) / n' >= floor  =>  n' <= (strip_extent-1)/(floor+1)
        n_prime = int((strip_extent - 1.0) / (floor + 1.0))
        n_prime = max(1, min(n, n_prime))
        return n_prime, 1.0

    def _draw_led_bar(self, canvas: Canvas, rect: Rect) -> None:
        vertical = self.orientation != "horizontal"
        reverse = self.reverse

        pad = self.padding
        inner_pad = 5.0
        seg_radius = 3.0
        bezel_radius = 8.0
        label_h = 18.0 if self.label else 0.0

        canvas.save()

        if self.label:
            canvas.set_font("sans-serif", 12.0, "regular")
            canvas.set_fill_color(*_DIM)
            if vertical:
                canvas.draw_text(self.label, rect.x + rect.w / 2,
                                 rect.y + 13, "center")
            else:
                canvas.draw_text(self.label, rect.x + pad, rect.y + 13, "left")

        # near-black module bezel
        bez_x = rect.x + pad
        bez_y = rect.y + label_h + pad
        bez_w = max(1.0, rect.w - 2 * pad)
        bez_h = max(1.0, rect.h - label_h - 2 * pad)
        canvas.set_fill_color(*_LED_BEZEL)
        canvas.fill_round_rect(bez_x, bez_y, bez_w, bez_h, bezel_radius)

        # segment strip interior
        strip_x = bez_x + inner_pad
        strip_y = bez_y + inner_pad
        strip_w = max(1.0, bez_w - 2 * inner_pad)
        strip_h = max(1.0, bez_h - 2 * inner_pad)

        # min_segment_size recovery rescales the count/gap to the FILL-axis
        # extent; lit deciles + palette resample to the rendered count.
        strip_extent = strip_h if vertical else strip_w
        n, gap = self._led_layout(strip_extent)
        lit = self._led_lit(n)
        palette = self._led_palette(n)

        if vertical:
            seg_h = (strip_h - (n + 1) * gap) / n
            seg_w = strip_w
            for i in range(n):
                # slot 0 = bottom-most; reverse puts segment 1 at the TOP
                slot = (n - 1 - i) if reverse else i
                y = strip_y + gap + (n - 1 - slot) * (seg_h + gap)
                self._led_draw_shape(canvas, strip_x, y, seg_w, seg_h,
                                     vertical=True, radius=seg_radius,
                                     color=palette[i], is_lit=lit[i])
        else:
            seg_w = (strip_w - (n + 1) * gap) / n
            seg_h = strip_h
            for i in range(n):
                # slot 0 = left-most; reverse puts segment 1 at the RIGHT
                slot = (n - 1 - i) if reverse else i
                x = strip_x + gap + slot * (seg_w + gap)
                self._led_draw_shape(canvas, x, strip_y, seg_w, seg_h,
                                     vertical=False, radius=seg_radius,
                                     color=palette[i], is_lit=lit[i])

        canvas.restore()

    # -- the console semicircle ([CXC-ICV-GAUGE]) ------------------------------
    #
    # Geometry recipe (spec, verbatim): fraction f in [0,1] maps to the math
    # angle theta = pi - f*pi (f=0 -> left, f=1 -> right, sweeping the top).
    # The backend is y-DOWN and stroke_arc takes backend radians, so the
    # backend angle is -theta (the same sign discipline the dial uses when it
    # negates degrees): arc(f0..f1) = stroke_arc(cx, cy, r, -pi+f0*pi,
    # -pi+f1*pi); the point at f is (cx - r*cos(f*pi), cy - r*sin(f*pi)).

    def _frac(self, v: float) -> float:
        span = (self.max_val - self.min_val) or 1.0
        return max(0.0, min(1.0, (v - self.min_val) / span))

    def _zone_fracs(self):
        """(warn, mid, danger) boundary fractions — 65/80/90 defaults."""
        span = (self.max_val - self.min_val) or 1.0
        warn = self.warning_threshold if self.warning_threshold is not None \
            else self.min_val + 0.65 * span
        danger = self.danger_threshold if self.danger_threshold is not None \
            else self.min_val + 0.90 * span
        mid = self.mid_threshold if self.mid_threshold is not None \
            else self.min_val + 0.80 * span
        return self._frac(warn), self._frac(mid), self._frac(danger), \
            warn, mid, danger

    def _state_color(self, t) -> tuple:
        """ok below warn, warn in the middle band, crit at/above danger —
        the ONLY console widget whose hue follows state ([CXC-ICV-GAUGE])."""
        _, _, _, warn, _, danger = self._zone_fracs()
        if self.value >= danger:
            return t.crit
        if self.value >= warn:
            return t.warn
        return t.ok

    @staticmethod
    def _arc_point(cx: float, cy: float, r: float, f: float):
        return cx - r * math.cos(f * math.pi), cy - r * math.sin(f * math.pi)

    def _draw_semicircle(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        bare = self.chrome == "bare"
        canvas.save()

        # card chrome (the pre-vis .card + .ph header) — skipped when bare (the
        # embedding card draws its own chrome + header).
        if bare:
            body_y = rect.y
        else:
            ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h,
                         ct.RADIUS_CARD, t)
            body_y = ct.draw_card_header(
                canvas, rect.x, rect.y, rect.w,
                self.label or "self / context_level",
                self.ns_tag, t=t)

        pad = ct.PAD_CARD_BODY
        bw = rect.w - 2 * pad
        s = min(bw, 236.0) / 220.0          # pre-vis viewBox 220x132
        cx = rect.x + rect.w / 2
        cy = body_y + 8 + 110.0 * s         # cy=110 in the 220 box
        r_band, band_w = 90.0 * s, 13.0 * s
        r_lead, lead_w = 72.0 * s, 6.0 * s

        f_warn, f_mid, f_danger, warn_v, mid_v, danger_v = self._zone_fracs()
        f_val = self._frac(self.value)
        state = self._state_color(t)

        def seg(f0: float, f1: float, r: float, color: tuple, w: float):
            if f1 <= f0:
                return
            canvas.set_stroke_color(*color)
            canvas.set_stroke_width(w)
            canvas.stroke_arc(cx, cy, r,
                              -math.pi + f0 * math.pi,
                              -math.pi + f1 * math.pi)

        # ZONE BAND — four segments ([CXC-ICV-GAUGE])
        seg(0.0, f_warn, r_band, t.accent_dim, band_w)
        seg(f_warn, f_mid, r_band, t.warn_dim, band_w)
        seg(f_mid, f_danger, r_band, t.warn, band_w)
        seg(f_danger, 1.0, r_band, t.crit, band_w)

        # PROGRESS lead arc — state-colored; hot-pulses only in the crit zone.
        # (round caps approximated with end-cap dots; no cap primitive.)
        canvas.set_opacity(self._lead_opacity)
        seg(0.0, f_val, r_lead, state, lead_w)
        if f_val > 0:
            for f_end in (0.0, f_val):
                ex, ey = self._arc_point(cx, cy, r_lead, f_end)
                canvas.set_fill_color(*state)
                canvas.fill_circle(ex, ey, lead_w / 2)
        canvas.set_opacity(1.0)

        # THRESHOLD TICKS at the zone boundaries, radiating outward
        canvas.set_stroke_width(2.0 * s)
        for f_b, col in ((f_warn, t.warn), (f_mid, t.warn),
                         (f_danger, t.crit)):
            x1, y1 = self._arc_point(cx, cy, r_band + band_w / 2, f_b)
            x2, y2 = self._arc_point(cx, cy, r_band + band_w / 2 + 9.5 * s,
                                     f_b)
            canvas.set_stroke_color(*col)
            canvas.draw_line(x1, y1, x2, y2)

        # VALUE MARKER dot on the band at the value angle (bg-stroked)
        vx, vy = self._arc_point(cx, cy, r_band, f_val)
        canvas.set_fill_color(*state)
        canvas.fill_circle(vx, vy, 5.5 * s)
        canvas.set_stroke_color(*t.bg)
        canvas.set_stroke_width(1.5 * s)
        canvas.stroke_circle(vx, vy, 5.5 * s)

        # 0 / 100 end labels (mono 8px faint, pre-vis coords in the 220 box)
        canvas.set_font(ct.FONT_MONO, max(7.0, 8.0 * s), "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(f"{self.min_val:.0f}",
                         cx + (47 - 110) * s, cy + 17 * s, "left")
        canvas.draw_text(f"{self.max_val:.0f}",
                         cx + (188 - 110) * s, cy + 17 * s, "left")

        # READOUT — big number (state-colored) + % suffix (dim)
        y = cy + 17 * s + 10
        canvas.set_font(ct.FONT_MONO, ct.READOUT_XL * min(1.0, s * 1.05),
                        "bold")
        big = f"{self.value:.1f}"
        mb = canvas.measure_text(big)
        canvas.set_font(ct.FONT_MONO, ct.READOUT_XL_SUFFIX, "regular")
        suffix = self.unit or "%"
        msf = canvas.measure_text(suffix)
        bx = cx - (mb.width + msf.width) / 2
        y_base = y + mb.ascent
        canvas.set_font(ct.FONT_MONO, ct.READOUT_XL * min(1.0, s * 1.05),
                        "bold")
        canvas.set_fill_color(*state)
        canvas.draw_text(big, bx, y_base, "left")
        canvas.set_font(ct.FONT_MONO, ct.READOUT_XL_SUFFIX, "regular")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(suffix, bx + mb.width + 2, y_base, "left")
        y = y_base + 8

        if bare:
            # arc + big readout ONLY ([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION]).
            canvas.restore()
            return

        # tok line — shown when the dict payload carried tokens (additive)
        if self._tokens is not None:
            tok = f"{self._tokens:,.0f}"
            if self._max_tokens:
                tok += f" / {self._max_tokens:,.0f}"
            tok += " tok"
            canvas.set_font(ct.FONT_MONO, ct.TELEMETRY, "regular")
            canvas.set_fill_color(*t.dim)
            m = canvas.measure_text(tok)
            canvas.draw_text(tok, cx, y + m.ascent, "center")
            y += m.ascent + m.descent + 4

        # src line — the perception source; stdin (authoritative) reads ok
        if self._source:
            canvas.set_font(ct.FONT_MONO, 8.6, "regular")
            src = f"SRC: {self._source.upper()}"
            ok_src = "stdin" in self._source.lower()
            canvas.set_fill_color(*(t.ok if ok_src else t.dim))
            m = canvas.measure_text(src)
            canvas.draw_text(src, cx, y + m.ascent, "center")
            y += m.ascent + m.descent + 4

        # THRESHOLD ROW — 65 | 80 | 90 cells under a dashed rule
        y += 6
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        ct.draw_dashed_line(canvas, rect.x + pad, y, rect.x + rect.w - pad)
        y += 8
        cells = [
            (warn_v, t.warn, self.value >= warn_v, False),
            (mid_v, t.warn, self.value >= mid_v, False),
            (danger_v, t.crit, self.value >= danger_v, True),
        ]
        cell_w = bw / 3.0
        row_bottom = y
        for i, (thr, ncol, fired, is_hot) in enumerate(cells):
            ccx = rect.x + pad + cell_w * (i + 0.5)
            canvas.set_font(ct.FONT_MONO, 13.0, "bold")
            canvas.set_fill_color(*ncol)
            m = canvas.measure_text("8")
            canvas.draw_text(f"{thr:.0f}", ccx, y + m.ascent, "center")
            sy = y + m.ascent + m.descent + 2
            canvas.set_font(ct.FONT_MONO, 8.0, "regular")
            if fired and is_hot:
                txt, col = "REACHED · HOT", t.crit
            elif fired:
                txt, col = "CROSSED", t.warn
            else:
                txt, col = "ARMED", t.dim
            canvas.set_fill_color(*col)
            ms = canvas.measure_text(txt)
            canvas.draw_text(txt, ccx, sy + ms.ascent, "center")
            row_bottom = max(row_bottom, sy + ms.ascent + ms.descent)
        y = row_bottom + 9

        # FOOT — only in the crit zone ("⚠" normalizes to "!" in Plex)
        if self.value >= danger_v:
            canvas.set_font(ct.FONT_MONO, 8.6, "regular")
            canvas.set_fill_color(*t.crit)
            foot = f"⚠ {danger_v:.0f} = prepare-to-compact guidance — " \
                   "author's call"
            m = canvas.measure_text(foot)
            canvas.draw_text(foot, cx, y + m.ascent, "center")

        canvas.restore()
