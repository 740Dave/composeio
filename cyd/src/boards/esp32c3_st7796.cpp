/*
 * boards/esp32c3_st7796.cpp — Board profile: ESP32-C3 Super Mini +
 * LCDWIKI MSP3525/MSP3526 — ST7796S 480x320 IPS over SPI, no touch.
 *
 * Spec: [SPEC:CXC-BRINGUP], [SPEC:CXC-BOARD-HW], [CXC-ER-BOARDS],
 * [CXC-RM-M5] board seam.
 *
 * The FOURTH SPI panel in the tree and the first that is genuinely
 * LANDSCAPE.  What is new here versus the three precedents:
 *
 *   - ST7796S, not ILI9341 and not ST7789T — a third controller family
 *     with its own extended-command UNLOCK/LOCK bracket (0xF0 C3/96 …
 *     0xF0 3C/69).  Drop either half and every register written between
 *     them is silently a no-op.
 *   - 480 WIDE.  The widest panel in the fleet, on the SMALLEST RAM
 *     budget.  A full frame is 307,200 B on a part with ~400 KB of SRAM
 *     shared with IDF, FreeRTOS, WiFi and TWAI — so this is emphatically
 *     a BAND-tier board: 20 bands of 15,360 B.
 *   - NO GRAM OFFSET.  The ST7796S GRAM is exactly this glass (320x480),
 *     unlike the C6's 172-of-240.  Stated in the panel header rather
 *     than left implicit, because "is there an offset?" is a question
 *     that has to be ASKED on every new controller.
 *   - NO TOUCH, and not because the module lacks it.  An FT6336U is
 *     fitted; the C3 has no pins left (see the occupancy table in
 *     esp32c3_st7796_panel.h).  touch() returns nullptr — the Qualia and
 *     C6 precedent.
 *   - THE DISPLAY SHARES THE BOARD WITH TWAI.  GPIO5/6 are CAN and are
 *     never touched here; this profile claims 0,1,3,4,7,10 and no more.
 *
 * Every panel constant lives in include/esp32c3_st7796_panel.h.
 */

#include "board_profile.h"
#include "esp32c3_st7796_panel.h"

#include <algorithm>
#include <cstring>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "c3_st7796";

static constexpr ledc_mode_t      kBlMode  = LEDC_LOW_SPEED_MODE;
static constexpr ledc_timer_t     kBlTimer = LEDC_TIMER_0;
static constexpr ledc_channel_t   kBlCh    = LEDC_CHANNEL_0;
static constexpr ledc_timer_bit_t kBlRes   = LEDC_TIMER_8_BIT;

/* ------------------------------------------------------------------ */
/* Display: ST7796S over SPI2_HOST                                     */
/* ------------------------------------------------------------------ */

class C3St7796Display : public DisplayDriver {
public:
    void init() override {
        ESP_LOGI(TAG, "ST7796S bring-up — SCLK=%d MOSI=%d CS=%d DC=%d "
                 "RST=%d BL=%d, %dx%d landscape, no GRAM offset",
                 kC3LcdPinSCLK, kC3LcdPinMOSI, kC3LcdPinCS, kC3LcdPinDC,
                 kC3LcdPinRST, kC3LcdPinBL, kC3LcdWidth, kC3LcdHeight);
        ESP_LOGW(TAG, "GPIO%d/%d are TWAI (CAN) on this board and are NOT "
                 "touched here; GPIO2/8/9 are strapping pins.  The six pins "
                 "above are every remaining free GPIO — there is no slack.",
                 kC3LcdPinCanTx, kC3LcdPinCanRx);

        init_gpio();
        init_spi();
        init_backlight();
        hardware_reset();
        init_sequence();

        ESP_LOGI(TAG, "ST7796S ready — %dx%d, MADCTL 0x%02X (bit3 BGR SET; "
                 "MV landscape), inversion cmd 0x%02X (IPS), SPI %d Hz "
                 "(datasheet TSCYCW 66 ns => 15.15 MHz ceiling), backlight "
                 "duty %u/255 active-HIGH",
                 kC3LcdWidth, kC3LcdHeight, kC3LcdMadctl, kC3LcdInvCmd,
                 kC3LcdSpiFreqHz, (unsigned)kC3LcdBacklightDefault);
        ESP_LOGW(TAG, "COLOUR ORDER, RGB565 BYTE ORDER, INVERSION and "
                 "BACKLIGHT cannot be self-verified: every readback on this "
                 "device decodes the same buffer with the same assumption "
                 "that wrote it.  The boot splash carries LABELLED pure "
                 "RED/GREEN/BLUE/WHITE/GREY bands for the human tier "
                 "([CXC-BOARD-HW-SELF-CONFIRMING]).");

        /* Display ON is issued AFTER the init table so nothing partially
         * configured is ever shown.  The vendor's own order (0x21 then
         * 0x29) is preserved; only the pin/delay-bearing steps live out
         * here rather than in the data table. */
        send_command(0x29);
        vTaskDelay(pdMS_TO_TICKS(50));
    }

    void transfer_band(const uint16_t* band_buf,
                       uint16_t y_start, uint16_t y_end) override {
        if (!band_buf || y_start > y_end || y_end >= kC3LcdHeight) return;

        set_address_window(0, y_start, kC3LcdWidth - 1, y_end);

        size_t total_bytes =
            (size_t)(y_end - y_start + 1) * kC3LcdWidth * sizeof(uint16_t);
        const uint8_t* src = reinterpret_cast<const uint8_t*>(band_buf);

        gpio_set_level((gpio_num_t)kC3LcdPinDC, 1);      /* data mode */

        while (total_bytes > 0) {
            size_t chunk = std::min(total_bytes, kC3LcdMaxChunkBytes);
            spi_transaction_t txn = {};
            txn.length = chunk * 8;
            txn.tx_buffer = src;
            esp_err_t err = spi_device_polling_transmit(_spi, &txn);
            if (err != ESP_OK) {
                /* ⚠ A SUCCESS HERE PROVES ALMOST NOTHING.  SPI writes are
                 * fire-and-forget: the panel never answers, so ESP_OK means
                 * the DMA engine accepted the buffer and nothing more.  The
                 * classic CYD rendered flawlessly, every write ESP_OK, over
                 * a panel frozen on frame 0.  This log is for the case where
                 * even that much fails. */
                ESP_LOGE(TAG, "SPI transfer failed at y=%u: %s",
                         (unsigned)y_start, esp_err_to_name(err));
                return;
            }
            src += chunk;
            total_bytes -= chunk;
        }
    }

    void set_backlight(uint8_t brightness) override {
        /* Active HIGH: duty IS brightness, and there is NO clamp.
         *
         * Deliberate, and not an oversight.  The Waveshare C6 profile
         * clamps to 50% because Waveshare publish a panel-LIFETIME
         * directive for that specific glass.  THIS vendor's documented
         * normal operating condition is the LED pin left UNCONNECTED —
         * i.e. pulled to 100% before any firmware runs — so a clamp here
         * would be a number invented on a bench, and copying the C6's
         * would be cargo cult.  If this 3.5" backlight runs hot, change
         * kC3LcdBacklightDefault; do not add a limit no evidence
         * supports. */
        ledc_set_duty(kBlMode, kBlCh, brightness);
        ledc_update_duty(kBlMode, kBlCh);
    }

private:
    spi_device_handle_t _spi = nullptr;

    void init_gpio() {
        gpio_config_t io = {};
        io.pin_bit_mask = (1ULL << kC3LcdPinDC) | (1ULL << kC3LcdPinRST);
        io.mode = GPIO_MODE_OUTPUT;
        io.pull_up_en = GPIO_PULLUP_DISABLE;
        io.pull_down_en = GPIO_PULLDOWN_DISABLE;
        io.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&io);
        gpio_set_level((gpio_num_t)kC3LcdPinRST, 1);   /* deassert */

        /* NO SD CS to drive.  Module pin 14 (SD_CS) is unconnected and the
         * module carries its own 10K pull-up (R8) to VCC3.3, so an inserted
         * card stays deselected with no firmware involvement.  The classic
         * CYD's shared-SPI hazard — an SD teardown resetting the DMA channel
         * the display relies on, after which every write still returns
         * ESP_OK — is structurally impossible here: there is no SD bus and
         * no pin left to give one. */
    }

    void init_spi() {
        spi_bus_config_t bus = {};
        bus.mosi_io_num = kC3LcdPinMOSI;
        bus.miso_io_num = kC3LcdPinMISO;        /* -1: write-only panel */
        bus.sclk_io_num = kC3LcdPinSCLK;
        bus.quadwp_io_num = -1;
        bus.quadhd_io_num = -1;
        bus.max_transfer_sz = (int)kC3LcdMaxChunkBytes;

        /* GDMA on the C3 — channels are driver-allocated, so AUTO (the
         * ESP32-era explicit-channel idiom does not apply).  SPI2_HOST is
         * the C3's only general-purpose SPI host; none of these six pins is
         * an IOMUX SPI pin, so the transfer routes through the GPIO matrix.
         * That is fine at 10 MHz and is one more reason not to overclock. */
        ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

        spi_device_interface_config_t dev = {};
        dev.clock_speed_hz = kC3LcdSpiFreqHz;
        dev.mode = 0;                            /* CPOL=0 CPHA=0, MSB first */
        dev.spics_io_num = kC3LcdPinCS;
        dev.queue_size = 7;
        dev.flags = SPI_DEVICE_NO_DUMMY;

        ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &_spi));

        int actual = 0;
        if (spi_device_get_actual_freq(_spi, &actual) == ESP_OK) {
            /* The REQUESTED clock and the ACHIEVED clock are different
             * numbers — only exact 80 MHz dividers are reachable — and the
             * one that matters against the datasheet is the achieved one.
             * Print it rather than the request, so nobody checks the wrong
             * number against the 15.15 MHz ceiling. */
            ESP_LOGI(TAG, "SPI2 requested %d Hz, ACHIEVED %d kHz "
                     "(ST7796S write-cycle ceiling ~15150 kHz)",
                     kC3LcdSpiFreqHz, actual);
        }
    }

    void init_backlight() {
        ledc_timer_config_t t = {};
        t.speed_mode = kBlMode;
        t.timer_num = kBlTimer;
        t.duty_resolution = kBlRes;
        t.freq_hz = kC3LcdBacklightFreqHz;
        t.clk_cfg = LEDC_AUTO_CLK;
        ledc_timer_config(&t);

        ledc_channel_config_t c = {};
        c.speed_mode = kBlMode;
        c.channel = kBlCh;
        c.timer_sel = kBlTimer;
        c.intr_type = LEDC_INTR_DISABLE;
        c.gpio_num = kC3LcdPinBL;
        c.duty = kC3LcdBacklightDefault;
        c.hpoint = 0;
        ledc_channel_config(&c);

        /* ⚠ THE LIT PANEL IS NOT A SIGNAL.  A 10K pull-up on the LED line
         * holds the BSS138 gate high, so this backlight is on at full
         * brightness from the moment the module has 5V and BEFORE any code
         * runs.  "The screen is lit" therefore proves the power rail and
         * says nothing at all about the MCU, the SPI wiring or this
         * firmware.  Claiming the pin here makes brightness OWNED rather
         * than defaulted. */
        ESP_LOGW(TAG, "backlight claimed on GPIO%d (LEDC, active HIGH).  It "
                 "was ALREADY ON via the module's 10K pull-up before this "
                 "firmware ran — a lit panel proves the 5V rail, not the MCU.",
                 kC3LcdPinBL);
    }

    /* A genuine pin toggle — this board, unlike the Hosyond, has one, so
     * init is correct from a warm esp_restart() as well as a cold boot.
     * Timing is the vendor's LCD_RESET(): 100 ms low, 50 ms high. */
    void hardware_reset() {
        gpio_set_level((gpio_num_t)kC3LcdPinRST, 0);
        vTaskDelay(pdMS_TO_TICKS(kC3LcdResetLowMs));
        gpio_set_level((gpio_num_t)kC3LcdPinRST, 1);
        vTaskDelay(pdMS_TO_TICKS(kC3LcdResetHighMs));
    }

    void send_command(uint8_t cmd) {
        gpio_set_level((gpio_num_t)kC3LcdPinDC, 0);
        spi_transaction_t txn = {};
        txn.length = 8;
        txn.tx_buffer = &cmd;
        spi_device_polling_transmit(_spi, &txn);
    }

    void send_data(const uint8_t* data, size_t len) {
        if (len == 0) return;
        gpio_set_level((gpio_num_t)kC3LcdPinDC, 1);
        spi_transaction_t txn = {};
        txn.length = len * 8;
        txn.tx_buffer = data;
        spi_device_polling_transmit(_spi, &txn);
    }

    void init_sequence() {
        /* Sleep out first — the panel powers up asleep.  Held out of the
         * table because the vendor's 120 ms settle would be obscured by the
         * table's per-row delay column. */
        send_command(0x11);
        vTaskDelay(pdMS_TO_TICKS(120));

        for (int i = 0; i < kC3LcdInitSeqLen; ++i) {
            const C3LcdInitCmd& c = kC3LcdInitSeq[i];
            send_command(c.cmd);
            if (c.len) send_data(c.args, c.len);
            if (c.delay_ms) vTaskDelay(pdMS_TO_TICKS(c.delay_ms));
        }
    }

    /* No GRAM bias on this panel (the offsets are 0 and are named in the
     * panel header so the QUESTION is visible).  With MADCTL's MV bit set
     * the address counter follows the transformed axes: CASET spans
     * 0..479 and RASET 0..319. */
    void set_address_window(uint16_t x0, uint16_t y0,
                            uint16_t x1, uint16_t y1) {
        uint16_t cx0 = x0 + kC3LcdColOffset;
        uint16_t cx1 = x1 + kC3LcdColOffset;
        uint16_t ry0 = y0 + kC3LcdRowOffset;
        uint16_t ry1 = y1 + kC3LcdRowOffset;

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

class C3St7796Profile : public BoardProfile {
public:
    const char* name() const override {
        return "esp32c3-supermini-st7796 (ESP32-C3, ST7796S 480x320 "
               "landscape, no touch, CAN-fed)";
    }

    PanelGeometry geometry() const override {
        return PanelGeometry{kC3LcdWidth, kC3LcdHeight, PanelShape::Rect};
    }

    DisplayDriver& display() override { return _display; }

    /* An FT6336U IS fitted on this module.  It is unusable here because
     * the C3 has no pins left, NOT because the hardware lacks it — see the
     * occupancy table in esp32c3_st7796_panel.h.  Adding touch means giving
     * up CAN or the UART console. */
    TouchDriver* touch() override { return nullptr; }

private:
    C3St7796Display _display;
};

BoardProfile& board_profile() {
    static C3St7796Profile profile;
    return profile;
}
