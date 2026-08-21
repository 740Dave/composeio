/*
 * text_wrap.h — shared soft-wrap + per-glyph measurement helper for the
 * CYD/embedded tier ([CXC-R-ENGINE-TEXT]).
 *
 * A DIRECT port of the Python engine's canonical wrap substrate
 * MultiLineEditMixin._wrap_lines (composeio/render/widgets/
 * multiline_text.py) and the _prefix_widths advance table
 * (composeio/render/widgets/text_input.py).  Not baked into one widget:
 * label/callout/text_input/chat_thread/rich-readout ports all compose it,
 * so line breaks NEVER fork per tier (reuse by reference, not fork — R3).
 *
 * THE ONE MEASUREMENT PATH.  Both the wrap and every caller read the SAME
 * cumulative per-glyph advances that `measure_prefix_advances` builds from
 * the canvas's OWN `measure_text` — the identical additive sum the glyphs
 * are drawn with — so a line break can never disagree with the rendered
 * advance (the exact CYD-fork hazard this closes).
 *
 * PARITY DOMAIN.  On the CYD, measure_text scales atlas advances by
 * (font_size / atlas_size).  At a BAKED atlas size (scale == 1) the
 * per-glyph advance is the integer atlas advance, which — because the atlas
 * is generated from the same IBM Plex faces with the same settings as the
 * desktop rasterizer (cyd/fonts/generate_atlas.py vs
 * composeio/render/ft_text.py) — EQUALS the Skia tier's advance.  So the
 * line-break decisions are IDENTICAL to Skia at a baked size (verified
 * bit-for-bit by the wrap_corpus parity harness).  At a non-baked size the
 * scaled float advances are an approximation of Skia's, not a claim of
 * pixel-parity.
 *
 * Pure C++ — depends only on the abstract Canvas contract (canvas.h), so it
 * compiles for the CYD (ESP32Canvas), the Qualia (QualiaCanvas) and the
 * host parity harness (a MockCanvas) from ONE source.
 */

#ifndef TEXT_WRAP_H
#define TEXT_WRAP_H

class Canvas;

/* A wrapped line as [start, end) indices into the shown string — contiguous,
 * covering the whole string; the port of the Python (start, end) pair. */
struct LineSpan {
    int start;
    int end;
};

/* Build the cumulative per-glyph advance table `xs` (len == n+1, xs[0] == 0)
 * of the first `n` bytes of `shown`, exactly as the Python `_prefix_widths`
 * does: xs[i+1] = xs[i] + canvas.measure_text(<one char>).width.  The canvas
 * font must already be set.  Returns n+1 (entries written), or -1 if
 * `cap` < n+1.  ASCII single-byte glyphs (the CYD atlas coverage). */
int measure_prefix_advances(Canvas& canvas, const char* shown, int n,
                            float* xs_out, int cap);

/* Greedy word-wrap of the first `n` chars of `shown` to `avail` px over the
 * cumulative advances `xs` (len >= n+1), byte-for-byte port of
 * MultiLineEditMixin._wrap_lines:
 *   - n == 0 or avail <= 0        -> one line spanning [0, n);
 *   - a line always takes >= 1 char (a single over-wide glyph hard-breaks);
 *   - otherwise breaks after the LAST space that fits (trailing space kept
 *     on the line);
 *   - a word longer than the line hard-breaks per glyph.
 * Writes up to `max_lines` spans into `out`; RETURNS the true line count
 * (which may exceed max_lines — the caller sizes `out` or clamps). */
int wrap_lines(const float* xs, int n, const char* shown, float avail,
               LineSpan* out, int max_lines);

/* One-shot convenience for a display widget: measure `shown` on `canvas`
 * (font already set) into the caller's `xs` scratch (cap >= len+1) and wrap
 * to `avail`.  Returns the true line count (see wrap_lines); -1 if the xs
 * scratch is too small.  Composes the two calls above — the widget never
 * re-implements either.  (Explicit '\n' is NOT segmented here; a multi-
 * paragraph widget splits on '\n' and calls this per paragraph — an additive
 * convenience over the faithful single-buffer _wrap_lines port.) */
int wrap_text_lines(Canvas& canvas, const char* shown, int n, float avail,
                    float* xs, int xs_cap, LineSpan* out, int max_lines);

/* -- DISPLAY wrap variant (read-only prose bubbles: chat_thread) -------------
 *
 * The `wrap_lines` above ports the EDIT-FIELD wrapper
 * MultiLineEditMixin._wrap_lines, which keeps a trailing space ON the line and
 * therefore requires the space AFTER a word to also fit for that word to stay
 * on the line (a caret can sit on the space).  A READ-ONLY prose surface wraps
 * differently: chat_thread.py's own wrappers (_wrap_runs for bubbles,
 * wrap_text for the harness packet) DROP the trailing space, so a word stays
 * on the line whenever the WORD ITSELF fits — even if its following space
 * overflows.  The two rules disagree by exactly one word at an exact-fit
 * column boundary (a word whose end lands in (avail - space_w, avail]).
 *
 * `wrap_lines_display` is the byte-for-byte port of that trailing-space-
 * EXCLUDED rule (word-greedy pack; an over-wide word hard-breaks per glyph;
 * trailing spaces stay in the returned span and the caller rstrips them before
 * draw).  It shares the SAME cumulative-advance `xs` measurement path as
 * `wrap_lines` — one measurement path, so wrap can never disagree with the
 * rendered advance.  Proven against the ACTUAL chat wrapper
 * (ChatThreadWidget._layout_body) by cyd/src/chat_wrap_selftest.cpp over
 * cyd/test/chat_wrap_corpus_generated.h (env:native_chat_wrap).
 *
 * This is an ADDITIVE substrate entry point — `wrap_lines`/`wrap_text_lines`
 * (the edit-field rule; master_detail / checklist / env:native_wrap) are
 * untouched.  [ESCALATED for ratification: a second, display-mode wrap on the
 * shared text_wrap substrate — the read-only analog of the edit wrapper, so
 * chat and any future read-only prose widget wrap identically to the desktop
 * WITHOUT forking a private wrapper into the widget.]
 *
 * Same signatures/semantics as wrap_lines / wrap_text_lines otherwise. */
int wrap_lines_display(const float* xs, int n, const char* shown, float avail,
                       LineSpan* out, int max_lines);
int wrap_text_lines_display(Canvas& canvas, const char* shown, int n,
                            float avail, float* xs, int xs_cap,
                            LineSpan* out, int max_lines);

#endif /* TEXT_WRAP_H */
