/*
 * overlay.h — reusable modal-overlay (scrim + panel + hit-capture)
 * substrate for the CYD/ESP32 embedded tier ([CXC-R-DIALOG]).
 *
 * The FIX-ONCE enabler behind `dialog` and any future modal (a
 * model_picker-style popup): the Skia/Python engine models a modal as a
 * full-surface SCRIM with a centered PANEL that CAPTURES all pointer
 * input while visible (DialogWidget.hit_test_tree —
 * composeio/render/widgets/dialog.py).  This helper extracts that model
 * ONCE (composed, not baked into one widget — the scroll_list / text_wrap
 * idiom) so every modal port reuses the SAME geometry + hit-capture
 * classification instead of re-deriving (and silently forking) it.
 *
 * TWO layers of "capture":
 *
 *  1. CROSS-WIDGET z-capture (nothing behind the scrim is reachable) is
 *     delivered by the SCENE, not this substrate: the flat CYD touch
 *     registry (touch_dispatch.cpp) hit-tests in REVERSE z-order and
 *     returns the topmost widget whose rect contains the tap.  A modal
 *     registered LAST with a FULL-VIEWPORT rect therefore swallows EVERY
 *     tap before it can reach the content beneath — the flat reverse-z
 *     registry already IS modal z-capture, provided the modal is the last
 *     interactive entry and covers the surface (compose_scene forces the
 *     dialog rect to the full viewport, and authoring places it last, the
 *     dialog.py "last child of a full-canvas pixel_stack" rule).
 *
 *  2. WITHIN-MODAL classification (which affordance a captured tap hit, or
 *     whether it fell on the scrim / dead panel space) is THIS substrate's
 *     job — overlay_classify below.  It mirrors DialogWidget.on_tap:
 *       - a tap inside a registered affordance FIRES it;
 *       - a tap on the scrim DISMISSES (when dismissible);
 *       - a tap on dead panel space is SWALLOWED (no-op).
 *
 * Pure math — depends only on Rect (widget.h); no Canvas, no ESP-IDF —
 * so it is host-compilable and parity-tested off-hardware
 * (env:native_overlay_test), like scroll_list / text_wrap.
 */

#ifndef OVERLAY_H
#define OVERLAY_H

#include "widget.h"   /* Rect */

/* Half-open containment — IDENTICAL to touch_dispatch.cpp hit_test
 * (x in [r.x, r.x+r.w), y in [r.y, r.y+r.h)), so the within-modal
 * classification can never disagree with the flat registry's z-capture. */
bool overlay_contains(const Rect& r, float x, float y);

/* Centered panel rect inside a viewport (DialogWidget._arrange_children
 * non-anchor branch): the panel is clamped to the viewport with a `margin`
 * inset on every side (dialog.py uses inner.w-16 / inner.h-16 → margin 8
 * per side), then centered.  A panel larger than the viewport is clamped;
 * a panel smaller is centered untouched. */
Rect overlay_center_panel(float vx, float vy, float vw, float vh,
                          float panel_w, float panel_h, float margin = 8.0f);

/* What a captured modal tap resolved to (DialogWidget.on_tap branches). */
enum class OverlayHit : uint8_t {
    Scrim,          /* outside the panel  → dismiss when dismissible   */
    PanelDeadSpace, /* inside panel, no affordance → swallow (no-op)   */
    Affordance,     /* inside affordance[index] → fire it              */
};

struct OverlayClassify {
    OverlayHit kind;
    int affordance;   /* index into affs when kind==Affordance, else -1 */
};

/* Classify a tap (x, y) already known to be inside the modal's captured
 * rect (the scene z-capture guarantees this).  `affs` are the panel's
 * tappable affordance rects, scanned in REVERSE order (topmost-first, the
 * scene z-order convention) so an overlapping later affordance wins — the
 * DialogWidget.hit_test_tree `reversed(children)` rule.  Returns:
 *   {Affordance, i}      when (x,y) is inside affs[i];
 *   {PanelDeadSpace, -1} when inside `panel` but no affordance;
 *   {Scrim, -1}          when outside `panel` (the scrim). */
OverlayClassify overlay_classify(const Rect& panel, const Rect* affs, int n,
                                 float x, float y);

#endif /* OVERLAY_H */
