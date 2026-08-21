/*
 * qualia_panel.h — THE PANEL CONSTANTS BLOCK for the Qualia RGB666 tier
 * [SPEC:CXC-EMBED-REACTIVE] R5, [CXC-ER-BOARDS] / [CXC-ER-RENDER]
 *
 * =====================================================================
 *  EVERY panel-specific number lives HERE and ONLY here.  If the
 *  physical panel needs tuning (timings, porches, pclk, init bytes,
 *  geometry), this file is the one place to change — the renderer,
 *  scene builder, and capture server are all parameter-driven.
 * =====================================================================
 *
 * Board:  Adafruit Qualia ESP32-S3 RGB666 (product 5800)
 * Panel:  Adafruit product 5793 — Round RGB TTL TFT, 4.0", 720x720,
 *         NV3052C driver, NO touchscreen.  CONFIRMED BY DAVID
 *         (2026-07-10) as the attached panel.
 *
 * Sources (mirrored EXACTLY, not guessed):
 *  - NV3052C init sequence + RGB timings: Adafruit_CircuitPython_Qualia
 *    adafruit_qualia/displays/round40.py (the driver for PID 5793) —
 *    the byte array below is machine-extracted from that file.
 *  - Pin map + IO-expander protocol: CircuitPython
 *    ports/espressif/boards/adafruit_qualia_s3_rgb666/pins.c and
 *    shared-module/dotclockframebuffer/__init__.c.
 *
 * DISPATCHER/DAVID-VERIFY: the numbers are the published values for
 * this exact panel, but only eyes on the glass confirm geometry/
 * colors/stability.  Retune = edit constants here, rebuild.
 */

#ifndef QUALIA_PANEL_H
#define QUALIA_PANEL_H

#include <cstdint>
#include <cstddef>

/* ----- Geometry ----------------------------------------------------- */

static constexpr int kQualiaWidth  = 720;
static constexpr int kQualiaHeight = 720;
static constexpr bool kQualiaRound = true;    /* visible area = inscribed
                                                 circle c=(360,360) r=360 */

/* ----- RGB dot-clock timings (round40.py _timings, verbatim) -------- */

static constexpr int kQualiaPclkHz          = 10000000;  /* 16M->10M: horizontal-flicker fix — PSRAM headroom per line, ~15Hz refresh (David 2026-07-10) */
static constexpr int kQualiaHsyncPulseWidth = 2;
static constexpr int kQualiaHsyncBackPorch  = 44;
static constexpr int kQualiaHsyncFrontPorch = 46;
static constexpr int kQualiaVsyncPulseWidth = 16;
static constexpr int kQualiaVsyncBackPorch  = 16;
static constexpr int kQualiaVsyncFrontPorch = 50;
/* round40: hsync_idle_low=False, vsync_idle_low=False, de_idle_high=
 * False, pclk_active_high=True, pclk_idle_high=False.
 * esp_lcd flag mapping (CircuitPython DotClockFramebuffer.c): */
static constexpr bool kQualiaHsyncIdleLow   = false;
static constexpr bool kQualiaVsyncIdleLow   = false;
static constexpr bool kQualiaDeIdleHigh     = false;
static constexpr bool kQualiaPclkActiveNeg  = false;  /* = !pclk_active_high */
static constexpr bool kQualiaPclkIdleHigh   = false;

/* Bounce buffer, in PIXELS.  0 = DMA straight from the PSRAM
 * framebuffer (Adafruit's proven configuration for this exact
 * board+panel).  If the dispatcher sees drift/tearing under WiFi
 * load, set e.g. 720*16 and rebuild — the driver switches to two
 * SRAM bounce buffers automatically. */
static constexpr size_t kQualiaBounceBufferPx = 720 * 20; /* 20-line SRAM
   bounce buffer (2x ~29KB).  Fixes the intermittent BLACK FLASH (bounce=0 ->
   DMA reads PSRAM at line rate -> a bandwidth spike underruns the FIFO ->
   RESTART_IN_VSYNC blanks a frame) AND the intermittent few-pixel VERTICAL
   JUMP (David 2026-07-10): the refill ISR (non-IRAM, see below) is briefly
   suspended whenever a critical section holds off interrupts — notably the
   1 Hz uxTaskGetSystemState CPU-load sampler — and if the refill falls a few
   lines behind, the frame rides up until it re-locks.  10 lines (~0.8 ms
   slack) was borderline against that; 20 lines (~1.6 ms) rides it out.
   REQUIRES CONFIG_LCD_RGB_ISR_IRAM_SAFE=n (the Cache-error panic was
   IRAM_SAFE=y + PSRAM-fb refill, documented-incompatible) — see
   sdkconfig.qualia.defaults.  720*20 divides 720*720 evenly (=36). */

/* ----- ESP32-S3 pin map (qualia_s3_rgb666 pins.c, verbatim) --------- */

static constexpr int kQualiaPinDE    = 2;
static constexpr int kQualiaPinVSYNC = 42;
static constexpr int kQualiaPinHSYNC = 41;
static constexpr int kQualiaPinPCLK  = 1;

/* esp_lcd data_gpio_nums order is B0..B4, G0..G5, R0..R4 (16-bit
 * RGB565 on the wire; the '666' panel's LSBs are tied per Adafruit). */
static constexpr int kQualiaPinsData[16] = {
    40, 39, 38, 0, 45,          /* B0..B4 */
    48, 47, 21, 14, 13, 12,     /* G0..G5 */
    11, 10, 9, 46, 3,           /* R0..R4 */
};

/* I2C bus to the PCA9554 IO expander (also the touch bus on panels
 * that have touch — 5793 has none). */
static constexpr int kQualiaPinSDA = 8;
static constexpr int kQualiaPinSCL = 18;
static constexpr uint32_t kQualiaExpanderI2CHz = 100000;  /* Adafruit default */

/* ----- PCA9554 IO expander (TFT_IO_EXPANDER dict, verbatim) --------- */

static constexpr uint8_t kQualiaExpanderAddr    = 0x3F;
static constexpr uint8_t kQualiaExpanderGpioReg = 1;     /* output register  */
static constexpr uint8_t kQualiaExpanderGpioData = 0xFD; /* base output bits */
static constexpr int kQualiaExpBitSCK      = 0;
static constexpr int kQualiaExpBitCS       = 1;
static constexpr int kQualiaExpBitReset    = 2;
static constexpr int kQualiaExpBitBacklite = 4;
static constexpr int kQualiaExpBitMOSI     = 7;

/* Expander bring-up writes ({len, bytes...} framing, verbatim from
 * the board's i2c_init_sequence): direction reg 3 = 0x78 (SCK/CS/
 * RESET/MOSI outputs, rest inputs), polarity reg 2 = 0. */
static constexpr uint8_t kQualiaExpanderI2CInit[] = {
    2, 3, 0x78,
    2, 2, 0x00,
};

/* ----- NV3052C init sequence ----------------------------------------
 *
 * displayio framing: {cmd, len|0x80, params..., [delay_ms]} — bit 7 of
 * the length byte means one delay byte follows the params.  Sent as
 * 9-bit SPI (DC bit + 8 data bits, MSB first) bit-banged through the
 * PCA9554 (CPOL=CPHA=0, CS asserted per step).
 *
 * MACHINE-EXTRACTED from adafruit_qualia/displays/round40.py
 * (156 steps, 470 bytes; ends 0x36=0x0A MADCTL, 0x11 sleep-out
 * +200 ms, 0x29 display-on +100 ms).  Do not hand-edit values —
 * regenerate from the Adafruit source if the panel revision changes.
 * ------------------------------------------------------------------ */

static constexpr uint8_t kQualiaNV3052CInit[] = {
    0xFF, 0x01, 0x30,
    0xFF, 0x01, 0x52,
    0xFF, 0x01, 0x01,
    0xE3, 0x01, 0x00,
    0x0A, 0x01, 0x11,
    0x23, 0x01, 0xA0,
    0x24, 0x01, 0x32,
    0x25, 0x01, 0x12,
    0x26, 0x01, 0x2E,
    0x27, 0x01, 0x2E,
    0x29, 0x01, 0x02,
    0x2A, 0x01, 0xCF,
    0x32, 0x01, 0x34,
    0x38, 0x01, 0x9C,
    0x39, 0x01, 0xA7,
    0x3A, 0x01, 0x27,
    0x3B, 0x01, 0x94,
    0x42, 0x01, 0x6D,
    0x43, 0x01, 0x83,
    0x81, 0x01, 0x00,
    0x91, 0x01, 0x67,
    0x92, 0x01, 0x67,
    0xA0, 0x01, 0x52,
    0xA1, 0x01, 0x50,
    0xA4, 0x01, 0x9C,
    0xA7, 0x01, 0x02,
    0xA8, 0x01, 0x02,
    0xA9, 0x01, 0x02,
    0xAA, 0x01, 0xA8,
    0xAB, 0x01, 0x28,
    0xAE, 0x01, 0xD2,
    0xAF, 0x01, 0x02,
    0xB0, 0x01, 0xD2,
    0xB2, 0x01, 0x26,
    0xB3, 0x01, 0x26,
    0xFF, 0x01, 0x30,
    0xFF, 0x01, 0x52,
    0xFF, 0x01, 0x02,
    0xB1, 0x01, 0x0A,
    0xD1, 0x01, 0x0E,
    0xB4, 0x01, 0x2F,
    0xD4, 0x01, 0x2D,
    0xB2, 0x01, 0x0C,
    0xD2, 0x01, 0x0C,
    0xB3, 0x01, 0x30,
    0xD3, 0x01, 0x2A,
    0xB6, 0x01, 0x1E,
    0xD6, 0x01, 0x16,
    0xB7, 0x01, 0x3B,
    0xD7, 0x01, 0x35,
    0xC1, 0x01, 0x08,
    0xE1, 0x01, 0x08,
    0xB8, 0x01, 0x0D,
    0xD8, 0x01, 0x0D,
    0xB9, 0x01, 0x05,
    0xD9, 0x01, 0x05,
    0xBD, 0x01, 0x15,
    0xDD, 0x01, 0x15,
    0xBC, 0x01, 0x13,
    0xDC, 0x01, 0x13,
    0xBB, 0x01, 0x12,
    0xDB, 0x01, 0x10,
    0xBA, 0x01, 0x11,
    0xDA, 0x01, 0x11,
    0xBE, 0x01, 0x17,
    0xDE, 0x01, 0x17,
    0xBF, 0x01, 0x0F,
    0xDF, 0x01, 0x0F,
    0xC0, 0x01, 0x16,
    0xE0, 0x01, 0x16,
    0xB5, 0x01, 0x2E,
    0xD5, 0x01, 0x3F,
    0xB0, 0x01, 0x03,
    0xD0, 0x01, 0x02,
    0xFF, 0x01, 0x30,
    0xFF, 0x01, 0x52,
    0xFF, 0x01, 0x03,
    0x08, 0x01, 0x09,
    0x09, 0x01, 0x0A,
    0x0A, 0x01, 0x0B,
    0x0B, 0x01, 0x0C,
    0x28, 0x01, 0x22,
    0x2A, 0x01, 0xE9,
    0x2B, 0x01, 0xE9,
    0x34, 0x01, 0x51,
    0x35, 0x01, 0x01,
    0x36, 0x01, 0x26,
    0x37, 0x01, 0x13,
    0x40, 0x01, 0x07,
    0x41, 0x01, 0x08,
    0x42, 0x01, 0x09,
    0x43, 0x01, 0x0A,
    0x44, 0x01, 0x22,
    0x45, 0x01, 0xDB,
    0x46, 0x01, 0xDC,
    0x47, 0x01, 0x22,
    0x48, 0x01, 0xDD,
    0x49, 0x01, 0xDE,
    0x50, 0x01, 0x0B,
    0x51, 0x01, 0x0C,
    0x52, 0x01, 0x0D,
    0x53, 0x01, 0x0E,
    0x54, 0x01, 0x22,
    0x55, 0x01, 0xDF,
    0x56, 0x01, 0xE0,
    0x57, 0x01, 0x22,
    0x58, 0x01, 0xE1,
    0x59, 0x01, 0xE2,
    0x80, 0x01, 0x1E,
    0x81, 0x01, 0x1E,
    0x82, 0x01, 0x1F,
    0x83, 0x01, 0x1F,
    0x84, 0x01, 0x05,
    0x85, 0x01, 0x0A,
    0x86, 0x01, 0x0A,
    0x87, 0x01, 0x0C,
    0x88, 0x01, 0x0C,
    0x89, 0x01, 0x0E,
    0x8A, 0x01, 0x0E,
    0x8B, 0x01, 0x10,
    0x8C, 0x01, 0x10,
    0x8D, 0x01, 0x00,
    0x8E, 0x01, 0x00,
    0x8F, 0x01, 0x1F,
    0x90, 0x01, 0x1F,
    0x91, 0x01, 0x1E,
    0x92, 0x01, 0x1E,
    0x93, 0x01, 0x02,
    0x94, 0x01, 0x04,
    0x96, 0x01, 0x1E,
    0x97, 0x01, 0x1E,
    0x98, 0x01, 0x1F,
    0x99, 0x01, 0x1F,
    0x9A, 0x01, 0x05,
    0x9B, 0x01, 0x09,
    0x9C, 0x01, 0x09,
    0x9D, 0x01, 0x0B,
    0x9E, 0x01, 0x0B,
    0x9F, 0x01, 0x0D,
    0xA0, 0x01, 0x0D,
    0xA1, 0x01, 0x0F,
    0xA2, 0x01, 0x0F,
    0xA3, 0x01, 0x00,
    0xA4, 0x01, 0x00,
    0xA5, 0x01, 0x1F,
    0xA6, 0x01, 0x1F,
    0xA7, 0x01, 0x1E,
    0xA8, 0x01, 0x1E,
    0xA9, 0x01, 0x01,
    0xAA, 0x01, 0x03,
    0xFF, 0x01, 0x30,
    0xFF, 0x01, 0x52,
    0xFF, 0x01, 0x00,
    0x36, 0x01, 0x0A,
    0x11, 0x81, 0x00, 0xC8,
    0x29, 0x81, 0x00, 0x64,
};

#endif /* QUALIA_PANEL_H */
