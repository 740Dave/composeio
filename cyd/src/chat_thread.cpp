/*
 * chat_thread.cpp — ChatThreadWidget implementation ([SPEC:CXC-IC]).
 *
 * Port of composeio/render/widgets/chat_thread.py draw() + _paint_bubble +
 * _paint_packet, reduced to the flat band tier (see widgets/chat_thread.h for
 * the documented tier reductions — plain prose bubbles, no inline markdown /
 * tables / selection / image affordance).
 *
 * The flagship consumer of BOTH shared CYD substrates:
 *   - text_wrap.h  wraps each message body at the bubble inner width.  The
 *     line breaks are the SAME bytes the desktop widget makes: text_wrap ==
 *     MultiLineEditMixin._wrap_lines bit-for-bit (wrap_corpus), and
 *     chat_thread._layout_body == _wrap_lines for plain prose (proven 0
 *     mismatches by tests/render/... the chat_thread parity corpus).
 *   - scroll_list.h owns the scroll STATE + clamp + gutter paging via its
 *     NON-BREAKING variable-height *_for(content_h) overloads (bubbles are
 *     variable height; this widget computes its own cumulative y offsets and
 *     culls to the viewport itself).
 *
 * Colours are the console_theme DARK role tokens (CONSOLE.active defaults to
 * DARK — the theme the Skia golden renders with), baked as constants the
 * checklist.cpp / master_detail.cpp way.
 *
 * Reference: composeio/render/widgets/chat_thread.py,
 *            widgets/console_theme.py (draw_card / tokens / radii).
 */

#include "widgets/chat_thread.h"
#include "text_wrap.h"
#include "canvas.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

/* -- geometry (console_theme + chat_thread.py verbatim) -------------- */
static constexpr float kPadThreadY   = 22.0f;   /* PAD_THREAD_Y   */
static constexpr float kGapTurn      = 20.0f;   /* GAP_TURN       */
static constexpr float kThreadMeasure = 560.0f; /* THREAD_MEASURE */
static constexpr float kRadiusRead   = 16.0f;   /* RADIUS_READ    */
static constexpr float kRadiusBubble = 12.0f;   /* RADIUS_BUBBLE  */
static constexpr float kRadiusPacket = 9.0f;    /* RADIUS_PACKET  */
static constexpr float kLabelMono    = 9.6f;    /* LABEL_MONO     */
static constexpr int   kMaxWrapLines = 64;      /* per-bubble line cap  */

/* -- console_theme DARK role tokens ---------------------------------- */
static constexpr uint8_t kPanel[3]     = {0x0D, 0x12, 0x1B};  /* card bg    */
static constexpr uint8_t kLine[3]      = {0x1B, 0x24, 0x32};  /* card border*/
static constexpr uint8_t kPanel2[3]    = {0x10, 0x17, 0x24};  /* david fill */
static constexpr uint8_t kPanel3[3]    = {0x0A, 0x0F, 0x17};  /* packet fill*/
static constexpr uint8_t kLine2[3]     = {0x26, 0x31, 0x3F};  /* bubble edge*/
static constexpr uint8_t kInk[3]       = {0xE7, 0xEC, 0xF3};  /* body ink   */
static constexpr uint8_t kDim[3]       = {0x8B, 0x98, 0xAA};  /* packet body*/
static constexpr uint8_t kFaint[3]     = {0x55, 0x61, 0x6F};  /* who-line   */
static constexpr uint8_t kAccent[3]    = {0x4E, 0xA8, 0xFF};  /* accent     */
static constexpr uint8_t kAccentDim[4] = {78, 168, 255, 33};  /* reply fill */
static constexpr uint8_t kAccentLine[4]= {78, 168, 255, 107}; /* reply edge */
static constexpr uint8_t kCrit[3]      = {0xFF, 0x3B, 0x5C};  /* guidance   */
static constexpr uint8_t kCritLine[4]  = {255, 59, 92, 140};

static const char* kFontUi   = "sans-serif";
static const char* kFontMono = "monospace";

/* ------------------------------------------------------------------- */

ChatThreadWidget::ChatThreadWidget(const char* id, float font_size)
    : Widget(id, "chat_thread"),
      _font_size(font_size > 0.0f ? font_size : 12.0f) {}

ChatThreadWidget::Voice ChatThreadWidget::voice_from_name(const char* v) {
    if (!v) return Voice::David;
    auto ieq = [](const char* a, const char* b) -> bool {
        while (*a && *b) {
            char ca = (*a >= 'A' && *a <= 'Z') ? (char)(*a + 32) : *a;
            if (ca != *b) return false;
            a++; b++;
        }
        return *a == '\0' && *b == '\0';
    };
    if (ieq(v, "reply"))     return Voice::Reply;
    if (ieq(v, "narration")) return Voice::Narration;
    if (ieq(v, "narrate"))   return Voice::Narration;
    if (ieq(v, "harness"))   return Voice::Harness;
    return Voice::David;   /* david / human / unknown */
}

void ChatThreadWidget::add_message(const char* sender, const char* text,
                                   Voice voice, const char* stamp,
                                   const char* kind) {
    if (!text) text = "";
    Message m;
    snprintf(m.sender, sizeof(m.sender), "%s", sender ? sender : "");
    snprintf(m.text,   sizeof(m.text),   "%s", text);
    snprintf(m.stamp,  sizeof(m.stamp),  "%s", stamp ? stamp : "");
    snprintf(m.kind,   sizeof(m.kind),   "%s", kind ? kind : "");
    m.voice = voice;
    if (_count < kMaxMessages) {
        _msgs[_count++] = m;
    } else {
        /* ring: drop the oldest, keep the newest (chat_thread.py max_messages) */
        for (int i = 1; i < kMaxMessages; i++) _msgs[i - 1] = _msgs[i];
        _msgs[kMaxMessages - 1] = m;
    }
}

Size ChatThreadWidget::measure(const Constraints& c) {
    /* chat_thread.py: Size(min(440, max_w), min(360, max_h)). */
    return Size{std::min(440.0f, c.max_width),
                std::min(360.0f, c.max_height)};
}

/* -- small text helpers (chat_thread.py _display_name / upper) -------- */

static void ascii_upper(const char* in, char* out, int cap) {
    int i = 0;
    for (; in[i] && i < cap - 1; i++)
        out[i] = (in[i] >= 'a' && in[i] <= 'z') ? (char)(in[i] - 32) : in[i];
    out[i] = '\0';
}

/* _display_name: a bare all-lowercase handle capitalizes its first letter
 * ("david" -> "David"); a name already carrying case is shown verbatim. */
static void display_name(const char* in, char* out, int cap) {
    bool all_lower = in[0] != '\0';
    for (const char* p = in; *p; ++p)
        if (*p >= 'A' && *p <= 'Z') { all_lower = false; break; }
    snprintf(out, cap, "%s", in);
    if (all_lower && out[0] >= 'a' && out[0] <= 'z')
        out[0] = (char)(out[0] - 32);
}

/* draw_card, band-tier degrade (master_detail.cpp idiom): flat panel fill +
 * 1px line border (no ambient shadow / vertical gradient on this tier). */
static void draw_card(Canvas& canvas, float x, float y, float w, float h) {
    canvas.set_fill_color(kPanel[0], kPanel[1], kPanel[2]);
    canvas.fill_round_rect(x, y, w, h, kRadiusRead);
    canvas.set_stroke_color(kLine[0], kLine[1], kLine[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_round_rect(x + 0.5f, y + 0.5f, w - 1.0f, h - 1.0f,
                             kRadiusRead);
}

/* ------------------------------------------------------------------- */
/* per-turn measure/paint                                              */
/* ------------------------------------------------------------------- */

float ChatThreadWidget::paint_turn(Canvas& canvas, const Message& m,
                                   float x0, float cw, float y,
                                   bool measure_only) const {
    if (m.voice == Voice::Harness)
        return paint_packet(canvas, m, x0, cw, y, measure_only);
    return paint_bubble(canvas, m, x0, cw, y, measure_only);
}

float ChatThreadWidget::paint_bubble(Canvas& canvas, const Message& m,
                                     float x0, float cw, float y,
                                     bool measure_only) const {
    const bool accent_bubble = (m.voice == Voice::Reply ||
                                m.voice == Voice::Narration);
    const float body_size = _font_size;
    const float pad_bx = 14.0f, pad_by = 9.0f;
    const float cap = std::min(cw * 0.78f, kThreadMeasure);
    const float body_w = cap - 2.0f * pad_bx;
    const float line_h = body_size * 1.5f;
    const float who_h = 14.0f;

    /* Wrap pass (always) — count lines + max line width for bh/bw.  Split on
     * '\n' into paragraphs (text_wrap does not segment newlines) and greedy-
     * wrap each to body_w with the shared substrate (the master_detail.cpp
     * paragraph loop; the wrap bytes are chat_thread.py's). */
    canvas.set_font(kFontUi, body_size, "regular");
    float max_line_w = 0.0f;
    int   total_lines = 0;
    {
        float xs[kTextCap + 1];
        LineSpan spans[kMaxWrapLines];
        const char* body = m.text;
        int para_start = 0, i = 0;
        bool done = false;
        for (; !done; i++) {
            char ch = body[i];
            if (ch != '\0' && ch != '\n') continue;
            if (ch == '\0') done = true;
            int plen = i - para_start;
            if (plen <= 0) {
                total_lines += 1;                 /* blank line */
            } else {
                if (plen > kTextCap) plen = kTextCap;
                const char* para = body + para_start;
                int nlines = wrap_text_lines_display(canvas, para, plen,
                                                     body_w, xs, kTextCap + 1,
                                                     spans, kMaxWrapLines);
                if (nlines < 0) nlines = 0;
                int shown = std::min(nlines, kMaxWrapLines);
                for (int li = 0; li < shown; li++) {
                    int s0 = spans[li].start, s1 = spans[li].end;
                    while (s1 > s0 && para[s1 - 1] == ' ') s1--;
                    char lb[kTextCap + 1];
                    int seg = s1 - s0;
                    if (seg > kTextCap) seg = kTextCap;
                    memcpy(lb, para + s0, seg);
                    lb[seg] = '\0';
                    float w = canvas.measure_text(lb).width;
                    if (w > max_line_w) max_line_w = w;
                }
                total_lines += shown;
            }
            para_start = i + 1;
        }
    }
    if (total_lines < 1) total_lines = 1;
    const float content_h = (float)total_lines * line_h;
    const float bh = content_h + 2.0f * pad_by;
    const float bw = std::min(cap, max_line_w + 2.0f * pad_bx);
    const float total = who_h + bh;
    if (measure_only) return total;

    const float bx = accent_bubble ? x0 : (x0 + cw - bw);

    /* who-line (mono LABEL_MONO, faint) — chat_thread.py exact format:
     *   accent voices: "ARCHITECT (Reply)"/"ARCHITECT (Narration)" [@stamp]
     *   david/human:   "David" [@stamp], right-anchored. */
    canvas.set_font(kFontMono, kLabelMono, "regular");
    TextMetrics wm = canvas.measure_text("Ag");
    float wbase = y + wm.ascent;
    char who[kSenderCap + 48];
    canvas.set_fill_color(kFaint[0], kFaint[1], kFaint[2]);
    if (accent_bubble) {
        char up[kSenderCap];
        ascii_upper(m.sender[0] ? m.sender : "architect", up, sizeof(up));
        const char* vl = (m.voice == Voice::Narration) ? "Narration" : "Reply";
        if (m.stamp[0])
            snprintf(who, sizeof(who), "%s (%s) @%s", up, vl, m.stamp);
        else
            snprintf(who, sizeof(who), "%s (%s)", up, vl);
        canvas.draw_text(who, bx + 2.0f, wbase, "left");
    } else {
        char disp[kSenderCap];
        display_name(m.sender[0] ? m.sender : "david", disp, sizeof(disp));
        if (m.stamp[0])
            snprintf(who, sizeof(who), "%s @%s", disp, m.stamp);
        else
            snprintf(who, sizeof(who), "%s", disp);
        float ww = canvas.measure_text(who).width;
        canvas.draw_text(who, bx + bw - ww - 2.0f, wbase, "left");
    }
    float by = y + who_h;

    /* bubble box (single-radius approximation, chat_thread.py) */
    if (accent_bubble) {
        canvas.set_fill_color(kAccentDim[0], kAccentDim[1], kAccentDim[2],
                              kAccentDim[3]);
        canvas.fill_round_rect(bx, by, bw, bh, kRadiusBubble);
        canvas.set_stroke_color(kAccentLine[0], kAccentLine[1], kAccentLine[2],
                                kAccentLine[3]);
    } else {
        canvas.set_fill_color(kPanel2[0], kPanel2[1], kPanel2[2]);
        canvas.fill_round_rect(bx, by, bw, bh, kRadiusBubble);
        canvas.set_stroke_color(kLine2[0], kLine2[1], kLine2[2]);
    }
    canvas.set_stroke_width(1.0f);
    canvas.stroke_round_rect(bx, by, bw, bh, kRadiusBubble);

    /* body lines (ink) — the SAME paragraph-wrap, drawn */
    canvas.set_font(kFontUi, body_size, "regular");
    TextMetrics bm = canvas.measure_text("Ag");
    float ly = by + pad_by;
    {
        float xs[kTextCap + 1];
        LineSpan spans[kMaxWrapLines];
        const char* body = m.text;
        int para_start = 0, i = 0;
        bool done = false;
        for (; !done; i++) {
            char ch = body[i];
            if (ch != '\0' && ch != '\n') continue;
            if (ch == '\0') done = true;
            int plen = i - para_start;
            if (plen <= 0) {
                ly += line_h;
            } else {
                if (plen > kTextCap) plen = kTextCap;
                const char* para = body + para_start;
                int nlines = wrap_text_lines_display(canvas, para, plen,
                                                     body_w, xs, kTextCap + 1,
                                                     spans, kMaxWrapLines);
                if (nlines < 0) nlines = 0;
                int shown = std::min(nlines, kMaxWrapLines);
                for (int li = 0; li < shown; li++) {
                    int s0 = spans[li].start, s1 = spans[li].end;
                    while (s1 > s0 && para[s1 - 1] == ' ') s1--;
                    char lb[kTextCap + 1];
                    int seg = s1 - s0;
                    if (seg > kTextCap) seg = kTextCap;
                    memcpy(lb, para + s0, seg);
                    lb[seg] = '\0';
                    canvas.set_fill_color(kInk[0], kInk[1], kInk[2]);
                    canvas.draw_text(lb, bx + pad_bx, ly + bm.ascent, "left");
                    ly += line_h;
                }
            }
            para_start = i + 1;
        }
    }
    return total;
}

float ChatThreadWidget::paint_packet(Canvas& canvas, const Message& m,
                                     float x0, float cw, float y,
                                     bool measure_only) const {
    /* Reduced harness packet: a centered mono card (no badge/attrs/glow —
     * the documented band-tier chrome reduction).  Border colour by kind
     * variant; a guidance packet keeps its 3px crit left bar. */
    const float body_size = _font_size * 0.82f;
    const float pw = std::min(cw * 0.92f, 620.0f);
    const float px = x0 + (cw - pw) / 2.0f;
    const float pad_px = 12.0f, pad_py = 9.0f;
    const float inner_w = pw - 2.0f * pad_px;
    const float line_h = body_size * 1.45f;
    const float hrow_h = 16.0f;

    /* variant from kind (chat_thread.py _packet_variant) */
    char kc[ChatThreadWidget::kSenderCap];
    ascii_upper(m.kind, kc, sizeof(kc));
    bool guidance = strstr(kc, "GUIDANCE") != nullptr;
    bool report   = strstr(kc, "REPORT")   != nullptr;

    canvas.set_font(kFontMono, body_size, "regular");
    int total_lines = 0;
    {
        float xs[kTextCap + 1];
        LineSpan spans[kMaxWrapLines];
        const char* body = m.text;
        int para_start = 0, i = 0;
        bool done = false;
        for (; !done; i++) {
            char ch = body[i];
            if (ch != '\0' && ch != '\n') continue;
            if (ch == '\0') done = true;
            int plen = i - para_start;
            if (plen <= 0) {
                total_lines += 1;
            } else {
                if (plen > kTextCap) plen = kTextCap;
                const char* para = body + para_start;
                int nlines = wrap_text_lines_display(canvas, para, plen,
                                                     inner_w, xs, kTextCap + 1,
                                                     spans, kMaxWrapLines);
                if (nlines < 0) nlines = 0;
                total_lines += std::min(nlines, kMaxWrapLines);
            }
            para_start = i + 1;
        }
    }
    const float body_h = (float)total_lines * line_h;
    const float ph = 2.0f * pad_py + hrow_h + body_h;
    if (measure_only) return ph;

    /* packet card */
    canvas.set_fill_color(kPanel3[0], kPanel3[1], kPanel3[2]);
    canvas.fill_round_rect(px, y, pw, ph, kRadiusPacket);
    if (report) {
        canvas.set_stroke_color(kAccentLine[0], kAccentLine[1], kAccentLine[2],
                                kAccentLine[3]);
    } else if (guidance) {
        canvas.set_stroke_color(kCritLine[0], kCritLine[1], kCritLine[2],
                                kCritLine[3]);
    } else {
        canvas.set_stroke_color(kLine2[0], kLine2[1], kLine2[2]);
    }
    canvas.set_stroke_width(1.0f);
    canvas.stroke_round_rect(px, y, pw, ph, kRadiusPacket);
    if (guidance) {
        canvas.set_fill_color(kCrit[0], kCrit[1], kCrit[2]);
        canvas.fill_rect(px, y + 2.0f, 3.0f, ph - 4.0f);
    }

    /* header: "FROM HARNESS" + KIND (reduced — no tracked spacing / badge) */
    canvas.set_font(kFontMono, 8.0f, "regular");
    TextMetrics hm = canvas.measure_text("Ag");
    float hbase = y + pad_py + hm.ascent;
    canvas.set_fill_color(kFaint[0], kFaint[1], kFaint[2]);
    canvas.draw_text("FROM HARNESS", px + pad_px, hbase, "left");
    float fw = canvas.measure_text("FROM HARNESS ").width;
    const char* kind = m.kind[0] ? kc : "PACKET";
    if (report)        canvas.set_fill_color(kAccent[0], kAccent[1], kAccent[2]);
    else if (guidance) canvas.set_fill_color(kCrit[0], kCrit[1], kCrit[2]);
    else               canvas.set_fill_color(kDim[0], kDim[1], kDim[2]);
    canvas.draw_text(kind, px + pad_px + fw + 6.0f, hbase, "left");

    /* body (dim) — same paragraph-wrap */
    canvas.set_font(kFontMono, body_size, "regular");
    TextMetrics bm = canvas.measure_text("Ag");
    float ly = y + pad_py + hrow_h;
    {
        float xs[kTextCap + 1];
        LineSpan spans[kMaxWrapLines];
        const char* body = m.text;
        int para_start = 0, i = 0;
        bool done = false;
        for (; !done; i++) {
            char ch = body[i];
            if (ch != '\0' && ch != '\n') continue;
            if (ch == '\0') done = true;
            int plen = i - para_start;
            if (plen <= 0) {
                ly += line_h;
            } else {
                if (plen > kTextCap) plen = kTextCap;
                const char* para = body + para_start;
                int nlines = wrap_text_lines_display(canvas, para, plen,
                                                     inner_w, xs, kTextCap + 1,
                                                     spans, kMaxWrapLines);
                if (nlines < 0) nlines = 0;
                int shown = std::min(nlines, kMaxWrapLines);
                for (int li = 0; li < shown; li++) {
                    int s0 = spans[li].start, s1 = spans[li].end;
                    while (s1 > s0 && para[s1 - 1] == ' ') s1--;
                    char lb[kTextCap + 1];
                    int seg = s1 - s0;
                    if (seg > kTextCap) seg = kTextCap;
                    memcpy(lb, para + s0, seg);
                    lb[seg] = '\0';
                    canvas.set_fill_color(kDim[0], kDim[1], kDim[2]);
                    canvas.draw_text(lb, px + pad_px, ly + bm.ascent, "left");
                    ly += line_h;
                }
            }
            para_start = i + 1;
        }
    }
    return ph;
}

/* ------------------------------------------------------------------- */
/* draw                                                                */
/* ------------------------------------------------------------------- */

void ChatThreadWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    draw_card(canvas, rect.x, rect.y, rect.w, rect.h);

    const float pad_x = std::max(18.0f, std::min(40.0f, rect.w * 0.04f));
    const float pad_y = kPadThreadY;
    const float content_x = rect.x + pad_x;
    const float content_w = std::max(60.0f, rect.w - 2.0f * pad_x);
    const float avail = rect.h - 2.0f * pad_y;

    canvas.save();
    canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, kRadiusRead);

    if (_count == 0) {
        canvas.set_font(kFontUi, 11.0f, "regular");
        canvas.set_fill_color(kFaint[0], kFaint[1], kFaint[2]);
        canvas.draw_text("awaiting the thread\xE2\x80\xA6",
                         rect.x + rect.w / 2.0f, rect.y + rect.h / 2.0f,
                         "center");
        canvas.restore();
        canvas.restore();
        return;
    }

    /* measure pass: per-turn block heights (chat_thread.py draw()) */
    float heights[kMaxMessages];
    float content_h = 0.0f;
    for (int j = 0; j < _count; j++) {
        heights[j] = paint_turn(canvas, _msgs[j], content_x, content_w,
                                0.0f, true);
        if (j) content_h += kGapTurn;
        content_h += heights[j];
    }
    _content_h = content_h;

    /* scroll substrate — variable-height mode, over the inner text region */
    _sl.top        = rect.y + pad_y;
    _sl.left       = rect.x;
    _sl.width      = rect.w;
    _sl.viewport_h = avail;
    bool overflow  = _sl.max_scroll_for(content_h) > 0.0f;
    _sl.gutter_w   = overflow ? 10.0f : 0.0f;
    if (_init_bottom) {
        _sl.offset = content_h;       /* pin to newest on first draw */
        _init_bottom = false;
    }
    _sl.set_offset_for(content_h);    /* re-clamp into [0, max] */

    /* bottom-anchor when under-full; else scrolled (offset=max shows newest) */
    float y;
    if (content_h <= avail)
        y = rect.y + pad_y + (avail - content_h);
    else
        y = rect.y + pad_y - _sl.offset;

    for (int j = 0; j < _count; j++) {
        if (y > rect.y + rect.h) break;
        if (y + heights[j] >= rect.y)
            paint_turn(canvas, _msgs[j], content_x, content_w, y, false);
        y += heights[j] + kGapTurn;
    }

    /* scrollbar — thin track + thumb on the right inner edge, on overflow */
    if (overflow) {
        float track_w = 4.0f;
        float tx = rect.x + rect.w - 10.0f;
        float ty = rect.y + pad_y;
        float th = avail;
        canvas.set_fill_color(kLine[0], kLine[1], kLine[2]);
        canvas.fill_round_rect(tx, ty, track_w, th, track_w / 2.0f);
        float m = _sl.max_scroll_for(content_h);
        float thumb_h = std::max(24.0f, th * (avail / content_h));
        float travel = std::max(0.0f, th - thumb_h);
        float frac = (m <= 0.0f) ? 0.0f : (_sl.offset / m);   /* 0=top 1=bot */
        canvas.set_fill_color(kFaint[0], kFaint[1], kFaint[2]);
        canvas.fill_round_rect(tx, ty + travel * frac, track_w, thumb_h,
                               track_w / 2.0f);
    }

    canvas.restore();   /* clip */
    canvas.restore();   /* top */
}

void ChatThreadWidget::on_tap(float x, float y) {
    /* Bubbles are NOT tap targets on this tier (the Skia widget's drag-select
     * + copy is Skia-only — see the header reductions).  A tap is consumed
     * ONLY to page the overflowing thread via the substrate's gutter tap-
     * zones — the honest interim on the CYD's tap-on-release touch tier (no
     * fabricated drag delta).  Geometry + _content_h persist from the last
     * draw(); next frame redraws from the new offset. */
    ScrollHit hit = _sl.classify_var(x, y, _content_h);
    if (hit.kind == ScrollHitKind::PageUp) {
        _sl.page_up_for(_content_h);
    } else if (hit.kind == ScrollHitKind::PageDown) {
        _sl.page_down_for(_content_h);
    }
}
