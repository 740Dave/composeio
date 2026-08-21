/*
 * image.cpp — Image widget implementation (M5)
 */

#include "widgets/image.h"
#include "canvas.h"

#include <algorithm>

ImageWidget::ImageWidget(const char* id, const char* src)
    : Widget(id, "image"), _src(src) {}

Size ImageWidget::measure(const Constraints& constraints) {
    return Size{std::min(120.0f, constraints.max_width),
                std::min(120.0f, constraints.max_height)};
}

void ImageWidget::draw(Canvas& canvas, const Rect& rect) {
    if (_handle) {
        canvas.draw_image(_handle, rect.x, rect.y, rect.w, rect.h);
        return;
    }

    /* Missing/undecodable asset: crossed box fallback */
    canvas.save();
    canvas.set_fill_color(0x28, 0x28, 0x3C, 160);
    canvas.fill_rect(rect.x, rect.y, rect.w, rect.h);
    canvas.set_stroke_color(0x66, 0x66, 0x78);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h);
    canvas.draw_line(rect.x, rect.y, rect.x + rect.w, rect.y + rect.h);
    canvas.draw_line(rect.x + rect.w, rect.y, rect.x, rect.y + rect.h);
    canvas.set_fill_color(0x66, 0x66, 0x78);
    canvas.set_font("sans-serif", 10.0f, "regular");
    canvas.draw_text(_src ? _src : "image",
                     rect.x + rect.w / 2.0f, rect.y + rect.h / 2.0f + 3.0f,
                     "center");
    canvas.restore();
}
