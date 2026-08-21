/*
 * text_input.cpp — TextInputWidget (M5 port of text_input.py's default
 * single-line chrome + an on-screen QWERTY, the keyboard.cpp substrate's
 * second consumer after coordinate).
 *
 * Visual reference: composeio/render/widgets/text_input.py draw() default
 * (form) chrome — label + single-line entry box + caret.  The qwerty
 * keyboard + SEND bar below it are the CYD text-entry addition (no desktop
 * on-screen keyboard).  See text_input.h for the faithful degradations.
 */

#include "widgets/text_input.h"
#include "canvas.h"

#include <algorithm>
#include <cstring>
#include "esp_log.h"

/* text_input.py / coordinate.py chrome palette (shared band-tier look). */
static constexpr uint8_t kFieldBg[3]    = {0x1A, 0x1A, 0x2E};
static constexpr uint8_t kFieldBorder[3]= {0x28, 0x33, 0x40};
static constexpr uint8_t kFieldText[3]  = {0xCC, 0xCC, 0xCC};
static constexpr uint8_t kPlaceholder[3]= {0x66, 0x6C, 0x78};
static constexpr uint8_t kCaret[3]      = {0xDC, 0xE4, 0xEE};
/* keyboard.compose.yaml key chrome (verbatim from coordinate.cpp). */
static constexpr uint8_t kKeyBg[3]      = {0x16, 0x1C, 0x24};
static constexpr uint8_t kKeyBorder[3]  = {0x28, 0x33, 0x40};
static constexpr uint8_t kKeyText[3]    = {0xDC, 0xE4, 0xEE};
static constexpr uint8_t kKeyAccent[3]  = {0x7F, 0xE0, 0xE8};   /* control keys */
static constexpr uint8_t kLabelText[3]  = {0xE8, 0xEC, 0xF0};
/* SEND bar (the commit affordance). */
static constexpr uint8_t kSendBg[3]     = {0x2E, 0x7D, 0x5B};
static constexpr uint8_t kSendText[3]   = {0xF0, 0xFF, 0xF4};

/* Chrome metrics (label + field above the keyboard, SEND bar below). */
static constexpr float kPad     = 8.0f;
static constexpr float kTop     = 4.0f;
static constexpr float kLabelH  = 20.0f;
static constexpr float kFieldH  = 28.0f;
static constexpr float kFieldGap= 8.0f;
static constexpr float kSendGap = 8.0f;
static constexpr float kSendH   = 32.0f;
static constexpr float kTextPad = 8.0f;

TextInputWidget::PublishFn TextInputWidget::s_publish = nullptr;

TextInputWidget::TextInputWidget(const char* id, const char* label,
                                 const char* placeholder, const char* ns_value,
                                 bool clear_on_commit, int max_len)
    : Widget(id, "text_input"),
      _label(label), _placeholder(placeholder), _ns_value(ns_value),
      _clear_on_commit(clear_on_commit),
      _max_len(std::max(1, std::min(max_len, kEditCap - 1))) {
    edit_reset(_buf);
}

float TextInputWidget::keyboard_origin_y() const {
    return kTop + (_label && _label[0] ? kLabelH : 0.0f) + kFieldH + kFieldGap;
}

void TextInputWidget::send_rect(float rx, float ry, float& sx, float& sy,
                                float& sw, float& sh) const {
    const KeyLayout& kb = qwerty_keyboard();
    sx = rx + kPad;
    sy = ry + keyboard_origin_y() + kb.height + kSendGap;
    sw = kb.width;
    sh = kSendH;
}

Size TextInputWidget::measure(const Constraints& constraints) {
    const KeyLayout& kb = qwerty_keyboard();
    float w = kPad + kb.width + kPad;
    float h = keyboard_origin_y() + kb.height + kSendGap + kSendH + kPad;
    return Size{std::min(w, constraints.max_width),
                std::min(h, constraints.max_height)};
}

void TextInputWidget::draw(Canvas& canvas, const Rect& rect) {
    const KeyLayout& kb = qwerty_keyboard();
    canvas.save();

    float x = rect.x + kPad;
    float y = rect.y + kTop;

    /* label (left, bold) */
    if (_label && _label[0]) {
        canvas.set_fill_color(kLabelText[0], kLabelText[1], kLabelText[2]);
        canvas.set_font("sans-serif", 13.0f, "bold");
        TextMetrics m = canvas.measure_text(_label);
        canvas.draw_text(_label, x, y + m.ascent, "left");
        y += kLabelH;
    }

    /* single-line field box (matches text_input.py default chrome) */
    float fw = kb.width;
    canvas.set_fill_color(kFieldBg[0], kFieldBg[1], kFieldBg[2]);
    canvas.fill_round_rect(x, y, fw, kFieldH, 6.0f);
    canvas.set_stroke_color(kFieldBorder[0], kFieldBorder[1], kFieldBorder[2]);
    canvas.set_stroke_width(2.0f);
    canvas.stroke_round_rect(x + 1.0f, y + 1.0f, fw - 2.0f, kFieldH - 2.0f, 5.0f);

    /* text / placeholder + caret, clipped to the entry */
    canvas.save();
    canvas.clip_round_rect(x + 2.0f, y + 2.0f, fw - 4.0f, kFieldH - 4.0f, 4.0f);
    canvas.set_font("monospace", 15.0f, "regular");
    float base = y + kFieldH / 2.0f;
    float avail = fw - 2.0f * kTextPad;
    if (_buf.len > 0) {
        /* prefix up to the caret (text_input.py keeps the caret visible) */
        char prefix[kEditCap];
        int c = std::max(0, std::min(_buf.cursor, _buf.len));
        std::memcpy(prefix, _buf.text, (size_t)c);
        prefix[c] = '\0';
        float cur_w = canvas.measure_text(prefix).width;
        float shift = std::max(0.0f, cur_w - avail);
        float origin = x + kTextPad - shift;
        canvas.set_fill_color(kFieldText[0], kFieldText[1], kFieldText[2]);
        TextMetrics tm = canvas.measure_text(_buf.text);
        float tbase = base + (tm.ascent - tm.descent) / 2.0f;
        canvas.draw_text(_buf.text, origin, tbase, "left");
        /* caret at the cursor (append-only on this tier -> at end) */
        float caret_x = origin + cur_w;
        canvas.set_stroke_color(kCaret[0], kCaret[1], kCaret[2]);
        canvas.set_stroke_width(1.0f);
        canvas.draw_line(caret_x + 0.5f, y + 6.0f, caret_x + 0.5f, y + kFieldH - 6.0f);
    } else if (_placeholder && _placeholder[0]) {
        canvas.set_fill_color(kPlaceholder[0], kPlaceholder[1], kPlaceholder[2]);
        TextMetrics pm = canvas.measure_text(_placeholder);
        float pbase = base + (pm.ascent - pm.descent) / 2.0f;
        canvas.draw_text(_placeholder, x + kTextPad, pbase, "left");
    }
    canvas.restore();

    /* qwerty keyboard (band-friendly: draw each visible cell, O(keys)) */
    float kx = rect.x + kPad;
    float ky = rect.y + keyboard_origin_y();
    for (int i = 0; i < kb.count; i++) {
        const Key& k = kb.keys[i];
        float cx = kx + k.x, cy = ky + k.y;
        canvas.set_fill_color(kKeyBg[0], kKeyBg[1], kKeyBg[2]);
        canvas.fill_round_rect(cx, cy, k.w, k.h, 4.0f);
        canvas.set_stroke_color(kKeyBorder[0], kKeyBorder[1], kKeyBorder[2]);
        canvas.set_stroke_width(1.0f);
        canvas.stroke_round_rect(cx, cy, k.w, k.h, 4.0f);

        bool ctrl = (k.kind != KeyKind::Char && k.kind != KeyKind::Space);
        const uint8_t* tc = ctrl ? kKeyAccent : kKeyText;
        canvas.set_fill_color(tc[0], tc[1], tc[2]);
        canvas.set_font("monospace", 14.0f, "regular");
        TextMetrics km = canvas.measure_text(k.cap);
        float kb_base = cy + k.h / 2.0f + (km.ascent - km.descent) / 2.0f;
        canvas.draw_text(k.cap, cx + k.w / 2.0f, kb_base, "center");
    }

    /* SEND bar (the commit affordance — text_input.py SEND/commit_namespace) */
    float sx, sy, sw, sh;
    send_rect(rect.x, rect.y, sx, sy, sw, sh);
    canvas.set_fill_color(kSendBg[0], kSendBg[1], kSendBg[2]);
    canvas.fill_round_rect(sx, sy, sw, sh, 6.0f);
    canvas.set_fill_color(kSendText[0], kSendText[1], kSendText[2]);
    canvas.set_font("sans-serif", 14.0f, "bold");
    TextMetrics sm = canvas.measure_text("SEND");
    float sbase = sy + sh / 2.0f + (sm.ascent - sm.descent) / 2.0f;
    canvas.draw_text("SEND", sx + sw / 2.0f, sbase, "center");

    canvas.restore();
}

void TextInputWidget::on_tap(float x, float y) {
    /* SEND bar commit? */
    float sx, sy, sw, sh;
    send_rect(0.0f, 0.0f, sx, sy, sw, sh);
    if (x >= sx && x < sx + sw && y >= sy && y < sy + sh) {
        commit();
        return;
    }

    /* keyboard hit-test (keyboard-local coords, the touch_dispatch contract) */
    const KeyLayout& kb = qwerty_keyboard();
    float lx = x - kPad;
    float ly = y - keyboard_origin_y();
    int idx = keyboard_key_at(kb, lx, ly);
    if (idx < 0) return;

    EditPolicy pol;
    pol.digits_only = false;
    pol.max_len = _max_len;
    keyboard_apply(_buf, kb.keys[idx], pol);   /* Char/Space insert, Backspace pop, Shift inert */
}

void TextInputWidget::commit() {
    if (_buf.len == 0) return;                 /* text_input.py: empty never commits */
    if (_ns_value && s_publish) s_publish(_ns_value, _buf.text);
    ESP_LOGI("text_input", "'%s' commit -> %s", widget_id(), _buf.text);
    if (_clear_on_commit) edit_reset(_buf);    /* chat-composer semantics */
}
