/*
 * led.h — LED status widget for ComposeUX embedded rendering
 *
 * Port of the Python LEDWidget from composeio/render/widgets/led.py.
 * Draws a circular LED with radial-gradient body, glow effect,
 * specular highlight, ring outline, and optional label.
 *
 * States: on, off, warning, danger + custom color override.
 *
 * Uses ONLY the abstract Canvas contract — no platform imports.
 */

#ifndef WIDGETS_LED_H
#define WIDGETS_LED_H

#include "widget.h"
#include <cstdint>

/* LED state enum */
enum class LEDState : uint8_t {
    On,
    Off,
    Warning,
    Danger,
};

/* Color triplet */
struct RGB {
    uint8_t r, g, b;
};

class LEDWidget : public Widget {
public:
    /**
     * @param id       Unique widget ID
     * @param state    Initial state
     * @param label    Optional text label below the LED (nullptr for none)
     */
    LEDWidget(const char* id,
              LEDState state = LEDState::Off,
              const char* label = nullptr);

    /* Set state */
    void set_state(LEDState state) { _state = state; }
    LEDState state() const { return _state; }

    /* Set custom color (only used when state != Off) */
    void set_custom_color(uint8_t r, uint8_t g, uint8_t b);
    void clear_custom_color();

    /* Set label */
    void set_label(const char* label) { _label = label; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

    /* -- Touch interaction [CYD-TOUCH-WIDGET-TAP] ------------------- */
    void on_tap(float x, float y) override;

private:
    LEDState _state;
    const char* _label;
    bool _has_custom_color = false;
    RGB _custom_color = {0, 0, 0};

    /* Resolve active color from state + custom override */
    RGB active_color() const;
};

#endif /* WIDGETS_LED_H */
