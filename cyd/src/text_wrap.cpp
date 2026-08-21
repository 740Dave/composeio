/*
 * text_wrap.cpp — shared soft-wrap + per-glyph measurement helper
 * ([CXC-R-ENGINE-TEXT]).  See text_wrap.h for the contract and the
 * Skia-parity domain.
 *
 * Byte-for-byte port of MultiLineEditMixin._wrap_lines
 * (composeio/render/widgets/multiline_text.py) and the _prefix_widths
 * advance accumulation (composeio/render/widgets/text_input.py).  Verified
 * against the Python ORACLE by cyd/src/text_wrap_selftest.cpp over
 * cyd/test/wrap_corpus_generated.h.
 */

#include "text_wrap.h"
#include "canvas.h"

int measure_prefix_advances(Canvas& canvas, const char* shown, int n,
                            float* xs_out, int cap) {
    if (cap < n + 1) return -1;
    xs_out[0] = 0.0f;
    float acc = 0.0f;                    /* the Python _prefix_widths `acc` */
    char buf[2];
    buf[1] = '\0';
    for (int i = 0; i < n; i++) {
        buf[0] = shown[i];               /* per-GLYPH advance, one measure */
        acc += canvas.measure_text(buf).width;
        xs_out[i + 1] = acc;
    }
    return n + 1;
}

int wrap_lines(const float* xs, int n, const char* shown, float avail,
               LineSpan* out, int max_lines) {
    /* Python: `if n == 0 or avail <= 0: return [(0, n)]` */
    if (n == 0 || avail <= 0.0f) {
        if (max_lines >= 1) {
            out[0].start = 0;
            out[0].end = n;
        }
        return 1;
    }

    int count = 0;
    int start = 0;
    while (start < n) {
        int end = start + 1;             /* a line always takes >= 1 char */
        /* Python: while end < n and xs[end+1] - xs[start] <= avail: end += 1 */
        while (end < n && (xs[end + 1] - xs[start]) <= avail) {
            end++;
        }
        if (end < n) {                   /* breaking mid-text: prefer last space */
            for (int i = end; i > start; i--) {
                if (shown[i - 1] == ' ') {
                    end = i;
                    break;
                }
            }
        }
        if (count < max_lines) {
            out[count].start = start;
            out[count].end = end;
        }
        count++;
        start = end;
    }
    return count;
}

int wrap_text_lines(Canvas& canvas, const char* shown, int n, float avail,
                    float* xs, int xs_cap, LineSpan* out, int max_lines) {
    if (measure_prefix_advances(canvas, shown, n, xs, xs_cap) < 0) {
        return -1;
    }
    return wrap_lines(xs, n, shown, avail, out, max_lines);
}

/* -- DISPLAY wrap (trailing-space-EXCLUDED; chat_thread.py _wrap_runs) ------
 *
 * Port of ChatThreadWidget._wrap_runs / module wrap_text: greedy word pack
 * where a word stays on the line whenever the WORD (its non-space extent)
 * fits, independent of whether the following space fits.  Trailing spaces are
 * left IN the span (contiguous coverage) and rstripped by the caller.  An
 * over-wide first word hard-breaks per glyph (_hard_break_piece), min 1 char.
 * See text_wrap.h for why this differs from the edit-field wrap_lines. */
int wrap_lines_display(const float* xs, int n, const char* shown, float avail,
                       LineSpan* out, int max_lines) {
    if (n == 0 || avail <= 0.0f) {
        if (max_lines >= 1) { out[0].start = 0; out[0].end = n; }
        return 1;
    }

    int count = 0;
    int start = 0;
    while (start < n) {
        /* Skip leading spaces for the fit decision; they stay in the span
         * (rare — a normal break leaves the next line starting on a word). */
        int i = start;
        while (i < n && shown[i] == ' ') i++;
        int end;
        if (i >= n) {
            end = n;                          /* trailing spaces only */
        } else {
            end = -1;
            bool first = true;
            while (i < n) {
                /* i at a word start (non-space).  Word is [i, we). */
                int we = i;
                while (we < n && shown[we] != ' ') we++;
                if ((xs[we] - xs[start]) <= avail) {
                    /* whole word fits — take it, then consume trailing spaces */
                    first = false;
                    i = we;
                    while (i < n && shown[i] == ' ') i++;
                    if (i >= n) { end = n; break; } /* rest is spaces -> keep */
                    /* else loop to the next word; `end` resolves on non-fit */
                } else if (first) {
                    /* over-wide FIRST word: hard-break per glyph (>= 1 char) */
                    int hb = i + 1;
                    while (hb < we && (xs[hb + 1] - xs[start]) <= avail) hb++;
                    end = hb;
                    break;
                } else {
                    /* a later word doesn't fit: break before it.  The spaces
                     * that preceded it were already consumed, so [start, i)
                     * carries them as trailing (rstripped by the caller). */
                    end = i;
                    break;
                }
            }
            if (end < 0) end = n;             /* fell off the end fitting */
        }
        if (count < max_lines) {
            out[count].start = start;
            out[count].end = end;
        }
        count++;
        start = end;
    }
    return count;
}

int wrap_text_lines_display(Canvas& canvas, const char* shown, int n,
                            float avail, float* xs, int xs_cap,
                            LineSpan* out, int max_lines) {
    if (measure_prefix_advances(canvas, shown, n, xs, xs_cap) < 0) {
        return -1;
    }
    return wrap_lines_display(xs, n, shown, avail, out, max_lines);
}
