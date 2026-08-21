/*
 * tab.h — Segmented tab-bar selector ([CXC-PRIM-TIER-PARITY] port of
 * composeio/render/widgets/tab.py, the base TabWidget).
 *
 * Renders the base TabWidget's segmented BAR faithfully: a rounded
 * SEG_BG strip, centred over the widget rect, with one variable-width
 * cell per label (cell width = max(70, text+28)), the active cell filled
 * SEG_SELECTED, labels centred in SEG_TEXT.  Tapping a cell moves the
 * selected indicator (local UI state — the base tab keys selection off
 * its own children, exactly as tab.py does; a tap does NOT publish).
 *
 * Deliberately NOT ported here (subclass concerns — see tab.py's
 * subclasses on the Skia tier):
 *   - per-tab CONTENT containers.  On this tier configs are flat
 *     pixel_stack (no nested tab pages), so the bar is the whole widget,
 *     mirroring how the headless Skia reference renders a tab (children
 *     are never arranged -> bar only).
 *   - the index publish/subscribe seam.  The BASE tab.py has no
 *     `namespace:` and no BINDABLE_ATTRS_BY_TYPE row; only console_tabs /
 *     master_detail / cic_tab_row add one (each with its own payload
 *     shape).  Adding an embedded-only index binding here would silently
 *     FORK tier behaviour from the Skia base (reactive_apply.h's explicit
 *     warning).  A future cic_tab_row port SUBCLASSES this bar with the
 *     index-namespace seam, matching the Skia layering.
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_TAB_H
#define WIDGETS_TAB_H

#include "widget.h"
#include <cstdint>

class TabWidget : public Widget {
public:
    static constexpr int kMaxTabs = 8;

    explicit TabWidget(const char* id, int initial_tab = 0);

    /* Append a tab label (copied; extra tabs past kMaxTabs are dropped). */
    void add_label(const char* label);
    int label_count() const { return _count; }

    /* Move the selected indicator (clamped to [0, count); no-op if
     * unchanged or out of range — mirrors tab.py set_active). */
    void set_active(int index);
    int active() const { return _active; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    char _labels[kMaxTabs][32];
    int _count = 0;
    int _active = 0;

    /* Segment x-bounds + bar y-band, computed in draw() for tap mapping
     * (the embedded mirror of tab.py's _seg_bounds). */
    float _seg_x0[kMaxTabs];
    float _seg_x1[kMaxTabs];
    int _seg_count = 0;
    float _bar_top = 0.0f;      /* rect.y (top of the tap band)   */
    float _bar_bot = 0.0f;      /* rect.y + bar_h (bottom of band) */
};

#endif /* WIDGETS_TAB_H */
