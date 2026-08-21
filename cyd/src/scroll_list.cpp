/*
 * scroll_list.cpp — vertical scroll + uniform-row list math.
 *
 * See scroll_list.h for the model.  Every formula here mirrors the Skia
 * reference (composeio/render/widgets/doc_picker.py on_scroll/_row_at +
 * draw) so a ported widget scrolls IDENTICALLY to its desktop twin.
 *
 * Canvas-free / ESP-IDF-free on purpose — host-compilable and parity
 * tested off hardware (env:native_scroll_test).
 */

#include "scroll_list.h"

#include <algorithm>
#include <cmath>

/* ---- model math (exact Skia parity) --------------------------------- */

float ScrollList::content_height() const {
    if (row_count <= 0) return 0.0f;
    /* Skia: content_h = len(rows) * (row_h + row_gap) — includes the
     * trailing gap, matching doc_picker.on_scroll exactly. */
    return static_cast<float>(row_count) * step();
}

float ScrollList::max_scroll() const {
    float m = content_height() - viewport_h;
    return m > 0.0f ? m : 0.0f;   /* Skia: max(0, content_h - _LIST_H) */
}

float ScrollList::clamp_offset(float v) const {
    /* Skia: max(0, min(max_scroll, v)) */
    float m = max_scroll();
    if (v < 0.0f) return 0.0f;
    if (v > m) return m;
    return v;
}

void ScrollList::set_offset(float v) {
    offset = clamp_offset(v);
}

void ScrollList::scroll_by_wheel(float dy, float step_px) {
    /* Skia on_scroll: self._scroll = clamp(self._scroll - dy * 20.0) */
    set_offset(offset - dy * step_px);
}

void ScrollList::scroll_by(float delta_px) {
    set_offset(offset + delta_px);
}

void ScrollList::page_up() {
    /* 20% overlap between pages (the conventional page step). */
    set_offset(offset - viewport_h * 0.8f);
}

void ScrollList::page_down() {
    set_offset(offset + viewport_h * 0.8f);
}

/* ---- layout --------------------------------------------------------- */

Rect ScrollList::viewport_rect() const {
    return Rect{left, top, width, viewport_h};
}

float ScrollList::row_y(int i) const {
    /* Skia draw: ry = top + pad - scroll; ry += (row_h+row_gap) per row. */
    return top + pad - offset + static_cast<float>(i) * step();
}

bool ScrollList::row_visible(int i) const {
    if (i < 0 || i >= row_count) return false;
    float ry = row_y(i);
    /* Skia draw cull: ry + row_h >= top && ry <= top + viewport_h. */
    return (ry + row_h >= top) && (ry <= top + viewport_h);
}

void ScrollList::visible_range(int& first, int& last) const {
    float s = step();
    if (s <= 0.0f || row_count <= 0 || viewport_h <= 0.0f) {
        first = 0;
        last = 0;
        return;
    }
    /* Solve the Skia cull predicate for i (tight window — O(visible),
     * band-renderer friendly):
     *   ry + row_h >= top          -> i >= (offset - pad - row_h) / s
     *   ry        <= top+viewport  -> i <= (offset - pad + viewport_h) / s
     * The bounds are inclusive (>=, <=), so first = ceil(lo), last-1 =
     * floor(hi).  A small epsilon keeps a row whose edge touches the
     * viewport EXACTLY (an integer solve nudged by float rounding) from
     * being culled off by one. */
    const float kEps = 1e-4f;
    int f = static_cast<int>(std::ceil((offset - pad - row_h) / s - kEps));
    int l = static_cast<int>(std::floor((offset - pad + viewport_h) / s + kEps));
    if (f < 0) f = 0;
    if (l >= row_count) l = row_count - 1;
    first = f;
    last = (l >= f) ? (l + 1) : f;
}

/* ---- hit testing ---------------------------------------------------- */

bool ScrollList::in_viewport(float y) const {
    /* Skia _row_at gate: y < top or y > top + _LIST_H -> outside. */
    return y >= top && y <= top + viewport_h;
}

int ScrollList::row_at(float y) const {
    if (!in_viewport(y)) return -1;
    float s = step();
    if (s <= 0.0f) return -1;
    /* Skia _row_at: ly = y - top + scroll; idx = int(ly // step);
     * hit only if the remainder falls in the row body (not the gap):
     *   0 <= idx < n  and  (ly % step) <= row_h.
     * The Skia `top` already folds in the pad (r.y+HDR+2); here pad is
     * explicit, so the anchor is (top + pad). */
    float ly = y - (top + pad) + offset;
    if (ly < 0.0f) return -1;
    int idx = static_cast<int>(std::floor(ly / s));
    if (idx < 0 || idx >= row_count) return -1;
    float rem = ly - static_cast<float>(idx) * s;   /* == fmod(ly, s), ly>=0 */
    if (rem > row_h) return -1;                      /* landed in the gap */
    return idx;
}

ScrollHit ScrollList::classify(float x, float y) const {
    ScrollHit hit;
    if (!in_viewport(y)) return hit;               /* None */

    /* Right-edge scrollbar gutter — the drag-less paging fallback.  Only
     * active when there is more content than fits AND a gutter is set. */
    if (gutter_w > 0.0f && max_scroll() > 0.0f &&
        x >= left + width - gutter_w && x <= left + width) {
        /* Thumb centre tracks the scroll fraction; tap above -> page up,
         * below -> page down. */
        float frac = (max_scroll() > 0.0f) ? (offset / max_scroll()) : 0.0f;
        float thumb_c = top + frac * viewport_h;
        hit.kind = (y < thumb_c) ? ScrollHitKind::PageUp
                                 : ScrollHitKind::PageDown;
        return hit;
    }

    int idx = row_at(y);
    if (idx >= 0) {
        hit.kind = ScrollHitKind::Row;
        hit.row = idx;
    }
    return hit;
}

/* ---- drag session (drag-READY) -------------------------------------- */

void ScrollList::drag_begin(float y) {
    _drag_y0 = y;
    _drag_off0 = offset;
}

void ScrollList::drag_update(float y) {
    /* Natural touch: dragging the content DOWN (y increases) reveals rows
     * ABOVE, so the offset DECREASES by the travel. */
    set_offset(_drag_off0 - (y - _drag_y0));
}

/* ---- variable-height content (non-breaking; see scroll_list.h) ------ */

float ScrollList::max_scroll_for(float content_h) const {
    float m = content_h - viewport_h;
    return m > 0.0f ? m : 0.0f;   /* same shape as max_scroll(), explicit content */
}

float ScrollList::clamp_offset_for(float v, float content_h) const {
    float m = max_scroll_for(content_h);
    if (v < 0.0f) return 0.0f;
    if (v > m) return m;
    return v;
}

void ScrollList::set_offset_for(float content_h) {
    offset = clamp_offset_for(offset, content_h);
}

void ScrollList::page_up_for(float content_h) {
    offset = clamp_offset_for(offset - viewport_h * 0.8f, content_h);
}

void ScrollList::page_down_for(float content_h) {
    offset = clamp_offset_for(offset + viewport_h * 0.8f, content_h);
}

ScrollHit ScrollList::classify_var(float x, float y, float content_h) const {
    ScrollHit hit;
    if (!in_viewport(y)) return hit;               /* None */
    if (gutter_w > 0.0f && max_scroll_for(content_h) > 0.0f &&
        x >= left + width - gutter_w && x <= left + width) {
        float m = max_scroll_for(content_h);
        float frac = (m > 0.0f) ? (offset / m) : 0.0f;
        float thumb_c = top + frac * viewport_h;
        hit.kind = (y < thumb_c) ? ScrollHitKind::PageUp
                                 : ScrollHitKind::PageDown;
    }
    return hit;                                     /* None if outside gutter */
}
