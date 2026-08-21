/*
 * boards/classic_cyd.cpp — Board profile: classic CYD (ESP32-2432S024)
 *
 * Spec: [CXC-RM-M5] board-family; [ED-HW-CYD-PINS], [ED-HW-CYD-QUIRKS],
 * [CYD-TOUCH-CAL].
 *
 * The first (and reference) board profile: ESP32 (no PSRAM) + ILI9341
 * 240x320 rect panel on HSPI + XPT2046 resistive touch sharing the bus.
 * This board is the performance FLOOR of the whole ComposeUX system —
 * what renders acceptably here is universally deployable.
 *
 * All board constants come from cyd_pins.h (this profile's data sheet).
 * Calibration mapping (raw ADC -> screen px) lives HERE, not in the
 * generic touch dispatcher.
 */

#include "board_profile.h"
#include "cyd_pins.h"
#include "../ili9341.h"
#include "touch_xpt2046.h"

#include <algorithm>

/* ------------------------------------------------------------------ */
/* Touch: XPT2046 + the 2026-03-31 calibration                        */
/* ------------------------------------------------------------------ */

class ClassicCydTouch : public TouchDriver {
public:
    void init() override { _xpt.init(); }

    TouchPoint read() override {
        TouchReading tr = _xpt.read();
        TouchPoint tp{0.0f, 0.0f, tr.pressure, tr.touched};
        if (!tr.touched) return tp;

        /* Axis swap: raw_X -> screen_Y, raw_Y -> screen_X on this board */
#if CYD_TOUCH_SWAP_XY
        uint16_t rx = tr.raw_y;
        uint16_t ry = tr.raw_x;
#else
        uint16_t rx = tr.raw_x;
        uint16_t ry = tr.raw_y;
#endif
        float sx = (float)(rx - CYD_TOUCH_CAL_X_MIN) * CYD_SCREEN_W
                   / (float)(CYD_TOUCH_CAL_X_MAX - CYD_TOUCH_CAL_X_MIN);
        float sy = (float)(ry - CYD_TOUCH_CAL_Y_MIN) * CYD_SCREEN_H
                   / (float)(CYD_TOUCH_CAL_Y_MAX - CYD_TOUCH_CAL_Y_MIN);
#if CYD_TOUCH_INVERT_X
        sx = CYD_SCREEN_W - 1.0f - sx;
#endif
#if CYD_TOUCH_INVERT_Y
        sy = CYD_SCREEN_H - 1.0f - sy;
#endif
        tp.x = std::max(0.0f, std::min(sx, (float)(CYD_SCREEN_W - 1)));
        tp.y = std::max(0.0f, std::min(sy, (float)(CYD_SCREEN_H - 1)));
        return tp;
    }

private:
    XPT2046 _xpt;
};

/* ------------------------------------------------------------------ */
/* The profile                                                         */
/* ------------------------------------------------------------------ */

class ClassicCydProfile : public BoardProfile {
public:
    const char* name() const override { return "classic-cyd (ESP32-2432S024, ILI9341 240x320)"; }

    PanelGeometry geometry() const override {
        return PanelGeometry{CYD_SCREEN_W, CYD_SCREEN_H, PanelShape::Rect};
    }

    DisplayDriver& display() override { return _display; }
    TouchDriver* touch() override { return &_touch; }

private:
    ILI9341 _display;
    ClassicCydTouch _touch;
};

BoardProfile& board_profile() {
    static ClassicCydProfile profile;
    return profile;
}
