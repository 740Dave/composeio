/*
 * text_input.h — labeled single-line text entry with an on-screen QWERTY
 * keyboard (M5 port of text_input.py, backlog #13).
 *
 * The desktop TextInputWidget captures text through a HARDWARE keyboard
 * (text_input.py on_text / on_key) and commits on Enter or focus-out.  An
 * ESP32 panel has only touch, so this port ADDS the on-screen QWERTY (the
 * keyboard.cpp substrate, qwerty_keyboard()) below the same labeled
 * single-line field the Skia widget draws in its DEFAULT (form) chrome.
 *
 * The text EDIT MODEL is the substrate's — printable-insert-at-caret and
 * Backspace-before-caret, exactly text_input.py on_text / Backspace — so
 * the entry logic never forks per tier (host-verified by
 * env:native_keyboard_test).  Uses ONLY the abstract Canvas contract.
 *
 * FAITHFUL DEGRADATIONS on the touch tier (documented, not silent —
 * [CXC-RM-M5], same discipline as coordinate.h):
 *   - COMMIT: text_input.py commits on Enter or focus-out.  The on-screen
 *     qwerty_keyboard() is a DIRECT mirror of keyboard.compose.yaml and has
 *     NO Enter key, and a touch panel has no focus-out.  So commit is an
 *     explicit widget-drawn SEND bar tap — the console-composer SEND /
 *     commit_namespace symmetry text_input.py already supports
 *     ([SPEC:CXC-CONSOLE-VISUAL] commit_namespace).  clear_on_commit gives
 *     the chat-composer semantics (the field clears on SEND).
 *   - CARET nav: the on-screen qwerty exposes no Left/Right/arrow keys, so
 *     the caret stays at end (append + Backspace), like coordinate.  The
 *     edit model still supports mid-caret move (edit_move, host-tested); a
 *     later layout revision could surface arrows.
 *   - SHIFT: keeps the substrate's v1 semantics (caller-signal only, no
 *     per-key case change — keycaps show uppercase, keys emit lowercase);
 *     the Shift key is inert on this tier.
 *   - SECRET / selection / clipboard / multi-line soft-wrap (the console
 *     chrome + MultiLineEditMixin) are desktop-only and out of scope here.
 *
 * The on-screen keyboard has NO Skia pixel oracle (there is no on-screen
 * keyboard on the desktop); only the substrate's hit/edit model is
 * parity-gated (env:native_keyboard_test), plus the single-line field
 * mirrors text_input.py's default chrome.
 */

#ifndef WIDGETS_TEXT_INPUT_H
#define WIDGETS_TEXT_INPUT_H

#include "widget.h"
#include "keyboard.h"
#include <cstdint>

class TextInputWidget : public Widget {
public:
    typedef void (*PublishFn)(const char* ns, const char* value);

    TextInputWidget(const char* id,
                    const char* label = "",
                    const char* placeholder = "",
                    const char* ns_value = nullptr,
                    bool clear_on_commit = false,
                    int max_len = kEditCap - 1);

    /* Scene-level publish hook (set once by the scene builder). */
    static void set_publish_fn(PublishFn fn) { s_publish = fn; }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    void commit();
    /* keyboard-local origin inside the widget rect (draw + on_tap share it) */
    float keyboard_origin_y() const;
    /* SEND bar rect, widget-relative (draw + on_tap share it) */
    void  send_rect(float rx, float ry, float& sx, float& sy,
                    float& sw, float& sh) const;

    const char* _label;
    const char* _placeholder;
    const char* _ns_value;
    bool        _clear_on_commit;
    int         _max_len;

    EditBuffer  _buf;

    static PublishFn s_publish;
};

#endif /* WIDGETS_TEXT_INPUT_H */
