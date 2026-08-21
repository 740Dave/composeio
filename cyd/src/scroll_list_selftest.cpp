/*
 * scroll_list_selftest.cpp — host parity harness for the scroll substrate.
 *
 * Asserts the C++ ScrollList model reproduces the Skia/Python scroll model
 * (composeio/render/widgets/doc_picker.py) BIT-for-bit on the load-bearing
 * math.  Every EXP_* below was computed by running the Python formulas
 * directly (row_h=34, row_gap=4, viewport=130, pad=2 — the doc_picker
 * constants); see the reference-value derivation in the build commit.
 *
 * Runs off hardware:  pio run -e native_scroll_test && \
 *                     .pio/build/native_scroll_test/program
 * Must print "ALL PASS" and exit 0.
 */

#include "scroll_list.h"

#include <cstdio>
#include <cmath>

static int g_fail = 0;

static void check_f(const char* what, float got, float exp) {
    if (std::fabs(got - exp) > 1e-3f) {
        std::printf("  FAIL %-28s got=%.4f exp=%.4f\n", what, got, exp);
        g_fail++;
    }
}

static void check_i(const char* what, int got, int exp) {
    if (got != exp) {
        std::printf("  FAIL %-28s got=%d exp=%d\n", what, got, exp);
        g_fail++;
    }
}

/* A ScrollList primed with the doc_picker geometry. */
static ScrollList make(int n, float offset = 0.0f) {
    ScrollList s;
    s.top = 100.0f;
    s.left = 0.0f;
    s.width = 260.0f;
    s.viewport_h = 130.0f;   /* _LIST_H  */
    s.row_h = 34.0f;         /* _ROW_H   */
    s.row_gap = 4.0f;        /* _ROW_GAP */
    s.pad = 2.0f;
    s.row_count = n;
    s.offset = offset;
    return s;
}

int main() {
    std::printf("scroll_list parity harness (vs doc_picker.py model)\n");

    /* --- content_height / max_scroll (pad-independent) --------------- */
    struct { int n; float content; float maxs; } cm[] = {
        {0,   0.0f,   0.0f},
        {1,  38.0f,   0.0f},
        {3, 114.0f,   0.0f},
        {4, 152.0f,  22.0f},
        {20,760.0f, 630.0f},
    };
    for (auto& c : cm) {
        ScrollList s = make(c.n);
        check_f("content_height", s.content_height(), c.content);
        check_f("max_scroll",     s.max_scroll(),     c.maxs);
    }

    /* --- scroll_by_wheel parity (doc_picker on_scroll) --------------- *
     * n=20 (max=630); sequence dy = -3,-3,-3,+1,+50 from offset 0.      */
    {
        ScrollList s = make(20, 0.0f);
        float expseq[] = {60.0f, 120.0f, 180.0f, 160.0f, 0.0f};
        float dys[]    = {-3.0f, -3.0f,  -3.0f,   1.0f,  50.0f};
        for (int i = 0; i < 5; i++) {
            s.scroll_by_wheel(dys[i]);          /* default step 20 */
            check_f("wheel_seq", s.offset, expseq[i]);
        }
    }

    /* --- clamp bounds ----------------------------------------------- */
    {
        ScrollList s = make(20);
        s.set_offset(-500.0f); check_f("clamp_low",  s.offset, 0.0f);
        s.set_offset(9999.0f); check_f("clamp_high", s.offset, 630.0f);
        s.set_offset(300.0f);  check_f("clamp_mid",  s.offset, 300.0f);
        /* short list can't scroll */
        ScrollList t = make(3);
        t.set_offset(500.0f);  check_f("clamp_short", t.offset, 0.0f);
    }

    /* --- drag delta (natural touch: down reveals rows above) -------- */
    {
        ScrollList s = make(20, 100.0f);
        s.drag_begin(200.0f);
        s.drag_update(150.0f);   /* finger up 50 -> offset += 50 -> 150 */
        check_f("drag_up",   s.offset, 150.0f);
        s.drag_update(260.0f);   /* finger down 60 from start -> 40 */
        check_f("drag_down", s.offset, 40.0f);
    }

    /* --- page paging (0.8 viewport step) ---------------------------- */
    {
        ScrollList s = make(20, 0.0f);
        s.page_down();                       /* +104 */
        check_f("page_down", s.offset, 104.0f);
        s.page_up();                         /* -104 -> 0 */
        check_f("page_up",   s.offset, 0.0f);
    }

    /* --- row_at parity (doc_picker _row_at), top=100 pad=2 ---------- */
    struct { float y; float off; int exp; } ra[] = {
        {101,  0.0f, -1}, {105,  0.0f,  0}, {140,  0.0f,  1},
        {145,  0.0f,  1}, {150,  0.0f,  1}, {229,  0.0f,  3},
        {231,  0.0f, -1}, {240,  0.0f, -1},
        {101, 50.0f,  1}, {105, 50.0f,  1}, {140, 50.0f,  2},
        {145, 50.0f,  2}, {150, 50.0f,  2}, {229, 50.0f,  4},
        {231, 50.0f, -1}, {240, 50.0f, -1},
    };
    for (auto& r : ra) {
        ScrollList s = make(20, r.off);
        check_i("row_at", s.row_at(r.y), r.exp);
    }

    /* --- visible_range (tight window == Skia draw cull) ------------- */
    struct { float off; int first; int last; } vr[] = {
        {0.0f,   0,  4},
        {50.0f,  1,  5},
        {630.0f, 16, 20},   /* max scroll */
    };
    for (auto& v : vr) {
        ScrollList s = make(20, v.off);
        int f = -9, l = -9;
        s.visible_range(f, l);
        check_i("vis_first", f, v.first);
        check_i("vis_last",  l, v.last);
        /* every index the tight range reports MUST pass the per-row cull,
         * and the neighbours just outside MUST fail it — proves tightness */
        for (int i = f; i < l; i++)
            check_i("vis_in_cull", s.row_visible(i) ? 1 : 0, 1);
        if (f > 0)
            check_i("vis_below",  s.row_visible(f - 1) ? 1 : 0, 0);
        if (l < s.row_count)
            check_i("vis_above",  s.row_visible(l) ? 1 : 0, 0);
    }

    /* --- classify(): gutter tap-zone paging (drag-less fallback) ---- */
    {
        ScrollList s = make(20, 315.0f);   /* mid scroll (max 630) */
        s.gutter_w = 16.0f;                /* right 16px = scrollbar gutter */
        float gx = s.left + s.width - 4.0f;/* inside the gutter */
        /* thumb centre = top + (offset/max)*viewport = 100 + 0.5*130 = 165 */
        ScrollHit up = s.classify(gx, 130.0f);
        check_i("gutter_up",   (int)up.kind, (int)ScrollHitKind::PageUp);
        ScrollHit dn = s.classify(gx, 200.0f);
        check_i("gutter_down", (int)dn.kind, (int)ScrollHitKind::PageDown);
        /* a tap left of the gutter is a row hit, not paging */
        ScrollHit row = s.classify(10.0f, 105.0f);
        check_i("row_hit_kind", (int)row.kind, (int)ScrollHitKind::Row);
        /* outside the viewport entirely */
        ScrollHit none = s.classify(10.0f, 50.0f);
        check_i("outside_none", (int)none.kind, (int)ScrollHitKind::None);
    }

    /* --- viewport_rect / row_y geometry ----------------------------- */
    {
        ScrollList s = make(20, 50.0f);
        Rect vp = s.viewport_rect();
        check_f("vp_x", vp.x, 0.0f);
        check_f("vp_y", vp.y, 100.0f);
        check_f("vp_w", vp.w, 260.0f);
        check_f("vp_h", vp.h, 130.0f);
        /* row_y(i) = top + pad - offset + i*38 = 52 + 38i */
        check_f("row_y0", s.row_y(0),  52.0f);
        check_f("row_y3", s.row_y(3), 166.0f);
    }

    if (g_fail == 0) {
        std::printf("ALL PASS\n");
        return 0;
    }
    std::printf("%d CHECK(S) FAILED\n", g_fail);
    return 1;
}
