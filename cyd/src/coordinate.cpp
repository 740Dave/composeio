/*
 * coordinate.cpp — CoordinateWidget (M5 port of coordinate.py + an
 * on-screen numeric keypad, the keyboard.cpp substrate's proof consumer).
 *
 * Visual reference: composeio/render/widgets/coordinate.py (label +
 * underscore-padded monospace digit display + status LED).  The keypad
 * below it is the CYD text-entry addition (no desktop on-screen keyboard).
 */

#include "widgets/coordinate.h"
#include "canvas.h"

#include <algorithm>
#include "esp_log.h"

/* coordinate.py colour constants (verbatim) */
static constexpr uint8_t kOff[3]        = {0x55, 0x55, 0x55};
static constexpr uint8_t kBlue[3]       = {0x3B, 0x8E, 0xD0};
static constexpr uint8_t kGreen[3]      = {0x2E, 0xCC, 0x71};
static constexpr uint8_t kDispBg[3]     = {0x1A, 0x1A, 0x2E};
static constexpr uint8_t kDispText[3]   = {0xCC, 0xCC, 0xCC};
static constexpr uint8_t kLedOutline[3] = {0x33, 0x33, 0x33};
/* keyboard.compose.yaml key chrome */
static constexpr uint8_t kKeyBg[3]      = {0x16, 0x1C, 0x24};
static constexpr uint8_t kKeyBorder[3]  = {0x28, 0x33, 0x40};
static constexpr uint8_t kKeyText[3]    = {0xDC, 0xE4, 0xEE};
static constexpr uint8_t kKeyAccent[3]  = {0x7F, 0xE0, 0xE8};   /* control keys */
static constexpr uint8_t kLabelText[3]  = {0xE8, 0xEC, 0xF0};

/* Chrome metrics (label + display band above the keypad). */
static constexpr float kPad     = 8.0f;
static constexpr float kTop     = 4.0f;
static constexpr float kLabelH  = 24.0f;
static constexpr float kDispH   = 28.0f;
static constexpr float kDispGap = 8.0f;

CoordinateWidget::PublishFn CoordinateWidget::s_publish = nullptr;

CoordinateWidget::CoordinateWidget(const char* id, const char* label,
                                   int max_digits, const char* ns_coordinates,
                                   const char* ns_status)
    : Widget(id, "coordinate"),
      _label(label), _max_digits(std::max(1, max_digits)),
      _ns_coordinates(ns_coordinates), _ns_status(ns_status) {
    edit_reset(_buf);
    _status = _armed ? Status::EmptyFlashing : Status::Dark;
}

void CoordinateWidget::set_armed(bool a) {
    _armed = a;
    if (!a) { edit_reset(_buf); set_status(Status::Dark); }
    else if (_buf.len == 0) { set_status(Status::EmptyFlashing); }
}

float CoordinateWidget::keypad_origin_y() const {
    return kTop + (_label && _label[0] ? kLabelH : 0.0f) + kDispH + kDispGap;
}

/* " ".join(digits.ljust(max_digits, "_")) — coordinate.py _format_digits */
void CoordinateWidget::format_digits(char* out, int cap) const {
    int o = 0;
    for (int i = 0; i < _max_digits && o < cap - 2; i++) {
        if (i) out[o++] = ' ';
        out[o++] = (i < _buf.len) ? _buf.text[i] : '_';
    }
    out[o] = '\0';
}

Size CoordinateWidget::measure(const Constraints& constraints) {
    const KeyLayout& kb = numeric_keypad();
    float w = kPad + kb.width + kPad;
    float h = keypad_origin_y() + kb.height + kPad;
    return Size{std::min(w, constraints.max_width),
                std::min(h, constraints.max_height)};
}

void CoordinateWidget::draw(Canvas& canvas, const Rect& rect) {
    const KeyLayout& kb = numeric_keypad();
    canvas.save();

    float x = rect.x + kPad;
    float y = rect.y + kTop;

    /* label (centered, bold) */
    if (_label && _label[0]) {
        canvas.set_fill_color(kLabelText[0], kLabelText[1], kLabelText[2]);
        canvas.set_font("sans-serif", 13.0f, "bold");
        TextMetrics m = canvas.measure_text(_label);
        canvas.draw_text(_label, rect.x + rect.w / 2.0f, y + m.ascent, "center");
        y += kLabelH;
    }

    /* digit display (monospace bold, underscore-padded) */
    float disp_w = kb.width - 32.0f;
    canvas.set_fill_color(kDispBg[0], kDispBg[1], kDispBg[2]);
    canvas.fill_round_rect(x, y, disp_w, kDispH, 4.0f);
    char fmt[80];
    format_digits(fmt, sizeof(fmt));
    canvas.set_fill_color(kDispText[0], kDispText[1], kDispText[2]);
    canvas.set_font("monospace", 16.0f, "bold");
    TextMetrics dm = canvas.measure_text(fmt);
    float dbase = y + kDispH / 2.0f + (dm.ascent - dm.descent) / 2.0f;
    canvas.draw_text(fmt, x + 8.0f, dbase, "left");

    /* status LED (r=8, dark outline) — solid colour on this tier */
    const uint8_t* led = kOff;
    switch (_status) {
        case Status::Dark:            led = kOff;   break;
        case Status::EmptyFlashing:   led = kBlue;  break;
        case Status::Partial:         led = kBlue;  break;
        case Status::ConfirmFlashing: led = kGreen; break;
        case Status::Committed:       led = kGreen; break;
    }
    float dot_x = x + disp_w + 16.0f;
    float dot_y = y + kDispH / 2.0f;
    canvas.set_fill_color(led[0], led[1], led[2]);
    canvas.fill_circle(dot_x, dot_y, 8.0f);
    canvas.set_stroke_color(kLedOutline[0], kLedOutline[1], kLedOutline[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_circle(dot_x, dot_y, 8.0f);

    /* numeric keypad (band-friendly: draw each visible cell, O(keys)) */
    float kx = rect.x + kPad;
    float ky = rect.y + keypad_origin_y();
    for (int i = 0; i < kb.count; i++) {
        const Key& k = kb.keys[i];
        float cx = kx + k.x, cy = ky + k.y;
        canvas.set_fill_color(kKeyBg[0], kKeyBg[1], kKeyBg[2]);
        canvas.fill_round_rect(cx, cy, k.w, k.h, 4.0f);
        canvas.set_stroke_color(kKeyBorder[0], kKeyBorder[1], kKeyBorder[2]);
        canvas.set_stroke_width(1.0f);
        canvas.stroke_round_rect(cx, cy, k.w, k.h, 4.0f);

        bool ctrl = (k.kind != KeyKind::Char);
        const uint8_t* tc = ctrl ? kKeyAccent : kKeyText;
        canvas.set_fill_color(tc[0], tc[1], tc[2]);
        canvas.set_font("monospace", 14.0f, "regular");
        TextMetrics km = canvas.measure_text(k.cap);
        float kb_base = cy + k.h / 2.0f + (km.ascent - km.descent) / 2.0f;
        canvas.draw_text(k.cap, cx + k.w / 2.0f, kb_base, "center");
    }

    canvas.restore();
}

void CoordinateWidget::on_tap(float x, float y) {
    if (!_armed) return;                 /* coordinate.py: keys gated by arm */

    const KeyLayout& kb = numeric_keypad();
    float lx = x - kPad;                 /* keypad-local */
    float ly = y - keypad_origin_y();
    int idx = keyboard_key_at(kb, lx, ly);
    if (idx < 0) return;

    EditPolicy pol;
    pol.digits_only = true;
    pol.max_len = _max_digits;

    KeyKind fired = keyboard_apply(_buf, kb.keys[idx], pol);
    switch (fired) {
        case KeyKind::Char:
            set_status(_buf.len >= _max_digits ? Status::ConfirmFlashing
                                               : Status::Partial);
            break;
        case KeyKind::Backspace:
            set_status(_buf.len ? Status::Partial : Status::EmptyFlashing);
            break;
        case KeyKind::Clear:
            set_status(_armed ? Status::EmptyFlashing : Status::Dark);
            break;
        case KeyKind::Enter:
            commit();
            break;
        default:
            break;                       /* None: blocked key, no change */
    }
}

static const char* status_name(CoordinateWidget::Status s) {
    switch (s) {
        case CoordinateWidget::Status::Dark:            return "dark";
        case CoordinateWidget::Status::EmptyFlashing:   return "empty_flashing";
        case CoordinateWidget::Status::Partial:         return "partial";
        case CoordinateWidget::Status::ConfirmFlashing: return "confirm_flashing";
        case CoordinateWidget::Status::Committed:       return "committed";
    }
    return "dark";
}

void CoordinateWidget::set_status(Status s) {
    if (s == _status) return;
    _status = s;
    if (_ns_status && s_publish) s_publish(_ns_status, status_name(s));
}

void CoordinateWidget::commit() {
    if (_buf.len == 0) return;           /* coordinate.py: empty never commits */
    if (_ns_coordinates && s_publish) s_publish(_ns_coordinates, _buf.text);
    ESP_LOGI("coordinate", "'%s' commit -> %s", widget_id(), _buf.text);
    set_status(Status::Committed);
}
