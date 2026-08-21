/*
 * canvas_qualia.h — Canvas contract on a full PSRAM framebuffer
 * [SPEC:CXC-EMBED-REACTIVE] R5, [CXC-ER-RENDER], [CXC-R-DRAW-CONTRACT]
 *
 * The framebuffer sibling of ESP32Canvas (canvas_esp32.h).  Same
 * ~30-function contract, same widgets — a genuinely different Layer-1:
 *
 *   - The whole 720x720 frame lives in octal PSRAM (RGB565 native-
 *     endian, ~1 MB) instead of a 7.6 KB SRAM band.  Compositing is
 *     full-frame; present()/render_bands() flush rows to the RGB
 *     panel via the board profile's transfer_band seam.
 *   - LIVE TRANSFORMS ARE REAL ([CXC-ER-C6]): translate/rotate/scale
 *     maintain an affine matrix (xform2d.h) applied to every draw op.
 *     Filled prims use inverse-mapped signed-distance coverage (AA);
 *     text glyphs and images are inverse-mapped and resampled; lines
 *     and paths transform their geometry.  These are the exact ops
 *     that are documented no-ops on the CYD band tier.
 *   - ROUND CLIP ([CXC-RM-M5] round-displays, consumed here): when the
 *     panel shape is Round, every pixel write is clipped to the
 *     inscribed circle via a per-row span table; the off-glass corners
 *     stay black.
 *
 * Dirty-region contract: kBandHeight below is the dirty QUANTUM the
 * scene's 32-bit band mask is computed against (reactive_apply.h).
 * 32 rows x 32 bits covers the full 720-row panel (23 bands used).
 * NOTE: the name intentionally matches canvas_esp32.h so the shared
 * compose_scene.cpp compiles against either tier — exactly one of the
 * two canvas headers is included per build.
 */

#ifndef CANVAS_QUALIA_H
#define CANVAS_QUALIA_H

#include "canvas.h"
#include "board_profile.h"
#include "xform2d.h"
#include <cstdint>

static constexpr int kBandHeight = 32;      /* dirty quantum (rows/bit) */
/* Shared name with canvas_esp32.h, where the buffer height and the
 * dirty quantum are DIFFERENT constants on the full-frame tier.  On
 * this tier the framebuffer is always whole, so the quantum is the
 * only band concept there is. */
static constexpr int kDirtyBandHeight = kBandHeight;
static constexpr int kMaxStateDepth = 8;

/* This tier renders transforms for real — compose_scene keys the
 * rotation/scale plumbing off this (the band tier leaves it out so
 * its honest-refusal contract is preserved). */
#define RENDER_TIER_TRANSFORMS 1

enum class QGradType : uint8_t { None, Linear, Radial };

struct QualiaState {
    uint8_t fill_r, fill_g, fill_b, fill_a;
    uint8_t stroke_r, stroke_g, stroke_b, stroke_a;
    float stroke_width;

    bool has_clip;                       /* device-space rect clip */
    float clip_x, clip_y, clip_w, clip_h;

    QGradType grad_type;
    float grad_cx, grad_cy, grad_r;
    float grad_x1, grad_y1, grad_x2, grad_y2;
    GradientStop grad_stops[kMaxGradientStops];
    int grad_stop_count;

    bool has_shadow;
    float shadow_ox, shadow_oy, shadow_blur;
    uint8_t shadow_r, shadow_g, shadow_b, shadow_a;

    float font_size;
    bool font_bold;
    bool font_commp;                     /* COMMP ("Common Pixel") family */

    float opacity;

    Mat2D m;                             /* current affine transform */
};

class QualiaCanvas : public Canvas {
public:
    QualiaCanvas() = default;
    ~QualiaCanvas();

    QualiaCanvas(const QualiaCanvas&) = delete;
    QualiaCanvas& operator=(const QualiaCanvas&) = delete;

    /** Board-profile display seam.  transfer_band on this tier takes
     * NATIVE-endian RGB565 rows (the RGB peripheral's memory format —
     * unlike the ILI9341's big-endian SPI stream). */
    void set_display(DisplayDriver* display) { _display = display; }

    /** Round-panel clip (call before create_surface; from the board
     * profile's PanelGeometry.shape). */
    void set_shape(PanelShape shape) { _round = (shape == PanelShape::Round); }

    /* -- Canvas contract ------------------------------------------- */

    void create_surface(int width, int height) override;
    void present() override;                 /* flush full frame */
    Size get_surface_size() override;

    void fill_rect(float x, float y, float w, float h) override;
    void stroke_rect(float x, float y, float w, float h) override;
    void fill_circle(float cx, float cy, float r) override;
    void stroke_circle(float cx, float cy, float r) override;
    void fill_round_rect(float x, float y, float w, float h,
                         float radius) override;
    void stroke_round_rect(float x, float y, float w, float h,
                           float radius) override;
    void stroke_arc(float cx, float cy, float r,
                    float start_angle, float end_angle) override;
    void fill_arc(float cx, float cy, float r,
                  float start_angle, float end_angle) override;
    void draw_line(float x1, float y1, float x2, float y2) override;
    void draw_path(const PathCommand* cmds, int count) override;
    void fill_path(const PathCommand* cmds, int count) override;

    void set_fill_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 255) override;
    void set_stroke_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 255) override;
    void set_stroke_width(float w) override;
    void set_linear_gradient(float x1, float y1, float x2, float y2,
                             const GradientStop* stops, int count) override;
    void set_radial_gradient(float cx, float cy, float radius,
                             const GradientStop* stops, int count) override;
    void set_shadow(float offset_x, float offset_y, float blur,
                    uint8_t r, uint8_t g, uint8_t b, uint8_t a) override;
    void clear_shadow() override;
    void set_opacity(float a) override;

    void save() override;
    void restore() override;
    /* REAL on this tier ([CXC-ER-C6]). */
    void translate(float dx, float dy) override;
    void rotate(float angle) override;
    void scale(float sx, float sy) override;
    void clip_rect(float x, float y, float w, float h) override;

    void set_font(const char* family, float size,
                  const char* weight = "regular") override;
    void draw_text(const char* text, float x, float y,
                   const char* align = "left") override;
    TextMetrics measure_text(const char* text) override;

    ImageHandle decode_image(const uint8_t* data, size_t len) override;
    void draw_image(ImageHandle handle, float x, float y,
                    float w, float h) override;
    void free_image(ImageHandle handle) override;

    /* -- Frame API (main-loop/capture surface, ESP32Canvas idiom) --- */

    typedef void (*DrawCallback)(Canvas& canvas, void* user_data);

    /** Composite the whole scene into the framebuffer and flush it. */
    void render_frame(DrawCallback draw_fn, void* user_data);

    /** Dirty repaint: restrict writes to rows of bands
     * [band_first, band_last] (kBandHeight rows per band), composite,
     * flush ONLY those rows ([CXC-ER-C5] — the changed region). */
    void render_bands(DrawCallback draw_fn, void* user_data,
                      int band_first, int band_last);

    /* -- 3-buffer radar path (R5 animated-scene perf) ----------------
     *
     * When the display exposes TWO scan-out framebuffers (zero_copy()),
     * the canvas's own PSRAM buffer becomes the BACKGROUND SNAPSHOT
     * (grid + instruments, no beam) and animated frames run:
     *   memcpy(back scan fb <- background)  ~sequential PSRAM restore
     *   draw_fn (beam only) into back fb    ~small area
     *   present by pointer swap             ~no copy
     * The background is (re)built via render_background_bands ONLY on
     * scene load or a reactive dirty mask — not per frame. */

    /** True when the display provides 2 scan-out fbs (pointer swap). */
    bool zero_copy() const { return _zero_copy; }

    /** Composite into the BACKGROUND buffer only — same row-window
     * semantics as render_bands, but NO flush (the animated frame
     * carries it to the glass).  Caller sets the sweep render phase
     * (GridOnly) before calling. */
    void render_background_bands(DrawCallback draw_fn, void* user_data,
                                 int band_first, int band_last);

    /** One animated frame: restore background into the back scan fb,
     * run draw_fn (beam-only pass) on top, swap by pointer.  Falls back
     * to render_frame when !zero_copy(). */
    void render_animated(DrawCallback draw_fn, void* user_data);

    /** PANEL BRING-UP PATTERN (David's requested validation frame):
     * a full-face HSV color wheel — angle -> hue, radius -> saturation
     * — round-clipped to the inscribed circle.  Immediately reveals
     * RGB channel order, geometry/centering, orientation, and tearing
     * on the physical glass.  Direct framebuffer write (bring-up
     * utility, not a contract op); call present() after. */
    void draw_test_wheel();

    /** The live framebuffer (native-endian RGB565, width*height) —
     * the capture server streams straight from it.  On the animated
     * zero-copy path this is the most recently PRESENTED scan fb
     * (background + beam); otherwise the canvas compose buffer. */
    const uint16_t* framebuffer() const {
        return _present_fb ? _present_fb : _fb;
    }
    int width() const { return _width; }
    int height() const { return _height; }

private:
    uint16_t* _fb = nullptr;            /* ACTIVE draw target */
    uint16_t* _own_fb = nullptr;        /* canvas-owned PSRAM buffer
                                           (== background snapshot on
                                           the zero-copy path) */
    uint16_t* _scan_fb[2] = {nullptr, nullptr};  /* driver scan-out fbs */
    int _scan_front = 0;                /* index currently presented */
    bool _zero_copy = false;
    const uint16_t* _present_fb = nullptr;  /* last presented scan fb */
    int _width = 0;
    int _height = 0;

    bool _round = false;
    int16_t* _span_x0 = nullptr;        /* per-row round-clip spans */
    int16_t* _span_x1 = nullptr;

    /* Active row window (render_bands dirty restriction). */
    int _limit_y0 = 0;
    int _limit_y1 = 0;

    DisplayDriver* _display = nullptr;

    QualiaState _state = {};
    QualiaState _state_stack[kMaxStateDepth] = {};
    int _state_depth = 0;

    void reset_state();

    /* -- pixel core -------------------------------------------------- */
    void put_pixel(int x, int y, uint8_t r, uint8_t g, uint8_t b, uint8_t a);
    void fill_hline(int x0, int x1, int y,
                    uint8_t r, uint8_t g, uint8_t b, uint8_t a);
    void fill_hline_gradient(int x0, int x1, int y);
    void resolve_fill_color(float px, float py, uint8_t& r, uint8_t& g,
                            uint8_t& b, uint8_t& a) const;

    /* -- identity fast paths (band-renderer algorithms, bandless) ---- */
    void fill_rect_axis(float x, float y, float w, float h);
    void fill_circle_axis(float cx, float cy, float r);
    void fill_round_rect_axis(float x, float y, float w, float h, float r);

    /* -- transformed field fills (inverse-mapped SDF coverage) ------- */
    enum class Field : uint8_t { Rect, RRect, Circle };
    void fill_field_xf(Field kind, float x, float y, float w, float h,
                       float radius);

    void stamp_stroke_disc(int cx, int cy, int r,
                           uint8_t cr, uint8_t cg, uint8_t cb, uint8_t ca);
    void draw_line_device(float x1, float y1, float x2, float y2,
                          float width_px);

    void flush_rows(int y0, int y1);
};

#endif /* CANVAS_QUALIA_H */
