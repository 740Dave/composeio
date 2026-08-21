/*
 * touch_ft6336.cpp — FT6336G capacitive touch driver (I2C)
 *
 * Spec: [CXC-BRINGUP], [CXC-ER-BOARDS].
 *
 * Uses the ESP-IDF i2c_master API (the same generation the Qualia
 * profile's expander bus uses — not the legacy i2c driver).
 *
 * The controller needs a settle window after reset before it will ACK:
 * the datasheet's power-on-to-valid figure is ~300 ms.  Reading too
 * early gets a NACK that looks exactly like "no touch controller
 * fitted" — which on this board is also the real question ([CXC-BRINGUP]
 * §4: ES3C28P capacitive vs ES3N28P).  So init() waits, then reports
 * identity registers so the two cases are distinguishable in the log.
 */

#include "touch_ft6336.h"

#include <cstring>

#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "ft6336";

/* Post-reset settle before the controller answers on the bus. */
static constexpr int kFt6336ResetSettleMs = 300;
static constexpr int kFt6336ResetLowMs = 20;

/* Per-transaction I2C timeout.  Generous: a hung bus should surface as
 * a logged error, not a watchdog reset of the render loop. */
static constexpr int kFt6336I2cTimeoutMs = 50;

bool FT6336::init(int sda, int scl, int rst_pin, int int_pin, int freq_hz) {
    /* ----- Reset the controller FIRST, before touching the bus -----
     * This one is a real pin (unlike the LCD's), so use it. */
    if (rst_pin >= 0) {
        gpio_config_t rst_cfg = {};
        rst_cfg.pin_bit_mask = (1ULL << rst_pin);
        rst_cfg.mode = GPIO_MODE_OUTPUT;
        rst_cfg.pull_up_en = GPIO_PULLUP_DISABLE;
        rst_cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
        rst_cfg.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&rst_cfg);

        gpio_set_level((gpio_num_t)rst_pin, 0);      /* active LOW */
        vTaskDelay(pdMS_TO_TICKS(kFt6336ResetLowMs));
        gpio_set_level((gpio_num_t)rst_pin, 1);
        vTaskDelay(pdMS_TO_TICKS(kFt6336ResetSettleMs));
    }

    /* INT is wired but we POLL: the render loop already ticks the touch
     * dispatcher, and the FT6336's INT is a pulse in its default trigger
     * mode — gating reads on it would drop presses.  Configured as a
     * pulled-up input so the line is not left floating. */
    if (int_pin >= 0) {
        gpio_config_t int_cfg = {};
        int_cfg.pin_bit_mask = (1ULL << int_pin);
        int_cfg.mode = GPIO_MODE_INPUT;
        int_cfg.pull_up_en = GPIO_PULLUP_ENABLE;
        int_cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
        int_cfg.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&int_cfg);
    }

    /* ----- I2C bus ------------------------------------------------- */
    i2c_master_bus_config_t bus_cfg = {};
    bus_cfg.i2c_port = -1;                  /* auto-select a free port */
    bus_cfg.sda_io_num = (gpio_num_t)sda;
    bus_cfg.scl_io_num = (gpio_num_t)scl;
    bus_cfg.clk_source = I2C_CLK_SRC_DEFAULT;
    bus_cfg.glitch_ignore_cnt = 7;
    bus_cfg.flags.enable_internal_pullup = true;

    esp_err_t err = i2c_new_master_bus(&bus_cfg, &_bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_new_master_bus failed (SDA=%d SCL=%d): %s",
                 sda, scl, esp_err_to_name(err));
        return false;
    }

    i2c_device_config_t dev_cfg = {};
    dev_cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    dev_cfg.device_address = kFt6336Addr;
    dev_cfg.scl_speed_hz = freq_hz;

    err = i2c_master_bus_add_device(_bus, &dev_cfg, &_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_master_bus_add_device failed: %s",
                 esp_err_to_name(err));
        return false;
    }

    /* ----- Identity -------------------------------------------------
     * A NACK here is the [CXC-BRINGUP] §4 signal: either this is the
     * ES3N28P (no capacitive touch fitted) or the bus is miswired. */
    err = i2c_master_probe(_bus, kFt6336Addr, kFt6336I2cTimeoutMs);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "no ACK from 0x%02X on SDA=%d SCL=%d (%s) — either "
                 "this board is the ES3N28P (no capacitive touch) or the "
                 "I2C bus is wrong.  Touch DISABLED.",
                 kFt6336Addr, sda, scl, esp_err_to_name(err));
        return false;
    }

    _present = true;
    read_regs(kFt6336RegChipId, &_chip_id, 1);
    read_regs(kFt6336RegFirmwareId, &_firmware_id, 1);
    read_regs(kFt6336RegPanelId, &_panel_id, 1);

    /* Stay in Active mode; leave threshold/report-rate at the factory
     * defaults (capacitive self-calibration means there is nothing to
     * tune here for a first bring-up). */
    write_reg(kFt6336RegDevMode, 0x00);
    write_reg(kFt6336RegCtrl, kFt6336CtrlKeepActive);

    /* Chip-id values seen in the wild on this family: 0x06 = FT6206,
     * 0x36 = FT6236, 0x64 = FT6336G (what the ES3C28P reports — measured
     * on the live board 2026-08-11).  Any ACK at all is the answer to
     * the question that matters at bring-up: a capacitive controller IS
     * fitted, so this is the C-variant SKU. */
    ESP_LOGI(TAG, "FT6336 up on SDA=%d SCL=%d @%d Hz — chip 0x%02X "
             "(0x64 = FT6336G), firmware 0x%02X, panel 0x%02X",
             sda, scl, freq_hz, _chip_id, _firmware_id, _panel_id);
    return true;
}

bool FT6336::read_regs(uint8_t reg, uint8_t* out, size_t len) {
    if (!_dev) return false;
    esp_err_t err = i2c_master_transmit_receive(
        _dev, &reg, 1, out, len, kFt6336I2cTimeoutMs);
    if (err != ESP_OK) {
        ESP_LOGD(TAG, "read reg 0x%02X failed: %s", reg, esp_err_to_name(err));
        return false;
    }
    return true;
}

bool FT6336::write_reg(uint8_t reg, uint8_t val) {
    if (!_dev) return false;
    uint8_t buf[2] = {reg, val};
    esp_err_t err = i2c_master_transmit(_dev, buf, sizeof(buf),
                                        kFt6336I2cTimeoutMs);
    if (err != ESP_OK) {
        ESP_LOGD(TAG, "write reg 0x%02X failed: %s", reg, esp_err_to_name(err));
        return false;
    }
    return true;
}

Ft6336Reading FT6336::read() {
    Ft6336Reading r = {};
    if (!_present) return r;

    /* One burst: TD_STATUS then P1 XH/XL/YH/YL/WEIGHT. */
    uint8_t buf[6] = {};
    if (!read_regs(kFt6336RegTdStatus, buf, sizeof(buf))) return r;

    r.points = buf[0] & 0x0F;
    if (r.points == 0 || r.points > 2) return r;   /* 0x0F/0xFF = bus junk */

    r.raw_x = (uint16_t)(((buf[1] & kFt6336MsbMask) << 8) | buf[2]);
    r.raw_y = (uint16_t)(((buf[3] & kFt6336MsbMask) << 8) | buf[4]);
    r.weight = buf[5];
    r.touched = true;
    return r;
}
