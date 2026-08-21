/*
 * label.h — Text label widget for ComposeUX embedded rendering
 *
 * Port of the Python LabelWidget from composeio/render/widgets/label.py.
 * Simple text display with configurable font size, color, alignment, weight.
 *
 * Uses ONLY the abstract Canvas contract — no platform imports.
 */

#ifndef WIDGETS_LABEL_H
#define WIDGETS_LABEL_H

#include "widget.h"
#include <cstdint>

class LabelWidget : public Widget {
public:
    LabelWidget(const char* id,
                const char* text = "",
                float font_size = 16.0f,
                const char* align = "left",
                const char* weight = "regular",
                const char* font_family = "sans-serif");

    /* Setters */
    void set_text(const char* t) { _text = t; }
    void set_font_size(float s) { _font_size = s; }
    void set_color(uint8_t r, uint8_t g, uint8_t b) {
        _color_r = r; _color_g = g; _color_b = b;
    }
    void set_align(const char* a) { _align = a; }
    void set_weight(const char* w) { _weight = w; }
    /** `font_family:` — atlas family selector ("sans-serif" default,
     * "commp"/"Common Pixel" -> the COMMP pixel font). */
    void set_font_family(const char* f) { _font_family = f ? f : "sans-serif"; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

    /* -- Sub-widget damage ([CXC-ER-DAMAGE]) -----------------------
     * A label draws ONE line of text and nothing else, so a text change
     * damages the LINE BOX, not the rect — and on a 24 px readout in a
     * 28 px box that line box is a third of the rows the rect
     * quantises to.  Identical text damages nothing at all. */
    void damage_snapshot(const char* attr, WidgetDamage& out) const override;
    bool damage_rows(const char* attr, const WidgetDamage& before,
                     const Rect& rect, Canvas& metrics,
                     DamageSpan& out) const override;

private:
    /* THE ONE baseline rule ([CXC-ER-DAMAGE]): draw() and damage_rows()
     * must agree on where the line sits, or the damage span is computed
     * for rows the glyphs are not in. */
    float baseline_y(const Rect& rect) const {
        return rect.y + rect.h / 2.0f + _font_size * 0.35f;
    }

    const char* _text;
    float _font_size;
    const char* _align;
    const char* _weight;
    const char* _font_family = "sans-serif";
    uint8_t _color_r = 0xE0;
    uint8_t _color_g = 0xE0;
    uint8_t _color_b = 0xE0;
};

#endif /* WIDGETS_LABEL_H */
