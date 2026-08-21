/*
 * gauge.h — Arc gauge widget for ComposeUX embedded rendering
 *
 * Port of the Python GaugeWidget from composeio/render/widgets/gauge.py.
 * Arc sweep from 225 deg (7 o'clock) clockwise to 315 deg (5 o'clock).
 * Features: arc track, value arc, tick marks, needle, value/title labels,
 * warning/danger color zones.
 *
 * STYLE MODES ([CXC-ICV-GAUGE] "generalize, don't fork"):
 *   "dial"    (default) the original 270-degree needle dial — UNCHANGED.
 *   "led_bar" the LM3914-style segmented LED level meter
 *             ([CXC-PRIM-LEDBAR], the [CXC-PRIM-TIER-PARITY] embedded
 *             port of gauge.py _draw_led_bar) — built from
 *             fill_round_rect/fill_circle ONLY, so the band and
 *             framebuffer canvases both render it.
 *   "bar"     the linear FILL / FADER gauge ([CXC-PRIM-CATALOG] port of
 *             gauge.py _draw_bar): an inset track the value fills as a
 *             fraction (vertical bottom->top or horizontal left->right),
 *             a bright level-cap line, a min/max scale, and a big raw-
 *             value readout.  Fill warms teal->amber->red on threshold.
 *             Built from fill_round_rect + clip_round_rect + a linear
 *             gradient + draw_line/draw_text — the rounded fill bottom
 *             uses the contract clip_round_rect (ESP32Canvas override).
 *   "semicircle" is NOT on this tier (needs the console_theme card/
 *             hotpulse substrate — honest dial fallback, serial-logged).
 *
 * Uses ONLY the abstract Canvas contract — no platform imports.
 */

#ifndef WIDGETS_GAUGE_H
#define WIDGETS_GAUGE_H

#include "widget.h"
#include <cstdint>

class GaugeWidget : public Widget {
public:
    /* Embedded bounds ([CXC-PRIM-LEDBAR] port): the palette source list
     * is stored in a fixed array (entries past the cap repeat the last
     * stored color, serial-logged); the segment count is clamped (the
     * Python tier is unbounded, but a runaway N is a per-band draw-loop
     * hazard on this tier).  Both are far above any authored config. */
    static constexpr int kMaxLedPalette = 32;
    static constexpr int kMaxLedSegments = 256;

    GaugeWidget(const char* id,
                float min_val = 0.0f,
                float max_val = 100.0f,
                float value = 0.0f,
                const char* unit = "",
                const char* label = "");

    /* Setters */
    void set_value(float v) { _value = v; }
    void set_min(float v) { _min_val = v; }
    void set_max(float v) { _max_val = v; }
    void set_unit(const char* u) { _unit = u; }
    void set_label(const char* l) { _label = l; }
    void set_warning_threshold(float v) { _warning_threshold = v; _has_warning = true; }
    void set_danger_threshold(float v) { _danger_threshold = v; _has_danger = true; }

    /* -- led_bar style mode ([CXC-PRIM-LEDBAR]) ---------------------- */
    /* Vocabulary + validation mirror gauge.py __init__ exactly:
     * unknown shape -> rectangular, unknown long-axis -> across,
     * negative gap/padding/min-size -> 0, segments clamped >= 1. */
    void set_style(const char* s);              /* "dial" | "led_bar"   */
    void set_orientation(const char* o);        /* vertical|horizontal  */
    void set_reverse(bool r) { _reverse = r; }
    void set_segments(int n);
    void add_segment_color(const char* hex);    /* '#RRGGBB' per entry  */
    void set_segment_shape(const char* s);      /* rectangular|square|round */
    void set_segment_long_axis(const char* s);  /* across|along         */
    void set_segment_gap(float g) { _segment_gap = (g >= 0.0f) ? g : 0.0f; }
    void set_padding(float p) { _padding = (p >= 0.0f) ? p : 0.0f; }
    void set_min_segment_size(float m)
        { _min_segment_size = (m >= 0.0f) ? m : 0.0f; }

    /* Getters */
    float value() const { return _value; }

    /* Getters for on_tap mapping */
    float min_val() const { return _min_val; }
    float max_val() const { return _max_val; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

    /* -- Sub-widget damage ([CXC-ER-DAMAGE]) -----------------------
     * led_bar ONLY: a value change relights SEGMENTS, and a segment is
     * ~10 rows of a 176-row widget.  The dial and bar styles move a
     * needle / a readout / a cap line and are NOT modelled here — they
     * fall through to "I don't know" and repaint the whole rect, which
     * is the pre-damage behaviour. */
    void damage_snapshot(const char* attr, WidgetDamage& out) const override;
    bool damage_rows(const char* attr, const WidgetDamage& before,
                     const Rect& rect, Canvas& metrics,
                     DamageSpan& out) const override;

    /* -- Touch interaction [CYD-TOUCH-WIDGET-TAP] ------------------- */
    void on_tap(float x, float y) override;

private:
    enum class Style : uint8_t { Dial, LedBar, Bar };
    enum class LedShape : uint8_t { Rectangular, Square, Round };

    float _min_val;
    float _max_val;
    float _value;
    const char* _unit;
    const char* _label;

    float _warning_threshold = 0.0f;
    float _danger_threshold = 0.0f;
    bool _has_warning = false;
    bool _has_danger = false;

    /* -- led_bar config ([CXC-PRIM-LEDBAR]; defaults == gauge.py) ---- */
    Style _style = Style::Dial;
    bool _vertical = true;              /* orientation != "horizontal"  */
    bool _reverse = false;
    int _segments = 10;
    uint8_t _seg_colors[kMaxLedPalette][3];
    int _seg_color_count = 0;           /* 0 -> the default engine palette */
    LedShape _seg_shape = LedShape::Rectangular;
    bool _long_axis_along = false;      /* segment_long_axis == "along" */
    float _segment_gap = 4.0f;
    float _padding = 8.0f;
    float _min_segment_size = 0.0f;     /* 0 = disabled (backward-compat) */

    /* Convert a 0-1 fraction to radians along the gauge arc */
    static float lerp_angle(float frac);

    /* The original 270-degree dial (draw() body pre-[CXC-PRIM-LEDBAR],
     * moved verbatim behind the style dispatch — rendering UNCHANGED). */
    void draw_dial(Canvas& canvas, const Rect& rect);

    /* -- led_bar internals (ports of the gauge.py _led_* helpers) ---- */
    /* ONE strip geometry, shared by draw_led_bar and damage_rows.
     * A second copy would fork the two, and a damage span computed
     * from geometry that has drifted from the draw is precisely an
     * under-invalidation ([CXC-ER-DAMAGE]). */
    struct LedGeom {
        bool vertical;
        int n;                  /* RENDERED segment count (led_layout)  */
        float gap;
        float strip_x, strip_y, strip_w, strip_h;
        float seg_w, seg_h;     /* one slot's extent                    */
    };
    void led_geometry(const Rect& rect, LedGeom& g) const;
    /* Slot origin of segment i (0-based, honouring `reverse:`). */
    float led_slot_y(const LedGeom& g, int i) const;
    float led_slot_x(const LedGeom& g, int i) const;
    /* gauge.py _led_lit: segment i (0-based) lit at this fraction. */
    static bool led_lit(int i, int n, double frac);
    double led_frac_of(float value) const;            /* _frac of any v */
    double led_frac() const;                          /* _frac, double  */
    void led_palette_color(int i, int n, uint8_t rgb[3]) const;
    void led_layout(float strip_extent, int& n_out, float& gap_out) const;
    void led_draw_seg(Canvas& canvas, float x, float y, float w, float h,
                      float r, const uint8_t rgb[3], bool lit) const;
    void led_draw_circle(Canvas& canvas, float cx, float cy, float r,
                         const uint8_t rgb[3], bool lit) const;
    void led_draw_shape(Canvas& canvas, float sx, float sy, float sw,
                        float sh, bool vertical, float radius,
                        const uint8_t rgb[3], bool lit) const;
    void draw_led_bar(Canvas& canvas, const Rect& rect);

    /* -- bar / fader style ([CXC-PRIM-CATALOG] — gauge.py _draw_bar) -- */
    float bar_frac() const;                             /* _frac         */
    void bar_threshold_color(uint8_t rgb[3]) const;     /* _threshold_color */
    void bar_readout_text(char* buf, size_t cap) const; /* _readout_text */
    void draw_bar(Canvas& canvas, const Rect& rect);
};

#endif /* WIDGETS_GAUGE_H */
