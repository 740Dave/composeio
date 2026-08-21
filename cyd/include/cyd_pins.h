/*
 * cyd_pins.h — Pin definitions for CYD (ESP32-2432S024)
 *
 * Spec: [ED-HW-CYD-PINS]
 *
 * All GPIO assignments for the Cheap Yellow Display board.
 * Used by the ILI9341 driver, touch driver, and auxiliary peripherals.
 */

#ifndef CYD_PINS_H
#define CYD_PINS_H

/* ------------------------------------------------------------------ */
/* Display — ILI9341 on HSPI bus                                      */
/* ------------------------------------------------------------------ */
#define CYD_TFT_MOSI       13      /* Shared with touch */
#define CYD_TFT_MISO       12      /* Shared with touch */
#define CYD_TFT_SCLK       14      /* Shared with touch */
#define CYD_TFT_CS          15
#define CYD_TFT_DC           2      /* Data/Command select */
#define CYD_TFT_RST        -1      /* No dedicated reset pin */

/* ------------------------------------------------------------------ */
/* Touch — XPT2046 on shared HSPI bus                                 */
/* ------------------------------------------------------------------ */
#define CYD_TOUCH_CS        33
#define CYD_TOUCH_IRQ       36      /* Input-only GPIO */

/* ------------------------------------------------------------------ */
/* Backlight — PWM via LEDC, 5kHz                                     */
/* ------------------------------------------------------------------ */
#define CYD_BACKLIGHT       27

/* ------------------------------------------------------------------ */
/* SD-slot chip-select — GPIO5                                        */
/* ------------------------------------------------------------------ */
/* ([TF7]) The CYD's SD CARD is no longer USED: the dev-only SD
 * push-capture (capture.cpp/sd_card.cpp) was deleted and the VSPI/SD
 * bus is never initialized, so the display owns the only SPI bus and
 * the shared-DMA bug class is structurally impossible.  We still DRIVE
 * this CS pin HIGH at display init ([ED-HW-CYD-QUIRKS]) so a physically
 * inserted card on the board's SD slot stays deselected and can't
 * disturb the bus.  The former SD data pins (MOSI 23 / MISO 19 /
 * SCLK 18) are gone — nothing initializes that bus anymore. */
#define CYD_SD_CS            5

/* ------------------------------------------------------------------ */
/* RGB LED — Active-low                                               */
/* ------------------------------------------------------------------ */
#define CYD_LED_R            4
#define CYD_LED_G           16
#define CYD_LED_B           17

/* ------------------------------------------------------------------ */
/* Sensors and Output                                                 */
/* ------------------------------------------------------------------ */
#define CYD_LDR             34      /* Photoresistor, ADC input-only */
#define CYD_SPEAKER         26      /* DAC2 output */

/* ------------------------------------------------------------------ */
/* Exposed GPIOs — P3 connector                                       */
/* ------------------------------------------------------------------ */
#define CYD_P3_IO1          21
#define CYD_P3_IO2          22
#define CYD_P3_IO3          35      /* Input-only */

/* ------------------------------------------------------------------ */
/* Display geometry                                                   */
/* ------------------------------------------------------------------ */
#define CYD_SCREEN_W       240
#define CYD_SCREEN_H       320

/* ------------------------------------------------------------------ */
/* MADCTL values — CYD panel quirk [ED-HW-CYD-QUIRKS]                */
/* Standard ILI9341 rotation doesn't work on this panel.              */
/* MADCTL bits: MY=0x80, MX=0x40, MV=0x20, ML=0x10, BGR=0x08        */
/* ------------------------------------------------------------------ */
#define CYD_MADCTL_PORTRAIT         0x20    /* MV | RGB — 240x320 */
#define CYD_MADCTL_LANDSCAPE        0xE0    /* MV|MY|MX|RGB — 320x240 */
#define CYD_MADCTL_PORTRAIT_INV     0x60    /* MV|MX|RGB */
#define CYD_MADCTL_LANDSCAPE_INV    0xA0    /* MV|MY|RGB */

/* ------------------------------------------------------------------ */
/* Touch calibration — calibrated 2026-03-31                          */
/* Axes swapped: raw_X→screen_Y, raw_Y→screen_X                      */
/* Y inverted: high raw_X = top of screen                             */
/* ------------------------------------------------------------------ */
#define CYD_TOUCH_CAL_X_MIN    353
#define CYD_TOUCH_CAL_X_MAX   3760
#define CYD_TOUCH_CAL_Y_MIN    300
#define CYD_TOUCH_CAL_Y_MAX   3862
#define CYD_TOUCH_SWAP_XY      1
#define CYD_TOUCH_INVERT_X     0
#define CYD_TOUCH_INVERT_Y     1

/* ------------------------------------------------------------------ */
/* SPI clock speeds                                                   */
/* ------------------------------------------------------------------ */
#define CYD_SPI_FREQ_HZ            20000000    /* 20 MHz display — 27 MHz
                                                * was above the reliable
                                                * margin for CYD ILI9341
                                                * panels and caused
                                                * intermittent init/first-
                                                * frame corruption (flaky
                                                * boot splash). */
#define CYD_SPI_TOUCH_FREQ_HZ      2500000     /* 2.5 MHz touch */

/* ------------------------------------------------------------------ */
/* Backlight PWM config                                               */
/* Note: LEDC enum values defined in ili9341.cpp where driver/ledc.h  */
/* is included. Only frequency is a plain integer.                    */
/* ------------------------------------------------------------------ */
#define CYD_BACKLIGHT_FREQ_HZ       5000

#endif /* CYD_PINS_H */
