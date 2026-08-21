/*
 * circle.cpp — Circle widget implementation
 *
 * Port of Python CircleWidget draw behavior to idiomatic embedded C++.
 * Filled and/or stroked circle.
 *
 * Reference: composeio/render/widgets/circle.py
 */

#include "widgets/circle.h"
#include "canvas.h"

#include <algorithm>

/* -------------------------------------------------------------------
 * Construction
 * ------------------------------------------------------------------- */

CircleWidget::CircleWidget(const char* id, float radius)
    : Widget(id, "circle"), _radius(radius) {}

/* -------------------------------------------------------------------
 * Measure
 * ------------------------------------------------------------------- */

Size CircleWidget::measure(const Constraints& constraints) {
    float d = _radius * 2.0f;
    return Size{
        std::min(d, constraints.max_width),
        std::min(d, constraints.max_height)
    };
}

/* -------------------------------------------------------------------
 * Draw — matches Python reference behavior
 * ------------------------------------------------------------------- */

void CircleWidget::draw(Canvas& canvas, const Rect& rect) {
    float ccx = rect.x + rect.w / 2.0f;
    float ccy = rect.y + rect.h / 2.0f;
    float r = std::min(rect.w, rect.h) / 2.0f;

    canvas.save();

    /* Fill */
    canvas.set_fill_color(_fill_r, _fill_g, _fill_b, _fill_a);
    canvas.fill_circle(ccx, ccy, r);

    /* Stroke */
    if (_has_stroke) {
        canvas.set_stroke_color(_stroke_r, _stroke_g, _stroke_b, _stroke_a);
        canvas.set_stroke_width(_stroke_width);
        canvas.stroke_circle(ccx, ccy, r);
    }

    canvas.restore();
}
