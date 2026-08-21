/*
 * touch_dispatch.cpp -- Touch hit testing, state machine, and dispatch
 *
 * Spec: [CYD-TOUCH-HIT], [CYD-TOUCH-STATE], [CYD-TOUCH-CAL]
 *
 * Implements:
 * - Widget registry (static array, 16 entries max)
 * - Hit testing (reverse z-order, topmost first)
 * - Touch state machine (IDLE -> DOWN -> TAP, with debounce)
 * - Calibration from raw XPT2046 ADC to screen coordinates
 * - Dispatch to widget on_tap() with local coordinates
 */

#include "touch_dispatch.h"
#include "cyd_pins.h"

#include "esp_log.h"
#include "esp_timer.h"

#include <algorithm>

static const char* TAG = "touch_dispatch";

/* -------------------------------------------------------------------
 * Widget registry [CYD-TOUCH-REGISTRY]
 * ------------------------------------------------------------------- */

static WidgetEntry widget_registry[kMaxWidgets];
static int widget_count = 0;

void register_widget(Widget* w, const Rect& r) {
    if (widget_count >= kMaxWidgets) {
        ESP_LOGW(TAG, "Widget registry full (%d max), ignoring %s",
                 kMaxWidgets, w->widget_id());
        return;
    }
    widget_registry[widget_count].widget = w;
    widget_registry[widget_count].rect = r;
    widget_count++;
    ESP_LOGI(TAG, "Registered widget '%s' at (%.0f,%.0f,%.0f,%.0f)",
             w->widget_id(), r.x, r.y, r.w, r.h);
}

void clear_registry() {
    widget_count = 0;
}

/* -------------------------------------------------------------------
 * Hit testing [CYD-TOUCH-HITTEST]
 * ------------------------------------------------------------------- */

Widget* hit_test(float sx, float sy) {
    /* Reverse order: last registered = topmost z-order */
    for (int i = widget_count - 1; i >= 0; i--) {
        const Rect& r = widget_registry[i].rect;
        if (sx >= r.x && sx < r.x + r.w &&
            sy >= r.y && sy < r.y + r.h) {
            return widget_registry[i].widget;
        }
    }
    return nullptr;
}

Widget* hit_test_with_rect(float sx, float sy, Rect& out_rect) {
    for (int i = widget_count - 1; i >= 0; i--) {
        const Rect& r = widget_registry[i].rect;
        if (sx >= r.x && sx < r.x + r.w &&
            sy >= r.y && sy < r.y + r.h) {
            out_rect = r;
            return widget_registry[i].widget;
        }
    }
    return nullptr;
}

/* Calibration moved to the board profile ([CXC-RM-M5] seam):
 * boards/classic_cyd.cpp owns raw-ADC -> screen-px mapping; this
 * dispatcher receives calibrated TouchPoints. */

/* -------------------------------------------------------------------
 * Touch state machine [CYD-TOUCH-STATE]
 *
 *          pressure > threshold
 * IDLE  ──────────────────────────>  DOWN
 *   ^                                  |
 *   |         pressure < threshold     |  held < 500ms
 *   |         for 50ms                 |  then released
 *   |<─────────────────────────────────|
 *   |         (RELEASE -> IDLE)        |
 *   |                                  v
 *   |<──────────────────────────── TAP (fire on_tap)
 * ------------------------------------------------------------------- */

enum class TouchState : uint8_t {
    Idle,
    Down,
};

/* State machine parameters */
static constexpr int64_t kDebounceUs   = 50 * 1000;    /* 50ms in microseconds */
static constexpr int64_t kMaxTapUs     = 500 * 1000;   /* 500ms in microseconds */

/* State */
static TouchState touch_state = TouchState::Idle;
static int64_t down_time_us = 0;           /* Timestamp when DOWN started */
static int64_t release_time_us = 0;        /* Timestamp of last release */
static float down_sx = 0, down_sy = 0;     /* Calibrated screen coords at DOWN */
static bool s_any_tap = false;
static unsigned s_tap_serial = 0;

bool any_tap_dispatched() { return s_any_tap; }
unsigned tap_serial() { return s_tap_serial; }

void process_touch(const TouchPoint& tp) {
    int64_t now_us = esp_timer_get_time();
    float sx = tp.x, sy = tp.y;

    switch (touch_state) {
        case TouchState::Idle:
            if (tp.touched) {
                /* Debounce: ignore if too soon after last release */
                if ((now_us - release_time_us) < kDebounceUs) {
                    break;
                }
                touch_state = TouchState::Down;
                down_time_us = now_us;
                down_sx = sx;
                down_sy = sy;
                ESP_LOGD(TAG, "DOWN at (%.1f, %.1f) p=%u", sx, sy, tp.pressure);
            }
            break;

        case TouchState::Down:
            if (!tp.touched) {
                /* Released -- check if it qualifies as a TAP */
                int64_t held_us = now_us - down_time_us;
                release_time_us = now_us;
                touch_state = TouchState::Idle;

                if (held_us <= kMaxTapUs) {
                    /* TAP event -- dispatch to widget */
                    ESP_LOGI(TAG, "TAP at (%.1f, %.1f) held=%lldms",
                             down_sx, down_sy, (long long)(held_us / 1000));

                    Rect widget_rect;
                    Widget* w = hit_test_with_rect(down_sx, down_sy, widget_rect);
                    if (w) {
                        /* Convert to local coordinates [CYD-TOUCH-DISPATCH] */
                        float local_x = down_sx - widget_rect.x;
                        float local_y = down_sy - widget_rect.y;
                        ESP_LOGI(TAG, "  -> widget '%s' local=(%.1f, %.1f)",
                                 w->widget_id(), local_x, local_y);
                        w->on_tap(local_x, local_y);
                        s_any_tap = true;
                        s_tap_serial++;
                    } else {
                        ESP_LOGD(TAG, "  -> no widget at tap position");
                    }
                } else {
                    ESP_LOGD(TAG, "LONG_PRESS ignored (held=%lldms)",
                             (long long)(held_us / 1000));
                }
            }
            break;
    }
}
