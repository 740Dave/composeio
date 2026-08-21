/*
 * hosyond_s3_panel.h — THE PANEL CONSTANTS BLOCK for the Hosyond
 * ESP32-S3 2.8" IPS tier ([CXC-BRINGUP], [CXC-ER-BOARDS], [CXC-RM-M5]).
 *
 * =====================================================================
 *  EVERY board-specific number lives HERE and ONLY here — pins, SPI
 *  clock, the ILI9341V init byte table, backlight polarity, and the
 *  FT6336G touch mapping.  Retune this file, rebuild; the profile
 *  (boards/hosyond_s3.cpp), the canvas, and the scene builder are all
 *  parameter-driven and stay untouched.
 * =====================================================================
 *
 * Board:  Hosyond / ES3C28P — ESP32-S3 (QFN56, 16 MB quad flash,
 *         8 MB OCTAL PSRAM) + 2.8" 240x320 IPS, ILI9341V over SPI,
 *         FT6336G capacitive touch over I2C.
 *         Native USB only — no onboard USB-serial bridge.
 *
 * Sources (transcribed, not guessed):
 *  - Pin map: the vendor IO allocation table
 *    5-..._Schematic/ESP32-S3...IO....xlsx  (read 2026-08-11).  Every
 *    GPIO below is a row in that table.
 *  - Init byte table: 2-..._Specification/ILI9341V_Init.txt, the
 *    vendor's LCD_Init() for the 2.8" IPS variant, verbatim.
 *  - SPI host/clock + touch bus: the vendor ESP-IDF LVGL demo's
 *    sdkconfig (1-..._Demo/ESP-IDF/2.8inch_ESP32-S3_LVGL) —
 *    SPI2_HOST, 80 MHz / divider 2 = 40 MHz, I2C0 @ 400 kHz.
 *
 * VERIFIED ON THE GLASS 2026-08-11 (David): panel live, colours correct,
 * touch interactions working.  [CXC-BRINGUP]'s second tier is SATISFIED —
 * the three constants below marked CONFIRMED are no longer "the vendor's
 * plausible values", they are measured-good.  They remain the retune
 * points if a different panel revision ever lands.
 */

#ifndef HOSYOND_S3_PANEL_H
#define HOSYOND_S3_PANEL_H

#include <cstdint>
#include <cstddef>

/* ----- Geometry ----------------------------------------------------- */

static constexpr int kHosyondWidth  = 240;
static constexpr int kHosyondHeight = 320;   /* portrait, rect */

/* Full-frame RGB565 = 153,600 B.  The rendering-tier call ([CXC-BRINGUP]
 * §5, recorded in the descriptor): this board holds a WHOLE frame in its
 * 8 MB PSRAM, so it renders in ONE full-height band instead of the CYD's
 * 20 passes — see CXC_BAND_HEIGHT in the build env. */
static constexpr size_t kHosyondFrameBytes =
    (size_t)kHosyondWidth * kHosyondHeight * sizeof(uint16_t);

/* ----- Display: ILI9341V on SPI2_HOST (FSPI) ------------------------ */
/* GPIO 10/11/12/13 are the S3's NATIVE IOMUX pins for FSPI
 * (CS0/D/CLK/Q), so the bus takes the fast direct route rather than the
 * GPIO matrix — that is why 40 MHz is comfortable here. */

static constexpr int kHosyondPinCS   = 10;   /* active LOW  */
static constexpr int kHosyondPinMOSI = 11;
static constexpr int kHosyondPinSCLK = 12;
static constexpr int kHosyondPinMISO = 13;
static constexpr int kHosyondPinDC   = 46;   /* HIGH = data, LOW = command */
static constexpr int kHosyondPinBL   = 45;   /* backlight, active HIGH */

/* ⚠ THERE IS NO LCD RESET GPIO.  The panel's reset line is tied to
 * CHIP_PU (IO table row 4: "液晶屏复位引脚 ... 复位ESP32和液晶屏").
 * Resetting the ESP32 resets the panel and nothing else can.  init()
 * MUST NOT pulse a reset pin — the vendor's LCD_RESET() does not apply.
 *
 * Consequence: after a COLD boot the panel arrives already hardware-
 * reset (CHIP_PU fell), but after esp_restart() it does NOT — the
 * digital reset leaves the panel in whatever state the last run left.
 * So the init table below OPENS with a SOFTWARE reset (0x01), which is
 * an SPI command, not a pin.  That is the same substitution the classic
 * CYD makes (CYD_TFT_RST == -1). */
static constexpr int kHosyondPinLcdReset = -1;

static constexpr int kHosyondSpiFreqHz = 40000000;  /* vendor demo: 80 MHz
                                                     * / divider 2.  Drop to
                                                     * 20 MHz if the glass
                                                     * shows init/first-frame
                                                     * corruption (the CYD's
                                                     * documented failure). */

/* MADCTL — portrait 240x320.  Vendor writes 0x36 = 0x08: MY=MX=MV=0
 * (no rotation, so column=X=240, row=Y=320) with the BGR bit SET.
 *
 * CONFIRMED on the glass 2026-08-11 (David): the BGR bit compensates for
 * the panel's PHYSICAL subpixel order, so standard RGB565 pixel data
 * displays with correct colours.  If a future panel revision swaps red
 * and blue, clear bit 3 (0x08 -> 0x00). */
static constexpr uint8_t kHosyondMadctlPortrait = 0x08;

/* CONFIRMED on the glass 2026-08-11 (David): this IPS panel ships
 * inverted — the vendor init issues 0x21 (Display Inversion ON) and their
 * LVGL config sets CONFIG_LV_INVERT_COLORS=y to match.  If a future panel
 * revision comes out as a photo negative, change this to 0x20. */
static constexpr uint8_t kHosyondInvCmd = 0x21;

/* Backlight PWM (LEDC).  Active HIGH on this board — do NOT inherit a
 * polarity from another profile.  IPS does not wash out the way the
 * CYD's TN panel does, so full duty is the default. */
static constexpr int     kHosyondBacklightFreqHz = 5000;
static constexpr uint8_t kHosyondBacklightDefault = 255;

/* ----- ILI9341V init table (vendor ILI9341V_Init.txt, verbatim) -----
 *
 * Table-driven so the byte sequence stays DATA, reviewable against the
 * vendor file line by line.  A row is {cmd, arg_count, args..., delay_ms}.
 * Ordering is the vendor's, with two documented edits:
 *   (1) 0x01 SWRESET prepended — we have no reset pin (see above).
 *   (2) MADCTL is written ONCE, portrait; the vendor writes it twice
 *       (once in the block, once via LCD_direction(USE_HORIZONTAL) —
 *       we do not want their landscape default). */

struct HosyondInitCmd {
    uint8_t cmd;
    uint8_t len;            /* argument byte count (0..15) */
    uint8_t args[15];
    uint16_t delay_ms;      /* settle time AFTER this command */
};

static constexpr HosyondInitCmd kHosyondInitSeq[] = {
    /* Software reset — stands in for the absent reset pin. */
    {0x01, 0, {}, 150},

    /* Power control B */
    {0xCF, 3, {0x00, 0xC1, 0x30}, 0},
    /* Power on sequence control */
    {0xED, 4, {0x64, 0x03, 0x12, 0x81}, 0},
    /* Driver timing control A */
    {0xE8, 3, {0x85, 0x00, 0x78}, 0},
    /* Power control A */
    {0xCB, 5, {0x39, 0x2C, 0x00, 0x34, 0x02}, 0},
    /* Pump ratio control */
    {0xF7, 1, {0x20}, 0},
    /* Driver timing control B */
    {0xEA, 2, {0x00, 0x00}, 0},
    /* Power control 1 — VRH[5:0].  NOTE: 0x13 here, not the CYD's
     * 0x23; this is a different panel with different power rails. */
    {0xC0, 1, {0x13}, 0},
    /* Power control 2 — SAP[2:0];BT[3:0] */
    {0xC1, 1, {0x13}, 0},
    /* VCOM control 1 */
    {0xC5, 2, {0x22, 0x35}, 0},
    /* VCOM control 2 */
    {0xC7, 1, {0xBD}, 0},

    /* Display inversion ON — the IPS panel quirk (see kHosyondInvCmd;
     * kept in the table so the sequence reads in vendor order). */
    {kHosyondInvCmd, 0, {}, 0},

    /* Memory Access Control — portrait + BGR (see kHosyondMadctlPortrait) */
    {0x36, 1, {kHosyondMadctlPortrait}, 0},

    /* Display function control */
    {0xB6, 2, {0x0A, 0xA2}, 0},
    /* Pixel format — 16-bit RGB565 */
    {0x3A, 1, {0x55}, 0},
    /* Interface control — MCU interface */
    {0xF6, 2, {0x01, 0x30}, 0},
    /* Frame rate control */
    {0xB1, 2, {0x00, 0x1B}, 0},
    /* 3-Gamma function disable */
    {0xF2, 1, {0x00}, 0},
    /* Gamma curve selected */
    {0x26, 1, {0x01}, 0},
    /* Positive gamma correction */
    {0xE0, 15, {0x0F, 0x35, 0x31, 0x0B, 0x0E, 0x06, 0x49, 0xA7,
                0x33, 0x07, 0x0F, 0x03, 0x0C, 0x0A, 0x00}, 0},
    /* Negative gamma correction */
    {0xE1, 15, {0x00, 0x0A, 0x0F, 0x04, 0x11, 0x08, 0x36, 0x58,
                0x4D, 0x07, 0x10, 0x0C, 0x32, 0x34, 0x0F}, 0},

    /* Exit sleep */
    {0x11, 0, {}, 120},
    /* Display ON */
    {0x29, 0, {}, 100},
};

static constexpr int kHosyondInitSeqLen =
    (int)(sizeof(kHosyondInitSeq) / sizeof(kHosyondInitSeq[0]));

/* ----- Touch: FT6336G, capacitive, I2C0 ----------------------------- */
/* SCL/SDA are SHARED with the ES8311 audio codec (IO table rows 21/22).
 * Harmless while audio is unused — the moment audio is enabled, that bus
 * needs arbitration ([SPEC:CXC-HAL-ACTUATE] will own that). */

static constexpr int kHosyondPinSCL      = 15;
static constexpr int kHosyondPinSDA      = 16;
static constexpr int kHosyondPinTouchInt = 17;   /* active LOW on touch */
static constexpr int kHosyondPinTouchRst = 18;   /* active LOW */
static constexpr int kHosyondI2cFreqHz   = 400000;

/* CONFIRMED on the glass 2026-08-11 (David: touch interactions work):
 * the FT6336G is capacitive and self-calibrating — unlike the CYD's
 * resistive XPT2046 there is NO calibration step, and the controller
 * reports in PANEL-NATIVE pixels (0..239 x, 0..319 y for this portrait
 * glass).  So the mapping is IDENTITY, and that is now measured, not
 * merely reasoned.
 *
 * The vendor's own LVGL demo runs the panel LANDSCAPE and therefore sets
 * SWAPXY + an invert; we run PORTRAIT, where those do not apply — which
 * is exactly why copying their flags would have been wrong.  If a press
 * ever lands rotated or mirrored, flip these three flags; the driver
 * logs RAW controller coordinates on every touch, so one press with a
 * corner target tells you which. */
static constexpr bool kHosyondTouchSwapXY  = false;
static constexpr bool kHosyondTouchInvertX = false;
static constexpr bool kHosyondTouchInvertY = false;

/* ----- Everything else on the board (NOT in scope for bring-up) -----
 *
 * Recorded here because [SPEC:CXC-HAL-ACTUATE] will want it, and
 * because a pin that is silently occupied is a bug waiting to happen.
 * Audio I2S, the amp enable, the RGB LED, SD and the battery ADC are
 * all UNCLAIMED by this profile — nothing below is initialized. */

static constexpr int kHosyondPinI2sMclk  = 4;
static constexpr int kHosyondPinI2sSclk  = 5;
static constexpr int kHosyondPinI2sDout  = 6;
static constexpr int kHosyondPinI2sLrck  = 7;
static constexpr int kHosyondPinI2sDin   = 8;
static constexpr int kHosyondPinAmpEn    = 1;    /* FM8002E, active LOW */
static constexpr int kHosyondPinBattAdc  = 9;    /* TP4054 charger */
static constexpr int kHosyondPinRgbLed   = 42;   /* WS2812B, single-wire */
static constexpr int kHosyondPinSdClk    = 38;
static constexpr int kHosyondPinSdD0     = 39;
static constexpr int kHosyondPinSdCmd    = 40;
static constexpr int kHosyondPinSdD1     = 41;
static constexpr int kHosyondPinSdD2     = 48;
static constexpr int kHosyondPinSdD3     = 47;

/* Brought out and genuinely free (IO table: 是否引出 = Y, no external
 * device).  NOTE: the bring-up briefing lists 2/3/21 — the IO table also
 * frees GPIO14, and frees 15/16 and 43/44 CONDITIONALLY (only while
 * touch+audio, resp. UART0, are unused). */
static constexpr int kHosyondFreeGpio[] = {2, 3, 14, 21};

#endif /* HOSYOND_S3_PANEL_H */
