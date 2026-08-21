/*
 * boards/waveshare_c6_lcd147.cpp — Board profile: Waveshare
 * ESP32-C6-LCD-1.47 — ST7789T 172x320 IPS over SPI, no touch.
 *
 * Spec: [CXC-BRINGUP], [CXC-ER-BOARDS], [CXC-RM-M5] board seam.
 *
 * The THIRD display architecture in the tree and the first on RISC-V
 * silicon that actually drives glass (the C3 profile was a DSL
 * conformance target only).  What is new here versus the two SPI
 * precedents:
 *
 *   - ST7789T, not ILI9341 — a different controller family with its
 *     own init table and its own register quirks.
 *   - A NON-240 panel width.  172 columns on a 240-column GRAM means
 *     every column address carries a +34 offset; get it wrong and the
 *     whole image slides sideways.  This is the first board where
 *     PanelGeometry.width is not 240, which is exactly the sort of
 *     assumption a board seam exists to keep out of the widgets.
 *   - A REAL reset pin (contrast the Hosyond, whose panel reset is tied
 *     to CHIP_PU), so init performs a genuine hardware reset.
 *   - NO TOUCH — touch() returns nullptr, the Qualia precedent.
 *   - Single-core RISC-V with NO PSRAM, so this is a BAND-tier board
 *     like the classic CYD: kBandHeight stays 16 and the band buffer is
 *     5,504 B of internal DMA-capable SRAM.
 *
 * Every panel constant lives in include/waveshare_c6_panel.h.
 */

#include "board_profile.h"
#include "waveshare_c6_panel.h"

#include <algorithm>
#include <cstring>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/rmt_tx.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "ws_c6_lcd147";

/* ------------------------------------------------------------------ */
/* WS2812 — extinguish it, deliberately                                */
/* ------------------------------------------------------------------ */

/* A WS2812 LATCHES its last colour and holds it until it is given new
 * data or loses power.  So "this profile does not use the RGB LED" does
 * NOT mean the LED is off — it means the LED keeps whatever the previous
 * firmware left in it.  On a freshly reflashed board still powered over
 * USB, that is the Waveshare factory demo's colour, burning at full
 * brightness forever (David spotted it lit, 2026-08-11).
 *
 * On a panel the vendor already asks us to run at <=50% brightness for
 * longevity, leaving an un-owned LED on is the wrong default.  We claim
 * the pin exactly once, write 24 zero bits, and hand it back.
 *
 * RMT rather than bit-bang: WS2812 bit timing is ~0.4/0.85 us and the
 * 1 us granularity of a busy-wait cannot hit it reliably. */
static void ws2812_extinguish(int gpio) {
    rmt_channel_handle_t chan = nullptr;
    rmt_tx_channel_config_t cfg = {};
    cfg.gpio_num = (gpio_num_t)gpio;
    cfg.clk_src = RMT_CLK_SRC_DEFAULT;
    cfg.resolution_hz = 10 * 1000 * 1000;      /* 0.1 us per tick */
    cfg.mem_block_symbols = 64;
    cfg.trans_queue_depth = 1;
    if (rmt_new_tx_channel(&cfg, &chan) != ESP_OK) {
        ESP_LOGW(TAG, "RGB LED: RMT channel unavailable — LED left as-is");
        return;
    }

    rmt_encoder_handle_t enc = nullptr;
    rmt_copy_encoder_config_t enc_cfg = {};
    if (rmt_new_copy_encoder(&enc_cfg, &enc) != ESP_OK) {
        rmt_del_channel(chan);
        return;
    }

    /* 24 bits of ZERO — a WS2812 "0" is a short high then a long low. */
    rmt_symbol_word_t bits[24];
    for (int i = 0; i < 24; ++i) {
        bits[i].level0 = 1; bits[i].duration0 = 4;   /* 0.4 us high */
        bits[i].level1 = 0; bits[i].duration1 = 9;   /* 0.9 us low  */
    }

    rmt_transmit_config_t tx = {};
    if (rmt_enable(chan) == ESP_OK) {
        rmt_transmit(chan, enc, bits, sizeof(bits), &tx);
        rmt_tx_wait_all_done(chan, 100);
        esp_rom_delay_us(100);                 /* >50 us latch */
        rmt_disable(chan);
        ESP_LOGI(TAG, "RGB LED (GPIO%d) driven OFF — a WS2812 holds its "
                 "last colour, so not touching it would leave the vendor "
                 "demo's LED burning", gpio);
    }

    rmt_del_encoder(enc);
    rmt_del_channel(chan);                     /* give the pin back */
}

/* One full-width scanline is 172 * 2 = 344 B; the band is 16 rows =
 * 5,504 B.  Chunk well above that so a band is a single transaction. */
static constexpr size_t kMaxChunkBytes = 8192;

static constexpr ledc_mode_t      kBlMode  = LEDC_LOW_SPEED_MODE;
static constexpr ledc_timer_t     kBlTimer = LEDC_TIMER_0;
static constexpr ledc_channel_t   kBlCh    = LEDC_CHANNEL_0;
static constexpr ledc_timer_bit_t kBlRes   = LEDC_TIMER_8_BIT;

/* ------------------------------------------------------------------ */
/* Display: ST7789T over SPI2_HOST                                     */
/* ------------------------------------------------------------------ */

class WsC6Display : public DisplayDriver {
public:
    void init() override {
        ESP_LOGI(TAG, "ST7789T bring-up — SCLK=%d MOSI=%d CS=%d DC=%d "
                 "RST=%d BL=%d, %dx%d, GRAM column offset %d",
                 kWsC6PinSCLK, kWsC6PinMOSI, kWsC6PinCS, kWsC6PinDC,
                 kWsC6PinRST, kWsC6PinBL, kWsC6Width, kWsC6Height,
                 kWsC6ColOffset);

        init_gpio();
        init_spi();
        init_backlight();
        hardware_reset();
        init_sequence();

        /* Not display bring-up, but it belongs to the board: an
         * unclaimed WS2812 is not an OFF WS2812 (see above). */
        ws2812_extinguish(kWsC6PinRgbLed);

        ESP_LOGI(TAG, "ST7789T ready — %dx%d portrait, MADCTL 0x%02X, "
                 "inversion cmd 0x%02X, SPI %d Hz, backlight ON",
                 kWsC6Width, kWsC6Height, kWsC6Madctl, kWsC6InvCmd,
                 kWsC6SpiFreqHz);
    }

    void transfer_band(const uint16_t* band_buf,
                       uint16_t y_start, uint16_t y_end) override {
        if (!band_buf || y_start > y_end || y_end >= kWsC6Height) return;

        set_address_window(0, y_start, kWsC6Width - 1, y_end);

        size_t total_bytes =
            (size_t)(y_end - y_start + 1) * kWsC6Width * sizeof(uint16_t);
        const uint8_t* src = reinterpret_cast<const uint8_t*>(band_buf);

        gpio_set_level((gpio_num_t)kWsC6PinDC, 1);      /* data mode */

        while (total_bytes > 0) {
            size_t chunk = std::min(total_bytes, kMaxChunkBytes);
            spi_transaction_t txn = {};
            txn.length = chunk * 8;
            txn.tx_buffer = src;
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
        /* Active HIGH: duty IS brightness — but CLAMPED.  The vendor
         * requires <= 50% for panel longevity (see kWsC6BacklightMax);
         * enforcing it here means no scene, config or future caller can
         * quietly outlive the panel.  Warn ONCE so the clamp is visible
         * without spamming a repaint loop. */
        if (brightness > kWsC6BacklightMax) {
            if (!_bl_clamp_warned) {
                _bl_clamp_warned = true;
                ESP_LOGW(TAG, "backlight %u clamped to %u — this panel is "
                         "rated for <=50%% brightness in sustained use "
                         "(vendor wiki + demo duty 4000/8191)",
                         brightness, kWsC6BacklightMax);
            }
            brightness = kWsC6BacklightMax;
        }
        ledc_set_duty(kBlMode, kBlCh, brightness);
        ledc_update_duty(kBlMode, kBlCh);
    }

private:
    spi_device_handle_t _spi = nullptr;
    bool _bl_clamp_warned = false;

    void init_gpio() {
        gpio_config_t io = {};
        io.pin_bit_mask = (1ULL << kWsC6PinDC) | (1ULL << kWsC6PinRST);
        io.mode = GPIO_MODE_OUTPUT;
        io.pull_up_en = GPIO_PULLUP_DISABLE;
        io.pull_down_en = GPIO_PULLDOWN_DISABLE;
        io.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&io);
        gpio_set_level((gpio_num_t)kWsC6PinRST, 1);   /* deassert */

        /* SD CS HIGH — the SD slot sits on THIS SPI bus (see the hazard
         * note in waveshare_c6_panel.h).  We never initialize an SD bus,
         * but an inserted card must stay deselected so it cannot drive
         * MISO or otherwise disturb the panel's transactions. */
        gpio_config_t sd = {};
        sd.pin_bit_mask = (1ULL << kWsC6PinSdCS);
        sd.mode = GPIO_MODE_OUTPUT;
        sd.pull_up_en = GPIO_PULLUP_ENABLE;
        sd.pull_down_en = GPIO_PULLDOWN_DISABLE;
        sd.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&sd);
        gpio_set_level((gpio_num_t)kWsC6PinSdCS, 1);
    }

    void init_spi() {
        spi_bus_config_t bus = {};
        bus.mosi_io_num = kWsC6PinMOSI;
        bus.miso_io_num = kWsC6PinMISO;         /* -1: write-only panel */
        bus.sclk_io_num = kWsC6PinSCLK;
        bus.quadwp_io_num = -1;
        bus.quadhd_io_num = -1;
        bus.max_transfer_sz = kMaxChunkBytes;

        /* GDMA on the C6 — channels are driver-allocated, so AUTO (the
         * ESP32-era explicit-channel idiom does not apply). */
        ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

        spi_device_interface_config_t dev = {};
        dev.clock_speed_hz = kWsC6SpiFreqHz;
        dev.mode = 0;
        dev.spics_io_num = kWsC6PinCS;
        dev.queue_size = 7;
        dev.flags = SPI_DEVICE_NO_DUMMY;

        ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &_spi));
    }

    void init_backlight() {
        ledc_timer_config_t t = {};
        t.speed_mode = kBlMode;
        t.timer_num = kBlTimer;
        t.duty_resolution = kBlRes;
        t.freq_hz = kWsC6BacklightFreqHz;
        t.clk_cfg = LEDC_AUTO_CLK;
        ledc_timer_config(&t);

        ledc_channel_config_t c = {};
        c.speed_mode = kBlMode;
        c.channel = kBlCh;
        c.timer_sel = kBlTimer;
        c.intr_type = LEDC_INTR_DISABLE;
        c.gpio_num = kWsC6PinBL;
        c.duty = kWsC6BacklightDefault;
        c.hpoint = 0;
        ledc_channel_config(&c);
    }

    /* A genuine pin toggle — this board, unlike the Hosyond, has one. */
    void hardware_reset() {
        gpio_set_level((gpio_num_t)kWsC6PinRST, 0);
        vTaskDelay(pdMS_TO_TICKS(kWsC6ResetLowMs));
        gpio_set_level((gpio_num_t)kWsC6PinRST, 1);
        vTaskDelay(pdMS_TO_TICKS(kWsC6ResetHighMs));
    }

    void send_command(uint8_t cmd) {
        gpio_set_level((gpio_num_t)kWsC6PinDC, 0);
        spi_transaction_t txn = {};
        txn.length = 8;
        txn.tx_buffer = &cmd;
        spi_device_polling_transmit(_spi, &txn);
    }

    void send_data(const uint8_t* data, size_t len) {
        if (len == 0) return;
        gpio_set_level((gpio_num_t)kWsC6PinDC, 1);
        spi_transaction_t txn = {};
        txn.length = len * 8;
        txn.tx_buffer = data;
        spi_device_polling_transmit(_spi, &txn);
    }

    void init_sequence() {
        /* Sleep out first — the panel powers up asleep.  Held out of the
         * table because it needs a 100 ms settle the table's per-row
         * delay would obscure. */
        send_command(0x11);
        vTaskDelay(pdMS_TO_TICKS(100));

        for (int i = 0; i < kWsC6InitSeqLen; ++i) {
            const WsC6InitCmd& c = kWsC6InitSeq[i];
            send_command(c.cmd);
            if (c.len) send_data(c.args, c.len);
            if (c.delay_ms) vTaskDelay(pdMS_TO_TICKS(c.delay_ms));
        }
    }

    /* THE OFFSET LIVES HERE and nowhere else.  Scene coordinates are
     * 0..171; GRAM columns are 34..205. */
    void set_address_window(uint16_t x0, uint16_t y0,
                            uint16_t x1, uint16_t y1) {
        uint16_t cx0 = x0 + kWsC6ColOffset;
        uint16_t cx1 = x1 + kWsC6ColOffset;
        uint16_t ry0 = y0 + kWsC6RowOffset;
        uint16_t ry1 = y1 + kWsC6RowOffset;

        send_command(0x2A);                     /* column address set */
        {
            uint8_t d[] = {(uint8_t)(cx0 >> 8), (uint8_t)(cx0 & 0xFF),
                           (uint8_t)(cx1 >> 8), (uint8_t)(cx1 & 0xFF)};
            send_data(d, sizeof(d));
        }
        send_command(0x2B);                     /* row address set */
        {
            uint8_t d[] = {(uint8_t)(ry0 >> 8), (uint8_t)(ry0 & 0xFF),
                           (uint8_t)(ry1 >> 8), (uint8_t)(ry1 & 0xFF)};
            send_data(d, sizeof(d));
        }
        send_command(0x2C);                     /* memory write */
    }
};

/* ------------------------------------------------------------------ */
/* The profile                                                         */
/* ------------------------------------------------------------------ */

class WsC6Profile : public BoardProfile {
public:
    const char* name() const override {
        return "waveshare-c6-lcd147 (ESP32-C6, ST7789T 172x320, no touch)";
    }

    PanelGeometry geometry() const override {
        return PanelGeometry{kWsC6Width, kWsC6Height, PanelShape::Rect};
    }

    DisplayDriver& display() override { return _display; }

    /* No touch panel on this board (Qualia precedent). */
    TouchDriver* touch() override { return nullptr; }

private:
    WsC6Display _display;
};

BoardProfile& board_profile() {
    static WsC6Profile profile;
    return profile;
}
