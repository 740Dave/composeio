/*
 * gauge_diag.cpp — the ON-TARGET DIAGNOSTIC for `rack_triple_gauge`.
 * [env:rack_triple_gauge_selftest]   [SPEC:CXC-BRINGUP] [SPEC:CXC-BOARD-HW]
 * [CXC-VISION-EPISTEMICS] [PCB-GAUGE3]
 *
 * ⛔ THIS IS NOT gauge_selftest.cpp.  Two files, two words that sound alike,
 * two DISJOINT jobs, and confusing them is how a board gets declared good on
 * the strength of a test that never touched it:
 *
 *   gauge_selftest.cpp  [env:native_gauge]              runs on the HOST.
 *       Proves the homing STATE MACHINE and the channel-set BINDING
 *       ARITHMETIC.  Touches no silicon.  Would pass identically if the
 *       board were never fabbed — which is exactly what it did.
 *
 *   gauge_diag.cpp      [env:rack_triple_gauge_selftest] runs on the C3.
 *       Proves what the MCU can REACH: the I2C bus, the expander's register
 *       file, all sixteen expander pads, the twelve L293D input lines and
 *       the three backlight channels.  Knows nothing about needles.
 *
 * ⛔ AND IT IS NOT A SUBSTITUTE FOR THE MULTIMETER PASS.  Read
 * docs/board-bringup/rack_triple_gauge-PREFLIGHT.md and do it FIRST, with
 * the power off and no module seated.  A short is a multimeter question; a
 * marginal logic level is a firmware question, and this board has a known
 * one (§4.1 — the MCP23017 sits on +5 V with 3.3 V pull-ups, 200 mV under
 * its guaranteed V_IH).  Rung 3 below exists for precisely that defect and
 * a meter cannot see it.  On 2026-08-12 three rounds of protocol
 * diagnostics ran against a transceiver that had no supply:
 * [CXC-VISION-EPISTEMICS] `rule-power-then-substrate-then-protocol`.
 *
 * ⛔ IT DRIVES NO NEEDLE TO A POSITION AND IT DOES NOT HOME.  Homing slams
 * a needle against a mechanical stop for 5.4 s; against wrong wiring that
 * is a mechanical event, not a diagnostic.  Homing belongs to the real
 * firmware, AFTER this passes.  What this DOES do is energise single coil
 * lines for ~150 ms each, which twitches a needle by up to a step or two.
 * That twitch is the measurement; it is not positioning.
 *
 * ------------------------------------------------------------------------
 * THE DESIGN RULE, and it is the whole point:
 *
 *   EVERY CHECK MUST BE ABLE TO FAIL, AND MUST DISTINGUISH FAILURE MODES
 *   RATHER THAN REPORTING ONE VERDICT.
 *
 * The fleet's canonical disaster is `MCP2515 successfully initialized` — a
 * TRUE statement about SPI that said nothing about the interrupt line,
 * giving a healthy log and zero receives forever.  So every rung here ends
 * with an explicit "PROVED / DID NOT PROVE" pair, three of them carry a
 * deliberate FAULT INJECTOR that makes the check fail on demand (an
 * instrument that can only report success is not an instrument), and no
 * rung reports a boolean where it can report a number.
 *
 * ------------------------------------------------------------------------
 * TWO INDEPENDENT CHAINS, and a stop in one does NOT gate the other:
 *
 *   CHAIN A   GPIO4/5 -> 4k7 pull-ups -> MCP23017 -> 12 lines -> 3x L293D
 *             rungs 2, 3, 4, 5
 *   CHAIN B   GPIO0/1/3 -> 150R -> 2N2222 -> +12V LED strip
 *             rung 6
 *
 * They share the +5 V rail and nothing else — different pins, different
 * parts, a different rail on the load side.  The ladder's stop rule exists
 * so a later rung is never read against an unproven earlier one; chain B is
 * not downstream of chain A, so a dead expander must not suppress a
 * backlight test that would have passed.  Getting this wrong is how a
 * genuine result gets thrown away.
 *
 * ------------------------------------------------------------------------
 * WHY THIS FILE SHARES NO CODE WITH mcp23017.cpp.
 *
 * It re-implements bus recovery, register read and register write instead
 * of linking the driver the real firmware uses.  That is deliberate and it
 * is [CXC-VISION-EPISTEMICS] `rule-self-verification` applied to ourselves:
 * a diagnostic that runs the driver under test cannot see a defect in that
 * driver, because it reads the answer back through the same assumption that
 * wrote it.  An independent implementation is a genuine second opinion.
 * The cost is ~80 duplicated lines; the pin numbers still come from the ONE
 * constants header, so there is no second copy of anything BOARD-specific.
 *
 * WHY printf AND NOT ESP_LOGx FOR THE REPORT BODY.
 * A runtime log level can silently drop lines.  A diagnostic whose FAIL row
 * is subject to a filter is not a diagnostic.  ESP_LOG is used only for the
 * things that are genuinely events.
 */

#ifdef CXC_G3_DIAG_APP

#include "announce.h"                 /* cxc_build_stamp / _fingerprint     */
#include "rack_triple_gauge_panel.h"  /* THE one place board numbers live   */

#include <cstdio>
#include <cstring>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_mac.h"        /* esp_read_mac — the USB serial on a native-USB
                             * part, and the only passive identity check    */
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "rom/ets_sys.h"

static const char* TAG = "g3diag";

/* ================================================================== */
/* Tunables — every one with its N+1 behaviour stated                  */
/* ================================================================== */

/*
 * STRESS LENGTH.  The defect this rung hunts is a logic level 200 mV under
 * threshold, which does not fail cleanly — it fails RARELY.  A run of N
 * pairs can only resolve an error rate down to 1/N, so N is a statement
 * about what "clean" is allowed to mean, and the report prints that floor
 * next to every rate so a zero is never read as "no errors".
 *
 * 10,000 at the OPERATIONAL speed (400 kHz) -> a floor of 100 ppm, ~4 s.
 *  2,000 at the CONTROL speed (100 kHz)     -> a floor of 500 ppm, ~5 s.
 *
 * Asymmetric on purpose: 400 kHz is where the board will actually live and
 * is the number that must be tight; 100 kHz is there only to answer "does
 * slowing the bus fix it?", which is a yes/no with an obvious workaround
 * (lower kG3I2cHz) and does not need the same resolution.
 *
 * At N+1 — a board that passes here and still misbehaves on the bench —
 * raise these two numbers; they are the only thing that has to change, and
 * the runtime scales linearly and visibly.
 */
static constexpr int kStressPairsFast = 10000;
static constexpr int kStressPairsSlow = 2000;
static constexpr uint32_t kI2cSlowHz  = 100000;

/* Injected-fault control lengths.  Small: their job is to prove a counter
 * CAN move, not to measure anything. */
static constexpr int kInjectedFaultPairs = 256;

/*
 * ⚠ GPB4-7 LEAVE THE CHIP ON J_EXP_MCP.  The real firmware keeps them
 * INPUTS (IODIRB = 0xF0) precisely so it never drives four pins belonging
 * to whatever a user plugged in.  Rung 4 asks for all sixteen pads, which
 * means briefly driving them.
 *
 * Set this to false if ANYTHING is connected to J_EXP_MCP.  Leaving it true
 * with a driven peripheral on that header is output contention — invisible
 * to this test, and potentially damaging to the other part.
 */
static constexpr bool kDriveExpansionNibble = true;

/* Dwell for the human-visible rungs.  1500 ms is the figure the fleet
 * already settled on for a bench operator watching a pin (see
 * env:esp32_c3_pin_drive_test): long enough that a DC meter follows it and
 * an eye cannot miss it. */
static constexpr int kEyeDwellMs   = 1500;
static constexpr int kCoilDwellMs  = 150;

/* MCP23017 registers, BANK 0.  The four the real driver uses are already in
 * rack_triple_gauge_panel.h; these are the ones only the diagnostic needs. */
static constexpr uint8_t kRegIPOLA   = 0x02;   /* input polarity — the rung-4
                                                * fault injector           */
static constexpr uint8_t kRegDEFVALA = 0x06;   /* 16 bits of R/W scratch    */
static constexpr uint8_t kRegGPPUA   = 0x0C;

/* ================================================================== */
/* Report plumbing                                                     */
/* ================================================================== */

static int  g_fail_a = 0;      /* chain A failures */
static int  g_fail_b = 0;      /* chain B failures */
static char g_stop_reason[192] = "";

static void rule(void) {
    printf("--------------------------------------------------------------\n");
}
static void head(const char* n, const char* title) {
    printf("\n");
    rule();
    printf("RUNG %s  %s\n", n, title);
    rule();
}
/* Every rung ends with these two.  The second one is not decoration: it is
 * the sentence that stops a true statement about one subsystem being read
 * as a verdict on another. */
static void proved(const char* s)     { printf("  PROVED     : %s\n", s); }
static void not_proved(const char* s) { printf("  DID NOT    : %s\n", s); }

static i2c_master_bus_handle_t s_bus = nullptr;
static i2c_master_dev_handle_t s_dev = nullptr;
static uint32_t s_dev_hz = 0;

/* ================================================================== */
/* Raw I2C — deliberately NOT mcp23017.cpp (see the header note)       */
/* ================================================================== */

static bool dev_open(uint32_t hz) {
    if (s_dev) { i2c_master_bus_rm_device(s_dev); s_dev = nullptr; }
    i2c_device_config_t c = {};
    c.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    c.device_address  = kG3McpAddr;
    c.scl_speed_hz    = hz;
    if (i2c_master_bus_add_device(s_bus, &c, &s_dev) != ESP_OK) return false;
    s_dev_hz = hz;
    return true;
}

/* Write `n` bytes starting at `reg`, relying on the MCP23017's address
 * auto-increment (IOCON.SEQOP = 0), which is what lets one transaction
 * carry both port bytes. */
static esp_err_t reg_write(uint8_t reg, const uint8_t* v, size_t n) {
    uint8_t b[5];
    if (n > 4) return ESP_ERR_INVALID_SIZE;
    b[0] = reg;
    memcpy(b + 1, v, n);
    return i2c_master_transmit(s_dev, b, n + 1, 50);
}
static esp_err_t reg_write1(uint8_t reg, uint8_t v) {
    return reg_write(reg, &v, 1);
}
static esp_err_t reg_write2(uint8_t reg, uint8_t a, uint8_t b) {
    uint8_t v[2] = { a, b };
    return reg_write(reg, v, 2);
}
static esp_err_t reg_read(uint8_t reg, uint8_t* v, size_t n) {
    return i2c_master_transmit_receive(s_dev, &reg, 1, v, n, 50);
}

/* ================================================================== */
/* The stop                                                            */
/* ================================================================== */

static void summary_block(void);

/*
 * A stop is not a halt: it re-prints the verdict every 15 s forever.  The
 * operator attaches the monitor AFTER the reset more often than not (the
 * C3's native USB port re-enumerates on reset, so the first second of
 * output is routinely lost), and a diagnostic whose result scrolled past
 * before anyone could read it has not reported anything.
 */
static void stop_here(const char* why) {
    snprintf(g_stop_reason, sizeof(g_stop_reason), "%s", why);
    for (;;) {
        summary_block();
        vTaskDelay(pdMS_TO_TICKS(15000));
    }
}

/* ================================================================== */
/* RUNG 1 — identity and the build stamp                               */
/* ================================================================== */
/*
 * TWO stamps, printed side by side, because they fail differently and the
 * DISAGREEMENT is the instrument.
 *
 *   cxc_build_stamp()      from gen/build_stamp.h, REGENERATED by
 *                          tools/gen_build_stamp.py on EVERY `pio run`.
 *                          Cannot go stale: the header's bytes change every
 *                          build, so whatever includes it is recompiled.
 *
 *   __DATE__ " " __TIME__  the compile time OF THIS TRANSLATION UNIT.
 *
 * The brief that produced this file asked for __DATE__/__TIME__ and named
 * the right enemy (a hand-maintained tag: one read "TF13" through ~10
 * flashes across three boards on 2026-08-11, including a board that did not
 * exist when the string was written).  But __DATE__ in a STABLE TU is that
 * same bug with the compiler holding the pen — a file that has not changed
 * is not recompiled, so its __TIME__ is the last time it happened to
 * change.  See announce.h's long note; the fleet has already paid for this.
 *
 * So both are printed and NEITHER is trusted alone.  If the TU stamp is
 * older than the generated stamp, gauge_diag.cpp was not recompiled this
 * build, and every "fix" you think you just flashed is still on your disk.
 * That is a fault injector for the reflash itself, obtained for free.
 */
static void rung1_identity(void) {
    head("1", "IDENTITY, BUILD STAMP, RESET REASON");

    printf("  generated stamp : %s   (fingerprint %08lx)\n",
           cxc_build_stamp(), (unsigned long)cxc_build_fingerprint());
    printf("  this TU compiled: %s %s\n", __DATE__, __TIME__);
    printf("  ^ if the second is OLDER than the first, gauge_diag.cpp was\n"
           "    NOT recompiled and you are reading a stale binary.\n");

    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    printf("  MAC / USB serial: %02X:%02X:%02X:%02X:%02X:%02X\n",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    /* No device identity is compiled in: the never-flash list is host
     * config (FLASH_DENY_SERIALS in the host overlay), enforced by the flash
     * guard before any upload.  A quoted MAC here would be a pseudonym in the
     * public tree and would match nothing. */
    printf("  ⚠ CONFIRM THIS IS THE BOARD YOU MEANT.  A port path is not an\n"
           "    identity.  Compare the MAC above with FLASH_DENY_SERIALS in\n"
           "    the host overlay (the LIVE dual-throttle controller is on it)\n"
           "    — never flash a board on that list.\n");

    const esp_reset_reason_t rr = esp_reset_reason();
    static const char* kNames[] = {
        "UNKNOWN", "POWERON", "EXT", "SW", "PANIC", "INT_WDT", "TASK_WDT",
        "WDT", "DEEPSLEEP", "BROWNOUT", "SDIO", "USB", "JTAG", "EFUSE",
        "PWR_GLITCH", "CPU_LOCKUP" };
    const int ri = (int)rr;
    printf("  reset reason    : %s\n",
           (ri >= 0 && ri < (int)(sizeof(kNames)/sizeof(kNames[0])))
               ? kNames[ri] : "?");

    printf("  I2C SDA=GPIO%d SCL=GPIO%d @%lu Hz | MCP23017 expected @0x%02X\n",
           kG3SdaGpio, kG3SclGpio, (unsigned long)kG3I2cHz, kG3McpAddr);
    printf("  backlights on GPIO%d / GPIO%d / GPIO%d\n",
           kG3IllumGpio[0], kG3IllumGpio[1], kG3IllumGpio[2]);

    if (rr == ESP_RST_BROWNOUT) {
        printf("\n  *** FAIL — LAST RESET WAS A BROWNOUT. ***\n"
               "  The 3V3 rail sagged below the C3's detector.  This is the\n"
               "  ONE rail instrument this board has (see rung 7) and it has\n"
               "  just fired.  Suspect, in order: the LM7805 in thermal\n"
               "  shutdown (it dissipates 1.96 W typical with NO heatsink —\n"
               "  a 127 degC rise, i.e. it shuts down at IDLE), an\n"
               "  under-rated 12 V supply, or a short pulling the 5 V rail\n"
               "  down.  MEASURE THE RAILS WITH THE METER before flashing\n"
               "  anything else.\n");
        g_fail_a++;
        stop_here("rung 1: last reset was ESP_RST_BROWNOUT — the 3V3 rail "
                  "collapsed. This is a POWER fault; do not debug protocol.");
    }

    proved("which binary is running, on which module, and how it last reset");
    not_proved("that any rail is at its nominal voltage — the C3 sees only "
               "its own 3V3, and only when it sags far enough to reset");
}

/* ================================================================== */
/* RUNG 2 — the bus, before and after the driver exists                */
/* ================================================================== */

/*
 * PULL-UP PRESENCE, measured without a meter.
 *
 * Drive the pin LOW as a push-pull output, release it to a HIGH-Z input
 * with the INTERNAL pull-up DISABLED, wait, and read.  A 4k7 external
 * pull-up against the few pF of a short trace charges the node in well
 * under a microsecond, so 200 us later it reads 1.  With NO external
 * pull-up the node is held near 0 by pad leakage for orders of magnitude
 * longer, so it reads 0.
 *
 * ⚠ WHAT MAKES THIS HONEST: the internal pull-up is ~45 kOhm, so leaving it
 * on would make a board with NO external pull-ups pass — the check would
 * confirm its own assumption.  Turning it off is the entire point, and it
 * is why this cannot simply read the idle level.
 *
 * ⚠ WHAT IT STILL CANNOT DO: distinguish 4k7 from 47k, or prove the
 * pull-up goes to +3V3 rather than to +5V.  Those are meter questions and
 * the preflight document asks them.
 */
static bool pullup_present(int gpio) {
    gpio_config_t io = {};
    io.pin_bit_mask = (1ULL << gpio);
    io.mode         = GPIO_MODE_OUTPUT;
    io.pull_up_en   = GPIO_PULLUP_DISABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io.intr_type    = GPIO_INTR_DISABLE;
    gpio_config(&io);
    gpio_set_level((gpio_num_t)gpio, 0);
    ets_delay_us(200);

    io.mode = GPIO_MODE_INPUT;
    gpio_config(&io);
    ets_delay_us(200);
    const int lvl = gpio_get_level((gpio_num_t)gpio);

    gpio_reset_pin((gpio_num_t)gpio);
    return lvl == 1;
}

static void rung2_bus_and_scan(void) {
    head("2", "I2C SUBSTRATE AND ADDRESS SCAN");

    /* --- 2a. idle levels, BEFORE the I2C driver exists ---------------
     * A stuck bus and an absent bus produce the same "no device found"
     * from a scan.  These two bits separate them, and they must be read
     * before the peripheral claims the pins. */
    gpio_config_t io = {};
    io.pin_bit_mask = (1ULL << kG3SdaGpio) | (1ULL << kG3SclGpio);
    io.mode         = GPIO_MODE_INPUT;
    io.pull_up_en   = GPIO_PULLUP_ENABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io.intr_type    = GPIO_INTR_DISABLE;
    gpio_config(&io);
    ets_delay_us(200);
    const int sda_idle = gpio_get_level((gpio_num_t)kG3SdaGpio);
    const int scl_idle = gpio_get_level((gpio_num_t)kG3SclGpio);
    printf("  idle levels (internal pull-ups ON) : SDA=%d SCL=%d\n",
           sda_idle, scl_idle);

    if (!sda_idle && !scl_idle) {
        printf("\n  *** FAIL — BOTH SDA AND SCL ARE LOW AT IDLE. ***\n"
               "  A stuck SLAVE can hold SDA. NOTHING legitimately holds\n"
               "  BOTH low at idle. This is a SHORT (to each other or to\n"
               "  GND), or the expander is powered but latched up. It is a\n"
               "  METER question: continuity SDA-GND, SCL-GND, SDA-SCL,\n"
               "  power off.\n");
        g_fail_a++;
        stop_here("rung 2: SDA and SCL both low at idle — a short, not a "
                  "protocol problem.");
    }
    if (!sda_idle) {
        printf("  ⚠ SDA low, SCL high — the signature of a slave stuck\n"
               "    mid-byte. Clocking 9 times to release it.\n");
    }

    /* --- 2b. nine-clock recovery, still before the driver ------------ */
    if (!sda_idle) {
        io.mode = GPIO_MODE_INPUT_OUTPUT_OD;
        gpio_config(&io);
        gpio_set_level((gpio_num_t)kG3SdaGpio, 1);
        for (int i = 0; i < 9; i++) {
            gpio_set_level((gpio_num_t)kG3SclGpio, 0); ets_delay_us(5);
            gpio_set_level((gpio_num_t)kG3SclGpio, 1); ets_delay_us(5);
            if (gpio_get_level((gpio_num_t)kG3SdaGpio)) break;
        }
        gpio_set_level((gpio_num_t)kG3SdaGpio, 0); ets_delay_us(5);
        gpio_set_level((gpio_num_t)kG3SclGpio, 1); ets_delay_us(5);
        gpio_set_level((gpio_num_t)kG3SdaGpio, 1); ets_delay_us(5);
        const bool ok = gpio_get_level((gpio_num_t)kG3SdaGpio) == 1;
        printf("  9-clock recovery: SDA %s\n", ok ? "RELEASED" : "STILL LOW");
        if (!ok) {
            printf("\n  *** FAIL — SDA STILL HELD LOW AFTER 9 CLOCKS. ***\n"
                   "  That is not a stuck slave. Suspect a solder bridge on\n"
                   "  the SDA net, a dead pull-up, or an expander whose VDD\n"
                   "  is missing. METER: continuity SDA to GND.\n");
            g_fail_a++;
            stop_here("rung 2: SDA held low through 9 clocks — a short or an "
                      "unpowered expander.");
        }
    }

    /* --- 2c. external pull-ups ------------------------------------- */
    const bool pu_sda = pullup_present(kG3SdaGpio);
    const bool pu_scl = pullup_present(kG3SclGpio);
    printf("  external pull-up present (internal OFF): SDA=%s SCL=%s\n",
           pu_sda ? "YES" : "NO", pu_scl ? "YES" : "NO");
    if (!pu_sda || !pu_scl) {
        printf("  ⚠ A missing external pull-up is NOT fatal here — the\n"
               "    driver enables the C3's internal ~45k, which works at\n"
               "    100 kHz on a short trace and is marginal at 400 kHz.\n"
               "    But R_SDA/R_SCL (4k7 to +3V3) are on the board, so a NO\n"
               "    here means an unsoldered or wrong-valued resistor.\n"
               "    CHECK IT ON THE METER before believing rung 3's numbers:\n"
               "    weak pull-ups and a marginal V_IH produce the SAME\n"
               "    intermittent mismatch, and rung 3 cannot tell them apart.\n");
    }

    /* --- 2d. install the driver and scan ---------------------------- */
    i2c_master_bus_config_t bc = {};
    bc.i2c_port          = -1;
    bc.sda_io_num        = (gpio_num_t)kG3SdaGpio;
    bc.scl_io_num        = (gpio_num_t)kG3SclGpio;
    bc.clk_source        = I2C_CLK_SRC_DEFAULT;
    bc.glitch_ignore_cnt = 7;
    bc.flags.enable_internal_pullup = true;
    esp_err_t e = i2c_new_master_bus(&bc, &s_bus);
    if (e != ESP_OK) {
        printf("\n  *** FAIL — i2c_new_master_bus: %s ***\n",
               esp_err_to_name(e));
        g_fail_a++;
        stop_here("rung 2: the I2C peripheral would not install. This is a "
                  "firmware/pin-config fault, not a board fault.");
    }

    printf("\n  scanning 0x03..0x77 — EVERY responder is reported, not just\n"
           "  the expected one. A device at an unexpected address is\n"
           "  information; a strap resistor is the usual reason.\n  ");
    int found = 0, found_at_20 = -1;
    uint8_t hits[16];
    int nhits = 0;
    for (int a = 0x03; a <= 0x77; a++) {
        if (i2c_master_probe(s_bus, a, 30) == ESP_OK) {
            found++;
            if (nhits < 16) hits[nhits++] = (uint8_t)a;
            if (a == kG3McpAddr) found_at_20 = a;
            printf("0x%02X ", a);
        }
    }
    printf("\n  responders: %d\n", found);

    /*
     * NEGATIVE CONTROL ON THE SCAN ITSELF.
     * With SDA shorted to GND the master reads an ACK at EVERY address, and
     * a scan that reports "117 devices found" looks like a rich bus rather
     * than a dead short. This board populates ONE I2C device. More than a
     * handful of responders is not a discovery, it is a fault.
     */
    if (found > 8) {
        printf("\n  *** FAIL — %d RESPONDERS ON A ONE-DEVICE BOARD. ***\n"
               "  Every address ACKing is the signature of SDA held LOW, not\n"
               "  of devices. The scan is reporting the short as success.\n"
               "  METER: SDA to GND, power off.\n", found);
        g_fail_a++;
        stop_here("rung 2: the whole address space ACKed — SDA is shorted "
                  "low, and the scan cannot tell that from a populated bus.");
    }

    if (found == 0) {
        printf("\n  *** FAIL — NOTHING ACKED ANYWHERE. ***\n"
               "  SDA and SCL idled HIGH, so the wires are not shorted; the\n"
               "  expander is not answering. In order of likelihood:\n"
               "    1. U1 seated backwards, or a bent-under pin\n"
               "    2. U1 VDD (pin 9) not reaching +5V — METER pin 9 to GND\n"
               "    3. U1 RESET (pin 18) not high\n"
               "    4. SDA/SCL open between the ESP header and U1 pins 13/12\n"
               "  ⚠ 4 IS THE ONE THIS FIRMWARE CANNOT SEE. An open trace and\n"
               "  a missing chip look identical from here. Beep it out.\n");
        g_fail_a++;
        stop_here("rung 2: no I2C device responded. Chain A stops; chain B "
                  "still runs, it shares nothing with this.");
    }

    if (found_at_20 < 0) {
        printf("\n  *** FAIL — SOMETHING ANSWERED, BUT NOT AT 0x%02X. ***\n",
               kG3McpAddr);
        for (int i = 0; i < nhits; i++) {
            if (hits[i] >= 0x20 && hits[i] <= 0x27) {
                printf("  0x%02X is an MCP23017 address with A2/A1/A0 = %d%d%d.\n"
                       "  This board ties all three to GND, so 0x20 is the only\n"
                       "  legal one. A different address means an address pin is\n"
                       "  NOT grounded — check U1 pins 15/16/17.\n",
                       hits[i], (hits[i] >> 2) & 1, (hits[i] >> 1) & 1,
                       hits[i] & 1);
            }
        }
        g_fail_a++;
        stop_here("rung 2: the expander is at the wrong address — an address "
                  "strap (U1 pins 15/16/17) is not grounded.");
    }

    printf("  MCP23017 present at 0x%02X.\n", kG3McpAddr);
    proved("SDA and SCL are electrically usable, the external pull-ups are "
           "indicated present, and a device ACKs at 0x20 with A0/A1/A2 "
           "correctly grounded");
    not_proved("that the ACK came from a HEALTHY part, that any register "
               "write survives, or that a single expander PAD reaches a "
               "L293D input. An ACK is one bit at one moment — rungs 3 and 4 "
               "are the ones that can fail");
}

/* ================================================================== */
/* RUNG 3 — I2C INTEGRITY STRESS: the rung for the known V_IH defect   */
/* ================================================================== */
/*
 * ⛔ WHY THIS RUNG EXISTS.
 * U1 pin 9 (VDD) is on +5V while R_SDA/R_SCL pull to +3V3. The MCP23017
 * specifies SDA/SCL as I2C Schmitt inputs with V_IH = 0.7 x VDD = 3.5 V at
 * VDD = 5.0 V. The bus can only reach 3.3 V. THE HIGH LEVEL IS 200 mV BELOW
 * THE GUARANTEED INPUT-HIGH THRESHOLD.
 *
 * The real Schmitt threshold on a typical part is ~2.5-3 V, so it will very
 * often work — at 25 degC, on this lot, on this bench. That is what makes it
 * dangerous rather than fatal, and it is why an ADDRESS SCAN IS NOT ENOUGH:
 * a scan proves ONE ACK at 3.3 V. It says nothing about the ten thousandth
 * register write on a warm afternoon. A single failure in ten thousand is
 * the signature, and downstream it appears as a needle that occasionally
 * jumps — for which nobody will suspect the I2C bus a month from now.
 *
 * SO THIS REPORTS A RATE, NOT A BOOLEAN.
 *
 * ⚠ DELIBERATE DEVIATION FROM THE BRIEF, and the reason matters.
 * The brief said "walking-1s and walking-0s across both PORT registers".
 * This walks DEFVALA/DEFVALB (0x06/0x07) instead — 16 bits of ordinary R/W
 * storage on the same die, reached over the same wires, through the same
 * address decode and the same register write path. Two reasons:
 *
 *   1. GPIOA/GPIOB drive the twelve L293D inputs. Walking a 1 across them
 *      ten thousand times energises single coil phases ten thousand times:
 *      the needles buzz and jitter for the whole run, the motors and the
 *      L293Ds warm up, and — worse — a mismatch could then be caused by the
 *      motor load rather than by the bus. That CONFLATES the two failure
 *      modes this rung exists to separate.
 *   2. DEFVAL has no effect on any pin unless interrupt-on-change is armed.
 *      GPINTEN and INTCON are left at their 0x00 reset values and U1's INTA
 *      and INTB (pins 19/20) are unconnected on this board anyway, so the
 *      register is inert by construction.
 *
 * What is given up is the output DRIVERS, and rung 4 covers exactly that.
 */
struct StressResult {
    int pairs;
    int write_err;      /* the transmit itself failed (NAK/timeout)        */
    int read_err;       /* the read-back transaction failed                */
    int mismatch;       /* both transactions succeeded, the DATA disagreed */
    int first_iter;
    uint8_t exp_a, exp_b, got_a, got_b;
};

/* The pattern list. Walking-1 and walking-0 across all sixteen bits find a
 * stuck or coupled bit; 0x00/0xFF/0x55/0xAA find the whole-byte and
 * alternating cases that a walking pattern can skip. */
static void pattern_for(int k, uint8_t* a, uint8_t* b) {
    const int n = k % 36;
    if (n < 16) {                       /* walking 1 */
        const uint16_t w = (uint16_t)(1u << n);
        *a = (uint8_t)(w & 0xFF); *b = (uint8_t)(w >> 8);
    } else if (n < 32) {                /* walking 0 */
        const uint16_t w = (uint16_t)~(1u << (n - 16));
        *a = (uint8_t)(w & 0xFF); *b = (uint8_t)(w >> 8);
    } else {
        static const uint8_t kFlat[4] = { 0x00, 0xFF, 0x55, 0xAA };
        *a = *b = kFlat[n - 32];
    }
}

/* `corrupt` is the FAULT INJECTOR: it compares against a value that is
 * deliberately not what was written, so the mismatch counter MUST reach
 * `pairs`. A run that reports 0 mismatches under injection proves the
 * comparison is broken and that every clean 0 above it meant nothing.
 * [CXC-VISION-EPISTEMICS] rule-negative-control — the drop counter that
 * reported 0 because it was structurally pinned at 0 is the same shape. */
static StressResult stress_run(int pairs, bool corrupt) {
    StressResult r = {};
    r.pairs = pairs;
    r.first_iter = -1;
    for (int i = 0; i < pairs; i++) {
        uint8_t wa, wb;
        pattern_for(i, &wa, &wb);
        if (reg_write2(kRegDEFVALA, wa, wb) != ESP_OK) { r.write_err++; continue; }
        uint8_t rd[2] = { 0, 0 };
        if (reg_read(kRegDEFVALA, rd, 2) != ESP_OK)    { r.read_err++;  continue; }
        const uint8_t ea = corrupt ? (uint8_t)(wa ^ 0xFF) : wa;
        const uint8_t eb = corrupt ? (uint8_t)(wb ^ 0xFF) : wb;
        if (rd[0] != ea || rd[1] != eb) {
            if (r.first_iter < 0) {
                r.first_iter = i;
                r.exp_a = ea; r.exp_b = eb; r.got_a = rd[0]; r.got_b = rd[1];
            }
            r.mismatch++;
        }
    }
    return r;
}

static void stress_report(const char* label, uint32_t hz,
                          const StressResult& r, int64_t us) {
    const int bad = r.write_err + r.read_err + r.mismatch;
    const double rate_ppm = r.pairs ? (1e6 * bad) / r.pairs : 0.0;
    const double floor_ppm = r.pairs ? 1e6 / r.pairs : 0.0;
    printf("  %-22s %6d pairs @%3lu kHz in %5lld ms\n",
           label, r.pairs, (unsigned long)(hz / 1000),
           (long long)(us / 1000));
    printf("      write NAK/timeout %5d | read NAK/timeout %5d | "
           "DATA mismatch %5d\n", r.write_err, r.read_err, r.mismatch);
    printf("      error rate %.1f ppm   (resolution floor of this run: "
           "%.0f ppm)\n", rate_ppm, floor_ppm);
    if (bad == 0)
        printf("      ⚠ 0 errors means the rate is BELOW %.0f ppm. It does "
               "NOT mean zero.\n", floor_ppm);
    if (r.first_iter >= 0)
        printf("      first disagreement at pair %d: wrote/expected %02X %02X,"
               " read %02X %02X\n", r.first_iter, r.exp_a, r.exp_b,
               r.got_a, r.got_b);
}

static void rung3_integrity(void) {
    head("3", "I2C INTEGRITY STRESS — the rung for the +5V/3V3 V_IH defect");

    printf("  U1 VDD is +5V; R_SDA/R_SCL pull to +3V3. V_IH = 0.7 x VDD =\n"
           "  3.5 V and the bus reaches 3.3 V: 200 mV UNDER THRESHOLD. Rung 2\n"
           "  proved one ACK. This measures how often a register round-trip\n"
           "  actually survives.\n\n");

    /* IOCON at BOTH 0x0A and 0x0B: those two addresses swap meaning with
     * the BANK bit and a warm part's mode is unknown. 0x00 = BANK 0 with
     * address auto-increment ON, which is what makes a 2-byte register pair
     * one transaction. */
    if (reg_write1(kG3RegIOCON_A, 0x00) != ESP_OK ||
        reg_write1(kG3RegIOCON_B, 0x00) != ESP_OK) {
        printf("  *** FAIL — cannot write IOCON. The part ACKed its address\n"
               "  and will not take a register write. That gap is exactly\n"
               "  what a marginal V_IH looks like.\n");
        g_fail_a++;
        stop_here("rung 3: the expander ACKs but will not accept a register "
                  "write.");
    }

    int64_t t0 = esp_timer_get_time();
    const StressResult fast = stress_run(kStressPairsFast, false);
    int64_t t_fast = esp_timer_get_time() - t0;
    stress_report("OPERATIONAL", kG3I2cHz, fast, t_fast);

    if (!dev_open(kI2cSlowHz)) {
        printf("  (could not re-open the device at %lu Hz; skipping the "
               "slow control)\n", (unsigned long)kI2cSlowHz);
    } else {
        printf("\n");
        t0 = esp_timer_get_time();
        const StressResult slow = stress_run(kStressPairsSlow, false);
        int64_t t_slow = esp_timer_get_time() - t0;
        stress_report("CONTROL (slow bus)", kI2cSlowHz, slow, t_slow);

        const int bad_f = fast.write_err + fast.read_err + fast.mismatch;
        const int bad_s = slow.write_err + slow.read_err + slow.mismatch;
        printf("\n  INTERPRETATION\n");
        if (bad_f == 0 && bad_s == 0) {
            printf("    clean at both speeds. The V_IH margin is negative ON\n"
                   "    PAPER and this part, at this temperature, tolerates\n"
                   "    it. That is NOT the same as the design being right —\n"
                   "    see PREFLIGHT §MOD A. Re-run warm.\n");
        } else if (bad_f > 0 && bad_s == 0) {
            printf("    ⚠ FAILS AT %lu kHz, CLEAN AT %lu kHz. That is the\n"
                   "    classic marginal-level/timing signature. WORKAROUND:\n"
                   "    lower kG3I2cHz in include/rack_triple_gauge_panel.h.\n"
                   "    FIX: MOD A — move U1 pin 9 AND pin 18 to +3V3.\n",
                   (unsigned long)(kG3I2cHz / 1000),
                   (unsigned long)(kI2cSlowHz / 1000));
        } else {
            printf("    ⚠ FAILS AT BOTH SPEEDS. Slowing the bus is not the\n"
                   "    answer. Suspect the pull-ups, a long/noisy SDA run,\n"
                   "    or U1's VDD itself. Check rung 2's pull-up line.\n");
        }
    }

    /* --- the fault injector ---------------------------------------- */
    printf("\n  NEGATIVE CONTROL — comparing against a deliberately WRONG\n"
           "  expected value. The mismatch counter MUST reach %d. If it does\n"
           "  not, the comparison is broken and every zero above is\n"
           "  meaningless.\n", kInjectedFaultPairs);
    dev_open(kG3I2cHz);
    const StressResult inj = stress_run(kInjectedFaultPairs, true);
    printf("      injected mismatches detected: %d of %d\n",
           inj.mismatch, kInjectedFaultPairs);
    if (inj.mismatch != kInjectedFaultPairs) {
        printf("\n  *** FAIL — THE INSTRUMENT IS BROKEN. ***\n"
               "  It did not detect a fault it was handed on a plate, so its\n"
               "  clean runs prove nothing. Do not trust rung 3's numbers.\n");
        g_fail_a++;
        stop_here("rung 3: the stress comparator failed its own fault "
                  "injection — the instrument cannot report failure.");
    }
    printf("      the counter CAN move. The zeros above are real zeros.\n");

    reg_write2(kRegDEFVALA, 0x00, 0x00);   /* leave it as found */

    proved("a measured error RATE for the round trip ESP32 -> SDA/SCL -> "
           "MCP23017 register file -> back, at two bus speeds, with the "
           "detector proven able to fire");
    not_proved("anything about the expander's OUTPUT DRIVERS, about any pad "
               "leaving the package, or about the L293Ds. DEFVAL is internal "
               "storage — it was chosen precisely so that a bus error could "
               "not be confused with a motor load. Rung 4 is the pads");
}

/* ================================================================== */
/* RUNG 4 — per-pin drive and read back, all sixteen pads              */
/* ================================================================== */
/*
 * ⛔ READ GPIO (0x12/0x13), NEVER OLAT (0x14/0x15).
 * On the MCP23017 a read of OLAT returns the OUTPUT LATCH — i.e. exactly
 * the byte you just wrote, through the same assumption that wrote it. It
 * would return "correct" for a pad shorted to ground, for a lifted pin, for
 * a cracked bond wire. A read of GPIO returns THE VALUE ON THE PORT PIN.
 * That one register choice is the whole difference between an instrument
 * and a mirror, and it is [CXC-VISION-EPISTEMICS] rule-self-verification in
 * one line of datasheet.
 *
 * This is the primitive that proved the CAN loopback PADS were good, and
 * therefore that the loopback FAILURES were the instrument's fault.
 *
 * The comparison is on the FULL 16-BIT PORT, not on the bit under test. A
 * solder bridge between two adjacent pads shows up as a second bit moving,
 * and a per-bit check would sail straight past it.
 */
static void rung4_pins(void) {
    head("4", "PER-PIN DRIVE AND READ-BACK — all 16 MCP23017 pads");

    const int npins = kDriveExpansionNibble ? 16 : 12;
    if (kDriveExpansionNibble) {
        printf("  ⚠ THIS DRIVES GPB4-7, WHICH LEAVE THE CHIP ON J_EXP_MCP.\n"
               "    UNPLUG ANYTHING ON THAT HEADER. The real firmware keeps\n"
               "    them inputs (IODIRB=0xF0) for exactly this reason. Set\n"
               "    kDriveExpansionNibble=false to test only the 12 coil\n"
               "    lines.\n");
    } else {
        printf("  GPB4-7 SKIPPED (kDriveExpansionNibble=false) — J_EXP_MCP is\n"
               "  assumed occupied. Those four pads are UNTESTED.\n");
    }
    printf("  ⚠ Each pad driven high is one L293D input asserted, so a needle\n"
           "    may TWITCH. That is expected. Nothing is positioned.\n\n");

    /* All pads outputs, all low: coils off, and only the pin under test
     * moves. OLAT before IODIR, same reason the real driver gives — the
     * transition must be float -> driven-LOW, never float -> whatever the
     * latch happened to hold. */
    const uint8_t dirb = kDriveExpansionNibble ? 0x00 : 0xF0;
    if (reg_write2(kG3RegOLATA, 0x00, 0x00) != ESP_OK ||
        reg_write2(kG3RegIODIRA, 0x00, dirb) != ESP_OK ||
        reg_write2(kRegIPOLA, 0x00, 0x00)   != ESP_OK ||
        reg_write2(kRegGPPUA, 0x00, 0x00)   != ESP_OK) {
        printf("  *** FAIL — cannot configure the ports. ***\n");
        g_fail_a++;
        stop_here("rung 4: port configuration writes failed.");
    }

    static const char* kNet[16] = {
        "GPA0 M1_IN1 -> U2.2",  "GPA1 M1_IN2 -> U2.7",
        "GPA2 M1_IN3 -> U2.10", "GPA3 M1_IN4 -> U2.15",
        "GPA4 M2_IN1 -> U3.2",  "GPA5 M2_IN2 -> U3.7",
        "GPA6 M2_IN3 -> U3.10", "GPA7 M2_IN4 -> U3.15",
        "GPB0 M3_IN1 -> U4.2",  "GPB1 M3_IN2 -> U4.7",
        "GPB2 M3_IN3 -> U4.10", "GPB3 M3_IN4 -> U4.15",
        "GPB4 -> J_EXP_MCP.1",  "GPB5 -> J_EXP_MCP.2",
        "GPB6 -> J_EXP_MCP.3",  "GPB7 -> J_EXP_MCP.4",
    };

    int bad_pins = 0;
    for (int p = 0; p < npins; p++) {
        const uint16_t bit = (uint16_t)(1u << p);
        bool pin_ok = true;
        /* `bus_fault` separates "the I2C transaction did not complete" from
         * "the transaction completed and the PAD disagreed". Without it, a
         * failed read leaves rd_hi at 0 and the interpretation below would
         * confidently announce "pad never went high" — a second, WRONG
         * message on top of a correct one, which is the exact habit this
         * whole file exists to break. */
        bool bus_fault = false;
        uint16_t rd_lo = 0, rd_hi = 0;

        for (int phase = 0; phase < 2; phase++) {
            const uint16_t want = phase ? bit : 0x0000;
            uint8_t rd[2] = { 0, 0 };
            if (reg_write2(kG3RegOLATA, (uint8_t)(want & 0xFF),
                           (uint8_t)(want >> 8)) != ESP_OK) {
                printf("  FAIL  %-24s I2C WRITE FAILED (drive %s)\n",
                       kNet[p], phase ? "high" : "low");
                pin_ok = false; bus_fault = true; break;
            }
            ets_delay_us(200);
            if (reg_read(kG3RegGPIOA, rd, 2) != ESP_OK) {
                printf("  FAIL  %-24s I2C READ FAILED (drive %s)\n",
                       kNet[p], phase ? "high" : "low");
                pin_ok = false; bus_fault = true; break;
            }
            const uint16_t got = (uint16_t)(rd[0] | (rd[1] << 8));
            const uint16_t mask = kDriveExpansionNibble ? 0xFFFF : 0x0FFF;
            if (phase) rd_hi = got; else rd_lo = got;
            if ((got & mask) != (want & mask)) pin_ok = false;
        }

        if (pin_ok) {
            printf("  PASS  %-24s low->0 high->1\n", kNet[p]);
        } else if (bus_fault) {
            bad_pins++;
            printf("        the BUS failed mid-test, so this pad is UNTESTED "
                   "rather than bad.\n"
                   "        Re-read rung 3 — an intermittent bus after a "
                   "clean stress run is\n"
                   "        the V_IH margin showing up warm.\n");
        } else {
            bad_pins++;
            const uint16_t diff_hi = (uint16_t)(rd_hi ^ bit);
            printf("  FAIL  %-24s drive-low read %04X, drive-high read %04X "
                   "(expected 0000 / %04X)\n", kNet[p], rd_lo, rd_hi, bit);
            if (rd_hi == 0 && rd_lo == 0)
                printf("        pad never went high: shorted to GND, or the\n"
                       "        output driver is dead.\n");
            else if (diff_hi & ~bit)
                printf("        OTHER BITS MOVED (%04X). That is a SOLDER\n"
                       "        BRIDGE between pads, and a per-bit check\n"
                       "        would have called this pin PASS.\n",
                       (uint16_t)(diff_hi & ~bit));
        }
    }

    /* --- the fault injector ----------------------------------------
     * IPOL inverts what the GPIO register reports for each bit. Set it and
     * a known-good read MUST flip. If it does not, we are not reading the
     * port read path at all and every PASS above is a mirror. */
    printf("\n  NEGATIVE CONTROL — IPOL=FFFF inverts the GPIO read path.\n"
           "  With all pads driven LOW, GPIO must now read FFFF.\n");
    uint8_t rd[2] = { 0, 0 };
    reg_write2(kG3RegOLATA, 0x00, 0x00);
    reg_write2(kRegIPOLA, 0xFF, 0xFF);
    ets_delay_us(200);
    reg_read(kG3RegGPIOA, rd, 2);
    const uint16_t inv = (uint16_t)(rd[0] | (rd[1] << 8));
    reg_write2(kRegIPOLA, 0x00, 0x00);
    printf("      GPIO read with IPOL set: %04X\n", inv);
    if (inv != 0xFFFF) {
        printf("\n  *** FAIL — THE READ PATH DID NOT INVERT. ***\n"
               "  The readback is not reflecting the port register, so the\n"
               "  per-pin PASSes above are not evidence about the pads.\n");
        g_fail_a++;
        stop_here("rung 4: IPOL did not invert the readback — the per-pin "
                  "test is a mirror, not an instrument.");
    }
    printf("      it inverted. The per-pin readbacks are reading the PADS.\n");

    /* Leave the part the way the real firmware wants to find it. */
    reg_write2(kG3RegOLATA, 0x00, 0x00);
    reg_write2(kG3RegIODIRA, kG3IodirA, kG3IodirB);

    if (bad_pins) {
        printf("\n  *** FAIL — %d of %d pads did not behave. ***\n",
               bad_pins, npins);
        g_fail_a++;
        stop_here("rung 4: one or more expander pads failed drive/read-back.");
    }

    proved("every tested MCP23017 pad drives both levels and the PORT "
           "register (not the latch) agrees, and no pad drags a neighbour "
           "with it");
    not_proved("that any pad's copper reaches its L293D input pin. An OPEN "
               "trace between U1 and U2/U3/U4 reads IDENTICALLY to a good "
               "one from here — the expander pad is fine either way. That is "
               "a CONTINUITY question and the preflight document asks it");
}

/* ================================================================== */
/* RUNG 5 — L293D reachability                                         */
/* ================================================================== */
/*
 * ⚠ WHAT THIS CAN AND CANNOT PROVE, and the gap is large enough that
 * stating it is the most useful thing on the rung:
 *
 *   CAN: that the MCP can assert each of the twelve L293D INPUT lines in a
 *        legal coil pattern, and — with a human watching — that a needle
 *        twitches when the gauge it is supposed to belong to is driven.
 *
 *   CANNOT: that the H-bridge OUTPUT reaches the connector. The L293D's
 *        outputs are not readable by anything on this board. A dead bridge,
 *        an open J_MOT crimp, a missing coil: all three produce exactly
 *        this output. The needle, or the meter, is the measurement.
 *
 * Pattern choice: IN1=1/IN2=0 energises coil A one way; IN1=0/IN2=1 the
 * other. IN1=IN2 shorts the coil through the low side — no current — which
 * is why 0x0 is safe to write at any moment.
 */
static void rung5_l293d(void) {
    head("5", "L293D REACHABILITY — one coil line at a time");

    printf("  ⚠ WATCH THE NEEDLES. Each line below energises ONE coil for\n"
           "  %d ms. A needle may twitch by a step or two. Note WHICH gauge\n"
           "  moves for each line — that is the only check on this board for\n"
           "  a swapped J_MOT harness, and a transposed pair still steps,\n"
           "  still homes, and runs BACKWARDS forever.\n\n", kCoilDwellMs);

    reg_write2(kG3RegIODIRA, 0x00, 0x00);
    reg_write2(kG3RegOLATA, 0x00, 0x00);

    struct Step { int gauge; const char* coil; uint16_t nib; int shift; };
    static const Step kSteps[] = {
        { 1, "A fwd (IN1)", 0x1, 0 }, { 1, "A rev (IN2)", 0x2, 0 },
        { 1, "B fwd (IN3)", 0x4, 0 }, { 1, "B rev (IN4)", 0x8, 0 },
        { 2, "A fwd (IN1)", 0x1, 4 }, { 2, "A rev (IN2)", 0x2, 4 },
        { 2, "B fwd (IN3)", 0x4, 4 }, { 2, "B rev (IN4)", 0x8, 4 },
        { 3, "A fwd (IN1)", 0x1, 8 }, { 3, "A rev (IN2)", 0x2, 8 },
        { 3, "B fwd (IN3)", 0x4, 8 }, { 3, "B rev (IN4)", 0x8, 8 },
    };
    int errs = 0;
    for (const Step& s : kSteps) {
        const uint16_t w = (uint16_t)(s.nib << s.shift);
        const esp_err_t e = reg_write2(kG3RegOLATA, (uint8_t)(w & 0xFF),
                                       (uint8_t)(w >> 8));
        printf("  gauge %d  coil %-13s  OLAT=%04X  %s\n",
               s.gauge, s.coil, w, e == ESP_OK ? "asserted" : "I2C FAILED");
        if (e != ESP_OK) errs++;
        vTaskDelay(pdMS_TO_TICKS(kCoilDwellMs));
        reg_write2(kG3RegOLATA, 0x00, 0x00);
        vTaskDelay(pdMS_TO_TICKS(40));
    }

    reg_write2(kG3RegIODIRA, kG3IodirA, kG3IodirB);

    if (errs) {
        printf("\n  *** FAIL — %d coil assertions did not reach the "
               "expander. ***\n", errs);
        g_fail_a++;
        stop_here("rung 5: I2C failed mid-sequence after rung 3 was clean — "
                  "an intermittent bus. Re-run rung 3 warm.");
    }

    proved("all twelve L293D INPUT lines can be asserted from the bus, in "
           "legal single-coil patterns");
    not_proved("that any H-bridge OUTPUT reaches its J_MOT pin, that a coil "
               "is connected, or that gauge N is the gauge you think it is. "
               "The twitch you did or did not see is the measurement, and it "
               "is a NEEDS-EYES row, not a machine result");
}

/* ================================================================== */
/* RUNG 6 — backlight channels (CHAIN B, independent of everything)    */
/* ================================================================== */
/*
 * Plain GPIO, NOT LEDC, on purpose. The question here is electrical
 * reachability — GPIO -> 150R -> 2N2222 base -> collector -> J_LED pin 2,
 * with the strip's other leg on +12V. LEDC adds a timer, a channel
 * allocation and a duty register to the suspect list for no gain.
 *
 * The DISAMBIGUATION that buys: if this rung lights a strip and the real
 * firmware does not, the fault is the LEDC configuration, not the wiring.
 * That is a one-line conclusion that would otherwise cost an evening.
 *
 * ⚠ NAMED INDIVIDUALLY, one at a time, the other two held LOW. "The
 * backlights work" cannot distinguish a swapped pair, and this board's
 * generator carries a comment claiming LED1 is on J_ESP_L pin 3 while the
 * netlist puts LED1_GPIO on pin 1 (see rack_triple_gauge.md §4.4) — so a
 * swap here is not hypothetical.
 *
 * GPIO0/1/3 are NOT strapping pins on the ESP32-C3 (straps are 2, 8, 9
 * only). Driving them is boot-safe.
 */
static void rung6_backlights(void) {
    head("6", "BACKLIGHT CHANNELS — chain B, shares nothing with chain A");

    gpio_config_t io = {};
    io.pin_bit_mask = (1ULL << kG3IllumGpio[0]) | (1ULL << kG3IllumGpio[1]) |
                      (1ULL << kG3IllumGpio[2]);
    io.mode         = GPIO_MODE_OUTPUT;
    io.pull_up_en   = GPIO_PULLUP_DISABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io.intr_type    = GPIO_INTR_DISABLE;
    gpio_config(&io);
    for (int i = 0; i < 3; i++) gpio_set_level((gpio_num_t)kG3IllumGpio[i], 0);

    printf("  ⚠ NEEDS EYES. Nothing in this rung can fail in software: the\n"
           "  C3 cannot read back through a 2N2222. ANSWER FOR EACH LINE\n"
           "  SEPARATELY — \"the backlights work\" cannot distinguish a\n"
           "  swapped pair.\n\n");
    printf("  all three OFF for 2 s — confirm ALL THREE are dark. A strip\n"
           "  that is lit NOW is a shorted transistor or a J_LED miswire.\n");
    vTaskDelay(pdMS_TO_TICKS(2000));

    static const char* kWhere[3] = {
        "GPIO0 -> R_LED1 150R -> Q_LED1 base -> J_LED1 pin 2  (gauge 1)",
        "GPIO1 -> R_LED2 150R -> Q_LED2 base -> J_LED2 pin 2  (gauge 2)",
        "GPIO3 -> R_LED3 150R -> Q_LED3 base -> J_LED3 pin 2  (gauge 3)",
    };
    for (int i = 0; i < 3; i++) {
        for (int j = 0; j < 3; j++)
            gpio_set_level((gpio_num_t)kG3IllumGpio[j], j == i ? 1 : 0);
        printf("  >>> CHANNEL %d ON, the other two OFF: %s\n", i + 1,
               kWhere[i]);
        printf("      which strip lit? EXACTLY ONE should be on.\n");
        vTaskDelay(pdMS_TO_TICKS(kEyeDwellMs));
    }
    for (int i = 0; i < 3; i++) gpio_set_level((gpio_num_t)kG3IllumGpio[i], 0);
    printf("  all three OFF again.\n");

    proved("nothing. This rung asserts; it cannot observe");
    not_proved("everything — including that a GPIO toggled at all. If NO "
               "strip lit, the ambiguity is real: put the meter on the "
               "2N2222 base (expect ~0.7 V when its channel is asserted) to "
               "split \"GPIO not driving\" from \"transistor/strip/+12V "
               "fault\". ALSO: LEDC IS NOT EXERCISED HERE. A strip that "
               "lights now and not under the real firmware is an LEDC "
               "configuration fault, not a wiring fault");
}

/* ================================================================== */
/* RUNG 7 — rail sanity                                                */
/* ================================================================== */
/*
 * The honest answer, because inventing a measurement is worse than not
 * having one.
 */
static void rung7_rails(void) {
    head("7", "RAIL SANITY — what this board can and cannot self-measure");

    printf("  NO RAIL ON THIS BOARD IS DIVIDED DOWN TO A READABLE PIN.\n"
           "  J_PWR_TAP (+12V / +5V / +3V3 / GND) is a HEADER FOR A METER —\n"
           "  it connects to no GPIO. There is nothing to sample, so this\n"
           "  rung reports NO MEASUREMENT rather than a number.\n\n");

    printf("  WHY IT CANNOT SIMPLY BE ADDED IN FIRMWARE: on the ESP32-C3 the\n"
           "  ADC-capable pins are ADC1 CH0-4 = GPIO0..4 and ADC2 CH0 =\n"
           "  GPIO5. This board uses GPIO0/1/3 for backlights, GPIO4 for SDA\n"
           "  and GPIO5 for SCL. THE ONLY FREE ADC PIN IS GPIO2 — which is a\n"
           "  BOOT STRAP, must be at its correct level at reset, and is\n"
           "  brought out UNLABELLED on J_EXP_ESP pin 1. A divider to GPIO2\n"
           "  is a board-revision decision, not a firmware change.\n\n");

    const esp_reset_reason_t rr = esp_reset_reason();
    printf("  THE ONE RAIL SIGNAL THAT DOES EXIST is the C3's brownout\n"
           "  detector, reported at rung 1: reset reason = %s.\n",
           rr == ESP_RST_BROWNOUT ? "BROWNOUT *** RAIL FAULT ***"
                                  : "not a brownout");

    proved("that the 3V3 rail at the module has not collapsed far enough to "
           "trip the brownout detector during this boot");
    not_proved("the value of ANY rail. +12V and +5V are entirely invisible "
               "to this MCU, and a 5V rail sagging to 4.0V — which would put "
               "the MCP23017's V_IH at 2.8V and make rung 3 PASS for the "
               "wrong reason — cannot be seen from here at all. RAILS ARE A "
               "METER QUESTION: PREFLIGHT §2");
}

/* ================================================================== */
/* Summary                                                             */
/* ================================================================== */

static void summary_block(void) {
    printf("\n");
    rule();
    printf("VERDICT — rack_triple_gauge diagnostic\n");
    printf("  build: %s (%08lx)\n", cxc_build_stamp(),
           (unsigned long)cxc_build_fingerprint());
    rule();
    if (g_stop_reason[0])
        printf("STOPPED: %s\n\n", g_stop_reason);

    printf("MACHINE-PROVEN (these can fail, and did not):\n");
    printf("  chain A (I2C -> MCP23017 -> L293D inputs): %s\n",
           g_fail_a ? "FAIL" : "pass");
    printf("  chain B (GPIO -> 2N2222):                  %s\n",
           g_fail_b ? "FAIL" : "asserted only — see below");
    printf("\nNEEDS EYES (no machine signal on this board can settle these):\n");
    printf("  [ ] rung 5 — did a needle twitch, and was it the gauge whose\n"
           "      index was printed? A swapped J_MOT still homes and runs\n"
           "      backwards forever.\n");
    printf("  [ ] rung 6 — did EXACTLY ONE strip light per channel, and was\n"
           "      it the one next to that gauge?\n");
    printf("  [ ] the LM7805 tab temperature after 10 minutes.\n");
    printf("\nNOT TESTED HERE, DELIBERATELY:\n");
    printf("  homing, needle positioning, the scene binding, WiFi, MQTT, the\n"
           "  config fetch. Homing drives a needle hard against a stop; that\n"
           "  is a mechanical event, not a diagnostic. Flash\n"
           "  [env:rack_triple_gauge] only after the rows above are settled.\n");
    rule();
}

/* ================================================================== */
extern "C" void app_main(void) {
    /* The C3's native USB-Serial-JTAG re-enumerates on reset, so the first
     * fraction of a second of output reliably goes nowhere. Nothing that
     * matters is printed before this delay, and the verdict is re-printed
     * on a loop besides. */
    vTaskDelay(pdMS_TO_TICKS(1200));

    printf("\n\n");
    rule();
    printf("rack_triple_gauge — ON-TARGET DIAGNOSTIC "
           "[env:rack_triple_gauge_selftest]\n");
    printf("  It drives NO needle to a position and it does NOT home.\n"
           "  Run the multimeter preflight FIRST:\n"
           "  docs/board-bringup/rack_triple_gauge-PREFLIGHT.md\n");
    rule();

    rung1_identity();
    rung2_bus_and_scan();

    if (!dev_open(kG3I2cHz)) {
        printf("\n*** FAIL — could not add the device handle at 0x%02X. ***\n",
               kG3McpAddr);
        g_fail_a++;
        stop_here("could not open an I2C device handle after a successful "
                  "probe — a driver fault, not a board fault.");
    }

    rung3_integrity();
    rung4_pins();
    rung5_l293d();
    rung6_backlights();
    rung7_rails();

    ESP_LOGI(TAG, "diagnostic complete");
    summary_block();

    /*
     * Repeat the two EYES rungs on a slow loop. The operator is standing at
     * a bench watching three needles and three strips and will want a
     * second look; making that cost a reflash is how a "did I see that?"
     * turns into a shrug. 15 s apart, so the coils are idle >95% of the
     * time.
     */
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(15000));
        printf("\n>>> repeating the NEEDS-EYES rungs (5 and 6). Reset to "
               "stop.\n");
        rung5_l293d();
        rung6_backlights();
        summary_block();
    }
}

#endif /* CXC_G3_DIAG_APP */
