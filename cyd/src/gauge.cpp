/*
 * gauge.cpp — Arc gauge widget implementation
 *
 * Port of Python GaugeWidget draw behavior to idiomatic embedded C++.
 * Arc sweeps from 225 deg (7 o'clock) clockwise to 315 deg (5 o'clock),
 * giving a 270-degree sweep.
 *
 * STYLE MODES: "dial" (default, UNCHANGED) + "led_bar" — the
 * [CXC-PRIM-LEDBAR]/[CXC-PRIM-TIER-PARITY] embedded port of gauge.py
 * _draw_led_bar (same lit thresholds, same 16%-dim hue-true unlit lens,
 * same shapes, same gap-shrink -> count-rescale overflow recovery).
 * Color math + the lit/palette index math run in DOUBLE so they match
 * Python's float semantics exactly; geometry stays float (raster
 * differences are AA/RGB565-class, per the crosscheck delta classes).
 *
 * Reference: composeio/render/widgets/gauge.py
 */

#include "widgets/gauge.h"
#include "canvas.h"

#include <cmath>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <strings.h>   /* strcasecmp — Python lower()s these vocab strings */
#include "esp_log.h"

/* ComposeUX dark theme — matches Python reference */
static constexpr uint8_t kBgR = 0x1E, kBgG = 0x1E, kBgB = 0x2E;
static constexpr uint8_t kTealR = 0x26, kTealG = 0xA6, kTealB = 0x9A;
static constexpr uint8_t kAmberR = 0xFF, kAmberG = 0xA7, kAmberB = 0x26;
static constexpr uint8_t kRedR = 0xEF, kRedG = 0x53, kRedB = 0x50;
static constexpr uint8_t kTextR = 0xE0, kTextG = 0xE0, kTextB = 0xE0;
static constexpr uint8_t kDimR = 0x66, kDimG = 0x66, kDimB = 0x78;
/* bar/fader inset track channel ([CXC-PRIM-TOKENS] _TRACK) */
static constexpr uint8_t kTrackR = 0x14, kTrackG = 0x14, kTrackB = 0x20;

/* -- led_bar tokens ([CXC-PRIM-LEDBAR] — the gauge.py _LED_* constants) */
static constexpr uint8_t kLedBezelR = 0x0A, kLedBezelG = 0x0A,
                         kLedBezelB = 0x0C;
static constexpr double kLedDimFactor = 0.16;   /* unlit = color * 0.16 */
static constexpr double kLedCoreLift = 0.30;    /* lit inner-lens lift  */
/* Default palette (10 deciles): blue idle / green x4 / yellow x3 / red x2 */
static constexpr int kLedDefaultPaletteLen = 10;
static const uint8_t kLedDefaultPalette[kLedDefaultPaletteLen][3] = {
    {0x02, 0x36, 0xFE},
    {0x01, 0xFF, 0x03}, {0x01, 0xFF, 0x03},
    {0x01, 0xFF, 0x03}, {0x01, 0xFF, 0x03},
    {0xFF, 0xEB, 0x01}, {0xFF, 0xEB, 0x01}, {0xFF, 0xEB, 0x01},
    {0xFE, 0x00, 0x02}, {0xFE, 0x00, 0x02},
};

static const char* TAG_LED = "gauge";

/* Gauge arc geometry (degrees) */
static constexpr float kArcStartDeg = 225.0f;  /* 7 o'clock position */
static constexpr float kArcSweepDeg = 270.0f;  /* sweeps clockwise to 5 o'clock */

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

/* -------------------------------------------------------------------
 * Construction
 * ------------------------------------------------------------------- */

GaugeWidget::GaugeWidget(const char* id, float min_val, float max_val,
                         float value, const char* unit, const char* label)
    : Widget(id, "gauge"), _min_val(min_val), _max_val(max_val),
      _value(value), _unit(unit), _label(label) {}

/* -------------------------------------------------------------------
 * led_bar config setters ([CXC-PRIM-LEDBAR] — gauge.py __init__ parity)
 * ------------------------------------------------------------------- */

void GaugeWidget::set_style(const char* s) {
    /* gauge.py dispatches on the EXACT style string (no lower()), with
     * everything unknown falling through to the dial — same here, plus
     * an honest log for the styles this tier has not ported yet. */
    if (s && strcmp(s, "led_bar") == 0) { _style = Style::LedBar; return; }
    if (s && strcmp(s, "bar") == 0) { _style = Style::Bar; return; }
    if (s && s[0] && strcmp(s, "dial") != 0)
        ESP_LOGW(TAG_LED, "'%s': style '%s' not on this tier — dial fallback",
                 widget_id(), s);
    _style = Style::Dial;
}

void GaugeWidget::set_orientation(const char* o) {
    /* Python: .lower() != "horizontal" -> vertical */
    _vertical = !(o && strcasecmp(o, "horizontal") == 0);
}

void GaugeWidget::set_segments(int n) {
    if (n < 1) n = 1;                       /* Python: max(1, int(n)) */
    if (n > kMaxLedSegments) {              /* embedded bound (header) */
        ESP_LOGW(TAG_LED, "'%s': segments %d clamped to %d (embedded bound)",
                 widget_id(), n, kMaxLedSegments);
        n = kMaxLedSegments;
    }
    _segments = n;
}

void GaugeWidget::set_segment_shape(const char* s) {
    if (s && strcasecmp(s, "square") == 0) _seg_shape = LedShape::Square;
    else if (s && strcasecmp(s, "round") == 0) _seg_shape = LedShape::Round;
    else _seg_shape = LedShape::Rectangular;   /* unknown -> rectangular */
}

void GaugeWidget::set_segment_long_axis(const char* s) {
    _long_axis_along = (s && strcasecmp(s, "along") == 0);
}

/* gauge.py _hex_to_rgb, slice-faithful: lstrip('#'), then bytes from
 * h[0:2] / h[2:4] / h[4:6] — a short tail slice parses as a 1-digit hex
 * (Python's int(h[4:6], 16) does exactly that); ANY empty slice or bad
 * digit -> mid-grey, so a bad entry never breaks the screen. */
static void led_hex_to_rgb(const char* s, uint8_t rgb[3]) {
    rgb[0] = rgb[1] = rgb[2] = 0x80;
    if (!s) return;
    while (*s == '#') s++;
    size_t len = strlen(s);
    uint8_t out[3];
    for (int i = 0; i < 3; i++) {
        size_t a = (size_t)i * 2, b = a + 2;
        if (b > len) b = len;
        if (a >= b) return;                    /* empty slice -> grey */
        int v = 0;
        for (size_t p = a; p < b; p++) {
            char c = s[p];
            int d;
            if (c >= '0' && c <= '9') d = c - '0';
            else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
            else return;                       /* bad digit -> grey */
            v = v * 16 + d;
        }
        out[i] = (uint8_t)v;
    }
    rgb[0] = out[0]; rgb[1] = out[1]; rgb[2] = out[2];
}

void GaugeWidget::add_segment_color(const char* hex) {
    if (_seg_color_count >= kMaxLedPalette) {
        ESP_LOGW(TAG_LED, "'%s': segment_colors past %d dropped (repeat-last "
                 "extension covers them)", widget_id(), kMaxLedPalette);
        return;
    }
    led_hex_to_rgb(hex, _seg_colors[_seg_color_count]);
    _seg_color_count++;
}

/* -------------------------------------------------------------------
 * Angle helper — 0-1 fraction to radians along gauge arc
 * ------------------------------------------------------------------- */

float GaugeWidget::lerp_angle(float frac) {
    /* 0 = start (225 deg), 1 = end (225 - 270 = -45 deg, i.e. 315 deg) */
    float deg = kArcStartDeg - frac * kArcSweepDeg;
    return deg * (float)M_PI / 180.0f;
}

/* -------------------------------------------------------------------
 * Measure
 * ------------------------------------------------------------------- */

Size GaugeWidget::measure(const Constraints& constraints) {
    float side = constraints.max_width;
    if (constraints.max_height < side) side = constraints.max_height;
    if (side > 200.0f) side = 200.0f;
    return Size{side, side};
}

/* -------------------------------------------------------------------
 * Draw — style dispatch ([CXC-ICV-GAUGE] style MODES, one widget)
 * ------------------------------------------------------------------- */

void GaugeWidget::draw(Canvas& canvas, const Rect& rect) {
    if (_style == Style::LedBar) {
        draw_led_bar(canvas, rect);
        return;
    }
    if (_style == Style::Bar) {
        draw_bar(canvas, rect);
        return;
    }
    draw_dial(canvas, rect);
}

/* -------------------------------------------------------------------
 * The original 270-degree dial — matches Python reference behavior.
 * Body UNCHANGED by [CXC-PRIM-LEDBAR] (was draw() before the dispatch).
 * ------------------------------------------------------------------- */

void GaugeWidget::draw_dial(Canvas& canvas, const Rect& rect) {
    float cx = rect.x + rect.w / 2.0f;
    float cy = rect.y + rect.h / 2.0f;
    float outer_r = std::min(rect.w, rect.h) / 2.0f - 4.0f;
    float inner_r = outer_r * 0.72f;

    canvas.save();

    /* --- background circle --- */
    canvas.set_fill_color(kBgR, kBgG, kBgB, 200);
    canvas.fill_circle(cx, cy, outer_r);

    /* --- arc track (dim) ---
     * Python uses: start_rad = radians(-_ARC_START_DEG)
     *              end_rad = radians(-_ARC_START_DEG + _ARC_SWEEP_DEG)
     * stroke_arc angles are in radians */
    canvas.set_stroke_color(kDimR, kDimG, kDimB);
    canvas.set_stroke_width(4.0f);
    float start_rad = -kArcStartDeg * (float)M_PI / 180.0f;
    float end_rad = (-kArcStartDeg + kArcSweepDeg) * (float)M_PI / 180.0f;
    canvas.stroke_arc(cx, cy, inner_r, start_rad, end_rad);

    /* --- coloured arc (value) --- */
    float range = _max_val - _min_val;
    float frac = 0.0f;
    if (range > 0.0001f) {
        frac = (_value - _min_val) / range;
        if (frac < 0.0f) frac = 0.0f;
        if (frac > 1.0f) frac = 1.0f;
    }

    if (frac > 0.0f) {
        float value_sweep = kArcSweepDeg * frac;

        /* Pick color based on thresholds */
        uint8_t arc_r, arc_g, arc_b;
        if (_has_danger && _value >= _danger_threshold) {
            arc_r = kRedR; arc_g = kRedG; arc_b = kRedB;
        } else if (_has_warning && _value >= _warning_threshold) {
            arc_r = kAmberR; arc_g = kAmberG; arc_b = kAmberB;
        } else {
            arc_r = kTealR; arc_g = kTealG; arc_b = kTealB;
        }

        canvas.set_stroke_color(arc_r, arc_g, arc_b);
        canvas.set_stroke_width(6.0f);
        float value_end_rad = (-kArcStartDeg + value_sweep) * (float)M_PI / 180.0f;
        canvas.stroke_arc(cx, cy, inner_r, start_rad, value_end_rad);
    }

    /* --- tick marks --- */
    static constexpr int kNumTicks = 10;
    canvas.set_stroke_color(kDimR, kDimG, kDimB);
    canvas.set_stroke_width(1.5f);

    for (int i = 0; i <= kNumTicks; i++) {
        float angle = lerp_angle((float)i / (float)kNumTicks);
        float cos_a = cosf(angle);
        float sin_a = sinf(angle);

        bool is_major = (i % 5 == 0);
        float tick_inner = inner_r - (is_major ? 10.0f : 5.0f);
        float tick_outer = inner_r + 2.0f;

        canvas.draw_line(
            cx + cos_a * tick_inner, cy - sin_a * tick_inner,
            cx + cos_a * tick_outer, cy - sin_a * tick_outer
        );
    }

    /* --- tick labels (at 0%, 50%, 100%) --- */
    canvas.set_fill_color(kTextR, kTextG, kTextB);
    canvas.set_font("sans-serif", outer_r * 0.13f, "regular");

    static const float kLabelPcts[] = {0.0f, 0.5f, 1.0f};
    for (int i = 0; i < 3; i++) {
        float pct = kLabelPcts[i];
        float angle = lerp_angle(pct);
        float cos_a = cosf(angle);
        float sin_a = sinf(angle);
        float lbl_r = inner_r - 18.0f;
        float lx = cx + cos_a * lbl_r;
        float ly = cy - sin_a * lbl_r;

        float val = _min_val + pct * (_max_val - _min_val);
        char buf[16];
        snprintf(buf, sizeof(buf), "%.0f", val);
        canvas.draw_text(buf, lx, ly, "center");
    }

    /* --- needle --- */
    float needle_angle = lerp_angle(frac);
    float cos_n = cosf(needle_angle);
    float sin_n = sinf(needle_angle);
    float needle_len = inner_r * 0.85f;

    canvas.set_stroke_color(kTextR, kTextG, kTextB);
    canvas.set_stroke_width(2.5f);
    canvas.draw_line(cx, cy, cx + cos_n * needle_len, cy - sin_n * needle_len);

    /* Needle hub */
    canvas.set_fill_color(kTextR, kTextG, kTextB);
    canvas.fill_circle(cx, cy, outer_r * 0.06f);

    /* --- value text --- */
    char value_buf[32];
    if (_unit && _unit[0]) {
        snprintf(value_buf, sizeof(value_buf), "%.1f %s", _value, _unit);
    } else {
        snprintf(value_buf, sizeof(value_buf), "%.1f", _value);
    }
    canvas.set_font("sans-serif", outer_r * 0.22f, "bold");
    canvas.set_fill_color(kTextR, kTextG, kTextB);
    canvas.draw_text(value_buf, cx, cy + outer_r * 0.58f, "center");

    /* --- label text --- */
    if (_label && _label[0]) {
        canvas.set_font("sans-serif", outer_r * 0.13f, "regular");
        canvas.set_fill_color(kDimR, kDimG, kDimB);
        canvas.draw_text(_label, cx, cy + outer_r * 0.78f, "center");
    }

    canvas.restore();
}

/* -------------------------------------------------------------------
 * led_bar — the segmented LED level meter ([CXC-PRIM-LEDBAR]).
 *
 * Direct port of gauge.py _draw_led_bar + its helpers (_led_lit,
 * _led_palette, _led_layout, _led_draw_shape, _led_draw_circle,
 * _led_draw_seg).  Built from fill_round_rect / fill_circle ONLY —
 * the Canvas-contract property that made this a mechanical back-end
 * pass ([CXC-PRIM-TIER-PARITY]).
 * ------------------------------------------------------------------- */

/* gauge.py _frac in DOUBLE (Python floats are doubles): span of 0 -> 1.0
 * (the exact `or 1.0` semantics — a negative span stays negative). */
double GaugeWidget::led_frac_of(float value) const {
    double span = (double)_max_val - (double)_min_val;
    if (span == 0.0) span = 1.0;
    double f = ((double)value - (double)_min_val) / span;
    if (f < 0.0) f = 0.0;
    if (f > 1.0) f = 1.0;
    return f;
}

double GaugeWidget::led_frac() const { return led_frac_of(_value); }

/* gauge.py _led_lit: segment i (0-based) is lit iff frac >= (i+1)/n
 * minus an inclusive epsilon.  Double math == Python bit-behavior. */
bool GaugeWidget::led_lit(int i, int n, double frac) {
    return frac >= ((double)(i + 1) / (double)n) - 1e-9;
}

/* gauge.py _led_palette collapsed to a per-index lookup.  full[k] =
 * src[min(k, len-1)] (repeat-last extension + [:full_n] truncation both
 * reduce to the clamp); the min_segment_size RESCALE path (n < full_n)
 * resamples k = round(i*(full_n-1)/(n-1)) with Python's banker's
 * rounding — nearbyint under the default FE_TONEAREST is exactly
 * round-half-to-even. */
void GaugeWidget::led_palette_color(int i, int n, uint8_t rgb[3]) const {
    const int full_n = _segments;
    int k = i;
    if (n < full_n) {
        k = (n <= 1) ? 0
            : (int)nearbyint((double)i * (double)(full_n - 1) /
                             (double)(n - 1));
    }
    if (_seg_color_count > 0) {
        int idx = (k < _seg_color_count) ? k : _seg_color_count - 1;
        rgb[0] = _seg_colors[idx][0];
        rgb[1] = _seg_colors[idx][1];
        rgb[2] = _seg_colors[idx][2];
    } else {
        int idx = (k < kLedDefaultPaletteLen) ? k : kLedDefaultPaletteLen - 1;
        rgb[0] = kLedDefaultPalette[idx][0];
        rgb[1] = kLedDefaultPalette[idx][1];
        rgb[2] = kLedDefaultPalette[idx][2];
    }
}

/* gauge.py _led_layout: min_segment_size overflow recovery.  Given the
 * fill-axis strip extent, yield (n_render, gap): (1) shrink segment_gap
 * toward a 1px floor; (2) if still infeasible, reduce the rendered
 * count to the largest n' whose per-segment extent clears the floor.
 * Disabled floor (0) or a feasible layout returns the config unchanged
 * (defaults render byte-identical). */
void GaugeWidget::led_layout(float strip_extent, int& n_out,
                             float& gap_out) const {
    const int n = _segments;
    const float gap = _segment_gap;
    const float floor_px = _min_segment_size;
    n_out = n;
    gap_out = gap;
    if (floor_px <= 0.0f || strip_extent <= 0.0f || n <= 0) return;

    auto slot = [&](int n_, float g_) -> float {
        return (strip_extent - (float)(n_ + 1) * g_) / (float)n_;
    };
    if (slot(n, gap) >= floor_px) return;

    /* (1) the gap that makes the per-segment extent == floor at count n */
    float needed_gap = (strip_extent - (float)n * floor_px) / (float)(n + 1);
    if (needed_gap >= 1.0f) {
        gap_out = (gap < needed_gap) ? gap : needed_gap;
        return;
    }
    /* (2) gap floored at 1px; largest n' that fits (int() truncation) */
    int n_prime = (int)((strip_extent - 1.0f) / (floor_px + 1.0f));
    if (n_prime < 1) n_prime = 1;
    if (n_prime > n) n_prime = n;
    n_out = n_prime;
    gap_out = 1.0f;
}

/* gauge.py _led_draw_seg: lit = palette color + a brighter inner lens
 * core (hue-true rim); unlit = the SAME color at 16% luminance.  Color
 * math in double with Python's int() truncation. */
void GaugeWidget::led_draw_seg(Canvas& canvas, float x, float y, float w,
                               float h, float r, const uint8_t rgb[3],
                               bool lit) const {
    if (w <= 0.0f || h <= 0.0f) return;
    if (lit) {
        canvas.set_fill_color(rgb[0], rgb[1], rgb[2]);
        canvas.fill_round_rect(x, y, w, h, r);
        uint8_t core[3];
        for (int j = 0; j < 3; j++) {
            int v = (int)((double)rgb[j] +
                          (255.0 - (double)rgb[j]) * kLedCoreLift);
            core[j] = (uint8_t)(v > 255 ? 255 : v);
        }
        float inset = std::min(1.8f, std::min(w * 0.18f, h * 0.18f));
        if (w - 2.0f * inset > 0.0f && h - 2.0f * inset > 0.0f) {
            canvas.set_fill_color(core[0], core[1], core[2]);
            canvas.fill_round_rect(x + inset, y + inset,
                                   w - 2.0f * inset, h - 2.0f * inset,
                                   std::max(1.0f, r - 1.0f));
        }
    } else {
        uint8_t dim[3];
        for (int j = 0; j < 3; j++) {
            int v = (int)((double)rgb[j] * kLedDimFactor);
            dim[j] = (uint8_t)(v < 0 ? 0 : v);
        }
        canvas.set_fill_color(dim[0], dim[1], dim[2]);
        canvas.fill_round_rect(x, y, w, h, r);
    }
}

/* gauge.py _led_draw_circle: segment_shape "round" — same lit/dim/lens
 * model as the rect, from fill_circle ONLY. */
void GaugeWidget::led_draw_circle(Canvas& canvas, float cx, float cy,
                                  float r, const uint8_t rgb[3],
                                  bool lit) const {
    if (r <= 0.0f) return;
    if (lit) {
        canvas.set_fill_color(rgb[0], rgb[1], rgb[2]);
        canvas.fill_circle(cx, cy, r);
        uint8_t core[3];
        for (int j = 0; j < 3; j++) {
            int v = (int)((double)rgb[j] +
                          (255.0 - (double)rgb[j]) * kLedCoreLift);
            core[j] = (uint8_t)(v > 255 ? 255 : v);
        }
        float inset = std::min(1.8f, r * 0.18f);
        if (r - inset > 0.0f) {
            canvas.set_fill_color(core[0], core[1], core[2]);
            canvas.fill_circle(cx, cy, r - inset);
        }
    } else {
        uint8_t dim[3];
        for (int j = 0; j < 3; j++) {
            int v = (int)((double)rgb[j] * kLedDimFactor);
            dim[j] = (uint8_t)(v < 0 ? 0 : v);
        }
        canvas.set_fill_color(dim[0], dim[1], dim[2]);
        canvas.fill_circle(cx, cy, r);
    }
}

/* gauge.py _led_draw_shape: one LED into its slot per segment_shape /
 * segment_long_axis.  Every derived shape is centered and never larger
 * than min(sw, sh) on its short axis — it can never overflow the slot. */
void GaugeWidget::led_draw_shape(Canvas& canvas, float sx, float sy,
                                 float sw, float sh, bool vertical,
                                 float radius, const uint8_t rgb[3],
                                 bool lit) const {
    if (sw <= 0.0f || sh <= 0.0f) return;
    if (_seg_shape == LedShape::Round) {
        /* diameter = min slot extent, centered in the slot */
        led_draw_circle(canvas, sx + sw / 2.0f, sy + sh / 2.0f,
                        std::min(sw, sh) / 2.0f, rgb, lit);
        return;
    }
    if (_seg_shape == LedShape::Square) {
        /* side = min slot extent, centered both ways */
        float side = std::min(sw, sh);
        led_draw_seg(canvas, sx + (sw - side) / 2.0f,
                     sy + (sh - side) / 2.0f, side, side,
                     std::min(radius, side * 0.2f), rgb, lit);
        return;
    }
    /* rectangular */
    if (_long_axis_along) {
        /* long axis ALONG the fill direction: cap the across-fill extent
         * so adjacent LEDs meet on their SHORT edges (centered). */
        if (vertical) {
            float w = std::min(sw, sh);
            led_draw_seg(canvas, sx + (sw - w) / 2.0f, sy, w, sh,
                         radius, rgb, lit);
        } else {
            float h = std::min(sh, sw);
            led_draw_seg(canvas, sx, sy + (sh - h) / 2.0f, sw, h,
                         radius, rgb, lit);
        }
        return;
    }
    /* across (default / baseline geometry): fill the whole slot */
    led_draw_seg(canvas, sx, sy, sw, sh, radius, rgb, lit);
}

/* THE ONE led_bar strip geometry ([CXC-ER-DAMAGE]).  draw_led_bar and
 * damage_rows both go through here; nobody re-derives a slot origin. */
void GaugeWidget::led_geometry(const Rect& rect, LedGeom& g) const {
    const bool has_label = (_label && _label[0]);
    const float pad = _padding;
    const float inner_pad = 5.0f;
    const float label_h = has_label ? 18.0f : 0.0f;

    float bez_x = rect.x + pad;
    float bez_y = rect.y + label_h + pad;
    float bez_w = std::max(1.0f, rect.w - 2.0f * pad);
    float bez_h = std::max(1.0f, rect.h - label_h - 2.0f * pad);

    g.vertical = _vertical;
    g.strip_x = bez_x + inner_pad;
    g.strip_y = bez_y + inner_pad;
    g.strip_w = std::max(1.0f, bez_w - 2.0f * inner_pad);
    g.strip_h = std::max(1.0f, bez_h - 2.0f * inner_pad);

    /* min_segment_size recovery rescales count/gap to the FILL-axis
     * extent; lit deciles + palette resample to the rendered count. */
    led_layout(g.vertical ? g.strip_h : g.strip_w, g.n, g.gap);

    if (g.vertical) {
        g.seg_h = (g.strip_h - (float)(g.n + 1) * g.gap) / (float)g.n;
        g.seg_w = g.strip_w;
    } else {
        g.seg_w = (g.strip_w - (float)(g.n + 1) * g.gap) / (float)g.n;
        g.seg_h = g.strip_h;
    }
}

/* slot 0 = bottom-most; reverse puts segment 1 at the TOP */
float GaugeWidget::led_slot_y(const LedGeom& g, int i) const {
    int slot = _reverse ? (g.n - 1 - i) : i;
    return g.strip_y + g.gap + (float)(g.n - 1 - slot) * (g.seg_h + g.gap);
}

/* slot 0 = left-most; reverse puts segment 1 at the RIGHT */
float GaugeWidget::led_slot_x(const LedGeom& g, int i) const {
    int slot = _reverse ? (g.n - 1 - i) : i;
    return g.strip_x + g.gap + (float)slot * (g.seg_w + g.gap);
}

void GaugeWidget::draw_led_bar(Canvas& canvas, const Rect& rect) {
    const bool vertical = _vertical;
    const bool has_label = (_label && _label[0]);

    const float pad = _padding;
    const float seg_radius = 3.0f;
    const float bezel_radius = 8.0f;
    const float label_h = has_label ? 18.0f : 0.0f;

    canvas.save();

    if (has_label) {
        canvas.set_font("sans-serif", 12.0f, "regular");
        canvas.set_fill_color(kDimR, kDimG, kDimB);
        if (vertical)
            canvas.draw_text(_label, rect.x + rect.w / 2.0f,
                             rect.y + 13.0f, "center");
        else
            canvas.draw_text(_label, rect.x + pad, rect.y + 13.0f, "left");
    }

    /* near-black module bezel */
    float bez_x = rect.x + pad;
    float bez_y = rect.y + label_h + pad;
    float bez_w = std::max(1.0f, rect.w - 2.0f * pad);
    float bez_h = std::max(1.0f, rect.h - label_h - 2.0f * pad);
    canvas.set_fill_color(kLedBezelR, kLedBezelG, kLedBezelB);
    canvas.fill_round_rect(bez_x, bez_y, bez_w, bez_h, bezel_radius);

    LedGeom g;
    led_geometry(rect, g);
    const int n = g.n;

    const double frac = led_frac();
    uint8_t rgb[3];

    if (vertical) {
        for (int i = 0; i < n; i++) {
            float y = led_slot_y(g, i);
            bool lit = led_lit(i, n, frac);
            led_palette_color(i, n, rgb);
            led_draw_shape(canvas, g.strip_x, y, g.seg_w, g.seg_h, true,
                           seg_radius, rgb, lit);
        }
    } else {
        for (int i = 0; i < n; i++) {
            float x = led_slot_x(g, i);
            bool lit = led_lit(i, n, frac);
            led_palette_color(i, n, rgb);
            led_draw_shape(canvas, x, g.strip_y, g.seg_w, g.seg_h, false,
                           seg_radius, rgb, lit);
        }
    }

    canvas.restore();
}

/* -------------------------------------------------------------------
 * Sub-widget damage ([CXC-ER-DAMAGE]) — led_bar only.
 *
 * A value change on a led_bar relights SEGMENTS and touches nothing
 * else: the bezel, the label and the unlit lenses of every unchanged
 * segment are byte-identical frame to frame.  So the damage is the
 * union of the SLOTS whose lit-ness flipped — typically one slot of
 * ten (10.6 of 176 rows on the 480x320 thrust panel).
 *
 * SUPERSET DISCIPLINE: the returned span is the SLOT rect, not the
 * drawn shape.  led_draw_shape centres every derived shape inside its
 * slot and can never overflow it (square/round/along all shrink), so
 * the slot is a superset by construction — plus kDamagePadRows of AA
 * slack at each edge, because fill_round_rect / fill_circle write
 * anti-aliased coverage on the boundary rows.
 * ------------------------------------------------------------------- */

/* Anti-aliasing slack, in scanlines, added at each edge of a computed
 * damage span.  fill_round_rect touches floor(y) and ceil(y+h)-1; two
 * rows is double that and costs ~0.9 ms of SPI on a 480-wide panel. */
static constexpr float kDamagePadRows = 2.0f;

void GaugeWidget::damage_snapshot(const char* attr, WidgetDamage& out) const {
    out.valid = false;
    if (_style != Style::LedBar) return;      /* dial/bar not modelled  */
    if (!attr || strcmp(attr, "value") != 0) return;
    out.valid = true;
    out.num = _value;
}

bool GaugeWidget::damage_rows(const char* attr, const WidgetDamage& before,
                              const Rect& rect, Canvas& metrics,
                              DamageSpan& out) const {
    (void)metrics;
    if (!before.valid) return false;
    if (_style != Style::LedBar) return false;
    if (!attr || strcmp(attr, "value") != 0) return false;

    LedGeom g;
    led_geometry(rect, g);
    if (g.n <= 0) return false;

    const double f_old = led_frac_of(before.num);
    const double f_new = led_frac_of(_value);

    bool any = false;
    float lo = 0.0f, hi = 0.0f;
    for (int i = 0; i < g.n; i++) {
        if (led_lit(i, g.n, f_old) == led_lit(i, g.n, f_new)) continue;
        float sy, sh;
        if (g.vertical) {
            sy = led_slot_y(g, i);
            sh = g.seg_h;
        } else {
            /* horizontal: a segment spans the whole strip height, so
             * the row span of ANY changed segment is the strip. */
            sy = g.strip_y;
            sh = g.strip_h;
        }
        if (!any) { lo = sy; hi = sy + sh; any = true; }
        else {
            if (sy < lo) lo = sy;
            if (sy + sh > hi) hi = sy + sh;
        }
    }

    if (!any) {
        /* The value moved but no segment crossed its threshold — the
         * glass is already correct.  Repaint NOTHING. */
        out.y0 = 1.0f;
        out.y1 = 0.0f;
        return true;
    }

    out.y0 = lo - kDamagePadRows;
    out.y1 = hi + kDamagePadRows;

    /* PAINT DIRECTION ([CXC-ER-DAMAGE] paint-order).  A multi-band
     * repaint's wavefront is visible; point it the way the change grew.
     *
     * A vertical bar with slot 0 at the BOTTOM fills upward as the value
     * RISES, so a rising value must paint from the bottom row upward or
     * the segments appear to light in reverse.  A falling value
     * extinguishes from the top down, which is the ascending default.
     * `reverse:` puts segment 1 at the top and flips both.  Horizontal
     * bars grow along X, where the row walk cannot express direction at
     * all — leave them ascending. */
    if (g.vertical) {
        const bool rising = f_new > f_old;
        out.bottom_up = _reverse ? !rising : rising;
    }

#ifdef CXC_DAMAGE_INJECT_FAULT
    /* THE NEGATIVE CONTROL FOR THE DAMAGE AUDIT — never defined in any
     * env; define it in the board's target_compile_definitions for ONE
     * flash when you need to re-prove the instrument.
     *
     * A row-CRC audit that reports 0 stale rows is worth exactly as
     * much as its ability to report a non-zero number.  This deliberately
     * eats 6 rows off the bottom of every gauge damage span — a textbook
     * under-invalidation, the same shape a geometry drift between
     * draw_led_bar and damage_rows would produce.  With it compiled in,
     * the boot self-test MUST report FAIL with stale rows inside the
     * segment strip.  If it still reports PASS, the audit is not an
     * instrument and nothing it says about this change is evidence. */
    out.y1 -= 6.0f;
    if (out.y1 < out.y0) out.y1 = out.y0;
#endif

    return true;
}

/* -------------------------------------------------------------------
 * bar / fader style ([CXC-PRIM-CATALOG] — direct port of gauge.py
 * _draw_bar + its _frac / _threshold_color / _readout_text helpers).
 *
 * An inset track the value fills as a fraction of [min, max]; the fill
 * grows from the origin edge (vertical -> bottom-up; horizontal ->
 * left).  A bright level-cap line marks the fill edge; a faint scale +
 * min/max labels frame it; a big raw-value readout sits opposite the
 * label.  The fill warms teal->amber->red on the thresholds.  Built
 * from fill_round_rect + clip_round_rect + a linear gradient (contract
 * primitives — R1 decompose-to-the-contract).
 * ------------------------------------------------------------------- */

/* gauge.py _frac (DOUBLE == Python float): span 0 -> 1.0, clamp 0..1. */
float GaugeWidget::bar_frac() const {
    double span = (double)_max_val - (double)_min_val;
    if (span == 0.0) span = 1.0;
    double f = ((double)_value - (double)_min_val) / span;
    if (f < 0.0) f = 0.0;
    if (f > 1.0) f = 1.0;
    return (float)f;
}

/* gauge.py _threshold_color: red past danger / amber past warn / teal. */
void GaugeWidget::bar_threshold_color(uint8_t rgb[3]) const {
    if (_has_danger && _value >= _danger_threshold) {
        rgb[0] = kRedR; rgb[1] = kRedG; rgb[2] = kRedB;
    } else if (_has_warning && _value >= _warning_threshold) {
        rgb[0] = kAmberR; rgb[1] = kAmberG; rgb[2] = kAmberB;
    } else {
        rgb[0] = kTealR; rgb[1] = kTealG; rgb[2] = kTealB;
    }
}

/* gauge.py _readout_text: integer when whole, else one decimal place. */
void GaugeWidget::bar_readout_text(char* buf, size_t cap) const {
    float v = _value;
    if (v == floorf(v) && fabsf(v) < 1e15f)
        snprintf(buf, cap, "%d", (int)v);
    else
        snprintf(buf, cap, "%.1f", v);
}

void GaugeWidget::draw_bar(Canvas& canvas, const Rect& rect) {
    const bool vertical = _vertical;
    const float frac = bar_frac();
    uint8_t color[3];
    bar_threshold_color(color);
    uint8_t bright[3] = {
        (uint8_t)std::min((int)color[0] + 46, 255),
        (uint8_t)std::min((int)color[1] + 46, 255),
        (uint8_t)std::min((int)color[2] + 46, 255),
    };
    const float radius = 6.0f;
    const float pad = 10.0f;
    const bool has_label = (_label && _label[0]);

    canvas.save();

    if (vertical) {
        float label_h = has_label ? 20.0f : 0.0f;
        float readout_h = 30.0f;
        float track_top = rect.y + label_h + pad;
        float track_bottom = rect.y + rect.h - readout_h - pad;
        float track_h = std::max(1.0f, track_bottom - track_top);
        float track_w = std::min(std::max(rect.w - 2.0f * pad, 8.0f), 64.0f);
        float track_x = rect.x + (rect.w - track_w) / 2.0f;
        float track_y = track_top;

        /* inset track channel */
        canvas.set_fill_color(kTrackR, kTrackG, kTrackB);
        canvas.fill_round_rect(track_x, track_y, track_w, track_h, radius);

        /* fill bottom-up, clipped to the rounded channel (rounded bottom
         * corners, flat level top) */
        if (frac > 0.0f) {
            float fill_h = track_h * frac;
            float fill_y = track_bottom - fill_h;
            canvas.save();
            canvas.clip_round_rect(track_x, track_y, track_w, track_h, radius);
            GradientStop stops[2] = {
                GradientStop(0.0f, bright[0], bright[1], bright[2], 255),
                GradientStop(1.0f, color[0], color[1], color[2], 255),
            };
            canvas.set_linear_gradient(track_x, fill_y, track_x, track_bottom,
                                       stops, 2);
            canvas.fill_rect(track_x, fill_y, track_w, fill_h);
            canvas.restore();
            /* bright level-cap line */
            canvas.set_stroke_color(bright[0], bright[1], bright[2]);
            canvas.set_stroke_width(2.0f);
            canvas.draw_line(track_x, fill_y, track_x + track_w, fill_y);
        }

        canvas.set_stroke_color(kDimR, kDimG, kDimB);
        canvas.set_stroke_width(1.0f);
        canvas.stroke_round_rect(track_x, track_y, track_w, track_h, radius);

        /* quarter-mark scale ticks along the right edge */
        for (int i = 0; i < 5; i++) {
            float ty = track_bottom - ((float)i / 4.0f) * track_h;
            canvas.draw_line(track_x + track_w, ty,
                             track_x + track_w + 4.0f, ty);
        }

        /* min/max end labels (mono, faint) */
        char nbuf[16];
        canvas.set_font("monospace", 10.0f, "regular");
        canvas.set_fill_color(kDimR, kDimG, kDimB);
        snprintf(nbuf, sizeof(nbuf), "%.0f", _max_val);
        canvas.draw_text(nbuf, track_x + track_w + 7.0f, track_y + 8.0f, "left");
        snprintf(nbuf, sizeof(nbuf), "%.0f", _min_val);
        canvas.draw_text(nbuf, track_x + track_w + 7.0f, track_bottom, "left");

        /* big raw-value readout (state-warmed) */
        char rbuf[32];
        bar_readout_text(rbuf, sizeof(rbuf));
        canvas.set_font("monospace", 22.0f, "bold");
        canvas.set_fill_color(color[0], color[1], color[2]);
        canvas.draw_text(rbuf, rect.x + rect.w / 2.0f,
                         track_bottom + 26.0f, "center");

        if (has_label) {
            canvas.set_font("sans-serif", 12.0f, "regular");
            canvas.set_fill_color(kDimR, kDimG, kDimB);
            canvas.draw_text(_label, rect.x + rect.w / 2.0f,
                             rect.y + 14.0f, "center");
        }
    } else {
        /* horizontal: fills left->right */
        float label_h = has_label ? 18.0f : 0.0f;
        float readout_h = 26.0f;
        float track_x = rect.x + pad;
        float track_right = rect.x + rect.w - pad;
        float track_w = std::max(1.0f, track_right - track_x);
        float track_h = std::min(
            std::max(rect.h - label_h - readout_h - 2.0f * pad, 6.0f), 22.0f);
        float track_y = rect.y + label_h + pad;

        canvas.set_fill_color(kTrackR, kTrackG, kTrackB);
        canvas.fill_round_rect(track_x, track_y, track_w, track_h, radius);

        if (frac > 0.0f) {
            float fill_w = track_w * frac;
            canvas.save();
            canvas.clip_round_rect(track_x, track_y, track_w, track_h, radius);
            GradientStop stops[2] = {
                GradientStop(0.0f, color[0], color[1], color[2], 255),
                GradientStop(1.0f, bright[0], bright[1], bright[2], 255),
            };
            canvas.set_linear_gradient(track_x, track_y,
                                       track_x + fill_w, track_y, stops, 2);
            canvas.fill_rect(track_x, track_y, fill_w, track_h);
            canvas.restore();
            canvas.set_stroke_color(bright[0], bright[1], bright[2]);
            canvas.set_stroke_width(2.0f);
            canvas.draw_line(track_x + fill_w, track_y,
                             track_x + fill_w, track_y + track_h);
        }

        canvas.set_stroke_color(kDimR, kDimG, kDimB);
        canvas.set_stroke_width(1.0f);
        canvas.stroke_round_rect(track_x, track_y, track_w, track_h, radius);

        if (has_label) {
            canvas.set_font("sans-serif", 12.0f, "regular");
            canvas.set_fill_color(kDimR, kDimG, kDimB);
            canvas.draw_text(_label, track_x, rect.y + 13.0f, "left");
        }

        char rbuf[32];
        bar_readout_text(rbuf, sizeof(rbuf));
        canvas.set_font("monospace", 20.0f, "bold");
        canvas.set_fill_color(color[0], color[1], color[2]);
        canvas.draw_text(rbuf, track_right, track_y + track_h + 22.0f, "right");
    }

    canvas.restore();
}

/* -------------------------------------------------------------------
 * on_tap [CYD-TOUCH-WIDGET-TAP]
 *
 * Map tap X position within the widget rect to a value between
 * min and max. Tap left side = lower value, right side = higher.
 * The x parameter is in local coordinates (relative to widget origin).
 * ------------------------------------------------------------------- */

void GaugeWidget::on_tap(float x, float y) {
    /* We need the widget width to map x. The measure() call used
     * max_width, but we don't have the rect here. Use 115.0f as the
     * typical gauge width from the dashboard layout, but since on_tap
     * receives local coords and we know the gauge is symmetric, we
     * can infer width from the last draw. For robustness, use the
     * measure result at a reasonable constraint. */
    (void)y;  /* Y not used for gauge tap */

    /* Assume widget width from the constraints used at layout time.
     * The caller provides local_x in [0, rect.w]. We use the Constraints
     * approach: measure to get preferred size, then use that width. */
    Constraints c = { 200.0f, 200.0f };
    Size s = measure(c);
    float width = s.width;

    /* Clamp x to valid range */
    float frac = x / width;
    if (frac < 0.0f) frac = 0.0f;
    if (frac > 1.0f) frac = 1.0f;

    float new_value = _min_val + frac * (_max_val - _min_val);
    set_value(new_value);

    ESP_LOGI("gauge_tap", "'%s' tapped at x=%.1f -> value=%.1f",
             widget_id(), x, new_value);
}
