/*
 * placeholder.cpp — Placeholder widget (M5)
 *
 * Visual reference: composeio/render/widgets/placeholder.py
 * (_DIM = 0x666678, _BG = 0x28283C, dashed 6/4 border, type label).
 */

#include "widgets/placeholder.h"
#include "canvas.h"

#include <algorithm>

static constexpr uint8_t kDim[3] = {0x66, 0x66, 0x78};
static constexpr uint8_t kBg[3]  = {0x28, 0x28, 0x3C};

PlaceholderWidget::PlaceholderWidget(const char* id, const char* original_type)
    : Widget(id, "placeholder"), _original_type(original_type) {}

Size PlaceholderWidget::measure(const Constraints& constraints) {
    return Size{std::min(140.0f, constraints.max_width),
                std::min(80.0f, constraints.max_height)};
}

void PlaceholderWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    canvas.set_fill_color(kBg[0], kBg[1], kBg[2], 120);
    canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 6.0f);

    /* Dashed border via line segments (matches the Python reference) */
    canvas.set_stroke_color(kDim[0], kDim[1], kDim[2], 200);
    canvas.set_stroke_width(1.0f);
    const float dash = 6.0f, gap = 4.0f;
    for (float x = rect.x; x < rect.x + rect.w; x += dash + gap) {
        float x2 = std::min(x + dash, rect.x + rect.w);
        canvas.draw_line(x, rect.y, x2, rect.y);
        canvas.draw_line(x, rect.y + rect.h, x2, rect.y + rect.h);
    }
    for (float y = rect.y; y < rect.y + rect.h; y += dash + gap) {
        float y2 = std::min(y + dash, rect.y + rect.h);
        canvas.draw_line(rect.x, y, rect.x, y2);
        canvas.draw_line(rect.x + rect.w, y, rect.x + rect.w, y2);
    }

    /* Centered type name */
    canvas.set_fill_color(kDim[0], kDim[1], kDim[2]);
    canvas.set_font("sans-serif", 11.0f, "regular");
    canvas.draw_text(_original_type ? _original_type : "?",
                     rect.x + rect.w / 2.0f, rect.y + rect.h / 2.0f + 4.0f,
                     "center");
    canvas.restore();
}
