/*
 * text_wrap_selftest.cpp — word-wrap PARITY harness ([CXC-R-ENGINE-TEXT]),
 * host AND (optionally) on-device.
 *
 * Proves the CYD text-wrap port (text_wrap.cpp) makes the IDENTICAL
 * line-break decisions as the Python engine's canonical substrate
 * MultiLineEditMixin._wrap_lines — the load-bearing cross-tier check: a wrap
 * that disagrees with the rendered advance forks text per tier.
 *
 * The Python side is the ORACLE: python/scripts/gen_wrap_corpus_header.py runs
 * _wrap_lines over a battery of (string, per-glyph advances, box width) cases
 * and bakes the expected line spans into cyd/test/wrap_corpus_generated.h.
 * This harness drives the SAME cases through the FULL CYD path — a MockCanvas
 * whose measure_text returns the corpus per-glyph advance (exactly as
 * ESP32Canvas::measure_text does at scale == 1), then
 * measure_prefix_advances -> wrap_lines — and matches each span bit-for-bit.
 * Same corpus idiom as dsl_selftest.cpp.
 *
 * Compiled ONLY when WRAP_CORPUS_TEST is defined:
 *
 *   HOST (the hardware-free CI foothold — must print N/N and exit 0):
 *     pio run -e native_wrap && .pio/build/native_wrap/program
 *   or standalone:
 *     clang++ -std=c++17 -DWRAP_CORPUS_TEST -Isrc -Itest -Iinclude \
 *         -o /tmp/wrap_selftest src/text_wrap.cpp src/text_wrap_selftest.cpp
 *     /tmp/wrap_selftest
 */

#ifdef WRAP_CORPUS_TEST

#include "text_wrap.h"
#include "canvas.h"
#include "wrap_corpus_generated.h"

#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------ */
/* MockCanvas — a concrete Canvas whose measure_text sums the corpus     */
/* per-char advances.  Every draw op is a no-op; only text metrics are   */
/* exercised (the wrap path reads nothing else).                         */
/* ------------------------------------------------------------------ */

class MockCanvas : public Canvas {
public:
    const WrapGlyph* glyphs = nullptr;
    int glyph_count = 0;

    float advance_of(char c) const {
        for (int i = 0; i < glyph_count; i++) {
            if (glyphs[i].ch == c) return glyphs[i].advance;
        }
        /* Unreachable for the corpus (every char in `shown` is mapped);
         * mirrors the atlas missing-glyph fallback if it ever fired. */
        return 0.0f;
    }

    /* -- the only method under test ------------------------------------ */
    TextMetrics measure_text(const char* text) override {
        float w = 0.0f;
        for (int i = 0; text[i]; i++) w += advance_of(text[i]);
        return TextMetrics{w, 16.0f, 12.0f, 4.0f};
    }

    /* -- everything else: no-ops to satisfy the abstract contract ------ */
    void fill_rect(float, float, float, float) override {}
    void stroke_rect(float, float, float, float) override {}
    void fill_circle(float, float, float) override {}
    void stroke_circle(float, float, float) override {}
    void fill_round_rect(float, float, float, float, float) override {}
    void stroke_round_rect(float, float, float, float, float) override {}
    void stroke_arc(float, float, float, float, float) override {}
    void fill_arc(float, float, float, float, float) override {}
    void draw_line(float, float, float, float) override {}
    void draw_path(const PathCommand*, int) override {}
    void fill_path(const PathCommand*, int) override {}
    void set_fill_color(uint8_t, uint8_t, uint8_t, uint8_t) override {}
    void set_stroke_color(uint8_t, uint8_t, uint8_t, uint8_t) override {}
    void set_stroke_width(float) override {}
    void set_linear_gradient(float, float, float, float,
                             const GradientStop*, int) override {}
    void set_radial_gradient(float, float, float,
                             const GradientStop*, int) override {}
    void set_shadow(float, float, float,
                    uint8_t, uint8_t, uint8_t, uint8_t) override {}
    void clear_shadow() override {}
    void set_opacity(float) override {}
    void save() override {}
    void restore() override {}
    void translate(float, float) override {}
    void rotate(float) override {}
    void scale(float, float) override {}
    void clip_rect(float, float, float, float) override {}
    void set_font(const char*, float, const char*) override {}
    void draw_text(const char*, float, float, const char*) override {}
    ImageHandle decode_image(const uint8_t*, size_t) override { return nullptr; }
    void draw_image(ImageHandle, float, float, float, float) override {}
    void free_image(ImageHandle) override {}
    void create_surface(int, int) override {}
    void present() override {}
    Size get_surface_size() override { return Size{0, 0}; }
};

/* ------------------------------------------------------------------ */

static constexpr int kMaxLines = 64;
static constexpr int kMaxXs = 256;

int main() {
    MockCanvas mock;
    int passed = 0;

    for (int ci = 0; ci < kWrapCaseCount; ci++) {
        const WrapCase& c = kWrapCases[ci];
        mock.glyphs = c.glyphs;
        mock.glyph_count = c.glyph_count;

        int n = c.shown_len;
        if (n + 1 > kMaxXs) {
            printf("FAIL %-24s : string too long for xs scratch\n", c.id);
            continue;
        }

        float xs[kMaxXs];
        LineSpan out[kMaxLines];
        /* The FULL CYD path: measure per-glyph advances off the canvas, then
         * wrap over them — exactly what a text-bearing widget calls. */
        int count = wrap_text_lines(mock, c.shown, n, c.avail,
                                    xs, kMaxXs, out, kMaxLines);

        bool ok = (count == c.expected_count) && (count <= kMaxLines);
        if (ok) {
            for (int i = 0; i < count; i++) {
                if (out[i].start != c.expected[i].start ||
                    out[i].end != c.expected[i].end) {
                    ok = false;
                    break;
                }
            }
        }

        if (ok) {
            passed++;
        } else {
            printf("FAIL %-24s : got %d lines, want %d [",
                   c.id, count, c.expected_count);
            for (int i = 0; i < count && i < kMaxLines; i++)
                printf("(%d,%d)", out[i].start, out[i].end);
            printf("] want [");
            for (int i = 0; i < c.expected_count; i++)
                printf("(%d,%d)", c.expected[i].start, c.expected[i].end);
            printf("]\n");
        }
    }

    printf("%d/%d wrap parity cases pass\n", passed, kWrapCaseCount);
    bool all = (passed == kWrapCaseCount);
    printf("WRAP-CORPUS RESULT: %s\n", all ? "PASS" : "FAIL");
    return all ? 0 : 1;
}

#endif /* WRAP_CORPUS_TEST */
