/*
 * button.h — Button widget for ComposeUX embedded rendering (M5)
 *
 * Port of the Python ButtonWidget (composeio/render/widgets/button.py)
 * visual spec: flat LED status dot in a 28px gutter + CTk-look rounded
 * button body with centered label (widgets/style.py constants).
 *
 * Embedded subset (honest): binary state engine only (on/off toggle),
 * led_behavior follow_state | solid | off (blink_500ms needs the
 * behavior engine — not on this tier yet).  Tap toggles the state,
 * updates the LED, and invokes the scene publish hook (bus publish
 * when a transport exists; logged otherwise).
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_BUTTON_H
#define WIDGETS_BUTTON_H

#include "widget.h"
#include <cstdint>

class ButtonWidget : public Widget {
public:
    typedef void (*PublishFn)(const char* ns, const char* value);

    ButtonWidget(const char* id,
                 const char* label = "Button",
                 const char* publish_namespace = nullptr);

    void set_led_behavior(const char* b) { _led_behavior = b; }
    void set_active(bool a) { _active = a; }
    bool active() const { return _active; }

    /* Scene-level publish hook (set once by the scene builder). */
    static void set_publish_fn(PublishFn fn) { s_publish = fn; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    const char* _label;
    const char* _publish_namespace;
    const char* _led_behavior = "follow_state";
    bool _active = false;
    bool _pressed = false;

    static PublishFn s_publish;
};

#endif /* WIDGETS_BUTTON_H */
