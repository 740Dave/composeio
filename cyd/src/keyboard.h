/*
 * keyboard.h — reusable on-screen touch keyboard / keypad substrate for
 * the CYD/ESP32 embedded tier.
 *
 * The FIX-ONCE enabler behind text_input (backlog #13) and coordinate
 * (#10): the desktop/Skia tier captures text through a HARDWARE keyboard
 * (text_input.py on_text/on_key, coordinate.py on_key) — an ESP32 panel
 * has only touch.  So the on-screen keyboard IS the CYD text-entry
 * mechanism, and it has NO Skia pixel oracle (there is no on-screen
 * keyboard WIDGET on the desktop — keyboard.compose.yaml is a
 * last-key-wins demo COMPONENT, not an edit model).
 *
 * What IS shared, and what this substrate pins bit-for-bit, is the EDIT
 * MODEL — the buffer mutation each key performs — which mirrors the
 * canonical desktop edit logic exactly (reuse by reference, never fork —
 * R3):
 *
 *   text_input.py on_text : insert printable at caret; cursor += len
 *   text_input.py Backspace: if cursor>0 delete the char BEFORE caret;
 *                            cursor -= 1     (edit_backspace)
 *   text_input.py Left/Right: clamp-move the caret        (edit_move)
 *   coordinate.py append  : digit only, len < max_digits  (EditPolicy)
 *   coordinate.py Backspace: pop the last char (caret pinned at end)
 *
 * Two halves, cleanly split so a widget composes exactly what it needs:
 *
 *   1. LAYOUT + HIT-TEST — a grid of key cells (rects relative to the
 *      keyboard origin), each carrying a KeyKind + emitted char + keycap
 *      label.  keyboard_key_at() maps a tap-on-release local (x,y) (the
 *      touch_dispatch.cpp contract) to a key index.  Band-renderer
 *      friendly: the widget draws only the visible key cells, O(keys),
 *      never an offscreen surface.  Two built-in layouts ship:
 *      numeric_keypad() (coordinate) and qwerty_keyboard() (text_input),
 *      the qwerty geometry a direct mirror of keyboard.compose.yaml
 *      (34x36 cells, 38px pitch — keycaps DISPLAY uppercase, EMIT
 *      lowercase, the physical-keyboard convention).
 *
 *   2. EDIT MODEL — EditBuffer (a bounded char buffer + caret) with the
 *      insert/backspace/move primitives above, and keyboard_apply()
 *      which resolves ONE key against a buffer under an EditPolicy
 *      (digits_only + max_len — the coordinate constraint).  This is the
 *      part the host parity harness (env:native_keyboard_test) asserts
 *      against the Python edit logic case-for-case.
 *
 * Pure C++ — depends only on fixed-size structs; no Canvas, no ESP-IDF —
 * so it is host-compilable and parity-tested off-hardware
 * (env:native_keyboard_test), exactly like scroll_list / text_wrap /
 * overlay.
 */

#ifndef KEYBOARD_H
#define KEYBOARD_H

#include <cstdint>

/* What a key does when tapped. */
enum class KeyKind : uint8_t {
    None,       /* inert / no-op (a blocked or ignored tap)          */
    Char,       /* insert Key.ch at the caret                        */
    Space,      /* insert a space (a Char whose ch is ' ')           */
    Backspace,  /* delete the char before the caret                  */
    Clear,      /* clear the whole buffer (coordinate Escape)        */
    Enter,      /* commit — the widget publishes; buffer untouched   */
    Shift,      /* case-shift toggle (v1: caller signal only — no    */
                /* per-key case change, matching keyboard.compose)   */
};

/* One key cell.  Rect is relative to the keyboard's origin (0,0); the
 * widget adds its keyboard's screen offset before drawing / hit-testing. */
struct Key {
    float   x, y, w, h;   /* cell rect, keyboard-local                */
    KeyKind kind;
    char    ch;           /* emitted char when kind==Char/Space       */
    const char* cap;      /* keycap label to draw (static string)     */
};

/* An immutable key grid.  `keys` points at a static array of `count`
 * cells; width/height are the overall keyboard extent (for layout). */
struct KeyLayout {
    const Key* keys;
    int   count;
    float width;
    float height;
};

/* -- built-in layouts (static storage; safe to hold the reference) ---- */

/* 3-wide numeric keypad + Clear/0/Backspace row + a full-width Enter bar
 * (coordinate.py).  216 x 224 px — fits the classic CYD's 240px width. */
const KeyLayout& numeric_keypad();

/* QWERTY alpha keyboard mirroring keyboard.compose.yaml geometry (376 x
 * 156).  Letters emit lowercase, keycaps show uppercase; a Shift key, a
 * Backspace key and a full-width Space bar.  For text_input (#13). */
const KeyLayout& qwerty_keyboard();

/* -- hit test --------------------------------------------------------- *
 * Map a keyboard-LOCAL tap (x,y) to a key index, or -1 outside every
 * cell.  Half-open containment IDENTICAL to touch_dispatch.cpp hit_test
 * (x in [kx, kx+kw), y in [ky, ky+kh)) so an in-widget keyboard hit can
 * never disagree with the flat registry's z-capture.  Forward scan (the
 * built-in layouts do not overlap); first match wins. */
int keyboard_key_at(const KeyLayout& layout, float x, float y);

/* ==================================================================== *
 * Edit model — mirrors text_input.py / coordinate.py buffer mutation.
 * ==================================================================== */

/* Bounded edit buffer: NUL-terminated text + caret.  64 chars is ample
 * for a coordinate string or a single-line field on this tier (no heap
 * churn — the embedded idiom).  `cursor` is a caret POSITION in
 * [0, len]. */
static constexpr int kEditCap = 64;

struct EditBuffer {
    char text[kEditCap];
    int  len;      /* chars in text (excluding NUL)   */
    int  cursor;   /* caret position, 0..len          */
};

/* Insert/append policy for keyboard_apply (the coordinate constraint). */
struct EditPolicy {
    bool digits_only = false;           /* reject non-ASCII-digit Char/Space */
    int  max_len     = kEditCap - 1;    /* refuse insert at/above this length */
};

void edit_reset(EditBuffer& b);         /* empty buffer, caret at 0 */
void edit_clear(EditBuffer& b);         /* alias of reset (coordinate Escape) */

/* Set the buffer from a C string (caret to end) — restore/echo path.
 * Truncated to kEditCap-1. */
void edit_set(EditBuffer& b, const char* s);

/* Insert one char at the caret: text = text[:cursor]+c+text[cursor:];
 * cursor += 1.  Refuses non-printable c and a full buffer.  Returns true
 * when it changed the buffer (text_input.py on_text, one char). */
bool edit_insert_char(EditBuffer& b, char c);

/* Insert a C string at the caret (multi-char paste / restore-at-caret),
 * each printable char in order.  Returns true if anything was inserted. */
bool edit_insert_str(EditBuffer& b, const char* s);

/* Delete the char BEFORE the caret (text_input.py Backspace): if
 * cursor>0, text = text[:cursor-1]+text[cursor:]; cursor -= 1.  Returns
 * true when a char was removed. */
bool edit_backspace(EditBuffer& b);

/* Delete the char AT the caret (text_input.py Delete): if cursor<len,
 * text = text[:cursor]+text[cursor+1:].  Returns true when removed. */
bool edit_delete(EditBuffer& b);

/* Move the caret by delta, clamped to [0, len] (text_input.py Left/Right
 * with no selection).  Returns true when the caret actually moved. */
bool edit_move(EditBuffer& b, int delta);

/* Resolve ONE key against the buffer under `pol` and RETURN the kind the
 * caller must react to:
 *   Char/Space -> inserted at caret (subject to digits_only + max_len);
 *                 a blocked char returns None (nothing happened — matches
 *                 coordinate.py ignoring an over-length / non-digit key);
 *   Backspace  -> edit_backspace (returns Backspace even on empty buffer,
 *                 so the caller may still repaint; buffer unchanged);
 *   Clear      -> edit_clear;
 *   Enter      -> buffer UNTOUCHED (the widget commits/publishes);
 *   Shift      -> buffer untouched (caller toggles its own shift state);
 *   None       -> nothing.
 * The one call site every keyboard-bearing widget routes on_tap through. */
KeyKind keyboard_apply(EditBuffer& b, const Key& key, const EditPolicy& pol);

/* ASCII helpers (locale-free, matching Python str.isdigit / isprintable
 * for the single-byte glyphs this tier covers). */
bool key_is_digit(char c);
bool key_is_printable(char c);

#endif /* KEYBOARD_H */
