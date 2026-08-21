/*
 * mcp23017.cpp — see mcp23017.h.  ESP-IDF i2c_master (5.x), same idiom as
 * touch_ft6336.cpp and boards/qualia_rgb666.cpp.
 */

#ifdef CXC_RACK_TRIPLE_GAUGE_APP

#include "mcp23017.h"
#include "rack_triple_gauge_panel.h"

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "rom/ets_sys.h"

static const char* TAG = "mcp23017";

static i2c_master_bus_handle_t s_bus = nullptr;
static i2c_master_dev_handle_t s_dev = nullptr;
static uint8_t  s_addr = kG3McpAddr;
static uint32_t s_errors = 0;
static const int kTimeoutMs = 50;

uint32_t mcp23017_error_count() { return s_errors; }

/* ------------------------------------------------------------------ */
/* Bus recovery — nine clocks, BEFORE the driver exists                */
/* ------------------------------------------------------------------ */
bool mcp23017_bus_recover(int sda_gpio, int scl_gpio) {
    gpio_config_t io = {};
    io.pin_bit_mask = (1ULL << sda_gpio) | (1ULL << scl_gpio);
    io.mode = GPIO_MODE_INPUT_OUTPUT_OD;      /* open drain + readback */
    io.pull_up_en = GPIO_PULLUP_ENABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&io);
    gpio_set_level((gpio_num_t)sda_gpio, 1);
    gpio_set_level((gpio_num_t)scl_gpio, 1);
    ets_delay_us(10);

    const int sda_idle = gpio_get_level((gpio_num_t)sda_gpio);
    if (sda_idle == 1) {
        /* Nothing to recover.  Reported at INFO because "the bus was
         * already fine" is the expected case and must not read like a
         * repair that happened. */
        ESP_LOGI(TAG, "bus recovery: SDA already idle HIGH — nothing stuck");
        return true;
    }

    ESP_LOGW(TAG, "bus recovery: SDA held LOW at idle — a slave is stuck "
             "mid-byte; clocking 9 times");
    for (int i = 0; i < 9; i++) {
        gpio_set_level((gpio_num_t)scl_gpio, 0); ets_delay_us(5);
        gpio_set_level((gpio_num_t)scl_gpio, 1); ets_delay_us(5);
        if (gpio_get_level((gpio_num_t)sda_gpio) == 1) break;
    }
    /* STOP */
    gpio_set_level((gpio_num_t)sda_gpio, 0); ets_delay_us(5);
    gpio_set_level((gpio_num_t)scl_gpio, 1); ets_delay_us(5);
    gpio_set_level((gpio_num_t)sda_gpio, 1); ets_delay_us(5);

    const bool ok = gpio_get_level((gpio_num_t)sda_gpio) == 1;
    ESP_LOGW(TAG, "bus recovery: SDA %s after 9 clocks",
             ok ? "RELEASED" : "*** STILL HELD LOW — this is not a stuck "
                              "slave; suspect a short, a dead pull-up, or "
                              "an unpowered expander ***");
    return ok;
}

/* ------------------------------------------------------------------ */
static bool wr(const uint8_t* buf, size_t n, const char* what) {
    esp_err_t e = i2c_master_transmit(s_dev, buf, n, kTimeoutMs);
    if (e != ESP_OK) {
        s_errors++;
        ESP_LOGE(TAG, "%s failed: %s", what, esp_err_to_name(e));
        return false;
    }
    return true;
}

bool mcp23017_init(int sda_gpio, int scl_gpio, uint32_t hz, uint8_t addr,
                   uint8_t iodir_a, uint8_t iodir_b) {
    s_addr = addr;
    s_errors = 0;

    mcp23017_bus_recover(sda_gpio, scl_gpio);

    i2c_master_bus_config_t bus_cfg = {};
    bus_cfg.i2c_port = -1;
    bus_cfg.sda_io_num = (gpio_num_t)sda_gpio;
    bus_cfg.scl_io_num = (gpio_num_t)scl_gpio;
    bus_cfg.clk_source = I2C_CLK_SRC_DEFAULT;
    bus_cfg.glitch_ignore_cnt = 7;
    /* The BOARD carries 4k7 pull-ups (R_SDA/R_SCL to +3V3), so the internal
     * ones are not needed and are enabled only as belt-and-braces for a
     * bench rig with the expander on flying leads.  ~45k in parallel with
     * 4k7 shifts nothing. */
    bus_cfg.flags.enable_internal_pullup = true;

    esp_err_t e = i2c_new_master_bus(&bus_cfg, &s_bus);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "i2c_new_master_bus (SDA=%d SCL=%d): %s",
                 sda_gpio, scl_gpio, esp_err_to_name(e));
        return false;
    }

    e = i2c_master_probe(s_bus, addr, kTimeoutMs);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "*** NO DEVICE AT 0x%02X *** (%s).  Ladder rung 3 "
                 "failed: check U1 is seated the right way round, that its "
                 "VDD rail is up, and that A0/A1/A2 are grounded.",
                 addr, esp_err_to_name(e));
        return false;
    }

    i2c_device_config_t dev_cfg = {};
    dev_cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    dev_cfg.device_address = addr;
    dev_cfg.scl_speed_hz = hz;
    e = i2c_master_bus_add_device(s_bus, &dev_cfg, &s_dev);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "i2c_master_bus_add_device: %s", esp_err_to_name(e));
        return false;
    }

    /* 1. IOCON.  Written at BOTH 0x0A and 0x0B because those two addresses
     *    swap meaning with the BANK bit, and we do not know which mode a
     *    warm part is in.  0x00 = BANK 0, SEQOP 0 (auto-increment ON) —
     *    auto-increment is what lets one transaction carry GPIOA and
     *    GPIOB, which is what makes a step cost one transaction for three
     *    motors instead of three. */
    { uint8_t b[2] = { kG3RegIOCON_A, 0x00 }; if (!wr(b, 2, "IOCON@0x0A")) return false; }
    { uint8_t b[2] = { kG3RegIOCON_B, 0x00 }; if (!wr(b, 2, "IOCON@0x0B")) return false; }

    /* 2. OLAT BEFORE IODIR.  See rack_triple_gauge_panel.h §2 — this
     *    ordering is what makes the pins go float -> driven-LOW rather than
     *    float -> whatever the latch held, which on a warm esp_restart() is
     *    the PREVIOUS step pattern, i.e. a coil kicking at reset. */
    { uint8_t b[3] = { kG3RegOLATA, 0x00, 0x00 }; if (!wr(b, 3, "OLATA/B")) return false; }

    /* 3. Direction.  iodir_b keeps GPB4-7 as INPUTS — they leave the chip
     *    on J_EXP_MCP and are not ours to drive. */
    { uint8_t b[3] = { kG3RegIODIRA, iodir_a, iodir_b };
      if (!wr(b, 3, "IODIRA/B")) return false; }

    /* 4. READ IT BACK.  The one check here that is not self-confirming. */
    uint8_t ra = 0xFF, rb = 0xFF;
    if (!mcp23017_read_iodir(&ra, &rb)) return false;
    if (ra != iodir_a || rb != iodir_b) {
        ESP_LOGE(TAG, "*** IODIR READBACK DISAGREES: wrote %02X/%02X, read "
                 "%02X/%02X.  The writes returned ESP_OK, so something on "
                 "the bus is ACKing that is not this part. ***",
                 iodir_a, iodir_b, ra, rb);
        s_errors++;
        return false;
    }

    ESP_LOGI(TAG, "MCP23017 @0x%02X ready — IODIRA=%02X IODIRB=%02X "
             "(GPB4-7 left as INPUTS: they are J_EXP_MCP)", addr, ra, rb);
    return true;
}

bool mcp23017_write_ports(uint8_t gpioa, uint8_t gpiob) {
    if (!s_dev) return false;
    uint8_t b[3] = { kG3RegGPIOA, gpioa, gpiob };
    return wr(b, 3, "GPIOA/B");
}

bool mcp23017_read_iodir(uint8_t* iodir_a, uint8_t* iodir_b) {
    if (!s_dev) return false;
    uint8_t reg = kG3RegIODIRA, rx[2] = { 0, 0 };
    esp_err_t e = i2c_master_transmit_receive(s_dev, &reg, 1, rx, 2,
                                             kTimeoutMs);
    if (e != ESP_OK) {
        s_errors++;
        ESP_LOGE(TAG, "IODIR readback: %s", esp_err_to_name(e));
        return false;
    }
    *iodir_a = rx[0];
    *iodir_b = rx[1];
    return true;
}

#endif /* CXC_RACK_TRIPLE_GAUGE_APP */
