/*
 * esp32c3_st7796_panel.h — THE PANEL CONSTANTS BLOCK for the ESP32-C3
 * Super Mini + LCDWIKI MSP3525/MSP3526 3.5" ST7796S tier
 * ([SPEC:CXC-BRINGUP], [SPEC:CXC-BOARD-HW], [CXC-ER-BOARDS]).
 *
 * =====================================================================
 *  EVERY board-specific number lives HERE and ONLY here — pins, SPI
 *  host and clock, geometry, the ST7796S init byte table, MADCTL,
 *  inversion, and backlight polarity/limits.  Retune this file,
 *  rebuild.  Never chase a constant through the driver.
 * =====================================================================
 *
 * Board:  ESP32-C3 Super Mini  (QFN32 rev v0.4, RISC-V RV32IMAC,
 *         SINGLE core, 160 MHz, 4 MB embedded XMC flash, NO PSRAM,
 *         NO FPU).  MAC = C3LCD_EXPECT_SERIAL (host overlay) -> device id
 *         `c3lcd-<last 3 MAC bytes>`.
 * Panel:  LCDWIKI MSP3525 / MSP3526, 3.5" IPS 480x320, ST7796S over
 *         4-wire SPI.  Capacitive touch (FT6336U) is FITTED on the
 *         module but its four CTP_* pins are UNCONNECTED — the C3 has
 *         no pins left.  touch() returns nullptr.
 *
 * Sources — transcribed, not invented:
 *   ${DATASHEETS_HOME}/hoysond/3.5" TFT 480x320 with Capacitive Touch/
 *     ST7796_Init.txt                       the vendor init sequence and
 *                                           the LCD_direction() MADCTL table
 *     ST7796S-Sitronix.pdf                  TSCYCW / TSCYCR write-cycle limits
 *     3.5inch_SPI_Module_..._Schematic.pdf  backlight polarity, SD_CS pull-up
 *     3.5inch_SPI_Module_..._User_Manual_EN.pdf   the J2 interface table
 *
 * TIER: NO PSRAM, so this is a BAND-tier renderer like the classic CYD
 * and the Waveshare C6.  480 x 320 x 2 = 307,200 B is a full frame and
 * the C3 has ~400 KB of SRAM total with IDF, FreeRTOS, WiFi and TWAI
 * resident — a full framebuffer is not merely tight, it is impossible.
 * kBandHeight stays at the 16-scanline default: 480*16*2 = 15,360 B of
 * internal DMA-capable SRAM, 20 bands per frame.
 *
 * NO FPU: RV32IMAC software-emulates every float and the band canvas is
 * float-heavy.  Measured on the C6 (same ISA class, same canvas, only
 * the SCENE changed): dial gauges 892.9 ms/frame vs `led_bar`
 * rectangles 31.2 ms — 28x, from scene authoring alone.  Author scenes
 * for this board out of rect / label / led_bar.  Do NOT respond by
 * de-floating the shared canvas.
 *
 * DAVID-VERIFY: MADCTL colour order, RGB565 byte order, inversion and
 * backlight level are the [CXC-BOARD-HW-SELF-CONFIRMING] class — every
 * on-device signal reads them back through the same assumption that
 * wrote them.  Only eyes on the glass settle them.  The firmware puts a
 * LABELLED pure-colour baseline on the panel at boot for exactly this.
 */

#ifndef ESP32C3_ST7796_PANEL_H
#define ESP32C3_ST7796_PANEL_H

#include <cstdint>
#include <cstddef>

/* ----- Geometry ------------------------------------------------------
 *
 * LANDSCAPE 480x320.  The ST7796S GRAM is 320 columns x 480 rows and
 * this glass is exactly 320x480, so — unlike the C6's 172-of-240 panel
 * — there is NO GRAM offset in either orientation.  With MADCTL's MV
 * bit set the address counter follows the transformed axes, so CASET
 * spans 0..479 and RASET spans 0..319. */

static constexpr int kC3LcdWidth  = 480;
static constexpr int kC3LcdHeight = 320;

/* No column/row bias on this panel.  Kept as named constants rather
 * than absent so the next reader can see the question was ASKED — the
 * C6 lost time to exactly this being implicit. */
static constexpr int kC3LcdColOffset = 0;
static constexpr int kC3LcdRowOffset = 0;

/* ----- Display: ST7796S on SPI2_HOST ---------------------------------
 *
 * Wiring as BUILT, confirmed by the owner against the module's J2
 * silkscreen 2026-08-12.  These six are LITERALLY every remaining free
 * pin on this board — see the occupancy note at the bottom of this
 * file.  There is no slack. */

static constexpr int kC3LcdPinSCLK = 4;    /* J2.7  SCK       */
static constexpr int kC3LcdPinMOSI = 7;    /* J2.6  SDI(MOSI) */
static constexpr int kC3LcdPinMISO = -1;   /* J2.9  SDO — UNCONNECTED.
                                            * Write-only panel; the
                                            * canvas never reads GRAM. */
static constexpr int kC3LcdPinCS   = 10;   /* J2.3  LCD_CS    */
static constexpr int kC3LcdPinDC   = 3;    /* J2.5  LCD_RS — the module
                                            * calls it RS; it is DC.
                                            * HIGH = data, LOW = command. */
static constexpr int kC3LcdPinRST  = 1;    /* J2.4  LCD_RST, active LOW */
static constexpr int kC3LcdPinBL   = 0;    /* J2.8  LED, active HIGH    */

/* A REAL reset pin — contrast the Hosyond, whose panel reset is tied to
 * CHIP_PU and therefore needs a SOFTWARE reset substituted at init so a
 * warm esp_restart() does not leave the panel in its old state.  This
 * board can do a genuine hardware reset and does.
 *
 * Timing is the vendor's LCD_RESET(): 100 ms low, 50 ms high. */
static constexpr int kC3LcdResetLowMs  = 100;
static constexpr int kC3LcdResetHighMs = 50;

/* SPI CLOCK — CONSERVATIVE ON PURPOSE, AND THE CEILING IS MEASURED FROM
 * THE DATASHEET, NOT GUESSED.
 *
 * ST7796S-Sitronix.pdf, serial interface AC characteristics:
 *     TSCYCW  serial clock cycle (WRITE)  66 ns min   -> 15.15 MHz MAX
 *     TSCYCR  serial clock cycle (READ)  150 ns min   -> 6.67 MHz (unused;
 *                                                       MISO is not wired)
 *
 * The C3's GPSPI source is 80 MHz, so only exact dividers are reachable:
 *     80/8 = 10.00 MHz   <- THIS, first light
 *     80/6 = 13.33 MHz   the fastest step still INSIDE the datasheet
 *     80/5 = 16.00 MHz   ALREADY OVER SPEC — do not
 *
 * Why 10 and not 13.33 for first light: the panel sits on a breadboard
 * behind a 74LVC245PW level shifter on dupont wire, none of which is a
 * controlled-impedance path, and this bring-up changes one thing at a
 * time.  The C6 paid for the other habit — pushing 11.4 -> 40 MHz there
 * improved EVERY machine signal (transfer 67.9 -> 18.4 ms, repaint
 * 97.5 -> 46 ms) while the DISPLAY GOT WORSE, updating very
 * infrequently with NO TEARING, because 2.6x over spec drops whole
 * transactions rather than half-writing them and SPI writes are
 * fire-and-forget so nothing upstream ever hears about it.
 *
 * COST, stated so the trade is visible: a full 480x320 frame is
 * 307,200 B = 246 ms of pure transfer at 10 MHz (184 ms at 13.33).  A
 * dirty single-band repaint is 15,360 B = 12.3 ms.  The reactive path
 * repaints bands, not frames, so the felt number is the second one. */
static constexpr int kC3LcdSpiFreqHz = 10000000;   /* 80/8 = 10.00 MHz */

/* One band is 480 * 16 * 2 = 15,360 B.  Chunk at exactly one band so a
 * band is ONE DMA transaction and the transfer instrumentation measures
 * a band rather than an arbitrary split. */
static constexpr size_t kC3LcdMaxChunkBytes = 15360;

/* ----- Backlight -----------------------------------------------------
 *
 * ACTIVE HIGH, and the module's own schematic says so in as many words
 * (annotations next to the BSS138 gate, translated):
 *     "LED 不接或者接高电平，背光常亮"  — LED unconnected OR driven HIGH,
 *                                        backlight always on
 *     "LED 接低电平，背光熄灭"          — LED driven LOW, backlight off
 * A 10K pull-up holds the gate high, so the backlight is ON at full
 * brightness from the instant the module has power and BEFORE any
 * firmware runs.  That matters for diagnosis: a lit panel proves the
 * 5V rail and nothing whatsoever about the MCU.  We claim GPIO0 and
 * drive it so brightness is OWNED rather than defaulted.
 *
 * NO CLAMP HERE, DELIBERATELY.  The Waveshare C6 profile clamps to 50%
 * because Waveshare publish a panel-LIFETIME directive for that
 * specific glass.  This vendor's documented normal operating condition
 * is the LED pin UNCONNECTED, i.e. 100% — copying the C6's limit would
 * be cargo cult, and a limit invented on this bench would be a number
 * no evidence supports.  If this 3.5" backlight runs hot or the 5V rail
 * sags, kC3LcdBacklightDefault is the one line to change.
 *
 * VCC is fed 5V ON PURPOSE: the module carries an XC6206 LDO and the
 * user manual states that feeding it 3.3V loses the regulator's
 * dropout and dims the backlight.  Logic is 3.3V through the
 * 74LVC245PW. */
static constexpr int     kC3LcdBacklightFreqHz = 5000;
static constexpr uint8_t kC3LcdBacklightDefault = 255;

/* ----- MADCTL --------------------------------------------------------
 *
 * ST7796S bit map: b7 MY | b6 MX | b5 MV | b4 ML | b3 BGR | b2 MH.
 * The vendor's LCD_direction() table (ST7796_Init.txt) enumerates all
 * four rotations, and EVERY one of them sets bit 3:
 *     case 0  (1<<3)|(1<<6)                 = 0x48  portrait  320x480
 *     case 1  (1<<3)|(1<<5)                 = 0x28  LANDSCAPE 480x320
 *     case 2  (1<<3)|(1<<7)                 = 0x88  portrait  flipped
 *     case 3  (1<<3)|(1<<7)|(1<<6)|(1<<5)   = 0xE8  LANDSCAPE flipped
 *
 * We take case 3 — the 180-degree landscape.  It was case 1 (0x28) from
 * first light until 2026-08-14, when David asked for the panel to be
 * rotated 180 degrees in its enclosure; the previous DAVID-VERIFY note
 * at the bottom of this block had already named 0xE8 as that fix.
 *
 * The delta from 0x28 is EXACTLY the two mirror bits:
 *     0x28 = 0b0010_1000   MY 0 | MX 0 | MV 1 | ML 0 | BGR 1 | MH 0
 *     0xE8 = 0b1110_1000   MY 1 | MX 1 | MV 1 | ML 0 | BGR 1 | MH 0
 *                          ^^^^^^^^^^^ the only bits that moved
 *
 * MV IS PRESERVED (still 1), so the geometry stays LANDSCAPE 480x320 and
 * kC3LcdWidth/kC3LcdHeight, the band height and every CASET/RASET span
 * are UNCHANGED — this is a scan-order flip, not a re-orientation.
 * BGR IS PRESERVED (still 1), so colour order is untouched: if colours
 * move, a bit was changed that should not have been, and the fix is to
 * come back here rather than to hunt in the driver.
 *
 * MIRROR-BIT ARITHMETIC, because the three wrong outcomes are distinct
 * one-bit errors and the eyes check below is built to separate them:
 *     0x28  MY0 MX0  unchanged            (neither bit took)
 *     0x68  MY0 MX1  mirrored HORIZONTALLY (only MX took)
 *     0xA8  MY1 MX0  mirrored VERTICALLY   (only MY took)
 *     0xE8  MY1 MX1  ROTATED 180           (both — the intent)
 *
 * The vendor's LCD_Init() writes 0x36 = 0x48 inside the sequence and
 * then LCD_direction() OVERWRITES 0x36 after DISPON; MADCTL is a plain
 * register, so writing the final value ONCE in the table's 0x36 slot
 * lands the identical end state with one fewer transaction.
 *
 * ⚠ MADCTL CANNOT BE VERIFIED FROM A CAPTURE — it acts at the panel's
 * SCAN-OUT, downstream of everything the MCU can read back.  GET
 * /capture.bmp renders the band buffer, which is IDENTICAL whether or not
 * this byte took effect, so a capture will look perfect over an
 * unrotated panel.  Same class as the C6's byte swap
 * ([CXC-BOARD-HW-SELF-CONFIRMING], [CXC-VISION-EPISTEMICS]
 * rule-self-verification).  EYES ONLY.
 *
 * ⚠ BIT 3 IS THE **BGR** BIT, AND IT IS SET.  It compensates for the
 * panel's physical subpixel order so standard RGB565 shows correct
 * colours.  DAVID-VERIFY: if RED AND BLUE ARE SWAPPED — the band
 * labelled BLUE reads red and the band labelled RED reads blue, while
 * GREEN stays green — that is a CHANNEL swap and the fix is one bit:
 * clear bit 3, 0xE8 -> 0xE0.
 *
 * That is a DIFFERENT symptom from a BYTE swap, and telling them apart
 * by eye is the whole reason the baseline bands are labelled:
 *   channel swap : blue -> RED,   red -> BLUE,  green unchanged
 *   byte swap    : blue 0x001F -> 0x1F00 = GREEN,
 *                  green 0x07E0 -> 0xE007 = RED
 *
 * DAVID-VERIFY (orientation): to go BACK to the original landscape the
 * fix is 0xE8 -> 0x28 (case 1).  For the partial-failure values see the
 * mirror-bit table above. */
static constexpr uint8_t kC3LcdMadctl = 0xE8;

/* ----- Inversion -----------------------------------------------------
 *
 * 0x21 is display-inversion ON and the vendor issues it deliberately —
 * this is an IPS panel and ships inverted.  DAVID-VERIFY: if the whole
 * screen is a photo negative (the dark background comes up near-white),
 * change to 0x20 (INVOFF). */
static constexpr uint8_t kC3LcdInvCmd = 0x21;

/* ----- RGB565 byte order — THE UNVERIFIABLE ONE ----------------------
 *
 * The vendor sequence NEVER TOUCHES the register that byte-swapped the
 * C6.  On ST7789 that is RAMCTRL (0xB0) param 2 bit 3; on ST7796S the
 * equivalent lives in the interface-control block and the vendor leaves
 * it at reset default, which for 4-wire SPI 16-bit is MSB-first — the
 * same order canvas_esp32.cpp's rgb565_be() emits.  So we write nothing
 * and expect it to be right.
 *
 * ⚠ THIS CANNOT BE CONFIRMED FROM A CAPTURE.  GET /capture.bmp decodes
 * the same band buffer with the same big-endian assumption it was
 * written with, so a byte swap renders a PERFECT capture over a wrong
 * panel — precisely what happened on the C6 on 2026-08-11.  Eyes only,
 * and the labelled BLUE band is the test: byte-swapped, it reads GREEN. */

/* ----- ST7796S init table (vendor ST7796_Init.txt, verbatim) ---------
 *
 * Data, not code — compare row by row against the vendor file.  The
 * hardware reset, the leading SLPOUT(0x11) + 120 ms settle and the
 * final DISPON are performed by the driver where they need pin toggling
 * or a long delay the table's per-row delay would obscure.
 *
 * 0xF0 is ST7796S Command Set Control: C3/96 UNLOCKS the extended
 * command set, 3C/69 LOCKS it again.  The unlock/lock pair brackets
 * every extended register the vendor writes, and dropping either half
 * silently turns the writes between them into no-ops. */

struct C3LcdInitCmd {
    uint8_t cmd;
    uint8_t len;            /* argument byte count (0..15) */
    uint8_t args[15];
    uint16_t delay_ms;
};

static constexpr C3LcdInitCmd kC3LcdInitSeq[] = {
    /* Memory Data Access Control — MY | MX | MV | BGR, the landscape
     * rotation flipped 180 degrees.  Vendor writes 0x48 here and its
     * LCD_direction() case 3 writes 0xE8 later; the end state is 0xE8.
     * The value is kC3LcdMadctl and is NOT repeated as a literal here —
     * one MADCTL number in this file, not two that can disagree. */
    {0x36, 1, {kC3LcdMadctl}, 0},
    /* Interface Pixel Format — 16 bit/px RGB565 */
    {0x3A, 1, {0x55}, 0},
    /* Command Set Control — UNLOCK the extended set (C3 then 96) */
    {0xF0, 1, {0xC3}, 0},
    {0xF0, 1, {0x96}, 0},
    /* Display Inversion Control */
    {0xB4, 1, {0x02}, 0},
    /* Entry Mode Set */
    {0xB7, 1, {0xC6}, 0},
    /* Power Control 1 */
    {0xC0, 2, {0xC0, 0x00}, 0},
    /* Power Control 2 */
    {0xC1, 1, {0x13}, 0},
    /* Power Control 3 */
    {0xC2, 1, {0xA7}, 0},
    /* VCOM Control */
    {0xC5, 1, {0x21}, 0},
    /* Display Output Ctrl Adjust */
    {0xE8, 8, {0x40, 0x8A, 0x1B, 0x1B, 0x23, 0x0A, 0xAC, 0x33}, 0},
    /* Positive Gamma Control */
    {0xE0, 14, {0xD2, 0x05, 0x08, 0x06, 0x05, 0x02, 0x2A,
                0x44, 0x46, 0x39, 0x15, 0x15, 0x2D, 0x32}, 0},
    /* Negative Gamma Control */
    {0xE1, 14, {0x96, 0x08, 0x0C, 0x09, 0x09, 0x25, 0x2E,
                0x43, 0x42, 0x35, 0x11, 0x11, 0x28, 0x2E}, 0},
    /* Command Set Control — LOCK the extended set again (3C then 69),
     * then the vendor's 120 ms settle before the display comes on. */
    {0xF0, 1, {0x3C}, 0},
    {0xF0, 1, {0x69}, 120},
    /* Display Inversion ON — the IPS quirk (see kC3LcdInvCmd) */
    {kC3LcdInvCmd, 0, {}, 0},
};

static constexpr int kC3LcdInitSeqLen =
    (int)(sizeof(kC3LcdInitSeq) / sizeof(kC3LcdInitSeq[0]));

/* ----- Everything else on this board, so no pin is silently occupied -
 *
 * ⚠ THE SIX DISPLAY PINS ABOVE ARE EVERY REMAINING FREE GPIO.  Full
 * occupancy of the ESP32-C3 Super Mini as wired:
 *
 *   GPIO0   LCD backlight (this file)
 *   GPIO1   LCD reset     (this file)
 *   GPIO2   STRAPPING     — boot mode.  Not usable.
 *   GPIO3   LCD DC/RS     (this file)
 *   GPIO4   LCD SCLK      (this file)
 *   GPIO5   CAN CTX       — TWAI transmit.  MUST NOT BE REUSED.
 *   GPIO6   CAN CRX       — TWAI receive.   MUST NOT BE REUSED.
 *   GPIO7   LCD MOSI      (this file)
 *   GPIO8   STRAPPING     — boot mode.  Not usable.
 *   GPIO9   STRAPPING     — boot mode.  Not usable.
 *   GPIO10  LCD CS        (this file)
 *   GPIO18  native USB D- — the console and the flashing path
 *   GPIO19  native USB D+ — the console and the flashing path
 *   GPIO20  UART0 RX
 *   GPIO21  UART0 TX
 *
 * Consequences worth stating rather than discovering:
 *   - TOUCH IS OFF.  The FT6336U on the module needs CTP_SCL / CTP_SDA
 *     / CTP_RST / CTP_INT and there are no pins.  Adding touch to this
 *     board means giving up CAN or the UART console, not finding a pin.
 *   - THIS BOARD CANNOT BE UART-SLAVED while it drives glass and CAN:
 *     GPIO20/21 are free only if the console is surrendered.
 *   - No SD.  Module pin 14 SD_CS is unconnected and the module carries
 *     its own 10K pull-up (R8) to VCC3.3, so an inserted card stays
 *     deselected with no firmware involvement.  The classic-CYD hazard
 *     class — an SD teardown resetting the DMA channel the display
 *     relies on, after which every SPI write still returns ESP_OK — is
 *     structurally impossible here because there is no SD bus to tear
 *     down and no pin to give it. */
static constexpr int kC3LcdPinCanTx = 5;   /* reserved — TWAI */
static constexpr int kC3LcdPinCanRx = 6;   /* reserved — TWAI */

#endif /* ESP32C3_ST7796_PANEL_H */
