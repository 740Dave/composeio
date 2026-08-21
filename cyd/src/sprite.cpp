/*
 * sprite.cpp — Sprite-sequence widget implementation (M5, [CXC-R-C18])
 */

#include "widgets/sprite.h"
#include "canvas.h"

#include <algorithm>
#include "esp_timer.h"

/* Playhead clock, latched once per render pass (see tick()). */
static int64_t s_clock_ms = 0;

void SpriteWidget::tick() {
    s_clock_ms = esp_timer_get_time() / 1000;
}

SpriteWidget::SpriteWidget(const char* id, float fps)
    : Widget(id, "sprite"), _fps(fps > 0.1f ? fps : 8.0f) {}

void SpriteWidget::add_frame(ImageHandle h) {
    if (h && _count < kMaxSpriteFrames) {
        _frames[_count++] = h;
    }
}

Size SpriteWidget::measure(const Constraints& constraints) {
    return Size{std::min(120.0f, constraints.max_width),
                std::min(120.0f, constraints.max_height)};
}

void SpriteWidget::draw(Canvas& canvas, const Rect& rect) {
    if (_count == 0) {
        /* No frames: crossed-box fallback (same as a missing image) */
        canvas.set_stroke_color(0x66, 0x66, 0x78);
        canvas.set_stroke_width(1.0f);
        canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h);
        canvas.draw_line(rect.x, rect.y, rect.x + rect.w, rect.y + rect.h);
        canvas.draw_line(rect.x + rect.w, rect.y, rect.x, rect.y + rect.h);
        return;
    }
    int idx = (int)((s_clock_ms * (int64_t)(_fps * 1000) / 1000000) % _count);
    canvas.draw_image(_frames[idx], rect.x, rect.y, rect.w, rect.h);
}
