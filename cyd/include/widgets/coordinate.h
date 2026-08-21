/*
 * coordinate.h — multi-digit coordinate entry with an on-screen numeric
 * keypad (M5 port of coordinate.py, backlog #10).
 *
 * The desktop CoordinateWidget captures digits through a HARDWARE keyboard
 * (coordinate.py on_key).  An ESP32 panel has only touch, so this port
 * ADDS an on-screen numeric keypad (the keyboard.cpp substrate,
 * numeric_keypad()) below the same digit-display + status-LED chrome the
 * Skia widget draws.  The digit EDIT MODEL is the substrate's — digit-only
 * append capped at max_digits, Backspace pops the last, exactly
 * coordinate.py — so the entry logic never forks per tier (host-verified
 * by env:native_keyboard_test).
 *
 * FAITHFUL DEGRADATIONS on the band tier (documented, not silent):
 *   - `armed`: coordinate.py gates keys behind an arm signal delivered on
 *     namespace_arm.  On-device bus BINDINGS are deferred on this tier
 *     ([CXC-RM-M5]), so with no arm namespace the keypad defaults ARMED
 *     (usable); a bound arm namespace would gate it once bindings land.
 *   - status BLINK (empty_flashing / confirm_flashing) renders as a SOLID
 *     colour — the CYD has no per-widget blink clock yet (same call as the
 *     other CYD animation deferrals); the status MODEL + transitions are
 *     the faithful port.
 *
 * The on-screen keypad has NO Skia pixel oracle (there is no on-screen
 * keyboard on the desktop) — only the digit display + LED band mirrors
 * coordinate.py, and only the substrate's hit/edit model is parity-gated.
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_COORDINATE_H
#define WIDGETS_COORDINATE_H

#include "widget.h"
#include "keyboard.h"
#include <cstdint>

class CoordinateWidget : public Widget {
public:
    typedef void (*PublishFn)(const char* ns, const char* value);

    /* Status states — a direct port of coordinate.py's status machine. */
    enum class Status : uint8_t {
        Dark,            /* not armed (#555)                 */
        EmptyFlashing,   /* armed, awaiting input (blue)     */
        Partial,         /* digits entered, incomplete (blue)*/
        ConfirmFlashing, /* all digits in, awaiting Enter    */
        Committed,       /* committed to the bus (green)     */
    };

    CoordinateWidget(const char* id,
                     const char* label = "Coordinates",
                     int max_digits = 8,
                     const char* ns_coordinates = nullptr,
                     const char* ns_status = nullptr);

    /* Scene-level publish hook (set once by the scene builder). */
    static void set_publish_fn(PublishFn fn) { s_publish = fn; }

    void set_armed(bool a);

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    void set_status(Status s);
    void commit();
    /* keyboard-local origin inside the widget rect (draw + on_tap share it) */
    float keypad_origin_y() const;
    void  format_digits(char* out, int cap) const;

    const char* _label;
    int         _max_digits;
    const char* _ns_coordinates;
    const char* _ns_status;

    EditBuffer  _buf;
    Status      _status = Status::EmptyFlashing;
    bool        _armed = true;   /* CYD default: armed (no bus arm binding) */

    static PublishFn s_publish;
};

#endif /* WIDGETS_COORDINATE_H */
