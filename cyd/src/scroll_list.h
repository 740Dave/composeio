/*
 * scroll_list.h — reusable vertical scroll + uniform-row list substrate
 * for the CYD/ESP32 embedded tier.
 *
 * The FIX-ONCE enabler behind master_detail, checklist/status_list,
 * bus_monitor and doc_picker: those Skia/Python widgets are all a
 * clipped viewport over a taller column of uniform rows, offset by a
 * scroll position.  This helper extracts that model ONCE (composed, not
 * baked into one widget — the text_wrap idiom) so each port reuses the
 * SAME scroll math instead of re-deriving (and silently forking) it.
 *
 * Scroll model — a faithful mirror of the Skia reference
 * (composeio/render/widgets/doc_picker.py, widget_select.py):
 *
 *     content_h  = row_count * (row_h + row_gap)
 *     max_scroll = max(0, content_h - viewport_h)
 *     offset     clamped to [0, max_scroll]
 *     draw:  ry = top + pad - offset;  row i at ry + i*(row_h+row_gap)
 *     hit:   ly = y - (top+pad) + offset;  idx = floor(ly / (row_h+row_gap))
 *
 * Band-renderer friendly: this NEVER allocates the content.  The widget
 * clips to viewport_rect() and draws ONLY visible_range() rows — the
 * work per frame is O(visible rows), not O(content), so a 200-row list
 * costs the same as a 6-row one on the 16-scanline band budget.
 *
 * Touch: the CYD touch substrate (touch_dispatch.cpp) is tap-on-release
 * ONLY — it tracks the DOWN position but never dispatches move deltas, so
 * continuous drag-to-scroll is NOT available on this tier.  This helper
 * therefore ships BOTH input paths:
 *   - drag_begin/drag_update: the full pixel-delta drag core, drag-READY
 *     so the layout half is complete the moment touch_dispatch gains a
 *     move dispatch (the documented follow-up);
 *   - page_up/page_down + classify(): the HONEST interim — a right-edge
 *     scrollbar gutter whose tap zones page the list.  A widget wires
 *     classify() to its on_tap so scrolling works today WITHOUT faking a
 *     drag delta the substrate can't actually observe.
 * scroll_by_wheel() mirrors the Skia on_scroll(dy) exactly for the
 * desktop/window tier and host parity tests.
 *
 * Pure math — depends only on Rect (widget.h); no Canvas, no ESP-IDF —
 * so it is host-compilable and parity-tested off-hardware
 * (env:native_scroll_test).
 */

#ifndef SCROLL_LIST_H
#define SCROLL_LIST_H

#include "widget.h"   /* Rect */

/* What a tap in a scroll-list resolved to (the tap-zone fallback for the
 * drag-less touch tier). */
enum class ScrollHitKind : uint8_t {
    None,       /* outside the list                             */
    Row,        /* a content row (index in ScrollHit.row)       */
    PageUp,     /* scrollbar gutter above the thumb → page up   */
    PageDown,   /* scrollbar gutter below the thumb → page down */
};

struct ScrollHit {
    ScrollHitKind kind = ScrollHitKind::None;
    int row = -1;   /* valid only when kind == Row */
};

class ScrollList {
public:
    /* -- geometry (set each draw from the widget's arranged viewport) -- */
    float top        = 0.0f;  /* screen y of the viewport top             */
    float left       = 0.0f;  /* screen x of the viewport left            */
    float width      = 0.0f;  /* viewport width                           */
    float viewport_h = 0.0f;  /* visible height (Skia _LIST_H)            */
    float row_h      = 0.0f;  /* uniform row height                       */
    float row_gap    = 0.0f;  /* gap between rows                         */
    float pad        = 0.0f;  /* top inset inside the viewport (Skia: +2) */
    int   row_count  = 0;     /* number of content rows                   */

    /* scrollbar gutter width; 0 disables the tap-zone fallback (row taps
     * span the full width).  When > 0, taps in the right `gutter_w` map
     * to page up/down and the widget may draw a thumb. */
    float gutter_w   = 0.0f;

    /* -- persistent scroll state (owned by the widget across frames) -- */
    float offset = 0.0f;      /* scroll position, always in [0, max_scroll] */

    /* -- model math (exact Skia parity; pad-independent) -------------- */
    float step() const { return row_h + row_gap; }
    float content_height() const;
    float max_scroll() const;
    float clamp_offset(float v) const;
    void  set_offset(float v);                 /* clamps into range */

    /* Skia on_scroll(dy) parity: offset -= dy * step_px, clamped.  Wheel
     * notches, not pixels — the desktop/window path. */
    void  scroll_by_wheel(float dy, float step_px = 20.0f);

    /* Direct pixel delta (a real drag): offset += delta_px, clamped. */
    void  scroll_by(float delta_px);

    /* Tap-zone paging (the drag-less interim).  A page overlaps the
     * previous by 20% (moves 0.8 * viewport_h), clamped. */
    void  page_up();
    void  page_down();

    /* -- layout ------------------------------------------------------- */
    Rect  viewport_rect() const;               /* {left, top, width, viewport_h} */
    float row_y(int i) const;                  /* screen y of row i's top */
    bool  row_visible(int i) const;            /* row i intersects the viewport */
    void  visible_range(int& first, int& last) const; /* tight [first,last) */

    /* -- hit testing -------------------------------------------------- */
    bool  in_viewport(float y) const;
    int   row_at(float y) const;               /* row index at y, or -1 */
    ScrollHit classify(float x, float y) const;/* gutter-aware tap resolve */

    /* -- drag session (drag-READY; wired when touch move dispatch lands) */
    void  drag_begin(float y);
    void  drag_update(float y);

    /* -- variable-height content (NON-BREAKING additions) ------------
     * The uniform-row model above assumes content_height = row_count*step.
     * A consumer whose items are VARIABLE height (chat_thread's word-wrapped
     * bubbles — line count, and therefore height, differs per message)
     * computes its OWN cumulative content height and drives the scroll
     * state through these *_for(content_h) overloads.  They take the content
     * height EXPLICITLY and never read row_count / row_h / step(), so the
     * uniform-row API (checklist / master_detail depend on it) is untouched —
     * this is a separate clamp-only use, exactly the sanctioned shape.  Only
     * `offset` is shared; the widget still owns it across frames.
     *
     * Culling and per-item placement are the widget's job (it walks its own
     * cumulative offsets and skips items outside [top, top+viewport_h]); the
     * substrate contributes the SAME clamp / paging / gutter math the
     * uniform path uses, just against an explicit content height. */
    float max_scroll_for(float content_h) const;   /* max(0, content_h - viewport_h) */
    float clamp_offset_for(float v, float content_h) const;
    void  set_offset_for(float content_h);          /* re-clamp `offset` into range */
    void  page_up_for(float content_h);             /* -0.8*viewport, clamped */
    void  page_down_for(float content_h);           /* +0.8*viewport, clamped */
    /* Gutter-aware tap resolve for a variable-height list: a tap in the
     * right `gutter_w` above/below the thumb pages up/down (thumb fraction =
     * offset / max_scroll_for(content_h)); there is NO Row hit (variable
     * items are not uniform rows — the widget hit-tests its own content).
     * Returns None outside the gutter. */
    ScrollHit classify_var(float x, float y, float content_h) const;

private:
    float _drag_y0   = 0.0f;
    float _drag_off0 = 0.0f;
};

#endif /* SCROLL_LIST_H */
