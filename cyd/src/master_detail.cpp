/*
 * master_detail.cpp — MasterDetailWidget implementation
 * ([CXC-PRIM-TIER-PARITY]).
 *
 * Port of composeio/render/widgets/master_detail.py draw() + on_tap():
 * the LEFT nav column geometry, selection chrome and label rendering are
 * VERBATIM (constants + accent-dim fill + 2.5px accent bar + ellipsized
 * label); the nav scroll model is COMPOSED from the shared ScrollList
 * substrate (scroll_list.cpp — the checklist.cpp idiom), and the RIGHT
 * detail body is wrapped with the shared text_wrap substrate.  See
 * widgets/master_detail.h for the documented tier reduction of the
 * detail pane (child-container layout the flat pixel_stack tier lacks).
 *
 * Colours are the console_theme DARK role tokens (CONSOLE.active defaults
 * to DARK — the theme the Skia golden renders with), baked as constants
 * the same way checklist.cpp / tab.cpp bake their style source.
 *
 * Reference: composeio/render/widgets/master_detail.py,
 *            widgets/console_theme.py (draw_card / ellipsize / tokens).
 */

#include "widgets/master_detail.h"
#include "text_wrap.h"
#include "canvas.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

/* -- geometry (master_detail.py verbatim) ---------------------------- */
static constexpr float kNavGap    = 18.0f;   /* NAV_GAP     */
static constexpr float kRowH      = 42.0f;   /* _ROW_H      */
static constexpr float kRowGap    = 4.0f;    /* _ROW_GAP    */
static constexpr float kNavPadTop = 6.0f;    /* _NAV_PAD_TOP */
static constexpr float kNavPadX   = 12.0f;   /* _NAV_PAD_X  */
static constexpr float kRadiusCard = 14.0f;  /* console_theme RADIUS_CARD */
static constexpr float kSbGutter  = 8.0f;    /* scrollbar gutter on overflow */
static constexpr float kNavFont   = 13.5f;   /* nav label size */

/* detail pane chrome (the flat-tier reduction — see header) */
static constexpr float kDetailPadX   = 8.0f;
static constexpr float kDetailPadTop = 6.0f;
static constexpr float kTitleFont    = 15.0f;
static constexpr float kBodyFont     = 13.5f;

/* -- console_theme DARK role tokens ---------------------------------- */
static constexpr uint8_t kInk[3]    = {0xE7, 0xEC, 0xF3};  /* ink   */
static constexpr uint8_t kDim[3]    = {0x8B, 0x98, 0xAA};  /* dim   */
static constexpr uint8_t kFaint[3]  = {0x55, 0x61, 0x6F};  /* faint */
static constexpr uint8_t kAccent[3] = {0x4E, 0xA8, 0xFF};  /* accent */
static constexpr uint8_t kPanel[3]  = {0x0D, 0x12, 0x1B};  /* panel (nav card) */
static constexpr uint8_t kLine[3]   = {0x1B, 0x24, 0x32};  /* line (border) */
/* accent_dim = (78,168,255,33) — the selected-row wash */
static constexpr uint8_t kAccentDim[4] = {0x4E, 0xA8, 0xFF, 33};

/* Fonts: FONT_UI = "sans-serif" (console_theme). */
static const char* kFontUi = "sans-serif";

MasterDetailWidget::PublishFn MasterDetailWidget::s_publish = nullptr;

/* ------------------------------------------------------------------- */

MasterDetailWidget::MasterDetailWidget(const char* id, float nav_width,
                                       const char* nav_namespace,
                                       int initial_tab)
    : Widget(id, "master_detail"),
      _nav_width(nav_width > 0.0f ? nav_width : 264.0f),
      _nav_namespace(nav_namespace ? nav_namespace : "_app/config/section"),
      _active(initial_tab < 0 ? 0 : initial_tab) {}

void MasterDetailWidget::add_section(const char* label, const char* detail) {
    if (_count >= kMaxSections) return;
    Section& s = _sections[_count];
    snprintf(s.label, sizeof(s.label), "%s", label ? label : "");
    snprintf(s.detail, sizeof(s.detail), "%s", detail ? detail : "");
    _count++;
}

void MasterDetailWidget::set_active(int index) {
    /* master_detail.py routes through TabWidget.set_active: clamp active
     * into range first, then require a valid, different index. */
    if (_count == 0) return;
    if (_active >= _count) _active = _count - 1;
    if (index != _active && index >= 0 && index < _count) _active = index;
}

const char* MasterDetailWidget::nav_value(int index, char* buf,
                                          int cap) const {
    if (!buf || cap <= 0) return buf;
    if (index < 0 || index >= _count) {
        snprintf(buf, cap, "%d", index);
        return buf;
    }
    /* master_detail.py nav_value: str(label).lower().  ASCII-faithful
     * (byte == char for the label set this tier renders). */
    int i = 0;
    const char* p = _sections[index].label;
    for (; p[i] && i < cap - 1; i++) {
        char c = p[i];
        buf[i] = (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
    }
    buf[i] = '\0';
    return buf;
}

int MasterDetailWidget::index_for(const char* value) const {
    if (!value) return -1;
    /* master_detail.py _index_for(str): str(value).strip().lower() matched
     * against each section's lowercased label. */
    const char* v = value;
    while (*v == ' ' || *v == '\t' || *v == '\n' || *v == '\r') ++v;
    int vlen = (int)strlen(v);
    while (vlen && (v[vlen - 1] == ' ' || v[vlen - 1] == '\t' ||
                    v[vlen - 1] == '\n' || v[vlen - 1] == '\r'))
        --vlen;
    if (vlen == 0) return -1;
    auto lower = [](char c) -> char {
        return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
    };
    for (int i = 0; i < _count; i++) {
        const char* lb = _sections[i].label;
        int j = 0;
        for (; j < vlen && lb[j]; j++)
            if (lower(lb[j]) != lower(v[j])) break;
        if (j == vlen && lb[j] == '\0') return i;
    }
    return -1;
}

Size MasterDetailWidget::measure(const Constraints& constraints) {
    /* master_detail.py fills the space the parent gives it; on the flat
     * pixel_stack tier the entry rect comes from x/y/w/h, so this is only
     * the API-symmetry answer. */
    return Size{constraints.max_width, constraints.max_height};
}

/* console_theme.ellipsize: measure_text-driven truncation.  ASCII-faithful
 * (byte == char for the label set this tier renders). */
static const char* ellipsize(Canvas& canvas, const char* text, float max_w,
                             char* buf, size_t cap) {
    if (canvas.measure_text(text).width <= max_w) {
        snprintf(buf, cap, "%s", text);
        return buf;
    }
    const char* ell = "\xE2\x80\xA6";   /* … (U+2026) */
    int len = (int)strlen(text);
    int lo = 0, hi = len;
    snprintf(buf, cap, "%s", ell);      /* best default */
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        int m = mid;
        while (m > 0 && text[m - 1] == ' ') m--;   /* rstrip prefix */
        char cand[MasterDetailWidget::kLabelCap + 8];
        if (m > (int)sizeof(cand) - 4) m = (int)sizeof(cand) - 4;
        memcpy(cand, text, m);
        cand[m] = '\0';
        strncat(cand, ell, sizeof(cand) - strlen(cand) - 1);
        if (canvas.measure_text(cand).width <= max_w) {
            snprintf(buf, cap, "%s", cand);
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return buf;
}

/* console_theme.draw_card, band-tier degradation ([CXC-ICV-TOKENS]
 * documented approximations): no ambient shadow / vertical gradient on
 * this tier — a flat panel fill + 1px line border. */
static void draw_card(Canvas& canvas, float x, float y, float w, float h) {
    canvas.set_fill_color(kPanel[0], kPanel[1], kPanel[2]);
    canvas.fill_round_rect(x, y, w, h, kRadiusCard);
    canvas.set_stroke_color(kLine[0], kLine[1], kLine[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_round_rect(x + 0.5f, y + 0.5f, w - 1.0f, h - 1.0f,
                             kRadiusCard);
}

void MasterDetailWidget::draw_nav(Canvas& canvas, const Rect& rect) {
    float nav_x = rect.x;
    float nav_w = _nav_width;

    /* LEFT nav column background panel + its border. */
    draw_card(canvas, nav_x, rect.y, nav_w, rect.h);

    /* Drive the shared scroll substrate from the arranged viewport.  A
     * variable section count in a FIXED box -> scroll, never grow.
     * row_gap = _ROW_GAP, pad = _NAV_PAD_TOP reproduce the Python model
     * ry = rect.y + _NAV_PAD_TOP; row i at ry + i*(ROW_H+ROW_GAP). */
    _nav.top        = rect.y;
    _nav.left       = nav_x;
    _nav.width      = nav_w;
    _nav.viewport_h = rect.h;
    _nav.row_h      = kRowH;
    _nav.row_gap    = kRowGap;
    _nav.pad        = kNavPadTop;
    _nav.row_count  = _count;
    bool overflow   = _nav.max_scroll() > 0.0f;
    _nav.gutter_w   = overflow ? kSbGutter : 0.0f;   /* tap-zone paging */
    _nav.set_offset(_nav.offset);                    /* re-clamp to range */

    canvas.save();
    canvas.clip_rect(nav_x, rect.y, nav_w, rect.h);

    /* O(visible rows), not O(sections): the substrate's tight window is
     * the band-renderer cull (proven equal in scroll_list_selftest). */
    int first = 0, last = 0;
    _nav.visible_range(first, last);
    for (int i = first; i < last; i++) {
        bool selected = (i == _active);
        float row_y0 = _nav.row_y(i);

        if (selected) {
            /* selected entry: accent-dim fill + a 2.5px accent left bar
             * (master_detail.py verbatim). */
            canvas.set_fill_color(kAccentDim[0], kAccentDim[1], kAccentDim[2],
                                  kAccentDim[3]);
            canvas.fill_round_rect(nav_x + 5.0f, row_y0 + 2.0f,
                                   nav_w - 10.0f, kRowH - 4.0f, 8.0f);
            canvas.set_fill_color(kAccent[0], kAccent[1], kAccent[2]);
            canvas.fill_round_rect(nav_x + 5.0f, row_y0 + 8.0f,
                                   2.5f, kRowH - 16.0f, 1.25f);
        }

        float cy = row_y0 + kRowH / 2.0f;
        canvas.set_font(kFontUi, kNavFont, "regular");
        TextMetrics m = canvas.measure_text("Ag");
        float base = cy + (m.ascent - m.descent) / 2.0f;
        const uint8_t* c = selected ? kAccent : kDim;
        canvas.set_fill_color(c[0], c[1], c[2]);
        char lbuf[kLabelCap + 8];
        const char* lbl = ellipsize(canvas, _sections[i].label,
                                    nav_w - 2.0f * kNavPadX, lbuf,
                                    sizeof(lbuf));
        canvas.draw_text(lbl, nav_x + kNavPadX + 6.0f, base, "left");
    }
    canvas.restore();

    /* scrollbar — only when the nav overflows its fixed box: a thin thumb
     * (the checklist.cpp idiom), never a control that resizes the box. */
    if (overflow) {
        float content_h = _nav.content_height();
        float track_w = 4.0f;
        float sx = nav_x + nav_w - track_w - 3.0f;
        float sy = rect.y + 4.0f;
        float sh = rect.h - 8.0f;
        canvas.set_fill_color(kLine[0], kLine[1], kLine[2]);
        canvas.fill_round_rect(sx, sy, track_w, sh, 2.0f);
        float thumb_h = std::max(20.0f, sh * (rect.h / content_h));
        float travel = std::max(0.0f, sh - thumb_h);
        float frac = _nav.offset / _nav.max_scroll();
        canvas.set_fill_color(kFaint[0], kFaint[1], kFaint[2]);
        canvas.fill_round_rect(sx, sy + travel * frac, track_w, thumb_h, 2.0f);
    }
}

void MasterDetailWidget::draw_detail(Canvas& canvas, float dx, float dy,
                                     float dw, float dh) {
    if (_count == 0 || dw <= 0.0f || dh <= 0.0f) return;

    canvas.save();
    canvas.clip_rect(dx, dy, dw, dh);

    const Section& sec = _sections[_active];
    float tx = dx + kDetailPadX;
    float tw = dw - 2.0f * kDetailPadX;
    float ty = dy + kDetailPadTop;

    /* Detail TITLE — the selected section's nav label (config-shared
     * field; the flat-tier detail chrome, see header). */
    canvas.set_font(kFontUi, kTitleFont, "bold");
    TextMetrics tm = canvas.measure_text("Ag");
    char tbuf[kLabelCap + 8];
    const char* title = ellipsize(canvas, sec.label, tw, tbuf, sizeof(tbuf));
    canvas.set_fill_color(kInk[0], kInk[1], kInk[2]);
    canvas.draw_text(title, tx, ty + tm.ascent, "left");
    /* a short accent rule under the title */
    float rule_y = ty + tm.ascent + tm.descent + 4.0f;
    canvas.set_fill_color(kAccent[0], kAccent[1], kAccent[2]);
    canvas.fill_rect(tx, rule_y, std::min(tw, 32.0f), 2.0f);
    ty = rule_y + 10.0f;

    /* Detail BODY — the section's `text:`, wrapped with the shared
     * text_wrap substrate (the one measurement path).  Split on '\n' into
     * paragraphs (text_wrap does not segment newlines) and greedy-wrap
     * each to the detail width. */
    canvas.set_font(kFontUi, kBodyFont, "regular");
    TextMetrics bm = canvas.measure_text("Ag");
    float lh = bm.ascent + bm.descent + 5.0f;
    canvas.set_fill_color(kDim[0], kDim[1], kDim[2]);

    float xs[kDetailCap + 1];
    LineSpan spans[24];
    const char* body = sec.detail;
    float bottom = dy + dh;

    int para_start = 0;
    int i = 0;
    bool done = false;
    for (; !done; i++) {
        char ch = body[i];
        if (ch != '\0' && ch != '\n') continue;
        if (ch == '\0') done = true;

        int plen = i - para_start;
        if (plen <= 0) {
            ty += lh;                     /* blank line */
        } else {
            if (plen > kDetailCap) plen = kDetailCap;
            const char* para = body + para_start;
            int nlines = wrap_text_lines(canvas, para, plen, tw, xs,
                                         (int)(sizeof(xs) / sizeof(xs[0])),
                                         spans, 24);
            if (nlines < 0) nlines = 0;          /* xs too small (unreached) */
            int shown = std::min(nlines, 24);
            for (int li = 0; li < shown; li++) {
                if (ty + bm.ascent > bottom) { done = true; break; }
                int s0 = spans[li].start;
                int s1 = spans[li].end;
                /* rstrip a trailing soft-wrap space for a cleaner left edge */
                while (s1 > s0 && para[s1 - 1] == ' ') s1--;
                int seg = s1 - s0;
                char linebuf[kDetailCap + 1];
                if (seg > kDetailCap) seg = kDetailCap;
                memcpy(linebuf, para + s0, seg);
                linebuf[seg] = '\0';
                canvas.draw_text(linebuf, tx, ty + bm.ascent, "left");
                ty += lh;
            }
        }
        para_start = i + 1;
        if (ty + bm.ascent > bottom) break;
    }

    canvas.restore();
}

void MasterDetailWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    canvas.clip_rect(rect.x, rect.y, rect.w, rect.h);

    draw_nav(canvas, rect);

    /* RIGHT detail pane: the active section only (the base routing
     * contract) — right of the nav column + gap. */
    float dx = rect.x + _nav_width + kNavGap;
    float dw = rect.w - _nav_width - kNavGap;
    draw_detail(canvas, dx, rect.y, dw, rect.h);

    canvas.restore();
}

void MasterDetailWidget::on_tap(float x, float y) {
    /* master_detail.py hit_test_tree: the nav column (x <= rect.x +
     * nav_width) switches sections; the detail pane delegates to its
     * children (no interactive detail on this tier).  Geometry persists
     * from the last draw(); next frame redraws from the new state. */
    if (x > _nav.left + _nav_width) return;   /* detail region — ignore */

    ScrollHit hit = _nav.classify(x, y);
    if (hit.kind == ScrollHitKind::Row) {
        set_active(hit.row);
        /* selection lives ON the bus (the declared nav seam) — an
         * Intelligence publishes the identical namespace (master_detail.py
         * on_tap; logged until a bus transport lands, like Button). */
        if (_nav_namespace && s_publish) {
            char vbuf[kLabelCap + 8];
            s_publish(_nav_namespace, nav_value(hit.row, vbuf, sizeof(vbuf)));
        }
    } else if (hit.kind == ScrollHitKind::PageUp) {
        _nav.page_up();
    } else if (hit.kind == ScrollHitKind::PageDown) {
        _nav.page_down();
    }
}
