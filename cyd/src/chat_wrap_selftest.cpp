/*
 * chat_wrap_selftest.cpp — chat_thread bubble word-wrap PARITY harness
 * ([SPEC:CXC-IC]), host AND (optionally) on-device.
 *
 * Proves a chat bubble wraps byte-identically on the CYD and the desktop.
 * The ORACLE is the DESKTOP chat widget's OWN wrapper
 * ChatThreadWidget._layout_body (composeio/render/widgets/chat_thread.py):
 * python/scripts/gen_chat_wrap_corpus_header.py runs it over a battery of real
 * message bodies at the real bubble inner width and bakes each RENDERED line's
 * plain text into cyd/test/chat_wrap_corpus_generated.h.  This harness drives
 * the SAME bodies through the FULL CYD path that chat_thread.cpp uses — a
 * MockCanvas returning the corpus per-glyph advance (as ESP32Canvas does at
 * scale == 1), then measure_prefix_advances -> wrap_lines, rstripping the
 * trailing soft-wrap space exactly as chat_thread.cpp draws each line — and
 * matches each line STRING bit-for-bit.
 *
 * If chat_thread's desktop wrapper and the CYD text_wrap substrate ever
 * diverge for plain prose, this fails: it is the end-to-end guard behind the
 * "the wrap line-breaks match the python widget" claim.
 *
 * Same corpus idiom as text_wrap_selftest.cpp / dsl_selftest.cpp.  Compiled
 * ONLY when CHAT_WRAP_CORPUS_TEST is defined:
 *
 *   HOST (the hardware-free CI foothold — must print N/N and exit 0):
 *     pio run -e native_chat_wrap && .pio/build/native_chat_wrap/program
 *   or standalone:
 *     clang++ -std=c++17 -DCHAT_WRAP_CORPUS_TEST -Isrc -Itest -Iinclude \
 *         -o /tmp/chat_wrap src/text_wrap.cpp src/chat_wrap_selftest.cpp
 *     /tmp/chat_wrap
 */

#ifdef CHAT_WRAP_CORPUS_TEST

#include "text_wrap.h"
#include "canvas.h"
#include "chat_wrap_corpus_generated.h"

#include <cstdio>
#include <cstring>

/* MockCanvas — measure_text sums the corpus per-char advances (the only
 * method the wrap path reads); every draw op is a no-op. */
class MockCanvas : public Canvas {
public:
    const ChatWrapGlyph* glyphs = nullptr;
    int glyph_count = 0;

    float advance_of(char c) const {
        for (int i = 0; i < glyph_count; i++)
            if (glyphs[i].ch == c) return glyphs[i].advance;
        return 0.0f;
    }

    TextMetrics measure_text(const char* text) override {
        float w = 0.0f;
        for (int i = 0; text[i]; i++) w += advance_of(text[i]);
        return TextMetrics{w, 16.0f, 12.0f, 4.0f};
    }

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

static constexpr int kMaxLines = 96;
static constexpr int kMaxXs = 256;

int main() {
    MockCanvas mock;
    int passed = 0;

    for (int ci = 0; ci < kChatWrapCaseCount; ci++) {
        const ChatWrapCase& c = kChatWrapCases[ci];
        mock.glyphs = c.glyphs;
        mock.glyph_count = c.glyph_count;

        int n = c.shown_len;
        bool ok = (n + 1 <= kMaxXs);
        int count = 0;
        float xs[kMaxXs];
        LineSpan out[kMaxLines];
        if (ok) {
            /* The FULL CYD path a chat bubble body takes (single paragraph —
             * the corpus bodies carry no '\n'): the DISPLAY wrap variant. */
            count = wrap_text_lines_display(mock, c.shown, n, c.avail,
                                            xs, kMaxXs, out, kMaxLines);
            ok = (count == c.line_count) && (count <= kMaxLines);
        }
        if (ok) {
            for (int i = 0; i < count; i++) {
                int s0 = out[i].start, s1 = out[i].end;
                /* rstrip the trailing soft-wrap space — exactly what
                 * chat_thread.cpp does before draw_text. */
                while (s1 > s0 && c.shown[s1 - 1] == ' ') s1--;
                int seg = s1 - s0;
                if ((int)strlen(c.lines[i]) != seg ||
                    strncmp(c.shown + s0, c.lines[i], seg) != 0) {
                    ok = false;
                    break;
                }
            }
        }

        if (ok) {
            passed++;
        } else {
            printf("FAIL %-16s : got %d lines, want %d\n",
                   c.id, count, c.line_count);
            for (int i = 0; i < count && i < kMaxLines; i++) {
                int s0 = out[i].start, s1 = out[i].end;
                while (s1 > s0 && c.shown[s1 - 1] == ' ') s1--;
                printf("   got  [%.*s]\n", s1 - s0, c.shown + s0);
            }
            for (int i = 0; i < c.line_count; i++)
                printf("   want [%s]\n", c.lines[i]);
        }
    }

    printf("%d/%d chat_thread wrap parity cases pass\n",
           passed, kChatWrapCaseCount);
    bool all = (passed == kChatWrapCaseCount);
    printf("CHAT-WRAP-CORPUS RESULT: %s\n", all ? "PASS" : "FAIL");
    return all ? 0 : 1;
}

#endif /* CHAT_WRAP_CORPUS_TEST */
