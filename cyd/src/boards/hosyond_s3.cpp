/*
 * boards/hosyond_s3.cpp — Board profile: Hosyond ESP32-S3 2.8" IPS
 * (ES3C28P) — ILI9341V 240x320 over SPI + FT6336G capacitive touch.
 *
 * Spec: [CXC-BRINGUP], [CXC-ER-BOARDS], [CXC-RM-M5] board seam.
 *
 * THE CYD'S PANEL ON THE QUALIA'S MCU.  The display controller is the
 * ILI9341 family the classic CYD already drives, so the driver shape
 * below is that driver's shape; the MCU is the ESP32-S3 with octal
 * PSRAM the Qualia profile already builds for, so the build config is
 * that config.  What is genuinely NEW is the FT6336G capacitive touch
 * (src/touch_ft6336.cpp) — the first I2C touch controller in the tree.
 *
 * Deliberately NOT a refactor of src/ili9341.cpp.  That driver hard-codes
 * the CYD's pins and the CYD's init bytes, and this panel differs in
 * both plus power rails, gamma, inversion and colour order.  The
 * in-tree precedent is that a board profile OWNS its display driver
 * (boards/qualia_rgb666.cpp does exactly this) — and folding two panels
 * into one parameterized driver would put a regression on the classic
 * CYD, the project's performance floor, which is not on the bench to
 * re-verify.  Recorded in the descriptor as a deferred consolidation.
 *
 * Every panel constant lives in include/hosyond_s3_panel.h — retune
 * there, not here.
 */

#include "board_profile.h"
#include "hosyond_s3_panel.h"
#include "touch_ft6336.h"

#include <algorithm>
#include <cstring>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_memory_utils.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/soc_caps.h"

static const char* TAG = "hosyond_s3";

/* ------------------------------------------------------------------ */
/* DMA reachability                                                    */
/* ------------------------------------------------------------------ */

/* The canvas band buffer on this board is a FULL FRAME in PSRAM
 * (153,600 B — see the tier call in hosyond_s3_panel.h).  The S3's GDMA
 * can reach external RAM, but the heap allocator does not tag PSRAM
 * MALLOC_CAP_DMA, so whether a given pointer is directly transmittable
 * is a runtime question.  Answer it once, at first transfer, and keep
 * an internal-SRAM bounce buffer for the case where it is not.
 *
 * This matters more than it looks: a buffer the DMA engine cannot read
 * is precisely the failure the bring-up brief warns about — SPI returns
 * ESP_OK over a panel that never changes. */
static inline bool ptr_dma_reachable(const void* p) {
    if (esp_ptr_dma_capable(p)) return true;
#if defined(SOC_PSRAM_DMA_CAPABLE) && SOC_PSRAM_DMA_CAPABLE
    if (esp_ptr_dma_ext_capable(p)) return true;
#endif
    return false;
}

/* Internal-SRAM staging chunk used only when the source is unreachable. */
static constexpr size_t kBounceBytes = 16384;

/* Per-transaction ceiling.  GDMA handles large transfers, but chunking
 * keeps one stalled transaction from parking the bus for a whole frame. */
static constexpr size_t kMaxChunkBytes = 32768;

/* ------------------------------------------------------------------ */
/* Backlight — LEDC PWM, ACTIVE HIGH on this board                     */
/* ------------------------------------------------------------------ */

static constexpr ledc_mode_t      kBlMode  = LEDC_LOW_SPEED_MODE;
static constexpr ledc_timer_t     kBlTimer = LEDC_TIMER_0;
static constexpr ledc_channel_t   kBlCh    = LEDC_CHANNEL_0;
static constexpr ledc_timer_bit_t kBlRes   = LEDC_TIMER_8_BIT;

/* ------------------------------------------------------------------ */
/* Display: ILI9341V over SPI2_HOST (FSPI)                             */
/* ------------------------------------------------------------------ */

class HosyondDisplay : public DisplayDriver {
public:
    void init() override {
        ESP_LOGI(TAG, "ILI9341V bring-up — CS=%d MOSI=%d SCLK=%d MISO=%d "
                 "DC=%d BL=%d (no LCD reset GPIO; reset is tied to CHIP_PU)",
                 kHosyondPinCS, kHosyondPinMOSI, kHosyondPinSCLK,
                 kHosyondPinMISO, kHosyondPinDC, kHosyondPinBL);

        init_gpio();
        init_spi();
        init_backlight();
        init_sequence();

        ESP_LOGI(TAG, "ILI9341V ready — %dx%d portrait, MADCTL 0x%02X, "
                 "inversion cmd 0x%02X, SPI %d Hz, backlight ON",
                 kHosyondWidth, kHosyondHeight, kHosyondMadctlPortrait,
                 kHosyondInvCmd, kHosyondSpiFreqHz);
    }

    void transfer_band(const uint16_t* band_buf,
                       uint16_t y_start, uint16_t y_end) override {
        if (!band_buf || y_start > y_end || y_end >= kHosyondHeight) return;

        set_address_window(0, y_start, kHosyondWidth - 1, y_end);

        size_t total_bytes =
            (size_t)(y_end - y_start + 1) * kHosyondWidth * sizeof(uint16_t);
        const uint8_t* src = reinterpret_cast<const uint8_t*>(band_buf);

        gpio_set_level((gpio_num_t)kHosyondPinDC, 1);   /* data mode */

        /* Resolve (and announce) the DMA route once. */
        if (!_dma_route_known) {
            _direct_dma = ptr_dma_reachable(band_buf);
            _dma_route_known = true;
            ESP_LOGI(TAG, "frame buffer at %p is %s — %s",
                     (const void*)band_buf,
                     _direct_dma ? "DMA-reachable" : "NOT DMA-reachable",
                     _direct_dma ? "transmitting in place"
                                 : "staging through an internal-SRAM bounce");
            if (!_direct_dma && !_bounce) {
                _bounce = static_cast<uint8_t*>(
                    heap_caps_malloc(kBounceBytes, MALLOC_CAP_DMA));
                if (!_bounce) {
                    ESP_LOGE(TAG, "bounce buffer alloc (%u B) FAILED — the "
                             "panel will not update", (unsigned)kBounceBytes);
                }
            }
        }

        while (total_bytes > 0) {
            size_t chunk = std::min(total_bytes, kMaxChunkBytes);
            const uint8_t* tx = src;

            if (!_direct_dma) {
                if (!_bounce) return;
                chunk = std::min(chunk, kBounceBytes);
                memcpy(_bounce, src, chunk);
                tx = _bounce;
            }

            spi_transaction_t txn = {};
            txn.length = chunk * 8;
            txn.tx_buffer = tx;
            esp_err_t err = spi_device_polling_transmit(_spi, &txn);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "SPI transfer failed at y=%u: %s",
                         (unsigned)y_start, esp_err_to_name(err));
                return;
            }

            src += chunk;
            total_bytes -= chunk;
        }
    }

    void set_backlight(uint8_t brightness) override {
        /* Active HIGH: duty IS brightness, no inversion. */
        ledc_set_duty(kBlMode, kBlCh, brightness);
        ledc_update_duty(kBlMode, kBlCh);
    }

private:
    spi_device_handle_t _spi = nullptr;
    uint8_t* _bounce = nullptr;
    bool _direct_dma = false;
    bool _dma_route_known = false;

    void init_gpio() {
        gpio_config_t dc_cfg = {};
        dc_cfg.pin_bit_mask = (1ULL << kHosyondPinDC);
        dc_cfg.mode = GPIO_MODE_OUTPUT;
        dc_cfg.pull_up_en = GPIO_PULLUP_DISABLE;
        dc_cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
        dc_cfg.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&dc_cfg);

        /* Nothing else to deselect: this board's SD card is on its own
         * SDIO pins (38-41/47/48), not on the display's SPI bus, and no
         * SD bus is ever initialized here.  The classic CYD's shared-DMA
         * freeze class is structurally absent — but if SD is ever enabled
         * on this board, revisit DMA channel allocation first. */
    }

    void init_spi() {
        spi_bus_config_t bus_cfg = {};
        bus_cfg.mosi_io_num = kHosyondPinMOSI;
        bus_cfg.miso_io_num = kHosyondPinMISO;
        bus_cfg.sclk_io_num = kHosyondPinSCLK;
        bus_cfg.quadwp_io_num = -1;
        bus_cfg.quadhd_io_num = -1;
        bus_cfg.max_transfer_sz = kMaxChunkBytes;

        /* SPI_DMA_CH_AUTO, not an explicit channel: the S3 uses GDMA,
         * where channels are allocated by the driver and the ESP32-era
         * "pin channel 1" idiom does not apply. */
        ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus_cfg,
                                           SPI_DMA_CH_AUTO));

        spi_device_interface_config_t dev_cfg = {};
        dev_cfg.clock_speed_hz = kHosyondSpiFreqHz;
        dev_cfg.mode = 0;
        dev_cfg.spics_io_num = kHosyondPinCS;
        dev_cfg.queue_size = 7;
        dev_cfg.flags = SPI_DEVICE_NO_DUMMY;

        ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev_cfg, &_spi));
    }

    void init_backlight() {
        ledc_timer_config_t timer_cfg = {};
        timer_cfg.speed_mode = kBlMode;
        timer_cfg.timer_num = kBlTimer;
        timer_cfg.duty_resolution = kBlRes;
        timer_cfg.freq_hz = kHosyondBacklightFreqHz;
        timer_cfg.clk_cfg = LEDC_AUTO_CLK;
        ledc_timer_config(&timer_cfg);

        ledc_channel_config_t ch_cfg = {};
        ch_cfg.speed_mode = kBlMode;
        ch_cfg.channel = kBlCh;
        ch_cfg.timer_sel = kBlTimer;
        ch_cfg.intr_type = LEDC_INTR_DISABLE;
        ch_cfg.gpio_num = kHosyondPinBL;
        ch_cfg.duty = kHosyondBacklightDefault;
        ch_cfg.hpoint = 0;
        ledc_channel_config(&ch_cfg);
    }

    void send_command(uint8_t cmd) {
        gpio_set_level((gpio_num_t)kHosyondPinDC, 0);
        spi_transaction_t txn = {};
        txn.length = 8;
        txn.tx_buffer = &cmd;
        spi_device_polling_transmit(_spi, &txn);
    }

    void send_data(const uint8_t* data, size_t len) {
        if (len == 0) return;
        gpio_set_level((gpio_num_t)kHosyondPinDC, 1);
        spi_transaction_t txn = {};
        txn.length = len * 8;
        txn.tx_buffer = data;
        spi_device_polling_transmit(_spi, &txn);
    }

    /* Walk the vendor init table.  Data, not code — compare it against
     * ILI9341V_Init.txt line by line. */
    void init_sequence() {
        for (int i = 0; i < kHosyondInitSeqLen; ++i) {
            const HosyondInitCmd& c = kHosyondInitSeq[i];
            send_command(c.cmd);
            if (c.len) send_data(c.args, c.len);
            if (c.delay_ms) vTaskDelay(pdMS_TO_TICKS(c.delay_ms));
        }
    }

    void set_address_window(uint16_t x0, uint16_t y0,
                            uint16_t x1, uint16_t y1) {
        send_command(0x2A);                     /* column address set */
        {
            uint8_t d[] = {(uint8_t)(x0 >> 8), (uint8_t)(x0 & 0xFF),
                           (uint8_t)(x1 >> 8), (uint8_t)(x1 & 0xFF)};
            send_data(d, sizeof(d));
        }
        send_command(0x2B);                     /* row address set */
        {
            uint8_t d[] = {(uint8_t)(y0 >> 8), (uint8_t)(y0 & 0xFF),
                           (uint8_t)(y1 >> 8), (uint8_t)(y1 & 0xFF)};
            send_data(d, sizeof(d));
        }
        send_command(0x2C);                     /* memory write */
    }
};

/* ------------------------------------------------------------------ */
/* Touch: FT6336G — capacitive, so NO calibration, only orientation    */
/* ------------------------------------------------------------------ */

class HosyondTouch : public TouchDriver {
public:
    void init() override {
        _ok = _ft.init(kHosyondPinSDA, kHosyondPinSCL,
                       kHosyondPinTouchRst, kHosyondPinTouchInt,
                       kHosyondI2cFreqHz);
        if (!_ok) {
            ESP_LOGW(TAG, "touch unavailable — the scene will render but "
                          "will not accept presses");
        }
    }

    TouchPoint read() override {
        TouchPoint tp{0.0f, 0.0f, 0, false};
        if (!_ok) return tp;

        Ft6336Reading r = _ft.read();
        if (!r.touched) return tp;

        /* The controller already reports panel-native PIXELS — there is
         * no ADC range to rescale (contrast ClassicCydTouch).  All that
         * remains is how the glass is oriented relative to the scene. */
        float sx = (float)r.raw_x;
        float sy = (float)r.raw_y;

        if (kHosyondTouchSwapXY) std::swap(sx, sy);
        if (kHosyondTouchInvertX) sx = (float)(kHosyondWidth - 1) - sx;
        if (kHosyondTouchInvertY) sy = (float)(kHosyondHeight - 1) - sy;

        tp.x = std::max(0.0f, std::min(sx, (float)(kHosyondWidth - 1)));
        tp.y = std::max(0.0f, std::min(sy, (float)(kHosyondHeight - 1)));
        tp.pressure = r.weight;
        tp.touched = true;

        /* RAW alongside mapped: acceptance row 4 is "a reported
         * coordinate matches where he pressed, corners included", and
         * when it does not, this line is what says which of the three
         * orientation flags to flip. */
        ESP_LOGI(TAG, "touch raw=(%u,%u) -> screen=(%.0f,%.0f) w=%u pts=%u",
                 r.raw_x, r.raw_y, tp.x, tp.y, r.weight, r.points);
        return tp;
    }

private:
    FT6336 _ft;
    bool _ok = false;
};

/* ------------------------------------------------------------------ */
/* The profile                                                         */
/* ------------------------------------------------------------------ */

class HosyondS3Profile : public BoardProfile {
public:
    const char* name() const override {
        return "hosyond-s3 (ES3C28P, ILI9341V 240x320 IPS, FT6336G touch)";
    }

    PanelGeometry geometry() const override {
        return PanelGeometry{kHosyondWidth, kHosyondHeight, PanelShape::Rect};
    }

    DisplayDriver& display() override { return _display; }
    TouchDriver* touch() override { return &_touch; }

private:
    HosyondDisplay _display;
    HosyondTouch _touch;
};

BoardProfile& board_profile() {
    static HosyondS3Profile profile;
    return profile;
}
