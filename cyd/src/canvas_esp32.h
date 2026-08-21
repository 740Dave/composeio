/*
 * canvas_esp32.h — ESP32 Canvas backend with band rendering
 *
 * Phase 1b: replaces full framebuffer (150KB) with a 16-scanline
 * band buffer (7,680 bytes). Renders the 320-height scene in 20 passes.
 * Each band: clear, draw all widgets clipped to band Y range, DMA transfer.
 *
 * This reduces SRAM usage by 95% and enables WiFi coexistence in Phase 2.
 *
 * Spec: [ED-PHASE1], [CXC-R-DRAW-CONTRACT]
 */

#ifndef CANVAS_ESP32_H
#define CANVAS_ESP32_H

#include "canvas.h"
#include "board_profile.h"
#include <cstdint>

/* Graphics state stack (save/restore) */
static constexpr int kMaxStateDepth = 8;

/* Band rendering configuration.
 *
 * 16 scanlines is the NO-PSRAM floor (the classic CYD: 7,680 B of
 * DMA-capable SRAM, 20 passes over the scene per frame).  A board with
 * room for a whole frame widens this to the full panel height and gets
 * ONE pass — the scene is walked once instead of once per band, which
 * is where most of the win is; the SPI transfer costs the same either
 * way ([CXC-BRINGUP] the bands-or-framebuffer call).
 *
 * Set per build env (-DCXC_BAND_HEIGHT=...), never per widget: this is
 * the BACKEND widening to meet the contract, which is the sanctioned
 * direction across the narrow waist. */
#ifndef CXC_BAND_HEIGHT
#define CXC_BAND_HEIGHT 16
#endif
static constexpr int kBandHeight = CXC_BAND_HEIGHT;

/* Dirty QUANTUM — the granularity a widget-level repaint is tracked and
 * transferred at (rows per bit of the scene's 32-bit dirty mask,
 * reactive_apply.h).  DELIBERATELY DECOUPLED from kBandHeight:
 *
 *   band tier   (kBandHeight 16): quantum == band, and render_bands
 *                                 walks exactly those bands — the
 *                                 classic CYD path, unchanged.
 *   frame tier  (kBandHeight 320): the buffer is the WHOLE FRAME and it
 *                                 PERSISTS between frames, so a repaint
 *                                 does not need a band at all — it
 *                                 re-rasterises the dirty ROW SPAN in
 *                                 place at its true offsets and
 *                                 transfers only those rows.  Without
 *                                 this the quantum would be the whole
 *                                 frame and every bus arrival would cost
 *                                 a full 153,600-byte SPI transfer.
 *
 * Constraint: ceil(panel_height / kDirtyBandHeight) <= 32 (mask width).
 * 320/16 = 20 bands — fits. */
#ifndef CXC_DIRTY_BAND_HEIGHT
#define CXC_DIRTY_BAND_HEIGHT 16
#endif
static constexpr int kDirtyBandHeight = CXC_DIRTY_BAND_HEIGHT;

/* Gradient types */
enum class GradientType : uint8_t {
    None,
    Linear,
    Radial
};

/* Saved graphics state */
struct GraphicsState {
    /* Fill color */
    uint8_t fill_r, fill_g, fill_b, fill_a;

    /* Stroke */
    uint8_t stroke_r, stroke_g, stroke_b, stroke_a;
    float stroke_width;

    /* Clip region */
    float clip_x, clip_y, clip_w, clip_h;
    bool has_clip;
    /* Rounded-clip corner radius (0 == rectangular clip; > 0 selects the
     * rounded-corner SDF test in put_pixel/fill_hline).  Set only by
     * clip_round_rect; plain clip_rect resets it to 0. */
    float clip_radius;

    /* Gradient state */
    GradientType grad_type;
    float grad_cx, grad_cy, grad_r;         /* radial center + radius */
    float grad_x1, grad_y1, grad_x2, grad_y2; /* linear endpoints */
    GradientStop grad_stops[kMaxGradientStops];
    int grad_stop_count;

    /* Shadow state */
    bool has_shadow;
    float shadow_ox, shadow_oy, shadow_blur;
    uint8_t shadow_r, shadow_g, shadow_b, shadow_a;

    /* Font state */
    float font_size;
    bool font_bold;
    bool font_commp;                     /* COMMP ("Common Pixel") family */

    /* Global opacity multiplier (set_opacity; 0.0-1.0).  Applied to
     * every pixel write — landed with the M5 draw_image/alpha pass. */
    float opacity;
};

/* Decoded image storage (the concrete ImageHandle on this backend).
 * RGB565 native-endian + optional A8 plane — 2-3 B/px keeps decoded
 * assets inside the no-PSRAM heap budget (vs 4 B/px RGBA8888). */
struct DecodedImage {
    uint16_t w;
    uint16_t h;
    uint16_t* pix565;   /* native-endian RGB565, w*h */
    uint8_t* alpha;     /* nullable A8 plane, w*h */
};


/* Forward declaration for widget list used by render_frame */
class Widget;
struct Rect;

class ESP32Canvas : public Canvas {
public:
    ESP32Canvas() = default;
    ~ESP32Canvas();

    ESP32Canvas(const ESP32Canvas&) = delete;
    ESP32Canvas& operator=(const ESP32Canvas&) = delete;

    /**
     * Attach the board's display driver for band transfer (any
     * DisplayDriver from the board profile — the canvas is
     * board-agnostic).  Must be called before render_frame().
     */
    void set_display(DisplayDriver* display) { _display = display; }

    /* -- Canvas interface implementation ---------------------------  */

    void create_surface(int width, int height) override;
    void present() override;
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
    /* Contract transform ops ([CXC-R-DRAW] 2026-07-06): documented
     * no-op stubs on this tier — a band-renderer raster transform is
     * M5+ work; transform-bound widgets draw untransformed (CYD is
     * POC-priority).  Declared so the C++ contract stays in lockstep
     * with the Python ABC (the decode_image precedent). */
    void translate(float dx, float dy) override;
    void rotate(float angle) override;
    void scale(float sx, float sy) override;
    void clip_rect(float x, float y, float w, float h) override;
    void clip_round_rect(float x, float y, float w, float h,
                         float radius) override;

    void set_font(const char* family, float size, const char* weight = "regular") override;
    void draw_text(const char* text, float x, float y, const char* align = "left") override;
    TextMetrics measure_text(const char* text) override;

    /* Image pipeline (real since M5): BMP24/BMP32/CI16 -> DecodedImage
     * (RGB565 + optional A8).  PNG/JPEG stay undecodable on this tier
     * (offline bakers emit BMP/CI16 for embedded); undecodable bytes
     * return nullptr and callers render the documented fallback. */
    ImageHandle decode_image(const uint8_t* data, size_t len) override;
    void draw_image(ImageHandle handle, float x, float y,
                    float w, float h) override;
    void free_image(ImageHandle handle) override;

    /* -- Band rendering API --------------------------------------- */

    /**
     * Render a full frame using band rendering.
     * Calls the provided draw callback for each band.
     * The callback should draw ALL widgets — the canvas handles
     * clipping to the current band automatically.
     *
     * Usage:
     *   canvas.render_frame([](Canvas& c) {
     *       // draw all widgets here
     *   });
     */
    typedef void (*DrawCallback)(Canvas& canvas, void* user_data);
    void render_frame(DrawCallback draw_fn, void* user_data);

    /**
     * Render ONLY bands [band_first, band_last] (inclusive band
     * indices; band N covers scanlines N*kDirtyBandHeight ..).  The
     * dirty re-render path ([CXC-ER-RENDER]): a changed widget
     * attribute repaints just the band(s) the widget occupies.  The
     * callback always draws the WHOLE scene; the canvas gates every
     * write to the requested rows.
     *
     * Band tier:  walks those bands, one scene pass each (unchanged).
     * Frame tier: ONE scene pass with writes gated to the row span,
     *             re-rasterised in place into the persistent frame
     *             buffer, then only those rows are transferred.
     */
    void render_bands(DrawCallback draw_fn, void* user_data,
                      int band_first, int band_last);

    /**
     * Repaint an arbitrary inclusive scanline span [y0, y1] and
     * transfer exactly those rows.  render_frame/render_bands are the
     * two callers; exposed because a span is the honest currency of a
     * dirty repaint (a band is just a quantised span).
     *
     * bottom_up walks the bands from y1 DOWN to y0 instead of up.
     *
     * ⚠ THIS IS PRESENTATION, NOT CORRECTNESS, and it exists because a
     * band-tier repaint is not atomic.  Measured on the C3/ST7796: a
     * 0 -> 255 thrust jump is 206 rows in 15 separate band transfers
     * over 230 ms (draw 70 + xfer 160), and 160.5 ms of that matches
     * the 0.778 ms/row wire model to within 0.3 ms — so the wavefront
     * is WIRE-BOUND and cannot be hidden by merging transfers (measured
     * per-transfer overhead: 20-76 us, 0.18% of the transfer).  What
     * CAN be fixed is which way it sweeps: an ascending walk made a
     * bottom-filling LED bar appear to light downward.  Each band still
     * sets its own address window, so the pixels are identical and the
     * row-CRC audit (keyed by scanline) is unaffected.
     */
    void render_rows(DrawCallback draw_fn, void* user_data,
                     int y0, int y1, bool bottom_up = false);

    /* ---- Damage audit ([CXC-ER-DAMAGE]) --------------------------
     *
     * THE INSTRUMENT FOR UNDER-INVALIDATION, and the reason it is an
     * instrument at all: it does NOT ask the dirty tracker anything.
     *
     * Every row this canvas hands to the display driver is CRC'd into
     * a per-scanline table.  audit_rows() then re-renders the WHOLE
     * frame from current scene state, CRCs each row, and compares.  A
     * mismatch means the panel is holding a row that a full repaint
     * would draw differently — i.e. a stale row: exactly the artifact
     * a missed dirty span produces, localised to the scanline.
     *
     * Ask what it shows if the thing is broken: a divergent row index.
     * Ask what it shows if the thing is fine: 0.  The two answers
     * differ, which is what a re-rendering /capture.bmp cannot manage
     * on this tier (it decodes the same buffer it just drew).
     *
     * WHAT IT STILL CANNOT CATCH, stated plainly:
     *   - anything below the driver handoff (a dropped SPI transfer, a
     *     bad address window, a dead panel).  It proves the pixels we
     *     SENT are right, never that they arrived.
     *   - a partial-WIDTH transfer.  The table is per-row and assumes
     *     every transfer covers the full scanline; column-windowed
     *     transfers would make a row's stored CRC a fiction.  That is
     *     the standing reason transfers here stay full-width.
     *   - a rasteriser bug that is identical in both passes.
     *
     * Compiled only under CXC_DAMAGE_AUDIT (1.3 KB of RAM on a
     * 320-row panel plus a CRC per transferred row).
     */
#ifdef CXC_DAMAGE_AUDIT
    /* Re-render the frame and compare every row against the CRC of
     * what was last SENT.  Transfers NOTHING (the panel is untouched).
     * @param first_bad  set to the lowest divergent scanline, or -1.
     * @return number of divergent scanlines (0 == clean). */
    int audit_rows(DrawCallback draw_fn, void* user_data, int* first_bad);
#endif

    /* Rows actually transferred by the last render_rows/render_bands/
     * render_frame call — the instrumentation seam for [PERF]. */
    int last_rows_painted() const { return _last_rows; }
    /* Microseconds spent rasterising / transferring in that call. */
    int64_t last_draw_us() const { return _last_draw_us; }
    int64_t last_xfer_us() const { return _last_xfer_us; }

    /* True when the buffer holds the whole frame and therefore
     * persists between renders (the full-framebuffer tier). */
    bool full_frame() const { return _full_frame; }

    /* Current band Y offset — used internally for coordinate translation */
    int band_y_offset() const { return _band_y; }

    /* Direct band buffer access (for testing / capture) */
    uint16_t* band_buffer() const { return _band_buf; }
    int width() const { return _width; }
    int height() const { return _height; }

private:
    /* Band buffer — 240 * 16 * 2 = 7,680 bytes in DMA-capable SRAM */
    uint16_t* _band_buf = nullptr;
    int _width = 0;
    int _height = 0;

    /* Current band being rendered */
    int _band_y = 0;   /* Scene Y that maps to buffer row 0 */

    /* ACTIVE ROW WINDOW (inclusive, scene coordinates) — every write is
     * gated to it.  On the band tier it is exactly the current band, so
     * nothing changes.  On the frame tier it is the dirty span, which is
     * how sub-frame repaint granularity survives a one-band frame
     * (the QualiaCanvas _limit_y0/_limit_y1 idiom, ported). */
    int _row_lo = 0;
    int _row_hi = 0;

    /* Buffer holds the whole frame (kBandHeight >= panel height) — set
     * once in create_surface. */
    bool _full_frame = false;

#ifdef CXC_DAMAGE_AUDIT
    /* Per-scanline CRC of what was last SENT to the panel (see
     * audit_rows).  height entries, allocated in create_surface. */
    uint32_t* _row_crc = nullptr;
    /* CRC one full scanline of the band buffer (row is scene Y). */
    uint32_t row_crc(int y) const;
#endif

    /* [PERF] instrumentation of the last render call. */
    int _last_rows = 0;
    int64_t _last_draw_us = 0;
    int64_t _last_xfer_us = 0;

    /* Display driver (board-profile seam) */
    DisplayDriver* _display = nullptr;

    /* Current graphics state */
    GraphicsState _state = {};
    GraphicsState _state_stack[kMaxStateDepth] = {};
    int _state_depth = 0;

    /* -- Internal helpers ------------------------------------------ */

    /* Convert RGBA to RGB565 big-endian (ILI9341 byte order) */
    static uint16_t rgb565_be(uint8_t r, uint8_t g, uint8_t b);

    /* Alpha blend and write a pixel into the band buffer.
     * x, y are in SCENE coordinates — the method offsets by _band_y.
     * Skips pixels outside the current band. */
    void put_pixel(int x, int y, uint8_t r, uint8_t g, uint8_t b, uint8_t a);

    /* Check if a scene-coordinate Y is inside the ACTIVE ROW WINDOW.
     * (On the band tier the window IS the band — identical semantics to
     * the pre-dirty-span `y >= _band_y && y < _band_y + kBandHeight`.) */
    inline bool in_band(int y) const {
        return y >= _row_lo && y <= _row_hi;
    }

    /* Rounded-clip inclusion test (only consulted when clip_radius > 0):
     * standard rounded-rect SDF — clamp the pixel center to the box
     * between the four corner-arc centers; if it lands in a corner
     * region its distance to the nearest arc center must be <= radius. */
    inline bool in_clip_round(int x, int y) const {
        float rr = _state.clip_radius;
        float cx0 = _state.clip_x + rr;
        float cx1 = _state.clip_x + _state.clip_w - rr;
        float cy0 = _state.clip_y + rr;
        float cy1 = _state.clip_y + _state.clip_h - rr;
        float px = (float)x + 0.5f, py = (float)y + 0.5f;
        float tx = px < cx0 ? cx0 : (px > cx1 ? cx1 : px);
        float ty = py < cy0 ? cy0 : (py > cy1 ? cy1 : py);
        float dx = px - tx, dy = py - ty;
        return dx * dx + dy * dy <= rr * rr;
    }

    /* Resolve fill color at a pixel position (handles gradient) */
    void resolve_fill_color(float px, float py,
                            uint8_t& r, uint8_t& g, uint8_t& b, uint8_t& a) const;

    /* Filled horizontal line in scene coordinates (clipped to band) */
    void fill_hline(int x0, int x1, int y,
                    uint8_t r, uint8_t g, uint8_t b, uint8_t a);
    void fill_hline_gradient(int x0, int x1, int y);

    /* Clear the band buffer to a color */
    void clear_band(uint8_t r, uint8_t g, uint8_t b);

    /* Reset the graphics state to render-pass defaults (one place, so
     * the band-walk and row-span passes cannot drift apart). */
    void reset_draw_state();

    /* Stamp a filled disc at (cx, cy) with the given radius and color.
     * Used by stroke_arc / stroke_circle / stroke_round_rect to produce
     * uniform-thickness stroked curves (round-cap stroking). */
    void stamp_stroke_disc(int cx, int cy, int r,
                           uint8_t cr, uint8_t cg, uint8_t cb, uint8_t ca);
};

#endif /* CANVAS_ESP32_H */
