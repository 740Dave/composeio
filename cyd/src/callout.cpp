/*
 * callout.cpp — CalloutWidget implementation ([CXC-PRIM-TIER-PARITY]).
 *
 * Direct port of composeio/render/widgets/callout.py draw.
 *
 * Reference: composeio/render/widgets/callout.py, widgets/style.py
 */

#include "widgets/callout.h"
#include "canvas.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

static constexpr uint8_t kChipBg[3] = {0x21, 0x21, 0x21};
static constexpr uint8_t kText[3]   = {0xD6, 0xD6, 0xD6};   /* style.TEXT */
static constexpr float kPadX = 8.0f;
static constexpr float kChipH = 22.0f;
static constexpr float kDotR = 3.5f;

CalloutWidget::CalloutWidget(const char* id, const char* text)
    : Widget(id, "callout") {
    set_text(text);
}

void CalloutWidget::set_text(const char* t) {
    snprintf(_text, sizeof(_text), "%s", t ? t : "");
}

/* callout.py _resolve: a 0-1 value is a fraction of the extent; anything
 * else is already px.  (The int-vs-float distinction Python makes for the
 * exact 0/1 edge is not available here — 0..1 is always a fraction.) */
static float resolve_coord(float v, float extent) {
    return (v >= 0.0f && v <= 1.0f) ? v * extent : v;
}

Size CalloutWidget::measure(const Constraints& constraints) {
    float w = (float)strlen(_text) * _font_size * 0.62f + 2.0f * kPadX + 20.0f;
    return Size{std::min(std::max(w, 60.0f), constraints.max_width),
                std::min(64.0f, constraints.max_height)};
}

void CalloutWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();

    float tx = rect.x + resolve_coord(_target_x, rect.w);
    float ty = rect.y + resolve_coord(_target_y, rect.h);

    /* chip near the target (offset up-left by default), clamped in rect */
    canvas.set_font("sans-serif", _font_size, "medium");
    TextMetrics tm = canvas.measure_text(_text[0] ? _text : " ");
    float chip_w = tm.width + 2.0f * kPadX;
    float cx = std::max(rect.x,
                        std::min(tx + _offset_x, rect.x + rect.w - chip_w));
    float cy = std::max(rect.y,
                        std::min(ty + _offset_y, rect.y + rect.h - kChipH));
    /* leader departs the chip edge nearest the target */
    float ax = cx + (tx > cx + chip_w / 2.0f ? chip_w : 0.0f);
    float ay = cy + (ty > cy + kChipH / 2.0f ? kChipH : kChipH / 2.0f);

    /* leader line, then target dot + halo ring */
    canvas.set_stroke_color(_color[0], _color[1], _color[2]);
    canvas.set_stroke_width(1.5f);
    canvas.draw_line(ax, ay, tx, ty);
    canvas.set_fill_color(_color[0], _color[1], _color[2]);
    canvas.fill_circle(tx, ty, kDotR);
    canvas.set_stroke_color(_color[0], _color[1], _color[2], 90);
    canvas.set_stroke_width(1.5f);
    canvas.stroke_circle(tx, ty, kDotR + 3.0f);

    /* chip (on top of the leader) */
    canvas.set_fill_color(kChipBg[0], kChipBg[1], kChipBg[2], 235);
    canvas.fill_round_rect(cx, cy, chip_w, kChipH, 5.0f);
    canvas.set_stroke_color(_color[0], _color[1], _color[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_round_rect(cx, cy, chip_w, kChipH, 5.0f);
    canvas.set_fill_color(kText[0], kText[1], kText[2]);
    /* style.baseline_for(cy + CHIP_H/2) with the chip font current */
    TextMetrics bm = canvas.measure_text("Ag");
    float baseline = cy + kChipH / 2.0f + (bm.ascent - bm.descent) / 2.0f;
    canvas.draw_text(_text, cx + kPadX, baseline, "left");

    canvas.restore();
}
