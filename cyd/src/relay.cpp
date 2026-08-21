/*
 * relay.cpp — RelayWidget implementation ([CXC-PRIM-TIER-PARITY]).
 *
 * Direct port of composeio/render/widgets/relay.py draw + _resolve_color
 * (and the style.py color constants captured at M2).  A flat colored
 * circle (r=20) driven by a state-engine value, with a label below.
 *
 * Reference: composeio/render/widgets/relay.py, widgets/style.py
 */

#include "widgets/relay.h"
#include "canvas.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <strings.h>

/* style.py constants (verbatim) */
static constexpr uint8_t kGray[3]   = {0x55, 0x55, 0x55};
static constexpr uint8_t kGreen[3]  = {0x2E, 0xCC, 0x71};
static constexpr uint8_t kYellow[3] = {0xF1, 0xC4, 0x0F};
static constexpr uint8_t kRed[3]    = {0xE7, 0x4C, 0x3C};
static constexpr uint8_t kOutline[3]= {0x33, 0x33, 0x33};
static constexpr uint8_t kText[3]   = {0xD6, 0xD6, 0xD6};

/* style.interpolate_color: int(a + (b - a) * frac), per channel. */
static void interpolate(const uint8_t a[3], const uint8_t b[3], double frac,
                        uint8_t out[3]) {
    for (int i = 0; i < 3; i++)
        out[i] = (uint8_t)((double)a[i] + ((double)b[i] - (double)a[i]) * frac);
}

RelayWidget::RelayWidget(const char* id, const char* engine_type,
                         const char* label)
    : Widget(id, "relay"), _label(label) {
    _value[0] = '\0';
    if (engine_type && strcasecmp(engine_type, "tri_level") == 0)
        _engine = Engine::TriLevel;
    else if (engine_type && strcasecmp(engine_type, "stop_light") == 0)
        _engine = Engine::StopLight;
    else if (engine_type && strcasecmp(engine_type, "enum") == 0)
        _engine = Engine::Enum;
    else if (engine_type && strcasecmp(engine_type, "continuous") == 0)
        _engine = Engine::Continuous;
    else
        _engine = Engine::Binary;
}

void RelayWidget::add_enum_value(const char* v) {
    if (!v || _enum_count >= kMaxEnumValues) return;
    snprintf(_enum_values[_enum_count], sizeof(_enum_values[0]), "%s", v);
    _enum_count++;
}

void RelayWidget::set_value(const char* v) {
    snprintf(_value, sizeof(_value), "%s", v ? v : "");
}

/* Port of RelayWidget._resolve_color. */
void RelayWidget::resolve_color(uint8_t rgb[3]) const {
    auto set = [&](const uint8_t c[3]) {
        rgb[0] = c[0]; rgb[1] = c[1]; rgb[2] = c[2];
    };
    const char* v = _value;

    switch (_engine) {
        case Engine::Binary:
            if (strcmp(v, "on") == 0) set(kGreen); else set(kGray);
            return;
        case Engine::TriLevel:
            if (strcmp(v, "full") == 0) set(kGreen);
            else if (strcmp(v, "half") == 0) set(kYellow);
            else set(kGray);
            return;
        case Engine::StopLight:
            if (strcmp(v, "green") == 0) set(kGreen);
            else if (strcmp(v, "yellow") == 0) set(kYellow);
            else if (strcmp(v, "red") == 0) set(kRed);
            else set(kGray);
            return;
        case Engine::Enum: {
            if (_enum_count <= 0) { set(kGray); return; }
            if (strcmp(v, _enum_values[0]) == 0) { set(kGray); return; }
            if (strcmp(v, _enum_values[_enum_count - 1]) == 0) {
                set(kGreen); return;
            }
            int idx = -1;
            for (int i = 0; i < _enum_count; i++)
                if (strcmp(v, _enum_values[i]) == 0) { idx = i; break; }
            if (idx < 0) { set(kGray); return; }
            double frac = (double)idx / (double)std::max(_enum_count - 1, 1);
            interpolate(kGray, kGreen, frac, rgb);
            return;
        }
        case Engine::Continuous: {
            char* end = nullptr;
            double fv = strtod(v, &end);
            if (end == v) { set(kGray); return; }   /* not a number */
            double frac = fv / 255.0;
            if (frac < 0.0) frac = 0.0;
            if (frac > 1.0) frac = 1.0;
            interpolate(kGray, kGreen, frac, rgb);
            return;
        }
    }
    set(kGray);
}

Size RelayWidget::measure(const Constraints& constraints) {
    float h = 56.0f + (_label ? 22.0f : 8.0f);
    float w = std::min(72.0f, constraints.max_width);
    return Size{w, std::min(h, constraints.max_height)};
}

void RelayWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    float cx = rect.x + rect.w / 2.0f;
    float cy = rect.y + 8.0f + 20.0f;   /* 56px canvas, oval 8..48 -> r=20 */

    uint8_t col[3];
    resolve_color(col);
    canvas.set_fill_color(col[0], col[1], col[2]);
    canvas.fill_circle(cx, cy, 20.0f);
    canvas.set_stroke_color(kOutline[0], kOutline[1], kOutline[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_circle(cx, cy, 20.0f);

    if (_label && _label[0]) {
        canvas.set_fill_color(kText[0], kText[1], kText[2]);
        canvas.set_font("sans-serif", 12.0f, "regular");
        TextMetrics m = canvas.measure_text(_label);
        canvas.draw_text(_label, cx, cy + 20.0f + 4.0f + m.ascent, "center");
    }
    canvas.restore();
}
