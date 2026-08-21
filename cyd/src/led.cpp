/*
 * led.cpp — LED widget implementation
 *
 * Port of Python LEDWidget draw behavior to idiomatic embedded C++.
 * Produces the same visual output: radial gradient body, glow when on,
 * specular highlight, ring outline, optional label.
 *
 * Reference: composeio/render/widgets/led.py
 */

#include "widgets/led.h"
#include "canvas.h"

#include <algorithm>
#include "esp_log.h"

/* Default palette — matches Python reference exactly */
static constexpr RGB kTeal   = {0x26, 0xA6, 0x9A};
static constexpr RGB kAmber  = {0xFF, 0xA7, 0x26};
static constexpr RGB kRed    = {0xEF, 0x53, 0x50};
static constexpr RGB kOff    = {0x44, 0x44, 0x55};

/* -------------------------------------------------------------------
 * Construction
 * ------------------------------------------------------------------- */

LEDWidget::LEDWidget(const char* id, LEDState state, const char* label)
    : Widget(id, "led"), _state(state), _label(label) {}

void LEDWidget::set_custom_color(uint8_t r, uint8_t g, uint8_t b) {
    _has_custom_color = true;
    _custom_color = {r, g, b};
}

void LEDWidget::clear_custom_color() {
    _has_custom_color = false;
}

RGB LEDWidget::active_color() const {
    if (_has_custom_color && _state != LEDState::Off) {
        return _custom_color;
    }
    switch (_state) {
        case LEDState::On:      return kTeal;
        case LEDState::Warning: return kAmber;
        case LEDState::Danger:  return kRed;
        case LEDState::Off:
        default:                return kOff;
    }
}

/* -------------------------------------------------------------------
 * Measure
 * ------------------------------------------------------------------- */

Size LEDWidget::measure(const Constraints& constraints) {
    float side = constraints.max_width;
    if (constraints.max_height < side) side = constraints.max_height;
    if (side > 60.0f) side = 60.0f;

    float label_h = _label ? 18.0f : 0.0f;
    return Size{side, side + label_h};
}

/* -------------------------------------------------------------------
 * Draw — matches Python reference behavior
 * ------------------------------------------------------------------- */

void LEDWidget::draw(Canvas& canvas, const Rect& rect) {
    RGB col = active_color();
    uint8_t r = col.r, g = col.g, b = col.b;

    float label_h = _label ? 18.0f : 0.0f;
    float available = rect.h - label_h;
    float side = (rect.w < available) ? rect.w : available;
    float led_r = side / 2.0f - 4.0f;
    float cx = rect.x + rect.w / 2.0f;
    float cy = rect.y + led_r + 4.0f;

    bool is_on = (_state != LEDState::Off);

    canvas.save();

    /* --- Glow (radial gradient from bright center to transparent) --- */
    if (is_on) {
        float glow_r = led_r * 2.0f;
        GradientStop glow_stops[3] = {
            GradientStop(0.0f, r, g, b, 100),
            GradientStop(0.5f, r, g, b,  40),
            GradientStop(1.0f, r, g, b,   0),
        };
        canvas.set_radial_gradient(cx, cy, glow_r, glow_stops, 3);
        canvas.fill_circle(cx, cy, glow_r);
    }

    /* --- LED body (filled circle with radial gradient) --- */
    {
        uint8_t bright_r = (uint8_t)std::min((int)r + 60, 255);
        uint8_t bright_g = (uint8_t)std::min((int)g + 60, 255);
        uint8_t bright_b = (uint8_t)std::min((int)b + 60, 255);
        uint8_t dark_r   = (uint8_t)std::max((int)r - 40, 0);
        uint8_t dark_g   = (uint8_t)std::max((int)g - 40, 0);
        uint8_t dark_b   = (uint8_t)std::max((int)b - 40, 0);

        GradientStop body_stops[3] = {
            GradientStop(0.0f, bright_r, bright_g, bright_b, 255),
            GradientStop(0.6f, r, g, b, 255),
            GradientStop(1.0f, dark_r, dark_g, dark_b, 255),
        };
        canvas.set_radial_gradient(cx, cy, led_r, body_stops, 3);
        canvas.fill_circle(cx, cy, led_r);
    }

    /* --- Specular highlight (small bright dot near top-left) --- */
    {
        float hl_cx = cx - led_r * 0.25f;
        float hl_cy = cy - led_r * 0.25f;
        float hl_r  = led_r * 0.4f;
        uint8_t hl_alpha = is_on ? 90 : 30;

        GradientStop hl_stops[2] = {
            GradientStop(0.0f, 255, 255, 255, hl_alpha),
            GradientStop(1.0f, 255, 255, 255, 0),
        };
        canvas.set_radial_gradient(hl_cx, hl_cy, hl_r, hl_stops, 2);
        canvas.fill_circle(hl_cx, hl_cy, hl_r);
    }

    /* --- Ring outline --- */
    canvas.set_stroke_color(0x55, 0x55, 0x66);
    canvas.set_stroke_width(1.5f);
    canvas.stroke_circle(cx, cy, led_r);

    /* --- Label --- */
    if (_label) {
        canvas.set_fill_color(0xBB, 0xBB, 0xCC);
        canvas.set_font("sans-serif", 12, "regular");
        canvas.draw_text(_label, cx, rect.y + rect.h - 2.0f, "center");
    }

    canvas.restore();
}

/* -------------------------------------------------------------------
 * on_tap [CYD-TOUCH-WIDGET-TAP]
 *
 * Toggle state: Off->On, On->Off, Warning->Off, Danger->Off
 * ------------------------------------------------------------------- */

void LEDWidget::on_tap(float x, float y) {
    (void)x;
    (void)y;

    LEDState old_state = _state;

    switch (_state) {
        case LEDState::Off:
            _state = LEDState::On;
            break;
        case LEDState::On:
        case LEDState::Warning:
        case LEDState::Danger:
        default:
            _state = LEDState::Off;
            break;
    }

    ESP_LOGI("led_tap", "'%s' tapped: %d -> %d",
             widget_id(), (int)old_state, (int)_state);
}
