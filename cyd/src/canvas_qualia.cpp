/*
 * canvas_qualia.cpp — Canvas contract on a full PSRAM framebuffer.
 * [SPEC:CXC-EMBED-REACTIVE] R5.  See canvas_qualia.h.
 *
 * The rasterization idiom is the proven ESP32Canvas one (canvas_esp32
 * .cpp) with three structural differences:
 *   1. No bands: put_pixel writes straight into the 720x720 PSRAM
 *      framebuffer; render_bands restricts the ROW WINDOW instead of
 *      re-rendering per 16-row band.
 *   2. Transforms are real: every op consults the affine matrix.
 *      Identity keeps the fast scanline paths; non-identity uses
 *      inverse-mapped signed-distance coverage (filled prims), device-
 *      space geometry (lines/paths/arc stamps), and inverse-mapped
 *      resampling (glyphs/images).  Non-uniform scale is approximated
 *      with the average axis scale for edge AA (documented; the
 *      binding use case — rotate/scale about the widget center — is a
 *      similarity transform and is exact).
 *   3. Round clip: per-row inscribed-circle spans (xform2d.h) gate all
 *      writes when the panel is round.  RGB565 is NATIVE-endian (the
 *      LCD_CAM RGB peripheral scans memory directly).
 */

#include "canvas_qualia.h"

#include <cstring>
#include <cmath>
#include <cstdlib>
#include <algorithm>

#include "esp_heap_caps.h"
#include "esp_log.h"

static const char* TAG = "canvas_qualia";

/* Font atlas — same generated asset as the band tier. */
#if __has_include("font_atlas.h")
    #include "font_atlas.h"
    #define HAS_FONT_ATLAS 1
#else
    #define HAS_FONT_ATLAS 0
#endif

/* ------------------------------------------------------------------ */
/* Pixel core                                                          */
/* ------------------------------------------------------------------ */

static inline uint16_t rgb565_native(uint8_t r, uint8_t g, uint8_t b) {
    return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

static inline void unpack565_native(uint16_t c, uint8_t& r, uint8_t& g,
                                    uint8_t& b) {
    r = (c >> 8) & 0xF8; r |= r >> 5;
    g = (c >> 3) & 0xFC; g |= g >> 6;
    b = (uint8_t)((c << 3) & 0xF8); b |= b >> 5;
}

void QualiaCanvas::put_pixel(int x, int y, uint8_t r, uint8_t g, uint8_t b,
                             uint8_t a) {
    if (x < 0 || x >= _width) return;
    if (y < _limit_y0 || y > _limit_y1) return;

    if (_round) {
        if (x < _span_x0[y] || x > _span_x1[y]) return;
    }

    if (_state.opacity < 0.999f) {
        a = (uint8_t)(a * _state.opacity + 0.5f);
        if (a == 0) return;
    }

    if (_state.has_clip) {
        if (x < (int)_state.clip_x || x >= (int)(_state.clip_x + _state.clip_w) ||
            y < (int)_state.clip_y || y >= (int)(_state.clip_y + _state.clip_h)) {
            return;
        }
    }

    size_t idx = (size_t)y * _width + x;
    if (a == 255) {
        _fb[idx] = rgb565_native(r, g, b);
    } else if (a == 0) {
        return;
    } else {
        uint8_t dr, dg, db;
        unpack565_native(_fb[idx], dr, dg, db);
        uint16_t inv_a = 255 - a;
        r = (uint8_t)((r * a + dr * inv_a + 127) / 255);
        g = (uint8_t)((g * a + dg * inv_a + 127) / 255);
        b = (uint8_t)((b * a + db * inv_a + 127) / 255);
        _fb[idx] = rgb565_native(r, g, b);
    }
}

void QualiaCanvas::fill_hline(int x0, int x1, int y,
                              uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    if (y < _limit_y0 || y > _limit_y1) return;

    if (_state.has_clip) {
        int cl = (int)_state.clip_x;
        int cr = (int)(_state.clip_x + _state.clip_w);
        if (y < (int)_state.clip_y || y >= (int)(_state.clip_y + _state.clip_h))
            return;
        if (x0 < cl) x0 = cl;
        if (x1 >= cr) x1 = cr - 1;
    }
    if (_round) {
        if (x0 < _span_x0[y]) x0 = _span_x0[y];
        if (x1 > _span_x1[y]) x1 = _span_x1[y];
    }
    if (x0 < 0) x0 = 0;
    if (x1 >= _width) x1 = _width - 1;
    if (x0 > x1) return;

    if (a == 255 && _state.opacity >= 0.999f) {
        uint16_t c = rgb565_native(r, g, b);
        uint16_t* row = _fb + (size_t)y * _width;
        for (int x = x0; x <= x1; x++) row[x] = c;
    } else if (a > 0) {
        for (int x = x0; x <= x1; x++) put_pixel(x, y, r, g, b, a);
    }
}

/* ------------------------------------------------------------------ */
/* Gradient evaluation (local-space coordinates)                       */
/* ------------------------------------------------------------------ */

static inline void lerp_stop(const GradientStop& a, const GradientStop& b,
                             float t, uint8_t& r, uint8_t& g, uint8_t& bb,
                             uint8_t& aa) {
    r = (uint8_t)(a.r + (b.r - a.r) * t);
    g = (uint8_t)(a.g + (b.g - a.g) * t);
    bb = (uint8_t)(a.b + (b.b - a.b) * t);
    aa = (uint8_t)(a.a + (b.a - a.a) * t);
}

void QualiaCanvas::resolve_fill_color(float px, float py, uint8_t& r,
                                      uint8_t& g, uint8_t& b,
                                      uint8_t& a) const {
    if (_state.grad_type == QGradType::None) {
        r = _state.fill_r; g = _state.fill_g;
        b = _state.fill_b; a = _state.fill_a;
        return;
    }

    float t = 0.0f;
    if (_state.grad_type == QGradType::Radial) {
        float dx = px - _state.grad_cx;
        float dy = py - _state.grad_cy;
        float dist = sqrtf(dx * dx + dy * dy);
        t = (_state.grad_r > 0.0f) ? (dist / _state.grad_r) : 1.0f;
    } else {
        float dx = _state.grad_x2 - _state.grad_x1;
        float dy = _state.grad_y2 - _state.grad_y1;
        float len2 = dx * dx + dy * dy;
        if (len2 > 0.0001f)
            t = ((px - _state.grad_x1) * dx + (py - _state.grad_y1) * dy) / len2;
    }
    if (t < 0.0f) t = 0.0f;
    if (t > 1.0f) t = 1.0f;

    int n = _state.grad_stop_count;
    if (n <= 0) {
        r = _state.fill_r; g = _state.fill_g;
        b = _state.fill_b; a = _state.fill_a;
        return;
    }
    if (n == 1 || t <= _state.grad_stops[0].offset) {
        const GradientStop& s = _state.grad_stops[0];
        r = s.r; g = s.g; b = s.b; a = s.a;
        return;
    }
    if (t >= _state.grad_stops[n - 1].offset) {
        const GradientStop& s = _state.grad_stops[n - 1];
        r = s.r; g = s.g; b = s.b; a = s.a;
        return;
    }
    for (int i = 0; i < n - 1; i++) {
        if (t >= _state.grad_stops[i].offset &&
            t <= _state.grad_stops[i + 1].offset) {
            float range = _state.grad_stops[i + 1].offset -
                          _state.grad_stops[i].offset;
            float lt = (range > 0.0001f)
                ? (t - _state.grad_stops[i].offset) / range : 0.0f;
            lerp_stop(_state.grad_stops[i], _state.grad_stops[i + 1], lt,
                      r, g, b, a);
            return;
        }
    }
    const GradientStop& s = _state.grad_stops[n - 1];
    r = s.r; g = s.g; b = s.b; a = s.a;
}

void QualiaCanvas::fill_hline_gradient(int x0, int x1, int y) {
    if (y < _limit_y0 || y > _limit_y1) return;
    if (_state.has_clip) {
        int cl = (int)_state.clip_x;
        int cr = (int)(_state.clip_x + _state.clip_w);
        if (y < (int)_state.clip_y || y >= (int)(_state.clip_y + _state.clip_h))
            return;
        if (x0 < cl) x0 = cl;
        if (x1 >= cr) x1 = cr - 1;
    }
    if (_round) {
        if (x0 < _span_x0[y]) x0 = _span_x0[y];
        if (x1 > _span_x1[y]) x1 = _span_x1[y];
    }
    if (x0 < 0) x0 = 0;
    if (x1 >= _width) x1 = _width - 1;
    for (int x = x0; x <= x1; x++) {
        uint8_t r, g, b, a;
        resolve_fill_color((float)x + 0.5f, (float)y + 0.5f, r, g, b, a);
        if (a > 0) put_pixel(x, y, r, g, b, a);
    }
}

/* ------------------------------------------------------------------ */
/* Surface / frame management                                          */
/* ------------------------------------------------------------------ */

QualiaCanvas::~QualiaCanvas() {
    if (_own_fb) heap_caps_free(_own_fb);   /* _fb may alias a driver fb */
    free(_span_x0);
    free(_span_x1);
}

void QualiaCanvas::reset_state() {
    memset(&_state, 0, sizeof(_state));
    _state.fill_a = 255;
    _state.stroke_r = 255; _state.stroke_g = 255;
    _state.stroke_b = 255; _state.stroke_a = 255;
    _state.stroke_width = 1.0f;
    _state.font_size = 16.0f;
    _state.opacity = 1.0f;
    _state.m = Mat2D::identity();
    _state_depth = 0;
}

void QualiaCanvas::create_surface(int width, int height) {
    _width = width;
    _height = height;

    size_t bytes = (size_t)width * height * sizeof(uint16_t);
    _fb = static_cast<uint16_t*>(
        heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!_fb) {
        ESP_LOGE(TAG, "PSRAM framebuffer alloc failed (%zu bytes)", bytes);
        return;
    }
    memset(_fb, 0, bytes);
    _own_fb = _fb;

    /* Zero-copy swap seam: when the display exposes its two scan-out
     * framebuffers, arm the 3-buffer radar path (render_animated).
     * The canvas-owned buffer above doubles as the background
     * snapshot; all classic paths (wheel, static dirty-band repaint)
     * keep compositing into it and flushing via transfer_band. */
    _zero_copy = false;
    _present_fb = nullptr;
    if (_display) {
        uint16_t* fbs[2] = {nullptr, nullptr};
        if (_display->scanout_framebuffers(fbs, 2) == 2 && fbs[0] && fbs[1]) {
            _scan_fb[0] = fbs[0];
            _scan_fb[1] = fbs[1];
            _scan_front = 0;            /* esp_lcd presents fb0 first */
            _zero_copy = true;
            ESP_LOGI(TAG, "zero-copy swap armed: scan fbs %p / %p",
                     fbs[0], fbs[1]);
        }
    }

    _span_x0 = static_cast<int16_t*>(malloc(sizeof(int16_t) * height));
    _span_x1 = static_cast<int16_t*>(malloc(sizeof(int16_t) * height));
    if (!_span_x0 || !_span_x1) {
        ESP_LOGE(TAG, "round-span table alloc failed");
        _round = false;
    }

    if (_round && _span_x0 && _span_x1) {
        float cx = width / 2.0f, cy = height / 2.0f;
        float r = (width < height ? width : height) / 2.0f;
        for (int y = 0; y < height; y++) {
            int x0, x1;
            xf_round_span(y, cx, cy, r, width, x0, x1);
            _span_x0[y] = (int16_t)x0;
            _span_x1[y] = (int16_t)x1;
        }
    } else if (_span_x0 && _span_x1) {
        for (int y = 0; y < height; y++) {
            _span_x0[y] = 0;
            _span_x1[y] = (int16_t)(width - 1);
        }
    }

    reset_state();
    _limit_y0 = 0;
    _limit_y1 = height - 1;

    ESP_LOGI(TAG, "PSRAM framebuffer surface: %dx%d %s (%zu bytes, "
             "dirty quantum %d rows)", width, height,
             _round ? "round" : "rect", bytes, kBandHeight);
}

Size QualiaCanvas::get_surface_size() {
    return Size{(float)_width, (float)_height};
}

void QualiaCanvas::flush_rows(int y0, int y1) {
    if (!_display || !_fb) return;
    if (y0 < 0) y0 = 0;
    if (y1 >= _height) y1 = _height - 1;
    if (y0 > y1) return;
    _display->transfer_band(_fb + (size_t)y0 * _width, (uint16_t)y0,
                            (uint16_t)y1);
    /* A copy-flush makes the compose buffer the capture source of
     * truth again (the animated path re-arms _present_fb per swap). */
    _present_fb = nullptr;
}

void QualiaCanvas::present() {
    flush_rows(0, _height - 1);
}

void QualiaCanvas::render_frame(DrawCallback draw_fn, void* user_data) {
    render_bands(draw_fn, user_data, 0,
                 (_height + kBandHeight - 1) / kBandHeight - 1);
}

void QualiaCanvas::render_bands(DrawCallback draw_fn, void* user_data,
                                int band_first, int band_last) {
    if (!_fb) return;
    int num_bands = (_height + kBandHeight - 1) / kBandHeight;
    if (band_first < 0) band_first = 0;
    if (band_last >= num_bands) band_last = num_bands - 1;
    if (band_first > band_last) return;

    int y0 = band_first * kBandHeight;
    int y1 = (band_last + 1) * kBandHeight - 1;
    if (y1 >= _height) y1 = _height - 1;

    /* One composite pass, writes gated to the dirty rows. */
    _limit_y0 = y0;
    _limit_y1 = y1;
    reset_state();
    draw_fn(*this, user_data);

    _limit_y0 = 0;
    _limit_y1 = _height - 1;

    flush_rows(y0, y1);
}

/* ------------------------------------------------------------------ */
/* 3-buffer radar path (R5 animated-scene perf)                        */
/* ------------------------------------------------------------------ */

void QualiaCanvas::render_background_bands(DrawCallback draw_fn,
                                           void* user_data,
                                           int band_first, int band_last) {
    if (!_own_fb) return;
    int num_bands = (_height + kBandHeight - 1) / kBandHeight;
    if (band_first < 0) band_first = 0;
    if (band_last >= num_bands) band_last = num_bands - 1;
    if (band_first > band_last) return;

    int y0 = band_first * kBandHeight;
    int y1 = (band_last + 1) * kBandHeight - 1;
    if (y1 >= _height) y1 = _height - 1;

    /* Composite into the background snapshot (the canvas-owned buffer)
     * with writes gated to the requested rows — NO flush; the next
     * animated frame's restore memcpy carries it to the glass. */
    _fb = _own_fb;
    _limit_y0 = y0;
    _limit_y1 = y1;
    reset_state();
    draw_fn(*this, user_data);
    _limit_y0 = 0;
    _limit_y1 = _height - 1;
}

void QualiaCanvas::render_animated(DrawCallback draw_fn, void* user_data) {
    if (!_own_fb) return;
    if (!_zero_copy || !_display) {
        /* No swap seam — classic full composite+flush. */
        render_frame(draw_fn, user_data);
        return;
    }

    size_t bytes = (size_t)_width * _height * sizeof(uint16_t);
    int back = 1 - _scan_front;

    /* 1. Restore: background snapshot -> back scan fb (sequential
     *    PSRAM copy). */
    memcpy(_scan_fb[back], _own_fb, bytes);

    /* 2. Beam-only pass composites ON TOP of the instrument layer
     *    (documented compositing model for the sweep) — all Canvas
     *    semantics incl. round clip apply. */
    _fb = _scan_fb[back];
    _limit_y0 = 0;
    _limit_y1 = _height - 1;
    reset_state();
    draw_fn(*this, user_data);
    _fb = _own_fb;

    /* 3. Present by pointer swap (vsync-latched in the driver). */
    if (_display->present_scanout(_scan_fb[back])) {
        _scan_front = back;
        _present_fb = _scan_fb[back];
    }
}

/* ------------------------------------------------------------------ */
/* Identity fast paths (band-renderer algorithms, bandless)            */
/* ------------------------------------------------------------------ */

void QualiaCanvas::fill_rect_axis(float x, float y, float w, float h) {
    float x0f = x, y0f = y, x1f = x + w, y1f = y + h;
    int py0 = (int)floorf(y0f);
    int py1 = (int)ceilf(y1f) - 1;
    int px0 = (int)floorf(x0f);
    int px1 = (int)ceilf(x1f) - 1;

    bool use_gradient = (_state.grad_type != QGradType::None);
    uint8_t fr = _state.fill_r, fg = _state.fill_g;
    uint8_t fb = _state.fill_b, fa = _state.fill_a;

    float left_cov = (float)(px0 + 1) - x0f;
    float right_cov = x1f - (float)px1;
    float top_cov = (float)(py0 + 1) - y0f;
    float bot_cov = y1f - (float)py1;
    if (left_cov > 1.0f) left_cov = 1.0f;
    if (right_cov > 1.0f) right_cov = 1.0f;
    if (top_cov > 1.0f) top_cov = 1.0f;
    if (bot_cov > 1.0f) bot_cov = 1.0f;

    int ix0 = px0 + (left_cov < 0.999f ? 1 : 0);
    int ix1 = px1 - (right_cov < 0.999f ? 1 : 0);

    for (int row = py0; row <= py1; row++) {
        if (row < _limit_y0 || row > _limit_y1) continue;

        float ycov = 1.0f;
        if (py0 == py1) ycov = y1f - y0f;
        else if (row == py0) ycov = top_cov;
        else if (row == py1) ycov = bot_cov;
        if (ycov <= 0.0f) continue;

        bool is_edge_row = (row == py0 && top_cov < 0.999f) ||
                           (row == py1 && bot_cov < 0.999f);

        if (use_gradient) {
            for (int col = px0; col <= px1; col++) {
                float xcov = 1.0f;
                if (px0 == px1) xcov = x1f - x0f;
                else if (col == px0) xcov = left_cov;
                else if (col == px1) xcov = right_cov;
                float coverage = xcov * ycov;
                uint8_t gr, gg, gb, ga;
                resolve_fill_color((float)col + 0.5f, (float)row + 0.5f,
                                   gr, gg, gb, ga);
                uint8_t aa = (uint8_t)(ga * coverage + 0.5f);
                if (aa > 0) put_pixel(col, row, gr, gg, gb, aa);
            }
        } else {
            if (left_cov < 0.999f && px0 <= px1) {
                uint8_t aa = (uint8_t)(fa * left_cov * ycov + 0.5f);
                if (aa > 0) put_pixel(px0, row, fr, fg, fb, aa);
            }
            if (ix0 <= ix1) {
                if (is_edge_row) {
                    uint8_t aa = (uint8_t)(fa * ycov + 0.5f);
                    fill_hline(ix0, ix1, row, fr, fg, fb, aa);
                } else {
                    fill_hline(ix0, ix1, row, fr, fg, fb, fa);
                }
            }
            if (right_cov < 0.999f && px1 > px0) {
                uint8_t aa = (uint8_t)(fa * right_cov * ycov + 0.5f);
                if (aa > 0) put_pixel(px1, row, fr, fg, fb, aa);
            }
        }
    }
}

void QualiaCanvas::fill_circle_axis(float cx, float cy, float r) {
    if (r <= 0.0f) return;
    int py0 = (int)floorf(cy - r) - 1;
    int py1 = (int)ceilf(cy + r) + 1;
    bool use_gradient = (_state.grad_type != QGradType::None);
    uint8_t fr = _state.fill_r, fg = _state.fill_g;
    uint8_t fb = _state.fill_b, fa = _state.fill_a;
    float r2 = r * r;

    for (int row = py0; row <= py1; row++) {
        if (row < _limit_y0 || row > _limit_y1) continue;
        float dy = (float)row + 0.5f - cy;
        float dy2 = dy * dy;

        if (dy2 >= r2) continue;
        float x_span = sqrtf(r2 - dy2);
        int px_left = (int)floorf(cx - x_span) - 1;
        int px_right = (int)floorf(cx + x_span) + 1;

        for (int col = px_left; col <= px_right; col++) {
            float dx = (float)col + 0.5f - cx;
            float dist = sqrtf(dy2 + dx * dx) - r;
            float cov = 0.5f - dist;
            if (cov <= 0.0f) continue;
            if (cov >= 1.0f) {
                /* run of interior pixels — find the end and hline it */
                int run_end = col;
                while (run_end + 1 <= px_right) {
                    float ndx = (float)(run_end + 1) + 0.5f - cx;
                    if (sqrtf(dy2 + ndx * ndx) - r > -0.5f) break;
                    run_end++;
                }
                if (use_gradient) fill_hline_gradient(col, run_end, row);
                else fill_hline(col, run_end, row, fr, fg, fb, fa);
                col = run_end;
                continue;
            }
            if (use_gradient) {
                uint8_t gr, gg, gb, ga;
                resolve_fill_color((float)col + 0.5f, (float)row + 0.5f,
                                   gr, gg, gb, ga);
                uint8_t aa = (uint8_t)(ga * cov + 0.5f);
                if (aa > 0) put_pixel(col, row, gr, gg, gb, aa);
            } else {
                uint8_t aa = (uint8_t)(fa * cov + 0.5f);
                if (aa > 0) put_pixel(col, row, fr, fg, fb, aa);
            }
        }
    }
}

void QualiaCanvas::fill_round_rect_axis(float x, float y, float w, float h,
                                        float radius) {
    float r = radius;
    if (r > w / 2.0f) r = w / 2.0f;
    if (r > h / 2.0f) r = h / 2.0f;
    if (r <= 0.5f) { fill_rect_axis(x, y, w, h); return; }

    int py0 = (int)floorf(y);
    int py1 = (int)ceilf(y + h) - 1;
    bool use_gradient = (_state.grad_type != QGradType::None);
    uint8_t fr = _state.fill_r, fg = _state.fill_g;
    uint8_t fb = _state.fill_b, fa = _state.fill_a;

    for (int row = py0; row <= py1; row++) {
        if (row < _limit_y0 || row > _limit_y1) continue;
        float ly = (float)row + 0.5f;
        int px0 = (int)floorf(x) - 1;
        int px1 = (int)ceilf(x + w);
        for (int col = px0; col <= px1; col++) {
            float lx = (float)col + 0.5f;
            float d = xf_rrect_sdist(lx, ly, x, y, w, h, r);
            float cov = xf_cov(d);
            if (cov <= 0.0f) continue;
            if (cov >= 0.999f && !use_gradient) {
                /* interior run */
                int run_end = col;
                while (run_end + 1 <= px1) {
                    float nlx = (float)(run_end + 1) + 0.5f;
                    if (xf_cov(xf_rrect_sdist(nlx, ly, x, y, w, h, r)) < 0.999f)
                        break;
                    run_end++;
                }
                fill_hline(col, run_end, row, fr, fg, fb, fa);
                col = run_end;
                continue;
            }
            uint8_t cr_, cg_, cb_, ca_;
            resolve_fill_color(lx, ly, cr_, cg_, cb_, ca_);
            uint8_t aa = (uint8_t)(ca_ * cov + 0.5f);
            if (aa > 0) put_pixel(col, row, cr_, cg_, cb_, aa);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Transformed field fill (inverse-mapped SDF coverage)                */
/* ------------------------------------------------------------------ */

void QualiaCanvas::fill_field_xf(Field kind, float x, float y, float w,
                                 float h, float radius) {
    Mat2D inv;
    if (!_state.m.invert(inv)) return;      /* degenerate scale 0 */

    float bx0, by0, bx1, by1;
    xf_rect_bbox(_state.m, x - 1, y - 1, w + 2, h + 2, bx0, by0, bx1, by1);

    int py0 = (int)floorf(by0);
    int py1 = (int)ceilf(by1);
    int px0 = (int)floorf(bx0);
    int px1 = (int)ceilf(bx1);
    if (py0 < _limit_y0) py0 = _limit_y0;
    if (py1 > _limit_y1) py1 = _limit_y1;
    if (px0 < 0) px0 = 0;
    if (px1 >= _width) px1 = _width - 1;

    float s = _state.m.scale_avg();
    if (s <= 0.0f) return;

    float cx = x + w * 0.5f, cy = y + h * 0.5f, cr = w * 0.5f;

    for (int row = py0; row <= py1; row++) {
        float dy = (float)row + 0.5f;
        for (int col = px0; col <= px1; col++) {
            float lx, ly;
            inv.apply((float)col + 0.5f, dy, lx, ly);
            float d;
            switch (kind) {
                case Field::Rect:   d = xf_rect_sdist(lx, ly, x, y, w, h); break;
                case Field::RRect:  d = xf_rrect_sdist(lx, ly, x, y, w, h, radius); break;
                default:            d = xf_circle_sdist(lx, ly, cx, cy, cr); break;
            }
            float cov = xf_cov(d * s);
            if (cov <= 0.0f) continue;
            uint8_t r_, g_, b_, a_;
            resolve_fill_color(lx, ly, r_, g_, b_, a_);
            uint8_t aa = (uint8_t)(a_ * cov + 0.5f);
            if (aa > 0) put_pixel(col, row, r_, g_, b_, aa);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Contract geometry ops                                               */
/* ------------------------------------------------------------------ */

void QualiaCanvas::fill_rect(float x, float y, float w, float h) {
    if (w <= 0 || h <= 0) return;
    if (_state.m.is_identity()) fill_rect_axis(x, y, w, h);
    else fill_field_xf(Field::Rect, x, y, w, h, 0);
}

void QualiaCanvas::stroke_rect(float x, float y, float w, float h) {
    float sw = _state.stroke_width;
    float half = sw / 2.0f;

    uint8_t save_fr = _state.fill_r, save_fg = _state.fill_g;
    uint8_t save_fb = _state.fill_b, save_fa = _state.fill_a;
    QGradType save_grad = _state.grad_type;
    _state.fill_r = _state.stroke_r; _state.fill_g = _state.stroke_g;
    _state.fill_b = _state.stroke_b; _state.fill_a = _state.stroke_a;
    _state.grad_type = QGradType::None;

    fill_rect(x - half, y - half, w + sw, sw);
    fill_rect(x - half, y + h - half, w + sw, sw);
    fill_rect(x - half, y + half, sw, h - sw);
    fill_rect(x + w - half, y + half, sw, h - sw);

    _state.fill_r = save_fr; _state.fill_g = save_fg;
    _state.fill_b = save_fb; _state.fill_a = save_fa;
    _state.grad_type = save_grad;
}

void QualiaCanvas::fill_circle(float cx, float cy, float r) {
    if (r <= 0.0f) return;
    if (_state.m.is_identity()) fill_circle_axis(cx, cy, r);
    else fill_field_xf(Field::Circle, cx - r, cy - r, 2 * r, 2 * r, 0);
}

void QualiaCanvas::fill_round_rect(float x, float y, float w, float h,
                                   float radius) {
    if (w <= 0 || h <= 0) return;
    float r = radius;
    if (r > w / 2.0f) r = w / 2.0f;
    if (r > h / 2.0f) r = h / 2.0f;
    if (_state.m.is_identity()) fill_round_rect_axis(x, y, w, h, r);
    else fill_field_xf(Field::RRect, x, y, w, h, r);
}

/* ------------------------------------------------------------------ */
/* Stroked curves — round-cap disc stamping (device space)             */
/* ------------------------------------------------------------------ */

void QualiaCanvas::stamp_stroke_disc(int icx, int icy, int ir, uint8_t cr,
                                     uint8_t cg, uint8_t cb, uint8_t ca) {
    if (ir <= 0) {
        put_pixel(icx, icy, cr, cg, cb, ca);
        return;
    }
    float rf = (float)ir;
    for (int dy = -ir - 1; dy <= ir + 1; dy++) {
        int y = icy + dy;
        if (y < _limit_y0 || y > _limit_y1) continue;
        float fy2 = (float)dy * (float)dy;
        for (int dx = -ir - 1; dx <= ir + 1; dx++) {
            float dist = sqrtf(fy2 + (float)dx * (float)dx) - rf;
            float cov = 0.5f - dist;
            if (cov <= 0.0f) continue;
            if (cov > 1.0f) cov = 1.0f;
            uint8_t aa = (uint8_t)(ca * cov + 0.5f);
            if (aa > 0) put_pixel(icx + dx, y, cr, cg, cb, aa);
        }
    }
}

void QualiaCanvas::stroke_arc(float cx, float cy, float r,
                              float start_angle, float end_angle) {
    uint8_t sr = _state.stroke_r, sg = _state.stroke_g;
    uint8_t sb = _state.stroke_b, sa = _state.stroke_a;

    const Mat2D& m = _state.m;
    float s = m.is_identity() ? 1.0f : m.scale_avg();
    int disc_r = (int)(_state.stroke_width * s / 2.0f + 0.5f);
    if (disc_r < 0) disc_r = 0;

    float r_dev = r * s;
    float abs_sweep = fabsf(end_angle - start_angle);
    float ang_step;
    if (r_dev > 1.0f) {
        ang_step = 1.0f / r_dev;
        float min_step = 0.5f * (float)M_PI / 180.0f;
        if (ang_step < min_step) ang_step = min_step;
    } else {
        ang_step = 0.1f;
    }
    int steps = (int)(abs_sweep / ang_step) + 1;
    if (steps < 1) steps = 1;
    float direction = (end_angle >= start_angle) ? 1.0f : -1.0f;

    for (int i = 0; i <= steps; i++) {
        float angle = start_angle + direction * ang_step * (float)i;
        if (direction > 0 && angle > end_angle) angle = end_angle;
        if (direction < 0 && angle < end_angle) angle = end_angle;
        /* point in LOCAL space, then through the matrix */
        float lx = cx + cosf(angle) * r;
        float ly = cy + sinf(angle) * r;
        float dx, dy;
        m.apply(lx, ly, dx, dy);
        stamp_stroke_disc((int)(dx + 0.5f), (int)(dy + 0.5f), disc_r,
                          sr, sg, sb, sa);
    }
}

void QualiaCanvas::stroke_circle(float cx, float cy, float r) {
    stroke_arc(cx, cy, r, 0.0f, 2.0f * (float)M_PI);
}

void QualiaCanvas::fill_arc(float cx, float cy, float r,
                            float start_angle, float end_angle) {
    if (r <= 0.0f) return;

    float sweep = end_angle - start_angle;
    if (sweep == 0.0f) return;
    if (sweep < 0.0f) { start_angle = end_angle; sweep = -sweep; }
    const float two_pi = 2.0f * (float)M_PI;
    bool full_circle = (sweep >= two_pi - 1e-4f);
    float a0 = fmodf(start_angle, two_pi);
    if (a0 < 0) a0 += two_pi;

    Mat2D inv;
    bool xf = !_state.m.is_identity();
    if (xf && !_state.m.invert(inv)) return;
    float s = xf ? _state.m.scale_avg() : 1.0f;

    float bx0, by0, bx1, by1;
    if (xf) {
        xf_rect_bbox(_state.m, cx - r - 1, cy - r - 1, 2 * r + 2, 2 * r + 2,
                     bx0, by0, bx1, by1);
    } else {
        bx0 = cx - r - 1; by0 = cy - r - 1;
        bx1 = cx + r + 1; by1 = cy + r + 1;
    }
    int py0 = (int)floorf(by0), py1 = (int)ceilf(by1);
    int px0 = (int)floorf(bx0), px1 = (int)ceilf(bx1);
    if (py0 < _limit_y0) py0 = _limit_y0;
    if (py1 > _limit_y1) py1 = _limit_y1;
    if (px0 < 0) px0 = 0;
    if (px1 >= _width) px1 = _width - 1;

    bool use_gradient = (_state.grad_type != QGradType::None);
    uint8_t fr = _state.fill_r, fg = _state.fill_g;
    uint8_t fb = _state.fill_b, fa = _state.fill_a;

    for (int row = py0; row <= py1; row++) {
        for (int col = px0; col <= px1; col++) {
            float lx, ly;
            if (xf) inv.apply((float)col + 0.5f, (float)row + 0.5f, lx, ly);
            else { lx = (float)col + 0.5f; ly = (float)row + 0.5f; }

            float dx = lx - cx, dy = ly - cy;
            float dist = sqrtf(dx * dx + dy * dy);
            float cov = (r - dist + 0.5f / s);
            cov *= s;                      /* device-space AA width */
            if (cov <= 0.0f) continue;
            if (cov > 1.0f) cov = 1.0f;

            if (!full_circle) {
                float ang = atan2f(dy, dx);
                float rel = ang - a0;
                rel = fmodf(rel, two_pi);
                if (rel < 0) rel += two_pi;
                if (rel > sweep) continue;
            }

            if (use_gradient) {
                uint8_t gr, gg, gb, ga;
                resolve_fill_color(lx, ly, gr, gg, gb, ga);
                uint8_t aa = (uint8_t)(ga * cov + 0.5f);
                if (aa > 0) put_pixel(col, row, gr, gg, gb, aa);
            } else {
                uint8_t aa = (uint8_t)(fa * cov + 0.5f);
                if (aa > 0) put_pixel(col, row, fr, fg, fb, aa);
            }
        }
    }
}

/* ------------------------------------------------------------------ */
/* Lines (device-space Wu / thick coverage — endpoints transformed)    */
/* ------------------------------------------------------------------ */

void QualiaCanvas::draw_line_device(float x1, float y1, float x2, float y2,
                                    float sw) {
    uint8_t sr = _state.stroke_r, sg = _state.stroke_g;
    uint8_t sb = _state.stroke_b, sa = _state.stroke_a;

    float dx = x2 - x1;
    float dy = y2 - y1;
    float line_len = sqrtf(dx * dx + dy * dy);
    if (line_len < 0.001f) {
        put_pixel((int)(x1 + 0.5f), (int)(y1 + 0.5f), sr, sg, sb, sa);
        return;
    }

    float nx = -dy / line_len;
    float ny = dx / line_len;
    float half_w = sw / 2.0f;

    if (sw <= 1.5f) {
        bool steep = fabsf(dy) > fabsf(dx);
        float fx1 = x1, fy1 = y1, fx2 = x2, fy2 = y2;
        if (steep) { std::swap(fx1, fy1); std::swap(fx2, fy2); }
        if (fx1 > fx2) { std::swap(fx1, fx2); std::swap(fy1, fy2); }

        float grad = (fx2 - fx1 > 0.001f) ? (fy2 - fy1) / (fx2 - fx1) : 0.0f;

        int xpxl1 = (int)(fx1 + 0.5f);
        float yend1 = fy1 + grad * ((float)xpxl1 - fx1);
        int ypxl1 = (int)floorf(yend1);
        float frac1 = yend1 - (float)ypxl1;
        if (steep) {
            put_pixel(ypxl1, xpxl1, sr, sg, sb, (uint8_t)(sa * (1.0f - frac1) + 0.5f));
            put_pixel(ypxl1 + 1, xpxl1, sr, sg, sb, (uint8_t)(sa * frac1 + 0.5f));
        } else {
            put_pixel(xpxl1, ypxl1, sr, sg, sb, (uint8_t)(sa * (1.0f - frac1) + 0.5f));
            put_pixel(xpxl1, ypxl1 + 1, sr, sg, sb, (uint8_t)(sa * frac1 + 0.5f));
        }
        float intery = yend1 + grad;

        int xpxl2 = (int)(fx2 + 0.5f);
        float yend2 = fy2 + grad * ((float)xpxl2 - fx2);
        int ypxl2 = (int)floorf(yend2);
        float frac2 = yend2 - (float)ypxl2;
        if (steep) {
            put_pixel(ypxl2, xpxl2, sr, sg, sb, (uint8_t)(sa * (1.0f - frac2) + 0.5f));
            put_pixel(ypxl2 + 1, xpxl2, sr, sg, sb, (uint8_t)(sa * frac2 + 0.5f));
        } else {
            put_pixel(xpxl2, ypxl2, sr, sg, sb, (uint8_t)(sa * (1.0f - frac2) + 0.5f));
            put_pixel(xpxl2, ypxl2 + 1, sr, sg, sb, (uint8_t)(sa * frac2 + 0.5f));
        }

        for (int xi = xpxl1 + 1; xi < xpxl2; xi++) {
            int iy = (int)floorf(intery);
            float frac = intery - (float)iy;
            if (steep) {
                put_pixel(iy, xi, sr, sg, sb, (uint8_t)(sa * (1.0f - frac) + 0.5f));
                put_pixel(iy + 1, xi, sr, sg, sb, (uint8_t)(sa * frac + 0.5f));
            } else {
                put_pixel(xi, iy, sr, sg, sb, (uint8_t)(sa * (1.0f - frac) + 0.5f));
                put_pixel(xi, iy + 1, sr, sg, sb, (uint8_t)(sa * frac + 0.5f));
            }
            intery += grad;
        }
    } else {
        int min_y = (int)floorf(std::min({y1, y2}) - half_w) - 1;
        int max_y = (int)ceilf(std::max({y1, y2}) + half_w) + 1;
        int min_x = (int)floorf(std::min({x1, x2}) - half_w) - 1;
        int max_x = (int)ceilf(std::max({x1, x2}) + half_w) + 1;
        if (min_y < _limit_y0) min_y = _limit_y0;
        if (max_y > _limit_y1) max_y = _limit_y1;
        if (min_x < 0) min_x = 0;
        if (max_x >= _width) max_x = _width - 1;

        float tx = dx / line_len;
        float ty = dy / line_len;

        for (int row = min_y; row <= max_y; row++) {
            for (int col = min_x; col <= max_x; col++) {
                float px = (float)col + 0.5f - x1;
                float py = (float)row + 0.5f - y1;
                float along = px * tx + py * ty;
                float perp = fabsf(px * nx + py * ny);
                float perp_cov = half_w + 0.5f - perp;
                if (perp_cov <= 0.0f) continue;
                if (perp_cov > 1.0f) perp_cov = 1.0f;
                float cap_cov = 1.0f;
                if (along < 0.0f) cap_cov = along + 0.5f;
                else if (along > line_len) cap_cov = 0.5f - (along - line_len);
                if (cap_cov <= 0.0f) continue;
                if (cap_cov > 1.0f) cap_cov = 1.0f;
                uint8_t aa = (uint8_t)(sa * perp_cov * cap_cov + 0.5f);
                if (aa > 0) put_pixel(col, row, sr, sg, sb, aa);
            }
        }
    }
}

void QualiaCanvas::draw_line(float x1, float y1, float x2, float y2) {
    const Mat2D& m = _state.m;
    float sw = _state.stroke_width;
    if (!m.is_identity()) {
        float a1, b1, a2, b2;
        m.apply(x1, y1, a1, b1);
        m.apply(x2, y2, a2, b2);
        draw_line_device(a1, b1, a2, b2, sw * m.scale_avg());
    } else {
        draw_line_device(x1, y1, x2, y2, sw);
    }
}

/* ------------------------------------------------------------------ */
/* Paths — flatten local, transform endpoints, scanline fill / stroke  */
/* ------------------------------------------------------------------ */

static constexpr int kCurveSteps = 16;
static constexpr int kMaxPathEdges = 384;

struct QPathEdge { float x0, y0, x1, y1; };
static QPathEdge s_edges[kMaxPathEdges];

static int q_flatten_path(const PathCommand* cmds, int count, bool close_all) {
    int n = 0;
    float cur_x = 0, cur_y = 0, sub_x = 0, sub_y = 0;
    bool have_cur = false;

    auto emit = [&](float x0, float y0, float x1, float y1) -> bool {
        if (x0 == x1 && y0 == y1) return true;
        if (n >= kMaxPathEdges) return false;
        s_edges[n++] = QPathEdge{x0, y0, x1, y1};
        return true;
    };

    for (int i = 0; i < count; i++) {
        const PathCommand& c = cmds[i];
        switch (c.op) {
            case PathCommand::Op::Move:
                if (close_all && have_cur &&
                    (cur_x != sub_x || cur_y != sub_y)) {
                    if (!emit(cur_x, cur_y, sub_x, sub_y)) return -1;
                }
                cur_x = sub_x = c.x;
                cur_y = sub_y = c.y;
                have_cur = true;
                break;
            case PathCommand::Op::Line:
                if (!have_cur) { cur_x = sub_x = c.x; cur_y = sub_y = c.y; have_cur = true; break; }
                if (!emit(cur_x, cur_y, c.x, c.y)) return -1;
                cur_x = c.x; cur_y = c.y;
                break;
            case PathCommand::Op::Quad: {
                if (!have_cur) { cur_x = sub_x = c.x; cur_y = sub_y = c.y; have_cur = true; break; }
                float px = cur_x, py = cur_y;
                for (int s = 1; s <= kCurveSteps; s++) {
                    float t = (float)s / kCurveSteps;
                    float mt = 1.0f - t;
                    float bx = mt * mt * cur_x + 2 * mt * t * c.cx1 + t * t * c.x;
                    float by = mt * mt * cur_y + 2 * mt * t * c.cy1 + t * t * c.y;
                    if (!emit(px, py, bx, by)) return -1;
                    px = bx; py = by;
                }
                cur_x = c.x; cur_y = c.y;
                break;
            }
            case PathCommand::Op::Cubic: {
                if (!have_cur) { cur_x = sub_x = c.x; cur_y = sub_y = c.y; have_cur = true; break; }
                float px = cur_x, py = cur_y;
                for (int s = 1; s <= kCurveSteps; s++) {
                    float t = (float)s / kCurveSteps;
                    float mt = 1.0f - t;
                    float bx = mt * mt * mt * cur_x + 3 * mt * mt * t * c.cx1 +
                               3 * mt * t * t * c.cx2 + t * t * t * c.x;
                    float by = mt * mt * mt * cur_y + 3 * mt * mt * t * c.cy1 +
                               3 * mt * t * t * c.cy2 + t * t * t * c.y;
                    if (!emit(px, py, bx, by)) return -1;
                    px = bx; py = by;
                }
                cur_x = c.x; cur_y = c.y;
                break;
            }
            case PathCommand::Op::Close:
                if (have_cur && (cur_x != sub_x || cur_y != sub_y)) {
                    if (!emit(cur_x, cur_y, sub_x, sub_y)) return -1;
                }
                cur_x = sub_x; cur_y = sub_y;
                break;
        }
    }
    if (close_all && have_cur && (cur_x != sub_x || cur_y != sub_y)) {
        if (!emit(cur_x, cur_y, sub_x, sub_y)) return -1;
    }
    return n;
}

void QualiaCanvas::draw_path(const PathCommand* cmds, int count) {
    int n = q_flatten_path(cmds, count, false);
    if (n < 0) {
        ESP_LOGW(TAG, "draw_path: edge pool overflow (>%d), truncated",
                 kMaxPathEdges);
        n = kMaxPathEdges;
    }
    for (int i = 0; i < n; i++) {
        draw_line(s_edges[i].x0, s_edges[i].y0, s_edges[i].x1, s_edges[i].y1);
    }
}

void QualiaCanvas::fill_path(const PathCommand* cmds, int count) {
    int n = q_flatten_path(cmds, count, true);
    if (n < 0) {
        ESP_LOGW(TAG, "fill_path: edge pool overflow (>%d), dropped",
                 kMaxPathEdges);
        return;
    }
    if (n == 0) return;

    /* Transform edge endpoints to device space (identity = no-op). */
    const Mat2D& m = _state.m;
    if (!m.is_identity()) {
        for (int i = 0; i < n; i++) {
            m.apply(s_edges[i].x0, s_edges[i].y0, s_edges[i].x0, s_edges[i].y0);
            m.apply(s_edges[i].x1, s_edges[i].y1, s_edges[i].x1, s_edges[i].y1);
        }
    }

    float min_y = s_edges[0].y0, max_y = s_edges[0].y0;
    for (int i = 0; i < n; i++) {
        min_y = std::min({min_y, s_edges[i].y0, s_edges[i].y1});
        max_y = std::max({max_y, s_edges[i].y0, s_edges[i].y1});
    }
    int ry0 = (int)floorf(min_y);
    int ry1 = (int)ceilf(max_y);
    if (ry0 < _limit_y0) ry0 = _limit_y0;
    if (ry1 > _limit_y1) ry1 = _limit_y1;

    bool use_gradient = (_state.grad_type != QGradType::None);
    uint8_t fr = _state.fill_r, fg = _state.fill_g;
    uint8_t fb = _state.fill_b, fa = _state.fill_a;

    Mat2D inv;
    bool xf = !m.is_identity() && m.invert(inv);

    static constexpr int kMaxCrossings = 32;
    float xs[kMaxCrossings];

    for (int row = ry0; row <= ry1; row++) {
        float sy = (float)row + 0.5f;
        int nx = 0;
        for (int i = 0; i < n && nx < kMaxCrossings; i++) {
            float y0 = s_edges[i].y0, y1 = s_edges[i].y1;
            if ((sy >= y0 && sy < y1) || (sy >= y1 && sy < y0)) {
                float t = (sy - y0) / (y1 - y0);
                xs[nx++] = s_edges[i].x0 + t * (s_edges[i].x1 - s_edges[i].x0);
            }
        }
        for (int i = 1; i < nx; i++) {
            float v = xs[i];
            int j = i - 1;
            while (j >= 0 && xs[j] > v) { xs[j + 1] = xs[j]; j--; }
            xs[j + 1] = v;
        }
        for (int i = 0; i + 1 < nx; i += 2) {
            int x0 = (int)ceilf(xs[i] - 0.5f);
            int x1 = (int)floorf(xs[i + 1] - 0.5f);
            if (x0 > x1) continue;
            if (use_gradient) {
                if (xf) {
                    /* gradient is authored in LOCAL space */
                    for (int x = x0; x <= x1; x++) {
                        float lx, ly;
                        inv.apply((float)x + 0.5f, sy, lx, ly);
                        uint8_t gr, gg, gb, ga;
                        resolve_fill_color(lx, ly, gr, gg, gb, ga);
                        if (ga > 0) put_pixel(x, row, gr, gg, gb, ga);
                    }
                } else {
                    fill_hline_gradient(x0, x1, row);
                }
            } else {
                fill_hline(x0, x1, row, fr, fg, fb, fa);
            }
        }
    }
}

void QualiaCanvas::stroke_round_rect(float x, float y, float w, float h,
                                     float radius) {
    float cr = radius;
    if (cr > w / 2.0f) cr = w / 2.0f;
    if (cr > h / 2.0f) cr = h / 2.0f;

    float pi = (float)M_PI;
    float pi2 = pi / 2.0f;

    /* Corner arcs in local space (stroke_arc transforms). */
    stroke_arc(x + cr, y + cr, cr, pi, pi + pi2);
    stroke_arc(x + w - cr, y + cr, cr, pi + pi2, 2.0f * pi);
    stroke_arc(x + w - cr, y + h - cr, cr, 0.0f, pi2);
    stroke_arc(x + cr, y + h - cr, cr, pi2, pi);

    /* Straight edges as stroked lines (transform-safe). */
    draw_line(x + cr, y, x + w - cr, y);
    draw_line(x + cr, y + h, x + w - cr, y + h);
    draw_line(x, y + cr, x, y + h - cr);
    draw_line(x + w, y + cr, x + w, y + h - cr);
}

/* ------------------------------------------------------------------ */
/* Style state                                                         */
/* ------------------------------------------------------------------ */

void QualiaCanvas::set_fill_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    _state.fill_r = r; _state.fill_g = g; _state.fill_b = b; _state.fill_a = a;
    _state.grad_type = QGradType::None;
}

void QualiaCanvas::set_stroke_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    _state.stroke_r = r; _state.stroke_g = g;
    _state.stroke_b = b; _state.stroke_a = a;
}

void QualiaCanvas::set_stroke_width(float w) { _state.stroke_width = w; }

void QualiaCanvas::set_linear_gradient(float x1, float y1, float x2, float y2,
                                       const GradientStop* stops, int count) {
    _state.grad_type = QGradType::Linear;
    _state.grad_x1 = x1; _state.grad_y1 = y1;
    _state.grad_x2 = x2; _state.grad_y2 = y2;
    _state.grad_stop_count =
        (count > kMaxGradientStops) ? kMaxGradientStops : count;
    for (int i = 0; i < _state.grad_stop_count; i++)
        _state.grad_stops[i] = stops[i];
}

void QualiaCanvas::set_radial_gradient(float cx, float cy, float radius,
                                       const GradientStop* stops, int count) {
    _state.grad_type = QGradType::Radial;
    _state.grad_cx = cx; _state.grad_cy = cy; _state.grad_r = radius;
    _state.grad_stop_count =
        (count > kMaxGradientStops) ? kMaxGradientStops : count;
    for (int i = 0; i < _state.grad_stop_count; i++)
        _state.grad_stops[i] = stops[i];
}

void QualiaCanvas::set_shadow(float offset_x, float offset_y, float blur,
                              uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
    _state.has_shadow = (a > 0 && (blur > 0 || offset_x != 0 || offset_y != 0));
    _state.shadow_ox = offset_x;
    _state.shadow_oy = offset_y;
    _state.shadow_blur = blur;
    _state.shadow_r = r; _state.shadow_g = g;
    _state.shadow_b = b; _state.shadow_a = a;
}

void QualiaCanvas::clear_shadow() { _state.has_shadow = false; }

void QualiaCanvas::set_opacity(float a) {
    if (a < 0.0f) a = 0.0f;
    if (a > 1.0f) a = 1.0f;
    _state.opacity = a;
}

void QualiaCanvas::save() {
    if (_state_depth < kMaxStateDepth) {
        _state_stack[_state_depth] = _state;
        _state_depth++;
    }
}

void QualiaCanvas::restore() {
    if (_state_depth > 0) {
        _state_depth--;
        _state = _state_stack[_state_depth];
    }
}

/* Transform ops — REAL on this tier ([CXC-ER-C6]). */

void QualiaCanvas::translate(float dx, float dy) { _state.m.translate(dx, dy); }
void QualiaCanvas::rotate(float angle) { _state.m.rotate(angle); }
void QualiaCanvas::scale(float sx, float sy) { _state.m.scale(sx, sy); }

void QualiaCanvas::clip_rect(float x, float y, float w, float h) {
    /* Device-space AABB of the (possibly transformed) clip rect. */
    float x0 = x, y0 = y, x1 = x + w, y1 = y + h;
    if (!_state.m.is_identity()) {
        xf_rect_bbox(_state.m, x, y, w, h, x0, y0, x1, y1);
    }
    float nw = x1 - x0, nh = y1 - y0;
    if (_state.has_clip) {
        float cx1 = std::max(x0, _state.clip_x);
        float cy1 = std::max(y0, _state.clip_y);
        float cx2 = std::min(x0 + nw, _state.clip_x + _state.clip_w);
        float cy2 = std::min(y0 + nh, _state.clip_y + _state.clip_h);
        _state.clip_x = cx1;
        _state.clip_y = cy1;
        _state.clip_w = (cx2 > cx1) ? cx2 - cx1 : 0;
        _state.clip_h = (cy2 > cy1) ? cy2 - cy1 : 0;
    } else {
        _state.clip_x = x0;
        _state.clip_y = y0;
        _state.clip_w = nw;
        _state.clip_h = nh;
        _state.has_clip = true;
    }
}

/* ------------------------------------------------------------------ */
/* Text — atlas glyphs; transformed text is inverse-mapped/resampled   */
/* ------------------------------------------------------------------ */

/* Case-insensitive family match: "commp" / "common pixel" -> the COMMP
 * pixel family; anything else -> the default sans-serif family.  Kept
 * local (not the atlas header's helper) so set_font works even in the
 * no-atlas fallback build. */
static bool family_is_commp(const char* family) {
    if (!family) return false;
    auto lc = [](char c) -> char {
        return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c; };
    auto ieq = [&](const char* s, const char* t) -> bool {
        while (*s && *t) { if (lc(*s) != lc(*t)) return false; s++; t++; }
        return *s == *t; };
    return ieq(family, "commp") || ieq(family, "common pixel");
}

void QualiaCanvas::set_font(const char* family, float size,
                            const char* weight) {
    _state.font_size = size;
    _state.font_bold = (weight && weight[0] == 'b');
    _state.font_commp = family_is_commp(family);
}

#if HAS_FONT_ATLAS

TextMetrics QualiaCanvas::measure_text(const char* text) {
    int target = (int)(_state.font_size + 0.5f);
    const FontAtlas* atlas = font_atlas_find_styled(_state.font_commp,
                                                    _state.font_bold, target);
    if (!atlas) {
        int len = 0; while (text[len]) len++;
        float scale = _state.font_size / 16.0f;
        float w = len * 8 * scale;
        float h = 16 * scale;
        return TextMetrics{w, h, h * 0.8f, h * 0.2f};
    }
    float scale = _state.font_size / (float)atlas->size;
    float width = 0;
    for (int i = 0; text[i]; i++) {
        const FontGlyph* g = font_atlas_glyph(atlas, (unsigned char)text[i]);
        if (g) width += g->advance * scale;
        else width += atlas->size * 0.5f * scale;
    }
    return TextMetrics{width, atlas->line_height * scale,
                       atlas->ascent * scale, atlas->descent * scale};
}

void QualiaCanvas::draw_text(const char* text, float x, float y,
                             const char* align) {
    if (!text || !text[0]) return;

    int target = (int)(_state.font_size + 0.5f);
    const FontAtlas* atlas = font_atlas_find_styled(_state.font_commp,
                                                    _state.font_bold, target);
    if (!atlas) return;

    float scale = _state.font_size / (float)atlas->size;
    TextMetrics m = measure_text(text);

    float draw_x = x;
    if (align[0] == 'c') draw_x = x - m.width / 2.0f;
    else if (align[0] == 'r') draw_x = x - m.width;
    float draw_y = y - m.ascent;

    uint8_t fr = _state.fill_r, fg = _state.fill_g;
    uint8_t fb = _state.fill_b, fa = _state.fill_a;

    bool xf = !_state.m.is_identity();
    Mat2D inv;
    if (xf && !_state.m.invert(inv)) return;

    for (int ci = 0; text[ci]; ci++) {
        const FontGlyph* glyph = font_atlas_glyph(atlas, (unsigned char)text[ci]);
        if (!glyph) {
            draw_x += atlas->size * 0.5f * scale;
            continue;
        }
        if (!glyph->bitmap) {
            draw_x += glyph->advance * scale;    /* space: real advance */
            continue;
        }

        int glyph_w = glyph->width;
        int glyph_h = glyph->height;
        float gx = draw_x + glyph->bearing_x * scale;
        float gy = draw_y + (atlas->ascent - glyph->bearing_y) * scale;
        float gw = glyph_w * scale;
        float gh = glyph_h * scale;

        if (!xf) {
            /* identity fast blit (band-tier algorithm, bandless) */
            int sw = (int)(gw + 0.5f);
            int sh = (int)(gh + 0.5f);
            int igx = (int)(gx + 0.5f);
            int igy = (int)(gy + 0.5f);
            for (int py = 0; py < sh; py++) {
                int src_row = py * glyph_h / sh;
                int screen_y = igy + py;
                if (screen_y < _limit_y0 || screen_y > _limit_y1) continue;
                for (int px = 0; px < sw; px++) {
                    int src_col = px * glyph_w / sw;
                    uint8_t alpha = glyph->bitmap[src_row * glyph_w + src_col];
                    if (alpha == 0) continue;
                    uint8_t final_a = (uint8_t)((alpha * fa + 127) / 255);
                    put_pixel(igx + px, screen_y, fr, fg, fb, final_a);
                }
            }
        } else {
            /* transformed: inverse-map the device bbox of the glyph
             * quad, bilinear-sample the A8 glyph bitmap */
            float bx0, by0, bx1, by1;
            xf_rect_bbox(_state.m, gx - 1, gy - 1, gw + 2, gh + 2,
                         bx0, by0, bx1, by1);
            int py0 = (int)floorf(by0), py1 = (int)ceilf(by1);
            int px0 = (int)floorf(bx0), px1 = (int)ceilf(bx1);
            if (py0 < _limit_y0) py0 = _limit_y0;
            if (py1 > _limit_y1) py1 = _limit_y1;
            if (px0 < 0) px0 = 0;
            if (px1 >= _width) px1 = _width - 1;

            for (int row = py0; row <= py1; row++) {
                for (int col = px0; col <= px1; col++) {
                    float lx, ly;
                    inv.apply((float)col + 0.5f, (float)row + 0.5f, lx, ly);
                    float u = (lx - gx) / scale;   /* glyph bitmap coords */
                    float v = (ly - gy) / scale;
                    if (u < -0.5f || v < -0.5f ||
                        u > glyph_w - 0.5f || v > glyph_h - 0.5f) continue;
                    /* bilinear sample */
                    float uf = u - 0.5f, vf = v - 0.5f;
                    int u0 = (int)floorf(uf), v0 = (int)floorf(vf);
                    float du = uf - u0, dv = vf - v0;
                    auto samp = [&](int uu, int vv) -> float {
                        if (uu < 0 || vv < 0 || uu >= glyph_w || vv >= glyph_h)
                            return 0.0f;
                        return (float)glyph->bitmap[vv * glyph_w + uu];
                    };
                    float a = samp(u0, v0) * (1 - du) * (1 - dv) +
                              samp(u0 + 1, v0) * du * (1 - dv) +
                              samp(u0, v0 + 1) * (1 - du) * dv +
                              samp(u0 + 1, v0 + 1) * du * dv;
                    if (a < 1.0f) continue;
                    uint8_t final_a = (uint8_t)((a * fa + 127.0f) / 255.0f);
                    if (final_a > 0) put_pixel(col, row, fr, fg, fb, final_a);
                }
            }
        }
        draw_x += glyph->advance * scale;
    }
}

#else  /* !HAS_FONT_ATLAS — measure-only stubs (atlas ships in-tree) */

TextMetrics QualiaCanvas::measure_text(const char* text) {
    int len = 0; while (text[len]) len++;
    float scale = _state.font_size / 16.0f;
    return TextMetrics{len * 8 * scale, 16 * scale,
                       12.8f * scale, 3.2f * scale};
}

void QualiaCanvas::draw_text(const char*, float, float, const char*) {}

#endif /* HAS_FONT_ATLAS */

/* ------------------------------------------------------------------ */
/* Images — BMP24/32 + CI16 (band-tier codecs, PSRAM-budget)           */
/* ------------------------------------------------------------------ */

/* PSRAM-backed decode budget: the Qualia has 8 MB of octal PSRAM, so
 * assets up to a full frame are fine (vs the CYD's 120 KB). */
static constexpr size_t kQMaxDecodedBytes = 2 * 1024 * 1024;

struct QImage {
    uint16_t w, h;
    uint16_t* pix565;      /* native-endian RGB565 */
    uint8_t* alpha;        /* nullable A8 */
};

static inline uint32_t q_le32(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline uint16_t q_le16(const uint8_t* p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static void* q_img_alloc(size_t n) {
    void* p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!p) p = malloc(n);
    return p;
}

ImageHandle QualiaCanvas::decode_image(const uint8_t* data, size_t len) {
    if (!data || len < 8) return nullptr;

    if (data[0] == 'C' && data[1] == 'I' && data[2] == '1' && data[3] == '6') {
        uint16_t w = q_le16(data + 4);
        uint16_t h = q_le16(data + 6);
        size_t need = (size_t)w * h * 2;
        if (w == 0 || h == 0 || len < 8 + need) return nullptr;
        if (need > kQMaxDecodedBytes) {
            ESP_LOGW(TAG, "decode_image: CI16 %ux%u exceeds budget", w, h);
            return nullptr;
        }
        auto* img = static_cast<QImage*>(malloc(sizeof(QImage)));
        if (!img) return nullptr;
        img->w = w; img->h = h; img->alpha = nullptr;
        img->pix565 = static_cast<uint16_t*>(q_img_alloc(need));
        if (!img->pix565) { free(img); return nullptr; }
        const uint8_t* src = data + 8;
        for (size_t i = 0; i < (size_t)w * h; i++)
            img->pix565[i] = q_le16(src + i * 2);
        return img;
    }

    if (data[0] == 'B' && data[1] == 'M' && len >= 54) {
        uint32_t px_off = q_le32(data + 10);
        uint32_t dib = q_le32(data + 14);
        if (dib < 40) return nullptr;
        int32_t w = (int32_t)q_le32(data + 18);
        int32_t h_signed = (int32_t)q_le32(data + 22);
        uint16_t bpp = q_le16(data + 28);
        uint32_t compression = q_le32(data + 30);
        bool top_down = h_signed < 0;
        int32_t h = top_down ? -h_signed : h_signed;

        if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return nullptr;
        if (bpp != 24 && bpp != 32) return nullptr;
        if (compression != 0 && compression != 3) return nullptr;

        size_t bytes_pp = bpp / 8;
        size_t stride = ((size_t)w * bytes_pp + 3) & ~3u;
        if (px_off + stride * h > len) return nullptr;

        size_t need = (size_t)w * h * 2 + (bpp == 32 ? (size_t)w * h : 0);
        if (need > kQMaxDecodedBytes) {
            ESP_LOGW(TAG, "decode_image: BMP %ldx%ld exceeds budget",
                     (long)w, (long)h);
            return nullptr;
        }

        auto* img = static_cast<QImage*>(malloc(sizeof(QImage)));
        if (!img) return nullptr;
        img->w = (uint16_t)w; img->h = (uint16_t)h;
        img->pix565 = static_cast<uint16_t*>(q_img_alloc((size_t)w * h * 2));
        img->alpha = (bpp == 32)
            ? static_cast<uint8_t*>(q_img_alloc((size_t)w * h)) : nullptr;
        if (!img->pix565 || (bpp == 32 && !img->alpha)) {
            if (img->pix565) heap_caps_free(img->pix565);
            if (img->alpha) heap_caps_free(img->alpha);
            free(img);
            return nullptr;
        }

        for (int32_t row = 0; row < h; row++) {
            int32_t src_row = top_down ? row : (h - 1 - row);
            const uint8_t* src = data + px_off + (size_t)src_row * stride;
            for (int32_t col = 0; col < w; col++) {
                const uint8_t* p = src + (size_t)col * bytes_pp;
                uint8_t b = p[0], g = p[1], r = p[2];
                size_t idx = (size_t)row * w + col;
                img->pix565[idx] = rgb565_native(r, g, b);
                if (img->alpha) img->alpha[idx] = p[3];
            }
        }
        return img;
    }

    return nullptr;
}

void QualiaCanvas::draw_image(ImageHandle handle, float x, float y,
                              float w, float h) {
    auto* img = static_cast<QImage*>(handle);
    if (!img || !img->pix565 || w <= 0 || h <= 0) return;

    bool xf = !_state.m.is_identity();
    Mat2D inv;
    if (xf && !_state.m.invert(inv)) return;

    float bx0, by0, bx1, by1;
    if (xf) {
        xf_rect_bbox(_state.m, x, y, w, h, bx0, by0, bx1, by1);
    } else {
        bx0 = x; by0 = y; bx1 = x + w; by1 = y + h;
    }
    int dy0 = (int)floorf(by0);
    int dy1 = (int)ceilf(by1) - 1;
    int dx0 = (int)floorf(bx0);
    int dx1 = (int)ceilf(bx1) - 1;
    if (dy0 < _limit_y0) dy0 = _limit_y0;
    if (dy1 > _limit_y1) dy1 = _limit_y1;
    if (dx0 < 0) dx0 = 0;
    if (dx1 >= _width) dx1 = _width - 1;

    for (int row = dy0; row <= dy1; row++) {
        for (int col = dx0; col <= dx1; col++) {
            float lx, ly;
            if (xf) inv.apply((float)col + 0.5f, (float)row + 0.5f, lx, ly);
            else { lx = (float)col + 0.5f; ly = (float)row + 0.5f; }

            int sx = (int)((lx - x) * img->w / w);
            int sy = (int)((ly - y) * img->h / h);
            if (sx < 0 || sy < 0 || sx >= img->w || sy >= img->h) continue;

            uint16_t c = img->pix565[(size_t)sy * img->w + sx];
            uint8_t a = img->alpha ? img->alpha[(size_t)sy * img->w + sx] : 255;
            if (a == 0) continue;
            uint8_t r, g, b;
            unpack565_native(c, r, g, b);
            put_pixel(col, row, r, g, b, a);
        }
    }
}

void QualiaCanvas::free_image(ImageHandle handle) {
    auto* img = static_cast<QImage*>(handle);
    if (!img) return;
    if (img->pix565) heap_caps_free(img->pix565);
    if (img->alpha) heap_caps_free(img->alpha);
    free(img);
}

/* ------------------------------------------------------------------ */
/* Panel bring-up: HSV color wheel (angle=hue, radius=saturation)      */
/* ------------------------------------------------------------------ */

static void hsv_to_rgb(float h, float s, float v,
                       uint8_t& r, uint8_t& g, uint8_t& b) {
    /* h in [0,360), s/v in [0,1] */
    float c = v * s;
    float hp = h / 60.0f;
    float x = c * (1.0f - fabsf(fmodf(hp, 2.0f) - 1.0f));
    float rf = 0, gf = 0, bf = 0;
    if (hp < 1)      { rf = c; gf = x; }
    else if (hp < 2) { rf = x; gf = c; }
    else if (hp < 3) { gf = c; bf = x; }
    else if (hp < 4) { gf = x; bf = c; }
    else if (hp < 5) { rf = x; bf = c; }
    else             { rf = c; bf = x; }
    float m = v - c;
    r = (uint8_t)((rf + m) * 255.0f + 0.5f);
    g = (uint8_t)((gf + m) * 255.0f + 0.5f);
    b = (uint8_t)((bf + m) * 255.0f + 0.5f);
}

void QualiaCanvas::draw_test_wheel() {
    if (!_fb) return;
    float cx = _width / 2.0f, cy = _height / 2.0f;
    float rad = (_width < _height ? _width : _height) / 2.0f;

    for (int y = 0; y < _height; y++) {
        int x0 = _round ? _span_x0[y] : 0;
        int x1 = _round ? _span_x1[y] : _width - 1;
        uint16_t* row = _fb + (size_t)y * _width;
        /* black off-glass corners */
        for (int x = 0; x < x0; x++) row[x] = 0;
        for (int x = x1 + 1; x < _width; x++) row[x] = 0;

        float dy = (float)y + 0.5f - cy;
        for (int x = x0; x <= x1; x++) {
            float dx = (float)x + 0.5f - cx;
            float dist = sqrtf(dx * dx + dy * dy);
            float hue = atan2f(dy, dx) * (180.0f / (float)M_PI) + 180.0f;
            if (hue >= 360.0f) hue = 0.0f;
            float sat = dist / rad;
            if (sat > 1.0f) sat = 1.0f;
            uint8_t r, g, b;
            hsv_to_rgb(hue, sat, 1.0f, r, g, b);
            row[x] = rgb565_native(r, g, b);
        }
    }
}
