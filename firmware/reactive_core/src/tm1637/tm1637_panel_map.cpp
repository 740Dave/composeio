/*
 * tm1637_panel_map.cpp — the PURE half of the TM1637 panel: the thrust->display
 * mapping and the subscribed-namespace list.  No Arduino, no GPIO; compiled
 * into BOTH env:native_test (host-tested) and the combined device env.
 *
 * Kept pure and separate from tm1637_panel.cpp on purpose: a bit-banged surface
 * is exactly the class whose defects are invisible on a bench (a wrong justify
 * still shows well-formed glyphs), so the value->digits decision MUST be
 * assertable on the host ([CXC-VISION-EPISTEMICS]).
 */
#include "tm1637_panel.h"

void tm1637_panel_format_thrust(const Tm1637& dev, double thrust, uint8_t* out) {
    /* Thrust is a percentage 0..100 ([DD-R503... the engine-thrust panel:
     * "THRUST IS A PERCENTAGE (0..100), NOT AN 8-BIT CODE"]).  Clamp to range
     * so a stray out-of-band value cannot paint a nonsense reading, then hand
     * the driver the double: tm1637_format_number TRUNCATES toward zero and
     * right-justifies blank-padded, placing a <=3-char value on the rightmost
     * three digits of a 4-digit tube.  No rounding here — rounding 99.7 to
     * "100" would be an over-range the operator cannot see, the exact reason
     * the driver truncates. */
    if (thrust < 0.0) thrust = 0.0;
    if (thrust > 100.0) thrust = 100.0;
    Tm1637Format fmt;
    fmt.decimals = -1;    /* integer render */
    fmt.pad_zero = false; /* blank pad (the shared seven_segment policy) */
    tm1637_format_number(dev, thrust, fmt, out);
}

size_t tm1637_panel_namespaces(const char** out, size_t cap) {
    static const char* kNs[TM1637_PANEL_TUBES] = {
        TM1637_PANEL_NS_TUBE1,
        TM1637_PANEL_NS_TUBE2,
        TM1637_PANEL_NS_TUBE3,
        TM1637_PANEL_NS_TUBE4,
    };
    size_t n = 0;
    for (size_t i = 0; i < TM1637_PANEL_TUBES && n < cap; i++) out[n++] = kNs[i];
    return n;
}
