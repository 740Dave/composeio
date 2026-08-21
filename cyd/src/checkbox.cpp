/*
 * checkbox.cpp — CheckboxWidget implementation ([CXC-PRIM-TIER-PARITY]).
 *
 * Direct port of composeio/render/widgets/checkbox.py draw + on_tap (and
 * the style.py CHECK_* constants captured at M2).
 *
 * Reference: composeio/render/widgets/checkbox.py, widgets/style.py
 */

#include "widgets/checkbox.h"
#include "canvas.h"

#include <algorithm>
#include <cstring>
#include "esp_log.h"

/* style.py constants (verbatim) */
static constexpr uint8_t kCheckFill[3]   = {0x1F, 0x53, 0x8D};
static constexpr uint8_t kCheckMark[3]   = {0xDC, 0xE4, 0xEE};
static constexpr uint8_t kCheckBorder[3] = {0x94, 0x9A, 0x9F};
static constexpr uint8_t kText[3]        = {0xD6, 0xD6, 0xD6};

static constexpr float kBox = 24.0f;         /* CTkCheckBox width/height */
static constexpr float kBoxRadius = 6.0f;

CheckboxWidget::PublishFn CheckboxWidget::s_publish = nullptr;

CheckboxWidget::CheckboxWidget(const char* id, const char* label,
                               const char* on_value, const char* off_value,
                               const char* bound_namespace)
    : Widget(id, "checkbox"), _label(label), _on_value(on_value),
      _off_value(off_value), _namespace(bound_namespace) {}

Size CheckboxWidget::measure(const Constraints& constraints) {
    int len = 0;
    if (_label) { while (_label[len]) len++; }
    float w = kBox + 10.0f + (float)len * 7.2f + 16.0f;
    return Size{std::min(w, constraints.max_width),
                std::min(kBox + 6.0f, constraints.max_height)};
}

void CheckboxWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    float bx = rect.x + 8.0f;
    float by = rect.y + (rect.h - kBox) / 2.0f;

    if (_checked) {
        canvas.set_fill_color(kCheckFill[0], kCheckFill[1], kCheckFill[2]);
        canvas.fill_round_rect(bx, by, kBox, kBox, kBoxRadius);
        /* checkmark (stroked polyline, matching checkbox.py draw_path) */
        canvas.set_stroke_color(kCheckMark[0], kCheckMark[1], kCheckMark[2]);
        canvas.set_stroke_width(2.5f);
        PathCommand check[3] = {
            PathCommand::move(bx + kBox * 0.24f, by + kBox * 0.52f),
            PathCommand::line(bx + kBox * 0.44f, by + kBox * 0.72f),
            PathCommand::line(bx + kBox * 0.78f, by + kBox * 0.30f),
        };
        canvas.draw_path(check, 3);
    } else {
        canvas.set_stroke_color(kCheckBorder[0], kCheckBorder[1],
                                kCheckBorder[2]);
        canvas.set_stroke_width(3.0f);
        canvas.stroke_round_rect(bx + 1.5f, by + 1.5f, kBox - 3.0f,
                                 kBox - 3.0f, kBoxRadius - 1.5f);
    }

    if (_label && _label[0]) {
        canvas.set_fill_color(kText[0], kText[1], kText[2]);
        canvas.set_font("sans-serif", 13.0f, "regular");
        /* style.baseline_for: center the font at rect vertical middle. */
        TextMetrics m = canvas.measure_text("Ag");
        float baseline = rect.y + rect.h / 2.0f + (m.ascent - m.descent) / 2.0f;
        canvas.draw_text(_label, bx + kBox + 10.0f, baseline, "left");
    }
    canvas.restore();
}

void CheckboxWidget::on_tap(float /*x*/, float /*y*/) {
    _checked = !_checked;
    const char* value = _checked ? _on_value : _off_value;
    ESP_LOGI("checkbox", "'%s' tapped -> %s%s%s", widget_id(), value,
             _namespace ? " publish " : "", _namespace ? _namespace : "");
    if (_namespace && s_publish) s_publish(_namespace, value);
}
