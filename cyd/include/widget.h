/*
 * widget.h — Widget base class for ComposeUX embedded rendering
 *
 * Spec: [CXC-R-WIDGET-CONTRACT]
 *
 * Port of the Python Widget base class. Phase 1a scope: measure() and draw().
 * Interaction and bus binding stubs present for API symmetry.
 */

#ifndef WIDGET_H
#define WIDGET_H

#include "canvas.h"

struct Rect {
    float x, y, w, h;
};

struct Constraints {
    float max_width;
    float max_height;
};

/* ------------------------------------------------------------------ *
 * SUB-WIDGET DAMAGE ([CXC-ER-DAMAGE])
 *
 * The dirty tracker's currency used to be the widget RECT: change one
 * attribute and every scanline the widget occupies repaints.  Measured
 * on the ESP32-C3 / ST7796S 480x320 band tier, one segment of a
 * ten-segment thrust bar (10.6 rows) invalidated 176 rows, and SPI
 * transfer is 73% of a repaint — so the granularity WAS the render
 * problem.
 *
 * The rect is a bound the DIRTY TRACKER can compute.  Which rows
 * actually changed is a question only the WIDGET can answer, because
 * only the widget knows its own internal geometry.  So the widget
 * answers it — and every widget that does not opt in keeps the exact
 * pre-existing rect behaviour (damage_rows() returning false is the
 * base-class default and the honest "I don't know").
 *
 * ⚠ THE FAILURE MODE IS UNDER-INVALIDATION: a span that misses a row
 * leaves a stale pixel that looks like memory corruption, is
 * content-dependent, and no smoke test will show it.  An override MUST
 * return a SUPERSET of the rows whose rendering can differ.  Verified
 * on hardware by the row-CRC damage audit (ESP32Canvas::audit_rows),
 * which is independent of this code: it re-renders the whole frame and
 * compares each row against a CRC of what was last SENT to the panel.
 * ------------------------------------------------------------------ */

/* A snapshot of the render-determining state of ONE bound attribute,
 * taken BEFORE the new value is applied.  POD, stack-allocated per
 * application — no ownership, no allocation. */
struct WidgetDamage {
    bool valid = false;      /* the widget filled this in                */
    float num = 0.0f;        /* numeric state (e.g. gauge value)         */
    char text[40] = {0};     /* string state (e.g. label text), exact    */
};

/* What a widget reports about the rows it changed.
 *
 * y1 < y0 means "nothing on the glass changed" — repaint NOTHING.
 *
 * bottom_up is a PRESENTATION hint, never a correctness one.  A band-tier
 * repaint cannot be atomic: 480x320 does not fit in SRAM, so a multi-band
 * span goes to the panel one band at a time and the wavefront is VISIBLE
 * (measured: a 0 -> 255 thrust jump is 15 transfers over 230 ms).  The
 * loop walked bands ascending because that is how loops are written, so a
 * bottom-filling LED bar appeared to light DOWNWARD — the owner's words,
 * "the segments get lit up in reverse order".  The final state was always
 * correct; only the paint order read backwards.
 *
 * The fix is not to hide the wavefront (that is wire-bound — see
 * [CXC-ER-DAMAGE] in canvas_esp32.h) but to point it the right way, and
 * the only party that knows which way a change GREW is the widget that
 * changed.  So the widget says. */
struct DamageSpan {
    float y0 = 1.0f;
    float y1 = 0.0f;
    bool bottom_up = false;  /* paint from y1 upward to y0 */
};

class Widget {
public:
    Widget(const char* id, const char* type)
        : _widget_id(id), _widget_type(type) {}

    virtual ~Widget() = default;

    const char* widget_id() const { return _widget_id; }
    const char* widget_type() const { return _widget_type; }

    /* -- Measure / Draw (subclasses MUST override) ----------------- */

    virtual Size measure(const Constraints& constraints) = 0;
    virtual void draw(Canvas& canvas, const Rect& rect) = 0;

    /* -- Sub-widget damage ([CXC-ER-DAMAGE]) ----------------------- */

    /**
     * Snapshot whatever determines this widget's rendering of the bound
     * attribute *attr* (the .compose.yaml attr spelling: "value",
     * "text", ...), so a later damage_rows() can say what changed.
     *
     * Base: leaves valid=false, which makes damage_rows() unreachable
     * and the caller fall back to the whole rect.  Every widget that
     * does not override BOTH of these is bit-identical to the
     * pre-damage behaviour.
     */
    virtual void damage_snapshot(const char* attr, WidgetDamage& out) const {
        (void)attr;
        out.valid = false;
    }

    /**
     * Report the inclusive scene-row span [y0, y1] in which this
     * widget's drawing can differ from the state captured in *before*
     * (which was taken before the current value was applied).
     *
     * @param metrics  a live canvas, for widgets that need real text
     *                 metrics.  Called OUTSIDE a render pass, so
     *                 mutating its font state is safe (every render
     *                 pass begins with reset_draw_state()).
     * @return false => "I don't know" — the caller repaints the whole
     *         rect (the safe default).  true with out.y1 < out.y0 =>
     *         "nothing on the glass changed" — repaint NOTHING.
     *
     * ⚠ A returned span MUST be a superset of the changed rows.  See
     * the header note above; this is the one place under-invalidation
     * can be introduced.
     */
    virtual bool damage_rows(const char* attr, const WidgetDamage& before,
                             const Rect& rect, Canvas& metrics,
                             DamageSpan& out) const {
        (void)attr; (void)before; (void)rect; (void)metrics; (void)out;
        return false;
    }

    /* -- Interaction stubs (Phase 1c+) ----------------------------- */

    virtual bool hit_test(float x, float y) { return false; }
    virtual void on_tap(float x, float y) {}

private:
    const char* _widget_id;
    const char* _widget_type;
};

#endif /* WIDGET_H */
