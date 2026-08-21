/*
 * sweep.cpp — DRADIS radar-sweep widget.  See include/widgets/sweep.h.
 *
 * Drawing strategy (framebuffer-tier perf): the trail is a fan of
 * chord-approximated wedge polygons via fill_path (cost ~ wedge area,
 * bbox-limited by the scanline filler), NOT per-segment fill_arc
 * (whose inverse-mapped field pass would test the whole disc bbox per
 * segment).  All sweep geometry is authored at LOCAL angle 0 and
 * rotated by the live phase through canvas.rotate() — the transform IS
 * the animation ([CXC-ER-C6]).
 */

#include "widgets/sweep.h"

#include <cmath>

#include "esp_timer.h"

/* Clock latched once per render pass (SpriteWidget idiom). */
static int64_t s_latched_ms = 0;

void SweepWidget::tick() {
    s_latched_ms = esp_timer_get_time() / 1000;
}

/* Render phase (3-buffer radar path) — Full unless the compositor is
 * building the background snapshot (GridOnly) or drawing the per-frame
 * beam pass (BeamOnly). */
static SweepRenderPhase s_phase = SweepRenderPhase::Full;

void SweepWidget::set_render_phase(SweepRenderPhase phase) {
    s_phase = phase;
}

SweepRenderPhase SweepWidget::render_phase() {
    return s_phase;
}

SweepWidget::SweepWidget(const char* id, float period_s, bool grid)
    : Widget(id, "sweep"),
      _period_s(period_s > 0.01f ? period_s : 0.01f),
      _grid(grid) {}

Size SweepWidget::measure(const Constraints& c) {
    return Size{c.max_width, c.max_height};
}

void SweepWidget::draw(Canvas& canvas, const Rect& rect) {
    float cx = rect.x + rect.w / 2.0f;
    float cy = rect.y + rect.h / 2.0f;
    float r = (rect.w < rect.h ? rect.w : rect.h) / 2.0f - 2.0f;
    if (r < 8.0f) return;

    /* Integrate phase: theta += omega * dt (smooth under live speed
     * modulation).  dt comes from the pass-latched clock. */
    if (_last_ms >= 0 && s_latched_ms > _last_ms) {
        float dt = (float)(s_latched_ms - _last_ms) / 1000.0f;
        _theta += (2.0f * (float)M_PI / _period_s) * _speed * dt;
        const float two_pi = 2.0f * (float)M_PI;
        while (_theta >= two_pi) _theta -= two_pi;
        while (_theta < 0.0f) _theta += two_pi;
    }
    _last_ms = s_latched_ms;

    /* ---- radar grid (unrotated) — BACKGROUND layer --------------- */
    if (_grid && s_phase != SweepRenderPhase::BeamOnly) {
        canvas.set_stroke_width(1.0f);
        canvas.set_stroke_color(_r, _g, _b, 44);
        canvas.stroke_circle(cx, cy, r);
        canvas.stroke_circle(cx, cy, r * 2.0f / 3.0f);
        canvas.stroke_circle(cx, cy, r / 3.0f);
        canvas.set_stroke_color(_r, _g, _b, 28);
        canvas.draw_line(cx - r, cy, cx + r, cy);
        canvas.draw_line(cx, cy - r, cx, cy + r);
        canvas.set_fill_color(_r, _g, _b, 90);
        canvas.fill_circle(cx, cy, 3.0f);
    }

    /* ---- rotating sweep: geometry at local angle 0, live rotate ---
     * PER-FRAME layer — skipped while the background snapshot is
     * being built (GridOnly). */
    if (s_phase == SweepRenderPhase::GridOnly) return;

    canvas.save();
    canvas.translate(cx, cy);
    canvas.rotate(_theta);

    /* Fading trail: wedge polygons trailing the head (negative local
     * angles = behind the clockwise-rotating head). */
    static constexpr int kTrailSegs = 22;
    static constexpr float kSegRad = 6.5f * (float)M_PI / 180.0f;
    for (int k = 0; k < kTrailSegs; k++) {
        float a0 = -(float)(k + 1) * kSegRad;
        float a1 = -(float)k * kSegRad;
        float decay = 1.0f - (float)k / kTrailSegs;
        uint8_t alpha = (uint8_t)(120.0f * decay * decay + 0.5f);
        if (alpha == 0) continue;

        PathCommand wedge[5];
        float mid = (a0 + a1) * 0.5f;
        wedge[0] = PathCommand::move(0, 0);
        wedge[1] = PathCommand::line(cosf(a1) * r, sinf(a1) * r);
        wedge[2] = PathCommand::line(cosf(mid) * r, sinf(mid) * r);
        wedge[3] = PathCommand::line(cosf(a0) * r, sinf(a0) * r);
        wedge[4] = PathCommand::close();

        canvas.set_fill_color(_r, _g, _b, alpha);
        canvas.fill_path(wedge, 5);
    }

    /* Head: bright leading line + tip highlight. */
    canvas.set_stroke_color(_r, _g, _b, 255);
    canvas.set_stroke_width(3.0f);
    canvas.draw_line(0, 0, r, 0);
    canvas.set_fill_color(255, 255, 255, 220);
    canvas.fill_circle(r - 4.0f, 0, 3.0f);

    canvas.restore();
}
