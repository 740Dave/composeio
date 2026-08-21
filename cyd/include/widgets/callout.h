/*
 * callout.h — Annotation chip + leader line ([CXC-PRIM-TIER-PARITY] port
 * of composeio/render/widgets/callout.py).
 *
 * A labeled chip with a leader line pointing at a target coordinate,
 * designed to sit in a pixel_stack ABOVE an image and annotate a point.
 * Built from draw_line + fill/stroke_circle + fill/stroke_round_rect +
 * text — all contract primitives.
 *
 * Bus binding (R3): a STRING value replaces the chip text
 * (RAttr::CalloutText).  The Skia tier also accepts a dict payload
 * (text/x/y/visible/color); a structured dict is not representable on
 * the reactive DslValue tier, so only the string form binds here (the
 * static target/offset/color render from config).
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_CALLOUT_H
#define WIDGETS_CALLOUT_H

#include "widget.h"
#include <cstdint>

class CalloutWidget : public Widget {
public:
    CalloutWidget(const char* id, const char* text = "");

    void set_text(const char* t);
    void set_target(float x, float y) { _target_x = x; _target_y = y; }
    void set_offset(float dx, float dy) { _offset_x = dx; _offset_y = dy; }
    void set_color(uint8_t r, uint8_t g, uint8_t b) {
        _color[0] = r; _color[1] = g; _color[2] = b;
    }
    void set_font_size(float s) { _font_size = s; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    char _text[64];
    float _target_x = 0.5f, _target_y = 0.5f;   /* 0-1 fraction OR px */
    float _offset_x = -70.0f, _offset_y = -46.0f;
    uint8_t _color[3] = {0x26, 0xA6, 0x9A};      /* teal default */
    float _font_size = 12.0f;
};

#endif /* WIDGETS_CALLOUT_H */
