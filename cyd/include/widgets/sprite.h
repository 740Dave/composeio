/*
 * sprite.h — Sprite-sequence widget (M5, [CXC-R-C18])
 *
 * Plays a pre-baked frame sequence (CI16/BMP assets from
 * bus://assets/animations/<name>/frame_<i>.*) via draw_image.  This is
 * the constrained-tier Lottie degradation: the SAME authored Lottie is
 * baked offline by scripts/bake_lottie_sprite.py through the ratified
 * evaluator; no animation evaluation runs on-device.
 *
 * Playhead = wall-clock frame index (esp_timer).  NOTE: unlike the
 * Skia tier's clocked-widget registry, this tier has no deterministic
 * render_frame_at(t) sampling — captures of animated scenes grab
 * whatever frame is current (documented; the pixel gate applies to the
 * baked frames themselves, which the desktop evaluator golden-tests).
 */

#ifndef WIDGETS_SPRITE_H
#define WIDGETS_SPRITE_H

#include "widget.h"

static constexpr int kMaxSpriteFrames = 16;

class SpriteWidget : public Widget {
public:
    SpriteWidget(const char* id, float fps = 8.0f);

    /**
     * Latch the playhead clock — call ONCE per render pass (main loop),
     * NOT per band: the band renderer invokes draw() once per band and
     * an unlatched clock would tear the sprite across band boundaries.
     */
    static void tick();

    /** Frame handles are owned by the scene (freed on reload). */
    void add_frame(ImageHandle h);
    int frame_count() const { return _count; }
    ImageHandle frame_handle(int i) const {
        return (i >= 0 && i < _count) ? _frames[i] : nullptr;
    }

    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    ImageHandle _frames[kMaxSpriteFrames] = {};
    int _count = 0;
    float _fps;
};

#endif /* WIDGETS_SPRITE_H */
