/*
 * emit_button.h — Momentary command button ([CXC-PRIM-TIER-PARITY] port
 * of composeio/render/widgets/emit_button.py).
 *
 * Where ButtonWidget toggles a state and publishes one scalar,
 * EmitButton fires a FIXED `emits:` list — each an explicit
 * {namespace, value} — in order, on every tap.  The physical-panel
 * command trigger.  Visual: the same LED-dot + rounded-body button look
 * (style.draw_dot + style.draw_button).
 *
 * Embedded honesty:
 *   - No pointer-down/up feed on this tier (tap-only touch), so the
 *     depressed/sink cue can't render — the body draws un-pressed.
 *   - The scene publish hook is log-only until a bus transport lands
 *     (like ButtonWidget); each emit's value is carried as its compact
 *     serialized text.
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_EMIT_BUTTON_H
#define WIDGETS_EMIT_BUTTON_H

#include "widget.h"
#include <cstdint>

class EmitButtonWidget : public Widget {
public:
    typedef void (*PublishFn)(const char* ns, const char* value);

    static constexpr int kMaxEmits = 8;

    EmitButtonWidget(const char* id, const char* label = "Emit");

    /* Append one emit (namespace + serialized value text); build-time. */
    void add_emit(const char* ns, const char* value_text);

    static void set_publish_fn(PublishFn fn) { s_publish = fn; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    const char* _label;
    char _ns[kMaxEmits][48];
    char _val[kMaxEmits][80];
    int _emit_count = 0;

    static PublishFn s_publish;
};

#endif /* WIDGETS_EMIT_BUTTON_H */
