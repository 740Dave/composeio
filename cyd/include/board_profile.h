/*
 * board_profile.h — The board-profile seam for the embedded tier
 *
 * Spec: [CXC-RM-M5] "a new board = a new profile, not a new rasterizer".
 *
 * Everything board-specific — display controller, touch controller,
 * pins, panel geometry INCLUDING shape — lives behind this interface.
 * The generic firmware (main.cpp), the band-rendering canvas, the
 * config-driven scene builder, and the touch dispatcher only ever see:
 *
 *   BoardProfile
 *     ├─ PanelGeometry   width/height + shape (rect | round)
 *     ├─ DisplayDriver   init / transfer_band / set_backlight
 *     └─ TouchDriver     init / read() -> calibrated TouchPoint (or null)
 *
 * Exactly ONE profile is linked into a firmware build (compile-time
 * selection, the ESPHome model): the profile translation unit defines
 * board_profile().  Classic CYD (ESP32 + ILI9341 240x320 rect +
 * XPT2046) is the first profile — boards/classic_cyd.cpp.  A future
 * Qualia S3 + round GC9A01/ST7701 board adds boards/qualia_s3.cpp with
 * shape=Round and a full-framebuffer-capable driver; nothing above
 * this header changes.
 *
 * Round displays: shape is carried here so the engine/canvas can clip
 * to the panel and offer round-friendly layouts ([CXC-RM-M5]
 * round-displays). The classic profile is Rect; the canvas does not
 * yet consume shape (no round board exists) — the seam is the point.
 */

#ifndef BOARD_PROFILE_H
#define BOARD_PROFILE_H

#include <cstdint>

/* ----- Panel geometry --------------------------------------------- */

enum class PanelShape : uint8_t {
    Rect,
    Round,      /* circular active area; width == height == diameter */
};

struct PanelGeometry {
    int width;
    int height;
    PanelShape shape;
};

/* ----- Display driver seam ----------------------------------------- */

class DisplayDriver {
public:
    virtual ~DisplayDriver() = default;

    /** Bring up the panel (bus, init sequence, orientation, backlight). */
    virtual void init() = 0;

    /**
     * Transfer a horizontal band of RGB565(BE) pixels to the panel.
     * @param band_buf  width * (y_end - y_start + 1) pixels
     * @param y_start   first scanline (inclusive)
     * @param y_end     last scanline (inclusive)
     */
    virtual void transfer_band(const uint16_t* band_buf,
                               uint16_t y_start, uint16_t y_end) = 0;

    /** Backlight brightness 0-255 (no-op if the board has none). */
    virtual void set_backlight(uint8_t brightness) = 0;

    /* ----- Zero-copy scan-out seam (R5 3-buffer radar path) ---------
     *
     * Drivers whose panel continuously scans driver-owned framebuffers
     * (esp_lcd RGB dot-clock) can expose them so the canvas composites
     * DIRECTLY into the back buffer and presents by POINTER SWAP — no
     * PSRAM->PSRAM copy.  Band/SPI drivers keep the defaults and the
     * canvas falls back to transfer_band. */

    /** Fill out[0..max-1] with the driver's scan-out framebuffers
     * (native-endian RGB565, width*height each).  Returns how many the
     * driver exposes; < 2 means no zero-copy swap is available. */
    virtual int scanout_framebuffers(uint16_t** out, int max) {
        (void)out; (void)max;
        return 0;
    }

    /** Present one of the scanout framebuffers by pointer (no copy;
     * latches at the panel's next frame start).  Returns false when
     * unsupported or fb is not a scanout framebuffer. */
    virtual bool present_scanout(uint16_t* fb) {
        (void)fb;
        return false;
    }
};

/* ----- Touch driver seam -------------------------------------------
 *
 * read() returns CALIBRATED screen coordinates — the profile owns the
 * raw-ADC-to-pixel mapping (axis swap/invert + cal range), because
 * that mapping is a board property, not an engine property. */

struct TouchPoint {
    float x;            /* screen px, valid when touched */
    float y;
    uint16_t pressure;  /* driver-specific units, 0 when not touched */
    bool touched;
};

class TouchDriver {
public:
    virtual ~TouchDriver() = default;
    virtual void init() = 0;
    virtual TouchPoint read() = 0;
};

/* ----- The profile -------------------------------------------------- */

class BoardProfile {
public:
    virtual ~BoardProfile() = default;

    virtual const char* name() const = 0;
    virtual PanelGeometry geometry() const = 0;
    virtual DisplayDriver& display() = 0;

    /** nullptr when the board has no touch input. */
    virtual TouchDriver* touch() = 0;
};

/**
 * The one profile linked into this firmware.  Defined by the selected
 * boards/<name>.cpp translation unit.
 */
BoardProfile& board_profile();

#endif /* BOARD_PROFILE_H */
