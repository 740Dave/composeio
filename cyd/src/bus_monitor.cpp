/*
 * bus_monitor.cpp — BusMonitorWidget implementation
 * ([CXC-PRIM-TIER-PARITY] port of
 *  composeio/render/widgets/bus_monitor.py).
 *
 * Direct port of bus_monitor.py draw() + the _on_bus_message line format,
 * with the scroll model COMPOSED from the shared ScrollList substrate
 * (scroll_list.cpp) in uniform-row mode — the checklist.cpp idiom (lines
 * are a fixed 15px pitch, so this is a plain uniform-row consumer, not a
 * variable-height *_for one).
 *
 * Colours are the bus_monitor.py verbatim type tokens; the chrome uses
 * the style.py tokens the reference draws with, baked as constants the
 * same way checklist.cpp / chat_thread.cpp bake console_theme / style.
 */

#include "widgets/bus_monitor.h"
#include "canvas.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

/* -- geometry (bus_monitor.py verbatim) ------------------------------ */
static constexpr float kLineH    = 15.0f;   /* _LINE_H          */
static constexpr float kFontSize = 11.0f;   /* _FONT_SIZE       */
static constexpr float kPadX     = 8.0f;    /* rect.x + 8       */
static constexpr float kTopPad   = 4.0f;    /* rect.y + 4       */
static constexpr float kLinePad  = 4.0f;    /* body ly = box top + 4 */
static constexpr float kTextInsetX = 6.0f;  /* line x = box x + 6 */
static constexpr float kSbGutter = 8.0f;    /* reserved right gutter on overflow */
static constexpr float kRadius   = 6.0f;    /* textbox round-rect corner */

/* -- type -> colour (bus_monitor.py _TYPE_COLORS / _OTHER, verbatim) -- */
static constexpr uint8_t kState[3]      = {0xFF, 0xFF, 0xFF};
static constexpr uint8_t kEvent[3]      = {0x00, 0xBC, 0xD4};
static constexpr uint8_t kHeartbeat[3]  = {0x88, 0x88, 0x88};
static constexpr uint8_t kDiscovery[3]  = {0xFF, 0xD7, 0x00};
static constexpr uint8_t kConfigPush[3] = {0xE0, 0x40, 0xFB};
static constexpr uint8_t kOther[3]      = {0xAA, 0xAA, 0xAA};

/* -- style.py chrome tokens ------------------------------------------ */
static constexpr uint8_t kText[3]      = {0xD6, 0xD6, 0xD6};   /* TEXT       */
static constexpr uint8_t kTextboxBg[3] = {0x33, 0x33, 0x33};   /* TEXTBOX_BG */

/* Fonts: FONT_UI = "sans-serif", FONT_MONO = "monospace" (style.py). */
static const char* kFontUi   = "sans-serif";
static const char* kFontMono = "monospace";

/* ------------------------------------------------------------------- */

BusMonitorWidget::BusMonitorWidget(const char* id, const char* label,
                                   bool show_heartbeats)
    : Widget(id, "bus_monitor"), _show_heartbeats(show_heartbeats) {
    snprintf(_label, sizeof(_label), "%s", label ? label : "");
}

BusMonitorWidget::MsgType BusMonitorWidget::type_from_name(const char* s) {
    if (!s) return MsgType::Other;
    /* case-insensitive compare against the canon type strings */
    auto ieq = [](const char* a, const char* b) -> bool {
        while (*a && *b) {
            char ca = (*a >= 'A' && *a <= 'Z') ? (char)(*a + 32) : *a;
            char cb = *b;
            if (ca != cb) return false;
            a++; b++;
        }
        return *a == '\0' && *b == '\0';
    };
    if (ieq(s, "state"))       return MsgType::State;
    if (ieq(s, "event"))       return MsgType::Event;
    if (ieq(s, "heartbeat"))   return MsgType::Heartbeat;
    if (ieq(s, "time_sync"))   return MsgType::TimeSync;
    if (ieq(s, "discovery"))   return MsgType::Discovery;
    if (ieq(s, "config_push")) return MsgType::ConfigPush;
    return MsgType::Other;
}

static const uint8_t* type_color(BusMonitorWidget::MsgType t) {
    switch (t) {
        case BusMonitorWidget::MsgType::State:      return kState;
        case BusMonitorWidget::MsgType::Event:      return kEvent;
        case BusMonitorWidget::MsgType::Heartbeat:  return kHeartbeat;
        case BusMonitorWidget::MsgType::TimeSync:   return kHeartbeat;
        case BusMonitorWidget::MsgType::Discovery:  return kDiscovery;
        case BusMonitorWidget::MsgType::ConfigPush: return kConfigPush;
        default:                                    return kOther;
    }
}

void BusMonitorWidget::add_line(const char* text, MsgType type) {
    if (!text || !text[0]) return;
    if (_count >= kMaxLines) {
        /* ring: drop the oldest (front), keep the newest kMaxLines-1 */
        memmove(&_lines[0], &_lines[1], sizeof(Line) * (kMaxLines - 1));
        _count = kMaxLines - 1;
    }
    Line& l = _lines[_count];
    snprintf(l.text, sizeof(l.text), "%s", text);
    l.type = type;
    _count++;
}

void BusMonitorWidget::add_message(const char* stamp, const char* namespace_,
                                   const char* type, const char* value,
                                   const char* source) {
    MsgType mt = type_from_name(type);
    /* show_heartbeats filter (bus_monitor.py _on_bus_message). */
    if (!_show_heartbeats &&
        (mt == MsgType::Heartbeat || mt == MsgType::TimeSync)) {
        return;
    }
    /* "[stamp] namespace: type value" (+ " (source)" when present) —
     * bus_monitor.py verbatim (value_str "" -> a harmless trailing space). */
    char line[kLineCap];
    int n = snprintf(line, sizeof(line), "[%s] %s: %s %s",
                     stamp ? stamp : "", namespace_ ? namespace_ : "",
                     type ? type : "", value ? value : "");
    if (source && source[0] && n > 0 && n < (int)sizeof(line)) {
        snprintf(line + n, sizeof(line) - n, " (%s)", source);
    }
    add_line(line, mt);
}

void BusMonitorWidget::clear_lines() { _count = 0; }

void BusMonitorWidget::set_label(const char* label) {
    snprintf(_label, sizeof(_label), "%s", label ? label : "");
}

Size BusMonitorWidget::measure(const Constraints& constraints) {
    /* bus_monitor.py: Size(min(416, max_w), min(240, max_h)). */
    float w = std::min(416.0f, constraints.max_width);
    float h = std::min(240.0f, constraints.max_height);
    return Size{w, h};
}

void BusMonitorWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    canvas.clip_rect(rect.x, rect.y, rect.w, rect.h);

    float x = rect.x + kPadX;
    float y = rect.y + kTopPad;
    float w = std::max(120.0f, rect.w - 2.0f * kPadX);

    /* header label (bus_monitor.py: bold 13 UI, TEXT colour, y += 20). */
    if (_label[0]) {
        canvas.set_fill_color(kText[0], kText[1], kText[2]);
        canvas.set_font(kFontUi, 13.0f, "bold");
        TextMetrics m = canvas.measure_text(_label);
        canvas.draw_text(_label, x, y + m.ascent, "left");
        y += 20.0f;
    }

    /* textbox body (TEXTBOX_BG rounded rect). */
    float box_h = std::max(40.0f, rect.y + rect.h - y - 8.0f);
    canvas.set_fill_color(kTextboxBg[0], kTextboxBg[1], kTextboxBg[2]);
    canvas.fill_round_rect(x, y, w, box_h, kRadius);

    /* Drive the shared scroll substrate from the arranged textbox.  Lines
     * are uniform (kLineH) -> plain uniform-row mode (checklist idiom):
     * ScrollList owns content_h / max_scroll / clamp; row_gap=0, pad=4
     * reproduce the Python body anchor (ly = box top + 4). */
    _sl.top        = y;
    _sl.left       = x;
    _sl.width      = w;
    _sl.viewport_h = box_h;
    _sl.row_h      = kLineH;
    _sl.row_gap    = 0.0f;
    _sl.pad        = kLinePad;
    _sl.row_count  = _count;
    bool overflow  = _sl.max_scroll() > 0.0f;
    _sl.gutter_w   = overflow ? kSbGutter : 0.0f;   /* tap-zone paging */
    if (_init_bottom) {
        /* auto-scroll to end: pin to the newest line on first draw. */
        _sl.offset = _sl.max_scroll();
        _init_bottom = false;
    }
    _sl.set_offset(_sl.offset);                     /* re-clamp to range */

    /* body: last-N-that-fit, monospace, coloured per type. */
    canvas.save();
    canvas.clip_round_rect(x, y, w, box_h, kRadius);
    canvas.set_font(kFontMono, kFontSize, "regular");
    TextMetrics lm = canvas.measure_text("Ag");

    /* O(visible lines), not O(lines): the substrate's tight window is the
     * band-renderer cull (proven equal in scroll_list_selftest). */
    int first = 0, last = 0;
    _sl.visible_range(first, last);
    for (int i = first; i < last; i++) {
        const Line& l = _lines[i];
        const uint8_t* c = type_color(l.type);
        float ly = _sl.row_y(i);
        canvas.set_fill_color(c[0], c[1], c[2]);
        canvas.draw_text(l.text, x + kTextInsetX, ly + lm.ascent, "left");
    }
    canvas.restore();

    /* scrollbar thumb — only when the log overflows its fixed box (the
     * checklist.cpp idiom: a thin thumb, never a control that resizes). */
    if (overflow) {
        float content_h = _sl.content_height();
        float track_w = 4.0f;
        float sx = x + w - track_w - 1.0f;
        float sy = y + 2.0f;
        float sh = box_h - 4.0f;
        canvas.set_fill_color(kOther[0], kOther[1], kOther[2]);
        float thumb_h = std::max(20.0f, sh * (box_h / content_h));
        float travel = std::max(0.0f, sh - thumb_h);
        float frac = _sl.offset / _sl.max_scroll();
        canvas.fill_round_rect(sx, sy + travel * frac, track_w, thumb_h, 2.0f);
    }

    canvas.restore();
}

void BusMonitorWidget::on_tap(float x, float y) {
    /* Lines are NOT tap targets (a read-only log).  A tap is consumed ONLY
     * to page the overflowing log via the substrate's gutter tap-zones —
     * the honest interim on the CYD's tap-on-release touch tier (no
     * fabricated drag delta).  Geometry persists from the last draw(). */
    ScrollHit hit = _sl.classify(x, y);
    if (hit.kind == ScrollHitKind::PageUp) {
        _sl.page_up();
    } else if (hit.kind == ScrollHitKind::PageDown) {
        _sl.page_down();
    }
}
