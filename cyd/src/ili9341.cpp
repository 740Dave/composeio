/*
 * ili9341.cpp — ILI9341 display driver for CYD (ESP32-2432S024)
 *
 * Uses ESP-IDF SPI master driver with DMA for framebuffer transfers.
 * CYD quirks: custom MADCTL (portrait=0x20), RGB color order,
 * backlight on GPIO 27 via LEDC PWM.
 *
 * Spec: [ED-PHASE1], [ED-HW-CYD-QUIRKS]
 */

#include "ili9341.h"
#include "cyd_pins.h"

#include <cstring>
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "ili9341";

/* Max DMA transfer size — ESP32 SPI DMA can do up to 4092 bytes per transaction.
 * One full-width scanline = 240 * 2 = 480 bytes.
 * We can transfer up to 8 scanlines per DMA transaction (3840 bytes). */
static constexpr size_t kMaxDmaBytes = 480 * 8;    /* 3840 bytes = 8 scanlines */
static constexpr uint16_t kScreenW = CYD_SCREEN_W; /* 240 */

/* -------------------------------------------------------------------
 * GPIO and SPI initialization
 * ------------------------------------------------------------------- */

void ILI9341::init_gpio() {
    /* DC pin — output */
    gpio_config_t dc_cfg = {};
    dc_cfg.pin_bit_mask = (1ULL << CYD_TFT_DC);
    dc_cfg.mode = GPIO_MODE_OUTPUT;
    dc_cfg.pull_up_en = GPIO_PULLUP_DISABLE;
    dc_cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    dc_cfg.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&dc_cfg);

    /* SD CS — hold HIGH to prevent HSPI bus interference [ED-HW-CYD-QUIRKS] */
    gpio_config_t sd_cs_cfg = {};
    sd_cs_cfg.pin_bit_mask = (1ULL << CYD_SD_CS);
    sd_cs_cfg.mode = GPIO_MODE_OUTPUT;
    sd_cs_cfg.pull_up_en = GPIO_PULLUP_ENABLE;
    sd_cs_cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    sd_cs_cfg.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&sd_cs_cfg);
    gpio_set_level(static_cast<gpio_num_t>(CYD_SD_CS), 1);

    /* Touch CS — hold HIGH (not used in Phase 1a) */
    gpio_config_t touch_cs_cfg = {};
    touch_cs_cfg.pin_bit_mask = (1ULL << CYD_TOUCH_CS);
    touch_cs_cfg.mode = GPIO_MODE_OUTPUT;
    touch_cs_cfg.pull_up_en = GPIO_PULLUP_ENABLE;
    touch_cs_cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    touch_cs_cfg.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&touch_cs_cfg);
    gpio_set_level(static_cast<gpio_num_t>(CYD_TOUCH_CS), 1);
}

void ILI9341::init_spi() {
    /* Configure HSPI bus */
    spi_bus_config_t bus_cfg = {};
    bus_cfg.mosi_io_num = CYD_TFT_MOSI;
    bus_cfg.miso_io_num = CYD_TFT_MISO;
    bus_cfg.sclk_io_num = CYD_TFT_SCLK;
    bus_cfg.quadwp_io_num = -1;
    bus_cfg.quadhd_io_num = -1;
    bus_cfg.max_transfer_sz = kMaxDmaBytes;

    /* EXPLICIT DMA channel 1 for the display (SPI2/HSPI).
     *
     * History: the original sprite-freeze bug was a shared-DMA hazard —
     * under SPI_DMA_CH_AUTO the SD card's VSPI teardown
     * (sd_deinit -> spi_bus_free(SPI3)) reset the DMA channel the
     * display relied on, so the panel kept ACKing SPI writes (ESP_OK)
     * but displayed nothing new after the boot->loop boundary.
     *
     * ([TF7]) The SD card is now removed entirely, so the display owns
     * the ONLY SPI bus and that bug class is structurally impossible.
     * We keep the channel EXPLICIT (not SPI_DMA_CH_AUTO) anyway — it is
     * clearer, harmless, and documents the intent. */
    esp_err_t ret = spi_bus_initialize(SPI2_HOST, &bus_cfg, 1 /* DMA ch */);
    ESP_ERROR_CHECK(ret);

    /* Attach display device */
    spi_device_interface_config_t dev_cfg = {};
    dev_cfg.clock_speed_hz = CYD_SPI_FREQ_HZ;
    dev_cfg.mode = 0;                  /* SPI mode 0 */
    dev_cfg.spics_io_num = CYD_TFT_CS;
    dev_cfg.queue_size = 7;
    dev_cfg.flags = SPI_DEVICE_NO_DUMMY;

    ret = spi_bus_add_device(SPI2_HOST, &dev_cfg, &_spi);
    ESP_ERROR_CHECK(ret);
}

/* -------------------------------------------------------------------
 * Low-level command/data sends
 * ------------------------------------------------------------------- */

void ILI9341::send_command(uint8_t cmd) {
    gpio_set_level(static_cast<gpio_num_t>(CYD_TFT_DC), 0);  /* Command mode */

    spi_transaction_t txn = {};
    txn.length = 8;
    txn.tx_buffer = &cmd;
    spi_device_polling_transmit(_spi, &txn);
}

void ILI9341::send_data_byte(uint8_t data) {
    gpio_set_level(static_cast<gpio_num_t>(CYD_TFT_DC), 1);  /* Data mode */

    spi_transaction_t txn = {};
    txn.length = 8;
    txn.tx_buffer = &data;
    spi_device_polling_transmit(_spi, &txn);
}

void ILI9341::send_data(const uint8_t* data, size_t len) {
    if (len == 0) return;

    gpio_set_level(static_cast<gpio_num_t>(CYD_TFT_DC), 1);  /* Data mode */

    /* Transfer in chunks that fit within DMA limit */
    while (len > 0) {
        size_t chunk = (len > kMaxDmaBytes) ? kMaxDmaBytes : len;
        spi_transaction_t txn = {};
        txn.length = chunk * 8;
        txn.tx_buffer = data;
        spi_device_polling_transmit(_spi, &txn);
        data += chunk;
        len -= chunk;
    }
}

/* -------------------------------------------------------------------
 * ILI9341 initialization sequence
 * ------------------------------------------------------------------- */

void ILI9341::init_sequence() {
    /* Software reset */
    send_command(0x01);
    vTaskDelay(pdMS_TO_TICKS(150));

    /* Sleep out */
    send_command(0x11);
    vTaskDelay(pdMS_TO_TICKS(150));

    /* Pixel format: 16-bit RGB565 */
    send_command(0x3A);
    send_data_byte(0x55);

    /* MADCTL — CYD portrait mode quirk [ED-HW-CYD-QUIRKS]
     * RGB color order (not BGR), MV bit set for correct scan direction */
    send_command(0x36);
    send_data_byte(CYD_MADCTL_PORTRAIT);

    /* Power control A */
    send_command(0xCB);
    {
        uint8_t d[] = {0x39, 0x2C, 0x00, 0x34, 0x02};
        send_data(d, sizeof(d));
    }

    /* Power control B */
    send_command(0xCF);
    {
        uint8_t d[] = {0x00, 0xC1, 0x30};
        send_data(d, sizeof(d));
    }

    /* Driver timing control A */
    send_command(0xE8);
    {
        uint8_t d[] = {0x85, 0x00, 0x78};
        send_data(d, sizeof(d));
    }

    /* Driver timing control B */
    send_command(0xEA);
    {
        uint8_t d[] = {0x00, 0x00};
        send_data(d, sizeof(d));
    }

    /* Power on sequence control */
    send_command(0xED);
    {
        uint8_t d[] = {0x64, 0x03, 0x12, 0x81};
        send_data(d, sizeof(d));
    }

    /* Pump ratio control */
    send_command(0xF7);
    send_data_byte(0x20);

    /* Power control 1 — GVDD = 4.60V */
    send_command(0xC0);
    send_data_byte(0x23);

    /* Power control 2 */
    send_command(0xC1);
    send_data_byte(0x10);

    /* VCOM control 1 */
    send_command(0xC5);
    {
        uint8_t d[] = {0x3E, 0x28};
        send_data(d, sizeof(d));
    }

    /* VCOM control 2 */
    send_command(0xC7);
    send_data_byte(0x86);

    /* Frame rate control — 70Hz */
    send_command(0xB1);
    {
        uint8_t d[] = {0x00, 0x18};
        send_data(d, sizeof(d));
    }

    /* Display function control */
    send_command(0xB6);
    {
        uint8_t d[] = {0x08, 0x82, 0x27};
        send_data(d, sizeof(d));
    }

    /* Gamma set — curve 1 */
    send_command(0x26);
    send_data_byte(0x01);

    /* Positive gamma correction */
    send_command(0xE0);
    {
        uint8_t d[] = {0x0F, 0x31, 0x2B, 0x0C, 0x0E, 0x08, 0x4E,
                       0xF1, 0x37, 0x07, 0x10, 0x03, 0x0E, 0x09, 0x00};
        send_data(d, sizeof(d));
    }

    /* Negative gamma correction */
    send_command(0xE1);
    {
        uint8_t d[] = {0x00, 0x0E, 0x14, 0x03, 0x11, 0x07, 0x31,
                       0xC1, 0x48, 0x08, 0x0F, 0x0C, 0x31, 0x36, 0x0F};
        send_data(d, sizeof(d));
    }

    /* Display ON */
    send_command(0x29);
    vTaskDelay(pdMS_TO_TICKS(100));
}

/* -------------------------------------------------------------------
 * Backlight via LEDC PWM
 * ------------------------------------------------------------------- */

/* LEDC constants for backlight PWM — defined here where driver/ledc.h is included */
static constexpr ledc_mode_t      kBlMode   = LEDC_LOW_SPEED_MODE;
static constexpr ledc_timer_t     kBlTimer  = LEDC_TIMER_0;
static constexpr ledc_channel_t   kBlCh     = LEDC_CHANNEL_0;
static constexpr ledc_timer_bit_t kBlRes    = LEDC_TIMER_8_BIT;

void ILI9341::init_backlight() {
    ledc_timer_config_t timer_cfg = {};
    timer_cfg.speed_mode = kBlMode;
    timer_cfg.timer_num = kBlTimer;
    timer_cfg.duty_resolution = kBlRes;
    timer_cfg.freq_hz = CYD_BACKLIGHT_FREQ_HZ;
    timer_cfg.clk_cfg = LEDC_AUTO_CLK;
    ledc_timer_config(&timer_cfg);

    ledc_channel_config_t ch_cfg = {};
    ch_cfg.speed_mode = kBlMode;
    ch_cfg.channel = kBlCh;
    ch_cfg.timer_sel = kBlTimer;
    ch_cfg.intr_type = LEDC_INTR_DISABLE;
    ch_cfg.gpio_num = CYD_BACKLIGHT;
    ch_cfg.duty = 100;   /* ~40% — CYD TN panel washes out at full duty */
    ch_cfg.hpoint = 0;
    ledc_channel_config(&ch_cfg);
}

void ILI9341::set_backlight(uint8_t brightness) {
    ledc_set_duty(kBlMode, kBlCh, brightness);
    ledc_update_duty(kBlMode, kBlCh);
}

/* -------------------------------------------------------------------
 * Address window + framebuffer transfer
 * ------------------------------------------------------------------- */

void ILI9341::set_address_window(uint16_t x0, uint16_t y0,
                                  uint16_t x1, uint16_t y1) {
    /* Column address set */
    send_command(0x2A);
    {
        uint8_t d[] = {
            static_cast<uint8_t>(x0 >> 8), static_cast<uint8_t>(x0 & 0xFF),
            static_cast<uint8_t>(x1 >> 8), static_cast<uint8_t>(x1 & 0xFF)
        };
        send_data(d, 4);
    }

    /* Row address set */
    send_command(0x2B);
    {
        uint8_t d[] = {
            static_cast<uint8_t>(y0 >> 8), static_cast<uint8_t>(y0 & 0xFF),
            static_cast<uint8_t>(y1 >> 8), static_cast<uint8_t>(y1 & 0xFF)
        };
        send_data(d, 4);
    }

    /* Memory write */
    send_command(0x2C);
}

void ILI9341::transfer_framebuffer(const uint16_t* framebuffer,
                                    uint16_t y_start, uint16_t y_end) {
    if (y_start > y_end || y_end >= CYD_SCREEN_H) return;

    set_address_window(0, y_start, kScreenW - 1, y_end);

    /* Calculate total bytes to transfer */
    size_t num_scanlines = y_end - y_start + 1;
    size_t total_bytes = num_scanlines * kScreenW * 2;

    /* The framebuffer stores native-endian RGB565 but the ILI9341 expects
     * big-endian (MSB first). We byte-swap during transfer by sending
     * the raw buffer — the framebuffer is pre-swapped at pixel write time
     * in the Canvas backend. */
    const uint8_t* src = reinterpret_cast<const uint8_t*>(
        framebuffer + (y_start * kScreenW));

    gpio_set_level(static_cast<gpio_num_t>(CYD_TFT_DC), 1);  /* Data mode */

    while (total_bytes > 0) {
        size_t chunk = (total_bytes > kMaxDmaBytes) ? kMaxDmaBytes : total_bytes;
        spi_transaction_t txn = {};
        txn.length = chunk * 8;
        txn.tx_buffer = src;
        spi_device_polling_transmit(_spi, &txn);
        src += chunk;
        total_bytes -= chunk;
    }
}

void ILI9341::transfer_band(const uint16_t* band_buf,
                             uint16_t y_start, uint16_t y_end) {
    if (y_start > y_end || y_end >= CYD_SCREEN_H) return;

    set_address_window(0, y_start, kScreenW - 1, y_end);

    size_t num_scanlines = y_end - y_start + 1;
    size_t total_bytes = num_scanlines * kScreenW * 2;

    /* Band buffer starts at index 0 — no offset */
    const uint8_t* src = reinterpret_cast<const uint8_t*>(band_buf);

    gpio_set_level(static_cast<gpio_num_t>(CYD_TFT_DC), 1);

    while (total_bytes > 0) {
        size_t chunk = (total_bytes > kMaxDmaBytes) ? kMaxDmaBytes : total_bytes;
        spi_transaction_t txn = {};
        txn.length = chunk * 8;
        txn.tx_buffer = src;
        spi_device_polling_transmit(_spi, &txn);
        src += chunk;
        total_bytes -= chunk;
    }
}

/* -------------------------------------------------------------------
 * Public init
 * ------------------------------------------------------------------- */

void ILI9341::init() {
    ESP_LOGI(TAG, "Initializing ILI9341 display driver");

    init_gpio();
    init_spi();
    init_backlight();
    init_sequence();

    ESP_LOGI(TAG, "ILI9341 ready — %dx%d portrait, backlight on", width(), height());
}
