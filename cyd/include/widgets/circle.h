/*
 * circle.h — Circle widget for ComposeUX embedded rendering
 *
 * Port of the Python CircleWidget from composeio/render/widgets/circle.py.
 * Filled and/or stroked circle.
 *
 * Uses ONLY the abstract Canvas contract — no platform imports.
 */

#ifndef WIDGETS_CIRCLE_H
#define WIDGETS_CIRCLE_H

#include "widget.h"
#include <cstdint>

class CircleWidget : public Widget {
public:
    CircleWidget(const char* id, float radius = 30.0f);

    /* Setters */
    void set_fill_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 255) {
        _fill_r = r; _fill_g = g; _fill_b = b; _fill_a = a;
    }
    void set_stroke_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 255) {
        _stroke_r = r; _stroke_g = g; _stroke_b = b; _stroke_a = a;
        _has_stroke = true;
    }
    void set_stroke_width(float w) { _stroke_width = w; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    float _radius;
    float _stroke_width = 1.0f;

    uint8_t _fill_r = 0x26, _fill_g = 0xA6, _fill_b = 0x9A, _fill_a = 255;
    uint8_t _stroke_r = 0, _stroke_g = 0, _stroke_b = 0, _stroke_a = 255;
    bool _has_stroke = false;
};

#endif /* WIDGETS_CIRCLE_H */
