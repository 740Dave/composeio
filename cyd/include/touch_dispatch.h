/*
 * touch_dispatch.h -- Touch hit testing and widget dispatch
 *
 * Spec: [CYD-TOUCH-HIT], [CYD-TOUCH-STATE], [CYD-TOUCH-CAL]
 *
 * Widget registry for hit testing, touch state machine (debounce + tap),
 * and calibrated coordinate mapping.
 */

#ifndef TOUCH_DISPATCH_H
#define TOUCH_DISPATCH_H

#include "widget.h"
#include "board_profile.h"

/* -------------------------------------------------------------------
 * Widget registry [CYD-TOUCH-REGISTRY]
 * ------------------------------------------------------------------- */

struct WidgetEntry {
    Widget* widget;
    Rect rect;
};

static constexpr int kMaxWidgets = 32;

/**
 * Register a widget and its layout rect for hit testing.
 * Call during scene setup, before the render loop.
 */
void register_widget(Widget* w, const Rect& r);

/**
 * Clear all registered widgets.
 */
void clear_registry();

/* -------------------------------------------------------------------
 * Hit testing [CYD-TOUCH-HITTEST]
 * ------------------------------------------------------------------- */

/**
 * Find the topmost widget at screen coordinates (sx, sy).
 * Searches in reverse order (last registered = topmost z-order).
 * Returns nullptr if no widget at that position.
 */
Widget* hit_test(float sx, float sy);

/**
 * Find the widget and its rect at screen coordinates.
 * Returns the widget pointer and fills out_rect with the widget's rect.
 * Returns nullptr if no widget at that position.
 */
Widget* hit_test_with_rect(float sx, float sy, Rect& out_rect);

/* -------------------------------------------------------------------
 * Touch processing [CYD-TOUCH-STATE], [CYD-TOUCH-CAL]
 * ------------------------------------------------------------------- */

/**
 * Process a CALIBRATED touch point through the state machine.
 * Calibration (raw ADC -> screen px) is the board profile's job
 * ([CXC-RM-M5] profile seam); this handles debounce, tap detection,
 * and widget dispatch only.  Call once per poll, before render_frame().
 */
void process_touch(const TouchPoint& tp);

/**
 * True once any tap has been dispatched since boot (used for the
 * [CXC-R-C9] touch-response acceptance log).
 */
bool any_tap_dispatched();

/**
 * Monotonic count of dispatched taps.  The R3 event-driven render loop
 * re-renders a full frame when this advances (a tap may have mutated
 * widget state anywhere), instead of rendering unconditionally.
 */
unsigned tap_serial();

#endif /* TOUCH_DISPATCH_H */
