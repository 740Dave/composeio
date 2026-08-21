/*
 * sweep.h — DRADIS radar-sweep widget (R5 live-transform showcase)
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-C6]
 *
 * A continuously rotating radar sweep with a fading trail over a faint
 * radar grid.  The rotation is driven through the Canvas TRANSFORM ops
 * (save/translate/rotate/restore) — the exact ops that are documented
 * no-ops on the CYD band tier and REAL on the Qualia framebuffer tier.
 * This is the reference widget for the cross-tier animation story: the
 * same declared widget renders live here and degrades to a static
 * wedge on a tier without transforms.
 *
 * Playhead: wall clock latched ONCE per render pass via tick() (the
 * SpriteWidget idiom).  The sweep angle INTEGRATES speed*dt, so a
 * bus-bound speed change modulates smoothly instead of jumping phase.
 *
 * .compose.yaml dialect (compose_scene):
 *   type: sweep
 *   period_s: 4.0          # seconds per revolution at speed 1.0
 *   grid: true             # faint rings + cross
 *   color: "#22FF88"       # sweep color
 *   speed: {namespace: dradis/speed, default: 1}   # bindable multiplier
 */

#ifndef WIDGETS_SWEEP_H
#define WIDGETS_SWEEP_H

#include "widget.h"
#include <cstdint>

/* Render phase for the 3-buffer radar path (R5 perf): the compositor
 * builds the BACKGROUND snapshot with sweeps in GridOnly, then draws
 * ONLY the rotating beam per animated frame (BeamOnly).  Full is the
 * classic single-pass behavior (and the safe default). */
enum class SweepRenderPhase : uint8_t { Full, GridOnly, BeamOnly };

class SweepWidget : public Widget {
public:
    SweepWidget(const char* id, float period_s = 4.0f, bool grid = true);

    /** Latch the clock — once per render pass, never per band. */
    static void tick();

    /** Select which layer(s) draw() renders (process-wide, like
     * tick()) — set by the compositor around each pass. */
    static void set_render_phase(SweepRenderPhase phase);

    /** Current phase (read by per-frame overlay widgets — e.g.
     * dradis_contacts skips contacts in the GridOnly background pass
     * and bakes its static range rings ONLY into it).  Compositor
     * plumbing, not behavior coupling: the beam no longer drives any
     * contact behavior (ping-on-sweep-pass removed 2026-07-16). */
    static SweepRenderPhase render_phase();

    void set_speed(float multiplier) { _speed = multiplier; }
    void set_period(float period_s) { _period_s = period_s > 0.01f ? period_s : 0.01f; }
    void set_color(uint8_t r, uint8_t g, uint8_t b) {
        _r = r; _g = g; _b = b;
    }

    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    float _period_s;
    bool _grid;
    float _speed = 1.0f;
    uint8_t _r = 0x22, _g = 0xFF, _b = 0x88;

    /* integrated phase (radians) + last integration timestamp */
    float _theta = 0.0f;
    int64_t _last_ms = -1;
};

#endif /* WIDGETS_SWEEP_H */
