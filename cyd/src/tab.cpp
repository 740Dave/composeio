/*
 * tab.cpp — TabWidget implementation ([CXC-PRIM-TIER-PARITY]).
 *
 * Direct port of composeio/render/widgets/tab.py draw() + on_tap() (the
 * segmented BAR; the style.py SEG_* constants captured verbatim at M2).
 * Per-tab content and the subclass index-namespace seam are intentionally
 * out of scope — see the header for why (non-forking layering).
 *
 * Reference: composeio/render/widgets/tab.py, widgets/style.py
 */

#include "widgets/tab.h"
#include "canvas.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include "esp_log.h"

/* style.py constants (verbatim). */
static constexpr uint8_t kSegBg[3]       = {0x4A, 0x4A, 0x4A};  /* SEG_BG       */
static constexpr uint8_t kSegSelected[3] = {0x1F, 0x53, 0x8D};  /* SEG_SELECTED */
static constexpr uint8_t kSegText[3]     = {0xDC, 0xE4, 0xEE};  /* SEG_TEXT     */

static constexpr float kBarH      = 32.0f;   /* tab.py _BAR_H */
static constexpr float kSegH      = 26.0f;   /* tab.py _SEG_H */
static constexpr float kStripR    = 6.0f;
static constexpr float kSelR      = 5.0f;
static constexpr float kMinCellW  = 70.0f;   /* tab.py max(70, text+28) */
static constexpr float kCellPad   = 28.0f;
static constexpr float kFontSize  = 13.0f;

TabWidget::TabWidget(const char* id, int initial_tab)
    : Widget(id, "tab"), _active(initial_tab < 0 ? 0 : initial_tab) {}

void TabWidget::add_label(const char* label) {
    if (_count >= kMaxTabs) return;
    snprintf(_labels[_count], sizeof(_labels[0]), "%s", label ? label : "");
    _count++;
}

void TabWidget::set_active(int index) {
    /* tab.py set_active clamps active into range first, then requires the
     * new index to be a valid, different tab.  The tap-serial full
     * re-render (touch_dispatch) repaints the moved indicator. */
    if (_count == 0) return;
    if (_active >= _count) _active = _count - 1;
    if (index != _active && index >= 0 && index < _count) _active = index;
}

Size TabWidget::measure(const Constraints& constraints) {
    /* tab.py measure with no child content: width defaults to 120,
     * height is the bar (children measure to 0 here). */
    return Size{std::min(120.0f, constraints.max_width),
                std::min(kBarH, constraints.max_height)};
}

void TabWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    _bar_top = rect.y;
    _bar_bot = rect.y + kBarH;

    /* Cell widths (tab.py: max(70, measure_text(lb).width + 28)); an
     * empty tab set still draws a 70px strip, matching `or [70.0]`. */
    canvas.set_font("sans-serif", kFontSize, "regular");
    float widths[kMaxTabs];
    float bar_w = 0.0f;
    for (int i = 0; i < _count; i++) {
        float tw = canvas.measure_text(_labels[i]).width + kCellPad;
        widths[i] = tw > kMinCellW ? tw : kMinCellW;
        bar_w += widths[i];
    }
    if (_count == 0) bar_w = kMinCellW;

    float bx = rect.x + (rect.w - bar_w) / 2.0f;
    float by = rect.y + (kBarH - kSegH) / 2.0f;

    /* Segmented-button background strip. */
    canvas.set_fill_color(kSegBg[0], kSegBg[1], kSegBg[2]);
    canvas.fill_round_rect(bx, by, bar_w, kSegH, kStripR);

    /* style.baseline_for: centre the current font at by + kSegH/2. */
    TextMetrics m = canvas.measure_text("Ag");
    float baseline = (by + kSegH / 2.0f) + (m.ascent - m.descent) / 2.0f;

    _seg_count = 0;
    float sx = bx;
    for (int i = 0; i < _count; i++) {
        float w = widths[i];
        if (i == _active) {
            canvas.set_fill_color(kSegSelected[0], kSegSelected[1],
                                  kSegSelected[2]);
            canvas.fill_round_rect(sx + 2.0f, by + 2.0f, w - 4.0f,
                                   kSegH - 4.0f, kSelR);
        }
        canvas.set_fill_color(kSegText[0], kSegText[1], kSegText[2]);
        canvas.draw_text(_labels[i], sx + w / 2.0f, baseline, "center");
        _seg_x0[i] = sx;
        _seg_x1[i] = sx + w;
        _seg_count++;
        sx += w;
    }
    canvas.restore();
}

void TabWidget::on_tap(float x, float y) {
    /* tab.py routes a tap to a tab only when it lands in the bar band
     * (hit_test_tree gates y <= rect.y + bar_h), then maps x to a cell
     * via _seg_bounds.  On this tier the whole widget rect is registered
     * for touch, so gate the bar band here to match. */
    if (y < _bar_top || y > _bar_bot) return;
    for (int i = 0; i < _seg_count; i++) {
        if (x >= _seg_x0[i] && x <= _seg_x1[i]) {
            if (i != _active) {
                set_active(i);
                ESP_LOGI("tab", "'%s' -> tab %d (%s)", widget_id(), i,
                         _labels[i]);
            }
            return;
        }
    }
}
