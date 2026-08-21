/*
 * overlay.cpp — modal-overlay substrate implementation (pure math).
 *
 * Mirror of composeio/render/widgets/dialog.py geometry
 * (_arrange_children centering) + hit-capture (hit_test_tree /
 * on_tap branch resolution).  See overlay.h.
 */

#include "overlay.h"

bool overlay_contains(const Rect& r, float x, float y) {
    return x >= r.x && x < r.x + r.w &&
           y >= r.y && y < r.y + r.h;
}

Rect overlay_center_panel(float vx, float vy, float vw, float vh,
                          float panel_w, float panel_h, float margin) {
    float max_w = vw - 2.0f * margin;
    float max_h = vh - 2.0f * margin;
    float pw = panel_w;
    float ph = panel_h;
    if (pw > max_w) pw = max_w;
    if (ph > max_h) ph = max_h;
    if (pw < 0.0f) pw = 0.0f;
    if (ph < 0.0f) ph = 0.0f;
    float px = vx + (vw - pw) / 2.0f;
    float py = vy + (vh - ph) / 2.0f;
    return Rect{px, py, pw, ph};
}

OverlayClassify overlay_classify(const Rect& panel, const Rect* affs, int n,
                                 float x, float y) {
    /* Topmost-first (reverse z), matching DialogWidget.hit_test_tree's
     * reversed(children) scan. */
    for (int i = n - 1; i >= 0; i--) {
        if (overlay_contains(affs[i], x, y))
            return OverlayClassify{OverlayHit::Affordance, i};
    }
    if (overlay_contains(panel, x, y))
        return OverlayClassify{OverlayHit::PanelDeadSpace, -1};
    return OverlayClassify{OverlayHit::Scrim, -1};
}
