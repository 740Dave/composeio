/*
 * dialog.cpp — DialogWidget implementation ([CXC-R-DIALOG] port of
 * composeio/render/widgets/dialog.py, flat-tier reduction — see
 * widgets/dialog.h).
 *
 * Scrim + centered rounded panel + title + word-wrapped body +
 * confirm/abort affordances + modal hit-capture.  Geometry + tap
 * classification come from the shared overlay substrate (overlay.cpp);
 * the body is wrapped with the shared text_wrap substrate.  Style
 * constants are dialog.py + style.py (baked the checklist.cpp way).
 */

#include "widgets/dialog.h"
#include "overlay.h"
#include "text_wrap.h"
#include "canvas.h"

#include <algorithm>
#include <cstring>
#include <cstdio>
#include "esp_log.h"

/* -- geometry (dialog.py verbatim where it maps) --------------------- */
static constexpr float kTitleH  = 36.0f;   /* _TITLE_H */
static constexpr float kPad     = 14.0f;   /* _PAD     */
static constexpr float kAffH    = 32.0f;   /* affordance button height */
static constexpr float kAffGap  = 10.0f;
static constexpr float kAffRad  = 6.0f;    /* button corner (style.py) */
static constexpr float kPanelRad = 10.0f;  /* dialog.py fill_round_rect 10 */
static constexpr float kBodyFont  = 13.0f;
static constexpr float kTitleFont = 14.0f;

/* -- style.py / dialog.py role tokens -------------------------------- */
static constexpr uint8_t kPanelBg[3] = {0x29, 0x29, 0x29};  /* FRAME_TOP_BG */
static constexpr uint8_t kBorder[3]  = {0x3A, 0x3A, 0x50};
static constexpr uint8_t kText[3]    = {0xD6, 0xD6, 0xD6};   /* style.TEXT   */
static constexpr uint8_t kAccent[3]  = {0x34, 0xC3, 0xD0};   /* _ACCENT      */
static constexpr uint8_t kAccentInk[3] = {0x0E, 0x1A, 0x1C}; /* dark on accent */
static const char* kFontUi = "sans-serif";

DialogWidget::PublishFn DialogWidget::s_publish = nullptr;
DialogWidget::DismissFn DialogWidget::s_dismiss = nullptr;
void*                   DialogWidget::s_dismiss_user = nullptr;

/* ------------------------------------------------------------------- */

DialogWidget::DialogWidget(const char* id, const char* title,
                           const char* body, bool dismissible,
                           float panel_width, float panel_height)
    : Widget(id, "dialog"),
      _dismissible(dismissible),
      _panel_w(panel_width > 0.0f ? panel_width : 300.0f),
      _panel_h(panel_height) {
    snprintf(_title, sizeof(_title), "%s", title ? title : "");
    snprintf(_body, sizeof(_body), "%s", body ? body : "");
    _confirm_label[0] = _abort_label[0] = '\0';
    _confirm_value[0] = '\0';
    _namespace[0] = _decision_ns[0] = '\0';
    _scrim[0] = 0; _scrim[1] = 0; _scrim[2] = 0; _scrim[3] = 150;
}

void DialogWidget::set_scrim_color(uint8_t r, uint8_t g, uint8_t b,
                                   uint8_t a) {
    _scrim[0] = r; _scrim[1] = g; _scrim[2] = b; _scrim[3] = a;
}

void DialogWidget::set_namespace(const char* ns) {
    snprintf(_namespace, sizeof(_namespace), "%s", ns ? ns : "");
}

void DialogWidget::set_decision_namespace(const char* ns) {
    snprintf(_decision_ns, sizeof(_decision_ns), "%s", ns ? ns : "");
}

void DialogWidget::set_confirm(const char* label, const char* value) {
    snprintf(_confirm_label, sizeof(_confirm_label), "%s",
             label ? label : "OK");
    snprintf(_confirm_value, sizeof(_confirm_value), "%s",
             value ? value : "confirm");
    _has_confirm = true;
}

void DialogWidget::set_abort(const char* label) {
    snprintf(_abort_label, sizeof(_abort_label), "%s", label ? label : "Cancel");
    _has_abort = true;
}

Size DialogWidget::measure(const Constraints& constraints) {
    /* Claims the whole surface (the scrim) — dialog.py measure(). */
    return Size{constraints.max_width, constraints.max_height};
}

void DialogWidget::cache_layout(Rect panel, const Rect* affs, int n) {
    _panel = panel;
    _n_aff = n < 0 ? 0 : (n > kMaxAff ? kMaxAff : n);
    for (int i = 0; i < _n_aff; i++) _aff[i] = affs[i];
}

/* Draw one affordance button and return its rect. */
static Rect draw_button(Canvas& canvas, float x, float y, float w, float h,
                        const char* label, bool primary) {
    if (primary) {
        canvas.set_fill_color(kAccent[0], kAccent[1], kAccent[2]);
        canvas.fill_round_rect(x, y, w, h, kAffRad);
    } else {
        canvas.set_fill_color(kPanelBg[0], kPanelBg[1], kPanelBg[2]);
        canvas.fill_round_rect(x, y, w, h, kAffRad);
        canvas.set_stroke_color(kBorder[0], kBorder[1], kBorder[2]);
        canvas.set_stroke_width(1.0f);
        canvas.stroke_round_rect(x, y, w, h, kAffRad);
    }
    canvas.set_font(kFontUi, 13.0f, "medium");
    const uint8_t* ink = primary ? kAccentInk : kText;
    canvas.set_fill_color(ink[0], ink[1], ink[2]);
    TextMetrics m = canvas.measure_text(label ? label : "");
    float base = y + h / 2.0f + (m.ascent - m.descent) / 2.0f;
    canvas.draw_text(label ? label : "", x + w / 2.0f, base, "center");
    return Rect{x, y, w, h};
}

void DialogWidget::draw(Canvas& canvas, const Rect& rect) {
    _rect = rect;

    canvas.save();

    /* scrim over the whole surface (modal capture backdrop) */
    canvas.set_fill_color(_scrim[0], _scrim[1], _scrim[2], _scrim[3]);
    canvas.fill_rect(rect.x, rect.y, rect.w, rect.h);

    /* panel width (clamped the overlay_center_panel way, margin 8) */
    const float margin = 8.0f;
    float pw = _panel_w;
    if (pw > rect.w - 2.0f * margin) pw = rect.w - 2.0f * margin;
    float inner_w = pw - 2.0f * kPad;
    if (inner_w < 1.0f) inner_w = 1.0f;

    bool has_title = _title[0] != '\0';
    float title_h = has_title ? kTitleH : 0.0f;
    bool has_aff = _has_confirm || _has_abort;
    float aff_row_h = has_aff ? (kAffH + kPad) : 0.0f;

    /* body wrap (shared text_wrap substrate, read-only prose variant —
     * chat_thread rule).  '\n' is not segmented here (dialog bodies are
     * short prose; the master_detail paragraph loop is overkill). */
    canvas.set_font(kFontUi, kBodyFont, "regular");
    TextMetrics bm = canvas.measure_text("Ag");
    float lh = bm.ascent + bm.descent + 4.0f;
    int blen = (int)strlen(_body);
    if (blen > kBodyCap) blen = kBodyCap;
    float xs[kBodyCap + 1];
    LineSpan spans[16];
    int nlines = 0;
    if (blen > 0) {
        nlines = wrap_text_lines_display(canvas, _body, blen, inner_w, xs,
                                         (int)(sizeof(xs) / sizeof(xs[0])),
                                         spans, 16);
        if (nlines < 0) nlines = 0;
        if (nlines > 16) nlines = 16;
    }
    float body_h = nlines * lh;

    /* panel height: configured, else content-sized (dialog.py) */
    float ph = _panel_h;
    if (ph <= 0.0f)
        ph = title_h + kPad + body_h + kPad + aff_row_h;

    Rect panel = overlay_center_panel(rect.x, rect.y, rect.w, rect.h,
                                      pw, ph, margin);

    /* panel body + border (dialog.py) */
    canvas.set_fill_color(kPanelBg[0], kPanelBg[1], kPanelBg[2]);
    canvas.fill_round_rect(panel.x, panel.y, panel.w, panel.h, kPanelRad);
    canvas.set_stroke_color(kBorder[0], kBorder[1], kBorder[2]);
    canvas.set_stroke_width(1.0f);
    canvas.stroke_round_rect(panel.x, panel.y, panel.w, panel.h, kPanelRad);

    /* clip content to the panel (a long body never bleeds past the frame) */
    canvas.save();
    canvas.clip_round_rect(panel.x, panel.y, panel.w, panel.h, kPanelRad);

    /* title + divider (dialog.py) */
    if (has_title) {
        canvas.set_font(kFontUi, kTitleFont, "medium");
        canvas.set_fill_color(kText[0], kText[1], kText[2]);
        TextMetrics tm = canvas.measure_text("Ag");
        float base = panel.y + kTitleH / 2.0f + (tm.ascent - tm.descent) / 2.0f;
        canvas.draw_text(_title, panel.x + kPad, base, "left");
        canvas.set_stroke_color(kBorder[0], kBorder[1], kBorder[2]);
        canvas.set_stroke_width(1.0f);
        canvas.draw_line(panel.x + 1.0f, panel.y + kTitleH,
                         panel.x + panel.w - 1.0f, panel.y + kTitleH);
    }

    /* body lines */
    if (nlines > 0) {
        canvas.set_font(kFontUi, kBodyFont, "regular");
        canvas.set_fill_color(kText[0], kText[1], kText[2]);
        float bx = panel.x + kPad;
        float by = panel.y + title_h + kPad;
        float body_bottom = panel.y + panel.h - aff_row_h;
        for (int li = 0; li < nlines; li++) {
            if (by + bm.ascent > body_bottom) break;
            int s0 = spans[li].start;
            int s1 = spans[li].end;
            while (s1 > s0 && _body[s1 - 1] == ' ') s1--;  /* rstrip soft-wrap */
            int seg = s1 - s0;
            if (seg > kBodyCap) seg = kBodyCap;
            char line[kBodyCap + 1];
            memcpy(line, _body + s0, seg);
            line[seg] = '\0';
            canvas.draw_text(line, bx, by + bm.ascent, "left");
            by += lh;
        }
    }
    canvas.restore();   /* panel clip */

    /* affordance row (built-in confirm/abort — the flat analog of
     * dialog.py's child Buttons).  confirm is primary/right. */
    Rect affs[kMaxAff];
    uint8_t roles[kMaxAff];
    int n = 0;
    if (has_aff) {
        float aff_y = panel.y + panel.h - kPad - kAffH;
        float avail = panel.w - 2.0f * kPad;
        float ax = panel.x + kPad;
        if (_has_confirm && _has_abort) {
            float bw = (avail - kAffGap) / 2.0f;
            affs[n] = draw_button(canvas, ax, aff_y, bw, kAffH,
                                  _abort_label, false);
            roles[n++] = 1;   /* abort  (left)  */
            affs[n] = draw_button(canvas, ax + bw + kAffGap, aff_y, bw, kAffH,
                                  _confirm_label, true);
            roles[n++] = 0;   /* confirm (right) */
        } else if (_has_confirm) {
            affs[n] = draw_button(canvas, ax, aff_y, avail, kAffH,
                                  _confirm_label, true);
            roles[n++] = 0;
        } else {
            affs[n] = draw_button(canvas, ax, aff_y, avail, kAffH,
                                  _abort_label, false);
            roles[n++] = 1;
        }
    }
    canvas.restore();   /* scrim/panel save */

    cache_layout(panel, affs, n);
    for (int i = 0; i < n; i++) _aff_role[i] = roles[i];
}

void DialogWidget::close(const char* decision) {
    /* decision != nullptr -> confirm: publish the value to the decision
     * namespace (defaults to the open/close namespace). */
    const char* dns = _decision_ns[0] ? _decision_ns : _namespace;
    bool decision_hit_gate = false;
    if (decision && dns[0] && s_publish) {
        s_publish(dns, decision);
        decision_hit_gate = (_namespace[0] && !strcmp(dns, _namespace));
    }
    /* publish "off" to the open/close gate for bus coherence — unless the
     * decision value already went to that same namespace (don't clobber). */
    if (_namespace[0] && s_publish && !decision_hit_gate)
        s_publish(_namespace, "off");

    /* flip our own SceneEntry.visible off + force a touch re-register
     * (the RAttr::Visible analog — the modal stops capturing). */
    if (s_dismiss) s_dismiss(s_dismiss_user, widget_id());
}

void DialogWidget::on_tap(float x, float y) {
    /* Local coords == screen coords (the scene forces the dialog rect to
     * the full viewport at 0,0).  Classify against the cached panel +
     * affordances (the ONE geometry path from draw()). */
    OverlayClassify c = overlay_classify(_panel, _aff, _n_aff, x, y);
    switch (c.kind) {
        case OverlayHit::Affordance: {
            uint8_t role = (c.affordance >= 0 && c.affordance < kMaxAff)
                               ? _aff_role[c.affordance] : 1;
            if (role == 0) {   /* confirm */
                ESP_LOGI("dialog", "'%s' CONFIRM -> %s", widget_id(),
                         _confirm_value);
                close(_confirm_value);
            } else {           /* abort */
                ESP_LOGI("dialog", "'%s' ABORT", widget_id());
                close(nullptr);
            }
            break;
        }
        case OverlayHit::Scrim:
            if (_dismissible) {
                ESP_LOGI("dialog", "'%s' scrim dismiss", widget_id());
                close(nullptr);
            }
            /* non-dismissible scrim tap: swallowed (modal capture) */
            break;
        case OverlayHit::PanelDeadSpace:
            /* dead panel space: swallowed, no change (dialog.py) */
            break;
    }
}
