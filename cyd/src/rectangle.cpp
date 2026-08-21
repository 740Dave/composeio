/*
 * rectangle.cpp — Rectangle widget implementation
 *
 * Port of Python RectangleWidget draw behavior to idiomatic embedded C++.
 * Filled and/or stroked rectangle with optional corner radius.
 *
 * Reference: composeio/render/widgets/rectangle.py
 */

#include "widgets/rectangle.h"
#include "canvas.h"

#include <algorithm>

/* -------------------------------------------------------------------
 * Construction
 * ------------------------------------------------------------------- */

RectangleWidget::RectangleWidget(const char* id, float width, float height)
    : Widget(id, "rectangle"), _rect_width(width), _rect_height(height) {}

/* -------------------------------------------------------------------
 * Measure
 * ------------------------------------------------------------------- */

Size RectangleWidget::measure(const Constraints& constraints) {
    return Size{
        std::min(_rect_width, constraints.max_width),
        std::min(_rect_height, constraints.max_height)
    };
}

/* -------------------------------------------------------------------
 * Draw — matches Python reference behavior
 * ------------------------------------------------------------------- */

void RectangleWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();

    /* Fill */
    canvas.set_fill_color(_fill_r, _fill_g, _fill_b, _fill_a);
    if (_corner_radius > 0.0f) {
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, _corner_radius);
    } else {
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h);
    }

    /* Stroke */
    if (_has_stroke) {
        canvas.set_stroke_color(_stroke_r, _stroke_g, _stroke_b, _stroke_a);
        canvas.set_stroke_width(_stroke_width);
        if (_corner_radius > 0.0f) {
            canvas.stroke_round_rect(rect.x, rect.y, rect.w, rect.h, _corner_radius);
        } else {
            canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h);
        }
    }

    canvas.restore();
}
