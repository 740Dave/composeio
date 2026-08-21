/*
 * keyboard_selftest.cpp — host parity harness for the on-screen keyboard
 * substrate.
 *
 * Asserts the C++ layout HIT-TEST resolves taps to the right key, and the
 * EDIT MODEL reproduces the Python edit logic (text_input.py on_text /
 * Backspace / caret-move, coordinate.py digit-append / max_digits / pop)
 * exactly.  Every EXP_* below was computed by running the Python formulas
 * by hand against those two widgets.
 *
 * Runs off hardware:  pio run -e native_keyboard_test && \
 *                     .pio/build/native_keyboard_test/program
 * Must print "ALL PASS" and exit 0.
 */

#include "keyboard.h"

#include <cstdio>
#include <cstring>

static int g_fail = 0;

static void check_i(const char* what, int got, int exp) {
    if (got != exp) {
        std::printf("  FAIL %-28s got=%d exp=%d\n", what, got, exp);
        g_fail++;
    }
}

static void check_s(const char* what, const char* got, const char* exp) {
    if (std::strcmp(got, exp) != 0) {
        std::printf("  FAIL %-28s got=\"%s\" exp=\"%s\"\n", what, got, exp);
        g_fail++;
    }
}

/* Resolve a tap and (when it lands on a Char key) report the emitted char. */
static char emit_at(const KeyLayout& L, float x, float y) {
    int i = keyboard_key_at(L, x, y);
    if (i < 0) return 0;
    return L.keys[i].ch;
}

int main() {
    std::printf("keyboard substrate parity harness "
                "(vs text_input.py / coordinate.py)\n");

    /* ============================================================ *
     * 1. numeric_keypad hit-test (cell 68x40, pitch 74/46)
     * ============================================================ */
    {
        const KeyLayout& L = numeric_keypad();
        check_i("num_count", L.count, 13);
        check_i("num_idx_1", keyboard_key_at(L,  34, 20),  0);   /* '1' */
        check_i("num_idx_2", keyboard_key_at(L, 108, 20),  1);   /* '2' */
        check_i("num_idx_3", keyboard_key_at(L, 182, 20),  2);   /* '3' */
        check_i("num_idx_5", keyboard_key_at(L, 108, 66),  4);   /* '5' */
        check_i("num_idx_C", keyboard_key_at(L,  34,161),  9);   /* Clear */
        check_i("num_idx_0", keyboard_key_at(L, 108,161), 10);   /* '0' */
        check_i("num_idx_bk",keyboard_key_at(L, 182,161), 11);   /* Backspace */
        check_i("num_idx_en",keyboard_key_at(L, 100,200), 12);   /* Enter bar */
        /* emitted chars on the digit keys */
        check_i("num_ch_1", emit_at(L,  34, 20), '1');
        check_i("num_ch_9", emit_at(L, 182,112), '9');
        check_i("num_ch_0", emit_at(L, 108,161), '0');
        /* gap between cells (x 68..74) and outside -> miss */
        check_i("num_gap",   keyboard_key_at(L,  70, 20), -1);
        check_i("num_out_x", keyboard_key_at(L, 300, 20), -1);
        check_i("num_out_y", keyboard_key_at(L, 100, 300), -1);
        /* kinds on the control keys */
        check_i("num_kind_C",  (int)L.keys[ 9].kind, (int)KeyKind::Clear);
        check_i("num_kind_bk", (int)L.keys[11].kind, (int)KeyKind::Backspace);
        check_i("num_kind_en", (int)L.keys[12].kind, (int)KeyKind::Enter);
    }

    /* ============================================================ *
     * 2. qwerty_keyboard hit-test (34x36, pitch 38; keyboard.compose.yaml)
     * ============================================================ */
    {
        const KeyLayout& L = qwerty_keyboard();
        check_i("qw_count", L.count, 29);
        check_i("qw_idx_q", keyboard_key_at(L,  17, 18),  0);
        check_i("qw_idx_p", keyboard_key_at(L, 359, 18),  9);
        check_i("qw_idx_a", keyboard_key_at(L,  17, 58), 10);
        check_i("qw_idx_l", keyboard_key_at(L, 321, 58), 18);
        check_i("qw_idx_sh",keyboard_key_at(L,  17, 98), 19);   /* Shift */
        check_i("qw_idx_m", keyboard_key_at(L, 283, 98), 26);
        check_i("qw_idx_bk",keyboard_key_at(L, 321, 98), 27);   /* Backspace */
        check_i("qw_idx_sp",keyboard_key_at(L, 188,138), 28);   /* Space bar */
        /* letters EMIT lowercase, keycaps DISPLAY uppercase */
        check_i("qw_ch_q",  emit_at(L,  17, 18), 'q');
        check_i("qw_ch_m",  emit_at(L, 283, 98), 'm');
        check_s("qw_cap_q", L.keys[0].cap, "Q");
        check_s("qw_cap_m", L.keys[26].cap, "M");
        check_i("qw_kind_sh",(int)L.keys[19].kind, (int)KeyKind::Shift);
        check_i("qw_kind_sp",(int)L.keys[28].kind, (int)KeyKind::Space);
        check_i("qw_ch_sp",  L.keys[28].ch, ' ');
        /* gap column (x 34..38) misses */
        check_i("qw_gap",   keyboard_key_at(L, 36, 18), -1);
    }

    /* ============================================================ *
     * 3. text_input.py edit model — insert at caret + Backspace + move
     * ============================================================ */
    {
        EditBuffer b; edit_reset(b);
        edit_insert_char(b, 'h');
        edit_insert_char(b, 'i');
        check_s("ti_hi",    b.text, "hi"); check_i("ti_hi_cur", b.cursor, 2);
        edit_move(b, -1);
        check_i("ti_left",  b.cursor, 1);
        edit_insert_char(b, 'X');            /* text[:1]+"X"+text[1:] */
        check_s("ti_ins",   b.text, "hXi");  check_i("ti_ins_cur", b.cursor, 2);
        edit_backspace(b);                   /* delete before cursor -> 'X' */
        check_s("ti_bk",    b.text, "hi");   check_i("ti_bk_cur", b.cursor, 1);
        edit_move(b, +5);                    /* clamp to len */
        check_i("ti_clampR",b.cursor, 2);
        edit_backspace(b);
        check_s("ti_bk2",   b.text, "h");    check_i("ti_bk2_cur", b.cursor, 1);
        edit_backspace(b);
        check_s("ti_bk3",   b.text, "");     check_i("ti_bk3_cur", b.cursor, 0);
        check_i("ti_bk_empty", edit_backspace(b) ? 1 : 0, 0);  /* no-op */
        /* Delete-at-caret + move-left return values */
        edit_set(b, "abc");
        edit_move(b, -3);                    /* caret to 0 */
        check_i("ti_del",   edit_delete(b) ? 1 : 0, 1);
        check_s("ti_del_t", b.text, "bc");   check_i("ti_del_cur", b.cursor, 0);
        check_i("ti_moveL0",edit_move(b, -1) ? 1 : 0, 0);       /* already 0 */
        /* non-printable is refused (text_input on_text isprintable filter) */
        check_i("ti_ctrl",  edit_insert_char(b, '\n') ? 1 : 0, 0);
        check_s("ti_ctrl_t",b.text, "bc");
    }

    /* ============================================================ *
     * 4. coordinate.py policy — digit-only + max_digits + pop-from-end
     * ============================================================ */
    {
        const KeyLayout& L = numeric_keypad();
        EditPolicy pol; pol.digits_only = true; pol.max_len = 4;  /* max_digits */
        EditBuffer b; edit_reset(b);

        auto tap = [&](float x, float y) {
            int i = keyboard_key_at(L, x, y);
            return (i < 0) ? KeyKind::None : keyboard_apply(b, L.keys[i], pol);
        };

        check_i("co_1", (int)tap( 34, 20), (int)KeyKind::Char);  /* '1' */
        check_i("co_2", (int)tap(108, 20), (int)KeyKind::Char);  /* '2' */
        check_s("co_12", b.text, "12");
        check_i("co_3", (int)tap(182, 20), (int)KeyKind::Char);  /* '3' */
        check_i("co_4", (int)tap( 34, 66), (int)KeyKind::Char);  /* '4' */
        check_s("co_1234", b.text, "1234");
        /* 5th digit refused: len >= max_digits -> None, buffer unchanged */
        check_i("co_5_block", (int)tap(108, 66), (int)KeyKind::None);
        check_s("co_still", b.text, "1234");
        /* Backspace pops the last (caret pinned at end) */
        check_i("co_bk", (int)tap(182,161), (int)KeyKind::Backspace);
        check_s("co_123", b.text, "123");
        /* Enter returns Enter and does NOT mutate the buffer (widget commits) */
        check_i("co_en", (int)tap(100,200), (int)KeyKind::Enter);
        check_s("co_en_t", b.text, "123");
        /* Clear (C) empties */
        check_i("co_clr", (int)tap(34,161), (int)KeyKind::Clear);
        check_s("co_clr_t", b.text, "");
        /* a non-digit char refused directly by policy (qwerty 'a' -> None) */
        Key alpha = { 0,0,10,10, KeyKind::Char, 'a', "A" };
        check_i("co_alpha_block", (int)keyboard_apply(b, alpha, pol),
                (int)KeyKind::None);
        check_s("co_alpha_t", b.text, "");
    }

    /* ============================================================ *
     * 5. coordinate _format_digits parity (display: ljust + join " ")
     * ============================================================ */
    {
        /* " ".join("12".ljust(4,"_")) == "1 2 _ _"  */
        EditBuffer b; edit_reset(b);
        edit_set(b, "12");
        char out[64];
        /* replicate the widget's format inline for the parity assertion */
        int n = 4, o = 0;
        for (int i = 0; i < n; i++) {
            if (i) out[o++] = ' ';
            out[o++] = (i < b.len) ? b.text[i] : '_';
        }
        out[o] = '\0';
        check_s("fmt_12", out, "1 2 _ _");
    }

    /* ============================================================ *
     * 6. text_input WIDGET path — qwerty + NON-digit policy (the exact
     *    on_tap route text_input.cpp drives: letters/space insert,
     *    field max_len cap, Backspace pops, Shift inert on this tier).
     * ============================================================ */
    {
        const KeyLayout& L = qwerty_keyboard();
        EditPolicy pol; pol.digits_only = false; pol.max_len = 5;  /* field cap */
        EditBuffer b; edit_reset(b);
        auto tap = [&](float x, float y) {
            int i = keyboard_key_at(L, x, y);
            return (i < 0) ? KeyKind::None : keyboard_apply(b, L.keys[i], pol);
        };
        check_i("tw_w",  (int)tap( 55, 18), (int)KeyKind::Char);   /* 'w' */
        check_i("tw_e",  (int)tap( 93, 18), (int)KeyKind::Char);   /* 'e' */
        check_s("tw_we", b.text, "we");
        check_i("tw_sp", (int)tap(188,138), (int)KeyKind::Space);  /* space bar */
        check_s("tw_we_sp", b.text, "we ");
        check_i("tw_a",  (int)tap( 17, 58), (int)KeyKind::Char);   /* 'a' */
        check_i("tw_s",  (int)tap( 55, 58), (int)KeyKind::Char);   /* 's' */
        check_s("tw_weas", b.text, "we as"); check_i("tw_len", b.len, 5);
        /* 6th char refused: len >= max_len -> None (the widget field cap) */
        check_i("tw_block", (int)tap( 93, 18), (int)KeyKind::None); /* 'e' */
        check_s("tw_still", b.text, "we as");
        /* qwerty Backspace key pops before the caret */
        check_i("tw_bk", (int)tap(321, 98), (int)KeyKind::Backspace);
        check_s("tw_bk_t", b.text, "we a");
        /* Shift is inert on this tier (caller-signal only): buffer untouched */
        check_i("tw_sh", (int)tap( 17, 98), (int)KeyKind::Shift);
        check_s("tw_sh_t", b.text, "we a");
    }

    if (g_fail == 0) {
        std::printf("ALL PASS\n");
        return 0;
    }
    std::printf("%d CHECK(S) FAILED\n", g_fail);
    return 1;
}
