/*
 * button.cpp — Button widget implementation (M5 port)
 *
 * Visual reference: composeio/render/widgets/button.py + style.py
 * (the CTk dark-blue production identity captured at M2).
 */

#include "widgets/button.h"
#include "canvas.h"

#include <algorithm>
#include "esp_log.h"

/* style.py constants (verbatim) */
static constexpr uint8_t kBtnFill[3]    = {0x1F, 0x53, 0x8D};
static constexpr uint8_t kBtnPressed[3] = {0x14, 0x37, 0x5E};
static constexpr uint8_t kBtnText[3]    = {0xDC, 0xE4, 0xEE};
static constexpr uint8_t kLedOff[3]     = {0x55, 0x55, 0x55};
static constexpr uint8_t kLedActive[3]  = {0x3B, 0x8E, 0xD0};
static constexpr uint8_t kLedOutline[3] = {0x33, 0x33, 0x33};
static constexpr float kBtnRadius = 6.0f;

ButtonWidget::PublishFn ButtonWidget::s_publish = nullptr;

ButtonWidget::ButtonWidget(const char* id, const char* label,
                           const char* publish_namespace)
    : Widget(id, "button"), _label(label),
      _publish_namespace(publish_namespace) {}

Size ButtonWidget::measure(const Constraints& constraints) {
    int len = 0;
    if (_label) { while (_label[len]) len++; }
    float w = std::min(std::max(28.0f + 108.0f, 20.0f + 8.0f + len * 8.0f + 40.0f),
                       constraints.max_width);
    return Size{w, std::min(36.0f, constraints.max_height)};
}

void ButtonWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    float cy = rect.y + rect.h / 2.0f;

    /* LED indicator: flat dot, 28px gutter (style.draw_dot) */
    bool led_on;
    if (_led_behavior && _led_behavior[0] == 's') {          /* solid */
        led_on = true;
    } else if (_led_behavior && _led_behavior[0] == 'o') {   /* off */
        led_on = false;
    } else {                                                  /* follow_state */
        led_on = _active;
    }
    const uint8_t* led = led_on ? kLedActive : kLedOff;
    canvas.set_fill_color(led[0], led[1], led[2]);
    canvas.fill_circle(rect.x + 14.0f, cy, 6.0f);
    canvas.set_stroke_color(kLedOutline[0], kLedOutline[1], kLedOutline[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_circle(rect.x + 14.0f, cy, 6.0f);

    /* Button body (style.draw_button) */
    float bx = rect.x + 28.0f;
    float bw = std::max(40.0f, rect.w - 28.0f - 4.0f);
    float bh = std::min(28.0f, rect.h - 4.0f);
    float by = cy - bh / 2.0f;

    const uint8_t* body = _pressed ? kBtnPressed : kBtnFill;
    canvas.set_fill_color(body[0], body[1], body[2]);
    canvas.fill_round_rect(bx, by, bw, bh, kBtnRadius);

    canvas.set_fill_color(kBtnText[0], kBtnText[1], kBtnText[2]);
    canvas.set_font("sans-serif", 13.0f, "regular");
    TextMetrics m = canvas.measure_text(_label ? _label : "");
    float baseline = by + bh / 2.0f + (m.ascent - m.descent) / 2.0f;
    canvas.draw_text(_label ? _label : "", bx + bw / 2.0f, baseline, "center");

    canvas.restore();
}

void ButtonWidget::on_tap(float /*x*/, float /*y*/) {
    _active = !_active;
    const char* value = _active ? "on" : "off";
    ESP_LOGI("button", "'%s' tapped -> %s%s%s", widget_id(), value,
             _publish_namespace ? " publish " : "",
             _publish_namespace ? _publish_namespace : "");
    if (_publish_namespace && s_publish) {
        s_publish(_publish_namespace, value);
    }
}
