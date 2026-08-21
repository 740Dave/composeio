/*
 * boards/qualia_rgb666.cpp — Board profile: Adafruit Qualia ESP32-S3
 * RGB666 + the 4.0" 720x720 round NV3052C panel (Adafruit 5793).
 * [SPEC:CXC-EMBED-REACTIVE] R5, [CXC-ER-BOARDS], [CXC-RM-M5] board seam.
 *
 * A genuinely different display architecture from the classic CYD:
 * the panel has NO framebuffer of its own — the ESP32-S3's LCD_CAM RGB
 * peripheral continuously scans a PSRAM framebuffer out over a 16-bit
 * parallel bus (RGB565 on the wire), while the NV3052C is configured
 * once at boot over a 9-bit SPI link bit-banged through the board's
 * PCA9554 I2C IO expander (there are no spare MCU pins — all 16 data
 * lines + sync are taken).
 *
 * Every panel constant (pins, timings, init bytes) lives in
 * include/qualia_panel.h — retune there, not here.
 *
 * transfer_band() on this profile takes NATIVE-endian RGB565 rows
 * (the RGB peripheral scans memory directly; contrast the ILI9341's
 * big-endian SPI stream) and blits them into the panel framebuffer
 * via esp_lcd_panel_draw_bitmap.
 *
 * No touch: product 5793 has no touchscreen (touch() == nullptr).
 */

#include "board_profile.h"
#include "qualia_panel.h"

#include <cstring>

#include "driver/i2c_master.h"
#include "esp_lcd_panel_rgb.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

static const char* TAG = "qualia_rgb666";

/* ------------------------------------------------------------------ */
/* PCA9554 9-bit SPI bit-bang (port of CircuitPython shared-module/    */
/* dotclockframebuffer ioexpander_send_init_sequence — same wire       */
/* behavior: CPOL=CPHA=0, DC bit first, CS per init step)              */
/* ------------------------------------------------------------------ */

class QualiaExpanderBus {
public:
    bool init() {
        i2c_master_bus_config_t bus_cfg = {};
        bus_cfg.i2c_port = -1;                      /* auto-select */
        bus_cfg.sda_io_num = (gpio_num_t)kQualiaPinSDA;
        bus_cfg.scl_io_num = (gpio_num_t)kQualiaPinSCL;
        bus_cfg.clk_source = I2C_CLK_SRC_DEFAULT;
        bus_cfg.glitch_ignore_cnt = 7;
        bus_cfg.flags.enable_internal_pullup = true;
        if (i2c_new_master_bus(&bus_cfg, &_bus) != ESP_OK) {
            ESP_LOGE(TAG, "I2C master bus init failed (SDA=%d SCL=%d)",
                     kQualiaPinSDA, kQualiaPinSCL);
            return false;
        }
        i2c_device_config_t dev_cfg = {};
        dev_cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
        dev_cfg.device_address = kQualiaExpanderAddr;
        dev_cfg.scl_speed_hz = kQualiaExpanderI2CHz;
        if (i2c_master_bus_add_device(_bus, &dev_cfg, &_dev) != ESP_OK) {
            ESP_LOGE(TAG, "PCA9554 not found at 0x%02X", kQualiaExpanderAddr);
            return false;
        }
        _shadow = kQualiaExpanderGpioData;
        return true;
    }

    /* Raw register write ({reg, data...}). */
    bool write(const uint8_t* buf, size_t len) {
        return i2c_master_transmit(_dev, buf, len, 1000) == ESP_OK;
    }

    /* Update the output register: shadow = (shadow & ~clear) | set. */
    void pin_change(uint8_t set_bits, uint8_t clear_bits) {
        _shadow = (uint8_t)((_shadow & ~clear_bits) | set_bits);
        uint8_t buf[2] = {kQualiaExpanderGpioReg, _shadow};
        write(buf, 2);
    }

    uint8_t shadow() const { return _shadow; }

    /* Make one expander pin an output and drive it (backlight). */
    void drive_pin(int bit, bool level) {
        /* direction register 3: 0 = output */
        uint8_t dir = 0x78;                 /* board default */
        dir &= (uint8_t)~(1u << bit);
        uint8_t dbuf[2] = {3, dir};
        write(dbuf, 2);
        pin_change(level ? (uint8_t)(1u << bit) : 0,
                   level ? 0 : (uint8_t)(1u << bit));
    }

private:
    i2c_master_bus_handle_t _bus = nullptr;
    i2c_master_dev_handle_t _dev = nullptr;
    uint8_t _shadow = 0;
};

/* Send one 9-bit-per-byte burst (DC + 8 data bits, MSB first). */
static void exp_spi_send(QualiaExpanderBus& bus, bool is_command,
                         const uint8_t* data, uint32_t len) {
    const uint8_t sck = (uint8_t)(1u << kQualiaExpBitSCK);
    const uint8_t cs = (uint8_t)(1u << kQualiaExpBitCS);
    const uint8_t mosi = (uint8_t)(1u << kQualiaExpBitMOSI);

    int dc_mask = is_command ? 0 : 0x100;
    for (uint32_t i = 0; i < len; i++) {
        int bits = data[i] | dc_mask;
        for (int j = 0; j < 9; j++) {
            /* CPOL=CPHA=0: fresh data on falling clk (and asserted CS) */
            if (bits & 0x100) {
                bus.pin_change(/*set*/ mosi, /*clear*/ (uint8_t)(sck | cs));
            } else {
                bus.pin_change(0, (uint8_t)(mosi | sck | cs));
            }
            /* latch on rising clk */
            bus.pin_change(sck, 0);
            bits <<= 1;
        }
    }
}

/* The full displayio-framed init sequence, through the expander. */
static bool send_panel_init(QualiaExpanderBus& bus) {
    const uint8_t sck = (uint8_t)(1u << kQualiaExpBitSCK);
    const uint8_t cs = (uint8_t)(1u << kQualiaExpBitCS);
    const uint8_t rst = (uint8_t)(1u << kQualiaExpBitReset);

    /* Expander bring-up writes (direction + polarity). */
    {
        const uint8_t* seq = kQualiaExpanderI2CInit;
        size_t total = sizeof(kQualiaExpanderI2CInit);
        for (size_t i = 0; i < total;) {
            uint8_t n = seq[i];
            if (!bus.write(&seq[i + 1], n)) {
                ESP_LOGE(TAG, "expander init write failed");
                return false;
            }
            i += (size_t)n + 1;
        }
    }

    /* Deassert CS, idle CLK, assert panel RESET. */
    bus.pin_change(/*set*/ cs, /*clear*/ (uint8_t)(sck | rst));
    vTaskDelay(pdMS_TO_TICKS(10));
    bus.pin_change(/*set*/ rst, 0);
    vTaskDelay(pdMS_TO_TICKS(100));

    /* NV3052C register sequence ({cmd, len|0x80, params, [delay]}). */
    const uint8_t* seq = kQualiaNV3052CInit;
    size_t total = sizeof(kQualiaNV3052CInit);
    int steps = 0;
    for (size_t i = 0; i < total;) {
        const uint8_t* cmd = seq + i;
        uint8_t data_size = cmd[1];
        bool has_delay = (data_size & 0x80) != 0;
        data_size &= 0x7F;

        exp_spi_send(bus, true, cmd, 1);
        exp_spi_send(bus, false, cmd + 2, data_size);

        bus.pin_change(0, sck);            /* idle CLK  */
        bus.pin_change(cs, 0);             /* deassert CS */

        size_t consumed = 2 + data_size;
        if (has_delay) {
            uint16_t ms = cmd[2 + data_size];
            if (ms == 255) ms = 500;
            vTaskDelay(pdMS_TO_TICKS(ms));
            consumed += 1;
        }
        i += consumed;
        steps++;
    }
    ESP_LOGI(TAG, "NV3052C init sequence sent (%d steps, %u bytes)",
             steps, (unsigned)total);
    return true;
}

/* ------------------------------------------------------------------ */
/* Display driver: LCD_CAM RGB panel scanning a PSRAM framebuffer      */
/* ------------------------------------------------------------------ */

/* VSYNC latch for the zero-copy swap: present_scanout() flips
 * esp_lcd's cur_fb_index and then waits one vsync so the panel is
 * guaranteed to have stopped scanning the OLD front buffer before the
 * caller starts compositing the next frame into it.  Callback is NOT
 * IRAM (CONFIG_LCD_RGB_ISR_IRAM_SAFE=n on this board — see
 * sdkconfig.qualia.defaults). */
static SemaphoreHandle_t s_vsync_sem = nullptr;

static bool qualia_on_vsync(esp_lcd_panel_handle_t /*panel*/,
                            const esp_lcd_rgb_panel_event_data_t* /*edata*/,
                            void* /*user_ctx*/) {
    BaseType_t woken = pdFALSE;
    if (s_vsync_sem) xSemaphoreGiveFromISR(s_vsync_sem, &woken);
    return woken == pdTRUE;
}

class QualiaRgbDisplay : public DisplayDriver {
public:
    void init() override {
        /* 1. Panel controller init over the expander. */
        if (!_expander.init() || !send_panel_init(_expander)) {
            ESP_LOGE(TAG, "panel init over PCA9554 FAILED — RGB scan-out "
                     "will show garbage until timings/expander are fixed");
        }

        /* 2. RGB dot-clock peripheral (esp_lcd), fb in PSRAM. */
        esp_lcd_rgb_panel_config_t cfg = {};
        cfg.clk_src = LCD_CLK_SRC_DEFAULT;
        cfg.timings.pclk_hz = kQualiaPclkHz;
        cfg.timings.h_res = kQualiaWidth;
        cfg.timings.v_res = kQualiaHeight;
        cfg.timings.hsync_pulse_width = kQualiaHsyncPulseWidth;
        cfg.timings.hsync_back_porch = kQualiaHsyncBackPorch;
        cfg.timings.hsync_front_porch = kQualiaHsyncFrontPorch;
        cfg.timings.vsync_pulse_width = kQualiaVsyncPulseWidth;
        cfg.timings.vsync_back_porch = kQualiaVsyncBackPorch;
        cfg.timings.vsync_front_porch = kQualiaVsyncFrontPorch;
        cfg.timings.flags.hsync_idle_low = kQualiaHsyncIdleLow;
        cfg.timings.flags.vsync_idle_low = kQualiaVsyncIdleLow;
        cfg.timings.flags.de_idle_high = kQualiaDeIdleHigh;
        cfg.timings.flags.pclk_active_neg = kQualiaPclkActiveNeg;
        cfg.timings.flags.pclk_idle_high = kQualiaPclkIdleHigh;

        cfg.data_width = 16;
        cfg.bits_per_pixel = 16;
        /* TWO driver framebuffers (R5 3-buffer radar path): the canvas
         * composites into the back one and presents by pointer swap —
         * no PSRAM->PSRAM flush copy, no tearing. */
        cfg.num_fbs = 2;
        cfg.bounce_buffer_size_px = kQualiaBounceBufferPx;
        cfg.dma_burst_size = 64;

        cfg.hsync_gpio_num = kQualiaPinHSYNC;
        cfg.vsync_gpio_num = kQualiaPinVSYNC;
        cfg.de_gpio_num = kQualiaPinDE;
        cfg.pclk_gpio_num = kQualiaPinPCLK;
        cfg.disp_gpio_num = -1;
        for (int i = 0; i < 16; i++)
            cfg.data_gpio_nums[i] = kQualiaPinsData[i];

        cfg.flags.fb_in_psram = 1;

        esp_err_t err = esp_lcd_new_rgb_panel(&cfg, &_panel);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_lcd_new_rgb_panel failed: %d", (int)err);
            _panel = nullptr;
            return;
        }
        esp_lcd_panel_reset(_panel);
        esp_lcd_panel_init(_panel);

        /* Grab the driver's two scan-out framebuffers (zero-copy swap
         * targets) and blank them.  CPU memset is coherent with the
         * bounce-buffer refill (also a CPU read through the cache). */
        void* fb0 = nullptr;
        void* fb1 = nullptr;
        if (esp_lcd_rgb_panel_get_frame_buffer(_panel, 2, &fb0, &fb1)
                == ESP_OK && fb0 && fb1) {
            _fbs[0] = static_cast<uint16_t*>(fb0);
            _fbs[1] = static_cast<uint16_t*>(fb1);
            size_t bytes = (size_t)kQualiaWidth * kQualiaHeight * 2;
            memset(_fbs[0], 0, bytes);
            memset(_fbs[1], 0, bytes);
            ESP_LOGI(TAG, "scan-out fbs: %p / %p (zero-copy swap armed)",
                     fb0, fb1);
        } else {
            ESP_LOGW(TAG, "esp_lcd_rgb_panel_get_frame_buffer failed — "
                     "falling back to copy-flush presents");
            /* Blank the panel framebuffer (draw one black frame). */
            static uint16_t zero_row[kQualiaWidth];
            memset(zero_row, 0, sizeof(zero_row));
            for (int y = 0; y < kQualiaHeight; y++)
                esp_lcd_panel_draw_bitmap(_panel, 0, y, kQualiaWidth, y + 1,
                                          zero_row);
        }

        /* VSYNC event: lets present_scanout() wait for the swap latch. */
        if (!s_vsync_sem) s_vsync_sem = xSemaphoreCreateBinary();
        esp_lcd_rgb_panel_event_callbacks_t cbs = {};
        cbs.on_vsync = qualia_on_vsync;
        if (esp_lcd_rgb_panel_register_event_callbacks(_panel, &cbs, nullptr)
                != ESP_OK) {
            ESP_LOGW(TAG, "vsync callback registration failed — swaps "
                     "will not be vsync-latched");
        }

        set_backlight(255);
        ESP_LOGI(TAG, "RGB panel up: %dx%d @ %d Hz pclk %.1f MHz "
                 "(fb in PSRAM)", kQualiaWidth, kQualiaHeight,
                 kQualiaPclkHz / ((kQualiaWidth + kQualiaHsyncPulseWidth +
                                   kQualiaHsyncBackPorch + kQualiaHsyncFrontPorch) *
                                  (kQualiaHeight + kQualiaVsyncPulseWidth +
                                   kQualiaVsyncBackPorch + kQualiaVsyncFrontPorch)),
                 kQualiaPclkHz / 1e6);
    }

    /* NATIVE-endian RGB565 rows -> panel framebuffer.  band_buf points
     * at row y_start of a width-strided buffer (the canvas's PSRAM
     * framebuffer), so partial flushes are zero-copy row blits. */
    void transfer_band(const uint16_t* band_buf, uint16_t y_start,
                       uint16_t y_end) override {
        if (!_panel) return;
        esp_lcd_panel_draw_bitmap(_panel, 0, y_start, kQualiaWidth,
                                  (int)y_end + 1, band_buf);
    }

    /* Backlight rides PCA9554 P4 (binary on this board). */
    void set_backlight(uint8_t brightness) override {
        _expander.drive_pin(kQualiaExpBitBacklite, brightness > 0);
    }

    /* ----- Zero-copy scan-out seam (R5 3-buffer radar path) -------- */

    int scanout_framebuffers(uint16_t** out, int max) override {
        int n = 0;
        for (int i = 0; i < 2 && n < max; i++)
            if (_fbs[i]) out[n++] = _fbs[i];
        return n;
    }

    /* draw_bitmap with a pointer that EQUALS one of the driver's own
     * framebuffers is esp_lcd's zero-copy swap: no memcpy, just a
     * cur_fb_index flip (+ any cache writeback the driver needs).  We
     * then wait one vsync so the previous front buffer is no longer
     * being scanned when the caller reuses it as the next back buffer
     * (CONFIG_LCD_RGB_RESTART_IN_VSYNC=y restarts the DMA from the new
     * index at vsync). */
    bool present_scanout(uint16_t* fb) override {
        if (!_panel || (fb != _fbs[0] && fb != _fbs[1]) || !fb) return false;
        if (s_vsync_sem) xSemaphoreTake(s_vsync_sem, 0);   /* clear stale */
        esp_lcd_panel_draw_bitmap(_panel, 0, 0, kQualiaWidth, kQualiaHeight,
                                  fb);
        if (s_vsync_sem &&
            xSemaphoreTake(s_vsync_sem, pdMS_TO_TICKS(100)) != pdTRUE) {
            ESP_LOGW(TAG, "present_scanout: vsync wait timed out");
        }
        return true;
    }

private:
    QualiaExpanderBus _expander;
    esp_lcd_panel_handle_t _panel = nullptr;
    uint16_t* _fbs[2] = {nullptr, nullptr};
};

/* ------------------------------------------------------------------ */
/* The profile                                                         */
/* ------------------------------------------------------------------ */

class QualiaProfile : public BoardProfile {
public:
    const char* name() const override {
        return "qualia-s3-rgb666 (NV3052C 720x720 round, Adafruit 5793)";
    }

    PanelGeometry geometry() const override {
        return PanelGeometry{kQualiaWidth, kQualiaHeight,
                             kQualiaRound ? PanelShape::Round
                                          : PanelShape::Rect};
    }

    DisplayDriver& display() override { return _display; }
    TouchDriver* touch() override { return nullptr; }   /* 5793: no touch */

private:
    QualiaRgbDisplay _display;
};

BoardProfile& board_profile() {
    static QualiaProfile profile;
    return profile;
}
