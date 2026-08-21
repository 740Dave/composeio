/*
 * canvas.h — Platform-independent Canvas drawing contract (C++)
 *
 * Spec: [CXC-R-DRAW-CONTRACT]
 *
 * Port of the Python Canvas ABC from composeio/render/canvas.py.
 * Phase 1b: expanded contract for gauge, label, rectangle, circle widgets.
 *
 * All coordinates in logical pixels. Colors in RGB565 for ESP32
 * backend efficiency, but the interface uses RGBA 0-255 for
 * platform independence — the backend converts internally.
 */

#ifndef CANVAS_H
#define CANVAS_H

#include <cstdint>
#include <cstddef>

/* ----- Value types ------------------------------------------------ */

struct Size {
    float width;
    float height;
};

struct TextMetrics {
    float width;
    float height;
    float ascent;
    float descent;
};

struct GradientStop {
    float offset;       /* 0.0 - 1.0 */
    uint8_t r, g, b, a;

    GradientStop() : offset(0), r(0), g(0), b(0), a(255) {}
    GradientStop(float off, uint8_t r_, uint8_t g_, uint8_t b_, uint8_t a_ = 255)
        : offset(off), r(r_), g(g_), b(b_), a(a_) {}
};

/* Maximum gradient stops per call */
static constexpr int kMaxGradientStops = 8;

/* Opaque backend image handle (returned by decode_image).
 * The concrete backend defines what it points at (decoded pixel
 * buffer, skia.Image, …).  nullptr == not decodable. */
typedef void* ImageHandle;

/* Path command ([CXC-R-DRAW-CONTRACT] draw_path/fill_path; port of the
 * Python PathCommand).  (x, y) is the end point; Quad uses (cx1, cy1);
 * Cubic uses both control points; Close ignores all coordinates. */
struct PathCommand {
    enum class Op : uint8_t { Move, Line, Quad, Cubic, Close };
    Op op;
    float x = 0, y = 0;
    float cx1 = 0, cy1 = 0;
    float cx2 = 0, cy2 = 0;

    static PathCommand move(float x, float y) {
        PathCommand c; c.op = Op::Move; c.x = x; c.y = y; return c;
    }
    static PathCommand line(float x, float y) {
        PathCommand c; c.op = Op::Line; c.x = x; c.y = y; return c;
    }
    static PathCommand quad(float cx, float cy, float x, float y) {
        PathCommand c; c.op = Op::Quad; c.cx1 = cx; c.cy1 = cy;
        c.x = x; c.y = y; return c;
    }
    static PathCommand cubic(float c1x, float c1y, float c2x, float c2y,
                             float x, float y) {
        PathCommand c; c.op = Op::Cubic; c.cx1 = c1x; c.cy1 = c1y;
        c.cx2 = c2x; c.cy2 = c2y; c.x = x; c.y = y; return c;
    }
    static PathCommand close() {
        PathCommand c; c.op = Op::Close; return c;
    }
};

/* ----- Abstract Canvas interface ---------------------------------- */

class Canvas {
public:
    virtual ~Canvas() = default;

    /* -- Geometry -------------------------------------------------- */

    virtual void fill_rect(float x, float y, float w, float h) = 0;
    virtual void stroke_rect(float x, float y, float w, float h) = 0;
    virtual void fill_circle(float cx, float cy, float r) = 0;
    virtual void stroke_circle(float cx, float cy, float r) = 0;

    /* Rounded rectangles */
    virtual void fill_round_rect(float x, float y, float w, float h,
                                 float radius) = 0;
    virtual void stroke_round_rect(float x, float y, float w, float h,
                                   float radius) = 0;

    /* Arcs — angles in radians.  fill_arc fills the pie sector
     * ([CXC-R-DRAW-CONTRACT]; landed on this tier in M5). */
    virtual void stroke_arc(float cx, float cy, float r,
                            float start_angle, float end_angle) = 0;
    virtual void fill_arc(float cx, float cy, float r,
                          float start_angle, float end_angle) = 0;

    /* Line */
    virtual void draw_line(float x1, float y1, float x2, float y2) = 0;

    /* Paths ([CXC-R-DRAW-CONTRACT]; landed on this tier in M5).
     * fill_path uses even-odd winding across subpaths. */
    virtual void draw_path(const PathCommand* cmds, int count) = 0;
    virtual void fill_path(const PathCommand* cmds, int count) = 0;

    /* -- Style State ----------------------------------------------- */

    virtual void set_fill_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 255) = 0;
    virtual void set_stroke_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 255) = 0;
    virtual void set_stroke_width(float w) = 0;

    virtual void set_linear_gradient(float x1, float y1, float x2, float y2,
                                     const GradientStop* stops, int count) = 0;
    virtual void set_radial_gradient(float cx, float cy, float radius,
                                     const GradientStop* stops, int count) = 0;

    virtual void set_shadow(float offset_x, float offset_y,
                            float blur,
                            uint8_t r, uint8_t g, uint8_t b, uint8_t a) = 0;
    virtual void clear_shadow() = 0;

    /* Global opacity multiplier 0.0-1.0 (contract style-state op;
     * declared 2026-07-06 with the transform ops below — see the
     * [CXC-R-DRAW] change record in 12-composeux-render-engine.lts). */
    virtual void set_opacity(float a) = 0;

    /* -- Transform & Clip ------------------------------------------
     *
     * translate/rotate/scale were declared here 2026-07-06 for the
     * [SPEC:CXC-BIND] bindable-transform pass (rotation/scale widget
     * attributes; the dradis sweep).  They were ALWAYS part of the
     * normative [CXC-R-DRAW-CONTRACT] table and the Python ABC — this
     * closes the C++ port's lockstep gap, following the decode_image
     * precedent: the contract defines the SEAM, not the feature
     * inventory.  ESP32Canvas stubs them as documented no-ops (a
     * band-renderer raster transform is M5+ work; transform-bound
     * widgets degrade to their untransformed draw on this tier —
     * CYD is POC-priority per the roadmap). Angle in radians. */

    virtual void save() = 0;
    virtual void restore() = 0;
    virtual void translate(float dx, float dy) = 0;
    virtual void rotate(float angle) = 0;
    virtual void scale(float sx, float sy) = 0;
    virtual void clip_rect(float x, float y, float w, float h) = 0;

    /* Rounded-rectangle clip ([CXC-R-DRAW-CONTRACT]; Python ABC parity —
     * canvas.py clip_round_rect).  The DEFAULT falls back to the
     * axis-aligned clip_rect (square corners); a backend with a
     * rounded-clip raster overrides it (ESP32Canvas does — the band
     * renderer's rounded-corner SDF test).  This closes the C++/Python
     * contract lockstep gap the gauge `bar`/fader fill requires to round
     * the fill's bottom corners against the track channel — same seam-
     * completion precedent as decode_image / the transform ops. */
    virtual void clip_round_rect(float x, float y, float w, float h,
                                 float radius) { (void)radius; clip_rect(x, y, w, h); }

    /* -- Text ------------------------------------------------------ */

    virtual void set_font(const char* family, float size, const char* weight = "regular") = 0;
    virtual void draw_text(const char* text, float x, float y, const char* align = "left") = 0;
    virtual TextMetrics measure_text(const char* text) = 0;

    /* -- Image ------------------------------------------------------
     *
     * decode_image was promoted to the contract 2026-07-06 (see the
     * [CXC-R-DRAW] change record in 12-composeux-render-engine.lts):
     * the platform-independent AssetManager fetches bytes itself and
     * needs the Layer-1 codec without a URI.  Returns nullptr when
     * the bytes are not decodable — callers render a fallback.
     * (draw_image/load_image remain M5 work on this backend; this is
     * the codec seam only, kept in lockstep with the Python ABC.)
     */

    virtual ImageHandle decode_image(const uint8_t* data, size_t len) = 0;

    /* Draw a decoded image scaled to the destination rect
     * ([CXC-R-DRAW-CONTRACT] draw_image; landed on this tier in M5 —
     * nearest-neighbor scaling, per-pixel alpha honored). */
    virtual void draw_image(ImageHandle handle, float x, float y,
                            float w, float h) = 0;

    /* Release a decode_image handle (C++-only lifecycle op; Python
     * backends garbage-collect).  Safe on nullptr. */
    virtual void free_image(ImageHandle handle) = 0;

    /* -- Surface --------------------------------------------------- */

    virtual void create_surface(int width, int height) = 0;
    virtual void present() = 0;
    virtual Size get_surface_size() = 0;
};

#endif /* CANVAS_H */
