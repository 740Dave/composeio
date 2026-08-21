/*
 * checkbox.h — Labeled checkbox input ([CXC-PRIM-TIER-PARITY] port of
 * composeio/render/widgets/checkbox.py).
 *
 * A 24px rounded box (3px border when unchecked; accent fill + checkmark
 * when checked) with a label to the right.  Tapping toggles it and
 * publishes on_value / off_value to the bound namespace (through the
 * shared scene publish hook — logged until a bus transport lands on this
 * tier, like ButtonWidget).  An inbound bus value can preset it (R3
 * binding -> RAttr::Checked).
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_CHECKBOX_H
#define WIDGETS_CHECKBOX_H

#include "widget.h"
#include <cstdint>

class CheckboxWidget : public Widget {
public:
    typedef void (*PublishFn)(const char* ns, const char* value);

    CheckboxWidget(const char* id,
                   const char* label = "",
                   const char* on_value = "on",
                   const char* off_value = "off",
                   const char* bound_namespace = nullptr);

    void set_checked(bool c) { _checked = c; }
    bool checked() const { return _checked; }

    static void set_publish_fn(PublishFn fn) { s_publish = fn; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    const char* _label;
    const char* _on_value;
    const char* _off_value;
    const char* _namespace;
    bool _checked = false;

    static PublishFn s_publish;
};

#endif /* WIDGETS_CHECKBOX_H */
