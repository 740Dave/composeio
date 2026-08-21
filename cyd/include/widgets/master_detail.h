/*
 * master_detail.h — two-region master/detail nav viewer
 * ([CXC-PRIM-TIER-PARITY] port of
 *  composeio/render/widgets/master_detail.py).
 *
 * A LEFT nav-list of one-line section entries + a RIGHT detail pane that
 * renders the selected section.  Tapping a nav row selects it (and, on a
 * tier with a bus transport, publishes the section name on nav_namespace);
 * the detail pane swaps to show that section's content.
 *
 * TWO shared substrates are COMPOSED, never re-derived (reuse by
 * reference — R3):
 *
 *   - the LEFT nav column is a ScrollList (scroll_list.h): a variable
 *     number of fixed-height rows in a FIXED box SCROLLS rather than
 *     grows.  visible_range() yields the tight [first,last) window so
 *     draw() is O(visible rows), not O(sections) — band-renderer
 *     friendly; classify() resolves a tap into a row or a gutter
 *     page-up/page-down (the honest interim for the CYD's tap-on-release
 *     touch tier — no fabricated drag delta).  This is the SAME move
 *     checklist.cpp makes (the first substrate consumer).
 *   - the RIGHT detail body is wrapped with text_wrap.h — the one shared
 *     soft-wrap + per-glyph measurement path, so a line break never forks
 *     per tier.
 *
 * TIER REDUCTION (documented, not a silent fork).  On the Skia/Python
 * tier the detail pane renders the selected section's CHILD CONTAINER
 * (an arbitrary widget tree).  The embedded tier lays out flat
 * pixel_stack scenes only — nested child containers are the SAME
 * detail-layout seam the base TabWidget port declined (widgets/tab.h:
 * per-tab content is out of scope on this tier).  So this port renders
 * the faithful, config-shared fields it CAN: each section's `label:`
 * (the nav title, verbatim the Skia parser's label extraction) and its
 * `text:` (rendered as a wrapped detail body — the same field the Skia
 * reference shows when a section is a text leaf, `{label:, type: label,
 * text:}` -> a label widget in the detail container).  A section that is
 * a multi-widget container shows its title + an honest deferred note.
 * Full child-container detail layout stays deferred with flat-tier
 * nested layout ([CXC-RM-M5]).
 *
 * The selection seam (nav_namespace, the master_detail SUBCLASS's index
 * topic that tab.h referenced) is a namespace SUBSCRIPTION, not a
 * bindings.py BINDABLE_ATTRS_BY_TYPE row — mirroring checklist `items`
 * and dradis_contacts `source` (reactive_apply.h RAttr::NavSelection).
 * On-device inbound selection is deferred with the rest of on-device
 * MQTT binding ([CXC-RM-M5]); a TAP selects today.
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_MASTER_DETAIL_H
#define WIDGETS_MASTER_DETAIL_H

#include "widget.h"
#include "scroll_list.h"
#include <cstdint>

class MasterDetailWidget : public Widget {
public:
    static constexpr int kMaxSections = 16;   /* overflow sections dropped */
    static constexpr int kLabelCap = 48;      /* per-section nav label     */
    static constexpr int kDetailCap = 256;    /* per-section detail body   */

    typedef void (*PublishFn)(const char* ns, const char* value);

    MasterDetailWidget(const char* id,
                       float nav_width = 264.0f,
                       const char* nav_namespace = "_app/config/section",
                       int initial_tab = 0);

    /* Append a section (both strings copied/truncated).  `detail` may be
     * null/empty (title-only section).  Extra sections past kMaxSections
     * are dropped. */
    void add_section(const char* label, const char* detail);
    int  section_count() const { return _count; }

    /* Move the selection (clamped to [0, count); no-op if unchanged or out
     * of range — mirrors TabWidget/master_detail.py set_active). */
    void set_active(int index);
    int  active() const { return _active; }

    /* The bus value for a section = its lowercased label (master_detail.py
     * nav_value).  Writes into `buf` (cap bytes); returns buf. */
    const char* nav_value(int index, char* buf, int cap) const;

    /* Resolve a nav-selection NAME to its section index — the string arm of
     * master_detail.py _index_for: the value is trimmed + lowercased and
     * matched (case-insensitively, ASCII) against each section's label.
     * Returns the matched index, or -1 when nothing matches (the live
     * on_bus_state apply withholds on -1).  [CXC-RM-M5] RAttr::NavSelection. */
    int index_for(const char* value) const;

    static void set_publish_fn(PublishFn fn) { s_publish = fn; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    struct Section {
        char label[kLabelCap];
        char detail[kDetailCap];
    };

    void draw_nav(Canvas& canvas, const Rect& rect);
    void draw_detail(Canvas& canvas, float dx, float dy, float dw, float dh);

    Section    _sections[kMaxSections];
    int        _count = 0;
    int        _active = 0;
    float      _nav_width;
    const char* _nav_namespace;
    ScrollList _nav;   /* persistent scroll state + shared scroll math */

    static PublishFn s_publish;
};

#endif /* WIDGETS_MASTER_DETAIL_H */
