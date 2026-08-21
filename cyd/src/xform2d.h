/*
 * xform2d.h — pure 2D affine transform + coverage geometry for the
 * framebuffer render tier ([SPEC:CXC-EMBED-REACTIVE] R5, [CXC-ER-RENDER]
 * live transforms / [CXC-R-DRAW] rotate/scale/translate).
 *
 * PURE C++: no ESP-IDF, no canvas, no widgets — just math.  Host-tested
 * by firmware/reactive_core env:native_test (which already -I's cyd/src).
 *
 * Conventions:
 *   - Column-vector affine: x' = a*x + c*y + e ; y' = b*x + d*y + f
 *   - Screen space: Y down, angles in radians, positive = clockwise
 *     (matches the Canvas contract / Python engine).
 *   - "Coverage" values are 0..1 pixel-area approximations from signed
 *     distance: cov = clamp(0.5 + d, 0, 1) with d in device pixels,
 *     positive inside — the same AA class the band renderer uses.
 */

#ifndef XFORM2D_H
#define XFORM2D_H

#include <cmath>

struct Mat2D {
    float a = 1, b = 0, c = 0, d = 1, e = 0, f = 0;

    static Mat2D identity() { return Mat2D{}; }

    bool is_identity() const {
        return a == 1 && b == 0 && c == 0 && d == 1 && e == 0 && f == 0;
    }

    /* this ∘ translate/rotate/scale — post-multiply, i.e. the new op
     * applies FIRST to incoming local coordinates (canvas semantics:
     * canvas.translate(); canvas.rotate(); draw(local)). */
    void translate(float dx, float dy) {
        e += a * dx + c * dy;
        f += b * dx + d * dy;
    }
    void rotate(float angle) {
        float co = cosf(angle), si = sinf(angle);
        float na = a * co + c * si;
        float nb = b * co + d * si;
        float nc = -a * si + c * co;
        float nd = -b * si + d * co;
        a = na; b = nb; c = nc; d = nd;
    }
    void scale(float sx, float sy) {
        a *= sx; b *= sx;
        c *= sy; d *= sy;
    }

    void apply(float x, float y, float& ox, float& oy) const {
        ox = a * x + c * y + e;
        oy = b * x + d * y + f;
    }

    /* Inverse (returns false when singular — degenerate scale 0). */
    bool invert(Mat2D& out) const {
        float det = a * d - b * c;
        if (fabsf(det) < 1e-9f) return false;
        float inv = 1.0f / det;
        out.a = d * inv;
        out.b = -b * inv;
        out.c = -c * inv;
        out.d = a * inv;
        out.e = (c * f - d * e) * inv;
        out.f = (b * e - a * f) * inv;
        return true;
    }

    /* Device-pixel lengths of the transformed unit axes — how much one
     * local unit measures on screen along local X / local Y. */
    float scale_x() const { return sqrtf(a * a + b * b); }
    float scale_y() const { return sqrtf(c * c + d * d); }
    float scale_avg() const { return 0.5f * (scale_x() + scale_y()); }

    /* The rotation the local +X axis lands at (for arc angle windows
     * under a similarity transform). */
    float rotation() const { return atan2f(b, a); }
};

/* Device-space AABB of a transformed local rect (4 corners). */
inline void xf_rect_bbox(const Mat2D& m, float x, float y, float w, float h,
                         float& min_x, float& min_y,
                         float& max_x, float& max_y) {
    float px[4], py[4];
    m.apply(x, y, px[0], py[0]);
    m.apply(x + w, y, px[1], py[1]);
    m.apply(x, y + h, px[2], py[2]);
    m.apply(x + w, y + h, px[3], py[3]);
    min_x = max_x = px[0];
    min_y = max_y = py[0];
    for (int i = 1; i < 4; i++) {
        if (px[i] < min_x) min_x = px[i];
        if (px[i] > max_x) max_x = px[i];
        if (py[i] < min_y) min_y = py[i];
        if (py[i] > max_y) max_y = py[i];
    }
}

/* Coverage clamp helper. */
inline float xf_cov(float signed_dist_px) {
    float c = 0.5f + signed_dist_px;
    if (c < 0.0f) return 0.0f;
    if (c > 1.0f) return 1.0f;
    return c;
}

/* Signed LOCAL-space distance (positive inside) from point to rect
 * boundary — min over the four edges (interior distance; outside
 * points get a negative axis-wise distance, exact for edge regions,
 * conservative at corners). */
inline float xf_rect_sdist(float lx, float ly,
                           float x, float y, float w, float h) {
    float dl = lx - x;
    float dr = (x + w) - lx;
    float dt = ly - y;
    float db = (y + h) - ly;
    float dx = dl < dr ? dl : dr;
    float dy = dt < db ? dt : db;
    if (dx >= 0 && dy >= 0) return dx < dy ? dx : dy;    /* inside */
    if (dx < 0 && dy < 0) return -sqrtf(dx * dx + dy * dy); /* corner out */
    return dx < dy ? dx : dy;                            /* edge out */
}

/* Signed local distance for a round rect (radius r clamped by caller). */
inline float xf_rrect_sdist(float lx, float ly,
                            float x, float y, float w, float h, float r) {
    /* Shrink to the inner rect, distance to it minus -r offset. */
    float cx0 = x + r, cy0 = y + r, cx1 = x + w - r, cy1 = y + h - r;
    float qx = lx < cx0 ? cx0 - lx : (lx > cx1 ? lx - cx1 : 0.0f);
    float qy = ly < cy0 ? cy0 - ly : (ly > cy1 ? ly - cy1 : 0.0f);
    float outside = sqrtf(qx * qx + qy * qy);
    if (outside > 0.0f) return r - outside;   /* corner-region field */
    /* Fully inside the inner rect: plain rect distance + r. */
    return xf_rect_sdist(lx, ly, x + r, y + r, w - 2 * r, h - 2 * r) + r;
}

/* Signed local distance for a circle (positive inside). */
inline float xf_circle_sdist(float lx, float ly,
                             float cx, float cy, float r) {
    float dx = lx - cx, dy = ly - cy;
    return r - sqrtf(dx * dx + dy * dy);
}

/* ------------------------------------------------------------------ */
/* Round-panel clip ([CXC-RM-M5] round-displays, consumed in R5)       */
/* ------------------------------------------------------------------ */

/* Inclusive X span [x0, x1] of the inscribed circle on scanline *row*
 * (pixel centers at +0.5).  Returns false when the row is entirely
 * off-glass (x0 > x1 also signals empty). */
inline bool xf_round_span(int row, float cx, float cy, float r, int width,
                          int& x0, int& x1) {
    float dy = (float)row + 0.5f - cy;
    float under = r * r - dy * dy;
    if (under <= 0.0f) { x0 = 1; x1 = 0; return false; }
    float span = sqrtf(under);
    x0 = (int)ceilf(cx - span - 0.5f);
    x1 = (int)floorf(cx + span - 0.5f);
    if (x0 < 0) x0 = 0;
    if (x1 >= width) x1 = width - 1;
    if (x0 > x1) { x0 = 1; x1 = 0; return false; }
    return true;
}

/* ------------------------------------------------------------------ */
/* Transformed dirty span ([CXC-ER-RENDER] widget-level dirty)         */
/* ------------------------------------------------------------------ */

/* Vertical span [y, y+h) a widget rect can touch under rotation about
 * its center with uniform scale *s* — the bounding circle of the rect,
 * angle-independent so one span covers before AND after a live angle
 * change. */
inline void xf_transformed_vspan(float x, float y, float w, float h,
                                 float s, float& out_y, float& out_h) {
    (void)x;
    float half_diag = 0.5f * sqrtf(w * w + h * h) * (s < 0 ? -s : s);
    float cy = y + h * 0.5f;
    out_y = cy - half_diag - 1.0f;      /* 1px AA guard */
    out_h = 2.0f * half_diag + 2.0f;
}

#endif /* XFORM2D_H */
