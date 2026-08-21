/*
 * keyboard.cpp — on-screen touch keyboard / keypad substrate.
 *
 * Layout tables + hit-test + the edit model that mirrors text_input.py /
 * coordinate.py.  Pure C++ (no Canvas, no ESP-IDF) — host-parity-tested
 * by keyboard_selftest.cpp (env:native_keyboard_test).
 */

#include "keyboard.h"

#include <cstring>

/* ------------------------------------------------------------------ */
/* ASCII class helpers (locale-free)                                   */
/* ------------------------------------------------------------------ */

bool key_is_digit(char c) { return c >= '0' && c <= '9'; }

/* Python str.isprintable for the single-byte glyphs this tier covers:
 * printable ASCII 0x20..0x7e.  (isprintable(" ") is True in Python.) */
bool key_is_printable(char c) {
    unsigned char u = (unsigned char)c;
    return u >= 0x20 && u < 0x7f;
}

/* ------------------------------------------------------------------ */
/* Built-in layouts                                                    */
/* ------------------------------------------------------------------ */

/* -- numeric keypad (coordinate.py) --------------------------------- *
 * 3 columns x 68px cell, 6px gap (pitch 74); 4 digit rows @40px + 6px
 * gap (pitch 46), then a full-width ENTER bar.  216 x 224 overall. */
static constexpr float kNumW  = 68.0f;
static constexpr float kNumH  = 40.0f;
static constexpr float kNumPX = 74.0f;   /* 68 + 6 gap */
static constexpr float kNumPY = 46.0f;   /* 40 + 6 gap */
static constexpr float kNumWidth = 3 * kNumW + 2 * 6.0f;   /* 216 */

static const Key kNumericKeys[] = {
    /* row 0 */
    {0*kNumPX, 0*kNumPY, kNumW, kNumH, KeyKind::Char, '1', "1"},
    {1*kNumPX, 0*kNumPY, kNumW, kNumH, KeyKind::Char, '2', "2"},
    {2*kNumPX, 0*kNumPY, kNumW, kNumH, KeyKind::Char, '3', "3"},
    /* row 1 */
    {0*kNumPX, 1*kNumPY, kNumW, kNumH, KeyKind::Char, '4', "4"},
    {1*kNumPX, 1*kNumPY, kNumW, kNumH, KeyKind::Char, '5', "5"},
    {2*kNumPX, 1*kNumPY, kNumW, kNumH, KeyKind::Char, '6', "6"},
    /* row 2 */
    {0*kNumPX, 2*kNumPY, kNumW, kNumH, KeyKind::Char, '7', "7"},
    {1*kNumPX, 2*kNumPY, kNumW, kNumH, KeyKind::Char, '8', "8"},
    {2*kNumPX, 2*kNumPY, kNumW, kNumH, KeyKind::Char, '9', "9"},
    /* row 3: Clear / 0 / Backspace */
    {0*kNumPX, 3*kNumPY, kNumW, kNumH, KeyKind::Clear,     0,   "C"},
    {1*kNumPX, 3*kNumPY, kNumW, kNumH, KeyKind::Char,     '0',  "0"},
    {2*kNumPX, 3*kNumPY, kNumW, kNumH, KeyKind::Backspace, 0,   "<-"},
    /* row 4: full-width Enter bar */
    {0.0f, 4*kNumPY, kNumWidth, kNumH, KeyKind::Enter, 0, "ENTER"},
};

const KeyLayout& numeric_keypad() {
    static const KeyLayout L = {
        kNumericKeys,
        (int)(sizeof(kNumericKeys) / sizeof(kNumericKeys[0])),
        kNumWidth,
        4 * kNumPY + kNumH,   /* 4*46 + 40 = 224 */
    };
    return L;
}

/* -- qwerty alpha keyboard (text_input.py, #13) --------------------- *
 * DIRECT mirror of keyboard.compose.yaml: 34x36 cells, 38px pitch,
 * rows q-p / a-l / (Shift z-m Backspace) / Space.  376 x 156.  Letters
 * emit lowercase (Key.ch), keycaps show uppercase (Key.cap). */
static constexpr float kKw  = 34.0f;
static constexpr float kKh  = 36.0f;
static constexpr float kKp  = 38.0f;   /* 34 + 4 gap */
static constexpr float kR0  = 0.0f;
static constexpr float kR1  = 40.0f;
static constexpr float kR2  = 80.0f;
static constexpr float kR3  = 120.0f;
static constexpr float kQwWidth = 376.0f;   /* 10*38 - 4 gap = 376 */

static const Key kQwertyKeys[] = {
    /* row 0: q w e r t y u i o p */
    {0*kKp,  kR0, kKw, kKh, KeyKind::Char, 'q', "Q"},
    {1*kKp,  kR0, kKw, kKh, KeyKind::Char, 'w', "W"},
    {2*kKp,  kR0, kKw, kKh, KeyKind::Char, 'e', "E"},
    {3*kKp,  kR0, kKw, kKh, KeyKind::Char, 'r', "R"},
    {4*kKp,  kR0, kKw, kKh, KeyKind::Char, 't', "T"},
    {5*kKp,  kR0, kKw, kKh, KeyKind::Char, 'y', "Y"},
    {6*kKp,  kR0, kKw, kKh, KeyKind::Char, 'u', "U"},
    {7*kKp,  kR0, kKw, kKh, KeyKind::Char, 'i', "I"},
    {8*kKp,  kR0, kKw, kKh, KeyKind::Char, 'o', "O"},
    {9*kKp,  kR0, kKw, kKh, KeyKind::Char, 'p', "P"},
    /* row 1: a s d f g h j k l */
    {0*kKp,  kR1, kKw, kKh, KeyKind::Char, 'a', "A"},
    {1*kKp,  kR1, kKw, kKh, KeyKind::Char, 's', "S"},
    {2*kKp,  kR1, kKw, kKh, KeyKind::Char, 'd', "D"},
    {3*kKp,  kR1, kKw, kKh, KeyKind::Char, 'f', "F"},
    {4*kKp,  kR1, kKw, kKh, KeyKind::Char, 'g', "G"},
    {5*kKp,  kR1, kKw, kKh, KeyKind::Char, 'h', "H"},
    {6*kKp,  kR1, kKw, kKh, KeyKind::Char, 'j', "J"},
    {7*kKp,  kR1, kKw, kKh, KeyKind::Char, 'k', "K"},
    {8*kKp,  kR1, kKw, kKh, KeyKind::Char, 'l', "L"},
    /* row 2: Shift, z x c v b n m, Backspace */
    {0*kKp,  kR2, kKw, kKh, KeyKind::Shift,     0,   "^"},
    {1*kKp,  kR2, kKw, kKh, KeyKind::Char,     'z',  "Z"},
    {2*kKp,  kR2, kKw, kKh, KeyKind::Char,     'x',  "X"},
    {3*kKp,  kR2, kKw, kKh, KeyKind::Char,     'c',  "C"},
    {4*kKp,  kR2, kKw, kKh, KeyKind::Char,     'v',  "V"},
    {5*kKp,  kR2, kKw, kKh, KeyKind::Char,     'b',  "B"},
    {6*kKp,  kR2, kKw, kKh, KeyKind::Char,     'n',  "N"},
    {7*kKp,  kR2, kKw, kKh, KeyKind::Char,     'm',  "M"},
    {8*kKp,  kR2, kKw, kKh, KeyKind::Backspace, 0,   "<-"},
    /* row 3: full-width Space bar */
    {0.0f,   kR3, kQwWidth, kKh, KeyKind::Space, ' ', "SPACE"},
};

const KeyLayout& qwerty_keyboard() {
    static const KeyLayout L = {
        kQwertyKeys,
        (int)(sizeof(kQwertyKeys) / sizeof(kQwertyKeys[0])),
        kQwWidth,
        kR3 + kKh,   /* 120 + 36 = 156 */
    };
    return L;
}

/* ------------------------------------------------------------------ */
/* Hit test                                                            */
/* ------------------------------------------------------------------ */

int keyboard_key_at(const KeyLayout& layout, float x, float y) {
    for (int i = 0; i < layout.count; i++) {
        const Key& k = layout.keys[i];
        if (x >= k.x && x < k.x + k.w &&
            y >= k.y && y < k.y + k.h) {
            return i;
        }
    }
    return -1;
}

/* ------------------------------------------------------------------ */
/* Edit model                                                          */
/* ------------------------------------------------------------------ */

void edit_reset(EditBuffer& b) {
    b.text[0] = '\0';
    b.len = 0;
    b.cursor = 0;
}

void edit_clear(EditBuffer& b) { edit_reset(b); }

void edit_set(EditBuffer& b, const char* s) {
    edit_reset(b);
    if (!s) return;
    for (int i = 0; s[i] && b.len < kEditCap - 1; i++) {
        b.text[b.len++] = s[i];
    }
    b.text[b.len] = '\0';
    b.cursor = b.len;
}

bool edit_insert_char(EditBuffer& b, char c) {
    if (!key_is_printable(c)) return false;       /* text_input on_text filter */
    if (b.len >= kEditCap - 1) return false;      /* buffer full               */
    if (b.cursor < 0) b.cursor = 0;
    if (b.cursor > b.len) b.cursor = b.len;
    /* text = text[:cursor] + c + text[cursor:] */
    for (int i = b.len; i > b.cursor; i--) {
        b.text[i] = b.text[i - 1];
    }
    b.text[b.cursor] = c;
    b.len++;
    b.cursor++;
    b.text[b.len] = '\0';
    return true;
}

bool edit_insert_str(EditBuffer& b, const char* s) {
    if (!s) return false;
    bool any = false;
    for (int i = 0; s[i]; i++) {
        if (edit_insert_char(b, s[i])) any = true;
    }
    return any;
}

bool edit_backspace(EditBuffer& b) {
    if (b.cursor <= 0 || b.len <= 0) return false;
    /* text = text[:cursor-1] + text[cursor:] */
    for (int i = b.cursor - 1; i < b.len - 1; i++) {
        b.text[i] = b.text[i + 1];
    }
    b.len--;
    b.cursor--;
    b.text[b.len] = '\0';
    return true;
}

bool edit_delete(EditBuffer& b) {
    if (b.cursor >= b.len) return false;
    /* text = text[:cursor] + text[cursor+1:] */
    for (int i = b.cursor; i < b.len - 1; i++) {
        b.text[i] = b.text[i + 1];
    }
    b.len--;
    b.text[b.len] = '\0';
    return true;
}

bool edit_move(EditBuffer& b, int delta) {
    int nc = b.cursor + delta;
    if (nc < 0) nc = 0;
    if (nc > b.len) nc = b.len;
    if (nc == b.cursor) return false;
    b.cursor = nc;
    return true;
}

KeyKind keyboard_apply(EditBuffer& b, const Key& key, const EditPolicy& pol) {
    switch (key.kind) {
        case KeyKind::Char:
        case KeyKind::Space: {
            char c = (key.kind == KeyKind::Space) ? ' ' : key.ch;
            /* coordinate.py policy: digit-only + len < max_digits.  A
             * blocked key is a no-op (no state change) -> report None. */
            if (pol.digits_only && !key_is_digit(c)) return KeyKind::None;
            if (b.len >= pol.max_len) return KeyKind::None;
            if (!edit_insert_char(b, c)) return KeyKind::None;
            return key.kind;
        }
        case KeyKind::Backspace:
            edit_backspace(b);           /* no-op on empty; caller may repaint */
            return KeyKind::Backspace;
        case KeyKind::Clear:
            edit_clear(b);
            return KeyKind::Clear;
        case KeyKind::Enter:
            return KeyKind::Enter;       /* buffer untouched — widget commits */
        case KeyKind::Shift:
            return KeyKind::Shift;       /* caller toggles its own shift state */
        default:
            return KeyKind::None;
    }
}
