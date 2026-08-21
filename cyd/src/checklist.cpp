/*
 * checklist.cpp — ChecklistWidget implementation ([CXC-PRIM-TIER-PARITY]).
 *
 * Direct port of composeio/render/widgets/nightwatch_checklist.py draw +
 * on_scroll, with the scroll model COMPOSED from the shared ScrollList
 * substrate (scroll_list.cpp) rather than re-derived — this widget is the
 * first substrate consumer.
 *
 * Colours are the console_theme DARK tokens (CONSOLE.active defaults to
 * DARK — the theme the Skia golden renders with), baked as constants the
 * same way checkbox.cpp bakes style.py.  The role tokens used here:
 *
 *   ok    #2FD07B   warn  #F5A623   faint #55616F
 *   ink   #E7ECF3   dim   #8B98AA   bg    #070A0F   line #1B2432
 *
 * Reference: composeio/render/widgets/nightwatch_checklist.py,
 *            widgets/console_theme.py (draw_tracked / measure_tracked /
 *            ellipsize).
 */

#include "widgets/checklist.h"
#include "canvas.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

/* -- geometry (nightwatch_checklist.py verbatim) --------------------- */
static constexpr float kRowH    = 27.0f;   /* ROW_H   */
static constexpr float kMarkerR = 6.0f;    /* MARKER_R */
static constexpr float kPadX    = 4.0f;    /* PAD_X   */
static constexpr float kSbGutter = 8.0f;   /* reserved right gutter when overflow */
static constexpr float kTagTrack = 0.8f;   /* status-tag letter-spacing */

/* -- console_theme DARK role tokens ---------------------------------- */
static constexpr uint8_t kOk[3]    = {0x2F, 0xD0, 0x7B};
static constexpr uint8_t kWarn[3]  = {0xF5, 0xA6, 0x23};
static constexpr uint8_t kFaint[3] = {0x55, 0x61, 0x6F};
static constexpr uint8_t kInk[3]   = {0xE7, 0xEC, 0xF3};
static constexpr uint8_t kDim[3]   = {0x8B, 0x98, 0xAA};
static constexpr uint8_t kBg[3]    = {0x07, 0x0A, 0x0F};
static constexpr uint8_t kLine[3]  = {0x1B, 0x24, 0x32};

/* Fonts: FONT_UI = "sans-serif", FONT_MONO = "monospace" (console_theme). */
static const char* kFontUi   = "sans-serif";
static const char* kFontMono = "monospace";

/* ------------------------------------------------------------------- */

ChecklistWidget::ChecklistWidget(const char* id, float font_size)
    : Widget(id, "checklist"), _font_size(font_size) {}

ChecklistWidget::Status ChecklistWidget::norm_status(const char* s) {
    if (!s) return Status::Deferred;
    /* case-insensitive compare against the canon statuses */
    auto ieq = [](const char* a, const char* b) -> bool {
        while (*a && *b) {
            char ca = (*a >= 'A' && *a <= 'Z') ? (char)(*a + 32) : *a;
            char cb = *b;
            if (ca != cb) return false;
            a++; b++;
        }
        return *a == '\0' && *b == '\0';
    };
    if (ieq(s, "done"))    return Status::Done;
    if (ieq(s, "partial")) return Status::Partial;
    return Status::Deferred;   /* deferred / unknown — calm, not crit */
}

void ChecklistWidget::add_row(const char* label, const char* status) {
    if (_count >= kMaxRows || !label || !label[0]) return;
    Row& r = _rows[_count];
    snprintf(r.label, sizeof(r.label), "%s", label);
    r.status = norm_status(status);
    _count++;
}

void ChecklistWidget::clear_rows() { _count = 0; }

/* ------------------------------------------------------------------- */
/* Live on_bus_state apply — minimal JSON reader                        */
/* ------------------------------------------------------------------- */
/*
 * A live publisher on the widget's `namespace:` replaces the whole task
 * list ([CXC-RM-M5], reactive_apply.h RAttr::ChecklistItems).  The on-device
 * JSON path elsewhere is cJSON, but cJSON is an ESP-IDF component — ABSENT on
 * the native host the reactive_core parity harness builds on — and the
 * acceptance requires a HOST-TESTED apply.  So the parse is a purpose-built,
 * host-portable reader for the ONE flat shape nightwatch_checklist.py _coerce
 * accepts; it is NOT a general JSON library, and it is the SINGLE parse path
 * both tiers share (no fork).  Anything richer than the contract degrades to
 * the widget's empty/keep-last path, exactly like the python _coerce guards.
 */

/* Skip JSON whitespace. */
static const char* j_ws(const char* p) {
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') ++p;
    return p;
}

/* Parse a "..."-quoted string starting at the opening quote into buf
 * (NUL-terminated, truncated to cap).  Handles the JSON escapes; a \uXXXX
 * emits '?' (the band tier's label set is ASCII, matching ellipsize()).
 * Returns the char past the closing quote, or nullptr if unterminated.
 * buf may be null (skip-only). */
static const char* j_str(const char* p, char* buf, size_t cap) {
    if (*p != '"') return nullptr;
    ++p;
    size_t n = 0;
    while (*p && *p != '"') {
        char c = *p++;
        if (c == '\\' && *p) {
            char e = *p++;
            switch (e) {
                case 'n': c = '\n'; break;
                case 't': c = '\t'; break;
                case 'r': c = '\r'; break;
                case 'b': c = '\b'; break;
                case 'f': c = '\f'; break;
                case 'u':
                    for (int i = 0; i < 4 && *p; ++i) ++p;
                    c = '?';
                    break;
                default: c = e; break;   /* \" \\ \/ and any other */
            }
        }
        if (buf && n + 1 < cap) buf[n++] = c;
    }
    if (*p != '"') return nullptr;       /* unterminated */
    if (buf && cap) buf[n] = '\0';
    return p + 1;
}

/* Advance past any JSON value (string-aware, balanced {}/[]). */
static const char* j_skip(const char* p) {
    p = j_ws(p);
    if (*p == '"') return j_str(p, nullptr, 0);
    if (*p == '{' || *p == '[') {
        int depth = 0;
        while (*p) {
            if (*p == '"') {
                p = j_str(p, nullptr, 0);
                if (!p) return nullptr;
                continue;
            }
            if (*p == '{' || *p == '[') ++depth;
            else if (*p == '}' || *p == ']') { if (--depth == 0) return p + 1; }
            ++p;
        }
        return nullptr;
    }
    /* scalar (number/true/false/null) — to the next delimiter. */
    while (*p && *p != ',' && *p != '}' && *p != ']' &&
           *p != ' ' && *p != '\t' && *p != '\n' && *p != '\r') ++p;
    return p;
}

/* python str().strip() on a mutable buffer (leading + trailing whitespace). */
static void j_trim(char* s) {
    char* p = s;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') ++p;
    if (p != s) memmove(s, p, strlen(p) + 1);
    size_t n = strlen(s);
    while (n && (s[n - 1] == ' ' || s[n - 1] == '\t' ||
                 s[n - 1] == '\n' || s[n - 1] == '\r'))
        s[--n] = '\0';
}

bool ChecklistWidget::set_items_json(const char* json) {
    if (!json) return false;
    const char* p = j_ws(json);
    /* python _coerce: a string whose first non-space char is not { or [ is
     * not a list -> None -> caller keeps the last good list. */
    if (*p != '{' && *p != '[') return false;

    /* {"tasks":[...]} unwrap (python: dict -> value.get("tasks")). */
    if (*p == '{') {
        const char* arr = nullptr;
        const char* q = p + 1;
        while (true) {
            q = j_ws(q);
            if (*q != '"') break;            /* '}' or malformed -> no array */
            char key[16];
            const char* after = j_str(q, key, sizeof(key));
            if (!after) break;
            q = j_ws(after);
            if (*q != ':') break;
            q = j_ws(q + 1);
            if (!strcmp(key, "tasks") && *q == '[') { arr = q; break; }
            q = j_skip(q);
            if (!q) break;
            q = j_ws(q);
            if (*q != ',') break;
            ++q;
        }
        if (!arr) { clear_rows(); return true; }  /* dict w/o tasks -> [] */
        p = arr;
    }

    /* From here p points at '[': a valid list container -> replace. */
    clear_rows();
    p = j_ws(p + 1);
    while (*p && *p != ']') {
        if (*p == '{') {
            char label[kLabelCap];  label[0]  = '\0';
            char name[kLabelCap];   name[0]   = '\0';
            char status[16];        status[0] = '\0';
            const char* q = p + 1;
            bool ok = true;
            while (true) {
                q = j_ws(q);
                if (*q == '}') { ++q; break; }
                if (*q != '"') { ok = false; break; }
                char key[16];
                const char* after = j_str(q, key, sizeof(key));
                if (!after) { ok = false; break; }
                q = j_ws(after);
                if (*q != ':') { ok = false; break; }
                q = j_ws(q + 1);
                if (*q == '"' && !strcmp(key, "label"))
                    q = j_str(q, label, sizeof(label));
                else if (*q == '"' && !strcmp(key, "name"))
                    q = j_str(q, name, sizeof(name));
                else if (*q == '"' && !strcmp(key, "status"))
                    q = j_str(q, status, sizeof(status));
                else
                    q = j_skip(q);
                if (!q) { ok = false; break; }
                q = j_ws(q);
                if (*q == ',') { ++q; continue; }
                if (*q == '}') { ++q; break; }
                ok = false; break;
            }
            if (!ok) break;                  /* malformed object -> stop */
            /* python: str(label or name or "").strip(); label wins. */
            j_trim(label);
            j_trim(name);
            j_trim(status);
            const char* lbl = label[0] ? label : (name[0] ? name : nullptr);
            if (lbl && *lbl) add_row(lbl, status[0] ? status : nullptr);
            p = j_ws(q);
        } else if (*p == '"') {
            char item[kLabelCap];
            const char* q = j_str(p, item, sizeof(item));
            if (!q) break;
            j_trim(item);                    /* python: it.strip() */
            if (item[0]) add_row(item, nullptr);   /* nullptr -> deferred */
            p = j_ws(q);
        } else {
            /* number/bool/null element -> python skips it silently. */
            const char* q = j_skip(p);
            if (!q) break;
            p = j_ws(q);
        }
        if (*p == ',') p = j_ws(p + 1);
        else break;                          /* ']' or malformed -> done */
    }
    return true;
}

Size ChecklistWidget::measure(const Constraints& constraints) {
    /* Content-sized, NOT growable: height is the true row-stack height
     * (mirrors nightwatch_checklist.measure). */
    int n = _count > 0 ? _count : 1;
    return Size{constraints.max_width, (float)n * kRowH};
}

/* -- state -> colour ------------------------------------------------- */
static const uint8_t* state_color(ChecklistWidget::Status st) {
    switch (st) {
        case ChecklistWidget::Status::Done:    return kOk;
        case ChecklistWidget::Status::Partial: return kWarn;
        default:                               return kFaint;
    }
}

void ChecklistWidget::draw_marker(Canvas& canvas, float cx, float cy,
                                  Status st) {
    const uint8_t* c = state_color(st);
    if (st == Status::Done) {
        canvas.set_fill_color(c[0], c[1], c[2]);
        canvas.fill_circle(cx, cy, kMarkerR);
        /* a drawn check (Plex ships no ✓; a font fallback forks pixels) */
        canvas.set_stroke_color(kBg[0], kBg[1], kBg[2]);
        canvas.set_stroke_width(1.6f);
        canvas.draw_line(cx - 2.8f, cy + 0.2f, cx - 0.6f, cy + 2.4f);
        canvas.draw_line(cx - 0.6f, cy + 2.4f, cx + 3.0f, cy - 2.4f);
    } else if (st == Status::Partial) {
        /* half-filled amber ring: filled left semicircle + full ring */
        canvas.set_fill_color(c[0], c[1], c[2]);
        canvas.fill_arc(cx, cy, kMarkerR, (float)M_PI / 2.0f,
                        3.0f * (float)M_PI / 2.0f);
        canvas.set_stroke_color(c[0], c[1], c[2]);
        canvas.set_stroke_width(1.6f);
        canvas.stroke_circle(cx, cy, kMarkerR);
    } else {
        /* deferred — faint hollow ring */
        canvas.set_stroke_color(c[0], c[1], c[2]);
        canvas.set_stroke_width(1.4f);
        canvas.stroke_circle(cx, cy, kMarkerR);
    }
}

/* console_theme.measure_tracked: per-glyph advance + tracking. */
static float measure_tracked(Canvas& canvas, const char* text, float track) {
    float w = 0.0f;
    int n = 0;
    char ch[2] = {0, 0};
    for (const char* p = text; *p; ++p, ++n) {
        ch[0] = *p;
        w += canvas.measure_text(ch).width + track;
    }
    return n ? w - track : 0.0f;
}

/* console_theme.draw_tracked: per-glyph draw with an advance nudge. */
static void draw_tracked(Canvas& canvas, const char* text, float x,
                         float baseline, float track,
                         const uint8_t* color) {
    canvas.set_fill_color(color[0], color[1], color[2]);
    float cx = x;
    char ch[2] = {0, 0};
    for (const char* p = text; *p; ++p) {
        ch[0] = *p;
        canvas.draw_text(ch, cx, baseline, "left");
        cx += canvas.measure_text(ch).width + track;
    }
}

/* console_theme.ellipsize: measure_text-driven truncation.  ASCII-faithful
 * (byte==char for the label set this tier renders). */
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
        /* prefix[:mid] rstripped + ell */
        int m = mid;
        while (m > 0 && text[m - 1] == ' ') m--;
        char cand[ChecklistWidget::kLabelCap + 4];
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

void ChecklistWidget::draw(Canvas& canvas, const Rect& rect) {
    canvas.save();
    canvas.clip_rect(rect.x, rect.y, rect.w, rect.h);

    if (_count == 0) {
        canvas.set_fill_color(kFaint[0], kFaint[1], kFaint[2]);
        canvas.set_font(kFontMono, 10.0f, "regular");
        canvas.draw_text("\xE2\x80\x94 no tasks recorded \xE2\x80\x94",
                         rect.x + kPadX, rect.y + 14.0f, "left");
        canvas.restore();
        return;
    }

    /* Drive the shared scroll substrate from the arranged viewport.  A
     * variable row count in a FIXED box -> scroll, never grow.  ScrollList
     * owns content_h / max_scroll / clamp; row_gap=0, pad=0 reproduce the
     * Python model content_h = n*ROW_H, ry = rect.y - scroll + i*ROW_H. */
    _sl.top        = rect.y;
    _sl.left       = rect.x;
    _sl.width      = rect.w;
    _sl.viewport_h = rect.h;
    _sl.row_h      = kRowH;
    _sl.row_gap    = 0.0f;
    _sl.pad        = 0.0f;
    _sl.row_count  = _count;
    bool overflow  = _sl.max_scroll() > 0.0f;
    _sl.gutter_w   = overflow ? kSbGutter : 0.0f;  /* tap-zone paging */
    _sl.set_offset(_sl.offset);                    /* re-clamp to range */

    float sb_gutter = overflow ? kSbGutter : 0.0f;
    float mx = rect.x + kPadX + kMarkerR + 2.0f;   /* marker centre x */
    float tx = mx + kMarkerR + 12.0f;              /* label x         */

    /* O(visible rows), not O(rows): the substrate's tight window is the
     * band-renderer cull (proven equal in scroll_list_selftest). */
    int first = 0, last = 0;
    _sl.visible_range(first, last);
    for (int i = first; i < last; i++) {
        const Row& row = _rows[i];
        float ry = _sl.row_y(i);
        float cy = ry + kRowH / 2.0f;
        draw_marker(canvas, mx, cy, row.status);

        const uint8_t* color = state_color(row.status);

        /* right-aligned status tag (tracked UPPERCASE mono, state colour) */
        const char* tag = (row.status == Status::Done) ? "DONE"
                        : (row.status == Status::Partial) ? "PARTIAL"
                        : "DEFERRED";
        canvas.set_font(kFontMono, 8.5f, "bold");
        float tag_w = measure_tracked(canvas, tag, kTagTrack);
        float tag_x = rect.x + rect.w - kPadX - sb_gutter - tag_w;
        TextMetrics ag = canvas.measure_text("Ag");
        float base = cy + (ag.ascent - ag.descent) / 2.0f;
        draw_tracked(canvas, tag, tag_x, base, kTagTrack, color);

        /* label — ink when done, dim when partial, faint when deferred */
        const uint8_t* ink = (row.status == Status::Done) ? kInk
                           : (row.status == Status::Partial) ? kDim
                           : kFaint;
        canvas.set_font(kFontUi, _font_size,
                        row.status == Status::Done ? "medium" : "regular");
        TextMetrics lm = canvas.measure_text("Ag");
        char lbuf[kLabelCap + 4];
        const char* lbl = ellipsize(canvas, row.label, tag_x - tx - 10.0f,
                                    lbuf, sizeof(lbuf));
        canvas.set_fill_color(ink[0], ink[1], ink[2]);
        canvas.draw_text(lbl, tx, cy + (lm.ascent - lm.descent) / 2.0f, "left");
    }

    /* scrollbar — only when the list overflows its fixed box: a thin
     * thumb, never a control that resizes the region. */
    if (overflow) {
        float content_h = _sl.content_height();
        float track_w = 4.0f;
        float sx = rect.x + rect.w - track_w - 1.0f;
        float sy = rect.y + 2.0f;
        float sh = rect.h - 4.0f;
        canvas.set_fill_color(kLine[0], kLine[1], kLine[2]);
        canvas.fill_round_rect(sx, sy, track_w, sh, 2.0f);
        float thumb_h = std::max(20.0f, sh * (rect.h / content_h));
        float travel = std::max(0.0f, sh - thumb_h);
        float frac = _sl.offset / _sl.max_scroll();
        canvas.set_fill_color(kFaint[0], kFaint[1], kFaint[2]);
        canvas.fill_round_rect(sx, sy + travel * frac, track_w, thumb_h, 2.0f);
    }

    canvas.restore();
}

void ChecklistWidget::on_tap(float x, float y) {
    /* Rows are NOT tap targets (the Python widget shows the arrow cursor —
     * hover_cursor "default"; the list holds no selectable text).  A tap is
     * consumed ONLY to page the overflowing list via the substrate's
     * gutter tap-zones — the honest interim on the CYD's tap-on-release
     * touch tier (no fabricated drag delta).  Geometry persists from the
     * last draw(); next frame redraws from the new offset. */
    ScrollHit hit = _sl.classify(x, y);
    if (hit.kind == ScrollHitKind::PageUp) {
        _sl.page_up();
    } else if (hit.kind == ScrollHitKind::PageDown) {
        _sl.page_down();
    }
}
