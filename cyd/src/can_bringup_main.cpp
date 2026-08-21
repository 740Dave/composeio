/*
 * can_bringup_main.cpp — the fleet's FIRST-LIGHT CAN node.
 * [SPEC:CXC-BRINGUP] [SPEC:CXC-BOARD-HW] [BP-CAN-ID] [BP-TYPE-CODES]
 *
 * Built by [env:esp32_c3_can_bringup] on an ESP32-C3 Super Mini + SN65HVD230.
 * Headless: no glass, no scene, no config fetch, no radio.  It does exactly
 * four things, and the fourth is why it exists:
 *
 *   1. publish an incrementing heartbeat on CAN every 1 s   (MCU -> Pi)
 *   2. subscribe `throttle/left` and log every arrival      (Pi  -> MCU)
 *   3. publish twai_get_status_info() every 5 s             (the instrument)
 *   4. say the same thing on the console
 *
 * WHY (3) IS THE POINT.  A CAN teardown on 2026-08-12 found that bus_can.cpp
 * never calls twai_get_status_info(), never enables TWAI_ALERT_BUS_OFF, never
 * calls twai_initiate_recovery(), and answers connected() with `s_started` —
 * so a BUS-OFF ESP32 reports CONNECTED forever, and ESP-IDF's TWAI driver does
 * NOT auto-recover.  This app does not fix that (deliberately out of scope: a
 * recovery policy and a first light are two changes whose blast radii would
 * compound).  It builds the MISSING INSTRUMENT, so the state is observable
 * before anyone writes a policy for it.
 *
 * WHY IT NAMES bus_can_transport() DIRECTLY INSTEAD OF bus_transport().
 * Every leaf in this tree should go through the seam; this node is not a leaf,
 * it is a substrate TEST.  Routing it through bus_transport_select would mean
 * an NVS key or a compile default could silently put this build on MQTT — and
 * a CAN bring-up that quietly proved MQTT would be worse than no bring-up at
 * all.  Same reasoning relay_main.cpp gives for naming its two substrates.
 *
 * WHAT THE INSTRUMENTS CAN AND CANNOT SEE, stated up front because this is the
 * file whose numbers get believed:
 *   - the frame COUNTER below is fed by bus_can_set_raw_sink(), which fires
 *     BELOW the subscription set.  A frame that arrives for a namespace this
 *     node does not know still increments it.  That distinction matters: with
 *     only the subscribed path instrumented, a mis-wired CRX and a merely
 *     unregistered namespace look identical (zero arrivals, no log).
 *   - the TWAI counters come from the CONTROLLER, not from this code.  They
 *     are the one part of this report not read back through the assumption
 *     that wrote it.
 *   - "heartbeat published" means twai_transmit() returned ESP_OK, which on
 *     CAN means SOME node ACKed the frame.  That is a real property of the
 *     segment (unlike an SPI write's ESP_OK) — but it says nothing about
 *     whether the bridge could NAME the namespace.  candump is the witness.
 */

#ifdef CXC_CAN_BRINGUP_APP

#include "bus_can.h"
#include "bus_can_id.h"
#include "bus_frame.h"
#include "bus_raw.h"
#include "bus_transport.h"
#include "bus_wire_codes.h"
#include "config_fetch.h"       /* device_id() only */

#include <cstdio>
#include <cstring>

#include "driver/gpio.h"
#include "soc/gpio_reg.h"
#include "esp_rom_sys.h"
#include "driver/twai.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char* TAG = "canbring";

/* The one namespace this node LISTENS to.  It is deliberately an EXISTING
 * fleet namespace, not a new one invented for a test: the bridge's MQTT->CAN
 * direction is gated by CAN_BRIDGE_ALLOWLIST, which on the deployed rpi5 is
 * exactly `throttle/left,throttle/right`.  Picking anything else would have
 * required widening a production filter to prove a wire works. */
static const char* const kSubNs = "throttle/left";

/* ------------------------------------------------------------------ */
/* Namespaces this node SPEAKS                                         */
/* ------------------------------------------------------------------ */
/*
 * All of them are CAN-ONLY: nothing on MQTT ever publishes them, so the
 * bridge's self-populating registry can NEVER learn their names by
 * observation.  Every one must appear in CAN_BRIDGE_REGISTER or the bridge
 * drops it — correctly and loudly — as an unresolvable hash.  See
 * deploy/can_mqtt_bridge/composeio-can-bridge.env.
 */
static char s_ns_heartbeat[96];
static char s_ns_status[96];

/* twai_get_status_info() field -> namespace leaf.  Names match the ESP-IDF
 * struct member names verbatim so a reader can go from an MQTT topic to the
 * IDF header with no translation table. */
static const char* const kTwaiLeaves[] = {
    "tx_error_counter",
    "rx_error_counter",
    "tx_failed_count",
    "rx_missed_count",
    "rx_overrun_count",
    "bus_error_count",
    "msgs_to_rx",
};
static constexpr size_t kTwaiFieldCount =
    sizeof(kTwaiLeaves) / sizeof(kTwaiLeaves[0]);
static char s_ns_twai[kTwaiFieldCount][96];

/* ------------------------------------------------------------------ */
/* The below-the-namespace arrival counter                             */
/* ------------------------------------------------------------------ */
/*
 * Runs on the TWAI rx task.  It COUNTS and STASHES; it does not log.  A
 * per-frame ESP_LOG on a receive path is the diagnostic that causes the
 * failure it reports — 25 ESP_LOGW in 60 ms, at 115200 baud, delayed the very
 * queue draining being warned about (2026-08-11).  The 5 s status line prints
 * these numbers instead.
 */
static volatile uint32_t s_raw_frames = 0;
static volatile uint16_t s_last_ns_hash = 0;
static volatile uint8_t s_last_type = 0xFF;
static volatile uint8_t s_last_len = 0;

static void raw_count(uint16_t ns_hash, BusMessageType type, uint8_t scope_bits,
                      const uint8_t* value, size_t len, void* ud) {
    (void)scope_bits; (void)value; (void)ud;
    s_raw_frames++;
    s_last_ns_hash = ns_hash;
    s_last_type = (uint8_t)type;
    s_last_len = (uint8_t)len;
}

/* ------------------------------------------------------------------ */
/* TWAI state -> a name that fits a classic CAN frame                  */
/* ------------------------------------------------------------------ */
/*
 * SEVEN CHARACTERS, MAXIMUM, and that is a hard substrate limit, not taste:
 * bus_value_encode_str emits [len][utf8...] and a classic CAN data field is
 * EIGHT bytes, so 7 characters is the whole budget.  "ERROR-ACTIVE" does not
 * fit and is REFUSED by publish_state rather than truncated — a truncated
 * status string decodes cleanly as a different status, which is the worst
 * possible failure for a health namespace.  Hence "ACTIVE".
 *
 * ESP-IDF's twai_state_t does NOT expose error-active / error-warning /
 * error-passive; those are properties of the two error COUNTERS, which the
 * controller maintains.  Deriving them here is therefore reading hardware, not
 * reading our own assumption back.  Thresholds are the CAN 2.0B ones:
 * >=96 warning, >=128 passive, and bus-off is the controller's own state.
 */
static const char* twai_state_name(const twai_status_info_t& st) {
    switch (st.state) {
        case TWAI_STATE_STOPPED:    return "STOPPED";
        case TWAI_STATE_BUS_OFF:    return "BUSOFF";
        case TWAI_STATE_RECOVERING: return "RECOVER";
        case TWAI_STATE_RUNNING: {
            uint32_t worst = st.tx_error_counter > st.rx_error_counter
                                 ? st.tx_error_counter : st.rx_error_counter;
            if (worst >= 128) return "PASSIVE";
            if (worst >= 96)  return "WARN";
            return "ACTIVE";
        }
        default: return "UNKNOWN";
    }
}

/* ------------------------------------------------------------------ */
/* PIN DRIVE TEST — can this pad drive AT ALL, at DC-meter speed?       */
/* ------------------------------------------------------------------ */
/*
 * [env:esp32_c3_pin_drive_test] only, behind CXC_PIN_DRIVE_TEST.
 *
 * THE QUESTION IT ANSWERS.  With a DC meter on CTX during a transmit retry the
 * owner measured 1.603 V, holding steady — almost exactly half of the 3.25 V
 * rail.  That is EITHER a ~50% duty square wave being averaged by a meter that
 * cannot follow microsecond edges (GPIO5 driving hard, silicon fine) OR a pad
 * sitting at mid-rail because nothing is driving it at all.  A DC meter cannot
 * tell those apart, and the whole transmit investigation currently rests on
 * assuming the first.
 *
 * So: drive the SAME pad as a 0.33 Hz square wave — 1.5 s high, 1.5 s low —
 * which any DC meter follows trivially.  The needle either swings 0 <-> 3.3 V
 * or it does not, and the ambiguity is gone in one reading.
 *
 * WHAT THE READBACK CAN AND CANNOT SEE, because this is the crux.  The pad is
 * configured GPIO_MODE_INPUT_OUTPUT, so the level logged is read back through
 * the input buffer from the actual pad rather than from the output register.
 * That detects a pad held by something external.  It does NOT detect an
 * unbonded castellated joint: the DIE pad would read back perfectly while the
 * PCB pad is connected to nothing — and this exact board has already produced
 * a castellation that buzzed continuous on a meter and still was not bonded.
 * Hence the pairing that makes this test worth building:
 *
 *   readback OK + meter swings   -> the pin drives, and the fault is downstream
 *   readback OK + meter flat     -> silicon fine, JOINT or WIRE open  <-- the
 *                                   hypothesis that has never been tested
 *   readback wrong               -> something external is holding the pad
 *
 * A CONTROL PIN runs the identical code path afterwards, so "the firmware did
 * not really drive anything" is excluded by comparison rather than by trust.
 *
 * ⚠ IF THE TRANSCEIVER IS WORKING, THIS DRIVES THE BUS DOMINANT FOR 1.5 s AT A
 * TIME.  That will show up as bus errors on the Pi.  That is a POSITIVE result,
 * not damage — the Pi's bus-error counter is currently 0, so any movement is
 * signal.  The pad is left HIGH (recessive) at the end so the segment is not
 * held down when the test finishes.
 */
#ifdef CXC_PIN_DRIVE_TEST

/* The control pin.  GPIO7 rather than GPIO10 purely so the two are told apart
 * easily at the header; both go to the display, which is not driven and whose
 * firmware does not exist yet, so neither is in use.  What makes it a good
 * CONTROL is that it is a castellated pad on the same board, driven through the
 * same GPIO peripheral by the same function — so a difference between it and
 * GPIO5 isolates the JOINT or the WIRE, and cannot be the silicon or the code. */
#define CXC_PIN_DRIVE_CONTROL_GPIO 7

static void drive_square(int gpio, const char* what, int cycles, int half_ms) {
    gpio_config_t io = {};
    io.pin_bit_mask = 1ULL << gpio;
    io.mode = GPIO_MODE_INPUT_OUTPUT;      /* readback comes from the PAD */
    io.pull_up_en = GPIO_PULLUP_DISABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&io);

    int mismatches = 0;
    int64_t t0 = esp_timer_get_time();
    for (int c = 0; c < cycles; c++) {
        for (int level = 1; level >= 0; level--) {
            gpio_set_level((gpio_num_t)gpio, level);
            esp_rom_delay_us(50);                       /* settle before reading */
            int got = gpio_get_level((gpio_num_t)gpio);
            if (got != level) mismatches++;
            ESP_LOGW(TAG, "  t=%5.1fs  gpio%-2d (%s)  DRIVE %s (%.2f V expected) "
                     " -> pad reads %s   %s",
                     (double)(esp_timer_get_time() - t0) / 1e6, gpio, what,
                     level ? "HIGH" : "LOW ", level ? 3.3 : 0.0,
                     got ? "HIGH" : "LOW ",
                     got == level ? "match"
                                  : "*** MISMATCH — the pad is not following "
                                    "the chip ***");
            vTaskDelay(pdMS_TO_TICKS(half_ms));
        }
    }
    /* Leave RECESSIVE.  On CTX a stuck LOW holds the whole segment dominant. */
    gpio_set_level((gpio_num_t)gpio, 1);
    ESP_LOGW(TAG, "  gpio%d left HIGH (recessive).  %d readback mismatch(es) in "
             "%d transitions.", gpio, mismatches, cycles * 2);
}

static void pin_drive_test(void) {
    const int cycles = 5;          /* 5 x (1.5 s high + 1.5 s low) = 15 s */
    const int half_ms = 1500;

    ESP_LOGW(TAG, "================================================================");
    ESP_LOGW(TAG, "PIN DRIVE TEST — build %s %s", __DATE__, __TIME__);
    ESP_LOGW(TAG, "");
    ESP_LOGW(TAG, "PHASE 1 of 2 starts NOW and runs 15 s.");
    ESP_LOGW(TAG, "  PUT THE DC METER ON THE TRANSCEIVER'S CTX PIN (the D/TXD");
    ESP_LOGW(TAG, "  input, fed from C3 GPIO%d).", CXC_CAN_TX_GPIO);
    ESP_LOGW(TAG, "  EXPECT: 0 V and 3.3 V alternating every 1.5 s.");
    ESP_LOGW(TAG, "  If it sits near 1.6 V and never moves, GPIO%d is NOT",
             CXC_CAN_TX_GPIO);
    ESP_LOGW(TAG, "  driving the transceiver — which is the open question.");
    ESP_LOGW(TAG, "");
    ESP_LOGW(TAG, "  BONUS, same 1.5 s windows, no extra run: probe CANH and");
    ESP_LOGW(TAG, "  CANL while CTX is held LOW.  A working transceiver drives");
    ESP_LOGW(TAG, "  CANH ~3.5 V / CANL ~1.5 V (dominant) and both to ~2.5 V");
    ESP_LOGW(TAG, "  while CTX is HIGH.  That tests the DRIVER, not just the pin.");
    ESP_LOGW(TAG, "  NOTE: if it works this holds the bus dominant 1.5 s at a");
    ESP_LOGW(TAG, "  time and the Pi WILL log bus errors.  That is a PASS.");
    ESP_LOGW(TAG, "================================================================");
    vTaskDelay(pdMS_TO_TICKS(3000));

    drive_square(CXC_CAN_TX_GPIO, "CAN CTX", cycles, half_ms);

    ESP_LOGW(TAG, "");
    ESP_LOGW(TAG, "================================================================");
    ESP_LOGW(TAG, "PHASE 2 of 2 — THE CONTROL.  Starts in 8 s, runs 15 s.");
    ESP_LOGW(TAG, "  MOVE THE METER TO C3 PAD GPIO%d (a spare castellation; it",
             CXC_PIN_DRIVE_CONTROL_GPIO);
    ESP_LOGW(TAG, "  goes to the undriven display header, nothing else uses it).");
    ESP_LOGW(TAG, "  EXPECT the SAME 0 V / 3.3 V alternation every 1.5 s.");
    ESP_LOGW(TAG, "");
    ESP_LOGW(TAG, "  THIS IS THE COMPARISON THAT MAKES PHASE 1 MEAN ANYTHING:");
    ESP_LOGW(TAG, "    both swing      -> GPIO%d drives; the fault is past the pad",
             CXC_CAN_TX_GPIO);
    ESP_LOGW(TAG, "    only gpio%d swings -> GPIO%d's JOINT or WIRE is open, and",
             CXC_PIN_DRIVE_CONTROL_GPIO, CXC_CAN_TX_GPIO);
    ESP_LOGW(TAG, "                       the silicon and firmware are exonerated");
    ESP_LOGW(TAG, "    neither swings  -> suspect the meter or the probe point");
    ESP_LOGW(TAG, "  Probe it AT THE CASTELLATED PAD to include the joint.");
    ESP_LOGW(TAG, "================================================================");
    vTaskDelay(pdMS_TO_TICKS(8000));

    drive_square(CXC_PIN_DRIVE_CONTROL_GPIO, "CONTROL", cycles, half_ms);

    ESP_LOGW(TAG, "PIN DRIVE TEST COMPLETE.  Both pads left HIGH.  Normal "
             "diagnostics follow.");
}
#endif /* CXC_PIN_DRIVE_TEST */

/* ------------------------------------------------------------------ */
/* PRE-PROTOCOL PIN PROBE — the substrate answers before the protocol   */
/* ------------------------------------------------------------------ */
/*
 * "Never let a hardware fault and new firmware be suspects at the same time."
 * Runs BEFORE twai_driver_install(), so nothing here is my protocol: it is the
 * GPIO matrix reporting what the SN65HVD230 is actually doing to the pin.
 *
 * IT EXISTS BECAUSE THE OBVIOUS SIGNALS ARE AMBIGUOUS.  A TWAI controller with
 * tx_error_counter == 0 and a pinned transmit queue has NEVER STARTED a
 * transmission — it is waiting for bus-idle (11 recessive bits) that never
 * comes.  That is a different fault from "transmits, nothing ACKs" (which
 * drives TEC to 128 and then bus-off in about thirty attempts).  From inside
 * the driver the two are one line: "twai_transmit failed: ESP_ERR_TIMEOUT".
 *
 * THE DISCRIMINATOR IS THE PULL RESISTOR, and it is the whole point of doing
 * this twice:
 *
 *   no-pull=0, pull-up=0   -> the pin is DRIVEN LOW.  On CAN, low is DOMINANT,
 *                             so the controller sees a bus that is never idle.
 *                             Transceiver unpowered (an unpowered SN65HVD230
 *                             sinks its RXD through the ESD structure), RXD
 *                             shorted to GND, or the wrong pin.
 *   no-pull=0, pull-up=1   -> the pin is FLOATING.  Nothing is connected to
 *                             it; the transceiver's RXD is not on this GPIO.
 *   no-pull=1              -> idle recessive, which is the healthy state.
 *   transitions > 0        -> the line is CARRYING TRAFFIC.  This is the only
 *                             positive proof in the whole function, and it
 *                             needs a talker on the segment while it runs
 *                             (`cangen can1` on the Pi).
 *
 * CTX is sampled too, as an input, before the driver claims it: a CTX that is
 * externally driven is a wiring fault the transmit path cannot report.
 */
struct PinProbe {
    int lows_nopull, highs_nopull;
    int lows_pullup, highs_pullup;
    int transitions;
};

/* Sample `gpio` for `ms` milliseconds in 10 ms slices, yielding between them.
 *
 * THE YIELD IS LOAD-BEARING.  The first version of this probe span a tight
 * gpio_get_level() loop for 400 ms and tripped the task watchdog ("IDLE (CPU 0)
 * did not reset the watchdog in time") on the single-core C3 — a diagnostic
 * that crashed the boot it was diagnosing, which is the same failure shape as a
 * per-frame ESP_LOGW on a receive path.  10 ms of sampling still resolves a
 * 500 kbit/s CAN frame (a 1-bit time is 2 us) many times over. */
static void sample(int gpio, int ms, int* highs, int* lows, int* transitions) {
    int prev = -1;
    for (int slice = 0; slice < ms / 10; slice++) {
        int64_t end = esp_timer_get_time() + 10000;
        while (esp_timer_get_time() < end) {
            int v = gpio_get_level((gpio_num_t)gpio);
            if (v) (*highs)++; else (*lows)++;
            if (transitions && prev >= 0 && v != prev) (*transitions)++;
            prev = v;
        }
        vTaskDelay(1);
        prev = -1;              /* a gap is not a transition */
    }
}

static void probe_one(int gpio, int ms, PinProbe* out) {
    memset(out, 0, sizeof(*out));
    gpio_config_t io = {};
    io.pin_bit_mask = 1ULL << gpio;
    io.mode = GPIO_MODE_INPUT;
    io.intr_type = GPIO_INTR_DISABLE;

    /* pass 1 — floating input: what the far end actually drives */
    io.pull_up_en = GPIO_PULLUP_DISABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    gpio_config(&io);
    sample(gpio, ms, &out->highs_nopull, &out->lows_nopull, &out->transitions);

    /* pass 2 — internal pull-up: separates "driven low" from "floating" */
    io.pull_up_en = GPIO_PULLUP_ENABLE;
    gpio_config(&io);
    sample(gpio, ms, &out->highs_pullup, &out->lows_pullup, nullptr);

    /* Leave it as a plain floating input; twai_driver_install() reconfigures
     * both pins through the GPIO matrix immediately after. */
    io.pull_up_en = GPIO_PULLUP_DISABLE;
    gpio_config(&io);
}

static const char* verdict(const PinProbe& p) {
    if (p.transitions > 0)                    return "TOGGLING (traffic present)";
    if (p.highs_nopull && !p.lows_nopull)     return "steady HIGH (recessive/idle — healthy)";
    if (p.lows_nopull && !p.highs_nopull)
        return p.highs_pullup ? "steady LOW unloaded, HIGH with pull-up => FLOATING (nothing driving this pin)"
                              : "steady LOW even with pull-up => DRIVEN LOW (permanent DOMINANT — the bus never goes idle)";
    return "mixed without a clean transition — inconclusive";
}

static void probe_can_pins(int rounds, int ms) {
    ESP_LOGW(TAG, "PRE-PROTOCOL PIN PROBE: sampling CTX=%d / CRX=%d as plain "
             "GPIO inputs for %d x %d ms BEFORE the TWAI driver is installed. "
             "Run a talker on the segment now (`cangen can1 -g 5` on the Pi) "
             "if you want the TOGGLING verdict.",
             CXC_CAN_TX_GPIO, CXC_CAN_RX_GPIO, rounds, ms * 2);
    for (int r = 0; r < rounds; r++) {
        PinProbe rx, tx;
        probe_one(CXC_CAN_RX_GPIO, ms, &rx);
        probe_one(CXC_CAN_TX_GPIO, ms, &tx);
        ESP_LOGW(TAG, "  round %d  CRX(gpio%d): nopull hi/lo=%d/%d  pullup "
                 "hi/lo=%d/%d  transitions=%d  => %s", r + 1, CXC_CAN_RX_GPIO,
                 rx.highs_nopull, rx.lows_nopull, rx.highs_pullup,
                 rx.lows_pullup, rx.transitions, verdict(rx));
        ESP_LOGW(TAG, "  round %d  CTX(gpio%d): nopull hi/lo=%d/%d  pullup "
                 "hi/lo=%d/%d  transitions=%d  => %s", r + 1, CXC_CAN_TX_GPIO,
                 tx.highs_nopull, tx.lows_nopull, tx.highs_pullup,
                 tx.lows_pullup, tx.transitions, verdict(tx));
    }
}

/* ------------------------------------------------------------------ */
/* LISTEN-ONLY TRIAL — the receive path, with the transmitter removed  */
/* ------------------------------------------------------------------ */
/*
 * TWAI_MODE_LISTEN_ONLY does not drive the bus AT ALL — not even the ACK bit.
 * That makes it two things at once:
 *
 *   1. SAFE on a live segment.  It cannot disturb the rpi5 bridge's traffic,
 *      so this can run against a production bus.
 *   2. The only way to test CRX WITHOUT CTX.  In normal mode the two are
 *      entangled: a node that cannot drive CTX cannot ACK, cannot signal an
 *      error flag, and racks up bus errors that look like receive faults.  The
 *      first bring-up attempt showed exactly that confusion — rx_error_counter
 *      129 and bus_error_count 18 within one second, which reads like "the RX
 *      wiring is wrong" and was nothing of the kind.
 *
 * A NON-ZERO FRAME COUNT HERE IS THE STRONGEST SINGLE FACT IN THIS BRING-UP.
 * It proves, in one number: the transceiver is powered, CANH/CANL are the
 * right way round, the segment is terminated, the bit rate and sample point
 * match the Pi, and CRX is on the pin under test.  A wrong answer to any one
 * of those gives zero.  It needs a talker on the segment while it runs.
 */
static int listen_only_trial(int rx_gpio, int tx_gpio, int ms,
                             const twai_timing_config_t& t_in,
                             const char* rate_name) {
    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
        (gpio_num_t)tx_gpio, (gpio_num_t)rx_gpio, TWAI_MODE_LISTEN_ONLY);
    g.rx_queue_len = 32;
    twai_timing_config_t t = t_in;
    twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    if (twai_driver_install(&g, &t, &f) != ESP_OK) {
        ESP_LOGE(TAG, "  LISTEN-ONLY rx=gpio%d: driver install FAILED", rx_gpio);
        return -1;
    }
    if (twai_start() != ESP_OK) {
        twai_driver_uninstall();
        ESP_LOGE(TAG, "  LISTEN-ONLY rx=gpio%d: start FAILED", rx_gpio);
        return -1;
    }

    int n = 0;
    uint32_t ids[3] = {0, 0, 0};
    int nid = 0;
    int64_t end = esp_timer_get_time() + (int64_t)ms * 1000;
    while (esp_timer_get_time() < end) {
        twai_message_t m;
        if (twai_receive(&m, pdMS_TO_TICKS(50)) != ESP_OK) continue;
        if (nid < 3) ids[nid++] = m.identifier;
        n++;
    }
    twai_status_info_t st = {};
    twai_get_status_info(&st);
    ESP_LOGW(TAG, "  LISTEN-ONLY rx=gpio%-2d @ %-6s: %3d frame(s) in %d ms | "
             "bus_err=%lu | first ids %08lX %08lX %08lX",
             rx_gpio, rate_name, n, ms,
             (unsigned long)st.bus_error_count,
             (unsigned long)ids[0], (unsigned long)ids[1], (unsigned long)ids[2]);
    twai_stop();
    twai_driver_uninstall();
    return n;
}

/* ------------------------------------------------------------------ */
/* BIT-TIME MEASUREMENT — ask the WIRE what rate it is running at      */
/* ------------------------------------------------------------------ */
/*
 * The listen-only trial answers "can this controller decode this line", which
 * conflates the pin, the bit rate and the sample point.  This answers the one
 * narrower question nothing else on the board can: HOW LONG IS A BIT.
 *
 * METHOD, AND WHY IT IS THIS ONE.  The first version of this function span a
 * sampling loop that checked esp_timer every 100 reads, and reported "shortest
 * dominant run = 1 sample => ~5812 kbit/s" with total confidence.  It was
 * measuring ITSELF: the timer call left a ~15 us blind gap between bursts, and
 * a level change across a gap looks exactly like a one-sample pulse.  The
 * giveaway was that the pulse COUNT (23574 in 500 ms) matched the GAP count
 * almost exactly.  An instrument whose output is dominated by its own sampling
 * artefacts is worse than no instrument, because the number looks like data.
 *
 * So: capture one UNINTERRUPTED burst into RAM with interrupts off, timing the
 * whole burst end to end, then analyse it afterwards.  No gaps, so no phantom
 * pulses.  Runs that touch either end of the buffer are discarded — they are
 * truncated by the window, not by the signal.
 *
 * On CAN the shortest dominant pulse is exactly ONE BIT TIME: bit stuffing
 * guarantees no more than five identical bits in a row, and isolated single
 * bits are everywhere in a real frame.  So min(dominant run) * sample period
 * is the bit time, and its reciprocal is the line rate — measured off the
 * wire, independent of what either end was configured to do.
 *
 * Deliberately NOT a substitute for a scope: it sees logic levels only, and is
 * blind to ringing, slew and a bus that is electrically marginal.
 */
static constexpr int kCapSamples = 16384;
static uint8_t s_cap[kCapSamples];

static void measure_bit_time(int gpio, int attempts) {
    gpio_config_t io = {};
    io.pin_bit_mask = 1ULL << gpio;
    io.mode = GPIO_MODE_INPUT;
    io.pull_up_en = GPIO_PULLUP_DISABLE;
    io.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&io);

    const uint32_t mask = 1u << gpio;

    for (int a = 0; a < attempts; a++) {
        portDISABLE_INTERRUPTS();
        int64_t t0 = esp_timer_get_time();
        for (int i = 0; i < kCapSamples; i++)
            s_cap[i] = (REG_READ(GPIO_IN_REG) & mask) ? 1 : 0;
        int64_t t1 = esp_timer_get_time();
        portENABLE_INTERRUPTS();

        double ns = (double)(t1 - t0) * 1000.0 / (double)kCapSamples;

        /* Run-length analysis, discarding the first and last runs: both are
         * clipped by the capture window rather than by the signal. */
        uint32_t min_low = 0xFFFFFFFF, min_high = 0xFFFFFFFF;
        int lows = 0, run = 1, first_run = 1;
        for (int i = 1; i < kCapSamples; i++) {
            if (s_cap[i] == s_cap[i - 1]) { run++; continue; }
            if (!first_run) {
                if (s_cap[i - 1] == 0) {
                    if ((uint32_t)run < min_low) min_low = run;
                    lows++;
                } else if ((uint32_t)run < min_high) min_high = run;
            }
            first_run = 0;
            run = 1;
        }

        if (min_low == 0xFFFFFFFF) {
            if (a == attempts - 1)
                ESP_LOGW(TAG, "  BIT-TIME gpio%d: no complete dominant pulse in "
                         "%d attempts x %d samples (%.0f ns/sample, %.0f us "
                         "window) — line idle or undriven", gpio, attempts,
                         kCapSamples, ns, ns * kCapSamples / 1000.0);
            vTaskDelay(pdMS_TO_TICKS(7));
            continue;
        }

        double bit_ns = min_low * ns;
        ESP_LOGW(TAG, "  BIT-TIME gpio%d: %.0f ns/sample, %.0f us window | "
                 "shortest dominant run %lu samples = %.0f ns => %.1f kbit/s | "
                 "shortest recessive run %lu | %d complete dominant pulses",
                 gpio, ns, ns * kCapSamples / 1000.0, (unsigned long)min_low,
                 bit_ns, 1e6 / bit_ns, (unsigned long)min_high, lows);
        return;
    }
}

/* ------------------------------------------------------------------ */
/* MCU-SIDE SELF-TEST — everything except the wire                     */
/* ------------------------------------------------------------------ */
/*
 * TWAI_MODE_NO_ACK plus a self-reception request, with CTX AND CRX ASSIGNED TO
 * THE SAME UNCONNECTED PAD, so the GPIO matrix feeds the controller's own
 * output straight back to its input.  No peer, no transceiver, no segment.
 *
 * THE SHARED PAD IS THE WHOLE TRICK, and it was learned the hard way: NO_ACK
 * mode alone is NOT an internal loopback on this silicon.  The controller
 * still watches the real CRX pin for bit errors, so the first version of this
 * test — NO_ACK on the live pins — drove tx_error_counter to 128 and reported
 * "FAIL, the fault is on the MCU side" while the MCU side was in fact fine.
 * A self-test that fails because of the thing it is supposed to exclude is
 * worse than none: it accuses the wrong component with total confidence.
 *
 * WHY IT EARNS ITS PLACE.  When a link does not work, the value of a test is
 * how much it EXCLUDES.  This one passes or fails on: the TWAI peripheral
 * itself, the bit-timing configuration being self-consistent, the [BP-CAN-ID]
 * arbitration-id encoder, the [BP-TYPE-CODES] type nibble, and the
 * bus_frame value encoder.  If it passes, every remaining suspect is OUTSIDE
 * the MCU — which turns "CAN does not work" into "the wire between GPIO4/5 and
 * the segment is wrong", and that is a sentence somebody at the bench can act
 * on.
 *
 * IT IS ALSO THE ONE CLAIM HERE THAT IS *NOT* SELF-VERIFYING BY ACCIDENT: the
 * id is rebuilt from the DECODED frame and compared against the value computed
 * independently in Python (composeio.config.namespace_hash), which is printed
 * beside it so the two can be read against each other rather than assumed
 * equal.
 *
 * ⚠ ASYMMETRIC EVIDENCE — READ THIS BEFORE BELIEVING A FAILURE.
 * This test returned PASS (id and payload matched byte for byte, tx_err=0) and
 * later returned FAIL (tx_err pinned at 128) for the SAME BINARY on the SAME
 * BOARD, across three unconnected pads, with every one of those pads proven by
 * a drive-and-read-back test to follow the chip perfectly.  The cause is that
 * with tx_io == rx_io the driver configures ONE pad twice, once as output and
 * once as input, and the surviving direction is not something this function
 * controls; `gpio_set_direction(GPIO_MODE_INPUT_OUTPUT)` after install makes
 * the outcome DETERMINISTIC but did not make it pass.
 *
 * So the verdict is asymmetric and the log says so: a PASS is real evidence
 * (those bytes had to come from somewhere), a FAIL is the ABSENCE of evidence
 * and must never be read as "the MCU is broken".  An earlier report in this
 * bring-up leaned on a PASS from this test as a headline proof; that claim was
 * withdrawn when the test proved unrepeatable.  An unrepeatable test is not a
 * measurement.
 */
static bool self_test_loopback(int loop_gpio) {
    /* One pad, both roles.  It must be a pin with NOTHING attached — on the
     * C3 Super Mini, GPIO10 (GPIO2/8/9 are strapping, 18/19 are USB, 20/21 are
     * UART0, 4/5 are the CAN pair under investigation). */
    twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
        (gpio_num_t)loop_gpio, (gpio_num_t)loop_gpio, TWAI_MODE_NO_ACK);
    g.rx_queue_len = 8;
    g.tx_queue_len = 8;
    twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();
    twai_filter_config_t f = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    if (twai_driver_install(&g, &t, &f) != ESP_OK) {
        ESP_LOGE(TAG, "  SELF-TEST: could not install TWAI in NO_ACK mode "
                 "on gpio%d", loop_gpio);
        return false;
    }

    /* THE LINE THAT MAKES THIS TEST REPRODUCIBLE, and its absence is why an
     * earlier version of this bring-up reported a PASS it could not repeat.
     *
     * With tx_io == rx_io the driver configures the SAME pad twice — once as an
     * OUTPUT for CTX and once as an INPUT for CRX — and whichever call lands
     * last wins.  Configured input-only, the pad's output driver is off, so the
     * controller drives dominant and reads back recessive: a bit error every
     * time, tx_error_counter climbing, and a "the MCU is broken" verdict from a
     * test whose whole purpose is to exonerate the MCU.  Configured
     * output-only, the input buffer is off and nothing is received.  Which of
     * the two happens depends on pad state this function does not own, so the
     * SAME BINARY passed and failed on the SAME HARDWARE.
     *
     * GPIO_MODE_INPUT_OUTPUT enables both buffers on the real pad and leaves
     * the GPIO-matrix routing intact, so the loopback is what it claims to be.
     */
    gpio_set_direction((gpio_num_t)loop_gpio, GPIO_MODE_INPUT_OUTPUT);

    if (twai_start() != ESP_OK) {
        ESP_LOGE(TAG, "  SELF-TEST: could not start TWAI on gpio%d", loop_gpio);
        twai_driver_uninstall();
        return false;
    }

    uint8_t code = 0;
    bus_wire_code_for_type(BusMessageType::Heartbeat, &code);
    /* [BP-CAN-ID] v2.  `src` is an ALLOCATED node id, not a hash of the device
     * id — this bring-up tool has none allocated, so it transmits as UNASSIGNED
     * and says so, which is the honest answer rather than a fabricated one. */
    uint32_t want_id = bus_can_id_encode(
        bus_priority_for(s_ns_heartbeat, BusMessageType::Heartbeat),
        bus_ns_hash(s_ns_heartbeat), code, kBusCanNodeUnassigned,
        bus_scope_bits_for_name("global"));

    uint8_t v[kBusValueMaxNumeric];
    size_t n = bus_value_encode_f32(7.0f, v);

    twai_message_t m = {};
    m.identifier = want_id;
    m.extd = 1;
    m.self = 1;                          /* self-reception request */
    m.data_length_code = (uint8_t)n;
    memcpy(m.data, v, n);

    bool ok = false;
    if (twai_transmit(&m, pdMS_TO_TICKS(200)) == ESP_OK) {
        twai_message_t r;
        if (twai_receive(&r, pdMS_TO_TICKS(500)) == ESP_OK) {
            bool id_ok = (r.identifier == want_id) && r.extd;
            bool len_ok = (r.data_length_code == n);
            bool data_ok = len_ok && memcmp(r.data, v, n) == 0;
            uint16_t got_ns = bus_can_id_ns_hash(r.identifier);
            uint8_t got_type = bus_can_id_type_code(r.identifier);
            ok = id_ok && data_ok;
            ESP_LOGW(TAG, "  SELF-TEST loopback: sent id 0x%08lX, got 0x%08lX "
                     "(ns 0x%04X, type %u, %u byte(s)) -> id %s, payload %s",
                     (unsigned long)want_id, (unsigned long)r.identifier,
                     got_ns, (unsigned)got_type, (unsigned)r.data_length_code,
                     id_ok ? "MATCH" : "MISMATCH",
                     data_ok ? "MATCH" : "MISMATCH");
        } else {
            ESP_LOGE(TAG, "  SELF-TEST loopback: transmitted but NOTHING came "
                     "back — the TWAI peripheral itself is not looping back");
        }
    } else {
        ESP_LOGE(TAG, "  SELF-TEST loopback: twai_transmit refused in NO_ACK "
                 "mode — the controller could not put a frame on its own wire");
    }

    twai_status_info_t st = {};
    twai_get_status_info(&st);
    ESP_LOGW(TAG, "  SELF-TEST result: %s | tx_err=%lu rx_err=%lu bus_err=%lu",
             ok ? "PASS — the MCU-side stack (peripheral, bit timing, "
                  "[BP-CAN-ID] encoder, [BP-TYPE-CODES] nibble, value codec) "
                  "round-tripped a frame with no wire involved"
                : "INCONCLUSIVE — NOT an MCU indictment.  This test is not "
                  "reliably reproducible on this part (see the note above); it "
                  "has returned PASS and FAIL for the same binary on the same "
                  "hardware.  A PASS here is evidence; a FAIL is only the "
                  "absence of evidence.  Do NOT conclude the MCU is at fault",
             (unsigned long)st.tx_error_counter,
             (unsigned long)st.rx_error_counter,
             (unsigned long)st.bus_error_count);

    twai_stop();
    twai_driver_uninstall();
    return ok;
}

/* ------------------------------------------------------------------ */

static void build_namespaces(const char* id) {
    snprintf(s_ns_heartbeat, sizeof(s_ns_heartbeat), "_device/%s/heartbeat", id);
    snprintf(s_ns_status, sizeof(s_ns_status), "_device/%s/status", id);
    for (size_t i = 0; i < kTwaiFieldCount; i++)
        snprintf(s_ns_twai[i], sizeof(s_ns_twai[i]), "_device/%s/twai/%s", id,
                 kTwaiLeaves[i]);
}

/* The arbitration ids this node WILL emit, printed at boot.
 *
 * THIS IS CONVENIENCE, NOT PROOF, and the banner says so.  These come from the
 * same bus_can_id_encode() / bus_ns_hash() that writes the frames, so they
 * cannot disagree with them — a property read back through the assumption that
 * wrote it.  The proof is a candump on can1 compared against an id computed
 * independently from composeio.config.namespace_hash on the host. */
static void print_expected_ids(const char* id) {
    (void)id;
    const uint8_t src = kBusCanNodeUnassigned;    /* nothing allocates ids yet */
    const uint8_t scope = bus_scope_bits_for_name("global");
    uint8_t hb_code = 0, st_code = 0;
    bus_wire_code_for_type(BusMessageType::Heartbeat, &hb_code);
    bus_wire_code_for_type(BusMessageType::State, &st_code);

    ESP_LOGI(TAG, "[BP-CAN-ID] v2 prio(3)|type(4)|ns(16)|src(4)|scope(2), "
             "src=%u (UNASSIGNED), scope=global (self-computed — VERIFY "
             "against candump + composeio.config.namespace_hash, do NOT take "
             "these as evidence)", (unsigned)src);
    ESP_LOGI(TAG, "  %-46s ns=0x%04X type=%u prio=%u id=0x%08lX", s_ns_heartbeat,
             bus_ns_hash(s_ns_heartbeat), hb_code,
             (unsigned)bus_priority_for(s_ns_heartbeat,
                                        BusMessageType::Heartbeat),
             (unsigned long)bus_can_id_encode(
                 bus_priority_for(s_ns_heartbeat, BusMessageType::Heartbeat),
                 bus_ns_hash(s_ns_heartbeat), hb_code, src, scope));
    ESP_LOGI(TAG, "  %-46s ns=0x%04X type=%u prio=%u id=0x%08lX", s_ns_status,
             bus_ns_hash(s_ns_status), st_code,
             (unsigned)bus_priority_for(s_ns_status, BusMessageType::State),
             (unsigned long)bus_can_id_encode(
                 bus_priority_for(s_ns_status, BusMessageType::State),
                 bus_ns_hash(s_ns_status), st_code, src, scope));
    for (size_t i = 0; i < kTwaiFieldCount; i++)
        ESP_LOGI(TAG, "  %-46s ns=0x%04X type=%u prio=%u id=0x%08lX",
                 s_ns_twai[i], bus_ns_hash(s_ns_twai[i]), st_code,
                 (unsigned)bus_priority_for(s_ns_twai[i],
                                            BusMessageType::State),
                 (unsigned long)bus_can_id_encode(
                     bus_priority_for(s_ns_twai[i], BusMessageType::State),
                     bus_ns_hash(s_ns_twai[i]), st_code, src, scope));
    ESP_LOGI(TAG, "  %-46s ns=0x%04X  (SUBSCRIBED — inbound)", kSubNs,
             bus_ns_hash(kSubNs));
}

static void publish_status(BusTransport& bus, uint32_t hb) {
    twai_status_info_t st = {};
    esp_err_t e = twai_get_status_info(&st);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "twai_get_status_info failed: %s — THE HEALTH REPORT "
                 "BELOW IS ABSENT, not zero", esp_err_to_name(e));
        return;
    }

    const char* name = twai_state_name(st);

    /* Console first, and with everything, because the console is the only
     * place all eight numbers fit in one line.  Eight bytes of classic CAN
     * cannot carry a health struct; it goes up as one namespace per field. */
    ESP_LOGI(TAG, "TWAI %s | tx_err=%lu rx_err=%lu tx_failed=%lu rx_missed=%lu "
             "rx_overrun=%lu bus_err=%lu msgs_to_rx=%lu msgs_to_tx=%lu | "
             "hb=%lu raw_rx_frames=%lu (last ns=0x%04X type=%u len=%u)",
             name,
             (unsigned long)st.tx_error_counter, (unsigned long)st.rx_error_counter,
             (unsigned long)st.tx_failed_count, (unsigned long)st.rx_missed_count,
             (unsigned long)st.rx_overrun_count, (unsigned long)st.bus_error_count,
             (unsigned long)st.msgs_to_rx, (unsigned long)st.msgs_to_tx,
             (unsigned long)hb, (unsigned long)s_raw_frames,
             s_last_ns_hash, (unsigned)s_last_type, (unsigned)s_last_len);

    if (st.state == TWAI_STATE_BUS_OFF)
        ESP_LOGE(TAG, "BUS-OFF.  ESP-IDF's TWAI driver does NOT recover on its "
                 "own and bus_can.cpp never calls twai_initiate_recovery(), so "
                 "this node is now permanently deaf and mute while "
                 "BusTransport::connected() keeps answering true.  That gap is "
                 "reported, not fixed, by this build.");

    bus.publish_state(s_ns_status, name);

    const uint32_t vals[kTwaiFieldCount] = {
        st.tx_error_counter, st.rx_error_counter, st.tx_failed_count,
        st.rx_missed_count,  st.rx_overrun_count, st.bus_error_count,
        st.msgs_to_rx,
    };
    for (size_t i = 0; i < kTwaiFieldCount; i++)
        bus.publish_number(s_ns_twai[i], (double)vals[i]);
}

static void drain(BusTransport& bus) {
    BusEvent ev;
    while (bus.poll(&ev)) {
        switch (ev.kind) {
            case BusEvent::Kind::Number:
                ESP_LOGI(TAG, "RX  %s = %.6g  (type=%s scope=%s)", ev.ns, ev.num,
                         bus_message_type_name(ev.type)
                             ? bus_message_type_name(ev.type) : "?",
                         ev.scope);
                break;
            case BusEvent::Kind::Bool:
                ESP_LOGI(TAG, "RX  %s = %s  (type=%s scope=%s)", ev.ns,
                         ev.b ? "true" : "false",
                         bus_message_type_name(ev.type)
                             ? bus_message_type_name(ev.type) : "?",
                         ev.scope);
                break;
            case BusEvent::Kind::String:
                ESP_LOGI(TAG, "RX  %s = \"%s\"  (type=%s scope=%s)", ev.ns,
                         ev.str,
                         bus_message_type_name(ev.type)
                             ? bus_message_type_name(ev.type) : "?",
                         ev.scope);
                break;
        }
    }
}

extern "C" void app_main(void) {
    /* The build stamp is COMPILER-WRITTEN.  A hand-maintained tag read "TF13"
     * through ~10 flashes across three boards on 2026-08-11, including one that
     * did not exist when the string was written — the instrument built to
     * detect staleness had itself gone stale.  __DATE__/__TIME__ cannot. */
    ESP_LOGI(TAG, "==================================================");
    ESP_LOGI(TAG, "ComposeIO CAN FIRST-LIGHT node");
    ESP_LOGI(TAG, "build: %s %s", __DATE__, __TIME__);
    ESP_LOGI(TAG, "device id: %s", device_id());
    ESP_LOGI(TAG, "CAN pins (compile defaults): CTX=%d CRX=%d @ %d bit/s",
             CXC_CAN_TX_GPIO, CXC_CAN_RX_GPIO, CXC_CAN_BITRATE);
    ESP_LOGI(TAG, "==================================================");

    /* bus_can's load_config() reads NVS (can.tx / can.rx / can.bitrate) so a
     * rig is re-pointed without a reflash.  Without an initialised partition
     * that read fails SILENTLY and the compiled defaults stand — which is the
     * right answer here but for the wrong reason, so initialise it. */
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        e = nvs_flash_init();
    }
    if (e != ESP_OK)
        ESP_LOGW(TAG, "nvs_flash_init: %s — NVS pin overrides are NOT readable "
                 "this boot; the compiled defaults above are what will be used",
                 esp_err_to_name(e));

    build_namespaces(device_id());

#ifdef CXC_PIN_DRIVE_TEST
    /* FIRST, before any other diagnostic.  The owner is standing at the bench
     * with a meter; making him wait 18 s for the rest of the ladder is how a
     * measurement gets taken at the wrong moment. */
    pin_drive_test();
#endif

    /* SELF-TEST FIRST, before ANY pin has been touched.  It passed earlier in
     * this bring-up and later failed on three separate unconnected pads, and
     * the two candidate explanations — the probes leave pads in a state the
     * TWAI driver does not re-establish, versus something changed off-chip —
     * are distinguished only by running it on a virgin GPIO config. */
    /* DRIVE-AND-READ-BACK — the most primitive electrical question there is,
     * and it is not TWAI, not the transport, not the protocol.  Set the pin as
     * an output, drive it LOW, read it back.  If it reads HIGH, something
     * OUTSIDE the chip is holding it up and no amount of controller
     * configuration will fix it.
     *
     * Added because the loopback self-test — which is internal and needs no
     * wiring — PASSED earlier in this bring-up (tx_err=0, id and payload
     * matched) and later FAILED on three separate supposedly-unconnected pads
     * with tx_error_counter climbing.  A bit error on a self-loopback means the
     * controller drove dominant and read back recessive, which is exactly what
     * a pin that cannot be pulled low looks like.  This test says so in one
     * bit, with no CAN semantics in the way. */
    {
        const int pads[] = {10, 7, 3, 6, 5};
        for (int i = 0; i < 5; i++) {
            gpio_config_t io = {};
            io.pin_bit_mask = 1ULL << pads[i];
            io.mode = GPIO_MODE_INPUT_OUTPUT;   /* readback of the real pad */
            io.pull_up_en = GPIO_PULLUP_DISABLE;
            io.pull_down_en = GPIO_PULLDOWN_DISABLE;
            io.intr_type = GPIO_INTR_DISABLE;
            gpio_config(&io);
            gpio_set_level((gpio_num_t)pads[i], 0);
            esp_rom_delay_us(200);
            int low = gpio_get_level((gpio_num_t)pads[i]);
            gpio_set_level((gpio_num_t)pads[i], 1);
            esp_rom_delay_us(200);
            int high = gpio_get_level((gpio_num_t)pads[i]);
            gpio_set_level((gpio_num_t)pads[i], 1);
            ESP_LOGW(TAG, "  DRIVE gpio%-2d: drove LOW -> read %d, drove HIGH -> "
                     "read %d  %s", pads[i], low, high,
                     (low == 0 && high == 1)
                         ? "OK (the pad follows the chip)"
                         : (low == 1 ? "*** CANNOT PULL LOW — held HIGH from "
                                       "OUTSIDE the chip ***"
                                     : "*** CANNOT DRIVE HIGH ***"));
            io.mode = GPIO_MODE_INPUT;
            gpio_config(&io);
        }
    }

    ESP_LOGW(TAG, "SELF-TEST (virgin pins, before any probe):");
    self_test_loopback(10);


    /* BEFORE the driver claims the pins.  See probe_can_pins()'s note: a
     * transmit that never starts and a transmit that nobody ACKs are one log
     * line from inside TWAI, and they are different faults with different
     * fixes. */
    probe_can_pins(2, 400);

    /* Then the receive path alone, on BOTH candidate pins.  Two trials, one
     * decisive answer: whichever pin returns frames is CRX, and it says so
     * without the transmitter ever being a suspect. */
    /* Named by the DEFINES, never by a literal.  The first bring-up carried
     * hand-typed 4/5 here as well as in the CMake branch, so a pin-map
     * correction had to be made in two places to be true — the shape of a
     * value that requires maintenance and will therefore rot. */
    measure_bit_time(CXC_CAN_RX_GPIO, 40);
    measure_bit_time(CXC_CAN_TX_GPIO, 40);

    /* Prove OUR side before blaming anyone else's. */
    /* SEVERAL PADS, not one.  The loopback passed on GPIO10 earlier in this
     * bring-up and later failed on it with tx_err climbing — which on a pad
     * that is supposed to be unconnected means the PAD acquired something,
     * not that the controller broke.  Trying more than one pad is what tells
     * those apart; a single-pad self-test cannot distinguish 'the MCU is
     * broken' from 'this particular pin is now wired'. */
    {
        const int pads[] = {10, 7, 3};
        for (int i = 0; i < 3; i++) {
            PinProbe pp; probe_one(pads[i], 100, &pp);
            ESP_LOGW(TAG, "  loopback pad gpio%d before test: nopull hi/lo=%d/%d "
                     "pullup hi/lo=%d/%d => %s", pads[i], pp.highs_nopull,
                     pp.lows_nopull, pp.highs_pullup, pp.lows_pullup, verdict(pp));
            if (self_test_loopback(pads[i])) break;
        }
    }

    /* WHOLE-HEADER SCAN.  Having established that neither GPIO4 nor GPIO5
     * carries a decodable CAN receive signal, the next question is whether ANY
     * pin on this board does — i.e. whether the transceiver's R output simply
     * landed somewhere else.  Cheap, passive, and it either finds the pin or
     * excludes the entire header, which is the difference between "try this
     * pin" and "the transceiver is not delivering a signal at all".
     *
     * A 500 kbit/s CAN receive line answers with a shortest dominant run of
     * ~2000 ns.  Anything an order of magnitude below that is ringing or
     * coupling, not data.
     *
     * Skipped: 8/9 (strapping), 18/19 (native USB — the console this report
     * arrives on), 10 (just used as the loopback pad). */
    ESP_LOGW(TAG, "GPIO SCAN — looking for a pin whose shortest dominant pulse "
             "is ~2000 ns (one bit at 500 kbit/s).  Needs dense traffic on the "
             "segment.");
    {
        const int pins[] = {0, 1, 2, 3, 4, 5, 6, 7, 10, 20, 21};
        for (int i = 0; i < (int)(sizeof(pins) / sizeof(pins[0])); i++)
            measure_bit_time(pins[i], 25);
    }

    ESP_LOGW(TAG, "LISTEN-ONLY TRIALS — safe on a live segment (this mode never "
             "drives the bus, not even ACK).  Needs a talker: run "
             "`cangen can1 ...` on the Pi NOW.");
    {
        struct { twai_timing_config_t t; const char* name; } rates[] = {
            { TWAI_TIMING_CONFIG_1MBITS(),   "1M"   },
            { TWAI_TIMING_CONFIG_800KBITS(), "800k" },
            { TWAI_TIMING_CONFIG_500KBITS(), "500k" },
            { TWAI_TIMING_CONFIG_250KBITS(), "250k" },
            { TWAI_TIMING_CONFIG_125KBITS(), "125k" },
            { TWAI_TIMING_CONFIG_100KBITS(), "100k" },
        };
        /* THE SWEEP IS THE POINT.  "500 kbit/s" is what both ends were
         * CONFIGURED to; a controller that decodes nothing is evidence that
         * one of them is not achieving it.  Trying every rate the driver has a
         * table for turns a belief into a measurement — and if none of them
         * decodes, the fault is not the bit rate and the sweep has excluded a
         * whole class instead of leaving it as a suspicion. */
        for (auto& r : rates)
            listen_only_trial(CXC_CAN_RX_GPIO, CXC_CAN_TX_GPIO, 1200, r.t,
                              r.name);
    }

    BusTransport& bus = bus_can_transport();
    ESP_LOGI(TAG, "transport: %s (needs_network=%s)", bus.name(),
             bus.needs_network() ? "yes" : "NO — radio stays off");

    bool up = bus.start(device_id());
    if (!up) {
        /* transport_registry.py rule 2: a KNOWN transport whose backend is
         * unreachable degrades LOUDLY and the node keeps running. */
        ESP_LOGE(TAG, "CAN DID NOT START.  Everything below this line is a "
                 "report about a link that is DOWN.");
    }

    print_expected_ids(device_id());

    /* Below-the-namespace arrival counting.  See raw_count()'s note: without
     * it, a mis-wired CRX and an unregistered namespace are the same signal. */
    bus_can_set_raw_sink(raw_count, nullptr);

    bus.subscribe(kSubNs);

    uint32_t hb = 0;
    int64_t next_hb_us = 0;
    int64_t next_status_us = 0;

    while (true) {
        int64_t now = esp_timer_get_time();

        if (now >= next_hb_us) {
            next_hb_us = now + 1000000;
            hb++;
            /* HEARTBEAT type, not State, on purpose.  It is what the message
             * IS ([BP-TYPE-CODES] code 4), and it puts a NON-ZERO type nibble
             * in the arbitration id — so the id in candump proves the type
             * table crossed the wire, instead of proving only that a zero is
             * a zero.  publish_number() would hard-code State. */
            uint8_t v[kBusValueMaxNumeric];
            size_t n = bus_value_encode_f32((float)hb, v);
            bool ok = bus.publish_raw(s_ns_heartbeat, BusMessageType::Heartbeat,
                                      v, n);
            if (!ok)
                ESP_LOGW(TAG, "heartbeat %lu NOT sent (twai_transmit refused — "
                         "on CAN that usually means nothing ACKed it)",
                         (unsigned long)hb);
            else if ((hb % 10) == 1)
                ESP_LOGI(TAG, "heartbeat %lu sent", (unsigned long)hb);
        }

        if (now >= next_status_us) {
            next_status_us = now + 5000000;
            publish_status(bus, hb);
        }

        drain(bus);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

#endif /* CXC_CAN_BRINGUP_APP */
