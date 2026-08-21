/*
 * checklist.h — scrollable status-list / checklist readout
 * ([CXC-PRIM-TIER-PARITY] port of
 *  composeio/render/widgets/nightwatch_checklist.py, generalized).
 *
 * A leaf that renders a data-driven task list, one compact row each:
 *
 *     <marker>  <label>                                   <STATUS>
 *
 * Three DISTINCT visual states — the marker AND the right-aligned status
 * tag both carry the state colour, so the state is legible two ways:
 *
 *   * done     — a FILLED ok-green disc with a drawn check + "DONE" in ok.
 *   * partial  — a HALF-filled warn-amber ring + "PARTIAL" in warn.
 *   * deferred — a faint HOLLOW ring + "DEFERRED" in faint (the calm rule,
 *     [CXC-ICV-TOKENS]: a deferred task is "not yet", never an alarm).
 *
 * VARIABLE row count in a FIXED box -> the list SCROLLS its rows rather
 * than growing.  The scroll model is NOT re-derived here: it is COMPOSED
 * from the shared ScrollList substrate (scroll_list.h) — the fix-once
 * enabler behind master_detail / bus_monitor / doc_picker too.  This
 * widget is the FIRST consumer, proving the substrate:
 *
 *   - ScrollList owns content_h / max_scroll / offset / clamp,
 *   - visible_range() yields the tight [first,last) window so draw() is
 *     O(visible rows), not O(rows) — band-renderer friendly,
 *   - row_y(i) places each visible row,
 *   - classify() resolves a tap into gutter page-up / page-down (the
 *     honest interim for the CYD's tap-on-release touch tier — no fake
 *     drag delta the substrate can't observe).
 *
 * The static `tasks:` / `items:` list in the scene spec is the render
 * DEFAULT (exactly like a label's `text`); a live publisher on the
 * widget's `namespace:` overrides it (the on_bus_state analog — see
 * reactive_apply.h RAttr::ChecklistItems; the on-device JSON-apply path
 * is deferred with the rest of on-device MQTT binding, [CXC-RM-M5]).
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_CHECKLIST_H
#define WIDGETS_CHECKLIST_H

#include "widget.h"
#include "scroll_list.h"
#include <cstdint>

class ChecklistWidget : public Widget {
public:
    static constexpr int kMaxRows = 64;   /* overflow rows are dropped */
    static constexpr int kLabelCap = 48;  /* per-row label buffer      */

    enum class Status : uint8_t { Done, Partial, Deferred };

    explicit ChecklistWidget(const char* id, float font_size = 12.5f);

    /* Append a row (label copied; status normalized — unknown => Deferred).
     * Extra rows past kMaxRows are dropped. */
    void add_row(const char* label, const char* status);
    void clear_rows();
    int  row_count() const { return _count; }

    /* Row inspectors (host-test / introspection; empty/Deferred out of range). */
    const char* row_label(int i) const {
        return (i >= 0 && i < _count) ? _rows[i].label : "";
    }
    Status row_status(int i) const {
        return (i >= 0 && i < _count) ? _rows[i].status : Status::Deferred;
    }

    /* Live on_bus_state apply ([CXC-RM-M5], [CXC-ER-RENDER]): replace the row
     * set from a JSON payload matching nightwatch_checklist.py _coerce exactly
     * ([SPEC:CXC-EMBED-REACTIVE]).  Accepts a bare array or a {"tasks":[...]}
     * wrapper; each element is {"label"|"name":str, "status":str} or a bare
     * string (-> deferred).  Returns TRUE when a valid list container was
     * parsed and the rows were replaced; FALSE (no change) when the payload is
     * not a JSON object/array (the python `_coerce -> None` no-op path — a
     * malformed publish never wipes the last good list).  Bounded by kMaxRows
     * (overflow rows dropped; NO dynamic allocation — band-tier memory cap). */
    bool set_items_json(const char* json);

    /* Canon status normalization (done/partial/deferred; else deferred). */
    static Status norm_status(const char* s);

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    struct Row {
        char   label[kLabelCap];
        Status status;
    };

    void draw_marker(Canvas& canvas, float cx, float cy, Status st);

    Row        _rows[kMaxRows];
    int        _count = 0;
    float      _font_size;
    ScrollList _sl;   /* persistent scroll state + shared scroll math */
};

#endif /* WIDGETS_CHECKLIST_H */
