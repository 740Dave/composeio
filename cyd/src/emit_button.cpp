/*
 * emit_button.cpp — EmitButtonWidget implementation ([CXC-PRIM-TIER-PARITY]).
 *
 * Direct port of composeio/render/widgets/emit_button.py draw + on_tap
 * (visual = style.draw_dot + style.draw_button, style.py constants).
 *
 * Reference: composeio/render/widgets/emit_button.py, widgets/style.py
 */

#include "widgets/emit_button.h"
#include "canvas.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include "esp_log.h"

/* style.py constants (verbatim) */
static constexpr uint8_t kBtnFill[3]  = {0x1F, 0x53, 0x8D};
static constexpr uint8_t kBtnText[3]  = {0xDC, 0xE4, 0xEE};
static constexpr uint8_t kLedOff[3]   = {0x55, 0x55, 0x55};
static constexpr uint8_t kLedOutline[3] = {0x33, 0x33, 0x33};
static constexpr float kBtnRadius = 6.0f;

EmitButtonWidget::PublishFn EmitButtonWidget::s_publish = nullptr;

EmitButtonWidget::EmitButtonWidget(const char* id, const char* label)
    : Widget(id, "emit_button"), _label(label) {}

void EmitButtonWidget::add_emit(const char* ns, const char* value_text) {
    if (!ns || _emit_count >= kMaxEmits) return;
    snprintf(_ns[_emit_count], sizeof(_ns[0]), "%s", ns);
    snprintf(_val[_emit_count], sizeof(_val[0]), "%s",
             value_text ? value_text : "");
    _emit_count++;
}

Size EmitButtonWidget::measure(const Constraints& constraints) {
    int len = 0;
    if (_label) { while (_label[len]) len++; }
    float w = std::min(std::max(28.0f + 108.0f,
                                20.0f + 8.0f + (float)len * 8.0f + 40.0f),
                       constraints.max_width);
    return Size{w, std::min(36.0f, constraints.max_height)};
}

void EmitButtonWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    float cy = rect.y + rect.h / 2.0f;

    /* LED status dot (flat) — always OFF (no pointer-hold cue on tier). */
    canvas.set_fill_color(kLedOff[0], kLedOff[1], kLedOff[2]);
    canvas.fill_circle(rect.x + 14.0f, cy, 6.0f);
    canvas.set_stroke_color(kLedOutline[0], kLedOutline[1], kLedOutline[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_circle(rect.x + 14.0f, cy, 6.0f);

    /* Button body (style.draw_button, un-pressed). */
    float bx = rect.x + 28.0f;
    float bw = std::max(40.0f, rect.w - 28.0f - 4.0f);
    float bh = std::min(28.0f, rect.h - 4.0f);
    float by = cy - bh / 2.0f;

    canvas.set_fill_color(kBtnFill[0], kBtnFill[1], kBtnFill[2]);
    canvas.fill_round_rect(bx, by, bw, bh, kBtnRadius);
    canvas.set_fill_color(kBtnText[0], kBtnText[1], kBtnText[2]);
    canvas.set_font("sans-serif", 13.0f, "regular");
    TextMetrics m = canvas.measure_text(_label ? _label : "");
    float baseline = by + bh / 2.0f + (m.ascent - m.descent) / 2.0f;
    canvas.draw_text(_label ? _label : "", bx + bw / 2.0f, baseline, "center");

    canvas.restore();
}

void EmitButtonWidget::on_tap(float /*x*/, float /*y*/) {
    ESP_LOGI("emit_button", "'%s' tapped -> %d emit(s)", widget_id(),
             _emit_count);
    for (int i = 0; i < _emit_count; i++) {
        ESP_LOGI("emit_button", "  emit %s = %s", _ns[i], _val[i]);
        if (s_publish) s_publish(_ns[i], _val[i]);
    }
}
