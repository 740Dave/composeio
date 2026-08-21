/*
 * overlay_selftest.cpp — host parity harness for the modal-overlay
 * substrate (scrim + centered panel + hit-CAPTURE classification).
 *
 * Asserts the within-modal tap resolution reproduces DialogWidget.on_tap
 * (composeio/render/widgets/dialog.py): a tap inside an affordance FIRES
 * it; a tap on the scrim (outside the panel) dismisses/swallows; a tap on
 * dead panel space is swallowed; and (the cross-widget z-capture the
 * scene's reverse-z registry delivers) an overlapping later affordance
 * wins.  Also checks the centered-panel geometry (clamp + center).
 *
 * Runs off hardware:  pio run -e native_overlay_test && \
 *                     .pio/build/native_overlay_test/program
 * Must print "ALL PASS" and exit 0.
 */

#include "overlay.h"

#include <cstdio>
#include <cmath>

static int g_fail = 0;

static void check_f(const char* what, float got, float exp) {
    if (std::fabs(got - exp) > 1e-3f) {
        std::printf("  FAIL %-24s got=%.4f exp=%.4f\n", what, got, exp);
        g_fail++;
    }
}

static void check_i(const char* what, int got, int exp) {
    if (got != exp) {
        std::printf("  FAIL %-24s got=%d exp=%d\n", what, got, exp);
        g_fail++;
    }
}

int main() {
    std::printf("overlay parity harness (vs dialog.py modal model)\n");

    /* --- overlay_contains: half-open, matches touch_dispatch hit_test --- */
    {
        Rect r{10.0f, 20.0f, 100.0f, 40.0f};
        check_i("contains_inside",   overlay_contains(r, 50, 30) ? 1 : 0, 1);
        check_i("contains_tl_edge",  overlay_contains(r, 10, 20) ? 1 : 0, 1);
        check_i("contains_br_edge",  overlay_contains(r, 110, 60) ? 1 : 0, 0);
        check_i("contains_left_out", overlay_contains(r, 9, 30) ? 1 : 0, 0);
        check_i("contains_above",    overlay_contains(r, 50, 19) ? 1 : 0, 0);
    }

    /* --- overlay_center_panel: center + clamp to viewport-margin ------- */
    {
        /* viewport 320x240, panel 300x120, margin 8 -> pw=300, ph=120,
         * px=(320-300)/2=10, py=(240-120)/2=60 */
        Rect p = overlay_center_panel(0, 0, 320, 240, 300, 120, 8);
        check_f("center_pw", p.w, 300.0f);
        check_f("center_ph", p.h, 120.0f);
        check_f("center_px", p.x, 10.0f);
        check_f("center_py", p.y, 60.0f);
    }
    {
        /* panel wider than the viewport clamps to vw-2*margin=224 on a
         * 240-wide panel; then px = margin = 8. */
        Rect p = overlay_center_panel(0, 0, 240, 320, 300, 80, 8);
        check_f("clamp_pw", p.w, 224.0f);
        check_f("clamp_px", p.x, 8.0f);
        check_f("clamp_ph", p.h, 80.0f);   /* height fits, untouched */
        check_f("clamp_py", p.y, 120.0f);  /* (320-80)/2 */
    }
    {
        /* viewport offset (nested) is honored (vx,vy added). */
        Rect p = overlay_center_panel(50, 30, 200, 200, 100, 100, 8);
        check_f("offset_px", p.x, 100.0f);  /* 50 + (200-100)/2 */
        check_f("offset_py", p.y, 80.0f);   /* 30 + (200-100)/2 */
    }

    /* --- overlay_classify: the hit-capture core ------------------------ *
     * panel (10,60,300,120); abort button (24,150,133,32) LEFT,
     * confirm button (167,150,133,32) RIGHT — the dialog affordance row. */
    Rect panel{10, 60, 300, 120};
    Rect affs[2] = {
        Rect{24, 150, 133, 32},    /* [0] abort  (left)  */
        Rect{167, 150, 133, 32},   /* [1] confirm (right) */
    };

    /* a tap inside the confirm affordance FIRES affordance 1 */
    {
        OverlayClassify c = overlay_classify(panel, affs, 2, 230, 166);
        check_i("confirm_kind", (int)c.kind, (int)OverlayHit::Affordance);
        check_i("confirm_idx",  c.affordance, 1);
    }
    /* a tap inside the abort affordance FIRES affordance 0 */
    {
        OverlayClassify c = overlay_classify(panel, affs, 2, 40, 160);
        check_i("abort_kind", (int)c.kind, (int)OverlayHit::Affordance);
        check_i("abort_idx",  c.affordance, 0);
    }
    /* a tap in the panel title/body area (no affordance) is SWALLOWED */
    {
        OverlayClassify c = overlay_classify(panel, affs, 2, 150, 90);
        check_i("deadspace_kind", (int)c.kind, (int)OverlayHit::PanelDeadSpace);
        check_i("deadspace_idx",  c.affordance, -1);
    }
    /* a tap OUTSIDE the panel is the SCRIM (dismiss/swallow) */
    {
        OverlayClassify c = overlay_classify(panel, affs, 2, 5, 5);
        check_i("scrim_kind", (int)c.kind, (int)OverlayHit::Scrim);
        check_i("scrim_idx",  c.affordance, -1);
    }
    /* a tap just below the panel (still on the surface) is scrim */
    {
        OverlayClassify c = overlay_classify(panel, affs, 2, 150, 300);
        check_i("scrim_below", (int)c.kind, (int)OverlayHit::Scrim);
    }
    /* no affordances (a message-only modal): panel dead space swallows,
     * outside is scrim — a dismissible scrim tap is the only close path */
    {
        OverlayClassify in = overlay_classify(panel, nullptr, 0, 150, 100);
        check_i("noaff_dead", (int)in.kind, (int)OverlayHit::PanelDeadSpace);
        OverlayClassify out = overlay_classify(panel, nullptr, 0, 2, 2);
        check_i("noaff_scrim", (int)out.kind, (int)OverlayHit::Scrim);
    }
    /* REVERSE z-order: overlapping affordances — the LAST (topmost) wins,
     * mirroring hit_test_tree's reversed(children) scan. */
    {
        Rect ov[2] = {
            Rect{100, 150, 120, 32},   /* [0] under */
            Rect{150, 150, 120, 32},   /* [1] over, overlaps [0] in x[150,220) */
        };
        OverlayClassify c = overlay_classify(panel, ov, 2, 180, 160);
        check_i("overlap_topmost", c.affordance, 1);
        /* left of the overlap, only [0] covers -> [0] */
        OverlayClassify l = overlay_classify(panel, ov, 2, 110, 160);
        check_i("overlap_lower", l.affordance, 0);
    }

    if (g_fail == 0) {
        std::printf("ALL PASS\n");
        return 0;
    }
    std::printf("%d CHECK(S) FAILED\n", g_fail);
    return 1;
}
