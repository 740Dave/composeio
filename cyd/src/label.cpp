/*
 * label.cpp — Label widget implementation
 *
 * Port of Python LabelWidget draw behavior to idiomatic embedded C++.
 * Simple text display with font size, color, alignment, and weight.
 *
 * Reference: composeio/render/widgets/label.py
 */

#include "widgets/label.h"
#include "canvas.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

/* -------------------------------------------------------------------
 * Construction
 * ------------------------------------------------------------------- */

LabelWidget::LabelWidget(const char* id, const char* text, float font_size,
                         const char* align, const char* weight,
                         const char* font_family)
    : Widget(id, "label"), _text(text), _font_size(font_size),
      _align(align), _weight(weight),
      _font_family(font_family ? font_family : "sans-serif") {}

/* -------------------------------------------------------------------
 * Measure
 * ------------------------------------------------------------------- */

Size LabelWidget::measure(const Constraints& constraints) {
    /* Rough estimate — actual measurement needs a canvas instance */
    float char_width = _font_size * 0.6f;
    int len = 0;
    if (_text) { while (_text[len]) len++; }
    float w = std::min(char_width * len, constraints.max_width);
    float h = _font_size * 1.4f;
    return Size{w, h};
}

/* -------------------------------------------------------------------
 * Draw — matches Python reference behavior
 * ------------------------------------------------------------------- */

void LabelWidget::draw(Canvas& canvas, const Rect& rect) {
    if (!_text || !_text[0]) return;

    canvas.save();
    canvas.set_fill_color(_color_r, _color_g, _color_b);
    canvas.set_font(_font_family, _font_size, _weight);

    float x;
    if (_align[0] == 'c') {
        x = rect.x + rect.w / 2.0f;
    } else if (_align[0] == 'r') {
        x = rect.x + rect.w;
    } else {
        x = rect.x;
    }

    canvas.draw_text(_text, x, baseline_y(rect), _align);

    canvas.restore();
}

/* -------------------------------------------------------------------
 * Sub-widget damage ([CXC-ER-DAMAGE])
 *
 * THE LINE BOX, not the rect.  draw() puts the baseline at
 * baseline_y(rect) and draw_text lays glyphs out from
 * (baseline - ascent) downward, so every pixel this widget can write
 * lives in [draw_y, draw_y + line_height] — the same rows for the old
 * string and the new one, because only the X advance differs.  The
 * repaint of those rows is a full re-render of the scene there, so the
 * OLD glyphs are erased by the background rather than needing their
 * own span.
 *
 * SUPERSET DISCIPLINE — the bound is derived, not assumed.  In
 * canvas_esp32 draw_text a glyph's top row is
 *     igy = int(draw_y + (ascent - bearing_y) * scale + 0.5)
 * and its bottom is igy + int(glyph_h * scale + 0.5).  Measured across
 * EVERY atlas in font_atlas.h (all sizes, regular + bold + COMMP):
 *   - min(ascent - bearing_y) == 0   -> no glyph rises above draw_y
 *   - max(ascent - bearing_y + h - line_height) == 1  -> at most one
 *     row below draw_y + line_height (the 6 px atlas's ')')
 * Two roundings can each add half a row.  So
 *     [draw_y - 1, draw_y + (line_height + 1) * scale + 1]
 * bounds it, and kLabelDamagePad adds a further row at each edge.
 * Confirmed on hardware by the row-CRC damage audit across the digit-
 * count matrix (9->10, 99->100, "---"->"0").
 * ------------------------------------------------------------------- */

static constexpr float kLabelDamagePad = 1.0f;

void LabelWidget::damage_snapshot(const char* attr, WidgetDamage& out) const {
    out.valid = false;
    if (!attr || strcmp(attr, "text") != 0) return;
    const char* t = _text ? _text : "";
    if (strlen(t) >= sizeof(out.text)) return;   /* can't snapshot exactly */
    out.valid = true;
    out.num = _font_size;
    snprintf(out.text, sizeof(out.text), "%s", t);
}

bool LabelWidget::damage_rows(const char* attr, const WidgetDamage& before,
                              const Rect& rect, Canvas& metrics,
                              DamageSpan& out) const {
    if (!before.valid) return false;
    if (!attr || strcmp(attr, "text") != 0) return false;
    if (before.num != _font_size) return false;  /* line box moved */

    const char* now = _text ? _text : "";
    if (strcmp(before.text, now) == 0) {
        /* The bus spoke but the glass already says this.  Nothing. */
        out.y0 = 1.0f;
        out.y1 = 0.0f;
        return true;
    }

    /* Real metrics from the canvas that will do the drawing — the font
     * state is reset at the start of every render pass, so setting it
     * here (outside one) cannot leak into a frame. */
    metrics.set_font(_font_family, _font_size, _weight);
    TextMetrics m = metrics.measure_text(now);
    if (m.height <= 0.0f) return false;          /* no atlas -> unknown */

    float draw_y = baseline_y(rect) - m.ascent;
    /* Bound = draw_y + (line_height + 1) * scale + 1, expressed without
     * the raw atlas numbers: m.height == line_height * scale, and the
     * smallest line_height in font_atlas.h is 7, so scale <= m.height/7
     * and (line_height + 1) * scale <= m.height * 8/7.  The bitmap-font
     * fallback (no atlas) is tighter still: draw_y + m.height + 1. */
    out.y0 = draw_y - 1.0f - kLabelDamagePad;
    out.y1 = draw_y + m.height * (8.0f / 7.0f) + 1.0f + kLabelDamagePad;
    /* One line of text repaints inside a single wavefront either way —
     * a label has no growth direction, so it never asks for one. */
    return true;
}
