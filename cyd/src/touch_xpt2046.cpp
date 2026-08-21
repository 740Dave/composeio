/*
 * touch_xpt2046.cpp -- XPT2046 resistive touch driver
 *
 * Spec: [CYD-TOUCH-DRIVER], [CYD-TOUCH-SPI], [CYD-TOUCH-READ]
 *
 * ARCHITECTURE (CYD sprite-freeze fix, TF3): the XPT2046 is a SECOND
 * spi_master device on SPI2_HOST — the SAME hardware SPI bus the
 * ILI9341 display already owns.  The ESP-IDF SPI driver configures the
 * bus pins ONCE (at ILI9341::init_spi) and owns CS + per-device
 * clock/mode; it reconfigures the peripheral atomically per
 * transaction.  A touch read therefore NEVER tears down the display's
 * pin routing.
 *
 * This replaces the previous bit-bang path, which did gpio_reset_pin()
 * on the shared HSPI pins (MOSI 13 / MISO 12 / SCLK 14) for every touch
 * read and then tried to "restore" them by hand.  Restoring 27 MHz
 * display SPI after a bit-bang read proved impossible (GPIO-matrix
 * ceiling ~26.6 MHz; even IOMUX re-attach did not revive the panel on
 * glass), so the display died on the first touch poll and the sprite
 * froze on frame 0.  Registering touch as a bus peer eliminates the
 * teardown at the root.  Bonus: GPIO12 (MISO) is an ESP32 strapping
 * pin — no longer gpio_reset_pin()'d, so no strapping side effects; and
 * the display CS (GPIO15) stays hardware-managed, never left asserted.
 *
 * Read protocol: 3-byte SPI transactions per channel (full-duplex; the
 * 12-bit result is in bits [14:3] of the 16-bit response).
 * Median-of-3 sampling for noise rejection.
 */

#include "touch_xpt2046.h"
#include "cyd_pins.h"

#include "esp_log.h"

#include <algorithm>

static const char* TAG = "xpt2046";

/* XPT2046 command bytes [CYD-TOUCH-READ] */
static constexpr uint8_t kCmdX  = 0xD0;  /* X position, differential, 12-bit */
static constexpr uint8_t kCmdY  = 0x90;  /* Y position, differential, 12-bit */
static constexpr uint8_t kCmdZ1 = 0xB0;  /* Z1 pressure */
static constexpr uint8_t kCmdZ2 = 0xC0;  /* Z2 pressure */

/* -------------------------------------------------------------------
 * init -- add XPT2046 to SPI2_HOST [CYD-TOUCH-SPI]
 *
 * IMPORTANT: spi_bus_initialize() was already called by ILI9341.
 * We only call spi_bus_add_device() here.
 * ------------------------------------------------------------------- */

void XPT2046::init() {
    init_with_cs(CYD_TOUCH_CS);
}

void XPT2046::init_with_cs(int cs_pin) {
    /* Add the XPT2046 as a SECOND device on the display's SPI2_HOST bus.
     * The SPI driver owns CS + per-device clock/mode and reconfigures
     * the peripheral atomically per transaction, so a touch read can
     * NEVER tear down the 27 MHz display SPI pin routing (the
     * sprite-freeze root cause). */
    if (_spi) return;   /* already added */

    spi_device_interface_config_t dev_cfg = {};
    dev_cfg.clock_speed_hz = CYD_SPI_TOUCH_FREQ_HZ;   /* 2.5 MHz */
    dev_cfg.mode = 0;                                 /* SPI mode 0 */
    dev_cfg.spics_io_num = cs_pin;                    /* hardware CS (GPIO33) */
    dev_cfg.queue_size = 1;

    esp_err_t ret = spi_bus_add_device(SPI2_HOST, &dev_cfg, &_spi);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "XPT2046 add_device failed: %s", esp_err_to_name(ret));
        _spi = nullptr;
        return;
    }
    ESP_LOGI(TAG, "XPT2046 touch ready on SPI2_HOST (CS=GPIO%d, %d Hz) — "
             "shared-bus peer, no pin teardown", cs_pin, CYD_SPI_TOUCH_FREQ_HZ);
}

void XPT2046::scan_cs_pins() {
    /* Try each plausible GPIO as touch CS.
     * Skip pins known to be used: 2(DC), 5(SD_CS), 12-15(HSPI),
     * 16-17(LED), 18-19,23(VSPI/SD), 27(backlight), 36(IRQ input-only) */
    static const int candidates[] = { 33, 25, 26, 32, 0, 4, 21, 22 };
    static const int num_candidates = sizeof(candidates) / sizeof(candidates[0]);

    ESP_LOGI(TAG, "=== XPT2046 CS PIN SCAN ===");
    ESP_LOGI(TAG, "Touch the screen NOW and hold during scan...");

    for (int i = 0; i < num_candidates; i++) {
        int pin = candidates[i];

        /* Remove previous device if any */
        if (_spi) {
            spi_bus_remove_device(_spi);
            _spi = nullptr;
        }

        /* Try this CS pin */
        spi_device_interface_config_t dev_cfg = {};
        dev_cfg.clock_speed_hz = CYD_SPI_TOUCH_FREQ_HZ;
        dev_cfg.mode = 0;
        dev_cfg.spics_io_num = pin;
        dev_cfg.queue_size = 1;

        esp_err_t ret = spi_bus_add_device(SPI2_HOST, &dev_cfg, &_spi);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "  GPIO%d: failed to add device (%s)", pin, esp_err_to_name(ret));
            _spi = nullptr;
            continue;
        }

        /* Read X, Y, Z1, Z2 */
        uint16_t x = read_channel(kCmdX);
        uint16_t y = read_channel(kCmdY);
        uint16_t z1 = read_channel(kCmdZ1);
        uint16_t z2 = read_channel(kCmdZ2);

        bool looks_real = (x != y || x != z1 || z1 != z2) && (z1 > 20);

        ESP_LOGI(TAG, "  GPIO%d: x=%u y=%u z1=%u z2=%u %s",
                 pin, x, y, z1, z2,
                 looks_real ? "<-- POSSIBLE MATCH" : "");

        spi_bus_remove_device(_spi);
        _spi = nullptr;
    }

    ESP_LOGI(TAG, "=== SCAN COMPLETE ===");
}

/* -------------------------------------------------------------------
 * read_channel -- single 12-bit ADC read via spi_master
 *
 * XPT2046 protocol: send command byte, read 2 bytes back.  A single
 * full-duplex 24-bit transaction holds CS low for the whole exchange
 * (driver-managed hardware CS).  The 12-bit result is in bits [14:3]
 * of the 16-bit response (rx[1..2]).
 * ------------------------------------------------------------------- */

uint16_t XPT2046::read_channel(uint8_t cmd) {
    if (!_spi) return 0;

    uint8_t tx[3] = { cmd, 0x00, 0x00 };
    uint8_t rx[3] = { 0, 0, 0 };

    spi_transaction_t txn = {};
    txn.length = 24;              /* 3 bytes */
    txn.tx_buffer = tx;
    txn.rx_buffer = rx;

    if (spi_device_polling_transmit(_spi, &txn) != ESP_OK) return 0;

    uint16_t raw = ((uint16_t)rx[1] << 8) | rx[2];
    raw >>= 3;
    raw &= 0x0FFF;
    return raw;
}

/* -------------------------------------------------------------------
 * Median-of-3 helper
 * ------------------------------------------------------------------- */

static uint16_t median3(uint16_t a, uint16_t b, uint16_t c) {
    if (a > b) std::swap(a, b);
    if (b > c) std::swap(b, c);
    if (a > b) std::swap(a, b);
    return b;
}

/* -------------------------------------------------------------------
 * read -- median-of-3 touch reading [CYD-TOUCH-READ]
 *
 * Takes 3 samples per axis, uses median for noise rejection.
 * Computes pressure from Z1/Z2 to determine touch state.
 *
 * NOTE: no manual CS/pin manipulation — the SPI driver deasserts the
 * display's hardware CS and asserts the touch CS per transaction.  The
 * display's pin routing is untouched, so render_frame stays healthy
 * across every touch poll (this is the sprite-freeze fix).
 * ------------------------------------------------------------------- */

TouchReading XPT2046::read() {
    TouchReading result = {};

    /* Read Z1/Z2 first to check if there's a touch */
    uint16_t z1 = read_channel(kCmdZ1);
    uint16_t z2 = read_channel(kCmdZ2);

    /* Diagnostic: log z1/z2 when they're non-trivial */
    static int diag_count = 0;
    if (++diag_count % 15 == 0 || z1 > 50) {
        ESP_LOGI(TAG, "z1=%u z2=%u", z1, z2);
    }

    /* Compute pressure [CYD-TOUCH-READ]:
     * z = z1 > 0 ? (x_raw * (z2/z1 - 1)) : 0
     * Simplified: if z1 is very small, no touch */
    if (z1 < 10) {
        read_channel(0x80);  /* Power down + PENIRQ re-arm */
        result.raw_x = 0;
        result.raw_y = 0;
        result.pressure = 0;
        result.touched = false;
        return result;
    }

    /* Take 3 samples for X and Y, use median */
    uint16_t x_samples[3];
    uint16_t y_samples[3];

    for (int i = 0; i < 3; i++) {
        x_samples[i] = read_channel(kCmdX);
        y_samples[i] = read_channel(kCmdY);
    }

    result.raw_x = median3(x_samples[0], x_samples[1], x_samples[2]);
    result.raw_y = median3(y_samples[0], y_samples[1], y_samples[2]);

    /* Power down and re-enable PENIRQ.
     * Command format: S=1 A2-A0=000 MODE=0 SER=0 PD1=0 PD0=0
     * 0x80 = 10000000 = start bit + power down + PENIRQ enabled */
    read_channel(0x80);

    /* Pressure calculation */
    uint32_t pressure = (uint32_t)result.raw_x * ((uint32_t)z2 * 1000 / z1 - 1000) / 1000;
    if (pressure > 0xFFFF) pressure = 0xFFFF;
    result.pressure = (uint16_t)pressure;
    result.touched = (result.pressure > kTouchPressureThreshold);

    return result;
}
