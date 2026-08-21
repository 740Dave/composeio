/*
 * waveshare_c6_panel.h — THE PANEL CONSTANTS BLOCK for the Waveshare
 * ESP32-C6-LCD-1.47 tier ([CXC-BRINGUP], [CXC-ER-BOARDS], [CXC-RM-M5]).
 *
 * =====================================================================
 *  EVERY board-specific number lives HERE and ONLY here — pins, SPI
 *  clock, the ST7789T init byte table, the GRAM column offset, and
 *  backlight polarity.  Retune this file, rebuild.
 * =====================================================================
 *
 * Board:  Waveshare ESP32-C6-LCD-1.47
 *         ESP32-C6FH4 (QFN32) rev v0.1 — RISC-V, SINGLE core + LP core,
 *         160 MHz, 4 MB embedded flash, Wi-Fi 6 / BT5 LE / 802.15.4.
 *         NO PSRAM.
 * Panel:  1.47" 172x320 IPS, ST7789T over SPI.  NO TOUCH.
 *
 * Sources (transcribed from the vendor kit, not guessed):
 *   ~/Dropbox/Data Sheets/waveshare/esp32-c6-1.47inch-lcd/
 *     ESP32-C6-LCD-1.47-Demo.zip -> ESP-IDF/ESP32-C6-LCD-1.47-Test/
 *       main/LCD_Driver/ST7789.h                 (pins, clock, geometry)
 *       main/LCD_Driver/Vernon_ST7789T/…c        (the init byte table)
 *       main/LVGL_Driver/LVGL_Driver.c           (the column offset)
 *
 * TIER: this board has NO PSRAM, so it is a BAND-tier renderer like the
 * classic CYD — kBandHeight stays at the 16-scanline default and the
 * band buffer is 172*16*2 = 5,504 B of internal DMA-capable SRAM.  It
 * does NOT take the Hosyond's full-frame path.
 *
 * DAVID-VERIFY: every number below is the vendor's own shipping value,
 * but [CXC-BRINGUP]'s second tier stands — only eyes on the glass
 * confirm the column offset, colour order and inversion.
 */

#ifndef WAVESHARE_C6_PANEL_H
#define WAVESHARE_C6_PANEL_H

#include <cstdint>
#include <cstddef>

/* ----- Geometry ----------------------------------------------------- */

static constexpr int kWsC6Width  = 172;
static constexpr int kWsC6Height = 320;   /* portrait, rect */

/* THE ST7789 GOTCHA.  The controller's GRAM is 240 columns wide; this
 * panel shows only 172 of them, starting at column 34.  Every column
 * address must be biased by this offset or the whole image slides.
 * (240 - 172) / 2 == 34, i.e. the glass is CENTRED in GRAM — which is
 * why the same offset is correct with or without the MX mirror below.
 * Vendor applies it in LVGL_Driver.c: draw_bitmap(x + Offset_X, ...). */
static constexpr int kWsC6ColOffset = 34;
static constexpr int kWsC6RowOffset = 0;

/* ----- Display: ST7789T on SPI2_HOST -------------------------------- */

static constexpr int kWsC6PinSCLK = 7;
static constexpr int kWsC6PinMOSI = 6;
static constexpr int kWsC6PinMISO = -1;   /* write-only panel */
static constexpr int kWsC6PinCS   = 14;
static constexpr int kWsC6PinDC   = 15;   /* HIGH = data, LOW = command */
static constexpr int kWsC6PinRST  = 21;   /* active LOW — a REAL reset pin */
static constexpr int kWsC6PinBL   = 22;   /* backlight, active HIGH */

/* Unlike the Hosyond ES3C28P (whose panel reset is tied to CHIP_PU),
 * this board brings the LCD reset out to its own GPIO.  So init performs
 * a genuine HARDWARE reset and does not need the software-reset
 * substitution.  Timing is the vendor's: 10 ms low, 10 ms high. */
static constexpr int kWsC6ResetLowMs  = 10;
static constexpr int kWsC6ResetHighMs = 10;

/* SPI clock — DO NOT RAISE THIS.  The vendor's value, and it is right.
 *
 * The nominal 12 MHz is really 80/7 = 11.43 MHz (the C6's SPI source is
 * 80 MHz, so only exact dividers are reachable).  That is comfortably
 * under the ST7789 datasheet's serial write-cycle minimum of 66 ns,
 * i.e. ~15 MHz MAXIMUM write clock.
 *
 * TRIED AND REVERTED 2026-08-11: raised to 40 MHz (80/2) on the
 * reasoning that transfer was ~70% of a dirty repaint.  Every machine
 * signal IMPROVED — transfer 67.9 -> 18.4 ms, repaint 97.5 -> 46 ms,
 * bus->glass ~103 -> ~50 ms — and the DISPLAY GOT WORSE: David reported
 * it "updating very infrequently", with NO tearing.
 *
 * That symptom is the tell.  At 2.6x over the datasheet the panel does
 * not latch reliably, so whole transactions are DROPPED rather than
 * half-written; nothing is torn because nothing partial arrives, and the
 * ESP32 side ACKs regardless because it never hears back.  Faster on the
 * wire, less reactive on the glass, all signals green.
 *
 * If this is ever revisited, 80/6 = 13.33 MHz is the only step that
 * stays inside the datasheet, and it buys 17% — not worth the risk. */
static constexpr int kWsC6SpiFreqHz = 12000000;   /* -> 80/7 = 11.43 MHz */

static constexpr int kWsC6BacklightFreqHz = 5000;

/* ⚠ VENDOR HARDWARE CONSTRAINT — NOT a taste setting.
 *
 * The Waveshare wiki for this board states: "Keep the screen brightness
 * at 50% or lower during use" — running full brightness for extended
 * periods shortens panel life.  The vendor's own demo agrees and is the
 * corroborating second source: ST7789.h ships LEDC_TEST_DUTY 4000 at
 * 13-bit resolution = 4000/8191 = 48.8%.
 *
 * kWsC6BacklightMax is ENFORCED in WsC6Display::set_backlight(), not
 * merely documented, because a scene or a future code path asking for
 * 255 would otherwise silently violate a hardware directive.  A board
 * limit belongs at the board seam.
 *
 * (Do NOT copy this to other profiles: the classic CYD runs ~40% because
 * its TN panel washes out — a LOOKS reason — and the Hosyond IPS runs
 * full.  This one is a LIFETIME reason and is specific to this panel.) */
static constexpr uint8_t kWsC6BacklightMax     = 127;  /* 50% of 255 */
static constexpr uint8_t kWsC6BacklightDefault = 124;  /* 48.6% — the
                                                        * vendor's own
                                                        * shipping duty */

/* MADCTL.  The vendor writes 0x36=0x00 during init and then calls
 * esp_lcd_panel_mirror(true, false) with rgb_endian = BGR, which ORs
 * MX (0x40) and BGR (0x08) into the register — final state 0x48.  We
 * write that final value ONCE; the end state is identical.
 *
 * DAVID-VERIFY (colour order): the BGR bit compensates for the panel's
 * physical subpixel order so standard RGB565 shows correct colours.  If
 * red and blue swap on the glass, clear bit 3 (0x48 -> 0x40). */
static constexpr uint8_t kWsC6Madctl = 0x48;

/* DAVID-VERIFY (inversion): ST7789 IPS panels ship inverted and the
 * vendor issues 0x21 (INVON).  If the image is a photo negative, change
 * to 0x20 (INVOFF). */
static constexpr uint8_t kWsC6InvCmd = 0x21;

/* ----- ST7789T init table (vendor Vernon_ST7789T.c, verbatim) --------
 *
 * Data, not code — compare row by row against panel_st7789t_init().
 * The hardware reset and SLPOUT that precede this table are performed
 * by the driver (they need pin toggling and a long settle). */

struct WsC6InitCmd {
    uint8_t cmd;
    uint8_t len;            /* argument byte count (0..15) */
    uint8_t args[15];
    uint16_t delay_ms;
};

static constexpr WsC6InitCmd kWsC6InitSeq[] = {
    /* Memory Access Control — MX | BGR (see kWsC6Madctl) */
    {0x36, 1, {kWsC6Madctl}, 0},
    /* Interface pixel format — 16 bit/px RGB565 */
    {0x3A, 1, {0x55}, 0},
    /* RAM control (RAMCTRL).  Param 2 bit 3 is the ENDIAN bit for 16-bit
     * pixel data, and it is the ONE byte on this board that a capture
     * can never check.
     *
     * The vendor ships 0xE8 — bit 3 SET, i.e. LITTLE-endian — because
     * their LVGL colour buffers are native little-endian uint16.  THIS
     * canvas emits RGB565 BIG-endian (canvas_esp32.cpp rgb565_be, the
     * order the ILI9341 boards want), so we clear bit 3: 0xE8 -> 0xE0.
     *
     * Symptom when this is wrong (observed on the glass by David
     * 2026-08-11, before the fix): every pixel byte-swaps, so pure blue
     * 0x001F reads as 0x1F00 = green, and pure green 0x07E0 reads as
     * 0xE007 = red.  It is NOT a BGR channel swap — that would show blue
     * as red — and MADCTL is not involved.
     *
     * Why it needed eyes: capture.bmp is decoded from the SAME band
     * buffer with the SAME big-endian assumption, so the capture looked
     * perfect while the panel did not.  A textbook [CXC-BRINGUP]
     * verify.human catch. */
    {0xB0, 2, {0x00, 0xE0}, 0},
    /* Porch setting */
    {0xB2, 5, {0x0C, 0x0C, 0x00, 0x33, 0x33}, 0},
    /* Gate control — Vgh=13.65V, Vgl=-10.43V */
    {0xB7, 1, {0x75}, 0},
    /* VCOM setting — 1.175V */
    {0xBB, 1, {0x1A}, 0},
    /* LCM control */
    {0xC0, 1, {0x80}, 0},
    /* VDV/VRH command enable */
    {0xC2, 2, {0x01, 0xFF}, 0},
    /* VRH set */
    {0xC3, 1, {0x13}, 0},
    /* VDV set */
    {0xC4, 1, {0x20}, 0},
    /* Frame rate control — 60 Hz */
    {0xC6, 1, {0x0F}, 0},
    /* Power control 1.
     * NOTE: the ST7789 datasheet gives this command TWO parameters
     * ({0xA4, 0xA1}), and the vendor's array literally contains both —
     * but they pass length 1, so their shipping firmware sends only
     * 0xA4.  We reproduce the VENDOR's behaviour, because that is the
     * configuration their board is known to run.  If the panel shows
     * power/contrast artefacts, sending both bytes is the first thing
     * to try. */
    {0xD0, 1, {0xA4}, 0},
    /* Positive voltage gamma */
    {0xE0, 14, {0xD0, 0x0D, 0x14, 0x0D, 0x0D, 0x09, 0x38,
                0x44, 0x4E, 0x3A, 0x17, 0x18, 0x2F, 0x30}, 0},
    /* Negative voltage gamma */
    {0xE1, 14, {0xD0, 0x09, 0x0F, 0x08, 0x07, 0x14, 0x37,
                0x44, 0x4D, 0x38, 0x15, 0x16, 0x2C, 0x2E}, 0},
    /* Display inversion ON — the IPS quirk (vendor mislabels this
     * "Sleep Out" in a comment; 0x21 is INVON). */
    {kWsC6InvCmd, 0, {}, 0},
    /* Display ON */
    {0x29, 0, {}, 100},
};

static constexpr int kWsC6InitSeqLen =
    (int)(sizeof(kWsC6InitSeq) / sizeof(kWsC6InitSeq[0]));

/* ----- Everything else on the board (NOT in scope for bring-up) -----
 * Recorded so a pin is never silently occupied. */
static constexpr int kWsC6PinRgbLed = 8;    /* WS2812 (vendor RGB/RGB.h) */

/* ⚠ THE SD CARD SHARES THE DISPLAY'S SPI BUS.  Vendor SD_SPI.h reuses
 * EXAMPLE_PIN_NUM_MOSI/SCLK and adds MISO=5, CS=4 — the SAME SPI2 bus
 * the panel is on.  This is the hazard class that froze the classic CYD:
 * an SD teardown (spi_bus_free) reset the DMA channel the display relied
 * on, the panel kept ACKing ESP_OK, and nothing updated again
 * ([ED-HW-CYD-QUIRKS]).
 *
 * This profile NEVER initializes an SD bus, so the display owns SPI2
 * outright and that bug is structurally impossible here — but we still
 * drive SD CS HIGH at init so a physically inserted card cannot chatter
 * on the shared lines.  If SD is ever enabled on this board, the DMA
 * channel and bus arbitration must be designed FIRST, not discovered. */
static constexpr int kWsC6PinSdCS   = 4;
static constexpr int kWsC6PinSdMISO = 5;    /* MOSI/SCLK shared with LCD */

#endif /* WAVESHARE_C6_PANEL_H */
